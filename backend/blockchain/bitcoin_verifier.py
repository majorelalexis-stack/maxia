"""MAXIA — Bitcoin On-Chain Transaction Verifier

Verifies BTC payments on the Bitcoin blockchain via Mempool.space API.
Fallback to Blockstream Esplora API. Same response schema (both use Esplora).

For Lightning micropayments, see lightning_client.py (ln.bot).
This module handles on-chain BTC verification for larger amounts.
"""

import asyncio
import logging
import time

from core.http_client import get_http_client
from core.error_utils import safe_error

logger = logging.getLogger("maxia.bitcoin_verifier")

# ── API endpoints (fallback chain) ──
MEMPOOL_API = "https://mempool.space/api"
BLOCKSTREAM_API = "https://blockstream.info/api"

# ── Rate limiting (10 req/s mempool.space) ──
_last_request_time = 0.0
_MIN_REQUEST_INTERVAL = 0.15  # 150ms between requests

# ── Block tip cache ──
_tip_height: int = 0
_tip_ts: float = 0.0
_TIP_TTL = 60  # refresh every 60s


async def _rate_limited_get(url: str, timeout: float = 15):
    """GET with rate limiting to avoid API throttling."""
    global _last_request_time
    now = time.monotonic()
    elapsed = now - _last_request_time
    if elapsed < _MIN_REQUEST_INTERVAL:
        await asyncio.sleep(_MIN_REQUEST_INTERVAL - elapsed)
    _last_request_time = time.monotonic()
    client = get_http_client()
    return await client.get(url, timeout=timeout)


async def _get_tip_height() -> int:
    """Get current Bitcoin block height (cached 60s)."""
    global _tip_height, _tip_ts
    if _tip_height > 0 and time.time() - _tip_ts < _TIP_TTL:
        return _tip_height

    for base_url in [MEMPOOL_API, BLOCKSTREAM_API]:
        try:
            resp = await _rate_limited_get(f"{base_url}/blocks/tip/height")
            if resp.status_code == 200:
                _tip_height = int(resp.text.strip())
                _tip_ts = time.time()
                return _tip_height
        except Exception:
            continue
    return _tip_height or 0


async def verify_bitcoin_transaction(
    tx_hash: str,
    expected_recipient: str = "",
    expected_amount_btc: float = 0,
    min_confirmations: int = 1,
) -> dict:
    """Verify a Bitcoin on-chain transaction.

    Args:
        tx_hash: Bitcoin transaction ID (64 hex chars).
        expected_recipient: Expected recipient address (any format: legacy, segwit, taproot).
        expected_amount_btc: Expected minimum amount in BTC.
        min_confirmations: Minimum confirmations required (default 1, use 6 for large amounts).

    Returns:
        dict with verified=True/False + details.
    """
    if not tx_hash or len(tx_hash) != 64:
        return {"verified": False, "error": "Invalid Bitcoin txid (must be 64 hex chars)"}

    tx_data = None

    for base_url in [MEMPOOL_API, BLOCKSTREAM_API]:
        try:
            resp = await _rate_limited_get(f"{base_url}/tx/{tx_hash}")
            if resp.status_code == 200:
                tx_data = resp.json()
                break
            elif resp.status_code == 404:
                continue
        except Exception as e:
            logger.warning("[Bitcoin] API %s error: %s", base_url, e)
            continue

    if not tx_data:
        return {"verified": False, "error": "Transaction not found on Bitcoin"}

    # ── Check confirmations ──
    status = tx_data.get("status", {})
    confirmed = status.get("confirmed", False)

    if confirmed:
        tip = await _get_tip_height()
        block_height = status.get("block_height", 0)
        confirmations = (tip - block_height + 1) if tip > 0 and block_height > 0 else 0
    else:
        confirmations = 0

    if confirmations < min_confirmations:
        return {
            "verified": False,
            "error": f"Insufficient confirmations: {confirmations}/{min_confirmations}",
            "confirmations": confirmations,
            "confirmed": confirmed,
        }

    # ── Check outputs for matching payment ──
    outputs = tx_data.get("vout", [])
    expected_sats = int(expected_amount_btc * 100_000_000) if expected_amount_btc > 0 else 0

    matched_output = None
    total_to_recipient = 0

    for vout in outputs:
        addr = vout.get("scriptpubkey_address", "")
        value_sats = vout.get("value", 0)

        if expected_recipient and addr == expected_recipient:
            total_to_recipient += value_sats
            if not matched_output:
                matched_output = vout

        if not expected_recipient and value_sats > 0:
            if not matched_output or value_sats > matched_output.get("value", 0):
                matched_output = vout

    # ── Validate recipient ──
    if expected_recipient and total_to_recipient == 0:
        return {
            "verified": False,
            "error": f"No output to expected recipient {expected_recipient}",
        }

    # ── Validate amount (1% tolerance) ──
    if expected_sats > 0 and total_to_recipient < expected_sats * 0.99:
        return {
            "verified": False,
            "error": f"Underpaid: {total_to_recipient} sats < {expected_sats} sats expected",
            "received_sats": total_to_recipient,
            "expected_sats": expected_sats,
        }

    # ── Build result ──
    fee_sats = tx_data.get("fee", 0)

    return {
        "verified": True,
        "chain": "bitcoin",
        "tx_hash": tx_hash,
        "confirmations": confirmations,
        "block_height": status.get("block_height"),
        "block_time": status.get("block_time"),
        "recipient": matched_output.get("scriptpubkey_address", "") if matched_output else "",
        "amount_sats": total_to_recipient or (matched_output.get("value", 0) if matched_output else 0),
        "amount_btc": (total_to_recipient or (matched_output.get("value", 0) if matched_output else 0)) / 100_000_000,
        "fee_sats": fee_sats,
        "outputs": len(outputs),
    }


async def get_address_balance(address: str) -> dict:
    """Get the BTC balance of an address."""
    for base_url in [MEMPOOL_API, BLOCKSTREAM_API]:
        try:
            resp = await _rate_limited_get(f"{base_url}/address/{address}")
            if resp.status_code == 200:
                data = resp.json()
                funded = data.get("chain_stats", {}).get("funded_txo_sum", 0)
                spent = data.get("chain_stats", {}).get("spent_txo_sum", 0)
                balance_sats = funded - spent
                return {
                    "address": address,
                    "balance_sats": balance_sats,
                    "balance_btc": balance_sats / 100_000_000,
                    "tx_count": data.get("chain_stats", {}).get("tx_count", 0),
                }
        except Exception as e:
            logger.warning("[Bitcoin] Balance check error: %s", e)
            continue
    return {"address": address, "balance_sats": 0, "balance_btc": 0, "error": "API unavailable"}
