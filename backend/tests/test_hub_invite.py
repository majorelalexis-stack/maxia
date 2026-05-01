"""Tests TDD pour MAXIA Hub R0b — Invitations + Claim ed25519.

Ordre TDD : RED avant implémentation.
"""
import json
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from unittest.mock import ANY

import httpx
from nacl.signing import SigningKey
import base58

from hub.hub_invite import HubInviter, invite_router  # noqa — pas encore créé


# ─── Helpers ─────────────────────────────────────────────────────────────────

def make_mock_db():
    mock = MagicMock()
    mock.raw_execute = AsyncMock(return_value=None)
    mock.raw_execute_fetchall = AsyncMock(return_value=[])
    mock._fetchone = AsyncMock(return_value=None)
    return mock


def make_keypair():
    sk = SigningKey.generate()
    pk_b58 = base58.b58encode(bytes(sk.verify_key)).decode()
    return sk, pk_b58


def sign_msg(sk: SigningKey, msg: str) -> str:
    return base58.b58encode(sk.sign(msg.encode()).signature).decode()


def make_http_response(status_code: int, body=None):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json = MagicMock(return_value=body or {})
    return resp


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_db():
    return make_mock_db()


@pytest.fixture
def inviter():
    return HubInviter()


@pytest.fixture
def keypair():
    return make_keypair()


# ─── A2A invite ───────────────────────────────────────────────────────────────

class TestA2AInvite:
    @pytest.mark.asyncio
    async def test_posts_to_agent_endpoint(self, inviter):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=make_http_response(200, {"result": "ok"}))
        result = await inviter.send_a2a_invite(client, "https://agent.example.com", "AgentX", "scout-1")
        assert result is True
        client.post.assert_called_once()
        call_args = client.post.call_args
        assert call_args[0][0] == "https://agent.example.com"

    @pytest.mark.asyncio
    async def test_payload_follows_jsonrpc_format(self, inviter):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=make_http_response(200))
        await inviter.send_a2a_invite(client, "https://ep.test", "Bot", "scout-2")
        payload = client.post.call_args[1]["json"]
        assert payload["jsonrpc"] == "2.0"
        assert payload["method"] == "tasks/send"
        assert "id" in payload
        assert "params" in payload
        msg = payload["params"]["message"]
        assert msg["role"] == "user"
        assert any("MAXIA" in p.get("text", "") for p in msg["parts"])

    @pytest.mark.asyncio
    async def test_http_error_returns_false(self, inviter):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(side_effect=httpx.HTTPError("timeout"))
        result = await inviter.send_a2a_invite(client, "https://ep.test", "Bot", "scout-3")
        assert result is False

    @pytest.mark.asyncio
    async def test_non_200_returns_false(self, inviter):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=make_http_response(503))
        result = await inviter.send_a2a_invite(client, "https://ep.test", "Bot", "scout-4")
        assert result is False

    @pytest.mark.asyncio
    async def test_successful_invite_records_in_db(self, inviter, mock_db):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=make_http_response(200))
        await inviter.send_a2a_invite(client, "https://ep.test", "Bot", "scout-5", db=mock_db)
        mock_db.raw_execute.assert_called_once()


# ─── Email invite ──────────────────────────────────────────────────────────────

class TestEmailInvite:
    @pytest.mark.asyncio
    async def test_sends_email_via_smtp_when_configured(self, inviter):
        with patch("hub.hub_invite._smtp_configured", return_value=True), \
             patch("hub.hub_invite._smtp_send") as mock_send:
            result = await inviter.send_email_invite(
                "dev@agent.example.com", "AgentX", "scout-10"
            )
        mock_send.assert_called_once()
        assert result is True

    @pytest.mark.asyncio
    async def test_skips_gracefully_when_smtp_not_configured(self, inviter):
        with patch("hub.hub_invite._smtp_configured", return_value=False):
            result = await inviter.send_email_invite(
                "dev@agent.example.com", "AgentX", "scout-11"
            )
        assert result is False

    @pytest.mark.asyncio
    async def test_email_rate_limit_10_per_day(self, inviter, mock_db):
        """Si 10 emails déjà envoyés aujourd'hui, renvoyer False."""
        today_start = int(time.time()) - (int(time.time()) % 86400)
        mock_db._fetchone = AsyncMock(return_value={"cnt": 10})
        with patch("hub.hub_invite._smtp_configured", return_value=True):
            result = await inviter.send_email_invite(
                "dev@agent.com", "Bot", "scout-12", db=mock_db
            )
        assert result is False

    @pytest.mark.asyncio
    async def test_email_allowed_when_under_limit(self, inviter, mock_db):
        mock_db._fetchone = AsyncMock(return_value={"cnt": 3})
        with patch("hub.hub_invite._smtp_configured", return_value=True), \
             patch("hub.hub_invite._smtp_send"):
            result = await inviter.send_email_invite(
                "dev@agent.com", "Bot", "scout-13", db=mock_db
            )
        assert result is True


# ─── Batch d'invitations ──────────────────────────────────────────────────────

class TestInviteBatch:
    def _scout_row(self, scout_id="s1", endpoint="https://ep.test", source="agentverse"):
        return {
            "scout_id": scout_id,
            "name": "TestAgent",
            "endpoint": endpoint,
            "source": source,
            "raw_data": "{}",
        }

    @pytest.mark.asyncio
    async def test_batch_only_invites_agents_with_endpoint(self, inviter, mock_db):
        rows = [
            self._scout_row("s1", "https://ep1.test"),
            self._scout_row("s2", None),           # pas d'endpoint → ignoré
        ]
        mock_db.raw_execute_fetchall = AsyncMock(return_value=rows)
        mock_db._fetchone = AsyncMock(return_value=None)  # pas encore invité

        with patch.object(inviter, "send_a2a_invite", AsyncMock(return_value=True)) as mock_send:
            stats = await inviter.run_invite_batch(mock_db, AsyncMock())

        assert mock_send.call_count == 1
        assert stats["a2a_sent"] == 1
        assert stats["skipped_no_endpoint"] == 1

    @pytest.mark.asyncio
    async def test_batch_skips_already_invited_agents(self, inviter, mock_db):
        rows = [self._scout_row("s3", "https://ep3.test")]
        mock_db.raw_execute_fetchall = AsyncMock(return_value=rows)
        mock_db._fetchone = AsyncMock(return_value={"invite_id": "existing"})

        with patch.object(inviter, "send_a2a_invite", AsyncMock(return_value=True)) as mock_send:
            stats = await inviter.run_invite_batch(mock_db, AsyncMock())

        mock_send.assert_not_called()
        assert stats["skipped_already_invited"] == 1

    @pytest.mark.asyncio
    async def test_batch_respects_a2a_limit(self, inviter, mock_db):
        rows = [self._scout_row(f"s{i}", f"https://ep{i}.test") for i in range(10)]
        mock_db.raw_execute_fetchall = AsyncMock(return_value=rows)
        mock_db._fetchone = AsyncMock(return_value=None)

        with patch.object(inviter, "send_a2a_invite", AsyncMock(return_value=True)) as mock_send:
            stats = await inviter.run_invite_batch(mock_db, AsyncMock(), a2a_limit=3)

        assert mock_send.call_count == 3
        assert stats["a2a_sent"] == 3

    @pytest.mark.asyncio
    async def test_batch_returns_required_stat_keys(self, inviter, mock_db):
        mock_db.raw_execute_fetchall = AsyncMock(return_value=[])
        stats = await inviter.run_invite_batch(mock_db, AsyncMock())
        for key in ("a2a_sent", "email_sent", "skipped_no_endpoint", "skipped_already_invited"):
            assert key in stats


# ─── Claim ed25519 ────────────────────────────────────────────────────────────

class TestScoutClaim:
    @pytest.mark.asyncio
    async def test_valid_sig_links_scout_to_hub(self, mock_db, keypair):
        sk, pk_b58 = keypair
        hub_id = "hub-abc"
        scout_id = "scout-xyz"
        sig = sign_msg(sk, hub_id + scout_id)

        mock_db._fetchone = AsyncMock(side_effect=[
            {"hub_id": hub_id, "public_key": pk_b58},  # hub agent trouvé
            {"scout_id": scout_id, "matched_hub_id": None},  # scout profile trouvé
        ])

        from hub.hub_invite import claim_scout_profile
        result = await claim_scout_profile(mock_db, hub_id, scout_id, sig)
        assert result["ok"] is True
        mock_db.raw_execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_invalid_sig_returns_401(self, mock_db, keypair):
        _, pk_b58 = keypair
        other_sk = SigningKey.generate()
        hub_id = "hub-def"
        scout_id = "scout-ghi"
        bad_sig = sign_msg(other_sk, hub_id + scout_id)

        mock_db._fetchone = AsyncMock(return_value={"hub_id": hub_id, "public_key": pk_b58})

        from fastapi import HTTPException
        from hub.hub_invite import claim_scout_profile
        with pytest.raises(HTTPException) as exc_info:
            await claim_scout_profile(mock_db, hub_id, scout_id, bad_sig)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_unknown_hub_id_returns_404(self, mock_db):
        mock_db._fetchone = AsyncMock(return_value=None)

        from fastapi import HTTPException
        from hub.hub_invite import claim_scout_profile
        with pytest.raises(HTTPException) as exc_info:
            await claim_scout_profile(mock_db, "ghost-hub", "scout-1", "badsig")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_unknown_scout_id_returns_404(self, mock_db, keypair):
        sk, pk_b58 = keypair
        hub_id = "hub-jkl"
        scout_id = "ghost-scout"
        sig = sign_msg(sk, hub_id + scout_id)

        mock_db._fetchone = AsyncMock(side_effect=[
            {"hub_id": hub_id, "public_key": pk_b58},  # hub trouvé
            None,                                        # scout introuvable
        ])

        from fastapi import HTTPException
        from hub.hub_invite import claim_scout_profile
        with pytest.raises(HTTPException) as exc_info:
            await claim_scout_profile(mock_db, hub_id, scout_id, sig)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_already_claimed_returns_409(self, mock_db, keypair):
        sk, pk_b58 = keypair
        hub_id = "hub-mno"
        scout_id = "scout-pqr"
        sig = sign_msg(sk, hub_id + scout_id)

        mock_db._fetchone = AsyncMock(side_effect=[
            {"hub_id": hub_id, "public_key": pk_b58},
            {"scout_id": scout_id, "matched_hub_id": "another-hub"},  # déjà réclamé
        ])

        from fastapi import HTTPException
        from hub.hub_invite import claim_scout_profile
        with pytest.raises(HTTPException) as exc_info:
            await claim_scout_profile(mock_db, hub_id, scout_id, sig)
        assert exc_info.value.status_code == 409


# ─── Endpoints (routing) ──────────────────────────────────────────────────────

class TestInviteEndpoints:
    def _app(self):
        from fastapi import FastAPI
        app = FastAPI()
        app.include_router(invite_router)
        return app

    def test_post_invite_run_exists(self):
        from fastapi.testclient import TestClient
        resp = TestClient(self._app(), raise_server_exceptions=False).post("/api/hub/invite/run")
        assert resp.status_code != 404

    def test_post_claim_exists(self):
        from fastapi.testclient import TestClient
        resp = TestClient(self._app(), raise_server_exceptions=False).post("/api/hub/invite/claim")
        assert resp.status_code != 404

    def test_get_stats_exists(self):
        from fastapi.testclient import TestClient
        resp = TestClient(self._app(), raise_server_exceptions=False).get("/api/hub/invite/stats")
        assert resp.status_code != 404
