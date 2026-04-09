"""Tests for MAXIA GitHub prospector (Plan V8 / Sprint 4)."""
from __future__ import annotations

import os
import sys

import pytest

BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..", "backend")
sys.path.insert(0, BACKEND_DIR)

from marketing.github_prospector import (  # noqa: E402
    GithubProspect,
    GithubProspector,
)


# ═══════════════════════════════════════════════════════════════════════════
#  Country inference
# ═══════════════════════════════════════════════════════════════════════════


class TestCountryInference:
    def test_direct_country(self):
        assert GithubProspector._infer_country("Singapore") == "SG"
        assert GithubProspector._infer_country("Japan") == "JP"
        assert GithubProspector._infer_country("Brazil") == "BR"

    def test_city_maps_to_country(self):
        assert GithubProspector._infer_country("Tokyo, Japan") == "JP"
        assert GithubProspector._infer_country("Sao Paulo, Brasil") == "BR"
        assert GithubProspector._infer_country("Lagos") == "NG"

    def test_blocked_countries_still_detected(self):
        # The inference MUST detect them so the compliance layer can skip
        assert GithubProspector._infer_country("Beijing, China") == "CN"
        assert GithubProspector._infer_country("Mumbai, India") == "IN"
        assert GithubProspector._infer_country("San Francisco") == "US"
        assert GithubProspector._infer_country("Moscow") == "RU"

    def test_empty(self):
        assert GithubProspector._infer_country("") == ""
        assert GithubProspector._infer_country(None) == ""  # type: ignore[arg-type]
        assert GithubProspector._infer_country(42) == ""  # type: ignore[arg-type]

    def test_case_insensitive(self):
        assert GithubProspector._infer_country("SINGAPORE") == "SG"
        assert GithubProspector._infer_country("Tokyo") == "JP"

    def test_unknown_location(self):
        assert GithubProspector._infer_country("Remote") == ""
        assert GithubProspector._infer_country("The Internet") == ""


# ═══════════════════════════════════════════════════════════════════════════
#  Language inference
# ═══════════════════════════════════════════════════════════════════════════


class TestLangInference:
    def test_japan_gives_ja(self):
        assert GithubProspector._lang_for("JP") == "ja"

    def test_brazil_gives_pt_br(self):
        assert GithubProspector._lang_for("BR") == "pt-br"

    def test_taiwan_gives_zh_tw(self):
        assert GithubProspector._lang_for("TW") == "zh-tw"

    def test_senegal_gives_fr(self):
        assert GithubProspector._lang_for("SN") == "fr"

    def test_unknown_defaults_en(self):
        assert GithubProspector._lang_for("XX") == "en"
        assert GithubProspector._lang_for("") == "en"

    def test_singapore_gives_en(self):
        assert GithubProspector._lang_for("SG") == "en"


# ═══════════════════════════════════════════════════════════════════════════
#  Email validation
# ═══════════════════════════════════════════════════════════════════════════


class TestEmailValidation:
    def test_valid(self):
        assert GithubProspector._is_valid_email("alex@example.com") is True
        assert GithubProspector._is_valid_email("a.b+c@example.co.jp") is True

    def test_invalid(self):
        assert GithubProspector._is_valid_email("") is False
        assert GithubProspector._is_valid_email("notanemail") is False
        assert GithubProspector._is_valid_email("@example.com") is False
        assert GithubProspector._is_valid_email("user@") is False
        assert GithubProspector._is_valid_email(None) is False  # type: ignore[arg-type]
        assert GithubProspector._is_valid_email(42) is False  # type: ignore[arg-type]


# ═══════════════════════════════════════════════════════════════════════════
#  Cache load/save + dedup
# ═══════════════════════════════════════════════════════════════════════════


class TestCache:
    def test_empty_cache(self, tmp_path):
        p = GithubProspector(cache_path=str(tmp_path / "cache.json"))
        assert p._seen_emails == set()

    def test_save_and_reload(self, tmp_path):
        path = str(tmp_path / "cache.json")
        p1 = GithubProspector(cache_path=path)
        p1._seen_emails.add("alice@example.com")
        p1._seen_emails.add("BOB@example.com")
        p1._save_cache()

        p2 = GithubProspector(cache_path=path)
        # Normalized to lowercase
        assert "alice@example.com" in p2._seen_emails
        assert "bob@example.com" in p2._seen_emails

    def test_corrupt_cache_falls_back(self, tmp_path):
        path = str(tmp_path / "cache.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write("not json {")
        p = GithubProspector(cache_path=path)
        assert p._seen_emails == set()

    def test_missing_cache_file(self, tmp_path):
        path = str(tmp_path / "does-not-exist.json")
        p = GithubProspector(cache_path=path)
        assert p._seen_emails == set()


# ═══════════════════════════════════════════════════════════════════════════
#  Headers construction
# ═══════════════════════════════════════════════════════════════════════════


class TestHeaders:
    def test_no_token(self, tmp_path):
        p = GithubProspector(token="", cache_path=str(tmp_path / "c.json"))
        headers = p._headers()
        assert "Authorization" not in headers
        assert "User-Agent" in headers

    def test_with_token(self, tmp_path):
        p = GithubProspector(token="ghp_xxx", cache_path=str(tmp_path / "c.json"))
        headers = p._headers()
        assert headers["Authorization"] == "Bearer ghp_xxx"
        assert "MAXIA" in headers["User-Agent"]


# ═══════════════════════════════════════════════════════════════════════════
#  GithubProspect dataclass
# ═══════════════════════════════════════════════════════════════════════════


class TestDataclass:
    def test_immutable(self):
        p = GithubProspect(
            login="alice", name="Alice", email="a@b.com", bio="",
            location="SG", country="SG", lang="en",
            profile_url="", public_repos=0, followers=0,
            topics_matched=(),
        )
        with pytest.raises(Exception):
            p.email = "other@b.com"  # type: ignore[misc]
