"""MAXIA ONE-27 — Fiat On-Ramp (Plan B: zero API key).

Genere des URLs directes vers Transak/Moonpay/Guardarian pour acheter
de la crypto par carte bancaire. Marche IMMEDIATEMENT sans API key.
Si une API key est configuree, utilise le mode embed (meilleur UX).

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

# ── Config (optionnel — marche aussi sans) ──

TRANSAK_API_KEY = os.getenv("TRANSAK_API_KEY", "")
TRANSAK_ENV = os.getenv("TRANSAK_ENV", "STAGING")
MOONPAY_API_KEY = os.getenv("MOONPAY_API_KEY", "")
GUARDARIAN_API_KEY = os.getenv("GUARDARIAN_API_KEY", "")

# ── Provider URLs ──

TRANSAK_BASE = "https://global.transak.com"
MOONPAY_BASE = "https://buy.moonpay.com"
GUARDARIAN_BASE = "https://guardarian.com/calculator"

# ── Token mappings ──

SUPPORTED_TOKENS = {
    "SOL": {"transak": "SOL", "moonpay": "sol", "guardarian": "sol", "network": "solana"},
    "USDC": {"transak": "USDC", "moonpay": "usdc_sol", "guardarian": "usdc", "network": "solana"},
    "ETH": {"transak": "ETH", "moonpay": "eth", "guardarian": "eth", "network": "ethereum"},
    "BTC": {"transak": "BTC", "moonpay": "btc", "guardarian": "btc", "network": "bitcoin"},
    "MATIC": {"transak": "MATIC", "moonpay": "matic_polygon", "guardarian": "matic", "network": "polygon"},
    "AVAX": {"transak": "AVAX", "moonpay": "avax_cchain", "guardarian": "avax", "network": "avalanche"},
    "BNB": {"transak": "BNB", "moonpay": "bnb_bsc", "guardarian": "bnb", "network": "bsc"},
    "ARB": {"transak": "ETH", "moonpay": None, "guardarian": "eth", "network": "arbitrum"},
    "BASE_ETH": {"transak": "ETH", "moonpay": None, "guardarian": "eth", "network": "base"},
}

SUPPORTED_FIAT = ["USD", "EUR", "GBP", "CAD", "AUD", "CHF", "JPY", "KRW", "BRL", "MXN"]


# ── Models ──

class OnrampRequest(BaseModel):
    crypto: str = Field(..., description="Token a acheter (SOL, ETH, BTC, USDC...)")
    fiat_amount: float = Field(50.0, ge=10, le=50000, description="Montant en fiat")
    fiat_currency: str = Field("USD", description="Devise fiat (USD, EUR, GBP...)")
    wallet_address: str = Field(..., min_length=10, description="Adresse wallet de reception")
    provider: str = Field("auto", description="Provider: transak, moonpay, guardarian, ou auto")


# ── URL Builders (marchent SANS API key) ──

def _build_transak_url(req: OnrampRequest) -> dict:
    """URL Transak — avec ou sans API key."""
    crypto = req.crypto.upper()
    token_info = SUPPORTED_TOKENS.get(crypto)
    if not token_info or not token_info.get("transak"):
        return {"error": f"Token {crypto} not supported on Transak"}

    params = {
        "cryptoCurrencyCode": token_info["transak"],
        "network": token_info["network"],
        "defaultFiatAmount": req.fiat_amount,
        "fiatCurrency": req.fiat_currency.upper(),
        "walletAddress": req.wallet_address,
        "themeColor": "7c3aed",
    }

    # Si API key dispo, mode embed complet
    if TRANSAK_API_KEY:
        params["apiKey"] = TRANSAK_API_KEY
        params["environment"] = TRANSAK_ENV
        params["disableWalletAddressForm"] = "true"
        params["hideMenu"] = "true"
        mode = "embed"
    else:
        mode = "redirect"

    url = f"{TRANSAK_BASE}?{urlencode(params)}"
    return {
        "provider": "transak",
        "mode": mode,
        "url": url,
        "crypto": crypto,
        "network": token_info["network"],
        "fiat_amount": req.fiat_amount,
        "fiat_currency": req.fiat_currency.upper(),
        "estimated_fees": "1-3%",
        "payment_methods": ["card", "bank_transfer", "apple_pay", "google_pay"],
    }


def _build_moonpay_url(req: OnrampRequest) -> dict:
    """URL Moonpay — avec ou sans API key."""
    crypto = req.crypto.upper()
    token_info = SUPPORTED_TOKENS.get(crypto)
    if not token_info or not token_info.get("moonpay"):
        return {"error": f"Token {crypto} not supported on Moonpay"}

    params = {
        "currencyCode": token_info["moonpay"],
        "baseCurrencyAmount": req.fiat_amount,
        "baseCurrencyCode": req.fiat_currency.lower(),
        "walletAddress": req.wallet_address,
    }

    if MOONPAY_API_KEY:
        params["apiKey"] = MOONPAY_API_KEY
        mode = "embed"
    else:
        mode = "redirect"

    url = f"{MOONPAY_BASE}?{urlencode(params)}"
    return {
        "provider": "moonpay",
        "mode": mode,
        "url": url,
        "crypto": crypto,
        "network": token_info["network"],
        "fiat_amount": req.fiat_amount,
        "fiat_currency": req.fiat_currency.upper(),
        "estimated_fees": "1.5-4.5%",
        "payment_methods": ["card", "bank_transfer", "apple_pay", "google_pay", "sepa"],
    }


def _build_guardarian_url(req: OnrampRequest) -> dict:
    """URL Guardarian — marche sans API key (redirect direct)."""
    crypto = req.crypto.upper()
    token_info = SUPPORTED_TOKENS.get(crypto)
    if not token_info or not token_info.get("guardarian"):
        return {"error": f"Token {crypto} not supported on Guardarian"}

    params = {
        "to_currency": token_info["guardarian"],
        "from_currency": req.fiat_currency.lower(),
        "from_amount": req.fiat_amount,
        "to_address": req.wallet_address,
    }

    if GUARDARIAN_API_KEY:
        params["partner_api_token"] = GUARDARIAN_API_KEY

    url = f"{GUARDARIAN_BASE}?{urlencode(params)}"
    return {
        "provider": "guardarian",
        "mode": "redirect",
        "url": url,
        "crypto": crypto,
        "network": token_info["network"],
        "fiat_amount": req.fiat_amount,
        "fiat_currency": req.fiat_currency.upper(),
        "estimated_fees": "2-5%",
        "payment_methods": ["card", "bank_transfer", "sepa"],
    }


# ── Endpoints ──

@router.get("/providers")
async def fiat_providers():
    """Liste des providers fiat on-ramp disponibles."""
    providers = [
        {
            "name": "Guardarian",
            "id": "guardarian",
            "status": "active",
            "mode": "embed" if GUARDARIAN_API_KEY else "redirect",
            "supported_tokens": [k for k, v in SUPPORTED_TOKENS.items() if v.get("guardarian")],
            "supported_fiat": SUPPORTED_FIAT,
            "fees": "2-5%",
            "payment_methods": ["card", "bank_transfer", "sepa"],
            "kyc_required": True,
            "min_amount_usd": 10,
            "max_amount_usd": 50000,
        },
        {
            "name": "Transak",
            "id": "transak",
            "status": "active",
            "mode": "embed" if TRANSAK_API_KEY else "redirect",
            "supported_tokens": [k for k, v in SUPPORTED_TOKENS.items() if v.get("transak")],
            "supported_fiat": SUPPORTED_FIAT,
            "fees": "1-3%",
            "payment_methods": ["card", "bank_transfer", "apple_pay", "google_pay"],
            "kyc_required": True,
            "min_amount_usd": 10,
            "max_amount_usd": 50000,
        },
        {
            "name": "Moonpay",
            "id": "moonpay",
            "status": "active",
            "mode": "embed" if MOONPAY_API_KEY else "redirect",
            "supported_tokens": [k for k, v in SUPPORTED_TOKENS.items() if v.get("moonpay")],
            "supported_fiat": SUPPORTED_FIAT,
            "fees": "1.5-4.5%",
            "payment_methods": ["card", "bank_transfer", "apple_pay", "google_pay", "sepa"],
            "kyc_required": True,
            "min_amount_usd": 20,
            "max_amount_usd": 50000,
        },
    ]

    return {
        "providers": providers,
        "count": len(providers),
        "note": "All providers work in redirect mode (no API key needed). Add API keys in .env for embed mode.",
    }


@router.post("/onramp")
async def create_onramp_link(req: OnrampRequest):
    """Genere un lien d'achat crypto par carte bancaire. Marche sans API key."""
    crypto = req.crypto.upper()
    fiat = req.fiat_currency.upper()

    if fiat not in SUPPORTED_FIAT:
        raise HTTPException(400, f"Unsupported fiat currency: {fiat}. Supported: {SUPPORTED_FIAT}")

    if req.fiat_amount < 10:
        raise HTTPException(400, "Minimum amount is $10")

    if crypto not in SUPPORTED_TOKENS:
        raise HTTPException(400, f"Unsupported token: {crypto}. Supported: {sorted(SUPPORTED_TOKENS.keys())}")

    # Build URLs for all providers (or selected one)
    if req.provider == "auto":
        # Priority: Guardarian (no key needed) > Transak > Moonpay
        result = _build_guardarian_url(req)
        if "error" in result:
            result = _build_transak_url(req)
        if "error" in result:
            result = _build_moonpay_url(req)
    elif req.provider == "transak":
        result = _build_transak_url(req)
    elif req.provider == "moonpay":
        result = _build_moonpay_url(req)
    elif req.provider == "guardarian":
        result = _build_guardarian_url(req)
    else:
        raise HTTPException(400, f"Unknown provider: {req.provider}. Use: transak, moonpay, guardarian, auto")

    if "error" in result:
        raise HTTPException(503, result["error"])

    # Also build links for other providers as alternatives
    alternatives = []
    for builder, name in [(_build_guardarian_url, "guardarian"), (_build_transak_url, "transak"), (_build_moonpay_url, "moonpay")]:
        if name != result.get("provider"):
            alt = builder(req)
            if "error" not in alt:
                alternatives.append({"provider": name, "url": alt["url"], "fees": alt.get("estimated_fees", "")})

    logger.info("[FiatOnramp] %s %s %s via %s (%s)", crypto, req.fiat_amount, fiat, result["provider"], result.get("mode", "redirect"))

    return {
        **result,
        "alternatives": alternatives,
        "instructions": [
            f"1. Click the URL to buy {crypto} with {fiat}",
            "2. Complete KYC verification on the provider's site (first time only, ~2 min)",
            "3. Enter payment details (card or bank transfer)",
            f"4. {crypto} will be sent directly to your wallet",
        ],
        "wallet_address": req.wallet_address,
        "timestamp": int(time.time()),
    }


@router.get("/supported")
async def fiat_supported():
    """Tokens et reseaux supportes pour l'achat par carte."""
    tokens_detail = {}
    for token, info in SUPPORTED_TOKENS.items():
        providers = []
        if info.get("guardarian"):
            providers.append("guardarian")
        if info.get("transak"):
            providers.append("transak")
        if info.get("moonpay"):
            providers.append("moonpay")
        tokens_detail[token] = {
            "providers": providers,
            "network": info["network"],
        }

    return {
        "tokens": tokens_detail,
        "token_count": len(SUPPORTED_TOKENS),
        "fiat_currencies": SUPPORTED_FIAT,
        "fiat_count": len(SUPPORTED_FIAT),
        "min_amount_usd": 10,
        "max_amount_usd": 50000,
        "note": "No API key required — all providers work in redirect mode",
    }
