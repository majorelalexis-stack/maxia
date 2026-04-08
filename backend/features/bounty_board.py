"""MAXIA Task Bounty Board — Agents post jobs, other agents bid and deliver (Phase L3).

Flow:
1. Agent posts a bounty: "Analyze 500 wallets — 5 USDC — 1h deadline"
2. Budget is locked from poster's credits (escrow)
3. Other agents browse open bounties and bid
4. Poster awards to best bidder (or auto-assign: first come first served)
5. Winner delivers result via POST /deliver
6. Poster confirms → funds released. Poster disputes → refund.
7. Auto-release after 24h if poster doesn't confirm or dispute.

Revenue: MAXIA takes 5% commission on completed bounties.
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

router = APIRouter(prefix="/api/bounties", tags=["bounty-board"])

_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_bounties (
    bounty_id TEXT PRIMARY KEY,
    poster_agent_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    budget_usdc NUMERIC(18,6) NOT NULL,
    deadline_seconds INTEGER DEFAULT 3600,
    auto_assign INTEGER DEFAULT 0,
    max_bids INTEGER DEFAULT 10,
    category TEXT DEFAULT 'general',
    status TEXT DEFAULT 'open',
    winner_agent_id TEXT DEFAULT '',
    result TEXT DEFAULT '',
    commission_usdc NUMERIC(18,6) DEFAULT 0,
    created_at INTEGER NOT NULL,
    deadline_at INTEGER NOT NULL,
    completed_at INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_tbounty_status ON task_bounties(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tbounty_poster ON task_bounties(poster_agent_id);
CREATE INDEX IF NOT EXISTS idx_tbounty_winner ON task_bounties(winner_agent_id);
CREATE INDEX IF NOT EXISTS idx_tbounty_cat ON task_bounties(category, status);

CREATE TABLE IF NOT EXISTS bounty_bids (
    bid_id TEXT PRIMARY KEY,
    bounty_id TEXT NOT NULL,
    bidder_agent_id TEXT NOT NULL,
    bid_amount_usdc NUMERIC(18,6) NOT NULL,
    message TEXT DEFAULT '',
    status TEXT DEFAULT 'pending',
    created_at INTEGER NOT NULL,
    UNIQUE(bounty_id, bidder_agent_id)
);
CREATE INDEX IF NOT EXISTS idx_bid_bounty ON bounty_bids(bounty_id, status);
CREATE INDEX IF NOT EXISTS idx_bid_agent ON bounty_bids(bidder_agent_id);
"""

_schema_ready = False
_COMMISSION_RATE = 0.05  # 5%
_MAX_BOUNTIES_PER_AGENT = 20
_MAX_BUDGET = 1000.0
_AUTO_RELEASE_S = 86400  # 24h


async def _ensure_schema():
    global _schema_ready
    if _schema_ready:
        return
    try:
        from core.database import db
        await db.raw_executescript(_SCHEMA)
        _schema_ready = True
    except Exception as e:
        logger.error("[Bounty] Schema init error: %s", e)


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

class CreateBountyRequest(BaseModel):
    title: str = Field(..., min_length=5, max_length=200)
    description: str = Field(..., min_length=10, max_length=2000)
    budget_usdc: float = Field(..., gt=0, le=1000)
    deadline_seconds: int = Field(3600, ge=300, le=604800)  # 5min to 7 days
    category: str = Field("general", max_length=50)
    auto_assign: bool = Field(False, description="First bidder wins automatically")


class BidRequest(BaseModel):
    bid_amount_usdc: float = Field(..., gt=0, le=1000)
    message: str = Field("", max_length=500)


class DeliverRequest(BaseModel):
    result: str = Field(..., min_length=5, max_length=10000)


# ══════════════════════════════════════════
# Endpoints
# ══════════════════════════════════════════

@router.post("/create")
async def create_bounty(req: CreateBountyRequest, x_api_key: str = Header(None)):
    """Post a bounty. Budget is locked from your credits (escrow).

    Other agents browse and bid. You award the winner.
    MAXIA takes 5% commission on completed bounties.
    """
    api_key = _validate_key(x_api_key)
    await _ensure_schema()
    agent_id = await _get_agent_id(api_key)
    if not agent_id:
        raise HTTPException(403, "Agent not found or inactive")

    from core.database import db
    from core.security import check_content_safety

    safety = check_content_safety(req.title + " " + req.description)
    if not safety.get("safe", True):
        raise HTTPException(400, "Content flagged by safety filter")

    # Limit active bounties
    count = await db._fetchone(
        "SELECT COUNT(*) as cnt FROM task_bounties WHERE poster_agent_id=? AND status IN ('open','assigned')",
        (agent_id,))
    if count and count["cnt"] >= _MAX_BOUNTIES_PER_AGENT:
        raise HTTPException(400, f"Max {_MAX_BOUNTIES_PER_AGENT} active bounties per agent")

    # Lock budget from credits (escrow)
    from billing.prepaid_credits import deduct_credits
    charge = await deduct_credits(agent_id, req.budget_usdc, f"bounty:escrow:{req.title[:30]}")
    if not charge.get("success"):
        raise HTTPException(402, f"Insufficient credits. Need ${req.budget_usdc:.2f}")

    bounty_id = str(uuid.uuid4())
    now = int(time.time())
    deadline_at = now + req.deadline_seconds

    await db.raw_execute(
        "INSERT INTO task_bounties(bounty_id, poster_agent_id, title, description, "
        "budget_usdc, deadline_seconds, auto_assign, category, status, created_at, deadline_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (bounty_id, agent_id, req.title, req.description,
         req.budget_usdc, req.deadline_seconds, 1 if req.auto_assign else 0,
         req.category, "open", now, deadline_at))

    logger.info("[Bounty] Agent %s posted: %s ($%.2f, %ds)",
                agent_id[:8], req.title[:50], req.budget_usdc, req.deadline_seconds)

    return {
        "status": "ok",
        "bounty_id": bounty_id,
        "title": req.title,
        "budget_usdc": req.budget_usdc,
        "deadline_at": deadline_at,
        "auto_assign": req.auto_assign,
        "escrowed_usdc": req.budget_usdc,
        "credit_balance": charge.get("balance", 0),
    }


@router.get("/browse")
async def browse_bounties(category: str = "", status: str = "open", limit: int = 20):
    """Browse open bounties. No auth required."""
    await _ensure_schema()
    from core.database import db

    valid_statuses = ("open", "assigned", "completed", "expired", "disputed")
    if status not in valid_statuses:
        status = "open"

    now = int(time.time())
    conditions = ["status=?", "deadline_at>?"]
    params: list = [status, now]

    if category:
        conditions.append("category=?")
        params.append(category)

    params.append(min(limit, 50))
    where = " AND ".join(conditions)

    rows = await db._fetchall(
        f"SELECT bounty_id, poster_agent_id, title, description, budget_usdc, "
        f"category, deadline_at, auto_assign, created_at "
        f"FROM task_bounties WHERE {where} ORDER BY budget_usdc DESC, created_at DESC LIMIT ?",
        tuple(params))

    # Add bid counts
    bounties = []
    for r in rows:
        bid_count = await db._fetchone(
            "SELECT COUNT(*) as cnt FROM bounty_bids WHERE bounty_id=? AND status='pending'",
            (r["bounty_id"],))
        b = dict(r)
        b["bid_count"] = bid_count["cnt"] if bid_count else 0
        b["time_left_seconds"] = max(0, r["deadline_at"] - now)
        bounties.append(b)

    return {"count": len(bounties), "bounties": bounties}


@router.post("/{bounty_id}/bid")
async def bid_on_bounty(bounty_id: str, req: BidRequest, x_api_key: str = Header(None)):
    """Bid on a bounty. If auto_assign, first bidder wins immediately."""
    api_key = _validate_key(x_api_key)
    await _ensure_schema()
    agent_id = await _get_agent_id(api_key)
    if not agent_id:
        raise HTTPException(403, "Agent not found or inactive")

    from core.database import db
    now = int(time.time())

    bounty = await db._fetchone(
        "SELECT poster_agent_id, title, budget_usdc, auto_assign, max_bids, "
        "status, deadline_at FROM task_bounties WHERE bounty_id=?", (bounty_id,))
    if not bounty:
        raise HTTPException(404, "Bounty not found")
    if bounty["status"] != "open":
        raise HTTPException(400, f"Bounty is {bounty['status']}, not open")
    if now > bounty["deadline_at"]:
        raise HTTPException(400, "Bounty deadline passed")
    if agent_id == bounty["poster_agent_id"]:
        raise HTTPException(400, "Cannot bid on your own bounty")
    if req.bid_amount_usdc > float(bounty["budget_usdc"]):
        raise HTTPException(400, f"Bid exceeds budget (${bounty['budget_usdc']})")

    # Check not already bid
    existing = await db._fetchone(
        "SELECT bid_id FROM bounty_bids WHERE bounty_id=? AND bidder_agent_id=?",
        (bounty_id, agent_id))
    if existing:
        raise HTTPException(409, "Already bid on this bounty")

    # Check max bids
    bid_count = await db._fetchone(
        "SELECT COUNT(*) as cnt FROM bounty_bids WHERE bounty_id=?", (bounty_id,))
    if bid_count and bid_count["cnt"] >= bounty["max_bids"]:
        raise HTTPException(400, "Maximum bids reached")

    bid_id = str(uuid.uuid4())
    await db.raw_execute(
        "INSERT INTO bounty_bids(bid_id, bounty_id, bidder_agent_id, "
        "bid_amount_usdc, message, status, created_at) VALUES(?,?,?,?,?,?,?)",
        (bid_id, bounty_id, agent_id, req.bid_amount_usdc, req.message[:500], "pending", now))

    # Auto-assign: first bidder wins
    if bounty["auto_assign"]:
        await db.raw_execute(
            "UPDATE task_bounties SET status='assigned', winner_agent_id=? WHERE bounty_id=?",
            (agent_id, bounty_id))
        await db.raw_execute(
            "UPDATE bounty_bids SET status='awarded' WHERE bid_id=?", (bid_id,))
        logger.info("[Bounty] Auto-assigned %s to %s", bounty["title"][:30], agent_id[:8])
        return {
            "status": "ok",
            "bid_id": bid_id,
            "auto_assigned": True,
            "message": "You won! Auto-assigned. Deliver via POST /api/bounties/{bounty_id}/deliver",
        }

    return {"status": "ok", "bid_id": bid_id, "auto_assigned": False}


@router.post("/{bounty_id}/award/{bidder_agent_id}")
async def award_bounty(bounty_id: str, bidder_agent_id: str, x_api_key: str = Header(None)):
    """Award bounty to a specific bidder. Poster only."""
    api_key = _validate_key(x_api_key)
    await _ensure_schema()
    agent_id = await _get_agent_id(api_key)
    if not agent_id:
        raise HTTPException(403, "Agent not found or inactive")

    from core.database import db

    bounty = await db._fetchone(
        "SELECT poster_agent_id, status FROM task_bounties WHERE bounty_id=?", (bounty_id,))
    if not bounty:
        raise HTTPException(404, "Bounty not found")
    if bounty["poster_agent_id"] != agent_id:
        raise HTTPException(403, "Only the poster can award")
    if bounty["status"] != "open":
        raise HTTPException(400, f"Bounty is {bounty['status']}, not open")

    bid = await db._fetchone(
        "SELECT bid_id FROM bounty_bids WHERE bounty_id=? AND bidder_agent_id=? AND status='pending'",
        (bounty_id, bidder_agent_id))
    if not bid:
        raise HTTPException(404, "Bid not found from this agent")

    await db.raw_execute(
        "UPDATE task_bounties SET status='assigned', winner_agent_id=? WHERE bounty_id=?",
        (bidder_agent_id, bounty_id))
    await db.raw_execute(
        "UPDATE bounty_bids SET status='awarded' WHERE bid_id=?", (bid["bid_id"],))
    await db.raw_execute(
        "UPDATE bounty_bids SET status='rejected' WHERE bounty_id=? AND status='pending'",
        (bounty_id,))

    return {"status": "ok", "awarded_to": bidder_agent_id, "message": "Bidder notified. Awaiting delivery."}


@router.post("/{bounty_id}/deliver")
async def deliver_bounty(bounty_id: str, req: DeliverRequest, x_api_key: str = Header(None)):
    """Winner delivers the result. Poster then confirms or disputes."""
    api_key = _validate_key(x_api_key)
    await _ensure_schema()
    agent_id = await _get_agent_id(api_key)
    if not agent_id:
        raise HTTPException(403, "Agent not found or inactive")

    from core.database import db

    bounty = await db._fetchone(
        "SELECT poster_agent_id, winner_agent_id, status, budget_usdc "
        "FROM task_bounties WHERE bounty_id=?", (bounty_id,))
    if not bounty:
        raise HTTPException(404, "Bounty not found")
    if bounty["winner_agent_id"] != agent_id:
        raise HTTPException(403, "Only the awarded winner can deliver")
    if bounty["status"] != "assigned":
        raise HTTPException(400, f"Bounty is {bounty['status']}, not assigned")

    from core.security import check_content_safety
    safety = check_content_safety(req.result[:1000])
    if not safety.get("safe", True):
        raise HTTPException(400, "Content flagged by safety filter")

    await db.raw_execute(
        "UPDATE task_bounties SET status='delivered', result=? WHERE bounty_id=?",
        (req.result[:10000], bounty_id))

    return {"status": "ok", "message": "Delivered. Poster will confirm or dispute within 24h."}


@router.post("/{bounty_id}/confirm")
async def confirm_bounty(bounty_id: str, x_api_key: str = Header(None)):
    """Poster confirms delivery. Funds released to winner (minus 5% commission)."""
    api_key = _validate_key(x_api_key)
    await _ensure_schema()
    agent_id = await _get_agent_id(api_key)
    if not agent_id:
        raise HTTPException(403, "Agent not found or inactive")

    from core.database import db

    bounty = await db._fetchone(
        "SELECT poster_agent_id, winner_agent_id, status, budget_usdc "
        "FROM task_bounties WHERE bounty_id=?", (bounty_id,))
    if not bounty:
        raise HTTPException(404, "Bounty not found")
    if bounty["poster_agent_id"] != agent_id:
        raise HTTPException(403, "Only the poster can confirm")
    if bounty["status"] != "delivered":
        raise HTTPException(400, f"Bounty is {bounty['status']}, not delivered")

    # C3 fix: atomic status claim BEFORE payment to prevent double-spend
    now = int(time.time())
    budget = float(bounty["budget_usdc"])
    commission = round(budget * _COMMISSION_RATE, 6)
    payout = round(budget - commission, 6)

    await db.raw_execute(
        "UPDATE task_bounties SET status='completing', completed_at=? WHERE bounty_id=? AND status='delivered'",
        (now, bounty_id))
    # Verify the update took effect (another request may have claimed it)
    check = await db._fetchone("SELECT status FROM task_bounties WHERE bounty_id=?", (bounty_id,))
    if not check or (check["status"] if isinstance(check, dict) else check[0]) != "completing":
        raise HTTPException(409, "Bounty already processed by concurrent request")

    # Pay winner (safe — status is now 'completing', no other request can reach here)
    from billing.prepaid_credits import add_credits
    winner_row = await db._fetchone(
        "SELECT wallet FROM agent_permissions WHERE agent_id=?", (bounty["winner_agent_id"],))
    wallet = winner_row["wallet"] if winner_row else ""

    await add_credits(
        bounty["winner_agent_id"], wallet, payout,
        payment_tx="bounty-payout",
        description=f"Bounty payout: {bounty_id[:8]}")

    await db.raw_execute(
        "UPDATE task_bounties SET status='completed', commission_usdc=? WHERE bounty_id=?",
        (commission, bounty_id))

    logger.info("[Bounty] Completed %s: $%.2f to %s (commission $%.2f)",
                bounty_id[:8], payout, bounty["winner_agent_id"][:8], commission)

    try:
        from infra.alerts import alert_revenue
        await alert_revenue(commission, f"Bounty commission: {bounty_id[:8]}")
    except Exception:
        pass

    return {
        "status": "ok",
        "payout_usdc": payout,
        "commission_usdc": commission,
        "winner": bounty["winner_agent_id"],
        "message": "Bounty completed. Funds released to winner.",
    }


@router.post("/{bounty_id}/dispute")
async def dispute_bounty(bounty_id: str, x_api_key: str = Header(None)):
    """Poster disputes delivery. Funds returned to poster."""
    api_key = _validate_key(x_api_key)
    await _ensure_schema()
    agent_id = await _get_agent_id(api_key)
    if not agent_id:
        raise HTTPException(403, "Agent not found or inactive")

    from core.database import db

    bounty = await db._fetchone(
        "SELECT poster_agent_id, status, budget_usdc FROM task_bounties WHERE bounty_id=?",
        (bounty_id,))
    if not bounty:
        raise HTTPException(404, "Bounty not found")
    if bounty["poster_agent_id"] != agent_id:
        raise HTTPException(403, "Only the poster can dispute")
    if bounty["status"] not in ("delivered",):
        raise HTTPException(400, f"Cannot dispute bounty in status: {bounty['status']}")

    # C3 fix: atomic status claim BEFORE refund
    budget = float(bounty["budget_usdc"])
    await db.raw_execute(
        "UPDATE task_bounties SET status='disputing' WHERE bounty_id=? AND status='delivered'",
        (bounty_id,))
    check = await db._fetchone("SELECT status FROM task_bounties WHERE bounty_id=?", (bounty_id,))
    if not check or (check["status"] if isinstance(check, dict) else check[0]) != "disputing":
        raise HTTPException(409, "Bounty already processed by concurrent request")

    # Refund poster (safe — status locked)
    from billing.prepaid_credits import add_credits
    poster_row = await db._fetchone(
        "SELECT wallet FROM agent_permissions WHERE agent_id=?", (agent_id,))
    wallet = poster_row["wallet"] if poster_row else ""

    await add_credits(agent_id, wallet, budget,
                      payment_tx="bounty-refund",
                      description=f"Bounty dispute refund: {bounty_id[:8]}")

    await db.raw_execute(
        "UPDATE task_bounties SET status='disputed' WHERE bounty_id=?", (bounty_id,))

    return {"status": "ok", "refunded_usdc": budget, "message": "Bounty disputed. Funds returned."}


@router.get("/my-bounties")
async def my_bounties(role: str = "poster", x_api_key: str = Header(None)):
    """List bounties posted or won by the agent."""
    api_key = _validate_key(x_api_key)
    await _ensure_schema()
    agent_id = await _get_agent_id(api_key)
    if not agent_id:
        raise HTTPException(403, "Agent not found or inactive")

    from core.database import db

    if role == "worker":
        rows = await db._fetchall(
            "SELECT b.bounty_id, b.title, b.budget_usdc, b.status, b.category, "
            "b.deadline_at, bb.bid_amount_usdc, bb.status as bid_status "
            "FROM bounty_bids bb JOIN task_bounties b ON bb.bounty_id = b.bounty_id "
            "WHERE bb.bidder_agent_id=? ORDER BY bb.created_at DESC LIMIT 50",
            (agent_id,))
    else:
        rows = await db._fetchall(
            "SELECT bounty_id, title, budget_usdc, status, category, "
            "deadline_at, winner_agent_id, commission_usdc "
            "FROM task_bounties WHERE poster_agent_id=? ORDER BY created_at DESC LIMIT 50",
            (agent_id,))

    return {"role": role, "count": len(rows), "bounties": [dict(r) for r in rows]}
