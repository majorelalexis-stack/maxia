"""MAXIA V12 — Enterprise, analytics, events, and tracking routes"""
import logging
import time
import json
import asyncio
from fastapi import APIRouter, HTTPException, Depends, Request
from error_utils import safe_error

logger = logging.getLogger(__name__)
router = APIRouter()


# ═══════════════════════════════════════════════════════════
#  SSE Events stream (admin)
# ═══════════════════════════════════════════════════════════

@router.get("/api/events/stream")
async def event_stream(request: Request):
    """SSE endpoint — stream de donnees temps reel pour le dashboard."""
    from admin_routes import _verify_admin
    if not _verify_admin(request):
        raise HTTPException(403, "Unauthorized — provide X-Admin-Key header or valid session cookie")
    from starlette.responses import StreamingResponse

    async def generate():
        last_decision_count = 0
        last_conversation_count = 0
        last_bus_processed = 0
        last_error_count = 0
        while True:
            try:
                from ceo_maxia import ceo, agent_bus
                status = ceo.get_status()
                stats = status.get("stats", {})
                decisions = stats.get("decisions", 0)
                conversations = stats.get("conversations", 0)
                errors = stats.get("erreurs", 0)
                bus_stats = agent_bus.get_stats()
                bus_processed = bus_stats.get("processed", 0)

                changed = (
                    decisions != last_decision_count
                    or conversations != last_conversation_count
                    or bus_processed != last_bus_processed
                    or errors != last_error_count
                )

                if changed:
                    last_decision_count = decisions
                    last_conversation_count = conversations
                    last_bus_processed = bus_processed
                    last_error_count = errors

                    event_data = json.dumps({
                        "type": "ceo_update",
                        "ts": int(time.time()),
                        "cycle": status.get("cycle", 0),
                        "running": status.get("running", False),
                        "emergency": status.get("emergency_stop", False),
                        "health": status.get("agents", {}).get("ANALYTICS", {}).get("health_score", 0),
                        "decisions": decisions,
                        "conversations": conversations,
                        "errors": errors,
                        "revenue": stats.get("revenue", 0),
                        "clients": stats.get("clients", 0),
                        "bus": {"pending": bus_stats.get("pending", 0), "processed": bus_processed},
                        "disabled_agents": list(status.get("disabled_agents", {}).keys()),
                        "crises": len([c for c in status.get("agents", {}).values() if isinstance(c, dict) and c.get("status") == "pause_crise"]),
                        "last_bus_messages": bus_stats.get("recent", [])[-2:],
                    })
                    yield f"data: {event_data}\n\n"
                else:
                    # Heartbeat every 30s even if no change
                    yield f": heartbeat {int(time.time())}\n\n"
            except Exception:
                yield f": error {int(time.time())}\n\n"
            await asyncio.sleep(5)

    return StreamingResponse(generate(), media_type="text/event-stream")


# ══════════════════════════════════════════
#  ANALYTICS — Page tracking (no external deps)
# ══════════════════════════════════════════

_track_dedup: dict = {}  # "session:page" -> timestamp (anti-spam)

@router.post("/api/track")
async def track_page(request: Request, req: dict):
    """Track a page view. Public, no auth. Rate-limited per session+page."""
    import hashlib
    from database import db
    page = (req.get("page") or "").strip()[:200]
    referrer = (req.get("referrer") or "").strip()[:500]
    if not page:
        return {"ok": True}  # Silent ignore

    ip = request.headers.get("x-real-ip") or (request.client.host if request.client else "")
    ua = (request.headers.get("user-agent") or "")[:300]
    device = "mobile" if any(k in ua.lower() for k in ("mobile", "android", "iphone")) else "desktop"

    # Session cookie (anonymous)
    sid = request.cookies.get("maxia_sid", "")
    if not sid:
        sid = hashlib.sha256(f"{ip}{ua}{time.time()}".encode()).hexdigest()[:16]

    # Dedup: 1 track per page per session per 5 min
    dedup_key = f"{sid}:{page}"
    now = time.time()
    if dedup_key in _track_dedup and now - _track_dedup[dedup_key] < 300:
        return {"ok": True}
    _track_dedup[dedup_key] = now

    # Cleanup old dedup entries (keep last 1000)
    if len(_track_dedup) > 1000:
        oldest = sorted(_track_dedup, key=_track_dedup.get)[:500]
        for k in oldest:
            _track_dedup.pop(k, None)

    try:
        await db.track_page_view(sid, page, referrer, ip, ua, device)
    except Exception as e:
        logger.warning("[Analytics] Track error: %s", e)

    from starlette.responses import JSONResponse as _TrackResp
    resp = _TrackResp({"ok": True})
    if not request.cookies.get("maxia_sid"):
        resp.set_cookie("maxia_sid", sid, httponly=True, secure=True, samesite="lax", max_age=365*86400)
    return resp


@router.get("/api/analytics/site")
async def analytics_dashboard(request: Request):
    """Analytics dashboard data. Admin only."""
    from security import require_admin
    require_admin(request)
    from database import db
    period = int(request.query_params.get("period", "30"))
    period = min(period, 365)
    return await db.get_analytics_summary(period)


# ══════════════════════════════════════════════════════════
#  V12.1: Agent Analytics (inscriptions en temps reel)
# ══════════════════════════════════════════════════════════

@router.get("/api/analytics/agents")
async def analytics_agents(period: str = "7d"):
    """Nombre d'agents inscrits par jour sur une periode."""
    try:
        from public_api import _registered_agents
        import datetime

        # Calculer la periode
        days = int(period.replace("d", "")) if "d" in period else 7
        now = int(time.time())
        cutoff = now - (days * 86400)

        # Compteur par jour
        daily = {}
        total = 0
        for key, agent in _registered_agents.items():
            ts = agent.get("registered_at", 0)
            total += 1
            if ts >= cutoff:
                day = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
                daily[day] = daily.get(day, 0) + 1

        # Remplir les jours sans inscription
        result = []
        for i in range(days - 1, -1, -1):
            d = (datetime.datetime.now() - datetime.timedelta(days=i)).strftime("%Y-%m-%d")
            result.append({"date": d, "registrations": daily.get(d, 0)})

        return {
            "total_agents": total,
            "period": period,
            "daily": result,
            "active_today": sum(1 for a in _registered_agents.values() if a.get("requests_today", 0) > 0),
        }
    except Exception as e:
        return safe_error(e, "operation")


@router.get("/api/analytics/agents/live")
async def analytics_agents_live():
    """Compteur live d'agents inscrits + derniere inscription."""
    try:
        from public_api import _registered_agents
        agents = list(_registered_agents.values())
        last = max(agents, key=lambda a: a.get("registered_at", 0)) if agents else {}
        return {
            "total": len(agents),
            "active_today": sum(1 for a in agents if a.get("requests_today", 0) > 0),
            "last_registration": {
                "name": last.get("name", ""),
                "wallet": last.get("wallet", "")[:16] + "..." if last.get("wallet") else "",
                "timestamp": last.get("registered_at", 0),
            } if last else None,
            "with_services": sum(1 for a in agents if a.get("services_listed", 0) > 0),
        }
    except Exception as e:
        return safe_error(e, "operation")


# ═══════════════════════════════════════════════════════════
#  ENTERPRISE — Fleet Management & Compliance Reports
# ═══════════════════════════════════════════════════════════

@router.get("/api/enterprise/fleet/{wallet}")
async def enterprise_fleet(wallet: str, request: Request):
    """Fleet overview — all agents owned by a wallet."""
    from fleet_manager import get_fleet_overview
    from database import db
    return await get_fleet_overview(wallet, db)

@router.post("/api/enterprise/fleet/toggle")
async def enterprise_toggle_agent(request: Request):
    """Activate/deactivate an agent in the fleet."""
    from security import require_admin
    require_admin(request)
    from fleet_manager import toggle_agent
    from database import db
    body = await request.json()
    return await toggle_agent(body.get("api_key", ""), body.get("enabled", True), db)

@router.get("/api/enterprise/compliance/{wallet}")
async def enterprise_compliance(wallet: str, request: Request, period: int = 30):
    """Generate a compliance report for a wallet (last N days)."""
    from compliance_report import generate_compliance_report
    from database import db
    return await generate_compliance_report(wallet, db, period)


@router.post("/api/enterprise/contact")
async def enterprise_contact(request: Request):
    """Receive enterprise contact form. Store in DB + send email + alert Telegram."""
    from database import db
    body = await request.json()

    company = body.get("company", "").strip()
    contact_name = body.get("contact_name", "").strip()
    email = body.get("email", "").strip()
    website = body.get("website", "").strip()
    agent_count = body.get("agent_count", "")
    plan = body.get("plan", "")
    volume = body.get("volume", "")
    use_case = body.get("use_case", "").strip()
    source = body.get("source", "")

    if not company or not contact_name or not email or not use_case:
        raise HTTPException(400, "company, contact_name, email, and use_case are required")

    # Store in DB
    lead_id = f"lead_{int(time.time())}_{company[:10].replace(' ','_')}"
    try:
        await db.raw_execute(
            "INSERT INTO enterprise_leads(id, company, contact_name, email, website, agent_count, plan, volume, use_case, source, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (lead_id, company, contact_name, email, website, agent_count, plan, volume, use_case, source, int(time.time())))
    except Exception:
        # Table may not exist yet — create it
        try:
            await db.raw_executescript(
                "CREATE TABLE IF NOT EXISTS enterprise_leads("
                "id TEXT PRIMARY KEY, company TEXT, contact_name TEXT, email TEXT, "
                "website TEXT, agent_count TEXT, plan TEXT, volume TEXT, "
                "use_case TEXT, source TEXT, status TEXT DEFAULT 'new', "
                "created_at INTEGER)")
            await db.raw_execute(
                "INSERT INTO enterprise_leads(id, company, contact_name, email, website, agent_count, plan, volume, use_case, source, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (lead_id, company, contact_name, email, website, agent_count, plan, volume, use_case, source, int(time.time())))
        except Exception as e:
            logger.error("[Enterprise] DB error: %s", e)

    # Send email notification to CEO
    try:
        from email_service import send_email
        email_body = (
            f"New Enterprise Lead!\n\n"
            f"Company: {company}\n"
            f"Contact: {contact_name}\n"
            f"Email: {email}\n"
            f"Website: {website}\n"
            f"Agents: {agent_count}\n"
            f"Plan: {plan}\n"
            f"Volume: {volume}\n"
            f"Source: {source}\n\n"
            f"Use Case:\n{use_case}\n"
        )
        await send_email("ceo@maxiaworld.app", f"Enterprise Lead: {company}", email_body)
    except Exception as e:
        logger.error("[Enterprise] Email error: %s", e)

    # Alert Telegram
    try:
        from alerts import alert_system
        await alert_system(
            f"NEW ENTERPRISE LEAD\n"
            f"Company: {company}\n"
            f"Contact: {contact_name} ({email})\n"
            f"Agents: {agent_count} | Plan: {plan}\n"
            f"Volume: {volume}\n"
            f"Use case: {use_case[:100]}..."
        )
    except Exception:
        pass

    return {"success": True, "lead_id": lead_id, "message": "We'll get back to you within 24 hours."}


# ═══════════════════════════════════════════════════════════
#  RPC INFRASTRUCTURE STATUS — Public transparency
# ═══════════════════════════════════════════════════════════

@router.get("/api/public/rpc-status")
async def rpc_status():
    """Public RPC infrastructure transparency."""
    import httpx
    from config import SOLANA_RPC_URLS
    statuses = []
    async with httpx.AsyncClient(timeout=3) as client:
        for url in SOLANA_RPC_URLS[:4]:
            name = "helius" if "helius" in url else url.split("//")[1].split("/")[0][:30]
            try:
                start = time.time()
                resp = await client.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "getHealth"})
                latency = int((time.time() - start) * 1000)
                statuses.append({"provider": name, "status": "operational" if resp.status_code == 200 else "degraded", "latency_ms": latency})
            except Exception:
                statuses.append({"provider": name, "status": "down", "latency_ms": None})
    return {"rpc_providers": statuses, "failover": "automatic", "commitment": "finalized"}
