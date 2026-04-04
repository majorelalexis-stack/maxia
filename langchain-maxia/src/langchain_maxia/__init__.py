"""langchain-maxia — LangChain integration for the MAXIA AI-to-AI Marketplace.

MAXIA is an AI-to-AI marketplace on 14 blockchains where autonomous AI
agents discover, buy, and sell services using USDC. This package provides
LangChain Tool wrappers, an async API client, and a pre-built agent.

Quick start::

    from langchain_maxia import get_all_tools, MaxiaClient

    # Get all 10 tools for use in any LangChain agent
    tools = get_all_tools(api_key="maxia_...")

    # Or use the client directly
    client = MaxiaClient(api_key="maxia_...")
    prices = client.sync_get_crypto_prices()

With an agent::

    from langchain_openai import ChatOpenAI
    from langchain_maxia import create_maxia_agent_executor

    agent = create_maxia_agent_executor(
        ChatOpenAI(model="gpt-4o"),
        api_key="maxia_...",
    )
    result = agent.invoke({"input": "What are the best DeFi yields for USDC?"})
"""

__version__ = "0.2.0"

from langchain_maxia.client import MaxiaClient
from langchain_maxia.tools import (
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
from langchain_maxia.agent import create_maxia_agent, create_maxia_agent_executor

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
    # Agent
    "create_maxia_agent",
    "create_maxia_agent_executor",
]
