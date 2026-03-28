"""MAXIA — Chainlink Oracle on Base mainnet (on-chain price verification).

Lit les aggregateurs Chainlink directement sur la blockchain Base via eth_call.
Pas d'API centralisee — lecture on-chain pure (RPC → smart contract → prix).

Usage:
    price = await get_chainlink_price("ETH")
    # {"price": 3200.12, "decimals": 8, "round_id": 123, "updated_at": 1774..., "age_s": 5, "source": "chainlink_base"}

    verified = await verify_price_chainlink("ETH", expected_price=3200, max_deviation_pct=1.0)
    # {"verified": True, "chainlink_price": 3201.5, "deviation_pct": 0.04}
"""
import time
import logging
import struct

import httpx

logger = logging.getLogger("chainlink")

# ── Base Mainnet RPC ──
# Utilise le RPC public Base ou un provider configure
import os
BASE_RPC_URL = os.getenv("BASE_RPC_URL", "https://mainnet.base.org")

# ── Chainlink Aggregator V3 addresses on Base mainnet ──
# Source: https://docs.chain.link/data-feeds/price-feeds/addresses?network=base
# Verifiees via description() au startup
CHAINLINK_FEEDS = {
    "ETH": {
        "address": "0x71041dddad3595F9CEd3DcCFBe3D1F4b0a16Bb70",
        "pair": "ETH / USD",
        "decimals": 8,
    },
    "BTC": {
        "address": "0xCCADC697c55bbB68dc5bCdf8d3CBe83CdD4E071E",
        "pair": "WBTC / USD",
        "decimals": 8,
    },
    "USDC": {
        "address": "0x7e860098F58bBFC8648a4311b374B1D669a2bc6B",
        "pair": "USDC / USD",
        "decimals": 8,
    },
}

# Function selectors (keccak256 first 4 bytes)
# latestRoundData() -> (uint80,int256,uint256,uint256,uint80)
_LATEST_ROUND_DATA = "0xfeaf968c"
# description() -> string
_DESCRIPTION = "0x7284e416"
# decimals() -> uint8
_DECIMALS = "0x313ce567"

# Cache (30s TTL — on-chain = authoritative, no need for short cache)
_cl_cache: dict = {}
_CL_CACHE_TTL = 30

# Metrics
_cl_metrics = {
    "total_requests": 0,
    "successful": 0,
    "errors": 0,
    "feeds_verified": 0,
}

# HTTP client singleton
_http: httpx.AsyncClient | None = None


async def _get_http() -> httpx.AsyncClient:
    global _http
    if _http is None or getattr(_http, "is_closed", True):
        _http = httpx.AsyncClient(
            timeout=httpx.Timeout(10, connect=5),
            limits=httpx.Limits(max_connections=5, max_keepalive_connections=3),
        )
    return _http


async def _eth_call(to: str, data: str) -> str:
    """Execute eth_call on Base mainnet. Returns hex result."""
    client = await _get_http()
    resp = await client.post(BASE_RPC_URL, json={
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_call",
        "params": [{"to": to, "data": data}, "latest"],
    })
    if resp.status_code != 200:
        raise Exception(f"Base RPC HTTP {resp.status_code}")
    result = resp.json()
    if "error" in result:
        raise Exception(f"RPC error: {result['error'].get('message', '')[:100]}")
    return result.get("result", "0x")


def _decode_latest_round_data(hex_data: str) -> dict:
    """Decode latestRoundData() ABI response.
    Returns: roundId, answer, startedAt, updatedAt, answeredInRound"""
    clean = hex_data[2:] if hex_data.startswith("0x") else hex_data
    if len(clean) < 320:  # 5 * 64 hex chars
        raise ValueError(f"Invalid response length: {len(clean)}")

    # Each value is 32 bytes (64 hex chars)
    round_id = int(clean[0:64], 16)
    answer = int(clean[64:128], 16)
    # Handle signed int256 for answer
    if answer >= 2**255:
        answer -= 2**256
    started_at = int(clean[128:192], 16)
    updated_at = int(clean[192:256], 16)
    answered_in_round = int(clean[256:320], 16)

    return {
        "round_id": round_id,
        "answer": answer,
        "started_at": started_at,
        "updated_at": updated_at,
        "answered_in_round": answered_in_round,
    }


async def get_chainlink_price(symbol: str) -> dict:
    """Read price from Chainlink aggregator on Base mainnet (on-chain).

    Returns:
        {"price": float, "decimals": int, "round_id": int, "updated_at": int,
         "age_s": int, "stale": bool, "source": "chainlink_base"}
    """
    _cl_metrics["total_requests"] += 1
    sym = symbol.upper()

    feed = CHAINLINK_FEEDS.get(sym)
    if not feed:
        return {"error": f"No Chainlink feed for {sym} on Base", "source": "chainlink_base"}

    # Cache check
    now = time.time()
    cached = _cl_cache.get(sym)
    if cached and now - cached["ts"] < _CL_CACHE_TTL:
        return cached["data"]

    try:
        hex_result = await _eth_call(feed["address"], _LATEST_ROUND_DATA)
        decoded = _decode_latest_round_data(hex_result)

        decimals = feed["decimals"]
        price = decoded["answer"] / (10 ** decimals)
        age_s = int(now) - decoded["updated_at"]
        # Chainlink feeds on Base update every ~1 hour or on 0.5% deviation
        is_stale = age_s > 3600  # >1 hour = potentially stale

        result = {
            "price": round(price, 6),
            "decimals": decimals,
            "round_id": decoded["round_id"],
            "updated_at": decoded["updated_at"],
            "age_s": age_s,
            "stale": is_stale,
            "source": "chainlink_base",
            "contract": feed["address"],
        }

        _cl_cache[sym] = {"data": result, "ts": now}
        _cl_metrics["successful"] += 1
        return result

    except Exception as e:
        _cl_metrics["errors"] += 1
        logger.error(f"[Chainlink] {sym} error: {e}")
        return {"error": str(e)[:100], "source": "chainlink_base"}


async def verify_price_chainlink(symbol: str, expected_price: float,
                                  max_deviation_pct: float = 2.0, max_age_s: int = 3600) -> dict:
    """Cross-verify a price against Chainlink on-chain feed.

    Returns: {"verified": bool, "chainlink_price": float, "deviation_pct": float, "age_s": int}
    """
    result = await get_chainlink_price(symbol)
    if "error" in result:
        return {"verified": False, "error": result["error"], "source": "chainlink_base"}

    cl_price = result["price"]
    age_s = result["age_s"]

    if age_s > max_age_s:
        return {"verified": False, "error": f"Chainlink price too old: {age_s}s",
                "chainlink_price": cl_price, "age_s": age_s}

    if expected_price > 0 and cl_price > 0:
        deviation = abs(cl_price - expected_price) / expected_price * 100
    else:
        deviation = 0

    verified = deviation <= max_deviation_pct
    return {
        "verified": verified,
        "chainlink_price": cl_price,
        "expected_price": expected_price,
        "deviation_pct": round(deviation, 2),
        "age_s": age_s,
        "source": "chainlink_base",
    }


async def verify_feeds_at_startup() -> dict:
    """Verify Chainlink feed addresses at startup by checking description().
    Returns {"ETH": True, "BTC": True, ...}"""
    results = {}
    for sym, feed in CHAINLINK_FEEDS.items():
        try:
            hex_result = await _eth_call(feed["address"], _DESCRIPTION)
            # Decode ABI string: offset (32 bytes) + length (32 bytes) + data
            clean = hex_result[2:] if hex_result.startswith("0x") else hex_result
            if len(clean) >= 192:
                str_len = int(clean[64:128], 16)
                desc_bytes = bytes.fromhex(clean[128:128 + str_len * 2])
                description = desc_bytes.decode("utf-8", errors="replace").strip()
                results[sym] = {
                    "verified": feed["pair"].lower().replace(" ", "") in description.lower().replace(" ", ""),
                    "description": description,
                    "address": feed["address"],
                }
                if results[sym]["verified"]:
                    _cl_metrics["feeds_verified"] += 1
                    logger.info(f"[Chainlink] {sym} feed verified: {description}")
                else:
                    logger.warning(f"[Chainlink] {sym} feed mismatch: expected '{feed['pair']}', got '{description}'")
            else:
                results[sym] = {"verified": False, "error": "description() returned empty"}
        except Exception as e:
            results[sym] = {"verified": False, "error": str(e)[:100]}
    return results


def get_metrics() -> dict:
    return {**_cl_metrics, "feeds_available": list(CHAINLINK_FEEDS.keys())}
