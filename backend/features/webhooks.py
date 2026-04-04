"""MAXIA Webhooks — Push notifications for agents (execute, escrow, payment, order events).

Agents register HTTPS callback URLs and receive signed POST payloads
when relevant events occur. HMAC-SHA256 signature in X-Webhook-Signature header.
Max 5 webhooks per agent. Retry 3x with exponential backoff.
"""
import asyncio
import hashlib
import hmac
import json
import logging
import secrets
import time
import uuid
from ipaddress import ip_address
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Header, HTTPException, Request
from core.error_utils import safe_error

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webhooks"])

# ── Valid event types ──
VALID_EVENTS = frozenset({
    "execute.complete",
    "escrow.funded",
    "escrow.released",
    "payment.received",
    "order.new",
})

MAX_WEBHOOKS_PER_AGENT = 5

# ── Schema ──
_WEBHOOK_SCHEMA = """
CREATE TABLE IF NOT EXISTS webhooks (
    id TEXT PRIMARY KEY,
    agent_api_key TEXT NOT NULL,
    url TEXT NOT NULL,
    events TEXT NOT NULL,
    secret TEXT NOT NULL,
    created_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_webhooks_agent ON webhooks(agent_api_key);

CREATE TABLE IF NOT EXISTS webhook_deliveries (
    id TEXT PRIMARY KEY,
    webhook_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    attempts INTEGER DEFAULT 0,
    last_attempt INTEGER,
    response_code INTEGER
);
CREATE INDEX IF NOT EXISTS idx_wh_deliveries_webhook ON webhook_deliveries(webhook_id);
"""

_schema_ready = False


async def _ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    from core.database import db
    await db.raw_executescript(_WEBHOOK_SCHEMA)
    _schema_ready = True
    logger.info("[Webhooks] Schema pret")


# ── SSRF protection ──

def _validate_webhook_url(url: str) -> str:
    """Validate webhook URL: must be HTTPS, no internal/private IPs."""
    if not url or not isinstance(url, str):
        raise HTTPException(400, "url is required")

    url = url.strip()
    parsed = urlparse(url)

    if parsed.scheme != "https":
        raise HTTPException(400, "Webhook URL must use HTTPS")

    hostname = parsed.hostname
    if not hostname:
        raise HTTPException(400, "Invalid webhook URL: no hostname")

    # Block obviously internal hostnames
    blocked_hosts = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"}
    if hostname.lower() in blocked_hosts:
        raise HTTPException(400, "Webhook URL must not point to internal addresses")

    # Block private/reserved IP ranges
    try:
        ip = ip_address(hostname)
        if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local:
            raise HTTPException(400, "Webhook URL must not point to private/reserved IPs")
    except ValueError:
        # hostname is a domain name, not an IP — that's fine
        pass

    # Block common internal TLDs
    if hostname.endswith(".local") or hostname.endswith(".internal"):
        raise HTTPException(400, "Webhook URL must not point to internal domains")

    if len(url) > 2048:
        raise HTTPException(400, "Webhook URL too long (max 2048 chars)")

    return url


def _row_val(row: Any, key: str, idx: int, default: Any = None) -> Any:
    """Extract value from DB row (dict or tuple/Row)."""
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[idx]
    except (IndexError, KeyError):
        return default


async def _read_body(request: Request) -> dict:
    raw = await request.body()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(400, "Invalid JSON body")


async def _get_agent_key(x_api_key: str | None) -> str:
    """Validate X-API-Key and return it. Raises 401 if missing/invalid."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    from core.database import db
    row = await db._fetchone("SELECT api_key FROM agents WHERE api_key=?", (x_api_key,))
    if not row:
        raise HTTPException(401, "Invalid API key")
    return x_api_key


# ═══════════════════════════════════════════════════════════
#  WEBHOOK DELIVERY ENGINE
# ═══════════════════════════════════════════════════════════

def _sign_payload(payload_bytes: bytes, secret: str) -> str:
    """Compute HMAC-SHA256 signature for webhook payload."""
    return hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()


async def send_webhook(agent_api_key: str, event_type: str, payload: dict) -> None:
    """Send webhook notifications to all matching registrations for an agent.

    Called from other modules when events occur (escrow funded, execution complete, etc.).
    Non-blocking — fires and forgets delivery tasks.
    """
    if event_type not in VALID_EVENTS:
        logger.warning("[Webhooks] Unknown event type: %s", event_type)
        return

    await _ensure_schema()
    from core.database import db

    try:
        rows = await db.raw_execute_fetchall(
            "SELECT id, url, events, secret FROM webhooks WHERE agent_api_key=?",
            (agent_api_key,),
        )
    except Exception as e:
        logger.error("[Webhooks] DB error fetching hooks: %s", e)
        return

    for row in rows:
        wh_id = _row_val(row, "id", 0, "")
        url = _row_val(row, "url", 1, "")
        events_raw = _row_val(row, "events", 2, "[]")
        secret = _row_val(row, "secret", 3, "")

        try:
            events = json.loads(events_raw)
        except Exception:
            events = []

        if event_type not in events:
            continue

        # Fire delivery in background — don't block the caller
        asyncio.create_task(_deliver_webhook(wh_id, url, secret, event_type, payload))


async def _deliver_webhook(
    webhook_id: str,
    url: str,
    secret: str,
    event_type: str,
    payload: dict,
) -> None:
    """Deliver a single webhook with retry (3 attempts, exponential backoff: 1s, 5s, 15s)."""
    from core.database import db

    delivery_id = f"whd_{uuid.uuid4().hex[:12]}"
    envelope = {
        "event": event_type,
        "timestamp": int(time.time()),
        "delivery_id": delivery_id,
        "data": payload,
    }
    body_bytes = json.dumps(envelope, default=str).encode()
    signature = _sign_payload(body_bytes, secret)

    headers = {
        "Content-Type": "application/json",
        "X-Webhook-Signature": signature,
        "X-Webhook-Event": event_type,
        "X-Webhook-Delivery": delivery_id,
        "User-Agent": "MAXIA-Webhooks/1.0",
    }

    backoff_delays = [1, 5, 15]
    status = "failed"
    response_code = 0
    attempts = 0

    for attempt_idx, delay in enumerate(backoff_delays):
        attempts = attempt_idx + 1
        try:
            from core.http_client import get_http_client
            client = get_http_client()
            resp = await asyncio.wait_for(
                client.post(url, content=body_bytes, headers=headers),
                timeout=10.0,
            )
            response_code = resp.status_code

            if 200 <= resp.status_code < 300:
                status = "delivered"
                logger.info("[Webhooks] Delivered %s to %s (attempt %d)", event_type, url, attempts)
                break
            else:
                logger.warning(
                    "[Webhooks] Non-2xx response %d from %s (attempt %d/%d)",
                    resp.status_code, url, attempts, len(backoff_delays),
                )
        except asyncio.TimeoutError:
            logger.warning("[Webhooks] Timeout delivering to %s (attempt %d/%d)", url, attempts, len(backoff_delays))
        except Exception as e:
            logger.warning("[Webhooks] Error delivering to %s (attempt %d/%d): %s", url, attempts, len(backoff_delays), e)

        # Wait before retry (unless last attempt)
        if attempt_idx < len(backoff_delays) - 1:
            await asyncio.sleep(delay)

    # Record delivery attempt in DB
    now = int(time.time())
    try:
        await db.raw_execute(
            "INSERT INTO webhook_deliveries (id, webhook_id, event_type, status, attempts, last_attempt, response_code) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (delivery_id, webhook_id, event_type, status, attempts, now, response_code),
        )
    except Exception as e:
        logger.error("[Webhooks] Failed to record delivery: %s", e)


# ═══════════════════════════════════════════════════════════
#  API ENDPOINTS
# ═══════════════════════════════════════════════════════════

@router.post("/api/webhooks/register")
async def webhook_register(request: Request, x_api_key: str = Header(None, alias="X-API-Key")):
    """Register a webhook URL for push notifications.

    Body: {"url": "https://...", "events": ["execute.complete", "escrow.funded", ...]}
    Returns: webhook_id + secret (store the secret — used to verify signatures).
    """
    await _ensure_schema()
    agent_key = await _get_agent_key(x_api_key)
    body = await _read_body(request)

    url = _validate_webhook_url(body.get("url", ""))
    events = body.get("events", [])

    if not isinstance(events, list) or not events:
        raise HTTPException(400, "events must be a non-empty list")

    # Validate each event type
    invalid = [e for e in events if e not in VALID_EVENTS]
    if invalid:
        raise HTTPException(400, f"Invalid event types: {invalid}. Valid: {sorted(VALID_EVENTS)}")

    # Enforce max webhooks per agent
    from core.database import db
    try:
        existing = await db.raw_execute_fetchall(
            "SELECT id FROM webhooks WHERE agent_api_key=?", (agent_key,),
        )
        if len(existing) >= MAX_WEBHOOKS_PER_AGENT:
            raise HTTPException(
                400,
                f"Max {MAX_WEBHOOKS_PER_AGENT} webhooks per agent. Delete one first.",
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, safe_error(e, "webhook register count check")["error"])

    webhook_id = f"wh_{uuid.uuid4().hex[:12]}"
    wh_secret = secrets.token_hex(32)
    now = int(time.time())

    try:
        await db.raw_execute(
            "INSERT INTO webhooks (id, agent_api_key, url, events, secret, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (webhook_id, agent_key, url, json.dumps(sorted(events)), wh_secret, now),
        )
    except Exception as e:
        raise HTTPException(500, safe_error(e, "webhook register insert")["error"])

    logger.info("[Webhooks] Registered %s for agent %s...%s → %s", webhook_id, agent_key[:4], agent_key[-4:], url)

    return {
        "webhook_id": webhook_id,
        "url": url,
        "events": sorted(events),
        "secret": wh_secret,
        "message": "Store the secret securely — it is used to verify X-Webhook-Signature on incoming payloads.",
    }


@router.get("/api/webhooks/list")
async def webhook_list(x_api_key: str = Header(None, alias="X-API-Key")):
    """List all registered webhooks for the authenticated agent."""
    await _ensure_schema()
    agent_key = await _get_agent_key(x_api_key)
    from core.database import db

    try:
        rows = await db.raw_execute_fetchall(
            "SELECT id, url, events, created_at FROM webhooks WHERE agent_api_key=? ORDER BY created_at DESC",
            (agent_key,),
        )
    except Exception as e:
        raise HTTPException(500, safe_error(e, "webhook list")["error"])

    webhooks = []
    for r in rows:
        events_raw = _row_val(r, "events", 2, "[]")
        try:
            events = json.loads(events_raw)
        except Exception:
            events = []

        webhooks.append({
            "webhook_id": _row_val(r, "id", 0, ""),
            "url": _row_val(r, "url", 1, ""),
            "events": events,
            "created_at": _row_val(r, "created_at", 3, 0),
        })

    return {"webhooks": webhooks, "total": len(webhooks), "max": MAX_WEBHOOKS_PER_AGENT}


@router.delete("/api/webhooks/{webhook_id}")
async def webhook_delete(webhook_id: str, x_api_key: str = Header(None, alias="X-API-Key")):
    """Delete a registered webhook. Only the owning agent can delete."""
    await _ensure_schema()
    agent_key = await _get_agent_key(x_api_key)
    from core.database import db

    try:
        row = await db._fetchone(
            "SELECT id FROM webhooks WHERE id=? AND agent_api_key=?",
            (webhook_id, agent_key),
        )
        if not row:
            raise HTTPException(404, "Webhook not found or not owned by this agent")

        await db.raw_execute("DELETE FROM webhooks WHERE id=? AND agent_api_key=?", (webhook_id, agent_key))
        # Clean up delivery history
        await db.raw_execute("DELETE FROM webhook_deliveries WHERE webhook_id=?", (webhook_id,))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, safe_error(e, "webhook delete")["error"])

    logger.info("[Webhooks] Deleted %s for agent %s...%s", webhook_id, agent_key[:4], agent_key[-4:])
    return {"success": True, "deleted": webhook_id}


@router.post("/api/webhooks/test")
async def webhook_test(request: Request, x_api_key: str = Header(None, alias="X-API-Key")):
    """Send a test event to a specific webhook to verify the URL works.

    Body: {"webhook_id": "wh_..."} — sends a test ping to that webhook.
    """
    await _ensure_schema()
    agent_key = await _get_agent_key(x_api_key)
    body = await _read_body(request)

    webhook_id = (body.get("webhook_id", "") or "").strip()
    if not webhook_id:
        raise HTTPException(400, "webhook_id required")

    from core.database import db

    try:
        row = await db._fetchone(
            "SELECT id, url, secret FROM webhooks WHERE id=? AND agent_api_key=?",
            (webhook_id, agent_key),
        )
        if not row:
            raise HTTPException(404, "Webhook not found or not owned by this agent")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, safe_error(e, "webhook test lookup")["error"])

    url = _row_val(row, "url", 1, "")
    secret = _row_val(row, "secret", 2, "")

    # Send a test payload synchronously (so we can return the result)
    test_payload = {
        "event": "test",
        "timestamp": int(time.time()),
        "delivery_id": f"whd_test_{uuid.uuid4().hex[:8]}",
        "data": {"message": "This is a test webhook from MAXIA. If you receive this, your endpoint is working."},
    }
    body_bytes = json.dumps(test_payload, default=str).encode()
    signature = _sign_payload(body_bytes, secret)

    headers = {
        "Content-Type": "application/json",
        "X-Webhook-Signature": signature,
        "X-Webhook-Event": "test",
        "User-Agent": "MAXIA-Webhooks/1.0",
    }

    try:
        from core.http_client import get_http_client
        client = get_http_client()
        resp = await asyncio.wait_for(
            client.post(url, content=body_bytes, headers=headers),
            timeout=10.0,
        )
        return {
            "success": 200 <= resp.status_code < 300,
            "status_code": resp.status_code,
            "url": url,
            "message": "Webhook endpoint responded successfully" if 200 <= resp.status_code < 300 else f"Endpoint returned HTTP {resp.status_code}",
        }
    except asyncio.TimeoutError:
        return {"success": False, "url": url, "message": "Timeout (10s) — endpoint did not respond in time"}
    except Exception as e:
        logger.warning("[Webhooks] Test delivery failed to %s: %s", url, e)
        return {"success": False, "url": url, "message": f"Connection error: could not reach endpoint"}
