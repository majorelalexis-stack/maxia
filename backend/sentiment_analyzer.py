"""MAXIA Sentiment Analysis — Social Listening + Real Market Data

Analyses crypto sentiment from free public sources:
- CoinGecko price momentum (24h + 7d change)
- alternative.me Fear & Greed Index
- Reddit community activity
- LunarCrush (optional, requires API key)

Weighted scoring: 60% price momentum + 40% market sentiment (Fear & Greed)
"""
import asyncio, time, re, os
import httpx
from http_client import get_http_client

LUNARCRUSH_KEY = os.getenv("LUNARCRUSH_API_KEY", "")

_sentiment_cache: dict = {}
_cache_ts: dict = {}  # Per-token cache timestamps
_CACHE_TTL = 300  # 5 minutes


async def _get_fear_greed_value() -> int:
    """Get Fear & Greed value (0-100) from web3_services (cached)."""
    try:
        from web3_services import get_fear_greed_index
        fng = await get_fear_greed_index()
        return fng.get("value", 50)
    except Exception:
        return 50  # Neutral fallback


async def get_sentiment(token: str = "BTC") -> dict:
    """Get real sentiment for a crypto token — weighted price momentum + Fear & Greed."""
    cache_key = token.upper()
    now = time.time()
    if cache_key in _sentiment_cache and now - _cache_ts.get(cache_key, 0) < _CACHE_TTL:
        return _sentiment_cache[cache_key]

    result = {
        "token": cache_key,
        "timestamp": int(now),
        "sources": [],
        "overall_sentiment": "neutral",
        "score": 50,
        "method": "weighted_real_data",
    }

    # ── Source 1: CoinGecko price momentum (real data) ──
    cg_data = await _coingecko_sentiment(cache_key)
    price_change_24h = 0.0
    price_score = 50.0
    if cg_data:
        result["sources"].append(cg_data)
        price_change_24h = cg_data.get("price_change_24h", 0)
        price_score = cg_data.get("score", 50)

    # ── Source 2: Fear & Greed Index (real from alternative.me) ──
    fng_value = await _get_fear_greed_value()
    result["sources"].append({
        "source": "fear_greed_index",
        "score": fng_value,
        "value": fng_value,
        "api": "alternative.me",
    })

    # ── Source 3: Reddit community activity ──
    reddit_data = await _reddit_sentiment(cache_key)
    if reddit_data:
        result["sources"].append(reddit_data)

    # ── Source 4: LunarCrush (if key available) ──
    if LUNARCRUSH_KEY:
        lunar_data = await _lunarcrush_sentiment(cache_key)
        if lunar_data:
            result["sources"].append(lunar_data)

    # ══ Weighted score: 60% price momentum + 40% Fear & Greed ══
    # Normalize price_change_24h to 0-100 scale:
    #   -10% or worse -> 0, +10% or better -> 100, 0% -> 50
    price_normalized = max(0, min(100, 50 + (price_change_24h * 5)))

    weighted_score = (price_normalized * 60 + fng_value * 40) / 100
    result["score"] = round(weighted_score, 1)

    # Add breakdown for transparency
    result["score_breakdown"] = {
        "price_momentum_score": round(price_normalized, 1),
        "price_momentum_weight": "60%",
        "fear_greed_score": fng_value,
        "fear_greed_weight": "40%",
        "price_change_24h": round(price_change_24h, 2),
    }

    # Determine sentiment label
    if weighted_score >= 70:
        result["overall_sentiment"] = "very_bullish"
    elif weighted_score >= 60:
        result["overall_sentiment"] = "bullish"
    elif weighted_score >= 40:
        result["overall_sentiment"] = "neutral"
    elif weighted_score >= 30:
        result["overall_sentiment"] = "bearish"
    else:
        result["overall_sentiment"] = "very_bearish"

    _sentiment_cache[cache_key] = result
    _cache_ts[cache_key] = now
    print(f"[Sentiment] {cache_key}: score={result['score']}, "
          f"sentiment={result['overall_sentiment']}, "
          f"price_24h={price_change_24h:+.2f}%, fng={fng_value}")
    return result


# Token name mapping for CoinGecko
_CG_IDS = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
    "BONK": "bonk", "JUP": "jupiter-exchange-solana", "RAY": "raydium",
    "WIF": "dogwifcoin", "RENDER": "render-token", "HNT": "helium",
    "TRUMP": "official-trump", "PYTH": "pyth-network", "ORCA": "orca",
    "DOGE": "dogecoin", "ADA": "cardano", "XRP": "ripple",
    "AVAX": "avalanche-2", "LINK": "chainlink", "DOT": "polkadot",
}


async def _coingecko_sentiment(token: str) -> dict:
    """Get community metrics from CoinGecko (free)."""
    cg_id = _CG_IDS.get(token, token.lower())
    try:
        client = get_http_client()
        r = await client.get(
            f"https://api.coingecko.com/api/v3/coins/{cg_id}",
            params={"localization": "false", "tickers": "false",
                     "market_data": "true", "community_data": "true"},
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            community = data.get("community_data", {})
            market = data.get("market_data", {})

            # Calculate sentiment from price change
            change_24h = market.get("price_change_percentage_24h", 0) or 0
            change_7d = market.get("price_change_percentage_7d", 0) or 0

            # Score: 50 = neutral, >50 = bullish, <50 = bearish
            price_score = 50 + (change_24h * 2) + (change_7d * 0.5)
            price_score = max(0, min(100, price_score))

            return {
                "source": "coingecko",
                "score": round(price_score, 1),
                "price_change_24h": round(change_24h, 2),
                "price_change_7d": round(change_7d, 2),
                "twitter_followers": community.get("twitter_followers", 0),
                "reddit_subscribers": community.get("reddit_subscribers", 0),
                "market_cap_rank": data.get("market_cap_rank", 0),
            }
    except Exception as e:
        print(f"[Sentiment] CoinGecko error: {e}")
    return {}


async def _reddit_sentiment(token: str) -> dict:
    """Estimate Reddit sentiment from CoinGecko reddit data."""
    # Simple heuristic based on community size
    cg_id = _CG_IDS.get(token, token.lower())
    try:
        client = get_http_client()
        r = await client.get(
            f"https://api.coingecko.com/api/v3/coins/{cg_id}",
            params={"localization": "false", "tickers": "false",
                     "market_data": "false", "community_data": "true"},
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            community = data.get("community_data", {})
            reddit_subs = community.get("reddit_subscribers", 0) or 0
            reddit_active = community.get("reddit_accounts_active_48h", 0) or 0

            # Active ratio = engagement
            ratio = (reddit_active / reddit_subs * 100) if reddit_subs > 0 else 0
            # High activity = bullish interest
            score = 50 + min(ratio * 5, 25)

            return {
                "source": "reddit_estimate",
                "score": round(score, 1),
                "reddit_subscribers": reddit_subs,
                "reddit_active_48h": reddit_active,
                "activity_ratio": round(ratio, 2),
            }
    except Exception:
        pass
    return {}


async def _lunarcrush_sentiment(token: str) -> dict:
    """Get sentiment from LunarCrush (requires API key)."""
    if not LUNARCRUSH_KEY:
        return {}
    try:
        client = get_http_client()
        r = await client.get(
            f"https://lunarcrush.com/api4/public/coins/{token.lower()}/v1",
            headers={"Authorization": f"Bearer {LUNARCRUSH_KEY}"},
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json().get("data", {})
            return {
                "source": "lunarcrush",
                "score": data.get("galaxy_score", 50),
                "social_volume": data.get("social_volume", 0),
                "social_dominance": data.get("social_dominance", 0),
                "sentiment": data.get("sentiment", 0),
            }
    except Exception as e:
        print(f"[Sentiment] LunarCrush error: {e}")
    return {}


async def get_trending() -> list:
    """Get trending tokens from CoinGecko."""
    try:
        client = get_http_client()
        r = await client.get("https://api.coingecko.com/api/v3/search/trending", timeout=10)
        if r.status_code == 200:
            data = r.json()
            coins = data.get("coins", [])
            return [
                {
                    "name": c["item"]["name"],
                    "symbol": c["item"]["symbol"],
                    "market_cap_rank": c["item"].get("market_cap_rank", 0),
                    "score": c["item"].get("score", 0),
                }
                for c in coins[:10]
            ]
    except Exception:
        pass
    return []
