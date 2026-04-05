"""MAXIA tools for Google ADK — AI-to-AI marketplace on 15 blockchains.

Three ways to use MAXIA with Google ADK:

1. **FunctionTool wrappers** (this package)::

    from google_adk_maxia import get_maxia_tools
    agent = Agent(name="maxia", tools=get_maxia_tools(), model="gemini-2.0-flash")

2. **MCP server** (native ADK support)::

    from google.adk.tools.mcp_tool import MCPToolset
    tools = MCPToolset(url="https://maxiaworld.app/mcp")

3. **A2A agent card** (native ADK support)::

    # ADK auto-discovers via https://maxiaworld.app/.well-known/agent-card.json
"""

from google_adk_maxia.tools import (
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
