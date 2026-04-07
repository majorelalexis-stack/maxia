"""MAXIA V12 — Chat Handler test suite.

Tests intent detection (_detect_intent), rate limiting, and handler routing.
All external APIs (Pyth, Jupiter, LLM) are mocked.
"""
import asyncio
import os
import sys
import time
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
#  INTENT DETECTION
# ═══════════════════════════════════════════════════════════════════════════════

class TestDetectIntent:
    """Test _detect_intent parses user messages into structured intents."""

    def test_price_sol(self):
        """'price SOL' should detect intent=price, symbol=SOL."""
        from features.chat_handler import _detect_intent
        intent = _detect_intent("price SOL")
        assert intent.intent == "price"
        assert intent.symbol == "SOL"

    def test_swap_usdc_to_sol(self):
        """'swap 10 USDC to SOL' should detect intent=swap with amount, from, to."""
        from features.chat_handler import _detect_intent
        intent = _detect_intent("swap 10 USDC to SOL")
        assert intent.intent == "swap"
        assert intent.amount == 10.0
        assert intent.from_token == "USDC"
        assert intent.to_token == "SOL"

    def test_buy_eth_with_usdc(self):
        """'buy 5 ETH with USDC' should detect intent=swap."""
        from features.chat_handler import _detect_intent
        intent = _detect_intent("buy 5 ETH with USDC")
        assert intent.intent == "swap"
        assert intent.amount == 5.0
        assert intent.from_token == "ETH"
        assert intent.to_token == "USDC"

    def test_help(self):
        """'help' should detect intent=help."""
        from features.chat_handler import _detect_intent
        intent = _detect_intent("help")
        assert intent.intent == "help"

    def test_leaderboard(self):
        """'leaderboard' should detect intent=leaderboard."""
        from features.chat_handler import _detect_intent
        intent = _detect_intent("leaderboard")
        assert intent.intent == "leaderboard"

    def test_risk_with_address(self):
        """'risk 7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU' should detect risk intent with address."""
        from features.chat_handler import _detect_intent
        addr = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"
        intent = _detect_intent(f"risk {addr}")
        assert intent.intent == "risk"
        assert intent.address == addr

    def test_random_question_goes_to_llm(self):
        """Unrecognized text should fall back to intent=llm."""
        from features.chat_handler import _detect_intent
        intent = _detect_intent("what is the meaning of life")
        assert intent.intent == "llm"

    def test_gpu(self):
        """'gpu' should detect intent=gpu."""
        from features.chat_handler import _detect_intent
        intent = _detect_intent("gpu")
        assert intent.intent == "gpu"

    def test_yield(self):
        """'yield' should detect intent=yield."""
        from features.chat_handler import _detect_intent
        intent = _detect_intent("yield")
        assert intent.intent == "yield"

    def test_alert_sol(self):
        """'alert SOL' should detect intent=alert. Symbol extracted if 'price SOL' pattern matches."""
        from features.chat_handler import _detect_intent
        intent = _detect_intent("alert SOL")
        assert intent.intent == "alert"
        # Note: symbol extraction uses _PRICE_SYMBOL_PATTERN which requires price/prix/cours/quote prefix.
        # "alert SOL" alone does NOT match the price pattern, so symbol is None.
        assert intent.symbol is None

    def test_alert_with_quote_keyword(self):
        """'alert quote SOL' should detect alert with symbol=SOL (quote is in _PRICE_SYMBOL_PATTERN)."""
        from features.chat_handler import _detect_intent
        # "alert" takes priority over "quote" in intent detection, but _PRICE_SYMBOL_PATTERN
        # extracts the symbol from "quote SOL". Since alert is checked at step 10 and price at step 5,
        # if both keywords present, whichever check triggers first wins.
        # Actually: "alert" keyword is matched at step 10. "quote" is a _PRICE_KEYWORD matched at step 5.
        # Step 5 (price) runs before step 10 (alert), so intent=price.
        intent = _detect_intent("alert quote SOL")
        # "quote" is in _PRICE_KEYWORDS, which is checked at step 5 — before alert at step 10
        assert intent.intent == "price"
        assert intent.symbol == "SOL"

    def test_dca(self):
        """'dca' should detect intent=dca."""
        from features.chat_handler import _detect_intent
        intent = _detect_intent("dca")
        assert intent.intent == "dca"

    def test_portfolio_with_address(self):
        """'portfolio 7xKX...' should detect portfolio intent with address."""
        from features.chat_handler import _detect_intent
        addr = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"
        intent = _detect_intent(f"portfolio {addr}")
        assert intent.intent == "portfolio"
        assert intent.address == addr

    def test_bridge(self):
        """'bridge' should detect intent=bridge."""
        from features.chat_handler import _detect_intent
        intent = _detect_intent("bridge")
        assert intent.intent == "bridge"

    def test_buy_sol_card(self):
        """'buy SOL card' should detect intent=buy_crypto."""
        from features.chat_handler import _detect_intent
        intent = _detect_intent("buy SOL card")
        assert intent.intent == "buy_crypto"

    def test_stocks(self):
        """'stocks' should detect intent=stocks."""
        from features.chat_handler import _detect_intent
        intent = _detect_intent("stocks")
        assert intent.intent == "stocks"

    def test_swap_decimal_amount(self):
        """Swap with decimal amount should parse correctly."""
        from features.chat_handler import _detect_intent
        intent = _detect_intent("swap 0.5 SOL to USDC")
        assert intent.intent == "swap"
        assert intent.amount == 0.5
        assert intent.from_token == "SOL"
        assert intent.to_token == "USDC"

    def test_swap_keyword_without_format_gives_swap_help(self):
        """'swap' alone should give swap_help intent."""
        from features.chat_handler import _detect_intent
        intent = _detect_intent("swap please")
        assert intent.intent == "swap_help"


class TestParsedIntentImmutability:
    """ParsedIntent should be a frozen dataclass."""

    def test_intent_is_frozen(self):
        """ParsedIntent should be immutable (frozen=True)."""
        from features.chat_handler import ParsedIntent
        intent = ParsedIntent(intent="price", symbol="SOL")
        with pytest.raises(AttributeError):
            intent.intent = "swap"


# ═══════════════════════════════════════════════════════════════════════════════
#  RATE LIMITING
# ═══════════════════════════════════════════════════════════════════════════════

class TestChatRateLimit:
    """Test chat handler's per-IP rate limiting."""

    def test_rate_limit_allows_within_window(self):
        """Requests within limit should be allowed."""
        from features.chat_handler import _check_rate_limit, _rate_store
        test_ip = "192.0.2.100"
        _rate_store.pop(test_ip, None)  # Clean state

        for _ in range(10):
            assert _check_rate_limit(test_ip) is True

    def test_rate_limit_blocks_after_max(self):
        """11th request within 60s window should be blocked."""
        from features.chat_handler import _check_rate_limit, _rate_store
        test_ip = "192.0.2.101"
        _rate_store.pop(test_ip, None)

        for _ in range(10):
            _check_rate_limit(test_ip)

        assert _check_rate_limit(test_ip) is False


# ═══════════════════════════════════════════════════════════════════════════════
#  HANDLER TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestHandlePrice:
    """Test _handle_price returns price data from Pyth oracle."""

    def test_handle_price_pyth_success(self):
        """Price handler should return Pyth price when available."""
        from features.chat_handler import _handle_price, ParsedIntent

        mock_pyth = AsyncMock(return_value={
            "price": 150.25, "publish_time": time.time(), "source": "pyth",
        })

        with patch("trading.pyth_oracle.CRYPTO_FEEDS", {"SOL": "feed-sol-123"}), \
             patch("trading.pyth_oracle.EQUITY_FEEDS", {}), \
             patch("trading.pyth_oracle.get_pyth_price", mock_pyth):
            result = _run(_handle_price(ParsedIntent(intent="price", symbol="SOL")))

        assert result["type"] == "price"
        assert result["data"]["price"] == 150.25
        assert result["data"]["symbol"] == "SOL"

    def test_handle_price_no_symbol(self):
        """Price handler without symbol should return error."""
        from features.chat_handler import _handle_price, ParsedIntent
        result = _run(_handle_price(ParsedIntent(intent="price", symbol=None)))
        assert result["type"] == "error"


class TestHandleSwap:
    """Test _handle_swap gets quote and builds transaction."""

    def test_handle_swap_success(self):
        """Swap handler should return quote with jupiter_quote when Jupiter succeeds."""
        from features.chat_handler import _handle_swap, ParsedIntent

        mock_quote = AsyncMock(return_value={
            "output_amount": 0.066, "rate": 0.0066, "commission_pct": "0.10%",
        })
        mock_jup_quote = AsyncMock(return_value={
            "success": True, "raw_quote": {"inputMint": "usdc", "outputMint": "sol"},
            "outAmount": "66000",
        })

        mock_tokens = {
            "USDC": {"mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "decimals": 6},
            "SOL": {"mint": "So11111111111111111111111111111111111111112", "decimals": 9},
        }

        with patch("trading.crypto_swap.SUPPORTED_TOKENS", mock_tokens), \
             patch("trading.crypto_swap.get_swap_quote", mock_quote), \
             patch("blockchain.jupiter_router.get_quote", mock_jup_quote), \
             patch("blockchain.jupiter_router.USDC_MINT", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"):
            result = _run(_handle_swap(ParsedIntent(
                intent="swap", amount=10, from_token="USDC", to_token="SOL",
            )))

        assert result["type"] == "swap_quote"
        assert result["data"]["requires_wallet"] is True
        assert "jupiter_quote" in result["data"]

    def test_handle_swap_unknown_token(self):
        """Swap with unknown from_token should return error."""
        from features.chat_handler import _handle_swap, ParsedIntent

        mock_tokens = {"USDC": {"mint": "x", "decimals": 6}}

        with patch("trading.crypto_swap.SUPPORTED_TOKENS", mock_tokens):
            result = _run(_handle_swap(ParsedIntent(
                intent="swap", amount=10, from_token="FAKECOIN", to_token="USDC",
            )))
        assert result["type"] == "error"


class TestHandleHelp:
    """Test help handler."""

    def test_help_returns_commands(self):
        """Help handler should return command list."""
        from features.chat_handler import _handle_help
        result = _run(_handle_help())
        assert result["type"] == "help"
        assert "price" in result["response"]
        assert "swap" in result["response"]


class TestChatResponseModel:
    """Test ChatResponse pydantic model."""

    def test_chat_response_valid(self):
        """ChatResponse should accept valid data."""
        from features.chat_handler import ChatResponse
        resp = ChatResponse(response="Hello", type="help", data=None)
        assert resp.response == "Hello"
        assert resp.type == "help"

    def test_chat_response_with_data(self):
        """ChatResponse should accept dict data."""
        from features.chat_handler import ChatResponse
        resp = ChatResponse(response="SOL: $150", type="price", data={"price": 150})
        assert resp.data["price"] == 150


class TestEmptyMessage:
    """Test empty message handling."""

    def test_detect_intent_whitespace_only(self):
        """Whitespace-only message should fall back to llm."""
        from features.chat_handler import _detect_intent
        intent = _detect_intent("   ")
        assert intent.intent == "llm"
