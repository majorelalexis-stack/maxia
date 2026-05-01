"""MAXIA Hub Phase 5 — Testament cryptographique.

Routes :
  POST   /api/hub/will                          → create_will (auth testator)
  POST   /api/hub/will/{will_id}/activate       → activate_will (auth testator)
  POST   /api/hub/will/accept/{will_id}         → accept_will (auth successor)
  POST   /api/hub/will/execute/{will_id}        → execute_will (public)
  DELETE /api/hub/will/{will_id}                → revoke_will (auth testator)
  GET    /api/hub/market/wills                  → list_auction_wills (public)
  POST   /api/hub/market/wills/{will_id}/bid    → place_bid (auth bidder)
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

_HEARTBEAT_WINDOW = 60
_AUCTION_DEFAULT_DURATION = 7 * 24 * 3600  # 7 jours


# ═══════════════════════════════════════════════════════════════════════════════
# Auth helper
# ═══════════════════════════════════════════════════════════════════════════════

def _verify_hub_sig(public_key_b58: str, hub_id: str, ts: int, sig_b58: str) -> bool:
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
# Fonctions métier pures (testables indépendamment)
# ═══════════════════════════════════════════════════════════════════════════════

def _compute_score_transfer(testator_score: int, successor_score: int, pct: float) -> int:
    """Calcule le nouveau score du successeur après transmission."""
    return min(100, successor_score + round(testator_score * pct))


# ═══════════════════════════════════════════════════════════════════════════════
# Fonctions de service (appelées par les routes ET les tests)
# ═══════════════════════════════════════════════════════════════════════════════

async def create_will(
    testator_hub_id: str,
    will_type: str,
    successor_hub_id: Optional[str],
    transfer_score_pct: float,
    transfer_lineage: bool,
    grace_period_hours: int,
    auction_end_ts: Optional[int],
    min_bid_usdc: float,
) -> dict:
    """Crée un testament en statut draft. 1 seul testament actif ou draft par agent."""
    existing = await db._fetchone(
        "SELECT will_id FROM hub_wills WHERE testator_hub_id=? AND status IN ('draft','active')",
        (testator_hub_id,),
    )
    if existing is not None:
        raise HTTPException(409, "Agent already has an active or draft will")

    actual_successor = None if will_type == "auction" else successor_hub_id
    will_id = uuid.uuid4().hex
    now = int(time.time())

    await db.raw_execute(
        "INSERT INTO hub_wills "
        "(will_id, testator_hub_id, will_type, successor_hub_id, transfer_score_pct, "
        "transfer_lineage, grace_period_hours, auction_end_ts, min_bid_usdc, "
        "status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?)",
        (
            will_id, testator_hub_id, will_type, actual_successor,
            transfer_score_pct, int(transfer_lineage), grace_period_hours,
            auction_end_ts, min_bid_usdc, now,
        ),
    )

    logger.info("[HUB-WILL] Created will %s for testator %s", will_id, testator_hub_id[:8])

    return {
        "will_id": will_id,
        "testator_hub_id": testator_hub_id,
        "will_type": will_type,
        "successor_hub_id": actual_successor,
        "transfer_score_pct": transfer_score_pct,
        "grace_period_hours": grace_period_hours,
        "min_bid_usdc": min_bid_usdc,
        "status": "draft",
        "created_at": now,
    }


async def activate_will(will_id: str, testator_hub_id: str) -> dict:
    """Passe un testament de draft → active. Calcule auction_end_ts si type auction."""
    w = await db._fetchone(
        "SELECT will_id, testator_hub_id, will_type, status, auction_end_ts "
        "FROM hub_wills WHERE will_id=?",
        (will_id,),
    )
    if w is None:
        raise HTTPException(404, "Will not found")
    if w["testator_hub_id"] != testator_hub_id:
        raise HTTPException(403, "Only the testator can activate this will")
    if w["status"] != "draft":
        raise HTTPException(409, f"Will is already in status '{w['status']}'")

    now = int(time.time())
    auction_end_ts = w["auction_end_ts"]
    if w["will_type"] == "auction" and auction_end_ts is None:
        auction_end_ts = now + _AUCTION_DEFAULT_DURATION

    await db.raw_execute(
        "UPDATE hub_wills SET status='active', activated_at=?, auction_end_ts=? WHERE will_id=?",
        (now, auction_end_ts, will_id),
    )

    logger.info("[HUB-WILL] Activated will %s", will_id)

    return {
        "will_id": will_id,
        "status": "active",
        "activated_at": now,
        "auction_end_ts": auction_end_ts,
    }


async def accept_will(will_id: str, successor_hub_id: str) -> dict:
    """Le successeur désigné accepte un testament simple/conditional."""
    w = await db._fetchone(
        "SELECT will_id, testator_hub_id, will_type, successor_hub_id, status, grace_start_ts "
        "FROM hub_wills WHERE will_id=?",
        (will_id,),
    )
    if w is None:
        raise HTTPException(404, "Will not found")
    if w["will_type"] == "auction":
        raise HTTPException(400, "Auction wills cannot be accepted directly; use the bidding system")
    if w["status"] != "active":
        raise HTTPException(409, f"Will is not active (current status: '{w['status']}')")
    if w["successor_hub_id"] != successor_hub_id:
        raise HTTPException(403, "Only the designated successor can accept this will")

    logger.info("[HUB-WILL] Will %s accepted by %s", will_id, successor_hub_id[:8])

    return {
        "will_id": will_id,
        "accepted": True,
        "successor_hub_id": successor_hub_id,
    }


async def execute_will(will_id: str) -> dict:
    """Exécute le testament si le testateur est inactif depuis >= grace_period_hours."""
    w = await db._fetchone(
        "SELECT will_id, testator_hub_id, successor_hub_id, will_type, "
        "transfer_score_pct, transfer_lineage, grace_period_hours, "
        "auction_end_ts, status "
        "FROM hub_wills WHERE will_id=?",
        (will_id,),
    )
    if w is None:
        raise HTTPException(404, "Will not found")
    if w["status"] != "active":
        raise HTTPException(409, f"Will is not active (current status: '{w['status']}')")

    testator = await db._fetchone(
        "SELECT hub_id, score, last_heartbeat FROM hub_agents WHERE hub_id=?",
        (w["testator_hub_id"],),
    )
    if testator is None:
        raise HTTPException(404, "Testator agent not found")

    now = int(time.time())
    last_hb = testator["last_heartbeat"] or 0
    grace_seconds = int(w["grace_period_hours"]) * 3600

    if last_hb + grace_seconds >= now:
        remaining = (last_hb + grace_seconds) - now
        raise HTTPException(
            403,
            f"Grace period not elapsed (testator last seen {(now - last_hb) // 3600}h ago, "
            f"need {w['grace_period_hours']}h). "
            f"{remaining // 3600}h remaining.",
        )

    # Déterminer le successeur
    successor_hub_id = w["successor_hub_id"]
    if w["will_type"] == "auction":
        top_bid = await db._fetchone(
            "SELECT bidder_hub_id, amount_usdc FROM hub_will_bids "
            "WHERE will_id=? AND status='active' ORDER BY amount_usdc DESC LIMIT 1",
            (will_id,),
        )
        if top_bid is None:
            raise HTTPException(409, "Auction has no bids — cannot execute")
        successor_hub_id = top_bid["bidder_hub_id"]

    successor = await db._fetchone(
        "SELECT hub_id, score FROM hub_agents WHERE hub_id=?",
        (successor_hub_id,),
    )
    if successor is None:
        raise HTTPException(404, "Successor agent not found")

    new_score = _compute_score_transfer(
        testator_score=int(testator["score"]),
        successor_score=int(successor["score"]),
        pct=float(w["transfer_score_pct"]),
    )

    await db.raw_execute(
        "UPDATE hub_agents SET score=? WHERE hub_id=?",
        (new_score, successor_hub_id),
    )

    if int(w["transfer_lineage"]) == 1:
        children = await db._fetchall(
            "SELECT lineage_id FROM hub_lineage WHERE parent_hub_id=? AND status='active'",
            (w["testator_hub_id"],),
        )
        if children:
            await db.raw_execute(
                "UPDATE hub_lineage SET parent_hub_id=? WHERE parent_hub_id=? AND status='active'",
                (successor_hub_id, w["testator_hub_id"]),
            )

    await db.raw_execute(
        "UPDATE hub_wills SET status='executed', executed_at=? WHERE will_id=?",
        (now, will_id),
    )

    logger.info(
        "[HUB-WILL] Executed will %s — testator %s → successor %s (score %d→%d)",
        will_id, w["testator_hub_id"][:8], successor_hub_id[:8],
        int(successor["score"]), new_score,
    )

    return {
        "will_id": will_id,
        "status": "executed",
        "testator_hub_id": w["testator_hub_id"],
        "successor_hub_id": successor_hub_id,
        "new_successor_score": new_score,
        "executed_at": now,
    }


async def revoke_will(will_id: str, testator_hub_id: str) -> dict:
    """Révoque un testament (draft|active → revoked)."""
    w = await db._fetchone(
        "SELECT will_id, testator_hub_id, status FROM hub_wills WHERE will_id=?",
        (will_id,),
    )
    if w is None:
        raise HTTPException(404, "Will not found")
    if w["testator_hub_id"] != testator_hub_id:
        raise HTTPException(403, "Only the testator can revoke this will")
    if w["status"] not in ("draft", "active"):
        raise HTTPException(409, f"Cannot revoke a will in status '{w['status']}'")

    now = int(time.time())
    await db.raw_execute(
        "UPDATE hub_wills SET status='revoked', revoked_at=? WHERE will_id=?",
        (now, will_id),
    )

    logger.info("[HUB-WILL] Revoked will %s", will_id)

    return {"will_id": will_id, "status": "revoked", "revoked_at": now}


async def list_auction_wills() -> dict:
    """Retourne les auctions actives avec auction_end_ts dans le futur."""
    now = int(time.time())
    rows = await db._fetchall(
        "SELECT will_id, testator_hub_id, will_type, successor_hub_id, "
        "transfer_score_pct, grace_period_hours, auction_end_ts, min_bid_usdc, "
        "status, created_at "
        "FROM hub_wills "
        "WHERE will_type='auction' AND status='active' AND auction_end_ts > ?",
        (now,),
    )
    wills = [dict(r) for r in rows]
    return {"total": len(wills), "wills": wills}


async def place_bid(will_id: str, bidder_hub_id: str, amount_usdc: float) -> dict:
    """Pose une enchère sur un testament de type auction."""
    w = await db._fetchone(
        "SELECT will_id, will_type, status, min_bid_usdc, auction_end_ts "
        "FROM hub_wills WHERE will_id=?",
        (will_id,),
    )
    if w is None:
        raise HTTPException(404, "Will not found")
    if w["will_type"] != "auction":
        raise HTTPException(400, "This will is not an auction")
    if w["status"] != "active":
        raise HTTPException(409, f"Will is not active (status: '{w['status']}')")

    now = int(time.time())
    auction_end = w["auction_end_ts"]
    if auction_end is not None and now >= auction_end:
        raise HTTPException(410, "Auction has ended")

    if amount_usdc < float(w["min_bid_usdc"]):
        raise HTTPException(
            400,
            f"Bid {amount_usdc} USDC is below minimum {w['min_bid_usdc']} USDC",
        )

    # Vérifier que l'enchère dépasse le top actuel
    top_bids = await db._fetchall(
        "SELECT amount_usdc FROM hub_will_bids "
        "WHERE will_id=? AND status='active' ORDER BY amount_usdc DESC LIMIT 1",
        (will_id,),
    )
    if top_bids:
        top_amount = float(top_bids[0]["amount_usdc"])
        if amount_usdc <= top_amount:
            raise HTTPException(
                400,
                f"Bid {amount_usdc} USDC must exceed current highest bid {top_amount} USDC",
            )

    bid_id = uuid.uuid4().hex
    await db.raw_execute(
        "INSERT OR REPLACE INTO hub_will_bids "
        "(bid_id, will_id, bidder_hub_id, amount_usdc, bid_ts, status) "
        "VALUES (?, ?, ?, ?, ?, 'active')",
        (bid_id, will_id, bidder_hub_id, amount_usdc, now),
    )

    logger.info("[HUB-WILL] Bid %s USDC on will %s by %s", amount_usdc, will_id, bidder_hub_id[:8])

    return {
        "bid_id": bid_id,
        "will_id": will_id,
        "bidder_hub_id": bidder_hub_id,
        "amount_usdc": amount_usdc,
        "bid_ts": now,
        "status": "active",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Routes FastAPI
# ═══════════════════════════════════════════════════════════════════════════════

will_router = APIRouter(prefix="/api/hub/will", tags=["hub-will"])
market_router = APIRouter(prefix="/api/hub/market", tags=["hub-will"])


@will_router.post("")
async def route_create_will(
    will_type: str = "simple",
    successor_hub_id: Optional[str] = None,
    transfer_score_pct: float = 0.8,
    transfer_lineage: bool = True,
    grace_period_hours: int = 72,
    auction_end_ts: Optional[int] = None,
    min_bid_usdc: float = 0.0,
    x_hub_id: Optional[str] = Header(None),
    x_hub_sig: Optional[str] = Header(None),
    x_hub_ts: Optional[str] = Header(None),
):
    agent = await _require_hub_agent_auth(x_hub_id, x_hub_sig, x_hub_ts)
    return await create_will(
        testator_hub_id=agent["hub_id"],
        will_type=will_type,
        successor_hub_id=successor_hub_id,
        transfer_score_pct=transfer_score_pct,
        transfer_lineage=transfer_lineage,
        grace_period_hours=grace_period_hours,
        auction_end_ts=auction_end_ts,
        min_bid_usdc=min_bid_usdc,
    )


@will_router.post("/{will_id}/activate")
async def route_activate_will(
    will_id: str,
    x_hub_id: Optional[str] = Header(None),
    x_hub_sig: Optional[str] = Header(None),
    x_hub_ts: Optional[str] = Header(None),
):
    agent = await _require_hub_agent_auth(x_hub_id, x_hub_sig, x_hub_ts)
    return await activate_will(will_id=will_id, testator_hub_id=agent["hub_id"])


@will_router.post("/accept/{will_id}")
async def route_accept_will(
    will_id: str,
    x_hub_id: Optional[str] = Header(None),
    x_hub_sig: Optional[str] = Header(None),
    x_hub_ts: Optional[str] = Header(None),
):
    agent = await _require_hub_agent_auth(x_hub_id, x_hub_sig, x_hub_ts)
    return await accept_will(will_id=will_id, successor_hub_id=agent["hub_id"])


@will_router.post("/execute/{will_id}")
async def route_execute_will(will_id: str):
    return await execute_will(will_id=will_id)


@will_router.delete("/{will_id}")
async def route_revoke_will(
    will_id: str,
    x_hub_id: Optional[str] = Header(None),
    x_hub_sig: Optional[str] = Header(None),
    x_hub_ts: Optional[str] = Header(None),
):
    agent = await _require_hub_agent_auth(x_hub_id, x_hub_sig, x_hub_ts)
    return await revoke_will(will_id=will_id, testator_hub_id=agent["hub_id"])


@market_router.get("/wills")
async def route_list_auction_wills():
    return await list_auction_wills()


@market_router.post("/wills/{will_id}/bid")
async def route_place_bid(
    will_id: str,
    amount_usdc: float,
    x_hub_id: Optional[str] = Header(None),
    x_hub_sig: Optional[str] = Header(None),
    x_hub_ts: Optional[str] = Header(None),
):
    agent = await _require_hub_agent_auth(x_hub_id, x_hub_sig, x_hub_ts)
    return await place_bid(
        will_id=will_id,
        bidder_hub_id=agent["hub_id"],
        amount_usdc=amount_usdc,
    )
