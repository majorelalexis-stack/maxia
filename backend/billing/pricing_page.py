"""MAXIA V12 — Pricing Page API: public pricing tiers for frontend consumption.

Sert les donnees structurees de pricing pour la page publique :
- 3 tiers : Free, Premium ($9.99/mois), Enterprise (custom)
- FAQ integree
- Matrice de comparaison entre tiers
- Aucune authentification requise (endpoints publics)
"""

import logging
from typing import Any

from fastapi import APIRouter

logger = logging.getLogger("maxia.pricing")

router = APIRouter(prefix="/api/pricing", tags=["pricing"])

# ── Tier Definitions ──

_FREE_FEATURES: list[str] = [
    "65+ token prices",
    "Crypto swap quotes",
    "DeFi yield scanner",
    "Market sentiment",
    "Fear & Greed Index",
    "Trending tokens",
    "Wallet analysis",
    "15 MCP tools",
]

_PREMIUM_FEATURES: list[str] = [
    "Everything in Free",
    "10,000 req/day",
    "DCA Bot automation",
    "Grid trading bot",
    "Price alerts (unlimited)",
    "Whale monitoring",
    "Copy trading",
    "Auto-compound DeFi",
    "Priority support",
    "30 MCP tools",
]

_ENTERPRISE_FEATURES: list[str] = [
    "Everything in Premium",
    "Unlimited requests",
    "SSO (Google/Microsoft)",
    "Fleet dashboard",
    "Multi-tenant isolation",
    "SLA 99.9%",
    "Dedicated support",
    "Custom integrations",
    "All 47 MCP tools",
]

TIERS: list[dict[str, Any]] = [
    {
        "name": "Free",
        "price_usd": 0,
        "price_label": "Free forever",
        "requests_per_day": 100,
        "features": _FREE_FEATURES,
        "limits": {"rate": "100 req/day", "swap_fee": "0.10%", "support": "Community"},
        "cta": "Get Started",
        "cta_url": "/register",
    },
    {
        "name": "Premium",
        "price_usd": 9.99,
        "price_label": "$9.99/month",
        "requests_per_day": 10_000,
        "features": _PREMIUM_FEATURES,
        "limits": {"rate": "10,000 req/day", "swap_fee": "0.05%", "support": "Email"},
        "cta": "Subscribe",
        "cta_url": "/api/credits/deposit",
        "popular": True,
    },
    {
        "name": "Enterprise",
        "price_usd": None,
        "price_label": "Custom",
        "requests_per_day": None,
        "features": _ENTERPRISE_FEATURES,
        "limits": {"rate": "Unlimited", "swap_fee": "0.01%", "support": "Dedicated"},
        "cta": "Contact Us",
        "cta_url": "mailto:ceo@maxiaworld.app",
    },
]

FAQ: list[dict[str, str]] = [
    {
        "q": "How do I pay?",
        "a": "Deposit USDC on Solana or Base. Your credits are used automatically.",
    },
    {
        "q": "Can I cancel anytime?",
        "a": "Yes. No lock-in. Your remaining credits stay in your account.",
    },
    {
        "q": "Which tokens are supported?",
        "a": "65+ tokens across 15 blockchains including SOL, ETH, BTC, and more.",
    },
    {
        "q": "Is there an API key?",
        "a": "Yes. Register at /register to get your API key instantly.",
    },
]

# ── Comparison Matrix ──

_COMPARISON_FEATURES: list[dict[str, Any]] = [
    {"feature": "Token prices (65+)", "free": True, "premium": True, "enterprise": True},
    {"feature": "Crypto swap quotes", "free": True, "premium": True, "enterprise": True},
    {"feature": "DeFi yield scanner", "free": True, "premium": True, "enterprise": True},
    {"feature": "Market sentiment", "free": True, "premium": True, "enterprise": True},
    {"feature": "Fear & Greed Index", "free": True, "premium": True, "enterprise": True},
    {"feature": "Wallet analysis", "free": True, "premium": True, "enterprise": True},
    {"feature": "MCP tools", "free": "15", "premium": "30", "enterprise": "47"},
    {"feature": "Requests per day", "free": "100", "premium": "10,000", "enterprise": "Unlimited"},
    {"feature": "Swap fee", "free": "0.10%", "premium": "0.05%", "enterprise": "0.01%"},
    {"feature": "DCA Bot", "free": False, "premium": True, "enterprise": True},
    {"feature": "Grid trading bot", "free": False, "premium": True, "enterprise": True},
    {"feature": "Price alerts", "free": False, "premium": "Unlimited", "enterprise": "Unlimited"},
    {"feature": "Whale monitoring", "free": False, "premium": True, "enterprise": True},
    {"feature": "Copy trading", "free": False, "premium": True, "enterprise": True},
    {"feature": "Auto-compound DeFi", "free": False, "premium": True, "enterprise": True},
    {"feature": "SSO (Google/Microsoft)", "free": False, "premium": False, "enterprise": True},
    {"feature": "Fleet dashboard", "free": False, "premium": False, "enterprise": True},
    {"feature": "Multi-tenant isolation", "free": False, "premium": False, "enterprise": True},
    {"feature": "SLA", "free": "Best effort", "premium": "99%", "enterprise": "99.9%"},
    {"feature": "Support", "free": "Community", "premium": "Email", "enterprise": "Dedicated"},
]


# ── Endpoints ──


@router.get("/tiers")
async def get_pricing_tiers() -> dict[str, Any]:
    """Public pricing tiers with FAQ — no auth required."""
    return {"tiers": TIERS, "faq": FAQ}


@router.get("/compare")
async def get_pricing_compare() -> dict[str, Any]:
    """Public feature comparison matrix between tiers."""
    return {
        "columns": ["Free", "Premium", "Enterprise"],
        "features": _COMPARISON_FEATURES,
    }


@router.get("/onboard")
async def get_onboarding_guide() -> dict[str, Any]:
    """Step-by-step onboarding guide for new AI agents."""
    return {
        "title": "Get started with MAXIA API in 60 seconds",
        "steps": [
            {
                "step": 1,
                "title": "Register your agent",
                "method": "POST",
                "endpoint": "/api/public/register",
                "body": {"wallet": "YOUR_SOLANA_OR_EVM_ADDRESS", "name": "MyAgent"},
                "result": "Instant API key — no email, no KYC",
                "free": True,
            },
            {
                "step": 2,
                "title": "Try the Free tier",
                "examples": [
                    {"desc": "Get token prices", "method": "GET", "endpoint": "/api/public/prices"},
                    {"desc": "Swap quote", "method": "GET", "endpoint": "/api/public/swap/quote?from=USDC&to=SOL&amount=10"},
                    {"desc": "DeFi yields", "method": "GET", "endpoint": "/api/public/defi/best-yield"},
                    {"desc": "Whale alerts", "method": "GET", "endpoint": "/api/trading/whales"},
                    {"desc": "Chat (NL)", "method": "POST", "endpoint": "/api/chat", "body": {"message": "price SOL"}},
                ],
                "limit": "100 requests/day — plenty for testing",
                "free": True,
            },
            {
                "step": 3,
                "title": "Upgrade to Premium ($9.99/month)",
                "benefits": [
                    "10,000 requests/day",
                    "DCA + Grid trading bots",
                    "Unlimited price alerts",
                    "Copy trading",
                    "Auto-compound DeFi",
                    "30 MCP tools",
                ],
                "how_to_pay": {
                    "stripe": {"method": "POST", "endpoint": "/api/premium/subscribe", "body": {"api_key": "YOUR_KEY", "payment_method": "stripe"}},
                    "usdc": {"method": "POST", "endpoint": "/api/premium/subscribe", "body": {"api_key": "YOUR_KEY", "payment_method": "usdc", "usdc_tx_hash": "TX_HASH"}},
                    "fiat_card": {"method": "POST", "endpoint": "/api/fiat/onramp", "body": {"crypto": "USDC", "fiat_amount": 10, "wallet_address": "YOUR_WALLET"}},
                },
            },
            {
                "step": 4,
                "title": "Integrate with your framework",
                "sdks": {
                    "python": "pip install maxia",
                    "typescript": "npm install maxia",
                    "langchain": "pip install langchain-maxia",
                    "crewai": "pip install crewai-tools-maxia",
                    "mcp": "Connect to https://maxiaworld.app/mcp/manifest",
                },
                "example_python": 'from maxia import Maxia\nm = Maxia()\nprint(m.prices())',
            },
        ],
        "support": {
            "email": "ceo@maxiaworld.app",
            "docs": "https://maxiaworld.app/docs",
            "pricing": "/api/pricing/tiers",
            "status": "/api/pricing/compare",
        },
    }
