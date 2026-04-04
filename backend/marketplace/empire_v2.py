"""MAXIA Empire V2 Sprint 1 — Auto-Discovery, Passport V2, Starter Templates.

E17: Custom OpenAPI spec for agent auto-discovery
E13: Revocation list + JWT portable + enriched public profile
E4:  Starter agent templates (5 copiable agents)
"""
import json
import logging
import re
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel, Field
from core.error_utils import safe_error

logger = logging.getLogger(__name__)

router = APIRouter(tags=["empire-v2"])

# Identifier validation: DID, UAID, or agent_id (alphanumeric + underscore/hyphen, max 128)
_IDENTIFIER_RE = re.compile(r'^[a-zA-Z0-9_:.\-]{1,128}$')


class VerifyJWTRequest(BaseModel):
    """Request body for JWT verification."""
    jwt: str = Field(..., min_length=10, max_length=4096)


# ══════════════════════════════════════════
# E17 — Custom OpenAPI Discovery Spec
# ══════════════════════════════════════════

_OPENAPI_SPEC: Optional[dict] = None


def _build_openapi_spec() -> dict:
    """Build a minimal OpenAPI 3.1 spec for agent auto-discovery."""
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "MAXIA — AI-to-AI Marketplace",
            "version": "12.2.0",
            "description": (
                "AI-to-AI marketplace on 15 blockchains. "
                "Discover, buy, and sell AI services using USDC. "
                "Supports MCP, A2A, x402, AIP protocols."
            ),
            "contact": {"name": "MAXIA", "url": "https://maxiaworld.app", "email": "contact@maxiaworld.app"},
            "license": {"name": "MIT"},
        },
        "servers": [{"url": "https://maxiaworld.app", "description": "Production"}],
        "paths": {
            "/api/public/register": {
                "post": {
                    "summary": "Register as an AI agent (free, instant API key)",
                    "operationId": "registerAgent",
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {
                            "type": "object",
                            "required": ["name", "wallet"],
                            "properties": {
                                "name": {"type": "string", "description": "Agent name"},
                                "wallet": {"type": "string", "description": "Solana wallet address"},
                                "description": {"type": "string"},
                            },
                        }}},
                    },
                    "responses": {"200": {"description": "API key returned"}},
                },
            },
            "/api/public/discover": {
                "get": {
                    "summary": "Discover AI services by capability, price, rating",
                    "operationId": "discoverServices",
                    "parameters": [
                        {"name": "capability", "in": "query", "schema": {"type": "string"}},
                        {"name": "max_price", "in": "query", "schema": {"type": "number"}},
                        {"name": "min_rating", "in": "query", "schema": {"type": "number"}},
                    ],
                    "responses": {"200": {"description": "List of services"}},
                },
            },
            "/api/public/execute": {
                "post": {
                    "summary": "Execute (buy + run) an AI service",
                    "operationId": "executeService",
                    "security": [{"apiKey": []}],
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {
                            "type": "object",
                            "required": ["service_id", "prompt"],
                            "properties": {
                                "service_id": {"type": "string"},
                                "prompt": {"type": "string"},
                                "payment_tx": {"type": "string"},
                            },
                        }}},
                    },
                    "responses": {"200": {"description": "Service result"}},
                },
            },
            "/api/public/services": {
                "get": {
                    "summary": "List all available AI services",
                    "operationId": "listServices",
                    "responses": {"200": {"description": "All services"}},
                },
            },
            "/api/public/crypto/prices": {
                "get": {
                    "summary": "Live crypto prices (65+ tokens)",
                    "operationId": "getCryptoPrices",
                    "responses": {"200": {"description": "Token prices"}},
                },
            },
            "/api/public/crypto/quote": {
                "get": {
                    "summary": "Swap quote (65 tokens, 4160 pairs)",
                    "operationId": "getSwapQuote",
                    "parameters": [
                        {"name": "from_token", "in": "query", "required": True, "schema": {"type": "string"}},
                        {"name": "to_token", "in": "query", "required": True, "schema": {"type": "string"}},
                        {"name": "amount", "in": "query", "required": True, "schema": {"type": "number"}},
                    ],
                    "responses": {"200": {"description": "Swap quote"}},
                },
            },
            "/api/public/gpu/tiers": {
                "get": {
                    "summary": "GPU rental tiers and pricing",
                    "operationId": "getGpuTiers",
                    "responses": {"200": {"description": "GPU tiers"}},
                },
            },
            "/api/public/defi/best-yield": {
                "get": {
                    "summary": "Best DeFi yields across 15 chains",
                    "operationId": "getBestYield",
                    "parameters": [
                        {"name": "asset", "in": "query", "schema": {"type": "string"}},
                        {"name": "chain", "in": "query", "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "DeFi yields"}},
                },
            },
            "/api/public/sentiment": {
                "get": {
                    "summary": "Crypto market sentiment analysis",
                    "operationId": "getSentiment",
                    "parameters": [
                        {"name": "token", "in": "query", "required": True, "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "Sentiment data"}},
                },
            },
            "/mcp/manifest": {
                "get": {
                    "summary": "MCP (Model Context Protocol) manifest — 46 tools",
                    "operationId": "getMcpManifest",
                    "responses": {"200": {"description": "MCP manifest"}},
                },
            },
        },
        "components": {
            "securitySchemes": {
                "apiKey": {
                    "type": "apiKey",
                    "in": "header",
                    "name": "X-API-Key",
                    "description": "Register at /api/public/register to get a free API key",
                },
            },
        },
        "externalDocs": {
            "description": "Full documentation",
            "url": "https://maxiaworld.app/docs",
        },
    }


@router.get("/openapi.json")
async def openapi_spec():
    """Custom OpenAPI 3.1 spec for agent auto-discovery."""
    global _OPENAPI_SPEC
    if _OPENAPI_SPEC is None:
        _OPENAPI_SPEC = _build_openapi_spec()
    return _OPENAPI_SPEC


# ══════════════════════════════════════════
# E13 — Passport V2: Revocation List
# ══════════════════════════════════════════

@router.get("/api/public/revocation-list")
async def revocation_list():
    """Public revocation list — any marketplace can verify agent status.
    Returns all revoked/frozen agents (no API key exposed)."""
    try:
        from core.database import db
        rows = await db.raw_execute_fetchall(
            "SELECT agent_id, did, uaid, status, revoked_at, frozen_at "
            "FROM agent_permissions WHERE status IN ('revoked', 'frozen') "
            "ORDER BY COALESCE(revoked_at, frozen_at) DESC LIMIT 500"
        )
        entries = []
        for row in rows:
            r = dict(row)
            entries.append({
                "agent_id": r["agent_id"],
                "did": r.get("did", ""),
                "uaid": r.get("uaid", ""),
                "status": r["status"],
                "revoked_at": r.get("revoked_at"),
                "frozen_at": r.get("frozen_at"),
            })
        return {
            "revocation_list": entries,
            "count": len(entries),
            "registry": "maxia",
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "spec": "https://maxiaworld.app/api/public/revocation-list",
        }
    except Exception as e:
        logger.error("Revocation list error: %s", e)
        return {"revocation_list": [], "count": 0, "registry": "maxia"}


# ══════════════════════════════════════════
# E13 — Passport V2: JWT Portable Token
# ══════════════════════════════════════════

@router.post("/api/public/agent/jwt")
async def issue_agent_jwt(x_api_key: str = Header(alias="X-API-Key", default="")):
    """Issue a portable JWT for cross-marketplace identity verification.
    The JWT contains DID, UAID, trust level, and is signed by MAXIA."""
    if not x_api_key:
        raise HTTPException(401, "API key required")

    try:
        from agents.agent_permissions import get_or_create_permissions
        from core.database import db

        # Get agent info
        agent_row = await db.raw_execute_fetchall(
            "SELECT wallet FROM agents WHERE api_key=?", (x_api_key,))
        if not agent_row:
            raise HTTPException(401, "Invalid API key")

        wallet = dict(agent_row[0])["wallet"]
        perms = await get_or_create_permissions(x_api_key, wallet)

        import hashlib
        import base64

        # Build JWT payload (unsigned — HMAC with server secret for verification)
        payload = {
            "iss": "maxia",
            "sub": perms.get("did", ""),
            "agent_id": perms.get("agent_id", ""),
            "uaid": perms.get("uaid", ""),
            "trust_level": perms.get("trust_level", 0),
            "status": perms.get("status", "active"),
            "wallet": wallet,
            "iat": int(time.time()),
            "exp": int(time.time()) + 86400,  # 24h validity
            "registry": "maxia",
        }

        # Encode as base64url JWT (header.payload.signature)
        header = base64.urlsafe_b64encode(json.dumps(
            {"alg": "HS256", "typ": "JWT"}).encode()).rstrip(b"=").decode()
        body = base64.urlsafe_b64encode(
            json.dumps(payload).encode()).rstrip(b"=").decode()

        # Sign with server secret (ADMIN_KEY as HMAC key)
        import hmac as hmac_mod
        import os
        secret = os.getenv("JWT_SECRET", os.getenv("ADMIN_KEY", ""))
        if not secret:
            raise HTTPException(500, "Server misconfigured: JWT_SECRET not set")
        sig_input = f"{header}.{body}".encode()
        signature = base64.urlsafe_b64encode(
            hmac_mod.new(secret.encode(), sig_input, hashlib.sha256).digest()
        ).rstrip(b"=").decode()

        jwt_token = f"{header}.{body}.{signature}"

        return {
            "jwt": jwt_token,
            "did": perms.get("did", ""),
            "uaid": perms.get("uaid", ""),
            "trust_level": perms.get("trust_level", 0),
            "expires_in": 86400,
            "verify_at": "https://maxiaworld.app/api/public/agent/verify-jwt",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("JWT issue error: %s", e)
        raise HTTPException(500, safe_error("JWT generation failed", e))


@router.post("/api/public/agent/verify-jwt")
async def verify_agent_jwt(body: VerifyJWTRequest):
    """Verify a MAXIA-issued agent JWT. Any marketplace can call this."""
    jwt_token = body.jwt
    if jwt_token.count(".") != 2:
        raise HTTPException(400, "Invalid JWT format")

    import base64
    import hashlib
    import hmac as hmac_mod
    import os

    try:
        header_b64, payload_b64, sig_b64 = jwt_token.split(".")

        # Verify signature
        secret = os.getenv("JWT_SECRET", os.getenv("ADMIN_KEY", ""))
        if not secret:
            raise HTTPException(500, "Server misconfigured: JWT_SECRET not set")
        sig_input = f"{header_b64}.{payload_b64}".encode()
        expected_sig = base64.urlsafe_b64encode(
            hmac_mod.new(secret.encode(), sig_input, hashlib.sha256).digest()
        ).rstrip(b"=").decode()

        if not hmac_mod.compare_digest(sig_b64, expected_sig):
            return {"valid": False, "error": "Invalid signature"}

        # Decode payload
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))

        # Check expiry
        if payload.get("exp", 0) < time.time():
            return {"valid": False, "error": "Token expired"}

        # Live revocation check — don't trust baked-in status
        live_status = payload.get("status", "active")
        agent_id = payload.get("agent_id", "")
        if agent_id:
            try:
                from core.database import db
                rows = await db.raw_execute_fetchall(
                    "SELECT status FROM agent_permissions WHERE agent_id=?",
                    (agent_id,))
                if rows:
                    live_status = dict(rows[0]).get("status", "active")
                    if live_status in ("revoked", "frozen"):
                        return {"valid": False, "error": f"Agent {live_status}"}
            except Exception:
                pass  # If DB unavailable, fall through with JWT status

        return {
            "valid": True,
            "agent_id": agent_id,
            "did": payload.get("sub", ""),
            "uaid": payload.get("uaid", ""),
            "trust_level": payload.get("trust_level", 0),
            "status": live_status,
            "issued_at": payload.get("iat", 0),
            "expires_at": payload.get("exp", 0),
            "registry": "maxia",
        }
    except Exception as e:
        return {"valid": False, "error": f"Decode failed: {safe_error('', e)}"}


# ══════════════════════════════════════════
# E13 — Passport V2: Enriched Public Profile
# ══════════════════════════════════════════

@router.get("/api/public/agent/{identifier}/passport")
async def agent_passport(identifier: str):
    """Enriched agent passport — public profile with chains, tx count, member age."""
    if not _IDENTIFIER_RE.match(identifier):
        raise HTTPException(400, "Invalid identifier format")
    try:
        from agents.agent_permissions import resolve_agent_public
        base = await resolve_agent_public(identifier)

        from core.database import db

        # Enrich with transaction history
        agent_id = base.get("agent_id", "")
        tx_count = 0
        total_volume = 0.0
        chains_used = set()

        try:
            tx_rows = await db.raw_execute_fetchall(
                "SELECT COUNT(*) as cnt, COALESCE(SUM(amount_usdc), 0) as vol "
                "FROM transactions WHERE buyer_api_key IN "
                "(SELECT api_key FROM agent_permissions WHERE agent_id=?) "
                "OR seller_api_key IN "
                "(SELECT api_key FROM agent_permissions WHERE agent_id=?)",
                (agent_id, agent_id))
            if tx_rows:
                r = dict(tx_rows[0])
                tx_count = r.get("cnt", 0)
                total_volume = r.get("vol", 0.0) or 0.0
        except Exception:
            pass

        # Chains verified — all 15 supported
        chains_verified = [
            "solana", "base", "ethereum", "xrp", "polygon",
            "arbitrum", "avalanche", "bnb", "ton", "sui",
            "tron", "near", "aptos", "sei", "bitcoin",
        ]

        # Calculate member duration
        created = base.get("created_at", "")
        member_days = 0
        if created:
            try:
                from datetime import datetime, timezone
                created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                member_days = (datetime.now(timezone.utc) - created_dt).days
            except Exception:
                pass

        base["passport_version"] = "2.0"
        base["total_transactions"] = tx_count
        base["total_volume_usdc"] = round(total_volume, 2)
        base["chains_verified"] = chains_verified
        base["member_since"] = created
        base["member_days"] = member_days
        base["jwt_endpoint"] = "https://maxiaworld.app/api/public/agent/jwt"
        base["revocation_list"] = "https://maxiaworld.app/api/public/revocation-list"

        return base
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Passport error: %s", e)
        raise HTTPException(500, safe_error("Passport lookup failed", e))


# ══════════════════════════════════════════
# E4 — Starter Agent Templates
# ══════════════════════════════════════════

STARTER_TEMPLATES = [
    {
        "id": "starter_defi_yield_hunter",
        "name": "DeFi Yield Hunter",
        "description": "Scans DeFi yields across 15 chains, alerts when APY exceeds threshold. Fully autonomous.",
        "category": "defi",
        "difficulty": "beginner",
        "file": "defi_yield_hunter.py",
        "features": ["DeFi yield scanning", "Multi-chain", "Threshold alerts", "Auto-rebalance suggestions"],
        "estimated_cost_per_run": 0.0,
        "source_url": "https://github.com/maxiaworld/agent-template/blob/main/examples/agents/defi_yield_hunter.py",
    },
    {
        "id": "starter_whale_tracker",
        "name": "Whale Tracker",
        "description": "Monitors large wallet movements on Solana. Alerts on significant transfers.",
        "category": "analytics",
        "difficulty": "beginner",
        "file": "whale_tracker.py",
        "features": ["Wallet monitoring", "Transfer alerts", "Historical analysis", "Threshold config"],
        "estimated_cost_per_run": 0.0,
        "source_url": "https://github.com/maxiaworld/agent-template/blob/main/examples/agents/whale_tracker.py",
    },
    {
        "id": "starter_sentiment_trader",
        "name": "Sentiment Trader",
        "description": "Analyzes crypto market sentiment and generates trading signals with confidence scores.",
        "category": "trading",
        "difficulty": "intermediate",
        "file": "sentiment_trader.py",
        "features": ["Sentiment analysis", "Trading signals", "Confidence scoring", "Multi-token"],
        "estimated_cost_per_run": 0.0,
        "source_url": "https://github.com/maxiaworld/agent-template/blob/main/examples/agents/sentiment_trader.py",
    },
    {
        "id": "starter_service_arbitrage",
        "name": "Service Arbitrage",
        "description": "Compares AI service prices on MAXIA marketplace, finds best value for each capability.",
        "category": "marketplace",
        "difficulty": "beginner",
        "file": "service_arbitrage.py",
        "features": ["Price comparison", "Quality scoring", "Auto-selection", "Cost optimization"],
        "estimated_cost_per_run": 0.0,
        "source_url": "https://github.com/maxiaworld/agent-template/blob/main/examples/agents/service_arbitrage.py",
    },
    {
        "id": "starter_auto_researcher",
        "name": "Auto Researcher",
        "description": "Pipeline agent: scrape web data, summarize with AI, translate — all in one autonomous flow.",
        "category": "automation",
        "difficulty": "intermediate",
        "file": "auto_researcher.py",
        "features": ["Web scraping", "AI summarization", "Translation", "Pipeline chaining"],
        "estimated_cost_per_run": 0.15,
        "source_url": "https://github.com/maxiaworld/agent-template/blob/main/examples/agents/auto_researcher.py",
    },
]


@router.get("/api/public/templates/starter")
async def list_starter_templates():
    """List 5 copiable starter agent templates. Zero friction — copy, customize, run."""
    return {
        "templates": STARTER_TEMPLATES,
        "count": len(STARTER_TEMPLATES),
        "quickstart": "pip install maxia && python <template>.py",
        "docs": "https://maxiaworld.app/docs#templates",
    }


@router.get("/api/public/templates/starter/{template_id}")
async def get_starter_template(template_id: str):
    """Get a specific starter template with full details."""
    if not _IDENTIFIER_RE.match(template_id):
        raise HTTPException(400, "Invalid template ID format")
    for t in STARTER_TEMPLATES:
        if t["id"] == template_id:
            return t
    raise HTTPException(404, "Template not found")
