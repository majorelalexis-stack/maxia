"""MAXIA Art.11 - Reseau de Reference"""
import os, uuid, time, json
from fastapi import APIRouter, Depends, HTTPException
from auth import require_auth
from database import db
from models import RegisterReferralRequest

router = APIRouter(prefix="/api/referrals", tags=["referrals"])
RATE_BPS = int(os.getenv("REFERRAL_RATE_BPS", "200"))

@router.get("/my-code")
async def my_code(wallet: str = Depends(require_auth)):
    code = wallet[:8].upper() + "MAXIA"
    async with db._db.execute("SELECT COUNT(*) AS cnt FROM referrals WHERE referrer=?", (wallet,)) as c:
        r = await c.fetchone()
    earned = await _earnings(wallet)
    return {"code": code, "wallet": wallet, "referral_count": r["cnt"],
            "earned_usdc": earned, "rate_pct": RATE_BPS / 100}

@router.post("/register")
async def register_referral(req: RegisterReferralRequest, wallet: str = Depends(require_auth)):
    async with db._db.execute("SELECT ref_id FROM referrals WHERE referee=?", (wallet,)) as c:
        if await c.fetchone():
            raise HTTPException(400, "Deja parraine.")
    ref = {
        "referralId": str(uuid.uuid4()), "referrer": req.referrer_code,
        "referee": wallet, "registeredAt": int(time.time()), "earnedUsdc": 0
    }
    await db._db.execute(
        "INSERT INTO referrals(ref_id,referrer,referee,data) VALUES(?,?,?,?)",
        (ref["referralId"], req.referrer_code, wallet, json.dumps(ref)))
    await db._db.commit()
    return {"ok": True, "referralId": ref["referralId"]}

@router.get("/earnings")
async def earnings(wallet: str = Depends(require_auth)):
    earned = await _earnings(wallet)
    async with db._db.execute("SELECT COUNT(*) AS cnt FROM referrals WHERE referrer=?", (wallet,)) as c:
        r = await c.fetchone()
    return {"wallet": wallet, "earned_usdc": earned, "count": r["cnt"], "rate_pct": RATE_BPS / 100}

async def _earnings(wallet: str) -> float:
    try:
        async with db._db.execute(
            "SELECT COALESCE(SUM(json_extract(data,'$.earnedUsdc')),0) AS total"
            " FROM referrals WHERE referrer=?", (wallet,)
        ) as c:
            r = await c.fetchone()
        return float(r["total"] or 0)
    except Exception:
        return 0.0

async def add_commission(referee: str, amount: float):
    try:
        async with db._db.execute("SELECT ref_id,data FROM referrals WHERE referee=?", (referee,)) as c:
            row = await c.fetchone()
        if not row:
            return
        commission = amount * RATE_BPS / 10000
        d = json.loads(row["data"])
        d["earnedUsdc"] = d.get("earnedUsdc", 0) + commission
        await db._db.execute("UPDATE referrals SET data=? WHERE ref_id=?", (json.dumps(d), row["ref_id"]))
        await db._db.commit()
    except Exception:
        pass
