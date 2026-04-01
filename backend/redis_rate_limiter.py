"""MAXIA Redis Rate Limiter V12 — Rate limiting distribue via Redis avec fallback in-memory

Remplace le rate limiting in-memory de security.py par un systeme Redis-backed :
- Cles Redis atomiques via INCR + EXPIRE (pas de race conditions)
- Format de cle : rate:{ip}:{date} avec TTL 24h
- Tiers : free=100/jour, pro=10000/jour, enterprise=100000/jour
- Degradation gracieuse : fallback in-memory si Redis est indisponible
- Endpoint : GET /api/rate-limit/status — usage courant par IP
"""
import logging
import time, os
from datetime import datetime, timezone
from collections import defaultdict
from fastapi import APIRouter, HTTPException, Request
from security import get_real_ip

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/rate-limit", tags=["rate-limit"])

# ── Config ──

REDIS_URL = os.getenv("REDIS_URL", "")

# Limites par tier (requetes par jour)
TIER_LIMITS = {
    "free": 2000,       # ~200 page views/day (each page = ~10 API calls)
    "pro": 20000,
    "enterprise": 200000,
}

DEFAULT_TIER = "free"
_KEY_TTL_SECONDS = 86400  # 24h TTL sur les cles Redis

# ── Redis client — initialise paresseusement ──

_redis = None
_redis_available = False


async def _get_redis():
    """Initialise la connexion Redis si pas encore fait. Retourne None si indisponible."""
    global _redis, _redis_available
    if _redis is not None:
        return _redis if _redis_available else None
    url = REDIS_URL
    if not url:
        _redis_available = False
        _redis = False  # Marque comme "tente"
        return None
    try:
        import redis.asyncio as aioredis
        _redis = aioredis.from_url(
            url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=3,
        )
        await _redis.ping()
        _redis_available = True
        logger.info("[RateLimit] Redis connecte")
        return _redis
    except Exception as e:
        logger.warning(f"[RateLimit] Redis indisponible ({e}) — fallback in-memory")
        _redis_available = False
        _redis = False
        return None


# ── Fallback in-memory ──

_mem_store: dict = defaultdict(int)  # "ip:date" -> count
_mem_store_max_keys = 50000


def _cleanup_mem_store():
    """Nettoie les entrees expirees du store in-memory."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    expired = [k for k in _mem_store if not k.endswith(today)]
    for k in expired:
        del _mem_store[k]


# ── Fonctions principales ──

async def check_rate_limit_redis(ip: str, endpoint: str = "", tier: str = "") -> bool:
    """Verifie le rate limit pour une IP. Retourne True si la requete est autorisee.

    Utilise Redis (INCR + EXPIRE atomique) si disponible, sinon fallback in-memory.
    Le tier determine la limite : free=100/jour, pro=10000/jour, enterprise=100000/jour.
    """
    if not tier:
        tier = DEFAULT_TIER
    limit = TIER_LIMITS.get(tier, TIER_LIMITS["free"])
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = f"rate:{ip}:{today}"

    # ── Tentative Redis ──
    r = await _get_redis()
    if r:
        try:
            # INCR atomique — cree la cle si elle n'existe pas
            count = await r.incr(key)
            if count == 1:
                # Premiere requete de la journee — positionner le TTL
                await r.expire(key, _KEY_TTL_SECONDS)
            return count <= limit
        except Exception:
            # Redis a plante en cours de route — fallback
            pass

    # ── Fallback in-memory ──
    mem_key = f"{ip}:{today}"
    _mem_store[mem_key] = _mem_store.get(mem_key, 0) + 1

    # Nettoyage periodique
    if len(_mem_store) > _mem_store_max_keys:
        _cleanup_mem_store()

    return _mem_store[mem_key] <= limit


async def get_usage(ip: str) -> dict:
    """Retourne les statistiques d'usage courant pour une IP.

    Inclut : requetes aujourd'hui, limite du tier, pourcentage utilise.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = f"rate:{ip}:{today}"
    count = 0
    source = "memory"

    # ── Tentative Redis ──
    r = await _get_redis()
    if r:
        try:
            val = await r.get(key)
            count = int(val) if val else 0
            source = "redis"
        except Exception:
            pass

    # ── Fallback in-memory ──
    if source == "memory":
        mem_key = f"{ip}:{today}"
        count = _mem_store.get(mem_key, 0)

    # Determiner le tier (free par defaut — le tier reel vient du contexte appelant)
    tier = DEFAULT_TIER
    limit = TIER_LIMITS[tier]

    return {
        "ip": ip,
        "date": today,
        "requests_today": count,
        "tier": tier,
        "limit": limit,
        "remaining": max(0, limit - count),
        "usage_pct": round((count / limit) * 100, 1) if limit > 0 else 0,
        "source": source,
    }


# ── Router FastAPI ──

@router.get("/status")
async def rate_limit_status(request: Request):
    """GET /api/rate-limit/status — Affiche l'usage courant de rate limiting pour l'IP appelante."""
    ip = get_real_ip(request)
    usage = await get_usage(ip)
    return {
        "status": "ok",
        "rate_limit": usage,
        "tiers": {name: {"requests_per_day": limit} for name, limit in TIER_LIMITS.items()},
    }


logger.info("[RateLimit] Redis rate limiter charge")
