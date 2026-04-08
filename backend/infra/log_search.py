"""MAXIA — Centralized log search API (PRO-I5).

Provides structured search across JSON log files without SSH access.
Admin-only endpoints for querying, filtering, and tailing logs.
"""
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from core.error_utils import safe_error

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/logs", tags=["logs"])

LOG_DIR = Path(__file__).parent.parent / "logs"
AUDIT_LOG = Path(__file__).parent.parent / ".audit_log.jsonl"
ADMIN_KEY = os.getenv("ADMIN_KEY", "")


def _require_admin(request: Request) -> None:
    """Check admin auth via cookie or header."""
    admin_key = (
        request.headers.get("X-Admin-Key", "")
        or request.cookies.get("maxia_admin", "")
    )
    if not ADMIN_KEY or admin_key != ADMIN_KEY:
        raise HTTPException(403, "Admin access required")


def _parse_log_line(line: str) -> dict[str, Any] | None:
    """Parse a JSON log line, return None if invalid."""
    line = line.strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        # Non-JSON log line — wrap it
        return {"ts": "", "level": "RAW", "module": "unknown", "msg": line}


def _search_file(
    filepath: Path,
    level: str | None = None,
    module: str | None = None,
    query: str | None = None,
    since: int | None = None,
    until: int | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Search a log file with filters."""
    if not filepath.exists():
        return []

    results: list[dict[str, Any]] = []
    lines: list[str] = []

    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception:
        return []

    # Read from end (newest first)
    for line in reversed(lines):
        if len(results) >= limit:
            break

        entry = _parse_log_line(line)
        if entry is None:
            continue

        # Filter by level
        if level and entry.get("level", "").upper() != level.upper():
            continue

        # Filter by module
        if module and module.lower() not in entry.get("module", "").lower():
            continue

        # Filter by text query
        if query and query.lower() not in json.dumps(entry, default=str).lower():
            continue

        # Filter by time range
        ts_str = entry.get("ts", "")
        if ts_str and (since or until):
            try:
                ts_epoch = int(
                    time.mktime(time.strptime(ts_str[:19], "%Y-%m-%dT%H:%M:%S"))
                )
                if since and ts_epoch < since:
                    continue
                if until and ts_epoch > until:
                    continue
            except (ValueError, OverflowError):
                pass

        results.append(entry)

    return results


@router.get("/search")
async def search_logs(
    request: Request,
    level: str | None = Query(None, description="Filter by level: ERROR, WARNING, INFO, DEBUG"),
    module: str | None = Query(None, description="Filter by module name (partial match)"),
    q: str | None = Query(None, description="Full-text search in log messages"),
    since: int | None = Query(None, description="Unix timestamp — logs after this time"),
    until: int | None = Query(None, description="Unix timestamp — logs before this time"),
    minutes: int | None = Query(None, description="Last N minutes (alternative to since/until)"),
    limit: int = Query(100, ge=1, le=1000, description="Max results (default 100)"),
    source: str = Query("app", description="Log source: app, audit, all"),
) -> dict[str, Any]:
    """Search structured JSON logs with filters.

    Admin-only. Searches backend/logs/maxia.log and .audit_log.jsonl.
    """
    _require_admin(request)

    # Convert minutes to since
    if minutes and not since:
        since = int(time.time()) - (minutes * 60)

    results: list[dict[str, Any]] = []

    # Search app logs
    if source in ("app", "all"):
        log_files = sorted(LOG_DIR.glob("maxia.log*"), reverse=True) if LOG_DIR.exists() else []
        remaining = limit - len(results)
        for log_file in log_files:
            if remaining <= 0:
                break
            found = _search_file(log_file, level, module, q, since, until, remaining)
            results.extend(found)
            remaining = limit - len(results)

    # Search audit log
    if source in ("audit", "all"):
        remaining = limit - len(results)
        if remaining > 0 and AUDIT_LOG.exists():
            found = _search_file(AUDIT_LOG, level, module, q, since, until, remaining)
            results.extend(found)

    return {
        "count": len(results),
        "limit": limit,
        "filters": {
            "level": level,
            "module": module,
            "query": q,
            "since": since,
            "until": until,
            "source": source,
        },
        "logs": results,
    }


@router.get("/tail")
async def tail_logs(
    request: Request,
    n: int = Query(50, ge=1, le=500, description="Number of lines"),
    level: str | None = Query(None, description="Filter by level"),
    source: str = Query("app", description="app or audit"),
) -> dict[str, Any]:
    """Get last N log entries (newest first). Admin-only."""
    _require_admin(request)

    filepath = AUDIT_LOG if source == "audit" else (LOG_DIR / "maxia.log")
    results = _search_file(filepath, level=level, limit=n)

    return {
        "count": len(results),
        "source": source,
        "logs": results,
    }


@router.get("/stats")
async def log_stats(request: Request) -> dict[str, Any]:
    """Log file statistics — sizes, line counts, level distribution. Admin-only."""
    _require_admin(request)

    stats: dict[str, Any] = {"files": [], "totals": {"lines": 0, "size_bytes": 0}}
    level_counts: dict[str, int] = {}

    # App logs
    if LOG_DIR.exists():
        for log_file in sorted(LOG_DIR.glob("maxia.log*")):
            size = log_file.stat().st_size
            line_count = 0
            try:
                with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        line_count += 1
                        entry = _parse_log_line(line)
                        if entry:
                            lvl = entry.get("level", "UNKNOWN")
                            level_counts[lvl] = level_counts.get(lvl, 0) + 1
            except Exception:
                pass
            stats["files"].append({
                "name": log_file.name,
                "size_bytes": size,
                "size_human": f"{size / 1024:.1f} KB" if size < 1048576 else f"{size / 1048576:.1f} MB",
                "lines": line_count,
            })
            stats["totals"]["lines"] += line_count
            stats["totals"]["size_bytes"] += size

    # Audit log
    if AUDIT_LOG.exists():
        size = AUDIT_LOG.stat().st_size
        line_count = 0
        try:
            with open(AUDIT_LOG, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line_count += 1
        except Exception:
            pass
        stats["files"].append({
            "name": ".audit_log.jsonl",
            "size_bytes": size,
            "size_human": f"{size / 1024:.1f} KB" if size < 1048576 else f"{size / 1048576:.1f} MB",
            "lines": line_count,
        })
        stats["totals"]["lines"] += line_count
        stats["totals"]["size_bytes"] += size

    stats["level_distribution"] = level_counts
    stats["totals"]["size_human"] = (
        f"{stats['totals']['size_bytes'] / 1024:.1f} KB"
        if stats["totals"]["size_bytes"] < 1048576
        else f"{stats['totals']['size_bytes'] / 1048576:.1f} MB"
    )

    return stats


@router.get("/errors")
async def recent_errors(
    request: Request,
    minutes: int = Query(60, description="Look back N minutes"),
    limit: int = Query(50, ge=1, le=500),
) -> dict[str, Any]:
    """Shortcut: get recent ERROR and WARNING logs. Admin-only."""
    _require_admin(request)

    since = int(time.time()) - (minutes * 60)
    errors = _search_file(
        LOG_DIR / "maxia.log", level="ERROR", since=since, limit=limit
    )
    warnings = _search_file(
        LOG_DIR / "maxia.log", level="WARNING", since=since, limit=limit
    )

    return {
        "period_minutes": minutes,
        "errors": {"count": len(errors), "logs": errors},
        "warnings": {"count": len(warnings), "logs": warnings},
    }
