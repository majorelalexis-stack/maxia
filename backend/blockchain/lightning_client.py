"""MAXIA Lightning Network Client — Bitcoin micropayments via ln.bot API.

Handles invoice creation, payment verification, and withdrawals.
ln.bot: zero monthly fee, pay-per-outbound-tx only, AI-agent native.

Env vars:
  LNBOT_API_KEY    — Wallet key (wk_...) from ln.bot
  LNBOT_WALLET_ID  — Wallet ID (wal_...) from ln.bot
"""
import logging
import time

from core.http_client import get_http_client

logger = logging.getLogger(__name__)

# ── Config ──
import os
LNBOT_API_KEY = os.getenv("LNBOT_API_KEY", "")
LNBOT_WALLET_ID = os.getenv("LNBOT_WALLET_ID", "")
_BASE_URL = "https://api.ln.bot"

# ── BTC price cache ──
_btc_price_cache: float = 0.0
_btc_price_ts: float = 0.0
_BTC_PRICE_TTL = 60  # refresh every 60s


async def get_btc_price() -> float:
    """Get current BTC/USD price (cached 60s). Uses MAXIA oracle first, CoinGecko fallback."""
    global _btc_price_cache, _btc_price_ts
    if _btc_price_cache > 0 and time.time() - _btc_price_ts < _BTC_PRICE_TTL:
        return _btc_price_cache
    # Try MAXIA's own price oracle first (already has BTC from Pyth/CoinGecko)
    try:
        from trading.price_oracle import get_prices
        prices = await get_prices()
        btc_data = prices.get("prices", prices).get("BTC", 0)
        btc_price = btc_data.get("price", btc_data) if isinstance(btc_data, dict) else btc_data
        if btc_price and btc_price > 0:
            _btc_price_cache = float(btc_price)
            _btc_price_ts = time.time()
            return _btc_price_cache
    except Exception:
        pass
    # Fallback: direct CoinGecko
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
        return _btc_price_cache or 83000.0  # fallback estimate


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
    """Create a Lightning invoice via ln.bot.

    Args:
        amount_sats: Amount in satoshis.
        description: Invoice description.
        callback_url: Unused (ln.bot uses webhooks configured per wallet).
        order_id: Optional reference for tracking.

    Returns:
        Dict with id, lightning_invoice (bolt11), amount, status, expires_at.
    """
    if not LNBOT_API_KEY or not LNBOT_WALLET_ID:
        return {"success": False, "error": "LNBOT_API_KEY or LNBOT_WALLET_ID not configured"}

    try:
        client = get_http_client()
        body = {
            "amount": amount_sats,
            "memo": description[:200],
        }
        if order_id:
            body["reference"] = order_id

        resp = await client.post(
            f"{_BASE_URL}/v1/wallets/{LNBOT_WALLET_ID}/invoices",
            json=body,
            headers={"Authorization": f"Bearer {LNBOT_API_KEY}", "Content-Type": "application/json"},
            timeout=15,
        )
        if resp.status_code >= 400:
            return {"success": False, "error": resp.text[:200]}

        data = resp.json()

        if not data.get("bolt11"):
            return {"success": False, "error": resp.text[:200]}

        return {
            "success": True,
            "id": str(data.get("number", "")),
            "lightning_invoice": data.get("bolt11", ""),
            "amount_sats": amount_sats,
            "amount_btc": amount_sats / 100_000_000,
            "status": "unpaid" if data.get("status") == "pending" else data.get("status", "unpaid"),
            "expires_at": data.get("expiresAt", ""),
        }
    except Exception as e:
        logger.error("[Lightning] Create invoice error: %s", e)
        return {"success": False, "error": str(e)[:100]}


async def check_payment(charge_id: str) -> dict:
    """Check the status of a Lightning payment.

    Args:
        charge_id: Invoice number (from create_invoice id field).

    Returns:
        Dict with id, status ("paid", "unpaid", "expired"), amount, settled_at.
    """
    if not LNBOT_API_KEY or not LNBOT_WALLET_ID:
        return {"success": False, "error": "LNBOT_API_KEY or LNBOT_WALLET_ID not configured"}

    try:
        client = get_http_client()
        resp = await client.get(
            f"{_BASE_URL}/v1/wallets/{LNBOT_WALLET_ID}/invoices/{charge_id}",
            headers={"Authorization": f"Bearer {LNBOT_API_KEY}"},
            timeout=10,
        )
        if resp.status_code >= 400:
            return {"success": False, "error": resp.text[:200]}

        data = resp.json()

        # ln.bot status: "pending" or "settled"
        lnbot_status = data.get("status", "unknown")
        is_paid = lnbot_status == "settled"
        normalized_status = "paid" if is_paid else "unpaid"

        return {
            "success": True,
            "id": str(data.get("number", charge_id)),
            "status": normalized_status,
            "amount_sats": data.get("amount", 0),
            "paid": is_paid,
            "settled_at": data.get("settledAt"),
            "fee_sats": 0,
        }
    except Exception as e:
        logger.error("[Lightning] Check payment error: %s", e)
        return {"success": False, "error": str(e)[:100]}


async def withdraw(amount_sats: int, address: str, callback_url: str = "") -> dict:
    """Send sats to a Lightning address or invoice via ln.bot.

    Args:
        amount_sats: Amount to send in satoshis.
        address: Lightning invoice (bolt11) or Lightning address (user@domain).
        callback_url: Unused (ln.bot uses webhooks configured per wallet).
    """
    if not LNBOT_API_KEY or not LNBOT_WALLET_ID:
        return {"success": False, "error": "LNBOT_API_KEY or LNBOT_WALLET_ID not configured"}

    try:
        client = get_http_client()
        body = {
            "target": address,
            "amount": amount_sats,
        }

        resp = await client.post(
            f"{_BASE_URL}/v1/wallets/{LNBOT_WALLET_ID}/payments",
            json=body,
            headers={"Authorization": f"Bearer {LNBOT_API_KEY}", "Content-Type": "application/json"},
            timeout=15,
        )
        if resp.status_code >= 400:
            return {"success": False, "error": resp.text[:200]}

        data = resp.json()

        return {
            "success": data.get("status") == "settled",
            "id": str(data.get("number", "")),
            "status": data.get("status", "unknown"),
            "amount_sats": amount_sats,
        }
    except Exception as e:
        logger.error("[Lightning] Withdraw error: %s", e)
        return {"success": False, "error": str(e)[:100]}
