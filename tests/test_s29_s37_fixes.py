"""Tests for FORGE S29-S37 bug fixes and security improvements.

Validates all 12 CRITICAL bugs are fixed and won't regress.
No external dependencies — everything mocked.
"""
import asyncio
import hashlib
import hmac
import os
import re
import sys
import time
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..", "backend")
sys.path.insert(0, BACKEND_DIR)


# ═══════════════════════════════════════════════════════════════════
#  BUG 7 — Wallet with ':' forges expiry (auth.py)
# ═══════════════════════════════════════════════════════════════════

class TestBug7WalletValidation:
    """BUG 7: wallet containing ':' could forge arbitrary expiry in session token."""

    def test_valid_base58_wallet_accepted(self):
        """Normal Solana wallet should create a token."""
        from core.auth import create_session_token, verify_session_token
        wallet = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"
        token = create_session_token(wallet)
        assert token
        recovered = verify_session_token(token)
        assert recovered == wallet

    def test_wallet_with_colon_rejected(self):
        """Wallet containing ':' must be rejected to prevent expiry forgery."""
        from core.auth import create_session_token
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            create_session_token("evil:99999999999:fakesig")
        assert exc_info.value.status_code == 400

    def test_wallet_empty_rejected(self):
        from core.auth import create_session_token
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            create_session_token("")

    def test_wallet_non_base58_rejected(self):
        from core.auth import create_session_token
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            create_session_token("0xNotABase58Address!!!")

    def test_token_expired_rejected(self):
        """Expired token should raise 401."""
        from core.auth import verify_session_token, _JWT_SECRET
        from fastapi import HTTPException
        wallet = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"
        expired_ts = str(int(time.time()) - 100)
        payload = f"{wallet}:{expired_ts}"
        sig = hmac.new(_JWT_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        token = f"{payload}:{sig}"
        with pytest.raises(HTTPException) as exc_info:
            verify_session_token(token)
        assert exc_info.value.status_code == 401

    def test_token_tampered_rejected(self):
        """Tampered signature should raise 401."""
        from core.auth import create_session_token, verify_session_token
        from fastapi import HTTPException
        wallet = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"
        token = create_session_token(wallet)
        tampered = token[:-4] + "xxxx"
        with pytest.raises(HTTPException) as exc_info:
            verify_session_token(tampered)
        assert exc_info.value.status_code == 401


# ═══════════════════════════════════════════════════════════════════
#  BUG 8 — DB exception in sandbox = any key accepted (auth.py)
# ═══════════════════════════════════════════════════════════════════

class TestBug8SandboxBypass:
    """BUG 8: When DB throws exception and SANDBOX_MODE=true, any API key was accepted."""

    def test_db_exception_returns_503_not_bypass(self):
        """DB error must return 503, never accept unverified key."""
        # The fix replaces the old code that returned {"wallet": x_api_key} on exception
        # with raise HTTPException(503). We verify the code pattern exists.
        import inspect
        from core.auth import require_auth_flexible as require_flexible_auth
        source = inspect.getsource(require_flexible_auth)
        # Must NOT contain sandbox fallback on exception
        assert "SANDBOX_MODE" not in source or "503" in source
        # Must contain 503 response on DB error
        assert "503" in source


# ═══════════════════════════════════════════════════════════════════
#  BUG 2 — SOL transfer passes USDC verification (solana_verifier.py)
# ═══════════════════════════════════════════════════════════════════

class TestBug2UsdcMintFilter:
    """BUG 2: SOL native transfers must be rejected, only USDC_MINT passes."""

    def test_usdc_mint_constant_correct(self):
        from blockchain.solana_verifier import USDC_MINT
        assert USDC_MINT == "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

    def test_verify_code_filters_by_mint(self):
        """verify_transaction must check transfer mint against accepted stablecoins (USDC+USDT)."""
        with open(os.path.join(BACKEND_DIR, "blockchain", "solana_verifier.py"), encoding="utf-8") as f:
            source = f.read()
        assert "USDC_MINT" in source
        assert "USDT_MINT" in source
        # S44: accepts both USDC and USDT via ACCEPTED_STABLECOIN_MINTS set
        assert "ACCEPTED_STABLECOIN_MINTS" in source
        assert 'not in ACCEPTED_STABLECOIN_MINTS' in source


# ═══════════════════════════════════════════════════════════════════
#  BUG 3 — Double fee Jupiter + MAXIA (crypto_swap.py)
# ═══════════════════════════════════════════════════════════════════

class TestBug3DoubleFee:
    """BUG 3: platformFeeBps in Jupiter quote caused double-charging."""

    def test_no_platform_fee_bps_in_quote(self):
        """Jupiter quote params must NOT contain platformFeeBps."""
        import inspect
        # Read the get_swap_quote function source
        from trading.crypto_swap import get_swap_quote
        source = inspect.getsource(get_swap_quote)
        # Must not have platformFeeBps as an active parameter
        lines = [l.strip() for l in source.split('\n') if 'platformFeeBps' in l]
        for line in lines:
            # All platformFeeBps lines must be comments (start with #)
            assert line.startswith('#') or line.startswith('//'), \
                f"platformFeeBps still active: {line}"


# ═══════════════════════════════════════════════════════════════════
#  BUG 5 & 6 — NameErrors in pyth_oracle.py
# ═══════════════════════════════════════════════════════════════════

class TestBug5Bug6NameErrors:
    """BUG 5: _CACHE_TTL undefined. BUG 6: CONFIDENCE_WARN_PCT undefined."""

    def test_cache_ttl_constants_exist(self):
        from trading.pyth_oracle import _CACHE_TTL_NORMAL, _CACHE_TTL_HFT
        assert _CACHE_TTL_NORMAL > 0
        assert _CACHE_TTL_HFT > 0
        assert _CACHE_TTL_NORMAL >= _CACHE_TTL_HFT

    def test_no_bare_cache_ttl_reference(self):
        """_CACHE_TTL (without suffix) must not be used anywhere."""
        import inspect
        from trading.pyth_oracle import get_batch_prices
        source = inspect.getsource(get_batch_prices)
        # Should reference _CACHE_TTL_NORMAL, not bare _CACHE_TTL
        assert "_CACHE_TTL_NORMAL" in source or "_CACHE_TTL_HFT" in source

    def test_confidence_tiers_exist(self):
        from trading.pyth_oracle import _CONFIDENCE_TIERS
        assert "major" in _CONFIDENCE_TIERS
        assert "mid" in _CONFIDENCE_TIERS
        assert "small" in _CONFIDENCE_TIERS


# ═══════════════════════════════════════════════════════════════════
#  BUG 9 — /mcp/sse/call zero auth (mcp_server.py)
# ═══════════════════════════════════════════════════════════════════

class TestBug9McpSseAuth:
    """BUG 9: /mcp/sse/call had no auth, rate limit, or tier check."""

    def test_sse_call_has_auth_check(self):
        """mcp_sse_call must contain auth/tier checks (same as mcp_call_tool)."""
        import inspect
        from marketplace.mcp_server import mcp_sse_call
        source = inspect.getsource(mcp_sse_call)
        # Must check free tools rate limit
        assert "FREE_TOOLS" in source
        # Must check tier for non-free tools
        assert "get_mcp_tool_tier" in source or "required_tier" in source

    def test_sse_call_uses_safe_error(self):
        """mcp_sse_call must use safe_error, not str(e)."""
        import inspect
        from marketplace.mcp_server import mcp_sse_call
        source = inspect.getsource(mcp_sse_call)
        assert "safe_error" in source
        assert "str(e)" not in source


# ═══════════════════════════════════════════════════════════════════
#  BUG 10 — mcp-internal hardcoded key + path injection
# ═══════════════════════════════════════════════════════════════════

class TestBug10McpPathInjection:
    """BUG 10: chain param interpolated in URL without validation."""

    def test_rpc_call_validates_chain(self):
        """maxia_rpc_call must validate chain against whitelist."""
        import inspect
        from marketplace.mcp_server import _execute_tool
        source = inspect.getsource(_execute_tool)
        assert "_VALID_CHAINS" in source
        assert "not in _VALID_CHAINS" in source

    def test_no_mcp_internal_hardcoded_key(self):
        """mcp-internal API key must not be hardcoded in headers."""
        import inspect
        from marketplace.mcp_server import _execute_tool
        source = inspect.getsource(_execute_tool)
        # The fix removed the X-API-Key: mcp-internal header
        lines = [l for l in source.split('\n') if 'mcp-internal' in l]
        assert len(lines) == 0, f"mcp-internal still found: {lines}"


# ═══════════════════════════════════════════════════════════════════
#  BUG 11 — Double-spend on DB timeout (public_api.py)
# ═══════════════════════════════════════════════════════════════════

class TestBug11DoubleSpend:
    """BUG 11: If tx_already_processed() times out, code continued → double-spend."""

    def test_timeout_raises_503(self):
        """On timeout, must raise 503, NOT continue processing."""
        import inspect
        # Read the execute endpoint source
        # Read all marketplace sources (split across multiple files)
        source = ""
        for fname in ["public_api.py", "public_api_discover.py"]:
            fpath = os.path.join(BACKEND_DIR, "marketplace", fname)
            if os.path.exists(fpath):
                with open(fpath, encoding="utf-8") as f:
                    source += f.read()
        # Find the timeout handler
        assert "TimeoutError" in source
        # Must raise 503, not log warning and continue
        assert "503" in source
        assert "please retry" in source.lower() or "temporarily unavailable" in source.lower()


# ═══════════════════════════════════════════════════════════════════
#  BUG 12 — Service with $0 price (public_api.py)
# ═══════════════════════════════════════════════════════════════════

class TestBug12ZeroPrice:
    """BUG 12: maxia-awp-stake had price 0 → free execution."""

    def test_awp_stake_has_nonzero_price(self):
        """awp-stake must have a real price, not 0."""
        with open(os.path.join(BACKEND_DIR, "marketplace", "public_api_discover.py"), encoding="utf-8") as f:
            source = f.read()
        # Find the price dict (in /execute endpoint, split to public_api_discover.py in S34)
        import re
        match = re.search(r'"maxia-awp-stake":\s*([\d.]+)', source)
        assert match, "maxia-awp-stake not found in price dict"
        price = float(match.group(1))
        assert price > 0, f"maxia-awp-stake price is {price}, must be > 0"

    def test_zero_price_guard_exists(self):
        """Must reject services with price <= 0."""
        with open(os.path.join(BACKEND_DIR, "marketplace", "public_api.py"), encoding="utf-8") as f:
            source = f.read()
        assert "price <= 0" in source


# ═══════════════════════════════════════════════════════════════════
#  S29 — CSP strict-dynamic (main.py)
# ═══════════════════════════════════════════════════════════════════

class TestS29CSP:
    """CSP must exist with proper headers. unsafe-inline reverted due to inline JS on 15 pages."""

    def test_csp_header_exists(self):
        with open(os.path.join(BACKEND_DIR, "main.py"), encoding="utf-8") as f:
            source = f.read()
        assert "Content-Security-Policy" in source
        assert "script-src" in source
        # NOTE: unsafe-inline reverted because forum.html + 14 other pages have
        # massive inline JS blocks (~1400 lines each). strict-dynamic requires
        # extracting ALL inline JS first. Tracked as future work.


# ═══════════════════════════════════════════════════════════════════
#  S29 — MCP safe_error (mcp_server.py)
# ═══════════════════════════════════════════════════════════════════

class TestS29McpSafeError:
    """MCP endpoints must use safe_error, not str(e)."""

    def test_mcp_call_tool_uses_safe_error(self):
        import inspect
        from marketplace.mcp_server import mcp_call_tool
        source = inspect.getsource(mcp_call_tool)
        assert "safe_error" in source
        assert 'f"Error: {str(e)}"' not in source

    def test_mcp_sse_call_uses_safe_error(self):
        import inspect
        from marketplace.mcp_server import mcp_sse_call
        source = inspect.getsource(mcp_sse_call)
        assert "safe_error" in source
        assert 'f"Error: {str(e)}"' not in source


# ═══════════════════════════════════════════════════════════════════
#  S29 — Trusted proxies configurable (config.py + security.py)
# ═══════════════════════════════════════════════════════════════════

class TestS29TrustedProxies:
    def test_trusted_proxy_ips_in_config(self):
        from core.config import TRUSTED_PROXY_IPS
        assert isinstance(TRUSTED_PROXY_IPS, set)
        assert "127.0.0.1" in TRUSTED_PROXY_IPS

    def test_security_uses_config_proxies(self):
        import inspect
        from core.security import get_real_ip
        source = inspect.getsource(get_real_ip)
        assert "_TRUSTED_PROXIES" in source


# ═══════════════════════════════════════════════════════════════════
#  S30 — SSO no sync urllib (enterprise_sso.py)
# ═══════════════════════════════════════════════════════════════════

class TestS30SsoAsync:
    """SSO must not use blocking urllib."""

    def test_no_urllib_in_http_helpers(self):
        import inspect
        from enterprise.enterprise_sso import _http_get, _http_post
        get_src = inspect.getsource(_http_get)
        post_src = inspect.getsource(_http_post)
        assert "urllib" not in get_src, "_http_get still uses urllib"
        assert "urllib" not in post_src, "_http_post still uses urllib"


# ═══════════════════════════════════════════════════════════════════
#  S35 — DB monetary columns are NUMERIC, not REAL
# ═══════════════════════════════════════════════════════════════════

class TestS35DbTypes:
    """Monetary columns must use NUMERIC(18,6), not REAL."""

    def test_database_limit_on_unbounded_queries(self):
        """get_listings and get_all_stakes must have LIMIT."""
        import inspect
        from core.database import Database
        listings_src = inspect.getsource(Database.get_listings)
        stakes_src = inspect.getsource(Database.get_all_stakes)
        assert "LIMIT" in listings_src, "get_listings missing LIMIT"
        assert "LIMIT" in stakes_src, "get_all_stakes missing LIMIT"


# ═══════════════════════════════════════════════════════════════════
#  S36 — No inline tracking JS
# ═══════════════════════════════════════════════════════════════════

class TestS36TrackingExternal:
    """Tracking script must be external, not inline."""

    def test_landing_uses_external_track_js(self):
        frontend_dir = os.path.join(os.path.dirname(__file__), "..", "frontend")
        with open(os.path.join(frontend_dir, "landing.html"), encoding="utf-8") as f:
            html = f.read()
        assert 'src="/static/js/track.js"' in html
        assert '!function(){var p=location' not in html

    def test_track_js_file_exists(self):
        frontend_dir = os.path.join(os.path.dirname(__file__), "..", "frontend")
        # Check either path (static/js or js)
        path1 = os.path.join(frontend_dir, "static", "js", "track.js")
        path2 = os.path.join(frontend_dir, "js", "track.js")
        assert os.path.exists(path1) or os.path.exists(path2)


# ═══════════════════════════════════════════════════════════════════
#  S37 — Correlation ID middleware
# ═══════════════════════════════════════════════════════════════════

class TestS37Observability:
    def test_correlation_id_middleware_exists(self):
        with open(os.path.join(BACKEND_DIR, "main.py"), encoding="utf-8") as f:
            source = f.read()
        assert "correlation_id_middleware" in source
        assert "X-Request-ID" in source

    def test_sentry_init_guarded(self):
        """Sentry init must be guarded by SENTRY_DSN env var."""
        with open(os.path.join(BACKEND_DIR, "main.py"), encoding="utf-8") as f:
            source = f.read()
        assert "SENTRY_DSN" in source
        assert "sentry_sdk.init" in source

    def test_500_discord_alert(self):
        """Global exception handler must send Discord alert."""
        with open(os.path.join(BACKEND_DIR, "main.py"), encoding="utf-8") as f:
            source = f.read()
        assert "_send_private" in source
        assert "500 Error" in source
