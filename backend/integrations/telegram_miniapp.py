"""MAXIA ONE-30 — Telegram Mini App Backend.

API backend pour la Telegram Mini App (WebApp).
Authentifie via initData Telegram, proxy vers les endpoints existants.

Endpoints:
  POST /api/tg/auth           — Valide initData Telegram, retourne session token
  GET  /api/tg/prices         — Prix live (proxy price_oracle)
  POST /api/tg/swap/quote     — Quote swap (proxy crypto_swap)
  GET  /api/tg/portfolio      — Portfolio wallet (proxy web3_services)
  GET  /api/tg/alerts          — Alertes actives (proxy trading_tools)
  POST /api/tg/alerts          — Creer une alerte (proxy trading_tools)
  GET  /api/tg/yields          — Meilleurs yields DeFi
  GET  /api/tg/trending        — Tokens trending pump.fun
  GET  /api/tg/menu            — Menu structure pour le Mini App UI

Securite:
  - Telegram initData hash verification (HMAC-SHA256)
  - Session tokens (JWT-like) pour les requetes suivantes
  - Rate limit 30 req/min par user
"""
import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from typing import Optional
from urllib.parse import parse_qs, unquote

from core.error_utils import safe_error
from fastapi import APIRouter, HTTPException, Header, Query
from pydantic import BaseModel, Field

logger = logging.getLogger("telegram_miniapp")

router = APIRouter(prefix="/api/tg", tags=["Telegram Mini App"])

# ── Config ──

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# ── Session store ──

_sessions: dict[str, dict] = {}  # session_token -> {user_id, username, created_at, ...}
_SESSION_TTL = 86400  # 24h
_SESSION_MAX = 10_000

# ── Rate limiting ──

_rate_store: dict[str, list[float]] = {}
_RATE_LIMIT = 30  # req/min
_RATE_WINDOW = 60  # seconds


def _check_tg_rate(user_id: str) -> bool:
    """Rate limit check per Telegram user."""
    now = time.time()
    cutoff = now - _RATE_WINDOW
    timestamps = _rate_store.get(user_id, [])
    timestamps = [t for t in timestamps if t > cutoff]
    if len(timestamps) >= _RATE_LIMIT:
        _rate_store[user_id] = timestamps
        return False
    timestamps.append(now)
    _rate_store[user_id] = timestamps
    return True


# ── Telegram initData verification ──

def _verify_init_data(init_data: str) -> Optional[dict]:
    """Verify Telegram Mini App initData using HMAC-SHA256.

    Returns parsed user data if valid, None if invalid.
    See: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    """
    if not TELEGRAM_BOT_TOKEN:
        return None

    try:
        # Parse the query string
        parsed = parse_qs(init_data, keep_blank_values=True)
        received_hash = parsed.get("hash", [""])[0]
        if not received_hash:
            return None

        # Build the data-check-string (sorted key=value pairs, excluding hash)
        data_pairs = []
        for key, values in sorted(parsed.items()):
            if key != "hash":
                data_pairs.append(f"{key}={values[0]}")
        data_check_string = "\n".join(data_pairs)

        # Compute the secret key
        secret_key = hmac.new(
            b"WebAppData",
            TELEGRAM_BOT_TOKEN.encode("utf-8"),
            hashlib.sha256,
        ).digest()

        # Compute the hash
        computed_hash = hmac.new(
            secret_key,
            data_check_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(computed_hash, received_hash):
            return None

        # Check auth_date freshness (max 24h)
        auth_date = int(parsed.get("auth_date", ["0"])[0])
        if time.time() - auth_date > 86400:
            return None

        # Parse user data
        user_json = parsed.get("user", ["{}"])[0]
        user_data = json.loads(unquote(user_json))

        return {
            "user_id": str(user_data.get("id", "")),
            "first_name": user_data.get("first_name", ""),
            "last_name": user_data.get("last_name", ""),
            "username": user_data.get("username", ""),
            "language_code": user_data.get("language_code", "en"),
            "is_premium": user_data.get("is_premium", False),
            "auth_date": auth_date,
        }
    except Exception as e:
        logger.warning("[TG MiniApp] initData verification error: %s", e)
        return None


def _get_session(token: str) -> Optional[dict]:
    """Get session by token, return None if expired or missing."""
    session = _sessions.get(token)
    if not session:
        return None
    if time.time() - session.get("created_at", 0) > _SESSION_TTL:
        _sessions.pop(token, None)
        return None
    return session


def _require_session(x_tg_session: str) -> dict:
    """Validate session token from header, raise 401 if invalid."""
    if not x_tg_session:
        raise HTTPException(401, "X-TG-Session header required. Call POST /api/tg/auth first.")
    session = _get_session(x_tg_session)
    if not session:
        raise HTTPException(401, "Session expired or invalid. Re-authenticate via POST /api/tg/auth.")
    user_id = session.get("user_id", "")
    if not _check_tg_rate(user_id):
        raise HTTPException(429, "Rate limited (30 req/min)")
    return session


# ── Models ──

class TGAuthRequest(BaseModel):
    init_data: str = Field(..., min_length=10, description="Telegram WebApp.initData string")
    wallet_address: Optional[str] = Field(None, description="Optional Solana/EVM wallet to link")


# ── Endpoints ──

@router.post("/auth")
async def tg_auth(req: TGAuthRequest):
    """Authenticate via Telegram initData. Returns a session token for subsequent requests."""
    user = _verify_init_data(req.init_data)
    if not user:
        raise HTTPException(401, "Invalid Telegram initData. Ensure bot token is configured.")

    # Cleanup old sessions
    if len(_sessions) > _SESSION_MAX:
        now = time.time()
        expired = [k for k, v in _sessions.items() if now - v.get("created_at", 0) > _SESSION_TTL]
        for k in expired:
            del _sessions[k]

    session_token = f"tg_{uuid.uuid4().hex}"
    _sessions[session_token] = {
        **user,
        "wallet_address": req.wallet_address,
        "created_at": int(time.time()),
    }

    logger.info("[TG MiniApp] Auth: user=%s username=%s", user["user_id"], user.get("username", ""))

    return {
        "session_token": session_token,
        "user": {
            "id": user["user_id"],
            "username": user.get("username", ""),
            "first_name": user.get("first_name", ""),
            "is_premium": user.get("is_premium", False),
        },
        "expires_in": _SESSION_TTL,
        "wallet_linked": bool(req.wallet_address),
    }


@router.get("/prices")
async def tg_prices(
    tokens: str = Query("SOL,ETH,BTC,USDC", description="Comma-separated token symbols"),
    x_tg_session: str = Header("", alias="X-TG-Session"),
):
    """Live token prices for the Mini App."""
    _require_session(x_tg_session)

    try:
        from trading.price_oracle import get_prices

        token_list = [t.strip().upper() for t in tokens.split(",") if t.strip()][:20]
        prices = await get_prices(token_list)
        return {
            "prices": prices,
            "count": len(prices),
            "timestamp": int(time.time()),
        }
    except Exception as e:
        raise HTTPException(500, safe_error(e, "tg_prices"))


@router.get("/portfolio")
async def tg_portfolio(
    x_tg_session: str = Header("", alias="X-TG-Session"),
):
    """Portfolio for the linked wallet."""
    session = _require_session(x_tg_session)
    wallet = session.get("wallet_address")

    if not wallet:
        return {
            "error": "No wallet linked. Re-authenticate with wallet_address.",
            "tokens": [],
            "total_value_usd": 0,
        }

    try:
        from features.web3_services import analyze_wallet

        result = await analyze_wallet(wallet)
        return result
    except Exception as e:
        raise HTTPException(500, safe_error(e, "tg_portfolio"))


@router.get("/trending")
async def tg_trending(
    x_tg_session: str = Header("", alias="X-TG-Session"),
):
    """Trending tokens from pump.fun."""
    _require_session(x_tg_session)

    try:
        client = get_http_client()
        resp = await client.get("https://frontend-api-v3.pump.fun/coins/trending", timeout=10)
        if resp.status_code != 200:
            return {"tokens": [], "count": 0, "source": "pump.fun"}

        data = resp.json()
        items = data if isinstance(data, list) else data.get("coins", [])
        tokens = []
        for item in items[:20]:
            tokens.append({
                "mint": item.get("mint", ""),
                "name": item.get("name", ""),
                "symbol": item.get("symbol", ""),
                "market_cap_usd": float(item.get("usd_market_cap", 0) or 0),
                "reply_count": int(item.get("reply_count", 0) or 0),
            })

        return {"tokens": tokens, "count": len(tokens), "source": "pump.fun"}
    except Exception as e:
        raise HTTPException(500, safe_error(e, "tg_trending"))


@router.get("/menu")
async def tg_menu():
    """Menu structure for the Telegram Mini App UI. No auth required."""
    return {
        "app_name": "MAXIA Trading",
        "version": "1.0.0",
        "sections": [
            {
                "id": "prices",
                "label": "Prices",
                "icon": "chart",
                "endpoint": "/api/tg/prices",
            },
            {
                "id": "swap",
                "label": "Swap",
                "icon": "swap",
                "endpoint": "/api/tg/swap/quote",
                "requires_wallet": True,
            },
            {
                "id": "portfolio",
                "label": "Portfolio",
                "icon": "wallet",
                "endpoint": "/api/tg/portfolio",
                "requires_wallet": True,
            },
            {
                "id": "alerts",
                "label": "Alerts",
                "icon": "bell",
                "endpoint": "/api/tg/alerts",
                "requires_wallet": True,
            },
            {
                "id": "yields",
                "label": "DeFi Yields",
                "icon": "percent",
                "endpoint": "/api/tg/yields",
            },
            {
                "id": "trending",
                "label": "Trending",
                "icon": "fire",
                "endpoint": "/api/tg/trending",
            },
        ],
        "auth_endpoint": "/api/tg/auth",
        "note": "Authenticate first via POST /api/tg/auth with Telegram WebApp.initData",
    }
