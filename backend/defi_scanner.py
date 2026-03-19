"""MAXIA DeFi Module — Yield Scanner via DeFiLlama

Scans APY across DeFi protocols. Free API, no key needed.
Exposes tools for AI agents to find the best yields.
"""
import asyncio, time
import httpx

DEFI_LLAMA_API = "https://yields.llama.fi"

_yield_cache: dict = {}
_yield_cache_ts: float = 0
_CACHE_TTL = 300  # 5 minutes


async def get_best_yields(asset: str = "USDC", chain: str = "", min_tvl: float = 100000, limit: int = 10) -> list:
    """Find the best yields for an asset across all DeFi protocols."""
    global _yield_cache, _yield_cache_ts

    if time.time() - _yield_cache_ts < _CACHE_TTL and _yield_cache:
        pools = _yield_cache
    else:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{DEFI_LLAMA_API}/pools")
                if resp.status_code == 200:
                    data = resp.json()
                    pools = data.get("data", [])
                    _yield_cache = pools
                    _yield_cache_ts = time.time()
                    print(f"[DeFi] Loaded {len(pools)} pools from DeFiLlama")
                else:
                    pools = []
        except Exception as e:
            print(f"[DeFi] DeFiLlama error: {e}")
            pools = []

    if not pools:
        return []

    # Filter by asset
    asset_upper = asset.upper()
    results = []
    for pool in pools:
        symbol = (pool.get("symbol") or "").upper()
        pool_chain = (pool.get("chain") or "").lower()
        tvl = pool.get("tvlUsd") or 0
        apy = pool.get("apy") or 0

        # Match asset
        if asset_upper not in symbol:
            continue
        # Filter chain
        if chain and chain.lower() not in pool_chain:
            continue
        # Min TVL
        if tvl < min_tvl:
            continue
        # Skip negative or zero APY
        if apy <= 0:
            continue

        results.append({
            "pool": pool.get("pool", ""),
            "project": pool.get("project", ""),
            "chain": pool.get("chain", ""),
            "symbol": pool.get("symbol", ""),
            "apy": round(apy, 2),
            "tvl_usd": round(tvl, 0),
            "apy_base": round(pool.get("apyBase") or 0, 2),
            "apy_reward": round(pool.get("apyReward") or 0, 2),
            "il_risk": pool.get("ilRisk", "no"),
            "stable_coin": pool.get("stablecoin", False),
        })

    # Sort by APY descending
    results.sort(key=lambda x: -x["apy"])
    return results[:limit]


async def get_protocol_stats(protocol: str = "aave") -> dict:
    """Get stats for a specific DeFi protocol."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"https://api.llama.fi/protocol/{protocol}")
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "name": data.get("name", protocol),
                    "tvl": data.get("currentChainTvls", {}),
                    "category": data.get("category", ""),
                    "chains": data.get("chains", []),
                    "url": data.get("url", ""),
                }
    except Exception as e:
        print(f"[DeFi] Protocol stats error: {e}")
    return {}


async def get_chain_tvl() -> list:
    """Get TVL by chain."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get("https://api.llama.fi/v2/chains")
            if resp.status_code == 200:
                data = resp.json()
                chains = []
                for c in data[:20]:
                    chains.append({
                        "name": c.get("name", ""),
                        "tvl": round(c.get("tvl", 0), 0),
                    })
                return chains
    except Exception:
        pass
    return []
