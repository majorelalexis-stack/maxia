"""Mission — Inbound email prospect handler (Plan CEO / Phase A).

Reads ``ceo@maxiaworld.app``, detects replies from prospects (non-Alexis
senders), routes each through :class:`sales.MaxiaSalesAgent`, and ships
the generated draft to Alexis on Telegram for GO/NO approval before
sending the real SMTP reply.

Two feature flags in ``.env``:

- ``ENABLE_EMAIL_SALES=1`` — master switch for the whole mission.
- ``AUTO_REPLY_EMAIL=1`` — skip the approval step and send drafts
  immediately. Keep at ``0`` until you've validated ~10 drafts by hand.

State:

- Uses ``sales/email_sales_state.json`` to persist a list of already
  handled ``message_id`` values so the same email isn't drafted twice
  across restarts.
- MaxiaSalesAgent persists its own per-prospect conversation state in
  ``sales/conversations.db`` keyed on ``email:<normalized_from_addr>``.

Rate limiting:

- Max ``MAX_DRAFTS_PER_CYCLE`` drafts per invocation. Each approval
  blocks up to ``APPROVAL_TIMEOUT_S`` seconds, so the mission is
  intentionally sparse: scheduled every 15 min, max 3 drafts per cycle,
  worst case ~6 min blocked per cycle.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Optional

log = logging.getLogger("ceo")

_LOCAL_CEO = Path(__file__).resolve().parent.parent

# Feature flags
_ENABLE = os.getenv("ENABLE_EMAIL_SALES", "0") == "1"
_AUTO_REPLY = os.getenv("AUTO_REPLY_EMAIL", "0") == "1"

# Throttling
MAX_DRAFTS_PER_CYCLE = 3
APPROVAL_TIMEOUT_S = 120

_STATE_FILE = _LOCAL_CEO / "sales" / "email_sales_state.json"


def _load_state() -> dict:
    try:
        if _STATE_FILE.exists():
            return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"handled_message_ids": [], "drafts_sent": 0, "drafts_denied": 0}


def _save_state(state: dict) -> None:
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(
            json.dumps(state, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as e:
        log.warning("[email_sales] state save failed: %s", e)


def _normalize_addr(addr: str) -> str:
    """Lowercase + strip. Used both for filtering and conversation_id."""
    return (addr or "").strip().lower()


def _is_prospect_reply(email_row: dict) -> bool:
    """Decide whether an inbound email should be handled by the sales agent.

    Rules:

    - Non-Alexis sender (``majorel``, ``maxia`` domain excluded)
    - Subject starts with ``Re:`` OR the body looks like a reply (>20 chars)
    - Not an automatic bounce / no-reply
    """
    from_addr = _normalize_addr(email_row.get("from_addr", ""))
    subject = (email_row.get("subject") or "").strip()
    body = (email_row.get("body") or "").strip()

    if not from_addr or "@" not in from_addr:
        return False
    if "majorel" in from_addr or "maxia" in from_addr:
        return False
    if any(tag in from_addr for tag in ("noreply", "no-reply", "mailer-daemon", "postmaster", "bounce")):
        return False
    if len(body) < 20:
        return False
    # Heuristic: replies usually begin with "Re:" (any language, RE:, RÉP:, etc.)
    low = subject.lower()
    looks_like_reply = (
        low.startswith("re:") or low.startswith("re :")
        or low.startswith("aw:") or low.startswith("rép:")
        or low.startswith("sv:")  # Swedish
        or low.startswith("antwoord:")  # Dutch
    )
    # We also accept cold inbound that are clearly MAXIA-related
    if not looks_like_reply:
        low_body = body.lower()
        if "maxia" not in low_body and "maxiaworld" not in low_body:
            return False
    return True


def _make_conversation_id(from_addr: str) -> str:
    """Stable, short conversation id keyed on the sender's email."""
    normalized = _normalize_addr(from_addr)
    # Hash keeps the id short and URL-safe even for very long addresses
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:10]
    return f"email:{digest}"


async def _draft_reply_for(email_row: dict) -> Optional[str]:
    """Generate the draft reply via the smart_reply -> MaxiaSalesAgent path."""
    try:
        from missions.telegram_smart_reply import answer_user_message
    except ImportError as e:
        log.warning("[email_sales] cannot import smart_reply: %s", e)
        return None

    from_addr = _normalize_addr(email_row.get("from_addr", ""))
    subject = email_row.get("subject", "")
    body = (email_row.get("body") or "")[:2000]
    # Flatten subject + body into a single prompt so the agent sees the
    # full context of the inbound email.
    user_message = f"Email subject: {subject}\n\nEmail body:\n{body}"

    try:
        reply = await answer_user_message(
            user_message=user_message,
            history=[],
            language_code=None,  # let MaxiaSalesAgent auto-detect
            user_id=from_addr,
            channel="email",
        )
    except Exception as e:
        log.warning("[email_sales] smart_reply failed for %s: %s", from_addr, e)
        return None
    if not isinstance(reply, str) or len(reply) < 20:
        return None
    return reply.strip()


async def _send_real_email(email_row: dict, draft: str) -> bool:
    """SMTP send the approved draft as a reply to the original email."""
    try:
        from email_manager import send_email
    except ImportError as e:
        log.warning("[email_sales] email_manager unavailable: %s", e)
        return False

    subject = email_row.get("subject") or ""
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
    reply_subject = subject[:200]

    result = await send_email(
        to=email_row.get("from_addr", ""),
        subject=reply_subject,
        body=draft,
        reply_to_id=email_row.get("message_id"),
    )
    if isinstance(result, dict) and result.get("success"):
        return True
    log.warning("[email_sales] SMTP send failed: %s", result)
    return False


async def mission_email_prospect_inbox(mem: dict, actions: dict) -> None:
    """Poll inbox, draft replies via the sales agent, optional approval, send."""
    if not _ENABLE:
        return

    try:
        from email_manager import read_inbox
    except ImportError as e:
        log.warning("[email_sales] email_manager unavailable: %s", e)
        return

    try:
        emails = await read_inbox(max_emails=10)
    except Exception as e:
        log.warning("[email_sales] read_inbox failed: %s", e)
        return

    if not emails:
        return

    state = _load_state()
    handled = set(state.get("handled_message_ids", []))

    drafts_this_cycle = 0
    for email_row in emails:
        if drafts_this_cycle >= MAX_DRAFTS_PER_CYCLE:
            break
        msg_id = email_row.get("message_id") or email_row.get("uid", "")
        if not msg_id or msg_id in handled:
            continue
        if not _is_prospect_reply(email_row):
            # Mark as handled so we don't re-evaluate on every cycle
            handled.add(msg_id)
            continue

        from_addr = _normalize_addr(email_row.get("from_addr", ""))
        log.info(
            "[email_sales] drafting reply to %s (subj=%s)",
            from_addr, (email_row.get("subject") or "")[:50],
        )

        draft = await _draft_reply_for(email_row)
        if not draft:
            handled.add(msg_id)
            continue

        drafts_this_cycle += 1

        # Send without approval only when AUTO_REPLY_EMAIL=1 is explicitly set
        if _AUTO_REPLY:
            sent = await _send_real_email(email_row, draft)
            handled.add(msg_id)
            if sent:
                state["drafts_sent"] = state.get("drafts_sent", 0) + 1
                log.info("[email_sales] AUTO-sent reply to %s", from_addr)
            continue

        # Otherwise, route through the Telegram approval helper
        try:
            from config_local import TELEGRAM_BOT_TOKEN, TELEGRAM_CEO_CHAT_ID
            from sales.approval import request_telegram_approval
        except ImportError as e:
            log.warning("[email_sales] approval helper unavailable: %s", e)
            handled.add(msg_id)
            continue

        action_id = f"email_draft_{hashlib.sha1(msg_id.encode()).hexdigest()[:10]}"
        title = f"ORANGE - Email reply to {from_addr[:40]}"
        body_preview = (
            f"Subject: {email_row.get('subject', '')[:100]}\n\n"
            f"--- DRAFT REPLY ---\n{draft[:800]}"
        )
        verdict = await request_telegram_approval(
            bot_token=TELEGRAM_BOT_TOKEN,
            chat_id=TELEGRAM_CEO_CHAT_ID,
            action_id=action_id,
            title=title,
            body=body_preview,
            timeout_s=APPROVAL_TIMEOUT_S,
        )

        if verdict == "human":
            sent = await _send_real_email(email_row, draft)
            if sent:
                state["drafts_sent"] = state.get("drafts_sent", 0) + 1
                log.info("[email_sales] APPROVED + sent reply to %s", from_addr)
            else:
                log.warning("[email_sales] approved but SMTP failed for %s", from_addr)
            handled.add(msg_id)
        elif verdict == "denied":
            state["drafts_denied"] = state.get("drafts_denied", 0) + 1
            log.info("[email_sales] DENIED reply to %s", from_addr)
            handled.add(msg_id)
        else:  # timeout — keep unhandled so next cycle retries once
            log.info("[email_sales] TIMEOUT on draft for %s (will retry next cycle)", from_addr)

    # Keep only the most recent 500 handled IDs to bound the file size
    state["handled_message_ids"] = list(handled)[-500:]
    _save_state(state)

    actions.setdefault("counts", {})
    actions["counts"]["email_sales_drafts"] = (
        actions["counts"].get("email_sales_drafts", 0) + drafts_this_cycle
    )
