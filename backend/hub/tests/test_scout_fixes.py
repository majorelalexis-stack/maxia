"""TDD — Scout fixes : Agentverse POST, ElizaOS new URL, MCP Registry source.
Run: pytest hub/tests/test_scout_fixes.py -v
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from hub.hub_scout import HubScout, _score_agent


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _mock_response(status: int, body: dict):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body
    return r


# ─── Fix 1 : Agentverse POST ─────────────────────────────────────────────────

class TestFetchAgentverse:

    @pytest.mark.asyncio
    async def test_uses_post_not_get(self):
        """Agentverse requiert POST, pas GET."""
        client = AsyncMock()
        client.post = AsyncMock(return_value=_mock_response(200, {"agents": []}))
        client.get = AsyncMock(side_effect=AssertionError("Should not use GET"))
        result = await HubScout().fetch_agentverse(client)
        assert client.post.called

    @pytest.mark.asyncio
    async def test_post_body_correct(self):
        client = AsyncMock()
        client.post = AsyncMock(return_value=_mock_response(200, {"agents": []}))
        await HubScout().fetch_agentverse(client)
        call_kwargs = client.post.call_args
        body = call_kwargs.kwargs.get("json") or (call_kwargs.args[1] if len(call_kwargs.args) > 1 else {})
        assert "cutoff" in body or "search_text" in body

    @pytest.mark.asyncio
    async def test_returns_agents(self):
        agent = {
            "address": "agent1abc123",
            "name": "DeFi Oracle Agent",
            "description": "Autonomous DeFi trading agent with price oracle integration",
            "endpoint": "https://agent.example.com",
        }
        client = AsyncMock()
        client.post = AsyncMock(return_value=_mock_response(200, {"agents": [agent]}))
        result = await HubScout().fetch_agentverse(client)
        assert len(result) == 1
        assert result[0]["source"] == "agentverse"
        assert result[0]["external_id"] == "agent1abc123"
        assert result[0]["framework"] == "fetchai"

    @pytest.mark.asyncio
    async def test_returns_empty_on_error(self):
        client = AsyncMock()
        client.post = AsyncMock(side_effect=Exception("timeout"))
        result = await HubScout().fetch_agentverse(client)
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_non_200(self):
        client = AsyncMock()
        client.post = AsyncMock(return_value=_mock_response(403, {}))
        result = await HubScout().fetch_agentverse(client)
        assert result == []


# ─── Fix 2 : ElizaOS nouvelle URL ────────────────────────────────────────────

class TestFetchElizaos:

    @pytest.mark.asyncio
    async def test_uses_new_url(self):
        client = AsyncMock()
        client.get = AsyncMock(return_value=_mock_response(200, {"registry": {}}))
        await HubScout().fetch_elizaos(client)
        called_url = client.get.call_args.args[0]
        assert "elizaos-plugins" in called_url or "raw.githubusercontent" in called_url

    @pytest.mark.asyncio
    async def test_parses_registry_dict_format(self):
        """Le nouveau format est {"registry": {"@plugin/name": {"git": {...}}}}"""
        registry_data = {
            "lastUpdatedAt": "2026-04-22T00:49:19.407Z",
            "registry": {
                "@eliza/plugin-defi": {
                    "description": "Autonomous DeFi trading and portfolio management plugin",
                    "git": {"repo": "elizaOS/plugin-defi"},
                },
                "@eliza/plugin-meme": {
                    "description": "Meme generation entertainment plugin",
                    "git": {"repo": "elizaOS/plugin-meme"},
                },
            },
        }
        client = AsyncMock()
        client.get = AsyncMock(return_value=_mock_response(200, registry_data))
        result = await HubScout().fetch_elizaos(client)
        assert len(result) >= 1
        assert all(r["source"] == "elizaos" for r in result)

    @pytest.mark.asyncio
    async def test_returns_empty_on_404(self):
        client = AsyncMock()
        client.get = AsyncMock(return_value=_mock_response(404, {}))
        result = await HubScout().fetch_elizaos(client)
        assert result == []


# ─── Fix 4 : MCP Registry officiel ──────────────────────────────────────────

class TestFetchMcpRegistry:

    @pytest.mark.asyncio
    async def test_returns_servers(self):
        body = {
            "servers": [
                {
                    "server": {
                        "name": "inference.sh/mcp",
                        "title": "inference.sh",
                        "description": "Run 150+ AI models — LLM inference API",
                        "remotes": [{"type": "streamable-http", "url": "https://api.inference.sh/mcp"}],
                    },
                    "_meta": {},
                }
            ],
            "nextCursor": None,
        }
        client = AsyncMock()
        client.get = AsyncMock(return_value=_mock_response(200, body))
        result = await HubScout().fetch_mcp_registry(client)
        assert len(result) == 1
        assert result[0]["source"] == "mcp_registry"
        assert result[0]["endpoint"] == "https://api.inference.sh/mcp"
        assert result[0]["framework"] == "mcp"

    @pytest.mark.asyncio
    async def test_handles_no_remotes(self):
        body = {
            "servers": [
                {
                    "server": {
                        "name": "no-remote/mcp",
                        "title": "No Remote",
                        "description": "MCP server without remote endpoint",
                        "remotes": [],
                    },
                    "_meta": {},
                }
            ],
        }
        client = AsyncMock()
        client.get = AsyncMock(return_value=_mock_response(200, body))
        result = await HubScout().fetch_mcp_registry(client)
        assert len(result) == 1
        assert result[0]["endpoint"] is None

    @pytest.mark.asyncio
    async def test_uses_correct_url(self):
        client = AsyncMock()
        client.get = AsyncMock(return_value=_mock_response(200, {"servers": []}))
        await HubScout().fetch_mcp_registry(client)
        called_url = client.get.call_args.args[0]
        assert "modelcontextprotocol.io" in called_url

    @pytest.mark.asyncio
    async def test_returns_empty_on_error(self):
        client = AsyncMock()
        client.get = AsyncMock(side_effect=Exception("network error"))
        result = await HubScout().fetch_mcp_registry(client)
        assert result == []


# ─── Scoring (non-régression) ─────────────────────────────────────────────────

class TestScoring:

    def test_defi_agent_eligible(self):
        assert _score_agent("DeFi Oracle", "Autonomous DeFi trading agent with price oracle") >= 2

    def test_meme_agent_rejected(self):
        assert _score_agent("Meme Bot", "Create viral meme content for entertainment") == 0

    def test_empty_description_rejected(self):
        assert _score_agent("Agent", "") == 0
