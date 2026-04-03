"""MAXIA — Polkadot (DOT) Transaction Verifier

Verifies USDC transfers on Polkadot Asset Hub via Subscan API.
USDC on Polkadot Asset Hub: asset ID 1337 (Circle native).

API: https://assethub-polkadot.api.subscan.io
"""
import logging
import os
import time

from core.http_client import get_http_client

logger = logging.getLogger("maxia.polkadot_verifier")

SUBSCAN_API_KEY = os.getenv("SUBSCAN_API_KEY", "")
SUBSCAN_URL = "https://assethub-polkadot.api.subscan.io"

# USDC on Polkadot Asset Hub (asset ID 1337, issued by Circle)
POLKADOT_USDC_ASSET_ID = 1337

# Cache
_tx_cache: dict = {}
_TX_CACHE_TTL = 300
_TX_CACHE_MAX = 1000


async def verify_usdc_transfer(
    block_hash_or_extrinsic: str,
    expected_amount_usdc: float = 0,
    expected_recipient: str = "",
) -> dict:
    """Verify a USDC transfer on Polkadot Asset Hub.

    Args:
        block_hash_or_extrinsic: Extrinsic hash or ID (e.g. "12345678-2").
        expected_amount_usdc: Expected USDC amount.
        expected_recipient: Expected recipient address.

    Returns:
        Dict with valid, amount_usdc, from, to.
    """
    if not block_hash_or_extrinsic or len(block_hash_or_extrinsic) < 5:
        return {"valid": False, "error": "Invalid extrinsic hash"}

    # Cache check
    cache_key = f"polkadot:{block_hash_or_extrinsic}"
    if cache_key in _tx_cache:
        cached = _tx_cache[cache_key]
        if time.time() - cached["ts"] < _TX_CACHE_TTL:
            return cached["result"]

    client = get_http_client()
    headers = {"Content-Type": "application/json"}
    if SUBSCAN_API_KEY:
        headers["X-API-Key"] = SUBSCAN_API_KEY

    try:
        # Query extrinsic details
        resp = await client.post(
            f"{SUBSCAN_URL}/api/scan/extrinsic",
            json={"hash": block_hash_or_extrinsic},
            headers=headers,
            timeout=10,
        )
        if resp.status_code != 200:
            return {"valid": False, "error": f"Subscan error: {resp.status_code}"}

        data = resp.json().get("data", {})
        if not data:
            return {"valid": False, "error": "Extrinsic not found"}

        # Check success
        if not data.get("success"):
            return {"valid": False, "error": "Extrinsic failed"}

        # Parse events for asset transfers
        events = data.get("event", [])
        sender = data.get("account_id", "")

        for event in events:
            module = event.get("module_id", "")
            event_id = event.get("event_id", "")

            # Look for Assets.Transferred event
            if module == "Assets" and event_id == "Transferred":
                params = event.get("params", [])
                # params: [asset_id, from, to, amount]
                if len(params) >= 4:
                    asset_id = params[0].get("value", 0) if isinstance(params[0], dict) else params[0]
                    from_addr = params[1].get("value", "") if isinstance(params[1], dict) else str(params[1])
                    to_addr = params[2].get("value", "") if isinstance(params[2], dict) else str(params[2])
                    raw_amount = params[3].get("value", 0) if isinstance(params[3], dict) else params[3]

                    if int(asset_id) != POLKADOT_USDC_ASSET_ID:
                        continue

                    usdc_amount = int(raw_amount) / 1_000_000  # 6 decimals

                    recipient_ok = not expected_recipient or to_addr == expected_recipient
                    amount_ok = expected_amount_usdc <= 0 or usdc_amount >= expected_amount_usdc * 0.999

                    if recipient_ok and amount_ok:
                        result = {
                            "valid": True,
                            "extrinsic": block_hash_or_extrinsic,
                            "from": from_addr or sender,
                            "to": to_addr,
                            "amount_usdc": usdc_amount,
                            "chain": "polkadot",
                        }
                        _cache_result(cache_key, result)
                        return result

        return {"valid": False, "error": "No USDC transfer found in extrinsic events"}

    except Exception as e:
        logger.error("[Polkadot] Subscan error: %s", e)
        return {"valid": False, "error": str(e)[:100]}


def _cache_result(key: str, result: dict):
    _tx_cache[key] = {"result": result, "ts": time.time()}
    if len(_tx_cache) > _TX_CACHE_MAX:
        oldest = sorted(_tx_cache, key=lambda k: _tx_cache[k]["ts"])
        for k in oldest[:_TX_CACHE_MAX // 2]:
            _tx_cache.pop(k, None)
