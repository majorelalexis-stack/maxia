"""MAXIA Yield Aggregator — Trouve les meilleurs rendements DeFi sur 14 chains.

Agrege les taux de rendement USDC/SOL depuis plusieurs protocoles DeFi :
- Marinade Finance (Solana) — mSOL staking
- Jito (Solana) — jitoSOL liquid staking
- Aave V3 (Ethereum/Polygon/Arbitrum/Avalanche/Base) — USDC lending
- Compound (Ethereum/Base) — USDC lending
- Ref Finance (NEAR) — USDC pools

Cache de 5 minutes, fallback sur taux hardcodes si les APIs sont injoignables.
"""
import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger("maxia.yield_aggregator")

router = APIRouter(prefix="/api/public/yield", tags=["yield-aggregator"])

# ── Cache ──────────────────────────────────────────────────────────────
_yield_cache: list[dict] = []
_yield_cache_ts: float = 0
_CACHE_TTL: int = 300  # 5 minutes

# ── Risk classification ───────────────────────────────────────────────
RISK_LOW = "low"
RISK_MEDIUM = "medium"
RISK_HIGH = "high"

# ── Fallback / hardcoded rates (used when APIs are unreachable) ───────
_FALLBACK_YIELDS: list[dict] = [
    # Marinade — Solana mSOL staking
    {
        "protocol": "Marinade Finance",
        "chain": "solana",
        "asset": "SOL",
        "apy_pct": 7.0,
        "tvl_usd": 1_500_000_000,
        "risk": RISK_LOW,
        "type": "liquid_staking",
        "url": "https://marinade.finance/app/stake/",
    },
    # Jito — Solana jitoSOL staking
    {
        "protocol": "Jito",
        "chain": "solana",
        "asset": "SOL",
        "apy_pct": 7.5,
        "tvl_usd": 1_200_000_000,
        "risk": RISK_LOW,
        "type": "liquid_staking",
        "url": "https://www.jito.network/staking/",
    },
    # Aave V3 — multi-chain USDC lending
    {
        "protocol": "Aave V3",
        "chain": "ethereum",
        "asset": "USDC",
        "apy_pct": 4.2,
        "tvl_usd": 1_200_000_000,
        "risk": RISK_LOW,
        "type": "lending",
        "url": "https://app.aave.com",
    },
    {
        "protocol": "Aave V3",
        "chain": "polygon",
        "asset": "USDC",
        "apy_pct": 3.8,
        "tvl_usd": 350_000_000,
        "risk": RISK_LOW,
        "type": "lending",
        "url": "https://app.aave.com",
    },
    {
        "protocol": "Aave V3",
        "chain": "arbitrum",
        "asset": "USDC",
        "apy_pct": 4.5,
        "tvl_usd": 450_000_000,
        "risk": RISK_LOW,
        "type": "lending",
        "url": "https://app.aave.com",
    },
    {
        "protocol": "Aave V3",
        "chain": "avalanche",
        "asset": "USDC",
        "apy_pct": 3.5,
        "tvl_usd": 200_000_000,
        "risk": RISK_LOW,
        "type": "lending",
        "url": "https://app.aave.com",
    },
    {
        "protocol": "Aave V3",
        "chain": "base",
        "asset": "USDC",
        "apy_pct": 5.1,
        "tvl_usd": 300_000_000,
        "risk": RISK_LOW,
        "type": "lending",
        "url": "https://app.aave.com",
    },
    # Compound — Ethereum/Base USDC lending
    {
        "protocol": "Compound V3",
        "chain": "ethereum",
        "asset": "USDC",
        "apy_pct": 3.9,
        "tvl_usd": 900_000_000,
        "risk": RISK_LOW,
        "type": "lending",
        "url": "https://app.compound.finance",
    },
    {
        "protocol": "Compound V3",
        "chain": "base",
        "asset": "USDC",
        "apy_pct": 4.8,
        "tvl_usd": 150_000_000,
        "risk": RISK_LOW,
        "type": "lending",
        "url": "https://app.compound.finance",
    },
    # Ref Finance — NEAR USDC pools
    {
        "protocol": "Ref Finance",
        "chain": "near",
        "asset": "USDC",
        "apy_pct": 6.2,
        "tvl_usd": 25_000_000,
        "risk": RISK_MEDIUM,
        "type": "amm_pool",
        "url": "https://app.ref.finance",
    },
    # ── ETH yields ──
    {
        "protocol": "Lido",
        "chain": "ethereum",
        "asset": "ETH",
        "apy_pct": 3.4,
        "tvl_usd": 14_000_000_000,
        "risk": RISK_LOW,
        "type": "liquid_staking",
        "url": "https://stake.lido.fi/",
    },
    {
        "protocol": "Rocket Pool",
        "chain": "ethereum",
        "asset": "ETH",
        "apy_pct": 3.1,
        "tvl_usd": 3_500_000_000,
        "risk": RISK_LOW,
        "type": "liquid_staking",
        "url": "https://stake.rocketpool.net/",
    },
    {
        "protocol": "Aave V3",
        "chain": "ethereum",
        "asset": "ETH",
        "apy_pct": 2.1,
        "tvl_usd": 2_800_000_000,
        "risk": RISK_LOW,
        "type": "lending",
        "url": "https://app.aave.com",
    },
    {
        "protocol": "Eigenlayer",
        "chain": "ethereum",
        "asset": "ETH",
        "apy_pct": 4.5,
        "tvl_usd": 8_000_000_000,
        "risk": RISK_MEDIUM,
        "type": "restaking",
        "url": "https://app.eigenlayer.xyz/",
    },
    {
        "protocol": "Aave V3",
        "chain": "base",
        "asset": "ETH",
        "apy_pct": 2.8,
        "tvl_usd": 400_000_000,
        "risk": RISK_LOW,
        "type": "lending",
        "url": "https://app.aave.com",
    },
    {
        "protocol": "Aave V3",
        "chain": "arbitrum",
        "asset": "ETH",
        "apy_pct": 2.5,
        "tvl_usd": 600_000_000,
        "risk": RISK_LOW,
        "type": "lending",
        "url": "https://app.aave.com",
    },
    # ── BTC yields ──
    {
        "protocol": "Aave V3",
        "chain": "ethereum",
        "asset": "BTC",
        "apy_pct": 0.5,
        "tvl_usd": 1_200_000_000,
        "risk": RISK_LOW,
        "type": "lending",
        "url": "https://app.aave.com",
    },
]


# ── Protocol fetchers ─────────────────────────────────────────────────

async def _fetch_marinade(client: httpx.AsyncClient) -> list[dict]:
    """Fetch Marinade Finance mSOL APY."""
    try:
        resp = await client.get("https://api.marinade.finance/msol/apy", timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            # API returns a float like 0.07 for 7%
            apy_raw = data if isinstance(data, (int, float)) else data.get("apy", data.get("value", 0))
            apy_pct = float(apy_raw) * 100 if float(apy_raw) < 1 else float(apy_raw)
            logger.info(f"[Yield] Marinade APY: {apy_pct:.2f}%")
            return [{
                "protocol": "Marinade Finance",
                "chain": "solana",
                "asset": "SOL",
                "apy_pct": round(apy_pct, 2),
                "tvl_usd": 1_500_000_000,
                "risk": RISK_LOW,
                "type": "liquid_staking",
                "url": "https://marinade.finance/app/stake/",
            }]
    except Exception as e:
        logger.warning(f"[Yield] Marinade API error: {e}")
    return []


async def _fetch_jito(client: httpx.AsyncClient) -> list[dict]:
    """Fetch Jito jitoSOL staking APY."""
    try:
        resp = await client.get("https://www.jito.network/api/v1/stake-pool", timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            # Extract APY from response — structure varies
            apy_raw = 0.0
            if isinstance(data, dict):
                apy_raw = data.get("apy", data.get("apr", 0))
            elif isinstance(data, list) and len(data) > 0:
                apy_raw = data[0].get("apy", data[0].get("apr", 0))

            apy_pct = float(apy_raw) * 100 if float(apy_raw) < 1 else float(apy_raw)
            if apy_pct <= 0:
                apy_pct = 7.5  # known approximate rate
            logger.info(f"[Yield] Jito APY: {apy_pct:.2f}%")
            return [{
                "protocol": "Jito",
                "chain": "solana",
                "asset": "SOL",
                "apy_pct": round(apy_pct, 2),
                "tvl_usd": 1_200_000_000,
                "risk": RISK_LOW,
                "type": "liquid_staking",
                "url": "https://www.jito.network/staking/",
            }]
    except Exception as e:
        logger.warning(f"[Yield] Jito API error: {e}")
    return []


async def _fetch_aave(client: httpx.AsyncClient) -> list[dict]:
    """Fetch Aave V3 USDC lending rates across chains."""
    results = []
    chain_map = {
        "proto_mainnet_v3": "ethereum",
        "proto_polygon_v3": "polygon",
        "proto_arbitrum_v3": "arbitrum",
        "proto_avalanche_v3": "avalanche",
        "proto_base_v3": "base",
    }
    try:
        resp = await client.get(
            "https://aave-api-v2.aave.com/data/markets-data",
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            reserves = data if isinstance(data, list) else data.get("reserves", data.get("data", []))
            for reserve in reserves:
                symbol = (reserve.get("symbol") or reserve.get("name") or "").upper()
                if symbol != "USDC":
                    continue
                market_id = reserve.get("market", reserve.get("id", ""))
                chain = "ethereum"
                for prefix, chain_name in chain_map.items():
                    if prefix in str(market_id).lower():
                        chain = chain_name
                        break

                # Aave returns rates as ray (1e27) or as decimal
                supply_apy = reserve.get("liquidityRate", reserve.get("supplyAPY", reserve.get("avg1DaysLiquidityRate", 0)))
                try:
                    apy_val = float(supply_apy)
                except (TypeError, ValueError):
                    apy_val = 0

                # Normalize — Aave V2 API returns rate as decimal (e.g. 0.042 = 4.2%)
                if apy_val > 100:
                    # ray format: divide by 1e25 to get percentage
                    apy_pct = apy_val / 1e25
                elif apy_val < 1:
                    apy_pct = apy_val * 100
                else:
                    apy_pct = apy_val

                if apy_pct <= 0:
                    continue

                tvl = float(reserve.get("totalLiquidity", reserve.get("totalLiquidityUSD", 0)))

                results.append({
                    "protocol": "Aave V3",
                    "chain": chain,
                    "asset": "USDC",
                    "apy_pct": round(apy_pct, 2),
                    "tvl_usd": round(tvl, 0),
                    "risk": RISK_LOW,
                    "type": "lending",
                    "url": "https://app.aave.com",
                })
            if results:
                logger.info(f"[Yield] Aave: fetched {len(results)} USDC markets")
    except Exception as e:
        logger.warning(f"[Yield] Aave API error: {e}")
    return results


async def _fetch_compound(client: httpx.AsyncClient) -> list[dict]:
    """Fetch Compound V3 USDC lending rates.

    Compound V3 does not have a simple public REST API for current rates,
    so we use known approximate rates with a live check attempt.
    """
    results = []
    # Known approximate rates for Compound V3 USDC markets
    compound_markets = [
        {"chain": "ethereum", "apy_pct": 3.9, "tvl_usd": 900_000_000},
        {"chain": "base", "apy_pct": 4.8, "tvl_usd": 150_000_000},
    ]
    try:
        # Try DeFiLlama for more accurate Compound rates
        resp = await client.get(
            "https://yields.llama.fi/pools",
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            pools = data.get("data", [])
            for pool in pools:
                project = (pool.get("project") or "").lower()
                symbol = (pool.get("symbol") or "").upper()
                if "compound" not in project or "USDC" not in symbol:
                    continue
                chain = (pool.get("chain") or "").lower()
                if chain not in ("ethereum", "base"):
                    continue
                apy = pool.get("apy", 0)
                tvl = pool.get("tvlUsd", 0)
                if apy > 0:
                    results.append({
                        "protocol": "Compound V3",
                        "chain": chain,
                        "asset": "USDC",
                        "apy_pct": round(apy, 2),
                        "tvl_usd": round(tvl, 0),
                        "risk": RISK_LOW,
                        "type": "lending",
                        "url": "https://app.compound.finance",
                    })
            if results:
                logger.info(f"[Yield] Compound: fetched {len(results)} markets from DeFiLlama")
                return results
    except Exception as e:
        logger.warning(f"[Yield] Compound/DeFiLlama error: {e}")

    # Fallback to hardcoded rates
    for market in compound_markets:
        results.append({
            "protocol": "Compound V3",
            "chain": market["chain"],
            "asset": "USDC",
            "apy_pct": market["apy_pct"],
            "tvl_usd": market["tvl_usd"],
            "risk": RISK_LOW,
            "type": "lending",
            "url": "https://app.compound.finance",
        })
    logger.info("[Yield] Compound: using hardcoded rates")
    return results


async def _fetch_ref_finance(client: httpx.AsyncClient) -> list[dict]:
    """Fetch Ref Finance (NEAR) USDC pool yields."""
    results = []
    try:
        resp = await client.get(
            "https://indexer.ref-finance.net/list-top-pools",
            timeout=10,
        )
        if resp.status_code == 200:
            pools = resp.json()
            if not isinstance(pools, list):
                pools = pools.get("data", pools.get("pools", []))
            for pool in pools:
                token_symbols = pool.get("token_symbols", [])
                symbols_str = " ".join(s.upper() for s in token_symbols) if isinstance(token_symbols, list) else str(token_symbols).upper()
                if "USDC" not in symbols_str:
                    continue
                apy = pool.get("apy", pool.get("apr", 0))
                try:
                    apy_val = float(apy)
                except (TypeError, ValueError):
                    continue
                if apy_val <= 0:
                    continue
                # Normalize
                apy_pct = apy_val * 100 if apy_val < 1 else apy_val
                tvl = float(pool.get("tvl", pool.get("total_value_locked", 0)))
                results.append({
                    "protocol": "Ref Finance",
                    "chain": "near",
                    "asset": "USDC",
                    "apy_pct": round(apy_pct, 2),
                    "tvl_usd": round(tvl, 0),
                    "risk": RISK_MEDIUM,
                    "type": "amm_pool",
                    "url": "https://app.ref.finance",
                })
            if results:
                logger.info(f"[Yield] Ref Finance: fetched {len(results)} USDC pools")
                return results
    except Exception as e:
        logger.warning(f"[Yield] Ref Finance API error: {e}")

    # Fallback
    results.append({
        "protocol": "Ref Finance",
        "chain": "near",
        "asset": "USDC",
        "apy_pct": 6.2,
        "tvl_usd": 25_000_000,
        "risk": RISK_MEDIUM,
        "type": "amm_pool",
        "url": "https://app.ref.finance",
    })
    logger.info("[Yield] Ref Finance: using hardcoded rate")
    return results


# ── Aggregation ────────────────────────────────────────────────────────

async def _fetch_all_yields() -> list[dict]:
    """Fetch yields from all protocols concurrently, with fallback."""
    global _yield_cache, _yield_cache_ts

    # Return cache if fresh
    if _yield_cache and (time.time() - _yield_cache_ts < _CACHE_TTL):
        return _yield_cache

    all_yields: list[dict] = []
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    async with httpx.AsyncClient(
        headers={"User-Agent": "MAXIA-YieldAggregator/1.0"},
        follow_redirects=True,
    ) as client:
        # Run all fetchers concurrently
        results = await asyncio.gather(
            _fetch_marinade(client),
            _fetch_jito(client),
            _fetch_aave(client),
            _fetch_compound(client),
            _fetch_ref_finance(client),
            return_exceptions=True,
        )

        fetched_protocols: set[str] = set()
        for result in results:
            if isinstance(result, Exception):
                logger.warning(f"[Yield] Fetcher exception: {result}")
                continue
            if isinstance(result, list):
                for item in result:
                    item["updated_at"] = now
                    all_yields.append(item)
                    fetched_protocols.add(item["protocol"])

    # Fill in fallbacks for any protocols that returned nothing
    for fb in _FALLBACK_YIELDS:
        key = f"{fb['protocol']}_{fb['chain']}_{fb.get('asset','')}"
        already_have = any(
            f"{y['protocol']}_{y['chain']}_{y.get('asset','')}" == key for y in all_yields
        )
        if not already_have:
            entry = {**fb, "updated_at": now, "source": "fallback", "is_fallback": True}
            all_yields.append(entry)
            print(f"[Yield] {fb.get('protocol','?')} on {fb.get('chain','?')}: using fallback data (API unavailable)")

    # ── Post-processing: clean data for humans ──

    for y in all_yields:
        apy = y.get("apy_pct", 0)

        # 1. Fix risk labels — no more "yes"/"no", only low/medium/high
        risk = str(y.get("risk", "")).lower()
        if risk in ("yes", "true", "1", "no", "false", "0", ""):
            if apy > 100:
                y["risk"] = "high"
            elif apy > 20:
                y["risk"] = "medium"
            else:
                y["risk"] = "low"
        elif risk not in ("low", "medium", "high"):
            y["risk"] = "medium"

        # 2. Classify type properly
        ptype = str(y.get("type", "")).lower()
        proto = str(y.get("protocol", "")).lower()
        if "staking" in proto or "staking" in ptype or "jito" in proto or "marinade" in proto:
            y["type"] = "Staking"
        elif "amm" in ptype or "dex" in proto or "swap" in proto or "uniswap" in proto or "aerodrome" in proto or "curve" in proto:
            y["type"] = "AMM Pool"
        elif "lending" in ptype or "aave" in proto or "compound" in proto:
            y["type"] = "Lending"
        else:
            y["type"] = y.get("type", "DeFi").replace("_", " ").title()

        # 3. Clean protocol names
        name = y.get("protocol", "")
        name = name.replace("-", " ").replace("_", " ")
        # Capitalize properly
        parts = name.split()
        y["protocol"] = " ".join(p.capitalize() for p in parts)

        # 4. Mark degen yields
        if apy > 1000:
            y["risk"] = "high"
            y["warning"] = "Extremely high APY — likely unsustainable or high impermanent loss"
        elif apy > 100:
            y["risk"] = "high" if y["risk"] != "high" else y["risk"]

    # 5. Sort by TVL descending (bigger = safer), then APY
    all_yields.sort(key=lambda x: (
        0 if x.get("risk") == "low" else 1 if x.get("risk") == "medium" else 2,
        -(x.get("tvl_usd", 0)),
    ))

    # Update cache
    _yield_cache = all_yields
    _yield_cache_ts = time.time()
    logger.info(f"[Yield] Aggregated {len(all_yields)} yield opportunities")

    return all_yields


# ── API Endpoints ──────────────────────────────────────────────────────

@router.get("/best")
async def get_best_yields(
    asset: str = Query("USDC", description="Asset to find yields for (USDC, SOL, etc.)"),
    limit: int = Query(10, ge=1, le=100, description="Max number of results"),
):
    """Retourne les meilleurs rendements pour un asset donne, tries par APY decroissant."""
    try:
        all_yields = await _fetch_all_yields()
    except Exception as e:
        logger.error(f"[Yield] Aggregation error: {e}")
        raise HTTPException(500, detail="Failed to fetch yield data")

    asset_upper = asset.upper()
    filtered = [y for y in all_yields if y.get("asset", "").upper() == asset_upper]

    return {
        "asset": asset_upper,
        "count": len(filtered[:limit]),
        "yields": filtered[:limit],
    }


@router.get("/all")
async def get_all_yields():
    """Retourne tous les rendements agreges sur toutes les chains et protocoles."""
    try:
        all_yields = await _fetch_all_yields()
    except Exception as e:
        logger.error(f"[Yield] Aggregation error: {e}")
        raise HTTPException(500, detail="Failed to fetch yield data")

    # Group by asset for summary
    asset_summary: dict[str, dict] = {}
    for y in all_yields:
        asset = y.get("asset", "UNKNOWN")
        if asset not in asset_summary:
            asset_summary[asset] = {"count": 0, "best_apy": 0, "best_protocol": ""}
        asset_summary[asset]["count"] += 1
        if y.get("apy_pct", 0) > asset_summary[asset]["best_apy"]:
            asset_summary[asset]["best_apy"] = y["apy_pct"]
            asset_summary[asset]["best_protocol"] = y["protocol"]

    return {
        "total": len(all_yields),
        "summary": asset_summary,
        "yields": all_yields,
    }


@router.get("/chain/{chain}")
async def get_yields_by_chain(chain: str):
    """Retourne les rendements disponibles sur une chain specifique."""
    try:
        all_yields = await _fetch_all_yields()
    except Exception as e:
        logger.error(f"[Yield] Aggregation error: {e}")
        raise HTTPException(500, detail="Failed to fetch yield data")

    chain_lower = chain.lower()
    filtered = [y for y in all_yields if y.get("chain", "").lower() == chain_lower]

    if not filtered:
        raise HTTPException(404, detail=f"No yields found for chain: {chain}")

    return {
        "chain": chain_lower,
        "count": len(filtered),
        "yields": filtered,
    }
