"""Public API — Tool routes (Scrape, Image, Wallet-monitor, Referral, Compliance, Aliases).

Extracted from public_api.py (S34 split).
"""
import logging, time, json, secrets

from fastapi import APIRouter, HTTPException, Header, Request

from marketplace.public_api_shared import (
    _registered_agents, _agent_services, _transactions,
    _load_from_db, _check_safety, _check_rate, _get_agent, _safe_float,
    _validate_solana_address,
)

logger = logging.getLogger(__name__)

router = APIRouter()

@router.post("/scrape")
async def scrape_web(req: dict, x_api_key: str = Header(None, alias="X-API-Key")):
    """Scrape une URL et retourne le contenu structure. Requiert API key."""
    if not x_api_key:
        raise HTTPException(401, "Header X-API-Key requis")
    _get_agent(x_api_key)
    _check_rate(x_api_key)

    url = req.get("url", "")
    if not url:
        raise HTTPException(400, "Champ 'url' requis")

    # Fix #1: Clamp max_text_length bounds
    try:
        max_text_length = max(100, min(50000, int(req.get("max_text_length", 10000))))
    except (TypeError, ValueError):
        max_text_length = 10000

    # Fix #7: String length limit on URL
    url = url.strip()[:2000]

    from ai.web_scraper import scrape_url
    return await scrape_url(
        url=url,
        extract_links=req.get("extract_links", True),
        extract_images=req.get("extract_images", True),
        max_text_length=max_text_length,
    )


@router.post("/scrape/batch")
async def scrape_batch(req: dict, x_api_key: str = Header(None, alias="X-API-Key")):
    """Scrape plusieurs URLs (max 5). Requiert API key."""
    if not x_api_key:
        raise HTTPException(401, "Header X-API-Key requis")
    _get_agent(x_api_key)
    _check_rate(x_api_key)

    urls = req.get("urls", [])
    if not urls:
        raise HTTPException(400, "Champ 'urls' requis (liste)")

    # Fix #1: Clamp max_text_length bounds
    try:
        max_text_length = max(100, min(50000, int(req.get("max_text_length", 5000))))
    except (TypeError, ValueError):
        max_text_length = 5000

    from ai.web_scraper import scrape_multiple
    return await scrape_multiple(urls[:5], max_text_length=max_text_length)


# ══════════════════════════════════════════
#  IMAGE GENERATION (Art.26)
# ══════════════════════════════════════════

@router.post("/image/generate")
async def generate_image(req: dict, x_api_key: str = Header(None, alias="X-API-Key")):
    """Genere une image a partir d un prompt. Requiert API key."""
    if not x_api_key:
        raise HTTPException(401, "Header X-API-Key requis")
    agent = _get_agent(x_api_key)
    _check_rate(x_api_key)

    # Fix #7: String length limit on prompt
    prompt = req.get("prompt", "").strip()[:5000] if isinstance(req.get("prompt"), str) else ""
    if not prompt:
        raise HTTPException(400, "Champ 'prompt' requis")
    _check_safety(prompt, "prompt")

    # Fix #1: Int validation bounds for image parameters
    try:
        width = max(64, min(2048, int(req.get("width", 1024))))
        height = max(64, min(2048, int(req.get("height", 1024))))
        steps = max(1, min(50, int(req.get("steps", 4))))
        seed = max(0, min(999999999, int(req.get("seed", 0))))
    except (TypeError, ValueError):
        raise HTTPException(400, "Invalid image parameters (width, height, steps, seed must be integers)")

    from ai.image_gen import generate_image as gen_img
    result = await gen_img(
        prompt=prompt,
        model=req.get("model", "flux-schnell"),
        width=width,
        height=height,
        steps=steps,
        seed=seed,
    )

    if result.get("success"):
        agent["total_spent"] += 0.10  # $0.10 par image

    return result


@router.get("/image/models")
async def image_models():
    """Liste les modeles de generation d images. Sans auth."""
    from ai.image_gen import list_models
    return list_models()



# ══════════════════════════════════════════
#  WALLET MONITOR (Art.27)
# ══════════════════════════════════════════

@router.post("/wallet-monitor/add")
async def add_wallet_monitor(req: dict, x_api_key: str = Header(None, alias="X-API-Key")):
    """Ajouter un wallet a surveiller. Requiert API key."""
    if not x_api_key:
        raise HTTPException(401, "Header X-API-Key requis")
    agent = _get_agent(x_api_key)

    # Fix #7: String length limit + Fix #13: Validate Solana address
    wallet = req.get("wallet", "").strip()[:50] if isinstance(req.get("wallet"), str) else ""
    if not wallet:
        raise HTTPException(400, "Champ 'wallet' requis")
    _validate_solana_address(wallet, "wallet")

    # Fix #4: Float validation + Fix #7: String length limit on webhook_url
    try:
        min_sol_change = float(req.get("min_sol_change", 0.1))
    except (TypeError, ValueError):
        min_sol_change = 0.1
    webhook_url = req.get("webhook_url", "").strip()[:500] if isinstance(req.get("webhook_url"), str) else ""

    from features.wallet_monitor import add_monitor
    return await add_monitor(
        api_key=x_api_key,
        owner_name=agent["name"],
        wallet_address=wallet,
        webhook_url=webhook_url,
        alert_types=req.get("alert_types", None),
        min_sol_change=min_sol_change,
    )


@router.post("/wallet-monitor/remove")
async def remove_wallet_monitor(req: dict, x_api_key: str = Header(None, alias="X-API-Key")):
    """Arreter la surveillance d un wallet. Requiert API key."""
    if not x_api_key:
        raise HTTPException(401, "Header X-API-Key requis")

    monitor_id = req.get("monitor_id", "")
    if not monitor_id:
        raise HTTPException(400, "Champ 'monitor_id' requis")

    from features.wallet_monitor import remove_monitor
    return await remove_monitor(x_api_key, monitor_id)


@router.get("/wallet-monitor/my-monitors")
async def my_wallet_monitors(x_api_key: str = Header(None, alias="X-API-Key")):
    """Liste mes moniteurs actifs. Requiert API key."""
    if not x_api_key:
        raise HTTPException(401, "Header X-API-Key requis")
    _get_agent(x_api_key)

    from features.wallet_monitor import get_my_monitors
    return get_my_monitors(x_api_key)


@router.get("/wallet-monitor/alerts")
async def my_wallet_alerts(x_api_key: str = Header(None, alias="X-API-Key"), limit: int = 50):
    """Recupere mes alertes. Requiert API key."""
    if not x_api_key:
        raise HTTPException(401, "Header X-API-Key requis")
    _get_agent(x_api_key)

    from features.wallet_monitor import get_alerts
    return get_alerts(x_api_key, limit)


# ══════════════════════════════════════════
# REFERRAL PROGRAM — 50% commission share
# ══════════════════════════════════════════

REFERRAL_SHARE_PCT = 50  # referrer gets 50% of MAXIA's commission on referee's transactions

@router.get("/referral/my-code")
async def referral_my_code(x_api_key: str = Header(None, alias="X-API-Key")):
    """Get your referral code. Share it to earn 50% of referred agents' commissions."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    agent = _get_agent(x_api_key)
    # PRO-A7: Look up independent referral code from DB (not derived from API key)
    from core.database import db
    code = ""
    try:
        agent_row = await db.raw_execute_fetchall(
            "SELECT referral_code FROM agents WHERE api_key=? LIMIT 1", (x_api_key,))
        if agent_row:
            code = (agent_row[0]["referral_code"] if isinstance(agent_row[0], dict) else agent_row[0][0]) or ""
    except Exception:
        pass
    # Generate one if missing (legacy agent registered before migration 9)
    if not code:
        import secrets as _secrets
        code = _secrets.token_hex(4).upper()
        try:
            await db.raw_execute("UPDATE agents SET referral_code=? WHERE api_key=?", (code, x_api_key))
        except Exception:
            pass
    rows = await db.raw_execute_fetchall(
        "SELECT COUNT(*) AS cnt FROM referrals WHERE referrer=?", (x_api_key,))
    count = rows[0]["cnt"] if rows else 0
    # Calculate earnings
    earnings = 0.0
    try:
        rows2 = await db.raw_execute_fetchall(
            "SELECT data FROM referrals WHERE referrer=?", (x_api_key,))
        for r in rows2:
            d = json.loads(r["data"])
            earnings += d.get("earnedUsdc", 0)
    except Exception:
        pass
    return {
        "referral_code": code,
        "share_url": f"https://maxiaworld.app/api/public/register?referral_code={code}",
        "referrals": count,
        "earnings_usdc": round(earnings, 4),
        "commission": f"{REFERRAL_SHARE_PCT}% of MAXIA's commission on referred agents' transactions",
        "how_it_works": [
            "1. Share your referral code or link",
            "2. New agent registers with your code",
            "3. You earn 50% of every commission MAXIA takes on their transactions",
            "4. Passive income forever — swaps, service buys, GPU rentals",
        ],
    }


@router.get("/referral/my-referrals")
async def referral_list(x_api_key: str = Header(None, alias="X-API-Key")):
    """List agents I referred."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    agent = _get_agent(x_api_key)
    from core.database import db
    rows = await db.raw_execute_fetchall(
        "SELECT data FROM referrals WHERE referrer=? ORDER BY rowid DESC", (x_api_key,))
    referrals = []
    total_earned = 0.0
    for r in rows:
        d = json.loads(r["data"])
        earned = d.get("earnedUsdc", 0)
        total_earned += earned
        referrals.append({
            "referee_name": d.get("referee_name", d.get("referee", "")[:8] + "..."),
            "registered_at": d.get("registeredAt", 0),
            "earned_usdc": round(earned, 4),
        })
    return {"referrals": referrals, "total": len(referrals),
            "total_earned_usdc": round(total_earned, 4)}


@router.get("/referral/{api_key}")
async def referral_stats(api_key: str):
    """Public referral stats for any agent. Returns referral count and total commission earned."""
    await _load_from_db()
    # Validate the api_key exists
    agent = _registered_agents.get(api_key)
    if not agent:
        raise HTTPException(404, "Agent not found")

    # PRO-A7: Look up independent referral code from DB
    from core.database import db
    referral_code = ""
    try:
        agent_row = await db.raw_execute_fetchall(
            "SELECT referral_code FROM agents WHERE api_key=? LIMIT 1", (api_key,))
        if agent_row:
            referral_code = (agent_row[0]["referral_code"] if isinstance(agent_row[0], dict) else agent_row[0][0]) or ""
    except Exception:
        pass
    if not referral_code:
        import secrets as _secrets
        referral_code = _secrets.token_hex(4).upper()
        try:
            await db.raw_execute("UPDATE agents SET referral_code=? WHERE api_key=?", (referral_code, api_key))
        except Exception:
            pass

    # Count referrals
    rows = await db.raw_execute_fetchall(
        "SELECT COUNT(*) AS cnt FROM referrals WHERE referrer=?", (api_key,))
    count = rows[0]["cnt"] if rows else 0

    # Total commission earned
    earnings = 0.0
    try:
        rows2 = await db.raw_execute_fetchall(
            "SELECT data FROM referrals WHERE referrer=?", (api_key,))
        for r in rows2:
            d = json.loads(r["data"])
            earnings += d.get("earnedUsdc", 0)
    except Exception:
        pass

    return {
        "agent_name": agent.get("name", ""),
        "referral_code": referral_code,
        "total_referred": count,
        "total_commission_earned_usdc": round(earnings, 4),
        "commission_rate": f"{REFERRAL_SHARE_PCT}% of MAXIA's commission",
        "share_url": f"https://maxiaworld.app/api/public/register?referral_code={referral_code}",
    }


# ══════════════════════════════════════════
#  COMPLIANCE — OFAC Sanctions Check (Art.25)
# ══════════════════════════════════════════

@router.get("/compliance/check-wallet")
async def compliance_check_wallet(address: str = ""):
    """Check if a wallet address is on the OFAC sanctions list. Free, no auth needed."""
    if not address or len(address) < 20:
        raise HTTPException(400, "address query parameter required (min 20 chars)")
    result = check_ofac_wallet(address)
    return {
        "address": address,
        "sanctioned": result["sanctioned"],
        "risk_level": result["risk"],
        "provider": "MAXIA built-in (OFAC SDN subset + Tornado Cash + Lazarus Group)",
        "note": "For full coverage, integrate Chainalysis or TRM Labs API.",
    }


# ══════════════════════════════════════════
#  RATE LIMIT TIERS INFO
# ══════════════════════════════════════════

@router.get("/rate-limits")
async def rate_limits_info(x_api_key: str = Header(None, alias="X-API-Key")):
    """Check your current rate limit tier and usage."""
    from core.security import RATE_LIMIT_TIERS, get_agent_rate_tier
    if not x_api_key:
        return {
            "current_tier": "free",
            "tiers": RATE_LIMIT_TIERS,
            "note": "Register with POST /api/public/register to get an API key.",
        }
    await _load_from_db()
    try:
        _get_agent(x_api_key)
    except Exception:
        return {"current_tier": "free", "tiers": RATE_LIMIT_TIERS}

    tier_name = await get_agent_rate_tier(x_api_key)
    usage = await check_rate_limit_tiered(x_api_key)

    return {
        "current_tier": tier_name,
        "tier_details": RATE_LIMIT_TIERS[tier_name],
        "usage": {
            "remaining_per_min": usage.get("remaining_min", "?"),
            "remaining_per_day": usage.get("remaining_day", "?"),
        },
        "all_tiers": RATE_LIMIT_TIERS,
        "upgrade": "Contact team@maxiaworld.app for Pro/Enterprise access.",
    }


# ══════════════════════════════════════════
#  AGENT BUNDLE — 1 API call to get everything
# ══════════════════════════════════════════

@router.post("/agents/bundle")
async def agent_bundle(request: Request):
    """Register an AI agent and get EVERYTHING in one API call.
    Returns: API key, wallet setup, MCP tools access, A2A endpoint,
    marketplace listing, leaderboard entry, referral code.

    This is the fastest way to go from zero to live on MAXIA.
    One POST, one response, your agent is ready to trade.
    """
    body = await request.json()
    wallet = body.get("wallet", "").strip()[:50] if isinstance(body.get("wallet"), str) else ""
    name = body.get("name", "").strip()[:100] if isinstance(body.get("name"), str) else ""
    description = body.get("description", "").strip()[:2000] if isinstance(body.get("description"), str) else ""

    if not wallet or not name:
        raise HTTPException(400, "wallet and name required")

    if len(name) < 2:
        raise HTTPException(400, "name must be at least 2 characters")
    if len(wallet) < 20:
        raise HTTPException(400, "wallet address too short")

    # Art.1 — Content safety on all inputs
    _check_safety(name, "name")
    if description:
        _check_safety(description, "description")

    # OFAC sanctions check
    require_ofac_clear(wallet, "bundle registration wallet")

    await _load_from_db()

    from core.database import db

    # 1. Register agent (generate secure API key)
    api_key = f"maxia_{secrets.token_hex(24)}"
    agent = {
        "api_key": api_key,
        "name": name,
        "wallet": wallet,
        "description": description or f"AI agent {name}",
        "registered_at": int(time.time()),
        "volume_30d": 0.0,
        "total_spent": 0.0,
        "total_earned": 0.0,
        "tier": "BRONZE",
        "requests_today": 0,
        "services_listed": 0,
    }
    _registered_agents[api_key] = agent
    try:
        await db.save_agent(agent)
    except Exception as e:
        logger.error("DB save agent error: %s", e)

    # 2. PRO-A7: Generate independent referral code (not derived from API key or wallet)
    referral_code = secrets.token_hex(4).upper()
    try:
        await db.raw_execute("UPDATE agents SET referral_code=? WHERE api_key=?", (referral_code, api_key))
    except Exception as e:
        logger.warning("Failed to store bundle referral_code: %s", e)

    # 3. Build response with everything
    return {
        "success": True,
        "message": f"Welcome to MAXIA, {name}! Your agent is live.",

        # Identity
        "api_key": api_key,
        "wallet": wallet,
        "agent_name": name,
        "referral_code": referral_code,

        # Endpoints (everything your agent can do)
        "endpoints": {
            "swap_quote": "GET /api/public/crypto/quote?from=SOL&to=USDC&amount=10",
            "swap_execute": "POST /api/public/crypto/swap",
            "gpu_rent": "POST /api/public/gpu/rent",
            "gpu_tiers": "GET /api/public/gpu/tiers",
            "stocks_list": "GET /api/public/stocks",
            "stocks_buy": "POST /api/public/stocks/buy",
            "defi_yields": "GET /api/public/defi/best-yield?asset=USDC",
            "discover_services": "GET /api/public/discover",
            "register_service": "POST /api/public/services/register",
            "execute_service": "POST /api/public/services/{id}/execute",
            "credit_score": f"GET /api/public/credit-score/{wallet}",
            "mcp_tools": "GET /mcp/tools",
            "a2a_card": "GET /.well-known/agent.json",
            "prices": "GET /api/public/prices",
        },

        # Your current tier
        "tier": tier_info,

        # Commissions (first swap is FREE)
        "first_swap_free": True,
        "commission_tiers": {
            "BRONZE": "0.10%",
            "SILVER": "0.05%",
            "GOLD": "0.03%",
            "WHALE": "0.01%",
        },

        # Quick start code
        "quickstart_python": f'''import httpx
# Your agent is live! Try a swap:
r = httpx.get("https://maxiaworld.app/api/public/crypto/quote",
    params={{"from": "SOL", "to": "USDC", "amount": 10, "wallet": "{wallet}"}})
print(r.json())
''',

        # What's included (free)
        "included": [
            "107 tokens on 7 swap chains",
            "25 tokenized stocks (Pyth real-time)",
            "13 GPU tiers at cost (0% markup)",
            "DeFi yield scanner (14 chains)",
            "46 MCP tools",
            "A2A Protocol support",
            "On-chain escrow with AI disputes",
            "Agent leaderboard (AAA-CCC grades)",
            "Referral program (10% lifetime)",
            "First swap FREE (0% commission)",
        ],
    }


# ══════════════════════════════════════════
#  ROUTE ALIASES — alternate paths for discoverability
# ══════════════════════════════════════════

@router.get("/chains")
async def chains_alias():
    """Alias for /chain-support."""
    from marketplace.public_api_discover import chain_support
    return await chain_support()


@router.get("/agents/bundle")
async def agents_bundle_info():
    """GET info for /agents/bundle (POST required to register)."""
    return {
        "endpoint": "/api/public/agents/bundle",
        "method": "POST",
        "description": "Register an AI agent and get everything in one API call",
        "required_fields": {"wallet": "Solana or EVM address", "name": "Agent name (2+ chars)"},
        "optional_fields": {"description": "Agent description (max 2000 chars)"},
        "returns": "API key, MCP tools, A2A endpoint, marketplace listing, referral code",
        "example": {"wallet": "YOUR_WALLET_ADDRESS", "name": "MyAgent", "description": "AI trading bot"},
    }


@router.get("/trading/portfolio")
async def trading_portfolio_alias(x_api_key: str = Header(None, alias="X-API-Key")):
    """Trading portfolio — positions, P&L across all instruments."""
    if not x_api_key:
        return {"error": "Header X-API-Key required", "positions": [], "total_pnl_usdc": 0}
    agent = _get_agent(x_api_key)
    results = {"wallet": agent.get("wallet", ""), "positions": [], "total_pnl_usdc": 0}
    results["stocks"] = []
    # Swap history
    try:
        from core.database import db as _db
        swaps = await _db.raw_execute_fetchall(
            "SELECT data FROM transactions WHERE json_extract(data, '$.buyer') = ? ORDER BY created_at DESC LIMIT 20",
            (x_api_key,))
        results["recent_swaps"] = len(swaps)
    except Exception:
        results["recent_swaps"] = 0
    return results


@router.get("/info")
async def public_info():
    """Informations generales sur MAXIA."""
    return {
        "name": "MAXIA", "version": "12.0.0",
        "description": "AI-to-AI Marketplace on 15 Blockchains",
        "chains": 15, "chains_code_ready": 8, "tokens": 65, "mcp_tools": 47,
        "ai_services": 17, "gpu_tiers": 13, "stocks": 25,
        "protocols": ["REST", "MCP", "A2A", "AIP"],
        "docs": "https://maxiaworld.app/architecture",
        "api_base": "https://maxiaworld.app/api/public",
    }


@router.get("/pricing")
async def public_pricing():
    """Pricing des services MAXIA."""
    from core.config import get_commission_bps
    return {
        "marketplace": {
            "BRONZE": {"commission_pct": 1.5, "volume_under": 500},
            "GOLD": {"commission_pct": 0.5, "volume_range": "500-5000"},
            "WHALE": {"commission_pct": 0.1, "volume_over": 5000},
        },
        "swap": {
            "BRONZE": {"commission_bps": 10, "pct": "0.10%"},
            "SILVER": {"commission_bps": 5, "pct": "0.05%"},
            "GOLD": {"commission_bps": 3, "pct": "0.03%"},
            "WHALE": {"commission_bps": 1, "pct": "0.01%"},
        },
        "ai_services": {"code_gen": 3.99, "audit": 9.99, "sentiment": 1.99},
        "gpu": "See /api/public/gpu/tiers",
        "free_tier": {"requests_per_day": 100, "swap_commission": "0.10%"},
    }
