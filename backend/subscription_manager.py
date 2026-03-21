"""MAXIA Art.10 - Abonnements IA"""
import os, uuid, time, json
from fastapi import APIRouter, Depends, HTTPException
from auth import require_auth
from models import SubscribeRequest
from config import SUBSCRIPTION_PLANS

router = APIRouter(prefix="/api/subscriptions", tags=["subscriptions"])


def _get_db():
    from database import db
    return db


@router.get("/plans")
async def get_plans():
    return SUBSCRIPTION_PLANS

@router.post("/subscribe")
async def subscribe(req: SubscribeRequest, wallet: str = Depends(require_auth)):
    db = _get_db()
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
    await db.raw_execute(
        "INSERT OR REPLACE INTO subscriptions(sub_id,wallet,data) VALUES(?,?,?)",
        (sub["subscriptionId"], wallet, json.dumps(sub)))
    await db.record_transaction(wallet, req.tx_signature, price, "subscription")
    return sub

@router.get("/my")
async def my_sub(wallet: str = Depends(require_auth)):
    db = _get_db()
    rows = await db.raw_execute_fetchall(
        "SELECT data FROM subscriptions WHERE wallet=? ORDER BY created_at DESC LIMIT 1",
        (wallet,))
    if not rows:
        return {"plan": "none", "active": False}
    s = json.loads(rows[0]["data"] if isinstance(rows[0], dict) else rows[0][0])
    s["active"] = s.get("expiresAt", 0) > int(time.time())
    return s

@router.get("/revenue")
async def revenue():
    db = _get_db()
    rows = await db.raw_execute_fetchall(
        "SELECT COALESCE(SUM(json_extract(data,'$.priceUsdc')),0) AS total FROM subscriptions")
    total = float(rows[0]["total"]) if rows and rows[0] else 0
    return {"total_usdc": total}
