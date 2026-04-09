"""MAXIA CEO memory_prod — file-backed immutable records (P7 Plan CEO V7).

All records are frozen dataclasses that serialize to JSON. The store
guarantees:

- Atomic writes (write to temp file, then os.replace)
- Thread-safe across the CEO's missions
- Automatic pruning of endpoints with 3 consecutive failures
- Per-record ``verified_at`` / ``last_check`` timestamps
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field, replace
from typing import Final, Literal, Optional

CapabilityStatus = Literal["live", "degraded", "dead"]

MAX_CONSECUTIVE_FAILURES: Final[int] = 3


@dataclass(frozen=True)
class CapabilityRecord:
    """Immutable record of a verified production capability."""
    endpoint: str               # e.g. "/api/tg/prices"
    description: str            # human-readable
    method: str                 # "GET" | "POST" | "PUT" | "DELETE"
    status: CapabilityStatus    # live | degraded | dead
    verified_at: int            # first verified unix ts
    last_check: int             # latest health check unix ts
    success_count: int = 0
    consecutive_failures: int = 0
    last_latency_ms: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "CapabilityRecord":
        # Be tolerant of missing optional fields
        return cls(
            endpoint=str(data["endpoint"]),
            description=str(data.get("description", "")),
            method=str(data.get("method", "GET")).upper(),
            status=data.get("status", "live"),
            verified_at=int(data.get("verified_at", 0)),
            last_check=int(data.get("last_check", 0)),
            success_count=int(data.get("success_count", 0)),
            consecutive_failures=int(data.get("consecutive_failures", 0)),
            last_latency_ms=float(data.get("last_latency_ms", 0.0)),
        )


# ── Low-level IO ──


def load_json(path: str, default: object) -> object:
    """Load JSON from ``path``, returning ``default`` on error or missing."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def save_json(path: str, data: object) -> None:
    """Atomic write — temp file + os.replace so partial writes never land."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    dir_ = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(prefix=".mem_tmp_", suffix=".json", dir=dir_)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ── High-level store ──


@dataclass
class MemoryStore:
    """File-backed store for CEO production capabilities.

    Thread-safe via an internal lock. Persist-on-write.
    """
    capabilities_path: str
    _records: dict[str, CapabilityRecord] = field(default_factory=dict, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def __post_init__(self) -> None:
        self.reload()

    # ── Load / persist ──

    def reload(self) -> None:
        with self._lock:
            raw = load_json(self.capabilities_path, default={"capabilities": []})
            if not isinstance(raw, dict):
                raw = {"capabilities": []}
            items = raw.get("capabilities", []) if isinstance(raw, dict) else []
            self._records = {}
            for item in items:
                try:
                    record = CapabilityRecord.from_dict(item)
                    self._records[record.endpoint] = record
                except (KeyError, TypeError, ValueError):
                    continue

    def _persist_locked(self) -> None:
        payload = {
            "version": 1,
            "updated_at": int(time.time()),
            "count": len(self._records),
            "capabilities": [r.to_dict() for r in self._records.values()],
        }
        save_json(self.capabilities_path, payload)

    # ── Queries ──

    def get(self, endpoint: str) -> Optional[CapabilityRecord]:
        with self._lock:
            return self._records.get(endpoint)

    def all_live(self) -> list[CapabilityRecord]:
        with self._lock:
            return [r for r in self._records.values() if r.status == "live"]

    def all(self) -> list[CapabilityRecord]:
        with self._lock:
            return list(self._records.values())

    def count(self) -> int:
        with self._lock:
            return len(self._records)

    # ── Mutations ──

    def upsert_success(
        self,
        endpoint: str,
        description: str,
        method: str,
        latency_ms: float,
        now: Optional[int] = None,
    ) -> CapabilityRecord:
        """Record a successful health check. Adds the endpoint if new."""
        ts = int(now if now is not None else time.time())
        with self._lock:
            existing = self._records.get(endpoint)
            if existing is None:
                record = CapabilityRecord(
                    endpoint=endpoint,
                    description=description,
                    method=method.upper(),
                    status="live",
                    verified_at=ts,
                    last_check=ts,
                    success_count=1,
                    consecutive_failures=0,
                    last_latency_ms=float(latency_ms),
                )
            else:
                record = replace(
                    existing,
                    description=description or existing.description,
                    method=method.upper() or existing.method,
                    status="live",
                    last_check=ts,
                    success_count=existing.success_count + 1,
                    consecutive_failures=0,
                    last_latency_ms=float(latency_ms),
                )
            self._records[endpoint] = record
            self._persist_locked()
            return record

    def upsert_failure(
        self,
        endpoint: str,
        now: Optional[int] = None,
    ) -> Optional[CapabilityRecord]:
        """Record a failed health check.

        After ``MAX_CONSECUTIVE_FAILURES`` failures the endpoint is removed
        from the store entirely and ``None`` is returned.
        """
        ts = int(now if now is not None else time.time())
        with self._lock:
            existing = self._records.get(endpoint)
            if existing is None:
                # Never recorded success, nothing to mark dead.
                return None
            new_failures = existing.consecutive_failures + 1
            if new_failures >= MAX_CONSECUTIVE_FAILURES:
                del self._records[endpoint]
                self._persist_locked()
                return None
            # Degraded but still present
            new_status: CapabilityStatus = (
                "degraded" if new_failures >= 1 else "live"
            )
            record = replace(
                existing,
                status=new_status,
                last_check=ts,
                consecutive_failures=new_failures,
            )
            self._records[endpoint] = record
            self._persist_locked()
            return record

    def remove(self, endpoint: str) -> bool:
        with self._lock:
            if endpoint in self._records:
                del self._records[endpoint]
                self._persist_locked()
                return True
            return False

    def stats(self) -> dict[str, object]:
        with self._lock:
            records = list(self._records.values())
            live = sum(1 for r in records if r.status == "live")
            degraded = sum(1 for r in records if r.status == "degraded")
            return {
                "total": len(records),
                "live": live,
                "degraded": degraded,
            }
