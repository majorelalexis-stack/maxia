"""Live prices cache for the local CEO.

Fetches authoritative pricing from the MAXIA VPS every 15 minutes and
exposes it to downstream consumers (price_watcher, sales_agent) so they
never quote stale hardcoded values to prospects or to the competitor
monitor.

Safe-by-default: if the VPS is unreachable on any fetch (cold start or
refresh), the module returns the last successful snapshot. If no
successful fetch has happened yet, it returns the ``_FALLBACK`` dict
(conservative defaults) and logs a warning so CEO reasoning never
crashes on a network hiccup.

Three consumers
---------------
* ``get_live_gpu_tiers()``    — list of GPU tiers with live Akash
  markup, in the shape ``sales_agent`` expects
  (``tier``, ``vram_gb``, ``price_per_hour_usd``).
* ``get_live_maxia_prices()`` — flat dict in the shape
  ``price_watcher.MAXIA_PRICES`` expects
  (``gpu_rtx4090``, ``gpu_a100``, ``gpu_h100``, ``swap_fee_bps``,
  ``audit_basic``).
* ``get_live_crypto_prices()`` — symbol → float USD, for any future
  consumer that wants live token prices.

API is async-only. TTL is 15 minutes (900 s).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

log = logging.getLogger("maxia.local_ceo.live_prices")

# ── Config ─────────────────────────────────────────────────────────────

VPS_BASE: str = os.getenv("MAXIA_VPS_BASE", "https://maxiaworld.app").rstrip("/")
_TTL_SECONDS: float = 15 * 60  # 15 minutes
_FETCH_TIMEOUT: float = 8.0

# Conservative hardcoded defaults — ONLY used before the first successful
# fetch, i.e. if the CEO starts up offline. Every successful refresh
# replaces them with live values.
_FALLBACK: dict[str, Any] = {
    "gpu_tiers": [
        {"tier": "RTX4090", "vram_gb": 24, "price_per_hour_usd": 0.46},
        {"tier": "A100",    "vram_gb": 80, "price_per_hour_usd": 1.19},
        {"tier": "H100",    "vram_gb": 80, "price_per_hour_usd": 2.69},
    ],
    "maxia_prices": {
        "gpu_rtx4090": 0.46,
        "gpu_a100": 1.19,
        "gpu_h100": 2.69,
        "swap_fee_bps": 10,
        "audit_basic": 1.0,
    },
    "crypto": {},
    "updated_at": 0.0,
    "source": "fallback",
}


# ── In-memory cache ────────────────────────────────────────────────────

_cache: dict[str, Any] = dict(_FALLBACK)
_cache["updated_at"] = 0.0
_refresh_lock = asyncio.Lock()


def _is_stale() -> bool:
    """Return True if the cache is older than the TTL."""
    return (time.time() - float(_cache.get("updated_at") or 0.0)) > _TTL_SECONDS


# ── Transform helpers ──────────────────────────────────────────────────

def _normalize_gpu_tiers(raw_tiers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate the VPS ``/api/public/gpu/tiers`` shape into the shape
    the sales agent prompt expects. Drops any tier missing a price."""
    out: list[dict[str, Any]] = []
    for t in raw_tiers or []:
        if not isinstance(t, dict):
            continue
        price = t.get("price_per_hour_usdc")
        if price is None:
            price = t.get("price_per_hour_usd")
        if price is None or float(price) <= 0:
            continue
        label = str(t.get("label") or t.get("id") or "").strip()
        if not label:
            continue
        try:
            vram = int(t.get("vram_gb") or 0)
        except (TypeError, ValueError):
            vram = 0
        out.append({
            "tier": label,
            "vram_gb": vram,
            "price_per_hour_usd": round(float(price), 4),
            "available": bool(t.get("available")),
            "provider": t.get("provider") or "akash",
        })
    return out


def _extract_maxia_prices(
    gpu_tiers: list[dict[str, Any]],
    base_prices: dict[str, Any],
) -> dict[str, Any]:
    """Pick the handful of values ``price_watcher.MAXIA_PRICES`` uses."""
    by_id: dict[str, float] = {}
    for t in gpu_tiers or []:
        tier = str(t.get("tier", "")).lower().replace(" ", "")
        price = t.get("price_per_hour_usd")
        if tier and price is not None:
            by_id[tier] = float(price)

    swap_tiers = (base_prices or {}).get("swap_commission_tiers", {}) or {}
    bronze = swap_tiers.get("BRONZE", {}) if isinstance(swap_tiers, dict) else {}
    swap_fee_bps = int(bronze.get("bps") or _FALLBACK["maxia_prices"]["swap_fee_bps"])

    service_prices = (base_prices or {}).get("service_prices", {}) or {}
    audit_basic = float(
        service_prices.get("maxia-audit")
        or service_prices.get("maxia-code-review")
        or _FALLBACK["maxia_prices"]["audit_basic"]
    )

    return {
        "gpu_rtx4090": by_id.get("rtx4090", _FALLBACK["maxia_prices"]["gpu_rtx4090"]),
        "gpu_a100": by_id.get("a100") or by_id.get("a100_80") or _FALLBACK["maxia_prices"]["gpu_a100"],
        "gpu_h100": by_id.get("h100") or by_id.get("h100_sxm") or _FALLBACK["maxia_prices"]["gpu_h100"],
        "swap_fee_bps": swap_fee_bps,
        "audit_basic": audit_basic,
    }


def _extract_crypto_map(crypto_raw: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for sym, info in (crypto_raw or {}).get("prices", {}).items():
        if not isinstance(info, dict):
            continue
        try:
            price = float(info.get("price") or 0)
        except (TypeError, ValueError):
            continue
        if price > 0:
            out[str(sym).upper()] = price
    return out


# ── Fetchers ───────────────────────────────────────────────────────────

async def _fetch_json(url: str) -> dict[str, Any] | None:
    """GET + parse JSON. Returns None on any failure. Never raises."""
    try:
        import httpx  # deferred so a missing httpx can't break import
    except Exception as e:
        log.warning("[live_prices] httpx unavailable: %s", e)
        return None

    try:
        async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        log.warning("[live_prices] fetch %s failed: %s", url, e)
        return None


async def _refresh_once() -> bool:
    """Do one fetch cycle. Updates ``_cache`` only on full success.
    Returns True if the cache was updated."""
    gpu_raw = await _fetch_json(f"{VPS_BASE}/api/public/gpu/tiers")
    base_raw = await _fetch_json(f"{VPS_BASE}/api/public/prices")
    crypto_raw = await _fetch_json(f"{VPS_BASE}/api/public/crypto/prices")

    # We need at least the GPU tiers — base + crypto are optional.
    if not gpu_raw or not isinstance(gpu_raw.get("tiers"), list):
        log.warning("[live_prices] refresh skipped: gpu tiers missing")
        return False

    tiers = _normalize_gpu_tiers(gpu_raw.get("tiers", []))
    if not tiers:
        log.warning("[live_prices] refresh skipped: 0 usable gpu tiers")
        return False

    maxia_prices = _extract_maxia_prices(tiers, base_raw or {})
    crypto_map = _extract_crypto_map(crypto_raw or {})

    _cache["gpu_tiers"] = tiers
    _cache["maxia_prices"] = maxia_prices
    _cache["crypto"] = crypto_map
    _cache["updated_at"] = time.time()
    _cache["source"] = "vps_live"
    log.info(
        "[live_prices] refreshed: %d gpu_tiers, %d crypto, rtx4090=$%.2f/h",
        len(tiers), len(crypto_map), maxia_prices.get("gpu_rtx4090", 0),
    )
    return True


async def _maybe_refresh() -> None:
    """Refresh if stale, under a lock so concurrent callers don't
    hammer the VPS."""
    if not _is_stale():
        return
    async with _refresh_lock:
        if not _is_stale():
            return  # another coroutine refreshed while we were waiting
        await _refresh_once()


# ── Public API ─────────────────────────────────────────────────────────

async def force_refresh() -> bool:
    """Force a refresh regardless of TTL. Returns True if cache was
    updated. Safe to call from anywhere."""
    async with _refresh_lock:
        return await _refresh_once()


async def get_live_gpu_tiers() -> list[dict[str, Any]]:
    """Return the current live GPU tiers in ``sales_agent`` shape."""
    await _maybe_refresh()
    return list(_cache.get("gpu_tiers") or [])


async def get_live_maxia_prices() -> dict[str, Any]:
    """Return the MAXIA_PRICES dict for ``price_watcher``."""
    await _maybe_refresh()
    return dict(_cache.get("maxia_prices") or {})


async def get_live_crypto_prices() -> dict[str, float]:
    """Return symbol → USD price for supported tokens."""
    await _maybe_refresh()
    return dict(_cache.get("crypto") or {})


async def get_live_snapshot() -> dict[str, Any]:
    """Return the full cache (debug / diagnostics)."""
    await _maybe_refresh()
    return {
        "gpu_tiers": list(_cache.get("gpu_tiers") or []),
        "maxia_prices": dict(_cache.get("maxia_prices") or {}),
        "crypto_count": len(_cache.get("crypto") or {}),
        "updated_at": _cache.get("updated_at"),
        "source": _cache.get("source"),
        "ttl_seconds": _TTL_SECONDS,
    }
