"""Tests for MAXIA Telegram Mini App backend (P5 — Plan CEO V7).

Focus: HMAC initData validation, session management, rate limiting.
The router endpoints themselves are covered by smoke tests at VPS level.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
from unittest.mock import patch
from urllib.parse import quote, urlencode

import pytest

BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..", "backend")
sys.path.insert(0, BACKEND_DIR)


# ═══════════════════════════════════════════════════════════════════════════
#  Helpers — build a valid Telegram initData string
# ═══════════════════════════════════════════════════════════════════════════


def build_valid_init_data(
    bot_token: str,
    user_id: int = 12345,
    first_name: str = "Alice",
    username: str = "alice_t",
    language_code: str = "en",
    is_premium: bool = False,
    auth_date: int | None = None,
) -> str:
    """Build a properly-signed Telegram initData query string."""
    if auth_date is None:
        auth_date = int(time.time())
    user_json = json.dumps({
        "id": user_id,
        "first_name": first_name,
        "last_name": "",
        "username": username,
        "language_code": language_code,
        "is_premium": is_premium,
    }, separators=(",", ":"))

    fields = {
        "query_id": "AAH12345",
        "user": user_json,
        "auth_date": str(auth_date),
    }

    # Build data-check-string (sorted, excluding hash)
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret_key = hmac.new(
        b"WebAppData",
        bot_token.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    signature = hmac.new(
        secret_key,
        data_check.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    fields["hash"] = signature
    return urlencode(
        {k: (v if k == "user" else v) for k, v in fields.items()},
        quote_via=quote,
    )


@pytest.fixture
def bot_token() -> str:
    return "1234567890:FAKE_BOT_TOKEN_FOR_TESTS_XXXXXXXXXXX"


@pytest.fixture
def miniapp(bot_token: str):
    """Import telegram_miniapp with a patched bot token and fresh state."""
    with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": bot_token}):
        from integrations import telegram_miniapp as mm
        # Reset module-level state so tests don't leak into each other
        mm.TELEGRAM_BOT_TOKEN = bot_token
        mm._sessions.clear()
        mm._rate_store.clear()
        yield mm


# ═══════════════════════════════════════════════════════════════════════════
#  _verify_init_data
# ═══════════════════════════════════════════════════════════════════════════


class TestVerifyInitData:
    def test_valid_init_data(self, miniapp, bot_token):
        init_data = build_valid_init_data(bot_token, user_id=42, first_name="Bob")
        user = miniapp._verify_init_data(init_data)
        assert user is not None
        assert user["user_id"] == "42"
        assert user["first_name"] == "Bob"
        assert user["username"] == "alice_t"

    def test_tampered_hash_rejected(self, miniapp, bot_token):
        init_data = build_valid_init_data(bot_token)
        # Replace the hash with garbage
        parts = init_data.split("&")
        tampered = "&".join(
            "hash=ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
            if p.startswith("hash=") else p
            for p in parts
        )
        assert miniapp._verify_init_data(tampered) is None

    def test_missing_hash(self, miniapp, bot_token):
        init_data = build_valid_init_data(bot_token)
        parts = init_data.split("&")
        no_hash = "&".join(p for p in parts if not p.startswith("hash="))
        assert miniapp._verify_init_data(no_hash) is None

    def test_expired_auth_date(self, miniapp, bot_token):
        # auth_date 25h ago
        old = int(time.time()) - (86400 + 3600)
        init_data = build_valid_init_data(bot_token, auth_date=old)
        assert miniapp._verify_init_data(init_data) is None

    def test_tampered_user_field(self, miniapp, bot_token):
        """Modifying any field invalidates the hash."""
        init_data = build_valid_init_data(bot_token, user_id=1)
        # The user JSON is URL-encoded: `:` -> `%3A`
        tampered = init_data.replace("%22id%22%3A1%2C", "%22id%22%3A999%2C")
        assert tampered != init_data, "replace must actually change the string"
        assert miniapp._verify_init_data(tampered) is None

    def test_different_bot_token_rejected(self, miniapp, bot_token):
        """Data signed with another token must not validate."""
        init_data = build_valid_init_data("OTHER_BOT_TOKEN_ABCDEFGHIJKLMNOPQRST")
        assert miniapp._verify_init_data(init_data) is None

    def test_empty_bot_token_returns_none(self, miniapp, bot_token):
        miniapp.TELEGRAM_BOT_TOKEN = ""
        init_data = build_valid_init_data(bot_token)
        assert miniapp._verify_init_data(init_data) is None

    def test_malformed_init_data(self, miniapp):
        assert miniapp._verify_init_data("") is None
        assert miniapp._verify_init_data("not-a-query-string") is None

    def test_language_code_extracted(self, miniapp, bot_token):
        init_data = build_valid_init_data(bot_token, language_code="ja")
        user = miniapp._verify_init_data(init_data)
        assert user["language_code"] == "ja"

    def test_is_premium_flag(self, miniapp, bot_token):
        init_data = build_valid_init_data(bot_token, is_premium=True)
        user = miniapp._verify_init_data(init_data)
        assert user["is_premium"] is True


# ═══════════════════════════════════════════════════════════════════════════
#  Session management
# ═══════════════════════════════════════════════════════════════════════════


class TestSessions:
    def test_get_session_missing(self, miniapp):
        assert miniapp._get_session("nope") is None

    def test_get_session_fresh(self, miniapp):
        miniapp._sessions["tg_abc"] = {
            "user_id": "1",
            "created_at": int(time.time()),
        }
        sess = miniapp._get_session("tg_abc")
        assert sess is not None
        assert sess["user_id"] == "1"

    def test_get_session_expired_removed(self, miniapp):
        miniapp._sessions["tg_old"] = {
            "user_id": "1",
            "created_at": int(time.time()) - (86400 + 1),
        }
        assert miniapp._get_session("tg_old") is None
        assert "tg_old" not in miniapp._sessions

    def test_require_session_missing_header(self, miniapp):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            miniapp._require_session("")
        assert exc.value.status_code == 401

    def test_require_session_invalid_token(self, miniapp):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            miniapp._require_session("tg_unknown")
        assert exc.value.status_code == 401

    def test_require_session_valid(self, miniapp):
        miniapp._sessions["tg_good"] = {
            "user_id": "42",
            "created_at": int(time.time()),
        }
        sess = miniapp._require_session("tg_good")
        assert sess["user_id"] == "42"


# ═══════════════════════════════════════════════════════════════════════════
#  Rate limiting
# ═══════════════════════════════════════════════════════════════════════════


class TestRateLimit:
    def test_under_limit_ok(self, miniapp):
        for _ in range(29):
            assert miniapp._check_tg_rate("user1") is True

    def test_exactly_at_limit_blocked(self, miniapp):
        for _ in range(30):
            miniapp._check_tg_rate("user1")
        assert miniapp._check_tg_rate("user1") is False

    def test_different_users_independent(self, miniapp):
        for _ in range(30):
            miniapp._check_tg_rate("userA")
        # userB starts fresh
        assert miniapp._check_tg_rate("userB") is True

    def test_old_entries_pruned(self, miniapp):
        # Inject old timestamps
        now = time.time()
        miniapp._rate_store["userC"] = [now - 120] * 30  # all older than 60s window
        # Next call should succeed (all pruned)
        assert miniapp._check_tg_rate("userC") is True

    def test_require_session_triggers_rate_limit(self, miniapp):
        from fastapi import HTTPException

        miniapp._sessions["tg_rl"] = {
            "user_id": "rl_user",
            "created_at": int(time.time()),
        }
        # Exhaust the limit
        for _ in range(30):
            miniapp._require_session("tg_rl")
        with pytest.raises(HTTPException) as exc:
            miniapp._require_session("tg_rl")
        assert exc.value.status_code == 429
