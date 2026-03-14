"""MAXIA Art.9 V2 — x402 Middleware (Solana + Base multi-chain)"""
import json
from fastapi import Request
from fastapi.responses import JSONResponse
from config import (
    TREASURY_ADDRESS, TREASURY_ADDRESS_BASE,
    BASE_USDC_CONTRACT, BASE_CHAIN_ID, X402_PRICE_MAP,
)


async def x402_middleware(request: Request, call_next):
    """
    x402 V2 multi-chain middleware.
    Protected POST endpoints without X-Payment header get a 402 with
    payment options for both Solana and Base.
    """
    path = request.url.path
    price = X402_PRICE_MAP.get(path)

    if price and request.method == "POST":
        pay_header = request.headers.get("X-Payment")
        pay_network = request.headers.get("X-Payment-Network", "solana-mainnet")

        if not pay_header:
            accepts = []
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
            return JSONResponse(
                status_code=402,
                content={"x402Version": 2, "accepts": accepts},
                headers={"X-Payment-Required": "true"},
            )

        # verify based on network header
        if "base" in pay_network:
            from base_verifier import x402_verify_payment_base
            result = await x402_verify_payment_base(pay_header, price)
        else:
            from solana_verifier import verify_transaction
            ok = await verify_transaction(pay_header)
            result = {"valid": ok}

        if not result.get("valid"):
            return JSONResponse(
                status_code=402,
                content={"error": "Payment verification failed",
                         "detail": result.get("error", "")},
            )

    return await call_next(request)
