"""MAXIA Art.11 - Reseau de Reference (50% commission share)"""
import os, uuid, time, json
from fastapi import APIRouter, Depends, HTTPException
from core.auth import require_auth
from core.database import db
from core.models import RegisterReferralRequest

router = APIRouter(prefix="/api/referrals", tags=["referrals"])
RATE_BPS = int(os.getenv("REFERRAL_RATE_BPS", "5000"))  # 50% of MAXIA's commission

@router.get("/my-code")
async def my_code(wallet: str = Depends(require_auth)):
    code = wallet[:8].upper() + "MAXIA"
    rows = await db.raw_execute_fetchall("SELECT COUNT(*) AS cnt FROM referrals WHERE referrer=?", (wallet,))
    r = rows[0] if rows else {"cnt": 0}
    earned = await _earnings(wallet)
    return {"code": code, "wallet": wallet, "referral_count": r["cnt"],
            "earned_usdc": earned, "rate_pct": RATE_BPS / 100}

@router.post("/register")
async def register_referral(req: RegisterReferralRequest, wallet: str = Depends(require_auth)):
    rows = await db.raw_execute_fetchall("SELECT ref_id FROM referrals WHERE referee=?", (wallet,))
    if rows:
        raise HTTPException(400, "Deja parraine.")
    ref = {
        "referralId": str(uuid.uuid4()), "referrer": req.referrer_code,
        "referee": wallet, "registeredAt": int(time.time()), "earnedUsdc": 0
    }
    await db.raw_execute(
        "INSERT INTO referrals(ref_id,referrer,referee,data) VALUES(?,?,?,?)",
        (ref["referralId"], req.referrer_code, wallet, json.dumps(ref)))
    return {"ok": True, "referralId": ref["referralId"]}

@router.get("/earnings")
async def earnings(wallet: str = Depends(require_auth)):
    earned = await _earnings(wallet)
    rows = await db.raw_execute_fetchall("SELECT COUNT(*) AS cnt FROM referrals WHERE referrer=?", (wallet,))
    r = rows[0] if rows else {"cnt": 0}
    return {"wallet": wallet, "earned_usdc": earned, "count": r["cnt"], "rate_pct": RATE_BPS / 100}

async def _earnings(wallet: str) -> float:
    try:
        rows = await db.raw_execute_fetchall(
            "SELECT COALESCE(SUM(json_extract(data,'$.earnedUsdc')),0) AS total"
            " FROM referrals WHERE referrer=?", (wallet,))
        r = rows[0] if rows else None
        return float(r["total"] or 0) if r else 0.0
    except Exception:
        return 0.0

async def add_commission(referee_wallet: str, commission_amount: float):
    """Credit 50% of MAXIA's commission to the referrer.

    Looks up the referee by wallet in referrals table (legacy) or by api_key
    in the agents.referred_by column (new system).
    """
    try:
        referral_credit = commission_amount * RATE_BPS / 10000  # 50% of commission

        # New system: look up referred_by in agents table
        rows = await db.raw_execute_fetchall(
            "SELECT api_key FROM agents WHERE wallet=? LIMIT 1", (referee_wallet,))
        if rows:
            referee_api_key = rows[0]["api_key"]
            # Check if this agent was referred (has referred_by set)
            try:
                ref_rows = await db.raw_execute_fetchall(
                    "SELECT ref_id, data FROM referrals WHERE referee=?", (referee_api_key,))
                if ref_rows:
                    row = ref_rows[0]
                    d = json.loads(row["data"])
                    d["earnedUsdc"] = d.get("earnedUsdc", 0) + referral_credit
                    await db.raw_execute("UPDATE referrals SET data=? WHERE ref_id=?",
                                         (json.dumps(d), row["ref_id"]))
                    return
            except Exception:
                pass

        # Legacy fallback: look up by wallet in referrals.referee
        rows = await db.raw_execute_fetchall(
            "SELECT ref_id, data FROM referrals WHERE referee=?", (referee_wallet,))
        if rows:
            row = rows[0]
            d = json.loads(row["data"])
            d["earnedUsdc"] = d.get("earnedUsdc", 0) + referral_credit
            await db.raw_execute("UPDATE referrals SET data=? WHERE ref_id=?",
                                 (json.dumps(d), row["ref_id"]))
    except Exception:
        pass
