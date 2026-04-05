# composio-maxia

MAXIA AI marketplace tools for [Composio](https://composio.dev) — swap crypto, rent GPUs, DeFi yields, AI services on 15 blockchains.

## Installation

```bash
pip install composio-maxia
```

## Usage

```python
from composio_maxia import get_maxia_tools

# Get all 8 MAXIA tools
tools = get_maxia_tools()

# Use with any Composio-supported framework (LangChain, CrewAI, AutoGen, etc.)
```

Or use individual actions:

```python
from composio_maxia import maxia_swap_quote, maxia_gpu_tiers, maxia_defi_yields

# Get swap quote
quote = maxia_swap_quote(from_token="SOL", to_token="USDC", amount=10)

# List GPU tiers
gpus = maxia_gpu_tiers()

# Best DeFi yields
yields = maxia_defi_yields(asset="USDC")
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

## Configuration

Set `MAXIA_API_KEY` in your environment (optional — free endpoints work without a key):

```bash
export MAXIA_API_KEY=your-api-key
```

Get a free API key at [maxiaworld.app](https://maxiaworld.app).

## Links

- [MAXIA Website](https://maxiaworld.app)
- [MAXIA SDK](https://pypi.org/project/maxia/)
- [Composio Docs](https://docs.composio.dev/)
