"""Mission — Telegram Chat: interact with Alexis via @MAXIA_AI_bot.

Polls Telegram Bot API, responds via Chat agent, handles GO/NO approval buttons.
"""
import json
import logging
import os
import time
from datetime import datetime
from typing import Optional

import httpx

from agents import CHAT, MAXIA_KNOWLEDGE
from config_local import VPS_URL
from llm import ask

log = logging.getLogger("ceo")

_TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
_TELEGRAM_CEO_CHAT_ID = os.getenv("TELEGRAM_CEO_CHAT_ID", "")
_TELEGRAM_API = "https://api.telegram.org/bot"
_LOCAL_CEO_DIR = os.path.dirname(os.path.dirname(__file__))
_STATE_FILE = os.path.join(_LOCAL_CEO_DIR, "telegram_state.json")
_POLL_TIMEOUT = 5


def _load_state() -> dict:
    default = {"last_update_id": 0, "pending_approvals": []}
    try:
        if os.path.exists(_STATE_FILE):
            with open(_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.loads(f.read())
            for k, v in default.items():
                data.setdefault(k, v)
            return data
    except (json.JSONDecodeError, OSError) as e:
        log.error("[TELEGRAM] State load error: %s", e)
    return default


def _save_state(state: dict) -> None:
    try:
        with open(_STATE_FILE, "w", encoding="utf-8") as f:
            f.write(json.dumps(state, indent=2, default=str))
    except OSError as e:
        log.error("[TELEGRAM] State save error: %s", e)


def _is_configured() -> bool:
    return bool(_TELEGRAM_BOT_TOKEN and _TELEGRAM_CEO_CHAT_ID)


def _is_from_alexis(update: dict) -> bool:
    message = update.get("message") or update.get("callback_query", {}).get("message", {})
    return str(message.get("chat", {}).get("id", "")) == str(_TELEGRAM_CEO_CHAT_ID)


async def _telegram_request(method: str, data: Optional[dict] = None) -> Optional[dict]:
    url = f"{_TELEGRAM_API}{_TELEGRAM_BOT_TOKEN}/{method}"
    try:
        async with httpx.AsyncClient(timeout=_POLL_TIMEOUT + 10) as client:
            resp = await client.post(url, json=data) if data else await client.get(url)
            resp.raise_for_status()
            result = resp.json()
            if result.get("ok"):
                return result.get("result")
            log.warning("[TELEGRAM] API error: %s", result.get("description", "unknown"))
    except httpx.TimeoutException:
        pass
    except Exception as e:
        log.error("[TELEGRAM] Request error (%s): %s", method, e)
    return None


async def _send_message(text: str, reply_markup: Optional[dict] = None) -> Optional[dict]:
    data: dict = {"chat_id": _TELEGRAM_CEO_CHAT_ID, "text": text, "parse_mode": "Markdown"}
    if reply_markup:
        data["reply_markup"] = reply_markup
    return await _telegram_request("sendMessage", data)


async def _get_updates(offset: int) -> list[dict]:
    result = await _telegram_request("getUpdates", {
        "offset": offset, "timeout": _POLL_TIMEOUT,
        "allowed_updates": ["message", "callback_query"],
    })
    return result if isinstance(result, list) else []


async def _handle_text_message(text: str, mem: dict) -> str:
    """Generate a response using the Chat agent with recent context."""
    parts = []
    for key, label in [("tweets_posted", "Tweets"), ("health_alerts", "Alerts"), ("outreach_sent", "Outreach")]:
        items = mem.get(key, [])[-3:]
        if items:
            parts.append(f"{label}: {len(items)} recent")
    context = "\n".join(parts) if parts else "No recent activity."

    prompt = (
        f"Alexis wrote on Telegram: {text}\n\n"
        f"Recent CEO activity:\n{context}\n\n"
        f"Reply concisely in French. If he asks you to do something, confirm the action."
    )
    response = await ask(CHAT, prompt, knowledge=MAXIA_KNOWLEDGE[:2000])
    return response or "Je suis la, mais je n'ai pas pu generer de reponse. Reessaie."


async def _handle_callback_query(callback_data: str, callback_query_id: str, state: dict) -> None:
    """Handle GO/NO button presses for approval flows."""
    await _telegram_request("answerCallbackQuery", {"callback_query_id": callback_query_id})

    parts = callback_data.split(":", 1)
    if len(parts) != 2:
        await _send_message("Format de callback invalide.")
        return

    action_type, action_id = parts[0], parts[1]
    pending = state.get("pending_approvals", [])
    matched = [p for p in pending if p.get("id") == action_id]
    if not matched:
        await _send_message(f"Action `{action_id}` non trouvee ou deja traitee.")
        return

    approved = action_type == "approve"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{VPS_URL}/api/ceo/approval-result",
                json={"action_id": action_id, "approved": approved},
            )
            label = "approuvee" if approved else "rejetee"
            name = matched[0].get("name", action_id)
            if approved and resp.status_code != 200:
                await _send_message(f"Erreur VPS: HTTP {resp.status_code}")
            else:
                await _send_message(f"Action `{name}` {label}.")
    except Exception as e:
        await _send_message(f"Erreur: {str(e)[:100]}")

    state["pending_approvals"] = [p for p in pending if p.get("id") != action_id]


async def request_approval(action_name: str, action_id: str, description: str, level: str = "ORANGE") -> None:
    """Send an approval request to Alexis via Telegram with GO/NO buttons."""
    if not _is_configured():
        log.warning("[TELEGRAM] Not configured — cannot request approval")
        return
    text = (f"[{level}] Approbation requise\n\n*Action:* {action_name}\n"
            f"*Description:* {description}\n*Niveau:* {level}\n\nApprouver cette action?")
    reply_markup = {"inline_keyboard": [[
        {"text": "GO", "callback_data": f"approve:{action_id}"},
        {"text": "NO", "callback_data": f"reject:{action_id}"},
    ]]}
    await _send_message(text, reply_markup)

    state = _load_state()
    state["pending_approvals"].append({
        "id": action_id, "name": action_name, "description": description,
        "level": level, "requested_at": datetime.now().isoformat(),
    })
    cutoff = datetime.fromtimestamp(time.time() - 86400).isoformat()
    state["pending_approvals"] = [p for p in state["pending_approvals"] if p.get("requested_at", "") >= cutoff]
    _save_state(state)


async def mission_telegram_chat(mem: dict, actions: dict) -> None:
    """Poll Telegram for messages from Alexis and respond via Chat agent."""
    if not _is_configured():
        log.debug("[TELEGRAM] Bot not configured — skip")
        return

    state = _load_state()
    offset = state.get("last_update_id", 0) + 1

    # Poll for updates
    updates = await _get_updates(offset)
    if not updates:
        return

    processed = 0
    for update in updates:
        update_id = update.get("update_id", 0)
        state["last_update_id"] = max(state["last_update_id"], update_id)

        # Security: only process messages from Alexis
        if not _is_from_alexis(update):
            continue

        # Handle callback queries (approval buttons)
        if "callback_query" in update:
            cb = update["callback_query"]
            await _handle_callback_query(
                callback_data=cb.get("data", ""),
                callback_query_id=cb.get("id", ""),
                state=state,
            )
            processed += 1
            continue

        # Handle text messages
        message = update.get("message", {})
        text = message.get("text", "").strip()
        if not text:
            continue

        # Built-in commands
        if text.lower() == "/status":
            tweet_count = len(mem.get("tweets_posted", []))
            alerts = len(mem.get("health_alerts", []))
            outreach = len(mem.get("outreach_sent", []))
            status_text = (
                f"*MAXIA CEO Status*\n\n"
                f"Tweets postes: {tweet_count}\n"
                f"Alertes sante: {alerts}\n"
                f"Emails envoyes: {outreach}\n"
                f"Dernier check: {datetime.now().strftime('%H:%M')}"
            )
            await _send_message(status_text)
        elif text.lower() == "/metrics":
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get(f"{VPS_URL}/health")
                    if resp.status_code == 200:
                        health = resp.json()
                        await _send_message(f"*VPS Health:*\n```\n{json.dumps(health, indent=2)[:500]}\n```")
                    else:
                        await _send_message(f"VPS Health: HTTP {resp.status_code}")
            except Exception as e:
                await _send_message(f"Erreur metrics: {str(e)[:100]}")
        else:
            # General chat — use Chat agent
            response = await _handle_text_message(text, mem)
            await _send_message(response)

        processed += 1

        # Log in memory
        mem.setdefault("telegram_messages", []).append({
            "date": datetime.now().isoformat(),
            "from": "alexis",
            "text": text[:200],
        })

    # Trim telegram message history
    if len(mem.get("telegram_messages", [])) > 200:
        mem["telegram_messages"] = mem["telegram_messages"][-200:]

    _save_state(state)

    if processed > 0:
        log.info("[TELEGRAM] Processed %d update(s)", processed)
