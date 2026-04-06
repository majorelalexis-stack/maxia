"""MAXIA V12 — Public Whale Feed + Telegram Notifications (ONE-39).

Public feed of large on-chain movements (>$100K) detected by the
existing whale tracker (trading_features.check_whales).
Adds Telegram/Discord notifications when whales move.

Endpoints:
  GET  /api/whale/feed         — Public feed of recent whale alerts
  GET  /api/whale/top-wallets  — Top monitored wallets by alert count
  POST /api/whale/subscribe    — Subscribe Telegram chat to whale alerts
  DELETE /api/whale/unsubscribe — Unsubscribe from whale alerts
"""
import logging
import time
import os
import re
from typing import Optional

from fastapi import APIRouter, HTTPException, Header, Query

logger = logging.getLogger("maxia.whale_feed")
router = APIRouter(prefix="/api/whale", tags=["whale-alerts"])

_WALLET_RE = re.compile(r'^[1-9A-HJ-NP-Za-km-z]{32,44}$')
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# In-memory subscriber list (persisted via DB below)
_tg_subscribers: dict[str, dict] = {}  # chat_id -> {wallet_filter, subscribed_at}
_schema_ready = False

_SCHEMA = """
CREATE TABLE IF NOT EXISTS whale_tg_subscribers (
    chat_id TEXT PRIMARY KEY,
    wallet_filter TEXT DEFAULT '',
    min_amount_usdc REAL DEFAULT 100000,
    subscribed_at INTEGER NOT NULL,
    active INTEGER DEFAULT 1
);
"""


async def _get_db():
    from core.database import db
    return db


async def _validate_api_key(api_key: str) -> dict:
    """Validate API key against registered agents. Raises 401 if invalid."""
    if not api_key:
        raise HTTPException(401, "X-API-Key required")
    db = await _get_db()
    rows = await db.raw_execute_fetchall(
        "SELECT name, wallet FROM agents WHERE api_key=?", (api_key,))
    if not rows:
        raise HTTPException(401, "Invalid API key")
    return dict(rows[0])


async def _ensure_schema():
    global _schema_ready
    if _schema_ready:
        return
    try:
        db = await _get_db()
        await db.raw_executescript(_SCHEMA)
        _schema_ready = True
    except Exception as e:
        logger.error("Whale feed schema error: %s", e)


# ══════════════════════════════════════════
#  PUBLIC FEED — recent whale movements
# ══════════════════════════════════════════

@router.get("/feed")
async def whale_feed(
    limit: int = Query(20, ge=1, le=100),
    min_amount: float = Query(0, ge=0),
    wallet: Optional[str] = None,
):
    """Public feed of recent whale alerts. No auth required.

    - limit: max alerts to return (1-100)
    - min_amount: minimum USDC amount filter
    - wallet: filter by specific wallet address
    """
    db = await _get_db()
    params: list = []
    where_clauses = []

    if min_amount > 0:
        where_clauses.append("a.amount_usdc >= ?")
        params.append(min_amount)
    if wallet:
        if not _WALLET_RE.match(wallet):
            raise HTTPException(400, "Invalid wallet address")
        where_clauses.append("a.wallet = ?")
        params.append(wallet)

    where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    params.append(limit)

    try:
        rows = await db.raw_execute_fetchall(
            f"SELECT a.id, a.monitor_id, a.wallet, a.action, a.amount_usdc, "
            f"a.tx_signature, a.created_at "
            f"FROM whale_alerts a {where} "
            f"ORDER BY a.created_at DESC LIMIT ?", tuple(params))

        alerts = []
        for row in rows:
            r = dict(row)
            alerts.append({
                "id": r.get("id", ""),
                "wallet": r.get("wallet", ""),
                "action": r.get("action", "large_transfer"),
                "amount_usdc": r.get("amount_usdc", 0),
                "tx": r.get("tx_signature", ""),
                "timestamp": r.get("created_at", 0),
            })

        return {
            "alerts": alerts,
            "count": len(alerts),
            "filters": {
                "min_amount": min_amount,
                "wallet": wallet,
                "limit": limit,
            },
        }
    except Exception:
        return {"alerts": [], "count": 0, "note": "whale_alerts table not yet populated"}


# ══════════════════════════════════════════
#  TOP WALLETS — most active whales
# ══════════════════════════════════════════

@router.get("/top-wallets")
async def whale_top_wallets(limit: int = Query(10, ge=1, le=50)):
    """Top wallets by whale alert count. Public, no auth."""
    db = await _get_db()
    try:
        rows = await db.raw_execute_fetchall(
            "SELECT wallet, COUNT(*) as alert_count, "
            "SUM(amount_usdc) as total_volume, "
            "MAX(created_at) as last_seen "
            "FROM whale_alerts "
            "GROUP BY wallet ORDER BY total_volume DESC LIMIT ?", (limit,))
        wallets = []
        for row in rows:
            r = dict(row)
            wallets.append({
                "wallet": r["wallet"],
                "alert_count": r["alert_count"],
                "total_volume_usdc": round(r.get("total_volume") or 0, 2),
                "last_seen": r.get("last_seen", 0),
            })
        return {"top_wallets": wallets, "count": len(wallets)}
    except Exception:
        return {"top_wallets": [], "count": 0}


# ══════════════════════════════════════════
#  TELEGRAM SUBSCRIPTION
# ══════════════════════════════════════════

@router.post("/subscribe")
async def whale_subscribe(
    req: dict,
    x_api_key: str = Header(None, alias="X-API-Key"),
):
    """Subscribe a Telegram chat_id to whale alerts.

    Body: {"chat_id": "123456789", "min_amount_usdc": 100000, "wallet_filter": ""}
    """
    await _validate_api_key(x_api_key)
    await _ensure_schema()
    db = await _get_db()

    chat_id = str(req.get("chat_id", "")).strip()
    if not chat_id or not (chat_id.lstrip("-").isdigit()):
        raise HTTPException(400, "Valid Telegram chat_id required")

    min_amount = max(1000, float(req.get("min_amount_usdc", 100000)))
    wallet_filter = str(req.get("wallet_filter", "")).strip()[:64]
    if wallet_filter and not _WALLET_RE.match(wallet_filter):
        raise HTTPException(400, "Invalid wallet_filter address format")

    await db.raw_execute(
        "INSERT OR REPLACE INTO whale_tg_subscribers"
        "(chat_id, wallet_filter, min_amount_usdc, subscribed_at, active) "
        "VALUES(?,?,?,?,1)",
        (chat_id, wallet_filter, min_amount, int(time.time())))

    # Send confirmation via Telegram
    await _send_tg(chat_id,
        "Whale Alerts activated!\n\n"
        f"Min amount: ${min_amount:,.0f}\n"
        f"Wallet filter: {wallet_filter or 'all wallets'}\n\n"
        "You will be notified when large transfers are detected.")

    return {"success": True, "chat_id": chat_id, "min_amount_usdc": min_amount}


@router.delete("/unsubscribe")
async def whale_unsubscribe(
    chat_id: str = Query(...),
    x_api_key: str = Header(None, alias="X-API-Key"),
):
    """Unsubscribe a Telegram chat from whale alerts."""
    await _validate_api_key(x_api_key)
    await _ensure_schema()
    db = await _get_db()
    await db.raw_execute(
        "UPDATE whale_tg_subscribers SET active=0 WHERE chat_id=?", (chat_id,))
    return {"success": True, "chat_id": chat_id}


# ══════════════════════════════════════════
#  NOTIFICATION ENGINE — called by check_whales
# ══════════════════════════════════════════

async def notify_whale_alert(wallet: str, amount_usdc: float, tx_sig: str, chain: str = "solana"):
    """Send Telegram + Discord notifications for a whale alert.
    Call this from trading_features.check_whales() after inserting an alert.
    """
    msg = (
        f"<b>WHALE ALERT</b>\n\n"
        f"Wallet: <code>{wallet[:8]}...{wallet[-6:]}</code>\n"
        f"Amount: <b>${amount_usdc:,.2f}</b>\n"
        f"Chain: {chain.upper()}\n"
        f"TX: <code>{tx_sig[:16]}...</code>\n"
        f"Time: {time.strftime('%H:%M UTC', time.gmtime())}"
    )

    # Notify all active Telegram subscribers
    try:
        await _ensure_schema()
        db = await _get_db()
        subs = await db.raw_execute_fetchall(
            "SELECT chat_id, wallet_filter, min_amount_usdc "
            "FROM whale_tg_subscribers WHERE active=1")
        for sub in subs:
            s = dict(sub)
            if amount_usdc < (s.get("min_amount_usdc") or 0):
                continue
            wf = s.get("wallet_filter", "")
            if wf and wf != wallet:
                continue
            await _send_tg(s["chat_id"], msg)
    except Exception as e:
        logger.debug("Whale TG notify error: %s", e)

    # Discord system alert
    try:
        from infra.alerts import alert_system
        await alert_system(
            "Whale Alert",
            f"${amount_usdc:,.0f} moved by {wallet[:8]}... on {chain}")
    except Exception:
        pass


async def _send_tg(chat_id: str, text: str):
    """Send a Telegram message via bot."""
    if not TELEGRAM_BOT_TOKEN:
        return
    try:
        from core.http_client import get_http_client
        client = get_http_client()
        await client.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10)
    except Exception as e:
        logger.debug("Whale TG send error: %s", e)


def get_router():
    return router
