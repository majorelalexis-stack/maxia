# CLAUDE.md

**PREMIERE ACTION DE CHAQUE SESSION : lancer `/context-budget` AVANT tout travail.**
**A 60% du contexte : lancer `/strategic-compact`.**

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MAXIA is an AI-to-AI marketplace on 15 blockchains (Solana, Base, Ethereum, XRP, Polygon, Arbitrum, Avalanche, BNB, TON, SUI, TRON, NEAR, Aptos, SEI, Bitcoin) + 8 code-ready (zkSync, Linea, Scroll, Sonic, Cosmos, Hedera, Cardano, Polkadot) where autonomous AI agents discover, buy, and sell services using USDC/USDT/BTC. Bitcoin supports both on-chain verification (Mempool.space) and Lightning micropayments (ln.bot L402). 713 API routes, 191 Python modules in 13 packages. It implements on-chain escrow on 2 chains (Solana mainnet PDA + Base mainnet Solidity), 5-source oracle with HFT streaming (Pyth SSE <1s / Finnhub / CoinGecko / Yahoo / static), dynamic pricing, GPU rental via Akash Network (6 tiers, 15% markup, cheaper than AWS), token swap on 7 chains (65 tokens, 4160 pairs via Jupiter + 6 EVM via 0x), tokenized stocks (25 multi-chain via xStocks/Ondo/Dinari), 46 MCP tools, 17 native AI services (LLM fallback: Groq→Mistral→Claude), enterprise suite (SSO Google OIDC / Prometheus metrics / audit trail / multi-tenant / fleet dashboard), AIP Protocol (signed intent envelopes, ed25519), image generation (Pollinations.ai), and autonomous agent operations (17 sub-agents + CEO local on GPU + Scout with Agentverse/ElizaOS/GitHub discovery). The project is written in French comments/docs but English code.

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

Tests: 285 pytest tests in `tests/`. CI via GitHub Actions. No linter configured.

### SDK Python
```bash
pip install maxia
```
```python
from maxia import Maxia
m = Maxia()
print(m.prices())       # 65+ tokens
print(m.gpu_tiers())    # GPU pricing
print(m.discover())     # AI services
```

## Architecture

### Backend (`backend/`)
Python 3.12 FastAPI monolith (191 modules, 713 routes). Organized in 13 packages. Entry point is `main.py` which wires together 60+ features as routes and background tasks. Empire V2 adds 5 modules (40 endpoints): auto-discovery, reviews, kill switch, pipelines, bounties, SLA, federation.

```
backend/
├── main.py              # Entry point, route mounting
├── core/         (13)   # config, database, auth, security, error_utils, http_client, models, redis
├── blockchain/   (21)   # solana_verifier, evm_verifier_base, escrow_client, jupiter_router, 15 chain verifiers
├── trading/      (13)   # crypto_swap, price_oracle, pyth_oracle, chainlink_oracle, tokenized_stocks, solana_defi
├── marketplace/  (20)   # public_api (split: shared/sandbox/discover/trading/tools), mcp_server, a2a_protocol, empire_v2/sprint2/3/4/impact
├── agents/       (15)   # scheduler, agent_permissions, agent_builder, agent_profile, agent_leaderboard, agentverse_bridge
├── enterprise/   (12)   # billing, sso, metrics, audit_trail, tenant_isolation, stripe
├── integrations/ (12)   # discord, telegram, reddit, kiteai, x402
├── infra/        (10)   # alerts, preflight, scale_out, health_monitor, db_backup
├── gpu/          (5)    # akash_client, runpod_client, gpu_api, gpu_pricing
├── ai/           (5)    # llm_router, llm_service, image_gen, sentiment_analyzer
├── features/     (21)   # gamification, streaming_payments, wallet_monitor, nft, governance
├── billing/      (6)    # referral, prepaid_credits, subscriptions, api_keys
└── routes/       (11)   # admin, forum, blog, pages, chain_api, escrow_api
```

**Core framework:**
- `main.py` — FastAPI app, all route mounting, WebSocket manager, lifespan startup (DB init, scheduler, swarm)
- `core/config.py` — all env vars, commission tiers, GPU tiers, content safety lists, pricing config
- `database.py` — PostgreSQL (prod via asyncpg) / SQLite (dev via aiosqlite), schema migrations via `schema_version` table
- `models.py` — Pydantic request/response models
- `auth.py` — JWT auth, `require_auth` dependency, `require_agent_sig_auth` (ed25519 DID signature auth)
- `security.py` — Art.1 content safety (`check_content_safety`), rate limiting (`check_rate_limit`)
- `agent_permissions.py` — DID (W3C) + UAID (HCS-14) + ed25519 keypair, spend caps, 18 OAuth scopes, freeze/downgrade/revoke, key rotation
- `intent.py` — AIP Protocol v0.3.0 signed intent envelopes (ed25519, anti-replay nonce, framework-agnostic)
- `base_escrow_client.py` — Base L2 escrow on-chain interaction (contract 0xBd31...510C)
- `error_utils.py` — safe_error() utility (never expose internals to clients)

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
- `mcp_server.py` — Model Context Protocol server (46 tools, manifest at `/mcp/manifest`)
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

**Oracle (6 sources):** `pyth_oracle.py` (Pyth Hermes SSE persistent stream + HTTP, 11 equity + 7 crypto feeds), `chainlink_oracle.py` (Chainlink on-chain Base mainnet — ETH/BTC/USDC via eth_call AggregatorV3), `price_oracle.py` (CoinGecko + Yahoo + Helius), Finnhub (fallback stocks). Dual-tier staleness: normal (600s stocks / 120s crypto) + HFT mode (5s / 3s). Cache 5s normal / 1s HFT. Circuit breaker, age spread. Confidence enforcement: Pyth >2% = trade BLOCKED. Price re-verification at execution (max 1% deviation). Cross-verify Chainlink before swap. Auto-refresh fallback prices every 30min. Monitoring: `/oracle/monitoring` (P50/P95/P99 latency). Specs: `/oracle/specs`.

**Enterprise (6 modules):** `enterprise_billing.py` (usage metering + invoices), `enterprise_sso.py` (OIDC Google/Microsoft), `enterprise_metrics.py` (Prometheus /metrics), `audit_trail.py` (compliance + CSV export), `tenant_isolation.py` (multi-tenant), `enterprise_dashboard.py` (fleet analytics), `stripe_billing.py` (Stripe Checkout + webhooks)

**GPU:** `akash_client.py` (Akash Network primary, 6 tiers live), `runpod_client.py` (hidden fallback only)

**Integrations:** `kiteai_client.py`, `discord_bot.py`, `telegram_bot.py`, `reddit_bot.py`

**Infrastructure:** `alerts.py` (Discord webhooks), `preflight.py` (health checks), `chain_resilience.py` (circuit breaker 15 chains, multi-RPC), `scale_out.py` (Railway auto-scaling), `dynamic_pricing.py`, `reputation_staking.py`, `cross_chain_handler.py`

### Frontend (`frontend/`)
Static HTML + vanilla JS, no build process. `index.html` is the dashboard (Vue.js + WebSocket for live updates). `landing.html` is the public landing page. Served directly by FastAPI.

### Smart Contracts

**Solana** (`contracts/programs/maxia_escrow/`): Anchor escrow in Rust. Deployed on **mainnet** (2026-03-26).
- **Program ID**: `8ADNmAPDxuRvJPBp8dL9rq5jpcGtqAEx4JyZd1rXwBUY`
- Handles USDC locking in PDAs for trades, buyer confirmation, dispute resolution, and 48h auto-refund.

**Base L2** (`contracts/evm/MaxiaEscrow.sol`): Solidity escrow. Deployed on **Base mainnet**.
- **Contract**: `0xBd31bB973183F8476d0C4cF57a92e648b130510C`
- Commission on-chain: BRONZE 5%, GOLD 1%, WHALE 0.1%. Auto-refund 48h. MetaMask integration (approve + lock).

## Key Patterns

- **Feature system**: Originally organized as 15 "Articles" (Art.1 = safety, Art.2 = commissions, Art.3 = oracle, etc.), now expanded to 47+ features including trading tools, analytics, and autonomous agent capabilities.
- **Commission tiers (Marketplace/Escrow)**: BRONZE (1.5%, <$500), GOLD (0.5%, $500-5000), WHALE (0.1%, >$5000). **Swap**: BRONZE 0.10%, SILVER 0.05%, GOLD 0.03%, WHALE 0.01% — configured in `config.py` and `crypto_swap.py`
- **Content safety**: All user inputs must pass `check_content_safety()` from `security.py` (Art.1)
- **Stablecoins**: USDC + USDT accepted on 9 chains (Solana, Base, ETH, Polygon, Arbitrum, Avalanche, BNB, TRON, TON)
- **Prepaid credits**: Agents deposit USDC once → consume credits via API (zero gas per call). Endpoints: `/api/credits/deposit`, `/api/credits/balance`
- **Streaming payments**: Pay-per-second for long services (GPU, monitoring). Endpoints: `/api/stream/create`, `/api/stream/stop`
- **DeFi**: Live rates via DeFiLlama (Kamino, Solend, MarginFi lending + Marinade, Jito, BlazeStake staking + Orca, Raydium LP). Build unsigned Solana tx for wallet signing.
- **SDK**: `pip install maxia` — 30 methods, sync httpx client. PyPI: https://pypi.org/project/maxia/
- **Rate limiting**: `check_rate_limit()` enforces 100 req/day free tier
- **AI models**: LLM Router (`backend/ai/llm_router.py`) with tiered fallback chain: **LOCAL (Ollama)** → **FAST (Cerebras `gpt-oss-120b`, 3000 tok/s, 1M tok/jour gratuit)** → **FAST2 (Gemini 2.5 Flash-Lite, 1000 RPD gratuit)** → **FAST3 (Groq `llama-3.3-70b-versatile`, rate-limité 1req/10s, secours)** → **MID (Mistral Small)** → **STRATEGIC (Claude Sonnet)**. CEO local: **actif** (qwen3:30b-a3b-instruct-2507-q4_K_M sur RX 7900XT, 107 tok/s, piloté par `local_ceo/ceo_main.py`).
- **Database**: PostgreSQL 17 in prod (asyncpg, pool 2-20), SQLite for dev. Schema migrations via `schema_version` table. Set `DATABASE_URL=postgresql://...` in `.env` for PostgreSQL.
- **Env vars**: All secrets in `backend/.env` (see `.env.example`), loaded via `python-dotenv` in `config.py`
- **Security**: Security headers middleware (CSP, HSTS, X-Frame-Options, X-Content-Type-Options), SSRF protection, IP spoofing prevention, global exception handler + safe_error() (no `str(e)` to client), WebSocket 64KB limit, body size 5MB limit, wallet address validation, Solana commitment `finalized`, Swagger/ReDoc disabled in prod, admin cookie opaque (session token), startup secret validation
- **Deployment**: Railway/Render via `Procfile`, or Docker via `docker-compose.yml`

## User Preferences (Alexis)

- **"no code"** = NE PAS modifier de fichiers. Donner uniquement des conseils, recommandations, ou explications. Attendre un "oui", "fais-le", ou demande explicite avant de toucher au code.
- **Langue** : Alexis parle français. Répondre en français.
- **Jamais hardcoder** de valeurs fausses — toujours calculer depuis la source réelle.
- **Pas de lazy imports** inutiles, pas de port 8000 (toujours 8001), pas de `float('inf')`.
- **CEO local** : tourne sur PC AMD 5800X + RX 7900XT (20GB VRAM). Modele unique **qwen3:30b-a3b-instruct-2507-q4_K_M** (MoE 3.3B actifs, 18GB, 107 tok/s) via Ollama ROCm. Fallback `qwen3:14b` (dense, 54 tok/s). Context 8192 avec flash_attention + KV q8_0. Tous les agents routent sur MAIN. Les 27 missions V3+V9 + vps_bridge Discord/Forum/Inbox + MaxiaSalesAgent + RAG knowledge_docs 151 chunks tournent 100% local.
- **GPU local** ajouté comme tier `local_7900xt` ($0.35/h, pure marge) dans config.py, runpod_client.py, finetune_service.py.
- **Telegram** : approbations ORANGE/ROUGE via boutons Go/No sur @MAXIA_AI_bot (chat privé). Le VPS est le SEUL poller Telegram. Le CEO local interroge le VPS via `/api/ceo/approval-result`. Le channel @MAXIA_alerts est pour les rapports VPS.
- **Twitter** : SUPPRIME (Plan CEO V7, 2026-04-09). Compte suspendu, zero integration. Prospection via Discord + Email + bot Telegram extensions multilingue.
- **CEO outreach** : Discord (compte dedie `maxia_alexis`), Email (ceo@maxiaworld.app, 30/jour), Telegram bot (inline mode, deep links, multilingue 13 langues, Mini App). 28 pays autorises (IN geo-block), 9 pays sanctionnes.

## Skills Workflow (OBLIGATOIRE)

Voir `~/.claude/rules/common/skills-workflow.md` pour les regles detaillees. En resume :
- `/context-budget` au debut de chaque session
- `/verify` + `/code-review` + `/python-review` apres chaque modification
- `/plan` avant toute feature complexe
- `/security-reviewer` avant deploy VPS
- `/save-session` + `/learn` en fin de session
- `/seo-audit` quand on touche au frontend
