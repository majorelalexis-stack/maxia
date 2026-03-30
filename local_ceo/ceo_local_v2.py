"""CEO Local V2 — 8 missions, 1 modele unique, zero spam.

Missions:
  1. Tweet feature du jour (15h-16h, peak US EST)
  2. 5 opportunites Twitter + mention alert → mail (8h)
  3. Rapport GitHub + skills + annuaires + LLM analysis → mail (9h)
  4. Moderation forum (toutes les heures)
  5. Analyse nouveaux agents (inclus dans rapport)
  6. Veille concurrentielle (hebdo dimanche 10h)
  7. Surveillance sante site (toutes les 5 min, extended checks)
  8. Tweet engagement tracking (feedback loop)

Usage: python ceo_local_v2.py
"""
import asyncio
import json
import time
import os
import random
import logging
import subprocess
import httpx
from datetime import datetime

from config_local import (
    VPS_URL, ADMIN_KEY, OLLAMA_URL, OLLAMA_MODEL,
    ALEXIS_EMAIL, BROWSER_PROFILE_DIR,
    HEALTH_CHECK_INTERVAL_S, MODERATION_INTERVAL_S,
    GITHUB_REPOS, MAXIA_FEATURES,
)
from kaspa_miner import start_miner, stop_miner, is_mining, get_stats

logging.basicConfig(level=logging.INFO, format="%(asctime)s [CEO] %(message)s")
log = logging.getLogger("ceo")

# Mining Kaspa — active par defaut, desactivable via env
KASPA_MINING_ENABLED = os.getenv("KASPA_MINING_ENABLED", "1") == "1"

# ══════════════════════════════════════════
# Knowledge Base — CEO connait MAXIA
# ══════════════════════════════════════════

_KNOWLEDGE_FILE = os.path.join(os.path.dirname(__file__), "maxia_knowledge.md")
MAXIA_KNOWLEDGE = ""
try:
    with open(_KNOWLEDGE_FILE, "r", encoding="utf-8") as f:
        MAXIA_KNOWLEDGE = f.read()
    log.info("Knowledge base loaded (%d chars)", len(MAXIA_KNOWLEDGE))
except Exception:
    MAXIA_KNOWLEDGE = "MAXIA is an AI-to-AI marketplace on 14 blockchains. Website: maxiaworld.app"
    log.warning("Knowledge base not found — using minimal context")

CEO_SYSTEM_PROMPT = (
    "You are the CEO of MAXIA, an AI-to-AI marketplace. "
    "You know MAXIA deeply. Here is your knowledge base:\n\n"
    + MAXIA_KNOWLEDGE[:3000] +
    "\n\nRules: Professional tone. No hype words. No competitor bashing. "
    "80% value, 20% MAXIA mention. Always include maxiaworld.app link when relevant."
)

# ══════════════════════════════════════════
# Memoire locale
# ══════════════════════════════════════════

_DIR = os.path.dirname(__file__)
_MEMORY_FILE = os.path.join(_DIR, "ceo_memory.json")
_ACTIONS_FILE = os.path.join(_DIR, "actions_today.json")


def _load_memory() -> dict:
    default = {
        "tweets_posted": [], "opportunities_sent": [], "repos_scanned": [],
        "agents_seen": [], "sites_found": [], "moderation_log": [],
        "health_alerts": [], "feature_index": 0, "regles": [],
        "tweet_engagement": [], "competitive_reports": [],
    }
    try:
        if os.path.exists(_MEMORY_FILE):
            with open(_MEMORY_FILE, "r", encoding="utf-8") as f:
                data = json.loads(f.read())
            for k, v in default.items():
                if k not in data:
                    data[k] = v
            return data
    except Exception as e:
        log.error("Memory load error: %s", e)
    return default


def _save_memory(mem: dict):
    # Trim lists
    for key in ["tweets_posted", "opportunities_sent", "moderation_log", "health_alerts", "tweet_engagement", "competitive_reports"]:
        if len(mem.get(key, [])) > 200:
            mem[key] = mem[key][-200:]
    if len(mem.get("agents_seen", [])) > 500:
        mem["agents_seen"] = mem["agents_seen"][-500:]
    try:
        with open(_MEMORY_FILE, "w", encoding="utf-8") as f:
            f.write(json.dumps(mem, indent=2, default=str, ensure_ascii=False))
    except Exception as e:
        log.error("Memory save error: %s", e)


def _load_actions_today() -> dict:
    default = {"date": "", "counts": {"tweet_feature": 0, "opportunities_sent": 0, "report_sent": 0, "moderation_done": 0, "health_checks": 0}}
    try:
        if os.path.exists(_ACTIONS_FILE):
            with open(_ACTIONS_FILE, "r", encoding="utf-8") as f:
                data = json.loads(f.read())
            today = datetime.now().strftime("%Y-%m-%d")
            if data.get("date") != today:
                data = default
                data["date"] = today
            return data
    except Exception:
        pass
    default["date"] = datetime.now().strftime("%Y-%m-%d")
    return default


def _save_actions(actions: dict):
    try:
        with open(_ACTIONS_FILE, "w", encoding="utf-8") as f:
            f.write(json.dumps(actions, indent=2))
    except Exception:
        pass


# ══════════════════════════════════════════
# LLM — appel Ollama (modele unique)
# ══════════════════════════════════════════

_last_llm_call = 0.0  # timestamp du dernier appel LLM


async def llm(prompt: str, system: str = "", max_tokens: int = 1000, retries: int = 2) -> str:
    """Appel Ollama local avec retry. think=False pour Qwen3 (sinon response vide).
    Auto-switch: arrete le miner Kaspa avant l'appel. La relance est geree par la boucle principale."""
    global _last_llm_call

    # Stop miner avant d'utiliser le GPU (seulement si il mine vraiment)
    if is_mining():
        log.info("[MINING] Pause miner pour appel LLM...")
        stop_miner()
        await asyncio.sleep(3)  # Laisser le GPU se liberer
        # Restart Ollama pour qu'il recupere la VRAM liberee
        log.info("[OLLAMA] Restart Ollama apres arret miner...")
        try:
            subprocess.run(["taskkill", "/F", "/IM", "ollama.exe"],
                           capture_output=True, timeout=5)
            await asyncio.sleep(2)
            subprocess.Popen(["ollama", "serve"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            await asyncio.sleep(5)  # Attendre qu'Ollama soit pret
            # Warmup — forcer le chargement du modele en VRAM
            async with httpx.AsyncClient(timeout=30) as warmup:
                await warmup.post(f"{OLLAMA_URL}/api/generate", json={
                    "model": OLLAMA_MODEL, "prompt": "hi", "stream": False,
                    "think": False, "options": {"num_predict": 1}})
            log.info("[OLLAMA] Warmup OK — modele charge en VRAM")
        except Exception as e:
            log.warning("[OLLAMA] Restart/warmup error: %s", e)

    _last_llm_call = time.time()
    full = f"{system}\n\n{prompt}" if system else prompt
    for attempt in range(retries):
        try:
            async with httpx.AsyncClient(timeout=180) as client:
                resp = await client.post(f"{OLLAMA_URL}/api/generate", json={
                    "model": OLLAMA_MODEL,
                    "prompt": full,
                    "stream": False,
                    "think": False,
                    "options": {"num_predict": max_tokens, "temperature": 0.7},
                })
                resp.raise_for_status()
                result = resp.json().get("response", "").strip()
                if result:
                    return result
                log.warning("LLM returned empty response (attempt %d/%d)", attempt + 1, retries)
        except Exception as e:
            log.error("LLM error (attempt %d/%d): %s", attempt + 1, retries, e)
        if attempt < retries - 1:
            await asyncio.sleep(5)
    return ""


# ══════════════════════════════════════════
# Email — envoyer via VPS API
# ══════════════════════════════════════════

async def send_mail(subject: str, body: str):
    """Envoie un mail a Alexis via l'API VPS."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(f"{VPS_URL}/api/inbox/send", json={
                "to": ALEXIS_EMAIL,
                "subject": subject,
                "body": body,
            }, headers={"X-Admin-Key": ADMIN_KEY})
            if resp.status_code == 200:
                log.info("Mail envoye: %s", subject)
            else:
                log.error("Mail error %d: %s", resp.status_code, resp.text[:100])
    except Exception as e:
        log.error("Mail send error: %s", e)


# ══════════════════════════════════════════
# Mission 1 — Tweet feature du jour
# ══════════════════════════════════════════

async def mission_tweet_feature(mem: dict, actions: dict):
    """Poste 1 tweet presentant une feature MAXIA."""
    if actions["counts"]["tweet_feature"] >= 1:
        log.info("Tweet deja poste aujourd'hui — skip")
        return

    # Choisir la feature suivante (rotation)
    idx = mem.get("feature_index", 0) % len(MAXIA_FEATURES)
    feature = MAXIA_FEATURES[idx]
    mem["feature_index"] = idx + 1

    # Generer le tweet via LLM
    tweet_text = await llm(
        f"Write a short tweet (max 250 chars) presenting this feature of MAXIA:\n"
        f"Feature: {feature['name']}\n"
        f"Description: {feature['desc']}\n"
        f"Link: https://{feature['link']}\n\n"
        f"Rules:\n- Professional tone, not salesy\n- Include the link\n- End with: #MAXIA #AI #Web3 #Solana\n- Max 250 characters total",
        system=CEO_SYSTEM_PROMPT,
        max_tokens=100,
    )

    if not tweet_text or len(tweet_text) < 20:
        tweet_text = f"{feature['name']} — {feature['desc']}\n\nhttps://{feature['link']}\n\n#MAXIA #AI #Web3 #Solana"

    # Poster via browser
    try:
        from browser_agent import browser
        await browser.post_tweet(tweet_text)
        log.info("Tweet poste: %s", tweet_text[:80])
        mem["tweets_posted"].append({"date": datetime.now().isoformat(), "feature": feature["name"], "text": tweet_text[:200]})
        actions["counts"]["tweet_feature"] = 1

        # Track tweet engagement for feedback loop (Mission 8)
        mem.setdefault("tweet_engagement", []).append({
            "date": datetime.now().isoformat(),
            "feature_name": feature["name"],
            "feature_desc": feature["desc"][:100],
            "tweet_preview": tweet_text[:140],
            "status": "posted",
        })
    except Exception as e:
        log.error("Tweet error: %s", e)


# ══════════════════════════════════════════
# Mission 2 — 5 opportunites Twitter → mail
# ══════════════════════════════════════════

async def mission_twitter_scan_hourly(mem: dict):
    """Scan horaire — accumule les opportunites dans la memoire."""
    keywords = ["AI agent marketplace", "autonomous AI agent", "AI-to-AI", "crypto AI agent",
                "Solana AI", "MCP server", "agent protocol", "AI service marketplace"]

    kw = random.choice(keywords)
    found = 0
    try:
        from browser_agent import browser
        tweets = await browser.search_twitter(kw, max_results=5)
        sent_ids = set(o.get("id") for o in mem.get("opportunities_sent", []))
        today_opps = [o for o in mem.get("todays_opportunities", []) ]
        today_ids = set(o.get("id") for o in today_opps)

        for tweet in tweets:
            tweet_id = tweet.get("id", tweet.get("url", ""))
            if tweet_id in sent_ids or tweet_id in today_ids:
                continue

            comment = await llm(
                f"Write a short, helpful reply (max 500 chars) to this tweet:\n"
                f"Tweet: {tweet.get('text', '')[:300]}\n\n"
                f"Rules:\n- Be helpful, add value\n- Mention MAXIA only if relevant\n- Max 280 chars\n- Complete your sentence.",
                system=CEO_SYSTEM_PROMPT,
                max_tokens=300,
            )

            mem.setdefault("todays_opportunities", []).append({
                "id": tweet_id,
                "url": tweet.get("url", ""),
                "author": tweet.get("author", ""),
                "text": tweet.get("text", "")[:300],
                "suggested_comment": comment[:500] if comment else "",
                "keyword": kw,
                "ts": time.time(),
            })
            found += 1
        log.info("Twitter scan [%s]: %d new opportunities (total today: %d)", kw, found, len(mem.get("todays_opportunities", [])))

        # Check @MAXIA_WORLD mentions (immediate alert)
        mentions = await browser.search_twitter("@MAXIA_WORLD", max_results=5)
        new_mentions = [m for m in mentions if m.get("id", m.get("url", "")) not in sent_ids]
        if new_mentions:
            mention_body = "Nouvelles mentions de @MAXIA_WORLD:\n\n"
            for i, m in enumerate(new_mentions[:5], 1):
                mention_body += f"#{i} — @{m.get('author', '?')}\n"
                mention_body += f"  {m.get('text', '')[:300]}\n"
                mention_body += f"  Lien: {m.get('url', '')}\n\n"
            await send_mail("[MAXIA CEO] \U0001f514 Mention Twitter", mention_body)
            for m in new_mentions:
                mem.setdefault("opportunities_sent", []).append({"id": m.get("id", m.get("url", "")), "type": "mention", "date": datetime.now().isoformat()})
    except Exception as e:
        log.error("Twitter scan error: %s", e)


async def mission_send_best_opportunities(mem: dict, actions: dict):
    """Envoie le best-of des opportunites accumulees (1x/jour a 10h)."""
    if actions["counts"].get("opportunities_sent", 0) >= 1:
        return

    all_opps = mem.get("todays_opportunities", [])
    if not all_opps:
        log.info("Aucune opportunite accumulee — skip mail")
        return

    # Trier par pertinence — utiliser le LLM pour choisir les 5 meilleures
    if len(all_opps) > 5:
        opps_text = "\n".join(f"{i+1}. @{o['author']}: {o['text'][:150]}" for i, o in enumerate(all_opps[:20]))
        ranking = await llm(
            f"Here are {len(all_opps[:20])} tweets found today. Pick the 5 most relevant for MAXIA "
            f"(an AI-to-AI marketplace). Return ONLY the numbers (e.g. '3,7,1,12,5'):\n\n{opps_text}",
            system=CEO_SYSTEM_PROMPT,
            max_tokens=50,
        )
        try:
            indices = [int(x.strip()) - 1 for x in ranking.split(",") if x.strip().isdigit()]
            best = [all_opps[i] for i in indices if 0 <= i < len(all_opps)][:5]
        except Exception:
            best = all_opps[:5]
    else:
        best = all_opps[:5]

    # GitHub opportunities — re-generer les commentaires vides
    all_gh = mem.get("todays_github_opportunities", [])
    for opp in all_gh:
        if not opp.get("suggested_comment"):
            comment = await llm(
                f"A developer posted this GitHub issue:\n"
                f"Repo: {opp.get('repo', '')}\n"
                f"Title: {opp.get('title', '')}\n"
                f"Body: {opp.get('body_preview', '')}\n\n"
                f"Write a helpful reply (max 500 chars) that adds value. "
                f"Mention MAXIA only if directly relevant to their problem. "
                f"Be a helpful developer, not a salesperson. Complete your sentence.",
                system=CEO_SYSTEM_PROMPT,
                max_tokens=300,
            )
            opp["suggested_comment"] = comment[:500] if comment else ""
    best_gh = all_gh[:5]

    # Reddit opportunities — re-generer les commentaires vides
    all_reddit = mem.get("todays_reddit_opportunities", [])
    for opp in all_reddit:
        if not opp.get("suggested_comment"):
            comment = await llm(
                f"A developer posted this on r/{opp.get('subreddit', '')}:\n"
                f"Title: {opp.get('title', '')}\n"
                f"Body: {opp.get('body_preview', opp.get('title', ''))[:200]}\n\n"
                f"Write a helpful reply (max 500 chars). Be a helpful community member, not a salesperson. Complete your sentence.",
                system=CEO_SYSTEM_PROMPT,
                max_tokens=300,
            )
            opp["suggested_comment"] = comment[:500] if comment else ""

    # Twitter opportunities — re-generer les commentaires vides
    for opp in best:
        if not opp.get("suggested_comment"):
            comment = await llm(
                f"A developer tweeted: {opp.get('text', '')[:200]}\n\n"
                f"Write a helpful reply (max 500 chars). Be insightful, not promotional. Complete your sentence.",
                system=CEO_SYSTEM_PROMPT,
                max_tokens=300,
            )
            opp["suggested_comment"] = comment[:500] if comment else ""

    today = datetime.now().strftime("%d/%m/%Y")
    body = f"MAXIA CEO — Opportunites du {today}\n\n"

    if best:
        body += f"═══ TWITTER ({len(best)} / {len(all_opps)} scannes) ═══\n\n"
        for i, opp in enumerate(best, 1):
            body += f"--- Twitter #{i} ---\n"
            body += f"Auteur: @{opp['author']}\n"
            body += f"Tweet: {opp['text']}\n"
            body += f"Lien: {opp['url']}\n"
            body += f"Commentaire suggere: {opp['suggested_comment']}\n\n"

    if best_gh:
        body += f"═══ GITHUB ({len(best_gh)} / {len(all_gh)} scannes) ═══\n\n"
        for i, opp in enumerate(best_gh, 1):
            body += f"--- GitHub #{i} ---\n"
            body += f"Repo: {opp.get('repo', '')}\n"
            body += f"Issue: {opp.get('title', '')}\n"
            body += f"Auteur: @{opp.get('author', '')}\n"
            body += f"Lien: {opp.get('url', '')}\n"
            body += f"Commentaire suggere: {opp.get('suggested_comment', '')}\n\n"

    # Reddit opportunities (all_reddit deja charge + commentaires re-generes)
    best_reddit = all_reddit[:3]

    if best_reddit:
        body += f"═══ REDDIT ({len(best_reddit)} / {len(all_reddit)} scannes) ═══\n\n"
        for i, opp in enumerate(best_reddit, 1):
            body += f"--- Reddit #{i} ---\n"
            body += f"Subreddit: r/{opp.get('subreddit', '')}\n"
            body += f"Post: {opp.get('title', '')}\n"
            body += f"Auteur: u/{opp.get('author', '')}\n"
            body += f"Lien: {opp.get('url', '')}\n"
            body += f"Commentaire suggere: {opp.get('suggested_comment', '')}\n\n"

    # Discord servers
    all_discord = mem.get("todays_discord_opportunities", [])
    if all_discord:
        body += f"═══ DISCORD ({len(all_discord)} serveurs trouves) ═══\n\n"
        for i, srv in enumerate(all_discord[:5], 1):
            body += f"--- Serveur #{i} ---\n"
            body += f"Nom: {srv.get('name', '')}\n"
            body += f"Description: {srv.get('description', '')}\n"
            body += f"Lien: {srv.get('url', '')}\n\n"

    total = len(best) + len(best_gh) + len(best_reddit) + len(all_discord)
    if total == 0:
        body += "Aucune opportunite pertinente trouvee aujourd'hui.\n"

    await send_mail(f"[MAXIA CEO] {total} opportunites du jour - {today}", body)
    mem["opportunities_sent"].extend(best)
    mem.setdefault("github_opportunities", []).extend(best_gh)
    mem.setdefault("reddit_opportunities", []).extend(best_reddit)
    mem.setdefault("discord_opportunities", []).extend(all_discord)
    mem["todays_opportunities"] = []
    mem["todays_github_opportunities"] = []
    mem["todays_reddit_opportunities"] = []
    mem["todays_discord_opportunities"] = []
    actions["counts"]["opportunities_sent"] = 1


# ══════════════════════════════════════════
# Mission 2c — Scan GitHub issues/discussions (horaire)
# ══════════════════════════════════════════

_GITHUB_KEYWORDS = [
    "marketplace", "monetize", "AI agent", "MCP server", "escrow",
    "autonomous agent", "agent-to-agent", "USDC payment", "swap token",
    "GPU rental", "agent protocol", "agent marketplace",
]

_BLOCKED_ORGS = ["langchain-ai"]  # Banni — ne pas scanner

async def mission_github_scan_hourly(mem: dict):
    """Scan horaire GitHub — cherche des issues/discussions pertinentes."""
    repos_to_scan = [r for r in GITHUB_REPOS if not any(blocked in r for blocked in _BLOCKED_ORGS)]
    repo = random.choice(repos_to_scan)
    found = 0

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # Scanner les issues recentes
            resp = await client.get(
                f"https://api.github.com/repos/{repo}/issues?state=open&sort=updated&per_page=10",
                headers={"Accept": "application/vnd.github.v3+json"},
            )
            if resp.status_code != 200:
                log.warning("GitHub API %d for %s", resp.status_code, repo)
                return

            issues = resp.json()
            sent_ids = set(o.get("id") for o in mem.get("github_opportunities", []))
            today_gh = mem.setdefault("todays_github_opportunities", [])
            today_ids = set(o.get("id") for o in today_gh)

            for issue in issues:
                issue_id = str(issue.get("id", ""))
                if issue_id in sent_ids or issue_id in today_ids:
                    continue

                title = issue.get("title", "")
                body_text = (issue.get("body") or "")[:500]
                content = f"{title} {body_text}".lower()

                # Verifier la pertinence
                relevant = any(kw.lower() in content for kw in _GITHUB_KEYWORDS)
                if not relevant:
                    continue

                # Generer un commentaire suggere
                comment = await llm(
                    f"A developer posted this GitHub issue:\n"
                    f"Repo: {repo}\n"
                    f"Title: {title}\n"
                    f"Body: {body_text[:300]}\n\n"
                    f"Write a helpful reply (max 500 chars) that adds value. "
                    f"Mention MAXIA only if directly relevant to their problem. "
                    f"Be a helpful developer, not a salesperson. Complete your sentence.",
                    system=CEO_SYSTEM_PROMPT,
                    max_tokens=300,
                )

                today_gh.append({
                    "id": issue_id,
                    "repo": repo,
                    "title": title[:150],
                    "url": issue.get("html_url", ""),
                    "author": issue.get("user", {}).get("login", ""),
                    "body_preview": body_text[:200],
                    "suggested_comment": comment[:500] if comment else "",
                    "type": "issue",
                    "ts": time.time(),
                })
                found += 1

                if found >= 3:
                    break

            await asyncio.sleep(1)  # Rate limit GitHub

            # Scanner aussi les discussions si le repo en a
            try:
                resp_disc = await client.get(
                    f"https://api.github.com/repos/{repo}/discussions?per_page=5",
                    headers={"Accept": "application/vnd.github.v3+json"},
                )
                if resp_disc.status_code == 200:
                    for disc in resp_disc.json():
                        disc_id = str(disc.get("id", ""))
                        if disc_id in sent_ids or disc_id in today_ids:
                            continue
                        title = disc.get("title", "")
                        if any(kw.lower() in title.lower() for kw in _GITHUB_KEYWORDS):
                            comment = await llm(
                                f"A developer started this GitHub discussion:\n"
                                f"Repo: {repo}\nTitle: {title}\n\n"
                                f"Write a helpful reply (max 200 chars).",
                                system=CEO_SYSTEM_PROMPT,
                                max_tokens=80,
                            )
                            today_gh.append({
                                "id": disc_id, "repo": repo, "title": title[:150],
                                "url": disc.get("html_url", ""), "author": disc.get("user", {}).get("login", ""),
                                "suggested_comment": comment[:200] if comment else "",
                                "type": "discussion", "ts": time.time(),
                            })
                            found += 1
            except Exception:
                pass  # Discussions API not available for all repos

    except Exception as e:
        log.error("GitHub scan error: %s", e)

    log.info("GitHub scan [%s]: %d opportunities (total today: %d)", repo, found, len(mem.get("todays_github_opportunities", [])))


# ══════════════════════════════════════════
# Mission 2d — Scan Reddit (horaire)
# ══════════════════════════════════════════

_REDDIT_SUBS = ["LocalLLaMA", "SolanaDev", "solana", "ethereum", "MachineLearning", "artificial", "ollama", "defi"]

async def mission_reddit_scan_hourly(mem: dict):
    """Scan Reddit — cherche des posts pertinents pour MAXIA."""
    sub = random.choice(_REDDIT_SUBS)
    found = 0
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"https://www.reddit.com/r/{sub}/new.json?limit=15",
                headers={"User-Agent": "MAXIA-CEO/2.0"},
            )
            if resp.status_code != 200:
                log.warning("Reddit %d for r/%s", resp.status_code, sub)
                return

            posts = resp.json().get("data", {}).get("children", [])
            sent_ids = set(o.get("id") for o in mem.get("reddit_opportunities", []))
            today_reddit = mem.setdefault("todays_reddit_opportunities", [])
            today_ids = set(o.get("id") for o in today_reddit)

            for post in posts:
                data = post.get("data", {})
                post_id = data.get("id", "")
                if post_id in sent_ids or post_id in today_ids:
                    continue

                title = data.get("title", "")
                selftext = (data.get("selftext") or "")[:300]
                content = f"{title} {selftext}".lower()

                relevant = any(kw.lower() in content for kw in _GITHUB_KEYWORDS)
                if not relevant:
                    continue

                comment = await llm(
                    f"A developer posted this on r/{sub}:\n"
                    f"Title: {title}\n"
                    f"Body: {selftext[:200]}\n\n"
                    f"Write a helpful reply (max 500 chars). Be a helpful community member, not a salesperson. Complete your sentence.",
                    system=CEO_SYSTEM_PROMPT,
                    max_tokens=300,
                )

                today_reddit.append({
                    "id": post_id,
                    "subreddit": sub,
                    "title": title[:150],
                    "url": f"https://reddit.com{data.get('permalink', '')}",
                    "author": data.get("author", ""),
                    "suggested_comment": comment[:200] if comment else "",
                    "ts": time.time(),
                })
                found += 1
                if found >= 3:
                    break

    except Exception as e:
        log.error("Reddit scan error: %s", e)

    log.info("Reddit scan [r/%s]: %d opportunities (total today: %d)", sub, found, len(mem.get("todays_reddit_opportunities", [])))


# ══════════════════════════════════════════
# Mission 2e — Scan Discord (via serveurs publics indexés)
# ══════════════════════════════════════════

_DISCORD_SEARCH_TERMS = ["AI agent marketplace", "autonomous agent crypto", "MCP server solana", "agent escrow USDC"]

async def mission_discord_scan_hourly(mem: dict):
    """Discord — pas d'API publique. Utilise une liste statique de serveurs pertinents."""
    # Disboard bloque les bots (403). On maintient une liste statique.
    # Le CEO inclura ces serveurs dans le rapport hebdomadaire pour qu'Alexis les rejoigne manuellement.
    pass  # Scan actif desactive — serveurs listes dans le rapport concurrentiel


# ══════════════════════════════════════════
# Mission 3 — Rapport GitHub + skills + annuaires
# ══════════════════════════════════════════

async def mission_daily_report(mem: dict, actions: dict):
    """Scan GitHub repos, cherche skills/annuaires, envoie rapport."""
    if actions["counts"]["report_sent"] >= 1:
        log.info("Rapport deja envoye aujourd'hui — skip")
        return

    report_parts = []

    # 3a: GitHub repos
    report_parts.append("=== GITHUB REPOS — NOUVEAUTES ===\n")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            for repo in GITHUB_REPOS[:10]:  # Max 10 pour pas rate limit
                try:
                    resp = await client.get(
                        f"https://api.github.com/repos/{repo}/releases/latest",
                        headers={"Accept": "application/vnd.github.v3+json"},
                    )
                    if resp.status_code == 200:
                        release = resp.json()
                        pub_date = release.get("published_at", "")[:10]
                        # Seulement les releases des 7 derniers jours
                        if pub_date >= (datetime.now().strftime("%Y-%m-") + "01"):
                            report_parts.append(f"- {repo}: {release.get('name', 'new release')} ({pub_date})\n")
                            report_parts.append(f"  {release.get('body', '')[:200]}\n\n")
                except Exception:
                    pass
                await asyncio.sleep(1)  # Rate limit GitHub API
    except Exception as e:
        report_parts.append(f"  Erreur scan GitHub: {e}\n")

    # 3a-bis: LLM analysis of GitHub findings
    github_summary = "".join(report_parts)
    if "new release" in github_summary.lower() or len(report_parts) > 2:
        llm_analysis = await llm(
            f"Here are recent GitHub releases from projects in the AI agent ecosystem:\n\n"
            f"{github_summary}\n\n"
            f"MAXIA is an AI-to-AI marketplace on 14 blockchains (Solana, Base, etc.) with on-chain escrow, "
            f"token swaps, GPU rental, and 17 AI services.\n\n"
            f"For each release, explain in 1-2 sentences:\n"
            f"1. What this means for MAXIA (opportunity, threat, or neutral)\n"
            f"2. Any concrete action MAXIA should take\n\n"
            f"Prioritize findings by relevance to MAXIA (most relevant first).",
            system="You are the MAXIA CEO analyzing competitive intelligence. Be concise and actionable.",
            max_tokens=600,
        )
        if llm_analysis:
            report_parts.append("\n=== ANALYSE CEO — Impact pour MAXIA ===\n")
            report_parts.append(llm_analysis + "\n")

    # 3b: Annuaires ou inscrire MAXIA
    report_parts.append("\n=== ANNUAIRES & VISIBILITE ===\n")
    directories = await llm(
        "List 5 websites or directories where an AI-to-AI marketplace should be listed for visibility. "
        "For each, give: name, URL, and what needs to be done to register. "
        "Focus on: AI directories, Product Hunt alternatives, agent registries, crypto/DeFi directories. "
        "Format: 1. Name — URL — Action needed",
        system="You are an AI marketing expert. Give practical, actionable suggestions.",
        max_tokens=500,
    )
    report_parts.append(directories + "\n" if directories else "  Pas de suggestions\n")

    # 3c: Nouveaux agents inscrits
    report_parts.append("\n=== NOUVEAUX AGENTS INSCRITS ===\n")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{VPS_URL}/api/public/leaderboard")
            if resp.status_code == 200:
                data = resp.json()
                agents = data.get("agents", data.get("leaderboard", []))
                known = set(mem.get("agents_seen", []))
                new_agents = [a for a in agents if a.get("name", "") not in known]
                if new_agents:
                    for a in new_agents[:10]:
                        report_parts.append(f"  - {a.get('name', '?')} (wallet: {str(a.get('wallet', ''))[:12]}...)\n")
                        mem["agents_seen"].append(a.get("name", ""))
                else:
                    report_parts.append("  Aucun nouvel agent\n")
    except Exception as e:
        report_parts.append(f"  Erreur: {e}\n")

    # Envoyer le mail
    today = datetime.now().strftime("%d/%m/%Y")
    body = f"MAXIA CEO — Rapport quotidien du {today}\n\n" + "".join(report_parts)
    await send_mail(f"[MAXIA CEO] Rapport quotidien - {today}", body)
    actions["counts"]["report_sent"] = 1


# ══════════════════════════════════════════
# Mission 4 — Moderation forum
# ══════════════════════════════════════════

async def mission_moderate_forum(mem: dict):
    """Scanne les nouveaux posts du forum, detecte le spam."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{VPS_URL}/api/public/forum?sort=new&limit=20")
            if resp.status_code != 200:
                return
            data = resp.json()
            posts = data.get("posts", [])

        moderated_ids = set(m.get("id") for m in mem.get("moderation_log", []))
        suspicious = []

        for post in posts:
            pid = post.get("id", "")
            if pid in moderated_ids:
                continue

            title = post.get("title", "")
            body = post.get("body", "")
            content = f"{title} {body}".lower()

            # Detection basique de spam
            is_spam = False
            reasons = []
            if content.count("http") > 3:
                is_spam = True
                reasons.append("trop de liens")
            if any(w in content for w in ["buy followers", "free money", "send me", "dm me", "click here", "airdrop claim"]):
                is_spam = True
                reasons.append("mots spam detectes")
            if len(body) > 0 and len(set(body.split())) < 5 and len(body) > 50:
                is_spam = True
                reasons.append("contenu repetitif")

            mem["moderation_log"].append({"id": pid, "title": title[:50], "spam": is_spam, "ts": time.time()})

            if is_spam:
                suspicious.append({"id": pid, "title": title, "author": post.get("author_name", ""), "reasons": reasons})

        if suspicious:
            body = "⚠️ Posts suspects detectes sur le forum:\n\n"
            for s in suspicious:
                body += f"- \"{s['title']}\" par {s['author']}\n  Raisons: {', '.join(s['reasons'])}\n  ID: {s['id']}\n\n"
            body += "Action: verifier et supprimer si necessaire via /api/admin/forum/ban"
            await send_mail("[MAXIA CEO] ⚠️ Moderation forum - posts suspects", body)
            log.warning("Posts suspects: %d", len(suspicious))
        else:
            log.info("Moderation: %d posts verifies, aucun suspect", len(posts))

    except Exception as e:
        log.error("Moderation error: %s", e)


# ══════════════════════════════════════════
# Mission 6 — Veille concurrentielle (hebdo)
# ══════════════════════════════════════════

COMPETITOR_URLS = [
    {"name": "Virtuals Protocol", "url": "https://api.virtuals.io", "site": "https://virtuals.io"},
    {"name": "Autonolas (Olas)", "url": "https://registry.olas.network", "site": "https://olas.network"},
    {"name": "CrewAI", "url": "https://www.crewai.com", "site": "https://www.crewai.com"},
    {"name": "Fetch.ai Marketplace", "url": "https://agentverse.ai", "site": "https://fetch.ai"},
]


async def mission_competitive_watch(mem: dict, actions: dict) -> None:
    """Scan hebdomadaire des concurrents — envoie un rapport comparatif."""
    if actions["counts"].get("competitive_watch", 0) >= 1:
        log.info("Veille concurrentielle deja faite cette semaine — skip")
        return

    competitor_data = []
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        for comp in COMPETITOR_URLS:
            snippet = ""
            try:
                resp = await client.get(comp["site"], headers={"User-Agent": "MAXIA-CEO/2.0"})
                if resp.status_code == 200:
                    # Extract text content (first 2000 chars of body for LLM analysis)
                    raw = resp.text[:2000]
                    snippet = raw
                else:
                    snippet = f"(HTTP {resp.status_code})"
            except Exception as e:
                snippet = f"(erreur: {str(e)[:60]})"

            competitor_data.append({"name": comp["name"], "site": comp["site"], "snippet": snippet})
            await asyncio.sleep(2)  # Politesse

    # LLM analysis
    comp_text = ""
    for c in competitor_data:
        comp_text += f"\n--- {c['name']} ({c['site']}) ---\n{c['snippet'][:500]}\n"

    analysis = await llm(
        f"Analyze these AI agent marketplace competitors vs MAXIA.\n\n"
        f"MAXIA: AI-to-AI marketplace on 14 blockchains, on-chain escrow (Solana+Base), "
        f"65 token swaps, GPU rental (Akash), 17 AI services, 46 MCP tools, "
        f"tokenized stocks, autonomous CEO agent.\n\n"
        f"Competitors:\n{comp_text}\n\n"
        f"For each competitor:\n"
        f"1. What they offer that MAXIA doesn't\n"
        f"2. What MAXIA offers that they don't\n"
        f"3. Threat level (LOW/MEDIUM/HIGH)\n"
        f"4. One actionable recommendation for MAXIA\n\n"
        f"End with: Overall competitive position summary (2-3 sentences).",
        system="You are a strategic analyst for MAXIA. Be specific, factual, and actionable.",
        max_tokens=800,
    )

    # Build report
    week_num = datetime.now().isocalendar()[1]
    year = datetime.now().year
    today = datetime.now().strftime("%d/%m/%Y")

    body = f"MAXIA CEO — Veille concurrentielle semaine {week_num} ({year})\n"
    body += f"Date: {today}\n\n"
    body += "=== CONCURRENTS SCANNES ===\n"
    for c in competitor_data:
        status = "OK" if "(HTTP" not in c["snippet"] and "(erreur" not in c["snippet"] else "ERREUR"
        body += f"  - {c['name']} ({c['site']}): {status}\n"
    body += f"\n=== ANALYSE STRATEGIQUE ===\n{analysis}\n" if analysis else "\n(analyse LLM indisponible)\n"

    await send_mail(f"[MAXIA CEO] Veille concurrentielle - semaine {week_num}", body)
    actions["counts"]["competitive_watch"] = 1
    mem.setdefault("competitive_reports", []).append({
        "date": today,
        "week": week_num,
        "competitors_scanned": len(competitor_data),
    })
    log.info("Veille concurrentielle semaine %d envoyee", week_num)


# ══════════════════════════════════════════
# Mission 7 — Surveillance sante site
# ══════════════════════════════════════════

async def mission_health_check(mem: dict):
    """Ping le site et verifie les endpoints critiques (GET + POST)."""
    # GET checks
    get_checks = {
        "site": f"{VPS_URL}/",
        "prices": f"{VPS_URL}/api/public/crypto/prices",
        "forum": f"{VPS_URL}/api/public/forum",
    }
    # crypto_quote needs params — check via POST checks instead
    # POST checks — verify endpoints respond (don't actually create data)
    post_checks = {
        "register_endpoint": {
            "url": f"{VPS_URL}/api/public/register",
            "json": {},  # Empty body — expect 422 (validation error), not 500/timeout
            "accept_codes": [200, 400, 422],
        },
        "forum_create_endpoint": {
            "url": f"{VPS_URL}/api/public/forum/create",
            "json": {},  # Empty body — expect 422 (validation error), not 500/timeout
            "accept_codes": [200, 400, 401, 422],
        },
    }
    failures = []

    async with httpx.AsyncClient(timeout=8) as client:
        # GET endpoints
        for name, url in get_checks.items():
            try:
                resp = await client.get(url)
                if resp.status_code != 200:
                    failures.append(f"{name}: HTTP {resp.status_code}")
            except Exception as e:
                failures.append(f"{name}: {str(e)[:50]}")

        # POST endpoints — just verify they respond (any non-5xx = OK)
        for name, cfg in post_checks.items():
            try:
                resp = await client.post(cfg["url"], json=cfg["json"])
                if resp.status_code >= 500:
                    failures.append(f"{name}: HTTP {resp.status_code} (server error)")
                elif resp.status_code not in cfg["accept_codes"]:
                    # Unexpected but not a failure — log for info
                    log.info("Health POST %s: HTTP %d (unexpected but not 5xx)", name, resp.status_code)
            except Exception as e:
                failures.append(f"{name}: {str(e)[:50]}")

    if failures:
        alert = "🔴 MAXIA DOWN — Endpoints en erreur:\n\n" + "\n".join(f"  - {f}" for f in failures)
        alert += f"\n\nTimestamp: {datetime.now().isoformat()}"

        # Eviter le spam d'alertes (max 1 par 10 min)
        last_alert = mem.get("health_alerts", [{}])[-1].get("ts", 0) if mem.get("health_alerts") else 0
        if time.time() - last_alert > 600:
            await send_mail("[MAXIA CEO] 🔴 SITE DOWN - maxiaworld.app", alert)
            mem["health_alerts"].append({"ts": time.time(), "failures": failures})
            log.error("ALERTE: %s", ", ".join(failures))
    else:
        log.info("Health OK — tous les endpoints repondent")


# ══════════════════════════════════════════
# Boucle principale
# ══════════════════════════════════════════

async def run():
    """Boucle principale du CEO Local V2."""
    log.info("═══════════════════════════════════════")
    log.info("  MAXIA CEO Local V2 — demarrage")
    log.info("  Modele: %s", OLLAMA_MODEL)
    log.info("  Email: %s", ALEXIS_EMAIL)
    log.info("  VPS: %s", VPS_URL)
    log.info("  Kaspa Mining: %s", "ACTIF" if KASPA_MINING_ENABLED else "DESACTIVE")
    log.info("═══════════════════════════════════════")

    # Demarrer le miner Kaspa si active
    if KASPA_MINING_ENABLED:
        if start_miner():
            log.info("[MINING] Kaspa miner demarre au boot")
        else:
            log.warning("[MINING] Echec demarrage miner — verifier TeamRedMiner")

    mem = _load_memory()
    mem.setdefault("todays_opportunities", [])
    last_health = 0
    last_mining_stats = 0
    last_moderation = 0
    last_twitter_scan = 0
    last_tweet = 0
    last_opportunities_mail = 0
    last_report = 0
    last_competitive = 0

    while True:
        try:
            now = time.time()
            dt_now = datetime.now()
            hour = dt_now.hour
            weekday = dt_now.weekday()  # 0=lundi, 6=dimanche
            actions = _load_actions_today()

            # Mission 7: Health check (toutes les 5 min)
            if now - last_health >= HEALTH_CHECK_INTERVAL_S:
                await mission_health_check(mem)
                last_health = now
                actions["counts"]["health_checks"] = actions["counts"].get("health_checks", 0) + 1

            # Mission 4: Moderation forum (toutes les heures)
            if now - last_moderation >= MODERATION_INTERVAL_S:
                await mission_moderate_forum(mem)
                last_moderation = now
                actions["counts"]["moderation_done"] = actions["counts"].get("moderation_done", 0) + 1

            # Mission 2a: Scan ALL platforms HOURLY (accumule les opportunites)
            if now - last_twitter_scan >= 3600:
                await mission_twitter_scan_hourly(mem)
                await mission_github_scan_hourly(mem)
                await mission_reddit_scan_hourly(mem)
                await mission_discord_scan_hourly(mem)
                last_twitter_scan = now

            # Mission 2b: Envoyer le best-of par mail (10h)
            if hour == 10 and actions["counts"].get("opportunities_sent", 0) == 0:
                if now - last_opportunities_mail >= 3600:
                    await mission_send_best_opportunities(mem, actions)
                    last_opportunities_mail = now

            # Mission 3: Rapport quotidien (9h)
            if hour == 9 and actions["counts"].get("report_sent", 0) == 0:
                if now - last_report >= 3600:
                    await mission_daily_report(mem, actions)
                    last_report = now

            # Mission 1: Tweet feature (15h-16h — peak US EST 9:30 AM)
            if 15 <= hour <= 16 and actions["counts"].get("tweet_feature", 0) == 0:
                if now - last_tweet >= 3600:
                    await mission_tweet_feature(mem, actions)
                    last_tweet = now

            # Mission 6: Veille concurrentielle (dimanche 10h)
            if weekday == 6 and hour == 10 and actions["counts"].get("competitive_watch", 0) == 0:
                if now - last_competitive >= 3600:
                    await mission_competitive_watch(mem, actions)
                    last_competitive = now

            # Mission 9: Check emails de Alexis (toutes les 5 min)
            if now - mem.get("_last_email_check", 0) >= 300:
                await mission_check_alexis_emails(mem)
                mem["_last_email_check"] = now

            # Mining — relancer si GPU libre depuis 60s (pas d'appel LLM recent)
            if KASPA_MINING_ENABLED and not is_mining() and _last_llm_call > 0:
                idle_since = now - _last_llm_call
                if idle_since >= 60:
                    start_miner()
                    log.info("[MINING] Miner relance (GPU idle depuis %ds)", int(idle_since))

            # Mining stats (toutes les heures)
            if KASPA_MINING_ENABLED and now - last_mining_stats >= 3600:
                stats = get_stats()
                log.info("[MINING] Stats: mining=%s, total=%.2fh, starts=%d, stops=%d, hashrate=%s",
                         stats["is_mining"], stats["total_mining_hours"],
                         stats["starts"], stats["stops"], stats["hashrate"])
                last_mining_stats = now

            # Sauvegarder
            _save_memory(mem)
            _save_actions(actions)

        except Exception as e:
            log.error("Boucle principale error: %s", e)

        await asyncio.sleep(60)  # Check toutes les minutes


# ══════════════════════════════════════════
# Mission 9 — Check emails de Alexis (mode mail)
# ══════════════════════════════════════════

async def mission_check_alexis_emails(mem: dict):
    """Lit ceo@maxiaworld.app, si Alexis a envoyé un mail → répond avec le LLM."""
    try:
        from email_manager import read_inbox
        emails = await read_inbox(max_emails=5)
        if not emails:
            return

        answered_ids = set(mem.get("emails_answered", []))
        for em in emails:
            msg_id = em.get("message_id", em.get("uid", ""))
            if msg_id in answered_ids:
                continue

            from_addr = em.get("from_addr", "").lower()
            subject = em.get("subject", "")
            body = em.get("body", "")

            # Repondre seulement aux mails d'Alexis
            if "majorel" not in from_addr and "maxia" not in from_addr:
                continue

            log.info("Email recu de %s: %s", from_addr, subject[:50])

            # Generer la reponse avec le LLM + knowledge base
            response = await llm(
                f"Alexis (the founder of MAXIA) sent you this email:\n\n"
                f"Subject: {subject}\n"
                f"Body: {body[:2000]}\n\n"
                f"Reply as the MAXIA CEO. Be helpful, concise, and factual. "
                f"If he asks you to do something, explain what you can do and what you've done. "
                f"If he asks about MAXIA status, give real info from your knowledge base.",
                system=CEO_SYSTEM_PROMPT,
                max_tokens=500,
            )

            if response:
                # Envoyer la reponse par mail
                reply_subject = f"Re: {subject}" if not subject.startswith("Re:") else subject
                await send_mail(reply_subject, response)
                log.info("Email repondu: %s", reply_subject[:50])

                mem.setdefault("emails_answered", []).append(msg_id)
                if len(mem["emails_answered"]) > 100:
                    mem["emails_answered"] = mem["emails_answered"][-100:]
    except Exception as e:
        log.error("Email check error: %s", e)


# ══════════════════════════════════════════
# Mode terminal interactif
# ══════════════════════════════════════════

async def terminal_mode():
    """Mode interactif — parler au CEO en direct."""
    print("\n  ╔═══════════════════════════════════════╗")
    print("  ║  MAXIA CEO — Mode Terminal            ║")
    print("  ║  Tape ta question, 'quit' pour sortir ║")
    print("  ╚═══════════════════════════════════════╝\n")

    mem = _load_memory()

    while True:
        try:
            user_input = await asyncio.get_event_loop().run_in_executor(
                None, lambda: input("\n[Alexis] > ")
            )
        except (EOFError, KeyboardInterrupt):
            break

        if not user_input.strip():
            continue
        if user_input.strip().lower() in ("quit", "exit", "q"):
            print("[CEO] Au revoir, Alexis.")
            break

        cmd = user_input.strip().lower()

        # Commandes speciales
        if cmd == "status":
            print(f"[CEO] Tweets postes: {len(mem.get('tweets_posted', []))}")
            print(f"[CEO] Opportunites accumulees: {len(mem.get('todays_opportunities', []))}")
            print(f"[CEO] Agents vus: {len(mem.get('agents_seen', []))}")
            print(f"[CEO] Health alerts: {len(mem.get('health_alerts', []))}")
            continue

        if cmd == "scan twitter":
            print("[CEO] Scan Twitter en cours...")
            await mission_twitter_scan_hourly(mem)
            _save_memory(mem)
            print(f"[CEO] Done — {len(mem.get('todays_opportunities', []))} opportunites Twitter accumulees")
            continue

        if cmd == "scan github":
            print("[CEO] Scan GitHub issues/discussions en cours...")
            await mission_github_scan_hourly(mem)
            _save_memory(mem)
            print(f"[CEO] Done — {len(mem.get('todays_github_opportunities', []))} opportunites GitHub accumulees")
            continue

        if cmd == "rapport":
            print("[CEO] Generation du rapport quotidien...")
            actions = _load_actions_today()
            await mission_daily_report(mem, actions)
            _save_memory(mem)
            _save_actions(actions)
            print("[CEO] Rapport envoye par mail.")
            continue

        if cmd == "health":
            print("[CEO] Health check...")
            await mission_health_check(mem)
            _save_memory(mem)
            continue

        if cmd == "tweet":
            print("[CEO] Redaction du tweet...")
            actions = _load_actions_today()
            await mission_tweet_feature(mem, actions)
            _save_memory(mem)
            _save_actions(actions)
            continue

        if cmd == "send opportunities":
            print("[CEO] Envoi du best-of Twitter...")
            actions = _load_actions_today()
            await mission_send_best_opportunities(mem, actions)
            _save_memory(mem)
            _save_actions(actions)
            continue

        if cmd == "help":
            print("[CEO] Commandes disponibles:")
            print("  status           — voir les stats du jour")
            print("  scan twitter     — forcer un scan Twitter maintenant")
            print("  scan github      — forcer un scan GitHub + rapport")
            print("  health           — verifier la sante du site")
            print("  tweet            — poster le tweet du jour")
            print("  send opportunities — envoyer le best-of Twitter par mail")
            print("  help             — cette aide")
            print("  quit             — quitter")
            print("  (ou pose une question libre)")
            continue

        # Question libre → LLM
        print("[CEO] Reflexion...")
        response = await llm(
            f"Alexis asks: {user_input}\n\nRespond as the MAXIA CEO. Be helpful and factual. Use your knowledge of MAXIA.",
            system=CEO_SYSTEM_PROMPT,
            max_tokens=500,
        )
        if response:
            # Nettoyer le thinking de Qwen3
            if "<think>" in response and "</think>" in response:
                response = response.split("</think>")[-1].strip()
            print(f"\n[CEO] {response}")
        else:
            print("[CEO] Erreur LLM — pas de reponse")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "chat":
        print("""
    ╔═══════════════════════════════════════╗
    ║    MAXIA CEO Local V2 — Chat Mode     ║
    ╚═══════════════════════════════════════╝
        """)
        asyncio.run(terminal_mode())
    else:
        print("""
    ╔═══════════════════════════════════════╗
    ║    MAXIA CEO Local V2                 ║
    ║    8 missions · 1 modele · 0 spam     ║
    ╚═══════════════════════════════════════╝
        """)
        asyncio.run(run())
