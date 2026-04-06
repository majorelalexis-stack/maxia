"""MAXIA CEO Blog Writer — Genere et publie 1 article/jour sur l'actualite Web3.

Fetch RSS feeds -> filtre 24h -> selectionne top actus -> genere article via Ollama -> publie sur VPS.
Lance quotidiennement par ceo_local_v2.py a 8h UTC.

Usage standalone: python blog_writer.py
"""
import logging
import json
import time
import re
import hashlib
from datetime import datetime, timezone, timedelta
from typing import Optional
from xml.etree import ElementTree

logger = logging.getLogger(__name__)

# ── Configuration ──

try:
    from config_local import VPS_URL, OLLAMA_URL, OLLAMA_MODEL, CEO_API_KEY
except ImportError:
    VPS_URL = "https://maxiaworld.app"
    OLLAMA_URL = "http://localhost:11434"
    OLLAMA_MODEL = "qwen3.5:27b"
    CEO_API_KEY = ""

RSS_FEEDS = [
    {"name": "CoinDesk", "url": "https://www.coindesk.com/arc/outboundfeeds/rss/"},
    {"name": "The Block", "url": "https://www.theblock.co/rss.xml"},
    {"name": "CoinTelegraph", "url": "https://cointelegraph.com/rss"},
    {"name": "Decrypt", "url": "https://decrypt.co/feed"},
    {"name": "Blockworks", "url": "https://blockworks.co/feed"},
]

# Keywords prioritaires pour MAXIA
PRIORITY_KEYWORDS = [
    "ai agent", "autonomous agent", "ai marketplace", "mcp", "a2a",
    "solana", "base", "ethereum", "polygon", "arbitrum", "avalanche",
    "defi", "yield", "swap", "dex", "escrow", "gpu", "inference",
    "tokenized stock", "rwa", "stablecoin", "usdc",
    "sui", "aptos", "near", "ton", "sei", "tron", "bnb",
    "llm", "fine-tun", "ollama", "groq", "mistral", "anthropic", "openai",
]

# Jours -> categories
DAY_CATEGORIES = {
    0: "market-analysis",   # Lundi
    1: "tech-deep-dive",    # Mardi
    2: "market-analysis",   # Mercredi
    3: "tech-deep-dive",    # Jeudi
    4: "market-analysis",   # Vendredi
    5: "weekly-recap",      # Samedi
    6: "general",           # Dimanche
}


def _fetch_rss(url: str, timeout: int = 15) -> list[dict]:
    """Fetch et parse un flux RSS. Retourne liste de {title, summary, link, published}."""
    import urllib.request
    items = []
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "MAXIA-CEO/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        root = ElementTree.fromstring(data)

        # RSS 2.0 format
        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            summary = (item.findtext("description") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub = (item.findtext("pubDate") or "").strip()
            if title:
                # Clean HTML from summary
                summary = re.sub(r"<[^>]+>", "", summary)[:500]
                items.append({"title": title, "summary": summary, "link": link, "published": pub})

        # Atom format fallback
        if not items:
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            for entry in root.findall(".//atom:entry", ns):
                title = (entry.findtext("atom:title", namespaces=ns) or "").strip()
                summary = (entry.findtext("atom:summary", namespaces=ns) or "").strip()
                link_el = entry.find("atom:link", ns)
                link = link_el.get("href", "") if link_el is not None else ""
                pub = (entry.findtext("atom:published", namespaces=ns) or "").strip()
                if title:
                    summary = re.sub(r"<[^>]+>", "", summary)[:500]
                    items.append({"title": title, "summary": summary, "link": link, "published": pub})

    except Exception as e:
        logger.warning("RSS fetch failed for %s: %s", url, e)
    return items


def _score_article(article: dict) -> float:
    """Score un article par pertinence MAXIA. Plus c'est haut, plus c'est pertinent."""
    text = (article.get("title", "") + " " + article.get("summary", "")).lower()
    score = 0.0
    for kw in PRIORITY_KEYWORDS:
        if kw in text:
            score += 2.0
    # Bonus pour AI + crypto combo
    has_ai = any(k in text for k in ["ai agent", "autonomous", "llm", "inference", "mcp"])
    has_crypto = any(k in text for k in ["solana", "ethereum", "defi", "swap", "blockchain"])
    if has_ai and has_crypto:
        score += 5.0
    return score


def fetch_top_news(max_articles: int = 5) -> list[dict]:
    """Fetch toutes les sources RSS, filtre 24h, retourne les top articles par pertinence."""
    all_articles = []
    for feed in RSS_FEEDS:
        items = _fetch_rss(feed["url"])
        for item in items:
            item["source"] = feed["name"]
        all_articles.extend(items)
        logger.info("RSS %s: %d articles", feed["name"], len(items))

    if not all_articles:
        logger.warning("No RSS articles fetched from any source")
        return []

    # Score et trie par pertinence
    for a in all_articles:
        a["_score"] = _score_article(a)
    all_articles.sort(key=lambda x: x["_score"], reverse=True)

    # Deduplicate par titre similaire
    seen = set()
    unique = []
    for a in all_articles:
        key = re.sub(r"[^a-z0-9]", "", a["title"].lower())[:50]
        if key not in seen:
            seen.add(key)
            unique.append(a)

    return unique[:max_articles]


def generate_article(news: list[dict], category: str) -> Optional[dict]:
    """Genere un article blog via Ollama (Qwen 3 14B)."""
    import urllib.request

    if not news:
        return None

    # Build the news context
    news_context = ""
    for i, n in enumerate(news, 1):
        news_context += f"{i}. [{n['source']}] {n['title']}\n   {n['summary'][:200]}\n   Source: {n['link']}\n\n"

    today = datetime.now(timezone.utc).strftime("%B %d, %Y")

    prompt = f"""You are the CEO AI of MAXIA, the first AI-to-AI marketplace on 14 blockchains (Solana, Base, Ethereum, etc.).
Write a blog article about today's Web3 news for the MAXIA blog. Date: {today}. Category: {category}.

Here are today's top Web3 news stories:

{news_context}

INSTRUCTIONS:
- Write in English, professional but accessible tone
- Title: catchy, specific, under 80 characters
- Structure: intro paragraph, then 3-5 sections (one per news story), then conclusion
- Each section: summarize the news, then add YOUR analysis of how it impacts the AI agent economy and MAXIA marketplace
- Include relevant context about MAXIA features when natural (escrow, 65 tokens, GPU rental, MCP tools, etc.)
- DO NOT invent facts or numbers — only use information from the sources above
- DO NOT add markdown image links
- Length: 500-800 words
- End with a brief outlook for the week ahead

FORMAT your response as JSON:
{{"title": "...", "body": "...(markdown)...", "tags": ["tag1", "tag2", ...], "summary": "...(2 sentences max)..."}}

Return ONLY the JSON, no other text."""

    try:
        payload = json.dumps({
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.7, "num_predict": 2048},
        }).encode()

        req = urllib.request.Request(
            f"{OLLAMA_URL}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read())

        response_text = result.get("response", "")

        # Extract JSON from response (handle markdown code blocks)
        response_text = response_text.strip()
        if response_text.startswith("```"):
            response_text = re.sub(r"^```(?:json)?\n?", "", response_text)
            response_text = re.sub(r"\n?```$", "", response_text)

        # Handle /think tags from Qwen 3
        if "<think>" in response_text:
            response_text = re.sub(r"<think>.*?</think>", "", response_text, flags=re.DOTALL).strip()

        article = json.loads(response_text)

        # Validate
        if not article.get("title") or not article.get("body"):
            logger.error("Generated article missing title or body")
            return None

        if len(article["body"]) < 300:
            logger.warning("Generated article too short (%d chars), retrying not implemented", len(article["body"]))
            return None

        article["category"] = category
        return article

    except json.JSONDecodeError as e:
        logger.error("Failed to parse LLM response as JSON: %s", e)
        logger.debug("Raw response: %s", response_text[:500] if 'response_text' in dir() else "N/A")
        return None
    except Exception as e:
        logger.error("Ollama generate error: %s", e)
        return None


def publish_article(article: dict, ceo_key: str) -> Optional[dict]:
    """Publie l'article sur le VPS via l'API admin."""
    import urllib.request

    payload = json.dumps({
        "title": article["title"],
        "body": article["body"],
        "summary": article.get("summary", ""),
        "category": article.get("category", "market-analysis"),
        "tags": article.get("tags", []),
        "status": "published",
        "author": "MAXIA CEO AI",
    }).encode()

    try:
        req = urllib.request.Request(
            f"{VPS_URL}/api/admin/blog/create",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "X-CEO-Key": ceo_key,
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())

        if result.get("success"):
            logger.info("Blog article published: %s (slug: %s)", article["title"], result.get("slug"))
            return result
        else:
            logger.error("Blog publish failed: %s", result)
            return None
    except Exception as e:
        logger.error("Blog publish error: %s", e)
        return None


def check_already_posted_today(ceo_key: str) -> bool:
    """Verifie si un article a deja ete poste aujourd'hui."""
    import urllib.request
    try:
        req = urllib.request.Request(f"{VPS_URL}/api/public/blog?limit=1")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        posts = data.get("posts", [])
        if not posts:
            return False
        latest = posts[0]
        latest_ts = latest.get("published_at") or latest.get("created_at") or 0
        today_start = int(datetime.now(timezone.utc).replace(hour=0, minute=0, second=0).timestamp())
        return int(latest_ts) >= today_start
    except Exception:
        return False


async def run_daily_blog():
    """Point d'entree principal — appele par le scheduler CEO."""
    ceo_key = CEO_API_KEY
    if not ceo_key:
        logger.error("CEO_API_KEY not found — cannot publish blog")
        return None

    # Check if already posted today
    if check_already_posted_today(ceo_key):
        logger.info("Blog article already posted today, skipping")
        return None

    # Determine category based on day of week
    today_dow = datetime.now(timezone.utc).weekday()
    category = DAY_CATEGORIES.get(today_dow, "market-analysis")

    # Fetch news
    logger.info("Fetching RSS feeds...")
    news = fetch_top_news(max_articles=5)
    if not news:
        logger.warning("No news fetched, skipping blog post")
        return None
    logger.info("Got %d top articles", len(news))

    # Generate article
    logger.info("Generating article (category: %s)...", category)
    article = generate_article(news, category)
    if not article:
        logger.error("Article generation failed")
        return None
    logger.info("Article generated: %s (%d chars)", article["title"], len(article["body"]))

    # Publish
    result = publish_article(article, ceo_key)
    if result:
        # Log to actions_today.json (V3 dict format: {"date": ..., "counts": {...}})
        try:
            import os
            actions_path = os.path.join(os.path.dirname(__file__), "actions_today.json")
            actions = {}
            if os.path.exists(actions_path):
                with open(actions_path, "r") as f:
                    raw = json.load(f)
                if isinstance(raw, dict):
                    actions = raw
                # If it's a list (legacy format), ignore and use fresh dict
            if "counts" not in actions:
                actions["counts"] = {}
            actions["counts"]["blog_posted"] = actions["counts"].get("blog_posted", 0) + 1
            with open(actions_path, "w") as f:
                json.dump(actions, f, indent=2, default=str)
        except Exception:
            pass

    return result


# ── Standalone execution ──
if __name__ == "__main__":
    import asyncio
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

    print("=== MAXIA CEO Blog Writer ===")
    print(f"Ollama: {OLLAMA_URL} / {OLLAMA_MODEL}")
    print(f"VPS: {VPS_URL}")
    print()

    result = asyncio.run(run_daily_blog())
    if result:
        print(f"\nPublished: {result.get('slug', '')}")
        print(f"URL: {VPS_URL}/blog?article={result.get('slug', '')}")
    else:
        print("\nNo article published (already posted today, or error)")
