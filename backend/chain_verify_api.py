"""MAXIA Chain Verification API — EVM chain info + verify endpoints.

Extracted from main.py (Session 33 refactoring).
Uses unified EvmVerifier instances for all 6 EVM chains.
"""
import os
from fastapi import APIRouter, Request
from pydantic import BaseModel
from typing import Optional

from evm_verifier_base import EvmVerifier
from security import check_rate_limit
from config import (
    BASE_RPC, BASE_CHAIN_ID, BASE_USDC_CONTRACT, BASE_MIN_TX_USDC,
    TREASURY_ADDRESS_BASE,
    ETH_RPC, ETH_CHAIN_ID, ETH_USDC_CONTRACT, TREASURY_ADDRESS_ETH,
    POLYGON_RPC, POLYGON_CHAIN_ID, POLYGON_USDC_CONTRACT, TREASURY_ADDRESS_POLYGON,
    ARBITRUM_RPC, ARBITRUM_CHAIN_ID, ARBITRUM_USDC_CONTRACT, TREASURY_ADDRESS_ARBITRUM,
    AVALANCHE_RPC, AVALANCHE_CHAIN_ID, AVALANCHE_USDC_CONTRACT, TREASURY_ADDRESS_AVALANCHE,
    BNB_RPC, BNB_CHAIN_ID, BNB_USDC_CONTRACT, TREASURY_ADDRESS_BNB,
    SEI_RPC, SEI_CHAIN_ID, SEI_USDC_CONTRACT, TREASURY_ADDRESS_SEI,
)

router = APIRouter(tags=["chain-verify"])


# ── Unified EVM verifier instances ──

evm_verifiers = {
    "base": EvmVerifier("Base", BASE_CHAIN_ID, "base-mainnet",
        [os.getenv("BASE_RPC", "https://mainnet.base.org"), "https://base.llamarpc.com"],
        BASE_USDC_CONTRACT, TREASURY_ADDRESS_BASE, BASE_MIN_TX_USDC),
    "ethereum": EvmVerifier("Ethereum", ETH_CHAIN_ID, "ethereum-mainnet",
        [os.getenv("ETH_RPC", "https://eth.llamarpc.com"), "https://eth.drpc.org"],
        ETH_USDC_CONTRACT, TREASURY_ADDRESS_ETH, 1.0),
    "polygon": EvmVerifier("Polygon", POLYGON_CHAIN_ID, "polygon-mainnet",
        [os.getenv("POLYGON_RPC", "https://polygon-rpc.com"), "https://polygon.llamarpc.com"],
        POLYGON_USDC_CONTRACT, TREASURY_ADDRESS_POLYGON),
    "arbitrum": EvmVerifier("Arbitrum", ARBITRUM_CHAIN_ID, "arbitrum-mainnet",
        [os.getenv("ARBITRUM_RPC", "https://arb1.arbitrum.io/rpc"), "https://arbitrum.llamarpc.com"],
        ARBITRUM_USDC_CONTRACT, TREASURY_ADDRESS_ARBITRUM),
    "avalanche": EvmVerifier("Avalanche", AVALANCHE_CHAIN_ID, "avalanche-mainnet",
        [os.getenv("AVALANCHE_RPC", "https://api.avax.network/ext/bc/C/rpc"), "https://avalanche.drpc.org"],
        AVALANCHE_USDC_CONTRACT, TREASURY_ADDRESS_AVALANCHE),
    "bnb": EvmVerifier("BNB", BNB_CHAIN_ID, "bnb-mainnet",
        [os.getenv("BNB_RPC", "https://bsc-dataseed.binance.org"), "https://bsc.llamarpc.com"],
        BNB_USDC_CONTRACT, TREASURY_ADDRESS_BNB),
    "sei": EvmVerifier("SEI", SEI_CHAIN_ID, "sei-mainnet",
        [os.getenv("SEI_RPC", "https://evm-rpc.sei-apis.com")],
        SEI_USDC_CONTRACT, TREASURY_ADDRESS_SEI),
}


class ChainVerifyRequest(BaseModel):
    tx_hash: str
    expected_to: Optional[str] = None
    expected_amount_raw: Optional[int] = None


# ── BASE ──

@router.get("/api/base/info")
async def base_info():
    return {
        "network": "base-mainnet", "chainId": BASE_CHAIN_ID, "rpc": BASE_RPC,
        "usdcContract": BASE_USDC_CONTRACT, "treasury": TREASURY_ADDRESS_BASE,
        "status": "active" if TREASURY_ADDRESS_BASE else "not_configured",
    }

@router.post("/api/base/verify")
async def verify_base_tx(req: ChainVerifyRequest, request: Request):
    await check_rate_limit(request)
    return await evm_verifiers["base"].verify_transaction(req.tx_hash, req.expected_to)

@router.post("/api/base/verify-usdc")
async def verify_base_usdc(req: ChainVerifyRequest, request: Request):
    await check_rate_limit(request)
    return await evm_verifiers["base"].verify_usdc_transfer(req.tx_hash, req.expected_amount_raw)


# ── ETHEREUM ──

@router.get("/api/ethereum/info")
async def ethereum_info():
    from config import ETH_MIN_TX_USDC
    return {
        "network": "ethereum-mainnet", "chainId": ETH_CHAIN_ID, "rpc": ETH_RPC,
        "usdcContract": ETH_USDC_CONTRACT, "treasury": TREASURY_ADDRESS_ETH,
        "minTransactionUsdc": ETH_MIN_TX_USDC,
        "status": "active" if TREASURY_ADDRESS_ETH else "not_configured",
        "note": "Ethereum mainnet for large transactions only (high gas fees). Use Solana or Base for small amounts.",
    }

@router.post("/api/ethereum/verify")
async def verify_eth_tx(req: ChainVerifyRequest, request: Request):
    await check_rate_limit(request)
    return await evm_verifiers["ethereum"].verify_transaction(req.tx_hash, req.expected_to)

@router.post("/api/ethereum/verify-usdc")
async def verify_eth_usdc(req: ChainVerifyRequest, request: Request):
    await check_rate_limit(request)
    return await evm_verifiers["ethereum"].verify_usdc_transfer(req.tx_hash, req.expected_amount_raw)


# ── POLYGON ──

@router.get("/api/polygon/info")
async def polygon_info():
    return {
        "network": "polygon-mainnet", "chainId": POLYGON_CHAIN_ID, "rpc": POLYGON_RPC,
        "usdcContract": POLYGON_USDC_CONTRACT, "treasury": TREASURY_ADDRESS_POLYGON,
        "status": "active" if TREASURY_ADDRESS_POLYGON else "not_configured",
    }

@router.post("/api/polygon/verify")
async def verify_polygon_tx(req: ChainVerifyRequest, request: Request):
    await check_rate_limit(request)
    return await evm_verifiers["polygon"].verify_transaction(req.tx_hash, req.expected_to)

@router.post("/api/polygon/verify-usdc")
async def verify_polygon_usdc(req: ChainVerifyRequest, request: Request):
    await check_rate_limit(request)
    return await evm_verifiers["polygon"].verify_usdc_transfer(req.tx_hash, req.expected_amount_raw)


# ── ARBITRUM ──

@router.get("/api/arbitrum/info")
async def arbitrum_info():
    return {
        "network": "arbitrum-mainnet", "chainId": ARBITRUM_CHAIN_ID, "rpc": ARBITRUM_RPC,
        "usdcContract": ARBITRUM_USDC_CONTRACT, "treasury": TREASURY_ADDRESS_ARBITRUM,
        "status": "active" if TREASURY_ADDRESS_ARBITRUM else "not_configured",
    }

@router.post("/api/arbitrum/verify")
async def verify_arbitrum_tx(req: ChainVerifyRequest, request: Request):
    await check_rate_limit(request)
    return await evm_verifiers["arbitrum"].verify_transaction(req.tx_hash, req.expected_to)

@router.post("/api/arbitrum/verify-usdc")
async def verify_arbitrum_usdc(req: ChainVerifyRequest, request: Request):
    await check_rate_limit(request)
    return await evm_verifiers["arbitrum"].verify_usdc_transfer(req.tx_hash, req.expected_amount_raw)


# ── AVALANCHE ──

@router.get("/api/avalanche/info")
async def avalanche_info():
    return {
        "network": "avalanche-mainnet", "chainId": AVALANCHE_CHAIN_ID, "rpc": AVALANCHE_RPC,
        "usdcContract": AVALANCHE_USDC_CONTRACT, "treasury": TREASURY_ADDRESS_AVALANCHE,
        "status": "active" if TREASURY_ADDRESS_AVALANCHE else "not_configured",
    }

@router.post("/api/avalanche/verify")
async def verify_avalanche_tx(req: ChainVerifyRequest, request: Request):
    await check_rate_limit(request)
    return await evm_verifiers["avalanche"].verify_transaction(req.tx_hash, req.expected_to)

@router.post("/api/avalanche/verify-usdc")
async def verify_avalanche_usdc(req: ChainVerifyRequest, request: Request):
    await check_rate_limit(request)
    return await evm_verifiers["avalanche"].verify_usdc_transfer(req.tx_hash, req.expected_amount_raw)


# ── BNB ──

@router.get("/api/bnb/info")
async def bnb_info():
    return {
        "network": "bnb-mainnet", "chainId": BNB_CHAIN_ID, "rpc": BNB_RPC,
        "usdcContract": BNB_USDC_CONTRACT, "treasury": TREASURY_ADDRESS_BNB,
        "status": "active" if TREASURY_ADDRESS_BNB else "not_configured",
    }

@router.post("/api/bnb/verify")
async def verify_bnb_tx(req: ChainVerifyRequest, request: Request):
    await check_rate_limit(request)
    return await evm_verifiers["bnb"].verify_transaction(req.tx_hash, req.expected_to)

@router.post("/api/bnb/verify-usdc")
async def verify_bnb_usdc(req: ChainVerifyRequest, request: Request):
    await check_rate_limit(request)
    return await evm_verifiers["bnb"].verify_usdc_transfer(req.tx_hash, req.expected_amount_raw)
