"""MAXIA — Hedera (HBAR) Transaction Verifier

Verifies USDC transfers on Hedera via Mirror Node REST API.
Hedera has fixed $0.0001 fees per tx — ideal for AI micropayments.
USDC on Hedera: HTS token 0.0.456858 (issued by Circle).

API: https://mainnet-public.mirrornode.hedera.com
"""
import logging
import time

from core.http_client import get_http_client

logger = logging.getLogger("maxia.hedera_verifier")

# USDC on Hedera (HTS token ID)
HEDERA_USDC_TOKEN_ID = "0.0.456858"  # Circle USDC
HEDERA_USDT_TOKEN_ID = "0.0.4992305"  # Tether USDT
ACCEPTED_STABLECOINS = {HEDERA_USDC_TOKEN_ID, HEDERA_USDT_TOKEN_ID}

HEDERA_MIRROR_URLS = [
    "https://mainnet-public.mirrornode.hedera.com",
    "https://mainnet.mirrornode.hedera.com",
]

# Cache
_tx_cache: dict = {}
_TX_CACHE_TTL = 300
_TX_CACHE_MAX = 1000


async def verify_usdc_transfer(
    transaction_id: str,
    expected_amount_usdc: float = 0,
    expected_recipient: str = "",
) -> dict:
    """Verify a USDC/USDT transfer on Hedera.

    Args:
        transaction_id: Hedera transaction ID (e.g. "0.0.1234-1234567890-123456789").
        expected_amount_usdc: Expected amount in USDC.
        expected_recipient: Expected recipient account (e.g. "0.0.5678").

    Returns:
        Dict with valid, amount_usdc, from, to.
    """
    if not transaction_id or len(transaction_id) < 5:
        return {"valid": False, "error": "Invalid transaction ID"}

    # Cache check
    cache_key = f"hedera:{transaction_id}"
    if cache_key in _tx_cache:
        cached = _tx_cache[cache_key]
        if time.time() - cached["ts"] < _TX_CACHE_TTL:
            return cached["result"]

    # Hedera tx IDs use format: 0.0.account-seconds-nanos
    # Mirror API wants: 0.0.account-seconds-nanos (with dashes)
    tx_id_api = transaction_id.replace("@", "-")
    client = get_http_client()

    for mirror_url in HEDERA_MIRROR_URLS:
        try:
            # Get transaction details
            resp = await client.get(
                f"{mirror_url}/api/v1/transactions/{tx_id_api}",
                timeout=10,
            )
            if resp.status_code != 200:
                continue

            data = resp.json()
            transactions = data.get("transactions", [])
            if not transactions:
                return {"valid": False, "error": "Transaction not found"}

            tx = transactions[0]

            # Check tx succeeded
            if tx.get("result") != "SUCCESS":
                return {"valid": False, "error": f"Transaction failed: {tx.get('result')}"}

            # Parse token transfers
            token_transfers = tx.get("token_transfers", [])
            for transfer in token_transfers:
                token_id = transfer.get("token_id", "")
                if token_id not in ACCEPTED_STABLECOINS:
                    continue

                amount = transfer.get("amount", 0)
                account = transfer.get("account", "")

                # Positive amount = receiving (credit)
                if amount <= 0:
                    continue

                usdc_amount = amount / 1_000_000  # USDC/USDT = 6 decimals on Hedera

                # Find sender (negative transfer for same token)
                sender = ""
                for t2 in token_transfers:
                    if t2.get("token_id") == token_id and t2.get("amount", 0) < 0:
                        sender = t2.get("account", "")
                        break

                # Check recipient match
                recipient_ok = not expected_recipient or account == expected_recipient
                # Check amount match (0.1% tolerance)
                amount_ok = expected_amount_usdc <= 0 or usdc_amount >= expected_amount_usdc * 0.999

                if recipient_ok and amount_ok:
                    result = {
                        "valid": True,
                        "transaction_id": transaction_id,
                        "from": sender,
                        "to": account,
                        "amount_usdc": usdc_amount,
                        "token": "USDC" if token_id == HEDERA_USDC_TOKEN_ID else "USDT",
                        "chain": "hedera",
                    }
                    _cache_result(cache_key, result)
                    return result

            return {"valid": False, "error": "No USDC/USDT transfer found in transaction"}

        except Exception as e:
            logger.warning("[Hedera] Mirror %s error: %s", mirror_url[:30], e)
            continue

    return {"valid": False, "error": "All Hedera mirror nodes failed"}


def _cache_result(key: str, result: dict):
    _tx_cache[key] = {"result": result, "ts": time.time()}
    if len(_tx_cache) > _TX_CACHE_MAX:
        oldest = sorted(_tx_cache, key=lambda k: _tx_cache[k]["ts"])
        for k in oldest[:_TX_CACHE_MAX // 2]:
            _tx_cache.pop(k, None)
