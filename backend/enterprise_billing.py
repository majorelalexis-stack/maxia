"""MAXIA Enterprise Billing V12 — Facturation usage-based avec metering, invoicing et tiers

Systeme de facturation autonome (sans SDK externe) qui suit :
- Appels API par tenant (metered)
- Heures GPU consommees par tenant
- Volume de swaps par tenant
- Usage de tokens LLM
- Generation mensuelle de factures
- 4 tiers : Free, Pro ($9.99), Enterprise ($299), Custom

Accumulateur in-memory avec flush vers DB toutes les 60 secondes.
"""
import os, time, uuid, asyncio, json
from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel

router = APIRouter(prefix="/api/enterprise/billing", tags=["enterprise-billing"])

# ── Config ──

BILLING_ENABLED = os.getenv("BILLING_ENABLED", "false").lower() == "true"

# ── Tiers de facturation ──

BILLING_TIERS = {
    "free": {
        "name": "Free",
        "monthly_price": 0.0,
        "included_api_calls": 1000,
        "included_gpu_hours": 0.0,
        "included_swap_volume": 100.0,
        "included_llm_tokens": 50000,
        "overage_api_call": 0.001,      # $0.001 par appel au-dela
        "overage_gpu_hour": 0.50,       # $0.50/h GPU au-dela
        "overage_swap_bps": 10,         # 10 bps sur le volume au-dela
        "overage_llm_per_1k": 0.01,    # $0.01 par 1k tokens au-dela
    },
    "pro": {
        "name": "Pro",
        "monthly_price": 9.99,
        "included_api_calls": 50000,
        "included_gpu_hours": 10.0,
        "included_swap_volume": 10000.0,
        "included_llm_tokens": 500000,
        "overage_api_call": 0.0005,
        "overage_gpu_hour": 0.40,
        "overage_swap_bps": 7,
        "overage_llm_per_1k": 0.008,
    },
    "enterprise": {
        "name": "Enterprise",
        "monthly_price": 299.0,
        "included_api_calls": 1000000,
        "included_gpu_hours": 200.0,
        "included_swap_volume": 500000.0,
        "included_llm_tokens": 5000000,
        "overage_api_call": 0.0002,
        "overage_gpu_hour": 0.30,
        "overage_swap_bps": 5,
        "overage_llm_per_1k": 0.005,
    },
    "custom": {
        "name": "Custom",
        "monthly_price": 0.0,  # Negocie individuellement
        "included_api_calls": 0,
        "included_gpu_hours": 0.0,
        "included_swap_volume": 0.0,
        "included_llm_tokens": 0,
        "overage_api_call": 0.0,
        "overage_gpu_hour": 0.0,
        "overage_swap_bps": 0,
        "overage_llm_per_1k": 0.0,
    },
}

# Metriques supportees
VALID_METRICS = {"api_calls", "gpu_hours", "swap_volume", "llm_tokens"}

# ── Schema DB ──

_schema_ready = False

_BILLING_SCHEMA = """
CREATE TABLE IF NOT EXISTS billing_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    metric TEXT NOT NULL,
    value REAL NOT NULL DEFAULT 0,
    recorded_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);

CREATE INDEX IF NOT EXISTS idx_billing_tenant_metric ON billing_usage(tenant_id, metric, recorded_at);

CREATE TABLE IF NOT EXISTS billing_tenants (
    tenant_id TEXT PRIMARY KEY,
    tier TEXT NOT NULL DEFAULT 'free',
    custom_config TEXT DEFAULT '',
    created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS billing_invoices (
    invoice_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    month TEXT NOT NULL,
    tier TEXT NOT NULL,
    base_price REAL NOT NULL DEFAULT 0,
    overage_total REAL NOT NULL DEFAULT 0,
    total_amount REAL NOT NULL DEFAULT 0,
    line_items TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'draft',
    generated_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);

CREATE INDEX IF NOT EXISTS idx_invoices_tenant_month ON billing_invoices(tenant_id, month);
"""


async def _ensure_schema():
    """Cree les tables de billing si elles n'existent pas encore."""
    global _schema_ready
    if _schema_ready:
        return
    try:
        from database import db
        await db.raw_executescript(_BILLING_SCHEMA)
        _schema_ready = True
        print("[Billing] Schema pret")
    except Exception as e:
        print(f"[Billing] Erreur schema: {e}")


# ── Accumulateur in-memory (flush toutes les 60s) ──

_accumulator: dict = {}  # (tenant_id, metric) -> accumulated_value
_accumulator_lock = asyncio.Lock() if hasattr(asyncio, "Lock") else None
_last_flush = time.time()
FLUSH_INTERVAL_S = 60


def _get_lock():
    """Lazy init du lock asyncio (doit etre cree dans la boucle)."""
    global _accumulator_lock
    if _accumulator_lock is None:
        _accumulator_lock = asyncio.Lock()
    return _accumulator_lock


async def record_usage(tenant_id: str, metric: str, value: float):
    """Enregistre une mesure d'usage dans l'accumulateur in-memory.

    L'accumulateur est flush vers la DB toutes les FLUSH_INTERVAL_S secondes.
    Les metriques supportees : api_calls, gpu_hours, swap_volume, llm_tokens.
    """
    if not BILLING_ENABLED:
        return
    if metric not in VALID_METRICS:
        return
    if value <= 0:
        return

    lock = _get_lock()
    async with lock:
        key = (tenant_id, metric)
        _accumulator[key] = _accumulator.get(key, 0.0) + value

    # Flush si necessaire
    await _maybe_flush()


async def _maybe_flush():
    """Flush l'accumulateur vers la DB si le delai est depasse."""
    global _last_flush
    now = time.time()
    if now - _last_flush < FLUSH_INTERVAL_S:
        return
    _last_flush = now
    await _flush_accumulator()


async def _flush_accumulator():
    """Ecrit toutes les mesures accumulees en DB et vide le buffer."""
    lock = _get_lock()
    async with lock:
        if not _accumulator:
            return
        snapshot = dict(_accumulator)
        _accumulator.clear()

    try:
        from database import db
        await _ensure_schema()
        now_ts = int(time.time())
        for (tenant_id, metric), value in snapshot.items():
            await db.raw_execute(
                "INSERT INTO billing_usage (tenant_id, metric, value, recorded_at) VALUES (?, ?, ?, ?)",
                (tenant_id, metric, value, now_ts),
            )
        print(f"[Billing] Flush: {len(snapshot)} mesures ecrites en DB")
    except Exception as e:
        # Remettre les donnees dans l'accumulateur en cas d'erreur
        async with lock:
            for key, value in snapshot.items():
                _accumulator[key] = _accumulator.get(key, 0.0) + value
        print(f"[Billing] Erreur flush: {e}")


# ── Fonctions de calcul ──

def _get_month_range(month: str) -> tuple:
    """Retourne le timestamp debut/fin pour un mois donne (format YYYY-MM)."""
    try:
        start = datetime.strptime(month, "%Y-%m")
    except ValueError:
        raise HTTPException(400, f"Format mois invalide: {month}. Utiliser YYYY-MM")

    # Premier jour du mois suivant
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)

    return int(start.timestamp()), int(end.timestamp())


async def get_usage_summary(tenant_id: str, month: str) -> dict:
    """Retourne l'usage agrege pour un tenant sur un mois donne."""
    from database import db
    await _ensure_schema()

    # Flush d'abord pour avoir les donnees les plus recentes
    await _flush_accumulator()

    start_ts, end_ts = _get_month_range(month)

    summary = {}
    for metric in VALID_METRICS:
        rows = await db.raw_execute_fetchall(
            "SELECT COALESCE(SUM(value), 0) as total FROM billing_usage "
            "WHERE tenant_id = ? AND metric = ? AND recorded_at >= ? AND recorded_at < ?",
            (tenant_id, metric, start_ts, end_ts),
        )
        total = rows[0][0] if rows and rows[0] else 0.0
        # Gestion dict vs tuple (PostgreSQL vs SQLite)
        if isinstance(total, dict):
            total = list(total.values())[0]
        summary[metric] = float(total) if total else 0.0

    return {
        "tenant_id": tenant_id,
        "month": month,
        "usage": summary,
    }


async def _get_tenant_tier(tenant_id: str) -> str:
    """Recupere le tier d'un tenant depuis la DB."""
    from database import db
    await _ensure_schema()
    rows = await db.raw_execute_fetchall(
        "SELECT tier FROM billing_tenants WHERE tenant_id = ?",
        (tenant_id,),
    )
    if rows:
        val = rows[0]
        return val["tier"] if isinstance(val, dict) else val[0]
    return "free"


async def _check_stripe_subscription(tenant_id: str) -> dict:
    """Verifie si le tenant a un abonnement Stripe actif.

    Retourne {"active": bool, "plan": str, "stripe_covers_base": bool}.
    Si stripe_billing n'est pas disponible, retourne inactive.
    """
    try:
        from stripe_billing import has_active_stripe_subscription, get_subscription_status
        if await has_active_stripe_subscription(tenant_id):
            status = await get_subscription_status(tenant_id)
            return {
                "active": True,
                "plan": status.get("plan", ""),
                "stripe_covers_base": True,
            }
    except ImportError:
        pass  # stripe_billing non installe/disponible
    except Exception as e:
        print(f"[Billing] Stripe check warning: {e}")
    return {"active": False, "plan": "", "stripe_covers_base": False}


async def generate_invoice(tenant_id: str, month: str) -> dict:
    """Genere une facture pour un tenant sur un mois donne.

    Calcule : prix de base du tier + depassements (overages) par metrique.
    Si le tenant a un abonnement Stripe actif, le prix de base est couvert par Stripe
    et la facture MAXIA ne contient que les depassements (overages).
    """
    from database import db
    await _ensure_schema()

    # Verifier si une facture existe deja pour ce mois
    existing = await db.raw_execute_fetchall(
        "SELECT invoice_id, total_amount, status FROM billing_invoices "
        "WHERE tenant_id = ? AND month = ?",
        (tenant_id, month),
    )
    if existing:
        row = existing[0]
        inv_id = row["invoice_id"] if isinstance(row, dict) else row[0]
        total = row["total_amount"] if isinstance(row, dict) else row[1]
        status = row["status"] if isinstance(row, dict) else row[2]
        return {
            "invoice_id": inv_id,
            "status": status,
            "total_amount": total,
            "note": "Facture deja generee pour ce mois",
        }

    # Verifier si Stripe couvre le prix de base
    stripe_info = await _check_stripe_subscription(tenant_id)
    stripe_covers_base = stripe_info["stripe_covers_base"]

    # Recuperer le tier et l'usage
    tier_name = await _get_tenant_tier(tenant_id)
    tier = BILLING_TIERS.get(tier_name, BILLING_TIERS["free"])
    usage = await get_usage_summary(tenant_id, month)

    # Calculer les depassements
    line_items = []
    overage_total = 0.0

    # API calls
    api_used = usage["usage"].get("api_calls", 0)
    api_over = max(0, api_used - tier["included_api_calls"])
    api_charge = api_over * tier["overage_api_call"]
    line_items.append({
        "metric": "api_calls",
        "used": api_used,
        "included": tier["included_api_calls"],
        "overage": api_over,
        "charge": round(api_charge, 4),
    })
    overage_total += api_charge

    # GPU hours
    gpu_used = usage["usage"].get("gpu_hours", 0)
    gpu_over = max(0, gpu_used - tier["included_gpu_hours"])
    gpu_charge = gpu_over * tier["overage_gpu_hour"]
    line_items.append({
        "metric": "gpu_hours",
        "used": gpu_used,
        "included": tier["included_gpu_hours"],
        "overage": gpu_over,
        "charge": round(gpu_charge, 4),
    })
    overage_total += gpu_charge

    # Swap volume
    swap_used = usage["usage"].get("swap_volume", 0)
    swap_over = max(0, swap_used - tier["included_swap_volume"])
    swap_charge = (swap_over * tier["overage_swap_bps"]) / 10000  # BPS -> fraction
    line_items.append({
        "metric": "swap_volume",
        "used": swap_used,
        "included": tier["included_swap_volume"],
        "overage": swap_over,
        "charge": round(swap_charge, 4),
    })
    overage_total += swap_charge

    # LLM tokens
    llm_used = usage["usage"].get("llm_tokens", 0)
    llm_over = max(0, llm_used - tier["included_llm_tokens"])
    llm_charge = (llm_over / 1000) * tier["overage_llm_per_1k"]
    line_items.append({
        "metric": "llm_tokens",
        "used": llm_used,
        "included": tier["included_llm_tokens"],
        "overage": llm_over,
        "charge": round(llm_charge, 4),
    })
    overage_total += llm_charge

    # Total — si Stripe couvre le base_price, on ne facture que les overages
    base_price = tier["monthly_price"]
    if stripe_covers_base:
        # Le prix de base est deja paye via Stripe, seuls les overages restent
        effective_base = 0.0
    else:
        effective_base = base_price

    total_amount = round(effective_base + overage_total, 2)
    invoice_id = f"INV-{uuid.uuid4().hex[:12].upper()}"

    # Statut : si Stripe actif et pas d'overages, la facture est deja payee
    invoice_status = "draft"
    if stripe_covers_base and overage_total == 0:
        invoice_status = "paid_via_stripe"
    elif stripe_covers_base:
        invoice_status = "base_paid_via_stripe"

    # Sauvegarder en DB
    await db.raw_execute(
        "INSERT INTO billing_invoices (invoice_id, tenant_id, month, tier, base_price, "
        "overage_total, total_amount, line_items, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            invoice_id, tenant_id, month, tier_name,
            effective_base, round(overage_total, 4), total_amount,
            json.dumps(line_items), invoice_status,
        ),
    )

    result = {
        "invoice_id": invoice_id,
        "tenant_id": tenant_id,
        "month": month,
        "tier": tier_name,
        "tier_name": tier["name"],
        "base_price": effective_base,
        "overage_total": round(overage_total, 4),
        "total_amount": total_amount,
        "line_items": line_items,
        "status": invoice_status,
        "generated_at": datetime.utcnow().isoformat(),
    }

    # Ajouter les infos Stripe si actif
    if stripe_covers_base:
        result["stripe_subscription"] = {
            "active": True,
            "plan": stripe_info["plan"],
            "base_price_covered": base_price,
            "note": "Prix de base couvert par abonnement Stripe",
        }

    return result


# ── Pydantic models ──

class RecordUsageRequest(BaseModel):
    tenant_id: str
    metric: str
    value: float


# ── Endpoints FastAPI ──

@router.get("/tiers")
async def get_billing_tiers():
    """Retourne les tiers de facturation disponibles."""
    return {"tiers": BILLING_TIERS, "enabled": BILLING_ENABLED}


@router.post("/record")
async def api_record_usage(req: RecordUsageRequest):
    """Enregistre une mesure d'usage pour un tenant."""
    if not BILLING_ENABLED:
        return {"status": "billing_disabled", "message": "Billing non active (BILLING_ENABLED=false)"}

    if req.metric not in VALID_METRICS:
        raise HTTPException(400, f"Metrique invalide: {req.metric}. Valides: {', '.join(sorted(VALID_METRICS))}")
    if req.value <= 0:
        raise HTTPException(400, "La valeur doit etre > 0")
    if not req.tenant_id or len(req.tenant_id) > 128:
        raise HTTPException(400, "tenant_id invalide (1-128 caracteres)")

    await record_usage(req.tenant_id, req.metric, req.value)
    return {
        "status": "recorded",
        "tenant_id": req.tenant_id,
        "metric": req.metric,
        "value": req.value,
    }


@router.get("/usage")
async def api_get_usage(
    tenant_id: str = Query(..., description="ID du tenant"),
    month: str = Query(default=None, description="Mois au format YYYY-MM (defaut: mois courant)"),
):
    """Retourne le resume d'usage pour un tenant sur un mois donne."""
    if not BILLING_ENABLED:
        return {"status": "billing_disabled"}

    if not tenant_id:
        raise HTTPException(400, "tenant_id requis")

    if month is None:
        month = datetime.utcnow().strftime("%Y-%m")

    summary = await get_usage_summary(tenant_id, month)
    tier = await _get_tenant_tier(tenant_id)
    tier_info = BILLING_TIERS.get(tier, BILLING_TIERS["free"])

    return {
        **summary,
        "tier": tier,
        "tier_name": tier_info["name"],
        "limits": {
            "api_calls": tier_info["included_api_calls"],
            "gpu_hours": tier_info["included_gpu_hours"],
            "swap_volume": tier_info["included_swap_volume"],
            "llm_tokens": tier_info["included_llm_tokens"],
        },
    }


@router.get("/invoice/{month}")
async def api_get_invoice(
    month: str,
    tenant_id: str = Query(..., description="ID du tenant"),
):
    """Genere ou recupere la facture pour un tenant sur un mois."""
    if not BILLING_ENABLED:
        return {"status": "billing_disabled"}

    if not tenant_id:
        raise HTTPException(400, "tenant_id requis")

    invoice = await generate_invoice(tenant_id, month)
    return invoice


@router.post("/tenant")
async def api_create_tenant(
    request: Request,
):
    """Cree ou met a jour un tenant avec son tier de facturation."""
    if not BILLING_ENABLED:
        return {"status": "billing_disabled"}

    body = await request.json()
    tenant_id = body.get("tenant_id", "")
    tier = body.get("tier", "free")

    if not tenant_id or len(tenant_id) > 128:
        raise HTTPException(400, "tenant_id invalide (1-128 caracteres)")
    if tier not in BILLING_TIERS:
        raise HTTPException(400, f"Tier invalide: {tier}. Valides: {', '.join(BILLING_TIERS.keys())}")

    from database import db
    await _ensure_schema()

    # Upsert : INSERT ou UPDATE si existe deja
    existing = await db.raw_execute_fetchall(
        "SELECT tenant_id FROM billing_tenants WHERE tenant_id = ?",
        (tenant_id,),
    )
    if existing:
        await db.raw_execute(
            "UPDATE billing_tenants SET tier = ? WHERE tenant_id = ?",
            (tier, tenant_id),
        )
        action = "updated"
    else:
        await db.raw_execute(
            "INSERT INTO billing_tenants (tenant_id, tier) VALUES (?, ?)",
            (tenant_id, tier),
        )
        action = "created"

    return {
        "status": action,
        "tenant_id": tenant_id,
        "tier": tier,
        "tier_name": BILLING_TIERS[tier]["name"],
    }


# ── Background flush task ──

async def billing_flush_loop():
    """Tache de fond qui flush l'accumulateur toutes les 60 secondes.
    A lancer via asyncio.create_task() au demarrage de l'app.
    """
    while True:
        await asyncio.sleep(FLUSH_INTERVAL_S)
        try:
            await _flush_accumulator()
        except Exception as e:
            print(f"[Billing] Erreur flush loop: {e}")
