"""Notifier — Notifications desktop + Discord + Telegram pour approbation humaine."""
import asyncio
import time
import httpx
from config_local import (
    DISCORD_WEBHOOK_URL, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
    APPROVAL_TIMEOUT_ORANGE_S, APPROVAL_TIMEOUT_ROUGE_S,
    AUTO_EXECUTE_MAX_USD,
)

# File d'attente d'approbation
_pending_approvals: dict = {}  # {action_id: {decision, approved, timestamp}}


async def notify_desktop(title: str, message: str):
    """Notification native Windows via plyer."""
    try:
        from plyer import notification
        await asyncio.to_thread(
            notification.notify,
            title=f"CEO MAXIA: {title}"[:64],
            message=message[:256],
            timeout=10,
        )
    except Exception as e:
        print(f"[Notifier] Desktop notification failed: {e}")


async def notify_discord(title: str, message: str, priority: str = "vert"):
    """Envoie une alerte Discord via webhook."""
    if not DISCORD_WEBHOOK_URL:
        return
    color_map = {"vert": 0x00FF00, "orange": 0xFF8C00, "rouge": 0xFF0000}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(DISCORD_WEBHOOK_URL, json={
                "embeds": [{
                    "title": f"CEO MAXIA — {title}",
                    "description": message[:2000],
                    "color": color_map.get(priority, 0x808080),
                }],
            })
    except Exception as e:
        print(f"[Notifier] Discord failed: {e}")


async def notify_telegram(title: str, message: str):
    """Envoie un message Telegram."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": f"*CEO MAXIA — {title}*\n\n{message[:3000]}",
                    "parse_mode": "Markdown",
                },
            )
    except Exception as e:
        print(f"[Notifier] Telegram failed: {e}")


async def notify_all(title: str, message: str, priority: str = "vert"):
    """Notifie sur tous les canaux."""
    await asyncio.gather(
        notify_desktop(title, message),
        notify_discord(title, message, priority),
        notify_telegram(title, message),
        return_exceptions=True,
    )


async def request_approval(action_id: str, decision: dict) -> str:
    """Demande une approbation humaine pour une decision ORANGE/ROUGE.

    Returns: "auto" | "human" | "timeout" | "denied"
    """
    priority = decision.get("priority", "orange").lower()
    amount = decision.get("params", {}).get("amount_usd", 0)

    if priority == "vert":
        return "auto"

    timeout = APPROVAL_TIMEOUT_ORANGE_S if priority == "orange" else APPROVAL_TIMEOUT_ROUGE_S

    # Notifier
    action_desc = decision.get("action", "unknown")
    agent = decision.get("agent", "?")
    msg = (
        f"Action: {action_desc}\n"
        f"Agent: {agent}\n"
        f"Priorite: {priority.upper()}\n"
        f"Montant: ${amount:.2f}\n\n"
        f"Timeout: {timeout // 60} min\n"
        f"Repondre 'approve {action_id}' pour valider."
    )
    await notify_all(f"Approbation requise [{priority.upper()}]", msg, priority)

    # Enregistrer dans la file
    _pending_approvals[action_id] = {
        "decision": decision,
        "approved": None,
        "timestamp": time.time(),
    }

    # Attendre l'approbation (poll toutes les 10s)
    start = time.time()
    while time.time() - start < timeout:
        entry = _pending_approvals.get(action_id)
        if entry and entry["approved"] is not None:
            del _pending_approvals[action_id]
            return "human" if entry["approved"] else "denied"
        await asyncio.sleep(10)

    # Timeout
    del _pending_approvals[action_id]
    if priority == "orange" and amount <= AUTO_EXECUTE_MAX_USD:
        return "timeout"  # auto-execute pour orange sous seuil
    elif priority == "rouge":
        return "denied"  # ROUGE = jamais auto-execute
    return "timeout"


def approve_action(action_id: str, approved: bool = True):
    """Appeler depuis l'exterieur pour approuver/refuser une action."""
    if action_id in _pending_approvals:
        _pending_approvals[action_id]["approved"] = approved
        return True
    return False


def get_pending_approvals() -> list:
    """Liste les approbations en attente."""
    return [
        {"id": k, "action": v["decision"].get("action", "?"),
         "priority": v["decision"].get("priority", "?"),
         "waiting_since": v["timestamp"]}
        for k, v in _pending_approvals.items()
    ]
