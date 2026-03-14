"""MAXIA Alertes V11 — Notifications Discord en francais"""
import os, time, json
import httpx
from config import DISCORD_WEBHOOK_URL

_last_alert: dict = {}
_COOLDOWN = 300


async def send_discord(title: str, message: str, color: int = 0x7C6BF8,
                       urgent: bool = False) -> bool:
    """Envoie une alerte Discord via webhook."""
    if not DISCORD_WEBHOOK_URL:
        print(f"[Alertes] Discord non configure — {title}: {message}")
        return False

    key = title
    now = time.time()
    if key in _last_alert and now - _last_alert[key] < _COOLDOWN and not urgent:
        return False
    _last_alert[key] = now

    embed = {
        "title": f"{'🚨 ' if urgent else '🤖 '}{title}",
        "description": message,
        "color": color,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "footer": {"text": "MAXIA V12 — CEO AI + Marketplace"},
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                DISCORD_WEBHOOK_URL,
                json={"embeds": [embed]},
            )
        if resp.status_code in (200, 204):
            print(f"[Alertes] Discord envoye: {title}")
            return True
        print(f"[Alertes] Discord erreur {resp.status_code}")
        return False
    except Exception as e:
        print(f"[Alertes] Discord erreur: {e}")
        return False


async def alert_low_balance(balance: float, wallet: str):
    """Alerte quand la reserve est basse."""
    await send_discord(
        "⚠️ RESERVE BASSE",
        f"Le wallet `{wallet[:8]}...` n a plus que **{balance:.4f} SOL**\n"
        f"Rechargez le wallet pour eviter l arret de l agent marketing.",
        color=0xFF4560, urgent=True,
    )


async def alert_daily_report(stats: dict):
    """Rapport quotidien en francais."""
    await send_discord(
        "📊 Rapport Quotidien MAXIA",
        f"**Profits nets :** {stats.get('profits', 0):.2f} USDC\n"
        f"**Revenus du mois :** {stats.get('monthly_revenue', 0):.2f} USDC\n"
        f"**Depenses du mois :** {stats.get('monthly_spend', 0):.2f} USDC\n"
        f"**Prospects contactes aujourd hui :** {stats.get('prospects', 0)}\n"
        f"**Prospects total :** {stats.get('total_prospects', 0)}\n"
        f"**Conversions :** {stats.get('conversions', 0)}\n"
        f"**Tresorerie :** {stats.get('treasury_balance', 0):.2f} USDC\n"
        f"**Volume 24h :** {stats.get('volume_24h', 0):.2f} USDC\n"
        f"**Services actifs :** {stats.get('listing_count', 0)}\n"
        f"**Mode :** {stats.get('tier', 'survie')}\n"
        f"**Uptime :** {stats.get('uptime', '0h 0m')}",
        color=0x00E5CC,
    )


async def alert_prospect_contacted(wallet: str, message_preview: str):
    """Notification quand un prospect est contacte."""
    await send_discord(
        "🎯 Nouveau prospect contacte",
        f"**Wallet :** `{wallet[:8]}...{wallet[-4:]}`\n"
        f"**Message envoye :** {message_preview[:150]}",
        color=0x00E676,
    )


async def alert_error(module: str, error: str):
    """Alerte sur erreur critique."""
    await send_discord(
        f"❌ Erreur — {module}",
        f"```\n{error[:500]}\n```",
        color=0xFF4560, urgent=True,
    )


async def alert_system(title: str, message: str):
    """Alerte systeme generique."""
    await send_discord(title, message, color=0x7C6BF8)


async def alert_escrow_created(amount: float, buyer: str, seller: str, service: str):
    """Notification nouvel escrow."""
    await send_discord(
        "💰 Nouvel Escrow cree",
        f"**Montant :** {amount:.2f} USDC\n"
        f"**Acheteur :** `{buyer[:8]}...`\n"
        f"**Vendeur :** `{seller[:8]}...`\n"
        f"**Service :** {service}",
        color=0x7C6BF8,
    )


async def alert_escrow_released(amount: float, seller: str):
    """Notification paiement libere."""
    await send_discord(
        "✅ Paiement libere",
        f"**{amount:.2f} USDC** envoyes au vendeur `{seller[:8]}...`\n"
        f"Service livre avec succes.",
        color=0x00E676,
    )


async def alert_new_client(wallet: str, service: str, amount: float):
    """Notification nouveau client."""
    await send_discord(
        "🆕 Nouveau client",
        f"**Wallet :** `{wallet[:8]}...`\n"
        f"**Service :** {service}\n"
        f"**Montant :** {amount:.2f} USDC",
        color=0x00E5CC,
    )


async def alert_revenue(amount: float, source: str):
    """Notification revenu."""
    await send_discord(
        "💵 Revenu recu",
        f"**+{amount:.2f} USDC** depuis {source}",
        color=0x00E676,
    )


async def alert_swarm_clone(name: str, niche: str, price: float):
    """Notification nouveau clone de l essaim."""
    await send_discord(
        "🐝 Nouveau clone deploye",
        f"**Nom :** {name}\n"
        f"**Niche :** {niche}\n"
        f"**Prix :** {price:.2f} USDC/requete",
        color=0x7C6BF8,
    )
