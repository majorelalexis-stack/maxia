"""MAXIA V12 — WebSocket handlers (extracted from main.py, S33)"""
import asyncio
import json
import logging
import os
import time
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from marketplace.auction_manager import AuctionManager
from agents.agent_worker import agent_worker

logger = logging.getLogger(__name__)
router = APIRouter()

# ── WebSocket state ──
_ws_clients: dict = {}
_ws_by_wallet: dict = {}  # wallet -> [ws1, ws2, ...]
_ws_connections: dict = {}  # ip -> count
_WS_MAX_PER_IP = 20
_WS_MAX_MESSAGE_SIZE = 65536  # 64 KB (H5)

# ── Redis pub/sub for multi-worker WS broadcast ──
_redis_pubsub = None
REDIS_URL = os.getenv("REDIS_URL", "")
WS_CHANNEL = "maxia:ws:broadcast"

# ── Auction manager (used by /auctions WS + lifespan expiry worker) ──
auction_manager = AuctionManager()


async def init_redis_pubsub():
    """Initialise Redis pub/sub si REDIS_URL est defini. Optionnel."""
    global _redis_pubsub
    if not REDIS_URL:
        return
    try:
        import redis.asyncio as aioredis
        _redis_pubsub = await aioredis.from_url(REDIS_URL)
        asyncio.create_task(_redis_ws_listener())
        logger.info("[WS] Redis pub/sub actif: %s", REDIS_URL.split('@')[-1] if '@' in REDIS_URL else REDIS_URL)
    except ImportError:
        logger.info("[WS] redis[async] non installe — mode single-worker")
    except Exception as e:
        logger.error("[WS] Redis error: %s — mode single-worker", e)


async def _redis_ws_listener():
    """Ecoute les messages Redis et les forward aux clients WebSocket locaux."""
    try:
        pubsub = _redis_pubsub.pubsub()
        await pubsub.subscribe(WS_CHANNEL)
        async for message in pubsub.listen():
            if message["type"] == "message":
                try:
                    msg = json.loads(message["data"])
                    await _local_broadcast(msg)
                except Exception:
                    pass
    except Exception as e:
        logger.error("[WS] Redis listener error: %s", e)


async def _local_broadcast(msg: dict):
    """Broadcast aux clients WebSocket de CE worker uniquement."""
    dead = []
    for cid, ws in _ws_clients.items():
        try:
            await ws.send_json(msg)
        except Exception:
            dead.append(cid)
    for cid in dead:
        _ws_clients.pop(cid, None)


async def broadcast_all(msg: dict):
    """Broadcast a tous les clients WS. Si Redis est actif, publie sur le channel
    pour que tous les workers recoivent le message. Sinon, broadcast local."""
    if _redis_pubsub:
        try:
            await _redis_pubsub.publish(WS_CHANNEL, json.dumps(msg, default=str))
            return  # Redis distribue a tous les workers via le listener
        except Exception:
            pass  # Fallback local si Redis echoue
    await _local_broadcast(msg)


async def send_to_wallet(wallet: str, msg: dict):
    """Push a message to all WebSocket connections for a specific wallet."""
    connections = _ws_by_wallet.get(wallet, [])
    dead = []
    for ws in connections:
        try:
            await ws.send_json(msg)
        except Exception:
            dead.append(ws)
    for ws in dead:
        connections.remove(ws)
    if not connections:
        _ws_by_wallet.pop(wallet, None)


# ── WS message helpers (H5: size control) ──

async def _ws_receive_json(ws: WebSocket) -> dict:
    """Recoit un message JSON avec controle de taille (H5)."""
    raw = await ws.receive_text()
    if len(raw) > _WS_MAX_MESSAGE_SIZE:
        await ws.close(1009, "Message too large")
        raise WebSocketDisconnect(1009)
    return json.loads(raw)


async def _ws_receive_json_timeout(ws: WebSocket, timeout: float) -> dict:
    """Recoit un message JSON avec timeout et controle de taille (H5)."""
    raw = await asyncio.wait_for(ws.receive_text(), timeout=timeout)
    if len(raw) > _WS_MAX_MESSAGE_SIZE:
        await ws.close(1009, "Message too large")
        raise WebSocketDisconnect(1009)
    return json.loads(raw)


# ═══════════════════════════════════════════════════════════
#  WEBSOCKET ENDPOINTS
# ═══════════════════════════════════════════════════════════

@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    ip = ws.client.host if ws.client else "unknown"
    if _ws_connections.get(ip, 0) >= _WS_MAX_PER_IP:
        await ws.close(1008)
        return
    _ws_connections[ip] = _ws_connections.get(ip, 0) + 1
    await ws.accept()
    cid = str(uuid.uuid4())
    _ws_clients[cid] = ws
    authenticated_wallet = None
    try:
        while True:
            # Auth timeout + H5: controle taille message
            if not authenticated_wallet:
                try:
                    msg = await _ws_receive_json_timeout(ws, timeout=30.0)
                except asyncio.TimeoutError:
                    await ws.send_json({"type": "AUTH_TIMEOUT", "error": "Authentication required within 30 seconds"})
                    await ws.close(1008)
                    break
            else:
                msg = await _ws_receive_json(ws)
            if msg.get("type") == "AUTH":
                wallet = msg.get("wallet", "")
                signature = msg.get("signature", "")
                nonce = msg.get("nonce", "")
                if wallet and signature and nonce:
                    # Verify nonce exists and matches
                    from core.auth import NONCES, _USED_NONCES, _cleanup_used_nonces, _USED_NONCES_MAX
                    entry = NONCES.get(wallet)
                    if not entry or entry[0] != nonce:
                        await ws.send_json({"type": "AUTH_FAILED", "error": "Invalid or expired nonce"})
                        await ws.close(1008)
                        break
                    # Anti-replay: check nonce not already used
                    replay_key = f"{wallet}:{nonce}"
                    if replay_key in _USED_NONCES:
                        await ws.send_json({"type": "AUTH_FAILED", "error": "Nonce already used (replay detected)"})
                        await ws.close(1008)
                        break
                    # Verifier la signature ed25519
                    try:
                        from nacl.signing import VerifyKey
                        import base58 as b58
                        message = f"MAXIA login: {nonce}".encode()
                        pub_bytes = b58.b58decode(wallet)
                        vk = VerifyKey(pub_bytes)
                        sig_bytes = bytes.fromhex(signature) if len(signature) == 128 else b58.b58decode(signature)
                        vk.verify(message, sig_bytes)
                        # Consume the nonce (anti-replay)
                        NONCES.pop(wallet, None)
                        _USED_NONCES[replay_key] = time.time()
                        if len(_USED_NONCES) > _USED_NONCES_MAX:
                            _cleanup_used_nonces()
                        authenticated_wallet = wallet
                        agent_worker.register_external_agent(wallet)
                        _ws_by_wallet.setdefault(wallet, []).append(ws)
                        await ws.send_json({"type": "AUTH_OK", "wallet": wallet})
                    except Exception as e:
                        logger.error("[WS] Auth signature error: %s", e)
                        await ws.send_json({"type": "AUTH_FAILED", "error": "Signature invalide"})
                else:
                    await ws.send_json({"type": "AUTH_FAILED", "error": "wallet, signature et nonce requis"})
            elif msg.get("type") == "PING":
                await ws.send_json({"type": "PONG", "timestamp": int(time.time() * 1000)})
    except WebSocketDisconnect:
        pass
    finally:
        _ws_clients.pop(cid, None)
        _ws_connections[ip] = max(0, _ws_connections.get(ip, 1) - 1)
        if authenticated_wallet:
            wl = _ws_by_wallet.get(authenticated_wallet, [])
            if ws in wl:
                wl.remove(ws)
            if not wl:
                _ws_by_wallet.pop(authenticated_wallet, None)


@router.websocket("/auctions")
async def auction_ws(ws: WebSocket):
    ip = ws.client.host if ws.client else "unknown"
    if _ws_connections.get(ip, 0) >= _WS_MAX_PER_IP:
        await ws.close(1008)
        return
    _ws_connections[ip] = _ws_connections.get(ip, 0) + 1
    await ws.accept()
    cid = str(uuid.uuid4())
    await auction_manager.register(cid, ws)
    wallet = None
    try:
        for a in auction_manager.get_open_auctions():
            await ws.send_json({"type": "AUCTION_OPENED", "payload": a})
        while True:
            # H5: controle taille message
            msg = await _ws_receive_json(ws)
            if msg.get("type") == "AUTH":
                _wallet = msg.get("wallet", "")
                _sig = msg.get("signature", "")
                _nonce = msg.get("nonce", "")
                if _wallet and _sig and _nonce:
                    # Verify nonce exists and matches
                    from core.auth import NONCES as _NONCES
                    _entry = _NONCES.get(_wallet)
                    if not _entry or _entry[0] != _nonce:
                        await ws.send_json({"type": "AUTH_FAILED", "error": "Invalid or expired nonce"})
                        continue
                    try:
                        from nacl.signing import VerifyKey
                        import base58 as b58
                        message = f"MAXIA login: {_nonce}".encode()
                        pub_bytes = b58.b58decode(_wallet)
                        vk = VerifyKey(pub_bytes)
                        sig_bytes = bytes.fromhex(_sig) if len(_sig) == 128 else b58.b58decode(_sig)
                        vk.verify(message, sig_bytes)
                        wallet = _wallet
                        auction_manager.set_wallet(cid, wallet)
                        agent_worker.register_external_agent(wallet)
                        await ws.send_json({"type": "AUTH_OK", "wallet": wallet})
                    except Exception as e:
                        logger.error("[WS/auctions] Auth signature error: %s", e)
                        await ws.send_json({"type": "AUTH_FAILED", "error": "Signature invalide"})
                else:
                    await ws.send_json({"type": "AUTH_FAILED", "error": "wallet, signature et nonce requis"})
            elif msg.get("type") == "PLACE_BID":
                if not wallet:
                    await ws.send_json({"type": "ERROR", "payload": {"reason": "AUTH requis — envoyez wallet + signature + nonce."}})
                    continue
                res = await auction_manager.place_bid(
                    msg["auctionId"], float(msg.get("bidUsdc", 0)), wallet)
                if not res["ok"]:
                    await ws.send_json({"type": "ERROR", "payload": {"reason": res["reason"]}})
    except WebSocketDisconnect:
        pass
    finally:
        await auction_manager.unregister(cid)
        _ws_connections[ip] = max(0, _ws_connections.get(ip, 1) - 1)


@router.websocket("/ws/prices")
async def ws_prices(websocket: WebSocket):
    """WebSocket: real-time price updates. Max 5 per IP.

    Modes (send JSON after connect):
      {"mode": "hft"}     — Pyth SSE streaming, push on every price update (<1s)
      {"mode": "normal"}  — polling every 5s (default si pas de message initial)
    """
    ip = websocket.client.host if websocket.client else "unknown"
    _ws_connections[ip] = _ws_connections.get(ip, 0) + 1
    if _ws_connections[ip] > _WS_MAX_PER_IP:
        _ws_connections[ip] -= 1
        await websocket.close(code=1008, reason="Too many connections")
        return
    await websocket.accept()

    # Detecter le mode (attente 2s pour un message optionnel)
    mode = "normal"
    try:
        msg = await asyncio.wait_for(websocket.receive_json(), timeout=2.0)
        mode = msg.get("mode", "normal")
    except (asyncio.TimeoutError, Exception):
        pass  # Pas de message = mode normal

    try:
        if mode == "hft":
            # Mode HFT: subscribe au stream Pyth SSE, push chaque update
            from trading.pyth_oracle import _sse_subscribers, _CANDLE_MAX_SUBSCRIBERS, start_pyth_stream
            await start_pyth_stream()
            if len(_sse_subscribers) >= _CANDLE_MAX_SUBSCRIBERS:
                await websocket.send_json({"error": "Too many price subscribers. Try again later."})
                await websocket.close(1013)
                return
            q: asyncio.Queue = asyncio.Queue(maxsize=50)
            _sse_subscribers.append(q)
            try:
                while True:
                    price_update = await q.get()
                    await websocket.send_json({"type": "price_hft", "data": price_update, "ts": int(time.time())})
            finally:
                _sse_subscribers.remove(q)
        else:
            # Mode normal: polling toutes les 5s
            while True:
                try:
                    from trading.price_oracle import get_crypto_prices
                    prices = await get_crypto_prices()
                    await websocket.send_json({"type": "prices", "data": prices, "ts": int(time.time())})
                except WebSocketDisconnect:
                    break
                except Exception as e:
                    err_msg = str(e).lower()
                    if err_msg and "close" not in err_msg and "cancelled" not in err_msg:
                        logger.error("[WS/prices] Error: %s", e)
                await asyncio.sleep(5)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        err_msg = str(e).lower()
        if err_msg and "disconnect" not in err_msg and "close" not in err_msg and "cancelled" not in err_msg:
            logger.error("[WS/prices] Connection error: %s", e)
    finally:
        _ws_connections[ip] = max(0, _ws_connections.get(ip, 1) - 1)


@router.websocket("/ws/chart")
async def ws_chart(websocket: WebSocket):
    """WebSocket: real-time OHLCV candles from Pyth SSE stream.
    Push candle updates every tick (<1s). Supports 1s, 5s, 1m intervals.

    Send after connect: {"symbol": "SOL", "interval": 1}  (interval in seconds)
    Receives: {"type": "candle_update", "symbol": "SOL", "interval": 1, "time": ..., "open": ..., "high": ..., "low": ..., "close": ...}
    """
    # Per-IP connection limit (same as /ws/prices)
    ip = websocket.client.host if websocket.client else "unknown"
    _ws_connections[ip] = _ws_connections.get(ip, 0) + 1
    if _ws_connections[ip] > _WS_MAX_PER_IP:
        _ws_connections[ip] -= 1
        await websocket.close(code=1008, reason="Too many connections")
        return
    await websocket.accept()
    try:
        params = await _ws_receive_json_timeout(websocket, timeout=5.0)
        symbol = params.get("symbol", "SOL").upper()[:20]
        interval = int(params.get("interval", 1))
        if interval not in (1, 5, 60, 3600, 21600, 86400):
            interval = 1

        from trading.pyth_oracle import _candle_subscribers, get_recent_candles

        # Envoyer l'historique recent
        history = get_recent_candles(symbol, interval, limit=300)
        if history:
            await websocket.send_json({"type": "history", "symbol": symbol, "interval": interval, "candles": history})

        # Souscrire aux updates live (capped to prevent unbounded memory)
        from trading.pyth_oracle import _CANDLE_MAX_SUBSCRIBERS
        if len(_candle_subscribers) >= _CANDLE_MAX_SUBSCRIBERS:
            await websocket.send_json({"error": "Too many candle subscribers. Try again later."})
            await websocket.close(1013)
            return
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        _candle_subscribers.append(q)
        try:
            while True:
                msg = await q.get()
                if msg.get("symbol") == symbol and msg.get("interval") == interval:
                    await websocket.send_json(msg)
        finally:
            _candle_subscribers.remove(q)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        err_msg = str(e).lower()
        if err_msg and "disconnect" not in err_msg and "close" not in err_msg and "cancelled" not in err_msg:
            logger.error("[WS/chart] Error: %s", e)
    finally:
        _ws_connections[ip] = max(0, _ws_connections.get(ip, 1) - 1)


@router.websocket("/ws/candles")
async def ws_candles(websocket: WebSocket):
    """WebSocket: real-time candle updates every 60 seconds."""
    # Per-IP connection limit (same as /ws/prices)
    ip = websocket.client.host if websocket.client else "unknown"
    _ws_connections[ip] = _ws_connections.get(ip, 0) + 1
    if _ws_connections[ip] > _WS_MAX_PER_IP:
        _ws_connections[ip] -= 1
        await websocket.close(code=1008, reason="Too many connections")
        return
    await websocket.accept()
    try:
        # Get subscription params from first message (H5: controle taille)
        params = await _ws_receive_json_timeout(websocket, timeout=10.0)
        symbol = params.get("symbol", "SOL").upper()[:20]  # Max 20 chars
        interval = params.get("interval", "1m")
        # Validate symbol format (alphanumeric only) and interval whitelist
        import re as _re_ws
        if not _re_ws.match(r'^[A-Z0-9_/]{1,20}$', symbol):
            await websocket.send_json({"error": "Invalid symbol format"})
            await websocket.close()
            return
        _VALID_INTERVALS = {"1m", "5m", "15m", "1h", "4h", "1d", "1w"}
        if interval not in _VALID_INTERVALS:
            await websocket.send_json({"error": f"Invalid interval. Valid: {', '.join(sorted(_VALID_INTERVALS))}"})
            await websocket.close()
            return
        while True:
            try:
                from core.database import db as _db_candles
                rows = await _db_candles.raw_execute_fetchall(
                    "SELECT symbol, interval, open, high, low, close, volume, timestamp FROM price_candles "
                    "WHERE symbol=? AND interval=? ORDER BY timestamp DESC LIMIT 1", (symbol, interval))
                if rows:
                    r = rows[0]
                    await websocket.send_json({"type": "candle", "symbol": symbol, "interval": interval,
                        "o": r["open"], "h": r["high"], "l": r["low"], "c": r["close"], "v": r["volume"], "t": r["timestamp"]})
            except WebSocketDisconnect:
                break
            except Exception as e:
                err_msg = str(e).lower()
                if err_msg and "close" not in err_msg and "cancelled" not in err_msg:
                    logger.error("[WS/candles] Error: %s", e)
            await asyncio.sleep(60 if interval != "1m" else 10)
    except Exception as e:
        if "disconnect" not in str(e).lower():
            logger.error("[WS/candles] Connection error: %s", e)
    finally:
        _ws_connections[ip] = max(0, _ws_connections.get(ip, 1) - 1)
