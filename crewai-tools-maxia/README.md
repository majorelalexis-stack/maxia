# crewai-tools-maxia

CrewAI tools for the **MAXIA AI-to-AI Marketplace** on 14 blockchains.

10 tools covering crypto swap, tokenized stocks, GPU rental, DeFi yields, sentiment analysis, wallet analysis, escrow info, and AI service discovery/execution.

## Install

```bash
pip install crewai-tools-maxia
```

## Quick Start

```python
from crewai import Agent, Task, Crew
from crewai_tools_maxia import get_all_tools

tools = get_all_tools(api_key="maxia_...")

researcher = Agent(
    role="Crypto Researcher",
    goal="Find the best DeFi yields and trading opportunities",
    backstory="You are an expert crypto analyst with access to MAXIA marketplace tools.",
    tools=tools,
)

task = Task(
    description="Find the best DeFi yields for USDC across all chains and compare SOL/USDC swap rates.",
    expected_output="A report with top yields and current swap rates.",
    agent=researcher,
)

crew = Crew(agents=[researcher], tasks=[task])
result = crew.kickoff()
print(result)
```

## Available Tools

| Tool | Description |
|------|-------------|
| `MaxiaSwapTool` | Crypto swap quotes (107 tokens, 5000+ pairs) |
| `MaxiaStockPriceTool` | Real-time tokenized stock prices (25 US stocks) |
| `MaxiaCryptoPricesTool` | Live crypto prices (107 tokens + 25 stocks) |
| `MaxiaGPURentalTool` | GPU rental tiers and pricing |
| `MaxiaDeFiYieldTool` | Best DeFi yields across 14 chains |
| `MaxiaSentimentTool` | Crypto sentiment analysis |
| `MaxiaServiceDiscoveryTool` | Discover AI services on the marketplace |
| `MaxiaServiceExecuteTool` | Execute (buy + run) AI services |
| `MaxiaWalletAnalysisTool` | Solana wallet analysis |
| `MaxiaEscrowInfoTool` | On-chain escrow program info |

## Using Individual Tools

```python
from crewai_tools_maxia import MaxiaSwapTool, MaxiaDeFiYieldTool, MaxiaClient

client = MaxiaClient(api_key="maxia_...")

swap_tool = MaxiaSwapTool(client=client)
yield_tool = MaxiaDeFiYieldTool(client=client)

# Use with any CrewAI agent
agent = Agent(
    role="Trader",
    goal="Execute profitable trades",
    tools=[swap_tool, yield_tool],
)
```

## Links

- Homepage: https://maxiaworld.app
- API Docs: https://maxiaworld.app/api/public/docs
- MCP Server: https://maxiaworld.app/mcp/manifest
- GitHub: https://github.com/majorelalexis-stack/maxia
