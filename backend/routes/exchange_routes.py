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
@router.get("/api/public/tokens/candidates")
async def token_candidates():
    """Auto-listing: discover trending tokens with volume > $100K on supported chains."""
    from features.token_autolisting import get_listing_candidates
    return await get_listing_candidates()
