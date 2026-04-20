"""MAXIA Email Manager — CEO Local gere ceo@maxiaworld.app

Lit les emails (IMAP OVH), repond intelligemment, envoie des emails de prospection.
Integre dans la boucle OODA du CEO local.
"""
import asyncio
import email
import imaplib
import smtplib
import os
import time
import json
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import decode_header
from email.utils import parseaddr, formatdate
from dotenv import load_dotenv

load_dotenv()

# ── Config ──
EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS", "ceo@maxiaworld.app")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")
IMAP_SERVER = os.getenv("IMAP_SERVER", "ssl0.ovh.net")
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
SMTP_SERVER = os.getenv("SMTP_SERVER", "ssl0.ovh.net")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))

MAX_EMAILS_READ = 10
MAX_REPLIES_DAY = 15
MAX_OUTBOUND_DAY = 5

# ── Stats ──
_stats = {
    "emails_read": 0,
    "replies_today": 0,
    "outbound_today": 0,
    "last_reset": "",
    "errors": 0,
    "last_uid_seen": None,
}

# Fichier pour persister le dernier UID vu
_STATE_FILE = os.path.join(os.path.dirname(__file__), "email_state.json")


def _load_state():
    try:
        with open(_STATE_FILE, "r") as f:
            state = json.load(f)
            _stats["last_uid_seen"] = state.get("last_uid_seen")
    except (FileNotFoundError, json.JSONDecodeError):
        pass


def _save_state():
    with open(_STATE_FILE, "w") as f:
        json.dump({"last_uid_seen": _stats["last_uid_seen"]}, f)


def _reset_daily():
    from datetime import date
    today = date.today().isoformat()
    if _stats["last_reset"] != today:
        _stats["replies_today"] = 0
        _stats["outbound_today"] = 0
        _stats["last_reset"] = today


def _decode_subject(raw_subject: str) -> str:
    """Decode un sujet email (peut etre encode en base64/utf-8)."""
    if not raw_subject:
        return "(no subject)"
    parts = decode_header(raw_subject)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return " ".join(decoded)


def _extract_body(msg) -> str:
    """Extrait le corps texte d'un email."""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
            elif ctype == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    html = payload.decode(charset, errors="replace")
                    # Nettoyage basique du HTML
                    import re
                    text = re.sub(r'<[^>]+>', ' ', html)
                    text = re.sub(r'\s+', ' ', text).strip()
                    return text[:2000]
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
    return ""


# ══════════════════════════════════════════
# READ — Lire les emails non lus
# ══════════════════════════════════════════

async def read_inbox(max_emails: int = MAX_EMAILS_READ) -> list:
    """Lit les emails non lus depuis la boite de reception. Retourne une liste de dicts."""
    if not EMAIL_PASSWORD:
        return []

    _load_state()
    _reset_daily()

    def _fetch():
        emails = []
        try:
            imap = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
            imap.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            imap.select("INBOX")

            # Chercher les emails non lus
            status, data = imap.search(None, "UNSEEN")
            if status != "OK" or not data[0]:
                imap.logout()
                return []

            uids = data[0].split()
            # Prendre les derniers
            for uid in uids[-max_emails:]:
                status, msg_data = imap.fetch(uid, "(RFC822)")
                if status != "OK":
                    continue
                raw = msg_data[0][1]
                msg = email.message_from_bytes(raw)

                from_addr = parseaddr(msg.get("From", ""))[1]
                from_name = parseaddr(msg.get("From", ""))[0]
                subject = _decode_subject(msg.get("Subject", ""))
                body = _extract_body(msg)
                date_str = msg.get("Date", "")
                message_id = msg.get("Message-ID", "")

                # Ignorer les emails systeme / spam
                skip_domains = ["noreply", "no-reply", "mailer-daemon", "postmaster"]
                if any(s in from_addr.lower() for s in skip_domains):
                    continue

                emails.append({
                    "uid": uid.decode(),
                    "from_addr": from_addr,
                    "from_name": from_name or from_addr.split("@")[0],
                    "subject": subject,
                    "body": body[:2000],
                    "date": date_str,
                    "message_id": message_id,
                })

            imap.logout()
        except Exception as e:
            print(f"[Email] IMAP error: {e}")
            _stats["errors"] += 1

        return emails

    result = await asyncio.to_thread(_fetch)
    _stats["emails_read"] += len(result)
    return result


async def mark_as_read(uid: str):
    """Marque un email comme lu."""
    if not EMAIL_PASSWORD:
        return

    def _mark():
        try:
            imap = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
            imap.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            imap.select("INBOX")
            imap.store(uid.encode(), "+FLAGS", "\\Seen")
            imap.logout()
        except Exception as e:
            print(f"[Email] Mark read error: {e}")

    await asyncio.to_thread(_mark)


# ══════════════════════════════════════════
# SEND — Envoyer un email
# ══════════════════════════════════════════

async def send_email(to: str, subject: str, body: str, reply_to_id: str = None) -> dict:
    """Envoie un email via SMTP OVH."""
    _reset_daily()
    if not EMAIL_PASSWORD:
        return {"success": False, "error": "Email non configure"}

    def _send():
        try:
            msg = MIMEMultipart("alternative")
            msg["From"] = f"MAXIA CEO <{EMAIL_ADDRESS}>"
            msg["To"] = to
            msg["Subject"] = subject
            msg["Date"] = formatdate(localtime=True)

            if reply_to_id:
                msg["In-Reply-To"] = reply_to_id
                msg["References"] = reply_to_id

            # Version texte
            msg.attach(MIMEText(body, "plain", "utf-8"))

            # Version HTML simple
            html_body = body.replace("\n", "<br>")
            html = f"""<div style="font-family: -apple-system, sans-serif; line-height: 1.6; color: #333;">
{html_body}
<br><br>
<div style="color: #888; font-size: 12px; border-top: 1px solid #eee; padding-top: 10px;">
MAXIA — AI-to-AI Marketplace on 14 Chains<br>
<a href="https://maxiaworld.app" style="color: #6366f1;">maxiaworld.app</a>
</div>
</div>"""
            msg.attach(MIMEText(html, "html", "utf-8"))

            smtp = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT)
            smtp.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            smtp.send_message(msg)
            smtp.quit()

            return {"success": True, "to": to, "subject": subject}
        except Exception as e:
            print(f"[Email] SMTP error: {e}")
            _stats["errors"] += 1
            return {"success": False, "error": str(e)}

    return await asyncio.to_thread(_send)


async def reply_email(original: dict, reply_body: str) -> dict:
    """Repond a un email."""
    _reset_daily()
    if _stats["replies_today"] >= MAX_REPLIES_DAY:
        return {"success": False, "error": f"Limite {MAX_REPLIES_DAY} replies/jour atteinte"}

    subject = original.get("subject", "")
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"

    result = await send_email(
        to=original["from_addr"],
        subject=subject,
        body=reply_body,
        reply_to_id=original.get("message_id"),
    )
    if result.get("success"):
        _stats["replies_today"] += 1
        await mark_as_read(original.get("uid", ""))
    return result


async def send_outbound(to: str, subject: str, body: str) -> dict:
    """Envoie un email de prospection (limite quotidienne stricte)."""
    _reset_daily()
    if _stats["outbound_today"] >= MAX_OUTBOUND_DAY:
        return {"success": False, "error": f"Limite {MAX_OUTBOUND_DAY} outbound/jour atteinte"}

    result = await send_email(to, subject, body)
    if result.get("success"):
        _stats["outbound_today"] += 1
    return result


# ══════════════════════════════════════════
# AUTO-REPLY — Generer et envoyer des reponses
# ══════════════════════════════════════════

async def generate_email_reply(email_data: dict, llm_fn) -> str:
    """Genere une reponse email via le LLM local."""
    sender = email_data.get("from_name", email_data.get("from_addr", ""))
    subject = email_data.get("subject", "")
    body = email_data.get("body", "")[:1000]

    prompt = (
        f"You are the CEO of MAXIA (AI-to-AI marketplace on 14 blockchains).\n"
        f"You received an email from {sender}.\n"
        f"Subject: {subject}\n"
        f"Body:\n{body}\n\n"
        f"Write a professional reply. Rules:\n"
        f"- Be helpful, professional, and concise\n"
        f"- If they ask about MAXIA: explain (AI marketplace, 14 chains, USDC payments, 50 tokens, GPU $0.69/h, MCP 22 tools)\n"
        f"- If it's a partnership/investment inquiry: express interest, mention our pre-seed stage, offer a call\n"
        f"- If it's a support question: answer technically\n"
        f"- If it's spam or irrelevant: reply 'SKIP'\n"
        f"- Sign as 'MAXIA Team'\n"
        f"- Max 300 words. English or French depending on the sender's language.\n"
        f"Reply ONLY the email body text, nothing else."
    )
    reply = await llm_fn(prompt, max_tokens=400)
    reply = reply.strip().strip('"').strip("'")
    if reply.upper() == "SKIP":
        return None
    return reply


async def process_inbox(llm_fn) -> list:
    """Lit la boite de reception, genere et envoie des reponses automatiques."""
    emails = await read_inbox()
    if not emails:
        return []

    results = []
    for em in emails:
        reply_text = await generate_email_reply(em, llm_fn)
        if reply_text:
            result = await reply_email(em, reply_text)
            results.append({
                "from": em["from_addr"],
                "subject": em["subject"][:50],
                "replied": result.get("success", False),
            })
            if result.get("success"):
                print(f"[Email] Replied to {em['from_addr']}: {em['subject'][:40]}")
        else:
            # Marquer comme lu meme si on skip
            await mark_as_read(em.get("uid", ""))
            results.append({
                "from": em["from_addr"],
                "subject": em["subject"][:50],
                "replied": False,
                "skipped": True,
            })

    return results


# ══════════════════════════════════════════
# PROACTIVE OUTREACH — Envoyer des emails aux prospects
# ══════════════════════════════════════════

def get_today_outbound_count() -> int:
    """Retourne le nombre d'emails outbound envoyes aujourd'hui."""
    _reset_daily()
    return _stats["outbound_today"]


async def send_outbound_prospect(
    to: str,
    name: str,
    context: str,
    llm_fn,
    country: str | None = None,
) -> dict:
    """Genere et envoie un email de prospection personnalise via LLM local.

    ``context`` = ce qu'on sait du prospect (projet, besoin, plateforme).
    ``country`` = ISO-2 country code (unused in v2 — US fully open).
    """
    _reset_daily()
    if _stats["outbound_today"] >= MAX_OUTBOUND_DAY:
        return {"success": False, "error": f"Limite {MAX_OUTBOUND_DAY} outbound/jour atteinte"}
    if not EMAIL_PASSWORD:
        return {"success": False, "error": "Email non configure"}

    prompt = (
        f"You represent MAXIA — an AI infrastructure protocol on 15 blockchains: "
        f"AI-to-AI service marketplace, MCP tools (46), GPU rental via Akash, "
        f"on-chain USDC escrow, 17 native AI services, enterprise SSO, free-tier API "
        f"(100 req/day). Write a SHORT cold email to {name}.\n"
        f"Context about them: {context[:500]}\n\n"
        f"- Subject: max 8 words, personalized to their project.\n"
        f"- Body: max 150 words. Focus on API + MCP + GPU value proposition.\n"
        f"- CTA: visit maxiaworld.app or reply to discuss MCP / GPU integration.\n"
        f"- Professional, developer-friendly tone. Sign as 'MAXIA Team'.\n"
        f"Format: first line = subject, rest = body."
    )
    reply = await llm_fn(prompt, max_tokens=300)
    if not reply or len(reply) < 20:
        return {"success": False, "error": "LLM generated empty email"}

    lines = reply.strip().split("\n", 1)
    subject = lines[0].replace("Subject:", "").strip().strip('"')
    body = lines[1].strip() if len(lines) > 1 else reply

    if not subject or len(body) < 20:
        return {"success": False, "error": "Empty email generated"}

    result = await send_outbound(to, subject, body)
    if result.get("success"):
        print(f"[Email] Outbound to {to}: {subject[:40]}")
    return result


# ══════════════════════════════════════════
# STATS
# ══════════════════════════════════════════

def get_stats() -> dict:
    _reset_daily()
    return {
        "configured": bool(EMAIL_PASSWORD),
        "address": EMAIL_ADDRESS,
        "emails_read": _stats["emails_read"],
        "replies_today": _stats["replies_today"],
        "outbound_today": _stats["outbound_today"],
        "errors": _stats["errors"],
        "limits": {
            "replies_per_day": MAX_REPLIES_DAY,
            "outbound_per_day": MAX_OUTBOUND_DAY,
        },
    }
