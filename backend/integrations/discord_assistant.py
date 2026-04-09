"""Discord assistant listener — Phase 1.

Connects to the Discord Gateway as ``MAXIA assistant`` (a bot separate
from ``MAXIA outreach``) and listens for MESSAGE_CREATE events in the
configured ``#ask-ai`` channel. Each new human message is forwarded to
``ceo_bridge.ingest_message()`` so CEO Local can auto-reply.

Design notes
------------
- **Read-only**: this module never writes to Discord. Writing is done
  by ``ceo_bridge._dispatch_discord()`` when CEO Local posts its reply.
- **No new dependency**: uses the already-installed ``websockets``
  library rather than pulling in ``discord.py``.
- **Single channel scope**: only ``DISCORD_ASK_AI_CHANNEL_ID`` is
  listened to. Messages in any other channel are ignored.
- **Bot filter**: messages from bots (incl. the outreach bot and the
  assistant itself) are skipped to avoid loops.
- **Resilient**: reconnects on disconnect with exponential backoff.

Required env vars:
    DISCORD_ASSISTANT_TOKEN    Bot token (not client secret)
    DISCORD_ASK_AI_CHANNEL_ID  The Discord channel ID (right-click → Copy ID)

Required privileged intent: ``Message Content Intent`` must be enabled
in Discord Developer Portal → MAXIA assistant → Bot → Privileged
Gateway Intents. Without it, ``message.content`` arrives empty.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
from typing import Optional

import websockets
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger("maxia.discord_assistant")

# Discord Gateway — v10 JSON encoding
GATEWAY_URL = "wss://gateway.discord.gg/?v=10&encoding=json"

# Gateway opcodes we care about
OP_DISPATCH = 0
OP_HEARTBEAT = 1
OP_IDENTIFY = 2
OP_RECONNECT = 7
OP_INVALID_SESSION = 9
OP_HELLO = 10
OP_HEARTBEAT_ACK = 11

# Intents bitmask (Discord Gateway Intents API v10)
INTENT_GUILDS = 1 << 0          # 1
INTENT_GUILD_MESSAGES = 1 << 9  # 512
INTENT_MESSAGE_CONTENT = 1 << 15  # 32768 (PRIVILEGED)
INTENTS = INTENT_GUILDS | INTENT_GUILD_MESSAGES | INTENT_MESSAGE_CONTENT

# Max characters we forward to the bridge (Discord cap is 4000 for
# premium, 2000 default — trim for safety)
MAX_INGEST_CHARS = 3500

# Reconnect backoff
MIN_BACKOFF = 1.0
MAX_BACKOFF = 60.0


class DiscordAssistantListener:
    """Minimal read-only Discord Gateway client.

    Only listens; writing is delegated to ``ceo_bridge._dispatch_discord``.
    """

    def __init__(
        self,
        *,
        token: str,
        ask_channel_id: str,
    ) -> None:
        if not token or len(token) < 30:
            raise ValueError("DISCORD_ASSISTANT_TOKEN invalid or missing")
        if not ask_channel_id or not ask_channel_id.isdigit():
            raise ValueError("DISCORD_ASK_AI_CHANNEL_ID must be a numeric channel id")
        self._token = token
        self._ask_channel_id = ask_channel_id
        self._sequence: Optional[int] = None
        self._heartbeat_interval: float = 41.25  # sane default, will be replaced
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._running = True
        self._bot_user_id: str = ""

    async def stop(self) -> None:
        self._running = False
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()

    async def run(self) -> None:
        """Main loop — connect, listen, reconnect on disconnect."""
        backoff = MIN_BACKOFF
        while self._running:
            try:
                await self._session_once()
                backoff = MIN_BACKOFF  # reset on clean exit
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(
                    "[discord_assistant] session error: %s — reconnecting in %.1fs",
                    e, backoff,
                )
                await asyncio.sleep(backoff + random.uniform(0, 0.5))
                backoff = min(backoff * 2, MAX_BACKOFF)

    async def _session_once(self) -> None:
        async with websockets.connect(
            GATEWAY_URL, max_size=2**20, ping_interval=None,
        ) as ws:
            logger.info("[discord_assistant] Gateway connected")

            # HELLO must arrive first
            hello = await asyncio.wait_for(ws.recv(), timeout=10)
            hello_msg = json.loads(hello)
            if hello_msg.get("op") != OP_HELLO:
                raise RuntimeError(f"expected HELLO, got op={hello_msg.get('op')}")
            self._heartbeat_interval = (
                hello_msg["d"]["heartbeat_interval"] / 1000.0
            )

            # Start the heartbeat loop
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(ws))

            # Send IDENTIFY
            await ws.send(json.dumps({
                "op": OP_IDENTIFY,
                "d": {
                    "token": self._token,
                    "intents": INTENTS,
                    "properties": {
                        "os": "linux",
                        "browser": "maxia-assistant",
                        "device": "maxia-assistant",
                    },
                    "presence": {
                        "status": "online",
                        "activities": [{
                            "name": "MAXIA Community",
                            "type": 3,  # Watching
                        }],
                        "afk": False,
                    },
                },
            }))

            async for raw in ws:
                if not self._running:
                    return
                try:
                    await self._handle_event(json.loads(raw))
                except Exception as e:
                    logger.error("[discord_assistant] event handler error: %s", e)

    async def _heartbeat_loop(self, ws) -> None:
        """Send heartbeats at heartbeat_interval."""
        try:
            # First heartbeat has a jitter per Discord docs
            await asyncio.sleep(self._heartbeat_interval * random.random())
            while self._running:
                try:
                    await ws.send(json.dumps({
                        "op": OP_HEARTBEAT, "d": self._sequence,
                    }))
                except ConnectionClosed:
                    return
                await asyncio.sleep(self._heartbeat_interval)
        except asyncio.CancelledError:
            return

    async def _handle_event(self, msg: dict) -> None:
        op = msg.get("op")
        if op == OP_DISPATCH:
            if "s" in msg and msg["s"] is not None:
                self._sequence = int(msg["s"])
            event_type = msg.get("t", "")
            data = msg.get("d", {})
            if event_type == "READY":
                user = data.get("user", {}) if isinstance(data, dict) else {}
                self._bot_user_id = str(user.get("id", ""))
                logger.info(
                    "[discord_assistant] READY — bot user=%s username=%s",
                    self._bot_user_id, user.get("username", "?"),
                )
            elif event_type == "MESSAGE_CREATE":
                await self._on_message(data)
        elif op == OP_HEARTBEAT:
            # Gateway asked us to heartbeat NOW
            pass
        elif op == OP_HEARTBEAT_ACK:
            pass
        elif op == OP_RECONNECT:
            logger.info("[discord_assistant] Gateway asked reconnect")
            raise ConnectionClosed(None, None)
        elif op == OP_INVALID_SESSION:
            logger.warning("[discord_assistant] invalid session — reconnecting")
            await asyncio.sleep(5)
            raise ConnectionClosed(None, None)

    async def _on_message(self, data: dict) -> None:
        if not isinstance(data, dict):
            return

        channel_id = str(data.get("channel_id", ""))
        if channel_id != self._ask_channel_id:
            return  # ignore all other channels

        # Skip bots (including ourselves) and webhooks
        author = data.get("author") or {}
        if not isinstance(author, dict):
            return
        if author.get("bot") or author.get("system"):
            return
        author_id = str(author.get("id", ""))
        if author_id and author_id == self._bot_user_id:
            return

        content = str(data.get("content", "")).strip()
        if not content:
            return

        message_id = str(data.get("id", ""))
        username = str(author.get("global_name") or author.get("username") or "")

        try:
            from ceo_bridge import ingest_message
            msg_id = await ingest_message(
                channel="discord",
                source_ref=f"{channel_id}:{message_id}",
                user_id=author_id,
                user_name=username[:64],
                message=content[:MAX_INGEST_CHARS],
                language="",
            )
            logger.info(
                "[discord_assistant] ingested %s user=%s (%d chars)",
                msg_id, username[:24], len(content),
            )
        except Exception as e:
            logger.error("[discord_assistant] ingest failed: %s", e)


# ══════════════════════════════════════════
#  Lifespan hooks (called from main.py)
# ══════════════════════════════════════════


_runner: Optional[asyncio.Task] = None
_listener: Optional[DiscordAssistantListener] = None


async def start_listener() -> None:
    """Start the Discord assistant listener as a background task.

    Safe to call on every startup — no-ops if the env vars are missing
    so dev/test runs don't require a Discord bot to be configured.
    """
    global _runner, _listener
    token = os.getenv("DISCORD_ASSISTANT_TOKEN", "")
    channel_id = os.getenv("DISCORD_ASK_AI_CHANNEL_ID", "")
    if not token or not channel_id:
        logger.info(
            "[discord_assistant] DISCORD_ASSISTANT_TOKEN or "
            "DISCORD_ASK_AI_CHANNEL_ID not set — listener skipped"
        )
        return
    try:
        _listener = DiscordAssistantListener(
            token=token, ask_channel_id=channel_id,
        )
    except ValueError as e:
        logger.warning("[discord_assistant] config invalid: %s", e)
        return

    _runner = asyncio.create_task(_listener.run())
    logger.info(
        "[discord_assistant] listener started (channel_id=%s)",
        channel_id,
    )


async def stop_listener() -> None:
    """Stop the background listener (called on app shutdown)."""
    global _runner, _listener
    if _listener:
        await _listener.stop()
    if _runner and not _runner.done():
        _runner.cancel()
        try:
            await _runner
        except (asyncio.CancelledError, Exception):
            pass
    _runner = None
    _listener = None
