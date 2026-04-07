"""MAXIA ONE-40 — Token Sniper Bot.

Detecte les nouveaux tokens sur pump.fun, filtre par criteres de qualite,
et alerte les utilisateurs via webhook/Telegram.

Endpoints:
  GET  /api/sniper/new-tokens       — Derniers tokens detectes (filtrables)
  POST /api/sniper/watch             — Creer une alerte sniper
  GET  /api/sniper/watchlist         — Lister ses alertes actives
  DELETE /api/sniper/watch/{watch_id} — Supprimer une alerte
  GET  /api/sniper/stats             — Statistiques du scanner

Worker background: scan pump.fun toutes les 30s, alerte les watchers.
"""
import asyncio
import json
import logging
import time
import uuid
from typing import Optional

from core.error_utils import safe_error
from core.http_client import get_http_client
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

logger = logging.getLogger("token_sniper")

router = APIRouter(prefix="/api/sniper", tags=["Token Sniper"])

# ── Config ──

_PUMP_API = "https://frontend-api-v3.pump.fun/coins"
_SCAN_INTERVAL = 30  # seconds
_MAX_WATCHLIST = 50   # per wallet
_CACHE_TTL = 15       # seconds

# ── State ──

_detected_tokens: list[dict] = []  # most recent first, max 200
_detected_mints: set[str] = set()  # dedup
_watchlist: dict[str, dict] = {}   # watch_id -> watch config
_last_scan: float = 0
_scan_count: int = 0
_alert_count: int = 0


# ── Models ──

class WatchRequest(BaseModel):
    wallet: str = Field(..., min_length=10, description="Wallet address")
    min_market_cap_usd: float = Field(0, ge=0, description="Min market cap USD")
    max_market_cap_usd: float = Field(100_000, ge=0, description="Max market cap USD (catch early)")
    min_replies: int = Field(0, ge=0, description="Min reply count (social proof)")
    keywords: list[str] = Field(default_factory=list, description="Keywords in name/description")
    webhook_url: Optional[str] = None
    telegram_chat_id: Optional[str] = None


# ── Scanner ──

async def _fetch_new_tokens() -> list[dict]:
    """Fetch latest tokens from pump.fun, fallback to DexScreener."""
    client = get_http_client()

    # Try pump.fun first
    try:
        resp = await client.get(
            "https://frontend-api-v3.pump.fun/coins/trending",
            timeout=10,
        )
        if resp.status_code == 200 and resp.content:
            data = resp.json()
            items = data if isinstance(data, list) else data.get("coins", data.get("results", []))
            if items:
                return [{"_source": "pump.fun", **i} for i in items[:50]]
    except Exception:
        pass

    # Fallback: DexScreener token boosts (new/trending tokens)
    try:
        resp = await client.get(
            "https://api.dexscreener.com/token-boosts/latest/v1",
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list):
                return [{"_source": "dexscreener", **i} for i in data[:50]]
    except Exception:
        pass

    return []


def _parse_token(raw: dict) -> dict:
    """Normalize a token from pump.fun or DexScreener into our format."""
    source = raw.get("_source", "unknown")

    if source == "dexscreener":
        return {
            "mint": raw.get("tokenAddress", ""),
            "name": raw.get("description", raw.get("tokenAddress", "")[:12]),
            "symbol": raw.get("symbol", "?"),
            "description": raw.get("description", "")[:200],
            "market_cap_usd": 0,
            "market_cap_sol": 0,
            "reply_count": 0,
            "creator": "",
            "image_uri": raw.get("icon", raw.get("header", "")),
            "created_timestamp": int(time.time()),
            "complete": False,
            "bonding_curve": "",
            "detected_at": int(time.time()),
            "source": "dexscreener",
            "chain": raw.get("chainId", "solana"),
            "url": raw.get("url", ""),
        }

    # pump.fun format
    mint = raw.get("mint", "")
    return {
        "mint": mint,
        "name": raw.get("name", ""),
        "symbol": raw.get("symbol", ""),
        "description": raw.get("description", "")[:200],
        "market_cap_usd": float(raw.get("usd_market_cap", raw.get("market_cap", 0)) or 0),
        "market_cap_sol": float(raw.get("market_cap_sol", 0) or 0),
        "reply_count": int(raw.get("reply_count", 0) or 0),
        "creator": raw.get("creator", ""),
        "image_uri": raw.get("image_uri", ""),
        "created_timestamp": int(raw.get("created_timestamp", 0) or 0),
        "complete": raw.get("complete", False),
        "bonding_curve": raw.get("bonding_curve", ""),
        "detected_at": int(time.time()),
        "source": "pump.fun",
    }


def _matches_watch(token: dict, watch: dict) -> bool:
    """Check if a token matches a watcher's criteria."""
    mc = token.get("market_cap_usd", 0)
    if mc < watch.get("min_market_cap_usd", 0):
        return False
    if mc > watch.get("max_market_cap_usd", 100_000):
        return False
    if token.get("reply_count", 0) < watch.get("min_replies", 0):
        return False

    keywords = watch.get("keywords", [])
    if keywords:
        text = f"{token.get('name', '')} {token.get('symbol', '')} {token.get('description', '')}".lower()
        if not any(kw.lower() in text for kw in keywords):
            return False

    return True


async def _notify_watcher(watch: dict, token: dict) -> None:
    """Send sniper alert to a watcher via Telegram and/or webhook."""
    global _alert_count

    msg = (
        f"🎯 MAXIA Sniper Alert!\n"
        f"Token: {token['name']} ({token['symbol']})\n"
        f"Mint: {token['mint']}\n"
        f"Market cap: ${token['market_cap_usd']:,.0f}\n"
        f"Replies: {token['reply_count']}\n"
        f"Created: {token['created_timestamp']}\n"
        f"pump.fun: https://pump.fun/{token['mint']}"
    )

    # Telegram
    chat_id = watch.get("telegram_chat_id")
    if chat_id:
        try:
            import os
            bot_token = os.getenv("TELEGRAM_CLIENT_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN", "")
            if bot_token:
                client = get_http_client()
                await client.post(
                    f"https://api.telegram.org/bot{bot_token}/sendMessage",
                    json={"chat_id": chat_id, "text": msg},
                )
        except Exception as e:
            logger.warning("[Sniper] Telegram error: %s", e)

    # Webhook
    webhook = watch.get("webhook_url")
    if webhook:
        try:
            client = get_http_client()
            await client.post(webhook, json={
                "type": "sniper_alert",
                "watch_id": watch["watch_id"],
                "token": token,
                "message": msg,
            })
        except Exception as e:
            logger.warning("[Sniper] Webhook error: %s", e)

    _alert_count += 1


async def sniper_worker():
    """Background worker — scans pump.fun every 30s, alerts watchers."""
    global _last_scan, _scan_count

    while True:
        try:
            await asyncio.sleep(_SCAN_INTERVAL)

            raw_tokens = await _fetch_new_tokens()
            new_count = 0

            for raw in raw_tokens:
                mint = raw.get("mint", "") or raw.get("tokenAddress", "")
                if not mint or mint in _detected_mints:
                    continue

                token = _parse_token(raw)
                _detected_mints.add(mint)
                _detected_tokens.insert(0, token)
                new_count += 1

                # Check against all active watchers
                for watch in _watchlist.values():
                    if _matches_watch(token, watch):
                        await _notify_watcher(watch, token)

            # Cap detected list at 200
            if len(_detected_tokens) > 200:
                removed = _detected_tokens[200:]
                _detected_tokens[:] = _detected_tokens[:200]
                for t in removed:
                    _detected_mints.discard(t.get("mint", ""))

            _last_scan = time.time()
            _scan_count += 1

            if new_count > 0:
                logger.info("[Sniper] Scan #%d: %d new tokens detected", _scan_count, new_count)

        except Exception as e:
            logger.error("[Sniper] Worker error: %s", e)


# ── Endpoints ──

@router.get("/new-tokens")
async def get_new_tokens(
    min_market_cap: float = Query(0, ge=0, description="Min market cap USD"),
    max_market_cap: float = Query(1_000_000, ge=0, description="Max market cap USD"),
    min_replies: int = Query(0, ge=0, description="Min reply count"),
    keyword: str = Query("", description="Keyword filter (name/symbol)"),
    limit: int = Query(20, ge=1, le=100, description="Max results"),
):
    """Derniers tokens detectes sur pump.fun, filtrables."""
    # If no tokens cached yet, do a live fetch
    if not _detected_tokens:
        raw_tokens = await _fetch_new_tokens()
        for raw in raw_tokens:
            mint = raw.get("mint", "") or raw.get("tokenAddress", "")
            if mint and mint not in _detected_mints:
                token = _parse_token(raw)
                _detected_mints.add(mint)
                _detected_tokens.insert(0, token)

    filtered = []
    for t in _detected_tokens:
        mc = t.get("market_cap_usd", 0)
        if mc < min_market_cap or mc > max_market_cap:
            continue
        if t.get("reply_count", 0) < min_replies:
            continue
        if keyword:
            text = f"{t.get('name', '')} {t.get('symbol', '')}".lower()
            if keyword.lower() not in text:
                continue
        filtered.append(t)
        if len(filtered) >= limit:
            break

    return {
        "tokens": filtered,
        "count": len(filtered),
        "total_detected": len(_detected_tokens),
        "last_scan": int(_last_scan) if _last_scan else None,
        "scan_interval_s": _SCAN_INTERVAL,
    }


@router.post("/watch")
async def create_watch(req: WatchRequest):
    """Creer une alerte sniper — notifie quand un nouveau token matche les criteres."""
    # Check limit per wallet
    wallet_watches = [w for w in _watchlist.values() if w.get("wallet") == req.wallet]
    if len(wallet_watches) >= _MAX_WATCHLIST:
        raise HTTPException(400, f"Max {_MAX_WATCHLIST} watches per wallet")

    if not req.webhook_url and not req.telegram_chat_id:
        raise HTTPException(400, "webhook_url ou telegram_chat_id requis pour recevoir les alertes")

    watch_id = f"snp_{uuid.uuid4().hex[:12]}"
    watch = {
        "watch_id": watch_id,
        "wallet": req.wallet,
        "min_market_cap_usd": req.min_market_cap_usd,
        "max_market_cap_usd": req.max_market_cap_usd,
        "min_replies": req.min_replies,
        "keywords": req.keywords[:10],  # max 10 keywords
        "webhook_url": req.webhook_url,
        "telegram_chat_id": req.telegram_chat_id,
        "created_at": int(time.time()),
        "alerts_sent": 0,
    }
    _watchlist[watch_id] = watch

    return {
        "status": "created",
        "watch": watch,
        "note": f"Scanning pump.fun every {_SCAN_INTERVAL}s. You'll be notified when a matching token appears.",
    }


@router.get("/watchlist")
async def get_watchlist(wallet: str = Query(..., min_length=10)):
    """Lister les alertes sniper actives pour un wallet."""
    watches = [w for w in _watchlist.values() if w.get("wallet") == wallet]
    return {
        "wallet": wallet,
        "count": len(watches),
        "watches": watches,
    }


@router.delete("/watch/{watch_id}")
async def delete_watch(watch_id: str):
    """Supprimer une alerte sniper."""
    if watch_id not in _watchlist:
        raise HTTPException(404, f"Watch not found: {watch_id}")

    watch = _watchlist.pop(watch_id)
    return {
        "status": "deleted",
        "watch_id": watch_id,
        "wallet": watch.get("wallet"),
    }


@router.get("/stats")
async def sniper_stats():
    """Statistiques du scanner sniper."""
    return {
        "total_detected": len(_detected_tokens),
        "unique_mints": len(_detected_mints),
        "active_watches": len(_watchlist),
        "total_scans": _scan_count,
        "total_alerts_sent": _alert_count,
        "last_scan": int(_last_scan) if _last_scan else None,
        "scan_interval_s": _SCAN_INTERVAL,
        "source": "pump.fun",
        "status": "active" if _scan_count > 0 else "starting",
    }
