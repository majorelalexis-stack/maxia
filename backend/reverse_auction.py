"""MAXIA Art.48 — Reverse Auction (Encheres inversees)

Systeme d'encheres inversees ou les acheteurs publient des demandes
et les agents vendeurs enchérissent de maniere competitive.

Pattern inspire de SingularityNET RFAI :
  request → bid → evaluate → settle

Scoring multi-attribut pour eviter la course vers le bas :
  40% reputation + 25% SLA + 20% prix + 15% rapidite
"""
import logging
import uuid, time, json
from datetime import datetime, timezone, timedelta
from error_utils import safe_error
from typing import Optional
from fastapi import APIRouter, HTTPException, Header, Query
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/auction", tags=["reverse-auction"])

# ── Prix planchers par type de service (evite la course vers le bas) ──

SERVICE_FLOORS = {
    "llm_inference": 0.001,
    "image_gen": 0.01,
    "fine_tuning": 0.50,
    "data_analysis": 0.05,
    "sentiment": 0.02,
    "web_scraping": 0.01,
    "gpu_rental": 0.10,
    "translation": 0.005,
    "code_review": 0.05,
    "custom": 0.01,
}

VALID_SLA_TIERS = {"basic", "standard", "premium"}
VALID_STATUSES_REQUEST = {"open", "bidding", "awarded", "expired", "cancelled"}
VALID_STATUSES_BID = {"pending", "accepted", "rejected", "withdrawn"}

SLA_SCORES = {"basic": 0.3, "standard": 0.6, "premium": 1.0}

# ── Schema auto-create ──

_schema_ready = False

_REVERSE_AUCTION_SCHEMA = """
CREATE TABLE IF NOT EXISTS auction_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    buyer_id TEXT NOT NULL,
    service_type TEXT NOT NULL,
    description TEXT NOT NULL,
    budget_max_usdc REAL NOT NULL,
    sla_tier TEXT NOT NULL DEFAULT 'standard',
    deadline TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL,
    awarded_at TEXT,
    winning_bid_id INTEGER
);

CREATE INDEX IF NOT EXISTS idx_auction_req_status ON auction_requests(status);
CREATE INDEX IF NOT EXISTS idx_auction_req_buyer ON auction_requests(buyer_id);
CREATE INDEX IF NOT EXISTS idx_auction_req_type ON auction_requests(service_type, status);

CREATE TABLE IF NOT EXISTS auction_bids (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id INTEGER NOT NULL,
    seller_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    price_usdc REAL NOT NULL,
    estimated_time_s INTEGER NOT NULL,
    sla_commitment TEXT NOT NULL DEFAULT 'standard',
    message TEXT,
    score REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    FOREIGN KEY (request_id) REFERENCES auction_requests(id)
);

CREATE INDEX IF NOT EXISTS idx_auction_bid_request ON auction_bids(request_id, score DESC);
CREATE INDEX IF NOT EXISTS idx_auction_bid_seller ON auction_bids(seller_id);
"""


async def _ensure_schema():
    """Cree les tables si elles n'existent pas encore."""
    global _schema_ready
    if _schema_ready:
        return
    try:
        from database import db
        await db.raw_executescript(_REVERSE_AUCTION_SCHEMA)
        _schema_ready = True
        print("[ReverseAuction] Schema pret")
    except Exception as e:
        print(f"[ReverseAuction] Erreur schema: {e}")


# ── Scoring multi-attribut ──

async def _get_seller_reputation(seller_id: str) -> float:
    """Recupere le score composite normalise (0-1) depuis agent_scores.
    Fallback a 0.5 si le leaderboard n'est pas disponible."""
    try:
        from database import db
        rows = await db.raw_execute_fetchall(
            "SELECT composite_score FROM agent_scores WHERE agent_id = ?",
            (seller_id,),
        )
        if rows:
            # composite_score est deja entre 0 et 1
            return max(0.0, min(1.0, float(rows[0]["composite_score"])))
    except Exception:
        pass
    return 0.5


def score_bid(
    price_usdc: float,
    estimated_time_s: int,
    sla_commitment: str,
    seller_reputation: float,
    request_budget: float,
) -> float:
    """
    Calcule un score multi-attribut pour un bid.
    Poids : 40% reputation, 25% SLA, 20% prix, 15% vitesse.
    Resultat entre 0 et 1.
    """
    # Prix : plus c'est bas par rapport au budget, mieux c'est
    price_score = max(0.0, 1.0 - (price_usdc / request_budget)) if request_budget > 0 else 0.0

    # Reputation : directement le score composite (0-1)
    reputation = max(0.0, min(1.0, seller_reputation))

    # SLA : basic=0.3, standard=0.6, premium=1.0
    sla_score = SLA_SCORES.get(sla_commitment, 0.3)

    # Vitesse : plus c'est rapide, mieux c'est (cap a 1h = 3600s)
    speed_score = max(0.0, 1.0 - (estimated_time_s / 3600)) if estimated_time_s < 3600 else 0.0

    composite = (
        0.40 * reputation
        + 0.25 * sla_score
        + 0.20 * price_score
        + 0.15 * speed_score
    )
    return round(composite, 4)


# ── Pydantic models ──

class AuctionRequestCreate(BaseModel):
    service_type: str = Field(min_length=1, max_length=50)
    description: str = Field(min_length=5, max_length=2000)
    budget_max_usdc: float = Field(gt=0, le=100000)
    sla_tier: str = "standard"
    deadline_minutes: int = Field(default=60, ge=5, le=10080)  # 5 min a 7 jours


class AuctionBidCreate(BaseModel):
    agent_id: str = Field(min_length=1, max_length=100)
    price_usdc: float = Field(gt=0, le=100000)
    estimated_time_s: int = Field(ge=1, le=604800)  # 1s a 7 jours
    sla_commitment: str = "standard"
    message: Optional[str] = Field(default=None, max_length=1000)


# ── Helpers ──

def _utc_now() -> str:
    """ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(iso_str: str) -> datetime:
    """Parse un timestamp ISO 8601."""
    return datetime.fromisoformat(iso_str.replace("Z", "+00:00"))


# ── Endpoints ──

@router.post("/request")
async def create_auction_request(
    req: AuctionRequestCreate,
    x_api_key: str = Header(alias="X-API-Key"),
):
    """Buyer publie une demande de service. Les agents vendeurs peuvent ensuite encherir."""
    await _ensure_schema()
    from database import db
    from security import check_content_safety

    if not x_api_key:
        raise HTTPException(401, "X-API-Key requis")

    # Validation SLA tier
    if req.sla_tier not in VALID_SLA_TIERS:
        raise HTTPException(400, f"sla_tier invalide. Valeurs: {', '.join(sorted(VALID_SLA_TIERS))}")

    # Validation type de service
    floor = SERVICE_FLOORS.get(req.service_type)
    if floor is None:
        raise HTTPException(400, f"service_type invalide. Valeurs: {', '.join(sorted(SERVICE_FLOORS.keys()))}")

    # Budget doit etre >= prix plancher
    if req.budget_max_usdc < floor:
        raise HTTPException(
            400,
            f"Budget trop bas. Minimum pour {req.service_type}: ${floor} USDC",
        )

    # Filtrage contenu Art.1
    check_content_safety(req.description, "description")

    # Calcul du deadline
    deadline = datetime.now(timezone.utc) + timedelta(minutes=req.deadline_minutes)
    deadline_str = deadline.strftime("%Y-%m-%dT%H:%M:%SZ")
    now_str = _utc_now()

    await db.raw_execute(
        "INSERT INTO auction_requests "
        "(buyer_id, service_type, description, budget_max_usdc, sla_tier, deadline, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 'open', ?)",
        (x_api_key, req.service_type, req.description, req.budget_max_usdc,
         req.sla_tier, deadline_str, now_str),
    )

    # Recuperer l'ID auto-incremente
    rows = await db.raw_execute_fetchall(
        "SELECT id FROM auction_requests WHERE buyer_id = ? AND created_at = ? "
        "ORDER BY id DESC LIMIT 1",
        (x_api_key, now_str),
    )
    request_id = rows[0]["id"] if rows else 0

    print(f"[ReverseAuction] Nouvelle demande #{request_id}: {req.service_type} "
          f"(budget: ${req.budget_max_usdc}, deadline: {req.deadline_minutes}min)")

    return {
        "ok": True,
        "request_id": request_id,
        "service_type": req.service_type,
        "budget_max_usdc": req.budget_max_usdc,
        "sla_tier": req.sla_tier,
        "deadline": deadline_str,
        "status": "open",
    }


@router.get("/requests")
async def list_open_requests(
    service_type: Optional[str] = Query(None, description="Filtrer par type de service"),
    limit: int = Query(20, ge=1, le=100),
):
    """Liste les demandes ouvertes (open ou bidding). Pas d'auth requise."""
    await _ensure_schema()
    from database import db

    if service_type:
        if service_type not in SERVICE_FLOORS:
            raise HTTPException(400, f"service_type invalide. Valeurs: {', '.join(sorted(SERVICE_FLOORS.keys()))}")
        rows = await db.raw_execute_fetchall(
            "SELECT id, buyer_id, service_type, description, budget_max_usdc, "
            "sla_tier, deadline, status, created_at, awarded_at, winning_bid_id "
            "FROM auction_requests "
            "WHERE status IN ('open', 'bidding') AND service_type = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (service_type, limit),
        )
    else:
        rows = await db.raw_execute_fetchall(
            "SELECT id, buyer_id, service_type, description, budget_max_usdc, "
            "sla_tier, deadline, status, created_at, awarded_at, winning_bid_id "
            "FROM auction_requests "
            "WHERE status IN ('open', 'bidding') "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )

    requests = []
    for r in rows:
        rd = dict(r)
        # Compter le nombre de bids
        bid_rows = await db.raw_execute_fetchall(
            "SELECT COUNT(*) as cnt FROM auction_bids WHERE request_id = ?",
            (rd["id"],),
        )
        rd["bid_count"] = bid_rows[0]["cnt"] if bid_rows else 0
        # Masquer le buyer_id (securite)
        rd["buyer_id"] = rd["buyer_id"][:8] + "..."
        requests.append(rd)

    return {"requests": requests, "count": len(requests)}


@router.get("/request/{request_id}")
async def get_request_details(request_id: int):
    """Details d'une demande + tous les bids (scores et tries)."""
    await _ensure_schema()
    from database import db

    rows = await db.raw_execute_fetchall(
        "SELECT id, buyer_id, service_type, description, budget_max_usdc, "
        "sla_tier, deadline, status, created_at, awarded_at, winning_bid_id "
        "FROM auction_requests WHERE id = ?", (request_id,),
    )
    if not rows:
        raise HTTPException(404, "Demande non trouvee")

    request_data = dict(rows[0])

    # Recuperer tous les bids, tries par score decroissant
    bid_rows = await db.raw_execute_fetchall(
        "SELECT id, request_id, seller_id, agent_id, price_usdc, "
        "estimated_time_s, sla_commitment, message, score, status, created_at "
        "FROM auction_bids WHERE request_id = ? ORDER BY score DESC",
        (request_id,),
    )
    bids = []
    for b in bid_rows:
        bd = dict(b)
        # Masquer le seller_id partiellement (securite)
        bd["seller_id"] = bd["seller_id"][:8] + "..."
        bids.append(bd)

    request_data["bids"] = bids
    request_data["bid_count"] = len(bids)
    # Masquer le buyer_id
    request_data["buyer_id"] = request_data["buyer_id"][:8] + "..."

    return request_data


@router.post("/bid/{request_id}")
async def submit_bid(
    request_id: int,
    bid: AuctionBidCreate,
    x_api_key: str = Header(alias="X-API-Key"),
):
    """Seller soumet un bid pour une demande. Score auto-calcule."""
    await _ensure_schema()
    from database import db
    from security import check_content_safety

    if not x_api_key:
        raise HTTPException(401, "X-API-Key requis")

    # Verifier que la demande existe et est ouverte
    rows = await db.raw_execute_fetchall(
        "SELECT id, buyer_id, service_type, budget_max_usdc, sla_tier, "
        "deadline, status "
        "FROM auction_requests WHERE id = ?", (request_id,),
    )
    if not rows:
        raise HTTPException(404, "Demande non trouvee")

    request_data = dict(rows[0])

    if request_data["status"] not in ("open", "bidding"):
        raise HTTPException(400, f"Demande non disponible (status: {request_data['status']})")

    # Verifier que le deadline n'est pas passe
    deadline = _parse_iso(request_data["deadline"])
    if datetime.now(timezone.utc) > deadline:
        raise HTTPException(400, "Demande expiree")

    # Le buyer ne peut pas encherir sur sa propre demande
    if x_api_key == request_data["buyer_id"]:
        raise HTTPException(400, "Vous ne pouvez pas encherir sur votre propre demande")

    # Verifier que le seller n'a pas deja encheri
    existing = await db.raw_execute_fetchall(
        "SELECT id FROM auction_bids WHERE request_id = ? AND seller_id = ?",
        (request_id, x_api_key),
    )
    if existing:
        raise HTTPException(400, "Vous avez deja soumis un bid pour cette demande")

    # Validation SLA
    if bid.sla_commitment not in VALID_SLA_TIERS:
        raise HTTPException(400, f"sla_commitment invalide. Valeurs: {', '.join(sorted(VALID_SLA_TIERS))}")

    # Prix plancher
    floor = SERVICE_FLOORS.get(request_data["service_type"], 0.01)
    if bid.price_usdc < floor:
        raise HTTPException(400, f"Prix trop bas. Minimum pour {request_data['service_type']}: ${floor} USDC")

    # Prix <= budget
    if bid.price_usdc > request_data["budget_max_usdc"]:
        raise HTTPException(400, f"Prix depasse le budget (max: ${request_data['budget_max_usdc']} USDC)")

    # Filtrage contenu Art.1
    if bid.message:
        check_content_safety(bid.message, "message")

    # Calcul du score
    reputation = await _get_seller_reputation(x_api_key)
    bid_score = score_bid(
        price_usdc=bid.price_usdc,
        estimated_time_s=bid.estimated_time_s,
        sla_commitment=bid.sla_commitment,
        seller_reputation=reputation,
        request_budget=request_data["budget_max_usdc"],
    )

    now_str = _utc_now()

    await db.raw_execute(
        "INSERT INTO auction_bids "
        "(request_id, seller_id, agent_id, price_usdc, estimated_time_s, "
        "sla_commitment, message, score, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
        (request_id, x_api_key, bid.agent_id, bid.price_usdc,
         bid.estimated_time_s, bid.sla_commitment, bid.message or "",
         bid_score, now_str),
    )

    # Recuperer l'ID du bid
    bid_rows = await db.raw_execute_fetchall(
        "SELECT id FROM auction_bids WHERE request_id = ? AND seller_id = ? "
        "ORDER BY id DESC LIMIT 1",
        (request_id, x_api_key),
    )
    bid_id = bid_rows[0]["id"] if bid_rows else 0

    # Passer la demande en 'bidding' au premier bid
    if request_data["status"] == "open":
        await db.raw_execute(
            "UPDATE auction_requests SET status = 'bidding' WHERE id = ?",
            (request_id,),
        )

    print(f"[ReverseAuction] Bid #{bid_id} sur demande #{request_id}: "
          f"${bid.price_usdc} USDC, score={bid_score}")

    return {
        "ok": True,
        "bid_id": bid_id,
        "request_id": request_id,
        "price_usdc": bid.price_usdc,
        "score": bid_score,
        "reputation_used": round(reputation, 4),
        "status": "pending",
    }


@router.post("/accept/{request_id}/{bid_id}")
async def accept_bid(
    request_id: int,
    bid_id: int,
    x_api_key: str = Header(alias="X-API-Key"),
):
    """Buyer accepte un bid. Cree un escrow et attribue la demande."""
    await _ensure_schema()
    from database import db

    if not x_api_key:
        raise HTTPException(401, "X-API-Key requis")

    # Verifier que la demande existe et appartient au buyer
    req_rows = await db.raw_execute_fetchall(
        "SELECT id, buyer_id, service_type, budget_max_usdc, status "
        "FROM auction_requests WHERE id = ?", (request_id,),
    )
    if not req_rows:
        raise HTTPException(404, "Demande non trouvee")

    request_data = dict(req_rows[0])

    if request_data["buyer_id"] != x_api_key:
        raise HTTPException(403, "Seul le buyer peut accepter un bid")

    if request_data["status"] not in ("open", "bidding"):
        raise HTTPException(400, f"Demande non disponible (status: {request_data['status']})")

    # Verifier que le bid existe et appartient a cette demande
    bid_rows = await db.raw_execute_fetchall(
        "SELECT id, request_id, seller_id, agent_id, price_usdc, "
        "estimated_time_s, sla_commitment, message, score, status, created_at "
        "FROM auction_bids WHERE id = ? AND request_id = ?",
        (bid_id, request_id),
    )
    if not bid_rows:
        raise HTTPException(404, "Bid non trouve pour cette demande")

    bid_data = dict(bid_rows[0])

    if bid_data["status"] != "pending":
        raise HTTPException(400, f"Bid non disponible (status: {bid_data['status']})")

    now_str = _utc_now()

    # Mettre a jour la demande
    await db.raw_execute(
        "UPDATE auction_requests SET status = 'awarded', awarded_at = ?, winning_bid_id = ? "
        "WHERE id = ?",
        (now_str, bid_id, request_id),
    )

    # Accepter le bid gagnant
    await db.raw_execute(
        "UPDATE auction_bids SET status = 'accepted' WHERE id = ?",
        (bid_id,),
    )

    # Rejeter les autres bids
    await db.raw_execute(
        "UPDATE auction_bids SET status = 'rejected' WHERE request_id = ? AND id != ?",
        (request_id, bid_id),
    )

    # Tenter de creer un escrow automatiquement
    escrow_result = None
    try:
        from escrow_client import escrow_client
        # Recuperer les wallets du buyer et du seller
        buyer_agent = await db.raw_execute_fetchall(
            "SELECT wallet FROM agents WHERE api_key = ?", (x_api_key,),
        )
        seller_agent = await db.raw_execute_fetchall(
            "SELECT wallet FROM agents WHERE api_key = ?", (bid_data["seller_id"],),
        )
        if buyer_agent and seller_agent:
            buyer_wallet = buyer_agent[0]["wallet"]
            seller_wallet = seller_agent[0]["wallet"]
            escrow_result = {
                "note": "Escrow en attente — le buyer doit envoyer un paiement USDC au wallet escrow",
                "amount_usdc": bid_data["price_usdc"],
                "buyer_wallet": buyer_wallet,
                "seller_wallet": seller_wallet,
            }
    except Exception as e:
        escrow_result = {"note": f"Escrow non disponible: {e}"}

    # Nombre de bids rejetes
    rejected = await db.raw_execute_fetchall(
        "SELECT COUNT(*) as cnt FROM auction_bids WHERE request_id = ? AND status = 'rejected'",
        (request_id,),
    )
    rejected_count = rejected[0]["cnt"] if rejected else 0

    print(f"[ReverseAuction] Demande #{request_id} attribuee au bid #{bid_id} "
          f"(${bid_data['price_usdc']} USDC, {rejected_count} bids rejetes)")

    return {
        "ok": True,
        "request_id": request_id,
        "winning_bid_id": bid_id,
        "price_usdc": bid_data["price_usdc"],
        "seller_agent_id": bid_data["agent_id"],
        "rejected_bids": rejected_count,
        "escrow": escrow_result,
    }


@router.delete("/request/{request_id}")
async def cancel_request(
    request_id: int,
    x_api_key: str = Header(alias="X-API-Key"),
):
    """Buyer annule sa demande (uniquement si aucun bid n'est accepte)."""
    await _ensure_schema()
    from database import db

    if not x_api_key:
        raise HTTPException(401, "X-API-Key requis")

    rows = await db.raw_execute_fetchall(
        "SELECT id, buyer_id, status "
        "FROM auction_requests WHERE id = ?", (request_id,),
    )
    if not rows:
        raise HTTPException(404, "Demande non trouvee")

    request_data = dict(rows[0])

    if request_data["buyer_id"] != x_api_key:
        raise HTTPException(403, "Seul le buyer peut annuler sa demande")

    if request_data["status"] == "awarded":
        raise HTTPException(400, "Impossible d'annuler — un bid a deja ete accepte")

    if request_data["status"] in ("cancelled", "expired"):
        raise HTTPException(400, f"Demande deja {request_data['status']}")

    # Annuler la demande
    await db.raw_execute(
        "UPDATE auction_requests SET status = 'cancelled' WHERE id = ?",
        (request_id,),
    )

    # Retirer tous les bids pending
    await db.raw_execute(
        "UPDATE auction_bids SET status = 'withdrawn' WHERE request_id = ? AND status = 'pending'",
        (request_id,),
    )

    print(f"[ReverseAuction] Demande #{request_id} annulee par le buyer")

    return {"ok": True, "request_id": request_id, "status": "cancelled"}


@router.get("/my-requests")
async def my_requests(
    x_api_key: str = Header(alias="X-API-Key"),
    limit: int = Query(20, ge=1, le=100),
):
    """Liste les demandes du buyer connecte."""
    await _ensure_schema()
    from database import db

    if not x_api_key:
        raise HTTPException(401, "X-API-Key requis")

    rows = await db.raw_execute_fetchall(
        "SELECT id, buyer_id, service_type, description, budget_max_usdc, "
        "sla_tier, deadline, status, created_at, awarded_at, winning_bid_id "
        "FROM auction_requests WHERE buyer_id = ? ORDER BY created_at DESC LIMIT ?",
        (x_api_key, limit),
    )

    requests = []
    for r in rows:
        rd = dict(r)
        bid_rows = await db.raw_execute_fetchall(
            "SELECT COUNT(*) as cnt FROM auction_bids WHERE request_id = ?",
            (rd["id"],),
        )
        rd["bid_count"] = bid_rows[0]["cnt"] if bid_rows else 0
        requests.append(rd)

    return {"requests": requests, "count": len(requests)}


@router.get("/my-bids")
async def my_bids(
    x_api_key: str = Header(alias="X-API-Key"),
    limit: int = Query(20, ge=1, le=100),
):
    """Liste les bids du seller connecte."""
    await _ensure_schema()
    from database import db

    if not x_api_key:
        raise HTTPException(401, "X-API-Key requis")

    rows = await db.raw_execute_fetchall(
        "SELECT b.*, r.service_type, r.description as request_description, "
        "r.budget_max_usdc, r.status as request_status "
        "FROM auction_bids b "
        "JOIN auction_requests r ON b.request_id = r.id "
        "WHERE b.seller_id = ? ORDER BY b.created_at DESC LIMIT ?",
        (x_api_key, limit),
    )

    return {"bids": [dict(r) for r in rows], "count": len(rows)}


# ── Background: expiration des demandes ──

async def expire_old_requests():
    """
    Expire les demandes dont le deadline est depasse.
    A appeler toutes les 10 minutes via le scheduler.
    """
    await _ensure_schema()
    from database import db

    now_str = _utc_now()

    try:
        # Trouver les demandes a expirer
        expired_rows = await db.raw_execute_fetchall(
            "SELECT id, buyer_id, service_type FROM auction_requests "
            "WHERE status IN ('open', 'bidding') AND deadline < ?",
            (now_str,),
        )

        if not expired_rows:
            return {"expired": 0}

        expired_ids = [r["id"] for r in expired_rows]

        for req in expired_rows:
            req_id = req["id"]

            # Compter les bids pour notification
            bid_rows = await db.raw_execute_fetchall(
                "SELECT COUNT(*) as cnt FROM auction_bids WHERE request_id = ? AND status = 'pending'",
                (req_id,),
            )
            bid_count = bid_rows[0]["cnt"] if bid_rows else 0

            # Expirer la demande
            await db.raw_execute(
                "UPDATE auction_requests SET status = 'expired' WHERE id = ?",
                (req_id,),
            )

            # Retirer les bids pending
            await db.raw_execute(
                "UPDATE auction_bids SET status = 'withdrawn' WHERE request_id = ? AND status = 'pending'",
                (req_id,),
            )

            if bid_count > 0:
                print(f"[ReverseAuction] Demande #{req_id} expiree avec {bid_count} bids non evalues")

        print(f"[ReverseAuction] {len(expired_ids)} demande(s) expiree(s)")
        return {"expired": len(expired_ids), "ids": expired_ids}

    except Exception as e:
        print(f"[ReverseAuction] Erreur expiration: {e}")
        return {"expired": 0, "error": "An error occurred"}


# ── Stats ──

@router.get("/stats")
async def auction_stats():
    """Statistiques globales des encheres inversees."""
    await _ensure_schema()
    from database import db

    try:
        req_rows = await db.raw_execute_fetchall(
            "SELECT status, COUNT(*) as cnt FROM auction_requests GROUP BY status"
        )
        bid_rows = await db.raw_execute_fetchall(
            "SELECT status, COUNT(*) as cnt FROM auction_bids GROUP BY status"
        )
        vol_rows = await db.raw_execute_fetchall(
            "SELECT COALESCE(SUM(b.price_usdc), 0) as total_volume "
            "FROM auction_bids b WHERE b.status = 'accepted'"
        )
        avg_rows = await db.raw_execute_fetchall(
            "SELECT COALESCE(AVG(b.score), 0) as avg_score, "
            "COALESCE(AVG(b.price_usdc), 0) as avg_price "
            "FROM auction_bids b WHERE b.status = 'pending'"
        )

        return {
            "requests_by_status": {r["status"]: r["cnt"] for r in req_rows},
            "bids_by_status": {r["status"]: r["cnt"] for r in bid_rows},
            "total_awarded_volume_usdc": float(vol_rows[0]["total_volume"]) if vol_rows else 0.0,
            "active_avg_score": round(float(avg_rows[0]["avg_score"]), 4) if avg_rows else 0.0,
            "active_avg_price_usdc": round(float(avg_rows[0]["avg_price"]), 4) if avg_rows else 0.0,
            "service_floors": SERVICE_FLOORS,
            "scoring_weights": {
                "reputation": 0.40,
                "sla": 0.25,
                "price": 0.20,
                "speed": 0.15,
            },
        }
    except Exception as e:
        return safe_error(e, "operation")
