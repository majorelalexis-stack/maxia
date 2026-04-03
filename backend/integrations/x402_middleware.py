"""MAXIA Art.9 V2 — x402 Middleware (14 chains: Solana + Base + Ethereum + XRPL + TON + SUI + Polygon + Arbitrum + Avalanche + BNB + TRON + NEAR + Aptos + SEI)"""
import logging
import asyncio, os
from fastapi import Request

logger = logging.getLogger(__name__)
from fastapi.responses import JSONResponse
from core.config import (
    TREASURY_ADDRESS, TREASURY_ADDRESS_BASE, TREASURY_ADDRESS_ETH,
    TREASURY_ADDRESS_XRPL, TREASURY_ADDRESS_TON, TREASURY_ADDRESS_SUI,
    TREASURY_ADDRESS_POLYGON, TREASURY_ADDRESS_ARBITRUM,
    TREASURY_ADDRESS_AVALANCHE, TREASURY_ADDRESS_BNB,
    TREASURY_ADDRESS_TRON,
    BASE_USDC_CONTRACT, BASE_CHAIN_ID,
    ETH_USDC_CONTRACT, ETH_CHAIN_ID, ETH_MIN_TX_USDC,
    TON_USDT_JETTON, SUI_USDC_TYPE,
    POLYGON_USDC_CONTRACT, POLYGON_CHAIN_ID,
    ARBITRUM_USDC_CONTRACT, ARBITRUM_CHAIN_ID,
    AVALANCHE_USDC_CONTRACT, AVALANCHE_CHAIN_ID,
    BNB_USDC_CONTRACT, BNB_CHAIN_ID,
    TRON_USDT_CONTRACT, TRON_USDC_CONTRACT,
    X402_PRICE_MAP, SUPPORTED_NETWORKS,
)

# ── Constants ──
DEFAULT_NETWORK = "solana-mainnet"
VERIFICATION_TIMEOUT = 30  # seconds


async def x402_middleware(request: Request, call_next):
    """
    x402 V2 multi-chain middleware.
    Protected POST endpoints without X-Payment header get a 402 with
    payment options for Solana, Base, Ethereum, and XRPL.
    Ethereum only for transactions >= ETH_MIN_TX_USDC.
    """
    path = request.url.path
    price = X402_PRICE_MAP.get(path)

    if price and request.method == "POST":
        pay_header = request.headers.get("X-Payment")
        pay_network = request.headers.get("X-Payment-Network", DEFAULT_NETWORK)

        if not pay_header:
            accepts = []
            # Solana
            if TREASURY_ADDRESS:
                accepts.append({
                    "scheme": "exact",
                    "network": "solana-mainnet",
                    "maxAmountRequired": str(int(price * 1e6)),
                    "resource": path,
                    "description": f"MAXIA service: {path}",
                    "mimeType": "application/json",
                    "payTo": TREASURY_ADDRESS,
                    "maxTimeoutSeconds": 60,
                })
            # Base L2
            if TREASURY_ADDRESS_BASE:
                accepts.append({
                    "scheme": "exact",
                    "network": "base-mainnet",
                    "maxAmountRequired": str(int(price * 1e6)),
                    "resource": path,
                    "description": f"MAXIA service: {path}",
                    "mimeType": "application/json",
                    "payTo": TREASURY_ADDRESS_BASE,
                    "asset": BASE_USDC_CONTRACT,
                    "maxTimeoutSeconds": 60,
                    "extra": {"chainId": BASE_CHAIN_ID},
                })
            # Ethereum — large transactions only
            if TREASURY_ADDRESS_ETH and price >= ETH_MIN_TX_USDC:
                accepts.append({
                    "scheme": "exact",
                    "network": "ethereum-mainnet",
                    "maxAmountRequired": str(int(price * 1e6)),
                    "resource": path,
                    "description": f"MAXIA service: {path} (Ethereum — large transactions only)",
                    "mimeType": "application/json",
                    "payTo": TREASURY_ADDRESS_ETH,
                    "asset": ETH_USDC_CONTRACT,
                    "maxTimeoutSeconds": 120,
                    "extra": {"chainId": ETH_CHAIN_ID, "minAmount": ETH_MIN_TX_USDC},
                })
            # XRPL
            if TREASURY_ADDRESS_XRPL:
                accepts.append({
                    "scheme": "exact",
                    "network": "xrpl-mainnet",
                    "maxAmountRequired": str(int(price * 1e6)),
                    "resource": path,
                    "description": f"MAXIA service: {path}",
                    "mimeType": "application/json",
                    "payTo": TREASURY_ADDRESS_XRPL,
                    "asset": "USD",
                    "maxTimeoutSeconds": 60,
                })
            # TON (non-EVM — uses USDT, not USDC)
            if TREASURY_ADDRESS_TON:
                accepts.append({
                    "scheme": "exact",
                    "network": "ton-mainnet",
                    "maxAmountRequired": str(int(price * 1e6)),
                    "resource": path,
                    "description": f"MAXIA service: {path} (TON — USDT)",
                    "mimeType": "application/json",
                    "payTo": TREASURY_ADDRESS_TON,
                    "asset": TON_USDT_JETTON,
                    "maxTimeoutSeconds": 60,
                })
            # SUI (non-EVM)
            if TREASURY_ADDRESS_SUI:
                accepts.append({
                    "scheme": "exact",
                    "network": "sui-mainnet",
                    "maxAmountRequired": str(int(price * 1e6)),
                    "resource": path,
                    "description": f"MAXIA service: {path}",
                    "mimeType": "application/json",
                    "payTo": TREASURY_ADDRESS_SUI,
                    "asset": SUI_USDC_TYPE,
                    "maxTimeoutSeconds": 60,
                })
            # Polygon PoS (EVM)
            if TREASURY_ADDRESS_POLYGON:
                accepts.append({
                    "scheme": "exact",
                    "network": "polygon-mainnet",
                    "maxAmountRequired": str(int(price * 1e6)),
                    "resource": path,
                    "description": f"MAXIA service: {path}",
                    "mimeType": "application/json",
                    "payTo": TREASURY_ADDRESS_POLYGON,
                    "asset": POLYGON_USDC_CONTRACT,
                    "maxTimeoutSeconds": 60,
                    "extra": {"chainId": POLYGON_CHAIN_ID},
                })
            # Arbitrum One (EVM L2)
            if TREASURY_ADDRESS_ARBITRUM:
                accepts.append({
                    "scheme": "exact",
                    "network": "arbitrum-mainnet",
                    "maxAmountRequired": str(int(price * 1e6)),
                    "resource": path,
                    "description": f"MAXIA service: {path}",
                    "mimeType": "application/json",
                    "payTo": TREASURY_ADDRESS_ARBITRUM,
                    "asset": ARBITRUM_USDC_CONTRACT,
                    "maxTimeoutSeconds": 60,
                    "extra": {"chainId": ARBITRUM_CHAIN_ID},
                })
            # Avalanche C-Chain (EVM)
            if TREASURY_ADDRESS_AVALANCHE:
                accepts.append({
                    "scheme": "exact",
                    "network": "avalanche-mainnet",
                    "maxAmountRequired": str(int(price * 1e6)),
                    "resource": path,
                    "description": f"MAXIA service: {path}",
                    "mimeType": "application/json",
                    "payTo": TREASURY_ADDRESS_AVALANCHE,
                    "asset": AVALANCHE_USDC_CONTRACT,
                    "maxTimeoutSeconds": 60,
                    "extra": {"chainId": AVALANCHE_CHAIN_ID},
                })
            # BNB Chain (EVM)
            if TREASURY_ADDRESS_BNB:
                accepts.append({
                    "scheme": "exact",
                    "network": "bnb-mainnet",
                    "maxAmountRequired": str(int(price * 1e6)),
                    "resource": path,
                    "description": f"MAXIA service: {path}",
                    "mimeType": "application/json",
                    "payTo": TREASURY_ADDRESS_BNB,
                    "asset": BNB_USDC_CONTRACT,
                    "maxTimeoutSeconds": 60,
                    "extra": {"chainId": BNB_CHAIN_ID},
                })
            # TRON (non-EVM — USDT primary, USDC available)
            if TREASURY_ADDRESS_TRON:
                accepts.append({
                    "scheme": "exact",
                    "network": "tron-mainnet",
                    "maxAmountRequired": str(int(price * 1e6)),
                    "resource": path,
                    "description": f"MAXIA service: {path} (TRON — USDT/USDC TRC-20)",
                    "mimeType": "application/json",
                    "payTo": TREASURY_ADDRESS_TRON,
                    "asset": TRON_USDT_CONTRACT,
                    "maxTimeoutSeconds": 60,
                })
            # NEAR
            _near_treasury = os.getenv("TREASURY_ADDRESS_NEAR", "")
            if _near_treasury:
                accepts.append({
                    "scheme": "exact",
                    "network": "near-mainnet",
                    "maxAmountRequired": str(int(price * 1e6)),
                    "resource": path,
                    "description": f"MAXIA service: {path}",
                    "mimeType": "application/json",
                    "payTo": _near_treasury,
                    "asset": "17208628f84f5d6ad33f0da3bbbeb27ffcb398eac501a31bd6ad2011e36133a1",
                    "maxTimeoutSeconds": 60,
                })
            # Aptos
            _aptos_treasury = os.getenv("TREASURY_ADDRESS_APTOS", "")
            if _aptos_treasury:
                accepts.append({
                    "scheme": "exact",
                    "network": "aptos-mainnet",
                    "maxAmountRequired": str(int(price * 1e6)),
                    "resource": path,
                    "description": f"MAXIA service: {path}",
                    "mimeType": "application/json",
                    "payTo": _aptos_treasury,
                    "asset": "0xbae207659db88bea0cbead6da0ed00aac12edcdda169e591cd41c94180b46f3b::usdc::USDC",
                    "maxTimeoutSeconds": 60,
                })
            # SEI (EVM)
            _sei_treasury = os.getenv("TREASURY_ADDRESS_SEI", "")
            if _sei_treasury:
                from core.config import SEI_USDC_CONTRACT, SEI_CHAIN_ID
                accepts.append({
                    "scheme": "exact",
                    "network": "sei-mainnet",
                    "maxAmountRequired": str(int(price * 1e6)),
                    "resource": path,
                    "description": f"MAXIA service: {path}",
                    "mimeType": "application/json",
                    "payTo": _sei_treasury,
                    "asset": SEI_USDC_CONTRACT,
                    "maxTimeoutSeconds": 60,
                    "extra": {"chainId": SEI_CHAIN_ID},
                })
            return JSONResponse(
                status_code=402,
                content={"x402Version": 2, "accepts": accepts},
                headers={"X-Payment-Required": "true"},
            )

        # ── Validate network ──
        if pay_network not in SUPPORTED_NETWORKS:
            return JSONResponse(
                status_code=402,
                content={
                    "error": f"Unsupported network: {pay_network}",
                    "supported": SUPPORTED_NETWORKS,
                },
            )

        # ── ETH threshold check ──
        if "ethereum" in pay_network and price < ETH_MIN_TX_USDC:
            return JSONResponse(
                status_code=402,
                content={
                    "error": f"Ethereum payments require a minimum of ${ETH_MIN_TX_USDC} due to gas fees. Use Solana or Base for smaller amounts.",
                },
            )

        # ── Logging ──
        pay_tx = pay_header
        logger.info(f"[x402] Payment attempt: {pay_network} tx={pay_tx[:16]}... path={path} amount=${price}")

        # ── Verify payment with timeout ──
        try:
            if "ethereum" in pay_network:
                from blockchain.eth_verifier import x402_verify_payment_eth
                verify_call = x402_verify_payment_eth(pay_header, price)
            elif "base" in pay_network:
                from blockchain.base_verifier import x402_verify_payment_base
                verify_call = x402_verify_payment_base(pay_header, price)
            elif "xrpl" in pay_network or "xrp" in pay_network:
                try:
                    from blockchain.xrpl_verifier import verify_xrpl_transaction
                except ImportError:
                    return JSONResponse(
                        status_code=402,
                        content={"error": "XRPL verification unavailable (xrpl-py not installed)"},
                    )
                verify_call = verify_xrpl_transaction(
                    tx_hash=pay_header,
                    expected_dest=TREASURY_ADDRESS_XRPL,
                    expected_amount=price,
                )
            elif "ton" in pay_network:
                from blockchain.ton_verifier import verify_ton_transaction
                verify_call = verify_ton_transaction(
                    tx_hash=pay_header,
                    expected_dest=TREASURY_ADDRESS_TON,
                    expected_amount=price,
                )
            elif "sui" in pay_network:
                from blockchain.sui_verifier import verify_sui_transaction
                verify_call = verify_sui_transaction(
                    tx_digest=pay_header,
                    expected_dest=TREASURY_ADDRESS_SUI,
                    expected_amount=price,
                )
            elif "polygon" in pay_network:
                from blockchain.polygon_verifier import x402_verify_payment_polygon
                verify_call = x402_verify_payment_polygon(pay_header, price)
            elif "arbitrum" in pay_network:
                from blockchain.arbitrum_verifier import x402_verify_payment_arbitrum
                verify_call = x402_verify_payment_arbitrum(pay_header, price)
            elif "avalanche" in pay_network:
                from blockchain.avalanche_verifier import x402_verify_payment_avalanche
                verify_call = x402_verify_payment_avalanche(pay_header, price)
            elif "bnb" in pay_network:
                from blockchain.bnb_verifier import x402_verify_payment_bnb
                verify_call = x402_verify_payment_bnb(pay_header, price)
            elif "tron" in pay_network:
                from blockchain.tron_verifier import x402_verify_payment_tron
                verify_call = x402_verify_payment_tron(pay_header, price)
            elif "near" in pay_network:
                from blockchain.near_verifier import verify_near_transaction
                verify_call = verify_near_transaction(
                    tx_hash=pay_header, sender_id="",
                    expected_dest=os.getenv("TREASURY_ADDRESS_NEAR", ""),
                    expected_amount=price,
                )
            elif "aptos" in pay_network:
                from blockchain.aptos_verifier import verify_aptos_transaction
                verify_call = verify_aptos_transaction(
                    tx_hash=pay_header,
                    expected_dest=os.getenv("TREASURY_ADDRESS_APTOS", ""),
                    expected_amount=price,
                )
            elif "sei" in pay_network:
                from blockchain.sei_verifier import x402_verify_payment_sei
                verify_call = x402_verify_payment_sei(pay_header, price)
            else:
                from blockchain.solana_verifier import verify_transaction
                verify_call = verify_transaction(
                    tx_signature=pay_header,
                    expected_amount_usdc=price,
                    expected_recipient=TREASURY_ADDRESS,
                )

            result = await asyncio.wait_for(verify_call, timeout=VERIFICATION_TIMEOUT)
        except asyncio.TimeoutError:
            return JSONResponse(
                status_code=402,
                content={"error": "Payment verification timeout. Try again."},
            )

        # ── Standardize result key ──
        # XRPL, TON, SUI, TRON verifiers use "verified"; Solana/Base/ETH use "valid"
        if any(net in pay_network for net in ("xrpl", "xrp", "ton", "sui", "tron")):
            is_valid = result.get("verified", False)
        else:
            is_valid = result.get("valid", False)

        logger.info(f"[x402] Verification result: {'VALID' if is_valid else 'INVALID'}")

        # V-16: Replay protection — record tx signature to prevent reuse
        if is_valid and pay_header:
            try:
                from core.database import db as _x402_db
                if await _x402_db.tx_already_processed(pay_header):
                    return JSONResponse(status_code=402, content={"error": "Payment already used (replay detected)"})
                await _x402_db.record_transaction("x402", pay_header, price, "x402_payment")
            except Exception as e:
                logger.error(f"[x402] Replay check error: {e}")

        if not is_valid:
            return JSONResponse(
                status_code=402,
                content={
                    "error": "Payment verification failed",
                    "detail": result.get("error", ""),
                },
            )

    return await call_next(request)
