"""Tests for MAXIA compliance layer — country filter + disclaimers (P2)."""
from __future__ import annotations

import os
import sys

import pytest

BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..", "backend")
sys.path.insert(0, BACKEND_DIR)

from compliance import (  # noqa: E402
    ALLOWED_COUNTRIES,
    BLOCKED_COUNTRIES,
    GEO_BLOCKED_COUNTRIES,
    SUPPORTED_LANGUAGES,
    get_disclaimer,
    get_short_disclaimer,
    is_allowed,
    normalize_country_code,
)


class TestCountryFilterAllowed:
    @pytest.mark.parametrize("code", sorted(ALLOWED_COUNTRIES))
    def test_every_allowed_country_can_trade(self, code: str):
        r = is_allowed(code, "trading")
        assert r.allowed is True
        assert r.code == "OK"
        assert r.country == code

    def test_count_is_28(self):
        assert len(ALLOWED_COUNTRIES) == 28

    def test_india_not_in_allowed(self):
        assert "IN" not in ALLOWED_COUNTRIES

    def test_china_not_in_allowed(self):
        assert "CN" not in ALLOWED_COUNTRIES

    def test_us_not_in_allowed(self):
        assert "US" not in ALLOWED_COUNTRIES


class TestCountryFilterBlocked:
    @pytest.mark.parametrize("code", sorted(BLOCKED_COUNTRIES))
    def test_every_blocked_country_blocked(self, code: str):
        for feature in ("discovery", "marketing", "payment", "trading", "withdrawal"):
            r = is_allowed(code, feature)
            assert r.allowed is False, f"{code} should be blocked for {feature}"
            assert r.code == "BLOCKED"

    def test_blocked_contains_sanctioned(self):
        for sanctioned in ("KP", "IR", "SY", "CU", "RU", "BY", "CN"):
            assert sanctioned in BLOCKED_COUNTRIES


class TestCountryFilterGeoBlocked:
    def test_india_discovery_allowed(self):
        r = is_allowed("IN", "discovery")
        assert r.allowed is True
        assert r.code == "GEO_BLOCKED"

    def test_india_marketing_blocked(self):
        r = is_allowed("IN", "marketing")
        assert r.allowed is False
        assert r.code == "GEO_BLOCKED"
        assert "VASP" in r.reason

    def test_india_trading_blocked(self):
        r = is_allowed("IN", "trading")
        assert r.allowed is False
        assert r.code == "GEO_BLOCKED"

    def test_india_payment_blocked(self):
        r = is_allowed("IN", "payment")
        assert r.allowed is False
        assert r.code == "GEO_BLOCKED"

    def test_india_withdrawal_blocked(self):
        r = is_allowed("IN", "withdrawal")
        assert r.allowed is False
        assert r.code == "GEO_BLOCKED"


class TestCountryFilterUnknown:
    def test_unknown_country_discovery_only(self):
        r = is_allowed("XX", "discovery")
        assert r.allowed is True
        assert r.code == "UNKNOWN"

    def test_unknown_country_no_trading(self):
        r = is_allowed("XX", "trading")
        assert r.allowed is False

    def test_empty_country(self):
        r = is_allowed("", "discovery")
        assert r.allowed is True
        assert r.code == "UNKNOWN"

    def test_none_country(self):
        r = is_allowed(None, "trading")
        assert r.allowed is False

    def test_invalid_country_length(self):
        assert normalize_country_code("USA") == ""
        assert normalize_country_code("U") == ""

    def test_lower_case_normalized(self):
        assert normalize_country_code("sg") == "SG"
        assert normalize_country_code(" sg ") == "SG"

    def test_invalid_feature_rejected(self):
        r = is_allowed("SG", "nuclear_launch")
        assert r.allowed is False
        assert r.code == "UNKNOWN"


class TestCountryFilterLogic:
    def test_allowed_blocked_geoblocked_disjoint(self):
        assert ALLOWED_COUNTRIES.isdisjoint(BLOCKED_COUNTRIES)
        assert ALLOWED_COUNTRIES.isdisjoint(GEO_BLOCKED_COUNTRIES)
        assert BLOCKED_COUNTRIES.isdisjoint(GEO_BLOCKED_COUNTRIES)

    def test_result_is_frozen(self):
        from compliance.country_filter import ComplianceResult

        r = is_allowed("SG", "trading")
        assert isinstance(r, ComplianceResult)
        with pytest.raises(Exception):
            r.allowed = False  # type: ignore[misc]


class TestDisclaimers:
    def test_all_languages_have_long_disclaimer(self):
        for lang in SUPPORTED_LANGUAGES:
            text = get_disclaimer(lang)
            assert isinstance(text, str)
            assert len(text) > 50
            assert "MAXIA" in text

    def test_all_languages_have_short_disclaimer(self):
        for lang in SUPPORTED_LANGUAGES:
            text = get_short_disclaimer(lang)
            assert isinstance(text, str)
            assert len(text) > 10

    def test_english_fallback_for_unknown(self):
        assert get_disclaimer("klingon") == get_disclaimer("en")
        assert get_disclaimer(None) == get_disclaimer("en")  # type: ignore[arg-type]
        assert get_disclaimer(42) == get_disclaimer("en")  # type: ignore[arg-type]

    def test_alias_zh_cn_maps_to_zh_tw(self):
        assert get_disclaimer("zh-CN") == get_disclaimer("zh-tw")
        assert get_disclaimer("zh_cn") == get_disclaimer("zh-tw")
        assert get_disclaimer("zh") == get_disclaimer("zh-tw")

    def test_alias_pt_pt_maps_to_pt_br(self):
        assert get_disclaimer("pt-PT") == get_disclaimer("pt-br")
        assert get_disclaimer("pt") == get_disclaimer("pt-br")

    def test_alias_es_region(self):
        assert get_disclaimer("es-MX") == get_disclaimer("es")
        assert get_disclaimer("es-ES") == get_disclaimer("es")

    def test_telegram_locale_format(self):
        # Telegram sends language_code like "en", "fr", "ja-jp"
        for raw in ("en", "fr", "ja-jp", "ko-kr"):
            text = get_disclaimer(raw)
            assert "MAXIA" in text

    def test_restricted_regions_mentioned_in_english(self):
        text = get_disclaimer("en")
        for code in ("CN", "KP", "IR", "SY", "CU", "RU", "BY", "US"):
            assert code in text, f"{code} missing from English disclaimer"

    def test_india_mentioned_in_english(self):
        text = get_disclaimer("en")
        assert "India" in text or "IN" in text

    def test_all_13_languages_supported(self):
        assert len(SUPPORTED_LANGUAGES) == 13
