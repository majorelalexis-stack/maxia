"""MAXIA Health Monitor — Checks all endpoints every 5 minutes"""
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

async def run_health_monitor():
    """Check critical endpoints every 5 minutes. Alert on failure."""
    logger.info("[HealthMonitor] Started — checking 6 endpoints every 5 min")
    while True:
        await asyncio.sleep(300)  # 5 minutes
        failures = []
        # Use X-Internal header so middleware can exempt localhost health checks
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{PORT}", timeout=10,
            headers={"X-Internal": "health-monitor", "User-Agent": "MAXIA-HealthMonitor/1.0"},
        ) as client:
            for path, expected in ENDPOINTS:
                try:
                    r = await client.get(path)
                    # 429 = rate limit, pas une panne (le endpoint fonctionne)
                    if r.status_code != expected and r.status_code != 429:
                        failures.append(f"{path}: {r.status_code}")
                except Exception as e:
                    failures.append(f"{path}: {e}")
        if failures:
            logger.error(f"[HealthMonitor] {len(failures)} FAILURES: {failures}")
            try:
                from infra.alerts import alert_error
                await alert_error("HealthMonitor", f"{len(failures)} endpoints down: {', '.join(failures)}")
            except Exception:
                pass
        else:
            # Log OK every hour (not every 5 min to reduce noise)
            if int(time.time()) % 3600 < 300:
                logger.info(f"[HealthMonitor] All {len(ENDPOINTS)} endpoints OK")
