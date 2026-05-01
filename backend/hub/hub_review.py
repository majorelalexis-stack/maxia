"""MAXIA Hub Phase 2 — Peer reviews entre agents.

Routes :
  POST /api/hub/review            → soumettre une review (agent → agent, après transaction)
  GET  /api/hub/reviews/{hub_id}  → liste des reviews reçues (publiques)
"""
from __future__ import annotations

import logging
import time
import uuid

from fastapi import APIRouter, HTTPException

from core.database import db
from hub.hub_models import HubReviewRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/hub", tags=["hub-review"])

# Score minimum pour pouvoir rédiger une review
_MIN_REVIEWER_SCORE = 15


# ─── Endpoints ───────────────────────────────────────────────────────────────

async def submit_review(req: HubReviewRequest) -> dict:
    """Valide et persiste une peer review.

    Règles :
    - reviewer ≠ reviewed
    - reviewer.score >= 15
    - Au moins 1 transaction escrow commune
    - Un reviewer ne peut reviewer le même reviewed qu'une fois par escrow_id
    """
    # 1. Auto-review interdite
    if req.reviewer_hub_id == req.reviewed_hub_id:
        raise HTTPException(status_code=400, detail="Self-review not allowed")

    # 2. Récupérer le reviewer
    reviewer_row = await db._fetchone(
        "SELECT hub_id, wallet, score FROM hub_agents WHERE hub_id=?",
        (req.reviewer_hub_id,),
    )
    if reviewer_row is None:
        raise HTTPException(status_code=404, detail="Reviewer agent not found")

    reviewer = dict(reviewer_row)

    # 3. Score minimum
    if int(reviewer.get("score") or 0) < _MIN_REVIEWER_SCORE:
        raise HTTPException(
            status_code=403,
            detail=f"Reviewer score too low (minimum {_MIN_REVIEWER_SCORE})",
        )

    # 4. Récupérer le reviewed
    reviewed_row = await db._fetchone(
        "SELECT hub_id, wallet FROM hub_agents WHERE hub_id=?",
        (req.reviewed_hub_id,),
    )
    if reviewed_row is None:
        raise HTTPException(status_code=404, detail="Reviewed agent not found")

    reviewed = dict(reviewed_row)

    # 5. Vérifier la transaction commune via escrow_id
    reviewer_wallet = reviewer["wallet"]
    reviewed_wallet = reviewed["wallet"]

    escrow_rows = await db.raw_execute_fetchall(
        "SELECT escrow_id FROM escrow_records "
        "WHERE escrow_id=? AND ("
        "  (buyer=? AND seller=?) OR (buyer=? AND seller=?)"
        ")",
        (
            req.escrow_id,
            reviewer_wallet, reviewed_wallet,
            reviewed_wallet, reviewer_wallet,
        ),
    )
    if not escrow_rows:
        raise HTTPException(
            status_code=403,
            detail="No common transaction found for this escrow_id",
        )

    # 6. Vérifier doublon (reviewer_hub_id, escrow_id)
    existing_rows = await db.raw_execute_fetchall(
        "SELECT review_id FROM hub_reviews "
        "WHERE reviewer_hub_id=? AND escrow_id=?",
        (req.reviewer_hub_id, req.escrow_id),
    )
    if existing_rows:
        raise HTTPException(
            status_code=409,
            detail="Review already submitted for this escrow",
        )

    # 7. Persister la review
    review_id = uuid.uuid4().hex
    now = int(time.time())

    await db.raw_execute(
        "INSERT INTO hub_reviews "
        "(review_id, reviewer_hub_id, reviewed_hub_id, escrow_id, rating, comment, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            review_id,
            req.reviewer_hub_id,
            req.reviewed_hub_id,
            req.escrow_id,
            req.rating,
            req.comment,
            now,
        ),
    )

    return {
        "review_id": review_id,
        "reviewer_hub_id": req.reviewer_hub_id,
        "reviewed_hub_id": req.reviewed_hub_id,
        "rating": req.rating,
        "created_at": now,
    }


async def get_hub_reviews(hub_id: str) -> dict:
    """Retourne la liste publique des reviews reçues par un agent."""
    rows = await db.raw_execute_fetchall(
        "SELECT review_id, reviewer_hub_id, reviewed_hub_id, escrow_id, "
        "rating, comment, created_at "
        "FROM hub_reviews WHERE reviewed_hub_id=? ORDER BY created_at DESC",
        (hub_id,),
    )

    reviews = [
        {
            "review_id": r["review_id"],
            "reviewer_hub_id": r["reviewer_hub_id"],
            "rating": r["rating"],
            "comment": r.get("comment"),
            "created_at": r["created_at"],
        }
        for r in rows
    ]

    return {"hub_id": hub_id, "reviews": reviews, "count": len(reviews)}


# ─── Route handlers (wrappers FastAPI) ───────────────────────────────────────

@router.post("/review")
async def submit_review_endpoint(req: HubReviewRequest) -> dict:
    """Soumettre une peer review (agent → agent, après transaction)."""
    return await submit_review(req)


@router.get("/reviews/{hub_id}")
async def get_reviews_endpoint(hub_id: str) -> dict:
    """Liste des reviews reçues par un agent (données publiques)."""
    return await get_hub_reviews(hub_id)
