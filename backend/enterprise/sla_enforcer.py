"""MAXIA SLA Enforcer V12 — Application automatique des SLA avec penalites progressives

Surveille les scores des agents et applique des penalites croissantes :
warning -> reduced_visibility -> probation -> suspended -> delisted.
Inclut un circuit breaker par agent (5 echecs consecutifs = suspension 5 min).
"""
import logging
import time
from datetime import datetime
import os
from fastapi import APIRouter, Request, HTTPException
from typing import Optional

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sla", tags=["sla"])

# ── SLA Tiers ──

SLA_TIERS = {
    "basic": {
        "max_response_ms": 5000,
        "min_uptime_pct": 95.0,
        "min_success_pct": 90.0,
        "description": "Niveau de base — tolere jusqu'a 5s de latence, 95% uptime",
    },
    "standard": {
        "max_response_ms": 2000,
        "min_uptime_pct": 99.0,
        "min_success_pct": 95.0,
        "description": "Standard — 2s max, 99% uptime, 95% succes",
    },
    "premium": {
        "max_response_ms": 500,
        "min_uptime_pct": 99.9,
        "min_success_pct": 99.0,
        "description": "Premium — 500ms max, 99.9% uptime, 99% succes",
    },
}

# ── Niveaux de penalites (progressifs) ──

PENALTY_LEVELS = [
    {"level": "none",               "min_score": 0.65, "description": "Aucune penalite"},
    {"level": "warning",            "min_score": 0.50, "description": "Avertissement enregistre"},
    {"level": "reduced_visibility", "min_score": 0.35, "description": "Visibilite reduite dans les resultats"},
    {"level": "probation",          "min_score": 0.20, "description": "Probation — plus de nouveaux escrows"},
    {"level": "suspended",          "min_score": 0.00, "description": "Suspendu temporairement du marketplace"},
]


def _determine_penalty(composite_score: float) -> str:
    """Determine le niveau de penalite en fonction du score composite."""
    if composite_score >= 0.65:
        return "none"
    elif composite_score >= 0.50:
        return "warning"
    elif composite_score >= 0.35:
        return "reduced_visibility"
    elif composite_score >= 0.20:
        return "probation"
    else:
        # En dessous de 0.20 : delisted si deja suspendu, sinon suspended
        return "suspended"


def _is_delisted(composite_score: float, current_penalty: str) -> bool:
    """Un agent est delisted si son score < 0.20 ET il etait deja suspendu."""
    return composite_score < 0.20 and current_penalty == "suspended"


# ── Schema auto-create ──

_schema_ready = False

_SLA_SCHEMA = """
CREATE TABLE IF NOT EXISTS sla_violations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    violation_type TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '',
    penalty_applied TEXT NOT NULL DEFAULT 'warning',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_sla_agent ON sla_violations(agent_id, created_at);
"""


async def _ensure_schema():
    """Cree la table sla_violations si elle n'existe pas."""
    global _schema_ready
    if _schema_ready:
        return
    try:
        from core.database import db
        await db.raw_executescript(_SLA_SCHEMA)
        _schema_ready = True
        logger.info("[SLA] Schema pret")
    except Exception as e:
        logger.error(f"[SLA] Erreur schema: {e}")


# ── Circuit breaker en memoire (par agent) ──

# Format : { agent_id: {"consecutive_failures": int, "suspended_until": float} }
_circuit_breakers: dict = {}

# Seuils du circuit breaker
CIRCUIT_BREAKER_THRESHOLD = 5        # Echecs consecutifs avant suspension
CIRCUIT_BREAKER_COOLDOWN_S = 300     # 5 minutes de suspension


def _cleanup_circuit_breakers():
    """Nettoyage memoire : supprime les entrees inactives depuis plus d'une heure.

    Appele automatiquement quand le dict depasse 500 entrees.
    Supprime les agents sans echec recent (consecutive_failures == 0 et pas de suspension active).
    """
    now = time.time()
    one_hour_ago = now - 3600
    to_remove = [
        aid for aid, cb in _circuit_breakers.items()
        if cb["consecutive_failures"] == 0
        and (cb["suspended_until"] <= 0 or cb["suspended_until"] < one_hour_ago)
    ]
    for aid in to_remove:
        del _circuit_breakers[aid]
    if to_remove:
        logger.info(f"[SLA] Circuit breaker cleanup: {len(to_remove)} entrees supprimees, {len(_circuit_breakers)} restantes")


def circuit_breaker_record(agent_id: str, success: bool) -> dict:
    """Enregistre un resultat dans le circuit breaker. Retourne le statut.

    Appele apres chaque execution de service.
    5 echecs consecutifs -> suspension automatique de 5 minutes.
    Un succes remet le compteur a zero.
    """
    now = time.time()

    # Nettoyage memoire si le dict depasse 500 entrees
    if len(_circuit_breakers) > 500:
        _cleanup_circuit_breakers()

    if agent_id not in _circuit_breakers:
        _circuit_breakers[agent_id] = {"consecutive_failures": 0, "suspended_until": 0}

    cb = _circuit_breakers[agent_id]

    # Verifier si la suspension est terminee
    if cb["suspended_until"] > 0 and now >= cb["suspended_until"]:
        cb["consecutive_failures"] = 0
        cb["suspended_until"] = 0

    if success:
        # Reset sur succes
        cb["consecutive_failures"] = 0
        cb["suspended_until"] = 0
        return {"circuit_open": False, "failures": 0}
    else:
        cb["consecutive_failures"] += 1

        if cb["consecutive_failures"] >= CIRCUIT_BREAKER_THRESHOLD:
            cb["suspended_until"] = now + CIRCUIT_BREAKER_COOLDOWN_S
            return {
                "circuit_open": True,
                "failures": cb["consecutive_failures"],
                "suspended_until": cb["suspended_until"],
                "cooldown_seconds": CIRCUIT_BREAKER_COOLDOWN_S,
            }

        return {
            "circuit_open": False,
            "failures": cb["consecutive_failures"],
            "remaining_before_trip": CIRCUIT_BREAKER_THRESHOLD - cb["consecutive_failures"],
        }


def is_circuit_open(agent_id: str) -> bool:
    """Verifie si le circuit breaker est ouvert (agent temporairement suspendu)."""
    if agent_id not in _circuit_breakers:
        return False

    cb = _circuit_breakers[agent_id]

    # La suspension a expire
    if cb["suspended_until"] > 0 and time.time() >= cb["suspended_until"]:
        cb["consecutive_failures"] = 0
        cb["suspended_until"] = 0
        return False

    return cb["suspended_until"] > 0


# ── Application des penalites ──

async def _log_violation(agent_id: str, violation_type: str, details: str, penalty: str):
    """Enregistre une violation SLA en base."""
    await _ensure_schema()
    from core.database import db

    await db.raw_execute(
        "INSERT INTO sla_violations (agent_id, violation_type, details, penalty_applied, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (agent_id, violation_type, details, penalty, datetime.utcnow().isoformat()),
    )


async def _apply_penalty(agent_id: str, new_penalty: str, composite_score: float):
    """Applique la penalite dans agent_scores et envoie une alerte si grave."""
    from core.database import db

    await db.raw_execute(
        "UPDATE agent_scores SET penalty_level = ? WHERE agent_id = ?",
        (new_penalty, agent_id),
    )

    # Alertes Telegram pour suspensions et delistings
    if new_penalty in ("suspended", "delisted"):
        try:
            from infra.alerts import alert_system
            await alert_system(
                f"SLA Enforcement — {new_penalty.upper()}",
                f"Agent {agent_id[:16]}... {new_penalty} (score: {composite_score:.2f}). "
                f"L'agent est retire du marketplace actif.",
            )
        except Exception as e:
            logger.error(f"[SLA] Erreur alerte Telegram: {e}")

    logger.info(f"[SLA] Agent {agent_id[:16]}... -> penalite: {new_penalty} (score: {composite_score:.2f})")


async def enforce_sla_single(agent_id: str) -> dict:
    """Verifie et applique les SLA pour un seul agent. Retourne le resultat."""
    await _ensure_schema()
    from core.database import db

    # Recuperer le score actuel
    rows = await db.raw_execute_fetchall(
        "SELECT composite_score, penalty_level FROM agent_scores WHERE agent_id = ?",
        (agent_id,),
    )

    if not rows:
        return {"agent_id": agent_id, "error": "Pas de score enregistre"}

    score = rows[0]["composite_score"]
    current_penalty = rows[0]["penalty_level"]

    # Determiner la nouvelle penalite
    if _is_delisted(score, current_penalty):
        new_penalty = "delisted"
    else:
        new_penalty = _determine_penalty(score)

    result = {
        "agent_id": agent_id,
        "composite_score": score,
        "previous_penalty": current_penalty,
        "new_penalty": new_penalty,
        "changed": new_penalty != current_penalty,
    }

    # Si la penalite a change, l'enregistrer
    if new_penalty != current_penalty:
        await _log_violation(
            agent_id=agent_id,
            violation_type="penalty_change",
            details=f"Score {score:.4f}: {current_penalty} -> {new_penalty}",
            penalty=new_penalty,
        )
        await _apply_penalty(agent_id, new_penalty, score)

    return result


async def enforce_sla_all() -> dict:
    """Verifie et applique les SLA pour tous les agents avec un score.

    Doit etre appele periodiquement par le scheduler (toutes les heures, apres recalculate_all_scores).
    """
    await _ensure_schema()
    from core.database import db

    rows = await db.raw_execute_fetchall(
        "SELECT agent_id, composite_score, penalty_level FROM agent_scores",
    )

    if not rows:
        return {"checked": 0, "changed": 0, "penalties": {}}

    checked = 0
    changed = 0
    penalty_summary: dict = {}

    for row in rows:
        agent_id = row["agent_id"]
        score = row["composite_score"]
        current_penalty = row["penalty_level"]

        # Determiner la nouvelle penalite
        if _is_delisted(score, current_penalty):
            new_penalty = "delisted"
        else:
            new_penalty = _determine_penalty(score)

        # Compter par penalite
        penalty_summary[new_penalty] = penalty_summary.get(new_penalty, 0) + 1

        if new_penalty != current_penalty:
            await _log_violation(
                agent_id=agent_id,
                violation_type="penalty_change",
                details=f"Score {score:.4f}: {current_penalty} -> {new_penalty}",
                penalty=new_penalty,
            )
            await _apply_penalty(agent_id, new_penalty, score)
            changed += 1

        checked += 1

    logger.info(f"[SLA] Verification complete: {checked} agents, {changed} changements")
    return {"checked": checked, "changed": changed, "penalties": penalty_summary}


# ── Endpoints API ──

@router.get("/tiers")
async def get_sla_tiers():
    """Liste les tiers SLA disponibles et leurs exigences."""
    return {
        "tiers": SLA_TIERS,
        "penalty_levels": [
            {"level": p["level"], "min_score": p["min_score"], "description": p["description"]}
            for p in PENALTY_LEVELS
        ],
        "circuit_breaker": {
            "threshold": CIRCUIT_BREAKER_THRESHOLD,
            "cooldown_seconds": CIRCUIT_BREAKER_COOLDOWN_S,
        },
    }


@router.get("/violations/{agent_id}")
async def get_agent_violations(
    agent_id: str,
    limit: int = 50,
):
    """Historique des violations SLA pour un agent."""
    await _ensure_schema()
    from core.database import db

    rows = await db.raw_execute_fetchall(
        "SELECT id, agent_id, violation_type, details, penalty_applied, created_at "
        "FROM sla_violations WHERE agent_id = ? ORDER BY created_at DESC LIMIT ?",
        (agent_id, limit),
    )

    violations = [dict(r) for r in rows]

    # Statut circuit breaker actuel
    cb_open = is_circuit_open(agent_id)
    cb_info = _circuit_breakers.get(agent_id, {"consecutive_failures": 0, "suspended_until": 0})

    return {
        "agent_id": agent_id,
        "violations": violations,
        "count": len(violations),
        "circuit_breaker": {
            "open": cb_open,
            "consecutive_failures": cb_info["consecutive_failures"],
            "suspended_until": cb_info["suspended_until"] if cb_open else None,
        },
    }


@router.post("/check")
async def manual_sla_check(req: dict, request: Request):
    """Declenche manuellement une verification SLA pour un agent (admin uniquement).

    Body: {agent_id: str}
    """
    # Verification admin (API key CEO ou appel local)
    api_key = request.headers.get("X-API-Key", "")
    ceo_key = os.getenv("CEO_API_KEY", "")
    is_local = request.client and request.client.host in ("127.0.0.1", "::1", "localhost")
    if not is_local and (not api_key or api_key != ceo_key):
        raise HTTPException(403, "Admin access required")

    agent_id = req.get("agent_id", "")
    if not agent_id:
        return {"error": "agent_id requis"}

    result = await enforce_sla_single(agent_id)
    return result
