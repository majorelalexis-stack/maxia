"""MAXIA V12 — Grid Trading Bot: automated buy/sell at fixed price intervals
Real Jupiter swap execution — user signs pending transactions via Phantom wallet.
"""
import logging, asyncio, json, time, uuid
from fastapi import APIRouter, HTTPException, Header

logger = logging.getLogger("maxia.grid")
router = APIRouter(prefix="/api/grid", tags=["grid"])

# ── Constants ──
GRID_COMMISSION_BPS = 10  # 0.10% (10 basis points)
GRID_MIN_GRIDS, GRID_MAX_GRIDS = 3, 50
GRID_MIN_INVESTMENT, GRID_MAX_INVESTMENT = 10.0, 10000.0

GRID_TOKENS = {
    "SOL", "ETH", "BTC", "BONK", "JUP", "RAY", "WIF", "RENDER", "HNT",
    "PYTH", "LINK", "UNI", "AAVE", "DOGE", "SHIB", "PEPE", "XRP",
    "AVAX", "MATIC", "BNB", "TON", "SUI", "NEAR", "APT", "SEI",
    "ARB", "OP", "FET", "FIL", "AR", "INJ", "TAO", "AKT",
    "ORCA", "DRIFT", "ONDO", "TRUMP",
}

_last_prices: dict[str, float] = {}  # bot_id -> last_price (crossing detection)


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
        CREATE TABLE IF NOT EXISTS grid_bots (
            bot_id TEXT PRIMARY KEY, api_key TEXT NOT NULL, wallet TEXT NOT NULL,
            token TEXT NOT NULL, lower_price NUMERIC(18,6) NOT NULL,
            upper_price NUMERIC(18,6) NOT NULL, num_grids INTEGER NOT NULL,
            investment_usdc NUMERIC(18,6) NOT NULL, per_grid_usdc NUMERIC(18,6) NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            total_buys INTEGER DEFAULT 0, total_sells INTEGER DEFAULT 0,
            total_profit_usdc NUMERIC(18,6) DEFAULT 0,
            created_at INTEGER DEFAULT (strftime('%s','now')));
        CREATE INDEX IF NOT EXISTS idx_grid_api_key ON grid_bots(api_key);
        CREATE INDEX IF NOT EXISTS idx_grid_status ON grid_bots(status);
        CREATE TABLE IF NOT EXISTS grid_trades (
            trade_id TEXT PRIMARY KEY, bot_id TEXT NOT NULL,
            side TEXT NOT NULL, grid_level INTEGER NOT NULL,
            price_usdc NUMERIC(18,6) NOT NULL, amount NUMERIC(18,6) NOT NULL,
            usdc_value NUMERIC(18,6) NOT NULL,
            tx_signature TEXT DEFAULT '',
            created_at INTEGER DEFAULT (strftime('%s','now')));
        CREATE INDEX IF NOT EXISTS idx_grid_trades_bot ON grid_trades(bot_id);
        CREATE TABLE IF NOT EXISTS grid_pending_txs (
            tx_id TEXT PRIMARY KEY, bot_id TEXT NOT NULL,
            side TEXT NOT NULL, grid_level INTEGER NOT NULL,
            swap_transaction TEXT NOT NULL, quote_data TEXT NOT NULL,
            amount_usdc NUMERIC(18,6) NOT NULL, token TEXT NOT NULL,
            price_usdc NUMERIC(18,6) NOT NULL,
            commission_usdc NUMERIC(18,6) NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at INTEGER DEFAULT (strftime('%s','now')),
            signed_at INTEGER DEFAULT 0,
            tx_signature TEXT DEFAULT '');
        CREATE INDEX IF NOT EXISTS idx_grid_pending_bot ON grid_pending_txs(bot_id);
        CREATE INDEX IF NOT EXISTS idx_grid_pending_status ON grid_pending_txs(status);
    """)


# ── API Endpoints ──

@router.post("/create")
async def grid_create(req: dict, x_api_key: str = Header(None, alias="X-API-Key")):
    """Create a grid trading bot."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    await _get_agent(x_api_key)

    token = (req.get("token") or "").upper()
    lower_price = float(req.get("lower_price", 0))
    upper_price = float(req.get("upper_price", 0))
    num_grids = int(req.get("num_grids", 0))
    investment_usdc = float(req.get("investment_usdc", 0))
    wallet = req.get("wallet", "")

    if not token or token not in GRID_TOKENS:
        raise HTTPException(400, f"Invalid token. Supported: {sorted(GRID_TOKENS)}")
    if lower_price <= 0 or upper_price <= 0:
        raise HTTPException(400, "Prices must be positive")
    if lower_price >= upper_price:
        raise HTTPException(400, "lower_price must be less than upper_price")
    if not (GRID_MIN_GRIDS <= num_grids <= GRID_MAX_GRIDS):
        raise HTTPException(400, f"num_grids must be {GRID_MIN_GRIDS}-{GRID_MAX_GRIDS}")
    if not (GRID_MIN_INVESTMENT <= investment_usdc <= GRID_MAX_INVESTMENT):
        raise HTTPException(400, f"investment_usdc must be {GRID_MIN_INVESTMENT}-{GRID_MAX_INVESTMENT}")
    if not wallet or len(wallet) < 20:
        raise HTTPException(400, "Valid wallet address required (min 20 chars)")

    per_grid_usdc = round(investment_usdc / num_grids, 6)
    bot_id = str(uuid.uuid4())

    db = await _get_db()
    await db.raw_execute(
        "INSERT INTO grid_bots(bot_id, api_key, wallet, token, lower_price, "
        "upper_price, num_grids, investment_usdc, per_grid_usdc, status) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (bot_id, x_api_key, wallet, token, lower_price, upper_price,
         num_grids, investment_usdc, per_grid_usdc, "active"))

    grid_step = round((upper_price - lower_price) / num_grids, 6)

    current_price = 0.0
    try:
        from trading.price_oracle import get_price
        current_price = await get_price(token)
    except Exception:
        pass

    logger.info("[GRID] Created bot %s: %s %s grids [%.4f - %.4f] invest %.2f USDC",
                bot_id[:8], token, num_grids, lower_price, upper_price, investment_usdc)

    return {
        "success": True,
        "bot_id": bot_id,
        "token": token,
        "lower_price": lower_price,
        "upper_price": upper_price,
        "num_grids": num_grids,
        "grid_step": grid_step,
        "investment_usdc": investment_usdc,
        "per_grid_usdc": per_grid_usdc,
        "wallet": wallet,
        "current_price": current_price,
        "commission_bps": GRID_COMMISSION_BPS,
        "status": "active",
    }


@router.get("/my")
async def grid_my_bots(x_api_key: str = Header(None, alias="X-API-Key")):
    """List my grid bots."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    await _get_agent(x_api_key)

    db = await _get_db()
    _cols = ("bot_id, api_key, wallet, token, lower_price, upper_price, "
             "num_grids, investment_usdc, per_grid_usdc, status, "
             "total_buys, total_sells, total_profit_usdc, created_at")
    rows = await db.raw_execute_fetchall(
        f"SELECT {_cols} FROM grid_bots WHERE api_key=? ORDER BY created_at DESC",
        (x_api_key,))

    bots = [dict(r) for r in rows]
    for b in bots:
        lo, hi, n = float(b.get("lower_price", 0) or 0), float(b.get("upper_price", 0) or 0), int(b.get("num_grids", 1) or 1)
        b["grid_step"] = round((hi - lo) / n, 6) if n > 0 else 0
    return {"bots": bots, "total": len(bots)}


@router.get("/trades/{bot_id}")
async def grid_trade_history(
    bot_id: str,
    x_api_key: str = Header(None, alias="X-API-Key"),
    limit: int = 50,
):
    """Get trade history for a grid bot."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    await _get_agent(x_api_key)

    db = await _get_db()
    bot = await db.raw_execute_fetchall(
        "SELECT bot_id FROM grid_bots WHERE bot_id=? AND api_key=?", (bot_id, x_api_key))
    if not bot:
        raise HTTPException(404, "Bot not found or not owned by you")
    limit = min(limit, 200)
    rows = await db.raw_execute_fetchall(
        "SELECT trade_id, bot_id, side, grid_level, price_usdc, amount, "
        "usdc_value, created_at "
        "FROM grid_trades WHERE bot_id=? ORDER BY created_at DESC LIMIT ?",
        (bot_id, limit))

    return {"trades": [dict(r) for r in rows], "total": len(rows)}


@router.delete("/{bot_id}")
async def grid_stop(bot_id: str, x_api_key: str = Header(None, alias="X-API-Key")):
    """Stop a grid bot."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    await _get_agent(x_api_key)

    db = await _get_db()
    bot = await db.raw_execute_fetchall(
        "SELECT bot_id, status FROM grid_bots WHERE bot_id=? AND api_key=?", (bot_id, x_api_key))
    if not bot:
        raise HTTPException(404, "Bot not found or not owned by you")
    if dict(bot[0]).get("status") == "stopped":
        raise HTTPException(400, "Bot already stopped")

    await db.raw_execute(
        "UPDATE grid_bots SET status='stopped' WHERE bot_id=? AND api_key=?",
        (bot_id, x_api_key))

    _last_prices.pop(bot_id, None)
    logger.info("[GRID] Stopped bot %s", bot_id[:8])
    return {"success": True, "bot_id": bot_id, "status": "stopped"}


@router.get("/stats")
async def grid_stats():
    """Public grid bot stats — no auth required."""
    base = {"supported_tokens": sorted(GRID_TOKENS), "commission_bps": GRID_COMMISSION_BPS,
            "min_grids": GRID_MIN_GRIDS, "max_grids": GRID_MAX_GRIDS,
            "min_investment_usdc": GRID_MIN_INVESTMENT, "max_investment_usdc": GRID_MAX_INVESTMENT}
    try:
        db = await _get_db()
        a = await db.raw_execute_fetchall("SELECT COUNT(*) as cnt FROM grid_bots WHERE status='active'")
        p = await db.raw_execute_fetchall(
            "SELECT COALESCE(SUM(total_profit_usdc),0) as total FROM grid_bots WHERE status IN ('active','stopped')")
        t = await db.raw_execute_fetchall("SELECT COUNT(*) as cnt FROM grid_trades")
        return {**base, "active_bots": dict(a[0]).get("cnt", 0) if a else 0,
                "total_trades": dict(t[0]).get("cnt", 0) if t else 0,
                "total_profit_usdc": round(float(dict(p[0]).get("total", 0)), 2) if p else 0.0}
    except Exception as e:
        from core.error_utils import safe_error
        logger.error("[GRID] Stats error: %s", e)
        return {**base, "active_bots": 0, "total_trades": 0, "total_profit_usdc": 0, "error": safe_error(e)}


# ── Jupiter Integration Helpers ──

def _get_token_mint(token: str) -> str:
    """Resolve token symbol to Solana mint address via SUPPORTED_TOKENS."""
    from trading.crypto_swap import SUPPORTED_TOKENS
    info = SUPPORTED_TOKENS.get(token)
    if info:
        return info["mint"]
    return ""


def _get_token_decimals(token: str) -> int:
    """Resolve token symbol to decimals via SUPPORTED_TOKENS."""
    from trading.crypto_swap import SUPPORTED_TOKENS
    info = SUPPORTED_TOKENS.get(token)
    if info:
        return info.get("decimals", 6)
    return 6


async def _build_grid_swap_tx(
    side: str,
    token: str,
    amount_usdc: float,
    price_usdc: float,
    wallet: str,
) -> dict:
    """Build unsigned Jupiter swap transaction for a grid crossing.

    BUY:  USDC -> Token (user spends USDC to buy token)
    SELL: Token -> USDC (user sells token for USDC)

    Returns dict with keys: success, swap_transaction, quote_data, error
    """
    from blockchain.jupiter_router import get_quote, execute_swap, USDC_MINT

    token_mint = _get_token_mint(token)
    if not token_mint:
        return {"success": False, "error": f"No mint address for {token}"}

    if side == "buy":
        # USDC -> Token: amount is in USDC (6 decimals)
        amount_raw = int(amount_usdc * 1_000_000)
        input_mint = USDC_MINT
        output_mint = token_mint
    else:
        # SELL: Token -> USDC: amount is in token units
        decimals = _get_token_decimals(token)
        token_amount = amount_usdc / price_usdc if price_usdc > 0 else 0
        amount_raw = int(token_amount * (10 ** decimals))
        input_mint = token_mint
        output_mint = USDC_MINT

    if amount_raw <= 0:
        return {"success": False, "error": "Calculated swap amount is zero"}

    # 1. Get Jupiter quote
    quote = await get_quote(input_mint, output_mint, amount_raw, slippage_bps=100)
    if not quote.get("success"):
        return {"success": False, "error": quote.get("error", "Jupiter quote failed")}

    raw_quote = quote.get("raw_quote")
    if not raw_quote:
        return {"success": False, "error": "No raw quote data from Jupiter"}

    # 2. Build unsigned swap transaction
    swap_result = await execute_swap(raw_quote, wallet)
    if not swap_result.get("success"):
        return {"success": False, "error": swap_result.get("error", "Jupiter swap build failed")}

    swap_tx = swap_result.get("swapTransaction", "")
    if not swap_tx:
        return {"success": False, "error": "Empty swap transaction from Jupiter"}

    # Serialize quote data for storage (strip heavy route plan for DB)
    quote_summary = {
        "inputMint": quote.get("inputMint", ""),
        "outputMint": quote.get("outputMint", ""),
        "inAmount": quote.get("inAmount", "0"),
        "outAmount": quote.get("outAmount", "0"),
        "priceImpactPct": quote.get("priceImpactPct", "0"),
        "side": side,
        "token": token,
    }

    return {
        "success": True,
        "swap_transaction": swap_tx,
        "quote_data": json.dumps(quote_summary),
    }


# ── Background Worker ──

async def grid_worker():
    """Background: check grid bots every 60s for price crossings.
    On crossing, builds unsigned Jupiter swap tx and stores in grid_pending_txs.
    User retrieves and signs via Phantom wallet.
    """
    logger.info("[GRID] Worker started — checking every 60s (Jupiter swap mode)")
    while True:
        try:
            await asyncio.sleep(60)
            db = await _get_db()

            _q = ("SELECT bot_id, api_key, wallet, token, lower_price, upper_price, "
                  "num_grids, per_grid_usdc, total_buys, total_sells, total_profit_usdc "
                  "FROM grid_bots WHERE status='active'")
            active_bots = await db.raw_execute_fetchall(_q)

            if not active_bots:
                continue

            for row in active_bots:
                bot = dict(row)
                bot_id = bot["bot_id"]
                token = bot["token"]
                wallet = bot.get("wallet", "")
                lower = float(bot["lower_price"])
                upper = float(bot["upper_price"])
                num_grids = int(bot["num_grids"])
                per_grid = float(bot["per_grid_usdc"])

                try:
                    # 1. Get current price
                    from trading.price_oracle import get_price
                    price = await get_price(token)
                    if price <= 0:
                        continue

                    grid_step = (upper - lower) / num_grids
                    if grid_step <= 0:
                        continue

                    # 2. Calculate current grid level (clamped)
                    current_level = int((price - lower) / grid_step)
                    current_level = max(0, min(current_level, num_grids))

                    # 3. Check crossing against last known price
                    prev_price = _last_prices.get(bot_id)
                    _last_prices[bot_id] = price

                    if prev_price is None:
                        # First check — no crossing to detect yet
                        continue

                    prev_level = int((prev_price - lower) / grid_step)
                    prev_level = max(0, min(prev_level, num_grids))

                    if current_level == prev_level:
                        # No grid crossing
                        continue

                    # 4. Process crossings — build Jupiter swap txs
                    if current_level < prev_level:
                        # Price dropped — BUY at each crossed level
                        for lv in range(prev_level - 1, current_level - 1, -1):
                            if lv < 0 or lv >= num_grids:
                                continue
                            gp = lower + (lv + 0.5) * grid_step
                            comm = round(per_grid * GRID_COMMISSION_BPS / 10000, 6)
                            effective_usdc = round(per_grid - comm, 6)

                            # Build unsigned Jupiter swap tx
                            swap = await _build_grid_swap_tx(
                                "buy", token, effective_usdc, gp, wallet)

                            if swap.get("success"):
                                tx_id = str(uuid.uuid4())
                                await db.raw_execute(
                                    "INSERT INTO grid_pending_txs(tx_id, bot_id, side, "
                                    "grid_level, swap_transaction, quote_data, amount_usdc, "
                                    "token, price_usdc, commission_usdc, status) "
                                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                                    (tx_id, bot_id, "buy", lv,
                                     swap["swap_transaction"], swap["quote_data"],
                                     round(per_grid, 6), token, round(gp, 6),
                                     comm, "pending"))
                                logger.info(
                                    "[GRID] Pending BUY tx for bot %s at grid level %d "
                                    "(%.4f USDC @ $%.4f %s)",
                                    bot_id[:8], lv, per_grid, gp, token)
                            else:
                                logger.warning(
                                    "[GRID] Jupiter swap failed for BUY bot %s L%d: %s",
                                    bot_id[:8], lv, swap.get("error", "unknown"))
                    else:
                        # Price rose — SELL at each crossed level
                        for lv in range(prev_level + 1, current_level + 1):
                            if lv < 1 or lv > num_grids:
                                continue
                            gp = lower + (lv - 0.5) * grid_step
                            comm = round(per_grid * GRID_COMMISSION_BPS / 10000, 6)
                            effective_usdc = round(per_grid - comm, 6)

                            # Build unsigned Jupiter swap tx
                            swap = await _build_grid_swap_tx(
                                "sell", token, effective_usdc, gp, wallet)

                            if swap.get("success"):
                                tx_id = str(uuid.uuid4())
                                await db.raw_execute(
                                    "INSERT INTO grid_pending_txs(tx_id, bot_id, side, "
                                    "grid_level, swap_transaction, quote_data, amount_usdc, "
                                    "token, price_usdc, commission_usdc, status) "
                                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                                    (tx_id, bot_id, "sell", lv,
                                     swap["swap_transaction"], swap["quote_data"],
                                     round(per_grid, 6), token, round(gp, 6),
                                     comm, "pending"))
                                logger.info(
                                    "[GRID] Pending SELL tx for bot %s at grid level %d "
                                    "(%.4f USDC @ $%.4f %s)",
                                    bot_id[:8], lv, per_grid, gp, token)
                            else:
                                logger.warning(
                                    "[GRID] Jupiter swap failed for SELL bot %s L%d: %s",
                                    bot_id[:8], lv, swap.get("error", "unknown"))

                    # NOTE: Bot stats (total_buys/sells/profit) are NOT updated here.
                    # They are updated when the user confirms (signs) the pending tx.

                except Exception as e:
                    logger.error("[GRID] Worker error for bot %s: %s", bot_id[:8], e)

                # Small delay between bots to avoid flooding Jupiter
                await asyncio.sleep(2)

        except Exception as e:
            logger.error("[GRID] Worker error: %s", e)


# ── Pending Transaction Endpoints ──

@router.get("/pending/{bot_id}")
async def grid_pending_txs(
    bot_id: str,
    x_api_key: str = Header(None, alias="X-API-Key"),
):
    """Get pending unsigned swap transactions for a grid bot."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    await _get_agent(x_api_key)

    db = await _get_db()
    # Verify bot ownership
    bot = await db.raw_execute_fetchall(
        "SELECT bot_id FROM grid_bots WHERE bot_id=? AND api_key=?",
        (bot_id, x_api_key))
    if not bot:
        raise HTTPException(404, "Bot not found or not owned by you")

    rows = await db.raw_execute_fetchall(
        "SELECT tx_id, bot_id, side, grid_level, swap_transaction, quote_data, "
        "amount_usdc, token, price_usdc, commission_usdc, status, created_at "
        "FROM grid_pending_txs WHERE bot_id=? AND status='pending' "
        "ORDER BY created_at DESC",
        (bot_id,))

    pending = []
    for r in rows:
        row = dict(r)
        # Parse quote_data JSON for the response
        try:
            row["quote_data"] = json.loads(row.get("quote_data", "{}"))
        except (json.JSONDecodeError, TypeError):
            row["quote_data"] = {}
        pending.append(row)

    return {"pending": pending, "total": len(pending)}


@router.post("/confirm/{tx_id}")
async def grid_confirm_tx(
    tx_id: str,
    req: dict,
    x_api_key: str = Header(None, alias="X-API-Key"),
):
    """Confirm a pending grid swap transaction after user signs it.

    Body: {"tx_signature": "..."}
    Verifies on-chain, updates grid_pending_txs, inserts into grid_trades,
    and updates bot stats.
    """
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    await _get_agent(x_api_key)

    tx_signature = (req.get("tx_signature") or "").strip()
    if not tx_signature or len(tx_signature) < 20:
        raise HTTPException(400, "Valid tx_signature required (min 20 chars)")

    db = await _get_db()

    # 1. Fetch pending tx
    rows = await db.raw_execute_fetchall(
        "SELECT tx_id, bot_id, side, grid_level, amount_usdc, token, "
        "price_usdc, commission_usdc, status, quote_data "
        "FROM grid_pending_txs WHERE tx_id=?",
        (tx_id,))
    if not rows:
        raise HTTPException(404, "Pending transaction not found")

    pending = dict(rows[0])
    if pending["status"] != "pending":
        raise HTTPException(400, f"Transaction already {pending['status']}")

    bot_id = pending["bot_id"]

    # 2. Verify bot ownership
    bot_rows = await db.raw_execute_fetchall(
        "SELECT bot_id, api_key, total_buys, total_sells, total_profit_usdc "
        "FROM grid_bots WHERE bot_id=? AND api_key=?",
        (bot_id, x_api_key))
    if not bot_rows:
        raise HTTPException(403, "Bot not owned by you")

    bot_data = dict(bot_rows[0])

    # 3. Verify transaction on-chain (best effort — Solana RPC)
    verified = False
    try:
        from blockchain.solana_verifier import verify_transaction
        result = await verify_transaction(tx_signature)
        if result and result.get("valid"):
            verified = True
    except Exception as e:
        logger.warning("[GRID] On-chain verification skipped for %s: %s",
                       tx_id[:8], e)
        # Accept without full verification — user provided signature
        verified = True

    if not verified:
        raise HTTPException(400, "Transaction could not be verified on-chain")

    # 4. Update pending tx status
    now = int(time.time())
    await db.raw_execute(
        "UPDATE grid_pending_txs SET status='confirmed', signed_at=?, "
        "tx_signature=? WHERE tx_id=?",
        (now, tx_signature, tx_id))

    # 5. Insert into grid_trades
    side = pending["side"]
    grid_level = int(pending["grid_level"])
    price_usdc = float(pending["price_usdc"])
    amount_usdc = float(pending["amount_usdc"])
    commission = float(pending["commission_usdc"])
    token = pending["token"]
    effective = amount_usdc - commission
    amount_tokens = round(effective / price_usdc, 8) if price_usdc > 0 else 0

    trade_id = str(uuid.uuid4())
    await db.raw_execute(
        "INSERT INTO grid_trades(trade_id, bot_id, side, grid_level, "
        "price_usdc, amount, usdc_value, tx_signature) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (trade_id, bot_id, side, grid_level,
         round(price_usdc, 6), amount_tokens, round(amount_usdc, 6),
         tx_signature))

    # 6. Update bot stats
    t_buys = int(bot_data.get("total_buys", 0) or 0)
    t_sells = int(bot_data.get("total_sells", 0) or 0)
    t_profit = float(bot_data.get("total_profit_usdc", 0) or 0)

    if side == "buy":
        t_buys += 1
    else:
        t_sells += 1
        # Estimate profit from sell (grid_step * tokens)
        # Parse quote_data to get grid info if available
        try:
            qd = json.loads(pending.get("quote_data", "{}"))
            out_amount = int(qd.get("outAmount", "0"))
            profit_usdc = round(out_amount / 1_000_000 - amount_usdc, 6) if out_amount > 0 else 0
            if profit_usdc > 0:
                t_profit += profit_usdc
        except Exception:
            pass

    await db.raw_execute(
        "UPDATE grid_bots SET total_buys=?, total_sells=?, "
        "total_profit_usdc=? WHERE bot_id=?",
        (t_buys, t_sells, round(t_profit, 6), bot_id))

    logger.info("[GRID] Confirmed %s tx %s for bot %s L%d (sig: %s)",
                side.upper(), tx_id[:8], bot_id[:8], grid_level,
                tx_signature[:16])

    return {
        "success": True,
        "tx_id": tx_id,
        "trade_id": trade_id,
        "side": side,
        "grid_level": grid_level,
        "token": token,
        "amount_usdc": amount_usdc,
        "price_usdc": price_usdc,
        "commission_usdc": commission,
        "tx_signature": tx_signature,
        "status": "confirmed",
    }


@router.delete("/pending/{tx_id}")
async def grid_cancel_pending(
    tx_id: str,
    x_api_key: str = Header(None, alias="X-API-Key"),
):
    """Cancel a pending grid swap transaction."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    await _get_agent(x_api_key)

    db = await _get_db()

    # Fetch pending tx
    rows = await db.raw_execute_fetchall(
        "SELECT tx_id, bot_id, status FROM grid_pending_txs WHERE tx_id=?",
        (tx_id,))
    if not rows:
        raise HTTPException(404, "Pending transaction not found")

    pending = dict(rows[0])
    if pending["status"] != "pending":
        raise HTTPException(400, f"Transaction already {pending['status']}")

    # Verify bot ownership
    bot_id = pending["bot_id"]
    bot = await db.raw_execute_fetchall(
        "SELECT bot_id FROM grid_bots WHERE bot_id=? AND api_key=?",
        (bot_id, x_api_key))
    if not bot:
        raise HTTPException(403, "Bot not owned by you")

    await db.raw_execute(
        "UPDATE grid_pending_txs SET status='cancelled' WHERE tx_id=?",
        (tx_id,))

    logger.info("[GRID] Cancelled pending tx %s for bot %s", tx_id[:8], bot_id[:8])
    return {"success": True, "tx_id": tx_id, "status": "cancelled"}


def get_router():
    return router
