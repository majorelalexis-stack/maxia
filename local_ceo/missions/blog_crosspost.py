"""Mission — Blog cross-post to Dev.to + Hashnode (Plan CEO V9 / mission 4).

Cross-posts the 11 static SEO blog articles from ``frontend/blog/`` to
Dev.to and Hashnode using their public REST APIs. Each cross-post
includes a ``canonical_url`` pointing back to maxiaworld.app so Google
credits the original without penalizing duplicate content.

Cadence:
- 1 cross-post per day max (both platforms combined)
- Requires API keys in .env: DEVTO_API_KEY, HASHNODE_API_KEY
- If either key is missing, the respective platform is skipped
- Once all 11 articles are published on a platform, the mission
  becomes a no-op for that platform.

State lives in ``mem["blog_crossposts"]`` as a dict:
    {"dev.to": set-of-published-slugs, "hashnode": set-of-published-slugs}

The mission reads the HTML file, strips tags to markdown-ish text, and
POSTs to each platform. Image handling is left to the canonical URL.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger("ceo")

_REPO_ROOT = Path(__file__).parent.parent.parent
BLOG_DIR = _REPO_ROOT / "frontend" / "blog"

BLOG_SLUGS: list[str] = [
    "ai-agent-economy-2026-complete-guide",
    "what-is-ai-to-ai-marketplace",
    "trade-crypto-15-blockchains-ai-agent",
    "usdc-escrow-ai-services-guide",
    "top-mcp-tools-crypto-agents-2026",
    "akash-vs-aws-gpu-ai-inference",
    "langchain-vs-crewai-crypto-bots",
    "pyth-vs-chainlink-oracle-choice",
    "paper-trading-ai-agents-explained",
    "bitcoin-lightning-l402-ai-micropayments",
    "agent-to-agent-protocol-a2a-intro",
]

DEFAULT_TAGS: list[str] = ["ai", "crypto", "webdev", "blockchain"]
DEVTO_API = "https://dev.to/api/articles"
HASHNODE_API = "https://gql.hashnode.com/"


def _canonical_url(slug: str) -> str:
    return f"https://maxiaworld.app/blog/{slug}"


def _html_to_markdown(html: str) -> str:
    """Very simple HTML -> Markdown-ish conversion for cross-post bodies.

    Not a full converter — just enough so the target platforms render
    the article acceptably. The canonical_url ensures readers click
    through to the polished maxiaworld.app version anyway.
    """
    # Strip <head>, <style>, <script>
    html = re.sub(r"<head.*?</head>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<style.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<script.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<nav.*?</nav>", "", html, flags=re.DOTALL | re.IGNORECASE)

    # Headings
    html = re.sub(r"<h1[^>]*>(.*?)</h1>", r"# \1\n\n", html,
                  flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<h2[^>]*>(.*?)</h2>", r"## \1\n\n", html,
                  flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<h3[^>]*>(.*?)</h3>", r"### \1\n\n", html,
                  flags=re.DOTALL | re.IGNORECASE)

    # Paragraphs and line breaks
    html = re.sub(r"<p[^>]*>(.*?)</p>", r"\1\n\n", html,
                  flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)

    # Code
    html = re.sub(r"<code[^>]*>(.*?)</code>", r"`\1`", html,
                  flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<pre[^>]*>(.*?)</pre>", r"```\n\1\n```\n",
                  html, flags=re.DOTALL | re.IGNORECASE)

    # Bold + italic + links
    html = re.sub(r"<(b|strong)[^>]*>(.*?)</(b|strong)>", r"**\2**",
                  html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', r"[\2](\1)",
                  html, flags=re.DOTALL | re.IGNORECASE)

    # Lists
    html = re.sub(r"<li[^>]*>(.*?)</li>", r"- \1\n", html,
                  flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"</?(ul|ol)[^>]*>", "\n", html, flags=re.IGNORECASE)

    # Tables (crude)
    html = re.sub(r"<t[hd][^>]*>(.*?)</t[hd]>", r"| \1 ", html,
                  flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"</tr>", "|\n", html, flags=re.IGNORECASE)
    html = re.sub(r"</?(table|thead|tbody|tr)[^>]*>", "\n", html,
                  flags=re.IGNORECASE)

    # Strip remaining tags
    html = re.sub(r"<[^>]+>", "", html)

    # Decode a handful of common entities
    replacements = {
        "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"',
        "&#39;": "'", "&nbsp;": " ",
    }
    for k, v in replacements.items():
        html = html.replace(k, v)

    # Collapse whitespace
    html = re.sub(r"\n{3,}", "\n\n", html)
    return html.strip()


def _extract_title(html: str, slug: str) -> str:
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.DOTALL | re.IGNORECASE)
    if m:
        return re.sub(r"<[^>]+>", "", m.group(1)).strip()[:120]
    return slug.replace("-", " ").title()


async def _devto_publish(
    title: str, markdown: str, canonical: str, tags: list[str],
) -> Optional[str]:
    """Create a Dev.to article. Returns the published URL or None."""
    api_key = os.getenv("DEVTO_API_KEY", "")
    if not api_key:
        return None
    try:
        import httpx
    except ImportError:
        return None

    body_md = f"> Originally published at {canonical}\n\n{markdown}"
    payload = {
        "article": {
            "title": title,
            "published": True,
            "body_markdown": body_md,
            "tags": [t[:25] for t in tags][:4],
            "canonical_url": canonical,
        }
    }
    headers = {"api-key": api_key, "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(DEVTO_API, json=payload, headers=headers)
    except httpx.HTTPError as e:
        log.warning("[crosspost] dev.to http error: %s", e)
        return None
    if resp.status_code not in (200, 201):
        log.warning("[crosspost] dev.to HTTP %d: %s",
                    resp.status_code, resp.text[:200])
        return None
    try:
        data = resp.json()
        return data.get("url") or data.get("canonical_url")
    except Exception:
        return None


async def _hashnode_publish(
    title: str, markdown: str, canonical: str, tags: list[str],
) -> Optional[str]:
    """Create a Hashnode post via the GraphQL API. Returns URL or None."""
    api_key = os.getenv("HASHNODE_API_KEY", "")
    publication_id = os.getenv("HASHNODE_PUBLICATION_ID", "")
    if not api_key or not publication_id:
        return None
    try:
        import httpx
    except ImportError:
        return None

    body_md = f"> Originally published at {canonical}\n\n{markdown}"
    # Hashnode expects at most 5 tag slugs
    tag_objs = [{"slug": t, "name": t.title()} for t in tags[:5]]
    mutation = """
    mutation PublishPost($input: PublishPostInput!) {
      publishPost(input: $input) {
        post { id, url, title }
      }
    }
    """
    variables = {
        "input": {
            "title": title,
            "contentMarkdown": body_md,
            "publicationId": publication_id,
            "originalArticleURL": canonical,
            "tags": tag_objs,
        }
    }
    headers = {"Authorization": api_key, "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                HASHNODE_API,
                json={"query": mutation, "variables": variables},
                headers=headers,
            )
    except httpx.HTTPError as e:
        log.warning("[crosspost] hashnode http error: %s", e)
        return None
    if resp.status_code != 200:
        log.warning("[crosspost] hashnode HTTP %d: %s",
                    resp.status_code, resp.text[:200])
        return None
    try:
        data = resp.json()
        post = data.get("data", {}).get("publishPost", {}).get("post", {})
        return post.get("url")
    except Exception:
        return None


def _load_article(slug: str) -> Optional[tuple[str, str]]:
    path = BLOG_DIR / f"{slug}.html"
    if not path.exists():
        return None
    try:
        html = path.read_text(encoding="utf-8")
    except OSError as e:
        log.warning("[crosspost] cannot read %s: %s", path, e)
        return None
    title = _extract_title(html, slug)
    markdown = _html_to_markdown(html)
    return title, markdown


async def mission_blog_crosspost(mem: dict, actions: dict) -> None:
    """Cross-post the next unpublished blog article to Dev.to + Hashnode."""
    state = mem.setdefault("blog_crossposts", {"dev_to": [], "hashnode": []})
    devto_done: set[str] = set(state.get("dev_to", []))
    hashnode_done: set[str] = set(state.get("hashnode", []))

    # Rate limit: 1 cross-post per day across both platforms combined
    today = datetime.now().strftime("%Y-%m-%d")
    if mem.get("_blog_crosspost_last_date") == today:
        return

    pending_devto = [s for s in BLOG_SLUGS if s not in devto_done]
    pending_hashnode = [s for s in BLOG_SLUGS if s not in hashnode_done]

    if not pending_devto and not pending_hashnode:
        log.debug("[crosspost] all articles already cross-posted")
        return

    # Pick the first pending slug (same slug for both platforms if possible)
    slug = None
    if pending_devto:
        slug = pending_devto[0]
    elif pending_hashnode:
        slug = pending_hashnode[0]

    if slug is None:
        return

    article = _load_article(slug)
    if article is None:
        log.warning("[crosspost] article not found: %s", slug)
        return
    title, markdown = article
    canonical = _canonical_url(slug)

    if slug in pending_devto:
        url = await _devto_publish(title, markdown, canonical, DEFAULT_TAGS)
        if url:
            devto_done.add(slug)
            log.info("[crosspost] dev.to OK %s -> %s", slug, url)
            mem.setdefault("blog_crossposts_log", []).append({
                "platform": "dev.to", "slug": slug, "url": url, "date": today,
            })

    if slug in pending_hashnode:
        url = await _hashnode_publish(title, markdown, canonical, DEFAULT_TAGS)
        if url:
            hashnode_done.add(slug)
            log.info("[crosspost] hashnode OK %s -> %s", slug, url)
            mem.setdefault("blog_crossposts_log", []).append({
                "platform": "hashnode", "slug": slug, "url": url, "date": today,
            })

    state["dev_to"] = sorted(devto_done)
    state["hashnode"] = sorted(hashnode_done)
    mem["_blog_crosspost_last_date"] = today
    actions["counts"]["blog_crosspost"] = 1
