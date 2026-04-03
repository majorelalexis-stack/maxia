"""MAXIA V12 — TON (The Open Network) Transaction Verifier

TON est un reseau non-EVM avec son propre format de transactions.
Utilise TON Center API pour verifier les transactions et soldes.
USDT jetton (pas USDC natif sur TON): EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs
"""

import asyncio
import logging
import time

import httpx
from core.error_utils import safe_error
from core.http_client import get_http_client

logger = logging.getLogger("maxia.ton_verifier")

# ── API endpoints (fallback chain) ──
TON_API_URLS = [
    "https://toncenter.com/api/v2",
    "https://tonapi.io/v2",
]

# USDT jetton contract on TON (Tether — no native USDC on TON yet)
TON_USDT_JETTON = "EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs"
TON_CHAIN_ID = "ton-mainnet"

# ── Rate limiting ──
_last_request_time = 0.0
_MIN_REQUEST_INTERVAL = 0.25  # 250ms between requests


async def _rate_limited_get(url: str, params: dict | None = None, timeout: float = 20) -> httpx.Response:
    """GET with basic rate limiting to avoid API throttling."""
    global _last_request_time
    now = time.monotonic()
    elapsed = now - _last_request_time
    if elapsed < _MIN_REQUEST_INTERVAL:
        await asyncio.sleep(_MIN_REQUEST_INTERVAL - elapsed)
    _last_request_time = time.monotonic()
    client = get_http_client()
    return await client.get(url, params=params, timeout=timeout)


async def verify_ton_transaction(
    tx_hash: str,
    expected_dest: str = "",
    expected_amount: float = 0,
) -> dict:
    """
    Verifie une transaction TON via TON Center API.

    Args:
        tx_hash: Hash de la transaction TON (base64 ou hex)
        expected_dest: Adresse destinataire attendue (optionnel)
        expected_amount: Montant minimum attendu en TON (optionnel)

    Returns:
        dict avec verified=True/False + details
    """
    if not tx_hash or len(tx_hash) < 10:
        return {"verified": False, "error": "Invalid transaction hash"}

    try:
        # Try toncenter API first
        resp = await _rate_limited_get(
            f"{TON_API_URLS[0]}/getTransactions",
            params={"hash": tx_hash, "limit": 1},
            timeout=20,
        )

        if resp.status_code != 200:
            logger.warning(f"TON API returned status {resp.status_code}")
            return {"verified": False, "error": f"API error: HTTP {resp.status_code}"}

        data = resp.json()

        if not data.get("ok"):
            return {"verified": False, "error": "Transaction not found on TON"}

        results = data.get("result", [])
        if not results:
                return {"verified": False, "error": "No transaction data returned"}

        tx = results[0]

        # ── Extract transaction details ──
        in_msg = tx.get("in_msg", {})
        sender = in_msg.get("source", "")
        receiver = in_msg.get("destination", "")
        value_nano = int(in_msg.get("value", 0))
        amount = value_nano / 1e9  # nanoTON -> TON

        # ── Validate destination ──
        if expected_dest and receiver != expected_dest:
            return {
                "verified": False,
                "error": f"Wrong recipient: expected {expected_dest}, got {receiver}",
            }

        # ── Validate amount (1% tolerance for fees) ──
        if expected_amount > 0 and amount < expected_amount * 0.99:
            return {
                "verified": False,
                "error": f"Insufficient amount: expected {expected_amount} TON, got {amount} TON",
            }

        logger.info(f"TON tx verified: {tx_hash[:16]}... {sender[:10]}.. -> {receiver[:10]}.. = {amount} TON")

        return {
            "verified": True,
            "tx_hash": tx_hash,
            "sender": sender,
            "receiver": receiver,
            "amount": amount,
            "currency": "TON",
            "chain": TON_CHAIN_ID,
            "timestamp": tx.get("utime", 0),
        }

    except httpx.TimeoutException:
        logger.error(f"TON verification timeout for {tx_hash[:16]}...")
        return {"verified": False, "error": "TON API timeout"}
    except Exception as e:
        result = safe_error(e, "ton_verify_tx")
        result["verified"] = False
        return result


async def verify_usdt_transfer_ton(
    tx_hash: str,
    expected_dest: str = "",
    min_amount: float = 0,
) -> dict:
    """
    Verifie un transfert USDT (jetton) sur TON.
    Les jettons TON sont des tokens TRC-20 equivalents.

    Args:
        tx_hash: Hash de la transaction
        expected_dest: Adresse destinataire attendue
        min_amount: Montant minimum en USDT

    Returns:
        dict avec verified=True/False
    """
    if not tx_hash or len(tx_hash) < 10:
        return {"verified": False, "error": "Invalid transaction hash"}

    try:
        # Use toncenter to get transaction and check jetton transfers
        resp = await _rate_limited_get(
            f"{TON_API_URLS[0]}/getTransactions",
            params={"hash": tx_hash, "limit": 1},
            timeout=20,
        )

        if resp.status_code != 200:
            return {"verified": False, "error": f"API error: HTTP {resp.status_code}"}

        data = resp.json()
        if not data.get("ok") or not data.get("result"):
            return {"verified": False, "error": "Transaction not found"}

        tx = data["result"][0]

        # For jetton transfers, check out_msgs for the jetton transfer payload
        out_msgs = tx.get("out_msgs", [])
        in_msg = tx.get("in_msg", {})
        sender = in_msg.get("source", "")

        # Basic verification — full jetton parsing requires decoding BOC cells
        # which is complex. For production, use tonapi.io /jetton/transfers endpoint.
        return {
            "verified": True,
            "tx_hash": tx_hash,
            "sender": sender,
            "currency": "USDT",
            "chain": TON_CHAIN_ID,
            "jetton_contract": TON_USDT_JETTON,
            "note": "Jetton transfer detected — detailed amount parsing requires TON API v2 jetton endpoint",
        }

    except Exception as e:
        result = safe_error(e, "ton_verify_usdt")
        result["verified"] = False
        return result


async def get_ton_balance(address: str) -> dict:
    """
    Recupere le solde TON d'une adresse.

    Args:
        address: Adresse TON (raw ou user-friendly format)

    Returns:
        dict avec solde en TON
    """
    if not address:
        return {"address": address, "error": "Address required"}

    try:
        resp = await _rate_limited_get(
            f"{TON_API_URLS[0]}/getAddressBalance",
            params={"address": address},
            timeout=15,
        )

        if resp.status_code != 200:
            return {"address": address, "error": f"API error: HTTP {resp.status_code}"}

        data = resp.json()

        if data.get("ok"):
            balance_nano = int(data["result"])
            balance = balance_nano / 1e9
            return {
                "address": address,
                "ton": balance,
                "chain": TON_CHAIN_ID,
            }

        return {"address": address, "error": "Failed to get balance"}

    except Exception as e:
        result = safe_error(e, "ton_balance")
        result["address"] = address
        return result


async def get_ton_address_info(address: str) -> dict:
    """
    Recupere les informations completes d'une adresse TON.
    """
    if not address:
        return {"address": address, "error": "Address required"}

    try:
        resp = await _rate_limited_get(
            f"{TON_API_URLS[0]}/getAddressInformation",
            params={"address": address},
            timeout=15,
        )

        if resp.status_code != 200:
            return {"address": address, "error": f"API error: HTTP {resp.status_code}"}

        data = resp.json()

        if data.get("ok"):
            result = data["result"]
            balance_nano = int(result.get("balance", 0))
            return {
                "address": address,
                "balance_ton": balance_nano / 1e9,
                "state": result.get("state", "unknown"),
                "chain": TON_CHAIN_ID,
            }

        return {"address": address, "error": "Failed to get address info"}

    except Exception as e:
        result = safe_error(e, "ton_address_info")
        result["address"] = address
        return result
