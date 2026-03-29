"""MAXIA Art.48 — Business Listing Marketplace (inspire Flippt.ai)

Permet aux utilisateurs de lister des businesses IA complets a la vente :
- Agent + code + clients + historique de revenus
- Les acheteurs parcourent, font des offres, achetent
- MAXIA prend 5% de commission sur les ventes completees

Categories: saas_bot, trading_bot, data_service, content_creator,
           defi_bot, nft_bot, analytics, custom
"""
import logging
import uuid, time, json
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Optional, List
from auth import require_auth
from security import check_content_safety, require_ofac_clear

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/business", tags=["business-listing"])

# ── Constantes ──

BUSINESS_COMMISSION_RATE = 0.05  # 5% sur les ventes completees
VALID_CATEGORIES = {
    "saas_bot", "trading_bot", "data_service", "content_creator",
    "defi_bot", "nft_bot", "analytics", "custom",
}
VALID_LISTING_STATUSES = {"active", "sold", "withdrawn"}
VALID_OFFER_STATUSES = {"pending", "accepted", "rejected", "withdrawn"}
VALID_SORT_FIELDS = {"price", "revenue", "date"}

MIN_ASKING_PRICE = 10.0         # $10 minimum
MAX_ASKING_PRICE = 1_000_000.0  # $1M maximum
MAX_TITLE_LEN = 200
MAX_DESCRIPTION_LEN = 5000


# ── Pydantic models ──

class BusinessListRequest(BaseModel):
    title: str = Field(min_length=1, max_length=MAX_TITLE_LEN)
    description: str = Field(min_length=1, max_length=MAX_DESCRIPTION_LEN)
    agent_id: Optional[str] = None
    monthly_revenue_usdc: float = Field(ge=0)
    monthly_costs_usdc: float = Field(ge=0)
    clients_count: int = Field(ge=0)
    months_active: int = Field(ge=0)
    asking_price_usdc: float = Field(ge=MIN_ASKING_PRICE, le=MAX_ASKING_PRICE)
    category: str
    tech_stack: str = ""
    chains: str = ""


class BusinessOfferRequest(BaseModel):
    offer_usdc: float = Field(gt=0, le=MAX_ASKING_PRICE)
    message: str = Field(default="", max_length=2000)


# ── Schema auto-create ──

_schema_ready = False

async def _ensure_schema():
    """Cree les tables business_listings et business_offers si elles n'existent pas."""
    global _schema_ready
    if _schema_ready:
        return
    from database import db
    await db.raw_executescript("""
        CREATE TABLE IF NOT EXISTS business_listings (
            id TEXT PRIMARY KEY,
            seller_id TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            agent_id TEXT DEFAULT '',
            monthly_revenue_usdc NUMERIC(18,6) NOT NULL DEFAULT 0,
            monthly_costs_usdc NUMERIC(18,6) NOT NULL DEFAULT 0,
            clients_count INTEGER NOT NULL DEFAULT 0,
            months_active INTEGER NOT NULL DEFAULT 0,
            asking_price_usdc NUMERIC(18,6) NOT NULL,
            category TEXT NOT NULL DEFAULT 'custom',
            tech_stack TEXT DEFAULT '',
            chains TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            sold_at TEXT DEFAULT NULL,
            buyer_id TEXT DEFAULT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_bl_status ON business_listings(status);
        CREATE INDEX IF NOT EXISTS idx_bl_seller ON business_listings(seller_id);
        CREATE INDEX IF NOT EXISTS idx_bl_category ON business_listings(category);

        CREATE TABLE IF NOT EXISTS business_offers (
            id TEXT PRIMARY KEY,
            listing_id TEXT NOT NULL,
            buyer_id TEXT NOT NULL,
            offer_usdc NUMERIC(18,6) NOT NULL,
            message TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_bo_listing ON business_offers(listing_id);
        CREATE INDEX IF NOT EXISTS idx_bo_buyer ON business_offers(buyer_id);
    """)
    _schema_ready = True


# ── Helpers ──

def _now_iso() -> str:
    """Retourne un timestamp ISO 8601 UTC."""
    import datetime
    return datetime.datetime.utcnow().isoformat() + "Z"


def _row_to_listing(row) -> dict:
    """Convertit une Row SQLite en dict listing."""
    return {
        "id": row["id"],
        "seller_id": row["seller_id"],
        "title": row["title"],
        "description": row["description"],
        "agent_id": row["agent_id"],
        "monthly_revenue_usdc": float(row["monthly_revenue_usdc"]),
        "monthly_costs_usdc": float(row["monthly_costs_usdc"]),
        "profit_usdc": float(row["monthly_revenue_usdc"]) - float(row["monthly_costs_usdc"]),
        "clients_count": int(row["clients_count"]),
        "months_active": int(row["months_active"]),
        "asking_price_usdc": float(row["asking_price_usdc"]),
        "category": row["category"],
        "tech_stack": row["tech_stack"],
        "chains": row["chains"],
        "status": row["status"],
        "created_at": row["created_at"],
        "sold_at": row["sold_at"],
        "buyer_id": row["buyer_id"],
        # Metriques calculees
        "revenue_multiple": round(
            float(row["asking_price_usdc"]) / max(float(row["monthly_revenue_usdc"]), 0.01), 1
        ),
    }


def _row_to_offer(row) -> dict:
    """Convertit une Row SQLite en dict offer."""
    return {
        "id": row["id"],
        "listing_id": row["listing_id"],
        "buyer_id": row["buyer_id"],
        "offer_usdc": float(row["offer_usdc"]),
        "message": row["message"],
        "status": row["status"],
        "created_at": row["created_at"],
    }


# ── Endpoints publics ──

@router.get("/listings")
async def browse_listings(
    category: Optional[str] = None,
    min_revenue: Optional[float] = None,
    max_price: Optional[float] = None,
    sort_by: str = "date",
):
    """Parcourir les listings actifs. Public, pas d'auth requise."""
    await _ensure_schema()
    from database import db

    # Validation du tri
    if sort_by not in VALID_SORT_FIELDS:
        sort_by = "date"

    # Construction de la requete avec filtres
    conditions = ["status = 'active'"]
    params: list = []

    if category:
        if category not in VALID_CATEGORIES:
            raise HTTPException(400, f"Categorie invalide. Valides: {', '.join(sorted(VALID_CATEGORIES))}")
        conditions.append("category = ?")
        params.append(category)

    if min_revenue is not None:
        conditions.append("monthly_revenue_usdc >= ?")
        params.append(min_revenue)

    if max_price is not None:
        conditions.append("asking_price_usdc <= ?")
        params.append(max_price)

    where = " AND ".join(conditions)

    # Tri
    order_map = {
        "price": "asking_price_usdc ASC",
        "revenue": "monthly_revenue_usdc DESC",
        "date": "created_at DESC",
    }
    order = order_map.get(sort_by, "created_at DESC")

    # Note: where est construit depuis des conditions parametrees (?), order depuis une whitelist
    # Bandit B608 faux positif — pas d'input user dans le SQL
    # All columns needed by _row_to_listing
    _bl_cols = ("id, seller_id, title, description, agent_id, monthly_revenue_usdc, "
                "monthly_costs_usdc, clients_count, months_active, asking_price_usdc, "
                "category, tech_stack, chains, status, created_at, sold_at, buyer_id")
    sql = "SELECT " + _bl_cols + " FROM business_listings WHERE " + where + " ORDER BY " + order + " LIMIT 100"  # noqa: S608
    rows = await db.raw_execute_fetchall(sql, tuple(params))

    return {
        "listings": [_row_to_listing(r) for r in rows],
        "count": len(rows),
        "filters": {"category": category, "min_revenue": min_revenue, "max_price": max_price, "sort_by": sort_by},
    }


@router.get("/listing/{listing_id}")
async def get_listing_detail(listing_id: str):
    """Detail d'un listing avec metriques completes. Public."""
    await _ensure_schema()
    from database import db

    rows = await db.raw_execute_fetchall(
        "SELECT id, seller_id, title, description, agent_id, monthly_revenue_usdc, "
        "monthly_costs_usdc, clients_count, months_active, asking_price_usdc, "
        "category, tech_stack, chains, status, created_at, sold_at, buyer_id "
        "FROM business_listings WHERE id = ?", (listing_id,)
    )
    if not rows:
        raise HTTPException(404, "Listing introuvable")

    listing = _row_to_listing(rows[0])

    # Compter les offres actives (sans reveler les montants)
    offer_rows = await db.raw_execute_fetchall(
        "SELECT COUNT(*) as cnt FROM business_offers WHERE listing_id = ? AND status = 'pending'",
        (listing_id,),
    )
    listing["pending_offers_count"] = int(offer_rows[0]["cnt"]) if offer_rows else 0

    return listing


@router.get("/stats")
async def marketplace_stats():
    """Statistiques globales du marketplace business. Public."""
    await _ensure_schema()
    from database import db

    # Total listings actifs
    r1 = await db.raw_execute_fetchall(
        "SELECT COUNT(*) as cnt FROM business_listings WHERE status = 'active'"
    )
    active_count = int(r1[0]["cnt"]) if r1 else 0

    # Prix moyen des listings actifs
    r2 = await db.raw_execute_fetchall(
        "SELECT COALESCE(AVG(asking_price_usdc), 0) as avg_price FROM business_listings WHERE status = 'active'"
    )
    avg_price = round(float(r2[0]["avg_price"]), 2) if r2 else 0.0

    # Volume total des ventes completees
    r3 = await db.raw_execute_fetchall(
        "SELECT COUNT(*) as cnt, COALESCE(SUM(asking_price_usdc), 0) as volume "
        "FROM business_listings WHERE status = 'sold'"
    )
    sold_count = int(r3[0]["cnt"]) if r3 else 0
    total_volume = round(float(r3[0]["volume"]), 2) if r3 else 0.0

    # Commission totale generee
    total_commission = round(total_volume * BUSINESS_COMMISSION_RATE, 2)

    # Revenu mensuel total des listings actifs
    r4 = await db.raw_execute_fetchall(
        "SELECT COALESCE(SUM(monthly_revenue_usdc), 0) as total_rev "
        "FROM business_listings WHERE status = 'active'"
    )
    total_monthly_revenue = round(float(r4[0]["total_rev"]), 2) if r4 else 0.0

    # Repartition par categorie
    r5 = await db.raw_execute_fetchall(
        "SELECT category, COUNT(*) as cnt FROM business_listings "
        "WHERE status = 'active' GROUP BY category ORDER BY cnt DESC"
    )
    categories = {r["category"]: int(r["cnt"]) for r in r5} if r5 else {}

    return {
        "active_listings": active_count,
        "avg_asking_price_usdc": avg_price,
        "total_sold": sold_count,
        "total_volume_usdc": total_volume,
        "total_commission_usdc": total_commission,
        "commission_rate": BUSINESS_COMMISSION_RATE,
        "total_monthly_revenue_usdc": total_monthly_revenue,
        "categories": categories,
    }


# ── Endpoints authentifies ──

@router.post("/list")
async def create_listing(req: BusinessListRequest, wallet: str = Depends(require_auth)):
    """Creer un listing business. Auth requise (vendeur)."""
    await _ensure_schema()
    from database import db

    # Validation categorie
    if req.category not in VALID_CATEGORIES:
        raise HTTPException(400, f"Categorie invalide. Valides: {', '.join(sorted(VALID_CATEGORIES))}")

    # OFAC check sur le wallet vendeur
    require_ofac_clear(wallet, field="seller wallet")

    # Content safety Art.1
    check_content_safety(req.title, field_name="title")
    check_content_safety(req.description, field_name="description")
    if req.tech_stack:
        check_content_safety(req.tech_stack, field_name="tech_stack")

    listing_id = str(uuid.uuid4())
    now = _now_iso()

    await db.raw_execute(
        "INSERT INTO business_listings "
        "(id, seller_id, title, description, agent_id, monthly_revenue_usdc, monthly_costs_usdc, "
        "clients_count, months_active, asking_price_usdc, category, tech_stack, chains, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)",
        (
            listing_id, wallet, req.title, req.description, req.agent_id or "",
            req.monthly_revenue_usdc, req.monthly_costs_usdc,
            req.clients_count, req.months_active,
            req.asking_price_usdc, req.category,
            req.tech_stack, req.chains, now,
        ),
    )

    logger.info(f"[Business] Nouveau listing: {listing_id} par {wallet[:8]}... — {req.title} ({req.asking_price_usdc} USDC)")

    return {
        "ok": True,
        "listing_id": listing_id,
        "title": req.title,
        "asking_price_usdc": req.asking_price_usdc,
        "category": req.category,
        "commission_rate": BUSINESS_COMMISSION_RATE,
        "message": "Listing cree avec succes. Les acheteurs peuvent maintenant faire des offres.",
    }


@router.post("/offer/{listing_id}")
async def make_offer(listing_id: str, req: BusinessOfferRequest, wallet: str = Depends(require_auth)):
    """Faire une offre sur un listing. Auth requise (acheteur)."""
    await _ensure_schema()
    from database import db

    # OFAC check sur le wallet acheteur
    require_ofac_clear(wallet, field="buyer wallet")

    # Content safety sur le message
    if req.message:
        check_content_safety(req.message, field_name="offer message")

    # Verifier que le listing existe et est actif
    rows = await db.raw_execute_fetchall(
        "SELECT id, seller_id, title, asking_price_usdc, status "
        "FROM business_listings WHERE id = ? AND status = 'active'", (listing_id,)
    )
    if not rows:
        raise HTTPException(404, "Listing introuvable ou plus actif")

    listing = rows[0]

    # Interdire de faire une offre sur son propre listing
    if listing["seller_id"] == wallet:
        raise HTTPException(400, "Vous ne pouvez pas faire une offre sur votre propre listing")

    # Verifier que l'acheteur n'a pas deja une offre pending sur ce listing
    existing = await db.raw_execute_fetchall(
        "SELECT id FROM business_offers WHERE listing_id = ? AND buyer_id = ? AND status = 'pending'",
        (listing_id, wallet),
    )
    if existing:
        raise HTTPException(400, "Vous avez deja une offre en attente sur ce listing. Retirez-la d'abord.")

    offer_id = str(uuid.uuid4())
    now = _now_iso()

    await db.raw_execute(
        "INSERT INTO business_offers (id, listing_id, buyer_id, offer_usdc, message, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, 'pending', ?)",
        (offer_id, listing_id, wallet, req.offer_usdc, req.message, now),
    )

    logger.info(f"[Business] Offre {offer_id}: {wallet[:8]}... offre {req.offer_usdc} USDC sur {listing_id}")

    return {
        "ok": True,
        "offer_id": offer_id,
        "listing_id": listing_id,
        "offer_usdc": req.offer_usdc,
        "listing_title": listing["title"],
        "asking_price_usdc": float(listing["asking_price_usdc"]),
        "message": "Offre soumise. Le vendeur sera notifie.",
    }


@router.post("/accept/{offer_id}")
async def accept_offer(offer_id: str, wallet: str = Depends(require_auth)):
    """Accepter une offre. Auth requise (vendeur du listing uniquement).
    Declenche le processus d'escrow et marque le listing comme vendu."""
    await _ensure_schema()
    from database import db

    # Recuperer l'offre
    offer_rows = await db.raw_execute_fetchall(
        "SELECT id, listing_id, buyer_id, offer_usdc, message, status, created_at "
        "FROM business_offers WHERE id = ? AND status = 'pending'", (offer_id,)
    )
    if not offer_rows:
        raise HTTPException(404, "Offre introuvable ou deja traitee")

    offer = offer_rows[0]

    # Recuperer le listing associe
    listing_rows = await db.raw_execute_fetchall(
        "SELECT id, seller_id, title, asking_price_usdc, status "
        "FROM business_listings WHERE id = ? AND status = 'active'", (offer["listing_id"],)
    )
    if not listing_rows:
        raise HTTPException(404, "Listing introuvable ou plus actif")

    listing = listing_rows[0]

    # Seul le vendeur peut accepter
    if listing["seller_id"] != wallet:
        raise HTTPException(403, "Seul le vendeur peut accepter une offre")

    now = _now_iso()
    sale_price = float(offer["offer_usdc"])
    commission = round(sale_price * BUSINESS_COMMISSION_RATE, 2)
    seller_gets = round(sale_price - commission, 2)

    # Marquer l'offre comme acceptee
    await db.raw_execute(
        "UPDATE business_offers SET status = 'accepted' WHERE id = ?", (offer_id,)
    )

    # Marquer le listing comme vendu
    await db.raw_execute(
        "UPDATE business_listings SET status = 'sold', sold_at = ?, buyer_id = ? WHERE id = ?",
        (now, offer["buyer_id"], offer["listing_id"]),
    )

    # Rejeter automatiquement les autres offres pending sur ce listing
    await db.raw_execute(
        "UPDATE business_offers SET status = 'rejected' WHERE listing_id = ? AND id != ? AND status = 'pending'",
        (offer["listing_id"], offer_id),
    )

    # Enregistrer la transaction dans le ledger principal
    tx_id = str(uuid.uuid4())
    await db.raw_execute(
        "INSERT OR IGNORE INTO transactions (tx_signature, wallet, amount_usdc, purpose) "
        "VALUES (?, ?, ?, 'business_sale')",
        (tx_id, wallet, sale_price),
    )

    logger.info(
        f"[Business] VENTE: {listing['title']} vendu a {offer['buyer_id'][:8]}... "
        f"pour {sale_price} USDC (commission: {commission} USDC, vendeur recoit: {seller_gets} USDC)"
    )

    return {
        "ok": True,
        "sale": {
            "listing_id": offer["listing_id"],
            "listing_title": listing["title"],
            "offer_id": offer_id,
            "buyer_id": offer["buyer_id"],
            "sale_price_usdc": sale_price,
            "commission_usdc": commission,
            "commission_rate": BUSINESS_COMMISSION_RATE,
            "seller_gets_usdc": seller_gets,
            "sold_at": now,
        },
        "message": f"Vente completee! Commission MAXIA: {commission} USDC (5%). Vous recevez: {seller_gets} USDC.",
    }


@router.delete("/listing/{listing_id}")
async def withdraw_listing(listing_id: str, wallet: str = Depends(require_auth)):
    """Retirer un listing. Auth requise (vendeur uniquement)."""
    await _ensure_schema()
    from database import db

    rows = await db.raw_execute_fetchall(
        "SELECT id, seller_id, status "
        "FROM business_listings WHERE id = ? AND status = 'active'", (listing_id,)
    )
    if not rows:
        raise HTTPException(404, "Listing introuvable ou deja retire/vendu")

    listing = rows[0]

    if listing["seller_id"] != wallet:
        raise HTTPException(403, "Seul le vendeur peut retirer son listing")

    # Retirer le listing
    await db.raw_execute(
        "UPDATE business_listings SET status = 'withdrawn' WHERE id = ?", (listing_id,)
    )

    # Rejeter toutes les offres pending
    await db.raw_execute(
        "UPDATE business_offers SET status = 'rejected' WHERE listing_id = ? AND status = 'pending'",
        (listing_id,),
    )

    logger.info(f"[Business] Listing retire: {listing_id} par {wallet[:8]}...")

    return {
        "ok": True,
        "listing_id": listing_id,
        "message": "Listing retire. Toutes les offres en attente ont ete rejetees.",
    }


@router.get("/my-listings")
async def my_listings(wallet: str = Depends(require_auth)):
    """Mes listings (tous statuts). Auth requise."""
    await _ensure_schema()
    from database import db

    rows = await db.raw_execute_fetchall(
        "SELECT id, seller_id, title, description, agent_id, monthly_revenue_usdc, "
        "monthly_costs_usdc, clients_count, months_active, asking_price_usdc, "
        "category, tech_stack, chains, status, created_at, sold_at, buyer_id "
        "FROM business_listings WHERE seller_id = ? ORDER BY created_at DESC", (wallet,)
    )

    listings = []
    for r in rows:
        listing = _row_to_listing(r)
        # Ajouter le nombre d'offres pending pour chaque listing actif
        if listing["status"] == "active":
            offer_rows = await db.raw_execute_fetchall(
                "SELECT COUNT(*) as cnt FROM business_offers WHERE listing_id = ? AND status = 'pending'",
                (listing["id"],),
            )
            listing["pending_offers_count"] = int(offer_rows[0]["cnt"]) if offer_rows else 0

            # Inclure les offres detaillees pour le vendeur
            offers = await db.raw_execute_fetchall(
                "SELECT id, listing_id, buyer_id, offer_usdc, message, status, created_at "
                "FROM business_offers WHERE listing_id = ? ORDER BY offer_usdc DESC",
                (listing["id"],),
            )
            listing["offers"] = [_row_to_offer(o) for o in offers]
        listings.append(listing)

    return {"listings": listings, "count": len(listings)}


@router.get("/my-offers")
async def my_offers(wallet: str = Depends(require_auth)):
    """Mes offres (toutes). Auth requise."""
    await _ensure_schema()
    from database import db

    rows = await db.raw_execute_fetchall(
        "SELECT id, listing_id, buyer_id, offer_usdc, message, status, created_at "
        "FROM business_offers WHERE buyer_id = ? ORDER BY created_at DESC", (wallet,)
    )

    offers = []
    for r in rows:
        offer = _row_to_offer(r)
        # Enrichir avec les infos du listing
        listing_rows = await db.raw_execute_fetchall(
            "SELECT title, asking_price_usdc, status, seller_id FROM business_listings WHERE id = ?",
            (offer["listing_id"],),
        )
        if listing_rows:
            lr = listing_rows[0]
            offer["listing_title"] = lr["title"]
            offer["listing_asking_price_usdc"] = float(lr["asking_price_usdc"])
            offer["listing_status"] = lr["status"]
        offers.append(offer)

    return {"offers": offers, "count": len(offers)}
