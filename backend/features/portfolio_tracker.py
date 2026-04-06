"""MAXIA V12 — Multi-Chain Portfolio Tracker (ONE-33).

Aggregates balances across 14 chains with USD conversion via price oracle.
Endpoints:
  GET /api/portfolio/{address}          — full portfolio with USD values
  GET /api/portfolio/{address}/chains   — per-chain breakdown
"""
import logging
import asyncio
import time
import re
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger("maxia.portfolio")
router = APIRouter(prefix="/api/portfolio", tags=["portfolio-tracker"])

# ── Address format validators ──
_SOLANA_RE = re.compile(r'^[1-9A-HJ-NP-Za-km-z]{32,44}$')
_EVM_RE = re.compile(r'^0x[0-9a-fA-F]{40}$')
_XRPL_RE = re.compile(r'^r[1-9A-HJ-NP-Za-km-z]{24,34}$')
_TON_RE = re.compile(r'^(EQ|UQ)[A-Za-z0-9_-]{46,48}$')
_SUI_RE = re.compile(r'^0x[0-9a-fA-F]{64}$')
_TRON_RE = re.compile(r'^T[1-9A-HJ-NP-Za-km-z]{33}$')
_NEAR_RE = re.compile(r'^[a-z0-9._-]{2,64}(\.near|\.testnet)$|^[0-9a-f]{64}$')
_APTOS_RE = re.compile(r'^0x[0-9a-fA-F]{64}$')
_BTC_RE = re.compile(r'^(1|3|bc1)[a-zA-HJ-NP-Z0-9]{25,62}$')

# Cache: address -> {data, ts}
_portfolio_cache: dict[str, dict] = {}
CACHE_TTL = 30  # 30 seconds


def _detect_chains(address: str) -> list[str]:
    """Detect which chains an address format is compatible with."""
    chains = []
    if _SOLANA_RE.match(address):
        chains.append("solana")
    if _EVM_RE.match(address):
        chains.extend(["ethereum", "base", "polygon", "arbitrum", "avalanche", "bnb", "sei"])
    if _XRPL_RE.match(address):
        chains.append("xrpl")
    if _TON_RE.match(address):
        chains.append("ton")
    if _SUI_RE.match(address):
        chains.append("sui")
    if _TRON_RE.match(address):
        chains.append("tron")
    if _NEAR_RE.match(address):
        chains.append("near")
    if _APTOS_RE.match(address):
        chains.append("aptos")
    if _BTC_RE.match(address):
        chains.append("bitcoin")
    return chains


async def _fetch_solana_balance(address: str) -> dict:
    """Fetch SOL + USDC balance on Solana."""
    try:
        from blockchain.solana_tx import get_sol_balance, get_usdc_balance
        sol, usdc = await asyncio.gather(
            get_sol_balance(address),
            get_usdc_balance(address),
            return_exceptions=True,
        )
        sol_bal = sol if isinstance(sol, (int, float)) else 0
        usdc_bal = usdc if isinstance(usdc, (int, float)) else 0
        return {"chain": "solana", "native": {"symbol": "SOL", "balance": sol_bal},
                "stablecoins": [{"symbol": "USDC", "balance": usdc_bal}]}
    except Exception as e:
        logger.debug("Solana balance error for %s: %s", address, e)
        return {"chain": "solana", "error": "unavailable"}


async def _fetch_evm_balance(address: str, chain: str) -> dict:
    """Fetch native + USDC balance on an EVM chain."""
    chain_config = {
        "ethereum": {"module": "blockchain.eth_verifier", "symbol": "ETH"},
        "base": {"module": "blockchain.base_verifier", "symbol": "ETH"},
        "polygon": {"module": "blockchain.polygon_verifier", "symbol": "POL"},
        "arbitrum": {"module": "blockchain.arbitrum_verifier", "symbol": "ETH"},
        "avalanche": {"module": "blockchain.avalanche_verifier", "symbol": "AVAX"},
        "bnb": {"module": "blockchain.bnb_verifier", "symbol": "BNB"},
        "sei": {"module": "blockchain.sei_verifier", "symbol": "SEI"},
    }
    cfg = chain_config.get(chain)
    if not cfg:
        return {"chain": chain, "error": "unsupported"}
    try:
        import importlib
        mod = importlib.import_module(cfg["module"])
        verifier = getattr(mod, "verifier", None)
        if verifier and hasattr(verifier, "get_native_balance"):
            result = await verifier.get_native_balance(address)
            balance = result.get("balance_eth", result.get("balance", 0))
            return {"chain": chain,
                    "native": {"symbol": cfg["symbol"], "balance": balance},
                    "stablecoins": []}
        return {"chain": chain, "error": "no balance method"}
    except Exception as e:
        logger.debug("EVM %s balance error for %s: %s", chain, address, e)
        return {"chain": chain, "error": "unavailable"}


async def _fetch_xrpl_balance(address: str) -> dict:
    try:
        from blockchain.xrpl_verifier import get_xrpl_balance
        result = await get_xrpl_balance(address)
        return {"chain": "xrpl",
                "native": {"symbol": "XRP", "balance": result.get("xrp", 0)},
                "stablecoins": [{"symbol": "USDC", "balance": result.get("usdc", 0)}]}
    except Exception as e:
        logger.debug("XRPL balance error: %s", e)
        return {"chain": "xrpl", "error": "unavailable"}


async def _fetch_ton_balance(address: str) -> dict:
    try:
        from blockchain.ton_verifier import get_ton_balance
        result = await get_ton_balance(address)
        return {"chain": "ton",
                "native": {"symbol": "TON", "balance": result.get("ton", 0)},
                "stablecoins": []}
    except Exception as e:
        logger.debug("TON balance error: %s", e)
        return {"chain": "ton", "error": "unavailable"}


async def _fetch_sui_balance(address: str) -> dict:
    try:
        from blockchain.sui_verifier import get_sui_balance
        result = await get_sui_balance(address)
        return {"chain": "sui",
                "native": {"symbol": "SUI", "balance": result.get("sui", 0)},
                "stablecoins": []}
    except Exception as e:
        logger.debug("SUI balance error: %s", e)
        return {"chain": "sui", "error": "unavailable"}


async def _fetch_tron_balance(address: str) -> dict:
    try:
        from blockchain.tron_verifier import get_tron_balance
        result = await get_tron_balance(address)
        return {"chain": "tron",
                "native": {"symbol": "TRX", "balance": result.get("trx", 0)},
                "stablecoins": []}
    except Exception as e:
        logger.debug("TRON balance error: %s", e)
        return {"chain": "tron", "error": "unavailable"}


async def _fetch_near_balance(address: str) -> dict:
    try:
        from blockchain.near_verifier import get_near_balance
        result = await get_near_balance(address)
        return {"chain": "near",
                "native": {"symbol": "NEAR", "balance": result.get("near", 0)},
                "stablecoins": []}
    except Exception as e:
        logger.debug("NEAR balance error: %s", e)
        return {"chain": "near", "error": "unavailable"}


async def _fetch_aptos_balance(address: str) -> dict:
    try:
        from blockchain.aptos_verifier import get_aptos_balance
        result = await get_aptos_balance(address)
        return {"chain": "aptos",
                "native": {"symbol": "APT", "balance": result.get("apt", 0)},
                "stablecoins": []}
    except Exception as e:
        logger.debug("Aptos balance error: %s", e)
        return {"chain": "aptos", "error": "unavailable"}


async def _fetch_bitcoin_balance(address: str) -> dict:
    try:
        from blockchain.bitcoin_verifier import get_address_balance
        result = await get_address_balance(address)
        return {"chain": "bitcoin",
                "native": {"symbol": "BTC", "balance": result.get("balance_btc", 0)},
                "stablecoins": []}
    except Exception as e:
        logger.debug("Bitcoin balance error: %s", e)
        return {"chain": "bitcoin", "error": "unavailable"}


_CHAIN_FETCHERS = {
    "solana": _fetch_solana_balance,
    "ethereum": lambda addr: _fetch_evm_balance(addr, "ethereum"),
    "base": lambda addr: _fetch_evm_balance(addr, "base"),
    "polygon": lambda addr: _fetch_evm_balance(addr, "polygon"),
    "arbitrum": lambda addr: _fetch_evm_balance(addr, "arbitrum"),
    "avalanche": lambda addr: _fetch_evm_balance(addr, "avalanche"),
    "bnb": lambda addr: _fetch_evm_balance(addr, "bnb"),
    "sei": lambda addr: _fetch_evm_balance(addr, "sei"),
    "xrpl": _fetch_xrpl_balance,
    "ton": _fetch_ton_balance,
    "sui": _fetch_sui_balance,
    "tron": _fetch_tron_balance,
    "near": _fetch_near_balance,
    "aptos": _fetch_aptos_balance,
    "bitcoin": _fetch_bitcoin_balance,
}


async def _get_usd_prices(symbols: list[str]) -> dict[str, float]:
    """Fetch USD prices for symbols via price oracle."""
    prices: dict[str, float] = {}
    try:
        from trading.price_oracle import get_price
        tasks = {sym: get_price(sym) for sym in symbols}
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        for sym, result in zip(tasks.keys(), results):
            if isinstance(result, (int, float)) and result > 0:
                prices[sym] = result
            elif isinstance(result, dict) and result.get("price"):
                prices[sym] = result["price"]
    except Exception as e:
        logger.debug("Price fetch error: %s", e)

    # Stablecoins = $1
    for stable in ("USDC", "USDT", "DAI", "BUSD"):
        prices.setdefault(stable, 1.0)
    return prices


# ══════════════════════════════════════════
#  FULL PORTFOLIO
# ══════════════════════════════════════════

@router.get("/{address}")
async def get_portfolio(
    address: str,
    chains: Optional[str] = Query(None, description="Comma-separated chains to query (default: auto-detect)"),
):
    """Multi-chain portfolio: balances + USD values for a wallet address.

    Address format auto-detected:
    - Solana: base58 32-44 chars
    - EVM (ETH/Base/Polygon/...): 0x + 40 hex chars
    - XRP: r + 24-34 chars
    - etc.

    Optional: ?chains=solana,ethereum to limit chains.
    """
    if not address or len(address) > 128:
        raise HTTPException(400, "Invalid address")

    # Check cache
    cache_key = f"{address}:{chains or 'auto'}"
    cached = _portfolio_cache.get(cache_key)
    if cached and time.time() - cached["ts"] < CACHE_TTL:
        return cached["data"]

    # Detect or parse chains
    if chains:
        target_chains = [c.strip().lower() for c in chains.split(",") if c.strip()]
        invalid = [c for c in target_chains if c not in _CHAIN_FETCHERS]
        if invalid:
            raise HTTPException(400, f"Unsupported chains: {', '.join(invalid)}")
        # Validate address format against each requested chain
        _chain_validators = {
            "solana": _SOLANA_RE, "xrpl": _XRPL_RE, "ton": _TON_RE,
            "sui": _SUI_RE, "tron": _TRON_RE, "near": _NEAR_RE,
            "aptos": _APTOS_RE, "bitcoin": _BTC_RE,
        }
        for chain in target_chains:
            validator = _chain_validators.get(chain, _EVM_RE)
            if not validator.match(address):
                raise HTTPException(400, f"Invalid address format for {chain}")
    else:
        target_chains = _detect_chains(address)

    if not target_chains:
        raise HTTPException(400, "Could not detect chain from address format. Use ?chains= parameter.")

    # Fetch balances in parallel (with 10s timeout per chain)
    tasks = []
    for chain in target_chains:
        fetcher = _CHAIN_FETCHERS.get(chain)
        if fetcher:
            tasks.append(asyncio.wait_for(fetcher(address), timeout=10))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Collect balances and symbols for pricing
    chain_balances = []
    symbols_needed: set[str] = set()
    for result in results:
        if isinstance(result, Exception):
            continue
        if isinstance(result, dict) and "error" not in result:
            chain_balances.append(result)
            native = result.get("native", {})
            if native.get("balance", 0) > 0:
                symbols_needed.add(native["symbol"])
            for sc in result.get("stablecoins", []):
                if sc.get("balance", 0) > 0:
                    symbols_needed.add(sc["symbol"])

    # Fetch USD prices
    prices = await _get_usd_prices(list(symbols_needed)) if symbols_needed else {}

    # Calculate USD values
    total_usd = 0.0
    holdings = []
    for cb in chain_balances:
        native = cb.get("native", {})
        native_bal = native.get("balance", 0) or 0
        native_sym = native.get("symbol", "")
        native_price = prices.get(native_sym, 0)
        native_usd = native_bal * native_price

        chain_entry = {
            "chain": cb["chain"],
            "native": {
                "symbol": native_sym,
                "balance": native_bal,
                "price_usd": native_price,
                "value_usd": round(native_usd, 2),
            },
            "stablecoins": [],
            "total_usd": round(native_usd, 2),
        }

        sc_total = 0.0
        for sc in cb.get("stablecoins", []):
            sc_bal = sc.get("balance", 0) or 0
            sc_sym = sc.get("symbol", "")
            sc_price = prices.get(sc_sym, 1.0)
            sc_usd = sc_bal * sc_price
            sc_total += sc_usd
            chain_entry["stablecoins"].append({
                "symbol": sc_sym,
                "balance": sc_bal,
                "value_usd": round(sc_usd, 2),
            })

        chain_entry["total_usd"] = round(native_usd + sc_total, 2)
        total_usd += native_usd + sc_total
        holdings.append(chain_entry)

    # Sort by value descending
    holdings.sort(key=lambda x: x["total_usd"], reverse=True)

    response = {
        "address": address,
        "total_value_usd": round(total_usd, 2),
        "chains_queried": len(target_chains),
        "chains_with_balance": len([h for h in holdings if h["total_usd"] > 0]),
        "holdings": holdings,
        "prices": {sym: round(p, 4) for sym, p in prices.items()},
        "cached": False,
        "timestamp": int(time.time()),
    }

    # Cache result
    _portfolio_cache[cache_key] = {"data": response, "ts": time.time()}
    # Evict old cache entries (keep max 500)
    if len(_portfolio_cache) > 500:
        oldest_keys = sorted(_portfolio_cache, key=lambda k: _portfolio_cache[k]["ts"])[:100]
        for k in oldest_keys:
            _portfolio_cache.pop(k, None)

    return response


# ══════════════════════════════════════════
#  PER-CHAIN BREAKDOWN
# ══════════════════════════════════════════

@router.get("/{address}/chains")
async def get_portfolio_chains(address: str):
    """List which chains this address is compatible with."""
    if not address or len(address) > 128:
        raise HTTPException(400, "Invalid address")
    detected = _detect_chains(address)
    return {
        "address": address,
        "compatible_chains": detected,
        "count": len(detected),
        "all_supported": list(_CHAIN_FETCHERS.keys()),
    }


def get_router():
    return router
