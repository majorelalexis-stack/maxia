"""Mission 19 — Skill Scout: scan GitHub trending, extract skills, list free on marketplace.

Runs daily. Discovers trending repos, extracts actionable skills from them,
and lists them for free ($0) on the MAXIA skill marketplace.
Free skills attract agents to the platform.
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

# GitHub trending scrape fallback (no auth needed)
GITHUB_TRENDING_URL = "https://api.github.com/search/repositories"
_SKILL_CATEGORIES = ["ai", "blockchain", "defi", "security", "data", "infrastructure"]
_MAX_SKILLS_PER_RUN = 5
_COOLDOWN_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "skill_scout_state.json")


def _load_state() -> dict:
    try:
        if os.path.exists(_COOLDOWN_FILE):
            with open(_COOLDOWN_FILE, "r", encoding="utf-8") as f:
                return json.loads(f.read())
    except Exception:
        pass
    return {"last_run": "", "listed_skills": []}


def _save_state(state: dict) -> None:
    try:
        with open(_COOLDOWN_FILE, "w", encoding="utf-8") as f:
            f.write(json.dumps(state, indent=2, ensure_ascii=False))
    except Exception as e:
        log.error("[SkillScout] Save state error: %s", e)


async def _fetch_trending_repos() -> list:
    """Fetch trending repos from GitHub (last 7 days, AI/blockchain focus)."""
    repos = []
    queries = [
        "ai agent framework",
        "solana defi",
        "smart contract security",
        "llm optimization",
        "blockchain data",
    ]
    async with httpx.AsyncClient(timeout=15) as client:
        for q in queries:
            try:
                resp = await client.get(GITHUB_TRENDING_URL, params={
                    "q": f"{q} created:>2026-04-01",
                    "sort": "stars",
                    "order": "desc",
                    "per_page": 3,
                })
                if resp.status_code == 200:
                    items = resp.json().get("items", [])
                    for item in items:
                        repos.append({
                            "name": item["full_name"],
                            "description": (item.get("description") or "")[:200],
                            "stars": item.get("stargazers_count", 0),
                            "language": item.get("language", ""),
                            "topics": item.get("topics", [])[:5],
                            "url": item.get("html_url", ""),
                        })
                elif resp.status_code == 403:
                    log.warning("[SkillScout] GitHub rate limited, stopping")
                    break
            except Exception as e:
                log.warning("[SkillScout] GitHub search error: %s", e)
            await asyncio.sleep(2)  # Politesse
    return repos


async def _extract_skills_from_repos(repos: list) -> list:
    """Use LLM to extract actionable skills from repo descriptions."""
    if not repos:
        return []

    repo_text = ""
    for r in repos[:10]:
        repo_text += (
            f"- {r['name']} ({r['stars']}★, {r['language']}): "
            f"{r['description']} | topics: {', '.join(r['topics'])}\n"
        )

    prompt = (
        f"From these trending GitHub repos, extract {_MAX_SKILLS_PER_RUN} actionable skills "
        f"that an AI agent could learn and apply.\n\n"
        f"Repos:\n{repo_text}\n\n"
        f"For each skill, output JSON array with objects:\n"
        f'{{"skill_name": "short name (3-6 words)", '
        f'"skill_content": "detailed how-to (100-200 words, actionable steps)", '
        f'"source_repo": "owner/repo"}}\n\n'
        f"Only return the JSON array, no markdown."
    )

    response = await llm(prompt, system="You are a technical skill extraction system. Output valid JSON only.")
    if not response:
        return []

    # Parse LLM response
    try:
        # Handle markdown code blocks
        text = response.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
        if text.startswith("json"):
            text = text[4:].strip()
        skills = json.loads(text)
        if isinstance(skills, list):
            return skills[:_MAX_SKILLS_PER_RUN]
    except (json.JSONDecodeError, ValueError) as e:
        log.warning("[SkillScout] LLM JSON parse error: %s", e)
    return []


async def _list_skill_on_marketplace(skill: dict) -> bool:
    """List a skill on the MAXIA marketplace via API (free, $0)."""
    if not CEO_API_KEY:
        log.warning("[SkillScout] No CEO_API_KEY — cannot list skills")
        return False

    headers = {"X-API-Key": CEO_API_KEY}

    # Step 1: Learn the skill
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(f"{VPS_URL}/api/agent/skills/learn", headers=headers, json={
                "skill_name": skill["skill_name"],
                "skill_content": skill["skill_content"],
                "source": "github",
                "confidence": 0.7,
            })
            if resp.status_code not in (200, 201):
                log.warning("[SkillScout] Learn failed: %s", resp.text[:100])
                return False
    except Exception as e:
        log.warning("[SkillScout] Learn request error: %s", e)
        return False

    # Step 2: Apply it a few times to build confidence
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            for _ in range(3):
                await client.post(f"{VPS_URL}/api/agent/skills/apply", headers=headers, json={
                    "skill_name": skill["skill_name"],
                })
                await asyncio.sleep(0.5)
    except Exception:
        pass  # Non-critical

    # Step 3: Export to marketplace (free)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(f"{VPS_URL}/api/agent/skills/export", headers=headers, json={
                "skill_name": skill["skill_name"],
                "price_usdc": 0.0,
            })
            if resp.status_code in (200, 201):
                log.info("[SkillScout] Listed free skill: %s", skill["skill_name"])
                return True
            else:
                log.warning("[SkillScout] Export failed: %s", resp.text[:100])
    except Exception as e:
        log.warning("[SkillScout] Export error: %s", e)
    return False


async def mission_skill_scout(mem: dict, actions: dict) -> None:
    """Daily scan: GitHub trending → extract skills → list free on marketplace."""
    state = _load_state()

    # Once per day max
    today = datetime.now().strftime("%Y-%m-%d")
    if state.get("last_run") == today:
        log.info("[SkillScout] Already ran today — skip")
        return

    log.info("[SkillScout] Starting daily scan...")

    # 1. Fetch trending repos
    repos = await _fetch_trending_repos()
    if not repos:
        log.warning("[SkillScout] No repos found")
        state["last_run"] = today
        _save_state(state)
        return

    log.info("[SkillScout] Found %d trending repos", len(repos))

    # 2. Extract skills via LLM
    skills = await _extract_skills_from_repos(repos)
    if not skills:
        log.warning("[SkillScout] LLM extracted 0 skills")
        state["last_run"] = today
        _save_state(state)
        return

    log.info("[SkillScout] Extracted %d skills", len(skills))

    # 3. List each on marketplace
    listed = 0
    for skill in skills:
        name = skill.get("skill_name", "")
        if not name or name in state.get("listed_skills", []):
            continue
        ok = await _list_skill_on_marketplace(skill)
        if ok:
            listed += 1
            state.setdefault("listed_skills", []).append(name)
        await asyncio.sleep(2)

    state["last_run"] = today
    _save_state(state)
    log.info("[SkillScout] Done: %d new skills listed (total: %d)",
             listed, len(state.get("listed_skills", [])))
