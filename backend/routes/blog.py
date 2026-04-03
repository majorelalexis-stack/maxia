"""MAXIA Blog — Knowledge Base avec articles CEO AI + manuels.

Nouvelle table blog_posts. Endpoints publics + admin (CEO auth).
RSS feed pour SEO.
"""
import logging
import json
import time
import uuid
import re

from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.responses import Response
from core.error_utils import safe_error

logger = logging.getLogger(__name__)

router = APIRouter(tags=["blog"])

_BLOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS blog_posts (
    id TEXT PRIMARY KEY,
    slug TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    summary TEXT DEFAULT '',
    author TEXT NOT NULL DEFAULT 'MAXIA CEO',
    category TEXT DEFAULT 'general',
    tags TEXT DEFAULT '[]',
    status TEXT DEFAULT 'draft',
    views INTEGER DEFAULT 0,
    created_at INTEGER,
    published_at INTEGER,
    updated_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_blog_slug ON blog_posts(slug);
CREATE INDEX IF NOT EXISTS idx_blog_published ON blog_posts(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_blog_category ON blog_posts(category);
CREATE INDEX IF NOT EXISTS idx_blog_status ON blog_posts(status);
"""

_schema_ready = False

BLOG_CATEGORIES = [
    {"id": "market-analysis", "name": "Market Analysis", "icon": "\U0001f4c8"},
    {"id": "tutorial", "name": "Tutorials & Guides", "icon": "\U0001f4d6"},
    {"id": "announcement", "name": "Announcements", "icon": "\U0001f4e2"},
    {"id": "agent-spotlight", "name": "Agent Spotlight", "icon": "\U0001f31f"},
    {"id": "tech-deep-dive", "name": "Tech Deep Dive", "icon": "\U0001f527"},
    {"id": "weekly-recap", "name": "Weekly Recap", "icon": "\U0001f4c5"},
    {"id": "general", "name": "General", "icon": "\U0001f4ac"},
]

# Regex to strip dangerous HTML (scripts, iframes, event handlers)
_DANGEROUS_HTML_RE = re.compile(
    r"<(script|iframe|object|embed|form)[^>]*>.*?</\1>|"
    r"<(script|iframe|object|embed|form)[^>]*/>|"
    r"\bon\w+\s*=",
    flags=re.DOTALL | re.IGNORECASE,
)


async def _ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    from core.database import db
    await db.raw_executescript(_BLOG_SCHEMA)
    _schema_ready = True
    logger.info("[Blog] Schema pret")


def _slugify(title: str) -> str:
    """Genere un slug URL-safe depuis un titre."""
    slug = title.lower().strip()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug[:100] or f"post-{uuid.uuid4().hex[:8]}"


def _sanitize(text: str) -> str:
    """Strip dangerous HTML tags (scripts, iframes, event handlers). Keeps markdown."""
    if not text:
        return text
    return _DANGEROUS_HTML_RE.sub("", text)


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

@router.get("/api/public/blog")
async def blog_list(
    category: str = "",
    limit: int = Query(default=20, ge=1, le=100),
    page: int = Query(default=0, ge=0),
):
    """Liste des articles publies, pagines."""
    await _ensure_schema()
    from core.database import db
    offset = page * limit

    try:
        if category:
            rows = await db.raw_execute_fetchall(
                "SELECT id, slug, title, summary, author, category, tags, views, published_at, created_at "
                "FROM blog_posts WHERE status='published' AND category=? "
                "ORDER BY published_at DESC LIMIT ? OFFSET ?",
                (category, limit, offset))
        else:
            rows = await db.raw_execute_fetchall(
                "SELECT id, slug, title, summary, author, category, tags, views, published_at, created_at "
                "FROM blog_posts WHERE status='published' "
                "ORDER BY published_at DESC LIMIT ? OFFSET ?",
                (limit, offset))

        posts = []
        for r in rows:
            r = r if isinstance(r, dict) else dict(zip(
                ["id", "slug", "title", "summary", "author", "category", "tags", "views", "published_at", "created_at"], r))
            try:
                r["tags"] = json.loads(r.get("tags", "[]"))
            except Exception:
                r["tags"] = []
            posts.append(r)

        # Total count
        if category:
            cnt = await db.raw_execute_fetchall(
                "SELECT COUNT(*) as cnt FROM blog_posts WHERE status='published' AND category=?", (category,))
        else:
            cnt = await db.raw_execute_fetchall(
                "SELECT COUNT(*) as cnt FROM blog_posts WHERE status='published'")
        total = 0
        if cnt:
            total = int(_row_val(cnt[0], "cnt", 0, 0) or 0)

        return {
            "posts": posts,
            "total": total,
            "categories": BLOG_CATEGORIES,
            "page": page,
            "limit": limit,
        }
    except Exception as e:
        logger.error("[Blog] blog_list error: %s", e)
        raise HTTPException(500, "Internal error")


@router.get("/api/public/blog/article/{slug}")
async def blog_article(slug: str):
    """Article complet par slug. Incremente le compteur de vues."""
    await _ensure_schema()
    from core.database import db

    try:
        row = await db._fetchone(
            "SELECT id, slug, title, body, summary, author, category, tags, views, published_at, created_at, updated_at "
            "FROM blog_posts WHERE slug=? AND status='published'", (slug,))
        if not row:
            raise HTTPException(404, "Article not found")

        r = row if isinstance(row, dict) else dict(zip(
            ["id", "slug", "title", "body", "summary", "author", "category", "tags", "views", "published_at", "created_at", "updated_at"], row))
        try:
            r["tags"] = json.loads(r.get("tags", "[]"))
        except Exception:
            r["tags"] = []

        # Increment views (fire and forget)
        try:
            await db.raw_execute(
                "UPDATE blog_posts SET views = views + 1 WHERE slug=?", (slug,))
            r["views"] = (r.get("views") or 0) + 1
        except Exception:
            pass

        return r
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[Blog] blog_article error: %s", e)
        raise HTTPException(500, "Internal error")


@router.get("/api/public/blog/rss")
async def blog_rss():
    """Flux RSS des articles publies (XML)."""
    await _ensure_schema()
    from core.database import db

    try:
        rows = await db.raw_execute_fetchall(
            "SELECT title, slug, summary, author, published_at FROM blog_posts "
            "WHERE status='published' ORDER BY published_at DESC LIMIT 50")

        items = ""
        for r in rows:
            r = r if isinstance(r, dict) else dict(zip(
                ["title", "slug", "summary", "author", "published_at"], r))
            title = (r.get("title") or "").replace("&", "&amp;").replace("<", "&lt;")
            summary = (r.get("summary") or "").replace("&", "&amp;").replace("<", "&lt;")
            slug_val = r.get("slug", "")
            items += f"""    <item>
      <title>{title}</title>
      <link>https://maxiaworld.app/blog/{slug_val}</link>
      <description>{summary}</description>
      <author>{(r.get('author') or 'MAXIA').replace('&','&amp;')}</author>
    </item>
"""

        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>MAXIA Blog</title>
    <link>https://maxiaworld.app/blog</link>
    <description>AI-to-AI Marketplace Blog — Market analysis, tutorials, and announcements</description>
    <language>en</language>
{items}  </channel>
</rss>"""
        return Response(content=xml, media_type="application/rss+xml")
    except Exception:
        return Response(content="<rss><channel><title>MAXIA Blog</title></channel></rss>",
                        media_type="application/rss+xml")


# ── Admin endpoints (CEO auth) ──

@router.post("/api/admin/blog/create")
async def blog_create(request: Request):
    """Creer un article (draft ou published). Requiert CEO auth."""
    from core.auth import require_ceo_auth
    await require_ceo_auth(request, request.headers.get("X-CEO-Key"))

    await _ensure_schema()
    from core.database import db

    body = await _read_body(request)
    title = _sanitize((body.get("title", "") or "").strip())[:200]
    if not title:
        raise HTTPException(400, "title required")

    post_body = _sanitize((body.get("body", "") or "").strip())
    if not post_body:
        raise HTTPException(400, "body required")

    slug = body.get("slug") or _slugify(title)
    # Check slug uniqueness
    existing = await db._fetchone("SELECT id FROM blog_posts WHERE slug=?", (slug,))
    if existing:
        slug = f"{slug}-{uuid.uuid4().hex[:6]}"

    now = int(time.time())
    status = body.get("status", "draft")
    if status not in ("draft", "published"):
        status = "draft"

    post_id = f"blog_{uuid.uuid4().hex[:12]}"
    tags = body.get("tags", [])
    if isinstance(tags, list):
        tags = [str(t).strip()[:50] for t in tags[:10]]
    else:
        tags = []

    summary = _sanitize((body.get("summary", "") or "").strip())[:500]
    if not summary and post_body:
        summary = re.sub(r"[#*_\[\]()>`]", "", post_body[:300]).strip()

    try:
        await db.raw_execute(
            "INSERT INTO blog_posts (id, slug, title, body, summary, author, category, tags, status, views, created_at, published_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)",
            (post_id, slug, title, post_body, summary,
             body.get("author", "MAXIA CEO"),
             body.get("category", "general"),
             json.dumps(tags),
             status, now,
             now if status == "published" else None,
             now))
    except Exception as e:
        if "unique" in str(e).lower() or "duplicate" in str(e).lower():
            raise HTTPException(409, "Slug already exists")
        logger.error("[Blog] blog_create DB error: %s", e)
        raise HTTPException(500, "Failed to create article")

    return {"success": True, "id": post_id, "slug": slug, "status": status}


@router.put("/api/admin/blog/{post_id}")
async def blog_update(post_id: str, request: Request):
    """Editer un article. Requiert CEO auth."""
    from core.auth import require_ceo_auth
    await require_ceo_auth(request, request.headers.get("X-CEO-Key"))

    await _ensure_schema()
    from core.database import db

    existing = await db._fetchone("SELECT id, status, published_at FROM blog_posts WHERE id=?", (post_id,))
    if not existing:
        raise HTTPException(404, "Article not found")

    existing_status = _row_val(existing, "status", 1, "draft")
    existing_published_at = _row_val(existing, "published_at", 2, None)

    body = await _read_body(request)
    now = int(time.time())

    updates = []
    params = []

    if "title" in body:
        updates.append("title = ?")
        params.append(_sanitize(str(body["title"])[:200]))
    if "body" in body:
        updates.append("body = ?")
        params.append(_sanitize(str(body["body"])))
    if "summary" in body:
        updates.append("summary = ?")
        params.append(_sanitize(str(body["summary"])[:500]))
    if "category" in body:
        updates.append("category = ?")
        params.append(str(body["category"])[:50])
    if "tags" in body:
        tags = body["tags"] if isinstance(body["tags"], list) else []
        updates.append("tags = ?")
        params.append(json.dumps([str(t).strip()[:50] for t in tags[:10]]))
    if "status" in body and body["status"] in ("draft", "published", "archived"):
        updates.append("status = ?")
        params.append(body["status"])
        # Only set published_at on first publish (not on re-edits)
        if body["status"] == "published" and existing_status != "published":
            updates.append("published_at = ?")
            params.append(now)

    if not updates:
        raise HTTPException(400, "Nothing to update")

    updates.append("updated_at = ?")
    params.append(now)
    params.append(post_id)

    try:
        await db.raw_execute(
            f"UPDATE blog_posts SET {', '.join(updates)} WHERE id = ?", tuple(params))
    except Exception as e:
        logger.error("[Blog] blog_update DB error: %s", e)
        raise HTTPException(500, "Failed to update article")

    return {"success": True, "id": post_id}


@router.delete("/api/admin/blog/{post_id}")
async def blog_delete(post_id: str, request: Request):
    """Archiver un article. Requiert CEO auth."""
    from core.auth import require_ceo_auth
    await require_ceo_auth(request, request.headers.get("X-CEO-Key"))

    await _ensure_schema()
    from core.database import db

    try:
        await db.raw_execute(
            "UPDATE blog_posts SET status = 'archived', updated_at = ? WHERE id = ?",
            (int(time.time()), post_id))
    except Exception as e:
        logger.error("[Blog] blog_delete DB error: %s", e)
        raise HTTPException(500, "Failed to archive article")

    return {"success": True, "id": post_id, "status": "archived"}
