"""MAXIA Price Oracle V12 — Prix live via Helius DAS API

Utilise l API Helius DAS (getAsset) sur le meme endpoint RPC
pour recuperer les prix des tokens.

Strategie:
1. Helius DAS getAsset (parallel batches) -> prix live
2. CoinGecko pour les tokens manquants
3. Fallback mars 2026 si echec

Optimisations V12:
- Parallel fetch (50 tokens en ~1s au lieu de 7.5s)
- Circuit breaker (coupe apres 3 echecs, retry apres 60s)
- Connection pool HTTP partage
"""
import asyncio, time
import httpx
from config import get_rpc_url, HELIUS_API_KEY


# ── Circuit Breaker ──

class CircuitBreaker:
    """Coupe les appels apres N echecs consecutifs. Retry apres cooldown."""

    def __init__(self, name: str, max_failures: int = 3, cooldown_s: int = 60):
        self.name = name
        self.max_failures = max_failures
        self.cooldown_s = cooldown_s
        self._failures = 0
        self._open_until = 0  # timestamp

    @property
    def is_open(self) -> bool:
        if self._failures < self.max_failures:
            return False
        if time.time() > self._open_until:
            # Half-open: allow one retry
            self._failures = self.max_failures - 1
            return False
        return True

    def record_success(self):
        self._failures = 0

    def record_failure(self):
        self._failures += 1
        if self._failures >= self.max_failures:
            self._open_until = time.time() + self.cooldown_s
            print(f"[CircuitBreaker] {self.name} OPEN — {self._failures} failures, retry in {self.cooldown_s}s")

    def get_status(self) -> dict:
        return {
            "name": self.name,
            "state": "open" if self.is_open else "closed",
            "failures": self._failures,
            "max": self.max_failures,
        }


_cb_helius = CircuitBreaker("helius", max_failures=3, cooldown_s=60)
_cb_coingecko = CircuitBreaker("coingecko", max_failures=3, cooldown_s=120)
_cb_yahoo = CircuitBreaker("yahoo", max_failures=3, cooldown_s=120)

# ── Shared HTTP client pool ──
_http_pool: httpx.AsyncClient = None


async def _get_http() -> httpx.AsyncClient:
    """Retourne un client HTTP partage (connection pooling)."""
    global _http_pool
    if _http_pool is None or getattr(_http_pool, 'is_closed', True):
        _http_pool = httpx.AsyncClient(
            timeout=10,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _http_pool


async def close_http_pool():
    """Close the shared HTTP client pool (call at shutdown)."""
    global _http_pool
    if _http_pool is not None and not getattr(_http_pool, 'is_closed', True):
        await _http_pool.aclose()
        _http_pool = None

# Token mints pour getAsset
TOKEN_MINTS = {
    # Crypto
    "SOL": "So11111111111111111111111111111111111111112",
    "USDC": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "USDT": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
    "BONK": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
    "JUP": "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
    "RAY": "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R",
    "WIF": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
    "RENDER": "rndrizKT3MK1iimdxRdWabcF7Zg7AR5T4nud4EkHBof",
    "HNT": "hntyVP6YFm1Hg25TN9WGLqM12b8TQmcknKrdu1oxWux",
    "TRUMP": "6p6xgHyF7AeE6TZkSmFsko444wqoP15icUSqi2jfGiPN",
    "PYTH": "HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3",
    "W": "85VBFQZC9TZkfaptBWjvUw7YbZjy52A6mjtPGjstQAmQ",
    "ETH": "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs",
    "BTC": "3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh",
    "ORCA": "orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMU1kektZE",
    # V12: Tokens additionnels
    "JTO": "jtojtomepa8beP8AuQc6eXt5FriJwfFMwQx2v2f9mCL",
    "TNSR": "TNSRxcUxoT9xBG3de7PiJyTDYu7kskLqcpddxnEJAS6",
    "MEW": "MEW1gQWJ3nEXg2qgERiKu7FAFj79PHvQVREQUzScPP5",
    "POPCAT": "7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr",
    "MOBILE": "mb1eu7TzEc71KxDpsmsKoucSSuuoGLv1drys1oP2jh6",
    "MNDE": "MNDEFzGvMt87ueuHvVU9VcTqsAP5b3fTGPsHuuPA5ey",
    "MSOL": "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So",
    "JITOSOL": "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn",
    "BSOL": "bSo13r4TkiE4KumL71LsHTPpL2euBYLFx6h9HP3piy1",
    "DRIFT": "DriFtupJYLTosbwoN8koMbEYSx54aFAVLddWsbksjwg7",
    "KMNO": "KMNo3nJsBXfcpJTVhZcXLW7RmTwTt4GVFE7suUBo9sS",
    "PENGU": "2zMMhcVQEXDtdE6vsFS7S7D5oUodfJHE8vd1gnBouauv",
    "AI16Z": "HeLp6NuQkmYB4pYWo2zYs22mESHXPQYzXbB8n4V98jwC",
    "FARTCOIN": "9BB6NFEcjBCtnNLFko2FqVQBq8HHM13kCyYcdQbgpump",
    "GRASS": "Grass7B4RdKfBCjTKgSqnXkqjwiGvQyFbuSCUJr3XXjs",
    "ZEUS": "ZEUS1aR7aX8DFFJf5QjWj2ftDDdNTroMNGo8YoQm3Gq",
    "NOSOL": "nosXBVoaCTtYdLvKY6Csb4AC8JCdQKKAaWYtx2ZMoo7",
    "SAMO": "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU",
    "STEP": "StepAscQoEioFxxWGnh2sLBDFp9d8rvKz2Yp39iDpyT",
    "BOME": "ukHH6c7mMyiWCf1b9pnWe25TSpkDDt3H5pQZgZ74J82",
    "SLERF": "7BgBvyjrZX1YKz4oh9mjb8ZScatkkwb8DzFx7LoiVkM3",
    "MPLX": "METAewgxyPbgwsseH8T16a39CQ5VyVxZi9zXiDPY18m",
    "INF": "5oVNBeEEQvYi1cX3ir8Dx5n1P7pdxydbGF2X4TxVusJm",
    "PNUT": "2qEHjDLDLbuBgRYvsxhc5D6uDWAivNFZGan56P1tpump",
    "GOAT": "CzLSujWBLFsSjncfkh59rUFqvafWcY5tzedWJSuypump",
    # xStocks (actions tokenisees — vrais mints Backed Finance)
    "AAPL": "XsbEhLAtcf6HdfpFZ5xEMdqW8nfAvcsP5bdudRLJzJp",
    "TSLA": "XsDoVfqeBukxuZHWhdvWHBhgEHjGNst4MLodqsJHzoB",
    "NVDA": "Xsc9qvGR1efVDFGLrVsmkzv3qi45LTBjeUKSPmx9qEh",
    "GOOGL": "XsCPL9dNWBMvFtTmwcCA5v3xWPSMEBCszbQdiLLq6aN",
    "MSFT": "XsMTBZsqrDgTRWKzKMGSDE8GQjPX4mNQHN3fLFMKfBJ",
    "AMZN": "Xs3eBt7uRfJX8QUs4suhyU8p2M6DoUDrJyWBa8LLZsg",
    "META": "XsoeC2iBhNSXVgVB9GNofBSVw3VF9LDLBqSMhRdZi43",
    "MSTR": "XsP7xzNPvEHS1m6qfanPUGjNmdnmsLKEoNAnHjdxxyZ",
    "SPY": "XsoCS1TfEyfFhfvj8EtZ528L3CaKBDBRqRapnBbDF2W",
    "QQQ": "Xs8S1uUs1zvS2p7iwtsG3b6fkhpvmwz4GYU3gWAmWHZ",
}

FALLBACK_PRICES = {
    "SOL": 139, "USDC": 1.0, "USDT": 1.0, "BONK": 0.000025,
    "JUP": 0.72, "RAY": 2.5, "WIF": 1.18, "RENDER": 7.5,
    "HNT": 3.92, "TRUMP": 2.87, "PYTH": 0.058, "W": 0.10, "ETH": 3119, "BTC": 90613, "ORCA": 1.50,
    "JTO": 2.5, "TNSR": 0.6, "MEW": 0.003, "POPCAT": 0.4, "MOBILE": 0.001,
    "MNDE": 0.08, "MSOL": 150, "JITOSOL": 150, "BSOL": 140, "DRIFT": 1.2,
    "KMNO": 0.08, "PENGU": 0.01, "AI16Z": 0.5, "FARTCOIN": 0.8, "GRASS": 1.5,
    "ZEUS": 0.3, "NOSOL": 1.0, "SAMO": 0.008, "STEP": 0.04, "BOME": 0.003,
    "SLERF": 0.1, "MPLX": 0.03, "INF": 150, "PNUT": 0.2, "GOAT": 0.1,
    "AAPL": 257, "TSLA": 397, "NVDA": 178, "GOOGL": 299,
    "MSFT": 403, "AMZN": 213, "META": 614, "MSTR": 340,
    "SPY": 672, "QQQ": 515,
    "NFLX": 99, "AMD": 192, "PLTR": 157, "COIN": 200,
    "CRM": 280, "INTC": 43, "UBER": 75, "MARA": 20,
    "AVGO": 330, "DIA": 495, "IWM": 262, "GLD": 450,
    "ARKK": 55, "RIOT": 12, "SHOP": 100, "SQ": 80,
    "PYPL": 70, "ORCL": 170,
}

_price_cache: dict = {}
_cache_ts: float = 0
_CACHE_TTL = 60  # 1 minute (etait 30s — reduire les appels API)

# Stock prices cache (separate, longer TTL)
_stock_cache: dict = {}
_stock_cache_ts: float = 0
_STOCK_CACHE_TTL = 180  # 3 minutes (etait 2 — Yahoo rate limit)

# Per-symbol cache pour eviter les refetch inutiles
_symbol_cache: dict = {}  # {symbol: {"price": ..., "ts": ..., "source": ...}}
_SYMBOL_CACHE_TTL = 45  # secondes — cache individuel par symbole

# Stats compteur (pour monitoring)
_cache_stats = {"hits": 0, "misses": 0}

print("[PriceOracle] Initialise — Helius DAS API + Yahoo Finance + CoinGecko + fallback (cache 60s)")


async def _fetch_yahoo_stock_prices() -> dict:
    """Fetch real-time stock prices from Yahoo Finance (free, no API key)."""
    if _cb_yahoo.is_open:
        return {}
    stocks = ["AAPL", "TSLA", "NVDA", "GOOGL", "MSFT", "AMZN", "META", "MSTR", "SPY", "QQQ"]
    prices = {}
    try:
        client = await _get_http()
        # Yahoo Finance v8 API (free, no key needed)
        symbols = ",".join(stocks)
        url = f"https://query1.finance.yahoo.com/v8/finance/spark?symbols={symbols}&range=1d&interval=1d"
        resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code == 200:
            data = resp.json()
            for sym, info in data.items():
                try:
                    close = info.get("close", [])
                    prev = info.get("previousClose", 0)
                    price = close[-1] if close else info.get("regularMarketPrice", 0)
                    if price and price > 0:
                        change_pct = ((price - prev) / prev * 100) if prev else 0
                        prices[sym] = {"price": round(price, 2), "change": round(change_pct, 2), "source": "yahoo"}
                except Exception:
                    pass
    except Exception as e:
        print(f"[PriceOracle] Yahoo Finance error: {e}")

    # Fallback: try v7 quote API if v8 fails
    if not prices:
        try:
            client = await _get_http()
            symbols = ",".join(stocks)
            url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={symbols}"
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code == 200:
                data = resp.json()
                for q in data.get("quoteResponse", {}).get("result", []):
                    sym = q.get("symbol", "")
                    price = q.get("regularMarketPrice", 0)
                    change = q.get("regularMarketChangePercent", 0)
                    if sym and price:
                        prices[sym] = {"price": round(price, 2), "change": round(change, 2), "source": "yahoo_v7"}
        except Exception as e2:
            print(f"[PriceOracle] Yahoo v7 error: {e2}")

    if prices:
        _cb_yahoo.record_success()
        print(f"[PriceOracle] Yahoo Finance: {len(prices)} stock prices live")
    else:
        _cb_yahoo.record_failure()
    return prices


async def _fetch_one_helius(client: httpx.AsyncClient, rpc: str, sym: str, mint: str) -> tuple:
    """Fetch un seul token via Helius. Retourne (sym, price_dict) ou (sym, None)."""
    try:
        payload = {"jsonrpc": "2.0", "id": 1, "method": "getAsset", "params": {"id": mint}}
        resp = await client.post(rpc, json=payload)
        data = resp.json()
        result = data.get("result", {})
        if result:
            token_info = result.get("token_info", {})
            price_info = token_info.get("price_info", {})
            price = price_info.get("price_per_token", 0)
            if price and price > 0:
                return (sym, {"price": round(float(price), 6), "source": "helius_das"})
    except Exception:
        pass
    return (sym, None)


async def _fetch_helius_prices() -> dict:
    """Recupere les prix via Helius DAS API — parallel batches de 10."""
    if _cb_helius.is_open:
        print("[PriceOracle] Helius circuit breaker OPEN — skipping")
        return {}

    rpc = get_rpc_url()
    if not rpc:
        return {}

    prices = {}
    client = await _get_http()
    items = list(TOKEN_MINTS.items())

    # Fetch en batches paralleles de 10
    BATCH_SIZE = 10
    for i in range(0, len(items), BATCH_SIZE):
        batch = items[i:i + BATCH_SIZE]
        tasks = [_fetch_one_helius(client, rpc, sym, mint) for sym, mint in batch]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, tuple) and result[1] is not None:
                prices[result[0]] = result[1]
        # Petit delai entre batches pour pas surcharger
        if i + BATCH_SIZE < len(items):
            await asyncio.sleep(0.1)

    if prices:
        _cb_helius.record_success()
    else:
        _cb_helius.record_failure()

    return prices


async def get_prices(symbols: list = None) -> dict:
    """Recupere les prix — Helius DAS + fallback."""
    global _price_cache, _cache_ts

    if time.time() - _cache_ts < _CACHE_TTL and _price_cache:
        if symbols:
            return {s: _price_cache.get(s, {"price": FALLBACK_PRICES.get(s, 0), "source": "fallback"}) for s in symbols}
        return _price_cache

    prices = {}

    # Source 1: Helius DAS API
    helius_prices = await _fetch_helius_prices()
    prices.update(helius_prices)

    # Source 2: CoinGecko pour les tokens manquants
    stock_syms = {"AAPL","TSLA","NVDA","GOOGL","MSFT","AMZN","META","MSTR","SPY","QQQ",
                  "NFLX","AMD","PLTR","COIN","CRM","INTC","UBER","MARA","AVGO","DIA",
                  "IWM","GLD","ARKK","RIOT","SHOP","SQ","PYPL","ORCL"}
    missing_crypto = [s for s in TOKEN_MINTS if s not in prices and s not in stock_syms]
    if missing_crypto:
        # Map symbols to CoinGecko IDs
        SYM_TO_COINGECKO = {
            "SOL": "solana", "USDC": "usd-coin", "USDT": "tether", "BONK": "bonk",
            "JUP": "jupiter-exchange-solana", "RAY": "raydium", "WIF": "dogwifcoin",
            "RENDER": "render-token", "HNT": "helium", "TRUMP": "official-trump",
            "PYTH": "pyth-network", "W": "wormhole", "ETH": "ethereum", "BTC": "bitcoin",
            "ORCA": "orca", "JTO": "jito-governance-token", "TNSR": "tensor",
            "MEW": "cat-in-a-dogs-world", "POPCAT": "popcat", "MOBILE": "helium-mobile",
            "MNDE": "marinade", "MSOL": "msol", "JITOSOL": "jito-staked-sol",
            "BSOL": "blazestake-staked-sol", "DRIFT": "drift-protocol",
            "KMNO": "kamino", "PENGU": "pudgy-penguins", "AI16Z": "ai16z",
            "FARTCOIN": "fartcoin", "GRASS": "grass", "ZEUS": "zeus-network",
            "NOSOL": "nosana", "SAMO": "samoyedcoin", "STEP": "step-finance",
            "BOME": "book-of-meme", "SLERF": "slerf", "MPLX": "metaplex",
            "INF": "infinity-by-sanctum", "PNUT": "peanut-the-squirrel",
            "GOAT": "goatseus-maximus",
        }
        cg_ids = [SYM_TO_COINGECKO[s] for s in missing_crypto if s in SYM_TO_COINGECKO]
        if cg_ids and not _cb_coingecko.is_open:
            try:
                ids_str = ",".join(cg_ids)
                client = await _get_http()
                resp = await client.get(
                    f"https://api.coingecko.com/api/v3/simple/price?ids={ids_str}&vs_currencies=usd"
                )
                if resp.status_code == 200:
                    cg_data = resp.json()
                    cg_id_to_sym = {v: k for k, v in SYM_TO_COINGECKO.items()}
                    for cg_id, price_data in cg_data.items():
                        sym = cg_id_to_sym.get(cg_id, "")
                        if sym and price_data.get("usd"):
                            prices[sym] = {
                                "price": round(float(price_data["usd"]), 6),
                                "source": "coingecko",
                                "mint": TOKEN_MINTS.get(sym, ""),
                            }
                    cg_count = sum(1 for s in missing_crypto if s in prices)
                    if cg_count:
                        _cb_coingecko.record_success()
                        print(f"[PriceOracle] CoinGecko: {cg_count} additional prices fetched")
                else:
                    _cb_coingecko.record_failure()
            except Exception as e:
                _cb_coingecko.record_failure()
                print(f"[PriceOracle] CoinGecko error: {e}")

    # Source 3: Fallback pour tout ce qui manque encore
    for sym, fb_price in FALLBACK_PRICES.items():
        if sym not in prices:
            prices[sym] = {"price": fb_price, "source": "fallback"}

    _price_cache = prices
    _cache_ts = time.time()

    live = sum(1 for p in prices.values() if p.get("source") == "helius_das")
    fb = sum(1 for p in prices.values() if p.get("source") == "fallback")
    print(f"[PriceOracle] {live} live (Helius DAS), {fb} fallback (total {len(prices)})")

    if symbols:
        return {s: prices.get(s, {"price": FALLBACK_PRICES.get(s, 0), "source": "fallback"}) for s in symbols}
    return prices


async def get_price(symbol: str) -> float:
    """Retourne le prix d'un symbole — utilise le cache par symbole d'abord."""
    now = time.time()
    cached = _symbol_cache.get(symbol)
    if cached and now - cached.get("ts", 0) < _SYMBOL_CACHE_TTL:
        _cache_stats["hits"] += 1
        return cached.get("price", FALLBACK_PRICES.get(symbol, 0))
    _cache_stats["misses"] += 1
    prices = await get_prices([symbol])
    result = prices.get(symbol, {})
    price = result.get("price", FALLBACK_PRICES.get(symbol, 0))
    _symbol_cache[symbol] = {"price": price, "ts": now, "source": result.get("source", "unknown")}
    return price


def get_cache_stats() -> dict:
    """Retourne les stats du cache prix + circuit breakers."""
    return {
        "global_cache_age_s": round(time.time() - _cache_ts, 1) if _cache_ts else None,
        "global_cache_size": len(_price_cache),
        "symbol_cache_size": len(_symbol_cache),
        "stock_cache_age_s": round(time.time() - _stock_cache_ts, 1) if _stock_cache_ts else None,
        "hits": _cache_stats["hits"],
        "misses": _cache_stats["misses"],
        "hit_rate": f"{_cache_stats['hits'] / max(1, _cache_stats['hits'] + _cache_stats['misses']):.0%}",
        "circuit_breakers": {
            "helius": _cb_helius.get_status(),
            "coingecko": _cb_coingecko.get_status(),
            "yahoo": _cb_yahoo.get_status(),
        },
    }


async def get_crypto_prices() -> dict:
    stock_syms = {"AAPL","TSLA","NVDA","GOOGL","MSFT","AMZN","META","MSTR","SPY","QQQ",
                  "NFLX","AMD","PLTR","COIN","CRM","INTC","UBER","MARA","AVGO","DIA",
                  "IWM","GLD","ARKK","RIOT","SHOP","SQ","PYPL","ORCL"}
    cryptos = [s for s in TOKEN_MINTS if s not in stock_syms]
    return await get_prices(cryptos)


async def get_stock_prices() -> dict:
    global _stock_cache, _stock_cache_ts
    stocks = ["AAPL", "TSLA", "NVDA", "GOOGL", "MSFT", "AMZN", "META", "MSTR", "SPY", "QQQ",
              "NFLX", "AMD", "PLTR", "COIN", "CRM", "INTC", "UBER", "MARA",
              "AVGO", "DIA", "IWM", "GLD", "ARKK", "RIOT", "SHOP", "SQ", "PYPL", "ORCL"]

    # Use cache if fresh
    if time.time() - _stock_cache_ts < _STOCK_CACHE_TTL and _stock_cache:
        return _stock_cache

    # Try Yahoo Finance first (real-time, free)
    yahoo_prices = await _fetch_yahoo_stock_prices()
    if yahoo_prices and len(yahoo_prices) >= 5:
        result = {}
        for sym in stocks:
            if sym in yahoo_prices:
                result[sym] = yahoo_prices[sym]
            else:
                result[sym] = {"price": FALLBACK_PRICES.get(sym, 0), "change": 0, "source": "fallback"}
        _stock_cache = result
        _stock_cache_ts = time.time()
        return result

    # Fallback to Helius DAS (may not work for stocks)
    helius_prices = await get_prices(stocks)
    if helius_prices:
        _stock_cache = helius_prices
        _stock_cache_ts = time.time()
        return helius_prices

    # Final fallback
    return {s: {"price": FALLBACK_PRICES.get(s, 0), "change": 0, "source": "fallback"} for s in stocks}
