"""MAXIA tools for Google ADK — plain functions wrapped by FunctionTool.

Google ADK's FunctionTool works exactly like AutoGen: it takes a plain
Python function with type annotations and auto-generates the JSON schema.
ADK also supports MCP servers natively, so MAXIA can be used via:
1. These FunctionTool wrappers (this package)
2. MAXIA's MCP server directly (https://maxiaworld.app/mcp)
3. MAXIA's A2A agent card (https://maxiaworld.app/.well-known/agent-card.json)

Usage::

    from google_adk_maxia import get_maxia_tools
    from google.adk.agents import Agent

    tools = get_maxia_tools()
    agent = Agent(name="maxia-agent", tools=tools, model="gemini-2.0-flash")
"""

from __future__ import annotations

import json

from google.adk.tools import FunctionTool

from google_adk_maxia.client import maxia_get, maxia_post

__all__ = [
    "maxia_discover",
    "maxia_get_price",
    "maxia_swap_quote",
    "maxia_gpu_tiers",
    "maxia_defi_yields",
    "maxia_sentiment",
    "maxia_buy_service",
    "maxia_wallet_analysis",
    "get_maxia_tools",
]


def maxia_discover(capability: str = "", max_price: float = 100.0) -> dict:
    """Search AI services on the MAXIA marketplace (15 blockchains).

    Args:
        capability: Filter by type — code, audit, sentiment, data, image, text, scraper.
        max_price: Maximum price in USDC (default 100).

    Returns:
        Available services with pricing in USDC, ratings, and providers.
    """
    params: dict = {}
    if capability:
        params["capability"] = capability
    if max_price != 100.0:
        params["max_price"] = max_price
    return maxia_get("/api/public/discover", params or None)


def maxia_get_price(token: str) -> dict:
    """Get the live price of a cryptocurrency from MAXIA oracle (Pyth + Chainlink + CoinGecko).

    Args:
        token: Token symbol — SOL, BTC, ETH, USDC, BONK, JUP, etc.

    Returns:
        Current price in USD with source information.
    """
    data = maxia_get("/api/public/crypto/prices")
    key = token.lower()
    entry = data.get(key, data.get(token.upper()))
    if entry is None:
        return {"error": f"Token {token} not found", "available": list(data.keys())[:20]}
    price = entry if isinstance(entry, (int, float)) else entry.get("usd", 0)
    return {"token": token.upper(), "price_usd": price, "source": "MAXIA oracle"}


def maxia_swap_quote(from_token: str, to_token: str, amount: float) -> dict:
    """Get a crypto swap quote — 65 tokens, 7 chains, 4160 pairs via Jupiter + 0x.

    Args:
        from_token: Token to sell (e.g. SOL, USDC, ETH).
        to_token: Token to buy (e.g. USDC, SOL, BTC).
        amount: Amount to swap.

    Returns:
        Quote with output amount, price, and commission breakdown.
    """
    return maxia_get("/api/public/crypto/quote", {
        "from_token": from_token.upper(),
        "to_token": to_token.upper(),
        "amount": amount,
    })


def maxia_gpu_tiers() -> dict:
    """List GPU rental tiers on MAXIA via Akash Network (6 tiers, $0.15-$4.74/h, pay in USDC).

    Returns:
        Available GPU tiers with name, VRAM, pricing, and availability.
    """
    return maxia_get("/api/public/gpu/tiers")


def maxia_defi_yields(asset: str = "USDC", chain: str = "") -> dict:
    """Find the best DeFi yields across 15 chains via DeFiLlama.

    Args:
        asset: Asset to find yields for — USDC, SOL, ETH, BTC.
        chain: Optional chain filter — solana, ethereum, base, arbitrum.

    Returns:
        Best yields sorted by APY with protocol and pool details.
    """
    params: dict = {"asset": asset}
    if chain:
        params["chain"] = chain
    return maxia_get("/api/public/defi/best-yield", params)


def maxia_sentiment(token: str) -> dict:
    """Get crypto market sentiment for a token (Fear & Greed Index, social signals, trend).

    Args:
        token: Token symbol — BTC, ETH, SOL, etc.

    Returns:
        Sentiment score, social volume, and trend direction.
    """
    return maxia_get("/api/public/sentiment", {"token": token.upper()})


def maxia_buy_service(service_type: str, prompt: str) -> dict:
    """Execute an AI service on MAXIA marketplace (sandbox mode, virtual $10K USDC).

    Args:
        service_type: Service type — text, code, audit, data, image_gen.
        prompt: Your request for the AI service.

    Returns:
        Service execution result with cost.
    """
    return maxia_post("/api/public/sandbox/execute", {
        "service_type": service_type,
        "prompt": prompt,
    })


def maxia_wallet_analysis(address: str) -> dict:
    """Analyze a Solana wallet — holdings, balance, profile classification.

    Args:
        address: Solana wallet address to analyze.

    Returns:
        Token holdings, SOL/USDC balance, profile type (whale/trader/holder).
    """
    return maxia_get("/api/public/wallet-analysis", {"address": address})


def get_maxia_tools() -> list[FunctionTool]:
    """Return all 8 MAXIA tools as Google ADK FunctionTool instances.

    Usage::

        from google_adk_maxia import get_maxia_tools
        from google.adk.agents import Agent

        tools = get_maxia_tools()
        agent = Agent(name="maxia-agent", tools=tools, model="gemini-2.0-flash")
    """
    return [
        FunctionTool(maxia_discover),
        FunctionTool(maxia_get_price),
        FunctionTool(maxia_swap_quote),
        FunctionTool(maxia_gpu_tiers),
        FunctionTool(maxia_defi_yields),
        FunctionTool(maxia_sentiment),
        FunctionTool(maxia_buy_service),
        FunctionTool(maxia_wallet_analysis),
    ]
