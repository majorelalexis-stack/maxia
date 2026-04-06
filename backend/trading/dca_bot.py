"""MAXIA V12 — DCA Bot: Dollar-Cost Averaging automated trading"""
import logging
import asyncio
import time
import uuid
from fastapi import APIRouter, HTTPException, Header

logger = logging.getLogger("maxia.dca")

router = APIRouter(prefix="/api/dca", tags=["dca"])

# ── Constants ──
DCA_COMMISSION_BPS = 10  # 0.10% (10 basis points)
DCA_MIN_AMOUNT = 1.0
DCA_MAX_AMOUNT = 1000.0
DCA_MAX_FAIL_STREAK = 3

FREQUENCY_SECONDS = {
    "daily": 86400,
    "weekly": 604800,
    "biweekly": 1209600,
    "monthly": 2592000,
}

# Tokens supported for DCA (subset of crypto_swap SUPPORTED_TOKENS)
DCA_TOKENS = {
    "SOL", "ETH", "BTC", "BONK", "JUP", "RAY", "WIF", "RENDER", "HNT",
    "PYTH", "LINK", "UNI", "AAVE", "DOGE", "SHIB", "PEPE", "XRP",
    "AVAX", "MATIC", "BNB", "TON", "SUI", "NEAR", "APT", "SEI",
    "ARB", "OP", "FET", "FIL", "AR", "INJ", "TAO", "AKT",
    "ORCA", "DRIFT", "ONDO", "TRUMP",
}


async def _get_db():
    from core.database import db
    return db


async def _get_agent(api_key: str) -> dict:
    db = await _get_db()
    agent = await db.get_agent(api_key)
    if not agent:
        raise HTTPException(401, "Invalid API key")
    return agent


async def ensure_tables():
    db = await _get_db()
    await db.raw_executescript("""
        CREATE TABLE IF NOT EXISTS dca_orders (
            order_id TEXT PRIMARY KEY, api_key TEXT NOT NULL, wallet TEXT NOT NULL,
            from_token TEXT NOT NULL DEFAULT 'USDC', to_token TEXT NOT NULL,
            amount_usdc NUMERIC(18,6) NOT NULL, frequency TEXT NOT NULL DEFAULT 'weekly',
            status TEXT NOT NULL DEFAULT 'active', total_executed INTEGER DEFAULT 0,
            total_invested_usdc NUMERIC(18,6) DEFAULT 0, total_received NUMERIC(18,6) DEFAULT 0,
            next_run INTEGER NOT NULL, last_run INTEGER DEFAULT 0, fail_streak INTEGER DEFAULT 0,
            created_at INTEGER DEFAULT (strftime('%s','now')));
        CREATE INDEX IF NOT EXISTS idx_dca_api_key ON dca_orders(api_key);
        CREATE INDEX IF NOT EXISTS idx_dca_status ON dca_orders(status);
        CREATE INDEX IF NOT EXISTS idx_dca_next_run ON dca_orders(next_run);
        CREATE TABLE IF NOT EXISTS dca_executions (
            exec_id TEXT PRIMARY KEY, order_id TEXT NOT NULL,
            price_usdc NUMERIC(18,6) NOT NULL, amount_usdc NUMERIC(18,6) NOT NULL,
            received NUMERIC(18,6) NOT NULL, commission_usdc NUMERIC(18,6) DEFAULT 0,
            created_at INTEGER DEFAULT (strftime('%s','now')));
        CREATE INDEX IF NOT EXISTS idx_dca_exec_order ON dca_executions(order_id);
    """)


# ── API Endpoints ──

@router.post("/create")
async def dca_create(req: dict, x_api_key: str = Header(None, alias="X-API-Key")):
    """Create a DCA order. Buys to_token with USDC at regular intervals."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    agent = await _get_agent(x_api_key)

    to_token = (req.get("to_token") or "").upper()
    amount_usdc = float(req.get("amount_usdc", 0))
    frequency = (req.get("frequency") or "weekly").lower()
    wallet = req.get("wallet", "")

    # Validation
    if not to_token or to_token not in DCA_TOKENS:
        raise HTTPException(400, f"Invalid to_token. Supported: {sorted(DCA_TOKENS)}")
    if to_token == "USDC":
        raise HTTPException(400, "Cannot DCA into USDC (from_token is already USDC)")
    if amount_usdc < DCA_MIN_AMOUNT:
        raise HTTPException(400, f"Minimum amount: {DCA_MIN_AMOUNT} USDC")
    if amount_usdc > DCA_MAX_AMOUNT:
        raise HTTPException(400, f"Maximum amount: {DCA_MAX_AMOUNT} USDC per execution")
    if frequency not in FREQUENCY_SECONDS:
        raise HTTPException(400, f"Invalid frequency. Use: {list(FREQUENCY_SECONDS.keys())}")
    if not wallet or len(wallet) < 20:
        raise HTTPException(400, "Valid wallet address required (min 20 chars)")

    now = int(time.time())
    next_run = now + FREQUENCY_SECONDS[frequency]
    order_id = str(uuid.uuid4())

    db = await _get_db()
    await db.raw_execute(
        "INSERT INTO dca_orders(order_id, api_key, wallet, from_token, to_token, "
        "amount_usdc, frequency, status, next_run) VALUES(?,?,?,?,?,?,?,?,?)",
        (order_id, x_api_key, wallet, "USDC", to_token, amount_usdc,
         frequency, "active", next_run))

    # Get current price for info
    current_price = 0.0
    try:
        from trading.price_oracle import get_price
        current_price = await get_price(to_token)
    except Exception:
        pass

    logger.info("[DCA] Created order %s: %s USDC -> %s (%s) for %s",
                order_id[:8], amount_usdc, to_token, frequency, wallet[:8])

    return {
        "success": True,
        "order_id": order_id,
        "to_token": to_token,
        "amount_usdc": amount_usdc,
        "frequency": frequency,
        "wallet": wallet,
        "next_run_ts": next_run,
        "current_price": current_price,
        "commission_bps": DCA_COMMISSION_BPS,
        "status": "active",
    }


@router.get("/my")
async def dca_my_orders(
    x_api_key: str = Header(None, alias="X-API-Key"),
    status: str = None,
):
    """List my DCA orders."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    await _get_agent(x_api_key)

    db = await _get_db()
    _cols = ("order_id, api_key, wallet, from_token, to_token, amount_usdc, "
             "frequency, status, total_executed, total_invested_usdc, total_received, "
             "next_run, last_run, fail_streak, created_at")
    if status:
        rows = await db.raw_execute_fetchall(
            f"SELECT {_cols} FROM dca_orders WHERE api_key=? AND status=? ORDER BY created_at DESC",
            (x_api_key, status))
    else:
        rows = await db.raw_execute_fetchall(
            f"SELECT {_cols} FROM dca_orders WHERE api_key=? ORDER BY created_at DESC",
            (x_api_key,))

    orders = []
    for r in rows:
        row = dict(r)
        avg_price = 0.0
        invested = float(row.get("total_invested_usdc", 0) or 0)
        received = float(row.get("total_received", 0) or 0)
        if received > 0:
            avg_price = round(invested / received, 6)
        row["avg_price"] = avg_price
        orders.append(row)

    return {"orders": orders, "total": len(orders)}


@router.get("/executions/{order_id}")
async def dca_executions(
    order_id: str,
    x_api_key: str = Header(None, alias="X-API-Key"),
    limit: int = 20,
):
    """Get execution history for a DCA order."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    await _get_agent(x_api_key)

    db = await _get_db()
    # Verify ownership
    order = await db.raw_execute_fetchall(
        "SELECT order_id FROM dca_orders WHERE order_id=? AND api_key=?",
        (order_id, x_api_key))
    if not order:
        raise HTTPException(404, "Order not found or not owned by you")

    limit = min(limit, 200)
    rows = await db.raw_execute_fetchall(
        "SELECT exec_id, order_id, price_usdc, amount_usdc, received, "
        "commission_usdc, created_at "
        "FROM dca_executions WHERE order_id=? ORDER BY created_at DESC LIMIT ?",
        (order_id, limit))

    return {"executions": [dict(r) for r in rows], "total": len(rows)}


@router.delete("/{order_id}")
async def dca_cancel(order_id: str, x_api_key: str = Header(None, alias="X-API-Key")):
    """Cancel a DCA order."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    await _get_agent(x_api_key)

    db = await _get_db()
    # Verify ownership
    order = await db.raw_execute_fetchall(
        "SELECT order_id, status FROM dca_orders WHERE order_id=? AND api_key=?",
        (order_id, x_api_key))
    if not order:
        raise HTTPException(404, "Order not found or not owned by you")
    if dict(order[0]).get("status") == "cancelled":
        raise HTTPException(400, "Order already cancelled")

    await db.raw_execute(
        "UPDATE dca_orders SET status='cancelled' WHERE order_id=? AND api_key=?",
        (order_id, x_api_key))

    logger.info("[DCA] Cancelled order %s", order_id[:8])
    return {"success": True, "order_id": order_id, "status": "cancelled"}


@router.get("/stats")
async def dca_stats():
    """Public DCA stats — no auth required."""
    try:
        db = await _get_db()
        active = await db.raw_execute_fetchall(
            "SELECT COUNT(*) as cnt FROM dca_orders WHERE status='active'")
        total_invested = await db.raw_execute_fetchall(
            "SELECT COALESCE(SUM(total_invested_usdc), 0) as total "
            "FROM dca_orders WHERE status IN ('active', 'completed')")

        active_count = dict(active[0]).get("cnt", 0) if active else 0
        invested_sum = float(dict(total_invested[0]).get("total", 0)) if total_invested else 0.0

        return {
            "active_bots": active_count,
            "total_invested_usdc": round(invested_sum, 2),
            "supported_tokens": sorted(DCA_TOKENS),
            "frequencies": list(FREQUENCY_SECONDS.keys()),
            "commission_bps": DCA_COMMISSION_BPS,
            "min_amount_usdc": DCA_MIN_AMOUNT,
            "max_amount_usdc": DCA_MAX_AMOUNT,
        }
    except Exception as e:
        from core.error_utils import safe_error
        logger.error("[DCA] Stats error: %s", e)
        return {
            "active_bots": 0,
            "total_invested_usdc": 0,
            "supported_tokens": sorted(DCA_TOKENS),
            "frequencies": list(FREQUENCY_SECONDS.keys()),
            "commission_bps": DCA_COMMISSION_BPS,
            "error": safe_error(e),
        }


# ── Background Worker ──

async def dca_worker():
    """Background: execute due DCA orders every 60 seconds."""
    logger.info("[DCA] Worker started — checking every 60s")
    while True:
        try:
            await asyncio.sleep(60)
            db = await _get_db()
            now = int(time.time())

            due_orders = await db.raw_execute_fetchall(
                "SELECT order_id, api_key, wallet, from_token, to_token, "
                "amount_usdc, frequency, status, total_executed, "
                "total_invested_usdc, total_received, next_run, fail_streak "
                "FROM dca_orders WHERE status='active' AND next_run<=?",
                (now,))

            if not due_orders:
                continue

            for row in due_orders:
                order = dict(row)
                order_id = order["order_id"]
                to_token = order["to_token"]
                amount_usdc = float(order["amount_usdc"])

                try:
                    # 1. Get current price
                    from trading.price_oracle import get_price
                    price = await get_price(to_token)
                    if price <= 0:
                        raise ValueError(f"Price unavailable for {to_token}")

                    # 2. Calculate token amount (amount_usdc / price)
                    raw_received = amount_usdc / price

                    # 3. Apply commission (0.10%)
                    commission_usdc = round(amount_usdc * DCA_COMMISSION_BPS / 10000, 6)
                    effective_amount = amount_usdc - commission_usdc
                    received = round(effective_amount / price, 8)

                    # 4. Record execution
                    exec_id = str(uuid.uuid4())
                    await db.raw_execute(
                        "INSERT INTO dca_executions(exec_id, order_id, price_usdc, "
                        "amount_usdc, received, commission_usdc) VALUES(?,?,?,?,?,?)",
                        (exec_id, order_id, round(price, 6), amount_usdc,
                         received, commission_usdc))

                    # 5. Update order stats
                    new_total_executed = int(order.get("total_executed", 0) or 0) + 1
                    new_total_invested = float(order.get("total_invested_usdc", 0) or 0) + amount_usdc
                    new_total_received = float(order.get("total_received", 0) or 0) + received
                    new_next_run = now + FREQUENCY_SECONDS.get(order["frequency"], 604800)

                    await db.raw_execute(
                        "UPDATE dca_orders SET total_executed=?, total_invested_usdc=?, "
                        "total_received=?, next_run=?, last_run=?, fail_streak=0 "
                        "WHERE order_id=?",
                        (new_total_executed, round(new_total_invested, 6),
                         round(new_total_received, 8), new_next_run, now, order_id))

                    logger.info(
                        "[DCA] Executed %s: %.2f USDC -> %.8f %s @ $%.4f (commission $%.4f)",
                        order_id[:8], amount_usdc, received, to_token,
                        price, commission_usdc)

                    # 6. Try Telegram alert (best effort)
                    try:
                        await _send_dca_alert(order, price, received, commission_usdc)
                    except Exception:
                        pass

                except Exception as e:
                    # Increment fail streak
                    fail_streak = int(order.get("fail_streak", 0) or 0) + 1
                    if fail_streak >= DCA_MAX_FAIL_STREAK:
                        await db.raw_execute(
                            "UPDATE dca_orders SET status='paused', fail_streak=? "
                            "WHERE order_id=?",
                            (fail_streak, order_id))
                        logger.warning(
                            "[DCA] Paused order %s after %d consecutive failures: %s",
                            order_id[:8], fail_streak, e)
                    else:
                        # Push next_run by 5 minutes to retry later
                        retry_next = now + 300
                        await db.raw_execute(
                            "UPDATE dca_orders SET fail_streak=?, next_run=? "
                            "WHERE order_id=?",
                            (fail_streak, retry_next, order_id))
                        logger.error(
                            "[DCA] Execution failed for %s (streak %d): %s",
                            order_id[:8], fail_streak, e)

                # Small delay between orders to avoid flooding
                await asyncio.sleep(1)

        except Exception as e:
            logger.error("[DCA] Worker error: %s", e)


async def _send_dca_alert(order: dict, price: float, received: float, commission: float):
    """Best-effort Telegram notification for DCA execution."""
    try:
        import os
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        if not bot_token:
            return
        db = await _get_db()
        agent = await db.raw_execute_fetchall(
            "SELECT telegram_chat_id FROM agents WHERE api_key=?", (order["api_key"],))
        if not agent:
            return
        chat_id = dict(agent[0]).get("telegram_chat_id")
        if not chat_id:
            return
        from core.http_client import get_http_client
        invested = float(order.get("total_invested_usdc", 0) or 0) + float(order["amount_usdc"])
        msg = (f"DCA Executed\nToken: {order['to_token']}\n"
               f"Spent: ${float(order['amount_usdc']):.2f} USDC\n"
               f"Received: {received:.8f} {order['to_token']}\n"
               f"Price: ${price:.4f} | Commission: ${commission:.4f}\n"
               f"Total invested: ${invested:.2f}")
        client = get_http_client()
        await client.post(f"https://api.telegram.org/bot{bot_token}/sendMessage",
                          json={"chat_id": chat_id, "text": msg}, timeout=5)
    except Exception:
        pass


def get_router():
    return router
