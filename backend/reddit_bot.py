"""MAXIA Reddit Bot — Automatic marketing on Reddit

Posts to r/solana, r/solanadev, r/cryptocurrency.
Monitors mentions and responds to relevant posts.

Requires: pip install asyncpraw

Reddit API keys:
  REDDIT_CLIENT_ID
  REDDIT_CLIENT_SECRET
  REDDIT_USERNAME
  REDDIT_PASSWORD

IMPORTANT: Reddit account must be 30+ days old.
Bot posts max 2x/week per subreddit to avoid bans.
"""
import asyncio, time, os, json, random
import httpx

REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET", "")
REDDIT_USERNAME = os.getenv("REDDIT_USERNAME", "")
REDDIT_PASSWORD = os.getenv("REDDIT_PASSWORD", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

MAXIA_URL = "https://maxiaworld.app"
GITHUB_URL = "https://github.com/MAXIAWORLD/demo-agent"

_running = False
_post_history_file = "/tmp/maxia_reddit_history.json"

# Subreddits to target
TARGET_SUBS = [
    {"name": "solana", "flair": None, "frequency_days": 4},
    {"name": "solanadev", "flair": None, "frequency_days": 3},
    {"name": "cryptocurrency", "flair": None, "frequency_days": 7},
    {"name": "LocalLLaMA", "flair": None, "frequency_days": 7},
]

# Keywords to monitor in posts
MONITOR_KEYWORDS = [
    "ai agent marketplace", "agent to agent", "a2a protocol",
    "ai agent solana", "sell ai services", "mcp server",
    "ai agent earn", "monetize ai agent", "agent commerce",
]

# Post templates (CEO GHOST-WRITER generates the actual content)
POST_TEMPLATES = [
    {
        "subreddits": ["solana", "solanadev"],
        "type": "showcase",
        "title": "I built an AI-to-AI marketplace on Solana where AI agents sell services to each other",
        "body_hint": "technical, show API endpoints, mention demo-agent GitHub",
    },
    {
        "subreddits": ["solanadev"],
        "type": "technical",
        "title": "Open-source: Python bot that registers on a marketplace and earns USDC",
        "body_hint": "code-focused, show demo_seller.py, mention MCP + A2A",
    },
    {
        "subreddits": ["cryptocurrency"],
        "type": "discussion",
        "title": "AI agents are starting to trade services with each other on Solana. Here's how it works.",
        "body_hint": "explain A2A concept, simple, not too technical",
    },
    {
        "subreddits": ["solana"],
        "type": "update",
        "title": "MAXIA V12: AI marketplace now supports MCP protocol + DeFi yield scanning",
        "body_hint": "changelog style, new features, what changed",
    },
    {
        "subreddits": ["LocalLLaMA"],
        "type": "crosspost",
        "title": "Built a marketplace where LLM-powered agents can sell services to each other",
        "body_hint": "focus on LLM integration, Groq free tier, multi-model routing",
    },
]


def _load_history() -> dict:
    try:
        with open(_post_history_file) as f:
            return json.load(f)
    except Exception:
        return {"posts": [], "comments": []}


def _save_history(history: dict):
    try:
        with open(_post_history_file, "w") as f:
            json.dump(history, f)
    except Exception:
        pass


def _can_post(subreddit: str, history: dict) -> bool:
    """Check if we can post to this subreddit (frequency limit)."""
    sub_config = next((s for s in TARGET_SUBS if s["name"] == subreddit), None)
    if not sub_config:
        return False
    freq = sub_config["frequency_days"] * 86400
    last_post = max(
        [p["ts"] for p in history["posts"] if p["sub"] == subreddit],
        default=0,
    )
    return time.time() - last_post > freq


async def _generate_post_body(template: dict) -> str:
    """Use Groq to generate a Reddit post body."""
    if not GROQ_API_KEY:
        return _fallback_body(template)

    prompt = (
        f"Write a Reddit post body for r/{template['subreddits'][0]}.\n"
        f"Title: {template['title']}\n"
        f"Style: {template['body_hint']}\n"
        f"MAXIA is an AI-to-AI marketplace on Solana. maxiaworld.app\n"
        f"Demo agent: github.com/MAXIAWORLD/demo-agent\n"
        f"Features: MCP server, A2A discovery, DeFi yield scan, negotiate prices, USDC payments\n"
        f"API: register free, POST /sell to list, POST /execute to buy\n\n"
        f"RULES:\n"
        f"- Write like a developer sharing a project, NOT marketing\n"
        f"- Be humble: 'looking for feedback', 'built this over weekends'\n"
        f"- Include 1-2 code snippets or API examples\n"
        f"- End with a question to drive comments\n"
        f"- Max 300 words\n"
        f"- Include {MAXIA_URL} and {GITHUB_URL}\n"
        f"- NO emojis, NO hype words, NO 'revolutionary'\n"
    )

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 600,
                    "temperature": 0.7,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[Reddit] Groq error: {e}")

    return _fallback_body(template)


def _fallback_body(template: dict) -> str:
    """Static fallback if Groq fails."""
    return (
        f"Hey everyone,\n\n"
        f"I've been building MAXIA — an open marketplace where AI agents trade services "
        f"with each other on Solana.\n\n"
        f"The idea: any AI agent can register (free), list a service, set a price in USDC, "
        f"and other agents discover and buy it via API.\n\n"
        f"What's live:\n"
        f"- A2A discovery via /.well-known/agent.json\n"
        f"- MCP server with 8 tools\n"
        f"- DeFi yield scanning (DeFiLlama)\n"
        f"- Price negotiation between agents\n"
        f"- USDC payments verified on-chain\n\n"
        f"Demo agent (fork and sell your own services):\n"
        f"{GITHUB_URL}\n\n"
        f"API docs: {MAXIA_URL}/docs-html\n\n"
        f"Would love feedback from devs here. What services would your agent want to buy or sell?\n"
    )


async def _generate_comment(post_title: str, post_body: str) -> str:
    """Generate a relevant comment for a post."""
    if not GROQ_API_KEY:
        return ""

    prompt = (
        f"Someone posted on Reddit about AI agents:\n"
        f"Title: {post_title}\n"
        f"Body: {post_body[:500]}\n\n"
        f"Write a SHORT helpful reply (max 100 words) that:\n"
        f"- Adds value to the discussion\n"
        f"- Mentions MAXIA only if directly relevant\n"
        f"- Is NOT salesy\n"
        f"- If not relevant, return SKIP\n"
        f"MAXIA = AI-to-AI marketplace, maxiaworld.app\n"
    )

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 200,
                    "temperature": 0.7,
                },
            )
            if resp.status_code == 200:
                text = resp.json()["choices"][0]["message"]["content"].strip()
                if "SKIP" in text:
                    return ""
                return text
    except Exception:
        pass
    return ""


async def post_to_reddit(subreddit: str, title: str, body: str) -> dict:
    """Post to a subreddit using Reddit API (no asyncpraw needed)."""
    if not all([REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USERNAME, REDDIT_PASSWORD]):
        return {"error": "Reddit API keys not configured"}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # Get access token
            auth_resp = await client.post(
                "https://www.reddit.com/api/v1/access_token",
                auth=(REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET),
                data={
                    "grant_type": "password",
                    "username": REDDIT_USERNAME,
                    "password": REDDIT_PASSWORD,
                },
                headers={"User-Agent": f"MAXIA_Bot/1.0 by {REDDIT_USERNAME}"},
            )
            token = auth_resp.json().get("access_token")
            if not token:
                return {"error": "Auth failed", "detail": auth_resp.json()}

            # Submit post
            post_resp = await client.post(
                "https://oauth.reddit.com/api/submit",
                headers={
                    "Authorization": f"Bearer {token}",
                    "User-Agent": f"MAXIA_Bot/1.0 by {REDDIT_USERNAME}",
                },
                data={
                    "kind": "self",
                    "sr": subreddit,
                    "title": title,
                    "text": body,
                },
            )
            result = post_resp.json()
            success = result.get("success", False)
            url = ""
            if result.get("json", {}).get("data", {}).get("url"):
                url = result["json"]["data"]["url"]
            return {"success": success, "url": url, "subreddit": subreddit}
    except Exception as e:
        return {"error": str(e)}


async def run_reddit_bot():
    """Main loop — posts and monitors Reddit."""
    global _running
    _running = True

    if not REDDIT_CLIENT_ID:
        print("[Reddit] Keys not configured — bot disabled")
        return

    print("[Reddit] Bot started — monitoring + auto-posting")

    while _running:
        try:
            history = _load_history()

            # Check each subreddit
            for template in POST_TEMPLATES:
                for sub in template["subreddits"]:
                    if _can_post(sub, history):
                        body = await _generate_post_body(template)
                        result = await post_to_reddit(sub, template["title"], body)
                        if result.get("success"):
                            history["posts"].append({
                                "sub": sub,
                                "title": template["title"],
                                "ts": time.time(),
                                "url": result.get("url", ""),
                            })
                            _save_history(history)
                            print(f"[Reddit] Posted to r/{sub}: {template['title']}")
                        else:
                            print(f"[Reddit] Failed r/{sub}: {result.get('error', '')}")
                        await asyncio.sleep(60)  # Wait between posts
                        break  # One post per cycle

        except Exception as e:
            print(f"[Reddit] Loop error: {e}")

        # Check every 6 hours
        await asyncio.sleep(21600)


def stop():
    global _running
    _running = False


def get_stats() -> dict:
    history = _load_history()
    return {
        "configured": bool(REDDIT_CLIENT_ID),
        "total_posts": len(history.get("posts", [])),
        "total_comments": len(history.get("comments", [])),
        "last_post": history["posts"][-1] if history.get("posts") else None,
        "target_subs": [s["name"] for s in TARGET_SUBS],
    }
