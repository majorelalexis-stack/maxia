"""MAXIA Art.58 — Agent Subcontracting (delegation automatique de sous-taches)

Systeme de sous-traitance entre agents IA : un agent principal recoit une tache
complexe, la decompose en sous-taches, decouvre d'autres agents MAXIA capables
de les executer, negocie les prix, cree des escrows, et orchestre le travail.

Tables :
  - subcontracts : contrats principaux
  - subtasks : sous-taches assignees aux agents

Commission MAXIA : 1% sur chaque paiement de sous-tache (en plus de la
commission standard du marketplace).
"""

import uuid, time, json
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from typing import Optional

from auth import require_auth

router = APIRouter(prefix="/api/subcontract", tags=["agent-subcontracting"])

# ── Commission MAXIA sur les sous-taches ──
SUBCONTRACT_COMMISSION_PCT = 1.0

# ── Schema lazy ──

_schema_ready = False

_SUBCONTRACT_SCHEMA = """
CREATE TABLE IF NOT EXISTS subcontracts (
    id TEXT PRIMARY KEY,
    principal_agent_id TEXT NOT NULL,
    task TEXT NOT NULL,
    budget_usdc REAL NOT NULL,
    spent_usdc REAL NOT NULL DEFAULT 0,
    commission_total REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_subcontracts_agent ON subcontracts(principal_agent_id);
CREATE INDEX IF NOT EXISTS idx_subcontracts_status ON subcontracts(status);

CREATE TABLE IF NOT EXISTS subtasks (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL,
    type TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    assigned_agent_id TEXT,
    price_usdc REAL NOT NULL DEFAULT 0,
    commission_usdc REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    result TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT,
    FOREIGN KEY (contract_id) REFERENCES subcontracts(id)
);

CREATE INDEX IF NOT EXISTS idx_subtasks_contract ON subtasks(contract_id);
CREATE INDEX IF NOT EXISTS idx_subtasks_agent ON subtasks(assigned_agent_id);
CREATE INDEX IF NOT EXISTS idx_subtasks_type ON subtasks(type);
CREATE INDEX IF NOT EXISTS idx_subtasks_status ON subtasks(status);
"""


async def _ensure_schema():
    """Cree les tables subcontracts + subtasks si elles n'existent pas encore."""
    global _schema_ready
    if _schema_ready:
        return
    try:
        from database import db
        await db.raw_executescript(_SUBCONTRACT_SCHEMA)
        _schema_ready = True
        print("[Subcontract] Schema pret")
    except Exception as e:
        print(f"[Subcontract] Erreur schema: {e}")


# ── Pydantic models ──

class SubtaskDef(BaseModel):
    """Definition d'une sous-tache a creer avec le contrat."""
    type: str = Field(..., min_length=1, max_length=80)
    description: str = Field(default="", max_length=2000)


class CreateContractRequest(BaseModel):
    """Requete de creation d'un contrat de sous-traitance."""
    task_description: str = Field(..., min_length=5, max_length=5000)
    subtasks: list[SubtaskDef] = Field(..., min_length=1, max_length=50)
    budget_usdc: float = Field(..., gt=0, le=100000)


class AssignSubtaskRequest(BaseModel):
    """Requete d'assignation d'un agent a une sous-tache."""
    subtask_id: str = Field(..., min_length=5, max_length=80)
    agent_id: str = Field(..., min_length=5, max_length=120)
    price_usdc: float = Field(..., gt=0, le=50000)


class CompleteSubtaskRequest(BaseModel):
    """Requete de completion d'une sous-tache."""
    subtask_id: str = Field(..., min_length=5, max_length=80)
    result: str = Field(..., min_length=1, max_length=50000)


# ══════════════════════════════════════════════════════════════════════════════
# FONCTIONS METIER
# ══════════════════════════════════════════════════════════════════════════════

async def create_contract(
    principal_agent_id: str,
    task_description: str,
    subtasks: list[dict],
    budget_usdc: float,
) -> dict:
    """Cree un contrat de sous-traitance avec ses sous-taches.

    L'agent principal definit la tache globale, la decompose en sous-taches,
    et reserve un budget maximum en USDC.
    """
    await _ensure_schema()
    from database import db

    if budget_usdc <= 0:
        raise HTTPException(400, "Le budget doit etre positif")
    if not subtasks:
        raise HTTPException(400, "Au moins une sous-tache requise")

    contract_id = f"SC-{uuid.uuid4().hex[:12].upper()}"

    await db.raw_execute(
        "INSERT INTO subcontracts (id, principal_agent_id, task, budget_usdc, status) "
        "VALUES (?, ?, ?, ?, 'open')",
        (contract_id, principal_agent_id, task_description, budget_usdc),
    )

    # Creer les sous-taches
    created_subtasks = []
    for st in subtasks:
        subtask_id = f"ST-{uuid.uuid4().hex[:12].upper()}"
        st_type = st.get("type", "general")
        st_desc = st.get("description", "")

        await db.raw_execute(
            "INSERT INTO subtasks (id, contract_id, type, description, status) "
            "VALUES (?, ?, ?, ?, 'pending')",
            (subtask_id, contract_id, st_type, st_desc),
        )

        created_subtasks.append({
            "subtask_id": subtask_id,
            "type": st_type,
            "description": st_desc,
            "status": "pending",
        })

    return {
        "contract_id": contract_id,
        "principal_agent_id": principal_agent_id,
        "task": task_description,
        "budget_usdc": budget_usdc,
        "subtasks": created_subtasks,
        "status": "open",
    }


async def find_subcontractors(subtask_type: str, max_price: float = 0) -> list:
    """Decouvre les agents capables d'executer un type de sous-tache.

    Cherche dans les services enregistres sur le marketplace par type.
    Filtre par prix max si specifie.
    """
    await _ensure_schema()
    from database import db

    # Chercher dans les services enregistres (agent_services)
    if max_price > 0:
        rows = await db.raw_execute_fetchall(
            "SELECT id, agent_api_key, agent_name, agent_wallet, name, description, "
            "type, price_usdc, rating, rating_count, sales "
            "FROM agent_services "
            "WHERE status = 'active' AND type = ? AND price_usdc <= ? "
            "ORDER BY rating DESC, sales DESC LIMIT 20",
            (subtask_type, max_price),
        )
    else:
        rows = await db.raw_execute_fetchall(
            "SELECT id, agent_api_key, agent_name, agent_wallet, name, description, "
            "type, price_usdc, rating, rating_count, sales "
            "FROM agent_services "
            "WHERE status = 'active' AND type = ? "
            "ORDER BY rating DESC, sales DESC LIMIT 20",
            (subtask_type,),
        )

    cols = ["id", "agent_api_key", "agent_name", "agent_wallet", "name",
            "description", "type", "price_usdc", "rating", "rating_count", "sales"]

    results = []
    for row in rows:
        r = dict(zip(cols, row if not isinstance(row, dict) else list(row.values()),
        )) if not isinstance(row, dict) else row

        results.append({
            "service_id": r["id"],
            "agent_id": r["agent_api_key"],
            "agent_name": r["agent_name"],
            "agent_wallet": r["agent_wallet"],
            "service_name": r["name"],
            "description": r["description"],
            "type": r["type"],
            "price_usdc": r["price_usdc"],
            "rating": r["rating"],
            "sales": r["sales"],
        })

    return results


async def assign_subtask(
    contract_id: str,
    subtask_id: str,
    agent_id: str,
    price_usdc: float,
    wallet: str,
) -> dict:
    """Assigne un agent a une sous-tache avec un prix negocie.

    Verifie que le prix total ne depasse pas le budget du contrat.
    """
    await _ensure_schema()
    from database import db

    # Verifier le contrat
    contracts = await db.raw_execute_fetchall(
        "SELECT id, principal_agent_id, budget_usdc, spent_usdc, status "
        "FROM subcontracts WHERE id = ?",
        (contract_id,),
    )
    if not contracts:
        raise HTTPException(404, "Contrat introuvable")

    c = dict(zip(
        ["id", "principal_agent_id", "budget_usdc", "spent_usdc", "status"],
        contracts[0] if not isinstance(contracts[0], dict) else list(contracts[0].values()),
    )) if not isinstance(contracts[0], dict) else contracts[0]

    if c["status"] not in ("open", "in_progress"):
        raise HTTPException(400, f"Contrat {c['status']} — assignation impossible")

    # Seul l'agent principal peut assigner
    if c["principal_agent_id"] != wallet:
        raise HTTPException(403, "Seul l'agent principal peut assigner des sous-taches")

    # Verifier le budget restant
    remaining_budget = c["budget_usdc"] - c["spent_usdc"]
    if price_usdc > remaining_budget:
        raise HTTPException(400,
            f"Budget insuffisant: {price_usdc:.2f} demande, "
            f"{remaining_budget:.2f} restant sur {c['budget_usdc']:.2f}")

    # Verifier la sous-tache
    tasks = await db.raw_execute_fetchall(
        "SELECT id, contract_id, status FROM subtasks WHERE id = ? AND contract_id = ?",
        (subtask_id, contract_id),
    )
    if not tasks:
        raise HTTPException(404, "Sous-tache introuvable dans ce contrat")

    t = dict(zip(
        ["id", "contract_id", "status"],
        tasks[0] if not isinstance(tasks[0], dict) else list(tasks[0].values()),
    )) if not isinstance(tasks[0], dict) else tasks[0]

    if t["status"] != "pending":
        raise HTTPException(400, f"Sous-tache deja {t['status']}")

    # Calculer la commission MAXIA
    commission = round(price_usdc * SUBCONTRACT_COMMISSION_PCT / 100, 6)

    # Assigner
    await db.raw_execute(
        "UPDATE subtasks SET assigned_agent_id = ?, price_usdc = ?, "
        "commission_usdc = ?, status = 'assigned' WHERE id = ?",
        (agent_id, price_usdc, commission, subtask_id),
    )

    # Mettre a jour le contrat
    new_spent = round(c["spent_usdc"] + price_usdc, 6)
    await db.raw_execute(
        "UPDATE subcontracts SET spent_usdc = ?, status = 'in_progress' WHERE id = ?",
        (new_spent, contract_id),
    )

    return {
        "contract_id": contract_id,
        "subtask_id": subtask_id,
        "assigned_agent_id": agent_id,
        "price_usdc": price_usdc,
        "commission_usdc": commission,
        "agent_gets_usdc": round(price_usdc - commission, 6),
        "budget_remaining": round(c["budget_usdc"] - new_spent, 6),
    }


async def complete_subtask(
    contract_id: str,
    subtask_id: str,
    result: str,
    wallet: str,
) -> dict:
    """Marque une sous-tache comme terminee avec son resultat.

    Seul l'agent assigne peut marquer la tache comme terminee.
    """
    await _ensure_schema()
    from database import db

    # Verifier la sous-tache
    tasks = await db.raw_execute_fetchall(
        "SELECT id, contract_id, assigned_agent_id, price_usdc, status "
        "FROM subtasks WHERE id = ? AND contract_id = ?",
        (subtask_id, contract_id),
    )
    if not tasks:
        raise HTTPException(404, "Sous-tache introuvable dans ce contrat")

    t = dict(zip(
        ["id", "contract_id", "assigned_agent_id", "price_usdc", "status"],
        tasks[0] if not isinstance(tasks[0], dict) else list(tasks[0].values()),
    )) if not isinstance(tasks[0], dict) else tasks[0]

    if t["status"] != "assigned":
        raise HTTPException(400, f"Sous-tache {t['status']} — completion impossible")

    # Seul l'agent assigne ou le principal peut completer
    contracts = await db.raw_execute_fetchall(
        "SELECT principal_agent_id FROM subcontracts WHERE id = ?",
        (contract_id,),
    )
    principal = None
    if contracts:
        principal = (contracts[0]["principal_agent_id"]
                     if isinstance(contracts[0], dict)
                     else contracts[0][0])

    if wallet not in (t["assigned_agent_id"], principal):
        raise HTTPException(403, "Seul l'agent assigne ou le principal peut completer")

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    await db.raw_execute(
        "UPDATE subtasks SET status = 'completed', result = ?, completed_at = ? "
        "WHERE id = ?",
        (result, now_iso, subtask_id),
    )

    return {
        "contract_id": contract_id,
        "subtask_id": subtask_id,
        "status": "completed",
        "price_usdc": t["price_usdc"],
        "completed_at": now_iso,
    }


async def get_contract_status(contract_id: str) -> dict:
    """Retourne le statut complet d'un contrat avec toutes ses sous-taches."""
    await _ensure_schema()
    from database import db

    # Charger le contrat
    contracts = await db.raw_execute_fetchall(
        "SELECT id, principal_agent_id, task, budget_usdc, spent_usdc, "
        "commission_total, status, created_at, completed_at "
        "FROM subcontracts WHERE id = ?",
        (contract_id,),
    )
    if not contracts:
        raise HTTPException(404, "Contrat introuvable")

    c_cols = ["id", "principal_agent_id", "task", "budget_usdc", "spent_usdc",
              "commission_total", "status", "created_at", "completed_at"]
    c = dict(zip(
        c_cols,
        contracts[0] if not isinstance(contracts[0], dict) else list(contracts[0].values()),
    )) if not isinstance(contracts[0], dict) else contracts[0]

    # Charger les sous-taches
    tasks = await db.raw_execute_fetchall(
        "SELECT id, type, description, assigned_agent_id, price_usdc, "
        "commission_usdc, status, result, created_at, completed_at "
        "FROM subtasks WHERE contract_id = ? ORDER BY created_at",
        (contract_id,),
    )

    t_cols = ["id", "type", "description", "assigned_agent_id", "price_usdc",
              "commission_usdc", "status", "result", "created_at", "completed_at"]

    subtask_list = []
    for row in tasks:
        t = dict(zip(
            t_cols,
            row if not isinstance(row, dict) else list(row.values()),
        )) if not isinstance(row, dict) else row

        subtask_list.append({
            "subtask_id": t["id"],
            "type": t["type"],
            "description": t["description"],
            "assigned_agent_id": t["assigned_agent_id"],
            "price_usdc": t["price_usdc"],
            "commission_usdc": t["commission_usdc"],
            "status": t["status"],
            "result": t["result"] if t["status"] == "completed" else None,
            "completed_at": t["completed_at"],
        })

    # Stats
    total_subtasks = len(subtask_list)
    completed = sum(1 for s in subtask_list if s["status"] == "completed")
    assigned = sum(1 for s in subtask_list if s["status"] == "assigned")
    pending = sum(1 for s in subtask_list if s["status"] == "pending")

    return {
        "contract_id": c["id"],
        "principal_agent_id": c["principal_agent_id"],
        "task": c["task"],
        "budget_usdc": c["budget_usdc"],
        "spent_usdc": c["spent_usdc"],
        "commission_total": c["commission_total"],
        "status": c["status"],
        "created_at": c["created_at"],
        "completed_at": c["completed_at"],
        "subtasks": subtask_list,
        "progress": {
            "total": total_subtasks,
            "pending": pending,
            "assigned": assigned,
            "completed": completed,
            "pct_complete": round(completed / total_subtasks * 100, 1) if total_subtasks else 0,
        },
    }


async def settle_contract(contract_id: str, wallet: str) -> dict:
    """Finalise un contrat — paie tous les sous-traitants et collecte les resultats.

    Toutes les sous-taches doivent etre completed. MAXIA prend 1% sur chaque
    paiement de sous-tache. L'agent principal recoit tous les resultats.
    """
    await _ensure_schema()
    from database import db

    # Charger le contrat
    contracts = await db.raw_execute_fetchall(
        "SELECT id, principal_agent_id, budget_usdc, spent_usdc, status "
        "FROM subcontracts WHERE id = ?",
        (contract_id,),
    )
    if not contracts:
        raise HTTPException(404, "Contrat introuvable")

    c = dict(zip(
        ["id", "principal_agent_id", "budget_usdc", "spent_usdc", "status"],
        contracts[0] if not isinstance(contracts[0], dict) else list(contracts[0].values()),
    )) if not isinstance(contracts[0], dict) else contracts[0]

    if c["status"] == "settled":
        raise HTTPException(400, "Contrat deja finalise")

    # Seul l'agent principal peut finaliser
    if c["principal_agent_id"] != wallet:
        raise HTTPException(403, "Seul l'agent principal peut finaliser le contrat")

    # Charger les sous-taches
    tasks = await db.raw_execute_fetchall(
        "SELECT id, assigned_agent_id, price_usdc, commission_usdc, status, result "
        "FROM subtasks WHERE contract_id = ?",
        (contract_id,),
    )

    t_cols = ["id", "assigned_agent_id", "price_usdc", "commission_usdc", "status", "result"]

    # Verifier que toutes les taches assignees sont completees
    payments = []
    results = []
    total_commission = 0.0
    total_paid = 0.0

    for row in tasks:
        t = dict(zip(
            t_cols,
            row if not isinstance(row, dict) else list(row.values()),
        )) if not isinstance(row, dict) else row

        if t["status"] == "assigned":
            raise HTTPException(400,
                f"Sous-tache {t['id']} encore en cours. "
                "Toutes les sous-taches assignees doivent etre completees avant le settlement.")

        if t["status"] == "completed":
            agent_gets = round(t["price_usdc"] - t["commission_usdc"], 6)
            payments.append({
                "subtask_id": t["id"],
                "agent_id": t["assigned_agent_id"],
                "price_usdc": t["price_usdc"],
                "commission_usdc": t["commission_usdc"],
                "agent_gets_usdc": agent_gets,
            })
            results.append({
                "subtask_id": t["id"],
                "result": t["result"],
            })
            total_commission += t["commission_usdc"]
            total_paid += t["price_usdc"]

    # Mettre a jour le contrat
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    refund = round(c["budget_usdc"] - total_paid, 6)

    await db.raw_execute(
        "UPDATE subcontracts SET status = 'settled', commission_total = ?, "
        "completed_at = ? WHERE id = ?",
        (round(total_commission, 6), now_iso, contract_id),
    )

    return {
        "contract_id": contract_id,
        "status": "settled",
        "total_paid_usdc": round(total_paid, 6),
        "total_commission_usdc": round(total_commission, 6),
        "refund_to_principal_usdc": max(refund, 0),
        "payments": payments,
        "results": results,
        "settled_at": now_iso,
    }


# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/create")
async def api_create_contract(req: CreateContractRequest, wallet: str = Depends(require_auth)):
    """Cree un contrat de sous-traitance.

    L'agent principal definit la tache, les sous-taches, et le budget max.
    Les sous-taches sont creees en statut 'pending' en attente d'assignation.
    """
    subtask_dicts = [{"type": s.type, "description": s.description} for s in req.subtasks]
    result = await create_contract(
        principal_agent_id=wallet,
        task_description=req.task_description,
        subtasks=subtask_dicts,
        budget_usdc=req.budget_usdc,
    )
    return {"ok": True, **result}


@router.get("/discover")
async def api_discover_agents(
    type: str = Query(..., min_length=1, description="Type de service recherche"),
    max_price: float = Query(0, ge=0, description="Prix maximum en USDC (0 = pas de limite)"),
):
    """Decouvre des agents capables d'executer un type de sous-tache.

    Cherche dans les services enregistres sur le marketplace.
    Trie par rating puis par nombre de ventes.
    """
    agents = await find_subcontractors(type, max_price)
    return {"ok": True, "count": len(agents), "agents": agents}


@router.get("/{contract_id}")
async def api_get_contract(contract_id: str):
    """Retourne le statut complet d'un contrat avec toutes ses sous-taches."""
    result = await get_contract_status(contract_id)
    return {"ok": True, **result}


@router.post("/{contract_id}/assign")
async def api_assign_subtask(
    contract_id: str,
    req: AssignSubtaskRequest,
    wallet: str = Depends(require_auth),
):
    """Assigne un agent a une sous-tache avec un prix negocie.

    Seul l'agent principal du contrat peut assigner. Le prix est debite
    du budget du contrat. Commission MAXIA : 1% par sous-tache.
    """
    result = await assign_subtask(
        contract_id=contract_id,
        subtask_id=req.subtask_id,
        agent_id=req.agent_id,
        price_usdc=req.price_usdc,
        wallet=wallet,
    )
    return {"ok": True, **result}


@router.post("/{contract_id}/complete")
async def api_complete_subtask(
    contract_id: str,
    req: CompleteSubtaskRequest,
    wallet: str = Depends(require_auth),
):
    """Marque une sous-tache comme terminee avec son resultat.

    Seul l'agent assigne ou le principal peut completer la tache.
    """
    result = await complete_subtask(
        contract_id=contract_id,
        subtask_id=req.subtask_id,
        result=req.result,
        wallet=wallet,
    )
    return {"ok": True, **result}


@router.post("/{contract_id}/settle")
async def api_settle_contract(contract_id: str, wallet: str = Depends(require_auth)):
    """Finalise un contrat — paie tous les sous-traitants.

    Toutes les sous-taches assignees doivent etre completees. MAXIA prend 1%
    sur chaque sous-tache. Le budget non utilise est rembourse au principal.
    """
    result = await settle_contract(contract_id, wallet)
    return {"ok": True, **result}


# ══════════════════════════════════════════════════════════════════════════════

print("[Subcontract] Art.58 Agent Subcontracting charge — delegation automatique de sous-taches")
