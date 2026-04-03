"""MAXIA Agent Leaderboard V12 — Scoring bayesien et classement des agents marketplace

Systeme de notation composite pour tous les agents enregistres sur le marketplace.
Utilise un score Beta-Bayesian + metriques de performance (latence, uptime, taux de succes).
Les grades vont de AAA (elite) a CCC (sous-performant).
"""
import logging
import time, uuid
from datetime import datetime, timedelta
from fastapi import APIRouter, Query, Request, HTTPException

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/leaderboard", tags=["leaderboard"])

# ── Schema auto-create ──

_schema_ready = False

_LEADERBOARD_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    date TEXT NOT NULL,
    success_count INTEGER NOT NULL DEFAULT 0,
    failure_count INTEGER NOT NULL DEFAULT 0,
    total_requests INTEGER NOT NULL DEFAULT 0,
    avg_latency_ms NUMERIC(18,6) NOT NULL DEFAULT 0,
    p99_latency_ms NUMERIC(18,6) NOT NULL DEFAULT 0,
    uptime_minutes INTEGER NOT NULL DEFAULT 0,
    total_minutes INTEGER NOT NULL DEFAULT 0,
    disputes_won INTEGER NOT NULL DEFAULT 0,
    disputes_lost INTEGER NOT NULL DEFAULT 0,
    revenue_usdc NUMERIC(18,6) NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_metrics_agent_date ON agent_metrics(agent_id, date);

CREATE TABLE IF NOT EXISTS agent_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT UNIQUE NOT NULL,
    bayesian_trust NUMERIC(18,6) NOT NULL DEFAULT 0.5,
    success_rate_30d NUMERIC(18,6) NOT NULL DEFAULT 0,
    latency_score NUMERIC(18,6) NOT NULL DEFAULT 0,
    uptime_score NUMERIC(18,6) NOT NULL DEFAULT 0,
    stake_weight NUMERIC(18,6) NOT NULL DEFAULT 0,
    composite_score NUMERIC(18,6) NOT NULL DEFAULT 0,
    grade TEXT NOT NULL DEFAULT 'B',
    last_calculated TEXT NOT NULL DEFAULT '',
    penalty_level TEXT NOT NULL DEFAULT 'none'
);

CREATE INDEX IF NOT EXISTS idx_scores_composite ON agent_scores(composite_score DESC);
CREATE INDEX IF NOT EXISTS idx_scores_grade ON agent_scores(grade);
"""


async def _ensure_schema():
    """Cree les tables si elles n'existent pas encore."""
    global _schema_ready
    if _schema_ready:
        return
    try:
        from core.database import db
        await db.raw_executescript(_LEADERBOARD_SCHEMA)
        _schema_ready = True
        logger.info("Schema pret")
    except Exception as e:
        logger.error(f"Erreur schema: {e}")


# ── Calcul du score composite ──

# Seuils de grades : du meilleur au pire
GRADE_THRESHOLDS = [
    (0.95, "AAA"),
    (0.85, "AA"),
    (0.75, "A"),
    (0.65, "BBB"),
    (0.55, "BB"),
    (0.45, "B"),
]


def _compute_grade(composite: float) -> str:
    """Determine le grade en fonction du score composite."""
    for threshold, grade in GRADE_THRESHOLDS:
        if composite >= threshold:
            return grade
    return "CCC"


def _compute_composite(
    successes: int,
    failures: int,
    total: int,
    p99_latency: float,
    uptime_min: int,
    total_min: int,
    stake_weight: float,
) -> dict:
    """Calcule le score composite Beta-Bayesian pour un agent.

    Formule :
    - bayesian_trust = alpha / (alpha + beta) avec alpha = 1 + successes, beta = 1 + failures
    - success_rate = successes / total (fenetre 30 jours)
    - latency_score = max(0, 1 - (p99_latency / 5000))
    - uptime_score = uptime_minutes / total_minutes
    - composite = 0.30 * bayesian + 0.25 * success_rate + 0.20 * latency + 0.15 * uptime + 0.10 * stake
    """
    # Prior bayesien (Beta distribution)
    alpha = 1 + successes
    beta_param = 1 + failures
    bayesian_trust = alpha / (alpha + beta_param)

    # Taux de succes brut
    success_rate = successes / total if total > 0 else 0.0

    # Score de latence (0 = lent, 1 = rapide)
    latency_score = max(0.0, 1.0 - (p99_latency / 5000.0))

    # Score d'uptime
    uptime_score = uptime_min / total_min if total_min > 0 else 0.0

    # Composite pondere
    composite = (
        0.30 * bayesian_trust
        + 0.25 * success_rate
        + 0.20 * latency_score
        + 0.15 * uptime_score
        + 0.10 * stake_weight
    )

    # Clamp entre 0 et 1
    composite = max(0.0, min(1.0, composite))

    return {
        "bayesian_trust": round(bayesian_trust, 4),
        "success_rate_30d": round(success_rate, 4),
        "latency_score": round(latency_score, 4),
        "uptime_score": round(uptime_score, 4),
        "stake_weight": round(stake_weight, 4),
        "composite_score": round(composite, 4),
        "grade": _compute_grade(composite),
    }


# ── Enregistrement de metriques ──

async def record_metric(agent_id: str, success: bool, latency_ms: float, revenue_usdc: float = 0.0):
    """Enregistre une metrique de transaction pour un agent.

    Appele en interne apres chaque execution de service.
    Agrege dans la table agent_metrics par (agent_id, date).
    """
    await _ensure_schema()
    from core.database import db

    today = datetime.utcnow().strftime("%Y-%m-%d")

    # Verifier si une entree existe deja pour aujourd'hui
    rows = await db.raw_execute_fetchall(
        "SELECT id, success_count, failure_count, total_requests, "
        "avg_latency_ms, p99_latency_ms, revenue_usdc "
        "FROM agent_metrics WHERE agent_id = ? AND date = ?",
        (agent_id, today),
    )

    if rows:
        row = rows[0]
        old_success = row["success_count"]
        old_failure = row["failure_count"]
        old_total = row["total_requests"]
        old_avg = row["avg_latency_ms"]
        old_p99 = row["p99_latency_ms"]
        old_rev = row["revenue_usdc"]

        new_success = old_success + (1 if success else 0)
        new_failure = old_failure + (0 if success else 1)
        new_total = old_total + 1

        # Moyenne incrementale de la latence
        new_avg = ((old_avg * old_total) + latency_ms) / new_total

        # p99 approxime : garder le max entre ancien p99 et nouvelle latence
        new_p99 = max(old_p99, latency_ms)

        new_rev = old_rev + revenue_usdc

        await db.raw_execute(
            "UPDATE agent_metrics SET success_count = ?, failure_count = ?, "
            "total_requests = ?, avg_latency_ms = ?, p99_latency_ms = ?, "
            "revenue_usdc = ? WHERE id = ?",
            (new_success, new_failure, new_total, round(new_avg, 2),
             round(new_p99, 2), round(new_rev, 6), row["id"]),
        )
    else:
        # Premiere transaction de la journee pour cet agent
        await db.raw_execute(
            "INSERT INTO agent_metrics "
            "(agent_id, date, success_count, failure_count, total_requests, "
            "avg_latency_ms, p99_latency_ms, uptime_minutes, total_minutes, "
            "disputes_won, disputes_lost, revenue_usdc) "
            "VALUES (?, ?, ?, ?, 1, ?, ?, 0, 0, 0, 0, ?)",
            (
                agent_id, today,
                1 if success else 0,
                0 if success else 1,
                round(latency_ms, 2),
                round(latency_ms, 2),
                round(revenue_usdc, 6),
            ),
        )


# ── Recalcul global des scores (appele par le scheduler) ──

async def recalculate_all_scores():
    """Recalcule le score composite de tous les agents sur les 30 derniers jours.

    Agrege agent_metrics sur 30 jours, calcule le composite, et met a jour agent_scores.
    Doit etre appele toutes les heures par le scheduler.
    """
    await _ensure_schema()
    from core.database import db

    cutoff_date = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")
    now_str = datetime.utcnow().isoformat()

    # Agreger les metriques sur 30 jours par agent
    rows = await db.raw_execute_fetchall(
        "SELECT agent_id, "
        "COALESCE(SUM(success_count), 0) AS total_success, "
        "COALESCE(SUM(failure_count), 0) AS total_failure, "
        "COALESCE(SUM(total_requests), 0) AS total_reqs, "
        "COALESCE(MAX(p99_latency_ms), 0) AS max_p99, "
        "COALESCE(SUM(uptime_minutes), 0) AS total_uptime, "
        "COALESCE(SUM(total_minutes), 0) AS total_min, "
        "COALESCE(SUM(revenue_usdc), 0) AS total_rev "
        "FROM agent_metrics WHERE date >= ? "
        "GROUP BY agent_id",
        (cutoff_date,),
    )

    if not rows:
        return 0

    updated = 0
    for row in rows:
        agent_id = row["agent_id"]

        # Recuperer le stake_weight depuis la table stakes (reputation_staking)
        stake_weight = 0.0
        try:
            stake_rows = await db.raw_execute_fetchall(
                "SELECT data FROM stakes WHERE wallet = ? ORDER BY created_at DESC LIMIT 1",
                (agent_id,),
            )
            if stake_rows:
                import json
                stake_data = json.loads(stake_rows[0]["data"])
                stake_amount = stake_data.get("amount", 0)
                # Normaliser : 500 USDC stake = 1.0, lineaire jusqu'a 0
                stake_weight = min(1.0, stake_amount / 500.0)
        except Exception:
            pass

        scores = _compute_composite(
            successes=row["total_success"],
            failures=row["total_failure"],
            total=row["total_reqs"],
            p99_latency=row["max_p99"],
            uptime_min=row["total_uptime"],
            total_min=row["total_min"],
            stake_weight=stake_weight,
        )

        # Recuperer le penalty_level existant (ne pas l'ecraser ici, c'est sla_enforcer qui gere)
        existing = await db.raw_execute_fetchall(
            "SELECT penalty_level FROM agent_scores WHERE agent_id = ?",
            (agent_id,),
        )
        penalty = existing[0]["penalty_level"] if existing else "none"

        # Upsert dans agent_scores
        await db.raw_execute(
            "INSERT INTO agent_scores "
            "(agent_id, bayesian_trust, success_rate_30d, latency_score, "
            "uptime_score, stake_weight, composite_score, grade, last_calculated, penalty_level) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(agent_id) DO UPDATE SET "
            "bayesian_trust = excluded.bayesian_trust, "
            "success_rate_30d = excluded.success_rate_30d, "
            "latency_score = excluded.latency_score, "
            "uptime_score = excluded.uptime_score, "
            "stake_weight = excluded.stake_weight, "
            "composite_score = excluded.composite_score, "
            "grade = excluded.grade, "
            "last_calculated = excluded.last_calculated",
            (
                agent_id,
                scores["bayesian_trust"],
                scores["success_rate_30d"],
                scores["latency_score"],
                scores["uptime_score"],
                scores["stake_weight"],
                scores["composite_score"],
                scores["grade"],
                now_str,
                penalty,
            ),
        )
        updated += 1

    logger.info(f"{updated} agents recalcules")
    return updated


# ── Endpoints API ──

@router.get("")
async def get_leaderboard(
    limit: int = Query(50, ge=1, le=200, description="Nombre max d'agents"),
):
    """Top agents tries par score composite decroissant."""
    await _ensure_schema()
    from core.database import db

    rows = await db.raw_execute_fetchall(
        "SELECT agent_id, bayesian_trust, success_rate_30d, latency_score, "
        "uptime_score, stake_weight, composite_score, grade, last_calculated, "
        "penalty_level "
        "FROM agent_scores ORDER BY composite_score DESC LIMIT ?",
        (limit,),
    )

    results = []
    for row in rows:
        entry = {
            "agent_id": row["agent_id"],
            "composite_score": row["composite_score"],
            "grade": row["grade"],
            "bayesian_trust": row["bayesian_trust"],
            "success_rate_30d": row["success_rate_30d"],
            "latency_score": row["latency_score"],
            "uptime_score": row["uptime_score"],
            "stake_weight": row["stake_weight"],
            "penalty_level": row["penalty_level"],
            "last_calculated": row["last_calculated"],
        }
        results.append(entry)

    return {"leaderboard": results, "count": len(results)}


@router.get("/agent/{agent_id}")
async def get_agent_details(agent_id: str):
    """Metriques detaillees + score + grade pour un agent specifique."""
    await _ensure_schema()
    from core.database import db

    # Score actuel
    score_rows = await db.raw_execute_fetchall(
        "SELECT id, agent_id, bayesian_trust, success_rate_30d, latency_score, "
        "uptime_score, stake_weight, composite_score, grade, last_calculated "
        "FROM agent_scores WHERE agent_id = ?", (agent_id,),
    )

    # Metriques des 30 derniers jours
    cutoff_date = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")
    metric_rows = await db.raw_execute_fetchall(
        "SELECT date, success_count, failure_count, total_requests, "
        "avg_latency_ms, p99_latency_ms, uptime_minutes, total_minutes, "
        "disputes_won, disputes_lost, revenue_usdc "
        "FROM agent_metrics WHERE agent_id = ? AND date >= ? "
        "ORDER BY date DESC",
        (agent_id, cutoff_date),
    )

    if not score_rows and not metric_rows:
        return {"error": "Agent non trouve dans le leaderboard", "agent_id": agent_id}

    # Agreger les metriques
    total_success = sum(r["success_count"] for r in metric_rows)
    total_failure = sum(r["failure_count"] for r in metric_rows)
    total_reqs = sum(r["total_requests"] for r in metric_rows)
    total_revenue = sum(r["revenue_usdc"] for r in metric_rows)
    total_disputes_won = sum(r["disputes_won"] for r in metric_rows)
    total_disputes_lost = sum(r["disputes_lost"] for r in metric_rows)

    score = dict(score_rows[0]) if score_rows else {
        "composite_score": 0, "grade": "B", "bayesian_trust": 0.5,
        "success_rate_30d": 0, "latency_score": 0, "uptime_score": 0,
        "stake_weight": 0, "penalty_level": "none", "last_calculated": "",
    }

    return {
        "agent_id": agent_id,
        "score": {
            "composite": score["composite_score"],
            "grade": score["grade"],
            "bayesian_trust": score["bayesian_trust"],
            "success_rate_30d": score["success_rate_30d"],
            "latency_score": score["latency_score"],
            "uptime_score": score["uptime_score"],
            "stake_weight": score["stake_weight"],
            "penalty_level": score["penalty_level"],
            "last_calculated": score["last_calculated"],
        },
        "metrics_30d": {
            "total_requests": total_reqs,
            "successes": total_success,
            "failures": total_failure,
            "success_rate": round(total_success / total_reqs, 4) if total_reqs > 0 else 0,
            "revenue_usdc": round(total_revenue, 2),
            "disputes_won": total_disputes_won,
            "disputes_lost": total_disputes_lost,
            "days_active": len(metric_rows),
        },
        "daily_metrics": [dict(r) for r in metric_rows[:14]],  # 14 derniers jours max
    }


@router.post("/record")
async def record_transaction_metric(req: dict, request: Request):
    """Enregistre une metrique de transaction (appel interne uniquement — API key requise)."""
    # Auth: X-API-Key ou appel local uniquement
    api_key = request.headers.get("X-API-Key", "")
    is_local = request.client and request.client.host in ("127.0.0.1", "::1", "localhost")
    if not api_key and not is_local:
        raise HTTPException(403, "API key requise pour enregistrer des metriques")

    agent_id = str(req.get("agent_id", ""))[:128]
    if not agent_id:
        raise HTTPException(400, "agent_id requis")

    success = bool(req.get("success", True))
    latency_ms = max(0.0, min(float(req.get("latency_ms", 0)), 300000))  # Cap 5min
    revenue_usdc = max(0.0, min(float(req.get("revenue_usdc", 0)), 100000))  # Cap $100k

    await record_metric(agent_id, success, latency_ms, revenue_usdc)

    return {
        "recorded": True,
        "agent_id": agent_id,
        "success": success,
        "latency_ms": latency_ms,
    }


@router.get("/grades")
async def get_grades_summary():
    """Resume : nombre d'agents par grade."""
    await _ensure_schema()
    from core.database import db

    rows = await db.raw_execute_fetchall(
        "SELECT grade, COUNT(*) AS agent_count "
        "FROM agent_scores GROUP BY grade ORDER BY "
        "CASE grade "
        "  WHEN 'AAA' THEN 1 WHEN 'AA' THEN 2 WHEN 'A' THEN 3 "
        "  WHEN 'BBB' THEN 4 WHEN 'BB' THEN 5 WHEN 'B' THEN 6 "
        "  WHEN 'CCC' THEN 7 ELSE 8 END",
    )

    total = sum(r["agent_count"] for r in rows)
    grades = {r["grade"]: r["agent_count"] for r in rows}

    return {
        "grades": grades,
        "total_agents": total,
        "distribution": {
            grade: round(count / total * 100, 1) if total > 0 else 0
            for grade, count in grades.items()
        },
    }
