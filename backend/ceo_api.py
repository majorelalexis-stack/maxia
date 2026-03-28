"""CEO MAXIA — API routes extracted from main.py."""
import time
import json

from fastapi import APIRouter, HTTPException, Request
from error_utils import safe_error
from database import db

router = APIRouter(prefix="/api/ceo", tags=["ceo"])


# ═══════════════════════════════════════════════════════════
#  CEO MAXIA — API endpoints
# ═══════════════════════════════════════════════════════════

@router.get("/status")
async def ceo_status(request: Request):
    from security import require_admin
    require_admin(request)
    try:
        from ceo_maxia import ceo
        return ceo.get_status()
    except Exception as e:
        return {"error": "An error occurred", "ceo": "not_loaded"}


@router.post("/message")
async def ceo_message(request: Request):
    """Envoie un message au CEO — il repond automatiquement."""
    from security import require_admin
    require_admin(request)
    try:
        from ceo_maxia import ceo
        body = await request.json()
        if len(str(body)) > 50000:
            raise HTTPException(400, "Payload too large")
        canal = body.get("canal", "api")
        user = body.get("user", "anonymous")
        message = body.get("message", "")
        if not message:
            return {"error": "message required"}
        response = await ceo.handle_message(canal, user, message)
        return response
    except Exception as e:
        return safe_error(e, "operation")


@router.post("/feedback")
async def ceo_feedback(request: Request):
    """Envoie un feedback client au CEO (TESTIMONIAL)."""
    from security import require_admin
    require_admin(request)
    try:
        from ceo_maxia import ceo
        body = await request.json()
        if len(str(body)) > 50000:
            raise HTTPException(400, "Payload too large")
        user = body.get("user", "anonymous")
        feedback = body.get("feedback", "")
        if not feedback:
            return {"error": "feedback required"}
        return await ceo.handle_feedback(user, feedback)
    except Exception as e:
        return safe_error(e, "operation")


@router.post("/ping")
async def ceo_ping(request: Request):
    """Le fondateur signale sa presence."""
    from security import require_admin
    require_admin(request)
    try:
        from ceo_maxia import ceo
        ceo.fondateur_ping()
        return {"status": "ok", "message": "Fondateur ping recu"}
    except Exception as e:
        return safe_error(e, "operation")


# ══════════════════════════════════════════
#  CEO — Nouvelles fonctions (NEGOTIATOR, COMPLIANCE, PARTNERSHIP, ANALYTICS, CRISIS)
# ══════════════════════════════════════════

@router.post("/negotiate")
async def ceo_negotiate(request: Request):
    """Negociation automatique de prix avec un agent acheteur."""
    from security import require_admin
    require_admin(request)
    try:
        body = await request.json()
        if len(str(body)) > 50000:
            raise HTTPException(400, "Payload too large")
        from ceo_maxia import ceo
        return await ceo.negotiate_price(
            body.get("buyer", ""),
            body.get("service", ""),
            float(body.get("proposed_price", 0)),
        )
    except Exception as e:
        return safe_error(e, "operation")


@router.post("/negotiate/bundle")
async def ceo_negotiate_bundle(request: Request):
    """Negociation de pack de services avec remise volume."""
    from security import require_admin
    require_admin(request)
    try:
        body = await request.json()
        from ceo_maxia import ceo
        return await ceo.negotiate_bundle(
            body.get("buyer", ""),
            body.get("services", []),
        )
    except Exception as e:
        return safe_error(e, "operation")


@router.post("/compliance/wallet")
async def ceo_compliance_wallet(request: Request):
    """Verifie la conformite AML d'un wallet."""
    from security import require_admin
    require_admin(request)
    try:
        body = await request.json()
        from ceo_maxia import ceo
        return await ceo.check_wallet(body.get("wallet", ""))
    except Exception as e:
        return safe_error(e, "operation")


@router.post("/compliance/transaction")
async def ceo_compliance_tx(request: Request):
    """Verifie la conformite d'une transaction."""
    from security import require_admin
    require_admin(request)
    try:
        body = await request.json()
        from ceo_maxia import ceo
        return await ceo.check_transaction(
            float(body.get("amount", 0)),
            body.get("sender", ""),
            body.get("receiver", ""),
        )
    except Exception as e:
        return safe_error(e, "operation")


@router.get("/partnerships")
async def ceo_partnerships(request: Request):
    """Liste les opportunites de partenariat detectees."""
    from security import require_admin
    require_admin(request)
    try:
        from ceo_maxia import ceo
        return {"opportunities": await ceo.scan_partners()}
    except Exception as e:
        return safe_error(e, "operation")


@router.get("/analytics")
async def ceo_analytics(request: Request):
    """Metriques avancees : LTV, churn, funnel, health score."""
    from security import require_admin
    require_admin(request)
    try:
        from ceo_maxia import ceo
        return await ceo.get_analytics()
    except Exception as e:
        return safe_error(e, "operation")


@router.get("/analytics/weekly")
async def ceo_analytics_weekly(request: Request):
    """Rapport hebdomadaire enrichi pour le fondateur."""
    from security import require_admin
    require_admin(request)
    try:
        from ceo_maxia import ceo
        return await ceo.weekly_report()
    except Exception as e:
        return safe_error(e, "operation")


@router.get("/crises")
async def ceo_crises(request: Request):
    """Detecte les crises en cours."""
    from security import require_admin
    require_admin(request)
    try:
        from ceo_maxia import ceo
        crises = await ceo.detect_crises()
        return {"crises": crises, "count": len(crises)}
    except Exception as e:
        return safe_error(e, "operation")


@router.get("/agent-bus")
async def ceo_agent_bus(request: Request):
    """Statistiques du bus inter-agents."""
    from security import require_admin
    require_admin(request)
    try:
        from ceo_maxia import agent_bus
        return agent_bus.get_stats()
    except Exception as e:
        return safe_error(e, "operation")


# ═══════════════════════════════════════════════════════════
#  CEO AUTONOME — Endpoints securises PC local <-> VPS
# ═══════════════════════════════════════════════════════════

# Rate limit CEO endpoints: 30 req/min per IP
_ceo_rate: dict = {}
_CEO_RATE_LIMIT = 30
_CEO_RATE_WINDOW = 60


def _check_ceo_rate(ip: str):
    now = time.time()
    # Hard cap: if dict exceeds 1000 entries, prune all stale and force cleanup
    if len(_ceo_rate) > 1000:
        stale_ips = [k for k, v in _ceo_rate.items() if not v or v[-1] < now - _CEO_RATE_WINDOW * 2]
        for k in stale_ips:
            _ceo_rate.pop(k, None)
        # If still over limit after pruning, clear everything
        if len(_ceo_rate) > 1000:
            _ceo_rate.clear()
    _ceo_rate.setdefault(ip, [])
    _ceo_rate[ip] = [t for t in _ceo_rate[ip] if t > now - _CEO_RATE_WINDOW]
    if len(_ceo_rate[ip]) >= _CEO_RATE_LIMIT:
        raise HTTPException(429, "CEO API rate limit: 30 req/min")
    _ceo_rate[ip].append(now)
    # Prune stale IPs
    if len(_ceo_rate) > 500:
        stale_ips = [k for k, v in _ceo_rate.items() if not v or v[-1] < now - _CEO_RATE_WINDOW * 2]
        for k in stale_ips:
            _ceo_rate.pop(k, None)


@router.get("/state")
async def ceo_full_state(request: Request):
    """Etat complet du VPS pour le CEO local."""
    from auth import require_ceo_auth
    _check_ceo_rate(request.client.host if request.client else "?")
    await require_ceo_auth(request, request.headers.get("X-CEO-Key"))
    try:
        from ceo_maxia import ceo, get_llm_costs
        status = ceo.get_status()
        mem = ceo.memory._data
        # Compter les vrais services actifs depuis la DB (pas la memoire CEO)
        try:
            stats = await db.get_marketplace_stats()
            services_count = stats.get("services_listed", 0)
        except Exception:
            services_count = mem.get("services", 0)
        return {
            "kpi": {
                "revenue_24h": mem.get("revenue_usd", 0),
                "clients_actifs": mem.get("clients", 0),
                "services_actifs": services_count,
                "emergency_stop": mem.get("emergency_stop", False),
                "budget_vert": mem.get("budget_vert", 0),
            },
            "agents": {name: mem.get("agents", {}).get(name, {})
                       for name in ["GHOST-WRITER", "HUNTER", "SCOUT", "WATCHDOG",
                                    "NEGOTIATOR", "COMPLIANCE", "PARTNERSHIP", "ANALYTICS"]},
            "errors": mem.get("erreurs_recurrentes", [])[-10:],
            "decisions_recent": mem.get("decisions", [])[-20:],
            "llm_costs": get_llm_costs(),
            "cycle": status.get("cycle", 0),
            # Scout data pour le CEO local
            "onchain_agents": getattr(ceo, "_onchain_agents", [])[-20:],
            "contacts_pending": [c for c in getattr(ceo, "_scout_contacts", [])
                                if c.get("response") == "pending"][-10:],
        }
    except Exception as e:
        return safe_error(e, "operation")


@router.post("/execute")
async def ceo_execute_action(request: Request):
    """Executer une action decidee par le CEO local."""
    from auth import require_ceo_auth
    await require_ceo_auth(request, request.headers.get("X-CEO-Key"))
    from security import check_ceo_spending_limit, record_ceo_action, audit_log

    body = await request.json()
    action = body.get("action", "")
    agent = body.get("agent", "")
    params = body.get("params", {})
    priority = body.get("priority", "vert")
    ip = request.client.host if request.client else "unknown"

    if not action:
        raise HTTPException(400, "action required")

    # Verifier les limites
    amount = params.get("amount_usd", 0)
    check = check_ceo_spending_limit(action, amount)
    if not check["allowed"]:
        audit_log("ceo_execute_blocked", ip, f"{action}: {check['reason']}", "ceo-local")
        return {"success": False, "error": check["reason"]}

    # ROUGE = jamais auto-execute depuis le PC
    if priority == "rouge":
        audit_log("ceo_execute_rouge_blocked", ip, f"{action} blocked (rouge)", "ceo-local")
        return {"success": False, "error": "ROUGE actions cannot be auto-executed"}

    # Scout actions — gérées directement sans passer par ceo_executor
    if action == "scout_contact":
        try:
            from ceo_maxia import ceo, scout_first_contact_a2a
            addr = params.get("agent_address", "")
            chain = params.get("chain", "")
            pitch = params.get("pitch", "")
            result = await scout_first_contact_a2a(addr, chain, pitch)
            if result.get("success"):
                ceo._scout_contacts.append(result.get("contact", {}))
            audit_log("scout_contact", ip, f"{addr[:10]}... on {chain}", "ceo-local")
            return result
        except Exception as e:
            return {"success": False, "error": "An error occurred"}

    if action == "scout_scan":
        try:
            from ceo_maxia import ceo, scout_scan_onchain_agents
            chain = params.get("chain", "all")
            agents = await scout_scan_onchain_agents(ceo.memory)
            ceo._onchain_agents = agents
            audit_log("scout_scan", ip, f"{len(agents)} agents on {chain}", "ceo-local")
            return {"success": True, "agents": agents[:20]}
        except Exception as e:
            return {"success": False, "error": "An error occurred"}

    # Executer via ceo_executor
    try:
        from ceo_maxia import ceo
        from ceo_executor import execute_decision
        decision = {
            "action": _build_action_string(action, params),
            "cible": agent.upper(),
            "priorite": priority.upper(),
        }
        result = await execute_decision(decision, ceo.memory, db)
        record_ceo_action(action)
        audit_log("ceo_execute", ip, f"{action} -> {agent}: {result}", "ceo-local")
        return {
            "success": result.get("executed", False),
            "result": result.get("detail", result.get("reason", "")),
            "tx_id": result.get("action_id"),
        }
    except Exception as e:
        audit_log("ceo_execute_error", ip, str(e), "ceo-local")
        return {"success": False, "error": "An error occurred"}


@router.post("/update-price")
async def ceo_update_price(request: Request):
    """Modifier le prix d'un service."""
    from auth import require_ceo_auth
    await require_ceo_auth(request, request.headers.get("X-CEO-Key"))
    from security import audit_log
    ip = request.client.host if request.client else "unknown"

    body = await request.json()
    service_id = body.get("service_id")
    new_price = body.get("new_price")
    reason = body.get("reason", "CEO decision")

    if new_price is None:
        raise HTTPException(400, "new_price required")

    try:
        await db.update_service(service_id, {"price_usdc": float(new_price)})
        audit_log("ceo_update_price", ip, f"service={service_id} price={new_price} reason={reason}", "ceo-local")
        return {"success": True, "service_id": service_id, "new_price": new_price}
    except Exception as e:
        return {"success": False, "error": "An error occurred"}


@router.post("/toggle-agent")
async def ceo_toggle_agent(request: Request):
    """Activer/desactiver un sous-agent."""
    from auth import require_ceo_auth
    await require_ceo_auth(request, request.headers.get("X-CEO-Key"))
    from security import audit_log
    ip = request.client.host if request.client else "unknown"

    body = await request.json()
    agent_name = body.get("agent_name", "").upper()
    enabled = body.get("enabled", True)

    try:
        from ceo_maxia import ceo
        if enabled:
            ceo.memory.enable_agent(agent_name)
        else:
            ceo.memory.disable_agent(agent_name, "Disabled by CEO local")
        audit_log("ceo_toggle_agent", ip, f"{agent_name} -> {'enabled' if enabled else 'disabled'}", "ceo-local")
        return {"success": True, "agent": agent_name, "enabled": enabled}
    except Exception as e:
        return {"success": False, "error": "An error occurred"}


@router.get("/transactions")
async def ceo_transactions(request: Request, limit: int = 50):
    """Dernieres transactions pour analyse."""
    from auth import require_ceo_auth
    await require_ceo_auth(request, request.headers.get("X-CEO-Key"))
    try:
        rows = await db.get_activity(limit)
        return {"transactions": rows, "count": len(rows)}
    except Exception as e:
        return {"error": "An error occurred", "transactions": []}


@router.get("/approval-result/{action_id}")
async def ceo_approval_result(action_id: str, request: Request):
    """Resultat d'approbation Telegram pour le CEO Local. Retourne approved/denied/pending."""
    from auth import require_ceo_auth
    await require_ceo_auth(request, request.headers.get("X-CEO-Key"))
    from telegram_bot import get_approval_result
    result = get_approval_result(action_id)
    return {"action_id": action_id, "result": result or "pending"}


def _build_action_string(action: str, params: dict) -> str:
    """Construit une string d'action pour le ceo_executor existant."""
    if action == "post_tweet":
        return f"tweet: {params.get('text', '')}"
    elif action == "update_price":
        return f"adjust price service {params.get('service_id', '')} to {params.get('new_price', '')}: {params.get('reason', '')}"
    elif action == "contact_prospect":
        return f"contact wallet {params.get('wallet', '')} via {params.get('canal', 'solana_memo')}: {params.get('message', '')}"
    elif action == "toggle_agent":
        return f"{'enable' if params.get('enabled', True) else 'disable'} agent {params.get('agent_name', '')}"
    elif action == "send_alert":
        return f"alert: {params.get('message', '')}"
    elif action == "deploy_page":
        return f"deploy blog: {params.get('title', 'MAXIA Update')}"
    elif action == "generate_report":
        return f"generate report: {params.get('topic', 'weekly')}"
    else:
        return f"{action}: {json.dumps(params, default=str)[:200]}"
