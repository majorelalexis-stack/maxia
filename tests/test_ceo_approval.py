"""Tests for CEO approval-result endpoint (P0 fix — plan master V7).

Contract (from local_ceo/missions/telegram_chat.py:128):
    POST /api/ceo/approval-result
    Body: {"action_id": str, "approved": bool, ...}

These tests are DB-free: the picoclaw_gateway._get_db dependency is replaced
with an in-memory fake that mirrors the raw_execute / raw_execute_fetchall /
raw_executescript surface used by the handlers.
"""
from __future__ import annotations

import os
import sys
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..", "backend")
sys.path.insert(0, BACKEND_DIR)


class _FakeDB:
    """Minimal async DB stub: stores ceo_approvals rows in a dict."""

    def __init__(self) -> None:
        self.approvals: dict[str, dict[str, Any]] = {}
        self.scripts: list[str] = []

    async def raw_executescript(self, sql: str) -> None:
        self.scripts.append(sql)

    async def raw_execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        sql_up = sql.upper()
        if sql_up.startswith("INSERT INTO CEO_APPROVALS"):
            action_id, approved, action_name, level, decided_at, decided_by = params
            self.approvals[action_id] = {
                "action_id": action_id,
                "approved": approved,
                "action_name": action_name,
                "level": level,
                "decided_at": decided_at,
                "decided_by": decided_by,
            }

    async def raw_execute_fetchall(
        self, sql: str, params: tuple[Any, ...] = ()
    ) -> list[dict[str, Any]]:
        sql_up = sql.upper()
        if "FROM CEO_APPROVALS" in sql_up and "WHERE ACTION_ID" in sql_up:
            action_id = params[0]
            row = self.approvals.get(action_id)
            return [row] if row else []
        return []


@pytest.fixture
def fake_db() -> _FakeDB:
    return _FakeDB()


@pytest.fixture
def gateway(fake_db: _FakeDB):
    """Import picoclaw_gateway with _get_db patched to return our fake DB."""
    from agents import picoclaw_gateway as gw

    # Reset the cached _schema_ready flag so _ensure_schema runs each test
    gw._schema_ready = False

    async def _fake_get_db():
        return fake_db

    with patch.object(gw, "_get_db", _fake_get_db):
        yield gw


# ═══════════════════════════════════════════════════════════════════════════
#  POST /api/ceo/approval-result
# ═══════════════════════════════════════════════════════════════════════════


class TestApprovalResultPost:
    @pytest.mark.asyncio
    async def test_valid_approved(self, gateway, fake_db):
        result = await gateway.approval_result(
            req={"action_id": "act_001", "approved": True},
            x_ceo_key=None,
        )
        assert result["success"] is True
        assert result["action_id"] == "act_001"
        assert result["approved"] is True
        assert result["idempotent"] is False
        assert result["decided_at"] > 0
        assert "act_001" in fake_db.approvals
        assert fake_db.approvals["act_001"]["approved"] == 1

    @pytest.mark.asyncio
    async def test_valid_rejected(self, gateway, fake_db):
        result = await gateway.approval_result(
            req={"action_id": "act_002", "approved": False},
            x_ceo_key=None,
        )
        assert result["approved"] is False
        assert fake_db.approvals["act_002"]["approved"] == 0

    @pytest.mark.asyncio
    async def test_with_full_payload(self, gateway, fake_db):
        result = await gateway.approval_result(
            req={
                "action_id": "act_003",
                "approved": True,
                "action_name": "send_email_campaign",
                "level": "RED",
            },
            x_ceo_key=None,
        )
        assert result["success"] is True
        assert fake_db.approvals["act_003"]["action_name"] == "send_email_campaign"
        assert fake_db.approvals["act_003"]["level"] == "RED"

    @pytest.mark.asyncio
    async def test_invalid_level_defaults_to_orange(self, gateway, fake_db):
        await gateway.approval_result(
            req={"action_id": "act_004", "approved": True, "level": "PINK"},
            x_ceo_key=None,
        )
        assert fake_db.approvals["act_004"]["level"] == "ORANGE"

    @pytest.mark.asyncio
    async def test_lowercase_level_normalized(self, gateway, fake_db):
        await gateway.approval_result(
            req={"action_id": "act_005", "approved": True, "level": "green"},
            x_ceo_key=None,
        )
        assert fake_db.approvals["act_005"]["level"] == "GREEN"

    @pytest.mark.asyncio
    async def test_idempotent_second_call(self, gateway, fake_db):
        r1 = await gateway.approval_result(
            req={"action_id": "act_006", "approved": True},
            x_ceo_key=None,
        )
        assert r1["idempotent"] is False

        r2 = await gateway.approval_result(
            req={"action_id": "act_006", "approved": False},  # try to flip
            x_ceo_key=None,
        )
        assert r2["idempotent"] is True
        # First write wins — still approved
        assert r2["approved"] is True
        assert fake_db.approvals["act_006"]["approved"] == 1

    @pytest.mark.asyncio
    async def test_missing_action_id_rejected(self, gateway):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await gateway.approval_result(
                req={"approved": True},
                x_ceo_key=None,
            )
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_invalid_action_id_rejected(self, gateway):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await gateway.approval_result(
                req={"action_id": "bad id with spaces!", "approved": True},
                x_ceo_key=None,
            )
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_action_id_too_long_rejected(self, gateway):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await gateway.approval_result(
                req={"action_id": "a" * 65, "approved": True},
                x_ceo_key=None,
            )
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_non_bool_approved_rejected(self, gateway):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await gateway.approval_result(
                req={"action_id": "act_007", "approved": "yes"},
                x_ceo_key=None,
            )
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_strict_auth_with_valid_key(self, gateway, fake_db):
        with patch.dict(os.environ, {"CEO_API_KEY": "secret_ceo_key_123"}):
            result = await gateway.approval_result(
                req={"action_id": "act_008", "approved": True},
                x_ceo_key="secret_ceo_key_123",
            )
            assert result["success"] is True

    @pytest.mark.asyncio
    async def test_strict_auth_with_invalid_key_rejected(self, gateway):
        from fastapi import HTTPException

        with patch.dict(os.environ, {"CEO_API_KEY": "secret_ceo_key_123"}):
            with pytest.raises(HTTPException) as exc:
                await gateway.approval_result(
                    req={"action_id": "act_009", "approved": True},
                    x_ceo_key="wrong_key",
                )
            assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_no_header_no_env_stays_public(self, gateway, fake_db):
        # Even if CEO_API_KEY env is unset, posting without header is allowed
        # (backward compat with existing CEO local client).
        old = os.environ.pop("CEO_API_KEY", None)
        try:
            result = await gateway.approval_result(
                req={"action_id": "act_010", "approved": True},
                x_ceo_key=None,
            )
            assert result["success"] is True
        finally:
            if old is not None:
                os.environ["CEO_API_KEY"] = old


# ═══════════════════════════════════════════════════════════════════════════
#  GET /api/ceo/approval-result/{action_id}
# ═══════════════════════════════════════════════════════════════════════════


class TestApprovalResultGet:
    @pytest.mark.asyncio
    async def test_read_after_write(self, gateway, fake_db):
        await gateway.approval_result(
            req={
                "action_id": "act_011",
                "approved": True,
                "action_name": "post_signal",
                "level": "ORANGE",
            },
            x_ceo_key=None,
        )

        result = await gateway.get_approval_result(action_id="act_011", x_api_key=None)
        assert result["action_id"] == "act_011"
        assert result["approved"] is True
        assert result["action_name"] == "post_signal"
        assert result["level"] == "ORANGE"
        assert result["decided_by"] == "alexis"

    @pytest.mark.asyncio
    async def test_read_missing_404(self, gateway):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await gateway.get_approval_result(action_id="act_missing", x_api_key=None)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_read_invalid_action_id(self, gateway):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await gateway.get_approval_result(action_id="bad id!", x_api_key=None)
        assert exc.value.status_code == 400
