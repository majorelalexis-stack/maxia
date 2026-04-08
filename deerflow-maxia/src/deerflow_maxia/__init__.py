"""deerflow-maxia — MAXIA skills for DeerFlow v2.

Connect DeerFlow agents to the MAXIA AI-to-AI marketplace on 15 blockchains.
Provides DeerFlow-compatible skills for crypto swap, AI services, GPU rental,
DeFi yields, tokenized stocks, on-chain escrow, and more.

Two integration modes:

1. **MCP mode** (zero code): Copy ``extensions_config.example.json`` into your
   DeerFlow project root as ``extensions_config.json``. DeerFlow auto-discovers
   all 47 MAXIA tools via MCP protocol.

2. **Skills mode** (this package): Import MAXIA skills as DeerFlow skill
   functions for finer control, custom logic, or offline usage.

Quick start::

    from deerflow_maxia import MaxiaClient, get_all_skills

    # Get skill functions for DeerFlow
    skills = get_all_skills(api_key="maxia_...")

    # Or use the client directly
    client = MaxiaClient()
    prices = client.sync_get_prices()
"""

__version__ = "0.1.0"

from deerflow_maxia.client import MaxiaClient
from deerflow_maxia.skills import get_all_skills, get_skill

__all__ = [
    "MaxiaClient",
    "get_all_skills",
    "get_skill",
]
