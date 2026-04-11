"""Public slash commands for @MAXIA_AI_bot.

Ported from the VPS ``backend/integrations/telegram_bot.py`` handlers
that were disabled when the VPS poller was taken offline (2026-04-11).
The router now runs locally, so these handlers live here and are wired
from ``missions/telegram_chat.py::_process_one_update``.

Three commands are exposed to every user who DMs @MAXIA_AI_bot:

  * ``/start`` — welcome message + inline keyboard with Mini App buttons
    (Open Trading, Buy Crypto, Sniper).
  * ``/price [SYMBOL]`` — live price for a token (defaults to SOL),
    fetched from the VPS oracle.
  * ``/help`` — command list + Mini App button.

Localization is intentionally limited to FR and EN based on
``language_code`` to keep the module dependency-free. The original VPS
had a 13-language i18n module; porting it is out of scope for now.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import httpx

log = logging.getLogger("ceo.telegram_public")

_TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
_TELEGRAM_API = "https://api.telegram.org/bot"
_VPS_URL = os.getenv("VPS_URL", "https://maxiaworld.app").rstrip("/")
_MINIAPP_URL = f"{_VPS_URL}/miniapp"
_BUY_URL = f"{_VPS_URL}/buy"
_SNIPER_URL = f"{_VPS_URL}/sniper"


def _is_fr(lang_code: Optional[str]) -> bool:
    return (lang_code or "").lower().startswith("fr")


async def _tg_post(method: str, data: dict) -> Optional[dict]:
    if not _TELEGRAM_BOT_TOKEN:
        return None
    url = f"{_TELEGRAM_API}{_TELEGRAM_BOT_TOKEN}/{method}"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=data)
            if resp.status_code != 200:
                log.warning("[public] HTTP %s on %s: %s",
                            resp.status_code, method, resp.text[:200])
                return None
            result = resp.json()
            if not result.get("ok"):
                log.warning("[public] API error on %s: %s",
                            method, result.get("description", "unknown"))
                return None
            return result.get("result")
    except Exception as e:
        log.warning("[public] request error (%s): %s", method, e)
        return None


def _welcome_text(first_name: str, lang_code: Optional[str]) -> str:
    name = first_name.strip() or ("trader" if not _is_fr(lang_code) else "trader")
    if _is_fr(lang_code):
        return (
            f"Bienvenue sur MAXIA, {name}.\n\n"
            "Marketplace AI-to-AI sur 15 blockchains avec USDC. "
            "Achete/vends des services IA, rent du GPU, swap 65 tokens, "
            "trade des stocks tokenises, tout depuis Telegram.\n\n"
            "Ouvre la Mini App pour commencer."
        )
    return (
        f"Welcome to MAXIA, {name}.\n\n"
        "The AI-to-AI marketplace on 15 blockchains with USDC. "
        "Buy/sell AI services, rent GPU, swap 65 tokens, trade "
        "tokenized stocks — all from Telegram.\n\n"
        "Open the Mini App to get started."
    )


def _welcome_keyboard(lang_code: Optional[str]) -> dict:
    if _is_fr(lang_code):
        labels = ("Trading Mini App", "Acheter crypto", "Sniper")
    else:
        labels = ("Open Trading", "Buy Crypto", "Sniper")
    return {
        "inline_keyboard": [
            [{"text": labels[0], "web_app": {"url": _MINIAPP_URL}}],
            [{"text": labels[1], "web_app": {"url": _BUY_URL}}],
            [{"text": labels[2], "web_app": {"url": _SNIPER_URL}}],
        ]
    }


def _help_text(lang_code: Optional[str]) -> str:
    if _is_fr(lang_code):
        return (
            "*Commandes MAXIA*\n\n"
            "/start — Message de bienvenue + Mini App\n"
            "/price <TOKEN> — Prix live (ex: `/price SOL`)\n"
            "/help — Cette aide\n\n"
            "Besoin d'autre chose ? Ecris ta question en langage naturel, "
            "MAXIA te repondra."
        )
    return (
        "*MAXIA commands*\n\n"
        "/start — Welcome + Mini App\n"
        "/price <TOKEN> — Live price (e.g. `/price SOL`)\n"
        "/help — This help\n\n"
        "Need something else? Just type your question in plain English "
        "and MAXIA will answer."
    )


def _price_text(symbol: str, price: Optional[float], source: str, lang_code: Optional[str]) -> str:
    if price is None:
        if _is_fr(lang_code):
            return f"Impossible de recuperer le prix de *{symbol}* pour le moment."
        return f"Could not fetch the price for *{symbol}* right now."
    if _is_fr(lang_code):
        return f"*{symbol}* = `${price:,.4f}`\n_Source: {source}_"
    return f"*{symbol}* = `${price:,.4f}`\n_Source: {source}_"


async def handle_start(chat_id: int, first_name: str, lang_code: Optional[str]) -> None:
    await _tg_post(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": _welcome_text(first_name, lang_code),
            "reply_markup": _welcome_keyboard(lang_code),
        },
    )


async def handle_price(chat_id: int, args: str, lang_code: Optional[str]) -> None:
    symbol = (args or "SOL").strip().upper().split()[0] if args else "SOL"
    price: Optional[float] = None
    source = "oracle"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{_VPS_URL}/oracle/price/live/{symbol}")
            if resp.status_code == 200:
                d = resp.json()
                price = d.get("price")
                source = d.get("source", "oracle")
    except Exception as e:
        log.debug("[public] /price %s fetch error: %s", symbol, e)

    await _tg_post(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": _price_text(symbol, price, source, lang_code),
            "parse_mode": "Markdown",
        },
    )


async def handle_help(chat_id: int, lang_code: Optional[str]) -> None:
    await _tg_post(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": _help_text(lang_code),
            "parse_mode": "Markdown",
            "reply_markup": {
                "inline_keyboard": [[
                    {
                        "text": "Trading Mini App" if _is_fr(lang_code) else "Open Trading",
                        "web_app": {"url": _MINIAPP_URL},
                    },
                ]]
            },
        },
    )


async def handle_public_command(
    *,
    command: str,
    text: str,
    chat_id: int,
    first_name: str,
    lang_code: Optional[str],
) -> bool:
    """Dispatch a public slash command. Returns True if handled.

    Called from ``missions/telegram_chat.py::_process_one_update`` when a
    text message's first word is ``/start``, ``/price`` or ``/help``.
    """
    cmd = command.lower()
    if cmd == "/start":
        await handle_start(chat_id, first_name, lang_code)
        return True
    if cmd == "/price":
        args = text.split(None, 1)[1] if " " in text else ""
        await handle_price(chat_id, args, lang_code)
        return True
    if cmd == "/help":
        await handle_help(chat_id, lang_code)
        return True
    return False
