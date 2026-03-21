"""MAXIA V12 — SUI Blockchain Transaction Verifier

SUI est un reseau non-EVM utilisant JSON-RPC.
Verifie les transactions et soldes via les fullnodes SUI.
USDC sur SUI: 0xdba34672e30cb065b1f93e3ab55318768fd6fef66c15942c9f7cb846e2f900e7::usdc::USDC
"""

import asyncio
import logging
import time

import httpx

logger = logging.getLogger("maxia.sui_verifier")

# ── RPC endpoints (fallback chain) ──
SUI_RPC_URLS = [
    "https://fullnode.mainnet.sui.io:443",
    "https://sui-mainnet.public.blastapi.io",
]

# USDC coin type on SUI
SUI_USDC_TYPE = "0xdba34672e30cb065b1f93e3ab55318768fd6fef66c15942c9f7cb846e2f900e7::usdc::USDC"
SUI_NATIVE_TYPE = "0x2::sui::SUI"
SUI_CHAIN_ID = "sui-mainnet"

# ── Rate limiting ──
_last_request_time = 0.0
_MIN_REQUEST_INTERVAL = 0.25  # 250ms between requests


async def _rate_limited_post(client: httpx.AsyncClient, url: str, payload: dict) -> httpx.Response:
    """POST with basic rate limiting to avoid RPC throttling."""
    global _last_request_time
    now = time.monotonic()
    elapsed = now - _last_request_time
    if elapsed < _MIN_REQUEST_INTERVAL:
        await asyncio.sleep(_MIN_REQUEST_INTERVAL - elapsed)
    _last_request_time = time.monotonic()
    return await client.post(url, json=payload)


async def _sui_rpc_call(client: httpx.AsyncClient, method: str, params: list, rpc_url: str = "") -> dict:
    """Execute a SUI JSON-RPC call with fallback."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params,
    }

    urls = [rpc_url] if rpc_url else SUI_RPC_URLS

    last_error = None
    for url in urls:
        try:
            resp = await _rate_limited_post(client, url, payload)
            if resp.status_code == 200:
                data = resp.json()
                if "error" in data:
                    last_error = data["error"].get("message", str(data["error"]))
                    continue
                return data
            last_error = f"HTTP {resp.status_code}"
        except Exception as e:
            last_error = str(e)
            continue

    return {"error": last_error or "All RPC endpoints failed"}


async def verify_sui_transaction(
    tx_digest: str,
    expected_dest: str = "",
    expected_amount: float = 0,
) -> dict:
    """
    Verifie une transaction SUI via JSON-RPC.

    Args:
        tx_digest: Digest de la transaction SUI (base58)
        expected_dest: Adresse destinataire attendue (optionnel)
        expected_amount: Montant minimum attendu en SUI (optionnel)

    Returns:
        dict avec verified=True/False + details
    """
    if not tx_digest or len(tx_digest) < 10:
        return {"verified": False, "error": "Invalid transaction digest"}

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            data = await _sui_rpc_call(
                client,
                "sui_getTransactionBlock",
                [tx_digest, {
                    "showEffects": True,
                    "showInput": True,
                    "showBalanceChanges": True,
                }],
            )

            if "error" in data and isinstance(data["error"], str):
                return {"verified": False, "error": data["error"]}

            result = data.get("result")
            if not result:
                return {"verified": False, "error": "Transaction not found on SUI"}

            # ── Check execution status ──
            effects = result.get("effects", {})
            status = effects.get("status", {}).get("status", "")
            if status != "success":
                return {
                    "verified": False,
                    "error": f"Transaction failed with status: {status}",
                }

            # ── Extract sender ──
            sender = result.get("transaction", {}).get("data", {}).get("sender", "")

            # ── Extract balance changes ──
            changes = result.get("balanceChanges", [])
            receiver = ""
            amount = 0.0
            currency = "SUI"

            for change in changes:
                change_amount = int(change.get("amount", 0))
                if change_amount > 0:
                    # Positive balance change = receiver
                    owner = change.get("owner", {})
                    if isinstance(owner, dict):
                        receiver = owner.get("AddressOwner", "")
                    elif isinstance(owner, str):
                        receiver = owner

                    coin_type = change.get("coinType", "")
                    if SUI_USDC_TYPE in coin_type:
                        # USDC has 6 decimals on SUI
                        amount = change_amount / 1e6
                        currency = "USDC"
                    else:
                        # SUI has 9 decimals (MIST to SUI)
                        amount = change_amount / 1e9
                        currency = "SUI"

            # ── Validate destination ──
            if expected_dest and receiver != expected_dest:
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

            logger.info(f"SUI tx verified: {tx_digest[:16]}... {sender[:10]}.. -> {receiver[:10]}.. = {amount} {currency}")

            return {
                "verified": True,
                "tx_digest": tx_digest,
                "sender": sender,
                "receiver": receiver,
                "amount": amount,
                "currency": currency,
                "chain": SUI_CHAIN_ID,
                "gas_used": effects.get("gasUsed", {}),
            }

    except httpx.TimeoutException:
        logger.error(f"SUI verification timeout for {tx_digest[:16]}...")
        return {"verified": False, "error": "SUI RPC timeout"}
    except Exception as e:
        logger.error(f"SUI verification error: {e}")
        return {"verified": False, "error": str(e)}


async def verify_usdc_transfer_sui(
    tx_digest: str,
    expected_dest: str = "",
    min_amount: float = 0,
) -> dict:
    """
    Verifie specifiquement un transfert USDC sur SUI.

    Args:
        tx_digest: Digest de la transaction
        expected_dest: Adresse destinataire attendue
        min_amount: Montant minimum en USDC

    Returns:
        dict avec verified=True/False
    """
    if not tx_digest or len(tx_digest) < 10:
        return {"verified": False, "error": "Invalid transaction digest"}

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            data = await _sui_rpc_call(
                client,
                "sui_getTransactionBlock",
                [tx_digest, {
                    "showEffects": True,
                    "showInput": True,
                    "showBalanceChanges": True,
                }],
            )

            if "error" in data and isinstance(data["error"], str):
                return {"verified": False, "error": data["error"]}

            result = data.get("result")
            if not result:
                return {"verified": False, "error": "Transaction not found"}

            # Check status
            status = result.get("effects", {}).get("status", {}).get("status", "")
            if status != "success":
                return {"verified": False, "error": f"Transaction failed: {status}"}

            # Find USDC balance changes
            changes = result.get("balanceChanges", [])
            usdc_found = False
            receiver = ""
            usdc_amount = 0.0

            for change in changes:
                coin_type = change.get("coinType", "")
                change_amount = int(change.get("amount", 0))

                if SUI_USDC_TYPE in coin_type and change_amount > 0:
                    usdc_found = True
                    usdc_amount = change_amount / 1e6  # USDC = 6 decimals
                    owner = change.get("owner", {})
                    if isinstance(owner, dict):
                        receiver = owner.get("AddressOwner", "")
                    elif isinstance(owner, str):
                        receiver = owner

            if not usdc_found:
                return {"verified": False, "error": "No USDC transfer found in transaction"}

            if expected_dest and receiver != expected_dest:
                return {
                    "verified": False,
                    "error": f"Wrong USDC recipient: expected {expected_dest}, got {receiver}",
                }

            if min_amount > 0 and usdc_amount < min_amount * 0.99:
                return {
                    "verified": False,
                    "error": f"Insufficient USDC: expected {min_amount}, got {usdc_amount}",
                }

            return {
                "verified": True,
                "tx_digest": tx_digest,
                "receiver": receiver,
                "amount": usdc_amount,
                "currency": "USDC",
                "chain": SUI_CHAIN_ID,
                "coin_type": SUI_USDC_TYPE,
            }

    except Exception as e:
        logger.error(f"SUI USDC verification error: {e}")
        return {"verified": False, "error": str(e)}


async def get_sui_balance(address: str) -> dict:
    """
    Recupere le solde SUI natif d'une adresse.

    Args:
        address: Adresse SUI (0x...)

    Returns:
        dict avec solde en SUI
    """
    if not address:
        return {"address": address, "error": "Address required"}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            data = await _sui_rpc_call(
                client,
                "suix_getBalance",
                [address, SUI_NATIVE_TYPE],
            )

            if "error" in data and isinstance(data["error"], str):
                return {"address": address, "error": data["error"]}

            result = data.get("result", {})
            total_balance = int(result.get("totalBalance", 0))
            balance = total_balance / 1e9  # MIST to SUI

            return {
                "address": address,
                "sui": balance,
                "chain": SUI_CHAIN_ID,
                "coin_object_count": result.get("coinObjectCount", 0),
            }

    except Exception as e:
        logger.error(f"SUI balance error for {address}: {e}")
        return {"address": address, "error": str(e)}


async def get_sui_usdc_balance(address: str) -> dict:
    """
    Recupere le solde USDC d'une adresse SUI.
    """
    if not address:
        return {"address": address, "error": "Address required"}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            data = await _sui_rpc_call(
                client,
                "suix_getBalance",
                [address, SUI_USDC_TYPE],
            )

            if "error" in data and isinstance(data["error"], str):
                return {"address": address, "usdc": 0.0, "chain": SUI_CHAIN_ID}

            result = data.get("result", {})
            total_balance = int(result.get("totalBalance", 0))
            balance = total_balance / 1e6  # USDC = 6 decimals

            return {
                "address": address,
                "usdc": balance,
                "chain": SUI_CHAIN_ID,
                "coin_type": SUI_USDC_TYPE,
            }

    except Exception as e:
        logger.error(f"SUI USDC balance error for {address}: {e}")
        return {"address": address, "error": str(e)}
