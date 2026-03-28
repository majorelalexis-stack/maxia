"""CEO MAXIA — API routes extracted from main.py."""
import os
import re
import time
import json
import hashlib

from fastapi import APIRouter, HTTPException, Request
from error_utils import safe_error
def _get_db():
    """Lazy DB import to avoid stale singleton reference."""
    import database
    return database.db

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
            stats = await _get_db().get_marketplace_stats()
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
        await _get_db().update_service(service_id, {"price_usdc": float(new_price)})
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
        rows = await _get_db().get_activity(limit)
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


# ═══════════════════════════════════════════════════════════
#  CEO — Health, Emergency, Sync, Think
# ═══════════════════════════════════════════════════════════

@router.get("/health")
async def ceo_health_check(request: Request):
    """Sante de tous les composants."""
    from auth import require_ceo_auth
    await require_ceo_auth(request, request.headers.get("X-CEO-Key"))
    try:
        from ceo_maxia import ceo, get_llm_costs
        from llm_router import router as llm_router

        health = {
            "healthy": True,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "components": {
                "database": "ok",
                "ceo": "running" if ceo._running else "stopped",
                "emergency_stop": ceo.memory.is_stopped(),
                "llm_costs": get_llm_costs(),
                "router_stats": llm_router.get_stats(),
            },
        }
        # Check DB
        try:
            await _get_db().get_stats()
        except Exception:
            health["components"]["database"] = "error"
            health["healthy"] = False

        return health
    except Exception as e:
        return {"healthy": False, "error": "An error occurred"}


@router.post("/emergency-stop")
async def ceo_emergency_stop(request: Request):
    """Arret d'urgence du CEO."""
    from auth import require_ceo_auth
    await require_ceo_auth(request, request.headers.get("X-CEO-Key"))
    from security import audit_log
    from alerts import alert_system
    ip = request.client.host if request.client else "unknown"

    try:
        from ceo_maxia import ceo
        ceo.memory._data["emergency_stop"] = True
        ceo.memory.save()
        audit_log("ceo_emergency_stop", ip, "Emergency stop activated by CEO local", "ceo-local")
        await alert_system.send("CEO EMERGENCY STOP", "Activated by CEO local agent")
        return {"success": True, "emergency_stop": True}
    except Exception as e:
        return {"success": False, "error": "An error occurred"}


# ── Coordination locale ↔ VPS ──

_local_ceo_state = {
    "active": False,
    "last_sync": 0,
    "recent_actions": [],  # Actions faites par le CEO local
}


@router.post("/sync")
async def ceo_sync(request: Request):
    """Synchronisation CEO local <-> VPS. Evite les double-posts."""
    from auth import require_ceo_auth
    await require_ceo_auth(request, request.headers.get("X-CEO-Key"))

    body = await request.json()
    local_actions = body.get("actions", [])
    local_active = body.get("active", False)

    # Enregistrer l'etat du CEO local
    _local_ceo_state["active"] = local_active
    _local_ceo_state["last_sync"] = time.time()
    # Garder les 100 dernieres actions locales
    _local_ceo_state["recent_actions"].extend(local_actions)
    _local_ceo_state["recent_actions"] = _local_ceo_state["recent_actions"][-100:]

    # Retourner les actions recentes du VPS CEO
    try:
        from ceo_maxia import ceo
        vps_actions = ceo.memory._data.get("decisions", [])[-20:]
        return {
            "vps_actions": vps_actions,
            "local_registered": len(local_actions),
            "vps_marketing_paused": _local_ceo_state["active"],
        }
    except Exception as e:
        return safe_error(e, "operation")


def is_local_ceo_active() -> bool:
    """Le VPS CEO verifie si le local est actif (sync < 15 min)."""
    return _local_ceo_state["active"] and time.time() - _local_ceo_state["last_sync"] < 900


def local_ceo_did_action(action_type: str) -> bool:
    """Verifie si le CEO local a deja fait cette action recemment."""
    for a in _local_ceo_state["recent_actions"][-50:]:
        if a.get("action") == action_type:
            return True
    return False


# ── Think (LLM delegation) ──

_think_cache: dict = {}


def _normalize_for_cache(prompt: str) -> str:
    """Normalise un prompt pour le cache semantique.
    Supprime les chiffres volatils (timestamps, montants exacts) pour
    que des prompts similaires matchent le meme cache."""
    n = prompt[:500].lower()
    # Remplacer les nombres par des placeholders
    n = re.sub(r'\$[\d.]+', '$X', n)
    n = re.sub(r'\d{4}-\d{2}-\d{2}', 'DATE', n)
    n = re.sub(r'\d{2}:\d{2}', 'TIME', n)
    n = re.sub(r'=\d+', '=N', n)
    # Supprimer les espaces multiples
    n = re.sub(r'\s+', ' ', n).strip()
    return n


def _compress_prompt(prompt: str) -> str:
    """Compresse un prompt pour economiser des tokens Claude.
    - Arrondit les chiffres a 2 decimales
    - Supprime les lignes vides en double
    - Tronque a 3000 chars max
    """
    # Arrondir les nombres longs
    compressed = re.sub(r'(\d+\.\d{3,})', lambda m: f"{float(m.group()):.2f}", prompt)
    # Supprimer les lignes vides en double
    compressed = re.sub(r'\n{3,}', '\n\n', compressed)
    # Supprimer les espaces en trop
    compressed = re.sub(r'  +', ' ', compressed)
    return compressed[:3000]


@router.post("/think")
async def ceo_think(request: Request):
    """Le CEO local delegue une reflexion strategique a Claude sur le VPS.
    Evite de payer Claude 2x — le local envoie le prompt, le VPS reflechit."""
    from auth import require_ceo_auth
    await require_ceo_auth(request, request.headers.get("X-CEO-Key"))
    from security import audit_log

    body = await request.json()
    prompt = body.get("prompt", "")
    tier = body.get("tier", "fast")  # fast|mid|strategic
    max_tokens = min(body.get("max_tokens", 1000), 4000)
    ip = request.client.host if request.client else "unknown"

    if not prompt:
        raise HTTPException(400, "prompt required")

    # Cache semantique: prompts similaires = meme reponse (1h)
    normalized = _normalize_for_cache(prompt)
    prompt_hash = hashlib.sha256(normalized.encode()).hexdigest()[:12]
    cache_key = f"ceo_think_{prompt_hash}"
    cached = _think_cache.get(cache_key)
    if cached and time.time() - cached["ts"] < 3600:  # Cache 1h
        audit_log("ceo_think_cached", ip, f"tier={tier} hash={prompt_hash}", "ceo-local")
        return {"result": cached["result"], "tier": tier, "cached": True, "cost_usd": 0}

    try:
        from llm_router import router as llm_router, Tier
        from ceo_maxia import CEO_IDENTITY

        tier_map = {"fast": Tier.FAST, "mid": Tier.MID, "strategic": Tier.STRATEGIC}
        llm_tier = tier_map.get(tier, Tier.FAST)

        # Compresser le prompt: arrondir les chiffres, limiter la taille
        clean_prompt = _compress_prompt(prompt)

        result = await llm_router.call(
            clean_prompt, tier=llm_tier,
            system=CEO_IDENTITY, max_tokens=max_tokens,
        )

        # Cache le resultat
        _think_cache[cache_key] = {"result": result, "ts": time.time()}
        # Nettoyer le cache (max 50 entrees)
        if len(_think_cache) > 50:
            oldest = sorted(_think_cache.items(), key=lambda x: x[1]["ts"])[:25]
            for k, _ in oldest:
                _think_cache.pop(k, None)

        cost = llm_router.costs_today.get(tier, {}).get("cost", 0)
        audit_log("ceo_think", ip, f"tier={tier} tokens~{len(result)//4} hash={prompt_hash}", "ceo-local")
        return {"result": result, "tier": tier, "cached": False, "cost_usd": round(cost, 4)}
    except Exception as e:
        return safe_error(e, "operation")


# ═══════════════════════════════════════════════════════════
#  CEO — Admin-facing (disabled-agents, ROI, A/B, LLM costs)
# ═══════════════════════════════════════════════════════════

@router.get("/disabled-agents")
async def ceo_disabled(request: Request):
    """Liste les agents desactives."""
    from security import require_admin
    require_admin(request)
    from ceo_maxia import ceo
    return {"disabled": ceo.get_disabled_agents()}


@router.get("/roi")
async def ceo_roi(request: Request):
    """Stats ROI par agent et par type d'action."""
    from security import require_admin
    require_admin(request)
    from ceo_maxia import ceo
    return ceo.get_roi()


@router.get("/ab-tests")
async def ceo_ab_tests(request: Request):
    """Resultats des tests A/B en cours."""
    from security import require_admin
    require_admin(request)
    from ceo_maxia import ceo
    return ceo.get_ab_results()


@router.post("/ab-tests")
async def ceo_create_ab_test(request: Request):
    """Cree un nouveau test A/B."""
    from security import require_admin
    require_admin(request)
    body = await request.json()
    from ceo_maxia import ceo
    ceo.create_test(body.get("name", ""), body.get("variant_a", ""), body.get("variant_b", ""))
    return {"success": True}


@router.get("/llm-costs")
async def ceo_llm_costs(request: Request):
    """LLM token usage and estimated cost per model."""
    from security import require_admin
    require_admin(request)
    from ceo_maxia import get_llm_costs
    return get_llm_costs()


# ═══════════════════════════════════════════════════════════
#  CEO — Ask (chat with CEO)
# ═══════════════════════════════════════════════════════════

@router.post("/ask")
async def ceo_ask(request: Request):
    """Chat with the CEO MAXIA. Ask questions, give orders, get updates."""
    from security import require_admin
    require_admin(request)
    try:
        body = await request.json()
        message = body.get("message", body.get("text", ""))
        if not message:
            return {"error": "message required"}
        if len(message) > 2000:
            raise HTTPException(400, "Message too long (max 2000 chars)")
        # Enrichir avec le contexte reel du CEO
        try:
            from ceo_maxia import ceo
            status = ceo.get_status()
            context = (
                f"Revenue 24h: {status.get('stats', {}).get('revenue_24h', 0)} USDC | "
                f"Clients actifs: {status.get('stats', {}).get('active_clients', 0)} | "
                f"Services: {status.get('stats', {}).get('services_count', 0)} | "
                f"Cycle: {status.get('cycle', 0)} | "
                f"Emergency: {status.get('emergency_stop', False)} | "
                f"Agents actifs: {len([a for a in status.get('agents', {}).values() if a.get('enabled')])} / 17"
            )
        except Exception:
            context = "Status indisponible"

        from groq import Groq
        c = Groq(api_key=os.getenv("GROQ_API_KEY", ""))
        resp = c.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": (
                    "Tu es le CEO de MAXIA, un marketplace AI-to-AI sur 14 blockchains (maxiaworld.app). "
                    "Tu geres 17 sous-agents, le marketing, le WATCHDOG, et la strategie. "
                    "Tu reponds au FONDATEUR Alexis. Sois direct, concis, strategique. "
                    "Reponds en texte simple, PAS en JSON. En francais.\n\n"
                    f"ETAT ACTUEL: {context}"
                )},
                {"role": "user", "content": message},
            ],
            max_tokens=500,
        )
        raw = resp.choices[0].message.content
        return {"success": True, "from": "CEO MAXIA", "response": raw, "context": context}
    except Exception as e:
        return safe_error(e, "operation")


# ═══════════════════════════════════════════════════════════
#  CEO — Vector Memory
# ═══════════════════════════════════════════════════════════

@router.get("/memory")
async def ceo_memory_stats(request: Request):
    """Get CEO vector memory statistics."""
    from security import require_admin
    require_admin(request)
    try:
        from ceo_vector_memory import vector_memory
        return vector_memory.stats()
    except Exception as e:
        return safe_error(e, "operation")


@router.get("/memory/search")
async def ceo_memory_search(request: Request, q: str = "", collection: str = ""):
    """Search CEO memory. Example: /api/ceo/memory/search?q=whale+conversion"""
    from security import require_admin
    require_admin(request)
    try:
        from ceo_vector_memory import vector_memory
        results = vector_memory.search(q, collection=collection or None, max_results=5)
        return {"query": q, "results": results}
    except Exception as e:
        return safe_error(e, "operation")


# ═══════════════════════════════════════════════════════════
#  APPROVALS — Dashboard approval system
# ═══════════════════════════════════════════════════════════

@router.get("/approvals")
async def ceo_get_approvals(request: Request):
    """Liste les decisions en attente d'approbation (orange/rouge)."""
    from security import require_admin
    require_admin(request)
    from ceo_maxia import ceo
    pending = ceo.memory._data.get("pending_approvals", [])
    active = [p for p in pending if p.get("status") == "pending"]
    history = [p for p in pending if p.get("status") != "pending"][-20:]
    return {"pending": active, "history": history, "count": len(active)}


@router.post("/approvals/{approval_id}/approve")
async def ceo_approve(approval_id: str, request: Request):
    """Approuve une decision en attente et l'execute."""
    from security import require_admin
    require_admin(request)
    from ceo_maxia import ceo
    pending = ceo.memory._data.get("pending_approvals", [])
    found = None
    for p in pending:
        if p.get("id") == approval_id and p.get("status") == "pending":
            found = p
            break
    if not found:
        raise HTTPException(404, "Approval not found or already processed")

    # Execute la decision
    decision = {
        "action": found["action"],
        "cible": found["cible"],
        "priorite": "VERT",  # Force VERT pour bypass les checks
    }
    try:
        from ceo_executor import execute_decision
        result = await execute_decision(decision, ceo.memory, db)
        found["status"] = "approved"
        found["approved_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        found["result"] = str(result.get("detail", result.get("reason", "")))[:200]
        ceo.memory.save()
        ceo.memory.fondateur_responded()
        ceo.memory.log_decision("vert", f"APPROVED: {found['action']}", "fondateur", found["cible"])
        return {"success": True, "id": approval_id, "result": result}
    except Exception as e:
        found["status"] = "error"
        found["error"] = str(e)[:200]
        ceo.memory.save()
        raise HTTPException(500, "Internal server error")


@router.post("/approvals/{approval_id}/deny")
async def ceo_deny(approval_id: str, request: Request):
    """Refuse une decision en attente."""
    from security import require_admin
    require_admin(request)
    from ceo_maxia import ceo
    pending = ceo.memory._data.get("pending_approvals", [])
    found = None
    for p in pending:
        if p.get("id") == approval_id and p.get("status") == "pending":
            found = p
            break
    if not found:
        raise HTTPException(404, "Approval not found or already processed")

    found["status"] = "denied"
    found["denied_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    ceo.memory.save()
    ceo.memory.fondateur_responded()
    ceo.memory.log_decision("vert", f"DENIED: {found['action']}", "fondateur", found["cible"])
    return {"success": True, "id": approval_id, "status": "denied"}


# ═══════════════════════════════════════════════════════════
#  CEO — Web Analytics & Twitter Analytics
# ═══════════════════════════════════════════════════════════

@router.get("/analytics/web")
async def ceo_web_analytics(request: Request):
    """Web analytics for CEO — visitors, signups, referrers, conversion."""
    from auth import require_ceo_auth
    await require_ceo_auth(request, request.headers.get("X-CEO-Key"))

    try:
        now = int(time.time())
        h24 = now - 86400
        h7d = now - 604800

        # Signups last 24h and 7d
        signups_24h = await _get_db().raw_execute_fetchall(
            "SELECT COUNT(*) as cnt FROM agents WHERE created_at > ?", (h24,))
        signups_7d = await _get_db().raw_execute_fetchall(
            "SELECT COUNT(*) as cnt FROM agents WHERE created_at > ?", (h7d,))
        total_agents = await _get_db().raw_execute_fetchall(
            "SELECT COUNT(*) as cnt FROM agents")

        # Transactions last 24h
        tx_24h = await _get_db().raw_execute_fetchall(
            "SELECT COUNT(*) as cnt FROM marketplace_tx WHERE created_at > ?", (h24,))

        # Revenue last 24h
        revenue = await _get_db().raw_execute_fetchall(
            "SELECT COALESCE(SUM(commission_usdc), 0) as total FROM marketplace_tx WHERE created_at > ?", (h24,))

        # Active services
        services = await _get_db().raw_execute_fetchall(
            "SELECT COUNT(*) as cnt FROM agent_services WHERE status='active'")

        # Recent signups (last 5)
        recent = await _get_db().raw_execute_fetchall(
            "SELECT name, wallet, created_at FROM agents ORDER BY created_at DESC LIMIT 5")

        return {
            "signups_24h": signups_24h[0]["cnt"] if signups_24h else 0,
            "signups_7d": signups_7d[0]["cnt"] if signups_7d else 0,
            "total_agents": total_agents[0]["cnt"] if total_agents else 0,
            "transactions_24h": tx_24h[0]["cnt"] if tx_24h else 0,
            "revenue_24h_usdc": float(revenue[0]["total"]) if revenue else 0,
            "active_services": services[0]["cnt"] if services else 0,
            "recent_signups": [
                {"name": r["name"], "wallet": r["wallet"][:8] + "...", "created_at": r["created_at"]}
                for r in (recent or [])
            ],
            "conversion_note": "Visitors data requires nginx log parsing — TODO",
            "ts": now,
        }
    except Exception as e:
        return safe_error(e, "ceo_web_analytics")


@router.get("/analytics/twitter")
async def ceo_twitter_analytics(request: Request):
    """Twitter performance stats for CEO strategy — reads from conversions.json."""
    from auth import require_ceo_auth
    await require_ceo_auth(request, request.headers.get("X-CEO-Key"))

    from pathlib import Path

    stats: dict = {"tweets": {}, "comments": {}, "platform_scores": {}}

    # Read conversions.json (CEO local tracks success/failure per action)
    conv_path = Path(__file__).parent.parent / "local_ceo" / "conversions.json"
    if conv_path.exists():
        try:
            with open(conv_path) as f:
                conv = json.load(f)
            # Extract Twitter-specific stats
            for action, data in conv.items():
                if "twitter" in action.lower() or "tweet" in action.lower():
                    stats["tweets"][action] = data
                if "comment" in action.lower() and "twitter" in action.lower():
                    stats["comments"][action] = data
        except Exception:
            pass

    # Read platform_scores.json
    scores_path = Path(__file__).parent.parent / "local_ceo" / "platform_scores.json"
    if scores_path.exists():
        try:
            with open(scores_path) as f:
                stats["platform_scores"] = json.load(f)
        except Exception:
            pass

    # Read actions_today.json for daily counts
    actions_path = Path(__file__).parent.parent / "local_ceo" / "actions_today.json"
    if actions_path.exists():
        try:
            with open(actions_path) as f:
                stats["actions_today"] = json.load(f)
        except Exception:
            pass

    # Read learnings.json for strategic insights
    learn_path = Path(__file__).parent.parent / "local_ceo" / "learnings.json"
    if learn_path.exists():
        try:
            with open(learn_path) as f:
                stats["learnings"] = json.load(f)
        except Exception:
            pass

    return stats


# ═══════════════════════════════════════════════════════════
#  CEO — Competitor Intelligence
# ═══════════════════════════════════════════════════════════

@router.get("/competitors")
async def ceo_competitors(request: Request):
    """Competitive intelligence — scrapes competitor stats for CEO strategy."""
    from auth import require_ceo_auth
    await require_ceo_auth(request, request.headers.get("X-CEO-Key"))

    from http_client import get_http_client
    client = get_http_client()
    competitors: dict = {}

    # Virtuals Protocol — agents count
    try:
        r = await client.get("https://api.virtuals.io/api/tokens?limit=1", timeout=10)
        if r.status_code == 200:
            d = r.json()
            competitors["virtuals"] = {
                "name": "Virtuals Protocol",
                "agents": d.get("total", "50000+"),
                "url": "https://virtuals.io",
                "strength": "Token incentives, large community",
            }
    except Exception:
        competitors["virtuals"] = {"name": "Virtuals Protocol", "agents": "50000+", "note": "API unreachable"}

    # ElizaOS — GitHub stars
    try:
        r = await client.get("https://api.github.com/repos/elizaOS/eliza", timeout=10)
        if r.status_code == 200:
            d = r.json()
            competitors["elizaos"] = {
                "name": "ElizaOS",
                "github_stars": d.get("stargazers_count", 0),
                "forks": d.get("forks_count", 0),
                "open_issues": d.get("open_issues_count", 0),
                "url": "https://github.com/elizaOS/eliza",
                "strength": "Open source, huge community",
            }
    except Exception:
        competitors["elizaos"] = {"name": "ElizaOS", "note": "GitHub API unreachable"}

    # Olas/Autonolas — services registered
    try:
        r = await client.get("https://registry.olas.network/api/v1/services?limit=1", timeout=10)
        if r.status_code == 200:
            d = r.json()
            total = d.get("total", len(d.get("results", d.get("services", []))))
            competitors["olas"] = {
                "name": "Autonolas/Olas",
                "registered_services": total,
                "url": "https://olas.network",
                "strength": "On-chain agent registry, tokenomics",
            }
    except Exception:
        competitors["olas"] = {"name": "Autonolas/Olas", "note": "API unreachable"}

    # MAXIA self-assessment
    try:
        agents = await _get_db().raw_execute_fetchall("SELECT COUNT(*) as cnt FROM agents")
        services = await _get_db().raw_execute_fetchall("SELECT COUNT(*) as cnt FROM agent_services WHERE status='active'")
        competitors["maxia"] = {
            "name": "MAXIA (us)",
            "registered_agents": agents[0]["cnt"] if agents else 0,
            "active_services": services[0]["cnt"] if services else 0,
            "chains": 14,
            "mcp_tools": 46,
            "strength": "AI-to-AI marketplace, escrow on-chain, 14 chains",
            "weakness": "0 revenue, 0 paying clients, low visibility",
        }
    except Exception:
        pass

    return {"competitors": competitors, "note": "CEO should compare and identify gaps weekly"}


# ═══════════════════════════════════════════════════════════
#  CEO — Weekly Objectives
# ═══════════════════════════════════════════════════════════

@router.get("/objectives")
async def ceo_get_objectives(request: Request):
    """Get current weekly objective and history."""
    from auth import require_ceo_auth
    await require_ceo_auth(request, request.headers.get("X-CEO-Key"))
    from ceo_objectives import get_objectives, check_pivot_needed
    data = get_objectives()
    data["pivot_check"] = check_pivot_needed()
    return data


@router.post("/objectives/set")
async def ceo_set_objective(request: Request):
    """Set a new weekly objective."""
    from auth import require_ceo_auth
    await require_ceo_auth(request, request.headers.get("X-CEO-Key"))
    body = await request.json()
    from ceo_objectives import set_weekly_objective
    return set_weekly_objective(
        body.get("objective", ""),
        int(body.get("target", 5)),
        body.get("metric", "signups"),
    )


@router.post("/objectives/update")
async def ceo_update_progress(request: Request):
    """Update progress on current objective."""
    from auth import require_ceo_auth
    await require_ceo_auth(request, request.headers.get("X-CEO-Key"))
    body = await request.json()
    from ceo_objectives import update_progress
    return update_progress(
        int(body.get("current", 0)),
        body.get("strategy", ""),
        body.get("action", ""),
    )


# ═══════════════════════════════════════════════════════════
#  CEO — Feedback Loop (all-in-one strategic data)
# ═══════════════════════════════════════════════════════════

@router.get("/feedback-loop")
async def ceo_feedback_loop(request: Request):
    """Complete feedback loop data for CEO strategic reasoning.
    Combines web analytics, Twitter performance, competitors, and objectives
    into a single prompt-ready package for Qwen 14B think=on."""
    from auth import require_ceo_auth
    await require_ceo_auth(request, request.headers.get("X-CEO-Key"))

    # Gather all data sources
    results: dict = {}

    # Web analytics
    try:
        now = int(time.time())
        h24 = now - 86400
        signups = await _get_db().raw_execute_fetchall("SELECT COUNT(*) as cnt FROM agents WHERE created_at > ?", (h24,))
        total = await _get_db().raw_execute_fetchall("SELECT COUNT(*) as cnt FROM agents")
        revenue = await _get_db().raw_execute_fetchall(
            "SELECT COALESCE(SUM(commission_usdc), 0) as total FROM marketplace_tx WHERE created_at > ?", (h24,))
        results["web"] = {
            "signups_24h": signups[0]["cnt"] if signups else 0,
            "total_agents": total[0]["cnt"] if total else 0,
            "revenue_24h": float(revenue[0]["total"]) if revenue else 0,
        }
    except Exception:
        results["web"] = {"error": "DB unavailable"}

    # Objectives
    try:
        from ceo_objectives import get_objectives, check_pivot_needed
        obj = get_objectives()
        results["objective"] = obj.get("current_week")
        results["pivot_check"] = check_pivot_needed()
        results["objective_history"] = obj.get("history", [])[-4:]  # Last 4 weeks
    except Exception:
        results["objective"] = None

    # Conversions (from local CEO files)
    from pathlib import Path
    conv_path = Path(__file__).parent.parent / "local_ceo" / "conversions.json"
    if conv_path.exists():
        try:
            with open(conv_path) as f:
                results["conversions"] = json.load(f)
        except Exception:
            pass

    # Generate CEO prompt
    results["ceo_prompt"] = (
        "You are the CEO of MAXIA, an AI-to-AI marketplace on 14 blockchains. "
        "Analyze the following data and propose a NEW acquisition strategy for this week. "
        "Be specific: which channels, which targets, what message, what metric to track. "
        "If the current strategy has score < 30%, PIVOT to something completely different. "
        "Data: " + json.dumps(results, default=str)
    )

    return results
