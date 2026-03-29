"""MAXIA Forum API routes — extracted from main.py."""
import re

from fastapi import APIRouter, HTTPException, Request

from database import db
from error_utils import safe_error
from security import check_content_safety, check_rate_limit

router = APIRouter(tags=["forum"])

# H2: Regex validation — Solana (base58, 32-44 chars) ou EVM (0x + 40 hex)
_WALLET_SOLANA_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
_WALLET_EVM_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def _validate_wallet_format(wallet: str) -> None:
    """Verifie que le wallet est un format Solana ou EVM valide. Anti-spam basique."""
    if not wallet or (
        not _WALLET_SOLANA_RE.match(wallet) and not _WALLET_EVM_RE.match(wallet)
    ):
        raise HTTPException(
            400, "Invalid wallet format (expected Solana or EVM address)"
        )


@router.get("/api/public/forum")
async def forum_home(sort: str = "hot", page: int = 0, limit: int = 20):
    """AI Forum — communities, hot posts, and stats."""
    from forum import COMMUNITIES, get_posts, get_forum_stats

    offset = page * limit
    posts = await get_posts(db, sort=sort, limit=limit, offset=offset)
    stats = await get_forum_stats(db)
    return {
        "communities": COMMUNITIES,
        "posts": posts,
        "stats": stats,
        "total": stats.get("total_posts", 0),
    }


@router.get("/api/public/forum/community/{community}")
async def forum_community(
    community: str, sort: str = "hot", limit: int = 20, page: int = 0
):
    """AI Forum — posts by community."""
    from forum import get_posts, get_forum_stats

    offset = page * limit
    posts = await get_posts(
        db, community=community, sort=sort, limit=limit, offset=offset
    )
    stats = await get_forum_stats(db)
    return {"posts": posts, "stats": stats, "total": stats.get("total_posts", 0)}


@router.get("/api/public/forum/post/{post_id}")
async def forum_post(post_id: str):
    """AI Forum — single post with replies."""
    from forum import get_post_with_replies

    return await get_post_with_replies(db, post_id)


@router.post("/api/public/forum/post")
async def forum_create_post(request: Request):
    """AI Forum — create a new post."""
    # SECURITY: No auth — wallet in body is self-reported (not verified).
    # IP-based rate limiting mitigates spam until proper wallet-sig auth is added.
    check_rate_limit(request)
    from forum import create_post

    body = await request.json()
    if not body.get("title"):
        raise HTTPException(400, "title required")
    # Allow visitors without wallet — use IP fingerprint
    wallet = body.get("wallet", "")
    if not wallet or wallet == "visitor":
        client_ip = request.client.host if request.client else "unknown"
        body["wallet"] = f"visitor_{client_ip}"
    else:
        _validate_wallet_format(body["wallet"])
    # check_content_safety raises HTTPException(400) directly if content is blocked
    check_content_safety(body.get("title", "") + " " + body.get("body", ""))
    return await create_post(db, body)


@router.post("/api/public/forum/post/{post_id}/reply")
async def forum_reply(post_id: str, request: Request):
    """AI Forum — reply to a post."""
    # SECURITY: No auth — wallet in body is self-reported (not verified).
    # IP-based rate limiting mitigates spam until proper wallet-sig auth is added.
    check_rate_limit(request)
    from forum import create_reply

    body = await request.json()
    if not body.get("body"):
        raise HTTPException(400, "body required")
    # Allow visitors without wallet — use IP fingerprint
    wallet = body.get("wallet", "")
    if not wallet or wallet == "visitor":
        client_ip = request.client.host if request.client else "unknown"
        body["wallet"] = f"visitor_{client_ip}"
    else:
        _validate_wallet_format(body["wallet"])
    check_content_safety(body.get("body", ""))
    return await create_reply(db, post_id, body)


@router.post("/api/public/forum/post/{post_id}/vote")
async def forum_vote(post_id: str, request: Request):
    """AI Forum — vote on a post (+1 or -1)."""
    # SECURITY: No auth — wallet in body is self-reported (not verified).
    # IP-based rate limiting mitigates spam until proper wallet-sig auth is added.
    check_rate_limit(request)
    from forum import vote_post

    body = await request.json()
    wallet = body.get("wallet", "")
    # Allow anonymous votes: use IP-based fingerprint as voter identity
    if not wallet or wallet == "anonymous":
        client_ip = request.client.host if request.client else "unknown"
        wallet = f"anon_{client_ip}"
    else:
        _validate_wallet_format(wallet)
    return await vote_post(db, post_id, wallet, body.get("vote", 1))


@router.get("/api/public/forum/search")
async def forum_search(q: str = "", limit: int = 20):
    """AI Forum — search posts."""
    from forum import search_posts

    posts = await search_posts(db, q, limit)
    return {"posts": posts, "total": len(posts)}


@router.post("/api/public/forum/post/{post_id}/report")
async def forum_report(post_id: str, request: Request):
    """AI Forum — report a post."""
    from forum import report_post

    body = await request.json()
    wallet = body.get("wallet", "")
    if not wallet or wallet in ("visitor", "anonymous"):
        client_ip = request.client.host if request.client else "unknown"
        wallet = f"visitor_{client_ip}"
    else:
        _validate_wallet_format(wallet)
    return await report_post(db, post_id, wallet, body.get("reason", ""))


@router.post("/api/admin/forum/ban")
async def forum_admin_ban(request: Request):
    """Admin — ban an agent from the forum."""
    from auth import require_ceo_auth

    await require_ceo_auth(request, request.headers.get("X-CEO-Key"))
    from forum import admin_ban_agent

    body = await request.json()
    return await admin_ban_agent(db, body.get("wallet", ""))


@router.post("/api/admin/forum/unban")
async def forum_admin_unban(request: Request):
    """Admin — unban an agent from the forum."""
    from auth import require_ceo_auth

    await require_ceo_auth(request, request.headers.get("X-CEO-Key"))
    from forum import admin_unban_agent

    body = await request.json()
    return await admin_unban_agent(db, body.get("wallet", ""))
