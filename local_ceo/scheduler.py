"""Scheduler — mission timing, off days, and execution hooks.

Provides:
  - is_off_day(): deterministic random off day per week
  - run_mission(): execute a mission coroutine with before/after hooks
  - send_mail(): send email via VPS API
"""
import hashlib
import logging
import random
import time
from datetime import datetime

import httpx

from config_local import (
    VPS_URL, ADMIN_KEY, ALEXIS_EMAIL,
    OFF_DAYS_PER_WEEK,
    current_email_quota,
)

log = logging.getLogger("ceo")

# ══════════════════════════════════════════
# Email — envoyer via VPS API
# ══════════════════════════════════════════


async def send_mail(subject: str, body: str) -> None:
    """Envoie un mail a Alexis via l'API VPS."""
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
                log.error("Mail error %d: %s", resp.status_code, resp.text[:100])
    except Exception as e:
        log.error("Mail send error: %s", e)


# ══════════════════════════════════════════
# Anti-spam — jour off aleatoire (Phase 6)
# ══════════════════════════════════════════


def is_off_day() -> bool:
    """1 random off day per week — deterministic via week seed."""
    if OFF_DAYS_PER_WEEK <= 0:
        return False
    today = datetime.now().date()
    week_seed = f"maxia_off_{today.isocalendar()[0]}_{today.isocalendar()[1]}"
    rng = random.Random(hashlib.md5(week_seed.encode()).hexdigest())
    off_days = sorted(rng.sample(range(7), min(OFF_DAYS_PER_WEEK, 7)))
    is_off = today.weekday() in off_days
    if is_off:
        log.info("[OFF-DAY] Jour off (weekday=%d, off=%s) — pas de tweet", today.weekday(), off_days)
    return is_off


# ══════════════════════════════════════════
# Hooks Pipeline — before/after mission execution
# ══════════════════════════════════════════

mission_stats: dict = {}  # {"mission_name": {"runs": 0, "errors": 0, "last_duration": 0, "last_error": ""}}


async def run_mission(name: str, coro, mem: dict, actions: dict) -> bool:
    """Execute a mission with before/after hooks. Returns True if success."""
    # -- BEFORE HOOK --
    stats = mission_stats.setdefault(name, {
        "runs": 0, "errors": 0, "last_duration": 0, "last_error": "",
    })

    # Check email quota — uses current_email_quota() so the cap ramps up
    # progressively over EMAIL_RAMP_UP_DAYS days, then multiplied by the
    # reputation tracker (0.33-1.0) so a high bounce rate auto-throttles us
    # before OVH/Gmail blacklists the domain.
    if "mail" in name or "report" in name or "opportunities" in name:
        mail_count = (
            actions["counts"].get("health_report_sent", 0) +
            actions["counts"].get("report_sent", 0) +
            actions["counts"].get("opportunities_sent", 0)
        )
        try:
            from email_reputation import get_quota_multiplier
            mult = get_quota_multiplier()
        except Exception:
            mult = 1.0
        cap = max(1, int(current_email_quota() * mult))
        if mail_count >= cap:
            log.debug("[HOOK] %s skipped — email quota %d/%d (mult=%.2f)",
                      name, mail_count, cap, mult)
            return False

    # -- EXECUTE --
    start = time.time()
    success = True
    try:
        await coro
        stats["runs"] += 1
    except Exception as e:
        success = False
        stats["errors"] += 1
        stats["last_error"] = str(e)[:100]
        log.error("[HOOK] %s FAILED: %s", name, str(e)[:100])

    # -- AFTER HOOK --
    duration = time.time() - start
    stats["last_duration"] = round(duration, 1)
    if duration > 300:
        log.warning("[HOOK] %s took %.0fs (>5min)", name, duration)

    return success
