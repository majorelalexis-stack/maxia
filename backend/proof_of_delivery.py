"""MAXIA Proof of Delivery V1 — Pattern UMA Optimistic Oracle adapte off-chain.
Liveness period de 2h : si le buyer ne dispute pas, la livraison est auto-confirmee.
Tables auto-creees au demarrage comme les autres modules."""
import asyncio
import json
import logging
import os
import time
import uuid

logger = logging.getLogger(__name__)

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Optional

def _get_db():
    """Lazy import to avoid stale reference from monkey-patched db singleton."""
    import database
    return database.db
from auth import require_auth
from alerts import alert_system, alert_error
from security import audit_log

# ── Configuration ──
LIVENESS_SECONDS = int(os.getenv("POD_LIVENESS_SECONDS", "7200"))  # 2h par defaut
AUTO_RESOLVE_MAX_USDC = float(os.getenv("POD_AUTO_RESOLVE_MAX", "50"))  # Auto-execute si <= $50

router = APIRouter(prefix="/delivery", tags=["delivery"])

# ══════════════════════════════════════════
# SCHEMA — Tables auto-creees
# ══════════════════════════════════════════

POD_SCHEMA = """
CREATE TABLE IF NOT EXISTS deliveries (
    id TEXT PRIMARY KEY,
    escrow_id TEXT NOT NULL,
    seller_wallet TEXT NOT NULL,
    buyer_wallet TEXT NOT NULL,
    delivery_hash TEXT NOT NULL,
    delivered_at INTEGER NOT NULL,
    status TEXT DEFAULT 'pending',
    liveness_end INTEGER NOT NULL,
    confirmed_at INTEGER,
    disputed_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_deliveries_escrow ON deliveries(escrow_id);
CREATE INDEX IF NOT EXISTS idx_deliveries_status ON deliveries(status);

CREATE TABLE IF NOT EXISTS pod_disputes (
    id TEXT PRIMARY KEY,
    delivery_id TEXT NOT NULL,
    escrow_id TEXT NOT NULL,
    initiator TEXT NOT NULL,
    reason TEXT NOT NULL,
    evidence_hash TEXT DEFAULT '',
    ai_recommendation TEXT,
    ai_confidence NUMERIC(18,6),
    resolution TEXT,
    resolved_at INTEGER,
    resolved_by TEXT,
    created_at INTEGER DEFAULT (strftime('%s','now')),
    FOREIGN KEY (delivery_id) REFERENCES deliveries(id)
);
CREATE INDEX IF NOT EXISTS idx_pod_disputes_delivery ON pod_disputes(delivery_id);
"""

_schema_initialized = False


async def _ensure_schema():
    """Cree les tables si elles n'existent pas encore."""
    global _schema_initialized
    if _schema_initialized:
        return
    try:
        db = _get_db()
        if not db:
            return
        await db.raw_executescript(POD_SCHEMA)
        _schema_initialized = True
        logger.info("Tables deliveries + pod_disputes creees")
    except Exception as e:
        logger.error("Schema error: %s", e)


# ══════════════════════════════════════════
# MODELES PYDANTIC
# ══════════════════════════════════════════

class DeliverySubmitRequest(BaseModel):
    escrow_id: str
    delivery_hash: str = Field(min_length=16, max_length=128)


class DeliveryDisputeRequest(BaseModel):
    reason: str = Field(min_length=10, max_length=2000)
    evidence_hash: str = Field(default="", max_length=128)


# ══════════════════════════════════════════
# HELPERS INTERNES
# ══════════════════════════════════════════

async def _get_delivery(delivery_id: str) -> Optional[dict]:
    """Recupere une livraison par ID."""
    db = _get_db(); rows = await db.raw_execute_fetchall(
        "SELECT id, escrow_id, seller_wallet, buyer_wallet, delivery_hash, "
        "delivered_at, status, liveness_end, confirmed_at, disputed_at "
        "FROM deliveries WHERE id=?", (delivery_id,))
    return dict(rows[0]) if rows else None


async def _get_delivery_by_escrow(escrow_id: str) -> Optional[dict]:
    """Recupere une livraison par escrow_id."""
    db = _get_db(); rows = await db.raw_execute_fetchall(
        "SELECT id, escrow_id, seller_wallet, buyer_wallet, delivery_hash, "
        "delivered_at, status, liveness_end, confirmed_at, disputed_at "
        "FROM deliveries WHERE escrow_id=?", (escrow_id,))
    return dict(rows[0]) if rows else None


async def _get_escrow_data(escrow_id: str) -> Optional[dict]:
    """Recupere les donnees de l'escrow depuis escrow_records."""
    db = _get_db(); rows = await db.raw_execute_fetchall(
        "SELECT data FROM escrow_records WHERE escrow_id=?", (escrow_id,))
    if rows:
        return json.loads(rows[0]["data"])
    return None


async def _release_escrow(escrow_id: str, buyer_wallet: str):
    """Libere l'escrow via le client escrow (confirm_delivery)."""
    from escrow_client import escrow_client
    result = await escrow_client.confirm_delivery(escrow_id, buyer_wallet)
    return result


async def _refund_escrow(escrow_id: str, buyer_wallet: str):
    """Rembourse l'escrow via reclaim (admin override: resolve_dispute)."""
    from escrow_client import escrow_client
    result = await escrow_client.resolve_dispute(escrow_id, release_to_seller=False)
    return result


# ══════════════════════════════════════════
# ENDPOINTS
# ══════════════════════════════════════════

@router.post("/submit")
async def submit_delivery(req: DeliverySubmitRequest, auth=Depends(require_auth)):
    """Seller soumet le hash de livraison apres avoir complete le service.
    Demarre la liveness period (2h par defaut)."""
    await _ensure_schema()

    # Verifier que l'escrow existe
    escrow = await _get_escrow_data(req.escrow_id)
    if not escrow:
        raise HTTPException(404, "Escrow introuvable")

    if escrow.get("status") != "locked":
        raise HTTPException(400, f"Escrow non verrouille (status: {escrow.get('status')})")

    # Verifier qu'il n'y a pas deja une livraison pour cet escrow
    existing = await _get_delivery_by_escrow(req.escrow_id)
    if existing:
        raise HTTPException(409, f"Livraison deja soumise pour cet escrow (id: {existing['id']})")

    # Verifier que l'appelant est bien le seller (auth = wallet string)
    seller = escrow.get("seller", "")
    wallet = auth if isinstance(auth, str) else auth.get("wallet", "") if isinstance(auth, dict) else ""
    if seller and wallet and seller.lower() != wallet.lower():
        raise HTTPException(403, "Seul le seller peut soumettre une livraison")

    now = int(time.time())
    delivery_id = str(uuid.uuid4())

    delivery = {
        "id": delivery_id,
        "escrow_id": req.escrow_id,
        "seller_wallet": seller,
        "buyer_wallet": escrow.get("buyer", ""),
        "delivery_hash": req.delivery_hash,
        "delivered_at": now,
        "status": "pending",
        "liveness_end": now + LIVENESS_SECONDS,
    }

    db = _get_db(); await db.raw_execute(
        "INSERT INTO deliveries (id, escrow_id, seller_wallet, buyer_wallet, "
        "delivery_hash, delivered_at, status, liveness_end) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (delivery["id"], delivery["escrow_id"], delivery["seller_wallet"],
         delivery["buyer_wallet"], delivery["delivery_hash"],
         delivery["delivered_at"], delivery["status"], delivery["liveness_end"]))

    liveness_min = LIVENESS_SECONDS // 60
    logger.info("Livraison soumise: %s... escrow=%s... liveness=%dmin",
                delivery_id[:8], req.escrow_id[:8], liveness_min)
    audit_log("pod_submit", "system",
              f"delivery={delivery_id} escrow={req.escrow_id} hash={req.delivery_hash[:16]}...")

    await alert_system(
        "Livraison soumise",
        f"Escrow `{req.escrow_id[:8]}...`\n"
        f"Hash: `{req.delivery_hash[:16]}...`\n"
        f"Liveness: {liveness_min} min\n"
        f"Le buyer a {liveness_min} min pour contester ou confirmer.",
    )

    return {
        "success": True,
        "delivery_id": delivery_id,
        "status": "pending",
        "liveness_end": delivery["liveness_end"],
        "liveness_minutes": liveness_min,
        "message": f"Livraison soumise. Auto-confirmation dans {liveness_min} min si pas de dispute.",
    }


@router.post("/confirm/{delivery_id}")
async def confirm_delivery(delivery_id: str, auth=Depends(require_auth)):
    """Buyer confirme la livraison (le hash correspond). Libere l'escrow."""
    await _ensure_schema()

    delivery = await _get_delivery(delivery_id)
    if not delivery:
        raise HTTPException(404, "Livraison introuvable")

    # Verifier que l'appelant est le buyer
    wallet = auth if isinstance(auth, str) else auth.get("wallet", "") if isinstance(auth, dict) else ""
    if delivery["buyer_wallet"] and wallet and delivery["buyer_wallet"].lower() != wallet.lower():
        raise HTTPException(403, "Seul le buyer peut confirmer la livraison")

    if delivery["status"] != "pending":
        raise HTTPException(400, f"Status invalide: {delivery['status']}")

    # Confirmer
    now = int(time.time())
    db = _get_db(); await db.raw_execute(
        "UPDATE deliveries SET status='confirmed', confirmed_at=? WHERE id=?",
        (now, delivery_id))

    # Liberer l'escrow
    escrow_result = await _release_escrow(delivery["escrow_id"], delivery["buyer_wallet"])

    logger.info("Livraison confirmee par buyer: %s... escrow=%s...",
                delivery_id[:8], delivery['escrow_id'][:8])
    audit_log("pod_confirm", "system",
              f"delivery={delivery_id} escrow={delivery['escrow_id']} by=buyer")

    await alert_system(
        "Livraison confirmee",
        f"Buyer a confirme la livraison `{delivery_id[:8]}...`\n"
        f"Escrow `{delivery['escrow_id'][:8]}...` libere au seller.",
    )

    return {
        "success": True,
        "delivery_id": delivery_id,
        "status": "confirmed",
        "escrow_released": escrow_result.get("success", False),
    }


@router.post("/dispute/{delivery_id}")
async def dispute_delivery(delivery_id: str, req: DeliveryDisputeRequest,
                           auth=Depends(require_auth)):
    """Buyer conteste la livraison. Declenche l'evaluation IA."""
    await _ensure_schema()

    delivery = await _get_delivery(delivery_id)
    if not delivery:
        raise HTTPException(404, "Livraison introuvable")

    # Verifier que l'appelant est le buyer
    wallet = auth if isinstance(auth, str) else auth.get("wallet", "") if isinstance(auth, dict) else ""
    if delivery["buyer_wallet"] and wallet and delivery["buyer_wallet"].lower() != wallet.lower():
        raise HTTPException(403, "Seul le buyer peut contester la livraison")

    if delivery["status"] != "pending":
        raise HTTPException(400, f"Status invalide pour dispute: {delivery['status']}")

    # Mettre a jour le statut
    now = int(time.time())
    db = _get_db(); await db.raw_execute(
        "UPDATE deliveries SET status='disputed', disputed_at=? WHERE id=?",
        (now, delivery_id))

    # Creer le dispute record
    dispute_id = str(uuid.uuid4())
    db = _get_db(); await db.raw_execute(
        "INSERT INTO pod_disputes (id, delivery_id, escrow_id, initiator, reason, "
        "evidence_hash, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (dispute_id, delivery_id, delivery["escrow_id"], "buyer",
         req.reason, req.evidence_hash, now))

    logger.info("Dispute ouverte: %s... delivery=%s... reason=%s...",
                dispute_id[:8], delivery_id[:8], req.reason[:50])
    audit_log("pod_dispute", "system",
              f"dispute={dispute_id} delivery={delivery_id} reason={req.reason[:100]}")

    # Lancer l'evaluation IA en arriere-plan
    from dispute_resolver import evaluate_dispute
    asyncio.create_task(_run_ai_evaluation(dispute_id, delivery_id, req.reason, req.evidence_hash))

    await alert_system(
        "Dispute ouverte",
        f"Buyer conteste la livraison `{delivery_id[:8]}...`\n"
        f"Escrow: `{delivery['escrow_id'][:8]}...`\n"
        f"Raison: {req.reason[:200]}\n"
        f"Evaluation IA en cours...",
    )

    return {
        "success": True,
        "dispute_id": dispute_id,
        "delivery_id": delivery_id,
        "status": "disputed",
        "message": "Dispute ouverte. Evaluation IA en cours.",
    }


async def _run_ai_evaluation(dispute_id: str, delivery_id: str,
                             reason: str, evidence_hash: str):
    """Execute l'evaluation IA et traite le resultat."""
    try:
        from dispute_resolver import evaluate_dispute, resolve_dispute
        result = await evaluate_dispute(delivery_id, reason, evidence_hash)

        # Mettre a jour le dispute avec la recommandation IA
        db = _get_db(); await db.raw_execute(
            "UPDATE pod_disputes SET ai_recommendation=?, ai_confidence=? WHERE id=?",
            (result.get("recommendation", ""), result.get("confidence", 0), dispute_id))

        # Recuperer les donnees de l'escrow pour le montant
        delivery = await _get_delivery(delivery_id)
        escrow = await _get_escrow_data(delivery["escrow_id"]) if delivery else None
        amount = escrow.get("amount_usdc", 0) if escrow else 0
        confidence = result.get("confidence", 0)

        # Decision auto ou manuelle ?
        if confidence >= 80 and amount <= AUTO_RESOLVE_MAX_USDC:
            # Auto-execute (VERT) — haute confiance + petit montant
            resolution = result.get("recommendation", "release")
            await resolve_dispute(dispute_id, resolution)
            logger.info("Auto-resolved dispute %s... -> %s (confidence=%s%%, amount=$%.2f)",
                        dispute_id[:8], resolution, confidence, amount)
        else:
            # Envoi Telegram pour approbation admin (ORANGE)
            await _send_dispute_for_approval(
                dispute_id, delivery_id, result, amount, confidence)
            logger.info("Dispute %s... envoyee au fondateur (confidence=%s%%, amount=$%.2f)",
                        dispute_id[:8], confidence, amount)

    except Exception as e:
        logger.error("Erreur evaluation IA dispute %s...: %s", dispute_id[:8], e)
        await alert_error("PoD", f"Evaluation dispute echouee: {e}")


async def _send_dispute_for_approval(dispute_id: str, delivery_id: str,
                                     ai_result: dict, amount: float,
                                     confidence: float):
    """Envoie la dispute au fondateur sur Telegram avec boutons Go/No-Go."""
    tg_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    tg_chat = os.getenv("TELEGRAM_CHAT_ID", "")
    if not tg_token or not tg_chat:
        logger.warning("Telegram non configure — dispute %s... en attente", dispute_id[:8])
        return

    recommendation = ai_result.get("recommendation", "?")
    reasoning = ai_result.get("reasoning", "Pas de details")

    message = (
        f"<b>DISPUTE #{dispute_id[:8]}...</b>\n\n"
        f"Montant: <b>${amount:.2f} USDC</b>\n"
        f"IA recommande: <b>{recommendation.upper()}</b>\n"
        f"Confiance: <b>{confidence:.0f}%</b>\n\n"
        f"Analyse:\n<i>{reasoning[:500]}</i>\n\n"
        f"Delivery: <code>{delivery_id[:12]}...</code>"
    )

    payload = {
        "chat_id": tg_chat,
        "text": message[:4000],
        "parse_mode": "HTML",
        "reply_markup": json.dumps({
            "inline_keyboard": [[
                {"text": "Refund buyer", "callback_data": f"dispute_refund:{dispute_id}"},
                {"text": "Release seller", "callback_data": f"dispute_release:{dispute_id}"},
                {"text": "Split 50/50", "callback_data": f"dispute_split:{dispute_id}"},
            ]]
        }),
    }

    try:
        from http_client import get_http_client
        client = get_http_client()
        resp = await client.post(
            f"https://api.telegram.org/bot{tg_token}/sendMessage",
            json=payload,
            timeout=10,
        )
        if resp.status_code != 200:
            logger.error("Telegram erreur %d", resp.status_code)
    except Exception as e:
        logger.error("Telegram erreur: %s", e)


@router.get("/pending", response_model=None)
async def list_pending_deliveries(auth=Depends(require_auth)):
    """Liste toutes les livraisons en periode de liveness (monitoring)."""
    await _ensure_schema()

    now = int(time.time())
    db = _get_db(); rows = await db.raw_execute_fetchall(
        "SELECT id, escrow_id, seller_wallet, buyer_wallet, delivery_hash, "
        "delivered_at, status, liveness_end, confirmed_at, disputed_at "
        "FROM deliveries WHERE status='pending' ORDER BY liveness_end ASC")
    deliveries = []
    for row in rows:
        d = dict(row)
        d["liveness_remaining_seconds"] = max(0, d["liveness_end"] - now)
        d["liveness_expired"] = d["liveness_remaining_seconds"] == 0
        deliveries.append(d)

    return {
        "pending_count": len(deliveries),
        "deliveries": deliveries,
    }


@router.get("/{escrow_id}")
async def get_delivery_status(escrow_id: str, auth=Depends(require_auth)):
    """Recupere le statut de livraison pour un escrow."""
    await _ensure_schema()

    delivery = await _get_delivery_by_escrow(escrow_id)
    if not delivery:
        raise HTTPException(404, "Aucune livraison pour cet escrow")

    # Charger les disputes liees
    db = _get_db(); disputes = await db.raw_execute_fetchall(
        "SELECT id, delivery_id, escrow_id, initiator, reason, evidence_hash, "
        "ai_recommendation, ai_confidence, resolution, resolved_at, resolved_by, created_at "
        "FROM pod_disputes WHERE delivery_id=? ORDER BY created_at DESC",
        (delivery["id"],))
    disputes_list = [dict(d) for d in disputes] if disputes else []

    now = int(time.time())
    remaining_seconds = max(0, delivery["liveness_end"] - now)

    return {
        "delivery": delivery,
        "disputes": disputes_list,
        "liveness_remaining_seconds": remaining_seconds,
        "liveness_expired": remaining_seconds == 0,
    }


# ══════════════════════════════════════════
# TACHE DE FOND — Verification des liveness expirees
# ══════════════════════════════════════════

async def check_liveness_expirations():
    """Verifie les livraisons dont la liveness a expire.
    Toute livraison pending dont le delai est passe est auto-confirmee.
    A appeler toutes les 5 minutes depuis le scheduler."""
    await _ensure_schema()

    now = int(time.time())
    db = _get_db(); rows = await db.raw_execute_fetchall(
        "SELECT id, escrow_id, seller_wallet, buyer_wallet, status, liveness_end "
        "FROM deliveries WHERE status='pending' AND liveness_end < ?", (now,))

    if not rows:
        return

    count = 0
    for row in rows:
        delivery = dict(row)
        delivery_id = delivery["id"]
        escrow_id = delivery["escrow_id"]

        try:
            # Auto-confirmer
            db = _get_db(); await db.raw_execute(
                "UPDATE deliveries SET status='confirmed', confirmed_at=? WHERE id=?",
                (now, delivery_id))

            # Liberer l'escrow
            escrow_result = await _release_escrow(escrow_id, delivery["buyer_wallet"])

            status = "OK" if escrow_result.get("success") else f"WARN: {escrow_result.get('error', '?')}"
            logger.info("Auto-confirmed delivery %s... for escrow %s... (liveness expired) [%s]",
                        delivery_id[:8], escrow_id[:8], status)
            audit_log("pod_auto_confirm", "system",
                      f"delivery={delivery_id} escrow={escrow_id}")
            count += 1

        except Exception as e:
            logger.error("Erreur auto-confirm %s...: %s", delivery_id[:8], e)
            await alert_error("PoD", f"Auto-confirm echoue delivery={delivery_id[:8]}... : {e}")

    if count > 0:
        logger.info("%d livraison(s) auto-confirmee(s)", count)
