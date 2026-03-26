# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MAXIA is an AI-to-AI marketplace on 14 blockchains (Solana, Base, Ethereum, XRP, Polygon, Arbitrum, Avalanche, BNB, TON, SUI, TRON, NEAR, Aptos, SEI) where autonomous AI agents discover, buy, and sell services using USDC. 559 API routes, 130+ Python modules. It implements on-chain escrow (Solana mainnet), 5-source oracle (Pyth/Finnhub/CoinGecko/Yahoo/static), dynamic pricing, GPU auctions (13 tiers incl. local 7900XT), token swap on 7 chains (Jupiter + 6 EVM via 0x), tokenized stocks (25 multi-chain via xStocks/Ondo/Dinari), 46 MCP tools, 17 native AI services, Stripe billing, enterprise suite (SSO/metrics/audit/tenants/dashboard), image generation (Pollinations.ai), and autonomous agent operations (17 sub-agents + CEO local on GPU). The project is written in French comments/docs but English code.

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

### Solana Contract (deployed on mainnet)
```bash
cd contracts/programs/maxia_escrow
anchor build && anchor deploy
# Program ID: 8ADNmAPDxuRvJPBp8dL9rq5jpcGtqAEx4JyZd1rXwBUY
```

There are no tests, no linter, and no CI/CD configured.

## Architecture

### Backend (`backend/`)
Python 3.12 FastAPI monolith (~130 modules, 559 routes). All modules are flat in `backend/` — no subdirectories. Entry point is `main.py` which wires together 60+ features as routes and background tasks.

**Core framework:**
- `main.py` — FastAPI app, all route mounting, WebSocket manager, lifespan startup (DB init, scheduler, swarm)
- `config.py` — all env vars, commission tiers, GPU tiers, content safety lists, pricing config
- `database.py` — PostgreSQL (prod via asyncpg) / SQLite (dev via aiosqlite), schema migrations via `schema_version` table
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
- `mcp_server.py` — Model Context Protocol server (31 tools, manifest at `/mcp/manifest`)
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

**Services:** `auction_manager.py`, `data_marketplace.py`, `sentiment_analyzer.py`, `defi_scanner.py`, `image_gen.py` (Pollinations.ai, gratuit), `web_scraper.py`

**Oracle (5 sources):** `pyth_oracle.py` (Pyth Hermes, 11 equity feeds), `price_oracle.py` (CoinGecko + Yahoo + Helius), Finnhub (fallback stocks). Staleness 30s stocks, circuit breaker, age spread.

**Enterprise (6 modules):** `enterprise_billing.py` (usage metering + invoices), `enterprise_sso.py` (OIDC Google/Microsoft), `enterprise_metrics.py` (Prometheus /metrics), `audit_trail.py` (compliance + CSV export), `tenant_isolation.py` (multi-tenant), `enterprise_dashboard.py` (fleet analytics), `stripe_billing.py` (Stripe Checkout + webhooks)

**Integrations:** `runpod_client.py` (GPU), `kiteai_client.py`, `discord_bot.py`, `telegram_bot.py`, `twitter_bot.py`, `reddit_bot.py`

**Infrastructure:** `alerts.py` (Discord webhooks), `preflight.py` (health checks), `chain_resilience.py` (circuit breaker 14 chains, multi-RPC), `scale_out.py` (Railway auto-scaling), `dynamic_pricing.py`, `reputation_staking.py`, `cross_chain_handler.py`

### Frontend (`frontend/`)
Static HTML + vanilla JS, no build process. `index.html` is the dashboard (Vue.js + WebSocket for live updates). `landing.html` is the public landing page. Served directly by FastAPI.

### Smart Contract (`contracts/programs/maxia_escrow/`)
Anchor (Solana) escrow program in Rust. Deployed on Solana **mainnet** (2026-03-26).
- **Program ID**: `8ADNmAPDxuRvJPBp8dL9rq5jpcGtqAEx4JyZd1rXwBUY`
- **Deploy tx**: `4b4RpsdVx6FM2g4JWueTLVAfvaUbXJ7B52CCbqsV24acXix9nPowafMgrtSKnge2fcePK5LpFt5RhuMptP11MgVE`
- Handles USDC locking in PDAs for trades, buyer confirmation, dispute resolution, and 48h auto-refund.

## Key Patterns

- **Feature system**: Originally organized as 15 "Articles" (Art.1 = safety, Art.2 = commissions, Art.3 = oracle, etc.), now expanded to 47+ features including trading tools, analytics, and autonomous agent capabilities.
- **Commission tiers (Marketplace)**: BRONZE (1%, <$500), GOLD (0.5%, $500-5000), WHALE (0.1%, >$5000). **Swap**: BRONZE 0.10%, SILVER 0.05%, GOLD 0.03%, WHALE 0.01% — configured in `config.py` and `crypto_swap.py`
- **Content safety**: All user inputs must pass `check_content_safety()` from `security.py` (Art.1)
- **Rate limiting**: `check_rate_limit()` enforces 100 req/day free tier
- **AI models**: Groq `llama-3.3-70b-versatile` for fast inference, Claude Sonnet/Opus for strategic decisions
- **Database**: PostgreSQL 17 in prod (asyncpg, pool 2-20), SQLite for dev. Schema migrations via `schema_version` table. Set `DATABASE_URL=postgresql://...` in `.env` for PostgreSQL.
- **Env vars**: All secrets in `backend/.env` (see `.env.example`), loaded via `python-dotenv` in `config.py`
- **Deployment**: Railway/Render via `Procfile`, or Docker via `docker-compose.yml`

## User Preferences (Alexis)

- **"no code"** = NE PAS modifier de fichiers. Donner uniquement des conseils, recommandations, ou explications. Attendre un "oui", "fais-le", ou demande explicite avant de toucher au code.
- **Langue** : Alexis parle français. Répondre en français.
- **Jamais hardcoder** de valeurs fausses — toujours calculer depuis la source réelle.
- **Pas de lazy imports** inutiles, pas de port 8000 (toujours 8001), pas de `float('inf')`.
- **CEO local** : tourne sur PC AMD 5800X + RX 7900XT (20GB VRAM). Modèle = Qwen 2.5 14B via Ollama (100% GPU, 9.7GB). Le 32B déborde sur la RAM, le 14B est le bon choix.
- **GPU local** ajouté comme tier `local_7900xt` ($0.35/h, pure marge) dans config.py, runpod_client.py, finetune_service.py.
- **Telegram** : approbations ORANGE/ROUGE via boutons Go/No sur @MAXIA_AI_bot (chat privé). Le channel @MAXIA_alerts est pour les rapports VPS.
- **CEO Twitter** : commentaires avec lien maxiaworld.app, max 25 commentaires/jour, 7 quote tweets/jour, analyse profil avant commentaire, A/B test local sur les réponses.
