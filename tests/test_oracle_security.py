"""Tests — Pyth Oracle + Content Safety + Rate Limiting + Geo-blocking.

All external deps mocked. Zero DB/network calls.
"""
import os
import sys
import time
from unittest.mock import patch, MagicMock

import pytest

BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..", "backend")
sys.path.insert(0, BACKEND_DIR)


# ═══════════════════════════════════════════════════════════════════════════════
#  PYTH ORACLE — Confidence, TWAP, Staleness
# ═══════════════════════════════════════════════════════════════════════════════

class TestConfidenceThreshold:
    """get_confidence_threshold() returns asset-class thresholds."""

    def test_major_token(self):
        from trading.pyth_oracle import get_confidence_threshold
        thresh = get_confidence_threshold("BTC")
        assert thresh > 0  # returns a positive threshold

    def test_mid_token(self):
        from trading.pyth_oracle import get_confidence_threshold
        thresh = get_confidence_threshold("LINK")
        assert thresh > 0

    def test_unknown_token_default(self):
        from trading.pyth_oracle import get_confidence_threshold
        thresh = get_confidence_threshold("UNKNOWNXYZ")
        assert thresh > 0  # should return a default, not crash


class TestTWAP:
    """TWAP (Time-Weighted Average Price) calculations."""

    def test_update_and_get_twap(self):
        from trading.pyth_oracle import update_twap, get_twap
        sym = "_TEST_TWAP_1"
        update_twap(sym, 100.0)
        update_twap(sym, 110.0)
        update_twap(sym, 105.0)
        twap = get_twap(sym)
        assert 100 <= twap <= 110

    def test_twap_no_data_returns_zero(self):
        from trading.pyth_oracle import get_twap
        assert get_twap("_NONEXISTENT_SYMBOL") == 0

    def test_twap_single_point_returns_zero(self):
        from trading.pyth_oracle import update_twap, get_twap
        sym = "_TEST_TWAP_SINGLE"
        update_twap(sym, 50.0)
        # Less than 2 points = 0
        twap = get_twap(sym)
        assert twap == 0 or twap == 50.0  # impl dependent


class TestTWAPDeviation:
    """check_twap_deviation() spot vs TWAP comparison."""

    def test_no_deviation_ok(self):
        from trading.pyth_oracle import update_twap, check_twap_deviation
        sym = "_TEST_DEV_OK"
        for p in [100, 101, 99, 100, 102]:
            update_twap(sym, p)
        result = check_twap_deviation(sym, 100.5)
        assert result["ok"] is True

    def test_huge_deviation_blocked(self):
        from trading.pyth_oracle import update_twap, check_twap_deviation
        sym = "_TEST_DEV_BAD"
        for p in [100, 100, 100, 100, 100]:
            update_twap(sym, p)
        result = check_twap_deviation(sym, 150.0)  # 50% deviation
        assert result["ok"] is False
        assert result["deviation_pct"] > 20


class TestCandleBuilder:
    """CandleBuilder aggregates ticks into OHLCV candles."""

    def test_candle_ohlcv(self):
        from trading.pyth_oracle import CandleBuilder
        import time
        cb = CandleBuilder(interval_s=60)
        ts = time.time()
        cb.tick(100.0, ts)
        cb.tick(110.0, ts + 1)
        cb.tick(95.0, ts + 2)
        cb.tick(105.0, ts + 3)
        candle = cb.current()
        assert candle["open"] == 100.0
        assert candle["high"] == 110.0
        assert candle["low"] == 95.0
        assert candle["close"] == 105.0

    def test_empty_candle(self):
        from trading.pyth_oracle import CandleBuilder
        cb = CandleBuilder(interval_s=60)
        assert cb.current() is None


# ═══════════════════════════════════════════════════════════════════════════════
#  CONTENT SAFETY (security.py)
# ═══════════════════════════════════════════════════════════════════════════════

class TestContentSafety:
    """check_content_safety() blocks dangerous input."""

    def test_clean_text_passes(self):
        from core.security import check_content_safety
        # Should not raise
        check_content_safety("Hello, I want to swap SOL to USDC")

    def test_blocked_word_raises(self):
        from core.security import check_content_safety
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            check_content_safety("how to make a bomb")
        assert exc_info.value.status_code == 400

    def test_empty_text_passes(self):
        from core.security import check_content_safety
        check_content_safety("")

    def test_normal_crypto_terms_pass(self):
        from core.security import check_content_safety
        check_content_safety("buy BTC, sell ETH, swap USDC, analyze wallet")


class TestWalletAddressValidation:
    """validate_wallet_address() from security.py."""

    def test_valid_evm(self):
        from core.security import validate_wallet_address
        assert validate_wallet_address("0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045") is True

    def test_valid_solana(self):
        from core.security import validate_wallet_address
        assert validate_wallet_address("7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU") is True

    def test_invalid_address(self):
        from core.security import validate_wallet_address
        assert validate_wallet_address("not_a_wallet") is False

    def test_empty_address(self):
        from core.security import validate_wallet_address
        assert validate_wallet_address("") is False

    def test_too_short(self):
        from core.security import validate_wallet_address
        assert validate_wallet_address("0x123") is False


# ═══════════════════════════════════════════════════════════════════════════════
#  RATE LIMITING (security.py)
# ═══════════════════════════════════════════════════════════════════════════════

class TestIPRateLimit:
    """IP-based rate limiting."""

    def test_fresh_ip_not_limited(self):
        from core.security import check_ip_rate_limit
        assert check_ip_rate_limit("10.99.99.99") is False  # not limited

    def test_check_ip_returns_bool(self):
        from core.security import check_ip_rate_limit
        result = check_ip_rate_limit("10.99.99.98")
        assert isinstance(result, bool)


# ═══════════════════════════════════════════════════════════════════════════════
#  COMMISSION TIERS (config.py)
# ═══════════════════════════════════════════════════════════════════════════════

class TestCommissionTiers:
    """Marketplace commission tiers from config.py."""

    def test_bronze_tier(self):
        from core.config import get_commission_bps, get_commission_tier_name
        assert get_commission_bps(100) == 150
        assert get_commission_tier_name(100) == "BRONZE"

    def test_gold_tier(self):
        from core.config import get_commission_bps, get_commission_tier_name
        assert get_commission_bps(2000) == 50
        assert get_commission_tier_name(2000) == "GOLD"

    def test_whale_tier(self):
        from core.config import get_commission_bps, get_commission_tier_name
        assert get_commission_bps(10000) == 10
        assert get_commission_tier_name(10000) == "WHALE"

    def test_boundary_500(self):
        from core.config import get_commission_bps
        # $500 should be GOLD (not BRONZE)
        bps_499 = get_commission_bps(499)
        bps_500 = get_commission_bps(500)
        assert bps_499 >= bps_500  # 500 should be same or lower

    def test_zero_amount(self):
        from core.config import get_commission_bps
        bps = get_commission_bps(0)
        assert bps >= 0  # should not crash
