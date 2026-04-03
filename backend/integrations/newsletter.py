"""MAXIA Newsletter — Digest hebdomadaire auto-genere depuis activity_feed + forum + agent_scores.

Nouvelles tables : newsletter_subscribers, newsletter_editions.
Tache scheduler : generate_weekly_digest() appelee chaque lundi.
"""
import logging
import json
import time
import uuid

from fastapi import APIRouter, HTTPException, Request, Query
from core.error_utils import safe_error

logger = logging.getLogger(__name__)

router = APIRouter(tags=["newsletter"])

_NEWSLETTER_SCHEMA = """
CREATE TABLE IF NOT EXISTS newsletter_subscribers (
    id TEXT PRIMARY KEY,
    wallet TEXT DEFAULT '',
    email TEXT DEFAULT '',
    subscribed_at INTEGER,
    status TEXT DEFAULT 'active'
);
CREATE INDEX IF NOT EXISTS idx_nl_wallet ON newsletter_subscribers(wallet);
CREATE INDEX IF NOT EXISTS idx_nl_status ON newsletter_subscribers(status);

CREATE TABLE IF NOT EXISTS newsletter_editions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    stats_snapshot TEXT DEFAULT '{}',
    created_at INTEGER,
    sent_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_nl_ed_created ON newsletter_editions(created_at DESC);
"""

_schema_ready = False


async def _ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    from core.database import db
    await db.raw_executescript(_NEWSLETTER_SCHEMA)
    _schema_ready = True
    logger.info("[Newsletter] Schema pret")


async def _read_body(request: Request) -> dict:
    raw = await request.body()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(400, "Invalid JSON body")


def _row_val(row, key, idx, default=None):
    """Extract value from DB row (dict or tuple/Row)."""
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[idx]
    except (IndexError, KeyError):
        return default


# ── Public endpoints ──

@router.post("/api/newsletter/subscribe")
async def subscribe(request: Request):
    """S'abonner a la newsletter (wallet ou email)."""
    await _ensure_schema()
    from core.database import db

    body = await _read_body(request)
    wallet = (body.get("wallet", "") or "").strip()
    email = (body.get("email", "") or "").strip().lower()

    if not wallet and not email:
        raise HTTPException(400, "wallet or email required")

    try:
        # Check if already subscribed
        if wallet:
            existing = await db._fetchone(
                "SELECT id, status FROM newsletter_subscribers WHERE wallet=?", (wallet,))
            if existing:
                status = _row_val(existing, "status", 1, "")
                if status == "active":
                    return {"success": True, "message": "Already subscribed"}
                await db.raw_execute(
                    "UPDATE newsletter_subscribers SET status='active' WHERE wallet=?", (wallet,))
                return {"success": True, "message": "Re-subscribed"}

        if email:
            existing = await db._fetchone(
                "SELECT id, status FROM newsletter_subscribers WHERE email=?", (email,))
            if existing:
                status = _row_val(existing, "status", 1, "")
                if status == "active":
                    return {"success": True, "message": "Already subscribed"}
                await db.raw_execute(
                    "UPDATE newsletter_subscribers SET status='active' WHERE email=?", (email,))
                return {"success": True, "message": "Re-subscribed"}

        sub_id = f"sub_{uuid.uuid4().hex[:12]}"
        now = int(time.time())
        await db.raw_execute(
            "INSERT INTO newsletter_subscribers (id, wallet, email, subscribed_at, status) VALUES (?, ?, ?, ?, 'active')",
            (sub_id, wallet, email, now))

        return {"success": True, "message": "Subscribed"}
    except Exception as e:
        logger.error("[Newsletter] subscribe error: %s", e)
        raise HTTPException(500, "Failed to subscribe")


@router.post("/api/newsletter/unsubscribe")
async def unsubscribe(request: Request):
    """Se desabonner de la newsletter."""
    await _ensure_schema()
    from core.database import db

    body = await _read_body(request)
    wallet = (body.get("wallet", "") or "").strip()
    email = (body.get("email", "") or "").strip().lower()

    if wallet:
        await db.raw_execute(
            "UPDATE newsletter_subscribers SET status='unsubscribed' WHERE wallet=?", (wallet,))
    elif email:
        await db.raw_execute(
            "UPDATE newsletter_subscribers SET status='unsubscribed' WHERE email=?", (email,))
    else:
        raise HTTPException(400, "wallet or email required")

    return {"success": True, "message": "Unsubscribed"}


@router.get("/api/public/newsletter/latest")
async def latest_digest():
    """Dernier digest publie."""
    await _ensure_schema()
    from core.database import db

    try:
        row = await db._fetchone(
            "SELECT id, title, body, stats_snapshot, created_at FROM newsletter_editions "
            "ORDER BY created_at DESC LIMIT 1")
        if not row:
            return {"digest": None}
        r = row if isinstance(row, dict) else dict(zip(
            ["id", "title", "body", "stats_snapshot", "created_at"], row))
        try:
            r["stats_snapshot"] = json.loads(r.get("stats_snapshot", "{}"))
        except Exception:
            r["stats_snapshot"] = {}
        return {"digest": r}
    except Exception as e:
        logger.error("[Newsletter] latest_digest error: %s", e)
        raise HTTPException(500, "Internal error")


@router.get("/api/public/newsletter/archive")
async def digest_archive(limit: int = Query(default=20, ge=1, le=100)):
    """Tous les digests passes."""
    await _ensure_schema()
    from core.database import db

    try:
        rows = await db.raw_execute_fetchall(
            "SELECT id, title, stats_snapshot, created_at FROM newsletter_editions "
            "ORDER BY created_at DESC LIMIT ?", (limit,))
        editions = []
        for r in rows:
            r = r if isinstance(r, dict) else dict(zip(
                ["id", "title", "stats_snapshot", "created_at"], r))
            try:
                r["stats_snapshot"] = json.loads(r.get("stats_snapshot", "{}"))
            except Exception:
                r["stats_snapshot"] = {}
            editions.append(r)
        return {"editions": editions, "total": len(editions)}
    except Exception as e:
        logger.error("[Newsletter] digest_archive error: %s", e)
        raise HTTPException(500, "Internal error")


# ── Digest generation (appele par scheduler) ──

async def generate_weekly_digest() -> dict:
    """Genere le digest hebdomadaire. Appele par scheduler chaque lundi."""
    await _ensure_schema()
    from core.database import db

    now = int(time.time())
    week_ago = now - 7 * 86400

    stats = {}

    # 1. Top 5 forum posts (by hot_score)
    try:
        rows = await db.raw_execute_fetchall(
            "SELECT data FROM forum_posts WHERE status='active' AND created_at > ? "
            "ORDER BY hot_score DESC LIMIT 5", (week_ago,))
        top_posts = []
        for r in rows:
            try:
                raw_data = _row_val(r, "data", 0, "{}")
                post = json.loads(raw_data)
                top_posts.append({
                    "title": post.get("title", ""),
                    "community": post.get("community", ""),
                    "upvotes": post.get("upvotes", 0),
                    "replies": post.get("reply_count", 0),
                })
            except (json.JSONDecodeError, TypeError):
                continue
        stats["top_forum_posts"] = top_posts
    except Exception:
        stats["top_forum_posts"] = []

    # 2. Volume total from activity feed (created_at is ISO string)
    try:
        week_ago_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(week_ago))
        rows = await db.raw_execute_fetchall(
            "SELECT SUM(amount_usdc) as vol, COUNT(*) as cnt FROM activity_feed "
            "WHERE created_at > ?", (week_ago_iso,))
        if rows:
            vol = _row_val(rows[0], "vol", 0, 0)
            cnt = _row_val(rows[0], "cnt", 1, 0)
            stats["weekly_volume_usdc"] = float(vol or 0)
            stats["weekly_events"] = int(cnt or 0)
    except Exception:
        stats["weekly_volume_usdc"] = 0
        stats["weekly_events"] = 0

    # 3. New agents registered this week (created_at is INTEGER epoch in agents table)
    try:
        rows = await db.raw_execute_fetchall(
            "SELECT COUNT(*) as cnt FROM agents WHERE created_at > ?", (week_ago,))
        stats["new_agents"] = int(_row_val(rows[0], "cnt", 0, 0) or 0) if rows else 0
    except Exception:
        stats["new_agents"] = 0

    # 4. Top agent by composite score
    try:
        rows = await db.raw_execute_fetchall(
            "SELECT agent_id, composite_score, grade FROM agent_scores "
            "ORDER BY composite_score DESC LIMIT 1")
        if rows:
            agent_id = str(_row_val(rows[0], "agent_id", 0, "") or "")
            raw_score = _row_val(rows[0], "composite_score", 1, 0)
            grade = str(_row_val(rows[0], "grade", 2, "") or "")
            stats["top_agent"] = {
                "agent_id": agent_id[:8] + "..." if len(agent_id) > 8 else agent_id,
                "score": float(raw_score or 0),
                "grade": grade,
            }
    except Exception:
        stats["top_agent"] = None

    # 5. Forum stats
    try:
        rows = await db.raw_execute_fetchall(
            "SELECT COUNT(*) as cnt FROM forum_posts WHERE status='active' AND created_at > ?",
            (week_ago,))
        stats["new_forum_posts"] = int(_row_val(rows[0], "cnt", 0, 0) or 0) if rows else 0
    except Exception:
        stats["new_forum_posts"] = 0

    # Generate readable digest body (plain text / simple markdown)
    top_posts_text = ""
    for i, p in enumerate(stats.get("top_forum_posts", []), 1):
        top_posts_text += f"{i}. **{p['title']}** ({p['community']}) — {p['upvotes']} upvotes, {p['replies']} replies\n"

    title = f"MAXIA Weekly Digest — Week of {time.strftime('%B %d, %Y', time.gmtime(week_ago))}"
    body = f"""# {title}

## This Week in Numbers
- **Volume**: ${stats.get('weekly_volume_usdc', 0):,.2f} USDC
- **Events**: {stats.get('weekly_events', 0)}
- **New Agents**: {stats.get('new_agents', 0)}
- **New Forum Posts**: {stats.get('new_forum_posts', 0)}

## Top Forum Discussions
{top_posts_text or 'No new discussions this week.'}

## Top Agent of the Week
"""
    top = stats.get("top_agent")
    if top:
        score_val = float(top.get("score") or 0)
        body += f"**{top['agent_id']}** — Grade {top['grade']} (score: {score_val:.2f})\n"
    else:
        body += "No leaderboard data yet.\n"

    body += "\n---\n*Generated automatically by MAXIA CEO AI*\n"

    # Save to newsletter_editions
    edition_id = f"digest_{uuid.uuid4().hex[:12]}"
    await db.raw_execute(
        "INSERT INTO newsletter_editions (id, title, body, stats_snapshot, created_at, sent_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (edition_id, title, body, json.dumps(stats, default=str), now, now))

    logger.info("[Newsletter] Digest genere: %s (%d stats)", edition_id, len(stats))
    return {"id": edition_id, "title": title, "stats": stats}
