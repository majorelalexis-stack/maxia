"""MAXIA V12 — TRON Blockchain Verifier (TRC-20 USDT/USDC + TRX natif)"""
import logging
import asyncio, logging, time
import httpx
from core.http_client import get_http_client

logger = logging.getLogger("maxia.tron_verifier")

TRON_API_URLS = [
    "https://api.trongrid.io",
    "https://apilist.tronscanapi.com",
]


async def verify_tron_transaction(
    tx_id: str, expected_dest: str = "", expected_amount: float = 0
) -> dict:
    """Verifie une transaction sur le reseau TRON (TRX natif ou TRC-20)."""
    try:
        client = get_http_client()
        # 1. Recuperer la transaction brute
        resp = await client.post(
            f"{TRON_API_URLS[0]}/wallet/gettransactionbyid",
            json={"value": tx_id},
            timeout=20,
        )
        data = resp.json()

        if not data or not data.get("txID"):
            return {"verified": False, "error": "Transaction not found"}

        # 2. Recuperer le receipt pour confirmation
        resp2 = await client.post(
            f"{TRON_API_URLS[0]}/wallet/gettransactioninfobyid",
            json={"value": tx_id},
            timeout=20,
        )
        info = resp2.json()

        if info.get("receipt", {}).get("result") != "SUCCESS":
            return {"verified": False, "error": "Transaction not confirmed"}

        # 3. Extraire les details du contrat
        contract = data.get("raw_data", {}).get("contract", [{}])[0]
        contract_type = contract.get("type", "")
        params = contract.get("parameter", {}).get("value", {})

        if contract_type == "TransferContract":
            # Transfert TRX natif
            sender = _hex_to_base58(params.get("owner_address", ""))
            receiver = _hex_to_base58(params.get("to_address", ""))
            amount = params.get("amount", 0) / 1e6  # SUN -> TRX
            currency = "TRX"

        elif contract_type == "TriggerSmartContract":
            # Transfert TRC-20 (USDT/USDC)
            contract_addr = _hex_to_base58(
                params.get("contract_address", "")
            )
            sender = _hex_to_base58(params.get("owner_address", ""))

            # Decoder transfer(address,uint256) depuis call_data
            call_data = params.get("data", "")
            if len(call_data) >= 136:
                receiver = _hex_to_base58("41" + call_data[32:72])
                amount = int(call_data[72:136], 16) / 1e6
            else:
                return {
                    "verified": False,
                    "error": "Cannot decode TRC-20 transfer",
                }

            # Identifier le token
            if contract_addr == "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t":
                currency = "USDT"
            elif contract_addr == "TEkxiTehnzSmSe2XqrBj4w32RUN966rdz8":
                currency = "USDC"
            else:
                currency = "TRC20"

        else:
            return {
                "verified": False,
                "error": f"Unsupported contract type: {contract_type}",
            }

        # 4. Validations
        if expected_dest and receiver != expected_dest:
            return {"verified": False, "error": "Wrong recipient"}
        if expected_amount > 0 and amount < expected_amount * 0.99:
            return {"verified": False, "error": "Insufficient amount"}

        return {
            "verified": True,
            "tx_id": tx_id,
            "sender": sender,
            "receiver": receiver,
            "amount": amount,
            "currency": currency,
        }

    except Exception as e:
        logger.error(f"TRON verification error: {e}")
        return {"verified": False, "error": "An error occurred"}


def _hex_to_base58(hex_addr: str) -> str:
    """Convertit une adresse TRON hexadecimale en base58check."""
    try:
        import base58
        import hashlib

        if hex_addr.startswith("0x"):
            hex_addr = "41" + hex_addr[2:]
        if not hex_addr.startswith("41"):
            hex_addr = "41" + hex_addr

        addr_bytes = bytes.fromhex(hex_addr)
        h1 = hashlib.sha256(addr_bytes).digest()
        h2 = hashlib.sha256(h1).digest()
        return base58.b58encode(addr_bytes + h2[:4]).decode()
    except Exception:
        return hex_addr


async def get_tron_balance(address: str) -> dict:
    """Recupere le solde TRX d'un wallet TRON."""
    try:
        client = get_http_client()
        resp = await client.post(
            f"{TRON_API_URLS[0]}/wallet/getaccount",
            json={"address": address, "visible": True},
            timeout=15,
        )
        data = resp.json()
        balance = data.get("balance", 0) / 1e6
        return {"address": address, "trx": balance}
    except Exception as e:
        return {"address": address, "error": "An error occurred"}


async def x402_verify_payment_tron(tx_id: str, expected_amount: float) -> dict:
    """Wrapper x402 pour verification de paiement TRON."""
    from core.config import TREASURY_ADDRESS_TRON

    result = await verify_tron_transaction(
        tx_id,
        expected_dest=TREASURY_ADDRESS_TRON,
        expected_amount=expected_amount,
    )
    return result
