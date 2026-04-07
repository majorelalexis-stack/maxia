"""MAXIA ONE-56 — Export fiscal CSV par wallet.

Genere un CSV avec toutes les transactions d'un wallet pour une annee fiscale.
Sources: crypto_swaps, escrow_records, dca_executions, grid_trades,
         marketplace_tx, prepaid_transactions, gpu_instances.

Endpoint: GET /api/export/fiscal?wallet=X&year=2026&format=csv
"""
import csv
import io
import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/export", tags=["fiscal-export"])


async def _get_db():
    from core.database import db
    return db


def _ts_to_iso(ts: Optional[int]) -> str:
    """Convert unix timestamp to ISO 8601 date string."""
    if not ts or ts < 1000000000:
        return ""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _year_bounds(year: int) -> tuple[int, int]:
    """Return (start_ts, end_ts) for a given year in UTC."""
    start = int(datetime(year, 1, 1, tzinfo=timezone.utc).timestamp())
    end = int(datetime(year + 1, 1, 1, tzinfo=timezone.utc).timestamp())
    return start, end


async def _fetch_swaps(db, wallet: str, start: int, end: int) -> list[dict]:
    """Fetch crypto swaps (Solana + EVM) for a wallet."""
    try:
        rows = await db.raw_execute_fetchall(
        "SELECT swap_id, from_token, to_token, amount_in, amount_out, "
        "commission, payment_tx, status, created_at "
        "FROM crypto_swaps WHERE buyer_wallet=? AND created_at>=? AND created_at<? "
        "ORDER BY created_at",
        (wallet, start, end),
    )
    except Exception:
        return []
    results = []
    for r in rows:
        results.append({
            "date": _ts_to_iso(r.get("created_at")),
            "type": "swap",
            "description": f"{r.get('from_token', '')} -> {r.get('to_token', '')}",
            "amount_in": r.get("amount_in", 0),
            "token_in": r.get("from_token", ""),
            "amount_out": r.get("amount_out", 0),
            "token_out": r.get("to_token", ""),
            "fee_usdc": r.get("commission", 0),
            "tx_id": r.get("payment_tx") or r.get("swap_id", ""),
            "status": r.get("status", ""),
        })
    return results


async def _fetch_escrows(db, wallet: str, start: int, end: int) -> list[dict]:
    """Fetch escrow records where wallet is buyer or seller."""
    try:
        rows = await db.raw_execute_fetchall(
        "SELECT escrow_id, buyer, seller, status, data, created_at "
        "FROM escrow_records WHERE (buyer=? OR seller=?) AND created_at>=? AND created_at<? "
        "ORDER BY created_at",
        (wallet, wallet, start, end),
    )
    except Exception:
        return []
    results = []
    for r in rows:
        data = {}
        try:
            data = json.loads(r.get("data", "{}"))
        except Exception:
            pass
        role = "buyer" if r.get("buyer") == wallet else "seller"
        amount = data.get("amount_usdc", 0)
        results.append({
            "date": _ts_to_iso(r.get("created_at")),
            "type": f"escrow_{role}",
            "description": f"Escrow {r.get('status', '')} — {data.get('service_id', 'N/A')}",
            "amount_in": amount if role == "seller" else 0,
            "token_in": "USDC" if role == "seller" else "",
            "amount_out": amount if role == "buyer" else 0,
            "token_out": "USDC" if role == "buyer" else "",
            "fee_usdc": data.get("commission", 0),
            "tx_id": r.get("escrow_id", ""),
            "status": r.get("status", ""),
        })
    return results


async def _fetch_dca(db, wallet: str, start: int, end: int) -> list[dict]:
    """Fetch DCA bot executions for a wallet."""
    try:
        rows = await db.raw_execute_fetchall(
            "SELECT e.exec_id, e.order_id, e.price_usdc, e.amount_usdc, "
            "e.received, e.commission_usdc, e.created_at, o.to_token "
            "FROM dca_executions e JOIN dca_orders o ON e.order_id=o.order_id "
            "WHERE o.wallet=? AND e.created_at>=? AND e.created_at<? "
            "ORDER BY e.created_at",
            (wallet, start, end),
        )
    except Exception:
        return []
    results = []
    for r in rows:
        results.append({
            "date": _ts_to_iso(r.get("created_at")),
            "type": "dca_buy",
            "description": f"DCA USDC -> {r.get('to_token', '?')} @ ${r.get('price_usdc', 0):.2f}",
            "amount_in": r.get("received", 0),
            "token_in": r.get("to_token", ""),
            "amount_out": r.get("amount_usdc", 0),
            "token_out": "USDC",
            "fee_usdc": r.get("commission_usdc", 0),
            "tx_id": r.get("exec_id", ""),
            "status": "executed",
        })
    return results


async def _fetch_grid(db, wallet: str, start: int, end: int) -> list[dict]:
    """Fetch grid bot trades for a wallet."""
    try:
        rows = await db.raw_execute_fetchall(
            "SELECT t.trade_id, t.bot_id, t.side, t.price_usdc, t.amount, "
            "t.usdc_value, t.tx_signature, t.created_at, b.token "
            "FROM grid_trades t JOIN grid_bots b ON t.bot_id=b.bot_id "
            "WHERE b.wallet=? AND t.created_at>=? AND t.created_at<? "
            "ORDER BY t.created_at",
            (wallet, start, end),
        )
    except Exception:
        return []
    results = []
    for r in rows:
        side = r.get("side", "BUY")
        token = r.get("token", "?")
        results.append({
            "date": _ts_to_iso(r.get("created_at")),
            "type": f"grid_{side.lower()}",
            "description": f"Grid {side} {token} @ ${r.get('price_usdc', 0):.2f}",
            "amount_in": r.get("amount", 0) if side == "BUY" else r.get("usdc_value", 0),
            "token_in": token if side == "BUY" else "USDC",
            "amount_out": r.get("usdc_value", 0) if side == "BUY" else r.get("amount", 0),
            "token_out": "USDC" if side == "BUY" else token,
            "fee_usdc": 0,
            "tx_id": r.get("tx_signature") or r.get("trade_id", ""),
            "status": "executed",
        })
    return results


async def _fetch_marketplace(db, wallet: str, start: int, end: int) -> list[dict]:
    """Fetch marketplace purchases/sales."""
    try:
        rows = await db.raw_execute_fetchall(
        "SELECT tx_id, buyer, seller, service, price_usdc, commission_usdc, "
        "seller_gets_usdc, created_at "
        "FROM marketplace_tx WHERE (buyer=? OR seller=?) AND created_at>=? AND created_at<? "
        "ORDER BY created_at",
        (wallet, wallet, start, end),
    )
    except Exception:
        return []
    results = []
    for r in rows:
        role = "buyer" if r.get("buyer") == wallet else "seller"
        results.append({
            "date": _ts_to_iso(r.get("created_at")),
            "type": f"marketplace_{role}",
            "description": f"Service: {r.get('service', 'N/A')}",
            "amount_in": r.get("seller_gets_usdc", 0) if role == "seller" else 0,
            "token_in": "USDC" if role == "seller" else "",
            "amount_out": r.get("price_usdc", 0) if role == "buyer" else 0,
            "token_out": "USDC" if role == "buyer" else "",
            "fee_usdc": r.get("commission_usdc", 0),
            "tx_id": r.get("tx_id", ""),
            "status": "completed",
        })
    return results


async def _fetch_gpu(db, wallet: str, start: int, end: int) -> list[dict]:
    """Fetch GPU rental records."""
    try:
        rows = await db.raw_execute_fetchall(
        "SELECT instance_id, gpu_tier, duration_hours, total_cost, commission, "
        "payment_tx, status, created_at "
        "FROM gpu_instances WHERE agent_wallet=? AND created_at>=? AND created_at<? "
        "ORDER BY created_at",
        (wallet, start, end),
    )
    except Exception:
        return []
    results = []
    for r in rows:
        results.append({
            "date": _ts_to_iso(r.get("created_at")),
            "type": "gpu_rental",
            "description": f"GPU {r.get('gpu_tier', '?')} — {r.get('duration_hours', 0)}h",
            "amount_in": 0,
            "token_in": "",
            "amount_out": r.get("total_cost", 0),
            "token_out": "USDC",
            "fee_usdc": r.get("commission", 0),
            "tx_id": r.get("payment_tx") or r.get("instance_id", ""),
            "status": r.get("status", ""),
        })
    return results


def _build_csv(transactions: list[dict], wallet: str, year: int) -> str:
    """Build CSV string from transaction list."""
    output = io.StringIO()
    writer = csv.writer(output)
    # Header
    writer.writerow([
        "Date (UTC)", "Type", "Description",
        "Amount In", "Token In", "Amount Out", "Token Out",
        "Fee (USDC)", "Transaction ID", "Status",
    ])
    # Summary header
    writer.writerow([])
    writer.writerow([f"MAXIA Fiscal Export — Wallet: {wallet} — Year: {year}"])
    writer.writerow([f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"])
    writer.writerow([f"Total transactions: {len(transactions)}"])
    writer.writerow([])
    # Column headers
    writer.writerow([
        "Date (UTC)", "Type", "Description",
        "Amount In", "Token In", "Amount Out", "Token Out",
        "Fee (USDC)", "Transaction ID", "Status",
    ])
    # Data rows
    total_fees = 0.0
    for tx in transactions:
        fee = float(tx.get("fee_usdc", 0) or 0)
        total_fees += fee
        writer.writerow([
            tx.get("date", ""),
            tx.get("type", ""),
            tx.get("description", ""),
            tx.get("amount_in", ""),
            tx.get("token_in", ""),
            tx.get("amount_out", ""),
            tx.get("token_out", ""),
            f"{fee:.6f}" if fee else "",
            tx.get("tx_id", ""),
            tx.get("status", ""),
        ])
    # Summary footer
    writer.writerow([])
    writer.writerow(["SUMMARY"])
    writer.writerow(["Total transactions", len(transactions)])
    writer.writerow(["Total fees paid (USDC)", f"{total_fees:.6f}"])
    # Breakdown by type
    type_counts: dict[str, int] = {}
    for tx in transactions:
        t = tx.get("type", "unknown")
        type_counts[t] = type_counts.get(t, 0) + 1
    for t, count in sorted(type_counts.items()):
        writer.writerow([f"  {t}", count])
    return output.getvalue()


# ── Endpoint ──

@router.get("/fiscal")
async def export_fiscal(
    wallet: str = Query(..., min_length=10, description="Wallet address"),
    year: int = Query(2026, ge=2020, le=2030, description="Fiscal year"),
):
    """Export all transactions for a wallet as CSV for tax reporting.

    Includes: swaps, escrow, DCA, grid, marketplace, GPU rentals.
    All amounts in USDC at time of transaction.
    """
    from core.security import validate_wallet_address
    if not validate_wallet_address(wallet):
        raise HTTPException(400, "Invalid wallet address")

    db = await _get_db()
    start, end = _year_bounds(year)

    # Fetch all sources in parallel (graceful if tables don't exist yet)
    import asyncio
    swap_task = asyncio.create_task(_fetch_swaps(db, wallet, start, end))
    escrow_task = asyncio.create_task(_fetch_escrows(db, wallet, start, end))
    dca_task = asyncio.create_task(_fetch_dca(db, wallet, start, end))
    grid_task = asyncio.create_task(_fetch_grid(db, wallet, start, end))
    market_task = asyncio.create_task(_fetch_marketplace(db, wallet, start, end))
    gpu_task = asyncio.create_task(_fetch_gpu(db, wallet, start, end))

    swaps, escrows, dcas, grids, markets, gpus = await asyncio.gather(
        swap_task, escrow_task, dca_task, grid_task, market_task, gpu_task,
    )

    # Merge and sort by date
    all_tx = swaps + escrows + dcas + grids + markets + gpus
    all_tx.sort(key=lambda x: x.get("date", ""))

    if not all_tx:
        return {
            "wallet": wallet,
            "year": year,
            "transactions": 0,
            "message": "No transactions found for this wallet and year.",
        }

    csv_content = _build_csv(all_tx, wallet, year)

    filename = f"maxia_fiscal_{wallet[:8]}_{year}.csv"
    return StreamingResponse(
        io.BytesIO(csv_content.encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/fiscal/summary")
async def fiscal_summary(
    wallet: str = Query(..., min_length=10, description="Wallet address"),
    year: int = Query(2026, ge=2020, le=2030, description="Fiscal year"),
):
    """Get a summary of all transactions for a wallet (no CSV download).

    Returns counts and totals by type.
    """
    from core.security import validate_wallet_address
    if not validate_wallet_address(wallet):
        raise HTTPException(400, "Invalid wallet address")

    db = await _get_db()
    start, end = _year_bounds(year)

    import asyncio
    swaps, escrows, dcas, grids, markets, gpus = await asyncio.gather(
        _fetch_swaps(db, wallet, start, end),
        _fetch_escrows(db, wallet, start, end),
        _fetch_dca(db, wallet, start, end),
        _fetch_grid(db, wallet, start, end),
        _fetch_marketplace(db, wallet, start, end),
        _fetch_gpu(db, wallet, start, end),
    )

    all_tx = swaps + escrows + dcas + grids + markets + gpus
    total_fees = sum(float(tx.get("fee_usdc", 0) or 0) for tx in all_tx)

    breakdown = {}
    for tx in all_tx:
        t = tx.get("type", "unknown")
        breakdown[t] = breakdown.get(t, 0) + 1

    return {
        "wallet": wallet,
        "year": year,
        "total_transactions": len(all_tx),
        "total_fees_usdc": round(total_fees, 6),
        "breakdown": breakdown,
        "sources": {
            "swaps": len(swaps),
            "escrow": len(escrows),
            "dca": len(dcas),
            "grid": len(grids),
            "marketplace": len(markets),
            "gpu": len(gpus),
        },
        "download_url": f"/api/export/fiscal?wallet={wallet}&year={year}",
    }
