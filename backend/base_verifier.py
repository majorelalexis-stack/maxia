"""MAXIA Art.13 — Base (Coinbase L2) Verifier & x402 EVM Support"""
import os, asyncio
import httpx
from config import BASE_RPC, BASE_CHAIN_ID, BASE_USDC_CONTRACT, X402_FACILITATOR_URL


async def verify_base_transaction(tx_hash: str, expected_to: str = None) -> dict:
    """Verify a transaction on Base L2 via eth_getTransactionReceipt."""
    payload = {
        "jsonrpc": "2.0", "id": 1,
        "method": "eth_getTransactionReceipt",
        "params": [tx_hash],
    }
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(BASE_RPC, json=payload)
                data = resp.json()
            result = data.get("result")
            if not result:
                await asyncio.sleep(2 ** attempt)
                continue
            if result.get("status") != "0x1":
                return {"valid": False, "error": "Transaction reverted"}
            if expected_to and result.get("to", "").lower() != expected_to.lower():
                return {"valid": False, "error": "Recipient mismatch"}
            return {
                "valid": True,
                "blockNumber": int(result.get("blockNumber", "0x0"), 16),
                "from": result.get("from", ""),
                "to": result.get("to", ""),
                "gasUsed": int(result.get("gasUsed", "0x0"), 16),
                "network": "base-mainnet",
                "chainId": BASE_CHAIN_ID,
            }
        except Exception as e:
            print(f"[BaseVerifier] Attempt {attempt + 1} failed: {e}")
            await asyncio.sleep(2 ** attempt)
    return {"valid": False, "error": "Verification failed after retries"}


async def verify_usdc_transfer_base(tx_hash: str, expected_amount_raw: int = None) -> dict:
    """Verify a USDC ERC-20 Transfer event on Base."""
    receipt = await verify_base_transaction(tx_hash)
    if not receipt.get("valid"):
        return receipt

    payload = {
        "jsonrpc": "2.0", "id": 1,
        "method": "eth_getTransactionReceipt",
        "params": [tx_hash],
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(BASE_RPC, json=payload)
            data = resp.json()
        logs = data.get("result", {}).get("logs", [])
        transfer_topic = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
        for log in logs:
            topics = log.get("topics", [])
            if (log.get("address", "").lower() == BASE_USDC_CONTRACT.lower()
                    and len(topics) >= 3
                    and topics[0] == transfer_topic):
                amount = int(log.get("data", "0x0"), 16)
                from_addr = "0x" + topics[1][-40:]
                to_addr = "0x" + topics[2][-40:]
                receipt["usdcTransfer"] = {
                    "from": from_addr,
                    "to": to_addr,
                    "amount_raw": amount,
                    "amount_usdc": amount / 1e6,
                }
                if expected_amount_raw and amount < expected_amount_raw:
                    return {"valid": False, "error": f"Insufficient: {amount} < {expected_amount_raw}"}
                return receipt
        return {"valid": False, "error": "No USDC transfer found in logs"}
    except Exception as e:
        return {"valid": False, "error": str(e)}


async def x402_verify_payment_base(payment_header: str, expected_amount_usdc: float) -> dict:
    """Verify an x402 payment on Base via the Coinbase facilitator."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{X402_FACILITATOR_URL}/verify",
                json={
                    "paymentPayload": payment_header,
                    "network": "base-mainnet",
                    "expectedAmount": str(int(expected_amount_usdc * 1e6)),
                },
            )
            result = resp.json()
        if resp.status_code == 200 and result.get("valid"):
            return {
                "valid": True,
                "txHash": result.get("txHash", ""),
                "network": "base-mainnet",
                "settledAmount": result.get("settledAmount"),
            }
        return {"valid": False, "error": result.get("error", "Facilitator rejected")}
    except Exception as e:
        return {"valid": False, "error": f"Facilitator error: {e}"}


def build_x402_challenge_base(path: str, price_usdc: float, pay_to: str) -> dict:
    """Build an x402 402-response payload for Base network."""
    return {
        "scheme": "exact",
        "network": "base-mainnet",
        "maxAmountRequired": str(int(price_usdc * 1e6)),
        "resource": path,
        "description": f"MAXIA service: {path}",
        "mimeType": "application/json",
        "payTo": pay_to,
        "asset": BASE_USDC_CONTRACT,
        "maxTimeoutSeconds": 60,
        "extra": {"chainId": BASE_CHAIN_ID, "facilitator": X402_FACILITATOR_URL},
    }
