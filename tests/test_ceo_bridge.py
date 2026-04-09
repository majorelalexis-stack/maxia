"""Tests for the Phase 1 CEO bridge (ceo_bridge.py).

The bridge exposes 3 endpoints under /api/ceo/messages/* with an
X-CEO-Key auth header. These tests exercise the handler functions
directly with an in-memory fake DB — no HTTP, no real DB, no network.

Covered:
    - ingest: validation, escalation pre-flag, side effects
    - pending: batch fetch + atomic status flip
    - reply: dispatch success / escalated path / 404
    - auth: missing / invalid / valid X-CEO-Key
    - status: public counters
"""
from __future__ import annotations

import os
import sys
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..", "backend")
sys.path.insert(0, BACKEND_DIR)


# ═══════════════════════════════════════════════════════════════════════════
#  Fake DB — mirrors the raw_execute / raw_execute_fetchall surface
# ═══════════════════════════════════════════════════════════════════════════


class _FakeDB:
    """In-memory substitute for core.database.db, ceo_pending_replies only."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    async def raw_executescript(self, sql: str) -> None:
        pass  # schema is a no-op in tests

    async def raw_execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        sql_up = " ".join(sql.upper().split())
        if sql_up.startswith("INSERT INTO CEO_PENDING_REPLIES"):
            (
                msg_id, channel, source_ref, user_id, user_name, message,
                language, received_at, status, escalated,
            ) = params
            self.rows[msg_id] = {
                "msg_id": msg_id,
                "channel": channel,
                "source_ref": source_ref,
                "user_id": user_id,
                "user_name": user_name,
                "message": message,
                "language": language,
                "received_at": received_at,
                "status": status,
                "response": "",
                "confidence": 0.0,
                "escalated": escalated,
                "responded_at": 0,
            }
        elif sql_up.startswith("UPDATE CEO_PENDING_REPLIES SET STATUS=? WHERE MSG_ID=? AND STATUS=?"):
            new_status, msg_id, old_status = params
            row = self.rows.get(msg_id)
            if row and row["status"] == old_status:
                row["status"] = new_status
        elif sql_up.startswith("UPDATE CEO_PENDING_REPLIES SET STATUS=?, RESPONSE=?"):
            new_status, response, confidence, escalated, now, msg_id = params
            row = self.rows.get(msg_id)
            if row:
                row["status"] = new_status
                row["response"] = response
                row["confidence"] = confidence
                row["escalated"] = escalated
                row["responded_at"] = now

    def seed(self, msg_id: str, **fields) -> None:
        """Test helper — insert a fully-formed row without going through ingest."""
        base = {
            "msg_id": msg_id,
            "channel": "discord",
            "source_ref": "100:200",
            "user_id": "u",
            "user_name": "U",
            "message": "seed message",
            "language": "",
            "received_at": 0,
            "status": "processing",
            "response": "",
            "confidence": 0.0,
            "escalated": 0,
            "responded_at": 0,
        }
        base.update(fields)
        self.rows[msg_id] = base

    async def raw_execute_fetchall(
        self, sql: str, params: tuple[Any, ...] = ()
    ) -> list[dict[str, Any]]:
        sql_up = " ".join(sql.upper().split())

        if "FROM CEO_PENDING_REPLIES WHERE STATUS=? AND CHANNEL=?" in sql_up:
            status, channel, limit = params
            results = [
                r for r in self.rows.values()
                if r["status"] == status and r["channel"] == channel
            ]
            results.sort(key=lambda r: r["received_at"])
            return results[:limit]

        if "FROM CEO_PENDING_REPLIES WHERE STATUS=? ORDER BY" in sql_up:
            status, limit = params
            results = [r for r in self.rows.values() if r["status"] == status]
            results.sort(key=lambda r: r["received_at"])
            return results[:limit]

        if "SELECT CHANNEL, SOURCE_REF, MESSAGE, USER_ID, USER_NAME, ESCALATED" in sql_up:
            msg_id = params[0]
            row = self.rows.get(msg_id)
            return [row] if row else []

        if "SELECT STATUS, COUNT(*) AS CNT FROM CEO_PENDING_REPLIES" in sql_up:
            counts: dict[str, int] = {}
            for r in self.rows.values():
                counts[r["status"]] = counts.get(r["status"], 0) + 1
            return [{"status": s, "cnt": c} for s, c in counts.items()]

        if "SELECT MSG_ID FROM CEO_PENDING_REPLIES WHERE STATUS=? AND RECEIVED_AT < ?" in sql_up:
            status, cutoff = params
            return [
                {"msg_id": r["msg_id"]}
                for r in self.rows.values()
                if r["status"] == status and r["received_at"] < cutoff
            ]

        # SELECT ID FROM FORUM_POSTS (forum dispatcher lookup — not exercised here)
        if "FROM FORUM_POSTS" in sql_up:
            return []

        return []


@pytest.fixture
def fake_db() -> _FakeDB:
    return _FakeDB()


@pytest.fixture
def bridge(fake_db: _FakeDB):
    """Import ceo_bridge with _get_db and dispatch shims patched."""
    import ceo_bridge as cb

    async def _fake_get_db():
        return fake_db

    async def _fake_dispatch_discord(source_ref: str, response: str) -> bool:
        return True  # pretend Discord accepted

    async def _fake_dispatch_forum(source_ref: str, response: str) -> bool:
        return True

    with (
        patch.object(cb, "_get_db", _fake_get_db),
        patch.object(cb, "_dispatch_discord", AsyncMock(side_effect=_fake_dispatch_discord)),
        patch.object(cb, "_dispatch_forum", AsyncMock(side_effect=_fake_dispatch_forum)),
        patch.dict(os.environ, {"CEO_API_KEY": "test_ceo_key"}),
    ):
        yield cb


# ═══════════════════════════════════════════════════════════════════════════
#  POST /api/ceo/messages/ingest
# ═══════════════════════════════════════════════════════════════════════════


class TestIngest:
    @pytest.mark.asyncio
    async def test_valid_message_queued(self, bridge, fake_db):
        result = await bridge.ingest_endpoint(
            req={
                "channel": "discord",
                "source_ref": "123:456",
                "user_id": "user_abc",
                "user_name": "Alice",
                "message": "How does escrow work?",
                "language": "en",
            },
            x_ceo_key="test_ceo_key",
        )
        assert result["status"] == "pending"
        assert result["msg_id"].startswith("msg_")
        # Persisted
        assert len(fake_db.rows) == 1
        row = next(iter(fake_db.rows.values()))
        assert row["channel"] == "discord"
        assert row["message"] == "How does escrow work?"
        assert row["escalated"] == 0

    @pytest.mark.asyncio
    async def test_invalid_channel_rejected(self, bridge):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await bridge.ingest_endpoint(
                req={
                    "channel": "sms",  # not in VALID_CHANNELS
                    "source_ref": "x",
                    "user_id": "u",
                    "user_name": "u",
                    "message": "hi",
                },
                x_ceo_key="test_ceo_key",
            )
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_empty_message_rejected(self, bridge):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await bridge.ingest_endpoint(
                req={
                    "channel": "forum",
                    "source_ref": "post_abc",
                    "user_id": "u",
                    "user_name": "u",
                    "message": "   ",
                },
                x_ceo_key="test_ceo_key",
            )
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_message_too_long_rejected(self, bridge):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await bridge.ingest_endpoint(
                req={
                    "channel": "forum",
                    "source_ref": "post_abc",
                    "user_id": "u",
                    "user_name": "u",
                    "message": "x" * 5000,
                },
                x_ceo_key="test_ceo_key",
            )
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_auto_escalates_on_sensitive_keyword(self, bridge, fake_db):
        await bridge.ingest_endpoint(
            req={
                "channel": "forum",
                "source_ref": "post_xyz",
                "user_id": "u",
                "user_name": "u",
                "message": "I want a REFUND for my lost USDC",
            },
            x_ceo_key="test_ceo_key",
        )
        row = next(iter(fake_db.rows.values()))
        assert row["escalated"] == 1

    @pytest.mark.asyncio
    async def test_auth_rejected_without_key(self, bridge):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await bridge.ingest_endpoint(
                req={
                    "channel": "discord",
                    "source_ref": "1:2",
                    "user_id": "u",
                    "user_name": "u",
                    "message": "hi",
                },
                x_ceo_key="",
            )
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_auth_rejected_with_wrong_key(self, bridge):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await bridge.ingest_endpoint(
                req={
                    "channel": "discord",
                    "source_ref": "1:2",
                    "user_id": "u",
                    "user_name": "u",
                    "message": "hi",
                },
                x_ceo_key="wrong_key",
            )
        assert exc.value.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════
#  GET /api/ceo/messages/pending
# ═══════════════════════════════════════════════════════════════════════════


class TestPending:
    @pytest.mark.asyncio
    async def test_returns_queued_and_marks_processing(self, bridge, fake_db):
        # Queue 2 messages
        await bridge.ingest_endpoint(
            req={
                "channel": "discord",
                "source_ref": "100:200",
                "user_id": "u1",
                "user_name": "U1",
                "message": "question one",
            },
            x_ceo_key="test_ceo_key",
        )
        await bridge.ingest_endpoint(
            req={
                "channel": "forum",
                "source_ref": "post_a",
                "user_id": "u2",
                "user_name": "U2",
                "message": "question two",
            },
            x_ceo_key="test_ceo_key",
        )

        result = await bridge.pending_endpoint(
            x_ceo_key="test_ceo_key", limit=10, channel=None,
        )
        assert result["count"] == 2
        assert len(result["messages"]) == 2

        # Both rows should now be 'processing'
        for row in fake_db.rows.values():
            assert row["status"] == "processing"

    @pytest.mark.asyncio
    async def test_filter_by_channel(self, bridge, fake_db):
        await bridge.ingest_endpoint(
            req={"channel": "discord", "source_ref": "1:1", "user_id": "u", "user_name": "u", "message": "d1"},
            x_ceo_key="test_ceo_key",
        )
        await bridge.ingest_endpoint(
            req={"channel": "forum", "source_ref": "p1", "user_id": "u", "user_name": "u", "message": "f1"},
            x_ceo_key="test_ceo_key",
        )

        result = await bridge.pending_endpoint(
            x_ceo_key="test_ceo_key", limit=10, channel="discord",
        )
        assert result["count"] == 1
        assert result["messages"][0]["channel"] == "discord"

    @pytest.mark.asyncio
    async def test_empty_queue_returns_empty_list(self, bridge):
        result = await bridge.pending_endpoint(
            x_ceo_key="test_ceo_key", limit=10, channel=None,
        )
        assert result["count"] == 0
        assert result["messages"] == []

    @pytest.mark.asyncio
    async def test_pending_auth_required(self, bridge):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await bridge.pending_endpoint(
                x_ceo_key="", limit=10, channel=None,
            )
        assert exc.value.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════
#  POST /api/ceo/messages/{msg_id}/reply
# ═══════════════════════════════════════════════════════════════════════════


class TestReply:
    async def _queue_msg(self, bridge, *, channel: str = "discord",
                         message: str = "hi",
                         source_ref: str = "100:200") -> str:
        result = await bridge.ingest_endpoint(
            req={
                "channel": channel,
                "source_ref": source_ref,
                "user_id": "u",
                "user_name": "U",
                "message": message,
            },
            x_ceo_key="test_ceo_key",
        )
        return result["msg_id"]

    @pytest.mark.asyncio
    async def test_valid_reply_dispatched(self, bridge, fake_db):
        msg_id = await self._queue_msg(bridge)

        result = await bridge.reply_endpoint(
            req={
                "response": "MAXIA escrow locks USDC in a PDA on Solana.",
                "confidence": 0.9,
                "escalated": False,
            },
            msg_id=msg_id,
            x_ceo_key="test_ceo_key",
        )
        assert result["success"] is True
        assert result["dispatched"] is True
        assert result["escalated"] is False
        assert result["status"] == "replied"
        assert fake_db.rows[msg_id]["status"] == "replied"
        assert fake_db.rows[msg_id]["confidence"] == 0.9

    @pytest.mark.asyncio
    async def test_escalated_reply_not_dispatched(self, bridge, fake_db):
        msg_id = await self._queue_msg(bridge)

        result = await bridge.reply_endpoint(
            req={
                "response": "Draft: I'll escalate to Alexis.",
                "confidence": 0.2,
                "escalated": True,
            },
            msg_id=msg_id,
            x_ceo_key="test_ceo_key",
        )
        assert result["status"] == "escalated"
        assert result["dispatched"] is False
        assert result["escalated"] is True
        assert fake_db.rows[msg_id]["status"] == "escalated"

    @pytest.mark.asyncio
    async def test_response_with_sensitive_keyword_auto_escalates(self, bridge, fake_db):
        msg_id = await self._queue_msg(bridge, message="How are prices set?")
        # Even if CEO Local doesn't flag escalated, the server does it when
        # the response itself contains sensitive words.
        result = await bridge.reply_endpoint(
            req={
                "response": "Please file a refund request via support",
                "confidence": 0.8,
                "escalated": False,
            },
            msg_id=msg_id,
            x_ceo_key="test_ceo_key",
        )
        assert result["escalated"] is True
        assert result["status"] == "escalated"

    @pytest.mark.asyncio
    async def test_msg_not_found_returns_404(self, bridge):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await bridge.reply_endpoint(
                req={"response": "...", "confidence": 0.5, "escalated": False},
                msg_id="msg_000000000000",
                x_ceo_key="test_ceo_key",
            )
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_empty_response_rejected(self, bridge):
        from fastapi import HTTPException

        msg_id = await self._queue_msg(bridge)
        with pytest.raises(HTTPException) as exc:
            await bridge.reply_endpoint(
                req={"response": "", "confidence": 0.5, "escalated": False},
                msg_id=msg_id,
                x_ceo_key="test_ceo_key",
            )
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_reply_auth_required(self, bridge):
        from fastapi import HTTPException

        msg_id = await self._queue_msg(bridge)
        with pytest.raises(HTTPException) as exc:
            await bridge.reply_endpoint(
                req={"response": "ok", "confidence": 0.5, "escalated": False},
                msg_id=msg_id,
                x_ceo_key="",
            )
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_confidence_clamped_to_unit_interval(self, bridge, fake_db):
        msg_id = await self._queue_msg(bridge)
        await bridge.reply_endpoint(
            req={"response": "ok", "confidence": 5.0, "escalated": False},
            msg_id=msg_id,
            x_ceo_key="test_ceo_key",
        )
        assert fake_db.rows[msg_id]["confidence"] == 1.0

        msg_id2 = await self._queue_msg(bridge, source_ref="100:201")
        await bridge.reply_endpoint(
            req={"response": "ok", "confidence": -2.0, "escalated": False},
            msg_id=msg_id2,
            x_ceo_key="test_ceo_key",
        )
        assert fake_db.rows[msg_id2]["confidence"] == 0.0


# ═══════════════════════════════════════════════════════════════════════════
#  GET /api/ceo/messages/status
# ═══════════════════════════════════════════════════════════════════════════


class TestStatus:
    @pytest.mark.asyncio
    async def test_status_returns_counters(self, bridge):
        # Queue 2 messages
        await bridge.ingest_endpoint(
            req={
                "channel": "discord",
                "source_ref": "1:1",
                "user_id": "u",
                "user_name": "u",
                "message": "hi",
            },
            x_ceo_key="test_ceo_key",
        )
        await bridge.ingest_endpoint(
            req={
                "channel": "forum",
                "source_ref": "p",
                "user_id": "u",
                "user_name": "u",
                "message": "hi",
            },
            x_ceo_key="test_ceo_key",
        )

        result = await bridge.bridge_status()
        assert result["bridge"] == "ceo_bridge"
        assert "discord" in result["channels"]
        assert "forum" in result["channels"]
        assert result["counters"].get("pending", 0) == 2


# ═══════════════════════════════════════════════════════════════════════════
#  Pure helpers
# ═══════════════════════════════════════════════════════════════════════════


class TestHelpers:
    def test_should_escalate_detects_refund(self):
        import ceo_bridge as cb
        assert cb._should_escalate("Please refund me") is True

    def test_should_escalate_detects_legal(self):
        import ceo_bridge as cb
        assert cb._should_escalate("I will take LEGAL action") is True

    def test_should_escalate_ignores_normal_text(self):
        import ceo_bridge as cb
        assert cb._should_escalate("How does swap work?") is False

    def test_should_escalate_handles_non_string(self):
        import ceo_bridge as cb
        assert cb._should_escalate(None) is False  # type: ignore[arg-type]
        assert cb._should_escalate(123) is False  # type: ignore[arg-type]

    # ── False-positive regression guards (word boundaries) ──

    def test_should_escalate_no_fp_tissue(self):
        import ceo_bridge as cb
        # "sue" inside "tissue" must NOT trigger escalation
        assert cb._should_escalate("I have a tissue issue") is False

    def test_should_escalate_no_fp_ensued(self):
        import ceo_bridge as cb
        assert cb._should_escalate("chaos ensued after the release") is False

    def test_should_escalate_no_fp_hackathon(self):
        import ceo_bridge as cb
        # "hack" IS inside "hackathon" but with \b the word-bounded
        # check only matches the standalone word. "hackathon" is a full
        # word itself, so \bhack\b does NOT match — False.
        assert cb._should_escalate("I joined a hackathon last week") is False

    # ── Multilingual positive cases — 13 languages ──

    def test_should_escalate_french_remboursement(self):
        import ceo_bridge as cb
        assert cb._should_escalate("je veux un remboursement maintenant") is True

    def test_should_escalate_french_avocat(self):
        import ceo_bridge as cb
        assert cb._should_escalate("je vais prendre un avocat") is True

    def test_should_escalate_spanish_reembolso(self):
        import ceo_bridge as cb
        assert cb._should_escalate("quiero mi reembolso ahora") is True

    def test_should_escalate_spanish_estafa(self):
        import ceo_bridge as cb
        assert cb._should_escalate("esto es una estafa") is True

    def test_should_escalate_german_rueckerstattung(self):
        import ceo_bridge as cb
        assert cb._should_escalate("ich brauche eine Rückerstattung") is True

    def test_should_escalate_german_anwalt(self):
        import ceo_bridge as cb
        assert cb._should_escalate("ich werde einen Anwalt einschalten") is True

    def test_should_escalate_portuguese_reembolso(self):
        import ceo_bridge as cb
        assert cb._should_escalate("quero meu reembolso") is True

    def test_should_escalate_italian_rimborso(self):
        import ceo_bridge as cb
        assert cb._should_escalate("voglio un rimborso") is True

    def test_should_escalate_italian_truffa(self):
        import ceo_bridge as cb
        assert cb._should_escalate("è una truffa") is True

    def test_should_escalate_dutch_terugbetaling(self):
        import ceo_bridge as cb
        assert cb._should_escalate("ik wil een terugbetaling") is True

    def test_should_escalate_turkish_dolandirici(self):
        import ceo_bridge as cb
        assert cb._should_escalate("siz dolandırıcısınız") is True

    def test_should_escalate_russian_vozvrat(self):
        import ceo_bridge as cb
        assert cb._should_escalate("я хочу возврат денег") is True

    def test_should_escalate_russian_vzlom(self):
        import ceo_bridge as cb
        assert cb._should_escalate("мой кошелек взломан") is True

    def test_should_escalate_arabic_refund(self):
        import ceo_bridge as cb
        assert cb._should_escalate("أريد استرداد الأموال") is True

    def test_should_escalate_arabic_lawyer(self):
        import ceo_bridge as cb
        assert cb._should_escalate("سأتحدث مع محامي") is True

    def test_should_escalate_hindi_refund(self):
        import ceo_bridge as cb
        assert cb._should_escalate("मुझे धनवापसी चाहिए") is True

    def test_should_escalate_hindi_lawyer(self):
        import ceo_bridge as cb
        assert cb._should_escalate("मैं वकील से बात करूंगा") is True

    def test_should_escalate_chinese_refund(self):
        import ceo_bridge as cb
        assert cb._should_escalate("我要退款") is True

    def test_should_escalate_chinese_scam(self):
        import ceo_bridge as cb
        assert cb._should_escalate("这是一个骗局") is True

    def test_should_escalate_chinese_stolen(self):
        import ceo_bridge as cb
        assert cb._should_escalate("我的钱包被盗了") is True

    def test_should_escalate_japanese_refund(self):
        import ceo_bridge as cb
        assert cb._should_escalate("返金してください") is True

    def test_should_escalate_japanese_lawyer(self):
        import ceo_bridge as cb
        assert cb._should_escalate("弁護士に相談します") is True

    def test_should_escalate_japanese_scam(self):
        import ceo_bridge as cb
        assert cb._should_escalate("これは詐欺です") is True

    # ── Multilingual negative cases — normal messages should NOT escalate ──

    def test_should_escalate_french_normal(self):
        import ceo_bridge as cb
        assert cb._should_escalate("Bonjour, comment fonctionne le swap ?") is False

    def test_should_escalate_chinese_normal(self):
        import ceo_bridge as cb
        assert cb._should_escalate("MAXIA 支持哪些区块链?") is False

    def test_should_escalate_japanese_normal(self):
        import ceo_bridge as cb
        assert cb._should_escalate("MAXIAはどのように動作しますか") is False

    def test_should_escalate_spanish_normal(self):
        import ceo_bridge as cb
        assert cb._should_escalate("¿Qué es MAXIA?") is False

    def test_validate_channel_lowercases(self):
        import ceo_bridge as cb
        assert cb._validate_channel("DISCORD") == "discord"
        assert cb._validate_channel("  Forum  ") == "forum"

    def test_validate_channel_rejects_unknown(self):
        from fastapi import HTTPException
        import ceo_bridge as cb

        with pytest.raises(HTTPException):
            cb._validate_channel("twitter")


# ═══════════════════════════════════════════════════════════════════════════
#  Phase 2A — Escalation Telegram alerts
# ═══════════════════════════════════════════════════════════════════════════


class TestEscalationAlert:
    def test_format_alert_contains_required_fields(self):
        import ceo_bridge as cb
        text = cb._format_escalation_alert(
            msg_id="msg_abc",
            channel="discord",
            user_name="Alice",
            user_id="42",
            user_message="Can I get a refund?",
            draft_response="Draft reply here",
        )
        assert "ESCALADE" in text
        assert "discord" in text
        assert "Alice" in text
        assert "42" in text
        assert "refund" in text.lower()
        assert "Draft" in text
        assert "msg_abc" in text

    def test_format_alert_escapes_html(self):
        import ceo_bridge as cb
        text = cb._format_escalation_alert(
            msg_id="msg_abc",
            channel="discord",
            user_name="<script>",
            user_id="42",
            user_message="a & b",
            draft_response="",
        )
        # Raw script tag must NOT survive
        assert "<script>" not in text
        assert "&lt;script&gt;" in text
        # Ampersand must be escaped too
        assert "a &amp; b" in text

    def test_format_alert_truncates_long_message(self):
        import ceo_bridge as cb
        long_msg = "x" * 5000
        text = cb._format_escalation_alert(
            msg_id="msg_abc",
            channel="discord",
            user_name="U",
            user_id="42",
            user_message=long_msg,
            draft_response="",
        )
        # Should stay well under 4096 chars (Telegram hard cap)
        assert len(text) < 4096

    @pytest.mark.asyncio
    async def test_notify_skipped_without_env_vars(self, bridge):
        from unittest.mock import AsyncMock, patch
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TELEGRAM_BOT_TOKEN", None)
            os.environ.pop("TELEGRAM_ALERT_CHAT_ID", None)
            result = await bridge._notify_escalation_telegram(
                msg_id="msg_abc",
                channel="discord",
                user_name="U",
                user_id="42",
                user_message="refund please",
                draft_response="",
            )
            assert result is False

    @pytest.mark.asyncio
    async def test_notify_posts_to_telegram_with_env(self, bridge):
        from unittest.mock import AsyncMock, MagicMock, patch

        captured: dict = {}

        class _FakeResp:
            status_code = 200
            text = "ok"

        class _FakeClient:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return None
            async def post(self, url, json=None):
                captured["url"] = url
                captured["json"] = json
                return _FakeResp()

        env = {
            "TELEGRAM_BOT_TOKEN": "fake_tg_token",
            "TELEGRAM_ALERT_CHAT_ID": "@MAXIA_alerts",
            "CEO_API_KEY": "test_ceo_key",
        }
        with (
            patch.dict(os.environ, env),
            patch.object(bridge.httpx, "AsyncClient", _FakeClient),
        ):
            result = await bridge._notify_escalation_telegram(
                msg_id="msg_abc",
                channel="discord",
                user_name="Alice",
                user_id="42",
                user_message="I want a refund",
                draft_response="This will be handled by Alexis",
            )
        assert result is True
        assert "fake_tg_token" in captured["url"]
        payload = captured["json"]
        assert payload["chat_id"] == "@MAXIA_alerts"
        assert payload["parse_mode"] == "HTML"
        assert "refund" in payload["text"].lower()
        assert "Alice" in payload["text"]


# ═══════════════════════════════════════════════════════════════════════════
#  Phase 2B — Janitor
# ═══════════════════════════════════════════════════════════════════════════


class TestJanitor:
    @pytest.mark.asyncio
    async def test_recovers_stale_processing(self, bridge, fake_db):
        import time as _t
        now = int(_t.time())
        fake_db.seed("msg_stalea11111", status="processing", received_at=now - 9999)
        fake_db.seed("msg_staleb22222", status="processing", received_at=now - 9999)
        # Fresh processing message — must NOT be recovered
        fake_db.seed("msg_freshc33333", status="processing", received_at=now - 10)

        recovered = await bridge.recover_stale_processing(stale_seconds=300)
        assert recovered == 2

        assert fake_db.rows["msg_stalea11111"]["status"] == "pending"
        assert fake_db.rows["msg_staleb22222"]["status"] == "pending"
        assert fake_db.rows["msg_freshc33333"]["status"] == "processing"

    @pytest.mark.asyncio
    async def test_noop_when_no_stale(self, bridge, fake_db):
        import time as _t
        now = int(_t.time())
        fake_db.seed("msg_freshd44444", status="processing", received_at=now - 5)
        recovered = await bridge.recover_stale_processing(stale_seconds=300)
        assert recovered == 0
        assert fake_db.rows["msg_freshd44444"]["status"] == "processing"

    @pytest.mark.asyncio
    async def test_does_not_touch_other_statuses(self, bridge, fake_db):
        import time as _t
        now = int(_t.time())
        fake_db.seed("msg_pending5555", status="pending", received_at=now - 9999)
        fake_db.seed("msg_replied6666", status="replied", received_at=now - 9999)
        fake_db.seed("msg_escaltd7777", status="escalated", received_at=now - 9999)

        recovered = await bridge.recover_stale_processing(stale_seconds=300)
        assert recovered == 0
        assert fake_db.rows["msg_pending5555"]["status"] == "pending"
        assert fake_db.rows["msg_replied6666"]["status"] == "replied"
        assert fake_db.rows["msg_escaltd7777"]["status"] == "escalated"

    @pytest.mark.asyncio
    async def test_enforces_minimum_stale_seconds(self, bridge, fake_db):
        """Caller passing stale_seconds<30 is bumped to 30 to avoid
        recovering messages that are still being processed."""
        import time as _t
        now = int(_t.time())
        fake_db.seed("msg_freshe55555", status="processing", received_at=now - 20)
        recovered = await bridge.recover_stale_processing(stale_seconds=0)
        # 20 s < 30 s floor → must NOT recover
        assert recovered == 0
        assert fake_db.rows["msg_freshe55555"]["status"] == "processing"
