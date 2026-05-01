"""Tests TDD — Hub score unifié avec boosts R1-R4.

Cas couverts :
  1. Score base 50 + r1=10 + r2=5 + r3=15 + r4=3  → 83
  2. Score base 90 + boosts max (15+10+20+5=50)     → 100 (cap)
  3. Score base 50 + tous boosts à 0               → 50 (inchangé)
  4. Score base 50, r1_boost négatif / None         → traité comme 0
  5. compute_hub_score lit les 4 champs depuis hub_agents
"""
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ─── helpers ──────────────────────────────────────────────────────────────────

def make_mock_db():
    mock = MagicMock()
    mock.raw_execute = AsyncMock(return_value=None)
    mock.raw_execute_fetchall = AsyncMock(return_value=[])
    mock._fetchone = AsyncMock(return_value=None)
    return mock


def hub_agent_row(
    hub_id: str = "hub_unified",
    wallet: str = "WalletU",
    birth_ts: int | None = None,
    uptime_30d: float = 0.0,
    score: int = 0,
    score_r1_boost: float = 0.0,
    score_r2_boost: float = 0.0,
    score_r3_eas: float = 0.0,
    score_r4_ext: float = 0.0,
) -> dict:
    return {
        "hub_id": hub_id,
        "wallet": wallet,
        "birth_ts": birth_ts or int(time.time()),
        "uptime_30d": uptime_30d,
        "score": score,
        "score_r1_boost": score_r1_boost,
        "score_r2_boost": score_r2_boost,
        "score_r3_eas": score_r3_eas,
        "score_r4_ext": score_r4_ext,
        "chain": "solana",
        "framework": "custom",
        "status": "active",
    }


# Composantes de base qui donnent exactement score=50 via la formule
# raw = 100*0.40 + 0*0.20 + 2.5/5*100*0.20 + 0*0.10 + 0*0.10 - 0*0.30
#     = 40 + 0 + 10 + 0 + 0 = 50
_BASE_COMPONENTS_50 = {
    "escrow_completion_rate": 100,
    "uptime_30d": 0.0,
    "peer_review_avg": 2.5,
    "stake_tier": 0.0,
    "age_bonus": 0,
    "dispute_rate": 0,
    "x402_unlocked": True,
}

# Composantes qui donnent score=90 (avant boosts)
# raw = 100*0.40 + 100*0.20 + 5/5*100*0.20 + 1.0*100*0.10 + 0*0.10 - 0*0.30
#     = 40 + 20 + 20 + 10 + 0 = 90
_BASE_COMPONENTS_90 = {
    "escrow_completion_rate": 100,
    "uptime_30d": 100.0,
    "peer_review_avg": 5.0,
    "stake_tier": 1.0,
    "age_bonus": 0,
    "dispute_rate": 0,
    "x402_unlocked": True,
}


# ═══════════════════════════════════════════════════════════════════════════════
# Cas 1 — score_base=50 + boosts partiels → 83
# ═══════════════════════════════════════════════════════════════════════════════

class TestBoostsAddedToBase:

    def test_score_base50_with_partial_boosts_returns_83(self):
        """score_base=50, r1=10, r2=5, r3=15, r4=3 → 50+10+5+15+3 = 83."""
        from hub.hub_score import _compute_score_from_components

        boosts = {
            "score_r1_boost": 10.0,
            "score_r2_boost": 5.0,
            "score_r3_eas": 15.0,
            "score_r4_ext": 3.0,
        }
        comps = {**_BASE_COMPONENTS_50, **boosts}
        score = _compute_score_from_components(comps)
        assert score == 83

    def test_score_formula_gives_50_without_boosts(self):
        """Sanity : composantes _BASE_COMPONENTS_50 donnent bien 50 sans boosts."""
        from hub.hub_score import _compute_score_from_components

        # Sans clés boosts dans le dict → doit retourner 50 (compatibilité)
        score = _compute_score_from_components(_BASE_COMPONENTS_50)
        assert score == 50


# ═══════════════════════════════════════════════════════════════════════════════
# Cas 2 — boosts max → cap 100
# ═══════════════════════════════════════════════════════════════════════════════

class TestBoostsCap100:

    def test_score_base90_with_max_boosts_capped_at_100(self):
        """score_base=90, tous boosts max (15+10+20+5=50) → min(90+50, 100) = 100."""
        from hub.hub_score import _compute_score_from_components

        boosts = {
            "score_r1_boost": 15.0,
            "score_r2_boost": 10.0,
            "score_r3_eas": 20.0,
            "score_r4_ext": 5.0,
        }
        comps = {**_BASE_COMPONENTS_90, **boosts}
        score = _compute_score_from_components(comps)
        assert score == 100

    def test_score_never_exceeds_100_with_any_boosts(self):
        """Quelle que soit la somme de boosts, score <= 100."""
        from hub.hub_score import _compute_score_from_components

        boosts = {
            "score_r1_boost": 99.0,
            "score_r2_boost": 99.0,
            "score_r3_eas": 99.0,
            "score_r4_ext": 99.0,
        }
        comps = {**_BASE_COMPONENTS_90, **boosts}
        score = _compute_score_from_components(comps)
        assert score <= 100


# ═══════════════════════════════════════════════════════════════════════════════
# Cas 3 — boosts à zéro → score inchangé
# ═══════════════════════════════════════════════════════════════════════════════

class TestZeroBoostsNoChange:

    def test_score_base50_zero_boosts_returns_50(self):
        """Tous boosts à 0 → score identique au score sans boosts."""
        from hub.hub_score import _compute_score_from_components

        boosts = {
            "score_r1_boost": 0.0,
            "score_r2_boost": 0.0,
            "score_r3_eas": 0.0,
            "score_r4_ext": 0.0,
        }
        comps = {**_BASE_COMPONENTS_50, **boosts}
        score = _compute_score_from_components(comps)
        assert score == 50

    def test_score_absent_boost_keys_treated_as_zero(self):
        """Dict sans clés boosts → même résultat qu'avec boosts=0."""
        from hub.hub_score import _compute_score_from_components

        score_with_explicit_zeros = _compute_score_from_components({
            **_BASE_COMPONENTS_50,
            "score_r1_boost": 0.0,
            "score_r2_boost": 0.0,
            "score_r3_eas": 0.0,
            "score_r4_ext": 0.0,
        })
        score_without_keys = _compute_score_from_components(_BASE_COMPONENTS_50)

        assert score_with_explicit_zeros == score_without_keys


# ═══════════════════════════════════════════════════════════════════════════════
# Cas 4 — boosts négatifs ou None → traités comme 0
# ═══════════════════════════════════════════════════════════════════════════════

class TestNegativeOrNoneBoosts:

    def test_negative_r1_boost_treated_as_zero(self):
        """r1_boost=-5 → traité comme 0, ne pénalise pas le score."""
        from hub.hub_score import _compute_score_from_components

        comps_neg = {
            **_BASE_COMPONENTS_50,
            "score_r1_boost": -5.0,
            "score_r2_boost": 0.0,
            "score_r3_eas": 0.0,
            "score_r4_ext": 0.0,
        }
        comps_zero = {
            **_BASE_COMPONENTS_50,
            "score_r1_boost": 0.0,
            "score_r2_boost": 0.0,
            "score_r3_eas": 0.0,
            "score_r4_ext": 0.0,
        }
        score_neg = _compute_score_from_components(comps_neg)
        score_zero = _compute_score_from_components(comps_zero)
        assert score_neg == score_zero

    def test_none_boosts_treated_as_zero(self):
        """Boost None → traité comme 0 (valeur SQL NULL)."""
        from hub.hub_score import _compute_score_from_components

        comps = {
            **_BASE_COMPONENTS_50,
            "score_r1_boost": None,
            "score_r2_boost": None,
            "score_r3_eas": None,
            "score_r4_ext": None,
        }
        score = _compute_score_from_components(comps)
        assert score == 50

    def test_all_negative_boosts_same_as_zero(self):
        """Tous les boosts négatifs → même score que boosts=0."""
        from hub.hub_score import _compute_score_from_components

        comps_neg = {
            **_BASE_COMPONENTS_50,
            "score_r1_boost": -15.0,
            "score_r2_boost": -10.0,
            "score_r3_eas": -20.0,
            "score_r4_ext": -5.0,
        }
        score = _compute_score_from_components(comps_neg)
        assert score == 50


# ═══════════════════════════════════════════════════════════════════════════════
# Cas 5 — compute_hub_score lit les 4 champs depuis hub_agents
# ═══════════════════════════════════════════════════════════════════════════════

class TestComputeHubScoreReadsBoostsFromDB:

    @pytest.mark.asyncio
    async def test_compute_hub_score_reads_r1_r2_r3_r4_fields(self):
        """compute_hub_score lit score_r1_boost/r2/r3/r4 depuis hub_agents."""
        from hub.hub_score import compute_hub_score

        mock_db = make_mock_db()
        agent = hub_agent_row(
            hub_id="hub_boosts_test",
            wallet="WalletBT",
            score_r1_boost=10.0,
            score_r2_boost=5.0,
            score_r3_eas=15.0,
            score_r4_ext=3.0,
        )
        mock_db._fetchone = AsyncMock(return_value=agent)
        mock_db.raw_execute_fetchall = AsyncMock(return_value=[])

        with patch("hub.hub_score.db", mock_db):
            result = await compute_hub_score("hub_boosts_test")

        # La requête _fetchone doit avoir inclus les champs boost
        call_args = mock_db._fetchone.call_args_list
        first_call_sql = call_args[0][0][0].lower()
        assert "score_r1_boost" in first_call_sql or "score_r1_boost" in str(call_args)

    @pytest.mark.asyncio
    async def test_compute_hub_score_boosts_affect_final_score(self):
        """compute_hub_score avec boosts non nuls → score > score sans boosts."""
        from hub.hub_score import compute_hub_score

        mock_db_no_boost = make_mock_db()
        agent_no_boost = hub_agent_row(
            hub_id="hub_no_boost",
            wallet="WalletNB",
            uptime_30d=0.0,
            score_r1_boost=0.0,
            score_r2_boost=0.0,
            score_r3_eas=0.0,
            score_r4_ext=0.0,
        )
        mock_db_no_boost._fetchone = AsyncMock(return_value=agent_no_boost)
        mock_db_no_boost.raw_execute_fetchall = AsyncMock(return_value=[])

        mock_db_with_boost = make_mock_db()
        agent_with_boost = hub_agent_row(
            hub_id="hub_with_boost",
            wallet="WalletWB",
            uptime_30d=0.0,
            score_r1_boost=10.0,
            score_r2_boost=5.0,
            score_r3_eas=15.0,
            score_r4_ext=3.0,
        )
        mock_db_with_boost._fetchone = AsyncMock(return_value=agent_with_boost)
        mock_db_with_boost.raw_execute_fetchall = AsyncMock(return_value=[])

        with patch("hub.hub_score.db", mock_db_no_boost):
            result_no_boost = await compute_hub_score("hub_no_boost")

        with patch("hub.hub_score.db", mock_db_with_boost):
            result_with_boost = await compute_hub_score("hub_with_boost")

        assert result_with_boost["score"] > result_no_boost["score"]

    @pytest.mark.asyncio
    async def test_compute_hub_score_boosts_included_in_components(self):
        """compute_hub_score → components inclut les valeurs de boost."""
        from hub.hub_score import compute_hub_score

        mock_db = make_mock_db()
        agent = hub_agent_row(
            hub_id="hub_comp_check",
            wallet="WalletCC",
            score_r1_boost=7.0,
            score_r2_boost=3.0,
            score_r3_eas=12.0,
            score_r4_ext=2.0,
        )
        mock_db._fetchone = AsyncMock(return_value=agent)
        mock_db.raw_execute_fetchall = AsyncMock(return_value=[])

        with patch("hub.hub_score.db", mock_db):
            result = await compute_hub_score("hub_comp_check")

        comps = result["components"]
        assert comps.get("score_r1_boost") == 7.0
        assert comps.get("score_r2_boost") == 3.0
        assert comps.get("score_r3_eas") == 12.0
        assert comps.get("score_r4_ext") == 2.0

    @pytest.mark.asyncio
    async def test_compute_hub_score_null_boosts_in_db_treated_as_zero(self):
        """Boosts NULL en DB → score identique à boosts=0."""
        from hub.hub_score import compute_hub_score

        mock_db_null = make_mock_db()
        agent_null = hub_agent_row(hub_id="hub_null", wallet="WalletN")
        agent_null["score_r1_boost"] = None
        agent_null["score_r2_boost"] = None
        agent_null["score_r3_eas"] = None
        agent_null["score_r4_ext"] = None
        mock_db_null._fetchone = AsyncMock(return_value=agent_null)
        mock_db_null.raw_execute_fetchall = AsyncMock(return_value=[])

        mock_db_zero = make_mock_db()
        agent_zero = hub_agent_row(hub_id="hub_zero", wallet="WalletZ")
        mock_db_zero._fetchone = AsyncMock(return_value=agent_zero)
        mock_db_zero.raw_execute_fetchall = AsyncMock(return_value=[])

        with patch("hub.hub_score.db", mock_db_null):
            result_null = await compute_hub_score("hub_null")

        with patch("hub.hub_score.db", mock_db_zero):
            result_zero = await compute_hub_score("hub_zero")

        assert result_null["score"] == result_zero["score"]
