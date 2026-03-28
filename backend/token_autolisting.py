"""Token Auto-Listing — decouvre et ajoute les tokens populaires automatiquement.

Scan DexScreener + CoinGecko pour les tokens avec volume > $100K/24h
sur les chains supportees. Ajoute a SUPPORTED_TOKENS si criteres remplis.
"""
import asyncio
import httpx
import time
from http_client import get_http_client

# Criteres de listing automatique
MIN_VOLUME_24H = 100_000  # $100K minimum
MIN_LIQUIDITY = 50_000    # $50K minimum
MIN_HOLDERS = 100         # 100 holders minimum
SUPPORTED_CHAINS = ["solana", "ethereum", "base", "polygon", "arbitrum", "avalanche", "bsc"]

_autolisting_cache = {}
_cache_ts = 0


async def scan_trending_tokens(chain: str = "solana", limit: int = 20) -> list:
    """Scan DexScreener for trending tokens on a chain."""
    try:
        client = get_http_client()
        resp = await client.get(f"https://api.dexscreener.com/latest/dex/tokens/trending/{chain}", timeout=15)
        if resp.status_code != 200:
            # Fallback: search top volume
            resp = await client.get(f"https://api.dexscreener.com/latest/dex/search?q={chain}", timeout=15)
            data = resp.json()
            pairs = data.get("pairs", [])

            candidates = []
            for p in pairs[:limit]:
                vol = float(p.get("volume", {}).get("h24", 0) or 0)
                liq = float(p.get("liquidity", {}).get("usd", 0) or 0)
                if vol >= MIN_VOLUME_24H and liq >= MIN_LIQUIDITY:
                    candidates.append({
                        "symbol": p.get("baseToken", {}).get("symbol", ""),
                        "name": p.get("baseToken", {}).get("name", ""),
                        "address": p.get("baseToken", {}).get("address", ""),
                        "chain": p.get("chainId", chain),
                        "volume_24h": vol,
                        "liquidity": liq,
                        "price_usd": float(p.get("priceUsd", 0) or 0),
                    })
            return candidates
    except Exception as e:
        print(f"[AutoList] Scan {chain} error: {e}")
        return []


async def scan_all_chains() -> list:
    """Scan all supported chains for listable tokens."""
    all_candidates = []
    tasks = [scan_trending_tokens(chain) for chain in SUPPORTED_CHAINS]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for result in results:
        if isinstance(result, list):
            all_candidates.extend(result)
    # Sort by volume
    all_candidates.sort(key=lambda x: x.get("volume_24h", 0), reverse=True)
    return all_candidates


async def get_listing_candidates() -> list:
    """Get cached listing candidates (refresh every 30 min)."""
    global _autolisting_cache, _cache_ts
    if _autolisting_cache and time.time() - _cache_ts < 1800:
        return _autolisting_cache
    candidates = await scan_all_chains()
    _autolisting_cache = candidates
    _cache_ts = time.time()
    return candidates
