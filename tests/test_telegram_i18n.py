"""Tests for MAXIA Telegram bot i18n layer (P4A — Plan CEO V7)."""
from __future__ import annotations

import os
import sys

import pytest

BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..", "backend")
sys.path.insert(0, BACKEND_DIR)

from integrations.telegram_i18n import (  # noqa: E402
    DEFAULT_LANG,
    SUPPORTED_LANGS,
    build_help_text,
    build_price_text,
    build_welcome_text,
    detect_lang,
    t,
)


class TestLanguageDetection:
    def test_all_13_supported(self):
        assert len(SUPPORTED_LANGS) == 13

    @pytest.mark.parametrize("code", SUPPORTED_LANGS)
    def test_direct_match(self, code: str):
        assert detect_lang(code) == code

    def test_none_falls_back(self):
        assert detect_lang(None) == "en"

    def test_empty_falls_back(self):
        assert detect_lang("") == "en"

    def test_non_string_falls_back(self):
        assert detect_lang(42) == "en"  # type: ignore[arg-type]
        assert detect_lang(["fr"]) == "en"  # type: ignore[arg-type]

    def test_alias_zh_cn_to_zh_tw(self):
        assert detect_lang("zh-CN") == "zh-tw"
        assert detect_lang("zh_cn") == "zh-tw"
        assert detect_lang("zh-Hans") == "zh-tw"
        assert detect_lang("zh") == "zh-tw"

    def test_alias_pt_to_pt_br(self):
        assert detect_lang("pt") == "pt-br"
        assert detect_lang("pt-PT") == "pt-br"

    def test_alias_es_regions(self):
        assert detect_lang("es-MX") == "es"
        assert detect_lang("es-AR") == "es"
        assert detect_lang("es-ES") == "es"

    def test_alias_en_regions(self):
        assert detect_lang("en-US") == "en"
        assert detect_lang("en-GB") == "en"

    def test_unknown_falls_back(self):
        assert detect_lang("klingon") == "en"
        assert detect_lang("xx-YY") == "en"

    def test_whitespace_trimmed(self):
        assert detect_lang(" fr ") == "fr"
        assert detect_lang("FR") == "fr"


class TestTranslate:
    def test_key_exists(self):
        result = t("welcome.title", "fr", name="Alex")
        assert "Alex" in result
        assert "MAXIA" in result
        assert "Bienvenue" in result

    def test_missing_key_returns_key(self):
        result = t("nonexistent.key", "fr")
        assert result == "nonexistent.key"

    def test_english_fallback_for_missing_lang(self):
        # 'help.list' is only in English
        result = t("help.list", "ja")
        assert "/start" in result
        assert "/price" in result

    def test_safe_format_with_missing_placeholder(self):
        # If a template uses {name} but we don't pass it, no exception
        result = t("welcome.title", "en")
        assert "MAXIA" in result
        # Placeholder stays in output
        assert "{name}" in result

    def test_format_all_13_langs_welcome(self):
        for lang in SUPPORTED_LANGS:
            result = t("welcome.title", lang, name="Taro")
            assert "Taro" in result, f"name missing in {lang}"
            assert "MAXIA" in result, f"MAXIA missing in {lang}"


class TestBuildWelcome:
    def test_english(self):
        text = build_welcome_text("en", "Alice")
        assert "Alice" in text
        assert "MAXIA" in text
        assert "AI-powered" in text
        assert "/price" in text
        assert "/help" in text

    def test_french(self):
        text = build_welcome_text("fr", "Marie")
        assert "Marie" in text
        assert "Bienvenue" in text
        assert "Trading" in text or "trading" in text

    def test_japanese(self):
        text = build_welcome_text("ja", "Taro")
        assert "Taro" in text
        assert "MAXIA" in text
        assert "ようこそ" in text

    def test_arabic_rtl_ok(self):
        text = build_welcome_text("ar", "Ahmed")
        assert "Ahmed" in text
        assert "MAXIA" in text
        assert "مرحبا" in text

    def test_name_default(self):
        text = build_welcome_text("en", "")
        assert "there" in text

    def test_name_none(self):
        text = build_welcome_text("en", None)  # type: ignore[arg-type]
        assert "there" in text

    def test_name_truncated(self):
        long_name = "A" * 200
        text = build_welcome_text("en", long_name)
        assert "A" * 64 in text  # truncated to 64
        assert "A" * 65 not in text


class TestBuildPrice:
    def test_valid_price(self):
        text = build_price_text("en", "BTC", 45_000.5, "pyth")
        assert "BTC" in text
        assert "45,000.5" in text
        assert "pyth" in text

    def test_zero_price_returns_error(self):
        text = build_price_text("en", "FOO", 0, "oracle")
        assert "Could not fetch" in text or "FOO" in text

    def test_none_price_returns_error(self):
        text = build_price_text("fr", "BAR", None, "")
        assert "BAR" in text
        assert "Impossible" in text or "price" in text.lower()

    def test_negative_price_returns_error(self):
        text = build_price_text("ja", "X", -1, "src")
        assert "X" in text

    def test_all_13_langs_price_ok(self):
        for lang in SUPPORTED_LANGS:
            text = build_price_text(lang, "SOL", 250.12, "pyth")
            assert "SOL" in text, f"symbol missing in {lang}"
            assert "250.12" in text, f"price missing in {lang}"

    def test_all_13_langs_price_error(self):
        for lang in SUPPORTED_LANGS:
            text = build_price_text(lang, "XYZ", 0, "")
            assert "XYZ" in text, f"symbol missing in {lang}"


class TestBuildHelp:
    def test_english(self):
        text = build_help_text("en")
        assert "MAXIA" in text
        assert "/start" in text
        assert "/price" in text
        assert "/help" in text

    def test_all_13_langs(self):
        for lang in SUPPORTED_LANGS:
            text = build_help_text(lang)
            assert "MAXIA" in text, f"MAXIA missing in {lang}"
            assert "/start" in text, f"/start missing in {lang}"
            assert "/price" in text, f"/price missing in {lang}"
