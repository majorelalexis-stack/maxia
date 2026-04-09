"""Missions 2a-2e — Scan Twitter, GitHub, Reddit, Discord + scoring + best-of mail.

Accumulates opportunities hourly, then sends a scored best-of email at 19h30.
"""
import asyncio
import logging
import random
import time
from datetime import datetime

import httpx

from config_local import VPS_URL, GITHUB_REPOS
from llm import llm
from agents import CEO_SYSTEM_PROMPT, GITHUB_KEYWORDS, REDDIT_SUBS, BLOCKED_ORGS
from scheduler import send_mail

log = logging.getLogger("ceo")


# ══════════════════════════════════════════
# Mission 2a — Twitter scan hourly — DISABLED (Plan CEO V7, 2026-04-09)
# ══════════════════════════════════════════

async def mission_twitter_scan_hourly(mem: dict) -> None:
    """DISABLED — no-op stub. Twitter removed from MAXIA (V7).

    Kept as a stub so ceo_main.py imports do not break. Outreach now
    flows through email (VPS /api/marketing/email) and Discord bot
    MAXIA outreach in MAXIA Community, governed by the V7 compliance
    filter (28 allowed countries, OFAC blocked list).
    """
    log.debug("[opps] twitter scan disabled (Plan CEO V7)")
    return


# ══════════════════════════════════════════
# Scoring helper
# ══════════════════════════════════════════

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
        f"2. Ecris 3 reponses DIFFERENTES (max 240 chars CHACUNE, DOIT inclure maxiaworld.app):\n"
        f"   - V1 TECHNIQUE: reponse de dev expert, factuelle, avec lien maxiaworld.app\n"
        f"   - V2 CASUAL: ton decontracte, amical, avec lien maxiaworld.app\n"
        f"   - V3 VALUE: apporter de la valeur concrete, avec lien maxiaworld.app\n"
        f"3. Invitation forum (1 phrase, max 200 chars): invite cette personne a poster sur maxiaworld.app/forum\n\n"
        f"Format STRICT (pas d'autre texte):\n"
        f"SCORE|<nombre>\n"
        f"V1|<reponse technique max 240 chars avec lien maxiaworld.app>\n"
        f"V2|<reponse casual max 240 chars avec lien maxiaworld.app>\n"
        f"V3|<reponse value max 240 chars avec lien maxiaworld.app>\n"
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
                variants.append(line.split("|", 1)[1].strip()[:240])
            elif line.startswith("INVITE|"):
                invite = line.split("|", 1)[1].strip()[:200]

    opp["score"] = max(1, min(10, score))
    opp["variants"] = variants if variants else [opp.get("suggested_comment", "")[:280]]
    opp["forum_invite"] = invite
    return opp


# ══════════════════════════════════════════
# Mission 2b — Send best-of scored opportunities
# ══════════════════════════════════════════

async def mission_send_best_opportunities(mem: dict, actions: dict) -> None:
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
            label = ["TECHNIQUE", "CASUAL", "VALUE"][j - 1] if j <= 3 else f"V{j}"
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
# Mission 2c — GitHub issues/discussions scan (hourly)
# ══════════════════════════════════════════

async def mission_github_scan_hourly(mem: dict) -> None:
    """Scan horaire GitHub — cherche des issues/discussions pertinentes."""
    repos_to_scan = [r for r in GITHUB_REPOS if not any(blocked in r for blocked in BLOCKED_ORGS)]
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
                relevant = any(kw.lower() in content for kw in GITHUB_KEYWORDS)
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
                        if any(kw.lower() in title.lower() for kw in GITHUB_KEYWORDS):
                            comment = await llm(
                                f"A developer started this GitHub discussion:\n"
                                f"Repo: {repo}\nTitle: {title}\n\n"
                                f"Write a helpful reply (max 200 chars).",
                                system=CEO_SYSTEM_PROMPT,
                                max_tokens=80,
                            )
                            today_gh.append({
                                "id": disc_id, "repo": repo, "title": title[:150],
                                "url": disc.get("html_url", ""),
                                "author": disc.get("user", {}).get("login", ""),
                                "suggested_comment": comment[:200] if comment else "",
                                "type": "discussion", "ts": time.time(),
                            })
                            found += 1
            except Exception:
                pass  # Discussions API not available for all repos

    except Exception as e:
        log.error("GitHub scan error: %s", e)

    log.info("GitHub scan [%s]: %d opportunities (total today: %d)",
             repo, found, len(mem.get("todays_github_opportunities", [])))


# ══════════════════════════════════════════
# Mission 2d — Reddit scan (hourly)
# ══════════════════════════════════════════

async def mission_reddit_scan_hourly(mem: dict) -> None:
    """Scan Reddit — cherche des posts pertinents pour MAXIA."""
    sub = random.choice(REDDIT_SUBS)
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

                relevant = any(kw.lower() in content for kw in GITHUB_KEYWORDS)
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

    log.info("Reddit scan [r/%s]: %d opportunities (total today: %d)",
             sub, found, len(mem.get("todays_reddit_opportunities", [])))


# ══════════════════════════════════════════
# Mission 2e — Discord scan (disabled)
# ══════════════════════════════════════════

async def mission_discord_scan_hourly(mem: dict) -> None:
    """Discord — pas d'API publique. Utilise une liste statique de serveurs pertinents."""
    # Disboard bloque les bots (403). On maintient une liste statique.
    # Le CEO inclura ces serveurs dans le rapport hebdomadaire pour qu'Alexis les rejoigne manuellement.
    pass  # Scan actif desactive — serveurs listes dans le rapport concurrentiel
