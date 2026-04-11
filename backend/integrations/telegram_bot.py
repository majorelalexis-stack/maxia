"""MAXIA — Telegram Bot (httpx-only, multilingue 13 langues — Plan CEO V7).

Repond a /start, configure le Menu Button pour la Mini App,
et gere les commandes de base. Long polling dans un background task.

Commandes:
  /start     — Message de bienvenue + bouton Mini App (localise)
  /price SOL — Prix live (localise)
  /help      — Liste des commandes (localise)

Plan CEO V7 / P4A: multilingue static via telegram_i18n (13 langues,
zero latence, zero LLM call pour les messages fixes).
"""
import asyncio
import logging
import os

import httpx

from integrations.telegram_i18n import (
    build_help_text,
    build_price_text,
    build_welcome_text,
    detect_lang,
    t,
)

logger = logging.getLogger("telegram_bot")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
# Disabled by default 2026-04-11: the local CEO's telegram_router is
# now the single Telegram long-poller for the @MAXIA_AI_bot token. Two
# pollers on the same token fight for getUpdates and return 409
# Conflict, breaking every approval flow. Set TELEGRAM_BOT_ENABLED=1
# explicitly if you decide to hand Telegram polling back to the VPS.
BOT_ENABLED = os.getenv("TELEGRAM_BOT_ENABLED", "0") == "1"
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


async def close_bot_client():
    """Close the bot's httpx client on shutdown."""
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None


async def _tg_api(method: str, data: dict = None) -> dict:
    """Call Telegram Bot API."""
    client = await _get_client()
    try:
        if data:
            resp = await client.post(f"{_BASE}/{method}", json=data, timeout=15)
        else:
            resp = await client.get(f"{_BASE}/{method}", timeout=15)
        if resp.status_code != 200:
            logger.warning("[TG Bot] HTTP %s on %s", resp.status_code, method)
        try:
            return resp.json()
        except Exception:
            logger.warning("[TG Bot] Non-JSON response on %s: %s", method, resp.text[:100])
            return {}
    except Exception as e:
        logger.warning("[TG Bot] API error %s: %s", method, e)
        return {}


async def _send_message(chat_id: int, text: str, reply_markup: dict = None):
    """Send a text message."""
    data = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        data["reply_markup"] = reply_markup
    return await _tg_api("sendMessage", data)


async def _handle_start(chat_id: int, first_name: str, lang: str = "en"):
    """Handle /start command — welcome + Mini App button (localized)."""
    text = build_welcome_text(lang=lang, name=first_name)
    markup = {
        "inline_keyboard": [[
            {"text": t("button.open_trading", lang), "web_app": {"url": MINIAPP_URL}},
        ], [
            {"text": t("button.buy_crypto", lang), "web_app": {"url": "https://maxiaworld.app/buy"}},
        ], [
            {"text": t("button.sniper", lang), "web_app": {"url": "https://maxiaworld.app/sniper"}},
        ]]
    }
    await _send_message(chat_id, text, markup)


async def _handle_price(chat_id: int, args: str, lang: str = "en"):
    """Handle /price command (localized response)."""
    symbol = args.strip().upper() if args else "SOL"
    try:
        client = await _get_client()
        resp = await client.get(
            f"http://127.0.0.1:8001/oracle/price/live/{symbol}",
            timeout=5,
        )
        d = resp.json()
        price = d.get("price", 0)
        source = d.get("source", "oracle")
        await _send_message(
            chat_id,
            build_price_text(lang=lang, symbol=symbol, price=price, source=source),
        )
    except Exception as e:
        logger.warning("[TG Bot] Price error for %s: %s", symbol, e)
        await _send_message(
            chat_id,
            build_price_text(lang=lang, symbol=symbol, price=None, source=""),
        )


async def _handle_help(chat_id: int, lang: str = "en"):
    """Handle /help command (localized)."""
    text = build_help_text(lang=lang)
    markup = {
        "inline_keyboard": [[
            {"text": t("button.open_trading", lang), "web_app": {"url": MINIAPP_URL}},
        ]]
    }
    await _send_message(chat_id, text, markup)


async def _process_update(update: dict):
    """Process a single Telegram update."""
    msg = update.get("message", {})
    text = msg.get("text", "")
    chat_id = msg.get("chat", {}).get("id")
    from_user = msg.get("from", {}) or {}
    first_name = from_user.get("first_name", "there")
    lang = detect_lang(from_user.get("language_code", ""))

    logger.info(
        "[TG Bot] Update: chat_id=%s lang=%s text=%s",
        chat_id, lang, text[:50] if text else "none",
    )

    if not chat_id or not text:
        return

    text_lower = text.strip().lower()

    if text_lower == "/start" or text_lower.startswith("/start "):
        await _handle_start(chat_id, first_name, lang)
    elif text_lower.startswith("/price"):
        args = text[6:].strip()
        await _handle_price(chat_id, args, lang)
    elif text_lower == "/help":
        await _handle_help(chat_id, lang)
    else:
        # Forward to chat handler for NL processing
        try:
            client = await _get_client()
            resp = await client.post(
                "http://127.0.0.1:8001/api/chat",
                json={"message": text, "lang": lang},
                timeout=10,
            )
            d = resp.json()
            response = d.get("response", "")
            if response:
                await _send_message(chat_id, response)
        except Exception as e:
            logger.warning("[TG Bot] Chat handler error: %s", e)


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

    logger.info("[TG Bot] Menu button + commands configured")


async def run_telegram_bot():
    """Long polling loop — runs as a background task."""
    global _last_update_id, _running

    if not BOT_ENABLED:
        logger.info(
            "[TG Bot] VPS Telegram poller disabled (TELEGRAM_BOT_ENABLED!=1). "
            "Local CEO telegram_router is the sole poller."
        )
        return

    if not BOT_TOKEN or len(BOT_TOKEN) < 20:
        logger.info("[TG Bot] TELEGRAM_BOT_TOKEN not set or invalid — bot disabled")
        return

    if _running:
        logger.info("[TG Bot] Already running — skipping")
        return

    _running = True

    # Verify token works
    me = await _tg_api("getMe")
    if not me.get("ok"):
        logger.error(
            "[TG Bot] Invalid token: %s",
            me.get("description", "unknown error"),
        )
        _running = False
        return

    bot_name = me.get("result", {}).get("username", "?")
    logger.info("[TG Bot] Bot verified: @%s", bot_name)

    # Configure menu button on startup
    await setup_bot_menu()

    logger.info("[TG Bot] Starting long polling...")

    consecutive_errors = 0
    while True:
        try:
            client = await _get_client()
            resp = await client.get(
                f"{_BASE}/getUpdates",
                params={"offset": _last_update_id + 1, "timeout": 30, "limit": 20},
                timeout=40,
            )
            data = resp.json()

            if not data.get("ok"):
                err = data.get("description", "")
                if "terminated by other" in err:
                    logger.warning("[TG Bot] Conflict: another instance polling. Retrying in 5s...")
                    await asyncio.sleep(5)
                    continue
                logger.warning("[TG Bot] API error: %s", err)
                consecutive_errors += 1
                if consecutive_errors > 10:
                    logger.warning("[TG Bot] Too many errors, pausing 60s")
                    await asyncio.sleep(60)
                    consecutive_errors = 0
                continue

            consecutive_errors = 0
            updates = data.get("result", [])

            for update in updates:
                update_id = update.get("update_id", 0)
                if update_id > _last_update_id:
                    _last_update_id = update_id
                try:
                    await _process_update(update)
                except Exception as e:
                    logger.warning("[TG Bot] Update error: %s", e)

        except asyncio.CancelledError:
            logger.info("[TG Bot] Shutting down")
            break
        except Exception as e:
            logger.warning("[TG Bot] Polling error: %s", e)
            await asyncio.sleep(5)
