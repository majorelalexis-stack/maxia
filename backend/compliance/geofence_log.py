"""Geofence audit log — async wrapper over core.database.

Uses the existing MAXIA database abstraction so the same queries run
transparently on SQLite (dev) and PostgreSQL (prod). The schema lives
in ``core.database.Database.MIGRATIONS[15]`` and is applied
automatically on startup.

IP addresses are SHA-256 hashed with a salt so we prove we blocked a
connection without storing PII (GDPR data minimization).

All functions are **best-effort**: a logging failure never raises, so
a DB outage cannot break the geofence middleware. The middleware
calls :func:`log_decision` inside a try/except as additional safety.
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
from typing import Any, Optional

log = logging.getLogger("maxia.geofence.log")

_SALT: str = os.getenv(
    "GEOFENCE_LOG_SALT",
    "maxia-geofence-2026-04-11-rotate-yearly",
)


def _hash_ip(ip: str) -> str:
    """Return the first 16 hex chars of SHA-256(salt + ip). GDPR-safe."""
    if not ip:
        return ""
    return hashlib.sha256(f"{_SALT}:{ip}".encode("utf-8")).hexdigest()[:16]


async def log_decision(
    *,
    ip: str,
    country: str,
    tier: str,
    route_class: str,
    path: str,
    action: str,
    method: str = "GET",
    user_agent: str = "",
    session_id: str = "",
) -> None:
    """Append one decision to ``geofence_log``. Best-effort, never raises."""
    try:
        from core.database import db
        await db.raw_execute(
            "INSERT INTO geofence_log "
            "(ts, ip_hash, country, tier, route_class, path, method, action, user_agent, session_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                int(time.time()),
                _hash_ip(ip),
                (country or "")[:2].upper(),
                (tier or "")[:20],
                (route_class or "")[:20],
                (path or "")[:255],
                (method or "GET")[:10],
                (action or "")[:50],
                (user_agent or "")[:255],
                (session_id or "")[:64],
            ),
        )
    except Exception as e:
        log.debug("[geofence_log] insert failed: %s", e)


# ══════════════════════════════════════════
# Admin queries (read-only)
# ══════════════════════════════════════════


async def count_last_24h() -> dict[str, int]:
    """Count decisions by action in the last 24 hours."""
    try:
        from core.database import db
        cutoff = int(time.time()) - 86400
        rows = await db.raw_execute_fetchall(
            "SELECT action, COUNT(*) AS n FROM geofence_log "
            "WHERE ts >= ? GROUP BY action",
            (cutoff,),
        )
        return {r["action"]: int(r["n"]) for r in rows}
    except Exception as e:
        log.debug("[geofence_log] count_last_24h failed: %s", e)
        return {}


async def top_blocked_countries(
    limit: int = 10,
    since_s: int = 86400 * 7,
) -> list[dict[str, Any]]:
    """Top blocked countries in the last ``since_s`` seconds."""
    try:
        from core.database import db
        cutoff = int(time.time()) - since_s
        rows = await db.raw_execute_fetchall(
            "SELECT country, tier, COUNT(*) AS n FROM geofence_log "
            "WHERE ts >= ? AND action LIKE 'block_%' "
            "GROUP BY country, tier ORDER BY n DESC LIMIT ?",
            (cutoff, max(1, min(int(limit), 100))),
        )
        return [
            {"country": r["country"], "tier": r["tier"], "count": int(r["n"])}
            for r in rows
        ]
    except Exception as e:
        log.debug("[geofence_log] top_blocked_countries failed: %s", e)
        return []


async def top_blocked_paths(
    limit: int = 10,
    since_s: int = 86400 * 7,
) -> list[dict[str, Any]]:
    """Top blocked paths in the last ``since_s`` seconds."""
    try:
        from core.database import db
        cutoff = int(time.time()) - since_s
        rows = await db.raw_execute_fetchall(
            "SELECT path, COUNT(*) AS n FROM geofence_log "
            "WHERE ts >= ? AND action LIKE 'block_%' "
            "GROUP BY path ORDER BY n DESC LIMIT ?",
            (cutoff, max(1, min(int(limit), 100))),
        )
        return [{"path": r["path"], "count": int(r["n"])} for r in rows]
    except Exception as e:
        log.debug("[geofence_log] top_blocked_paths failed: %s", e)
        return []


async def stats() -> dict[str, Any]:
    """Aggregate stats for the admin dashboard."""
    try:
        from core.database import db
        total_rows = await db.raw_execute_fetchall(
            "SELECT COUNT(*) AS n FROM geofence_log"
        )
        archive_rows = await db.raw_execute_fetchall(
            "SELECT COUNT(*) AS n FROM geofence_log_archive"
        )
        tier_rows = await db.raw_execute_fetchall(
            "SELECT tier, COUNT(*) AS n FROM geofence_log GROUP BY tier"
        )
        action_rows = await db.raw_execute_fetchall(
            "SELECT action, COUNT(*) AS n FROM geofence_log GROUP BY action"
        )
        last_24h = await count_last_24h()
        return {
            "total_rows": int(total_rows[0]["n"]) if total_rows else 0,
            "archived_rows": int(archive_rows[0]["n"]) if archive_rows else 0,
            "by_tier": {r["tier"]: int(r["n"]) for r in tier_rows},
            "by_action": {r["action"]: int(r["n"]) for r in action_rows},
            "last_24h": last_24h,
        }
    except Exception as e:
        log.debug("[geofence_log] stats failed: %s", e)
        return {"error": str(e)}


async def recent_entries(
    limit: int = 100,
    country: Optional[str] = None,
    action_prefix: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Return recent log entries for the admin UI (read-only)."""
    try:
        from core.database import db
        base = (
            "SELECT ts, ip_hash, country, tier, route_class, path, method, action "
            "FROM geofence_log"
        )
        conditions: list[str] = []
        params: list[Any] = []
        if country:
            conditions.append("country = ?")
            params.append(country.upper()[:2])
        if action_prefix:
            conditions.append("action LIKE ?")
            params.append(f"{action_prefix}%")
        if conditions:
            base += " WHERE " + " AND ".join(conditions)
        base += " ORDER BY ts DESC LIMIT ?"
        params.append(max(1, min(int(limit), 1000)))
        rows = await db.raw_execute_fetchall(base, tuple(params))
        return [dict(r) for r in rows]
    except Exception as e:
        log.debug("[geofence_log] recent_entries failed: %s", e)
        return []


async def rotate_old_rows(retention_days: int = 90) -> int:
    """Move rows older than ``retention_days`` to the archive table.

    Returns the number of rows moved. Safe to call idempotently — if
    a run is interrupted mid-way, the next run will continue from
    where it left off.
    """
    try:
        from core.database import db
        cutoff = int(time.time()) - retention_days * 86400
        before = await db.raw_execute_fetchall(
            "SELECT COUNT(*) AS n FROM geofence_log WHERE ts < ?",
            (cutoff,),
        )
        count_before = int(before[0]["n"]) if before else 0
        if count_before == 0:
            return 0
        await db.raw_execute(
            "INSERT INTO geofence_log_archive "
            "(id, ts, ip_hash, country, tier, route_class, path, method, action, user_agent, session_id) "
            "SELECT id, ts, ip_hash, country, tier, route_class, path, method, action, user_agent, session_id "
            "FROM geofence_log WHERE ts < ?",
            (cutoff,),
        )
        await db.raw_execute(
            "DELETE FROM geofence_log WHERE ts < ?",
            (cutoff,),
        )
        log.info(
            "[geofence_log] rotated %d rows older than %d days",
            count_before, retention_days,
        )
        return count_before
    except Exception as e:
        log.error("[geofence_log] rotate failed: %s", e)
        return 0
