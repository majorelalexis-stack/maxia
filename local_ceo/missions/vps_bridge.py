"""Mission — VPS Bridge (Phase 1).

The "CEO Local answers on every channel" worker.

Every ~30 seconds, this mission:
    1. Polls ``{VPS_URL}/api/ceo/messages/pending`` (X-CEO-Key auth) for
       user messages queued by Discord/Forum/Inbox integrations.
    2. For each message, builds a knowledge-grounded prompt using the
       same helpers as ``telegram_smart_reply`` (llms-full.txt +
       memory_prod).
    3. Calls the local qwen3.5:27b via ``llm.llm()`` with a public-user
       system prompt (NOT the Alexis CHAT prompt).
    4. Detects sensitive keywords for escalation (refund/legal/hack/...).
    5. POSTs the generated reply to ``/api/ceo/messages/{msg_id}/reply``.
       The backend dispatches it to the source channel (or, if escalated,
       stores it for manual review by Alexis).

Design notes
------------
- **No parallelism**: qwen3.5:27b needs ~17 GB VRAM. Only one reply is
  generated at a time. The mission processes messages sequentially.
- **Throughput**: ~400 replies/hour max (5–8 s per reply on a 7900 XT).
  Not a problem with zero clients.
- **No state**: the VPS owns the queue; if CEO Local crashes, messages
  marked ``processing`` are recovered by the server-side janitor
  (Phase 2).
- **Fail soft**: any error on one message doesn't stop the loop.

Env vars used (all in config_local):
    VPS_URL         https://maxiaworld.app
    CEO_API_KEY     shared secret for X-CEO-Key auth
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional

import httpx

from config_local import VPS_URL, CEO_API_KEY

log = logging.getLogger("ceo")

# Relative imports for siblings in the ``missions`` package
try:
    from missions.telegram_smart_reply import (
        _build_knowledge_blob, _detect_lang,
    )
except ImportError as _e:
    log.warning("[vps_bridge] cannot import telegram_smart_reply helpers: %s", _e)
    def _build_knowledge_blob(query: Optional[str] = None) -> str:
        return "MAXIA is an AI-to-AI marketplace on 15 blockchains. Website: maxiaworld.app"
    def _detect_lang(lang: Optional[str]) -> str:
        return "English"

POLL_ENDPOINT = "/api/ceo/messages/pending"
REPLY_ENDPOINT = "/api/ceo/messages/{msg_id}/reply"

POLL_INTERVAL_SECONDS = 30
POLL_BATCH_SIZE = 5
POLL_TIMEOUT_SECONDS = 15
LLM_MAX_TOKENS = 400
LLM_TIMEOUT_SECONDS = 120

# Server-side escalation keywords — mirrors backend/ceo_bridge.py so
# CEO Local can skip the LLM call entirely when the user's message is
# clearly sensitive. Multilingual across the 13 MAXIA Community
# languages. Runs a word-boundary regex over non-CJK scripts PLUS a
# substring scan over CJK (Chinese + Japanese).
_ESCALATION_WORD_RE = re.compile(
    r"\b("
    # English
    r"refund|refunded|refunding|chargeback|"
    r"lawsuit|legal|lawyer|attorney|sue|sued|suing|"
    r"hack|hacked|hacker|hacking|"
    r"stolen|steal|stole|"
    r"scam|scammed|scammer|"
    r"fraud|defraud|fraudulent|"
    r"exploit|exploited|phish|phishing|"
    r"kyc|aml|gdpr|ccpa|police|compromise|compromised|"
    # French
    r"remboursement|rembourser|rembourse|remboursee|"
    r"juridique|avocat|poursuite|poursuivre|poursuivi|"
    r"plainte|plaindre|piratage|pirater|piratee|"
    r"arnaque|arnaquer|escroquerie|escroc|"
    r"fraude|frauder|vole|volee|voler|derobe|derobee|"
    r"litige|tribunal|reclamation|reclamer|"
    # Spanish
    r"reembolso|reembolsar|reembolsado|"
    r"abogado|demanda|demandar|"
    r"hackeado|hackear|robado|robar|"
    r"estafa|estafar|estafado|fraudulento|"
    r"denuncia|denunciar|policia|juicio|pleito|"
    # German
    r"rueckerstattung|rückerstattung|erstattung|erstatten|"
    r"anwalt|rechtlich|klage|klagen|"
    r"gehackt|gestohlen|diebstahl|"
    r"betrug|betrogen|polizei|beschwerde|gericht|"
    # Portuguese
    r"reembolso|advogado|processo|processar|"
    r"roubado|roubar|golpe|golpista|fraudado|"
    r"policia|tribunal|"
    # Italian
    r"rimborso|rimborsare|avvocato|causa|"
    r"denuncia|denunciare|hackerato|"
    r"rubato|rubare|furto|"
    r"truffa|truffato|frode|reclamo|tribunale|"
    # Dutch
    r"terugbetaling|terugbetalen|advocaat|rechtszaak|"
    r"gestolen|stelen|oplichting|oplichter|"
    r"politie|klacht|rechtbank"
    r")\b",
    re.IGNORECASE | re.UNICODE,
)

# Substring keywords — non-Latin scripts (CJK, Cyrillic, Arabic,
# Devanagari) + agglutinative Turkish. Word boundaries don't work
# reliably for these, so we match stems as substrings.
_ESCALATION_CJK_KEYWORDS: tuple[str, ...] = (
    # Turkish
    "iade ", " iade", "hukuki", "avukat", " dava", "dava ",
    "çalindi", "çalındı", "calindi", "çalmak", "calmak",
    "dolandırıc", "dolandiric", "sahtekar", "sahteci",
    " polis ", " polis.", "şikayet", "sikayet", "mahkeme",
    # Russian
    "возврат", "вернуть", "верните",
    "юридическ", "адвокат", " иск ",
    "взлом", "украли", "украден", "украл ",
    "мошенничество", "мошенник", "обман",
    "полиция", "жалоб",
    # Arabic
    "استرداد", "استرجاع", "قانوني", "قانونية", "محامي",
    "دعوى", "محكمة", "اختراق", "سرقة", "سرق",
    "احتيال", "نصب", "شرطة", "شكوى",
    # Hindi
    "धनवापसी", "वापसी", "कानूनी", "वकील", "मुकदमा", "न्यायालय",
    "हैक", "चोरी", "चोर", "धोखाधड़ी", "धोखा", "घोटाला",
    "पुलिस", "शिकायत",
    # Simplified Chinese
    "退款", "退钱", "退还", "赔偿",
    "律师", "法律", "起诉", "诉讼", "打官司",
    "被黑", "被盗", "黑客", "盗了", "盗取", "窃取",
    "欺诈", "诈骗", "骗局", "骗子",
    "警察", "报警", "投诉",
    # Japanese
    "返金", "払い戻", "弁護士", "法的", "訴訟", "訴える",
    "ハッキング", "ハック", "盗まれ", "盗難",
    "詐欺", "通報", "苦情",
)


def _is_sensitive(text: str) -> bool:
    """Multilingual sensitive-keyword check."""
    if not isinstance(text, str) or not text:
        return False
    if _ESCALATION_WORD_RE.search(text):
        return True
    for kw in _ESCALATION_CJK_KEYWORDS:
        if kw in text:
            return True
    return False


# Back-compat alias for existing call sites
_ESCALATION_RE = _ESCALATION_WORD_RE

# Per-channel max reply length (dispatcher will trim again, but we do
# the trimming client-side so the LLM doesn't waste tokens).
_MAX_REPLY_CHARS = {
    "discord": 1800,
    "forum": 2500,
    "inbox": 2500,
    "email": 3500,
}


def _strip_think_tags(text: str) -> str:
    """Remove Qwen3 ``<think>...</think>`` leakage if any."""
    if not isinstance(text, str):
        return ""
    if "</think>" in text:
        text = text.split("</think>", 1)[-1]
    if "<think>" in text:
        text = text.split("<think>", 1)[0]
    return text.strip()


def _build_system_prompt(channel: str, lang_name: str, user_message: str | None = None) -> str:
    knowledge = _build_knowledge_blob(query=user_message) if user_message else _build_knowledge_blob()
    channel_notes = {
        "discord": "You are answering in a public Discord channel. Keep it conversational, plain text, no markdown headings, under 1500 characters. Emojis are fine.",
        "forum": "You are replying on the MAXIA forum (public). You may use simple markdown (**bold**, lists). Stay under 2000 characters.",
        "inbox": "You are replying to a direct in-app inbox message. Professional tone, plain text, under 2000 characters.",
        "email": "You are replying to a support email. Start with a friendly greeting, end with 'Alexis & the MAXIA team'. Plain text, no markdown. Under 3000 characters.",
    }
    channel_rule = channel_notes.get(channel, channel_notes["inbox"])
    return (
        "You are the MAXIA public assistant. You answer questions about the "
        "MAXIA AI-to-AI marketplace from STRANGERS (not Alexis the founder). "
        "Rules:\n"
        "1. Use ONLY the facts in the KNOWLEDGE block below. If a fact is "
        "not there, say you don't know and point to https://maxiaworld.app.\n"
        "2. Never invent pricing, commission percentages, endpoint names, "
        "or partner names.\n"
        "3. Never promise refunds, legal guarantees, or custom deals. If the "
        "user asks for any of those, tell them Alexis will follow up by email.\n"
        "4. NEVER mention or speculate about: number of users, client counts, "
        "revenue numbers, wallet balances, MRR/ARR, funding, or any internal "
        "metric. If asked, reply 'we don't share those publicly yet — see "
        "https://maxiaworld.app for the latest on what we've built'.\n"
        "5. Frame MAXIA positively: 'production-ready', 'live on mainnet', "
        "'active development', 'welcoming early users'. Do NOT say 'beta', "
        "'zero clients', 'no revenue', 'early stage', 'just launched'.\n"
        "6. **LANGUAGE RULE — CRITICAL**: Respond in EXACTLY the same "
        "language as the user's message. If the user writes in French, your "
        "entire reply MUST be in French. If Spanish → Spanish. If Chinese → "
        "Chinese. If Portuguese → Portuguese. Mirror the user's language "
        "precisely. Do NOT translate. Do NOT default to English. "
        f"(Detected language hint from metadata: {lang_name} — but ALWAYS "
        "trust the language of the actual message over this hint.)\n"
        f"7. {channel_rule}\n\n"
        f"KNOWLEDGE:\n{knowledge}"
    )


async def _ask_llm(
    *,
    user_message: str,
    channel: str,
    lang_name: str,
) -> str:
    """Call qwen3.5:27b via ``local_ceo/llm.py`` legacy interface."""
    try:
        from llm import llm  # legacy entry, supports explicit ``system``
    except ImportError as e:
        log.warning("[vps_bridge] llm module unavailable: %s", e)
        return ""

    system = _build_system_prompt(channel, lang_name, user_message=user_message)
    prompt = (
        f"User message:\n{user_message}\n\n"
        "Your reply (single turn, no preamble):"
    )
    try:
        response = await llm(
            prompt=prompt,
            system=system,
            max_tokens=LLM_MAX_TOKENS,
            retries=2,
            timeout=LLM_TIMEOUT_SECONDS,
        )
    except Exception as e:
        log.warning("[vps_bridge] llm.llm() exception: %s", e)
        return ""

    return _strip_think_tags(response)


async def _poll_pending(client: httpx.AsyncClient) -> list[dict]:
    url = f"{VPS_URL.rstrip('/')}{POLL_ENDPOINT}"
    try:
        resp = await client.get(
            url,
            headers={"X-CEO-Key": CEO_API_KEY},
            params={"limit": POLL_BATCH_SIZE},
        )
    except Exception as e:
        log.warning("[vps_bridge] poll network error: %s", e)
        return []

    if resp.status_code == 401 or resp.status_code == 503:
        log.warning("[vps_bridge] poll auth/config error %d", resp.status_code)
        return []
    if not 200 <= resp.status_code < 300:
        log.warning("[vps_bridge] poll HTTP %d", resp.status_code)
        return []

    try:
        data = resp.json() or {}
    except Exception:
        return []

    msgs = data.get("messages") or []
    return [m for m in msgs if isinstance(m, dict)]


async def _post_reply(
    client: httpx.AsyncClient,
    msg_id: str,
    response_text: str,
    confidence: float,
    escalated: bool,
) -> bool:
    url = f"{VPS_URL.rstrip('/')}{REPLY_ENDPOINT.format(msg_id=msg_id)}"
    try:
        resp = await client.post(
            url,
            headers={"X-CEO-Key": CEO_API_KEY, "Content-Type": "application/json"},
            json={
                "response": response_text,
                "confidence": confidence,
                "escalated": escalated,
            },
        )
    except Exception as e:
        log.warning("[vps_bridge] post reply network error: %s", e)
        return False

    if not 200 <= resp.status_code < 300:
        log.warning(
            "[vps_bridge] post reply HTTP %d for %s: %s",
            resp.status_code, msg_id, resp.text[:120] if isinstance(resp.text, str) else "",
        )
        return False
    return True


async def _handle_one(client: httpx.AsyncClient, msg: dict) -> None:
    msg_id = str(msg.get("msg_id", ""))
    channel = str(msg.get("channel", ""))
    user_message = str(msg.get("message", "")).strip()
    user_id = str(msg.get("user_id", "")).strip()
    language_code = str(msg.get("language", ""))
    pre_escalated = bool(msg.get("escalated", 0))

    if not msg_id or not channel or not user_message:
        return

    lang_name = _detect_lang(language_code)

    # Client-side escalation check BEFORE calling the LLM.
    # If the message contains sensitive keywords (any of 13 languages),
    # skip the LLM entirely and flag it so Alexis handles it manually.
    if pre_escalated or _is_sensitive(user_message):
        log.info(
            "[vps_bridge] msg=%s ESCALATE (sensitive keywords, no LLM call)",
            msg_id,
        )
        await _post_reply(
            client,
            msg_id,
            response_text=(
                "This question will be handled by Alexis personally. "
                "He will follow up within 24 hours."
            ),
            confidence=1.0,
            escalated=True,
        )
        return

    # Route through the smart_reply library, which decides between
    # MaxiaSalesAgent (prospect with user_id + feature flag) and the
    # legacy knowledge-grounded flow. This gives us:
    #   - Staged funnel + per-user memory via MaxiaSalesAgent
    #   - Closing-stage alert to Alexis
    #   - Legacy fallback if the sales agent is unavailable
    response_text = ""
    try:
        from missions.telegram_smart_reply import answer_user_message
        response_text = await answer_user_message(
            user_message=user_message,
            history=[],  # sales agent uses its own persisted state
            language_code=language_code,
            user_id=user_id or None,
            channel=channel,
        )
    except Exception as e:
        log.warning("[vps_bridge] smart_reply route failed: %s", e)

    # Last-resort fallback to the legacy direct-llm path if the smart_reply
    # layer returned nothing (e.g. import chain broken during a refactor).
    if not response_text or len(response_text) < 5:
        log.info("[vps_bridge] msg=%s falling back to legacy _ask_llm", msg_id)
        response_text = await _ask_llm(
            user_message=user_message,
            channel=channel,
            lang_name=lang_name,
        )

    if not response_text or len(response_text) < 5:
        log.warning("[vps_bridge] msg=%s empty LLM response", msg_id)
        await _post_reply(
            client,
            msg_id,
            response_text=(
                "I could not generate a clean answer right now. "
                "Please visit https://maxiaworld.app or try again."
            ),
            confidence=0.0,
            escalated=True,
        )
        return

    # Trim per-channel (MaxiaSalesAgent has its own 3800-char cap but we
    # re-apply channel-specific limits here for Discord/Forum/Inbox sizes).
    cap = _MAX_REPLY_CHARS.get(channel, 2000)
    response_text = response_text[:cap]

    await _post_reply(
        client,
        msg_id,
        response_text=response_text,
        confidence=0.80,  # sales agent ground in catalog — slightly higher confidence
        escalated=False,
    )
    log.info(
        "[vps_bridge] msg=%s REPLIED channel=%s user=%s (%d chars)",
        msg_id, channel, user_id[:32] if user_id else "?", len(response_text),
    )


async def mission_vps_bridge(mem: dict, actions: dict) -> None:
    """Run one poll-and-reply cycle.

    Designed to be called every ~30 seconds from ``ceo_main.py``. Each
    invocation processes up to ``POLL_BATCH_SIZE`` messages sequentially.
    Missing credentials are not an error — the mission simply no-ops so
    dev runs without a VPS don't spam warnings.
    """
    if not CEO_API_KEY:
        return
    if not VPS_URL:
        return

    async with httpx.AsyncClient(timeout=POLL_TIMEOUT_SECONDS) as client:
        messages = await _poll_pending(client)
        if not messages:
            return
        log.info("[vps_bridge] polled %d pending message(s)", len(messages))
        for msg in messages:
            try:
                await _handle_one(client, msg)
            except Exception as e:
                log.warning(
                    "[vps_bridge] handler exception on msg=%s: %s",
                    msg.get("msg_id", "?"), e,
                )

    # Record the last run so the scheduler can throttle
    import time as _t
    mem["_vps_bridge_last_run"] = _t.time()
    actions["counts"]["vps_bridge_replies"] = (
        actions["counts"].get("vps_bridge_replies", 0) + len(messages)
    )
