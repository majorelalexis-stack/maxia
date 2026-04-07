"""MAXIA Memory-as-a-Service — Persistent agent memory via API (PRO-K16).

Agents store and retrieve memories (key-value with metadata) through the MAXIA API.
Memories persist across sessions. Agents can search, update, and expire memories.

Use cases:
- Agent stores learned preferences, strategies, contact info
- Agent retrieves context from previous sessions
- Cross-agent memory sharing (with permissions)

Pricing: deducted from prepaid credits ($0.001 per write, reads are free).
"""
import json
import logging
import time
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel, Field
from core.error_utils import safe_error

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/memory", tags=["memory-service"])

# ══════════════════════════════════════════
# Schema
# ══════════════════════════════════════════

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_memories (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    namespace TEXT DEFAULT 'default',
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    metadata TEXT DEFAULT '{}',
    importance REAL DEFAULT 0.5,
    access_count INTEGER DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    expires_at INTEGER DEFAULT 0,
    UNIQUE(agent_id, namespace, key)
);
CREATE INDEX IF NOT EXISTS idx_mem_agent ON agent_memories(agent_id, namespace);
CREATE INDEX IF NOT EXISTS idx_mem_key ON agent_memories(agent_id, key);
CREATE INDEX IF NOT EXISTS idx_mem_importance ON agent_memories(agent_id, importance DESC);
"""

_schema_ready = False
_WRITE_COST_USDC = 0.001  # $0.001 per write
_MAX_MEMORIES_PER_AGENT = 1000
_MAX_VALUE_SIZE = 10000  # 10KB per memory


async def _ensure_schema():
    global _schema_ready
    if _schema_ready:
        return
    try:
        from core.database import db
        await db.raw_executescript(_SCHEMA)
        _schema_ready = True
    except Exception as e:
        logger.error("[Memory] Schema init error: %s", e)


async def _get_agent_id(api_key: str) -> Optional[str]:
    """Resolve api_key to agent_id."""
    from core.database import db
    row = await db._fetchone(
        "SELECT agent_id FROM agent_permissions WHERE api_key=? AND status='active'",
        (api_key,))
    return row["agent_id"] if row else None


def _validate_key(x_api_key: Optional[str]) -> str:
    if not x_api_key or not x_api_key.startswith("maxia_"):
        raise HTTPException(401, "Missing or invalid X-API-Key header")
    return x_api_key


# ══════════════════════════════════════════
# Models
# ══════════════════════════════════════════

class StoreMemoryRequest(BaseModel):
    key: str = Field(..., min_length=1, max_length=200)
    value: str = Field(..., min_length=1, max_length=10000)
    namespace: str = Field("default", max_length=50)
    metadata: dict = Field(default_factory=dict)
    importance: float = Field(0.5, ge=0.0, le=1.0)
    ttl_seconds: int = Field(0, ge=0, le=31536000, description="Time to live (0 = never expires)")


class SearchMemoryRequest(BaseModel):
    query: str = Field("", max_length=200)
    namespace: str = Field("", max_length=50)
    min_importance: float = Field(0.0, ge=0.0, le=1.0)
    limit: int = Field(20, ge=1, le=100)


# ══════════════════════════════════════════
# Endpoints
# ══════════════════════════════════════════

@router.post("/store")
async def store_memory(req: StoreMemoryRequest, x_api_key: str = Header(None)):
    """Store a memory. Costs $0.001 per write (deducted from prepaid credits).

    Memories are persistent across sessions. Use namespaces to organize
    (e.g., 'contacts', 'strategies', 'preferences').
    """
    api_key = _validate_key(x_api_key)
    await _ensure_schema()
    agent_id = await _get_agent_id(api_key)
    if not agent_id:
        raise HTTPException(403, "Agent not found or inactive")

    from core.security import check_content_safety
    safety = check_content_safety(req.value)
    if not safety.get("safe", True):
        raise HTTPException(400, "Memory content flagged by safety filter")

    from core.database import db
    now = int(time.time())
    expires = now + req.ttl_seconds if req.ttl_seconds > 0 else 0

    # Check memory limit
    count_row = await db._fetchone(
        "SELECT COUNT(*) as cnt FROM agent_memories WHERE agent_id=?", (agent_id,))
    current_count = count_row["cnt"] if count_row else 0

    # Check if updating existing memory (upsert)
    existing = await db._fetchone(
        "SELECT id FROM agent_memories WHERE agent_id=? AND namespace=? AND key=?",
        (agent_id, req.namespace, req.key))

    if not existing and current_count >= _MAX_MEMORIES_PER_AGENT:
        raise HTTPException(400, f"Memory limit reached ({_MAX_MEMORIES_PER_AGENT}). Delete old memories first.")

    # Charge for write
    from billing.prepaid_credits import deduct_credits
    charge = await deduct_credits(agent_id, _WRITE_COST_USDC, f"memory:store:{req.key[:50]}")
    if not charge.get("success"):
        raise HTTPException(402, f"Insufficient credits. Need ${_WRITE_COST_USDC}. "
                            f"Deposit USDC: POST /api/credits/deposit")

    metadata_json = json.dumps(req.metadata, ensure_ascii=False)[:2000]

    if existing:
        await db.raw_execute(
            "UPDATE agent_memories SET value=?, metadata=?, importance=?, "
            "updated_at=?, expires_at=? WHERE id=?",
            (req.value, metadata_json, req.importance, now, expires, existing["id"]))
        action = "updated"
        mem_id = existing["id"]
    else:
        mem_id = str(uuid.uuid4())
        await db.raw_execute(
            "INSERT INTO agent_memories(id, agent_id, namespace, key, value, metadata, "
            "importance, access_count, created_at, updated_at, expires_at) "
            "VALUES(?,?,?,?,?,?,?,0,?,?,?)",
            (mem_id, agent_id, req.namespace, req.key, req.value,
             metadata_json, req.importance, now, now, expires))
        action = "stored"

    logger.info("[Memory] Agent %s %s: %s/%s", agent_id[:8], action, req.namespace, req.key)

    return {
        "status": "ok",
        "action": action,
        "memory_id": mem_id,
        "charged_usdc": _WRITE_COST_USDC,
        "credit_balance": charge.get("balance", 0),
    }


@router.get("/recall/{key}")
async def recall_memory(key: str, namespace: str = "default", x_api_key: str = Header(None)):
    """Recall a specific memory by key. Free (no credit charge)."""
    api_key = _validate_key(x_api_key)
    await _ensure_schema()
    agent_id = await _get_agent_id(api_key)
    if not agent_id:
        raise HTTPException(403, "Agent not found or inactive")

    from core.database import db
    now = int(time.time())

    row = await db._fetchone(
        "SELECT id, key, value, metadata, importance, access_count, created_at, updated_at, expires_at "
        "FROM agent_memories WHERE agent_id=? AND namespace=? AND key=? "
        "AND (expires_at=0 OR expires_at>?)",
        (agent_id, namespace, key, now))

    if not row:
        raise HTTPException(404, f"Memory '{key}' not found in namespace '{namespace}'")

    # Increment access count
    await db.raw_execute(
        "UPDATE agent_memories SET access_count=access_count+1 WHERE id=?", (row["id"],))

    result = dict(row)
    try:
        result["metadata"] = json.loads(result.get("metadata", "{}"))
    except (json.JSONDecodeError, TypeError):
        result["metadata"] = {}

    return {"status": "ok", "memory": result}


@router.post("/search")
async def search_memories(req: SearchMemoryRequest, x_api_key: str = Header(None)):
    """Search memories by keyword, namespace, or importance. Free."""
    api_key = _validate_key(x_api_key)
    await _ensure_schema()
    agent_id = await _get_agent_id(api_key)
    if not agent_id:
        raise HTTPException(403, "Agent not found or inactive")

    from core.database import db
    now = int(time.time())

    conditions = ["agent_id=?", "(expires_at=0 OR expires_at>?)"]
    params: list = [agent_id, now]

    if req.namespace:
        conditions.append("namespace=?")
        params.append(req.namespace)

    if req.min_importance > 0:
        conditions.append("importance>=?")
        params.append(req.min_importance)

    if req.query:
        conditions.append("(key LIKE ? OR value LIKE ?)")
        like = f"%{req.query}%"
        params.extend([like, like])

    where = " AND ".join(conditions)
    params.append(req.limit)

    rows = await db._fetchall(
        f"SELECT id, namespace, key, value, metadata, importance, access_count, "
        f"created_at, updated_at FROM agent_memories WHERE {where} "
        f"ORDER BY importance DESC, updated_at DESC LIMIT ?",
        tuple(params))

    memories = []
    for r in rows:
        m = dict(r)
        try:
            m["metadata"] = json.loads(m.get("metadata", "{}"))
        except (json.JSONDecodeError, TypeError):
            m["metadata"] = {}
        memories.append(m)

    return {"status": "ok", "count": len(memories), "memories": memories}


@router.delete("/forget/{key}")
async def forget_memory(key: str, namespace: str = "default", x_api_key: str = Header(None)):
    """Delete a specific memory. Free."""
    api_key = _validate_key(x_api_key)
    await _ensure_schema()
    agent_id = await _get_agent_id(api_key)
    if not agent_id:
        raise HTTPException(403, "Agent not found or inactive")

    from core.database import db
    await db.raw_execute(
        "DELETE FROM agent_memories WHERE agent_id=? AND namespace=? AND key=?",
        (agent_id, namespace, key))

    return {"status": "ok", "forgotten": key, "namespace": namespace}


@router.get("/stats")
async def memory_stats(x_api_key: str = Header(None)):
    """Get memory usage statistics for the agent."""
    api_key = _validate_key(x_api_key)
    await _ensure_schema()
    agent_id = await _get_agent_id(api_key)
    if not agent_id:
        raise HTTPException(403, "Agent not found or inactive")

    from core.database import db
    now = int(time.time())

    total = await db._fetchone(
        "SELECT COUNT(*) as cnt FROM agent_memories WHERE agent_id=?", (agent_id,))
    active = await db._fetchone(
        "SELECT COUNT(*) as cnt FROM agent_memories WHERE agent_id=? AND (expires_at=0 OR expires_at>?)",
        (agent_id, now))
    namespaces = await db._fetchall(
        "SELECT namespace, COUNT(*) as cnt FROM agent_memories WHERE agent_id=? GROUP BY namespace",
        (agent_id,))
    top = await db._fetchall(
        "SELECT key, namespace, importance, access_count FROM agent_memories "
        "WHERE agent_id=? ORDER BY access_count DESC LIMIT 5", (agent_id,))

    return {
        "agent_id": agent_id,
        "total_memories": total["cnt"] if total else 0,
        "active_memories": active["cnt"] if active else 0,
        "max_memories": _MAX_MEMORIES_PER_AGENT,
        "namespaces": {r["namespace"]: r["cnt"] for r in namespaces},
        "most_accessed": [dict(r) for r in top],
        "write_cost_usdc": _WRITE_COST_USDC,
    }
