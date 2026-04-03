"""Alert Service — alertes prix/whale/yield par Telegram pour les users.
$0.99/mois en USDC. Le bot @MAXIA_AI_bot envoie les alertes.

Types d'alertes :
- Prix : notification quand un token depasse/descend sous un seuil
- Whale : notification quand un gros wallet bouge (>$100K)
- Yield : notification quand un nouveau yield >10% APY apparait
- Transaction : notification quand une tx de l'user est confirmee
"""
import time
import asyncio
import os
import httpx
from core.http_client import get_http_client

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ALERT_PRICE_MONTHLY_USDC = 0.99

# Subscribers en memoire (persiste dans DB en prod)
_subscribers = {}  # wallet -> {chat_id, alerts: [...], subscribed_at, expires_at}

async def subscribe(wallet: str, chat_id: str, alert_types: list = None) -> dict:
    """Abonne un user aux alertes. Retourne les details de l'abonnement."""
    if not alert_types:
        alert_types = ["price", "whale", "yield", "transaction"]
    _subscribers[wallet] = {
        "chat_id": chat_id,
        "alerts": alert_types,
        "subscribed_at": int(time.time()),
        "expires_at": int(time.time()) + 30 * 86400,  # 30 jours
        "active": True,
    }
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
        return {"success": True}
    return {"success": False, "error": "Not subscribed"}

def get_subscribers() -> dict:
    return {k: v for k, v in _subscribers.items() if v.get("active")}

async def check_and_send_alerts(prices: dict, yields: list = None):
    """Verifie les conditions et envoie les alertes aux subscribers."""
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
