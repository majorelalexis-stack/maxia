"""Tests for CEO memory_prod store (P7 — Plan CEO V7)."""
from __future__ import annotations

import json
import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from local_ceo.memory_prod.store import (  # noqa: E402
    MAX_CONSECUTIVE_FAILURES,
    CapabilityRecord,
    MemoryStore,
    load_json,
    save_json,
)


# ═══════════════════════════════════════════════════════════════════════════
#  CapabilityRecord
# ═══════════════════════════════════════════════════════════════════════════


class TestCapabilityRecord:
    def test_immutable(self):
        rec = CapabilityRecord(
            endpoint="/health", description="h", method="GET",
            status="live", verified_at=1, last_check=1,
        )
        with pytest.raises(Exception):
            rec.endpoint = "/hacked"  # type: ignore[misc]

    def test_roundtrip(self):
        rec = CapabilityRecord(
            endpoint="/oracle/specs", description="specs", method="GET",
            status="live", verified_at=100, last_check=200,
            success_count=5, consecutive_failures=0, last_latency_ms=42.5,
        )
        data = rec.to_dict()
        rec2 = CapabilityRecord.from_dict(data)
        assert rec == rec2

    def test_from_dict_tolerates_missing(self):
        rec = CapabilityRecord.from_dict({"endpoint": "/x"})
        assert rec.endpoint == "/x"
        assert rec.method == "GET"
        assert rec.status == "live"


# ═══════════════════════════════════════════════════════════════════════════
#  load_json / save_json
# ═══════════════════════════════════════════════════════════════════════════


class TestJsonIo:
    def test_load_missing_returns_default(self, tmp_path):
        path = str(tmp_path / "nope.json")
        assert load_json(path, default={"a": 1}) == {"a": 1}

    def test_save_then_load(self, tmp_path):
        path = str(tmp_path / "data.json")
        save_json(path, {"k": "v"})
        assert load_json(path, default=None) == {"k": "v"}

    def test_save_is_atomic(self, tmp_path):
        """Partial writes should never be visible."""
        path = str(tmp_path / "data.json")
        save_json(path, {"a": 1})
        # Corrupt by truncation
        with open(path, "w", encoding="utf-8") as f:
            f.write('{"half')
        # load should fall back
        assert load_json(path, default={"ok": True}) == {"ok": True}

    def test_save_creates_parent_dir(self, tmp_path):
        path = str(tmp_path / "nested" / "deep" / "file.json")
        save_json(path, [1, 2, 3])
        assert load_json(path, default=None) == [1, 2, 3]


# ═══════════════════════════════════════════════════════════════════════════
#  MemoryStore
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def store_path(tmp_path) -> str:
    return str(tmp_path / "capabilities_prod.json")


@pytest.fixture
def store(store_path: str) -> MemoryStore:
    return MemoryStore(capabilities_path=store_path)


class TestMemoryStore:
    def test_empty_store(self, store):
        assert store.count() == 0
        assert store.all() == []
        assert store.all_live() == []

    def test_upsert_new_success(self, store):
        rec = store.upsert_success(
            endpoint="/health", description="h", method="GET",
            latency_ms=12.3, now=1000,
        )
        assert rec.endpoint == "/health"
        assert rec.status == "live"
        assert rec.success_count == 1
        assert rec.verified_at == 1000
        assert rec.last_check == 1000
        assert store.count() == 1

    def test_upsert_existing_success_increments(self, store):
        store.upsert_success("/health", "h", "GET", 10.0, now=1000)
        rec = store.upsert_success("/health", "h", "GET", 20.0, now=2000)
        assert rec.success_count == 2
        # verified_at preserved on update
        assert rec.verified_at == 1000
        assert rec.last_check == 2000
        assert rec.last_latency_ms == 20.0

    def test_failure_without_prior_success_ignored(self, store):
        assert store.upsert_failure("/ghost") is None
        assert store.count() == 0

    def test_failure_marks_degraded(self, store):
        store.upsert_success("/health", "h", "GET", 10.0, now=1000)
        rec = store.upsert_failure("/health", now=2000)
        assert rec is not None
        assert rec.status == "degraded"
        assert rec.consecutive_failures == 1

    def test_three_failures_prune(self, store):
        store.upsert_success("/health", "h", "GET", 10.0, now=1000)
        for _ in range(MAX_CONSECUTIVE_FAILURES - 1):
            store.upsert_failure("/health")
        # Final failure prunes
        assert store.upsert_failure("/health") is None
        assert store.get("/health") is None
        assert store.count() == 0

    def test_success_resets_failure_count(self, store):
        store.upsert_success("/x", "x", "GET", 1.0, now=1000)
        store.upsert_failure("/x", now=1001)
        store.upsert_failure("/x", now=1002)
        rec = store.upsert_success("/x", "x", "GET", 1.0, now=1003)
        assert rec.consecutive_failures == 0
        assert rec.status == "live"

    def test_all_live_filters_degraded(self, store):
        store.upsert_success("/a", "a", "GET", 1.0, now=1000)
        store.upsert_success("/b", "b", "GET", 1.0, now=1000)
        store.upsert_failure("/b", now=1001)
        live = store.all_live()
        assert len(live) == 1
        assert live[0].endpoint == "/a"

    def test_remove(self, store):
        store.upsert_success("/a", "a", "GET", 1.0, now=1000)
        assert store.remove("/a") is True
        assert store.remove("/a") is False

    def test_persistence_across_reload(self, store_path):
        s1 = MemoryStore(capabilities_path=store_path)
        s1.upsert_success("/a", "a", "GET", 1.5, now=100)
        s1.upsert_success("/b", "b", "POST", 2.5, now=200)
        s2 = MemoryStore(capabilities_path=store_path)
        assert s2.count() == 2
        rec_a = s2.get("/a")
        assert rec_a is not None
        assert rec_a.last_latency_ms == 1.5

    def test_stats(self, store):
        store.upsert_success("/a", "a", "GET", 1.0, now=100)
        store.upsert_success("/b", "b", "GET", 1.0, now=100)
        store.upsert_failure("/b", now=101)
        stats = store.stats()
        assert stats["total"] == 2
        assert stats["live"] == 1
        assert stats["degraded"] == 1

    def test_persist_json_shape(self, store, store_path):
        store.upsert_success("/a", "desc", "GET", 5.5, now=100)
        with open(store_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["version"] == 1
        assert data["count"] == 1
        assert isinstance(data["capabilities"], list)
        assert data["capabilities"][0]["endpoint"] == "/a"

    def test_reload_ignores_corrupt_entries(self, store_path):
        save_json(store_path, {
            "version": 1,
            "capabilities": [
                {"endpoint": "/good", "method": "GET", "status": "live",
                 "verified_at": 1, "last_check": 1},
                "not a dict",
                {"no_endpoint": "whatever"},
            ],
        })
        store = MemoryStore(capabilities_path=store_path)
        assert store.count() == 1
        assert store.get("/good") is not None


# ═══════════════════════════════════════════════════════════════════════════
#  Static JSON files ship with the repo
# ═══════════════════════════════════════════════════════════════════════════


class TestStaticFiles:
    @pytest.fixture
    def memory_dir(self) -> str:
        return os.path.join(ROOT, "local_ceo", "memory_prod")

    def test_country_allowlist_shape(self, memory_dir):
        path = os.path.join(memory_dir, "country_allowlist.json")
        data = load_json(path, default={})
        assert isinstance(data, dict)
        assert len(data["allowed"]) == 28
        assert "IN" in data["geo_blocked"]
        assert "CN" in data["blocked"]
        assert "US" in data["blocked"]

    def test_quotas_shape(self, memory_dir):
        path = os.path.join(memory_dir, "quotas_daily.json")
        data = load_json(path, default={})
        assert isinstance(data, dict)
        assert data["channels"]["email"]["daily_cap"] == 30
        assert data["channels"]["discord"]["per_server_daily"] == 10
        assert data["total_daily_outreach_cap"] == 60

    def test_outreach_channels_shape(self, memory_dir):
        path = os.path.join(memory_dir, "outreach_channels.json")
        data = load_json(path, default={})
        assert isinstance(data, dict)
        assert "telegram_bot" in data["channels"]
        assert data["channels"]["telegram_bot"]["status"] == "live"
        langs = data["channels"]["telegram_bot"]["supported_languages"]
        assert len(langs) == 13

    def test_ban_history_shape(self, memory_dir):
        path = os.path.join(memory_dir, "ban_history.json")
        data = load_json(path, default={})
        assert isinstance(data, dict)
        assert isinstance(data["incidents"], list)
        assert "rules" in data
