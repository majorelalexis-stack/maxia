"""MAXIA Health Monitor — Checks endpoints (5min) + infra (60s) with escalation"""
import logging
import asyncio, time
import httpx
from core.config import PORT

logger = logging.getLogger(__name__)

ENDPOINTS = [
    ("/health", 200),
    ("/api/public/crypto/prices", 200),
    ("/api/public/stocks", 200),
    ("/api/public/leaderboard", 200),
    ("/mcp/tools", 200),
    ("/.well-known/agent.json", 200),
    ("/api/public/forum", 200),
    ("/api/public/defi/best-yield", 200),
    ("/api/public/chains", 200),
    ("/api/public/forum/notifications/count?wallet=monitor", 200),
    ("/oracle/specs", 200),
]

# Track consecutive failures for escalation
_failure_counts: dict[str, int] = {}
_ESCALATION_THRESHOLD = 2  # Alert after 2 consecutive failures


async def _check_infra() -> list[str]:
    """Check Redis and PostgreSQL connectivity. Returns list of failures."""
    failures = []
    # Redis check
    try:
        from core.redis_client import redis_client
        if redis_client.is_connected:
            await redis_client._redis.ping()
        # If not connected, it's using in-memory fallback — not a failure
    except Exception as e:
        failures.append(f"Redis: {e}")
    # DB check
    try:
        from core.database import db
        await db._fetchone("SELECT 1")
    except Exception as e:
        failures.append(f"Database: {e}")
    return failures


async def _handle_result(name: str, failed: bool, detail: str = ""):
    """Handle check result with escalation and recovery logic."""
    prev_count = _failure_counts.get(name, 0)
    if failed:
        _failure_counts[name] = prev_count + 1
        count = _failure_counts[name]
        if count == 1:
            logger.warning("[HealthMonitor] %s — FAILING (1st): %s", name, detail)
        elif count == _ESCALATION_THRESHOLD:
            logger.error("[HealthMonitor] %s — ALERT (consecutive failures): %s", name, detail)
            try:
                from infra.alerts import alert_error
                await alert_error("HealthMonitor", f"{name} down ({count}x): {detail}")
            except Exception:
                pass
        # Don't spam alerts after escalation — log only
        elif count > _ESCALATION_THRESHOLD and count % 5 == 0:
            logger.error("[HealthMonitor] %s — still failing (%dx)", name, count)
    else:
        if prev_count >= _ESCALATION_THRESHOLD:
            # Was failing, now recovered — send recovery notification
            logger.info("[HealthMonitor] %s — RECOVERED after %d failures", name, prev_count)
            try:
                from infra.alerts import alert_error
                await alert_error("HealthMonitor", f"{name} RECOVERED after {prev_count} consecutive failures")
            except Exception:
                pass
        _failure_counts[name] = 0


async def _check_endpoints():
    """Check HTTP endpoints."""
    async with httpx.AsyncClient(
        base_url=f"http://127.0.0.1:{PORT}", timeout=10,
        headers={"X-Internal": "health-monitor", "User-Agent": "MAXIA-HealthMonitor/1.0"},
    ) as client:
        for path, expected in ENDPOINTS:
            try:
                r = await client.get(path)
                failed = r.status_code != expected and r.status_code != 429
                detail = f"{path}: {r.status_code}" if failed else ""
                await _handle_result(f"endpoint:{path}", failed, detail)
            except Exception as e:
                await _handle_result(f"endpoint:{path}", True, f"{path}: {e}")


async def run_health_monitor():
    """Check infra every 60s, endpoints every 5 minutes. Alert on escalated failures."""
    logger.info("[HealthMonitor] Started — infra checks 60s, endpoints 5min")
    cycle = 0
    while True:
        await asyncio.sleep(60)
        cycle += 1
        # Infra checks every 60 seconds
        infra_failures = await _check_infra()
        for f in infra_failures:
            name = f.split(":")[0].strip()
            await _handle_result(f"infra:{name}", True, f)
        # Mark infra as OK if no failures
        for name in ("Redis", "Database"):
            if not any(f.startswith(name) for f in infra_failures):
                await _handle_result(f"infra:{name}", False)
        # Endpoint checks every 5 minutes (cycle 5 = 5*60s = 300s)
        if cycle % 5 == 0:
            await _check_endpoints()
            # Log OK summary hourly (cycle 60 = 60*60s = 3600s)
            if cycle % 60 == 0:
                total_failing = sum(1 for v in _failure_counts.values() if v > 0)
                if total_failing == 0:
                    logger.info("[HealthMonitor] All checks OK")
                cycle = 0  # Reset to avoid overflow
