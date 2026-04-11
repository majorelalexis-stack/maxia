"""Mission — DISBOARD bump reminder (Plan CEO V9 / mission 1).

DISBOARD's ``/bump`` command is a Discord **slash command**. Bots cannot
invoke slash commands from other bots, and using a selfbot violates
Discord ToS and triggers instant account ban. The safe, ToS-compliant
solution is to **remind Alexis** via Telegram every 2 hours so he can
run ``/bump`` himself in MAXIA Community.

Schedule:
- 6 reminders per day, one every 2h between 09:00 and 21:00 local time
- Stops after 8 bumps/day (DISBOARD caps bumping impact per day)
- Logs each reminder + Alexis's confirmation in mem["disboard_bumps"]

The MAXIA Community server + bump URL lives in
``local_ceo/memory_prod/outreach_channels.json`` under
``discord_ceo.community_server`` — this file was populated by the
deploy session on 2026-04-09.

Reminder content is a short plain-text Telegram message with a direct
deep link to the Discord channel; no LLM call (deterministic message
keeps the job cheap and reliable).
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger("ceo")

MEMORY_PROD_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "memory_prod",
)
OUTREACH_PATH = os.path.join(MEMORY_PROD_DIR, "outreach_channels.json")

MAX_BUMPS_PER_DAY: int = 8
BUMP_INTERVAL_SECONDS: int = 7200          # 2 hours
ACTIVE_HOURS_LOCAL = (9, 21)               # 09h00 - 20h59 local time
DISBOARD_URL = "https://disboard.org/server/1491796153241698356"


def _load_community_info() -> Optional[dict]:
    """Read MAXIA Community guild_id + general channel id from memory_prod."""
    try:
        with open(OUTREACH_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError) as e:
        log.warning("[disboard_bump] cannot read outreach_channels.json: %s", e)
        return None

    discord_ceo = data.get("channels", {}).get("discord_ceo", {})
    community = discord_ceo.get("community_server", {})
    if not community.get("guild_id"):
        return None

    general_id = community.get("channels", {}).get("general")
    return {
        "guild_id": community["guild_id"],
        "general_channel_id": general_id,
        "name": community.get("name", "MAXIA Community"),
    }


def _discord_deep_link(guild_id: str, channel_id: Optional[str]) -> str:
    """Return a discord.com/channels/... URL that opens the right channel."""
    base = "https://discord.com/channels"
    if channel_id:
        return f"{base}/{guild_id}/{channel_id}"
    return f"{base}/{guild_id}/@home"


def _within_active_hours(dt: datetime) -> bool:
    return ACTIVE_HOURS_LOCAL[0] <= dt.hour < ACTIVE_HOURS_LOCAL[1]


def _daily_bump_count(mem: dict, today: str) -> int:
    bumps = mem.get("disboard_bumps", [])
    return sum(1 for b in bumps if isinstance(b, dict) and b.get("date") == today)


async def mission_disboard_bump(mem: dict, actions: dict) -> None:
    """Send a Telegram reminder to Alexis to run /bump in MAXIA Community.

    Runs safely through ``notifier.notify_telegram_alert``. If any step
    fails (no Telegram config, no community info), the mission logs a
    warning and returns without crashing the scheduler.
    """
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")

    if not _within_active_hours(now):
        log.debug("[disboard_bump] outside active hours (%dh) — skip", now.hour)
        return

    sent_today = _daily_bump_count(mem, today)
    if sent_today >= MAX_BUMPS_PER_DAY:
        log.debug("[disboard_bump] cap reached %d/%d — skip", sent_today, MAX_BUMPS_PER_DAY)
        return

    last_bump_ts = float(mem.get("_disboard_last_bump_ts", 0) or 0)
    if time.time() - last_bump_ts < BUMP_INTERVAL_SECONDS:
        remaining = int(BUMP_INTERVAL_SECONDS - (time.time() - last_bump_ts))
        log.debug("[disboard_bump] too soon (%ds left) — skip", remaining)
        return

    community = _load_community_info()
    if community is None:
        log.warning("[disboard_bump] community server info missing — skip")
        return

    deep_link = _discord_deep_link(
        community["guild_id"], community.get("general_channel_id"),
    )
    text = (
        f"MAXIA DISBOARD bump reminder ({sent_today + 1}/{MAX_BUMPS_PER_DAY} today)\n\n"
        f"Open MAXIA Community: {deep_link}\n"
        f"Type /bump in any channel.\n\n"
        f"DISBOARD listing: {DISBOARD_URL}"
    )

    try:
        from notifier import notify_telegram_alert
        await notify_telegram_alert("DISBOARD bump", text)
    except Exception as e:
        log.warning("[disboard_bump] notify_telegram_alert failed: %s", e)
        return

    mem.setdefault("disboard_bumps", []).append({
        "date": today,
        "ts": int(time.time()),
        "index": sent_today + 1,
    })
    mem["_disboard_last_bump_ts"] = time.time()
    # Trim history to last 60 days
    if len(mem["disboard_bumps"]) > 400:
        mem["disboard_bumps"] = mem["disboard_bumps"][-400:]

    actions["counts"]["disboard_bumps"] = sent_today + 1
    log.info("[disboard_bump] reminder sent (%d/%d today)",
             sent_today + 1, MAX_BUMPS_PER_DAY)
    try:
        from memory import log_action
        log_action(
            "disboard_bump",
            target="telegram_bot",
            details=f"reminder {sent_today + 1}/{MAX_BUMPS_PER_DAY} today",
        )
    except Exception as _e:
        log.debug("[disboard_bump] log_action failed: %s", _e)
