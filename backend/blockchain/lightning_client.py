"""MAXIA Lightning Network Client — Bitcoin micropayments via OpenNode API.

Handles invoice creation, payment verification, and withdrawals.
OpenNode auto-converts BTC to USD (1% fee).

Env vars:
  OPENNODE_API_KEY  — API key from opennode.com
  OPENNODE_ENV      — "live" or "dev" (default: live)
"""
import logging
import time
from typing import Optional

import httpx
from core.http_client import get_http_client
from core.config import get_rpc_url

logger = logging.getLogger(__name__)

# ── Config ──
import os
OPENNODE_API_KEY = os.getenv("OPENNODE_API_KEY", "")
OPENNODE_ENV = os.getenv("OPENNODE_ENV", "live")
_BASE_URL = "https://api.opennode.com" if OPENNODE_ENV == "live" else "https://dev-api.opennode.com"

# ── BTC price cache ──
_btc_price_cache: float = 0.0
_btc_price_ts: float = 0.0
_BTC_PRICE_TTL = 60  # refresh every 60s


async def get_btc_price() -> float:
    """Get current BTC/USD price (cached 60s)."""
    global _btc_price_cache, _btc_price_ts
    if _btc_price_cache > 0 and time.time() - _btc_price_ts < _BTC_PRICE_TTL:
        return _btc_price_cache
    try:
        client = get_http_client()
        resp = await client.get(
            "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd",
            timeout=10,
        )
        price = resp.json().get("bitcoin", {}).get("usd", 0)
        if price > 0:
            _btc_price_cache = float(price)
            _btc_price_ts = time.time()
        return _btc_price_cache
    except Exception as e:
        logger.warning("[Lightning] BTC price fetch failed: %s", e)
        return _btc_price_cache or 60000.0  # fallback


def usd_to_sats(usd_amount: float, btc_price: float) -> int:
    """Convert USD amount to satoshis."""
    if btc_price <= 0:
        return 0
    btc = usd_amount / btc_price
    return int(btc * 100_000_000)


def sats_to_usd(sats: int, btc_price: float) -> float:
    """Convert satoshis to USD."""
    if btc_price <= 0:
        return 0.0
    btc = sats / 100_000_000
    return round(btc * btc_price, 6)


async def create_invoice(
    amount_sats: int,
    description: str = "MAXIA AI Service",
    callback_url: str = "",
    order_id: str = "",
) -> dict:
    """Create a Lightning invoice via OpenNode.

    Args:
        amount_sats: Amount in satoshis.
        description: Invoice description.
        callback_url: Webhook URL for payment notification.
        order_id: Optional order ID for idempotency.

    Returns:
        Dict with id, lightning_invoice (bolt11), amount, status, expires_at.
    """
    if not OPENNODE_API_KEY:
        return {"success": False, "error": "OPENNODE_API_KEY not configured"}

    try:
        client = get_http_client()
        body = {
            "amount": amount_sats,
            "description": description[:200],
            "currency": "btc",
        }
        if callback_url:
            body["callback_url"] = callback_url
        if order_id:
            body["order_id"] = order_id

        resp = await client.post(
            f"{_BASE_URL}/v1/charges",
            json=body,
            headers={"Authorization": OPENNODE_API_KEY, "Content-Type": "application/json"},
            timeout=15,
        )
        data = resp.json().get("data", {})

        if not data.get("id"):
            return {"success": False, "error": resp.text[:200]}

        return {
            "success": True,
            "id": data["id"],
            "lightning_invoice": data.get("lightning_invoice", {}).get("payreq", ""),
            "amount_sats": amount_sats,
            "amount_btc": data.get("amount", 0),
            "status": data.get("status", "unpaid"),
            "expires_at": data.get("lightning_invoice", {}).get("expires_at", 0),
            "chain_invoice": data.get("chain_invoice", {}).get("address", ""),
        }
    except Exception as e:
        logger.error("[Lightning] Create invoice error: %s", e)
        return {"success": False, "error": str(e)[:100]}


async def check_payment(charge_id: str) -> dict:
    """Check the status of a Lightning payment.

    Returns:
        Dict with id, status ("paid", "unpaid", "expired"), amount, settled_at.
    """
    if not OPENNODE_API_KEY:
        return {"success": False, "error": "OPENNODE_API_KEY not configured"}

    try:
        client = get_http_client()
        resp = await client.get(
            f"{_BASE_URL}/v1/charge/{charge_id}",
            headers={"Authorization": OPENNODE_API_KEY},
            timeout=10,
        )
        data = resp.json().get("data", {})

        return {
            "success": True,
            "id": data.get("id", charge_id),
            "status": data.get("status", "unknown"),
            "amount_sats": data.get("amount", 0),
            "paid": data.get("status") == "paid",
            "settled_at": data.get("settled_at"),
            "fee_sats": data.get("fee", 0),
        }
    except Exception as e:
        logger.error("[Lightning] Check payment error: %s", e)
        return {"success": False, "error": str(e)[:100]}


async def withdraw(amount_sats: int, address: str, callback_url: str = "") -> dict:
    """Withdraw sats to a Lightning address or invoice.

    Args:
        amount_sats: Amount to withdraw in satoshis.
        address: Lightning invoice (bolt11) or Lightning address (user@domain).
        callback_url: Optional webhook for withdrawal status.
    """
    if not OPENNODE_API_KEY:
        return {"success": False, "error": "OPENNODE_API_KEY not configured"}

    try:
        client = get_http_client()
        body = {
            "type": "ln",
            "amount": amount_sats,
            "address": address,
        }
        if callback_url:
            body["callback_url"] = callback_url

        resp = await client.post(
            f"{_BASE_URL}/v2/withdrawals",
            json=body,
            headers={"Authorization": OPENNODE_API_KEY, "Content-Type": "application/json"},
            timeout=15,
        )
        data = resp.json().get("data", {})

        return {
            "success": bool(data.get("id")),
            "id": data.get("id", ""),
            "status": data.get("status", "unknown"),
            "amount_sats": amount_sats,
        }
    except Exception as e:
        logger.error("[Lightning] Withdraw error: %s", e)
        return {"success": False, "error": str(e)[:100]}
