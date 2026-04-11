"""Mission — GitHub prospector daily (Plan CEO V9 / mission 2).

Runs ``backend.marketing.github_prospector.GithubProspector`` once per
day to find 20-30 new crypto + AI developers with public emails on
GitHub. For each prospect:

1. Infer country via GitHub ``location`` field.
2. Check the V7 compliance allowlist (28 countries, India geo-blocked,
   10 blocked). Skip if not allowed.
3. Infer language from country (ja, ko, pt-br, es, fr, en, ...).
4. Generate a 150-word personalized cold email via qwen3.5:27b
   referencing the prospect's top repository topic.
5. Send via the existing ``marketing.email_outreach.EmailOutreach``
   engine which enforces RGPD consent, 30/day cap, 30-min spacing,
   injection guards, and RFC-5322 address validation.

The mission logs each attempt in ``mem["github_prospects"]``. Runs at
10:00 local time, once per day.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from datetime import datetime
from typing import Optional

log = logging.getLogger("ceo")

# Ensure the backend package is importable from the CEO local process
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)
)))
_BACKEND_DIR = os.path.join(_REPO_ROOT, "backend")
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


# Topics to search on GitHub. Each run rotates through a subset so we
# explore the whole ecosystem over time instead of hammering one topic.
GITHUB_TOPICS: list[list[str]] = [
    ["solana-sdk", "solana-program", "anchor-framework"],
    ["ai-agent", "llm-agent", "autonomous-agent"],
    ["ethereum", "base-l2", "arbitrum"],
    ["defi", "yield-farming", "liquid-staking"],
    ["langchain", "crewai", "autogen"],
    ["crypto-trading-bot", "algorithmic-trading", "mev"],
    ["mcp-server", "model-context-protocol", "tool-use"],
]

MAX_PROSPECTS_PER_RUN: int = 25
MAX_EMAILS_PER_RUN: int = 10          # spread the 30/day cap across days
DAILY_RUN_HOUR: int = 10              # local time
CTA_LINK: str = "https://maxiaworld.app/register"
UNSUBSCRIBE_LINK: str = "https://maxiaworld.app/unsubscribe"


def _select_topics_for_today() -> list[str]:
    """Rotate topic groups by day of the week."""
    idx = datetime.now().weekday() % len(GITHUB_TOPICS)
    return GITHUB_TOPICS[idx]


def _already_ran_today(mem: dict) -> bool:
    last = mem.get("_github_prospect_last_run", "")
    return last == datetime.now().strftime("%Y-%m-%d")


async def _generate_personalized_email(
    prospect_lang: str,
    prospect_name: str,
    prospect_bio: str,
    prospect_topic: str,
) -> Optional[tuple[str, str, str]]:
    """Use qwen3.5:27b to write a 150-word cold email in the target language.

    Falls back to the static 13-language template if the LLM call fails.
    Returns (subject, body_text, body_html).
    """
    try:
        from marketing import render_outreach_email
    except ImportError as e:
        log.warning("[gh_prospect] render_outreach_email unavailable: %s", e)
        return None

    # Static template (fast, reliable, reviewed legal wording)
    subject, text, html = render_outreach_email(
        lang=prospect_lang,
        name=prospect_name,
        cta_link=CTA_LINK,
        unsubscribe_link=UNSUBSCRIBE_LINK,
    )

    # Optional LLM personalization: prepend a short sentence referencing
    # the prospect's topic. Skip if Ollama is unreachable.
    try:
        from llm import ask
        from agents import WRITER
        personal_line_prompt = (
            f"Write ONE short sentence in {prospect_lang} (max 25 words) "
            f"that mentions the developer's focus on '{prospect_topic}' and "
            f"MAXIA as an AI-to-AI marketplace on 15 chains. Plain text, no "
            f"quotes, no emoji, no exclamation marks."
        )
        lead = await ask(WRITER, personal_line_prompt)
        if isinstance(lead, str) and 10 < len(lead) < 200:
            lead = lead.strip().split("\n", 1)[0]
            # Prepend the lead line to both text and html variants
            text = f"{lead}\n\n{text}"
            html = f"<p>{lead}</p>{html}"
    except Exception as e:
        log.debug("[gh_prospect] LLM personalization skipped: %s", e)

    return subject, text, html


async def mission_github_prospect(mem: dict, actions: dict) -> None:
    """Daily GitHub prospector + multilingual email outreach."""
    if _already_ran_today(mem):
        log.debug("[gh_prospect] already ran today — skip")
        return

    now = datetime.now()
    if now.hour != DAILY_RUN_HOUR:
        log.debug("[gh_prospect] not in %02dh window (now=%02d) — skip",
                  DAILY_RUN_HOUR, now.hour)
        return

    try:
        from marketing.github_prospector import GithubProspector
        from marketing.email_outreach import (
            EmailOutreach, BlockedByCompliance, BlockedByConsent,
            RateLimitExceeded, InvalidEmail,
        )
    except ImportError as e:
        log.error("[gh_prospect] cannot import backend modules: %s", e)
        return

    topics = _select_topics_for_today()
    log.info("[gh_prospect] searching topics: %s", topics)

    prospector = GithubProspector(
        token=os.getenv("GITHUB_TOKEN", ""),
    )
    try:
        prospects = await prospector.search(
            topics=topics, max_results=MAX_PROSPECTS_PER_RUN,
        )
    except Exception as e:
        log.error("[gh_prospect] scrape error: %s", e)
        return

    if not prospects:
        log.info("[gh_prospect] 0 new prospects")
        mem["_github_prospect_last_run"] = now.strftime("%Y-%m-%d")
        return

    engine = EmailOutreach()
    sent = 0
    skipped = 0
    errors = 0

    for prospect in prospects[:MAX_EMAILS_PER_RUN]:
        # Country + lang already inferred by the scraper
        if not prospect.country:
            skipped += 1
            continue

        rendered = await _generate_personalized_email(
            prospect_lang=prospect.lang,
            prospect_name=prospect.name or prospect.login,
            prospect_bio=prospect.bio,
            prospect_topic=topics[0] if topics else "crypto",
        )
        if rendered is None:
            skipped += 1
            continue
        subject, text, html = rendered

        try:
            await engine.send(
                to=prospect.email,
                subject=subject,
                body_text=text,
                body_html=html,
                lang=prospect.lang,
                country=prospect.country,
            )
            sent += 1
            mem.setdefault("github_prospects", []).append({
                "ts": int(time.time()),
                "login": prospect.login,
                "email": prospect.email,
                "country": prospect.country,
                "lang": prospect.lang,
                "status": "sent",
            })
            try:
                from memory import log_action
                log_action(
                    "gh_prospect_sent",
                    target=prospect.email,
                    details=f"to @{prospect.login} ({prospect.country}, {prospect.lang}): {subject[:100]}",
                )
            except Exception as _e:
                log.debug("[gh_prospect] log_action failed: %s", _e)
            try:
                from vector_memory_local import vmem as _vmem
                if _vmem:
                    _vmem.store_contact(
                        username=prospect.login,
                        platform="github",
                        info=(
                            f"{prospect.email} ({prospect.country}, {prospect.lang}). "
                            f"Cold-emailed {datetime.now().strftime('%Y-%m-%d')}: {subject[:120]}"
                        ),
                    )
            except Exception as _e:
                log.debug("[gh_prospect] store_contact failed: %s", _e)
        except (BlockedByCompliance, BlockedByConsent) as e:
            skipped += 1
            log.info("[gh_prospect] blocked %s: %s", prospect.email, e)
        except (RateLimitExceeded, InvalidEmail) as e:
            errors += 1
            log.warning("[gh_prospect] rate-limited/invalid %s: %s",
                        prospect.email, e)
            break  # daily cap hit — stop the loop
        except Exception as e:
            errors += 1
            log.error("[gh_prospect] send error for %s: %s", prospect.email, e)

        # Gentle pacing between sends even though the engine enforces it
        await asyncio.sleep(1)

    mem["_github_prospect_last_run"] = now.strftime("%Y-%m-%d")
    actions["counts"]["github_prospect"] = sent
    log.info(
        "[gh_prospect] done: %d sent, %d skipped, %d errors (%d prospects found)",
        sent, skipped, errors, len(prospects),
    )
    try:
        from memory import log_action
        log_action(
            "gh_prospect_run",
            target="daily",
            details=f"sent={sent} skipped={skipped} errors={errors} found={len(prospects)} topics={','.join(topics)}",
        )
    except Exception as _e:
        log.debug("[gh_prospect] log_action run-summary failed: %s", _e)

    # Keep history bounded (last 500 sends)
    if len(mem.get("github_prospects", [])) > 500:
        mem["github_prospects"] = mem["github_prospects"][-500:]
