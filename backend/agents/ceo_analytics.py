"""MAXIA V12 — CEO Analytics endpoints (feedback loop).

Gives the CEO agent real data to make better decisions:
- /api/ceo/analytics/web          — signups, agents, swaps, volume
- /api/ceo/analytics/performance  — CEO action tracking (tweets, emails)
- /api/ceo/analytics/competitors  — GitHub stars, cached 24h
- /api/ceo/analytics/kpi          — weekly KPI scoring 0-100
- POST /api/ceo/actions/log       — log a CEO action
- PATCH /api/ceo/actions/{id}     — update status / engagement
"""
import hmac
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Header, Request

logger = logging.getLogger("maxia.ceo_analytics")

router = APIRouter(prefix="/api/ceo", tags=["ceo-analytics"])

# ── Schema ──

_schema_ready = False

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ceo_actions (
    action_id TEXT PRIMARY KEY,
    action_type TEXT NOT NULL,
    content TEXT DEFAULT '',
    platform TEXT DEFAULT 'twitter',
    status TEXT DEFAULT 'proposed',
    engagement TEXT DEFAULT '{}',
    created_at INTEGER DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_ceo_actions_type ON ceo_actions(action_type, created_at);
CREATE INDEX IF NOT EXISTS idx_ceo_actions_status ON ceo_actions(status, created_at);
"""

VALID_ACTION_TYPES = frozenset({
    "tweet_proposed", "tweet_posted", "email_sent", "email_replied",
    "comment_proposed", "comment_posted", "quote_tweet",
    "dm_sent", "dm_replied", "thread_posted",
})

VALID_STATUSES = frozenset({
    "proposed", "approved", "posted", "rejected",
})


# ── Helpers ──

async def _get_db():
    from core.database import db
    return db


async def _ensure_schema():
    global _schema_ready
    if _schema_ready:
        return
    try:
        db = await _get_db()
        await db.raw_executescript(_SCHEMA_SQL)
        _schema_ready = True
    except Exception as e:
        logger.error("[CEO Analytics] Schema error: %s", e)


def _require_ceo_key(x_ceo_key: str) -> None:
    """Validate CEO auth key (timing-safe comparison)."""
    expected = os.getenv("CEO_API_KEY", "")
    if not expected:
        raise HTTPException(503, "CEO gateway not configured")
    if not x_ceo_key or not hmac.compare_digest(x_ceo_key.encode(), expected.encode()):
        raise HTTPException(403, "CEO key required")


def _now_epoch() -> int:
    return int(time.time())


def _epoch_minus(seconds: int) -> int:
    return _now_epoch() - seconds


# ── Competitor cache (24h TTL) ──

_competitor_cache: dict = {}
_competitor_cache_ts: float = 0.0
_COMPETITOR_TTL_S = 86400  # 24h

COMPETITOR_REPOS = {
    "virtuals_protocol": "Virtual-Protocol/virtuals-python",
    "elizaos": "elizaOS/eliza",
    "olas": "valory-ai/olas",
    "autogpt": "Significant-Gravitas/AutoGPT",
    "crewai": "crewAIInc/crewAI",
}


# ═══════════════════════════════════════════
#  1. GET /api/ceo/analytics/web
# ═══════════════════════════════════════════

@router.get("/analytics/web")
async def ceo_analytics_web(
    x_ceo_key: str = Header("", alias="X-CEO-Key"),
):
    """Web analytics from database — signups, agents, swaps, volume."""
    _require_ceo_key(x_ceo_key)
    await _ensure_schema()
    db = await _get_db()

    now = _now_epoch()
    day_ago = now - 86400
    week_ago = now - 604800

    result = {
        "visitors_24h": 0,
        "signups_24h": 0,
        "signups_7d": 0,
        "total_agents": 0,
        "total_services": 0,
        "total_swaps_24h": 0,
        "total_volume_24h": 0.0,
        "top_referrers": [],
        "conversion_rate": 0.0,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    # Visitors 24h — unique wallets that made any transaction in 24h
    try:
        rows = await db.raw_execute_fetchall(
            "SELECT COUNT(DISTINCT wallet) as cnt FROM transactions WHERE created_at > ?",
            (day_ago,))
        result["visitors_24h"] = rows[0]["cnt"] if rows else 0
    except Exception as e:
        logger.warning("[CEO Analytics] visitors query failed: %s", e)

    # Signups 24h
    try:
        rows = await db.raw_execute_fetchall(
            "SELECT COUNT(*) as cnt FROM agents WHERE created_at > ?",
            (day_ago,))
        result["signups_24h"] = rows[0]["cnt"] if rows else 0
    except Exception as e:
        logger.warning("[CEO Analytics] signups_24h query failed: %s", e)

    # Signups 7d
    try:
        rows = await db.raw_execute_fetchall(
            "SELECT COUNT(*) as cnt FROM agents WHERE created_at > ?",
            (week_ago,))
        result["signups_7d"] = rows[0]["cnt"] if rows else 0
    except Exception as e:
        logger.warning("[CEO Analytics] signups_7d query failed: %s", e)

    # Total agents
    try:
        rows = await db.raw_execute_fetchall("SELECT COUNT(*) as cnt FROM agents")
        result["total_agents"] = rows[0]["cnt"] if rows else 0
    except Exception as e:
        logger.warning("[CEO Analytics] total_agents query failed: %s", e)

    # Total active services
    try:
        rows = await db.raw_execute_fetchall(
            "SELECT COUNT(*) as cnt FROM agent_services WHERE status='active'")
        result["total_services"] = rows[0]["cnt"] if rows else 0
    except Exception as e:
        logger.warning("[CEO Analytics] total_services query failed: %s", e)

    # Total swaps 24h
    try:
        rows = await db.raw_execute_fetchall(
            "SELECT COUNT(*) as cnt FROM crypto_swaps WHERE created_at > ?",
            (day_ago,))
        result["total_swaps_24h"] = rows[0]["cnt"] if rows else 0
    except Exception as e:
        logger.warning("[CEO Analytics] total_swaps_24h query failed: %s", e)

    # Total volume 24h (from transactions)
    try:
        rows = await db.raw_execute_fetchall(
            "SELECT COALESCE(SUM(amount_usdc), 0) as vol FROM transactions WHERE created_at > ?",
            (day_ago,))
        result["total_volume_24h"] = float(rows[0]["vol"]) if rows else 0.0
    except Exception as e:
        logger.warning("[CEO Analytics] total_volume_24h query failed: %s", e)

    # Top referrers (from referrals table)
    try:
        rows = await db.raw_execute_fetchall(
            "SELECT referrer, COUNT(*) as cnt FROM referrals "
            "GROUP BY referrer ORDER BY cnt DESC LIMIT 10")
        result["top_referrers"] = [{"referrer": r["referrer"], "count": r["cnt"]} for r in rows]
    except Exception as e:
        logger.warning("[CEO Analytics] top_referrers query failed: %s", e)

    # Conversion rate
    visitors = max(result["visitors_24h"], 1)
    result["conversion_rate"] = round(result["signups_24h"] / visitors, 4)

    return result


# ═══════════════════════════════════════════
#  2. GET /api/ceo/analytics/performance
# ═══════════════════════════════════════════

@router.get("/analytics/performance")
async def ceo_analytics_performance(
    x_ceo_key: str = Header("", alias="X-CEO-Key"),
):
    """CEO action performance — tweets proposed/posted, emails, KPI score."""
    _require_ceo_key(x_ceo_key)
    await _ensure_schema()
    db = await _get_db()

    now = _now_epoch()
    week_ago = now - 604800
    day_start = now - (now % 86400)  # Start of today (UTC)

    result = {
        "tweets_proposed_7d": 0,
        "tweets_posted_7d": 0,
        "emails_sent_7d": 0,
        "emails_replied_7d": 0,
        "comments_posted_7d": 0,
        "actions_today": [],
        "score_weekly": 0,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    # Tweets proposed 7d
    try:
        rows = await db.raw_execute_fetchall(
            "SELECT COUNT(*) as cnt FROM ceo_actions "
            "WHERE action_type IN ('tweet_proposed','comment_proposed') AND created_at > ?",
            (week_ago,))
        result["tweets_proposed_7d"] = rows[0]["cnt"] if rows else 0
    except Exception as e:
        logger.warning("[CEO Analytics] tweets_proposed query failed: %s", e)

    # Tweets posted 7d
    try:
        rows = await db.raw_execute_fetchall(
            "SELECT COUNT(*) as cnt FROM ceo_actions "
            "WHERE action_type IN ('tweet_posted','comment_posted','quote_tweet') "
            "AND status='posted' AND created_at > ?",
            (week_ago,))
        result["tweets_posted_7d"] = rows[0]["cnt"] if rows else 0
    except Exception as e:
        logger.warning("[CEO Analytics] tweets_posted query failed: %s", e)

    # Emails sent 7d
    try:
        rows = await db.raw_execute_fetchall(
            "SELECT COUNT(*) as cnt FROM ceo_actions "
            "WHERE action_type='email_sent' AND created_at > ?",
            (week_ago,))
        result["emails_sent_7d"] = rows[0]["cnt"] if rows else 0
    except Exception as e:
        logger.warning("[CEO Analytics] emails_sent query failed: %s", e)

    # Emails replied 7d
    try:
        rows = await db.raw_execute_fetchall(
            "SELECT COUNT(*) as cnt FROM ceo_actions "
            "WHERE action_type='email_replied' AND created_at > ?",
            (week_ago,))
        result["emails_replied_7d"] = rows[0]["cnt"] if rows else 0
    except Exception as e:
        logger.warning("[CEO Analytics] emails_replied query failed: %s", e)

    # Comments posted 7d
    try:
        rows = await db.raw_execute_fetchall(
            "SELECT COUNT(*) as cnt FROM ceo_actions "
            "WHERE action_type='comment_posted' AND status='posted' AND created_at > ?",
            (week_ago,))
        result["comments_posted_7d"] = rows[0]["cnt"] if rows else 0
    except Exception as e:
        logger.warning("[CEO Analytics] comments_posted query failed: %s", e)

    # Actions today
    try:
        rows = await db.raw_execute_fetchall(
            "SELECT action_id, action_type, content, platform, status, engagement, created_at "
            "FROM ceo_actions WHERE created_at > ? ORDER BY created_at DESC LIMIT 50",
            (day_start,))
        result["actions_today"] = [
            {
                "action_id": r["action_id"],
                "action_type": r["action_type"],
                "content": r["content"][:200] if r.get("content") else "",
                "platform": r["platform"],
                "status": r["status"],
                "engagement": _safe_json_load(r.get("engagement", "{}")),
                "created_at": r["created_at"],
            }
            for r in rows
        ]
    except Exception as e:
        logger.warning("[CEO Analytics] actions_today query failed: %s", e)

    # Weekly KPI score (0-100)
    # Formula: posted_content * 10 + emails_sent * 5 + replies * 15, capped at 100
    posted = result["tweets_posted_7d"] + result["comments_posted_7d"]
    score = min(100, posted * 10 + result["emails_sent_7d"] * 5 + result["emails_replied_7d"] * 15)
    result["score_weekly"] = score

    return result


# ═══════════════════════════════════════════
#  3. GET /api/ceo/analytics/competitors
# ═══════════════════════════════════════════

@router.get("/analytics/competitors")
async def ceo_analytics_competitors(
    x_ceo_key: str = Header("", alias="X-CEO-Key"),
):
    """Competitive intelligence — GitHub stars (cached 24h)."""
    _require_ceo_key(x_ceo_key)
    await _ensure_schema()

    global _competitor_cache, _competitor_cache_ts

    # Return cache if fresh
    if _competitor_cache and (time.time() - _competitor_cache_ts) < _COMPETITOR_TTL_S:
        return _competitor_cache

    db = await _get_db()
    competitors = {}

    # Fetch GitHub stars (public API, no key needed)
    github_token = os.getenv("GITHUB_TOKEN", "")
    headers = {"Accept": "application/vnd.github.v3+json"}
    if github_token:
        headers["Authorization"] = f"token {github_token}"

    async with httpx.AsyncClient(timeout=15.0) as client:
        for name, repo in COMPETITOR_REPOS.items():
            try:
                resp = await client.get(
                    f"https://api.github.com/repos/{repo}",
                    headers=headers,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    competitors[name] = {
                        "github_stars": data.get("stargazers_count", 0),
                        "github_forks": data.get("forks_count", 0),
                        "open_issues": data.get("open_issues_count", 0),
                        "language": data.get("language", ""),
                    }
                else:
                    competitors[name] = {"github_stars": 0, "error": f"HTTP {resp.status_code}"}
            except Exception as e:
                competitors[name] = {"github_stars": 0, "error": str(e)[:80]}

    # MAXIA stats from DB
    maxia_stats = {"agents": 0, "services": 0, "volume_7d": 0.0}
    try:
        rows = await db.raw_execute_fetchall("SELECT COUNT(*) as cnt FROM agents")
        maxia_stats["agents"] = rows[0]["cnt"] if rows else 0
    except Exception:
        pass
    try:
        rows = await db.raw_execute_fetchall(
            "SELECT COUNT(*) as cnt FROM agent_services WHERE status='active'")
        maxia_stats["services"] = rows[0]["cnt"] if rows else 0
    except Exception:
        pass
    try:
        week_ago = _epoch_minus(604800)
        rows = await db.raw_execute_fetchall(
            "SELECT COALESCE(SUM(amount_usdc), 0) as vol FROM transactions WHERE created_at > ?",
            (week_ago,))
        maxia_stats["volume_7d"] = float(rows[0]["vol"]) if rows else 0.0
    except Exception:
        pass

    result = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "competitors": competitors,
        "maxia": maxia_stats,
    }

    # Cache result
    _competitor_cache = result
    _competitor_cache_ts = time.time()

    return result


# ═══════════════════════════════════════════
#  4. GET /api/ceo/analytics/kpi
# ═══════════════════════════════════════════

@router.get("/analytics/kpi")
async def ceo_analytics_kpi(
    x_ceo_key: str = Header("", alias="X-CEO-Key"),
):
    """Weekly KPI scoring — targets vs actuals, trend, recommendation."""
    _require_ceo_key(x_ceo_key)
    await _ensure_schema()
    db = await _get_db()

    now = _now_epoch()
    week_ago = now - 604800
    two_weeks_ago = now - 1209600

    # Current ISO week
    today = datetime.now(timezone.utc)
    iso_week = today.strftime("%G-W%V")

    # ── Objectives ──

    # 1. New agents this week
    new_agents = 0
    new_agents_prev = 0
    try:
        rows = await db.raw_execute_fetchall(
            "SELECT COUNT(*) as cnt FROM agents WHERE created_at > ?", (week_ago,))
        new_agents = rows[0]["cnt"] if rows else 0
        rows = await db.raw_execute_fetchall(
            "SELECT COUNT(*) as cnt FROM agents WHERE created_at > ? AND created_at <= ?",
            (two_weeks_ago, week_ago))
        new_agents_prev = rows[0]["cnt"] if rows else 0
    except Exception as e:
        logger.warning("[CEO Analytics] kpi new_agents failed: %s", e)

    # 2. Swap volume this week
    swap_volume = 0.0
    swap_volume_prev = 0.0
    try:
        rows = await db.raw_execute_fetchall(
            "SELECT COALESCE(SUM(amount_in), 0) as vol FROM crypto_swaps WHERE created_at > ?",
            (week_ago,))
        swap_volume = float(rows[0]["vol"]) if rows else 0.0
        rows = await db.raw_execute_fetchall(
            "SELECT COALESCE(SUM(amount_in), 0) as vol FROM crypto_swaps "
            "WHERE created_at > ? AND created_at <= ?",
            (two_weeks_ago, week_ago))
        swap_volume_prev = float(rows[0]["vol"]) if rows else 0.0
    except Exception as e:
        logger.warning("[CEO Analytics] kpi swap_volume failed: %s", e)

    # 3. Content posted this week
    content_posted = 0
    content_posted_prev = 0
    try:
        rows = await db.raw_execute_fetchall(
            "SELECT COUNT(*) as cnt FROM ceo_actions "
            "WHERE status='posted' AND created_at > ?",
            (week_ago,))
        content_posted = rows[0]["cnt"] if rows else 0
        rows = await db.raw_execute_fetchall(
            "SELECT COUNT(*) as cnt FROM ceo_actions "
            "WHERE status='posted' AND created_at > ? AND created_at <= ?",
            (two_weeks_ago, week_ago))
        content_posted_prev = rows[0]["cnt"] if rows else 0
    except Exception as e:
        logger.warning("[CEO Analytics] kpi content_posted failed: %s", e)

    # Targets
    targets = {
        "new_agents": 5,
        "swap_volume": 100.0,
        "content_posted": 7,
    }

    # Score each objective (0-100, linear)
    def _score(actual: float, target: float) -> int:
        if target <= 0:
            return 100
        return min(100, int((actual / target) * 100))

    objectives = {
        "new_agents": {
            "target": targets["new_agents"],
            "actual": new_agents,
            "previous_week": new_agents_prev,
            "score": _score(new_agents, targets["new_agents"]),
        },
        "swap_volume": {
            "target": targets["swap_volume"],
            "actual": round(swap_volume, 2),
            "previous_week": round(swap_volume_prev, 2),
            "score": _score(swap_volume, targets["swap_volume"]),
        },
        "content_posted": {
            "target": targets["content_posted"],
            "actual": content_posted,
            "previous_week": content_posted_prev,
            "score": _score(content_posted, targets["content_posted"]),
        },
    }

    # Total score (weighted average)
    total_score = int(
        objectives["new_agents"]["score"] * 0.4
        + objectives["swap_volume"]["score"] * 0.3
        + objectives["content_posted"]["score"] * 0.3
    )

    # Trend vs previous week
    current_total = new_agents + swap_volume + content_posted
    prev_total = new_agents_prev + swap_volume_prev + content_posted_prev
    if current_total > prev_total * 1.1:
        trend = "up"
    elif current_total < prev_total * 0.9:
        trend = "down"
    else:
        trend = "stable"

    # Recommendation based on weakest area
    weakest = min(objectives, key=lambda k: objectives[k]["score"])
    recommendations = {
        "new_agents": "Focus on agent acquisition — increase outreach, post on AI directories, engage in Discord/Telegram communities.",
        "swap_volume": "Boost swap volume — promote trading features, highlight low fees, target whale wallets.",
        "content_posted": "Increase content output — post more tweets, write threads, comment on trending AI topics.",
    }

    return {
        "week": iso_week,
        "objectives": objectives,
        "total_score": total_score,
        "trend": trend,
        "weakest_area": weakest,
        "recommendation": recommendations.get(weakest, ""),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ═══════════════════════════════════════════
#  5. POST /api/ceo/actions/log
# ═══════════════════════════════════════════

@router.post("/actions/log")
async def ceo_action_log(
    req: dict,
    x_ceo_key: str = Header("", alias="X-CEO-Key"),
):
    """Log a CEO action (tweet proposed, email sent, etc.)."""
    _require_ceo_key(x_ceo_key)
    await _ensure_schema()

    action_type = str(req.get("action_type", "")).strip()
    if action_type not in VALID_ACTION_TYPES:
        raise HTTPException(400, f"action_type must be one of: {', '.join(sorted(VALID_ACTION_TYPES))}")

    content = str(req.get("content", "")).strip()
    if len(content) > 5000:
        raise HTTPException(400, "content max 5000 chars")

    platform = str(req.get("platform", "twitter")).strip().lower()
    if len(platform) > 32:
        raise HTTPException(400, "platform max 32 chars")

    action_id = str(uuid.uuid4())[:12]
    now = _now_epoch()

    db = await _get_db()
    await db.raw_execute(
        "INSERT INTO ceo_actions(action_id, action_type, content, platform, status, engagement, created_at) "
        "VALUES(?,?,?,?,?,?,?)",
        (action_id, action_type, content, platform, "proposed", "{}", now))

    return {"action_id": action_id, "status": "proposed"}


# ═══════════════════════════════════════════
#  6. PATCH /api/ceo/actions/{action_id}
# ═══════════════════════════════════════════

@router.patch("/actions/{action_id}")
async def ceo_action_update(
    action_id: str,
    req: dict,
    x_ceo_key: str = Header("", alias="X-CEO-Key"),
):
    """Update CEO action status and/or engagement data."""
    _require_ceo_key(x_ceo_key)
    await _ensure_schema()

    if not action_id or len(action_id) > 64:
        raise HTTPException(400, "Invalid action_id")

    db = await _get_db()

    # Verify action exists
    rows = await db.raw_execute_fetchall(
        "SELECT action_id, status, engagement FROM ceo_actions WHERE action_id=?",
        (action_id,))
    if not rows:
        raise HTTPException(404, "Action not found")

    current = dict(rows[0])
    new_status = str(req.get("status", current["status"])).strip()
    if new_status not in VALID_STATUSES:
        raise HTTPException(400, f"status must be one of: {', '.join(sorted(VALID_STATUSES))}")

    # Merge engagement data
    existing_engagement = _safe_json_load(current.get("engagement", "{}"))
    new_engagement = req.get("engagement")
    if new_engagement and isinstance(new_engagement, dict):
        merged = {**existing_engagement, **new_engagement}
    else:
        merged = existing_engagement

    engagement_json = json.dumps(merged)

    await db.raw_execute(
        "UPDATE ceo_actions SET status=?, engagement=? WHERE action_id=?",
        (new_status, engagement_json, action_id))

    return {
        "action_id": action_id,
        "status": new_status,
        "engagement": merged,
        "updated": True,
    }


# ── Utility ──

def _safe_json_load(val) -> dict:
    """Safely parse JSON string, returning empty dict on failure."""
    if isinstance(val, dict):
        return val
    if not val or not isinstance(val, str):
        return {}
    try:
        parsed = json.loads(val)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def get_router():
    """Factory function for main.py router mounting."""
    return router
