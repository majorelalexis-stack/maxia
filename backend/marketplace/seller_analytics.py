"""MAXIA V12 — Seller Analytics Dashboard API (ONE-36).

Provides analytics endpoints for service sellers: revenue trends,
per-service metrics, client analysis, and CSV export.
All endpoints require X-API-Key header (seller auth).
"""
import logging
import time
import csv
import datetime
import io
import re
from typing import Optional

from fastapi import APIRouter, HTTPException, Header
from fastapi.responses import StreamingResponse

logger = logging.getLogger("maxia.seller_analytics")
router = APIRouter(prefix="/api/seller/analytics", tags=["seller-analytics"])


async def _get_db():
    from core.database import db
    return db


async def _get_seller(api_key: str) -> dict:
    """Validate API key and return agent info. Raises 401/404."""
    if not api_key:
        raise HTTPException(401, "X-API-Key required")
    db = await _get_db()
    rows = await db.raw_execute_fetchall(
        "SELECT name, wallet, tier, total_earned, total_spent, "
        "volume_30d, services_listed, created_at "
        "FROM agents WHERE api_key=?", (api_key,))
    if not rows:
        raise HTTPException(401, "Invalid API key")
    return dict(rows[0])


# ══════════════════════════════════════════
#  OVERVIEW — KPIs snapshot
# ══════════════════════════════════════════

@router.get("/overview")
async def analytics_overview(x_api_key: str = Header(None, alias="X-API-Key")):
    """Seller KPI overview: revenue, sales, rating, active services, top client."""
    seller = await _get_seller(x_api_key)
    db = await _get_db()
    name = seller["name"]

    # Transaction stats
    tx_rows = await db.raw_execute_fetchall(
        "SELECT COUNT(*) as cnt, COALESCE(SUM(seller_gets_usdc), 0) as revenue, "
        "COALESCE(SUM(commission_usdc), 0) as fees "
        "FROM marketplace_tx WHERE seller=?", (name,))
    tx = dict(tx_rows[0]) if tx_rows else {"cnt": 0, "revenue": 0, "fees": 0}

    # 30-day revenue
    ts_30d = int(time.time()) - 30 * 86400
    rev30_rows = await db.raw_execute_fetchall(
        "SELECT COUNT(*) as cnt, COALESCE(SUM(seller_gets_usdc), 0) as revenue "
        "FROM marketplace_tx WHERE seller=? AND created_at>=?", (name, ts_30d))
    rev30 = dict(rev30_rows[0]) if rev30_rows else {"cnt": 0, "revenue": 0}

    # 7-day revenue
    ts_7d = int(time.time()) - 7 * 86400
    rev7_rows = await db.raw_execute_fetchall(
        "SELECT COUNT(*) as cnt, COALESCE(SUM(seller_gets_usdc), 0) as revenue "
        "FROM marketplace_tx WHERE seller=? AND created_at>=?", (name, ts_7d))
    rev7 = dict(rev7_rows[0]) if rev7_rows else {"cnt": 0, "revenue": 0}

    # Active services
    svc_rows = await db.raw_execute_fetchall(
        "SELECT COUNT(*) as cnt FROM agent_services "
        "WHERE agent_api_key=? AND status='active'", (x_api_key,))
    active_services = dict(svc_rows[0]).get("cnt", 0) if svc_rows else 0

    # Average rating across services
    rating_rows = await db.raw_execute_fetchall(
        "SELECT AVG(rating) as avg_rating, SUM(rating_count) as total_reviews "
        "FROM agent_services WHERE agent_api_key=? AND status='active'", (x_api_key,))
    rating = dict(rating_rows[0]) if rating_rows else {}
    avg_rating = round(rating.get("avg_rating") or 0, 2)
    total_reviews = rating.get("total_reviews") or 0

    # Top client (most purchases)
    top_client_rows = await db.raw_execute_fetchall(
        "SELECT buyer, COUNT(*) as purchases, SUM(price_usdc) as total_spent "
        "FROM marketplace_tx WHERE seller=? "
        "GROUP BY buyer ORDER BY purchases DESC LIMIT 1", (name,))
    top_client = None
    if top_client_rows:
        tc = dict(top_client_rows[0])
        top_client = {"name": tc["buyer"], "purchases": tc["purchases"],
                      "total_spent": round(tc.get("total_spent") or 0, 2)}

    return {
        "seller": name,
        "tier": seller.get("tier", "BRONZE"),
        "kpis": {
            "total_revenue": round(tx.get("revenue") or 0, 2),
            "total_sales": tx.get("cnt") or 0,
            "total_fees_paid": round(tx.get("fees") or 0, 2),
            "revenue_30d": round(rev30.get("revenue") or 0, 2),
            "sales_30d": rev30.get("cnt") or 0,
            "revenue_7d": round(rev7.get("revenue") or 0, 2),
            "sales_7d": rev7.get("cnt") or 0,
            "active_services": active_services,
            "avg_rating": avg_rating,
            "total_reviews": total_reviews,
        },
        "top_client": top_client,
    }


# ══════════════════════════════════════════
#  REVENUE — Time-series revenue data
# ══════════════════════════════════════════

@router.get("/revenue")
async def analytics_revenue(
    x_api_key: str = Header(None, alias="X-API-Key"),
    period: str = "daily",
    days: int = 30,
):
    """Revenue time-series: daily, weekly, or monthly buckets.

    - period: daily | weekly | monthly
    - days: lookback window (max 365)
    """
    if period not in ("daily", "weekly", "monthly"):
        raise HTTPException(422, "period must be daily, weekly, or monthly")
    seller = await _get_seller(x_api_key)
    days = max(1, min(365, days))
    ts_start = int(time.time()) - days * 86400
    db = await _get_db()

    rows = await db.raw_execute_fetchall(
        "SELECT created_at, price_usdc, seller_gets_usdc, commission_usdc, service "
        "FROM marketplace_tx WHERE seller=? AND created_at>=? "
        "ORDER BY created_at", (seller["name"], ts_start))

    # Period → strftime format
    _fmt = {"daily": "%Y-%m-%d", "weekly": "%Y-W%W", "monthly": "%Y-%m"}[period]

    # Bucket transactions
    buckets: dict[str, dict] = {}
    for row in rows:
        r = dict(row)
        ts = r.get("created_at") or 0
        dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
        key = dt.strftime(_fmt)

        if key not in buckets:
            buckets[key] = {"period": key, "revenue": 0, "sales": 0, "fees": 0}
        buckets[key]["revenue"] += r.get("seller_gets_usdc") or 0
        buckets[key]["sales"] += 1
        buckets[key]["fees"] += r.get("commission_usdc") or 0

    # Round values
    series = []
    for b in sorted(buckets.values(), key=lambda x: x["period"]):
        b["revenue"] = round(b["revenue"], 2)
        b["fees"] = round(b["fees"], 2)
        series.append(b)

    total_rev = sum(b["revenue"] for b in series)
    total_sales = sum(b["sales"] for b in series)

    return {
        "seller": seller["name"],
        "period": period,
        "days": days,
        "total_revenue": round(total_rev, 2),
        "total_sales": total_sales,
        "series": series,
    }


# ══════════════════════════════════════════
#  SERVICES — Per-service analytics
# ══════════════════════════════════════════

@router.get("/services")
async def analytics_services(x_api_key: str = Header(None, alias="X-API-Key")):
    """Per-service analytics: revenue, sales, rating, conversion for each service."""
    seller = await _get_seller(x_api_key)
    db = await _get_db()

    # Get services
    svc_rows = await db.raw_execute_fetchall(
        "SELECT id, name, price_usdc, rating, rating_count, sales, type, status, listed_at "
        "FROM agent_services WHERE agent_api_key=?", (x_api_key,))
    services = [dict(r) for r in svc_rows]

    # Aggregate all service transaction data in one query
    agg_rows = await db.raw_execute_fetchall(
        "SELECT service, COUNT(*) as cnt, COALESCE(SUM(seller_gets_usdc), 0) as revenue "
        "FROM marketplace_tx WHERE seller=? GROUP BY service",
        (seller["name"],))
    agg = {dict(r)["service"]: dict(r) for r in agg_rows}

    now = int(time.time())
    result = []
    for svc in services:
        tx = agg.get(svc["name"], {"cnt": 0, "revenue": 0})
        listed_at = svc.get("listed_at") or 0
        days_listed = (now - listed_at) // 86400 if listed_at else 0

        result.append({
            "id": svc["id"],
            "name": svc["name"],
            "price_usdc": svc.get("price_usdc", 0),
            "status": svc.get("status", "active"),
            "type": svc.get("type", ""),
            "total_sales": tx.get("cnt") or 0,
            "total_revenue": round(tx.get("revenue") or 0, 2),
            "rating": svc.get("rating", 0),
            "rating_count": svc.get("rating_count", 0),
            "days_listed": days_listed,
            "avg_daily_sales": round((tx.get("cnt") or 0) / max(days_listed, 1), 2),
        })

    # Sort by revenue descending
    result.sort(key=lambda x: x["total_revenue"], reverse=True)

    return {
        "seller": seller["name"],
        "services": result,
        "total_services": len(result),
        "active_services": sum(1 for s in result if s["status"] == "active"),
    }


# ══════════════════════════════════════════
#  CLIENTS — Client analysis
# ══════════════════════════════════════════

@router.get("/clients")
async def analytics_clients(
    x_api_key: str = Header(None, alias="X-API-Key"),
    limit: int = 20,
):
    """Client analysis: top buyers, repeat rate, avg spend."""
    seller = await _get_seller(x_api_key)
    limit = max(1, min(100, limit))
    db = await _get_db()

    # Aggregate by buyer
    client_rows = await db.raw_execute_fetchall(
        "SELECT buyer, COUNT(*) as purchases, "
        "SUM(price_usdc) as total_spent, "
        "MIN(created_at) as first_purchase, "
        "MAX(created_at) as last_purchase "
        "FROM marketplace_tx WHERE seller=? "
        "GROUP BY buyer ORDER BY total_spent DESC LIMIT ?",
        (seller["name"], limit))

    clients = []
    for row in client_rows:
        c = dict(row)
        clients.append({
            "name": c["buyer"],
            "purchases": c["purchases"],
            "total_spent": round(c.get("total_spent") or 0, 2),
            "first_purchase": c.get("first_purchase"),
            "last_purchase": c.get("last_purchase"),
            "is_repeat": (c["purchases"] or 0) > 1,
        })

    # Aggregate stats over ALL clients (not just paged top-N)
    all_stats_rows = await db.raw_execute_fetchall(
        "SELECT COUNT(DISTINCT buyer) as total, "
        "SUM(CASE WHEN cnt > 1 THEN 1 ELSE 0 END) as repeats, "
        "AVG(total_spent) as avg_spend FROM ("
        "  SELECT buyer, COUNT(*) as cnt, SUM(price_usdc) as total_spent "
        "  FROM marketplace_tx WHERE seller=? GROUP BY buyer"
        ")", (seller["name"],))
    agg = dict(all_stats_rows[0]) if all_stats_rows else {}
    total_clients_all = agg.get("total") or 0
    repeat_clients_all = agg.get("repeats") or 0

    return {
        "seller": seller["name"],
        "clients": clients,
        "total_clients": total_clients_all,
        "repeat_clients": repeat_clients_all,
        "repeat_rate": round(repeat_clients_all / max(total_clients_all, 1) * 100, 1),
        "avg_spend": round(agg.get("avg_spend") or 0, 2),
    }


# ══════════════════════════════════════════
#  EXPORT — CSV download
# ══════════════════════════════════════════

@router.get("/export")
async def analytics_export(
    x_api_key: str = Header(None, alias="X-API-Key"),
    days: int = 90,
):
    """Export seller transaction history as CSV. Max 365 days."""
    seller = await _get_seller(x_api_key)
    days = max(1, min(365, days))
    ts_start = int(time.time()) - days * 86400
    db = await _get_db()

    rows = await db.raw_execute_fetchall(
        "SELECT tx_id, buyer, service, price_usdc, commission_usdc, "
        "seller_gets_usdc, created_at "
        "FROM marketplace_tx WHERE seller=? AND created_at>=? "
        "ORDER BY created_at DESC", (seller["name"], ts_start))

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["tx_id", "buyer", "service", "price_usdc",
                     "commission_usdc", "seller_gets_usdc", "timestamp"])
    for row in rows:
        r = dict(row)
        writer.writerow([
            r.get("tx_id", ""),
            r.get("buyer", ""),
            r.get("service", ""),
            r.get("price_usdc", 0),
            r.get("commission_usdc", 0),
            r.get("seller_gets_usdc", 0),
            r.get("created_at", 0),
        ])

    output.seek(0)
    safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', seller['name'])[:50]
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=maxia_sales_{safe_name}_{days}d.csv"},
    )


def get_router():
    return router
