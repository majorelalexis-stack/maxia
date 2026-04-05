"""MAXIA tools for Composio — AI-to-AI marketplace on 15 blockchains.

Usage with any Composio-supported framework::

    from composio_maxia import get_maxia_tools

    tools = get_maxia_tools()
    # Pass to LangChain, CrewAI, AutoGen, or any Composio agent
"""

from composio_maxia.actions import (
    maxia_discover,
    maxia_get_price,
    maxia_swap_quote,
    maxia_gpu_tiers,
    maxia_defi_yields,
    maxia_sentiment,
    maxia_buy_service,
    maxia_wallet_analysis,
    get_maxia_tools,
)

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

__version__ = "0.1.0"
