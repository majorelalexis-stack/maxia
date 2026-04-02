"""MAXIA V12 — Admin routes (agent permissions, admin tools, seeding, admin panel)"""
import logging
import os
import time
import json
import hmac
import secrets
from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from auth import require_auth
from error_utils import safe_error

logger = logging.getLogger(__name__)
router = APIRouter()

FRONTEND_INDEX = Path(__file__).parent.parent / "frontend" / "index.html"
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

ADMIN_KEY = os.getenv("ADMIN_KEY", "")  # MUST be set in .env — no hardcoded default
_ADMIN_SESSIONS_MAX = 100  # Cap to prevent unbounded growth
_ADMIN_SESSIONS_FILE = Path(__file__).parent / ".admin_sessions.json"


def _load_admin_sessions() -> dict:
    """Load sessions from disk — survives restarts."""
    try:
        if _ADMIN_SESSIONS_FILE.exists():
            data = json.loads(_ADMIN_SESSIONS_FILE.read_text())
            now = time.time()
            return {k: v for k, v in data.items() if v > now}
    except Exception:
        pass
    return {}


def _save_admin_sessions():
    """Persist sessions to disk — merge with existing file (multi-worker safe)."""
    try:
        now = time.time()
        # Read existing from disk (other workers may have added sessions)
        existing = {}
        if _ADMIN_SESSIONS_FILE.exists():
            try:
                existing = json.loads(_ADMIN_SESSIONS_FILE.read_text())
            except Exception:
                pass
        # Merge: combine disk + memory, remove expired
        merged = {k: v for k, v in {**existing, **_ADMIN_SESSIONS}.items() if v > now}
        _ADMIN_SESSIONS_FILE.write_text(json.dumps(merged))
        # Update local memory too
        _ADMIN_SESSIONS.update(merged)
    except Exception:
        pass


_ADMIN_SESSIONS: dict = _load_admin_sessions()


def _verify_admin(request: Request) -> bool:
    """Verifie l'auth admin via header X-Admin-Key OU cookie session opaque."""
    # 1) Header direct (pour API calls)
    header_key = request.headers.get("X-Admin-Key", "")
    if header_key and ADMIN_KEY and hmac.compare_digest(header_key, ADMIN_KEY):
        return True
    # 2) Cookie session opaque (pour dashboard browser)
    cookie_token = request.cookies.get("maxia_admin", "")
    if cookie_token:
        # Check memory first
        if cookie_token in _ADMIN_SESSIONS and _ADMIN_SESSIONS[cookie_token] > time.time():
            return True
        # Fallback: reload from disk (another worker may have created it)
        refreshed = _load_admin_sessions()
        _ADMIN_SESSIONS.update(refreshed)
        if cookie_token in _ADMIN_SESSIONS and _ADMIN_SESSIONS[cookie_token] > time.time():
            return True
    return False


# ── Admin Panel ──

@router.get("/admin", response_class=HTMLResponse, include_in_schema=False)
async def admin_login_page():
    """Page de login admin — formulaire qui stocke la cle en sessionStorage."""
    return HTMLResponse("""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MAXIA Admin</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#060a14;color:#e4e4e7;font-family:'DM Sans',sans-serif;display:flex;align-items:center;justify-content:center;height:100vh}
.login{background:rgba(255,255,255,.03);backdrop-filter:blur(8px);border:1px solid rgba(255,255,255,.05);border-radius:16px;padding:40px;max-width:400px;width:90%}
h1{font-family:'Syne',sans-serif;font-size:28px;margin-bottom:8px;background:linear-gradient(135deg,#00e5ff,#7c3aed,#f43f5e);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
p{color:#a1a1aa;font-size:14px;margin-bottom:24px}
input{width:100%;padding:14px;border-radius:10px;background:#060a14;border:1px solid rgba(255,255,255,.08);color:#e4e4e7;font-size:15px;margin-bottom:16px;outline:none;font-family:'JetBrains Mono',monospace}
input:focus{border-color:#00e5ff}
button{width:100%;padding:14px;background:linear-gradient(135deg,#00e5ff,#7c3aed);color:#fff;border:none;border-radius:10px;font-size:16px;font-weight:700;cursor:pointer;font-family:'DM Sans',sans-serif}
button:hover{transform:translateY(-2px);box-shadow:0 8px 30px rgba(0,229,255,.3)}
.err{color:#EF4444;font-size:13px;margin-top:12px;display:none}
</style></head><body>
<div class="login">
<h1>MAXIA Admin</h1>
<p>Enter your admin key to access the dashboard.</p>
<form onsubmit="return doLogin()">
<input type="password" id="admin-key" placeholder="Admin Key" autofocus>
<button type="submit">Login</button>
</form>
<div class="err" id="err">Invalid key. Try again.</div>
</div>
<script>
function doLogin(){
  var key=document.getElementById('admin-key').value;
  if(!key)return false;
  fetch('/admin/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key:key}),credentials:'include'})
  .then(function(r){return r.json()})
  .then(function(d){if(d.ok){window.location.href='/dashboard'}else{document.getElementById('err').style.display='block'}})
  .catch(function(){document.getElementById('err').style.display='block'});
  return false;
}
</script></body></html>""")


@router.post("/admin/login", include_in_schema=False)
async def admin_login(req: Request):
    """Verifie la cle admin via POST body, pose un cookie httponly avec token opaque."""
    from fastapi.responses import RedirectResponse
    try:
        body = await req.json()
        key = body.get("key", "")
    except Exception:
        key = ""
    if not key or not ADMIN_KEY or not hmac.compare_digest(key, ADMIN_KEY):
        raise HTTPException(401, "Invalid admin key")
    # Token opaque au lieu de la cle en clair dans le cookie
    token = secrets.token_hex(32)
    # Cleanup expired sessions + enforce cap
    now = time.time()
    expired = [k for k, exp in _ADMIN_SESSIONS.items() if exp < now]
    for k in expired:
        _ADMIN_SESSIONS.pop(k, None)
    if len(_ADMIN_SESSIONS) >= _ADMIN_SESSIONS_MAX:
        oldest = min(_ADMIN_SESSIONS, key=_ADMIN_SESSIONS.get)
        _ADMIN_SESSIONS.pop(oldest, None)
    _ADMIN_SESSIONS[token] = now + 86400  # 24h
    _save_admin_sessions()
    resp = JSONResponse({"ok": True, "redirect": "/dashboard"})
    resp.set_cookie("maxia_admin", token, httponly=True, secure=True, samesite="lax", max_age=86400)
    return resp


@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
async def serve_dashboard(request: Request):
    """Dashboard admin — authentification via header X-Admin-Key ou cookie session opaque."""
    if not _verify_admin(request):
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/admin", status_code=302)
    if FRONTEND_INDEX.exists():
        return HTMLResponse(FRONTEND_INDEX.read_text(encoding="utf-8"))
    alt_paths = [
        Path("/opt/maxia/frontend/index.html"),
        Path(__file__).parent / "index.html",
    ]
    for p in alt_paths:
        if p.exists():
            return HTMLResponse(p.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>MAXIA</h1><p>Dashboard introuvable.</p>")


# ── API Stats / Activity (admin) ──

@router.get("/api/stats")
async def get_stats(request: Request):
    from security import require_admin
    require_admin(request)
    from database import db
    return await db.get_stats()


@router.get("/api/activity")
async def get_activity(request: Request, limit: int = 30):
    from security import require_admin
    require_admin(request)
    from database import db
    return await db.get_activity(limit)


# ── CEO reset emergency ──

@router.post("/api/admin/ceo-reset-emergency")
async def ceo_reset_emergency(request: Request):
    """Reset l'emergency stop du CEO."""
    from security import require_admin
    require_admin(request)
    try:
        from ceo_maxia import ceo
        ceo.reset_emergency()
        return {"status": "ok", "emergency_stop": False}
    except Exception as e:
        return safe_error(e, "operation")


# ══════════════════════════════════════════
#  AGENT PERMISSIONS — Admin endpoints (freeze/unfreeze/downgrade/revoke/scopes)
# ══════════════════════════════════════════

@router.get("/api/agents/permissions")
async def agents_list_permissions(request: Request):
    """Liste tous les agents et leurs permissions. Admin only."""
    from auth import require_ceo_auth
    await require_ceo_auth(request, request.headers.get("X-CEO-Key"))
    from agent_permissions import list_all_agents
    return {"agents": await list_all_agents()}


@router.get("/api/agents/{agent_id}/permissions")
async def agent_get_permissions(agent_id: str, request: Request):
    """Permissions d'un agent specifique. Admin only."""
    from auth import require_ceo_auth
    await require_ceo_auth(request, request.headers.get("X-CEO-Key"))
    from agent_permissions import get_agent_perms_by_id
    return await get_agent_perms_by_id(agent_id)


@router.post("/api/agents/{agent_id}/freeze")
async def agent_freeze(agent_id: str, request: Request):
    """Freeze un agent — lectures OK, ecritures bloquees."""
    from auth import require_ceo_auth
    await require_ceo_auth(request, request.headers.get("X-CEO-Key"))
    from agent_permissions import freeze_agent
    from database import db
    result = await freeze_agent(agent_id)
    # Audit
    try:
        from audit_trail import audit_log
        await audit_log("admin", "agent_freeze", agent_id, db=db,
                       agent_id=agent_id, metadata={"action": "freeze"})
    except Exception:
        pass
    return result


@router.post("/api/agents/{agent_id}/unfreeze")
async def agent_unfreeze(agent_id: str, request: Request):
    """Unfreeze un agent — retour a active."""
    from auth import require_ceo_auth
    await require_ceo_auth(request, request.headers.get("X-CEO-Key"))
    from agent_permissions import unfreeze_agent
    from database import db
    result = await unfreeze_agent(agent_id)
    try:
        from audit_trail import audit_log
        await audit_log("admin", "agent_unfreeze", agent_id, db=db,
                       agent_id=agent_id, metadata={"action": "unfreeze"})
    except Exception:
        pass
    return result


@router.post("/api/agents/{agent_id}/downgrade")
async def agent_downgrade(agent_id: str, level: int, request: Request):
    """Downgrade le trust level. Les caps s'ajustent automatiquement."""
    from auth import require_ceo_auth
    await require_ceo_auth(request, request.headers.get("X-CEO-Key"))
    from agent_permissions import downgrade_agent
    from database import db
    result = await downgrade_agent(agent_id, level)
    try:
        from audit_trail import audit_log
        await audit_log("admin", "agent_downgrade", agent_id, db=db,
                       agent_id=agent_id, metadata=result)
    except Exception:
        pass
    return result


@router.post("/api/agents/{agent_id}/revoke")
async def agent_revoke(agent_id: str, request: Request):
    """Revoke definitivement un agent. Tout bloque."""
    from auth import require_ceo_auth
    await require_ceo_auth(request, request.headers.get("X-CEO-Key"))
    from agent_permissions import revoke_agent
    from database import db
    result = await revoke_agent(agent_id)
    try:
        from audit_trail import audit_log
        await audit_log("admin", "agent_revoke", agent_id, db=db,
                       agent_id=agent_id, metadata={"action": "revoke"})
    except Exception:
        pass
    return result


@router.post("/api/agents/{agent_id}/scopes")
async def agent_update_scopes(agent_id: str, request: Request):
    """Met a jour les scopes d'un agent. Body: {"scopes": ["swap:*", "gpu:read"]}"""
    from auth import require_ceo_auth
    await require_ceo_auth(request, request.headers.get("X-CEO-Key"))
    body = await request.json()
    scopes = body.get("scopes", [])
    if not isinstance(scopes, list):
        raise HTTPException(400, "scopes must be a list")
    from agent_permissions import update_agent_scopes
    return await update_agent_scopes(agent_id, scopes)


@router.get("/api/agents/scopes/available")
async def agents_available_scopes():
    """Liste tous les scopes disponibles."""
    from agent_permissions import ALL_SCOPES, DEFAULT_SCOPES, TRUST_LEVEL_DEFAULTS
    return {
        "available_scopes": ALL_SCOPES,
        "defaults_by_trust_level": {
            k: {"scopes": v, **TRUST_LEVEL_DEFAULTS[k]}
            for k, v in DEFAULT_SCOPES.items()
        },
    }


@router.post("/api/agents/{agent_id}/rotate-key")
async def agent_rotate_key(agent_id: str, request: Request):
    """Rotate l'API key d'un agent. Garde DID, UAID, trust, historique. Admin only."""
    from auth import require_ceo_auth
    await require_ceo_auth(request, request.headers.get("X-CEO-Key"))
    from agent_permissions import rotate_agent_key
    from database import db
    result = await rotate_agent_key(agent_id)
    try:
        from audit_trail import audit_log
        await audit_log("admin", "agent_key_rotation", agent_id, db=db,
                       agent_id=agent_id, metadata={"old_prefix": result.get("old_key_prefix", "")})
    except Exception:
        pass
    return result


@router.get("/api/public/agent/{identifier}")
async def public_agent_lookup(identifier: str):
    """Resolve un agent par DID ou UAID. Public, sans auth."""
    from agent_permissions import resolve_agent_public
    return await resolve_agent_public(identifier)


@router.get("/agent/{agent_id}/did.json")
async def agent_did_document(agent_id: str):
    """W3C DID Document for an agent."""
    from agent_permissions import generate_did_document
    from database import db
    try:
        rows = await db.raw_execute_fetchall(
            "SELECT agent_id, wallet, public_key, uaid, status, trust_level "
            "FROM agent_permissions WHERE agent_id=?", (agent_id,))
        if not rows:
            raise HTTPException(404, "Agent not found")
        a = dict(rows[0])
        doc = generate_did_document(
            a["agent_id"], a.get("public_key", ""), a["wallet"],
            a.get("uaid", ""), a.get("status", "active"), a.get("trust_level", 0))
        return JSONResponse(doc, headers={"Content-Type": "application/did+json"})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, "DID document error")


@router.get("/.well-known/did.json")
async def maxia_did_document():
    """W3C DID Document for MAXIA itself (the marketplace)."""
    return JSONResponse({
        "@context": ["https://www.w3.org/ns/did/v1"],
        "id": "did:web:maxiaworld.app",
        "verificationMethod": [{
            "id": "did:web:maxiaworld.app#treasury",
            "type": "Ed25519VerificationKey2020",
            "controller": "did:web:maxiaworld.app",
            "publicKeyBase58": "7RtCpikgfd6xiFQyVoxjV51HN14XXRrQJiJ3KrzUdQsW",
        }],
        "service": [
            {"id": "#marketplace", "type": "AIMarketplace", "serviceEndpoint": "https://maxiaworld.app/api/public"},
            {"id": "#a2a", "type": "AgentToAgent", "serviceEndpoint": "https://maxiaworld.app/a2a"},
            {"id": "#mcp", "type": "ModelContextProtocol", "serviceEndpoint": "https://maxiaworld.app/mcp/manifest"},
        ],
        "maxia:chains": 14,
        "maxia:tokens": 107,
        "maxia:escrow": "8ADNmAPDxuRvJPBp8dL9rq5jpcGtqAEx4JyZd1rXwBUY",
    })


# ═══════════════════════════════════════════════════════════
#  AI CARD — AgentMesh Discovery (.well-known/ai-card.json)
# ═══════════════════════════════════════════════════════════

AI_CARD = {
    "name": "MAXIA",
    "description": "AI-to-AI marketplace on 14 blockchains",
    "version": "12.0.0",
    "homepage": "https://maxiaworld.app",
    "identity": {
        "did": "did:web:maxiaworld.app",
        "public_key": "7RtCpikgfd6xiFQyVoxjV51HN14XXRrQJiJ3KrzUdQsW",
        "algorithm": "Ed25519",
    },
    "capabilities": [
        "marketplace", "swap", "gpu-rental", "escrow", "stocks",
        "llm", "mcp", "sentiment", "defi", "wallet-analysis",
    ],
    "services": [
        {"protocol": "a2a", "url": "https://maxiaworld.app/a2a"},
        {"protocol": "mcp", "url": "https://maxiaworld.app/mcp/manifest"},
        {"protocol": "aip", "url": "https://maxiaworld.app/api/public"},
    ],
    "trust": {
        "escrow_chains": ["solana", "base"],
        "payment_tokens": ["USDC"],
        "solana_program": "8ADNmAPDxuRvJPBp8dL9rq5jpcGtqAEx4JyZd1rXwBUY",
        "base_contract": "0xBd31bB973183F8476d0C4cF57a92e648b130510C",
    },
}


@router.get("/.well-known/ai-card.json")
async def ai_card_wellknown():
    """AgentMesh AI Card for MAXIA discovery."""
    return AI_CARD


@router.get("/ai-card.json")
async def ai_card_shortcut():
    """AgentMesh AI Card (shortcut path)."""
    return AI_CARD


@router.post("/api/public/intent/verify")
async def verify_signed_intent(request: Request):
    """Verify a signed intent envelope. Public endpoint."""
    from database import db
    try:
        intent = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON body")

    # Detect format: AIP (has 'intent' field) vs legacy (has 'sig' field)
    if "intent" in intent or "proof" in intent:
        from intent import verify_intent_from_request
        return await verify_intent_from_request(intent)
    else:
        # Legacy MAXIA format
        from intent import verify_intent_legacy
        did = intent.get("did", "")
        if not did:
            return {"valid": False, "error": "No DID in intent"}
        try:
            rows = await db.raw_execute_fetchall(
                "SELECT public_key, status FROM agent_permissions WHERE did=?", (did,))
            if not rows:
                return {"valid": False, "error": f"DID not found: {did}"}
            if rows[0].get("status") == "revoked":
                return {"valid": False, "error": "Agent revoked"}
            pub_key = rows[0].get("public_key", "")
            if not pub_key:
                return {"valid": False, "error": "No public key"}
            return await verify_intent_legacy(intent, pub_key)
        except Exception as e:
            return {"valid": False, "error": "An error occurred"[:200]}


# ── Admin tools (cache stats, audit log, ceo disable, twitter, x402, etc.) ──

@router.get("/api/cache/stats")
async def cache_stats():
    """Statistiques du cache prix (hit rate, age)."""
    try:
        from price_oracle import get_cache_stats
        return get_cache_stats()
    except Exception as e:
        return safe_error(e, "operation")


@router.get("/api/admin/audit-log")
async def admin_audit_log(request: Request, limit: int = 50):
    """Log d'audit des actions admin (IP, timestamp, action)."""
    from security import require_admin, get_audit_log_async
    require_admin(request)
    return {"entries": await get_audit_log_async(limit)}


@router.post("/api/admin/ceo/disable-agent")
async def admin_disable_agent(request: Request):
    """Desactive un sous-agent specifique (kill switch granulaire)."""
    from security import require_admin
    require_admin(request)
    body = await request.json()
    agent_name = body.get("agent", "")
    reason = body.get("reason", "manual")
    if not agent_name:
        return {"error": "agent name required"}
    from ceo_maxia import ceo
    ceo.disable_agent(agent_name, reason)
    return {"success": True, "disabled": agent_name, "reason": reason}


@router.post("/api/admin/ceo/enable-agent")
async def admin_enable_agent(request: Request):
    """Reactive un sous-agent."""
    from security import require_admin
    require_admin(request)
    body = await request.json()
    agent_name = body.get("agent", "")
    if not agent_name:
        return {"error": "agent name required"}
    from ceo_maxia import ceo
    ceo.enable_agent(agent_name)
    return {"success": True, "enabled": agent_name}


@router.get("/api/twitter/status")
async def twitter_status():
    try:
        from twitter_bot import get_stats
        return get_stats()
    except Exception as e:
        return {"error": "An error occurred", "configured": False}


@router.get("/api/reddit/status")
async def reddit_status():
    try:
        from reddit_bot import get_stats
        return get_stats()
    except Exception as e:
        return {"error": "An error occurred", "configured": False}


@router.get("/api/outreach/status")
async def outreach_status():
    """Get agent outreach bot statistics."""
    try:
        from agent_outreach import get_stats
        return get_stats()
    except Exception as e:
        return safe_error(e, "operation")


@router.post("/api/admin/outreach-now")
async def admin_outreach_now(request: Request):
    """Manually trigger an outreach cycle. Admin only."""
    from security import require_admin
    require_admin(request)
    from agent_outreach import run_outreach_cycle
    return await run_outreach_cycle()


@router.get("/MAXIA_DOCS.md")
async def serve_rag_docs():
    """Serve RAG-optimized documentation for LLM ingestion."""
    import pathlib
    doc_path = pathlib.Path(__file__).parent.parent / "frontend" / "MAXIA_DOCS.md"
    if doc_path.exists():
        return FileResponse(str(doc_path), media_type="text/markdown")
    return {"error": "docs not found"}


@router.post("/api/admin/reddit-post")
async def admin_reddit_post(request: Request):
    """Manually post to Reddit. Admin only."""
    from security import require_admin
    require_admin(request)
    body = await request.json()
    subreddit = body.get("subreddit", "solanadev")
    title = body.get("title", "")
    text = body.get("text", "")
    if not title or not text:
        return {"error": "title and text required"}
    from reddit_bot import post_to_reddit
    return await post_to_reddit(subreddit, title, text)


@router.get("/api/watchdog/health")
async def watchdog_health(request: Request):
    """Run health check on all endpoints. Admin only."""
    from security import require_admin
    require_admin(request)
    try:
        from ceo_maxia import watchdog_health_check
        return await watchdog_health_check()
    except Exception as e:
        return safe_error(e, "operation")


@router.get("/api/admin/backups")
async def admin_backups(request: Request):
    """List DB backups."""
    from security import require_admin
    require_admin(request)
    from db_backup import get_backup_list
    return {"backups": get_backup_list()}

@router.post("/api/admin/backup-now")
async def admin_backup_now(request: Request):
    """Trigger immediate DB backup."""
    from security import require_admin
    require_admin(request)
    from db_backup import backup_db
    return await backup_db()

@router.post("/api/admin/backup-restore")
async def admin_backup_restore(request: Request):
    """Restore DB from a backup file. Creates safety backup first."""
    from security import require_admin
    require_admin(request)
    body = await request.json()
    backup_name = body.get("file", "")
    if not backup_name:
        return {"error": "file required (e.g. maxia_20260320_120000.db)"}
    from db_backup import restore_db
    return await restore_db(backup_name)

@router.post("/api/admin/backup-verify")
async def admin_backup_verify(request: Request):
    """Verify a backup file is valid and readable."""
    from security import require_admin
    require_admin(request)
    body = await request.json()
    from db_backup import verify_backup
    return await verify_backup(body.get("file", ""))


@router.get("/api/admin/errors")
async def admin_errors(request: Request, limit: int = 50, module: str = ""):
    """Error tracker dashboard — dernieres erreurs et stats par module."""
    from auth import require_ceo_auth
    await require_ceo_auth(request, request.headers.get("X-CEO-Key"))
    from error_tracker import get_errors, get_error_stats
    return {"errors": get_errors(limit, module), "stats": get_error_stats()}


@router.get("/api/public/api-pricing")
async def api_pricing():
    """Pricing des tiers API (free, pro, enterprise)."""
    from api_keys import API_TIERS
    return {"tiers": API_TIERS, "currency": "USDC/month"}


@router.post("/api/admin/tweet")
async def admin_post_tweet(request: Request):
    """Post un tweet manuellement (admin only)."""
    from security import require_admin
    require_admin(request)
    try:
        body = await request.json()
        text = body.get("text", "")
        if not text:
            return {"error": "text required"}
        from twitter_bot import post_tweet
        return await post_tweet(text)
    except Exception as e:
        return safe_error(e, "operation")


# ═══════════════════════════════════════════════════════════
#  x402 V2 (Art.9) — Multi-chain info
# ═══════════════════════════════════════════════════════════

@router.get("/api/x402/info")
async def x402_info():
    from config import (
        TREASURY_ADDRESS, TREASURY_ADDRESS_BASE,
        TREASURY_ADDRESS_ETH, TREASURY_ADDRESS_XRPL,
        TREASURY_ADDRESS_POLYGON, TREASURY_ADDRESS_ARBITRUM,
        TREASURY_ADDRESS_AVALANCHE, TREASURY_ADDRESS_BNB,
        SUPPORTED_NETWORKS, X402_PRICE_MAP,
    )
    return {
        "version": 2,
        "networks": SUPPORTED_NETWORKS,
        "payTo": {
            "solana": TREASURY_ADDRESS,
            "base": TREASURY_ADDRESS_BASE,
            "ethereum": TREASURY_ADDRESS_ETH,
            "xrpl": TREASURY_ADDRESS_XRPL,
            "polygon": TREASURY_ADDRESS_POLYGON,
            "arbitrum": TREASURY_ADDRESS_ARBITRUM,
            "avalanche": TREASURY_ADDRESS_AVALANCHE,
            "bnb": TREASURY_ADDRESS_BNB,
        },
        "priceMap": X402_PRICE_MAP,
        "protocols": ["x402-v2", "ap2"],
    }


# ══════════════════════════════════════════════════════════
#  ADMIN: Seed initial services (one-time setup)
# ══════════════════════════════════════════════════════════

@router.post("/api/admin/seed-services")
async def seed_services(request: Request):
    """Ajoute les services initiaux (une seule fois)."""
    import uuid
    from security import require_admin
    require_admin(request)
    from database import db
    from seed_data import INITIAL_SERVICES
    from config import TREASURY_ADDRESS
    existing = await db.get_listings()
    if len(existing) >= 4:
        return {"message": "Services deja listes", "count": len(existing)}
    added = 0
    for svc in INITIAL_SERVICES:
        exists = any(l.get("name") == svc["name"] for l in existing)
        if not exists:
            listing = {
                "id": str(uuid.uuid4()),
                "agentId": TREASURY_ADDRESS,
                "name": svc["name"],
                "type": svc["type"],
                "description": svc["description"],
                "priceUsdc": svc["priceUsdc"],
                "rating": 5.0,
                "txCount": 0,
                "createdAt": int(time.time()),
            }
            await db.save_listing(listing)
            added += 1
    return {"message": f"{added} services ajoutes", "total": len(existing) + added}


@router.post("/api/admin/seed-datasets")
async def seed_datasets(request: Request):
    """Ajoute les datasets initiaux (une seule fois)."""
    import uuid
    from security import require_admin
    require_admin(request)
    from database import db
    from seed_data import INITIAL_DATASETS
    from config import TREASURY_ADDRESS
    try:
        existing = await db.raw_execute_fetchall("SELECT data FROM datasets")
        existing_list = [json.loads(r[0]) for r in existing] if existing else []
    except Exception:
        existing_list = []
    if len(existing_list) >= 4:
        return {"message": "Datasets deja listes", "count": len(existing_list)}
    added = 0
    for ds in INITIAL_DATASETS:
        exists = any(d.get("name") == ds["name"] for d in existing_list)
        if not exists:
            dataset = {
                "datasetId": str(uuid.uuid4()),
                "seller": TREASURY_ADDRESS,
                "name": ds["name"],
                "description": ds["description"],
                "category": ds["category"],
                "sizeMb": ds["size_mb"],
                "priceUsdc": ds["price_usdc"],
                "sampleHash": ds["sample_hash"],
                "format": ds["format"],
                "rating": 5.0,
                "purchases": 0,
                "createdAt": int(time.time()),
            }
            await db.raw_execute(
                "INSERT OR REPLACE INTO datasets(dataset_id,seller,data) VALUES(?,?,?)",
                (dataset["datasetId"], TREASURY_ADDRESS, json.dumps(dataset)),
            )
            added += 1
    return {"message": f"{added} datasets ajoutes", "total": len(existing_list) + added}


# ── Agent autonomous status ──

@router.get("/api/agent/status")
async def agent_status(request: Request):
    """Statut agent. CEO VPS removed — returns minimal info."""
    from security import require_admin
    require_admin(request)
    from security import get_daily_spend_stats
    return {"status": "CEO VPS removed", "daily_spend": get_daily_spend_stats()}


# ── Agent preflight ──

@router.get("/api/agent/preflight")
async def preflight(request: Request):
    """Diagnostic systeme complet. Admin only."""
    from security import require_admin
    require_admin(request)
    from preflight import check_system_ready
    results = await check_system_ready()
    return results
