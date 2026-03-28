"""MAXIA V12 — Aptos Transaction Verifier
Aptos est un reseau Move (comme SUI) avec smart contracts en Move.
Utilise REST API pour verifier les transactions et soldes.
"""

import asyncio
import logging
import time

import httpx
from error_utils import safe_error
from http_client import get_http_client

logger = logging.getLogger("maxia.aptos_verifier")

# ── API endpoints (fallback chain) ──
APTOS_API_URLS = [
    "https://fullnode.mainnet.aptoslabs.com/v1",
    "https://aptos-mainnet.pontem.network/v1",
]

# USDC coin type on Aptos
APTOS_USDC_TYPE = "0xbae207659db88bea0cbead6da0ed00aac12edcdda169e591cd41c94180b46f3b::usdc::USDC"
APTOS_CHAIN_ID = "aptos-mainnet"

# ── Rate limiting ──
_last_request_time = 0.0
_MIN_REQUEST_INTERVAL = 0.25  # 250ms between requests


async def _rate_limited_get(url: str, timeout: float = 20) -> httpx.Response:
    """GET with basic rate limiting to avoid API throttling."""
    global _last_request_time
    now = time.monotonic()
    elapsed = now - _last_request_time
    if elapsed < _MIN_REQUEST_INTERVAL:
        await asyncio.sleep(_MIN_REQUEST_INTERVAL - elapsed)
    _last_request_time = time.monotonic()
    client = get_http_client()
    return await client.get(url, timeout=timeout)


async def _aptos_api_call(path: str, api_url: str = "", timeout: float = 20) -> dict:
    """Execute an Aptos REST API GET call with fallback."""
    urls = [api_url] if api_url else APTOS_API_URLS

    last_error = None
    for url in urls:
        try:
            full_url = f"{url}{path}" if path.startswith("/") else f"{url}/{path}"
            resp = await _rate_limited_get(full_url, timeout=timeout)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 404:
                return {"error": "Not found", "status_code": 404}
            last_error = f"HTTP {resp.status_code}"
        except Exception as e:
            last_error = str(e)
            continue

    return {"error": last_error or "All API endpoints failed"}


async def verify_aptos_transaction(
    tx_hash: str,
    expected_dest: str = "",
    expected_amount: float = 0,
) -> dict:
    """
    Verifie une transaction Aptos via REST API.

    Args:
        tx_hash: Hash de la transaction Aptos
        expected_dest: Adresse destinataire attendue (optionnel)
        expected_amount: Montant minimum attendu (optionnel)

    Returns:
        dict avec verified=True/False + details
    """
    if not tx_hash or len(tx_hash) < 10:
        return {"verified": False, "error": "Invalid transaction hash"}

    try:
        data = await _aptos_api_call(
            f"/transactions/by_hash/{tx_hash}",
            timeout=20,
        )

        if "error" in data:
            return {"verified": False, "error": data["error"]}

        # ── Check execution success ──
        success = data.get("success", False)
        if not success:
            vm_status = data.get("vm_status", "unknown")
            return {
                "verified": False,
                "error": f"Transaction failed with vm_status: {vm_status}",
            }

        # ── Extract sender ──
        sender = data.get("sender", "")

        # ── Parse events for transfers ──
        events = data.get("events", [])
        receiver = ""
        amount = 0.0
        currency = "APT"

        for event in events:
            event_type = event.get("type", "")

            # Detect deposit events (coin received)
            if "0x1::coin::DepositEvent" in event_type or "0x1::coin::deposit" in event_type:
                event_data = event.get("data", {})
                event_amount = int(event_data.get("amount", 0))

                # Extract receiver from the event guid or account
                guid = event.get("guid", {})
                account_address = guid.get("account_address", "")
                if account_address:
                    receiver = account_address

                # Detect coin type from the event type string
                if "AptosCoin" in event_type or "aptos_coin" in event_type:
                    # APT has 8 decimals
                    amount = event_amount / 1e8
                    currency = "APT"
                elif "usdc" in event_type.lower() or APTOS_USDC_TYPE in event_type:
                    # USDC has 6 decimals
                    amount = event_amount / 1e6
                    currency = "USDC"
                else:
                    # Default to 8 decimals (APT standard)
                    amount = event_amount / 1e8
                    currency = "APT"

            # Also check CoinDeposit events (newer Aptos versions)
            if "0x1::coin::CoinDeposit" in event_type:
                event_data = event.get("data", {})
                event_amount = int(event_data.get("amount", 0))
                account = event_data.get("account", "")
                coin_type = event_data.get("coin_type", "")

                if account:
                    receiver = account

                if "AptosCoin" in coin_type:
                    amount = event_amount / 1e8
                    currency = "APT"
                elif "usdc" in coin_type.lower():
                    amount = event_amount / 1e6
                    currency = "USDC"

        # ── Validate destination ──
        if expected_dest and receiver.lower() != expected_dest.lower():
            return {
                "verified": False,
                "error": f"Wrong recipient: expected {expected_dest}, got {receiver}",
            }

        # ── Validate amount (1% tolerance) ──
        if expected_amount > 0 and amount < expected_amount * 0.99:
            return {
                "verified": False,
                "error": f"Insufficient amount: expected {expected_amount}, got {amount} {currency}",
            }

        logger.info(
            f"Aptos tx verified: {tx_hash[:16]}... {sender[:10]}.. -> {receiver[:10]}.. = {amount} {currency}"
        )

        return {
            "verified": True,
            "tx_hash": tx_hash,
            "sender": sender,
            "receiver": receiver,
            "amount": amount,
            "currency": currency,
            "chain": APTOS_CHAIN_ID,
            "version": data.get("version", ""),
            "gas_used": data.get("gas_used", ""),
        }

    except httpx.TimeoutException:
        logger.error(f"Aptos verification timeout for {tx_hash[:16]}...")
        return {"verified": False, "error": "Aptos API timeout"}
    except Exception as e:
        result = safe_error(e, "aptos_verify_tx")
        result["verified"] = False
        return result


async def get_aptos_balance(address: str) -> dict:
    """
    Recupere le solde APT natif d une adresse.

    Args:
        address: Adresse Aptos (0x...)

    Returns:
        dict avec solde en APT
    """
    if not address:
        return {"address": address, "error": "Address required"}

    try:
        resource_type = "0x1::coin::CoinStore<0x1::aptos_coin::AptosCoin>"
        data = await _aptos_api_call(
            f"/accounts/{address}/resource/{resource_type}",
            timeout=15,
        )

        if "error" in data:
            return {"address": address, "error": data["error"]}

        coin_value = int(data.get("data", {}).get("coin", {}).get("value", 0))
        balance = coin_value / 1e8  # APT has 8 decimals

        return {
            "address": address,
            "apt": balance,
            "chain": APTOS_CHAIN_ID,
        }

    except Exception as e:
        result = safe_error(e, "aptos_balance")
        result["address"] = address
        return result


async def get_aptos_usdc_balance(address: str) -> dict:
    """
    Recupere le solde USDC d une adresse Aptos.

    Args:
        address: Adresse Aptos (0x...)

    Returns:
        dict avec solde USDC
    """
    if not address:
        return {"address": address, "error": "Address required"}

    try:
        resource_type = f"0x1::coin::CoinStore<{APTOS_USDC_TYPE}>"
        data = await _aptos_api_call(
            f"/accounts/{address}/resource/{resource_type}",
            timeout=15,
        )

        if "error" in data:
            # Account may not have USDC coin store registered
            return {"address": address, "usdc": 0.0, "chain": APTOS_CHAIN_ID}

        coin_value = int(data.get("data", {}).get("coin", {}).get("value", 0))
        balance = coin_value / 1e6  # USDC = 6 decimals

        return {
            "address": address,
            "usdc": balance,
            "chain": APTOS_CHAIN_ID,
            "coin_type": APTOS_USDC_TYPE,
        }

    except Exception as e:
        result = safe_error(e, "aptos_usdc_balance")
        result["address"] = address
        return result
