"""MAXIA Art.1 V12 — Securite, filtrage contenu, rate limiting, burst protection, audit, garde-fous financiers

Inclut:
- Art.1: Filtrage contenu (check_content_safety)
- Art.4: Garde-fous financiers (check_financial_limits)
- Art.25: OFAC Sanctions — liste locale + Chainalysis Oracle on-chain (EVM)
- Art.26: Rate limit tiers (free/pro/enterprise)
- Burst protection anti-DDoS
"""
import logging
import re, time, json, os
from collections import defaultdict
from pathlib import Path
from datetime import datetime
from fastapi import HTTPException, Request
import httpx
from config import (
    BLOCKED_WORDS, BLOCKED_PATTERNS,
    GROWTH_MAX_SPEND_DAY, GROWTH_MAX_SPEND_TX,
)

# ── Validation d'adresses wallet (EVM + Solana) ──
_EVM_ADDR_RE = re.compile(r'^0x[0-9a-fA-F]{40}$')
_SOLANA_ADDR_RE = re.compile(r'^[1-9A-HJ-NP-Za-km-z]{32,44}$')


def validate_wallet_address(address: str, chain: str = "auto") -> bool:
    """Valide le format d'une adresse wallet (EVM 0x... ou Solana base58).
    chain: 'evm', 'solana', ou 'auto' (detection automatique)."""
    if not address or len(address) < 20:
        return False
    if chain == "evm" or address.startswith("0x"):
        return bool(_EVM_ADDR_RE.match(address))
    return bool(_SOLANA_ADDR_RE.match(address))


# ── Audit log (admin actions) ──

_AUDIT_LOG_FILE = Path(__file__).parent / ".audit_log.jsonl"
_audit_buffer: list = []


def audit_log(action: str, ip: str, details: str = "", user: str = "admin"):
    """Log une action admin avec timestamp, IP, details."""
    entry = {
        "ts": datetime.now().isoformat(),
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
    ip = get_real_ip(request)
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


def check_admin_key():
    """H4: Verifie que ADMIN_KEY est suffisamment fort en production. Appele au demarrage."""
    from config import SANDBOX_MODE
    admin_key = os.getenv("ADMIN_KEY", "")
    if SANDBOX_MODE:
        return True  # Pas critique en mode sandbox/dev
    if not admin_key or len(admin_key) < 16:
        print("[SECURITY] CRITICAL: ADMIN_KEY is missing or too short (< 16 chars)!")
        print("[SECURITY] CRITICAL: Admin endpoints are vulnerable. Set a strong ADMIN_KEY in .env (32+ chars).")
        return False
    return True


# ── IP extraction securisee (anti-spoofing) ──

# Proxies de confiance — seuls ces IPs peuvent injecter X-Forwarded-For
_TRUSTED_PROXIES = {"127.0.0.1", "::1"}


def get_real_ip(request: Request) -> str:
    """Extrait l'IP reelle du client de maniere securisee.

    Ne fait confiance a X-Forwarded-For QUE si la requete vient d'un proxy connu.
    Prend la DERNIERE IP de la chaine (la plus proche du proxy, donc la plus fiable).
    """
    client_ip = request.client.host if request.client else "unknown"

    # X-Forwarded-For seulement si la connexion directe vient d'un proxy de confiance
    if client_ip in _TRUSTED_PROXIES:
        xff = request.headers.get("X-Forwarded-For", "")
        if xff:
            # Derniere IP de la chaine = la plus fiable (ajoutee par notre proxy)
            parts = [p.strip() for p in xff.split(",") if p.strip()]
            if parts:
                return parts[-1]

    return client_ip


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
    """Async rate limit — uses redis_rate_limiter (daily quotas) + redis_client (per-minute).

    Ordre de priorite :
    1. redis_rate_limiter (INCR+EXPIRE, quotas journaliers par tier)
    2. redis_client existant (sorted sets, 60 req/min)
    3. In-memory fallback
    """
    ip = get_real_ip(request)
    # 1. Redis rate limiter (quotas journaliers par tier)
    try:
        from redis_rate_limiter import check_rate_limit_redis
        allowed = await check_rate_limit_redis(ip, endpoint=request.url.path)
        if not allowed:
            raise HTTPException(429, "Rate limit journalier depasse. Reessayez demain ou passez en tier Pro.")
    except ImportError:
        pass
    except HTTPException:
        raise
    except Exception:
        pass
    # 2. Redis client existant (per-minute burst protection)
    if _redis_client is not None and _redis_client.is_connected:
        allowed = await _redis_client.rate_limit_check(ip, RATE_LIMIT, RATE_WINDOW)
        if not allowed:
            raise HTTPException(429, "Rate limit depasse. Reessayez dans 1 minute.")
        return
    # 3. Fallback: in-memory (sync path)
    _check_rate_limit_memory(ip)


def check_rate_limit(request: Request) -> None:
    """Rate limit — essaie Redis (redis_rate_limiter) puis fallback in-memory.

    Redis permet le rate limiting distribue (multi-worker/multi-instance).
    Si Redis est indisponible, degrade gracieusement vers le store in-memory local.
    """
    ip = get_real_ip(request)
    # Tentative Redis via redis_rate_limiter (async dans un sync context)
    try:
        import asyncio
        from redis_rate_limiter import check_rate_limit_redis
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # On est dans un contexte async — on ne peut pas run_until_complete
            # Le check Redis sera fait par check_rate_limit_async a la place
            _check_rate_limit_memory(ip)
            return
        allowed = loop.run_until_complete(check_rate_limit_redis(ip, endpoint=request.url.path))
        if not allowed:
            raise HTTPException(429, "Rate limit journalier depasse. Reessayez demain ou passez en tier Pro.")
        return
    except ImportError:
        pass
    except HTTPException:
        raise
    except Exception:
        pass
    # Fallback in-memory
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
    # H3: Ignorer les query params — ne checker que le path
    path = endpoint.split("?", 1)[0].lower()

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


# ── IP-based rate limiting (per-IP, per-minute) ──

_ip_requests: dict = defaultdict(list)  # ip -> [timestamps]
IP_RATE_LIMIT = 100   # max requests per minute per IP
IP_RATE_WINDOW = 60   # seconds


def check_ip_rate_limit(ip: str) -> bool:
    """Returns True if IP is rate-limited (should be blocked)."""
    now = time.time()
    _ip_requests[ip] = [t for t in _ip_requests[ip] if now - t < IP_RATE_WINDOW]
    if len(_ip_requests[ip]) >= IP_RATE_LIMIT:
        return True
    _ip_requests[ip].append(now)
    # Periodic cleanup to prevent memory leak
    if len(_ip_requests) > _RATE_STORE_MAX_KEYS:
        expired = [k for k, ts in _ip_requests.items() if not ts or ts[-1] < now - IP_RATE_WINDOW * 2]
        for k in expired:
            del _ip_requests[k]
    return False


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


# ── OFAC Sanctions Check V2 (Art.25 — Compliance) ──
# Upgrade: Chainalysis Sanctions Oracle (on-chain, EVM) + liste locale etendue + refresh GitHub
# Sources:
# 1. Chainalysis Sanctions Oracle — contrat on-chain EVM, isSanctioned(address)
# 2. Liste locale hardcodee — Tornado Cash, Lazarus, Garantex, Blender, Sinbad, etc.
# 3. Fichier .ofac_addresses.txt — adresses additionnelles
# 4. refresh_ofac_list() — MAJ depuis GitHub (0xB10C/ofac-sanctioned-digital-currency-addresses)

_OFAC_SANCTIONED_ADDRESSES: set = set()
_OFAC_LOADED = False
_OFAC_FILE = Path(__file__).parent / ".ofac_addresses.txt"
_OFAC_LAST_REFRESH: float = 0
_OFAC_REFRESH_INTERVAL = 86400  # 24h entre chaque refresh GitHub

# Chainalysis Sanctions Oracle — meme contrat sur la plupart des chains EVM
# Fonction: isSanctioned(address) returns (bool)
_CHAINALYSIS_ORACLE_DEFAULT = "0x40C57923924B5c5c5455c48D93317139ADDaC8fb"
_CHAINALYSIS_ORACLE_BASE = "0x3A91A31cB3dC49b4db9Ce721F50a9D076c8D739B"

# ABI minimal pour isSanctioned(address) — function selector 0xdfb80831
_IS_SANCTIONED_SELECTOR = "0xdfb80831"

# RPCs par chain pour l'appel Chainalysis (utilise les RPCs deja configurees dans config)
_CHAINALYSIS_RPCS = {
    "ethereum": ("ETH_RPC", "https://eth.llamarpc.com", _CHAINALYSIS_ORACLE_DEFAULT),
    "base": ("BASE_RPC", "https://mainnet.base.org", _CHAINALYSIS_ORACLE_BASE),
    "polygon": (None, "https://polygon-rpc.com", _CHAINALYSIS_ORACLE_DEFAULT),
    "arbitrum": (None, "https://arb1.arbitrum.io/rpc", _CHAINALYSIS_ORACLE_DEFAULT),
    "avalanche": (None, "https://api.avax.network/ext/bc/C/rpc", _CHAINALYSIS_ORACLE_DEFAULT),
    "bnb": (None, "https://bsc-dataseed.binance.org", _CHAINALYSIS_ORACLE_DEFAULT),
    "optimism": (None, "https://mainnet.optimism.io", _CHAINALYSIS_ORACLE_DEFAULT),
}


def _load_ofac_list():
    """Charge la liste OFAC — adresses hardcodees + fichier local."""
    global _OFAC_SANCTIONED_ADDRESSES, _OFAC_LOADED
    if _OFAC_LOADED:
        return

    # Liste etendue d'adresses sanctionnees connues (OFAC SDN + UE + UK)
    known_sanctioned = {
        # ═══ Tornado Cash (OFAC Aug 2022, Nov 2022) ═══
        "0x8589427373D6D84E98730D7795D8f6f8731FDA16",
        "0x722122dF12D4e14e13Ac3b6895a86e84145b6967",
        "0xDD4c48C0B24039969fC16D1cdF626eaB821d3384",
        "0xd90e2f925DA726b50C4Ed8D0Fb90Ad053324F31b",
        "0xd96f2B1c14Db8458374d9Aca76E26c3D18364307",
        "0x4736dCf1b7A3d580672CcE6E7c65cd5cc9cFBfA9",
        "0xD4B88Df4D29F5CedD6857912842cff3b20C8Cfa3",
        "0x910Cbd523D972eb0a6f4cAe4618aD62622b39DbF",
        "0xA160cdAB225685dA1d56aa342Ad8841c3b53f291",
        "0xFD8610d20aA15b7B2E3Be39B396a1bC3516c7144",
        "0xF60dD140cFf0706bAE9Cd734Ac3683731B816CeD",
        "0x179f48C78f57A3A78f0608cC9197B8972921d1D2",
        "0xb1C8094B234DcE6e03f10a5b673c1d8C69739A00",
        "0x84443CFd09A48AF6eF360C6976C5392aC5023a1F",
        "0xd47438C816c9E7f2E2888E060936a499Af9582b3",
        "0x330bdFADE01eE9bF63C209Ee33102DD334618e0a",
        "0x1E34A77868E19A6647b1f2F47B51ed72dEDE95DD",
        "0xba214c1c1928a32Bffe790263E38B4Af9bFCD659",
        "0xb6f5ec1A0a9cd1526536D3F0426c429529471F40",
        "0x527653eA119F3E6a1F5BD18fbF4714081D7B31ce",
        "0x58E8dCC13BE9780fC42E8723D8EaD4CF46943dF2",
        "0xD691F27f38B395864Ea86CfC7253969B409c362d",
        "0xaEaaC358560e11f52454D997AAFF2c5731B6f8a6",
        "0x1356c899D8C9467C7f71C195612F8A395aBf2f0a",
        "0xA60C772958a3eD56c1F15dD055bA37AC8e523a0D",
        "0x169AD27A470D064DEDE56a2D3ff727986b15D52B",
        "0x0836222F2B2B24A3F36f98668Ed8F0B38D1a872f",
        "0x178169B423a011fff22B9e3F3abeA13571f90Ec3",
        "0x610B717796ad172B316836AC95a2ffad065CeaB4",
        "0xbB93e510BbCD0B7beb5A853875f9eC60275CF498",
        # ═══ Lazarus Group / DPRK (EVM) ═══
        "0x098B716B8Aaf21512996dC57EB0615e2383E2f96",
        "0xa7e5DEDdBD51b0D2B68798F94d1B34B1f0b2ca05",
        "0xfEC8A60023265364D066a1212fDE3930F6Ae9b7c",
        "0x53b6936513e738f44FB50d2b9476730C0Ab3Bfc1",
        "0x3CBdeD43EFdAf0FC77b9C55F6fC9988fCC9b757d",
        "0x47CE0C6eD5B0Ce3d3A51fdb1C52DC66a7c3c2936",
        "0xC1b634853Cb333D3aD8663715b08f41A3Aec47cC",
        "0x1da5821544e25c636c1417Ba96Ade4Cf6D2f9B5A",
        "0x7F367cC41522cE07553e823bf3be79A889DEbe1B",
        "0x9F4cda013E354b8fC285BF4b9A60460cEe7f7Ea9",
        # ═══ Lazarus Group / DPRK (Solana) ═══
        "2vftDntVBDE6QLLkMxzPWZMZKHS3X8hFkveUQMBijZJZ",
        "CVXJ7LpK1RnHaEWxz3DXadqjfCPe5bkU2HZjwfbHqmvX",
        "BbykCqVvExXqLJqt97gMt7kXjH9bSLNz8tMdtFV6cuvP",
        # ═══ Garantex (Russie, OFAC Avr 2022) ═══
        "0x6f1cA141A28907F78Ebaa64f83D078645f73519D",
        "0x48549A34AE37b12F6a30566245176994e17C6b4A",
        "0x5512d943eD1f7c8a43F3435C85F7aB68b30121b0",
        # ═══ Blender.io (OFAC Mai 2022) ═══
        "0x94A1B5CdB22c43faab4AbEb5c74999895464Ddaf",
        "0xf3701f445b6bdafeDbca97D1e477357839e4120d",
        "0x36654F0bFDb33443B84F8AFAB3D28F63c61d5789",
        # ═══ Sinbad.io (OFAC Nov 2023) ═══
        "0x25B60668719De2a837e97F758Bb0509A0DC4C7F1",
        "bc1qu9dgflqxw4eyhn2cdrsjlfmeafy5q8dh66a0xl",  # BTC
        "bc1q6xptve5q4rlu59kunh3evsrgf4xr8vmmnfqfl2",  # BTC
        # ═══ Chatex (OFAC Nov 2021) ═══
        "0x6aCA8a28600101599e12F67Fa8EC11aE02F3a7ba",
        # ═══ Suex (OFAC Sep 2021) ═══
        "0x2f389cE8bD8ff92De3402FFCe4691d17fC4f6535",
        "0x19Aa5Fe80D33a56D56c78e82eA5E50E5d80b4dff",
    }
    _OFAC_SANCTIONED_ADDRESSES.update({addr.lower() for addr in known_sanctioned})

    # Charger les adresses additionnelles depuis le fichier local
    try:
        if _OFAC_FILE.exists():
            for line in _OFAC_FILE.read_text().strip().split("\n"):
                addr = line.strip()
                if addr and not addr.startswith("#"):
                    _OFAC_SANCTIONED_ADDRESSES.add(addr.lower())
    except Exception:
        pass

    _OFAC_LOADED = True
    print(f"[OFAC] Loaded {len(_OFAC_SANCTIONED_ADDRESSES)} sanctioned addresses (local list)")


def _is_evm_address(address: str) -> bool:
    """Detecte si une adresse est au format EVM (0x + 40 hex chars)."""
    return bool(re.match(r"^0x[0-9a-fA-F]{40}$", address))


def _get_chain_rpc(chain: str) -> str:
    """Retourne l'URL RPC pour une chain EVM (config ou fallback public)."""
    config_key, fallback_url, _ = _CHAINALYSIS_RPCS.get(chain, (None, None, None))
    if config_key:
        env_val = os.getenv(config_key, "")
        if env_val:
            return env_val
    return fallback_url or ""


async def check_chainalysis_oracle(address: str, chain: str = "ethereum") -> dict:
    """Verifie une adresse EVM via le Chainalysis Sanctions Oracle on-chain.

    Appel eth_call sur le contrat isSanctioned(address).
    Gratuit, pas de cle API — lecture on-chain directe.

    Args:
        address: Adresse EVM (0x...)
        chain: Nom de la chain EVM (ethereum, base, polygon, arbitrum, etc.)

    Returns:
        {"sanctioned": bool, "source": "chainalysis_oracle", "chain": str}
        ou {"error": "..."} si le call echoue
    """
    chain_lower = chain.lower()
    chain_info = _CHAINALYSIS_RPCS.get(chain_lower)
    if not chain_info:
        return {"error": f"Chain '{chain}' not supported for Chainalysis check", "sanctioned": False}

    rpc_url = _get_chain_rpc(chain_lower)
    if not rpc_url:
        return {"error": f"No RPC available for chain '{chain}'", "sanctioned": False}

    oracle_address = chain_info[2]

    # Encoder l'appel isSanctioned(address)
    # function selector: 0xdfb80831
    # address encodee: padded 32 bytes
    addr_clean = address.lower().replace("0x", "")
    call_data = f"{_IS_SANCTIONED_SELECTOR}000000000000000000000000{addr_clean}"

    try:
        async with httpx.AsyncClient(timeout=8) as client:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "eth_call",
                "params": [
                    {"to": oracle_address, "data": call_data},
                    "latest",
                ],
            }
            resp = await client.post(rpc_url, json=payload)
            if resp.status_code != 200:
                return {"error": f"RPC HTTP {resp.status_code}", "sanctioned": False}

            result = resp.json().get("result", "0x")

            # Le retour est un bool encode: 0x...0001 = true, 0x...0000 = false
            if result and len(result) >= 66:
                is_sanctioned = int(result, 16) != 0
            elif result == "0x":
                # Contrat non deploye ou erreur — ne pas bloquer
                return {"error": "Empty response from oracle", "sanctioned": False}
            else:
                is_sanctioned = int(result, 16) != 0 if result else False

            if is_sanctioned:
                audit_log(
                    "chainalysis_hit", "system",
                    f"Chainalysis Oracle: sanctioned on {chain}: {address[:20]}..."
                )

            return {
                "sanctioned": is_sanctioned,
                "source": "chainalysis_oracle",
                "chain": chain_lower,
                "oracle": oracle_address,
            }

    except httpx.TimeoutException:
        return {"error": f"Chainalysis Oracle timeout on {chain}", "sanctioned": False}
    except Exception as e:
        return {"error": f"Chainalysis Oracle error: {str(e)[:100]}", "sanctioned": False}


def check_ofac_wallet(address: str) -> dict:
    """Verifie si un wallet est sur la liste OFAC (check local synchrone).
    Pour les chains EVM, utiliser aussi check_ofac_wallet_enhanced() (async).

    Returns: {"sanctioned": bool, "risk": "clear"|"sanctioned"|"unknown"}
    """
    _load_ofac_list()
    if not address:
        return {"sanctioned": False, "risk": "unknown", "address": ""}

    addr_lower = address.lower().strip()

    if addr_lower in _OFAC_SANCTIONED_ADDRESSES:
        audit_log("ofac_hit", "system", f"Sanctioned address detected: {address[:20]}...")
        return {
            "sanctioned": True,
            "risk": "sanctioned",
            "address": address,
            "action": "Transaction blocked — OFAC sanctioned address",
        }

    return {"sanctioned": False, "risk": "clear", "address": address}


async def check_ofac_wallet_enhanced(address: str, chain: str = "auto") -> dict:
    """Check OFAC complet: liste locale + Chainalysis Oracle (EVM).

    Pour les adresses EVM, interroge aussi le contrat Chainalysis on-chain.
    Pour Solana/non-EVM, utilise uniquement la liste locale.

    Args:
        address: Adresse du wallet (EVM 0x... ou Solana base58)
        chain: Chain EVM specifique, ou "auto" pour detecter

    Returns:
        {"sanctioned": bool, "risk": str, "sources_checked": list}
    """
    _load_ofac_list()
    if not address:
        return {"sanctioned": False, "risk": "unknown", "address": "", "sources_checked": []}

    addr_lower = address.lower().strip()
    sources_checked = ["local_list"]

    # 1. Check liste locale (rapide, synchrone)
    if addr_lower in _OFAC_SANCTIONED_ADDRESSES:
        audit_log("ofac_hit", "system", f"Local list: sanctioned address: {address[:20]}...")
        return {
            "sanctioned": True,
            "risk": "sanctioned",
            "address": address,
            "action": "Transaction blocked — OFAC sanctioned address (local list)",
            "sources_checked": sources_checked,
        }

    # 2. Check Chainalysis Oracle pour les adresses EVM
    if _is_evm_address(address):
        # Determiner la chain si "auto"
        if chain == "auto":
            chain = "ethereum"  # Default: Ethereum (couverture la plus large)

        oracle_result = await check_chainalysis_oracle(address, chain)
        sources_checked.append(f"chainalysis_oracle_{chain}")

        if oracle_result.get("sanctioned"):
            return {
                "sanctioned": True,
                "risk": "sanctioned",
                "address": address,
                "action": f"Transaction blocked — Chainalysis Oracle ({chain})",
                "sources_checked": sources_checked,
            }

        if "error" in oracle_result:
            # Oracle indisponible — on continue avec le resultat local (clear)
            sources_checked.append(f"oracle_error: {oracle_result['error'][:50]}")

    return {
        "sanctioned": False,
        "risk": "clear",
        "address": address,
        "sources_checked": sources_checked,
    }


def require_ofac_clear(address: str, field: str = "wallet"):
    """Raise 403 si le wallet est sanctionne OFAC (check local synchrone).
    Pour un check complet avec Chainalysis, utiliser require_ofac_clear_async()."""
    result = check_ofac_wallet(address)
    if result["sanctioned"]:
        raise HTTPException(
            403,
            f"Transaction blocked: {field} address is on the OFAC sanctions list. "
            "MAXIA complies with international sanctions regulations."
        )


async def require_ofac_clear_async(address: str, chain: str = "auto", field: str = "wallet"):
    """Raise 403 si le wallet est sanctionne — check local + Chainalysis Oracle (async)."""
    result = await check_ofac_wallet_enhanced(address, chain)
    if result["sanctioned"]:
        raise HTTPException(
            403,
            f"Transaction blocked: {field} address is on the OFAC sanctions list. "
            "MAXIA complies with international sanctions regulations."
        )


async def refresh_ofac_list() -> dict:
    """Met a jour la liste OFAC depuis le repo GitHub 0xB10C.

    Telecharge les listes d'adresses sanctionnees (BTC, ETH, USDT) et les ajoute
    a la liste locale. Peut etre appele periodiquement (scheduler).

    Returns:
        {"added": int, "total": int, "sources": list}
    """
    global _OFAC_LAST_REFRESH
    _load_ofac_list()

    now = time.time()
    if now - _OFAC_LAST_REFRESH < _OFAC_REFRESH_INTERVAL:
        return {
            "added": 0,
            "total": len(_OFAC_SANCTIONED_ADDRESSES),
            "skipped": "refresh too recent",
            "next_refresh_in_s": int(_OFAC_REFRESH_INTERVAL - (now - _OFAC_LAST_REFRESH)),
        }

    base_url = "https://raw.githubusercontent.com/0xB10C/ofac-sanctioned-digital-currency-addresses/lists"
    files = [
        "sanctioned_addresses_XBT.txt",   # Bitcoin
        "sanctioned_addresses_ETH.txt",   # Ethereum
        "sanctioned_addresses_USDT.txt",  # USDT (multi-chain)
        "sanctioned_addresses_XMR.txt",   # Monero
        "sanctioned_addresses_LTC.txt",   # Litecoin
        "sanctioned_addresses_ZEC.txt",   # Zcash
        "sanctioned_addresses_DASH.txt",  # Dash
        "sanctioned_addresses_XRP.txt",   # XRP
    ]

    added = 0
    sources_fetched = []
    initial_count = len(_OFAC_SANCTIONED_ADDRESSES)

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            for filename in files:
                try:
                    resp = await client.get(f"{base_url}/{filename}")
                    if resp.status_code == 200:
                        lines = resp.text.strip().split("\n")
                        for line in lines:
                            addr = line.strip()
                            if addr and not addr.startswith("#"):
                                addr_lower = addr.lower()
                                if addr_lower not in _OFAC_SANCTIONED_ADDRESSES:
                                    _OFAC_SANCTIONED_ADDRESSES.add(addr_lower)
                                    added += 1
                        sources_fetched.append(filename)
                    # 404 = fichier pas encore disponible, ignorer silencieusement
                except Exception:
                    continue

    except Exception as e:
        print(f"[OFAC] Refresh error: {e}")
        return {"added": added, "total": len(_OFAC_SANCTIONED_ADDRESSES), "error": "An error occurred"[:100]}

    # Persister les nouvelles adresses dans le fichier local
    if added > 0:
        try:
            existing = set()
            if _OFAC_FILE.exists():
                existing = set(_OFAC_FILE.read_text().strip().split("\n"))
            all_addrs = existing | {a for a in _OFAC_SANCTIONED_ADDRESSES}
            # Ecrire toutes les adresses (sans doublons)
            _OFAC_FILE.write_text(
                "# OFAC sanctioned addresses — auto-updated by MAXIA\n"
                f"# Last refresh: {datetime.utcnow().isoformat()}\n"
                + "\n".join(sorted(a for a in all_addrs if a and not a.startswith("#")))
                + "\n"
            )
        except Exception as e:
            print(f"[OFAC] Error saving to file: {e}")

    _OFAC_LAST_REFRESH = now
    print(f"[OFAC] Refresh complete: +{added} addresses (total: {len(_OFAC_SANCTIONED_ADDRESSES)}) from {len(sources_fetched)} sources")

    return {
        "added": added,
        "total": len(_OFAC_SANCTIONED_ADDRESSES),
        "sources": sources_fetched,
    }


# ── Rate Limit Tiers (Art.26 — Tiered Access) ──

RATE_LIMIT_TIERS = {
    "free":       {"req_per_day": 100,   "req_per_min": 5,   "label": "Free"},
    "pro":        {"req_per_day": 10000, "req_per_min": 100, "label": "Pro"},
    "enterprise": {"req_per_day": 100000,"req_per_min": 1000,"label": "Enterprise"},
}

# Agent tier mapping (loaded from DB or set via API key prefix)
_agent_tiers: dict = {}  # api_key -> tier name


def set_agent_rate_tier(api_key: str, tier: str):
    """Set rate limit tier for an agent. Called when agent upgrades."""
    if tier in RATE_LIMIT_TIERS:
        _agent_tiers[api_key] = tier


def get_agent_rate_tier(api_key: str) -> str:
    """Get rate limit tier for an agent. Default: free."""
    return _agent_tiers.get(api_key, "free")


def check_rate_limit_tiered(api_key: str) -> dict:
    """Check rate limits based on agent's tier. Returns {"allowed": bool, "tier": str, ...}"""
    tier_name = get_agent_rate_tier(api_key)
    tier = RATE_LIMIT_TIERS[tier_name]
    now = time.time()

    # Per-minute check
    key_min = f"tier:{api_key}:min"
    _rate_store[key_min] = [t for t in _rate_store.get(key_min, []) if t > now - 60]
    if len(_rate_store[key_min]) >= tier["req_per_min"]:
        return {
            "allowed": False,
            "tier": tier_name,
            "reason": f"Rate limit: {tier['req_per_min']} req/min ({tier['label']} tier)",
            "limit": tier["req_per_min"],
            "remaining": 0,
            "reset_in_s": 60,
        }

    # Per-day check
    key_day = f"tier:{api_key}:day"
    day_start = now - (now % 86400)
    _rate_store[key_day] = [t for t in _rate_store.get(key_day, []) if t > day_start]
    if len(_rate_store[key_day]) >= tier["req_per_day"]:
        return {
            "allowed": False,
            "tier": tier_name,
            "reason": f"Daily limit: {tier['req_per_day']} req/day ({tier['label']} tier)",
            "limit": tier["req_per_day"],
            "remaining": 0,
            "reset_in_s": int(day_start + 86400 - now),
        }

    _rate_store[key_min].append(now)
    _rate_store[key_day].append(now)

    return {
        "allowed": True,
        "tier": tier_name,
        "limit_min": tier["req_per_min"],
        "remaining_min": tier["req_per_min"] - len(_rate_store[key_min]),
        "limit_day": tier["req_per_day"],
        "remaining_day": tier["req_per_day"] - len(_rate_store[key_day]),
    }
