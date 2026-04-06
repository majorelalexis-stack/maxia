"""MAXIA V12 — Grid Trading Bot: automated buy/sell at fixed price intervals"""
import logging, asyncio, uuid
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
            created_at INTEGER DEFAULT (strftime('%s','now')));
        CREATE INDEX IF NOT EXISTS idx_grid_trades_bot ON grid_trades(bot_id);
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


# ── Background Worker ──

async def grid_worker():
    """Background: check grid bots every 60 seconds for price crossings."""
    logger.info("[GRID] Worker started — checking every 60s")
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

                    # 4. Process crossings
                    t_buys = int(bot.get("total_buys", 0) or 0)
                    t_sells = int(bot.get("total_sells", 0) or 0)
                    t_profit = float(bot.get("total_profit_usdc", 0) or 0)

                    if current_level < prev_level:
                        # Price dropped — BUY at each crossed level
                        for lv in range(prev_level - 1, current_level - 1, -1):
                            if lv < 0 or lv >= num_grids:
                                continue
                            gp = lower + (lv + 0.5) * grid_step
                            comm = round(per_grid * GRID_COMMISSION_BPS / 10000, 6)
                            amt = round((per_grid - comm) / gp, 8) if gp > 0 else 0
                            await db.raw_execute(
                                "INSERT INTO grid_trades(trade_id,bot_id,side,grid_level,"
                                "price_usdc,amount,usdc_value) VALUES(?,?,?,?,?,?,?)",
                                (str(uuid.uuid4()), bot_id, "buy", lv,
                                 round(gp, 6), amt, round(per_grid, 6)))
                            t_buys += 1
                            logger.info("[GRID] BUY %s L%d: %.8f %s @ $%.4f",
                                        bot_id[:8], lv, amt, token, gp)
                    else:
                        # Price rose — SELL at each crossed level
                        for lv in range(prev_level + 1, current_level + 1):
                            if lv < 1 or lv > num_grids:
                                continue
                            gp = lower + (lv - 0.5) * grid_step
                            comm = round(per_grid * GRID_COMMISSION_BPS / 10000, 6)
                            amt = round((per_grid - comm) / gp, 8) if gp > 0 else 0
                            profit = round(grid_step * amt, 6)
                            await db.raw_execute(
                                "INSERT INTO grid_trades(trade_id,bot_id,side,grid_level,"
                                "price_usdc,amount,usdc_value) VALUES(?,?,?,?,?,?,?)",
                                (str(uuid.uuid4()), bot_id, "sell", lv,
                                 round(gp, 6), amt, round(per_grid, 6)))
                            t_sells += 1
                            t_profit += profit
                            logger.info("[GRID] SELL %s L%d: %.8f %s @ $%.4f (+$%.4f)",
                                        bot_id[:8], lv, amt, token, gp, profit)

                    # 5. Update bot stats
                    await db.raw_execute(
                        "UPDATE grid_bots SET total_buys=?, total_sells=?, "
                        "total_profit_usdc=? WHERE bot_id=?",
                        (t_buys, t_sells, round(t_profit, 6), bot_id))

                except Exception as e:
                    logger.error("[GRID] Worker error for bot %s: %s", bot_id[:8], e)

                # Small delay between bots to avoid flooding
                await asyncio.sleep(1)

        except Exception as e:
            logger.error("[GRID] Worker error: %s", e)


def get_router():
    return router
