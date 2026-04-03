"""MAXIA Alertes V12 — Alertes sensibles -> Telegram prive, systeme -> Discord public"""
import logging
import os, time, json
import httpx
from core.config import DISCORD_WEBHOOK_URL, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from core.http_client import get_http_client

logger = logging.getLogger(__name__)

_last_alert: dict = {}
_COOLDOWN = 300


# ══════════════════════════════════════════
# TRANSPORT — Telegram prive (fondateur) + Discord public
# ══════════════════════════════════════════

async def _send_private(text: str, urgent: bool = False) -> bool:
    """Envoie un message au chat prive Telegram du fondateur (MAXIA CEO ALERTS)."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning(f"Telegram prive non configure — {text[:100]}")
        return False

    key = text[:50]
    now = time.time()
    if key in _last_alert and now - _last_alert[key] < _COOLDOWN and not urgent:
        return False
    _last_alert[key] = now

    try:
        client = get_http_client()
        resp = await client.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text[:4000],
                "parse_mode": "HTML",
            },
            timeout=10,
        )
        if resp.status_code == 200:
            return True
        logger.error(f"Telegram prive erreur {resp.status_code}")
        return False
    except Exception as e:
        logger.error(f"Telegram prive erreur: {e}")
        return False


async def _send_discord(title: str, message: str, color: int = 0x7C6BF8) -> bool:
    """Envoie une alerte sur Discord public (systeme uniquement)."""
    if not DISCORD_WEBHOOK_URL:
        return False

    key = title
    now = time.time()
    if key in _last_alert and now - _last_alert[key] < _COOLDOWN:
        return False
    _last_alert[key] = now

    embed = {
        "title": f"\U0001f916 {title}",
        "description": message[:2000],
        "color": color,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "footer": {"text": "MAXIA V12"},
    }
    try:
        client = get_http_client()
        resp = await client.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]}, timeout=10)
        return resp.status_code in (200, 204)
    except Exception:
        return False


# ══════════════════════════════════════════
# ALERTES SENSIBLES — Telegram prive uniquement
# ══════════════════════════════════════════

async def alert_low_balance(balance: float, wallet: str):
    await _send_private(
        f"\u26a0\ufe0f <b>RESERVE BASSE</b>\n\n"
        f"Wallet <code>{wallet[:8]}...</code> : <b>{balance:.4f} SOL</b>\n"
        f"Rechargez pour eviter l'arret du marketing.",
        urgent=True,
    )


async def alert_daily_report(stats: dict):
    await _send_private(
        f"\U0001f4ca <b>Rapport Quotidien MAXIA</b>\n\n"
        f"Profits nets : <b>{stats.get('profits', 0):.2f} USDC</b>\n"
        f"Revenus du mois : <b>{stats.get('monthly_revenue', 0):.2f} USDC</b>\n"
        f"Depenses du mois : <b>{stats.get('monthly_spend', 0):.2f} USDC</b>\n"
        f"Prospects aujourd'hui : <b>{stats.get('prospects', 0)}</b>\n"
        f"Prospects total : <b>{stats.get('total_prospects', 0)}</b>\n"
        f"Conversions : <b>{stats.get('conversions', 0)}</b>\n"
        f"Tresorerie : <b>{stats.get('treasury_balance', 0):.2f} USDC</b>\n"
        f"Volume 24h : <b>{stats.get('volume_24h', 0):.2f} USDC</b>\n"
        f"Services actifs : <b>{stats.get('listing_count', 0)}</b>\n"
        f"Mode : <b>{stats.get('tier', 'survie')}</b>\n"
        f"Uptime : <b>{stats.get('uptime', '0h 0m')}</b>",
    )


async def alert_prospect_contacted(wallet: str, message_preview: str):
    await _send_private(
        f"\U0001f3af <b>Nouveau prospect contacte</b>\n\n"
        f"Wallet : <code>{wallet[:8]}...{wallet[-4:]}</code>\n"
        f"Message : {message_preview[:150]}",
    )


async def alert_error(module: str, error: str):
    await _send_private(
        f"\u274c <b>Erreur — {module}</b>\n\n"
        f"<code>{error[:500]}</code>",
        urgent=True,
    )


async def alert_escrow_created(amount: float, buyer: str, seller: str, service: str):
    await _send_private(
        f"\U0001f4b0 <b>Nouvel Escrow</b>\n\n"
        f"Montant : <b>{amount:.2f} USDC</b>\n"
        f"Acheteur : <code>{buyer[:8]}...</code>\n"
        f"Vendeur : <code>{seller[:8]}...</code>\n"
        f"Service : {service}",
    )


async def alert_escrow_released(amount: float, seller: str):
    await _send_private(
        f"\u2705 <b>Paiement libere</b>\n\n"
        f"<b>{amount:.2f} USDC</b> envoyes au vendeur <code>{seller[:8]}...</code>",
    )


async def alert_new_client(wallet: str, service: str, amount: float):
    await _send_private(
        f"\U0001f195 <b>Nouveau client</b>\n\n"
        f"Wallet : <code>{wallet[:8]}...</code>\n"
        f"Service : {service}\n"
        f"Montant : <b>{amount:.2f} USDC</b>",
    )


async def alert_revenue(amount: float, source: str):
    await _send_private(
        f"\U0001f4b5 <b>Revenu recu</b>\n\n"
        f"<b>+{amount:.2f} USDC</b> depuis {source}",
    )


async def alert_swarm_clone(name: str, niche: str, price: float):
    await _send_private(
        f"\U0001f41d <b>Nouveau clone deploye</b>\n\n"
        f"Nom : {name}\nNiche : {niche}\nPrix : {price:.2f} USDC/req",
    )


# ══════════════════════════════════════════
# ALERTES SYSTEME — Discord public (pas de donnees sensibles)
# ══════════════════════════════════════════

async def alert_system(title: str, message: str):
    """Alerte systeme publique (startup, status). Pas de chiffres business."""
    await _send_discord(title, message, color=0x7C6BF8)


# ══════════════════════════════════════════
# COMPAT — ancien nom utilise par d'autres modules
# ══════════════════════════════════════════

send_discord = _send_discord
