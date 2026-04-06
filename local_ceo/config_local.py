"""Config CEO Local V2 — Dual-model Qwen 3.5 27B + VL 7B, 14 missions, zero spam.

Le CEO ne poste RIEN sauf 1 tweet/jour. Tout passe par mail a Alexis.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ══════════════════════════════════════════
# VPS connection
# ══════════════════════════════════════════
VPS_URL = os.getenv("VPS_URL", "https://maxiaworld.app")
CEO_API_KEY = os.getenv("CEO_API_KEY", "")
ADMIN_KEY = os.getenv("ADMIN_KEY", "")

# ── Notifications (optionnel) ──
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ══════════════════════════════════════════
# Ollama — dual-model (texte: qwen3.5:27b + vision: qwen2.5vl:7b)
# ══════════════════════════════════════════
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3.5:27b")
VISION_MODEL = os.getenv("VISION_MODEL", "qwen2.5vl:7b")

# Backward compat
OLLAMA_CEO_MODEL = OLLAMA_MODEL
OLLAMA_EXECUTOR_MODEL = OLLAMA_MODEL
OLLAMA_VISION_MODEL = VISION_MODEL
OLLAMA_BROWSER_MODEL = OLLAMA_MODEL
OLLAMA_MAX_LOADED_MODELS = 1

# Mistral (fallback cloud — rarement utilise)
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "")
MISTRAL_MODEL = os.getenv("MISTRAL_MODEL", "mistral-small-2503")

# ══════════════════════════════════════════
# Email — destination Alexis
# ══════════════════════════════════════════
ALEXIS_EMAIL = "majorel.alexis@gmail.com"
CEO_EMAIL = "ceo@maxiaworld.app"

# ══════════════════════════════════════════
# Browser
# ══════════════════════════════════════════
BROWSER_PROFILE_DIR = os.getenv("BROWSER_PROFILE_DIR", os.path.expanduser("~/.maxia-ceo-browser"))

# ══════════════════════════════════════════
# Limites — STRICTES
# ══════════════════════════════════════════
MAX_TWEETS_DAY = 1  # 1 seul tweet feature/jour
MAX_EMAILS_DAY = 5  # opportunites + rapport + alertes
MAX_ACTIONS_DAY = 50  # scans + moderation + health checks

# Tout le reste est ZERO
MAX_COMMENTS_TWITTER_DAY = 0
MAX_QUOTE_TWEETS_DAY = 0
MAX_REDDIT_POSTS_DAY = 0
MAX_REDDIT_COMMENTS_DAY = 0
MAX_GITHUB_COMMENTS_DAY = 0
MAX_DISCORD_MESSAGES_DAY = 0
MAX_TELEGRAM_MESSAGES_DAY = 0
OFF_DAYS_PER_WEEK = 1  # 1 jour off aleatoire/semaine — anti-spam (Phase 6)

# Backward compat pour browser_agent.py
MIN_ACTION_SPACING_S = 3600
MIN_TWEET_SPACING_S = 86400  # 1 tweet/jour = 24h spacing

# Fichiers
ACTIONS_TODAY_FILE = os.path.join(os.path.dirname(__file__), "actions_today.json")
STRATEGY_FILE = os.path.join(os.path.dirname(__file__), "strategy.md")
LEARNINGS_FILE = os.path.join(os.path.dirname(__file__), "learnings.json")
RND_FINDINGS_FILE = os.path.join(os.path.dirname(__file__), "rnd_findings.md")
PLATFORM_SCORES_FILE = os.path.join(os.path.dirname(__file__), "platform_scores.json")
AUDIT_DB_PATH = os.path.join(os.path.dirname(__file__), "ceo_audit.db")

# Intervalles
HEALTH_CHECK_INTERVAL_S = 1800  # 30 min (2 GET x 48/jour = 96 req, dans la limite)
MODERATION_INTERVAL_S = 3600    # 1h
OODA_INTERVAL_S = 300           # 5 min (backward compat)

# ══════════════════════════════════════════
# Kaspa Mining — auto-switch avec CEO
# ══════════════════════════════════════════
KASPA_MINING_ENABLED = os.getenv("KASPA_MINING_ENABLED", "0") == "1"  # Desactive — GPU mining KAS non rentable (mars 2026)
TEAMREDMINER_DIR = os.getenv("TEAMREDMINER_DIR", r"C:\Mining\TeamRedMiner")

# Approbation (backward compat)
APPROVAL_TIMEOUT_ORANGE_S = 120
APPROVAL_TIMEOUT_ROUGE_S = 7200
AUTO_EXECUTE_MAX_USD = 5.0

# ══════════════════════════════════════════
# Personnalite
# ══════════════════════════════════════════
PERSONALITY = {
    "tone": "professional, calm, confident",
    "language": "english",
    "forbidden_words": [
        "revolutionary", "game-changing", "disruptive", "moon", "lambo",
        "100x", "guaranteed", "insane", "mind-blowing",
        "better than", "kills", "destroys", "rip", "dead project",
    ],
    "positive_rules": [
        "1 tweet/jour max — presenter une feature MAXIA",
        "Ne JAMAIS commenter, liker, DM, ou poster ailleurs",
        "Tout passe par mail a Alexis sauf le tweet quotidien",
    ],
}

CONFIDENTIAL = {
    "never_share": [
        "client count", "revenue numbers", "wallet balances",
        "internal metrics", "API keys", "passwords",
    ],
}

# ══════════════════════════════════════════
# GitHub repos a scanner
# ══════════════════════════════════════════
GITHUB_REPOS = [
    "elizaOS/eliza", "langchain-ai/langchain", "ollama/ollama",
    "run-llama/llama_index", "VRSEN/agency-swarm", "goat-sdk/goat",
    "microsoft/autogen", "crewAIInc/crewAI", "valory-xyz/open-autonomy",
    "fetchai/uAgents", "e2b-dev/E2B", "browser-use/browser-use",
    "jup-ag/jupiter-quote-api-node", "anthropics/anthropic-cookbook",
    "openai/swarm",
]

# MAXIA features pour le tweet quotidien (rotation)
MAXIA_FEATURES = [
    {"name": "Token Swap", "desc": "Swap 65+ tokens across 14 blockchains. Powered by Jupiter. Low fees.", "link": "maxiaworld.app/app#swap"},
    {"name": "AI Marketplace", "desc": "Buy & sell AI services with USDC. Sentiment analysis, code gen, audits.", "link": "maxiaworld.app/marketplace"},
    {"name": "Live Trading", "desc": "Real-time candlestick charts. 68 crypto + 25 tokenized stocks. Sub-second prices.", "link": "maxiaworld.app/app#trading"},
    {"name": "GPU Rental", "desc": "Rent GPUs via Akash Network. 6 tiers from $0.15/h. Cheaper than AWS.", "link": "maxiaworld.app/app#gpu"},
    {"name": "On-chain Escrow", "desc": "USDC escrow on Solana & Base. Auto-refund 48h. Zero trust needed.", "link": "maxiaworld.app/app#escrow"},
    {"name": "Tokenized Stocks", "desc": "Trade AAPL, TSLA, NVDA and 25 more stocks with crypto. 24/7.", "link": "maxiaworld.app/app#stocks"},
    {"name": "AI Forum", "desc": "First AI-to-AI forum. Agents discuss, trade leads, post bounties.", "link": "maxiaworld.app/forum"},
    {"name": "MCP Tools", "desc": "46 MCP tools for Claude, Cursor, LangChain. Connect your AI to MAXIA.", "link": "maxiaworld.app/mcp/manifest"},
    {"name": "Agent Registration", "desc": "Register your AI agent in 30 seconds. Get an API key. Start earning USDC.", "link": "maxiaworld.app/register"},
    {"name": "Wallet Analysis", "desc": "Deep analysis of any wallet. PnL, risk score, holdings, history.", "link": "maxiaworld.app/app"},
    {"name": "DeFi Yields", "desc": "Find the best yields across Solana DeFi. Auto-scan lending & staking.", "link": "maxiaworld.app/app#yields"},
    {"name": "Sentiment Analysis", "desc": "AI analyzes 1000+ sources for any token. Bullish/bearish + confidence.", "link": "maxiaworld.app/marketplace"},
    {"name": "Multi-chain Support", "desc": "14 blockchains: Solana, Base, Ethereum, XRP, Polygon, Arbitrum, Avalanche, BNB, TON, SUI, TRON, NEAR, Aptos, SEI.", "link": "maxiaworld.app"},
    {"name": "Bug Reports", "desc": "Found a bug? Report it in 10 seconds. No wallet needed. We fix fast.", "link": "maxiaworld.app/forum?community=bugs"},
]
