"""MAXIA Subscriptions — Abonnements recurrents et streaming de paiements USDC."""
import uuid, time, json, logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

logger = logging.getLogger("maxia.subscriptions")

router = APIRouter(prefix="/api/subscriptions", tags=["subscriptions"])

# ── Stockage en memoire (en prod: base de donnees) ──
_subscriptions: dict = {}  # sub_id -> subscription dict
_payment_log: list = []    # historique des paiements traites

# ── Intervalles ──
INTERVAL_SECONDS = {
    "daily": 86400,
    "weekly": 604800,
    "monthly": 2592000,  # 30 jours
}

SUPPORTED_CHAINS = [
    "solana", "base", "ethereum", "xrpl", "polygon",
    "arbitrum", "avalanche", "bnb", "ton", "sui", "tron",
]


# ═══════════════════════════════════════════════════════════
#  Pydantic Models
# ═══════════════════════════════════════════════════════════

class CreateSubscriptionRequest(BaseModel):
    subscriber: str = Field(..., min_length=1, description="Wallet address du souscripteur")
    provider: str = Field(..., min_length=1, description="Wallet address du fournisseur")
    service_id: str = Field(..., min_length=1, description="ID du service souscrit")
    amount_usdc: float = Field(..., gt=0, description="Montant USDC par periode")
    interval: str = Field(..., pattern=r"^(daily|weekly|monthly)$", description="Frequence de paiement")
    chain: str = Field(default="solana", description="Blockchain pour les paiements")


class CancelSubscriptionRequest(BaseModel):
    reason: Optional[str] = None


# ═══════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _compute_next_payment(interval: str, from_time: Optional[str] = None) -> str:
    """Calcule la prochaine date de paiement a partir de maintenant ou d'un timestamp."""
    if from_time:
        base = datetime.fromisoformat(from_time)
    else:
        base = datetime.now(timezone.utc)
    delta_s = INTERVAL_SECONDS.get(interval, 86400)
    next_dt = base + timedelta(seconds=delta_s)
    return next_dt.isoformat()


def _build_subscription(req: CreateSubscriptionRequest) -> dict:
    """Construit un dict subscription a partir de la requete."""
    now = _now_iso()
    return {
        "sub_id": str(uuid.uuid4()),
        "subscriber": req.subscriber,
        "provider": req.provider,
        "service_id": req.service_id,
        "amount_usdc": req.amount_usdc,
        "interval": req.interval,
        "status": "active",
        "created_at": now,
        "next_payment_at": _compute_next_payment(req.interval),
        "payments_made": 0,
        "total_paid_usdc": 0.0,
        "chain": req.chain,
    }


# ═══════════════════════════════════════════════════════════
#  POST /api/subscriptions/create
# ═══════════════════════════════════════════════════════════

@router.post("/create")
async def create_subscription(req: CreateSubscriptionRequest):
    """Cree un abonnement recurrent USDC entre deux agents."""
    # Validation chain
    if req.chain not in SUPPORTED_CHAINS:
        raise HTTPException(400, f"Chain non supportee: {req.chain}. Chains: {SUPPORTED_CHAINS}")

    # Pas d'abonnement a soi-meme
    if req.subscriber == req.provider:
        raise HTTPException(400, "Le souscripteur et le fournisseur ne peuvent pas etre identiques.")

    # Verifier doublon actif
    for sub in _subscriptions.values():
        if (sub["subscriber"] == req.subscriber
                and sub["provider"] == req.provider
                and sub["service_id"] == req.service_id
                and sub["status"] == "active"):
            raise HTTPException(
                409,
                f"Abonnement actif deja existant: {sub['sub_id']}"
            )

    sub = _build_subscription(req)
    _subscriptions[sub["sub_id"]] = sub

    logger.info(
        "Subscription created: %s -> %s, %.2f USDC/%s on %s",
        req.subscriber[:8], req.provider[:8], req.amount_usdc, req.interval, req.chain,
    )

    return {"ok": True, "subscription": sub}


# ═══════════════════════════════════════════════════════════
#  GET /api/subscriptions/list
# ═══════════════════════════════════════════════════════════

@router.get("/list")
async def list_subscriptions(
    agent: str = Query(..., description="Wallet address de l'agent"),
    role: Optional[str] = Query(None, pattern=r"^(subscriber|provider)$",
                                description="Filtrer par role"),
    status: Optional[str] = Query(None, pattern=r"^(active|paused|cancelled|expired)$",
                                  description="Filtrer par statut"),
):
    """Liste les abonnements d'un agent (comme souscripteur ou fournisseur)."""
    results = []
    for sub in _subscriptions.values():
        is_subscriber = sub["subscriber"] == agent
        is_provider = sub["provider"] == agent
        if not (is_subscriber or is_provider):
            continue
        if role == "subscriber" and not is_subscriber:
            continue
        if role == "provider" and not is_provider:
            continue
        if status and sub["status"] != status:
            continue
        results.append(sub)

    # Tri par date de creation decroissante
    results.sort(key=lambda s: s["created_at"], reverse=True)
    return {"count": len(results), "subscriptions": results}


# ═══════════════════════════════════════════════════════════
#  GET /api/subscriptions/stats  (before /{sub_id} to avoid path collision)
# ═══════════════════════════════════════════════════════════

@router.get("/stats")
async def subscription_stats():
    """Statistiques globales des abonnements MAXIA."""
    all_subs = list(_subscriptions.values())
    active = [s for s in all_subs if s["status"] == "active"]
    cancelled = [s for s in all_subs if s["status"] == "cancelled"]

    total_mrr = 0.0
    for s in active:
        if s["interval"] == "daily":
            total_mrr += s["amount_usdc"] * 30
        elif s["interval"] == "weekly":
            total_mrr += s["amount_usdc"] * 4.33
        elif s["interval"] == "monthly":
            total_mrr += s["amount_usdc"]

    total_paid = sum(s["total_paid_usdc"] for s in all_subs)
    total_payments = sum(s["payments_made"] for s in all_subs)

    # Repartition par chain
    chains = {}
    for s in active:
        chains[s["chain"]] = chains.get(s["chain"], 0) + 1

    return {
        "total_subscriptions": len(all_subs),
        "active": len(active),
        "cancelled": len(cancelled),
        "paused": len([s for s in all_subs if s["status"] == "paused"]),
        "expired": len([s for s in all_subs if s["status"] == "expired"]),
        "total_payments_processed": total_payments,
        "total_paid_usdc": round(total_paid, 2),
        "estimated_mrr_usdc": round(total_mrr, 2),
        "active_by_chain": chains,
        "payment_log_size": len(_payment_log),
    }


# ═══════════════════════════════════════════════════════════
#  GET /api/subscriptions/x402/stats  (before /{sub_id} to avoid path collision)
# ═══════════════════════════════════════════════════════════

@router.get("/x402/stats", tags=["x402"])
async def x402_stats():
    """
    Statistiques des micropaiements x402 et comparaison avec les abonnements.
    Permet de voir le ratio one-time vs recurring.
    """
    sub_total = sum(s["total_paid_usdc"] for s in _subscriptions.values())
    active_subs = len([s for s in _subscriptions.values() if s["status"] == "active"])

    return {
        "x402_micropayments": {
            "description": "One-time micropayments via x402 protocol",
            "note": "Detailed x402 stats available at /api/x402/info",
        },
        "subscriptions": {
            "description": "Recurring USDC payments between agents",
            "active_count": active_subs,
            "total_volume_usdc": round(sub_total, 2),
        },
        "comparison": {
            "model": "x402 for one-time, subscriptions for recurring",
            "supported_chains": SUPPORTED_CHAINS,
        },
    }


# ═══════════════════════════════════════════════════════════
#  GET /api/subscriptions/{sub_id}
# ═══════════════════════════════════════════════════════════

@router.get("/{sub_id}")
async def get_subscription(sub_id: str):
    """Retourne les details d'un abonnement."""
    sub = _subscriptions.get(sub_id)
    if not sub:
        raise HTTPException(404, f"Abonnement introuvable: {sub_id}")
    return sub


# ═══════════════════════════════════════════════════════════
#  POST /api/subscriptions/{sub_id}/cancel
# ═══════════════════════════════════════════════════════════

@router.post("/{sub_id}/cancel")
async def cancel_subscription(sub_id: str, req: Optional[CancelSubscriptionRequest] = None):
    """Annule un abonnement. Le statut passe a 'cancelled'."""
    sub = _subscriptions.get(sub_id)
    if not sub:
        raise HTTPException(404, f"Abonnement introuvable: {sub_id}")

    if sub["status"] == "cancelled":
        raise HTTPException(400, "Abonnement deja annule.")

    sub["status"] = "cancelled"
    sub["cancelled_at"] = _now_iso()
    if req and req.reason:
        sub["cancel_reason"] = req.reason

    logger.info("Subscription cancelled: %s (reason: %s)", sub_id, req.reason if req else "none")

    return {"ok": True, "subscription": sub}


# ═══════════════════════════════════════════════════════════
#  POST /api/subscriptions/{sub_id}/process
# ═══════════════════════════════════════════════════════════

@router.post("/{sub_id}/process")
async def process_payment(sub_id: str):
    """
    Traite le prochain paiement d'un abonnement.
    Appele par le scheduler. Le transfert USDC est simule
    (V13 smart contract necessaire pour les vrais transferts on-chain).
    """
    sub = _subscriptions.get(sub_id)
    if not sub:
        raise HTTPException(404, f"Abonnement introuvable: {sub_id}")

    if sub["status"] != "active":
        raise HTTPException(400, f"Abonnement non actif (statut: {sub['status']})")

    # Verifier que le paiement est du
    now = datetime.now(timezone.utc)
    next_payment = datetime.fromisoformat(sub["next_payment_at"])
    if now < next_payment:
        return {
            "ok": False,
            "reason": "Paiement pas encore du",
            "next_payment_at": sub["next_payment_at"],
        }

    # Simuler le paiement (log uniquement, pas de transfert reel)
    payment_record = {
        "payment_id": str(uuid.uuid4()),
        "sub_id": sub_id,
        "subscriber": sub["subscriber"],
        "provider": sub["provider"],
        "amount_usdc": sub["amount_usdc"],
        "chain": sub["chain"],
        "processed_at": _now_iso(),
        "simulated": True,  # V13: sera remplace par un vrai tx_signature
    }
    _payment_log.append(payment_record)

    # Mettre a jour l'abonnement
    sub["payments_made"] += 1
    sub["total_paid_usdc"] += sub["amount_usdc"]
    sub["last_payment_at"] = payment_record["processed_at"]
    sub["next_payment_at"] = _compute_next_payment(sub["interval"], payment_record["processed_at"])

    logger.info(
        "Payment processed (simulated): %s -> %s, %.2f USDC (#%d)",
        sub["subscriber"][:8], sub["provider"][:8],
        sub["amount_usdc"], sub["payments_made"],
    )

    return {"ok": True, "payment": payment_record, "subscription": sub}


# ═══════════════════════════════════════════════════════════
#  Fonctions utilitaires pour le scheduler
# ═══════════════════════════════════════════════════════════

async def process_due_subscriptions() -> dict:
    """
    Traite tous les abonnements dont le paiement est du.
    Appele par le scheduler periodiquement.
    Retourne un resume des paiements traites.
    """
    now = datetime.now(timezone.utc)
    processed = 0
    failed = 0
    skipped = 0

    for sub_id, sub in _subscriptions.items():
        if sub["status"] != "active":
            skipped += 1
            continue

        next_payment = datetime.fromisoformat(sub["next_payment_at"])
        if now >= next_payment:
            try:
                # Simuler le paiement
                payment_record = {
                    "payment_id": str(uuid.uuid4()),
                    "sub_id": sub_id,
                    "subscriber": sub["subscriber"],
                    "provider": sub["provider"],
                    "amount_usdc": sub["amount_usdc"],
                    "chain": sub["chain"],
                    "processed_at": _now_iso(),
                    "simulated": True,
                }
                _payment_log.append(payment_record)

                sub["payments_made"] += 1
                sub["total_paid_usdc"] += sub["amount_usdc"]
                sub["last_payment_at"] = payment_record["processed_at"]
                sub["next_payment_at"] = _compute_next_payment(
                    sub["interval"], payment_record["processed_at"]
                )
                processed += 1
            except Exception as e:
                logger.error("Failed to process subscription %s: %s", sub_id, e)
                failed += 1

    logger.info(
        "Subscription batch: %d processed, %d failed, %d skipped",
        processed, failed, skipped,
    )
    return {"processed": processed, "failed": failed, "skipped": skipped}
