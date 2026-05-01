"""Tests TDD pour MAXIA Hub Phase 5 — Testament cryptographique.

Ordre TDD : écrits AVANT l'implémentation → tous RED au premier lancement.
"""
from __future__ import annotations

import time
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from nacl.signing import SigningKey
import base58


# ─── Helpers ─────────────────────────────────────────────────────────────────

def make_keypair():
    sk = SigningKey.generate()
    pk_b58 = base58.b58encode(bytes(sk.verify_key)).decode()
    return sk, pk_b58


def sign_hub_auth(sk: SigningKey, hub_id: str, ts: int) -> str:
    msg = f"{hub_id}:{ts}"
    return base58.b58encode(sk.sign(msg.encode()).signature).decode()


def make_mock_db():
    mock = MagicMock()
    mock.raw_execute = AsyncMock(return_value=None)
    mock.raw_execute_fetchall = AsyncMock(return_value=[])
    mock._fetchone = AsyncMock(return_value=None)
    mock._fetchall = AsyncMock(return_value=[])
    return mock


def hub_agent_row(
    hub_id: str = "hub_abc12345678901234567890123456",
    name: str = "TestAgent",
    score: int = 50,
    status: str = "active",
    public_key: str = None,
    last_heartbeat: int = None,
):
    _, pk = make_keypair()
    return {
        "hub_id": hub_id,
        "name": name,
        "score": score,
        "status": status,
        "public_key": public_key or pk,
        "last_heartbeat": last_heartbeat if last_heartbeat is not None else int(time.time()) - 60,
    }


def will_row(
    will_id: str = None,
    testator_hub_id: str = "testator_hub_id_32charsxxxxxxxxx",
    successor_hub_id: str = "successor_hub_id_32charsxxxxxxxx",
    will_type: str = "simple",
    transfer_score_pct: float = 0.8,
    transfer_lineage: int = 1,
    grace_period_hours: int = 72,
    grace_start_ts: int = None,
    auction_end_ts: int = None,
    min_bid_usdc: float = 0.0,
    status: str = "draft",
    created_at: int = None,
    activated_at: int = None,
    executed_at: int = None,
    revoked_at: int = None,
):
    return {
        "will_id": will_id or uuid.uuid4().hex,
        "testator_hub_id": testator_hub_id,
        "successor_hub_id": successor_hub_id,
        "will_type": will_type,
        "transfer_score_pct": transfer_score_pct,
        "transfer_lineage": transfer_lineage,
        "grace_period_hours": grace_period_hours,
        "grace_start_ts": grace_start_ts,
        "auction_end_ts": auction_end_ts,
        "min_bid_usdc": min_bid_usdc,
        "status": status,
        "created_at": created_at or int(time.time()) - 3600,
        "activated_at": activated_at,
        "executed_at": executed_at,
        "revoked_at": revoked_at,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# TestCreateWill — POST /api/hub/will
# ═══════════════════════════════════════════════════════════════════════════════

class TestCreateWill:

    @pytest.mark.asyncio
    async def test_create_will_simple_returns_draft(self):
        """Création d'un testament simple → statut draft."""
        from hub.hub_will import create_will

        testator = hub_agent_row(hub_id="testator_hub_id_32charsxxxxxxxxx")
        mock_db = make_mock_db()
        mock_db._fetchone.side_effect = [
            None,  # pas de testament actif/draft existant
        ]

        with patch("hub.hub_will.db", mock_db):
            result = await create_will(
                testator_hub_id="testator_hub_id_32charsxxxxxxxxx",
                will_type="simple",
                successor_hub_id="successor_hub_id_32charsxxxxxxxx",
                transfer_score_pct=0.8,
                transfer_lineage=True,
                grace_period_hours=72,
                auction_end_ts=None,
                min_bid_usdc=0.0,
            )

        assert result["status"] == "draft"
        assert "will_id" in result
        mock_db.raw_execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_will_duplicate_returns_409(self):
        """Testateur avec testament existant (draft/active) → 409."""
        from hub.hub_will import create_will
        from fastapi import HTTPException

        existing = will_row(status="active")
        mock_db = make_mock_db()
        mock_db._fetchone.return_value = existing

        with patch("hub.hub_will.db", mock_db):
            with pytest.raises(HTTPException) as exc_info:
                await create_will(
                    testator_hub_id="testator_hub_id_32charsxxxxxxxxx",
                    will_type="simple",
                    successor_hub_id="successor_hub_id_32charsxxxxxxxx",
                    transfer_score_pct=0.8,
                    transfer_lineage=True,
                    grace_period_hours=72,
                    auction_end_ts=None,
                    min_bid_usdc=0.0,
                )
        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_create_will_auction_ignores_successor(self):
        """Type auction → successor_hub_id stocké comme NULL."""
        from hub.hub_will import create_will

        mock_db = make_mock_db()
        mock_db._fetchone.return_value = None

        captured_params = []
        async def capture(sql, params=()):
            captured_params.append(params)
        mock_db.raw_execute.side_effect = capture

        with patch("hub.hub_will.db", mock_db):
            result = await create_will(
                testator_hub_id="testator_hub_id_32charsxxxxxxxxx",
                will_type="auction",
                successor_hub_id="someone_hub_id_32charsxxxxxxxxxx",
                transfer_score_pct=0.5,
                transfer_lineage=True,
                grace_period_hours=48,
                auction_end_ts=None,
                min_bid_usdc=10.0,
            )

        # successor doit être None pour une auction
        assert result["successor_hub_id"] is None

    @pytest.mark.asyncio
    async def test_create_will_draft_also_blocks_409(self):
        """Testateur avec testament en DRAFT déjà → 409 aussi."""
        from hub.hub_will import create_will
        from fastapi import HTTPException

        existing = will_row(status="draft")
        mock_db = make_mock_db()
        mock_db._fetchone.return_value = existing

        with patch("hub.hub_will.db", mock_db):
            with pytest.raises(HTTPException) as exc_info:
                await create_will(
                    testator_hub_id="testator_hub_id_32charsxxxxxxxxx",
                    will_type="simple",
                    successor_hub_id="successor_hub_id_32charsxxxxxxxx",
                    transfer_score_pct=0.8,
                    transfer_lineage=True,
                    grace_period_hours=72,
                    auction_end_ts=None,
                    min_bid_usdc=0.0,
                )
        assert exc_info.value.status_code == 409


# ═══════════════════════════════════════════════════════════════════════════════
# TestActivateWill — POST /api/hub/will/{will_id}/activate
# ═══════════════════════════════════════════════════════════════════════════════

class TestActivateWill:

    @pytest.mark.asyncio
    async def test_activate_draft_will_sets_active(self):
        """Activation d'un testament draft → statut active."""
        from hub.hub_will import activate_will

        w = will_row(status="draft")
        mock_db = make_mock_db()
        mock_db._fetchone.return_value = w

        with patch("hub.hub_will.db", mock_db):
            result = await activate_will(
                will_id=w["will_id"],
                testator_hub_id=w["testator_hub_id"],
            )

        assert result["status"] == "active"
        mock_db.raw_execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_activate_wrong_testator_returns_403(self):
        """Seul le testateur peut activer → 403 si autre agent."""
        from hub.hub_will import activate_will
        from fastapi import HTTPException

        w = will_row(testator_hub_id="testator_hub_id_32charsxxxxxxxxx", status="draft")
        mock_db = make_mock_db()
        mock_db._fetchone.return_value = w

        with patch("hub.hub_will.db", mock_db):
            with pytest.raises(HTTPException) as exc_info:
                await activate_will(
                    will_id=w["will_id"],
                    testator_hub_id="impostor_hub_id_32charsxxxxxxxxx",
                )
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_activate_nonexistent_will_returns_404(self):
        """Testament inconnu → 404."""
        from hub.hub_will import activate_will
        from fastapi import HTTPException

        mock_db = make_mock_db()
        mock_db._fetchone.return_value = None

        with patch("hub.hub_will.db", mock_db):
            with pytest.raises(HTTPException) as exc_info:
                await activate_will(
                    will_id="nonexistent_will_id",
                    testator_hub_id="testator_hub_id_32charsxxxxxxxxx",
                )
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_activate_already_active_returns_409(self):
        """Testament déjà actif → 409."""
        from hub.hub_will import activate_will
        from fastapi import HTTPException

        w = will_row(status="active")
        mock_db = make_mock_db()
        mock_db._fetchone.return_value = w

        with patch("hub.hub_will.db", mock_db):
            with pytest.raises(HTTPException) as exc_info:
                await activate_will(
                    will_id=w["will_id"],
                    testator_hub_id=w["testator_hub_id"],
                )
        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_activate_auction_sets_auction_end_ts(self):
        """Activation d'une auction sans auction_end_ts → maintenant + 7 jours."""
        from hub.hub_will import activate_will

        w = will_row(will_type="auction", status="draft", auction_end_ts=None)
        mock_db = make_mock_db()
        mock_db._fetchone.return_value = w

        before = int(time.time())
        with patch("hub.hub_will.db", mock_db):
            result = await activate_will(
                will_id=w["will_id"],
                testator_hub_id=w["testator_hub_id"],
            )
        after = int(time.time())

        seven_days = 7 * 24 * 3600
        assert result["auction_end_ts"] is not None
        assert before + seven_days <= result["auction_end_ts"] <= after + seven_days


# ═══════════════════════════════════════════════════════════════════════════════
# TestAcceptWill — POST /api/hub/will/accept/{will_id}
# ═══════════════════════════════════════════════════════════════════════════════

class TestAcceptWill:

    @pytest.mark.asyncio
    async def test_accept_will_by_successor_ok(self):
        """Successeur désigné accepte → résultat ok."""
        from hub.hub_will import accept_will

        w = will_row(
            will_type="simple",
            status="active",
            successor_hub_id="successor_hub_id_32charsxxxxxxxx",
        )
        mock_db = make_mock_db()
        mock_db._fetchone.return_value = w

        with patch("hub.hub_will.db", mock_db):
            result = await accept_will(
                will_id=w["will_id"],
                successor_hub_id="successor_hub_id_32charsxxxxxxxx",
            )

        assert result["accepted"] is True

    @pytest.mark.asyncio
    async def test_accept_will_wrong_successor_returns_403(self):
        """Un autre agent tente d'accepter → 403."""
        from hub.hub_will import accept_will
        from fastapi import HTTPException

        w = will_row(
            will_type="simple",
            status="active",
            successor_hub_id="successor_hub_id_32charsxxxxxxxx",
        )
        mock_db = make_mock_db()
        mock_db._fetchone.return_value = w

        with patch("hub.hub_will.db", mock_db):
            with pytest.raises(HTTPException) as exc_info:
                await accept_will(
                    will_id=w["will_id"],
                    successor_hub_id="impostor_hub_id_32charsxxxxxxxxx",
                )
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_accept_will_auction_type_returns_400(self):
        """Testament de type auction ne peut pas être accepté directement → 400."""
        from hub.hub_will import accept_will
        from fastapi import HTTPException

        w = will_row(will_type="auction", status="active", successor_hub_id=None)
        mock_db = make_mock_db()
        mock_db._fetchone.return_value = w

        with patch("hub.hub_will.db", mock_db):
            with pytest.raises(HTTPException) as exc_info:
                await accept_will(
                    will_id=w["will_id"],
                    successor_hub_id="someone_hub_id_32charsxxxxxxxxxx",
                )
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_accept_will_not_active_returns_409(self):
        """Testament non actif → 409."""
        from hub.hub_will import accept_will
        from fastapi import HTTPException

        w = will_row(will_type="simple", status="draft",
                     successor_hub_id="successor_hub_id_32charsxxxxxxxx")
        mock_db = make_mock_db()
        mock_db._fetchone.return_value = w

        with patch("hub.hub_will.db", mock_db):
            with pytest.raises(HTTPException) as exc_info:
                await accept_will(
                    will_id=w["will_id"],
                    successor_hub_id="successor_hub_id_32charsxxxxxxxx",
                )
        assert exc_info.value.status_code == 409


# ═══════════════════════════════════════════════════════════════════════════════
# TestExecuteWill — POST /api/hub/will/execute/{will_id}
# ═══════════════════════════════════════════════════════════════════════════════

class TestExecuteWill:

    @pytest.mark.asyncio
    async def test_execute_will_transfers_score(self):
        """Exécution du testament → score successeur augmenté."""
        from hub.hub_will import execute_will

        now = int(time.time())
        # testator mort depuis 80h (> 72h grace period)
        dead_heartbeat = now - (80 * 3600)
        testator = hub_agent_row(
            hub_id="testator_hub_id_32charsxxxxxxxxx",
            score=100,
            last_heartbeat=dead_heartbeat,
        )
        successor = hub_agent_row(
            hub_id="successor_hub_id_32charsxxxxxxxx",
            score=20,
        )
        w = will_row(
            will_type="simple",
            status="active",
            successor_hub_id="successor_hub_id_32charsxxxxxxxx",
            transfer_score_pct=0.8,
            transfer_lineage=0,
            grace_period_hours=72,
        )

        mock_db = make_mock_db()
        mock_db._fetchone.side_effect = [
            w,         # fetch will
            testator,  # fetch testator
            successor, # fetch successor
        ]
        mock_db._fetchall.return_value = []  # pas d'enfants lineage

        with patch("hub.hub_will.db", mock_db):
            result = await execute_will(will_id=w["will_id"])

        # score attendu: min(100, 20 + round(100 * 0.8)) = min(100, 20+80) = 100
        assert result["new_successor_score"] == 100
        assert result["status"] == "executed"

    @pytest.mark.asyncio
    async def test_execute_will_heartbeat_too_recent_returns_403(self):
        """Heartbeat récent (< 72h) → 403, exécution refusée."""
        from hub.hub_will import execute_will
        from fastapi import HTTPException

        now = int(time.time())
        # heartbeat il y a seulement 10h
        recent_heartbeat = now - (10 * 3600)
        testator = hub_agent_row(
            hub_id="testator_hub_id_32charsxxxxxxxxx",
            last_heartbeat=recent_heartbeat,
        )
        w = will_row(
            will_type="simple",
            status="active",
            successor_hub_id="successor_hub_id_32charsxxxxxxxx",
            grace_period_hours=72,
        )

        mock_db = make_mock_db()
        mock_db._fetchone.side_effect = [w, testator]

        with patch("hub.hub_will.db", mock_db):
            with pytest.raises(HTTPException) as exc_info:
                await execute_will(will_id=w["will_id"])
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_execute_will_not_active_returns_409(self):
        """Testament pas en statut active → 409."""
        from hub.hub_will import execute_will
        from fastapi import HTTPException

        w = will_row(status="draft")
        mock_db = make_mock_db()
        mock_db._fetchone.return_value = w

        with patch("hub.hub_will.db", mock_db):
            with pytest.raises(HTTPException) as exc_info:
                await execute_will(will_id=w["will_id"])
        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_execute_will_nonexistent_returns_404(self):
        """Testament inconnu → 404."""
        from hub.hub_will import execute_will
        from fastapi import HTTPException

        mock_db = make_mock_db()
        mock_db._fetchone.return_value = None

        with patch("hub.hub_will.db", mock_db):
            with pytest.raises(HTTPException) as exc_info:
                await execute_will(will_id="nonexistent_will_id")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_execute_will_score_capped_at_100(self):
        """Score successeur après transfert plafonné à 100."""
        from hub.hub_will import execute_will

        now = int(time.time())
        dead_heartbeat = now - (80 * 3600)
        testator = hub_agent_row(
            hub_id="testator_hub_id_32charsxxxxxxxxx",
            score=100,
            last_heartbeat=dead_heartbeat,
        )
        successor = hub_agent_row(
            hub_id="successor_hub_id_32charsxxxxxxxx",
            score=90,
        )
        w = will_row(
            will_type="simple",
            status="active",
            successor_hub_id="successor_hub_id_32charsxxxxxxxx",
            transfer_score_pct=1.0,
            transfer_lineage=0,
            grace_period_hours=72,
        )

        mock_db = make_mock_db()
        mock_db._fetchone.side_effect = [w, testator, successor]
        mock_db._fetchall.return_value = []

        with patch("hub.hub_will.db", mock_db):
            result = await execute_will(will_id=w["will_id"])

        assert result["new_successor_score"] == 100

    @pytest.mark.asyncio
    async def test_execute_will_auction_uses_top_bidder(self):
        """Testament de type auction → le gagnant de l'enchère devient successeur."""
        from hub.hub_will import execute_will

        now = int(time.time())
        dead_heartbeat = now - (80 * 3600)
        testator = hub_agent_row(
            hub_id="testator_hub_id_32charsxxxxxxxxx",
            score=60,
            last_heartbeat=dead_heartbeat,
        )
        winner = hub_agent_row(
            hub_id="winner__hub_id_32charsxxxxxxxxxx",
            score=10,
        )
        w = will_row(
            will_type="auction",
            status="active",
            successor_hub_id=None,
            transfer_score_pct=0.5,
            transfer_lineage=0,
            grace_period_hours=72,
            auction_end_ts=now - 3600,  # enchère terminée
        )
        top_bid = {
            "bid_id": uuid.uuid4().hex,
            "will_id": w["will_id"],
            "bidder_hub_id": "winner__hub_id_32charsxxxxxxxxxx",
            "amount_usdc": 100.0,
            "bid_ts": now - 7200,
            "status": "active",
        }

        mock_db = make_mock_db()
        mock_db._fetchone.side_effect = [
            w,        # fetch will
            testator, # fetch testator
            top_bid,  # fetch top bid
            winner,   # fetch winner as successor
        ]
        mock_db._fetchall.return_value = []

        with patch("hub.hub_will.db", mock_db):
            result = await execute_will(will_id=w["will_id"])

        assert result["successor_hub_id"] == "winner__hub_id_32charsxxxxxxxxxx"
        assert result["status"] == "executed"

    @pytest.mark.asyncio
    async def test_execute_will_transfers_lineage_children(self):
        """transfer_lineage=1 → enfants reparentés vers successeur."""
        from hub.hub_will import execute_will

        now = int(time.time())
        dead_heartbeat = now - (80 * 3600)
        testator = hub_agent_row(
            hub_id="testator_hub_id_32charsxxxxxxxxx",
            score=50,
            last_heartbeat=dead_heartbeat,
        )
        successor = hub_agent_row(
            hub_id="successor_hub_id_32charsxxxxxxxx",
            score=10,
        )
        w = will_row(
            will_type="simple",
            status="active",
            successor_hub_id="successor_hub_id_32charsxxxxxxxx",
            transfer_score_pct=0.5,
            transfer_lineage=1,
            grace_period_hours=72,
        )
        child = {"lineage_id": uuid.uuid4().hex, "child_hub_id": "child1_hub_id_32charsxxxxxxxxx"}

        mock_db = make_mock_db()
        mock_db._fetchone.side_effect = [w, testator, successor]
        mock_db._fetchall.return_value = [child]

        with patch("hub.hub_will.db", mock_db):
            result = await execute_will(will_id=w["will_id"])

        # raw_execute doit avoir été appelé au moins 3 fois :
        # 1) UPDATE score successeur, 2) UPDATE lineage enfants, 3) UPDATE will status
        assert mock_db.raw_execute.call_count >= 3


# ═══════════════════════════════════════════════════════════════════════════════
# TestRevokeWill — DELETE /api/hub/will/{will_id}
# ═══════════════════════════════════════════════════════════════════════════════

class TestRevokeWill:

    @pytest.mark.asyncio
    async def test_revoke_draft_will_sets_revoked(self):
        """Révocation d'un testament draft → statut revoked."""
        from hub.hub_will import revoke_will

        w = will_row(status="draft")
        mock_db = make_mock_db()
        mock_db._fetchone.return_value = w

        with patch("hub.hub_will.db", mock_db):
            result = await revoke_will(
                will_id=w["will_id"],
                testator_hub_id=w["testator_hub_id"],
            )

        assert result["status"] == "revoked"
        mock_db.raw_execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_revoke_active_will_sets_revoked(self):
        """Révocation d'un testament active → statut revoked."""
        from hub.hub_will import revoke_will

        w = will_row(status="active")
        mock_db = make_mock_db()
        mock_db._fetchone.return_value = w

        with patch("hub.hub_will.db", mock_db):
            result = await revoke_will(
                will_id=w["will_id"],
                testator_hub_id=w["testator_hub_id"],
            )

        assert result["status"] == "revoked"

    @pytest.mark.asyncio
    async def test_revoke_wrong_testator_returns_403(self):
        """Révocation par un autre agent → 403."""
        from hub.hub_will import revoke_will
        from fastapi import HTTPException

        w = will_row(testator_hub_id="testator_hub_id_32charsxxxxxxxxx", status="active")
        mock_db = make_mock_db()
        mock_db._fetchone.return_value = w

        with patch("hub.hub_will.db", mock_db):
            with pytest.raises(HTTPException) as exc_info:
                await revoke_will(
                    will_id=w["will_id"],
                    testator_hub_id="impostor_hub_id_32charsxxxxxxxxx",
                )
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_revoke_executed_will_returns_409(self):
        """Testament déjà exécuté → 409."""
        from hub.hub_will import revoke_will
        from fastapi import HTTPException

        w = will_row(status="executed")
        mock_db = make_mock_db()
        mock_db._fetchone.return_value = w

        with patch("hub.hub_will.db", mock_db):
            with pytest.raises(HTTPException) as exc_info:
                await revoke_will(
                    will_id=w["will_id"],
                    testator_hub_id=w["testator_hub_id"],
                )
        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_revoke_nonexistent_will_returns_404(self):
        """Testament inconnu → 404."""
        from hub.hub_will import revoke_will
        from fastapi import HTTPException

        mock_db = make_mock_db()
        mock_db._fetchone.return_value = None

        with patch("hub.hub_will.db", mock_db):
            with pytest.raises(HTTPException) as exc_info:
                await revoke_will(
                    will_id="nonexistent_will_id",
                    testator_hub_id="testator_hub_id_32charsxxxxxxxxx",
                )
        assert exc_info.value.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# TestPlaceBid — POST /api/hub/market/wills/{will_id}/bid
# ═══════════════════════════════════════════════════════════════════════════════

class TestPlaceBid:

    @pytest.mark.asyncio
    async def test_place_bid_valid(self):
        """Enchère valide sur une auction active → enregistrée."""
        from hub.hub_will import place_bid

        w = will_row(
            will_type="auction",
            status="active",
            min_bid_usdc=5.0,
            auction_end_ts=int(time.time()) + 3600,
        )
        mock_db = make_mock_db()
        mock_db._fetchone.side_effect = [
            w,    # fetch will
            None, # pas d'enchère existante de ce bidder
        ]

        with patch("hub.hub_will.db", mock_db):
            result = await place_bid(
                will_id=w["will_id"],
                bidder_hub_id="bidder__hub_id_32charsxxxxxxxxxx",
                amount_usdc=50.0,
            )

        assert "bid_id" in result
        mock_db.raw_execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_place_bid_below_minimum_returns_400(self):
        """Enchère sous le minimum → 400."""
        from hub.hub_will import place_bid
        from fastapi import HTTPException

        w = will_row(
            will_type="auction",
            status="active",
            min_bid_usdc=100.0,
            auction_end_ts=int(time.time()) + 3600,
        )
        mock_db = make_mock_db()
        mock_db._fetchone.return_value = w

        with patch("hub.hub_will.db", mock_db):
            with pytest.raises(HTTPException) as exc_info:
                await place_bid(
                    will_id=w["will_id"],
                    bidder_hub_id="bidder__hub_id_32charsxxxxxxxxxx",
                    amount_usdc=50.0,
                )
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_place_bid_not_auction_returns_400(self):
        """Testament non-auction → 400."""
        from hub.hub_will import place_bid
        from fastapi import HTTPException

        w = will_row(will_type="simple", status="active")
        mock_db = make_mock_db()
        mock_db._fetchone.return_value = w

        with patch("hub.hub_will.db", mock_db):
            with pytest.raises(HTTPException) as exc_info:
                await place_bid(
                    will_id=w["will_id"],
                    bidder_hub_id="bidder__hub_id_32charsxxxxxxxxxx",
                    amount_usdc=50.0,
                )
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_place_bid_auction_ended_returns_410(self):
        """Enchère après la date de fin → 410."""
        from hub.hub_will import place_bid
        from fastapi import HTTPException

        w = will_row(
            will_type="auction",
            status="active",
            min_bid_usdc=0.0,
            auction_end_ts=int(time.time()) - 3600,  # terminée
        )
        mock_db = make_mock_db()
        mock_db._fetchone.return_value = w

        with patch("hub.hub_will.db", mock_db):
            with pytest.raises(HTTPException) as exc_info:
                await place_bid(
                    will_id=w["will_id"],
                    bidder_hub_id="bidder__hub_id_32charsxxxxxxxxxx",
                    amount_usdc=50.0,
                )
        assert exc_info.value.status_code == 410

    @pytest.mark.asyncio
    async def test_place_bid_below_current_highest_returns_400(self):
        """Enchère inférieure à l'enchère actuelle la plus haute → 400."""
        from hub.hub_will import place_bid
        from fastapi import HTTPException

        w = will_row(
            will_type="auction",
            status="active",
            min_bid_usdc=10.0,
            auction_end_ts=int(time.time()) + 3600,
        )
        existing_bid = {
            "bid_id": uuid.uuid4().hex,
            "will_id": w["will_id"],
            "bidder_hub_id": "other_bidder_32charsxxxxxxxxxx",
            "amount_usdc": 200.0,
            "bid_ts": int(time.time()) - 100,
            "status": "active",
        }
        mock_db = make_mock_db()
        mock_db._fetchone.side_effect = [
            w,
            existing_bid,  # enchère existante de CE bidder (None ici, on teste le top bid global)
        ]
        # top bid via _fetchall
        mock_db._fetchall.return_value = [existing_bid]

        with patch("hub.hub_will.db", mock_db):
            with pytest.raises(HTTPException) as exc_info:
                await place_bid(
                    will_id=w["will_id"],
                    bidder_hub_id="bidder__hub_id_32charsxxxxxxxxxx",
                    amount_usdc=50.0,  # < 200.0
                )
        assert exc_info.value.status_code == 400


# ═══════════════════════════════════════════════════════════════════════════════
# TestListAuctionWills — GET /api/hub/market/wills
# ═══════════════════════════════════════════════════════════════════════════════

class TestListAuctionWills:

    @pytest.mark.asyncio
    async def test_list_returns_active_auctions_only(self):
        """Seules les auctions en statut active avec auction_end_ts futur sont listées."""
        from hub.hub_will import list_auction_wills

        now = int(time.time())
        active_auction = will_row(
            will_type="auction",
            status="active",
            auction_end_ts=now + 3600,
        )
        mock_db = make_mock_db()
        mock_db._fetchall.return_value = [active_auction]

        with patch("hub.hub_will.db", mock_db):
            result = await list_auction_wills()

        assert result["total"] >= 0
        assert "wills" in result

    @pytest.mark.asyncio
    async def test_list_empty_returns_zero(self):
        """Aucune auction active → total 0."""
        from hub.hub_will import list_auction_wills

        mock_db = make_mock_db()
        mock_db._fetchall.return_value = []

        with patch("hub.hub_will.db", mock_db):
            result = await list_auction_wills()

        assert result["total"] == 0
        assert result["wills"] == []


# ═══════════════════════════════════════════════════════════════════════════════
# TestComputeScoreTransfer — règles métier pures
# ═══════════════════════════════════════════════════════════════════════════════

class TestComputeScoreTransfer:

    def test_score_transfer_basic(self):
        """Transfert de score : min(100, succ + round(test * pct))."""
        from hub.hub_will import _compute_score_transfer

        assert _compute_score_transfer(testator_score=100, successor_score=20, pct=0.8) == 100
        assert _compute_score_transfer(testator_score=50, successor_score=10, pct=0.5) == 35
        assert _compute_score_transfer(testator_score=0, successor_score=50, pct=1.0) == 50

    def test_score_transfer_capped_at_100(self):
        """Score ne dépasse jamais 100."""
        from hub.hub_will import _compute_score_transfer

        for ts in range(0, 101, 10):
            for ss in range(0, 101, 10):
                result = _compute_score_transfer(ts, ss, 1.0)
                assert result <= 100

    def test_score_transfer_never_negative(self):
        """Score jamais négatif."""
        from hub.hub_will import _compute_score_transfer

        assert _compute_score_transfer(0, 0, 0.0) == 0
        assert _compute_score_transfer(0, 5, 0.0) == 5
