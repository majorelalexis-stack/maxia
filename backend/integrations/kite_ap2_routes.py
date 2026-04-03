"""MAXIA V12 — Kite AI and AP2 (Google Agent Payments Protocol) routes"""
import logging
from fastapi import APIRouter, HTTPException, Depends, Request
from core.auth import require_auth
from core.security import check_content_safety
from core.error_utils import safe_error

logger = logging.getLogger(__name__)
router = APIRouter()


# ═══════════════════════════════════════════════════════════
#  KITE AI (Art.14)
# ═══════════════════════════════════════════════════════════

@router.get("/api/kite/info")
async def kite_info():
    from integrations.kiteai_client import kite_client
    return {
        "platform": "kite-ai",
        "agentId": kite_client.agent_id or "not-registered",
        "apiConfigured": bool(kite_client.api_key),
        "features": ["agent_identity", "agent_payments", "service_discovery", "poai"],
    }


@router.post("/api/kite/register-agent")
async def kite_register(req: dict, wallet: str = Depends(require_auth)):
    from integrations.kiteai_client import kite_client
    return await kite_client.register_agent(
        name=req.get("name", f"MAXIA-{wallet[:8]}"),
        capabilities=req.get("capabilities", ["ai_inference", "data", "gpu"]),
        metadata={"wallet": wallet, "platform": "maxia"},
    )


@router.get("/api/kite/verify-agent/{agent_id}")
async def kite_verify(agent_id: str):
    from integrations.kiteai_client import kite_client
    return await kite_client.verify_agent(agent_id)


@router.post("/api/kite/pay")
async def kite_pay(req: dict, wallet: str = Depends(require_auth)):
    from integrations.kiteai_client import kite_client
    from core.database import db
    result = await kite_client.create_payment(
        to_agent=req.get("to_agent", ""),
        amount_usdc=float(req.get("amount_usdc", 0)),
        purpose=req.get("purpose", "service"),
    )
    if result.get("success"):
        await db.record_transaction(
            wallet, result.get("txHash", ""),
            float(req.get("amount_usdc", 0)), "kite_payment",
        )
    return result


@router.get("/api/kite/discover")
async def kite_discover(category: str = None, max_price: float = None):
    from integrations.kiteai_client import kite_client
    return await kite_client.discover_services(category, max_price)


@router.post("/api/kite/poai")
async def kite_poai(req: dict, wallet: str = Depends(require_auth)):
    from integrations.kiteai_client import kite_client
    return await kite_client.report_contribution(
        task_id=req.get("task_id", ""),
        result_hash=req.get("result_hash", ""),
        model_used=req.get("model", "gemini-2.0-flash"),
    )


# ═══════════════════════════════════════════════════════════
#  AP2 — Google Agent Payments Protocol (Art.15)
# ═══════════════════════════════════════════════════════════

@router.get("/api/ap2/info")
async def ap2_info():
    from integrations.ap2_manager import ap2_manager
    return ap2_manager.get_info()


@router.get("/api/ap2/stats")
async def ap2_stats():
    from integrations.ap2_manager import ap2_manager
    return ap2_manager.get_stats()


@router.post("/api/ap2/mandate/intent")
async def ap2_create_intent(req: dict, wallet: str = Depends(require_auth)):
    from integrations.ap2_manager import ap2_manager
    return ap2_manager.create_intent_mandate(
        user_wallet=wallet,
        max_amount=float(req.get("max_amount", 1000)),
        categories=req.get("categories"),
        ttl_seconds=int(req.get("ttl_seconds", 3600)),
    )


@router.post("/api/ap2/mandate/cart")
async def ap2_create_cart(req: dict, wallet: str = Depends(require_auth)):
    from integrations.ap2_manager import ap2_manager
    return ap2_manager.create_cart_mandate(
        intent_mandate_id=req.get("intent_mandate_id", ""),
        items=req.get("items", []),
        total_usdc=float(req.get("total_usdc", 0)),
        payment_method=req.get("payment_method", "usdc_solana"),
    )


@router.post("/api/ap2/pay")
async def ap2_pay_incoming(request: Request):
    """Accept incoming AP2 payment from external agent."""
    from integrations.ap2_manager import ap2_manager
    from core.models import AP2PaymentRequest
    body = await request.json()
    req = AP2PaymentRequest(**body)
    # #7: Content safety on any string fields
    if hasattr(req, 'network') and req.network:
        check_content_safety(req.network, "network")
    return await ap2_manager.process_payment(
        intent_mandate=req.intent_mandate,
        cart_mandate=req.cart_mandate,
        payment_payload=req.payment_payload,
        network=req.network,
    )


@router.post("/api/ap2/pay-external")
async def ap2_pay_outgoing(req: dict, wallet: str = Depends(require_auth)):
    """Use AP2 to pay for an external agent service."""
    from integrations.ap2_manager import ap2_manager
    # #7: Content safety on purpose field
    purpose = req.get("purpose", "ai_service")
    if purpose:
        check_content_safety(purpose, "purpose")
    # #7: SSRF validation on service_url
    service_url = req.get("service_url", "")
    if service_url:
        from integrations.webhook_dispatcher import validate_callback_url
        validate_callback_url(service_url)
    return await ap2_manager.pay_external(
        service_url=service_url,
        amount_usdc=float(req.get("amount_usdc", 0)),
        user_wallet=wallet,
        provider_wallet=req.get("provider_wallet", ""),
        purpose=purpose,
    )
