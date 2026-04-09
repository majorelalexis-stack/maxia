"""MAXIA CEO local — prod-only memory store (P7 Plan CEO V7).

Invariants:
- Only endpoints/features that have been VERIFIED live on the VPS are
  written here.
- Each record carries ``verified_at``, ``last_check``, and ``status``.
- Dead endpoints (3 consecutive failures) are removed automatically by
  the refresher script ``update_ceo_memory.py``.

Files:
    capabilities_prod.json   — list of endpoints currently live in prod
    outreach_channels.json   — Discord/email/Telegram channel status
    country_allowlist.json   — 28 countries allowed + geo-blocked/blocked
    quotas_daily.json        — per-channel daily rate limits
    ban_history.json         — incidents for learning
    successful_templates.json — messages with response_rate > 5%
    failed_templates.json    — messages with response_rate < 1% (avoid)
"""
from local_ceo.memory_prod.store import (
    CapabilityRecord,
    CapabilityStatus,
    MemoryStore,
    load_json,
    save_json,
)

__all__ = [
    "CapabilityRecord",
    "CapabilityStatus",
    "MemoryStore",
    "load_json",
    "save_json",
]
