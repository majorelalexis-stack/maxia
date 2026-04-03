"""MAXIA V12 — Staking, credit score, and alert subscription routes"""
import logging
from fastapi import APIRouter, HTTPException, Depends, Request
from core.auth import require_auth
from core.error_utils import safe_error

logger = logging.getLogger(__name__)
router = APIRouter()


# ═══════════════════════════════════════════════════════════
#  V11: REPUTATION STAKING (Art.18)
# ═══════════════════════════════════════════════════════════

@router.get("/api/staking/stats")
async def staking_stats():
    from infra.reputation_staking import reputation_staking
    try:
        return await reputation_staking.get_stats()
    except Exception:
        return {"total_stakers": 0, "total_staked_usdc": 0, "pending_disputes": 0, "total_slashed": 0, "min_stake_usdc": 50, "slash_pct": 50, "dispute_delay_h": 48}

@router.get("/api/staking/{wallet}")
async def get_stake(wallet: str):
    from infra.reputation_staking import reputation_staking
    return reputation_staking.get_stake(wallet)

@router.post("/api/staking/stake")
async def create_stake(req: dict, wallet: str = Depends(require_auth)):
    from infra.reputation_staking import reputation_staking
    return await reputation_staking.stake(
        wallet=wallet,
        amount_usdc=float(req.get("amount_usdc", 0)),
        tx_signature=req.get("tx_signature", ""),
    )

@router.post("/api/staking/dispute")
async def open_dispute(req: dict, wallet: str = Depends(require_auth)):
    from infra.reputation_staking import reputation_staking
    return await reputation_staking.open_dispute(
        reporter_wallet=wallet,
        accused_wallet=req.get("accused_wallet", ""),
        reason=req.get("reason", ""),
        evidence=req.get("evidence", ""),
    )

@router.post("/api/staking/resolve")
async def resolve_dispute(req: dict, request: Request):
    from core.security import require_admin
    require_admin(request)
    from infra.reputation_staking import reputation_staking
    from core.database import db
    return await reputation_staking.resolve_dispute(
        dispute_id=req.get("dispute_id", ""),
        slash=req.get("slash", False),
        db=db,
    )

@router.post("/api/staking/unstake")
async def unstake(wallet: str = Depends(require_auth)):
    """Withdraw remaining stake (minus any slash). Sends USDC back on-chain."""
    from infra.reputation_staking import reputation_staking
    return await reputation_staking.unstake(wallet=wallet)


# ── Agent Credit Score (portable, verifiable) ──

@router.get("/api/public/credit-score/{wallet}")
async def get_credit_score(wallet: str):
    """Get portable credit score for an agent. Verifiable by any platform."""
    from agents.agent_credit_score import compute_credit_score
    from core.database import db
    return await compute_credit_score(wallet, db)


@router.post("/api/public/credit-score/verify")
async def verify_credit_score(request: Request):
    """Verify a credit score signature from another platform."""
    from agents.agent_credit_score import verify_score_signature, VERIFICATION_FEE_USDC
    body = await request.json()
    valid = verify_score_signature(
        body.get("wallet", ""),
        body.get("score", 0),
        body.get("grade", ""),
        body.get("computed_at", ""),
        body.get("signature", ""),
    )
    return {"valid": valid, "fee_usdc": VERIFICATION_FEE_USDC}


# ═══════════════════════════════════════════════════════════
#  ALERT SERVICE — $0.99/mo Telegram alerts (price/whale/yield/tx)
# ═══════════════════════════════════════════════════════════

@router.post("/api/public/alerts/subscribe")
async def alert_subscribe(request: Request):
    """Subscribe to MAXIA Telegram alerts ($0.99/month USDC)."""
    from infra.alert_service import subscribe
    body = await request.json()
    return await subscribe(body.get("wallet", ""), body.get("chat_id", ""), body.get("alerts"))

@router.post("/api/public/alerts/unsubscribe")
async def alert_unsubscribe(request: Request):
    """Unsubscribe from MAXIA Telegram alerts."""
    from infra.alert_service import unsubscribe
    body = await request.json()
    return await unsubscribe(body.get("wallet", ""))

@router.get("/api/public/alerts/plans")
async def alert_plans():
    """Available alert subscription plans."""
    return {
        "plans": [
            {"name": "Basic", "price_usdc": 0.99, "period": "monthly", "alerts": ["price", "whale", "yield", "transaction"]},
        ],
        "free_alerts": ["transaction"],  # Transaction alerts are free for all users
    }
