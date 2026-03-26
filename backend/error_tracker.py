"""Error Tracker — capture et stocke les erreurs pour debug.
Alternative legere a Sentry. Expose via /api/admin/errors."""
import time
import traceback
from collections import deque

_errors: deque = deque(maxlen=500)  # Keep last 500 errors


def track_error(module: str, error: Exception, context: str = ""):
    """Enregistre une erreur avec contexte."""
    _errors.append({
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "module": module,
        "error": str(error),
        "type": type(error).__name__,
        "traceback": traceback.format_exc()[-500:],
        "context": context[:200],
    })


def get_errors(limit: int = 50, module: str = "") -> list:
    """Retourne les dernieres erreurs, filtrees par module optionnel."""
    errors = list(_errors)
    if module:
        errors = [e for e in errors if e["module"] == module]
    return errors[-limit:]


def get_error_stats() -> dict:
    """Stats d'erreurs par module."""
    stats: dict = {}
    for e in _errors:
        m = e["module"]
        stats.setdefault(m, 0)
        stats[m] += 1
    return {"total": len(_errors), "by_module": stats}
