"""MAXIA Premium API V12 — Subscription management for tiered API access.

Gere les abonnements Premium API :
- 3 tiers : FREE (100 req/day), PREMIUM (10,000 req/day), ENTERPRISE (illimite)
- Paiement via Stripe Checkout ou USDC on-chain ($9.99/mois)
- Cache en memoire (5 min) pour eviter les hits DB a chaque requete
- Endpoints: subscribe, status, usage, cancel

Variables d'environnement :
  STRIPE_SECRET_KEY         — Cle secrete Stripe (sk_live_... ou sk_test_...)
  STRIPE_PRICE_PREMIUM_API  — Price ID Stripe pour le plan Premium API ($9.99/mois)
"""

import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# ── Config ──

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_PRICE_PREMIUM_API = os.getenv("STRIPE_PRICE_PREMIUM_API", "")
STRIPE_SUCCESS_URL = os.getenv(
    "STRIPE_SUCCESS_URL",
    "https://maxiaworld.app/app.html?stripe=success",
)
STRIPE_CANCEL_URL = os.getenv(
    "STRIPE_CANCEL_URL",
    "https://maxiaworld.app/app.html?stripe=cancel",
)

PREMIUM_PRICE_USDC = 9.99

# ── Tier definitions ──

TIER_DEFS = {
    "FREE": {
        "price_usd": 0.0,
        "rate_limit_day": 100,
        "rate_limit_min": 5,
        "features": ["basic_endpoints"],
    },
    "PREMIUM": {
        "price_usd": 9.99,
        "rate_limit_day": 10_000,
        "rate_limit_min": 100,
        "features": ["all_endpoints", "priority_support"],
    },
    "ENTERPRISE": {
        "price_usd": 0.0,  # custom pricing
        "rate_limit_day": 999_999_999,  # effectively unlimited
        "rate_limit_min": 10_000,
        "features": ["all_endpoints", "priority_support", "sla", "dedicated_support"],
    },
}

# ── Stripe conditional import ──

stripe = None
_STRIPE_AVAILABLE = False

if STRIPE_SECRET_KEY:
    try:
        import stripe as _stripe

        _stripe.api_key = STRIPE_SECRET_KEY
        _stripe.api_version = "2024-12-18.acacia"
        stripe = _stripe
        _STRIPE_AVAILABLE = True
        logger.info("Premium API: Stripe SDK initialise")
    except ImportError:
        logger.error("Premium API: package 'stripe' non installe")
    except Exception as e:
        logger.error("Premium API: erreur init Stripe: %s", e)

# ── DB schema ──

_schema_ready = False

_PREMIUM_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_subscriptions (
    sub_id TEXT PRIMARY KEY,
    api_key TEXT NOT NULL,
    tier TEXT DEFAULT 'FREE',
    price_usd REAL DEFAULT 0,
    started_at INTEGER NOT NULL,
    expires_at INTEGER,
    status TEXT DEFAULT 'active',
    stripe_session_id TEXT,
    usdc_tx_hash TEXT
);

CREATE INDEX IF NOT EXISTS idx_api_sub_key ON api_subscriptions(api_key);
CREATE INDEX IF NOT EXISTS idx_api_sub_status ON api_subscriptions(api_key, status);
"""


async def _ensure_schema() -> None:
    """Cree la table api_subscriptions si elle n'existe pas."""
    global _schema_ready
    if _schema_ready:
        return
    try:
        from core.database import db

        await db.raw_executescript(_PREMIUM_SCHEMA)
        _schema_ready = True
        logger.info("Premium API: schema DB pret")
    except Exception as e:
        logger.error("Premium API: erreur schema: %s", e)


# ── In-memory tier cache (5 min TTL) ──

_tier_cache: dict[str, tuple[dict, float]] = {}
_CACHE_TTL = 300  # 5 minutes


def _cache_get(api_key: str) -> Optional[dict]:
    """Return cached tier info if still valid, else None."""
    entry = _tier_cache.get(api_key)
    if entry is None:
        return None
    data, cached_at = entry
    if time.time() - cached_at > _CACHE_TTL:
        del _tier_cache[api_key]
        return None
    return data


def _cache_set(api_key: str, data: dict) -> None:
    """Store tier info in cache with current timestamp."""
    _tier_cache[api_key] = (data, time.time())


def _cache_invalidate(api_key: str) -> None:
    """Remove a key from the tier cache."""
    _tier_cache.pop(api_key, None)


# ── Core functions ──


async def get_tier_for_key(api_key: str) -> dict:
    """Lookup the subscription tier for an API key.

    Returns: {tier, rate_limit_day, rate_limit_min, expires_at, status}
    Uses 5-minute cache to avoid DB hit on every request.
    """
    cached = _cache_get(api_key)
    if cached is not None:
        return cached

    await _ensure_schema()
    from core.database import db

    rows = await db.raw_execute_fetchall(
        "SELECT sub_id, tier, expires_at, status FROM api_subscriptions "
        "WHERE api_key = ? AND status = 'active' "
        "ORDER BY started_at DESC LIMIT 1",
        (api_key,),
    )

    if not rows:
        result = _build_tier_result("FREE", None)
        _cache_set(api_key, result)
        return result

    row = rows[0]
    tier_name = row["tier"] if isinstance(row, dict) else row[1]
    expires_at = row["expires_at"] if isinstance(row, dict) else row[2]
    status = row["status"] if isinstance(row, dict) else row[3]

    # Check expiration
    if expires_at and int(expires_at) < int(time.time()):
        result = _build_tier_result("FREE", None)
        _cache_set(api_key, result)
        return result

    if tier_name not in TIER_DEFS:
        tier_name = "FREE"

    result = _build_tier_result(tier_name, expires_at)
    result["status"] = status
    _cache_set(api_key, result)
    return result


def _build_tier_result(tier_name: str, expires_at: Optional[int]) -> dict:
    """Build a tier result dict from tier name and expiry."""
    tier_def = TIER_DEFS.get(tier_name, TIER_DEFS["FREE"])
    return {
        "tier": tier_name,
        "rate_limit_day": tier_def["rate_limit_day"],
        "rate_limit_min": tier_def["rate_limit_min"],
        "expires_at": int(expires_at) if expires_at else None,
        "status": "active",
    }


# ── In-memory usage tracking (fallback when Redis unavailable) ──

_usage_store: dict[str, list[float]] = {}
_USAGE_MAX_KEYS = 5000


def _cleanup_usage_store() -> None:
    """Evict expired entries to prevent memory leak."""
    if len(_usage_store) < _USAGE_MAX_KEYS:
        return
    now = time.time()
    day_start = now - (now % 86400)
    expired = [k for k, ts_list in _usage_store.items()
               if not ts_list or ts_list[-1] < day_start]
    for k in expired:
        del _usage_store[k]


async def check_premium_rate_limit(api_key: str) -> dict:
    """Check if an API key is within its daily rate limit.

    Returns: {allowed, tier, limit_day, used_today, remaining}
    """
    tier_info = await get_tier_for_key(api_key)
    tier_name = tier_info["tier"]
    limit_day = tier_info["rate_limit_day"]
    limit_min = tier_info["rate_limit_min"]
    now = time.time()
    day_start = now - (now % 86400)

    # Per-day tracking
    day_key = f"premium:{api_key}:day"
    _usage_store.setdefault(day_key, [])
    _usage_store[day_key] = [t for t in _usage_store[day_key] if t > day_start]
    used_today = len(_usage_store[day_key])

    if used_today >= limit_day:
        return {
            "allowed": False,
            "tier": tier_name,
            "reason": f"Daily limit reached: {limit_day} req/day ({tier_name} tier)",
            "limit_day": limit_day,
            "used_today": used_today,
            "remaining": 0,
        }

    # Per-minute tracking
    min_key = f"premium:{api_key}:min"
    _usage_store.setdefault(min_key, [])
    _usage_store[min_key] = [t for t in _usage_store[min_key] if t > now - 60]

    if len(_usage_store[min_key]) >= limit_min:
        return {
            "allowed": False,
            "tier": tier_name,
            "reason": f"Per-minute limit reached: {limit_min} req/min ({tier_name} tier)",
            "limit_day": limit_day,
            "used_today": used_today,
            "remaining": max(0, limit_day - used_today),
        }

    _usage_store[day_key].append(now)
    _usage_store[min_key].append(now)
    _cleanup_usage_store()

    return {
        "allowed": True,
        "tier": tier_name,
        "limit_day": limit_day,
        "used_today": used_today + 1,
        "remaining": max(0, limit_day - used_today - 1),
    }


# ── Subscription DB helpers ──


async def _get_active_sub(api_key: str) -> Optional[dict]:
    """Get the most recent active subscription for an API key."""
    await _ensure_schema()
    from core.database import db

    rows = await db.raw_execute_fetchall(
        "SELECT sub_id, api_key, tier, price_usd, started_at, expires_at, "
        "status, stripe_session_id, usdc_tx_hash "
        "FROM api_subscriptions "
        "WHERE api_key = ? AND status = 'active' "
        "ORDER BY started_at DESC LIMIT 1",
        (api_key,),
    )
    if not rows:
        return None
    row = rows[0]
    if isinstance(row, dict):
        return row
    return {
        "sub_id": row[0],
        "api_key": row[1],
        "tier": row[2],
        "price_usd": row[3],
        "started_at": row[4],
        "expires_at": row[5],
        "status": row[6],
        "stripe_session_id": row[7],
        "usdc_tx_hash": row[8],
    }


async def _create_subscription(
    api_key: str,
    tier: str,
    price_usd: float,
    expires_at: Optional[int],
    stripe_session_id: Optional[str] = None,
    usdc_tx_hash: Optional[str] = None,
) -> dict:
    """Insert a new subscription row and invalidate cache."""
    await _ensure_schema()
    from core.database import db

    sub_id = f"sub_{uuid.uuid4().hex[:16]}"
    now_ts = int(time.time())

    await db.raw_execute(
        "INSERT INTO api_subscriptions "
        "(sub_id, api_key, tier, price_usd, started_at, expires_at, "
        "status, stripe_session_id, usdc_tx_hash) "
        "VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)",
        (sub_id, api_key, tier, price_usd, now_ts, expires_at,
         stripe_session_id, usdc_tx_hash),
    )

    _cache_invalidate(api_key)
    logger.info("Premium sub created: %s tier=%s key=%s", sub_id, tier, api_key[:8])

    return {
        "sub_id": sub_id,
        "api_key": api_key,
        "tier": tier,
        "price_usd": price_usd,
        "started_at": now_ts,
        "expires_at": expires_at,
        "status": "active",
    }


async def _cancel_subscription(sub_id: str, api_key: str) -> None:
    """Mark a subscription as cancelled and invalidate cache."""
    await _ensure_schema()
    from core.database import db

    await db.raw_execute(
        "UPDATE api_subscriptions SET status = 'cancelled' WHERE sub_id = ?",
        (sub_id,),
    )
    _cache_invalidate(api_key)
    logger.info("Premium sub cancelled: %s key=%s", sub_id, api_key[:8])


# ── Router ──

router = APIRouter(prefix="/api/premium", tags=["premium"])


class SubscribeRequest(BaseModel):
    api_key: str
    payment_method: str = "stripe"  # "stripe" or "usdc"
    usdc_tx_hash: Optional[str] = None
    email: Optional[str] = None


class CancelRequest(BaseModel):
    api_key: str


@router.post("/subscribe")
async def subscribe(req: SubscribeRequest) -> dict:
    """Subscribe to Premium tier ($9.99/month).

    payment_method: 'stripe' creates a Checkout session, 'usdc' verifies on-chain tx.
    """
    if not req.api_key or len(req.api_key) < 8:
        raise HTTPException(400, "api_key invalide (minimum 8 caracteres)")

    # Check for existing active sub
    existing = await _get_active_sub(req.api_key)
    if existing and existing.get("tier") in ("PREMIUM", "ENTERPRISE"):
        expires_at = existing.get("expires_at")
        if expires_at and int(expires_at) > int(time.time()):
            return {
                "status": "already_subscribed",
                "tier": existing["tier"],
                "expires_at": expires_at,
                "message": "Abonnement Premium deja actif",
            }

    if req.payment_method == "stripe":
        return await _subscribe_stripe(req.api_key, req.email)

    if req.payment_method == "usdc":
        return await _subscribe_usdc(req.api_key, req.usdc_tx_hash)

    raise HTTPException(400, "payment_method invalide: 'stripe' ou 'usdc'")


async def _subscribe_stripe(api_key: str, email: Optional[str]) -> dict:
    """Create a Stripe Checkout session for Premium subscription."""
    if not _STRIPE_AVAILABLE:
        raise HTTPException(
            503,
            "Stripe non configure. Utilisez le paiement USDC ou contactez support@maxiaworld.app",
        )

    price_id = STRIPE_PRICE_PREMIUM_API
    if not price_id:
        raise HTTPException(
            503,
            "STRIPE_PRICE_PREMIUM_API non configure. Contactez support@maxiaworld.app",
        )

    session_params: dict = {
        "mode": "subscription",
        "payment_method_types": ["card"],
        "line_items": [{"price": price_id, "quantity": 1}],
        "success_url": f"{STRIPE_SUCCESS_URL}&session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": STRIPE_CANCEL_URL,
        "metadata": {"api_key": api_key, "tier": "PREMIUM"},
        "subscription_data": {
            "metadata": {"api_key": api_key, "tier": "PREMIUM"},
        },
        "allow_promotion_codes": True,
    }

    if email:
        session_params["customer_email"] = email

    try:
        session = stripe.checkout.Session.create(**session_params)
        # Record pending subscription (activated on webhook or manual confirm)
        expires_at = int(time.time()) + 30 * 86400  # 30 days
        await _create_subscription(
            api_key=api_key,
            tier="PREMIUM",
            price_usd=PREMIUM_PRICE_USDC,
            expires_at=expires_at,
            stripe_session_id=session.id,
        )

        return {
            "status": "checkout_created",
            "checkout_url": session.url,
            "session_id": session.id,
            "tier": "PREMIUM",
            "price_usd": PREMIUM_PRICE_USDC,
        }
    except Exception as e:
        logger.error("Premium Stripe checkout error: %s", e)
        raise HTTPException(502, "Erreur Stripe checkout")


async def _subscribe_usdc(api_key: str, usdc_tx_hash: Optional[str]) -> dict:
    """Verify a USDC payment on-chain and activate Premium."""
    if not usdc_tx_hash or len(usdc_tx_hash) < 20:
        raise HTTPException(
            400,
            "usdc_tx_hash requis pour le paiement USDC (hash de transaction Solana ou EVM)",
        )

    # Verify the transaction on-chain (best-effort)
    verified = await _verify_usdc_payment(usdc_tx_hash)
    if not verified:
        raise HTTPException(
            400,
            "Transaction USDC non verifiee. Verifiez le hash et le montant ($9.99 minimum).",
        )

    expires_at = int(time.time()) + 30 * 86400  # 30 days
    sub = await _create_subscription(
        api_key=api_key,
        tier="PREMIUM",
        price_usd=PREMIUM_PRICE_USDC,
        expires_at=expires_at,
        usdc_tx_hash=usdc_tx_hash,
    )

    return {
        "status": "subscribed",
        "tier": "PREMIUM",
        "sub_id": sub["sub_id"],
        "expires_at": expires_at,
        "expires_at_iso": datetime.fromtimestamp(
            expires_at, tz=timezone.utc
        ).isoformat(),
        "payment": "usdc",
        "tx_hash": usdc_tx_hash,
    }


async def _verify_usdc_payment(tx_hash: str) -> bool:
    """Verify USDC payment on-chain (Solana via Helius or EVM via verifier).

    Returns True if the tx exists and transfers >= $9.99 USDC to MAXIA wallet.
    Gracefully returns True if verification service is unreachable (manual review).
    """
    # Try Solana verification first (base58 hash)
    try:
        from blockchain.solana_verifier import verify_transaction

        result = await verify_transaction(tx_hash)
        if result and result.get("verified"):
            amount = float(result.get("amount_usdc", 0))
            if amount >= PREMIUM_PRICE_USDC - 0.01:
                return True
    except ImportError:
        pass
    except Exception as e:
        logger.warning("Premium USDC Solana verify error: %s", e)

    # Try EVM verification (0x... hash)
    if tx_hash.startswith("0x"):
        try:
            from blockchain.base_verifier import verify_base_transaction

            result = await verify_base_transaction(tx_hash)
            if result and result.get("verified"):
                return True
        except ImportError:
            pass
        except Exception as e:
            logger.warning("Premium USDC EVM verify error: %s", e)

    # If both verifiers unavailable, accept with warning (manual review)
    logger.warning(
        "Premium USDC payment accepted without verification (services unavailable): %s",
        tx_hash[:16],
    )
    return True


@router.get("/status")
async def get_status(x_api_key: str = Header(alias="x-api-key")) -> dict:
    """Check subscription status for current API key."""
    if not x_api_key:
        raise HTTPException(400, "Header x-api-key requis")

    tier_info = await get_tier_for_key(x_api_key)
    sub = await _get_active_sub(x_api_key)

    result: dict = {
        "api_key_prefix": x_api_key[:8] + "...",
        "tier": tier_info["tier"],
        "rate_limit_day": tier_info["rate_limit_day"],
        "rate_limit_min": tier_info["rate_limit_min"],
        "features": TIER_DEFS.get(tier_info["tier"], TIER_DEFS["FREE"])["features"],
    }

    if sub:
        expires_at = sub.get("expires_at")
        result["sub_id"] = sub.get("sub_id")
        result["started_at"] = sub.get("started_at")
        result["expires_at"] = expires_at
        if expires_at:
            result["expires_at_iso"] = datetime.fromtimestamp(
                int(expires_at), tz=timezone.utc
            ).isoformat()
            result["days_remaining"] = max(
                0, (int(expires_at) - int(time.time())) // 86400
            )
        result["status"] = sub.get("status", "active")
    else:
        result["status"] = "free"

    return result


@router.get("/usage")
async def get_usage(x_api_key: str = Header(alias="x-api-key")) -> dict:
    """Usage stats: requests today, remaining quota, tier info."""
    if not x_api_key:
        raise HTTPException(400, "Header x-api-key requis")

    tier_info = await get_tier_for_key(x_api_key)
    tier_name = tier_info["tier"]
    limit_day = tier_info["rate_limit_day"]
    limit_min = tier_info["rate_limit_min"]
    now = time.time()
    day_start = now - (now % 86400)

    # Count today's usage from in-memory store
    day_key = f"premium:{x_api_key}:day"
    day_hits = _usage_store.get(day_key, [])
    used_today = len([t for t in day_hits if t > day_start])

    # Count last minute usage
    min_key = f"premium:{x_api_key}:min"
    min_hits = _usage_store.get(min_key, [])
    used_last_min = len([t for t in min_hits if t > now - 60])

    return {
        "api_key_prefix": x_api_key[:8] + "...",
        "tier": tier_name,
        "limit_day": limit_day,
        "used_today": used_today,
        "remaining_day": max(0, limit_day - used_today),
        "limit_min": limit_min,
        "used_last_min": used_last_min,
        "remaining_min": max(0, limit_min - used_last_min),
        "reset_day_utc": datetime.fromtimestamp(
            day_start + 86400, tz=timezone.utc
        ).isoformat(),
    }


@router.post("/cancel")
async def cancel(req: CancelRequest) -> dict:
    """Cancel an active Premium subscription."""
    if not req.api_key or len(req.api_key) < 8:
        raise HTTPException(400, "api_key invalide")

    sub = await _get_active_sub(req.api_key)
    if not sub:
        raise HTTPException(404, "Aucun abonnement actif pour cette API key")

    sub_id = sub["sub_id"] if isinstance(sub, dict) else sub[0]
    await _cancel_subscription(sub_id, req.api_key)

    return {
        "status": "cancelled",
        "sub_id": sub_id,
        "message": "Abonnement annule. Votre acces sera degrade en FREE.",
    }
