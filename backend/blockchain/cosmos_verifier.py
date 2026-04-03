"""MAXIA — Cosmos (IBC) Transaction Verifier

Verifies USDC transfers on Cosmos Hub and Noble chain via LCD REST API.
Noble is the native USDC issuer on Cosmos (Circle's official chain).
IBC transfers from Noble propagate to 50+ Cosmos chains.

API: https://lcd-cosmoshub.keplr.app or https://rest.cosmos.directory/noble
"""
import logging
import time

from core.http_client import get_http_client

logger = logging.getLogger("maxia.cosmos_verifier")

# Noble USDC denom (native issuance by Circle)
NOBLE_USDC_DENOM = "uusdc"  # 6 decimals (1 USDC = 1_000_000 uusdc)
# IBC denom on Cosmos Hub (from Noble via IBC)
COSMOSHUB_USDC_IBC = "ibc/498A0751C798A0D9A389AA3691123DADA57DAA4FE165D5C75894505B876BA6E4"

COSMOS_LCD_URLS = [
    "https://rest.cosmos.directory/noble",
    "https://lcd-cosmoshub.keplr.app",
    "https://rest.cosmos.directory/cosmoshub",
]

# Cache
_tx_cache: dict = {}
_TX_CACHE_TTL = 300
_TX_CACHE_MAX = 1000


async def verify_usdc_transfer(
    tx_hash: str,
    expected_amount_usdc: float = 0,
    expected_recipient: str = "",
) -> dict:
    """Verify a USDC transfer on Cosmos/Noble.

    Args:
        tx_hash: Transaction hash (hex string).
        expected_amount_usdc: Expected amount in USDC.
        expected_recipient: Expected recipient address (cosmos1... or noble1...).

    Returns:
        Dict with valid, amount_usdc, from, to.
    """
    if not tx_hash or len(tx_hash) < 10:
        return {"valid": False, "error": "Invalid tx hash"}

    # Cache check
    cache_key = f"cosmos:{tx_hash}"
    if cache_key in _tx_cache:
        cached = _tx_cache[cache_key]
        if time.time() - cached["ts"] < _TX_CACHE_TTL:
            return cached["result"]

    tx_hash = tx_hash.upper()
    client = get_http_client()

    for lcd_url in COSMOS_LCD_URLS:
        try:
            resp = await client.get(
                f"{lcd_url}/cosmos/tx/v1beta1/txs/{tx_hash}",
                timeout=10,
            )
            if resp.status_code != 200:
                continue

            data = resp.json()
            tx_resp = data.get("tx_response", {})

            # Check tx succeeded
            if tx_resp.get("code", -1) != 0:
                return {"valid": False, "error": f"Transaction failed: code {tx_resp.get('code')}"}

            # Parse events for transfer
            for event in tx_resp.get("events", []):
                if event.get("type") != "transfer":
                    continue
                attrs = {a["key"]: a["value"] for a in event.get("attributes", [])}
                amount_str = attrs.get("amount", "")
                recipient = attrs.get("recipient", "")
                sender = attrs.get("sender", "")

                # Parse amount: "1000000uusdc" or "1000000ibc/498A..."
                usdc_amount = 0.0
                if NOBLE_USDC_DENOM in amount_str:
                    raw = amount_str.replace(NOBLE_USDC_DENOM, "")
                    usdc_amount = int(raw) / 1_000_000
                elif COSMOSHUB_USDC_IBC in amount_str:
                    raw = amount_str.replace(COSMOSHUB_USDC_IBC, "")
                    usdc_amount = int(raw) / 1_000_000

                if usdc_amount <= 0:
                    continue

                # Check recipient match
                recipient_ok = not expected_recipient or recipient == expected_recipient
                # Check amount match (0.1% tolerance)
                amount_ok = expected_amount_usdc <= 0 or usdc_amount >= expected_amount_usdc * 0.999

                if recipient_ok and amount_ok:
                    result = {
                        "valid": True,
                        "tx_hash": tx_hash,
                        "from": sender,
                        "to": recipient,
                        "amount_usdc": usdc_amount,
                        "chain": "cosmos",
                    }
                    _cache_result(cache_key, result)
                    return result

            return {"valid": False, "error": "No USDC transfer found in transaction events"}

        except Exception as e:
            logger.warning("[Cosmos] LCD %s error: %s", lcd_url[:30], e)
            continue

    return {"valid": False, "error": "All Cosmos LCD endpoints failed"}


def _cache_result(key: str, result: dict):
    _tx_cache[key] = {"result": result, "ts": time.time()}
    if len(_tx_cache) > _TX_CACHE_MAX:
        oldest = sorted(_tx_cache, key=lambda k: _tx_cache[k]["ts"])
        for k in oldest[:_TX_CACHE_MAX // 2]:
            _tx_cache.pop(k, None)
