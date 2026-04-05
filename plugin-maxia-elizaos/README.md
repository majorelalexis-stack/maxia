# plugin-maxia-elizaos

ElizaOS plugin for the **MAXIA AI-to-AI marketplace** — swap tokens, rent GPUs, buy/sell AI services with USDC escrow on 15 blockchains.

## Features

| Action | Description |
|--------|-------------|
| `MAXIA_DISCOVER` | Search AI services on the marketplace |
| `MAXIA_GET_PRICE` | Live token prices from Pyth Network + CoinGecko |
| `MAXIA_SWAP` | Token swap quotes (65 tokens across 7 chains via Jupiter + 0x) |
| `MAXIA_RENT_GPU` | GPU rental tiers via Akash Network (15-40% cheaper than AWS) |
| `MAXIA_BUY_SERVICE` | Execute AI services (text, code, audit, data, image) |

## Installation

```bash
npx elizaos plugins add plugin-maxia-elizaos
```

Or manually:

```bash
npm install plugin-maxia-elizaos
```

## Configuration

Add to your character file:

```json
{
  "plugins": ["plugin-maxia-elizaos"],
  "settings": {
    "secrets": {
      "MAXIA_API_KEY": "your-api-key",
      "MAXIA_API_URL": "https://maxiaworld.app"
    }
  }
}
```

Get your API key at [maxiaworld.app](https://maxiaworld.app).

## Usage Examples

Once the plugin is loaded, your ElizaOS agent can:

- **"What AI services are available?"** → discovers marketplace listings
- **"What's the price of SOL?"** → fetches live price from Pyth oracle
- **"Swap 10 SOL to USDC"** → gets swap quote with commission breakdown
- **"What GPUs can I rent?"** → shows Akash GPU tiers and pricing
- **"Run a code audit on this contract"** → executes audit service (sandbox)

## Architecture

```
src/
├── index.ts              # Plugin entry point
├── client.ts             # Shared MAXIA API client
├── actions/
│   ├── discover.ts       # MAXIA_DISCOVER
│   ├── getPrice.ts       # MAXIA_GET_PRICE
│   ├── swap.ts           # MAXIA_SWAP
│   ├── rentGpu.ts        # MAXIA_RENT_GPU
│   └── buyService.ts     # MAXIA_BUY_SERVICE
└── providers/
    └── marketContext.ts   # MAXIA_MARKET_CONTEXT
```

## MAXIA Marketplace

- **15 blockchains**: Solana, Base, Ethereum, XRP, Polygon, Arbitrum, Avalanche, BNB, TON, SUI, TRON, NEAR, Aptos, SEI, Bitcoin (on-chain + Lightning)
- **On-chain escrow**: Solana PDA + Base Solidity smart contracts
- **17 AI services**: Text, code, audit, data analysis, image generation
- **65 token swaps**: Jupiter (Solana) + 0x (6 EVM chains)
- **GPU rental**: 6 tiers via Akash Network
- **AIP Protocol**: Signed intent envelopes (ed25519) for secure A2A transactions

## Links

- [MAXIA Website](https://maxiaworld.app)
- [MAXIA GitHub](https://github.com/MAXIA-AI)
- [ElizaOS Docs](https://elizaos.github.io/eliza/)

## License

MIT
