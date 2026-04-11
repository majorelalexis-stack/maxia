"""Telegram router — single getUpdates poller + pub/sub dispatch.

Only ONE long-poll per bot token is allowed by Telegram. This module
runs that single long-poll and dispatches incoming updates to the rest
of the local CEO (telegram_chat handler, approval waiters, etc.).

Previously, three different modules polled `getUpdates` in parallel
(community_news, telegram_chat, sales/approval), racing each other and
the VPS poller, leading to permanent 409 Conflict and approval timeouts.

Consumers API:
  - start_router(mem, actions) -- launch the background task (call once)
  - stop_router() -- cancel the task on shutdown
  - await_approval(action_id, timeout_s) -> Verdict
      Register a waiter for a specific action_id. Returns "human",
      "denied", or "timeout". Safe to call concurrently from any
      mission.
  - register_message_handler(fn) -- subscribe to every incoming update
      (text messages + callback_query). Fn signature:
      `async def fn(update: dict, mem: dict, actions: dict) -> None`

Offset persistence: the router uses the same `telegram_state.json` file
as the legacy telegram_chat mission, so migrating is transparent.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Awaitable, Callable, Literal, Optional

import httpx

log = logging.getLogger("ceo.telegram_router")

Verdict = Literal["human", "denied", "timeout"]

# ── Config ──
_TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
_TELEGRAM_API_BASE = f"https://api.telegram.org/bot{_TELEGRAM_BOT_TOKEN}" if _TELEGRAM_BOT_TOKEN else ""
_LONG_POLL_TIMEOUT_S = 25
_HTTP_TIMEOUT_S = 40
_BACKOFF_ON_ERROR_S = 5
_BACKOFF_ON_CONFLICT_S = 10

_LOCAL_CEO_DIR = os.path.dirname(os.path.abspath(__file__))
_STATE_FILE = os.path.join(_LOCAL_CEO_DIR, "telegram_state.json")

# ── State ──
_task: Optional[asyncio.Task] = None
_waiters: dict[str, asyncio.Future[Verdict]] = {}
_message_handlers: list[Callable[[dict, dict, dict], Awaitable[None]]] = []
_offset: int = 0
_offset_loaded: bool = False


def _load_offset() -> int:
    """Load last_update_id from shared state file. Shared with telegram_chat."""
    global _offset, _offset_loaded
    if _offset_loaded:
        return _offset
    try:
        if os.path.exists(_STATE_FILE):
            with open(_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.loads(f.read())
            _offset = int(data.get("last_update_id", 0))
    except (json.JSONDecodeError, OSError) as e:
        log.warning("[TG Router] offset load error: %s", e)
        _offset = 0
    _offset_loaded = True
    return _offset


def _save_offset(value: int) -> None:
    """Persist last_update_id, merging with existing state file keys."""
    data: dict[str, Any] = {"last_update_id": value, "pending_approvals": []}
    try:
        if os.path.exists(_STATE_FILE):
            with open(_STATE_FILE, "r", encoding="utf-8") as f:
                existing = json.loads(f.read() or "{}")
            if isinstance(existing, dict):
                existing["last_update_id"] = value
                data = existing
        with open(_STATE_FILE, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, indent=2, default=str))
    except (json.JSONDecodeError, OSError) as e:
        log.warning("[TG Router] offset save error: %s", e)


def register_message_handler(
    handler: Callable[[dict, dict, dict], Awaitable[None]],
) -> None:
    """Register a coroutine that receives every Telegram update.

    Handler is called with (update, mem, actions). Exceptions are caught
    and logged so a broken handler never kills the poll loop.
    """
    _message_handlers.append(handler)
    log.info("[TG Router] registered message handler: %s", handler.__name__)


async def await_approval(action_id: str, timeout_s: float) -> Verdict:
    """Wait for a GO/NO button click matching action_id.

    The router dispatches incoming callback_query whose ``data`` ends
    with ``action_id`` to this waiter's future. Returns:
      * "human" if callback_data starts with "approve:"
      * "denied" if callback_data starts with "reject:"
      * "timeout" if no matching callback arrives in time
    """
    loop = asyncio.get_running_loop()
    if action_id in _waiters:
        log.warning("[TG Router] duplicate waiter for %s — replacing", action_id)
        old = _waiters.pop(action_id)
        if not old.done():
            old.set_result("timeout")

    fut: asyncio.Future[Verdict] = loop.create_future()
    _waiters[action_id] = fut
    try:
        return await asyncio.wait_for(fut, timeout=timeout_s)
    except asyncio.TimeoutError:
        return "timeout"
    finally:
        _waiters.pop(action_id, None)


async def _answer_callback(client: httpx.AsyncClient, callback_query_id: str) -> None:
    """Dismiss the Telegram button spinner after a click. Best-effort."""
    try:
        await client.post(
            f"{_TELEGRAM_API_BASE}/answerCallbackQuery",
            json={"callback_query_id": callback_query_id, "text": "Recu"},
            timeout=10,
        )
    except Exception as e:
        log.debug("[TG Router] answerCallbackQuery failed: %s", e)


def _resolve_waiter(callback_data: str) -> bool:
    """Try to resolve an explicit waiter. Returns True if matched."""
    if ":" not in callback_data:
        return False
    verb, action_id = callback_data.split(":", 1)
    fut = _waiters.get(action_id)
    if fut is None or fut.done():
        return False
    verdict: Verdict = "human" if verb == "approve" else "denied"
    fut.set_result(verdict)
    log.info("[TG Router] waiter resolved: action_id=%s verdict=%s", action_id, verdict)
    return True


async def _dispatch(
    update: dict,
    mem: dict,
    actions: dict,
    client: httpx.AsyncClient,
) -> None:
    """Route one Telegram update to waiters and registered handlers."""
    cb = update.get("callback_query")
    if cb:
        data = cb.get("data", "") or ""
        cb_id = cb.get("id", "") or ""
        await _answer_callback(client, cb_id)

        if _resolve_waiter(data):
            return

    for handler in _message_handlers:
        try:
            await handler(update, mem, actions)
        except Exception as e:
            log.warning(
                "[TG Router] handler %s error: %s",
                getattr(handler, "__name__", "?"), e,
            )


async def _run_loop(mem: dict, actions: dict) -> None:
    """Single long-poll loop. Runs until cancelled."""
    global _offset

    if not _TELEGRAM_BOT_TOKEN:
        log.info("[TG Router] TELEGRAM_BOT_TOKEN not set — router disabled")
        return

    _load_offset()
    log.info("[TG Router] starting, offset=%d", _offset)

    consecutive_errors = 0
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
        while True:
            try:
                resp = await client.get(
                    f"{_TELEGRAM_API_BASE}/getUpdates",
                    params={
                        "offset": _offset + 1,
                        "timeout": _LONG_POLL_TIMEOUT_S,
                        # channel_post = admin-authored posts in @MAXIA_alerts
                        # (Alexis typing "tu es la", /status etc. as channel
                        # admin). Treated the same as "message" downstream.
                        "allowed_updates": '["message","callback_query","channel_post"]',
                    },
                    timeout=_HTTP_TIMEOUT_S,
                )
                data = resp.json() if resp.status_code == 200 else {}

                if not data.get("ok"):
                    err = (data.get("description") or "").lower()
                    if "terminated by other" in err or "conflict" in err:
                        log.warning(
                            "[TG Router] 409 conflict — another poller alive, "
                            "sleeping %ds", _BACKOFF_ON_CONFLICT_S,
                        )
                        await asyncio.sleep(_BACKOFF_ON_CONFLICT_S)
                        continue
                    log.warning("[TG Router] API error: %s", err or resp.text[:200])
                    consecutive_errors += 1
                    if consecutive_errors > 10:
                        log.warning("[TG Router] too many errors, pausing 60s")
                        await asyncio.sleep(60)
                        consecutive_errors = 0
                    continue

                consecutive_errors = 0
                updates = data.get("result") or []

                for upd in updates:
                    update_id = int(upd.get("update_id", 0))
                    if update_id > _offset:
                        _offset = update_id
                    try:
                        await _dispatch(upd, mem, actions, client)
                    except Exception as e:
                        log.warning("[TG Router] dispatch error: %s", e)

                if updates:
                    _save_offset(_offset)

            except asyncio.CancelledError:
                log.info("[TG Router] cancel received — stopping")
                raise
            except Exception as e:
                log.warning("[TG Router] loop error: %s", e)
                await asyncio.sleep(_BACKOFF_ON_ERROR_S)


def start_router(mem: dict, actions: dict) -> None:
    """Start the router background task (idempotent)."""
    global _task
    if _task and not _task.done():
        log.info("[TG Router] already running")
        return
    _task = asyncio.create_task(_run_loop(mem, actions), name="telegram_router")
    log.info("[TG Router] background task scheduled")


def stop_router() -> None:
    """Cancel the router background task if running."""
    global _task
    if _task and not _task.done():
        _task.cancel()
        log.info("[TG Router] stop requested")
