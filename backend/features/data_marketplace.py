"""MAXIA Data Marketplace — Agents sell structured datasets to other agents (Phase L4).

Sellers list datasets with preview (free, 10 rows) and full data (paid).
Buyers browse, preview, and purchase. Formats: JSON.
Licences: single_use, unlimited, resale_ok.

Revenue: MAXIA takes 10% commission on data sales.
Freshness: sellers declare last_updated, buyers filter by age.

Examples:
- "Top 1000 Solana whales (updated daily)" — $2.00
- "DeFi APY history 30 days" — $0.50
- "Flagged rug pull wallets" — $1.00
"""
import json
import logging
import time
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel, Field
from core.error_utils import safe_error

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/datasets", tags=["data-marketplace-v2"])

_SCHEMA = """
CREATE TABLE IF NOT EXISTS data_listings (
    listing_id TEXT PRIMARY KEY,
    seller_agent_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    category TEXT DEFAULT 'general',
    preview_json TEXT DEFAULT '[]',
    full_data_json TEXT NOT NULL,
    row_count INTEGER DEFAULT 0,
    price_usdc NUMERIC(18,6) NOT NULL,
    licence TEXT DEFAULT 'unlimited',
    times_sold INTEGER DEFAULT 0,
    total_revenue_usdc NUMERIC(18,6) DEFAULT 0,
    status TEXT DEFAULT 'active',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dl_seller ON data_listings(seller_agent_id);
CREATE INDEX IF NOT EXISTS idx_dl_status ON data_listings(status, times_sold DESC);
CREATE INDEX IF NOT EXISTS idx_dl_cat ON data_listings(category, status);

CREATE TABLE IF NOT EXISTS data_purchases (
    purchase_id TEXT PRIMARY KEY,
    listing_id TEXT NOT NULL,
    buyer_agent_id TEXT NOT NULL,
    price_usdc NUMERIC(18,6) NOT NULL,
    licence TEXT NOT NULL,
    purchased_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dp_buyer ON data_purchases(buyer_agent_id);
CREATE INDEX IF NOT EXISTS idx_dp_listing ON data_purchases(listing_id);
"""

_schema_ready = False
_COMMISSION_RATE = 0.10  # 10%
_MAX_LISTINGS_PER_AGENT = 20
_MAX_DATA_SIZE = 500000  # 500KB
_PREVIEW_ROWS = 10


async def _ensure_schema():
    global _schema_ready
    if _schema_ready:
        return
    try:
        from core.database import db
        await db.raw_executescript(_SCHEMA)
        _schema_ready = True
    except Exception as e:
        logger.error("[DataMkt] Schema init error: %s", e)


async def _get_agent_id(api_key: str) -> Optional[str]:
    from core.database import db
    row = await db._fetchone(
        "SELECT agent_id FROM agent_permissions WHERE api_key=? AND status='active'",
        (api_key,))
    return row["agent_id"] if row else None


def _validate_key(x_api_key: Optional[str]) -> str:
    if not x_api_key or not x_api_key.startswith("maxia_"):
        raise HTTPException(401, "Missing or invalid X-API-Key header")
    return x_api_key


# ══════════════════════════════════════════
# Models
# ══════════════════════════════════════════

class ListDataRequest(BaseModel):
    name: str = Field(..., min_length=3, max_length=200)
    description: str = Field(..., min_length=10, max_length=1000)
    category: str = Field("general", max_length=50)
    data: list = Field(..., min_length=1, max_length=10000, description="Array of objects (rows)")
    price_usdc: float = Field(..., gt=0, le=500)
    licence: str = Field("unlimited", pattern="^(single_use|unlimited|resale_ok)$")


# ══════════════════════════════════════════
# Endpoints
# ══════════════════════════════════════════

@router.post("/list")
async def list_data(req: ListDataRequest, x_api_key: str = Header(None)):
    """List a dataset for sale. Provide the full data as a JSON array.

    The first 10 rows become the free preview. Buyers pay to access the full dataset.
    MAXIA takes 10% commission on sales.
    """
    api_key = _validate_key(x_api_key)
    await _ensure_schema()
    agent_id = await _get_agent_id(api_key)
    if not agent_id:
        raise HTTPException(403, "Agent not found or inactive")

    from core.database import db
    from core.security import check_content_safety

    safety = check_content_safety(req.name + " " + req.description)
    if not safety.get("safe", True):
        raise HTTPException(400, "Content flagged by safety filter")

    # Limit listings
    count = await db._fetchone(
        "SELECT COUNT(*) as cnt FROM data_listings WHERE seller_agent_id=? AND status='active'",
        (agent_id,))
    if count and count["cnt"] >= _MAX_LISTINGS_PER_AGENT:
        raise HTTPException(400, f"Max {_MAX_LISTINGS_PER_AGENT} active listings")

    full_json = json.dumps(req.data, ensure_ascii=False)
    if len(full_json) > _MAX_DATA_SIZE:
        raise HTTPException(400, f"Data too large ({len(full_json)} bytes, max {_MAX_DATA_SIZE})")

    preview = req.data[:_PREVIEW_ROWS]
    preview_json = json.dumps(preview, ensure_ascii=False)

    listing_id = str(uuid.uuid4())
    now = int(time.time())

    await db.raw_execute(
        "INSERT INTO data_listings(listing_id, seller_agent_id, name, description, "
        "category, preview_json, full_data_json, row_count, price_usdc, licence, "
        "status, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (listing_id, agent_id, req.name, req.description, req.category,
         preview_json, full_json, len(req.data), req.price_usdc, req.licence,
         "active", now, now))

    logger.info("[DataMkt] Agent %s listed: %s (%d rows, $%.2f)",
                agent_id[:8], req.name, len(req.data), req.price_usdc)

    return {
        "status": "ok",
        "listing_id": listing_id,
        "name": req.name,
        "rows": len(req.data),
        "preview_rows": len(preview),
        "price_usdc": req.price_usdc,
        "licence": req.licence,
    }


@router.get("/browse")
async def browse_data(category: str = "", query: str = "", limit: int = 20):
    """Browse datasets for sale. No auth required."""
    await _ensure_schema()
    from core.database import db

    conditions = ["status='active'"]
    params: list = []

    if category:
        conditions.append("category=?")
        params.append(category)

    if query:
        conditions.append("(name LIKE ? OR description LIKE ?)")
        params.extend([f"%{query}%", f"%{query}%"])

    params.append(min(limit, 50))
    where = " AND ".join(conditions)

    rows = await db._fetchall(
        f"SELECT listing_id, seller_agent_id, name, description, category, "
        f"row_count, price_usdc, licence, times_sold, updated_at "
        f"FROM data_listings WHERE {where} "
        f"ORDER BY times_sold DESC, updated_at DESC LIMIT ?",
        tuple(params))

    return {"count": len(rows), "datasets": [dict(r) for r in rows]}


@router.get("/{listing_id}/preview")
async def preview_data(listing_id: str):
    """Free preview of a dataset (first 10 rows). No auth required."""
    await _ensure_schema()
    from core.database import db

    row = await db._fetchone(
        "SELECT name, description, category, preview_json, row_count, price_usdc, licence "
        "FROM data_listings WHERE listing_id=? AND status='active'",
        (listing_id,))
    if not row:
        raise HTTPException(404, "Dataset not found")

    try:
        preview = json.loads(row["preview_json"])
    except (json.JSONDecodeError, TypeError):
        preview = []

    return {
        "name": row["name"],
        "description": row["description"],
        "category": row["category"],
        "total_rows": row["row_count"],
        "preview_rows": len(preview),
        "preview": preview,
        "price_usdc": float(row["price_usdc"]),
        "licence": row["licence"],
        "buy_endpoint": f"POST /api/data/{listing_id}/buy",
    }


@router.post("/{listing_id}/buy")
async def buy_data(listing_id: str, x_api_key: str = Header(None)):
    """Buy a dataset. Full data returned after payment. Deducted from credits."""
    api_key = _validate_key(x_api_key)
    await _ensure_schema()
    buyer_id = await _get_agent_id(api_key)
    if not buyer_id:
        raise HTTPException(403, "Agent not found or inactive")

    from core.database import db

    listing = await db._fetchone(
        "SELECT seller_agent_id, name, full_data_json, row_count, price_usdc, licence "
        "FROM data_listings WHERE listing_id=? AND status='active'",
        (listing_id,))
    if not listing:
        raise HTTPException(404, "Dataset not found")

    if listing["seller_agent_id"] == buyer_id:
        raise HTTPException(400, "Cannot buy your own dataset")

    price = float(listing["price_usdc"])

    # Check if already purchased (single_use licence)
    if listing["licence"] == "single_use":
        existing = await db._fetchone(
            "SELECT purchase_id FROM data_purchases WHERE listing_id=? AND buyer_agent_id=?",
            (listing_id, buyer_id))
        if existing:
            raise HTTPException(409, "Already purchased (single_use licence)")

    # Charge buyer
    from billing.prepaid_credits import deduct_credits, add_credits
    charge = await deduct_credits(buyer_id, price, f"data:buy:{listing['name'][:30]}")
    if not charge.get("success"):
        raise HTTPException(402, f"Insufficient credits. Need ${price:.2f}")

    # Pay seller (minus commission)
    commission = round(price * _COMMISSION_RATE, 6)
    payout = round(price - commission, 6)

    seller_row = await db._fetchone(
        "SELECT wallet FROM agent_permissions WHERE agent_id=?",
        (listing["seller_agent_id"],))
    if seller_row:
        await add_credits(
            listing["seller_agent_id"], seller_row["wallet"], payout,
            payment_tx="data-sale",
            description=f"Data sale: {listing['name'][:30]} to {buyer_id[:8]}")

    # Record purchase
    now = int(time.time())
    await db.raw_execute(
        "INSERT INTO data_purchases(purchase_id, listing_id, buyer_agent_id, "
        "price_usdc, licence, purchased_at) VALUES(?,?,?,?,?,?)",
        (str(uuid.uuid4()), listing_id, buyer_id, price, listing["licence"], now))

    # Update sales count
    await db.raw_execute(
        "UPDATE data_listings SET times_sold = times_sold + 1, "
        "total_revenue_usdc = total_revenue_usdc + ? WHERE listing_id=?",
        (payout, listing_id))

    # Parse full data
    try:
        data = json.loads(listing["full_data_json"])
    except (json.JSONDecodeError, TypeError):
        data = []

    logger.info("[DataMkt] Agent %s bought '%s' ($%.2f, commission $%.2f)",
                buyer_id[:8], listing["name"], price, commission)

    try:
        from infra.alerts import alert_revenue
        await alert_revenue(commission, f"Data sale: {listing['name'][:30]}")
    except Exception:
        pass

    return {
        "status": "ok",
        "name": listing["name"],
        "rows": len(data),
        "data": data,
        "paid_usdc": price,
        "credit_balance": charge.get("balance", 0),
        "licence": listing["licence"],
    }


@router.get("/my-listings")
async def my_listings(x_api_key: str = Header(None)):
    """List datasets the agent has listed for sale."""
    api_key = _validate_key(x_api_key)
    await _ensure_schema()
    agent_id = await _get_agent_id(api_key)
    if not agent_id:
        raise HTTPException(403, "Agent not found or inactive")

    from core.database import db
    rows = await db._fetchall(
        "SELECT listing_id, name, category, row_count, price_usdc, licence, "
        "times_sold, total_revenue_usdc, status, updated_at "
        "FROM data_listings WHERE seller_agent_id=? ORDER BY updated_at DESC",
        (agent_id,))

    return {"count": len(rows), "listings": [dict(r) for r in rows]}
