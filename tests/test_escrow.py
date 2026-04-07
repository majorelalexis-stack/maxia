"""MAXIA V12 — Escrow API tests.

Tests the /api/escrow endpoints: public info, auth guards,
input validation, and admin-only restrictions.
All external dependencies (Solana, Base RPC, DB) are mocked.
"""
import pytest
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import AsyncClient, ASGITransport

# Setup env before imports
import os

os.environ.setdefault("JWT_SECRET", "ci-test-secret-key-32chars-minimum")
os.environ.setdefault("SANDBOX_MODE", "true")
os.environ.setdefault("ADMIN_KEY", "ci-admin-key-32chars-minimum-here")

import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from main import app

# ── Test constants ──
TEST_WALLET = "TestWallet123456789012345678901234567890123"
ADMIN_KEY = os.environ["ADMIN_KEY"]
BASE_URL = "http://test"


# ── Fixtures ──


@pytest.fixture
def auth_headers():
    """Create headers that bypass auth for testing."""
    return {"Authorization": "Bearer test-token"}


@pytest.fixture
def admin_headers():
    """Create headers with admin key."""
    return {"X-Admin-Key": ADMIN_KEY}


# ══════════════════════════════════════════════════════════════════════════════
#  PUBLIC ENDPOINTS (no auth)
# ══════════════════════════════════════════════════════════════════════════════


class TestEscrowPublicEndpoints:
    """Tests for unauthenticated /api/escrow endpoints."""

    @pytest.mark.asyncio
    async def test_escrow_info(self):
        """GET /api/escrow/info returns 200 with solana/base keys."""
        with patch(
            "blockchain.base_escrow_client.get_stats",
            new_callable=AsyncMock,
            return_value={"total_escrows": 0, "total_volume_usdc": 0, "total_commissions_usdc": 0},
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url=BASE_URL) as client:
                resp = await client.get("/api/escrow/info")
            assert resp.status_code == 200
            data = resp.json()
            assert "solana" in data
            assert "base" in data
            assert "chains" in data
            assert data["escrow_enabled"] is True
            # Solana section has expected keys
            assert "program_id" in data["solana"]
            assert "escrow_wallet" in data["solana"]
            assert "network" in data["solana"]
            # Base section has expected keys
            assert "contract" in data["base"]
            assert "network" in data["base"]

    @pytest.mark.asyncio
    async def test_escrow_base_stats(self):
        """GET /api/escrow/base/stats returns 200."""
        mock_stats = {
            "total_escrows": 5,
            "total_volume_usdc": 1000.0,
            "total_commissions_usdc": 15.0,
        }
        with patch(
            "blockchain.base_escrow_client.get_stats",
            new_callable=AsyncMock,
            return_value=mock_stats,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url=BASE_URL) as client:
                resp = await client.get("/api/escrow/base/stats")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total_escrows"] == 5
            assert data["total_volume_usdc"] == 1000.0

    @pytest.mark.asyncio
    async def test_escrow_base_contract(self):
        """GET /api/escrow/base/contract returns 200 with contract address."""
        with patch(
            "blockchain.base_escrow_client.get_contract_info",
            return_value={
                "address": "0xBd31bB973183F8476d0C4cF57a92e648b130510C",
                "chain": "base",
                "chain_id": 8453,
            },
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url=BASE_URL) as client:
                resp = await client.get("/api/escrow/base/contract")
            assert resp.status_code == 200
            data = resp.json()
            assert "address" in data
            assert data["address"].startswith("0x")
            assert data["chain"] == "base"


# ══════════════════════════════════════════════════════════════════════════════
#  AUTH GUARDS
# ══════════════════════════════════════════════════════════════════════════════


class TestEscrowAuth:
    """Tests that auth-required endpoints reject unauthenticated requests."""

    @pytest.mark.asyncio
    async def test_create_escrow_no_auth(self):
        """POST /api/escrow/create without Bearer token returns 401."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url=BASE_URL) as client:
            resp = await client.post("/api/escrow/create", json={"tx_signature": "abc"})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_list_escrows_no_auth(self):
        """GET /api/escrow/list without Bearer token returns 401."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url=BASE_URL) as client:
            resp = await client.get("/api/escrow/list")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_confirm_escrow_no_auth(self):
        """POST /api/escrow/confirm without Bearer token returns 401."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url=BASE_URL) as client:
            resp = await client.post("/api/escrow/confirm", json={"escrow_id": "test"})
        assert resp.status_code == 401


# ══════════════════════════════════════════════════════════════════════════════
#  INPUT VALIDATION
# ══════════════════════════════════════════════════════════════════════════════


class TestEscrowValidation:
    """Tests for input validation on escrow endpoints."""

    @pytest.mark.asyncio
    async def test_create_escrow_missing_tx_signature(self):
        """POST /api/escrow/create without tx_signature returns 400."""
        with patch(
            "core.auth.verify_session_token",
            return_value=TEST_WALLET,
        ), patch(
            "core.security.require_ofac_clear",
            return_value=None,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url=BASE_URL) as client:
                resp = await client.post(
                    "/api/escrow/create",
                    json={"seller_wallet": "SellerAddr1234567890123456789012345678901234", "amount_usdc": 10},
                    headers={"Authorization": "Bearer test-token"},
                )
            assert resp.status_code == 400
            assert "tx_signature" in resp.json().get("detail", "").lower()

    @pytest.mark.asyncio
    async def test_create_escrow_invalid_timeout(self):
        """POST /api/escrow/create with timeout_hours=999 returns 400."""
        with patch(
            "core.auth.verify_session_token",
            return_value=TEST_WALLET,
        ), patch(
            "core.security.require_ofac_clear",
            return_value=None,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url=BASE_URL) as client:
                resp = await client.post(
                    "/api/escrow/create",
                    json={
                        "tx_signature": "5abc123def",
                        "seller_wallet": "SellerAddr1234567890123456789012345678901234",
                        "amount_usdc": 10,
                        "timeout_hours": 999,
                    },
                    headers={"Authorization": "Bearer test-token"},
                )
            assert resp.status_code == 400
            assert "timeout" in resp.json().get("detail", "").lower()

    @pytest.mark.asyncio
    async def test_create_escrow_valid_timeout_range(self):
        """POST /api/escrow/create with timeout_hours=1 and 168 pass timeout validation.

        These requests may still fail on other validations (tx_signature verify etc.)
        but they must NOT fail on timeout_hours check.
        """
        with patch(
            "core.auth.verify_session_token",
            return_value=TEST_WALLET,
        ), patch(
            "core.security.require_ofac_clear",
            return_value=None,
        ), patch(
            "blockchain.escrow_client.EscrowClient.create_escrow",
            new_callable=AsyncMock,
            return_value={"success": True, "escrowId": "test-id"},
        ) as mock_create:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url=BASE_URL) as client:
                # timeout_hours=1 (minimum)
                resp1 = await client.post(
                    "/api/escrow/create",
                    json={
                        "tx_signature": "5valid_sig_1",
                        "seller_wallet": "SellerAddr1234567890123456789012345678901234",
                        "amount_usdc": 10,
                        "timeout_hours": 1,
                    },
                    headers={"Authorization": "Bearer test-token"},
                )
                # timeout_hours=168 (maximum)
                resp2 = await client.post(
                    "/api/escrow/create",
                    json={
                        "tx_signature": "5valid_sig_2",
                        "seller_wallet": "SellerAddr1234567890123456789012345678901234",
                        "amount_usdc": 50,
                        "timeout_hours": 168,
                    },
                    headers={"Authorization": "Bearer test-token"},
                )
            # Both should NOT be 400 for timeout
            # They succeed because create_escrow is mocked
            assert resp1.status_code == 200
            assert resp2.status_code == 200
            assert mock_create.call_count == 2

    @pytest.mark.asyncio
    async def test_base_verify_missing_tx_hash(self):
        """POST /api/escrow/base/verify with empty tx_hash returns 400."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url=BASE_URL) as client:
            resp = await client.post("/api/escrow/base/verify", json={"tx_hash": ""})
        assert resp.status_code == 400
        assert "tx_hash" in resp.json().get("detail", "").lower()

    @pytest.mark.asyncio
    async def test_base_verify_invalid_tx_hash(self):
        """POST /api/escrow/base/verify with tx_hash without 0x prefix returns 400."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url=BASE_URL) as client:
            resp = await client.post(
                "/api/escrow/base/verify",
                json={"tx_hash": "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890ab"},
            )
        assert resp.status_code == 400
        assert "tx_hash" in resp.json().get("detail", "").lower()


# ══════════════════════════════════════════════════════════════════════════════
#  ADMIN-ONLY ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════


class TestEscrowResolveAdmin:
    """Tests that admin-only endpoints reject non-admin callers."""

    @pytest.mark.asyncio
    async def test_resolve_no_admin(self):
        """POST /api/escrow/resolve without admin key returns 403."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url=BASE_URL) as client:
            resp = await client.post(
                "/api/escrow/resolve",
                json={"escrow_id": "test", "release_to_seller": True},
            )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_stats_no_admin(self):
        """GET /api/escrow/stats without admin key returns 403."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url=BASE_URL) as client:
            resp = await client.get("/api/escrow/stats")
        assert resp.status_code == 403
