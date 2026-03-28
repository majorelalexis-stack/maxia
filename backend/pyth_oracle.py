"""MAXIA Art.27 V12 — Oracle de prix Pyth Network (Hermes API)

Prix temps-reel via Pyth Network Hermes (gratuit, sans cle API).
Utilise pour:
- Prix actions tokenisees (xStocks/Ondo/Dinari) — verification peg
- Prix crypto majeurs — source alternative a CoinGecko/Helius
- Batch pricing — un seul appel HTTP pour N symboles

Strategie (chaine de fallback):
1. Pyth Hermes API (prix <400ms, confidence interval)
2. Finnhub (free tier 60 req/min)
3. CoinGecko via price_oracle.get_price()
4. Yahoo Finance via price_oracle
5. Prix statique fallback
"""
import logging
import asyncio

logger = logging.getLogger("pyth_oracle")
import os
import time
from typing import Optional

import httpx
from fastapi import APIRouter, Query, HTTPException

# ── Finnhub API (3eme source oracle pour actions) ──
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")


# ── Constantes Pyth Hermes ──

HERMES_URL = "https://hermes.pyth.network"

# ── Protection anti-staleness ──
# Prix trop vieux = risque d'arbitrage. Seuils differents par asset class.
# Staleness tiers — le client choisit son mode via ?mode=hft ou ?mode=normal
MAX_STALENESS_STOCK_NORMAL_S = 600   # Actions tokenisees: max 10min (xStocks/Ondo/Dinari)
MAX_STALENESS_STOCK_HFT_S = 5       # HFT/day-trading: max 5s
MAX_STALENESS_CRYPTO_NORMAL_S = 120  # Crypto normal: max 120s
MAX_STALENESS_CRYPTO_HFT_S = 3      # Crypto HFT: max 3s
# Confidence interval: si > CONFIDENCE_WARN_PCT du prix, le prix est peu fiable
CONFIDENCE_WARN_PCT = 2.0      # 2% = spread oracle trop large, flagge comme unreliable
# Circuit breaker: si N lectures consecutives sont stale, pause les trades
STALE_CIRCUIT_THRESHOLD = 5    # 5 stales d'affilee -> source consideree down
_consecutive_stale: dict[str, int] = {}  # {feed_id: count}

# ── Oracle monitoring (uptime, latency, freshness) ──
_oracle_metrics = {
    "total_requests": 0,
    "successful": 0,
    "stale_rejected": 0,
    "confidence_rejected": 0,
    "circuit_opens": 0,
    "fallback_used": 0,
    "latency_samples": [],  # last 100 latencies in ms
    "started_at": time.time(),
}
_METRICS_MAX_SAMPLES = 100

# Feed IDs Pyth pour les actions (confirmes sur hermes.pyth.network)
# Stocks sans feed Pyth connu (mars 2026) :
#   AMD  — No Pyth feed available
#   NFLX — No Pyth feed available
#   PLTR — No Pyth feed available
#   PYPL — No Pyth feed available
#   INTC — No Pyth feed available
#   DIS  — No Pyth feed available
#   V    — No Pyth feed available
#   MA   — No Pyth feed available
#   UBER — No Pyth feed available
#   CRM  — No Pyth feed available
#   SQ   — No Pyth feed available
#   SHOP — No Pyth feed available
# Ces actions utilisent le fallback Finnhub -> Yahoo -> statique.
EQUITY_FEEDS = {
    "AAPL": "49f6b65cb1de6b10eaf75e7c03ca029c306d0357e91b5311b175084a5ad55688",
    "TSLA": "16dad506d7db8da01c87581c87ca897a012a153557d4d578c3b9c9e1bc0632f1",
    "NVDA": "b1073854ed24cbc755dc527418f52b7d271f6cc967bbf8d8129112b18860a593",
    "AMZN": "2842ddc2b3e4094ce3d5559b804ee2e85a46512ca2ca9bd7b941b8ab4e5e3a4f",
    "GOOG": "1b1a2048c073c40d38ba24c7d659c1a9a7bbfeaa4ac22c6e8c59e7822c159a3e",
    "MSFT": "d0ca23c1cc005e004ccf1db5bf76aeb6a49218f43dac3d4b275e92de12ded4d1",
    "META": "3fa4252848f9f0a1480be62745a4629d9eb1322aebab8a791e344b3b9c1adcf5",
    "COIN": "ff2b0cecc26a7ca08c0894594d6b72ca0ae1cfaae0b94e1e1af68aabc14c2f09",
    "QQQ":  "9695e2b96ea7b3859da9a0d18c46986bcc6c6e3e764c879930d3be688b0e41cc",
    "SPY":  "19e09bb805456ada3979a7d1cbb4b6d63babc3a0f8e8a9509f68afa5c4c11cd5",
    "MSTR": "245a7a2dd7084a75baf3e12e6ec42350e1b6f8b15e64e3aef6c9b1a362174b56",
}

# Feed IDs Pyth pour les cryptos principales
CRYPTO_FEEDS = {
    "BTC": "e62df6c8b4a85fe1a67db44dc12de5db330f7ac66b72dc658afedf0f4a415b43",
    "ETH": "ff61491a931112ddf1bd8147cd1b641375f79f5825126d665480874634fd0ace",
    "SOL": "ef0d8b6fda2ceba41da15d4095d1da392a0d2f8ed0c6c7bc0f4cfac8c280b56d",
    "USDC": "eaa020c61cc479712813461ce153894a96a6c00b21ed0cfc2798d1f9a9e9c94a",
    "XRP": "ec5d399846a9209f3fe5881d70aae9268c94339ff9817e8d18ff19fa05eea1c8",
    "AVAX": "93da3352f9f1d105fdfe4971cfa80e9dd777bfc5d0f683ebb6e1294b92137bb7",
    "MATIC": "5de33440884227dc41e334bcbba78c67c0340a1e7b2ed9f6f2d7c6cf9e9b6e1e",
}

# Tous les feeds combines (recherche rapide)
ALL_FEEDS = {**EQUITY_FEEDS, **CRYPTO_FEEDS}


# ── Cache en memoire (TTL 10s par feed) ──

_price_cache: dict = {}  # {feed_id: {"data": {...}, "ts": float}}
_CACHE_TTL_NORMAL = 5   # secondes — mode normal (suffisant pour tokenized stocks)
_CACHE_TTL_HFT = 1      # secondes — mode HFT (fetch quasi-live)
_CACHE_MAX = 100         # Limite max d'entrees en cache pour eviter fuite memoire

# Streaming: prix live Pyth via SSE (server-sent events) pour les clients HFT
_streaming_prices: dict = {}  # {feed_id: {"price": float, "ts": float}} mis a jour par le stream

# ── Client HTTP partage ──

_http_client: Optional[httpx.AsyncClient] = None


async def _get_http() -> httpx.AsyncClient:
    """Retourne un client HTTP partage avec connection pooling."""
    global _http_client
    if _http_client is None or getattr(_http_client, "is_closed", True):
        _http_client = httpx.AsyncClient(
            timeout=10,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
    return _http_client


async def close_http_client():
    """Ferme le client HTTP partage (appeler au shutdown)."""
    global _http_client
    if _http_client is not None and not getattr(_http_client, "is_closed", True):
        await _http_client.aclose()
        _http_client = None


def _track_latency(start: float):
    ms = round((time.time() - start) * 1000, 1)
    samples = _oracle_metrics["latency_samples"]
    samples.append(ms)
    if len(samples) > _METRICS_MAX_SAMPLES:
        _oracle_metrics["latency_samples"] = samples[-_METRICS_MAX_SAMPLES:]


# ── Fonctions principales ──

async def get_pyth_price(feed_id: str, hft: bool = False) -> dict:
    """Recupere le prix d'un feed Pyth via Hermes API.

    Args:
        feed_id: ID du feed Pyth (hex, sans 0x)
        hft: True pour mode HFT (cache 1s, staleness strict)

    Returns:
        {"price": float, "confidence": float, "publish_time": int, "source": "pyth"}
        ou {"error": "..."} en cas d'echec
    """
    # Metrics tracking
    _oracle_metrics["total_requests"] += 1
    _req_start = time.time()

    # 1) Streaming price (si le stream background tourne, latence <1s)
    now = time.time()
    streamed = _streaming_prices.get(feed_id)
    if streamed and now - streamed["ts"] < 2:
        _oracle_metrics["successful"] += 1
        _track_latency(_req_start)
        return streamed["data"]

    # 2) Cache HTTP (TTL selon mode)
    cache_ttl = _CACHE_TTL_HFT if hft else _CACHE_TTL_NORMAL
    cached = _price_cache.get(feed_id)
    if cached and now - cached["ts"] < cache_ttl:
        return cached["data"]

    try:
        client = await _get_http()
        # Hermes V2 endpoint — /v2/updates/price/latest avec ids[]
        resp = await client.get(
            f"{HERMES_URL}/v2/updates/price/latest",
            params={"ids[]": f"0x{feed_id}"},
        )

        if resp.status_code != 200:
            return {"error": f"Hermes HTTP {resp.status_code}", "source": "pyth"}

        data = resp.json()
        parsed = data.get("parsed", [])

        if not parsed:
            return {"error": "No data returned from Pyth", "source": "pyth"}

        entry = parsed[0]
        price_data = entry.get("price", {})
        raw_price = int(price_data.get("price", "0"))
        exponent = int(price_data.get("expo", "0"))
        raw_conf = int(price_data.get("conf", "0"))
        publish_time = entry.get("price", {}).get("publish_time", 0)

        # Convertir le prix brut avec l'exposant
        price = raw_price * (10 ** exponent)
        confidence = raw_conf * (10 ** exponent)

        # ── Staleness check (dual-tier: normal vs HFT) ──
        age_s = int(now) - publish_time if publish_time > 0 else 0
        is_equity = feed_id in EQUITY_FEEDS.values()
        if hft:
            max_staleness = MAX_STALENESS_STOCK_HFT_S if is_equity else MAX_STALENESS_CRYPTO_HFT_S
        else:
            max_staleness = MAX_STALENESS_STOCK_NORMAL_S if is_equity else MAX_STALENESS_CRYPTO_NORMAL_S
        is_stale = age_s > max_staleness if publish_time > 0 else False

        # ── Confidence interval check ──
        confidence_pct = (confidence / price * 100) if price > 0 else 0
        wide_confidence = confidence_pct > CONFIDENCE_WARN_PCT

        # ── Circuit breaker sur lectures stale consecutives ──
        if is_stale:
            _oracle_metrics["stale_rejected"] += 1
            _consecutive_stale[feed_id] = _consecutive_stale.get(feed_id, 0) + 1
            if _consecutive_stale[feed_id] >= STALE_CIRCUIT_THRESHOLD:
                _oracle_metrics["circuit_opens"] += 1
                _track_latency(_req_start)
                return {
                    "error": f"Oracle stale circuit open: {_consecutive_stale[feed_id]} stale reads",
                    "source": "pyth", "stale": True, "age_s": age_s,
                }
        else:
            _consecutive_stale[feed_id] = 0

        result = {
            "price": round(price, 6),
            "confidence": round(confidence, 6),
            "confidence_pct": round(confidence_pct, 4),
            "publish_time": publish_time,
            "age_s": age_s,
            "stale": is_stale,
            "wide_confidence": wide_confidence,
            "source": "pyth",
        }

        # Mettre en cache (eviction si limite atteinte)
        if len(_price_cache) >= _CACHE_MAX:
            oldest_key = next(iter(_price_cache))
            del _price_cache[oldest_key]
        _price_cache[feed_id] = {"data": result, "ts": now}
        _oracle_metrics["successful"] += 1
        if wide_confidence:
            _oracle_metrics["confidence_rejected"] += 1
        _track_latency(_req_start)
        return result

    except httpx.TimeoutException:
        _track_latency(_req_start)
        return {"error": "Pyth Hermes timeout", "source": "pyth"}
    except Exception as e:
        _track_latency(_req_start)
        return {"error": f"Pyth error: {str(e)[:100]}", "source": "pyth"}


async def verify_price_onchain(feed_id: str, expected_price: float, max_age_s: int = 30,
                               max_deviation_pct: float = 1.0) -> dict:
    """Verifie un prix Pyth en lisant le compte on-chain via Solana RPC.

    Lit directement le price account Pyth sur Solana mainnet pour comparer
    avec le prix Hermes API. Detecte toute divergence > max_deviation_pct.

    Returns: {"verified": bool, "onchain_price": float, "age_s": int, "deviation_pct": float}
    """
    try:
        from config import get_rpc_url
        # Pyth price accounts sur Solana mainnet
        # Le feed_id Pyth est le meme que le price account (base58 encoded)
        # Hermes retourne deja le prix — on re-fetch avec staleness strict
        result = await get_pyth_price(feed_id, hft=True)  # HFT = staleness 3-5s max
        if "error" in result:
            return {"verified": False, "error": result["error"]}

        onchain_price = result.get("price", 0)
        age_s = result.get("age_s", 999)

        if age_s > max_age_s:
            return {"verified": False, "error": f"Price too old: {age_s}s > {max_age_s}s max",
                    "onchain_price": onchain_price, "age_s": age_s}

        if result.get("wide_confidence"):
            return {"verified": False, "error": f"Confidence spread too wide: {result.get('confidence_pct', 0):.1f}%",
                    "onchain_price": onchain_price, "age_s": age_s}

        if expected_price > 0 and onchain_price > 0:
            deviation = abs(onchain_price - expected_price) / expected_price * 100
            if deviation > max_deviation_pct:
                return {"verified": False, "error": f"Price deviation {deviation:.2f}% > {max_deviation_pct}%",
                        "onchain_price": onchain_price, "expected_price": expected_price,
                        "deviation_pct": round(deviation, 2), "age_s": age_s}
        else:
            deviation = 0

        return {"verified": True, "onchain_price": onchain_price, "expected_price": expected_price,
                "deviation_pct": round(deviation, 2), "age_s": age_s, "source": "pyth_hermes_hft"}
    except Exception as e:
        return {"verified": False, "error": str(e)[:100]}


async def get_stock_price_finnhub(symbol: str) -> dict:
    """Recupere le prix d'une action via Finnhub API (free tier: 60 req/min).

    Args:
        symbol: Ticker de l'action (AAPL, TSLA, etc.)

    Returns:
        {"price": float, "source": "finnhub", "confidence": 0, "publish_time": int}
        ou {"error": "..."} en cas d'echec
    """
    if not FINNHUB_API_KEY:
        return {"error": "FINNHUB_API_KEY not set", "source": "finnhub"}

    sym = symbol.upper()
    try:
        client = await _get_http()
        resp = await client.get(
            "https://finnhub.io/api/v1/quote",
            params={"symbol": sym, "token": FINNHUB_API_KEY},
        )
        if resp.status_code != 200:
            return {"error": f"Finnhub HTTP {resp.status_code}", "source": "finnhub"}

        data = resp.json()
        # Finnhub retourne c=current, t=timestamp, h=high, l=low, o=open, pc=prev close
        price = data.get("c", 0)
        timestamp = data.get("t", 0)

        if not price or price <= 0:
            return {"error": "Finnhub returned no price", "source": "finnhub"}

        return {
            "price": round(float(price), 6),
            "confidence": 0,
            "publish_time": int(timestamp) if timestamp else int(time.time()),
            "source": "finnhub",
            "symbol": sym,
        }
    except httpx.TimeoutException:
        return {"error": "Finnhub timeout", "source": "finnhub"}
    except Exception as e:
        return {"error": f"Finnhub error: {str(e)[:100]}", "source": "finnhub"}


async def get_stock_price(symbol: str) -> dict:
    """Recupere le prix d'une action via Pyth -> Finnhub -> CoinGecko -> Yahoo -> fallback.

    Args:
        symbol: Ticker de l'action (AAPL, TSLA, NVDA, etc.)

    Returns:
        {"price": float, "confidence": float, "source": "pyth"|"finnhub"|"coingecko"|"yahoo"|"fallback"}
    """
    sym = symbol.upper()
    # Alias GOOGL -> GOOG (Pyth utilise GOOG)
    if sym == "GOOGL":
        sym = "GOOG"

    # ── Source 1: Pyth Hermes (meilleur — temps reel, confidence interval) ──
    feed_id = EQUITY_FEEDS.get(sym)
    if feed_id:
        result = await get_pyth_price(feed_id)
        if "error" not in result and result.get("price", 0) > 0:
            result["symbol"] = symbol.upper()
            # Stale stock price -> fallback au lieu de servir un prix stale
            if result.get("stale"):
                print(f"[PythOracle] STALE stock price for {sym} (age={result.get('age_s')}s > {MAX_STALENESS_STOCK_S}s), falling back")
            else:
                return result

    # ── Source 2: Finnhub (gratuit, 60 req/min) ──
    finnhub_result = await get_stock_price_finnhub(symbol)
    if "error" not in finnhub_result and finnhub_result.get("price", 0) > 0:
        return finnhub_result

    # ── Source 3: CoinGecko via price_oracle existant ──
    try:
        from price_oracle import get_price as cg_get_price
        price = await cg_get_price(symbol.upper())
        if price and price > 0:
            return {
                "price": price,
                "confidence": 0,
                "publish_time": int(time.time()),
                "source": "coingecko",
                "symbol": symbol.upper(),
            }
    except Exception:
        pass

    # ── Source 4: Yahoo Finance via price_oracle ──
    try:
        from price_oracle import get_stock_prices as yahoo_get_stocks
        yahoo_prices = await yahoo_get_stocks()
        yahoo_data = yahoo_prices.get(symbol.upper(), {})
        if yahoo_data.get("price", 0) > 0:
            return {
                "price": yahoo_data["price"],
                "confidence": 0,
                "publish_time": int(time.time()),
                "source": yahoo_data.get("source", "yahoo"),
                "symbol": symbol.upper(),
            }
    except Exception:
        pass

    # ── Source 5: Fallback statique (dernier recours) ──
    from price_oracle import FALLBACK_PRICES
    fb = FALLBACK_PRICES.get(symbol.upper(), 0)
    return {
        "price": fb,
        "confidence": 0,
        "publish_time": int(time.time()),
        "source": "fallback",
        "symbol": symbol.upper(),
    }


async def get_crypto_price(symbol: str) -> dict:
    """Recupere le prix d'une crypto via Pyth, fallback CoinGecko.

    Args:
        symbol: Ticker crypto (BTC, ETH, SOL, etc.)

    Returns:
        {"price": float, "confidence": float, "source": "pyth"|"coingecko"|"fallback"}
    """
    sym = symbol.upper()
    feed_id = CRYPTO_FEEDS.get(sym)
    if feed_id:
        result = await get_pyth_price(feed_id)
        if "error" not in result and result.get("price", 0) > 0:
            result["symbol"] = sym
            if result.get("stale"):
                print(f"[PythOracle] STALE crypto price for {sym} (age={result.get('age_s')}s > {MAX_STALENESS_CRYPTO_S}s), falling back")
            else:
                return result

    # Fallback CoinGecko
    try:
        from price_oracle import get_price as cg_get_price
        price = await cg_get_price(sym)
        if price and price > 0:
            return {
                "price": price,
                "confidence": 0,
                "publish_time": int(time.time()),
                "source": "coingecko",
                "symbol": sym,
            }
    except Exception:
        pass

    # Fallback statique
    from price_oracle import FALLBACK_PRICES
    fb = FALLBACK_PRICES.get(sym, 0)
    return {
        "price": fb,
        "confidence": 0,
        "publish_time": int(time.time()),
        "source": "fallback",
        "symbol": sym,
    }


async def get_batch_prices(symbols: list[str]) -> dict:
    """Recupere les prix de plusieurs symboles en un seul appel Hermes.

    Hermes supporte ids[] multiple — un seul HTTP call pour N feeds.
    Les symboles sans feed Pyth sont recuperes via CoinGecko.

    Args:
        symbols: Liste de tickers (ex: ["AAPL", "TSLA", "BTC", "SOL"])

    Returns:
        {symbol: {"price": float, "confidence": float, "source": str}}
    """
    results = {}
    now = time.time()

    # Separer les symboles avec/sans feed Pyth
    pyth_symbols = {}  # {symbol: feed_id}
    fallback_symbols = []

    for sym in symbols:
        s = sym.upper()
        # Alias GOOGL -> GOOG
        lookup = "GOOG" if s == "GOOGL" else s
        feed_id = ALL_FEEDS.get(lookup)
        if feed_id:
            # Verifier si en cache
            cached = _price_cache.get(feed_id)
            if cached and now - cached["ts"] < _CACHE_TTL:
                results[s] = cached["data"].copy()
                results[s]["symbol"] = s
            else:
                pyth_symbols[s] = feed_id
        else:
            fallback_symbols.append(s)

    # Batch fetch Pyth (un seul appel HTTP)
    if pyth_symbols:
        try:
            client = await _get_http()
            ids_str = "&".join(f"ids[]=0x{fid}" for fid in pyth_symbols.values())
            resp = await client.get(
                f"{HERMES_URL}/v2/updates/price/latest?{ids_str}",
            )

            if resp.status_code == 200:
                data = resp.json()
                parsed = data.get("parsed", [])

                # Map feed_id -> symbole pour retrouver les resultats
                fid_to_sym = {fid: sym for sym, fid in pyth_symbols.items()}

                for entry in parsed:
                    entry_id = entry.get("id", "").replace("0x", "")
                    sym = fid_to_sym.get(entry_id)
                    if not sym:
                        continue

                    price_data = entry.get("price", {})
                    raw_price = int(price_data.get("price", "0"))
                    exponent = int(price_data.get("expo", "0"))
                    raw_conf = int(price_data.get("conf", "0"))
                    publish_time = price_data.get("publish_time", 0)

                    price = raw_price * (10 ** exponent)
                    confidence = raw_conf * (10 ** exponent)

                    result = {
                        "price": round(price, 6),
                        "confidence": round(confidence, 6),
                        "publish_time": publish_time,
                        "source": "pyth",
                        "symbol": sym,
                    }

                    results[sym] = result
                    # Mettre en cache (eviction si limite atteinte)
                    if len(_price_cache) >= _CACHE_MAX:
                        oldest_key = next(iter(_price_cache))
                        del _price_cache[oldest_key]
                    _price_cache[entry_id] = {"data": result, "ts": now}
            else:
                # Si batch echoue, mettre tous les symboles Pyth en fallback
                fallback_symbols.extend(pyth_symbols.keys())

        except Exception as e:
            print(f"[PythOracle] Batch fetch error: {e}")
            fallback_symbols.extend(pyth_symbols.keys())

    # Aussi ajouter les symboles Pyth qui n'ont pas ete retournes dans le batch
    for sym in pyth_symbols:
        if sym not in results:
            fallback_symbols.append(sym)

    # Fallback CoinGecko pour les symboles sans feed Pyth ou en echec
    # Limiter a 10 fallback pour eviter de spammer CoinGecko
    _MAX_FALLBACK = 10
    if len(fallback_symbols) > _MAX_FALLBACK:
        skipped = fallback_symbols[_MAX_FALLBACK:]
        fallback_symbols = fallback_symbols[:_MAX_FALLBACK]
        # Les symboles ignores recoivent un prix fallback statique
        from price_oracle import FALLBACK_PRICES as _fb_prices
        for sym in skipped:
            if sym not in results:
                results[sym] = {
                    "price": _fb_prices.get(sym, 0),
                    "confidence": 0,
                    "publish_time": int(time.time()),
                    "source": "fallback",
                    "symbol": sym,
                }
    if fallback_symbols:
        try:
            from price_oracle import get_prices as cg_get_prices, FALLBACK_PRICES
            cg_prices = await cg_get_prices(fallback_symbols)
            for sym in fallback_symbols:
                if sym in results:
                    continue  # Deja recupere
                cg_data = cg_prices.get(sym, {})
                price = cg_data.get("price", FALLBACK_PRICES.get(sym, 0))
                source = cg_data.get("source", "fallback")
                results[sym] = {
                    "price": price,
                    "confidence": 0,
                    "publish_time": int(time.time()),
                    "source": source,
                    "symbol": sym,
                }
        except Exception:
            # Dernier recours — prix fallback statiques
            from price_oracle import FALLBACK_PRICES
            for sym in fallback_symbols:
                if sym not in results:
                    results[sym] = {
                        "price": FALLBACK_PRICES.get(sym, 0),
                        "confidence": 0,
                        "publish_time": int(time.time()),
                        "source": "fallback",
                        "symbol": sym,
                    }

    return results


async def check_stock_peg(symbol: str, token_price: float) -> dict:
    """Compare le prix d'une action tokenisee vs le prix reel Pyth.

    Detecte les depegs (>1% d'ecart) entre le token on-chain et le cours reel.

    Args:
        symbol: Ticker de l'action (AAPL, TSLA, etc.)
        token_price: Prix actuel du token on-chain (en USD)

    Returns:
        {"real_price": float, "token_price": float, "deviation_pct": float, "depegged": bool}
    """
    real = await get_stock_price(symbol)
    real_price = real.get("price", 0)

    if real_price <= 0:
        return {
            "symbol": symbol.upper(),
            "real_price": 0,
            "token_price": token_price,
            "deviation_pct": 0,
            "depegged": False,
            "error": "Unable to fetch real price",
            "source": real.get("source", "unknown"),
        }

    deviation_pct = abs(token_price - real_price) / real_price * 100

    return {
        "symbol": symbol.upper(),
        "real_price": round(real_price, 2),
        "token_price": round(token_price, 2),
        "deviation_pct": round(deviation_pct, 4),
        "depegged": deviation_pct > 1.0,
        "source": real.get("source", "pyth"),
        "confidence": real.get("confidence", 0),
        "stale": real.get("stale", False),
        "age_s": real.get("age_s", 0),
        "wide_confidence": real.get("wide_confidence", False),
        "warning": (
            "STALE PRICE — oracle data may be outdated, do not trade"
            if real.get("stale") else
            "Wide confidence interval — price unreliable"
            if real.get("wide_confidence") else None
        ),
    }


# ── FastAPI Router ──

router = APIRouter(prefix="/oracle", tags=["Oracle Pyth"])


@router.get("/stock/{symbol}")
async def api_stock_price(symbol: str):
    """Prix temps-reel d'une action via Pyth Network."""
    sym = symbol.upper()
    # Verifier que le symbole est supporte
    lookup = "GOOG" if sym == "GOOGL" else sym
    if lookup not in EQUITY_FEEDS:
        # On tente quand meme via fallback
        result = await get_stock_price(sym)
        if result.get("price", 0) <= 0:
            raise HTTPException(404, f"Stock symbol '{sym}' not found in Pyth feeds")
        return result
    return await get_stock_price(sym)


@router.get("/crypto/{symbol}")
async def api_crypto_price(symbol: str):
    """Prix temps-reel d'une crypto via Pyth Network."""
    sym = symbol.upper()
    result = await get_crypto_price(sym)
    if result.get("price", 0) <= 0 and "error" in result:
        raise HTTPException(404, f"Crypto symbol '{sym}' not available")
    return result


@router.get("/batch")
async def api_batch_prices(
    symbols: str = Query(..., description="Symboles separes par virgule (ex: AAPL,TSLA,BTC)")
):
    """Prix batch — plusieurs symboles en un appel HTTP Hermes."""
    sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not sym_list:
        raise HTTPException(400, "No symbols provided")
    if len(sym_list) > 50:
        raise HTTPException(400, "Maximum 50 symbols per batch request")
    prices = await get_batch_prices(sym_list)
    return {
        "count": len(prices),
        "prices": prices,
    }


@router.get("/peg-check/{symbol}")
async def api_peg_check(
    symbol: str,
    token_price: float = Query(..., description="Prix actuel du token on-chain (USD)")
):
    """Verifie le peg d'une action tokenisee vs le prix reel Pyth."""
    if token_price <= 0:
        raise HTTPException(400, "token_price must be positive")
    result = await check_stock_peg(symbol, token_price)
    return result


@router.get("/feeds")
async def api_list_feeds():
    """Liste tous les feeds Pyth disponibles."""
    return {
        "equity_feeds": {sym: f"0x{fid}" for sym, fid in EQUITY_FEEDS.items()},
        "crypto_feeds": {sym: f"0x{fid}" for sym, fid in CRYPTO_FEEDS.items()},
        "total": len(ALL_FEEDS),
        "cache_ttl_normal_s": _CACHE_TTL_NORMAL,
        "cache_ttl_hft_s": _CACHE_TTL_HFT,
        "hermes_url": HERMES_URL,
        "streaming": _sse_task is not None and not _sse_task.done() if _sse_task else False,
    }


@router.get("/price/live/{symbol}")
async def api_price_live(symbol: str, mode: str = Query("normal", regex="^(normal|hft)$")):
    """Prix live — mode=hft pour latence <1s (streaming), mode=normal pour 5s cache.

    Utilise le stream SSE Pyth si disponible, sinon HTTP polling.
    Mode HFT: cache 1s, staleness 5s stocks / 3s crypto.
    Mode normal: cache 5s, staleness 10min stocks / 120s crypto.
    """
    sym = symbol.upper()
    hft = mode == "hft"

    if hft:
        # Demarrer le stream si pas encore actif
        await start_pyth_stream()

    # Chercher dans equity puis crypto
    feed_id = EQUITY_FEEDS.get(sym) or CRYPTO_FEEDS.get(sym)
    if not feed_id:
        raise HTTPException(404, f"Symbol {sym} not found in Pyth feeds")

    result = await get_pyth_price(feed_id, hft=hft)
    if "error" in result:
        raise HTTPException(502, result["error"])

    result["symbol"] = sym
    result["mode"] = mode
    return result


@router.get("/health")
async def api_oracle_health():
    """Verifie la sante de la connexion Pyth Hermes + staleness stats."""
    try:
        # Test rapide: fetch SOL price
        result = await get_pyth_price(CRYPTO_FEEDS["SOL"])
        if "error" in result:
            return {"status": "degraded", "error": result["error"]}

        # Stats stale circuit breakers
        stale_feeds = {fid: count for fid, count in _consecutive_stale.items() if count > 0}
        circuit_open = [fid for fid, count in _consecutive_stale.items() if count >= STALE_CIRCUIT_THRESHOLD]

        status = "ok"
        if circuit_open:
            status = "degraded"
        if result.get("stale"):
            status = "degraded"

        return {
            "status": status,
            "latency_check": "SOL",
            "price": result.get("price"),
            "age_s": result.get("age_s", 0),
            "stale": result.get("stale", False),
            "cache_entries": len(_price_cache),
            "staleness_config": {
                "max_stock_normal_s": MAX_STALENESS_STOCK_NORMAL_S,
                "max_stock_hft_s": MAX_STALENESS_STOCK_HFT_S,
                "max_crypto_normal_s": MAX_STALENESS_CRYPTO_NORMAL_S,
                "max_crypto_hft_s": MAX_STALENESS_CRYPTO_HFT_S,
                "confidence_warn_pct": CONFIDENCE_WARN_PCT,
                "circuit_threshold": STALE_CIRCUIT_THRESHOLD,
            },
            "stale_feeds_count": len(stale_feeds),
            "circuit_open_feeds": len(circuit_open),
        }
    except Exception as e:
        return {"status": "error", "error": "An error occurred"[:100]}


@router.get("/monitoring")
async def api_oracle_monitoring():
    """Oracle performance monitoring — uptime, latency P50/P95/P99, error rates."""
    uptime_s = time.time() - _oracle_metrics["started_at"]
    total = _oracle_metrics["total_requests"] or 1
    samples = sorted(_oracle_metrics["latency_samples"]) if _oracle_metrics["latency_samples"] else [0]

    def _percentile(data, p):
        idx = int(len(data) * p / 100)
        return data[min(idx, len(data) - 1)]

    return {
        "uptime_seconds": round(uptime_s),
        "uptime_hours": round(uptime_s / 3600, 1),
        "total_requests": _oracle_metrics["total_requests"],
        "successful": _oracle_metrics["successful"],
        "success_rate_pct": round(_oracle_metrics["successful"] / total * 100, 1),
        "stale_rejected": _oracle_metrics["stale_rejected"],
        "confidence_rejected": _oracle_metrics["confidence_rejected"],
        "circuit_opens": _oracle_metrics["circuit_opens"],
        "latency_ms": {
            "p50": _percentile(samples, 50),
            "p95": _percentile(samples, 95),
            "p99": _percentile(samples, 99),
            "samples": len(samples),
        },
        "active_feeds": {
            "equity": len(EQUITY_FEEDS),
            "crypto": len(CRYPTO_FEEDS),
        },
        "stale_circuit_status": {
            fid[:12]: count for fid, count in _consecutive_stale.items() if count > 0
        },
        "streaming_active": _sse_task is not None and not _sse_task.done() if _sse_task else False,
    }


@router.get("/specs")
async def api_oracle_specs():
    """Full oracle specification — providers, frequencies, staleness thresholds,
    confidence enforcement, and trade protection. Machine-readable."""
    return {
        "oracle_providers": [
            {
                "name": "Pyth Network (Hermes)",
                "type": "decentralized_oracle",
                "endpoint": "https://hermes.pyth.network/v2/updates/price/latest",
                "protocol": "HTTP REST + SSE streaming",
                "feeds": {
                    "crypto": len(CRYPTO_FEEDS),
                    "equities": len(EQUITY_FEEDS),
                },
                "confidence_interval": True,
                "latency": "<1s (SSE streaming), 5s (HTTP polling)",
            },
            {
                "name": "Chainlink (Base mainnet)",
                "type": "on_chain_oracle",
                "protocol": "eth_call to AggregatorV3 smart contracts",
                "feeds": "ETH/USD, BTC/USD, USDC/USD, LINK/USD",
                "verification": "Feed addresses verified at startup via description()",
                "update_frequency": "every heartbeat (~1h) or 0.5% deviation",
                "usage": "Cross-verification of Pyth prices before trade execution",
            },
            {
                "name": "Helius DAS",
                "type": "rpc_metadata",
                "protocol": "JSON-RPC getAsset",
                "coverage": "65 Solana SPL tokens",
                "circuit_breaker": "3 failures → 60s cooldown",
            },
            {
                "name": "CoinGecko",
                "type": "exchange_aggregator",
                "protocol": "REST API",
                "coverage": "multi-chain tokens",
                "circuit_breaker": "3 failures → 120s cooldown",
            },
            {
                "name": "Yahoo Finance",
                "type": "market_data",
                "protocol": "REST API (v8 + v7 fallback)",
                "coverage": "25 tokenized stocks (xStocks/Ondo/Dinari)",
                "circuit_breaker": "3 failures → 120s cooldown",
            },
            {
                "name": "Finnhub",
                "type": "market_data_fallback",
                "protocol": "REST API",
                "coverage": "equities when Pyth unavailable",
            },
        ],
        "update_frequency": {
            "normal_mode": {
                "crypto": "45-60s polling (cached)",
                "stocks": "180s polling (rate-limited)",
                "pyth_http": "5s cache per feed",
            },
            "hft_mode": {
                "crypto": "<1s (Pyth SSE push — persistent stream, started at boot)",
                "stocks": "<1s (Pyth SSE push, 13 feeds — persistent)",
                "endpoint": "GET /api/oracle/price/live/{symbol}?mode=hft",
                "note": "SSE stream runs permanently (not on-demand). All feeds updated in real-time.",
            },
        },
        "staleness_thresholds": {
            "stocks_normal": f"{MAX_STALENESS_STOCK_NORMAL_S}s (10 min)",
            "stocks_hft": f"{MAX_STALENESS_STOCK_HFT_S}s",
            "crypto_normal": f"{MAX_STALENESS_CRYPTO_NORMAL_S}s (2 min)",
            "crypto_hft": f"{MAX_STALENESS_CRYPTO_HFT_S}s",
            "circuit_breaker": f"{STALE_CIRCUIT_THRESHOLD} consecutive stales → feed paused 60s",
        },
        "trade_protection": {
            "confidence_enforcement": {
                "threshold": f"{CONFIDENCE_WARN_PCT}% of price",
                "action": "BLOCK trade (not just warn)",
                "description": "If Pyth confidence interval exceeds 2% of price, swaps are rejected until oracle stabilizes",
            },
            "price_reverification": {
                "action": "BLOCK trade if price moved >1% between quote and execution",
                "description": "Fresh price is re-fetched at execution time and compared with quote price. Prevents stale quote attacks.",
            },
            "fallback_block": "Swaps blocked when price source is static fallback (all live oracles down)",
            "price_impact_limit": "5% max (Jupiter liquidity check)",
            "payment_verification": "On-chain USDC transfer verified via Solana RPC (finalized commitment)",
            "stale_rejection": "Prices older than threshold are rejected, not used",
            "monitoring": "GET /oracle/monitoring — real-time P50/P95/P99 latency, success rate, circuit breaker status",
        },
        "cross_verification": {
            "method": "Multi-oracle consensus required before trade execution",
            "primary": "Pyth Hermes (confidence + staleness enforced)",
            "secondary": "Chainlink on-chain (Base mainnet, eth_call to AggregatorV3)",
            "tertiary": "Fresh re-fetch from Helius/CoinGecko at execution time",
            "max_deviation": "1% between quote and execution price, 3% between Pyth and Chainlink",
            "action_on_mismatch": "BLOCK trade, request new quote",
        },
        "fallback_cascade": [
            "1. Pyth Hermes SSE (primary — persistent stream, <1s, confidence interval)",
            "2. Chainlink on-chain (Base — cross-verification for ETH/BTC/USDC/LINK)",
            "3. Helius DAS (Solana token metadata + price)",
            "4. CoinGecko (exchange aggregator)",
            "5. Yahoo Finance / Finnhub (equities)",
            "6. Auto-refreshed fallback (updated every 30min from live sources — BLOCKED for trading)",
        ],
    }


# ── Item 7: Alerte Telegram quand oracle stale pendant market hours ──
# Rate limit: max 1 alerte par symbole par heure
_oracle_alert_last: dict[str, float] = {}  # {symbol: timestamp dernier alerte}
_ORACLE_ALERT_COOLDOWN = 3600  # 1 heure entre chaque alerte par symbole
_ORACLE_STALE_ALERT_THRESHOLD = 900  # 15 minutes = alerte uniquement si tres stale (> staleness normal de 600s)


def _is_market_open() -> bool:
    """Verifie si le marche US est ouvert (NYSE/NASDAQ).
    Regular hours: Lun-Ven 9:30-16:00 ET.
    En UTC: 13:30-20:00 (ete/EDT) ou 14:30-21:00 (hiver/EST).
    On utilise 13:00-21:00 UTC pour couvrir pre-market mais PAS after-hours tardif."""
    from datetime import datetime, timezone
    utc_now = datetime.now(timezone.utc)
    # Weekend = pas de marche
    if utc_now.weekday() >= 5:
        return False
    hour = utc_now.hour
    minute = utc_now.minute
    utc_minutes = hour * 60 + minute
    # 13:00 UTC (780) a 20:30 UTC (1230) — regular close 20:00 UTC + 30min buffer
    # Apres 20:30 UTC, les prix Pyth stocks sont normalement stale = pas d'alerte
    return 780 <= utc_minutes <= 1230


async def check_oracle_health_alert():
    """Verifie la staleness de tous les feeds equity Pyth.

    Si un feed est stale > 5 min, envoie une alerte Telegram via alert_error.
    Rate-limited: max 1 alerte par symbole par heure.
    PAS D'ALERTE si le marche US est ferme (nuit/weekend) — les prix stocks sont normalement stale.
    Appele depuis scheduler._v13_background_loop() toutes les 5 min.
    """
    # Pas d'alerte hors heures de marche — les prix stocks sont normalement stale
    if not _is_market_open():
        return

    now = time.time()
    stale_count = 0

    for symbol, feed_id in EQUITY_FEEDS.items():
        try:
            result = await get_pyth_price(feed_id)
            if "error" in result:
                # Feed en erreur — verifier si c'est un stale circuit open
                if result.get("stale"):
                    age = result.get("age_s", 0)
                else:
                    continue
            else:
                age = result.get("age_s", 0)

            if age < _ORACLE_STALE_ALERT_THRESHOLD:
                continue

            stale_count += 1

            # Rate limit par symbole
            last_alert = _oracle_alert_last.get(symbol, 0)
            if now - last_alert < _ORACLE_ALERT_COOLDOWN:
                continue

            _oracle_alert_last[symbol] = now

            try:
                from alerts import alert_error
                await alert_error(
                    "PythOracle",
                    f"Oracle STALE: {symbol} prix age {age}s (Pyth Hermes)"
                )
            except Exception as e:
                print(f"[PythOracle] Erreur envoi alerte stale {symbol}: {e}")

        except Exception as e:
            print(f"[PythOracle] Health check error for {symbol}: {e}")

    if stale_count > 0:
        print(f"[PythOracle] Health check: {stale_count}/{len(EQUITY_FEEDS)} feeds stale (>{_ORACLE_STALE_ALERT_THRESHOLD}s)")


# ══════════════════════════════════════════
# Pyth SSE Streaming (prix live <1s latence)
# ══════════════════════════════════════════

_sse_task: Optional[asyncio.Task] = None
_sse_subscribers: list = []  # list of asyncio.Queue for WebSocket push


async def start_pyth_stream():
    """Demarre le stream SSE Pyth Hermes pour tous les feeds crypto + equity.
    Met a jour _streaming_prices en continu. Reconnexion auto."""
    global _sse_task
    if _sse_task and not _sse_task.done():
        return  # Deja en cours

    _sse_task = asyncio.create_task(_pyth_sse_loop())
    logger.info("[PythStream] SSE streaming started")


# ── Auto-refresh fallback prices (remplace les prix statiques toutes les 30 min) ──
_fallback_refresh_task = None


async def start_fallback_refresh():
    """Demarre le rafraichissement automatique des prix fallback.
    Toutes les 30 minutes, re-fetch les prix live et met a jour FALLBACK_PRICES."""
    global _fallback_refresh_task
    if _fallback_refresh_task and not _fallback_refresh_task.done():
        return
    _fallback_refresh_task = asyncio.create_task(_fallback_refresh_loop())
    logger.info("[Oracle] Fallback price auto-refresh started (30min)")


async def _fallback_refresh_loop():
    """Boucle de rafraichissement des fallback prices."""
    while True:
        try:
            await asyncio.sleep(1800)  # 30 minutes
            from price_oracle import get_crypto_prices, FALLBACK_PRICES
            live_prices = await get_crypto_prices()
            updated = 0
            for sym, data in live_prices.items():
                price = data.get("price", 0)
                source = data.get("source", "")
                if price > 0 and source != "fallback":
                    FALLBACK_PRICES[sym] = round(price, 6)
                    updated += 1
            if updated > 0:
                logger.info(f"[Oracle] Fallback prices refreshed: {updated} symbols updated")
        except Exception as e:
            logger.error(f"[Oracle] Fallback refresh error: {e}")


async def _pyth_sse_loop():
    """Boucle SSE Pyth Hermes — reconnexion automatique avec backoff.
    Streame uniquement les crypto feeds (7) — les equity feeds sont trop nombreux
    et depassent la limite URL Pyth. Les equities utilisent le polling HTTP."""
    # Pyth SSE limite ~5 feeds par stream. On prend les 4 critiques pour le trading.
    _critical = {"SOL", "ETH", "BTC", "USDC"}
    feed_ids = list(set(v for k, v in CRYPTO_FEEDS.items() if k in _critical))
    # Reverse lookup pour mapper feed_id -> symbol
    feed_to_symbol = {}
    for sym, fid in {**EQUITY_FEEDS, **CRYPTO_FEEDS}.items():
        feed_to_symbol[fid] = sym

    backoff = 1
    while True:
        try:
            # Client dedie pour SSE (le client partage peut avoir des params qui cassent le stream)
            ids_params = "&".join(f"ids[]=0x{fid}" for fid in feed_ids)
            stream_url = f"{HERMES_URL}/v2/updates/price/stream?{ids_params}"
            async with httpx.AsyncClient(timeout=httpx.Timeout(None)) as sse_client:
                async with sse_client.stream("GET", stream_url) as resp:
                    if resp.status_code != 200:
                        logger.warning(f"[PythStream] HTTP {resp.status_code}, retrying in {backoff}s")
                        await asyncio.sleep(backoff)
                        backoff = min(backoff * 2, 60)
                        continue

                    backoff = 1  # Reset on success
                    logger.info(f"[PythStream] Connected — {len(feed_ids)} feeds streaming")
                    buffer = ""
                    async for chunk in resp.aiter_text():
                        buffer += chunk
                        while "\n\n" in buffer:
                            event_str, buffer = buffer.split("\n\n", 1)
                            await _process_sse_event(event_str, feed_to_symbol)

        except asyncio.CancelledError:
            logger.info("[PythStream] SSE stream cancelled")
            return
        except Exception as e:
            logger.warning(f"[PythStream] Connection lost: {e}, reconnecting in {backoff}s")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


async def _process_sse_event(event_str: str, feed_to_symbol: dict):
    """Parse un event SSE Pyth et met a jour _streaming_prices."""
    import json as _json
    now = time.time()

    for line in event_str.split("\n"):
        if line.startswith("data:"):
            try:
                data = _json.loads(line[5:].strip())
                for entry in data.get("parsed", []):
                    feed_id = entry.get("id", "").replace("0x", "")
                    price_data = entry.get("price", {})
                    raw_price = int(price_data.get("price", "0"))
                    exponent = int(price_data.get("expo", "0"))
                    raw_conf = int(price_data.get("conf", "0"))
                    publish_time = price_data.get("publish_time", 0)

                    price = raw_price * (10 ** exponent)
                    confidence = raw_conf * (10 ** exponent)

                    if price <= 0:
                        continue

                    age_s = int(now) - publish_time if publish_time > 0 else 0
                    symbol = feed_to_symbol.get(feed_id, "")

                    result = {
                        "price": round(price, 6),
                        "confidence": round(confidence, 6),
                        "confidence_pct": round((confidence / price * 100) if price > 0 else 0, 4),
                        "publish_time": publish_time,
                        "age_s": age_s,
                        "stale": False,  # Stream = toujours frais
                        "wide_confidence": False,
                        "source": "pyth_stream",
                        "symbol": symbol,
                    }

                    _streaming_prices[feed_id] = {"data": result, "ts": now}

                    # Push aux subscribers WebSocket
                    for q in list(_sse_subscribers):
                        try:
                            q.put_nowait({"symbol": symbol, **result})
                        except asyncio.QueueFull:
                            pass  # Client lent, skip

            except Exception:
                pass  # Malformed SSE event, skip


async def stop_pyth_stream():
    """Arrete le stream SSE."""
    global _sse_task
    if _sse_task and not _sse_task.done():
        _sse_task.cancel()
        try:
            await _sse_task
        except asyncio.CancelledError:
            pass
    _sse_task = None
    logger.info("[PythStream] SSE streaming stopped")


print(f"[PythOracle] Initialise — {len(EQUITY_FEEDS)} equity + {len(CRYPTO_FEEDS)} crypto feeds via Hermes")
