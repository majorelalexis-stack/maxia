"""Public API — Shared state and helper functions.

Extracted from public_api.py (S34 split).
"""
import logging
import uuid, time, hashlib, secrets, asyncio, json, datetime, re

logger = logging.getLogger(__name__)
from error_utils import safe_error
from fastapi import APIRouter, HTTPException, Header, Request
from config import (
    TREASURY_ADDRESS, GROQ_API_KEY, GROQ_MODEL,
    get_commission_bps, get_commission_tier_name, BLOCKED_WORDS, BLOCKED_PATTERNS,
)
from security import check_content_safety, check_ofac_wallet, require_ofac_clear, check_rate_limit_tiered, check_rate_limit

# Fix #13: Solana address validation helper
_SOLANA_ADDR_RE = re.compile(r'^[1-9A-HJ-NP-Za-km-z]{32,44}$')

def _validate_solana_address(addr: str, field: str = "wallet"):
    if not addr or not _SOLANA_ADDR_RE.match(addr):
        raise HTTPException(400, f"Invalid Solana address in {field}")


def _safe_float(val, field: str = "value", default: float = 0.0) -> float:
    """Safe float conversion — rejects NaN, Infinity, None."""
    import math
    try:
        f = float(val) if val is not None else default
    except (TypeError, ValueError):
        raise HTTPException(400, f"{field} must be a number")
    if math.isnan(f) or math.isinf(f):
        raise HTTPException(400, f"{field} must be a finite number")
    return f

# ── Sandbox mode (test without real USDC) ──
import os
SANDBOX_MODE = os.getenv("SANDBOX_MODE", "false").lower() == "true"
SANDBOX_PREFIX = "sandbox_"

# ── Stockage en memoire (en prod: base de donnees) ──
_registered_agents: dict = {}   # api_key -> agent info
_agent_services: list = []      # services listes par des IA externes
_transactions: list = []        # historique des transactions
_db_loaded: bool = False

# ── Clone detection: track content hashes to detect duplicate services ──
_service_content_hashes: dict = {}  # content_hash -> {"original_id": str, "original_agent": str, "listed_at": int}


def _compute_service_hash(name: str, description: str, endpoint: str) -> str:
    """Hash the core content of a service to detect clones."""
    # Normalize: lowercase, strip whitespace, remove punctuation
    normalized = f"{name.lower().strip()}|{description.lower().strip()[:500]}|{endpoint.lower().strip()}"
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def _check_clone(name: str, description: str, endpoint: str, agent_api_key: str) -> dict:
    """Check if a service is a clone of an existing one. Returns clone info or None."""
    content_hash = _compute_service_hash(name, description, endpoint)
    if content_hash in _service_content_hashes:
        original = _service_content_hashes[content_hash]
        # Same agent re-listing = allowed (update)
        if original.get("original_agent_key") == agent_api_key:
            return {}
        return {
            "is_clone": True,
            "original_service_id": original["original_id"],
            "original_agent": original["original_agent"],
            "similarity": "exact_match",
        }
    return {}


def _register_service_hash(service_id: str, agent_name: str, agent_key: str,
                           name: str, description: str, endpoint: str):
    """Register a service content hash for clone detection."""
    content_hash = _compute_service_hash(name, description, endpoint)
    if content_hash not in _service_content_hashes:
        _service_content_hashes[content_hash] = {
            "original_id": service_id,
            "original_agent": agent_name,
            "original_agent_key": agent_key,
            "listed_at": int(time.time()),
        }


def _is_original_creator(service_id: str) -> bool:
    """Check if a service is the original (not a clone)."""
    for info in _service_content_hashes.values():
        if info["original_id"] == service_id:
            return True
    return False


_db_last_sync: float = 0
_DB_SYNC_INTERVAL = 300  # re-sync from DB every 5 min


async def _load_from_db():
    """Load agents and services from DB on first access, then periodically re-sync."""
    global _db_loaded, _db_last_sync
    import time as _time
    now = _time.time()
    if _db_loaded and now - _db_last_sync < _DB_SYNC_INTERVAL:
        return
    _db_loaded = True
    _db_last_sync = now
    try:
        from database import db
        agents = await asyncio.wait_for(db.get_all_agents(), timeout=10)
        for a in agents:
            key = a["api_key"]
            existing = _registered_agents.get(key)
            if existing:
                # Merge: garder les stats RAM (requests_today, etc) mais mettre a jour depuis DB
                existing.update({
                    "name": a["name"], "wallet": a["wallet"],
                    "description": a.get("description", ""),
                    "tier": a.get("tier", existing.get("tier", "BRONZE")),
                    "volume_30d": max(a.get("volume_30d", 0), existing.get("volume_30d", 0)),
                    "total_spent": max(a.get("total_spent", 0), existing.get("total_spent", 0)),
                    "total_earned": max(a.get("total_earned", 0), existing.get("total_earned", 0)),
                })
            else:
                _registered_agents[key] = {
                    "api_key": key, "name": a["name"], "wallet": a["wallet"],
                    "description": a.get("description", ""), "tier": a.get("tier", "BRONZE"),
                    "volume_30d": a.get("volume_30d", 0), "total_spent": a.get("total_spent", 0),
                    "total_earned": a.get("total_earned", 0), "services_listed": a.get("services_listed", 0),
                    "requests_today": 0, "registered_at": a.get("created_at", 0),
                }
        # Services: merge par ID (pas d'append duplicatif)
        existing_ids = {s["id"] for s in _agent_services}
        services = await asyncio.wait_for(db.get_services(), timeout=10)
        for s in services:
            sd = dict(s)
            if sd["id"] not in existing_ids:
                _agent_services.append(sd)
                existing_ids.add(sd["id"])
        if agents or services:
            logger.info("Loaded from DB: %s agents, %s services", len(agents), len(services))
    except Exception as e:
        logger.error("DB load error: %s", e)

# ── Groq client ──
groq_client = None
if GROQ_API_KEY:
    try:
        from groq import Groq
        groq_client = Groq(api_key=GROQ_API_KEY)
    except Exception:
        pass

# Rate limit par API key
_rate_limits: dict = {}
RATE_LIMIT_FREE = 100  # requetes/jour

# Fix #3: Lock for agent stat updates to prevent race conditions
_agent_update_lock = asyncio.Lock()

# Fix #11: Brute force API key protection
_failed_lookups: dict = {}  # ip -> count


# ══════════════════════════════════════════
#  SECURITE ART.1 — Filtrage anti-abus
# ══════════════════════════════════════════

def _check_safety(text: str, field: str = "content"):
    """Filtrage anti-pedopornographie et contenu illegal sur TOUT."""
    check_content_safety(text, field)


RATE_LIMITS_BY_TIER = {
    "BRONZE": 100,    # free tier
    "GOLD": 1000,      # $500+ volume
    "WHALE": 10000,    # $5000+ volume
}


def _check_rate(api_key: str):
    """Rate limit par API key — quota basé sur le tier de l'agent."""
    # Fix #5: Use UTC explicitly for consistent rate limiting across timezones
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    key = f"{api_key}:{today}"
    _rate_limits.setdefault(key, 0)
    _rate_limits[key] += 1

    # Cleanup stale date keys to avoid memory leak
    stale = [k for k in _rate_limits if ":" in k and not k.endswith(f":{today}")]
    for k in stale:
        _rate_limits.pop(k, None)

    # Fix #9: Cap total keys to prevent unbounded memory growth
    if len(_rate_limits) > 10000:
        today_keys = {k: v for k, v in _rate_limits.items() if k.endswith(f":{today}")}
        _rate_limits.clear()
        _rate_limits.update(today_keys)

    # Determine tier-based limit
    agent = _registered_agents.get(api_key, {})
    tier = agent.get("tier", "BRONZE")
    limit = RATE_LIMITS_BY_TIER.get(tier, RATE_LIMIT_FREE)

    if _rate_limits[key] > limit:
        raise HTTPException(429, f"Limite quotidienne atteinte ({limit} req/jour, tier {tier}). Augmentez votre volume pour un tier superieur.")


def _get_agent(api_key: str, client_ip: str = "") -> dict:
    """Recupere l'agent depuis sa cle API.
    Fix #11: Track failed lookups and block IPs with too many attempts.
    """
    # Fix #20: Cleanup _failed_lookups to prevent unbounded memory growth
    if len(_failed_lookups) > 10000:
        _failed_lookups.clear()

    # Fix #11: Block IPs with excessive failed lookups
    if client_ip and _failed_lookups.get(client_ip, 0) > 100:
        raise HTTPException(429, "Too many invalid API key attempts")

    agent = _registered_agents.get(api_key)
    if not agent:
        # Fix #11: Track failed lookup by IP
        if client_ip:
            _failed_lookups[client_ip] = _failed_lookups.get(client_ip, 0) + 1
        raise HTTPException(401, "API key invalide. Inscrivez-vous sur /api/public/register")

    # Fix #11: Reset failed count on success
    if client_ip:
        _failed_lookups.pop(client_ip, None)
    return agent
