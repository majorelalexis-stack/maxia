"""MAXIA Sentiment Analysis — Social Listening

Analyses crypto sentiment from free public sources.
No API key needed for basic sentiment.
LunarCrush API key optional for premium data.
"""
import asyncio, time, re, os
import httpx

LUNARCRUSH_KEY = os.getenv("LUNARCRUSH_API_KEY", "")

_sentiment_cache: dict = {}
_cache_ts: float = 0
_CACHE_TTL = 300  # 5 minutes


async def get_sentiment(token: str = "BTC") -> dict:
    """Get sentiment for a crypto token from multiple free sources."""
    global _sentiment_cache, _cache_ts
    
    cache_key = token.upper()
    if cache_key in _sentiment_cache and time.time() - _cache_ts < _CACHE_TTL:
        return _sentiment_cache[cache_key]

    result = {
        "token": cache_key,
        "timestamp": int(time.time()),
        "sources": [],
        "overall_sentiment": "neutral",
        "score": 50,
    }

    # Source 1: CoinGecko community data (free)
    cg_data = await _coingecko_sentiment(cache_key)
    if cg_data:
        result["sources"].append(cg_data)

    # Source 2: Reddit mentions estimation
    reddit_data = await _reddit_sentiment(cache_key)
    if reddit_data:
        result["sources"].append(reddit_data)

    # Source 3: LunarCrush (if key available)
    if LUNARCRUSH_KEY:
        lunar_data = await _lunarcrush_sentiment(cache_key)
        if lunar_data:
            result["sources"].append(lunar_data)

    # Calculate overall score
    scores = [s.get("score", 50) for s in result["sources"] if s.get("score")]
    if scores:
        avg = sum(scores) / len(scores)
        result["score"] = round(avg, 1)
        if avg >= 70:
            result["overall_sentiment"] = "very_bullish"
        elif avg >= 60:
            result["overall_sentiment"] = "bullish"
        elif avg >= 40:
            result["overall_sentiment"] = "neutral"
        elif avg >= 30:
            result["overall_sentiment"] = "bearish"
        else:
            result["overall_sentiment"] = "very_bearish"

    _sentiment_cache[cache_key] = result
    _cache_ts = time.time()
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
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"https://api.coingecko.com/api/v3/coins/{cg_id}",
                params={"localization": "false", "tickers": "false",
                         "market_data": "true", "community_data": "true"},
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
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"https://api.coingecko.com/api/v3/coins/{cg_id}",
                params={"localization": "false", "tickers": "false",
                         "market_data": "false", "community_data": "true"},
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
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"https://lunarcrush.com/api4/public/coins/{token.lower()}/v1",
                headers={"Authorization": f"Bearer {LUNARCRUSH_KEY}"},
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
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get("https://api.coingecko.com/api/v3/search/trending")
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
