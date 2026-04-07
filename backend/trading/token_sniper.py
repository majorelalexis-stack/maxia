"""MAXIA ONE-40/51 — Token Sniper Bot.

Detecte les nouveaux tokens via DexScreener (primary) + pump.fun (fallback).
Filtre par criteres, alerte via Telegram/webhook.
ONE-51: auto-buy via unsigned Jupiter transactions.

Endpoints:
  GET  /api/sniper/new-tokens       — Derniers tokens detectes (filtrables)
  POST /api/sniper/watch             — Creer une alerte sniper (+ auto_buy_usdc)
  GET  /api/sniper/watchlist         — Lister ses alertes actives
  DELETE /api/sniper/watch/{watch_id} — Supprimer une alerte
  GET  /api/sniper/stats             — Statistiques du scanner
  GET  /api/sniper/pending           — Pending unsigned buy txs
  POST /api/sniper/confirm/{tx_id}   — Confirm a signed buy tx

Worker background: scan toutes les 30s.
"""
import asyncio
import json
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
    auto_buy_usdc: float = Field(0, ge=0, le=1000, description="Auto-buy amount in USDC (0 = disabled)")


# ── DB Tables (ONE-51) ──

async def _get_db():
    from core.database import db
    return db


async def ensure_tables():
    db = await _get_db()
    await db.raw_executescript("""
        CREATE TABLE IF NOT EXISTS sniper_pending_txs (
            tx_id TEXT PRIMARY KEY,
            watcher_id TEXT NOT NULL,
            token_mint TEXT NOT NULL,
            token_symbol TEXT NOT NULL,
            swap_transaction TEXT NOT NULL,
            amount_usdc NUMERIC(18,6) NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at INTEGER DEFAULT (strftime('%s','now')),
            tx_signature TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_sniper_tx_watcher ON sniper_pending_txs(watcher_id, status);

        CREATE TABLE IF NOT EXISTS sniper_watchers (
            watcher_id TEXT PRIMARY KEY,
            wallet TEXT NOT NULL,
            filters TEXT NOT NULL,
            auto_buy_usdc NUMERIC(18,6) DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'active',
            created_at INTEGER DEFAULT (strftime('%s','now'))
        );
        CREATE INDEX IF NOT EXISTS idx_sniper_watchers_wallet ON sniper_watchers(wallet);
        CREATE INDEX IF NOT EXISTS idx_sniper_watchers_status ON sniper_watchers(status);
    """)
    await _load_watchers_from_db()


async def _load_watchers_from_db() -> None:
    """Load active watchers from DB into in-memory _watchlist on startup."""
    try:
        db = await _get_db()
        rows = await db.raw_execute_fetchall(
            "SELECT watcher_id, wallet, filters, auto_buy_usdc, created_at "
            "FROM sniper_watchers WHERE status = 'active'"
        )
        for row in rows:
            filters = json.loads(row["filters"])
            watch = {
                "watch_id": row["watcher_id"],
                "wallet": row["wallet"],
                "min_market_cap_usd": filters.get("min_market_cap_usd", 0),
                "max_market_cap_usd": filters.get("max_market_cap_usd", 100_000),
                "min_boosts": filters.get("min_boosts", 0),
                "keywords": filters.get("keywords", []),
                "webhook_url": filters.get("webhook_url"),
                "telegram_chat_id": filters.get("telegram_chat_id"),
                "auto_buy_usdc": float(row.get("auto_buy_usdc", 0) or 0),
                "created_at": row.get("created_at", 0),
                "alerts_sent": 0,
            }
            _watchlist[row["watcher_id"]] = watch
        if rows:
            logger.info("[SNIPER] Loaded %d watchers from database", len(rows))
    except Exception as e:
        logger.error("[SNIPER] Failed to load watchers from DB: %s", e)


# ── Auto-buy helper (ONE-51) ──

async def _build_auto_buy_tx(watch: dict, token: dict) -> Optional[dict]:
    """Build an unsigned Jupiter buy tx for a detected token.

    Returns the pending tx record or None on failure.
    """
    amount_usdc = watch.get("auto_buy_usdc", 0)
    if amount_usdc <= 0:
        return None

    mint = token.get("mint", "")
    chain = token.get("chain", "solana")
    if not mint or chain != "solana":
        return None  # Jupiter only works on Solana

    wallet = watch.get("wallet", "")
    if not wallet:
        return None

    try:
        from blockchain.jupiter_router import get_quote, execute_swap, USDC_MINT

        amount_raw = int(amount_usdc * 1e6)  # USDC has 6 decimals
        quote = await get_quote(USDC_MINT, mint, amount_raw)
        if not quote.get("success"):
            logger.warning("[Sniper] Auto-buy quote failed for %s: %s",
                           mint[:12], quote.get("error", "unknown"))
            return None

        raw_quote = quote.get("raw_quote")
        if not raw_quote:
            logger.warning("[Sniper] Auto-buy: no raw_quote for %s", mint[:12])
            return None

        swap_result = await execute_swap(raw_quote, wallet)
        if not swap_result.get("success"):
            logger.warning("[Sniper] Auto-buy swap build failed for %s: %s",
                           mint[:12], swap_result.get("error", "unknown"))
            return None

        tx_id = f"snp_tx_{uuid.uuid4().hex[:12]}"
        symbol = token.get("symbol", mint[:8])
        swap_tx_data = swap_result.get("transaction", swap_result.get("swapTransaction", ""))

        pending = {
            "tx_id": tx_id,
            "watcher_id": watch["watch_id"],
            "token_mint": mint,
            "token_symbol": symbol,
            "swap_transaction": swap_tx_data,
            "amount_usdc": amount_usdc,
            "status": "pending",
            "created_at": int(time.time()),
            "tx_signature": "",
        }

        # Persist to DB
        db = await _get_db()
        await db.raw_execute(
            "INSERT INTO sniper_pending_txs "
            "(tx_id, watcher_id, token_mint, token_symbol, swap_transaction, amount_usdc, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (tx_id, watch["watch_id"], mint, symbol, swap_tx_data, amount_usdc, "pending"),
        )

        logger.info("[Sniper] Auto-buy tx built: %s for %s USDC on %s",
                     tx_id, amount_usdc, symbol)
        return pending

    except Exception as e:
        logger.error("[Sniper] Auto-buy error for %s: %s", mint[:12], e)
        return None


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
    """Send sniper alert via Telegram and/or webhook. Build auto-buy tx if enabled."""
    global _alert_count

    source = token.get("source", "?")
    url = token.get("url", "")

    # ONE-51: Build auto-buy tx if enabled
    pending_tx = None
    auto_buy_usdc = watch.get("auto_buy_usdc", 0)
    if auto_buy_usdc > 0:
        pending_tx = await _build_auto_buy_tx(watch, token)

    buy_info = ""
    if pending_tx:
        buy_info = (
            f"\n\nBuy tx ready — sign in app\n"
            f"Amount: {pending_tx['amount_usdc']} USDC\n"
            f"TX ID: {pending_tx['tx_id']}"
        )

    msg = (
        f"MAXIA Sniper Alert!\n"
        f"Token: {token.get('name', '?')}\n"
        f"Address: {token['mint'][:20]}...\n"
        f"Boosts: {token.get('boosts', 0)}\n"
        f"Chain: {token.get('chain', 'solana')}\n"
        f"Source: {source}\n"
        f"Link: {url}"
        f"{buy_info}"
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
        "auto_buy_usdc": req.auto_buy_usdc,
        "created_at": int(time.time()),
        "alerts_sent": 0,
    }
    _watchlist[watch_id] = watch

    # Persist to DB
    try:
        filters_json = json.dumps({
            "min_market_cap_usd": req.min_market_cap_usd,
            "max_market_cap_usd": req.max_market_cap_usd,
            "min_boosts": req.min_boosts,
            "keywords": req.keywords[:10],
            "webhook_url": req.webhook_url,
            "telegram_chat_id": req.telegram_chat_id,
        })
        db = await _get_db()
        await db.raw_execute(
            "INSERT INTO sniper_watchers (watcher_id, wallet, filters, auto_buy_usdc) "
            "VALUES (?, ?, ?, ?)",
            (watch_id, req.wallet, filters_json, req.auto_buy_usdc),
        )
    except Exception as e:
        logger.error("[SNIPER] Failed to persist watcher %s: %s", watch_id, e)

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
    _watchlist.pop(watch_id)

    # Remove from DB
    try:
        db = await _get_db()
        await db.raw_execute(
            "UPDATE sniper_watchers SET status = 'deleted' WHERE watcher_id = ?",
            (watch_id,),
        )
    except Exception as e:
        logger.error("[SNIPER] Failed to delete watcher %s from DB: %s", watch_id, e)

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


# ══════════════════════════════════════════
#  ONE-51 — Pending Buy Transactions
# ══════════════════════════════════════════

@router.get("/pending")
async def get_pending_txs(wallet: str = Query(..., min_length=10)):
    """Returns pending unsigned buy transactions for this wallet."""
    # Find watcher IDs belonging to this wallet
    wallet_watcher_ids = [
        w["watch_id"] for w in _watchlist.values()
        if w.get("wallet") == wallet
    ]
    if not wallet_watcher_ids:
        return {"wallet": wallet, "count": 0, "pending_txs": []}

    try:
        db = await _get_db()
        placeholders = ",".join("?" for _ in wallet_watcher_ids)
        rows = await db.raw_execute_fetchall(
            f"SELECT tx_id, watcher_id, token_mint, token_symbol, "
            f"swap_transaction, amount_usdc, status, created_at "
            f"FROM sniper_pending_txs "
            f"WHERE watcher_id IN ({placeholders}) AND status = 'pending' "
            f"ORDER BY created_at DESC LIMIT 50",
            tuple(wallet_watcher_ids),
        )
        return {
            "wallet": wallet,
            "count": len(rows),
            "pending_txs": rows,
        }
    except Exception as e:
        logger.error("[Sniper] Pending txs query error: %s", e)
        return {"wallet": wallet, "count": 0, "pending_txs": [], "error": safe_error(e)}


class ConfirmRequest(BaseModel):
    tx_signature: str = Field(..., min_length=10, max_length=200,
                              description="On-chain transaction signature")


@router.post("/confirm/{tx_id}")
async def confirm_buy_tx(tx_id: str, req: ConfirmRequest):
    """Confirm a pending sniper buy transaction with its on-chain signature."""
    if not tx_id or len(tx_id) > 100:
        raise HTTPException(400, "Invalid tx_id")

    try:
        db = await _get_db()
        rows = await db.raw_execute_fetchall(
            "SELECT tx_id, watcher_id, token_mint, token_symbol, amount_usdc, status "
            "FROM sniper_pending_txs WHERE tx_id = ?",
            (tx_id,),
        )
        if not rows:
            raise HTTPException(404, f"Pending transaction not found: {tx_id}")

        row = rows[0]
        if row["status"] != "pending":
            raise HTTPException(400, f"Transaction already {row['status']}")

        # Verify on-chain (best-effort — Jupiter swap tx goes to DEX, not treasury)
        verified = False
        try:
            from blockchain.solana_verifier import verify_transaction
            result = await verify_transaction(req.tx_signature)
            if result and result.get("valid"):
                verified = True
        except Exception as e:
            logger.warning("[Sniper] On-chain verify failed (accepting): %s", e)
            verified = True

        # Update status
        new_status = "confirmed" if verified else "failed"
        await db.raw_execute(
            "UPDATE sniper_pending_txs SET status = ?, tx_signature = ? WHERE tx_id = ?",
            (new_status, req.tx_signature, tx_id),
        )

        return {
            "status": new_status,
            "tx_id": tx_id,
            "token_mint": row["token_mint"],
            "token_symbol": row["token_symbol"],
            "amount_usdc": row["amount_usdc"],
            "tx_signature": req.tx_signature,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("[Sniper] Confirm error: %s", e)
        raise HTTPException(500, safe_error(e))
