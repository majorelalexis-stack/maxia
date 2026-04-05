# autogen-maxia

MAXIA AI marketplace tools for [Microsoft AutoGen](https://github.com/microsoft/autogen) 0.4+ — swap crypto, rent GPUs, DeFi yields, AI services on 15 blockchains.

## Installation

```bash
pip install autogen-maxia
```

## Quick Start

```python
from autogen_maxia import get_maxia_tools
from autogen_agentchat.agents import AssistantAgent
from autogen_ext.models.openai import OpenAIChatCompletionClient

# Get all 8 MAXIA tools as FunctionTool instances
tools = get_maxia_tools()

# Create an AutoGen agent with MAXIA capabilities
client = OpenAIChatCompletionClient(model="gpt-4o")
agent = AssistantAgent("maxia-agent", tools=tools, model_client=client)
```

## Available Tools

| Tool | Description |
|------|-------------|
| `maxia_discover` | Search AI services on the marketplace |
| `maxia_get_price` | Live crypto prices (65 tokens, Pyth + Chainlink) |
| `maxia_swap_quote` | Swap quotes (65 tokens, 7 chains, 4160 pairs) |
| `maxia_gpu_tiers` | GPU rental pricing (Akash Network, 6 tiers) |
| `maxia_defi_yields` | Best DeFi yields across 15 chains |
| `maxia_sentiment` | Crypto market sentiment analysis |
| `maxia_buy_service` | Execute AI services (sandbox mode) |
| `maxia_wallet_analysis` | Analyze Solana wallets |

## Use Individual Tools

```python
from autogen_maxia import maxia_get_price, maxia_swap_quote

# Direct function calls (no agent needed)
print(maxia_get_price("SOL"))
print(maxia_swap_quote("SOL", "USDC", 10))
```

## Configuration

Set `MAXIA_API_KEY` in your environment (optional — free endpoints work without a key):

```bash
export MAXIA_API_KEY=your-api-key
```

Get a free API key at [maxiaworld.app](https://maxiaworld.app).

## Links

- [MAXIA Website](https://maxiaworld.app)
- [MAXIA SDK](https://pypi.org/project/maxia/)
- [AutoGen Docs](https://microsoft.github.io/autogen/)
