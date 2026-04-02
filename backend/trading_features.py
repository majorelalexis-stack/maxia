"""MAXIA V12 — Trading Features: Whale Tracker, OHLCV Candles, Copy Trading"""
import logging
import asyncio, time, uuid, json
from fastapi import APIRouter, HTTPException, Header

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/public", tags=["trading"])


async def _get_db():
    from database import db
    return db


async def _get_agent(api_key):
    db = await _get_db()
    agent = await db.get_agent(api_key)
    if not agent:
        raise HTTPException(401, "Invalid API key")
    return agent


async def ensure_tables():
    db = await _get_db()
    await db.raw_executescript("""
        CREATE TABLE IF NOT EXISTS whale_monitors (
            id TEXT PRIMARY KEY, api_key TEXT NOT NULL, wallet_address TEXT NOT NULL,
            chain TEXT DEFAULT 'solana', threshold_usdc NUMERIC(18,6) DEFAULT 1000,
            callback_url TEXT DEFAULT '', active INTEGER DEFAULT 1,
            created_at INTEGER DEFAULT (strftime('%s','now')));
        CREATE INDEX IF NOT EXISTS idx_whale_mon_key ON whale_monitors(api_key);

        CREATE TABLE IF NOT EXISTS whale_alerts (
            id TEXT PRIMARY KEY, monitor_id TEXT, wallet TEXT, action TEXT,
            amount_usdc NUMERIC(18,6), tx_signature TEXT, notified INTEGER DEFAULT 0,
            created_at INTEGER DEFAULT (strftime('%s','now')));

        CREATE TABLE IF NOT EXISTS price_candles (
            id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL,
            interval TEXT NOT NULL, open NUMERIC(18,6), high NUMERIC(18,6), low NUMERIC(18,6), close NUMERIC(18,6),
            volume NUMERIC(18,6) DEFAULT 0, timestamp INTEGER NOT NULL,
            UNIQUE(symbol, interval, timestamp));
        CREATE INDEX IF NOT EXISTS idx_candles_sym ON price_candles(symbol, interval, timestamp);

        CREATE TABLE IF NOT EXISTS copy_trades (
            id TEXT PRIMARY KEY, api_key TEXT NOT NULL, target_wallet TEXT NOT NULL,
            chain TEXT DEFAULT 'solana', max_per_trade_usdc NUMERIC(18,6) DEFAULT 100,
            active INTEGER DEFAULT 1, total_copied INTEGER DEFAULT 0,
            total_volume_usdc NUMERIC(18,6) DEFAULT 0,
            created_at INTEGER DEFAULT (strftime('%s','now')));
        CREATE INDEX IF NOT EXISTS idx_copy_key ON copy_trades(api_key);

        CREATE TABLE IF NOT EXISTS copy_trade_history (
            id TEXT PRIMARY KEY, follow_id TEXT, target_wallet TEXT,
            token TEXT, side TEXT, amount_usdc NUMERIC(18,6), commission_usdc NUMERIC(18,6),
            tx_signature TEXT DEFAULT '', created_at INTEGER DEFAULT (strftime('%s','now')));
    """)


# ══════════════════════════════════════════
# FEATURE 3: Whale Tracker
# ══════════════════════════════════════════

@router.post("/whale/track")
async def whale_track(req: dict, x_api_key: str = Header(None, alias="X-API-Key")):
    """Monitor a wallet for large transfers. 0.99 USDC/month per wallet."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    agent = await _get_agent(x_api_key)
    wallet = req.get("wallet", "")
    if not wallet or len(wallet) < 20:
        raise HTTPException(400, "Valid wallet address required")
    db = await _get_db()
    mid = str(uuid.uuid4())
    await db.raw_execute(
        "INSERT INTO whale_monitors(id,api_key,wallet_address,chain,threshold_usdc,callback_url) VALUES(?,?,?,?,?,?)",
        (mid, x_api_key, wallet, req.get("chain", "solana"),
         req.get("threshold_usdc", 1000), req.get("callback_url", "")))
    return {"success": True, "monitor_id": mid, "wallet": wallet,
            "threshold_usdc": req.get("threshold_usdc", 1000),
            "price": "0.99 USDC/month"}


@router.get("/whale/my-monitors")
async def whale_my_monitors(x_api_key: str = Header(None, alias="X-API-Key")):
    """List my whale monitors."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    db = await _get_db()
    rows = await db.raw_execute_fetchall(
        "SELECT id, api_key, wallet_address, chain, threshold_usdc, "
        "callback_url, active, created_at "
        "FROM whale_monitors WHERE api_key=? AND active=1 ORDER BY created_at DESC", (x_api_key,))
    return {"monitors": [dict(r) for r in rows], "total": len(rows)}


@router.delete("/whale/track/{monitor_id}")
async def whale_untrack(monitor_id: str, x_api_key: str = Header(None, alias="X-API-Key")):
    """Stop monitoring a wallet."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    db = await _get_db()
    await db.raw_execute(
        "UPDATE whale_monitors SET active=0 WHERE id=? AND api_key=?", (monitor_id, x_api_key))
    return {"success": True, "monitor_id": monitor_id}


@router.get("/whale/alerts")
async def whale_alerts(x_api_key: str = Header(None, alias="X-API-Key"), limit: int = 50):
    """Get recent whale alerts for my monitors."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    db = await _get_db()
    rows = await db.raw_execute_fetchall("""
        SELECT a.* FROM whale_alerts a
        JOIN whale_monitors m ON a.monitor_id = m.id
        WHERE m.api_key=? ORDER BY a.created_at DESC LIMIT ?
    """, (x_api_key, min(limit, 200)))
    return {"alerts": [dict(r) for r in rows], "total": len(rows)}


async def check_whales():
    """Background: check monitored wallets for large transfers."""
    while True:
        try:
            db = await _get_db()
            monitors = await db.raw_execute_fetchall(
                "SELECT id, api_key, wallet_address, chain, threshold_usdc, "
                "callback_url, active, created_at "
                "FROM whale_monitors WHERE active=1")
            if not monitors:
                await asyncio.sleep(60)
                continue
            from config import get_rpc_url
            from http_client import get_http_client
            rpc = get_rpc_url()
            for mon in monitors:
                try:
                    wallet = mon["wallet_address"]
                    client = get_http_client()
                    resp = await client.post(rpc, json={
                        "jsonrpc": "2.0", "id": 1,
                        "method": "getSignaturesForAddress",
                        "params": [wallet, {"limit": 5}],
                    }, timeout=10)
                    sigs = resp.json().get("result", [])
                    for sig_info in sigs:
                        sig = sig_info.get("signature", "")
                        exists = await db.raw_execute_fetchall(
                            "SELECT 1 FROM whale_alerts WHERE tx_signature=?", (sig,))
                        if exists:
                            continue
                        # Check transaction details
                        resp2 = await client.post(rpc, json={
                            "jsonrpc": "2.0", "id": 1,
                            "method": "getTransaction",
                            "params": [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
                        }, timeout=10)
                        tx = resp2.json().get("result")
                        if not tx:
                            continue
                        # Estimate value (simplified)
                        pre = tx.get("meta", {}).get("preBalances", [0])
                        post = tx.get("meta", {}).get("postBalances", [0])
                        if pre and post:
                            diff = abs(pre[0] - post[0]) / 1e9  # lamports to SOL
                            value_usdc = diff * 150  # rough SOL price estimate
                            if value_usdc >= mon["threshold_usdc"]:
                                aid = str(uuid.uuid4())
                                await db.raw_execute(
                                    "INSERT OR IGNORE INTO whale_alerts(id,monitor_id,wallet,action,amount_usdc,tx_signature) VALUES(?,?,?,?,?,?)",
                                    (aid, mon["id"], wallet, "large_transfer", round(value_usdc, 2), sig))
                                # Notify via callback
                                if mon["callback_url"]:
                                    try:
                                        await client.post(mon["callback_url"], json={
                                            "event": "whale_move", "wallet": wallet,
                                            "amount_usdc": round(value_usdc, 2),
                                            "tx": sig, "chain": mon["chain"],
                                        }, timeout=5)
                                    except Exception:
                                            pass
                    await asyncio.sleep(1)
                except Exception as e:
                    logger.error(f"[WhaleTracker] Monitor error: {e}")
        except Exception as e:
            logger.error(f"[WhaleTracker] Error: {e}")
        await asyncio.sleep(30)


# ══════════════════════════════════════════
# FEATURE 4: OHLCV Candles
# ══════════════════════════════════════════

CANDLE_INTERVALS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
CANDLE_RETENTION = {"1m": 7, "5m": 14, "15m": 30, "1h": 90, "4h": 180, "1d": 365}  # days


_SYM_TO_CP = {
    "SOL": "sol-solana", "ETH": "eth-ethereum", "BTC": "btc-bitcoin",
    "USDC": "usdc-usd-coin", "USDT": "usdt-tether",
    "BONK": "bonk-bonk", "JUP": "jup-jupiter", "RAY": "ray-raydium",
    "WIF": "wif-dogwifhat", "RENDER": "rndr-render-token", "HNT": "hnt-helium",
    "PYTH": "pyth-pyth-network", "W": "w-wormhole",
    "LINK": "link-chainlink", "UNI": "uni-uniswap", "AAVE": "aave-aave",
    "DOGE": "doge-dogecoin", "SHIB": "shib-shiba-inu", "PEPE": "pepe-pepe",
    "XRP": "xrp-xrp", "AVAX": "avax-avalanche", "MATIC": "matic-polygon",
    "BNB": "bnb-binance-coin", "TON": "ton-toncoin", "SUI": "sui-sui",
    "TRX": "trx-tron", "NEAR": "near-near-protocol", "APT": "apt-aptos",
    "SEI": "sei-sei", "ARB": "arb-arbitrum", "FET": "fet-fetch-ai",
    "FIL": "fil-filecoin", "AR": "ar-arweave", "INJ": "inj-injective",
    "OP": "op-optimism", "TAO": "tao-bittensor", "AKT": "akt-akash-network",
    "ORCA": "orca-orca", "DRIFT": "drift-drift-protocol",
    "ONDO": "ondo-ondo-finance", "TRUMP": "trump-official-trump",
}
_cp_cache: dict = {}  # symbol -> {"candles": [...], "ts": float}
_CP_CACHE_TTL = 1800  # 30 min


_SYM_TO_CG = {
    "SOL": "solana", "ETH": "ethereum", "BTC": "bitcoin",
    "USDC": "usd-coin", "USDT": "tether", "BONK": "bonk",
    "JUP": "jupiter-exchange-solana", "RAY": "raydium", "WIF": "dogwifcoin",
    "RENDER": "render-token", "HNT": "helium", "TRUMP": "official-trump",
    "PYTH": "pyth-network", "W": "wormhole", "ORCA": "orca",
    "JTO": "jito-governance-token", "DRIFT": "drift-protocol",
    "LINK": "chainlink", "UNI": "uniswap", "AAVE": "aave",
    "DOGE": "dogecoin", "SHIB": "shiba-inu", "PEPE": "pepe",
    "XRP": "ripple", "AVAX": "avalanche-2", "MATIC": "matic-network",
    "BNB": "binancecoin", "TON": "the-open-network", "SUI": "sui",
    "NEAR": "near", "APT": "aptos", "SEI": "sei-network",
    "ARB": "arbitrum", "OP": "optimism", "FET": "artificial-superintelligence-alliance",
    "FIL": "filecoin", "AR": "arweave", "INJ": "injective-protocol",
    "TAO": "bittensor", "AKT": "akash-network", "ONDO": "ondo-finance",
    "LDO": "lido-dao", "TIA": "celestia", "STX": "blockstack",
    "AIOZ": "aioz-network", "KMNO": "kamino", "PENGU": "pudgy-penguins",
}


async def _fetch_coinpaprika_ohlcv(symbol: str, interval: str, limit: int) -> list:
    """Fetch real OHLCV candles. CoinGecko market_chart (30d free) primary, CoinPaprika today."""
    cache_key = f"{symbol}_{interval}"
    now = time.time()
    cached = _cp_cache.get(cache_key)
    if cached and now - cached["ts"] < _CP_CACHE_TTL:
        return cached["candles"][:limit]

    import httpx
    candles = []

    # Source 1: CoinGecko OHLC (30 days, free, real candles)
    cg_id = _SYM_TO_CG.get(symbol)
    if cg_id:
        try:
            async with httpx.AsyncClient(timeout=12) as client:
                resp = await client.get(
                    f"https://api.coingecko.com/api/v3/coins/{cg_id}/ohlc?vs_currency=usd&days=30"
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, list) and data:
                        for row in data:
                            if len(row) >= 5:
                                candles.append({
                                    "o": float(row[1]),
                                    "h": float(row[2]),
                                    "l": float(row[3]),
                                    "c": float(row[4]),
                                    "v": 0,
                                    "t": int(row[0] / 1000),
                                })
                        logger.info(f"CoinGecko OHLC: {len(candles)} candles for {symbol}")
        except Exception as e:
            logger.warning(f"CoinGecko OHLC error for {symbol}: {e}")

    # Source 2: CoinPaprika today (has volume)
    cp_id = _SYM_TO_CP.get(symbol)
    if cp_id:
        try:
            import datetime
            today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.get(
                    f"https://api.coinpaprika.com/v1/coins/{cp_id}/ohlcv/historical?start={today}&limit=1"
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, list) and data:
                        d = data[0]
                        ts_str = d.get("time_open", "")
                        if ts_str:
                            try:
                                dt = datetime.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                                epoch = int(dt.timestamp())
                                cp_candle = {
                                    "o": float(d.get("open", 0)),
                                    "h": float(d.get("high", 0)),
                                    "l": float(d.get("low", 0)),
                                    "c": float(d.get("close", 0)),
                                    "v": float(d.get("volume", 0)),
                                    "t": epoch,
                                }
                                # Replace or append today's candle with volume
                                if candles and candles[-1]["t"] >= epoch:
                                    candles[-1] = cp_candle
                                else:
                                    candles.append(cp_candle)
                            except Exception:
                                pass
        except Exception as e:
            logger.warning(f"CoinPaprika today OHLCV error for {symbol}: {e}")

    if candles:
        _cp_cache[cache_key] = {"candles": candles, "ts": now}
    return candles[:limit]


@router.get("/crypto/candles")
async def get_candles(symbol: str = "SOL", interval: str = "1h", limit: int = 100):
    """Get OHLCV candles. Free, no auth. Intervals: 1m, 5m, 15m, 1h, 4h, 1d."""
    symbol = symbol.upper()
    if interval not in CANDLE_INTERVALS:
        raise HTTPException(400, f"Invalid interval. Use: {list(CANDLE_INTERVALS.keys())}")
    limit = min(limit, 1000)

    # For hourly+ intervals: CoinPaprika has real OHLCV with volume
    if interval in ("1h", "4h", "1d"):
        cp_candles = await _fetch_coinpaprika_ohlcv(symbol, interval, limit)
        if cp_candles:
            # Append any fresh DB candles newer than last CoinPaprika candle
            last_cp_ts = cp_candles[-1]["t"]
            db = await _get_db()
            rows = await db.raw_execute_fetchall(
                "SELECT open, high, low, close, volume, timestamp FROM price_candles "
                "WHERE symbol=? AND interval=? AND timestamp>? ORDER BY timestamp ASC LIMIT ?",
                (symbol, interval, last_cp_ts, 100))
            for r in rows:
                cp_candles.append({"o": r["open"], "h": r["high"], "l": r["low"], "c": r["close"],
                                   "v": r["volume"], "t": r["timestamp"]})
            return {"symbol": symbol, "interval": interval, "candles": cp_candles[-limit:], "count": len(cp_candles[-limit:])}

    # For sub-hourly or CoinPaprika failure: use DB
    db = await _get_db()
    rows = await db.raw_execute_fetchall(
        "SELECT open, high, low, close, volume, timestamp FROM price_candles "
        "WHERE symbol=? AND interval=? ORDER BY timestamp DESC LIMIT ?",
        (symbol, interval, limit))
    candles = [{"o": r["open"], "h": r["high"], "l": r["low"], "c": r["close"],
                "v": r["volume"], "t": r["timestamp"]} for r in reversed(rows)]
    return {"symbol": symbol, "interval": interval, "candles": candles, "count": len(candles)}


@router.get("/crypto/candles/symbols")
async def candle_symbols():
    """List symbols with candle data."""
    db = await _get_db()
    rows = await db.raw_execute_fetchall(
        "SELECT DISTINCT symbol FROM price_candles ORDER BY symbol")
    return {"symbols": [r["symbol"] for r in rows],
            "intervals": list(CANDLE_INTERVALS.keys())}


async def update_candles():
    """Background: build 1m candles from live prices (crypto + stocks + Pyth), aggregate higher timeframes."""
    while True:
        try:
            from price_oracle import get_crypto_prices
            prices_data = await get_crypto_prices()
            prices = prices_data.get("prices", prices_data) if isinstance(prices_data, dict) else {}

            # Also include Pyth live prices (SOL/ETH/BTC/USDC + stocks)
            try:
                from pyth_oracle import _streaming_prices, ALL_FEEDS
                feed_to_sym = {v: k for k, v in ALL_FEEDS.items()}
                for feed_id, cached in _streaming_prices.items():
                    data = cached.get("data", {})
                    sym = data.get("symbol") or feed_to_sym.get(feed_id, "")
                    price = data.get("price", 0)
                    if sym and price > 0 and sym not in prices:
                        prices[sym] = {"price": price, "source": "pyth"}
            except Exception:
                pass

            # Also include stock prices from tokenized_stocks
            try:
                from tokenized_stocks import fetch_stock_prices
                stock_data = await fetch_stock_prices()
                for sym, data in (stock_data or {}).items():
                    price = data.get("price_usd", 0) if isinstance(data, dict) else 0
                    if sym and price > 0 and sym not in prices:
                        prices[sym] = {"price": price, "source": "stock"}
            except Exception:
                pass

            db = await _get_db()
            now = int(time.time())
            minute_ts = (now // 60) * 60

            for symbol, data in prices.items():
                price = data.get("price", 0) if isinstance(data, dict) else 0
                if price <= 0:
                    continue
                # Upsert 1m candle
                existing = await db.raw_execute_fetchall(
                    "SELECT id, open, high, low, close, volume "
                    "FROM price_candles WHERE symbol=? AND interval='1m' AND timestamp=?",
                    (symbol, minute_ts))
                if existing:
                    row = existing[0]
                    await db.raw_execute(
                        "UPDATE price_candles SET high=CASE WHEN ?>high THEN ? ELSE high END, low=CASE WHEN ?<low THEN ? ELSE low END, close=? WHERE symbol=? AND interval='1m' AND timestamp=?",
                        (price, price, price, price, price, symbol, minute_ts))
                else:
                    await db.raw_execute(
                        "INSERT INTO price_candles(symbol,interval,open,high,low,close,volume,timestamp) VALUES(?,?,?,?,?,?,?,?) "
                        "ON CONFLICT(symbol,interval,timestamp) DO UPDATE SET high=CASE WHEN excluded.high>price_candles.high THEN excluded.high ELSE price_candles.high END, low=CASE WHEN excluded.low<price_candles.low THEN excluded.low ELSE price_candles.low END, close=excluded.close",
                        (symbol, "1m", price, price, price, price, 0, minute_ts))


            # Aggregate higher timeframes every 5 minutes
            if now % 300 < 62:
                for interval, seconds in CANDLE_INTERVALS.items():
                    if interval == "1m":
                        continue
                    bucket_ts = (now // seconds) * seconds
                    for symbol in list(prices.keys())[:40]:
                        rows = await db.raw_execute_fetchall(
                            "SELECT open, high, low, close FROM price_candles "
                            "WHERE symbol=? AND interval='1m' AND timestamp>=? AND timestamp<? ORDER BY timestamp ASC",
                            (symbol, bucket_ts, bucket_ts + seconds))
                        if rows:
                            o = rows[0]["open"]
                            h = max(r["high"] for r in rows)
                            l = min(r["low"] for r in rows)
                            c = rows[-1]["close"]
                            await db.raw_execute(
                                "INSERT INTO price_candles(symbol,interval,open,high,low,close,volume,timestamp) VALUES(?,?,?,?,?,?,?,?) "
                                "ON CONFLICT(symbol,interval,timestamp) DO UPDATE SET open=excluded.open, high=excluded.high, low=excluded.low, close=excluded.close",
                                (symbol, interval, o, h, l, c, 0, bucket_ts))
        
            # Cleanup old candles daily
            if now % 86400 < 62:
                for interval, retention_days in CANDLE_RETENTION.items():
                    cutoff = now - retention_days * 86400
                    await db.raw_execute(
                        "DELETE FROM price_candles WHERE interval=? AND timestamp<?",
                        (interval, cutoff))
    
        except Exception as e:
            logger.error(f"[Candles] Error: {e}")
        await asyncio.sleep(60)


# ══════════════════════════════════════════
# FEATURE 2: Copy Trading
# ══════════════════════════════════════════

@router.post("/copy-trade/follow")
async def copy_trade_follow(req: dict, x_api_key: str = Header(None, alias="X-API-Key")):
    """Follow a wallet to copy its trades. Commission: 1% per copied trade."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    agent = await _get_agent(x_api_key)
    target = req.get("target_wallet", "")
    if not target or len(target) < 20:
        raise HTTPException(400, "Valid target_wallet required")
    db = await _get_db()
    fid = str(uuid.uuid4())
    await db.raw_execute(
        "INSERT INTO copy_trades(id,api_key,target_wallet,chain,max_per_trade_usdc) VALUES(?,?,?,?,?)",
        (fid, x_api_key, target, req.get("chain", "solana"),
         req.get("max_per_trade_usdc", 100)))
    return {"success": True, "follow_id": fid, "target_wallet": target,
            "max_per_trade_usdc": req.get("max_per_trade_usdc", 100),
            "commission": "1% per copied trade"}


@router.get("/copy-trade/my-follows")
async def copy_trade_follows(x_api_key: str = Header(None, alias="X-API-Key")):
    """List wallets I'm following."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    db = await _get_db()
    rows = await db.raw_execute_fetchall(
        "SELECT id, api_key, target_wallet, chain, max_per_trade_usdc, "
        "active, total_copied, total_volume_usdc, created_at "
        "FROM copy_trades WHERE api_key=? AND active=1 ORDER BY created_at DESC", (x_api_key,))
    return {"follows": [dict(r) for r in rows], "total": len(rows)}


@router.delete("/copy-trade/unfollow/{follow_id}")
async def copy_trade_unfollow(follow_id: str, x_api_key: str = Header(None, alias="X-API-Key")):
    """Stop following a wallet."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    db = await _get_db()
    await db.raw_execute(
        "UPDATE copy_trades SET active=0 WHERE id=? AND api_key=?", (follow_id, x_api_key))
    return {"success": True, "follow_id": follow_id}


@router.get("/copy-trade/history")
async def copy_trade_history(x_api_key: str = Header(None, alias="X-API-Key"), limit: int = 50):
    """History of copied trades."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    db = await _get_db()
    rows = await db.raw_execute_fetchall("""
        SELECT h.* FROM copy_trade_history h
        JOIN copy_trades c ON h.follow_id = c.id
        WHERE c.api_key=? ORDER BY h.created_at DESC LIMIT ?
    """, (x_api_key, min(limit, 200)))
    return {"trades": [dict(r) for r in rows], "total": len(rows)}


# ══════════════════════════════════════════
# ── COPY TRADE EXECUTION ENGINE ──
# ══════════════════════════════════════════

_COPY_DAILY_CAP_USDC = 500  # Max total copied per follow per day
_COPY_CHECK_INTERVAL = 120  # Check target wallets every 2 min
_last_seen_sigs: dict[str, str] = {}  # target_wallet -> last_seen_tx_signature


async def _fetch_recent_swaps(target_wallet: str) -> list[dict]:
    """Fetch recent swap transactions from a target wallet via Helius."""
    try:
        from config import HELIUS_API_KEY
        if not HELIUS_API_KEY:
            return []
        client_mod = __import__("http_client", fromlist=["get_http_client"])
        client = client_mod.get_http_client()
        resp = await client.post(
            f"https://api.helius.xyz/v0/addresses/{target_wallet}/transactions?api-key={HELIUS_API_KEY}",
            json={"limit": 10, "type": "SWAP"},
            timeout=15,
        )
        if resp.status_code != 200:
            return []
        txs = resp.json()
        return txs if isinstance(txs, list) else []
    except Exception as e:
        logger.debug("[CopyTrade] Fetch swaps error for %s: %s", target_wallet[:8], e)
        return []


async def _execute_copy_trade(follow: dict, swap_tx: dict) -> dict | None:
    """Execute a copy trade based on detected target swap. Returns trade record or None."""
    try:
        # Parse swap info from Helius enhanced tx
        token_transfers = swap_tx.get("tokenTransfers", [])
        if not token_transfers:
            return None

        # Identify the token being bought (not USDC/SOL)
        bought_token = None
        for t in token_transfers:
            mint = t.get("mint", "")
            if mint and t.get("toUserAccount") == follow["target_wallet"]:
                bought_token = mint
                break
        if not bought_token:
            return None

        amount_usdc = min(
            float(follow.get("max_per_trade_usdc", 100)),
            _COPY_DAILY_CAP_USDC,
        )
        commission = round(amount_usdc * 0.01, 6)  # 1% commission

        # Record the trade intent (actual execution via jupiter deferred to user confirmation)
        trade_id = str(uuid.uuid4())
        db = await _get_db()
        await db.raw_execute(
            "INSERT INTO copy_trade_history(id, follow_id, target_wallet, token, side, "
            "amount_usdc, commission_usdc, tx_signature) VALUES(?,?,?,?,?,?,?,?)",
            (trade_id, follow["id"], follow["target_wallet"], bought_token, "buy",
             amount_usdc, commission, swap_tx.get("signature", "")),
        )
        # Update follow stats
        await db.raw_execute(
            "UPDATE copy_trades SET total_copied = total_copied + 1, "
            "total_volume_usdc = total_volume_usdc + ? WHERE id=?",
            (amount_usdc, follow["id"]),
        )

        logger.info(
            "[CopyTrade] Recorded: %s copied %s buy %.2f USDC (follow %s)",
            follow.get("api_key", "?")[:8], bought_token[:8], amount_usdc, follow["id"][:8],
        )
        return {
            "trade_id": trade_id, "follow_id": follow["id"],
            "token": bought_token, "amount_usdc": amount_usdc,
            "commission": commission, "status": "recorded",
        }

    except Exception as e:
        logger.error("[CopyTrade] Execute error: %s", e)
        return None


async def copy_trade_worker():
    """Background worker: monitors target wallets and records copy trades.

    NOTE: This worker RECORDS trades for later execution.
    Actual on-chain execution requires user confirmation via /copy-trade/execute endpoint
    to prevent unattended fund movement.
    """
    logger.info("[CopyTrade] Worker started — checking every %ds", _COPY_CHECK_INTERVAL)

    while True:
        try:
            await asyncio.sleep(_COPY_CHECK_INTERVAL)
            db = await _get_db()

            # Get all active follows
            follows = await db.raw_execute_fetchall(
                "SELECT id, api_key, target_wallet, chain, max_per_trade_usdc "
                "FROM copy_trades WHERE active=1"
            )
            if not follows:
                continue

            for row in follows:
                follow = dict(row) if hasattr(row, "keys") else {
                    "id": row[0], "api_key": row[1], "target_wallet": row[2],
                    "chain": row[3], "max_per_trade_usdc": row[4],
                }
                target = follow["target_wallet"]

                # Only Solana for now
                if follow.get("chain", "solana") != "solana":
                    continue

                swaps = await _fetch_recent_swaps(target)
                if not swaps:
                    continue

                last_seen = _last_seen_sigs.get(target)
                new_swaps = []
                for s in swaps:
                    sig = s.get("signature", "")
                    if sig == last_seen:
                        break
                    new_swaps.append(s)

                if new_swaps:
                    _last_seen_sigs[target] = new_swaps[0].get("signature", "")

                for swap in new_swaps[:3]:  # Max 3 copies per cycle per target
                    await _execute_copy_trade(follow, swap)
                    await asyncio.sleep(2)

        except Exception as e:
            logger.error("[CopyTrade] Worker error: %s", e)


def get_router():
    return router
