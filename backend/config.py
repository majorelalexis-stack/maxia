"""MAXIA Config V12 — Configuration centralisee + Dynamic Pricing + Bridge + Staking + 17 Agents"""
import os
from dotenv import load_dotenv
load_dotenv()

# ── Solana ──
TREASURY_ADDRESS   = os.getenv("TREASURY_ADDRESS", "")
ESCROW_ADDRESS     = os.getenv("ESCROW_ADDRESS", "")
ESCROW_PRIVKEY_B58 = os.getenv("ESCROW_PRIVKEY_B58", "")
SOLANA_RPC         = os.getenv("SOLANA_RPC", "https://api.mainnet-beta.solana.com")
HELIUS_API_KEY     = os.getenv("HELIUS_API_KEY", "")
FEE_BPS            = int(os.getenv("FEE_BPS", "10"))

def get_rpc_url() -> str:
    if HELIUS_API_KEY:
        return f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
    return SOLANA_RPC

# ── IA (Groq) ──
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL   = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

# ── GPU ──
RUNPOD_API_KEY = os.getenv("RUNPOD_API_KEY", "")

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
SUPPORTED_NETWORKS   = ["solana-mainnet", "base-mainnet", "ethereum-mainnet", "xrpl-mainnet"]
X402_PRICE_MAP = {
    "/api/marketplace/commands": 0.50,
    "/api/data/datasets":       0.10,
    "/api/gpu/auctions":        1.00,
}

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

# Prospect targeting
PROSPECT_MIN_SOL        = float(os.getenv("PROSPECT_MIN_SOL", "50"))
PROSPECT_MAX_PER_DAY    = int(os.getenv("PROSPECT_MAX_PER_DAY", "5"))
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

# ── Commissions (base — ajustables par Dynamic Pricing) ──
COMMISSION_TIERS = [
    {"name": "BRONZE",  "min_volume": 0,    "max_volume": 500,  "rate_bps": 500},
    {"name": "GOLD",    "min_volume": 500,  "max_volume": 5000, "rate_bps": 100},
    {"name": "WHALE",   "min_volume": 5000, "max_volume": None, "rate_bps": 10},
]

def get_commission_bps(volume_30d: float) -> int:
    for tier in reversed(COMMISSION_TIERS):
        if volume_30d >= tier["min_volume"]:
            return tier["rate_bps"]
    return 500

# ── GPU Tiers ──
GPU_TIERS = [
    {"id": "rtx4090",   "label": "RTX 4090",     "vram_gb": 24,  "base_price_per_hour": 0.69},
    {"id": "a100_80",   "label": "A100 80GB",    "vram_gb": 80,  "base_price_per_hour": 1.79},
    {"id": "h100_sxm5", "label": "H100 SXM5",    "vram_gb": 80,  "base_price_per_hour": 2.69},
    {"id": "a6000",     "label": "RTX A6000",    "vram_gb": 48,  "base_price_per_hour": 0.99},
    {"id": "4xa100",    "label": "4x A100 80GB", "vram_gb": 320, "base_price_per_hour": 7.16},
]

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
ACCEPTED_CURRENCIES  = ["USDC", "SOL", "ETH"]
CURRENCY_SLIPPAGE_PCT = float(os.getenv("CURRENCY_SLIPPAGE_PCT", "2"))

# ── V12: XRP Ledger (3eme blockchain) ──
XRPL_RPC = os.getenv("XRPL_RPC", "https://s2.ripple.com:51234/")
XRPL_USDC_ISSUER = os.getenv("XRPL_USDC_ISSUER", "rcEGREd8NmkKRE8GE424sksyt1tJVFZwu")
TREASURY_ADDRESS_XRPL = os.getenv("TREASURY_ADDRESS_XRPL", "")

# ── V12: LLM Router (CEO autonome) ──
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "")
MISTRAL_MODEL = os.getenv("MISTRAL_MODEL", "mistral-small-2503")
CEO_LOCAL_MODE = os.getenv("CEO_LOCAL_MODE", "true").lower() == "true"

# ── V12: CEO API (PC local <-> VPS) ──
CEO_API_KEY = os.getenv("CEO_API_KEY", "")
CEO_ALLOWED_IPS = os.getenv("CEO_ALLOWED_IPS", "")  # Comma-separated IP whitelist
