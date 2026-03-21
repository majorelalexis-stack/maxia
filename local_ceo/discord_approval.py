"""Discord Approval — Envoie des demandes d'approbation sur Discord et lit les reponses.

Le CEO envoie un message avec l'action ORANGE/ROUGE.
Le fondateur repond "ok", "approve", "oui" ou "deny", "non", "refuse".
Le bot poll les messages recents pour trouver la reponse.
"""
import asyncio
import time
import httpx
import os
from config_local import DISCORD_WEBHOOK_URL

_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
_CHANNEL_ID = ""  # Extrait du webhook URL

# Extraire le channel ID du webhook URL
if DISCORD_WEBHOOK_URL:
    # Webhook URL: https://discord.com/api/webhooks/{webhook_id}/{token}
    # On ne peut pas extraire le channel_id du webhook, il faut le configurer
    _CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID", "")

# Si pas de bot token dans local .env, essayer le backend .env
if not _BOT_TOKEN:
    try:
        backend_env = os.path.join(os.path.dirname(__file__), "..", "backend", ".env")
        if os.path.exists(backend_env):
            for line in open(backend_env):
                if line.startswith("DISCORD_BOT_TOKEN="):
                    _BOT_TOKEN = line.strip().split("=", 1)[1]
                    break
    except Exception:
        pass


async def send_approval_request(action_id: str, action: str, agent: str,
                                 priority: str, details: str = "") -> str:
    """Envoie une demande d'approbation sur Discord et attend la reponse.

    Returns: "approved" | "denied" | "timeout"
    """
    if not DISCORD_WEBHOOK_URL:
        return "timeout"

    # Envoyer le message d'approbation
    color = 0xFF8C00 if priority == "orange" else 0xFF0000
    timeout_min = 30 if priority == "orange" else 120

    message = {
        "embeds": [{
            "title": f"APPROBATION REQUISE [{priority.upper()}]",
            "description": (
                f"**Action:** {action}\n"
                f"**Agent:** {agent}\n"
                f"**ID:** `{action_id}`\n"
                f"{details[:500]}\n\n"
                f"Repondez **ok** ou **oui** pour approuver\n"
                f"Repondez **non** ou **deny** pour refuser\n\n"
                f"Timeout: {timeout_min} min"
            ),
            "color": color,
        }],
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(DISCORD_WEBHOOK_URL, json=message)
    except Exception as e:
        print(f"[Discord] Send error: {e}")
        return "timeout"

    # Si pas de bot token ou channel ID, on ne peut pas lire les reponses
    if not _BOT_TOKEN or not _CHANNEL_ID:
        # Fallback: attendre le timeout puis auto-approve les ORANGE < $5
        print(f"[Discord] No bot token/channel — waiting {timeout_min} min then auto-decide")
        await asyncio.sleep(min(timeout_min * 60, 300))  # Max 5 min d'attente
        return "timeout"

    # Poll les messages Discord pour trouver la reponse
    start = time.time()
    timeout_s = timeout_min * 60

    while time.time() - start < timeout_s:
        try:
            response = await _check_discord_response(action_id)
            if response:
                return response
        except Exception:
            pass
        await asyncio.sleep(15)  # Check toutes les 15s

    return "timeout"


async def _check_discord_response(action_id: str) -> str | None:
    """Verifie si le fondateur a repondu sur Discord."""
    if not _BOT_TOKEN or not _CHANNEL_ID:
        return None

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"https://discord.com/api/v10/channels/{_CHANNEL_ID}/messages?limit=10",
                headers={"Authorization": f"Bot {_BOT_TOKEN}"},
            )
            if resp.status_code != 200:
                return None

            messages = resp.json()
            for msg in messages:
                # Ignorer les messages du bot
                if msg.get("author", {}).get("bot"):
                    continue

                content = msg.get("content", "").lower().strip()
                ts = msg.get("timestamp", "")

                # Verifier si c'est recent (< 5 min)
                # Les messages Discord sont en ISO format
                try:
                    from datetime import datetime, timezone
                    msg_time = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    age = (datetime.now(timezone.utc) - msg_time).total_seconds()
                    if age > 300:  # Plus de 5 min = trop vieux
                        continue
                except Exception:
                    continue

                # Chercher une approbation
                if content in ("ok", "oui", "yes", "approve", "approved", "go", "valide"):
                    return "approved"
                elif content in ("non", "no", "deny", "denied", "refuse", "stop", "annule"):
                    return "denied"

    except Exception:
        pass

    return None


async def get_discord_channel_id() -> str:
    """Recupere le channel ID du premier channel texte du serveur."""
    if not _BOT_TOKEN:
        return ""
    # On ne peut pas le faire sans le guild_id
    # Le fondateur doit le configurer dans .env : DISCORD_CHANNEL_ID=xxxxx
    return _CHANNEL_ID
