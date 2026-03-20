"""MAXIA Art.2 V11 — Dynamic Pricing (Yield Management)"""
import time
from config import (
    DYNAMIC_PRICING_ENABLED, DYNAMIC_PRICING_MIN_BPS,
    DYNAMIC_PRICING_MAX_BPS, DYNAMIC_PRICING_VOLUME_THRESH,
    COMMISSION_TIERS,
)

# Etat du pricing dynamique
_current_adjustment_bps = 0
_last_volume_24h = 0.0
_last_check = 0


async def adjust_market_fees(db) -> dict:
    """
    Ajuste les commissions en temps reel selon le volume 24h.
    - Volume +20% -> commission +1% (attirer profits)
    - Volume -20% -> commission vers Elite 2% (attirer flux)
    Borne entre DYNAMIC_PRICING_MIN_BPS et DYNAMIC_PRICING_MAX_BPS.
    """
    global _current_adjustment_bps, _last_volume_24h, _last_check

    if not DYNAMIC_PRICING_ENABLED:
        return {"enabled": False}

    now = time.time()
    if now - _last_check < 300:  # Check toutes les 5 min max
        return get_pricing_status()
    _last_check = now

    try:
        stats = await db.get_stats()
        current_vol = stats.get("volume_24h", 0)
    except Exception:
        return {"enabled": True, "error": "DB unavailable"}

    if _last_volume_24h > 0:
        change_pct = ((current_vol - _last_volume_24h) / _last_volume_24h) * 100

        if change_pct > DYNAMIC_PRICING_VOLUME_THRESH:
            # Volume monte -> augmenter les commissions (plus de marge)
            _current_adjustment_bps = min(
                _current_adjustment_bps + 10,
                DYNAMIC_PRICING_MAX_BPS,
            )
            print(f"[DynamicPricing] Volume +{change_pct:.0f}% -> commission +10 BPS (total adj: {_current_adjustment_bps})")

        elif change_pct < -DYNAMIC_PRICING_VOLUME_THRESH:
            # Volume baisse -> baisser les commissions (attirer du flux)
            _current_adjustment_bps = max(
                _current_adjustment_bps - 10,
                -DYNAMIC_PRICING_MAX_BPS,
            )
            print(f"[DynamicPricing] Volume {change_pct:.0f}% -> commission -10 BPS (total adj: {_current_adjustment_bps})")

    _last_volume_24h = current_vol

    # Appliquer les ajustements aux tiers
    _apply_adjustments()

    return get_pricing_status()


def _apply_adjustments():
    """Applique l'ajustement aux COMMISSION_TIERS (in-place)."""
    base_rates = [500, 100, 10]  # BRONZE, GOLD, WHALE (valeurs de base)
    for i, tier in enumerate(COMMISSION_TIERS):
        if i >= len(base_rates):
            break
        adjusted = base_rates[i] + _current_adjustment_bps
        adjusted = max(DYNAMIC_PRICING_MIN_BPS, min(DYNAMIC_PRICING_MAX_BPS, adjusted))
        tier["rate_bps"] = adjusted


def get_pricing_status() -> dict:
    return {
        "enabled": DYNAMIC_PRICING_ENABLED,
        "adjustment_bps": _current_adjustment_bps,
        "last_volume_24h": _last_volume_24h,
        "current_tiers": [
            {"name": t["name"], "rate_bps": t["rate_bps"]}
            for t in COMMISSION_TIERS
        ],
        "bounds": {
            "min_bps": DYNAMIC_PRICING_MIN_BPS,
            "max_bps": DYNAMIC_PRICING_MAX_BPS,
            "volume_threshold_pct": DYNAMIC_PRICING_VOLUME_THRESH,
        },
    }
