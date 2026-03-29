"""MAXIA Art.26 V12 — Async Webhook Dispatcher with HMAC Signatures & Retry

Buyers provide a callback_url when purchasing a service.
When result is ready, MAXIA POSTs to the callback_url with HMAC-SHA256 signature.
Retry with exponential backoff: 30s, 2m, 10m, 1h (max 5 attempts).
"""
import logging
import asyncio, hashlib, hmac, json, time, uuid

logger = logging.getLogger(__name__)
from ipaddress import ip_address
from typing import Optional
from urllib.parse import urlparse

import httpx
from http_client import get_http_client

# ── Constants ──

MAX_ATTEMPTS = 5
RETRY_DELAYS = [30, 120, 600, 3600]  # seconds: 30s, 2m, 10m, 1h
DELIVERY_TIMEOUT = 15  # seconds per HTTP request
EVENT_TYPE = "service.result"

# ── SQL Schema ──

WEBHOOK_SCHEMA = """
CREATE TABLE IF NOT EXISTS webhook_callbacks (
    id TEXT PRIMARY KEY,
    buyer_wallet TEXT NOT NULL,
    callback_url TEXT NOT NULL,
    service_id TEXT DEFAULT '',
    command_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    payload TEXT DEFAULT '',
    attempts INTEGER NOT NULL DEFAULT 0,
    next_retry_at INTEGER,
    created_at INTEGER DEFAULT (strftime('%s','now'))
);

CREATE INDEX IF NOT EXISTS idx_webhook_command ON webhook_callbacks(command_id);
CREATE INDEX IF NOT EXISTS idx_webhook_wallet ON webhook_callbacks(buyer_wallet);
CREATE INDEX IF NOT EXISTS idx_webhook_retry ON webhook_callbacks(status, next_retry_at);
"""

# ── Helpers ──

_PRIVATE_RANGES = [
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
    "127.0.0.0/8", "169.254.0.0/16", "::1/128", "fc00::/7",
]


def _is_private_ip(host: str) -> bool:
    """Check if a hostname resolves to a private/internal IP."""
    try:
        addr = ip_address(host)
        return addr.is_private or addr.is_loopback or addr.is_link_local
    except ValueError:
        return False


def validate_callback_url(url: str) -> str:
    """Validate that callback_url is HTTPS, not localhost, not internal IP.
    Returns the validated URL or raises ValueError.
    """
    parsed = urlparse(url)

    if parsed.scheme != "https":
        raise ValueError("Callback URL must use HTTPS")

    hostname = parsed.hostname or ""

    if not hostname:
        raise ValueError("Callback URL has no hostname")

    # Block localhost variants
    if hostname in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        raise ValueError("Callback URL cannot point to localhost")

    # Block internal IPs
    if _is_private_ip(hostname):
        raise ValueError("Callback URL cannot point to internal/private IP")

    return url


def _sign_payload(payload_bytes: bytes, secret: str) -> str:
    """HMAC-SHA256 signature of the payload using the buyer's API key hash as secret."""
    return hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()


# ── Table bootstrap ──

async def ensure_tables(db):
    """Create webhook_callbacks table if it doesn't exist."""
    await db.raw_executescript(WEBHOOK_SCHEMA)
    logger.info("Tables ensured")


# ── Core functions ──

async def register_callback(
    db,
    buyer_wallet: str,
    callback_url: str,
    command_id: str,
    service_id: str = "",
) -> dict:
    """Store a webhook callback registration."""
    validated_url = validate_callback_url(callback_url)
    webhook_id = str(uuid.uuid4())

    await db.raw_execute(
        "INSERT INTO webhook_callbacks(id, buyer_wallet, callback_url, service_id, command_id, status) "
        "VALUES(?,?,?,?,?,?)",
        (webhook_id, buyer_wallet, validated_url, service_id, command_id, "pending"),
    )
    # commit handled by raw_execute

    return {
        "webhook_id": webhook_id,
        "callback_url": validated_url,
        "command_id": command_id,
        "status": "pending",
    }


async def _get_signing_secret(db, buyer_wallet: str) -> str:
    """Get the HMAC signing secret for a buyer.
    Uses the buyer's api_key_hash from api_keys_v2, falling back to a SHA-256
    of the wallet address if no scoped key exists.
    """
    try:
        rows = await db.raw_execute_fetchall(
            "SELECT api_key_hash FROM api_keys_v2 WHERE agent_wallet=? AND revoked_at IS NULL "
            "ORDER BY created_at DESC LIMIT 1",
            (buyer_wallet,),
        )
        if rows:
            return rows[0]["api_key_hash"]
    except Exception:
        pass

    # Fallback: hash the wallet address itself
    return hashlib.sha256(buyer_wallet.encode()).hexdigest()


async def _deliver(
    db,
    webhook_id: str,
    callback_url: str,
    payload_bytes: bytes,
    secret: str,
) -> bool:
    """Attempt a single delivery. Returns True on success (2xx)."""
    signature = _sign_payload(payload_bytes, secret)
    ts = str(int(time.time()))

    headers = {
        "Content-Type": "application/json",
        "X-MAXIA-Signature": signature,
        "X-MAXIA-Event": EVENT_TYPE,
        "X-MAXIA-Timestamp": ts,
        "User-Agent": "MAXIA-Webhook/1.0",
    }

    try:
        client = get_http_client()
        resp = await client.post(callback_url, content=payload_bytes, headers=headers, timeout=DELIVERY_TIMEOUT)

        if 200 <= resp.status_code < 300:
            await db.raw_execute(
                "UPDATE webhook_callbacks SET status='delivered', attempts=attempts+1 WHERE id=?",
                (webhook_id,),
            )
            # commit handled by raw_execute
            return True
        else:
            logger.warning(f"Delivery to {callback_url} returned {resp.status_code}")
    except httpx.TimeoutException:
        logger.warning(f"Timeout delivering to {callback_url}")
    except Exception as e:
        logger.error(f"Delivery error for {callback_url}: {e}")

    return False


async def dispatch(db, command_id: str, result_payload: dict) -> dict:
    """Dispatch result to all registered callbacks for a command_id."""
    rows = await db.raw_execute_fetchall(
        "SELECT id, buyer_wallet, callback_url, service_id, command_id, "
        "status, payload, attempts, next_retry_at, created_at "
        "FROM webhook_callbacks WHERE command_id=? AND status='pending'",
        (command_id,),
    )

    if not rows:
        return {"dispatched": 0, "message": "No pending webhooks for this command"}

    results = []
    for row in rows:
        row = dict(row)
        webhook_id = row["id"]
        callback_url = row["callback_url"]
        buyer_wallet = row["buyer_wallet"]

        payload = {
            "event": EVENT_TYPE,
            "command_id": command_id,
            "webhook_id": webhook_id,
            "timestamp": int(time.time()),
            "data": result_payload,
        }
        payload_bytes = json.dumps(payload, separators=(",", ":")).encode()
        secret = await _get_signing_secret(db, buyer_wallet)

        # Store payload for potential retries
        await db.raw_execute(
            "UPDATE webhook_callbacks SET payload=? WHERE id=?",
            (payload_bytes.decode(), webhook_id),
        )
        # commit handled by raw_execute

        success = await _deliver(db, webhook_id, callback_url, payload_bytes, secret)

        if not success:
            # Schedule first retry
            next_retry = int(time.time()) + RETRY_DELAYS[0]
            await db.raw_execute(
                "UPDATE webhook_callbacks SET attempts=1, next_retry_at=? WHERE id=?",
                (next_retry, webhook_id),
            )
            # commit handled by raw_execute

        results.append({
            "webhook_id": webhook_id,
            "callback_url": callback_url,
            "delivered": success,
        })

    return {"dispatched": len(results), "results": results}


async def retry_worker(db):
    """Background async loop that retries failed deliveries.
    Run this as an asyncio.create_task() on startup.
    """
    logger.info("Retry worker started")
    while True:
        try:
            now = int(time.time())
            rows = await db.raw_execute_fetchall(
                "SELECT id, buyer_wallet, callback_url, service_id, command_id, "
                "status, payload, attempts, next_retry_at, created_at "
                "FROM webhook_callbacks WHERE status='pending' "
                "AND next_retry_at IS NOT NULL AND next_retry_at<=? "
                "AND attempts < ?",
                (now, MAX_ATTEMPTS),
            )

            for row in rows:
                row = dict(row)
                webhook_id = row["id"]
                callback_url = row["callback_url"]
                buyer_wallet = row["buyer_wallet"]
                attempts = row["attempts"]
                stored_payload = row.get("payload", "")

                if not stored_payload:
                    # No payload stored — mark as failed
                    await db.raw_execute(
                        "UPDATE webhook_callbacks SET status='failed' WHERE id=?",
                        (webhook_id,),
                    )
                    # commit handled by raw_execute
                    continue

                payload_bytes = stored_payload.encode()
                secret = await _get_signing_secret(db, buyer_wallet)
                success = await _deliver(db, webhook_id, callback_url, payload_bytes, secret)

                if not success:
                    new_attempts = attempts + 1
                    if new_attempts >= MAX_ATTEMPTS:
                        await db.raw_execute(
                            "UPDATE webhook_callbacks SET status='failed', attempts=? WHERE id=?",
                            (new_attempts, webhook_id),
                        )
                    else:
                        delay_idx = min(new_attempts - 1, len(RETRY_DELAYS) - 1)
                        next_retry = int(time.time()) + RETRY_DELAYS[delay_idx]
                        await db.raw_execute(
                            "UPDATE webhook_callbacks SET attempts=?, next_retry_at=? WHERE id=?",
                            (new_attempts, next_retry, webhook_id),
                        )
                    # commit handled by raw_execute

            if rows:
                logger.info(f"Retried {len(rows)} webhook(s)")

        except Exception as e:
            logger.error(f"Retry worker error: {e}")

        await asyncio.sleep(15)


async def get_webhook_history(db, wallet: str, limit: int = 20) -> list[dict]:
    """Return recent webhooks for a wallet."""
    rows = await db.raw_execute_fetchall(
        "SELECT id, callback_url, service_id, command_id, status, attempts, created_at "
        "FROM webhook_callbacks WHERE buyer_wallet=? ORDER BY created_at DESC LIMIT ?",
        (wallet, limit),
    )
    return [dict(r) for r in rows]


async def test_webhook(callback_url: str) -> dict:
    """Send a test ping to verify the endpoint is reachable."""
    validated_url = validate_callback_url(callback_url)

    ping_payload = {
        "event": "webhook.test",
        "timestamp": int(time.time()),
        "data": {"message": "MAXIA webhook test ping"},
    }
    payload_bytes = json.dumps(ping_payload, separators=(",", ":")).encode()

    # Sign with a static test secret
    signature = _sign_payload(payload_bytes, "maxia_test_ping")

    headers = {
        "Content-Type": "application/json",
        "X-MAXIA-Signature": signature,
        "X-MAXIA-Event": "webhook.test",
        "X-MAXIA-Timestamp": str(int(time.time())),
        "User-Agent": "MAXIA-Webhook/1.0",
    }

    try:
        client = get_http_client()
        resp = await client.post(validated_url, content=payload_bytes, headers=headers, timeout=DELIVERY_TIMEOUT)

        return {
            "url": validated_url,
            "status_code": resp.status_code,
            "ok": 200 <= resp.status_code < 300,
        }
    except httpx.TimeoutException:
        return {"url": validated_url, "status_code": 0, "ok": False, "error": "Timeout"}
    except Exception as e:
        return {"url": validated_url, "status_code": 0, "ok": False, "error": "An error occurred"}
