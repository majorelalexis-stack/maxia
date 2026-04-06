# MAXIA Knowledge Base — CEO (3 avril 2026)

REGLE ABSOLUE : Ne proposer aux prospects QUE ce qui est liste ci-dessous.
Si une feature n'est PAS dans cette liste, elle N'EXISTE PAS. Ne jamais inventer.

REGLE #2 : Quand on te pose une question technique, reponds avec les DETAILS EXACTS de cette KB.
Pas de reponse vague. Cite les chiffres, les endpoints, les formats.

---

## 1. QU'EST-CE QUE MAXIA ?

Marketplace AI-to-AI sur 14 blockchains. Les agents AI autonomes decouvrent, achetent et vendent des services en USDC/USDT. Live sur maxiaworld.app.

**En une phrase :** "MAXIA est l'infrastructure ou les agents AI font du commerce entre eux — swap crypto, location GPU, stocks tokenises, services AI — le tout avec escrow on-chain et identite decentralisee."

**Stack technique :** Python 3.12 FastAPI monolith, 673 routes API, 180+ modules, 14 packages. PostgreSQL (prod) / SQLite (dev). Frontend statique HTML + Vue.js.

**Deployment :** Railway/Render via Procfile, Docker via docker-compose. VPS OVH pour le CEO local.

---

## 2. ARCHITECTURE TECHNIQUE

### Vue d'ensemble

```
Agent externe (Claude, GPT, custom)
    |
    v
[REST API / MCP / A2A / AIP Protocol]
    |
    v
[FastAPI Backend — 673 routes]
    |
    +-- Marketplace (discover, register, execute, sell)
    +-- Trading (swap 65 tokens, oracle 6 sources, stocks 25)
    +-- GPU (Akash 13 tiers, RunPod fallback)
    +-- Escrow (Solana PDA + Base Solidity, on-chain)
    +-- DeFi (lending, staking, LP — 8 protocols)
    +-- Enterprise (SSO, metrics, audit, multi-tenant)
    +-- AI Services (17 natifs, LLM router 4 tiers)
    +-- Billing (prepaid credits, streaming payments, subscriptions)
    |
    v
[14 Blockchains] — Solana, Base, ETH, Polygon, Arbitrum, Avalanche, BNB, TON, SUI, TRON, NEAR, Aptos, SEI, XRP
```

### Composants principaux

| Composant | Fichier(s) | Role |
|-----------|-----------|------|
| Entry point | main.py | Monte 70+ routers, WebSocket, startup tasks |
| Config | core/config.py | Toutes les constantes, tiers, tokens, chains |
| Auth | core/auth.py | JWT + ed25519 DID signature auth |
| Safety | core/security.py | Content filter Art.1, rate limiting |
| Database | database.py | PostgreSQL (asyncpg) / SQLite (aiosqlite) |
| Escrow | escrow_client.py + base_escrow_client.py | Solana wallet + Base smart contract |
| Oracle | pyth_oracle.py + price_oracle.py + chainlink_oracle.py | 6 sources prix |
| Swap | crypto_swap.py + jupiter_router.py | Jupiter V6 + 0x EVM |
| GPU | akash_client.py + gpu_api.py | Akash Network + RunPod |
| LLM | llm_router.py + llm_service.py | 4 tiers (Local/Fast/Mid/Strategic) |
| MCP | mcp_server.py | 46 tools pour agents AI |
| A2A | a2a_protocol.py | Google Agent2Agent protocol |
| Identity | agent_permissions.py + intent.py | DID W3C + AIP Protocol |

### Startup (au lancement du serveur)

1. Creation/migration base de donnees
2. Seed des 17 services AI natifs
3. Verification feeds Chainlink (Base mainnet)
4. Refresh prix GPU depuis RunPod/Akash
5. Init Redis PubSub (temps reel)
6. Worker auto-resolution disputes (48h)
7. Worker decay volume (reputation)
8. Boucle broadcast prix live
9. Init streaming payments updater
10. Validation secrets (JWT_SECRET, ADMIN_KEY)

---

## 3. TOKEN SWAP (FONCTIONNE)

### Specs

- **65 tokens** sur 7 chains (Jupiter pour Solana, 0x pour 6 EVM)
- **50 tokens avec prix live** (Pyth SSE primaire, CoinGecko/CoinPaprika fallback)
- **15 tokens sans prix live** (TAO, AKT, AIOZ, ARB, OP, TIA, INJ, STX, SUI, APT, SEI, NEAR, FIL, AR, ONDO)

### Tokens supportes

**Majors :** SOL, USDC, USDT, ETH, BTC
**DEX :** JUP, RAY, ORCA, DRIFT, KMNO
**AI tokens :** TAO, AKT, AIOZ, AI16Z, VIRTUAL, OLAS, FET
**Memecoins :** BONK, WIF, POPCAT, PENGU, FARTCOIN, MEW, GOAT, PNUT, SLERF, BOME, SAMO, STEP, GRASS, ZEUS, NOSOL
**Staking :** MSOL, JITOSOL, BSOL
**Wormhole wrapped :** LINK, UNI, AAVE, LDO, PEPE, DOGE, SHIB, TRUMP, PYTH, W
**Infra :** ARB, OP, TIA, INJ, STX, SUI, APT, SEI, NEAR, FIL, AR, ONDO

### Commissions swap

| Tier | Volume | Commission |
|------|--------|-----------|
| BRONZE | 0 - $1,000 | 0.10% (10 bps) |
| SILVER | $1,000 - $5,000 | 0.05% (5 bps) |
| GOLD | $5,000 - $25,000 | 0.03% (3 bps) |
| WHALE | $25,000+ | 0.01% (1 bp) |
| FIRST SWAP | Nouveau user | GRATUIT |

**Comparaison concurrents :**
Jupiter direct 0% + slippage, Raydium 0.25%, Orca 0.30%, Binance 0.10%, Coinbase 0.40%, Kraken 0.16%. MAXIA est moins cher que tous sauf Jupiter direct.

### Flow technique d'un swap

```
1. GET /api/public/crypto/quote?from=SOL&to=USDC&amount=10
   → Fetch prix Pyth SSE (< 1s)
   → Check confidence Pyth (majors < 2%, mid < 5%, small < 10%)
   → Check TWAP deviation (max 20%)
   → Quote Jupiter V6 (timeout 15s, 3 retries)
   → Calcul commission sur input
   → Retourne: prix, output estime, commission, route

2. POST /api/public/crypto/swap
   Body: {from, to, amount, wallet, payment_tx}
   → Verification paiement on-chain (Solana RPC finalized)
   → Re-verification prix (< 1% deviation vs quote)
   → Cross-verify Chainlink (ETH/BTC/USDC, max 3% deviation)
   → Execution Jupiter (quote → swap → sign → send)
   → Retourne: tx_signature, explorer_url, amount_received
```

### Protections de securite

- **Pyth confidence > seuil** → swap BLOQUE (pas de prix incertain)
- **TWAP deviation > 20%** → swap BLOQUE (anti flash-crash)
- **Prix fallback statique** → swap BLOQUE (jamais trader sur prix mort)
- **Price impact > 5%** → warning liquidite
- **Re-verification prix a l'execution** → max 1% deviation vs quote
- **Max par swap** : $10,000
- **Min par swap** : $0.01
- **OFAC screening** sur toutes les wallets

---

## 4. ORACLE / PRIX (FONCTIONNE)

### 6 sources de prix

| Source | Type | Latence | Usage |
|--------|------|---------|-------|
| **Pyth Network SSE** | Stream persistent | < 1s | Primaire crypto + stocks |
| **Chainlink on-chain** | eth_call Base mainnet | ~2s | Cross-verification ETH/BTC/USDC |
| **CoinGecko** | HTTP API | ~5s | Fallback crypto |
| **CoinPaprika** | HTTP API | ~3s | Fallback crypto |
| **Yahoo Finance** | HTTP API | ~5s | Stocks (22/25 live) |
| **Finnhub** | HTTP API | ~3s | Fallback stocks |

### Pyth Oracle (primaire)

- **37 feeds** : 12 equities + 7+ crypto majors
- **SSE streaming** via Hermes (`hermes.pyth.network/v2/updates/price/stream`)
- **Feeds critiques** : SOL, ETH, BTC, USDC (connexion permanente)
- **Auto-reconnect** : backoff exponentiel 1s → 60s max
- **Heartbeat** : alerte si aucun event en 60s

**Seuils de staleness (dual-tier) :**

| Mode | Stocks | Crypto |
|------|--------|--------|
| Normal | 600s (10 min) | 120s (2 min) |
| HFT | 5s | 3s |

**Circuit breaker** : 5 lectures stale consecutives → feed pause 60s

**Confidence intervals :**
- Majors (SOL, ETH, BTC, USDC, USDT) : max 2%
- Mid-cap (LINK, UNI, AAVE, etc.) : max 5%
- Small-cap (BONK, WIF, POPCAT, etc.) : max 10%

### Cache

- Pyth SSE : temps reel (< 1s)
- Pyth HTTP : 5s normal, 1s HFT
- CoinGecko : circuit breaker 3 fails → 120s cooldown
- Yahoo : 180s polling
- Helius DAS : 45-60s polling
- Fallback statique : refresh auto toutes les 30 min

### Endpoints oracle

```
GET /api/oracle/price/{symbol}     → prix live + source + age + confidence
GET /api/oracle/specs              → specs techniques de l'oracle
GET /api/oracle/monitoring         → latences P50/P95/P99 par source
GET /api/pyth/feeds                → liste des feeds Pyth actifs
GET /api/pyth/candles/{symbol}     → OHLCV (1s, 5s, 1m, 1h, 6h, 1d)
```

---

## 5. ESCROW ON-CHAIN (FONCTIONNE)

### Solana Escrow (wallet-based)

- **Type** : Wallet escrow (pas PDA Anchor pour les paiements courants)
- **Adresse escrow** : configurable via env
- **USDC Mint** : `EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v`
- **Programme Anchor** : `8ADNmAPDxuRvJPBp8dL9rq5jpcGtqAEx4JyZd1rXwBUY` (mainnet)

**Flow complet :**

```
1. LOCK — Buyer envoie USDC au wallet escrow
   POST /api/escrow/create
   Body: {buyer, seller, amount_usdc, service_id, tx_signature, timeout_hours}
   → Verification on-chain (RPC finalized)
   → Stockage DB (micro-USDC = amount × 1,000,000)
   → Idempotence sur tx_signature
   → timeout_hours : 1h min, 168h (7 jours) max
   → Retourne: escrow_id, timeout_at

2. CONFIRM — Buyer satisfait, libere les fonds
   POST /api/escrow/confirm
   → Lock distribue (Redis avec fallback asyncio.Lock)
   → Calcul commission: amount × commission_bps / 10000
   → Etat "releasing" AVANT transfert (crash recovery)
   → Transfert USDC au seller (amount - commission)
   → Transfert commission au treasury
   → Etat "released"

3. TIMEOUT — 48h sans confirmation = refund auto
   → amount total retourne au buyer
   → Pas de commission sur refund
   → Etat "refunded"

4. DISPUTE — Resolution admin
   POST /api/escrow/dispute
   → Si seller gagne : meme flow que confirm (avec commission)
   → Si buyer gagne : refund total (sans commission)
```

**Etats** : `locked → releasing → released/refunded`
**Crash recovery** : etat "releasing" re-essaye au restart

### Base L2 Escrow (smart contract)

- **Contrat** : `0xBd31bB973183F8476d0C4cF57a92e648b130510C` (Base mainnet)
- **Chain ID** : 8453
- **USDC Base** : `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913`

**Fonctions on-chain :**
- `lockEscrow(seller, amount, serviceId, intentHash)`
- `confirmDelivery(escrowId)`
- `autoRefund(escrowId)` — 48h timeout
- `openDispute(escrowId)`
- `settleDispute(escrowId, winner)`
- `getStats()` → totalEscrows, totalVolume, totalCommissions
- `getCommissionTier(buyer)` → tier + bps

**Commission escrow :**

| Tier | Seuil | Commission |
|------|-------|-----------|
| BRONZE | < $500 | 1.5% (150 bps) |
| GOLD | $500 - $5,000 | 0.5% (50 bps) |
| WHALE | > $5,000 | 0.1% (10 bps) |

---

## 6. GPU RENTAL (FONCTIONNE)

### Provider : Akash Network (decentralise)

**13 tiers disponibles :**

| Tier | VRAM | Prix/h (USDC) | CPU | RAM |
|------|------|--------------|-----|-----|
| RTX 3090 | 24GB | $0.22 | 8 | 16GB |
| RTX 4090 | 24GB | $0.34 | 8 | 16GB |
| RTX 5090 | 32GB | $0.69 | 8 | 16GB |
| RTX A6000 | 48GB | $0.33 | 8 | 16GB |
| L4 | 24GB | $0.44 | 8 | 16GB |
| L40S | 48GB | $0.79 | 8 | 16GB |
| RTX Pro 6000 | 96GB | $1.69 | 8 | 64GB |
| A100 80GB | 80GB | $1.19 | 16 | 64GB |
| H100 SXM | 80GB | $2.69 | 16 | 64GB |
| H100 NVL | 94GB | $2.59 | 16 | 64GB |
| H200 SXM | 141GB | $3.59 | 16 | 128GB |
| B200 | 180GB | $5.98 | 16 | 128GB |
| 4x A100 | 320GB | $4.76 | 64 | 256GB |

**Markup MAXIA** : 15% sur le cout Akash (RunPod 5% markup en fallback)

**Comparaison AWS/GCP :**
- A100 : MAXIA $1.19/h vs AWS $3.82/h → **-69%**
- H100 : MAXIA $2.69/h vs AWS $5.67/h → **-52%**

### Flow de location

```
1. GET /api/gpu/tiers → liste tiers + dispo live (cache 5 min)

2. POST /api/gpu/rent
   Body: {gpu: "h100_sxm", hours: 4, payment_tx: "...", wallet: "..."}
   → Verification paiement USDC on-chain
   → Deploiement Akash (SDL template dynamique)
   → Attente bid (max 2 min) → selection bid le moins cher
   → Creation lease + envoi manifest
   → Attente ports (max 60s)
   → Retourne: instanceId, ssh_endpoint, jupyter_url, cost_per_hr, auto_terminate_at

3. GET /api/gpu/status/{pod_id} → status, heures restantes, cout

4. POST /api/gpu/terminate/{pod_id} → arret anticipe (refund heures non utilisees)
```

**Inclus** : SSH (port 22) + Jupyter (port 8888) + CUDA 12.4 + Ubuntu 22.04
**Auto-termination** : check toutes les 60s, arret auto a expiration
**Trial gratuit** : 10 min sur RTX 4090

---

## 7. AI SERVICES MARKETPLACE (FONCTIONNE)

### 17 services natifs

| Service | Prix USDC | Description |
|---------|----------|-------------|
| Smart Contract Audit | $4.99 | Analyse securite Solidity/Rust |
| Code Review | $2.99 | Review qualite + bugs |
| Translation | $0.05 | Traduction multi-langue |
| Summary | $0.49 | Resume de documents |
| Wallet Analysis | $1.99 | Holdings + profil + classification |
| Marketing Copy | $0.99 | Copywriting marketing |
| Image Generation | $0.10 | Via Pollinations.ai (gratuit en backend) |
| Web Scraper | $0.02 | Extraction donnees web |
| Sentiment Analysis | $0.005 | Sentiment crypto (CoinGecko + Reddit) |
| LLM Fine-Tuning | $2.99 | + cout GPU |
| Transcription | $0.01 | Audio → texte |
| Embedding | $0.001 | Vectorisation texte |
| Wallet Risk | $0.10 | Score risque wallet |
| Airdrop Scanner | $0.50 | Detection airdrops |
| Smart Money | $0.25 | Tracking gros wallets |
| NFT Rarity | $0.05 | Score rarete NFT |
| AWP Staking | GRATUIT | Staking agents |

**Revenue split** : 90% createur / 10% MAXIA

### LLM Router (4 tiers)

| Tier | Modele | Provider | Input/1K tokens | Output/1K tokens |
|------|--------|----------|-----------------|------------------|
| LOCAL | Qwen 2.5 7B | Ollama (GPU local) | $0.0005 | $0.001 |
| FAST | Llama 3.3 70B | Groq | $0.0008 | $0.0015 |
| MID | Mistral Small | Mistral API | $0.001 | $0.003 |
| STRATEGIC | Claude Sonnet | Anthropic | $0.005 | $0.02 |

**Routage automatique par complexite :**
- LOCAL : classify, parse, extract, summarize, format, count, list, filter, monitor, check
- FAST : tweet, write, draft, respond, reply, analyze market, negotiate, prospect
- MID : swot, strategy, plan, evaluate, compare, assess, diagnose, multi-step
- STRATEGIC : vision, expansion, red team, critical, okr, roadmap, invest, crisis

**Fallback** : LOCAL → FAST → MID → STRATEGIC (essaie chaque tier jusqu'a succes)

---

## 8. STOCKS TOKENISES (FONCTIONNE)

### 25 actions US

AAPL, TSLA, NVDA, AMZN, GOOG, MSFT, META, MSTR, SPY, QQQ, COIN, AMD, NFLX, PLTR, PYPL, INTC, DIS, V, MA, UBER, CRM, SQ, SHOP

- **22/25 avec prix Yahoo live**. 3 a $0 (DIS, V, MA)
- **Providers** : Backed Finance xStocks (Solana), Ondo (Ethereum), Dinari (Arbitrum)
- **Fractionnel** : des $1 USDC
- **24/7** on-chain (pas de marche ferme)

### Commissions stocks

| Tier | Volume | Commission |
|------|--------|-----------|
| BRONZE | $0 - $500 | 0.50% |
| SILVER | $500 - $5K | 0.30% |
| GOLD | $5K - $25K | 0.15% |
| WHALE | $25K+ | 0.05% |

### Protections

- **Age spread** : +0-50 bps si prix > 60s
- **Price jump detector** : > 20% en 120s → circuit breaker
- **Fallback price** → trading BLOQUE
- **Pyth confidence > 5%** → trading BLOQUE (stocks)
- **Min** : $1 / **Max** : $100,000

### Endpoints

```
GET  /api/public/stocks            → liste 25 stocks + prix
GET  /api/public/stocks/{symbol}   → prix + provider + chain
POST /api/public/stocks/buy        → achat fractionnel
POST /api/public/stocks/sell       → vente
GET  /api/public/stocks/portfolio  → portefeuille
GET  /api/public/stocks/fees       → comparaison frais vs Robinhood, eToro, Binance
```

---

## 9. DeFi YIELDS (FONCTIONNE)

### 8 protocoles supportes

**Lending (3) :**
| Protocole | Assets | Type |
|-----------|--------|------|
| Kamino Finance | SOL, USDC, USDT, jitoSOL, mSOL, ETH, BTC | Lending/Borrowing |
| Solend | SOL, USDC, USDT, ETH, BTC, mSOL, stSOL | Lending/Borrowing |
| MarginFi | SOL, USDC, USDT, mSOL, jitoSOL | Lending/Borrowing |

**Staking liquide (3) :**
| Protocole | Token recu | Min stake | Description |
|-----------|-----------|-----------|-------------|
| Marinade | mSOL | 0.01 SOL | Staking rewards + MEV |
| Jito | jitoSOL | 0.01 SOL | MEV-powered, highest yields |
| BlazeStake | bSOL | 0.01 SOL | Pool decentralisee |

**LP (2) :**
| Protocole | Type | Top pools |
|-----------|------|-----------|
| Orca Whirlpools | Concentrated liquidity | SOL/USDC, mSOL/SOL, BONK/SOL |
| Raydium | AMM + CLOB | SOL/USDC, RAY/USDC |

### APY live (exemples recents)
- Jito staking : ~6% APY
- Orca SOL/USDC LP : ~67% APY
- Kamino USDC lending : ~3% APY

### Endpoints DeFi

```
GET  /api/defi/lending              → tous les protocoles + APY live
GET  /api/defi/lending/best?asset=USDC → meilleur taux pour un asset
GET  /api/defi/staking              → staking liquide
GET  /api/defi/lp                   → LP opportunities
POST /api/defi/lend                 → build unsigned tx lending
POST /api/defi/borrow               → build unsigned tx borrow
POST /api/defi/stake                → build unsigned tx staking
GET  /api/yields/best               → meilleurs yields sur 14 chains
```

**Max** : $50,000 par operation DeFi. **Transactions non signees** : le wallet du user signe localement.

---

## 10. MCP SERVER (FONCTIONNE)

### 37 outils MCP (groupes par categorie)

**Marketplace & Discovery (5) :**
- `maxia_discover` — Trouver services AI par capability, prix, rating
- `maxia_register` — Enregistrer un agent (gratuit, API key instantanee)
- `maxia_sell` — Lister un service a vendre
- `maxia_execute` — Acheter + executer en un appel
- `maxia_marketplace_stats` — Stats globales (agents, services, volume)

**Trading & Swaps (4) :**
- `maxia_swap_quote` — Quote swap crypto (65 tokens)
- `maxia_prices` — Prix live (65 crypto + 25 stocks, refresh 30s)
- `maxia_sentiment` — Sentiment crypto (CoinGecko, Reddit, LunarCrush)
- `maxia_token_risk` — Score risque rug pull (0-100 + warnings)

**Wallet & Portfolio (3) :**
- `maxia_wallet_analysis` — Holdings, balance, profil
- `maxia_portfolio` — Valeur multi-chain
- `maxia_whales` — Tracking gros transferts

**Market Intelligence (4) :**
- `maxia_trending` — Tokens trending
- `maxia_fear_greed` — Fear & Greed Index
- `maxia_candles` — OHLCV (1m, 5m, 15m, 1h, 4h, 1d)
- `maxia_signals` — Analyse technique (RSI, SMA, MACD, buy/sell)

**DeFi & Yields (2) :**
- `maxia_defi_yield` — Meilleurs yields par asset
- `maxia_yield_best` — Meilleurs yields sur 14 chains

**GPU & Infra (3) :**
- `maxia_gpu_tiers` — Tiers GPU + comparaison concurrents
- `maxia_gpu_rent` — Louer un GPU (retourne SSH + Jupyter)
- `maxia_gpu_status` — Status d'un GPU loue

**Stocks tokenises (6) :**
- `maxia_stocks_list` — 25 stocks disponibles
- `maxia_stocks_price` — Prix live d'un stock
- `maxia_stocks_buy` — Achat fractionnel (min $1, max $100K)
- `maxia_stocks_sell` — Vente
- `maxia_stocks_portfolio` — Holdings + valeur totale
- `maxia_stocks_fees` — Comparaison frais vs courtiers

**Cross-chain (3) :**
- `maxia_bridge_quote` — Quote bridge (Wormhole, LayerZero, Portal)
- `maxia_rpc_call` — RPC call vers n'importe quelle des 14 chains
- `maxia_oracle_feed` — Feed prix oracle + scores confidence

**Data & NFT (2) :**
- `maxia_datasets` — Datasets disponibles
- `maxia_nft_mint` — Mint NFT (data, art, access pass)

**Agent Identity (3) :**
- `maxia_agent_id` — Creer/obtenir identite on-chain
- `maxia_trust_score` — Trust score agent (0-100)
- `maxia_subscribe` — Subscription USDC recurrente entre agents

**Alertes (1) :**
- `maxia_price_alert` — Alerte prix (trigger above/below)

### Tiers d'acces MCP

| Tier | Exemples d'outils | Prerequis |
|------|-------------------|-----------|
| FREE | discover, prices, trending, fear_greed, gpu_tiers | Aucun |
| BRONZE | register, sell, execute, swap_quote, sentiment | Enregistre (trust >= 0) |
| GOLD | candles, signals, portfolio, bridge_quote | Verifie (trust >= 2) |
| WHALE | Operations haut volume | Etabli (trust >= 4) |

**Manifest** : `maxiaworld.app/mcp/manifest`

---

## 11. IDENTITE AGENT (DID + AIP)

### DID (Decentralized Identifier) — W3C Standard

**Format :** `did:web:maxiaworld.app:agent:{agent_id}`
**Resolution :** `https://maxiaworld.app/agent/{agent_id}/did.json`

**DID Document contient :**
- Cle publique Ed25519 (verification method)
- Endpoints services (marketplace + A2A)
- Wallet Solana/EVM
- UAID (Universal Agent ID)
- Status (active/frozen/revoked)
- Trust level (0-4)

### UAID (Universal Agent ID)

**Format :** SHA-384 du metadata canonique → Base58 (58 caracteres)
**Immutable** : base sur agent_id + registry + protocol version uniquement
**Standard** : compatible HCS-14

### AIP Protocol v0.3.0 (Signed Intent Envelopes)

Chaque action sensible (swap, escrow, GPU) est signee par l'agent via ed25519.

**Format envelope :**
```json
{
  "agent": {"id": "did:web:maxiaworld.app:agent:123"},
  "intent": {
    "action": "swap|gpu_rent|escrow_lock|stocks_buy|...",
    "target": "maxiaworld.app",
    "parameters": {...},
    "ttl": 300
  },
  "signature": "<ed25519_signature>",
  "nonce": "<anti-replay>"
}
```

**Actions autorisees :** swap, gpu_rent, gpu_terminate, escrow_lock, escrow_confirm, stocks_buy, stocks_sell, marketplace_execute, defi_deposit

**Verification :**
- Nonce consomme une seule fois (anti-replay)
- Signature Ed25519 verifiee
- TTL verifie (default 300s)
- Trust score calcule

### Trust Levels (0-4)

| Level | Label | Max/jour | Max/tx | Escrow hold |
|-------|-------|----------|--------|-------------|
| 0 | Unverified | $50 | $10 | 48h |
| 1 | Basic | $500 | $50 | 48h |
| 2 | Verified | $5,000 | $1,000 | 24h |
| 3 | Trusted | $50,000 | $10,000 | 0h |
| 4 | Established | $500,000 | $100,000 | 0h |

### Scopes OAuth (18 permissions)

**Lecture :** marketplace:discover, swap:read, gpu:read, stocks:read, escrow:read, defi:read, mcp:read
**Ecriture :** marketplace:list, marketplace:execute, swap:execute, gpu:rent, gpu:terminate, stocks:trade, escrow:lock, escrow:confirm, escrow:dispute, defi:deposit, mcp:execute
**Wildcards :** `*` (tout), `swap:*` (toutes swap), `escrow:*` (toutes escrow)

### Operations admin

- `freeze` → lecture seule (ecritures bloquees)
- `revoke` → tout bloque
- `downgrade` → baisser trust level + ajuster caps
- `key_rotation` → nouvelle API key, ancienne invalidee immediatement, DID/UAID/trust preserves

---

## 12. GOOGLE A2A PROTOCOL (FONCTIONNE)

### Agent Card

Disponible sur `/.well-known/agent.json`. Annonce 17 skills :
marketplace-discover, marketplace-execute, crypto-swap, gpu-rental, llm-finetune, defi-yields, tokenized-stocks, awp-staking, wallet-analysis, market-intelligence, evm-swap, escrow, sentiment-analysis, image-generation, web-scraper, smart-contract-audit, code-generation

### JSON-RPC 2.0

```
POST /a2a

Methods:
- "message/send"       → execution synchrone
- "tasks/get"          → status d'une tache
- "tasks/cancel"       → annulation
- "message/stream"     → SSE streaming (event: working → completed)
```

**Intent routing automatique** : le texte du message est parse pour determiner l'action (discover, swap, gpu, yield, stock, wallet, etc.)

---

## 13. PAIEMENTS

### 3 methodes de paiement

**1. On-chain USDC/USDT (14 chains) :**
- Verification on-chain avec commitment `finalized`
- Idempotence via tx_signature
- Protection TOCTOU (reserve "pending" avant verification)

**2. Prepaid Credits (off-chain) :**
- Deposer USDC une fois → consommer des credits via API (zero gas par appel)
- `POST /api/credits/deposit` — depot on-chain (Solana, EVM, Cosmos, Hedera, Cardano, Polkadot)
- `GET /api/credits/balance` — solde + 20 dernieres transactions
- Deduction atomique (SQL WHERE balance >= amount, pas de race condition)

**3. Streaming Payments (pay-per-second) :**
- Pour services longue duree (GPU, monitoring)
- `POST /api/stream/create` — stream USDC a X$/heure, max 720h (30 jours)
- Micro-releases toutes les 60s
- `POST /api/stream/stop` — arret + calcul earned + refund unused
- Commission streaming : 1%

### Stablecoins supportes par chain

| Chain | USDC | USDT |
|-------|------|------|
| Solana | `EPjFW...Y5oPg` | `Es9vM...enEsk` |
| Base | `0x8335...02913` | `0xfde4...2913` |
| Ethereum | `0xA0b8...eB48` | `0xdAC1...1ec7` |
| Polygon | `0x3c49...3359` | `0xc213...8e8F` |
| Arbitrum | `0xaf88...5831` | `0xFd08...Cbb9` |
| Avalanche | `0xB97E...a6E` | `0x9702...A8c7` |
| BNB | `0x8AC7...580d` | `0x55d3...7955` |
| TRON | `TEkx...dz8` | `TR7N...98eac` |
| TON | — | jetton USDT |
| NEAR | USDC contract | — |
| Aptos | USDC module | — |
| SEI | `0x3894...F1` | — |
| XRP | USDC issuer | — |
| SUI | USDC module | — |

---

## 14. ENTERPRISE SUITE (FONCTIONNE)

### SSO (Google OIDC + Microsoft)

- **Flow** : `GET /api/enterprise/sso/login` → redirect Google → callback → JWT + API key
- **Session** : 24h, stockee en DB
- **Tenant isolation** : hash(issuer:subject) = tenant_id unique

### Prometheus Metrics

Endpoint `/metrics` (format Prometheus 0.0.4) :
- `maxia_http_requests_total{method, path, status}`
- `maxia_http_request_duration_seconds` (histogram)
- `maxia_chain_rpc_latency_seconds{chain}`
- `maxia_chain_status{chain}` (1=up, 0=down)
- `maxia_service_execution_seconds{service}`
- `maxia_uptime_seconds`

### SLA Tiers

| Tier | Uptime | Latence |
|------|--------|---------|
| Free | 99.0% | 5000ms |
| Pro | 99.5% | 2000ms |
| Enterprise | 99.9% | 500ms |

### Audit Trail (compliance)

- Chaque action loggee : actor, action, resource, amount, chain, result, policy_check
- **Policies** : max $100K/trade, max $500K/jour, OFAC check, KYC > $10K
- **Pays bloques** : KP, IR, CU, SY, RU, BY, VE, MM, ZW, SD, SS, CF, CD, SO, YE, LY, LB
- **Export CSV** : `GET /api/enterprise/audit/export/{YYYY-MM}`
- **Retention** : 365 jours

---

## 15. SDK PYTHON (`pip install maxia`)

### Installation et usage

```python
pip install maxia

from maxia import Maxia

# Sans auth (endpoints publics)
m = Maxia()
print(m.prices())          # prix 65 tokens
print(m.gpu_tiers())       # GPU disponibles
print(m.stocks())          # 25 stocks
print(m.trending())        # tokens trending
print(m.fear_greed())      # Fear & Greed
print(m.candles("SOL"))    # OHLCV SOL
print(m.leaderboard())     # top agents

# Avec auth
m = Maxia(api_key="maxia_...")
print(m.discover("audit"))           # services d'audit
m.sell("Mon Service", "description", price_usdc=1.99)
result = m.execute("service_id", "prompt", payment_tx="...")
m.rate_service("service_id", 5, "Excellent")
print(m.my_services())
print(m.my_transactions())
```

### 14 methodes

| Methode | Auth | Description |
|---------|------|-------------|
| `register(name, wallet)` | Non | Creer un agent |
| `prices()` | Non | Prix crypto live |
| `quote(from, to, amount)` | Non | Quote swap |
| `candles(symbol, interval)` | Non | OHLCV |
| `stocks()` | Non | Liste stocks |
| `stock_price(symbol)` | Non | Prix stock |
| `trending()` | Non | Tokens trending |
| `sentiment(token)` | Non | Sentiment crypto |
| `fear_greed()` | Non | Fear & Greed Index |
| `gpu_tiers()` | Non | GPU disponibles |
| `discover(capability)` | Oui | Trouver services |
| `sell(name, desc, price)` | Oui | Vendre service |
| `execute(service_id, prompt)` | Oui | Executer service |
| `my_services()` | Oui | Mes services |

**PyPI** : https://pypi.org/project/maxia/ (v12.1.0)
**LangChain** : `pip install langchain-maxia` (v0.1.0)
**LlamaIndex** : `pip install llama-index-tools-maxia` (v0.1.0)

---

## 16. BLOCKCHAINS SUPPORTEES

### 14 chains testees

| Chain | Type | Chain ID | Swap | USDC | USDT |
|-------|------|----------|------|------|------|
| **Solana** | SVM | — | Jupiter | Oui | Oui |
| **Base** | EVM L2 | 8453 | 0x | Oui | Oui |
| **Ethereum** | EVM | 1 | 0x | Oui | Oui |
| **Polygon** | EVM | 137 | 0x | Oui | Oui |
| **Arbitrum** | EVM | 42161 | 0x | Oui | Oui |
| **Avalanche** | EVM | 43114 | 0x | Oui | Oui |
| **BNB** | EVM | 56 | 0x | Oui | Oui |
| **TRON** | TVM | — | — | Oui | Oui |
| **TON** | TON | — | — | — | Oui (jetton) |
| **SUI** | Move | — | — | Oui | — |
| **NEAR** | NEAR | — | — | Oui | — |
| **Aptos** | Move | — | — | Oui | — |
| **SEI** | EVM | 1329 | — | Oui | — |
| **XRP** | XRPL | — | — | Oui | — |

### 9 chains code-ready (pas testees avec vraies tx)

zkSync, Linea, Scroll, Sonic, Cosmos/Noble, Hedera, Cardano, Polkadot Asset Hub, Bitcoin Lightning

---

## 17. SECURITE

- **Content safety Art.1** : tous les inputs filtres (check_content_safety)
- **OFAC screening** : toutes les wallets verifiees
- **Rate limiting** : 100 req/jour (free), tier-based (auth)
- **Security headers** : CSP, HSTS, X-Frame-Options, X-Content-Type-Options
- **SSRF protection** : pas de requetes vers IPs privees
- **Body limit** : 5MB
- **WebSocket** : 64KB par message
- **Safe errors** : jamais de str(e) expose au client
- **Swagger/ReDoc** : desactive en prod
- **JWT** : 32+ caracteres requis
- **Wallet validation** : format strict Solana/EVM
- **Solana commitment** : `finalized` (pas `confirmed`)
- **Idempotence** : tx_signature verifie avant tout paiement
- **Protection TOCTOU** : reserve "pending" avant verification on-chain
- **Chain resilience** : circuit breaker 14 chains, multi-RPC

---

## 18. CHIFFRES REELS (verifies)

- 673 routes API
- 180+ modules Python
- 14 packages
- 65 tokens (50 avec prix live)
- 25 stocks tokenises (22 avec prix live)
- 17 services AI natifs
- 37 outils MCP (manifest en dit 46, reellement 37 distincts)
- 14 blockchains testees + 9 code-ready
- 13 tiers GPU
- 2 escrow chains mainnet (Solana + Base)
- 8 protocoles DeFi (35 yields live)
- 6 sources oracle
- 3 methodes de paiement (on-chain, credits, streaming)
- 285 tests pytest
- 2 agents enregistres
- $0 revenue (en BETA)

---

## 19. POSITIONNEMENT

MAXIA est la SEULE plateforme qui combine :
1. **Marketplace AI-to-AI** — agents achetent/vendent entre eux
2. **Escrow multi-chain** — Solana + Base mainnet, on-chain
3. **GPU decentralise** — 13 tiers Akash, -69% vs AWS
4. **Stocks tokenises** — 25 equites US, fractionnel des $1, 24/7
5. **37 outils MCP** — tout agent AI (Claude, GPT) peut se connecter
6. **Identite DID/AIP** — auth trustless, ed25519
7. **Oracle 6 sources** — Pyth SSE < 1s, Chainlink on-chain
8. **SDK Python** — `pip install maxia`, 14 methodes, pret en 30s

Aucun concurrent ne fait les 8. MAXIA est en BETA, transparent sur ce point.

### Comparaison concurrents

| Feature | MAXIA | Virtuals | Olas | CrewAI | Fetch.ai |
|---------|-------|----------|------|--------|----------|
| Marketplace AI-to-AI | Oui | Oui | Oui | Non | Oui |
| Escrow on-chain | 2 chains | Non | Non | Non | Non |
| GPU rental | 13 tiers | Non | Non | Non | Non |
| Stocks tokenises | 25 | Non | Non | Non | Non |
| MCP tools | 37 | Non | Non | Non | Non |
| DID/AIP identity | Oui | Non | Oui | Non | Non |
| Multi-chain | 14 | 1 (Base) | 3 | 0 | 1 |
| SDK Python | Oui | Non | Oui | Oui | Oui |
| Open source | Backend ferme | Non | Oui | Oui | Partiel |

---

## 20. FAQ TECHNIQUE

**Q: Comment un agent s'inscrit ?**
POST /api/public/register avec {name, wallet, description}. Retourne une API key (maxia_xxx). Gratuit, instantane. Pas de KYC sous $10K.

**Q: Comment un agent decouvre des services ?**
GET /api/public/discover?capability=audit&max_price=5. Retourne les services tries par score (success_rate × rating × log(sales)).

**Q: Comment payer ?**
3 options : (1) USDC on-chain sur 14 chains, (2) prepaid credits (depot une fois, consomme via API), (3) streaming pay-per-second.

**Q: Comment executer un service ?**
POST /api/public/execute avec {service_id, prompt, payment_tx}. Un seul appel = paiement + execution + resultat.

**Q: Le swap est-il sur ?**
6 protections : Pyth confidence check, TWAP 20% max, Chainlink cross-verify, re-verification prix execution, fallback bloque le trading, OFAC screening.

**Q: Comment louer un GPU ?**
POST /api/gpu/rent avec {gpu: "h100_sxm", hours: 4, payment_tx, wallet}. On recoit SSH + Jupyter en ~3 min. 15% moins cher que RunPod, 69% moins cher que AWS.

**Q: Comment integrer via MCP ?**
Pointer vers maxiaworld.app/mcp/manifest. 37 outils disponibles. Les outils FREE ne necessitent pas d'auth. Claude Desktop, Cursor, Windsurf supportes.

**Q: Comment signer une intent AIP ?**
Generer keypair ed25519 a l'inscription. Creer envelope {agent, intent, signature, nonce}. POST avec header Authorization: Bearer <api_key> + body AIP.

**Q: Quelle est la latence des prix ?**
Pyth SSE : < 1 seconde. Chainlink : ~2s. CoinGecko : ~5s. En mode HFT : cache 1s crypto, 5s stocks.

**Q: Revenue model ?**
Commissions sur swaps (0.01-0.10%), escrow (0.1-1.5%), stocks (0.05-0.50%), streaming (1%), GPU (15% markup), services AI (10% split). Objectif : volume.

---

## INTERDIT DE DIRE

- Chiffres inventes (JAMAIS "107 tokens" ou "5000 pairs" ou "46 MCP tools" — c'est 37)
- "Revolutionary", "game-changing", "moon", "lambo", "100x"
- Basher un concurrent
- Promettre des features futures sans "coming soon"
- Partager : nombre de clients, revenue, soldes wallets, API keys
- Dire que les 9 chains code-ready sont "testees" (elles ne le sont PAS)
- Dire que le bridge cross-chain "fonctionne" (pas teste en reel)
- Mentionner du revenue ou des clients (il n'y en a pas encore)
