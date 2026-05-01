"""Tests TDD pour MAXIA Hub Phase 4 — lignées d'agents (spawn, héritage, arbre).

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
):
    sk, pk = make_keypair()
    return {
        "hub_id": hub_id,
        "name": name,
        "score": score,
        "status": status,
        "public_key": public_key or pk,
        "did": f"did:maxia:solana:{hub_id[:8]}",
        "uaid": uuid.uuid4().hex,
        "endpoint": "https://agent.example.com",
        "chain": "solana",
        "framework": "custom",
        "capabilities": '["text"]',
        "birth_ts": int(time.time()) - 3600,
        "last_heartbeat": int(time.time()) - 60,
        "uptime_30d": 95.0,
        "wallet": f"Wallet{hub_id[:8]}",
        "corpus_opt_out": 0,
    }, sk


def lineage_row(
    lineage_id: str = None,
    parent_hub_id: str = "parent_hub_id_32charsxxxxxxxxx",
    child_hub_id: str = "child__hub_id_32charsxxxxxxxxx",
    generation: int = 1,
    inherited_score_bonus: int = 5,
    status: str = "pending",
    reason: str = "test spawn",
    created_at: int = None,
    accepted_at: int = None,
):
    return {
        "lineage_id": lineage_id or uuid.uuid4().hex,
        "parent_hub_id": parent_hub_id,
        "child_hub_id": child_hub_id,
        "generation": generation,
        "inherited_score_bonus": inherited_score_bonus,
        "status": status,
        "reason": reason,
        "created_at": created_at or int(time.time()),
        "accepted_at": accepted_at,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# TestHubSpawn — POST /api/hub/lineage/spawn
# ═══════════════════════════════════════════════════════════════════════════════

class TestHubSpawn:

    @pytest.mark.asyncio
    async def test_spawn_valid_creates_pending_lineage(self):
        """Spawn valide → enregistrement en statut 'pending'."""
        from hub.hub_lineage import spawn_lineage

        parent_row, parent_sk = hub_agent_row(
            hub_id="parent_hub_id_32charsxxxxxxxxx",
            score=50,
        )
        child_row, _ = hub_agent_row(
            hub_id="child__hub_id_32charsxxxxxxxxx",
            score=10,
        )

        mock_db = make_mock_db()
        # parent existe et a un score >= 10
        # child existe
        # child n'a pas encore de parent (aucune lignée active)
        # parent a < 10 enfants actifs
        mock_db._fetchone.side_effect = [
            parent_row,   # fetch parent
            child_row,    # fetch child
            None,         # child n'a pas déjà un parent dans hub_lineage
            {"cnt": 2},   # parent a 2 enfants actifs (< 10)
            # _get_agent_generation remonte la chaîne via _fetchone :
            None,         # parent n'a pas de parent → génération 0
        ]

        with patch("hub.hub_lineage.db", mock_db):
            result = await spawn_lineage(
                parent_hub_id="parent_hub_id_32charsxxxxxxxxx",
                child_hub_id="child__hub_id_32charsxxxxxxxxx",
                reason="test spawn",
            )

        assert result["status"] == "pending"
        assert "lineage_id" in result
        mock_db.raw_execute.assert_called()

    @pytest.mark.asyncio
    async def test_spawn_parent_score_too_low_returns_403(self):
        """Parent avec score < 10 → 403."""
        from hub.hub_lineage import spawn_lineage
        from fastapi import HTTPException

        parent_row, _ = hub_agent_row(
            hub_id="parent_hub_id_32charsxxxxxxxxx",
            score=5,  # trop bas
        )
        child_row, _ = hub_agent_row(hub_id="child__hub_id_32charsxxxxxxxxx")

        mock_db = make_mock_db()
        mock_db._fetchone.side_effect = [parent_row, child_row]

        with patch("hub.hub_lineage.db", mock_db):
            with pytest.raises(HTTPException) as exc_info:
                await spawn_lineage(
                    parent_hub_id="parent_hub_id_32charsxxxxxxxxx",
                    child_hub_id="child__hub_id_32charsxxxxxxxxx",
                    reason="test",
                )
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_spawn_child_already_has_parent_returns_409(self):
        """Enfant déjà lié → 409."""
        from hub.hub_lineage import spawn_lineage
        from fastapi import HTTPException

        parent_row, _ = hub_agent_row(
            hub_id="parent_hub_id_32charsxxxxxxxxx",
            score=50,
        )
        child_row, _ = hub_agent_row(hub_id="child__hub_id_32charsxxxxxxxxx")

        mock_db = make_mock_db()
        existing_lineage = lineage_row(status="active")
        mock_db._fetchone.side_effect = [
            parent_row,
            child_row,
            existing_lineage,  # child a déjà un parent
        ]

        with patch("hub.hub_lineage.db", mock_db):
            with pytest.raises(HTTPException) as exc_info:
                await spawn_lineage(
                    parent_hub_id="parent_hub_id_32charsxxxxxxxxx",
                    child_hub_id="child__hub_id_32charsxxxxxxxxx",
                    reason="test",
                )
        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_spawn_self_returns_400(self):
        """Parent == enfant → 400."""
        from hub.hub_lineage import spawn_lineage
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await spawn_lineage(
                parent_hub_id="same_hub_id_32chars_xxxxxxxxxxx",
                child_hub_id="same_hub_id_32chars_xxxxxxxxxxx",
                reason="test",
            )
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_spawn_max_children_exceeded_returns_403(self):
        """Plus de 10 enfants actifs → 403."""
        from hub.hub_lineage import spawn_lineage
        from fastapi import HTTPException

        parent_row, _ = hub_agent_row(
            hub_id="parent_hub_id_32charsxxxxxxxxx",
            score=80,
        )
        child_row, _ = hub_agent_row(hub_id="child__hub_id_32charsxxxxxxxxx")

        mock_db = make_mock_db()
        mock_db._fetchone.side_effect = [
            parent_row,
            child_row,
            None,          # child n'a pas de parent
            {"cnt": 10},   # parent a déjà 10 enfants → max atteint
        ]

        with patch("hub.hub_lineage.db", mock_db):
            with pytest.raises(HTTPException) as exc_info:
                await spawn_lineage(
                    parent_hub_id="parent_hub_id_32charsxxxxxxxxx",
                    child_hub_id="child__hub_id_32charsxxxxxxxxx",
                    reason="test",
                )
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_spawn_unknown_child_returns_404(self):
        """Enfant inconnu → 404."""
        from hub.hub_lineage import spawn_lineage
        from fastapi import HTTPException

        parent_row, _ = hub_agent_row(
            hub_id="parent_hub_id_32charsxxxxxxxxx",
            score=50,
        )

        mock_db = make_mock_db()
        mock_db._fetchone.side_effect = [
            parent_row,
            None,  # child introuvable
        ]

        with patch("hub.hub_lineage.db", mock_db):
            with pytest.raises(HTTPException) as exc_info:
                await spawn_lineage(
                    parent_hub_id="parent_hub_id_32charsxxxxxxxxx",
                    child_hub_id="unknown_child_hub_32charsxxxxxx",
                    reason="test",
                )
        assert exc_info.value.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# TestHubAccept — POST /api/hub/lineage/accept/{lineage_id}
# ═══════════════════════════════════════════════════════════════════════════════

class TestHubAccept:

    @pytest.mark.asyncio
    async def test_accept_pending_lineage_activates(self):
        """Acceptation d'une lignée pending → statut active."""
        from hub.hub_lineage import accept_lineage

        parent_row, _ = hub_agent_row(
            hub_id="parent_hub_id_32charsxxxxxxxxx",
            score=80,
        )
        child_row, child_sk = hub_agent_row(
            hub_id="child__hub_id_32charsxxxxxxxxx",
            score=20,
        )
        lin_row = lineage_row(
            parent_hub_id="parent_hub_id_32charsxxxxxxxxx",
            child_hub_id="child__hub_id_32charsxxxxxxxxx",
            inherited_score_bonus=8,
            status="pending",
        )

        mock_db = make_mock_db()
        mock_db._fetchone.side_effect = [
            lin_row,     # fetch lineage
            parent_row,  # fetch parent
            child_row,   # fetch child
        ]

        with patch("hub.hub_lineage.db", mock_db):
            result = await accept_lineage(
                lineage_id=lin_row["lineage_id"],
                child_hub_id="child__hub_id_32charsxxxxxxxxx",
            )

        assert result["status"] == "active"
        # Vérifier que raw_execute a été appelé (UPDATE statut + score)
        assert mock_db.raw_execute.call_count >= 1

    @pytest.mark.asyncio
    async def test_accept_applies_score_bonus(self):
        """L'activation applique le bonus de score à l'enfant."""
        from hub.hub_lineage import accept_lineage

        parent_row, _ = hub_agent_row(
            hub_id="parent_hub_id_32charsxxxxxxxxx",
            score=80,
        )
        child_row, _ = hub_agent_row(
            hub_id="child__hub_id_32charsxxxxxxxxx",
            score=20,
        )
        lin_row = lineage_row(
            parent_hub_id="parent_hub_id_32charsxxxxxxxxx",
            child_hub_id="child__hub_id_32charsxxxxxxxxx",
            inherited_score_bonus=8,
            status="pending",
        )

        mock_db = make_mock_db()
        mock_db._fetchone.side_effect = [lin_row, parent_row, child_row]

        with patch("hub.hub_lineage.db", mock_db):
            result = await accept_lineage(
                lineage_id=lin_row["lineage_id"],
                child_hub_id="child__hub_id_32charsxxxxxxxxx",
            )

        # Le résultat doit mentionner le bonus appliqué
        assert result.get("inherited_bonus") == 8

    @pytest.mark.asyncio
    async def test_accept_score_bonus_is_10_percent_of_parent(self):
        """Le bonus hérité est exactement round(parent_score * 0.10)."""
        from hub.hub_lineage import _compute_inherited_bonus

        assert _compute_inherited_bonus(parent_score=80) == 8
        assert _compute_inherited_bonus(parent_score=100) == 10
        assert _compute_inherited_bonus(parent_score=15) == 2  # round(1.5) = 2
        assert _compute_inherited_bonus(parent_score=0) == 0
        assert _compute_inherited_bonus(parent_score=7) == 1   # round(0.7) = 1

    @pytest.mark.asyncio
    async def test_accept_score_capped_at_100(self):
        """Score enfant après bonus ne dépasse pas 100."""
        from hub.hub_lineage import accept_lineage

        parent_row, _ = hub_agent_row(
            hub_id="parent_hub_id_32charsxxxxxxxxx",
            score=100,
        )
        child_row, _ = hub_agent_row(
            hub_id="child__hub_id_32charsxxxxxxxxx",
            score=95,  # score déjà haut
        )
        lin_row = lineage_row(
            parent_hub_id="parent_hub_id_32charsxxxxxxxxx",
            child_hub_id="child__hub_id_32charsxxxxxxxxx",
            inherited_score_bonus=10,
            status="pending",
        )

        mock_db = make_mock_db()
        mock_db._fetchone.side_effect = [lin_row, parent_row, child_row]

        executed_sqls = []
        async def capture_execute(sql, params=None):
            executed_sqls.append((sql, params))
        mock_db.raw_execute.side_effect = capture_execute

        with patch("hub.hub_lineage.db", mock_db):
            result = await accept_lineage(
                lineage_id=lin_row["lineage_id"],
                child_hub_id="child__hub_id_32charsxxxxxxxxx",
            )

        # Vérifier via le résultat retourné que le score après ne dépasse pas 100
        assert result["child_score_after"] <= 100, (
            f"Score dépasse 100 : {result['child_score_after']} — plafonnement manquant"
        )
        # 95 + 10 = 105 → plafonné à 100
        assert result["child_score_after"] == 100

    @pytest.mark.asyncio
    async def test_accept_unknown_lineage_returns_404(self):
        """Lignée inconnue → 404."""
        from hub.hub_lineage import accept_lineage
        from fastapi import HTTPException

        mock_db = make_mock_db()
        mock_db._fetchone.return_value = None

        with patch("hub.hub_lineage.db", mock_db):
            with pytest.raises(HTTPException) as exc_info:
                await accept_lineage(
                    lineage_id="nonexistent_lineage_id",
                    child_hub_id="child__hub_id_32charsxxxxxxxxx",
                )
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_accept_wrong_child_returns_403(self):
        """Un agent autre que l'enfant tente d'accepter → 403."""
        from hub.hub_lineage import accept_lineage
        from fastapi import HTTPException

        lin_row = lineage_row(
            parent_hub_id="parent_hub_id_32charsxxxxxxxxx",
            child_hub_id="child__hub_id_32charsxxxxxxxxx",
            status="pending",
        )

        mock_db = make_mock_db()
        mock_db._fetchone.return_value = lin_row

        with patch("hub.hub_lineage.db", mock_db):
            with pytest.raises(HTTPException) as exc_info:
                await accept_lineage(
                    lineage_id=lin_row["lineage_id"],
                    child_hub_id="impostor_hub_id_32charsxxxxxxx",  # mauvais agent
                )
        assert exc_info.value.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════════
# TestHubLineageTree — GET /api/hub/lineage/{hub_id}
# ═══════════════════════════════════════════════════════════════════════════════

class TestHubLineageTree:

    @pytest.mark.asyncio
    async def test_get_lineage_returns_tree(self):
        """L'arbre retourné contient root + dynasty_size + max_generation."""
        from hub.hub_lineage import get_lineage_tree

        root_row, _ = hub_agent_row(
            hub_id="root___hub_id_32charsxxxxxxxxx",
            name="RootAgent",
            score=80,
        )

        mock_db = make_mock_db()
        # root agent exists
        mock_db._fetchone.side_effect = [
            root_row,  # fetch agent
            None,      # root n'a pas de parent
        ]
        # Pas d'enfants
        mock_db._fetchall.return_value = []

        with patch("hub.hub_lineage.db", mock_db):
            result = await get_lineage_tree("root___hub_id_32charsxxxxxxxxx")

        assert "root" in result
        assert "dynasty_size" in result
        assert "max_generation" in result
        assert result["root"]["hub_id"] == "root___hub_id_32charsxxxxxxxxx"

    @pytest.mark.asyncio
    async def test_get_lineage_max_3_generations(self):
        """L'arbre est limité à 3 générations (gen 0, 1, 2)."""
        from hub.hub_lineage import _build_lineage_tree

        # Simuler un arbre à 4 niveaux — la fonction doit s'arrêter à la génération 2
        root = {
            "hub_id": "root___hub_id_32charsxxxxxxxxx",
            "name": "Root",
            "score": 80,
            "generation": 0,
        }

        mock_db = make_mock_db()
        # G1 children
        g1_child = {"hub_id": "g1_____hub_id_32charsxxxxxxxxx", "name": "G1", "score": 40}
        # G2 children
        g2_child = {"hub_id": "g2_____hub_id_32charsxxxxxxxxx", "name": "G2", "score": 20}
        # G3 children — ne doivent PAS apparaître
        g3_child = {"hub_id": "g3_____hub_id_32charsxxxxxxxxx", "name": "G3", "score": 10}

        # _fetchall appelé successivement pour G1, G2, G3
        mock_db._fetchall.side_effect = [
            [g1_child],   # enfants du root (G1)
            [g2_child],   # enfants de G1 (G2)
            [g3_child],   # enfants de G2 (G3) — doit être ignoré
        ]

        with patch("hub.hub_lineage.db", mock_db):
            tree = await _build_lineage_tree(
                hub_id="root___hub_id_32charsxxxxxxxxx",
                name="Root",
                score=80,
                current_gen=0,
                max_gen=2,
            )

        # G3 ne doit pas avoir de children chargés
        g1_node = tree["children"][0]
        g2_node = g1_node["children"][0]
        assert g2_node["children"] == []

    @pytest.mark.asyncio
    async def test_get_lineage_dynasty_badge_conditions(self):
        """Badge DYNASTY : gen==0, score>=50, dynasty_size>=3."""
        from hub.hub_lineage import _compute_dynasty_badge

        # Toutes conditions remplies
        assert _compute_dynasty_badge(generation=0, score=50, dynasty_size=3) is True
        assert _compute_dynasty_badge(generation=0, score=80, dynasty_size=5) is True

        # Conditions non remplies
        assert _compute_dynasty_badge(generation=1, score=80, dynasty_size=5) is False  # pas fondateur
        assert _compute_dynasty_badge(generation=0, score=49, dynasty_size=3) is False  # score trop bas
        assert _compute_dynasty_badge(generation=0, score=80, dynasty_size=2) is False  # trop petit

    @pytest.mark.asyncio
    async def test_get_lineage_unknown_hub_returns_404(self):
        """Hub inconnu → 404."""
        from hub.hub_lineage import get_lineage_tree
        from fastapi import HTTPException

        mock_db = make_mock_db()
        mock_db._fetchone.return_value = None

        with patch("hub.hub_lineage.db", mock_db):
            with pytest.raises(HTTPException) as exc_info:
                await get_lineage_tree("nonexistent_hub_id_32chars_xxxx")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_list_children_returns_direct_children_only(self):
        """list_direct_children retourne uniquement les enfants directs actifs."""
        from hub.hub_lineage import list_direct_children

        parent_row, _ = hub_agent_row(
            hub_id="parent_hub_id_32charsxxxxxxxxx",
            score=80,
        )

        child1 = {"hub_id": "child1_hub_id_32charsxxxxxxxxx", "name": "Child1",
                  "score": 30, "status": "active"}
        child2 = {"hub_id": "child2_hub_id_32charsxxxxxxxxx", "name": "Child2",
                  "score": 20, "status": "active"}

        mock_db = make_mock_db()
        mock_db._fetchone.return_value = parent_row
        mock_db._fetchall.return_value = [child1, child2]

        with patch("hub.hub_lineage.db", mock_db):
            result = await list_direct_children("parent_hub_id_32charsxxxxxxxxx")

        assert result["total"] == 2
        assert len(result["children"]) == 2

    @pytest.mark.asyncio
    async def test_list_children_excludes_pending(self):
        """Les lignées en statut 'pending' n'apparaissent pas dans les enfants actifs."""
        from hub.hub_lineage import list_direct_children

        parent_row, _ = hub_agent_row(
            hub_id="parent_hub_id_32charsxxxxxxxxx",
            score=80,
        )

        mock_db = make_mock_db()
        mock_db._fetchone.return_value = parent_row
        # La query filtre sur status='active' → retourne liste vide
        mock_db._fetchall.return_value = []

        with patch("hub.hub_lineage.db", mock_db):
            result = await list_direct_children("parent_hub_id_32charsxxxxxxxxx")

        assert result["total"] == 0


# ═══════════════════════════════════════════════════════════════════════════════
# TestHubLineageRules — règles métier
# ═══════════════════════════════════════════════════════════════════════════════

class TestHubLineageRules:

    @pytest.mark.asyncio
    async def test_cycle_detection_returns_400(self):
        """Un ancêtre ne peut pas devenir enfant de l'un de ses descendants."""
        from hub.hub_lineage import spawn_lineage, _is_ancestor
        from fastapi import HTTPException

        # _is_ancestor doit détecter que child est en réalité un ancêtre du parent
        mock_db = make_mock_db()

        # Chaîne : grandparent → parent. On veut spawner grandparent comme enfant de parent → cycle
        # _fetchall retourne la chaîne d'ancêtres du parent
        mock_db._fetchall.return_value = [
            {"parent_hub_id": "grandparent_hub_32charsxxxxxxxxx"}  # parent est enfant de grandparent
        ]

        with patch("hub.hub_lineage.db", mock_db):
            is_ancestor = await _is_ancestor(
                potential_ancestor="grandparent_hub_32charsxxxxxxxxx",
                hub_id="parent_hub_id_32charsxxxxxxxxx",
            )
        assert is_ancestor is True

    @pytest.mark.asyncio
    async def test_generation_4_blocked_returns_403(self):
        """Un enfant en génération 3 ne peut pas spawner (max gen = 2)."""
        from hub.hub_lineage import spawn_lineage
        from fastapi import HTTPException

        # Parent est en génération 3 → son enfant serait en génération 4 → interdit
        parent_row, _ = hub_agent_row(
            hub_id="parent_hub_id_32charsxxxxxxxxx",
            score=50,
        )
        child_row, _ = hub_agent_row(
            hub_id="child__hub_id_32charsxxxxxxxxx",
            score=10,
        )

        mock_db = make_mock_db()
        # parent existe, score ok
        # child existe
        # child n'a pas de parent
        # parent a < 10 enfants
        # MAIS parent est en génération 3 (depth=3 dans la chaîne)
        mock_db._fetchone.side_effect = [
            parent_row,
            child_row,
            None,         # child n'a pas de parent
            {"cnt": 0},   # parent a 0 enfants
        ]

        # _get_agent_generation retourne 3 pour le parent
        with patch("hub.hub_lineage.db", mock_db), \
             patch("hub.hub_lineage._get_agent_generation", AsyncMock(return_value=3)):
            with pytest.raises(HTTPException) as exc_info:
                await spawn_lineage(
                    parent_hub_id="parent_hub_id_32charsxxxxxxxxx",
                    child_hub_id="child__hub_id_32charsxxxxxxxxx",
                    reason="test",
                )
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_dynasty_size_counts_all_descendants(self):
        """dynasty_size compte le fondateur + tous les descendants (toutes générations)."""
        from hub.hub_lineage import _count_dynasty_size

        mock_db = make_mock_db()

        # Root a 2 enfants, chaque enfant a 1 petit-enfant → total = 1 + 2 + 2 = 5
        mock_db._fetchall.side_effect = [
            [  # G1 enfants du root
                {"hub_id": "g1a_hub_id_32charsxxxxxxxxxx"},
                {"hub_id": "g1b_hub_id_32charsxxxxxxxxxx"},
            ],
            [{"hub_id": "g2a_hub_id_32charsxxxxxxxxxx"}],  # G2 enfants de g1a
            [{"hub_id": "g2b_hub_id_32charsxxxxxxxxxx"}],  # G2 enfants de g1b
            [],  # G3 enfants de g2a (aucun)
            [],  # G3 enfants de g2b (aucun)
        ]

        with patch("hub.hub_lineage.db", mock_db):
            size = await _count_dynasty_size("root___hub_id_32charsxxxxxxxxx")

        assert size == 5  # root + 2 G1 + 2 G2

    @pytest.mark.asyncio
    async def test_inherited_bonus_not_cumulated_artificially(self):
        """Le bonus est basé sur le score RÉEL du parent, pas un score gonflé."""
        from hub.hub_lineage import _compute_inherited_bonus

        # Score 10 → bonus 1 (round(1.0))
        # Le bonus ne peut jamais dépasser round(100 * 0.10) = 10
        for score in range(0, 101):
            bonus = _compute_inherited_bonus(score)
            assert bonus == round(score * 0.10)
            assert 0 <= bonus <= 10

    @pytest.mark.asyncio
    async def test_spawn_cycle_in_spawn_flow_returns_400(self):
        """spawn_lineage avec cycle détecté → 400."""
        from hub.hub_lineage import spawn_lineage
        from fastapi import HTTPException

        parent_row, _ = hub_agent_row(
            hub_id="parent_hub_id_32charsxxxxxxxxx",
            score=50,
        )
        child_row, _ = hub_agent_row(
            hub_id="child__hub_id_32charsxxxxxxxxx",
            score=10,
        )

        mock_db = make_mock_db()
        mock_db._fetchone.side_effect = [
            parent_row,
            child_row,
            None,       # child n'a pas de parent dans hub_lineage
            {"cnt": 0}, # parent a 0 enfants actifs
        ]

        # _get_agent_generation retourne 0 (fondateur)
        # Mais _is_ancestor détecte que child_hub_id est ancêtre de parent_hub_id
        with patch("hub.hub_lineage.db", mock_db), \
             patch("hub.hub_lineage._get_agent_generation", AsyncMock(return_value=0)), \
             patch("hub.hub_lineage._is_ancestor", AsyncMock(return_value=True)):
            with pytest.raises(HTTPException) as exc_info:
                await spawn_lineage(
                    parent_hub_id="parent_hub_id_32charsxxxxxxxxx",
                    child_hub_id="child__hub_id_32charsxxxxxxxxx",
                    reason="test",
                )
        assert exc_info.value.status_code == 400
