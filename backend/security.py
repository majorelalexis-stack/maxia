"""MAXIA Art.1 V12 — Securite, filtrage contenu, rate limiting, burst protection, audit, garde-fous financiers"""
import re, time, json, os
from collections import defaultdict
from pathlib import Path
from datetime import datetime
from fastapi import HTTPException, Request
from config import (
    BLOCKED_WORDS, BLOCKED_PATTERNS,
    GROWTH_MAX_SPEND_DAY, GROWTH_MAX_SPEND_TX,
)


# ── Audit log (admin actions) ──

_AUDIT_LOG_FILE = Path(__file__).parent / ".audit_log.jsonl"
_audit_buffer: list = []


def audit_log(action: str, ip: str, details: str = "", user: str = "admin"):
    """Log une action admin avec timestamp, IP, details."""
    entry = {
        "ts": datetime.utcnow().isoformat(),
        "action": action,
        "ip": ip,
        "user": user,
        "details": details[:500],
    }
    _audit_buffer.append(entry)
    print(f"[AUDIT] {action} from {ip}: {details[:100]}")
    # Flush au disque tous les 5 entrees
    if len(_audit_buffer) >= 5:
        _flush_audit()


def _flush_audit():
    global _audit_buffer
    try:
        with open(_AUDIT_LOG_FILE, "a") as f:
            for entry in _audit_buffer:
                f.write(json.dumps(entry) + "\n")
        _audit_buffer = []
    except Exception as e:
        print(f"[AUDIT] Flush error: {e}")


def get_audit_log(limit: int = 50) -> list:
    """Retourne les dernieres entrees du log d'audit."""
    _flush_audit()
    entries = []
    try:
        if _AUDIT_LOG_FILE.exists():
            lines = _AUDIT_LOG_FILE.read_text().strip().split("\n")
            for line in lines[-limit:]:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except Exception:
        pass
    return entries


# ── Admin auth helper ──

def require_admin(request: Request) -> str:
    """Verifie l'admin key depuis le header X-Admin-Key uniquement.
    Log chaque tentative dans l'audit log."""
    admin_key = os.getenv("ADMIN_KEY", "")
    if not admin_key:
        raise HTTPException(500, "ADMIN_KEY not configured")
    ip = request.client.host if request.client else "unknown"
    # Header only (secure)
    import hmac
    key = request.headers.get("X-Admin-Key", "")
    if not hmac.compare_digest(key, admin_key):
        audit_log("admin_auth_failed", ip, f"method=header path={request.url.path}")
        raise HTTPException(403, "Unauthorized")
    audit_log("admin_auth_ok", ip, f"method=header path={request.url.path}")
    return key


# ── JWT secret validation ──

def check_jwt_secret():
    """Verifie que JWT_SECRET n'est pas un default insecure. Appele au demarrage."""
    secret = os.getenv("JWT_SECRET", "")
    insecure_defaults = ["", "secret", "changeme", "your-secret-key", "maxia", "test"]
    if secret.lower() in insecure_defaults or len(secret) < 16:
        print("[SECURITY] ⚠️  JWT_SECRET is insecure or missing! Set a strong random value (32+ chars).")
        return False
    return True

# ── Content filtering ──

_compiled_patterns = [re.compile(p) for p in BLOCKED_PATTERNS]

def check_content_safety(text: str, field_name: str = "content") -> None:
    """Verifie qu'un texte ne contient pas de contenu interdit."""
    lower = text.lower()
    for word in BLOCKED_WORDS:
        if word in lower:
            raise HTTPException(400, f"ART.1 — Contenu bloque dans {field_name}")
    for pattern in _compiled_patterns:
        if pattern.search(text):
            raise HTTPException(400, f"ART.1 — Contenu interdit detecte dans {field_name}")


# ── Rate limiting (Redis-backed with in-memory fallback) ──

_rate_store: dict = defaultdict(list)
RATE_LIMIT = 60
RATE_WINDOW = 60
_RATE_STORE_MAX_KEYS = 10000

# Redis client reference — set via set_redis_client() at startup
_redis_client = None


def set_redis_client(client):
    """Inject the Redis client for rate limiting. Called from lifespan."""
    global _redis_client
    _redis_client = client


def _cleanup_rate_store():
    """Evite la fuite memoire en nettoyant les entrees expirees."""
    now = time.time()
    expired_keys = [ip for ip, ts in _rate_store.items() if not ts or ts[-1] < now - RATE_WINDOW * 2]
    for ip in expired_keys:
        del _rate_store[ip]


async def check_rate_limit_async(request: Request) -> None:
    """Async rate limit — uses Redis if available, else in-memory fallback."""
    ip = request.client.host if request.client else "unknown"
    if _redis_client is not None and _redis_client.is_connected:
        allowed = await _redis_client.rate_limit_check(ip, RATE_LIMIT, RATE_WINDOW)
        if not allowed:
            raise HTTPException(429, "Rate limit depasse. Reessayez dans 1 minute.")
        return
    # Fallback: in-memory (sync path)
    _check_rate_limit_memory(ip)


def check_rate_limit(request: Request) -> None:
    """Synchronous rate limit fallback (in-memory only). 60 req/min."""
    ip = request.client.host if request.client else "unknown"
    _check_rate_limit_memory(ip)


# ── Smart Rate Limiting (free vs paid endpoints) ──

_FREE_PATH_KEYWORDS = [
    "prices", "candles", "leaderboard", "templates", "trending",
    "fear-greed", "stocks", "gpu/tiers", "sentiment", "token-risk",
    "wallet-analysis", "defi", "sla", "clone/stats",
    "mcp", "docs-html", "docs",
]

_smart_rate_info: dict = defaultdict(dict)


def check_rate_limit_smart(identifier: str, endpoint: str = "") -> bool:
    """
    Smart rate limiting: free endpoints are unlimited, paid endpoints get 60 req/min.
    Returns True if request is allowed, False if rate-limited.
    Sets rate limit info in _smart_rate_info[identifier] for response headers.
    """
    path = endpoint.lower()

    # FREE endpoints — always allowed
    for kw in _FREE_PATH_KEYWORDS:
        if kw in path:
            _smart_rate_info[identifier] = {
                "X-RateLimit-Limit": "unlimited",
                "X-RateLimit-Remaining": "unlimited",
                "X-RateLimit-Tier": "free",
            }
            return True

    # PAID / AUTH endpoints — 60 req/min sliding window
    now = time.time()
    _rate_store[identifier] = [t for t in _rate_store[identifier] if t > now - RATE_WINDOW]
    remaining = max(0, RATE_LIMIT - len(_rate_store[identifier]))

    _smart_rate_info[identifier] = {
        "X-RateLimit-Limit": str(RATE_LIMIT),
        "X-RateLimit-Remaining": str(remaining),
        "X-RateLimit-Reset": str(int(now + RATE_WINDOW)),
        "X-RateLimit-Tier": "paid",
    }

    if len(_rate_store[identifier]) >= RATE_LIMIT:
        return False

    _rate_store[identifier].append(now)
    if len(_rate_store) > _RATE_STORE_MAX_KEYS:
        _cleanup_rate_store()
    # Prune stale entries from _smart_rate_info to avoid memory leak
    if len(_smart_rate_info) > 5000:
        keys_to_remove = list(_smart_rate_info.keys())[:len(_smart_rate_info) - 5000]
        for k in keys_to_remove:
            _smart_rate_info.pop(k, None)
    return True


def get_rate_limit_info(identifier: str) -> dict:
    """Return rate limit headers info for a given identifier."""
    return _smart_rate_info.get(identifier, {})


def _check_rate_limit_memory(ip: str) -> None:
    """In-memory sliding window rate check."""
    now = time.time()
    _rate_store[ip] = [t for t in _rate_store[ip] if t > now - RATE_WINDOW]
    if len(_rate_store[ip]) >= RATE_LIMIT:
        raise HTTPException(429, "Rate limit depasse. Reessayez dans 1 minute.")
    _rate_store[ip].append(now)
    # Proactive cleanup every 1000 keys (not just at max)
    if len(_rate_store) > 1000 and len(_rate_store) % 100 == 0:
        _cleanup_rate_store()
    if len(_rate_store) > _RATE_STORE_MAX_KEYS:
        _cleanup_rate_store()


# ── Burst protection (anti-DDoS) ──

_burst_store: dict = defaultdict(list)
BURST_LIMIT = 20       # max 20 requetes
BURST_WINDOW = 2       # en 2 secondes
_burst_bans: dict = {} # {ip: ban_until_timestamp}
BURST_BAN_DURATION = 60  # ban 60 secondes apres un burst


def check_burst_limit(ip: str) -> bool:
    """Verifie les bursts (>20 req/2s). Retourne True si OK, False si bloque."""
    now = time.time()

    # Verifie si l'IP est encore bannie
    if ip in _burst_bans:
        if now < _burst_bans[ip]:
            return False
        else:
            del _burst_bans[ip]

    _burst_store[ip] = [t for t in _burst_store[ip] if t > now - BURST_WINDOW]
    if len(_burst_store[ip]) >= BURST_LIMIT:
        _burst_bans[ip] = now + BURST_BAN_DURATION
        print(f"[Security] BURST BAN: {ip} ({len(_burst_store[ip])} req/{BURST_WINDOW}s)")
        return False

    _burst_store[ip].append(now)
    return True


def get_burst_ban_remaining(ip: str) -> int:
    """Retourne les secondes restantes de ban, 0 si pas banni."""
    if ip not in _burst_bans:
        return 0
    remaining = _burst_bans[ip] - time.time()
    return max(0, int(remaining))


# ── Garde-fous financiers (Art.4 V12) avec persistance fichier ──

_SPEND_FILE = Path(__file__).parent / ".daily_spend.json"


def _load_spend_log() -> dict:
    """Charge le log de depenses depuis le fichier."""
    try:
        if _SPEND_FILE.exists():
            data = json.loads(_SPEND_FILE.read_text())
            if data.get("date") == time.strftime("%Y-%m-%d"):
                return data
    except Exception:
        pass
    return {"date": time.strftime("%Y-%m-%d"), "total": 0.0, "tx_count": 0}


def _save_spend_log(log: dict):
    """Sauvegarde le log de depenses sur disque."""
    try:
        _SPEND_FILE.write_text(json.dumps(log))
    except Exception as e:
        print(f"[Security] Erreur sauvegarde spend log: {e}")


def check_financial_limits(amount_usdc: float) -> dict:
    """
    Verifie les limites financieres avant une depense de l'agent.
    Retourne {"allowed": True/False, "reason": "..."}
    """
    log = _load_spend_log()

    # Limite par transaction
    if amount_usdc > GROWTH_MAX_SPEND_TX:
        return {
            "allowed": False,
            "reason": f"Montant {amount_usdc} USDC depasse la limite par tx ({GROWTH_MAX_SPEND_TX} USDC)",
        }

    # Limite journaliere
    if log["total"] + amount_usdc > GROWTH_MAX_SPEND_DAY:
        return {
            "allowed": False,
            "reason": f"Budget journalier epuise ({log['total']:.2f}/{GROWTH_MAX_SPEND_DAY} USDC)",
        }

    return {"allowed": True, "reason": "OK"}


def record_spend(amount_usdc: float):
    """Enregistre une depense dans le compteur journalier (persiste sur disque)."""
    log = _load_spend_log()
    log["total"] += amount_usdc
    log["tx_count"] += 1
    _save_spend_log(log)


def get_daily_spend_stats() -> dict:
    """Retourne les stats de depenses du jour."""
    log = _load_spend_log()
    return {
        "date": log["date"],
        "total_usdc": log["total"],
        "tx_count": log["tx_count"],
        "limit_usdc": GROWTH_MAX_SPEND_DAY,
        "remaining_usdc": max(0, GROWTH_MAX_SPEND_DAY - log["total"]),
    }


# ── CEO spending limits (Art.4 V12 — PC local -> VPS) ──

_CEO_DAILY_LIMITS = {
    "update_price": {"max_per_day": 20, "max_amount_usd": 0},
    "post_tweet": {"max_per_day": 10, "max_amount_usd": 0},
    "post_reddit": {"max_per_day": 5, "max_amount_usd": 0},
    "send_alert": {"max_per_day": 50, "max_amount_usd": 0},
    "contact_prospect": {"max_per_day": 10, "max_amount_usd": 1.0},
    "toggle_agent": {"max_per_day": 10, "max_amount_usd": 0},
    "adjust_budget": {"max_per_day": 5, "max_amount_usd": 50.0},
    "execute_trade": {"max_per_day": 3, "max_amount_usd": 100.0},
    "deploy_page": {"max_per_day": 5, "max_amount_usd": 0},
    "browse_competitor": {"max_per_day": 20, "max_amount_usd": 0},
    "generate_report": {"max_per_day": 10, "max_amount_usd": 0},
}

_ceo_action_counts: dict = {}
_ceo_action_date: str = ""


def check_ceo_spending_limit(action: str, amount_usd: float = 0) -> dict:
    """Verifie les limites de depenses pour une action CEO.
    Returns: {"allowed": True/False, "reason": "..."}
    """
    global _ceo_action_counts, _ceo_action_date

    today = time.strftime("%Y-%m-%d")
    if _ceo_action_date != today:
        _ceo_action_counts = {}
        _ceo_action_date = today

    limits = _CEO_DAILY_LIMITS.get(action)
    if not limits:
        # Action inconnue — prudence
        return {"allowed": True, "reason": "Unknown action type — no specific limit"}

    # Limite par jour
    count = _ceo_action_counts.get(action, 0)
    if count >= limits["max_per_day"]:
        return {
            "allowed": False,
            "reason": f"CEO daily limit reached for {action}: {count}/{limits['max_per_day']}",
        }

    # Limite par montant
    if limits["max_amount_usd"] > 0 and amount_usd > limits["max_amount_usd"]:
        return {
            "allowed": False,
            "reason": f"Amount ${amount_usd} exceeds CEO limit for {action} (${limits['max_amount_usd']})",
        }

    return {"allowed": True, "reason": "OK"}


def record_ceo_action(action: str):
    """Enregistre une action CEO dans le compteur quotidien."""
    global _ceo_action_counts, _ceo_action_date
    today = time.strftime("%Y-%m-%d")
    if _ceo_action_date != today:
        _ceo_action_counts = {}
        _ceo_action_date = today
    _ceo_action_counts[action] = _ceo_action_counts.get(action, 0) + 1
