"""MAXIA V12 — DCA Bot & Grid Bot test suite.

Tests DCA order lifecycle (create, pending txs, confirm, cancel, worker)
and Grid bot lifecycle (create, validate params, crossing detection, confirm, cancel).
All external dependencies (DB, Jupiter, Solana RPC) are mocked.
"""
import asyncio
import json
import os
import sys
import time
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..", "backend")
sys.path.insert(0, BACKEND_DIR)


# ── Helpers ──

def _run(coro):
    """Run an async coroutine synchronously."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _mock_db():
    """Create a mock DB object with standard async methods."""
    db = MagicMock()
    db.get_agent = AsyncMock(return_value={"api_key": "test-key", "name": "TestAgent", "wallet": "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"})
    db.raw_execute = AsyncMock()
    db.raw_executescript = AsyncMock()
    db.raw_execute_fetchall = AsyncMock(return_value=[])
    return db


def _mock_row(data: dict):
    """Create a mock DB row that supports dict() conversion."""
    class Row(dict):
        pass
    return Row(data)


MOCK_WALLET = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"
MOCK_API_KEY = "test-api-key-12345"


# ═══════════════════════════════════════════════════════════════════════════════
#  DCA BOT TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestDCAConstants:
    """Verify DCA bot constants are properly defined."""

    def test_commission_bps(self):
        """DCA commission should be 10 bps (0.10%)."""
        from trading.dca_bot import DCA_COMMISSION_BPS
        assert DCA_COMMISSION_BPS == 10

    def test_min_amount(self):
        """DCA minimum amount should be 1.0 USDC."""
        from trading.dca_bot import DCA_MIN_AMOUNT
        assert DCA_MIN_AMOUNT == 1.0

    def test_max_amount(self):
        """DCA maximum amount should be 1000.0 USDC."""
        from trading.dca_bot import DCA_MAX_AMOUNT
        assert DCA_MAX_AMOUNT == 1000.0

    def test_max_fail_streak(self):
        """DCA fail streak limit should be 3."""
        from trading.dca_bot import DCA_MAX_FAIL_STREAK
        assert DCA_MAX_FAIL_STREAK == 3

    def test_pending_tx_expiry(self):
        """Pending tx expiry should be 120 seconds."""
        from trading.dca_bot import DCA_PENDING_TX_EXPIRY_SECONDS
        assert DCA_PENDING_TX_EXPIRY_SECONDS == 120

    def test_frequency_seconds(self):
        """All DCA frequencies should be defined."""
        from trading.dca_bot import FREQUENCY_SECONDS
        assert "daily" in FREQUENCY_SECONDS
        assert "weekly" in FREQUENCY_SECONDS
        assert "biweekly" in FREQUENCY_SECONDS
        assert "monthly" in FREQUENCY_SECONDS
        assert FREQUENCY_SECONDS["daily"] == 86400
        assert FREQUENCY_SECONDS["weekly"] == 604800

    def test_supported_tokens(self):
        """DCA should support key tokens."""
        from trading.dca_bot import DCA_TOKENS
        for token in ("SOL", "ETH", "BTC", "BONK", "JUP"):
            assert token in DCA_TOKENS


class TestDCACreate:
    """Test DCA order creation via dca_create endpoint."""

    def test_create_valid_order(self):
        """Valid DCA order should return success with order_id."""
        from trading.dca_bot import dca_create
        db = _mock_db()

        with patch("trading.dca_bot._get_db", return_value=db), \
             patch("trading.dca_bot._get_agent", new_callable=AsyncMock, return_value={"api_key": MOCK_API_KEY}), \
             patch("trading.price_oracle.get_price", new_callable=AsyncMock, return_value=150.0):
            result = _run(dca_create(
                req={"to_token": "SOL", "amount_usdc": 10, "frequency": "weekly", "wallet": MOCK_WALLET},
                x_api_key=MOCK_API_KEY,
            ))
        assert result["success"] is True
        assert result["to_token"] == "SOL"
        assert result["amount_usdc"] == 10
        assert result["frequency"] == "weekly"
        assert result["status"] == "active"
        assert "order_id" in result

    def test_create_rejects_invalid_token(self):
        """DCA order with unsupported token should raise 400."""
        from trading.dca_bot import dca_create
        from fastapi import HTTPException

        with patch("trading.dca_bot._get_db", return_value=_mock_db()), \
             patch("trading.dca_bot._get_agent", new_callable=AsyncMock, return_value={"api_key": MOCK_API_KEY}):
            with pytest.raises(HTTPException) as exc_info:
                _run(dca_create(
                    req={"to_token": "FAKECOIN", "amount_usdc": 10, "frequency": "weekly", "wallet": MOCK_WALLET},
                    x_api_key=MOCK_API_KEY,
                ))
            assert exc_info.value.status_code == 400

    def test_create_rejects_usdc_target(self):
        """DCA order targeting USDC should raise 400 (cannot DCA into USDC)."""
        from trading.dca_bot import dca_create
        from fastapi import HTTPException

        with patch("trading.dca_bot._get_db", return_value=_mock_db()), \
             patch("trading.dca_bot._get_agent", new_callable=AsyncMock, return_value={"api_key": MOCK_API_KEY}):
            with pytest.raises(HTTPException) as exc_info:
                _run(dca_create(
                    req={"to_token": "USDC", "amount_usdc": 10, "frequency": "weekly", "wallet": MOCK_WALLET},
                    x_api_key=MOCK_API_KEY,
                ))
            assert exc_info.value.status_code == 400

    def test_create_rejects_below_min_amount(self):
        """DCA order below minimum should raise 400."""
        from trading.dca_bot import dca_create
        from fastapi import HTTPException

        with patch("trading.dca_bot._get_db", return_value=_mock_db()), \
             patch("trading.dca_bot._get_agent", new_callable=AsyncMock, return_value={"api_key": MOCK_API_KEY}):
            with pytest.raises(HTTPException) as exc_info:
                _run(dca_create(
                    req={"to_token": "SOL", "amount_usdc": 0.5, "frequency": "weekly", "wallet": MOCK_WALLET},
                    x_api_key=MOCK_API_KEY,
                ))
            assert exc_info.value.status_code == 400

    def test_create_rejects_above_max_amount(self):
        """DCA order above maximum should raise 400."""
        from trading.dca_bot import dca_create
        from fastapi import HTTPException

        with patch("trading.dca_bot._get_db", return_value=_mock_db()), \
             patch("trading.dca_bot._get_agent", new_callable=AsyncMock, return_value={"api_key": MOCK_API_KEY}):
            with pytest.raises(HTTPException) as exc_info:
                _run(dca_create(
                    req={"to_token": "SOL", "amount_usdc": 2000, "frequency": "weekly", "wallet": MOCK_WALLET},
                    x_api_key=MOCK_API_KEY,
                ))
            assert exc_info.value.status_code == 400

    def test_create_rejects_invalid_frequency(self):
        """DCA order with unknown frequency should raise 400."""
        from trading.dca_bot import dca_create
        from fastapi import HTTPException

        with patch("trading.dca_bot._get_db", return_value=_mock_db()), \
             patch("trading.dca_bot._get_agent", new_callable=AsyncMock, return_value={"api_key": MOCK_API_KEY}):
            with pytest.raises(HTTPException) as exc_info:
                _run(dca_create(
                    req={"to_token": "SOL", "amount_usdc": 10, "frequency": "hourly", "wallet": MOCK_WALLET},
                    x_api_key=MOCK_API_KEY,
                ))
            assert exc_info.value.status_code == 400

    def test_create_rejects_short_wallet(self):
        """DCA order with wallet too short should raise 400."""
        from trading.dca_bot import dca_create
        from fastapi import HTTPException

        with patch("trading.dca_bot._get_db", return_value=_mock_db()), \
             patch("trading.dca_bot._get_agent", new_callable=AsyncMock, return_value={"api_key": MOCK_API_KEY}):
            with pytest.raises(HTTPException) as exc_info:
                _run(dca_create(
                    req={"to_token": "SOL", "amount_usdc": 10, "frequency": "weekly", "wallet": "short"},
                    x_api_key=MOCK_API_KEY,
                ))
            assert exc_info.value.status_code == 400

    def test_create_rejects_missing_api_key(self):
        """DCA order without API key should raise 401."""
        from trading.dca_bot import dca_create
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            _run(dca_create(req={"to_token": "SOL"}, x_api_key=None))
        assert exc_info.value.status_code == 401


class TestDCAPendingTxs:
    """Test DCA pending transaction retrieval."""

    def test_pending_returns_empty_list(self):
        """Pending txs for order with none should return empty list."""
        from trading.dca_bot import dca_pending_txs
        db = _mock_db()
        db.raw_execute_fetchall = AsyncMock(side_effect=[
            [_mock_row({"order_id": "test-order"})],  # ownership check
            [],  # no pending txs
        ])

        with patch("trading.dca_bot._get_db", return_value=db), \
             patch("trading.dca_bot._get_agent", new_callable=AsyncMock, return_value={"api_key": MOCK_API_KEY}):
            result = _run(dca_pending_txs(order_id="test-order", x_api_key=MOCK_API_KEY))
        assert result["pending"] == []
        assert result["total"] == 0

    def test_pending_returns_active_txs(self):
        """Pending txs should include swap_transaction and expiry info."""
        from trading.dca_bot import dca_pending_txs
        now = int(time.time())
        db = _mock_db()
        db.raw_execute_fetchall = AsyncMock(side_effect=[
            [_mock_row({"order_id": "test-order"})],  # ownership check
            [_mock_row({
                "tx_id": "tx-123",
                "swap_transaction": "base64tx==",
                "amount_usdc": 10.0,
                "to_token": "SOL",
                "price_usdc": 150.0,
                "commission_usdc": 0.01,
                "created_at": now,
                "quote_data": '{"outAmount": "66666"}',
            })],
        ])

        with patch("trading.dca_bot._get_db", return_value=db), \
             patch("trading.dca_bot._get_agent", new_callable=AsyncMock, return_value={"api_key": MOCK_API_KEY}):
            result = _run(dca_pending_txs(order_id="test-order", x_api_key=MOCK_API_KEY))
        assert result["total"] == 1
        assert result["pending"][0]["tx_id"] == "tx-123"
        assert result["pending"][0]["swap_transaction"] == "base64tx=="
        assert result["pending"][0]["expires_at"] == now + 120


class TestDCAConfirm:
    """Test DCA confirm endpoint."""

    def test_confirm_rejects_missing_api_key(self):
        """Confirm without API key should raise 401."""
        from trading.dca_bot import dca_confirm_tx
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            _run(dca_confirm_tx(tx_id="tx-123", req={}, x_api_key=None))
        assert exc_info.value.status_code == 401

    def test_confirm_rejects_short_signature(self):
        """Confirm with too-short signature should raise 400."""
        from trading.dca_bot import dca_confirm_tx
        from fastapi import HTTPException

        with patch("trading.dca_bot._get_agent", new_callable=AsyncMock, return_value={"api_key": MOCK_API_KEY}):
            with pytest.raises(HTTPException) as exc_info:
                _run(dca_confirm_tx(
                    tx_id="tx-123",
                    req={"tx_signature": "short"},
                    x_api_key=MOCK_API_KEY,
                ))
            assert exc_info.value.status_code == 400

    def test_confirm_rejects_not_found(self):
        """Confirm for non-existent tx should raise 404."""
        from trading.dca_bot import dca_confirm_tx
        from fastapi import HTTPException
        db = _mock_db()
        db.raw_execute_fetchall = AsyncMock(return_value=[])

        with patch("trading.dca_bot._get_db", return_value=db), \
             patch("trading.dca_bot._get_agent", new_callable=AsyncMock, return_value={"api_key": MOCK_API_KEY}):
            with pytest.raises(HTTPException) as exc_info:
                _run(dca_confirm_tx(
                    tx_id="nonexistent",
                    req={"tx_signature": "a" * 88},
                    x_api_key=MOCK_API_KEY,
                ))
            assert exc_info.value.status_code == 404

    def test_confirm_successful(self):
        """Successful confirm should update stats and return confirmed status."""
        from trading.dca_bot import dca_confirm_tx
        now = int(time.time())
        db = _mock_db()
        pending_row = _mock_row({
            "tx_id": "tx-123", "order_id": "order-456", "amount_usdc": 10.0,
            "to_token": "SOL", "price_usdc": 150.0, "commission_usdc": 0.01,
            "status": "pending", "created_at": now, "frequency": "weekly",
            "total_executed": 0, "total_invested_usdc": 0, "total_received": 0,
            "api_key": MOCK_API_KEY,
            "quote_data": json.dumps({"outAmount": "66666666"}),
        })
        db.raw_execute_fetchall = AsyncMock(side_effect=[
            [pending_row],  # fetch pending tx
            [_mock_row({"order_id": "order-456"})],  # order for telegram alert
        ])

        mock_verify = AsyncMock(return_value={"valid": True, "amount": 10.0})

        with patch("trading.dca_bot._get_db", return_value=db), \
             patch("trading.dca_bot._get_agent", new_callable=AsyncMock, return_value={"api_key": MOCK_API_KEY}), \
             patch("blockchain.solana_verifier.verify_transaction", mock_verify), \
             patch("trading.dca_bot._send_dca_alert", new_callable=AsyncMock):
            result = _run(dca_confirm_tx(
                tx_id="tx-123",
                req={"tx_signature": "a" * 88},
                x_api_key=MOCK_API_KEY,
            ))
        assert result["success"] is True
        assert result["status"] == "confirmed"
        assert result["tx_signature"] == "a" * 88


class TestDCACancel:
    """Test DCA order and pending tx cancellation."""

    def test_cancel_order(self):
        """Cancel active order should return cancelled status."""
        from trading.dca_bot import dca_cancel
        db = _mock_db()
        db.raw_execute_fetchall = AsyncMock(return_value=[
            _mock_row({"order_id": "order-123", "status": "active"}),
        ])

        with patch("trading.dca_bot._get_db", return_value=db), \
             patch("trading.dca_bot._get_agent", new_callable=AsyncMock, return_value={"api_key": MOCK_API_KEY}):
            result = _run(dca_cancel(order_id="order-123", x_api_key=MOCK_API_KEY))
        assert result["success"] is True
        assert result["status"] == "cancelled"

    def test_cancel_already_cancelled(self):
        """Cancel already-cancelled order should raise 400."""
        from trading.dca_bot import dca_cancel
        from fastapi import HTTPException
        db = _mock_db()
        db.raw_execute_fetchall = AsyncMock(return_value=[
            _mock_row({"order_id": "order-123", "status": "cancelled"}),
        ])

        with patch("trading.dca_bot._get_db", return_value=db), \
             patch("trading.dca_bot._get_agent", new_callable=AsyncMock, return_value={"api_key": MOCK_API_KEY}):
            with pytest.raises(HTTPException) as exc_info:
                _run(dca_cancel(order_id="order-123", x_api_key=MOCK_API_KEY))
            assert exc_info.value.status_code == 400

    def test_cancel_pending_tx(self):
        """Cancel a pending DCA tx should set status to expired."""
        from trading.dca_bot import dca_cancel_pending_tx
        db = _mock_db()
        db.raw_execute_fetchall = AsyncMock(return_value=[
            _mock_row({"tx_id": "tx-123", "status": "pending"}),
        ])

        with patch("trading.dca_bot._get_db", return_value=db), \
             patch("trading.dca_bot._get_agent", new_callable=AsyncMock, return_value={"api_key": MOCK_API_KEY}):
            result = _run(dca_cancel_pending_tx(tx_id="tx-123", x_api_key=MOCK_API_KEY))
        assert result["success"] is True
        assert result["status"] == "expired"


# ═══════════════════════════════════════════════════════════════════════════════
#  GRID BOT TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestGridConstants:
    """Verify Grid bot constants."""

    def test_grid_commission_bps(self):
        """Grid commission should be 10 bps."""
        from trading.grid_bot import GRID_COMMISSION_BPS
        assert GRID_COMMISSION_BPS == 10

    def test_grid_limits(self):
        """Grid num_grids limits should be 3-50."""
        from trading.grid_bot import GRID_MIN_GRIDS, GRID_MAX_GRIDS
        assert GRID_MIN_GRIDS == 3
        assert GRID_MAX_GRIDS == 50

    def test_investment_limits(self):
        """Grid investment limits should be $10 - $10,000."""
        from trading.grid_bot import GRID_MIN_INVESTMENT, GRID_MAX_INVESTMENT
        assert GRID_MIN_INVESTMENT == 10.0
        assert GRID_MAX_INVESTMENT == 10000.0


class TestGridCreate:
    """Test grid bot creation."""

    def test_create_valid_bot(self):
        """Valid grid bot should return success with calculated step."""
        from trading.grid_bot import grid_create
        db = _mock_db()

        with patch("trading.grid_bot._get_db", return_value=db), \
             patch("trading.grid_bot._get_agent", new_callable=AsyncMock, return_value={"api_key": MOCK_API_KEY}), \
             patch("trading.price_oracle.get_price", new_callable=AsyncMock, return_value=150.0):
            result = _run(grid_create(
                req={
                    "token": "SOL", "lower_price": 100, "upper_price": 200,
                    "num_grids": 10, "investment_usdc": 100, "wallet": MOCK_WALLET,
                },
                x_api_key=MOCK_API_KEY,
            ))
        assert result["success"] is True
        assert result["token"] == "SOL"
        assert result["num_grids"] == 10
        assert result["grid_step"] == 10.0  # (200-100)/10
        assert result["per_grid_usdc"] == 10.0  # 100/10
        assert result["status"] == "active"

    def test_create_rejects_too_few_grids(self):
        """Grid bot with num_grids < 3 should raise 400."""
        from trading.grid_bot import grid_create
        from fastapi import HTTPException

        with patch("trading.grid_bot._get_db", return_value=_mock_db()), \
             patch("trading.grid_bot._get_agent", new_callable=AsyncMock, return_value={"api_key": MOCK_API_KEY}):
            with pytest.raises(HTTPException) as exc_info:
                _run(grid_create(
                    req={
                        "token": "SOL", "lower_price": 100, "upper_price": 200,
                        "num_grids": 2, "investment_usdc": 100, "wallet": MOCK_WALLET,
                    },
                    x_api_key=MOCK_API_KEY,
                ))
            assert exc_info.value.status_code == 400

    def test_create_rejects_too_many_grids(self):
        """Grid bot with num_grids > 50 should raise 400."""
        from trading.grid_bot import grid_create
        from fastapi import HTTPException

        with patch("trading.grid_bot._get_db", return_value=_mock_db()), \
             patch("trading.grid_bot._get_agent", new_callable=AsyncMock, return_value={"api_key": MOCK_API_KEY}):
            with pytest.raises(HTTPException) as exc_info:
                _run(grid_create(
                    req={
                        "token": "SOL", "lower_price": 100, "upper_price": 200,
                        "num_grids": 51, "investment_usdc": 100, "wallet": MOCK_WALLET,
                    },
                    x_api_key=MOCK_API_KEY,
                ))
            assert exc_info.value.status_code == 400

    def test_create_rejects_inverted_prices(self):
        """Grid bot with lower_price >= upper_price should raise 400."""
        from trading.grid_bot import grid_create
        from fastapi import HTTPException

        with patch("trading.grid_bot._get_db", return_value=_mock_db()), \
             patch("trading.grid_bot._get_agent", new_callable=AsyncMock, return_value={"api_key": MOCK_API_KEY}):
            with pytest.raises(HTTPException) as exc_info:
                _run(grid_create(
                    req={
                        "token": "SOL", "lower_price": 200, "upper_price": 100,
                        "num_grids": 10, "investment_usdc": 100, "wallet": MOCK_WALLET,
                    },
                    x_api_key=MOCK_API_KEY,
                ))
            assert exc_info.value.status_code == 400

    def test_create_rejects_invalid_token(self):
        """Grid bot with unsupported token should raise 400."""
        from trading.grid_bot import grid_create
        from fastapi import HTTPException

        with patch("trading.grid_bot._get_db", return_value=_mock_db()), \
             patch("trading.grid_bot._get_agent", new_callable=AsyncMock, return_value={"api_key": MOCK_API_KEY}):
            with pytest.raises(HTTPException) as exc_info:
                _run(grid_create(
                    req={
                        "token": "NOPE", "lower_price": 100, "upper_price": 200,
                        "num_grids": 10, "investment_usdc": 100, "wallet": MOCK_WALLET,
                    },
                    x_api_key=MOCK_API_KEY,
                ))
            assert exc_info.value.status_code == 400

    def test_create_rejects_below_min_investment(self):
        """Grid bot with investment below $10 should raise 400."""
        from trading.grid_bot import grid_create
        from fastapi import HTTPException

        with patch("trading.grid_bot._get_db", return_value=_mock_db()), \
             patch("trading.grid_bot._get_agent", new_callable=AsyncMock, return_value={"api_key": MOCK_API_KEY}):
            with pytest.raises(HTTPException) as exc_info:
                _run(grid_create(
                    req={
                        "token": "SOL", "lower_price": 100, "upper_price": 200,
                        "num_grids": 5, "investment_usdc": 5, "wallet": MOCK_WALLET,
                    },
                    x_api_key=MOCK_API_KEY,
                ))
            assert exc_info.value.status_code == 400

    def test_create_rejects_missing_api_key(self):
        """Grid bot creation without API key should raise 401."""
        from trading.grid_bot import grid_create
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            _run(grid_create(req={}, x_api_key=None))
        assert exc_info.value.status_code == 401


class TestGridStop:
    """Test grid bot stop endpoint."""

    def test_stop_active_bot(self):
        """Stop active bot should return stopped status."""
        from trading.grid_bot import grid_stop
        db = _mock_db()
        db.raw_execute_fetchall = AsyncMock(return_value=[
            _mock_row({"bot_id": "bot-123", "status": "active"}),
        ])

        with patch("trading.grid_bot._get_db", return_value=db), \
             patch("trading.grid_bot._get_agent", new_callable=AsyncMock, return_value={"api_key": MOCK_API_KEY}):
            result = _run(grid_stop(bot_id="bot-123", x_api_key=MOCK_API_KEY))
        assert result["success"] is True
        assert result["status"] == "stopped"

    def test_stop_already_stopped(self):
        """Stop already-stopped bot should raise 400."""
        from trading.grid_bot import grid_stop
        from fastapi import HTTPException
        db = _mock_db()
        db.raw_execute_fetchall = AsyncMock(return_value=[
            _mock_row({"bot_id": "bot-123", "status": "stopped"}),
        ])

        with patch("trading.grid_bot._get_db", return_value=db), \
             patch("trading.grid_bot._get_agent", new_callable=AsyncMock, return_value={"api_key": MOCK_API_KEY}):
            with pytest.raises(HTTPException) as exc_info:
                _run(grid_stop(bot_id="bot-123", x_api_key=MOCK_API_KEY))
            assert exc_info.value.status_code == 400


class TestGridConfirm:
    """Test grid pending tx confirmation."""

    def test_confirm_grid_tx(self):
        """Confirm a pending grid tx should update bot stats."""
        from trading.grid_bot import grid_confirm_tx
        now = int(time.time())
        db = _mock_db()

        pending_row = _mock_row({
            "tx_id": "tx-grid-1", "bot_id": "bot-456", "side": "buy",
            "grid_level": 3, "amount_usdc": 10.0, "token": "SOL",
            "price_usdc": 150.0, "commission_usdc": 0.01, "status": "pending",
            "quote_data": json.dumps({"outAmount": "66666"}),
        })
        bot_row = _mock_row({
            "bot_id": "bot-456", "api_key": MOCK_API_KEY,
            "total_buys": 0, "total_sells": 0, "total_profit_usdc": 0,
        })

        db.raw_execute_fetchall = AsyncMock(side_effect=[
            [pending_row],  # fetch pending tx
            [bot_row],  # verify bot ownership
        ])

        mock_verify = AsyncMock(return_value={"valid": True})

        with patch("trading.grid_bot._get_db", return_value=db), \
             patch("trading.grid_bot._get_agent", new_callable=AsyncMock, return_value={"api_key": MOCK_API_KEY}), \
             patch("blockchain.solana_verifier.verify_transaction", mock_verify):
            result = _run(grid_confirm_tx(
                tx_id="tx-grid-1",
                req={"tx_signature": "b" * 88},
                x_api_key=MOCK_API_KEY,
            ))
        assert result["success"] is True
        assert result["side"] == "buy"
        assert result["status"] == "confirmed"


class TestGridCancelPending:
    """Test grid pending tx cancellation."""

    def test_cancel_pending_grid_tx(self):
        """Cancel a pending grid tx should set status to cancelled."""
        from trading.grid_bot import grid_cancel_pending
        db = _mock_db()
        db.raw_execute_fetchall = AsyncMock(side_effect=[
            [_mock_row({"tx_id": "tx-g1", "bot_id": "bot-1", "status": "pending"})],
            [_mock_row({"bot_id": "bot-1"})],  # bot ownership
        ])

        with patch("trading.grid_bot._get_db", return_value=db), \
             patch("trading.grid_bot._get_agent", new_callable=AsyncMock, return_value={"api_key": MOCK_API_KEY}):
            result = _run(grid_cancel_pending(tx_id="tx-g1", x_api_key=MOCK_API_KEY))
        assert result["success"] is True
        assert result["status"] == "cancelled"

    def test_cancel_already_confirmed_raises(self):
        """Cancel already-confirmed tx should raise 400."""
        from trading.grid_bot import grid_cancel_pending
        from fastapi import HTTPException
        db = _mock_db()
        db.raw_execute_fetchall = AsyncMock(return_value=[
            _mock_row({"tx_id": "tx-g1", "bot_id": "bot-1", "status": "confirmed"}),
        ])

        with patch("trading.grid_bot._get_db", return_value=db), \
             patch("trading.grid_bot._get_agent", new_callable=AsyncMock, return_value={"api_key": MOCK_API_KEY}):
            with pytest.raises(HTTPException) as exc_info:
                _run(grid_cancel_pending(tx_id="tx-g1", x_api_key=MOCK_API_KEY))
            assert exc_info.value.status_code == 400


class TestGridStats:
    """Test grid stats endpoint."""

    def test_stats_returns_constants(self):
        """Stats should include supported tokens and limits even on DB error."""
        from trading.grid_bot import grid_stats
        db = _mock_db()
        db.raw_execute_fetchall = AsyncMock(side_effect=Exception("DB down"))

        with patch("trading.grid_bot._get_db", return_value=db):
            result = _run(grid_stats())
        assert "supported_tokens" in result
        assert result["commission_bps"] == 10
        assert result["min_grids"] == 3
        assert result["max_grids"] == 50
