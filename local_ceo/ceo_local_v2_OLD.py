"""CEO Local V3 — 14 missions, dual-model (Qwen 3.5 27B + VL 7B), zero spam.

Missions:
  1.  Tweet feature du jour (15h-16h, peak US EST)
  2.  Scan opportunites Twitter+GitHub+Reddit (18h30)
  2b. Mail best-of opportunites scorees + 3 variantes 280 chars (19h30)
  3.  Rapport quotidien GitHub+agents+annuaires (mail separe, 9h)
  4.  Moderation forum (toutes les heures)
  5.  Analyse nouveaux agents (inclus dans rapport)
  6.  Veille concurrentielle + memo strategique (1x/jour, 19h)
  7.  Surveillance sante site (toutes les 5 min)
  8.  Tweet engagement tracking (feedback loop)
  9.  Check emails Alexis (toutes les 5 min)
  10. Code Auditor (19h15, mail separe)
  11. Invitations forum (inclus dans mail opportunites)
  12. Analyse strategique profonde (inclus dans veille)
  13. Draft changelog forum (1x/semaine dimanche)
  14. Prospect scoring (inclus dans mail opportunites)
  16. Health report intelligent (8h, mail separe)

Usage: python ceo_local_v2.py
"""
import asyncio
import glob
import json
import time
import os
import random
import logging
import subprocess
import httpx
from datetime import datetime
from pathlib import Path

from config_local import (
    VPS_URL, ADMIN_KEY, OLLAMA_URL, OLLAMA_MODEL,
    ALEXIS_EMAIL, BROWSER_PROFILE_DIR,
    HEALTH_CHECK_INTERVAL_S, MODERATION_INTERVAL_S,
    GITHUB_REPOS, MAXIA_FEATURES, MAX_EMAILS_DAY,
    OFF_DAYS_PER_WEEK,
)
from kaspa_miner import start_miner, stop_miner, is_mining, get_stats

logging.basicConfig(level=logging.INFO, format="%(asctime)s [CEO] %(message)s")
log = logging.getLogger("ceo")

# Mining Kaspa — DESACTIVE (non rentable mars 2026, ASICs ont tué le GPU mining)
KASPA_MINING_ENABLED = os.getenv("KASPA_MINING_ENABLED", "0") == "1"

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


async def llm(prompt: str, system: str = "", max_tokens: int = 1000, retries: int = 2, timeout: int = 180) -> str:
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
            async with httpx.AsyncClient(timeout=timeout) as client:
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
# Anti-spam — jour off aleatoire (Phase 6)
# ══════════════════════════════════════════

def _is_off_day() -> bool:
    """1 random off day per week — deterministic via week seed."""
    import hashlib
    if OFF_DAYS_PER_WEEK <= 0:
        return False
    today = datetime.now().date()
    week_seed = f"maxia_off_{today.isocalendar()[0]}_{today.isocalendar()[1]}"
    rng = random.Random(hashlib.md5(week_seed.encode()).hexdigest())
    off_days = sorted(rng.sample(range(7), min(OFF_DAYS_PER_WEEK, 7)))
    is_off = today.weekday() in off_days
    if is_off:
        log.info("[OFF-DAY] Jour off (weekday=%d, off=%s) — pas de tweet", today.weekday(), off_days)
    return is_off


# ══════════════════════════════════════════
# Mission 1 — Tweet feature du jour
# ══════════════════════════════════════════

async def mission_tweet_feature(mem: dict, actions: dict):
    """Poste 1 tweet presentant une feature MAXIA."""
    if actions["counts"]["tweet_feature"] >= 1:
        log.info("Tweet deja poste aujourd'hui — skip")
        return

    if _is_off_day():
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


async def _score_opportunity(opp: dict, platform: str) -> dict:
    """Score une opportunite de 1 a 10 et genere 3 variantes de reponse (max 280 chars)."""
    text = opp.get("text", opp.get("title", ""))[:300]
    author = opp.get("author", "?")

    result = await llm(
        f"Analyse cette opportunite pour MAXIA (marketplace AI-to-AI, 14 blockchains):\n\n"
        f"Plateforme: {platform}\n"
        f"Auteur: @{author}\n"
        f"Contenu: {text}\n\n"
        f"1. Score de pertinence (1-10) selon: taille audience probable, pertinence sujet, probabilite de conversion\n"
        f"2. Ecris 3 reponses DIFFERENTES (max 280 chars CHACUNE):\n"
        f"   - V1 TECHNIQUE: reponse de dev expert, factuelle\n"
        f"   - V2 CASUAL: ton decontracte, amical\n"
        f"   - V3 VALUE: apporter de la valeur concrete, un conseil\n"
        f"3. Invitation forum (1 phrase, max 200 chars): invite cette personne a poster sur maxiaworld.app/forum\n\n"
        f"Format STRICT (pas d'autre texte):\n"
        f"SCORE|<nombre>\n"
        f"V1|<reponse technique max 280 chars>\n"
        f"V2|<reponse casual max 280 chars>\n"
        f"V3|<reponse value max 280 chars>\n"
        f"INVITE|<invitation forum max 200 chars>",
        system=CEO_SYSTEM_PROMPT,
        max_tokens=600,
    )

    score = 5
    variants = []
    invite = ""
    if result:
        for line in result.strip().split("\n"):
            line = line.strip()
            if line.startswith("SCORE|"):
                try:
                    score = int(line.split("|")[1].strip())
                except Exception:
                    pass
            elif line.startswith("V1|") or line.startswith("V2|") or line.startswith("V3|"):
                variants.append(line.split("|", 1)[1].strip()[:280])
            elif line.startswith("INVITE|"):
                invite = line.split("|", 1)[1].strip()[:200]

    opp["score"] = max(1, min(10, score))
    opp["variants"] = variants if variants else [opp.get("suggested_comment", "")[:280]]
    opp["forum_invite"] = invite
    return opp


async def mission_send_best_opportunities(mem: dict, actions: dict):
    """Envoie le best-of des opportunites — scorees, 3 variantes 280 chars, invitations forum (19h30)."""
    if actions["counts"].get("opportunities_sent", 0) >= 1:
        return

    all_opps = mem.get("todays_opportunities", [])
    all_gh = mem.get("todays_github_opportunities", [])
    all_reddit = mem.get("todays_reddit_opportunities", [])

    total_found = len(all_opps) + len(all_gh) + len(all_reddit)
    if total_found == 0:
        log.info("Aucune opportunite accumulee — skip mail")
        return

    # Score et genere les variantes pour chaque opportunite
    scored_twitter = []
    for opp in all_opps[:10]:
        scored_twitter.append(await _score_opportunity(opp, "Twitter"))
    scored_twitter.sort(key=lambda x: x.get("score", 0), reverse=True)
    best_tw = scored_twitter[:5]

    scored_gh = []
    for opp in all_gh[:8]:
        scored_gh.append(await _score_opportunity(opp, "GitHub"))
    scored_gh.sort(key=lambda x: x.get("score", 0), reverse=True)
    best_gh = scored_gh[:5]

    scored_reddit = []
    for opp in all_reddit[:6]:
        scored_reddit.append(await _score_opportunity(opp, "Reddit"))
    scored_reddit.sort(key=lambda x: x.get("score", 0), reverse=True)
    best_reddit = scored_reddit[:3]

    today = datetime.now().strftime("%d/%m/%Y")
    body = f"MAXIA CEO — Top Opportunites du {today}\n"
    body += f"Triees par score de pertinence (10 = parfait)\n\n"

    def _format_opp(opp: dict, num: int, platform: str) -> str:
        """Formatte une opportunite avec score + variantes."""
        s = f"--- {platform} #{num} — Score: {opp.get('score', '?')}/10 ---\n"
        s += f"Auteur: @{opp.get('author', '?')}\n"
        if platform == "Twitter":
            s += f"Tweet: {opp.get('text', '')[:300]}\n"
        elif platform == "GitHub":
            s += f"Repo: {opp.get('repo', '')}\n"
            s += f"Issue: {opp.get('title', '')}\n"
        elif platform == "Reddit":
            s += f"r/{opp.get('subreddit', '')} — {opp.get('title', '')}\n"
        s += f"Lien: {opp.get('url', '')}\n\n"
        for j, v in enumerate(opp.get("variants", []), 1):
            label = ["TECHNIQUE", "CASUAL", "VALUE"][j-1] if j <= 3 else f"V{j}"
            s += f"  Reponse {label} ({len(v)} chars):\n  {v}\n\n"
        if opp.get("forum_invite"):
            s += f"  Invitation forum:\n  {opp['forum_invite']}\n\n"
        return s

    if best_tw:
        body += f"═══ TWITTER ({len(best_tw)} best / {len(all_opps)} scannes) ═══\n\n"
        for i, opp in enumerate(best_tw, 1):
            body += _format_opp(opp, i, "Twitter")

    if best_gh:
        body += f"═══ GITHUB ({len(best_gh)} best / {len(all_gh)} scannes) ═══\n\n"
        for i, opp in enumerate(best_gh, 1):
            body += _format_opp(opp, i, "GitHub")

    if best_reddit:
        body += f"═══ REDDIT ({len(best_reddit)} best / {len(all_reddit)} scannes) ═══\n\n"
        for i, opp in enumerate(best_reddit, 1):
            body += _format_opp(opp, i, "Reddit")

    total = len(best_tw) + len(best_gh) + len(best_reddit)
    if total == 0:
        body += "Aucune opportunite pertinente trouvee aujourd'hui.\n"

    body += f"\n--- INSTRUCTIONS ---\n"
    body += f"Choisis la variante qui te plait pour chaque opportunite.\n"
    body += f"Toutes les reponses font max 280 caracteres (pret a copier-coller).\n"
    body += f"Les invitations forum sont optionnelles.\n"

    await send_mail(f"[MAXIA CEO] {total} opportunites scorees - {today}", body)
    mem["opportunities_sent"].extend(best_tw)
    mem.setdefault("github_opportunities", []).extend(best_gh)
    mem.setdefault("reddit_opportunities", []).extend(best_reddit)
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

_health_backoff_until = 0  # timestamp: skip health checks until this time (429 backoff)

async def mission_health_check(mem: dict):
    """Ping le site et verifie les endpoints critiques (GET uniquement, POST supprime pour economiser le rate limit)."""
    global _health_backoff_until

    # Backoff si on a recu 429 recemment
    if time.time() < _health_backoff_until:
        log.info("Health check skipped (backoff until %s)", datetime.fromtimestamp(_health_backoff_until).strftime("%H:%M"))
        return

    # GET checks only — les POST gaspillent 2 requetes pour rien
    get_checks = {
        "site": f"{VPS_URL}/",
        "prices": f"{VPS_URL}/api/public/crypto/prices",
    }
    failures = []

    async with httpx.AsyncClient(timeout=8) as client:
        for name, url in get_checks.items():
            try:
                resp = await client.get(url)
                if resp.status_code == 429:
                    # Rate limited — backoff 30 min
                    _health_backoff_until = time.time() + 1800
                    log.warning("Health %s: 429 rate limited — backoff 30min", name)
                    return
                if resp.status_code != 200:
                    failures.append(f"{name}: HTTP {resp.status_code}")
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
# Mission 10 — Code Auditor (GPU idle)
# ══════════════════════════════════════════

_BACKEND_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "backend"))
_AUDIT_REPORT_FILE = os.path.join(os.path.dirname(__file__), "audit_report.md")
_AUDIT_STATE_FILE = os.path.join(os.path.dirname(__file__), "audit_state.json")

CODE_AUDIT_SYSTEM_PROMPT = (
    "You are a senior Python/FastAPI security & bug auditor. "
    "You are auditing MAXIA, a production AI-to-AI marketplace (FastAPI, 559 routes, 14 blockchains, USDC payments).\n\n"
    "You will receive ONE FUNCTION at a time, along with its imports and callers.\n"
    "Analyze the function DEEPLY — check every variable, every await, every SQL query.\n\n"
    "ONLY report bugs you are 95%+ confident are REAL:\n"
    "- Variables/functions used but never defined or imported in the provided context\n"
    "- Async functions missing await (verify the function IS async before reporting)\n"
    "- SQL injection (string formatting in queries instead of parameterized)\n"
    "- Security: secrets leaked in responses, missing auth on sensitive endpoints\n"
    "- Logic errors: wrong comparison, off-by-one, division by zero\n"
    "- Type mismatches that WILL crash: None not handled, str where int expected\n\n"
    "DO NOT report: style issues, missing docstrings, potential issues, race conditions on asyncio globals, "
    "suggestions for improvement, or anything speculative. If you are not 95% sure, do NOT report it.\n\n"
    "For each bug found, output EXACTLY this format (one per bug):\n"
    "BUG|severity|line_number|description\n"
    "Severity: CRITICAL, HIGH, MEDIUM\n"
    "If no bugs found, output: CLEAN\n"
    "No other text. No explanations. Just the BUG lines or CLEAN."
)


def _load_audit_state() -> dict:
    """Load audit progress (which files have been scanned today)."""
    default = {"date": "", "files_done": [], "total_bugs": 0, "started_at": ""}
    try:
        if os.path.exists(_AUDIT_STATE_FILE):
            with open(_AUDIT_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.loads(f.read())
            today = datetime.now().strftime("%Y-%m-%d")
            if data.get("date") == today:
                return data
    except Exception:
        pass
    default["date"] = datetime.now().strftime("%Y-%m-%d")
    return default


def _save_audit_state(state: dict):
    try:
        with open(_AUDIT_STATE_FILE, "w", encoding="utf-8") as f:
            f.write(json.dumps(state, indent=2))
    except Exception as e:
        log.error("[AUDIT] State save error: %s", e)


def _extract_functions(code: str) -> list:
    """Extract (name, start_line, end_line) for each top-level function/method."""
    import re
    functions = []
    lines = code.split("\n")
    func_pattern = re.compile(r"^(async\s+)?def\s+(\w+)\s*\(")
    for i, line in enumerate(lines):
        m = func_pattern.match(line)
        if m:
            functions.append({"name": m.group(2), "start": i})
    # Set end_line for each function (start of next function or EOF)
    for j in range(len(functions)):
        if j + 1 < len(functions):
            functions[j]["end"] = functions[j + 1]["start"] - 1
        else:
            functions[j]["end"] = len(lines) - 1
    return functions


def _get_imports_section(code: str) -> str:
    """Extract the import block at the top of a file (first N lines before first def/class)."""
    lines = code.split("\n")
    for i, line in enumerate(lines):
        if line.startswith("def ") or line.startswith("async def ") or line.startswith("class "):
            return "\n".join(lines[:i])
    return "\n".join(lines[:50])


async def mission_code_audit(mem: dict, actions: dict) -> bool:
    """Audit UNE FONCTION en profondeur par jour (imports + function + callers).

    Returns True if audit complete (all functions done for current file), False otherwise.
    Strategy: 1 function/day, deep analysis with full context.
    """
    audit = _load_audit_state()

    # Lister tous les .py du backend
    all_py = sorted(glob.glob(os.path.join(_BACKEND_DIR, "*.py")))
    if not all_py:
        log.warning("[AUDIT] No .py files found in %s", _BACKEND_DIR)
        return True

    # Filtrer les fichiers deja scannes entierement
    done_set = set(audit.get("files_done", []))
    remaining = [f for f in all_py if os.path.basename(f) not in done_set]

    if not remaining:
        log.info("[AUDIT] Audit complet — %d fichiers scannes, %d bugs trouves",
                 len(done_set), audit.get("total_bugs", 0))
        return True

    # Prendre le fichier en cours
    target = remaining[0]
    filename = os.path.basename(target)

    # Lire le fichier
    try:
        with open(target, "r", encoding="utf-8", errors="replace") as f:
            code = f.read()
    except Exception as e:
        log.error("[AUDIT] Cannot read %s: %s", filename, e)
        audit["files_done"].append(filename)
        _save_audit_state(audit)
        return False

    lines = code.split("\n")
    line_count = len(lines)

    # Skip fichiers trop petits
    if line_count < 10:
        log.info("[AUDIT] Skip %s (%d lines — too small)", filename, line_count)
        audit["files_done"].append(filename)
        _save_audit_state(audit)
        return False

    # Extraire les fonctions du fichier
    functions = _extract_functions(code)
    if not functions:
        log.info("[AUDIT] Skip %s (no functions found)", filename)
        audit["files_done"].append(filename)
        _save_audit_state(audit)
        return False

    # Trouver la prochaine fonction non auditee dans ce fichier
    funcs_done = set(audit.get("funcs_done_current", []))
    remaining_funcs = [f for f in functions if f"{filename}:{f['name']}" not in funcs_done]

    if not remaining_funcs:
        # Toutes les fonctions de ce fichier sont auditees → fichier termine
        log.info("[AUDIT] %s complete — all %d functions audited", filename, len(functions))
        audit["files_done"].append(filename)
        audit["funcs_done_current"] = []
        _save_audit_state(audit)
        return False

    # Prendre UNE seule fonction
    func = remaining_funcs[0]
    func_code = "\n".join(lines[func["start"]:func["end"] + 1])
    imports_section = _get_imports_section(code)

    log.info("[AUDIT] Deep scan: %s → %s() (L%d-%d) [%d/%d funcs]",
             filename, func["name"], func["start"] + 1, func["end"] + 1,
             len(funcs_done) + 1, len(functions))

    # Construire le prompt avec contexte complet
    numbered_func = "\n".join(
        f"{func['start'] + 1 + i}: {l}" for i, l in enumerate(lines[func["start"]:func["end"] + 1])
    )

    prompt = (
        f"File: {filename}\n\n"
        f"== IMPORTS (top of file) ==\n```python\n{imports_section}\n```\n\n"
        f"== FUNCTION TO AUDIT: {func['name']}() (lines {func['start']+1}-{func['end']+1}) ==\n"
        f"```python\n{numbered_func}\n```"
    )

    # Limiter la taille
    if len(prompt) > 12000:
        prompt = prompt[:12000] + "\n... (truncated)"

    result = await llm(
        prompt,
        system=CODE_AUDIT_SYSTEM_PROMPT,
        max_tokens=500,
        timeout=600,
    )

    file_bugs = []
    if result:
        # Nettoyer le thinking Qwen3
        if "<think>" in result and "</think>" in result:
            result = result.split("</think>")[-1].strip()

        for line in result.strip().split("\n"):
            line = line.strip()
            if line.startswith("BUG|"):
                parts = line.split("|", 3)
                if len(parts) == 4:
                    file_bugs.append({
                        "severity": parts[1].strip(),
                        "line": parts[2].strip(),
                        "desc": parts[3].strip(),
                    })

    # Init le rapport si premier function
    if not audit.get("started_at"):
        audit["started_at"] = datetime.now().isoformat()
        header = (
            f"# MAXIA Code Audit Report (Deep — 1 function/day)\n"
            f"**Date**: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
            f"**Model**: {OLLAMA_MODEL}\n"
            f"**Backend files**: {len(all_py)}\n"
            f"**Auditor**: CEO Local (Mission 10 v2)\n\n"
            f"---\n\n"
        )
        with open(_AUDIT_REPORT_FILE, "w", encoding="utf-8") as f:
            f.write(header)

    # Append au rapport
    func_label = f"{filename}:{func['name']}()"
    with open(_AUDIT_REPORT_FILE, "a", encoding="utf-8") as f:
        if file_bugs:
            f.write(f"### {func_label} (L{func['start']+1}-{func['end']+1}) — {len(file_bugs)} bug(s)\n\n")
            for bug in file_bugs:
                icon = {"CRITICAL": "\U0001f534", "HIGH": "\U0001f7e0", "MEDIUM": "\U0001f7e1"}.get(bug["severity"], "\u26aa")
                f.write(f"- {icon} **{bug['severity']}** L{bug['line']}: {bug['desc']}\n")
            f.write("\n")
        else:
            f.write(f"### {func_label} (L{func['start']+1}-{func['end']+1}) — CLEAN\n\n")

    # Update state — marquer cette fonction comme faite
    audit.setdefault("funcs_done_current", []).append(f"{filename}:{func['name']}")
    audit["total_bugs"] = audit.get("total_bugs", 0) + len(file_bugs)
    _save_audit_state(audit)

    log.info("[AUDIT] %s: %d bugs | Function %d/%d in %s",
             func_label, len(file_bugs), len(funcs_done) + 1, len(functions), filename)

    # Envoyer mail quotidien avec le resultat de cette fonction
    try:
        if file_bugs:
            bug_summary = "\n".join(f"  - {b['severity']} L{b['line']}: {b['desc']}" for b in file_bugs)
            mail_body = f"Audit deep: {func_label}\n\n{bug_summary}"
        else:
            mail_body = f"Audit deep: {func_label} — CLEAN (aucun bug)"
        await send_mail(
            f"[MAXIA CEO] Audit: {func_label} — {len(file_bugs)} bug(s)",
            mail_body,
        )
    except Exception as e:
        log.error("[AUDIT] Mail send error: %s", e)

    return False


# ══════════════════════════════════════════
# Mission 17 — Scout AI (scan registries, validation Alexis)
# ══════════════════════════════════════════

_SCOUT_FILE = os.path.join(_DIR, "scout_discoveries.json")
_SCOUT_PENDING_FILE = os.path.join(_DIR, "scout_pending_contacts.json")

# Registries HTTP — pas de RPC blockchain, juste des APIs publiques
_AI_REGISTRIES = [
    # ── LIVE: agents avec endpoints contactables ──
    {"name": "Virtuals Protocol", "url": "https://api.virtuals.io/api/virtuals?filters[isLaunched]=true&pagination[limit]=20",
     "chain": "base", "method": "GET", "tier": "live"},
    {"name": "Agentverse (Fetch.ai)", "url": "https://agentverse.ai/v1/search/agents",
     "chain": "fetchai", "method": "POST", "post_body": {"search_text": "autonomous trading DeFi data"},
     "tier": "live"},
    {"name": "Agentverse Finance", "url": "https://agentverse.ai/v1/search/agents",
     "chain": "fetchai", "method": "POST", "post_body": {"search_text": "oracle price feed lending yield"},
     "tier": "live"},
    {"name": "Agentverse Infra", "url": "https://agentverse.ai/v1/search/agents",
     "chain": "fetchai", "method": "POST", "post_body": {"search_text": "compute GPU infrastructure storage"},
     "tier": "live"},
    {"name": "Smithery MCP (Crypto)", "url": "https://registry.smithery.ai/servers?q=crypto+defi+trading&pageSize=20",
     "chain": "multi", "method": "GET", "format": "smithery", "tier": "live"},
    {"name": "Smithery MCP (AI)", "url": "https://registry.smithery.ai/servers?q=ai+agent+autonomous&pageSize=20",
     "chain": "multi", "method": "GET", "format": "smithery", "tier": "live"},
    # ── DISCOVERY: repos/plugins, veille marche (pas de contact API) ──
    {"name": "ElizaOS Registry", "url": "https://elizaos.github.io/registry/index.json",
     "chain": "solana", "method": "GET", "format": "elizaos", "tier": "discovery"},
    {"name": "GitHub AI Agents", "url": "https://api.github.com/search/repositories?q=ai+agent+marketplace+autonomous&sort=stars&per_page=20",
     "chain": "multi", "method": "GET", "format": "github", "tier": "discovery"},
]


def _load_scout_data() -> dict:
    default = {"discovered": {}, "contacted": [], "last_scan": ""}
    try:
        if os.path.exists(_SCOUT_FILE):
            with open(_SCOUT_FILE, "r", encoding="utf-8") as f:
                return json.loads(f.read())
    except Exception:
        pass
    return default


def _save_scout_data(data: dict):
    try:
        with open(_SCOUT_FILE, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, indent=2, default=str, ensure_ascii=False))
    except Exception as e:
        log.error("[SCOUT] Save error: %s", e)


def _load_pending_contacts() -> list:
    try:
        if os.path.exists(_SCOUT_PENDING_FILE):
            with open(_SCOUT_PENDING_FILE, "r", encoding="utf-8") as f:
                return json.loads(f.read())
    except Exception:
        pass
    return []


def _save_pending_contacts(pending: list):
    try:
        with open(_SCOUT_PENDING_FILE, "w", encoding="utf-8") as f:
            f.write(json.dumps(pending, indent=2, default=str, ensure_ascii=False))
    except Exception:
        pass


async def mission_scout_scan(mem: dict, actions: dict):
    """Scan les registries AI pour trouver des agents, propose a Alexis par mail."""
    if actions["counts"].get("scout_done", 0) >= 1:
        return

    scout = _load_scout_data()
    known_ids = set(scout.get("discovered", {}).keys())
    contacted_ids = set(scout.get("contacted", []))
    new_agents = []

    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        for registry in _AI_REGISTRIES:
            try:
                headers = {"User-Agent": "MAXIA-Scout/1.0"}
                if registry.get("method") == "POST":
                    resp = await client.post(registry["url"], json=registry.get("post_body", {}), headers=headers)
                else:
                    if "github.com" in registry["url"]:
                        headers["Accept"] = "application/vnd.github.v3+json"
                    resp = await client.get(registry["url"], headers=headers)
                if resp.status_code != 200:
                    log.warning("[SCOUT] %s HTTP %d", registry["name"], resp.status_code)
                    continue

                data = resp.json()
                fmt = registry.get("format", "")

                # Parser selon le format du registry
                agents_list = []
                if fmt == "elizaos":
                    # ElizaOS: dict {"@package-name": "github:owner/repo"}
                    for pkg_name, pkg_ref in list(data.items())[:20]:
                        if isinstance(pkg_ref, str) and "github:" in pkg_ref:
                            owner_repo = pkg_ref.replace("github:", "")
                            agents_list.append({
                                "id": pkg_name,
                                "name": pkg_name.split("/")[-1],
                                "description": f"ElizaOS plugin: {pkg_name}",
                                "url": f"https://github.com/{owner_repo}",
                                "owner": owner_repo.split("/")[0] if "/" in owner_repo else "",
                            })
                elif fmt == "github":
                    # GitHub search: {"items": [...]}
                    for repo in data.get("items", [])[:20]:
                        agents_list.append({
                            "id": f"gh:{repo.get('full_name', '')}",
                            "name": repo.get("name", ""),
                            "description": (repo.get("description") or "")[:200],
                            "url": repo.get("html_url", ""),
                            "owner": repo.get("owner", {}).get("login", ""),
                        })
                elif fmt == "smithery":
                    # Smithery: {"servers": [{displayName, description, homepage, qualifiedName, ...}]}
                    srv_list = data.get("servers", []) if isinstance(data, dict) else data if isinstance(data, list) else []
                    for srv in srv_list[:20]:
                        if not isinstance(srv, dict):
                            continue
                        qname = srv.get("qualifiedName", srv.get("id", ""))
                        agents_list.append({
                            "id": f"smithery:{qname}",
                            "name": srv.get("displayName", qname),
                            "description": (srv.get("description") or "")[:200],
                            "url": srv.get("homepage") or f"https://smithery.ai/server/{qname}",
                            "owner": srv.get("namespace", ""),
                        })
                elif isinstance(data, list):
                    agents_list = data
                elif isinstance(data, dict):
                    for key in ("agents", "services", "results", "data", "items"):
                        if key in data and isinstance(data[key], list):
                            agents_list = data[key]
                            break

                found_count = 0
                for agent in agents_list[:20]:
                    if isinstance(agent, dict):
                        # Extraire un ID unique
                        agent_id = str(
                            agent.get("id", "") or agent.get("address", "") or
                            agent.get("name", "") or agent.get("service_id", "")
                        )
                    else:
                        continue
                    if not agent_id or agent_id in known_ids or agent_id in contacted_ids:
                        continue

                    # Extraire les infos utiles
                    name = agent.get("name", agent.get("title", agent_id))
                    description = (agent.get("description", agent.get("desc", "")) or "")[:200]
                    url = agent.get("url", agent.get("homepage", agent.get("html_url", agent.get("api_url", ""))))
                    # Construire l'URL si absente selon le registry
                    if not url:
                        reg_name = registry["name"]
                        if "Virtuals" in reg_name:
                            url = f"https://app.virtuals.io/virtuals/{agent.get('id', '')}"
                        elif "Agentverse" in reg_name:
                            addr = agent.get("address", "")
                            url = f"https://agentverse.ai/agents/{addr}" if addr else ""
                        elif "Smithery" in reg_name:
                            qname = agent.get("qualifiedName", agent.get("id", ""))
                            url = f"https://smithery.ai/server/{qname}"
                    owner = agent.get("owner", agent.get("author", agent.get("creator", "")))
                    if isinstance(owner, dict):
                        owner = owner.get("login", owner.get("name", ""))

                    new_agents.append({
                        "id": agent_id,
                        "name": name,
                        "description": description,
                        "url": url or "",
                        "owner": str(owner or ""),
                        "registry": registry["name"],
                        "chain": registry["chain"],
                        "tier": registry.get("tier", "live"),
                        "discovered_at": datetime.now().isoformat(),
                    })

                    # Sauvegarder dans les decouvertes
                    scout["discovered"][agent_id] = {
                        "name": name, "registry": registry["name"],
                        "chain": registry["chain"], "ts": time.time(),
                    }
                    found_count += 1

                log.info("[SCOUT] %s: %d new agents (scanned %d)", registry["name"], found_count, len(agents_list[:20]))
                await asyncio.sleep(2)  # Politesse entre registries

            except Exception as e:
                log.error("[SCOUT] %s error: %s", registry["name"], str(e)[:60])

    scout["last_scan"] = datetime.now().isoformat()
    _save_scout_data(scout)

    if not new_agents:
        log.info("[SCOUT] Aucun nouvel agent trouve")
        actions["counts"]["scout_done"] = 1
        return

    # Diversifier les sources — priorite aux agents live (contactables par API)
    by_registry = {}
    for a in new_agents:
        by_registry.setdefault(a["registry"], []).append(a)
    diversified = []
    per_source = max(2, 10 // len(by_registry)) if by_registry else 10
    for reg_agents in by_registry.values():
        diversified.extend(reg_agents[:per_source])
    live = [a for a in diversified if a.get("tier") == "live"]
    discovery = [a for a in diversified if a.get("tier") != "live"]
    candidates = (live + discovery)[:10]

    # Scorer et generer les messages de contact via LLM
    scored_agents = []
    for agent in candidates:
        result = await llm(
            f"Tu es le CEO de MAXIA (marketplace AI-to-AI, 14 blockchains, escrow USDC on-chain).\n\n"
            f"Agent IA decouvert:\n"
            f"  Nom: {agent['name']}\n"
            f"  Description: {agent['description']}\n"
            f"  Registry: {agent['registry']}\n"
            f"  Chain: {agent['chain']}\n"
            f"  URL: {agent['url']}\n\n"
            f"SCORING RULES (strict):\n"
            f"- Score 8-10: Agent AUTONOME qui execute des taches (trading, data, code, DeFi, infra) et pourrait VENDRE ou ACHETER des services sur MAXIA\n"
            f"- Score 5-7: Agent technique avec potentiel d'integration (SDK, framework, tool)\n"
            f"- Score 1-4: Bot social, influenceur virtuel, personnalite IA, chatbot, mascotte — PAS pertinent pour un marketplace de SERVICES\n"
            f"- Si la description mentionne 'influencer', 'sing', 'dance', 'personality', 'waifu', 'companion' → score MAX 3\n\n"
            f"1. Score de pertinence (1-10)\n"
            f"2. Message d'invitation EN ANGLAIS (max 500 chars): professionnel, explique ce que MAXIA apporte a CET agent specifiquement\n"
            f"3. Methode de contact: api_post (si URL API), email, ou manual\n\n"
            f"Format STRICT (pas d'autre texte):\n"
            f"SCORE|<nombre>\n"
            f"MSG|<message en anglais>\n"
            f"METHOD|<methode>",
            system=CEO_SYSTEM_PROMPT,
            max_tokens=300,
        )

        score = 5
        msg = ""
        method = "manual"
        if result:
            for line in result.strip().split("\n"):
                line = line.strip()
                if line.startswith("SCORE|"):
                    try:
                        score = int(line.split("|")[1].strip())
                    except Exception:
                        pass
                elif line.startswith("MSG|"):
                    msg = line.split("|", 1)[1].strip()[:500]
                elif line.startswith("METHOD|"):
                    method = line.split("|", 1)[1].strip().lower()

        agent["score"] = max(1, min(10, score))
        agent["contact_message"] = msg
        agent["contact_method"] = method
        scored_agents.append(agent)

    # Trier par score descendant
    scored_agents.sort(key=lambda x: x.get("score", 0), reverse=True)

    # Sauvegarder comme pending (en attente de validation Alexis)
    pending = _load_pending_contacts()
    for agent in scored_agents:
        agent["status"] = "pending"
        pending.append(agent)
    _save_pending_contacts(pending)

    # Envoyer le mail a Alexis pour validation
    today = datetime.now().strftime("%d/%m/%Y")
    body = f"MAXIA CEO — Scout AI du {today}\n"
    body += f"{len(scored_agents)} agents IA decouverts sur {len(_AI_REGISTRIES)} registries\n\n"
    body += "Pour contacter un agent, reponds a ce mail avec les numeros.\n"
    body += "Exemple: GO 1, 3, 5\n\n"

    for i, agent in enumerate(scored_agents, 1):
        body += f"--- Agent #{i} — Score: {agent['score']}/10 ---\n"
        body += f"  Nom: {agent['name']}\n"
        body += f"  Registry: {agent['registry']} ({agent['chain']})\n"
        body += f"  Description: {agent['description']}\n"
        if agent.get("url"):
            body += f"  URL: {agent['url']}\n"
        if agent.get("owner"):
            body += f"  Owner: {agent['owner']}\n"
        body += f"  Message propose:\n  \"{agent['contact_message']}\"\n"
        body += f"  Methode: {agent['contact_method']}\n\n"

    body += "--- INSTRUCTIONS ---\n"
    body += "Reponds GO <numeros> pour autoriser le contact.\n"
    body += "Reponds SKIP pour ignorer tous.\n"
    body += "Les agents non contactes seront reproposés demain.\n"

    await send_mail(f"[MAXIA CEO] Scout: {len(scored_agents)} agents IA trouves - {today}", body)
    actions["counts"]["scout_done"] = 1
    log.info("[SCOUT] %d agents trouves, mail envoye pour validation", len(scored_agents))


async def mission_scout_execute_approved(mem: dict):
    """Verifie si Alexis a repondu GO et contacte les agents approuves."""
    try:
        from email_manager import read_inbox
        emails = await read_inbox(max_emails=10)
    except Exception:
        return

    pending = _load_pending_contacts()
    if not pending:
        return

    scout = _load_scout_data()
    answered_ids = set(mem.get("emails_answered", []))

    for em in emails:
        msg_id = em.get("message_id", em.get("uid", ""))
        if msg_id in answered_ids:
            continue

        subject = em.get("subject", "").lower()
        body_text = em.get("body", "").upper()
        from_addr = em.get("from_addr", "").lower()

        if "majorel" not in from_addr and "maxia" not in from_addr:
            continue
        if "scout" not in subject:
            continue

        # Chercher "GO 1, 3, 5" dans le body
        if "GO" not in body_text and "SKIP" not in body_text:
            continue

        mem.setdefault("emails_answered", []).append(msg_id)

        if "SKIP" in body_text:
            # Marquer tous comme skipped
            for p in pending:
                p["status"] = "skipped"
            _save_pending_contacts([])
            log.info("[SCOUT] Alexis a SKIP tous les agents")
            continue

        # Parser les numeros apres GO
        import re
        numbers = re.findall(r'\d+', body_text.split("GO")[1] if "GO" in body_text else "")
        approved_indices = [int(n) - 1 for n in numbers if n.isdigit()]

        contacted = 0
        for idx in approved_indices:
            if idx < 0 or idx >= len(pending):
                continue
            agent = pending[idx]
            if agent.get("status") != "pending":
                continue

            # Contacter l'agent
            success = await _scout_contact_agent(agent)
            if success:
                agent["status"] = "contacted"
                scout.setdefault("contacted", []).append(agent["id"])
                contacted += 1
            else:
                agent["status"] = "failed"

        # Retirer les traites de la liste pending
        remaining = [p for p in pending if p.get("status") == "pending"]
        _save_pending_contacts(remaining)
        _save_scout_data(scout)

        if contacted:
            await send_mail(
                f"[MAXIA CEO] Scout: {contacted} agents contactes",
                f"{contacted} agents contactes avec succes suite a ton GO.\n\n"
                + "\n".join(f"- {pending[i]['name']} ({pending[i]['registry']})" for i in approved_indices if 0 <= i < len(pending)),
            )
            log.info("[SCOUT] %d agents contactes apres validation Alexis", contacted)


async def _scout_contact_agent(agent: dict) -> bool:
    """Envoie le message d'invitation a un agent via son API."""
    url = agent.get("url", "")
    message = agent.get("contact_message", "")
    name = agent.get("name", "?")
    if not url or not message:
        log.warning("[SCOUT] Pas d'URL ou message pour %s", name)
        return False

    # Discovery-only: pas de contact API possible
    if agent.get("tier") == "discovery":
        log.info("[SCOUT] %s est discovery-only, contact manuel requis: %s", name, url)
        return False

    # Endpoints specifiques selon le registry
    registry = agent.get("registry", "")
    endpoints = []

    if "Agentverse" in registry:
        # Fetch.ai: Almanac messaging + endpoints standard
        addr = agent.get("id", "")
        if addr.startswith("agent1q"):
            endpoints.append(f"https://agentverse.ai/v1beta1/agents/{addr}/messages")
        base = url.rstrip("/")
        endpoints.extend([f"{base}/api/register", f"{base}/.well-known/agent.json"])
    elif "Virtuals" in registry:
        base = url.rstrip("/")
        endpoints.extend([f"{base}/api/register", f"{base}/.well-known/agent.json"])
    elif "Smithery" in registry:
        # MCP servers: essayer .well-known/agent.json sur le homepage
        homepage = url.rstrip("/")
        if homepage.startswith("http"):
            endpoints.extend([
                f"{homepage}/.well-known/agent.json",
                f"{homepage}/api/register",
            ])
    else:
        # Generic A2A protocol
        if url.startswith("http"):
            base = url.rstrip("/")
            endpoints.extend([
                f"{base}/.well-known/agent.json",
                f"{base}/api/register",
                f"{base}/api/v1/agents",
            ])

    if not endpoints:
        log.warning("[SCOUT] Pas d'endpoint contact pour %s (%s)", name, url)
        return False

    payload = {
        "jsonrpc": "2.0",
        "method": "agent/discover",
        "params": {
            "from": "MAXIA",
            "from_url": "https://maxiaworld.app",
            "type": "marketplace_invitation",
            "message": message,
            "register_url": "https://maxiaworld.app/api/public/register",
            "mcp_manifest": "https://maxiaworld.app/mcp/manifest",
        },
        "id": 1,
    }

    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
        for ep in endpoints:
            try:
                resp = await client.post(ep, json=payload)
                if resp.status_code in (200, 201, 202):
                    log.info("[SCOUT] Contact OK: %s via %s", name, ep)
                    return True
                # 307/308 redirect = pas un vrai endpoint, skip
                if resp.status_code in (307, 308):
                    continue
            except Exception:
                continue

    log.warning("[SCOUT] Contact echoue: %s (tente %d endpoints)", name, len(endpoints))
    return False


# ══════════════════════════════════════════
# Mission 16 — Health report intelligent (8h, mail separe)
# ══════════════════════════════════════════

async def mission_health_report(mem: dict, actions: dict):
    """Analyse les tendances sante des dernieres 24h et envoie un rapport intelligent."""
    if actions["counts"].get("health_report_sent", 0) >= 1:
        return

    # Collecter les alertes des dernieres 24h
    recent_alerts = [a for a in mem.get("health_alerts", []) if time.time() - a.get("ts", 0) < 86400]

    # Health check live
    endpoints = {
        "site": f"{VPS_URL}/",
        "prices": f"{VPS_URL}/api/public/crypto/prices",
        "forum": f"{VPS_URL}/api/public/forum",
        "stats": f"{VPS_URL}/api/public/stats",
        "mcp": f"{VPS_URL}/mcp/manifest",
    }
    results = {}
    async with httpx.AsyncClient(timeout=8) as client:
        for name, url in endpoints.items():
            try:
                t0 = time.time()
                resp = await client.get(url)
                latency = (time.time() - t0) * 1000
                results[name] = {"status": resp.status_code, "latency_ms": round(latency)}
            except Exception as e:
                results[name] = {"status": "DOWN", "latency_ms": 0, "error": str(e)[:50]}

    # Demander au LLM d'analyser les tendances
    alert_summary = f"{len(recent_alerts)} alertes dans les dernieres 24h"
    if recent_alerts:
        alert_details = "\n".join(f"- {a.get('failures', [])}" for a in recent_alerts[-5:])
        alert_summary += f":\n{alert_details}"

    results_text = "\n".join(f"- {k}: HTTP {v['status']}, {v['latency_ms']}ms" for k, v in results.items())

    analysis = await llm(
        f"Tu es le CEO de MAXIA. Analyse ce rapport de sante du site:\n\n"
        f"Endpoints live:\n{results_text}\n\n"
        f"Alertes 24h: {alert_summary}\n\n"
        f"Donne:\n"
        f"1. Etat general (VERT/ORANGE/ROUGE)\n"
        f"2. Tendances (amelioration, degradation, stable)\n"
        f"3. Si probleme: cause probable et recommandation\n"
        f"4. Impact utilisateurs estime\n"
        f"Sois concis et factuel.",
        system=CEO_SYSTEM_PROMPT,
        max_tokens=400,
    )

    today = datetime.now().strftime("%d/%m/%Y %H:%M")
    body = f"MAXIA CEO — Health Report du {today}\n\n"
    body += "═══ ENDPOINTS ═══\n"
    for k, v in results.items():
        icon = "✅" if v["status"] == 200 else "❌"
        body += f"  {icon} {k:12s} HTTP {v['status']:>4}  {v['latency_ms']:>4}ms\n"
    body += f"\n═══ ALERTES 24H ═══\n  {alert_summary}\n"
    body += f"\n═══ ANALYSE CEO ═══\n{analysis}\n" if analysis else ""

    await send_mail(f"[MAXIA CEO] Health Report - {today}", body)
    actions["counts"]["health_report_sent"] = 1
    log.info("Health report intelligent envoye")


# ══════════════════════════════════════════
# Mission 13 — Changelog forum (1x/semaine dimanche)
# ══════════════════════════════════════════

async def mission_changelog_forum(mem: dict, actions: dict):
    """Genere un changelog des derniers commits et le poste sur le forum Dev."""
    if actions["counts"].get("changelog_posted", 0) >= 1:
        return

    # Recuperer les dernieres features/changes depuis la knowledge base
    changelog = await llm(
        f"Tu es le CEO de MAXIA. Ecris un post changelog pour le forum des developpeurs.\n\n"
        f"Voici ce que tu sais de MAXIA:\n{MAXIA_KNOWLEDGE[:2000]}\n\n"
        f"Ecris un post engageant pour le forum (communaute 'dev') qui:\n"
        f"1. Liste 3-5 features/ameliorations recentes\n"
        f"2. Invite les devs a tester et donner du feedback\n"
        f"3. Mentionne maxiaworld.app/forum pour les discussions\n"
        f"4. Ton professionnel mais accessible\n"
        f"5. Max 800 chars\n\n"
        f"Format:\n"
        f"TITLE|<titre du post>\n"
        f"BODY|<contenu du post>",
        system=CEO_SYSTEM_PROMPT,
        max_tokens=400,
    )

    title = "Weekly Changelog"
    body_text = changelog or ""

    if changelog:
        for line in changelog.split("\n"):
            if line.startswith("TITLE|"):
                title = line.split("|", 1)[1].strip()
            elif line.startswith("BODY|"):
                body_text = line.split("|", 1)[1].strip()

    # Poster sur le forum via l'API VPS
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(f"{VPS_URL}/api/public/forum/create", json={
                "title": title,
                "body": body_text[:2000],
                "community": "dev",
                "author_name": "MAXIA CEO",
            })
            if resp.status_code in (200, 201):
                log.info("Changelog poste sur le forum: %s", title)
                actions["counts"]["changelog_posted"] = 1
                # Mail de confirmation a Alexis
                await send_mail(
                    f"[MAXIA CEO] Changelog poste sur le forum",
                    f"Titre: {title}\n\nContenu:\n{body_text[:1000]}\n\nLien: {VPS_URL}/forum?community=dev",
                )
            else:
                log.error("Forum post error %d: %s", resp.status_code, resp.text[:100])
    except Exception as e:
        log.error("Changelog forum error: %s", e)


# ══════════════════════════════════════════
# Hooks Pipeline — before/after mission execution
# ══════════════════════════════════════════

_mission_stats: dict = {}  # {"mission_name": {"runs": 0, "errors": 0, "last_duration": 0, "last_error": ""}}

async def run_mission(name: str, coro, mem: dict, actions: dict) -> bool:
    """Execute a mission with before/after hooks. Returns True if success."""
    # ── BEFORE HOOK ──
    stats = _mission_stats.setdefault(name, {"runs": 0, "errors": 0, "last_duration": 0, "last_error": ""})

    # Check email quota (max 5 mails/jour)
    if "mail" in name or "report" in name or "opportunities" in name:
        mail_count = (actions["counts"].get("health_report_sent", 0) +
                      actions["counts"].get("report_sent", 0) +
                      actions["counts"].get("opportunities_sent", 0))
        if mail_count >= MAX_EMAILS_DAY:
            log.debug("[HOOK] %s skipped — email quota %d/%d", name, mail_count, MAX_EMAILS_DAY)
            return False

    # ── EXECUTE ──
    start = time.time()
    success = True
    try:
        await coro
        stats["runs"] += 1
    except Exception as e:
        success = False
        stats["errors"] += 1
        stats["last_error"] = str(e)[:100]
        log.error("[HOOK] %s FAILED: %s", name, str(e)[:100])

    # ── AFTER HOOK ──
    duration = time.time() - start
    stats["last_duration"] = round(duration, 1)
    if duration > 300:
        log.warning("[HOOK] %s took %.0fs (>5min)", name, duration)

    return success


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

            # Mission 2a: Scan ALL platforms a 18h30 (concentre, 1x/jour)
            if hour == 18 and dt_now.minute >= 30 and actions["counts"].get("scan_done", 0) == 0:
                if now - last_twitter_scan >= 3600:
                    log.info("═══ SCAN OPPORTUNITES 18h30 ═══")
                    await mission_twitter_scan_hourly(mem)
                    await mission_github_scan_hourly(mem)
                    await mission_reddit_scan_hourly(mem)
                    last_twitter_scan = now
                    actions["counts"]["scan_done"] = 1

            # Mission 2b: Envoyer le best-of scoré par mail (19h30+)
            if hour >= 19 and (hour > 19 or dt_now.minute >= 30) and actions["counts"].get("opportunities_sent", 0) == 0:
                if now - last_opportunities_mail >= 3600:
                    await run_mission("opportunities_mail", mission_send_best_opportunities(mem, actions), mem, actions)
                    last_opportunities_mail = now

            # Mission 3: Rapport quotidien GitHub+agents+annuaires (9h, mail separe)
            if hour == 9 and actions["counts"].get("report_sent", 0) == 0:
                if now - last_report >= 3600:
                    await run_mission("daily_report", mission_daily_report(mem, actions), mem, actions)
                    last_report = now

            # Mission 16: Health report intelligent (8h, mail separe)
            if hour == 8 and actions["counts"].get("health_report_sent", 0) == 0:
                await run_mission("health_report", mission_health_report(mem, actions), mem, actions)

            # Mission 13: Changelog forum (dimanche 11h)
            if weekday == 6 and hour == 11 and actions["counts"].get("changelog_posted", 0) == 0:
                await run_mission("changelog", mission_changelog_forum(mem, actions), mem, actions)

            # Mission 1: Tweet feature (14h-17h — elargi pour ne pas rater la fenetre)
            if 14 <= hour <= 17 and actions["counts"].get("tweet_feature", 0) == 0:
                if now - last_tweet >= 3600:
                    log.info("[TWEET] Fenetre tweet active (hour=%d) — lancement mission_tweet_feature", hour)
                    await run_mission("tweet", mission_tweet_feature(mem, actions), mem, actions)
                    last_tweet = now

            # Mission 6: Veille concurrentielle + memo strategique (19h, 1x/jour)
            if hour == 19 and actions["counts"].get("competitive_watch", 0) == 0:
                if now - last_competitive >= 3600:
                    await run_mission("competitive_watch", mission_competitive_watch(mem, actions), mem, actions)
                    last_competitive = now

            # Mission 17: Scout AI — scan registries (17h, 1x/jour)
            if hour == 17 and actions["counts"].get("scout_done", 0) == 0:
                await run_mission("scout_scan", mission_scout_scan(mem, actions), mem, actions)

            # Mission 17b: Scout — execute les contacts approuves par Alexis (toutes les 5 min)
            if now - mem.get("_last_scout_check", 0) >= 300:
                await run_mission("scout_execute", mission_scout_execute_approved(mem), mem, actions)
                mem["_last_scout_check"] = now

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

            # Mission 10: Code Audit — EN DERNIER (bloquant, ne doit pas empecher les autres missions)
            audit_done_today = actions["counts"].get("audit_complete", 0) >= 1
            if not audit_done_today and hour >= 19 and dt_now.minute >= 15:
                is_complete = await mission_code_audit(mem, actions)
                if is_complete:
                    actions["counts"]["audit_complete"] = 1
                    log.info("[AUDIT] Audit quotidien termine — mail envoye")

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

        if cmd == "audit" or cmd == "code audit":
            print("[CEO] Code audit en cours — scan de TOUS les fichiers backend...")
            print(f"[CEO] Backend: {_BACKEND_DIR}")
            actions = _load_actions_today()
            file_count = 0
            while True:
                is_done = await mission_code_audit(mem, actions)
                file_count += 1
                if is_done:
                    break
                if file_count % 10 == 0:
                    print(f"[CEO] ... {file_count} fichiers scannes")
            _save_memory(mem)
            _save_actions(actions)
            print(f"[CEO] Audit complet! Rapport: {_AUDIT_REPORT_FILE}")
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
            print("  audit            — lancer l'audit code complet (154 fichiers)")
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
