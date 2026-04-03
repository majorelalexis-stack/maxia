"""MAXIA — Cardano (ADA) Transaction Verifier

Verifies USDC transfers on Cardano via Blockfrost API.
USDC on Cardano: policy ID from IOHK/Circle partnership.

API: https://cardano-mainnet.blockfrost.io/api/v0
Requires: BLOCKFROST_API_KEY env var.
"""
import logging
import os
import time

from core.http_client import get_http_client

logger = logging.getLogger("maxia.cardano_verifier")

BLOCKFROST_API_KEY = os.getenv("BLOCKFROST_API_KEY", "")
BLOCKFROST_URL = "https://cardano-mainnet.blockfrost.io/api/v0"

# USDC on Cardano (IOHK bridge from Ethereum)
CARDANO_USDC_POLICY = "f66d78b4a3cb3d37afa0ec36461e51ecbde00f26c8f0a68f94b69880"  # USDC policy ID
CARDANO_USDC_ASSET = f"{CARDANO_USDC_POLICY}555344432e65"  # USDC.e hex

# Cache
_tx_cache: dict = {}
_TX_CACHE_TTL = 300
_TX_CACHE_MAX = 1000


async def verify_usdc_transfer(
    tx_hash: str,
    expected_amount_usdc: float = 0,
    expected_recipient: str = "",
) -> dict:
    """Verify a USDC transfer on Cardano via Blockfrost.

    Args:
        tx_hash: Cardano transaction hash.
        expected_amount_usdc: Expected USDC amount.
        expected_recipient: Expected recipient address (addr1...).

    Returns:
        Dict with valid, amount_usdc, from, to.
    """
    if not tx_hash or len(tx_hash) < 10:
        return {"valid": False, "error": "Invalid tx hash"}
    if not BLOCKFROST_API_KEY:
        return {"valid": False, "error": "BLOCKFROST_API_KEY not configured"}

    # Cache check
    cache_key = f"cardano:{tx_hash}"
    if cache_key in _tx_cache:
        cached = _tx_cache[cache_key]
        if time.time() - cached["ts"] < _TX_CACHE_TTL:
            return cached["result"]

    client = get_http_client()
    headers = {"project_id": BLOCKFROST_API_KEY}

    try:
        # Get transaction UTXOs
        resp = await client.get(
            f"{BLOCKFROST_URL}/txs/{tx_hash}/utxos",
            headers=headers,
            timeout=10,
        )
        if resp.status_code != 200:
            return {"valid": False, "error": f"Blockfrost error: {resp.status_code}"}

        data = resp.json()
        outputs = data.get("outputs", [])

        for output in outputs:
            address = output.get("address", "")
            for asset in output.get("amount", []):
                unit = asset.get("unit", "")
                quantity = int(asset.get("quantity", 0))

                # Check if it's USDC
                if CARDANO_USDC_POLICY not in unit:
                    continue

                usdc_amount = quantity / 1_000_000  # 6 decimals

                # Get sender from inputs
                sender = ""
                for inp in data.get("inputs", []):
                    sender = inp.get("address", "")
                    break

                recipient_ok = not expected_recipient or address == expected_recipient
                amount_ok = expected_amount_usdc <= 0 or usdc_amount >= expected_amount_usdc * 0.999

                if recipient_ok and amount_ok:
                    result = {
                        "valid": True,
                        "tx_hash": tx_hash,
                        "from": sender,
                        "to": address,
                        "amount_usdc": usdc_amount,
                        "chain": "cardano",
                    }
                    _cache_result(cache_key, result)
                    return result

        return {"valid": False, "error": "No USDC transfer found in transaction"}

    except Exception as e:
        logger.error("[Cardano] Blockfrost error: %s", e)
        return {"valid": False, "error": str(e)[:100]}


def _cache_result(key: str, result: dict):
    _tx_cache[key] = {"result": result, "ts": time.time()}
    if len(_tx_cache) > _TX_CACHE_MAX:
        oldest = sorted(_tx_cache, key=lambda k: _tx_cache[k]["ts"])
        for k in oldest[:_TX_CACHE_MAX // 2]:
            _tx_cache.pop(k, None)
