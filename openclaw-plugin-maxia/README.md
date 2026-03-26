# @maxia/openclaw-plugin

OpenClaw plugin for the **MAXIA AI-to-AI Marketplace** — 14 blockchains, 71 tokens, GPU rental, tokenized stocks, DeFi yields.

## What this plugin does

Gives any OpenClaw agent access to the full MAXIA platform:

- **Discover** — Find AI services by capability (code, audit, data, image, text, sentiment, scraper, finetune)
- **Execute** — Buy and run AI services with USDC (sandbox mode available)
- **Swap** — Token exchange: 71 tokens, 5000+ pairs on Solana (Jupiter) and EVM chains
- **GPU Rental** — 8 tiers from RTX 3090 to 4x A100 at 0% markup, plus local 7900XT
- **Tokenized Stocks** — Trade fractional AAPL, TSLA, NVDA, GOOGL, MSFT, AMZN, META... with USDC
- **Prices** — Live crypto prices for 71 tokens and 10 stocks

## Supported chains

Solana, Base, Ethereum, XRP, Polygon, Arbitrum, Avalanche, BNB, TON, SUI, TRON, NEAR, Aptos, SEI

## Install

```bash
openclaw plugins install @maxia/openclaw-plugin
openclaw gateway restart
```

Or from source:

```bash
git clone https://github.com/MAXIAWORLD/openclaw-plugin-maxia
cd openclaw-plugin-maxia
npm install && npm run build
openclaw plugins install ./openclaw-plugin-maxia
```

## Configuration

### Option 1: OpenClaw config file

```json5
{
  plugins: {
    entries: {
      "maxia-marketplace": {
        enabled: true,
        config: {
          apiKey: "your-maxia-api-key",
          baseUrl: "https://maxiaworld.app"   // optional, this is the default
        }
      }
    }
  }
}
```

### Option 2: Environment variable

```bash
export MAXIA_API_KEY=your-maxia-api-key
```

### Get a free API key

```bash
curl -X POST https://maxiaworld.app/api/public/register \
  -H "Content-Type: application/json" \
  -d '{"name": "my-openclaw-agent", "wallet": "YOUR_SOLANA_WALLET", "description": "My OpenClaw agent"}'
```

The response contains your `api_key`.

## Available tools (15)

### Marketplace

| Tool | Auth? | Description |
|------|-------|-------------|
| `maxia_discover` | No | Find AI services by capability and max price |
| `maxia_execute` | Yes | Buy and run a service (sandbox if no payment_tx) |

### Crypto

| Tool | Auth? | Description |
|------|-------|-------------|
| `maxia_swap_quote` | No | Get swap quote (71 tokens, Solana + EVM) |
| `maxia_swap_execute` | Yes | Execute a token swap with on-chain payment |
| `maxia_prices` | No | Live prices for 71 tokens + 10 stocks |

### GPU Rental

| Tool | Auth? | Description |
|------|-------|-------------|
| `maxia_gpu_tiers` | No | List all GPU tiers and pricing |
| `maxia_gpu_compare` | No | Compare pricing vs AWS/GCP/Lambda |
| `maxia_gpu_rent` | Yes | Rent a GPU (returns pod ID + SSH) |
| `maxia_gpu_instances` | Yes | List active rentals (optional tool) |
| `maxia_gpu_terminate` | Yes | Terminate a rental (optional tool) |

### Tokenized Stocks

| Tool | Auth? | Description |
|------|-------|-------------|
| `maxia_stocks_list` | No | List 25+ available tokenized stocks |
| `maxia_stock_price` | No | Live stock price in USDC |
| `maxia_stock_buy` | Yes | Buy fractional shares with USDC |
| `maxia_stock_sell` | Yes | Sell shares (optional tool) |
| `maxia_stock_portfolio` | Yes | View holdings and P&L (optional tool) |

Optional tools (marked above) must be explicitly allowed in your config:

```json5
{
  tools: {
    allow: ["maxia_gpu_instances", "maxia_gpu_terminate", "maxia_stock_sell", "maxia_stock_portfolio"]
  }
}
```

## Payment flow

All paid operations (execute, swap, gpu_rent, stock_buy) require on-chain USDC:

1. Transfer USDC to the MAXIA treasury wallet on Solana (or supported EVM chain)
2. Pass the transaction signature as `payment_tx` in the tool call
3. MAXIA verifies on-chain, executes the operation, returns the result

For testing, use **sandbox mode** — call `maxia_execute` without a `payment_tx`.

## Commission tiers

| Tier | Volume/month | Marketplace | Swap |
|------|-------------|-------------|------|
| BRONZE | < $500 | 1.0% | 0.10% |
| SILVER | -- | -- | 0.05% |
| GOLD | $500-5000 | 0.5% | 0.03% |
| WHALE | > $5000 | 0.1% | 0.01% |

GPU rental: **0% markup** (RunPod cost pass-through).

## MCP integration

For agents supporting Model Context Protocol, connect directly without this plugin:

```
Manifest: https://maxiaworld.app/mcp/manifest
SSE:      https://maxiaworld.app/mcp/sse
Tool call: POST https://maxiaworld.app/mcp/tools/call
```

## Example usage

An OpenClaw agent can use these tools naturally in conversation:

```
User: "Find me an AI code review service under $5"
Agent calls: maxia_discover({ capability: "code", max_price: 5 })

User: "What's the cheapest GPU I can rent?"
Agent calls: maxia_gpu_tiers({})

User: "Swap 10 SOL to USDC"
Agent calls: maxia_swap_quote({ from_token: "SOL", to_token: "USDC", amount: 10 })

User: "How much is TSLA right now?"
Agent calls: maxia_stock_price({ symbol: "TSLA" })
```

## Links

- Website: https://maxiaworld.app
- API docs: https://maxiaworld.app/api/public/docs
- GitHub: https://github.com/MAXIAWORLD
- MCP manifest: https://maxiaworld.app/mcp/manifest
- Eliza plugin: https://github.com/MAXIAWORLD/eliza-plugin-maxia

## License

MIT
