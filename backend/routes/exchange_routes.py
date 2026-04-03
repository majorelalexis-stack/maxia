"""MAXIA V12 — Exchange, stocks, bridge, and pricing routes"""
import logging
import time
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException, Depends, Request
from core.auth import require_auth
from core.error_utils import safe_error

logger = logging.getLogger(__name__)
router = APIRouter()


# ═══════════════════════════════════════════════════════════
#  EXCHANGE (Art.6)
# ═══════════════════════════════════════════════════════════

@router.get("/api/exchange/tokens")
async def get_tokens():
    from core.database import db
    return await db.get_tokens()


@router.post("/api/exchange/tokens")
async def list_token_api(req: dict, wallet: str = Depends(require_auth)):
    from core.database import db
    t = {
        "mint": req.get("mint"), "symbol": req.get("symbol"),
        "name": req.get("name"), "decimals": req.get("decimals", 9),
        "price": req.get("initial_price", 0), "creator": wallet,
    }
    await db.save_token(t)
    return t


@router.get("/api/exchange/orders")
async def get_orders(mint: str):
    from core.database import db
    return await db.get_open_orders(mint)


@router.get("/api/agents/{wallet}/stats")
async def agent_stats(wallet: str):
    from core.database import db
    from core.config import get_commission_bps
    try:
        volume = await db.get_agent_volume_30d(wallet)
    except Exception:
        volume = 0.0
    bps = get_commission_bps(volume)
    tiers = [{"name": "WHALE", "min": 5000}, {"name": "GOLD", "min": 500}, {"name": "BRONZE", "min": 0}]
    tier = next((t["name"] for t in tiers if volume >= t["min"]), "BRONZE")
    return {"wallet": wallet, "volume30d": volume, "commissionBps": bps, "tier": tier}


@router.get("/api/agents/{wallet}/portfolio-stats")
async def agent_portfolio_stats(wallet: str):
    """Retourne les stats portfolio: swaps, volume 30j, tier, fees saved, activite recente, badges."""
    from core.database import db
    from core.config import get_commission_bps
    try:
        swap_count = await db.get_swap_count(wallet)
    except Exception:
        swap_count = 0
    try:
        volume = await db.get_swap_volume_30d(wallet)
    except Exception:
        try:
            volume = await db.get_agent_volume_30d(wallet)
        except Exception:
            volume = 0.0
    bps = get_commission_bps(volume)
    tiers = [{"name": "WHALE", "min": 5000, "bps": 1}, {"name": "GOLD", "min": 500, "bps": 3},
             {"name": "SILVER", "min": 100, "bps": 5}, {"name": "BRONZE", "min": 0, "bps": 10}]
    tier = next((t["name"] for t in tiers if volume >= t["min"]), "BRONZE")
    tier_bps = next((t["bps"] for t in tiers if volume >= t["min"]), 10)
    # Fees saved vs baseline 0.10%
    baseline_bps = 10
    fees_saved = volume * (baseline_bps - tier_bps) / 10000

    # Recent activity (last 20 transactions for this wallet)
    activity = []
    try:
        rows = await db.raw_execute_fetchall(
            "SELECT tx_signature, amount_usdc, purpose, created_at FROM transactions "
            "WHERE wallet = ? ORDER BY created_at DESC LIMIT 20", (wallet,))
        for r in rows:
            activity.append({
                "tx": r["tx_signature"] if isinstance(r, dict) else r[0],
                "amount": r["amount_usdc"] if isinstance(r, dict) else r[1],
                "purpose": r["purpose"] if isinstance(r, dict) else r[2],
                "date": r["created_at"] if isinstance(r, dict) else r[3],
            })
    except Exception:
        pass

    # Badges
    badges = []
    try:
        badge_rows = await db.raw_execute_fetchall(
            "SELECT badge_name, badge_icon, earned_at FROM badges WHERE agent_id = ? ORDER BY earned_at DESC",
            (wallet,))
        for r in badge_rows:
            name = r["badge_name"] if isinstance(r, dict) else r[0]
            icon = r["badge_icon"] if isinstance(r, dict) else r[1]
            earned = r["earned_at"] if isinstance(r, dict) else r[2]
            badges.append({"name": name, "icon": icon, "earned_at": earned})
    except Exception:
        pass

    return {
        "wallet": wallet,
        "swap_count": swap_count,
        "volume30d": volume,
        "tier": tier,
        "commission_bps": tier_bps,
        "fees_saved": round(fees_saved, 2),
        "activity": activity,
        "badges": badges,
    }


# ═══════════════════════════════════════════════════════════
#  V11: DYNAMIC PRICING (Art.16)
# ═══════════════════════════════════════════════════════════

@router.get("/api/pricing/status")
async def pricing_status(request: Request):
    """Pricing strategy status. Admin only."""
    from core.security import require_admin
    require_admin(request)
    from infra.dynamic_pricing import get_pricing_status
    return get_pricing_status()

@router.post("/api/pricing/adjust")
async def pricing_force_adjust(request: Request):
    """Force un ajustement du pricing. Admin only."""
    from core.security import require_admin
    require_admin(request)
    from infra.dynamic_pricing import adjust_market_fees
    from core.database import db
    result = await adjust_market_fees(db)
    return result


# /api/bridge/* — already served by bridge_service router

# ══════════════════════════════════════════════════════════
#  V11: BOURSE ACTIONS TOKENISEES (Art.23)
# ══════════════════════════════════════════════════════════

@router.get("/api/stocks/stats")
async def stock_exchange_stats():
    from trading.tokenized_stocks import stock_exchange
    return stock_exchange.get_stats()


@router.get("/api/stocks/market-status")
async def stock_market_status():
    """Reference info: US stock market hours (for price feed context only). MAXIA tokenized stocks trade 24/7 on-chain."""
    now_utc = datetime.now(timezone.utc)
    et_offset = timedelta(hours=-5)  # EST (simplified — DST would be -4)
    now_et = now_utc + et_offset
    # Check DST (Mar-Nov): second Sunday Mar to first Sunday Nov
    month = now_et.month
    if 3 < month < 11:
        et_offset = timedelta(hours=-4)
        now_et = now_utc + et_offset
    elif month == 3:
        # Second Sunday of March
        second_sunday = 14 - (datetime(now_et.year, 3, 1).weekday() + 1) % 7
        if now_et.day >= second_sunday:
            et_offset = timedelta(hours=-4)
            now_et = now_utc + et_offset
    elif month == 11:
        first_sunday = 7 - (datetime(now_et.year, 11, 1).weekday() + 1) % 7
        if now_et.day < first_sunday:
            et_offset = timedelta(hours=-4)
            now_et = now_utc + et_offset

    weekday = now_et.weekday()  # 0=Monday, 6=Sunday
    hour = now_et.hour
    minute = now_et.minute
    time_minutes = hour * 60 + minute  # minutes since midnight

    is_weekday = weekday < 5
    market_open_min = 9 * 60 + 30   # 9:30 AM ET
    market_close_min = 16 * 60       # 4:00 PM ET
    pre_market_open = 4 * 60         # 4:00 AM ET
    after_hours_close = 20 * 60      # 8:00 PM ET

    if not is_weekday:
        status = "closed"
        session = "weekend"
    elif pre_market_open <= time_minutes < market_open_min:
        status = "pre_market"
        session = "Pre-Market (4:00 AM - 9:30 AM ET)"
    elif market_open_min <= time_minutes < market_close_min:
        status = "open"
        session = "Regular Trading (9:30 AM - 4:00 PM ET)"
    elif market_close_min <= time_minutes < after_hours_close:
        status = "after_hours"
        session = "After-Hours (4:00 PM - 8:00 PM ET)"
    else:
        status = "closed"
        session = "Closed"

    # Next open time
    if status in ("open", "pre_market", "after_hours"):
        next_open = "Now (or next regular session at 9:30 AM ET)"
    elif weekday == 4 and time_minutes >= after_hours_close:
        next_open = "Monday 9:30 AM ET"
    elif weekday >= 5:
        days_until_monday = (7 - weekday) % 7
        if days_until_monday == 0:
            days_until_monday = 1
        next_open = f"Monday 9:30 AM ET ({days_until_monday} day{'s' if days_until_monday > 1 else ''})"
    else:
        next_open = "Today 9:30 AM ET" if time_minutes < market_open_min else "Tomorrow 9:30 AM ET"

    return {
        "maxia_status": "open_24_7",
        "maxia_note": "MAXIA tokenized stocks trade 24/7 — they are on-chain tokens, not traditional equities.",
        "nyse_status": status,
        "nyse_session": session,
        "current_time_et": now_et.strftime("%Y-%m-%d %H:%M ET"),
        "nyse_next_open": next_open,
        "note": "Tokenized stocks are synthetic on-chain assets (NOT traditional equities). Trade 24/7. Prices reference the underlying stock via oracle. Off-hours = wider oracle spreads.",
        "providers": {
            "xStocks_Backed": {"chain": "Solana", "stocks": 11},
            "Ondo_GM": {"chain": "Ethereum", "stocks": 2},
            "Dinari_dShares": {"chain": "Arbitrum", "stocks": 12},
        },
    }


@router.get("/api/public/tokens/candidates")
async def token_candidates():
    """Auto-listing: discover trending tokens with volume > $100K on supported chains."""
    from features.token_autolisting import get_listing_candidates
    return await get_listing_candidates()
