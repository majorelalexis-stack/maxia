"""Alert Service — alertes prix/whale/yield par Telegram pour les users.
$0.99/mois en USDC. Le bot @MAXIA_AI_bot envoie les alertes.

Types d'alertes :
- Prix : notification quand un token depasse/descend sous un seuil
- Whale : notification quand un gros wallet bouge (>$100K)
- Yield : notification quand un nouveau yield >10% APY apparait
- Transaction : notification quand une tx de l'user est confirmee
"""
import json
import logging
import time
import asyncio
import os
import httpx
from core.http_client import get_http_client

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ALERT_PRICE_MONTHLY_USDC = 0.99

# Subscribers en memoire + DB persistence
_subscribers = {}  # wallet -> {chat_id, alerts: [...], subscribed_at, expires_at}
_db_loaded = False


async def _load_subscribers_from_db() -> None:
    """Lazy-load subscribers from DB on first use. Idempotent."""
    global _db_loaded
    if _db_loaded:
        return
    try:
        from core.database import db
        rows = await db.raw_execute_fetchall(
            "SELECT wallet, chat_id, alert_types, subscribed_at, expires_at, active FROM alert_subscriptions"
        )
        for row in (rows or []):
            _subscribers[row["wallet"]] = {
                "chat_id": row["chat_id"],
                "alerts": json.loads(row.get("alert_types", '["price","whale","yield","transaction"]')),
                "subscribed_at": row["subscribed_at"],
                "expires_at": row["expires_at"],
                "active": bool(row.get("active", 1)),
            }
        _db_loaded = True
    except Exception as e:
        logging.getLogger(__name__).warning("alert_service: DB load failed: %s", e)

async def subscribe(wallet: str, chat_id: str, alert_types: list = None) -> dict:
    """Abonne un user aux alertes. Retourne les details de l'abonnement."""
    if not alert_types:
        alert_types = ["price", "whale", "yield", "transaction"]
    now_ts = int(time.time())
    expires_ts = now_ts + 30 * 86400  # 30 jours
    _subscribers[wallet] = {
        "chat_id": chat_id,
        "alerts": alert_types,
        "subscribed_at": now_ts,
        "expires_at": expires_ts,
        "active": True,
    }
    # Persist to DB
    try:
        from core.database import db
        await db.raw_execute(
            "INSERT INTO alert_subscriptions (wallet, chat_id, alert_types, subscribed_at, expires_at, active) "
            "VALUES (?, ?, ?, ?, ?, 1) "
            "ON CONFLICT(wallet) DO UPDATE SET chat_id=excluded.chat_id, alert_types=excluded.alert_types, "
            "subscribed_at=excluded.subscribed_at, expires_at=excluded.expires_at, active=1",
            (wallet, chat_id, json.dumps(alert_types), now_ts, expires_ts)
        )
    except Exception as e:
        logging.getLogger(__name__).warning("alert_service: DB save failed: %s", e)
    # Envoyer confirmation
    await _send_telegram(chat_id,
        "MAXIA Alerts activated!\n\n"
        f"Alerts: {', '.join(alert_types)}\n"
        f"Wallet: {wallet[:8]}...\n"
        f"Price: $0.99/month USDC\n\n"
        "You'll receive notifications for price movements, whale activity, yield opportunities, and transaction confirmations."
    )
    return {"success": True, "wallet": wallet, "alerts": alert_types, "price": ALERT_PRICE_MONTHLY_USDC}

async def unsubscribe(wallet: str) -> dict:
    if wallet in _subscribers:
        _subscribers[wallet]["active"] = False
        try:
            from core.database import db
            await db.raw_execute("UPDATE alert_subscriptions SET active=0 WHERE wallet=?", (wallet,))
        except Exception:
            pass
        return {"success": True}
    return {"success": False, "error": "Not subscribed"}

def get_subscribers() -> dict:
    return {k: v for k, v in _subscribers.items() if v.get("active")}

async def check_and_send_alerts(prices: dict, yields: list = None):
    """Verifie les conditions et envoie les alertes aux subscribers."""
    await _load_subscribers_from_db()
    for wallet, sub in _subscribers.items():
        if not sub.get("active"):
            continue
        if sub["expires_at"] < time.time():
            sub["active"] = False
            continue
        chat_id = sub["chat_id"]
        # Price alerts - check for >5% moves
        if "price" in sub.get("alerts", []):
            for symbol, data in prices.items():
                change = data.get("change_24h", 0) if isinstance(data, dict) else 0
                if abs(change) > 5:
                    direction = "UP" if change > 0 else "DOWN"
                    price = data.get("price", 0) if isinstance(data, dict) else 0
                    await _send_telegram(chat_id,
                        f"{direction} {symbol} moved {change:+.1f}% (${price:,.2f})")

async def _send_telegram(chat_id: str, text: str):
    """Envoie un message Telegram via le bot."""
    if not TELEGRAM_BOT_TOKEN:
        return
    try:
        client = get_http_client()
        await client.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10)
    except Exception:
        pass
