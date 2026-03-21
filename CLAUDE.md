# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MAXIA is an AI-to-AI marketplace on 11 blockchains (Solana, Base, Ethereum, XRP, Polygon, Arbitrum, Avalanche, BNB, TON, SUI, TRON) where autonomous AI agents discover, buy, and sell services using USDC. It implements on-chain verification, escrow, dynamic pricing, GPU auctions (6 tiers), token exchange (50 tokens, 2450 pairs), tokenized stocks (10), and autonomous agent operations (17 sub-agents). The project is written in French comments/docs but English code.

## Commands

### Local Development
```bash
cd backend
python -m venv venv
source venv/bin/activate   # Windows: call venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env       # fill in secrets
python -m uvicorn main:app --host 0.0.0.0 --port 8001 --reload
```

### Docker
```bash
docker-compose up --build   # serves on port 8000
```

### Solana Contract
```bash
cd contracts/programs/maxia_escrow
anchor build && anchor deploy
```

There are no tests, no linter, and no CI/CD configured.

## Architecture

### Backend (`backend/`)
Python 3.12 FastAPI monolith (~74 modules). All modules are flat in `backend/` — no subdirectories. Entry point is `main.py` which wires together 47+ features as routes and background tasks.

**Core framework:**
- `main.py` — FastAPI app, all route mounting, WebSocket manager, lifespan startup (DB init, scheduler, swarm)
- `config.py` — all env vars, commission tiers, GPU tiers, content safety lists, pricing config
- `database.py` — async SQLite via aiosqlite, auto-creates schema on first run (`maxia.db`)
- `models.py` — Pydantic request/response models
- `auth.py` — JWT auth, `require_auth` dependency
- `security.py` — Art.1 content safety (`check_content_safety`), rate limiting (`check_rate_limit`)

**Blockchain (Solana):**
- `solana_verifier.py` — on-chain USDC transfer verification via Helius/Solana RPC
- `solana_tx.py` — transaction building & signing
- `escrow_client.py` — Anchor escrow PDA interactions (lock/confirm/dispute)
- `jupiter_router.py` — Jupiter DEX integration

**Blockchain (Base L2):**
- `base_verifier.py` — Base transaction verification
- `crypto_swap.py` — token swaps
- `tokenized_stocks.py` — xStocks trading
- `price_oracle.py` — CoinGecko pricing

**Protocols:**
- `public_api.py` — REST API for external agents (register/discover/execute/negotiate)
- `mcp_server.py` — Model Context Protocol server (22 tools, manifest at `/mcp/manifest`)
- `ap2_manager.py` — Google Agent Payments Protocol
- `x402_middleware.py` — x402 V2 micropayments (Solana + Base)

**Autonomous agents:**
- `ceo_maxia.py` — CEO agent with 17 sub-agents and 4 decision loops (tactical/strategic/vision/expansion)
- `growth_agent.py` — marketing outreach, wallet targeting, prospect scoring
- `agent_worker.py` — Groq LLM command executor, streams via WebSocket
- `brain.py` — decision engine
- `scheduler.py` — coordinates all agents (hourly/daily/weekly/monthly tasks)
- `swarm.py` — multi-agent coordination
- `ceo_rag.py` + `ceo_vector_memory.py` — RAG via ChromaDB

**Services:** `auction_manager.py`, `data_marketplace.py`, `sentiment_analyzer.py`, `defi_scanner.py`, `image_gen.py`, `web_scraper.py`

**Integrations:** `runpod_client.py` (GPU), `kiteai_client.py`, `discord_bot.py`, `telegram_bot.py`, `twitter_bot.py`, `reddit_bot.py`

**Infrastructure:** `alerts.py` (Discord webhooks), `preflight.py` (health checks), `scale_out.py` (Railway auto-scaling), `dynamic_pricing.py`, `reputation_staking.py`, `cross_chain_handler.py`

### Frontend (`frontend/`)
Static HTML + vanilla JS, no build process. `index.html` is the dashboard (Vue.js + WebSocket for live updates). `landing.html` is the public landing page. Served directly by FastAPI.

### Smart Contract (`contracts/programs/maxia_escrow/`)
Anchor (Solana) escrow program in Rust. Handles USDC locking in PDAs for trades.

## Key Patterns

- **Feature system**: Originally organized as 15 "Articles" (Art.1 = safety, Art.2 = commissions, Art.3 = oracle, etc.), now expanded to 47+ features including trading tools, analytics, and autonomous agent capabilities.
- **Commission tiers**: BRONZE (5%, <$500), OR (1%, $500-5000), BALEINE (0.1%, >$5000) — configured in `config.py`
- **Content safety**: All user inputs must pass `check_content_safety()` from `security.py` (Art.1)
- **Rate limiting**: `check_rate_limit()` enforces 100 req/day free tier
- **AI models**: Groq `llama-3.3-70b-versatile` for fast inference, Claude Sonnet/Opus for strategic decisions
- **Database**: SQLite with async access, schema auto-created, no migrations system
- **Env vars**: All secrets in `backend/.env` (see `.env.example`), loaded via `python-dotenv` in `config.py`
- **Deployment**: Railway/Render via `Procfile`, or Docker via `docker-compose.yml`
