"""Mission 21 — Bounty Poster: CEO posts recurring task bounties for other agents.

Runs weekly. The CEO identifies useful tasks and posts them as bounties
on the MAXIA task board. This bootstraps the bounty economy and gives
agents work to do on the platform.
"""
import asyncio
import json
import logging
import os
import time
from datetime import datetime

import httpx

from config_local import VPS_URL, CEO_API_KEY
from llm import llm
from agents import CEO_SYSTEM_PROMPT

log = logging.getLogger("ceo")

_STATE_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "bounty_poster_state.json")

# Recurring bounty templates — posted weekly with fresh parameters
BOUNTY_TEMPLATES = [
    {
        "title_template": "Weekly DeFi yield scan — {date}",
        "description": (
            "Scan the top DeFi protocols on Solana, Base, and Ethereum. "
            "Report the best 10 yield opportunities with: protocol name, pool, "
            "current APY, TVL, risk rating (1-5), and whether it requires lockup. "
            "Exclude ponzinomics and unsustainable yields (>200% APY). "
            "Output as structured JSON."
        ),
        "budget_usdc": 3.0,
        "category": "research",
        "deadline_seconds": 172800,  # 2 days
    },
    {
        "title_template": "AI agent news digest — week of {date}",
        "description": (
            "Compile a digest of the top 10 AI agent developments this week. "
            "Sources: GitHub trending, Twitter/X, arXiv, TechCrunch, The Block. "
            "For each: title, 2-sentence summary, relevance to autonomous AI agents, "
            "and link. Focus on: new frameworks, funding rounds, integrations, "
            "and regulatory developments."
        ),
        "budget_usdc": 2.0,
        "category": "research",
        "deadline_seconds": 172800,
    },
    {
        "title_template": "Whale wallet analysis — Solana top movers {date}",
        "description": (
            "Analyze the top 20 Solana wallets by USDC/SOL movement this week. "
            "For each wallet: total volume, main counterparties, DEX vs CEX ratio, "
            "new token positions opened, and behavioral pattern (accumulator, "
            "distributor, trader, farmer). Identify any emerging trends."
        ),
        "budget_usdc": 4.0,
        "category": "trading",
        "deadline_seconds": 259200,  # 3 days
    },
    {
        "title_template": "MAXIA API security check — {date}",
        "description": (
            "Run a non-destructive security scan against the MAXIA public API. "
            "Test: rate limiting effectiveness, input validation on 10 endpoints, "
            "auth token handling, error message information leakage. "
            "Report findings with severity (LOW/MEDIUM/HIGH) and remediation. "
            "DO NOT attempt any destructive actions."
        ),
        "budget_usdc": 5.0,
        "category": "security",
        "deadline_seconds": 259200,
    },
]


def _load_state() -> dict:
    try:
        if os.path.exists(_STATE_FILE):
            with open(_STATE_FILE, "r", encoding="utf-8") as f:
                return json.loads(f.read())
    except Exception:
        pass
    return {"last_run": "", "bounties_posted": 0}


def _save_state(state: dict) -> None:
    try:
        with open(_STATE_FILE, "w", encoding="utf-8") as f:
            f.write(json.dumps(state, indent=2, ensure_ascii=False))
    except Exception as e:
        log.error("[BountyPoster] Save state error: %s", e)


async def _post_bounty(title: str, description: str, budget: float,
                       category: str, deadline_s: int) -> bool:
    """Post a bounty via the MAXIA API."""
    if not CEO_API_KEY:
        log.warning("[BountyPoster] No CEO_API_KEY")
        return False

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{VPS_URL}/api/bounties/create",
                headers={"X-API-Key": CEO_API_KEY},
                json={
                    "title": title[:200],
                    "description": description[:2000],
                    "budget_usdc": budget,
                    "category": category,
                    "deadline_seconds": deadline_s,
                    "auto_assign": False,
                })
            if resp.status_code in (200, 201):
                data = resp.json()
                log.info("[BountyPoster] Posted: %s ($%.2f) → %s",
                         title[:40], budget, data.get("bounty_id", "?")[:12])
                return True
            else:
                log.warning("[BountyPoster] Failed (%d): %s", resp.status_code, resp.text[:150])
    except Exception as e:
        log.warning("[BountyPoster] Post error: %s", e)
    return False


async def mission_bounty_poster(mem: dict, actions: dict) -> None:
    """Weekly: post recurring bounties for agents to work on."""
    state = _load_state()

    # Once per week max
    today = datetime.now().strftime("%Y-%m-%d")
    last = state.get("last_run", "")
    if last:
        try:
            days_since = (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(last, "%Y-%m-%d")).days
            if days_since < 7:
                log.info("[BountyPoster] Last run %d days ago — skip (weekly)", days_since)
                return
        except ValueError:
            pass

    log.info("[BountyPoster] Posting weekly bounties...")
    posted = 0

    # Check existing open bounties (avoid duplicates)
    existing_titles = set()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{VPS_URL}/api/bounties/browse?limit=50")
            if resp.status_code == 200:
                for b in resp.json().get("bounties", []):
                    existing_titles.add(b.get("title", ""))
    except Exception:
        pass

    date_str = datetime.now().strftime("%b %d")
    for tpl in BOUNTY_TEMPLATES:
        title = tpl["title_template"].format(date=date_str)
        if title in existing_titles:
            log.info("[BountyPoster] Skipping duplicate: %s", title[:40])
            continue

        ok = await _post_bounty(
            title=title,
            description=tpl["description"],
            budget=tpl["budget_usdc"],
            category=tpl["category"],
            deadline_s=tpl["deadline_seconds"],
        )
        if ok:
            posted += 1
            try:
                from memory import log_action
                log_action(
                    "bounty_posted",
                    target=tpl["category"],
                    details=f"{title[:80]} budget={tpl['budget_usdc']}USDC",
                )
            except Exception as _e:
                log.debug("[BountyPoster] log_action failed: %s", _e)
        await asyncio.sleep(2)

    state["last_run"] = today
    state["bounties_posted"] = state.get("bounties_posted", 0) + posted
    _save_state(state)
    log.info("[BountyPoster] Done: %d bounties posted (total lifetime: %d)",
             posted, state["bounties_posted"])
