"""CEO local — refresh memory_prod/capabilities_prod.json from live VPS.

Scans a known set of endpoints on the VPS and records live/degraded/dead
status in ``local_ceo/memory_prod/capabilities_prod.json``. Endpoints that
fail 3 consecutive checks are pruned automatically.

Run manually (``python -m local_ceo.update_ceo_memory``) or wire it to a
cron job every 4 hours.

Zero side-effects on the VPS: only GET endpoints with small payloads are
probed. POST endpoints are listed but marked ``method="POST"`` and skipped
for health checks (they can't be pinged safely).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from typing import Final

logger = logging.getLogger("maxia.ceo.update_memory")

# Ensure we can import local_ceo.memory_prod when run as a script
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from local_ceo.memory_prod.store import MemoryStore  # noqa: E402


# ── Config ──
DEFAULT_VPS_URL: Final[str] = os.getenv("VPS_URL", "https://maxiaworld.app")
DEFAULT_TIMEOUT: Final[float] = 10.0
MEMORY_DIR: Final[str] = os.path.join(_HERE, "memory_prod")
CAPABILITIES_PATH: Final[str] = os.path.join(MEMORY_DIR, "capabilities_prod.json")


# ── Endpoints to probe ──
# Only GET endpoints that are safe to hit repeatedly and don't require auth.
_PROBES: Final[list[dict]] = [
    {"path": "/health", "description": "Global backend health"},
    {"path": "/oracle/specs", "description": "Oracle configuration specs"},
    {"path": "/oracle/monitoring", "description": "Oracle P50/P95/P99 latency"},
    {"path": "/api/ceo/gateway/status", "description": "PicoClaw CEO gateway"},
    {"path": "/api/bots/leaderboard", "description": "Agent leaderboard"},
    {"path": "/mcp/manifest", "description": "MCP server manifest"},
]


async def _probe_one(
    client: "httpx.AsyncClient",
    base_url: str,
    endpoint: dict,
    store: MemoryStore,
) -> tuple[str, bool]:
    """Probe a single endpoint and update the store accordingly."""
    import httpx

    path = endpoint["path"]
    description = endpoint.get("description", "")
    url = f"{base_url.rstrip('/')}{path}"

    start = time.perf_counter()
    try:
        resp = await client.get(url, timeout=DEFAULT_TIMEOUT)
        latency_ms = (time.perf_counter() - start) * 1000.0
        if 200 <= resp.status_code < 300:
            store.upsert_success(
                endpoint=path,
                description=description,
                method="GET",
                latency_ms=latency_ms,
            )
            logger.info("[probe] OK  %s (%.0f ms)", path, latency_ms)
            return path, True
        logger.warning("[probe] HTTP %d on %s", resp.status_code, path)
    except (httpx.HTTPError, httpx.TimeoutException, OSError) as e:
        logger.warning("[probe] error on %s: %s", path, e)

    store.upsert_failure(endpoint=path)
    return path, False


async def refresh_memory(base_url: str = DEFAULT_VPS_URL) -> dict:
    """Probe all known endpoints and return summary stats.

    Pure async — no global state — safe to call from tests with a mock
    HTTP server.
    """
    import httpx

    store = MemoryStore(capabilities_path=CAPABILITIES_PATH)

    async with httpx.AsyncClient(follow_redirects=True) as client:
        results = await asyncio.gather(
            *(_probe_one(client, base_url, ep, store) for ep in _PROBES),
            return_exceptions=False,
        )

    ok_count = sum(1 for _, ok in results if ok)
    stats = store.stats()
    return {
        "probed": len(_PROBES),
        "ok": ok_count,
        "fail": len(_PROBES) - ok_count,
        "store_total": stats["total"],
        "store_live": stats["live"],
        "store_degraded": stats["degraded"],
    }


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    parser = argparse.ArgumentParser(description="Refresh CEO memory_prod")
    parser.add_argument("--vps-url", default=DEFAULT_VPS_URL)
    args = parser.parse_args()

    summary = asyncio.run(refresh_memory(args.vps_url))
    logger.info("[done] %s", summary)
    # Non-zero exit if any probe failed — useful for cron/monitoring.
    return 0 if summary["fail"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
