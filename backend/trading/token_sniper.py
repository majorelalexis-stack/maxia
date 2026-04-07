"""MAXIA ONE-40 — Token Sniper Bot.

Detecte les nouveaux tokens via DexScreener (primary) + pump.fun (fallback).
Filtre par criteres, alerte via Telegram/webhook.

Endpoints:
  GET  /api/sniper/new-tokens       — Derniers tokens detectes (filtrables)
  POST /api/sniper/watch             — Creer une alerte sniper
  GET  /api/sniper/watchlist         — Lister ses alertes actives
  DELETE /api/sniper/watch/{watch_id} — Supprimer une alerte
  GET  /api/sniper/stats             — Statistiques du scanner

Worker background: scan toutes les 30s.
"""
import asyncio
import logging
import os
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

_SCAN_INTERVAL = int(os.getenv("SNIPER_SCAN_INTERVAL", "30"))
_MAX_WATCHLIST = 50
_MAX_DETECTED = 200

# ── State ──

_detected_tokens: list[dict] = []
_detected_mints: set[str] = set()
_watchlist: dict[str, dict] = {}
_last_scan: float = 0
_scan_count: int = 0
_alert_count: int = 0
_source_used: str = "none"


# ── Models ──

class WatchRequest(BaseModel):
    wallet: str = Field(..., min_length=10, description="Wallet address")
    min_market_cap_usd: float = Field(0, ge=0)
    max_market_cap_usd: float = Field(100_000, ge=0)
    min_boosts: int = Field(0, ge=0, description="Min boost count (DexScreener social proof)")
    keywords: list[str] = Field(default_factory=list, description="Keywords in name/description")
    webhook_url: Optional[str] = None
    telegram_chat_id: Optional[str] = None


# ── Scanner ──

async def _fetch_new_tokens() -> list[dict]:
    """Fetch latest tokens. DexScreener primary, pump.fun fallback."""
    global _source_used
    client = get_http_client()

    # Primary: DexScreener token boosts (always works)
    try:
        resp = await client.get(
            "https://api.dexscreener.com/token-boosts/latest/v1",
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list) and data:
                _source_used = "dexscreener"
                return [{"_source": "dexscreener", **i} for i in data[:50]]
    except Exception as e:
        print(f"[Sniper] DexScreener error: {e}")

    # Fallback: pump.fun (may be blocked from VPS)
    try:
        resp = await client.get(
            "https://frontend-api-v3.pump.fun/coins/trending",
            timeout=10,
        )
        if resp.status_code == 200 and resp.content and len(resp.content) > 10:
            data = resp.json()
            items = data if isinstance(data, list) else data.get("coins", data.get("results", []))
            if items:
                _source_used = "pump.fun"
                return [{"_source": "pump.fun", **i} for i in items[:50]]
    except Exception:
        pass

    return []


def _get_mint(raw: dict) -> str:
    """Extract mint/address from raw token data."""
    return raw.get("mint", "") or raw.get("tokenAddress", "")


def _parse_token(raw: dict) -> dict:
    """Normalize a token from any source into our format."""
    source = raw.get("_source", "unknown")

    if source == "dexscreener":
        addr = raw.get("tokenAddress", "")
        desc = raw.get("description", "")
        chain = raw.get("chainId", "solana")
        boosts = int(raw.get("totalAmount", 0) or 0)
        url = raw.get("url", f"https://dexscreener.com/{chain}/{addr}")

        # Name: description or truncated address
        name = desc[:50] if desc else f"{addr[:6]}...{addr[-4:]}"
        # Symbol: truncated address (DexScreener doesn't provide symbol)
        symbol = f"{addr[:4]}...{addr[-4:]}"

        return {
            "mint": addr,
            "name": name,
            "symbol": symbol,
            "description": desc[:200],
            "market_cap_usd": 0,
            "boosts": boosts,
            "image_uri": raw.get("icon", ""),
            "detected_at": int(time.time()),
            "source": "dexscreener",
            "chain": chain,
            "url": url,
        }

    # pump.fun format
    mint = raw.get("mint", "")
    return {
        "mint": mint,
        "name": raw.get("name", ""),
        "symbol": raw.get("symbol", ""),
        "description": raw.get("description", "")[:200],
        "market_cap_usd": float(raw.get("usd_market_cap", raw.get("market_cap", 0)) or 0),
        "boosts": int(raw.get("reply_count", 0) or 0),
        "image_uri": raw.get("image_uri", ""),
        "detected_at": int(time.time()),
        "source": "pump.fun",
        "chain": "solana",
        "url": f"https://pump.fun/{mint}",
    }


def _matches_watch(token: dict, watch: dict) -> bool:
    """Check if a token matches a watcher's criteria."""
    mc = token.get("market_cap_usd", 0)
    if watch.get("min_market_cap_usd", 0) > 0 and mc < watch["min_market_cap_usd"]:
        return False
    if mc > watch.get("max_market_cap_usd", 100_000):
        return False
    if token.get("boosts", 0) < watch.get("min_boosts", 0):
        return False

    keywords = watch.get("keywords", [])
    if keywords:
        text = f"{token.get('name', '')} {token.get('symbol', '')} {token.get('description', '')}".lower()
        if not any(kw.lower() in text for kw in keywords):
            return False

    return True


async def _notify_watcher(watch: dict, token: dict) -> None:
    """Send sniper alert via Telegram and/or webhook."""
    global _alert_count

    source = token.get("source", "?")
    url = token.get("url", "")
    msg = (
        f"MAXIA Sniper Alert!\n"
        f"Token: {token.get('name', '?')}\n"
        f"Address: {token['mint'][:20]}...\n"
        f"Boosts: {token.get('boosts', 0)}\n"
        f"Chain: {token.get('chain', 'solana')}\n"
        f"Source: {source}\n"
        f"Link: {url}"
    )

    # Telegram (with retry)
    chat_id = watch.get("telegram_chat_id")
    if chat_id:
        bot_token = os.getenv("TELEGRAM_CLIENT_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN", "")
        if bot_token:
            for attempt in range(2):
                try:
                    client = get_http_client()
                    resp = await client.post(
                        f"https://api.telegram.org/bot{bot_token}/sendMessage",
                        json={"chat_id": chat_id, "text": msg},
                        timeout=10,
                    )
                    if resp.status_code == 429:
                        await asyncio.sleep(2)
                        continue
                    break
                except Exception as e:
                    print(f"[Sniper] Telegram error (attempt {attempt + 1}): {e}")

    # Webhook
    webhook = watch.get("webhook_url")
    if webhook:
        try:
            client = get_http_client()
            await client.post(webhook, json={
                "type": "sniper_alert",
                "watch_id": watch["watch_id"],
                "token": token,
            }, timeout=10)
        except Exception as e:
            print(f"[Sniper] Webhook error: {e}")

    _alert_count += 1
    watch["alerts_sent"] = watch.get("alerts_sent", 0) + 1


async def sniper_worker():
    """Background worker — scans every SCAN_INTERVAL seconds."""
    global _last_scan, _scan_count

    while True:
        try:
            await asyncio.sleep(_SCAN_INTERVAL)

            raw_tokens = await _fetch_new_tokens()
            new_count = 0

            for raw in raw_tokens:
                mint = _get_mint(raw)
                if not mint or mint in _detected_mints:
                    continue

                token = _parse_token(raw)
                _detected_mints.add(mint)
                _detected_tokens.insert(0, token)
                new_count += 1

                # Check against active watchers
                for watch in list(_watchlist.values()):
                    if _matches_watch(token, watch):
                        await _notify_watcher(watch, token)

            # Cap detected list
            if len(_detected_tokens) > _MAX_DETECTED:
                removed = _detected_tokens[_MAX_DETECTED:]
                _detected_tokens[:] = _detected_tokens[:_MAX_DETECTED]
                for t in removed:
                    _detected_mints.discard(t.get("mint", ""))

            _last_scan = time.time()
            _scan_count += 1

            if new_count > 0:
                print(f"[Sniper] Scan #{_scan_count}: {new_count} new tokens ({_source_used})")

        except Exception as e:
            print(f"[Sniper] Worker error: {e}")


# ── Endpoints ──

@router.get("/new-tokens")
async def get_new_tokens(
    min_market_cap: float = Query(0, ge=0),
    max_market_cap: float = Query(1_000_000, ge=0),
    min_boosts: int = Query(0, ge=0, description="Min boost/reply count"),
    keyword: str = Query("", description="Keyword filter"),
    limit: int = Query(20, ge=1, le=100),
):
    """Derniers tokens detectes, filtrables."""
    # Live fetch if empty
    if not _detected_tokens:
        raw_tokens = await _fetch_new_tokens()
        for raw in raw_tokens:
            mint = _get_mint(raw)
            if mint and mint not in _detected_mints:
                token = _parse_token(raw)
                _detected_mints.add(mint)
                _detected_tokens.insert(0, token)

    filtered = []
    for t in _detected_tokens:
        mc = t.get("market_cap_usd", 0)
        if mc < min_market_cap:
            continue
        if max_market_cap > 0 and mc > max_market_cap and t.get("source") == "pump.fun":
            continue
        if t.get("boosts", 0) < min_boosts:
            continue
        if keyword:
            text = f"{t.get('name', '')} {t.get('symbol', '')} {t.get('description', '')}".lower()
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
        "source": _source_used,
    }


@router.post("/watch")
async def create_watch(req: WatchRequest):
    """Creer une alerte sniper."""
    wallet_watches = [w for w in _watchlist.values() if w.get("wallet") == req.wallet]
    if len(wallet_watches) >= _MAX_WATCHLIST:
        raise HTTPException(400, f"Max {_MAX_WATCHLIST} watches per wallet")

    if not req.webhook_url and not req.telegram_chat_id:
        raise HTTPException(400, "webhook_url ou telegram_chat_id requis")

    watch_id = f"snp_{uuid.uuid4().hex[:12]}"
    watch = {
        "watch_id": watch_id,
        "wallet": req.wallet,
        "min_market_cap_usd": req.min_market_cap_usd,
        "max_market_cap_usd": req.max_market_cap_usd,
        "min_boosts": req.min_boosts,
        "keywords": req.keywords[:10],
        "webhook_url": req.webhook_url,
        "telegram_chat_id": req.telegram_chat_id,
        "created_at": int(time.time()),
        "alerts_sent": 0,
    }
    _watchlist[watch_id] = watch

    return {"status": "created", "watch": watch}


@router.get("/watchlist")
async def get_watchlist(wallet: str = Query(..., min_length=10)):
    """Lister les alertes sniper actives."""
    watches = [w for w in _watchlist.values() if w.get("wallet") == wallet]
    return {"wallet": wallet, "count": len(watches), "watches": watches}


@router.delete("/watch/{watch_id}")
async def delete_watch(watch_id: str):
    """Supprimer une alerte sniper."""
    if watch_id not in _watchlist:
        raise HTTPException(404, f"Watch not found: {watch_id}")
    watch = _watchlist.pop(watch_id)
    return {"status": "deleted", "watch_id": watch_id}


@router.get("/stats")
async def sniper_stats():
    """Statistiques du scanner."""
    return {
        "total_detected": len(_detected_tokens),
        "unique_mints": len(_detected_mints),
        "active_watches": len(_watchlist),
        "total_scans": _scan_count,
        "total_alerts_sent": _alert_count,
        "last_scan": int(_last_scan) if _last_scan else None,
        "scan_interval_s": _SCAN_INTERVAL,
        "source": _source_used,
        "status": "active" if _scan_count > 0 else "starting",
    }
