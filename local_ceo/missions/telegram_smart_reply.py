"""Mission — Telegram intelligent auto-reply (Plan CEO V9 / mission 6).

Two modes, selected via the ``user_id`` argument to
``answer_user_message``:

- **Legacy knowledge-grounded mode** (no ``user_id``, or feature flag off):
  Builds a context blob from ``memory_prod/`` and ``frontend/llms-full.txt``
  and calls the CHAT agent via the standard ``llm.ask()`` pipeline. Used
  for Alexis's own assistant thread where there is no staged funnel.

- **MaxiaSalesAgent mode** (``user_id`` provided, ``ENABLE_MAXIA_SALES=1``):
  Routes the message through :class:`sales.MaxiaSalesAgent`, which
  maintains a per-prospect staged funnel (intro → qualification → value →
  needs → objection → closing), grounds every reply in
  ``sales/maxia_catalog.json``, and persists state in
  ``sales/conversations.db``. When a prospect reaches the ``6_closing``
  stage, Alexis gets a Telegram alert on @MAXIA_alerts so he can step in.

This file is a library. The caller (``telegram_chat.py`` or a VPS-side
bridge) is responsible for determining whether a message is from Alexis
or from a prospect, and passing ``user_id`` accordingly.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from typing import Optional

log = logging.getLogger("ceo")

_LOCAL_CEO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.dirname(_LOCAL_CEO_DIR)

# Allow ``from sales import MaxiaSalesAgent`` when this module is imported
# from the main CEO venv (missions is a sibling of sales).
if _LOCAL_CEO_DIR not in sys.path:
    sys.path.insert(0, _LOCAL_CEO_DIR)

# Feature flag: opt in per-deployment so we can rollback in one env var.
_ENABLE_MAXIA_SALES = os.getenv("ENABLE_MAXIA_SALES", "1") == "1"

# Single agent instance shared across all prospects. Cheap to construct
# (catalog is loaded once) and thread-safe for sequential async calls.
_sales_agent_singleton = None
_sales_agent_err: Optional[str] = None


def _get_sales_agent():
    """Lazy singleton so we don't pay the catalog load cost on every import."""
    global _sales_agent_singleton, _sales_agent_err
    if _sales_agent_singleton is not None:
        return _sales_agent_singleton
    if _sales_agent_err is not None:
        return None
    try:
        from sales import MaxiaSalesAgent
        _sales_agent_singleton = MaxiaSalesAgent()
        log.info("[smart_reply] MaxiaSalesAgent singleton initialized")
        return _sales_agent_singleton
    except Exception as e:
        _sales_agent_err = str(e)
        log.warning("[smart_reply] MaxiaSalesAgent unavailable: %s", e)
        return None

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


def _build_static_header() -> str:
    """Compliance + quotas header that should always be in the prompt.

    Kept separate from the retrieval result so we can stack RAG chunks
    underneath without losing the non-negotiable compliance rules.
    """
    parts: list[str] = []

    countries = _load_json(os.path.join(MEMORY_PROD_DIR, "country_allowlist.json"))
    if isinstance(countries, dict):
        allowed = countries.get("allowed", [])
        blocked = countries.get("blocked", [])
        parts.append("=== COMPLIANCE ===")
        parts.append(f"Allowed countries (28): {', '.join(allowed)}")
        parts.append(f"Blocked: {', '.join(blocked)}")
        parts.append("India geo-blocked for marketing (read-only OK).")

    quotas = _load_json(os.path.join(MEMORY_PROD_DIR, "quotas_daily.json"))
    if isinstance(quotas, dict):
        parts.append("\n=== DAILY QUOTAS ===")
        parts.append(f"Total outreach cap: {quotas.get('total_daily_outreach_cap', '?')}")

    return "\n".join(parts)


def _build_static_blob() -> str:
    """Legacy static blob — used as fallback when RAG is unavailable."""
    parts: list[str] = []

    try:
        with open(LLMS_FULL_PATH, "r", encoding="utf-8") as f:
            overview = f.read(1500)
        parts.append("=== MAXIA OVERVIEW ===")
        parts.append(overview)
    except OSError:
        pass

    caps = _load_json(os.path.join(MEMORY_PROD_DIR, "capabilities_prod.json"))
    cap_items = caps.get("capabilities", []) if isinstance(caps, dict) else []
    if cap_items:
        parts.append("\n=== LIVE ENDPOINTS (verified) ===")
        for cap in cap_items[:15]:
            if isinstance(cap, dict):
                parts.append(f"- {cap.get('method', 'GET')} {cap.get('endpoint', '')} "
                             f"({cap.get('description', '')})")

    channels = _load_json(os.path.join(MEMORY_PROD_DIR, "outreach_channels.json"))
    if isinstance(channels, dict):
        ch = channels.get("channels", {})
        parts.append("\n=== OUTREACH CHANNELS ===")
        for name, info in ch.items():
            if isinstance(info, dict):
                parts.append(f"- {name}: {info.get('status', '?')}")

    header = _build_static_header()
    if header:
        parts.append("\n" + header)

    blob = "\n".join(parts)
    if len(blob) > MAX_CONTEXT_CHARS:
        blob = blob[:MAX_CONTEXT_CHARS] + "\n... (truncated)"
    return blob


def _build_knowledge_blob(query: str | None = None) -> str:
    """Build the knowledge block injected into the LLM system prompt.

    When ``query`` is provided and the RAG module is available, retrieve
    the top semantically-relevant chunks and stack them under a static
    compliance/quotas header. Otherwise fall back to the legacy static
    blob (backward compat — callers that pass no query see the old
    behaviour).
    """
    if query and isinstance(query, str) and query.strip():
        try:
            from rag_knowledge import build_rag_context
        except ImportError:
            return _build_static_blob()

        header = _build_static_header()
        rag_context = build_rag_context(
            query=query,
            max_chars=max(500, MAX_CONTEXT_CHARS - len(header) - 100),
        )
        if rag_context:
            parts = []
            if header:
                parts.append(header)
            parts.append("=== RELEVANT KNOWLEDGE (semantic retrieval) ===")
            parts.append(rag_context)
            blob = "\n\n".join(parts)
            if len(blob) > MAX_CONTEXT_CHARS:
                blob = blob[:MAX_CONTEXT_CHARS] + "\n... (truncated)"
            return blob

    return _build_static_blob()


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


async def _alert_closing_stage(user_id: str, reply: str, stage: str) -> None:
    """Ping Alexis on his CEO Telegram chat when a prospect hits closing.

    Fires exactly once per conversation — the agent's own persisted state
    lets us deduplicate by checking whether the previous turn was also in
    ``6_closing``. Silent failure: a missed alert must not break the reply.
    """
    try:
        from notifier import notify_telegram
        snippet = reply[:300]
        await notify_telegram(
            "Sales agent — closing stage",
            f"Prospect `{user_id}` just reached *{stage}*.\n\n"
            f"Latest bot reply:\n{snippet}\n\n"
            f"Intervene on @MAXIA_AI_bot if you want to take over.",
        )
    except Exception as e:
        log.debug("[smart_reply] closing alert failed: %s", e)


async def _sales_reply(
    user_message: str,
    user_id: str,
    channel: str,
    language_code: Optional[str],
) -> Optional[str]:
    """Route a prospect message through MaxiaSalesAgent.

    Returns the reply string on success, or ``None`` to signal the caller
    should fall back to the legacy knowledge-grounded flow.
    """
    if not _ENABLE_MAXIA_SALES:
        return None
    agent = _get_sales_agent()
    if agent is None:
        return None

    # Build a stable conversation_id per prospect per channel
    conversation_id = f"{channel}:{user_id}"

    # Infer lang from Telegram language_code ("en", "fr", "es", ...) as a
    # hint; MaxiaSalesAgent will fall back to its own heuristic if empty.
    lang: Optional[str] = None
    if isinstance(language_code, str) and language_code:
        code = language_code.lower().split("-")[0]
        if code in ("en", "fr", "es", "de", "it", "pt", "ja", "ko", "zh", "ar"):
            lang = code

    # Snapshot previous stage to detect transition INTO closing (avoids
    # firing the alert on every subsequent turn while still in closing).
    prev_state = agent.get_state(conversation_id)
    prev_stage = prev_state.stage.value if prev_state else None

    try:
        reply, stage = await agent.reply(
            conversation_id=conversation_id,
            user_message=user_message,
            channel=channel,
            user_id=user_id,
            lang=lang,
        )
    except Exception as e:
        log.warning("[smart_reply] sales agent failed: %s", e)
        return None

    # Alert on transition into closing (only fires once per conversation)
    if stage.value == "6_closing" and prev_stage != "6_closing":
        await _alert_closing_stage(user_id, reply, stage.value)

    return reply


async def answer_user_message(
    user_message: str,
    history: list[dict],
    language_code: Optional[str] = None,
    user_id: Optional[str] = None,
    channel: str = "telegram",
) -> str:
    """Return a grounded answer in the user's language.

    Args:
        user_message: the incoming text from the user.
        history: legacy-mode conversation history (list of ``{role, content}``).
            Ignored by MaxiaSalesAgent mode which persists its own state.
        language_code: Telegram ``from.language_code`` if known.
        user_id: **if set**, treat the sender as a prospect and route through
            :class:`sales.MaxiaSalesAgent` (subject to ``ENABLE_MAXIA_SALES``).
            If ``None``, fall back to the legacy knowledge-grounded flow —
            used for Alexis's own assistant thread.
        channel: ``telegram`` | ``email`` | ``github`` | ``web``. Determines
            the conversation_id namespace for the sales agent.
    """
    if not isinstance(user_message, str) or not user_message.strip():
        return "Please send a question."

    # Prospect routing: try MaxiaSalesAgent first when user_id is known
    if user_id:
        sales_reply = await _sales_reply(
            user_message=user_message,
            user_id=str(user_id),
            channel=channel,
            language_code=language_code,
        )
        if sales_reply:
            return sales_reply.strip()[:3800]
        # Fall through to legacy if the agent refused to handle it

    knowledge = _build_knowledge_blob(query=user_message)
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
