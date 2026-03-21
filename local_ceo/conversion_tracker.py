"""Conversion Tracker — Funnel follow → inscription → transaction.

#12: Track les conversions.
#13: Apprendre des erreurs (stop ce qui marche pas).
#20: Optimiser les prompts automatiquement.
"""
import json
import os
import time

_TRACKER_FILE = os.path.join(os.path.dirname(__file__), "conversions.json")


def _load() -> dict:
    try:
        if os.path.exists(_TRACKER_FILE):
            return json.loads(open(_TRACKER_FILE, encoding="utf-8").read())
    except Exception:
        pass
    return {
        "funnel": {"follows": 0, "visits": 0, "registers": 0, "transactions": 0},
        "action_stats": {},  # {action_type: {attempts, successes, failures}}
        "prompt_stats": {},  # {prompt_hash: {uses, decisions_generated, actions_succeeded}}
        "learned_rules": [],
    }


def _save(data: dict):
    try:
        with open(_TRACKER_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
    except Exception:
        pass


def track_action(action_type: str, success: bool):
    """Enregistre le resultat d'une action pour analyse."""
    data = _load()
    stats = data["action_stats"].setdefault(action_type, {"attempts": 0, "successes": 0, "failures": 0})
    stats["attempts"] += 1
    if success:
        stats["successes"] += 1
    else:
        stats["failures"] += 1
    _save(data)


def track_prompt(prompt_hash: str, decisions_count: int, success_count: int):
    """Track quel prompt genere les meilleures decisions."""
    data = _load()
    stats = data["prompt_stats"].setdefault(prompt_hash, {"uses": 0, "decisions": 0, "successes": 0})
    stats["uses"] += 1
    stats["decisions"] += decisions_count
    stats["successes"] += success_count
    _save(data)


def get_failing_actions(min_attempts: int = 5, max_success_rate: float = 0.2) -> list:
    """#13: Retourne les actions qui echouent systematiquement."""
    data = _load()
    failing = []
    for action, stats in data["action_stats"].items():
        if stats["attempts"] >= min_attempts:
            rate = stats["successes"] / stats["attempts"]
            if rate <= max_success_rate:
                failing.append({
                    "action": action,
                    "attempts": stats["attempts"],
                    "success_rate": f"{rate:.0%}",
                    "recommendation": "STOP" if rate == 0 else "REDUCE",
                })
    return failing


def get_best_actions(min_attempts: int = 3) -> list:
    """Retourne les actions qui marchent le mieux."""
    data = _load()
    good = []
    for action, stats in data["action_stats"].items():
        if stats["attempts"] >= min_attempts:
            rate = stats["successes"] / stats["attempts"]
            if rate >= 0.6:
                good.append({"action": action, "success_rate": f"{rate:.0%}", "attempts": stats["attempts"]})
    good.sort(key=lambda x: x["success_rate"], reverse=True)
    return good


def generate_learned_rules() -> list:
    """#13: Genere des regles basees sur les stats."""
    rules = []
    failing = get_failing_actions()
    for f in failing:
        rules.append(f"STOP {f['action']}: {f['success_rate']} success rate after {f['attempts']} attempts")
    best = get_best_actions()
    for b in best:
        rules.append(f"KEEP {b['action']}: {b['success_rate']} success rate")
    return rules


def get_funnel_stats() -> dict:
    data = _load()
    return data["funnel"]


def get_action_report() -> dict:
    data = _load()
    return {
        "action_stats": data["action_stats"],
        "failing": get_failing_actions(),
        "best": get_best_actions(),
        "learned_rules": generate_learned_rules(),
    }
