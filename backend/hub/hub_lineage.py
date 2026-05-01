"""MAXIA Hub Phase 4 — Lignées d'agents (spawn, héritage de réputation, arbre généalogique).

Routes :
  POST /api/hub/lineage/spawn              → déclarer un enfant Hub (statut pending)
  GET  /api/hub/lineage/{hub_id}           → arbre généalogique complet (max 3 générations)
  GET  /api/hub/lineage/{hub_id}/children  → liste enfants directs actifs
  POST /api/hub/lineage/accept/{lineage_id} → enfant accepte la relation

Règles métier :
  - Un agent Hub ne peut avoir qu'UN seul parent
  - Max 10 enfants directs actifs sans augmenter le stake
  - Max 3 générations (gen 0 fondateur, gen 1, gen 2 — gen 3 interdit)
  - Détection de cycle (un ancêtre ne peut pas devenir enfant)
  - Parent doit avoir score >= 10 pour spawner
  - Héritage de réputation : 10% du score parent → bonus appliqué à l'acceptation
  - Badge DYNASTY : gen==0, score>=50, dynasty_size>=3
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Header

import base58
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

from core.database import db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/hub/lineage", tags=["hub-lineage"])

# ─── Constantes ──────────────────────────────────────────────────────────────

_MAX_CHILDREN = 10
_MIN_PARENT_SCORE = 10
_MAX_GENERATION = 2          # gen 0, 1, 2 autorisées → gen 3 bloquée
_HEARTBEAT_WINDOW = 60       # secondes de tolérance pour le timestamp
_SCORE_INHERITANCE_PCT = 0.10
_DYNASTY_MIN_SCORE = 50
_DYNASTY_MIN_SIZE = 3


# ═══════════════════════════════════════════════════════════════════════════════
# Auth helper (même pattern que hub_registry)
# ═══════════════════════════════════════════════════════════════════════════════

def _verify_hub_sig(public_key_b58: str, hub_id: str, ts: int, sig_b58: str) -> bool:
    """Vérifie la signature ed25519 de (hub_id:ts)."""
    try:
        pk_bytes = base58.b58decode(public_key_b58)
        sig_bytes = base58.b58decode(sig_b58)
        message = f"{hub_id}:{ts}".encode()
        VerifyKey(pk_bytes).verify(message, sig_bytes)
        return True
    except (BadSignatureError, Exception):
        return False


async def _require_hub_agent_auth(
    x_hub_id: Optional[str],
    x_hub_sig: Optional[str],
    x_hub_ts: Optional[str],
) -> dict:
    """Vérifie l'authentification d'un agent Hub via ed25519.

    Retourne la ligne hub_agents si valide. Lève HTTPException sinon.
    """
    if not x_hub_id or not x_hub_sig or not x_hub_ts:
        raise HTTPException(401, "Missing hub auth headers (X-Hub-ID, X-Hub-Sig, X-Hub-Ts)")

    try:
        ts = int(x_hub_ts)
    except ValueError:
        raise HTTPException(401, "X-Hub-Ts must be a unix timestamp integer")

    now = int(time.time())
    if abs(now - ts) > _HEARTBEAT_WINDOW:
        raise HTTPException(401, "Timestamp out of window (±60s)")

    row = await db._fetchone(
        "SELECT hub_id, name, public_key, score, status FROM hub_agents WHERE hub_id=?",
        (x_hub_id,),
    )
    if row is None:
        raise HTTPException(404, "Hub agent not found")
    if row["status"] != "active":
        raise HTTPException(403, "Hub agent is not active")

    if not _verify_hub_sig(row["public_key"], x_hub_id, ts, x_hub_sig):
        raise HTTPException(403, "Invalid signature")

    return dict(row)


# ═══════════════════════════════════════════════════════════════════════════════
# Fonctions métier (testables indépendamment)
# ═══════════════════════════════════════════════════════════════════════════════

def _compute_inherited_bonus(parent_score: int) -> int:
    """Calcule le bonus de score hérité : round(parent_score * 10%)."""
    return round(parent_score * _SCORE_INHERITANCE_PCT)


def _compute_dynasty_badge(generation: int, score: int, dynasty_size: int) -> bool:
    """Retourne True si l'agent mérite le badge DYNASTY."""
    return generation == 0 and score >= _DYNASTY_MIN_SCORE and dynasty_size >= _DYNASTY_MIN_SIZE


async def _is_ancestor(potential_ancestor: str, hub_id: str) -> bool:
    """Retourne True si potential_ancestor est un ancêtre de hub_id.

    Remonte la chaîne de parenté de hub_id jusqu'à trouver potential_ancestor
    ou atteindre la racine. Protège contre les cycles infinis (max 10 niveaux).
    """
    current = hub_id
    for _ in range(10):  # limite de sécurité anti-boucle infinie
        rows = await db._fetchall(
            "SELECT parent_hub_id FROM hub_lineage WHERE child_hub_id=? AND status='active'",
            (current,),
        )
        if not rows:
            return False
        parent_id = rows[0]["parent_hub_id"]
        if parent_id == potential_ancestor:
            return True
        current = parent_id
    return False


async def _get_agent_generation(hub_id: str) -> int:
    """Retourne la génération de l'agent (0=fondateur, 1=enfant, 2=petit-enfant...)."""
    generation = 0
    current = hub_id
    for _ in range(10):
        row = await db._fetchone(
            "SELECT parent_hub_id FROM hub_lineage WHERE child_hub_id=? AND status='active'",
            (current,),
        )
        if row is None:
            break
        generation += 1
        current = row["parent_hub_id"]
    return generation


async def _count_dynasty_size(hub_id: str) -> int:
    """Compte le fondateur + tous ses descendants récursivement (BFS, max 3 niveaux)."""
    total = 1  # le fondateur lui-même
    queue = [hub_id]
    depth = 0
    while queue and depth <= _MAX_GENERATION:
        next_queue = []
        for current_id in queue:
            children = await db._fetchall(
                "SELECT hub_id FROM hub_agents ha "
                "JOIN hub_lineage hl ON ha.hub_id = hl.child_hub_id "
                "WHERE hl.parent_hub_id=? AND hl.status='active' AND ha.status='active'",
                (current_id,),
            )
            for child in children:
                total += 1
                next_queue.append(child["hub_id"])
        queue = next_queue
        depth += 1
    return total


async def _build_lineage_tree(
    hub_id: str,
    name: str,
    score: int,
    current_gen: int,
    max_gen: int,
) -> dict:
    """Construit récursivement l'arbre de lignée jusqu'à max_gen générations."""
    node: dict = {
        "hub_id": hub_id,
        "name": name,
        "score": score,
        "generation": current_gen,
        "dynasty_badge": False,  # calculé au niveau racine
        "children": [],
    }

    if current_gen >= max_gen:
        return node

    children_rows = await db._fetchall(
        "SELECT ha.hub_id, ha.name, ha.score "
        "FROM hub_agents ha "
        "JOIN hub_lineage hl ON ha.hub_id = hl.child_hub_id "
        "WHERE hl.parent_hub_id=? AND hl.status='active' AND ha.status='active'",
        (hub_id,),
    )

    for child_row in children_rows:
        child_node = await _build_lineage_tree(
            hub_id=child_row["hub_id"],
            name=child_row["name"],
            score=child_row["score"],
            current_gen=current_gen + 1,
            max_gen=max_gen,
        )
        node["children"].append(child_node)

    return node


# ═══════════════════════════════════════════════════════════════════════════════
# Fonctions de service (appelées par les routes ET les tests)
# ═══════════════════════════════════════════════════════════════════════════════

async def spawn_lineage(
    parent_hub_id: str,
    child_hub_id: str,
    reason: str,
) -> dict:
    """Crée une relation de lignée en statut 'pending'.

    Applique toutes les règles métier (score, max enfants, cycle, génération).
    """
    # Règle 0 : auto-spawn interdit
    if parent_hub_id == child_hub_id:
        raise HTTPException(400, "An agent cannot be its own parent")

    # Règle 1 : parent doit exister avec score >= MIN
    parent_row = await db._fetchone(
        "SELECT hub_id, name, score, status FROM hub_agents WHERE hub_id=?",
        (parent_hub_id,),
    )
    if parent_row is None:
        raise HTTPException(404, "Parent hub agent not found")
    if int(parent_row["score"]) < _MIN_PARENT_SCORE:
        raise HTTPException(403, f"Parent score too low (minimum {_MIN_PARENT_SCORE})")

    # Règle 2 : enfant doit exister
    child_row = await db._fetchone(
        "SELECT hub_id, name, score, status FROM hub_agents WHERE hub_id=?",
        (child_hub_id,),
    )
    if child_row is None:
        raise HTTPException(404, "Child hub agent not found")

    # Règle 3 : enfant n'a pas déjà un parent
    existing_parent = await db._fetchone(
        "SELECT lineage_id FROM hub_lineage WHERE child_hub_id=? AND status IN ('active', 'pending')",
        (child_hub_id,),
    )
    if existing_parent is not None:
        raise HTTPException(409, "This agent already has a parent")

    # Règle 4 : max 10 enfants actifs par parent
    children_count_row = await db._fetchone(
        "SELECT COUNT(*) as cnt FROM hub_lineage "
        "WHERE parent_hub_id=? AND status='active'",
        (parent_hub_id,),
    )
    cnt = int(children_count_row["cnt"]) if children_count_row else 0
    if cnt >= _MAX_CHILDREN:
        raise HTTPException(403, "Increase stake to spawn more (max 10 active children)")

    # Règle 5 : max génération (parent ne peut pas être en génération MAX)
    parent_generation = await _get_agent_generation(parent_hub_id)
    if parent_generation >= _MAX_GENERATION:
        raise HTTPException(
            403,
            f"Maximum lineage depth ({_MAX_GENERATION} generations) reached. "
            "Cannot spawn further.",
        )

    # Règle 6 : détection de cycle (child_hub_id ne doit pas être un ancêtre du parent)
    cycle_detected = await _is_ancestor(
        potential_ancestor=child_hub_id,
        hub_id=parent_hub_id,
    )
    if cycle_detected:
        raise HTTPException(400, "Cycle detected: child is already an ancestor of parent")

    # Calcul du bonus héréditaire
    parent_score = int(parent_row["score"])
    inherited_bonus = _compute_inherited_bonus(parent_score)
    child_generation = parent_generation + 1

    # Créer la lignée en statut pending
    lineage_id = uuid.uuid4().hex
    now = int(time.time())

    await db.raw_execute(
        "INSERT INTO hub_lineage"
        "(lineage_id, parent_hub_id, child_hub_id, generation, "
        "inherited_score_bonus, status, reason, created_at) "
        "VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)",
        (lineage_id, parent_hub_id, child_hub_id, child_generation,
         inherited_bonus, reason[:200], now),
    )

    logger.info(
        "[HUB-LINEAGE] Spawn pending: %s → %s (gen=%d, bonus=+%d)",
        parent_hub_id[:8], child_hub_id[:8], child_generation, inherited_bonus,
    )

    return {
        "status": "pending",
        "lineage_id": lineage_id,
        "parent_hub_id": parent_hub_id,
        "child_hub_id": child_hub_id,
        "generation": child_generation,
        "inherited_score_bonus": inherited_bonus,
        "message": "Lineage pending. Child agent must accept via POST /api/hub/lineage/accept/{lineage_id}",
    }


async def accept_lineage(lineage_id: str, child_hub_id: str) -> dict:
    """L'enfant accepte la relation de lignée.

    - Passe le statut à 'active'
    - Applique le bonus de réputation à l'enfant (MIN(100, score + bonus))
    """
    # Vérifier que la lignée existe
    lin_row = await db._fetchone(
        "SELECT lineage_id, parent_hub_id, child_hub_id, generation, "
        "inherited_score_bonus, status FROM hub_lineage WHERE lineage_id=?",
        (lineage_id,),
    )
    if lin_row is None:
        raise HTTPException(404, "Lineage not found")

    lin = dict(lin_row)

    # Vérifier que c'est bien l'enfant qui accepte
    if lin["child_hub_id"] != child_hub_id:
        raise HTTPException(403, "Only the designated child agent can accept this lineage")

    if lin["status"] != "pending":
        raise HTTPException(409, f"Lineage is already in status '{lin['status']}'")

    # Charger parent et enfant
    parent_row = await db._fetchone(
        "SELECT hub_id, score FROM hub_agents WHERE hub_id=?",
        (lin["parent_hub_id"],),
    )
    child_row = await db._fetchone(
        "SELECT hub_id, score FROM hub_agents WHERE hub_id=?",
        (lin["child_hub_id"],),
    )

    # Calculer le bonus (re-calculé depuis le score actuel du parent)
    parent_score = int(parent_row["score"]) if parent_row else 0
    inherited_bonus = int(lin["inherited_score_bonus"])
    child_score = int(child_row["score"]) if child_row else 0

    new_score = min(100, child_score + inherited_bonus)
    now = int(time.time())

    # Activer la lignée
    await db.raw_execute(
        "UPDATE hub_lineage SET status='active', accepted_at=? WHERE lineage_id=?",
        (now, lineage_id),
    )

    # Appliquer le bonus de score à l'enfant
    if inherited_bonus > 0:
        await db.raw_execute(
            "UPDATE hub_agents SET score=? WHERE hub_id=?",
            (new_score, child_hub_id),
        )

    logger.info(
        "[HUB-LINEAGE] Accepted: %s → %s (bonus=+%d, score %d→%d)",
        lin["parent_hub_id"][:8], child_hub_id[:8],
        inherited_bonus, child_score, new_score,
    )

    return {
        "status": "active",
        "lineage_id": lineage_id,
        "parent_hub_id": lin["parent_hub_id"],
        "child_hub_id": child_hub_id,
        "generation": int(lin["generation"]),
        "inherited_bonus": inherited_bonus,
        "child_score_before": child_score,
        "child_score_after": new_score,
    }


async def get_lineage_tree(hub_id: str) -> dict:
    """Retourne l'arbre généalogique complet (max 3 générations) d'un agent."""
    agent_row = await db._fetchone(
        "SELECT hub_id, name, score, status FROM hub_agents WHERE hub_id=?",
        (hub_id,),
    )
    if agent_row is None:
        raise HTTPException(404, "Hub agent not found")

    # Déterminer la génération de cet agent dans sa propre lignée
    generation = await _get_agent_generation(hub_id)

    # Construire l'arbre
    tree = await _build_lineage_tree(
        hub_id=hub_id,
        name=agent_row["name"],
        score=int(agent_row["score"]),
        current_gen=generation,
        max_gen=generation + _MAX_GENERATION,
    )

    # Calculer dynasty_size
    dynasty_size = await _count_dynasty_size(hub_id)

    # Badge DYNASTY
    dynasty_badge = _compute_dynasty_badge(
        generation=generation,
        score=int(agent_row["score"]),
        dynasty_size=dynasty_size,
    )
    tree["dynasty_badge"] = dynasty_badge

    # Calculer max_generation réelle dans l'arbre
    def _max_gen_in_tree(node: dict, depth: int = 0) -> int:
        if not node["children"]:
            return depth
        return max(_max_gen_in_tree(child, depth + 1) for child in node["children"])

    max_gen = _max_gen_in_tree(tree)

    return {
        "root": tree,
        "dynasty_size": dynasty_size,
        "max_generation": max_gen,
    }


async def list_direct_children(hub_id: str) -> dict:
    """Retourne les enfants directs actifs d'un agent."""
    agent_row = await db._fetchone(
        "SELECT hub_id, name FROM hub_agents WHERE hub_id=?",
        (hub_id,),
    )
    if agent_row is None:
        raise HTTPException(404, "Hub agent not found")

    children_rows = await db._fetchall(
        "SELECT ha.hub_id, ha.name, ha.score, ha.status, hl.generation, hl.inherited_score_bonus "
        "FROM hub_agents ha "
        "JOIN hub_lineage hl ON ha.hub_id = hl.child_hub_id "
        "WHERE hl.parent_hub_id=? AND hl.status='active' AND ha.status='active'",
        (hub_id,),
    )

    children = [dict(r) for r in children_rows]

    return {
        "parent_hub_id": hub_id,
        "total": len(children),
        "children": children,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Routes FastAPI
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/spawn")
async def route_spawn_lineage(
    child_hub_id: str,
    reason: str = "",
    x_hub_id: Optional[str] = Header(None),
    x_hub_sig: Optional[str] = Header(None),
    x_hub_ts: Optional[str] = Header(None),
):
    """Déclarer un enfant Hub. Le parent doit être authentifié via ed25519."""
    agent = await _require_hub_agent_auth(x_hub_id, x_hub_sig, x_hub_ts)
    return await spawn_lineage(
        parent_hub_id=agent["hub_id"],
        child_hub_id=child_hub_id,
        reason=reason,
    )


@router.post("/accept/{lineage_id}")
async def route_accept_lineage(
    lineage_id: str,
    x_hub_id: Optional[str] = Header(None),
    x_hub_sig: Optional[str] = Header(None),
    x_hub_ts: Optional[str] = Header(None),
):
    """L'enfant accepte la relation de lignée."""
    agent = await _require_hub_agent_auth(x_hub_id, x_hub_sig, x_hub_ts)
    return await accept_lineage(lineage_id=lineage_id, child_hub_id=agent["hub_id"])


@router.get("/{hub_id}")
async def route_get_lineage(hub_id: str):
    """Arbre généalogique complet (max 3 générations). Public."""
    return await get_lineage_tree(hub_id)


@router.get("/{hub_id}/children")
async def route_list_children(hub_id: str):
    """Liste des enfants directs actifs. Public."""
    return await list_direct_children(hub_id)
