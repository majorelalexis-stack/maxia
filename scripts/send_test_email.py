"""MAXIA — Send a smoke test email to ceo@maxiaworld.app + discord-ceo@maxiaworld.app.

Uses the same SMTP config as backend/integrations/email_service.py
(reads OVH credentials from backend/.env). Sends a single test message
to both inboxes via SMTP_SSL on ssl0.ovh.net:465.

Usage:
    python scripts/send_test_email.py
"""
from __future__ import annotations

import os
import smtplib
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BACKEND, ".env"))
except ImportError:
    pass

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass

EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS", "")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")
SMTP_SERVER = os.getenv("SMTP_SERVER", "ssl0.ovh.net")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))

RECIPIENTS = [
    "ceo@maxiaworld.app",
    "discord-ceo@maxiaworld.app",
]


def build_message(to_addr: str) -> MIMEMultipart:
    msg = MIMEMultipart("alternative")
    msg["From"] = f"MAXIA CEO <{EMAIL_ADDRESS}>"
    msg["To"] = to_addr
    msg["Subject"] = "[MAXIA] SMTP smoke test — Plan CEO V9 deploy"
    msg["Date"] = formatdate(localtime=True)
    msg["List-Unsubscribe"] = "<mailto:unsubscribe@maxiaworld.app>"

    text = (
        "MAXIA CEO — SMTP smoke test\n"
        "================================\n\n"
        f"Sent at: {datetime.now().isoformat()}\n"
        f"To: {to_addr}\n"
        f"From: {EMAIL_ADDRESS}\n"
        f"SMTP: {SMTP_SERVER}:{SMTP_PORT}\n\n"
        "If you received this email, the OVH SMTP relay is operational\n"
        "and the V9 outreach engine (backend/marketing/email_outreach.py)\n"
        "can use the same credentials to send compliance-aware cold emails\n"
        "in 13 languages, capped at 30/day across 28 allowed countries.\n\n"
        "MAXIA — AI-to-AI marketplace on 15 blockchains\n"
        "https://maxiaworld.app\n"
    )
    html = (
        "<div style='font-family:Inter,system-ui,sans-serif;line-height:1.6;color:#333;max-width:600px;margin:0 auto;padding:20px;'>"
        "<h2 style='color:#7c3aed;margin:0 0 16px;'>MAXIA CEO — SMTP smoke test</h2>"
        f"<p><b>Sent at:</b> {datetime.now().isoformat()}<br>"
        f"<b>To:</b> {to_addr}<br>"
        f"<b>From:</b> {EMAIL_ADDRESS}<br>"
        f"<b>SMTP:</b> {SMTP_SERVER}:{SMTP_PORT}</p>"
        "<p>If you received this email, the OVH SMTP relay is "
        "operational and the V9 outreach engine "
        "(<code>backend/marketing/email_outreach.py</code>) can use the "
        "same credentials to send compliance-aware cold emails in 13 "
        "languages, capped at 30/day across 28 allowed countries.</p>"
        "<hr style='border:none;border-top:1px solid #eee;margin:20px 0;'>"
        "<p style='color:#888;font-size:12px;'>"
        "MAXIA — AI-to-AI marketplace on 15 blockchains<br>"
        "<a href='https://maxiaworld.app' style='color:#6366f1;'>maxiaworld.app</a>"
        "</p>"
        "</div>"
    )
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    return msg


def main() -> int:
    if not EMAIL_PASSWORD or not EMAIL_ADDRESS:
        print("[FATAL] EMAIL_ADDRESS / EMAIL_PASSWORD not set in backend/.env")
        return 2

    print(f"Connecting to {SMTP_SERVER}:{SMTP_PORT} as {EMAIL_ADDRESS}...")
    try:
        smtp = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, timeout=30)
        smtp.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
    except Exception as e:
        print(f"[FATAL] SMTP connect/login failed: {e}")
        return 1

    failures: list[tuple[str, str]] = []
    successes: list[str] = []
    for to in RECIPIENTS:
        try:
            msg = build_message(to)
            smtp.sendmail(EMAIL_ADDRESS, [to], msg.as_string())
            print(f"[OK] sent to {to}")
            successes.append(to)
        except Exception as e:
            print(f"[FAIL] {to}: {e}")
            failures.append((to, str(e)))

    smtp.quit()
    print()
    print(f"Sent: {len(successes)}/{len(RECIPIENTS)}")
    for s in successes:
        print(f"  + {s}")
    for to, err in failures:
        print(f"  - {to}: {err}")

    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
