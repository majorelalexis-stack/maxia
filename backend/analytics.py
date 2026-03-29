"""MAXIA Analytics — Dashboard analytics with cached aggregation queries"""
import logging
import time, json
from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analytics", tags=["Analytics"])

# In-memory cache (Redis-compatible fallback)
_cache: dict = {}
_CACHE_TTL_S = 60  # 1 minute default


def _cache_get(key: str):
    """Get from cache if not expired."""
    entry = _cache.get(key)
    if entry and (time.time() - entry["ts"]) < entry.get("ttl", _CACHE_TTL_S):
        return entry["data"]
    return None


def _cache_set(key: str, data, ttl: int = None):
    """Set cache entry with TTL."""
    _cache[key] = {"data": data, "ts": time.time(), "ttl": ttl or _CACHE_TTL_S}


def _parse_period(period: str) -> int:
    """Parse period string like '7d', '24h', '30d' to hours."""
    period = period.strip().lower()
    if period.endswith("d"):
        return int(period[:-1]) * 24
    elif period.endswith("h"):
        return int(period[:-1])
    return 168  # default 7 days


def _parse_granularity(granularity: str) -> int:
    """Parse granularity string like '1h', '6h', '1d' to hours."""
    g = granularity.strip().lower()
    if g.endswith("d"):
        return int(g[:-1]) * 24
    elif g.endswith("h"):
        return int(g[:-1])
    return 24


# ── Core analytics functions ──

async def get_volume_timeseries(db, period_hours: int = 168,
                                 granularity_hours: int = 24) -> list:
    """Volume buckets over a time period."""
    cache_key = f"volume_{period_hours}_{granularity_hours}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    now = int(time.time())
    start = now - period_hours * 3600
    bucket_size = granularity_hours * 3600
    buckets = []

    try:
        # Generate time buckets and query each
        t = start
        while t < now:
            bucket_end = min(t + bucket_size, now)
            row = await db._fetchone(
                "SELECT COALESCE(SUM(amount_usdc), 0) AS vol, COUNT(*) AS tx_count "
                "FROM transactions WHERE created_at >= ? AND created_at < ?",
                (t, bucket_end))
            buckets.append({
                "timestamp": t,
                "timestamp_end": bucket_end,
                "volume_usdc": round(float(row["vol"]), 2) if row else 0.0,
                "tx_count": int(row["tx_count"]) if row else 0,
            })
            t += bucket_size
    except Exception as e:
        logger.error(f"[Analytics] volume_timeseries error: {e}")

    _cache_set(cache_key, buckets, ttl=30)
    return buckets


async def get_top_agents(db, limit: int = 20, period_days: int = 30) -> list:
    """Top agents by volume in the given period."""
    cache_key = f"top_agents_{limit}_{period_days}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    cutoff = int(time.time()) - period_days * 86400
    agents = []

    try:
        rows = await db.raw_execute_fetchall(
            "SELECT wallet, SUM(amount_usdc) AS total_volume, COUNT(*) AS tx_count "
            "FROM transactions WHERE created_at >= ? "
            "GROUP BY wallet ORDER BY total_volume DESC LIMIT ?",
            (cutoff, limit))
        for r in rows:
            row = dict(r)
            # Try to get agent name
            agent_row = await db._fetchone(
                "SELECT name, tier FROM agents WHERE wallet=?", (row["wallet"],))
            agents.append({
                "wallet": row["wallet"],
                "name": agent_row["name"] if agent_row else row["wallet"][:12] + "...",
                "tier": agent_row["tier"] if agent_row else "BRONZE",
                "volume_usdc": round(float(row["total_volume"]), 2),
                "tx_count": int(row["tx_count"]),
            })
    except Exception as e:
        logger.error(f"[Analytics] top_agents error: {e}")

    _cache_set(cache_key, agents)
    return agents


async def get_revenue_breakdown(db, period_days: int = 30) -> dict:
    """Revenue breakdown by service type, chain, and commission tier."""
    cache_key = f"revenue_{period_days}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    cutoff = int(time.time()) - period_days * 86400
    result = {"by_purpose": {}, "by_chain": {}, "total_volume_usdc": 0, "total_commission_usdc": 0}

    try:
        # By purpose (service type)
        rows = await db.raw_execute_fetchall(
            "SELECT purpose, SUM(amount_usdc) AS vol, COUNT(*) AS cnt "
            "FROM transactions WHERE created_at >= ? GROUP BY purpose ORDER BY vol DESC",
            (cutoff,))
        for r in rows:
            row = dict(r)
            result["by_purpose"][row["purpose"]] = {
                "volume_usdc": round(float(row["vol"]), 2),
                "tx_count": int(row["cnt"]),
            }

        # Total volume
        total_row = await db._fetchone(
            "SELECT COALESCE(SUM(amount_usdc), 0) AS total FROM transactions WHERE created_at >= ?",
            (cutoff,))
        result["total_volume_usdc"] = round(float(total_row["total"]), 2) if total_row else 0

        # Commission revenue from marketplace_tx
        comm_row = await db._fetchone(
            "SELECT COALESCE(SUM(commission_usdc), 0) AS comm, "
            "COALESCE(SUM(price_usdc), 0) AS vol "
            "FROM marketplace_tx WHERE created_at >= ?",
            (cutoff,))
        result["total_commission_usdc"] = round(float(comm_row["comm"]), 2) if comm_row else 0

    except Exception as e:
        logger.error(f"[Analytics] revenue_breakdown error: {e}")

    _cache_set(cache_key, result)
    return result


async def get_service_popularity(db, limit: int = 10) -> list:
    """Most purchased services."""
    cache_key = f"service_pop_{limit}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    services = []
    try:
        # From marketplace_tx
        rows = await db.raw_execute_fetchall(
            "SELECT service, COUNT(*) AS purchases, SUM(price_usdc) AS revenue "
            "FROM marketplace_tx GROUP BY service ORDER BY purchases DESC LIMIT ?",
            (limit,))
        for r in rows:
            row = dict(r)
            # Try to get service details
            svc = await db.get_service(row["service"])
            services.append({
                "service_id": row["service"],
                "name": svc["name"] if svc else row["service"][:20],
                "type": svc.get("type", "unknown") if svc else "unknown",
                "purchases": int(row["purchases"]),
                "revenue_usdc": round(float(row["revenue"]), 2),
                "rating": svc.get("rating", 0) if svc else 0,
            })
    except Exception as e:
        logger.error(f"[Analytics] service_popularity error: {e}")

    _cache_set(cache_key, services)
    return services


async def get_realtime_metrics(db) -> dict:
    """Real-time metrics: active escrows, pending commands, agent count."""
    cache_key = "realtime"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    metrics = {
        "active_escrows": 0,
        "pending_commands": 0,
        "agent_count": 0,
        "services_active": 0,
        "volume_24h": 0.0,
        "volume_7d": 0.0,
        "timestamp": int(time.time()),
    }

    try:
        # Active escrows
        row = await db._fetchone(
            "SELECT COUNT(*) AS cnt FROM escrow_records WHERE status='locked'")
        metrics["active_escrows"] = int(row["cnt"]) if row else 0
    except Exception:
        pass

    try:
        # Pending commands
        rows = await db.raw_execute_fetchall("SELECT data FROM commands")
        pending = 0
        for r in rows:
            try:
                cmd = json.loads(r["data"])
                if cmd.get("status") == "pending":
                    pending += 1
            except Exception:
                pass
        metrics["pending_commands"] = pending
    except Exception:
        pass

    try:
        # Agent count
        row = await db._fetchone("SELECT COUNT(*) AS cnt FROM agents")
        metrics["agent_count"] = int(row["cnt"]) if row else 0
    except Exception:
        pass

    try:
        # Active services
        row = await db._fetchone(
            "SELECT COUNT(*) AS cnt FROM agent_services WHERE status='active'")
        metrics["services_active"] = int(row["cnt"]) if row else 0
    except Exception:
        pass

    try:
        # Volume 24h
        cutoff_24h = int(time.time()) - 86400
        row = await db._fetchone(
            "SELECT COALESCE(SUM(amount_usdc), 0) AS vol FROM transactions WHERE created_at >= ?",
            (cutoff_24h,))
        metrics["volume_24h"] = round(float(row["vol"]), 2) if row else 0.0
    except Exception:
        pass

    try:
        # Volume 7d
        cutoff_7d = int(time.time()) - 7 * 86400
        row = await db._fetchone(
            "SELECT COALESCE(SUM(amount_usdc), 0) AS vol FROM transactions WHERE created_at >= ?",
            (cutoff_7d,))
        metrics["volume_7d"] = round(float(row["vol"]), 2) if row else 0.0
    except Exception:
        pass

    _cache_set(cache_key, metrics, ttl=15)
    return metrics


# ── FastAPI Router Endpoints ──

# We need access to the DB singleton — imported at route call time
def _get_db():
    from database import db
    return db


@router.get("/volume")
async def api_volume(period: str = Query("7d", description="Period: 7d, 24h, 30d"),
                     granularity: str = Query("1d", description="Granularity: 1h, 6h, 1d")):
    """Volume timeseries for the given period and granularity."""
    db = _get_db()
    period_h = _parse_period(period)
    gran_h = _parse_granularity(granularity)
    data = await get_volume_timeseries(db, period_hours=period_h, granularity_hours=gran_h)
    return {"period": period, "granularity": granularity, "buckets": data}


@router.get("/top-agents")
async def api_top_agents(limit: int = Query(20, ge=1, le=100),
                          period: str = Query("30d", description="Period: 7d, 30d, 90d")):
    """Top agents by volume."""
    db = _get_db()
    period_d = _parse_period(period) // 24
    data = await get_top_agents(db, limit=limit, period_days=period_d)
    return {"period": period, "limit": limit, "agents": data}


@router.get("/revenue")
async def api_revenue(period: str = Query("30d", description="Period: 7d, 30d, 90d")):
    """Revenue breakdown by service type and chain."""
    db = _get_db()
    period_d = _parse_period(period) // 24
    data = await get_revenue_breakdown(db, period_days=period_d)
    return {"period": period, **data}


@router.get("/services")
async def api_services(limit: int = Query(10, ge=1, le=50)):
    """Most popular services by purchase count."""
    db = _get_db()
    data = await get_service_popularity(db, limit=limit)
    return {"limit": limit, "services": data}


@router.get("/realtime")
async def api_realtime():
    """Real-time platform metrics."""
    db = _get_db()
    data = await get_realtime_metrics(db)
    return data
