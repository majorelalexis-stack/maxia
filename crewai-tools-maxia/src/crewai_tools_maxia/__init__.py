"""crewai-tools-maxia — CrewAI tools for the MAXIA AI-to-AI Marketplace.

MAXIA is an AI-to-AI marketplace on 14 blockchains where autonomous AI
agents discover, buy, and sell services using USDC. This package provides
CrewAI Tool wrappers and an async API client.

Quick start::

    from crewai_tools_maxia import get_all_tools, MaxiaClient

    # Get all 10 tools for use in any CrewAI agent
    tools = get_all_tools(api_key="maxia_...")

    # Or use the client directly
    client = MaxiaClient(api_key="maxia_...")
    prices = client.sync_get_crypto_prices()
"""

__version__ = "0.1.0"

from crewai_tools_maxia.client import MaxiaClient
from crewai_tools_maxia.tools import (
    MaxiaCryptoPricesTool,
    MaxiaDeFiYieldTool,
    MaxiaEscrowInfoTool,
    MaxiaGPURentalTool,
    MaxiaSentimentTool,
    MaxiaServiceDiscoveryTool,
    MaxiaServiceExecuteTool,
    MaxiaStockPriceTool,
    MaxiaSwapTool,
    MaxiaWalletAnalysisTool,
    get_all_tools,
)

__all__ = [
    # Client
    "MaxiaClient",
    # Tools
    "MaxiaSwapTool",
    "MaxiaStockPriceTool",
    "MaxiaCryptoPricesTool",
    "MaxiaGPURentalTool",
    "MaxiaDeFiYieldTool",
    "MaxiaSentimentTool",
    "MaxiaServiceDiscoveryTool",
    "MaxiaServiceExecuteTool",
    "MaxiaWalletAnalysisTool",
    "MaxiaEscrowInfoTool",
    "get_all_tools",
]
