"""MAXIA V12 — Critical Module Tests.

Tests for auth.py, intent.py, price_oracle.py, crypto_swap.py, database.py.
All external dependencies (HTTP, Redis, Solana RPC) are mocked.
Each test is independent — no shared mutable state between tests.
"""
import asyncio
import hashlib
import hmac
import json
import os
import sys
import time
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Ensure backend/ is importable ──
BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..", "backend")
sys.path.insert(0, BACKEND_DIR)


# ═══════════════════════════════════════════════════════════════════════════════
#  1. AUTH MODULE (auth.py) — JWT, brute force, API key
# ═══════════════════════════════════════════════════════════════════════════════


class TestJWTCreation:
    """Test create_session_token creates valid signed tokens."""

    def test_creates_three_part_token(self):
        from core.auth import create_session_token
        wallet = "7v91N7iZ9mNicL8WfG6cgSCKyRXydQjLh6UYBWwm6y1Q"
        token = create_session_token(wallet)
        parts = token.rsplit(":", 2)
        assert len(parts) == 3, "Token should have wallet:expiry:hmac"

    def test_expiry_is_24h_in_future(self):
        from core.auth import create_session_token
        wallet = "7v91N7iZ9mNicL8WfG6cgSCKyRXydQjLh6UYBWwm6y1Q"
        token = create_session_token(wallet)
        parts = token.rsplit(":", 2)
        expiry = int(parts[1])
        now = int(time.time())
        # Should be ~24h (86400s) in the future, allow 10s tolerance
        assert 86390 <= (expiry - now) <= 86410

    def test_hmac_is_hex_64_chars(self):
        from core.auth import create_session_token
        wallet = "7v91N7iZ9mNicL8WfG6cgSCKyRXydQjLh6UYBWwm6y1Q"
        token = create_session_token(wallet)
        sig = token.rsplit(":", 2)[2]
        assert len(sig) == 64  # SHA-256 hex digest
        assert all(c in "0123456789abcdef" for c in sig)

    def test_rejects_invalid_wallet_format(self):
        from core.auth import create_session_token
        from fastapi import HTTPException
        # Wallet with forbidden base58 chars (0, O, I, l) or special chars
        with pytest.raises(HTTPException) as exc_info:
            create_session_token("invalid:wallet:with:colons")
        assert exc_info.value.status_code == 400

    def test_rejects_wallet_with_colons(self):
        """Colons in wallet could forge the expiry field (BUG 7 fix)."""
        from core.auth import create_session_token
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            create_session_token("wallet:fake_expiry:fake_sig")


class TestJWTVerification:
    """Test verify_session_token correctly validates or rejects tokens."""

    def test_roundtrip_valid_token(self):
        from core.auth import create_session_token, verify_session_token
        wallet = "7v91N7iZ9mNicL8WfG6cgSCKyRXydQjLh6UYBWwm6y1Q"
        token = create_session_token(wallet)
        assert verify_session_token(token) == wallet

    def test_different_wallets_produce_different_tokens(self):
        from core.auth import create_session_token
        t1 = create_session_token("7v91N7iZ9mNicL8WfG6cgSCKyRXydQjLh6UYBWwm6y1Q")
        t2 = create_session_token("DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263")
        assert t1 != t2

    def test_rejects_tampered_signature(self):
        from core.auth import create_session_token, verify_session_token
        from fastapi import HTTPException
        token = create_session_token("7v91N7iZ9mNicL8WfG6cgSCKyRXydQjLh6UYBWwm6y1Q")
        parts = token.rsplit(":", 2)
        # Flip one character in the HMAC
        tampered_sig = "a" + parts[2][1:]
        tampered_token = f"{parts[0]}:{parts[1]}:{tampered_sig}"
        with pytest.raises(HTTPException) as exc_info:
            verify_session_token(tampered_token)
        assert exc_info.value.status_code == 401

    def test_rejects_tampered_expiry(self):
        from core.auth import create_session_token, verify_session_token
        from fastapi import HTTPException
        token = create_session_token("7v91N7iZ9mNicL8WfG6cgSCKyRXydQjLh6UYBWwm6y1Q")
        parts = token.rsplit(":", 2)
        # Change expiry to a different value
        tampered_token = f"{parts[0]}:{int(parts[1]) + 1}:{parts[2]}"
        with pytest.raises(HTTPException):
            verify_session_token(tampered_token)

    def test_rejects_expired_token(self):
        from core.auth import verify_session_token, _JWT_SECRET
        from fastapi import HTTPException
        wallet = "7v91N7iZ9mNicL8WfG6cgSCKyRXydQjLh6UYBWwm6y1Q"
        expired_time = int(time.time()) - 3600
        payload = f"{wallet}:{expired_time}"
        sig = hmac.new(_JWT_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        expired_token = f"{payload}:{sig}"
        with pytest.raises(HTTPException) as exc_info:
            verify_session_token(expired_token)
        assert exc_info.value.status_code == 401

    def test_rejects_malformed_single_part(self):
        from core.auth import verify_session_token
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            verify_session_token("single_part_only")
        assert exc_info.value.status_code == 401

    def test_rejects_malformed_two_parts(self):
        from core.auth import verify_session_token
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            verify_session_token("two:parts")


class TestBruteForceLockout:
    """Test _check_brute_force and _record_failed use in-memory fallback."""

    @pytest.mark.asyncio
    async def test_allows_below_threshold(self):
        from core.auth import _check_brute_force, _record_failed, _FAILED_ATTEMPTS
        wallet = f"test_bf_below_{uuid.uuid4().hex[:8]}"
        _FAILED_ATTEMPTS.pop(wallet, None)
        # Record a few failures (below 10)
        for _ in range(5):
            await _record_failed(wallet)
        # Should NOT raise
        await _check_brute_force(wallet)

    @pytest.mark.asyncio
    async def test_blocks_after_max_attempts(self):
        from core.auth import _check_brute_force, _record_failed, _FAILED_ATTEMPTS, _MAX_FAILED_ATTEMPTS
        from fastapi import HTTPException
        wallet = f"test_bf_block_{uuid.uuid4().hex[:8]}"
        _FAILED_ATTEMPTS.pop(wallet, None)
        # Record max failures using in-memory fallback
        for _ in range(_MAX_FAILED_ATTEMPTS):
            await _record_failed(wallet)
        # Should raise 429
        with pytest.raises(HTTPException) as exc_info:
            await _check_brute_force(wallet)
        assert exc_info.value.status_code == 429

    @pytest.mark.asyncio
    async def test_lockout_message_mentions_5_minutes(self):
        from core.auth import _check_brute_force, _record_failed, _FAILED_ATTEMPTS, _MAX_FAILED_ATTEMPTS
        from fastapi import HTTPException
        wallet = f"test_bf_msg_{uuid.uuid4().hex[:8]}"
        _FAILED_ATTEMPTS.pop(wallet, None)
        for _ in range(_MAX_FAILED_ATTEMPTS):
            await _record_failed(wallet)
        with pytest.raises(HTTPException) as exc_info:
            await _check_brute_force(wallet)
        assert "5 minutes" in exc_info.value.detail


class TestRequireSessionAuth:
    """Test require_session_auth dependency."""

    @pytest.mark.asyncio
    async def test_valid_bearer_token(self):
        from core.auth import require_session_auth, create_session_token
        wallet = "7v91N7iZ9mNicL8WfG6cgSCKyRXydQjLh6UYBWwm6y1Q"
        token = create_session_token(wallet)
        result = await require_session_auth(authorization=f"Bearer {token}")
        assert result == wallet

    @pytest.mark.asyncio
    async def test_missing_authorization_header(self):
        from core.auth import require_session_auth
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await require_session_auth(authorization=None)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_invalid_authorization_format(self):
        from core.auth import require_session_auth
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            await require_session_auth(authorization="Basic dXNlcjpwYXNz")


class TestNonceRedisHelpers:
    """Test nonce storage functions are callable and have correct TTL."""

    def test_nonce_ttl_is_300(self):
        from core.auth import NONCE_TTL
        assert NONCE_TTL == 300

    def test_nonce_functions_are_async_callables(self):
        from core.auth import _nonce_set, _nonce_get, _nonce_delete, _nonce_mark_used, _nonce_is_used
        import asyncio
        assert asyncio.iscoroutinefunction(_nonce_set)
        assert asyncio.iscoroutinefunction(_nonce_get)
        assert asyncio.iscoroutinefunction(_nonce_delete)
        assert asyncio.iscoroutinefunction(_nonce_mark_used)
        assert asyncio.iscoroutinefunction(_nonce_is_used)


# ═══════════════════════════════════════════════════════════════════════════════
#  2. INTENT MODULE (intent.py) — ed25519 sign/verify, anti-replay, expiry
# ═══════════════════════════════════════════════════════════════════════════════


class TestIntentLegacySignVerify:
    """Test legacy sign_intent / verify_intent_legacy (nacl-based)."""

    @staticmethod
    def _make_keypair():
        """Generate an ed25519 keypair using PyNaCl."""
        from nacl.signing import SigningKey
        import base58
        sk = SigningKey.generate()
        vk = sk.verify_key
        private_hex = sk.encode().hex()
        public_b58 = base58.b58encode(bytes(vk)).decode()
        return private_hex, public_b58

    def test_sign_returns_all_required_fields(self):
        from marketplace.intent import sign_intent_legacy
        priv, pub = self._make_keypair()
        envelope = sign_intent_legacy(
            action="swap",
            params={"from": "USDC", "to": "SOL", "amount": 100},
            private_key_hex=priv,
            did="did:web:maxiaworld.app:agent:test1",
        )
        required = ["v", "did", "action", "params", "nonce", "expires", "sig"]
        for field in required:
            assert field in envelope, f"Missing field: {field}"

    def test_sign_sets_correct_action(self):
        from marketplace.intent import sign_intent_legacy
        priv, pub = self._make_keypair()
        envelope = sign_intent_legacy(
            action="gpu_rent",
            params={"tier": "h100_sxm"},
            private_key_hex=priv,
            did="did:web:maxiaworld.app:agent:test2",
        )
        assert envelope["action"] == "gpu_rent"
        assert envelope["v"] == 1

    def test_sign_expiry_is_in_future(self):
        from marketplace.intent import sign_intent_legacy
        from datetime import datetime, timezone
        priv, pub = self._make_keypair()
        envelope = sign_intent_legacy(
            action="swap",
            params={},
            private_key_hex=priv,
            did="did:web:maxiaworld.app:agent:test3",
            expires_s=300,
        )
        expires_dt = datetime.fromisoformat(envelope["expires"].replace("Z", "+00:00"))
        assert expires_dt > datetime.now(timezone.utc)

    def test_sign_nonce_is_unique(self):
        from marketplace.intent import sign_intent_legacy
        priv, pub = self._make_keypair()
        nonces = set()
        for i in range(10):
            envelope = sign_intent_legacy(
                action="swap", params={"i": i},
                private_key_hex=priv,
                did=f"did:web:maxiaworld.app:agent:test4_{i}",
            )
            nonces.add(envelope["nonce"])
        # Nonces use time.time_ns() + DID so different DIDs guarantee uniqueness
        assert len(nonces) == 10, "Each nonce should be unique"

    @pytest.mark.asyncio
    async def test_verify_valid_signature(self):
        from marketplace.intent import sign_intent_legacy, verify_intent_legacy
        priv, pub = self._make_keypair()
        envelope = sign_intent_legacy(
            action="swap",
            params={"from": "USDC", "to": "SOL"},
            private_key_hex=priv,
            did="did:web:maxiaworld.app:agent:verify_test",
        )
        # Mock the anti-replay store to allow verification
        # _nonce_is_used/_nonce_mark_used are imported from auth inside verify_intent_legacy
        with patch("core.auth._nonce_is_used", new_callable=AsyncMock, return_value=False), \
             patch("core.auth._nonce_mark_used", new_callable=AsyncMock):
            result = await verify_intent_legacy(envelope, pub)
        assert result["valid"] is True
        assert result["action"] == "swap"

    @pytest.mark.asyncio
    async def test_verify_rejects_bad_signature(self):
        from marketplace.intent import sign_intent_legacy, verify_intent_legacy
        priv1, pub1 = self._make_keypair()
        _, pub2 = self._make_keypair()  # Different keypair
        envelope = sign_intent_legacy(
            action="swap", params={},
            private_key_hex=priv1,
            did="did:web:maxiaworld.app:agent:badsig_test",
        )
        with patch("core.auth._nonce_is_used", new_callable=AsyncMock, return_value=False), \
             patch("core.auth._nonce_mark_used", new_callable=AsyncMock):
            result = await verify_intent_legacy(envelope, pub2)
        assert result["valid"] is False

    @pytest.mark.asyncio
    async def test_verify_rejects_expired_intent(self):
        from marketplace.intent import sign_intent_legacy, verify_intent_legacy
        priv, pub = self._make_keypair()
        envelope = sign_intent_legacy(
            action="swap", params={},
            private_key_hex=priv,
            did="did:web:maxiaworld.app:agent:expired_test",
            expires_s=-10,  # Already expired
        )
        result = await verify_intent_legacy(envelope, pub)
        assert result["valid"] is False
        assert "Expired" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_verify_rejects_missing_field(self):
        from marketplace.intent import verify_intent_legacy
        incomplete = {"v": 1, "did": "test", "action": "swap"}
        _, pub = self._make_keypair()
        result = await verify_intent_legacy(incomplete, pub)
        assert result["valid"] is False
        assert "Missing" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_anti_replay_rejects_reused_nonce(self):
        """If nonce was already used, verification should fail (FAIL-CLOSE)."""
        from marketplace.intent import sign_intent_legacy, verify_intent_legacy
        priv, pub = self._make_keypair()
        envelope = sign_intent_legacy(
            action="swap", params={},
            private_key_hex=priv,
            did="did:web:maxiaworld.app:agent:replay_test",
        )
        # Simulate nonce already used (patching on auth module where it's imported from)
        with patch("core.auth._nonce_is_used", new_callable=AsyncMock, return_value=True):
            result = await verify_intent_legacy(envelope, pub)
        assert result["valid"] is False
        assert "replay" in result.get("error", "").lower()

    @pytest.mark.asyncio
    async def test_anti_replay_fails_closed_when_redis_down(self):
        """When anti-replay store is unavailable, DENY (fail-close, S3 fix)."""
        from marketplace.intent import sign_intent_legacy, verify_intent_legacy
        priv, pub = self._make_keypair()
        envelope = sign_intent_legacy(
            action="swap", params={},
            private_key_hex=priv,
            did="did:web:maxiaworld.app:agent:redis_down_test",
        )
        # Simulate Redis unavailable (patching on auth module where it's imported from)
        with patch("core.auth._nonce_is_used", new_callable=AsyncMock, side_effect=Exception("Redis down")):
            result = await verify_intent_legacy(envelope, pub)
        assert result["valid"] is False
        assert "unavailable" in result.get("error", "").lower()


class TestIntentKeyConversion:
    """Test base58/cryptography key conversion helpers."""

    def test_pub_key_roundtrip_base58(self):
        from marketplace.intent import pub_key_to_base58, base58_to_pub_key, generate_aip_keypair, AIP_AVAILABLE
        if not AIP_AVAILABLE:
            pytest.skip("aip-protocol not installed")
        priv, pub = generate_aip_keypair()
        b58 = pub_key_to_base58(pub)
        restored = base58_to_pub_key(b58)
        assert pub.public_bytes_raw() == restored.public_bytes_raw()

    def test_nacl_to_crypto_bridge(self):
        from nacl.signing import SigningKey
        from marketplace.intent import nacl_pub_to_crypto_pub
        sk = SigningKey.generate()
        vk = sk.verify_key
        crypto_pub = nacl_pub_to_crypto_pub(vk)
        assert crypto_pub.public_bytes_raw() == bytes(vk)


class TestIntentDefaultActions:
    """Test the MAXIA_DEFAULT_ACTIONS list."""

    def test_default_actions_include_core_operations(self):
        from marketplace.intent import MAXIA_DEFAULT_ACTIONS
        assert "swap" in MAXIA_DEFAULT_ACTIONS
        assert "gpu_rent" in MAXIA_DEFAULT_ACTIONS
        assert "escrow_lock" in MAXIA_DEFAULT_ACTIONS
        assert "marketplace_execute" in MAXIA_DEFAULT_ACTIONS

    def test_default_actions_not_empty(self):
        from marketplace.intent import MAXIA_DEFAULT_ACTIONS
        assert len(MAXIA_DEFAULT_ACTIONS) >= 5


# ═══════════════════════════════════════════════════════════════════════════════
#  3. PRICE ORACLE MODULE (price_oracle.py) — cache, fallback, structure
# ═══════════════════════════════════════════════════════════════════════════════


class TestPriceOracleFallbackPrices:
    """Test that FALLBACK_PRICES are complete and reasonable."""

    def test_fallback_has_major_crypto(self):
        from trading.price_oracle import FALLBACK_PRICES
        for sym in ["SOL", "BTC", "ETH", "USDC", "USDT"]:
            assert sym in FALLBACK_PRICES, f"Missing {sym} in FALLBACK_PRICES"
            assert FALLBACK_PRICES[sym] > 0

    def test_fallback_has_stablecoins_near_1(self):
        from trading.price_oracle import FALLBACK_PRICES
        assert FALLBACK_PRICES["USDC"] == 1.0
        assert FALLBACK_PRICES["USDT"] == 1.0

    def test_fallback_has_stocks(self):
        from trading.price_oracle import FALLBACK_PRICES
        for sym in ["AAPL", "TSLA", "NVDA", "GOOGL", "MSFT"]:
            assert sym in FALLBACK_PRICES, f"Missing {sym}"
            assert FALLBACK_PRICES[sym] > 10  # Stocks should be > $10

    def test_fallback_btc_reasonable_range(self):
        from trading.price_oracle import FALLBACK_PRICES
        assert FALLBACK_PRICES["BTC"] > 10000  # BTC should be > $10k
        assert FALLBACK_PRICES["BTC"] < 1_000_000  # and < $1M

    def test_fallback_count_at_least_50(self):
        from trading.price_oracle import FALLBACK_PRICES
        assert len(FALLBACK_PRICES) >= 50


class TestPriceOracleCacheTTL:
    """Test cache TTL configuration values."""

    def test_crypto_cache_ttl(self):
        from trading.price_oracle import _CACHE_TTL
        assert _CACHE_TTL == 60  # 1 minute

    def test_stock_cache_ttl(self):
        from trading.price_oracle import _STOCK_CACHE_TTL
        assert _STOCK_CACHE_TTL == 180  # 3 minutes

    def test_symbol_cache_ttl(self):
        from trading.price_oracle import _SYMBOL_CACHE_TTL
        assert _SYMBOL_CACHE_TTL == 45

    def test_symbol_cache_max_size(self):
        from trading.price_oracle import _SYMBOL_CACHE_MAX
        assert _SYMBOL_CACHE_MAX == 200


class TestPriceOracleGetPrices:
    """Test get_prices returns correct structure with mocked external APIs."""

    @pytest.mark.asyncio
    async def test_returns_dict(self):
        from trading.price_oracle import get_prices, _price_cache, _cache_ts
        from trading import price_oracle
        # Force cache miss by resetting cache timestamp
        old_ts = price_oracle._cache_ts
        price_oracle._cache_ts = 0
        # Mock all external fetchers to return empty (fallback only)
        with patch("trading.price_oracle._fetch_helius_prices", new_callable=AsyncMock, return_value={}), \
             patch("trading.price_oracle._get_http", new_callable=AsyncMock):
            result = await get_prices()
        price_oracle._cache_ts = old_ts  # Restore
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_returns_fallback_when_apis_fail(self):
        from trading.price_oracle import get_prices, FALLBACK_PRICES
        from trading import price_oracle
        old_ts = price_oracle._cache_ts
        price_oracle._cache_ts = 0
        # Mock all external sources to fail
        mock_http = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_http.get = AsyncMock(return_value=mock_resp)
        with patch("trading.price_oracle._fetch_helius_prices", new_callable=AsyncMock, return_value={}), \
             patch("trading.price_oracle._get_http", new_callable=AsyncMock, return_value=mock_http):
            result = await get_prices()
        price_oracle._cache_ts = old_ts
        # Should have fallback prices for SOL
        assert "SOL" in result
        assert result["SOL"]["source"] == "fallback"

    @pytest.mark.asyncio
    async def test_returns_subset_when_symbols_specified(self):
        from trading.price_oracle import get_prices, FALLBACK_PRICES
        from trading import price_oracle
        old_ts = price_oracle._cache_ts
        price_oracle._cache_ts = 0
        with patch("trading.price_oracle._fetch_helius_prices", new_callable=AsyncMock, return_value={}), \
             patch("trading.price_oracle._get_http", new_callable=AsyncMock):
            result = await get_prices(symbols=["SOL", "BTC"])
        price_oracle._cache_ts = old_ts
        assert "SOL" in result
        assert "BTC" in result

    @pytest.mark.asyncio
    async def test_uses_cache_when_fresh(self):
        from trading import price_oracle
        # Seed the cache with test data
        old_cache = price_oracle._price_cache
        old_ts = price_oracle._cache_ts
        price_oracle._price_cache = {"SOL": {"price": 999.99, "source": "test"}}
        price_oracle._cache_ts = time.time()  # Fresh
        result = await price_oracle.get_prices()
        assert result["SOL"]["price"] == 999.99
        assert result["SOL"]["source"] == "test"
        # Restore
        price_oracle._price_cache = old_cache
        price_oracle._cache_ts = old_ts

    @pytest.mark.asyncio
    async def test_each_price_entry_has_source(self):
        from trading import price_oracle
        old_ts = price_oracle._cache_ts
        price_oracle._cache_ts = 0
        with patch("trading.price_oracle._fetch_helius_prices", new_callable=AsyncMock, return_value={}), \
             patch("trading.price_oracle._get_http", new_callable=AsyncMock):
            result = await price_oracle.get_prices()
        price_oracle._cache_ts = old_ts
        for sym, data in result.items():
            assert "source" in data, f"{sym} missing 'source' field"
            assert "price" in data, f"{sym} missing 'price' field"


class TestPriceOracleCircuitBreaker:
    """Test the local CircuitBreaker class in price_oracle."""

    def test_initial_state_closed(self):
        from trading.price_oracle import CircuitBreaker
        cb = CircuitBreaker("test_cb", max_failures=3, cooldown_s=60)
        assert cb.is_open is False

    def test_opens_after_max_failures(self):
        from trading.price_oracle import CircuitBreaker
        cb = CircuitBreaker("test_cb_open", max_failures=3, cooldown_s=60)
        for _ in range(3):
            cb.record_failure()
        assert cb.is_open is True

    def test_closes_after_success(self):
        from trading.price_oracle import CircuitBreaker
        cb = CircuitBreaker("test_cb_close", max_failures=3, cooldown_s=60)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        assert cb.is_open is False
        assert cb._failures == 0

    def test_get_status_returns_dict(self):
        from trading.price_oracle import CircuitBreaker
        cb = CircuitBreaker("test_status", max_failures=3, cooldown_s=60)
        status = cb.get_status()
        assert status["name"] == "test_status"
        assert status["state"] == "closed"
        assert status["failures"] == 0
        assert status["max"] == 3

    def test_half_open_after_cooldown(self):
        from trading.price_oracle import CircuitBreaker
        cb = CircuitBreaker("test_halfopen", max_failures=1, cooldown_s=1)
        cb.record_failure()
        assert cb.is_open is True  # Should be open immediately
        # Simulate time passing beyond cooldown by setting _open_until in the past
        cb._open_until = time.time() - 1
        # Now is_open should return False (half-open), allowing a retry
        assert cb.is_open is False


class TestTokenMints:
    """Test TOKEN_MINTS integrity in price_oracle."""

    def test_has_major_tokens(self):
        from trading.price_oracle import TOKEN_MINTS
        for sym in ["SOL", "USDC", "BTC", "ETH", "BONK"]:
            assert sym in TOKEN_MINTS

    def test_sol_mint_is_correct(self):
        from trading.price_oracle import TOKEN_MINTS
        assert TOKEN_MINTS["SOL"] == "So11111111111111111111111111111111111111112"

    def test_usdc_mint_is_correct(self):
        from trading.price_oracle import TOKEN_MINTS
        assert TOKEN_MINTS["USDC"] == "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


# ═══════════════════════════════════════════════════════════════════════════════
#  4. CRYPTO SWAP MODULE (crypto_swap.py) — fee calculation, tiers
# ═══════════════════════════════════════════════════════════════════════════════


class TestSwapCommissionTiersBPS:
    """Test get_swap_commission_bps for all tiers including first-swap-free."""

    def test_first_swap_free(self):
        from trading.crypto_swap import get_swap_commission_bps
        assert get_swap_commission_bps(100, volume_30d=0, swap_count=0) == 0

    def test_bronze_default(self):
        from trading.crypto_swap import get_swap_commission_bps
        # volume_30d=0 with existing swaps -> BRONZE
        assert get_swap_commission_bps(50, volume_30d=0) == 10

    def test_bronze_upper_boundary(self):
        from trading.crypto_swap import get_swap_commission_bps
        assert get_swap_commission_bps(100, volume_30d=999) == 10

    def test_silver_lower_boundary(self):
        from trading.crypto_swap import get_swap_commission_bps
        assert get_swap_commission_bps(100, volume_30d=1000) == 5

    def test_silver_upper_boundary(self):
        from trading.crypto_swap import get_swap_commission_bps
        assert get_swap_commission_bps(100, volume_30d=4999) == 5

    def test_gold_lower_boundary(self):
        from trading.crypto_swap import get_swap_commission_bps
        assert get_swap_commission_bps(100, volume_30d=5000) == 3

    def test_gold_upper_boundary(self):
        from trading.crypto_swap import get_swap_commission_bps
        assert get_swap_commission_bps(100, volume_30d=24999) == 3

    def test_whale_lower_boundary(self):
        from trading.crypto_swap import get_swap_commission_bps
        assert get_swap_commission_bps(100, volume_30d=25000) == 1

    def test_whale_large_volume(self):
        from trading.crypto_swap import get_swap_commission_bps
        assert get_swap_commission_bps(100, volume_30d=1_000_000) == 1


class TestSwapTierNames:
    """Test get_swap_tier_name returns correct tier names."""

    def test_free_tier_name(self):
        from trading.crypto_swap import get_swap_tier_name
        assert get_swap_tier_name(100, volume_30d=0, swap_count=0) == "FREE"

    def test_bronze_tier_name(self):
        from trading.crypto_swap import get_swap_tier_name
        assert get_swap_tier_name(100, volume_30d=0) == "BRONZE"

    def test_silver_tier_name(self):
        from trading.crypto_swap import get_swap_tier_name
        assert get_swap_tier_name(100, volume_30d=2000) == "SILVER"

    def test_gold_tier_name(self):
        from trading.crypto_swap import get_swap_tier_name
        assert get_swap_tier_name(100, volume_30d=10000) == "GOLD"

    def test_whale_tier_name(self):
        from trading.crypto_swap import get_swap_tier_name
        assert get_swap_tier_name(100, volume_30d=50000) == "WHALE"


class TestSwapTiersConfig:
    """Test SWAP_COMMISSION_TIERS dict structure and monotonicity."""

    def test_four_tiers_defined(self):
        from trading.crypto_swap import SWAP_COMMISSION_TIERS
        assert set(SWAP_COMMISSION_TIERS.keys()) == {"BRONZE", "SILVER", "GOLD", "WHALE"}

    def test_each_tier_has_required_fields(self):
        from trading.crypto_swap import SWAP_COMMISSION_TIERS
        for name, tier in SWAP_COMMISSION_TIERS.items():
            assert "min_amount" in tier, f"{name} missing min_amount"
            assert "max_amount" in tier, f"{name} missing max_amount"
            assert "bps" in tier, f"{name} missing bps"

    def test_bps_monotonically_decrease(self):
        from trading.crypto_swap import get_swap_commission_bps
        volumes = [0, 500, 1000, 5000, 25000, 100_000]
        bps = [get_swap_commission_bps(100, volume_30d=v) for v in volumes]
        for i in range(1, len(bps)):
            assert bps[i] <= bps[i - 1], f"BPS not decreasing: {bps}"

    def test_no_gaps_in_tier_ranges(self):
        """Tier ranges should be contiguous with no gaps."""
        from trading.crypto_swap import SWAP_COMMISSION_TIERS
        tiers = sorted(SWAP_COMMISSION_TIERS.values(), key=lambda t: t["min_amount"])
        for i in range(1, len(tiers)):
            assert tiers[i]["min_amount"] == tiers[i - 1]["max_amount"], \
                f"Gap between tier ending at {tiers[i-1]['max_amount']} and starting at {tiers[i]['min_amount']}"


class TestSwapQuoteValidation:
    """Test get_swap_quote input validation (no network calls)."""

    @pytest.mark.asyncio
    async def test_unknown_from_token(self):
        from trading.crypto_swap import get_swap_quote
        result = await get_swap_quote("FAKECOIN", "SOL", 100)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_unknown_to_token(self):
        from trading.crypto_swap import get_swap_quote
        result = await get_swap_quote("SOL", "NONEXISTENT", 100)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_same_token_pair(self):
        from trading.crypto_swap import get_swap_quote
        result = await get_swap_quote("SOL", "SOL", 100)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_negative_amount(self):
        from trading.crypto_swap import get_swap_quote
        result = await get_swap_quote("SOL", "USDC", -10)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_zero_amount(self):
        from trading.crypto_swap import get_swap_quote
        result = await get_swap_quote("SOL", "USDC", 0)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_case_insensitive_tokens(self):
        """Token symbols should be uppercased internally."""
        from trading.crypto_swap import get_swap_quote
        result = await get_swap_quote("sol", "sol", 100)
        assert "error" in result  # Same token error (both uppercased to SOL)

    def test_safety_caps_defined(self):
        from trading.crypto_swap import MAX_SWAP_AMOUNT_USD, MIN_SWAP_AMOUNT_USD
        assert MAX_SWAP_AMOUNT_USD == 10000
        assert MIN_SWAP_AMOUNT_USD == 0.01


class TestSupportedTokensIntegrity:
    """Test SUPPORTED_TOKENS catalog."""

    def test_at_least_50_tokens(self):
        from trading.crypto_swap import SUPPORTED_TOKENS
        assert len(SUPPORTED_TOKENS) >= 50

    def test_each_token_has_mint_name_decimals(self):
        from trading.crypto_swap import SUPPORTED_TOKENS
        for sym, info in SUPPORTED_TOKENS.items():
            assert "mint" in info, f"{sym} missing mint"
            assert "name" in info, f"{sym} missing name"
            assert "decimals" in info, f"{sym} missing decimals"
            assert isinstance(info["decimals"], int)

    def test_sol_mint_correct(self):
        from trading.crypto_swap import SUPPORTED_TOKENS
        assert SUPPORTED_TOKENS["SOL"]["mint"] == "So11111111111111111111111111111111111111112"

    def test_usdc_mint_correct(self):
        from trading.crypto_swap import SUPPORTED_TOKENS
        assert SUPPORTED_TOKENS["USDC"]["mint"] == "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


# ═══════════════════════════════════════════════════════════════════════════════
#  5. DATABASE MODULE (database.py) — init, CRUD, migrations
# ═══════════════════════════════════════════════════════════════════════════════


class TestDatabaseSchema:
    """Test schema constants and whitelists."""

    def test_allowed_agent_columns_is_frozenset(self):
        from core.database import ALLOWED_AGENT_COLUMNS
        assert isinstance(ALLOWED_AGENT_COLUMNS, frozenset)

    def test_allowed_service_columns_is_frozenset(self):
        from core.database import ALLOWED_SERVICE_COLUMNS
        assert isinstance(ALLOWED_SERVICE_COLUMNS, frozenset)

    def test_agent_columns_include_expected(self):
        from core.database import ALLOWED_AGENT_COLUMNS
        for col in ["name", "wallet", "description", "tier", "volume_30d"]:
            assert col in ALLOWED_AGENT_COLUMNS

    def test_agent_columns_exclude_dangerous(self):
        from core.database import ALLOWED_AGENT_COLUMNS
        for col in ["password", "admin", "DROP", "privkey"]:
            assert col not in ALLOWED_AGENT_COLUMNS

    def test_db_schema_creates_agents_table(self):
        from core.database import DB_SCHEMA
        assert "CREATE TABLE IF NOT EXISTS agents" in DB_SCHEMA

    def test_db_schema_creates_transactions_table(self):
        from core.database import DB_SCHEMA
        assert "CREATE TABLE IF NOT EXISTS transactions" in DB_SCHEMA

    def test_db_schema_creates_crypto_swaps_table(self):
        from core.database import DB_SCHEMA
        assert "CREATE TABLE IF NOT EXISTS crypto_swaps" in DB_SCHEMA

    def test_db_schema_creates_indexes(self):
        from core.database import DB_SCHEMA
        assert "CREATE INDEX IF NOT EXISTS" in DB_SCHEMA


class TestDatabaseInitAndCRUD:
    """Test Database connect, save_agent, get_agent using in-memory SQLite."""

    @pytest.fixture
    async def test_db(self, tmp_path):
        """Create a fresh Database instance with a temporary SQLite file."""
        from core.database import Database, DB_SCHEMA
        db = Database()
        db._pg = None
        import aiosqlite
        db_path = str(tmp_path / "test.db")
        db._db = await aiosqlite.connect(db_path)
        db._db.row_factory = aiosqlite.Row
        await db._db.execute("PRAGMA journal_mode=WAL")
        await db._db.execute("PRAGMA foreign_keys=ON")
        await db._db.executescript(DB_SCHEMA)
        yield db
        await db._db.close()

    @pytest.mark.asyncio
    async def test_save_and_get_agent(self, test_db):
        agent = {
            "api_key": "test-key-12345678",
            "name": "TestAgent",
            "wallet": "7v91N7iZ9mNicL8WfG6cgSCKyRXydQjLh6UYBWwm6y1Q",
            "description": "A test agent",
            "tier": "BRONZE",
        }
        await test_db.save_agent(agent)
        result = await test_db.get_agent("test-key-12345678")
        assert result is not None
        assert result["name"] == "TestAgent"
        assert result["wallet"] == "7v91N7iZ9mNicL8WfG6cgSCKyRXydQjLh6UYBWwm6y1Q"
        assert result["tier"] == "BRONZE"

    @pytest.mark.asyncio
    async def test_get_nonexistent_agent_returns_none(self, test_db):
        result = await test_db.get_agent("nonexistent-key")
        assert result is None

    @pytest.mark.asyncio
    async def test_count_agents_initially_zero(self, test_db):
        count = await test_db.count_agents()
        assert count == 0

    @pytest.mark.asyncio
    async def test_count_agents_after_insert(self, test_db):
        agent = {
            "api_key": "count-key-001",
            "name": "Agent1",
            "wallet": "7v91N7iZ9mNicL8WfG6cgSCKyRXydQjLh6UYBWwm6y1Q",
        }
        await test_db.save_agent(agent)
        count = await test_db.count_agents()
        assert count == 1

    @pytest.mark.asyncio
    async def test_save_agent_upsert(self, test_db):
        """save_agent with same api_key should update (INSERT OR REPLACE)."""
        agent_v1 = {
            "api_key": "upsert-key-001",
            "name": "OriginalName",
            "wallet": "7v91N7iZ9mNicL8WfG6cgSCKyRXydQjLh6UYBWwm6y1Q",
        }
        await test_db.save_agent(agent_v1)
        agent_v2 = {
            "api_key": "upsert-key-001",
            "name": "UpdatedName",
            "wallet": "7v91N7iZ9mNicL8WfG6cgSCKyRXydQjLh6UYBWwm6y1Q",
        }
        await test_db.save_agent(agent_v2)
        result = await test_db.get_agent("upsert-key-001")
        assert result["name"] == "UpdatedName"
        count = await test_db.count_agents()
        assert count == 1  # Still only 1 agent

    @pytest.mark.asyncio
    async def test_get_all_agents(self, test_db):
        for i in range(3):
            await test_db.save_agent({
                "api_key": f"all-key-{i:03d}",
                "name": f"Agent{i}",
                "wallet": f"7v91N7iZ9mNicL8WfG6cgSCKyRXydQjLh6UYBWwm6y{i}Q",
            })
        all_agents = await test_db.get_all_agents()
        assert len(all_agents) == 3

    @pytest.mark.asyncio
    async def test_update_agent_allowed_columns(self, test_db):
        agent = {
            "api_key": "update-key-001",
            "name": "BeforeUpdate",
            "wallet": "7v91N7iZ9mNicL8WfG6cgSCKyRXydQjLh6UYBWwm6y1Q",
            "tier": "BRONZE",
        }
        await test_db.save_agent(agent)
        await test_db.update_agent("update-key-001", {"name": "AfterUpdate", "tier": "GOLD"})
        result = await test_db.get_agent("update-key-001")
        assert result["name"] == "AfterUpdate"
        assert result["tier"] == "GOLD"

    @pytest.mark.asyncio
    async def test_update_agent_ignores_dangerous_columns(self, test_db):
        """Columns not in ALLOWED_AGENT_COLUMNS should be silently ignored."""
        agent = {
            "api_key": "safe-update-key",
            "name": "SafeAgent",
            "wallet": "7v91N7iZ9mNicL8WfG6cgSCKyRXydQjLh6UYBWwm6y1Q",
        }
        await test_db.save_agent(agent)
        # Try to inject a dangerous column — should be filtered out
        await test_db.update_agent("safe-update-key", {"password": "hacked", "name": "StillSafe"})
        result = await test_db.get_agent("safe-update-key")
        assert result["name"] == "StillSafe"
        # 'password' is not a column in the agents table, so it should not appear

    @pytest.mark.asyncio
    async def test_raw_execute_fetchall(self, test_db):
        rows = await test_db.raw_execute_fetchall("SELECT COUNT(*) as cnt FROM agents")
        assert len(rows) == 1
        assert rows[0]["cnt"] == 0


class TestDatabaseMigrations:
    """Test migration system structure."""

    def test_migrations_dict_exists(self):
        from core.database import Database
        assert hasattr(Database, "MIGRATIONS")
        assert isinstance(Database.MIGRATIONS, dict)

    def test_migrations_have_sequential_keys(self):
        from core.database import Database
        keys = sorted(Database.MIGRATIONS.keys())
        assert keys[0] == 1  # Starts at 1
        assert len(keys) >= 4  # At least 4 migrations

    def test_each_migration_has_description_and_sql(self):
        from core.database import Database
        for version, (desc, sql) in Database.MIGRATIONS.items():
            assert isinstance(desc, str), f"Migration {version} description not a string"
            assert isinstance(sql, str), f"Migration {version} SQL not a string"
            assert len(desc) > 5, f"Migration {version} description too short"

    def test_migration_2_creates_agent_permissions(self):
        from core.database import Database
        _, sql = Database.MIGRATIONS[2]
        assert "agent_permissions" in sql

    def test_migration_3_adds_did_column(self):
        from core.database import Database
        _, sql = Database.MIGRATIONS[3]
        assert "did" in sql

    def test_migration_4_adds_public_key(self):
        from core.database import Database
        _, sql = Database.MIGRATIONS[4]
        assert "public_key" in sql


class TestDatabasePgParams:
    """Test the _pg_params and _pg_convert helper methods."""

    def test_pg_params_noop_for_sqlite(self):
        from core.database import Database
        db = Database()
        db._pg = None  # SQLite mode
        sql, params = db._pg_params("SELECT * FROM agents WHERE api_key=?", ("key1",))
        assert sql == "SELECT * FROM agents WHERE api_key=?"
        assert params == ("key1",)

    def test_pg_convert_noop_for_sqlite(self):
        from core.database import Database
        db = Database()
        db._pg = None
        sql = db._pg_convert("SELECT strftime('%s','now')")
        # Should return unchanged (no PG mode)
        assert sql == "SELECT strftime('%s','now')"
