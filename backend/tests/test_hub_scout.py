"""Tests TDD pour MAXIA Hub — R0 Scout actif (Agentverse + ElizaOS + GitHub).

Ordre TDD : ces tests sont écrits AVANT l'implémentation.
Ils doivent tous échouer (RED) au premier lancement.
"""
import json
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from hub.hub_scout import HubScout, scout_router  # noqa: E402  (pas encore créé)


# ─── Mock helpers ────────────────────────────────────────────────────────────

def make_mock_db():
    mock = MagicMock()
    mock.raw_execute = AsyncMock(return_value=None)
    mock.raw_execute_fetchall = AsyncMock(return_value=[])
    mock._fetchone = AsyncMock(return_value=None)
    return mock


def make_http_response(status_code: int, body):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json = MagicMock(return_value=body)
    return resp


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_db():
    return make_mock_db()


@pytest.fixture
def scout():
    return HubScout()


# ─── Agentverse fetch ────────────────────────────────────────────────────────

class TestAgentverseFetch:
    @pytest.mark.asyncio
    async def test_returns_normalized_agents(self, scout):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=make_http_response(200, {
            "agents": [
                {"address": "agent1abc", "name": "AlphaBot", "protocols": ["text"]},
                {"address": "agent2def", "name": "BetaBot"},
            ]
        }))
        result = await scout.fetch_agentverse(client)
        assert len(result) == 2
        assert result[0]["source"] == "agentverse"
        assert result[0]["external_id"] == "agent1abc"
        assert result[0]["name"] == "AlphaBot"

    @pytest.mark.asyncio
    async def test_http_error_returns_empty_list(self, scout):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=httpx.HTTPError("timeout"))
        result = await scout.fetch_agentverse(client)
        assert result == []

    @pytest.mark.asyncio
    async def test_non_200_returns_empty_list(self, scout):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=make_http_response(503, {}))
        result = await scout.fetch_agentverse(client)
        assert result == []

    @pytest.mark.asyncio
    async def test_agent_without_address_is_skipped(self, scout):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=make_http_response(200, {
            "agents": [
                {"name": "NoAddress"},         # pas d'address → ignoré
                {"address": "agent3ghi", "name": "ValidBot"},
            ]
        }))
        result = await scout.fetch_agentverse(client)
        assert len(result) == 1
        assert result[0]["external_id"] == "agent3ghi"


# ─── ElizaOS fetch ───────────────────────────────────────────────────────────

class TestElizaosFetch:
    @pytest.mark.asyncio
    async def test_returns_normalized_agents_from_list(self, scout):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=make_http_response(200, [
            {"id": "eliza-001", "name": "ElizaBot", "endpoint": "https://eliza.example.com"},
            {"id": "eliza-002", "name": "AgentX"},
        ]))
        result = await scout.fetch_elizaos(client)
        assert len(result) == 2
        assert result[0]["source"] == "elizaos"
        assert result[0]["external_id"] == "eliza-001"
        assert result[0]["endpoint"] == "https://eliza.example.com"

    @pytest.mark.asyncio
    async def test_http_error_returns_empty_list(self, scout):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=httpx.HTTPError("connection refused"))
        result = await scout.fetch_elizaos(client)
        assert result == []

    @pytest.mark.asyncio
    async def test_non_200_returns_empty_list(self, scout):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=make_http_response(404, {}))
        result = await scout.fetch_elizaos(client)
        assert result == []

    @pytest.mark.asyncio
    async def test_agent_without_id_and_name_is_skipped(self, scout):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=make_http_response(200, [
            {},                                                        # vide → ignoré
            {"id": "eliza-ok", "name": "Kept"},
        ]))
        result = await scout.fetch_elizaos(client)
        assert len(result) == 1
        assert result[0]["external_id"] == "eliza-ok"


# ─── GitHub fetch ────────────────────────────────────────────────────────────

class TestGitHubFetch:
    @pytest.mark.asyncio
    async def test_returns_ai_agent_repos(self, scout):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=make_http_response(200, {
            "total_count": 2,
            "items": [
                {"full_name": "user/my-agent", "description": "An AI agent",
                 "html_url": "https://github.com/user/my-agent",
                 "stargazers_count": 15, "forks_count": 3},
                {"full_name": "org/agent-v2", "description": "Another agent",
                 "html_url": "https://github.com/org/agent-v2",
                 "stargazers_count": 42, "forks_count": 10},
            ]
        }))
        result = await scout.fetch_github(client)
        assert len(result) == 2
        assert result[0]["source"] == "github"
        assert result[0]["external_id"] == "user/my-agent"
        assert result[0]["endpoint"] is None

    @pytest.mark.asyncio
    async def test_rate_limited_returns_empty_list(self, scout):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=make_http_response(403, {"message": "rate limit"}))
        result = await scout.fetch_github(client)
        assert result == []

    @pytest.mark.asyncio
    async def test_http_error_returns_empty_list(self, scout):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=httpx.HTTPError("network error"))
        result = await scout.fetch_github(client)
        assert result == []

    @pytest.mark.asyncio
    async def test_raw_data_contains_stars_and_url(self, scout):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=make_http_response(200, {
            "items": [
                {"full_name": "x/y", "description": None,
                 "html_url": "https://github.com/x/y",
                 "stargazers_count": 99, "forks_count": 5},
            ]
        }))
        result = await scout.fetch_github(client)
        raw = json.loads(result[0]["raw_data"])
        assert raw["stars"] == 99
        assert "github.com/x/y" in raw["url"]


# ─── Store results ───────────────────────────────────────────────────────────

class TestHubScoutStore:
    def _agent(self, source="agentverse", ext_id="agent1", name="A"):
        return {
            "source": source, "external_id": ext_id, "name": name,
            "endpoint": None, "framework": source, "description": None,
            "raw_data": "{}",
        }

    @pytest.mark.asyncio
    async def test_new_agent_is_stored(self, scout, mock_db):
        mock_db._fetchone = AsyncMock(return_value=None)
        found, new = await scout.store_results(mock_db, [self._agent()])
        assert found == 1
        assert new == 1
        mock_db.raw_execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_duplicate_is_not_stored_again(self, scout, mock_db):
        mock_db._fetchone = AsyncMock(return_value={"scout_id": "existing"})
        found, new = await scout.store_results(mock_db, [self._agent()])
        assert found == 1
        assert new == 0
        mock_db.raw_execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_stored_status_is_unverified(self, scout, mock_db):
        mock_db._fetchone = AsyncMock(return_value=None)
        captured = []
        async def capture(*args, **kwargs):
            captured.extend(args)
        mock_db.raw_execute = capture
        await scout.store_results(mock_db, [self._agent()])
        assert any("unverified" in str(a) for a in captured)

    @pytest.mark.asyncio
    async def test_mixed_new_and_duplicate_counts_correctly(self, scout, mock_db):
        call_count = 0
        async def fetchone_side(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return None if call_count == 1 else {"scout_id": "x"}

        mock_db._fetchone = fetchone_side
        agents = [self._agent(ext_id="a1"), self._agent(ext_id="a2")]
        found, new = await scout.store_results(mock_db, agents)
        assert found == 2
        assert new == 1

    @pytest.mark.asyncio
    async def test_empty_list_returns_zero_counts(self, scout, mock_db):
        found, new = await scout.store_results(mock_db, [])
        assert found == 0
        assert new == 0


# ─── Full run ────────────────────────────────────────────────────────────────

class TestHubScoutRun:
    @pytest.mark.asyncio
    async def test_run_calls_all_three_sources(self, scout, mock_db):
        client = AsyncMock(spec=httpx.AsyncClient)
        with patch.object(scout, "fetch_agentverse", AsyncMock(return_value=[])) as av, \
             patch.object(scout, "fetch_elizaos", AsyncMock(return_value=[])) as el, \
             patch.object(scout, "fetch_github", AsyncMock(return_value=[])) as gh, \
             patch.object(scout, "store_results", AsyncMock(return_value=(0, 0))):
            await scout.run(mock_db, client)
        av.assert_called_once()
        el.assert_called_once()
        gh.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_returns_stats_with_required_keys(self, scout, mock_db):
        client = AsyncMock(spec=httpx.AsyncClient)
        with patch.object(scout, "fetch_agentverse", AsyncMock(return_value=[])), \
             patch.object(scout, "fetch_elizaos", AsyncMock(return_value=[])), \
             patch.object(scout, "fetch_github", AsyncMock(return_value=[])), \
             patch.object(scout, "store_results", AsyncMock(return_value=(0, 0))):
            stats = await scout.run(mock_db, client)
        assert "agents_found" in stats
        assert "agents_new" in stats
        assert isinstance(stats["agents_found"], int)

    @pytest.mark.asyncio
    async def test_run_source_exception_does_not_crash(self, scout, mock_db):
        client = AsyncMock(spec=httpx.AsyncClient)
        with patch.object(scout, "fetch_agentverse", AsyncMock(side_effect=Exception("boom"))), \
             patch.object(scout, "fetch_elizaos", AsyncMock(return_value=[])), \
             patch.object(scout, "fetch_github", AsyncMock(return_value=[])), \
             patch.object(scout, "store_results", AsyncMock(return_value=(0, 0))):
            stats = await scout.run(mock_db, client)
        assert "agents_found" in stats

    @pytest.mark.asyncio
    async def test_run_aggregates_counts_from_all_sources(self, scout, mock_db):
        def _a(src, eid):
            return {"source": src, "external_id": eid, "name": "X",
                    "endpoint": None, "framework": src, "description": None, "raw_data": "{}"}

        client = AsyncMock(spec=httpx.AsyncClient)
        with patch.object(scout, "fetch_agentverse", AsyncMock(return_value=[_a("agentverse", "a1")])), \
             patch.object(scout, "fetch_elizaos", AsyncMock(return_value=[_a("elizaos", "e1"), _a("elizaos", "e2")])), \
             patch.object(scout, "fetch_github", AsyncMock(return_value=[])), \
             patch.object(scout, "store_results", AsyncMock(side_effect=[(1, 1), (2, 2)])):
            stats = await scout.run(mock_db, client)
        assert stats["agents_found"] == 3
        assert stats["agents_new"] == 3


# ─── Endpoints (routage uniquement — pas d'intégration DB) ───────────────────

class TestHubScoutEndpoints:
    def _app(self):
        from fastapi import FastAPI
        app = FastAPI()
        app.include_router(scout_router)
        return app

    def test_post_run_endpoint_exists(self):
        from fastapi.testclient import TestClient
        client = TestClient(self._app(), raise_server_exceptions=False)
        resp = client.post("/api/hub/scout/run")
        assert resp.status_code != 404

    def test_get_results_endpoint_exists(self):
        from fastapi.testclient import TestClient
        client = TestClient(self._app(), raise_server_exceptions=False)
        resp = client.get("/api/hub/scout/results")
        assert resp.status_code != 404

    def test_get_status_endpoint_exists(self):
        from fastapi.testclient import TestClient
        client = TestClient(self._app(), raise_server_exceptions=False)
        resp = client.get("/api/hub/scout/status")
        assert resp.status_code != 404
