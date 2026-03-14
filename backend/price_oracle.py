"""MAXIA Price Oracle V11 — Prix live via Helius DAS API

Utilise l API Helius DAS (getAsset) sur le meme endpoint RPC
pour recuperer les prix des tokens. Fonctionne car Helius RPC
est autorise par Railway.

Strategie:
1. Helius DAS getAsset (meme domaine que RPC) -> prix live
2. Fallback mars 2026 si echec
"""
import asyncio, time
import httpx
from config import get_rpc_url, HELIUS_API_KEY

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
    "SOL": 119, "USDC": 1.0, "USDT": 1.0, "BONK": 0.000025,
    "JUP": 0.72, "RAY": 2.5, "WIF": 1.18, "RENDER": 7.5,
    "HNT": 3.92, "TRUMP": 2.87, "PYTH": 0.058, "W": 0.10, "ETH": 2400, "BTC": 80000, "ORCA": 1.50,
    "AAPL": 260, "TSLA": 407, "NVDA": 185, "GOOGL": 307,
    "MSFT": 403, "AMZN": 212, "META": 651, "MSTR": 340,
    "SPY": 585, "QQQ": 515,
}

_price_cache: dict = {}
_cache_ts: float = 0
_CACHE_TTL = 30

print("[PriceOracle] Initialise — Helius DAS API + fallback")


async def _fetch_helius_prices() -> dict:
    """Recupere les prix via Helius DAS API (getAsset) sur le meme domaine RPC."""
    rpc = get_rpc_url()
    if not rpc:
        return {}

    prices = {}

    # Methode 1: getAsset pour chaque token (DAS API sur meme endpoint)
    for sym, mint in TOKEN_MINTS.items():
        try:
            payload = {
                "jsonrpc": "2.0", "id": 1,
                "method": "getAsset",
                "params": {"id": mint},
            }
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.post(rpc, json=payload)
                data = resp.json()

            result = data.get("result", {})
            if result:
                # Helius getAsset retourne token_info.price_info
                token_info = result.get("token_info", {})
                price_info = token_info.get("price_info", {})
                price = price_info.get("price_per_token", 0)

                if price and price > 0:
                    prices[sym] = {"price": round(float(price), 6), "source": "helius_das"}
                    continue

                # Aussi checker content.links.image pour verifier que c est le bon token
                # Si pas de prix dans getAsset, essayer la methode 2
        except Exception:
            pass
        await asyncio.sleep(0.15)

    # Methode 2: Pour SOL specifiquement, utiliser getBalance d un gros compte
    # et comparer avec le prix connu. Ou lire un pool Raydium/Orca.
    # Plus simple: utiliser Helius /v0/tokens/metadata endpoint
    if "SOL" not in prices and HELIUS_API_KEY:
        try:
            # Helius a un endpoint REST specifique pour les prix
            url = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
            # Essayer getAsset avec le wrapped SOL
            payload = {
                "jsonrpc": "2.0", "id": 1,
                "method": "getAsset",
                "params": {"id": "So11111111111111111111111111111111111111112"},
            }
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.post(url, json=payload)
                data = resp.json()
            result = data.get("result", {})
            ti = result.get("token_info", {})
            pi = ti.get("price_info", {})
            price = pi.get("price_per_token", 0)
            if price and price > 0:
                prices["SOL"] = {"price": round(float(price), 6), "source": "helius_das"}
        except Exception:
            pass

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

    # Source 2: Fallback pour tout ce qui manque
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
    prices = await get_prices([symbol])
    return prices.get(symbol, {}).get("price", FALLBACK_PRICES.get(symbol, 0))


async def get_crypto_prices() -> dict:
    cryptos = ["SOL", "USDC", "USDT", "BONK", "JUP", "RAY", "WIF", "RENDER", "HNT", "TRUMP", "PYTH", "W", "ETH", "BTC", "ORCA"]
    return await get_prices(cryptos)


async def get_stock_prices() -> dict:
    stocks = ["AAPL", "TSLA", "NVDA", "GOOGL", "MSFT", "AMZN", "META", "MSTR", "SPY", "QQQ"]
    return await get_prices(stocks)
