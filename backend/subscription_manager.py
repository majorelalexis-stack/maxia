"""MAXIA Art.10 - Abonnements IA"""
import os, uuid, time, json
from fastapi import APIRouter, Depends, HTTPException
from auth import require_auth
from database import db
from models import SubscribeRequest
from config import SUBSCRIPTION_PLANS

router = APIRouter(prefix="/api/subscriptions", tags=["subscriptions"])

@router.get("/plans")
async def get_plans():
    return SUBSCRIPTION_PLANS

@router.post("/subscribe")
async def subscribe(req: SubscribeRequest, wallet: str = Depends(require_auth)):
    if req.plan not in SUBSCRIPTION_PLANS:
        raise HTTPException(400, f"Plan inconnu: {list(SUBSCRIPTION_PLANS.keys())}")
    if await db.tx_already_processed(req.tx_signature):
        raise HTTPException(400, "Transaction deja utilisee.")
    plan = SUBSCRIPTION_PLANS[req.plan]
    price = plan["price_usdc"] * req.duration_months
    expires_at = int(time.time()) + req.duration_months * 30 * 86400
    sub = {
        "subscriptionId": str(uuid.uuid4()), "wallet": wallet, "plan": req.plan,
        "priceUsdc": price, "txSignature": req.tx_signature,
        "startedAt": int(time.time()), "expiresAt": expires_at,
        "status": "active", "durationMonths": req.duration_months
    }
    await db._db.execute(
        "INSERT OR REPLACE INTO subscriptions(sub_id,wallet,data) VALUES(?,?,?)",
        (sub["subscriptionId"], wallet, json.dumps(sub)))
    await db._db.commit()
    await db.record_transaction(wallet, req.tx_signature, price, "subscription")
    return sub

@router.get("/my")
async def my_sub(wallet: str = Depends(require_auth)):
    async with db._db.execute(
        "SELECT data FROM subscriptions WHERE wallet=?"
        " AND json_extract(data,'$.status')='active'"
        " ORDER BY json_extract(data,'$.expiresAt') DESC LIMIT 1",
        (wallet,)
    ) as c:
        row = await c.fetchone()
    if not row:
        return {"plan": "none", "active": False}
    s = json.loads(row[0])
    s["active"] = s["expiresAt"] > int(time.time())
    return s

@router.get("/revenue")
async def revenue():
    async with db._db.execute(
        "SELECT COALESCE(SUM(json_extract(data,'$.priceUsdc')),0) AS total FROM subscriptions"
    ) as c:
        row = await c.fetchone()
    return {"total_usdc": float(row["total"]) if row else 0}
