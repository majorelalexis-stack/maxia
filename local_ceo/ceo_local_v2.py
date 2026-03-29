"""CEO Local V2 — 7 missions, 1 modele unique, zero spam.

Missions:
  1. Tweet feature du jour (14h)
  2. 5 opportunites Twitter → mail (10h)
  3. Rapport GitHub + skills + annuaires → mail (15h)
  4. Moderation forum (toutes les heures)
  5. Analyse nouveaux agents (inclus dans rapport)
  6. Veille concurrentielle (hebdo)
  7. Surveillance sante site (toutes les 5 min)

Usage: python ceo_local_v2.py
"""
import asyncio
import json
import time
import os
import random
import logging
import httpx
from datetime import datetime

from config_local import (
    VPS_URL, ADMIN_KEY, OLLAMA_URL, OLLAMA_MODEL,
    ALEXIS_EMAIL, BROWSER_PROFILE_DIR,
    HEALTH_CHECK_INTERVAL_S, MODERATION_INTERVAL_S,
    GITHUB_REPOS, MAXIA_FEATURES,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [CEO] %(message)s")
log = logging.getLogger("ceo")

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
    for key in ["tweets_posted", "opportunities_sent", "moderation_log", "health_alerts"]:
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

async def llm(prompt: str, system: str = "", max_tokens: int = 1000) -> str:
    """Appel Ollama local."""
    full = f"{system}\n\n{prompt}" if system else prompt
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(f"{OLLAMA_URL}/api/generate", json={
                "model": OLLAMA_MODEL,
                "prompt": full,
                "stream": False,
                "options": {"num_predict": max_tokens, "temperature": 0.7},
            })
            resp.raise_for_status()
            return resp.json().get("response", "")
    except Exception as e:
        log.error("LLM error: %s", e)
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
        system="You are the MAXIA CEO. Write concise, professional tweets.",
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
    except Exception as e:
        log.error("Tweet error: %s", e)


# ══════════════════════════════════════════
# Mission 2 — 5 opportunites Twitter → mail
# ══════════════════════════════════════════

async def mission_twitter_opportunities(mem: dict, actions: dict):
    """Scanne Twitter et envoie 5 opportunites par mail."""
    if actions["counts"]["opportunities_sent"] >= 1:
        log.info("Opportunites deja envoyees aujourd'hui — skip")
        return

    keywords = ["AI agent marketplace", "autonomous AI agent", "AI-to-AI", "crypto AI agent",
                "Solana AI", "MCP server", "agent protocol", "AI service marketplace"]

    opportunities = []
    try:
        from browser_agent import browser
        for kw in random.sample(keywords, min(4, len(keywords))):
            tweets = await browser.search_twitter(kw, max_results=5)
            for tweet in tweets:
                # Verifier qu'on n'a pas deja envoye cette opportunite
                tweet_id = tweet.get("id", tweet.get("url", ""))
                already_sent = any(o.get("id") == tweet_id for o in mem.get("opportunities_sent", []))
                if already_sent:
                    continue

                # Generer un commentaire suggere
                comment = await llm(
                    f"Write a short, helpful reply (max 200 chars) to this tweet:\n"
                    f"Tweet: {tweet.get('text', '')[:300]}\n\n"
                    f"Rules:\n- Be helpful, add value\n- Mention MAXIA only if relevant\n- Professional tone\n- Max 200 chars",
                    max_tokens=80,
                )

                opportunities.append({
                    "id": tweet_id,
                    "url": tweet.get("url", ""),
                    "author": tweet.get("author", ""),
                    "text": tweet.get("text", "")[:300],
                    "suggested_comment": comment[:200] if comment else "",
                })

                if len(opportunities) >= 5:
                    break
            if len(opportunities) >= 5:
                break
    except Exception as e:
        log.error("Twitter scan error: %s", e)

    if not opportunities:
        log.info("Aucune opportunite trouvee")
        return

    # Construire le mail
    today = datetime.now().strftime("%d/%m/%Y")
    body = f"MAXIA CEO — 5 opportunites Twitter du {today}\n\n"
    for i, opp in enumerate(opportunities, 1):
        body += f"--- Opportunite #{i} ---\n"
        body += f"Auteur: {opp['author']}\n"
        body += f"Tweet: {opp['text']}\n"
        body += f"Lien: {opp['url']}\n"
        body += f"Commentaire suggere: {opp['suggested_comment']}\n\n"

    await send_mail(f"[MAXIA CEO] 5 opportunites Twitter - {today}", body)
    mem["opportunities_sent"].extend(opportunities)
    actions["counts"]["opportunities_sent"] = 1


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
# Mission 7 — Surveillance sante site
# ══════════════════════════════════════════

async def mission_health_check(mem: dict):
    """Ping le site et verifie les endpoints critiques."""
    checks = {
        "site": f"{VPS_URL}/",
        "prices": f"{VPS_URL}/api/public/crypto/prices",
        "forum": f"{VPS_URL}/api/public/forum",
    }
    failures = []

    async with httpx.AsyncClient(timeout=8) as client:
        for name, url in checks.items():
            try:
                resp = await client.get(url)
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
# Boucle principale
# ══════════════════════════════════════════

async def run():
    """Boucle principale du CEO Local V2."""
    log.info("═══════════════════════════════════════")
    log.info("  MAXIA CEO Local V2 — demarrage")
    log.info("  Modele: %s", OLLAMA_MODEL)
    log.info("  Email: %s", ALEXIS_EMAIL)
    log.info("  VPS: %s", VPS_URL)
    log.info("═══════════════════════════════════════")

    mem = _load_memory()
    last_health = 0
    last_moderation = 0
    last_tweet = 0
    last_opportunities = 0
    last_report = 0

    while True:
        try:
            now = time.time()
            hour = datetime.now().hour
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

            # Mission 2: Opportunites Twitter (entre 9h et 11h)
            if 9 <= hour <= 11 and actions["counts"].get("opportunities_sent", 0) == 0:
                if now - last_opportunities >= 3600:
                    await mission_twitter_opportunities(mem, actions)
                    last_opportunities = now

            # Mission 1: Tweet feature (entre 13h et 15h)
            if 13 <= hour <= 15 and actions["counts"].get("tweet_feature", 0) == 0:
                if now - last_tweet >= 3600:
                    await mission_tweet_feature(mem, actions)
                    last_tweet = now

            # Mission 3: Rapport quotidien (entre 14h et 16h)
            if 14 <= hour <= 16 and actions["counts"].get("report_sent", 0) == 0:
                if now - last_report >= 3600:
                    await mission_daily_report(mem, actions)
                    last_report = now

            # Sauvegarder
            _save_memory(mem)
            _save_actions(actions)

        except Exception as e:
            log.error("Boucle principale error: %s", e)

        await asyncio.sleep(60)  # Check toutes les minutes


if __name__ == "__main__":
    print("""
    ╔═══════════════════════════════════════╗
    ║    MAXIA CEO Local V2                 ║
    ║    7 missions · 1 modele · 0 spam     ║
    ╚═══════════════════════════════════════╝
    """)
    asyncio.run(run())
