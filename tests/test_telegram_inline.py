"""Tests for MAXIA Telegram bot inline mode (P4B — Plan CEO V7)."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Optional

import pytest

BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..", "backend")
sys.path.insert(0, BACKEND_DIR)

from integrations.telegram_inline import (  # noqa: E402
    DEFAULT_CACHE_TIME,
    InlineHandlers,
    MAX_RESULTS,
    build_answer_payload,
    handle_agent,
    handle_gpu,
    handle_price,
    handle_swap,
    route_inline_query,
)


# ═══════════════════════════════════════════════════════════════════════════
#  Fakes
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class FakeOracle:
    prices: dict[str, dict] = field(default_factory=lambda: {
        "BTC": {"price": 45_000.50, "source": "pyth", "change_24h": 2.3},
        "ETH": {"price": 2_500.12, "source": "pyth", "change_24h": -1.1},
        "SOL": {"price": 245.67, "source": "pyth", "change_24h": 5.2},
        "DOGE": {"price": 0.12, "source": "coingecko", "change_24h": 0.5},
    })
    calls: list[str] = field(default_factory=list)
    raise_on: Optional[str] = None

    async def get_price(self, symbol: str) -> dict:
        self.calls.append(symbol)
        if self.raise_on == symbol:
            raise RuntimeError(f"oracle down for {symbol}")
        return self.prices.get(symbol, {"price": 0, "source": "none"})


@dataclass
class FakeSwap:
    quotes: dict[tuple, dict] = field(default_factory=dict)

    async def get_quote(self, amount: float, from_sym: str, to_sym: str) -> dict:
        key = (amount, from_sym, to_sym)
        if key in self.quotes:
            return self.quotes[key]
        # Default: 1 USDC = 0.0004 ETH
        if from_sym == "USDC" and to_sym == "ETH":
            return {
                "out_amount": amount * 0.0004,
                "source": "jupiter",
                "price_impact": 0.05,
            }
        if from_sym == "ETH" and to_sym == "USDC":
            return {
                "out_amount": amount * 2500.0,
                "source": "0x",
                "price_impact": 0.12,
            }
        return {"out_amount": 0, "source": "none"}


@dataclass
class FakeGpu:
    tiers: list[dict] = field(default_factory=lambda: [
        {"name": "rtx3090", "vram_gb": 24, "price_usd_hour": 0.35, "provider": "akash"},
        {"name": "rtx4090", "vram_gb": 24, "price_usd_hour": 0.55, "provider": "akash"},
        {"name": "a100", "vram_gb": 80, "price_usd_hour": 1.80, "provider": "akash"},
        {"name": "h100", "vram_gb": 80, "price_usd_hour": 3.20, "provider": "akash"},
    ])

    async def list_tiers(self, filter_name: Optional[str] = None) -> list[dict]:
        if filter_name is None:
            return list(self.tiers)
        return [t for t in self.tiers if filter_name in t["name"].lower()]


@dataclass
class FakeAgent:
    catalog: dict[str, list[dict]] = field(default_factory=lambda: {
        "ceo": [{
            "id": "svc_ceo_analyst",
            "name": "CEO Market Analyst",
            "description": "Deep market analysis and reports",
            "price_usdc": 2.99,
        }],
        "trader": [
            {"id": "svc_trader_1", "name": "DCA Trader",
             "description": "Dollar-cost averaging bot", "price_usdc": 0.50},
            {"id": "svc_trader_2", "name": "Grid Trader",
             "description": "Grid trading bot", "price_usdc": 0.75},
        ],
    })

    async def find(self, name: str) -> list[dict]:
        return self.catalog.get(name.lower(), [])


@pytest.fixture
def handlers() -> InlineHandlers:
    return InlineHandlers(
        oracle=FakeOracle(),
        swap=FakeSwap(),
        gpu=FakeGpu(),
        agent=FakeAgent(),
        base_url="https://maxiaworld.app",
    )


# ═══════════════════════════════════════════════════════════════════════════
#  Price handler
# ═══════════════════════════════════════════════════════════════════════════


class TestHandlePrice:
    @pytest.mark.asyncio
    async def test_btc_price(self, handlers):
        results = await handle_price("price BTC", handlers)
        assert len(results) == 1
        article = results[0]
        assert article["type"] == "article"
        assert "BTC" in article["title"]
        assert "$45,000.50" in article["title"]
        assert "+2.30%" in article["title"]
        assert article["id"]
        assert len(article["id"]) <= 64

    @pytest.mark.asyncio
    async def test_price_case_insensitive(self, handlers):
        r1 = await handle_price("price btc", handlers)
        r2 = await handle_price("PRICE BTC", handlers)
        r3 = await handle_price("Price BTC", handlers)
        assert len(r1) == len(r2) == len(r3) == 1

    @pytest.mark.asyncio
    async def test_unknown_token_empty(self, handlers):
        results = await handle_price("price XXX", handlers)
        assert results == []

    @pytest.mark.asyncio
    async def test_malformed_returns_empty(self, handlers):
        assert await handle_price("not a price query", handlers) == []
        assert await handle_price("price", handlers) == []
        assert await handle_price("price BTC ETH", handlers) == []

    @pytest.mark.asyncio
    async def test_oracle_exception_handled(self, handlers):
        handlers.oracle.raise_on = "BTC"  # type: ignore[attr-defined]
        results = await handle_price("price BTC", handlers)
        assert results == []

    @pytest.mark.asyncio
    async def test_negative_change(self, handlers):
        results = await handle_price("price ETH", handlers)
        assert len(results) == 1
        assert "-1.10%" in results[0]["title"]

    @pytest.mark.asyncio
    async def test_url_in_article(self, handlers):
        results = await handle_price("price SOL", handlers)
        assert "SOL" in results[0]["url"]
        assert "maxiaworld.app" in results[0]["url"]


# ═══════════════════════════════════════════════════════════════════════════
#  Swap handler
# ═══════════════════════════════════════════════════════════════════════════


class TestHandleSwap:
    @pytest.mark.asyncio
    async def test_simple_swap(self, handlers):
        results = await handle_swap("swap 100 USDC ETH", handlers)
        assert len(results) == 1
        article = results[0]
        assert "USDC" in article["title"]
        assert "ETH" in article["title"]

    @pytest.mark.asyncio
    async def test_same_symbol_rejected(self, handlers):
        assert await handle_swap("swap 100 USDC USDC", handlers) == []

    @pytest.mark.asyncio
    async def test_zero_amount_rejected(self, handlers):
        assert await handle_swap("swap 0 USDC ETH", handlers) == []

    @pytest.mark.asyncio
    async def test_huge_amount_rejected(self, handlers):
        assert await handle_swap("swap 9999999 USDC ETH", handlers) == []

    @pytest.mark.asyncio
    async def test_decimal_amount(self, handlers):
        results = await handle_swap("swap 1.5 ETH USDC", handlers)
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_price_impact_shown(self, handlers):
        results = await handle_swap("swap 100 USDC ETH", handlers)
        text = results[0]["input_message_content"]["message_text"]
        assert "impact" in text.lower() or "%" in text


# ═══════════════════════════════════════════════════════════════════════════
#  GPU handler
# ═══════════════════════════════════════════════════════════════════════════


class TestHandleGpu:
    @pytest.mark.asyncio
    async def test_list_all(self, handlers):
        results = await handle_gpu("gpu", handlers)
        assert len(results) == 4
        assert all(r["type"] == "article" for r in results)

    @pytest.mark.asyncio
    async def test_filter_rtx(self, handlers):
        results = await handle_gpu("gpu rtx", handlers)
        assert len(results) == 2
        assert all("rtx" in r["title"].lower() for r in results)

    @pytest.mark.asyncio
    async def test_filter_a100(self, handlers):
        results = await handle_gpu("gpu a100", handlers)
        assert len(results) == 1
        assert "a100" in results[0]["title"].lower()

    @pytest.mark.asyncio
    async def test_no_match(self, handlers):
        results = await handle_gpu("gpu xyz", handlers)
        assert results == []

    @pytest.mark.asyncio
    async def test_max_results_capped(self, handlers):
        # Inject a huge tier list
        handlers.gpu.tiers = [  # type: ignore[attr-defined]
            {"name": f"gpu{i}", "vram_gb": 24, "price_usd_hour": 0.1, "provider": "akash"}
            for i in range(50)
        ]
        results = await handle_gpu("gpu", handlers)
        assert len(results) <= MAX_RESULTS


# ═══════════════════════════════════════════════════════════════════════════
#  Agent handler
# ═══════════════════════════════════════════════════════════════════════════


class TestHandleAgent:
    @pytest.mark.asyncio
    async def test_ceo_agent(self, handlers):
        results = await handle_agent("agent CEO", handlers)
        assert len(results) == 1
        assert "CEO Market Analyst" in results[0]["title"]

    @pytest.mark.asyncio
    async def test_trader_multiple(self, handlers):
        results = await handle_agent("agent trader", handlers)
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_unknown_empty(self, handlers):
        assert await handle_agent("agent nonexistent", handlers) == []


# ═══════════════════════════════════════════════════════════════════════════
#  Router
# ═══════════════════════════════════════════════════════════════════════════


class TestRouteInlineQuery:
    @pytest.mark.asyncio
    async def test_routes_price(self, handlers):
        results = await route_inline_query("price BTC", handlers)
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_routes_swap(self, handlers):
        results = await route_inline_query("swap 100 USDC ETH", handlers)
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_routes_gpu(self, handlers):
        results = await route_inline_query("gpu", handlers)
        assert len(results) == 4

    @pytest.mark.asyncio
    async def test_routes_agent(self, handlers):
        results = await route_inline_query("agent CEO", handlers)
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_empty_query(self, handlers):
        assert await route_inline_query("", handlers) == []
        assert await route_inline_query("   ", handlers) == []

    @pytest.mark.asyncio
    async def test_non_string(self, handlers):
        assert await route_inline_query(None, handlers) == []  # type: ignore[arg-type]
        assert await route_inline_query(42, handlers) == []  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_too_long(self, handlers):
        assert await route_inline_query("a" * 500, handlers) == []

    @pytest.mark.asyncio
    async def test_unknown_keyword(self, handlers):
        assert await route_inline_query("hello world", handlers) == []

    @pytest.mark.asyncio
    async def test_case_insensitive_keyword(self, handlers):
        r1 = await route_inline_query("Price BTC", handlers)
        r2 = await route_inline_query("PRICE BTC", handlers)
        assert len(r1) == len(r2) == 1


# ═══════════════════════════════════════════════════════════════════════════
#  answerInlineQuery payload
# ═══════════════════════════════════════════════════════════════════════════


class TestAnswerPayload:
    def test_basic(self):
        results = [{"type": "article", "id": "a", "title": "t",
                    "input_message_content": {"message_text": "m"}}]
        payload = build_answer_payload("query123", results)
        assert payload["inline_query_id"] == "query123"
        assert payload["results"] == results
        assert payload["cache_time"] == DEFAULT_CACHE_TIME
        assert payload["is_personal"] is False

    def test_max_results_capped(self):
        many = [{"type": "article", "id": f"id{i}", "title": "t",
                 "input_message_content": {"message_text": "m"}} for i in range(100)]
        payload = build_answer_payload("q", many)
        assert len(payload["results"]) == MAX_RESULTS

    def test_cache_time_clamped(self):
        payload = build_answer_payload("q", [], cache_time=-5)
        assert payload["cache_time"] == 0
        payload2 = build_answer_payload("q", [], cache_time=999_999)
        assert payload2["cache_time"] == 86400

    def test_inline_query_id_truncated(self):
        long_id = "x" * 200
        payload = build_answer_payload(long_id, [])
        assert len(payload["inline_query_id"]) <= 64
