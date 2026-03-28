"""MAXIA — Pump.fun Token Launcher (PumpPortal API)

Permet de consulter les tokens trending sur pump.fun,
recuperer les infos d'un token, et preparer des transactions
de lancement (unsigned, a signer par le wallet frontend).
MAXIA ne prend aucune commission — pump.fun prend ses frais.
"""
import logging
import time
from collections import defaultdict
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from error_utils import safe_error

logger = logging.getLogger("token_launcher")

router = APIRouter(prefix="/api/token", tags=["Token Launcher"])

# ── Rate limiting ──
_rate_store: dict[str, list[float]] = defaultdict(list)
_RATE_WINDOW = 60.0

# ── Cache ──
_cache: dict[str, tuple[float, dict]] = {}
_TRENDING_CACHE_TTL = 30   # 30s
_INFO_CACHE_TTL = 15       # 15s

# ── API URLs ──
_PUMPPORTAL_TRADE = "https://pumpportal.fun/api/trade-local"
_PUMP_TRENDING = "https://frontend-api-v3.pump.fun/coins/trending"
_PUMP_COIN_INFO = "https://frontend-api-v3.pump.fun/coins"


class TokenLaunchRequest(BaseModel):
    """Parametres pour preparer un lancement de token."""
    name: str = Field(min_length=1, max_length=32, description="Nom du token")
    symbol: str = Field(min_length=1, max_length=10, description="Ticker (ex: MTK)")
    description: str = Field(max_length=500, default="", description="Description du token")
    initial_buy_sol: float = Field(
        gt=0, le=10.0,
        description="SOL initial a acheter (0.01 - 10 SOL)",
    )


def _check_rate(request: Request, limit: int) -> None:
    """Verifie le rate limit par IP."""
    from security import get_real_ip
    ip = get_real_ip(request)
    now = time.time()
    cutoff = now - _RATE_WINDOW
    _rate_store[ip] = [t for t in _rate_store[ip] if t > cutoff]
    if len(_rate_store[ip]) >= limit:
        raise HTTPException(429, f"Rate limit depasse ({limit} req/min). Reessayez dans 1 minute.")
    _rate_store[ip].append(now)


def _get_cache(key: str, ttl: float) -> Optional[dict]:
    """Retourne la valeur cachee si non expiree."""
    entry = _cache.get(key)
    if entry and time.time() - entry[0] < ttl:
        return entry[1]
    return None


def _set_cache(key: str, value: dict) -> None:
    """Stocke une valeur en cache."""
    _cache[key] = (time.time(), value)


def _validate_symbol(symbol: str) -> str:
    """Valide et normalise un ticker."""
    import re
    cleaned = symbol.strip().upper()
    if not re.match(r"^[A-Z0-9]{1,10}$", cleaned):
        raise HTTPException(400, "Symbol invalide — lettres et chiffres uniquement, max 10 chars")
    return cleaned


def _validate_mint(mint: str) -> str:
    """Valide une adresse mint Solana (base58, 32-44 chars)."""
    import re
    if not re.match(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$", mint):
        raise HTTPException(400, "Adresse mint invalide (base58 attendu)")
    return mint


# ══════════════════════════════════════════
# Routes FastAPI
# ══════════════════════════════════════════

@router.get("/trending")
async def get_trending(request: Request):
    """Tokens trending sur pump.fun (cache 30s)."""
    _check_rate(request, limit=20)

    cached = _get_cache("trending", _TRENDING_CACHE_TTL)
    if cached:
        return cached

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(_PUMP_TRENDING)

        if resp.status_code != 200:
            raise HTTPException(502, "pump.fun API indisponible")

        data = resp.json()
        # Normaliser la reponse
        tokens = []
        items = data if isinstance(data, list) else data.get("coins", data.get("results", []))
        for item in items[:50]:  # Max 50 tokens
            tokens.append({
                "mint": item.get("mint", ""),
                "name": item.get("name", ""),
                "symbol": item.get("symbol", ""),
                "market_cap_sol": item.get("market_cap_sol", 0),
                "market_cap_usd": item.get("usd_market_cap", item.get("market_cap", 0)),
                "reply_count": item.get("reply_count", 0),
                "image_uri": item.get("image_uri", ""),
                "created_timestamp": item.get("created_timestamp", 0),
            })

        result = {
            "tokens": tokens,
            "count": len(tokens),
            "source": "pump.fun",
            "timestamp": int(time.time()),
        }
        _set_cache("trending", result)
        return result

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, detail=safe_error(exc, "token_trending"))


@router.get("/info/{mint}")
async def get_token_info(request: Request, mint: str):
    """Infos detaillees d'un token pump.fun par adresse mint."""
    _check_rate(request, limit=20)
    mint = _validate_mint(mint)

    cache_key = f"info:{mint}"
    cached = _get_cache(cache_key, _INFO_CACHE_TTL)
    if cached:
        return cached

    try:
        url = f"{_PUMP_COIN_INFO}/{mint}"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)

        if resp.status_code == 404:
            raise HTTPException(404, f"Token {mint} non trouve sur pump.fun")
        if resp.status_code != 200:
            raise HTTPException(502, "pump.fun API indisponible")

        data = resp.json()
        result = {
            "mint": data.get("mint", mint),
            "name": data.get("name", ""),
            "symbol": data.get("symbol", ""),
            "description": data.get("description", ""),
            "image_uri": data.get("image_uri", ""),
            "market_cap_sol": data.get("market_cap_sol", 0),
            "market_cap_usd": data.get("usd_market_cap", data.get("market_cap", 0)),
            "virtual_sol_reserves": data.get("virtual_sol_reserves", 0),
            "virtual_token_reserves": data.get("virtual_token_reserves", 0),
            "total_supply": data.get("total_supply", 0),
            "reply_count": data.get("reply_count", 0),
            "creator": data.get("creator", ""),
            "created_timestamp": data.get("created_timestamp", 0),
            "complete": data.get("complete", False),
            "bonding_curve": data.get("bonding_curve", ""),
            "source": "pump.fun",
            "timestamp": int(time.time()),
        }
        _set_cache(cache_key, result)
        return result

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, detail=safe_error(exc, "token_info"))


@router.post("/prepare-launch")
async def prepare_launch(request: Request, body: TokenLaunchRequest):
    """Prepare une transaction de lancement de token via PumpPortal.

    Retourne la transaction serialisee (base64) a signer par le wallet frontend.
    MAXIA ne prend aucune commission — pump.fun prend ses frais natifs.
    """
    _check_rate(request, limit=5)

    # Validation du contenu (Art.1) — raises HTTPException if unsafe
    from security import check_content_safety
    for text in [body.name, body.symbol, body.description]:
        if text:
            check_content_safety(text, field_name="token_launch")

    symbol = _validate_symbol(body.symbol)

    try:
        import base64

        payload = {
            "publicKey": "",  # sera rempli par le frontend avant signature
            "action": "create",
            "tokenMetadata": {
                "name": body.name.strip(),
                "symbol": symbol,
                "description": body.description.strip(),
            },
            "mint": "",  # keypair genere par PumpPortal
            "denominatedInSol": "true",
            "amount": body.initial_buy_sol,
            "slippage": 10,
            "priorityFee": 0.0005,
            "pool": "pump",
        }

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(_PUMPPORTAL_TRADE, json=payload)

        if resp.status_code == 200:
            # PumpPortal retourne la transaction serialisee
            tx_bytes = resp.content
            tx_b64 = base64.b64encode(tx_bytes).decode("utf-8")
            return {
                "success": True,
                "transaction_base64": tx_b64,
                "transaction_size_bytes": len(tx_bytes),
                "token": {
                    "name": body.name.strip(),
                    "symbol": symbol,
                    "initial_buy_sol": body.initial_buy_sol,
                },
                "note": "Transaction non signee — a signer par le wallet (Phantom/Solflare)",
                "commission": "0% MAXIA (pump.fun fees apply)",
                "timestamp": int(time.time()),
            }

        # Erreur PumpPortal
        error_text = resp.text[:200] if resp.text else f"HTTP {resp.status_code}"
        logger.warning(f"PumpPortal error {resp.status_code}: {error_text}")
        raise HTTPException(502, f"PumpPortal error: {error_text}")

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, detail=safe_error(exc, "token_launch"))


print("[TokenLauncher] Initialise — pump.fun + PumpPortal (0% commission MAXIA)")
