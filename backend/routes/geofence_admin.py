"""Geofence admin endpoints — runtime rules + audit log introspection.

Exposes under ``/api/admin/geofence/*`` so Alexis can:

* Inspect the current tier rules (``GET /rules``)
* Force a hot reload of the YAML source (``POST /reload``)
* Inspect the audit log stats + top blocked countries/paths
* Query recent decisions for a country or action prefix
* Trigger log rotation (archive old rows)
* Check middleware health / kill switch status

All endpoints require the existing admin auth (``X-Admin-Key`` header
via :func:`core.security.require_admin`) — no new auth surface.

This router is **read-mostly**: modifying the country tiers goes
through direct editing of ``country_tiers.yaml`` then hitting the
``/reload`` endpoint. This avoids a whole CRUD surface for a file
that should change < once a month.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from core.security import require_admin
from core.error_utils import safe_error

log = logging.getLogger("maxia.geofence.admin")

router = APIRouter(prefix="/api/admin/geofence", tags=["admin-geofence"])


@router.get("/status")
async def geofence_status(request: Request) -> JSONResponse:
    """Return middleware health + kill switch state + data source stats."""
    require_admin(request)
    try:
        from compliance.country_filter import stats as filter_stats
        from core.geo_blocking import GEOFENCE_ENABLED, _GEOIP_DB_PATH

        data = {
            "enabled": bool(GEOFENCE_ENABLED),
            "kill_switch_env": "GEOFENCE_ENABLED (default=1)",
            "maxmind_db_path": str(_GEOIP_DB_PATH),
            "maxmind_db_present": _GEOIP_DB_PATH.exists(),
            "country_filter": filter_stats(),
        }
        return JSONResponse({"ok": True, **data})
    except Exception as e:
        return JSONResponse(safe_error(e, "geofence_status"), status_code=500)


@router.get("/rules")
async def geofence_rules(request: Request) -> JSONResponse:
    """Dump the current 4-tier country classification."""
    require_admin(request)
    try:
        from compliance.country_filter import (
            _country_index,
            _feature_gates,
            _load_yaml_data,
        )
        _load_yaml_data()
        by_tier: dict[str, list[dict[str, str]]] = {
            "hard": [], "license": [], "caution": [], "allowed": [],
        }
        for entry in _country_index.values():
            by_tier.setdefault(entry.tier, []).append({
                "code": entry.code,
                "name": entry.name,
                "reason": entry.reason,
                "regulator": entry.regulator,
                "notes": entry.notes,
            })
        for key in by_tier:
            by_tier[key].sort(key=lambda e: e["code"])
        return JSONResponse({
            "ok": True,
            "total": len(_country_index),
            "by_tier": {
                k: {"count": len(v), "countries": v}
                for k, v in by_tier.items()
            },
            "feature_gates": _feature_gates,
        })
    except Exception as e:
        return JSONResponse(safe_error(e, "geofence_rules"), status_code=500)


@router.post("/reload")
async def geofence_reload(request: Request) -> JSONResponse:
    """Force a full hot-reload of ``country_tiers.yaml``.

    Use this after editing the YAML file on the running server —
    the middleware will pick up the new rules on the next request
    without a restart.
    """
    require_admin(request)
    try:
        from compliance.country_filter import reload_data
        result = reload_data()
        log.info("[geofence_admin] reload requested, result=%s", result)
        return JSONResponse({"ok": True, **result})
    except Exception as e:
        return JSONResponse(safe_error(e, "geofence_reload"), status_code=500)


@router.get("/stats")
async def geofence_log_stats(request: Request) -> JSONResponse:
    """Audit log statistics for the dashboard widget."""
    require_admin(request)
    try:
        from compliance.geofence_log import stats as log_stats
        return JSONResponse({"ok": True, **(await log_stats())})
    except Exception as e:
        return JSONResponse(safe_error(e, "geofence_stats"), status_code=500)


@router.get("/top-countries")
async def geofence_top_countries(
    request: Request,
    limit: int = Query(10, ge=1, le=100),
    days: int = Query(7, ge=1, le=365),
) -> JSONResponse:
    """Top blocked countries in the given window."""
    require_admin(request)
    try:
        from compliance.geofence_log import top_blocked_countries
        rows = await top_blocked_countries(
            limit=limit, since_s=days * 86400,
        )
        return JSONResponse({"ok": True, "days": days, "rows": rows})
    except Exception as e:
        return JSONResponse(safe_error(e, "geofence_top_countries"), status_code=500)


@router.get("/top-paths")
async def geofence_top_paths(
    request: Request,
    limit: int = Query(10, ge=1, le=100),
    days: int = Query(7, ge=1, le=365),
) -> JSONResponse:
    """Top blocked paths in the given window."""
    require_admin(request)
    try:
        from compliance.geofence_log import top_blocked_paths
        rows = await top_blocked_paths(
            limit=limit, since_s=days * 86400,
        )
        return JSONResponse({"ok": True, "days": days, "rows": rows})
    except Exception as e:
        return JSONResponse(safe_error(e, "geofence_top_paths"), status_code=500)


@router.get("/log")
async def geofence_log_recent(
    request: Request,
    limit: int = Query(100, ge=1, le=1000),
    country: Optional[str] = Query(None, regex=r"^[A-Za-z]{2}$"),
    action: Optional[str] = Query(None, regex=r"^[a-z_]{1,30}$"),
) -> JSONResponse:
    """Return recent audit log entries (read-only)."""
    require_admin(request)
    try:
        from compliance.geofence_log import recent_entries
        rows = await recent_entries(
            limit=limit,
            country=country.upper() if country else None,
            action_prefix=action,
        )
        return JSONResponse({"ok": True, "rows": rows, "count": len(rows)})
    except Exception as e:
        return JSONResponse(safe_error(e, "geofence_log_recent"), status_code=500)


@router.post("/rotate")
async def geofence_rotate(
    request: Request,
    retention_days: int = Query(90, ge=1, le=3650),
) -> JSONResponse:
    """Move rows older than ``retention_days`` to the archive table.

    Meant to be called manually or by a cron job. Idempotent — safe to
    retry if interrupted.
    """
    require_admin(request)
    try:
        from compliance.geofence_log import rotate_old_rows
        moved = await rotate_old_rows(retention_days=retention_days)
        log.info(
            "[geofence_admin] rotated %d rows (retention=%d days)",
            moved, retention_days,
        )
        return JSONResponse({"ok": True, "rotated": moved})
    except Exception as e:
        return JSONResponse(safe_error(e, "geofence_rotate"), status_code=500)


@router.get("/check")
async def geofence_check(
    request: Request,
    country: str = Query(..., regex=r"^[A-Za-z]{2}$"),
    feature: str = Query("trading"),
) -> JSONResponse:
    """Test a (country, feature) pair against the current rules.

    Useful for debugging or verifying that a YAML change has the
    intended effect before users are impacted.
    """
    require_admin(request)
    try:
        from compliance.country_filter import (
            get_tier,
            check_feature,
            get_country_entry,
            is_allowed,
        )
        tier = get_tier(country)
        decision = check_feature(country, feature)  # type: ignore[arg-type]
        entry = get_country_entry(country)
        legacy = is_allowed(country, feature)
        return JSONResponse({
            "ok": True,
            "country": country.upper(),
            "feature": feature,
            "tier": tier,
            "decision": decision,
            "legacy": {
                "allowed": legacy.allowed,
                "code": legacy.code,
                "reason": legacy.reason,
            },
            "entry": (
                {
                    "code": entry.code,
                    "name": entry.name,
                    "tier": entry.tier,
                    "reason": entry.reason,
                    "regulator": entry.regulator,
                    "notes": entry.notes,
                }
                if entry else None
            ),
        })
    except Exception as e:
        return JSONResponse(safe_error(e, "geofence_check"), status_code=500)


log.info("[geofence_admin] router mounted — 8 endpoints")
