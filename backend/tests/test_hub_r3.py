"""Tests TDD pour MAXIA Hub R3 — EAS attestations Base mainnet.

Ordre TDD : RED avant implémentation.
"""
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from hub.hub_r3 import (  # noqa — pas encore créé
    EASAttestation,
    EASFetcher,
    verify_attestation,
    parse_attestation_score,
    compute_r3_boost,
    apply_r3_attestation,
    r3_router,
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


def make_eas_gql_response(uid="0xabc", recipient="0xWallet", attester="0xAttester",
                          schema_id="0xSchema", revoked=False,
                          ts=None, expiry=0, decoded_data=None):
    ts = ts or int(time.time())
    decoded_data = decoded_data or '[{"name":"score","type":"uint8","value":{"value":80}}]'
    return {
        "data": {
            "attestation": {
                "id": uid,
                "recipient": recipient,
                "attester": attester,
                "schemaId": schema_id,
                "revoked": revoked,
                "time": ts,
                "expirationTime": expiry,
                "decodedDataJson": decoded_data,
            }
        }
    }


@pytest.fixture
def mock_db():
    return make_mock_db()


@pytest.fixture
def fetcher():
    return EASFetcher()


# ─── EASAttestation dataclass ─────────────────────────────────────────────────

class TestEASAttestation:
    def test_has_required_fields(self):
        a = EASAttestation(
            uid="0xabc", recipient="0xR", attester="0xA",
            schema_id="0xS", revoked=False, time=1000,
            expiration_time=0, attestation_score=80,
        )
        assert a.uid == "0xabc"
        assert a.attestation_score == 80
        assert a.error is None

    def test_error_field_optional(self):
        a = EASAttestation(uid="0x", recipient="", attester="",
                           schema_id="", revoked=False, time=0,
                           expiration_time=0, attestation_score=0, error="not found")
        assert a.error == "not found"


# ─── verify_attestation ───────────────────────────────────────────────────────

class TestVerifyAttestation:
    def _att(self, **kw):
        defaults = dict(uid="0xabc", recipient="0xwallet", attester="0xatt",
                        schema_id="0xschema", revoked=False,
                        time=int(time.time()), expiration_time=0, attestation_score=80)
        defaults.update(kw)
        return EASAttestation(**defaults)

    def test_valid_attestation_passes(self):
        assert verify_attestation(self._att(), "0xwallet", "0xschema") is True

    def test_revoked_attestation_fails(self):
        assert verify_attestation(self._att(revoked=True), "0xwallet", "0xschema") is False

    def test_expired_attestation_fails(self):
        past = int(time.time()) - 3600
        assert verify_attestation(self._att(expiration_time=past), "0xwallet", "0xschema") is False

    def test_no_expiry_zero_always_valid(self):
        assert verify_attestation(self._att(expiration_time=0), "0xwallet", "0xschema") is True

    def test_recipient_mismatch_fails(self):
        assert verify_attestation(self._att(), "0xother", "0xschema") is False

    def test_recipient_case_insensitive(self):
        assert verify_attestation(self._att(recipient="0xABCD"), "0xabcd", "0xschema") is True

    def test_schema_mismatch_fails_when_schema_set(self):
        assert verify_attestation(self._att(), "0xwallet", "0xDIFFERENT") is False

    def test_empty_expected_schema_skips_schema_check(self):
        assert verify_attestation(self._att(schema_id="0xany"), "0xwallet", "") is True

    def test_error_attestation_fails(self):
        a = self._att()
        a.error = "rpc timeout"
        assert verify_attestation(a, "0xwallet", "0xschema") is False


# ─── parse_attestation_score ─────────────────────────────────────────────────

class TestParseAttestationScore:
    def test_extracts_score_from_decoded_data(self):
        data = '[{"name":"score","type":"uint8","value":{"value":75}}]'
        assert parse_attestation_score(data) == 75

    def test_score_at_root_value(self):
        data = '[{"name":"score","type":"uint8","value":90}]'
        assert parse_attestation_score(data) == 90

    def test_clamps_score_to_100(self):
        data = '[{"name":"score","type":"uint8","value":{"value":200}}]'
        assert parse_attestation_score(data) == 100

    def test_negative_score_returns_zero(self):
        data = '[{"name":"score","type":"uint8","value":{"value":-10}}]'
        assert parse_attestation_score(data) == 0

    def test_missing_score_field_returns_default(self):
        data = '[{"name":"comment","type":"string","value":"great"}]'
        assert parse_attestation_score(data) == 50

    def test_invalid_json_returns_default(self):
        assert parse_attestation_score("not json") == 50

    def test_empty_string_returns_default(self):
        assert parse_attestation_score("") == 50


# ─── EASFetcher ───────────────────────────────────────────────────────────────

class TestEASFetch:
    @pytest.mark.asyncio
    async def test_fetches_attestation_via_graphql(self, fetcher):
        uid = "0xdeadbeef"
        gql_resp = make_eas_gql_response(uid=uid)
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=make_http_response(200, gql_resp))
        att = await fetcher.fetch_attestation(uid, client)
        assert att is not None
        assert att.uid == uid
        assert att.error is None

    @pytest.mark.asyncio
    async def test_request_sends_graphql_query(self, fetcher):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=make_http_response(200,
                                make_eas_gql_response()))
        await fetcher.fetch_attestation("0xtest", client)
        payload = client.post.call_args[1]["json"]
        assert "query" in payload
        assert "0xtest" in str(payload.get("variables", {}))

    @pytest.mark.asyncio
    async def test_attestation_not_found_returns_error(self, fetcher):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=make_http_response(200,
                                {"data": {"attestation": None}}))
        att = await fetcher.fetch_attestation("0xmissing", client)
        assert att.error is not None

    @pytest.mark.asyncio
    async def test_http_error_returns_error_attestation(self, fetcher):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(side_effect=httpx.HTTPError("timeout"))
        att = await fetcher.fetch_attestation("0xuid", client)
        assert att.error is not None

    @pytest.mark.asyncio
    async def test_non_200_returns_error_attestation(self, fetcher):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=make_http_response(503))
        att = await fetcher.fetch_attestation("0xuid", client)
        assert att.error is not None

    @pytest.mark.asyncio
    async def test_revoked_flag_parsed(self, fetcher):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=make_http_response(200,
                                make_eas_gql_response(revoked=True)))
        att = await fetcher.fetch_attestation("0xuid", client)
        assert att.revoked is True

    @pytest.mark.asyncio
    async def test_score_decoded_from_data(self, fetcher):
        decoded = '[{"name":"score","type":"uint8","value":{"value":65}}]'
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=make_http_response(200,
                                make_eas_gql_response(decoded_data=decoded)))
        att = await fetcher.fetch_attestation("0xuid", client)
        assert att.attestation_score == 65


# ─── compute_r3_boost ──────────────────────────────────────────────────────────

class TestComputeR3Boost:
    def test_no_attestations_gives_zero(self):
        assert compute_r3_boost([]) == 0.0

    def test_single_perfect_attestation_from_top_agent(self):
        # score=100, attestor_hub_score=100 → weight=1.0, contrib=5.0
        boost = compute_r3_boost([(100, 100)])
        assert boost == pytest.approx(5.0, abs=0.01)

    def test_four_perfect_attestations_hit_cap(self):
        boost = compute_r3_boost([(100, 100)] * 4)
        assert boost == pytest.approx(20.0, abs=0.01)

    def test_boost_capped_at_20(self):
        boost = compute_r3_boost([(100, 100)] * 100)
        assert boost <= 20.0

    def test_low_attestor_score_reduces_contribution(self):
        low = compute_r3_boost([(100, 10)])
        high = compute_r3_boost([(100, 100)])
        assert low < high

    def test_zero_score_attestation_contributes_nothing(self):
        assert compute_r3_boost([(0, 100)]) == 0.0

    def test_anti_sybil_attestor_score_50_is_full_weight(self):
        at_50 = compute_r3_boost([(100, 50)])
        at_100 = compute_r3_boost([(100, 100)])
        assert at_50 == pytest.approx(at_100, abs=0.01)

    def test_boost_is_float(self):
        assert isinstance(compute_r3_boost([(80, 60)]), float)


# ─── apply_r3_attestation ─────────────────────────────────────────────────────

class TestApplyR3Attestation:
    def _hub_row(self, hub_id="hub1", wallet="0xwallet"):
        return {"hub_id": hub_id, "wallet": wallet}

    def _att(self, uid="0xuid", recipient="0xwallet", revoked=False, score=80):
        return EASAttestation(
            uid=uid, recipient=recipient, attester="0xattester",
            schema_id="0xschema", revoked=revoked,
            time=int(time.time()), expiration_time=0,
            attestation_score=score,
        )

    @pytest.mark.asyncio
    async def test_valid_attestation_stored_and_boost_updated(self, mock_db):
        mock_db._fetchone = AsyncMock(side_effect=[
            self._hub_row(),             # hub agent
            None,                        # attestor not in Hub → weight 0.1
            None,                        # attestation not yet stored
        ])
        mock_db.raw_execute_fetchall = AsyncMock(return_value=[
            {"attestation_score": 80, "attester_hub_score": 0.0}
        ])
        client = AsyncMock(spec=httpx.AsyncClient)
        with patch("hub.hub_r3.EASFetcher") as MockFetcher, \
             patch("hub.hub_r3._EAS_SCHEMA_ID", "0xschema"):
            mf = MockFetcher.return_value
            mf.fetch_attestation = AsyncMock(return_value=self._att())
            result = await apply_r3_attestation(mock_db, "hub1", "0xuid", client)
        assert "boost" in result
        assert result["boost"] >= 0
        assert mock_db.raw_execute.call_count >= 2  # INSERT + UPDATE

    @pytest.mark.asyncio
    async def test_unknown_hub_id_raises_404(self, mock_db):
        mock_db._fetchone = AsyncMock(return_value=None)
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await apply_r3_attestation(mock_db, "ghost", "0xuid", AsyncMock())
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_revoked_attestation_raises_422(self, mock_db):
        mock_db._fetchone = AsyncMock(return_value=self._hub_row())
        from fastapi import HTTPException
        client = AsyncMock(spec=httpx.AsyncClient)
        with patch("hub.hub_r3.EASFetcher") as MockFetcher:
            mf = MockFetcher.return_value
            mf.fetch_attestation = AsyncMock(return_value=self._att(revoked=True))
            with pytest.raises(HTTPException) as exc_info:
                await apply_r3_attestation(mock_db, "hub1", "0xuid", client)
        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_recipient_mismatch_raises_422(self, mock_db):
        mock_db._fetchone = AsyncMock(return_value=self._hub_row(wallet="0xmine"))
        from fastapi import HTTPException
        client = AsyncMock(spec=httpx.AsyncClient)
        with patch("hub.hub_r3.EASFetcher") as MockFetcher:
            mf = MockFetcher.return_value
            mf.fetch_attestation = AsyncMock(
                return_value=self._att(recipient="0xother")
            )
            with pytest.raises(HTTPException) as exc_info:
                await apply_r3_attestation(mock_db, "hub1", "0xuid", client)
        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_already_stored_attestation_raises_409(self, mock_db):
        mock_db._fetchone = AsyncMock(side_effect=[
            self._hub_row(),
            None,
            {"uid": "0xuid"},  # déjà stockée
        ])
        from fastapi import HTTPException
        client = AsyncMock(spec=httpx.AsyncClient)
        with patch("hub.hub_r3.EASFetcher") as MockFetcher:
            mf = MockFetcher.return_value
            mf.fetch_attestation = AsyncMock(return_value=self._att())
            with pytest.raises(HTTPException) as exc_info:
                await apply_r3_attestation(mock_db, "hub1", "0xuid", client)
        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_attestor_in_hub_gets_full_weight(self, mock_db):
        mock_db._fetchone = AsyncMock(side_effect=[
            self._hub_row(),
            {"hub_id": "att-hub", "score": 100},  # attestor Hub score=100
            None,                                   # pas encore stockée
        ])
        mock_db.raw_execute_fetchall = AsyncMock(return_value=[
            {"attestation_score": 100, "attester_hub_score": 100.0}
        ])
        client = AsyncMock(spec=httpx.AsyncClient)
        with patch("hub.hub_r3.EASFetcher") as MockFetcher, \
             patch("hub.hub_r3._EAS_SCHEMA_ID", "0xschema"):
            mf = MockFetcher.return_value
            mf.fetch_attestation = AsyncMock(return_value=self._att(score=100))
            result = await apply_r3_attestation(mock_db, "hub1", "0xuid", client)
        assert result["boost"] == pytest.approx(5.0, abs=0.01)

    @pytest.mark.asyncio
    async def test_result_contains_required_fields(self, mock_db):
        mock_db._fetchone = AsyncMock(side_effect=[
            self._hub_row(), None, None,
        ])
        mock_db.raw_execute_fetchall = AsyncMock(return_value=[
            {"attestation_score": 80, "attester_hub_score": 0.0}
        ])
        client = AsyncMock(spec=httpx.AsyncClient)
        with patch("hub.hub_r3.EASFetcher") as MockFetcher, \
             patch("hub.hub_r3._EAS_SCHEMA_ID", "0xschema"):
            mf = MockFetcher.return_value
            mf.fetch_attestation = AsyncMock(return_value=self._att())
            result = await apply_r3_attestation(mock_db, "hub1", "0xuid", client)
        for key in ("hub_id", "uid", "boost", "attestation_score", "attester_hub_score"):
            assert key in result


# ─── Endpoints ────────────────────────────────────────────────────────────────

class TestR3Endpoints:
    def _app(self):
        from fastapi import FastAPI
        app = FastAPI()
        app.include_router(r3_router)
        return app

    def test_post_attest_endpoint_exists(self):
        from fastapi.testclient import TestClient
        resp = TestClient(self._app(), raise_server_exceptions=False).post(
            "/api/hub/r3/attest", json={"hub_id": "h1", "uid": "0xabc"}
        )
        assert resp.status_code != 404

    def test_get_attestations_endpoint_exists(self):
        from fastapi.testclient import TestClient
        resp = TestClient(self._app(), raise_server_exceptions=False).get(
            "/api/hub/r3/hub1"
        )
        assert resp.status_code != 404
