"""CEO Objectives — measurable weekly goals with scoring."""
import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

OBJECTIVES_PATH = Path(__file__).parent.parent / "local_ceo" / "ceo_objectives.json"


def _load() -> dict[str, Any]:
    if OBJECTIVES_PATH.exists():
        try:
            with open(OBJECTIVES_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return {"current_week": None, "history": [], "streak_pivot": 0}


def _save(data: dict[str, Any]) -> None:
    try:
        OBJECTIVES_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(OBJECTIVES_PATH, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error("Failed to save objectives: %s", e)


def set_weekly_objective(objective: str, target: int, metric: str) -> dict[str, Any]:
    """Set this week's objective. Called by CEO strategic loop."""
    data = _load()
    # Archive current week if exists
    if data["current_week"]:
        data["history"].append(data["current_week"])
        data["history"] = data["history"][-12:]  # Keep 12 weeks
    data["current_week"] = {
        "objective": objective,
        "target": target,
        "metric": metric,  # e.g. "signups", "revenue_usdc", "transactions"
        "current": 0,
        "score": 0,
        "started_at": int(time.time()),
        "strategy": "",
        "actions_taken": [],
        "pivot_count": 0,
    }
    _save(data)
    return data["current_week"]


def update_progress(current: int, strategy: str = "", action: str = "") -> dict[str, Any]:
    """Update progress toward weekly objective. Called daily by CEO."""
    data = _load()
    if not data["current_week"]:
        return {"error": "No objective set"}
    week = data["current_week"]
    week["current"] = current
    week["score"] = min(100, int(current / max(week["target"], 1) * 100))
    if strategy:
        week["strategy"] = strategy
    if action:
        week["actions_taken"].append({"action": action, "ts": int(time.time()), "progress": current})
        week["actions_taken"] = week["actions_taken"][-50:]  # Cap
    _save(data)
    return week


def check_pivot_needed() -> dict[str, Any]:
    """Check if CEO should pivot strategy. Called by strategic loop."""
    data = _load()
    if not data["current_week"]:
        return {"pivot": False, "reason": "No objective set"}
    week = data["current_week"]
    elapsed_days = (time.time() - week["started_at"]) / 86400

    if elapsed_days >= 7:
        # Week is over — score it
        return {"pivot": True, "reason": f"Week complete. Score: {week['score']}%", "score": week["score"]}

    if elapsed_days >= 3 and week["score"] < 20:
        # 3 days in, less than 20% progress — mid-week pivot
        return {"pivot": True, "reason": f"Mid-week: only {week['score']}% after {elapsed_days:.1f} days", "score": week["score"]}

    return {"pivot": False, "score": week["score"], "days_elapsed": round(elapsed_days, 1), "days_remaining": round(7 - elapsed_days, 1)}


def get_objectives() -> dict[str, Any]:
    """Get current and historical objectives."""
    return _load()
