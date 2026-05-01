"""MAXIA Hub Phase 2 — Score composite d'agent.

Routes :
  GET  /api/hub/score/{hub_id}           → score détaillé (toutes les composantes)
  POST /api/hub/score/{hub_id}/recalc    → force recalcul immédiat
  GET  /api/hub/leaderboard              → top 50 agents par score (query: chain, framework)
"""
from __future__ import annotations

import json
import logging
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from core.database import db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/hub", tags=["hub-score"])

# Seuil minimum de score pour pouvoir rédiger une review
_MIN_REVIEWER_SCORE = 15

# Plafond x402 : score ≤ 30 si aucune transaction x402 vérifiée
_X402_SCORE_CAP = 30


# ─── Grade mapping ────────────────────────────────────────────────────────────

def _score_to_grade(score: int) -> str:
    """Retourne le grade correspondant au score Hub (0-100)."""
    if score >= 95:
        return "AAA"
    if score >= 85:
        return "AA"
    if score >= 75:
        return "A"
    if score >= 65:
        return "BBB"
    if score >= 55:
        return "BB"
    if score >= 45:
        return "B"
    return "CCC"


# ─── Formule de score ─────────────────────────────────────────────────────────

def _compute_score_from_components(components: dict) -> int:
    """Calcule le score Hub final à partir des composantes brutes.

    Formule :
      raw = (ecr * 0.40) + (uptime * 0.20) + (review_pct * 0.20)
            + (stake * 0.10) + (age * 0.10) - (dispute * 0.30)

    Toutes les entrées sont normalisées 0-100 avant multiplication.
    """
    ecr = float(components["escrow_completion_rate"])       # 0-100
    uptime = float(components["uptime_30d"])                # 0-100
    peer_avg = float(components["peer_review_avg"])         # 0-5
    stake = float(components["stake_tier"])                 # 0-1.0
    age = float(components["age_bonus"])                    # 0-10
    dispute = float(components["dispute_rate"])             # 0-100
    x402_unlocked = bool(components["x402_unlocked"])

    # Normaliser peer_review sur 100 (5 → 100)
    review_pct = (peer_avg / 5.0) * 100.0

    # Normaliser stake sur 100
    stake_pct = stake * 100.0

    # age_bonus est déjà 0-10, normaliser sur 100
    age_pct = age * 10.0

    raw = (
        ecr * 0.40
        + uptime * 0.20
        + review_pct * 0.20
        + stake_pct * 0.10
        + age_pct * 0.10
        - dispute * 0.30
    )

    # Boosts additifs R1-R4 (clampés à 0 minimum — jamais négatifs)
    r1 = max(0.0, float(components.get("score_r1_boost") or 0.0))
    r2 = max(0.0, float(components.get("score_r2_boost") or 0.0))
    r3 = max(0.0, float(components.get("score_r3_eas") or 0.0))
    r4 = max(0.0, float(components.get("score_r4_ext") or 0.0))

    score = int(round(max(0.0, min(100.0, raw + r1 + r2 + r3 + r4))))

    # Plafond x402
    if not x402_unlocked:
        score = min(score, _X402_SCORE_CAP)

    return score


# ─── Récupération des composantes ─────────────────────────────────────────────

async def _fetch_score_components(
    hub_id: str,
    wallet: str,
    uptime_30d: float | None = None,
    birth_ts: int | None = None,
) -> dict:
    """Récupère toutes les composantes brutes du score pour un agent.

    Paramètres :
      hub_id     : identifiant Hub de l'agent
      wallet     : adresse wallet de l'agent (pour requêtes escrow/stake)
      uptime_30d : si None, lu depuis hub_agents en DB
      birth_ts   : si None, lu depuis hub_agents en DB
    """
    # Lire uptime_30d et birth_ts depuis hub_agents si non fournis
    if uptime_30d is None or birth_ts is None:
        hub_row = await db._fetchone(
            "SELECT uptime_30d, birth_ts FROM hub_agents WHERE hub_id=?",
            (hub_id,),
        )
        if hub_row is not None:
            hub_dict = dict(hub_row)
            if uptime_30d is None:
                uptime_30d = float(hub_dict.get("uptime_30d") or 0.0)
            if birth_ts is None:
                birth_ts = int(hub_dict.get("birth_ts") or int(time.time()))
        else:
            if uptime_30d is None:
                uptime_30d = 0.0
            if birth_ts is None:
                birth_ts = int(time.time())
    now = int(time.time())
    cutoff_30d = now - 30 * 24 * 3600

    # ── 1. escrow_completion_rate ────────────────────────────────────────────
    escrow_rows = await db.raw_execute_fetchall(
        "SELECT "
        "  COALESCE(SUM(CASE WHEN status='released' THEN 1 ELSE 0 END), 0) AS released, "
        "  COALESCE(SUM(CASE WHEN status='disputed' THEN 1 ELSE 0 END), 0) AS disputed, "
        "  COALESCE(SUM(CASE WHEN status='expired'  THEN 1 ELSE 0 END), 0) AS expired "
        "FROM escrow_records "
        "WHERE (buyer=? OR seller=?) AND created_at>=?",
        (wallet, wallet, cutoff_30d),
    )

    if escrow_rows:
        row = escrow_rows[0]
        released = int(row.get("released") or 0)
        disputed = int(row.get("disputed") or 0)
        expired = int(row.get("expired") or 0)
        total_escrow = released + disputed + expired
        if total_escrow == 0:
            escrow_completion_rate = 50  # neutre
        else:
            escrow_completion_rate = int(round(released / total_escrow * 100))
    else:
        escrow_completion_rate = 50

    # ── 2. uptime_30d ────────────────────────────────────────────────────────
    # Déjà calculé par hub_registry → passé en paramètre

    # ── 3. peer_review_avg ───────────────────────────────────────────────────
    review_rows = await db.raw_execute_fetchall(
        "SELECT COALESCE(AVG(rating), 0) AS avg_rating, COUNT(*) AS cnt "
        "FROM hub_reviews WHERE reviewed_hub_id=?",
        (hub_id,),
    )
    if review_rows and int(review_rows[0].get("cnt") or 0) > 0:
        peer_review_avg = float(review_rows[0].get("avg_rating") or 2.5)
    else:
        peer_review_avg = 2.5  # neutre

    # ── 4. stake_tier ────────────────────────────────────────────────────────
    stake_rows = await db.raw_execute_fetchall(
        "SELECT data FROM stakes WHERE wallet=? ORDER BY created_at DESC LIMIT 1",
        (wallet,),
    )
    if stake_rows:
        try:
            stake_data = json.loads(stake_rows[0]["data"])
            stake_amount = float(stake_data.get("amount", 0))
            stake_tier = min(1.0, stake_amount / 1000.0)
        except (ValueError, TypeError, KeyError):
            stake_tier = 0.0
    else:
        stake_tier = 0.0

    # ── 5. age_bonus ─────────────────────────────────────────────────────────
    months_old = (int(time.time()) - birth_ts) / (30 * 24 * 3600)
    age_bonus = min(10, int(months_old))

    # ── 6. dispute_rate ──────────────────────────────────────────────────────
    dispute_rows = await db.raw_execute_fetchall(
        "SELECT COUNT(*) AS cnt FROM pod_disputes d "
        "JOIN deliveries del ON d.delivery_id=del.id "
        "WHERE (del.seller_wallet=? OR del.buyer_wallet=?) AND del.created_at>=?",
        (wallet, wallet, cutoff_30d),
    )
    delivery_rows = await db.raw_execute_fetchall(
        "SELECT COUNT(*) AS cnt FROM deliveries "
        "WHERE (seller_wallet=? OR buyer_wallet=?) AND created_at>=?",
        (wallet, wallet, cutoff_30d),
    )

    n_disputes = int((dispute_rows[0].get("cnt") or 0) if dispute_rows else 0)
    n_deliveries = int((delivery_rows[0].get("cnt") or 0) if delivery_rows else 0)
    dispute_rate = (n_disputes / max(1, n_deliveries)) * 100 if n_deliveries > 0 else 0

    # ── 7. x402_unlocked ─────────────────────────────────────────────────────
    # En phase dev : pas de table x402_transactions → toujours True
    x402_unlocked = True
    try:
        x402_rows = await db.raw_execute_fetchall(
            "SELECT COUNT(*) AS cnt FROM x402_transactions WHERE wallet=? LIMIT 1",
            (wallet,),
        )
        if x402_rows:
            x402_unlocked = int(x402_rows[0].get("cnt") or 0) > 0
    except Exception:
        x402_unlocked = True  # table absente → pas de plafond en phase dev

    return {
        "escrow_completion_rate": escrow_completion_rate,
        "uptime_30d": uptime_30d,
        "peer_review_avg": peer_review_avg,
        "stake_tier": stake_tier,
        "age_bonus": age_bonus,       # sera écrasé par compute_hub_score
        "dispute_rate": dispute_rate,
        "x402_unlocked": x402_unlocked,
    }


# ─── Calcul du score complet ─────────────────────────────────────────────────

async def compute_hub_score(hub_id: str) -> dict:
    """Calcule le score Hub final + toutes les composantes.

    Retourne :
      {hub_id, score, grade, components: {…}, x402_unlocked, calculated_at}
    """
    row = await db._fetchone(
        "SELECT hub_id, wallet, birth_ts, uptime_30d, score, "
        "score_r1_boost, score_r2_boost, score_r3_eas, score_r4_ext "
        "FROM hub_agents WHERE hub_id=?",
        (hub_id,),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    row_dict = dict(row)
    wallet = row_dict["wallet"]
    uptime_30d = float(row_dict.get("uptime_30d") or 0.0)
    birth_ts = int(row_dict.get("birth_ts") or int(time.time()))

    components = await _fetch_score_components(
        hub_id, wallet, uptime_30d=uptime_30d, birth_ts=birth_ts
    )

    # Injecter les boosts R1-R4 depuis la ligne hub_agents
    components["score_r1_boost"] = row_dict.get("score_r1_boost") or 0.0
    components["score_r2_boost"] = row_dict.get("score_r2_boost") or 0.0
    components["score_r3_eas"] = row_dict.get("score_r3_eas") or 0.0
    components["score_r4_ext"] = row_dict.get("score_r4_ext") or 0.0

    score = _compute_score_from_components(components)
    grade = _score_to_grade(score)

    return {
        "hub_id": hub_id,
        "score": score,
        "grade": grade,
        "components": components,
        "x402_unlocked": components["x402_unlocked"],
        "calculated_at": int(time.time()),
    }


# ─── Persistance ─────────────────────────────────────────────────────────────

async def persist_hub_score(hub_id: str, score: int) -> None:
    """Met à jour le score dans hub_agents."""
    await db.raw_execute(
        "UPDATE hub_agents SET score=? WHERE hub_id=?",
        (score, hub_id),
    )


# ─── Recalcul global ─────────────────────────────────────────────────────────

async def recalculate_all_hub_scores() -> int:
    """Recalcule le score de tous les agents actifs. Retourne le nb mis à jour."""
    agents = await db.raw_execute_fetchall(
        "SELECT hub_id, wallet, birth_ts, uptime_30d FROM hub_agents WHERE status='active'",
    )
    if not agents:
        return 0

    updated = 0
    for agent in agents:
        hub_id = agent["hub_id"]
        try:
            result = await compute_hub_score(hub_id)
            await persist_hub_score(hub_id, result["score"])
            updated += 1
        except Exception as e:
            logger.warning("Erreur recalcul score hub_id=%s: %s", hub_id, e)

    logger.info("Hub scores recalcules: %d agents", updated)
    return updated


# ─── Endpoints ───────────────────────────────────────────────────────────────

@router.get("/score/{hub_id}")
async def get_hub_score(hub_id: str) -> dict:
    """Score détaillé d'un agent Hub (toutes les composantes)."""
    return await compute_hub_score(hub_id)


@router.post("/score/{hub_id}/recalc")
async def recalc_hub_score(hub_id: str) -> dict:
    """Force le recalcul immédiat du score d'un agent Hub."""
    result = await compute_hub_score(hub_id)
    await persist_hub_score(hub_id, result["score"])
    return {**result, "recalculated": True}


@router.get("/leaderboard")
async def get_hub_leaderboard(
    limit: int = Query(default=50, ge=1, le=200),
    chain: Optional[str] = Query(default=None),
    framework: Optional[str] = Query(default=None),
) -> dict:
    """Top agents triés par score décroissant.

    Filtres optionnels : chain, framework.
    """
    conditions = ["status='active'"]
    params: list = []

    if chain is not None:
        conditions.append("chain=?")
        params.append(chain)

    if framework is not None:
        conditions.append("framework=?")
        params.append(framework)

    where = " AND ".join(conditions)
    params.append(limit)

    rows = await db.raw_execute_fetchall(
        f"SELECT hub_id, wallet, name, chain, framework, score, uptime_30d, "
        f"birth_ts, last_heartbeat "
        f"FROM hub_agents WHERE {where} "
        f"ORDER BY score DESC LIMIT ?",
        tuple(params),
    )

    leaderboard = [
        {
            "hub_id": r["hub_id"],
            "name": r.get("name", ""),
            "chain": r.get("chain", ""),
            "framework": r.get("framework", ""),
            "score": r["score"],
            "grade": _score_to_grade(int(r["score"])),
            "uptime_30d": r.get("uptime_30d", 0.0),
            "last_heartbeat": r.get("last_heartbeat"),
        }
        for r in rows
    ]

    return {"leaderboard": leaderboard, "count": len(leaderboard)}
