"""MAXIA — Discord bot live smoke test (Plan CEO V7 / Sprint 5).

Sends ONE test message to a known channel via the real bot token.
Used to validate the full pipeline end-to-end:

    .env DISCORD_BOT_TOKEN
        -> backend/marketing/discord_outreach.py
        -> Discord REST API
        -> channel shows the message

Usage (from repo root):

    python scripts/test_discord_bot.py

Exit codes:
    0   message delivered
    1   SMTP / compliance / rate limit error
    2   missing configuration
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

# ── Load .env + path setup ──

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BACKEND, ".env"))
except ImportError:
    pass  # assume env vars already set


# ── Target channel (MAXIA test server #general) ──
#
# server: MAXIA test (1491791707296104529)
# channel: #general (1491791708201943266)
TEST_SERVER_ID: str = "1491791707296104529"
TEST_CHANNEL_ID: str = "1491791708201943266"
TEST_MESSAGE: str = (
    "**MAXIA bot** online — Plan CEO V7 Sprint 5 smoke test.\n"
    "AI-to-AI marketplace on 15 chains. https://maxiaworld.app"
)


async def run() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("discord.smoke")

    token = os.getenv("DISCORD_BOT_TOKEN", "")
    if not token or len(token) < 30:
        log.error("DISCORD_BOT_TOKEN not set in backend/.env")
        return 2
    log.info("Token length=%d (redacted)", len(token))

    # Import inside run() so dotenv has already loaded
    from marketing import DiscordOutreach, DiscordResult, BlockedByCompliance

    # Bypass the 14-day warming ramp for this smoke test by pretending the
    # bot has already warmed up. The daily caps still apply.
    import time as _time
    engine = DiscordOutreach(
        warming_start_ts=_time.time() - 30 * 86400,
    )

    try:
        result: DiscordResult = await engine.send(
            server_id=TEST_SERVER_ID,
            channel_id=TEST_CHANNEL_ID,
            content=TEST_MESSAGE,
            country="SG",  # any allowed country
        )
    except BlockedByCompliance as e:
        log.error("Compliance block: %s", e)
        return 1
    except Exception as e:
        log.error("Send failed: %s", e)
        return 1

    log.info(
        "Sent OK — server_count=%d total_count=%d warming_day=%d",
        result.server_count_today,
        result.total_count_today,
        result.warming_day,
    )
    print("\n[OK] Discord smoke test passed.")
    print(f"     Server:  {result.server_id}")
    print(f"     Channel: {result.channel_id}")
    print(f"     Sent at: {result.sent_at}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
