"""Self Updater — git pull + restart automatique.

#18: Le CEO se met a jour tout seul.
"""
import os
import subprocess
import time


_REPO_DIR = os.path.join(os.path.dirname(__file__), "..")
_LAST_CHECK_FILE = os.path.join(os.path.dirname(__file__), ".last_update_check")


def check_for_updates() -> dict:
    """Verifie s'il y a des mises a jour sur git."""
    try:
        # Git fetch
        subprocess.run(["git", "fetch"], cwd=_REPO_DIR, capture_output=True, timeout=30)

        # Comparer local vs remote
        local = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=_REPO_DIR, capture_output=True, text=True, timeout=10
        ).stdout.strip()

        remote = subprocess.run(
            ["git", "rev-parse", "origin/main"], cwd=_REPO_DIR, capture_output=True, text=True, timeout=10
        ).stdout.strip()

        has_updates = local != remote

        # Log du dernier check
        with open(_LAST_CHECK_FILE, "w") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\n{local}\n{remote}\n")

        if has_updates:
            # Voir les commits en retard
            log = subprocess.run(
                ["git", "log", "--oneline", f"{local}..{remote}"],
                cwd=_REPO_DIR, capture_output=True, text=True, timeout=10
            ).stdout.strip()
            return {"updates": True, "local": local[:8], "remote": remote[:8], "commits": log}

        return {"updates": False, "local": local[:8]}

    except Exception as e:
        return {"updates": False, "error": str(e)}


def apply_updates() -> dict:
    """Git pull et signale qu'un restart est necessaire."""
    try:
        result = subprocess.run(
            ["git", "pull", "origin", "main"],
            cwd=_REPO_DIR, capture_output=True, text=True, timeout=60
        )

        if result.returncode == 0:
            return {
                "success": True,
                "output": result.stdout[:200],
                "restart_needed": True,
            }
        else:
            return {"success": False, "error": result.stderr[:200]}

    except Exception as e:
        return {"success": False, "error": str(e)}


def needs_check(interval_hours: int = 6) -> bool:
    """Verifie si on doit checker les updates (toutes les 6h)."""
    try:
        if os.path.exists(_LAST_CHECK_FILE):
            age = time.time() - os.path.getmtime(_LAST_CHECK_FILE)
            return age > interval_hours * 3600
    except Exception:
        pass
    return True
