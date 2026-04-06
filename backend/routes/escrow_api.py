"""MAXIA Escrow routes — extracted from main.py."""
from fastapi import APIRouter, HTTPException, Depends, Request, Header
from core.error_utils import safe_error

router = APIRouter(prefix="/api/escrow", tags=["escrow"])


# ── Deferred imports (resolved at call time to avoid circular deps) ──

def _get_escrow_client():
    from blockchain.escrow_client import escrow_client
    return escrow_client


async def _require_wallet(
    authorization: str = Header(None, alias="Authorization"),
) -> str:
    """Auth for escrow: requires Bearer session token.
    On-chain verification provides additional security."""
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1]
        from core.auth import verify_session_token
        return verify_session_token(token)
    raise HTTPException(401, "Authentication required: Bearer token needed")


# ══════════════════════════════════════════════════════════
#  V11: ESCROW ON-CHAIN (Art.21)
# ══════════════════════════════════════════════════════════

@router.get("/info")
async def escrow_public_info():
    """Escrow public info — Solana + Base contracts, stats. No auth."""
    from core.config import ESCROW_PROGRAM_ID, ESCROW_CONTRACT_BASE, ESCROW_ADDRESS
    escrow_client = _get_escrow_client()
    stats = escrow_client.get_stats()

    # Base escrow stats (on-chain)
    base_stats = {}
    try:
        from blockchain.base_escrow_client import get_stats as base_get_stats, get_contract_info
        base_stats = await base_get_stats()
    except Exception:
        pass

    return {
        "solana": {
            "program_id": ESCROW_PROGRAM_ID,
            "escrow_wallet": ESCROW_ADDRESS,
            "explorer": f"https://solscan.io/account/{ESCROW_PROGRAM_ID}",
            "network": "mainnet-beta",
            "active_escrows": stats.get("active", 0) if isinstance(stats, dict) else 0,
            "total_escrows": stats.get("total", 0) if isinstance(stats, dict) else 0,
        },
        "base": {
            "contract": ESCROW_CONTRACT_BASE,
            "explorer": f"https://basescan.org/address/{ESCROW_CONTRACT_BASE}",
            "network": "base-mainnet",
            "total_escrows": base_stats.get("total_escrows", 0),
            "total_volume_usdc": base_stats.get("total_volume_usdc", 0),
            "total_commissions_usdc": base_stats.get("total_commissions_usdc", 0),
        },
        "chains": ["solana", "base"],
        "escrow_enabled": True,
    }

@router.get("/base/stats")
async def escrow_base_stats():
    """Base escrow on-chain stats. Public endpoint."""
    from blockchain.base_escrow_client import get_stats
    return await get_stats()


@router.get("/base/contract")
async def escrow_base_contract():
    """Base escrow contract info — address, ABI, explorer link."""
    from blockchain.base_escrow_client import get_contract_info
    return get_contract_info()


@router.post("/base/verify")
async def escrow_base_verify(req: dict):
    """Verify a Base escrow transaction by tx hash."""
    tx_hash = req.get("tx_hash", "")
    if not tx_hash or not tx_hash.startswith("0x"):
        raise HTTPException(400, "tx_hash required (0x...)")
    from blockchain.base_escrow_client import verify_escrow_tx
    return await verify_escrow_tx(tx_hash)


@router.get("/base/{escrow_id}")
async def escrow_base_get(escrow_id: str):
    """Get Base escrow details by ID."""
    if not escrow_id.startswith("0x"):
        escrow_id = "0x" + escrow_id
    from blockchain.base_escrow_client import get_escrow
    return await get_escrow(escrow_id)


@router.get("/stats")
async def escrow_stats(request: Request):
    """Escrow stats detailles. Admin only (contient wallet addresses)."""
    from core.security import require_admin
    require_admin(request)
    escrow_client = _get_escrow_client()
    return escrow_client.get_stats()

@router.get("/list")
async def list_escrows(wallet: str = Depends(_require_wallet)):
    """List escrows for the authenticated wallet (as buyer or seller). Reads from DB."""
    import json as _json
    try:
        from core.database import db
        rows = await db.raw_execute_fetchall(
            "SELECT escrow_id, buyer, seller, status, data, created_at "
            "FROM escrow_records WHERE buyer=? OR seller=? ORDER BY created_at DESC",
            (wallet, wallet))
        escrows = []
        for r in rows:
            e = dict(r)
            db_status = e.get("status", "unknown")
            db_created = e.get("created_at")
            if e.get("data"):
                try:
                    e.update(_json.loads(e["data"]))
                except Exception:
                    pass
            # DB columns are source of truth — override JSON
            e["status"] = db_status
            e["escrowId"] = e.get("escrowId", e.get("escrow_id", ""))
            # Fix timeout display
            timeout_at = e.get("timeoutAt", 0)
            if timeout_at and db_created:
                e["timeout_hours"] = max(1, int((timeout_at - db_created) / 3600))
            if db_created and db_created > 1000000000:
                import datetime
                e["created_at"] = datetime.datetime.fromtimestamp(db_created).isoformat()
            del e["data"]
            escrows.append(e)
        return {"escrows": escrows, "count": len(escrows), "wallet": wallet}
    except Exception:
        # Fallback to in-memory
        escrow_client = _get_escrow_client()
        all_escrows = escrow_client._escrows
        user_escrows = [e for e in all_escrows.values()
                        if e.get("buyer") == wallet or e.get("seller") == wallet]
        return {"escrows": user_escrows, "count": len(user_escrows), "wallet": wallet}

@router.get("/{escrow_id}")
async def get_escrow_by_id(escrow_id: str, wallet: str = Depends(_require_wallet)):
    """Get escrow details. Auth required (only buyer/seller can view)."""
    escrow_client = _get_escrow_client()
    data = escrow_client.get_escrow(escrow_id)
    if data.get("error"):
        raise HTTPException(404, data["error"])
    if wallet not in (data.get("buyer", ""), data.get("seller", "")):
        raise HTTPException(403, "Not authorized to view this escrow")
    return data

@router.post("/create")
async def create_escrow(req: dict, wallet: str = Depends(_require_wallet)):
    from core.security import require_ofac_clear
    require_ofac_clear(wallet, "buyer_wallet")
    escrow_client = _get_escrow_client()
    # #6: Validate timeout_hours at API level
    timeout = int(req.get("timeout_hours", 72))
    if timeout < 1 or timeout > 168:
        raise HTTPException(400, "timeout_hours must be 1-168")
    tx_signature = req.get("tx_signature", "")
    if not tx_signature:
        raise HTTPException(400, "tx_signature required — send USDC to escrow wallet first")
    return await escrow_client.create_escrow(
        buyer_wallet=wallet,
        seller_wallet=req.get("seller_wallet", ""),
        amount_usdc=float(req.get("amount_usdc", 0)),
        service_id=req.get("service_id", ""),
        tx_signature=tx_signature,
        timeout_hours=timeout,
    )

@router.post("/confirm")
async def confirm_escrow(req: dict, wallet: str = Depends(_require_wallet)):
    escrow_client = _get_escrow_client()
    return await escrow_client.confirm_delivery(
        escrow_id=req.get("escrow_id", ""),
        buyer_wallet=wallet,
    )

@router.post("/reclaim")
async def reclaim_escrow(req: dict, wallet: str = Depends(_require_wallet)):
    escrow_client = _get_escrow_client()
    return await escrow_client.reclaim_timeout(
        escrow_id=req.get("escrow_id", ""),
        buyer_wallet=wallet,
    )

@router.post("/resolve")
async def resolve_escrow_dispute(req: dict, request: Request):
    # #1 / #4 / #7: Admin-only endpoint for dispute resolution
    from core.security import require_admin
    require_admin(request)
    escrow_client = _get_escrow_client()
    return await escrow_client.resolve_dispute(
        escrow_id=req.get("escrow_id", ""),
        release_to_seller=req.get("release_to_seller", False),
    )
