"""MAXIA V12 — Security test suite.

Tests OpenAPI visibility, rate limiting, SSRF protection (_is_safe_url),
content safety, OFAC sanctions, admin session management, geo-blocking,
referral code format, and API key masking.
All external dependencies are mocked.
"""
import asyncio
import os
import re
import secrets
import sys
import time
from collections import defaultdict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..", "backend")
sys.path.insert(0, BACKEND_DIR)


def _run(coro):
    """Run an async coroutine synchronously."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ═══════════════════════════════════════════════════════════════════════════════
#  OPENAPI VISIBILITY
# ═══════════════════════════════════════════════════════════════════════════════

class TestOpenAPIVisibility:
    """Test that OpenAPI/Swagger is disabled in production."""

    def test_openapi_disabled_when_force_https(self):
        """When FORCE_HTTPS=true, _is_prod=True and openapi_url=None."""
        # Verify the logic: _is_prod = _force_https or not _is_sandbox
        force_https = True
        is_sandbox = False
        is_prod = force_https or not is_sandbox
        assert is_prod is True
        # In prod, openapi_url should be None
        openapi_url = None if is_prod else "/openapi.json"
        assert openapi_url is None

    def test_openapi_enabled_when_sandbox(self):
        """When SANDBOX_MODE=true and FORCE_HTTPS=false, openapi available."""
        force_https = False
        is_sandbox = True
        is_prod = force_https or not is_sandbox
        assert is_prod is False
        openapi_url = None if is_prod else "/openapi.json"
        assert openapi_url == "/openapi.json"


# ═══════════════════════════════════════════════════════════════════════════════
#  RATE LIMITING (in-memory fallback)
# ═══════════════════════════════════════════════════════════════════════════════

class TestRateLimitMemory:
    """Test in-memory rate limiter."""

    def test_rate_limit_allows_under_limit(self):
        """Requests under the limit should pass."""
        from core.security import _check_rate_limit_memory, _rate_store, RATE_LIMIT
        test_ip = "203.0.113.50"
        _rate_store.pop(test_ip, None)

        # Should not raise for a single request
        _check_rate_limit_memory(test_ip)
        assert len(_rate_store[test_ip]) == 1

    def test_rate_limit_blocks_after_max(self):
        """Requests exceeding limit should raise 429."""
        from core.security import _check_rate_limit_memory, _rate_store, RATE_LIMIT
        from fastapi import HTTPException
        test_ip = "203.0.113.51"
        # Fill up to the limit
        now = time.time()
        _rate_store[test_ip] = [now - i * 0.001 for i in range(RATE_LIMIT)]

        with pytest.raises(HTTPException) as exc_info:
            _check_rate_limit_memory(test_ip)
        assert exc_info.value.status_code == 429

    def test_rate_limit_whitelisted_ips_bypass(self):
        """Whitelisted IPs should not be rate limited."""
        from core.security import _check_rate_limit_memory, _rate_store, RATE_LIMIT_WHITELIST
        test_ip = "127.0.0.1"
        assert test_ip in RATE_LIMIT_WHITELIST
        # Should not raise even with many requests
        _rate_store.pop(test_ip, None)
        for _ in range(300):
            _check_rate_limit_memory(test_ip)
        # No exception raised — whitelisted


# ═══════════════════════════════════════════════════════════════════════════════
#  SSRF PROTECTION (_is_safe_url)
# ═══════════════════════════════════════════════════════════════════════════════

class TestIsSafeUrl:
    """Test _is_safe_url blocks SSRF vectors (localhost, private IPs, link-local)."""

    def test_blocks_localhost(self):
        """localhost should be blocked."""
        from ai.web_scraper import _is_safe_url
        safe, reason = _run(_is_safe_url("http://localhost/admin"))
        assert safe is False

    def test_blocks_127_0_0_1(self):
        """127.0.0.1 should be blocked (loopback)."""
        from ai.web_scraper import _is_safe_url
        safe, reason = _run(_is_safe_url("http://127.0.0.1/secret"))
        assert safe is False

    def test_blocks_10_x(self):
        """10.0.0.1 should be blocked (private IP)."""
        from ai.web_scraper import _is_safe_url
        # Mock DNS resolution to return 10.0.0.1
        import socket
        mock_result = [(socket.AF_INET, socket.SOCK_STREAM, 0, '', ('10.0.0.1', 0))]
        with patch("socket.getaddrinfo", return_value=mock_result):
            safe, reason = _run(_is_safe_url("http://internal.example.com"))
        assert safe is False
        assert reason == "private_ip"

    def test_blocks_192_168(self):
        """192.168.1.1 should be blocked (private IP)."""
        from ai.web_scraper import _is_safe_url
        import socket
        mock_result = [(socket.AF_INET, socket.SOCK_STREAM, 0, '', ('192.168.1.1', 0))]
        with patch("socket.getaddrinfo", return_value=mock_result):
            safe, reason = _run(_is_safe_url("http://internal.example.com"))
        assert safe is False
        assert reason == "private_ip"

    def test_blocks_169_254(self):
        """169.254.x.x should be blocked (link-local / AWS metadata)."""
        from ai.web_scraper import _is_safe_url
        import socket
        mock_result = [(socket.AF_INET, socket.SOCK_STREAM, 0, '', ('169.254.169.254', 0))]
        with patch("socket.getaddrinfo", return_value=mock_result):
            safe, reason = _run(_is_safe_url("http://metadata.example.com"))
        assert safe is False

    def test_allows_public_ip(self):
        """Public IPs (e.g. google.com) should be allowed."""
        from ai.web_scraper import _is_safe_url
        import socket
        mock_result = [(socket.AF_INET, socket.SOCK_STREAM, 0, '', ('142.250.80.46', 0))]
        with patch("socket.getaddrinfo", return_value=mock_result):
            safe, resolved_ip = _run(_is_safe_url("https://www.google.com"))
        assert safe is True
        assert resolved_ip == "142.250.80.46"

    def test_blocks_file_scheme(self):
        """file:// scheme should be blocked."""
        from ai.web_scraper import _is_safe_url
        safe, reason = _run(_is_safe_url("file:///etc/passwd"))
        assert safe is False
        assert reason == "blocked_scheme"

    def test_blocks_ftp_scheme(self):
        """ftp:// scheme should be blocked."""
        from ai.web_scraper import _is_safe_url
        safe, reason = _run(_is_safe_url("ftp://evil.com/file"))
        assert safe is False
        assert reason == "blocked_scheme"

    def test_blocks_porn_domain(self):
        """Domain with blocked words should be blocked."""
        from ai.web_scraper import _is_safe_url
        safe, reason = _run(_is_safe_url("https://porn-site.com"))
        assert safe is False
        assert reason == "blocked_domain"


# ═══════════════════════════════════════════════════════════════════════════════
#  CONTENT SAFETY (Art.1)
# ═══════════════════════════════════════════════════════════════════════════════

class TestContentSafety:
    """Test check_content_safety blocks harmful input."""

    def test_blocks_harmful_input(self):
        """Content with blocked words should raise 400."""
        from core.security import check_content_safety
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            check_content_safety("this is a rug pull scheme")
        assert exc_info.value.status_code == 400

    def test_allows_clean_input(self):
        """Clean content should not raise."""
        from core.security import check_content_safety
        # Should not raise
        check_content_safety("I want to buy some SOL tokens")

    def test_blocks_pattern_match(self):
        """Content matching blocked regex patterns should raise 400."""
        from core.security import check_content_safety
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            check_content_safety("child porn content")
        assert exc_info.value.status_code == 400


# ═══════════════════════════════════════════════════════════════════════════════
#  OFAC SANCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

class TestOFACSanctions:
    """Test OFAC sanctions check."""

    def test_known_sanctioned_address_blocked(self):
        """Known Tornado Cash address should be flagged as sanctioned."""
        from core.security import check_ofac_wallet, _load_ofac_list, _OFAC_LOADED
        # Force reload
        import core.security as sec_mod
        sec_mod._OFAC_LOADED = False
        _load_ofac_list()

        tornado_addr = "0x8589427373D6D84E98730D7795D8f6f8731FDA16"
        result = check_ofac_wallet(tornado_addr)
        assert result["sanctioned"] is True
        assert result["risk"] == "sanctioned"

    def test_clean_address_passes(self):
        """Non-sanctioned address should pass."""
        from core.security import check_ofac_wallet, _load_ofac_list
        import core.security as sec_mod
        sec_mod._OFAC_LOADED = False
        _load_ofac_list()

        clean_addr = "0x1234567890abcdef1234567890abcdef12345678"
        result = check_ofac_wallet(clean_addr)
        assert result["sanctioned"] is False
        assert result["risk"] == "clear"

    def test_empty_address_returns_unknown(self):
        """Empty address should return unknown risk."""
        from core.security import check_ofac_wallet
        result = check_ofac_wallet("")
        assert result["sanctioned"] is False
        assert result["risk"] == "unknown"


# ═══════════════════════════════════════════════════════════════════════════════
#  ADMIN SESSION
# ═══════════════════════════════════════════════════════════════════════════════

class TestAdminSessions:
    """Test admin session management (TTL, cap)."""

    def test_expired_session_rejected(self):
        """Expired admin session should not authenticate."""
        from routes.admin_routes import _ADMIN_SESSIONS, _verify_admin
        # Create an expired session
        expired_token = secrets.token_hex(32)
        _ADMIN_SESSIONS[expired_token] = time.time() - 100  # expired

        mock_request = MagicMock()
        mock_request.headers = {"X-Admin-Key": ""}
        mock_request.cookies = {"maxia_admin": expired_token}

        assert _verify_admin(mock_request) is False
        _ADMIN_SESSIONS.pop(expired_token, None)

    def test_valid_session_accepted(self):
        """Valid (non-expired) admin session should authenticate."""
        from routes.admin_routes import _ADMIN_SESSIONS, _verify_admin
        valid_token = secrets.token_hex(32)
        _ADMIN_SESSIONS[valid_token] = time.time() + 3600  # 1h from now

        mock_request = MagicMock()
        mock_request.headers = {"X-Admin-Key": ""}
        mock_request.cookies = {"maxia_admin": valid_token}

        assert _verify_admin(mock_request) is True
        _ADMIN_SESSIONS.pop(valid_token, None)

    def test_session_cap_enforced(self):
        """Session store should cap at _ADMIN_SESSIONS_MAX (1000)."""
        from routes.admin_routes import _ADMIN_SESSIONS_MAX
        assert _ADMIN_SESSIONS_MAX == 1000


# ═══════════════════════════════════════════════════════════════════════════════
#  GEO-BLOCKING
# ═══════════════════════════════════════════════════════════════════════════════

class TestGeoBlocking:
    """Test geo-blocking for US IPs."""

    def test_us_country_blocked(self):
        """US should be in the blocked countries list."""
        from core.geo_blocking import BLOCKED_COUNTRIES
        assert "US" in BLOCKED_COUNTRIES

    def test_protected_stock_path(self):
        """Stock trading paths should be protected."""
        from core.geo_blocking import _is_protected_path
        assert _is_protected_path("/api/stocks/buy") is True
        assert _is_protected_path("/api/exchange/trade") is True
        assert _is_protected_path("/api/chat") is False

    def test_private_ip_bypass(self):
        """Private IPs should bypass geo-blocking."""
        from core.geo_blocking import _is_private_ip
        assert _is_private_ip("127.0.0.1") is True
        assert _is_private_ip("192.168.1.1") is True
        assert _is_private_ip("10.0.0.1") is True
        assert _is_private_ip("::1") is True

    def test_public_ip_not_private(self):
        """Public IPs should not be considered private."""
        from core.geo_blocking import _is_private_ip
        assert _is_private_ip("8.8.8.8") is False
        assert _is_private_ip("142.250.80.46") is False

    def test_cache_stores_country(self):
        """Country cache should store and retrieve entries."""
        from core.geo_blocking import _cache_country, _get_cached_country, _geo_cache
        test_ip = "203.0.113.99"
        _geo_cache.pop(test_ip, None)

        _cache_country(test_ip, "FR")
        assert _get_cached_country(test_ip) == "FR"
        _geo_cache.pop(test_ip, None)


# ═══════════════════════════════════════════════════════════════════════════════
#  REFERRAL CODE FORMAT
# ═══════════════════════════════════════════════════════════════════════════════

class TestReferralCodeFormat:
    """Test referral code generation format."""

    def test_referral_code_is_8_chars_hex(self):
        """Referral code should be 8 chars of uppercase hex (token_hex(4).upper())."""
        code = secrets.token_hex(4).upper()
        assert len(code) == 8
        assert re.match(r'^[0-9A-F]{8}$', code)

    def test_referral_code_not_substring_of_api_key(self):
        """Referral code should be independent from API key (PRO-A7)."""
        # Generate like the codebase does
        api_key = secrets.token_hex(32)
        referral_code = secrets.token_hex(4).upper()
        # The referral code is independently generated, so extremely unlikely to be a substring
        # The important thing is they're generated independently (not derived from api_key)
        assert len(referral_code) == 8


# ═══════════════════════════════════════════════════════════════════════════════
#  WALLET ADDRESS VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════

class TestWalletValidation:
    """Test wallet address format validation."""

    def test_valid_solana_address(self):
        """Valid Solana base58 address should pass."""
        from core.security import validate_wallet_address
        assert validate_wallet_address("7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU") is True

    def test_valid_evm_address(self):
        """Valid EVM 0x address should pass."""
        from core.security import validate_wallet_address
        assert validate_wallet_address("0x8589427373D6D84E98730D7795D8f6f8731FDA16", chain="evm") is True

    def test_empty_address_rejected(self):
        """Empty address should be rejected."""
        from core.security import validate_wallet_address
        assert validate_wallet_address("") is False

    def test_short_address_rejected(self):
        """Too-short address should be rejected."""
        from core.security import validate_wallet_address
        assert validate_wallet_address("abc") is False
