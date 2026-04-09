"""Mission — SEO directory auto-submit helper (Plan CEO V9 / mission 8).

Most SEO directories require a human-filled web form with a captcha, so
fully automated submission is not legal/safe. Instead, this mission
**prepares** submissions for Alexis by sending a ready-to-paste Telegram
message every few days containing:

- The directory URL
- The pre-filled standard payload (name, tagline, description, logo
  URL, category, tags) that Alexis just copies and pastes
- A link to the tracker file ``docs/SEO_SUBMISSIONS.md``

One directory per run, max 3 runs per week (Mon/Wed/Fri at 10h local).
Alexis gets 3 tiny nudges per week → 12 backlinks in a month with
zero thinking required on his side.

If a directory has a **public JSON submission API** (no captcha), a
future iteration can POST directly. Today's catalog contains only
form-based directories, so the mission is purely a reminder router.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Optional

log = logging.getLogger("ceo")

# Curated list sorted by ROI. Each entry is a dict with enough info
# for Alexis to paste-and-go. Pulled from docs/SEO_SUBMISSIONS.md.
DIRECTORY_QUEUE: list[dict] = [
    {
        "slug": "futurepedia",
        "name": "Futurepedia",
        "url": "https://www.futurepedia.io/submit-tool",
        "category": "AI tools",
        "traffic": "3M visits/month",
    },
    {
        "slug": "theresanaiforthat",
        "name": "There's An AI For That",
        "url": "https://theresanaiforthat.com/submit/",
        "category": "AI tools",
        "traffic": "2M visits/month",
    },
    {
        "slug": "aixploria",
        "name": "AIxploria",
        "url": "https://www.aixploria.com/en/submit/",
        "category": "AI directory",
        "traffic": "500k visits/month",
    },
    {
        "slug": "ai-agents-directory",
        "name": "AI Agents Directory",
        "url": "https://aiagentsdirectory.com/submit-agent",
        "category": "AI agents (niche)",
        "traffic": "niche AI",
    },
    {
        "slug": "dappradar",
        "name": "DappRadar",
        "url": "https://dappradar.com/submit",
        "category": "Crypto dApps",
        "traffic": "biggest dApp dir",
    },
    {
        "slug": "alchemy-dapps",
        "name": "Alchemy Dapp Store",
        "url": "https://www.alchemy.com/dapps",
        "category": "Crypto dApps",
        "traffic": "dev-focused",
    },
    {
        "slug": "awesome-solana-github",
        "name": "Awesome Solana (GitHub PR)",
        "url": "https://github.com/paul-schaaf/awesome-solana",
        "category": "GitHub list",
        "traffic": "permanent backlink",
    },
    {
        "slug": "awesome-base-github",
        "name": "Awesome Base (GitHub PR)",
        "url": "https://github.com/base-org/awesome-base",
        "category": "GitHub list",
        "traffic": "permanent backlink",
    },
    {
        "slug": "tgstat",
        "name": "TGStat",
        "url": "https://tgstat.com/",
        "category": "Telegram directory",
        "traffic": "5M users/month",
    },
    {
        "slug": "storebot",
        "name": "Storebot",
        "url": "https://storebot.me/",
        "category": "Telegram bot directory",
        "traffic": "1M users/month",
    },
    {
        "slug": "combot",
        "name": "Combot",
        "url": "https://combot.org/",
        "category": "Telegram bot directory",
        "traffic": "dev-oriented",
    },
    {
        "slug": "discadia",
        "name": "Discadia",
        "url": "https://discadia.com/",
        "category": "Discord directory",
        "traffic": "server discovery",
    },
]

# Mon/Wed/Fri at 10h local
SCHEDULE_WEEKDAYS = {0, 2, 4}
SCHEDULE_HOUR: int = 10

STANDARD_PAYLOAD = (
    "Name:  MAXIA\n"
    "Tagline:  AI-to-AI marketplace on 15 blockchains\n"
    "URL:  https://maxiaworld.app\n"
    "Email:  ceo@maxiaworld.app\n"
    "Category:  AI, Crypto, Marketplace, Developer Tools\n"
    "Short description (160):  MAXIA is the first AI-to-AI marketplace "
    "where autonomous agents discover, buy, and sell services on 15 "
    "blockchains using USDC.\n"
    "Long description (600):  MAXIA is the first production AI-to-AI "
    "marketplace. Autonomous AI agents register, discover services, and "
    "pay each other in USDC across 15 blockchains. Features include 46 "
    "MCP tools, GPU rental via Akash Network, 65-token multi-chain swap "
    "via Jupiter and 0x, on-chain escrow on Solana and Base L2 with "
    "48-hour auto-refund, DeFi yields aggregation, tokenized stocks, "
    "and Lightning L402 micropayments. Free tier (100 req/day), paper "
    "trading by default, Python + TypeScript SDKs on PyPI and npm.\n"
    "Logo:  https://maxiaworld.app/favicon.svg\n"
    "OG image:  https://maxiaworld.app/og-image.png\n"
    "Tags:  ai, crypto, marketplace, solana, ethereum, gpu, agents, mcp"
)


def _next_directory(mem: dict) -> Optional[dict]:
    done: set[str] = set(mem.get("seo_submissions_done", []))
    for entry in DIRECTORY_QUEUE:
        if entry["slug"] not in done:
            return entry
    return None


async def mission_seo_submit(mem: dict, actions: dict) -> None:
    """Send Alexis one directory submission reminder per scheduled run."""
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")

    if now.weekday() not in SCHEDULE_WEEKDAYS:
        return
    if now.hour != SCHEDULE_HOUR:
        return
    if mem.get("_seo_submit_last_date") == today:
        return

    entry = _next_directory(mem)
    if entry is None:
        log.info("[seo_submit] queue exhausted — all directories processed")
        return

    text = (
        f"SEO submission reminder\n\n"
        f"Directory: {entry['name']}\n"
        f"URL: {entry['url']}\n"
        f"Category: {entry['category']} ({entry['traffic']})\n\n"
        f"Copy-paste payload below into the form:\n\n"
        f"{STANDARD_PAYLOAD}\n\n"
        f"Once submitted, reply 'seo done {entry['slug']}' in this chat "
        f"and the CEO will mark it complete."
    )

    try:
        from notifier import notify_telegram_alert
        await notify_telegram_alert("SEO submit", text)
    except Exception as e:
        log.warning("[seo_submit] notify failed: %s", e)
        return

    mem.setdefault("seo_submissions_pending", []).append({
        "slug": entry["slug"],
        "date": today,
        "url": entry["url"],
        "ts": int(time.time()),
    })
    mem["_seo_submit_last_date"] = today
    actions["counts"]["seo_submit"] = 1
    log.info("[seo_submit] reminder sent for %s", entry["slug"])
