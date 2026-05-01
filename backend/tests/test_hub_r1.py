"""Tests TDD pour MAXIA Hub R1 — Boost on-chain externe (Solana + Base).

Ordre TDD : RED avant implémentation.
"""
import time
import pytest
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from hub.hub_r1 import (  # noqa — pas encore créé
    WalletActivity,
    WalletHistoryFetcher,
    compute_r1_boost,
    apply_r1_boost,
    r1_router,
)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def make_mock_db():
    mock = MagicMock()
    mock.raw_execute = AsyncMock(return_value=None)
    mock.raw_execute_fetchall = AsyncMock(return_value=[])
    mock._fetchone = AsyncMock(return_value=None)
    return mock


def make_http_response(status_code: int, body=None):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json = MagicMock(return_value=body or {})
    return resp


@pytest.fixture
def mock_db():
    return make_mock_db()


@pytest.fixture
def fetcher():
    return WalletHistoryFetcher()


# ─── WalletActivity dataclass ─────────────────────────────────────────────────

class TestWalletActivity:
    def test_has_required_fields(self):
        a = WalletActivity(chain="solana", wallet="abc", tx_count=10, wallet_age_days=30)
        assert a.chain == "solana"
        assert a.tx_count == 10
        assert a.wallet_age_days == 30
        assert a.error is None

    def test_error_field_optional(self):
        a = WalletActivity(chain="base", wallet="0x1", tx_count=0, wallet_age_days=0, error="timeout")
        assert a.error == "timeout"


# ─── Solana history fetch ──────────────────────────────────────────────────────

class TestSolanaHistoryFetch:
    @pytest.mark.asyncio
    async def test_returns_tx_count_from_signatures(self, fetcher):
        now = int(time.time())
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=make_http_response(200, {
            "result": [
                {"signature": "sig1", "blockTime": now - 10},
                {"signature": "sig2", "blockTime": now - 100},
                {"signature": "sig3", "blockTime": now - 86400 * 180},  # 180 jours
            ]
        }))
        activity = await fetcher.fetch_solana("wallet123", client)
        assert activity.chain == "solana"
        assert activity.tx_count == 3
        assert activity.error is None

    @pytest.mark.asyncio
    async def test_wallet_age_from_oldest_tx(self, fetcher):
        now = int(time.time())
        age_days = 365
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=make_http_response(200, {
            "result": [
                {"signature": "sig1", "blockTime": now - 3600},
                {"signature": "sig2", "blockTime": now - 86400 * age_days},  # le plus ancien
            ]
        }))
        activity = await fetcher.fetch_solana("wallet123", client)
        assert activity.wallet_age_days >= age_days - 1  # tolérance 1j

    @pytest.mark.asyncio
    async def test_empty_history_returns_zero(self, fetcher):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=make_http_response(200, {"result": []}))
        activity = await fetcher.fetch_solana("newwallet", client)
        assert activity.tx_count == 0
        assert activity.wallet_age_days == 0

    @pytest.mark.asyncio
    async def test_rpc_error_returns_error_activity(self, fetcher):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(side_effect=httpx.HTTPError("timeout"))
        activity = await fetcher.fetch_solana("wallet123", client)
        assert activity.tx_count == 0
        assert activity.error is not None

    @pytest.mark.asyncio
    async def test_non_200_returns_error_activity(self, fetcher):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=make_http_response(429, {}))
        activity = await fetcher.fetch_solana("wallet123", client)
        assert activity.error is not None

    @pytest.mark.asyncio
    async def test_request_uses_correct_rpc_method(self, fetcher):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=make_http_response(200, {"result": []}))
        await fetcher.fetch_solana("wallet123", client)
        payload = client.post.call_args[1]["json"]
        assert payload["method"] == "getSignaturesForAddress"
        assert payload["params"][0] == "wallet123"


# ─── Base history fetch ───────────────────────────────────────────────────────

class TestBaseHistoryFetch:
    @pytest.mark.asyncio
    async def test_returns_tx_count_from_eth_call(self, fetcher):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=make_http_response(200, {
            "result": "0x1a"  # 26 en hex
        }))
        activity = await fetcher.fetch_base("0xWallet", client)
        assert activity.chain == "base"
        assert activity.tx_count == 26
        assert activity.error is None

    @pytest.mark.asyncio
    async def test_rpc_error_returns_error_activity(self, fetcher):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(side_effect=httpx.HTTPError("refused"))
        activity = await fetcher.fetch_base("0xWallet", client)
        assert activity.tx_count == 0
        assert activity.error is not None

    @pytest.mark.asyncio
    async def test_non_200_returns_error_activity(self, fetcher):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=make_http_response(500, {}))
        activity = await fetcher.fetch_base("0xWallet", client)
        assert activity.error is not None

    @pytest.mark.asyncio
    async def test_request_uses_eth_transaction_count_method(self, fetcher):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=make_http_response(200, {"result": "0x0"}))
        await fetcher.fetch_base("0xABC", client)
        payload = client.post.call_args[1]["json"]
        assert payload["method"] == "eth_getTransactionCount"
        assert payload["params"][0] == "0xABC"

    @pytest.mark.asyncio
    async def test_zero_tx_count_is_valid(self, fetcher):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=make_http_response(200, {"result": "0x0"}))
        activity = await fetcher.fetch_base("0xNew", client)
        assert activity.tx_count == 0
        assert activity.error is None


# ─── compute_r1_boost ──────────────────────────────────────────────────────────

class TestComputeR1Boost:
    def test_zero_activity_gives_zero_boost(self):
        a = WalletActivity(chain="solana", wallet="w", tx_count=0, wallet_age_days=0)
        assert compute_r1_boost(a) == 0.0

    def test_high_activity_gives_max_boost(self):
        a = WalletActivity(chain="solana", wallet="w", tx_count=500, wallet_age_days=730)
        boost = compute_r1_boost(a)
        assert boost == pytest.approx(15.0, abs=0.01)

    def test_boost_capped_at_15(self):
        a = WalletActivity(chain="solana", wallet="w", tx_count=99999, wallet_age_days=9999)
        boost = compute_r1_boost(a)
        assert boost <= 15.0

    def test_partial_activity_gives_partial_boost(self):
        a_low = WalletActivity(chain="base", wallet="w", tx_count=10, wallet_age_days=30)
        a_high = WalletActivity(chain="base", wallet="w", tx_count=300, wallet_age_days=500)
        assert compute_r1_boost(a_low) < compute_r1_boost(a_high)

    def test_error_activity_gives_zero_boost(self):
        a = WalletActivity(chain="solana", wallet="w", tx_count=0, wallet_age_days=0, error="rpc timeout")
        assert compute_r1_boost(a) == 0.0

    def test_boost_is_float(self):
        a = WalletActivity(chain="solana", wallet="w", tx_count=50, wallet_age_days=180)
        boost = compute_r1_boost(a)
        assert isinstance(boost, float)

    def test_200_txs_365_days_near_max(self):
        a = WalletActivity(chain="solana", wallet="w", tx_count=200, wallet_age_days=365)
        boost = compute_r1_boost(a)
        assert boost >= 12.0  # doit être proche du max


# ─── apply_r1_boost ───────────────────────────────────────────────────────────

class TestApplyR1Boost:
    def _hub_row(self, hub_id="hub1", wallet="wallet1", chain="solana"):
        return {"hub_id": hub_id, "wallet": wallet, "chain": chain}

    @pytest.mark.asyncio
    async def test_updates_score_r1_boost_in_hub_agents(self, mock_db):
        mock_db._fetchone = AsyncMock(return_value=self._hub_row())
        client = AsyncMock(spec=httpx.AsyncClient)
        with patch("hub.hub_r1.WalletHistoryFetcher") as MockFetcher:
            mock_f = MockFetcher.return_value
            mock_f.fetch_solana = AsyncMock(return_value=WalletActivity(
                chain="solana", wallet="wallet1", tx_count=100, wallet_age_days=200
            ))
            result = await apply_r1_boost(mock_db, "hub1", client)

        assert result["boost"] >= 0
        mock_db.raw_execute.assert_called()

    @pytest.mark.asyncio
    async def test_unknown_hub_id_raises_404(self, mock_db):
        mock_db._fetchone = AsyncMock(return_value=None)
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await apply_r1_boost(mock_db, "ghost", AsyncMock())
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_result_contains_boost_and_details(self, mock_db):
        mock_db._fetchone = AsyncMock(return_value=self._hub_row())
        client = AsyncMock(spec=httpx.AsyncClient)
        with patch("hub.hub_r1.WalletHistoryFetcher") as MockFetcher:
            mock_f = MockFetcher.return_value
            mock_f.fetch_solana = AsyncMock(return_value=WalletActivity(
                chain="solana", wallet="wallet1", tx_count=50, wallet_age_days=90
            ))
            result = await apply_r1_boost(mock_db, "hub1", client)

        assert "boost" in result
        assert "tx_count" in result
        assert "wallet_age_days" in result
        assert "chain" in result

    @pytest.mark.asyncio
    async def test_base_chain_uses_fetch_base(self, mock_db):
        mock_db._fetchone = AsyncMock(return_value=self._hub_row(chain="base", wallet="0xABC"))
        client = AsyncMock(spec=httpx.AsyncClient)
        with patch("hub.hub_r1.WalletHistoryFetcher") as MockFetcher:
            mock_f = MockFetcher.return_value
            mock_f.fetch_base = AsyncMock(return_value=WalletActivity(
                chain="base", wallet="0xABC", tx_count=20, wallet_age_days=0
            ))
            await apply_r1_boost(mock_db, "hub1", client)
        mock_f.fetch_base.assert_called_once()


# ─── Endpoints ────────────────────────────────────────────────────────────────

class TestR1Endpoints:
    def _app(self):
        from fastapi import FastAPI
        app = FastAPI()
        app.include_router(r1_router)
        return app

    def test_post_refresh_endpoint_exists(self):
        from fastapi.testclient import TestClient
        resp = TestClient(self._app(), raise_server_exceptions=False).post("/api/hub/r1/refresh/hub1")
        assert resp.status_code != 404

    def test_get_r1_detail_endpoint_exists(self):
        from fastapi.testclient import TestClient
        resp = TestClient(self._app(), raise_server_exceptions=False).get("/api/hub/r1/hub1")
        assert resp.status_code != 404
