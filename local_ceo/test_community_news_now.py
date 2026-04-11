"""Test script — Force ``mission_community_news`` to run NOW.

Bypasses the ``DAILY_HOUR`` gate and the daily-dedupe check to let Alexis
test the full flow end-to-end:

    1. Fetch live stats from the VPS.
    2. Generate a Discord draft via qwen3:30b-a3b-instruct-2507.
    3. Send the draft to the Telegram CEO chat with GO/NO buttons.
    4. Wait for Alexis to tap a button (or text reply) — up to
       ``APPROVAL_TIMEOUT_ORANGE_S`` seconds.
    5. If approved, POST to ``#announcements`` on Discord.

Usage:
    cd local_ceo
    python test_community_news_now.py

The script uses a temporary in-memory dict so it never touches
``ceo_memory.json`` — safe to run even while the main CEO is still
running in another terminal.
"""
from __future__ import annotations

import asyncio
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [TEST] %(message)s",
)
log = logging.getLogger("ceo")


async def main() -> int:
    log.info("═══════════════════════════════════════")
    log.info("  community_news — one-shot test (force=True)")
    log.info("═══════════════════════════════════════")

    # Imports inside main() so logging is configured first
    from missions.community_news import mission_community_news

    # Throw-away memory — do not touch the real ceo_memory.json
    mem: dict = {"community_news_posts": []}
    actions: dict = {"counts": {}}

    try:
        await mission_community_news(mem, actions, force=True)
    except KeyboardInterrupt:
        log.warning("Cancelled by user (Ctrl+C)")
        return 130
    except Exception as e:
        log.error("Test crashed: %s", e, exc_info=True)
        return 1

    posts = mem.get("community_news_posts", [])
    if posts:
        last = posts[-1]
        log.info(
            "SUCCESS: posted %d chars to channel %s",
            last.get("length", 0),
            last.get("channel_id", "?"),
        )
        return 0

    log.warning(
        "No post was published — either the approval was denied, "
        "the approval timed out, or the Discord POST failed. "
        "Check the log lines above."
    )
    return 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
