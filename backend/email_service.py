"""MAXIA Email Service — API endpoints pour la boite mail ceo@maxiaworld.app

Expose IMAP read + SMTP send via les endpoints FastAPI du dashboard.
"""
import asyncio
import email
import imaplib
import smtplib
import os
import json
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import decode_header
from email.utils import parseaddr, formatdate
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from dotenv import load_dotenv
from security import require_admin

load_dotenv()

router = APIRouter(prefix="/api/inbox", tags=["email"])

# ── Config ──
EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS", "")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")
IMAP_SERVER = os.getenv("IMAP_SERVER", "ssl0.ovh.net")
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
SMTP_SERVER = os.getenv("SMTP_SERVER", "ssl0.ovh.net")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))


def _decode_header_value(raw: str) -> str:
    if not raw:
        return ""
    parts = decode_header(raw)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return " ".join(decoded)


def _extract_body(msg) -> str:
    text_body = ""
    html_body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            charset = part.get_content_charset() or "utf-8"
            content = payload.decode(charset, errors="replace")
            if ctype == "text/plain" and not text_body:
                text_body = content
            elif ctype == "text/html" and not html_body:
                html_body = content
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            content = payload.decode(charset, errors="replace")
            if msg.get_content_type() == "text/html":
                html_body = content
            else:
                text_body = content
    return text_body, html_body


def _parse_date(date_str: str) -> str:
    if not date_str:
        return ""
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(date_str)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return date_str[:20]


# ══════════════════════════════════════════
# GET /api/inbox/messages — Lire les emails
# ══════════════════════════════════════════

@router.get("/messages")
async def get_messages(request: Request, folder: str = "INBOX", limit: int = 30, unread_only: bool = False):
    """Recupere les emails de la boite mail."""
    require_admin(request)
    if not EMAIL_PASSWORD:
        raise HTTPException(400, "Email non configure")

    def _fetch():
        emails = []
        try:
            imap = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
            imap.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            imap.select(folder, readonly=True)

            criteria = "UNSEEN" if unread_only else "ALL"
            status, data = imap.search(None, criteria)
            if status != "OK" or not data[0]:
                imap.logout()
                return []

            uids = data[0].split()
            # Les plus recents en premier
            for uid in reversed(uids[-limit:]):
                status, msg_data = imap.fetch(uid, "(RFC822 FLAGS)")
                if status != "OK":
                    continue
                raw = msg_data[0][1]
                msg = email.message_from_bytes(raw)

                # Flags (lu/non lu)
                flags_data = msg_data[0][0].decode() if msg_data[0][0] else ""
                is_read = "\\Seen" in flags_data

                from_raw = msg.get("From", "")
                from_name, from_addr = parseaddr(from_raw)
                from_name = _decode_header_value(from_name) or from_addr.split("@")[0]
                subject = _decode_header_value(msg.get("Subject", ""))
                date_str = _parse_date(msg.get("Date", ""))
                message_id = msg.get("Message-ID", "")
                text_body, html_body = _extract_body(msg)

                emails.append({
                    "uid": uid.decode(),
                    "from_addr": from_addr,
                    "from_name": from_name,
                    "subject": subject or "(no subject)",
                    "date": date_str,
                    "read": is_read,
                    "body_text": text_body[:5000],
                    "body_html": html_body[:10000] if html_body else "",
                    "message_id": message_id,
                    "has_attachments": any(
                        part.get_content_disposition() == "attachment"
                        for part in msg.walk()
                    ) if msg.is_multipart() else False,
                })

            imap.logout()
        except Exception as e:
            print(f"[Email] IMAP error: {e}")
            raise

        return emails

    try:
        result = await asyncio.to_thread(_fetch)
        unread = sum(1 for e in result if not e.get("read"))
        return {
            "address": EMAIL_ADDRESS,
            "folder": folder,
            "total": len(result),
            "unread": unread,
            "messages": result,
        }
    except Exception as e:
        raise HTTPException(500, "Email service error")


# ══════════════════════════════════════════
# GET /api/inbox/message/{uid} — Lire un email
# ══════════════════════════════════════════

@router.get("/message/{uid}")
async def get_message(uid: str, request: Request):
    """Recupere un email specifique et le marque comme lu."""
    require_admin(request)
    if not EMAIL_PASSWORD:
        raise HTTPException(400, "Email non configure")

    def _fetch_one():
        try:
            imap = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
            imap.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            imap.select("INBOX")

            # Marquer comme lu
            imap.store(uid.encode(), "+FLAGS", "\\Seen")

            status, msg_data = imap.fetch(uid.encode(), "(RFC822)")
            if status != "OK":
                imap.logout()
                return None

            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)

            from_name, from_addr = parseaddr(msg.get("From", ""))
            from_name = _decode_header_value(from_name) or from_addr.split("@")[0]
            subject = _decode_header_value(msg.get("Subject", ""))
            text_body, html_body = _extract_body(msg)

            imap.logout()
            return {
                "uid": uid,
                "from_addr": from_addr,
                "from_name": from_name,
                "to": msg.get("To", ""),
                "subject": subject,
                "date": _parse_date(msg.get("Date", "")),
                "body_text": text_body,
                "body_html": html_body,
                "message_id": msg.get("Message-ID", ""),
            }
        except Exception as e:
            print(f"[Email] Fetch error: {e}")
            raise

    try:
        result = await asyncio.to_thread(_fetch_one)
        if not result:
            raise HTTPException(404, "Email not found")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, "Email service error")


# ══════════════════════════════════════════
# POST /api/inbox/send — Envoyer un email
# ══════════════════════════════════════════

@router.post("/send")
async def send_email(req: dict, request: Request = None):
    """Envoie un email."""
    if request is not None:
        require_admin(request)
    if not EMAIL_PASSWORD:
        raise HTTPException(400, "Email non configure")

    to = req.get("to", "").strip()
    subject = req.get("subject", "").strip()
    body = req.get("body", "").strip()
    reply_to_id = req.get("reply_to_id")

    if not to or not subject:
        raise HTTPException(400, "Champs 'to' et 'subject' requis")

    # Security: validate email format and block injection
    import re
    if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', to):
        raise HTTPException(400, "Adresse email invalide")
    if '\n' in to or '\r' in to or '\n' in subject or '\r' in subject:
        raise HTTPException(400, "Caracteres invalides detectes")

    def _send():
        msg = MIMEMultipart("alternative")
        msg["From"] = f"MAXIA <{EMAIL_ADDRESS}>"
        msg["To"] = to
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=True)

        if reply_to_id:
            msg["In-Reply-To"] = reply_to_id
            msg["References"] = reply_to_id

        msg.attach(MIMEText(body, "plain", "utf-8"))

        # Version HTML
        import html as _html
        html_body = _html.escape(body).replace("\n", "<br>")
        html = f"""<div style="font-family:-apple-system,sans-serif;line-height:1.6;color:#333;">
{html_body}
<br><br>
<div style="color:#888;font-size:12px;border-top:1px solid #eee;padding-top:10px;">
MAXIA — AI-to-AI Marketplace on 14 Chains<br>
<a href="https://maxiaworld.app" style="color:#6366f1;">maxiaworld.app</a>
</div></div>"""
        msg.attach(MIMEText(html, "html", "utf-8"))

        smtp = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT)
        smtp.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        smtp.send_message(msg)
        smtp.quit()

    try:
        await asyncio.to_thread(_send)
        print(f"[Email] Sent to {to}: {subject[:40]}")
        return {"success": True, "to": to, "subject": subject}
    except Exception as e:
        raise HTTPException(500, "Email service error")


# ══════════════════════════════════════════
# POST /api/inbox/reply/{uid} — Repondre a un email
# ══════════════════════════════════════════

@router.post("/reply/{uid}")
async def reply_to_email(uid: str, req: dict, request: Request):
    """Repond a un email existant."""
    require_admin(request)
    # D'abord recuperer l'original
    original = await get_message(uid, request)

    subject = original["subject"]
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"

    return await send_email({
        "to": original["from_addr"],
        "subject": subject,
        "body": req.get("body", ""),
        "reply_to_id": original.get("message_id"),
    }, request)


# ══════════════════════════════════════════
# DELETE /api/inbox/message/{uid} — Supprimer
# ══════════════════════════════════════════

@router.delete("/message/{uid}")
async def delete_message(uid: str, request: Request):
    """Supprime un email (le deplace dans Trash)."""
    require_admin(request)
    if not EMAIL_PASSWORD:
        raise HTTPException(400, "Email non configure")

    def _delete():
        imap = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
        imap.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        imap.select("INBOX")
        imap.store(uid.encode(), "+FLAGS", "\\Deleted")
        imap.expunge()
        imap.logout()

    try:
        await asyncio.to_thread(_delete)
        return {"success": True, "deleted": uid}
    except Exception as e:
        raise HTTPException(500, "Email service error")


# ══════════════════════════════════════════
# POST /api/inbox/mark-read/{uid}
# ══════════════════════════════════════════

@router.post("/mark-read/{uid}")
async def mark_read(uid: str, request: Request):
    """Marque un email comme lu."""
    require_admin(request)
    if not EMAIL_PASSWORD:
        raise HTTPException(400, "Email non configure")

    def _mark():
        imap = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
        imap.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        imap.select("INBOX")
        imap.store(uid.encode(), "+FLAGS", "\\Seen")
        imap.logout()

    try:
        await asyncio.to_thread(_mark)
        return {"success": True}
    except Exception as e:
        raise HTTPException(500, "Email service error")
