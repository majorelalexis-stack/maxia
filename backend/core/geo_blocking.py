"""Geo-blocking middleware — bloque les IPs US sur les routes stocks/trading (compliance)."""

import json
import logging
import time

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse

from core.security import get_real_ip, audit_log

logger = logging.getLogger("maxia.geo")

# ── Cache IP -> country code (24h TTL) ──
_geo_cache: dict[str, tuple[str, float]] = {}
_CACHE_TTL = 86400  # 24 heures

# ── Rate-limit protection pour ip-api.com (45 req/min, on cap a 40) ──
_api_calls_this_minute: int = 0
_api_minute_start: float = 0.0
_API_RATE_LIMIT = 40

# ── Configuration ──
BLOCKED_COUNTRIES: frozenset[str] = frozenset({"US"})
PROTECTED_PREFIXES: tuple[str, ...] = ("/api/stocks/", "/api/exchange/")
BLOCKED_MCP_TOOLS: tuple[str, ...] = ("stocks_buy", "stocks_sell")

# ── IPs privees / localhost — jamais bloquees ──
_PRIVATE_PREFIXES: tuple[str, ...] = ("127.", "10.", "192.168.", "172.16.", "172.17.",
                                       "172.18.", "172.19.", "172.20.", "172.21.",
                                       "172.22.", "172.23.", "172.24.", "172.25.",
                                       "172.26.", "172.27.", "172.28.", "172.29.",
                                       "172.30.", "172.31.")
_PRIVATE_EXACT: frozenset[str] = frozenset({"::1", "localhost", "0.0.0.0"})

GEO_BLOCKED_RESPONSE = JSONResponse(
    status_code=451,
    content={"error": "This service is not available in your region", "code": "GEO_RESTRICTED"},
)


def _is_private_ip(ip: str) -> bool:
    """Retourne True si l'IP est privee ou localhost."""
    if ip in _PRIVATE_EXACT:
        return True
    return ip.startswith(_PRIVATE_PREFIXES)


def _is_protected_path(path: str) -> bool:
    """Retourne True si le path est protege par le geo-blocking."""
    return path.startswith(PROTECTED_PREFIXES)


async def _is_blocked_mcp_call(request: Request, path: str) -> bool:
    """Verifie si un appel MCP cible un outil stocks bloque."""
    if path != "/mcp/tools/call":
        return False
    try:
        body = await request.body()
        if not body:
            return False
        data = json.loads(body)
        tool_name = data.get("name", "") or data.get("tool", "")
        return any(blocked in tool_name for blocked in BLOCKED_MCP_TOOLS)
    except Exception:
        return False


async def _lookup_country(ip: str) -> str | None:
    """Requete ip-api.com pour obtenir le country code. Retourne None en cas d'erreur."""
    global _api_calls_this_minute, _api_minute_start

    now = time.monotonic()

    # Reset compteur chaque minute
    if now - _api_minute_start >= 60:
        _api_calls_this_minute = 0
        _api_minute_start = now

    # Protection rate-limit : skip si on approche la limite
    if _api_calls_this_minute >= _API_RATE_LIMIT:
        logger.warning("[GEO] Rate limit approaching (%d/min), skipping lookup for %s",
                       _api_calls_this_minute, ip)
        return None

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"http://ip-api.com/json/{ip}?fields=countryCode")
        _api_calls_this_minute += 1

        if resp.status_code == 200:
            data = resp.json()
            return data.get("countryCode")
        logger.warning("[GEO] ip-api.com returned status %d for %s", resp.status_code, ip)
        return None
    except Exception as exc:
        logger.warning("[GEO] ip-api.com unreachable for %s: %s", ip, exc)
        return None


def _get_cached_country(ip: str) -> str | None:
    """Retourne le country code depuis le cache, ou None si expire/absent."""
    entry = _geo_cache.get(ip)
    if entry is None:
        return None
    country, ts = entry
    if time.monotonic() - ts > _CACHE_TTL:
        del _geo_cache[ip]
        return None
    return country


def _cache_country(ip: str, country: str) -> None:
    """Stocke le country code dans le cache."""
    _geo_cache[ip] = (country, time.monotonic())


async def geo_block_middleware(request: Request, call_next):
    """Middleware de geo-blocking — bloque les IPs US sur les routes stocks/trading."""
    path = request.url.path

    # Fast-path : verifier si la route est protegee
    is_protected = _is_protected_path(path)
    is_mcp_path = path == "/mcp/tools/call"

    if not is_protected and not is_mcp_path:
        return await call_next(request)

    # Extraire l'IP reelle
    ip = get_real_ip(request)

    # Bypass : IPs privees / localhost
    if _is_private_ip(ip):
        return await call_next(request)

    # Pour MCP, verifier le body avant de faire le lookup geo
    if is_mcp_path:
        if not await _is_blocked_mcp_call(request, path):
            return await call_next(request)

    # Lookup country code (cache d'abord, puis API)
    country = _get_cached_country(ip)
    if country is None:
        country = await _lookup_country(ip)
        if country is not None:
            _cache_country(ip, country)

    # Fail-open : si on n'a pas pu determiner le pays, laisser passer
    if country is None:
        return await call_next(request)

    # Bloquer si pays dans la liste
    if country in BLOCKED_COUNTRIES:
        audit_log("geo_block", ip, f"US IP blocked: {path}")
        logger.info("[GEO] Blocked US IP %s on %s", ip, path)
        return GEO_BLOCKED_RESPONSE

    return await call_next(request)
