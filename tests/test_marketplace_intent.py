"""MAXIA V12 — Marketplace intent & execution tests.

Tests AIP Protocol intent envelopes, legacy signed intents, marketplace
discovery endpoints, and shared marketplace utilities.
"""
import pytest
import time
import os
import sys
import json
import hashlib
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta

os.environ.setdefault("JWT_SECRET", "ci-test-secret-key-32chars-minimum")
os.environ.setdefault("SANDBOX_MODE", "true")
os.environ.setdefault("ADMIN_KEY", "ci-admin-key-32chars-minimum-here")

BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..", "backend")
sys.path.insert(0, BACKEND_DIR)


# ═══════════════════════════════════════════════════════════════════════════════
#  TestIntentProtocol — AIP Protocol intent envelopes & legacy fallback
# ═══════════════════════════════════════════════════════════════════════════════

class TestIntentProtocol:
    """Test intent envelope creation, validation, and action mapping."""

    def test_intent_types_exist(self):
        """MAXIA_DEFAULT_ACTIONS has core action types: swap, gpu_rent, escrow_lock, etc."""
        from marketplace.intent import MAXIA_DEFAULT_ACTIONS
        assert isinstance(MAXIA_DEFAULT_ACTIONS, list)
        assert len(MAXIA_DEFAULT_ACTIONS) >= 5
        for action in ["swap", "gpu_rent", "escrow_lock", "escrow_confirm", "marketplace_execute"]:
            assert action in MAXIA_DEFAULT_ACTIONS, f"{action} missing from MAXIA_DEFAULT_ACTIONS"

    def test_intent_types_include_trading(self):
        """MAXIA_DEFAULT_ACTIONS includes stocks and DeFi actions."""
        from marketplace.intent import MAXIA_DEFAULT_ACTIONS
        assert "stocks_buy" in MAXIA_DEFAULT_ACTIONS
        assert "stocks_sell" in MAXIA_DEFAULT_ACTIONS
        assert "defi_deposit" in MAXIA_DEFAULT_ACTIONS

    def test_legacy_sign_intent_returns_envelope(self):
        """sign_intent_legacy() returns dict with all required fields."""
        from nacl.signing import SigningKey
        import base58

        sk = SigningKey.generate()
        pk = sk.verify_key
        pk_b58 = base58.b58encode(bytes(pk)).decode()

        from marketplace.intent import sign_intent_legacy
        did = "did:web:maxiaworld.app:agent:test-001"

        envelope = sign_intent_legacy(
            action="swap",
            params={"from": "USDC", "to": "SOL", "amount": 100},
            private_key_hex=sk.encode().hex(),
            did=did,
            expires_s=300,
        )

        assert isinstance(envelope, dict)
        assert envelope["action"] == "swap"
        assert envelope["did"] == did
        assert envelope["v"] == 1
        assert "nonce" in envelope
        assert "expires" in envelope
        assert "sig" in envelope
        assert "params" in envelope

    def test_legacy_intent_nonce_unique(self):
        """Multiple calls to sign_intent_legacy produce different nonces."""
        import time as _time
        from nacl.signing import SigningKey
        from marketplace.intent import sign_intent_legacy

        sk = SigningKey.generate()
        did = "did:web:maxiaworld.app:agent:test-002"
        hex_key = sk.encode().hex()

        env1 = sign_intent_legacy("swap", {"from": "USDC"}, hex_key, did)
        _time.sleep(0.001)  # ensure time_ns() advances
        env2 = sign_intent_legacy("swap", {"from": "USDC"}, hex_key, did)

        assert env1["nonce"] != env2["nonce"], "Nonces must be unique"

    def test_legacy_intent_has_valid_timestamp(self):
        """Intent expires field is a valid ISO timestamp in the near future."""
        from nacl.signing import SigningKey
        from marketplace.intent import sign_intent_legacy

        sk = SigningKey.generate()
        did = "did:web:maxiaworld.app:agent:test-003"

        envelope = sign_intent_legacy("swap", {"from": "USDC"}, sk.encode().hex(), did, expires_s=300)
        expires_str = envelope["expires"]

        expires_dt = datetime.fromisoformat(expires_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)

        # Expires should be within 300s (+/- 5s tolerance for test execution)
        diff = (expires_dt - now).total_seconds()
        assert 290 <= diff <= 310, f"Expiry should be ~300s from now, got {diff}s"

    def test_legacy_intent_signature_valid(self):
        """A signed legacy intent can be verified with the matching public key."""
        from nacl.signing import SigningKey, VerifyKey
        import base58

        from marketplace.intent import sign_intent_legacy

        sk = SigningKey.generate()
        pk = sk.verify_key
        did = "did:web:maxiaworld.app:agent:test-004"

        envelope = sign_intent_legacy(
            action="gpu_rent",
            params={"tier": "h100", "hours": 2},
            private_key_hex=sk.encode().hex(),
            did=did,
        )

        # Manually verify signature
        payload = json.dumps({
            "action": envelope["action"], "did": envelope["did"],
            "expires": envelope["expires"], "nonce": envelope["nonce"],
            "params": envelope["params"], "v": envelope["v"],
        }, sort_keys=True, separators=(",", ":"))

        sig_bytes = base58.b58decode(envelope["sig"])
        # Should not raise
        pk.verify(payload.encode(), sig_bytes)

    def test_legacy_intent_bad_signature_rejected(self):
        """A forged signature fails verification."""
        from nacl.signing import SigningKey, VerifyKey
        from nacl.exceptions import BadSignatureError
        import base58

        from marketplace.intent import sign_intent_legacy

        sk = SigningKey.generate()
        sk2 = SigningKey.generate()  # different key
        pk2 = sk2.verify_key
        did = "did:web:maxiaworld.app:agent:test-005"

        envelope = sign_intent_legacy(
            action="swap",
            params={"from": "USDC", "to": "SOL"},
            private_key_hex=sk.encode().hex(),
            did=did,
        )

        payload = json.dumps({
            "action": envelope["action"], "did": envelope["did"],
            "expires": envelope["expires"], "nonce": envelope["nonce"],
            "params": envelope["params"], "v": envelope["v"],
        }, sort_keys=True, separators=(",", ":"))

        sig_bytes = base58.b58decode(envelope["sig"])
        with pytest.raises(BadSignatureError):
            pk2.verify(payload.encode(), sig_bytes)

    @pytest.mark.asyncio
    async def test_verify_legacy_intent_expired(self):
        """verify_intent_legacy rejects an expired intent."""
        from marketplace.intent import verify_intent_legacy

        expired_intent = {
            "v": 1,
            "did": "did:web:maxiaworld.app:agent:expired",
            "action": "swap",
            "params": {"from": "USDC"},
            "nonce": "abc123",
            "expires": "2020-01-01T00:00:00Z",
            "sig": "fakesig",
        }

        result = await verify_intent_legacy(expired_intent, "fakepub")
        assert result["valid"] is False
        assert "Expired" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_verify_legacy_intent_missing_field(self):
        """verify_intent_legacy rejects an intent missing required fields."""
        from marketplace.intent import verify_intent_legacy

        incomplete_intent = {
            "v": 1,
            "did": "did:web:maxiaworld.app:agent:test",
            "action": "swap",
            # missing: params, nonce, expires, sig
        }

        result = await verify_intent_legacy(incomplete_intent, "fakepub")
        assert result["valid"] is False
        assert "Missing" in result.get("error", "")


# ═══════════════════════════════════════════════════════════════════════════════
#  TestMarketplaceDiscovery — ASGI transport tests for discovery endpoints
# ═══════════════════════════════════════════════════════════════════════════════

class TestMarketplaceDiscovery:
    """Test marketplace discovery and service listing endpoints via ASGI."""

    @pytest.mark.asyncio
    async def test_discover_services(self):
        """GET /api/public/discover returns 200 with services list."""
        from httpx import AsyncClient, ASGITransport
        from main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/public/discover")
        assert resp.status_code == 200
        data = resp.json()
        assert "agents" in data or "results_count" in data

    @pytest.mark.asyncio
    async def test_discover_returns_categories(self):
        """Discovery response includes leaderboard (grouped by type/category)."""
        from httpx import AsyncClient, ASGITransport
        from main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/public/discover")
        assert resp.status_code == 200
        data = resp.json()
        # discover returns a leaderboard dict keyed by service type
        assert "leaderboard" in data
        assert isinstance(data["leaderboard"], dict)

    @pytest.mark.asyncio
    async def test_marketplace_stats(self):
        """GET /api/public/marketplace-stats returns 200."""
        from httpx import AsyncClient, ASGITransport
        from main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/public/marketplace-stats")
        assert resp.status_code == 200
        data = resp.json()
        # Should contain agent/service/transaction counts
        assert isinstance(data, dict)

    @pytest.mark.asyncio
    async def test_service_templates(self):
        """GET /api/public/services returns 200 with service list."""
        from httpx import AsyncClient, ASGITransport
        from main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/public/services")
        assert resp.status_code == 200
        data = resp.json()
        assert "services" in data
        assert isinstance(data["services"], list)

    @pytest.mark.asyncio
    async def test_services_have_required_fields(self):
        """Each service in /services has name, description, price fields."""
        from httpx import AsyncClient, ASGITransport
        from main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/public/discover")
        assert resp.status_code == 200
        data = resp.json()
        services = data.get("agents", [])
        # At least the MAXIA native services should be present
        assert len(services) > 0, "Discovery should return at least MAXIA native services"
        for svc in services:
            assert "name" in svc, f"Service missing 'name': {svc}"
            assert "description" in svc, f"Service missing 'description': {svc}"
            assert "price_usdc" in svc, f"Service missing 'price_usdc': {svc}"

    @pytest.mark.asyncio
    async def test_mcp_manifest(self):
        """GET /mcp/manifest returns 200 with tools list."""
        from httpx import AsyncClient, ASGITransport
        from main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/mcp/manifest")
        assert resp.status_code == 200
        data = resp.json()
        assert "tools" in data
        assert isinstance(data["tools"], list)
        assert len(data["tools"]) > 0
        # Each tool should have a name and description
        for tool in data["tools"][:5]:  # check first 5
            assert "name" in tool
            assert "description" in tool


# ═══════════════════════════════════════════════════════════════════════════════
#  TestMarketplaceShared — Shared utilities from public_api_shared
# ═══════════════════════════════════════════════════════════════════════════════

class TestMarketplaceShared:
    """Test shared marketplace helpers and configuration."""

    def test_validate_solana_address_valid(self):
        """Valid Solana addresses pass validation."""
        from marketplace.public_api_shared import _validate_solana_address
        # Should not raise for valid addresses
        _validate_solana_address("11111111111111111111111111111111")  # system program
        _validate_solana_address("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v")  # USDC mint

    def test_validate_solana_address_invalid(self):
        """Empty and invalid addresses are rejected."""
        from marketplace.public_api_shared import _validate_solana_address
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            _validate_solana_address("")
        assert exc_info.value.status_code == 400

        with pytest.raises(HTTPException) as exc_info:
            _validate_solana_address("not-a-wallet!")
        assert exc_info.value.status_code == 400

        with pytest.raises(HTTPException) as exc_info:
            _validate_solana_address("short")
        assert exc_info.value.status_code == 400

    def test_commission_calculation(self):
        """Commission tiers return correct basis points."""
        from core.config import get_commission_bps, get_commission_tier_name

        # BRONZE: < $500 -> 150 bps (1.5%)
        assert get_commission_bps(0) == 150
        assert get_commission_bps(100) == 150
        assert get_commission_tier_name(100) == "BRONZE"

        # GOLD: $500-$5000 -> 50 bps (0.5%)
        assert get_commission_bps(500) == 50
        assert get_commission_bps(2000) == 50
        assert get_commission_tier_name(500) == "GOLD"

        # WHALE: > $5000 -> 10 bps (0.1%)
        assert get_commission_bps(5000) == 10
        assert get_commission_bps(50000) == 10
        assert get_commission_tier_name(5000) == "WHALE"

    def test_sandbox_mode_active(self):
        """SANDBOX_MODE is detected as true in test environment."""
        from marketplace.public_api_shared import SANDBOX_MODE
        assert SANDBOX_MODE is True, "SANDBOX_MODE should be True when env SANDBOX_MODE=true"

    def test_api_response_format(self):
        """Test that safe_float enforces numeric validation (envelope pattern)."""
        from marketplace.public_api_shared import _safe_float
        from fastapi import HTTPException

        # Valid conversions
        assert _safe_float(10, "amount") == 10.0
        assert _safe_float("3.14", "price") == 3.14
        assert _safe_float(None, "val", default=0.0) == 0.0

        # NaN rejected
        with pytest.raises(HTTPException) as exc_info:
            _safe_float(float("nan"), "amount")
        assert exc_info.value.status_code == 400

        # Infinity rejected
        with pytest.raises(HTTPException) as exc_info:
            _safe_float(float("inf"), "amount")
        assert exc_info.value.status_code == 400

        # Non-numeric rejected
        with pytest.raises(HTTPException) as exc_info:
            _safe_float("not_a_number", "amount")
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_services_endpoint(self):
        """GET /api/public/services returns 200 with service data."""
        from httpx import AsyncClient, ASGITransport
        from main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/public/services")
        assert resp.status_code == 200
        data = resp.json()
        assert "services" in data
        assert "total" in data
        assert isinstance(data["total"], int)

    def test_compute_service_hash_consistent(self):
        """Service hash is deterministic for the same inputs."""
        from marketplace.public_api_shared import _compute_service_hash

        h1 = _compute_service_hash("My Service", "Does things", "https://example.com")
        h2 = _compute_service_hash("My Service", "Does things", "https://example.com")
        assert h1 == h2

        # Different inputs produce different hashes
        h3 = _compute_service_hash("Other Service", "Does things", "https://example.com")
        assert h1 != h3

    def test_compute_service_hash_normalizes_case(self):
        """Service hash normalizes case and whitespace."""
        from marketplace.public_api_shared import _compute_service_hash

        h1 = _compute_service_hash("My Service", "Does things", "https://example.com")
        h2 = _compute_service_hash("MY SERVICE", "DOES THINGS", "HTTPS://EXAMPLE.COM")
        # Both should be the same after normalization (lowercase + strip)
        assert h1 == h2
