"""Mission — Telegram intelligent auto-reply (Plan CEO V9 / mission 6).

Extends ``missions/telegram_chat.py`` with a knowledge-grounded
answering mode. When a user DMs @MAXIA_AI_bot with a question (not a
slash command), this mission:

1. Detects the user's language (Telegram ``from.language_code``).
2. Pulls relevant MAXIA facts from ``local_ceo/memory_prod/``:
   - ``capabilities_prod.json`` (live endpoints)
   - ``outreach_channels.json`` (channels status)
   - ``country_allowlist.json`` (28 allowed countries)
   - ``quotas_daily.json`` (rate limits)
3. Also pulls the first 3 paragraphs of ``frontend/llms-full.txt``
   (high-signal overview).
4. Sends everything as context to qwen3.5:27b along with the user's
   question and the last 10 messages of the conversation thread.
5. Returns the LLM response in the **user's language** (the bot already
   auto-replies in that language — the existing ``telegram_chat.py``
   chat flow calls this as a library function).

This file exposes ``answer_user_message()`` as a library function that
the existing ``telegram_chat.py`` can call. It does NOT replace the
polling loop — it just adds a smarter response path.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

log = logging.getLogger("ceo")

_LOCAL_CEO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.dirname(_LOCAL_CEO_DIR)

MEMORY_PROD_DIR = os.path.join(_LOCAL_CEO_DIR, "memory_prod")
LLMS_FULL_PATH = os.path.join(_REPO_ROOT, "frontend", "llms-full.txt")

MAX_CONTEXT_CHARS: int = 4000
MAX_CONVERSATION_TURNS: int = 10


def _load_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _build_knowledge_blob() -> str:
    """Concatenate high-signal MAXIA context into a prompt-friendly blob."""
    parts: list[str] = []

    # llms-full.txt header (first 1500 chars)
    try:
        with open(LLMS_FULL_PATH, "r", encoding="utf-8") as f:
            overview = f.read(1500)
        parts.append("=== MAXIA OVERVIEW ===")
        parts.append(overview)
    except OSError:
        pass

    # Live capabilities from capabilities_prod.json
    caps = _load_json(os.path.join(MEMORY_PROD_DIR, "capabilities_prod.json"))
    cap_items = caps.get("capabilities", []) if isinstance(caps, dict) else []
    if cap_items:
        parts.append("\n=== LIVE ENDPOINTS (verified) ===")
        for cap in cap_items[:15]:
            if isinstance(cap, dict):
                parts.append(f"- {cap.get('method', 'GET')} {cap.get('endpoint', '')} "
                             f"({cap.get('description', '')})")

    # Outreach channels state
    channels = _load_json(os.path.join(MEMORY_PROD_DIR, "outreach_channels.json"))
    if isinstance(channels, dict):
        ch = channels.get("channels", {})
        parts.append("\n=== OUTREACH CHANNELS ===")
        for name, info in ch.items():
            if isinstance(info, dict):
                status = info.get("status", "?")
                parts.append(f"- {name}: {status}")

    # Country allowlist
    countries = _load_json(os.path.join(MEMORY_PROD_DIR, "country_allowlist.json"))
    if isinstance(countries, dict):
        allowed = countries.get("allowed", [])
        blocked = countries.get("blocked", [])
        parts.append("\n=== COMPLIANCE ===")
        parts.append(f"Allowed countries (28): {', '.join(allowed)}")
        parts.append(f"Blocked: {', '.join(blocked)}")
        parts.append("India geo-blocked for marketing (read-only OK).")

    # Quotas
    quotas = _load_json(os.path.join(MEMORY_PROD_DIR, "quotas_daily.json"))
    if isinstance(quotas, dict):
        parts.append("\n=== DAILY QUOTAS ===")
        parts.append(f"Total outreach cap: {quotas.get('total_daily_outreach_cap', '?')}")

    blob = "\n".join(parts)
    if len(blob) > MAX_CONTEXT_CHARS:
        blob = blob[:MAX_CONTEXT_CHARS] + "\n... (truncated)"
    return blob


def _detect_lang(language_code: Optional[str]) -> str:
    """Normalize Telegram language_code to a short hint for the LLM prompt."""
    if not isinstance(language_code, str) or not language_code:
        return "English"
    code = language_code.lower().split("-")[0]
    mapping = {
        "en": "English", "fr": "French", "ja": "Japanese", "ko": "Korean",
        "zh": "Traditional Chinese", "th": "Thai", "vi": "Vietnamese",
        "id": "Indonesian", "hi": "Hindi", "ar": "Arabic", "he": "Hebrew",
        "pt": "Brazilian Portuguese", "es": "Spanish",
    }
    return mapping.get(code, "English")


def _format_history(history: list[dict]) -> str:
    lines = []
    for turn in history[-MAX_CONVERSATION_TURNS:]:
        if not isinstance(turn, dict):
            continue
        role = turn.get("role", "user")
        content = str(turn.get("content", ""))[:500]
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


async def answer_user_message(
    user_message: str,
    history: list[dict],
    language_code: Optional[str] = None,
) -> str:
    """Return a grounded answer in the user's language.

    Library function — called by telegram_chat.py when a user sends a
    free-form message (not a slash command). The caller is responsible
    for persisting the conversation history.
    """
    if not isinstance(user_message, str) or not user_message.strip():
        return "Please send a question."

    knowledge = _build_knowledge_blob()
    lang_name = _detect_lang(language_code)
    history_text = _format_history(history)

    system = (
        "You are the MAXIA CEO assistant. You answer questions about the "
        "MAXIA AI-to-AI marketplace. Use ONLY the facts provided in the "
        "KNOWLEDGE block below. If the answer is not in the knowledge, say "
        "you don't know and suggest visiting https://maxiaworld.app. Be "
        "concise (max 200 words). Never invent endpoint names, token "
        "counts, or partner names.\n\n"
        f"KNOWLEDGE:\n{knowledge}\n\n"
        f"Respond in {lang_name}. No emoji. No markdown except **bold**."
    )

    prompt = f"Conversation so far:\n{history_text}\n\nUser: {user_message}\n\nAssistant:"

    try:
        from llm import ask
        from agents import CHAT
    except ImportError as e:
        log.warning("[smart_reply] llm module unavailable: %s", e)
        return (
            "I am the MAXIA assistant but my reasoning engine is offline "
            "right now. Please visit https://maxiaworld.app for details."
        )

    try:
        response = await ask(CHAT, prompt, system=system)
    except TypeError:
        # Some older `ask` signatures don't accept `system` kwarg
        try:
            response = await ask(CHAT, f"{system}\n\n{prompt}")
        except Exception as e:
            log.warning("[smart_reply] llm call failed: %s", e)
            return "Sorry, I could not generate a response. Please try again."
    except Exception as e:
        log.warning("[smart_reply] llm call failed: %s", e)
        return "Sorry, I could not generate a response. Please try again."

    if not isinstance(response, str) or len(response) < 5:
        return "Sorry, I could not generate a response."

    # Trim to Telegram-safe length
    return response.strip()[:3800]
