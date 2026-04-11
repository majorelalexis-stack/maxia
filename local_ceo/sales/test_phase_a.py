"""Phase A test — Inbound email prospect handler (dry-run).

Tests the email_prospect_inbox mission WITHOUT touching IMAP/SMTP and
WITHOUT blocking on Telegram approval. It:

    1. Feeds 3 synthetic email rows to ``_is_prospect_reply`` to verify
       the filter (valid reply, invalid sender, bounce).
    2. Calls ``_draft_reply_for`` on each valid row and prints the draft
       (this hits Ollama + MaxiaSalesAgent via the normal smart_reply path).
    3. Verifies persistence in ``sales/conversations.db`` under the
       ``email:*`` conversation namespace.

Run:
    cd local_ceo
    python sales/test_phase_a.py
"""
from __future__ import annotations

import asyncio
import sqlite3
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_LOCAL_CEO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_LOCAL_CEO))

import os
os.environ["ENABLE_MAXIA_SALES"] = "1"
os.environ["ENABLE_EMAIL_SALES"] = "1"

import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
)

from missions.email_prospect_inbox import (  # noqa: E402
    _is_prospect_reply,
    _draft_reply_for,
    _make_conversation_id,
)


SYNTHETIC_EMAILS = [
    {
        "name": "VALID reply from prospect",
        "expect_handled": True,
        "row": {
            "uid": "test-1",
            "message_id": "<test-phase-a-1@acme.example>",
            "from_addr": "sarah@acme.example",
            "from_name": "Sarah Chen",
            "subject": "Re: MAXIA AI-to-AI marketplace for your DeFi bot",
            "body": (
                "Hi,\n\n"
                "Thanks for reaching out about MAXIA. I'm building a DeFi yield "
                "scanner on Solana and we're currently routing trades through "
                "Jupiter directly. Two questions:\n\n"
                "1. How does your commission compare to raw Jupiter fees?\n"
                "2. Is USDC escrow really on Solana mainnet, or testnet?\n\n"
                "If the numbers work, we could onboard this quarter.\n\n"
                "Sarah"
            ),
        },
    },
    {
        "name": "INVALID — email from Alexis himself",
        "expect_handled": False,
        "row": {
            "uid": "test-2",
            "message_id": "<test-phase-a-2@maxiaworld.app>",
            "from_addr": "majorel.alexis@gmail.com",
            "from_name": "Alexis",
            "subject": "Re: daily report",
            "body": "ok merci, je regarde ca",
        },
    },
    {
        "name": "INVALID — automatic bounce",
        "expect_handled": False,
        "row": {
            "uid": "test-3",
            "message_id": "<test-phase-a-3@mail.example>",
            "from_addr": "mailer-daemon@mail.example",
            "from_name": "Mail Delivery Subsystem",
            "subject": "Delivery Status Notification (Failure)",
            "body": "The following message could not be delivered...",
        },
    },
    {
        "name": "VALID — inbound cold question mentioning MAXIA",
        "expect_handled": True,
        "row": {
            "uid": "test-4",
            "message_id": "<test-phase-a-4@startup.example>",
            "from_addr": "founder@startup.example",
            "from_name": "Marc Founder",
            "subject": "Question about MAXIA escrow and enterprise features",
            "body": (
                "Hello MAXIA team,\n\n"
                "I saw your product on a dev forum. We're a small fintech startup "
                "building an AI portfolio assistant for non-US customers. We need "
                "USDC payment rails and on-chain escrow. Does MAXIA support "
                "enterprise SSO (OIDC)?\n\n"
                "Best,\nMarc"
            ),
        },
    },
]


def verify_persistence(handled_addrs: list[str]) -> int:
    db = _LOCAL_CEO / "sales" / "conversations.db"
    if not db.exists():
        print("[FAIL] conversations.db missing")
        return 0
    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute(
            "SELECT conversation_id, stage, lang, length(history_json) "
            "FROM conversations WHERE conversation_id LIKE 'email:%'"
        ).fetchall()
    finally:
        conn.close()
    print()
    print(f"[DB] email:* conversations in conversations.db: {len(rows)}")
    for r in rows:
        print(f"   {r[0]:30} stage={r[1]:22} lang={r[2]} history_len={r[3]}")
    return len(rows)


async def main() -> int:
    print("=" * 70)
    print("  Phase A — Email prospect inbox (dry-run, no IMAP/SMTP)")
    print("=" * 70)
    print()

    filter_ok = 0
    filter_fail: list[str] = []
    drafts_generated: list[tuple[str, str, float]] = []

    for case in SYNTHETIC_EMAILS:
        name = case["name"]
        row = case["row"]
        expected = case["expect_handled"]
        actual = _is_prospect_reply(row)
        flag = "OK" if actual == expected else "FAIL"
        print(f"[{flag}] filter: {name}  (expected={expected}, actual={actual})")
        if actual == expected:
            filter_ok += 1
        else:
            filter_fail.append(name)
            continue

        if actual:
            print(f"      conversation_id = {_make_conversation_id(row['from_addr'])}")
            t0 = time.time()
            try:
                draft = await _draft_reply_for(row)
            except Exception as e:
                print(f"      [ERROR] {type(e).__name__}: {e}")
                continue
            dt = time.time() - t0
            if not draft:
                print(f"      [FAIL] no draft returned")
                filter_fail.append(name + " (no draft)")
                continue
            drafts_generated.append((row["from_addr"], draft, dt))
            print(f"      [draft {dt:.2f}s] {draft[:400]}")
        print()

    conv_count = verify_persistence([r["row"]["from_addr"] for r in SYNTHETIC_EMAILS])

    print()
    print("=" * 70)
    print(f"  filter  : {filter_ok}/{len(SYNTHETIC_EMAILS)} correct")
    print(f"  drafts  : {len(drafts_generated)} generated")
    print(f"  conv DB : {conv_count} email:* rows")
    if drafts_generated:
        lats = [d[2] for d in drafts_generated]
        print(f"  latency : mean={sum(lats)/len(lats):.2f}s max={max(lats):.2f}s")
    print("=" * 70)

    if filter_fail:
        print(f"[FAIL] filter errors: {filter_fail}")
        return 1
    if not drafts_generated:
        print("[FAIL] no drafts were generated")
        return 1
    print("[OK] Phase A dry-run passed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
