"""LLM Router — Route chaque requete vers le bon tier pour reduire les couts ~80%.

Tiers :
  LOCAL      — Ollama (Qwen2.5-14B) : classification, parsing, resumes, monitoring
  FAST       — Cerebras gpt-oss-120b : analyse marche, redaction tweets, negociation (3000 tok/s, 1M tok/jour gratuit)
  FAST2      — Gemini 2.5 Flash-Lite : fallback gratuit (1000 RPD, 250K TPM)
  FAST3      — Groq llama-3.3-70b   : secours (rate-limite mais fonctionnel)
  MID        — Mistral Small       : raisonnement moyen, SWOT leger, multi-step
  STRATEGIC  — Claude Sonnet/Opus  : decisions critiques, vision, expansion, red teaming

Fallback automatique : LOCAL -> FAST (Cerebras) -> FAST2 (Gemini) -> FAST3 (Groq) -> MID -> STRATEGIC
"""
import asyncio, logging, time
import httpx
from enum import Enum
from core.http_client import get_http_client

logger = logging.getLogger(__name__)


class Tier(str, Enum):
    LOCAL = "local"
    FAST = "fast"        # Cerebras (gratuit, 1M tok/jour)
    FAST2 = "fast2"      # Gemini Flash-Lite (gratuit, 1000 RPD)
    FAST3 = "fast3"      # Groq (secours, rate-limite)
    MID = "mid"
    STRATEGIC = "strategic"


# Mots-cles pour classifier la complexite
_TIER_KEYWORDS = {
    Tier.LOCAL: [
        "classify", "parse", "extract", "summarize", "resume", "format",
        "count", "list", "filter", "monitor", "check", "health", "status",
        "translate", "categorize", "tag",
    ],
    Tier.FAST: [
        "tweet", "write", "draft", "respond", "reply", "analyze market",
        "negotiate", "prospect", "outreach", "content", "message",
    ],
    Tier.MID: [
        "swot", "strategy", "plan", "evaluate", "compare", "assess",
        "diagnose", "multi-step", "report", "weekly",
    ],
    Tier.STRATEGIC: [
        "vision", "expansion", "red team", "critical", "okr", "roadmap",
        "invest", "crisis", "long-term", "global",
    ],
}

# Cout estime par 1K tokens (input, output)
_TIER_COSTS = {
    Tier.LOCAL: (0.0, 0.0),
    Tier.FAST: (0.0, 0.0),       # Cerebras free tier
    Tier.FAST2: (0.0, 0.0),      # Gemini free tier
    Tier.FAST3: (0.0, 0.0),      # Groq free tier (secours)
    Tier.MID: (0.0002, 0.0006),  # Mistral Small
    Tier.STRATEGIC: (0.003, 0.015),  # Claude Sonnet (default)
}


class LLMRouter:
    """Route les appels LLM vers le tier optimal."""

    def __init__(self):
        self.costs_today = {t.value: {"calls": 0, "cost": 0.0} for t in Tier}
        self._date = time.strftime("%Y-%m-%d")
        self._fallback_chain = [Tier.LOCAL, Tier.FAST, Tier.FAST2, Tier.FAST3, Tier.MID, Tier.STRATEGIC]
        # Config chargee depuis config.py
        try:
            from core.config import (
                OLLAMA_URL, OLLAMA_MODEL,
                CEREBRAS_API_KEY, CEREBRAS_MODEL,
                GOOGLE_AI_KEY, GOOGLE_AI_MODEL,
                MISTRAL_API_KEY, MISTRAL_MODEL,
                GROQ_API_KEY, ANTHROPIC_API_KEY,
            )
            self._ollama_url = OLLAMA_URL
            self._ollama_model = OLLAMA_MODEL
            self._cerebras_key = CEREBRAS_API_KEY
            self._cerebras_model = CEREBRAS_MODEL
            self._google_ai_key = GOOGLE_AI_KEY
            self._google_ai_model = GOOGLE_AI_MODEL
            self._mistral_key = MISTRAL_API_KEY
            self._mistral_model = MISTRAL_MODEL
            self._groq_key = GROQ_API_KEY
            self._anthropic_key = ANTHROPIC_API_KEY
        except ImportError:
            import os
            self._ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
            self._ollama_model = os.getenv("OLLAMA_MODEL", "qwen2.5:14b")
            self._cerebras_key = os.getenv("CEREBRAS_API_KEY", "")
            self._cerebras_model = os.getenv("CEREBRAS_MODEL", "gpt-oss-120b")
            self._google_ai_key = os.getenv("GOOGLE_AI_KEY", "")
            self._google_ai_model = os.getenv("GOOGLE_AI_MODEL", "gemini-2.5-flash-lite")
            self._mistral_key = os.getenv("MISTRAL_API_KEY", "")
            self._mistral_model = os.getenv("MISTRAL_MODEL", "mistral-small-latest")
            self._groq_key = os.getenv("GROQ_API_KEY", "")
            self._anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")

    def _reset_if_new_day(self):
        today = time.strftime("%Y-%m-%d")
        if self._date != today:
            self.costs_today = {t.value: {"calls": 0, "cost": 0.0} for t in Tier}
            self._date = today

    def classify_complexity(self, task_description: str) -> Tier:
        """Classifie la complexite d'une tache vers le bon tier."""
        desc = task_description.lower()
        # Score par tier
        scores = {t: 0 for t in Tier}
        for tier, keywords in _TIER_KEYWORDS.items():
            for kw in keywords:
                if kw in desc:
                    scores[tier] += 1
        # Longueur du prompt comme signal
        if len(task_description) > 2000:
            scores[Tier.MID] += 1
        if len(task_description) > 5000:
            scores[Tier.STRATEGIC] += 1
        # Retourner le tier avec le meilleur score, defaut LOCAL
        best = max(scores, key=scores.get)
        if scores[best] == 0:
            return Tier.LOCAL
        return best

    async def call(self, prompt: str, tier: Tier = None, system: str = "",
                   max_tokens: int = 500, timeout: float = 30.0) -> str:
        """Appelle le bon LLM selon le tier. Fallback automatique avec timeout par tier."""
        self._reset_if_new_day()
        if tier is None:
            tier = self.classify_complexity(prompt)

        # Tenter le tier demande puis fallback
        start_idx = self._fallback_chain.index(tier)
        for t in self._fallback_chain[start_idx:]:
            try:
                result = await asyncio.wait_for(
                    self._call_tier(t, system, prompt, max_tokens),
                    timeout=timeout,
                )
                if result:
                    self._track(t, len(prompt), len(result))
                    return result
            except asyncio.TimeoutError:
                logger.warning(f"[LLMRouter] {t.value} timeout ({timeout}s), trying next...")
                continue
            except Exception as e:
                logger.warning(f"[LLMRouter] {t.value} failed: {e}, trying next...")
                continue
        return ""

    async def _call_tier(self, tier: Tier, system: str, prompt: str,
                         max_tokens: int) -> str:
        if tier == Tier.LOCAL:
            return await self._call_ollama(system, prompt, max_tokens)
        elif tier == Tier.FAST:
            return await self._call_cerebras(system, prompt, max_tokens)
        elif tier == Tier.FAST2:
            return await self._call_gemini(system, prompt, max_tokens)
        elif tier == Tier.FAST3:
            return await self._call_groq(system, prompt, max_tokens)
        elif tier == Tier.MID:
            return await self._call_mistral(system, prompt, max_tokens)
        elif tier == Tier.STRATEGIC:
            return await self._call_anthropic(system, prompt, max_tokens)
        return ""

    async def _call_ollama(self, system: str, prompt: str, max_tokens: int) -> str:
        """Appel Ollama local (cout 0)."""
        full_prompt = f"{system}\n\n{prompt}" if system else prompt
        client = get_http_client()
        resp = await client.post(
            f"{self._ollama_url}/api/generate",
            json={
                "model": self._ollama_model,
                "prompt": full_prompt,
                "stream": False,
                "options": {"num_predict": max_tokens, "temperature": 0.7},
            },
            timeout=20.0,
        )
        resp.raise_for_status()
        return resp.json().get("response", "")

    async def _call_cerebras(self, system: str, prompt: str, max_tokens: int) -> str:
        """Appel Cerebras — gratuit, 30 RPM, 1M tok/jour, ~3000 tok/s."""
        if not self._cerebras_key:
            raise RuntimeError("No Cerebras API key")
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.append({"role": "user", "content": prompt})
        client = get_http_client()
        resp = await client.post(
            "https://api.cerebras.ai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {self._cerebras_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self._cerebras_model,
                "messages": msgs,
                "max_tokens": max_tokens,
                "temperature": 0.7,
            },
            timeout=25.0,
        )
        resp.raise_for_status()
        data = resp.json()
        choices = data.get("choices", [])
        return choices[0]["message"]["content"].strip() if choices else ""

    async def _call_gemini(self, system: str, prompt: str, max_tokens: int) -> str:
        """Appel Google Gemini via endpoint OpenAI-compatible — gratuit, 1000 RPD."""
        if not self._google_ai_key:
            raise RuntimeError("No Google AI key")
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.append({"role": "user", "content": prompt})
        client = get_http_client()
        resp = await client.post(
            "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
            headers={
                "Authorization": f"Bearer {self._google_ai_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self._google_ai_model,
                "messages": msgs,
                "max_tokens": max_tokens,
                "temperature": 0.7,
            },
            timeout=25.0,
        )
        resp.raise_for_status()
        data = resp.json()
        choices = data.get("choices", [])
        return choices[0]["message"]["content"].strip() if choices else ""

    _groq_last_call: float = 0
    _GROQ_MIN_INTERVAL: float = 2.0

    async def _call_groq(self, system: str, prompt: str, max_tokens: int) -> str:
        """Appel Groq — secours, rate-limite (30 RPM free)."""
        if not self._groq_key:
            raise RuntimeError("No Groq API key")
        now = time.time()
        elapsed = now - LLMRouter._groq_last_call
        if elapsed < self._GROQ_MIN_INTERVAL:
            await asyncio.sleep(self._GROQ_MIN_INTERVAL - elapsed)
        LLMRouter._groq_last_call = time.time()
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.append({"role": "user", "content": prompt})
        client = get_http_client()
        resp = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {self._groq_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": msgs,
                "max_tokens": max_tokens,
                "temperature": 0.7,
            },
            timeout=25.0,
        )
        resp.raise_for_status()
        data = resp.json()
        choices = data.get("choices", [])
        return choices[0]["message"]["content"].strip() if choices else ""

    async def _call_mistral(self, system: str, prompt: str, max_tokens: int) -> str:
        """Appel Mistral API."""
        if not self._mistral_key:
            raise RuntimeError("No Mistral API key")
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.append({"role": "user", "content": prompt})
        client = get_http_client()
        resp = await client.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {self._mistral_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self._mistral_model,
                "messages": msgs,
                "max_tokens": max_tokens,
                "temperature": 0.7,
            },
            timeout=25.0,
        )
        resp.raise_for_status()
        data = resp.json()
        choices = data.get("choices", [])
        return choices[0]["message"]["content"].strip() if choices else ""

    async def _call_anthropic(self, system: str, prompt: str, max_tokens: int) -> str:
        """Appel Claude Sonnet (defaut pour STRATEGIC)."""
        if not self._anthropic_key:
            raise RuntimeError("No Anthropic API key")
        client = get_http_client()
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self._anthropic_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": max_tokens,
                "system": system or "You are a helpful assistant.",
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()
        ct = data.get("content", [])
        return ct[0].get("text", "") if ct else ""

    def _track(self, tier: Tier, input_len: int, output_len: int):
        """Track les couts par tier."""
        self.costs_today[tier.value]["calls"] += 1
        # Estimation grossiere : ~4 chars/token
        tokens_in = input_len // 4
        tokens_out = output_len // 4
        rates = _TIER_COSTS.get(tier, (0, 0))
        cost = (tokens_in / 1000 * rates[0]) + (tokens_out / 1000 * rates[1])
        self.costs_today[tier.value]["cost"] += cost

    def get_stats(self) -> dict:
        self._reset_if_new_day()
        total_calls = sum(v["calls"] for v in self.costs_today.values())
        total_cost = sum(v["cost"] for v in self.costs_today.values())
        return {
            "date": self._date,
            "total_calls": total_calls,
            "total_cost_usd": round(total_cost, 4),
            "by_tier": self.costs_today,
        }

    def is_local_available(self) -> bool:
        """Verifie si Ollama est accessible."""
        try:
            import httpx
            resp = httpx.get(f"{self._ollama_url}/api/tags", timeout=3)
            return resp.status_code == 200
        except Exception:
            return False


# Singleton
router = LLMRouter()
