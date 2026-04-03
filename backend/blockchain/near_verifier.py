"""MAXIA V12 — NEAR Protocol Transaction Verifier
NEAR est un reseau non-EVM avec smart contracts en Rust/JS.
Utilise JSON-RPC pour verifier les transactions et soldes.
"""

import asyncio
import base64
import json
import logging
import time

import httpx
from core.http_client import get_http_client

logger = logging.getLogger("maxia.near_verifier")

# ── RPC endpoints (fallback chain) ──
NEAR_RPC_URLS = [
    "https://rpc.mainnet.near.org",
    "https://near.lava.build",
    "https://rpc.fastnear.com",
]

# USDC contract on NEAR (bridged via Rainbow Bridge)
NEAR_USDC_CONTRACT = "17208628f84f5d6ad33f0da3bbbeb27ffcb398eac501a31bd6ad2011e36133a1"
NEAR_CHAIN_ID = "near-mainnet"

# ── Rate limiting ──
_last_request_time = 0.0
_MIN_REQUEST_INTERVAL = 0.25  # 250ms between requests


async def _rate_limited_post(url: str, payload: dict, timeout: float = 20) -> httpx.Response:
    """POST with basic rate limiting to avoid RPC throttling."""
    global _last_request_time
    now = time.monotonic()
    elapsed = now - _last_request_time
    if elapsed < _MIN_REQUEST_INTERVAL:
        await asyncio.sleep(_MIN_REQUEST_INTERVAL - elapsed)
    _last_request_time = time.monotonic()
    client = get_http_client()
    return await client.post(url, json=payload, timeout=timeout)


async def _near_rpc_call(method: str, params, rpc_url: str = "", timeout: float = 20) -> dict:
    """Execute a NEAR JSON-RPC call with fallback. params can be list or dict."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params,
    }

    urls = [rpc_url] if rpc_url else NEAR_RPC_URLS

    last_error = None
    for url in urls:
        try:
            resp = await _rate_limited_post(url, payload, timeout=timeout)
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


async def verify_near_transaction(
    tx_hash: str,
    sender_id: str = "",
    expected_dest: str = "",
    expected_amount: float = 0,
) -> dict:
    """
    Verifie une transaction NEAR via JSON-RPC.

    Args:
        tx_hash: Hash de la transaction NEAR
        sender_id: Account ID de l expediteur (requis par NEAR RPC)
        expected_dest: Account ID destinataire attendu (optionnel)
        expected_amount: Montant minimum attendu en NEAR (optionnel)

    Returns:
        dict avec verified=True/False + details
    """
    if not tx_hash or len(tx_hash) < 10:
        return {"verified": False, "error": "Invalid transaction hash"}

    try:
        data = await _near_rpc_call(
            "tx",
            [tx_hash, sender_id, True],
            timeout=20,
        )

        if "error" in data and isinstance(data["error"], str):
            return {"verified": False, "error": data["error"]}

        result = data.get("result")
        if not result:
            return {"verified": False, "error": "Transaction not found on NEAR"}

        # ── Check execution status ──
        status = result.get("status", {})
        success = False
        if isinstance(status, dict):
            if "SuccessValue" in status or "SuccessReceiptId" in status:
                success = True
        if not success:
            return {
                "verified": False,
                "error": f"Transaction failed with status: {status}",
            }

        # ── Extract sender/receiver from transaction ──
        transaction = result.get("transaction", {})
        sender = transaction.get("signer_id", sender_id)
        receiver = transaction.get("receiver_id", "")
        amount = 0.0
        currency = "NEAR"

        # ── Parse actions for native NEAR transfers ──
        actions = transaction.get("actions", [])
        for action in actions:
            if isinstance(action, dict) and "Transfer" in action:
                deposit = int(action["Transfer"].get("deposit", 0))
                if deposit > 0:
                    # NEAR has 24 decimals (yoctoNEAR)
                    amount = deposit / 1e24
                    currency = "NEAR"

            # ── Parse ft_transfer function calls ──
            if isinstance(action, dict) and "FunctionCall" in action:
                fc = action["FunctionCall"]
                method_name = fc.get("method_name", "")
                if method_name == "ft_transfer":
                    try:
                        args_b64 = fc.get("args", "")
                        args_bytes = base64.b64decode(args_b64)
                        args = json.loads(args_bytes)
                        receiver = args.get("receiver_id", receiver)
                        ft_amount = int(args.get("amount", 0))
                        # Detect if USDC (6 decimals) based on receiver contract
                        if transaction.get("receiver_id", "") == NEAR_USDC_CONTRACT:
                            amount = ft_amount / 1e6
                            currency = "USDC"
                        else:
                            amount = ft_amount / 1e24
                            currency = "TOKEN"
                    except Exception as e:
                        logger.warning(f"Failed to decode ft_transfer args: {e}")

        # ── Parse receipt logs for NEP-141 EVENT_JSON events ──
        receipts_outcome = result.get("receipts_outcome", [])
        for receipt_outcome in receipts_outcome:
            outcome = receipt_outcome.get("outcome", {})
            logs = outcome.get("logs", [])
            for log_entry in logs:
                if "EVENT_JSON" in log_entry:
                    try:
                        event_str = log_entry.split("EVENT_JSON:", 1)[1].strip()
                        event = json.loads(event_str)
                        if event.get("standard") == "nep141" and event.get("event") == "ft_transfer":
                            event_data = event.get("data", [{}])
                            if event_data:
                                item = event_data[0]
                                receiver = item.get("new_owner_id", receiver)
                                ft_amount = int(item.get("amount", 0))
                                amount = ft_amount / 1e6
                                currency = "USDC"
                    except Exception as e:
                        logger.warning(f"Failed to parse EVENT_JSON log: {e}")

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

        logger.info(
            f"NEAR tx verified: {tx_hash[:16]}... {sender[:10]}.. -> {receiver[:10]}.. = {amount} {currency}"
        )

        return {
            "verified": True,
            "tx_hash": tx_hash,
            "sender": sender,
            "receiver": receiver,
            "amount": amount,
            "currency": currency,
            "chain": NEAR_CHAIN_ID,
        }

    except httpx.TimeoutException:
        logger.error(f"NEAR verification timeout for {tx_hash[:16]}...")
        return {"verified": False, "error": "NEAR RPC timeout"}
    except Exception as e:
        logger.error(f"NEAR verification error: {e}")
        return {"verified": False, "error": "An error occurred"}


async def get_near_balance(account_id: str) -> dict:
    """
    Recupere le solde NEAR natif d un compte.

    Args:
        account_id: NEAR account ID (e.g. "alice.near")

    Returns:
        dict avec solde en NEAR
    """
    if not account_id:
        return {"account_id": account_id, "error": "Account ID required"}

    try:
        data = await _near_rpc_call(
            "query",
            {
                "request_type": "view_account",
                "finality": "final",
                "account_id": account_id,
            },
            timeout=15,
        )

        if "error" in data and isinstance(data["error"], str):
            return {"account_id": account_id, "error": data["error"]}

        result = data.get("result", {})
        amount_yocto = int(result.get("amount", 0))
        balance = amount_yocto / 1e24  # yoctoNEAR to NEAR

        return {
            "account_id": account_id,
            "near": balance,
            "chain": NEAR_CHAIN_ID,
            "storage_usage": result.get("storage_usage", 0),
        }

    except Exception as e:
        logger.error(f"NEAR balance error for {account_id}: {e}")
        return {"account_id": account_id, "error": "An error occurred"}


async def get_near_usdc_balance(account_id: str) -> dict:
    """
    Recupere le solde USDC d un compte NEAR via ft_balance_of.

    Args:
        account_id: NEAR account ID

    Returns:
        dict avec solde USDC
    """
    if not account_id:
        return {"account_id": account_id, "error": "Account ID required"}

    try:
        args_json = json.dumps({"account_id": account_id})
        args_base64 = base64.b64encode(args_json.encode()).decode()

        data = await _near_rpc_call(
            "query",
            {
                "request_type": "call_function",
                "finality": "final",
                "account_id": NEAR_USDC_CONTRACT,
                "method_name": "ft_balance_of",
                "args_base64": args_base64,
            },
            timeout=15,
        )

        if "error" in data and isinstance(data["error"], str):
            return {"account_id": account_id, "usdc": 0.0, "chain": NEAR_CHAIN_ID}

        result = data.get("result", {})
        result_bytes = result.get("result", [])
        if result_bytes:
            # Result is a list of byte values, decode to string
            balance_str = bytes(result_bytes).decode("utf-8").strip('"')
            balance = int(balance_str) / 1e6  # USDC = 6 decimals
        else:
            balance = 0.0

        return {
            "account_id": account_id,
            "usdc": balance,
            "chain": NEAR_CHAIN_ID,
            "contract": NEAR_USDC_CONTRACT,
        }

    except Exception as e:
        logger.error(f"NEAR USDC balance error for {account_id}: {e}")
        return {"account_id": account_id, "error": "An error occurred"}
