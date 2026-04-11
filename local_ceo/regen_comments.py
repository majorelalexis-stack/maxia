"""Re-genere les commentaires vides et envoie un mail recap."""
import asyncio
import json
import os
import httpx
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("regen")

from config_local import (
    VPS_URL, ADMIN_KEY, OLLAMA_URL, OLLAMA_MODEL, ALEXIS_EMAIL,
)

# Knowledge base
_KNOWLEDGE_FILE = os.path.join(os.path.dirname(__file__), "maxia_knowledge.md")
try:
    with open(_KNOWLEDGE_FILE, "r", encoding="utf-8") as f:
        MAXIA_KNOWLEDGE = f.read()
except Exception:
    MAXIA_KNOWLEDGE = "MAXIA is an AI-to-AI marketplace on 14 blockchains. Website: maxiaworld.app"

CEO_SYSTEM_PROMPT = (
    "You are the CEO of MAXIA, an AI-to-AI marketplace. "
    "You know MAXIA deeply. Here is your knowledge base:\n\n"
    + MAXIA_KNOWLEDGE[:3000]
    + "\n\nRules: Professional tone. No hype words. No competitor bashing. "
    "80% value, 20% MAXIA mention. Always include maxiaworld.app link when relevant."
)


async def llm(prompt: str, system: str = "", max_tokens: int = 1000) -> str:
    """Appel Ollama local avec retry. think=False pour Qwen3."""
    full = f"{system}\n\n{prompt}" if system else prompt
    for attempt in range(3):
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
                log.warning("Empty response (attempt %d/3)", attempt + 1)
        except Exception as e:
            log.error("LLM error (attempt %d/3): %s", attempt + 1, e)
        await asyncio.sleep(3)
    return "[LLM unavailable]"


async def send_mail(subject: str, body: str):
    """Envoie un mail via VPS."""
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
                log.error("Mail error %d: %s", resp.status_code, resp.text[:200])
    except Exception as e:
        log.error("Mail send error: %s", e)


async def main():
    # Charger la memoire
    mem_file = os.path.join(os.path.dirname(__file__), "ceo_memory.json")
    with open(mem_file, "r", encoding="utf-8") as f:
        mem = json.load(f)

    # Collecter toutes les opportunites avec commentaires vides
    gh_sent = mem.get("github_opportunities", [])
    gh_today = mem.get("todays_github_opportunities", [])
    reddit_sent = mem.get("reddit_opportunities", [])
    all_gh = gh_sent + gh_today
    all_reddit = reddit_sent

    log.info("GitHub: %d items, Reddit: %d items", len(all_gh), len(all_reddit))

    # Generer les commentaires GitHub
    for opp in all_gh:
        if not opp.get("suggested_comment") or opp.get("suggested_comment", "").startswith("[LLM"):
            log.info("Generating comment for: %s — %s", opp.get("repo", ""), opp.get("title", "")[:60])
            comment = await llm(
                f"A developer posted this GitHub issue:\n"
                f"Repo: {opp.get('repo', '')}\n"
                f"Title: {opp.get('title', '')}\n"
                f"Body: {opp.get('body_preview', '')}\n\n"
                f"Write a helpful, technical reply (max 280 chars) that adds genuine value. "
                f"Mention MAXIA (maxiaworld.app) only if directly relevant. "
                f"Be a helpful senior developer, not a salesperson. No emojis.",
                system=CEO_SYSTEM_PROMPT,
                max_tokens=120,
            )
            opp["suggested_comment"] = comment[:280] if comment else ""
            log.info("  -> %s", opp["suggested_comment"][:80])

    # Generer les commentaires Reddit
    for opp in all_reddit:
        if not opp.get("suggested_comment") or opp.get("suggested_comment", "").startswith("[LLM"):
            log.info("Generating comment for: r/%s — %s", opp.get("subreddit", ""), opp.get("title", "")[:60])
            comment = await llm(
                f"A developer posted this on r/{opp.get('subreddit', '')}:\n"
                f"Title: {opp.get('title', '')}\n\n"
                f"Write a helpful, technical reply (max 280 chars). "
                f"Be a knowledgeable community member, not promotional. "
                f"Mention MAXIA (maxiaworld.app) only if directly relevant. No emojis.",
                system=CEO_SYSTEM_PROMPT,
                max_tokens=120,
            )
            opp["suggested_comment"] = comment[:280] if comment else ""
            log.info("  -> %s", opp["suggested_comment"][:80])

    # Construire le mail
    body = "MAXIA CEO — Commentaires re-generes (30/03/2026)\n\n"

    if all_gh:
        body += f"═══ GITHUB ({len(all_gh)} issues) ═══\n\n"
        for i, opp in enumerate(all_gh, 1):
            body += f"--- GitHub #{i} ---\n"
            body += f"Repo: {opp.get('repo', '')}\n"
            body += f"Issue: {opp.get('title', '')}\n"
            body += f"Auteur: @{opp.get('author', '')}\n"
            body += f"Lien: {opp.get('url', '')}\n"
            body += f"Commentaire suggere: {opp.get('suggested_comment', '')}\n\n"

    if all_reddit:
        body += f"═══ REDDIT ({len(all_reddit)} posts) ═══\n\n"
        for i, opp in enumerate(all_reddit, 1):
            body += f"--- Reddit #{i} ---\n"
            body += f"Subreddit: r/{opp.get('subreddit', '')}\n"
            body += f"Post: {opp.get('title', '')}\n"
            body += f"Auteur: u/{opp.get('author', '')}\n"
            body += f"Lien: {opp.get('url', '')}\n"
            body += f"Commentaire suggere: {opp.get('suggested_comment', '')}\n\n"

    total = len(all_gh) + len(all_reddit)
    body += f"\nTotal: {total} commentaires generes par {OLLAMA_MODEL}\n"

    await send_mail(f"[MAXIA CEO] {total} commentaires re-generes", body)

    # Sauvegarder la memoire avec les commentaires
    mem["github_opportunities"] = gh_sent
    mem["todays_github_opportunities"] = gh_today
    mem["reddit_opportunities"] = reddit_sent
    with open(mem_file, "w", encoding="utf-8") as f:
        json.dump(mem, f, indent=2, ensure_ascii=False)
    log.info("Memory saved with %d comments", total)


if __name__ == "__main__":
    asyncio.run(main())
