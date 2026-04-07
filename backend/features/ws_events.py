"""MAXIA V12 — Real-time WebSocket event streaming for agents.

Provides a pub/sub event bus that pushes live marketplace events
to connected WebSocket clients authenticated via API key.

Events:
  - service.listed    — new service listed on marketplace
  - service.executed  — service executed (anonymized)
  - agent.registered  — new agent registered
  - price.update      — periodic price updates (every 30s, top 5 tokens)
  - system.stats      — periodic marketplace stats (every 60s)
"""
import asyncio
import json
import logging
import time
from typing import Set

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

# ── Constants ──
_MAX_WS_CONNECTIONS = 100
_QUEUE_MAX_SIZE = 256
_HEARTBEAT_INTERVAL = 30  # seconds
_PRICE_UPDATE_INTERVAL = 30  # seconds
_STATS_UPDATE_INTERVAL = 60  # seconds
_TOP_TOKENS = ["SOL", "BTC", "ETH", "USDC", "JUP"]

# ── Event bus: set of asyncio.Queue, one per connected client ──
_event_subscribers: Set[asyncio.Queue] = set()
_subscriber_count = 0  # track total (including after removals from set)

# ── Background task handles (for shutdown) ──
_periodic_tasks: list = []


async def publish_event(event_type: str, data: dict) -> None:
    """Broadcast an event to all connected WebSocket event subscribers.

    Drops the message silently if a client's queue is full (backpressure).
    """
    if not _event_subscribers:
        return
    message = {
        "event": event_type,
        "data": data,
        "ts": int(time.time()),
    }
    dead: list[asyncio.Queue] = []
    for queue in list(_event_subscribers):
        try:
            queue.put_nowait(message)
        except asyncio.QueueFull:
            # Client is too slow — drop this message for them
            pass
        except Exception:
            dead.append(queue)
    for q in dead:
        _event_subscribers.discard(q)


def _validate_api_key(api_key: str) -> dict | None:
    """Check if an API key is valid. Returns agent dict or None."""
    if not api_key or not api_key.startswith("maxia_"):
        return None
    try:
        from marketplace.public_api_shared import _registered_agents
        return _registered_agents.get(api_key)
    except Exception:
        return None


async def _ensure_agents_loaded() -> None:
    """Load agents from DB if not yet loaded (first WS connection may arrive before HTTP)."""
    try:
        from marketplace.public_api_shared import _db_loaded, _load_from_db
        if not _db_loaded:
            await _load_from_db()
    except Exception:
        pass


async def ws_events_endpoint(websocket: WebSocket) -> None:
    """WebSocket endpoint: /ws/events?api_key=maxia_xxx

    Streams real-time marketplace events to authenticated agents.
    Auth via query param because WebSocket does not easily support custom headers.
    """
    global _subscriber_count

    # ── Connection limit ──
    if len(_event_subscribers) >= _MAX_WS_CONNECTIONS:
        await websocket.close(code=1013, reason="Too many event stream connections")
        return

    # ── Auth via query param ──
    api_key = websocket.query_params.get("api_key", "")

    # Ensure agent data is loaded before validating key
    await _ensure_agents_loaded()

    agent = _validate_api_key(api_key)
    if not agent:
        await websocket.close(code=1008, reason="Invalid or missing api_key")
        return

    await websocket.accept()
    agent_name = agent.get("name", "unknown")
    logger.info("[WS/events] Connected: %s (%s)", agent_name, api_key[:6] + "...")

    # ── Create per-client queue ──
    queue: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAX_SIZE)
    _event_subscribers.add(queue)
    _subscriber_count += 1

    # Send welcome message
    try:
        await websocket.send_json({
            "event": "connected",
            "data": {
                "agent": agent_name,
                "subscribed_events": [
                    "service.listed",
                    "service.executed",
                    "agent.registered",
                    "price.update",
                    "system.stats",
                ],
                "heartbeat_interval_s": _HEARTBEAT_INTERVAL,
            },
            "ts": int(time.time()),
        })
    except Exception:
        _event_subscribers.discard(queue)
        return

    # ── Read loop (heartbeat + event push) ──
    async def _push_events() -> None:
        """Push events from queue to WebSocket."""
        while True:
            msg = await queue.get()
            await websocket.send_json(msg)

    async def _heartbeat() -> None:
        """Send periodic pings so clients know the connection is alive."""
        while True:
            await asyncio.sleep(_HEARTBEAT_INTERVAL)
            await websocket.send_json({
                "event": "heartbeat",
                "ts": int(time.time()),
            })

    async def _receive_loop() -> None:
        """Listen for client messages (PING support + graceful close detection)."""
        while True:
            raw = await websocket.receive_text()
            # Accept PING from client
            if len(raw) > 65536:
                await websocket.close(1009, "Message too large")
                return
            try:
                msg = json.loads(raw)
                if msg.get("type") == "PING":
                    await websocket.send_json({
                        "event": "PONG",
                        "ts": int(time.time()),
                    })
            except (json.JSONDecodeError, Exception):
                pass

    try:
        # Run push, heartbeat, and receive concurrently
        await asyncio.gather(
            _push_events(),
            _heartbeat(),
            _receive_loop(),
        )
    except WebSocketDisconnect:
        pass
    except Exception as e:
        err_msg = str(e).lower()
        if "disconnect" not in err_msg and "close" not in err_msg and "cancelled" not in err_msg:
            logger.error("[WS/events] Error: %s", e)
    finally:
        _event_subscribers.discard(queue)
        logger.info("[WS/events] Disconnected: %s", agent_name)


# ═══════════════════════════════════════════════════════════
#  PERIODIC BACKGROUND TASKS (price + stats)
# ═══════════════════════════════════════════════════════════

async def _price_update_loop() -> None:
    """Publish price.update events every 30s for top tokens."""
    while True:
        try:
            await asyncio.sleep(_PRICE_UPDATE_INTERVAL)
            if not _event_subscribers:
                continue  # No clients connected — skip work

            prices = {}
            try:
                from trading.price_oracle import get_crypto_prices
                all_prices = await get_crypto_prices()
                # Extract top tokens from the price data
                for token in _TOP_TOKENS:
                    key_lower = token.lower()
                    if key_lower in all_prices:
                        prices[token] = all_prices[key_lower]
                    elif token in all_prices:
                        prices[token] = all_prices[token]
            except Exception as e:
                logger.debug("[WS/events] Price fetch error: %s", e)
                continue

            if prices:
                await publish_event("price.update", {
                    "prices": prices,
                    "tokens": list(prices.keys()),
                    "count": len(prices),
                })
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("[WS/events] Price loop error: %s", e)
            await asyncio.sleep(10)


async def _stats_update_loop() -> None:
    """Publish system.stats events every 60s with marketplace statistics."""
    while True:
        try:
            await asyncio.sleep(_STATS_UPDATE_INTERVAL)
            if not _event_subscribers:
                continue  # No clients connected — skip work

            stats = {}
            try:
                from marketplace.public_api_shared import (
                    _registered_agents, _agent_services, _transactions,
                )
                now = int(time.time())
                day_ago = now - 86400

                stats = {
                    "total_agents": len(_registered_agents),
                    "total_services": len([s for s in _agent_services if s.get("status") == "active"]),
                    "volume_24h_usdc": round(
                        sum(
                            t.get("price_usdc", 0)
                            for t in _transactions
                            if t.get("timestamp", 0) > day_ago
                        ),
                        2,
                    ),
                    "transactions_24h": sum(
                        1 for t in _transactions if t.get("timestamp", 0) > day_ago
                    ),
                }
            except Exception as e:
                logger.debug("[WS/events] Stats fetch error: %s", e)
                continue

            if stats:
                await publish_event("system.stats", stats)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("[WS/events] Stats loop error: %s", e)
            await asyncio.sleep(10)


async def start_periodic_events() -> None:
    """Start the periodic background tasks for price and stats updates.

    Called from main.py lifespan. Returns immediately (tasks run in background).
    """
    t_prices = asyncio.create_task(_price_update_loop())
    t_stats = asyncio.create_task(_stats_update_loop())
    _periodic_tasks.extend([t_prices, t_stats])
    logger.info("[WS/events] Periodic event tasks started (prices: %ds, stats: %ds)",
                _PRICE_UPDATE_INTERVAL, _STATS_UPDATE_INTERVAL)


def stop_periodic_events() -> None:
    """Cancel periodic tasks on shutdown."""
    for t in _periodic_tasks:
        try:
            t.cancel()
        except Exception:
            pass
    _periodic_tasks.clear()
