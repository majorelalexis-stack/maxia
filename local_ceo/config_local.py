"""Config pour l'agent CEO local (PC)."""
import os
from dotenv import load_dotenv

load_dotenv()

# VPS connection
VPS_URL = os.getenv("VPS_URL", "https://maxiaworld.app")
CEO_API_KEY = os.getenv("CEO_API_KEY", "")

# Ollama (LLM local)
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:14b")
OLLAMA_VISION_MODEL = os.getenv("OLLAMA_VISION_MODEL", "minicpm-v")

# Mistral (tier MID)
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "")
MISTRAL_MODEL = os.getenv("MISTRAL_MODEL", "mistral-small-2503")

# Boucle OODA
OODA_INTERVAL_S = int(os.getenv("OODA_INTERVAL_S", "300"))  # 5 min (GPU local = rapide)

# Gates d'approbation
APPROVAL_TIMEOUT_ORANGE_S = int(os.getenv("APPROVAL_TIMEOUT_ORANGE_S", "1800"))  # 30 min
APPROVAL_TIMEOUT_ROUGE_S = int(os.getenv("APPROVAL_TIMEOUT_ROUGE_S", "7200"))    # 2h
AUTO_EXECUTE_MAX_USD = float(os.getenv("AUTO_EXECUTE_MAX_USD", "5.0"))  # Seuil orange auto

# Notifications
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Browser
BROWSER_PROFILE_DIR = os.getenv("BROWSER_PROFILE_DIR", os.path.expanduser("~/.maxia-browser"))

# Limites quotidiennes
MAX_TWEETS_DAY = int(os.getenv("MAX_TWEETS_DAY", "2"))  # Qualite > quantite : max 2/jour
MAX_REDDIT_POSTS_DAY = int(os.getenv("MAX_REDDIT_POSTS_DAY", "5"))
MAX_ACTIONS_DAY = int(os.getenv("MAX_ACTIONS_DAY", "300"))

# Audit DB
AUDIT_DB_PATH = os.getenv("AUDIT_DB_PATH", "ceo_audit.db")
