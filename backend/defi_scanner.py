"""MAXIA DeFi Module — Yield Scanner via DeFiLlama + Native Staking (Marinade, Jito, Lido)

Scans APY across DeFi protocols. Free API, no key needed.
Exposes tools for AI agents to find the best yields.
"""
import logging
import asyncio, time
import httpx
from http_client import get_http_client

logger = logging.getLogger(__name__)

DEFI_LLAMA_API = "https://yields.llama.fi"

_yield_cache: list = []
_yield_cache_ts: float = 0
_CACHE_TTL = 300  # 5 minutes

# ── Native staking yields (updated periodically) ──
_staking_cache: list = []
_staking_cache_ts: float = 0
_STAKING_TTL = 600  # 10 min

# ── Cache des yields DeFiLlama (fallback quand les APIs natives echouent) ──
_defillama_cache: dict = {}
_defillama_cache_ts: float = 0


async def _fetch_defillama_yields() -> dict:
    """Fetch les yields depuis DeFiLlama comme fallback universel."""
    global _defillama_cache, _defillama_cache_ts
    if _defillama_cache and time.time() - _defillama_cache_ts < 600:  # 10 min cache
        return _defillama_cache
    try:
        client = get_http_client()
        resp = await client.get("https://yields.llama.fi/pools", timeout=15)
        resp.raise_for_status()
        pools = resp.json().get("data", [])
        # Index par protocol+symbol pour lookup rapide
        result = {}
        for p in pools:
            key = f"{p.get('project', '').lower()}_{p.get('symbol', '').lower()}"
            if key not in result or p.get("apy", 0) > result[key].get("apy", 0):
                result[key] = {
                    "protocol": p.get("project", ""),
                    "symbol": p.get("symbol", ""),
                    "apy": round(p.get("apy", 0), 2),
                    "tvl": round(p.get("tvlUsd", 0), 0),
                    "chain": p.get("chain", ""),
                }
        _defillama_cache = result
        _defillama_cache_ts = time.time()
        logger.info(f"[DeFi] Cache DeFiLlama: {len(result)} pools indexes")
        return result
    except Exception as e:
        logger.error(f"[DeFi] DeFiLlama fallback fetch error: {e}")
        return _defillama_cache or {}


async def _fetch_native_staking() -> list:
    """Fetch native staking yields from Marinade, Jito, Lido APIs."""
    global _staking_cache, _staking_cache_ts
    if time.time() - _staking_cache_ts < _STAKING_TTL and _staking_cache:
        return _staking_cache

    yields = []
    try:
        client = get_http_client()
        # Marinade Finance (mSOL) — Solana native staking
        try:
            resp = await client.get("https://api.marinade.finance/tlv", timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                apy = data.get("avg_staking_apy", 0.07) * 100  # decimal to %
                tvl = data.get("total_sol", 0) * 140  # approx SOL price
                yields.append({
                    "pool": "marinade-msol", "project": "marinade-finance",
                    "chain": "Solana", "symbol": "SOL-mSOL", "type": "staking",
                    "apy": round(apy, 2), "tvl_usd": round(tvl, 0),
                    "apy_base": round(apy, 2), "apy_reward": 0,
                    "il_risk": "no", "stable_coin": False,
                    "risk": "LOW", "risk_score": 10,
                    "description": "Liquid staking SOL via Marinade. Earn staking rewards while keeping SOL liquid as mSOL.",
                    "url": "https://marinade.finance/app/stake/",
                })
        except Exception:
            pass

        # Jito (JitoSOL) — MEV-boosted staking
        try:
            resp = await client.get("https://kobe.mainnet.jito.network/api/v1/stake_pool", timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                apy = float(data.get("apy", 0.08)) * 100
                tvl = float(data.get("total_lamports", 0)) / 1e9 * 140
                yields.append({
                    "pool": "jito-jitosol", "project": "jito",
                    "chain": "Solana", "symbol": "SOL-JitoSOL", "type": "staking",
                    "apy": round(apy, 2), "tvl_usd": round(tvl, 0),
                    "apy_base": round(apy - 1, 2), "apy_reward": 1.0,
                    "il_risk": "no", "stable_coin": False,
                    "risk": "LOW", "risk_score": 10,
                    "description": "MEV-boosted liquid staking. Higher APY than regular staking thanks to MEV tips.",
                    "url": "https://www.jito.network/staking/",
                })
        except Exception:
            pass

        # Lido (stETH) — Ethereum staking
        try:
            resp = await client.get("https://eth-api.lido.fi/v1/protocol/steth/apr/sma", timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                apy = data.get("data", {}).get("smaApr", 3.5)
                yields.append({
                    "pool": "lido-steth", "project": "lido",
                    "chain": "Ethereum", "symbol": "ETH-stETH", "type": "staking",
                    "apy": round(float(apy), 2), "tvl_usd": 32_000_000_000,
                    "apy_base": round(float(apy), 2), "apy_reward": 0,
                    "il_risk": "no", "stable_coin": False,
                    "risk": "LOW", "risk_score": 5,
                    "description": "Liquid staking ETH via Lido. Largest ETH staking protocol ($32B TVL).",
                    "url": "https://stake.lido.fi/",
                })
        except Exception:
            pass

    except Exception as e:
        logger.error(f"[DeFi] Staking fetch error: {e}")

    # Fallback via DeFiLlama si les APIs natives ont echoue
    llama = await _fetch_defillama_yields()

    if not any(y["project"] == "marinade-finance" for y in yields):
        marinade_data = llama.get("marinade-finance_msol", {})
        marinade_apy = marinade_data.get("apy", 0)
        marinade_tvl = marinade_data.get("tvl", 0)
        yields.append({
            "pool": "marinade-msol", "project": "marinade-finance",
            "chain": "Solana", "symbol": "SOL-mSOL", "type": "staking",
            "apy": marinade_apy if marinade_apy > 0 else 0,
            "tvl_usd": marinade_tvl if marinade_tvl > 0 else 0,
            "apy_base": marinade_apy if marinade_apy > 0 else 0, "apy_reward": 0,
            "il_risk": "no", "stable_coin": False,
            "risk": "LOW", "risk_score": 10,
            "description": "Liquid staking SOL via Marinade.",
            "url": "https://marinade.finance/app/stake/",
            "source": "defillama" if marinade_apy > 0 else "unavailable",
        })
    if not any(y["project"] == "jito" for y in yields):
        jito_data = llama.get("jito_jitosol", {})
        jito_apy = jito_data.get("apy", 0)
        jito_tvl = jito_data.get("tvl", 0)
        yields.append({
            "pool": "jito-jitosol", "project": "jito",
            "chain": "Solana", "symbol": "SOL-JitoSOL", "type": "staking",
            "apy": jito_apy if jito_apy > 0 else 0,
            "tvl_usd": jito_tvl if jito_tvl > 0 else 0,
            "apy_base": round(jito_apy * 0.86, 2) if jito_apy > 0 else 0,
            "apy_reward": round(jito_apy * 0.14, 2) if jito_apy > 0 else 0,
            "il_risk": "no", "stable_coin": False,
            "risk": "LOW", "risk_score": 10,
            "description": "MEV-boosted liquid staking SOL.",
            "url": "https://www.jito.network/staking/",
            "source": "defillama" if jito_apy > 0 else "unavailable",
        })
    if not any(y["project"] == "lido" for y in yields):
        lido_data = llama.get("lido_steth", {})
        lido_apy = lido_data.get("apy", 0)
        lido_tvl = lido_data.get("tvl", 0)
        yields.append({
            "pool": "lido-steth", "project": "lido",
            "chain": "Ethereum", "symbol": "ETH-stETH", "type": "staking",
            "apy": lido_apy if lido_apy > 0 else 0,
            "tvl_usd": lido_tvl if lido_tvl > 0 else 0,
            "apy_base": lido_apy if lido_apy > 0 else 0, "apy_reward": 0,
            "il_risk": "no", "stable_coin": False,
            "risk": "LOW", "risk_score": 5,
            "description": "Liquid staking ETH via Lido.",
            "url": "https://stake.lido.fi/",
            "source": "defillama" if lido_apy > 0 else "unavailable",
        })

    _staking_cache = yields
    _staking_cache_ts = time.time()
    return yields


def _compute_risk(pool: dict) -> tuple:
    """Compute risk level and score for a DeFi pool."""
    apy = pool.get("apy", 0) or 0
    tvl = pool.get("tvlUsd", 0) or 0
    il = pool.get("ilRisk", "no")
    stable = pool.get("stablecoin", False)
    project = (pool.get("project") or "").lower()

    score = 0
    # APY risk: very high APY = higher risk
    if apy > 100:
        score += 40
    elif apy > 30:
        score += 20
    elif apy > 15:
        score += 10

    # TVL risk: low TVL = higher risk
    if tvl < 100_000:
        score += 30
    elif tvl < 1_000_000:
        score += 15
    elif tvl < 10_000_000:
        score += 5

    # IL risk
    if il == "yes":
        score += 15

    # Stablecoin = lower risk
    if stable:
        score -= 10

    # Known blue-chip protocols = lower risk
    blue_chips = ["aave", "compound", "lido", "marinade", "jito", "maker", "curve", "uniswap", "raydium", "orca"]
    if project in blue_chips:
        score -= 15

    score = max(0, min(100, score))
    if score < 25:
        return "LOW", score
    elif score < 55:
        return "MEDIUM", score
    return "HIGH", score


# ── Protocol URL mapping ──
_PROTOCOL_URLS = {
    # Lending
    "aave-v3": "https://app.aave.com/", "aave-v2": "https://app.aave.com/",
    "compound-v3": "https://app.compound.finance/", "compound-v2": "https://app.compound.finance/",
    "kamino": "https://app.kamino.finance/", "kamino-lend": "https://app.kamino.finance/",
    "marginfi": "https://app.marginfi.com/", "solend": "https://solend.fi/",
    "morpho-blue": "https://app.morpho.org/", "morpho-aave": "https://app.morpho.org/",
    "spark": "https://app.spark.fi/", "maker": "https://app.sky.money/",
    "benqi-lending": "https://app.benqi.fi/", "benqi-staked-avax": "https://app.benqi.fi/",
    "venus": "https://app.venus.io/",
    "navi-lending": "https://app.naviprotocol.io/", "scallop-lend": "https://app.scallop.io/",
    # Staking
    "marinade-finance": "https://marinade.finance/app/stake/",
    "jito": "https://www.jito.network/staking/",
    "lido": "https://stake.lido.fi/?ref=maxia", "rocket-pool": "https://stake.rocketpool.net/",
    "sanctum": "https://app.sanctum.so/", "eigenlayer": "https://app.eigenlayer.xyz/",
    "eigenpie": "https://app.eigenpie.com/", "mantle-staked-eth": "https://www.mantle.xyz/",
    # DEX / LP
    "raydium": "https://raydium.io/liquidity/", "orca": "https://www.orca.so/pools",
    "meteora": "https://app.meteora.ag/",
    "jupiter": "https://jup.ag/?referrer=maxia",
    "curve-dex": "https://curve.fi/", "curve": "https://curve.fi/",
    "uniswap-v3": "https://app.uniswap.org/", "uniswap-v2": "https://app.uniswap.org/",
    "pancakeswap-amm-v3": "https://pancakeswap.finance/liquidity",
    "sushiswap": "https://www.sushi.com/", "balancer-v2": "https://app.balancer.fi/",
    "aerodrome-v2": "https://aerodrome.finance/liquidity", "aerodrome-slipstream": "https://aerodrome.finance/liquidity",
    "velodrome-v2": "https://app.velodrome.finance/",
    "cetus": "https://app.cetus.zone/", "trader-joe": "https://traderjoexyz.com/",
    "ref-finance": "https://app.ref.finance/",
    # Perps / Yield
    "gmx": "https://app.gmx.io/", "gmx-v2": "https://app.gmx.io/",
    "drift-protocol": "https://app.drift.trade/", "drift-staked-sol": "https://app.drift.trade/",
    "yearn-finance": "https://yearn.fi/", "convex-finance": "https://www.convexfinance.com/",
    "pendle": "https://app.pendle.finance/", "stargate": "https://stargate.finance/",
    # 1inch referral (0.1% to referrer)
    "1inch": "https://app.1inch.io/#/1/simple/swap?referrer=maxia",
}


def _get_protocol_url(project: str, pool_id: str = "") -> str:
    """Get deposit URL for a protocol. Falls back to DeFiLlama pool page."""
    key = (project or "").lower().strip()
    if key in _PROTOCOL_URLS:
        return _PROTOCOL_URLS[key]
    # Try partial match
    for k, v in _PROTOCOL_URLS.items():
        if k in key or key in k:
            return v
    # Fallback: DeFiLlama pool page (always works)
    if pool_id:
        return f"https://defillama.com/yields/pool/{pool_id}"
    return f"https://defillama.com/yields?project={project}"


def _detect_type(pool: dict) -> str:
    """Detect yield type from pool metadata."""
    project = (pool.get("project") or "").lower()
    symbol = (pool.get("symbol") or "").upper()

    # Staking
    staking_keywords = ["staking", "stake", "lido", "marinade", "jito", "rocket-pool", "cbeth"]
    if any(k in project for k in staking_keywords) or "STAKED" in symbol or "stETH" in symbol:
        return "staking"

    # Lending
    lending_keywords = ["aave", "compound", "benqi", "kamino", "marginfi", "solend", "morpho", "spark"]
    if any(k in project for k in lending_keywords):
        return "lending"

    # LP / DEX
    lp_keywords = ["uniswap", "raydium", "orca", "curve", "sushiswap", "pancakeswap", "meteora", "balancer"]
    if any(k in project for k in lp_keywords):
        return "lp"

    # Farming
    if (pool.get("apyReward") or 0) > (pool.get("apyBase") or 0):
        return "farming"

    return "other"


async def get_best_yields(asset: str = "USDC", chain: str = "", min_tvl: float = 100000,
                          limit: int = 10, yield_type: str = "") -> list:
    """Find the best yields for an asset across all DeFi protocols + native staking.
    yield_type: staking, lending, lp, farming, or empty for all.
    """
    global _yield_cache, _yield_cache_ts

    if time.time() - _yield_cache_ts < _CACHE_TTL and _yield_cache:
        pools = _yield_cache
    else:
        try:
            client = get_http_client()
            resp = await client.get(f"{DEFI_LLAMA_API}/pools", timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                pools = data.get("data", [])
                _yield_cache = pools
                _yield_cache_ts = time.time()
                logger.info(f"[DeFi] Loaded {len(pools)} pools from DeFiLlama")
            else:
                pools = []
        except Exception as e:
            logger.error(f"[DeFi] DeFiLlama error: {e}")
            pools = []

    # Also fetch native staking yields
    staking_yields = await _fetch_native_staking()

    if not pools and not staking_yields:
        return []

    asset_upper = asset.upper()
    results = []

    # Process DeFiLlama pools
    for pool in pools:
        symbol = (pool.get("symbol") or "").upper()
        pool_chain = (pool.get("chain") or "").lower()
        tvl = pool.get("tvlUsd") or 0
        apy = pool.get("apy") or 0

        if asset_upper and asset_upper != "ALL" and asset_upper not in symbol:
            continue
        if chain and chain.lower() not in pool_chain:
            continue
        if tvl < min_tvl:
            continue
        if apy <= 0:
            continue

        risk_level, risk_score = _compute_risk(pool)
        pool_type = _detect_type(pool)

        if yield_type and pool_type != yield_type:
            continue

        pool_id = pool.get("pool", "")
        project_name = pool.get("project", "")
        results.append({
            "pool": pool_id,
            "project": project_name,
            "chain": pool.get("chain", ""),
            "symbol": pool.get("symbol", ""),
            "type": pool_type,
            "apy": round(apy, 2),
            "tvl_usd": round(tvl, 0),
            "apy_base": round(pool.get("apyBase") or 0, 2),
            "apy_reward": round(pool.get("apyReward") or 0, 2),
            "il_risk": pool.get("ilRisk", "no"),
            "stable_coin": pool.get("stablecoin", False),
            "risk": risk_level,
            "risk_score": risk_score,
            "url": _get_protocol_url(project_name, pool_id),
        })

    # Add native staking yields
    for sy in staking_yields:
        sym_upper = (sy.get("symbol") or "").upper()
        sy_chain = (sy.get("chain") or "").lower()
        if asset_upper and asset_upper != "ALL" and asset_upper not in sym_upper:
            continue
        if chain and chain.lower() not in sy_chain:
            continue
        if yield_type and sy.get("type") != yield_type:
            continue
        results.append(sy)

    results.sort(key=lambda x: -x["apy"])
    return results[:limit]


async def get_protocol_stats(protocol: str = "aave") -> dict:
    """Get stats for a specific DeFi protocol."""
    try:
        client = get_http_client()
        resp = await client.get(f"https://api.llama.fi/protocol/{protocol}", timeout=10)
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
        logger.error(f"[DeFi] Protocol stats error: {e}")
    return {}


async def get_chain_tvl() -> list:
    """Get TVL by chain."""
    try:
        client = get_http_client()
        resp = await client.get("https://api.llama.fi/v2/chains", timeout=10)
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
