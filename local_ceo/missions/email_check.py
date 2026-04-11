"""Missions 9 & 13 — Check Alexis emails + Weekly changelog forum post.

Mission 9: Reads ceo@maxiaworld.app, replies to Alexis emails via LLM.
Mission 13: Posts a weekly changelog to the forum (Sunday 11h).
"""
import logging
from datetime import datetime

import httpx

from config_local import VPS_URL
from llm import llm
from agents import CEO_SYSTEM_PROMPT, MAXIA_KNOWLEDGE
from scheduler import send_mail

log = logging.getLogger("ceo")


# ══════════════════════════════════════════
# Mission 9 — Check emails de Alexis
# ══════════════════════════════════════════

async def mission_check_alexis_emails(mem: dict) -> None:
    """Lit ceo@maxiaworld.app, si Alexis a envoye un mail -> repond avec le LLM."""
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
# Mission 13 — Changelog forum (1x/semaine dimanche)
# ══════════════════════════════════════════

async def mission_changelog_forum(mem: dict, actions: dict) -> None:
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
