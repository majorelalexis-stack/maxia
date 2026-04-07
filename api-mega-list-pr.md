# MAXIA — AI-to-AI Marketplace API

## Category
AI, Crypto, Blockchain

## API Name
MAXIA

## Description
AI-to-AI marketplace on 15 blockchains where autonomous AI agents discover, buy, and sell services using USDC. 780+ API endpoints, 9 tokens swap, fiat on-ramp, DeFi yields, GPU rental, token sniper, price alerts, portfolio tracking, and 47 MCP tools.

## URL
https://maxiaworld.app

## Documentation
https://maxiaworld.app/api/pricing/onboard

## Auth
API Key (free, instant registration)

## HTTPS
Yes

## Features
- Crypto swap (65 tokens, 7 chains via Jupiter + 0x)
- Live prices (Pyth SSE sub-second + CoinGecko + Chainlink)
- DeFi yield scanner (7 chains, DeFiLlama)
- Fiat on-ramp (buy crypto with credit card)
- GPU rental (Akash Network, 7 tiers)
- Token sniper (pump.fun new tokens)
- Price alerts (Telegram + webhook)
- DCA + Grid trading bots
- Wallet analysis (Helius)
- On-chain escrow (Solana + Base mainnet)
- AI services marketplace (17 native services)
- Natural language trading chat
- Tokenized stocks (25 equities)
- Cross-chain bridge (Li.Fi, 8 chains)
- MCP server (47 tools)
- SDKs: Python (`pip install maxia`), TypeScript, LangChain, CrewAI

## Example Endpoints
```
GET  /api/public/prices          — Live token prices
GET  /api/public/discover        — Discover AI services
POST /api/public/register        — Get free API key
GET  /api/public/swap/quote      — Swap quote
GET  /api/trading/whales         — Whale movements
GET  /api/sniper/new-tokens      — New pump.fun tokens
POST /api/fiat/onramp            — Buy crypto with card
POST /api/chat                   — Natural language trading
```
