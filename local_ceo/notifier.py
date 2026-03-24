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


async def _send_telegram_approval(action_id: str, action_desc: str, agent: str,
                                   priority: str, params: dict) -> int:
    """Envoie un message Telegram avec boutons inline Go/No-Go. Retourne le message_id."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return 0
    emoji = "🟠" if priority == "orange" else "🔴"
    # Resume des params utiles
    details = ""
    if params.get("username"):
        details += f"Target: @{params['username']}\n"
    if params.get("text"):
        details += f"Message: {params['text'][:200]}\n"
    if params.get("amount_usd"):
        details += f"Montant: ${params['amount_usd']:.2f}\n"

    text = (
        f"{emoji} *{priority.upper()}* — {action_desc}\n"
        f"Agent: {agent}\n"
        f"{details}\n"
        f"Reponds *Go* ou *No*"
    )
    keyboard = {
        "inline_keyboard": [[
            {"text": "✅ Go", "callback_data": f"approve:{action_id}"},
            {"text": "❌ No", "callback_data": f"deny:{action_id}"},
        ]]
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": text,
                    "parse_mode": "Markdown",
                    "reply_markup": keyboard,
                },
            )
            data = resp.json()
            return data.get("result", {}).get("message_id", 0)
    except Exception as e:
        print(f"[Notifier] Telegram approval send failed: {e}")
        return 0


async def _poll_telegram_approval(action_id: str, timeout_s: int) -> str:
    """Poll Telegram pour les callback queries (boutons) et messages texte.
    Retourne 'approved', 'denied', ou 'timeout'."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return "timeout"

    start = time.time()
    last_update_id = 0

    while time.time() - start < timeout_s:
        # Check dashboard approval (dict) BEFORE polling Telegram
        entry = _pending_approvals.get(action_id)
        if entry and entry.get("approved") is not None:
            result = "approved" if entry["approved"] else "denied"
            print(f"[Notifier] Dashboard: {result.upper()} ({action_id})")
            return result

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                # Short polling (5s) — permet de checker le dict entre les polls
                params = {"timeout": 5, "allowed_updates": ["callback_query", "message"]}
                if last_update_id:
                    params["offset"] = last_update_id + 1
                resp = await client.get(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates",
                    params=params,
                )
                updates = resp.json().get("result", [])

                for update in updates:
                    last_update_id = update.get("update_id", last_update_id)

                    # Check callback query (bouton clique)
                    cb = update.get("callback_query")
                    if cb:
                        data = cb.get("data", "")
                        cb_id = cb.get("id", "")
                        msg_id = cb.get("message", {}).get("message_id", 0)
                        chat_id = cb.get("message", {}).get("chat", {}).get("id", "")

                        if data == f"approve:{action_id}":
                            # Confirmer visuellement : modifier le message + popup
                            try:
                                await client.post(
                                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery",
                                    json={"callback_query_id": cb_id, "text": "✅ Approuve!"},
                                )
                                if msg_id and chat_id:
                                    await client.post(
                                        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText",
                                        json={
                                            "chat_id": chat_id,
                                            "message_id": msg_id,
                                            "text": f"✅ APPROUVE — {action_id}\n\nAction en cours...",
                                        },
                                    )
                            except Exception:
                                pass
                            print(f"[Notifier] Telegram: APPROVED by button ({action_id})")
                            return "approved"

                        elif data == f"deny:{action_id}":
                            try:
                                await client.post(
                                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery",
                                    json={"callback_query_id": cb_id, "text": "❌ Refuse!"},
                                )
                                if msg_id and chat_id:
                                    await client.post(
                                        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText",
                                        json={
                                            "chat_id": chat_id,
                                            "message_id": msg_id,
                                            "text": f"❌ REFUSE — {action_id}\n\nAction annulee.",
                                        },
                                    )
                            except Exception:
                                pass
                            print(f"[Notifier] Telegram: DENIED by button ({action_id})")
                            return "denied"

                        else:
                            # Bouton d'un autre action_id — ignorer
                            try:
                                await client.post(
                                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery",
                                    json={"callback_query_id": cb_id},
                                )
                            except Exception:
                                pass

                    # Check text message (Go / No)
                    msg = update.get("message", {})
                    chat_id = str(msg.get("chat", {}).get("id", ""))
                    text = msg.get("text", "").strip().lower()
                    if chat_id == str(TELEGRAM_CHAT_ID) and text:
                        if text in ("go", "yes", "oui", "ok", "approve", f"approve {action_id}"):
                            print(f"[Notifier] Telegram: APPROVED by text ({action_id})")
                            return "approved"
                        elif text in ("no", "non", "stop", "deny", "refuse"):
                            print(f"[Notifier] Telegram: DENIED by text ({action_id})")
                            return "denied"

        except Exception as e:
            print(f"[Notifier] Telegram poll error: {e}")
            await asyncio.sleep(5)

    return "timeout"


async def request_approval(action_id: str, decision: dict) -> str:
    """Demande une approbation humaine via Telegram (boutons Go/No-Go).

    Returns: "auto" | "human" | "timeout" | "denied"
    """
    priority = decision.get("priority", "orange").lower()
    amount = decision.get("params", {}).get("amount_usd", 0)

    if priority == "vert":
        return "auto"

    timeout = APPROVAL_TIMEOUT_ORANGE_S if priority == "orange" else APPROVAL_TIMEOUT_ROUGE_S

    action_desc = decision.get("action", "unknown")
    agent = decision.get("agent", "?")
    params = decision.get("params", {})

    # Enregistrer dans la file
    _pending_approvals[action_id] = {
        "decision": decision,
        "approved": None,
        "timestamp": time.time(),
    }

    # Envoyer message Telegram avec boutons Go/No-Go
    msg_id = await _send_telegram_approval(action_id, action_desc, agent, priority, params)

    if msg_id:
        # Poll Telegram pour la reponse (bouton ou texte)
        result = await _poll_telegram_approval(action_id, timeout)
        _pending_approvals.pop(action_id, None)

        if result == "approved":
            return "human"
        elif result == "denied":
            return "denied"
        # timeout → continue ci-dessous
    else:
        # Telegram failed, notify desktop + Discord
        await notify_desktop(f"Approbation {priority.upper()}", f"{action_desc} — {agent}")
        await notify_discord(f"Approbation requise", f"{action_desc} par {agent}", priority)
        # Wait with basic poll
        start = time.time()
        while time.time() - start < timeout:
            entry = _pending_approvals.get(action_id)
            if entry and entry["approved"] is not None:
                _pending_approvals.pop(action_id, None)
                return "human" if entry["approved"] else "denied"
            await asyncio.sleep(10)

    # Timeout
    _pending_approvals.pop(action_id, None)
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
