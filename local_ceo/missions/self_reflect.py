"""Mission — daily self-reflection and learning extraction.

Runs once per day around 22h local. Reads the last 24 hours of mission
activity from SQLite ``actions`` table + mem counters, asks qwen3 to
extract 3-5 concrete insights, and stores them via
``memory.compress_and_store_learning()`` so they land in both the
SQLite ``learnings`` table and the ChromaDB ``learnings`` collection.

Before this mission existed the ChromaDB ``learnings`` collection was
frozen at 27 chunks from March 27, and ``learnings`` SQLite table was
empty. The CEO had no way to remember what worked or failed day-to-day.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

log = logging.getLogger("ceo")

REFLECT_HOUR = 22  # 22h local
MAX_INSIGHTS = 5


def _fetch_day_summary() -> dict:
    """Pull today's actions from SQLite and bucket them by type."""
    try:
        from memory import get_today_actions
        rows = get_today_actions() or []
    except Exception as e:
        log.warning("[self_reflect] get_today_actions failed: %s", e)
        return {"total": 0, "by_type": {}, "samples": []}

    by_type: dict[str, int] = {}
    samples: list[dict] = []
    for r in rows:
        t = r.get("type") if isinstance(r, dict) else None
        if not t:
            continue
        by_type[t] = by_type.get(t, 0) + 1
        if len(samples) < 20:
            samples.append(
                {
                    "type": t,
                    "target": (r.get("target") or "")[:80],
                    "details": (r.get("details") or "")[:200],
                }
            )
    return {"total": len(rows), "by_type": by_type, "samples": samples}


def _build_reflection_prompt(summary: dict, mem: dict, actions: dict) -> str:
    """Render the day summary as a prompt the LLM can reason about."""
    today = datetime.now().strftime("%Y-%m-%d")
    lines: list[str] = []
    lines.append(f"Date: {today}")
    lines.append(f"Total actions logged: {summary['total']}")
    lines.append("")
    lines.append("Actions by type:")
    for t, n in sorted(summary["by_type"].items(), key=lambda x: -x[1]):
        lines.append(f"  {t}: {n}")
    lines.append("")
    counts = (actions or {}).get("counts", {}) or {}
    lines.append("Today's counters:")
    for k, v in sorted(counts.items()):
        if v:
            lines.append(f"  {k}: {v}")
    lines.append("")
    lines.append("Sample action details (up to 20):")
    for s in summary["samples"]:
        lines.append(f"  - [{s['type']}] target={s['target']} {s['details']}")
    lines.append("")
    # Mem bucket sizes — lets the LLM see longer-term trends
    lines.append("Mem bucket sizes (historical):")
    for key in [
        "github_prospects",
        "outreach_sent",
        "disboard_bumps",
        "community_news_posts",
        "health_alerts",
        "weekly_reports",
    ]:
        v = mem.get(key)
        if isinstance(v, list):
            lines.append(f"  {key}: {len(v)}")

    lines.append("")
    lines.append(
        "Tache: en tant que CEO MAXIA, analyse cette journee et extrais "
        f"{MAX_INSIGHTS} insights courts et concrets sur ce qui a marche, "
        "ce qui n'a pas marche, et ce qu'il faut changer demain. Un "
        "insight par ligne, prefixe par '- ', max 120 caracteres par "
        "insight. Aucun blabla, aucune redondance. Pas d'introduction."
    )
    return "\n".join(lines)


def _parse_insights(raw: str) -> list[str]:
    """Extract bullet lines from the LLM output."""
    insights: list[str] = []
    if not isinstance(raw, str):
        return insights
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if line[:1] in {"-", "*", "•"}:
            line = line[1:].strip()
        elif line[:2].rstrip(".").isdigit() and "." in line[:3]:
            line = line.split(".", 1)[1].strip()
        if len(line) < 15 or len(line) > 250:
            continue
        insights.append(line)
        if len(insights) >= MAX_INSIGHTS:
            break
    return insights


async def mission_self_reflect(mem: dict, actions: dict) -> None:
    """Daily reflection — runs once per day at REFLECT_HOUR."""
    today = datetime.now().strftime("%Y-%m-%d")
    if mem.get("_self_reflect_last_date") == today:
        return
    if datetime.now().hour != REFLECT_HOUR:
        return

    log.info("[self_reflect] starting daily reflection")

    summary = _fetch_day_summary()
    if summary["total"] == 0:
        log.info("[self_reflect] no actions logged today — skip")
        mem["_self_reflect_last_date"] = today
        return

    prompt = _build_reflection_prompt(summary, mem, actions)

    try:
        from llm import ask
        from agents import STRATEGIST
    except ImportError as e:
        log.warning("[self_reflect] llm module unavailable: %s", e)
        return

    try:
        raw = await ask(STRATEGIST, prompt)
    except Exception as e:
        log.warning("[self_reflect] llm call failed: %s", e)
        return

    if not isinstance(raw, str) or len(raw) < 10:
        log.warning("[self_reflect] empty LLM response")
        return

    insights = _parse_insights(raw)
    if not insights:
        log.warning("[self_reflect] no insights parsed from LLM output")
        return

    try:
        from memory import compress_and_store_learning, log_action
        stored = 0
        for insight in insights:
            try:
                compress_and_store_learning(
                    insight=insight,
                    source="self_reflect",
                    topic="daily",
                    confidence=0.6,
                )
                stored += 1
            except Exception as e:
                log.warning("[self_reflect] store failed: %s", e)
        try:
            log_action(
                "self_reflect_done",
                target="daily",
                details=f"{stored}/{len(insights)} insights stored, {summary['total']} actions analyzed",
            )
        except Exception as _e:
            log.debug("[self_reflect] log_action failed: %s", _e)
        log.info(
            "[self_reflect] stored %d/%d insights from %d actions",
            stored, len(insights), summary["total"],
        )
    except Exception as e:
        log.warning("[self_reflect] store layer unavailable: %s", e)

    mem["_self_reflect_last_date"] = today
    actions.setdefault("counts", {})["self_reflect"] = 1
