"""LLM — Unified Ollama interface for CEO Local.

Two entry points:
  - ask(agent, prompt, knowledge) — call with AgentConfig (temperature, think, timeout)
  - llm(prompt, system, max_tokens, retries, timeout) — legacy interface for existing missions

Auto-switches GPU: stops Kaspa miner before LLM call, restarts Ollama if needed.
"""
import asyncio
import logging
import subprocess
import time
from typing import Optional

import httpx

from config_local import OLLAMA_URL, OLLAMA_MODEL, OLLAMA_NUM_CTX
from agents import AgentConfig, MAXIA_KNOWLEDGE

log = logging.getLogger("ceo")

# Timestamp du dernier appel LLM (utilise par la boucle principale pour relancer le miner)
last_llm_call: float = 0.0


# ══════════════════════════════════════════
# GPU auto-switch: stop miner before LLM
# ══════════════════════════════════════════

async def _ensure_gpu_free() -> None:
    """Stop Kaspa miner if running, restart Ollama to reclaim VRAM."""
    try:
        from kaspa_miner import is_mining, stop_miner
        if is_mining():
            log.info("[MINING] Pause miner pour appel LLM...")
            stop_miner()
            await asyncio.sleep(3)  # Laisser le GPU se liberer
            # Restart Ollama pour qu'il recupere la VRAM liberee
            log.info("[OLLAMA] Restart Ollama apres arret miner...")
            try:
                subprocess.run(
                    ["taskkill", "/F", "/IM", "ollama.exe"],
                    capture_output=True, timeout=5,
                )
                await asyncio.sleep(2)
                subprocess.Popen(
                    ["ollama", "serve"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                await asyncio.sleep(5)  # Attendre qu'Ollama soit pret
                # Warmup — forcer le chargement du modele en VRAM
                async with httpx.AsyncClient(timeout=30) as warmup:
                    await warmup.post(f"{OLLAMA_URL}/api/generate", json={
                        "model": OLLAMA_MODEL, "prompt": "hi", "stream": False,
                        "think": False, "options": {"num_predict": 1},
                    })
                log.info("[OLLAMA] Warmup OK — modele charge en VRAM")
            except Exception as e:
                log.warning("[OLLAMA] Restart/warmup error: %s", e)
    except ImportError:
        pass  # kaspa_miner not available


# ══════════════════════════════════════════
# ask() — New unified interface with AgentConfig
# ══════════════════════════════════════════

async def ask(
    agent: AgentConfig,
    prompt: str,
    knowledge: str = "",
    retries: int = 2,
) -> str:
    """Call Ollama with agent config. Injects knowledge base if provided.

    Args:
        agent: AgentConfig with system_prompt, think, max_tokens, temperature, timeout.
        prompt: The user/task prompt.
        knowledge: Optional knowledge base text to inject into system prompt.
        retries: Number of retry attempts on failure.

    Returns:
        LLM response text, or empty string on failure.
    """
    global last_llm_call

    await _ensure_gpu_free()
    last_llm_call = time.time()

    # Build system prompt with optional knowledge injection
    system = agent.system_prompt
    if knowledge:
        system = f"{system}\n\n--- KNOWLEDGE BASE ---\n{knowledge[:3000]}"

    # For think mode: prepend /think to the prompt (Qwen3.5 feature)
    effective_prompt = f"/think\n{prompt}" if agent.think else prompt

    for attempt in range(retries):
        try:
            async with httpx.AsyncClient(timeout=agent.timeout) as client:
                resp = await client.post(f"{OLLAMA_URL}/api/generate", json={
                    "model": OLLAMA_MODEL,
                    "prompt": effective_prompt,
                    "system": system,
                    "stream": False,
                    "think": agent.think,
                    "options": {
                        "num_predict": agent.max_tokens,
                        "temperature": agent.temperature,
                        "num_ctx": OLLAMA_NUM_CTX,
                    },
                })
                resp.raise_for_status()
                result = resp.json().get("response", "").strip()
                if result:
                    log.debug("[%s] Response: %d chars", agent.name, len(result))
                    return result
                log.warning("[%s] Empty response (attempt %d/%d)", agent.name, attempt + 1, retries)
        except httpx.TimeoutException:
            log.error("[%s] Timeout %ds (attempt %d/%d)", agent.name, agent.timeout, attempt + 1, retries)
        except Exception as e:
            log.error("[%s] Error (attempt %d/%d): %s", agent.name, attempt + 1, retries, e)
        if attempt < retries - 1:
            await asyncio.sleep(5)

    return ""


# ══════════════════════════════════════════
# llm() — Legacy interface (backward compat)
# ══════════════════════════════════════════

async def llm(
    prompt: str,
    system: str = "",
    max_tokens: int = 1000,
    retries: int = 2,
    timeout: int = 180,
) -> str:
    """Legacy Ollama call. think=False for Qwen3 (otherwise empty response).
    Auto-switch: stops Kaspa miner before call. Restart managed by main loop."""
    global last_llm_call

    await _ensure_gpu_free()
    last_llm_call = time.time()

    full = f"{system}\n\n{prompt}" if system else prompt
    for attempt in range(retries):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(f"{OLLAMA_URL}/api/generate", json={
                    "model": OLLAMA_MODEL,
                    "prompt": full,
                    "stream": False,
                    "think": False,
                    "options": {"num_predict": max_tokens, "temperature": 0.7, "num_ctx": OLLAMA_NUM_CTX},
                })
                resp.raise_for_status()
                result = resp.json().get("response", "").strip()
                if result:
                    return result
                log.warning("LLM returned empty response (attempt %d/%d)", attempt + 1, retries)
        except Exception as e:
            log.error("LLM error (attempt %d/%d): %s", attempt + 1, retries, e)
        if attempt < retries - 1:
            await asyncio.sleep(5)
    return ""
