"""MAXIA Composio custom actions — 8 tools for the AI-to-AI marketplace.

Each action is a standalone function decorated with @action that Composio
auto-discovers and makes available to any connected framework.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from composio import action

_BASE_URL = os.getenv("MAXIA_API_URL", "https://maxiaworld.app")
_TIMEOUT = 20.0


def _headers() -> dict[str, str]:
    h: dict[str, str] = {"Accept": "application/json"}
    key = os.getenv("MAXIA_API_KEY", "")
    if key:
        h["X-API-Key"] = key
    return h


def _get(path: str, params: dict[str, Any] | None = None) -> dict:
    resp = httpx.get(f"{_BASE_URL}{path}", params=params, headers=_headers(), timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _post(path: str, body: dict[str, Any]) -> dict:
    resp = httpx.post(f"{_BASE_URL}{path}", json=body, headers=_headers(), timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


@action(toolname="maxia", requires=[])
def maxia_discover(capability: str = "", max_price: float = 100.0) -> dict:
    """Discover AI services on the MAXIA marketplace (15 blockchains).

    Search by capability: code, audit, sentiment, data, image, text, scraper.
    Returns service name, price in USDC, rating, and provider.

    :param capability: Filter by type (code, audit, sentiment, data, image, text, scraper)
    :param max_price: Maximum price in USDC (default 100)
    :return result: List of available AI services with pricing
    """
    params: dict[str, Any] = {}
    if capability:
        params["capability"] = capability
    if max_price != 100.0:
        params["max_price"] = max_price
    return _get("/api/public/discover", params or None)


@action(toolname="maxia", requires=[])
def maxia_get_price(token: str) -> dict:
    """Get live crypto price from MAXIA oracle (Pyth + Chainlink + CoinGecko).

    Supports 65 tokens: SOL, BTC, ETH, USDC, BONK, JUP, WIF, RNDR, etc.

    :param token: Token symbol (e.g. SOL, BTC, ETH, BONK)
    :return result: Current price in USD with source
    """
    data = _get("/api/public/crypto/prices")
    key = token.lower()
    entry = data.get(key, data.get(token))
    if entry is None:
        available = list(data.keys())[:20]
        return {"error": f"Token {token} not found", "available": available}
    price = entry if isinstance(entry, (int, float)) else entry.get("usd", 0)
    return {"token": token.upper(), "price_usd": price, "source": "MAXIA oracle"}


@action(toolname="maxia", requires=[])
def maxia_swap_quote(from_token: str, to_token: str, amount: float) -> dict:
    """Get a crypto swap quote — 65 tokens, 7 chains, 4160 pairs.

    Routes via Jupiter (Solana) + 0x (6 EVM chains).
    Commission: 0.01-0.10% depending on volume tier.

    :param from_token: Token to sell (e.g. SOL, USDC, ETH)
    :param to_token: Token to buy (e.g. USDC, SOL, BTC)
    :param amount: Amount to swap
    :return result: Quote with output amount, price, and commission
    """
    return _get("/api/public/crypto/quote", {
        "from_token": from_token.upper(),
        "to_token": to_token.upper(),
        "amount": amount,
    })


@action(toolname="maxia", requires=[])
def maxia_gpu_tiers() -> dict:
    """List GPU rental tiers on MAXIA via Akash Network.

    6 tiers from RTX 4090 ($0.76/h) to H200 ($4.74/h).
    15-40% cheaper than AWS. Pay in USDC.

    :return result: Available GPU tiers with pricing
    """
    return _get("/api/public/gpu/tiers")


@action(toolname="maxia", requires=[])
def maxia_defi_yields(asset: str = "USDC", chain: str = "") -> dict:
    """Find best DeFi yields across 15 chains via DeFiLlama.

    Covers lending (Aave, Kamino), staking (Jito, Marinade), LP (Orca, Raydium).

    :param asset: Asset to find yields for (USDC, SOL, ETH, BTC)
    :param chain: Optional chain filter (solana, ethereum, base, arbitrum)
    :return result: Best yields sorted by APY
    """
    params: dict[str, Any] = {"asset": asset}
    if chain:
        params["chain"] = chain
    return _get("/api/public/defi/best-yield", params)


@action(toolname="maxia", requires=[])
def maxia_sentiment(token: str) -> dict:
    """Get crypto market sentiment analysis for a token.

    Returns Fear & Greed Index, social signals, and trend direction.

    :param token: Token symbol (BTC, ETH, SOL, etc.)
    :return result: Sentiment score and analysis
    """
    return _get("/api/public/sentiment", {"token": token.upper()})


@action(toolname="maxia", requires=[])
def maxia_buy_service(service_type: str, prompt: str) -> dict:
    """Execute an AI service on MAXIA marketplace (sandbox mode).

    Uses virtual $10K USDC balance — no real payment needed.
    Service types: text, code, audit, data, image_gen.

    :param service_type: Service type (text, code, audit, data, image_gen)
    :param prompt: Your request for the AI service
    :return result: Service execution result
    """
    return _post("/api/public/sandbox/execute", {
        "service_type": service_type,
        "prompt": prompt,
    })


@action(toolname="maxia", requires=[])
def maxia_wallet_analysis(address: str) -> dict:
    """Analyze a Solana wallet — holdings, balance, classification.

    Returns token holdings, SOL/USDC balance, profile type (whale/trader/holder).

    :param address: Solana wallet address
    :return result: Wallet analysis with holdings and classification
    """
    return _get("/api/public/wallet-analysis", {"address": address})


def get_maxia_tools() -> list:
    """Return all 8 MAXIA Composio actions as a list.

    Usage::

        from composio_maxia import get_maxia_tools
        tools = get_maxia_tools()
    """
    return [
        maxia_discover,
        maxia_get_price,
        maxia_swap_quote,
        maxia_gpu_tiers,
        maxia_defi_yields,
        maxia_sentiment,
        maxia_buy_service,
        maxia_wallet_analysis,
    ]
