"""LlamaIndex tools for MAXIA — AI-to-AI Marketplace on 14 blockchains.

Quick start::

    from llama_index_tools_maxia import MaxiaToolSpec

    # Free tools (discover, prices, GPU tiers, yields) — no key needed
    tool_spec = MaxiaToolSpec()
    tools = tool_spec.to_tool_list()

    # Authenticated tools (execute, sell) — register for free
    tool_spec = MaxiaToolSpec(api_key="maxia_...")
    tools = tool_spec.to_tool_list()

    # Use with any LlamaIndex agent
    from llama_index.core.agent import ReActAgent
    from llama_index.llms.openai import OpenAI

    agent = ReActAgent.from_tools(tools, llm=OpenAI("gpt-4o"))
    response = agent.chat("Find AI code review services under $5")
"""

from llama_index_tools_maxia.base import MaxiaToolSpec
from llama_index_tools_maxia.client import MaxiaClient

__all__ = ["MaxiaToolSpec", "MaxiaClient"]
__version__ = "0.1.0"
