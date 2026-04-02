"""MAXIA V12 — Marketplace inline routes (listings, app-store, forum create, creator marketplace)"""
import logging
import re
import json
import uuid
import time
from fastapi import APIRouter, HTTPException, Depends, Request
from auth import require_auth
from security import check_content_safety
from error_utils import safe_error
from models import ListingCreateRequest, CommandRequest

logger = logging.getLogger(__name__)
router = APIRouter()

# Wallet validation regex (Solana + EVM)
_WALLET_SOLANA_RE = re.compile(r'^[1-9A-HJ-NP-Za-km-z]{32,44}$')
_WALLET_EVM_RE = re.compile(r'^0x[0-9a-fA-F]{40}$')


def _validate_wallet_format(wallet: str) -> None:
    """Verifie que le wallet est un format Solana ou EVM valide."""
    if not wallet or (
        not _WALLET_SOLANA_RE.match(wallet) and not _WALLET_EVM_RE.match(wallet)
    ):
        raise HTTPException(
            400, "Invalid wallet format (expected Solana or EVM address)"
        )


# ── App Store API endpoints ──

@router.get("/api/public/app-store")
async def app_store_home():
    """AI Agent App Store — featured agents and categories."""
    from app_store import CATEGORIES, get_featured_agents
    from database import db
    featured = await get_featured_agents(db)
    return {
        "categories": CATEGORIES,
        "featured": featured,
        "total_agents": len(featured),
    }

@router.get("/api/public/app-store/category/{category}")
async def app_store_category(category: str):
    """AI Agent App Store — agents by category."""
    from app_store import get_agents_by_category, CATEGORIES_MAP
    from database import db
    if category not in CATEGORIES_MAP:
        raise HTTPException(404, f"Category '{category}' not found")
    agents = await get_agents_by_category(db, category)
    return {
        "category": CATEGORIES_MAP[category],
        "agents": agents,
        "total": len(agents),
    }

@router.get("/api/public/app-store/search")
async def app_store_search(q: str = ""):
    """AI Agent App Store — search agents by name or description."""
    from app_store import search_agents
    from database import db
    if not q or len(q) < 2:
        raise HTTPException(400, "Query must be at least 2 characters")
    # Sanitize query length
    q = q[:100]
    agents = await search_agents(db, q)
    return {
        "query": q,
        "results": agents,
        "total": len(agents),
    }


# Forum POST — defined on router directly to avoid BaseHTTPMiddleware body streaming deadlock
@router.post("/api/public/forum/create")
async def forum_create_post_direct(request: Request):
    import json as _json
    from forum import create_post
    from database import db
    raw = await request.body()
    body = _json.loads(raw) if raw else {}
    if not body.get("title"):
        raise HTTPException(400, "title required")
    wallet = body.get("wallet", "")
    if not wallet or wallet == "visitor":
        client_ip = request.client.host if request.client else "unknown"
        body["wallet"] = f"visitor_{client_ip}"
    check_content_safety(body.get("title", "") + " " + body.get("body", ""))
    return await create_post(db, body)


# ── Creator Marketplace ──
@router.get("/api/public/marketplace")
async def marketplace_home():
    from creator_marketplace import TOOL_CATEGORIES, get_tools, ensure_marketplace_tables
    from database import db
    await ensure_marketplace_tables(db)
    tools = await get_tools(db, sort="popular", limit=50)
    total_value = sum(t.get("price_usdc", 0) for t in tools)
    return {
        "categories": TOOL_CATEGORIES,
        "tools": tools,
        "total": len(tools),
        "revenue_split": {"creator": "90%", "platform": "10%"},
        "stats": {
            "total_tools": len(tools),
            "total_creators": len(set(t.get("creator_wallet", "") for t in tools)),
            "revenue_shared": round(total_value * 0.9, 2),
        },
    }

@router.get("/api/public/marketplace/category/{category}")
async def marketplace_category(category: str, sort: str = "popular", limit: int = 20):
    from creator_marketplace import get_tools
    from database import db
    return await get_tools(db, category=category, sort=sort, limit=limit)

@router.get("/api/public/marketplace/tool/{tool_id}")
async def marketplace_tool_detail(tool_id: str):
    from creator_marketplace import get_tool_detail
    from database import db
    return await get_tool_detail(db, tool_id)

@router.post("/api/public/marketplace/publish")
async def marketplace_publish(request: Request):
    from creator_marketplace import publish_tool
    from database import db
    body = await request.json()
    if not body.get("name") or not body.get("creator_wallet"):
        raise HTTPException(400, "name and creator_wallet required")
    # check_content_safety raises HTTPException(400) directly if content is blocked
    check_content_safety(body.get("name", "") + " " + body.get("description", ""))
    return await publish_tool(db, body)

@router.post("/api/public/marketplace/tool/{tool_id}/purchase")
async def marketplace_purchase(tool_id: str, request: Request):
    from security import check_rate_limit
    await check_rate_limit(request)
    from creator_marketplace import purchase_tool
    from database import db
    body = await request.json()
    wallet = body.get("wallet", "")
    if not wallet or len(wallet) < 20:
        raise HTTPException(400, "Valid wallet address required")
    _validate_wallet_format(wallet)
    return await purchase_tool(db, tool_id, wallet)

@router.post("/api/public/marketplace/tool/{tool_id}/review")
async def marketplace_review(tool_id: str, request: Request):
    from creator_marketplace import review_tool
    from database import db
    body = await request.json()
    return await review_tool(db, tool_id, body.get("wallet", ""), body.get("rating", 5), body.get("review", ""))

@router.post("/api/public/marketplace/tool/{tool_id}/update")
async def marketplace_update(tool_id: str, request: Request):
    from security import check_rate_limit
    await check_rate_limit(request)
    from creator_marketplace import update_tool_version
    from database import db
    body = await request.json()
    creator_wallet = body.get("creator_wallet", "")
    if not creator_wallet or len(creator_wallet) < 20:
        raise HTTPException(400, "Valid creator_wallet required")
    _validate_wallet_format(creator_wallet)
    return await update_tool_version(db, tool_id, creator_wallet, body)

@router.get("/api/public/marketplace/search")
async def marketplace_search(q: str = "", limit: int = 20):
    from creator_marketplace import search_tools
    from database import db
    return await search_tools(db, q, limit)

@router.get("/api/creator/stats/{wallet}")
async def creator_stats(wallet: str):
    from creator_marketplace import get_creator_stats
    from database import db
    return await get_creator_stats(db, wallet)


# ═══════════════════════════════════════════════════════════
#  MARKETPLACE (Art.7 + Art.8)
# ═══════════════════════════════════════════════════════════

@router.get("/api/marketplace/listings")
async def get_listings(type: str = None, max_price: float = None):
    from database import db
    listings = await db.get_listings()
    if type:
        listings = [l for l in listings if l.get("type") == type]
    if max_price:
        listings = [l for l in listings if l.get("priceUsdc", 0) <= max_price]
    return listings


@router.post("/api/marketplace/listings")
async def create_listing(req: ListingCreateRequest, wallet: str = Depends(require_auth)):
    from database import db
    check_content_safety(req.name, "name")
    check_content_safety(req.description, "description")
    l = {
        "id": str(uuid.uuid4()), "agentId": wallet, "name": req.name,
        "type": req.type, "description": req.description,
        "priceUsdc": req.price_usdc, "rating": 5.0, "txCount": 0,
        "createdAt": int(time.time()),
    }
    await db.save_listing(l)
    return l


@router.post("/api/marketplace/commands")
async def create_command(req: CommandRequest, wallet: str = Depends(require_auth)):
    from database import db
    from solana_verifier import verify_transaction
    from config import TREASURY_ADDRESS
    check_content_safety(req.prompt, "prompt")
    if await db.tx_already_processed(req.tx_signature):
        raise HTTPException(400, "Transaction deja utilisee.")
    # Verification complete: montant + destinataire
    tx_result = await verify_transaction(
        tx_signature=req.tx_signature,
        expected_amount_usdc=req.amount_usdc,
        expected_recipient=TREASURY_ADDRESS,
    )
    if not tx_result.get("valid"):
        raise HTTPException(400, f"Transaction invalide: {tx_result.get('error', 'verification echouee')}")
    cmd = {
        "commandId": str(uuid.uuid4()), "serviceId": req.service_id,
        "buyerWallet": wallet, "txSignature": req.tx_signature,
        "prompt": req.prompt, "status": "pending",
        "createdAt": int(time.time()),
        "verified_amount": tx_result.get("amount_usdc", 0),
    }
    await db.save_command(cmd)
    await db.record_transaction(wallet, req.tx_signature, req.amount_usdc, "marketplace")
    return {"commandId": cmd["commandId"], "status": "pending"}


@router.get("/api/marketplace/commands/{command_id}")
async def get_command(command_id: str, wallet: str = Depends(require_auth)):
    from database import db
    rows = await db.raw_execute_fetchall("SELECT data FROM commands WHERE command_id=?", (command_id,))
    row = rows[0] if rows else None
    if not row:
        raise HTTPException(404, "Commande introuvable.")
    cmd = json.loads(row[0])
    if cmd.get("buyerWallet") != wallet:
        raise HTTPException(403, "Acces refuse.")
    return cmd
