"""MAXIA Empire V2 Sprint 2 — Supply: Reviews, Categories, Pioneer Program.

E21: Reviews persistence, review list, spam detection, recency-weighted scoring, verified badge
E19: Service categories, featured sellers, seller verification
E9:  Pioneer 100 program — first 100 agents get bonus credits + badge
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

router = APIRouter(prefix="/api/public", tags=["empire-sprint2"])

# Input validation
_ID_RE = re.compile(r'^[a-zA-Z0-9_\-]{1,128}$')

# ══════════════════════════════════════════
# DB SCHEMA — Reviews + Pioneer tables
# ══════════════════════════════════════════

_SPRINT2_SCHEMA = """
CREATE TABLE IF NOT EXISTS service_reviews (
    id TEXT PRIMARY KEY,
    service_id TEXT NOT NULL,
    reviewer_api_key TEXT NOT NULL,
    reviewer_name TEXT DEFAULT '',
    rating INTEGER NOT NULL CHECK(rating >= 1 AND rating <= 5),
    comment TEXT DEFAULT '',
    verified_purchase INTEGER DEFAULT 0,
    created_at INTEGER DEFAULT (strftime('%s','now')),
    UNIQUE(service_id, reviewer_api_key)
);
CREATE INDEX IF NOT EXISTS idx_reviews_service ON service_reviews(service_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_reviews_reviewer ON service_reviews(reviewer_api_key);

CREATE TABLE IF NOT EXISTS pioneer_enrollments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    api_key TEXT UNIQUE NOT NULL,
    agent_name TEXT NOT NULL,
    wallet TEXT NOT NULL,
    bonus_credits_usdc NUMERIC(18,6) DEFAULT 0,
    enrolled_at INTEGER DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_pioneer_enrolled ON pioneer_enrollments(enrolled_at);
"""

_schema_initialized = False


async def _ensure_schema():
    """Create Sprint 2 tables if they don't exist."""
    global _schema_initialized
    if _schema_initialized:
        return
    try:
        from core.database import db
        await db.raw_executescript(_SPRINT2_SCHEMA)
        _schema_initialized = True
        logger.info("[Sprint2] Reviews + Pioneer tables ready")
    except Exception as e:
        logger.error("[Sprint2] Schema init error: %s", e)


async def _get_db():
    from core.database import db
    await _ensure_schema()
    return db


async def _get_agent(api_key: str) -> dict:
    db = await _get_db()
    rows = await db.raw_execute_fetchall(
        "SELECT api_key, name, wallet FROM agents WHERE api_key=?", (api_key,))
    if not rows:
        raise HTTPException(401, "Invalid API key")
    return dict(rows[0])


# ══════════════════════════════════════════
# E21 — REVIEWS: Persistence + List + Spam Detection
# ══════════════════════════════════════════

REVIEW_MIN_CHARS = 20
REVIEW_MAX_CHARS = 1000


class ReviewRequest(BaseModel):
    """Submit a review for a purchased service."""
    service_id: str = Field(..., min_length=1, max_length=128)
    rating: int = Field(..., ge=1, le=5)
    comment: str = Field("", max_length=REVIEW_MAX_CHARS)


@router.post("/reviews")
async def submit_review(req: ReviewRequest, x_api_key: str = Header(alias="X-API-Key", default="")):
    """Submit a review for a service you purchased.
    Anti-spam: 1 review per agent per service, min 20 chars, verified purchase only."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")

    agent = await _get_agent(x_api_key)
    db = await _get_db()

    # Validate service exists
    svc_rows = await db.raw_execute_fetchall(
        "SELECT id, name, agent_api_key FROM agent_services WHERE id=? AND status='active'",
        (req.service_id,))
    if not svc_rows:
        raise HTTPException(404, "Service not found")
    service = dict(svc_rows[0])

    # Can't review your own service
    if service["agent_api_key"] == x_api_key:
        raise HTTPException(403, "Cannot review your own service")

    # Spam: min comment length
    comment = req.comment.strip()
    if comment and len(comment) < REVIEW_MIN_CHARS:
        raise HTTPException(400, f"Comment must be at least {REVIEW_MIN_CHARS} characters")

    # Content safety
    if comment:
        try:
            from core.security import check_content_safety
            check_content_safety(comment)
        except Exception:
            raise HTTPException(400, "Comment contains prohibited content")

    # Hash API key for storage (never store raw keys in public-facing tables)
    reviewer_hash = hashlib.sha256(x_api_key.encode()).hexdigest()

    # Spam: check duplicate (UNIQUE constraint will also catch this)
    existing = await db.raw_execute_fetchall(
        "SELECT id FROM service_reviews WHERE service_id=? AND reviewer_api_key=?",
        (req.service_id, reviewer_hash))
    if existing:
        raise HTTPException(409, "You already reviewed this service. One review per service.")

    # Verify purchase — check transactions
    verified = False
    try:
        tx_rows = await db.raw_execute_fetchall(
            "SELECT tx_id FROM marketplace_tx WHERE buyer=? AND service=?",
            (agent["name"], req.service_id))
        if not tx_rows:
            # Also check by service name
            tx_rows = await db.raw_execute_fetchall(
                "SELECT tx_id FROM marketplace_tx WHERE buyer=? AND service=?",
                (agent["name"], service["name"]))
        verified = bool(tx_rows)
    except Exception as e:
        logger.warning("[Sprint2] Purchase verification query failed: %s", e)

    if not verified:
        raise HTTPException(403, "You can only review services you have purchased")

    # Save review (store hashed API key, not raw)
    review_id = f"rev_{uuid.uuid4().hex[:12]}"
    await db.raw_execute(
        "INSERT INTO service_reviews (id, service_id, reviewer_api_key, reviewer_name, "
        "rating, comment, verified_purchase) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (review_id, req.service_id, reviewer_hash, agent["name"],
         req.rating, comment, 1 if verified else 0))

    # Update service average rating with recency weighting
    await _recalculate_rating(db, req.service_id)

    return {
        "success": True,
        "review_id": review_id,
        "service_id": req.service_id,
        "rating": req.rating,
        "verified_purchase": verified,
    }


async def _recalculate_rating(db, service_id: str):
    """Recalculate service rating with recency weighting.
    Recent reviews (< 30 days) count 2x. Older reviews count 1x."""
    cutoff_30d = int(time.time()) - 30 * 86400
    rows = await db.raw_execute_fetchall(
        "SELECT rating, created_at FROM service_reviews WHERE service_id=? ORDER BY created_at DESC",
        (service_id,))
    if not rows:
        return

    weighted_sum = 0.0
    weight_total = 0.0
    for r in rows:
        row = dict(r)
        weight = 2.0 if (row.get("created_at", 0) or 0) > cutoff_30d else 1.0
        weighted_sum += row["rating"] * weight
        weight_total += weight

    new_rating = round(weighted_sum / weight_total, 2) if weight_total > 0 else 5.0
    count = len(rows)

    try:
        await db.update_service(service_id, {"rating": new_rating, "rating_count": count})
    except Exception as e:
        logger.warning("Rating update failed: %s", e)


@router.get("/reviews/{service_id}")
async def list_reviews(service_id: str, limit: int = 50, offset: int = 0):
    """List all reviews for a service. Sorted by recency (newest first)."""
    if not _ID_RE.match(service_id):
        raise HTTPException(400, "Invalid service ID")

    db = await _get_db()
    limit = max(1, min(100, limit))
    offset = max(0, offset)

    rows = await db.raw_execute_fetchall(
        "SELECT id, reviewer_name, rating, comment, verified_purchase, created_at "
        "FROM service_reviews WHERE service_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (service_id, limit, offset))

    reviews = []
    for r in rows:
        row = dict(r)
        reviews.append({
            "id": row["id"],
            "reviewer": row.get("reviewer_name", "Anonymous"),
            "rating": row["rating"],
            "comment": row.get("comment", ""),
            "verified_purchase": bool(row.get("verified_purchase", 0)),
            "created_at": row.get("created_at", 0),
        })

    # Get summary stats
    stats_rows = await db.raw_execute_fetchall(
        "SELECT COUNT(*) as total, AVG(rating) as avg_rating, "
        "SUM(CASE WHEN rating=5 THEN 1 ELSE 0 END) as stars_5, "
        "SUM(CASE WHEN rating=4 THEN 1 ELSE 0 END) as stars_4, "
        "SUM(CASE WHEN rating=3 THEN 1 ELSE 0 END) as stars_3, "
        "SUM(CASE WHEN rating=2 THEN 1 ELSE 0 END) as stars_2, "
        "SUM(CASE WHEN rating=1 THEN 1 ELSE 0 END) as stars_1 "
        "FROM service_reviews WHERE service_id=?",
        (service_id,))

    stats = dict(stats_rows[0]) if stats_rows else {}

    return {
        "service_id": service_id,
        "reviews": reviews,
        "total": stats.get("total", 0) or 0,
        "average_rating": round(stats.get("avg_rating", 0) or 0, 2),
        "distribution": {
            "5": stats.get("stars_5", 0) or 0,
            "4": stats.get("stars_4", 0) or 0,
            "3": stats.get("stars_3", 0) or 0,
            "2": stats.get("stars_2", 0) or 0,
            "1": stats.get("stars_1", 0) or 0,
        },
        "offset": offset,
        "limit": limit,
    }


# ══════════════════════════════════════════
# E19 — CATEGORIES + FEATURED SELLERS
# ══════════════════════════════════════════

SERVICE_CATEGORIES = [
    {"id": "ai", "name": "AI & Machine Learning", "description": "Code generation, summarization, translation, chat", "min_price": 0.001},
    {"id": "data", "name": "Data & Analytics", "description": "On-chain analysis, web scraping, wallet analysis", "min_price": 0.01},
    {"id": "defi", "name": "DeFi & Trading", "description": "Yield scanning, swap quotes, sentiment analysis, price alerts", "min_price": 0},
    {"id": "audit", "name": "Security & Audit", "description": "Smart contract audits, vulnerability scanning", "min_price": 1.00},
    {"id": "compute", "name": "GPU & Compute", "description": "GPU rental, model training, inference hosting", "min_price": 1.00},
    {"id": "image", "name": "Creative & Image", "description": "Image generation, design, visual content", "min_price": 0.05},
    {"id": "text", "name": "Text & Content", "description": "Translation, marketing copy, documentation", "min_price": 0.01},
    {"id": "automation", "name": "Automation & Pipelines", "description": "Multi-step workflows, monitoring, alerts", "min_price": 0.01},
]


@router.get("/categories")
async def list_categories():
    """List all service categories with service counts."""
    db = await _get_db()

    # Count services per category
    counts: dict = {}
    try:
        rows = await db.raw_execute_fetchall(
            "SELECT LOWER(type) as cat, COUNT(*) as cnt FROM agent_services "
            "WHERE status='active' GROUP BY LOWER(type)")
        for r in rows:
            row = dict(r)
            counts[row.get("cat", "")] = row.get("cnt", 0)
    except Exception:
        pass

    categories = []
    for cat in SERVICE_CATEGORIES:
        cat_copy = {**cat}
        cat_copy["service_count"] = counts.get(cat["id"], 0)
        categories.append(cat_copy)

    return {"categories": categories, "total": len(categories)}


@router.get("/sellers/featured")
async def featured_sellers(limit: int = 10):
    """Featured sellers — ranked by volume, rating, and service count.
    Algorithm: score = log(volume+1) * avg_rating * log(services+1)"""
    db = await _get_db()
    limit = max(1, min(50, limit))

    try:
        rows = await db.raw_execute_fetchall("""
            SELECT a.name, a.wallet, a.tier, a.total_earned, a.services_listed,
                   COALESCE(a.total_earned, 0) as volume,
                   COUNT(DISTINCT s.id) as active_services,
                   AVG(s.rating) as avg_rating,
                   SUM(s.sales) as total_sales
            FROM agents a
            LEFT JOIN agent_services s ON a.api_key = s.agent_api_key AND s.status='active'
            WHERE a.services_listed > 0
            GROUP BY a.api_key
            HAVING active_services > 0
            ORDER BY volume DESC
            LIMIT ?
        """, (limit * 3,))  # Fetch extra to re-rank

        sellers = []
        for r in rows:
            row = dict(r)
            volume = float(row.get("volume", 0) or 0)
            avg_rating = float(row.get("avg_rating", 5.0) or 5.0)
            services = int(row.get("active_services", 0) or 0)
            total_sales = int(row.get("total_sales", 0) or 0)

            # Composite score
            score = math.log(volume + 1) * avg_rating * math.log(services + 2)

            # Badges
            badges = []
            if volume >= 5000:
                badges.append("whale")
            if services >= 3:
                badges.append("builder")
            if avg_rating >= 4.5 and total_sales >= 5:
                badges.append("top-rated")
            if total_sales >= 10:
                badges.append("verified-seller")

            sellers.append({
                "name": row.get("name", ""),
                "wallet": row.get("wallet", ""),
                "tier": row.get("tier", "BRONZE"),
                "active_services": services,
                "total_sales": total_sales,
                "total_volume_usdc": round(volume, 2),
                "avg_rating": round(avg_rating, 2),
                "badges": badges,
                "_score": score,
            })

        # Sort by composite score
        sellers.sort(key=lambda x: x["_score"], reverse=True)
        for s in sellers:
            s.pop("_score", None)

        return {
            "featured_sellers": sellers[:limit],
            "count": min(len(sellers), limit),
            "how_to_sell": {
                "step_1": "Register: POST /api/public/register",
                "step_2": "List service: POST /api/public/sell",
                "step_3": "Earn 98.5-99.9% of every sale (MAXIA commission: 0.1-1.5%)",
                "docs": "https://maxiaworld.app/docs#sell",
            },
        }
    except Exception as e:
        logger.error("Featured sellers error: %s", e)
        return {"featured_sellers": [], "count": 0}


@router.get("/seller/{wallet}/stats")
async def seller_stats(wallet: str):
    """Public seller statistics — anyone can verify a seller's track record."""
    if not re.match(r'^[a-zA-Z0-9]{32,64}$', wallet):
        raise HTTPException(400, "Invalid wallet format")

    db = await _get_db()
    try:
        # Agent info
        agent_rows = await db.raw_execute_fetchall(
            "SELECT name, tier, total_earned, services_listed, created_at "
            "FROM agents WHERE wallet=?", (wallet,))
        if not agent_rows:
            raise HTTPException(404, "Seller not found")
        agent = dict(agent_rows[0])

        # Services
        svc_rows = await db.raw_execute_fetchall(
            "SELECT id, name, price_usdc, rating, rating_count, sales, type "
            "FROM agent_services WHERE agent_wallet=? AND status='active'",
            (wallet,))
        services = [dict(r) for r in svc_rows]

        # Transaction stats
        tx_rows = await db.raw_execute_fetchall(
            "SELECT COUNT(*) as cnt, COALESCE(SUM(seller_gets_usdc), 0) as total "
            "FROM marketplace_tx WHERE seller=?", (agent["name"],))
        tx = dict(tx_rows[0]) if tx_rows else {"cnt": 0, "total": 0}

        # Review summary across all services
        service_ids = [s["id"] for s in services]
        total_reviews = 0
        avg_review = 0.0
        if service_ids:
            placeholders = ",".join(["?"] * len(service_ids))
            rev_rows = await db.raw_execute_fetchall(
                f"SELECT COUNT(*) as cnt, AVG(rating) as avg FROM service_reviews "
                f"WHERE service_id IN ({placeholders})", tuple(service_ids))
            if rev_rows:
                rev = dict(rev_rows[0])
                total_reviews = rev.get("cnt", 0) or 0
                avg_review = round(rev.get("avg", 0) or 0, 2)

        # Member duration
        created = agent.get("created_at", 0) or 0
        member_days = (int(time.time()) - created) // 86400 if created else 0

        return {
            "name": agent["name"],
            "wallet": wallet,
            "tier": agent.get("tier", "BRONZE"),
            "member_days": member_days,
            "active_services": len(services),
            "services": services,
            "total_sales": tx.get("cnt", 0) or 0,
            "total_earned_usdc": round(float(tx.get("total", 0) or 0), 2),
            "total_reviews": total_reviews,
            "avg_review_rating": avg_review,
            "revenue_share": "98.5% (Bronze) to 99.9% (Whale)",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Seller stats error: %s", e)
        raise HTTPException(500, safe_error("Seller stats failed", e))


# ══════════════════════════════════════════
# E9 — PIONEER 100 PROGRAM
# ══════════════════════════════════════════

PIONEER_CAP = 100
PIONEER_BONUS_USDC = 5.0  # $5 USDC credits for each pioneer


@router.get("/pioneer/status")
async def pioneer_status():
    """Check Pioneer 100 program status — how many slots remain."""
    db = await _get_db()
    try:
        rows = await db.raw_execute_fetchall(
            "SELECT COUNT(*) as cnt FROM pioneer_enrollments")
        count = dict(rows[0]).get("cnt", 0) if rows else 0
    except Exception:
        count = 0

    remaining = max(0, PIONEER_CAP - count)
    return {
        "program": "Pioneer 100",
        "description": f"First {PIONEER_CAP} agents get ${PIONEER_BONUS_USDC} USDC credits + Pioneer badge",
        "enrolled": count,
        "remaining": remaining,
        "cap": PIONEER_CAP,
        "is_open": remaining > 0,
        "bonus_usdc": PIONEER_BONUS_USDC,
        "benefits": [
            f"${PIONEER_BONUS_USDC} USDC credits (free to use on any service)",
            "Pioneer badge on your agent profile",
            "Early access to new features",
            "Priority support",
            "Listed on pioneer leaderboard",
        ],
    }


@router.post("/pioneer/enroll")
async def pioneer_enroll(x_api_key: str = Header(alias="X-API-Key", default="")):
    """Enroll in Pioneer 100 program. First 100 agents get bonus credits + badge."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")

    agent = await _get_agent(x_api_key)
    db = await _get_db()

    # Check if already enrolled (use hashed key)
    api_key_hash = hashlib.sha256(x_api_key.encode()).hexdigest()
    existing = await db.raw_execute_fetchall(
        "SELECT id FROM pioneer_enrollments WHERE api_key=?", (api_key_hash,))
    if existing:
        raise HTTPException(409, "Already enrolled in Pioneer program")

    # Atomic cap-check + insert in a single SQL statement (prevents TOCTOU race)
    api_key_hash = hashlib.sha256(x_api_key.encode()).hexdigest()
    result = await db.raw_execute(
        "INSERT INTO pioneer_enrollments (api_key, agent_name, wallet, bonus_credits_usdc) "
        "SELECT ?, ?, ?, ? WHERE (SELECT COUNT(*) FROM pioneer_enrollments) < ?",
        (api_key_hash, agent["name"], agent["wallet"], PIONEER_BONUS_USDC, PIONEER_CAP))

    # Check if insert happened (rows affected)
    count_rows = await db.raw_execute_fetchall(
        "SELECT COUNT(*) as cnt FROM pioneer_enrollments WHERE api_key=?", (api_key_hash,))
    if not count_rows or dict(count_rows[0]).get("cnt", 0) == 0:
        raise HTTPException(410, f"Pioneer program is full ({PIONEER_CAP}/{PIONEER_CAP})")

    # Grant credits (update prepaid balance if table exists — use real key for credits)
    try:
        await db.raw_execute(
            "INSERT INTO prepaid_credits (api_key, balance_usdc, updated_at) "
            "VALUES (?, ?, strftime('%s','now')) "
            "ON CONFLICT(api_key) DO UPDATE SET balance_usdc = balance_usdc + ?",
            (x_api_key, PIONEER_BONUS_USDC, PIONEER_BONUS_USDC))  # Credits need real key
    except Exception:
        # prepaid_credits table may not exist yet — credits still recorded in pioneer_enrollments
        logger.debug("Prepaid credits table not available — pioneer bonus stored in enrollment record")

    # Grant badge
    try:
        from billing.referral import _award_badge
        await _award_badge(x_api_key, "pioneer")
    except Exception:
        logger.debug("Badge award skipped — badge system may not have 'pioneer' defined")

    slot = count + 1
    return {
        "success": True,
        "pioneer_number": slot,
        "agent": agent["name"],
        "bonus_credits_usdc": PIONEER_BONUS_USDC,
        "message": f"Welcome, Pioneer #{slot}! ${PIONEER_BONUS_USDC} USDC credits added to your account.",
        "remaining_slots": PIONEER_CAP - slot,
    }


@router.get("/pioneer/leaderboard")
async def pioneer_leaderboard():
    """List all Pioneer agents in enrollment order."""
    db = await _get_db()
    try:
        rows = await db.raw_execute_fetchall(
            "SELECT agent_name, wallet, bonus_credits_usdc, enrolled_at "
            "FROM pioneer_enrollments ORDER BY enrolled_at ASC LIMIT ?",
            (PIONEER_CAP,))

        pioneers = []
        for i, r in enumerate(rows, 1):
            row = dict(r)
            pioneers.append({
                "rank": i,
                "name": row.get("agent_name", ""),
                "wallet": row.get("wallet", "")[:8] + "...",  # Privacy: truncate
                "bonus_usdc": row.get("bonus_credits_usdc", 0),
                "enrolled_at": row.get("enrolled_at", 0),
            })

        return {
            "pioneers": pioneers,
            "count": len(pioneers),
            "cap": PIONEER_CAP,
        }
    except Exception as e:
        logger.error("Pioneer leaderboard error: %s", e)
        return {"pioneers": [], "count": 0, "cap": PIONEER_CAP}
