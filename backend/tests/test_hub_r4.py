"""Tests TDD pour MAXIA Hub R4 — Boost registres externes (Agentverse + ElizaOS).

Ordre TDD : RED avant implémentation.
"""
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from hub.hub_r4 import (  # noqa — pas encore créé
    ExternalPresence,
    ExternalRegistryChecker,
    compute_r4_boost,
    apply_r4_boost,
    r4_router,
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
def checker():
    return ExternalRegistryChecker()


# ─── ExternalPresence dataclass ───────────────────────────────────────────────

class TestExternalPresence:
    def test_has_required_fields(self):
        p = ExternalPresence(source="agentverse", external_id="agent1",
                             present=True, first_seen_days=90)
        assert p.source == "agentverse"
        assert p.present is True
        assert p.first_seen_days == 90
        assert p.error is None

    def test_error_field_optional(self):
        p = ExternalPresence(source="elizaos", external_id="e1",
                             present=False, first_seen_days=0, error="timeout")
        assert p.error == "timeout"

    def test_absent_agent_valid(self):
        p = ExternalPresence(source="agentverse", external_id="ghost",
                             present=False, first_seen_days=0)
        assert p.present is False
        assert p.error is None


# ─── check_agentverse ─────────────────────────────────────────────────────────

class TestCheckAgentverse:
    @pytest.mark.asyncio
    async def test_present_agent_returns_true(self, checker):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=make_http_response(200, {
            "address": "agent1abc", "name": "TestBot"
        }))
        p = await checker.check_agentverse("agent1abc", client, first_seen_days=120)
        assert p.present is True
        assert p.source == "agentverse"
        assert p.first_seen_days == 120

    @pytest.mark.asyncio
    async def test_absent_agent_returns_false(self, checker):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=make_http_response(404))
        p = await checker.check_agentverse("ghost", client)
        assert p.present is False
        assert p.error is None

    @pytest.mark.asyncio
    async def test_http_error_returns_error_presence(self, checker):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=httpx.HTTPError("timeout"))
        p = await checker.check_agentverse("agent1", client)
        assert p.present is False
        assert p.error is not None

    @pytest.mark.asyncio
    async def test_request_url_contains_external_id(self, checker):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=make_http_response(200, {"address": "myagent"}))
        await checker.check_agentverse("myagent", client)
        url = client.get.call_args[0][0]
        assert "myagent" in url


# ─── check_elizaos ────────────────────────────────────────────────────────────

class TestCheckElizaos:
    @pytest.mark.asyncio
    async def test_present_agent_found_by_id(self, checker):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=make_http_response(200, [
            {"id": "eliza-001", "name": "AlphaBot"},
            {"id": "eliza-002", "name": "BetaBot"},
        ]))
        p = await checker.check_elizaos("eliza-001", client, first_seen_days=60)
        assert p.present is True
        assert p.source == "elizaos"
        assert p.first_seen_days == 60

    @pytest.mark.asyncio
    async def test_absent_agent_returns_false(self, checker):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=make_http_response(200, [
            {"id": "eliza-999", "name": "Other"},
        ]))
        p = await checker.check_elizaos("ghost-id", client)
        assert p.present is False

    @pytest.mark.asyncio
    async def test_http_error_returns_error_presence(self, checker):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=httpx.HTTPError("refused"))
        p = await checker.check_elizaos("e1", client)
        assert p.error is not None

    @pytest.mark.asyncio
    async def test_non_200_returns_error_presence(self, checker):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=make_http_response(503))
        p = await checker.check_elizaos("e1", client)
        assert p.error is not None

    @pytest.mark.asyncio
    async def test_found_by_name_fallback(self, checker):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=make_http_response(200, [
            {"name": "TargetBot"},
        ]))
        p = await checker.check_elizaos("TargetBot", client)
        assert p.present is True


# ─── compute_r4_boost ──────────────────────────────────────────────────────────

class TestComputeR4Boost:
    def _p(self, source="agentverse", present=True, days=0, error=None):
        return ExternalPresence(source=source, external_id="x",
                                present=present, first_seen_days=days, error=error)

    def test_no_presences_gives_zero(self):
        assert compute_r4_boost([]) == 0.0

    def test_absent_gives_zero(self):
        assert compute_r4_boost([self._p(present=False)]) == 0.0

    def test_present_with_zero_age_gives_presence_bonus(self):
        boost = compute_r4_boost([self._p(days=0)])
        assert boost > 0.0
        assert boost == pytest.approx(1.0, abs=0.01)

    def test_present_with_full_seniority_gives_max_per_registry(self):
        # days=120 → max seniority per registry
        boost = compute_r4_boost([self._p(days=120)])
        assert boost == pytest.approx(2.5, abs=0.01)

    def test_two_registries_max_is_5(self):
        presences = [
            self._p("agentverse", days=120),
            self._p("elizaos", days=120),
        ]
        boost = compute_r4_boost(presences)
        assert boost == pytest.approx(5.0, abs=0.01)

    def test_boost_capped_at_5(self):
        presences = [self._p(days=9999)] * 10
        assert compute_r4_boost(presences) <= 5.0

    def test_seniority_increases_boost(self):
        low = compute_r4_boost([self._p(days=10)])
        high = compute_r4_boost([self._p(days=100)])
        assert low < high

    def test_error_presence_contributes_nothing(self):
        assert compute_r4_boost([self._p(error="timeout")]) == 0.0

    def test_boost_is_float(self):
        assert isinstance(compute_r4_boost([self._p(days=30)]), float)


# ─── apply_r4_boost ───────────────────────────────────────────────────────────

class TestApplyR4Boost:
    def _hub_row(self, hub_id="hub1"):
        return {"hub_id": hub_id}

    @pytest.mark.asyncio
    async def test_stores_presence_and_updates_boost(self, mock_db):
        mock_db._fetchone = AsyncMock(side_effect=[
            self._hub_row(),          # hub agent
            None,                     # agentverse first_seen (not in scout)
            None,                     # elizaos first_seen (not in scout)
        ])
        client = AsyncMock(spec=httpx.AsyncClient)
        with patch("hub.hub_r4.ExternalRegistryChecker") as MockChecker:
            mc = MockChecker.return_value
            mc.check_agentverse = AsyncMock(return_value=ExternalPresence(
                source="agentverse", external_id="av1", present=True, first_seen_days=90))
            mc.check_elizaos = AsyncMock(return_value=ExternalPresence(
                source="elizaos", external_id="e1", present=False, first_seen_days=0))
            result = await apply_r4_boost(mock_db, "hub1",
                                          agentverse_id="av1", elizaos_id="e1",
                                          http_client=client)
        assert "boost" in result
        assert result["boost"] >= 0
        assert mock_db.raw_execute.call_count >= 2  # 2 UPSERT + 1 UPDATE

    @pytest.mark.asyncio
    async def test_unknown_hub_raises_404(self, mock_db):
        mock_db._fetchone = AsyncMock(return_value=None)
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await apply_r4_boost(mock_db, "ghost", "av1", "e1", AsyncMock())
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_first_seen_from_scout_results(self, mock_db):
        now = int(time.time())
        days_ago = 45
        mock_db._fetchone = AsyncMock(side_effect=[
            self._hub_row(),
            {"discovered_at": now - 86400 * days_ago},  # scout first_seen agentverse
            None,                                         # elizaos not scouted
        ])
        client = AsyncMock(spec=httpx.AsyncClient)
        with patch("hub.hub_r4.ExternalRegistryChecker") as MockChecker:
            mc = MockChecker.return_value
            mc.check_agentverse = AsyncMock(return_value=ExternalPresence(
                source="agentverse", external_id="av1",
                present=True, first_seen_days=days_ago))
            mc.check_elizaos = AsyncMock(return_value=ExternalPresence(
                source="elizaos", external_id="e1", present=False, first_seen_days=0))
            result = await apply_r4_boost(mock_db, "hub1", "av1", "e1", client)
        # first_seen transmis au checker = days_ago
        assert mc.check_agentverse.call_args[1].get("first_seen_days", None) == days_ago \
            or mc.check_agentverse.call_args[0][2] == days_ago

    @pytest.mark.asyncio
    async def test_result_contains_required_fields(self, mock_db):
        mock_db._fetchone = AsyncMock(side_effect=[
            self._hub_row(), None, None,
        ])
        client = AsyncMock(spec=httpx.AsyncClient)
        with patch("hub.hub_r4.ExternalRegistryChecker") as MockChecker:
            mc = MockChecker.return_value
            mc.check_agentverse = AsyncMock(return_value=ExternalPresence(
                source="agentverse", external_id="av1", present=True, first_seen_days=30))
            mc.check_elizaos = AsyncMock(return_value=ExternalPresence(
                source="elizaos", external_id="e1", present=True, first_seen_days=60))
            result = await apply_r4_boost(mock_db, "hub1", "av1", "e1", client)
        for key in ("hub_id", "boost", "agentverse_present", "elizaos_present"):
            assert key in result

    @pytest.mark.asyncio
    async def test_both_ids_optional_none_skips_check(self, mock_db):
        mock_db._fetchone = AsyncMock(return_value=self._hub_row())
        with patch("hub.hub_r4.ExternalRegistryChecker") as MockChecker:
            mc = MockChecker.return_value
            mc.check_agentverse = AsyncMock()
            mc.check_elizaos = AsyncMock()
            result = await apply_r4_boost(mock_db, "hub1",
                                          agentverse_id=None, elizaos_id=None,
                                          http_client=AsyncMock())
        mc.check_agentverse.assert_not_called()
        mc.check_elizaos.assert_not_called()
        assert result["boost"] == 0.0


# ─── Endpoints ────────────────────────────────────────────────────────────────

class TestR4Endpoints:
    def _app(self):
        from fastapi import FastAPI
        app = FastAPI()
        app.include_router(r4_router)
        return app

    def test_post_link_endpoint_exists(self):
        from fastapi.testclient import TestClient
        resp = TestClient(self._app(), raise_server_exceptions=False).post(
            "/api/hub/r4/link",
            json={"hub_id": "h1", "agentverse_id": "av1", "elizaos_id": "e1"}
        )
        assert resp.status_code != 404

    def test_get_r4_detail_endpoint_exists(self):
        from fastapi.testclient import TestClient
        resp = TestClient(self._app(), raise_server_exceptions=False).get("/api/hub/r4/hub1")
        assert resp.status_code != 404
