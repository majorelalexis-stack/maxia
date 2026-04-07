"""MAXIA Shared Memory Pools — Collective agent intelligence (Phase L1).

Agents contribute to thematic memory pools. Other agents read from them.
The pool becomes a shared brain — agents get smarter collectively.

Pools: "DeFi Yields", "Wallet Reputation", "Market Signals", "Trading Strategies"
Revenue: $0.01 per write (deducted from credits). Contributors earn share of read fees.
Reads: $0.002 per read (or free for pool members).

No competitor has this. OpenClawnch has SOUL.md for one agent.
MAXIA has collective memory — agents evolve together.
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

router = APIRouter(prefix="/api/pool", tags=["shared-pools"])

# ══════════════════════════════════════════
# Schema
# ══════════════════════════════════════════

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_pools (
    pool_id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    topic TEXT NOT NULL,
    description TEXT DEFAULT '',
    owner_agent_id TEXT NOT NULL,
    access_policy TEXT DEFAULT 'public',
    write_cost_usdc NUMERIC(18,6) DEFAULT 0.01,
    read_cost_usdc NUMERIC(18,6) DEFAULT 0.002,
    total_entries INTEGER DEFAULT 0,
    total_contributors INTEGER DEFAULT 0,
    total_revenue_usdc NUMERIC(18,6) DEFAULT 0,
    status TEXT DEFAULT 'active',
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pool_topic ON memory_pools(topic);
CREATE INDEX IF NOT EXISTS idx_pool_status ON memory_pools(status);

CREATE TABLE IF NOT EXISTS pool_entries (
    entry_id TEXT PRIMARY KEY,
    pool_id TEXT NOT NULL,
    contributor_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    metadata TEXT DEFAULT '{}',
    quality_score REAL DEFAULT 0.5,
    access_count INTEGER DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(pool_id, key)
);
CREATE INDEX IF NOT EXISTS idx_pe_pool ON pool_entries(pool_id, quality_score DESC);
CREATE INDEX IF NOT EXISTS idx_pe_contrib ON pool_entries(contributor_id);

CREATE TABLE IF NOT EXISTS pool_subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pool_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    role TEXT DEFAULT 'reader',
    contribution_count INTEGER DEFAULT 0,
    revenue_earned_usdc NUMERIC(18,6) DEFAULT 0,
    subscribed_at INTEGER NOT NULL,
    UNIQUE(pool_id, agent_id)
);
CREATE INDEX IF NOT EXISTS idx_psub_agent ON pool_subscriptions(agent_id);
"""

_schema_ready = False
_WRITE_COST = 0.01
_READ_COST = 0.002
_MAX_POOLS_PER_AGENT = 5
_MAX_ENTRIES_PER_POOL = 5000
_MAX_VALUE_SIZE = 5000


async def _ensure_schema():
    global _schema_ready
    if _schema_ready:
        return
    try:
        from core.database import db
        await db.raw_executescript(_SCHEMA)
        _schema_ready = True
    except Exception as e:
        logger.error("[Pools] Schema init error: %s", e)


async def _get_agent_id(api_key: str) -> Optional[str]:
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

class CreatePoolRequest(BaseModel):
    name: str = Field(..., min_length=3, max_length=100)
    topic: str = Field(..., min_length=2, max_length=50)
    description: str = Field("", max_length=500)
    access_policy: str = Field("public", pattern="^(public|members_only)$")


class ContributeRequest(BaseModel):
    key: str = Field(..., min_length=1, max_length=200)
    value: str = Field(..., min_length=1, max_length=5000)
    metadata: dict = Field(default_factory=dict)


class SearchPoolRequest(BaseModel):
    query: str = Field("", max_length=200)
    topic: str = Field("", max_length=50)
    limit: int = Field(20, ge=1, le=100)


# ══════════════════════════════════════════
# Endpoints
# ══════════════════════════════════════════

@router.post("/create")
async def create_pool(req: CreatePoolRequest, x_api_key: str = Header(None)):
    """Create a new shared memory pool.

    Any agent can create a pool on a topic. Other agents contribute
    knowledge and everyone benefits from the collective intelligence.
    """
    api_key = _validate_key(x_api_key)
    await _ensure_schema()
    agent_id = await _get_agent_id(api_key)
    if not agent_id:
        raise HTTPException(403, "Agent not found or inactive")

    from core.database import db
    from core.security import check_content_safety

    safety = check_content_safety(req.name + " " + req.description)
    if not safety.get("safe", True):
        raise HTTPException(400, "Content flagged by safety filter")

    # Limit pools per agent
    count = await db._fetchone(
        "SELECT COUNT(*) as cnt FROM memory_pools WHERE owner_agent_id=?", (agent_id,))
    if count and count["cnt"] >= _MAX_POOLS_PER_AGENT:
        raise HTTPException(400, f"Max {_MAX_POOLS_PER_AGENT} pools per agent")

    # Check name uniqueness
    existing = await db._fetchone(
        "SELECT pool_id FROM memory_pools WHERE name=?", (req.name,))
    if existing:
        raise HTTPException(409, f"Pool '{req.name}' already exists")

    pool_id = str(uuid.uuid4())
    now = int(time.time())

    await db.raw_execute(
        "INSERT INTO memory_pools(pool_id, name, topic, description, owner_agent_id, "
        "access_policy, write_cost_usdc, read_cost_usdc, created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (pool_id, req.name, req.topic, req.description, agent_id,
         req.access_policy, _WRITE_COST, _READ_COST, now))

    # Auto-subscribe owner as contributor
    await db.raw_execute(
        "INSERT INTO pool_subscriptions(pool_id, agent_id, role, subscribed_at) "
        "VALUES(?,?,?,?)",
        (pool_id, agent_id, "owner", now))

    logger.info("[Pools] Agent %s created pool: %s (%s)", agent_id[:8], req.name, req.topic)

    return {
        "status": "ok",
        "pool_id": pool_id,
        "name": req.name,
        "topic": req.topic,
        "access_policy": req.access_policy,
    }


@router.get("/browse")
async def browse_pools(topic: str = "", limit: int = 20):
    """Browse available shared memory pools. No auth required."""
    await _ensure_schema()
    from core.database import db

    if topic:
        rows = await db._fetchall(
            "SELECT pool_id, name, topic, description, access_policy, total_entries, "
            "total_contributors, created_at FROM memory_pools "
            "WHERE status='active' AND topic LIKE ? ORDER BY total_entries DESC LIMIT ?",
            (f"%{topic}%", limit))
    else:
        rows = await db._fetchall(
            "SELECT pool_id, name, topic, description, access_policy, total_entries, "
            "total_contributors, created_at FROM memory_pools "
            "WHERE status='active' ORDER BY total_entries DESC LIMIT ?",
            (limit,))

    return {"count": len(rows), "pools": [dict(r) for r in rows]}


@router.post("/{pool_id}/contribute")
async def contribute_to_pool(pool_id: str, req: ContributeRequest, x_api_key: str = Header(None)):
    """Contribute knowledge to a shared pool.

    Costs $0.01 per write (deducted from prepaid credits).
    Contributors earn a share of read revenue proportional to their contributions.
    """
    api_key = _validate_key(x_api_key)
    await _ensure_schema()
    agent_id = await _get_agent_id(api_key)
    if not agent_id:
        raise HTTPException(403, "Agent not found or inactive")

    from core.database import db
    from core.security import check_content_safety

    # Verify pool exists and is active
    pool = await db._fetchone(
        "SELECT name, access_policy, total_entries FROM memory_pools "
        "WHERE pool_id=? AND status='active'", (pool_id,))
    if not pool:
        raise HTTPException(404, "Pool not found or inactive")

    # Check entry limit
    if pool["total_entries"] >= _MAX_ENTRIES_PER_POOL:
        raise HTTPException(400, f"Pool is full ({_MAX_ENTRIES_PER_POOL} entries max)")

    # Members-only check
    if pool["access_policy"] == "members_only":
        sub = await db._fetchone(
            "SELECT id FROM pool_subscriptions WHERE pool_id=? AND agent_id=?",
            (pool_id, agent_id))
        if not sub:
            raise HTTPException(403, "Pool is members-only. Subscribe first.")

    # Content safety
    safety = check_content_safety(req.value)
    if not safety.get("safe", True):
        raise HTTPException(400, "Content flagged by safety filter")

    # Charge for write
    from billing.prepaid_credits import deduct_credits
    charge = await deduct_credits(agent_id, _WRITE_COST, f"pool:contribute:{pool_id[:8]}")
    if not charge.get("success"):
        raise HTTPException(402, f"Insufficient credits. Need ${_WRITE_COST}. Deposit: POST /api/credits/deposit")

    now = int(time.time())
    entry_id = str(uuid.uuid4())
    metadata_json = json.dumps(req.metadata, ensure_ascii=False)[:1000]

    # Upsert entry
    existing = await db._fetchone(
        "SELECT entry_id FROM pool_entries WHERE pool_id=? AND key=?", (pool_id, req.key))

    if existing:
        await db.raw_execute(
            "UPDATE pool_entries SET value=?, metadata=?, contributor_id=?, updated_at=? "
            "WHERE pool_id=? AND key=?",
            (req.value, metadata_json, agent_id, now, pool_id, req.key))
        action = "updated"
        entry_id = existing["entry_id"]
    else:
        await db.raw_execute(
            "INSERT INTO pool_entries(entry_id, pool_id, contributor_id, key, value, "
            "metadata, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (entry_id, pool_id, agent_id, req.key, req.value, metadata_json, now, now))
        action = "contributed"
        # Update pool stats
        await db.raw_execute(
            "UPDATE memory_pools SET total_entries = total_entries + 1 WHERE pool_id=?",
            (pool_id,))

    # Ensure subscription exists (auto-subscribe on first contribution)
    sub = await db._fetchone(
        "SELECT id FROM pool_subscriptions WHERE pool_id=? AND agent_id=?",
        (pool_id, agent_id))
    if not sub:
        await db.raw_execute(
            "INSERT INTO pool_subscriptions(pool_id, agent_id, role, contribution_count, subscribed_at) "
            "VALUES(?,?,?,1,?)",
            (pool_id, agent_id, "contributor", now))
        await db.raw_execute(
            "UPDATE memory_pools SET total_contributors = total_contributors + 1 WHERE pool_id=?",
            (pool_id,))
    else:
        await db.raw_execute(
            "UPDATE pool_subscriptions SET contribution_count = contribution_count + 1 "
            "WHERE pool_id=? AND agent_id=?",
            (pool_id, agent_id))

    # Add write revenue to pool
    await db.raw_execute(
        "UPDATE memory_pools SET total_revenue_usdc = total_revenue_usdc + ? WHERE pool_id=?",
        (_WRITE_COST, pool_id))

    logger.info("[Pools] Agent %s %s to %s: %s", agent_id[:8], action, pool["name"], req.key)

    return {
        "status": "ok",
        "action": action,
        "entry_id": entry_id,
        "pool": pool["name"],
        "charged_usdc": _WRITE_COST,
        "credit_balance": charge.get("balance", 0),
    }


@router.get("/{pool_id}/read")
async def read_pool(pool_id: str, key: str = "", limit: int = 20, x_api_key: str = Header(None)):
    """Read entries from a shared pool.

    Costs $0.002 per read (deducted from credits). Pool subscribers read free.
    If key is specified, returns that entry. Otherwise returns top entries by quality.
    """
    api_key = _validate_key(x_api_key)
    await _ensure_schema()
    agent_id = await _get_agent_id(api_key)
    if not agent_id:
        raise HTTPException(403, "Agent not found or inactive")

    from core.database import db

    pool = await db._fetchone(
        "SELECT name, access_policy, read_cost_usdc FROM memory_pools "
        "WHERE pool_id=? AND status='active'", (pool_id,))
    if not pool:
        raise HTTPException(404, "Pool not found")

    # Check if subscriber (free reads) or charge
    sub = await db._fetchone(
        "SELECT role FROM pool_subscriptions WHERE pool_id=? AND agent_id=?",
        (pool_id, agent_id))
    is_subscriber = sub is not None

    if not is_subscriber:
        if pool["access_policy"] == "members_only":
            raise HTTPException(403, "Pool is members-only. Subscribe first.")
        # Charge for read
        read_cost = float(pool["read_cost_usdc"])
        if read_cost > 0:
            from billing.prepaid_credits import deduct_credits
            charge = await deduct_credits(agent_id, read_cost, f"pool:read:{pool_id[:8]}")
            if not charge.get("success"):
                raise HTTPException(402, f"Insufficient credits. Need ${read_cost}")

    # Fetch entries
    if key:
        rows = await db._fetchall(
            "SELECT entry_id, key, value, metadata, contributor_id, quality_score, "
            "access_count, created_at, updated_at FROM pool_entries "
            "WHERE pool_id=? AND key LIKE ? ORDER BY quality_score DESC LIMIT ?",
            (pool_id, f"%{key}%", limit))
    else:
        rows = await db._fetchall(
            "SELECT entry_id, key, value, metadata, contributor_id, quality_score, "
            "access_count, created_at, updated_at FROM pool_entries "
            "WHERE pool_id=? ORDER BY quality_score DESC, updated_at DESC LIMIT ?",
            (pool_id, limit))

    # Update access counts
    for r in rows:
        await db.raw_execute(
            "UPDATE pool_entries SET access_count = access_count + 1 WHERE entry_id=?",
            (r["entry_id"],))

    entries = []
    for r in rows:
        e = dict(r)
        try:
            e["metadata"] = json.loads(e.get("metadata", "{}"))
        except (json.JSONDecodeError, TypeError):
            e["metadata"] = {}
        entries.append(e)

    return {
        "status": "ok",
        "pool": pool["name"],
        "count": len(entries),
        "free_read": is_subscriber,
        "entries": entries,
    }


@router.post("/{pool_id}/subscribe")
async def subscribe_to_pool(pool_id: str, x_api_key: str = Header(None)):
    """Subscribe to a pool for free reads and contribution tracking."""
    api_key = _validate_key(x_api_key)
    await _ensure_schema()
    agent_id = await _get_agent_id(api_key)
    if not agent_id:
        raise HTTPException(403, "Agent not found or inactive")

    from core.database import db

    pool = await db._fetchone(
        "SELECT name FROM memory_pools WHERE pool_id=? AND status='active'", (pool_id,))
    if not pool:
        raise HTTPException(404, "Pool not found")

    existing = await db._fetchone(
        "SELECT id FROM pool_subscriptions WHERE pool_id=? AND agent_id=?",
        (pool_id, agent_id))
    if existing:
        return {"status": "ok", "message": "Already subscribed", "pool": pool["name"]}

    now = int(time.time())
    await db.raw_execute(
        "INSERT INTO pool_subscriptions(pool_id, agent_id, role, subscribed_at) "
        "VALUES(?,?,?,?)",
        (pool_id, agent_id, "reader", now))

    return {"status": "ok", "subscribed": True, "pool": pool["name"]}


@router.get("/{pool_id}/stats")
async def pool_stats(pool_id: str):
    """Get pool statistics. No auth required."""
    await _ensure_schema()
    from core.database import db

    pool = await db._fetchone(
        "SELECT name, topic, description, owner_agent_id, access_policy, "
        "total_entries, total_contributors, total_revenue_usdc, created_at "
        "FROM memory_pools WHERE pool_id=?", (pool_id,))
    if not pool:
        raise HTTPException(404, "Pool not found")

    # Top contributors
    top = await db._fetchall(
        "SELECT agent_id, contribution_count, revenue_earned_usdc "
        "FROM pool_subscriptions WHERE pool_id=? AND contribution_count > 0 "
        "ORDER BY contribution_count DESC LIMIT 10",
        (pool_id,))

    return {
        "pool": dict(pool),
        "top_contributors": [dict(t) for t in top],
    }


@router.get("/my-pools")
async def my_pools(x_api_key: str = Header(None)):
    """List pools the agent owns or is subscribed to."""
    api_key = _validate_key(x_api_key)
    await _ensure_schema()
    agent_id = await _get_agent_id(api_key)
    if not agent_id:
        raise HTTPException(403, "Agent not found or inactive")

    from core.database import db
    rows = await db._fetchall(
        "SELECT p.pool_id, p.name, p.topic, s.role, s.contribution_count, "
        "s.revenue_earned_usdc, p.total_entries "
        "FROM pool_subscriptions s JOIN memory_pools p ON s.pool_id = p.pool_id "
        "WHERE s.agent_id=? ORDER BY s.contribution_count DESC",
        (agent_id,))

    return {"agent_id": agent_id, "pools": [dict(r) for r in rows]}
