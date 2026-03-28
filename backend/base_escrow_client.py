"""MAXIA Base Escrow Client — interact with MaxiaEscrow.sol on Base mainnet.

Contract: 0xBd31bB973183F8476d0C4cF57a92e648b130510C
Chain: Base (8453)
USDC: 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913

Functions: lockEscrow, confirmDelivery, autoRefund, openDispute, settleDispute, getStats
"""
import json
import logging
import os
import time
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════
# Config
# ══════════════════════════════════════════

BASE_RPC = os.getenv("BASE_RPC", "https://mainnet.base.org")
ESCROW_CONTRACT = os.getenv("ESCROW_CONTRACT_BASE", "0xBd31bB973183F8476d0C4cF57a92e648b130510C")
USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
CHAIN_ID = 8453

# Load ABI
_ABI_PATH = Path(__file__).parent.parent / "contracts" / "evm" / "MaxiaEscrow_abi.json"
_ABI = []
try:
    with open(_ABI_PATH) as f:
        _ABI = json.load(f)
    logger.info(f"[BaseEscrow] ABI loaded — {len(_ABI)} entries")
except FileNotFoundError:
    logger.warning(f"[BaseEscrow] ABI not found at {_ABI_PATH}")


# ══════════════════════════════════════════
# Read-only functions (no wallet needed)
# ══════════════════════════════════════════

async def _rpc_call(method: str, params: list) -> dict:
    """Raw JSON-RPC call to Base."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(BASE_RPC, json={
            "jsonrpc": "2.0", "method": method, "params": params, "id": 1,
        })
        return resp.json()


def _keccak256(data: bytes) -> bytes:
    """Compute Keccak-256 hash (NOT NIST SHA3-256 — Ethereum uses pre-NIST Keccak)."""
    # Use pysha3 if available, otherwise pycryptodome, otherwise eth_hash
    try:
        import sha3
        k = sha3.keccak_256(data)
        return k.digest()
    except ImportError:
        pass
    try:
        from Crypto.Hash import keccak
        k = keccak.new(digest_bits=256, data=data)
        return k.digest()
    except ImportError:
        pass
    try:
        from eth_hash.auto import keccak as eth_keccak
        return eth_keccak(data)
    except ImportError:
        pass
    # Last resort: use web3 if installed
    try:
        from web3 import Web3
        return Web3.keccak(data)
    except ImportError:
        raise ImportError(
            "No Keccak-256 library found. Install one of: pysha3, pycryptodome, eth-hash[pycryptodome], web3"
        )


def _encode_function_call(func_name: str, param_types: list, param_values: list) -> str:
    """Encode a Solidity function call (minimal ABI encoding)."""
    # Function selector = first 4 bytes of keccak256(signature)
    sig = f"{func_name}({','.join(param_types)})"
    selector = _keccak256(sig.encode()).hex()[:8]

    # For simple view calls with no params
    if not param_types:
        return "0x" + selector

    # Encode params (only support basic types for read calls)
    encoded = selector
    for ptype, pval in zip(param_types, param_values):
        if ptype == "address":
            encoded += pval.lower().replace("0x", "").zfill(64)
        elif ptype == "bytes32":
            encoded += pval.replace("0x", "").zfill(64)
        elif ptype == "uint256":
            encoded += hex(int(pval))[2:].zfill(64)
    return "0x" + encoded


async def get_stats() -> dict:
    """Get escrow stats: totalEscrows, totalVolume, totalCommissions."""
    try:
        # getStats() returns (uint256, uint256, uint256)
        data = _encode_function_call("getStats", [], [])
        result = await _rpc_call("eth_call", [{"to": ESCROW_CONTRACT, "data": data}, "latest"])
        hex_result = result.get("result", "0x")

        if hex_result == "0x" or len(hex_result) < 130:
            return {"total_escrows": 0, "total_volume_usdc": 0, "total_commissions_usdc": 0}

        # Decode 3 uint256 values
        clean = hex_result[2:]  # Remove 0x
        total_escrows = int(clean[0:64], 16)
        total_volume = int(clean[64:128], 16) / 1e6  # USDC has 6 decimals
        total_commissions = int(clean[128:192], 16) / 1e6

        return {
            "total_escrows": total_escrows,
            "total_volume_usdc": round(total_volume, 2),
            "total_commissions_usdc": round(total_commissions, 2),
            "contract": ESCROW_CONTRACT,
            "chain": "base",
            "chain_id": CHAIN_ID,
        }
    except Exception as e:
        logger.error(f"[BaseEscrow] getStats error: {e}")
        return {"total_escrows": 0, "total_volume_usdc": 0, "total_commissions_usdc": 0}


async def get_commission_tier(buyer_address: str) -> dict:
    """Get commission tier for a buyer based on volume."""
    try:
        data = _encode_function_call("getCommissionTier", ["address"], [buyer_address])
        result = await _rpc_call("eth_call", [{"to": ESCROW_CONTRACT, "data": data}, "latest"])
        hex_result = result.get("result", "0x")

        if hex_result == "0x" or len(hex_result) < 130:
            return {"tier": "BRONZE", "bps": 150}

        # Decode: string + uint256 (ABI encoded)
        clean = hex_result[2:]
        bps = int(clean[-64:], 16)
        tier = "WHALE" if bps <= 10 else "GOLD" if bps <= 50 else "BRONZE"
        return {"tier": tier, "bps": bps, "pct": round(bps / 100, 2)}
    except Exception as e:
        logger.error(f"[BaseEscrow] getCommissionTier error: {e}")
        return {"tier": "BRONZE", "bps": 150}


async def get_escrow(escrow_id_hex: str) -> dict:
    """Get escrow details by ID."""
    try:
        data = _encode_function_call("escrows", ["bytes32"], [escrow_id_hex])
        result = await _rpc_call("eth_call", [{"to": ESCROW_CONTRACT, "data": data}, "latest"])
        hex_result = result.get("result", "0x")

        if hex_result == "0x" or len(hex_result) < 200:
            return {"error": "Escrow not found"}

        clean = hex_result[2:]
        buyer = "0x" + clean[24:64]
        seller = "0x" + clean[88:128]
        amount = int(clean[128:192], 16) / 1e6
        commission = int(clean[192:256], 16) / 1e6
        seller_gets = int(clean[256:320], 16) / 1e6
        locked_at = int(clean[320:384], 16)
        status_int = int(clean[384:448], 16)

        status_names = ["Locked", "Confirmed", "Disputed", "Refunded", "Settled"]
        status = status_names[status_int] if status_int < len(status_names) else "Unknown"

        return {
            "escrow_id": escrow_id_hex,
            "buyer": buyer,
            "seller": seller,
            "amount_usdc": round(amount, 2),
            "commission_usdc": round(commission, 2),
            "seller_gets_usdc": round(seller_gets, 2),
            "locked_at": locked_at,
            "status": status,
            "chain": "base",
            "contract": ESCROW_CONTRACT,
        }
    except Exception as e:
        logger.error(f"[BaseEscrow] getEscrow error: {e}")
        return {"error": "Failed to fetch escrow details"}


async def verify_escrow_tx(tx_hash: str) -> dict:
    """Verify an escrow transaction on Base by checking the receipt and events."""
    try:
        result = await _rpc_call("eth_getTransactionReceipt", [tx_hash])
        receipt = result.get("result")
        if not receipt:
            return {"valid": False, "error": "Transaction not found"}

        if receipt.get("status") != "0x1":
            return {"valid": False, "error": "Transaction reverted"}

        # Check it was sent to our escrow contract
        to_addr = (receipt.get("to") or "").lower()
        if to_addr != ESCROW_CONTRACT.lower():
            return {"valid": False, "error": "Transaction not sent to escrow contract"}

        # Parse logs for EscrowCreated event
        logs = receipt.get("logs", [])
        escrow_id = None
        for log in logs:
            if len(log.get("topics", [])) >= 3:
                escrow_id = log["topics"][1] if log["topics"][1] != "0x" + "0" * 64 else None

        return {
            "valid": True,
            "tx_hash": tx_hash,
            "block": int(receipt.get("blockNumber", "0x0"), 16),
            "gas_used": int(receipt.get("gasUsed", "0x0"), 16),
            "escrow_id": escrow_id,
            "contract": ESCROW_CONTRACT,
            "chain": "base",
        }
    except Exception as e:
        logger.error(f"[BaseEscrow] verify_tx error: {e}")
        return {"valid": False, "error": "Failed to verify escrow transaction"}


# ══════════════════════════════════════════
# Contract info
# ══════════════════════════════════════════

def get_contract_info() -> dict:
    """Return contract info for frontend/API."""
    return {
        "address": ESCROW_CONTRACT,
        "chain": "base",
        "chain_id": CHAIN_ID,
        "usdc": USDC_BASE,
        "explorer": f"https://basescan.org/address/{ESCROW_CONTRACT}",
        "abi_available": len(_ABI) > 0,
        "functions": [
            "lockEscrow(seller, amount, serviceId)",
            "confirmDelivery(escrowId)",
            "autoRefund(escrowId)",
            "openDispute(escrowId)",
            "settleDispute(escrowId, winner)",
        ],
    }


print(f"[BaseEscrow] Contract {ESCROW_CONTRACT[:10]}...{ESCROW_CONTRACT[-6:]} on Base (chain {CHAIN_ID})")
