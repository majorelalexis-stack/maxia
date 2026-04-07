"""MAXIA V12 — DCA, Grid, and Token Sniper bot tests."""
import pytest
import time
import os
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("JWT_SECRET", "ci-test-secret-key-32chars-minimum")
os.environ.setdefault("SANDBOX_MODE", "true")
os.environ.setdefault("ADMIN_KEY", "ci-admin-key-32chars-minimum-here")

import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from httpx import AsyncClient, ASGITransport


# ── Helpers ──


def _mock_db():
    """Create a mock DB with standard async methods."""
    db = MagicMock()
    db.get_agent = AsyncMock(return_value=None)
    db.raw_execute = AsyncMock()
    db.raw_executescript = AsyncMock()
    db.raw_execute_fetchall = AsyncMock(return_value=[])
    return db


# We must patch DB calls during app import/lifespan so the app starts cleanly.
# Patch at module level to avoid real DB/scheduler init during ASGI tests.

_LIFESPAN_PATCHES = [
    "core.database.db",
]


# ═══════════════════════════════════════════════════════════════════════════════
#  DCA BOT TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestDCABot:
    """Test DCA bot constants, token set, and auth-guarded endpoints."""

    def test_dca_constants(self):
        """DCA_COMMISSION_BPS == 10, DCA_MIN_AMOUNT == 1.0, DCA_MAX_AMOUNT == 1000.0."""
        from trading.dca_bot import DCA_COMMISSION_BPS, DCA_MIN_AMOUNT, DCA_MAX_AMOUNT

        assert DCA_COMMISSION_BPS == 10
        assert DCA_MIN_AMOUNT == 1.0
        assert DCA_MAX_AMOUNT == 1000.0

    def test_dca_tokens_set(self):
        """DCA_TOKENS has >= 30 tokens and includes SOL, ETH, BTC."""
        from trading.dca_bot import DCA_TOKENS

        assert len(DCA_TOKENS) >= 30
        assert "SOL" in DCA_TOKENS
        assert "ETH" in DCA_TOKENS
        assert "BTC" in DCA_TOKENS

    def test_dca_frequency_seconds(self):
        """FREQUENCY_SECONDS has daily/weekly/biweekly/monthly with correct values."""
        from trading.dca_bot import FREQUENCY_SECONDS

        assert FREQUENCY_SECONDS["daily"] == 86400
        assert FREQUENCY_SECONDS["weekly"] == 604800
        assert FREQUENCY_SECONDS["biweekly"] == 1209600
        assert FREQUENCY_SECONDS["monthly"] == 2592000

    def test_dca_max_fail_streak(self):
        """DCA_MAX_FAIL_STREAK == 3."""
        from trading.dca_bot import DCA_MAX_FAIL_STREAK

        assert DCA_MAX_FAIL_STREAK == 3

    def test_dca_pending_tx_expiry(self):
        """DCA_PENDING_TX_EXPIRY_SECONDS == 120."""
        from trading.dca_bot import DCA_PENDING_TX_EXPIRY_SECONDS

        assert DCA_PENDING_TX_EXPIRY_SECONDS == 120

    @pytest.mark.asyncio
    async def test_dca_create_no_auth(self):
        """POST /api/dca/create without API key returns 401."""
        from main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/dca/create",
                json={"to_token": "SOL", "amount_usdc": 10},
            )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_dca_list_no_auth(self):
        """GET /api/dca/my without API key returns 401."""
        from main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/dca/my")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_dca_pending_no_auth(self):
        """GET /api/dca/pending/{order_id} without API key returns 401."""
        from main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/dca/pending/fake-order-id")
        assert resp.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════════
#  GRID BOT TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestGridBot:
    """Test Grid bot constants, token set, and auth-guarded endpoints."""

    def test_grid_constants(self):
        """Check GRID_COMMISSION_BPS, GRID_MIN_GRIDS, GRID_MAX_GRIDS."""
        from trading.grid_bot import (
            GRID_COMMISSION_BPS,
            GRID_MIN_GRIDS,
            GRID_MAX_GRIDS,
        )

        assert GRID_COMMISSION_BPS == 10
        assert GRID_MIN_GRIDS == 3
        assert GRID_MAX_GRIDS == 50

    def test_grid_tokens_set(self):
        """GRID_TOKENS has >= 20 tokens."""
        from trading.grid_bot import GRID_TOKENS

        assert len(GRID_TOKENS) >= 20

    def test_grid_supported_tokens_include_majors(self):
        """SOL, ETH, BTC must be in GRID_TOKENS."""
        from trading.grid_bot import GRID_TOKENS

        assert "SOL" in GRID_TOKENS
        assert "ETH" in GRID_TOKENS
        assert "BTC" in GRID_TOKENS

    def test_grid_investment_limits(self):
        """GRID_MIN_INVESTMENT == 10.0, GRID_MAX_INVESTMENT == 10000.0."""
        from trading.grid_bot import GRID_MIN_INVESTMENT, GRID_MAX_INVESTMENT

        assert GRID_MIN_INVESTMENT == 10.0
        assert GRID_MAX_INVESTMENT == 10000.0

    @pytest.mark.asyncio
    async def test_grid_price_validation(self):
        """upper_price must be > lower_price — grid_create rejects inverted."""
        from trading.grid_bot import grid_create
        from fastapi import HTTPException

        mock_db = _mock_db()
        mock_db.get_agent = AsyncMock(return_value={"api_key": "k"})

        with patch("trading.grid_bot._get_db", return_value=mock_db), \
             patch("trading.grid_bot._get_agent", new_callable=AsyncMock, return_value={"api_key": "k"}):
            with pytest.raises(HTTPException) as exc_info:
                await grid_create(
                    req={
                        "token": "SOL",
                        "lower_price": 200,
                        "upper_price": 100,
                        "num_grids": 10,
                        "investment_usdc": 100,
                        "wallet": "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU",
                    },
                    x_api_key="test-key",
                )
            assert exc_info.value.status_code == 400
            assert "lower_price" in str(exc_info.value.detail).lower()

    def test_grid_worker_sleep_interval(self):
        """Grid worker uses asyncio.sleep(60) — verify via source inspection."""
        import inspect
        from trading.grid_bot import grid_worker

        source = inspect.getsource(grid_worker)
        assert "asyncio.sleep(60)" in source

    @pytest.mark.asyncio
    async def test_grid_create_no_auth(self):
        """POST /api/grid/create without API key returns 401."""
        from main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/grid/create",
                json={"token": "SOL", "lower_price": 100, "upper_price": 200},
            )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_grid_list_no_auth(self):
        """GET /api/grid/my without API key returns 401."""
        from main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/grid/my")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_grid_pending_no_auth(self):
        """GET /api/grid/pending/{bot_id} without API key returns 401."""
        from main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/grid/pending/fake-bot-id")
        assert resp.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════════
#  TOKEN SNIPER TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestTokenSniper:
    """Test Token Sniper config, models, and endpoints."""

    def test_sniper_config(self):
        """_SCAN_INTERVAL == 30, _MAX_WATCHLIST == 50."""
        from trading.token_sniper import _SCAN_INTERVAL, _MAX_WATCHLIST

        assert _SCAN_INTERVAL == 30
        assert _MAX_WATCHLIST == 50

    def test_sniper_watch_request_model(self):
        """WatchRequest validates correctly with required fields."""
        from trading.token_sniper import WatchRequest

        # Valid construction
        req = WatchRequest(
            wallet="7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU",
            telegram_chat_id="123456",
        )
        assert req.wallet == "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"
        assert req.min_market_cap_usd == 0
        assert req.max_market_cap_usd == 100_000
        assert req.auto_buy_usdc == 0

        # Missing wallet should raise ValidationError
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            WatchRequest()

    @pytest.mark.asyncio
    async def test_sniper_new_tokens_public(self):
        """GET /api/sniper/new-tokens returns 200."""
        from main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Patch the HTTP fetch so it doesn't hit real DexScreener
            with patch("trading.token_sniper._fetch_new_tokens", new_callable=AsyncMock, return_value=[]):
                resp = await client.get("/api/sniper/new-tokens")
        assert resp.status_code == 200
        data = resp.json()
        assert "tokens" in data
        assert "scan_interval_s" in data

    @pytest.mark.asyncio
    async def test_sniper_stats_public(self):
        """GET /api/sniper/stats returns 200 with scan_count fields."""
        from main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/sniper/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_scans" in data
        assert "scan_interval_s" in data

    @pytest.mark.asyncio
    async def test_sniper_watch_no_auth(self):
        """POST /api/sniper/watch without wallet returns 422."""
        from main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/sniper/watch", json={})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_sniper_watchlist_no_wallet(self):
        """GET /api/sniper/watchlist without wallet param returns 422."""
        from main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/sniper/watchlist")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_sniper_pending_no_wallet(self):
        """GET /api/sniper/pending without wallet param returns 422."""
        from main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/sniper/pending")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_sniper_delete_nonexistent(self):
        """DELETE /api/sniper/watch/fake-id returns 404."""
        from main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete("/api/sniper/watch/fake-id")
        assert resp.status_code == 404
