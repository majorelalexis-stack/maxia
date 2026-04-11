"""Geofencing middleware — Plan Tier 1 Global.

Replaces the legacy US-only /api/stocks/ filter with a full 4-tier
jurisdiction gate driven by :mod:`compliance.country_filter` and
:mod:`compliance.regulated_routes`.

Design
------

For every incoming request:

1. Skip entirely if ``GEOFENCE_ENABLED=0`` (kill switch).
2. Extract client IP (respecting X-Forwarded-For from trusted proxy).
3. Classify the route (``always_open`` / ``casp`` / ``casp_read`` / ``admin``).
4. Resolve country via MaxMind GeoLite2 (offline, no rate limit) with
   fallback to the legacy ip-api.com path if the MaxMind DB is missing.
5. Look up the country tier (``hard`` / ``license`` / ``caution`` / ``allowed`` / ``unknown``).
6. Decide via a compact decision table:
   * ``hard``  → always 451 (even ``always_open``)
   * ``admin`` route → skip (auth enforces access)
   * ``always_open`` + non-hard → 200
   * ``casp`` / ``casp_read`` + ``license`` / ``unknown`` → 451
   * ``casp`` / ``casp_read`` + ``caution`` → 200 + X-Compliance-Notice header
   * ``casp`` / ``casp_read`` + ``allowed`` → 200
7. Log the decision to :func:`compliance.geofence_log.log_decision`
   (audit trail, compliance retention 5 years).
8. Set response headers for observability.

The module exports ``geo_block_middleware`` (unchanged name) so that
``backend/main.py:543`` works without any edit. We ALSO export
``geofence_middleware`` as an alias for new callers that prefer the
clearer name.

Fail mode
---------

Any unhandled exception in the middleware falls through to the next
middleware with the request untouched (fail-open). This is intentional:
a bug in the geofence must NEVER take down the whole API. The error
is logged loudly so it can be caught and fixed.

Kill switch
-----------

Set the environment variable ``GEOFENCE_ENABLED=0`` to bypass the
middleware entirely. Useful for debugging, A/B rollback, or emergency
if the tier data contains a wrong entry that blocks legitimate traffic.
Default is ``1`` (active).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from core.security import get_real_ip, audit_log
from compliance.country_filter import (
    get_tier,
    get_country_entry,
    check_feature,
)
from compliance.regulated_routes import classify_route

logger = logging.getLogger("maxia.geofence")

# ══════════════════════════════════════════
# Configuration
# ══════════════════════════════════════════

GEOFENCE_ENABLED: bool = os.getenv("GEOFENCE_ENABLED", "1") != "0"

# MaxMind GeoLite2-Country DB location (optional, falls back to ip-api.com)
_GEOIP_DB_PATH = Path(__file__).parent / "data" / "GeoLite2-Country.mmdb"
_geoip_reader: Any = None  # lazy-initialized geoip2.database.Reader

# IP → country cache (24h TTL) — shared with the legacy ip-api.com fallback
_geo_cache: dict[str, tuple[str, float]] = {}
_CACHE_TTL = 86400  # 24 hours

# Rate limit protection for the ip-api.com fallback (45 req/min, cap at 40)
_api_calls_this_minute: int = 0
_api_minute_start: float = 0.0
_API_RATE_LIMIT = 40

# IPs privées / localhost — jamais bloquées
_PRIVATE_PREFIXES: tuple[str, ...] = (
    "127.", "10.", "192.168.",
    "172.16.", "172.17.", "172.18.", "172.19.", "172.20.",
    "172.21.", "172.22.", "172.23.", "172.24.", "172.25.",
    "172.26.", "172.27.", "172.28.", "172.29.", "172.30.", "172.31.",
    "::1", "fc", "fd",  # IPv6 loopback + ULA
)
_PRIVATE_EXACT: frozenset[str] = frozenset({
    "::1", "localhost", "0.0.0.0", "",
    # FastAPI TestClient / Starlette test clients use synthetic hosts
    "testclient", "testserver",
})


# ══════════════════════════════════════════
# MaxMind resolver (preferred)
# ══════════════════════════════════════════

def _get_geoip_reader() -> Any:
    """Lazy-load the MaxMind GeoLite2 DB. Returns None if unavailable."""
    global _geoip_reader
    if _geoip_reader is not None:
        return _geoip_reader
    if not _GEOIP_DB_PATH.exists():
        return None
    try:
        import geoip2.database
        _geoip_reader = geoip2.database.Reader(str(_GEOIP_DB_PATH))
        logger.info("[GEOFENCE] MaxMind GeoLite2 loaded: %s", _GEOIP_DB_PATH)
        return _geoip_reader
    except ImportError:
        logger.warning(
            "[GEOFENCE] geoip2 package not installed — "
            "falling back to ip-api.com (rate-limited, slower)"
        )
        return None
    except Exception as e:
        logger.error("[GEOFENCE] MaxMind load error: %s — fallback to ip-api", e)
        return None


def _lookup_country_maxmind(ip: str) -> Optional[str]:
    """Resolve IP → country code via local MaxMind DB. Fast (<1ms),
    no rate limit, no external call. Returns None if DB unavailable or
    IP not in database.
    """
    reader = _get_geoip_reader()
    if reader is None:
        return None
    try:
        response = reader.country(ip)
        code = response.country.iso_code
        return str(code).upper() if code else None
    except Exception as e:
        logger.debug("[GEOFENCE] MaxMind lookup failed for %s: %s", ip, e)
        return None


# ══════════════════════════════════════════
# Legacy ip-api.com fallback
# ══════════════════════════════════════════

async def _lookup_country_ipapi(ip: str) -> Optional[str]:
    """Fallback resolver via ip-api.com. Rate-limited (40 req/min cap)."""
    global _api_calls_this_minute, _api_minute_start

    now = time.monotonic()
    if now - _api_minute_start >= 60:
        _api_calls_this_minute = 0
        _api_minute_start = now

    if _api_calls_this_minute >= _API_RATE_LIMIT:
        logger.warning(
            "[GEOFENCE] ip-api rate limit approaching (%d/min), skipping %s",
            _api_calls_this_minute, ip,
        )
        return None

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(
                f"http://ip-api.com/json/{ip}?fields=countryCode"
            )
        _api_calls_this_minute += 1
        if resp.status_code == 200:
            data = resp.json()
            code = data.get("countryCode")
            return str(code).upper() if code else None
        logger.warning("[GEOFENCE] ip-api returned %d for %s", resp.status_code, ip)
        return None
    except Exception as e:
        logger.warning("[GEOFENCE] ip-api unreachable for %s: %s", ip, e)
        return None


# ══════════════════════════════════════════
# Cache
# ══════════════════════════════════════════

def _get_cached_country(ip: str) -> Optional[str]:
    entry = _geo_cache.get(ip)
    if entry is None:
        return None
    country, ts = entry
    if time.monotonic() - ts > _CACHE_TTL:
        _geo_cache.pop(ip, None)
        return None
    return country


def _cache_country(ip: str, country: str) -> None:
    _geo_cache[ip] = (country, time.monotonic())
    # Cap cache at 10k entries (LRU-ish: just drop oldest if too big)
    if len(_geo_cache) > 10_000:
        oldest = sorted(_geo_cache.items(), key=lambda kv: kv[1][1])[:1000]
        for ip_old, _ in oldest:
            _geo_cache.pop(ip_old, None)


async def _resolve_country(ip: str) -> Optional[str]:
    """Unified IP → country resolver. Tries cache, then MaxMind, then
    ip-api.com fallback. Caches the positive result for 24h."""
    cached = _get_cached_country(ip)
    if cached is not None:
        return cached

    country = _lookup_country_maxmind(ip)
    if country is None:
        country = await _lookup_country_ipapi(ip)

    if country is not None:
        _cache_country(ip, country)
    return country


# ══════════════════════════════════════════
# Private IP handling
# ══════════════════════════════════════════

def _is_private_ip(ip: str) -> bool:
    """True for localhost, RFC1918 private, IPv6 loopback."""
    if ip in _PRIVATE_EXACT:
        return True
    for prefix in _PRIVATE_PREFIXES:
        if ip.startswith(prefix):
            return True
    return False


# ══════════════════════════════════════════
# Response builders
# ══════════════════════════════════════════

def _build_451_response(country: str, tier: str, path: str, reason: str) -> JSONResponse:
    """Build the HTTP 451 JSON response for a blocked request.

    Accept-aware: if the client requests HTML, the middleware returns
    the ``blocked.html`` page instead (handled by the middleware,
    not this helper).
    """
    entry = get_country_entry(country)
    country_name = entry.name if entry else country
    regulator = getattr(entry, "regulator", "") if entry else ""

    payload: dict[str, Any] = {
        "error": "jurisdiction_not_supported",
        "code": "GEO_RESTRICTED",
        "detected_country": country,
        "country_name": country_name,
        "tier": tier,
        "reason": reason or f"{country_name} is not currently supported.",
        "path": path,
        "message": (
            f"MAXIA trading and custody services are not available to "
            f"residents of {country_name}. Documentation, blog, and "
            f"developer tools remain accessible at "
            f"https://maxiaworld.app/docs."
        ),
        "appeal_email": "compliance@maxiaworld.app",
        "docs_url": "https://maxiaworld.app/docs",
        "terms_url": "https://maxiaworld.app/legal",
    }
    if regulator:
        payload["regulator"] = regulator

    return JSONResponse(
        status_code=451,
        content=payload,
        headers={
            "X-Geofence-Tier": tier,
            "X-Geofence-Country": country,
            "X-Geofence-Action": "block_451",
            "Cache-Control": "no-store",
        },
    )


def _build_451_html_response(country: str, tier: str, path: str) -> Response:
    """HTML variant of the 451 response for browser requests.

    Serves ``frontend/blocked.html`` with basic string substitution so
    the user sees a branded page instead of raw JSON.
    """
    from starlette.responses import HTMLResponse

    entry = get_country_entry(country)
    country_name = entry.name if entry else country

    blocked_html_path = (
        Path(__file__).parent.parent.parent / "frontend" / "blocked.html"
    )
    try:
        html = blocked_html_path.read_text(encoding="utf-8")
        html = (
            html
            .replace("{{COUNTRY_CODE}}", country)
            .replace("{{COUNTRY_NAME}}", country_name)
            .replace("{{TIER}}", tier)
            .replace("{{PATH}}", path)
        )
    except Exception as e:
        logger.warning("[GEOFENCE] blocked.html read failed: %s", e)
        html = (
            f"<!doctype html><html><head><title>Not available</title></head>"
            f"<body><h1>MAXIA is not available in {country_name}</h1>"
            f"<p>Contact <a href=\"mailto:compliance@maxiaworld.app\">"
            f"compliance@maxiaworld.app</a> if you believe this is a mistake.</p>"
            f"<p><a href=\"https://maxiaworld.app/docs\">Documentation</a> "
            f"remains accessible.</p></body></html>"
        )

    return HTMLResponse(
        content=html,
        status_code=451,
        headers={
            "X-Geofence-Tier": tier,
            "X-Geofence-Country": country,
            "X-Geofence-Action": "block_451_html",
            "Cache-Control": "no-store",
        },
    )


# ══════════════════════════════════════════
# Middleware entry point
# ══════════════════════════════════════════

async def geo_block_middleware(request: Request, call_next):
    """FastAPI middleware — main entry point.

    Keep the name ``geo_block_middleware`` for backwards compat with
    ``backend/main.py``. New callers can use the alias
    ``geofence_middleware`` below.
    """
    # Kill switch
    if not GEOFENCE_ENABLED:
        return await call_next(request)

    path = request.url.path

    try:
        # 1. Classify the route
        route_class = classify_route(path, request.method)

        # 2. Admin routes: never gated here (auth enforces access)
        if route_class == "admin":
            return await call_next(request)

        # 3. Get real IP
        ip = get_real_ip(request)

        # 4. Bypass for private/localhost
        if _is_private_ip(ip):
            return await call_next(request)

        # 5. Resolve country
        country = await _resolve_country(ip)

        # 6. Decision table
        if country is None:
            # Unknown IP — fail-open on always_open, fail-closed on CASP
            if route_class == "always_open":
                return await call_next(request)
            # CASP without country info = deny (fail-safe)
            _log_decision(ip, "??", "unknown", route_class, path, "block_451_unknown_ip")
            response = _pick_response_format(
                request, "??", "unknown", path,
                reason="Unable to determine your jurisdiction from your IP.",
            )
            return response

        tier = get_tier(country)

        # 7. HARD tier: always block, even always_open
        if tier == "hard":
            _log_decision(ip, country, tier, route_class, path, "block_451_hard")
            return _pick_response_format(request, country, tier, path)

        # 8. ALWAYS_OPEN route: allow (non-HARD)
        if route_class == "always_open":
            _log_decision(ip, country, tier, route_class, path, "pass_open")
            response = await call_next(request)
            _add_headers(response, country, tier, "pass_open")
            return response

        # 9. CASP / CASP_READ: gate by feature
        feature = "trading" if route_class == "casp" else "trading"
        decision = check_feature(country, feature)

        if decision == "allow":
            _log_decision(ip, country, tier, route_class, path, "pass_casp_allowed")
            response = await call_next(request)
            _add_headers(response, country, tier, "pass_casp_allowed")
            return response

        if decision == "allow_with_banner":
            _log_decision(ip, country, tier, route_class, path, "pass_casp_caution")
            response = await call_next(request)
            _add_headers(response, country, tier, "pass_casp_caution")
            response.headers["X-Compliance-Notice"] = "acknowledge_required"
            response.headers["X-Compliance-Country"] = country
            return response

        # deny
        _log_decision(ip, country, tier, route_class, path, "block_451_license")
        return _pick_response_format(request, country, tier, path)

    except Exception as e:
        logger.exception("[GEOFENCE] middleware error on %s: %s", path, e)
        # Fail-open: never break the API because of a geofence bug
        return await call_next(request)


# Alias for new callers
geofence_middleware = geo_block_middleware


# ══════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════

def _pick_response_format(
    request: Request,
    country: str,
    tier: str,
    path: str,
    reason: str = "",
) -> Response:
    """Return a 451 response in JSON or HTML depending on Accept header."""
    accept = (request.headers.get("accept") or "").lower()
    if "text/html" in accept and "application/json" not in accept:
        return _build_451_html_response(country, tier, path)
    entry = get_country_entry(country) if country and country != "??" else None
    return _build_451_response(
        country, tier, path,
        reason or (entry.reason if entry else ""),
    )


def _add_headers(response: Response, country: str, tier: str, action: str) -> None:
    """Attach geofence observability headers to a response."""
    try:
        response.headers["X-Geofence-Tier"] = tier
        response.headers["X-Geofence-Country"] = country
        response.headers["X-Geofence-Action"] = action
    except Exception:
        pass


def _log_decision(
    ip: str,
    country: str,
    tier: str,
    route_class: str,
    path: str,
    action: str,
) -> None:
    """Fire-and-forget write to the audit log. Never blocks the request.

    Schedules the async insert as a background task and returns
    immediately. DB failures are swallowed inside the task so they
    cannot impact request handling. The middleware holds no reference
    to the task — it's an intentional "fire and forget".
    """
    try:
        from compliance.geofence_log import log_decision as _alog
        # Fire-and-forget: schedule in the running event loop
        asyncio.create_task(
            _alog(
                ip=ip,
                country=country,
                tier=tier,
                route_class=route_class,
                path=path,
                action=action,
            )
        )
    except RuntimeError:
        # No running loop (shouldn't happen inside middleware) — fall back
        try:
            if action.startswith("block_"):
                audit_log("geo_block", ip, f"{country}/{tier}/{path}")
        except Exception:
            pass
    except Exception as e:
        logger.debug("[GEOFENCE] audit log schedule failed: %s", e)
