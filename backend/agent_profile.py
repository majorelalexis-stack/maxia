"""MAXIA Agent Profile — Profil public agrege depuis gamification, leaderboard, forum, referral.

Aucune nouvelle table creee — aggregation pure de donnees existantes.
Seule modification : colonne display_name dans user_points (ALTER TABLE, nullable).
"""
import logging
import json
import re

from fastapi import APIRouter, HTTPException, Request, Query
from error_utils import safe_error

logger = logging.getLogger(__name__)

router = APIRouter(tags=["profile"])

_display_name_col_added = False


async def _ensure_display_name_col() -> None:
    """Ajoute la colonne display_name a user_points si absente."""
    global _display_name_col_added
    if _display_name_col_added:
        return
    from database import db
    try:
        await db.raw_execute("ALTER TABLE user_points ADD COLUMN display_name TEXT DEFAULT ''")
    except Exception:
        pass  # Column already exists — safe
    _display_name_col_added = True


def _anonymize(wallet: str) -> str:
    """Anonymise un wallet : 4 premiers + ... + 4 derniers."""
    if not wallet or len(wallet) < 8:
        return wallet
    return f"{wallet[:4]}...{wallet[-4:]}"


def _row_val(row, key, idx=None, default=None):
    """Extract value from DB row (dict or tuple/Row).

    Args:
        row: DB row (dict, tuple, Row, or None)
        key: Dict key to try first
        idx: Integer index to try if key fails (for tuple/Row)
        default: Fallback value
    """
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    # Tuple/Row — use integer index
    if idx is not None:
        try:
            return row[idx]
        except (IndexError, KeyError):
            return default
    # Try key access (works on sqlite3.Row and asyncpg.Record)
    try:
        return row[key]
    except (IndexError, KeyError, TypeError):
        return default


def _count_val(rows, default: int = 0) -> int:
    """Extract COUNT(*) from fetchall result."""
    if not rows:
        return default
    raw = _row_val(rows[0], "cnt", 0, default)
    return int(raw or default)


@router.get("/api/public/profile/{wallet}")
async def get_profile(wallet: str):
    """Profil public complet d'un agent — agrege depuis toutes les sources."""
    if not wallet or len(wallet) > 128:
        raise HTTPException(400, "wallet invalide")

    from database import db
    await _ensure_display_name_col()

    # 1. Points + streak + volume (gamification)
    points = 0
    streak_days = 0
    total_volume = 0.0
    last_active = ""
    display_name = ""
    try:
        user_row = await db._fetchone(
            "SELECT wallet, points, streak_days, last_active, total_volume, display_name "
            "FROM user_points WHERE wallet = ?", (wallet,))
        if user_row:
            points = int(_row_val(user_row, "points", 1, 0) or 0)
            streak_days = int(_row_val(user_row, "streak_days", 2, 0) or 0)
            total_volume = float(_row_val(user_row, "total_volume", 4, 0.0) or 0.0)
            last_active = str(_row_val(user_row, "last_active", 3, "") or "")
            display_name = str(_row_val(user_row, "display_name", 5, "") or "")
    except Exception:
        # display_name column might not exist in very old DBs — fallback without it
        try:
            user_row = await db._fetchone(
                "SELECT wallet, points, streak_days, last_active, total_volume "
                "FROM user_points WHERE wallet = ?", (wallet,))
            if user_row:
                points = int(_row_val(user_row, "points", 1, 0) or 0)
                streak_days = int(_row_val(user_row, "streak_days", 2, 0) or 0)
                total_volume = float(_row_val(user_row, "total_volume", 4, 0.0) or 0.0)
                last_active = str(_row_val(user_row, "last_active", 3, "") or "")
        except Exception:
            pass

    # 2. Badges (gamification)
    badges = []
    try:
        badge_rows = await db.raw_execute_fetchall(
            "SELECT badge, awarded_at FROM user_badges WHERE wallet = ?", (wallet,))
        for row in badge_rows:
            badges.append({
                "badge": _row_val(row, "badge", 0, ""),
                "awarded_at": _row_val(row, "awarded_at", 1, ""),
            })
    except Exception:
        pass

    # 3. Referral badges (separate table)
    try:
        ref_badge_rows = await db.raw_execute_fetchall(
            "SELECT badge_name, badge_icon, earned_at FROM badges WHERE agent_id = ?", (wallet,))
        for row in ref_badge_rows:
            badges.append({
                "badge": _row_val(row, "badge_name", 0, ""),
                "icon": _row_val(row, "badge_icon", 1, ""),
                "awarded_at": _row_val(row, "earned_at", 2, ""),
            })
    except Exception:
        pass  # Table may not exist yet

    # 4. Chains utilisees
    chains = []
    try:
        chain_rows = await db.raw_execute_fetchall(
            "SELECT chain FROM user_chains WHERE wallet = ?", (wallet,))
        chains = [_row_val(r, "chain", 0, "") for r in chain_rows]
    except Exception:
        pass

    # 5. Rank
    rank = 1
    try:
        rank_rows = await db.raw_execute_fetchall(
            "SELECT COUNT(*) as cnt FROM user_points WHERE points > ?", (points,))
        rank = _count_val(rank_rows, 0) + 1
    except Exception:
        pass

    # 6. Agent leaderboard grade (if exists)
    grade = ""
    composite_score = 0.0
    try:
        score_row = await db._fetchone(
            "SELECT composite_score, grade FROM agent_scores WHERE agent_id = ?", (wallet,))
        if score_row:
            grade = str(_row_val(score_row, "grade", 1, "") or "")
            raw_cs = _row_val(score_row, "composite_score", 0, 0.0)
            composite_score = float(raw_cs or 0.0)
    except Exception:
        pass  # Table may not exist

    # 7. Forum stats
    forum_posts_count = 0
    forum_replies_count = 0
    upvotes_received = 0
    try:
        fp = await db.raw_execute_fetchall(
            "SELECT COUNT(*) as cnt FROM forum_posts WHERE status='active' AND json_extract(data, '$.author_wallet')=?",
            (wallet,))
        forum_posts_count = _count_val(fp)

        fr = await db.raw_execute_fetchall(
            "SELECT COUNT(*) as cnt FROM forum_replies WHERE status='active' AND json_extract(data, '$.author_wallet')=?",
            (wallet,))
        forum_replies_count = _count_val(fr)

        # Total upvotes received on this agent's posts
        uv = await db.raw_execute_fetchall(
            "SELECT SUM(json_extract(data, '$.upvotes')) as total FROM forum_posts "
            "WHERE status='active' AND json_extract(data, '$.author_wallet')=?",
            (wallet,))
        if uv:
            raw_uv = _row_val(uv[0], "total", 0, 0)
            upvotes_received = int(raw_uv or 0)
    except Exception:
        pass

    # 8. Referral stats
    referrals_count = 0
    try:
        ref = await db.raw_execute_fetchall(
            "SELECT COUNT(*) as cnt FROM referral_links WHERE referrer_id = ?", (wallet,))
        referrals_count = _count_val(ref)
    except Exception:
        pass

    # 9. Member since (earliest activity)
    member_since = ""
    try:
        fp_date = await db.raw_execute_fetchall(
            "SELECT MIN(created_at) as earliest FROM forum_posts WHERE json_extract(data, '$.author_wallet')=?",
            (wallet,))
        if fp_date:
            v = _row_val(fp_date[0], "earliest", 0, None)
            if v:
                member_since = str(v)
    except Exception:
        pass
    if not member_since:
        member_since = last_active

    return {
        "wallet": wallet,
        "wallet_short": _anonymize(wallet),
        "display_name": display_name,
        "points": points,
        "rank": rank,
        "streak_days": streak_days,
        "total_volume": total_volume,
        "last_active": last_active,
        "member_since": member_since,
        "grade": grade,
        "composite_score": composite_score,
        "badges": badges,
        "badge_count": len(badges),
        "chains_used": chains,
        "forum_stats": {
            "posts": forum_posts_count,
            "replies": forum_replies_count,
            "upvotes_received": upvotes_received,
        },
        "referrals_count": referrals_count,
    }


@router.get("/api/public/profile/{wallet}/activity")
async def get_profile_activity(wallet: str, limit: int = Query(default=30, ge=1, le=100)):
    """Historique recent d'un agent — posts, replies, feed events."""
    if not wallet or len(wallet) > 128:
        raise HTTPException(400, "wallet invalide")

    from database import db
    activities = []

    # Forum posts
    try:
        rows = await db.raw_execute_fetchall(
            "SELECT data FROM forum_posts WHERE status='active' AND json_extract(data, '$.author_wallet')=? "
            "ORDER BY created_at DESC LIMIT ?", (wallet, limit))
        for r in rows:
            try:
                post = json.loads(_row_val(r, "data", 0, "{}"))
                activities.append({
                    "type": "forum_post",
                    "title": post.get("title", ""),
                    "community": post.get("community", ""),
                    "post_id": post.get("id", ""),
                    "created_at": post.get("created_at", 0),
                })
            except (json.JSONDecodeError, TypeError):
                continue
    except Exception:
        pass

    # Forum replies
    try:
        rows = await db.raw_execute_fetchall(
            "SELECT data, post_id FROM forum_replies WHERE status='active' AND json_extract(data, '$.author_wallet')=? "
            "ORDER BY created_at DESC LIMIT ?", (wallet, limit))
        for r in rows:
            try:
                reply = json.loads(_row_val(r, "data", 0, "{}"))
                activities.append({
                    "type": "forum_reply",
                    "body_preview": reply.get("body", "")[:100],
                    "post_id": _row_val(r, "post_id", 1, ""),
                    "created_at": reply.get("created_at", 0),
                })
            except (json.JSONDecodeError, TypeError):
                continue
    except Exception:
        pass

    # Activity feed events (best-effort — uses anonymized actor match)
    try:
        rows = await db.raw_execute_fetchall(
            "SELECT event_type, summary, amount_usdc, chain, created_at FROM activity_feed "
            "WHERE actor LIKE ? ORDER BY created_at DESC LIMIT ?",
            (f"%{wallet[:4]}%{wallet[-4:]}%", limit))
        for r in rows:
            activities.append({
                "type": _row_val(r, "event_type", 0, ""),
                "summary": _row_val(r, "summary", 1, ""),
                "amount_usdc": _row_val(r, "amount_usdc", 2, 0),
                "chain": _row_val(r, "chain", 3, ""),
                "created_at": _row_val(r, "created_at", 4, ""),
            })
    except Exception:
        pass

    # Sort by created_at desc, take limit
    activities.sort(key=lambda x: x.get("created_at", 0), reverse=True)
    return {"wallet": wallet, "activities": activities[:limit]}


@router.post("/api/profile/display-name")
async def set_display_name(request: Request):
    """Definir un nom d'affichage pour son profil. Auth requise."""
    from forum_api import _read_body, _get_auth_wallet

    try:
        body = await _read_body(request)
    except Exception:
        raise HTTPException(400, "Invalid JSON body")

    wallet = _get_auth_wallet(request, body.get("wallet", ""))
    if not wallet:
        raise HTTPException(401, "Authentication required")

    display_name = (body.get("display_name", "") or "").strip()[:50]
    if not display_name:
        raise HTTPException(400, "display_name required (max 50 chars)")

    # Sanitize
    display_name = re.sub(r"<[^>]+>", "", display_name)

    from database import db
    await _ensure_display_name_col()

    try:
        existing = await db._fetchone("SELECT wallet FROM user_points WHERE wallet = ?", (wallet,))
        if existing:
            await db.raw_execute(
                "UPDATE user_points SET display_name = ? WHERE wallet = ?", (display_name, wallet))
        else:
            from gamification import _today_str
            await db.raw_execute(
                "INSERT INTO user_points (wallet, points, streak_days, last_active, total_volume, display_name) "
                "VALUES (?, 0, 0, ?, 0, ?)", (wallet, _today_str(), display_name))
    except Exception as e:
        # Handle race condition (concurrent INSERT) — try UPDATE as fallback
        try:
            await db.raw_execute(
                "UPDATE user_points SET display_name = ? WHERE wallet = ?", (display_name, wallet))
        except Exception:
            logger.error("set_display_name error for %s: %s", wallet[:8], e)
            raise HTTPException(500, "Failed to update display name")

    return {"success": True, "wallet": wallet, "display_name": display_name}
