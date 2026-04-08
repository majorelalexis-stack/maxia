"""Tests — Chat Handler intent detection + Wallet Risk scoring.

All external deps mocked. Zero DB/network calls.
"""
import os
import sys
import time
from dataclasses import replace
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..", "backend")
sys.path.insert(0, BACKEND_DIR)


# ═══════════════════════════════════════════════════════════════════════════════
#  CHAT HANDLER — Intent Detection
# ═══════════════════════════════════════════════════════════════════════════════

class TestParsedIntent:
    """ParsedIntent frozen dataclass behavior."""

    def test_frozen_dataclass_immutable(self):
        from features.chat_handler import ParsedIntent
        intent = ParsedIntent(intent="price", symbol="BTC")
        with pytest.raises(AttributeError):
            intent.symbol = "ETH"

    def test_replace_on_frozen(self):
        from features.chat_handler import ParsedIntent
        intent = ParsedIntent(intent="swap", symbol="SOL")
        new_intent = replace(intent, wallet="ABC123")
        assert new_intent.wallet == "ABC123"
        assert new_intent.symbol == "SOL"
        assert intent.wallet is None  # original unchanged

    def test_default_values(self):
        from features.chat_handler import ParsedIntent
        intent = ParsedIntent(intent="help")
        assert intent.symbol is None
        assert intent.amount is None
        assert intent.wallet is None
        assert intent.raw_message == ""


class TestDetectIntent:
    """_detect_intent() parses user messages into structured intents."""

    def test_price_query_btc(self):
        from features.chat_handler import _detect_intent
        intent = _detect_intent("price BTC")
        assert intent.intent == "price"
        assert intent.symbol == "BTC"

    def test_price_query_sol(self):
        from features.chat_handler import _detect_intent
        intent = _detect_intent("what is the price of SOL")
        assert intent.intent == "price"
        assert intent.symbol == "SOL"

    def test_swap_intent(self):
        from features.chat_handler import _detect_intent
        intent = _detect_intent("swap 10 USDC to SOL")
        assert intent.intent == "swap"

    def test_help_intent(self):
        from features.chat_handler import _detect_intent
        intent = _detect_intent("help")
        assert intent.intent == "help"

    def test_empty_message(self):
        from features.chat_handler import _detect_intent
        intent = _detect_intent("")
        assert intent.intent in ("unknown", "help", "chat", "llm")

    def test_raw_message_preserved(self):
        from features.chat_handler import _detect_intent
        msg = "analyze wallet ABC123"
        intent = _detect_intent(msg)
        assert intent.raw_message == msg

    def test_portfolio_intent(self):
        from features.chat_handler import _detect_intent
        intent = _detect_intent("show my portfolio")
        assert intent.intent == "portfolio"

    def test_sentiment_falls_to_llm(self):
        from features.chat_handler import _detect_intent
        intent = _detect_intent("sentiment ETH")
        # "sentiment" is not a recognized keyword — falls to LLM
        assert intent.intent == "llm"

    def test_risk_intent(self):
        from features.chat_handler import _detect_intent
        intent = _detect_intent("risk score for wallet")
        assert intent.intent == "risk"


class TestChatRateLimit:
    """Chat handler rate limiting."""

    def test_rate_limit_allows_normal(self):
        from features.chat_handler import _check_rate_limit
        # Fresh IP should be allowed
        assert _check_rate_limit("192.168.99.99") is True

    def test_rate_limit_blocks_flood(self):
        from features.chat_handler import _check_rate_limit
        ip = "192.168.99.100"
        for _ in range(15):
            _check_rate_limit(ip)
        assert _check_rate_limit(ip) is False


# ═══════════════════════════════════════════════════════════════════════════════
#  WALLET RISK — Scoring
# ═══════════════════════════════════════════════════════════════════════════════

class TestWalletValidation:
    """Wallet address format validation."""

    def test_valid_evm_address(self):
        from features.wallet_risk import _validate_address
        assert _validate_address("0x1234567890abcdef1234567890abcdef12345678", "ethereum") is True

    def test_invalid_evm_short(self):
        from features.wallet_risk import _validate_address
        assert _validate_address("0x1234", "ethereum") is False

    def test_valid_solana_address(self):
        from features.wallet_risk import _validate_address
        assert _validate_address("7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU", "solana") is True

    def test_invalid_empty(self):
        from features.wallet_risk import _validate_address
        assert _validate_address("", "solana") is False

    def test_auto_detect_evm(self):
        from features.wallet_risk import _validate_address
        assert _validate_address("0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045", "auto") is True


class TestReputationScore:
    """_score_reputation() scoring logic."""

    def test_zero_tx_high_risk(self):
        from features.wallet_risk import _score_reputation
        score, reasons = _score_reputation(tx_count=0, balance=0)
        assert score >= 50  # high risk for zero activity

    def test_many_tx_lower_risk(self):
        from features.wallet_risk import _score_reputation
        score, reasons = _score_reputation(tx_count=500, balance=10.0)
        assert score < 50

    def test_returns_reasons(self):
        from features.wallet_risk import _score_reputation
        _, reasons = _score_reputation(tx_count=0, balance=0)
        assert isinstance(reasons, list)
        assert len(reasons) > 0


class TestFraudScore:
    """_score_fraud() scoring logic."""

    def test_normal_address_low_fraud(self):
        from features.wallet_risk import _score_fraud
        score, reasons = _score_fraud("0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045", "ethereum", tx_count=100)
        assert score < 50

    def test_returns_list_reasons(self):
        from features.wallet_risk import _score_fraud
        _, reasons = _score_fraud("0x0000000000000000000000000000000000000000", "ethereum", tx_count=0)
        assert isinstance(reasons, list)


class TestFinancialScore:
    """_score_financial() scoring logic."""

    def test_zero_balance_poor(self):
        from features.wallet_risk import _score_financial
        score, reasons = _score_financial(balance=0, tx_count=0, chain="solana", oldest_slot=0, newest_slot=0)
        assert score >= 40  # poor financial health = high risk

    def test_good_balance(self):
        from features.wallet_risk import _score_financial
        score, reasons = _score_financial(balance=100.0, tx_count=200, chain="solana", oldest_slot=1000, newest_slot=5000)
        assert score < 50


class TestCompositeScore:
    """_composite() weighted scoring."""

    def test_all_low_risk(self):
        from features.wallet_risk import _composite
        score, level = _composite(rep=10, fraud=10, fin=10)
        assert score <= 20
        assert level == "LOW"

    def test_all_high_risk(self):
        from features.wallet_risk import _composite
        score, level = _composite(rep=90, fraud=90, fin=90)
        assert score >= 80
        assert level in ("HIGH", "CRITICAL")

    def test_scores_are_weighted(self):
        from features.wallet_risk import _composite
        score_high_fraud, _ = _composite(rep=10, fraud=90, fin=10)
        score_low_all, _ = _composite(rep=10, fraud=10, fin=10)
        assert score_high_fraud > score_low_all

    def test_medium_level(self):
        from features.wallet_risk import _composite
        score, level = _composite(rep=50, fraud=50, fin=50)
        assert level in ("MEDIUM", "HIGH")
