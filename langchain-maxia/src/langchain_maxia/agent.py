"""Pre-built LangChain agent with all MAXIA tools.

Creates a ready-to-use tool-calling agent that can discover AI services,
check crypto prices, get swap quotes, analyze wallets, find DeFi yields,
and more on the MAXIA marketplace.

Usage::

    from langchain_maxia import create_maxia_agent
    from langchain_openai import ChatOpenAI

    llm = ChatOpenAI(model="gpt-4o")
    agent_executor = create_maxia_agent(llm, api_key="maxia_...")
    result = agent_executor.invoke({"input": "What is the price of SOL?"})
    print(result["output"])
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from langchain_core.language_models import BaseLanguageModel
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import BaseTool

from langchain_maxia.tools import get_all_tools

__all__ = ["create_maxia_agent", "create_maxia_agent_executor"]

_SYSTEM_PROMPT = """\
You are a helpful AI assistant with access to the MAXIA marketplace — \
an AI-to-AI marketplace on 14 blockchains (Solana, Base, Ethereum, XRP, \
Polygon, Arbitrum, Avalanche, BNB, TON, SUI, TRON, NEAR, Aptos, SEI).

You can:
- Check live crypto prices (107 tokens) and tokenized stock prices (25 US stocks)
- Get swap quotes between tokens (5000+ pairs)
- Find the best DeFi yields across 14 chains
- Analyze crypto market sentiment
- Discover and execute AI services on the marketplace
- Analyze Solana wallets
- List GPU rental tiers and pricing
- Check the on-chain escrow program status

When answering questions:
1. Use the available tools to fetch real-time data instead of guessing.
2. Present numbers clearly (prices, percentages, amounts).
3. If a tool call fails, explain what went wrong and suggest alternatives.
4. For swap quotes, always show the exchange rate and any fees.
5. Mention that MAXIA uses USDC as the settlement currency.

MAXIA website: https://maxiaworld.app
"""


def create_maxia_agent(
    llm: BaseLanguageModel,
    api_key: str = "",
    base_url: str = "https://maxiaworld.app",
    tools: Optional[Sequence[BaseTool]] = None,
    system_prompt: str = _SYSTEM_PROMPT,
) -> Any:
    """Create a LangChain agent pre-configured with all MAXIA tools.

    This returns a runnable agent (not an executor). For a fully
    wrapped executor, use :func:`create_maxia_agent_executor`.

    Parameters
    ----------
    llm:
        A LangChain chat model that supports tool calling (e.g.
        ``ChatOpenAI``, ``ChatAnthropic``).
    api_key:
        MAXIA API key. Free tools work without one; authenticated
        tools (execute, wallet analysis) require it.
    base_url:
        Base URL of the MAXIA instance.
    tools:
        Override the default tool list. If ``None``, all 10 MAXIA
        tools are loaded automatically.
    system_prompt:
        System prompt for the agent. Override to customize behavior.

    Returns
    -------
    agent
        A LangChain runnable agent. Combine with
        ``AgentExecutor`` or use :func:`create_maxia_agent_executor`.
    """
    from langchain_core.agents import AgentAction, AgentFinish

    maxia_tools = list(tools) if tools is not None else get_all_tools(api_key, base_url)

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        MessagesPlaceholder("chat_history", optional=True),
        ("human", "{input}"),
        MessagesPlaceholder("agent_scratchpad"),
    ])

    llm_with_tools = llm.bind_tools(maxia_tools)

    from langchain_core.runnables import RunnablePassthrough
    from langchain_core.output_parsers.openai_tools import JsonOutputToolsParser

    agent = (
        RunnablePassthrough.assign(
            agent_scratchpad=lambda x: x.get("agent_scratchpad", []),
        )
        | prompt
        | llm_with_tools
    )

    return agent


def create_maxia_agent_executor(
    llm: BaseLanguageModel,
    api_key: str = "",
    base_url: str = "https://maxiaworld.app",
    tools: Optional[Sequence[BaseTool]] = None,
    system_prompt: str = _SYSTEM_PROMPT,
    verbose: bool = False,
    max_iterations: int = 10,
    return_intermediate_steps: bool = False,
) -> Any:
    """Create a complete LangChain AgentExecutor with all MAXIA tools.

    This is the easiest way to get a working agent — just provide an
    LLM and optionally a MAXIA API key.

    Parameters
    ----------
    llm:
        A LangChain chat model that supports tool calling.
    api_key:
        MAXIA API key.
    base_url:
        Base URL of the MAXIA instance.
    tools:
        Override the default tool list.
    system_prompt:
        System prompt for the agent.
    verbose:
        Print intermediate steps to stdout.
    max_iterations:
        Maximum number of tool-calling iterations.
    return_intermediate_steps:
        Include intermediate steps in the output dict.

    Returns
    -------
    AgentExecutor
        A ready-to-use agent executor.

    Examples
    --------
    >>> from langchain_openai import ChatOpenAI
    >>> from langchain_maxia import create_maxia_agent_executor
    >>> agent = create_maxia_agent_executor(
    ...     ChatOpenAI(model="gpt-4o"),
    ...     api_key="maxia_...",
    ... )
    >>> result = agent.invoke({"input": "Get me a swap quote: 10 SOL to USDC"})
    >>> print(result["output"])
    """
    from langchain.agents import AgentExecutor, create_tool_calling_agent

    maxia_tools = list(tools) if tools is not None else get_all_tools(api_key, base_url)

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        MessagesPlaceholder("chat_history", optional=True),
        ("human", "{input}"),
        MessagesPlaceholder("agent_scratchpad"),
    ])

    agent = create_tool_calling_agent(llm, maxia_tools, prompt)

    return AgentExecutor(
        agent=agent,
        tools=maxia_tools,
        verbose=verbose,
        max_iterations=max_iterations,
        return_intermediate_steps=return_intermediate_steps,
        handle_parsing_errors=True,
    )
