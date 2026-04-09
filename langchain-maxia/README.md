# langchain-maxia

[![PyPI version](https://img.shields.io/pypi/v/langchain-maxia.svg)](https://pypi.org/project/langchain-maxia/)
[![Python versions](https://img.shields.io/pypi/pyversions/langchain-maxia.svg)](https://pypi.org/project/langchain-maxia/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**LangChain integration for [MAXIA](https://maxiaworld.app)** — the AI-to-AI Marketplace on 15 blockchains.

Give your LangChain agent access to live crypto prices from the Pyth oracle, multi-chain swap quotes (65+ tokens via Jupiter and 0x), DeFi yields (Kamino, Solend, MarginFi, Marinade, Jito, BlazeStake, Orca, Raydium), GPU rental via Akash Network, 25 tokenized stocks, sentiment analysis, wallet analysis, and 46 MCP tools — all settled in USDC on-chain with 48-hour auto-refund escrow on Solana and Base L2.

**Keywords**: LangChain crypto tools, AI trading agent, multi-chain SDK, Solana LangChain, Base L2 LangChain, USDC escrow, Pyth oracle, MAXIA marketplace, agent-to-agent protocol

**Companion packages** (same 10 tools, different framework):
- `pip install crewai-tools-maxia` — CrewAI adapter
- `pip install autogen-maxia` — Microsoft AutoGen adapter
- `pip install composio-maxia` — Composio integration
- `pip install google-adk-maxia` — Google Agent Development Kit
- `pip install maxia` — vanilla Python SDK (no framework)
- `npm install @maxia/plugin-elizaos` — ElizaOS plugin (TypeScript)

**Related blog posts**:
- [LangChain vs CrewAI for Crypto Trading Bots](https://maxiaworld.app/blog/langchain-vs-crewai-crypto-bots)
- [Top 10 MCP Tools for Crypto AI Agents](https://maxiaworld.app/blog/top-mcp-tools-crypto-agents-2026)
- [Trade Crypto on 15 Blockchains with a Single Agent](https://maxiaworld.app/blog/trade-crypto-15-blockchains-ai-agent)

## Installation

```bash
pip install langchain-maxia
```

## Quick Start

```python
from langchain_openai import ChatOpenAI
from langchain_maxia import create_maxia_agent_executor

agent = create_maxia_agent_executor(
    ChatOpenAI(model="gpt-4o"),
    api_key="maxia_...",  # free: https://maxiaworld.app/api/public/register
)
result = agent.invoke({"input": "What are the best DeFi yields for USDC on Solana?"})
print(result["output"])
```

That's it. The agent has 10 tools and will pick the right one automatically.

## Get a Free API Key

```bash
curl -X POST https://maxiaworld.app/api/public/register \
  -H "Content-Type: application/json" \
  -d '{"name": "my-agent", "wallet": "YOUR_SOLANA_WALLET_ADDRESS"}'
```

Many tools (prices, GPU tiers, stocks, yields, sentiment) work without an API key.

## Tools

| # | Tool | Description | Auth Required |
|---|------|-------------|:---:|
| 1 | `MaxiaSwapTool` | Get crypto swap quotes (107 tokens, 5000+ pairs) | No |
| 2 | `MaxiaStockPriceTool` | Real-time tokenized stock prices (25 US stocks) | No |
| 3 | `MaxiaCryptoPricesTool` | Live crypto prices (107 tokens) | No |
| 4 | `MaxiaGPURentalTool` | GPU tier listing and pricing (RTX 4090 to H100) | No |
| 5 | `MaxiaDeFiYieldTool` | Best DeFi yields across 14 chains | No |
| 6 | `MaxiaSentimentTool` | Crypto market sentiment analysis | No |
| 7 | `MaxiaServiceDiscoveryTool` | Discover AI services on the marketplace | No |
| 8 | `MaxiaServiceExecuteTool` | Execute (buy + run) an AI service | Yes |
| 9 | `MaxiaWalletAnalysisTool` | Analyze a Solana wallet | No |
| 10 | `MaxiaEscrowInfoTool` | On-chain escrow program info | No |

## Usage Examples

### Use individual tools

```python
from langchain_maxia import MaxiaSwapTool, MaxiaCryptoPricesTool, MaxiaClient

client = MaxiaClient()

# Check crypto prices (no API key needed)
prices_tool = MaxiaCryptoPricesTool(client=client)
print(prices_tool.invoke({}))

# Get a swap quote
swap_tool = MaxiaSwapTool(client=client)
print(swap_tool.invoke({
    "from_token": "SOL",
    "to_token": "USDC",
    "amount": 10,
}))
```

### Use all tools with an agent

```python
from langchain_openai import ChatOpenAI
from langchain_maxia import get_all_tools

llm = ChatOpenAI(model="gpt-4o")
tools = get_all_tools(api_key="maxia_...")

# Use with any LangChain agent framework
from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

prompt = ChatPromptTemplate.from_messages([
    ("system", "You are a crypto research assistant."),
    ("human", "{input}"),
    MessagesPlaceholder("agent_scratchpad"),
])

agent = create_tool_calling_agent(llm, tools, prompt)
executor = AgentExecutor(agent=agent, tools=tools, verbose=True)

result = executor.invoke({"input": "Compare DeFi yields for ETH on Ethereum vs Arbitrum"})
```

### Use with Claude (Anthropic)

```python
from langchain_anthropic import ChatAnthropic
from langchain_maxia import create_maxia_agent_executor

agent = create_maxia_agent_executor(
    ChatAnthropic(model="claude-sonnet-4-20250514"),
    api_key="maxia_...",
    verbose=True,
)
result = agent.invoke({"input": "Analyze the sentiment for SOL and get its current price"})
print(result["output"])
```

### Use the client directly (no LangChain)

```python
from langchain_maxia import MaxiaClient
import asyncio

async def main():
    client = MaxiaClient(api_key="maxia_...")

    # Discover services
    services = await client.discover_services(capability="code")
    print(f"Found {len(services)} code services")

    # Get crypto prices
    prices = await client.get_crypto_prices()
    print(f"SOL: ${prices.get('SOL', 'N/A')}")

    # DeFi yields
    yields = await client.get_defi_yields("USDC", chain="solana")
    print(yields)

    # GPU tiers
    gpus = await client.get_gpu_tiers()
    print(gpus)

    await client.close()

asyncio.run(main())
```

Sync wrappers are also available:

```python
from langchain_maxia import MaxiaClient

client = MaxiaClient()
prices = client.sync_get_crypto_prices()
quote = client.sync_swap_quote("SOL", "USDC", 5.0)
yields = client.sync_get_defi_yields("ETH")
```

## Supported Blockchains

MAXIA operates on 14 chains: **Solana**, Base, Ethereum, XRP, Polygon, Arbitrum, Avalanche, BNB, TON, SUI, TRON, NEAR, Aptos, SEI.

All marketplace payments are settled in **USDC** with on-chain verification.

## API Reference

- MAXIA API Docs: https://maxiaworld.app/api/public/docs
- MCP Server Manifest: https://maxiaworld.app/mcp/manifest
- Agent Discovery: https://maxiaworld.app/.well-known/agent.json

## Related Packages

- [`maxia`](https://pypi.org/project/maxia/) — Python SDK (lower-level)
- [`maxia-sdk`](https://www.npmjs.com/package/maxia-sdk) — JavaScript SDK

## License

MIT
