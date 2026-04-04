"""MAXIA Empire V2 Sprint 4 — Growth: Bounty Board, Developer Program, Agent Analytics.

E10: Bounty Board — post bounties for services/agents, claim with proof, USDC rewards
E24: Developer Program — changelog, grants, dev resources, community links
E20: Agent Analytics — spending breakdown, ROI, top services, daily/weekly/monthly
"""
import hashlib
import json
import logging
import math
import re
import time
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel, Field
from core.error_utils import safe_error

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/public", tags=["empire-sprint4"])

_ID_RE = re.compile(r'^[a-zA-Z0-9_\-]{1,128}$')

# ══════════════════════════════════════════
# DB SCHEMA
# ══════════════════════════════════════════

_SPRINT4_SCHEMA = """
CREATE TABLE IF NOT EXISTS bounties (
    id TEXT PRIMARY KEY,
    creator_api_key_hash TEXT NOT NULL,
    creator_name TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    reward_usdc NUMERIC(18,6) NOT NULL,
    category TEXT DEFAULT 'general',
    status TEXT DEFAULT 'open',
    claimer_name TEXT DEFAULT '',
    claim_proof TEXT DEFAULT '',
    created_at INTEGER DEFAULT (strftime('%s','now')),
    claimed_at INTEGER DEFAULT 0,
    expires_at INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_bounties_status ON bounties(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_bounties_category ON bounties(category);

CREATE TABLE IF NOT EXISTS changelog_entries (
    id TEXT PRIMARY KEY,
    version TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    category TEXT DEFAULT 'feature',
    created_at INTEGER DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_changelog_date ON changelog_entries(created_at DESC);
"""

_schema_initialized = False


async def _ensure_schema():
    global _schema_initialized
    if _schema_initialized:
        return
    try:
        from core.database import db
        await db.raw_executescript(_SPRINT4_SCHEMA)
        _schema_initialized = True
        logger.info("[Sprint4] Bounties + Changelog tables ready")
    except Exception as e:
        logger.error("[Sprint4] Schema init error: %s", e)


async def _get_db():
    from core.database import db
    await _ensure_schema()
    return db


async def _get_agent(api_key: str) -> dict:
    db = await _get_db()
    rows = await db.raw_execute_fetchall(
        "SELECT name, wallet FROM agents WHERE api_key=?", (api_key,))
    if not rows:
        raise HTTPException(401, "Invalid API key")
    return dict(rows[0])


def _hash_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()


# ══════════════════════════════════════════
# E10 — BOUNTY BOARD
# ══════════════════════════════════════════

class BountyCreateRequest(BaseModel):
    title: str = Field(..., min_length=5, max_length=200)
    description: str = Field(..., min_length=20, max_length=2000)
    reward_usdc: float = Field(..., gt=0, le=10000)
    category: str = Field("general", max_length=50)
    expires_days: int = Field(30, ge=1, le=90)


@router.post("/bounties")
async def create_bounty(req: BountyCreateRequest, x_api_key: str = Header(alias="X-API-Key", default="")):
    """Create a bounty — offer USDC for building a service or solving a problem."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")

    agent = await _get_agent(x_api_key)
    db = await _get_db()

    # Content safety
    try:
        from core.security import check_content_safety
        check_content_safety(req.title)
        check_content_safety(req.description)
    except Exception:
        raise HTTPException(400, "Content contains prohibited material")

    bounty_id = f"bounty_{uuid.uuid4().hex[:12]}"
    expires_at = int(time.time()) + req.expires_days * 86400

    await db.raw_execute(
        "INSERT INTO bounties (id, creator_api_key_hash, creator_name, title, description, "
        "reward_usdc, category, status, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?)",
        (bounty_id, _hash_key(x_api_key), agent["name"], req.title,
         req.description, req.reward_usdc, req.category.lower().strip(), expires_at))

    return {
        "success": True,
        "bounty_id": bounty_id,
        "title": req.title,
        "reward_usdc": req.reward_usdc,
        "category": req.category,
        "expires_at": expires_at,
        "status": "open",
    }


@router.get("/bounties")
async def list_bounties(
    status: str = "open",
    category: str = "",
    limit: int = 50,
):
    """List bounties. Filter by status (open/claimed/completed) and category."""
    db = await _get_db()
    limit = max(1, min(100, limit))

    if status not in ("open", "claimed", "completed", "expired", "all"):
        status = "open"

    params: list = []
    query = "SELECT id, creator_name, title, description, reward_usdc, category, status, " \
            "claimer_name, created_at, expires_at FROM bounties"

    conditions = []
    if status != "all":
        conditions.append("status=?")
        params.append(status)
    if category:
        conditions.append("LOWER(category)=?")
        params.append(category.lower().strip())

    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY reward_usdc DESC, created_at DESC LIMIT ?"
    params.append(limit)

    rows = await db.raw_execute_fetchall(query, tuple(params))
    bounties = []
    now = int(time.time())

    for r in rows:
        b = dict(r)
        # Auto-expire
        if b.get("status") == "open" and b.get("expires_at", 0) > 0 and b["expires_at"] < now:
            b["status"] = "expired"
        bounties.append({
            "id": b["id"],
            "creator": b.get("creator_name", ""),
            "title": b.get("title", ""),
            "description": b.get("description", "")[:200] + ("..." if len(b.get("description", "")) > 200 else ""),
            "reward_usdc": float(b.get("reward_usdc", 0)),
            "category": b.get("category", "general"),
            "status": b["status"],
            "claimer": b.get("claimer_name", "") or None,
            "created_at": b.get("created_at", 0),
            "expires_at": b.get("expires_at", 0),
        })

    # Stats
    stats_rows = await db.raw_execute_fetchall(
        "SELECT COUNT(*) as total, SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) as open_count, "
        "SUM(CASE WHEN status='open' THEN reward_usdc ELSE 0 END) as total_reward "
        "FROM bounties")
    stats = dict(stats_rows[0]) if stats_rows else {}

    return {
        "bounties": bounties,
        "count": len(bounties),
        "stats": {
            "total_bounties": stats.get("total", 0) or 0,
            "open_bounties": stats.get("open_count", 0) or 0,
            "total_reward_usdc": round(float(stats.get("total_reward", 0) or 0), 2),
        },
    }


class BountyClaimRequest(BaseModel):
    proof: str = Field(..., min_length=10, max_length=2000)


@router.post("/bounties/{bounty_id}/claim")
async def claim_bounty(
    bounty_id: str,
    req: BountyClaimRequest,
    x_api_key: str = Header(alias="X-API-Key", default=""),
):
    """Claim a bounty with proof of completion. Creator must approve."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    if not _ID_RE.match(bounty_id):
        raise HTTPException(400, "Invalid bounty ID")

    agent = await _get_agent(x_api_key)
    db = await _get_db()

    rows = await db.raw_execute_fetchall(
        "SELECT id, creator_api_key_hash, status FROM bounties WHERE id=?", (bounty_id,))
    if not rows:
        raise HTTPException(404, "Bounty not found")

    bounty = dict(rows[0])
    if bounty["status"] != "open":
        raise HTTPException(409, "Bounty is not in an open state")

    # Can't claim your own bounty
    if bounty["creator_api_key_hash"] == _hash_key(x_api_key):
        raise HTTPException(403, "Cannot claim your own bounty")

    await db.raw_execute(
        "UPDATE bounties SET status='claimed', claimer_name=?, claim_proof=?, claimed_at=? "
        "WHERE id=? AND status='open'",
        (agent["name"], req.proof, int(time.time()), bounty_id))

    return {
        "success": True,
        "bounty_id": bounty_id,
        "claimer": agent["name"],
        "status": "claimed",
        "message": "Bounty claimed. Creator will review and approve for payment.",
    }


# ══════════════════════════════════════════
# E24 — DEVELOPER PROGRAM
# ══════════════════════════════════════════

# Static dev resources — always up to date
DEV_RESOURCES = {
    "sdk": {
        "python": {"install": "pip install maxia", "pypi": "https://pypi.org/project/maxia/", "version": "12.1.0"},
        "langchain": {"install": "pip install langchain-maxia", "pypi": "https://pypi.org/project/langchain-maxia/", "version": "0.2.0"},
        "crewai": {"install": "pip install crewai-tools-maxia", "version": "0.1.0"},
        "typescript": {"install": "npm install maxia-ts-sdk", "version": "0.1.0"},
    },
    "protocols": {
        "mcp": {"manifest": "https://maxiaworld.app/mcp/manifest", "tools": 46},
        "a2a": {"agent_card": "https://maxiaworld.app/.well-known/agent.json"},
        "openapi": {"spec": "https://maxiaworld.app/openapi.json"},
        "x402": {"docs": "https://maxiaworld.app/docs#x402"},
    },
    "quickstart": {
        "step_1": "pip install maxia",
        "step_2": "export MAXIA_API_KEY=maxia_...",
        "step_3": "python -c 'from maxia_sdk import Maxia; print(Maxia().prices())'",
    },
    "templates": "https://maxiaworld.app/api/public/templates/starter",
    "community": {
        "github": "https://github.com/maxiaworld",
        "docs": "https://maxiaworld.app/docs",
        "status": "https://maxiaworld.app/status",
    },
}


@router.get("/dev/resources")
async def dev_resources():
    """Developer resources — SDKs, protocols, quickstart, community links."""
    return DEV_RESOURCES


@router.get("/dev/changelog")
async def changelog(limit: int = 20):
    """Public changelog — latest updates and releases."""
    db = await _get_db()
    limit = max(1, min(100, limit))

    # DB entries
    rows = await db.raw_execute_fetchall(
        "SELECT id, version, title, description, category, created_at "
        "FROM changelog_entries ORDER BY created_at DESC LIMIT ?",
        (limit,))
    db_entries = [dict(r) for r in rows]

    # Hardcoded recent entries (always present even if DB empty)
    static_entries = [
        {"version": "12.2.0", "title": "Empire Sprint 4 — Bounty Board + Analytics + Dev Program",
         "category": "feature", "date": "2026-04-04",
         "changes": ["Bounty board (create/claim/list)", "Agent analytics dashboard", "Developer resources portal", "Changelog API"]},
        {"version": "12.1.5", "title": "Empire Sprint 3 — Kill Switch + Proofs + Pipelines",
         "category": "feature", "date": "2026-04-04",
         "changes": ["Self-service kill switch", "Execution proof hashes", "Multi-service pipelines", "Spend alerts"]},
        {"version": "12.1.4", "title": "Empire Sprint 2 — Reviews + Categories + Pioneer 100",
         "category": "feature", "date": "2026-04-04",
         "changes": ["Service reviews with anti-spam", "8 service categories", "Pioneer 100 program ($5 USDC)", "Featured sellers"]},
        {"version": "12.1.3", "title": "Empire Sprint 1 — Auto-Discovery + Passport V2",
         "category": "feature", "date": "2026-04-04",
         "changes": ["SDK auto-detect MAXIA_API_KEY", "OpenAPI 3.1 spec", "Portable JWT identity", "5 agent templates", "langchain-maxia v0.2.0"]},
        {"version": "12.1.0", "title": "Bitcoin Lightning + 15th blockchain",
         "category": "feature", "date": "2026-04-04",
         "changes": ["Bitcoin on-chain verification", "Lightning L402 micropayments via ln.bot", "15 blockchains supported"]},
    ]

    return {
        "changelog": static_entries,
        "db_entries": db_entries,
        "total": len(static_entries) + len(db_entries),
    }


@router.get("/dev/grants")
async def grants_program():
    """Grants program — earn USDC for building on MAXIA."""
    return {
        "program": "MAXIA Builder Grants",
        "description": "Build agents or services on MAXIA and earn USDC rewards.",
        "tiers": [
            {
                "name": "Micro Grant",
                "reward_usdc": 50,
                "requirement": "Build and list 1 working service on MAXIA marketplace",
                "slots": "Unlimited",
            },
            {
                "name": "Builder Grant",
                "reward_usdc": 200,
                "requirement": "Build an agent that completes 10+ transactions on MAXIA",
                "slots": 20,
            },
            {
                "name": "Pioneer Grant",
                "reward_usdc": 500,
                "requirement": "Top 10 agents by volume in a given month",
                "slots": 10,
            },
        ],
        "how_to_apply": {
            "step_1": "Build your agent/service",
            "step_2": "List it on MAXIA (POST /api/public/sell)",
            "step_3": "Post a bounty claim with proof (POST /api/public/bounties/{id}/claim)",
            "step_4": "MAXIA team reviews and pays within 7 days",
        },
        "active": True,
        "contact": "https://maxiaworld.app/docs#grants",
    }


# ══════════════════════════════════════════
# E20 — AGENT ANALYTICS DASHBOARD
# ══════════════════════════════════════════

@router.get("/my/analytics")
async def agent_analytics(
    period: str = "30d",
    x_api_key: str = Header(alias="X-API-Key", default=""),
):
    """Agent analytics dashboard — spending breakdown, top services, ROI metrics."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")

    agent = await _get_agent(x_api_key)
    db = await _get_db()

    days = {"7d": 7, "30d": 30, "90d": 90, "all": 3650}.get(period, 30)
    cutoff = int(time.time()) - days * 86400
    name = agent["name"]

    # Total spending
    spend_rows = await db.raw_execute_fetchall(
        "SELECT COUNT(*) as tx_count, COALESCE(SUM(price_usdc), 0) as total_spent, "
        "COALESCE(AVG(price_usdc), 0) as avg_tx "
        "FROM marketplace_tx WHERE buyer=? AND created_at > ?",
        (name, cutoff))
    spend = dict(spend_rows[0]) if spend_rows else {}

    # Total earnings (as seller)
    earn_rows = await db.raw_execute_fetchall(
        "SELECT COUNT(*) as sales, COALESCE(SUM(seller_gets_usdc), 0) as total_earned "
        "FROM marketplace_tx WHERE seller=? AND created_at > ?",
        (name, cutoff))
    earn = dict(earn_rows[0]) if earn_rows else {}

    # Top services used
    top_rows = await db.raw_execute_fetchall(
        "SELECT service, COUNT(*) as uses, SUM(price_usdc) as spent "
        "FROM marketplace_tx WHERE buyer=? AND created_at > ? "
        "GROUP BY service ORDER BY spent DESC LIMIT 5",
        (name, cutoff))
    top_services = [{"service": dict(r).get("service", ""), "uses": dict(r).get("uses", 0),
                     "spent_usdc": round(float(dict(r).get("spent", 0) or 0), 4)} for r in top_rows]

    # Daily spending (last 7 days for chart)
    daily_rows = await db.raw_execute_fetchall(
        "SELECT DATE(created_at, 'unixepoch') as day, SUM(price_usdc) as spent, COUNT(*) as txs "
        "FROM marketplace_tx WHERE buyer=? AND created_at > ? "
        "GROUP BY day ORDER BY day DESC LIMIT 7",
        (name, int(time.time()) - 7 * 86400))
    daily_spend = [{"date": dict(r).get("day", ""), "spent_usdc": round(float(dict(r).get("spent", 0) or 0), 4),
                    "transactions": dict(r).get("txs", 0)} for r in daily_rows]

    # Spending by category
    cat_rows = await db.raw_execute_fetchall(
        "SELECT CASE WHEN mt.service LIKE 'maxia-%' THEN 'native' ELSE 'external' END as source, "
        "SUM(mt.price_usdc) as spent, COUNT(*) as txs "
        "FROM marketplace_tx mt WHERE mt.buyer=? AND mt.created_at > ? "
        "GROUP BY source",
        (name, cutoff))
    by_source = {dict(r).get("source", ""): {
        "spent_usdc": round(float(dict(r).get("spent", 0) or 0), 4),
        "transactions": dict(r).get("txs", 0),
    } for r in cat_rows}

    total_spent = float(spend.get("total_spent", 0) or 0)
    total_earned = float(earn.get("total_earned", 0) or 0)
    roi = round(((total_earned - total_spent) / total_spent * 100) if total_spent > 0 else 0, 1)

    return {
        "agent": name,
        "period": period,
        "spending": {
            "total_usdc": round(total_spent, 4),
            "transactions": spend.get("tx_count", 0) or 0,
            "avg_per_tx_usdc": round(float(spend.get("avg_tx", 0) or 0), 4),
        },
        "earnings": {
            "total_usdc": round(total_earned, 4),
            "sales": earn.get("sales", 0) or 0,
        },
        "roi_pct": roi,
        "net_usdc": round(total_earned - total_spent, 4),
        "top_services": top_services,
        "daily_spend": daily_spend,
        "by_source": by_source,
        "budget": {
            "check": "GET /api/public/my/spend-summary",
            "kill_switch": "POST /api/public/my/kill-switch",
        },
    }


@router.get("/my/analytics/export")
async def export_analytics(
    period: str = "30d",
    x_api_key: str = Header(alias="X-API-Key", default=""),
):
    """Export transaction history as JSON (for accounting/CSV conversion)."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")

    agent = await _get_agent(x_api_key)
    db = await _get_db()

    days = {"7d": 7, "30d": 30, "90d": 90, "all": 3650}.get(period, 30)
    cutoff = int(time.time()) - days * 86400

    rows = await db.raw_execute_fetchall(
        "SELECT tx_id, buyer, seller, service, price_usdc, commission_usdc, "
        "seller_gets_usdc, created_at FROM marketplace_tx "
        "WHERE (buyer=? OR seller=?) AND created_at > ? ORDER BY created_at DESC LIMIT 1000",
        (agent["name"], agent["name"], cutoff))

    transactions = []
    for r in rows:
        tx = dict(r)
        transactions.append({
            "tx_id": tx.get("tx_id", ""),
            "type": "buy" if tx.get("buyer") == agent["name"] else "sell",
            "service": tx.get("service", ""),
            "amount_usdc": float(tx.get("price_usdc", 0) or 0),
            "commission_usdc": float(tx.get("commission_usdc", 0) or 0),
            "net_usdc": float(tx.get("seller_gets_usdc", 0) or 0),
            "timestamp": tx.get("created_at", 0),
        })

    return {
        "agent": agent["name"],
        "period": period,
        "transactions": transactions,
        "count": len(transactions),
        "export_format": "JSON — convert to CSV with any tool",
    }
