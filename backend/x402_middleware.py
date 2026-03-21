"""MAXIA Art.9 V2 — x402 Middleware (Solana + Base + Ethereum + XRPL multi-chain)"""
import asyncio
from fastapi import Request
from fastapi.responses import JSONResponse
from config import (
    TREASURY_ADDRESS, TREASURY_ADDRESS_BASE, TREASURY_ADDRESS_ETH,
    TREASURY_ADDRESS_XRPL,
    BASE_USDC_CONTRACT, BASE_CHAIN_ID,
    ETH_USDC_CONTRACT, ETH_CHAIN_ID, ETH_MIN_TX_USDC,
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
        print(f"[x402] Payment attempt: {pay_network} tx={pay_tx[:16]}... path={path} amount=${price}")

        # ── Verify payment with timeout ──
        try:
            if "ethereum" in pay_network:
                from eth_verifier import x402_verify_payment_eth
                verify_call = x402_verify_payment_eth(pay_header, price)
            elif "base" in pay_network:
                from base_verifier import x402_verify_payment_base
                verify_call = x402_verify_payment_base(pay_header, price)
            elif "xrpl" in pay_network or "xrp" in pay_network:
                try:
                    from xrpl_verifier import verify_xrpl_transaction
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
            else:
                from solana_verifier import verify_transaction
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
        if "xrpl" in pay_network or "xrp" in pay_network:
            is_valid = result.get("verified", False)
        else:
            is_valid = result.get("valid", False)

        print(f"[x402] Verification result: {'VALID' if is_valid else 'INVALID'}")

        if not is_valid:
            return JSONResponse(
                status_code=402,
                content={
                    "error": "Payment verification failed",
                    "detail": result.get("error", ""),
                },
            )

    return await call_next(request)
