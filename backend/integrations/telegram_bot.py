"""MAXIA — Telegram Bot (minimal, httpx-only, zero dependencies).

Repond a /start, configure le Menu Button pour la Mini App,
et gere les commandes de base. Long polling dans un background task.

Commandes:
  /start     — Message de bienvenue + bouton Mini App
  /price SOL — Prix live
  /help      — Liste des commandes
"""
import asyncio
import json
import logging
import os
import time

import httpx

logger = logging.getLogger("telegram_bot")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
MINIAPP_URL = "https://maxiaworld.app/miniapp"
_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"
_last_update_id = 0
_running = False
_client: httpx.AsyncClient = None


async def _get_client() -> httpx.AsyncClient:
    """Get or create a dedicated httpx client for the bot."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=40)
    return _client


async def _tg_api(method: str, data: dict = None) -> dict:
    """Call Telegram Bot API."""
    client = await _get_client()
    try:
        if data:
            resp = await client.post(f"{_BASE}/{method}", json=data, timeout=15)
        else:
            resp = await client.get(f"{_BASE}/{method}", timeout=15)
        return resp.json()
    except Exception as e:
        print(f"[TG Bot] API error {method}: {e}")
        return {}


async def _send_message(chat_id: int, text: str, reply_markup: dict = None):
    """Send a text message."""
    data = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        data["reply_markup"] = reply_markup
    return await _tg_api("sendMessage", data)


async def _handle_start(chat_id: int, first_name: str):
    """Handle /start command — welcome + Mini App button."""
    text = (
        f"Welcome to <b>MAXIA</b>, {first_name}!\n\n"
        "AI-powered trading across 15 blockchains.\n\n"
        "What you can do:\n"
        "  /price SOL — Live price\n"
        "  /help — All commands\n\n"
        "Or tap the button below to open the full trading app:"
    )
    markup = {
        "inline_keyboard": [[
            {"text": "Open MAXIA Trading", "web_app": {"url": MINIAPP_URL}},
        ], [
            {"text": "Buy Crypto with Card", "url": "https://maxiaworld.app/buy"},
        ], [
            {"text": "Token Sniper", "url": "https://maxiaworld.app/sniper"},
        ]]
    }
    await _send_message(chat_id, text, markup)


async def _handle_price(chat_id: int, args: str):
    """Handle /price command."""
    symbol = args.strip().upper() if args else "SOL"
    try:
        client = await _get_client()
        resp = await client.get(f"http://127.0.0.1:8000/oracle/price/live/{symbol}", timeout=5)
        d = resp.json()
        if d.get("price", 0) > 0:
            price = d["price"]
            source = d.get("source", "oracle")
            await _send_message(chat_id, f"<b>{symbol}</b>: ${price:,.4f}\nSource: {source}")
        else:
            await _send_message(chat_id, f"Could not fetch price for {symbol}")
    except Exception as e:
        print(f"[TG Bot] Price error: {e}")
        await _send_message(chat_id, f"Error fetching price for {symbol}")


async def _handle_help(chat_id: int):
    """Handle /help command."""
    text = (
        "<b>MAXIA Bot Commands:</b>\n\n"
        "/start — Welcome + Mini App\n"
        "/price SOL — Live token price\n"
        "/price ETH — Live ETH price\n"
        "/price BTC — Live BTC price\n"
        "/help — This help message\n\n"
        "Or open the full trading app with the Menu button."
    )
    markup = {
        "inline_keyboard": [[
            {"text": "Open MAXIA Trading", "web_app": {"url": MINIAPP_URL}},
        ]]
    }
    await _send_message(chat_id, text, markup)


async def _process_update(update: dict):
    """Process a single Telegram update."""
    msg = update.get("message", {})
    text = msg.get("text", "")
    chat_id = msg.get("chat", {}).get("id")
    first_name = msg.get("from", {}).get("first_name", "there")

    print(f"[TG Bot] Update: chat_id={chat_id} text={text[:50] if text else 'none'}")

    if not chat_id or not text:
        return

    text_lower = text.strip().lower()

    if text_lower == "/start" or text_lower.startswith("/start "):
        await _handle_start(chat_id, first_name)
    elif text_lower.startswith("/price"):
        args = text[6:].strip()
        await _handle_price(chat_id, args)
    elif text_lower == "/help":
        await _handle_help(chat_id)
    else:
        # Forward to chat handler for NL processing
        try:
            client = await _get_client()
            resp = await client.post(
                "http://127.0.0.1:8000/api/chat",
                json={"message": text},
                timeout=10,
            )
            d = resp.json()
            response = d.get("response", "")
            if response:
                await _send_message(chat_id, response)
        except Exception:
            pass


async def setup_bot_menu():
    """Configure the bot's menu button to open the Mini App."""
    if not BOT_TOKEN:
        return

    # Set the Menu Button for all chats
    await _tg_api("setChatMenuButton", {
        "menu_button": {
            "type": "web_app",
            "text": "MAXIA Trading",
            "web_app": {"url": MINIAPP_URL},
        }
    })

    # Set bot commands
    await _tg_api("setMyCommands", {
        "commands": [
            {"command": "start", "description": "Welcome + open Mini App"},
            {"command": "price", "description": "Live token price (e.g. /price SOL)"},
            {"command": "help", "description": "List all commands"},
        ]
    })

    print("[TG Bot] Menu button + commands configured")


async def run_telegram_bot():
    """Long polling loop — runs as a background task."""
    global _last_update_id, _running

    if not BOT_TOKEN:
        print("[TG Bot] TELEGRAM_BOT_TOKEN not set — bot disabled")
        return

    if _running:
        print("[TG Bot] Already running — skipping")
        return

    _running = True

    # Configure menu button on startup
    await setup_bot_menu()

    print("[TG Bot] Starting long polling...")

    while True:
        try:
            client = await _get_client()
            resp = await client.get(
                f"{_BASE}/getUpdates",
                params={"offset": _last_update_id + 1, "timeout": 30, "limit": 20},
                timeout=35,
            )
            data = resp.json()
            updates = data.get("result", [])

            for update in updates:
                update_id = update.get("update_id", 0)
                if update_id > _last_update_id:
                    _last_update_id = update_id
                try:
                    await _process_update(update)
                except Exception as e:
                    print(f"[TG Bot] Update processing error: {e}")

        except Exception as e:
            print(f"[TG Bot] Polling error: {e}")
            await asyncio.sleep(5)
