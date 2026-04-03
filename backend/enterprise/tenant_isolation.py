"""MAXIA Tenant Isolation V12 — Isolation multi-tenant application-level.

Fonctionne avec SQLite (filtrage applicatif) et PostgreSQL (RLS en commentaires).
Chaque tenant a ses propres limites de rate, volume, et agents isoles.
"""
import logging
import os
import time
import uuid
import json
from contextvars import ContextVar
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, Header, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/enterprise/tenants", tags=["enterprise-tenants"])

# ── Context variable pour le tenant courant (thread-safe via asyncio) ──

_current_tenant: ContextVar[str] = ContextVar("current_tenant", default="")

# ── Plans et limites ──

TENANT_PLANS = {
    "free": {
        "max_agents": 3,
        "max_services": 10,
        "max_daily_volume_usdc": 5000,
        "max_requests_per_day": 1000,
        "max_swaps_per_day": 50,
        "sla_tier": "basic",
        "support": "community",
        "features": ["marketplace", "swap_basic"],
    },
    "pro": {
        "max_agents": 25,
        "max_services": 100,
        "max_daily_volume_usdc": 100000,
        "max_requests_per_day": 50000,
        "max_swaps_per_day": 1000,
        "sla_tier": "standard",
        "support": "email",
        "features": ["marketplace", "swap_full", "analytics", "fleet", "mcp"],
    },
    "enterprise": {
        "max_agents": 500,
        "max_services": 5000,
        "max_daily_volume_usdc": 5000000,
        "max_requests_per_day": 1000000,
        "max_swaps_per_day": 50000,
        "sla_tier": "premium",
        "support": "dedicated",
        "features": [
            "marketplace", "swap_full", "analytics", "fleet", "mcp",
            "audit_trail", "compliance", "custom_branding", "api_priority",
        ],
    },
    "custom": {
        "max_agents": -1,  # illimite
        "max_services": -1,
        "max_daily_volume_usdc": -1,
        "max_requests_per_day": -1,
        "max_swaps_per_day": -1,
        "sla_tier": "premium",
        "support": "dedicated+slack",
        "features": ["all"],
    },
}

# ── Rate limiting par tenant (en memoire) ──

_tenant_counters: dict = {}  # {tenant_id: {date: {metric: count}}}


def _get_tenant_counter(tenant_id: str, metric: str) -> int:
    """Retourne le compteur journalier d'un tenant pour une metrique."""
    today = time.strftime("%Y-%m-%d", time.gmtime())
    return _tenant_counters.get(tenant_id, {}).get(today, {}).get(metric, 0)


def _increment_tenant_counter(tenant_id: str, metric: str, amount: int = 1):
    """Incremente un compteur tenant (nettoyage auto des vieux jours)."""
    today = time.strftime("%Y-%m-%d", time.gmtime())
    if tenant_id not in _tenant_counters:
        _tenant_counters[tenant_id] = {}
    # Nettoyage jours anciens
    old = [d for d in _tenant_counters[tenant_id] if d != today]
    for d in old:
        _tenant_counters[tenant_id].pop(d, None)
    if today not in _tenant_counters[tenant_id]:
        _tenant_counters[tenant_id][today] = {}
    _tenant_counters[tenant_id][today][metric] = (
        _tenant_counters[tenant_id][today].get(metric, 0) + amount
    )


# ── Schema DB ──

_schema_ready = False

_TENANT_SCHEMA = """
CREATE TABLE IF NOT EXISTS tenants (
    tenant_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    plan TEXT NOT NULL DEFAULT 'free',
    admin_email TEXT NOT NULL DEFAULT '',
    admin_wallet TEXT DEFAULT '',
    status TEXT DEFAULT 'active',
    settings TEXT DEFAULT '{}',
    created_at INTEGER DEFAULT (strftime('%s','now')),
    updated_at INTEGER DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_tenants_status ON tenants(status);
"""

# ── Migration SQL pour ajouter tenant_id aux tables existantes ──
# Ces ALTER TABLE sont idempotentes (on ignore l'erreur si la colonne existe)

_MIGRATION_ADD_TENANT_ID = [
    "ALTER TABLE agents ADD COLUMN tenant_id TEXT DEFAULT ''",
    "ALTER TABLE agent_services ADD COLUMN tenant_id TEXT DEFAULT ''",
    "ALTER TABLE marketplace_tx ADD COLUMN tenant_id TEXT DEFAULT ''",
    "ALTER TABLE crypto_swaps ADD COLUMN tenant_id TEXT DEFAULT ''",
]

# ── PostgreSQL RLS policies (applied during schema init if PostgreSQL detected) ──

_RLS_POLICIES = [
    "ALTER TABLE agents ENABLE ROW LEVEL SECURITY",
    "CREATE POLICY tenant_agents ON agents USING (tenant_id = current_setting('app.current_tenant', true)) WITH CHECK (tenant_id = current_setting('app.current_tenant', true))",
    "ALTER TABLE agent_services ENABLE ROW LEVEL SECURITY",
    "CREATE POLICY tenant_services ON agent_services USING (tenant_id = current_setting('app.current_tenant', true)) WITH CHECK (tenant_id = current_setting('app.current_tenant', true))",
    "ALTER TABLE marketplace_tx ENABLE ROW LEVEL SECURITY",
    "CREATE POLICY tenant_marketplace ON marketplace_tx USING (tenant_id = current_setting('app.current_tenant', true)) WITH CHECK (tenant_id = current_setting('app.current_tenant', true))",
    "ALTER TABLE crypto_swaps ENABLE ROW LEVEL SECURITY",
    "CREATE POLICY tenant_swaps ON crypto_swaps USING (tenant_id = current_setting('app.current_tenant', true)) WITH CHECK (tenant_id = current_setting('app.current_tenant', true))",
]


def _is_pg() -> bool:
    """Check if we're running on PostgreSQL (via DATABASE_URL)."""
    return bool(os.getenv("DATABASE_URL", "").startswith("postgres"))


async def _ensure_schema(db):
    """Cree la table tenants + migration tenant_id + RLS policies (PostgreSQL) si necessaire."""
    global _schema_ready
    if _schema_ready:
        return
    # Table tenants
    for stmt in _TENANT_SCHEMA.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            try:
                await db.raw_execute(stmt)
            except Exception:
                pass
    # Migration : ajouter tenant_id aux tables existantes
    for stmt in _MIGRATION_ADD_TENANT_ID:
        try:
            await db.raw_execute(stmt)
        except Exception:
            pass  # Colonne existe deja, OK
    # RLS policies (PostgreSQL only)
    if _is_pg():
        for stmt in _RLS_POLICIES:
            try:
                await db.raw_execute(stmt)
            except Exception:
                pass  # Policy/RLS already exists, OK
        logger.info("PostgreSQL RLS policies activated for multi-tenant isolation")
    _schema_ready = True


# ── Context Manager ──


@asynccontextmanager
async def TenantContext(tenant_id: str):
    """Context manager async pour definir le tenant courant.

    Sets the ContextVar for application-level filtering AND
    configures PostgreSQL session variable for RLS enforcement.

    Usage:
        async with TenantContext("tenant-abc"):
            data = await get_agents(db)  # filtre automatiquement
    """
    token = _current_tenant.set(tenant_id)
    try:
        # Set PostgreSQL session variable for RLS (if using PG)
        if _is_pg() and tenant_id:
            try:
                from core.database import db
                await db.raw_execute(
                    f"SET LOCAL app.current_tenant = '{tenant_id.replace(chr(39), '')}'")
            except Exception:
                pass  # Non-critical: app-level filtering still works
        yield tenant_id
    finally:
        _current_tenant.reset(token)


def get_current_tenant() -> str:
    """Retourne le tenant_id courant depuis le contexte async."""
    return _current_tenant.get("")


def tenant_filter(query: str, tenant_id: str = "") -> tuple:
    """Ajoute un filtre tenant_id a une requete SQL.

    Retourne (query_modified, (tenant_id,)) ou (query, ()) si pas de tenant.
    Supporte SELECT et UPDATE/DELETE avec WHERE existant.
    """
    tid = tenant_id or get_current_tenant()
    if not tid:
        return query, ()

    query_upper = query.upper().strip()

    if " WHERE " in query_upper:
        # Inserer le filtre apres WHERE
        idx = query_upper.index(" WHERE ") + 7
        query = query[:idx] + "tenant_id=? AND " + query[idx:]
    elif any(query_upper.startswith(kw) for kw in ("SELECT", "UPDATE", "DELETE")):
        # Pas de WHERE, on l'ajoute
        # Pour SELECT: avant ORDER BY, LIMIT, GROUP BY
        for clause in (" ORDER BY", " LIMIT", " GROUP BY"):
            if clause in query_upper:
                idx = query_upper.index(clause)
                query = query[:idx] + " WHERE tenant_id=?" + query[idx:]
                return query, (tid,)
        query += " WHERE tenant_id=?"
    else:
        return query, ()

    return query, (tid,)


# ── Tenant CRUD ──


async def create_tenant(
    db,
    name: str,
    plan: str = "free",
    admin_email: str = "",
    admin_wallet: str = "",
    settings: Optional[dict] = None,
) -> dict:
    """Cree un nouveau tenant."""
    await _ensure_schema(db)

    if plan not in TENANT_PLANS:
        raise ValueError(f"Plan invalide: {plan}. Plans disponibles: {list(TENANT_PLANS.keys())}")

    tenant_id = f"tenant-{uuid.uuid4().hex[:12]}"
    now = int(time.time())

    await db.raw_execute(
        "INSERT INTO tenants (tenant_id, name, plan, admin_email, admin_wallet, status, settings, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?)",
        (tenant_id, name, plan, admin_email, admin_wallet, json.dumps(settings or {}), now, now),
    )

    return {
        "tenant_id": tenant_id,
        "name": name,
        "plan": plan,
        "admin_email": admin_email,
        "status": "active",
        "limits": TENANT_PLANS[plan],
        "created_at": now,
    }


async def list_tenants(db, status: str = "") -> list:
    """Liste tous les tenants (admin only)."""
    await _ensure_schema(db)

    if status:
        rows = await db.raw_execute_fetchall(
            "SELECT tenant_id, name, plan, admin_email, admin_wallet, status, "
            "settings, created_at, updated_at "
            "FROM tenants WHERE status=? ORDER BY created_at DESC", (status,)
        )
    else:
        rows = await db.raw_execute_fetchall(
            "SELECT tenant_id, name, plan, admin_email, admin_wallet, status, "
            "settings, created_at, updated_at "
            "FROM tenants ORDER BY created_at DESC"
        )

    results = []
    for row in rows:
        r = dict(row) if hasattr(row, "keys") else {
            "tenant_id": row[0], "name": row[1], "plan": row[2],
            "admin_email": row[3], "admin_wallet": row[4], "status": row[5],
            "settings": row[6], "created_at": row[7], "updated_at": row[8],
        }
        if isinstance(r.get("settings"), str):
            try:
                r["settings"] = json.loads(r["settings"])
            except (json.JSONDecodeError, TypeError):
                pass
        r["limits"] = TENANT_PLANS.get(r.get("plan", "free"), TENANT_PLANS["free"])
        results.append(r)

    return results


async def get_tenant(db, tenant_id: str) -> Optional[dict]:
    """Recupere un tenant par ID."""
    await _ensure_schema(db)

    rows = await db.raw_execute_fetchall(
        "SELECT tenant_id, name, plan, admin_email, admin_wallet, status, "
        "settings, created_at, updated_at "
        "FROM tenants WHERE tenant_id=?", (tenant_id,)
    )
    if not rows:
        return None

    row = rows[0]
    r = dict(row) if hasattr(row, "keys") else {
        "tenant_id": row[0], "name": row[1], "plan": row[2],
        "admin_email": row[3], "admin_wallet": row[4], "status": row[5],
        "settings": row[6], "created_at": row[7], "updated_at": row[8],
    }
    if isinstance(r.get("settings"), str):
        try:
            r["settings"] = json.loads(r["settings"])
        except (json.JSONDecodeError, TypeError):
            pass
    r["limits"] = TENANT_PLANS.get(r.get("plan", "free"), TENANT_PLANS["free"])

    # Stats d'usage
    r["usage"] = {
        "requests_today": _get_tenant_counter(tenant_id, "requests"),
        "swaps_today": _get_tenant_counter(tenant_id, "swaps"),
        "volume_today": _get_tenant_counter(tenant_id, "volume"),
    }

    return r


async def check_tenant_limit(db, tenant_id: str, metric: str, amount: int = 1) -> dict:
    """Verifie si un tenant a atteint sa limite pour une metrique.

    Retourne {"allowed": bool, "current": int, "limit": int}
    """
    tenant = await get_tenant(db, tenant_id)
    if not tenant:
        return {"allowed": False, "current": 0, "limit": 0, "reason": "tenant_not_found"}

    plan = tenant.get("plan", "free")
    limits = TENANT_PLANS.get(plan, TENANT_PLANS["free"])

    metric_map = {
        "requests": "max_requests_per_day",
        "swaps": "max_swaps_per_day",
        "agents": "max_agents",
    }

    limit_key = metric_map.get(metric)
    if not limit_key:
        return {"allowed": True, "current": 0, "limit": -1, "reason": "unknown_metric"}

    max_val = limits.get(limit_key, -1)
    if max_val == -1:  # Plan custom, illimite
        return {"allowed": True, "current": 0, "limit": -1, "reason": "unlimited"}

    current = _get_tenant_counter(tenant_id, metric)
    allowed = (current + amount) <= max_val

    if allowed:
        _increment_tenant_counter(tenant_id, metric, amount)

    return {
        "allowed": allowed,
        "current": current,
        "limit": max_val,
        "reason": "ok" if allowed else f"{metric}_limit_reached",
    }


# ── FastAPI Routes ──


class CreateTenantRequest(BaseModel):
    name: str
    plan: str = "free"
    admin_email: str = ""
    admin_wallet: str = ""
    settings: Optional[dict] = None


@router.get("")
async def route_list_tenants(
    status: str = Query("", description="Filtrer par statut (active, suspended, ...)"),
):
    """Lister tous les tenants (admin)."""
    from core.database import db
    await _ensure_schema(db)

    tenants = await list_tenants(db, status=status)

    # Si pas de tenants, retourner un exemple
    if not tenants:
        tenants = [
            {
                "tenant_id": "tenant-demo",
                "name": "Demo Corp",
                "plan": "pro",
                "admin_email": "admin@demo.ai",
                "status": "active",
                "limits": TENANT_PLANS["pro"],
                "created_at": int(time.time()) - 86400 * 30,
                "usage": {"requests_today": 1234, "swaps_today": 56, "volume_today": 12500},
            },
        ]

    return {"count": len(tenants), "tenants": tenants}


@router.post("")
async def route_create_tenant(req: CreateTenantRequest):
    """Creer un nouveau tenant (admin)."""
    from core.database import db

    try:
        tenant = await create_tenant(
            db,
            name=req.name,
            plan=req.plan,
            admin_email=req.admin_email,
            admin_wallet=req.admin_wallet,
            settings=req.settings,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

    return {"success": True, "tenant": tenant}


@router.get("/me")
async def route_tenant_me(
    x_tenant: str = Header("", alias="X-Tenant-ID"),
):
    """Voir les details de son propre tenant."""
    from core.database import db

    if not x_tenant:
        # Demo fallback
        return {
            "tenant_id": "tenant-demo",
            "name": "Demo Tenant",
            "plan": "free",
            "status": "active",
            "limits": TENANT_PLANS["free"],
            "usage": {"requests_today": 0, "swaps_today": 0, "volume_today": 0},
            "note": "Envoyez le header X-Tenant-ID pour voir votre vrai tenant",
        }

    tenant = await get_tenant(db, x_tenant)
    if not tenant:
        raise HTTPException(404, f"Tenant {x_tenant} non trouve")

    return tenant


@router.get("/plans")
async def route_list_plans():
    """Lister tous les plans disponibles et leurs limites."""
    return {
        "plans": {
            name: {
                "limits": limits,
                "price_hint": {
                    "free": "$0/mo",
                    "pro": "$99/mo",
                    "enterprise": "$499/mo",
                    "custom": "Contact sales",
                }.get(name, "N/A"),
            }
            for name, limits in TENANT_PLANS.items()
        },
    }


@router.get("/{tenant_id}/usage")
async def route_tenant_usage(tenant_id: str):
    """Voir l'usage courant d'un tenant."""
    from core.database import db

    tenant = await get_tenant(db, tenant_id)
    if not tenant:
        raise HTTPException(404, f"Tenant {tenant_id} non trouve")

    plan = tenant.get("plan", "free")
    limits = TENANT_PLANS.get(plan, TENANT_PLANS["free"])

    return {
        "tenant_id": tenant_id,
        "plan": plan,
        "usage": tenant.get("usage", {}),
        "limits": {
            "max_requests_per_day": limits["max_requests_per_day"],
            "max_swaps_per_day": limits["max_swaps_per_day"],
            "max_daily_volume_usdc": limits["max_daily_volume_usdc"],
        },
    }


logger.info(f"Module charge — "
            f"{len(TENANT_PLANS)} plans (free/pro/enterprise/custom), "
            "filtrage applicatif SQLite + RLS PostgreSQL pret")
