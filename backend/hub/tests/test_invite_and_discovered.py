"""TDD — Fix invitations (toujours enregistrer) + endpoint discovered agents.
Run: pytest hub/tests/test_invite_and_discovered.py -v
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from hub.hub_invite import HubInviter


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _mock_response(status: int):
    r = MagicMock()
    r.status_code = status
    return r


def _mock_db():
    db = AsyncMock()
    db.raw_execute = AsyncMock()
    db._fetchone = AsyncMock(return_value=None)
    return db


# ─── Fix invitation : toujours enregistrer ────────────────────────────────────

class TestSendA2aInviteAlwaysRecords:

    @pytest.mark.asyncio
    async def test_records_as_sent_on_200(self):
        """HTTP 200 → status='sent', retourne True."""
        client = AsyncMock()
        client.post = AsyncMock(return_value=_mock_response(200))
        db = _mock_db()
        result = await HubInviter().send_a2a_invite(
            client, "https://agent.test/", "Agent", "scout123", db=db
        )
        assert result is True
        db.raw_execute.assert_called_once()
        _, call_params = db.raw_execute.call_args.args
        assert "sent" in call_params

    @pytest.mark.asyncio
    async def test_records_as_attempted_on_404(self):
        """HTTP 404 → status='attempted', retourne True (tentative comptabilisée)."""
        client = AsyncMock()
        client.post = AsyncMock(return_value=_mock_response(404))
        db = _mock_db()
        result = await HubInviter().send_a2a_invite(
            client, "https://agent.test/", "Agent", "scout123", db=db
        )
        assert result is True
        db.raw_execute.assert_called_once()
        _, call_params = db.raw_execute.call_args.args
        assert "attempted" in call_params

    @pytest.mark.asyncio
    async def test_records_as_attempted_on_405(self):
        """HTTP 405 (MCP ne comprend pas JSON-RPC) → toujours enregistré."""
        client = AsyncMock()
        client.post = AsyncMock(return_value=_mock_response(405))
        db = _mock_db()
        result = await HubInviter().send_a2a_invite(
            client, "https://mcp.server/", "MCP Agent", "scoutabc", db=db
        )
        assert result is True
        db.raw_execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_false_on_network_exception(self):
        """Exception réseau → retourne False, aucune écriture DB."""
        client = AsyncMock()
        client.post = AsyncMock(side_effect=Exception("timeout"))
        db = _mock_db()
        result = await HubInviter().send_a2a_invite(
            client, "https://agent.test/", "Agent", "scout123", db=db
        )
        assert result is False
        db.raw_execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_db_no_crash_on_200(self):
        """db=None + réponse 200 → ne crash pas, retourne True."""
        client = AsyncMock()
        client.post = AsyncMock(return_value=_mock_response(200))
        result = await HubInviter().send_a2a_invite(
            client, "https://agent.test/", "Agent", "scout123", db=None
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_no_db_no_crash_on_404(self):
        """db=None + réponse 404 → ne crash pas, retourne True."""
        client = AsyncMock()
        client.post = AsyncMock(return_value=_mock_response(404))
        result = await HubInviter().send_a2a_invite(
            client, "https://agent.test/", "Agent", "scout123", db=None
        )
        assert result is True


# ─── Endpoint /api/hub/agents/discovered ──────────────────────────────────────

class TestDiscoveredEndpoint:

    @pytest.mark.asyncio
    async def test_returns_eligible_agents(self):
        """Retourne les agents éligibles du scout avec discovered=True."""
        from hub.hub_registry import list_discovered_agents
        mock_rows = [
            {"scout_id": "abc123", "name": "DeFi Agent", "endpoint": "https://x.test/",
             "framework": "mcp", "source": "mcp_registry",
             "description": "LLM inference API", "discovered_at": 1000},
        ]
        mock_db = AsyncMock()
        mock_db.raw_execute_fetchall = AsyncMock(return_value=mock_rows)

        with patch("hub.hub_registry.db", new=mock_db):
            result = await list_discovered_agents()

        assert len(result) == 1
        assert result[0]["discovered"] is True
        assert result[0]["name"] == "DeFi Agent"
        assert result[0]["framework"] == "mcp"

    @pytest.mark.asyncio
    async def test_discovered_field_present(self):
        """Chaque entrée contient id, did, name, framework, discovered."""
        from hub.hub_registry import list_discovered_agents
        mock_rows = [
            {"scout_id": "zz9876", "name": "Audit Bot", "endpoint": None,
             "framework": "elizaos", "source": "elizaos",
             "description": "code audit agent", "discovered_at": 2000},
        ]
        mock_db = AsyncMock()
        mock_db.raw_execute_fetchall = AsyncMock(return_value=mock_rows)

        with patch("hub.hub_registry.db", new=mock_db):
            result = await list_discovered_agents()

        assert result[0]["id"].startswith("scout:")
        assert result[0]["did"].startswith("did:maxia:scout:")
        assert result[0]["discovered"] is True

    @pytest.mark.asyncio
    async def test_empty_when_no_eligible(self):
        """Retourne liste vide si aucun agent éligible."""
        from hub.hub_registry import list_discovered_agents
        mock_db = AsyncMock()
        mock_db.raw_execute_fetchall = AsyncMock(return_value=[])

        with patch("hub.hub_registry.db", new=mock_db):
            result = await list_discovered_agents()

        assert result == []

    @pytest.mark.asyncio
    async def test_framework_filter_passes_to_query(self):
        """Le filtre framework est transmis à la requête DB."""
        from hub.hub_registry import list_discovered_agents
        mock_db = AsyncMock()
        mock_db.raw_execute_fetchall = AsyncMock(return_value=[])

        with patch("hub.hub_registry.db", new=mock_db):
            await list_discovered_agents(framework="mcp")

        call_args = mock_db.raw_execute_fetchall.call_args
        query_params = call_args.args[1]
        assert "mcp" in query_params
