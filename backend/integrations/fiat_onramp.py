"""MAXIA ONE-27 — Fiat On-Ramp via Transak + Moonpay.

Genere des URLs embed pour acheter de la crypto par carte bancaire.
Pas de backend wallet custody — redirect vers le widget du provider.
Commission MAXIA via programme partenaire Transak/Moonpay.

Endpoints:
  GET  /api/fiat/providers      — liste des providers disponibles
  POST /api/fiat/onramp         — genere un lien d'achat personnalise
  GET  /api/fiat/supported      — tokens et chains supportes
"""
import logging
import os
import time
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger("fiat_onramp")

router = APIRouter(prefix="/api/fiat", tags=["fiat"])

# ── Config ──

TRANSAK_API_KEY = os.getenv("TRANSAK_API_KEY", "")
TRANSAK_ENV = os.getenv("TRANSAK_ENV", "STAGING")  # STAGING ou PRODUCTION
MOONPAY_API_KEY = os.getenv("MOONPAY_API_KEY", "")

TRANSAK_BASE = "https://global.transak.com"
MOONPAY_BASE = "https://buy.moonpay.com"

# Tokens supportes par provider (intersection avec MAXIA)
TRANSAK_TOKENS = {
    "SOL": {"network": "solana", "code": "SOL"},
    "USDC": {"network": "solana", "code": "USDC"},
    "ETH": {"network": "ethereum", "code": "ETH"},
    "BTC": {"network": "bitcoin", "code": "BTC"},
    "MATIC": {"network": "polygon", "code": "MATIC"},
    "AVAX": {"network": "avalanche", "code": "AVAX"},
    "BNB": {"network": "bsc", "code": "BNB"},
    "ARB": {"network": "arbitrum", "code": "ETH"},  # ETH on Arbitrum
    "BASE_ETH": {"network": "base", "code": "ETH"},
}

MOONPAY_TOKENS = {
    "SOL": {"code": "sol", "network": "solana"},
    "USDC": {"code": "usdc_sol", "network": "solana"},
    "ETH": {"code": "eth", "network": "ethereum"},
    "BTC": {"code": "btc", "network": "bitcoin"},
    "MATIC": {"code": "matic_polygon", "network": "polygon"},
    "AVAX": {"code": "avax_cchain", "network": "avalanche"},
    "BNB": {"code": "bnb_bsc", "network": "bsc"},
}

# Fiat currencies supportees
SUPPORTED_FIAT = ["USD", "EUR", "GBP", "CAD", "AUD", "CHF", "JPY", "KRW", "BRL", "MXN"]


# ── Models ──

class OnrampRequest(BaseModel):
    crypto: str = Field(..., description="Token a acheter (SOL, ETH, BTC, USDC...)")
    fiat_amount: float = Field(50.0, ge=10, le=50000, description="Montant en fiat")
    fiat_currency: str = Field("USD", description="Devise fiat (USD, EUR, GBP...)")
    wallet_address: str = Field(..., min_length=10, description="Adresse wallet de reception")
    provider: str = Field("auto", description="Provider: transak, moonpay, ou auto")
    network: Optional[str] = Field(None, description="Network override (solana, ethereum, base...)")


# ── Helpers ──

def _build_transak_url(req: OnrampRequest) -> dict:
    """Genere l'URL Transak embed."""
    if not TRANSAK_API_KEY:
        return {"error": "Transak not configured"}

    crypto = req.crypto.upper()
    token_info = TRANSAK_TOKENS.get(crypto)
    if not token_info:
        return {"error": f"Token {crypto} not supported on Transak"}

    network = req.network or token_info["network"]

    params = {
        "apiKey": TRANSAK_API_KEY,
        "environment": TRANSAK_ENV,
        "cryptoCurrencyCode": token_info["code"],
        "network": network,
        "defaultFiatAmount": req.fiat_amount,
        "fiatCurrency": req.fiat_currency.upper(),
        "walletAddress": req.wallet_address,
        "disableWalletAddressForm": "true",
        "themeColor": "7c3aed",  # MAXIA purple
        "hideMenu": "true",
        "redirectURL": "https://maxiaworld.app/onramp/success",
    }

    url = f"{TRANSAK_BASE}?{urlencode(params)}"
    return {
        "provider": "transak",
        "url": url,
        "crypto": crypto,
        "network": network,
        "fiat_amount": req.fiat_amount,
        "fiat_currency": req.fiat_currency.upper(),
        "estimated_fees": "1-3%",
        "payment_methods": ["card", "bank_transfer", "apple_pay", "google_pay"],
    }


def _build_moonpay_url(req: OnrampRequest) -> dict:
    """Genere l'URL Moonpay embed."""
    if not MOONPAY_API_KEY:
        return {"error": "Moonpay not configured"}

    crypto = req.crypto.upper()
    token_info = MOONPAY_TOKENS.get(crypto)
    if not token_info:
        return {"error": f"Token {crypto} not supported on Moonpay"}

    params = {
        "apiKey": MOONPAY_API_KEY,
        "currencyCode": token_info["code"],
        "baseCurrencyAmount": req.fiat_amount,
        "baseCurrencyCode": req.fiat_currency.lower(),
        "walletAddress": req.wallet_address,
        "colorCode": "%237c3aed",  # MAXIA purple
        "redirectURL": "https://maxiaworld.app/onramp/success",
    }

    url = f"{MOONPAY_BASE}?{urlencode(params)}"
    return {
        "provider": "moonpay",
        "url": url,
        "crypto": crypto,
        "network": token_info["network"],
        "fiat_amount": req.fiat_amount,
        "fiat_currency": req.fiat_currency.upper(),
        "estimated_fees": "1.5-4.5%",
        "payment_methods": ["card", "bank_transfer", "apple_pay", "google_pay", "sepa"],
    }


# ── Endpoints ──

@router.get("/providers")
async def fiat_providers():
    """Liste des providers fiat on-ramp disponibles."""
    providers = []

    if TRANSAK_API_KEY:
        providers.append({
            "name": "Transak",
            "id": "transak",
            "status": "active",
            "supported_tokens": list(TRANSAK_TOKENS.keys()),
            "supported_fiat": SUPPORTED_FIAT,
            "fees": "1-3%",
            "payment_methods": ["card", "bank_transfer", "apple_pay", "google_pay"],
            "kyc_required": True,
            "min_amount_usd": 10,
            "max_amount_usd": 50000,
        })

    if MOONPAY_API_KEY:
        providers.append({
            "name": "Moonpay",
            "id": "moonpay",
            "status": "active",
            "supported_tokens": list(MOONPAY_TOKENS.keys()),
            "supported_fiat": SUPPORTED_FIAT,
            "fees": "1.5-4.5%",
            "payment_methods": ["card", "bank_transfer", "apple_pay", "google_pay", "sepa"],
            "kyc_required": True,
            "min_amount_usd": 20,
            "max_amount_usd": 50000,
        })

    if not providers:
        providers.append({
            "name": "Coming Soon",
            "id": "none",
            "status": "pending_api_keys",
            "note": "Set TRANSAK_API_KEY or MOONPAY_API_KEY in .env to activate",
            "supported_tokens": list(set(list(TRANSAK_TOKENS.keys()) + list(MOONPAY_TOKENS.keys()))),
        })

    return {
        "providers": providers,
        "count": len([p for p in providers if p.get("status") == "active"]),
        "note": "Fiat on-ramp lets users buy crypto with credit card or bank transfer",
    }


@router.post("/onramp")
async def create_onramp_link(req: OnrampRequest):
    """Genere un lien d'achat crypto par carte bancaire."""
    crypto = req.crypto.upper()
    fiat = req.fiat_currency.upper()

    if fiat not in SUPPORTED_FIAT:
        raise HTTPException(400, f"Unsupported fiat currency: {fiat}. Supported: {SUPPORTED_FIAT}")

    if req.fiat_amount < 10:
        raise HTTPException(400, "Minimum amount is $10")

    # Auto-select provider
    if req.provider == "auto":
        # Prefer Transak (lower fees), fallback to Moonpay
        if TRANSAK_API_KEY and crypto in TRANSAK_TOKENS:
            result = _build_transak_url(req)
        elif MOONPAY_API_KEY and crypto in MOONPAY_TOKENS:
            result = _build_moonpay_url(req)
        else:
            raise HTTPException(
                503,
                f"No fiat provider available for {crypto}. "
                f"Supported tokens: {sorted(set(list(TRANSAK_TOKENS.keys()) + list(MOONPAY_TOKENS.keys())))}",
            )
    elif req.provider == "transak":
        result = _build_transak_url(req)
    elif req.provider == "moonpay":
        result = _build_moonpay_url(req)
    else:
        raise HTTPException(400, f"Unknown provider: {req.provider}. Use: transak, moonpay, auto")

    if "error" in result:
        raise HTTPException(503, result["error"])

    logger.info("[FiatOnramp] %s %s %s %s via %s", crypto, req.fiat_amount, fiat, req.wallet_address[:10], result["provider"])

    return {
        **result,
        "instructions": [
            f"1. Open the URL to buy {crypto} with {fiat}",
            "2. Complete KYC verification (first time only, ~2 min)",
            "3. Enter payment details (card or bank transfer)",
            f"4. {crypto} will be sent directly to your wallet",
        ],
        "wallet_address": req.wallet_address,
        "timestamp": int(time.time()),
    }


@router.get("/supported")
async def fiat_supported():
    """Tokens et reseaux supportes pour l'achat par carte."""
    all_tokens = sorted(set(list(TRANSAK_TOKENS.keys()) + list(MOONPAY_TOKENS.keys())))

    tokens_detail = {}
    for token in all_tokens:
        providers = []
        networks = set()
        if token in TRANSAK_TOKENS:
            providers.append("transak")
            networks.add(TRANSAK_TOKENS[token]["network"])
        if token in MOONPAY_TOKENS:
            providers.append("moonpay")
            networks.add(MOONPAY_TOKENS[token]["network"])
        tokens_detail[token] = {
            "providers": providers,
            "networks": sorted(networks),
        }

    return {
        "tokens": tokens_detail,
        "token_count": len(all_tokens),
        "fiat_currencies": SUPPORTED_FIAT,
        "fiat_count": len(SUPPORTED_FIAT),
        "min_amount_usd": 10,
        "max_amount_usd": 50000,
    }
