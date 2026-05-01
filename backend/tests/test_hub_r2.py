"""Tests TDD pour MAXIA Hub R2 — Boost GitHub (.well-known/maxia.json + stats).

Ordre TDD : RED avant implémentation.
"""
import json
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from nacl.signing import SigningKey
import base58

from hub.hub_r2 import (  # noqa — pas encore créé
    GitHubActivity,
    GitHubFetcher,
    verify_well_known,
    compute_r2_boost,
    apply_r2_boost,
    r2_router,
)


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


def make_well_known(sk, hub_id: str, ts: int | None = None) -> dict:
    ts = ts or int(time.time())
    sig = base58.b58encode(sk.sign((hub_id + str(ts)).encode()).signature).decode()
    pk_b58 = base58.b58encode(bytes(sk.verify_key)).decode()
    return {"hub_id": hub_id, "public_key": pk_b58, "sig": sig, "timestamp": ts}


def make_http_response(status_code: int, body=None):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json = MagicMock(return_value=body or {})
    resp.text = json.dumps(body or {})
    return resp


@pytest.fixture
def mock_db():
    return make_mock_db()


@pytest.fixture
def keypair():
    return make_keypair()


@pytest.fixture
def fetcher():
    return GitHubFetcher()


# ─── GitHubActivity dataclass ─────────────────────────────────────────────────

class TestGitHubActivity:
    def test_has_required_fields(self):
        a = GitHubActivity(owner="alice", repo="my-agent", stars=10, forks=2, commits_90d=15)
        assert a.owner == "alice"
        assert a.stars == 10
        assert a.commits_90d == 15
        assert a.error is None

    def test_error_field_optional(self):
        a = GitHubActivity(owner="x", repo="y", stars=0, forks=0, commits_90d=0, error="rate limit")
        assert a.error == "rate limit"


# ─── verify_well_known ────────────────────────────────────────────────────────

class TestVerifyWellKnown:
    def test_valid_sig_and_hub_id_returns_true(self, keypair):
        sk, pk_b58 = keypair
        hub_id = "hub-test-001"
        wk = make_well_known(sk, hub_id)
        assert verify_well_known(wk, hub_id, pk_b58) is True

    def test_wrong_sig_returns_false(self, keypair):
        sk, pk_b58 = keypair
        other_sk = SigningKey.generate()
        hub_id = "hub-test-002"
        wk = make_well_known(other_sk, hub_id)  # signé avec une autre clé
        assert verify_well_known(wk, hub_id, pk_b58) is False

    def test_hub_id_mismatch_returns_false(self, keypair):
        sk, pk_b58 = keypair
        wk = make_well_known(sk, "hub-real")
        assert verify_well_known(wk, "hub-different", pk_b58) is False

    def test_missing_sig_field_returns_false(self, keypair):
        _, pk_b58 = keypair
        wk = {"hub_id": "hub-x", "timestamp": int(time.time())}  # pas de sig
        assert verify_well_known(wk, "hub-x", pk_b58) is False

    def test_expired_timestamp_returns_false(self, keypair):
        sk, pk_b58 = keypair
        hub_id = "hub-old"
        old_ts = int(time.time()) - 86400 * 8  # 8 jours — TTL max = 7j
        wk = make_well_known(sk, hub_id, ts=old_ts)
        assert verify_well_known(wk, hub_id, pk_b58) is False

    def test_recent_timestamp_accepted(self, keypair):
        sk, pk_b58 = keypair
        hub_id = "hub-fresh"
        recent_ts = int(time.time()) - 3600  # 1h — bien dans la fenêtre
        wk = make_well_known(sk, hub_id, ts=recent_ts)
        assert verify_well_known(wk, hub_id, pk_b58) is True


# ─── GitHubFetcher — well-known ────────────────────────────────────────────────

class TestFetchWellKnown:
    @pytest.mark.asyncio
    async def test_fetches_from_raw_githubusercontent(self, fetcher, keypair):
        sk, pk_b58 = keypair
        wk = make_well_known(sk, "hub-1")
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=make_http_response(200, wk))
        result = await fetcher.fetch_well_known("alice/my-agent", client)
        assert result is not None
        assert result["hub_id"] == "hub-1"
        called_url = client.get.call_args[0][0]
        assert "raw.githubusercontent.com" in called_url
        assert "alice/my-agent" in called_url
        assert ".well-known/maxia.json" in called_url

    @pytest.mark.asyncio
    async def test_http_error_returns_none(self, fetcher):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=httpx.HTTPError("timeout"))
        result = await fetcher.fetch_well_known("alice/repo", client)
        assert result is None

    @pytest.mark.asyncio
    async def test_non_200_returns_none(self, fetcher):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=make_http_response(404))
        result = await fetcher.fetch_well_known("alice/repo", client)
        assert result is None


# ─── GitHubFetcher — repo stats ───────────────────────────────────────────────

class TestFetchRepoStats:
    @pytest.mark.asyncio
    async def test_returns_stars_and_forks(self, fetcher):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=make_http_response(200, {
            "stargazers_count": 42, "forks_count": 8,
            "full_name": "alice/my-agent",
        }))
        activity = await fetcher.fetch_repo_stats("alice", "my-agent", client)
        assert activity.stars == 42
        assert activity.forks == 8
        assert activity.error is None

    @pytest.mark.asyncio
    async def test_http_error_returns_error_activity(self, fetcher):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=httpx.HTTPError("timeout"))
        activity = await fetcher.fetch_repo_stats("alice", "repo", client)
        assert activity.error is not None

    @pytest.mark.asyncio
    async def test_rate_limited_returns_error_activity(self, fetcher):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=make_http_response(403, {"message": "rate limit"}))
        activity = await fetcher.fetch_repo_stats("alice", "repo", client)
        assert activity.error is not None


# ─── GitHubFetcher — commits 90j ──────────────────────────────────────────────

class TestFetchCommits90d:
    @pytest.mark.asyncio
    async def test_returns_commit_count(self, fetcher):
        commits = [{"sha": f"abc{i}"} for i in range(23)]
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=make_http_response(200, commits))
        count = await fetcher.fetch_commits_90d("alice", "repo", client)
        assert count == 23

    @pytest.mark.asyncio
    async def test_request_includes_since_param(self, fetcher):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=make_http_response(200, []))
        await fetcher.fetch_commits_90d("alice", "repo", client)
        params = client.get.call_args[1].get("params", {})
        assert "since" in params

    @pytest.mark.asyncio
    async def test_error_returns_zero(self, fetcher):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=httpx.HTTPError("refused"))
        count = await fetcher.fetch_commits_90d("alice", "repo", client)
        assert count == 0


# ─── compute_r2_boost ──────────────────────────────────────────────────────────

class TestComputeR2Boost:
    def _act(self, stars=0, forks=0, commits_90d=0, error=None):
        return GitHubActivity(owner="a", repo="b", stars=stars,
                              forks=forks, commits_90d=commits_90d, error=error)

    def test_zero_activity_gives_zero_boost(self):
        assert compute_r2_boost(self._act()) == 0.0

    def test_high_activity_gives_max_boost(self):
        boost = compute_r2_boost(self._act(stars=200, forks=60, commits_90d=100))
        assert boost == pytest.approx(10.0, abs=0.01)

    def test_boost_capped_at_10(self):
        boost = compute_r2_boost(self._act(stars=99999, forks=99999, commits_90d=99999))
        assert boost <= 10.0

    def test_partial_activity_gives_partial_boost(self):
        low = compute_r2_boost(self._act(stars=5, forks=1, commits_90d=3))
        high = compute_r2_boost(self._act(stars=80, forks=20, commits_90d=40))
        assert low < high

    def test_error_activity_gives_zero_boost(self):
        assert compute_r2_boost(self._act(error="rate limit")) == 0.0

    def test_100_stars_30_forks_50_commits_near_max(self):
        boost = compute_r2_boost(self._act(stars=100, forks=30, commits_90d=50))
        assert boost >= 8.0

    def test_boost_is_float(self):
        assert isinstance(compute_r2_boost(self._act(stars=10)), float)


# ─── apply_r2_boost ───────────────────────────────────────────────────────────

class TestApplyR2Boost:
    def _hub_row(self, hub_id="hub1", pk="pk_b58"):
        return {"hub_id": hub_id, "public_key": pk}

    @pytest.mark.asyncio
    async def test_valid_flow_updates_score_r2_boost(self, mock_db, keypair):
        sk, pk_b58 = keypair
        hub_id = "hub-r2-1"
        wk = make_well_known(sk, hub_id)
        mock_db._fetchone = AsyncMock(return_value=self._hub_row(hub_id, pk_b58))
        client = AsyncMock(spec=httpx.AsyncClient)

        with patch("hub.hub_r2.GitHubFetcher") as MockFetcher:
            mf = MockFetcher.return_value
            mf.fetch_well_known = AsyncMock(return_value=wk)
            mf.fetch_repo_stats = AsyncMock(return_value=GitHubActivity(
                owner="alice", repo="repo", stars=50, forks=10, commits_90d=20
            ))
            mf.fetch_commits_90d = AsyncMock(return_value=20)
            result = await apply_r2_boost(mock_db, hub_id, "alice/repo", client)

        assert result["boost"] >= 0
        mock_db.raw_execute.assert_called()

    @pytest.mark.asyncio
    async def test_unknown_hub_id_raises_404(self, mock_db):
        mock_db._fetchone = AsyncMock(return_value=None)
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await apply_r2_boost(mock_db, "ghost", "alice/repo", AsyncMock())
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_well_known_not_found_raises_422(self, mock_db, keypair):
        _, pk_b58 = keypair
        mock_db._fetchone = AsyncMock(return_value=self._hub_row(pk=pk_b58))
        from fastapi import HTTPException
        client = AsyncMock(spec=httpx.AsyncClient)
        with patch("hub.hub_r2.GitHubFetcher") as MockFetcher:
            mf = MockFetcher.return_value
            mf.fetch_well_known = AsyncMock(return_value=None)
            with pytest.raises(HTTPException) as exc_info:
                await apply_r2_boost(mock_db, "hub1", "alice/repo", client)
        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_sig_mismatch_raises_401(self, mock_db, keypair):
        sk, pk_b58 = keypair
        other_sk = SigningKey.generate()
        hub_id = "hub-r2-2"
        bad_wk = make_well_known(other_sk, hub_id)
        mock_db._fetchone = AsyncMock(return_value=self._hub_row(hub_id, pk_b58))
        from fastapi import HTTPException
        client = AsyncMock(spec=httpx.AsyncClient)
        with patch("hub.hub_r2.GitHubFetcher") as MockFetcher:
            mf = MockFetcher.return_value
            mf.fetch_well_known = AsyncMock(return_value=bad_wk)
            with pytest.raises(HTTPException) as exc_info:
                await apply_r2_boost(mock_db, hub_id, "alice/repo", client)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_result_contains_required_fields(self, mock_db, keypair):
        sk, pk_b58 = keypair
        hub_id = "hub-r2-3"
        wk = make_well_known(sk, hub_id)
        mock_db._fetchone = AsyncMock(return_value=self._hub_row(hub_id, pk_b58))
        client = AsyncMock(spec=httpx.AsyncClient)
        with patch("hub.hub_r2.GitHubFetcher") as MockFetcher:
            mf = MockFetcher.return_value
            mf.fetch_well_known = AsyncMock(return_value=wk)
            mf.fetch_repo_stats = AsyncMock(return_value=GitHubActivity(
                owner="alice", repo="repo", stars=10, forks=2, commits_90d=5
            ))
            mf.fetch_commits_90d = AsyncMock(return_value=5)
            result = await apply_r2_boost(mock_db, hub_id, "alice/repo", client)
        for key in ("hub_id", "boost", "stars", "forks", "commits_90d", "github_repo"):
            assert key in result


# ─── Endpoints ────────────────────────────────────────────────────────────────

class TestR2Endpoints:
    def _app(self):
        from fastapi import FastAPI
        app = FastAPI()
        app.include_router(r2_router)
        return app

    def test_post_refresh_endpoint_exists(self):
        from fastapi.testclient import TestClient
        resp = TestClient(self._app(), raise_server_exceptions=False).post(
            "/api/hub/r2/refresh/hub1", params={"github_repo": "alice/repo"}
        )
        assert resp.status_code != 404

    def test_get_r2_detail_endpoint_exists(self):
        from fastapi.testclient import TestClient
        resp = TestClient(self._app(), raise_server_exceptions=False).get("/api/hub/r2/hub1")
        assert resp.status_code != 404
