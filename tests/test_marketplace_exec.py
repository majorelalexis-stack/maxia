"""MAXIA V12 — Marketplace service execution test suite.

Tests native service dispatch (_dispatch_real_service), token/address extraction,
and LLM fallback. All external APIs are mocked.
"""
import asyncio
import json
import os
import sys
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
#  TOKEN & ADDRESS EXTRACTION
# ═══════════════════════════════════════════════════════════════════════════════

class TestExtractTokenFromPrompt:
    """Test _extract_token_from_prompt extracts crypto symbols from text."""

    def test_extract_sol(self):
        """'analyze SOL' should extract SOL."""
        from marketplace.public_api_discover import _extract_token_from_prompt
        assert _extract_token_from_prompt("analyze SOL") == "SOL"

    def test_extract_btc(self):
        """'what is BTC sentiment' should extract BTC."""
        from marketplace.public_api_discover import _extract_token_from_prompt
        assert _extract_token_from_prompt("what is BTC sentiment") == "BTC"

    def test_extract_eth(self):
        """'ETH price analysis' should extract ETH."""
        from marketplace.public_api_discover import _extract_token_from_prompt
        assert _extract_token_from_prompt("ETH price analysis") == "ETH"

    def test_extract_default_btc(self):
        """Prompt with no known token should default to first uppercase word or BTC."""
        from marketplace.public_api_discover import _extract_token_from_prompt
        result = _extract_token_from_prompt("hello world")
        # Should return something (first match or BTC)
        assert isinstance(result, str)
        assert len(result) >= 2

    def test_extract_from_lowercase(self):
        """Token extraction should work case-insensitively."""
        from marketplace.public_api_discover import _extract_token_from_prompt
        # The function upper()s the prompt first
        assert _extract_token_from_prompt("tell me about eth") == "ETH"

    def test_extract_known_token_priority(self):
        """Known tokens should take priority over unknown uppercase words."""
        from marketplace.public_api_discover import _extract_token_from_prompt
        assert _extract_token_from_prompt("THE SOL IS RISING") == "SOL"


class TestExtractAddressFromPrompt:
    """Test _extract_address_from_prompt extracts wallet addresses."""

    def test_extract_solana_address(self):
        """Should extract Solana base58 address from prompt."""
        from marketplace.public_api_discover import _extract_address_from_prompt
        addr = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"
        result = _extract_address_from_prompt(f"analyze wallet {addr}")
        assert result == addr

    def test_extract_evm_address(self):
        """Should extract EVM 0x address from prompt."""
        from marketplace.public_api_discover import _extract_address_from_prompt
        addr = "0x8589427373D6D84E98730D7795D8f6f8731FDA16"
        result = _extract_address_from_prompt(f"check {addr}")
        assert result == addr

    def test_no_address_returns_empty(self):
        """Prompt without address should return empty string."""
        from marketplace.public_api_discover import _extract_address_from_prompt
        result = _extract_address_from_prompt("hello world")
        assert result == ""


# ═══════════════════════════════════════════════════════════════════════════════
#  REAL SERVICE DISPATCH
# ═══════════════════════════════════════════════════════════════════════════════

class TestDispatchRealService:
    """Test _dispatch_real_service routes to real service implementations."""

    def test_sentiment_routes_to_analyzer(self):
        """maxia-sentiment should route to get_sentiment (mock)."""
        from marketplace.public_api_discover import _dispatch_real_service

        mock_sentiment = AsyncMock(return_value={
            "score": 0.75, "label": "bullish", "confidence": 0.85,
        })

        with patch("ai.sentiment_analyzer.get_sentiment", mock_sentiment):
            result = _run(_dispatch_real_service("maxia-sentiment", "analyze BTC"))
        assert result is not None
        parsed = json.loads(result)
        assert parsed["score"] == 0.75

    def test_image_routes_to_generator(self):
        """maxia-image should route to generate_image (mock)."""
        from marketplace.public_api_discover import _dispatch_real_service

        mock_gen = AsyncMock(return_value={
            "success": True, "url": "https://example.com/img.png", "model": "flux-schnell",
        })

        with patch("ai.image_gen.generate_image", mock_gen):
            result = _run(_dispatch_real_service("maxia-image", "a futuristic city"))
        assert result is not None
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert "image_url" in parsed

    def test_wallet_routes_to_analyzer(self):
        """maxia-wallet should route to analyze_wallet (mock) when address is present."""
        from marketplace.public_api_discover import _dispatch_real_service

        addr = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"
        mock_analyze = AsyncMock(return_value={
            "total_value_usd": 1500.0, "tokens": [{"symbol": "SOL", "amount": 10}],
        })

        with patch("features.web3_services.analyze_wallet", mock_analyze):
            result = _run(_dispatch_real_service("maxia-wallet", f"analyze {addr}"))
        assert result is not None
        parsed = json.loads(result)
        assert parsed["total_value_usd"] == 1500.0

    def test_unknown_service_returns_none(self):
        """Unknown service_id should return None (fallback to LLM)."""
        from marketplace.public_api_discover import _dispatch_real_service
        result = _run(_dispatch_real_service("maxia-unknown-service", "hello"))
        assert result is None

    def test_code_review_returns_none_for_llm(self):
        """Code review services should return None (handled by LLM fallback)."""
        from marketplace.public_api_discover import _dispatch_real_service
        result = _run(_dispatch_real_service("maxia-code", "review my code"))
        assert result is None

    def test_audit_returns_none_for_llm(self):
        """Audit service should return None (handled by LLM fallback)."""
        from marketplace.public_api_discover import _dispatch_real_service
        result = _run(_dispatch_real_service("maxia-audit", "audit my contract"))
        assert result is None


class TestExecuteNativeService:
    """Test _execute_native_service with real dispatch and LLM fallback."""

    def test_native_service_uses_real_dispatch_first(self):
        """Native service should try real dispatch before LLM."""
        from marketplace.public_api_discover import _execute_native_service

        mock_sentiment = AsyncMock(return_value={
            "score": 0.5, "label": "neutral", "confidence": 0.9,
        })

        with patch("ai.sentiment_analyzer.get_sentiment", mock_sentiment):
            result = _run(_execute_native_service("maxia-sentiment", "BTC sentiment"))
        parsed = json.loads(result)
        assert parsed["score"] == 0.5

    def test_native_service_falls_back_to_llm(self):
        """When real dispatch returns None, LLM fallback should be used."""
        from marketplace.public_api_discover import _execute_native_service

        mock_llm_result = "This is a code review result from the LLM."

        with patch("marketplace.public_api_discover._dispatch_real_service", new_callable=AsyncMock, return_value=None), \
             patch("marketplace.public_api_discover._execute_via_llm", new_callable=AsyncMock, return_value=mock_llm_result):
            result = _run(_execute_native_service("maxia-code", "review my code"))
        assert result == mock_llm_result


class TestExecuteViaLLM:
    """Test _execute_via_llm LLM Router + Cerebras fallback."""

    def test_llm_router_success(self):
        """LLM Router success should return the response text."""
        from marketplace.public_api_discover import _execute_via_llm

        mock_router = MagicMock()
        mock_router.call = AsyncMock(return_value="This is the LLM response.")

        with patch("ai.llm_router.router", mock_router):
            result = _run(_execute_via_llm("maxia-translate", "translate hello to French"))
        assert result == "This is the LLM response."

    def test_llm_router_timeout_uses_cerebras(self):
        """When LLM Router times out, Cerebras fallback should be tried."""
        from marketplace.public_api_discover import _execute_via_llm

        mock_router = MagicMock()
        mock_router.call = AsyncMock(side_effect=asyncio.TimeoutError())

        mock_http_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Cerebras response"}}],
        }
        mock_response.raise_for_status = MagicMock()
        mock_http_client.post = AsyncMock(return_value=mock_response)

        with patch("ai.llm_router.router", mock_router), \
             patch.dict(os.environ, {"CEREBRAS_API_KEY": "test-key"}), \
             patch("core.http_client.get_http_client", return_value=mock_http_client):
            result = _run(_execute_via_llm("maxia-translate", "translate hello"))
        assert result == "Cerebras response"


# ═══════════════════════════════════════════════════════════════════════════════
#  SERVICE PROMPTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestServicePrompts:
    """Test that service prompt definitions are complete."""

    def test_all_native_services_have_prompts(self):
        """Every native service ID should have a corresponding system prompt."""
        from marketplace.public_api_discover import _SERVICE_PROMPTS
        expected_services = [
            "maxia-audit", "maxia-code-review", "maxia-translate", "maxia-summary",
            "maxia-wallet-analysis", "maxia-sentiment", "maxia-image",
        ]
        for svc in expected_services:
            assert svc in _SERVICE_PROMPTS, f"Missing prompt for {svc}"
            assert len(_SERVICE_PROMPTS[svc]) > 10, f"Prompt too short for {svc}"

    def test_defi_yields_dispatch(self):
        """maxia-defi-yields should route to get_best_yields."""
        from marketplace.public_api_discover import _dispatch_real_service

        mock_yields = AsyncMock(return_value=[
            {"protocol": "Kamino", "apy": 8.5, "chain": "solana"},
        ])

        with patch("trading.defi_scanner.get_best_yields", mock_yields):
            result = _run(_dispatch_real_service("maxia-defi-yields", "best USDC yields"))
        assert result is not None
        parsed = json.loads(result)
        assert parsed["asset"] == "USDC"
        assert len(parsed["yields"]) == 1
