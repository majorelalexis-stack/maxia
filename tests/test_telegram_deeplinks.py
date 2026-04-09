"""Tests for MAXIA Telegram deep links + attribution tracker (P4C)."""
from __future__ import annotations

import os
import sys

import pytest

BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..", "backend")
sys.path.insert(0, BACKEND_DIR)

from integrations.telegram_deeplinks import (  # noqa: E402
    AttributionRecord,
    DeepLink,
    InMemoryAttributionStore,
    KNOWN_KINDS,
    extract_start_payload,
    parse_start_payload,
    track_start,
)


# ═══════════════════════════════════════════════════════════════════════════
#  parse_start_payload
# ═══════════════════════════════════════════════════════════════════════════


class TestParse:
    def test_ref_valid(self):
        link = parse_start_payload("ref_alexis123")
        assert link.valid is True
        assert link.kind == "ref"
        assert link.value == "alexis123"
        assert link.raw == "ref_alexis123"

    def test_region_valid(self):
        link = parse_start_payload("region_br")
        assert link.valid is True
        assert link.kind == "region"
        assert link.value == "br"

    def test_svc_valid(self):
        link = parse_start_payload("svc_ceo_analyst")
        assert link.valid is True
        assert link.kind == "svc"
        assert link.value == "ceo_analyst"

    def test_token_valid(self):
        link = parse_start_payload("token_SOL")
        assert link.valid is True
        assert link.kind == "token"
        assert link.value == "SOL"

    def test_app_valid(self):
        link = parse_start_payload("app_portfolio")
        assert link.valid is True
        assert link.kind == "app"

    def test_empty(self):
        link = parse_start_payload("")
        assert link.valid is False
        assert link.kind == "unknown"

    def test_none(self):
        link = parse_start_payload(None)
        assert link.valid is False

    def test_non_string(self):
        assert parse_start_payload(42).valid is False  # type: ignore[arg-type]
        assert parse_start_payload({"a": 1}).valid is False  # type: ignore[arg-type]

    def test_too_long(self):
        link = parse_start_payload("ref_" + "x" * 200)
        assert link.valid is False

    def test_unknown_prefix(self):
        link = parse_start_payload("hack_attempt")
        assert link.valid is False
        assert link.kind == "unknown"

    def test_no_underscore(self):
        link = parse_start_payload("abcdef")
        assert link.valid is False
        assert link.value == "abcdef"

    def test_empty_value(self):
        link = parse_start_payload("ref_")
        assert link.valid is False
        assert link.kind == "ref"

    def test_invalid_chars(self):
        assert parse_start_payload("ref_foo.bar").valid is False
        assert parse_start_payload("ref_foo bar").valid is False
        assert parse_start_payload("ref_foo/bar").valid is False
        assert parse_start_payload("ref_foo!bar").valid is False

    def test_case_insensitive_prefix(self):
        link = parse_start_payload("REF_alex")
        assert link.valid is True
        assert link.kind == "ref"
        assert link.value == "alex"

    def test_dash_in_value(self):
        link = parse_start_payload("ref_alex-123")
        assert link.valid is True
        assert link.value == "alex-123"

    def test_all_known_kinds_in_enum(self):
        assert KNOWN_KINDS == {"ref", "region", "svc", "token", "app"}

    def test_max_length_accepted(self):
        link = parse_start_payload("ref_" + "a" * 60)
        assert link.valid is True


# ═══════════════════════════════════════════════════════════════════════════
#  extract_start_payload
# ═══════════════════════════════════════════════════════════════════════════


class TestExtract:
    def test_start_alone(self):
        assert extract_start_payload("/start") == ""

    def test_start_with_payload(self):
        assert extract_start_payload("/start ref_alexis") == "ref_alexis"

    def test_start_case_insensitive(self):
        assert extract_start_payload("/START ref_x") == "ref_x"

    def test_start_trailing_space(self):
        assert extract_start_payload("/start  ref_x ") == "ref_x"

    def test_not_a_start_command(self):
        assert extract_start_payload("/help") == ""
        assert extract_start_payload("hello") == ""

    def test_start_with_multiple_words(self):
        # /start takes only the first token
        assert extract_start_payload("/start ref_x other") == ""

    def test_none(self):
        assert extract_start_payload(None) == ""  # type: ignore[arg-type]


# ═══════════════════════════════════════════════════════════════════════════
#  InMemoryAttributionStore
# ═══════════════════════════════════════════════════════════════════════════


class TestStore:
    def test_first_record_is_new(self):
        store = InMemoryAttributionStore()
        link = parse_start_payload("ref_alex")
        rec, is_new = store.record(user_id=42, link=link, now=100.0)
        assert is_new is True
        assert rec.user_id == 42
        assert rec.kind == "ref"
        assert rec.value == "alex"
        assert rec.first_seen_at == 100.0

    def test_second_record_idempotent(self):
        store = InMemoryAttributionStore()
        link = parse_start_payload("ref_alex")
        store.record(user_id=42, link=link, now=100.0)
        rec, is_new = store.record(user_id=42, link=link, now=200.0)
        assert is_new is False
        # first_seen_at stays at first call
        assert rec.first_seen_at == 100.0

    def test_different_user_same_payload(self):
        store = InMemoryAttributionStore()
        link = parse_start_payload("ref_alex")
        _, new1 = store.record(user_id=1, link=link, now=100.0)
        _, new2 = store.record(user_id=2, link=link, now=100.0)
        assert new1 is True
        assert new2 is True

    def test_same_user_different_payload(self):
        store = InMemoryAttributionStore()
        link1 = parse_start_payload("ref_alex")
        link2 = parse_start_payload("region_br")
        _, new1 = store.record(user_id=42, link=link1, now=100.0)
        _, new2 = store.record(user_id=42, link=link2, now=200.0)
        assert new1 is True
        assert new2 is True

    def test_get_existing(self):
        store = InMemoryAttributionStore()
        link = parse_start_payload("ref_alex")
        store.record(user_id=42, link=link, now=100.0)
        rec = store.get(42, "ref_alex")
        assert rec is not None
        assert rec.kind == "ref"

    def test_get_missing(self):
        store = InMemoryAttributionStore()
        assert store.get(1, "nope") is None

    def test_invalid_user_id(self):
        store = InMemoryAttributionStore()
        link = parse_start_payload("ref_alex")
        with pytest.raises(ValueError):
            store.record(user_id=0, link=link, now=100.0)
        with pytest.raises(ValueError):
            store.record(user_id=-1, link=link, now=100.0)

    def test_count_by_kind(self):
        store = InMemoryAttributionStore()
        store.record(1, parse_start_payload("ref_a"), 1.0)
        store.record(2, parse_start_payload("ref_b"), 2.0)
        store.record(3, parse_start_payload("region_br"), 3.0)
        store.record(4, parse_start_payload("svc_ceo"), 4.0)
        counts = store.count_by_kind()
        assert counts["ref"] == 2
        assert counts["region"] == 1
        assert counts["svc"] == 1


# ═══════════════════════════════════════════════════════════════════════════
#  track_start (end-to-end)
# ═══════════════════════════════════════════════════════════════════════════


class TestTrackStart:
    def test_track_valid_payload(self):
        store = InMemoryAttributionStore()
        link, rec, is_new = track_start(
            user_id=100,
            start_text="/start ref_carlos",
            store=store,
            clock=lambda: 5000.0,
        )
        assert link.valid is True
        assert rec is not None
        assert rec.first_seen_at == 5000.0
        assert is_new is True

    def test_track_empty_start(self):
        store = InMemoryAttributionStore()
        link, rec, is_new = track_start(
            user_id=100,
            start_text="/start",
            store=store,
            clock=lambda: 5000.0,
        )
        assert link.valid is False
        assert rec is None
        assert is_new is False

    def test_track_invalid_payload_not_stored(self):
        store = InMemoryAttributionStore()
        link, rec, is_new = track_start(
            user_id=100,
            start_text="/start hack.attempt",
            store=store,
            clock=lambda: 5000.0,
        )
        assert link.valid is False
        assert rec is None
        assert store.count_by_kind() == {}

    def test_track_repeat_click(self):
        store = InMemoryAttributionStore()
        track_start(100, "/start ref_x", store, clock=lambda: 1.0)
        _, rec, is_new = track_start(100, "/start ref_x", store, clock=lambda: 2.0)
        assert is_new is False
        # Original timestamp preserved
        assert rec.first_seen_at == 1.0

    def test_track_not_a_start_message(self):
        store = InMemoryAttributionStore()
        link, rec, is_new = track_start(
            user_id=100,
            start_text="/help",
            store=store,
            clock=lambda: 1.0,
        )
        assert link.valid is False
        assert rec is None

    def test_attribution_record_immutable(self):
        store = InMemoryAttributionStore()
        link = parse_start_payload("ref_alex")
        rec, _ = store.record(user_id=1, link=link, now=1.0)
        with pytest.raises(Exception):
            rec.user_id = 2  # type: ignore[misc]

    def test_deeplink_immutable(self):
        link = parse_start_payload("ref_alex")
        with pytest.raises(Exception):
            link.kind = "hack"  # type: ignore[misc]
