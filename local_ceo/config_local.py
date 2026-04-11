"""Config CEO Local V3 + V9 — Single model Qwen 3.5 27B, 27 missions, zero spam, no Twitter.

Le CEO ne poste JAMAIS directement (sauf MAXIA Community Discord ou il est admin).
Il propose du contenu, Alexis valide et poste manuellement.
Tout passe par Telegram (Go/No) sauf les actions auto V9 (DISBOARD bump reminder,
GitHub prospector email, community news post, blog crosspost, weekly report,
Reddit watch, SEO submit reminder).
"""
import os
from datetime import date, datetime

from dotenv import load_dotenv

load_dotenv()


def current_email_quota() -> int:
    """Return the email outreach cap effective today.

    Linear ramp from ``EMAIL_QUOTA_FLOOR`` to ``MAX_EMAILS_DAY`` over
    ``EMAIL_RAMP_UP_DAYS`` calendar days starting at ``EMAIL_RAMP_UP_START``.
    Defensive on parse errors: falls back to the floor.
    """
    try:
        start = datetime.strptime(EMAIL_RAMP_UP_START, "%Y-%m-%d").date()
    except ValueError:
        return EMAIL_QUOTA_FLOOR
    today = date.today()
    elapsed = (today - start).days
    if elapsed <= 0:
        return EMAIL_QUOTA_FLOOR
    if elapsed >= EMAIL_RAMP_UP_DAYS:
        return MAX_EMAILS_DAY
    span = MAX_EMAILS_DAY - EMAIL_QUOTA_FLOOR
    return EMAIL_QUOTA_FLOOR + int(span * elapsed / EMAIL_RAMP_UP_DAYS)

# ══════════════════════════════════════════
# VPS connection
# ══════════════════════════════════════════
VPS_URL = os.getenv("VPS_URL", "https://maxiaworld.app")
CEO_API_KEY = os.getenv("CEO_API_KEY", "")
ADMIN_KEY = os.getenv("ADMIN_KEY", "")

# ── Notifications (optionnel) ──
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")  # client bot — NE PAS utiliser pour le CEO
TELEGRAM_CEO_CHAT_ID = os.getenv("TELEGRAM_CEO_CHAT_ID", "")  # channel @MAXIA_alerts — go/no, rapports
# Alexis's own user_id. Used by _is_from_alexis() so direct DMs with
# @MAXIA_AI_bot still recognize him as CEO even though CEO_CHAT_ID now
# points to the separate channel.
TELEGRAM_ALEXIS_USER_ID = os.getenv("TELEGRAM_ALEXIS_USER_ID", "")

# ══════════════════════════════════════════
# Ollama — hybrid setup for 7900 XT 20 GB + 6 GB RAM overflow
# MAIN: qwen3:30b-a3b-instruct-2507-q4_K_M (MoE 3.3B actifs, 19 GB, ~30 tok/s)
#       → STRATEGIST / ANALYST / CHAT (long-form, reasoning, synthèse FR)
# FAST: qwen3:14b (9.3 GB, ~60-75 tok/s)
#       → WRITER / MONITOR (tweets, news courtes, health, smart_reply)
# Un seul modèle résident à la fois via keep_alive, swap ~5s.
# ══════════════════════════════════════════
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL_MAIN = os.getenv(
    "OLLAMA_MODEL_MAIN", "qwen3:30b-a3b-instruct-2507-q4_K_M"
)
OLLAMA_MODEL_FAST = os.getenv("OLLAMA_MODEL_FAST", "qwen3:14b")

# Default model (backward compat): callers qui n'ont pas d'AgentConfig.model
# utilisent le MAIN. Les missions critiques (WRITER/MONITOR) override via agent.model.
OLLAMA_MODEL = OLLAMA_MODEL_MAIN
VISION_MODEL = os.getenv("VISION_MODEL", OLLAMA_MODEL_MAIN)

# Backward compat
OLLAMA_CEO_MODEL = OLLAMA_MODEL_MAIN
OLLAMA_EXECUTOR_MODEL = OLLAMA_MODEL_FAST
OLLAMA_VISION_MODEL = OLLAMA_MODEL_MAIN
OLLAMA_BROWSER_MODEL = OLLAMA_MODEL_FAST
OLLAMA_MAX_LOADED_MODELS = 1  # swap MAIN/FAST via keep_alive, 1 résident à la fois
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "8192"))  # 8k avec flash_attn + kv_q8_0
OLLAMA_FLASH_ATTENTION = os.getenv("OLLAMA_FLASH_ATTENTION", "1") == "1"
OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "30m")  # MAIN stays loaded 30 min

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
# Email outreach quota with ramp-up. The "max" is the steady-state cap
# reached after EMAIL_RAMP_UP_DAYS days from EMAIL_RAMP_UP_START. Until
# then, the effective cap grows linearly from EMAIL_QUOTA_FLOOR to
# MAX_EMAILS_DAY. This avoids spiking SMTP volume in one shot when going
# from 5/day to 15/day, which Gmail/Outlook flag as spam.
MAX_EMAILS_DAY = 15  # was 5, scaled 2026-04-10
EMAIL_QUOTA_FLOOR = 5  # starting point of the ramp
EMAIL_RAMP_UP_DAYS = int(os.getenv("EMAIL_RAMP_UP_DAYS", "14"))
EMAIL_RAMP_UP_START = os.getenv("EMAIL_RAMP_UP_START", "2026-04-10")  # ISO date
MAX_ACTIONS_DAY = 100  # was 50, doubled 2026-04-10 to match scaled outreach

# ── PROPOSE, DON'T POST ──
# IMPORTANT: CEO never posts directly. It proposes content,
# sends it to Alexis via Telegram for approval, and Alexis posts manually.
PROPOSE_DONT_POST = True

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
        "Le CEO ne poste JAMAIS directement — il propose du contenu",
        "Alexis valide via Telegram (Go/No) puis poste manuellement",
        "Ne JAMAIS commenter, liker, DM, ou poster nulle part",
        "Tout passe par Telegram a Alexis pour validation",
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
