"""Tests TDD pour MAXIA Hub P6 — /live snapshot autonome.

Ordre TDD : ces tests sont écrits AVANT l'implémentation.
Ils doivent tous échouer (RED) au premier lancement, puis passer
en GREEN après écriture de hub_live.py.
"""
from __future__ import annotations

import json
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ─── Mock DB factory ─────────────────────────────────────────────────────────

def make_mock_db():
    mock = MagicMock()
    mock.raw_execute = AsyncMock(return_value=None)
    mock.raw_execute_fetchall = AsyncMock(return_value=[])
    mock._fetchone = AsyncMock(return_value=None)
    mock._fetchall = AsyncMock(return_value=[])
    return mock


def active_agent(hub_id="hub_1", name="Alpha", score=80, chain="solana", framework="custom"):
    return {
        "hub_id": hub_id,
        "name": name,
        "score": score,
        "chain": chain,
        "framework": framework,
        "status": "active",
    }


def forum_post(post_id="p1", title="My Post", category="general", hot_score=5.0, created_at=None):
    data = json.dumps({"author_hub_id": "hub_1", "title": title, "content": "body", "vote_count": 0})
    return {
        "id": post_id,
        "data": data,
        "category": category,
        "hot_score": hot_score,
        "created_at": created_at or int(time.time()),
        "status": "active",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# TestGradeMapping
# ═══════════════════════════════════════════════════════════════════════════════

class TestGradeMapping:

    def test_grade_aaa(self):
        from hub.hub_live import _score_to_grade
        assert _score_to_grade(95) == "AAA"
        assert _score_to_grade(100) == "AAA"

    def test_grade_aa(self):
        from hub.hub_live import _score_to_grade
        assert _score_to_grade(85) == "AA"
        assert _score_to_grade(94) == "AA"

    def test_grade_a(self):
        from hub.hub_live import _score_to_grade
        assert _score_to_grade(75) == "A"
        assert _score_to_grade(84) == "A"

    def test_grade_bbb(self):
        from hub.hub_live import _score_to_grade
        assert _score_to_grade(65) == "BBB"
        assert _score_to_grade(74) == "BBB"

    def test_grade_bb(self):
        from hub.hub_live import _score_to_grade
        assert _score_to_grade(55) == "BB"
        assert _score_to_grade(64) == "BB"

    def test_grade_b(self):
        from hub.hub_live import _score_to_grade
        assert _score_to_grade(45) == "B"
        assert _score_to_grade(54) == "B"

    def test_grade_ccc(self):
        from hub.hub_live import _score_to_grade
        assert _score_to_grade(0) == "CCC"
        assert _score_to_grade(44) == "CCC"


# ═══════════════════════════════════════════════════════════════════════════════
# TestNetworkHealth
# ═══════════════════════════════════════════════════════════════════════════════

class TestNetworkHealth:

    def test_healthy_when_active_and_score_above_30(self):
        from hub.hub_live import _compute_network_health
        assert _compute_network_health(active_agents=5, avg_score=50.0) == "healthy"

    def test_healthy_exact_boundary_score_30(self):
        from hub.hub_live import _compute_network_health
        assert _compute_network_health(active_agents=1, avg_score=30.0) == "healthy"

    def test_degraded_when_active_but_low_score(self):
        from hub.hub_live import _compute_network_health
        assert _compute_network_health(active_agents=3, avg_score=29.9) == "degraded"

    def test_degraded_score_zero(self):
        from hub.hub_live import _compute_network_health
        assert _compute_network_health(active_agents=2, avg_score=0.0) == "degraded"

    def test_critical_when_no_active_agents(self):
        from hub.hub_live import _compute_network_health
        assert _compute_network_health(active_agents=0, avg_score=0.0) == "critical"

    def test_critical_ignores_score_when_no_agents(self):
        from hub.hub_live import _compute_network_health
        assert _compute_network_health(active_agents=0, avg_score=99.0) == "critical"


# ═══════════════════════════════════════════════════════════════════════════════
# TestSnapshotGeneration
# ═══════════════════════════════════════════════════════════════════════════════

class TestSnapshotGeneration:

    @pytest.mark.asyncio
    async def test_snapshot_structure_keys(self):
        """Le snapshot contient toutes les clés requises."""
        from hub.hub_live import _regenerate_live_snapshot

        mock_db = make_mock_db()
        # total agents
        mock_db.raw_execute_fetchall = AsyncMock(side_effect=[
            [{"cnt": 10}],          # total_agents
            [{"cnt": 7}],           # active_agents
            [{"cnt": 50}],          # total_reviews
            [{"cnt": 3}],           # total_spawns
            [{"avg": 65.5}],        # avg_score
            [active_agent(hub_id="hub_1", score=90),
             active_agent(hub_id="hub_2", score=80)],  # top_agents
            [forum_post()],         # recent_posts
        ])

        with patch("hub.hub_live.db", mock_db):
            snap = await _regenerate_live_snapshot()

        assert "generated_at" in snap
        assert "cache_ttl" in snap
        assert snap["cache_ttl"] == 300
        assert "stats" in snap
        assert "top_agents" in snap
        assert "recent_posts" in snap
        assert "network_health" in snap

    @pytest.mark.asyncio
    async def test_snapshot_stats_values(self):
        """Les stats numériques sont correctement mappées."""
        from hub.hub_live import _regenerate_live_snapshot

        mock_db = make_mock_db()
        mock_db.raw_execute_fetchall = AsyncMock(side_effect=[
            [{"cnt": 12}],
            [{"cnt": 8}],
            [{"cnt": 40}],
            [{"cnt": 5}],
            [{"avg": 72.3}],
            [],  # top_agents vide
            [],  # recent_posts vide
        ])

        with patch("hub.hub_live.db", mock_db):
            snap = await _regenerate_live_snapshot()

        s = snap["stats"]
        assert s["total_agents"] == 12
        assert s["active_agents"] == 8
        assert s["total_reviews"] == 40
        assert s["total_spawns"] == 5
        assert s["avg_score"] == 72.3

    @pytest.mark.asyncio
    async def test_snapshot_top_agents_grade_added(self):
        """Chaque top_agent reçoit un champ grade calculé depuis le score."""
        from hub.hub_live import _regenerate_live_snapshot

        mock_db = make_mock_db()
        mock_db.raw_execute_fetchall = AsyncMock(side_effect=[
            [{"cnt": 1}],
            [{"cnt": 1}],
            [{"cnt": 0}],
            [{"cnt": 0}],
            [{"avg": 90.0}],
            [active_agent(hub_id="hub_top", score=90)],
            [],
        ])

        with patch("hub.hub_live.db", mock_db):
            snap = await _regenerate_live_snapshot()

        assert len(snap["top_agents"]) == 1
        agent = snap["top_agents"][0]
        assert agent["hub_id"] == "hub_top"
        assert agent["grade"] == "AA"  # score 90 → AA

    @pytest.mark.asyncio
    async def test_snapshot_recent_posts_title_extracted(self):
        """Le titre est extrait du champ JSON 'data'."""
        from hub.hub_live import _regenerate_live_snapshot

        post = forum_post(post_id="p99", title="Forum announcement")
        mock_db = make_mock_db()
        mock_db.raw_execute_fetchall = AsyncMock(side_effect=[
            [{"cnt": 1}], [{"cnt": 1}], [{"cnt": 0}], [{"cnt": 0}],
            [{"avg": 50.0}],
            [],
            [post],
        ])

        with patch("hub.hub_live.db", mock_db):
            snap = await _regenerate_live_snapshot()

        assert len(snap["recent_posts"]) == 1
        p = snap["recent_posts"][0]
        assert p["post_id"] == "p99"
        assert p["title"] == "Forum announcement"
        assert p["category"] == "general"

    @pytest.mark.asyncio
    async def test_snapshot_network_health_healthy(self):
        """network_health=healthy quand agents actifs et avg_score>=30."""
        from hub.hub_live import _regenerate_live_snapshot

        mock_db = make_mock_db()
        mock_db.raw_execute_fetchall = AsyncMock(side_effect=[
            [{"cnt": 5}], [{"cnt": 5}], [{"cnt": 0}], [{"cnt": 0}],
            [{"avg": 60.0}],
            [],
            [],
        ])

        with patch("hub.hub_live.db", mock_db):
            snap = await _regenerate_live_snapshot()

        assert snap["network_health"] == "healthy"

    @pytest.mark.asyncio
    async def test_snapshot_network_health_critical_no_agents(self):
        """network_health=critical quand aucun agent actif."""
        from hub.hub_live import _regenerate_live_snapshot

        mock_db = make_mock_db()
        mock_db.raw_execute_fetchall = AsyncMock(side_effect=[
            [{"cnt": 0}], [{"cnt": 0}], [{"cnt": 0}], [{"cnt": 0}],
            [{"avg": None}],
            [],
            [],
        ])

        with patch("hub.hub_live.db", mock_db):
            snap = await _regenerate_live_snapshot()

        assert snap["network_health"] == "critical"

    @pytest.mark.asyncio
    async def test_snapshot_avg_score_none_handled(self):
        """avg NULL en DB → 0.0 dans le snapshot (pas d'exception)."""
        from hub.hub_live import _regenerate_live_snapshot

        mock_db = make_mock_db()
        mock_db.raw_execute_fetchall = AsyncMock(side_effect=[
            [{"cnt": 3}], [{"cnt": 0}], [{"cnt": 0}], [{"cnt": 0}],
            [{"avg": None}],
            [],
            [],
        ])

        with patch("hub.hub_live.db", mock_db):
            snap = await _regenerate_live_snapshot()

        assert snap["stats"]["avg_score"] == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# TestCache
# ═══════════════════════════════════════════════════════════════════════════════

class TestCache:

    @pytest.mark.asyncio
    async def test_cache_returns_same_snapshot_within_ttl(self):
        """Deux appels dans le TTL retournent le même objet (pas deux régénérations)."""
        import hub.hub_live as live_module

        # Reset cache
        live_module._live_cache["data"] = None
        live_module._live_cache["generated_at"] = 0

        fake_snap = {"generated_at": 9999, "cache_ttl": 300}
        call_count = 0

        async def fake_regen():
            nonlocal call_count
            call_count += 1
            return fake_snap

        with patch.object(live_module, "_regenerate_live_snapshot", fake_regen):
            with patch("hub.hub_live.time") as mock_time:
                mock_time.time.return_value = 1000

                # Premier appel → régénère
                r1 = await live_module._get_live_snapshot()
                # Deuxième appel, même seconde → depuis cache
                r2 = await live_module._get_live_snapshot()

        assert call_count == 1
        assert r1 is r2

    @pytest.mark.asyncio
    async def test_cache_expires_after_ttl(self):
        """Après TTL+1s, le snapshot est régénéré."""
        import hub.hub_live as live_module

        live_module._live_cache["data"] = {"generated_at": 1000, "cache_ttl": 300}
        live_module._live_cache["generated_at"] = 1000

        call_count = 0

        async def fake_regen():
            nonlocal call_count
            call_count += 1
            return {"generated_at": 1301, "cache_ttl": 300}

        with patch.object(live_module, "_regenerate_live_snapshot", fake_regen):
            with patch("hub.hub_live.time") as mock_time:
                # TTL=300, départ=1000 → expiration à 1300. On est à 1301.
                mock_time.time.return_value = 1301

                await live_module._get_live_snapshot()

        assert call_count == 1  # a bien régénéré


# ═══════════════════════════════════════════════════════════════════════════════
# TestHTMLRender
# ═══════════════════════════════════════════════════════════════════════════════

class TestHTMLRender:

    def _make_snapshot(self, active=3, avg_score=60.0, health="healthy"):
        return {
            "generated_at": 1746100000,
            "cache_ttl": 300,
            "stats": {
                "total_agents": 5,
                "active_agents": active,
                "total_reviews": 20,
                "total_spawns": 2,
                "avg_score": avg_score,
            },
            "top_agents": [
                {"hub_id": "hub_1", "name": "Alpha", "score": 90, "grade": "AA",
                 "chain": "solana", "framework": "custom"},
            ],
            "recent_posts": [
                {"post_id": "p1", "title": "Hello world", "category": "general",
                 "hot_score": 5.0, "created_at": 1746099000},
            ],
            "network_health": health,
        }

    def test_html_contains_doctype(self):
        from hub.hub_live import _render_live_html
        html = _render_live_html(self._make_snapshot())
        assert "<!DOCTYPE html>" in html or "<!doctype html>" in html.lower()

    def test_html_contains_title(self):
        from hub.hub_live import _render_live_html
        html = _render_live_html(self._make_snapshot())
        assert "MAXIA Hub" in html

    def test_html_contains_meta_refresh(self):
        from hub.hub_live import _render_live_html
        html = _render_live_html(self._make_snapshot())
        assert 'http-equiv="refresh"' in html
        assert "300" in html

    def test_html_contains_agent_name(self):
        from hub.hub_live import _render_live_html
        html = _render_live_html(self._make_snapshot())
        assert "Alpha" in html

    def test_html_contains_post_title(self):
        from hub.hub_live import _render_live_html
        html = _render_live_html(self._make_snapshot())
        assert "Hello world" in html

    def test_html_health_healthy_label(self):
        from hub.hub_live import _render_live_html
        html = _render_live_html(self._make_snapshot(health="healthy"))
        assert "healthy" in html.lower()

    def test_html_health_critical_label(self):
        from hub.hub_live import _render_live_html
        html = _render_live_html(self._make_snapshot(health="critical"))
        assert "critical" in html.lower()

    def test_html_dark_theme(self):
        from hub.hub_live import _render_live_html
        html = _render_live_html(self._make_snapshot())
        assert "#0a0a0a" in html
