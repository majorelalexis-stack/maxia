"""MAXIA — Smoke test sur MAXIA Community server (Plan CEO V7).

Envoie :
  1. un message de test dans #general
  2. une annonce formelle dans #announcements

Les deux via le bot MAXIA outreach avec warming bypasse (test).
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BACKEND, ".env"))
except ImportError:
    pass

# Force UTF-8 for stdout on Windows
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass


# MAXIA Community (created 2026-04-09 via Claude Chrome)
SERVER_ID = "1491796153241698356"
CHANNEL_GENERAL = "1491799045914497095"
CHANNEL_ANNOUNCEMENTS = "1491798682239111178"

MSG_GENERAL = (
    "**MAXIA Community** is now live.\n\n"
    "AI-to-AI marketplace on 15 blockchains.\n"
    "46 MCP tools - GPU rental via Akash - 65 tokens swap via Jupiter.\n\n"
    "Paper trading by default. Not financial advice.\n"
    "https://maxiaworld.app"
)

MSG_ANNOUNCEMENTS = (
    "Welcome to **MAXIA Community**\n\n"
    "This server is the home of **MAXIA**, the AI-to-AI marketplace "
    "running on 15 blockchains with USDC/USDT escrow.\n\n"
    "**What we build**\n"
    "- 46 MCP tools accessible to any agent\n"
    "- GPU rental via Akash Network\n"
    "- 65 tokens swap via Jupiter\n"
    "- Multi-chain wallet integration\n\n"
    "**Rules**\n"
    "- English preferred in #general, local languages welcome in #asia / #latam / #japan / #europe / #africa\n"
    "- No financial advice, no pump and dump\n"
    "- Paper trading by default\n\n"
    "More info: https://maxiaworld.app"
)


async def send_one(
    label: str, channel_id: str, content: str, country: str = "SG",
) -> bool:
    from marketing import DiscordOutreach, DiscordResult, BlockedByCompliance

    # Bypass warming so we can test both channels in quick succession
    engine = DiscordOutreach(
        warming_start_ts=time.time() - 30 * 86400,
        min_spacing_seconds=1,  # allow 2 sends in this test
    )

    try:
        result: DiscordResult = await engine.send(
            server_id=SERVER_ID,
            channel_id=channel_id,
            content=content,
            country=country,
        )
    except BlockedByCompliance as e:
        print(f"[FAIL] {label}: compliance blocked - {e}")
        return False
    except Exception as e:
        print(f"[FAIL] {label}: {e}")
        return False

    print(f"[OK]   {label}: sent at {int(result.sent_at)}")
    return True


async def run() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    token = os.getenv("DISCORD_BOT_TOKEN", "")
    if not token or len(token) < 30:
        print("[FATAL] DISCORD_BOT_TOKEN not set in backend/.env")
        return 2

    print("MAXIA Community smoke test\n" + "=" * 50)
    print(f"server_id:      {SERVER_ID}")
    print(f"general:        {CHANNEL_GENERAL}")
    print(f"announcements:  {CHANNEL_ANNOUNCEMENTS}\n")

    ok1 = await send_one("#general", CHANNEL_GENERAL, MSG_GENERAL)
    await asyncio.sleep(1.5)  # respect Discord per-channel rate
    ok2 = await send_one("#announcements", CHANNEL_ANNOUNCEMENTS, MSG_ANNOUNCEMENTS)

    print("\n" + "=" * 50)
    if ok1 and ok2:
        print("SMOKE TEST PASSED — MAXIA Community outreach pipeline live.")
        return 0
    print("SMOKE TEST FAILED — see errors above.")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
