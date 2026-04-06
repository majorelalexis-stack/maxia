"""Mission — Weekly Strategy Review: collect metrics, analyze, detect pivots.

If score < 30% for 2 weeks, generates 3 pivot hypotheses.
"""
import json
import logging
import os
import time
from datetime import datetime
from typing import Any

import httpx

from agents import STRATEGIST, MAXIA_KNOWLEDGE
from config_local import VPS_URL
from llm import ask
from scheduler import send_mail

log = logging.getLogger("ceo")

_LOCAL_CEO_DIR = os.path.dirname(os.path.dirname(__file__))
_STRATEGY_HISTORY_FILE = os.path.join(_LOCAL_CEO_DIR, "strategy_history.json")

_OBJECTIVES = {
    "signups": 5, "email_replies": 2, "tweet_impressions": 50,
    "services_listed": 3, "health_uptime_pct": 99,
}


def _load_strategy_history() -> list[dict]:
    try:
        if os.path.exists(_STRATEGY_HISTORY_FILE):
            with open(_STRATEGY_HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.loads(f.read())
            return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError) as e:
        log.error("[STRATEGY] Load history error: %s", e)
    return []


def _save_strategy_history(history: list[dict]) -> None:
    try:
        with open(_STRATEGY_HISTORY_FILE, "w", encoding="utf-8") as f:
            f.write(json.dumps(history[-52:], indent=2, default=str, ensure_ascii=False))
    except OSError as e:
        log.error("[STRATEGY] Save history error: %s", e)


async def _fetch_vps_metrics() -> dict[str, Any]:
    """Collect metrics from VPS public endpoints."""
    metrics: dict[str, Any] = {"leaderboard": None, "marketplace_stats": None, "health": None, "fetch_errors": []}
    endpoints = {
        "leaderboard": f"{VPS_URL}/api/public/leaderboard",
        "marketplace_stats": f"{VPS_URL}/api/public/marketplace-stats",
        "health": f"{VPS_URL}/health",
    }
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        for key, url in endpoints.items():
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    metrics[key] = resp.json()
                else:
                    metrics["fetch_errors"].append(f"{key}: HTTP {resp.status_code}")
            except Exception as e:
                metrics["fetch_errors"].append(f"{key}: {str(e)[:60]}")
    return metrics


def _calculate_weekly_actuals(mem: dict, metrics: dict[str, Any]) -> dict[str, float]:
    """Calculate actual metrics for the week from memory + VPS data."""
    now = time.time()
    week_ago = now - 7 * 86400

    # Count email replies this week
    email_replies = sum(
        1 for entry in mem.get("outreach_sent", [])
        if entry.get("status") == "replied"
        and entry.get("date", "") >= datetime.fromtimestamp(week_ago).isoformat()
    )

    # Count tweets posted this week
    tweets_this_week = [
        t for t in mem.get("tweets_posted", [])
        if t.get("date", "") >= datetime.fromtimestamp(week_ago).isoformat()
    ]

    # Estimate tweet engagement from memory
    engagement = mem.get("tweet_engagement", [])
    recent_engagement = [
        e for e in engagement
        if e.get("date", "") >= datetime.fromtimestamp(week_ago).isoformat()
    ]
    tweet_impressions = len(recent_engagement) * 10  # rough estimate

    # Agent count from leaderboard
    leaderboard = metrics.get("leaderboard") or {}
    agents_list = leaderboard.get("agents", leaderboard.get("leaderboard", []))
    total_agents = len(agents_list) if isinstance(agents_list, list) else 0

    # Health alerts this week
    health_alerts_week = sum(
        1 for a in mem.get("health_alerts", [])
        if a.get("ts", 0) >= week_ago
    )
    # Estimate uptime (168 hours in a week, each alert ~30min downtime)
    estimated_downtime_hours = health_alerts_week * 0.5
    uptime_pct = max(0, 100 - (estimated_downtime_hours / 168 * 100))

    return {
        "signups": max(0, total_agents - mem.get("last_agent_count", total_agents)),
        "email_replies": email_replies,
        "tweet_impressions": tweet_impressions,
        "services_listed": 0,  # Would need marketplace_stats delta
        "health_uptime_pct": round(uptime_pct, 1),
        "tweets_posted": len(tweets_this_week),
        "total_agents": total_agents,
        "health_alerts": health_alerts_week,
    }


def _score_performance(actuals: dict[str, float]) -> int:
    """Score 0-100 based on how many objectives are met."""
    scores: list[float] = []
    for key, target in _OBJECTIVES.items():
        actual = actuals.get(key, 0)
        if target > 0:
            ratio = min(actual / target, 2.0)  # Cap at 200%
            scores.append(ratio * 100)
        else:
            scores.append(100.0)

    return round(sum(scores) / len(scores)) if scores else 0


async def mission_strategy_review(mem: dict, actions: dict) -> None:
    """Weekly strategy review: collect metrics, analyze, email report to Alexis."""
    if actions["counts"].get("strategy_review", 0) >= 1:
        log.info("[STRATEGY] Already reviewed this week — skip")
        return

    log.info("[STRATEGY] Starting weekly strategy review...")
    metrics = await _fetch_vps_metrics()
    actuals = _calculate_weekly_actuals(mem, metrics)
    score = _score_performance(actuals)

    history = _load_strategy_history()
    recent_scores = [h.get("score", 50) for h in history[-2:]]
    needs_pivot = len(recent_scores) >= 1 and all(s < 30 for s in recent_scores) and score < 30

    actuals_text = "\n".join(
        f"  {k}: {v} (target: {_OBJECTIVES.get(k, 'N/A')})"
        for k, v in actuals.items()
    )

    marketplace_stats = metrics.get("marketplace_stats") or {}
    stats_text = json.dumps(marketplace_stats, indent=2, default=str)[:1000] if marketplace_stats else "(unavailable)"
    fetch_errors = metrics.get("fetch_errors", [])
    errors_text = "\n".join(f"  - {e}" for e in fetch_errors) if fetch_errors else "None"
    prev_learnings = ""
    if history:
        last = history[-1]
        prev_learnings = f"\nLast week: {last.get('score', '?')}/100 — {last.get('key_insight', 'N/A')}"

    prompt = (
        f"Weekly Strategy Review for MAXIA (week {datetime.now().isocalendar()[1]}, {datetime.now().year})\n\n"
        f"PERFORMANCE METRICS:\n{actuals_text}\n\n"
        f"OVERALL SCORE: {score}/100\n\n"
        f"MARKETPLACE STATS:\n{stats_text}\n\n"
        f"DATA ERRORS:\n{errors_text}\n"
        f"{prev_learnings}\n\n"
        f"Analyze:\n"
        f"1. What worked this week (cite specific metrics)\n"
        f"2. What didn't work and why\n"
        f"3. Top 3 priorities for next week (specific, actionable)\n"
        f"4. One key learning to remember\n"
    )

    if needs_pivot:
        prompt += (
            f"\nWARNING: Score below 30% for 2+ weeks. "
            f"Recent scores: {recent_scores + [score]}.\n"
            f"Generate 3 PIVOT HYPOTHESES — fundamentally different approaches "
            f"MAXIA could try to gain traction. Be creative but realistic.\n"
        )

    prompt += "\nBe concise but thorough. Use data, not opinions."
    analysis = await ask(STRATEGIST, prompt, knowledge=MAXIA_KNOWLEDGE[:2000])

    week_num = datetime.now().isocalendar()[1]
    year = datetime.now().year
    today = datetime.now().strftime("%d/%m/%Y %H:%M")

    report = f"MAXIA CEO — Strategy Review — Week {week_num}/{year}\nDate: {today}\nScore: {score}/100\n"
    report += "=" * 50 + "\n\n=== PERFORMANCE vs OBJECTIVES ===\n"
    for key, target in _OBJECTIVES.items():
        actual = actuals.get(key, 0)
        status = "OK" if actual >= target else "BELOW"
        report += f"  [{status:5s}] {key:25s} {actual:>6} / {target}\n"
    report += f"\n  OVERALL SCORE: {score}/100\n"

    report += "\n=== ADDITIONAL METRICS ===\n"
    for key in ["tweets_posted", "total_agents", "health_alerts"]:
        if key in actuals:
            report += f"  {key}: {actuals[key]}\n"

    if fetch_errors:
        report += "\n=== DATA FETCH ERRORS ===\n"
        for err in fetch_errors:
            report += f"  - {err}\n"

    report += f"\n=== STRATEGIST ANALYSIS ===\n{analysis}\n" if analysis else ""

    if needs_pivot:
        report += "\n*** PIVOT MODE ACTIVATED — Score < 30% for 2+ weeks ***\n"

    subject = f"[MAXIA CEO] Strategy Review — Week {week_num} — Score {score}/100"
    if needs_pivot:
        subject += " [PIVOT NEEDED]"
    await send_mail(subject, report)

    # Extract key insight and save
    key_insight = ""
    if analysis:
        sentences = analysis.replace("\n", " ").split(".")
        key_insight = sentences[0].strip()[:200] if sentences else ""

    history.append({"week": week_num, "year": year, "date": today, "score": score,
                    "actuals": actuals, "key_insight": key_insight, "pivot_mode": needs_pivot})
    _save_strategy_history(history)

    mem.setdefault("strategy_learnings", []).append(
        {"date": today, "week": week_num, "score": score, "insight": key_insight})
    if len(mem.get("strategy_learnings", [])) > 52:
        mem["strategy_learnings"] = mem["strategy_learnings"][-52:]
    mem["last_agent_count"] = actuals.get("total_agents", 0)

    actions["counts"]["strategy_review"] = 1
    log.info("[STRATEGY] Weekly review done — score %d/100, pivot=%s", score, needs_pivot)
