"""MAXIA V12 — DCA Bot: Dollar-Cost Averaging automated trading
Real Jupiter swap execution — user signs pending transactions via Phantom wallet.
"""
import logging
import asyncio
import json
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
DCA_PENDING_TX_EXPIRY_SECONDS = 120  # Jupiter txs expire after ~2 minutes

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
        CREATE TABLE IF NOT EXISTS dca_pending_txs (
            tx_id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL,
            swap_transaction TEXT NOT NULL,
            quote_data TEXT NOT NULL,
            amount_usdc NUMERIC(18,6) NOT NULL,
            to_token TEXT NOT NULL,
            price_usdc NUMERIC(18,6) NOT NULL,
            commission_usdc NUMERIC(18,6) NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at INTEGER DEFAULT (strftime('%s','now')),
            signed_at INTEGER DEFAULT 0,
            tx_signature TEXT DEFAULT '');
        CREATE INDEX IF NOT EXISTS idx_dca_pending_order ON dca_pending_txs(order_id);
        CREATE INDEX IF NOT EXISTS idx_dca_pending_status ON dca_pending_txs(status);
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
    """Background: create pending Jupiter swap txs for due DCA orders every 60s.

    Instead of simulating executions in DB, the worker:
    1. Gets a real Jupiter quote for the swap
    2. Builds an unsigned swap transaction via Jupiter API
    3. Stores the pending tx in dca_pending_txs (status='pending')
    4. User retrieves and signs via Phantom, then calls /api/dca/confirm/{tx_id}
    """
    logger.info("[DCA] Worker started — checking every 60s (Jupiter real swaps)")
    while True:
        try:
            await asyncio.sleep(60)
            db = await _get_db()
            now = int(time.time())

            # Expire stale pending txs (older than 2 minutes)
            await db.raw_execute(
                "UPDATE dca_pending_txs SET status='expired' "
                "WHERE status='pending' AND created_at < ?",
                (now - DCA_PENDING_TX_EXPIRY_SECONDS,))

            due_orders = await db.raw_execute_fetchall(
                "SELECT order_id, api_key, wallet, from_token, to_token, "
                "amount_usdc, frequency, status, total_executed, "
                "total_invested_usdc, total_received, next_run, fail_streak "
                "FROM dca_orders WHERE status='active' AND next_run<=?",
                (now,))

            if not due_orders:
                continue

            from blockchain.jupiter_router import get_quote, execute_swap, USDC_MINT
            from trading.crypto_swap import SUPPORTED_TOKENS

            for row in due_orders:
                order = dict(row)
                order_id = order["order_id"]
                to_token = order["to_token"]
                amount_usdc = float(order["amount_usdc"])
                wallet = order["wallet"]

                try:
                    # 1. Resolve token mint address
                    token_info = SUPPORTED_TOKENS.get(to_token.upper())
                    if not token_info:
                        raise ValueError(f"Token {to_token} not supported — no mint found")
                    token_mint = token_info["mint"]
                    token_decimals = token_info.get("decimals", 6)

                    # 2. Calculate commission and effective swap amount
                    commission_usdc = round(amount_usdc * DCA_COMMISSION_BPS / 10000, 6)
                    effective_amount = amount_usdc - commission_usdc
                    # USDC has 6 decimals — convert to raw lamports
                    amount_raw = int(effective_amount * 1_000_000)

                    if amount_raw <= 0:
                        raise ValueError(f"Effective amount too small after commission: {effective_amount}")

                    # 3. Get real Jupiter quote
                    quote_result = await get_quote(USDC_MINT, token_mint, amount_raw)
                    if not quote_result.get("success"):
                        raise ValueError(
                            f"Jupiter quote failed for {to_token}: "
                            f"{quote_result.get('error', 'unknown')}")

                    raw_quote = quote_result.get("raw_quote", {})
                    out_amount_raw = int(quote_result.get("outAmount", "0"))
                    received = out_amount_raw / (10 ** token_decimals) if out_amount_raw > 0 else 0
                    price_usdc = round(effective_amount / received, 6) if received > 0 else 0

                    # 4. Build unsigned swap transaction
                    swap_result = await execute_swap(raw_quote, wallet)
                    if not swap_result.get("success"):
                        raise ValueError(
                            f"Jupiter swap build failed for {to_token}: "
                            f"{swap_result.get('error', 'unknown')}")

                    swap_transaction = swap_result.get("swapTransaction", "")
                    if not swap_transaction:
                        raise ValueError("Jupiter returned empty swapTransaction")

                    # 5. Store pending transaction
                    tx_id = str(uuid.uuid4())
                    quote_json = json.dumps({
                        "inAmount": quote_result.get("inAmount", "0"),
                        "outAmount": quote_result.get("outAmount", "0"),
                        "priceImpactPct": quote_result.get("priceImpactPct", "0"),
                        "lastValidBlockHeight": swap_result.get("lastValidBlockHeight", 0),
                    })

                    await db.raw_execute(
                        "INSERT INTO dca_pending_txs(tx_id, order_id, swap_transaction, "
                        "quote_data, amount_usdc, to_token, price_usdc, commission_usdc, "
                        "status, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (tx_id, order_id, swap_transaction, quote_json,
                         amount_usdc, to_token, price_usdc, commission_usdc,
                         "pending", now))

                    # 6. Advance next_run so worker doesn't re-trigger this order
                    #    (stats NOT updated yet — wait for user confirmation)
                    new_next_run = now + FREQUENCY_SECONDS.get(order["frequency"], 604800)
                    await db.raw_execute(
                        "UPDATE dca_orders SET next_run=?, last_run=?, fail_streak=0 "
                        "WHERE order_id=?",
                        (new_next_run, now, order_id))

                    logger.info(
                        "[DCA] Pending tx created for order %s — "
                        "%.2f USDC -> %s @ $%.4f (commission $%.4f) — "
                        "awaiting user signature [tx_id=%s]",
                        order_id[:8], amount_usdc, to_token,
                        price_usdc, commission_usdc, tx_id[:8])

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

                # Small delay between orders to avoid flooding Jupiter
                await asyncio.sleep(1)

        except Exception as e:
            logger.error("[DCA] Worker error: %s", e)


@router.get("/pending/{order_id}")
async def dca_pending_txs(
    order_id: str,
    x_api_key: str = Header(None, alias="X-API-Key"),
):
    """Get pending unsigned swap transactions for a DCA order."""
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

    now = int(time.time())

    # Expire stale pending txs before returning
    await db.raw_execute(
        "UPDATE dca_pending_txs SET status='expired' "
        "WHERE order_id=? AND status='pending' AND created_at < ?",
        (order_id, now - DCA_PENDING_TX_EXPIRY_SECONDS))

    rows = await db.raw_execute_fetchall(
        "SELECT tx_id, swap_transaction, amount_usdc, to_token, "
        "price_usdc, commission_usdc, created_at, quote_data "
        "FROM dca_pending_txs WHERE order_id=? AND status='pending' "
        "ORDER BY created_at DESC",
        (order_id,))

    pending = []
    for r in rows:
        row = dict(r)
        expires_at = int(row.get("created_at", 0)) + DCA_PENDING_TX_EXPIRY_SECONDS
        pending.append({
            "tx_id": row["tx_id"],
            "swap_transaction": row["swap_transaction"],
            "amount_usdc": float(row["amount_usdc"]),
            "to_token": row["to_token"],
            "price_usdc": float(row["price_usdc"]),
            "commission_usdc": float(row["commission_usdc"]),
            "created_at": row["created_at"],
            "expires_at": expires_at,
        })

    return {"pending": pending, "total": len(pending)}


@router.post("/confirm/{tx_id}")
async def dca_confirm_tx(
    tx_id: str,
    req: dict,
    x_api_key: str = Header(None, alias="X-API-Key"),
):
    """Confirm a signed DCA swap transaction after user broadcasts it.

    Body: {"tx_signature": "the_solana_tx_signature"}
    Verifies on-chain, then updates order stats and records execution.
    """
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    await _get_agent(x_api_key)

    tx_signature = (req.get("tx_signature") or "").strip()
    if not tx_signature or len(tx_signature) < 40:
        raise HTTPException(400, "Valid tx_signature required (Solana transaction signature)")

    db = await _get_db()

    # Fetch the pending tx
    rows = await db.raw_execute_fetchall(
        "SELECT pt.tx_id, pt.order_id, pt.amount_usdc, pt.to_token, "
        "pt.price_usdc, pt.commission_usdc, pt.status, pt.created_at, "
        "pt.quote_data, o.api_key, o.frequency, o.total_executed, "
        "o.total_invested_usdc, o.total_received "
        "FROM dca_pending_txs pt "
        "JOIN dca_orders o ON o.order_id = pt.order_id "
        "WHERE pt.tx_id=? AND o.api_key=?",
        (tx_id, x_api_key))

    if not rows:
        raise HTTPException(404, "Pending transaction not found or not owned by you")

    pending = dict(rows[0])

    if pending["status"] != "pending":
        raise HTTPException(400, f"Transaction is already '{pending['status']}' — cannot confirm")

    # Check expiry
    now = int(time.time())
    created = int(pending.get("created_at", 0))
    if now - created > DCA_PENDING_TX_EXPIRY_SECONDS:
        await db.raw_execute(
            "UPDATE dca_pending_txs SET status='expired' WHERE tx_id=?", (tx_id,))
        raise HTTPException(410, "Transaction expired — Jupiter transactions are valid for ~2 minutes")

    # Verify on-chain
    try:
        from blockchain.solana_verifier import verify_transaction
        verification = await verify_transaction(tx_signature)
    except Exception as e:
        logger.error("[DCA] On-chain verification error for tx %s: %s", tx_id[:8], e)
        raise HTTPException(502, "Failed to verify transaction on-chain — try again shortly")

    if not verification.get("valid"):
        raise HTTPException(
            400,
            f"Transaction not confirmed on-chain: {verification.get('error', 'not found or not finalized')}")

    # Parse quote data for received amount
    amount_usdc = float(pending["amount_usdc"])
    to_token = pending["to_token"]
    price_usdc = float(pending["price_usdc"])
    commission_usdc = float(pending["commission_usdc"])

    # Calculate received from quote
    quote_data = {}
    try:
        quote_data = json.loads(pending.get("quote_data", "{}"))
    except (json.JSONDecodeError, TypeError):
        pass

    out_amount_raw = int(quote_data.get("outAmount", "0"))
    from trading.crypto_swap import SUPPORTED_TOKENS
    token_info = SUPPORTED_TOKENS.get(to_token.upper(), {})
    token_decimals = token_info.get("decimals", 6)
    received = out_amount_raw / (10 ** token_decimals) if out_amount_raw > 0 else 0

    # 1. Mark pending tx as confirmed
    await db.raw_execute(
        "UPDATE dca_pending_txs SET status='confirmed', signed_at=?, tx_signature=? "
        "WHERE tx_id=?",
        (now, tx_signature, tx_id))

    # 2. Insert into dca_executions
    exec_id = str(uuid.uuid4())
    await db.raw_execute(
        "INSERT INTO dca_executions(exec_id, order_id, price_usdc, "
        "amount_usdc, received, commission_usdc) VALUES(?,?,?,?,?,?)",
        (exec_id, pending["order_id"], price_usdc, amount_usdc,
         round(received, 8), commission_usdc))

    # 3. Update dca_orders stats
    order_id = pending["order_id"]
    new_total_executed = int(pending.get("total_executed", 0) or 0) + 1
    new_total_invested = float(pending.get("total_invested_usdc", 0) or 0) + amount_usdc
    new_total_received = float(pending.get("total_received", 0) or 0) + received

    await db.raw_execute(
        "UPDATE dca_orders SET total_executed=?, total_invested_usdc=?, "
        "total_received=? WHERE order_id=?",
        (new_total_executed, round(new_total_invested, 6),
         round(new_total_received, 8), order_id))

    logger.info(
        "[DCA] Confirmed tx %s for order %s: %.2f USDC -> %.8f %s @ $%.4f "
        "(on-chain: %s)",
        tx_id[:8], order_id[:8], amount_usdc, received, to_token,
        price_usdc, tx_signature[:16])

    # Best-effort Telegram alert
    try:
        order_row = await db.raw_execute_fetchall(
            "SELECT * FROM dca_orders WHERE order_id=?", (order_id,))
        if order_row:
            await _send_dca_alert(dict(order_row[0]), price_usdc, received, commission_usdc)
    except Exception:
        pass

    return {
        "success": True,
        "tx_id": tx_id,
        "order_id": order_id,
        "exec_id": exec_id,
        "tx_signature": tx_signature,
        "amount_usdc": amount_usdc,
        "received": round(received, 8),
        "to_token": to_token,
        "price_usdc": price_usdc,
        "commission_usdc": commission_usdc,
        "status": "confirmed",
    }


@router.delete("/pending/{tx_id}")
async def dca_cancel_pending_tx(
    tx_id: str,
    x_api_key: str = Header(None, alias="X-API-Key"),
):
    """Cancel a pending DCA swap transaction (sets status='expired')."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    await _get_agent(x_api_key)

    db = await _get_db()

    # Verify ownership via join
    rows = await db.raw_execute_fetchall(
        "SELECT pt.tx_id, pt.status "
        "FROM dca_pending_txs pt "
        "JOIN dca_orders o ON o.order_id = pt.order_id "
        "WHERE pt.tx_id=? AND o.api_key=?",
        (tx_id, x_api_key))

    if not rows:
        raise HTTPException(404, "Pending transaction not found or not owned by you")

    pending = dict(rows[0])
    if pending["status"] != "pending":
        raise HTTPException(400, f"Transaction is already '{pending['status']}' — cannot cancel")

    await db.raw_execute(
        "UPDATE dca_pending_txs SET status='expired' WHERE tx_id=?", (tx_id,))

    logger.info("[DCA] Cancelled pending tx %s", tx_id[:8])
    return {"success": True, "tx_id": tx_id, "status": "expired"}


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
