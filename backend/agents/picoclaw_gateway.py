"""MAXIA V12 — PicoClaw CEO Gateway (ONE-3).

Lightweight message broker between external platforms (Web/Telegram/Discord/Slack)
and the CEO agent running on local PC. The VPS acts as a relay:

  1. External platform sends a command   → POST /api/ceo/command
  2. CEO local polls for pending commands → GET  /api/ceo/pending
  3. CEO local sends back the response   → POST /api/ceo/respond
  4. External platform reads response     → GET  /api/ceo/response/{cmd_id}

Auth: CEO auth via X-CEO-Key header for /pending and /respond.
      API key for /command (any registered agent can talk to CEO).
"""
import logging
import hmac
import time
import uuid
import re
from typing import Optional
from collections import OrderedDict

from fastapi import APIRouter, HTTPException, Header, Query

logger = logging.getLogger("maxia.picoclaw")
router = APIRouter(prefix="/api/ceo", tags=["picoclaw-ceo"])

# ── In-memory command queue (persisted to DB) ──
# cmd_id -> {platform, user, message, response, status, created_at, responded_at}
_commands: OrderedDict[str, dict] = OrderedDict()
MAX_QUEUE = 500

_schema_ready = False
_SCHEMA = """
CREATE TABLE IF NOT EXISTS ceo_commands (
    cmd_id TEXT PRIMARY KEY,
    platform TEXT NOT NULL DEFAULT 'web',
    user_id TEXT NOT NULL,
    user_name TEXT DEFAULT '',
    message TEXT NOT NULL,
    response TEXT DEFAULT '',
    status TEXT DEFAULT 'pending',
    priority INTEGER DEFAULT 0,
    created_at INTEGER NOT NULL,
    responded_at INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_ceo_cmd_status ON ceo_commands(status, created_at);
CREATE INDEX IF NOT EXISTS idx_ceo_cmd_user ON ceo_commands(user_id);

CREATE TABLE IF NOT EXISTS ceo_approvals (
    action_id TEXT PRIMARY KEY,
    approved INTEGER NOT NULL DEFAULT 0,
    action_name TEXT DEFAULT '',
    level TEXT DEFAULT 'ORANGE',
    decided_at INTEGER NOT NULL,
    decided_by TEXT DEFAULT 'alexis'
);
CREATE INDEX IF NOT EXISTS idx_ceo_approvals_decided ON ceo_approvals(decided_at DESC);
"""

# Valid approval levels (align with CEO local request_approval())
APPROVAL_LEVELS = {"GREEN", "ORANGE", "RED"}
_ACTION_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")

# Valid platforms
PLATFORMS = {"web", "telegram", "discord", "slack", "api", "mcp"}
_MSG_RE = re.compile(r'^[\s\S]{1,2000}$')


async def _get_db():
    from core.database import db
    return db


async def _ensure_schema():
    global _schema_ready
    if _schema_ready:
        return
    try:
        db = await _get_db()
        await db.raw_executescript(_SCHEMA)
        _schema_ready = True
    except Exception as e:
        logger.error("PicoClaw schema error: %s", e)


def _require_ceo_key(key: str):
    """Validate CEO auth key (timing-safe)."""
    import os
    expected = os.getenv("CEO_API_KEY", "")
    if not expected:
        raise HTTPException(503, "CEO gateway not configured")
    if not hmac.compare_digest(key.encode(), expected.encode()):
        raise HTTPException(401, "Invalid CEO key")


async def _validate_api_key(api_key: str) -> dict:
    """Validate API key against registered agents. Raises 401 if invalid."""
    if not api_key:
        raise HTTPException(401, "X-API-Key required")
    db = await _get_db()
    rows = await db.raw_execute_fetchall(
        "SELECT name, wallet FROM agents WHERE api_key=?", (api_key,))
    if not rows:
        raise HTTPException(401, "Invalid API key")
    return dict(rows[0])


# ══════════════════════════════════════════
#  SUBMIT COMMAND — from any platform
# ══════════════════════════════════════════

@router.post("/command")
async def submit_command(
    req: dict,
    x_api_key: str = Header(None, alias="X-API-Key"),
):
    """Submit a command to the CEO agent. Any registered agent can send commands.

    Body: {
        "message": "Analyze the latest whale movements",
        "platform": "web",       // web|telegram|discord|slack|api|mcp
        "user_id": "agent_123",  // optional, defaults to API key prefix
        "user_name": "MyAgent",  // optional display name
        "priority": 0            // 0=normal, 1=high, 2=urgent
    }
    """
    agent = await _validate_api_key(x_api_key)
    await _ensure_schema()

    message = str(req.get("message", "")).strip()
    if not message or len(message) > 2000:
        raise HTTPException(400, "message required (max 2000 chars)")

    platform = str(req.get("platform", "web")).lower()
    if platform not in PLATFORMS:
        raise HTTPException(400, f"platform must be one of: {', '.join(sorted(PLATFORMS))}")

    user_id = str(req.get("user_id", agent.get("name", x_api_key[:16]))).strip()[:64]
    user_name = str(req.get("user_name", "")).strip()[:64]
    priority = max(0, min(2, int(req.get("priority", 0))))

    cmd_id = str(uuid.uuid4())[:12]
    now = int(time.time())

    db = await _get_db()
    await db.raw_execute(
        "INSERT INTO ceo_commands(cmd_id, platform, user_id, user_name, "
        "message, status, priority, created_at) VALUES(?,?,?,?,?,?,?,?)",
        (cmd_id, platform, user_id, user_name, message, "pending", priority, now))

    # Keep memory queue bounded
    _commands[cmd_id] = {
        "cmd_id": cmd_id, "platform": platform, "user_id": user_id,
        "user_name": user_name, "message": message, "response": "",
        "status": "pending", "priority": priority,
        "created_at": now, "responded_at": 0,
    }
    if len(_commands) > MAX_QUEUE:
        _commands.popitem(last=False)

    return {
        "cmd_id": cmd_id,
        "status": "pending",
        "message": "Command queued for CEO",
        "poll_url": f"/api/ceo/response/{cmd_id}",
    }


# ══════════════════════════════════════════
#  CEO POLLS — get pending commands
# ══════════════════════════════════════════

@router.get("/pending")
async def get_pending_commands(
    x_ceo_key: str = Header(..., alias="X-CEO-Key"),
    limit: int = Query(10, ge=1, le=50),
):
    """CEO local polls this to get pending commands. CEO auth required."""
    _require_ceo_key(x_ceo_key)
    await _ensure_schema()
    db = await _get_db()

    rows = await db.raw_execute_fetchall(
        "SELECT cmd_id, platform, user_id, user_name, message, priority, created_at "
        "FROM ceo_commands WHERE status='pending' "
        "ORDER BY priority DESC, created_at ASC LIMIT ?", (limit,))

    commands = [dict(r) for r in rows]

    # Mark as processing
    for cmd in commands:
        await db.raw_execute(
            "UPDATE ceo_commands SET status='processing' WHERE cmd_id=?",
            (cmd["cmd_id"],))
        if cmd["cmd_id"] in _commands:
            _commands[cmd["cmd_id"]]["status"] = "processing"

    return {"commands": commands, "count": len(commands)}


# ══════════════════════════════════════════
#  CEO RESPONDS — send back answer
# ══════════════════════════════════════════

@router.post("/respond")
async def respond_command(
    req: dict,
    x_ceo_key: str = Header(..., alias="X-CEO-Key"),
):
    """CEO local sends the response back. CEO auth required.

    Body: {"cmd_id": "abc123", "response": "Analysis complete. Top whale..."}
    """
    _require_ceo_key(x_ceo_key)
    await _ensure_schema()

    cmd_id = str(req.get("cmd_id", "")).strip()
    response = str(req.get("response", "")).strip()
    if not cmd_id:
        raise HTTPException(400, "cmd_id required")
    if not response or len(response) > 10000:
        raise HTTPException(400, "response required (max 10000 chars)")

    db = await _get_db()
    now = int(time.time())
    await db.raw_execute(
        "UPDATE ceo_commands SET response=?, status='completed', responded_at=? "
        "WHERE cmd_id=?", (response, now, cmd_id))

    if cmd_id in _commands:
        _commands[cmd_id]["response"] = response
        _commands[cmd_id]["status"] = "completed"
        _commands[cmd_id]["responded_at"] = now

    return {"success": True, "cmd_id": cmd_id}


# ══════════════════════════════════════════
#  READ RESPONSE — external platform reads CEO answer
# ══════════════════════════════════════════

@router.get("/response/{cmd_id}")
async def get_response(
    cmd_id: str,
    x_api_key: str = Header(None, alias="X-API-Key"),
):
    """Read the CEO's response to a command. Returns status + response if ready."""
    await _validate_api_key(x_api_key)
    await _ensure_schema()

    # Check memory first
    if cmd_id in _commands:
        cmd = _commands[cmd_id]
        return {
            "cmd_id": cmd_id,
            "status": cmd["status"],
            "response": cmd["response"] if cmd["status"] == "completed" else "",
            "platform": cmd["platform"],
            "created_at": cmd["created_at"],
            "responded_at": cmd["responded_at"],
        }

    # Fallback to DB
    db = await _get_db()
    rows = await db.raw_execute_fetchall(
        "SELECT cmd_id, platform, message, response, status, "
        "created_at, responded_at "
        "FROM ceo_commands WHERE cmd_id=?", (cmd_id,))
    if not rows:
        raise HTTPException(404, "Command not found")
    r = dict(rows[0])
    return {
        "cmd_id": cmd_id,
        "status": r["status"],
        "response": r["response"] if r["status"] == "completed" else "",
        "platform": r["platform"],
        "created_at": r["created_at"],
        "responded_at": r["responded_at"],
    }


# ══════════════════════════════════════════
#  APPROVAL-RESULT — CEO local notifies VPS of Alexis's decision
# ══════════════════════════════════════════
#
# Flow:
#   1. CEO local (local_ceo/missions/telegram_chat.py) calls
#      request_approval() which sends a Telegram message with GO/NO buttons.
#   2. Alexis taps a button; CEO local receives the callback_query.
#   3. CEO local POSTs to this endpoint with {action_id, approved} so the
#      VPS has an auditable record and can expose status to other services.
#
# Auth: CEO local historically posts WITHOUT headers. To stay compatible we
# accept unauthenticated POSTs, but if X-CEO-Key is provided we validate it
# (strict mode). Idempotent on action_id (first write wins).

@router.post("/approval-result")
async def approval_result(
    req: dict,
    x_ceo_key: Optional[str] = Header(None, alias="X-CEO-Key"),
) -> dict:
    """Record Alexis's approval decision for a pending CEO action.

    Body: {
        "action_id": "abc123",      # required, must match regex ^[A-Za-z0-9_-]{1,64}$
        "approved": true,            # required, bool
        "action_name": "post_tweet", # optional, display name
        "level": "ORANGE",           # optional, GREEN|ORANGE|RED
    }
    """
    # Optional strict auth (if header present, must be valid)
    if x_ceo_key is not None:
        _require_ceo_key(x_ceo_key)

    await _ensure_schema()

    action_id = str(req.get("action_id", "")).strip()
    if not action_id or not _ACTION_ID_RE.match(action_id):
        raise HTTPException(400, "action_id required (alnum/_/-, 1-64 chars)")

    approved_raw = req.get("approved")
    if not isinstance(approved_raw, bool):
        raise HTTPException(400, "approved must be boolean")

    action_name = str(req.get("action_name", ""))[:128]
    level = str(req.get("level", "ORANGE")).upper()
    if level not in APPROVAL_LEVELS:
        level = "ORANGE"

    db = await _get_db()
    now = int(time.time())

    # Idempotent: check existing first
    existing = await db.raw_execute_fetchall(
        "SELECT approved, decided_at FROM ceo_approvals WHERE action_id=?",
        (action_id,),
    )
    if existing:
        row = dict(existing[0])
        return {
            "success": True,
            "action_id": action_id,
            "approved": bool(row.get("approved", 0)),
            "decided_at": int(row.get("decided_at", 0)),
            "idempotent": True,
        }

    await db.raw_execute(
        "INSERT INTO ceo_approvals(action_id, approved, action_name, level, "
        "decided_at, decided_by) VALUES(?,?,?,?,?,?)",
        (action_id, 1 if approved_raw else 0, action_name, level, now, "alexis"),
    )

    logger.info(
        "[CEO] Approval recorded: action_id=%s approved=%s level=%s",
        action_id, approved_raw, level,
    )

    return {
        "success": True,
        "action_id": action_id,
        "approved": approved_raw,
        "decided_at": now,
        "idempotent": False,
    }


@router.get("/approval-result/{action_id}")
async def get_approval_result(
    action_id: str,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
) -> dict:
    """Read the approval decision for a given action_id.

    Auth: optional API key (allows anonymous reads for public audit trail).
    """
    if not _ACTION_ID_RE.match(action_id):
        raise HTTPException(400, "invalid action_id")

    await _ensure_schema()
    db = await _get_db()
    rows = await db.raw_execute_fetchall(
        "SELECT action_id, approved, action_name, level, decided_at, decided_by "
        "FROM ceo_approvals WHERE action_id=?",
        (action_id,),
    )
    if not rows:
        raise HTTPException(404, "action_id not found")

    row = dict(rows[0])
    return {
        "action_id": row["action_id"],
        "approved": bool(row.get("approved", 0)),
        "action_name": row.get("action_name", ""),
        "level": row.get("level", "ORANGE"),
        "decided_at": int(row.get("decided_at", 0)),
        "decided_by": row.get("decided_by", "alexis"),
    }


# ══════════════════════════════════════════
#  STATUS — CEO gateway health
# ══════════════════════════════════════════

@router.get("/gateway/status")
async def gateway_status():
    """PicoClaw gateway status. Public, no auth."""
    await _ensure_schema()
    db = await _get_db()

    try:
        pending = await db.raw_execute_fetchall(
            "SELECT COUNT(*) as cnt FROM ceo_commands WHERE status='pending'")
        processing = await db.raw_execute_fetchall(
            "SELECT COUNT(*) as cnt FROM ceo_commands WHERE status='processing'")
        completed = await db.raw_execute_fetchall(
            "SELECT COUNT(*) as cnt FROM ceo_commands WHERE status='completed'")
        last_response = await db.raw_execute_fetchall(
            "SELECT responded_at FROM ceo_commands WHERE status='completed' "
            "ORDER BY responded_at DESC LIMIT 1")

        p = dict(pending[0]).get("cnt", 0) if pending else 0
        pr = dict(processing[0]).get("cnt", 0) if processing else 0
        c = dict(completed[0]).get("cnt", 0) if completed else 0
        lr = dict(last_response[0]).get("responded_at", 0) if last_response else 0

        return {
            "gateway": "picoclaw",
            "version": "1.0",
            "status": "online",
            "queue": {"pending": p, "processing": pr, "completed": c},
            "last_response_at": lr,
            "platforms": sorted(PLATFORMS),
        }
    except Exception:
        return {"gateway": "picoclaw", "status": "online", "queue": {}}


# ══════════════════════════════════════════
#  HISTORY — recent CEO interactions
# ══════════════════════════════════════════

@router.get("/history")
async def ceo_history(
    x_api_key: str = Header(None, alias="X-API-Key"),
    limit: int = Query(20, ge=1, le=100),
):
    """Recent CEO command history. Auth required."""
    await _validate_api_key(x_api_key)
    await _ensure_schema()
    db = await _get_db()

    rows = await db.raw_execute_fetchall(
        "SELECT cmd_id, platform, user_name, message, "
        "CASE WHEN status='completed' THEN response ELSE '' END as response, "
        "status, created_at, responded_at "
        "FROM ceo_commands ORDER BY created_at DESC LIMIT ?", (limit,))

    return {
        "history": [dict(r) for r in rows],
        "count": len(rows),
    }


def get_router():
    return router
