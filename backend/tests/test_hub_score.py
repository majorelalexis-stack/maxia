"""Tests TDD pour MAXIA Hub Phase 2 — score composite + peer reviews.

Ordre TDD : ces tests sont écrits AVANT l'implémentation.
Ils doivent tous échouer (RED) au premier lancement.
"""
import json
import time
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ─── Mock DB factory ─────────────────────────────────────────────────────────

def make_mock_db():
    """Crée un mock db compatible avec les patterns database.py."""
    mock = MagicMock()
    mock.raw_execute = AsyncMock(return_value=None)
    mock.raw_execute_fetchall = AsyncMock(return_value=[])
    mock._fetchone = AsyncMock(return_value=None)
    return mock


def hub_agent_row(
    hub_id: str = "hub_abc123",
    wallet: str = "WalletABC",
    birth_ts: int | None = None,
    uptime_30d: float = 80.0,
    score: int = 0,
) -> dict:
    """Ligne hub_agents minimale pour les tests."""
    return {
        "hub_id": hub_id,
        "wallet": wallet,
        "birth_ts": birth_ts or (int(time.time()) - 5 * 30 * 24 * 3600),  # 5 mois
        "uptime_30d": uptime_30d,
        "score": score,
        "chain": "solana",
        "framework": "custom",
        "status": "active",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# TestScoreComponents — test de chaque composante isolément
# ═══════════════════════════════════════════════════════════════════════════════

class TestScoreComponents:

    # ── escrow_completion_rate ────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_escrow_completion_rate_no_history_returns_50(self):
        """Sans historique escrow → neutre 50 (pas pénalisé)."""
        from hub.hub_score import _fetch_score_components

        mock_db = make_mock_db()
        mock_db.raw_execute_fetchall = AsyncMock(return_value=[])

        with patch("hub.hub_score.db", mock_db):
            agent = hub_agent_row()
            comps = await _fetch_score_components(agent["hub_id"], agent["wallet"])

        assert comps["escrow_completion_rate"] == 50

    @pytest.mark.asyncio
    async def test_escrow_completion_rate_all_completed_returns_100(self):
        """Toutes les transactions released → 100."""
        from hub.hub_score import _fetch_score_components

        mock_db = make_mock_db()
        agent = hub_agent_row()

        # escrow_records : 5 released, 0 disputed, 0 expired
        escrow_rows = [{"released": 5, "disputed": 0, "expired": 0}]

        async def fake_fetchall(sql, params=()):
            sql_lower = sql.lower()
            if "escrow_records" in sql_lower:
                return escrow_rows
            return []

        mock_db.raw_execute_fetchall = fake_fetchall
        mock_db._fetchone = AsyncMock(return_value=agent)

        with patch("hub.hub_score.db", mock_db):
            comps = await _fetch_score_components(agent["hub_id"], agent["wallet"])

        assert comps["escrow_completion_rate"] == 100

    @pytest.mark.asyncio
    async def test_escrow_completion_rate_all_disputed_returns_0(self):
        """Toutes les transactions disputées → 0."""
        from hub.hub_score import _fetch_score_components

        mock_db = make_mock_db()
        agent = hub_agent_row()

        escrow_rows = [{"released": 0, "disputed": 5, "expired": 0}]

        async def fake_fetchall(sql, params=()):
            sql_lower = sql.lower()
            if "escrow_records" in sql_lower:
                return escrow_rows
            return []

        mock_db.raw_execute_fetchall = fake_fetchall
        mock_db._fetchone = AsyncMock(return_value=agent)

        with patch("hub.hub_score.db", mock_db):
            comps = await _fetch_score_components(agent["hub_id"], agent["wallet"])

        assert comps["escrow_completion_rate"] == 0

    @pytest.mark.asyncio
    async def test_uptime_read_from_hub_agents(self):
        """uptime_30d est lu directement depuis hub_agents."""
        from hub.hub_score import _fetch_score_components

        mock_db = make_mock_db()
        agent = hub_agent_row(uptime_30d=73.5)
        mock_db._fetchone = AsyncMock(return_value=agent)
        mock_db.raw_execute_fetchall = AsyncMock(return_value=[])

        with patch("hub.hub_score.db", mock_db):
            comps = await _fetch_score_components(agent["hub_id"], agent["wallet"])

        # uptime_30d passé via argument
        assert comps["uptime_30d"] == 73.5

    @pytest.mark.asyncio
    async def test_stake_tier_no_stake_returns_0(self):
        """Sans stake → stake_tier = 0."""
        from hub.hub_score import _fetch_score_components

        mock_db = make_mock_db()
        mock_db.raw_execute_fetchall = AsyncMock(return_value=[])

        with patch("hub.hub_score.db", mock_db):
            agent = hub_agent_row()
            comps = await _fetch_score_components(agent["hub_id"], agent["wallet"])

        assert comps["stake_tier"] == 0.0

    @pytest.mark.asyncio
    async def test_stake_tier_1000_usdc_returns_1(self):
        """1000 USDC stake → stake_tier = 1.0 (plafond)."""
        from hub.hub_score import _fetch_score_components

        mock_db = make_mock_db()

        stake_row = [{"data": json.dumps({"amount": 1000, "status": "active"})}]

        async def fake_fetchall(sql, params=()):
            if "stakes" in sql.lower():
                return stake_row
            return []

        mock_db.raw_execute_fetchall = fake_fetchall

        with patch("hub.hub_score.db", mock_db):
            agent = hub_agent_row()
            comps = await _fetch_score_components(agent["hub_id"], agent["wallet"])

        assert comps["stake_tier"] == 1.0

    @pytest.mark.asyncio
    async def test_stake_tier_capped_at_1(self):
        """5000 USDC stake → stake_tier toujours 1.0 (min(1.0, amount/1000))."""
        from hub.hub_score import _fetch_score_components

        mock_db = make_mock_db()

        stake_row = [{"data": json.dumps({"amount": 5000, "status": "active"})}]

        async def fake_fetchall(sql, params=()):
            if "stakes" in sql.lower():
                return stake_row
            return []

        mock_db.raw_execute_fetchall = fake_fetchall

        with patch("hub.hub_score.db", mock_db):
            agent = hub_agent_row()
            comps = await _fetch_score_components(agent["hub_id"], agent["wallet"])

        assert comps["stake_tier"] == 1.0

    @pytest.mark.asyncio
    async def test_age_bonus_capped_at_10(self):
        """Agent de 24 mois → age_bonus = 10 (min(10, months))."""
        from hub.hub_score import _fetch_score_components

        # birth_ts = 24 mois ago
        birth_ts = int(time.time()) - 24 * 30 * 24 * 3600
        agent = hub_agent_row(birth_ts=birth_ts)

        mock_db = make_mock_db()
        mock_db._fetchone = AsyncMock(return_value=agent)
        mock_db.raw_execute_fetchall = AsyncMock(return_value=[])

        with patch("hub.hub_score.db", mock_db):
            comps = await _fetch_score_components(agent["hub_id"], agent["wallet"])

        assert comps["age_bonus"] == 10

    @pytest.mark.asyncio
    async def test_age_bonus_new_agent_is_zero(self):
        """Agent nouvellement créé (< 1 mois) → age_bonus = 0."""
        from hub.hub_score import _fetch_score_components

        # birth_ts = 1 heure ago
        birth_ts = int(time.time()) - 3600
        agent = hub_agent_row(birth_ts=birth_ts)

        mock_db = make_mock_db()
        mock_db._fetchone = AsyncMock(return_value=agent)
        mock_db.raw_execute_fetchall = AsyncMock(return_value=[])

        with patch("hub.hub_score.db", mock_db):
            comps = await _fetch_score_components(agent["hub_id"], agent["wallet"])

        assert comps["age_bonus"] == 0

    @pytest.mark.asyncio
    async def test_dispute_rate_zero_tx_returns_0(self):
        """Sans transactions → dispute_rate = 0 (éviter division par zéro)."""
        from hub.hub_score import _fetch_score_components

        mock_db = make_mock_db()
        mock_db.raw_execute_fetchall = AsyncMock(return_value=[])

        with patch("hub.hub_score.db", mock_db):
            agent = hub_agent_row()
            comps = await _fetch_score_components(agent["hub_id"], agent["wallet"])

        assert comps["dispute_rate"] == 0

    @pytest.mark.asyncio
    async def test_peer_review_avg_no_reviews_returns_neutral(self):
        """Sans reviews → peer_review_avg = 2.5 (neutre)."""
        from hub.hub_score import _fetch_score_components

        mock_db = make_mock_db()
        mock_db.raw_execute_fetchall = AsyncMock(return_value=[])

        with patch("hub.hub_score.db", mock_db):
            agent = hub_agent_row()
            comps = await _fetch_score_components(agent["hub_id"], agent["wallet"])

        assert comps["peer_review_avg"] == 2.5


# ═══════════════════════════════════════════════════════════════════════════════
# TestScoreFormula — test de la formule finale
# ═══════════════════════════════════════════════════════════════════════════════

class TestScoreFormula:

    def _make_components(self, **overrides) -> dict:
        """Composantes neutres par défaut."""
        base = {
            "escrow_completion_rate": 50,
            "uptime_30d": 80.0,
            "peer_review_avg": 2.5,
            "stake_tier": 0.0,
            "age_bonus": 5,
            "dispute_rate": 0,
            "x402_unlocked": True,
        }
        return {**base, **overrides}

    def test_score_clamped_0_100(self):
        """Le score final est toujours entre 0 et 100."""
        from hub.hub_score import _compute_score_from_components

        # Composantes extrêmes négatives
        comps = self._make_components(
            escrow_completion_rate=0,
            uptime_30d=0,
            peer_review_avg=1.0,
            stake_tier=0,
            age_bonus=0,
            dispute_rate=100,
        )
        score = _compute_score_from_components(comps)
        assert 0 <= score <= 100

        # Composantes extrêmes positives
        comps_max = self._make_components(
            escrow_completion_rate=100,
            uptime_30d=100,
            peer_review_avg=5.0,
            stake_tier=1.0,
            age_bonus=10,
            dispute_rate=0,
        )
        score_max = _compute_score_from_components(comps_max)
        assert 0 <= score_max <= 100

    def test_score_perfect_agent_returns_100(self):
        """Agent parfait (toutes composantes au max, 0 dispute) → 100."""
        from hub.hub_score import _compute_score_from_components

        comps = self._make_components(
            escrow_completion_rate=100,
            uptime_30d=100,
            peer_review_avg=5.0,
            stake_tier=1.0,
            age_bonus=10,
            dispute_rate=0,
        )
        score = _compute_score_from_components(comps)
        assert score == 100

    def test_score_dispute_penalty_applied(self):
        """dispute_rate non-nul réduit le score."""
        from hub.hub_score import _compute_score_from_components

        comps_no_dispute = self._make_components(dispute_rate=0)
        comps_with_dispute = self._make_components(dispute_rate=50)

        score_no = _compute_score_from_components(comps_no_dispute)
        score_with = _compute_score_from_components(comps_with_dispute)

        assert score_with < score_no

    def test_x402_cap_applied_when_no_transaction(self):
        """Si x402_unlocked=False → score plafonné à 30."""
        from hub.hub_score import _compute_score_from_components

        comps = self._make_components(
            escrow_completion_rate=100,
            uptime_30d=100,
            peer_review_avg=5.0,
            stake_tier=1.0,
            age_bonus=10,
            dispute_rate=0,
            x402_unlocked=False,
        )
        score = _compute_score_from_components(comps)
        assert score <= 30

    def test_x402_no_cap_when_has_transaction(self):
        """Si x402_unlocked=True → pas de plafond à 30."""
        from hub.hub_score import _compute_score_from_components

        comps = self._make_components(
            escrow_completion_rate=100,
            uptime_30d=100,
            peer_review_avg=5.0,
            stake_tier=1.0,
            age_bonus=10,
            dispute_rate=0,
            x402_unlocked=True,
        )
        score = _compute_score_from_components(comps)
        assert score > 30

    def test_grade_aaa_at_95(self):
        """Score ≥ 95 → grade AAA."""
        from hub.hub_score import _score_to_grade
        assert _score_to_grade(95) == "AAA"
        assert _score_to_grade(100) == "AAA"

    def test_grade_aa_at_85(self):
        """Score ≥ 85 et < 95 → grade AA."""
        from hub.hub_score import _score_to_grade
        assert _score_to_grade(85) == "AA"
        assert _score_to_grade(94) == "AA"

    def test_grade_ccc_below_45(self):
        """Score < 45 → grade CCC."""
        from hub.hub_score import _score_to_grade
        assert _score_to_grade(44) == "CCC"
        assert _score_to_grade(0) == "CCC"

    def test_grade_b_at_45(self):
        """Score ≥ 45 et < 55 → grade B."""
        from hub.hub_score import _score_to_grade
        assert _score_to_grade(45) == "B"
        assert _score_to_grade(54) == "B"

    def test_score_formula_weighted_correctly(self):
        """Vérifie les poids de la formule.

        raw = (ecr*40 + uptime*20 + review*20 + stake*10 + age*10) - (dispute*30)
        Avec : ecr=100, uptime=100, review=5/5→100%, stake=1→100%, age=10, dispute=0
        raw = 40 + 20 + 20 + 10 + 10 - 0 = 100
        """
        from hub.hub_score import _compute_score_from_components

        comps = {
            "escrow_completion_rate": 100,  # × 40 = 40
            "uptime_30d": 100,              # × 20 = 20
            "peer_review_avg": 5.0,         # × 20 = 20 (normalisé: 5/5 × 100%)
            "stake_tier": 1.0,              # × 10 = 10
            "age_bonus": 10,                # × 10 = 10
            "dispute_rate": 0,              # - 0
            "x402_unlocked": True,
        }
        score = _compute_score_from_components(comps)
        assert score == 100


# ═══════════════════════════════════════════════════════════════════════════════
# TestScoreEndpoints — test des routes HTTP
# ═══════════════════════════════════════════════════════════════════════════════

class TestScoreEndpoints:

    @pytest.mark.asyncio
    async def test_get_score_unknown_hub_returns_404(self):
        """GET /api/hub/score/{hub_id} → 404 si agent inconnu."""
        from hub.hub_score import get_hub_score
        from fastapi import HTTPException

        mock_db = make_mock_db()
        mock_db._fetchone = AsyncMock(return_value=None)

        with patch("hub.hub_score.db", mock_db):
            with pytest.raises(HTTPException) as exc:
                await get_hub_score("unknown_hub_id_xyz")

        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_get_score_returns_all_components(self):
        """GET /api/hub/score/{hub_id} → retourne score + toutes les composantes."""
        from hub.hub_score import get_hub_score

        mock_db = make_mock_db()
        agent = hub_agent_row(hub_id="hub_test1", wallet="WalletTest1")
        mock_db._fetchone = AsyncMock(return_value=agent)
        mock_db.raw_execute_fetchall = AsyncMock(return_value=[])

        with patch("hub.hub_score.db", mock_db):
            result = await get_hub_score("hub_test1")

        assert "score" in result
        assert "grade" in result
        assert "components" in result
        assert "x402_unlocked" in result
        assert "calculated_at" in result

        components = result["components"]
        required_keys = {
            "escrow_completion_rate", "uptime_30d", "peer_review_avg",
            "stake_tier", "age_bonus", "dispute_rate",
        }
        for key in required_keys:
            assert key in components, f"Composante manquante: {key}"

    @pytest.mark.asyncio
    async def test_get_score_hub_id_in_response(self):
        """GET /api/hub/score/{hub_id} → hub_id présent dans la réponse."""
        from hub.hub_score import get_hub_score

        mock_db = make_mock_db()
        agent = hub_agent_row(hub_id="hub_test2", wallet="WalletTest2")
        mock_db._fetchone = AsyncMock(return_value=agent)
        mock_db.raw_execute_fetchall = AsyncMock(return_value=[])

        with patch("hub.hub_score.db", mock_db):
            result = await get_hub_score("hub_test2")

        assert result["hub_id"] == "hub_test2"

    @pytest.mark.asyncio
    async def test_recalc_endpoint_persists_score(self):
        """POST /api/hub/score/{hub_id}/recalc → appelle raw_execute pour UPDATE."""
        from hub.hub_score import recalc_hub_score

        mock_db = make_mock_db()
        agent = hub_agent_row(hub_id="hub_recalc", wallet="WalletRecalc")
        mock_db._fetchone = AsyncMock(return_value=agent)
        mock_db.raw_execute_fetchall = AsyncMock(return_value=[])

        with patch("hub.hub_score.db", mock_db):
            result = await recalc_hub_score("hub_recalc")

        # raw_execute doit avoir été appelé au moins une fois (persist)
        mock_db.raw_execute.assert_called()
        assert "score" in result
        assert result["recalculated"] is True

    @pytest.mark.asyncio
    async def test_leaderboard_returns_sorted_by_score(self):
        """GET /api/hub/leaderboard → agents triés par score décroissant."""
        from hub.hub_score import get_hub_leaderboard

        mock_db = make_mock_db()

        # DB retourne agents déjà triés (ORDER BY score DESC)
        rows = [
            {**hub_agent_row(hub_id="hub_a", score=90), "name": "AgentA"},
            {**hub_agent_row(hub_id="hub_b", score=70), "name": "AgentB"},
            {**hub_agent_row(hub_id="hub_c", score=50), "name": "AgentC"},
        ]
        mock_db.raw_execute_fetchall = AsyncMock(return_value=rows)

        with patch("hub.hub_score.db", mock_db):
            result = await get_hub_leaderboard(limit=50, chain=None, framework=None)

        assert "leaderboard" in result
        lb = result["leaderboard"]
        assert len(lb) == 3
        # Vérifier l'ordre décroissant
        scores = [e["score"] for e in lb]
        assert scores == sorted(scores, reverse=True)

    @pytest.mark.asyncio
    async def test_leaderboard_filter_by_chain(self):
        """GET /api/hub/leaderboard?chain=solana → requête filtrée par chain."""
        from hub.hub_score import get_hub_leaderboard

        mock_db = make_mock_db()
        mock_db.raw_execute_fetchall = AsyncMock(return_value=[])

        with patch("hub.hub_score.db", mock_db):
            await get_hub_leaderboard(limit=50, chain="solana", framework=None)

        # Vérifier que la requête SQL contient le filtre chain
        call_args = mock_db.raw_execute_fetchall.call_args
        sql = call_args[0][0].lower()
        params = call_args[0][1]
        assert "chain" in sql or "solana" in str(params)

    @pytest.mark.asyncio
    async def test_leaderboard_max_50(self):
        """GET /api/hub/leaderboard → retourne au plus 50 agents par défaut."""
        from hub.hub_score import get_hub_leaderboard

        mock_db = make_mock_db()
        # 60 agents en DB
        rows = [
            {**hub_agent_row(hub_id=f"hub_{i}", score=60 - i), "name": f"Agent{i}"}
            for i in range(60)
        ]
        mock_db.raw_execute_fetchall = AsyncMock(return_value=rows[:50])

        with patch("hub.hub_score.db", mock_db):
            result = await get_hub_leaderboard(limit=50, chain=None, framework=None)

        assert len(result["leaderboard"]) <= 50


# ═══════════════════════════════════════════════════════════════════════════════
# TestPeerReview — test des routes peer review
# ═══════════════════════════════════════════════════════════════════════════════

class TestPeerReview:

    def _make_review_request(self, **overrides):
        """Payload review valide par défaut."""
        from hub.hub_models import HubReviewRequest
        base = dict(
            reviewer_hub_id="hub_reviewer",
            reviewed_hub_id="hub_reviewed",
            escrow_id="escrow_abc123",
            rating=4,
            comment="Good service",
        )
        base.update(overrides)
        return HubReviewRequest(**base)

    def _make_reviewer_agent(self, hub_id="hub_reviewer", score=20, wallet="WalletR1"):
        return {
            "hub_id": hub_id,
            "wallet": wallet,
            "score": score,
            "chain": "solana",
        }

    def _make_reviewed_agent(self, hub_id="hub_reviewed", score=30, wallet="WalletR2"):
        return {
            "hub_id": hub_id,
            "wallet": wallet,
            "score": score,
            "chain": "solana",
        }

    @pytest.mark.asyncio
    async def test_review_valid_submission(self):
        """Soumission valide → 200 + review_id dans la réponse."""
        from hub.hub_review import submit_review

        mock_db = make_mock_db()

        reviewer = self._make_reviewer_agent()
        reviewed = self._make_reviewed_agent()

        # escrow commun
        escrow_row = [{"escrow_id": "escrow_abc123", "buyer": "WalletR1", "seller": "WalletR2"}]

        async def fake_fetchone(sql, params=()):
            if "hub_id=?" in sql or "hub_id = ?" in sql:
                hub_id = params[0]
                if hub_id == "hub_reviewer":
                    return reviewer
                if hub_id == "hub_reviewed":
                    return reviewed
            if "hub_reviews" in sql.lower() and "unique" in sql.lower():
                return None  # pas de doublon
            return None

        async def fake_fetchall(sql, params=()):
            if "escrow_records" in sql.lower():
                return escrow_row
            if "hub_reviews" in sql.lower():
                return []
            return []

        mock_db._fetchone = fake_fetchone
        mock_db.raw_execute_fetchall = fake_fetchall

        req = self._make_review_request()

        with patch("hub.hub_review.db", mock_db):
            result = await submit_review(req)

        assert "review_id" in result
        assert result["review_id"]  # non vide
        mock_db.raw_execute.assert_called()  # INSERT effectué

    @pytest.mark.asyncio
    async def test_review_self_review_returns_400(self):
        """Un agent ne peut pas se reviewer lui-même → 400."""
        from hub.hub_review import submit_review
        from fastapi import HTTPException

        mock_db = make_mock_db()
        req = self._make_review_request(
            reviewer_hub_id="hub_same",
            reviewed_hub_id="hub_same",
        )

        with patch("hub.hub_review.db", mock_db):
            with pytest.raises(HTTPException) as exc:
                await submit_review(req)

        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_review_low_reviewer_score_returns_403(self):
        """Reviewer avec score < 15 → 403 Forbidden."""
        from hub.hub_review import submit_review
        from fastapi import HTTPException

        mock_db = make_mock_db()
        reviewer = self._make_reviewer_agent(score=10)  # score < 15
        reviewed = self._make_reviewed_agent()

        async def fake_fetchone(sql, params=()):
            if not params:
                return None
            hub_id = params[0]
            if hub_id == "hub_reviewer":
                return reviewer
            if hub_id == "hub_reviewed":
                return reviewed
            return None

        mock_db._fetchone = fake_fetchone

        req = self._make_review_request()

        with patch("hub.hub_review.db", mock_db):
            with pytest.raises(HTTPException) as exc:
                await submit_review(req)

        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_review_no_common_transaction_returns_403(self):
        """Reviewer sans transaction commune → 403."""
        from hub.hub_review import submit_review
        from fastapi import HTTPException

        mock_db = make_mock_db()
        reviewer = self._make_reviewer_agent(score=20)
        reviewed = self._make_reviewed_agent()

        async def fake_fetchone(sql, params=()):
            if not params:
                return None
            hub_id = params[0]
            if hub_id == "hub_reviewer":
                return reviewer
            if hub_id == "hub_reviewed":
                return reviewed
            return None

        async def fake_fetchall(sql, params=()):
            # Pas d'escrow commun
            return []

        mock_db._fetchone = fake_fetchone
        mock_db.raw_execute_fetchall = fake_fetchall

        req = self._make_review_request()

        with patch("hub.hub_review.db", mock_db):
            with pytest.raises(HTTPException) as exc:
                await submit_review(req)

        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_review_duplicate_escrow_returns_409(self):
        """Reviewer qui review 2× pour le même escrow → 409 Conflict."""
        from hub.hub_review import submit_review
        from fastapi import HTTPException

        mock_db = make_mock_db()
        reviewer = self._make_reviewer_agent(score=20)
        reviewed = self._make_reviewed_agent()

        escrow_row = [{"escrow_id": "escrow_abc123", "buyer": "WalletR1", "seller": "WalletR2"}]
        existing_review = [{"review_id": "rev_xyz", "reviewer_hub_id": "hub_reviewer"}]

        async def fake_fetchone(sql, params=()):
            if not params:
                return None
            hub_id = params[0]
            if hub_id == "hub_reviewer":
                return reviewer
            if hub_id == "hub_reviewed":
                return reviewed
            return None

        async def fake_fetchall(sql, params=()):
            sql_lower = sql.lower()
            if "escrow_records" in sql_lower:
                return escrow_row
            if "hub_reviews" in sql_lower:
                return existing_review  # doublon existant
            return []

        mock_db._fetchone = fake_fetchone
        mock_db.raw_execute_fetchall = fake_fetchall

        req = self._make_review_request()

        with patch("hub.hub_review.db", mock_db):
            with pytest.raises(HTTPException) as exc:
                await submit_review(req)

        assert exc.value.status_code == 409

    @pytest.mark.asyncio
    async def test_review_rating_out_of_range_returns_422(self):
        """Rating hors [1,5] → 422 (validation Pydantic)."""
        from hub.hub_models import HubReviewRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            HubReviewRequest(
                reviewer_hub_id="hub_r",
                reviewed_hub_id="hub_d",
                escrow_id="esc_1",
                rating=6,  # invalide
                comment=None,
            )

        with pytest.raises(ValidationError):
            HubReviewRequest(
                reviewer_hub_id="hub_r",
                reviewed_hub_id="hub_d",
                escrow_id="esc_1",
                rating=0,  # invalide
                comment=None,
            )

    @pytest.mark.asyncio
    async def test_review_comment_too_long_returns_422(self):
        """Commentaire > 500 chars → 422 (validation Pydantic)."""
        from hub.hub_models import HubReviewRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            HubReviewRequest(
                reviewer_hub_id="hub_r",
                reviewed_hub_id="hub_d",
                escrow_id="esc_1",
                rating=3,
                comment="x" * 501,  # trop long
            )

    @pytest.mark.asyncio
    async def test_get_reviews_returns_list(self):
        """GET /api/hub/reviews/{hub_id} → liste de reviews."""
        from hub.hub_review import get_hub_reviews

        mock_db = make_mock_db()

        reviews = [
            {
                "review_id": "rev_001",
                "reviewer_hub_id": "hub_reviewer",
                "reviewed_hub_id": "hub_target",
                "escrow_id": "esc_001",
                "rating": 5,
                "comment": "Excellent",
                "created_at": int(time.time()) - 3600,
            },
            {
                "review_id": "rev_002",
                "reviewer_hub_id": "hub_reviewer2",
                "reviewed_hub_id": "hub_target",
                "escrow_id": "esc_002",
                "rating": 3,
                "comment": None,
                "created_at": int(time.time()) - 7200,
            },
        ]
        mock_db.raw_execute_fetchall = AsyncMock(return_value=reviews)

        with patch("hub.hub_review.db", mock_db):
            result = await get_hub_reviews("hub_target")

        assert "reviews" in result
        assert len(result["reviews"]) == 2
        # Vérifier que reviewer_hub_id est présent (données publiques)
        for r in result["reviews"]:
            assert "reviewer_hub_id" in r
            assert "rating" in r

    @pytest.mark.asyncio
    async def test_get_reviews_unknown_hub_returns_empty_list(self):
        """GET /api/hub/reviews/{unknown} → liste vide (pas 404)."""
        from hub.hub_review import get_hub_reviews

        mock_db = make_mock_db()
        mock_db.raw_execute_fetchall = AsyncMock(return_value=[])

        with patch("hub.hub_review.db", mock_db):
            result = await get_hub_reviews("hub_unknown_xyz")

        assert "reviews" in result
        assert result["reviews"] == []

    @pytest.mark.asyncio
    async def test_get_reviews_count_in_response(self):
        """GET /api/hub/reviews/{hub_id} → count inclus dans la réponse."""
        from hub.hub_review import get_hub_reviews

        mock_db = make_mock_db()
        reviews = [
            {
                "review_id": "rev_001",
                "reviewer_hub_id": "hub_r",
                "reviewed_hub_id": "hub_t",
                "escrow_id": "esc_001",
                "rating": 4,
                "comment": "Good",
                "created_at": int(time.time()),
            }
        ]
        mock_db.raw_execute_fetchall = AsyncMock(return_value=reviews)

        with patch("hub.hub_review.db", mock_db):
            result = await get_hub_reviews("hub_t")

        assert "count" in result
        assert result["count"] == 1


# ═══════════════════════════════════════════════════════════════════════════════
# TestPersistHubScore — test de la fonction persist
# ═══════════════════════════════════════════════════════════════════════════════

class TestPersistHubScore:

    @pytest.mark.asyncio
    async def test_persist_updates_hub_agents(self):
        """persist_hub_score appelle raw_execute avec UPDATE hub_agents."""
        from hub.hub_score import persist_hub_score

        mock_db = make_mock_db()

        with patch("hub.hub_score.db", mock_db):
            await persist_hub_score("hub_abc", 75)

        mock_db.raw_execute.assert_called()
        call_args = mock_db.raw_execute.call_args
        sql = call_args[0][0].lower()
        assert "hub_agents" in sql
        assert "score" in sql

    @pytest.mark.asyncio
    async def test_persist_passes_correct_values(self):
        """persist_hub_score passe les bonnes valeurs (hub_id, score)."""
        from hub.hub_score import persist_hub_score

        mock_db = make_mock_db()

        with patch("hub.hub_score.db", mock_db):
            await persist_hub_score("hub_xyz", 82)

        call_args = mock_db.raw_execute.call_args
        params = call_args[0][1]
        # Les params doivent contenir 82 et "hub_xyz"
        assert 82 in params
        assert "hub_xyz" in params


# ═══════════════════════════════════════════════════════════════════════════════
# TestRecalculateAll — test de recalculate_all_hub_scores
# ═══════════════════════════════════════════════════════════════════════════════

class TestRecalculateAll:

    @pytest.mark.asyncio
    async def test_recalculate_all_returns_count(self):
        """recalculate_all_hub_scores retourne le nb d'agents mis à jour."""
        from hub.hub_score import recalculate_all_hub_scores

        mock_db = make_mock_db()

        agent1 = hub_agent_row(hub_id="hub_1", wallet="W1")
        agent2 = hub_agent_row(hub_id="hub_2", wallet="W2")
        agents = [agent1, agent2]

        # _fetchone retourne le bon agent selon hub_id
        async def fake_fetchone(sql, params=()):
            if not params:
                return None
            hub_id = params[0]
            for a in agents:
                if a["hub_id"] == hub_id:
                    return a
            return None

        async def fake_fetchall(sql, params=()):
            sql_lower = sql.lower()
            if "hub_agents" in sql_lower and "active" in sql_lower:
                return agents
            return []

        mock_db.raw_execute_fetchall = fake_fetchall
        mock_db._fetchone = fake_fetchone

        with patch("hub.hub_score.db", mock_db):
            count = await recalculate_all_hub_scores()

        assert count == 2

    @pytest.mark.asyncio
    async def test_recalculate_all_no_agents_returns_zero(self):
        """recalculate_all_hub_scores avec 0 agents actifs → retourne 0."""
        from hub.hub_score import recalculate_all_hub_scores

        mock_db = make_mock_db()
        mock_db.raw_execute_fetchall = AsyncMock(return_value=[])

        with patch("hub.hub_score.db", mock_db):
            count = await recalculate_all_hub_scores()

        assert count == 0
