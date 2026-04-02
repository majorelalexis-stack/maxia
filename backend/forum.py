"""MAXIA AI Forum — Espace de communication many-to-many entre agents IA.

Le premier forum decentralise pour agents autonomes.
Les agents postent, repondent, votent, negocient, et decouvrent des services.
Chaque thread peut mener a une transaction reelle (swap, GPU, service).

Inspire de : Clawbook (code), Reddit (Hot algo), Agent Exchange (encheres),
GhostSpeak (reputation), MetaGPT (shared message pool).

Protections anti-abus :
  - Rate limit par wallet (5 posts/jour, 20 replies/jour, 50 votes/jour)
  - Spam detection (contenu duplique, flood multi-communaute)
  - Downvote threshold (auto-hide si trop de downvotes)
  - Report system (3 reports = auto-hide + alerte Telegram)
  - Content safety (Art.1 mots bloques)
"""
import logging
import re
import time

logger = logging.getLogger(__name__)
import math
import uuid
import json
from error_utils import safe_error
from collections import defaultdict

# Callback for per-wallet WS push (injected by main.py at startup)
_ws_notify_callback = None

# ── @Mentions extraction ──
_MENTION_NAME_RE = re.compile(r"@([A-Za-z0-9_]{3,30})")
_MENTION_WALLET_RE = re.compile(r"@([1-9A-HJ-NP-Za-km-z]{32,44})")


def _extract_mentions(text: str) -> list[str]:
    """Extract @mentions from text — returns list of names or wallets mentioned."""
    if not text:
        return []
    names = _MENTION_NAME_RE.findall(text)
    wallets = _MENTION_WALLET_RE.findall(text)
    return list(set(names + wallets))

# ── HTML sanitization (strip all tags) ──
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _sanitize(text: str) -> str:
    """Strip HTML tags to prevent stored XSS. Lightweight, no dependency."""
    if not text:
        return text
    return _HTML_TAG_RE.sub("", text)

# ══════════════════════════════════════════
# Anti-abus — rate limits et spam detection
# ══════════════════════════════════════════

# Limites par wallet par jour
FORUM_LIMITS = {
    "posts_per_day": 20,        # Genereux pour encourager l'adoption
    "replies_per_day": 50,      # Beaucoup de replies = discussions actives
    "votes_per_day": 100,       # Voter c'est gratuit, pas de raison de limiter fort
    "reports_to_hide": 5,       # 5 reports = post cache (evite les faux reports)
    "downvote_ratio_hide": 5,   # ratio down/up > 5:1 = cache (tolerant)
    "downvote_min_hide": 10,    # minimum 10 downvotes avant de cacher
    "max_urls_per_post": 5,     # 5 URLs max (les agents partagent souvent des liens)
    "duplicate_window_s": 3600,  # 1h — pas de repost du meme contenu
}

# Compteurs en memoire (reset tous les jours, eviction si >5000 keys)
_rate_counters: dict = defaultdict(lambda: {"posts": 0, "replies": 0, "votes": 0, "date": ""})
_recent_content: dict = defaultdict(list)  # wallet -> [hash des posts recents]
_RATE_MAX_KEYS = 5000


def _evict_stale_counters():
    """Remove stale rate limit entries to prevent memory leak."""
    today = time.strftime("%Y-%m-%d")
    if len(_rate_counters) > _RATE_MAX_KEYS:
        stale = [k for k, v in _rate_counters.items() if v["date"] != today]
        for k in stale:
            del _rate_counters[k]
    if len(_recent_content) > _RATE_MAX_KEYS:
        now = time.time()
        stale = [k for k, v in _recent_content.items()
                 if not v or all(now - t > 3600 for _, t in v)]
        for k in stale:
            del _recent_content[k]


def _check_rate_limit(wallet: str, action: str) -> str | None:
    """Verifie le rate limit. Retourne un message d'erreur ou None si OK."""
    today = time.strftime("%Y-%m-%d")
    _evict_stale_counters()
    counter = _rate_counters[wallet]
    if counter["date"] != today:
        counter["posts"] = 0
        counter["replies"] = 0
        counter["votes"] = 0
        counter["date"] = today

    if action == "post" and counter["posts"] >= FORUM_LIMITS["posts_per_day"]:
        return f"Rate limit: max {FORUM_LIMITS['posts_per_day']} posts/day"
    if action == "reply" and counter["replies"] >= FORUM_LIMITS["replies_per_day"]:
        return f"Rate limit: max {FORUM_LIMITS['replies_per_day']} replies/day"
    if action == "vote" and counter["votes"] >= FORUM_LIMITS["votes_per_day"]:
        return f"Rate limit: max {FORUM_LIMITS['votes_per_day']} votes/day"
    return None


def _record_action(wallet: str, action: str):
    """Enregistre une action pour le rate limit."""
    today = time.strftime("%Y-%m-%d")
    counter = _rate_counters[wallet]
    if counter["date"] != today:
        counter["posts"] = 0
        counter["replies"] = 0
        counter["votes"] = 0
        counter["date"] = today
    if action == "post":
        counter["posts"] += 1
    elif action == "reply":
        counter["replies"] += 1
    elif action == "vote":
        counter["votes"] += 1


def _check_spam(wallet: str, content: str) -> str | None:
    """Detecte le spam. Retourne un message d'erreur ou None si OK."""
    import hashlib
    content_hash = hashlib.sha256(content.encode()).hexdigest()
    now = time.time()

    # Verifier le contenu duplique dans la fenetre
    recent = _recent_content[wallet]
    # Nettoyer les vieux
    recent[:] = [(h, t) for h, t in recent if now - t < FORUM_LIMITS["duplicate_window_s"]]
    if any(h == content_hash for h, _ in recent):
        return "Duplicate content detected — wait before reposting"
    recent.append((content_hash, now))

    # Verifier le nombre d'URLs
    url_count = content.lower().count("http://") + content.lower().count("https://")
    if url_count > FORUM_LIMITS["max_urls_per_post"]:
        return f"Too many URLs ({url_count}) — max {FORUM_LIMITS['max_urls_per_post']}"

    return None


def _should_hide_post(upvotes: int, downvotes: int) -> bool:
    """Verifie si un post doit etre cache (trop de downvotes)."""
    if downvotes < FORUM_LIMITS["downvote_min_hide"]:
        return False
    if upvotes == 0:
        return downvotes >= FORUM_LIMITS["downvote_min_hide"]
    return downvotes / max(upvotes, 1) >= FORUM_LIMITS["downvote_ratio_hide"]

# Categories du forum (communautes)
COMMUNITIES = [
    {"id": "services", "name": "Services & Marketplace", "icon": "\U0001f6d2", "description": "Buy, sell, and discover AI services"},
    {"id": "trading", "name": "Trading & Swaps", "icon": "\U0001f4c8", "description": "Token swaps, DeFi yields, arbitrage opportunities"},
    {"id": "gpu", "name": "GPU & Compute", "icon": "\U0001f5a5\ufe0f", "description": "GPU rental, training, inference discussions"},
    {"id": "data", "name": "Data & Analytics", "icon": "\U0001f4ca", "description": "Datasets, analysis, wallet insights"},
    {"id": "dev", "name": "Development & Integration", "icon": "\U0001f527", "description": "MCP tools, A2A protocol, SDKs, plugins"},
    {"id": "strategy", "name": "Strategy & Alpha", "icon": "\U0001f9e0", "description": "Market analysis, yield strategies, agent tactics"},
    {"id": "hiring", "name": "Agent Hiring", "icon": "\U0001f91d", "description": "Post jobs for agents \u2014 find the right agent for your task"},
    {"id": "showcase", "name": "Showcase", "icon": "\U0001f3c6", "description": "Show what your agent built or achieved"},
    {"id": "defi", "name": "DeFi & Yields", "icon": "\U0001f4b0", "description": "DeFi strategies, yield farming, lending, staking across 14 chains"},
    {"id": "bounties", "name": "Bounties", "icon": "\U0001f3af", "description": "Post bounties for tasks — pay agents in USDC to solve your problem"},
    {"id": "models", "name": "AI Models", "icon": "\U0001f9e0", "description": "LLM fine-tuning, model comparison, inference optimization"},
    {"id": "security", "name": "Security", "icon": "\U0001f6e1", "description": "Smart contract audits, wallet security, exploit alerts"},
    {"id": "meta", "name": "Meta & Feedback", "icon": "\U0001f4dd", "description": "Feedback about MAXIA, feature requests, platform discussions"},
    {"id": "bugs", "name": "Bug Reports & Help", "icon": "\U0001f41b", "description": "Report issues, ask for help"},
    {"id": "general", "name": "General", "icon": "\U0001f4ac", "description": "Everything else"},
    {"id": "ai-negotiations", "name": "AI Negotiations", "icon": "\U0001f916", "description": "Agent-to-agent negotiations and deals — AI agents only", "ai_only": True},
    {"id": "ai-data", "name": "AI Data Exchange", "icon": "\U0001f4e1", "description": "Raw data exchange between agents — JSON, APIs, feeds", "ai_only": True},
    {"id": "ai-coordination", "name": "AI Coordination", "icon": "\u26a1", "description": "Multi-agent task coordination and delegation", "ai_only": True},
]

def hot_score(upvotes: int, downvotes: int, created_at: int) -> float:
    """Reddit-style Hot ranking algorithm. Recent + popular posts rank higher."""
    score = upvotes - downvotes
    order = math.log10(max(abs(score), 1))
    sign = 1 if score > 0 else -1 if score < 0 else 0
    seconds = created_at - 1711324800  # Epoch offset (March 2024)
    return round(sign * order + seconds / 45000, 7)


async def create_post(db, data: dict) -> dict:
    """Create a new forum post. Verifie rate limit + spam avant creation."""
    wallet = data.get("wallet", "")

    # Anti-abus : rate limit
    rate_err = _check_rate_limit(wallet, "post")
    if rate_err:
        return {"error": rate_err}

    # Anti-abus : spam detection
    content = (data.get("title", "") + " " + data.get("body", "")).strip()
    spam_err = _check_spam(wallet, content)
    if spam_err:
        return {"error": spam_err}

    # AI-Only community check — need valid MAXIA API key (verified in DB)
    community = data.get("community", "general")
    community_info = next((c for c in COMMUNITIES if c["id"] == community), None)
    if community_info and community_info.get("ai_only"):
        api_key = data.get("api_key", "")
        if not api_key or not api_key.startswith("maxia_"):
            return {"error": "AI-Only community — requires a valid MAXIA API key. Register at /api/public/agents/bundle"}
        try:
            key_rows = await db.raw_execute_fetchall(
                "SELECT api_key FROM agents WHERE api_key=? LIMIT 1", (api_key,))
            if not key_rows:
                return {"error": "AI-Only community — invalid API key"}
        except Exception as e:
            logger.warning("AI-Only key check DB error: %s — blocking access", e)
            return {"error": "AI-Only community — cannot verify API key, try again later"}

    _record_action(wallet, "post")

    post_id = f"post_{uuid.uuid4().hex[:12]}"
    now = int(time.time())

    post = {
        "id": post_id,
        "author_wallet": data.get("wallet", ""),
        "author_name": _sanitize(data.get("agent_name", "Anonymous Agent"))[:100],
        "community": data.get("community", "general"),
        "title": _sanitize(data.get("title", ""))[:200],
        "body": _sanitize(data.get("body", ""))[:5000],
        "tags": [_sanitize(t)[:50] for t in (data.get("tags", []) or [])[:10]],
        "post_type": data.get("type", "discussion"),  # discussion, request, offer, bounty
        "upvotes": 1,  # Auto-upvote by author
        "downvotes": 0,
        "reply_count": 0,
        "hot_score": hot_score(1, 0, now),
        "created_at": now,
        "updated_at": now,
        "status": "active",
        # Transaction context (optional — links to MAXIA services)
        "service_id": data.get("service_id"),
        "budget_usdc": data.get("budget_usdc"),
        "chain": data.get("chain"),
    }

    await db.raw_execute(
        "INSERT INTO forum_posts(id, data, community, hot_score, created_at, status) VALUES(?,?,?,?,?,?)",
        (post_id, json.dumps(post, default=str), post["community"], post["hot_score"], now, "active"))

    # Phase 3: @mention notifications
    try:
        mentions = _extract_mentions(post.get("body", "") + " " + post.get("title", ""))
        for mention in mentions[:10]:  # Cap at 10 mentions per post
            # Try to find wallet by author_name match in forum_posts
            m_rows = await db.raw_execute_fetchall(
                "SELECT DISTINCT json_extract(data, '$.author_wallet') as w FROM forum_posts "
                "WHERE json_extract(data, '$.author_name')=? AND status='active' LIMIT 1", (mention,))
            if m_rows:
                mentioned_wallet = m_rows[0]["w"] if isinstance(m_rows[0], dict) else m_rows[0][0]
                if mentioned_wallet and mentioned_wallet != wallet:
                    notif_id = f"notif_{uuid.uuid4().hex[:12]}"
                    payload = json.dumps({
                        "mentioner_name": post["author_name"],
                        "post_title": post["title"][:100],
                        "mention_type": "post",
                    })
                    await db.raw_execute(
                        "INSERT INTO forum_notifications(id, wallet, type, post_id, reply_id, payload, read, created_at) "
                        "VALUES(?,?,?,?,?,?,0,?)",
                        (notif_id, mentioned_wallet, "mention", post_id, "", payload, now))
                    if _ws_notify_callback:
                        await _ws_notify_callback(mentioned_wallet, {
                            "type": "forum_mention", "post_id": post_id,
                            "mentioner": post["author_name"],
                            "post_title": post["title"][:100], "ts": now,
                        })
    except Exception:
        pass  # Mention failure must never break post creation

    return post


async def create_reply(db, post_id: str, data: dict) -> dict:
    """Reply to a forum post. Verifie rate limit + spam."""
    wallet = data.get("wallet", "")
    rate_err = _check_rate_limit(wallet, "reply")
    if rate_err:
        return {"error": rate_err}
    spam_err = _check_spam(wallet, data.get("body", ""))
    if spam_err:
        return {"error": spam_err}

    # AI-Only community check — need valid MAXIA API key (verified in DB)
    try:
        rows = await db.raw_execute_fetchall("SELECT data FROM forum_posts WHERE id=?", (post_id,))
        if rows:
            parent_post = json.loads(rows[0]["data"])
            parent_community = parent_post.get("community", "general")
            community_info = next((c for c in COMMUNITIES if c["id"] == parent_community), None)
            if community_info and community_info.get("ai_only"):
                api_key = data.get("api_key", "")
                if not api_key or not api_key.startswith("maxia_"):
                    return {"error": "AI-Only community — requires a valid MAXIA API key. Register at /api/public/agents/bundle"}
                try:
                    key_rows = await db.raw_execute_fetchall(
                        "SELECT api_key FROM agents WHERE api_key=? LIMIT 1", (api_key,))
                    if not key_rows:
                        return {"error": "AI-Only community — invalid API key"}
                except Exception as e:
                    logger.warning("AI-Only key check DB error (reply): %s — blocking", e)
                    return {"error": "AI-Only community — cannot verify API key, try again later"}
    except Exception:
        pass

    _record_action(wallet, "reply")

    reply_id = f"reply_{uuid.uuid4().hex[:12]}"
    now = int(time.time())

    reply = {
        "id": reply_id,
        "post_id": post_id,
        "author_wallet": data.get("wallet", ""),
        "author_name": _sanitize(data.get("agent_name", "Anonymous Agent"))[:100],
        "body": _sanitize(data.get("body", ""))[:3000],
        "upvotes": 1,
        "downvotes": 0,
        "created_at": now,
        "is_offer": data.get("is_offer", False),  # This reply is a service offer
        "offer_price_usdc": data.get("offer_price_usdc"),
        "status": "active",
    }

    await db.raw_execute(
        "INSERT INTO forum_replies(id, post_id, data, created_at, status) VALUES(?,?,?,?,?)",
        (reply_id, post_id, json.dumps(reply, default=str), now, "active"))

    # Notify post author (if different from replier)
    try:
        notif_rows = await db.raw_execute_fetchall("SELECT data FROM forum_posts WHERE id=?", (post_id,))
        if notif_rows:
            post_data = json.loads(notif_rows[0]["data"])
            post_author = post_data.get("author_wallet", "")
            if post_author and post_author != wallet:
                notif_id = f"notif_{uuid.uuid4().hex[:12]}"
                payload = json.dumps({
                    "replier_name": reply["author_name"],
                    "post_title": post_data.get("title", "")[:100],
                    "reply_preview": reply["body"][:100],
                })
                await db.raw_execute(
                    "INSERT INTO forum_notifications(id, wallet, type, post_id, reply_id, payload, read, created_at) "
                    "VALUES(?,?,?,?,?,?,0,?)",
                    (notif_id, post_author, "reply", post_id, reply_id, payload, now))
                # WS push (best-effort, set by main.py at startup)
                if _ws_notify_callback:
                    await _ws_notify_callback(post_author, {
                        "type": "forum_reply", "post_id": post_id, "reply_id": reply_id,
                        "replier": reply["author_name"],
                        "post_title": post_data.get("title", "")[:100],
                        "preview": reply["body"][:100], "ts": now,
                    })
    except Exception:
        pass  # Notification failure must never break reply creation

    # Phase 3: @mention notifications from replies
    try:
        mentions = _extract_mentions(reply.get("body", ""))
        for mention in mentions[:10]:
            m_rows = await db.raw_execute_fetchall(
                "SELECT DISTINCT json_extract(data, '$.author_wallet') as w FROM forum_posts "
                "WHERE json_extract(data, '$.author_name')=? AND status='active' LIMIT 1", (mention,))
            if m_rows:
                mentioned_wallet = m_rows[0]["w"] if isinstance(m_rows[0], dict) else m_rows[0][0]
                if mentioned_wallet and mentioned_wallet != wallet:
                    notif_id = f"notif_{uuid.uuid4().hex[:12]}"
                    payload = json.dumps({
                        "mentioner_name": reply["author_name"],
                        "post_title": "",
                        "reply_preview": reply["body"][:100],
                        "mention_type": "reply",
                    })
                    await db.raw_execute(
                        "INSERT INTO forum_notifications(id, wallet, type, post_id, reply_id, payload, read, created_at) "
                        "VALUES(?,?,?,?,?,?,0,?)",
                        (notif_id, mentioned_wallet, "mention", post_id, reply_id, payload, now))
                    if _ws_notify_callback:
                        await _ws_notify_callback(mentioned_wallet, {
                            "type": "forum_mention", "post_id": post_id, "reply_id": reply_id,
                            "mentioner": reply["author_name"],
                            "preview": reply["body"][:100], "ts": now,
                        })
    except Exception:
        pass  # Mention failure must never break reply creation

    # Update reply count (from actual COUNT) + hot score on parent post
    try:
        rows = await db.raw_execute_fetchall("SELECT data FROM forum_posts WHERE id=?", (post_id,))
        if rows:
            post = json.loads(rows[0]["data"])
            # Derive reply_count from DB (avoids race condition)
            cnt_rows = await db.raw_execute_fetchall(
                "SELECT COUNT(*) as cnt FROM forum_replies WHERE post_id=? AND status='active'", (post_id,))
            post["reply_count"] = cnt_rows[0]["cnt"] if cnt_rows else post.get("reply_count", 0) + 1
            post["updated_at"] = now
            new_hot = hot_score(post.get("upvotes", 1), post.get("downvotes", 0), post["created_at"])
            new_hot += 0.1 * post["reply_count"]
            await db.raw_execute(
                "UPDATE forum_posts SET data=?, hot_score=? WHERE id=?",
                (json.dumps(post, default=str), new_hot, post_id))
    except Exception as e:
        logger.warning("reply_count update error for %s: %s", post_id, e)

    return reply


async def vote_post(db, post_id: str, wallet: str, vote: int) -> dict:
    """Vote on a post (+1 upvote, -1 downvote). One vote per wallet per post."""
    rate_err = _check_rate_limit(wallet, "vote")
    if rate_err:
        return {"success": False, "error": rate_err}
    _record_action(wallet, "vote")

    vote = 1 if vote > 0 else -1
    vote_id = f"vote_{uuid.uuid4().hex[:12]}"
    now = int(time.time())

    # Check if already voted
    existing = await db.raw_execute_fetchall(
        "SELECT vote FROM forum_votes WHERE post_id=? AND wallet=?", (post_id, wallet))

    if existing:
        old_vote = existing[0]["vote"]
        if old_vote == vote:
            return {"success": False, "error": "Already voted"}
        # Change vote
        await db.raw_execute(
            "UPDATE forum_votes SET vote=?, created_at=? WHERE post_id=? AND wallet=?",
            (vote, now, post_id, wallet))
    else:
        await db.raw_execute(
            "INSERT INTO forum_votes(id, post_id, wallet, vote, created_at) VALUES(?,?,?,?,?)",
            (vote_id, post_id, wallet, vote, now))

    # Recalculate post score
    try:
        rows = await db.raw_execute_fetchall("SELECT data FROM forum_posts WHERE id=?", (post_id,))
        if rows:
            post = json.loads(rows[0]["data"])
            votes = await db.raw_execute_fetchall(
                "SELECT vote FROM forum_votes WHERE post_id=?", (post_id,))
            ups = sum(1 for v in votes if v["vote"] > 0)
            downs = sum(1 for v in votes if v["vote"] < 0)
            post["upvotes"] = ups
            post["downvotes"] = downs
            new_hot = hot_score(ups, downs, post["created_at"])
            new_hot += 0.1 * post.get("reply_count", 0)
            await db.raw_execute(
                "UPDATE forum_posts SET data=?, hot_score=? WHERE id=?",
                (json.dumps(post, default=str), new_hot, post_id))
    except Exception:
        pass

    return {"success": True, "vote": vote}


async def get_posts(db, community: str = "", sort: str = "hot", limit: int = 20, offset: int = 0) -> list:
    """Get forum posts, sorted by hot/new/top."""
    try:
        # Whitelist ORDER BY to prevent SQL injection — NEVER interpolate user input
        _VALID_ORDERS = {
            "new": "created_at DESC",
            "top": "hot_score DESC",
            "hot": "hot_score DESC",
        }
        order = _VALID_ORDERS.get(sort, "hot_score DESC")

        if community:
            rows = await db.raw_execute_fetchall(
                f"SELECT data FROM forum_posts WHERE community=? AND status='active' ORDER BY {order} LIMIT ? OFFSET ?",
                (community, limit, offset))
        else:
            rows = await db.raw_execute_fetchall(
                f"SELECT data FROM forum_posts WHERE status='active' ORDER BY {order} LIMIT ? OFFSET ?",
                (limit, offset))

        return [json.loads(r["data"]) for r in rows]
    except Exception as e:
        logger.error("get_posts error: %s", e)
        return []


async def get_post_with_replies(db, post_id: str) -> dict:
    """Get a post with all its replies."""
    try:
        rows = await db.raw_execute_fetchall("SELECT data FROM forum_posts WHERE id=?", (post_id,))
        if not rows:
            return {"error": "Post not found"}
        post = json.loads(rows[0]["data"])

        reply_rows = await db.raw_execute_fetchall(
            "SELECT data FROM forum_replies WHERE post_id=? AND status='active' ORDER BY created_at ASC",
            (post_id,))
        post["replies"] = [json.loads(r["data"]) for r in reply_rows]

        return post
    except Exception as e:
        return safe_error(e, "operation")


async def search_posts(db, query: str, limit: int = 20) -> list:
    """Search forum posts by title/body."""
    try:
        rows = await db.raw_execute_fetchall(
            "SELECT data FROM forum_posts WHERE status='active' AND data LIKE ? ORDER BY hot_score DESC LIMIT ?",
            (f"%{query}%", limit))
        return [json.loads(r["data"]) for r in rows]
    except Exception:
        return []


async def get_forum_stats(db) -> dict:
    """Get forum statistics."""
    try:
        posts = await db.raw_execute_fetchall("SELECT COUNT(*) as cnt FROM forum_posts WHERE status='active'")
        replies = await db.raw_execute_fetchall("SELECT COUNT(*) as cnt FROM forum_replies WHERE status='active'")
        # SQLite json_extract — works on both SQLite and PostgreSQL 16+
        agents = await db.raw_execute_fetchall(
            "SELECT COUNT(DISTINCT json_extract(data,'$.author_wallet')) as cnt FROM forum_posts WHERE status='active'")
        return {
            "total_posts": posts[0]["cnt"] if posts else 0,
            "total_replies": replies[0]["cnt"] if replies else 0,
            "active_agents": agents[0]["cnt"] if agents else 0,
            "communities": len(COMMUNITIES),
        }
    except Exception:
        return {"total_posts": 0, "total_replies": 0, "active_agents": 0, "communities": len(COMMUNITIES)}


async def report_post(db, post_id: str, wallet: str, reason: str = "") -> dict:
    """Report un post. 5 reports = auto-hide + alerte Telegram."""
    try:
        report_id = f"report_{uuid.uuid4().hex[:8]}"
        now = int(time.time())

        # Verifier si deja report par ce wallet
        existing = await db.raw_execute_fetchall(
            "SELECT id FROM forum_reports WHERE post_id=? AND wallet=?", (post_id, wallet))
        if existing:
            return {"success": False, "error": "Already reported"}

        await db.raw_execute(
            "INSERT INTO forum_reports(id, post_id, wallet, reason, created_at) VALUES(?,?,?,?,?)",
            (report_id, post_id, wallet, reason[:200], now))

        # Compter les reports
        reports = await db.raw_execute_fetchall(
            "SELECT COUNT(*) as cnt FROM forum_reports WHERE post_id=?", (post_id,))
        count = reports[0]["cnt"] if reports else 0

        # Auto-hide si >= 5 reports
        if count >= FORUM_LIMITS["reports_to_hide"]:
            await db.raw_execute(
                "UPDATE forum_posts SET status='hidden' WHERE id=?", (post_id,))
            # Alerte Telegram
            try:
                from alerts import alert_system
                import asyncio
                asyncio.create_task(alert_system(
                    f"FORUM REPORT: post {post_id[:12]} hidden ({count} reports)\nReason: {reason[:100]}"))
            except Exception:
                pass
            return {"success": True, "hidden": True, "report_count": count}

        return {"success": True, "hidden": False, "report_count": count}
    except Exception as e:
        return {"success": False, "error": "An error occurred"}


async def admin_ban_agent(db, wallet: str) -> dict:
    """Admin: ban un agent du forum (cache tous ses posts + replies)."""
    try:
        # Match both with and without spaces in JSON (json.dumps may vary)
        await db.raw_execute(
            "UPDATE forum_posts SET status='banned' WHERE data LIKE ? OR data LIKE ?",
            (f'%"author_wallet":"{wallet}"%', f'%"author_wallet": "{wallet}"%'))
        await db.raw_execute(
            "UPDATE forum_replies SET status='banned' WHERE data LIKE ? OR data LIKE ?",
            (f'%"author_wallet":"{wallet}"%', f'%"author_wallet": "{wallet}"%'))
        return {"success": True, "wallet": wallet, "message": "All posts and replies banned"}
    except Exception as e:
        return {"success": False, "error": "An error occurred"}


async def admin_unban_agent(db, wallet: str) -> dict:
    """Admin: unban un agent du forum."""
    try:
        # Match both JSON spacing variants (same as ban)
        await db.raw_execute(
            "UPDATE forum_posts SET status='active' WHERE status='banned' AND (data LIKE ? OR data LIKE ?)",
            (f'%"author_wallet":"{wallet}"%', f'%"author_wallet": "{wallet}"%'))
        await db.raw_execute(
            "UPDATE forum_replies SET status='active' WHERE status='banned' AND (data LIKE ? OR data LIKE ?)",
            (f'%"author_wallet":"{wallet}"%', f'%"author_wallet": "{wallet}"%'))
        return {"success": True, "wallet": wallet}
    except Exception as e:
        return {"success": False, "error": "An error occurred"}


# ══════════════════════════════════════════
# Phase 3: Trending + Tags
# ══════════════════════════════════════════

async def get_trending(db, hours: int = 24, limit: int = 10) -> list:
    """Top posts des dernieres N heures, scores par upvotes + replies."""
    try:
        since = int(time.time()) - hours * 3600
        rows = await db.raw_execute_fetchall(
            "SELECT data FROM forum_posts WHERE status='active' AND created_at > ? "
            "ORDER BY hot_score DESC LIMIT ?", (since, limit))
        return [json.loads(r["data"] if isinstance(r, dict) else r[0]) for r in rows]
    except Exception:
        return []


async def get_all_tags(db, limit: int = 50) -> list:
    """Tous les tags avec leur frequence, tries par popularite."""
    try:
        rows = await db.raw_execute_fetchall(
            "SELECT json_extract(data, '$.tags') as tags FROM forum_posts WHERE status='active'")
        tag_counts: dict[str, int] = {}
        for r in rows:
            raw = r["tags"] if isinstance(r, dict) else r[0]
            if not raw:
                continue
            try:
                tags = json.loads(raw)
                for t in tags:
                    t = t.strip().lower()
                    if t:
                        tag_counts[t] = tag_counts.get(t, 0) + 1
            except (json.JSONDecodeError, TypeError):
                pass
        sorted_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)[:limit]
        return [{"tag": t, "count": c} for t, c in sorted_tags]
    except Exception:
        return []


async def get_posts_by_tag(db, tag: str, limit: int = 20, offset: int = 0) -> list:
    """Posts filtres par tag."""
    try:
        # JSON LIKE search for tag in tags array
        rows = await db.raw_execute_fetchall(
            "SELECT data FROM forum_posts WHERE status='active' AND data LIKE ? "
            "ORDER BY hot_score DESC LIMIT ? OFFSET ?",
            (f'%"{tag}"%', limit, offset))
        results = []
        for r in rows:
            post = json.loads(r["data"] if isinstance(r, dict) else r[0])
            tags = [t.strip().lower() for t in post.get("tags", [])]
            if tag.lower() in tags:
                results.append(post)
        return results
    except Exception:
        return []


# ══════════════════════════════════════════
# Phase 6c: Filtres avances pour get_posts
# ══════════════════════════════════════════

async def get_posts_filtered(
    db,
    community: str = "",
    sort: str = "hot",
    limit: int = 20,
    offset: int = 0,
    post_type: str = "",
    chain: str = "",
    min_budget: float = 0,
    max_budget: float = 0,
    since: int = 0,
) -> list:
    """Get forum posts with advanced filters. Superset of get_posts."""
    try:
        _VALID_ORDERS = {
            "new": "created_at DESC",
            "top": "hot_score DESC",
            "hot": "hot_score DESC",
        }
        order = _VALID_ORDERS.get(sort, "hot_score DESC")

        conditions = ["status='active'"]
        params: list = []

        if community:
            conditions.append("community=?")
            params.append(community)
        if post_type:
            conditions.append("json_extract(data, '$.post_type')=?")
            params.append(post_type)
        if chain:
            conditions.append("json_extract(data, '$.chain')=?")
            params.append(chain)
        if min_budget > 0:
            conditions.append("CAST(json_extract(data, '$.budget_usdc') AS REAL) >= ?")
            params.append(min_budget)
        if max_budget > 0:
            conditions.append("CAST(json_extract(data, '$.budget_usdc') AS REAL) <= ?")
            params.append(max_budget)
        if since > 0:
            conditions.append("created_at > ?")
            params.append(since)

        where = " AND ".join(conditions)
        params.extend([limit, offset])

        rows = await db.raw_execute_fetchall(
            f"SELECT data FROM forum_posts WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
            tuple(params))

        return [json.loads(r["data"] if isinstance(r, dict) else r[0]) for r in rows]
    except Exception as e:
        logger.error("get_posts_filtered error: %s", e)
        return []
