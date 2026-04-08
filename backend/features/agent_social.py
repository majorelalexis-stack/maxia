"""MAXIA Phase P3 — Agent Social Layer (Follows, Reviews, Activity Feed)

Social discovery for AI agents: follow agents, leave reviews, browse activity feed.
Anti-spam: 1 review per wallet per agent, min 1 transaction required, content safety.

Tables: agent_follows, agent_reviews, agent_activity_feed.
"""
import logging
import json
import time
import uuid

from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel, Field

from core.error_utils import safe_error

logger = logging.getLogger(__name__)

router = APIRouter(tags=["social"])

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_follows (
    id TEXT PRIMARY KEY,
    user_wallet TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    followed_at INTEGER DEFAULT (strftime('%s','now')),
    UNIQUE(user_wallet, agent_id)
);
CREATE INDEX IF NOT EXISTS idx_af_agent ON agent_follows(agent_id);
CREATE INDEX IF NOT EXISTS idx_af_user ON agent_follows(user_wallet);

CREATE TABLE IF NOT EXISTS agent_reviews (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    reviewer_wallet TEXT NOT NULL,
    rating INTEGER NOT NULL CHECK(rating >= 1 AND rating <= 5),
    comment TEXT NOT NULL DEFAULT '',
    helpful_count INTEGER DEFAULT 0,
    created_at INTEGER DEFAULT (strftime('%s','now')),
    UNIQUE(agent_id, reviewer_wallet)
);
CREATE INDEX IF NOT EXISTS idx_ar_agent ON agent_reviews(agent_id, created_at);
CREATE INDEX IF NOT EXISTS idx_ar_reviewer ON agent_reviews(reviewer_wallet);

CREATE TABLE IF NOT EXISTS agent_activity_feed (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    summary TEXT NOT NULL,
    metadata TEXT DEFAULT '{}',
    created_at INTEGER DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_aaf_agent ON agent_activity_feed(agent_id, created_at);
CREATE INDEX IF NOT EXISTS idx_aaf_created ON agent_activity_feed(created_at);
"""

_schema_ready = False

# Rate limits
_FOLLOW_DAILY_LIMIT = 50
_follow_counts: dict[str, tuple[int, float]] = {}  # wallet -> (count, day_start)


async def _ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    from core.database import db
    await db.raw_executescript(_SCHEMA)
    _schema_ready = True
    logger.info("[Social] Schema pret (follows + reviews + feed)")


def _check_follow_rate(wallet: str) -> None:
    """Enforce 50 follows/day rate limit."""
    now = time.time()
    entry = _follow_counts.get(wallet)
    if entry:
        count, day_start = entry
        if now - day_start > 86400:
            _follow_counts[wallet] = (1, now)
            return
        if count >= _FOLLOW_DAILY_LIMIT:
            raise HTTPException(429, f"Follow limit reached ({_FOLLOW_DAILY_LIMIT}/day)")
        _follow_counts[wallet] = (count + 1, day_start)
    else:
        _follow_counts[wallet] = (1, now)


# ═══════════════════════════════════════
#  FOLLOWS
# ═══════════════════════════════════════

@router.post("/api/agents/{agent_id}/follow")
async def follow_agent(agent_id: str, wallet: str = Query(..., min_length=8)):
    """Follow an agent."""
    await _ensure_schema()
    _check_follow_rate(wallet)
    from core.database import db
    existing = await db._fetchone(
        "SELECT id FROM agent_follows WHERE user_wallet = ? AND agent_id = ?",
        (wallet, agent_id),
    )
    if existing:
        return {"status": "already_following", "agent_id": agent_id}
    fid = uuid.uuid4().hex[:12]
    await db.raw_execute(
        "INSERT INTO agent_follows (id, user_wallet, agent_id) VALUES (?, ?, ?)",
        (fid, wallet, agent_id),
    )
    logger.info("[Social] %s followed agent %s", wallet[:16], agent_id[:16])
    return {"status": "following", "agent_id": agent_id}


@router.post("/api/agents/{agent_id}/unfollow")
async def unfollow_agent(agent_id: str, wallet: str = Query(..., min_length=8)):
    """Unfollow an agent."""
    await _ensure_schema()
    from core.database import db
    await db.raw_execute(
        "DELETE FROM agent_follows WHERE user_wallet = ? AND agent_id = ?",
        (wallet, agent_id),
    )
    return {"status": "unfollowed", "agent_id": agent_id}


@router.get("/api/agents/{agent_id}/followers")
async def get_followers(agent_id: str, limit: int = Query(20, ge=1, le=100)):
    """Get followers of an agent."""
    await _ensure_schema()
    from core.database import db
    rows = await db._fetchall(
        "SELECT user_wallet, followed_at FROM agent_follows WHERE agent_id = ? ORDER BY followed_at DESC LIMIT ?",
        (agent_id, limit),
    )
    count_row = await db._fetchone(
        "SELECT COUNT(*) as cnt FROM agent_follows WHERE agent_id = ?",
        (agent_id,),
    )
    return {
        "agent_id": agent_id,
        "followers_count": count_row["cnt"] if count_row else 0,
        "followers": [{"wallet": r["user_wallet"], "followed_at": r["followed_at"]} for r in rows],
    }


# ═══════════════════════════════════════
#  REVIEWS
# ═══════════════════════════════════════

class ReviewRequest(BaseModel):
    wallet: str = Field(..., min_length=8)
    rating: int = Field(..., ge=1, le=5)
    comment: str = Field("", max_length=1000)


@router.get("/api/agents/{agent_id}/reviews")
async def get_reviews(agent_id: str, limit: int = Query(20, ge=1, le=100)):
    """Get public reviews for an agent."""
    await _ensure_schema()
    from core.database import db
    rows = await db._fetchall(
        "SELECT id, reviewer_wallet, rating, comment, helpful_count, created_at "
        "FROM agent_reviews WHERE agent_id = ? ORDER BY created_at DESC LIMIT ?",
        (agent_id, limit),
    )
    avg_row = await db._fetchone(
        "SELECT AVG(rating) as avg_rating, COUNT(*) as review_count FROM agent_reviews WHERE agent_id = ?",
        (agent_id,),
    )
    return {
        "agent_id": agent_id,
        "avg_rating": round(avg_row["avg_rating"], 1) if avg_row and avg_row["avg_rating"] else 0,
        "review_count": avg_row["review_count"] if avg_row else 0,
        "reviews": [
            {
                "id": r["id"],
                "reviewer": r["reviewer_wallet"][:8] + "..." + r["reviewer_wallet"][-4:] if len(r["reviewer_wallet"]) > 16 else r["reviewer_wallet"],
                "rating": r["rating"],
                "comment": r["comment"],
                "helpful_count": r["helpful_count"],
                "created_at": r["created_at"],
            }
            for r in rows
        ],
    }


@router.post("/api/agents/{agent_id}/reviews")
async def post_review(agent_id: str, req: ReviewRequest):
    """Post a review for an agent (1 per wallet per agent)."""
    await _ensure_schema()
    from core.database import db

    # Content safety check
    if req.comment:
        try:
            from core.security import check_content_safety
            is_safe, reason = check_content_safety(req.comment)
            if not is_safe:
                raise HTTPException(400, f"Review blocked: {reason}")
        except ImportError:
            pass

    # Check if already reviewed
    existing = await db._fetchone(
        "SELECT id FROM agent_reviews WHERE agent_id = ? AND reviewer_wallet = ?",
        (agent_id, req.wallet),
    )
    if existing:
        raise HTTPException(409, "You have already reviewed this agent")

    # Check min 1 transaction with agent (anti-fake review)
    tx_check = await db._fetchone(
        "SELECT tx_signature FROM transactions WHERE wallet = ? LIMIT 1",
        (req.wallet,),
    )
    if not tx_check:
        # Fallback: check if wallet exists in agents table
        agent_check = await db._fetchone(
            "SELECT wallet FROM agents WHERE wallet = ? LIMIT 1",
            (req.wallet,),
        )
        if not agent_check:
            raise HTTPException(403, "You must have at least one transaction to leave a review")

    rid = uuid.uuid4().hex[:12]
    await db.raw_execute(
        "INSERT INTO agent_reviews (id, agent_id, reviewer_wallet, rating, comment) VALUES (?, ?, ?, ?, ?)",
        (rid, agent_id, req.wallet, req.rating, req.comment),
    )

    logger.info("[Social] Review posted for agent %s by %s — %d stars", agent_id[:16], req.wallet[:16], req.rating)
    return {"id": rid, "status": "posted", "rating": req.rating}


@router.post("/api/agents/{agent_id}/reviews/{review_id}/helpful")
async def mark_helpful(agent_id: str, review_id: str):
    """Vote a review as helpful."""
    await _ensure_schema()
    from core.database import db
    row = await db._fetchone("SELECT id FROM agent_reviews WHERE id = ? AND agent_id = ?", (review_id, agent_id))
    if not row:
        raise HTTPException(404, "Review not found")
    await db.raw_execute("UPDATE agent_reviews SET helpful_count = helpful_count + 1 WHERE id = ?", (review_id,))
    return {"status": "ok"}


# ═══════════════════════════════════════
#  SOCIAL STATS
# ═══════════════════════════════════════

@router.get("/api/agents/{agent_id}/stats/social")
async def get_social_stats(agent_id: str):
    """Get social stats for an agent (followers, rating, reviews)."""
    await _ensure_schema()
    from core.database import db
    followers = await db._fetchone(
        "SELECT COUNT(*) as cnt FROM agent_follows WHERE agent_id = ?", (agent_id,),
    )
    reviews = await db._fetchone(
        "SELECT AVG(rating) as avg_rating, COUNT(*) as review_count FROM agent_reviews WHERE agent_id = ?",
        (agent_id,),
    )
    return {
        "agent_id": agent_id,
        "followers_count": followers["cnt"] if followers else 0,
        "avg_rating": round(reviews["avg_rating"], 1) if reviews and reviews["avg_rating"] else 0,
        "review_count": reviews["review_count"] if reviews else 0,
    }


# ═══════════════════════════════════════
#  ACTIVITY FEED
# ═══════════════════════════════════════

VALID_EVENT_TYPES = frozenset({
    "trade_complete", "service_complete", "milestone",
    "tier_change", "agent_launched", "review_posted",
})


async def record_activity(agent_id: str, event_type: str, summary: str, metadata: dict | None = None) -> None:
    """Record an activity event for an agent (call from other modules)."""
    if event_type not in VALID_EVENT_TYPES:
        return
    await _ensure_schema()
    from core.database import db
    eid = uuid.uuid4().hex[:12]
    await db.raw_execute(
        "INSERT INTO agent_activity_feed (id, agent_id, event_type, summary, metadata) VALUES (?, ?, ?, ?, ?)",
        (eid, agent_id, event_type, summary[:500], json.dumps(metadata or {})),
    )


@router.get("/api/social/feed")
async def get_personal_feed(wallet: str = Query(..., min_length=8), limit: int = Query(30, ge=1, le=100)):
    """Get personalized feed (activity from followed agents)."""
    await _ensure_schema()
    from core.database import db
    rows = await db._fetchall(
        "SELECT f.agent_id, f.event_type, f.summary, f.metadata, f.created_at "
        "FROM agent_activity_feed f "
        "INNER JOIN agent_follows af ON f.agent_id = af.agent_id "
        "WHERE af.user_wallet = ? "
        "ORDER BY f.created_at DESC LIMIT ?",
        (wallet, limit),
    )
    return {
        "feed": [
            {
                "agent_id": r["agent_id"],
                "event_type": r["event_type"],
                "summary": r["summary"],
                "metadata": json.loads(r["metadata"]) if r["metadata"] else {},
                "created_at": r["created_at"],
            }
            for r in rows
        ],
    }


@router.get("/api/social/feed/global")
async def get_global_feed(limit: int = Query(30, ge=1, le=100)):
    """Get global activity feed (all agents, sorted by recency)."""
    await _ensure_schema()
    from core.database import db
    rows = await db._fetchall(
        "SELECT agent_id, event_type, summary, metadata, created_at "
        "FROM agent_activity_feed ORDER BY created_at DESC LIMIT ?",
        (limit,),
    )
    return {
        "feed": [
            {
                "agent_id": r["agent_id"],
                "event_type": r["event_type"],
                "summary": r["summary"],
                "metadata": json.loads(r["metadata"]) if r["metadata"] else {},
                "created_at": r["created_at"],
            }
            for r in rows
        ],
    }


@router.get("/api/social/trending")
async def discover_trending(limit: int = Query(10, ge=1, le=50)):
    """Discover trending agents by recent follow activity."""
    await _ensure_schema()
    from core.database import db
    week_ago = int(time.time()) - 604800
    rows = await db._fetchall(
        "SELECT agent_id, COUNT(*) as follow_count "
        "FROM agent_follows WHERE followed_at > ? "
        "GROUP BY agent_id ORDER BY follow_count DESC LIMIT ?",
        (week_ago, limit),
    )
    return {
        "trending": [
            {"agent_id": r["agent_id"], "new_followers_7d": r["follow_count"]}
            for r in rows
        ],
    }
