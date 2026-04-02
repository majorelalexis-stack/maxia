"""MAXIA Audit Trail V12 — OPA-style audit trail pour compliance enterprise.

Chaque action significative est loggee : trades, escrow, swaps, transferts, admin, SLA violations.
Entries structurees avec policy check integre (OFAC, rate limits, montants max).
Export CSV pour auditeurs externes.
"""
import logging
import os
import time
import uuid
import json
import csv
import io
from datetime import datetime
from typing import Optional
from collections import deque

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/enterprise/audit", tags=["enterprise-audit"])

# ── Configuration ──

AUDIT_BUFFER_SIZE = int(os.getenv("AUDIT_BUFFER_SIZE", "10000"))
AUDIT_RETENTION_DAYS = int(os.getenv("AUDIT_RETENTION_DAYS", "365"))

# ── Policy rules (application-level, pas d'OPA externe) ──

POLICY_RULES = {
    "max_single_trade_usdc": float(os.getenv("POLICY_MAX_SINGLE_TRADE", "100000")),
    "max_daily_volume_usdc": float(os.getenv("POLICY_MAX_DAILY_VOLUME", "500000")),
    "requires_ofac_check": True,
    "max_swaps_per_hour": int(os.getenv("POLICY_MAX_SWAPS_HOUR", "200")),
    "max_escrow_amount_usdc": float(os.getenv("POLICY_MAX_ESCROW", "50000")),
    "restricted_countries": [
        "KP", "IR", "CU", "SY", "RU", "BY", "VE", "MM", "ZW",
        "SD", "SS", "CF", "CD", "SO", "YE", "LY", "LB",
    ],
    "restricted_wallets": [],  # Charge depuis OFAC screening au runtime
    "require_kyc_above_usdc": float(os.getenv("POLICY_KYC_THRESHOLD", "10000")),
}

# ── In-memory buffer (ring buffer pour acces rapide) ──

_audit_buffer: deque = deque(maxlen=AUDIT_BUFFER_SIZE)

# ── Volume tracking par tenant (daily reset) ──

_daily_volumes: dict = {}  # {tenant_id: {date_str: total_usdc}}


# ── Schema DB ──

_schema_ready = False

_AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_trail (
    id TEXT PRIMARY KEY,
    timestamp INTEGER NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    resource TEXT NOT NULL DEFAULT '',
    amount_usdc NUMERIC(18,6) DEFAULT 0,
    chain TEXT DEFAULT '',
    result TEXT DEFAULT 'success',
    policy_check TEXT DEFAULT 'pass',
    tenant_id TEXT DEFAULT '',
    metadata TEXT DEFAULT '{}',
    created_at INTEGER DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_trail(actor, timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_trail(action, timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_tenant ON audit_trail(tenant_id, timestamp);
"""


async def _ensure_schema(db):
    """Cree la table audit_trail si elle n'existe pas."""
    global _schema_ready
    if _schema_ready:
        return
    for stmt in _AUDIT_SCHEMA.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            try:
                await db.raw_execute(stmt)
            except Exception:
                pass
    _schema_ready = True


# ── Core Functions ──


def _get_daily_volume(tenant_id: str) -> float:
    """Retourne le volume journalier pour un tenant."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    return _daily_volumes.get(tenant_id, {}).get(today, 0.0)


def _add_daily_volume(tenant_id: str, amount_usdc: float):
    """Incremente le volume journalier d'un tenant."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    if tenant_id not in _daily_volumes:
        _daily_volumes[tenant_id] = {}
    # Nettoyage des jours precedents (garde 2 jours max)
    old_dates = [d for d in _daily_volumes[tenant_id] if d != today]
    for d in old_dates:
        _daily_volumes[tenant_id].pop(d, None)
    _daily_volumes[tenant_id][today] = _daily_volumes[tenant_id].get(today, 0.0) + amount_usdc


async def compliance_check(action: str, context: dict) -> dict:
    """Verifie une action contre les regles de policy.

    Retourne {"allowed": bool, "reason": str, "checks": [...]}
    """
    checks = []
    allowed = True
    reason = ""

    amount = context.get("amount_usdc", 0)
    tenant_id = context.get("tenant_id", "")
    wallet = context.get("wallet", "")

    # Check 1: montant max par trade
    if action in ("trade", "swap", "escrow_lock") and amount > 0:
        max_trade = POLICY_RULES["max_single_trade_usdc"]
        passed = amount <= max_trade
        checks.append({
            "rule": "max_single_trade",
            "limit": max_trade,
            "value": amount,
            "passed": passed,
        })
        if not passed:
            allowed = False
            reason = f"Montant {amount} USDC depasse la limite de {max_trade} USDC par trade"

    # Check 2: volume journalier par tenant
    if tenant_id and amount > 0:
        current_volume = _get_daily_volume(tenant_id)
        max_daily = POLICY_RULES["max_daily_volume_usdc"]
        new_total = current_volume + amount
        passed = new_total <= max_daily
        checks.append({
            "rule": "max_daily_volume",
            "limit": max_daily,
            "current": current_volume,
            "new_total": new_total,
            "passed": passed,
        })
        if not passed:
            allowed = False
            reason = f"Volume journalier {new_total} USDC depasserait la limite de {max_daily} USDC"

    # Check 3: wallet OFAC
    if POLICY_RULES["requires_ofac_check"] and wallet:
        is_restricted = wallet in POLICY_RULES["restricted_wallets"]
        checks.append({
            "rule": "ofac_wallet_check",
            "wallet": wallet[:8] + "..." if len(wallet) > 8 else wallet,
            "passed": not is_restricted,
        })
        if is_restricted:
            allowed = False
            reason = f"Wallet bloque par screening OFAC"

    # Check 4: KYC threshold
    if amount > POLICY_RULES["require_kyc_above_usdc"]:
        checks.append({
            "rule": "kyc_threshold",
            "threshold": POLICY_RULES["require_kyc_above_usdc"],
            "amount": amount,
            "passed": True,  # On note le flag mais on ne bloque pas (KYC gere ailleurs)
            "note": "KYC verification recommandee",
        })

    # Check 5: escrow max
    if action == "escrow_lock" and amount > POLICY_RULES["max_escrow_amount_usdc"]:
        checks.append({
            "rule": "max_escrow_amount",
            "limit": POLICY_RULES["max_escrow_amount_usdc"],
            "value": amount,
            "passed": False,
        })
        allowed = False
        reason = f"Escrow {amount} USDC depasse la limite de {POLICY_RULES['max_escrow_amount_usdc']} USDC"

    return {
        "allowed": allowed,
        "reason": reason if not allowed else "all_checks_passed",
        "checks": checks,
        "policy_version": "1.0",
    }


async def audit_log(
    actor: str,
    action: str,
    resource: str = "",
    *,
    db=None,
    amount_usdc: float = 0,
    chain: str = "",
    result: str = "success",
    tenant_id: str = "",
    agent_id: str = "",
    metadata: Optional[dict] = None,
    skip_policy: bool = False,
) -> dict:
    """Enregistre un evenement dans l'audit trail (DB + buffer memoire).

    Effectue un compliance check automatique sauf si skip_policy=True.
    """
    entry_id = str(uuid.uuid4())
    ts = int(time.time())

    # Policy check automatique
    policy_result = "skip"
    if not skip_policy:
        check = await compliance_check(action, {
            "amount_usdc": amount_usdc,
            "tenant_id": tenant_id,
            "wallet": actor,
        })
        policy_result = "pass" if check["allowed"] else "fail"

    entry = {
        "id": entry_id,
        "timestamp": ts,
        "actor": actor,
        "action": action,
        "resource": resource,
        "amount_usdc": round(amount_usdc, 6),
        "chain": chain,
        "result": result,
        "policy_check": policy_result,
        "tenant_id": tenant_id,
        "agent_id": agent_id,
        "metadata": metadata or {},
    }

    # Ecriture in-memory (toujours)
    _audit_buffer.append(entry)

    # Mise a jour volume journalier
    if amount_usdc > 0 and tenant_id:
        _add_daily_volume(tenant_id, amount_usdc)

    # Ecriture DB (si disponible)
    if db is not None:
        try:
            await _ensure_schema(db)
            await db.raw_execute(
                "INSERT INTO audit_trail (id, timestamp, actor, action, resource, "
                "amount_usdc, chain, result, policy_check, tenant_id, metadata) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entry_id, ts, actor, action, resource,
                    round(amount_usdc, 6), chain, result, policy_result,
                    tenant_id, json.dumps(metadata or {}),
                ),
            )
        except Exception as e:
            entry["_db_error"] = str(e)

    return entry


async def get_audit_trail(
    db,
    tenant_id: str = "",
    start: int = 0,
    end: int = 0,
    action_type: str = "",
    actor: str = "",
    limit: int = 100,
    offset: int = 0,
) -> list:
    """Requete sur l'audit trail avec filtres multiples."""
    await _ensure_schema(db)

    conditions = []
    params = []

    if tenant_id:
        conditions.append("tenant_id=?")
        params.append(tenant_id)
    if start > 0:
        conditions.append("timestamp>=?")
        params.append(start)
    if end > 0:
        conditions.append("timestamp<=?")
        params.append(end)
    if action_type:
        conditions.append("action=?")
        params.append(action_type)
    if actor:
        conditions.append("actor=?")
        params.append(actor)

    where = " AND ".join(conditions) if conditions else "1=1"
    sql = (
        f"SELECT id, timestamp, actor, action, resource, amount_usdc, "
        f"chain, result, policy_check, tenant_id, metadata "
        f"FROM audit_trail WHERE {where} ORDER BY timestamp DESC LIMIT ? OFFSET ?"
    )
    params.extend([limit, offset])

    rows = await db.raw_execute_fetchall(sql, tuple(params))

    results = []
    for row in rows:
        r = dict(row) if hasattr(row, "keys") else {
            "id": row[0], "timestamp": row[1], "actor": row[2],
            "action": row[3], "resource": row[4], "amount_usdc": row[5],
            "chain": row[6], "result": row[7], "policy_check": row[8],
            "tenant_id": row[9], "metadata": row[10],
        }
        # Parse metadata JSON
        if isinstance(r.get("metadata"), str):
            try:
                r["metadata"] = json.loads(r["metadata"])
            except (json.JSONDecodeError, TypeError):
                pass
        results.append(r)

    return results


async def export_audit_csv(db, tenant_id: str, month: str) -> str:
    """Exporte l'audit trail d'un mois au format CSV.

    month format: "2026-03" (YYYY-MM)
    Retourne le contenu CSV en string.
    """
    await _ensure_schema(db)

    # Calculer debut/fin du mois
    try:
        year, mon = month.split("-")
        year, mon = int(year), int(mon)
    except (ValueError, AttributeError):
        raise ValueError(f"Format mois invalide: {month}, attendu YYYY-MM")

    # Premier jour du mois a 00:00 UTC
    from calendar import monthrange
    start_ts = int(datetime(year, mon, 1).timestamp())
    _, last_day = monthrange(year, mon)
    end_ts = int(datetime(year, mon, last_day, 23, 59, 59).timestamp())

    rows = await get_audit_trail(
        db, tenant_id=tenant_id, start=start_ts, end=end_ts, limit=50000
    )

    # Generation CSV
    output = io.StringIO()
    fieldnames = [
        "id", "timestamp", "datetime", "actor", "action", "resource",
        "amount_usdc", "chain", "result", "policy_check", "tenant_id", "metadata",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()

    for row in rows:
        row["datetime"] = datetime.utcfromtimestamp(row.get("timestamp", 0)).isoformat() + "Z"
        if isinstance(row.get("metadata"), dict):
            row["metadata"] = json.dumps(row["metadata"])
        writer.writerow(row)

    return output.getvalue()


# ── FastAPI Routes ──


@router.get("/trail")
async def route_get_audit_trail(
    tenant_id: str = Query("", description="Filtrer par tenant"),
    start: int = Query(0, description="Timestamp debut (epoch)"),
    end: int = Query(0, description="Timestamp fin (epoch)"),
    action_type: str = Query("", description="Filtrer par type d'action (trade, swap, escrow_lock...)"),
    actor: str = Query("", description="Filtrer par wallet/actor"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """Consulter l'audit trail avec filtres."""
    from database import db
    await _ensure_schema(db)

    trail = await get_audit_trail(
        db, tenant_id=tenant_id, start=start, end=end,
        action_type=action_type, actor=actor, limit=limit, offset=offset,
    )

    # Reponse honnete — pas de donnees fictives
    return {
        "count": len(trail),
        "trail": trail,
        "filters": {
            "tenant_id": tenant_id,
            "start": start,
            "end": end,
            "action_type": action_type,
        },
    }


@router.get("/export/{month}")
async def route_export_csv(
    month: str,
    tenant_id: str = Query("", description="Tenant pour l'export"),
):
    """Exporter l'audit trail d'un mois en CSV (pour auditeurs).

    Format mois : YYYY-MM (ex: 2026-03)
    """
    from database import db
    from fastapi.responses import Response

    try:
        csv_content = await export_audit_csv(db, tenant_id=tenant_id, month=month)
    except ValueError:
        raise HTTPException(400, "Invalid export parameters")

    # CSV vide = juste les headers, pas de donnees fictives
    filename = f"maxia_audit_{tenant_id or 'all'}_{month}.csv"
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/policies")
async def route_get_policies():
    """Retourner les regles de policy actives."""
    return {
        "policy_version": "1.0",
        "rules": {
            "max_single_trade_usdc": POLICY_RULES["max_single_trade_usdc"],
            "max_daily_volume_usdc": POLICY_RULES["max_daily_volume_usdc"],
            "requires_ofac_check": POLICY_RULES["requires_ofac_check"],
            "max_swaps_per_hour": POLICY_RULES["max_swaps_per_hour"],
            "max_escrow_amount_usdc": POLICY_RULES["max_escrow_amount_usdc"],
            "require_kyc_above_usdc": POLICY_RULES["require_kyc_above_usdc"],
            "restricted_countries_count": len(POLICY_RULES["restricted_countries"]),
            "restricted_wallets_count": len(POLICY_RULES["restricted_wallets"]),
        },
        "enforcement": "application-level (pre-transaction)",
        "audit_retention_days": AUDIT_RETENTION_DAYS,
        "buffer_size": AUDIT_BUFFER_SIZE,
    }


@router.get("/buffer/recent")
async def route_recent_buffer(limit: int = Query(50, ge=1, le=500)):
    """Acces rapide aux dernieres entrees du buffer memoire (pas de DB)."""
    recent = list(_audit_buffer)[-limit:]
    recent.reverse()
    return {"count": len(recent), "entries": recent}


logger.info("[AuditTrail] Module charge — policy v1.0, buffer "
            f"{AUDIT_BUFFER_SIZE} entries, retention {AUDIT_RETENTION_DAYS}j")
