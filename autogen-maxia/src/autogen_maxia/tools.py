"""MAXIA tools for Microsoft AutoGen 0.4+ — plain functions wrapped by FunctionTool.

Each function has full type annotations and a docstring that the LLM uses
to decide when to call it. AutoGen's FunctionTool auto-generates the
JSON schema from the function signature.

Usage::

    from autogen_maxia import get_maxia_tools
    from autogen_agentchat.agents import AssistantAgent

    tools = get_maxia_tools()
    agent = AssistantAgent("maxia-agent", tools=tools, model_client=client)
"""

from __future__ import annotations

import json
from typing import Annotated

from autogen_core.tools import FunctionTool

from autogen_maxia.client import maxia_get, maxia_post

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


def maxia_discover(
    capability: Annotated[str, "Filter by type: code, audit, sentiment, data, image, text, scraper"] = "",
    max_price: Annotated[float, "Maximum price in USDC (default 100)"] = 100.0,
) -> str:
    """Search AI services on the MAXIA marketplace (15 blockchains).

    Returns available services with pricing in USDC, ratings, and providers.
    """
    params: dict = {}
    if capability:
        params["capability"] = capability
    if max_price != 100.0:
        params["max_price"] = max_price
    data = maxia_get("/api/public/discover", params or None)
    services = data.get("services", data) if isinstance(data, dict) else data
    if not services:
        return "No services found on MAXIA marketplace."
    lines = [f"- {s.get('name', '?')} (${s.get('price_usdc', '?')} USDC) — {s.get('description', '')[:80]}"
             for s in (services[:10] if isinstance(services, list) else [])]
    return f"Found {len(services)} services:\n" + "\n".join(lines)


def maxia_get_price(
    token: Annotated[str, "Token symbol: SOL, BTC, ETH, USDC, BONK, JUP, etc."],
) -> str:
    """Get the live price of a cryptocurrency from MAXIA oracle (Pyth + Chainlink + CoinGecko)."""
    data = maxia_get("/api/public/crypto/prices")
    key = token.lower()
    entry = data.get(key, data.get(token.upper()))
    if entry is None:
        available = ", ".join(list(data.keys())[:20])
        return f"Token {token} not found. Available: {available}"
    price = entry if isinstance(entry, (int, float)) else entry.get("usd", 0)
    return f"{token.upper()}: ${price:,.6f}"


def maxia_swap_quote(
    from_token: Annotated[str, "Token to sell (e.g. SOL, USDC, ETH)"],
    to_token: Annotated[str, "Token to buy (e.g. USDC, SOL, BTC)"],
    amount: Annotated[float, "Amount to swap"],
) -> str:
    """Get a crypto swap quote — 65 tokens, 7 chains, 4160 pairs via Jupiter + 0x."""
    data = maxia_get("/api/public/crypto/quote", {
        "from_token": from_token.upper(),
        "to_token": to_token.upper(),
        "amount": amount,
    })
    return json.dumps(data, indent=2)


def maxia_gpu_tiers() -> str:
    """List GPU rental tiers on MAXIA via Akash Network (6 tiers, $0.15-$4.74/h, pay in USDC)."""
    data = maxia_get("/api/public/gpu/tiers")
    tiers = data.get("tiers", data) if isinstance(data, dict) else data
    if not tiers or not isinstance(tiers, list):
        return json.dumps(data, indent=2)
    lines = [f"- {t.get('name', '?')} ({t.get('gpu', '?')}, {t.get('vram_gb', '?')}GB) — ${t.get('base_price_per_hour', '?')}/h"
             for t in tiers]
    return "MAXIA GPU Tiers (Akash Network):\n" + "\n".join(lines)


def maxia_defi_yields(
    asset: Annotated[str, "Asset: USDC, SOL, ETH, BTC"] = "USDC",
    chain: Annotated[str, "Optional chain filter: solana, ethereum, base, arbitrum"] = "",
) -> str:
    """Find the best DeFi yields across 15 chains via DeFiLlama (Aave, Jito, Marinade, Orca, etc.)."""
    params: dict = {"asset": asset}
    if chain:
        params["chain"] = chain
    data = maxia_get("/api/public/defi/best-yield", params)
    return json.dumps(data, indent=2)


def maxia_sentiment(
    token: Annotated[str, "Token symbol: BTC, ETH, SOL, etc."],
) -> str:
    """Get crypto market sentiment for a token (Fear & Greed Index, social signals, trend)."""
    data = maxia_get("/api/public/sentiment", {"token": token.upper()})
    return json.dumps(data, indent=2)


def maxia_buy_service(
    service_type: Annotated[str, "Service type: text, code, audit, data, image_gen"],
    prompt: Annotated[str, "Your request for the AI service"],
) -> str:
    """Execute an AI service on MAXIA marketplace (sandbox mode, virtual $10K USDC)."""
    data = maxia_post("/api/public/sandbox/execute", {
        "service_type": service_type,
        "prompt": prompt,
    })
    result = data.get("result", "")
    cost = data.get("cost_usdc", "?")
    return f"[{service_type}] Cost: ${cost} USDC\n\n{result}"


def maxia_wallet_analysis(
    address: Annotated[str, "Solana wallet address to analyze"],
) -> str:
    """Analyze a Solana wallet — holdings, balance, profile classification (whale/trader/holder)."""
    data = maxia_get("/api/public/wallet-analysis", {"address": address})
    return json.dumps(data, indent=2)


def get_maxia_tools() -> list[FunctionTool]:
    """Return all 8 MAXIA tools as AutoGen FunctionTool instances.

    Usage::

        from autogen_maxia import get_maxia_tools
        from autogen_agentchat.agents import AssistantAgent

        tools = get_maxia_tools()
        agent = AssistantAgent("maxia-agent", tools=tools, model_client=client)
    """
    return [
        FunctionTool(maxia_discover, description="Search AI services on MAXIA marketplace"),
        FunctionTool(maxia_get_price, description="Get live crypto price from MAXIA oracle"),
        FunctionTool(maxia_swap_quote, description="Get crypto swap quote (65 tokens, 7 chains)"),
        FunctionTool(maxia_gpu_tiers, description="List GPU rental tiers and pricing"),
        FunctionTool(maxia_defi_yields, description="Find best DeFi yields across 15 chains"),
        FunctionTool(maxia_sentiment, description="Get crypto market sentiment analysis"),
        FunctionTool(maxia_buy_service, description="Execute AI service on MAXIA (sandbox)"),
        FunctionTool(maxia_wallet_analysis, description="Analyze a Solana wallet"),
    ]
