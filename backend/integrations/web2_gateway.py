"""MAXIA Web2 Gateway V12 — Bridge for Web2 apps to use MAXIA services via Stripe."""

import logging, os, time, uuid
from typing import Optional
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_SUCCESS_URL = os.getenv("STRIPE_SUCCESS_URL", "https://maxiaworld.app/app.html?stripe=success")
STRIPE_CANCEL_URL = os.getenv("STRIPE_CANCEL_URL", "https://maxiaworld.app/app.html?stripe=cancel")
TOPUP_AMOUNTS = {5: 5.00, 10: 10.00, 25: 25.00, 50: 50.00}
_FREE_DAILY_LIMIT = 100
_GW_COLS = ("gw_id", "api_key", "app_name", "email", "webhook_url",
            "balance_usd", "total_spent_usd", "executions", "created_at", "status")

# ── Stripe conditional import ──
stripe = None
_STRIPE_AVAILABLE = False
if STRIPE_SECRET_KEY:
    try:
        import stripe as _stripe
        _stripe.api_key = STRIPE_SECRET_KEY
        _stripe.api_version = "2024-12-18.acacia"
        stripe = _stripe
        _STRIPE_AVAILABLE = True
        logger.info("Web2 Gateway: Stripe SDK initialise")
    except ImportError:
        logger.error("Web2 Gateway: package 'stripe' non installe")
    except Exception as e:
        logger.error("Web2 Gateway: erreur init Stripe: %s", e)

_schema_ready = False
_GATEWAY_SCHEMA = """
CREATE TABLE IF NOT EXISTS web2_gateways (
    gw_id TEXT PRIMARY KEY,
    api_key TEXT UNIQUE NOT NULL,
    app_name TEXT NOT NULL,
    email TEXT,
    webhook_url TEXT,
    balance_usd REAL DEFAULT 0,
    total_spent_usd REAL DEFAULT 0,
    executions INTEGER DEFAULT 0,
    created_at INTEGER NOT NULL,
    status TEXT DEFAULT 'active'
);
CREATE INDEX IF NOT EXISTS idx_gw_api_key ON web2_gateways(api_key);
CREATE INDEX IF NOT EXISTS idx_gw_status ON web2_gateways(status);
CREATE TABLE IF NOT EXISTS web2_gateway_executions (
    exec_id TEXT PRIMARY KEY, gw_id TEXT NOT NULL, service_id TEXT NOT NULL,
    price_usd REAL NOT NULL, status TEXT DEFAULT 'pending',
    result TEXT, created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_gw_exec_gw ON web2_gateway_executions(gw_id);
"""


async def _ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    try:
        from core.database import db
        await db.raw_executescript(_GATEWAY_SCHEMA)
        _schema_ready = True
    except Exception as e:
        logger.error("Web2 Gateway: erreur schema: %s", e)

_usage_store: dict[str, list[float]] = {}


def _check_free_rate_limit(api_key: str) -> bool:
    now = time.time()
    day_start = now - (now % 86400)
    day_key = f"gw:{api_key}:day"
    _usage_store.setdefault(day_key, [])
    _usage_store[day_key] = [t for t in _usage_store[day_key] if t > day_start]
    if len(_usage_store[day_key]) >= _FREE_DAILY_LIMIT:
        return False
    _usage_store[day_key].append(now)
    # Evict stale entries when store grows large
    if len(_usage_store) > 5000:
        expired = [k for k, v in _usage_store.items() if not v or v[-1] < day_start]
        for k in expired:
            del _usage_store[k]
    return True


def _row_to_dict(row) -> dict:
    return row if isinstance(row, dict) else dict(zip(_GW_COLS, row))


async def _get_gateway(where_col: str, value: str) -> Optional[dict]:
    await _ensure_schema()
    from core.database import db
    cols = ", ".join(_GW_COLS)
    rows = await db.raw_execute_fetchall(
        f"SELECT {cols} FROM web2_gateways WHERE {where_col} = ?", (value,),
    )
    return _row_to_dict(rows[0]) if rows else None


async def _update_balance(gw_id: str, delta: float, is_spend: bool) -> None:
    from core.database import db
    if is_spend:
        await db.raw_execute(
            "UPDATE web2_gateways SET balance_usd = balance_usd - ?, "
            "total_spent_usd = total_spent_usd + ?, executions = executions + 1 "
            "WHERE gw_id = ?", (delta, delta, gw_id),
        )
    else:
        await db.raw_execute(
            "UPDATE web2_gateways SET balance_usd = balance_usd + ? WHERE gw_id = ?",
            (delta, gw_id),
        )


def _get_service_price(service_id: str) -> Optional[float]:
    try:
        from core.config import SERVICE_PRICES
        from core.seed_data import NATIVE_SERVICES
    except ImportError:
        return None
    price = SERVICE_PRICES.get(service_id)
    if price is not None:
        return float(price)
    for svc in NATIVE_SERVICES:
        if svc["id"] == service_id:
            return float(svc.get("price_usdc", 0))
    return None


def _get_services_list() -> list[dict]:
    try:
        from core.config import SERVICE_PRICES
        from core.seed_data import NATIVE_SERVICES
    except ImportError:
        return []
    result: list[dict] = []
    for svc in NATIVE_SERVICES:
        price = SERVICE_PRICES.get(svc["id"], svc.get("price_usdc", 0))
        result.append({
            "service_id": svc["id"], "name": svc["name"],
            "description": svc["description"], "type": svc["type"],
            "price_usd": price, "machine_only": svc.get("machine_only", False),
        })
    return result


async def _execute_internal(service_id: str, params: dict) -> dict:
    try:
        if service_id == "maxia-image":
            from ai.image_gen import generate_image
            prompt = params.get("prompt", "")
            if not prompt:
                return {"status": "error", "message": "Missing 'prompt' parameter"}
            return {"status": "completed", "result": await generate_image(prompt)}

        if service_id == "maxia-sentiment":
            from ai.sentiment_analyzer import analyze_sentiment
            text = params.get("text", "")
            if not text:
                return {"status": "error", "message": "Missing 'text' parameter"}
            return {"status": "completed", "result": await analyze_sentiment(text)}

        if service_id in ("maxia-translate", "maxia-summary"):
            from ai.llm_service import llm_generate
            text = params.get("text", "")
            if not text:
                return {"status": "error", "message": "Missing 'text' parameter"}
            if service_id == "maxia-translate":
                lang = params.get("target_lang", "en")
                out = await llm_generate(f"Translate to {lang}. Return ONLY the translation:\n\n{text}")
                return {"status": "completed", "result": {"translation": out}}
            out = await llm_generate(f"Summarize into key bullet points:\n\n{text}")
            return {"status": "completed", "result": {"summary": out}}

        return {"status": "queued", "message": f"Service {service_id} execution queued"}
    except ImportError:
        return {"status": "queued", "message": f"Service {service_id} execution queued"}
    except Exception as e:
        logger.error("Web2 Gateway: exec error %s: %s", service_id, e)
        return {"status": "error", "message": "Internal execution error"}


async def _dispatch_webhook(url: str, payload: dict) -> None:
    try:
        from core.http_client import get_http_client
        client = await get_http_client()
        await client.post(url, json=payload, timeout=10.0)
    except Exception as e:
        logger.warning("Web2 Gateway: webhook failed: %s", e)

router = APIRouter(prefix="/api/gateway", tags=["web2-gateway"])

class RegisterRequest(BaseModel):
    app_name: str
    email: Optional[str] = None
    webhook_url: Optional[str] = None

class ExecuteRequest(BaseModel):
    service_id: str
    params: dict = {}

class TopupRequest(BaseModel):
    amount: int  # 5, 10, 25, or 50

class WebhookConfigRequest(BaseModel):
    webhook_url: str

# ── Endpoints ──

@router.post("/register")
async def register_gateway(req: RegisterRequest) -> dict:
    if not req.app_name or len(req.app_name) < 2 or len(req.app_name) > 128:
        raise HTTPException(400, "app_name invalide (2-128 caracteres)")
    if req.email and len(req.email) > 256:
        raise HTTPException(400, "email trop long (max 256)")
    if req.webhook_url and len(req.webhook_url) > 512:
        raise HTTPException(400, "webhook_url trop long (max 512)")

    await _ensure_schema()
    from core.database import db

    gw_id = f"gw_{uuid.uuid4().hex[:16]}"
    api_key = f"gw_{uuid.uuid4().hex}"
    now_ts = int(time.time())

    await db.raw_execute(
        "INSERT INTO web2_gateways "
        "(gw_id, api_key, app_name, email, webhook_url, balance_usd, "
        "total_spent_usd, executions, created_at, status) "
        "VALUES (?, ?, ?, ?, ?, 0, 0, 0, ?, 'active')",
        (gw_id, api_key, req.app_name, req.email, req.webhook_url, now_ts),
    )
    logger.info("Web2 Gateway: registered app=%s gw_id=%s", req.app_name, gw_id)
    return {
        "status": "registered", "gw_id": gw_id, "api_key": api_key,
        "app_name": req.app_name, "balance_usd": 0.0,
        "message": "Use x-api-key header. Top up via POST /api/gateway/topup.",
    }


def _require_gw(api_key: str) -> None:
    if not api_key or not api_key.startswith("gw_"):
        raise HTTPException(401, "API key gateway invalide (doit commencer par gw_)")


@router.post("/execute")
async def execute_service(req: ExecuteRequest, x_api_key: str = Header(alias="x-api-key")) -> dict:
    _require_gw(x_api_key)
    gw = await _get_gateway("api_key", x_api_key)
    if not gw:
        raise HTTPException(401, "API key gateway inconnue")
    if gw["status"] != "active":
        raise HTTPException(403, "Gateway desactive")

    price = _get_service_price(req.service_id)
    if price is None:
        raise HTTPException(404, f"Service inconnu: {req.service_id}")

    balance = float(gw["balance_usd"])
    charged = False
    if price > 0:
        if balance >= price:
            await _update_balance(gw["gw_id"], price, is_spend=True)
            charged = True
        elif balance <= 0:
            if not _check_free_rate_limit(x_api_key):
                raise HTTPException(429, "Limite 100 req/jour atteinte. Top up via /api/gateway/topup")
        else:
            raise HTTPException(402, f"Solde insuffisant: ${balance:.2f} < ${price:.2f}")

    result = await _execute_internal(req.service_id, req.params)
    exec_status = result.get("status", "error")

    # Record execution
    from core.database import db
    exec_id = f"exec_{uuid.uuid4().hex[:16]}"
    await db.raw_execute(
        "INSERT INTO web2_gateway_executions "
        "(exec_id, gw_id, service_id, price_usd, status, result, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (exec_id, gw["gw_id"], req.service_id, price if charged else 0.0,
         exec_status, str(result.get("result", "")), int(time.time())),
    )

    response = {
        "exec_id": exec_id, "service_id": req.service_id, "status": exec_status,
        "charged_usd": price if charged else 0.0,
        "result": result.get("result"), "message": result.get("message"),
    }
    if gw.get("webhook_url") and exec_status == "completed":
        import asyncio
        asyncio.create_task(_dispatch_webhook(gw["webhook_url"], response))
    return response


@router.get("/balance")
async def get_balance(x_api_key: str = Header(alias="x-api-key")) -> dict:
    _require_gw(x_api_key)
    gw = await _get_gateway("api_key", x_api_key)
    if not gw:
        raise HTTPException(401, "API key gateway inconnue")
    return {
        "gw_id": gw["gw_id"], "app_name": gw["app_name"],
        "balance_usd": float(gw["balance_usd"]),
        "total_spent_usd": float(gw["total_spent_usd"]),
        "executions": gw["executions"], "status": gw["status"],
        "free_tier_limit": _FREE_DAILY_LIMIT,
    }


@router.post("/topup")
async def topup_balance(req: TopupRequest, x_api_key: str = Header(alias="x-api-key")) -> dict:
    _require_gw(x_api_key)
    gw = await _get_gateway("api_key", x_api_key)
    if not gw:
        raise HTTPException(401, "API key gateway inconnue")
    if req.amount not in TOPUP_AMOUNTS:
        raise HTTPException(400, f"Montant invalide. Disponibles: {sorted(TOPUP_AMOUNTS)}")
    if not _STRIPE_AVAILABLE:
        raise HTTPException(503, "Stripe non configure. Contactez support@maxiaworld.app")

    amount_usd = TOPUP_AMOUNTS[req.amount]
    try:
        session = stripe.checkout.Session.create(
            mode="payment", payment_method_types=["card"],
            line_items=[{"price_data": {
                "currency": "usd",
                "product_data": {"name": f"MAXIA Gateway Credit ${int(amount_usd)}"},
                "unit_amount": int(amount_usd * 100),
            }, "quantity": 1}],
            success_url=f"{STRIPE_SUCCESS_URL}&gateway_topup=success",
            cancel_url=STRIPE_CANCEL_URL,
            metadata={"gw_id": gw["gw_id"], "topup_amount": str(amount_usd), "type": "gateway_topup"},
        )
        logger.info("Web2 Gateway: topup checkout gw_id=%s $%s", gw["gw_id"], amount_usd)
        return {"status": "checkout_created", "checkout_url": session.url,
                "session_id": session.id, "amount_usd": amount_usd, "gw_id": gw["gw_id"]}
    except Exception as e:
        logger.error("Web2 Gateway: Stripe error: %s", e)
        raise HTTPException(502, "Erreur Stripe checkout")


@router.get("/services")
async def list_services() -> dict:
    services = _get_services_list()
    return {"services": services, "count": len(services), "currency": "USD",
            "note": "Free tier: 100 req/day. Top up for unlimited access."}


@router.post("/webhook-config")
async def configure_webhook(req: WebhookConfigRequest, x_api_key: str = Header(alias="x-api-key")) -> dict:
    _require_gw(x_api_key)
    gw = await _get_gateway("api_key", x_api_key)
    if not gw:
        raise HTTPException(401, "API key gateway inconnue")
    if not req.webhook_url or len(req.webhook_url) > 512:
        raise HTTPException(400, "webhook_url invalide (max 512)")
    if not req.webhook_url.startswith(("http://", "https://")):
        raise HTTPException(400, "webhook_url doit commencer par http:// ou https://")
    from core.database import db
    await db.raw_execute(
        "UPDATE web2_gateways SET webhook_url = ? WHERE gw_id = ?",
        (req.webhook_url, gw["gw_id"]),
    )
    return {"status": "configured", "gw_id": gw["gw_id"], "webhook_url": req.webhook_url}


async def handle_gateway_topup_webhook(session: dict) -> dict:
    """Called from Stripe webhook when metadata.type == 'gateway_topup'."""
    metadata = session.get("metadata", {})
    gw_id = metadata.get("gw_id", "")
    if not gw_id:
        return {"status": "error", "reason": "missing_gw_id"}
    try:
        amount_usd = float(metadata.get("topup_amount", "0"))
    except (ValueError, TypeError):
        return {"status": "error", "reason": "invalid_amount"}
    if amount_usd <= 0:
        return {"status": "error", "reason": "zero_amount"}
    gw = await _get_gateway("gw_id", gw_id)
    if not gw:
        return {"status": "error", "reason": "unknown_gateway"}
    await _update_balance(gw_id, amount_usd, is_spend=False)
    logger.info("Web2 Gateway: topup credited gw_id=%s $%s", gw_id, amount_usd)
    return {"status": "balance_credited", "gw_id": gw_id, "amount_usd": amount_usd}
