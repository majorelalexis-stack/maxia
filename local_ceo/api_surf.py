"""API Surf — collecte de donnees R&D via API directes (pas de browser-use).

10x plus rapide et 100% fiable par rapport au scraping par vision.
Utilise les API REST publiques + tokens GitHub/Reddit quand disponibles.

Sources :
  - GitHub API     : trending, repos, issues, releases
  - Reddit API     : posts recents par subreddit (OAuth2)
  - DeFi Llama API : yields, TVL, protocols
  - CoinGecko API  : nouveaux tokens, prix, trending
  - DexScreener API: trending tokens, volumes
  - HN API         : top stories, show HN
"""
import os
import time
import json
import asyncio
import httpx

# Tokens optionnels (accelerent et evitent les rate limits)
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET", "")
REDDIT_USERNAME = os.getenv("REDDIT_USERNAME", "")
REDDIT_PASSWORD = os.getenv("REDDIT_PASSWORD", "")

_reddit_token = ""
_reddit_token_ts = 0


# ══════════════════════════════════════════
# GitHub API
# ══════════════════════════════════════════

async def github_trending(language: str = "", since: str = "daily") -> list:
    """Scrape GitHub trending via l'API search (pas de vrai endpoint trending).
    Retourne les repos les plus starred recemment."""
    headers = {"Accept": "application/vnd.github.v3+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"

    # Repos crees recemment avec beaucoup de stars
    import datetime
    week_ago = (datetime.datetime.utcnow() - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
    q = f"created:>{week_ago} stars:>10"
    if language:
        q += f" language:{language}"

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://api.github.com/search/repositories",
                params={"q": q, "sort": "stars", "order": "desc", "per_page": 10},
                headers=headers,
            )
            resp.raise_for_status()
            items = resp.json().get("items", [])
            return [
                {
                    "name": r["full_name"],
                    "description": (r.get("description") or "")[:200],
                    "stars": r["stargazers_count"],
                    "language": r.get("language", ""),
                    "url": r["html_url"],
                    "created": r["created_at"][:10],
                }
                for r in items[:10]
            ]
    except Exception as e:
        print(f"[API/GitHub] trending error: {e}")
        return []


async def github_repo_releases(repo: str, limit: int = 3) -> list:
    """Derniers releases d'un repo (ex: 'elizaOS/eliza')."""
    headers = {"Accept": "application/vnd.github.v3+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"https://api.github.com/repos/{repo}/releases",
                params={"per_page": limit},
                headers=headers,
            )
            resp.raise_for_status()
            return [
                {
                    "tag": r["tag_name"],
                    "name": r.get("name", ""),
                    "date": r["published_at"][:10] if r.get("published_at") else "",
                    "body": (r.get("body") or "")[:500],
                    "url": r["html_url"],
                }
                for r in resp.json()[:limit]
            ]
    except Exception as e:
        print(f"[API/GitHub] releases {repo} error: {e}")
        return []


async def github_repo_issues(repo: str, limit: int = 5) -> list:
    """Issues ouvertes recentes d'un repo."""
    headers = {"Accept": "application/vnd.github.v3+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"https://api.github.com/repos/{repo}/issues",
                params={"state": "open", "sort": "created", "direction": "desc", "per_page": limit},
                headers=headers,
            )
            resp.raise_for_status()
            return [
                {
                    "title": i["title"],
                    "url": i["html_url"],
                    "user": i["user"]["login"],
                    "created": i["created_at"][:10],
                    "comments": i["comments"],
                    "labels": [l["name"] for l in i.get("labels", [])],
                }
                for i in resp.json()[:limit]
                if not i.get("pull_request")  # Exclure les PRs
            ]
    except Exception as e:
        print(f"[API/GitHub] issues {repo} error: {e}")
        return []


async def github_search_repos(query: str, limit: int = 5) -> list:
    """Recherche de repos GitHub."""
    headers = {"Accept": "application/vnd.github.v3+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://api.github.com/search/repositories",
                params={"q": query, "sort": "updated", "order": "desc", "per_page": limit},
                headers=headers,
            )
            resp.raise_for_status()
            items = resp.json().get("items", [])
            return [
                {
                    "name": r["full_name"],
                    "description": (r.get("description") or "")[:200],
                    "stars": r["stargazers_count"],
                    "url": r["html_url"],
                    "updated": r["updated_at"][:10],
                }
                for r in items[:limit]
            ]
    except Exception as e:
        print(f"[API/GitHub] search error: {e}")
        return []


# ══════════════════════════════════════════
# Reddit API (OAuth2)
# ══════════════════════════════════════════

async def _get_reddit_token() -> str:
    """Obtient un token Reddit via OAuth2 password grant."""
    global _reddit_token, _reddit_token_ts
    # Token valide 1h, refresh a 50min
    if _reddit_token and time.time() - _reddit_token_ts < 3000:
        return _reddit_token
    if not REDDIT_CLIENT_ID or not REDDIT_CLIENT_SECRET:
        return ""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://www.reddit.com/api/v1/access_token",
                auth=(REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET),
                data={
                    "grant_type": "password",
                    "username": REDDIT_USERNAME,
                    "password": REDDIT_PASSWORD,
                },
                headers={"User-Agent": "MAXIA-CEO/1.0"},
            )
            resp.raise_for_status()
            _reddit_token = resp.json().get("access_token", "")
            _reddit_token_ts = time.time()
            return _reddit_token
    except Exception as e:
        print(f"[API/Reddit] token error: {e}")
        return ""


async def reddit_subreddit_new(subreddit: str, limit: int = 10) -> list:
    """Posts recents d'un subreddit via API Reddit."""
    token = await _get_reddit_token()
    headers = {"User-Agent": "MAXIA-CEO/1.0"}
    # Avec ou sans auth
    if token:
        headers["Authorization"] = f"Bearer {token}"
        base_url = f"https://oauth.reddit.com/r/{subreddit}/new"
    else:
        base_url = f"https://www.reddit.com/r/{subreddit}/new.json"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                base_url,
                params={"limit": limit},
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json().get("data", {}).get("children", [])
            return [
                {
                    "title": p["data"]["title"],
                    "url": f"https://reddit.com{p['data']['permalink']}",
                    "author": p["data"]["author"],
                    "score": p["data"]["score"],
                    "comments": p["data"]["num_comments"],
                    "created": time.strftime("%Y-%m-%d", time.gmtime(p["data"]["created_utc"])),
                    "selftext": (p["data"].get("selftext") or "")[:300],
                }
                for p in data[:limit]
            ]
    except Exception as e:
        print(f"[API/Reddit] r/{subreddit} error: {e}")
        return []


async def reddit_search(subreddit: str, query: str, limit: int = 5) -> list:
    """Recherche dans un subreddit via API Reddit."""
    token = await _get_reddit_token()
    headers = {"User-Agent": "MAXIA-CEO/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
        base_url = f"https://oauth.reddit.com/r/{subreddit}/search"
    else:
        base_url = f"https://www.reddit.com/r/{subreddit}/search.json"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                base_url,
                params={"q": query, "restrict_sr": "true", "sort": "new", "limit": limit},
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json().get("data", {}).get("children", [])
            return [
                {
                    "title": p["data"]["title"],
                    "url": f"https://reddit.com{p['data']['permalink']}",
                    "author": p["data"]["author"],
                    "score": p["data"]["score"],
                }
                for p in data[:limit]
            ]
    except Exception as e:
        print(f"[API/Reddit] search error: {e}")
        return []


# ══════════════════════════════════════════
# DeFi Llama API (publique, pas d'auth)
# ══════════════════════════════════════════

async def defillama_yields(chain: str = "Solana", limit: int = 10) -> list:
    """Top yields sur une chain via DeFi Llama."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get("https://yields.llama.fi/pools")
            resp.raise_for_status()
            pools = resp.json().get("data", [])
            # Filtrer par chain et trier par APY
            chain_lower = chain.lower()
            filtered = [p for p in pools if p.get("chain", "").lower() == chain_lower and p.get("apy", 0) > 0]
            filtered.sort(key=lambda x: x.get("apy", 0), reverse=True)
            return [
                {
                    "protocol": p.get("project", ""),
                    "symbol": p.get("symbol", ""),
                    "apy": round(p.get("apy", 0), 2),
                    "tvl": round(p.get("tvlUsd", 0), 0),
                    "chain": p.get("chain", ""),
                }
                for p in filtered[:limit]
            ]
    except Exception as e:
        print(f"[API/DeFi] yields {chain} error: {e}")
        return []


async def defillama_tvl_top(limit: int = 10) -> list:
    """Top protocols par TVL."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get("https://api.llama.fi/protocols")
            resp.raise_for_status()
            protocols = resp.json()
            protocols.sort(key=lambda x: x.get("tvl", 0), reverse=True)
            return [
                {
                    "name": p.get("name", ""),
                    "tvl": round(p.get("tvl", 0), 0),
                    "chain": p.get("chain", ""),
                    "category": p.get("category", ""),
                    "change_1d": round(p.get("change_1d", 0) or 0, 2),
                }
                for p in protocols[:limit]
            ]
    except Exception as e:
        print(f"[API/DeFi] TVL error: {e}")
        return []


# ══════════════════════════════════════════
# CoinGecko API (publique, rate limit 10-30 req/min)
# ══════════════════════════════════════════

async def coingecko_trending() -> list:
    """Tokens trending sur CoinGecko."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get("https://api.coingecko.com/api/v3/search/trending")
            resp.raise_for_status()
            coins = resp.json().get("coins", [])
            return [
                {
                    "name": c["item"]["name"],
                    "symbol": c["item"]["symbol"],
                    "market_cap_rank": c["item"].get("market_cap_rank"),
                    "price_btc": c["item"].get("price_btc", 0),
                }
                for c in coins[:10]
            ]
    except Exception as e:
        print(f"[API/CoinGecko] trending error: {e}")
        return []


async def coingecko_new_coins(limit: int = 10) -> list:
    """Nouveaux tokens sur CoinGecko (tries par date)."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://api.coingecko.com/api/v3/coins/list",
                params={"include_platform": "true"},
            )
            resp.raise_for_status()
            # La liste est enorme, on prend les derniers IDs (approximation)
            coins = resp.json()
            # CoinGecko n'a pas de tri par date, on retourne les derniers
            recent = coins[-limit:]
            return [
                {
                    "id": c["id"],
                    "symbol": c["symbol"],
                    "name": c["name"],
                    "platforms": list(c.get("platforms", {}).keys())[:3],
                }
                for c in recent
            ]
    except Exception as e:
        print(f"[API/CoinGecko] new coins error: {e}")
        return []


# ══════════════════════════════════════════
# DexScreener API (publique, pas d'auth)
# ══════════════════════════════════════════

async def dexscreener_trending(limit: int = 10) -> list:
    """Tokens trending sur DexScreener (boosted tokens)."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get("https://api.dexscreener.com/token-boosts/latest/v1")
            resp.raise_for_status()
            tokens = resp.json()
            if isinstance(tokens, list):
                return [
                    {
                        "name": t.get("tokenAddress", "")[:10],
                        "chain": t.get("chainId", ""),
                        "description": t.get("description", "")[:100],
                        "url": t.get("url", ""),
                    }
                    for t in tokens[:limit]
                ]
            return []
    except Exception as e:
        print(f"[API/DexScreener] trending error: {e}")
        return []


async def dexscreener_search(query: str, limit: int = 5) -> list:
    """Recherche de tokens sur DexScreener."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"https://api.dexscreener.com/latest/dex/search?q={query}")
            resp.raise_for_status()
            pairs = resp.json().get("pairs", [])
            return [
                {
                    "name": p.get("baseToken", {}).get("name", ""),
                    "symbol": p.get("baseToken", {}).get("symbol", ""),
                    "chain": p.get("chainId", ""),
                    "price_usd": p.get("priceUsd", "0"),
                    "volume_24h": round(float(p.get("volume", {}).get("h24", 0) or 0), 0),
                    "price_change_24h": p.get("priceChange", {}).get("h24", 0),
                    "url": p.get("url", ""),
                }
                for p in pairs[:limit]
            ]
    except Exception as e:
        print(f"[API/DexScreener] search error: {e}")
        return []


# ══════════════════════════════════════════
# Hacker News API (publique, pas d'auth)
# ══════════════════════════════════════════

async def hn_top_stories(limit: int = 10) -> list:
    """Top stories HN."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get("https://hacker-news.firebaseio.com/v0/topstories.json")
            resp.raise_for_status()
            ids = resp.json()[:limit]
            # Fetch chaque story en parallele
            tasks = []
            for story_id in ids:
                tasks.append(client.get(f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json"))
            responses = await asyncio.gather(*tasks, return_exceptions=True)
            stories = []
            for r in responses:
                if isinstance(r, Exception):
                    continue
                s = r.json()
                stories.append({
                    "title": s.get("title", ""),
                    "url": s.get("url", ""),
                    "score": s.get("score", 0),
                    "comments": s.get("descendants", 0),
                    "by": s.get("by", ""),
                })
            return stories
    except Exception as e:
        print(f"[API/HN] top stories error: {e}")
        return []


async def hn_show_stories(limit: int = 10) -> list:
    """Show HN stories."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get("https://hacker-news.firebaseio.com/v0/showstories.json")
            resp.raise_for_status()
            ids = resp.json()[:limit]
            tasks = []
            for story_id in ids:
                tasks.append(client.get(f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json"))
            responses = await asyncio.gather(*tasks, return_exceptions=True)
            stories = []
            for r in responses:
                if isinstance(r, Exception):
                    continue
                s = r.json()
                stories.append({
                    "title": s.get("title", ""),
                    "url": s.get("url", ""),
                    "score": s.get("score", 0),
                    "by": s.get("by", ""),
                })
            return stories
    except Exception as e:
        print(f"[API/HN] show stories error: {e}")
        return []


# ══════════════════════════════════════════
# Collecteur unifie — le CEO appelle ca
# ══════════════════════════════════════════

_collect_call_count = 0

async def collect_all_api_data() -> dict:
    """Collecte des donnees R&D en parallele via API. Rotation des sources pour varier.
    ~2-5 secondes au lieu de ~2 min."""
    global _collect_call_count
    _collect_call_count += 1
    n = _collect_call_count

    # Sources qui tournent a chaque appel pour varier les resultats
    import random
    github_langs = ["python", "typescript", "rust", "go", "javascript"]
    lang1 = github_langs[n % len(github_langs)]
    lang2 = github_langs[(n + 1) % len(github_langs)]

    repos_to_check = [
        ("elizaOS/eliza", "elizaos"), ("langchain-ai/langchain", "langchain"),
        ("goat-sdk/goat", "goat"), ("crewAIInc/crewAI", "crewai"),
        ("microsoft/autogen", "autogen"), ("ollama/ollama", "ollama"),
        ("Virtual-Protocol/virtuals-python", "virtuals"), ("fetchai/uAgents", "fetchai"),
    ]
    # Prendre 2 repos differents a chaque fois
    repo1 = repos_to_check[n % len(repos_to_check)]
    repo2 = repos_to_check[(n + 3) % len(repos_to_check)]

    subreddits = ["LocalLLaMA", "solanadev", "defi", "artificial",
                  "MachineLearning", "ethereum", "ollama", "LangChain"]
    sub1 = subreddits[n % len(subreddits)]
    sub2 = subreddits[(n + 2) % len(subreddits)]
    sub3 = subreddits[(n + 4) % len(subreddits)]

    chains = ["Solana", "Base", "Ethereum", "Polygon", "Arbitrum"]
    chain1 = chains[n % len(chains)]
    chain2 = chains[(n + 1) % len(chains)]

    # Recherche GitHub variee
    search_queries = [
        "AI agent marketplace", "crypto swap SDK", "solana DeFi tool",
        "LLM inference API", "autonomous agent", "MCP server",
        "agent-to-agent protocol", "on-chain escrow", "GPU rental API",
    ]
    search_q = search_queries[n % len(search_queries)]

    results = await asyncio.gather(
        github_trending(lang1),
        github_trending(lang2),
        github_repo_issues(repo1[0], 3),
        github_repo_issues(repo2[0], 3),
        github_repo_releases(repo1[0], 2),
        github_search_repos(search_q, 5),
        reddit_subreddit_new(sub1, 5),
        reddit_subreddit_new(sub2, 5),
        reddit_subreddit_new(sub3, 5),
        defillama_yields(chain1, 5),
        defillama_yields(chain2, 5),
        coingecko_trending(),
        dexscreener_trending(5),
        hn_top_stories(10),
        hn_show_stories(5),
        return_exceptions=True,
    )

    names = [
        f"github_trending_{lang1}", f"github_trending_{lang2}",
        f"{repo1[1]}_issues", f"{repo2[1]}_issues",
        f"{repo1[1]}_releases", f"github_search_{search_q[:15]}",
        f"reddit_{sub1}", f"reddit_{sub2}", f"reddit_{sub3}",
        f"defi_yields_{chain1}", f"defi_yields_{chain2}",
        "coingecko_trending", "dexscreener_trending",
        "hn_top", "hn_show",
    ]

    data = {}
    for name, result in zip(names, results):
        if isinstance(result, Exception):
            data[name] = {"error": str(result)}
        else:
            data[name] = result

    return data


def format_api_data_for_ceo(data: dict) -> str:
    """Formate les donnees API en texte lisible pour le CEO 14B."""
    parts = []

    # GitHub trending
    for key in ["github_trending_python", "github_trending_typescript"]:
        items = data.get(key, [])
        if items and not isinstance(items, dict):
            lang = "Python" if "python" in key else "TypeScript"
            parts.append(f"\n## GitHub Trending {lang}")
            for r in items[:5]:
                parts.append(f"- {r['name']} ({r['stars']}★): {r['description'][:80]}")

    # ElizaOS / LangChain issues
    for key, label in [("elizaos_issues", "ElizaOS"), ("langchain_issues", "LangChain")]:
        items = data.get(key, [])
        if items and not isinstance(items, dict):
            parts.append(f"\n## {label} Issues")
            for i in items[:3]:
                parts.append(f"- [{i['created']}] {i['title'][:80]} ({i['comments']} comments)")

    # Releases
    for key, label in [("elizaos_releases", "ElizaOS"), ("goat_releases", "GOAT SDK")]:
        items = data.get(key, [])
        if items and not isinstance(items, dict):
            r = items[0]
            parts.append(f"\n## {label} Latest Release: {r['tag']} ({r['date']})")
            parts.append(f"  {r['body'][:150]}")

    # Reddit
    for key in ["reddit_locallama", "reddit_solanadev", "reddit_defi", "reddit_artificial"]:
        items = data.get(key, [])
        if items and not isinstance(items, dict):
            sub = key.replace("reddit_", "r/")
            parts.append(f"\n## {sub}")
            for p in items[:3]:
                parts.append(f"- [{p['score']}↑] {p['title'][:80]}")

    # DeFi yields
    for key in ["defi_yields_solana", "defi_yields_base"]:
        items = data.get(key, [])
        if items and not isinstance(items, dict):
            chain = "Solana" if "solana" in key else "Base"
            parts.append(f"\n## DeFi Yields {chain}")
            for y in items[:3]:
                parts.append(f"- {y['protocol']}/{y['symbol']}: {y['apy']}% APY (TVL ${y['tvl']:,.0f})")

    # CoinGecko trending
    items = data.get("coingecko_trending", [])
    if items and not isinstance(items, dict):
        parts.append("\n## CoinGecko Trending")
        for c in items[:5]:
            parts.append(f"- {c['name']} ({c['symbol']}) — rank #{c.get('market_cap_rank', '?')}")

    # DexScreener
    items = data.get("dexscreener_trending", [])
    if items and not isinstance(items, dict):
        parts.append("\n## DexScreener Trending")
        for t in items[:3]:
            parts.append(f"- {t['chain']}: {t['description'][:60]}")

    # HN
    items = data.get("hn_top", [])
    if items and not isinstance(items, dict):
        parts.append("\n## Hacker News Top")
        for s in items[:5]:
            parts.append(f"- [{s['score']}] {s['title'][:80]}")

    items = data.get("hn_show", [])
    if items and not isinstance(items, dict):
        parts.append("\n## Show HN")
        for s in items[:3]:
            parts.append(f"- [{s['score']}] {s['title'][:80]}")

    return "\n".join(parts) if parts else "No API data collected."
