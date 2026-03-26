"""MAXIA V12 Backend — Comprehensive pytest test suite.

Tests all critical business logic without external API calls or DB.
Every external dependency (HTTP, DB, env vars) is mocked.
"""
import asyncio
import hashlib
import hmac
import os
import re
import sys
import time
from collections import defaultdict
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

# ── Ensure backend/ is importable ──
BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..", "backend")
sys.path.insert(0, BACKEND_DIR)


# ═══════════════════════════════════════════════════════════════════════════════
#  1. SWAP COMMISSION TIERS  (crypto_swap.py)
# ═══════════════════════════════════════════════════════════════════════════════

class TestSwapCommissions:
    """Verify BRONZE / SILVER / GOLD / WHALE thresholds and first-swap-free."""

    def test_first_swap_free(self):
        from crypto_swap import get_swap_commission_bps, get_swap_tier_name
        assert get_swap_commission_bps(100, volume_30d=0, swap_count=0) == 0
        assert get_swap_tier_name(100, volume_30d=0, swap_count=0) == "FREE"

    def test_bronze_tier_default(self):
        from crypto_swap import get_swap_commission_bps, get_swap_tier_name
        # volume_30d=0 -> BRONZE (0-1000)
        assert get_swap_commission_bps(50, volume_30d=0) == 10
        assert get_swap_tier_name(50, volume_30d=0) == "BRONZE"

    def test_bronze_upper_boundary(self):
        from crypto_swap import get_swap_commission_bps
        assert get_swap_commission_bps(100, volume_30d=999) == 10  # still BRONZE

    def test_silver_tier(self):
        from crypto_swap import get_swap_commission_bps, get_swap_tier_name
        assert get_swap_commission_bps(100, volume_30d=1000) == 5
        assert get_swap_tier_name(100, volume_30d=1000) == "SILVER"
        assert get_swap_commission_bps(100, volume_30d=4999) == 5

    def test_gold_tier(self):
        from crypto_swap import get_swap_commission_bps, get_swap_tier_name
        assert get_swap_commission_bps(100, volume_30d=5000) == 3
        assert get_swap_tier_name(100, volume_30d=5000) == "GOLD"
        assert get_swap_commission_bps(100, volume_30d=24999) == 3

    def test_whale_tier(self):
        from crypto_swap import get_swap_commission_bps, get_swap_tier_name
        assert get_swap_commission_bps(100, volume_30d=25000) == 1
        assert get_swap_tier_name(100, volume_30d=25000) == "WHALE"
        assert get_swap_commission_bps(100, volume_30d=1_000_000) == 1

    def test_tier_names_match_defined_tiers(self):
        from crypto_swap import SWAP_COMMISSION_TIERS
        expected = {"BRONZE", "SILVER", "GOLD", "WHALE"}
        assert set(SWAP_COMMISSION_TIERS.keys()) == expected


# ═══════════════════════════════════════════════════════════════════════════════
#  2. STOCK COMMISSION TIERS  (tokenized_stocks.py)
# ═══════════════════════════════════════════════════════════════════════════════

class TestStockCommissions:
    """Verify stock commission BRONZE / SILVER / GOLD / WHALE thresholds."""

    def test_bronze_stock(self):
        from tokenized_stocks import get_stock_commission_bps
        assert get_stock_commission_bps(100) == 50   # 0.5%
        assert get_stock_commission_bps(999) == 50

    def test_silver_stock(self):
        from tokenized_stocks import get_stock_commission_bps
        assert get_stock_commission_bps(1000) == 20  # 0.2%
        assert get_stock_commission_bps(4999) == 20

    def test_gold_stock(self):
        from tokenized_stocks import get_stock_commission_bps
        assert get_stock_commission_bps(5000) == 10  # 0.1%
        assert get_stock_commission_bps(24999) == 10

    def test_whale_stock(self):
        from tokenized_stocks import get_stock_commission_bps
        assert get_stock_commission_bps(25000) == 5  # 0.05%
        assert get_stock_commission_bps(500_000) == 5

    def test_stock_tier_names(self):
        from tokenized_stocks import STOCK_COMMISSION_TIERS
        expected = {"BRONZE", "SILVER", "GOLD", "WHALE"}
        assert set(STOCK_COMMISSION_TIERS.keys()) == expected


# ═══════════════════════════════════════════════════════════════════════════════
#  3. MARKETPLACE COMMISSIONS  (config.py)
# ═══════════════════════════════════════════════════════════════════════════════

class TestMarketplaceCommissions:
    """Verify per-transaction BRONZE / GOLD / WHALE from config.py."""

    def test_bronze_marketplace(self):
        from config import get_commission_bps
        assert get_commission_bps(100) == 100   # 1%
        assert get_commission_bps(499) == 100

    def test_gold_marketplace(self):
        from config import get_commission_bps
        assert get_commission_bps(500) == 50    # 0.5%
        assert get_commission_bps(4999) == 50

    def test_whale_marketplace(self):
        from config import get_commission_bps
        assert get_commission_bps(5000) == 10   # 0.1%
        assert get_commission_bps(100_000) == 10


# ═══════════════════════════════════════════════════════════════════════════════
#  4. CONTENT SAFETY  (security.py — Art.1)
# ═══════════════════════════════════════════════════════════════════════════════

class TestContentSafety:
    """Verify that blocked words/patterns are caught and safe text passes."""

    def test_safe_text_passes(self):
        from security import check_content_safety
        # Should NOT raise
        check_content_safety("Hello world, this is a legitimate AI service")
        check_content_safety("Trade 100 USDC for SOL on MAXIA marketplace")

    def test_blocked_word_caught(self):
        from security import check_content_safety
        from fastapi import HTTPException
        with pytest.raises(Exception):
            check_content_safety("this contains malware instructions")

    def test_blocked_pattern_caught(self):
        from security import check_content_safety
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            check_content_safety("content about child porn is blocked")

    def test_case_insensitive_blocking(self):
        from security import check_content_safety
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            check_content_safety("RANSOMWARE deployment guide")

    def test_multiple_blocked_words(self):
        """Every word in BLOCKED_WORDS should be caught."""
        from security import check_content_safety
        from fastapi import HTTPException
        from config import BLOCKED_WORDS
        for word in BLOCKED_WORDS:
            with pytest.raises(HTTPException):
                check_content_safety(f"test {word} test")


# ═══════════════════════════════════════════════════════════════════════════════
#  5. WALLET ADDRESS VALIDATION  (security.py)
# ═══════════════════════════════════════════════════════════════════════════════

class TestWalletValidation:
    """Verify Solana and EVM address validation logic."""

    def test_valid_solana_address(self):
        from security import validate_wallet_address
        # Typical Solana address (base58, 32-44 chars)
        assert validate_wallet_address("7v91N7iZ9mNicL8WfG6cgSCKyRXydQjLh6UYBWwm6y1Q") is True

    def test_valid_evm_address(self):
        from security import validate_wallet_address
        assert validate_wallet_address("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913") is True
        assert validate_wallet_address("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", chain="evm") is True

    def test_invalid_short_address(self):
        from security import validate_wallet_address
        assert validate_wallet_address("abc") is False
        assert validate_wallet_address("") is False

    def test_invalid_evm_wrong_prefix(self):
        from security import validate_wallet_address
        assert validate_wallet_address("1x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", chain="evm") is False

    def test_invalid_evm_wrong_length(self):
        from security import validate_wallet_address
        # Too short (39 hex chars instead of 40)
        assert validate_wallet_address("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA0291", chain="evm") is False

    def test_invalid_solana_forbidden_chars(self):
        from security import validate_wallet_address
        # Solana base58 excludes 0, O, I, l
        assert validate_wallet_address("0v91N7iZ9mNicL8WfG6cgSCKyRXydQjLh6UYBWwm6y1Q") is False

    def test_auto_detect_evm(self):
        from security import validate_wallet_address
        # Auto mode should detect 0x prefix as EVM
        assert validate_wallet_address("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", chain="auto") is True


# ═══════════════════════════════════════════════════════════════════════════════
#  6. CIRCUIT BREAKER  (chain_resilience.py)
# ═══════════════════════════════════════════════════════════════════════════════

class TestCircuitBreaker:
    """Test CLOSED -> OPEN -> HALF_OPEN -> CLOSED state transitions."""

    def _make_breaker(self, fail_max=3, reset_timeout=0.1, success_to_close=2):
        from chain_resilience import ChainCircuitBreaker
        return ChainCircuitBreaker(
            "test_chain",
            fail_max=fail_max,
            reset_timeout=reset_timeout,
            success_to_close=success_to_close,
        )

    def test_initial_state_closed(self):
        cb = self._make_breaker()
        assert cb.state == "closed"

    @pytest.mark.asyncio
    async def test_closed_to_open_after_failures(self):
        from chain_resilience import CircuitOpenError
        cb = self._make_breaker(fail_max=3)

        for i in range(3):
            with pytest.raises(ValueError):
                await cb.call(self._failing_coro())

        assert cb._state == "open"

    @pytest.mark.asyncio
    async def test_open_blocks_calls(self):
        from chain_resilience import CircuitOpenError
        cb = self._make_breaker(fail_max=1, reset_timeout=9999)

        # Trip the breaker
        with pytest.raises(ValueError):
            await cb.call(self._failing_coro())

        assert cb._state == "open"

        # Now calls should be blocked
        with pytest.raises(CircuitOpenError):
            await cb.call(self._success_coro())

    @pytest.mark.asyncio
    async def test_open_to_half_open_after_timeout(self):
        cb = self._make_breaker(fail_max=1, reset_timeout=0.05)

        # Trip the breaker
        with pytest.raises(ValueError):
            await cb.call(self._failing_coro())

        assert cb._state == "open"

        # Wait for reset_timeout
        await asyncio.sleep(0.1)

        # State property should now report half_open
        assert cb.state == "half_open"

    @pytest.mark.asyncio
    async def test_half_open_to_closed_after_successes(self):
        cb = self._make_breaker(fail_max=1, reset_timeout=0.05, success_to_close=2)

        # Trip the breaker
        with pytest.raises(ValueError):
            await cb.call(self._failing_coro())

        await asyncio.sleep(0.1)  # wait for half_open transition

        # Two successes in half_open should close the circuit
        await cb.call(self._success_coro())
        await cb.call(self._success_coro())

        assert cb._state == "closed"

    @pytest.mark.asyncio
    async def test_half_open_failure_reopens(self):
        cb = self._make_breaker(fail_max=1, reset_timeout=0.05, success_to_close=2)

        # Trip the breaker
        with pytest.raises(ValueError):
            await cb.call(self._failing_coro())

        await asyncio.sleep(0.1)

        # One failure in half_open -> back to open
        with pytest.raises(ValueError):
            await cb.call(self._failing_coro())

        assert cb._state == "open"

    @pytest.mark.asyncio
    async def test_reset_returns_to_closed(self):
        cb = self._make_breaker(fail_max=1)

        with pytest.raises(ValueError):
            await cb.call(self._failing_coro())

        assert cb._state == "open"

        await cb.reset()
        assert cb._state == "closed"
        assert cb._failures == 0

    @pytest.mark.asyncio
    async def test_stats_property(self):
        cb = self._make_breaker()
        stats = cb.stats
        assert stats["chain"] == "test_chain"
        assert stats["state"] == "closed"
        assert stats["failures"] == 0

    # Helpers
    @staticmethod
    async def _failing_coro():
        raise ValueError("RPC timeout")

    @staticmethod
    async def _success_coro():
        return {"ok": True}


class TestGetAllChainStatus:
    """Test get_all_chain_status() returns all 14 chains."""

    def test_returns_all_chains(self):
        from chain_resilience import get_all_chain_status, chain_breakers
        status = get_all_chain_status()
        # Should have entries for every chain_breaker
        assert len(status) == len(chain_breakers)
        for chain_name, info in status.items():
            assert "status" in info
            assert info["status"] in ("ok", "recovering", "down")


# ═══════════════════════════════════════════════════════════════════════════════
#  7. PYTH ORACLE STALENESS  (pyth_oracle.py)
# ═══════════════════════════════════════════════════════════════════════════════

class TestPythOracleStaleness:
    """Verify staleness thresholds: 30s for stocks, 120s for crypto."""

    def test_stock_staleness_30s(self):
        from pyth_oracle import MAX_STALENESS_STOCK_S
        assert MAX_STALENESS_STOCK_S == 30

    def test_crypto_staleness_120s(self):
        from pyth_oracle import MAX_STALENESS_CRYPTO_S
        assert MAX_STALENESS_CRYPTO_S == 120

    def test_confidence_warn_pct(self):
        from pyth_oracle import CONFIDENCE_WARN_PCT
        assert CONFIDENCE_WARN_PCT == 2.0

    def test_stale_circuit_threshold(self):
        from pyth_oracle import STALE_CIRCUIT_THRESHOLD
        assert STALE_CIRCUIT_THRESHOLD == 5

    def test_equity_feeds_exist(self):
        from pyth_oracle import EQUITY_FEEDS
        assert "AAPL" in EQUITY_FEEDS
        assert "TSLA" in EQUITY_FEEDS
        assert "NVDA" in EQUITY_FEEDS

    def test_crypto_feeds_exist(self):
        from pyth_oracle import CRYPTO_FEEDS
        assert "BTC" in CRYPTO_FEEDS
        assert "ETH" in CRYPTO_FEEDS
        assert "SOL" in CRYPTO_FEEDS


# ═══════════════════════════════════════════════════════════════════════════════
#  8. PRICE JUMP DETECTION  (tokenized_stocks.py)
# ═══════════════════════════════════════════════════════════════════════════════

class TestPriceJumpDetection:
    """>5% in 5 minutes should trigger a jump flag."""

    def test_no_history_no_jump(self):
        from tokenized_stocks import _check_price_jump
        is_jump, pct = _check_price_jump("TEST_NO_HIST", 100.0)
        assert is_jump is False
        assert pct == 0.0

    def test_small_change_no_jump(self):
        from tokenized_stocks import _record_price, _check_price_jump, _price_history
        symbol = "TEST_SMALL_CHANGE"
        _price_history.pop(symbol, None)  # clean state
        _record_price(symbol, 100.0)
        is_jump, pct = _check_price_jump(symbol, 104.0)  # 4% < 5%
        assert is_jump is False

    def test_big_change_triggers_jump(self):
        from tokenized_stocks import _record_price, _check_price_jump, _price_history
        symbol = "TEST_BIG_JUMP"
        _price_history.pop(symbol, None)
        _record_price(symbol, 100.0)
        is_jump, pct = _check_price_jump(symbol, 106.0)  # 6% > 5%
        assert is_jump is True
        assert pct == 6.0

    def test_negative_jump_triggers(self):
        from tokenized_stocks import _record_price, _check_price_jump, _price_history
        symbol = "TEST_NEG_JUMP"
        _price_history.pop(symbol, None)
        _record_price(symbol, 100.0)
        is_jump, pct = _check_price_jump(symbol, 93.0)  # -7% > 5%
        assert is_jump is True

    def test_exact_5pct_does_not_trigger(self):
        from tokenized_stocks import _record_price, _check_price_jump, _price_history
        symbol = "TEST_EXACT_5"
        _price_history.pop(symbol, None)
        _record_price(symbol, 100.0)
        is_jump, pct = _check_price_jump(symbol, 105.0)  # exactly 5% is NOT >5%
        assert is_jump is False


# ═══════════════════════════════════════════════════════════════════════════════
#  9. AGE SPREAD CALCULATION  (tokenized_stocks.py)
# ═══════════════════════════════════════════════════════════════════════════════

class TestAgeSpread:
    """0.5% per 60s of age, capped at 3%."""

    def test_fresh_price_zero_spread(self):
        from tokenized_stocks import _calc_age_spread
        assert _calc_age_spread(0) == 0.0
        assert _calc_age_spread(30) == 0.0
        assert _calc_age_spread(59) == 0.0

    def test_60s_age_spread(self):
        from tokenized_stocks import _calc_age_spread
        spread = _calc_age_spread(60)
        # 60/60 * 0.5 = 0.5%
        assert spread == 0.5

    def test_120s_age_spread(self):
        from tokenized_stocks import _calc_age_spread
        spread = _calc_age_spread(120)
        # 120/60 * 0.5 = 1.0%
        assert spread == 1.0

    def test_capped_at_3pct(self):
        from tokenized_stocks import _calc_age_spread
        spread = _calc_age_spread(600)
        # 600/60 * 0.5 = 5.0 -> capped at 3.0
        assert spread == 3.0

    def test_large_age_still_capped(self):
        from tokenized_stocks import _calc_age_spread
        spread = _calc_age_spread(10000)
        assert spread == 3.0


# ═══════════════════════════════════════════════════════════════════════════════
# 10. IP SPOOFING — get_real_ip()  (security.py)
# ═══════════════════════════════════════════════════════════════════════════════

class TestGetRealIp:
    """Verify get_real_ip() behavior with/without trusted proxy."""

    @staticmethod
    def _make_request(client_ip: str, xff: str = ""):
        """Create a mock FastAPI Request."""
        req = MagicMock()
        req.client = SimpleNamespace(host=client_ip)
        if xff:
            req.headers = {"X-Forwarded-For": xff}
        else:
            req.headers = {}
        return req

    def test_direct_connection_returns_client_ip(self):
        from security import get_real_ip
        req = self._make_request("203.0.113.42")
        assert get_real_ip(req) == "203.0.113.42"

    def test_untrusted_proxy_ignores_xff(self):
        """If client IP is NOT in trusted proxies, X-Forwarded-For is ignored."""
        from security import get_real_ip
        req = self._make_request("203.0.113.42", xff="10.0.0.1, 192.168.1.1")
        assert get_real_ip(req) == "203.0.113.42"

    def test_trusted_proxy_uses_last_xff(self):
        """If client IP IS a trusted proxy, use the last IP in XFF chain."""
        from security import get_real_ip
        req = self._make_request("127.0.0.1", xff="10.0.0.1, 203.0.113.50")
        assert get_real_ip(req) == "203.0.113.50"

    def test_trusted_proxy_single_xff(self):
        from security import get_real_ip
        req = self._make_request("127.0.0.1", xff="203.0.113.99")
        assert get_real_ip(req) == "203.0.113.99"

    def test_no_client_returns_unknown(self):
        from security import get_real_ip
        req = MagicMock()
        req.client = None
        req.headers = {}
        assert get_real_ip(req) == "unknown"


# ═══════════════════════════════════════════════════════════════════════════════
# 11. RATE LIMIT FREE ENDPOINTS  (security.py)
# ═══════════════════════════════════════════════════════════════════════════════

class TestRateLimitFreeEndpoints:
    """Verify query params don't bypass free endpoint detection (H3 fix)."""

    def test_free_endpoint_always_allowed(self):
        from security import check_rate_limit_smart
        # Free keywords: "prices", "candles", etc.
        for _ in range(200):
            assert check_rate_limit_smart("test_free_1", "/api/public/crypto/prices") is True

    def test_free_endpoint_with_query_params(self):
        """Query params should NOT bypass free endpoint check (H3)."""
        from security import check_rate_limit_smart
        assert check_rate_limit_smart("test_free_q", "/api/public/crypto/prices?token=SOL") is True

    def test_paid_endpoint_rate_limited(self):
        from security import check_rate_limit_smart, _rate_store
        # Clean state for this identifier
        _rate_store.pop("test_paid_1", None)
        # Paid endpoint — exhaust the limit (60 req/min)
        for i in range(60):
            assert check_rate_limit_smart("test_paid_1", "/api/marketplace/execute") is True
        # 61st should be blocked
        assert check_rate_limit_smart("test_paid_1", "/api/marketplace/execute") is False

    def test_mcp_is_free(self):
        from security import check_rate_limit_smart
        assert check_rate_limit_smart("test_mcp", "/mcp/manifest") is True

    def test_docs_is_free(self):
        from security import check_rate_limit_smart
        assert check_rate_limit_smart("test_docs", "/docs") is True


# ═══════════════════════════════════════════════════════════════════════════════
# 12. SESSION TOKENS  (auth.py)
# ═══════════════════════════════════════════════════════════════════════════════

class TestSessionTokens:
    """Create and verify round-trip for session tokens."""

    def test_create_and_verify_roundtrip(self):
        from auth import create_session_token, verify_session_token
        wallet = "7v91N7iZ9mNicL8WfG6cgSCKyRXydQjLh6UYBWwm6y1Q"
        token = create_session_token(wallet)

        # Verify returns the wallet
        result = verify_session_token(token)
        assert result == wallet

    def test_token_format_three_parts(self):
        from auth import create_session_token
        token = create_session_token("TestWallet123")
        parts = token.split(":")
        assert len(parts) == 3  # wallet:expiry:hmac

    def test_invalid_token_rejected(self):
        from auth import verify_session_token
        from fastapi import HTTPException
        with pytest.raises(Exception):
            verify_session_token("totally:invalid:signature")

    def test_tampered_wallet_rejected(self):
        from auth import create_session_token, verify_session_token
        from fastapi import HTTPException
        token = create_session_token("OriginalWallet")
        # Tamper with wallet part
        parts = token.split(":")
        parts[0] = "EvilWallet"
        tampered = ":".join(parts)
        with pytest.raises(HTTPException):
            verify_session_token(tampered)

    def test_expired_token_rejected(self):
        from auth import verify_session_token, _JWT_SECRET
        from fastapi import HTTPException
        # Craft an already-expired token
        wallet = "TestWallet"
        expired_time = int(time.time()) - 3600  # 1 hour ago
        payload = f"{wallet}:{expired_time}"
        sig = hmac.new(_JWT_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        expired_token = f"{payload}:{sig}"
        with pytest.raises(Exception):
            verify_session_token(expired_token)

    def test_malformed_token_rejected(self):
        from auth import verify_session_token
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            verify_session_token("only_one_part")
        with pytest.raises(HTTPException):
            verify_session_token("two:parts")


# ═══════════════════════════════════════════════════════════════════════════════
# 13. ESCROW CONFIG VALIDATION  (escrow_client.py)
# ═══════════════════════════════════════════════════════════════════════════════

class TestEscrowConfigValidation:
    """Verify _verify_escrow_config catches misconfigurations."""

    def test_same_escrow_and_treasury_address_rejected(self):
        """ESCROW_ADDRESS == TREASURY_ADDRESS must produce an error."""
        same_addr = "7v91N7iZ9mNicL8WfG6cgSCKyRXydQjLh6UYBWwm6y1Q"
        with patch("escrow_client.ESCROW_ADDRESS", same_addr), \
             patch("escrow_client.TREASURY_ADDRESS", same_addr), \
             patch("escrow_client.ESCROW_PRIVKEY_B58", ""):
            from escrow_client import _verify_escrow_config
            errors = _verify_escrow_config()
            matching = [e for e in errors if "ESCROW_ADDRESS == TREASURY_ADDRESS" in e]
            assert len(matching) >= 1

    def test_empty_escrow_address_flagged(self):
        with patch("escrow_client.ESCROW_ADDRESS", ""), \
             patch("escrow_client.TREASURY_ADDRESS", "something"), \
             patch("escrow_client.ESCROW_PRIVKEY_B58", ""):
            from escrow_client import _verify_escrow_config
            errors = _verify_escrow_config()
            matching = [e for e in errors if "ESCROW_ADDRESS non defini" in e]
            assert len(matching) >= 1

    def test_invalid_escrow_address_format(self):
        with patch("escrow_client.ESCROW_ADDRESS", "not-a-valid-address!!!"), \
             patch("escrow_client.TREASURY_ADDRESS", "something"), \
             patch("escrow_client.ESCROW_PRIVKEY_B58", ""):
            from escrow_client import _verify_escrow_config
            errors = _verify_escrow_config()
            matching = [e for e in errors if "invalide" in e.lower()]
            assert len(matching) >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# 14. SQL COLUMN WHITELIST  (database.py)
# ═══════════════════════════════════════════════════════════════════════════════

class TestSQLColumnWhitelist:
    """Verify only allowed columns pass the whitelist check."""

    def test_agent_allowed_columns(self):
        from database import ALLOWED_AGENT_COLUMNS
        # These should be allowed
        for col in ["name", "wallet", "description", "tier", "volume_30d"]:
            assert col in ALLOWED_AGENT_COLUMNS

    def test_agent_dangerous_columns_blocked(self):
        from database import ALLOWED_AGENT_COLUMNS
        # SQL injection attempts should NOT be in the whitelist
        for dangerous in ["password", "admin", "1; DROP TABLE", "wallet; --", "' OR 1=1"]:
            assert dangerous not in ALLOWED_AGENT_COLUMNS

    def test_service_allowed_columns(self):
        from database import ALLOWED_SERVICE_COLUMNS
        for col in ["name", "description", "price_usdc", "status", "rating"]:
            assert col in ALLOWED_SERVICE_COLUMNS

    def test_service_dangerous_columns_blocked(self):
        from database import ALLOWED_SERVICE_COLUMNS
        for dangerous in ["secret_key", "privkey", "admin", "DROP"]:
            assert dangerous not in ALLOWED_SERVICE_COLUMNS

    def test_whitelists_are_frozensets(self):
        """Frozensets are immutable — cannot be modified at runtime."""
        from database import ALLOWED_AGENT_COLUMNS, ALLOWED_SERVICE_COLUMNS
        assert isinstance(ALLOWED_AGENT_COLUMNS, frozenset)
        assert isinstance(ALLOWED_SERVICE_COLUMNS, frozenset)


# ═══════════════════════════════════════════════════════════════════════════════
# 15. GPU TIERS FALLBACK  (config.py)
# ═══════════════════════════════════════════════════════════════════════════════

class TestGPUTiers:
    """Verify GPU_TIERS_FALLBACK structure and prices."""

    def test_fallback_tiers_exist(self):
        from config import GPU_TIERS_FALLBACK
        assert len(GPU_TIERS_FALLBACK) >= 10

    def test_each_tier_has_required_fields(self):
        from config import GPU_TIERS_FALLBACK
        for tier in GPU_TIERS_FALLBACK:
            assert "id" in tier
            assert "label" in tier
            assert "vram_gb" in tier
            assert "base_price_per_hour" in tier

    def test_prices_are_positive(self):
        from config import GPU_TIERS_FALLBACK
        for tier in GPU_TIERS_FALLBACK:
            assert tier["base_price_per_hour"] > 0, f"{tier['id']} has non-positive price"

    def test_h100_exists(self):
        from config import GPU_TIERS_FALLBACK
        ids = [t["id"] for t in GPU_TIERS_FALLBACK]
        assert "h100_sxm" in ids or "h100_nvl" in ids


# ═══════════════════════════════════════════════════════════════════════════════
# 16. SWAP QUOTE VALIDATION  (crypto_swap.py)
# ═══════════════════════════════════════════════════════════════════════════════

class TestSwapQuoteValidation:
    """Test input validation for get_swap_quote (without network calls)."""

    @pytest.mark.asyncio
    async def test_unknown_from_token(self):
        from crypto_swap import get_swap_quote
        result = await get_swap_quote("FAKECOIN", "SOL", 100)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_unknown_to_token(self):
        from crypto_swap import get_swap_quote
        result = await get_swap_quote("SOL", "FAKECOIN", 100)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_same_token_rejected(self):
        from crypto_swap import get_swap_quote
        result = await get_swap_quote("SOL", "SOL", 100)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_negative_amount_rejected(self):
        from crypto_swap import get_swap_quote
        result = await get_swap_quote("SOL", "USDC", -10)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_zero_amount_rejected(self):
        from crypto_swap import get_swap_quote
        result = await get_swap_quote("SOL", "USDC", 0)
        assert "error" in result


# ═══════════════════════════════════════════════════════════════════════════════
# 17. SUPPORTED TOKENS INTEGRITY  (crypto_swap.py)
# ═══════════════════════════════════════════════════════════════════════════════

class TestSupportedTokens:
    """Verify the token catalog integrity."""

    def test_sol_and_usdc_present(self):
        from crypto_swap import SUPPORTED_TOKENS
        assert "SOL" in SUPPORTED_TOKENS
        assert "USDC" in SUPPORTED_TOKENS

    def test_token_has_required_fields(self):
        from crypto_swap import SUPPORTED_TOKENS
        for symbol, info in SUPPORTED_TOKENS.items():
            assert "mint" in info, f"{symbol} missing mint"
            assert "name" in info, f"{symbol} missing name"
            assert "decimals" in info, f"{symbol} missing decimals"

    def test_at_least_50_tokens(self):
        """CLAUDE.md says 71 tokens."""
        from crypto_swap import SUPPORTED_TOKENS
        assert len(SUPPORTED_TOKENS) >= 50


# ═══════════════════════════════════════════════════════════════════════════════
# 18. BURST PROTECTION  (security.py)
# ═══════════════════════════════════════════════════════════════════════════════

class TestBurstProtection:
    """Anti-DDoS burst limit: 20 req/2s, 60s ban."""

    def test_normal_traffic_passes(self):
        from security import check_burst_limit, _burst_store, _burst_bans
        ip = "test_burst_normal"
        _burst_store.pop(ip, None)
        _burst_bans.pop(ip, None)
        for _ in range(10):
            assert check_burst_limit(ip) is True

    def test_burst_triggers_ban(self):
        from security import check_burst_limit, _burst_store, _burst_bans, BURST_LIMIT
        ip = "test_burst_ban"
        _burst_store.pop(ip, None)
        _burst_bans.pop(ip, None)
        # Fill up to the limit
        for _ in range(BURST_LIMIT):
            check_burst_limit(ip)
        # Next one should be banned
        assert check_burst_limit(ip) is False


# ═══════════════════════════════════════════════════════════════════════════════
# 19. NONCE CLEANUP  (auth.py)
# ═══════════════════════════════════════════════════════════════════════════════

class TestNonceCleanup:
    """Verify nonce cleanup prevents unbounded memory growth."""

    def test_expired_nonces_cleaned(self):
        from auth import NONCES, _cleanup_nonces, NONCE_TTL
        # Add some expired nonces
        expired_time = time.time() - NONCE_TTL - 100
        for i in range(10):
            NONCES[f"expired_wallet_{i}"] = (f"nonce_{i}", expired_time)
        # Add one valid nonce
        NONCES["valid_wallet"] = ("valid_nonce", time.time() + 300)

        _cleanup_nonces()

        assert "valid_wallet" in NONCES
        # All expired should be removed
        for i in range(10):
            assert f"expired_wallet_{i}" not in NONCES


# ═══════════════════════════════════════════════════════════════════════════════
# 20. COMMISSION TIER CONSISTENCY
# ═══════════════════════════════════════════════════════════════════════════════

class TestTierConsistency:
    """Higher volumes / amounts should always get lower or equal fees."""

    def test_swap_tiers_monotonically_decrease(self):
        from crypto_swap import get_swap_commission_bps
        volumes = [0, 500, 1000, 5000, 25000, 100_000]
        bps = [get_swap_commission_bps(100, volume_30d=v) for v in volumes]
        for i in range(1, len(bps)):
            assert bps[i] <= bps[i - 1], f"BPS should decrease: {bps}"

    def test_stock_tiers_monotonically_decrease(self):
        from tokenized_stocks import get_stock_commission_bps
        amounts = [100, 1000, 5000, 25000, 100_000]
        bps = [get_stock_commission_bps(a) for a in amounts]
        for i in range(1, len(bps)):
            assert bps[i] <= bps[i - 1], f"BPS should decrease: {bps}"

    def test_marketplace_tiers_monotonically_decrease(self):
        from config import get_commission_bps
        amounts = [100, 500, 5000, 50_000]
        bps = [get_commission_bps(a) for a in amounts]
        for i in range(1, len(bps)):
            assert bps[i] <= bps[i - 1], f"BPS should decrease: {bps}"
