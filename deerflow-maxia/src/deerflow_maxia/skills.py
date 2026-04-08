"""DeerFlow skill functions wrapping MAXIA API.

Each function is a standalone DeerFlow skill that can be registered
in the DeerFlow skills config or used programmatically.

DeerFlow skills are simple async functions that return structured data.
"""
from __future__ import annotations

from typing import Any

from deerflow_maxia.client import MaxiaClient

__all__ = ["get_all_skills", "get_skill"]

# Shared client instance (lazy init)
_client: MaxiaClient | None = None


def _get_client(api_key: str = "") -> MaxiaClient:
    global _client
    if _client is None:
        _client = MaxiaClient(api_key=api_key)
    return _client


# -- Skill functions --


async def maxia_prices(api_key: str = "", **kwargs: Any) -> dict:
    """Get live crypto prices for 65+ tokens across 15 blockchains."""
    return await _get_client(api_key).get_prices()


async def maxia_swap_quote(
    from_token: str = "SOL",
    to_token: str = "USDC",
    amount: float = 1.0,
    api_key: str = "",
    **kwargs: Any,
) -> dict:
    """Get a crypto swap quote. Supports 65 tokens, 4160 pairs via Jupiter + 0x."""
    return await _get_client(api_key).swap_quote(from_token, to_token, amount)


async def maxia_discover(
    capability: str = "",
    max_price: float = 100.0,
    api_key: str = "",
    **kwargs: Any,
) -> list[dict]:
    """Discover AI services on MAXIA marketplace (sentiment, code audit, translation, etc.)."""
    return await _get_client(api_key).discover(capability, max_price)


async def maxia_execute(
    service_id: str = "",
    prompt: str = "",
    payment_tx: str = "",
    api_key: str = "",
    **kwargs: Any,
) -> dict:
    """Buy and execute an AI service from the MAXIA marketplace."""
    return await _get_client(api_key).execute(service_id, prompt, payment_tx)


async def maxia_gpu_tiers(api_key: str = "", **kwargs: Any) -> dict:
    """List GPU tiers available for rent (RTX 4090, A100, H100) with live pricing."""
    return await _get_client(api_key).gpu_tiers()


async def maxia_best_yield(
    asset: str = "USDC",
    chain: str = "",
    api_key: str = "",
    **kwargs: Any,
) -> dict:
    """Find best DeFi yields for an asset across 14 chains (lending, staking, LP)."""
    return await _get_client(api_key).best_yield(asset, chain)


async def maxia_stocks(api_key: str = "", **kwargs: Any) -> dict:
    """List all 25 tokenized stocks with live prices."""
    return await _get_client(api_key).list_stocks()


async def maxia_stock_price(symbol: str = "AAPL", api_key: str = "", **kwargs: Any) -> dict:
    """Get real-time price of a tokenized stock (AAPL, TSLA, NVDA, etc.)."""
    return await _get_client(api_key).stock_price(symbol)


async def maxia_sentiment(token: str = "BTC", api_key: str = "", **kwargs: Any) -> dict:
    """Get crypto sentiment analysis for a token (social, news, on-chain signals)."""
    return await _get_client(api_key).get_sentiment(token)


async def maxia_wallet(address: str = "", api_key: str = "", **kwargs: Any) -> dict:
    """Analyze a Solana wallet (holdings, balance, activity, risk score)."""
    return await _get_client(api_key).analyze_wallet(address)


async def maxia_escrow(api_key: str = "", **kwargs: Any) -> dict:
    """Get on-chain escrow info (Solana PDA + Base L2 smart contract)."""
    return await _get_client(api_key).escrow_info()


# -- Skill registry --

_SKILLS: dict[str, dict[str, Any]] = {
    "maxia_prices": {
        "fn": maxia_prices,
        "name": "maxia_prices",
        "description": "Get live crypto prices for 65+ tokens across 15 blockchains",
    },
    "maxia_swap_quote": {
        "fn": maxia_swap_quote,
        "name": "maxia_swap_quote",
        "description": "Get a crypto swap quote (65 tokens, 4160 pairs)",
    },
    "maxia_discover": {
        "fn": maxia_discover,
        "name": "maxia_discover",
        "description": "Discover AI services on MAXIA marketplace",
    },
    "maxia_execute": {
        "fn": maxia_execute,
        "name": "maxia_execute",
        "description": "Buy and execute an AI service",
    },
    "maxia_gpu_tiers": {
        "fn": maxia_gpu_tiers,
        "name": "maxia_gpu_tiers",
        "description": "List GPU rental tiers with live pricing",
    },
    "maxia_best_yield": {
        "fn": maxia_best_yield,
        "name": "maxia_best_yield",
        "description": "Find best DeFi yields across 14 chains",
    },
    "maxia_stocks": {
        "fn": maxia_stocks,
        "name": "maxia_stocks",
        "description": "List 25 tokenized stocks with live prices",
    },
    "maxia_stock_price": {
        "fn": maxia_stock_price,
        "name": "maxia_stock_price",
        "description": "Get real-time tokenized stock price",
    },
    "maxia_sentiment": {
        "fn": maxia_sentiment,
        "name": "maxia_sentiment",
        "description": "Crypto sentiment analysis (social + on-chain)",
    },
    "maxia_wallet": {
        "fn": maxia_wallet,
        "name": "maxia_wallet",
        "description": "Analyze a Solana wallet",
    },
    "maxia_escrow": {
        "fn": maxia_escrow,
        "name": "maxia_escrow",
        "description": "On-chain escrow program info",
    },
}


def get_all_skills(api_key: str = "") -> list[dict[str, Any]]:
    """Return all MAXIA skills as a list of dicts with fn, name, description."""
    if api_key:
        _get_client(api_key)
    return list(_SKILLS.values())


def get_skill(name: str, api_key: str = "") -> dict[str, Any] | None:
    """Get a single MAXIA skill by name."""
    if api_key:
        _get_client(api_key)
    return _SKILLS.get(name)
