"""MAXIA Art.13 Extended — Ethereum Mainnet Verifier (grosses transactions uniquement)"""
import asyncio
import httpx
from config import ETH_RPC, ETH_CHAIN_ID, ETH_USDC_CONTRACT, ETH_MIN_TX_USDC


async def verify_eth_transaction(tx_hash: str, expected_to: str = None) -> dict:
    """Verify a transaction on Ethereum mainnet via eth_getTransactionReceipt."""
    payload = {
        "jsonrpc": "2.0", "id": 1,
        "method": "eth_getTransactionReceipt",
        "params": [tx_hash],
    }
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(ETH_RPC, json=payload)
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
                "network": "ethereum-mainnet",
                "chainId": ETH_CHAIN_ID,
            }
        except Exception as e:
            print(f"[EthVerifier] Attempt {attempt + 1} failed: {e}")
            await asyncio.sleep(2 ** attempt)
    return {"valid": False, "error": "Verification failed after retries"}


async def verify_usdc_transfer_eth(tx_hash: str, expected_amount_raw: int = None,
                                    expected_recipient: str = None) -> dict:
    """Verify a USDC ERC-20 Transfer event on Ethereum mainnet with recipient + amount check."""
    if not expected_recipient:
        from config import TREASURY_ADDRESS_ETH
        expected_recipient = TREASURY_ADDRESS_ETH

    # Seuil minimum pour Ethereum (gas fees elevees)
    if expected_amount_raw and expected_amount_raw < int(ETH_MIN_TX_USDC * 1e6):
        return {
            "valid": False,
            "error": f"Montant trop faible pour Ethereum mainnet (min {ETH_MIN_TX_USDC} USDC). Utilisez Solana ou Base pour les petites transactions.",
        }

    receipt = await verify_eth_transaction(tx_hash, expected_to=None)
    if not receipt.get("valid"):
        return receipt

    payload = {
        "jsonrpc": "2.0", "id": 1,
        "method": "eth_getTransactionReceipt",
        "params": [tx_hash],
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(ETH_RPC, json=payload)
            data = resp.json()
        logs = data.get("result", {}).get("logs", [])
        transfer_topic = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
        for log in logs:
            topics = log.get("topics", [])
            if (log.get("address", "").lower() == ETH_USDC_CONTRACT.lower()
                    and len(topics) >= 3
                    and topics[0] == transfer_topic):
                amount = int(log.get("data", "0x0"), 16)
                from_addr = "0x" + topics[1][-40:]
                to_addr = "0x" + topics[2][-40:]

                # Verifier le destinataire
                if expected_recipient and to_addr.lower() != expected_recipient.lower():
                    return {
                        "valid": False,
                        "error": f"Recipient mismatch: {to_addr} != {expected_recipient}",
                    }

                # Verifier le montant
                if expected_amount_raw and amount < expected_amount_raw:
                    return {
                        "valid": False,
                        "error": f"Insufficient: {amount / 1e6:.2f} USDC < {expected_amount_raw / 1e6:.2f} USDC",
                    }

                receipt["usdcTransfer"] = {
                    "from": from_addr,
                    "to": to_addr,
                    "amount_raw": amount,
                    "amount_usdc": amount / 1e6,
                }
                return receipt
        return {"valid": False, "error": "No USDC transfer found in logs"}
    except Exception as e:
        return {"valid": False, "error": str(e)}


async def x402_verify_payment_eth(payment_header: str, expected_amount_usdc: float) -> dict:
    """Verify an x402 payment on Ethereum mainnet via direct on-chain verification."""
    return await verify_usdc_transfer_eth(
        tx_hash=payment_header,
        expected_amount_raw=int(expected_amount_usdc * 1e6),
    )


async def verify_eth_value_transfer(tx_hash: str, expected_recipient: str = None,
                                     min_eth: float = None) -> dict:
    """Verify a native ETH value transfer (not ERC-20)."""
    payload = {
        "jsonrpc": "2.0", "id": 1,
        "method": "eth_getTransactionByHash",
        "params": [tx_hash],
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(ETH_RPC, json=payload)
            data = resp.json()
        tx = data.get("result")
        if not tx:
            return {"valid": False, "error": "Transaction not found"}

        value_wei = int(tx.get("value", "0x0"), 16)
        value_eth = value_wei / 1e18
        to_addr = tx.get("to", "")

        if expected_recipient and to_addr.lower() != expected_recipient.lower():
            return {"valid": False, "error": f"Recipient mismatch: {to_addr}"}

        if min_eth and value_eth < min_eth:
            return {"valid": False, "error": f"Insufficient: {value_eth:.6f} ETH < {min_eth:.6f} ETH"}

        receipt = await verify_eth_transaction(tx_hash)
        if not receipt.get("valid"):
            return receipt

        receipt["ethTransfer"] = {
            "from": tx.get("from", ""),
            "to": to_addr,
            "value_wei": value_wei,
            "value_eth": value_eth,
        }
        return receipt
    except Exception as e:
        return {"valid": False, "error": str(e)}


# ══════════════════════════════════════════
# Fonctions de scan pour SCOUT agent
# ══════════════════════════════════════════

async def get_eth_block_number() -> int:
    """Get current Ethereum block number."""
    payload = {
        "jsonrpc": "2.0", "id": 1,
        "method": "eth_blockNumber",
        "params": [],
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(ETH_RPC, json=payload)
            data = resp.json()
        return int(data.get("result", "0x0"), 16)
    except Exception:
        return 0


async def get_contract_logs(contract_address: str, from_block: str = None,
                             topic0: str = None) -> list:
    """Get event logs for a contract (used by SCOUT to find AI agent interactions)."""
    if not from_block:
        current = await get_eth_block_number()
        from_block = hex(max(0, current - 5000))  # ~last 18h

    params = {
        "address": contract_address,
        "fromBlock": from_block,
        "toBlock": "latest",
    }
    if topic0:
        params["topics"] = [topic0]
    payload = {
        "jsonrpc": "2.0", "id": 1,
        "method": "eth_getLogs",
        "params": [params],
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(ETH_RPC, json=payload)
            data = resp.json()
        return data.get("result", [])
    except Exception as e:
        print(f"[EthVerifier] get_contract_logs error: {e}")
        return []


async def get_wallet_tx_count(address: str) -> int:
    """Get transaction count for a wallet (nonce = activity level)."""
    payload = {
        "jsonrpc": "2.0", "id": 1,
        "method": "eth_getTransactionCount",
        "params": [address, "latest"],
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(ETH_RPC, json=payload)
            data = resp.json()
        return int(data.get("result", "0x0"), 16)
    except Exception:
        return 0
