"""MAXIA — Compliance routes (GDPR data erasure, jurisdiction declaration).

PRO-F Phase — Legal compliance endpoints.
"""
import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from core.auth import require_auth
from core.error_utils import safe_error

logger = logging.getLogger(__name__)
router = APIRouter(tags=["compliance"])


# ══════════════════════════════════════════
#  F-C4: Jurisdiction Declaration
# ══════════════════════════════════════════


@router.post("/api/compliance/jurisdiction")
async def declare_jurisdiction(req: dict, request: Request) -> dict[str, Any]:
    """Record user's jurisdiction declaration (not US, not sanctioned).

    Required at registration or first trade. Stores consent with timestamp + IP.
    """
    from core.database import db

    wallet = req.get("wallet", "").strip()
    if not wallet or len(wallet) < 20:
        raise HTTPException(400, "wallet address required")

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
        "message": "Jurisdiction declaration recorded. You may proceed."
    }


@router.get("/api/compliance/jurisdiction/{wallet}")
async def check_jurisdiction(wallet: str) -> dict[str, Any]:
    """Check if a wallet has declared jurisdiction compliance."""
    from core.database import db

    if not wallet or len(wallet) < 20:
        raise HTTPException(400, "valid wallet address required")

    try:
        row = await db._fetchone(
            "SELECT declared_at, ip_address FROM jurisdiction_declarations "
            "WHERE wallet = ? ORDER BY declared_at DESC LIMIT 1",
            (wallet,),
        )
    except Exception:
        return {"declared": False, "wallet": wallet}

    if not row:
        return {"declared": False, "wallet": wallet}

    return {
        "declared": True,
        "wallet": wallet,
        "declared_at": row["declared_at"] if isinstance(row, dict) else row[0],
    }


# ══════════════════════════════════════════
#  F-C5: GDPR Data Erasure (Right to Erasure, Art.17)
# ══════════════════════════════════════════


@router.delete("/api/user/data")
async def gdpr_data_erasure(request: Request) -> dict[str, Any]:
    """GDPR Art.17 — Right to erasure.

    Deletes: profile, API keys, preferences, agent messages, skills, memory.
    Retains (AML obligation, 5 years): transactions, OFAC screenings, escrow records.
    Returns a timestamped receipt.
    """
    auth_info = await require_auth(request)
    api_key: str = auth_info.get("api_key", "")
    wallet: str = auth_info.get("wallet", "")

    if not api_key:
        raise HTTPException(401, "Authentication required for data erasure")

    from core.database import db
    ts = int(time.time())
    deleted_tables: list[str] = []
    retained_tables: list[str] = []

    # ── DELETE: user-controlled data ──
    deletable = [
        ("agents", "api_key", api_key),
        ("agent_skills", "agent_api_key", api_key),
        ("agent_memory", "agent_api_key", api_key),
        ("agent_messages", "from_agent", api_key),
        ("agent_messages", "to_agent", api_key),
        ("pool_entries", "contributor_api_key", api_key),
        ("skill_listings", "seller_api_key", api_key),
        ("data_listings", "seller_api_key", api_key),
        ("agent_children", "parent_api_key", api_key),
    ]

    for table, col, val in deletable:
        try:
            await db.raw_execute(
                f"DELETE FROM {table} WHERE {col} = ?", (val,)
            )
            deleted_tables.append(table)
        except Exception as e:
            # Table might not exist — skip silently
            logger.debug("[GDPR] Skip table %s: %s", table, e)

    # Also delete by wallet if available
    if wallet:
        wallet_deletable = [
            ("agents", "wallet"),
            ("referrals", "referrer_wallet"),
        ]
        for table, col in wallet_deletable:
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
        "[GDPR] Data erasure completed: api_key=%s wallet=%s deleted=%s",
        api_key[:8] + "...", wallet[:8] + "..." if wallet else "none",
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
async def gdpr_data_export(request: Request) -> dict[str, Any]:
    """GDPR Art.20 — Right to data portability.

    Returns all personal data in structured JSON format.
    """
    auth_info = await require_auth(request)
    api_key: str = auth_info.get("api_key", "")
    wallet: str = auth_info.get("wallet", "")

    if not api_key:
        raise HTTPException(401, "Authentication required for data export")

    from core.database import db
    export: dict[str, Any] = {"export_timestamp": int(time.time()), "format": "JSON"}

    # Agent profile
    try:
        row = await db._fetchone(
            "SELECT * FROM agents WHERE api_key = ?", (api_key,)
        )
        export["profile"] = dict(row) if row and isinstance(row, dict) else (
            {"data": list(row)} if row else None
        )
    except Exception:
        export["profile"] = None

    # Skills
    try:
        rows = await db._fetchall(
            "SELECT * FROM agent_skills WHERE agent_api_key = ?", (api_key,)
        )
        export["skills"] = [dict(r) if isinstance(r, dict) else list(r) for r in rows]
    except Exception:
        export["skills"] = []

    # Messages
    try:
        rows = await db._fetchall(
            "SELECT * FROM agent_messages WHERE from_agent = ? OR to_agent = ? "
            "ORDER BY created_at DESC LIMIT 1000",
            (api_key, api_key),
        )
        export["messages"] = [dict(r) if isinstance(r, dict) else list(r) for r in rows]
    except Exception:
        export["messages"] = []

    # Memory
    try:
        rows = await db._fetchall(
            "SELECT * FROM agent_memory WHERE agent_api_key = ?", (api_key,)
        )
        export["memory"] = [dict(r) if isinstance(r, dict) else list(r) for r in rows]
    except Exception:
        export["memory"] = []

    # Transaction count (not full data — too sensitive for unauthenticated export)
    if wallet:
        try:
            rows = await db._fetchall(
                "SELECT COUNT(*) as cnt FROM transactions WHERE buyer_wallet = ? OR seller_wallet = ?",
                (wallet, wallet),
            )
            export["transaction_count"] = rows[0]["cnt"] if rows and isinstance(rows[0], dict) else (
                rows[0][0] if rows else 0
            )
        except Exception:
            export["transaction_count"] = 0

    export["note"] = (
        "This export contains your personal data as stored by MAXIA. "
        "Transaction details are available via GET /api/export/fiscal?wallet=YOUR_WALLET. "
        "On-chain data is publicly available on respective block explorers."
    )

    return export
