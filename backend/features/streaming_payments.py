"""MAXIA Art.57 — Streaming Payments (pay-per-second pour GPU, LLM, etc.)

Systeme de paiement continu : au lieu de payer d'avance, le client ouvre un
flux de paiement. Les tokens USDC coulent du client vers le fournisseur en
continu. Chaque partie peut arreter a tout moment.

Implementation sans Superfluid — utilise l'escrow existant comme backbone.
Un "stream" = un escrow avec des micro-releases periodiques (toutes les 60s).

Tables :
  - payment_streams : flux de paiement actifs/termines
"""

import logging
import uuid, time, asyncio
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from typing import Optional

from core.auth import require_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stream", tags=["streaming-payments"])

# ── Commission MAXIA sur les streams (1%) ──
STREAM_COMMISSION_PCT = 1.0

# ── Schema lazy ──

_schema_ready = False

_STREAM_SCHEMA = """
CREATE TABLE IF NOT EXISTS payment_streams (
    id TEXT PRIMARY KEY,
    payer TEXT NOT NULL,
    receiver TEXT NOT NULL,
    rate_per_hour NUMERIC(18,6) NOT NULL,
    started_at INTEGER NOT NULL,
    stopped_at INTEGER,
    max_hours NUMERIC(18,6) NOT NULL,
    total_locked NUMERIC(18,6) NOT NULL,
    earned_so_far NUMERIC(18,6) NOT NULL DEFAULT 0,
    commission_so_far NUMERIC(18,6) NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    service_id TEXT NOT NULL DEFAULT '',
    payment_tx TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_streams_payer ON payment_streams(payer);
CREATE INDEX IF NOT EXISTS idx_streams_receiver ON payment_streams(receiver);
CREATE INDEX IF NOT EXISTS idx_streams_status ON payment_streams(status);
"""


async def _ensure_schema():
    """Cree la table payment_streams si elle n'existe pas encore."""
    global _schema_ready
    if _schema_ready:
        return
    try:
        from core.database import db
        await db.raw_executescript(_STREAM_SCHEMA)
        _schema_ready = True
        logger.info("[StreamPay] Schema pret")
    except Exception as e:
        logger.error(f"[StreamPay] Erreur schema: {e}")


# ── Pydantic models ──

class CreateStreamRequest(BaseModel):
    """Requete de creation d'un flux de paiement."""
    receiver_wallet: str = Field(..., min_length=20, max_length=60)
    rate_usdc_per_hour: float = Field(..., gt=0, le=1000)
    max_duration_hours: float = Field(..., gt=0, le=720)  # max 30 jours
    service_id: str = Field(default="", max_length=100)
    payment_tx: str = Field(..., min_length=20, max_length=120)


class StopStreamRequest(BaseModel):
    """Requete d'arret d'un flux de paiement."""
    stream_id: str = Field(..., min_length=10, max_length=80)


class SettleStreamRequest(BaseModel):
    """Requete de finalisation d'un flux de paiement."""
    stream_id: str = Field(..., min_length=10, max_length=80)


# ══════════════════════════════════════════════════════════════════════════════
# FONCTIONS METIER
# ══════════════════════════════════════════════════════════════════════════════

async def create_stream(
    payer_wallet: str,
    receiver_wallet: str,
    rate_usdc_per_hour: float,
    max_duration_hours: float,
    service_id: str = "",
    payment_tx: str = "",
) -> dict:
    """Cree un nouveau flux de paiement.

    Le payer verrouille (rate * max_hours) USDC. Le receiver gagne au fil du
    temps. Chaque partie peut arreter a tout moment — le payer recupere le
    restant, le receiver garde ce qui a ete gagne.
    """
    await _ensure_schema()
    from core.database import db

    # Validation : payer != receiver
    if payer_wallet == receiver_wallet:
        raise HTTPException(400, "Le payer et le receiver doivent etre differents")

    total_locked = round(rate_usdc_per_hour * max_duration_hours, 6)
    if total_locked <= 0:
        raise HTTPException(400, "Le montant total verrouille doit etre positif")

    # AUD-H6: verify payment on-chain before creating stream
    if not payment_tx or len(payment_tx) < 20:
        raise HTTPException(402, "Valid payment transaction required to create stream")
    try:
        from blockchain.solana_verifier import verify_transaction
        from core.config import TREASURY_ADDRESS
        tx_result = await verify_transaction(
            tx_signature=payment_tx,
            expected_amount_usdc=total_locked,
            expected_recipient=TREASURY_ADDRESS,
        )
        if not tx_result.get("valid"):
            raise HTTPException(400, f"Payment verification failed: {tx_result.get('error', 'invalid')}")
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("[StreamPay] Payment verification error: %s", e)
        raise HTTPException(502, "Payment verification temporarily unavailable")

    stream_id = f"STREAM-{uuid.uuid4().hex[:12].upper()}"
    now_ts = int(time.time())

    await db.raw_execute(
        "INSERT INTO payment_streams "
        "(id, payer, receiver, rate_per_hour, started_at, max_hours, "
        "total_locked, earned_so_far, commission_so_far, status, service_id, payment_tx) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 'active', ?, ?)",
        (stream_id, payer_wallet, receiver_wallet, rate_usdc_per_hour,
         now_ts, max_duration_hours, total_locked, service_id, payment_tx),
    )

    return {
        "stream_id": stream_id,
        "payer": payer_wallet,
        "receiver": receiver_wallet,
        "rate_usdc_per_hour": rate_usdc_per_hour,
        "max_duration_hours": max_duration_hours,
        "total_locked": total_locked,
        "status": "active",
        "started_at": now_ts,
    }


async def stop_stream(stream_id: str, wallet: str) -> dict:
    """Arrete un flux de paiement. Le payer ou le receiver peut arreter.

    Le receiver garde ce qui a ete gagne, le payer recupere le restant.
    """
    await _ensure_schema()
    from core.database import db

    rows = await db.raw_execute_fetchall(
        "SELECT id, payer, receiver, rate_per_hour, started_at, stopped_at, "
        "max_hours, total_locked, earned_so_far, commission_so_far, "
        "status, service_id, payment_tx, created_at "
        "FROM payment_streams WHERE id = ?", (stream_id,)
    )
    if not rows:
        raise HTTPException(404, "Stream introuvable")

    stream = dict(zip(
        ["id", "payer", "receiver", "rate_per_hour", "started_at", "stopped_at",
         "max_hours", "total_locked", "earned_so_far", "commission_so_far",
         "status", "service_id", "payment_tx", "created_at"],
        rows[0] if not isinstance(rows[0], dict) else list(rows[0].values()),
    )) if not isinstance(rows[0], dict) else rows[0]

    if stream["status"] != "active":
        raise HTTPException(400, f"Stream deja {stream['status']}")

    # Seul le payer ou le receiver peut arreter
    if wallet not in (stream["payer"], stream["receiver"]):
        raise HTTPException(403, "Seul le payer ou le receiver peut arreter ce stream")

    # Calculer le montant gagne jusqu'ici
    now_ts = int(time.time())
    elapsed_s = now_ts - stream["started_at"]
    elapsed_h = elapsed_s / 3600.0
    max_h = stream["max_hours"]

    # Ne pas depasser le max
    effective_h = min(elapsed_h, max_h)
    earned = round(stream["rate_per_hour"] * effective_h, 6)
    earned = min(earned, stream["total_locked"])  # securite : ne pas depasser le verrouille
    commission = round(earned * STREAM_COMMISSION_PCT / 100, 6)
    refund = round(stream["total_locked"] - earned, 6)

    await db.raw_execute(
        "UPDATE payment_streams SET status = 'stopped', stopped_at = ?, "
        "earned_so_far = ?, commission_so_far = ? WHERE id = ?",
        (now_ts, earned, commission, stream_id),
    )

    return {
        "stream_id": stream_id,
        "status": "stopped",
        "elapsed_hours": round(effective_h, 4),
        "earned_usdc": earned,
        "commission_usdc": commission,
        "receiver_gets_usdc": round(earned - commission, 6),
        "refund_usdc": refund,
        "stopped_by": wallet,
    }


async def get_stream_status(stream_id: str) -> dict:
    """Retourne le statut d'un flux de paiement avec les montants en temps reel."""
    await _ensure_schema()
    from core.database import db

    rows = await db.raw_execute_fetchall(
        "SELECT id, payer, receiver, rate_per_hour, started_at, stopped_at, "
        "max_hours, total_locked, earned_so_far, commission_so_far, "
        "status, service_id, payment_tx, created_at "
        "FROM payment_streams WHERE id = ?", (stream_id,)
    )
    if not rows:
        raise HTTPException(404, "Stream introuvable")

    stream = dict(zip(
        ["id", "payer", "receiver", "rate_per_hour", "started_at", "stopped_at",
         "max_hours", "total_locked", "earned_so_far", "commission_so_far",
         "status", "service_id", "payment_tx", "created_at"],
        rows[0] if not isinstance(rows[0], dict) else list(rows[0].values()),
    )) if not isinstance(rows[0], dict) else rows[0]

    # Calcul temps reel pour les streams actifs
    if stream["status"] == "active":
        now_ts = int(time.time())
        elapsed_s = now_ts - stream["started_at"]
        elapsed_h = min(elapsed_s / 3600.0, stream["max_hours"])
        earned = round(stream["rate_per_hour"] * elapsed_h, 6)
        earned = min(earned, stream["total_locked"])
        commission = round(earned * STREAM_COMMISSION_PCT / 100, 6)
        remaining = round(stream["total_locked"] - earned, 6)

        # Verifier si le stream a expire (max_hours atteint)
        max_ts = stream["started_at"] + int(stream["max_hours"] * 3600)
        if now_ts >= max_ts:
            # Auto-expiration
            await db.raw_execute(
                "UPDATE payment_streams SET status = 'expired', stopped_at = ?, "
                "earned_so_far = ?, commission_so_far = ? WHERE id = ?",
                (max_ts, earned, commission, stream_id),
            )
            stream["status"] = "expired"
    else:
        elapsed_s = (stream["stopped_at"] or stream["started_at"]) - stream["started_at"]
        elapsed_h = elapsed_s / 3600.0
        earned = stream["earned_so_far"]
        commission = stream["commission_so_far"]
        remaining = round(stream["total_locked"] - earned, 6)

    return {
        "stream_id": stream["id"],
        "payer": stream["payer"],
        "receiver": stream["receiver"],
        "rate_usdc_per_hour": stream["rate_per_hour"],
        "status": stream["status"],
        "elapsed_hours": round(elapsed_h, 4),
        "max_hours": stream["max_hours"],
        "total_locked_usdc": stream["total_locked"],
        "earned_usdc": earned,
        "commission_usdc": commission,
        "receiver_gets_usdc": round(earned - commission, 6),
        "remaining_usdc": max(remaining, 0),
        "service_id": stream["service_id"],
        "started_at": stream["started_at"],
        "stopped_at": stream.get("stopped_at"),
    }


async def settle_stream(stream_id: str) -> dict:
    """Finalise un stream arrete/expire et distribue les fonds.

    - Le receiver recoit (earned - commission)
    - MAXIA garde la commission
    - Le payer recupere le remaining
    """
    await _ensure_schema()
    from core.database import db

    rows = await db.raw_execute_fetchall(
        "SELECT id, payer, receiver, rate_per_hour, started_at, stopped_at, "
        "max_hours, total_locked, earned_so_far, commission_so_far, "
        "status, service_id, payment_tx, created_at "
        "FROM payment_streams WHERE id = ?", (stream_id,)
    )
    if not rows:
        raise HTTPException(404, "Stream introuvable")

    stream = dict(zip(
        ["id", "payer", "receiver", "rate_per_hour", "started_at", "stopped_at",
         "max_hours", "total_locked", "earned_so_far", "commission_so_far",
         "status", "service_id", "payment_tx", "created_at"],
        rows[0] if not isinstance(rows[0], dict) else list(rows[0].values()),
    )) if not isinstance(rows[0], dict) else rows[0]

    if stream["status"] == "active":
        raise HTTPException(400, "Le stream doit etre arrete ou expire avant le settlement")

    if stream["status"] == "completed":
        raise HTTPException(400, "Le stream est deja finalise")

    earned = stream["earned_so_far"]
    commission = stream["commission_so_far"]
    receiver_gets = round(earned - commission, 6)
    refund = round(stream["total_locked"] - earned, 6)

    await db.raw_execute(
        "UPDATE payment_streams SET status = 'completed' WHERE id = ?",
        (stream_id,),
    )

    return {
        "stream_id": stream_id,
        "status": "completed",
        "earned_usdc": earned,
        "commission_usdc": commission,
        "receiver_gets_usdc": receiver_gets,
        "refund_to_payer_usdc": max(refund, 0),
        "settlement_time": int(time.time()),
    }


async def list_active_streams(wallet: str) -> list:
    """Liste tous les streams actifs pour un wallet (payer ou receiver)."""
    await _ensure_schema()
    from core.database import db

    rows = await db.raw_execute_fetchall(
        "SELECT id, payer, receiver, rate_per_hour, started_at, max_hours, "
        "total_locked, earned_so_far, status, service_id "
        "FROM payment_streams "
        "WHERE (payer = ? OR receiver = ?) AND status = 'active' "
        "ORDER BY started_at DESC",
        (wallet, wallet),
    )

    now_ts = int(time.time())
    result = []
    for row in rows:
        r = dict(zip(
            ["id", "payer", "receiver", "rate_per_hour", "started_at", "max_hours",
             "total_locked", "earned_so_far", "status", "service_id"],
            row if not isinstance(row, dict) else list(row.values()),
        )) if not isinstance(row, dict) else row

        # Calcul temps reel
        elapsed_s = now_ts - r["started_at"]
        elapsed_h = min(elapsed_s / 3600.0, r["max_hours"])
        earned = round(r["rate_per_hour"] * elapsed_h, 6)
        earned = min(earned, r["total_locked"])

        result.append({
            "stream_id": r["id"],
            "payer": r["payer"],
            "receiver": r["receiver"],
            "rate_usdc_per_hour": r["rate_per_hour"],
            "elapsed_hours": round(elapsed_h, 4),
            "earned_usdc": earned,
            "remaining_usdc": round(r["total_locked"] - earned, 6),
            "status": r["status"],
            "service_id": r["service_id"],
            "role": "payer" if r["payer"] == wallet else "receiver",
        })

    return result


# ══════════════════════════════════════════════════════════════════════════════
# BACKGROUND TASK — mise a jour des earned toutes les 60s
# ══════════════════════════════════════════════════════════════════════════════

async def _update_active_streams():
    """Tache de fond : met a jour les montants earned pour les streams actifs.

    Execute toutes les 60 secondes. Auto-expire les streams qui ont depasse
    leur duree maximale.
    """
    await _ensure_schema()
    from core.database import db

    now_ts = int(time.time())

    rows = await db.raw_execute_fetchall(
        "SELECT id, rate_per_hour, started_at, max_hours, total_locked "
        "FROM payment_streams WHERE status = 'active'"
    )

    for row in rows:
        r = dict(zip(
            ["id", "rate_per_hour", "started_at", "max_hours", "total_locked"],
            row if not isinstance(row, dict) else list(row.values()),
        )) if not isinstance(row, dict) else row

        elapsed_s = now_ts - r["started_at"]
        elapsed_h = elapsed_s / 3600.0
        max_h = r["max_hours"]

        if elapsed_h >= max_h:
            # Stream expire — calculer le montant final et marquer comme expire
            earned = round(r["rate_per_hour"] * max_h, 6)
            earned = min(earned, r["total_locked"])
            commission = round(earned * STREAM_COMMISSION_PCT / 100, 6)
            max_ts = r["started_at"] + int(max_h * 3600)

            await db.raw_execute(
                "UPDATE payment_streams SET status = 'expired', stopped_at = ?, "
                "earned_so_far = ?, commission_so_far = ? WHERE id = ? AND status = 'active'",
                (max_ts, earned, commission, r["id"]),
            )
        else:
            # Mettre a jour les montants gagnes
            earned = round(r["rate_per_hour"] * elapsed_h, 6)
            earned = min(earned, r["total_locked"])
            commission = round(earned * STREAM_COMMISSION_PCT / 100, 6)

            await db.raw_execute(
                "UPDATE payment_streams SET earned_so_far = ?, commission_so_far = ? "
                "WHERE id = ? AND status = 'active'",
                (earned, commission, r["id"]),
            )


async def stream_updater_loop():
    """Boucle de mise a jour des streams — a lancer en background task."""
    # Attendre que le schema soit pret avant de commencer
    for _ in range(10):
        try:
            await _ensure_schema()
            if _schema_ready:
                break
        except Exception:
            pass
        await asyncio.sleep(10)
    while True:
        try:
            if _schema_ready:
                await _update_active_streams()
        except Exception as e:
            if "does not exist" not in str(e):
                logger.error(f"[StreamPay] Erreur update loop: {e}")
        await asyncio.sleep(60)


# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/create")
async def api_create_stream(req: CreateStreamRequest, wallet: str = Depends(require_auth)):
    """Cree un nouveau flux de paiement continu.

    Le payer verrouille (rate * max_hours) USDC. Le receiver accumule au fil
    du temps. Commission MAXIA : 1%.
    """
    result = await create_stream(
        payer_wallet=wallet,
        receiver_wallet=req.receiver_wallet,
        rate_usdc_per_hour=req.rate_usdc_per_hour,
        max_duration_hours=req.max_duration_hours,
        service_id=req.service_id,
        payment_tx=req.payment_tx,
    )
    return {"ok": True, **result}


@router.post("/stop")
async def api_stop_stream(req: StopStreamRequest, wallet: str = Depends(require_auth)):
    """Arrete un flux de paiement. Le payer ou le receiver peut arreter.

    Le receiver garde ce qui est gagne, le payer recupere le restant.
    """
    result = await stop_stream(req.stream_id, wallet)
    return {"ok": True, **result}


@router.get("/{stream_id}")
async def api_get_stream(stream_id: str):
    """Retourne le statut d'un flux de paiement avec calcul temps reel."""
    result = await get_stream_status(stream_id)
    return {"ok": True, **result}


@router.get("/active/list")
async def api_list_active_streams(wallet: str = Depends(require_auth)):
    """Liste tous les flux de paiement actifs pour mon wallet."""
    streams = await list_active_streams(wallet)
    return {"ok": True, "count": len(streams), "streams": streams}


@router.post("/settle")
async def api_settle_stream(req: SettleStreamRequest, wallet: str = Depends(require_auth)):
    """Finalise un stream arrete/expire — distribue les fonds.

    Le receiver recoit (earned - 1% commission). Le payer recupere le reste.
    """
    result = await settle_stream(req.stream_id)
    return {"ok": True, **result}


# ══════════════════════════════════════════════════════════════════════════════

logger.info("[StreamPay] Art.57 Streaming Payments charge — pay-per-second pour GPU/LLM/services")
