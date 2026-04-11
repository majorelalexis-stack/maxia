"""Telegram approval helper backed by the shared telegram_router.

Extracted from ``missions/community_news.py`` so email drafts, GitHub
prospect replies, and any future action that needs a human-in-the-loop
GO/NO can reuse the exact same blocking flow.

The router is the single Telegram long-poller for the whole local CEO
process; this module only sends the approval message and edits it with
the final verdict after the router resolves the waiter.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal, Optional

import httpx

log = logging.getLogger("ceo.sales.approval")

Verdict = Literal["human", "denied", "timeout"]


async def request_telegram_approval(
    *,
    bot_token: str,
    chat_id: str,
    action_id: str,
    title: str,
    body: str,
    timeout_s: int = 120,
) -> Verdict:
    """Send a GO/NO approval request to Telegram and block until resolved.

    Args:
        bot_token: Telegram bot token (``TELEGRAM_BOT_TOKEN`` from config).
        chat_id: Destination chat (``TELEGRAM_CEO_CHAT_ID``).
        action_id: Unique identifier for this action. Becomes the suffix
            of the ``callback_data`` and is used to match the user's reply.
            Must be URL-safe and unique per call.
        title: Short banner line shown at the top of the approval message.
            Example: ``"ORANGE — Email draft to prospect@acme.com"``.
        body: The actual content to review (will be wrapped in a code block,
            so don't include triple backticks).
        timeout_s: How long to wait for the user's click before returning
            ``"timeout"``. Default 120s.

    Returns:
        ``"human"`` if the user clicked GO,
        ``"denied"`` if the user clicked NO,
        ``"timeout"`` if nothing happened within ``timeout_s``.
    """
    if not bot_token or not chat_id:
        log.warning("[approval] missing bot_token or chat_id — returning timeout")
        return "timeout"

    from telegram_router import await_approval

    api_base = f"https://api.telegram.org/bot{bot_token}"

    preview = body[:900]
    text = (
        f"\U0001f7e0 *{title}*\n\n"
        f"```\n{preview}\n```\n\n"
        f"Approuver cette action ?"
    )
    keyboard = {
        "inline_keyboard": [[
            {"text": "\u2705 GO", "callback_data": f"approve:{action_id}"},
            {"text": "\u274c NO", "callback_data": f"reject:{action_id}"},
        ]]
    }

    message_id: Optional[int] = None
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.post(
                f"{api_base}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "Markdown",
                    "reply_markup": keyboard,
                },
            )
            if resp.status_code != 200:
                log.warning(
                    "[approval] sendMessage HTTP %s: %s",
                    resp.status_code, resp.text[:200],
                )
                return "timeout"
            try:
                message_id = int(resp.json().get("result", {}).get("message_id", 0)) or None
            except Exception:
                message_id = None
        except Exception as e:
            log.warning("[approval] send error: %s", e)
            return "timeout"

        verdict: Verdict = await await_approval(action_id, timeout_s)

        if message_id:
            stamp = datetime.now().strftime("%H:%M:%S")
            banner_map = {
                "human": f"\u2705 *APPROUVE a {stamp}*",
                "denied": f"\u274c *REFUSE a {stamp}*",
                "timeout": f"\u23f1 *TIMEOUT a {stamp}*",
            }
            new_text = f"{banner_map[verdict]}\n\n{text}"
            if len(new_text) > 4000:
                new_text = new_text[:4000] + "..."
            try:
                await client.post(
                    f"{api_base}/editMessageText",
                    json={
                        "chat_id": chat_id,
                        "message_id": message_id,
                        "text": new_text,
                        "parse_mode": "Markdown",
                    },
                    timeout=10,
                )
            except Exception as e:
                log.debug("[approval] editMessageText failed: %s", e)

    return verdict
