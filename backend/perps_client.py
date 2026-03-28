"""MAXIA — Jupiter Perpetuals Integration (read-only)

Fournit des donnees de marche perps (SOL-PERP, ETH-PERP, BTC-PERP)
via Jupiter Perps API + Pyth oracle pour les mark prices.
Aucune execution de trade — simulation et quotes uniquement.
"""
import logging
import time
from collections import defaultdict
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from http_client import get_http_client

from error_utils import safe_error

logger = logging.getLogger("perps_client")

router = APIRouter(prefix="/api/perps", tags=["Perpetuals"])

# ── Rate limiting (20 req/min par IP) ──
_rate_store: dict[str, list[float]] = defaultdict(list)
_RATE_LIMIT = 20
_RATE_WINDOW = 60.0

# ── Cache ──
_cache: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 10  # 10s

# ── Jupiter Perps API ──
_JUP_PERPS_STATS = "https://perps-api.jup.ag/v1/stats"
_JUP_PERPS_POSITIONS = "https://perps-api.jup.ag/v1/positions"

# ── Marches supportes ──
_MARKETS = {
    "SOL-PERP": {"base": "SOL", "max_leverage": 100},
    "ETH-PERP": {"base": "ETH", "max_leverage": 100},
    "BTC-PERP": {"base": "BTC", "max_leverage": 100},
}

# ── Frais (bps) ──
_OPEN_FEE_BPS = 6       # 0.06%
_CLOSE_FEE_BPS = 6      # 0.06%
_BORROW_RATE_HOURLY = 0.01  # 0.01%/h base


def _check_rate(request: Request) -> None:
    """Verifie le rate limit en memoire (20 req/min par IP)."""
    from security import get_real_ip
    ip = get_real_ip(request)
    now = time.time()
    cutoff = now - _RATE_WINDOW
    _rate_store[ip] = [t for t in _rate_store[ip] if t > cutoff]
    if len(_rate_store[ip]) >= _RATE_LIMIT:
        raise HTTPException(429, "Rate limit depasse (20 req/min). Reessayez dans 1 minute.")
    _rate_store[ip].append(now)


def _get_cache(key: str) -> Optional[dict]:
    """Retourne la valeur cachee si non expiree."""
    entry = _cache.get(key)
    if entry and time.time() - entry[0] < _CACHE_TTL:
        return entry[1]
    return None


def _set_cache(key: str, value: dict) -> None:
    """Stocke une valeur en cache."""
    _cache[key] = (time.time(), value)


async def _fetch_pyth_price(symbol: str) -> float:
    """Recupere le mark price via pyth_oracle (import lazy)."""
    from pyth_oracle import get_crypto_price
    result = await get_crypto_price(symbol)
    price = result.get("price", 0)
    return float(price) if price else 0.0


async def _fetch_jup_stats() -> dict:
    """Tente de recuperer les stats Jupiter Perps. Retourne {} si indisponible."""
    try:
        client = get_http_client()
        resp = await client.get(_JUP_PERPS_STATS, timeout=10)
        if resp.status_code == 200:
            return resp.json()
    except Exception as exc:
        logger.warning(f"Jupiter Perps stats unavailable: {exc}")
    return {}


async def _build_market_data(symbol: str, jup_stats: dict) -> dict:
    """Construit les donnees de marche pour un symbole, avec fallback Pyth."""
    mark_price = await _fetch_pyth_price(symbol)

    # Tenter d'extraire les donnees Jupiter
    jup_market = {}
    if jup_stats:
        # Jupiter renvoie les stats par pool/market — chercher le bon
        pools = jup_stats if isinstance(jup_stats, list) else jup_stats.get("pools", [])
        if isinstance(pools, list):
            for pool in pools:
                name = pool.get("name", "") or pool.get("symbol", "")
                if symbol.upper() in name.upper():
                    jup_market = pool
                    break

    funding_rate = float(jup_market.get("fundingRate", 0)) if jup_market else 0.0
    open_interest_long = float(jup_market.get("openInterestLong", 0)) if jup_market else 0.0
    open_interest_short = float(jup_market.get("openInterestShort", 0)) if jup_market else 0.0

    return {
        "market": f"{symbol}-PERP",
        "base_asset": symbol,
        "mark_price": mark_price,
        "funding_rate_hourly": funding_rate,
        "open_interest_long_usd": open_interest_long,
        "open_interest_short_usd": open_interest_short,
        "max_leverage": _MARKETS[f"{symbol}-PERP"]["max_leverage"],
        "fees": {
            "open_bps": _OPEN_FEE_BPS,
            "close_bps": _CLOSE_FEE_BPS,
            "borrow_rate_hourly_pct": _BORROW_RATE_HOURLY,
        },
        "source": "jupiter+pyth" if jup_market else "pyth_synthetic",
    }


def _simulate_position(
    mark_price: float, side: str, leverage: float, collateral_usd: float
) -> dict:
    """Simule une position perp (entry, liq price, fees, PnL +-5%)."""
    if mark_price <= 0:
        return {"error": "Mark price unavailable"}

    position_size_usd = collateral_usd * leverage
    open_fee_usd = position_size_usd * (_OPEN_FEE_BPS / 10000)
    close_fee_usd = position_size_usd * (_CLOSE_FEE_BPS / 10000)
    total_fees_usd = open_fee_usd + close_fee_usd

    is_long = side.lower() == "long"
    # Prix de liquidation: quand la perte = collateral - fees
    margin_after_fees = collateral_usd - open_fee_usd
    price_move_to_liq = margin_after_fees / (position_size_usd / mark_price)
    if is_long:
        liq_price = mark_price - price_move_to_liq
    else:
        liq_price = mark_price + price_move_to_liq

    # PnL a +-5%
    price_up_5 = mark_price * 1.05
    price_down_5 = mark_price * 0.95
    qty = position_size_usd / mark_price

    if is_long:
        pnl_up_5 = (price_up_5 - mark_price) * qty - total_fees_usd
        pnl_down_5 = (price_down_5 - mark_price) * qty - total_fees_usd
    else:
        pnl_up_5 = (mark_price - price_up_5) * qty - total_fees_usd
        pnl_down_5 = (mark_price - price_down_5) * qty - total_fees_usd

    return {
        "entry_price": round(mark_price, 4),
        "side": side.lower(),
        "leverage": leverage,
        "collateral_usd": collateral_usd,
        "position_size_usd": round(position_size_usd, 2),
        "liquidation_price": round(max(0, liq_price), 4),
        "fees": {
            "open_usd": round(open_fee_usd, 4),
            "close_usd": round(close_fee_usd, 4),
            "total_usd": round(total_fees_usd, 4),
        },
        "pnl_scenarios": {
            "price_plus_5pct": {
                "price": round(price_up_5, 4),
                "pnl_usd": round(pnl_up_5, 2),
                "roi_pct": round(pnl_up_5 / collateral_usd * 100, 2),
            },
            "price_minus_5pct": {
                "price": round(price_down_5, 4),
                "pnl_usd": round(pnl_down_5, 2),
                "roi_pct": round(pnl_down_5 / collateral_usd * 100, 2),
            },
        },
    }


# ══════════════════════════════════════════
# Routes FastAPI
# ══════════════════════════════════════════

@router.get("/markets")
async def get_markets(request: Request):
    """Liste les marches perps disponibles avec mark price, funding rate, OI."""
    _check_rate(request)
    cached = _get_cache("markets")
    if cached:
        return cached

    try:
        jup_stats = await _fetch_jup_stats()
        markets = []
        for market_name, cfg in _MARKETS.items():
            data = await _build_market_data(cfg["base"], jup_stats)
            markets.append(data)

        result = {"markets": markets, "count": len(markets), "timestamp": int(time.time())}
        _set_cache("markets", result)
        return result
    except Exception as exc:
        raise HTTPException(500, detail=safe_error(exc, "perps_markets"))


@router.get("/quote")
async def get_quote(
    request: Request,
    asset: str = Query(..., description="Asset (SOL, ETH, BTC)"),
    side: str = Query(..., description="long or short"),
    leverage: float = Query(..., gt=0, le=100, description="Leverage (1-100x)"),
    collateral: float = Query(..., gt=0, le=1_000_000, description="Collateral in USD"),
):
    """Simule une position perp (entry, liq price, fees, PnL a +-5%)."""
    _check_rate(request)

    asset_upper = asset.upper()
    market_key = f"{asset_upper}-PERP"
    if market_key not in _MARKETS:
        raise HTTPException(400, f"Asset non supporte: {asset}. Disponibles: {list(_MARKETS.keys())}")

    side_lower = side.lower()
    if side_lower not in ("long", "short"):
        raise HTTPException(400, "side doit etre 'long' ou 'short'")

    try:
        mark_price = await _fetch_pyth_price(asset_upper)
        if mark_price <= 0:
            raise HTTPException(503, f"Mark price indisponible pour {asset_upper}")

        sim = _simulate_position(mark_price, side_lower, leverage, collateral)
        return {
            "market": market_key,
            "quote": sim,
            "source": "pyth_oracle",
            "timestamp": int(time.time()),
            "note": "Simulation uniquement — pas d'execution de trade",
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, detail=safe_error(exc, "perps_quote"))


@router.get("/funding-rates")
async def get_funding_rates(request: Request):
    """Retourne les funding rates actuels pour tous les marches."""
    _check_rate(request)
    cached = _get_cache("funding_rates")
    if cached:
        return cached

    try:
        jup_stats = await _fetch_jup_stats()
        rates = []
        for market_name, cfg in _MARKETS.items():
            data = await _build_market_data(cfg["base"], jup_stats)
            rates.append({
                "market": market_name,
                "funding_rate_hourly": data["funding_rate_hourly"],
                "mark_price": data["mark_price"],
                "source": data["source"],
            })

        result = {"funding_rates": rates, "timestamp": int(time.time())}
        _set_cache("funding_rates", result)
        return result
    except Exception as exc:
        raise HTTPException(500, detail=safe_error(exc, "perps_funding"))


print(f"[Perps] Initialise — {len(_MARKETS)} marches (read-only, Jupiter + Pyth)")
