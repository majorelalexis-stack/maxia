"""Mission 4 — Forum moderation.

Scans new forum posts every hour, detects spam, alerts Alexis by email.
"""
import logging
import time

import httpx

from config_local import VPS_URL
from scheduler import send_mail

log = logging.getLogger("ceo")


async def mission_moderate_forum(mem: dict) -> None:
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
            spam_words = [
                "buy followers", "free money", "send me", "dm me",
                "click here", "airdrop claim",
            ]
            if any(w in content for w in spam_words):
                is_spam = True
                reasons.append("mots spam detectes")
            if len(body) > 0 and len(set(body.split())) < 5 and len(body) > 50:
                is_spam = True
                reasons.append("contenu repetitif")

            mem["moderation_log"].append({
                "id": pid, "title": title[:50], "spam": is_spam, "ts": time.time(),
            })

            if is_spam:
                suspicious.append({
                    "id": pid, "title": title,
                    "author": post.get("author_name", ""), "reasons": reasons,
                })

        if suspicious:
            body_text = "Posts suspects detectes sur le forum:\n\n"
            for s in suspicious:
                body_text += f"- \"{s['title']}\" par {s['author']}\n"
                body_text += f"  Raisons: {', '.join(s['reasons'])}\n"
                body_text += f"  ID: {s['id']}\n\n"
            body_text += "Action: verifier et supprimer si necessaire via /api/admin/forum/ban"
            await send_mail("[MAXIA CEO] Moderation forum - posts suspects", body_text)
            log.warning("Posts suspects: %d", len(suspicious))
        else:
            log.info("Moderation: %d posts verifies, aucun suspect", len(posts))

    except Exception as e:
        log.error("Moderation error: %s", e)
