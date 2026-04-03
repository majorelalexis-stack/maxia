"""MAXIA Art.12 - Data Marketplace"""
import os, uuid, time, json
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from core.auth import require_auth
from core.models import DatasetListRequest

router = APIRouter(prefix="/api/data", tags=["data_marketplace"])
FEE_BPS = int(os.getenv("DATA_FEE_BPS", "200"))


def _get_db():
    """Get the current DB singleton (patched at startup)."""
    from core.database import db
    return db


class DataPurchaseRequest(BaseModel):
    dataset_id: str
    tx_signature: str


@router.get("/datasets")
async def list_all(category: str = None, max_price: float = None):
    db = _get_db()
    rows = await db.raw_execute_fetchall("SELECT data FROM datasets ORDER BY created_at DESC")
    ds = [json.loads(r["data"] if isinstance(r, dict) else r[0]) for r in rows]
    if category:
        ds = [d for d in ds if d.get("category") == category]
    if max_price:
        ds = [d for d in ds if d.get("priceUsdc", 0) <= max_price]
    return ds


@router.post("/datasets")
async def create_dataset(req: DatasetListRequest, wallet: str = Depends(require_auth)):
    db = _get_db()
    fee = req.price_usdc * FEE_BPS / 10000
    d = {
        "datasetId": str(uuid.uuid4()), "seller": wallet, "name": req.name,
        "description": req.description, "category": req.category, "sizeMb": req.size_mb,
        "priceUsdc": req.price_usdc, "feeUsdc": fee, "netUsdc": req.price_usdc - fee,
        "sampleHash": req.sample_hash, "format": req.format,
        "sales": 0, "revenue": 0, "listedAt": int(time.time()), "status": "active"
    }
    await db.raw_execute(
        "INSERT INTO datasets(dataset_id,seller,data) VALUES(?,?,?)",
        (d["datasetId"], wallet, json.dumps(d)))
    return d


@router.post("/purchase")
async def purchase(req: DataPurchaseRequest, wallet: str = Depends(require_auth)):
    db = _get_db()
    if await db.tx_already_processed(req.tx_signature):
        raise HTTPException(400, "Transaction deja utilisee.")
    rows = await db.raw_execute_fetchall("SELECT data FROM datasets WHERE dataset_id=?", (req.dataset_id,))
    if not rows:
        raise HTTPException(404, "Dataset introuvable.")
    d = json.loads(rows[0]["data"] if isinstance(rows[0], dict) else rows[0][0])
    d["sales"] += 1
    d["revenue"] = d.get("revenue", 0) + d["netUsdc"]
    await db.raw_execute("UPDATE datasets SET data=? WHERE dataset_id=?", (json.dumps(d), req.dataset_id))
    p = {
        "purchaseId": str(uuid.uuid4()), "datasetId": req.dataset_id,
        "buyer": wallet, "seller": d["seller"],
        "priceUsdc": d["priceUsdc"], "feeUsdc": d["feeUsdc"],
        "txSignature": req.tx_signature, "purchasedAt": int(time.time())
    }
    await db.raw_execute(
        "INSERT INTO data_purchases(purchase_id,data) VALUES(?,?)",
        (p["purchaseId"], json.dumps(p)))
    await db.record_transaction(wallet, req.tx_signature, d["priceUsdc"], "data_purchase")
    return {"ok": True, "purchaseId": p["purchaseId"], "dataset": d}


@router.get("/my-datasets")
async def my_datasets(wallet: str = Depends(require_auth)):
    db = _get_db()
    rows = await db.raw_execute_fetchall("SELECT data FROM datasets WHERE seller=?", (wallet,))
    return [json.loads(r["data"] if isinstance(r, dict) else r[0]) for r in rows]


@router.get("/my-purchases")
async def my_purchases(wallet: str = Depends(require_auth)):
    """List all datasets purchased by the authenticated wallet."""
    db = _get_db()
    rows = await db.raw_execute_fetchall("SELECT data FROM data_purchases WHERE data LIKE ?", (f'%"buyer": "{wallet}"%',))
    return [json.loads(r["data"] if isinstance(r, dict) else r[0]) for r in rows]


@router.get("/download/{purchase_id}")
async def download_dataset(purchase_id: str, wallet: str = Depends(require_auth)):
    """Download a purchased dataset. Verifies buyer owns the purchase."""
    db = _get_db()

    # Verify purchase exists and belongs to buyer
    rows = await db.raw_execute_fetchall(
        "SELECT data FROM data_purchases WHERE purchase_id=?", (purchase_id,)
    )
    if not rows:
        raise HTTPException(404, "Purchase not found.")
    purchase = json.loads(rows[0]["data"] if isinstance(rows[0], dict) else rows[0][0])
    if purchase.get("buyer") != wallet:
        raise HTTPException(403, "Not your purchase.")

    # Fetch dataset metadata
    ds_rows = await db.raw_execute_fetchall(
        "SELECT data FROM datasets WHERE dataset_id=?", (purchase.get("datasetId"),)
    )
    if not ds_rows:
        raise HTTPException(404, "Dataset no longer available.")
    dataset = json.loads(ds_rows[0]["data"] if isinstance(ds_rows[0], dict) else ds_rows[0][0])

    # Return dataset with download_url if seller provided one, or metadata for retrieval
    download_url = dataset.get("downloadUrl") or dataset.get("sampleHash")
    return {
        "purchaseId": purchase_id,
        "datasetId": dataset.get("datasetId"),
        "name": dataset.get("name"),
        "format": dataset.get("format"),
        "sizeMb": dataset.get("sizeMb"),
        "downloadUrl": download_url,
        "message": "Contact seller for file delivery" if not download_url else "Use downloadUrl to retrieve data",
        "seller": dataset.get("seller"),
        "purchasedAt": purchase.get("purchasedAt"),
    }
