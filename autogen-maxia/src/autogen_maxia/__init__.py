"""MAXIA tools for Microsoft AutoGen 0.4+ — AI-to-AI marketplace on 15 blockchains.

Usage::

    from autogen_maxia import get_maxia_tools
    from autogen_agentchat.agents import AssistantAgent

    tools = get_maxia_tools()
    agent = AssistantAgent("maxia-agent", tools=tools, model_client=client)
"""

from autogen_maxia.tools import (
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
