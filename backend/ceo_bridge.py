"""CEO Bridge — Phase 1 + Phase 2 (A: escalation alerts, B: janitor).

Unified "CEO Local responds to everyone" bridge.

Flow:
    1. User sends a message on any channel (Discord #ask-ai, Forum, Inbox).
    2. The source integration calls `ingest_message()` (Python) or
       ``POST /api/ceo/messages/ingest`` (HTTP) to enqueue the message.
    3. CEO Local polls ``GET /api/ceo/messages/pending`` every 30s.
    4. CEO Local generates a response with qwen3.5:27b, checks for
       escalation keywords, and POSTs it back via
       ``POST /api/ceo/messages/{msg_id}/reply``.
    5. This module dispatches the response to the source channel
       (Discord via bot API, Forum via a new reply row, etc.).

Security: CEO auth via ``X-CEO-Key`` header (HMAC timing-safe compare).

Phase 2A — Escalation alerts
----------------------------
If the CEO Local flags ``escalated=true`` (or the server detects a
sensitive keyword in the incoming message or the generated response),
the message is marked ``status="escalated"``, the response is stored
but NOT dispatched, AND a Telegram alert is pushed to Alexis via
``TELEGRAM_ALERT_CHAT_ID`` / ``TELEGRAM_BOT_TOKEN``. Fire-and-forget —
alert failures never break the reply flow.

Phase 2B — Janitor
------------------
A background task (started from main.py lifespan) runs every 60 s and
resets messages that have been stuck in ``status='processing'`` for
more than ``JANITOR_STALE_SECONDS`` (default 300 s) back to
``pending``, so a crashed poller doesn't leave messages orphaned.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import time
import uuid
from typing import Optional

import httpx
from fastapi import APIRouter, Header, HTTPException, Path, Query

logger = logging.getLogger("maxia.ceo_bridge")

router = APIRouter(prefix="/api/ceo/messages", tags=["ceo-bridge"])

DISCORD_API_BASE = "https://discord.com/api/v10"
TELEGRAM_API_BASE = "https://api.telegram.org"

# Phase 2B janitor tuning
JANITOR_STALE_SECONDS = 300   # messages stuck in processing >5 min get reset
JANITOR_INTERVAL_SECONDS = 60

# Valid channels for the bridge. Keep small — add more in Phase 2.
VALID_CHANNELS = frozenset({"discord", "forum", "inbox", "email"})

# Status values persisted in ceo_pending_replies.status
STATUS_PENDING = "pending"
STATUS_PROCESSING = "processing"
STATUS_REPLIED = "replied"
STATUS_ESCALATED = "escalated"
STATUS_FAILED = "failed"

# Hard limits (enforced in code, not config)
MAX_MESSAGE_CHARS = 4000
MAX_RESPONSE_CHARS = 4000
MAX_PENDING_LIMIT = 50

# Escalation keywords — CEO Local can also flag escalated=True itself,
# but this is a belt-and-braces server-side check. Case-insensitive.
SENSITIVE_KEYWORDS = (
    "refund",
    "lawsuit",
    "legal",
    "lawyer",
    "sue",
    "hack",
    "stolen",
    "scam",
    "fraud",
    "exploit",
    "kyc",
    "police",
    "gdpr",
    "chargeback",
)


# ══════════════════════════════════════════
#  Internal helpers
# ══════════════════════════════════════════


async def _get_db():
    from core.database import db
    return db


def _require_ceo_key(key: str) -> None:
    """Validate CEO auth key (timing-safe HMAC compare)."""
    expected = os.getenv("CEO_API_KEY", "")
    if not expected:
        raise HTTPException(503, "CEO bridge not configured")
    if not key or not hmac.compare_digest(key.encode(), expected.encode()):
        raise HTTPException(401, "Invalid CEO key")


def _validate_channel(channel: str) -> str:
    cleaned = (channel or "").strip().lower()
    if cleaned not in VALID_CHANNELS:
        raise HTTPException(
            400, f"channel must be one of: {', '.join(sorted(VALID_CHANNELS))}"
        )
    return cleaned


def _validate_message(message: str) -> str:
    if not isinstance(message, str):
        raise HTTPException(400, "message must be a string")
    cleaned = message.strip()
    if not cleaned:
        raise HTTPException(400, "message is empty")
    if len(cleaned) > MAX_MESSAGE_CHARS:
        raise HTTPException(400, f"message exceeds {MAX_MESSAGE_CHARS} chars")
    return cleaned


def _should_escalate(text: str) -> bool:
    """Return True if ``text`` contains any sensitive keyword."""
    if not isinstance(text, str):
        return False
    lowered = text.lower()
    return any(kw in lowered for kw in SENSITIVE_KEYWORDS)


async def ingest_message(
    *,
    channel: str,
    source_ref: str,
    user_id: str,
    user_name: str,
    message: str,
    language: str = "",
) -> str:
    """Enqueue a new user message for CEO Local to answer.

    Can be called directly from backend code (e.g. forum.create_post hook)
    or via the POST /ingest HTTP endpoint.

    Returns the generated ``msg_id`` (opaque string, safe for logs).
    """
    channel = _validate_channel(channel)
    clean_message = _validate_message(message)

    msg_id = f"msg_{uuid.uuid4().hex[:12]}"
    now = int(time.time())
    escalated_flag = 1 if _should_escalate(clean_message) else 0

    db = await _get_db()
    await db.raw_execute(
        "INSERT INTO ceo_pending_replies(msg_id, channel, source_ref, user_id, "
        "user_name, message, language, received_at, status, escalated) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            msg_id,
            channel,
            (source_ref or "")[:256],
            (user_id or "")[:128],
            (user_name or "")[:128],
            clean_message,
            (language or "")[:16],
            now,
            STATUS_PENDING,
            escalated_flag,
        ),
    )
    logger.info(
        "[ceo_bridge] Ingested %s channel=%s source=%s user=%s escalated=%d",
        msg_id, channel, source_ref[:32], user_id[:32], escalated_flag,
    )
    return msg_id


# ══════════════════════════════════════════
#  Dispatchers — one per channel
# ══════════════════════════════════════════


async def _dispatch_discord(source_ref: str, response: str) -> bool:
    """Post a reply into a Discord channel via the MAXIA assistant bot.

    ``source_ref`` format: ``"<channel_id>:<original_message_id>"`` or just
    ``"<channel_id>"`` (first form replies in a thread, second posts a
    plain message).
    """
    token = os.getenv("DISCORD_ASSISTANT_TOKEN", "")
    if not token or len(token) < 30:
        logger.error("[ceo_bridge] DISCORD_ASSISTANT_TOKEN missing — cannot dispatch")
        return False

    parts = source_ref.split(":", 1)
    channel_id = parts[0].strip()
    message_id = parts[1].strip() if len(parts) > 1 else ""
    if not channel_id or not channel_id.isdigit():
        logger.error("[ceo_bridge] invalid discord source_ref: %s", source_ref[:32])
        return False

    url = f"{DISCORD_API_BASE}/channels/{channel_id}/messages"
    headers = {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json",
        "User-Agent": "MAXIA Assistant/1.0 (+https://maxiaworld.app)",
    }
    payload: dict = {"content": response[:1900]}  # Discord hard cap 2000
    if message_id and message_id.isdigit():
        payload["message_reference"] = {
            "message_id": message_id,
            "fail_if_not_exists": False,
        }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
    except Exception as e:
        logger.error("[ceo_bridge] discord dispatch network error: %s", e)
        return False

    if 200 <= resp.status_code < 300:
        return True
    logger.error(
        "[ceo_bridge] discord dispatch failed %d on channel %s",
        resp.status_code, channel_id,
    )
    return False


async def _dispatch_forum(source_ref: str, response: str) -> bool:
    """Post a reply under a forum post. ``source_ref`` is the post_id."""
    post_id = (source_ref or "").strip()
    if not post_id:
        return False

    try:
        db = await _get_db()
        rows = await db.raw_execute_fetchall(
            "SELECT id FROM forum_posts WHERE id=? AND status='active'", (post_id,))
        if not rows:
            logger.warning("[ceo_bridge] forum post not found: %s", post_id[:32])
            return False

        reply_id = f"reply_{uuid.uuid4().hex[:12]}"
        now = int(time.time())
        reply_body = response[:3000]
        reply_doc = {
            "id": reply_id,
            "post_id": post_id,
            "author_wallet": "ceo_bridge",
            "author_name": "MAXIA Assistant",
            "body": reply_body,
            "upvotes": 0,
            "downvotes": 0,
            "created_at": now,
            "is_offer": False,
            "offer_price_usdc": None,
            "status": "active",
            "ai_generated": True,
        }
        await db.raw_execute(
            "INSERT INTO forum_replies(id, post_id, data, created_at, status) "
            "VALUES(?,?,?,?,?)",
            (reply_id, post_id, json.dumps(reply_doc, default=str), now, "active"),
        )
        return True
    except Exception as e:
        logger.error("[ceo_bridge] forum dispatch error: %s", e)
        return False


async def _dispatch(channel: str, source_ref: str, response: str) -> bool:
    if channel == "discord":
        return await _dispatch_discord(source_ref, response)
    if channel == "forum":
        return await _dispatch_forum(source_ref, response)
    # inbox + email: Phase 2b — store only, no dispatch
    logger.info("[ceo_bridge] no dispatcher for channel=%s (stored only)", channel)
    return False


# ══════════════════════════════════════════
#  Phase 2A — Telegram escalation alerts
# ══════════════════════════════════════════


def _format_escalation_alert(
    *,
    msg_id: str,
    channel: str,
    user_name: str,
    user_id: str,
    user_message: str,
    draft_response: str,
) -> str:
    """Build a short Telegram HTML-formatted alert for Alexis.

    HTML is used (not Markdown) to sidestep Discord-style markdown
    collisions when messages contain ``*`` or ``_``.
    """
    import html as _html

    def esc(s: str) -> str:
        return _html.escape((s or "")[:600])

    lines = [
        "🚨 <b>ESCALADE MAXIA Assistant</b>",
        "",
        f"<b>Canal:</b> {esc(channel)}",
        f"<b>User:</b> {esc(user_name) or '?'} (<code>{esc(user_id) or '?'}</code>)",
        "",
        "<b>Message:</b>",
        f"<i>{esc(user_message[:500])}</i>",
    ]
    if draft_response:
        lines += [
            "",
            "<b>Draft du bot (NON envoye):</b>",
            f"<i>{esc(draft_response[:500])}</i>",
        ]
    lines += ["", f"<code>msg_id: {esc(msg_id)}</code>"]
    return "\n".join(lines)


async def _notify_escalation_telegram(
    *,
    msg_id: str,
    channel: str,
    user_name: str,
    user_id: str,
    user_message: str,
    draft_response: str,
) -> bool:
    """Push an escalation alert to ``TELEGRAM_ALERT_CHAT_ID``.

    Fire-and-forget — returns ``True`` on success, ``False`` on any
    failure (missing env var, network error, non-2xx). Never raises.

    Env vars:
        TELEGRAM_BOT_TOKEN       Shared with the client bot, but we send
                                 to the CEO alert chat (not clients).
        TELEGRAM_ALERT_CHAT_ID   Private chat or channel id where
                                 Alexis receives CEO alerts. Numeric
                                 (``-100...`` for channels) or ``@handle``.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_ALERT_CHAT_ID", "")
    if not token or not chat_id:
        logger.info(
            "[ceo_bridge] escalation TG alert skipped (TELEGRAM_BOT_TOKEN or "
            "TELEGRAM_ALERT_CHAT_ID not set) msg=%s",
            msg_id,
        )
        return False

    text = _format_escalation_alert(
        msg_id=msg_id,
        channel=channel,
        user_name=user_name,
        user_id=user_id,
        user_message=user_message,
        draft_response=draft_response,
    )

    url = f"{TELEGRAM_API_BASE}/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
    except Exception as e:
        logger.warning(
            "[ceo_bridge] escalation TG alert network error: %s (msg=%s)",
            e, msg_id,
        )
        return False

    if 200 <= resp.status_code < 300:
        logger.info("[ceo_bridge] escalation TG alert sent for msg=%s", msg_id)
        return True
    logger.warning(
        "[ceo_bridge] escalation TG alert HTTP %d for msg=%s: %s",
        resp.status_code, msg_id,
        resp.text[:200] if isinstance(resp.text, str) else "",
    )
    return False


# ══════════════════════════════════════════
#  HTTP endpoints
# ══════════════════════════════════════════


@router.post("/ingest")
async def ingest_endpoint(
    req: dict,
    x_ceo_key: str = Header("", alias="X-CEO-Key"),
) -> dict:
    """Enqueue a new user message. Requires X-CEO-Key.

    Body: {
        "channel": "discord",             # discord|forum|inbox|email
        "source_ref": "1234:5678",        # channel-specific identifier
        "user_id": "discord_user_id",
        "user_name": "SomeUser",
        "message": "How does escrow work?",
        "language": "en"                  # optional ISO hint
    }
    """
    _require_ceo_key(x_ceo_key)
    msg_id = await ingest_message(
        channel=str(req.get("channel", "")),
        source_ref=str(req.get("source_ref", "")),
        user_id=str(req.get("user_id", "")),
        user_name=str(req.get("user_name", "")),
        message=str(req.get("message", "")),
        language=str(req.get("language", "")),
    )
    return {"msg_id": msg_id, "status": STATUS_PENDING}


@router.get("/pending")
async def pending_endpoint(
    x_ceo_key: str = Header("", alias="X-CEO-Key"),
    limit: int = Query(10, ge=1, le=MAX_PENDING_LIMIT),
    channel: Optional[str] = Query(None),
) -> dict:
    """CEO Local polls this to get pending user messages.

    Messages are returned in FIFO order (oldest first) and atomically
    marked as ``processing`` so a crashed poller doesn't re-deliver them
    to itself. A stale ``processing`` message will be recovered by the
    janitor (Phase 2).
    """
    _require_ceo_key(x_ceo_key)
    db = await _get_db()

    if channel:
        channel = _validate_channel(channel)
        rows = await db.raw_execute_fetchall(
            "SELECT msg_id, channel, source_ref, user_id, user_name, "
            "message, language, received_at, escalated "
            "FROM ceo_pending_replies WHERE status=? AND channel=? "
            "ORDER BY received_at ASC LIMIT ?",
            (STATUS_PENDING, channel, limit),
        )
    else:
        rows = await db.raw_execute_fetchall(
            "SELECT msg_id, channel, source_ref, user_id, user_name, "
            "message, language, received_at, escalated "
            "FROM ceo_pending_replies WHERE status=? "
            "ORDER BY received_at ASC LIMIT ?",
            (STATUS_PENDING, limit),
        )

    messages = [dict(r) for r in rows]
    for m in messages:
        await db.raw_execute(
            "UPDATE ceo_pending_replies SET status=? WHERE msg_id=? AND status=?",
            (STATUS_PROCESSING, m["msg_id"], STATUS_PENDING),
        )
    return {"messages": messages, "count": len(messages)}


@router.post("/{msg_id}/reply")
async def reply_endpoint(
    req: dict,
    msg_id: str = Path(..., pattern=r"^msg_[a-f0-9]{12}$"),
    x_ceo_key: str = Header("", alias="X-CEO-Key"),
) -> dict:
    """CEO Local posts its generated response.

    Body: {
        "response": "...",       # required, <=4000 chars
        "confidence": 0.85,      # 0..1 (optional)
        "escalated": false       # optional; true = don't dispatch, store only
    }
    """
    _require_ceo_key(x_ceo_key)

    response = str(req.get("response", "")).strip()
    if not response:
        raise HTTPException(400, "response is empty")
    if len(response) > MAX_RESPONSE_CHARS:
        raise HTTPException(400, f"response exceeds {MAX_RESPONSE_CHARS} chars")

    try:
        confidence = float(req.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    escalated_req = bool(req.get("escalated", False))

    db = await _get_db()
    rows = await db.raw_execute_fetchall(
        "SELECT channel, source_ref, message, user_id, user_name, escalated "
        "FROM ceo_pending_replies WHERE msg_id=?",
        (msg_id,),
    )
    if not rows:
        raise HTTPException(404, "msg_id not found")

    row = dict(rows[0])
    # Merge: CEO Local's flag OR server-side pre-flag OR server-side re-check on response.
    must_escalate = (
        escalated_req
        or bool(row.get("escalated", 0))
        or _should_escalate(response)
    )

    now = int(time.time())
    dispatched = False

    if must_escalate:
        new_status = STATUS_ESCALATED
        logger.info(
            "[ceo_bridge] msg=%s ESCALATED (not dispatched) channel=%s",
            msg_id, row["channel"],
        )
        # Phase 2A: fire-and-forget Telegram alert to Alexis.
        # We schedule it as a task so the HTTP response doesn't block on
        # the Telegram API. Failures are logged but never propagated.
        try:
            asyncio.create_task(_notify_escalation_telegram(
                msg_id=msg_id,
                channel=row.get("channel", ""),
                user_name=row.get("user_name", "") or "",
                user_id=row.get("user_id", "") or "",
                user_message=row.get("message", "") or "",
                draft_response=response,
            ))
        except Exception as e:
            logger.warning("[ceo_bridge] escalation alert schedule error: %s", e)
    else:
        try:
            dispatched = await _dispatch(
                row["channel"], row["source_ref"], response,
            )
        except Exception as e:
            logger.error("[ceo_bridge] dispatch exception: %s", e)
            dispatched = False
        new_status = STATUS_REPLIED if dispatched else STATUS_FAILED

    await db.raw_execute(
        "UPDATE ceo_pending_replies SET status=?, response=?, confidence=?, "
        "escalated=?, responded_at=? WHERE msg_id=?",
        (
            new_status,
            response[:MAX_RESPONSE_CHARS],
            confidence,
            1 if must_escalate else 0,
            now,
            msg_id,
        ),
    )

    return {
        "success": True,
        "msg_id": msg_id,
        "status": new_status,
        "dispatched": dispatched,
        "escalated": must_escalate,
    }


@router.get("/status")
async def bridge_status() -> dict:
    """Public health: queue counters by status. No auth."""
    db = await _get_db()
    try:
        rows = await db.raw_execute_fetchall(
            "SELECT status, COUNT(*) as cnt FROM ceo_pending_replies GROUP BY status"
        )
        counters: dict[str, int] = {}
        for r in rows:
            d = dict(r)
            counters[str(d.get("status", ""))] = int(d.get("cnt", 0))
        return {
            "bridge": "ceo_bridge",
            "version": "1.0",
            "channels": sorted(VALID_CHANNELS),
            "counters": counters,
        }
    except Exception:
        return {"bridge": "ceo_bridge", "version": "1.0", "counters": {}}


def get_router() -> APIRouter:
    return router


# ══════════════════════════════════════════
#  Phase 2B — Janitor (recover stuck processing messages)
# ══════════════════════════════════════════


async def recover_stale_processing(stale_seconds: int = JANITOR_STALE_SECONDS) -> int:
    """Reset to ``pending`` any message stuck in ``processing`` for too long.

    Returns the number of rows recovered. A message is considered
    stuck if it entered ``processing`` (``received_at``) more than
    ``stale_seconds`` ago — using ``received_at`` as an
    approximation is safe because the state transition
    ``pending → processing`` happens inside the poll endpoint, which
    runs well under a second after ingestion.

    Called periodically by the janitor loop AND exposed as a library
    function so tests and ops can trigger it manually.
    """
    try:
        db = await _get_db()
    except Exception as e:
        logger.warning("[ceo_bridge] janitor db unavailable: %s", e)
        return 0

    cutoff = int(time.time()) - max(30, int(stale_seconds))

    try:
        rows = await db.raw_execute_fetchall(
            "SELECT msg_id FROM ceo_pending_replies "
            "WHERE status=? AND received_at < ?",
            (STATUS_PROCESSING, cutoff),
        )
    except Exception as e:
        logger.warning("[ceo_bridge] janitor query error: %s", e)
        return 0

    msg_ids = [dict(r).get("msg_id", "") for r in rows]
    msg_ids = [m for m in msg_ids if m]
    if not msg_ids:
        return 0

    recovered = 0
    for msg_id in msg_ids:
        try:
            await db.raw_execute(
                "UPDATE ceo_pending_replies SET status=? "
                "WHERE msg_id=? AND status=?",
                (STATUS_PENDING, msg_id, STATUS_PROCESSING),
            )
            recovered += 1
        except Exception as e:
            logger.warning(
                "[ceo_bridge] janitor reset failed msg=%s: %s", msg_id, e,
            )

    if recovered:
        logger.info(
            "[ceo_bridge] janitor recovered %d stale processing message(s)",
            recovered,
        )
    return recovered


_janitor_task: Optional[asyncio.Task] = None


async def _janitor_loop() -> None:
    """Forever loop — call the janitor every JANITOR_INTERVAL_SECONDS."""
    logger.info(
        "[ceo_bridge] janitor loop started (interval=%ds, stale=%ds)",
        JANITOR_INTERVAL_SECONDS, JANITOR_STALE_SECONDS,
    )
    try:
        while True:
            await asyncio.sleep(JANITOR_INTERVAL_SECONDS)
            try:
                await recover_stale_processing()
            except Exception as e:
                logger.warning("[ceo_bridge] janitor iteration error: %s", e)
    except asyncio.CancelledError:
        logger.info("[ceo_bridge] janitor loop cancelled")
        raise


async def start_janitor() -> None:
    """Start the background janitor. Idempotent."""
    global _janitor_task
    if _janitor_task and not _janitor_task.done():
        return
    _janitor_task = asyncio.create_task(_janitor_loop())


async def stop_janitor() -> None:
    """Stop the background janitor (called on app shutdown)."""
    global _janitor_task
    if _janitor_task and not _janitor_task.done():
        _janitor_task.cancel()
        try:
            await _janitor_task
        except (asyncio.CancelledError, Exception):
            pass
    _janitor_task = None
