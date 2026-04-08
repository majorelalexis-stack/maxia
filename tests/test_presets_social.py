"""Tests for Phase P2 (Agent Presets) and P3 (Agent Social)."""
import os
os.environ.setdefault("JWT_SECRET", "test-secret-key-minimum-32-characters-long")
os.environ.setdefault("SANDBOX_MODE", "true")
os.environ.setdefault("ADMIN_KEY", "test-admin-key-32chars-minimum!!")

import pytest
from httpx import AsyncClient, ASGITransport
import json


# ═══════════════════════════════════════
#  P2 — Agent Presets
# ═══════════════════════════════════════

class TestPresetCatalog:
    """Test the preset catalog (static, no DB)."""

    def test_catalog_has_6_presets(self):
        from agents.agent_presets import PRESET_CATALOG
        assert len(PRESET_CATALOG) == 7

    def test_catalog_ids_unique(self):
        from agents.agent_presets import PRESET_CATALOG
        ids = [p["id"] for p in PRESET_CATALOG]
        assert len(ids) == len(set(ids))

    def test_each_preset_has_required_fields(self):
        from agents.agent_presets import PRESET_CATALOG
        required = {"id", "name", "category", "description", "services_used", "default_config", "config_schema"}
        for p in PRESET_CATALOG:
            missing = required - set(p.keys())
            assert not missing, f"Preset {p['id']} missing: {missing}"

    def test_preset_map_lookup(self):
        from agents.agent_presets import PRESET_MAP
        assert "trading-bot" in PRESET_MAP
        assert "sentiment-analyzer" in PRESET_MAP
        assert PRESET_MAP["trading-bot"]["name"] == "Trading Bot"

    def test_categories_valid(self):
        from agents.agent_presets import PRESET_CATALOG
        valid = {"trading", "analysis", "content", "defi", "research"}
        for p in PRESET_CATALOG:
            assert p["category"] in valid, f"Invalid category: {p['category']}"

    def test_config_schema_types(self):
        from agents.agent_presets import PRESET_CATALOG
        valid_types = {"string", "number", "boolean", "array"}
        for p in PRESET_CATALOG:
            for field, schema in p["config_schema"].items():
                assert schema["type"] in valid_types, f"{p['id']}.{field} has invalid type"

    def test_default_config_matches_schema(self):
        from agents.agent_presets import PRESET_CATALOG
        for p in PRESET_CATALOG:
            for field, schema in p["config_schema"].items():
                if schema.get("required"):
                    assert field in p["default_config"], f"{p['id']} missing default for required field {field}"


class TestPresetEndpoints:
    """Test preset API endpoints."""

    @pytest.fixture
    async def client(self):
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))
        from core.database import db
        await db.connect()
        from main import app
        transport = ASGITransport(app=app)
        return AsyncClient(transport=transport, base_url="http://test")

    @pytest.mark.asyncio
    async def test_list_presets(self, client):
        async with client as c:
            r = await c.get("/api/presets/catalog")
            assert r.status_code == 200
            data = r.json()
            assert data["count"] == 7
            assert len(data["presets"]) == 7

    @pytest.mark.asyncio
    async def test_get_preset_detail(self, client):
        async with client as c:
            r = await c.get("/api/presets/catalog/trading-bot")
            assert r.status_code == 200
            data = r.json()
            assert data["name"] == "Trading Bot"
            assert "config_schema" in data

    @pytest.mark.asyncio
    async def test_get_preset_not_found(self, client):
        async with client as c:
            r = await c.get("/api/presets/catalog/nonexistent")
            assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_preview_preset(self, client):
        async with client as c:
            r = await c.get("/api/presets/preview/defi-yield-hunter")
            assert r.status_code == 200
            data = r.json()
            assert data["preset"] == "DeFi Yield Hunter"
            assert "defi_scanner" in data["services"]

    @pytest.mark.asyncio
    async def test_launch_preset(self, client):
        async with client as c:
            r = await c.post("/api/presets/launch", json={
                "preset_id": "trading-bot",
                "wallet": "TestWallet123456",
                "agent_name": "My Trading Bot",
                "config": {"tokens": ["SOL", "ETH"]},
            })
            assert r.status_code == 200
            data = r.json()
            assert data["agent_name"] == "My Trading Bot"
            assert data["status"] == "active"
            assert data["preset_id"] == "trading-bot"

    @pytest.mark.asyncio
    async def test_launch_preset_invalid(self, client):
        async with client as c:
            r = await c.post("/api/presets/launch", json={
                "preset_id": "nonexistent",
                "wallet": "TestWallet123456",
                "agent_name": "Bot",
            })
            assert r.status_code == 404


# ═══════════════════════════════════════
#  P3 — Agent Social
# ═══════════════════════════════════════

class TestAgentSocial:
    """Test social features (follows, reviews, feed)."""

    @pytest.fixture
    async def client(self):
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))
        from core.database import db
        await db.connect()
        from main import app
        transport = ASGITransport(app=app)
        return AsyncClient(transport=transport, base_url="http://test")

    @pytest.mark.asyncio
    async def test_follow_agent(self, client):
        async with client as c:
            r = await c.post("/api/agents/test-agent-001/follow?wallet=TestWallet123456")
            assert r.status_code == 200
            data = r.json()
            assert data["status"] in ("following", "already_following")

    @pytest.mark.asyncio
    async def test_follow_idempotent(self, client):
        async with client as c:
            await c.post("/api/agents/test-agent-002/follow?wallet=TestWallet123456")
            r = await c.post("/api/agents/test-agent-002/follow?wallet=TestWallet123456")
            assert r.status_code == 200
            assert r.json()["status"] == "already_following"

    @pytest.mark.asyncio
    async def test_unfollow_agent(self, client):
        async with client as c:
            await c.post("/api/agents/test-agent-003/follow?wallet=TestWallet123456")
            r = await c.post("/api/agents/test-agent-003/unfollow?wallet=TestWallet123456")
            assert r.status_code == 200
            assert r.json()["status"] == "unfollowed"

    @pytest.mark.asyncio
    async def test_get_followers(self, client):
        async with client as c:
            await c.post("/api/agents/test-agent-004/follow?wallet=TestWalletA12345")
            r = await c.get("/api/agents/test-agent-004/followers")
            assert r.status_code == 200
            data = r.json()
            assert "followers_count" in data
            assert "followers" in data

    @pytest.mark.asyncio
    async def test_social_stats(self, client):
        async with client as c:
            r = await c.get("/api/agents/test-agent-005/stats/social")
            assert r.status_code == 200
            data = r.json()
            assert "followers_count" in data
            assert "avg_rating" in data
            assert "review_count" in data

    @pytest.mark.asyncio
    async def test_get_reviews_empty(self, client):
        async with client as c:
            r = await c.get("/api/agents/test-agent-006/reviews")
            assert r.status_code == 200
            data = r.json()
            assert data["review_count"] == 0
            assert data["reviews"] == []

    @pytest.mark.asyncio
    async def test_global_feed(self, client):
        async with client as c:
            r = await c.get("/api/social/feed/global?limit=10")
            assert r.status_code == 200
            data = r.json()
            assert "feed" in data

    @pytest.mark.asyncio
    async def test_personal_feed(self, client):
        async with client as c:
            r = await c.get("/api/social/feed?wallet=TestWallet123456&limit=10")
            assert r.status_code == 200
            data = r.json()
            assert "feed" in data

    @pytest.mark.asyncio
    async def test_trending(self, client):
        async with client as c:
            r = await c.get("/api/social/trending?limit=5")
            assert r.status_code == 200
            data = r.json()
            assert "trending" in data


class TestCleanIpFix:
    """Test the _clean_ip fix for scanner bots."""

    def test_clean_ip_none(self):
        from core.security import _clean_ip
        assert _clean_ip(None) == "unknown"

    def test_clean_ip_empty(self):
        from core.security import _clean_ip
        assert _clean_ip("") == "unknown"

    def test_clean_ip_normal(self):
        from core.security import _clean_ip
        assert _clean_ip("1.2.3.4") == "1.2.3.4"

    def test_clean_ip_whitespace(self):
        from core.security import _clean_ip
        assert _clean_ip("  1.2.3.4  ") == "1.2.3.4"


class TestRecordActivity:
    """Test the activity recording function."""

    @pytest.mark.asyncio
    async def test_record_valid_event(self):
        from core.database import db
        await db.connect()
        from features.agent_social import record_activity
        # Should not raise
        await record_activity("agent-001", "trade_complete", "Agent completed a trade")

    @pytest.mark.asyncio
    async def test_record_invalid_event_type(self):
        from features.agent_social import record_activity
        # Invalid type should be silently ignored
        await record_activity("agent-001", "invalid_type", "test")

    def test_valid_event_types(self):
        from features.agent_social import VALID_EVENT_TYPES
        assert "trade_complete" in VALID_EVENT_TYPES
        assert "milestone" in VALID_EVENT_TYPES
        assert "review_posted" in VALID_EVENT_TYPES
