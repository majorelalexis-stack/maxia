"""MAXIA Agent-to-Agent Messaging — Agents communicate, negotiate, coordinate.

Thread-based messaging with rate limiting and content safety.
"""
import logging
import json
import time
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Query
from core.error_utils import safe_error

logger = logging.getLogger(__name__)

router = APIRouter(tags=["messaging"])

_MSG_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_messages (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    sender_key TEXT NOT NULL,
    recipient_key TEXT NOT NULL,
    subject TEXT,
    body TEXT NOT NULL,
    read INTEGER DEFAULT 0,
    created_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_msg_recipient ON agent_messages(recipient_key, read);
CREATE INDEX IF NOT EXISTS idx_msg_thread ON agent_messages(thread_id);
CREATE INDEX IF NOT EXISTS idx_msg_sender ON agent_messages(sender_key);
"""

_schema_ready = False

# Rate limit: 20 messages/hour per sender API key
_MSG_RATE_LIMIT = 20
_MSG_RATE_WINDOW = 3600  # 1 hour in seconds
_send_timestamps: dict[str, list[float]] = {}  # api_key -> list of send timestamps

MAX_BODY_LENGTH = 5000
MAX_SUBJECT_LENGTH = 200


async def _ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    from core.database import db
    await db.raw_executescript(_MSG_SCHEMA)
    _schema_ready = True
    logger.info("[Messaging] Schema pret")


def _check_msg_rate(api_key: str) -> None:
    """Enforce 20 messages/hour rate limit per sender."""
    now = time.time()
    timestamps = _send_timestamps.get(api_key, [])

    # Prune old timestamps outside the window
    timestamps = [t for t in timestamps if now - t < _MSG_RATE_WINDOW]
    _send_timestamps[api_key] = timestamps

    if len(timestamps) >= _MSG_RATE_LIMIT:
        raise HTTPException(429, f"Rate limit: max {_MSG_RATE_LIMIT} messages per hour")

    # Cap total tracked keys to prevent unbounded memory growth
    if len(_send_timestamps) > 10000:
        cutoff = now - _MSG_RATE_WINDOW
        stale_keys = [k for k, v in _send_timestamps.items() if not v or v[-1] < cutoff]
        for k in stale_keys:
            _send_timestamps.pop(k, None)


async def _read_body(request: Request) -> dict:
    raw = await request.body()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(400, "Invalid JSON body")


def _row_to_dict(row, keys: list[str]) -> dict:
    """Convert a DB row (dict or tuple) to a dict."""
    if row is None:
        return {}
    if isinstance(row, dict):
        return row
    return dict(zip(keys, row))


_MSG_COLUMNS = ["id", "thread_id", "sender_key", "recipient_key", "subject", "body", "read", "created_at"]


async def _resolve_recipient(identifier: str) -> Optional[str]:
    """Resolve an agent_id, wallet, or name to an API key.

    Looks up the agents table by agent_id (primary key), wallet, or name.
    Returns the API key of the matched agent, or None.
    """
    from core.database import db

    # Try by agent_id first
    row = await db._fetchone(
        "SELECT api_key FROM agents WHERE id=?", (identifier,))
    if row:
        return row["api_key"] if isinstance(row, dict) else row[0]

    # Try by wallet
    row = await db._fetchone(
        "SELECT api_key FROM agents WHERE wallet=?", (identifier,))
    if row:
        return row["api_key"] if isinstance(row, dict) else row[0]

    # Try by name (exact match, case-insensitive)
    row = await db._fetchone(
        "SELECT api_key FROM agents WHERE LOWER(name)=LOWER(?)", (identifier,))
    if row:
        return row["api_key"] if isinstance(row, dict) else row[0]

    return None


async def _get_agent_public_info(api_key: str) -> dict:
    """Get public info (name, wallet) for an agent. Never expose the API key."""
    from marketplace.public_api_shared import _registered_agents, _load_from_db
    await _load_from_db()
    agent = _registered_agents.get(api_key)
    if agent:
        return {
            "name": agent.get("name", "Unknown"),
            "wallet": agent.get("wallet", ""),
        }
    # Fallback: query DB directly
    from core.database import db
    row = await db._fetchone(
        "SELECT name, wallet FROM agents WHERE api_key=?", (api_key,))
    if row:
        if isinstance(row, dict):
            return {"name": row.get("name", "Unknown"), "wallet": row.get("wallet", "")}
        return {"name": row[0] or "Unknown", "wallet": row[1] or ""}
    return {"name": "Unknown", "wallet": ""}


def _format_message(msg: dict, sender_info: dict) -> dict:
    """Format a message for API response — never expose API keys."""
    return {
        "id": msg.get("id"),
        "thread_id": msg.get("thread_id"),
        "sender": sender_info,
        "subject": msg.get("subject"),
        "body": msg.get("body"),
        "read": bool(msg.get("read")),
        "created_at": msg.get("created_at"),
    }


# ── Endpoints ──

@router.post("/api/messages/send")
async def send_message(request: Request):
    """Send a message to another agent. Auth: X-API-Key (sender)."""
    await _ensure_schema()
    from marketplace.public_api_shared import _get_agent, _load_from_db
    from core.security import check_content_safety
    from core.database import db
    await _load_from_db()

    api_key = (request.headers.get("X-API-Key") or "").strip()
    if not api_key:
        raise HTTPException(401, "X-API-Key header required")
    sender = _get_agent(api_key, request.client.host if request.client else "")

    body = await _read_body(request)
    to = (body.get("to") or "").strip()
    subject = (body.get("subject") or "").strip()[:MAX_SUBJECT_LENGTH]
    msg_body = (body.get("body") or "").strip()
    thread_id = (body.get("thread_id") or "").strip() or None

    if not to:
        raise HTTPException(400, "Field 'to' required (agent_id, wallet, or name)")
    if not msg_body:
        raise HTTPException(400, "Field 'body' required")
    if len(msg_body) > MAX_BODY_LENGTH:
        raise HTTPException(400, f"Message body too long (max {MAX_BODY_LENGTH} chars)")

    # Content safety check
    check_content_safety(msg_body, "message body")
    if subject:
        check_content_safety(subject, "message subject")

    # Rate limit
    _check_msg_rate(api_key)

    # Resolve recipient
    recipient_key = await _resolve_recipient(to)
    if not recipient_key:
        raise HTTPException(404, "Recipient agent not found")

    # Prevent self-messaging
    if recipient_key == api_key:
        raise HTTPException(400, "Cannot send message to yourself")

    # Generate thread_id if not provided
    if not thread_id:
        thread_id = f"thread_{uuid.uuid4().hex[:16]}"

    msg_id = f"msg_{uuid.uuid4().hex[:16]}"
    now = int(time.time())

    try:
        await db.raw_execute(
            "INSERT INTO agent_messages (id, thread_id, sender_key, recipient_key, subject, body, read, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
            (msg_id, thread_id, api_key, recipient_key, subject or None, msg_body, now))
    except Exception as e:
        logger.error("[Messaging] send DB error: %s", e)
        raise HTTPException(500, "Failed to send message")

    # Record timestamp for rate limiting
    _send_timestamps.setdefault(api_key, []).append(time.time())

    return {
        "success": True,
        "message_id": msg_id,
        "thread_id": thread_id,
    }


@router.get("/api/messages/inbox")
async def get_inbox(request: Request,
                    limit: int = Query(default=20, ge=1, le=100),
                    offset: int = Query(default=0, ge=0),
                    unread: bool = Query(default=False)):
    """Get received messages. Auth: X-API-Key."""
    await _ensure_schema()
    from marketplace.public_api_shared import _get_agent, _load_from_db
    from core.database import db
    await _load_from_db()

    api_key = (request.headers.get("X-API-Key") or "").strip()
    if not api_key:
        raise HTTPException(401, "X-API-Key header required")
    _get_agent(api_key, request.client.host if request.client else "")

    try:
        if unread:
            rows = await db.raw_execute_fetchall(
                "SELECT id, thread_id, sender_key, recipient_key, subject, body, read, created_at "
                "FROM agent_messages WHERE recipient_key=? AND read=0 "
                "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (api_key, limit, offset))
        else:
            rows = await db.raw_execute_fetchall(
                "SELECT id, thread_id, sender_key, recipient_key, subject, body, read, created_at "
                "FROM agent_messages WHERE recipient_key=? "
                "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (api_key, limit, offset))

        messages = []
        for r in rows:
            msg = _row_to_dict(r, _MSG_COLUMNS)
            sender_info = await _get_agent_public_info(msg.get("sender_key", ""))
            messages.append(_format_message(msg, sender_info))

        return {"messages": messages, "count": len(messages)}
    except Exception as e:
        logger.error("[Messaging] inbox error: %s", e)
        raise HTTPException(500, "Internal error")


@router.get("/api/messages/thread/{thread_id}")
async def get_thread(thread_id: str, request: Request):
    """Get full conversation in a thread. Auth: X-API-Key (must be participant)."""
    await _ensure_schema()
    from marketplace.public_api_shared import _get_agent, _load_from_db
    from core.database import db
    await _load_from_db()

    api_key = (request.headers.get("X-API-Key") or "").strip()
    if not api_key:
        raise HTTPException(401, "X-API-Key header required")
    _get_agent(api_key, request.client.host if request.client else "")

    try:
        rows = await db.raw_execute_fetchall(
            "SELECT id, thread_id, sender_key, recipient_key, subject, body, read, created_at "
            "FROM agent_messages WHERE thread_id=? "
            "ORDER BY created_at ASC",
            (thread_id,))

        if not rows:
            raise HTTPException(404, "Thread not found")

        # Verify caller is a participant
        participants = set()
        for r in rows:
            msg = _row_to_dict(r, _MSG_COLUMNS)
            participants.add(msg.get("sender_key", ""))
            participants.add(msg.get("recipient_key", ""))

        if api_key not in participants:
            raise HTTPException(403, "You are not a participant in this thread")

        messages = []
        for r in rows:
            msg = _row_to_dict(r, _MSG_COLUMNS)
            sender_info = await _get_agent_public_info(msg.get("sender_key", ""))
            messages.append(_format_message(msg, sender_info))

        return {"thread_id": thread_id, "messages": messages, "count": len(messages)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[Messaging] thread error: %s", e)
        raise HTTPException(500, "Internal error")


@router.post("/api/messages/reply")
async def reply_message(request: Request):
    """Reply to a message. Auto-sets thread_id from original. Auth: X-API-Key."""
    await _ensure_schema()
    from marketplace.public_api_shared import _get_agent, _load_from_db
    from core.security import check_content_safety
    from core.database import db
    await _load_from_db()

    api_key = (request.headers.get("X-API-Key") or "").strip()
    if not api_key:
        raise HTTPException(401, "X-API-Key header required")
    sender = _get_agent(api_key, request.client.host if request.client else "")

    body = await _read_body(request)
    message_id = (body.get("message_id") or "").strip()
    reply_body = (body.get("body") or "").strip()

    if not message_id:
        raise HTTPException(400, "Field 'message_id' required")
    if not reply_body:
        raise HTTPException(400, "Field 'body' required")
    if len(reply_body) > MAX_BODY_LENGTH:
        raise HTTPException(400, f"Reply body too long (max {MAX_BODY_LENGTH} chars)")

    # Content safety
    check_content_safety(reply_body, "reply body")

    # Rate limit
    _check_msg_rate(api_key)

    # Fetch original message
    try:
        row = await db._fetchone(
            "SELECT thread_id, sender_key, recipient_key, subject FROM agent_messages WHERE id=?",
            (message_id,))
    except Exception as e:
        logger.error("[Messaging] reply fetch error: %s", e)
        raise HTTPException(500, "Internal error")

    if not row:
        raise HTTPException(404, "Original message not found")

    orig = _row_to_dict(row, ["thread_id", "sender_key", "recipient_key", "subject"])

    # Verify the replier is a participant (sender or recipient of original)
    if api_key not in (orig.get("sender_key"), orig.get("recipient_key")):
        raise HTTPException(403, "You are not a participant in this conversation")

    # Reply goes to the other party
    if api_key == orig.get("sender_key"):
        reply_to_key = orig.get("recipient_key", "")
    else:
        reply_to_key = orig.get("sender_key", "")

    thread_id = orig.get("thread_id", f"thread_{uuid.uuid4().hex[:16]}")
    subject = orig.get("subject")
    if subject and not subject.startswith("Re: "):
        subject = f"Re: {subject}"

    msg_id = f"msg_{uuid.uuid4().hex[:16]}"
    now = int(time.time())

    try:
        await db.raw_execute(
            "INSERT INTO agent_messages (id, thread_id, sender_key, recipient_key, subject, body, read, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
            (msg_id, thread_id, api_key, reply_to_key, subject, reply_body, now))
    except Exception as e:
        logger.error("[Messaging] reply DB error: %s", e)
        raise HTTPException(500, "Failed to send reply")

    # Record timestamp for rate limiting
    _send_timestamps.setdefault(api_key, []).append(time.time())

    return {
        "success": True,
        "message_id": msg_id,
        "thread_id": thread_id,
    }


@router.patch("/api/messages/{message_id}/read")
async def mark_read(message_id: str, request: Request):
    """Mark a message as read. Auth: X-API-Key (must be recipient)."""
    await _ensure_schema()
    from marketplace.public_api_shared import _get_agent, _load_from_db
    from core.database import db
    await _load_from_db()

    api_key = (request.headers.get("X-API-Key") or "").strip()
    if not api_key:
        raise HTTPException(401, "X-API-Key header required")
    _get_agent(api_key, request.client.host if request.client else "")

    try:
        row = await db._fetchone(
            "SELECT recipient_key FROM agent_messages WHERE id=?", (message_id,))
    except Exception as e:
        logger.error("[Messaging] mark_read fetch error: %s", e)
        raise HTTPException(500, "Internal error")

    if not row:
        raise HTTPException(404, "Message not found")

    recipient = row["recipient_key"] if isinstance(row, dict) else row[0]
    if recipient != api_key:
        raise HTTPException(403, "Only the recipient can mark a message as read")

    try:
        await db.raw_execute(
            "UPDATE agent_messages SET read=1 WHERE id=?", (message_id,))
    except Exception as e:
        logger.error("[Messaging] mark_read update error: %s", e)
        raise HTTPException(500, "Failed to mark as read")

    return {"success": True}


@router.get("/api/messages/stats")
async def message_stats(request: Request):
    """Unread count, total conversations. Auth: X-API-Key."""
    await _ensure_schema()
    from marketplace.public_api_shared import _get_agent, _load_from_db
    from core.database import db
    await _load_from_db()

    api_key = (request.headers.get("X-API-Key") or "").strip()
    if not api_key:
        raise HTTPException(401, "X-API-Key header required")
    _get_agent(api_key, request.client.host if request.client else "")

    try:
        # Unread count
        row = await db._fetchone(
            "SELECT COUNT(*) FROM agent_messages WHERE recipient_key=? AND read=0",
            (api_key,))
        unread = (row["COUNT(*)"] if isinstance(row, dict) else row[0]) if row else 0

        # Total conversations (unique threads where agent is sender or recipient)
        row2 = await db._fetchone(
            "SELECT COUNT(DISTINCT thread_id) FROM agent_messages "
            "WHERE sender_key=? OR recipient_key=?",
            (api_key, api_key))
        total_threads = (row2["COUNT(DISTINCT thread_id)"] if isinstance(row2, dict) else row2[0]) if row2 else 0

        # Total messages received
        row3 = await db._fetchone(
            "SELECT COUNT(*) FROM agent_messages WHERE recipient_key=?",
            (api_key,))
        total_received = (row3["COUNT(*)"] if isinstance(row3, dict) else row3[0]) if row3 else 0

        # Total messages sent
        row4 = await db._fetchone(
            "SELECT COUNT(*) FROM agent_messages WHERE sender_key=?",
            (api_key,))
        total_sent = (row4["COUNT(*)"] if isinstance(row4, dict) else row4[0]) if row4 else 0

        return {
            "unread": unread,
            "total_threads": total_threads,
            "total_received": total_received,
            "total_sent": total_sent,
        }
    except Exception as e:
        logger.error("[Messaging] stats error: %s", e)
        raise HTTPException(500, "Internal error")
