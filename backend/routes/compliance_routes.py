"""MAXIA — Compliance routes (GDPR data erasure, jurisdiction declaration).

PRO-F Phase — Legal compliance endpoints.
Audit fixes: C1 (auth), C2 (SQL safe), H3 (wallet oracle), H13 (column names), M2 (unauth jurisdiction).
"""
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from core.auth import require_auth_flexible
from core.error_utils import safe_error

logger = logging.getLogger(__name__)
router = APIRouter(tags=["compliance"])

# Whitelist of tables/columns allowed for GDPR deletion (C2 fix: no dynamic SQL)
_GDPR_DELETABLE = {
    # (table, column) — only hardcoded, never from user input
    ("agents", "api_key"),
    ("agent_skills", "agent_id"),
    ("agent_memory", "agent_id"),
    ("agent_messages", "from_agent"),
    ("agent_messages", "to_agent"),
    ("pool_entries", "contributor_agent_id"),
    ("skill_marketplace", "seller_agent_id"),
    ("data_listings", "seller_agent_id"),
    ("agent_children", "parent_agent_id"),
}
_GDPR_WALLET_DELETABLE = {
    ("agents", "wallet"),
    ("referrals", "referrer_wallet"),
}


# ══════════════════════════════════════════
#  F-C4: Jurisdiction Declaration
# ══════════════════════════════════════════


@router.post("/api/compliance/jurisdiction")
async def declare_jurisdiction(
    req: dict,
    request: Request,
    auth: dict = Depends(require_auth_flexible),
) -> dict[str, Any]:
    """Record user's jurisdiction declaration (not US, not sanctioned).

    Requires authentication (M2 fix). Wallet must match authenticated wallet.
    """
    from core.database import db

    wallet = auth.get("wallet", "")
    if not wallet:
        raise HTTPException(401, "Authentication required")

    declaration = req.get("declaration", False)
    if not declaration:
        raise HTTPException(
            400,
            "You must confirm that you are not a resident of the United States "
            "or a sanctioned country to use this platform."
        )

    ip = request.client.host if request.client else "unknown"
    ts = int(time.time())

    try:
        await db.raw_execute(
            "INSERT INTO jurisdiction_declarations "
            "(wallet, ip_address, declared_at, declaration_text) "
            "VALUES (?, ?, ?, ?)",
            (
                wallet,
                ip,
                ts,
                "I confirm I am not a resident or citizen of the United States "
                "or any sanctioned country, and I will not use VPN or proxy to "
                "circumvent geographic restrictions."
            ),
        )
    except Exception as e:
        logger.error("[COMPLIANCE] Jurisdiction declaration error: %s", e)
        raise HTTPException(500, safe_error("jurisdiction declaration"))

    logger.info("[COMPLIANCE] Jurisdiction declared: wallet=%s ip=%s", wallet[:8], ip)
    return {
        "status": "accepted",
        "wallet": wallet,
        "declared_at": ts,
    }


@router.get("/api/compliance/jurisdiction/check")
async def check_jurisdiction(
    auth: dict = Depends(require_auth_flexible),
) -> dict[str, Any]:
    """Check if the authenticated wallet has declared jurisdiction compliance.

    H3 fix: requires auth, no wallet enumeration.
    """
    from core.database import db

    wallet = auth.get("wallet", "")
    if not wallet:
        raise HTTPException(401, "Authentication required")

    try:
        row = await db._fetchone(
            "SELECT declared_at FROM jurisdiction_declarations "
            "WHERE wallet = ? ORDER BY declared_at DESC LIMIT 1",
            (wallet,),
        )
    except Exception:
        return {"declared": False}

    if not row:
        return {"declared": False}

    return {
        "declared": True,
        "declared_at": row["declared_at"] if isinstance(row, dict) else row[0],
    }


# ══════════════════════════════════════════
#  F-C5: GDPR Data Erasure (Right to Erasure, Art.17)
# ══════════════════════════════════════════


@router.delete("/api/user/data")
async def gdpr_data_erasure(
    auth: dict = Depends(require_auth_flexible),
) -> dict[str, Any]:
    """GDPR Art.17 — Right to erasure.

    C1 fix: uses Depends(require_auth_flexible) instead of direct call.
    C2 fix: table/column names validated against whitelist.
    H13 fix: correct column names matching actual schema.
    """
    wallet: str = auth.get("wallet", "")
    api_key: str = auth.get("api_key", "")
    agent_id: str = auth.get("did", "") or api_key

    if not wallet and not api_key:
        raise HTTPException(401, "Authentication required for data erasure")

    from core.database import db
    ts = int(time.time())
    deleted_tables: list[str] = []

    # ── DELETE: user-controlled data (C2 fix: whitelist validated) ──
    identifier = agent_id or api_key
    if identifier:
        for table, col in _GDPR_DELETABLE:
            try:
                await db.raw_execute(
                    f"DELETE FROM {table} WHERE {col} = ?", (identifier,)
                )
                deleted_tables.append(table)
            except Exception as e:
                logger.debug("[GDPR] Skip %s.%s: %s", table, col, e)

    # Also try with api_key directly for legacy tables
    if api_key and api_key != identifier:
        for table, col in [("agents", "api_key")]:
            try:
                await db.raw_execute(
                    f"DELETE FROM {table} WHERE {col} = ?", (api_key,)
                )
                if table not in deleted_tables:
                    deleted_tables.append(table)
            except Exception:
                pass

    # Delete by wallet
    if wallet:
        for table, col in _GDPR_WALLET_DELETABLE:
            try:
                await db.raw_execute(
                    f"DELETE FROM {table} WHERE {col} = ?", (wallet,)
                )
                if table not in deleted_tables:
                    deleted_tables.append(table)
            except Exception:
                pass

    # ── RETAIN: legally required (AML 5 years) ──
    retained_tables = [
        "transactions (AML — 5 years, French Commercial Code Art. L110-4)",
        "escrow_records (AML — 5 years)",
        "ofac_screenings (AML — 5 years)",
        "audit_log (LCEN — 1 year)",
    ]

    logger.info(
        "[GDPR] Data erasure: wallet=%s deleted=%s",
        wallet[:8] + "..." if wallet else "none",
        deleted_tables,
    )

    return {
        "status": "completed",
        "erasure_timestamp": ts,
        "deleted": deleted_tables,
        "retained_reason": retained_tables,
        "note": (
            "Your personal data has been deleted. Transaction records are retained "
            "for 5 years as required by French anti-money laundering law. "
            "On-chain blockchain data cannot be deleted due to immutability. "
            "For questions: support@maxiaworld.app"
        ),
    }


@router.get("/api/user/data/export")
async def gdpr_data_export(
    auth: dict = Depends(require_auth_flexible),
) -> dict[str, Any]:
    """GDPR Art.20 — Right to data portability.

    C1 fix: uses Depends(require_auth_flexible).
    """
    wallet: str = auth.get("wallet", "")
    api_key: str = auth.get("api_key", "")

    if not wallet and not api_key:
        raise HTTPException(401, "Authentication required for data export")

    from core.database import db
    export: dict[str, Any] = {"export_timestamp": int(time.time()), "format": "JSON"}

    # Agent profile
    if api_key:
        try:
            row = await db._fetchone(
                "SELECT * FROM agents WHERE api_key = ?", (api_key,)
            )
            export["profile"] = dict(row) if row and isinstance(row, dict) else None
        except Exception:
            export["profile"] = None

    # Transaction count
    if wallet:
        try:
            rows = await db._fetchall(
                "SELECT COUNT(*) as cnt FROM transactions WHERE buyer_wallet = ? OR seller_wallet = ?",
                (wallet, wallet),
            )
            export["transaction_count"] = rows[0]["cnt"] if rows and isinstance(rows[0], dict) else 0
        except Exception:
            export["transaction_count"] = 0

    export["note"] = (
        "This export contains your personal data as stored by MAXIA. "
        "Transaction details: GET /api/export/fiscal?wallet=YOUR_WALLET. "
        "On-chain data is publicly available on respective block explorers."
    )

    return export
