"""MAXIA L402 — Lightning-based API payments (HTTP 402 Payment Required).

L402 protocol: client calls API → gets 402 + Lightning invoice → pays → retries with proof.
Enables AI agents to pay per-call in sats without signup or stablecoins.

Flow:
  1. Agent calls /api/public/execute without payment_tx or credits
  2. If L402 enabled, API returns 402 with Lightning invoice in header
  3. Agent pays the invoice (2-5 seconds)
  4. Agent retries with X-Lightning-Payment: <charge_id> header
  5. API verifies payment and executes the service
"""
import logging
import time
from typing import Optional

from fastapi import HTTPException, Header, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

# ── Payment verification cache (avoid re-checking settled invoices) ──
_verified_payments: dict = {}  # charge_id -> {verified_at, amount_sats, service}
_MAX_CACHE = 10000


async def create_l402_challenge(amount_usd: float, service_id: str = "") -> dict:
    """Create a Lightning invoice for an L402 payment challenge.

    Returns a dict with the invoice and headers to send in a 402 response.
    """
    from blockchain.lightning_client import get_btc_price, usd_to_sats, create_invoice

    btc_price = await get_btc_price()
    amount_sats = usd_to_sats(amount_usd, btc_price)

    if amount_sats < 1:
        amount_sats = 1  # minimum 1 sat

    invoice = await create_invoice(
        amount_sats=amount_sats,
        description=f"MAXIA {service_id or 'service'} (${amount_usd})",
        order_id=f"l402_{service_id}_{int(time.time())}",
    )

    if not invoice.get("success"):
        return {"success": False, "error": invoice.get("error", "Invoice creation failed")}

    return {
        "success": True,
        "charge_id": invoice["id"],
        "lightning_invoice": invoice["lightning_invoice"],
        "amount_sats": amount_sats,
        "amount_usd": amount_usd,
        "btc_price": btc_price,
        "expires_at": invoice.get("expires_at"),
    }


def build_402_response(challenge: dict) -> JSONResponse:
    """Build an HTTP 402 response with Lightning payment details."""
    return JSONResponse(
        status_code=402,
        content={
            "error": "Payment Required",
            "method": "lightning",
            "lightning_invoice": challenge.get("lightning_invoice", ""),
            "charge_id": challenge.get("charge_id", ""),
            "amount_sats": challenge.get("amount_sats", 0),
            "amount_usd": challenge.get("amount_usd", 0),
            "instructions": [
                "1. Pay the Lightning invoice below (any Lightning wallet)",
                "2. Retry your request with header: X-Lightning-Payment: <charge_id>",
                "3. Payment settles in 2-5 seconds",
            ],
        },
        headers={
            "WWW-Authenticate": f'L402 invoice="{challenge.get("lightning_invoice", "")}", '
                                f'charge_id="{challenge.get("charge_id", "")}"',
        },
    )


async def verify_lightning_payment(charge_id: str, expected_usd: float = 0) -> dict:
    """Verify a Lightning payment was settled.

    Args:
        charge_id: ln.bot invoice number from the L402 challenge.
        expected_usd: Expected USD amount (for tolerance check).

    Returns:
        Dict with verified (bool), amount_sats, amount_usd.
    """
    # Check cache first
    if charge_id in _verified_payments:
        cached = _verified_payments[charge_id]
        return {"verified": True, "cached": True, **cached}

    from blockchain.lightning_client import check_payment, sats_to_usd, get_btc_price

    result = await check_payment(charge_id)

    if not result.get("paid"):
        return {
            "verified": False,
            "status": result.get("status", "unknown"),
            "error": f"Payment not settled (status: {result.get('status', 'unknown')})",
        }

    # Payment is settled — calculate USD value
    amount_sats = result.get("amount_sats", 0)
    btc_price = await get_btc_price()
    amount_usd = sats_to_usd(amount_sats, btc_price)

    # Tolerance check (BTC price can move between invoice creation and payment)
    if expected_usd > 0 and amount_usd < expected_usd * 0.90:
        return {
            "verified": False,
            "error": f"Underpaid: ${amount_usd:.4f} < ${expected_usd:.4f} (10% tolerance)",
        }

    # Cache the verification
    entry = {
        "amount_sats": amount_sats,
        "amount_usd": amount_usd,
        "verified_at": int(time.time()),
    }
    _verified_payments[charge_id] = entry

    # Trim cache
    if len(_verified_payments) > _MAX_CACHE:
        oldest = sorted(_verified_payments, key=lambda k: _verified_payments[k]["verified_at"])
        for k in oldest[:_MAX_CACHE // 2]:
            _verified_payments.pop(k, None)

    return {"verified": True, "amount_sats": amount_sats, "amount_usd": amount_usd}
