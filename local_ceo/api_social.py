"""API Social — Envoi direct via API HTTP (Discord, Telegram, GitHub).

Remplace l'approche Playwright/browser qui echoue regulierement.
Chaque fonction retourne {"success": True/False, "detail": "...", ...}
et ne crash jamais (gestion d'erreurs complete).

Tokens lus depuis les variables d'environnement (.env du CEO local).
"""
import os
import time
import httpx
from dotenv import load_dotenv

load_dotenv()

# ══════════════════════════════════════════
# Tokens et config — charges une seule fois
# ══════════════════════════════════════════
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
DISCORD_CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET", "")
REDDIT_USERNAME = os.getenv("REDDIT_USERNAME", "")
REDDIT_PASSWORD = os.getenv("REDDIT_PASSWORD", "")

# Timeout par defaut pour les requetes HTTP
_TIMEOUT = 15


# ══════════════════════════════════════════
#  1. DISCORD — Bot HTTP API (pas le gateway WebSocket)
# ══════════════════════════════════════════

_DISCORD_API = "https://discord.com/api/v10"


async def discord_send_message(channel_id: str, text: str) -> dict:
    """Envoie un message dans un channel Discord via le Bot HTTP API.

    Args:
        channel_id: ID du channel Discord (ex: "1480278298369065180")
        text: Contenu du message (max 2000 chars, tronque si besoin)

    Returns:
        {"success": True, "detail": "...", "message_id": "..."}
    """
    if not DISCORD_BOT_TOKEN:
        return {"success": False, "detail": "DISCORD_BOT_TOKEN absent dans .env"}
    if not channel_id:
        return {"success": False, "detail": "channel_id manquant"}
    if not text:
        return {"success": False, "detail": "text vide"}

    # Discord limite a 2000 caracteres
    text = text[:2000]

    try:
        url = f"{_DISCORD_API}/channels/{channel_id}/messages"
        headers = {
            "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
            "Content-Type": "application/json",
        }
        payload = {"content": text}

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(url, headers=headers, json=payload)

            if resp.status_code in (200, 201):
                data = resp.json()
                return {
                    "success": True,
                    "detail": f"Message envoye dans channel {channel_id}",
                    "message_id": data.get("id", ""),
                }
            else:
                return {
                    "success": False,
                    "detail": f"Discord API erreur {resp.status_code}: {resp.text[:200]}",
                }
    except Exception as e:
        return {"success": False, "detail": f"Discord API exception: {e}"}


async def discord_list_guilds() -> list:
    """Liste tous les serveurs (guilds) ou le bot est present.

    Returns:
        Liste de dicts {"id": "...", "name": "...", "icon": "..."} ou liste vide si erreur.
    """
    if not DISCORD_BOT_TOKEN:
        return []

    try:
        url = f"{_DISCORD_API}/users/@me/guilds"
        headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}"}

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                return resp.json()
            else:
                print(f"[DISCORD API] list_guilds erreur {resp.status_code}: {resp.text[:200]}")
                return []
    except Exception as e:
        print(f"[DISCORD API] list_guilds exception: {e}")
        return []


async def discord_list_channels(guild_id: str) -> list:
    """Liste les channels texte d'un serveur Discord.

    Args:
        guild_id: ID du serveur Discord

    Returns:
        Liste de dicts {"id": "...", "name": "...", "type": 0} (filtre type==0 = text channels)
    """
    if not DISCORD_BOT_TOKEN or not guild_id:
        return []

    try:
        url = f"{_DISCORD_API}/guilds/{guild_id}/channels"
        headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}"}

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                channels = resp.json()
                # Filtrer : type 0 = text channel
                return [ch for ch in channels if ch.get("type") == 0]
            else:
                print(f"[DISCORD API] list_channels erreur {resp.status_code}: {resp.text[:200]}")
                return []
    except Exception as e:
        print(f"[DISCORD API] list_channels exception: {e}")
        return []


async def discord_find_general_channel(guild_id: str) -> str:
    """Trouve le channel #general ou le premier channel texte d'un serveur.

    Args:
        guild_id: ID du serveur Discord

    Returns:
        ID du channel trouve, ou "" si aucun.
    """
    channels = await discord_list_channels(guild_id)
    if not channels:
        return ""

    # Chercher #general en priorite
    for ch in channels:
        if ch.get("name", "").lower() in ("general", "General", "general-chat", "chat"):
            return ch["id"]

    # Sinon prendre le premier channel texte (trie par position)
    sorted_channels = sorted(channels, key=lambda c: c.get("position", 999))
    if sorted_channels:
        return sorted_channels[0]["id"]

    return ""


# ══════════════════════════════════════════
#  2. TELEGRAM — Bot API (messages dans les groupes)
# ══════════════════════════════════════════

_TELEGRAM_API = "https://api.telegram.org"


async def telegram_send_group_message(chat_id: str, text: str) -> dict:
    """Envoie un message dans un groupe Telegram via le Bot API.

    Le bot doit etre membre du groupe et avoir les permissions d'envoi.

    Args:
        chat_id: ID du chat/groupe (ex: "-1001234567890" pour un groupe)
        text: Contenu du message (HTML supporte: <b>, <i>, <a href>, <code>)

    Returns:
        {"success": True/False, "detail": "...", "message_id": ...}
    """
    if not TELEGRAM_BOT_TOKEN:
        return {"success": False, "detail": "TELEGRAM_BOT_TOKEN absent dans .env"}
    if not chat_id:
        return {"success": False, "detail": "chat_id manquant"}
    if not text:
        return {"success": False, "detail": "text vide"}

    try:
        url = f"{_TELEGRAM_API}/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(url, json=payload)

            if resp.status_code == 200:
                data = resp.json()
                if data.get("ok"):
                    msg_id = data.get("result", {}).get("message_id", "")
                    return {
                        "success": True,
                        "detail": f"Message envoye dans chat {chat_id}",
                        "message_id": msg_id,
                    }
                else:
                    return {
                        "success": False,
                        "detail": f"Telegram API erreur: {data.get('description', 'unknown')}",
                    }
            else:
                return {
                    "success": False,
                    "detail": f"Telegram API HTTP {resp.status_code}: {resp.text[:200]}",
                }
    except Exception as e:
        return {"success": False, "detail": f"Telegram API exception: {e}"}


async def telegram_get_updates(limit: int = 10) -> list:
    """Recupere les derniers messages/updates recus par le bot.

    Utile pour decouvrir les groupes ou le bot est present
    et voir les messages recents.

    Args:
        limit: Nombre max d'updates a recuperer (1-100)

    Returns:
        Liste d'updates Telegram ou liste vide si erreur.
    """
    if not TELEGRAM_BOT_TOKEN:
        return []

    # Borner entre 1 et 100 (limite API Telegram)
    limit = max(1, min(100, limit))

    try:
        url = f"{_TELEGRAM_API}/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
        params = {"limit": limit}

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(url, params=params)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("ok"):
                    return data.get("result", [])
            return []
    except Exception as e:
        print(f"[TELEGRAM API] getUpdates exception: {e}")
        return []


# ══════════════════════════════════════════
#  3. GITHUB — REST API (issues, commentaires)
# ══════════════════════════════════════════

_GITHUB_API = "https://api.github.com"


async def github_comment_issue(repo: str, issue_number: int, text: str) -> dict:
    """Commente sur une issue GitHub existante.

    Args:
        repo: Nom complet du repo (ex: "elizaOS/eliza")
        issue_number: Numero de l'issue
        text: Contenu du commentaire (Markdown supporte)

    Returns:
        {"success": True/False, "detail": "...", "comment_id": ..., "html_url": "..."}
    """
    if not GITHUB_TOKEN:
        return {"success": False, "detail": "GITHUB_TOKEN absent dans .env"}
    if not repo or not issue_number:
        return {"success": False, "detail": "repo ou issue_number manquant"}
    if not text:
        return {"success": False, "detail": "text vide"}

    try:
        url = f"{_GITHUB_API}/repos/{repo}/issues/{issue_number}/comments"
        headers = {
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
        }
        payload = {"body": text}

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(url, headers=headers, json=payload)

            if resp.status_code in (200, 201):
                data = resp.json()
                return {
                    "success": True,
                    "detail": f"Commentaire poste sur {repo}#{issue_number}",
                    "comment_id": data.get("id", ""),
                    "html_url": data.get("html_url", ""),
                }
            elif resp.status_code == 404:
                return {
                    "success": False,
                    "detail": f"Repo ou issue introuvable: {repo}#{issue_number}",
                }
            elif resp.status_code == 403:
                return {
                    "success": False,
                    "detail": f"Acces refuse (token invalide ou scope insuffisant)",
                }
            else:
                return {
                    "success": False,
                    "detail": f"GitHub API erreur {resp.status_code}: {resp.text[:200]}",
                }
    except Exception as e:
        return {"success": False, "detail": f"GitHub API exception: {e}"}


async def github_create_issue(repo: str, title: str, body: str) -> dict:
    """Cree une nouvelle issue sur un repo GitHub.

    Args:
        repo: Nom complet du repo (ex: "elizaOS/eliza")
        title: Titre de l'issue
        body: Corps de l'issue (Markdown supporte)

    Returns:
        {"success": True/False, "detail": "...", "issue_number": ..., "html_url": "..."}
    """
    if not GITHUB_TOKEN:
        return {"success": False, "detail": "GITHUB_TOKEN absent dans .env"}
    if not repo or not title:
        return {"success": False, "detail": "repo ou title manquant"}

    try:
        url = f"{_GITHUB_API}/repos/{repo}/issues"
        headers = {
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
        }
        payload = {"title": title, "body": body or ""}

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(url, headers=headers, json=payload)

            if resp.status_code in (200, 201):
                data = resp.json()
                return {
                    "success": True,
                    "detail": f"Issue creee: {repo}#{data.get('number', '?')}",
                    "issue_number": data.get("number", 0),
                    "html_url": data.get("html_url", ""),
                }
            elif resp.status_code == 404:
                return {
                    "success": False,
                    "detail": f"Repo introuvable: {repo}",
                }
            elif resp.status_code == 403:
                return {
                    "success": False,
                    "detail": f"Acces refuse (token invalide ou scope insuffisant)",
                }
            else:
                return {
                    "success": False,
                    "detail": f"GitHub API erreur {resp.status_code}: {resp.text[:200]}",
                }
    except Exception as e:
        return {"success": False, "detail": f"GitHub API exception: {e}"}


async def github_list_issues(repo: str, limit: int = 5) -> list:
    """Liste les issues ouvertes recentes d'un repo GitHub.

    Args:
        repo: Nom complet du repo (ex: "elizaOS/eliza")
        limit: Nombre max d'issues a recuperer (1-30)

    Returns:
        Liste de dicts {"number": ..., "title": "...", "html_url": "...", "created_at": "..."}
        ou liste vide si erreur.
    """
    if not GITHUB_TOKEN:
        return []
    if not repo:
        return []

    # Borner entre 1 et 30
    limit = max(1, min(30, limit))

    try:
        url = f"{_GITHUB_API}/repos/{repo}/issues"
        headers = {
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
        }
        params = {
            "state": "open",
            "sort": "created",
            "direction": "desc",
            "per_page": limit,
        }

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(url, headers=headers, params=params)

            if resp.status_code == 200:
                issues = resp.json()
                # Filtrer les pull requests (l'API issues inclut aussi les PRs)
                return [
                    {
                        "number": issue["number"],
                        "title": issue["title"],
                        "html_url": issue["html_url"],
                        "created_at": issue.get("created_at", ""),
                        "labels": [l["name"] for l in issue.get("labels", [])],
                    }
                    for issue in issues
                    if "pull_request" not in issue
                ]
            else:
                print(f"[GITHUB API] list_issues erreur {resp.status_code}: {resp.text[:200]}")
                return []
    except Exception as e:
        print(f"[GITHUB API] list_issues exception: {e}")
        return []


# ══════════════════════════════════════════
#  4. REDDIT — OAuth2 API (comments, search)
# ══════════════════════════════════════════

_REDDIT_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
_REDDIT_API = "https://oauth.reddit.com"
_reddit_token_cache: dict = {"token": "", "expires_at": 0}


async def _get_reddit_token() -> str:
    """Get Reddit OAuth2 access token (cached, auto-refresh)."""
    now = time.time()
    if _reddit_token_cache["token"] and _reddit_token_cache["expires_at"] > now + 60:
        return _reddit_token_cache["token"]

    if not REDDIT_CLIENT_ID or not REDDIT_CLIENT_SECRET:
        return ""

    try:
        auth = (REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET)
        data = {
            "grant_type": "password",
            "username": REDDIT_USERNAME,
            "password": REDDIT_PASSWORD,
        }
        headers = {"User-Agent": "MAXIA-CEO/1.0"}

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(_REDDIT_TOKEN_URL, auth=auth, data=data, headers=headers)
            if resp.status_code == 200:
                body = resp.json()
                token = body.get("access_token", "")
                expires_in = body.get("expires_in", 3600)
                _reddit_token_cache["token"] = token
                _reddit_token_cache["expires_at"] = now + expires_in
                return token
            else:
                print(f"[REDDIT API] Token error {resp.status_code}: {resp.text[:200]}")
                return ""
    except Exception as e:
        print(f"[REDDIT API] Token exception: {e}")
        return ""


async def reddit_get_posts(subreddit: str, limit: int = 5) -> list:
    """Get recent posts from a subreddit.

    Args:
        subreddit: Subreddit name (without r/)
        limit: Number of posts to fetch (1-25)

    Returns:
        List of dicts {"id": "...", "title": "...", "url": "...", "selftext": "...", "author": "..."}
        or empty list on error.
    """
    token = await _get_reddit_token()
    if not token:
        return []

    limit = max(1, min(25, limit))

    try:
        url = f"{_REDDIT_API}/r/{subreddit}/new"
        headers = {
            "Authorization": f"Bearer {token}",
            "User-Agent": "MAXIA-CEO/1.0",
        }
        params = {"limit": limit}

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(url, headers=headers, params=params)
            if resp.status_code == 200:
                data = resp.json()
                posts = []
                for child in data.get("data", {}).get("children", []):
                    p = child.get("data", {})
                    posts.append({
                        "id": p.get("id", ""),
                        "title": p.get("title", ""),
                        "url": f"https://www.reddit.com{p.get('permalink', '')}",
                        "selftext": p.get("selftext", "")[:500],
                        "author": p.get("author", ""),
                        "score": p.get("score", 0),
                        "num_comments": p.get("num_comments", 0),
                    })
                return posts
            else:
                print(f"[REDDIT API] get_posts error {resp.status_code}: {resp.text[:200]}")
                return []
    except Exception as e:
        print(f"[REDDIT API] get_posts exception: {e}")
        return []


async def reddit_post_comment(post_id: str, text: str) -> dict:
    """Comment on a Reddit post.

    Args:
        post_id: Reddit post ID (without t3_ prefix)
        text: Comment text (Markdown supported)

    Returns:
        {"success": True/False, "detail": "...", "comment_id": "..."}
    """
    token = await _get_reddit_token()
    if not token:
        return {"success": False, "detail": "Reddit token unavailable (check REDDIT_* env vars)"}
    if not post_id or not text:
        return {"success": False, "detail": "post_id ou text manquant"}

    try:
        url = f"{_REDDIT_API}/api/comment"
        headers = {
            "Authorization": f"Bearer {token}",
            "User-Agent": "MAXIA-CEO/1.0",
        }
        data = {
            "thing_id": f"t3_{post_id}",
            "text": text,
        }

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(url, headers=headers, data=data)
            if resp.status_code == 200:
                body = resp.json()
                # Reddit returns success in jquery array format
                errors = body.get("json", {}).get("errors", [])
                if not errors:
                    comment_data = body.get("json", {}).get("data", {}).get("things", [{}])
                    comment_id = comment_data[0].get("data", {}).get("id", "") if comment_data else ""
                    return {
                        "success": True,
                        "detail": f"Comment posted on t3_{post_id}",
                        "comment_id": comment_id,
                    }
                else:
                    return {
                        "success": False,
                        "detail": f"Reddit API errors: {errors}",
                    }
            elif resp.status_code == 403:
                return {"success": False, "detail": "Reddit: acces refuse (banned ou scope insuffisant)"}
            else:
                return {
                    "success": False,
                    "detail": f"Reddit API error {resp.status_code}: {resp.text[:200]}",
                }
    except Exception as e:
        return {"success": False, "detail": f"Reddit API exception: {e}"}


async def reddit_search(subreddit: str, query: str, limit: int = 5) -> list:
    """Search posts in a subreddit.

    Args:
        subreddit: Subreddit name (without r/)
        query: Search query
        limit: Number of results (1-25)

    Returns:
        List of dicts {"id": "...", "title": "...", "url": "...", "selftext": "...", "author": "..."}
        or empty list on error.
    """
    token = await _get_reddit_token()
    if not token:
        return []

    limit = max(1, min(25, limit))

    try:
        url = f"{_REDDIT_API}/r/{subreddit}/search"
        headers = {
            "Authorization": f"Bearer {token}",
            "User-Agent": "MAXIA-CEO/1.0",
        }
        params = {
            "q": query,
            "restrict_sr": "true",
            "sort": "new",
            "limit": limit,
            "t": "week",  # Last week only
        }

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(url, headers=headers, params=params)
            if resp.status_code == 200:
                data = resp.json()
                posts = []
                for child in data.get("data", {}).get("children", []):
                    p = child.get("data", {})
                    posts.append({
                        "id": p.get("id", ""),
                        "title": p.get("title", ""),
                        "url": f"https://www.reddit.com{p.get('permalink', '')}",
                        "selftext": p.get("selftext", "")[:500],
                        "author": p.get("author", ""),
                        "score": p.get("score", 0),
                        "num_comments": p.get("num_comments", 0),
                    })
                return posts
            else:
                print(f"[REDDIT API] search error {resp.status_code}: {resp.text[:200]}")
                return []
    except Exception as e:
        print(f"[REDDIT API] search exception: {e}")
        return []
