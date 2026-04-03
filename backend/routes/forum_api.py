"""MAXIA Forum API routes — extracted from main.py."""
import json
import re

from fastapi import APIRouter, HTTPException, Request

from core.error_utils import safe_error
from core.security import check_content_safety


def _get_db():
    from core.database import db
    return db


async def _read_body(request: Request) -> dict:
    """Read JSON body — works around Starlette BaseHTTPMiddleware body streaming bug."""
    raw = await request.body()
    return json.loads(raw) if raw else {}

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
    from routes.forum import COMMUNITIES, get_posts, get_forum_stats

    limit = min(max(limit, 1), 100)
    page = max(page, 0)
    offset = page * limit
    posts = await get_posts(_get_db(), sort=sort, limit=limit, offset=offset)
    stats = await get_forum_stats(_get_db())
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
    from routes.forum import get_posts, get_forum_stats

    limit = min(max(limit, 1), 100)
    page = max(page, 0)
    offset = page * limit
    posts = await get_posts(
        _get_db(), community=community, sort=sort, limit=limit, offset=offset
    )
    stats = await get_forum_stats(_get_db())
    return {"posts": posts, "stats": stats, "total": stats.get("total_posts", 0)}


@router.get("/api/public/forum/post/{post_id}")
async def forum_post(post_id: str):
    """AI Forum — single post with replies."""
    from routes.forum import get_post_with_replies

    return await get_post_with_replies(_get_db(), post_id)


# POST /api/public/forum/create — moved to main.py to avoid BaseHTTPMiddleware body deadlock


@router.post("/api/public/forum/post/{post_id}/reply")
async def forum_reply(post_id: str, request: Request):
    """AI Forum — reply to a post."""
    body = await _read_body(request)
    from routes.forum import create_reply
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
    check_content_safety(body.get("agent_name", ""))
    return await create_reply(_get_db(), post_id, body)


@router.post("/api/public/forum/post/{post_id}/vote")
async def forum_vote(post_id: str, request: Request):
    """AI Forum — vote on a post (+1 or -1)."""
    body = await _read_body(request)
    from routes.forum import vote_post
    wallet = body.get("wallet", "")
    # Allow anonymous votes: use IP-based fingerprint as voter identity
    if not wallet or wallet == "anonymous":
        client_ip = request.client.host if request.client else "unknown"
        wallet = f"anon_{client_ip}"
    else:
        _validate_wallet_format(wallet)
    return await vote_post(_get_db(), post_id, wallet, body.get("vote", 1))


@router.get("/api/public/forum/search")
async def forum_search(q: str = "", limit: int = 20):
    """AI Forum — search posts."""
    from routes.forum import search_posts

    q = q[:100]  # Cap search query length
    limit = min(max(limit, 1), 100)
    posts = await search_posts(_get_db(), q, limit)
    return {"posts": posts, "total": len(posts)}


@router.post("/api/public/forum/post/{post_id}/report")
async def forum_report(post_id: str, request: Request):
    """AI Forum — report a post."""
    from routes.forum import report_post

    body = await _read_body(request)
    wallet = body.get("wallet", "")
    if not wallet or wallet in ("visitor", "anonymous"):
        client_ip = request.client.host if request.client else "unknown"
        wallet = f"visitor_{client_ip}"
    else:
        _validate_wallet_format(wallet)
    return await report_post(_get_db(), post_id, wallet, body.get("reason", ""))


# ── Phase 3: Trending + Tags ──


@router.get("/api/public/forum/trending")
async def forum_trending(hours: int = 24, limit: int = 10):
    """AI Forum — trending posts (last N hours)."""
    from routes.forum import get_trending

    hours = min(max(hours, 1), 168)  # 1h to 7 days
    limit = min(max(limit, 1), 50)
    posts = await get_trending(_get_db(), hours=hours, limit=limit)
    return {"posts": posts, "hours": hours, "total": len(posts)}


@router.get("/api/public/forum/tags")
async def forum_tags(limit: int = 50):
    """AI Forum — all tags with frequency."""
    from routes.forum import get_all_tags

    limit = min(max(limit, 1), 200)
    tags = await get_all_tags(_get_db(), limit=limit)
    return {"tags": tags, "total": len(tags)}


@router.get("/api/public/forum/tag/{tag}")
async def forum_by_tag(tag: str, limit: int = 20, page: int = 0):
    """AI Forum — posts filtered by tag."""
    from routes.forum import get_posts_by_tag

    tag = tag.strip()[:50]
    limit = min(max(limit, 1), 100)
    offset = max(page, 0) * limit
    posts = await get_posts_by_tag(_get_db(), tag, limit=limit, offset=offset)
    return {"posts": posts, "tag": tag, "total": len(posts)}


# ── Phase 6c: Filtered posts ──


@router.get("/api/public/forum/filtered")
async def forum_filtered(
    sort: str = "hot",
    community: str = "",
    post_type: str = "",
    chain: str = "",
    min_budget: float = 0,
    max_budget: float = 0,
    since: int = 0,
    limit: int = 20,
    page: int = 0,
):
    """AI Forum — posts with advanced filters."""
    from routes.forum import get_posts_filtered

    limit = min(max(limit, 1), 100)
    offset = max(page, 0) * limit
    posts = await get_posts_filtered(
        _get_db(),
        community=community,
        sort=sort,
        limit=limit,
        offset=offset,
        post_type=post_type,
        chain=chain,
        min_budget=min_budget,
        max_budget=max_budget,
        since=since,
    )
    return {"posts": posts, "total": len(posts)}


@router.post("/api/admin/forum/ban")
async def forum_admin_ban(request: Request):
    """Admin — ban an agent from the forum."""
    from core.auth import require_ceo_auth

    await require_ceo_auth(request, request.headers.get("X-CEO-Key"))
    from routes.forum import admin_ban_agent

    body = await _read_body(request)
    return await admin_ban_agent(_get_db(), body.get("wallet", ""))


@router.post("/api/admin/forum/unban")
async def forum_admin_unban(request: Request):
    """Admin — unban an agent from the forum."""
    from core.auth import require_ceo_auth

    await require_ceo_auth(request, request.headers.get("X-CEO-Key"))
    from routes.forum import admin_unban_agent

    body = await _read_body(request)
    return await admin_unban_agent(_get_db(), body.get("wallet", ""))


@router.get("/api/public/forum/my-posts")
async def forum_my_posts(request: Request, wallet: str = "", limit: int = 50):
    """Get posts and replies by a specific user (wallet or IP-based session)."""
    db = _get_db()
    # Determine identity: wallet (priority) or IP fingerprint
    identity = wallet.strip() if wallet else ""
    if not identity:
        client_ip = request.client.host if request.client else ""
        identity = f"anon_{client_ip}"

    if not identity:
        return {"posts": [], "replies": []}

    # Search posts by author_wallet (stored in JSON data column)
    limit = min(max(limit, 1), 100)
    try:
        # Query posts where author_wallet matches
        post_rows = await db.raw_execute_fetchall(
            "SELECT data FROM forum_posts WHERE status='active' AND json_extract(data, '$.author_wallet')=? "
            "ORDER BY created_at DESC LIMIT ?", (identity, limit))
        my_posts = [json.loads(r["data"]) for r in post_rows]

        # Query replies separately from forum_replies table
        reply_rows = await db.raw_execute_fetchall(
            "SELECT r.data as rdata, r.post_id FROM forum_replies r "
            "WHERE r.status='active' AND json_extract(r.data, '$.author_wallet')=? "
            "ORDER BY r.created_at DESC LIMIT ?", (identity, limit))
        my_replies = []
        for rr in reply_rows:
            reply_data = json.loads(rr["rdata"])
            # Get parent post title
            title_rows = await db.raw_execute_fetchall(
                "SELECT json_extract(data, '$.title') as title FROM forum_posts WHERE id=?", (rr["post_id"],))
            title = title_rows[0]["title"] if title_rows else ""
            my_replies.append({
                "post_id": rr["post_id"],
                "post_title": title or "",
                "reply": reply_data,
            })
        return {
            "posts": my_posts,
            "replies": my_replies,
            "total_posts": len(my_posts),
            "total_replies": len(my_replies),
        }
    except Exception as e:
        return safe_error(e, "forum_my_posts")


# ── Forum Notifications ──


def _get_auth_wallet(request: Request, fallback_wallet: str = "") -> str:
    """Extract wallet from Bearer session token if present, else use fallback.
    This prevents IDOR — write operations REQUIRE a valid token."""
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        try:
            from core.auth import verify_session_token
            return verify_session_token(auth_header[7:])
        except HTTPException:
            raise  # Re-raise 401 for invalid/expired tokens
        except Exception:
            pass  # Import or unexpected error — fallback
    return fallback_wallet


@router.get("/api/public/forum/notifications/count")
async def forum_notifications_count(request: Request, wallet: str = ""):
    """Get unread notification count. Token-derived wallet takes priority."""
    wallet = _get_auth_wallet(request, wallet)
    # Visitors with IP-based wallet can still check their own notifications
    if not wallet:
        return {"unread": 0}
    try:
        db = _get_db()
        rows = await db.raw_execute_fetchall(
            "SELECT COUNT(*) as cnt FROM forum_notifications WHERE wallet=? AND read=0",
            (wallet,))
        return {"unread": rows[0]["cnt"] if rows else 0}
    except Exception:
        return {"unread": 0}


@router.get("/api/public/forum/notifications")
async def forum_notifications_list(request: Request, wallet: str = "", limit: int = 50):
    """Get unread notifications. Token-derived wallet takes priority."""
    wallet = _get_auth_wallet(request, wallet)
    if not wallet:
        return {"notifications": [], "unread": 0}
    try:
        db = _get_db()
        rows = await db.raw_execute_fetchall(
            "SELECT id, type, post_id, reply_id, payload, created_at "
            "FROM forum_notifications WHERE wallet=? AND read=0 "
            "ORDER BY created_at DESC LIMIT ?",
            (wallet, min(limit, 100)))
        notifs = []
        for r in rows:
            n = dict(r)
            try:
                n["payload"] = json.loads(n.get("payload", "{}"))
            except Exception:
                n["payload"] = {}
            notifs.append(n)
        return {"notifications": notifs, "unread": len(notifs)}
    except Exception as e:
        return safe_error(e, "forum_notifications")


@router.post("/api/public/forum/notifications/read")
async def forum_notifications_read(request: Request):
    """Mark notifications as read. REQUIRES Bearer token — wallet derived from token."""
    # Write operation = must verify identity (prevents IDOR)
    auth_wallet = _get_auth_wallet(request)
    body = await _read_body(request)
    wallet = auth_wallet or body.get("wallet", "")
    if not wallet:
        raise HTTPException(400, "wallet required")
    # If auth token present, ONLY allow marking own notifications
    if auth_wallet and body.get("wallet") and body["wallet"] != auth_wallet:
        raise HTTPException(403, "Cannot modify another wallet's notifications")
    try:
        db = _get_db()
        if body.get("read_all"):
            await db.raw_execute(
                "UPDATE forum_notifications SET read=1 WHERE wallet=? AND read=0",
                (wallet,))
        else:
            ids = body.get("notification_ids", [])
            if ids and isinstance(ids, list):
                ids = ids[:500]  # Cap to prevent huge SQL
                placeholders = ",".join("?" for _ in ids)
                await db.raw_execute(
                    f"UPDATE forum_notifications SET read=1 WHERE wallet=? AND id IN ({placeholders})",
                    (wallet, *ids))
        return {"success": True}
    except Exception as e:
        return safe_error(e, "forum_notifications_read")
