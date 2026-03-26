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
import asyncio
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
MAX_STALENESS_STOCK_S = 30     # Actions: max 30s (marches volatils, bots rapides)
MAX_STALENESS_CRYPTO_S = 120   # Crypto: max 120s (24/7 mais moins critique)
# Confidence interval: si > CONFIDENCE_WARN_PCT du prix, le prix est peu fiable
CONFIDENCE_WARN_PCT = 2.0      # 2% = spread oracle trop large, flagge comme unreliable
# Circuit breaker: si N lectures consecutives sont stale, pause les trades
STALE_CIRCUIT_THRESHOLD = 5    # 5 stales d'affilee -> source consideree down
_consecutive_stale: dict[str, int] = {}  # {feed_id: count}

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
_CACHE_TTL = 10  # secondes — Pyth publie toutes les ~400ms, 10s suffit
_CACHE_MAX = 100  # Limite max d'entrees en cache pour eviter fuite memoire

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


# ── Fonctions principales ──

async def get_pyth_price(feed_id: str) -> dict:
    """Recupere le prix d'un feed Pyth via Hermes API.

    Args:
        feed_id: ID du feed Pyth (hex, sans 0x)

    Returns:
        {"price": float, "confidence": float, "publish_time": int, "source": "pyth"}
        ou {"error": "..."} en cas d'echec
    """
    # Verifier le cache d'abord
    now = time.time()
    cached = _price_cache.get(feed_id)
    if cached and now - cached["ts"] < _CACHE_TTL:
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

        # ── Staleness check ──
        age_s = int(now) - publish_time if publish_time > 0 else 0
        is_equity = feed_id in EQUITY_FEEDS.values()
        max_staleness = MAX_STALENESS_STOCK_S if is_equity else MAX_STALENESS_CRYPTO_S
        is_stale = age_s > max_staleness if publish_time > 0 else False

        # ── Confidence interval check ──
        confidence_pct = (confidence / price * 100) if price > 0 else 0
        wide_confidence = confidence_pct > CONFIDENCE_WARN_PCT

        # ── Circuit breaker sur lectures stale consecutives ──
        if is_stale:
            _consecutive_stale[feed_id] = _consecutive_stale.get(feed_id, 0) + 1
            if _consecutive_stale[feed_id] >= STALE_CIRCUIT_THRESHOLD:
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
        return result

    except httpx.TimeoutException:
        return {"error": "Pyth Hermes timeout", "source": "pyth"}
    except Exception as e:
        return {"error": f"Pyth error: {str(e)[:100]}", "source": "pyth"}


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
            params = [("ids[]", f"0x{fid}") for fid in pyth_symbols.values()]
            resp = await client.get(
                f"{HERMES_URL}/v2/updates/price/latest",
                params=params,
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
        "cache_ttl_s": _CACHE_TTL,
        "hermes_url": HERMES_URL,
    }


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
                "max_stock_s": MAX_STALENESS_STOCK_S,
                "max_crypto_s": MAX_STALENESS_CRYPTO_S,
                "confidence_warn_pct": CONFIDENCE_WARN_PCT,
                "circuit_threshold": STALE_CIRCUIT_THRESHOLD,
            },
            "stale_feeds_count": len(stale_feeds),
            "circuit_open_feeds": len(circuit_open),
        }
    except Exception as e:
        return {"status": "error", "error": str(e)[:100]}


# ── Item 7: Alerte Telegram quand oracle stale pendant market hours ──
# Rate limit: max 1 alerte par symbole par heure
_oracle_alert_last: dict[str, float] = {}  # {symbol: timestamp dernier alerte}
_ORACLE_ALERT_COOLDOWN = 3600  # 1 heure entre chaque alerte par symbole
_ORACLE_STALE_ALERT_THRESHOLD = 300  # 5 minutes = feed considere stale pour alerte


async def check_oracle_health_alert():
    """Verifie la staleness de tous les feeds equity Pyth.

    Si un feed est stale > 5 min, envoie une alerte Telegram via alert_error.
    Rate-limited: max 1 alerte par symbole par heure.
    Appele depuis scheduler._v13_background_loop() toutes les 5 min.
    """
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


print(f"[PythOracle] Initialise — {len(EQUITY_FEEDS)} equity + {len(CRYPTO_FEEDS)} crypto feeds via Hermes")
