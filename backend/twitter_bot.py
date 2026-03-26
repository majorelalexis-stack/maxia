"""MAXIA Twitter Bot V12 — Auto-post, reply to mentions, comment influencers

Uses X API v2 Free tier (1500 tweets/month, 10000 reads/month).
Controlled by CEO MAXIA via GHOST-WRITER and RESPONDER.
"""
import asyncio, os, time, json

from config import GPU_TIERS
_gpu_cheapest = f"${min(t['base_price_per_hour'] for t in GPU_TIERS if not t.get('local')):.2f}/h"

# ── Config ──
TWITTER_API_KEY = os.getenv("TWITTER_API_KEY", "")
TWITTER_API_SECRET = os.getenv("TWITTER_API_SECRET", "")
TWITTER_ACCESS_TOKEN = os.getenv("TWITTER_ACCESS_TOKEN", "")
TWITTER_ACCESS_SECRET = os.getenv("TWITTER_ACCESS_SECRET", "")

MAXIA_URL = "maxiaworld.app"
MAX_TWEETS_DAY = 2        # Qualite > quantite : 2 tweets/jour max
MAX_REPLIES_DAY = 10
MAX_COMMENTS_DAY = 15      # Priorite : commenter avec qualite
MAX_LIKES_DAY = 30         # Liker les posts pertinents (AI, crypto, devs)

# ── Stats ──
_stats = {
    "tweets_today": 0,
    "replies_today": 0,
    "comments_today": 0,
    "likes_today": 0,
    "last_reset": "",
    "last_mention_id": None,
    "total_tweets": 0,
    "total_replies": 0,
    "total_likes": 0,
    "total_comments": 0,
    "errors": 0,
}


def _reset_daily():
    from datetime import date
    today = date.today().isoformat()
    if _stats["last_reset"] != today:
        _stats["tweets_today"] = 0
        _stats["replies_today"] = 0
        _stats["comments_today"] = 0
        _stats["likes_today"] = 0
        _stats["last_reset"] = today


def _get_client():
    """Get tweepy Client for X API v2."""
    if not all([TWITTER_API_KEY, TWITTER_API_SECRET, TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_SECRET]):
        return None
    try:
        import tweepy
        client = tweepy.Client(
            consumer_key=TWITTER_API_KEY,
            consumer_secret=TWITTER_API_SECRET,
            access_token=TWITTER_ACCESS_TOKEN,
            access_token_secret=TWITTER_ACCESS_SECRET,
        )
        return client
    except ImportError:
        print("[Twitter] tweepy not installed")
        return None
    except Exception as e:
        print(f"[Twitter] Client error: {e}")
        return None


# ══════════════════════════════════════════
# POST — Poster un tweet
# ══════════════════════════════════════════

async def post_tweet(text: str) -> dict:
    """Poste un tweet. Max 280 chars. Retourne le tweet_id."""
    _reset_daily()
    if _stats["tweets_today"] >= MAX_TWEETS_DAY:
        return {"success": False, "error": f"Limite {MAX_TWEETS_DAY} tweets/jour atteinte"}

    if len(text) > 280:
        text = text[:277] + "..."

    client = _get_client()
    if not client:
        return {"success": False, "error": "Twitter API non configure"}

    try:
        def _post():
            return client.create_tweet(text=text)

        response = await asyncio.to_thread(_post)
        tweet_id = response.data["id"]
        _stats["tweets_today"] += 1
        _stats["total_tweets"] += 1
        print(f"[Twitter] Tweet poste: {text[:60]}... (id:{tweet_id})")
        return {"success": True, "tweet_id": tweet_id, "text": text}
    except Exception as e:
        _stats["errors"] += 1
        print(f"[Twitter] Post error: {e}")
        return {"success": False, "error": str(e)}


# ══════════════════════════════════════════
# REPLY — Repondre a un tweet
# ══════════════════════════════════════════

async def reply_to_tweet(tweet_id: str, text: str) -> dict:
    """Repond a un tweet specifique."""
    _reset_daily()
    if _stats["replies_today"] >= MAX_REPLIES_DAY:
        return {"success": False, "error": f"Limite {MAX_REPLIES_DAY} replies/jour atteinte"}

    if len(text) > 280:
        text = text[:277] + "..."

    client = _get_client()
    if not client:
        return {"success": False, "error": "Twitter API non configure"}

    try:
        def _reply():
            return client.create_tweet(text=text, in_reply_to_tweet_id=tweet_id)

        response = await asyncio.to_thread(_reply)
        reply_id = response.data["id"]
        _stats["replies_today"] += 1
        _stats["total_replies"] += 1
        print(f"[Twitter] Reply to {tweet_id}: {text[:60]}...")
        return {"success": True, "reply_id": reply_id, "text": text}
    except Exception as e:
        _stats["errors"] += 1
        print(f"[Twitter] Reply error: {e}")
        return {"success": False, "error": str(e)}


# ══════════════════════════════════════════
# MENTIONS — Lire et repondre aux mentions
# ══════════════════════════════════════════

async def check_mentions() -> list:
    """Lit les mentions @MAXIA_WORLD et retourne les nouvelles."""
    client = _get_client()
    if not client:
        return []

    try:
        def _get_me():
            return client.get_me()

        me = await asyncio.to_thread(_get_me)
        user_id = me.data.id

        def _get_mentions():
            params = {"max_results": 10}
            if _stats["last_mention_id"]:
                params["since_id"] = _stats["last_mention_id"]
            return client.get_users_mentions(
                id=user_id,
                tweet_fields=["created_at", "author_id", "text"],
                **params,
            )

        response = await asyncio.to_thread(_get_mentions)

        if not response.data:
            return []

        mentions = []
        for tweet in response.data:
            mentions.append({
                "id": tweet.id,
                "text": tweet.text,
                "author_id": tweet.author_id,
                "created_at": str(tweet.created_at) if tweet.created_at else "",
            })

        # Update last mention id
        if mentions:
            _stats["last_mention_id"] = str(mentions[0]["id"])

        print(f"[Twitter] {len(mentions)} nouvelles mentions")
        return mentions
    except Exception as e:
        print(f"[Twitter] Mentions error: {e}")
        return []


async def auto_reply_mentions() -> list:
    """Lit les mentions et repond automatiquement avec Groq."""
    mentions = await check_mentions()
    replies = []

    for mention in mentions:
        text = mention["text"]
        tweet_id = mention["id"]

        # Generer une reponse via Groq
        reply_text = await _generate_reply(text)
        if reply_text:
            result = await reply_to_tweet(str(tweet_id), reply_text)
            replies.append({
                "mention": text[:100],
                "reply": reply_text,
                "success": result.get("success", False),
            })

    return replies


async def _generate_reply(mention_text: str) -> str:
    """Genere une reponse a une mention via Groq."""
    try:
        from config import GROQ_API_KEY, GROQ_MODEL
        if not GROQ_API_KEY:
            return f"Thanks for reaching out! Check our AI-to-AI marketplace at {MAXIA_URL}"

        from groq import Groq
        client = Groq(api_key=GROQ_API_KEY)

        def _call():
            resp = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[
                    {"role": "system", "content": (
                        f"You are MAXIA social media manager. MAXIA is an AI-to-AI marketplace on Solana ({MAXIA_URL}). "
                        "Reply to this mention in max 250 chars. Be helpful, technical, not salesy. "
                        f"If they ask about services, mention: swap 5000+ pairs (107 tokens), GPU {_gpu_cheapest}, 25 tokenized stocks, marketplace for AI agents. "
                        "Always include the URL."
                    )},
                    {"role": "user", "content": f"Reply to: {mention_text}"},
                ],
                max_tokens=80, temperature=0.7,
            )
            return resp.choices[0].message.content.strip()

        result = await asyncio.to_thread(_call)
        return result if len(result) > 10 else f"Thanks! Check {MAXIA_URL} for AI-to-AI services."
    except Exception:
        return f"Thanks for reaching out! AI-to-AI marketplace: {MAXIA_URL}"


# ══════════════════════════════════════════
# COMMENT — Commenter d'autres comptes
# ══════════════════════════════════════════

async def comment_on_tweet(tweet_id: str, text: str) -> dict:
    """Commente un tweet d'un autre compte (meme que reply)."""
    _reset_daily()
    if _stats["comments_today"] >= MAX_COMMENTS_DAY:
        return {"success": False, "error": f"Limite {MAX_COMMENTS_DAY} comments/jour atteinte"}

    result = await reply_to_tweet(tweet_id, text)
    if result.get("success"):
        _stats["comments_today"] += 1
        _stats["total_comments"] += 1
    return result


# ══════════════════════════════════════════
# LIKE — Liker des tweets pertinents
# ══════════════════════════════════════════

async def like_tweet(tweet_id: str) -> dict:
    """Like un tweet. Priorite engagement > publication."""
    _reset_daily()
    if _stats["likes_today"] >= MAX_LIKES_DAY:
        return {"success": False, "error": f"Limite {MAX_LIKES_DAY} likes/jour atteinte"}

    client = _get_client()
    if not client:
        return {"success": False, "error": "Twitter API non configure"}

    try:
        def _like():
            return client.like(tweet_id)

        await asyncio.to_thread(_like)
        _stats["likes_today"] += 1
        _stats["total_likes"] += 1
        print(f"[Twitter] Liked tweet {tweet_id}")
        return {"success": True, "tweet_id": tweet_id}
    except Exception as e:
        _stats["errors"] += 1
        print(f"[Twitter] Like error: {e}")
        return {"success": False, "error": str(e)}


# ══════════════════════════════════════════
# ENGAGE — Liker + commenter des tweets cibles (IA, crypto, devs)
# ══════════════════════════════════════════

ENGAGE_QUERIES = [
    "AI agent crypto",
    "autonomous AI blockchain",
    "solana AI bot",
    "AI marketplace",
    "LLM agent USDC",
    "AI agent monetization",
]


async def engage_with_influencers() -> list:
    """Recherche des tweets pertinents, like et commente avec qualite.
    Strategie : engagement authentique > auto-promotion."""
    results = []

    for query in ENGAGE_QUERIES:
        if _stats["likes_today"] >= MAX_LIKES_DAY and _stats["comments_today"] >= MAX_COMMENTS_DAY:
            break

        tweets = await search_tweets(query, max_results=5)
        for tweet in tweets:
            tid = str(tweet["id"])

            # Like le tweet
            if _stats["likes_today"] < MAX_LIKES_DAY:
                like_result = await like_tweet(tid)
                if like_result.get("success"):
                    results.append({"action": "like", "tweet_id": tid, "text": tweet["text"][:80]})

            # Commenter si le tweet a du potentiel (>5 likes ou >2 RT)
            if (_stats["comments_today"] < MAX_COMMENTS_DAY
                    and (tweet.get("likes", 0) > 5 or tweet.get("retweets", 0) > 2)):
                comment = await _generate_quality_comment(tweet["text"])
                if comment:
                    comment_result = await comment_on_tweet(tid, comment)
                    if comment_result.get("success"):
                        results.append({"action": "comment", "tweet_id": tid, "comment": comment[:80]})

    print(f"[Twitter] Engagement: {len(results)} actions (likes + comments)")
    return results


async def _generate_quality_comment(tweet_text: str) -> str:
    """Genere un commentaire de qualite — PAS de spam, PAS de promo directe."""
    try:
        from config import GROQ_API_KEY, GROQ_MODEL
        if not GROQ_API_KEY:
            return None

        from groq import Groq
        client = Groq(api_key=GROQ_API_KEY)

        def _call():
            resp = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[
                    {"role": "system", "content": (
                        "Tu commentes un tweet en tant qu'expert AI/crypto. "
                        "REGLES STRICTES :\n"
                        "1. JAMAIS mentionner MAXIA ou maxiaworld.app dans le commentaire\n"
                        "2. Apporter de la VALEUR : insight technique, question pertinente, experience\n"
                        "3. Etre authentique — parler comme un dev, pas un marketeur\n"
                        "4. Max 200 chars. Pas de hashtags. Max 1 emoji.\n"
                        "5. Si tu n'as rien d'interessant a dire, reponds 'SKIP'\n"
                        "L'objectif est de construire une reputation d'expert, pas de vendre."
                    )},
                    {"role": "user", "content": f"Commente ce tweet :\n{tweet_text}"},
                ],
                max_tokens=60, temperature=0.8,
            )
            return resp.choices[0].message.content.strip()

        result = await asyncio.to_thread(_call)
        if result == "SKIP" or len(result) < 10:
            return None
        return result
    except Exception:
        return None


# ══════════════════════════════════════════
# SEARCH — Trouver des tweets a commenter
# ══════════════════════════════════════════

async def search_tweets(query: str, max_results: int = 10) -> list:
    """Recherche des tweets recents (pour trouver des opportunites de commentaire)."""
    client = _get_client()
    if not client:
        return []

    try:
        def _search():
            return client.search_recent_tweets(
                query=query,
                max_results=min(max_results, 10),
                tweet_fields=["created_at", "author_id", "public_metrics"],
            )

        response = await asyncio.to_thread(_search)
        if not response.data:
            return []

        tweets = []
        for tweet in response.data:
            metrics = tweet.public_metrics or {}
            tweets.append({
                "id": tweet.id,
                "text": tweet.text,
                "author_id": tweet.author_id,
                "likes": metrics.get("like_count", 0),
                "retweets": metrics.get("retweet_count", 0),
            })
        return tweets
    except Exception as e:
        print(f"[Twitter] Search error: {e}")
        return []


# ══════════════════════════════════════════
# BOUCLE AUTONOME — Toutes les 2 heures
# ══════════════════════════════════════════

async def run_twitter_bot():
    """Boucle autonome du bot Twitter VPS.
    Twitter est DELEGUE au CEO local — le VPS garde uniquement post_tweet()
    comme fallback si le CEO local le demande via l'API."""
    print("[Twitter] Bot VPS en mode passif (Twitter delegue au CEO local)")
    if not TWITTER_API_KEY:
        print("[Twitter] Bot inactif (pas de cles API)")
        return

    # Test de connexion au demarrage
    client = _get_client()
    if client:
        print("[Twitter] Client initialise (mode passif — post uniquement via API)")
    else:
        print("[Twitter] Erreur client — verifier les cles API")
        return

    # Pas de boucle active — le CEO local gere Twitter via Playwright
    # Le VPS expose uniquement post_tweet() comme fallback API
    while True:
        try:
            _reset_daily()
        except Exception as e:
            _stats["errors"] += 1

        # Attendre 2 heures
        await asyncio.sleep(7200)


def get_stats() -> dict:
    _reset_daily()
    return {
        "configured": bool(TWITTER_API_KEY),
        "tweets_today": _stats["tweets_today"],
        "replies_today": _stats["replies_today"],
        "comments_today": _stats["comments_today"],
        "likes_today": _stats["likes_today"],
        "total_tweets": _stats["total_tweets"],
        "total_replies": _stats["total_replies"],
        "total_comments": _stats["total_comments"],
        "total_likes": _stats["total_likes"],
        "errors": _stats["errors"],
        "strategy": "engagement_first",
        "limits": {
            "tweets_per_day": MAX_TWEETS_DAY,
            "replies_per_day": MAX_REPLIES_DAY,
            "comments_per_day": MAX_COMMENTS_DAY,
            "likes_per_day": MAX_LIKES_DAY,
            "tweets_per_month": 1500,
        },
    }
