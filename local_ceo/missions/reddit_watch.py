"""Mission — Reddit watch (read-only) (Plan CEO V9 / mission 7).

Monitors a handful of crypto + AI subreddits via the PUBLIC Reddit
JSON feed (no login, no OAuth, no write actions) and alerts Alexis via
Telegram when:

1. MAXIA / maxiaworld.app is mentioned anywhere in a new post or comment
2. A post contains keywords that signal an opportunity where MAXIA could
   be a helpful answer (e.g. "need AI agent marketplace", "multi-chain
   swap SDK", "GPU rental crypto")

The mission writes nothing to Reddit — Alexis stays the only one
posting. Pure signal routing. Avoids ban risk entirely.

Rate limit: runs at most once per hour. Respects Reddit's free
unauthenticated JSON API (max ~60 req/min).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Optional

log = logging.getLogger("ceo")

# Subreddits to watch. Only public SFW crypto + AI communities.
SUBREDDITS: list[str] = [
    "CryptoCurrency",
    "solana",
    "ethfinance",
    "defi",
    "AI_Agents",
    "LocalLLaMA",
    "LangChain",
    "MachineLearning",
]

# Phrases that trigger an alert. Case-insensitive substring match.
OPPORTUNITY_KEYWORDS: list[str] = [
    "ai agent marketplace",
    "agent to agent",
    "ai to ai",
    "multi-chain swap",
    "multichain swap",
    "gpu rental crypto",
    "mcp server crypto",
    "langchain crypto",
    "crewai crypto",
    "solana ai agent",
    "ai trading bot sdk",
    "crypto agent sdk",
]

# Mentions of MAXIA itself (always alert).
MAXIA_KEYWORDS: list[str] = [
    "maxia",
    "maxiaworld",
    "maxiaworld.app",
]

WATCH_INTERVAL_SECONDS: int = 3600   # 1 hour


def _already_seen(mem: dict, post_id: str) -> bool:
    seen = mem.get("_reddit_seen_ids", [])
    return post_id in seen


def _mark_seen(mem: dict, post_id: str) -> None:
    seen: list = mem.setdefault("_reddit_seen_ids", [])
    seen.append(post_id)
    # Keep only the last 1000 IDs
    if len(seen) > 1000:
        mem["_reddit_seen_ids"] = seen[-1000:]


def _classify(text: str) -> Optional[str]:
    """Return 'maxia_mention', 'opportunity', or None."""
    lower = text.lower()
    for kw in MAXIA_KEYWORDS:
        if kw in lower:
            return "maxia_mention"
    for kw in OPPORTUNITY_KEYWORDS:
        if kw in lower:
            return "opportunity"
    return None


async def _fetch_subreddit(client, subreddit: str) -> list[dict]:
    """GET https://www.reddit.com/r/<sub>/new.json?limit=25 (no auth)."""
    import httpx
    url = f"https://www.reddit.com/r/{subreddit}/new.json?limit=25"
    headers = {
        "User-Agent": "MAXIA-Watch/1.0 (+https://maxiaworld.app)",
    }
    try:
        resp = await client.get(url, headers=headers, timeout=15)
    except httpx.HTTPError as e:
        log.debug("[reddit] http error %s: %s", subreddit, e)
        return []
    if resp.status_code != 200:
        log.debug("[reddit] HTTP %d on r/%s", resp.status_code, subreddit)
        return []
    try:
        data = resp.json()
    except Exception:
        return []

    children = data.get("data", {}).get("children", []) or []
    posts = []
    for child in children:
        post = child.get("data", {}) if isinstance(child, dict) else {}
        posts.append(post)
    return posts


async def _notify(kind: str, post: dict, subreddit: str) -> None:
    """Send a Telegram alert to Alexis about this opportunity/mention."""
    try:
        from notifier import notify_telegram_alert
    except ImportError:
        return

    title = str(post.get("title", ""))[:200]
    url = f"https://reddit.com{post.get('permalink', '')}"
    author = str(post.get("author", "?"))[:32]
    excerpt = str(post.get("selftext", ""))[:300].replace("\n", " ")

    if kind == "maxia_mention":
        header = "MAXIA MENTION on Reddit"
    else:
        header = "Reddit opportunity"

    text = (
        f"{header}\n"
        f"r/{subreddit} by u/{author}\n\n"
        f"{title}\n\n"
        f"{excerpt}\n\n"
        f"{url}\n\n"
        f"(Reddit watch — do NOT reply from the MAXIA outreach bot. "
        f"Reply manually from your personal account if relevant.)"
    )
    try:
        await notify_telegram_alert(header, text)
    except Exception as e:
        log.warning("[reddit] notify failed: %s", e)


async def mission_reddit_watch(mem: dict, actions: dict) -> None:
    """Scan watched subreddits and alert on relevant new posts."""
    last_run = float(mem.get("_reddit_watch_last_run", 0) or 0)
    if time.time() - last_run < WATCH_INTERVAL_SECONDS:
        return

    try:
        import httpx
    except ImportError:
        log.warning("[reddit] httpx unavailable — skip")
        return

    new_mentions = 0
    new_opportunities = 0

    async with httpx.AsyncClient() as client:
        for sub in SUBREDDITS:
            posts = await _fetch_subreddit(client, sub)
            for post in posts:
                post_id = str(post.get("id", ""))
                if not post_id or _already_seen(mem, post_id):
                    continue
                blob = f"{post.get('title', '')} {post.get('selftext', '')}"
                kind = _classify(blob)
                if kind is None:
                    _mark_seen(mem, post_id)
                    continue

                await _notify(kind, post, sub)
                _mark_seen(mem, post_id)

                if kind == "maxia_mention":
                    new_mentions += 1
                    mem.setdefault("reddit_mentions", []).append({
                        "id": post_id,
                        "subreddit": sub,
                        "date": datetime.now().isoformat(),
                        "title": str(post.get("title", ""))[:200],
                    })
                else:
                    new_opportunities += 1

    mem["_reddit_watch_last_run"] = time.time()
    if new_mentions or new_opportunities:
        log.info(
            "[reddit] %d mentions, %d opportunities", new_mentions, new_opportunities,
        )
    actions["counts"]["reddit_watch"] = (
        actions["counts"].get("reddit_watch", 0) + new_mentions + new_opportunities
    )
