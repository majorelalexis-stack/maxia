"""MAXIA Config V12 — Configuration centralisee + Dynamic Pricing + Bridge + Staking + 17 Agents"""
import os
from dotenv import load_dotenv
load_dotenv()

# ── Solana ──
TREASURY_ADDRESS   = os.getenv("TREASURY_ADDRESS", "")
ESCROW_ADDRESS     = os.getenv("ESCROW_ADDRESS", "")
ESCROW_PRIVKEY_B58 = os.getenv("ESCROW_PRIVKEY_B58", "")
ESCROW_PROGRAM_ID  = os.getenv("ESCROW_PROGRAM_ID", "8ADNmAPDxuRvJPBp8dL9rq5jpcGtqAEx4JyZd1rXwBUY")
SOLANA_RPC         = os.getenv("SOLANA_RPC", "https://api.mainnet-beta.solana.com")
HELIUS_API_KEY     = os.getenv("HELIUS_API_KEY", "")
FEE_BPS            = int(os.getenv("FEE_BPS", "10"))

# Solana RPC failover — comme base_verifier.py, on essaie chaque URL dans l'ordre
SOLANA_RPC_URLS: list = []

def _build_solana_rpc_urls() -> list:
    urls = []
    if HELIUS_API_KEY:
        urls.append(f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}")
    custom = os.getenv("SOLANA_RPC", "")
    if custom and custom not in urls:
        urls.append(custom)
    # Fallbacks publics (rate-limited mais fonctionnels)
    urls.extend([
        "https://api.mainnet-beta.solana.com",
        "https://solana-mainnet.rpc.extrnode.com",
        "https://rpc.ankr.com/solana",
    ])
    return urls

SOLANA_RPC_URLS = _build_solana_rpc_urls()

def get_rpc_url() -> str:
    """V-20: Helius requires API key in URL (no header option). Never log this URL."""
    return SOLANA_RPC_URLS[0] if SOLANA_RPC_URLS else SOLANA_RPC

def get_rpc_url_safe() -> str:
    """Safe version for logging — masks the API key."""
    url = get_rpc_url()
    if "api-key=" in url:
        return url.split("api-key=")[0] + "api-key=***"
    return url

# ── IA (Groq) ──
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL   = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

# ── GPU ──
RUNPOD_API_KEY = os.getenv("RUNPOD_API_KEY", "")
AKASH_API_KEY = os.getenv("AKASH_API_KEY", "")
AKASH_WALLET = os.getenv("AKASH_WALLET", "")
AKASH_ENABLED = os.getenv("AKASH_ENABLED", "false").lower() == "true"

# ── AgentID (trust levels) ──
AGENTID_API_KEY = os.getenv("AGENTID_API_KEY", "")
AGENTID_ENABLED = os.getenv("AGENTID_ENABLED", "false").lower() == "true"

# ── Agent Marketing ──
MARKETING_WALLET_ADDRESS = os.getenv("MARKETING_WALLET_ADDRESS", "")
MARKETING_WALLET_PRIVKEY = os.getenv("MARKETING_WALLET_PRIVKEY", "")

MICRO_WALLET_ADDRESS   = os.getenv("MICRO_WALLET_ADDRESS", "")
MICRO_WALLET_PRIVKEY   = os.getenv("MICRO_WALLET_PRIVKEY", "")
ANTHROPIC_API_KEY      = os.getenv("ANTHROPIC_API_KEY", "")
GITHUB_TOKEN           = os.getenv("GITHUB_TOKEN", "")
# ── Alertes ──
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

# ── Base L2 ──
BASE_RPC              = os.getenv("BASE_RPC", "https://mainnet.base.org")
BASE_CHAIN_ID         = 8453
BASE_USDC_CONTRACT    = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
TREASURY_ADDRESS_BASE = os.getenv("TREASURY_ADDRESS_BASE", "")
BASE_MIN_TX_USDC      = float(os.getenv("BASE_MIN_TX_USDC", "0.01"))  # Min $0.01 on Base (low gas)
ESCROW_CONTRACT_BASE  = os.getenv("ESCROW_CONTRACT_BASE", "0xBd31bB973183F8476d0C4cF57a92e648b130510C")

# ── Ethereum Mainnet (grosses transactions uniquement) ──
ETH_RPC              = os.getenv("ETH_RPC", "https://eth.llamarpc.com")
ETH_CHAIN_ID         = 1
ETH_USDC_CONTRACT    = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
TREASURY_ADDRESS_ETH = os.getenv("TREASURY_ADDRESS_ETH", "")
ETH_MIN_TX_USDC      = float(os.getenv("ETH_MIN_TX_USDC", "10"))  # min $10 sur ETH (gas fees)

# ── Kite AI ──
KITE_API_URL  = os.getenv("KITE_API_URL", "https://api.gokite.ai/v1")
KITE_API_KEY  = os.getenv("KITE_API_KEY", "")
KITE_AGENT_ID = os.getenv("KITE_AGENT_ID", "")
KITE_AIR_URL  = os.getenv("KITE_AIR_URL", "https://air.gokite.ai/v1")

# ── AP2 ──
AP2_ENABLED     = os.getenv("AP2_ENABLED", "true").lower() == "true"
AP2_AGENT_ID    = os.getenv("AP2_AGENT_ID", "maxia-agent-001")
AP2_SIGNING_KEY = os.getenv("AP2_SIGNING_KEY", "")

# ── x402 ──
X402_FACILITATOR_URL = os.getenv("X402_FACILITATOR_URL", "https://x402.org/facilitator")
SUPPORTED_NETWORKS   = ["solana-mainnet", "base-mainnet", "ethereum-mainnet", "xrpl-mainnet", "ton-mainnet", "sui-mainnet", "polygon-mainnet", "arbitrum-mainnet", "avalanche-mainnet", "bnb-mainnet", "tron-mainnet", "near-mainnet", "aptos-mainnet", "sei-mainnet"]
X402_PRICE_MAP = {
    "/api/marketplace/commands": 0.50,
    "/api/gpu/auctions":        1.00,
}

# ── Finnhub (oracle fallback pour actions tokenisees) ──
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")  # Free tier: 60 req/min

# ── Serveur ──
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8001"))
# Multi-worker: run with `uvicorn main:app --workers 4` for production
# Or use Gunicorn: `gunicorn main:app -w 4 -k uvicorn.workers.UvicornWorker`
WORKERS = int(os.getenv("WEB_CONCURRENCY", "1"))

# ── Security ──
JWT_SECRET = os.getenv("JWT_SECRET", "")  # MUST be set in production (32+ chars)
FORCE_HTTPS = os.getenv("FORCE_HTTPS", "false").lower() == "true"
SANDBOX_MODE = os.getenv("SANDBOX_MODE", "false").lower() == "true"
BROKER_MARGIN      = float(os.getenv("BROKER_MARGIN", "1.00"))
AUCTION_DURATION_S = int(os.getenv("AUCTION_DURATION_S", "30"))
AGENT_TIMEOUT_S    = int(os.getenv("AGENT_TIMEOUT_S", "10"))

# ── Growth Agent ──
GROWTH_MAX_PROSPECTS_DAY = int(os.getenv("GROWTH_MAX_PROSPECTS_PER_DAY", "20"))
GROWTH_MAX_SPEND_DAY     = float(os.getenv("GROWTH_MAX_SPEND_PER_DAY_USDC", "20"))
GROWTH_MAX_SPEND_TX      = float(os.getenv("GROWTH_MAX_SPEND_PER_TX_USDC", "10"))
GROWTH_MIN_PROSPECT_SOL  = float(os.getenv("GROWTH_MIN_PROSPECT_SOL", "0.1"))
GROWTH_MONTHLY_BUDGET    = float(os.getenv("GROWTH_MONTHLY_BUDGET_USDC", "100"))
GROWTH_RESERVE_ALERT     = float(os.getenv("GROWTH_RESERVE_ALERT_USDC", "10"))


# Marketing Bot Tokens
DISCORD_BOT_TOKEN  = os.getenv("DISCORD_BOT_TOKEN", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHANNEL   = os.getenv("TELEGRAM_CHANNEL", "@MAXIA_alerts")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")  # Chat prive fondateur (CEO ALERTS)

# Prospect targeting
PROSPECT_MIN_SOL        = float(os.getenv("PROSPECT_MIN_SOL", "50"))
PROSPECT_MAX_PER_DAY    = int(os.getenv("PROSPECT_MAX_PER_DAY", "50"))
PROSPECT_COOLDOWN_DAYS  = int(os.getenv("PROSPECT_COOLDOWN_DAYS", "999"))  # 999 = never re-contact

TARGET_PROGRAMS = [
    "render1111111111111111111111111111111111111",
    "akash1111111111111111111111111111111111111",
]

# ── V11: Dynamic Pricing ──
DYNAMIC_PRICING_ENABLED       = os.getenv("DYNAMIC_PRICING_ENABLED", "true").lower() == "true"
DYNAMIC_PRICING_MIN_BPS       = int(os.getenv("DYNAMIC_PRICING_MIN_BPS", "5"))
DYNAMIC_PRICING_MAX_BPS       = int(os.getenv("DYNAMIC_PRICING_MAX_BPS", "500"))
DYNAMIC_PRICING_VOLUME_THRESH = float(os.getenv("DYNAMIC_PRICING_VOLUME_THRESHOLD_PCT", "20"))

# ── V11: Cross-Chain Bridge ──
LIFI_API_URL   = os.getenv("LIFI_API_URL", "https://li.quest/v1")
BRIDGE_ENABLED = os.getenv("BRIDGE_ENABLED", "true").lower() == "true"

# ── V11: Reputation Staking ──
STAKING_MIN_USDC      = float(os.getenv("STAKING_MIN_USDC", "50"))
STAKING_SLASH_PCT     = float(os.getenv("STAKING_SLASH_PCT", "50"))
STAKING_DISPUTE_DELAY = int(os.getenv("STAKING_DISPUTE_DELAY_H", "48"))

# ── V11: Scale-Out ──
SCALE_OUT_QUEUE_THRESHOLD = int(os.getenv("SCALE_OUT_QUEUE_THRESHOLD", "100"))
SCALE_OUT_COOLDOWN        = int(os.getenv("SCALE_OUT_COOLDOWN_S", "300"))
RAILWAY_API_TOKEN         = os.getenv("RAILWAY_API_TOKEN", "")

# ── Commissions (per-transaction — bigger trade = lower fee) ──
COMMISSION_TIERS = [
    {"name": "BRONZE",  "min_amount": 0,    "max_amount": 500,  "rate_bps": 100},   # 1%
    {"name": "GOLD",    "min_amount": 500,  "max_amount": 5000, "rate_bps": 50},    # 0.5%
    {"name": "WHALE",   "min_amount": 5000, "max_amount": None, "rate_bps": 10},    # 0.1%
]

def get_commission_bps(amount_usdc: float) -> int:
    """Commission based on transaction amount (not cumulative volume)."""
    for tier in reversed(COMMISSION_TIERS):
        if amount_usdc >= tier["min_amount"]:
            return tier["rate_bps"]
    return 100

def get_commission_tier_name(amount_usdc: float) -> str:
    for tier in reversed(COMMISSION_TIERS):
        if amount_usdc >= tier["min_amount"]:
            return tier["name"]
    return "BRONZE"

# ── GPU Tiers — prix LIVE RunPod (0% markup) ──
# Les prix sont fetches en live via l'API RunPod GraphQL.
# GPU_TIERS_FALLBACK = prix de dernier recours si l'API RunPod est down.
# GPU_TIERS = liste dynamique mise a jour par gpu_pricing.refresh_gpu_prices()
GPU_TIERS_FALLBACK = [
    # local_7900xt retire — GPU utilise par le CEO local (Ollama + Qwen)
    {"id": "rtx3090",     "label": "RTX 3090",       "vram_gb": 24,  "base_price_per_hour": 0.22},
    {"id": "rtx4090",     "label": "RTX 4090",       "vram_gb": 24,  "base_price_per_hour": 0.34},
    {"id": "rtx5090",     "label": "RTX 5090",       "vram_gb": 32,  "base_price_per_hour": 0.69},
    {"id": "a6000",       "label": "RTX A6000",      "vram_gb": 48,  "base_price_per_hour": 0.33},
    {"id": "l4",          "label": "L4",             "vram_gb": 24,  "base_price_per_hour": 0.44},
    {"id": "l40s",        "label": "L40S",           "vram_gb": 48,  "base_price_per_hour": 0.79},
    {"id": "rtx_pro6000", "label": "RTX Pro 6000",   "vram_gb": 96,  "base_price_per_hour": 1.69},
    {"id": "a100_80",     "label": "A100 80GB",      "vram_gb": 80,  "base_price_per_hour": 1.19},
    {"id": "h100_sxm",    "label": "H100 SXM",       "vram_gb": 80,  "base_price_per_hour": 2.69},
    {"id": "h100_nvl",    "label": "H100 NVL",       "vram_gb": 94,  "base_price_per_hour": 2.59},
    {"id": "h200",        "label": "H200 SXM",       "vram_gb": 141, "base_price_per_hour": 3.59},
    {"id": "b200",        "label": "B200",           "vram_gb": 180, "base_price_per_hour": 5.98},
    {"id": "4xa100",      "label": "4x A100 80GB",   "vram_gb": 320, "base_price_per_hour": 4.76},
]
# GPU_TIERS dynamique — mis a jour au demarrage + toutes les 30 min
GPU_TIERS = list(GPU_TIERS_FALLBACK)

# ── Service Prices (centralisees, modifiables sans toucher main.py) ──
SERVICE_PRICES = {
    "maxia-audit": 4.99,
    "maxia-code-review": 2.99,
    "maxia-translate": 0.05,
    "maxia-summary": 0.49,
    "maxia-wallet-analysis": 1.99,
    "maxia-marketing": 0.99,
    "maxia-image": 0.10,
    "maxia-scraper": 0.02,
    "maxia-finetune": 2.99,
    "maxia-awp-staking": 0.00,
    "maxia-transcription": 0.01,
    "maxia-embedding": 0.001,
    "maxia-sentiment": 0.005,
    "maxia-wallet-risk": 0.10,
    "maxia-airdrop-scanner": 0.50,
    "maxia-smart-money": 0.25,
    "maxia-nft-rarity": 0.05,
}

# ── Fine-tune pricing ──
FINETUNE_SERVICE_FEE = float(os.getenv("FINETUNE_SERVICE_FEE", "2.99"))
FINETUNE_GPU_MARKUP = float(os.getenv("FINETUNE_GPU_MARKUP", "0.10"))

# ── LLM cost tracking (cout interne par 1k tokens) ──
LLM_COSTS = {
    "local": {"input": 0.0005, "output": 0.001},
    "fast": {"input": 0.0008, "output": 0.0015},
    "mid": {"input": 0.001, "output": 0.003},
    "strategic": {"input": 0.003, "output": 0.015},
}

# ── Securite Art.1 ──
BLOCKED_WORDS = [
    "child abuse", "child porn", "cp", "pedo", "pedophile", "minor abuse",
    "underage", "csam", "grooming",
    "terror", "weapon", "murder", "assassination", "bomb", "explosive",
    "malware", "ransomware", "exploit", "hack", "phishing", "ddos",
    "scam", "rug pull", "ponzi", "fraud",
    "illegal", "trafficking", "laundering",
]
BLOCKED_PATTERNS = [
    r"(?i)\b(chi[1l]d|k[1i]d|m[1i]nor)\s*(p[0o]rn|s[3e]x|ab[uv]s)",
    r"(?i)\b(und[3e]rag[3e]|p[3e]d[0o])",
]

# ── Abonnements ──
SUBSCRIPTION_PLANS = {}  # Removed — MAXIA is pay-per-use only. No subscriptions.

# ── Twitter/X API ──
TWITTER_API_KEY        = os.getenv("TWITTER_API_KEY", "")
TWITTER_API_SECRET     = os.getenv("TWITTER_API_SECRET", "")
TWITTER_ACCESS_TOKEN   = os.getenv("TWITTER_ACCESS_TOKEN", "")
TWITTER_ACCESS_SECRET  = os.getenv("TWITTER_ACCESS_SECRET", "")

# ── V12: PostgreSQL + Redis ──
DATABASE_URL         = os.getenv("DATABASE_URL", "")           # postgresql://...
REDIS_URL            = os.getenv("REDIS_URL", "")              # redis://localhost:6379/0
ACCEPTED_CURRENCIES  = ["USDC", "SOL", "ETH", "TON", "SUI", "USDT"]
CURRENCY_SLIPPAGE_PCT = float(os.getenv("CURRENCY_SLIPPAGE_PCT", "2"))

# ── V12: XRP Ledger (3eme blockchain) ──
XRPL_RPC = os.getenv("XRPL_RPC", "https://s2.ripple.com:51234/")
XRPL_USDC_ISSUER = os.getenv("XRPL_USDC_ISSUER", "rcEGREd8NmkKRE8GE424sksyt1tJVFZwu")
TREASURY_ADDRESS_XRPL = os.getenv("TREASURY_ADDRESS_XRPL", "")

# ── V12: TON — The Open Network (4eme blockchain, non-EVM) ──
TON_API_URL = os.getenv("TON_API_URL", "https://toncenter.com/api/v2")
TREASURY_ADDRESS_TON = os.getenv("TREASURY_ADDRESS_TON", "")
TON_USDT_JETTON = "EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs"  # Tether USDT on TON

# ── V12: SUI (5eme blockchain, non-EVM) ──
SUI_RPC = os.getenv("SUI_RPC", "https://fullnode.mainnet.sui.io:443")
TREASURY_ADDRESS_SUI = os.getenv("TREASURY_ADDRESS_SUI", "")
SUI_USDC_TYPE = "0xdba34672e30cb065b1f93e3ab55318768fd6fef66c15942c9f7cb846e2f900e7::usdc::USDC"

# ── V12: Polygon PoS (6eme blockchain, EVM) ──
POLYGON_RPC              = os.getenv("POLYGON_RPC", "https://polygon-rpc.com")
POLYGON_CHAIN_ID         = 137
POLYGON_USDC_CONTRACT    = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"
TREASURY_ADDRESS_POLYGON = os.getenv("TREASURY_ADDRESS_POLYGON", "")

# ── V12: Arbitrum One (7eme blockchain, EVM L2) ──
ARBITRUM_RPC              = os.getenv("ARBITRUM_RPC", "https://arb1.arbitrum.io/rpc")
ARBITRUM_CHAIN_ID         = 42161
ARBITRUM_USDC_CONTRACT    = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
TREASURY_ADDRESS_ARBITRUM = os.getenv("TREASURY_ADDRESS_ARBITRUM", "")

# ── V12: Avalanche C-Chain (8eme blockchain, EVM) ──
AVALANCHE_RPC              = os.getenv("AVALANCHE_RPC", "https://api.avax.network/ext/bc/C/rpc")
AVALANCHE_CHAIN_ID         = 43114
AVALANCHE_USDC_CONTRACT    = "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E"
TREASURY_ADDRESS_AVALANCHE = os.getenv("TREASURY_ADDRESS_AVALANCHE", "")

# ── V12: BNB Chain (9eme blockchain, EVM) ──
BNB_RPC              = os.getenv("BNB_RPC", "https://bsc-dataseed.binance.org")
BNB_CHAIN_ID         = 56
BNB_USDC_CONTRACT    = "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d"
TREASURY_ADDRESS_BNB = os.getenv("TREASURY_ADDRESS_BNB", "")

# ── V12: TRON (10eme blockchain, non-EVM) ──
TRON_API_URL = os.getenv("TRON_API_URL", "https://api.trongrid.io")
TREASURY_ADDRESS_TRON = os.getenv("TREASURY_ADDRESS_TRON", "")
TRON_USDT_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"  # Tether USDT TRC-20
TRON_USDC_CONTRACT = "TEkxiTehnzSmSe2XqrBj4w32RUN966rdz8"  # USDC TRC-20

# ── V12: NEAR Protocol (12eme blockchain, non-EVM) ──
NEAR_RPC = os.getenv("NEAR_RPC", "https://rpc.mainnet.near.org")
TREASURY_ADDRESS_NEAR = os.getenv("TREASURY_ADDRESS_NEAR", "")
NEAR_USDC_CONTRACT = "17208628f84f5d6ad33f0da3bbbeb27ffcb398eac501a31bd6ad2011e36133a1"

# ── V12: Aptos (13eme blockchain, non-EVM Move) ──
APTOS_API = os.getenv("APTOS_API", "https://fullnode.mainnet.aptoslabs.com/v1")
TREASURY_ADDRESS_APTOS = os.getenv("TREASURY_ADDRESS_APTOS", "")
APTOS_USDC_TYPE = "0xbae207659db88bea0cbead6da0ed00aac12edcdda169e591cd41c94180b46f3b::usdc::USDC"

# ── V12: SEI (14eme blockchain, EVM) ──
SEI_RPC              = os.getenv("SEI_RPC", "https://evm-rpc.sei-apis.com")
SEI_CHAIN_ID         = 1329
SEI_USDC_CONTRACT    = "0x3894085Ef7Ff0f0aeDf52E2A2704928d1Ec074F1"
TREASURY_ADDRESS_SEI = os.getenv("TREASURY_ADDRESS_SEI", "")
SEI_MIN_TX_USDC      = float(os.getenv("SEI_MIN_TX_USDC", "0.01"))

# ── V12: LLM Router (CEO autonome) ──
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:14b")
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "")
MISTRAL_MODEL = os.getenv("MISTRAL_MODEL", "mistral-small-2503")
CEO_LOCAL_MODE = os.getenv("CEO_LOCAL_MODE", "true").lower() == "true"

# ── V12: CEO API (PC local <-> VPS) ──
CEO_API_KEY = os.getenv("CEO_API_KEY", "")
CEO_ALLOWED_IPS = os.getenv("CEO_ALLOWED_IPS", "")  # Comma-separated IP whitelist

# ── Startup Validation ──
import logging as _logging
_cfg_log = _logging.getLogger("config")

ADMIN_KEY = os.getenv("ADMIN_KEY", "")


def validate_secrets():
    """Valide que les secrets critiques sont definis. Appele au demarrage."""
    _warnings = []
    _errors = []

    if not SANDBOX_MODE:
        # En production, ces secrets sont OBLIGATOIRES
        if not JWT_SECRET or len(JWT_SECRET) < 16:
            _errors.append("JWT_SECRET absent ou trop court (<16 chars)")
        if not ADMIN_KEY or len(ADMIN_KEY) < 16:
            _warnings.append("ADMIN_KEY absent ou trop court — dashboard admin inaccessible")
        if not TREASURY_ADDRESS:
            _warnings.append("TREASURY_ADDRESS vide — paiements Solana impossibles")
        if CEO_API_KEY and not CEO_ALLOWED_IPS:
            _warnings.append("CEO_API_KEY defini sans CEO_ALLOWED_IPS — whitelist IP recommandee en prod")
    else:
        _cfg_log.info("SANDBOX_MODE=true — validation secrets allegee")

    # Toujours verifier (sandbox ou prod)
    if ESCROW_PRIVKEY_B58 and len(ESCROW_PRIVKEY_B58) < 32:
        _errors.append("ESCROW_PRIVKEY_B58 semble invalide (trop court)")

    for w in _warnings:
        _cfg_log.warning(f"[Config] {w}")
    for e in _errors:
        _cfg_log.error(f"[Config] CRITIQUE: {e}")
    if _errors and not SANDBOX_MODE:
        raise RuntimeError(
            f"Configuration invalide en production: {'; '.join(_errors)}. "
            "Corrigez .env avant de demarrer."
        )

validate_secrets()
