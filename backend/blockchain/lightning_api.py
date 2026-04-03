"""MAXIA Lightning API — Create/check invoices, get BTC price in sats."""
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/lightning", tags=["lightning"])


class InvoiceRequest(BaseModel):
    amount_usd: float = Field(..., gt=0, le=1000)
    description: str = Field(default="MAXIA payment", max_length=200)


@router.post("/invoice")
async def create_lightning_invoice(req: InvoiceRequest):
    """Create a Lightning invoice for a given USD amount."""
    from blockchain.lightning_client import get_btc_price, usd_to_sats, create_invoice

    btc_price = await get_btc_price()
    sats = usd_to_sats(req.amount_usd, btc_price)
    if sats < 1:
        raise HTTPException(400, "Amount too small (< 1 sat)")

    result = await create_invoice(sats, req.description)
    if not result.get("success"):
        raise HTTPException(500, result.get("error", "Invoice creation failed"))

    return {
        "lightning_invoice": result["lightning_invoice"],
        "charge_id": result["id"],
        "amount_sats": sats,
        "amount_usd": req.amount_usd,
        "btc_price": btc_price,
        "expires_at": result.get("expires_at"),
    }


@router.get("/check/{charge_id}")
async def check_lightning_payment(charge_id: str):
    """Check if a Lightning invoice has been paid."""
    from blockchain.lightning_client import check_payment

    result = await check_payment(charge_id)
    if not result.get("success"):
        raise HTTPException(500, result.get("error", "Check failed"))

    return {
        "charge_id": charge_id,
        "paid": result.get("paid", False),
        "status": result.get("status", "unknown"),
        "amount_sats": result.get("amount_sats", 0),
    }


@router.get("/price")
async def btc_price():
    """Get current BTC/USD price and sats conversion."""
    from blockchain.lightning_client import get_btc_price, usd_to_sats

    price = await get_btc_price()
    return {
        "btc_usd": price,
        "sats_per_dollar": usd_to_sats(1.0, price),
        "examples": {
            "$0.01": usd_to_sats(0.01, price),
            "$0.10": usd_to_sats(0.10, price),
            "$1.00": usd_to_sats(1.00, price),
            "$10.00": usd_to_sats(10.00, price),
        },
    }
