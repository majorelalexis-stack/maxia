"""Config pour l'agent CEO local (PC AMD 5800X + RX 7900XT 20GB VRAM).

Architecture 3 modeles :
  CEO (cerveau)   = Qwen 3 14B   — raisonne, decide, redige (think=on, chain-of-thought)
  Executeur (bras) = Qwen 3.5 9B  — surfe, poste, execute (rapide, tool calling)
  Vision (yeux)    = Qwen 2.5-VL 7B — lit les pages, screenshots (multimodal)

VRAM : 9.3 + 6.6 + 6.0 = ~22 GB → 2 en VRAM simultane, 1 en RAM overflow (4GB)
Ollama gere le swap automatiquement.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ══════════════════════════════════════════
# VPS connection (scout mode — collecte uniquement)
# ══════════════════════════════════════════
VPS_URL = os.getenv("VPS_URL", "https://maxiaworld.app")
CEO_API_KEY = os.getenv("CEO_API_KEY", "")

# ══════════════════════════════════════════
# Ollama — 3 modeles (cerveau + bras + yeux)
# OLLAMA_MAX_LOADED_MODELS=3 pour garder les 3 en VRAM/RAM simultanement
# Sans ca, Ollama decharge un modele a chaque swap (~5-10s de latence)
# ══════════════════════════════════════════
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MAX_LOADED_MODELS = 3  # 3 modeles charges simultanement (20GB VRAM + 4GB RAM overflow)

# CEO (cerveau) — Qwen 3 14B : raisonnement, strategie, redaction, decisions
# Chain-of-thought natif (think=on), meilleur modele de raisonnement dans 20GB
OLLAMA_CEO_MODEL = os.getenv("OLLAMA_CEO_MODEL", "qwen3:14b")

# Executeur (bras) — Qwen 3.5 9B : actions rapides, surf, posts, execution
# Pas de thinking (think=off), rapide, bon instruction following
OLLAMA_EXECUTOR_MODEL = os.getenv("OLLAMA_EXECUTOR_MODEL", "qwen3.5:9b")

# Vision (yeux) — Qwen 2.5-VL 7B : lecture de pages, screenshots, OCR
# Multimodal, voit les images/pages web
OLLAMA_VISION_MODEL = os.getenv("OLLAMA_VISION_MODEL", "qwen2.5vl:7b")

# Backward compat : OLLAMA_MODEL pointe vers le CEO
OLLAMA_MODEL = OLLAMA_CEO_MODEL
OLLAMA_BROWSER_MODEL = OLLAMA_VISION_MODEL

# Mistral (tier MID — fallback cloud)
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "")
MISTRAL_MODEL = os.getenv("MISTRAL_MODEL", "mistral-small-2503")

# ══════════════════════════════════════════
# Boucle OODA
# ══════════════════════════════════════════
OODA_INTERVAL_S = int(os.getenv("OODA_INTERVAL_S", "300"))  # 5 min (GPU local = rapide)

# Gates d'approbation
APPROVAL_TIMEOUT_ORANGE_S = int(os.getenv("APPROVAL_TIMEOUT_ORANGE_S", "120"))  # 2 min (rapide)
APPROVAL_TIMEOUT_ROUGE_S = int(os.getenv("APPROVAL_TIMEOUT_ROUGE_S", "7200"))    # 2h
AUTO_EXECUTE_MAX_USD = float(os.getenv("AUTO_EXECUTE_MAX_USD", "5.0"))  # Seuil orange auto

# ══════════════════════════════════════════
# Notifications
# ══════════════════════════════════════════
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Browser
BROWSER_PROFILE_DIR = os.getenv("BROWSER_PROFILE_DIR", os.path.expanduser("~/.maxia-ceo-browser"))

# ══════════════════════════════════════════
# Limites quotidiennes par plateforme
# ══════════════════════════════════════════
MAX_TWEETS_DAY = int(os.getenv("MAX_TWEETS_DAY", "2"))
MAX_COMMENTS_TWITTER_DAY = int(os.getenv("MAX_COMMENTS_TWITTER_DAY", "8"))
MAX_QUOTE_TWEETS_DAY = int(os.getenv("MAX_QUOTE_TWEETS_DAY", "3"))
MAX_REDDIT_POSTS_DAY = int(os.getenv("MAX_REDDIT_POSTS_DAY", "3"))
MAX_REDDIT_COMMENTS_DAY = int(os.getenv("MAX_REDDIT_COMMENTS_DAY", "6"))
MAX_GITHUB_COMMENTS_DAY = int(os.getenv("MAX_GITHUB_COMMENTS_DAY", "5"))
MAX_DISCORD_MESSAGES_DAY = int(os.getenv("MAX_DISCORD_MESSAGES_DAY", "6"))
MAX_TELEGRAM_MESSAGES_DAY = int(os.getenv("MAX_TELEGRAM_MESSAGES_DAY", "6"))
MAX_EMAILS_DAY = int(os.getenv("MAX_EMAILS_DAY", "3"))
MAX_ACTIONS_DAY = int(os.getenv("MAX_ACTIONS_DAY", "150"))

# ══════════════════════════════════════════
# Espacement et jours off (anti-spam toutes plateformes)
# ══════════════════════════════════════════
MIN_ACTION_SPACING_S = int(os.getenv("MIN_ACTION_SPACING_S", "1800"))  # 30 min entre commentaires/posts
MIN_TWEET_SPACING_S = int(os.getenv("MIN_TWEET_SPACING_S", "3600"))    # 60 min entre tweets originaux
OFF_DAYS_PER_WEEK = int(os.getenv("OFF_DAYS_PER_WEEK", "1"))          # 1 jour off/semaine (likes OK)
ACTIONS_TODAY_FILE = os.path.join(os.path.dirname(__file__), "actions_today.json")

# Email CEO
CEO_EMAIL = "ceo@maxiaworld.app"

# Audit DB
AUDIT_DB_PATH = os.getenv("AUDIT_DB_PATH", "ceo_audit.db")

# ══════════════════════════════════════════
# Personnalite du CEO — regles immuables
# ══════════════════════════════════════════

# Ton : professionnel, calme, confiant — pas un commercial surexcite
PERSONALITY = {
    # Regles de communication
    "tone": "professional, calm, confident — like a CEO who knows his product",
    "language": "english",  # Contenu public toujours en anglais

    # Mots interdits dans TOUT contenu genere (tweets, comments, emails, DMs)
    "forbidden_words": [
        # Negativite / conflit
        "disagree", "wrong", "bad", "terrible", "awful", "hate", "stupid",
        "dumb", "worst", "sucks", "skeptical", "doubt", "scam", "fraud",
        "garbage", "trash", "useless", "pathetic", "joke",
        # Hype excessif
        "revolutionary", "game-changing", "disruptive", "moon", "to the moon",
        "lambo", "100x", "guaranteed", "insane", "mind-blowing",
        # Denigrement concurrents
        "better than", "kills", "destroys", "rip", "dead project",
    ],

    # Regles de positivite
    "positive_rules": [
        "Always be respectful, even to hostile messages",
        "Never denigrate a competitor — highlight our differences instead",
        "If attacked: respond ONCE with facts, then move on. Never escalate.",
        "80% value, 20% MAXIA mention — be helpful first",
        "If in doubt about tone, do NOT post",
    ],
}

# ══════════════════════════════════════════
# Confidentialite — JAMAIS partager
# ══════════════════════════════════════════

CONFIDENTIAL = {
    # Informations interdites dans TOUT contenu public
    "never_share": [
        "client count",        # 0 ou pas, on ne dit jamais
        "revenue numbers",     # chiffre d'affaires
        "transaction volume",  # volume reel
        "agent count",         # nombre d'agents inscrits
        "user count",          # nombre d'utilisateurs
        "daily active users",  # DAU
        "monthly active users",  # MAU
        "profit",              # benefice
        "loss",                # perte
        "burn rate",           # taux de combustion
        "runway",              # duree avant epuisement
    ],

    # Reponse quand on demande des chiffres business
    "deflect_response": (
        "We don't share business metrics publicly. "
        "Here's what MAXIA can do for you: "
    ),
}

# ══════════════════════════════════════════
# R&D en temps mort — sources de veille
# ══════════════════════════════════════════

RND_SOURCES = {
    # Concurrents directs — marketplace AI agents
    "competitors": [
        {"name": "Virtuals Protocol", "urls": ["https://github.com/Virtual-Protocol", "https://x.com/virtikiprotocol"], "focus": "agent tokenization, gaming agents"},
        {"name": "Fetch.ai", "urls": ["https://github.com/fetchai", "https://x.com/Fetch_ai"], "focus": "uAgents framework, agent economy"},
        {"name": "SingularityNET", "urls": ["https://github.com/singnet", "https://x.com/SingularityNET"], "focus": "AI marketplace, AGIX token"},
        {"name": "Autonolas/Olas", "urls": ["https://github.com/valory-xyz", "https://x.com/auaborolas"], "focus": "autonomous agents, mech marketplace"},
        {"name": "MyShell", "urls": ["https://github.com/myshell-ai", "https://x.com/myshell_ai"], "focus": "AI agent creation, voice agents"},
        {"name": "ai16z/Eliza", "urls": ["https://github.com/elizaOS/eliza"], "focus": "agent framework, plugins, multi-chain"},
        {"name": "GOAT SDK", "urls": ["https://github.com/goat-sdk/goat"], "focus": "agent tools, on-chain actions"},
        {"name": "Morpheus AI", "urls": ["https://github.com/MorpheusAIs"], "focus": "decentralized AI, compute marketplace"},
        {"name": "Ritual", "urls": ["https://github.com/ritual-net"], "focus": "AI inference on-chain"},
        {"name": "Bittensor", "urls": ["https://github.com/opentensor"], "focus": "decentralized AI network, TAO"},
        {"name": "Phala Network", "urls": ["https://github.com/Phala-Network"], "focus": "AI agent coprocessor, TEE"},
    ],

    # Sources d'ameliorations techniques
    "improvement_sources": [
        "https://github.com/trending/python?since=daily",
        "https://github.com/trending/typescript?since=daily",
        "https://github.com/trending?spoken_language_code=en",
        "https://news.ycombinator.com",
        "https://news.ycombinator.com/shownew",
        "https://defillama.com/yields?chain=Solana",
        "https://defillama.com/yields?chain=Base",
        "https://dexscreener.com/trending",
        "https://www.coingecko.com/en/new-cryptocurrencies",
    ],

    # Protocoles/standards a suivre
    "protocols": [
        {"name": "MCP", "url": "https://github.com/modelcontextprotocol/specification", "impact": "nos 46 outils MCP"},
        {"name": "A2A", "url": "https://github.com/google/A2A", "impact": "notre protocole agent-to-agent"},
        {"name": "0x Protocol", "url": "https://github.com/0xProject/protocol/releases", "impact": "nos swaps EVM"},
        {"name": "Jupiter", "url": "https://github.com/jup-ag/jupiter-quote-api-node", "impact": "nos swaps Solana"},
        {"name": "x402", "url": "https://github.com/anthropics/anthropic-cookbook", "impact": "micropaiements"},
        {"name": "Pyth Oracle", "url": "https://github.com/pyth-network/pyth-sdk-solidity", "impact": "nos 25 stocks tokenises"},
    ],

    # Agent registries — pour le scout on-chain
    "agent_registries": [
        {"name": "Olas Registry", "chain": "ethereum", "url": "https://registry.olas.network"},
        {"name": "Virtuals Registry", "chain": "base", "url": "https://app.virtuals.io"},
        {"name": "Fetch.ai Almanac", "chain": "fetchhub", "url": "https://fetch.ai/docs/references/contracts/uagents-almanac/almanac-overview"},
        {"name": "ElizaOS Agents", "chain": "solana", "url": "https://github.com/elizaOS/eliza/tree/main/packages"},
    ],
}

# ══════════════════════════════════════════
# Fichiers de strategie (ecrits par le CEO)
# ══════════════════════════════════════════
STRATEGY_FILE = os.path.join(os.path.dirname(__file__), "strategy.md")
LEARNINGS_FILE = os.path.join(os.path.dirname(__file__), "learnings.json")
RND_FINDINGS_FILE = os.path.join(os.path.dirname(__file__), "rnd_findings.md")

# ══════════════════════════════════════════
# Score par plateforme (mis a jour par le CEO)
# ══════════════════════════════════════════
PLATFORM_SCORES_FILE = os.path.join(os.path.dirname(__file__), "platform_scores.json")
