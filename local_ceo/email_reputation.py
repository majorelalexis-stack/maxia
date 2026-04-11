"""Email reputation tracker — auto-pause outreach if SMTP starts to flag.

Tracks 3 signals:

  1. **Bounce rate** — percentage of recent outbound emails that came back
     undeliverable (parsed from inbound mailer-daemon notifications).
  2. **Reply rate** — percentage of recent outbound emails that received any
     reply within 14 days (low reply = low engagement = potential spam flag).
  3. **Unsubscribe rate** — bonus signal if a List-Unsubscribe header was
     followed by the recipient.

Persistence: ``local_ceo/email_reputation.json``. Updated on each call to
``record_outbound``, ``record_bounce``, ``record_reply``.

Usage from missions::

    from email_reputation import record_outbound, get_quota_multiplier

    cap = current_email_quota() * get_quota_multiplier()
    if mail_count >= cap:
        return  # over the budget

    # ... after sending ...
    record_outbound(to_addr=..., subject=..., msg_id=...)

When the bounce rate over the rolling 14-day window exceeds the threshold,
``get_quota_multiplier()`` drops to 0.33 (so a 15/day cap effectively becomes
5/day). When the reply rate is below the floor, multiplier drops to 0.66.
A Telegram alert is fired exactly once per state transition.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

log = logging.getLogger("ceo.email_rep")

_HERE = Path(__file__).resolve().parent
_STATE_FILE = _HERE / "email_reputation.json"

# Thresholds (tunable via env)
BOUNCE_THRESHOLD_PCT = float(os.getenv("EMAIL_BOUNCE_PAUSE_PCT", "5.0"))
REPLY_FLOOR_PCT = float(os.getenv("EMAIL_REPLY_FLOOR_PCT", "1.0"))
ROLLING_WINDOW_DAYS = int(os.getenv("EMAIL_REP_WINDOW_DAYS", "14"))
LOW_BUDGET_MULTIPLIER = 0.33
MED_BUDGET_MULTIPLIER = 0.66


def _load() -> dict:
    if not _STATE_FILE.exists():
        return {
            "outbound": [],   # list of {to, subject, msg_id, ts}
            "bounces": [],    # list of {msg_id, to, ts, reason}
            "replies": [],    # list of {msg_id, from, ts}
            "unsubscribes": [],  # list of {to, ts}
            "state": "ok",
            "state_changed_at": 0,
        }
    try:
        return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("[email_rep] state load failed: %s — reset", e)
        return {
            "outbound": [], "bounces": [], "replies": [], "unsubscribes": [],
            "state": "ok", "state_changed_at": 0,
        }


def _save(state: dict) -> None:
    try:
        # Trim each list to keep the file under ~1 MB
        for k in ("outbound", "bounces", "replies", "unsubscribes"):
            state[k] = state.get(k, [])[-1000:]
        _STATE_FILE.write_text(
            json.dumps(state, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as e:
        log.warning("[email_rep] state save failed: %s", e)


def _within_window(ts: float, window_days: int = ROLLING_WINDOW_DAYS) -> bool:
    cutoff = time.time() - window_days * 86400
    return ts >= cutoff


def record_outbound(*, to_addr: str, subject: str, msg_id: str = "") -> None:
    """Append one outbound email to the ledger."""
    s = _load()
    s.setdefault("outbound", []).append({
        "to": (to_addr or "")[:120],
        "subject": (subject or "")[:200],
        "msg_id": (msg_id or "")[:200],
        "ts": time.time(),
    })
    _save(s)


def record_bounce(*, msg_id: str = "", to_addr: str = "", reason: str = "") -> None:
    """Record a bounce. Either ``msg_id`` or ``to_addr`` is enough to match."""
    s = _load()
    s.setdefault("bounces", []).append({
        "msg_id": (msg_id or "")[:200],
        "to": (to_addr or "")[:120],
        "reason": (reason or "")[:200],
        "ts": time.time(),
    })
    _save(s)


def record_reply(*, from_addr: str, msg_id: str = "") -> None:
    """Record an inbound reply (used to compute reply rate)."""
    s = _load()
    s.setdefault("replies", []).append({
        "from": (from_addr or "")[:120],
        "msg_id": (msg_id or "")[:200],
        "ts": time.time(),
    })
    _save(s)


def record_unsubscribe(*, to_addr: str) -> None:
    """Record an unsubscribe (List-Unsubscribe header followed)."""
    s = _load()
    s.setdefault("unsubscribes", []).append({
        "to": (to_addr or "")[:120],
        "ts": time.time(),
    })
    _save(s)


def compute_metrics(window_days: int = ROLLING_WINDOW_DAYS) -> dict:
    """Compute the rolling-window bounce and reply rates.

    Returns a dict::

        {
            "outbound_count": int,
            "bounce_count": int,
            "reply_count": int,
            "unsubscribe_count": int,
            "bounce_pct": float,
            "reply_pct": float,
            "state": "ok" | "med" | "low",
            "multiplier": float,
        }
    """
    s = _load()
    out = [x for x in s.get("outbound", []) if _within_window(x.get("ts", 0), window_days)]
    bnc = [x for x in s.get("bounces", []) if _within_window(x.get("ts", 0), window_days)]
    rpl = [x for x in s.get("replies", []) if _within_window(x.get("ts", 0), window_days)]
    uns = [x for x in s.get("unsubscribes", []) if _within_window(x.get("ts", 0), window_days)]

    out_count = len(out)
    bnc_count = len(bnc)
    rpl_count = len(rpl)
    uns_count = len(uns)

    bounce_pct = (bnc_count / out_count * 100) if out_count else 0.0
    reply_pct = (rpl_count / out_count * 100) if out_count else 0.0

    # Decide quota state — bounce dominates over reply because bounce
    # damages domain reputation immediately (Gmail/Outlook spam filter).
    if out_count < 10:
        # Not enough data yet — assume OK so we don't suffocate the ramp
        state = "ok"
        multiplier = 1.0
    elif bounce_pct >= BOUNCE_THRESHOLD_PCT:
        state = "low"
        multiplier = LOW_BUDGET_MULTIPLIER
    elif reply_pct < REPLY_FLOOR_PCT:
        state = "med"
        multiplier = MED_BUDGET_MULTIPLIER
    else:
        state = "ok"
        multiplier = 1.0

    return {
        "window_days": window_days,
        "outbound_count": out_count,
        "bounce_count": bnc_count,
        "reply_count": rpl_count,
        "unsubscribe_count": uns_count,
        "bounce_pct": round(bounce_pct, 2),
        "reply_pct": round(reply_pct, 2),
        "state": state,
        "multiplier": multiplier,
    }


def get_quota_multiplier() -> float:
    """Return the multiplier to apply on top of ``current_email_quota()``.

    1.0 = full quota, 0.66 = -33% (low engagement), 0.33 = -66% (bounces).

    Side effect: if the state changed since the last call, fire a Telegram
    alert to Alexis so he knows the throttle kicked in.
    """
    metrics = compute_metrics()
    s = _load()
    new_state = metrics["state"]
    old_state = s.get("state", "ok")

    if new_state != old_state:
        s["state"] = new_state
        s["state_changed_at"] = int(time.time())
        _save(s)
        log.warning(
            "[email_rep] state %s -> %s (bounce=%.1f%% reply=%.1f%% n=%d)",
            old_state, new_state,
            metrics["bounce_pct"], metrics["reply_pct"], metrics["outbound_count"],
        )
        # Best-effort Telegram alert (non-blocking, fire-and-forget pattern)
        try:
            import asyncio
            from notifier import notify_telegram
            title = "Email reputation alert"
            msg = (
                f"Quota multiplier changed: *{old_state}* -> *{new_state}*\n"
                f"Bounce rate: {metrics['bounce_pct']}%\n"
                f"Reply rate: {metrics['reply_pct']}%\n"
                f"Window: {metrics['window_days']}d, n={metrics['outbound_count']}\n\n"
                f"Effective cap multiplier is now {metrics['multiplier']}x."
            )
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(notify_telegram(title, msg))
            except RuntimeError:
                # No running loop in this thread — sync fallback
                asyncio.run(notify_telegram(title, msg))
        except Exception as e:
            log.debug("[email_rep] telegram alert failed: %s", e)

    return metrics["multiplier"]


def snapshot() -> dict:
    """Read-only metrics snapshot for the dashboard."""
    return compute_metrics()
