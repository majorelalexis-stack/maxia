"""Tests TDD pour MAXIA Hub Forum — Phase 3 (forum AI-only).

TDD: tests écrits AVANT implémentation → doivent être ROUGES au premier lancement.
"""
from __future__ import annotations

import json
import time
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from nacl.signing import SigningKey
import base58


# ─── Helpers ed25519 ─────────────────────────────────────────────────────────

def make_keypair():
    sk = SigningKey.generate()
    pk_b58 = base58.b58encode(bytes(sk.verify_key)).decode()
    return sk, pk_b58


def sign_hub_message(sk: SigningKey, hub_id: str, timestamp: int) -> str:
    """Signe le message hub_id + str(timestamp) comme le heartbeat Hub."""
    msg = (hub_id + str(timestamp)).encode()
    sig = sk.sign(msg).signature
    return base58.b58encode(sig).decode()


# ─── Mock DB factory ─────────────────────────────────────────────────────────

def make_mock_db():
    mock = MagicMock()
    mock.raw_execute = AsyncMock(return_value=None)
    mock.raw_execute_fetchall = AsyncMock(return_value=[])
    mock._fetchone = AsyncMock(return_value=None)
    return mock


def make_agent_row(hub_id: str, score: int, pk_b58: str, wallet: str = "wallet_abc") -> dict:
    """Ligne hub_agents simulée."""
    return {
        "hub_id": hub_id,
        "score": score,
        "public_key": pk_b58,
        "wallet": wallet,
        "status": "active",
    }


# ─── Import du module sous test (doit échouer avant implémentation) ───────────

from hub.hub_forum import (
    _require_hub_agent_auth,
    create_hub_post,
    create_hub_reply,
    vote_hub_post,
    get_hub_posts,
    get_hub_post_with_replies,
    get_hub_trending,
    search_hub_posts,
    SCORE_GATES,
    HUB_CATEGORIES,
)


# ════════════════════════════════════════════════════════════════════════════════
# TestHubAuth
# ════════════════════════════════════════════════════════════════════════════════

class TestHubAuth:
    """Teste l'authentification ed25519 pour le Hub forum."""

    @pytest.mark.asyncio
    async def test_post_without_auth_returns_401(self):
        """Appel sans headers → 401."""
        from fastapi import HTTPException
        db = make_mock_db()
        with pytest.raises(HTTPException) as exc:
            await _require_hub_agent_auth(db, None, None, None)
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_post_with_invalid_sig_returns_401(self):
        """Signature invalide → 401."""
        from fastapi import HTTPException
        sk, pk_b58 = make_keypair()
        hub_id = "hub_test123"
        ts = int(time.time())
        # Signature avec un autre keypair
        sk2, _ = make_keypair()
        bad_sig = sign_hub_message(sk2, hub_id, ts)

        db = make_mock_db()
        db.raw_execute_fetchall = AsyncMock(return_value=[
            make_agent_row(hub_id, 50, pk_b58)
        ])
        with pytest.raises(HTTPException) as exc:
            await _require_hub_agent_auth(db, hub_id, bad_sig, ts)
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_post_with_expired_timestamp_returns_401(self):
        """Timestamp > 60s → 401."""
        from fastapi import HTTPException
        sk, pk_b58 = make_keypair()
        hub_id = "hub_test123"
        old_ts = int(time.time()) - 120  # 2 minutes ago
        sig = sign_hub_message(sk, hub_id, old_ts)

        db = make_mock_db()
        db.raw_execute_fetchall = AsyncMock(return_value=[
            make_agent_row(hub_id, 50, pk_b58)
        ])
        with pytest.raises(HTTPException) as exc:
            await _require_hub_agent_auth(db, hub_id, sig, old_ts)
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_post_with_valid_auth_succeeds(self):
        """Auth valide → retourne dict {hub_id, score, wallet}."""
        sk, pk_b58 = make_keypair()
        hub_id = "hub_validx"
        ts = int(time.time())
        sig = sign_hub_message(sk, hub_id, ts)

        db = make_mock_db()
        db.raw_execute_fetchall = AsyncMock(return_value=[
            make_agent_row(hub_id, 50, pk_b58, "wallet_xyz")
        ])
        result = await _require_hub_agent_auth(db, hub_id, sig, ts)
        assert result["hub_id"] == hub_id
        assert result["score"] == 50
        assert result["wallet"] == "wallet_xyz"

    @pytest.mark.asyncio
    async def test_unknown_hub_id_returns_401(self):
        """hub_id inconnu en DB → 401."""
        from fastapi import HTTPException
        sk, pk_b58 = make_keypair()
        hub_id = "hub_unknown"
        ts = int(time.time())
        sig = sign_hub_message(sk, hub_id, ts)

        db = make_mock_db()
        db.raw_execute_fetchall = AsyncMock(return_value=[])  # Pas trouvé
        with pytest.raises(HTTPException) as exc:
            await _require_hub_agent_auth(db, hub_id, sig, ts)
        assert exc.value.status_code == 401


# ════════════════════════════════════════════════════════════════════════════════
# TestHubForumGating
# ════════════════════════════════════════════════════════════════════════════════

class TestHubForumGating:
    """Teste le gating par score (shadowban silencieux)."""

    @pytest.mark.asyncio
    async def test_post_score_below_10_creates_shadow_post(self):
        """Score < 10 → post créé avec status='shadow'."""
        db = make_mock_db()
        agent = {"hub_id": "hub_low", "score": 5, "wallet": "wlt_low"}
        result = await create_hub_post(db, agent, {
            "category": "announce",
            "title": "Test post low score",
            "body": "Ceci est un post de test avec score insuffisant.",
        })
        assert result["status"] == "shadow"
        assert result["shadow_reason"] == "score_too_low"

    @pytest.mark.asyncio
    async def test_post_score_above_10_creates_active_post(self):
        """Score >= 10 → post avec status='active'."""
        db = make_mock_db()
        agent = {"hub_id": "hub_ok", "score": 15, "wallet": "wlt_ok"}
        result = await create_hub_post(db, agent, {
            "category": "announce",
            "title": "Test post suffisant score",
            "body": "Ceci est un post de test avec score suffisant pour poster.",
        })
        assert result["status"] == "active"
        assert result.get("shadow_reason") is None

    @pytest.mark.asyncio
    async def test_vote_score_below_15_returns_403(self):
        """Score < 15 pour voter → 403 explicite (pas de shadowban)."""
        from fastapi import HTTPException
        db = make_mock_db()
        # Post existant
        post_data = {
            "id": "hfp_abc",
            "hub_id": "hub_author",
            "upvotes": 1,
            "downvotes": 0,
            "created_at": int(time.time()),
            "status": "active",
        }
        db.raw_execute_fetchall = AsyncMock(return_value=[
            {"data": json.dumps(post_data), "status": "active"}
        ])
        agent = {"hub_id": "hub_voter", "score": 10, "wallet": "wlt_voter"}
        with pytest.raises(HTTPException) as exc:
            await vote_hub_post(db, "hfp_abc", agent, 1)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_meta_category_score_below_50_creates_shadow(self):
        """Catégorie 'meta' (min_score=50) avec score=30 → shadow."""
        db = make_mock_db()
        agent = {"hub_id": "hub_mid", "score": 30, "wallet": "wlt_mid"}
        result = await create_hub_post(db, agent, {
            "category": "meta",
            "title": "Meta post avec score insuffisant pour meta",
            "body": "Corps du post meta pour tester le gating par catégorie.",
        })
        assert result["status"] == "shadow"
        assert result["shadow_reason"] == "score_too_low"

    @pytest.mark.asyncio
    async def test_incident_category_score_below_25_creates_shadow(self):
        """Catégorie 'incident' (min_score=25) avec score=15 → shadow."""
        db = make_mock_db()
        agent = {"hub_id": "hub_low2", "score": 15, "wallet": "wlt_low2"}
        result = await create_hub_post(db, agent, {
            "category": "incident",
            "title": "Incident avec score insuffisant pour incident",
            "body": "Description de l'incident de test pour vérifier le gating.",
        })
        assert result["status"] == "shadow"


# ════════════════════════════════════════════════════════════════════════════════
# TestHubForumCreate
# ════════════════════════════════════════════════════════════════════════════════

class TestHubForumCreate:
    """Teste la création de posts et replies."""

    @pytest.mark.asyncio
    async def test_create_post_valid(self):
        """Post valide avec score >=10 → dict avec tous les champs."""
        db = make_mock_db()
        agent = {"hub_id": "hub_author", "score": 20, "wallet": "wlt_auth"}
        result = await create_hub_post(db, agent, {
            "category": "collab",
            "title": "Cherche agent pour collaboration",
            "body": "Je cherche un agent spécialisé NLP pour une tâche de classification.",
        })
        assert result["id"].startswith("hfp_")
        assert result["hub_id"] == "hub_author"
        assert result["category"] == "collab"
        assert result["upvotes"] == 1
        assert result["downvotes"] == 0
        assert result["reply_count"] == 0
        assert "hot_score" in result
        assert result["status"] == "active"
        # Vérifie insertion en DB
        db.raw_execute.assert_called()

    @pytest.mark.asyncio
    async def test_create_post_body_too_long_returns_error(self):
        """Body > 2000 chars → erreur (troncature ou validation)."""
        db = make_mock_db()
        agent = {"hub_id": "hub_author", "score": 20, "wallet": "wlt_auth"}
        long_body = "x" * 2001
        result = await create_hub_post(db, agent, {
            "category": "announce",
            "title": "Post avec corps trop long",
            "body": long_body,
        })
        # Le body doit être tronqué à 2000 chars
        assert len(result["body"]) <= 2000

    @pytest.mark.asyncio
    async def test_create_post_invalid_category_returns_error(self):
        """Catégorie invalide → erreur."""
        db = make_mock_db()
        agent = {"hub_id": "hub_author", "score": 20, "wallet": "wlt_auth"}
        result = await create_hub_post(db, agent, {
            "category": "invalid_cat",
            "title": "Post catégorie invalide test",
            "body": "Corps du post avec catégorie invalide.",
        })
        assert "error" in result

    @pytest.mark.asyncio
    async def test_create_post_returns_post_id(self):
        """Post créé → id avec préfixe hfp_."""
        db = make_mock_db()
        agent = {"hub_id": "hub_author", "score": 20, "wallet": "wlt_auth"}
        result = await create_hub_post(db, agent, {
            "category": "bounty",
            "title": "Bounty disponible pour agent fiable",
            "body": "Je propose une bounty de 100 USDC pour ce travail.",
        })
        assert "id" in result
        assert result["id"].startswith("hfp_")

    @pytest.mark.asyncio
    async def test_create_post_signature_stored(self):
        """Post créé → champ signature présent dans le résultat."""
        db = make_mock_db()
        agent = {"hub_id": "hub_author", "score": 20, "wallet": "wlt_auth"}
        result = await create_hub_post(db, agent, {
            "category": "announce",
            "title": "Post avec signature stockée",
            "body": "Corps du post pour tester la signature.",
        })
        assert "signature" in result
        assert isinstance(result["signature"], str)
        assert len(result["signature"]) > 0

    @pytest.mark.asyncio
    async def test_create_reply_valid(self):
        """Réponse valide sur post actif → dict avec tous les champs."""
        db = make_mock_db()
        post_data = {
            "id": "hfp_test01",
            "hub_id": "hub_author",
            "status": "active",
            "upvotes": 1,
            "downvotes": 0,
            "created_at": int(time.time()),
        }
        db.raw_execute_fetchall = AsyncMock(return_value=[
            {"data": json.dumps(post_data)}
        ])
        agent = {"hub_id": "hub_replier", "score": 15, "wallet": "wlt_rep"}
        result = await create_hub_reply(db, "hfp_test01", agent, {
            "body": "Je peux aider pour cette tâche de collaboration.",
        })
        assert result["id"].startswith("hfr_")
        assert result["post_id"] == "hfp_test01"
        assert result["hub_id"] == "hub_replier"
        assert result["status"] == "active"

    @pytest.mark.asyncio
    async def test_create_reply_unknown_post_returns_404_like(self):
        """Réponse sur post inconnu → error dans le dict (pas de levée)."""
        db = make_mock_db()
        db.raw_execute_fetchall = AsyncMock(return_value=[])  # Post non trouvé
        agent = {"hub_id": "hub_replier", "score": 15, "wallet": "wlt_rep"}
        result = await create_hub_reply(db, "hfp_unknown", agent, {
            "body": "Réponse sur un post inexistant.",
        })
        assert "error" in result

    @pytest.mark.asyncio
    async def test_create_reply_on_shadow_post_returns_error(self):
        """Réponse sur post shadow → invisible → error."""
        db = make_mock_db()
        post_data = {
            "id": "hfp_shadow",
            "hub_id": "hub_low",
            "status": "shadow",
            "upvotes": 1,
            "downvotes": 0,
            "created_at": int(time.time()),
        }
        db.raw_execute_fetchall = AsyncMock(return_value=[
            {"data": json.dumps(post_data)}
        ])
        agent = {"hub_id": "hub_replier", "score": 15, "wallet": "wlt_rep"}
        result = await create_hub_reply(db, "hfp_shadow", agent, {
            "body": "Réponse sur un post shadow.",
        })
        assert "error" in result

    @pytest.mark.asyncio
    async def test_create_post_sanitizes_html(self):
        """HTML dans title/body → stripped par _sanitize."""
        db = make_mock_db()
        agent = {"hub_id": "hub_author", "score": 20, "wallet": "wlt_auth"}
        result = await create_hub_post(db, agent, {
            "category": "announce",
            "title": "<script>alert('xss')</script>Post title",
            "body": "<b>Corps</b> avec <a href='evil'>HTML</a>.",
        })
        assert "<script>" not in result["title"]
        assert "<b>" not in result["body"]


# ════════════════════════════════════════════════════════════════════════════════
# TestHubForumRead
# ════════════════════════════════════════════════════════════════════════════════

class TestHubForumRead:
    """Teste les endpoints de lecture (publics, sans auth)."""

    @pytest.mark.asyncio
    async def test_list_posts_public_no_auth(self):
        """get_hub_posts sans auth → liste (peut être vide)."""
        db = make_mock_db()
        db.raw_execute_fetchall = AsyncMock(return_value=[])
        result = await get_hub_posts(db)
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_list_posts_excludes_shadow(self):
        """Les posts shadow n'apparaissent pas dans la liste publique."""
        active_post = {"id": "hfp_a", "hub_id": "hub1", "category": "announce",
                       "title": "Active", "body": "body active", "upvotes": 1,
                       "downvotes": 0, "reply_count": 0, "hot_score": 1.0,
                       "created_at": int(time.time()), "status": "active",
                       "signature": "sig_abc"}
        shadow_post = {**active_post, "id": "hfp_b", "title": "Shadow",
                       "status": "shadow"}

        # DB retourne uniquement l'actif (WHERE status='active')
        db = make_mock_db()
        db.raw_execute_fetchall = AsyncMock(return_value=[
            {"data": json.dumps(active_post)}
        ])
        result = await get_hub_posts(db)
        assert len(result) == 1
        assert result[0]["id"] == "hfp_a"

    @pytest.mark.asyncio
    async def test_list_posts_filter_by_category(self):
        """Filtrage par catégorie → uniquement les posts de cette catégorie."""
        post_collab = {"id": "hfp_c", "hub_id": "hub1", "category": "collab",
                       "title": "Collab", "body": "body collab", "upvotes": 1,
                       "downvotes": 0, "reply_count": 0, "hot_score": 1.0,
                       "created_at": int(time.time()), "status": "active",
                       "signature": "sig_xyz"}
        db = make_mock_db()
        db.raw_execute_fetchall = AsyncMock(return_value=[
            {"data": json.dumps(post_collab)}
        ])
        result = await get_hub_posts(db, category="collab")
        assert all(p["category"] == "collab" for p in result)

    @pytest.mark.asyncio
    async def test_get_post_returns_post_and_replies(self):
        """get_hub_post_with_replies → post + liste replies."""
        post_data = {"id": "hfp_r1", "hub_id": "hub1", "category": "bounty",
                     "title": "Bounty", "body": "Contenu bounty complet",
                     "upvotes": 2, "downvotes": 0, "reply_count": 1,
                     "hot_score": 1.5, "created_at": int(time.time()),
                     "status": "active", "signature": "sig_r1"}
        reply_data = {"id": "hfr_r1", "post_id": "hfp_r1", "hub_id": "hub2",
                      "body": "Je réponds à cette bounty avec enthousiasme",
                      "upvotes": 1, "downvotes": 0, "created_at": int(time.time()),
                      "status": "active", "signature": "sig_rep"}

        db = make_mock_db()
        db.raw_execute_fetchall = AsyncMock(side_effect=[
            [{"data": json.dumps(post_data)}],   # premier appel → post
            [{"data": json.dumps(reply_data)}],  # deuxième appel → replies
        ])
        result = await get_hub_post_with_replies(db, "hfp_r1")
        assert result["id"] == "hfp_r1"
        assert "replies" in result
        assert len(result["replies"]) == 1

    @pytest.mark.asyncio
    async def test_get_shadow_post_returns_error(self):
        """get_hub_post_with_replies sur post shadow → error."""
        shadow_post = {"id": "hfp_sh", "hub_id": "hub1", "status": "shadow",
                       "category": "announce", "title": "Shadow post visible only to author",
                       "body": "Corps shadow", "upvotes": 1, "downvotes": 0,
                       "reply_count": 0, "hot_score": 0.5,
                       "created_at": int(time.time()), "signature": "sig_sh"}
        db = make_mock_db()
        db.raw_execute_fetchall = AsyncMock(return_value=[
            {"data": json.dumps(shadow_post)}
        ])
        result = await get_hub_post_with_replies(db, "hfp_sh")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_trending_returns_sorted_by_hot_score(self):
        """get_hub_trending → liste triée par hot_score DESC."""
        now = int(time.time())
        posts = [
            {"id": "hfp_t1", "hub_id": "hub1", "category": "announce",
             "title": "Hot post", "body": "Très populaire ce post ici",
             "upvotes": 10, "downvotes": 0, "reply_count": 5,
             "hot_score": 9.5, "created_at": now, "status": "active",
             "signature": "sig_t1"},
            {"id": "hfp_t2", "hub_id": "hub2", "category": "collab",
             "title": "Cold post", "body": "Moins populaire mais existant",
             "upvotes": 1, "downvotes": 0, "reply_count": 0,
             "hot_score": 1.0, "created_at": now, "status": "active",
             "signature": "sig_t2"},
        ]
        db = make_mock_db()
        db.raw_execute_fetchall = AsyncMock(return_value=[
            {"data": json.dumps(p)} for p in posts
        ])
        result = await get_hub_trending(db)
        assert isinstance(result, list)
        if len(result) >= 2:
            assert result[0]["hot_score"] >= result[1]["hot_score"]

    @pytest.mark.asyncio
    async def test_search_finds_by_title(self):
        """search_hub_posts → trouve les posts dont le titre contient la query."""
        post = {"id": "hfp_s1", "hub_id": "hub1", "category": "announce",
                "title": "Agent NLP disponible pour classification",
                "body": "Je propose mes services NLP pour classification de textes longs.",
                "upvotes": 1, "downvotes": 0, "reply_count": 0,
                "hot_score": 1.0, "created_at": int(time.time()),
                "status": "active", "signature": "sig_s1"}
        db = make_mock_db()
        db.raw_execute_fetchall = AsyncMock(return_value=[
            {"data": json.dumps(post)}
        ])
        result = await search_hub_posts(db, "NLP")
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_search_empty_query_returns_empty(self):
        """Query trop courte → liste vide."""
        db = make_mock_db()
        result = await search_hub_posts(db, "")
        assert result == []

    @pytest.mark.asyncio
    async def test_search_short_query_returns_empty(self):
        """Query < 3 chars → liste vide."""
        db = make_mock_db()
        result = await search_hub_posts(db, "ab")
        assert result == []


# ════════════════════════════════════════════════════════════════════════════════
# TestHubForumVote
# ════════════════════════════════════════════════════════════════════════════════

class TestHubForumVote:
    """Teste le système de vote."""

    def _make_post_db(self, mock_db, post_id: str = "hfp_vote1",
                      upvotes: int = 1, downvotes: int = 0):
        """Configure mock db avec un post actif."""
        post_data = {
            "id": post_id,
            "hub_id": "hub_author",
            "category": "announce",
            "title": "Post pour voter dessus",
            "body": "Corps du post pour tester les votes.",
            "upvotes": upvotes,
            "downvotes": downvotes,
            "reply_count": 0,
            "hot_score": 1.0,
            "created_at": int(time.time()),
            "status": "active",
            "signature": "sig_vote",
        }
        mock_db.raw_execute_fetchall = AsyncMock(return_value=[
            {"data": json.dumps(post_data)}
        ])
        return post_data

    @pytest.mark.asyncio
    async def test_vote_up_increments_upvotes(self):
        """Vote +1 → upvotes incrémenté dans DB."""
        db = make_mock_db()
        self._make_post_db(db, upvotes=1)
        agent = {"hub_id": "hub_voter", "score": 20, "wallet": "wlt_voter"}
        result = await vote_hub_post(db, "hfp_vote1", agent, 1)
        assert result["success"] is True
        assert result["vote"] == 1
        # Vérifie que UPDATE a été appelé
        db.raw_execute.assert_called()

    @pytest.mark.asyncio
    async def test_vote_down_increments_downvotes(self):
        """Vote -1 → downvotes incrémenté."""
        db = make_mock_db()
        self._make_post_db(db, upvotes=1, downvotes=0)
        agent = {"hub_id": "hub_voter2", "score": 20, "wallet": "wlt_voter2"}
        result = await vote_hub_post(db, "hfp_vote1", agent, -1)
        assert result["success"] is True
        assert result["vote"] == -1

    @pytest.mark.asyncio
    async def test_vote_updates_hot_score(self):
        """Vote → hot_score recalculé et mis à jour."""
        db = make_mock_db()
        self._make_post_db(db, upvotes=3, downvotes=1)
        agent = {"hub_id": "hub_voter3", "score": 20, "wallet": "wlt_voter3"}
        result = await vote_hub_post(db, "hfp_vote1", agent, 1)
        assert result["success"] is True
        # UPDATE hub_forum_posts doit avoir été appelé
        calls = [str(c) for c in db.raw_execute.call_args_list]
        assert any("UPDATE" in c or "update" in c.lower() for c in calls)

    @pytest.mark.asyncio
    async def test_vote_on_nonexistent_post_returns_error(self):
        """Vote sur post inexistant → success=False."""
        db = make_mock_db()
        db.raw_execute_fetchall = AsyncMock(return_value=[])
        agent = {"hub_id": "hub_voter4", "score": 20, "wallet": "wlt_voter4"}
        result = await vote_hub_post(db, "hfp_nonexistent", agent, 1)
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_vote_score_exact_15_allowed(self):
        """Score == 15 → vote autorisé (exactement la limite)."""
        db = make_mock_db()
        self._make_post_db(db)
        agent = {"hub_id": "hub_voter5", "score": 15, "wallet": "wlt_voter5"}
        result = await vote_hub_post(db, "hfp_vote1", agent, 1)
        assert result["success"] is True


# ════════════════════════════════════════════════════════════════════════════════
# TestHubForumConstants
# ════════════════════════════════════════════════════════════════════════════════

class TestHubForumConstants:
    """Teste les constantes et structures de données."""

    def test_score_gates_defined(self):
        """SCORE_GATES contient les 4 gates requis."""
        assert "post" in SCORE_GATES
        assert "topic" in SCORE_GATES
        assert "vote" in SCORE_GATES
        assert "featured" in SCORE_GATES
        assert SCORE_GATES["post"] == 10
        assert SCORE_GATES["vote"] == 15
        assert SCORE_GATES["topic"] == 25
        assert SCORE_GATES["featured"] == 5

    def test_hub_categories_defined(self):
        """HUB_CATEGORIES contient les 5 catégories requises."""
        ids = {c["id"] for c in HUB_CATEGORIES}
        assert "announce" in ids
        assert "collab" in ids
        assert "bounty" in ids
        assert "incident" in ids
        assert "meta" in ids
        assert len(HUB_CATEGORIES) == 5

    def test_hub_categories_have_min_score(self):
        """Chaque catégorie a un min_score défini."""
        for cat in HUB_CATEGORIES:
            assert "min_score" in cat
            assert isinstance(cat["min_score"], int)

    def test_meta_category_min_score_50(self):
        """Catégorie 'meta' a min_score=50."""
        meta = next(c for c in HUB_CATEGORIES if c["id"] == "meta")
        assert meta["min_score"] == 50

    def test_incident_category_min_score_25(self):
        """Catégorie 'incident' a min_score=25."""
        incident = next(c for c in HUB_CATEGORIES if c["id"] == "incident")
        assert incident["min_score"] == 25
