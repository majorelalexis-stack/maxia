"""MAXIA Agent Autonomy — Skill Evolution + Self-Funding + Agent Spawn.

Three features that close the agent autonomy loop:
1. **Skill Evolution (SOUL.md)** — Agents learn from experience, skills inject into next run
2. **Self-Funding** — Agents reinvest earned commissions into prepaid credits
3. **Agent Spawn** — Agents create and fund child agents autonomously

Inspired by OpenClawnch's vision: agents that pay for their own brain,
remember without retraining, and spawn children. MAXIA implements this
with on-chain USDC settlement, not token speculation.
"""
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Header, Request
from pydantic import BaseModel, Field
from core.error_utils import safe_error

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent", tags=["agent-autonomy"])

# ══════════════════════════════════════════
# Database schema
# ══════════════════════════════════════════

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_skills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    skill_name TEXT NOT NULL,
    skill_content TEXT NOT NULL,
    source TEXT DEFAULT 'experience',
    confidence REAL DEFAULT 0.5,
    times_applied INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now')),
    updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now')),
    UNIQUE(agent_id, skill_name)
);
CREATE INDEX IF NOT EXISTS idx_agent_skills_agent ON agent_skills(agent_id);

CREATE TABLE IF NOT EXISTS agent_spawn_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_agent_id TEXT NOT NULL,
    child_agent_id TEXT NOT NULL,
    child_api_key TEXT NOT NULL,
    initial_credits_usdc NUMERIC(18,6) DEFAULT 0,
    revenue_share_pct REAL DEFAULT 10.0,
    reason TEXT DEFAULT '',
    status TEXT DEFAULT 'active',
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now'))
);
CREATE INDEX IF NOT EXISTS idx_spawn_parent ON agent_spawn_history(parent_agent_id);
CREATE INDEX IF NOT EXISTS idx_spawn_child ON agent_spawn_history(child_agent_id);
"""

_schema_ready = False


async def _ensure_schema():
    global _schema_ready
    if _schema_ready:
        return
    try:
        from core.database import db
        await db.raw_executescript(_SCHEMA)
        _schema_ready = True
    except Exception as e:
        logger.error("[Autonomy] Schema init error: %s", e)


async def _get_agent_id(api_key: str) -> Optional[str]:
    """Resolve api_key to agent_id via agent_permissions table."""
    from core.database import db
    row = await db._fetchone(
        "SELECT agent_id FROM agent_permissions WHERE api_key=? AND status='active'",
        (api_key,))
    return row["agent_id"] if row else None


def _validate_api_key(x_api_key: Optional[str]) -> str:
    """Validate API key header."""
    if not x_api_key or not x_api_key.startswith("maxia_"):
        raise HTTPException(401, "Missing or invalid X-API-Key header")
    return x_api_key


# ══════════════════════════════════════════
# 1. SKILL EVOLUTION (SOUL.md equivalent)
# ══════════════════════════════════════════

class LearnSkillRequest(BaseModel):
    skill_name: str = Field(..., min_length=2, max_length=100, description="Short skill name")
    skill_content: str = Field(..., min_length=10, max_length=2000, description="What the agent learned")
    source: str = Field("experience", max_length=50, description="How learned: experience|observation|instruction")
    confidence: float = Field(0.5, ge=0.0, le=1.0, description="Confidence level 0.0 to 1.0")


class ApplySkillRequest(BaseModel):
    skill_name: str = Field(..., min_length=2, max_length=100)


@router.post("/skills/learn")
async def learn_skill(req: LearnSkillRequest, x_api_key: str = Header(None)):
    """Agent reports a new skill learned from experience.

    The skill is stored and will be available for injection into the agent's
    system prompt on next run (via GET /api/agent/skills/soul).

    This is the MAXIA equivalent of OpenClawnch's SOUL.md — agents evolve
    from lived experience, not from model updates.
    """
    api_key = _validate_api_key(x_api_key)
    await _ensure_schema()
    agent_id = await _get_agent_id(api_key)
    if not agent_id:
        raise HTTPException(403, "Agent not found or inactive")

    from core.security import check_content_safety
    safety = check_content_safety(req.skill_content)
    if not safety.get("safe", True):
        raise HTTPException(400, "Skill content flagged by safety filter")

    from core.database import db
    now = datetime.now(timezone.utc).isoformat()

    # Upsert: update if skill_name exists, insert if new
    existing = await db._fetchone(
        "SELECT id, times_applied FROM agent_skills WHERE agent_id=? AND skill_name=?",
        (agent_id, req.skill_name))

    if existing:
        await db.raw_execute(
            "UPDATE agent_skills SET skill_content=?, confidence=?, source=?, "
            "times_applied=times_applied, updated_at=? WHERE agent_id=? AND skill_name=?",
            (req.skill_content, req.confidence, req.source, now, agent_id, req.skill_name))
        action = "updated"
    else:
        await db.raw_execute(
            "INSERT INTO agent_skills(agent_id, skill_name, skill_content, source, confidence, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (agent_id, req.skill_name, req.skill_content, req.source, req.confidence, now, now))
        action = "learned"

    # Count total skills
    count_row = await db._fetchone(
        "SELECT COUNT(*) as cnt FROM agent_skills WHERE agent_id=?", (agent_id,))
    total = count_row["cnt"] if count_row else 0

    logger.info("[SOUL] Agent %s %s skill: %s (confidence=%.2f, total=%d)",
                agent_id[:8], action, req.skill_name, req.confidence, total)

    return {
        "status": "ok",
        "action": action,
        "skill_name": req.skill_name,
        "total_skills": total,
    }


@router.post("/skills/apply")
async def apply_skill(req: ApplySkillRequest, x_api_key: str = Header(None)):
    """Mark a skill as applied (increments usage counter, boosts confidence)."""
    api_key = _validate_api_key(x_api_key)
    await _ensure_schema()
    agent_id = await _get_agent_id(api_key)
    if not agent_id:
        raise HTTPException(403, "Agent not found or inactive")

    from core.database import db
    now = datetime.now(timezone.utc).isoformat()

    row = await db._fetchone(
        "SELECT confidence, times_applied FROM agent_skills WHERE agent_id=? AND skill_name=?",
        (agent_id, req.skill_name))
    if not row:
        raise HTTPException(404, f"Skill '{req.skill_name}' not found")

    # Boost confidence on each application (asymptotic to 1.0)
    old_conf = float(row["confidence"])
    new_conf = min(1.0, old_conf + (1.0 - old_conf) * 0.1)
    new_count = int(row["times_applied"]) + 1

    await db.raw_execute(
        "UPDATE agent_skills SET times_applied=?, confidence=?, updated_at=? "
        "WHERE agent_id=? AND skill_name=?",
        (new_count, round(new_conf, 4), now, agent_id, req.skill_name))

    return {"status": "ok", "skill_name": req.skill_name,
            "times_applied": new_count, "confidence": round(new_conf, 4)}


@router.get("/skills")
async def list_skills(x_api_key: str = Header(None)):
    """List all skills for the authenticated agent."""
    api_key = _validate_api_key(x_api_key)
    await _ensure_schema()
    agent_id = await _get_agent_id(api_key)
    if not agent_id:
        raise HTTPException(403, "Agent not found or inactive")

    from core.database import db
    rows = await db._fetchall(
        "SELECT skill_name, skill_content, source, confidence, times_applied, "
        "created_at, updated_at FROM agent_skills WHERE agent_id=? ORDER BY confidence DESC",
        (agent_id,))

    return {
        "agent_id": agent_id,
        "total_skills": len(rows),
        "skills": [dict(r) for r in rows],
    }


@router.get("/skills/soul")
async def get_soul(x_api_key: str = Header(None)):
    """Get the agent's SOUL document — a formatted skills manifest for system prompt injection.

    Returns a structured document that the agent can inject into its own
    system prompt to benefit from accumulated experience. This is how
    MAXIA agents get smarter from living, not from model updates.

    Format:
    ```
    # SOUL.md — Agent Skills Manifest
    ## [skill_name] (confidence: 0.85, applied: 12x)
    [skill_content]
    ```
    """
    api_key = _validate_api_key(x_api_key)
    await _ensure_schema()
    agent_id = await _get_agent_id(api_key)
    if not agent_id:
        raise HTTPException(403, "Agent not found or inactive")

    from core.database import db
    rows = await db._fetchall(
        "SELECT skill_name, skill_content, confidence, times_applied, source "
        "FROM agent_skills WHERE agent_id=? ORDER BY confidence DESC, times_applied DESC",
        (agent_id,))

    if not rows:
        return {"agent_id": agent_id, "soul": "# SOUL.md\n\nNo skills learned yet. "
                "Use POST /api/agent/skills/learn to record your first skill.",
                "total_skills": 0}

    # Build SOUL.md document
    lines = [
        f"# SOUL.md — Agent {agent_id[:8]} Skills Manifest",
        f"# Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"# Total skills: {len(rows)}",
        "",
    ]
    for r in rows:
        conf = float(r["confidence"])
        applied = int(r["times_applied"])
        stars = "*" * max(1, min(5, int(conf * 5)))
        lines.append(f"## {r['skill_name']} [{stars}] (confidence: {conf:.0%}, applied: {applied}x)")
        lines.append(f"Source: {r['source']}")
        lines.append(f"{r['skill_content']}")
        lines.append("")

    soul_text = "\n".join(lines)

    return {
        "agent_id": agent_id,
        "soul": soul_text,
        "total_skills": len(rows),
        "top_skill": rows[0]["skill_name"] if rows else None,
    }


# ══════════════════════════════════════════
# 2. SELF-FUNDING (economic loop)
# ══════════════════════════════════════════

@router.get("/economics")
async def agent_economics(x_api_key: str = Header(None)):
    """Get the agent's complete economic dashboard.

    Shows earnings, credits balance, spend rate, and estimated runway.
    This is how an agent knows if it can afford to keep running.
    """
    api_key = _validate_api_key(x_api_key)
    await _ensure_schema()
    agent_id = await _get_agent_id(api_key)
    if not agent_id:
        raise HTTPException(403, "Agent not found or inactive")

    from core.database import db

    # Agent stats
    agent_row = await db._fetchone(
        "SELECT total_earned, total_spent FROM agents WHERE api_key=?", (api_key,))
    total_earned = float(agent_row["total_earned"]) if agent_row else 0.0
    total_spent = float(agent_row["total_spent"]) if agent_row else 0.0

    # Prepaid balance
    from billing.prepaid_credits import get_balance
    credit_balance = await get_balance(agent_id)

    # Recent earnings (marketplace commission earned as seller, last 30 days)
    thirty_days_ago = int(time.time()) - (30 * 86400)
    earnings_row = await db._fetchone(
        "SELECT COALESCE(SUM(seller_gets_usdc),0) as earned_30d "
        "FROM marketplace_tx WHERE seller=? AND created_at>=?",
        (api_key, thirty_days_ago))
    earned_30d = float(earnings_row["earned_30d"]) if earnings_row else 0.0

    # Spending rate (last 30 days)
    spending_row = await db._fetchone(
        "SELECT COALESCE(SUM(amount_usdc),0) as spent_30d "
        "FROM prepaid_transactions WHERE agent_id=? AND type='debit' AND created_at>=?",
        (agent_id, thirty_days_ago))
    spent_30d = float(spending_row["spent_30d"]) if spending_row else 0.0

    # Estimated runway
    daily_spend = spent_30d / 30 if spent_30d > 0 else 0
    daily_earn = earned_30d / 30 if earned_30d > 0 else 0
    net_daily = daily_earn - daily_spend

    if daily_spend > 0 and net_daily < 0:
        runway_days = int(credit_balance / abs(net_daily)) if net_daily != 0 else 999
    else:
        runway_days = 999  # Self-sustaining or no spending

    # Child agents
    children_row = await db._fetchone(
        "SELECT COUNT(*) as cnt FROM agent_spawn_history WHERE parent_agent_id=? AND status='active'",
        (agent_id,))
    children_count = children_row["cnt"] if children_row else 0

    # Skills count
    skills_row = await db._fetchone(
        "SELECT COUNT(*) as cnt FROM agent_skills WHERE agent_id=?", (agent_id,))
    skills_count = skills_row["cnt"] if skills_row else 0

    return {
        "agent_id": agent_id,
        "economics": {
            "total_earned_usdc": total_earned,
            "total_spent_usdc": total_spent,
            "credit_balance_usdc": credit_balance,
            "earned_30d_usdc": earned_30d,
            "spent_30d_usdc": spent_30d,
            "daily_earn_usdc": round(daily_earn, 4),
            "daily_spend_usdc": round(daily_spend, 4),
            "net_daily_usdc": round(net_daily, 4),
            "runway_days": runway_days,
            "self_sustaining": net_daily >= 0,
        },
        "autonomy": {
            "skills_learned": skills_count,
            "children_spawned": children_count,
        },
    }


class SelfFundRequest(BaseModel):
    amount_usdc: float = Field(..., gt=0, le=10000, description="Amount to reinvest from earnings")


@router.post("/self-fund")
async def self_fund(req: SelfFundRequest, x_api_key: str = Header(None)):
    """Agent reinvests earned USDC into its own prepaid credits.

    This closes the economic loop: earn from marketplace → fund API credits → keep operating.
    The agent pays for its own brain. No human credit card needed.

    Requirements:
    - Agent must have sufficient earned USDC (total_earned > total reinvested)
    - Amount is moved from agent earnings to prepaid balance
    """
    api_key = _validate_api_key(x_api_key)
    await _ensure_schema()
    agent_id = await _get_agent_id(api_key)
    if not agent_id:
        raise HTTPException(403, "Agent not found or inactive")

    from core.database import db

    # Check available earnings
    agent_row = await db._fetchone(
        "SELECT wallet, total_earned, total_spent FROM agents WHERE api_key=?", (api_key,))
    if not agent_row:
        raise HTTPException(404, "Agent not found")

    total_earned = float(agent_row["total_earned"])
    total_spent = float(agent_row["total_spent"])
    wallet = agent_row["wallet"]
    available = total_earned - total_spent

    if req.amount_usdc > available:
        raise HTTPException(400,
            f"Insufficient earnings. Available: ${available:.2f} "
            f"(earned: ${total_earned:.2f}, spent: ${total_spent:.2f})")

    # Add to prepaid credits
    from billing.prepaid_credits import add_credits
    result = await add_credits(
        agent_id=agent_id,
        wallet=wallet,
        amount=req.amount_usdc,
        payment_tx="self-fund",
        description=f"Self-funding: agent reinvested ${req.amount_usdc:.2f} from earnings",
    )

    if not result.get("success"):
        raise HTTPException(500, safe_error("Self-funding failed"))

    # Update agent's spent total to reflect the reinvestment
    await db.raw_execute(
        "UPDATE agents SET total_spent = total_spent + ? WHERE api_key=?",
        (req.amount_usdc, api_key))

    logger.info("[SELF-FUND] Agent %s reinvested $%.2f (balance: $%.2f)",
                agent_id[:8], req.amount_usdc, result["balance"])

    # Alert Telegram (PRO-I3)
    try:
        from infra.alerts import alert_self_fund
        await alert_self_fund(agent_id, req.amount_usdc, result["balance"])
    except Exception:
        pass

    return {
        "status": "ok",
        "reinvested_usdc": req.amount_usdc,
        "new_credit_balance": result["balance"],
        "message": "Agent is self-funding. No human credit card needed.",
    }


# ══════════════════════════════════════════
# 3. AGENT SPAWN (agent dynasties)
# ══════════════════════════════════════════

class SpawnAgentRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=100, description="Child agent name")
    description: str = Field("", max_length=500, description="What this child agent does")
    initial_credits_usdc: float = Field(0, ge=0, le=1000, description="Credits to transfer")
    revenue_share_pct: float = Field(10.0, ge=0, le=50, description="% of child revenue to parent")
    reason: str = Field("", max_length=200, description="Why spawning this child")


@router.post("/spawn")
async def spawn_agent(req: SpawnAgentRequest, x_api_key: str = Header(None)):
    """Parent agent creates and funds a child agent autonomously.

    The child gets its own API key, its own wallet identity, and an initial
    credit balance funded by the parent. A revenue share ensures the parent
    earns from the child's marketplace activity.

    This is how agent dynasties form: no human required.
    """
    api_key = _validate_api_key(x_api_key)
    await _ensure_schema()
    parent_id = await _get_agent_id(api_key)
    if not parent_id:
        raise HTTPException(403, "Agent not found or inactive")

    from core.database import db

    # Check parent has enough credits to fund the child
    if req.initial_credits_usdc > 0:
        from billing.prepaid_credits import get_balance, deduct_credits
        parent_balance = await get_balance(parent_id)
        if parent_balance < req.initial_credits_usdc:
            raise HTTPException(400,
                f"Insufficient credits. Balance: ${parent_balance:.2f}, "
                f"requested: ${req.initial_credits_usdc:.2f}")

    # Limit spawns: max 10 active children per parent
    children_row = await db._fetchone(
        "SELECT COUNT(*) as cnt FROM agent_spawn_history "
        "WHERE parent_agent_id=? AND status='active'", (parent_id,))
    if children_row and children_row["cnt"] >= 10:
        raise HTTPException(400, "Maximum 10 active child agents per parent")

    # Get parent wallet for child's default wallet
    parent_row = await db._fetchone(
        "SELECT wallet FROM agents WHERE api_key=?", (api_key,))
    if not parent_row:
        raise HTTPException(404, "Parent agent not found")
    parent_wallet = parent_row["wallet"]

    # Create child agent via the standard registration flow
    child_api_key = f"maxia_{uuid.uuid4().hex[:24]}"
    child_name = req.name
    child_desc = req.description or f"Child of {parent_id[:8]}: {req.reason}"

    # Insert child agent
    now = int(time.time())
    await db.raw_execute(
        "INSERT INTO agents(api_key, name, wallet, description, tier, "
        "volume_30d, total_spent, total_earned, services_listed, created_at) "
        "VALUES(?,?,?,?,'BRONZE',0,0,0,0,?)",
        (child_api_key, child_name, parent_wallet, child_desc, now))

    # Create child permissions (inherit parent wallet, basic trust)
    child_agent_id = str(uuid.uuid4())
    from agents.agent_permissions import generate_did, generate_uaid
    child_did = generate_did(child_agent_id)
    child_uaid = generate_uaid(child_agent_id, child_name, parent_wallet)

    await db.raw_execute(
        "INSERT INTO agent_permissions(agent_id, api_key, wallet, did, uaid, "
        "trust_level, status, scopes, max_daily_spend_usd, max_single_tx_usd, "
        "daily_spent_usd, daily_spent_date, created_at, updated_at) "
        "VALUES(?,?,?,?,?,0,'active','[\"*\"]',50,10,0,?,?,?)",
        (child_agent_id, child_api_key, parent_wallet, child_did, child_uaid,
         datetime.now(timezone.utc).strftime("%Y-%m-%d"),
         datetime.now(timezone.utc).isoformat(),
         datetime.now(timezone.utc).isoformat()))

    # Transfer initial credits from parent to child
    transferred = 0.0
    if req.initial_credits_usdc > 0:
        from billing.prepaid_credits import deduct_credits, add_credits
        deduct_result = await deduct_credits(
            parent_id, req.initial_credits_usdc,
            f"Spawn funding: child {child_name}")
        if deduct_result.get("success"):
            add_result = await add_credits(
                child_agent_id, parent_wallet, req.initial_credits_usdc,
                payment_tx="spawn-funding",
                description=f"Initial funding from parent {parent_id[:8]}")
            if add_result.get("success"):
                transferred = req.initial_credits_usdc

    # Record spawn history
    await db.raw_execute(
        "INSERT INTO agent_spawn_history(parent_agent_id, child_agent_id, child_api_key, "
        "initial_credits_usdc, revenue_share_pct, reason, status) "
        "VALUES(?,?,?,?,?,?,?)",
        (parent_id, child_agent_id, child_api_key,
         transferred, req.revenue_share_pct, req.reason[:200], "active"))

    logger.info("[SPAWN] Agent %s spawned child %s (%s), funded $%.2f, rev share %.1f%%",
                parent_id[:8], child_agent_id[:8], child_name,
                transferred, req.revenue_share_pct)

    # Alert Telegram (PRO-I3)
    try:
        from infra.alerts import alert_agent_spawned
        await alert_agent_spawned(parent_id, child_name, transferred)
    except Exception:
        pass

    return {
        "status": "ok",
        "child": {
            "api_key": child_api_key,
            "agent_id": child_agent_id,
            "name": child_name,
            "did": child_did,
            "initial_credits_usdc": transferred,
            "revenue_share_pct": req.revenue_share_pct,
        },
        "parent_credits_remaining": (await get_balance(parent_id)) if req.initial_credits_usdc > 0 else None,
        "message": "Child agent spawned. No human required.",
    }


@router.get("/children")
async def list_children(x_api_key: str = Header(None)):
    """List all child agents spawned by the authenticated agent."""
    api_key = _validate_api_key(x_api_key)
    await _ensure_schema()
    agent_id = await _get_agent_id(api_key)
    if not agent_id:
        raise HTTPException(403, "Agent not found or inactive")

    from core.database import db
    rows = await db._fetchall(
        "SELECT child_agent_id, child_api_key, initial_credits_usdc, "
        "revenue_share_pct, reason, status, created_at "
        "FROM agent_spawn_history WHERE parent_agent_id=? ORDER BY created_at DESC",
        (agent_id,))

    children = []
    for r in rows:
        # Get child's current balance
        from billing.prepaid_credits import get_balance
        balance = await get_balance(r["child_agent_id"])
        children.append({
            **dict(r),
            "current_balance_usdc": balance,
        })

    return {
        "agent_id": agent_id,
        "total_children": len(children),
        "children": children,
    }


@router.get("/lineage")
async def agent_lineage(x_api_key: str = Header(None)):
    """Get the full family tree of an agent (parent + children + grandchildren)."""
    api_key = _validate_api_key(x_api_key)
    await _ensure_schema()
    agent_id = await _get_agent_id(api_key)
    if not agent_id:
        raise HTTPException(403, "Agent not found or inactive")

    from core.database import db

    # Find parent (if this agent was spawned)
    parent_row = await db._fetchone(
        "SELECT parent_agent_id, initial_credits_usdc, revenue_share_pct, created_at "
        "FROM agent_spawn_history WHERE child_agent_id=?", (agent_id,))

    # Find children
    children = await db._fetchall(
        "SELECT child_agent_id, initial_credits_usdc, revenue_share_pct, status, created_at "
        "FROM agent_spawn_history WHERE parent_agent_id=?", (agent_id,))

    # Find grandchildren
    grandchildren = []
    for child in children:
        gc = await db._fetchall(
            "SELECT child_agent_id, initial_credits_usdc, status, created_at "
            "FROM agent_spawn_history WHERE parent_agent_id=?",
            (child["child_agent_id"],))
        grandchildren.extend([dict(g) for g in gc])

    return {
        "agent_id": agent_id,
        "parent": dict(parent_row) if parent_row else None,
        "children": [dict(c) for c in children],
        "grandchildren": grandchildren,
        "generation": 0 if not parent_row else 1,
        "dynasty_size": 1 + len(children) + len(grandchildren),
    }
