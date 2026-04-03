"""MAXIA Art.25 V12 — Scoped API Keys with Rate Limits & Audit Logging

Key format: maxia_{scope_prefix}_{random_hex}
Scopes: read, trade, admin
Tiers: free (100/day), pro (10000/day), enterprise (unlimited)
Stores SHA-256 hash only — plaintext never persisted.
"""
import logging
import hashlib, json, secrets, time, uuid
from typing import Optional
from fastapi import HTTPException, Header, Depends

logger = logging.getLogger(__name__)

# ── Constants ──

VALID_SCOPES = {"read", "trade", "admin"}

SCOPE_PREFIXES = {
    frozenset(["read"]): "ro",
    frozenset(["read", "trade"]): "rw",
    frozenset(["read", "trade", "admin"]): "adm",
}

TIER_LIMITS = {
    "free": 100,
    "pro": 10000,
    "enterprise": 0,  # 0 = unlimited
}

API_TIERS = {
    "free": {"daily_limit": 100, "rate_per_min": 10, "price_monthly": 0},
    "pro": {"daily_limit": 10000, "rate_per_min": 100, "price_monthly": 9.99},
    "enterprise": {"daily_limit": 100000, "rate_per_min": 1000, "price_monthly": 299, "sla": "99.9%", "support": "dedicated", "fleet": True, "compliance": True, "whitelabel": True},
}


def check_api_tier_limit(api_key_data: dict) -> bool:
    """Check if API key has exceeded its tier limit."""
    tier = api_key_data.get("tier", "free")
    limits = API_TIERS.get(tier, API_TIERS["free"])
    daily_count = api_key_data.get("daily_count", 0)
    return daily_count < limits["daily_limit"]

# ── SQL Schema ──

API_KEYS_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_keys_v2 (
    key_id TEXT PRIMARY KEY,
    api_key_hash TEXT NOT NULL UNIQUE,
    agent_wallet TEXT NOT NULL,
    name TEXT NOT NULL,
    scopes TEXT NOT NULL DEFAULT '["read","trade"]',
    tier TEXT NOT NULL DEFAULT 'free',
    rate_limit_day INTEGER NOT NULL DEFAULT 100,
    created_at INTEGER DEFAULT (strftime('%s','now')),
    last_used_at INTEGER,
    revoked_at INTEGER
);

CREATE INDEX IF NOT EXISTS idx_api_keys_v2_hash ON api_keys_v2(api_key_hash);
CREATE INDEX IF NOT EXISTS idx_api_keys_v2_wallet ON api_keys_v2(agent_wallet);

CREATE TABLE IF NOT EXISTS api_audit_log (
    id TEXT PRIMARY KEY,
    api_key_id TEXT NOT NULL,
    action TEXT NOT NULL,
    endpoint TEXT DEFAULT '',
    ip TEXT DEFAULT '',
    status_code INTEGER DEFAULT 200,
    timestamp INTEGER DEFAULT (strftime('%s','now'))
);

CREATE INDEX IF NOT EXISTS idx_audit_key ON api_audit_log(api_key_id, timestamp);
"""

# ── In-memory rate-limit counters (day-scoped) ──

_rate_counters: dict = {}  # key_id:YYYY-MM-DD -> count


def _rate_key(key_id: str) -> str:
    return f"{key_id}:{time.strftime('%Y-%m-%d')}"


# ── Helpers ──

def _hash_key(raw_key: str) -> str:
    """SHA-256 hash of a raw API key."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


def _scope_prefix(scopes: list[str]) -> str:
    fs = frozenset(scopes)
    return SCOPE_PREFIXES.get(fs, "rw")


def _generate_key(scopes: list[str]) -> str:
    """Generate a key in format maxia_{prefix}_{hex}."""
    prefix = _scope_prefix(scopes)
    random_part = secrets.token_hex(24)
    return f"maxia_{prefix}_{random_part}"


# ── Table bootstrap ──

async def ensure_tables(db):
    """Create api_keys_v2 and api_audit_log tables if they don't exist."""
    await db.raw_executescript(API_KEYS_SCHEMA)
    logger.info("Tables ensured")


# ── Core functions ──

async def create_key(
    db,
    wallet: str,
    name: str,
    scopes: Optional[list[str]] = None,
    tier: str = "free",
) -> dict:
    """Generate a new scoped API key. Returns the full key (only time it is shown)."""
    if scopes is None:
        scopes = ["read", "trade"]

    # Validate scopes
    invalid = set(scopes) - VALID_SCOPES
    if invalid:
        raise HTTPException(400, f"Invalid scopes: {invalid}")

    if tier not in TIER_LIMITS:
        raise HTTPException(400, f"Invalid tier: {tier}. Must be one of {list(TIER_LIMITS.keys())}")

    raw_key = _generate_key(scopes)
    key_hash = _hash_key(raw_key)
    key_id = str(uuid.uuid4())
    rate_limit = TIER_LIMITS[tier]

    await db.raw_execute(
        "INSERT INTO api_keys_v2(key_id, api_key_hash, agent_wallet, name, scopes, tier, rate_limit_day) "
        "VALUES(?,?,?,?,?,?,?)",
        (key_id, key_hash, wallet, name, json.dumps(sorted(scopes)), tier, rate_limit),
    )

    return {
        "key_id": key_id,
        "api_key": raw_key,  # Only time the plaintext is returned
        "wallet": wallet,
        "name": name,
        "scopes": sorted(scopes),
        "tier": tier,
        "rate_limit_day": rate_limit,
    }


async def validate_key(
    db,
    raw_key: str,
    required_scope: Optional[str] = None,
) -> dict:
    """Validate a raw API key. Returns key info dict or raises HTTPException.

    Also handles backward compatibility: if the key isn't found in api_keys_v2
    but exists in the legacy 'agents' table, it gets default scopes.
    """
    key_hash = _hash_key(raw_key)

    # Look up in api_keys_v2
    rows = await db.raw_execute_fetchall(
        "SELECT key_id, api_key_hash, agent_wallet, name, scopes, tier, "
        "rate_limit_day, created_at, last_used_at, revoked_at "
        "FROM api_keys_v2 WHERE api_key_hash=?", (key_hash,)
    )
    row = rows[0] if rows else None

    if row:
        info = dict(row)
        # Check revocation
        if info.get("revoked_at"):
            raise HTTPException(401, "API key has been revoked")

        scopes = json.loads(info["scopes"]) if isinstance(info["scopes"], str) else info["scopes"]

        # Check scope
        if required_scope and required_scope not in scopes:
            raise HTTPException(403, f"API key missing required scope: {required_scope}")

        # Check rate limit (0 = unlimited)
        rate_limit = info.get("rate_limit_day", 100)
        if rate_limit > 0:
            rk = _rate_key(info["key_id"])
            count = _rate_counters.get(rk, 0)
            if count >= rate_limit:
                raise HTTPException(429, f"Rate limit exceeded ({rate_limit}/day)")
            _rate_counters[rk] = count + 1

        # Update last_used_at
        now = int(time.time())
        await db.raw_execute(
            "UPDATE api_keys_v2 SET last_used_at=? WHERE key_id=?", (now, info["key_id"])
        )

        return {
            "key_id": info["key_id"],
            "wallet": info["agent_wallet"],
            "name": info["name"],
            "scopes": scopes,
            "tier": info["tier"],
            "rate_limit_day": rate_limit,
        }

    # ── Backward compatibility: check legacy agents table ──
    try:
        legacy_rows = await db.raw_execute_fetchall(
            "SELECT api_key, wallet, name FROM agents WHERE api_key=?", (raw_key,)
        )
        legacy = legacy_rows[0] if legacy_rows else None
    except Exception:
        legacy = None

    if legacy:
        legacy = dict(legacy)
        default_scopes = ["read", "trade"]
        if required_scope and required_scope not in default_scopes:
            raise HTTPException(403, f"Legacy key missing required scope: {required_scope}")
        return {
            "key_id": f"legacy_{legacy['api_key'][:12]}",
            "wallet": legacy["wallet"],
            "name": legacy["name"],
            "scopes": default_scopes,
            "tier": "free",
            "rate_limit_day": TIER_LIMITS["free"],
        }

    raise HTTPException(401, "Invalid API key")


def require_scope(scope: str):
    """Return a FastAPI Depends() that validates X-API-Key header has the required scope.

    Usage:
        @router.get("/admin/stats", dependencies=[Depends(require_scope("admin"))])
    """
    async def _dependency(x_api_key: str = Header(..., alias="X-API-Key")):
        from core.database import db
        return await validate_key(db, x_api_key, required_scope=scope)
    return _dependency


async def log_audit(
    db,
    key_id: str,
    action: str,
    endpoint: str = "",
    ip: str = "",
    status_code: int = 200,
):
    """Write an entry to the audit log."""
    entry_id = str(uuid.uuid4())
    try:
        await db.raw_execute(
            "INSERT INTO api_audit_log(id, api_key_id, action, endpoint, ip, status_code) "
            "VALUES(?,?,?,?,?,?)",
            (entry_id, key_id, action, endpoint, ip, status_code),
        )
    except Exception as e:
        logger.error(f"Audit log error: {e}")


async def revoke_key(db, key_id: str, wallet: str) -> dict:
    """Revoke a key. Only the owner (by wallet) can revoke."""
    rows = await db.raw_execute_fetchall(
        "SELECT key_id, api_key_hash, agent_wallet, name, scopes, tier, "
        "rate_limit_day, created_at, last_used_at, revoked_at "
        "FROM api_keys_v2 WHERE key_id=?", (key_id,)
    )
    row = rows[0] if rows else None

    if not row:
        raise HTTPException(404, "Key not found")

    info = dict(row)
    if info["agent_wallet"] != wallet:
        raise HTTPException(403, "Only the key owner can revoke")
    if info.get("revoked_at"):
        raise HTTPException(400, "Key already revoked")

    now = int(time.time())
    await db.raw_execute(
        "UPDATE api_keys_v2 SET revoked_at=? WHERE key_id=?", (now, key_id)
    )

    await log_audit(db, key_id, "revoke", ip="", status_code=200)
    return {"key_id": key_id, "revoked_at": now}


async def list_keys(db, wallet: str) -> list[dict]:
    """List all keys for a wallet. Key hashes are masked — only last 8 chars shown."""
    rows = await db.raw_execute_fetchall(
        "SELECT key_id, api_key_hash, agent_wallet, name, scopes, tier, "
        "rate_limit_day, created_at, last_used_at, revoked_at "
        "FROM api_keys_v2 WHERE agent_wallet=? ORDER BY created_at DESC",
        (wallet,),
    )
    result = []
    for r in rows:
        r = dict(r)
        result.append({
            "key_id": r["key_id"],
            "name": r["name"],
            "scopes": json.loads(r["scopes"]) if isinstance(r["scopes"], str) else r["scopes"],
            "tier": r["tier"],
            "rate_limit_day": r["rate_limit_day"],
            "key_hint": f"...{r['api_key_hash'][-8:]}",
            "created_at": r["created_at"],
            "last_used_at": r.get("last_used_at"),
            "revoked": r.get("revoked_at") is not None,
        })
    return result


async def get_audit_log(db, wallet: str, limit: int = 50) -> list[dict]:
    """Return recent audit entries for all keys belonging to a wallet."""
    rows = await db.raw_execute_fetchall(
        "SELECT a.* FROM api_audit_log a "
        "JOIN api_keys_v2 k ON a.api_key_id = k.key_id "
        "WHERE k.agent_wallet=? "
        "ORDER BY a.timestamp DESC LIMIT ?",
        (wallet, limit),
    )
    return [dict(r) for r in rows]
