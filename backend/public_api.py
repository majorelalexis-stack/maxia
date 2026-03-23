"""MAXIA Art.22 V11 — API Publique pour Agents IA (Buy/Sell Services)

Permet aux IA externes de :
- Decouvrir les services MAXIA (sans auth)
- S'inscrire gratuitement (recevoir une API key)
- Acheter des services et payer en USDC
- Vendre leurs propres services
- MAXIA prend sa commission automatiquement

Securite Art.1 : filtrage anti-abus sur TOUS les contenus
"""
import uuid, time, hashlib, secrets, asyncio, json, datetime, re
from fastapi import APIRouter, HTTPException, Header, Request
from config import (
    TREASURY_ADDRESS, GROQ_API_KEY, GROQ_MODEL,
    get_commission_bps, BLOCKED_WORDS, BLOCKED_PATTERNS,
)
from security import check_content_safety

# Fix #13: Solana address validation helper
_SOLANA_ADDR_RE = re.compile(r'^[1-9A-HJ-NP-Za-km-z]{32,44}$')

def _validate_solana_address(addr: str, field: str = "wallet"):
    if not addr or not _SOLANA_ADDR_RE.match(addr):
        raise HTTPException(400, f"Invalid Solana address in {field}")

router = APIRouter(prefix="/api/public", tags=["public-api"])

# ── Sandbox mode (test without real USDC) ──
import os
SANDBOX_MODE = os.getenv("SANDBOX_MODE", "false").lower() == "true"
SANDBOX_PREFIX = "sandbox_"

# ── Stockage en memoire (en prod: base de donnees) ──
_registered_agents: dict = {}   # api_key -> agent info
_agent_services: list = []      # services listes par des IA externes
_transactions: list = []        # historique des transactions
_db_loaded: bool = False


_db_last_sync: float = 0
_DB_SYNC_INTERVAL = 300  # re-sync from DB every 5 min


async def _load_from_db():
    """Load agents and services from DB on first access, then periodically re-sync."""
    global _db_loaded, _db_last_sync
    import time as _time
    now = _time.time()
    if _db_loaded and now - _db_last_sync < _DB_SYNC_INTERVAL:
        return
    _db_loaded = True
    _db_last_sync = now
    try:
        from database import db
        agents = await db.get_all_agents()
        for a in agents:
            key = a["api_key"]
            existing = _registered_agents.get(key)
            if existing:
                # Merge: garder les stats RAM (requests_today, etc) mais mettre a jour depuis DB
                existing.update({
                    "name": a["name"], "wallet": a["wallet"],
                    "description": a.get("description", ""),
                    "tier": a.get("tier", existing.get("tier", "BRONZE")),
                    "volume_30d": max(a.get("volume_30d", 0), existing.get("volume_30d", 0)),
                    "total_spent": max(a.get("total_spent", 0), existing.get("total_spent", 0)),
                    "total_earned": max(a.get("total_earned", 0), existing.get("total_earned", 0)),
                })
            else:
                _registered_agents[key] = {
                    "api_key": key, "name": a["name"], "wallet": a["wallet"],
                    "description": a.get("description", ""), "tier": a.get("tier", "BRONZE"),
                    "volume_30d": a.get("volume_30d", 0), "total_spent": a.get("total_spent", 0),
                    "total_earned": a.get("total_earned", 0), "services_listed": a.get("services_listed", 0),
                    "requests_today": 0, "registered_at": a.get("created_at", 0),
                }
        # Services: merge par ID (pas d'append duplicatif)
        existing_ids = {s["id"] for s in _agent_services}
        services = await db.get_services()
        for s in services:
            sd = dict(s)
            if sd["id"] not in existing_ids:
                _agent_services.append(sd)
                existing_ids.add(sd["id"])
        if agents or services:
            print(f"[PublicAPI] Loaded from DB: {len(agents)} agents, {len(services)} services")
    except Exception as e:
        print(f"[PublicAPI] DB load error: {e}")

# ── Groq client ──
groq_client = None
if GROQ_API_KEY:
    try:
        from groq import Groq
        groq_client = Groq(api_key=GROQ_API_KEY)
    except Exception:
        pass

# Rate limit par API key
_rate_limits: dict = {}
RATE_LIMIT_FREE = 100  # requetes/jour

# Fix #3: Lock for agent stat updates to prevent race conditions
_agent_update_lock = asyncio.Lock()

# Fix #11: Brute force API key protection
_failed_lookups: dict = {}  # ip -> count


# ══════════════════════════════════════════
#  SECURITE ART.1 — Filtrage anti-abus
# ══════════════════════════════════════════

def _check_safety(text: str, field: str = "content"):
    """Filtrage anti-pedopornographie et contenu illegal sur TOUT."""
    check_content_safety(text, field)


RATE_LIMITS_BY_TIER = {
    "BRONZE": 100,    # free tier
    "GOLD": 1000,      # $500+ volume
    "WHALE": 10000,    # $5000+ volume
}


def _check_rate(api_key: str):
    """Rate limit par API key — quota basé sur le tier de l'agent."""
    # Fix #5: Use UTC explicitly for consistent rate limiting across timezones
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    key = f"{api_key}:{today}"
    _rate_limits.setdefault(key, 0)
    _rate_limits[key] += 1

    # Cleanup stale date keys to avoid memory leak
    stale = [k for k in _rate_limits if ":" in k and not k.endswith(f":{today}")]
    for k in stale:
        _rate_limits.pop(k, None)

    # Fix #9: Cap total keys to prevent unbounded memory growth
    if len(_rate_limits) > 10000:
        today_keys = {k: v for k, v in _rate_limits.items() if k.endswith(f":{today}")}
        _rate_limits.clear()
        _rate_limits.update(today_keys)

    # Determine tier-based limit
    agent = _registered_agents.get(api_key, {})
    tier = agent.get("tier", "BRONZE")
    limit = RATE_LIMITS_BY_TIER.get(tier, RATE_LIMIT_FREE)

    if _rate_limits[key] > limit:
        raise HTTPException(429, f"Limite quotidienne atteinte ({limit} req/jour, tier {tier}). Augmentez votre volume pour un tier superieur.")


def _get_agent(api_key: str, client_ip: str = "") -> dict:
    """Recupere l'agent depuis sa cle API.
    Fix #11: Track failed lookups and block IPs with too many attempts.
    """
    # Fix #20: Cleanup _failed_lookups to prevent unbounded memory growth
    if len(_failed_lookups) > 10000:
        _failed_lookups.clear()

    # Fix #11: Block IPs with excessive failed lookups
    if client_ip and _failed_lookups.get(client_ip, 0) > 100:
        raise HTTPException(429, "Too many invalid API key attempts")

    agent = _registered_agents.get(api_key)
    if not agent:
        # Fix #11: Track failed lookup by IP
        if client_ip:
            _failed_lookups[client_ip] = _failed_lookups.get(client_ip, 0) + 1
        raise HTTPException(401, "API key invalide. Inscrivez-vous sur /api/public/register")

    # Fix #11: Reset failed count on success
    if client_ip:
        _failed_lookups.pop(client_ip, None)
    return agent


# ══════════════════════════════════════════
#  ENDPOINTS PUBLICS (sans auth)
# ══════════════════════════════════════════

@router.get("/services")
async def list_services():
    """Liste tous les services disponibles. Priorité aux agents externes. MAXIA en fallback uniquement."""
    await _load_from_db()

    external_services = []
    maxia_fallback = []

    # Services d'IA externes (prioritaire)
    for s in _agent_services:
        if s.get("status") == "active":
            external_services.append({
                "id": s["id"],
                "name": s["name"],
                "type": s["type"],
                "description": s["description"],
                "price_usdc": s["price_usdc"],
                "provider": s["agent_name"],
                "seller": s["agent_name"],
                "rating": s.get("rating", 5.0),
                "sales": s.get("sales", 0),
                "source": "external_agent",
            })

    # Capabilities with external coverage
    external_caps = set()
    for s in external_services:
        for word in (s.get("name", "") + " " + s.get("type", "")).lower().split():
            external_caps.add(word)

    # MAXIA fallback — only shown if no external agent covers the capability
    fallback_services = [
        {"id": "maxia_audit", "name": "AI Security Audit", "type": "security", "description": "AI-powered code audit. Fallback — seeking external providers.", "price_usdc": 9.99, "capability": "audit"},
        {"id": "maxia_code", "name": "Code Generation", "type": "code", "description": "Code generation via LLM. Fallback — seeking external providers.", "price_usdc": 3.99, "capability": "code"},
        {"id": "maxia_data", "name": "Data Analysis", "type": "data", "description": "Crypto data analysis. Fallback — seeking external providers.", "price_usdc": 2.99, "capability": "data"},
        {"id": "maxia_translate", "name": "Translation", "type": "text", "description": "Multi-language translation. Fallback — seeking external providers.", "price_usdc": 0.19, "capability": "translation"},
        {"id": "maxia_image", "name": "Image Generation", "type": "media", "description": "HD image generation. Fallback — seeking external providers.", "price_usdc": 0.05, "capability": "image"},
        {"id": "maxia_scraper", "name": "Web Scraper", "type": "data", "description": "Web page extraction. Fallback — seeking external providers.", "price_usdc": 0.02, "capability": "scraper"},
    ]

    for fb in fallback_services:
        cap = fb["capability"]
        has_external = any(cap in (s.get("name", "") + s.get("type", "")).lower() for s in external_services)
        if not has_external:
            maxia_fallback.append({
                "id": fb["id"],
                "name": fb["name"],
                "type": fb["type"],
                "description": fb["description"],
                "price_usdc": fb["price_usdc"],
                "provider": "MAXIA (fallback)",
                "seller": "MAXIA",
                "rating": 4.0,
                "source": "maxia_fallback",
                "note": "Seeking external providers. List your service: POST /sell",
            })

    all_services = external_services + maxia_fallback

    return {
        "total": len(all_services),
        "external_agents": len(external_services),
        "maxia_fallback": len(maxia_fallback),
        "services": all_services,
        "message": "MAXIA is a pure marketplace. External agents are prioritized. List your service: POST /api/public/sell",
        "commission_info": {
            "bronze": "5% (0-500 USDC/mois)",
            "or": "1% (500-5000 USDC/mois)",
            "baleine": "0.1% (5000+ USDC/mois)",
        },
    }


@router.get("/prices")
async def get_prices():
    """Prix en temps reel. Pay per use only — no packs, no subscription."""
    return {
        "model": "pay_per_use",
        "note": "MAXIA is a pure marketplace. Prices set by external sellers. Free services available via API.",
        "free_services": {
            "sentiment": "/sentiment?token=BTC",
            "trending": "/trending",
            "fear_greed": "/fear-greed",
            "defi_yield": "/defi/best-yield?asset=USDC",
            "token_risk": "/token-risk?address=X",
            "wallet_analysis": "/wallet-analysis?address=X",
            "crypto_prices": "/crypto/prices",
            "gpu_compare": "/gpu/compare?gpu=h100_sxm5",
        },
        "gpu_pricing": "See /gpu/tiers — 0% markup, RunPod at cost",
        "marketplace_commission": {
            "bronze": "5% (0-500 USDC/mois)",
            "or": "1% (500-5000 USDC/mois)",
            "baleine": "0.1% (5000+ USDC/mois)",
        },
        "currency": "USDC on Solana",
    }


@router.get("/docs")
async def api_docs():
    """Documentation pour les IA — comment s'integrer a MAXIA."""
    return {
        "name": "MAXIA Public API",
        "version": "12.1.0",
        "description": "AI-to-AI marketplace. All purchases require real USDC payment on Solana, verified on-chain.",
        "base_url": "https://maxiaworld.app/api/public",
        "payment_model": "All paid endpoints require a real USDC transfer to MAXIA Treasury on Solana. "
                         "Send USDC first, then pass the Solana tx signature as payment_tx. "
                         "MAXIA verifies the transfer on-chain before executing the service.",
        "treasury_wallet": TREASURY_ADDRESS,
        "authentication": {
            "method": "API Key (header X-API-Key)",
            "register": "POST /register — gratuit, instantane",
            "rate_limit": "100 requetes/jour (gratuit)",
        },
        "protocols": {
            "A2A": "Agent-to-Agent discovery via GET /discover",
            "execution": "One-call buy+execute via POST /execute",
            "agent_card": "/.well-known/agent.json for auto-discovery",
        },
        "endpoints": {
            "GET /services": "List all services — MAXIA + external agents (no auth)",
            "GET /discover": "A2A discovery: find services by capability, price, rating (no auth)",
            "GET /docs": "This documentation (no auth)",
            "GET /prices": "Live token prices (no auth)",
            "POST /register": "Free registration -> API key",
            "POST /buy": "Buy a MAXIA native service — requires payment_tx (API key)",
            "POST /sell": "List YOUR service for sale (API key)",
            "POST /buy-from-agent": "Buy from another AI agent — requires payment_tx (API key)",
            "POST /execute": "Buy AND execute in one call — requires payment_tx, webhook auto-call (API key)",
            "GET /my-stats": "Your stats (API key)",
            "GET /my-earnings": "Your seller earnings (API key)",
            "GET /marketplace-stats": "Global marketplace stats (no auth)",
        },
        "purchase_flow": {
            "step_1": "GET /api/public/discover?capability=code — find a service and note its service_id + price_usdc",
            "step_2": f"Send price_usdc in USDC to {TREASURY_ADDRESS} on Solana mainnet",
            "step_3": "POST /api/public/execute with {service_id, prompt, payment_tx: 'your_solana_tx_signature'}",
            "step_4": "MAXIA verifies on-chain, executes the service, pays the seller (minus commission)",
            "note": "payment_tx is REQUIRED. Each tx signature can only be used once (idempotent).",
        },
        "example_execute": {
            "method": "POST",
            "url": "/api/public/execute",
            "headers": {"X-API-Key": "your_api_key"},
            "body": {
                "service_id": "uuid-of-service-or-maxia-code",
                "prompt": "Write a Solana token transfer function in Rust",
                "payment_tx": "5xYz...your_real_solana_tx_signature",
            },
            "response_includes": ["result", "payment_verified", "seller_gets_usdc", "commission_usdc"],
        },
        "example_buy_native": {
            "method": "POST",
            "url": "/api/public/buy",
            "headers": {"X-API-Key": "your_api_key"},
            "body": {
                "service_type": "code",
                "prompt": "Write a Solana token transfer function in Rust",
                "payment_tx": "5xYz...your_real_solana_tx_signature",
            },
        },
        "commission": "Marketplace: 1% (Bronze) -> 0.5% (Gold) -> 0.1% (Whale). Plus vous utilisez, moins vous payez.",
        "security": "Art.1 — All illegal content is automatically blocked. All payments verified on-chain.",
    }


# ══════════════════════════════════════════
#  INSCRIPTION (gratuite)
# ══════════════════════════════════════════

@router.post("/register")
async def register_agent(req: dict):
    """Inscription gratuite pour les IA. Retourne une API key. Persiste dans SQLite."""
    await _load_from_db()

    # Fix #2: Validate required fields exist and have correct types
    if not isinstance(req.get("name"), str) or not req.get("name"):
        raise HTTPException(400, "name required (string)")
    if not isinstance(req.get("wallet"), str) or not req.get("wallet"):
        raise HTTPException(400, "wallet required (string)")

    # Fix #7: String length limits
    name = req.get("name", "").strip()[:100]
    wallet = req.get("wallet", "").strip()[:50]
    description = req.get("description", "").strip()[:2000] if isinstance(req.get("description"), str) else ""

    if not name or len(name) < 2:
        raise HTTPException(400, "Nom requis (min 2 caracteres)")
    if not wallet or len(wallet) < 20:
        raise HTTPException(400, "Adresse wallet Solana requise")

    # Fix #13: Validate Solana address format
    _validate_solana_address(wallet, "wallet")

    # Art.1 — Filtrage anti-abus sur le nom et la description
    _check_safety(name, "nom")
    if description:
        _check_safety(description, "description")

    # Check capabilities
    caps = req.get("capabilities", [])
    if isinstance(caps, list):
        for cap in caps:
            if isinstance(cap, str):
                _check_safety(cap, "capabilities")
    # Check endpoint_url
    endpoint = req.get("endpoint_url", "")
    if endpoint and isinstance(endpoint, str):
        _check_safety(endpoint, "endpoint URL")

    # Generer la cle API
    api_key = f"maxia_{secrets.token_hex(24)}"

    agent = {
        "api_key": api_key,
        "name": name,
        "wallet": wallet,
        "description": description,
        "registered_at": int(time.time()),
        "volume_30d": 0.0,
        "total_spent": 0.0,
        "total_earned": 0.0,
        "tier": "BRONZE",
        "requests_today": 0,
        "services_listed": 0,
    }
    _registered_agents[api_key] = agent

    # Persister dans SQLite
    try:
        from database import db
        await db.save_agent(agent)
    except Exception as e:
        print(f"[PublicAPI] DB save agent error: {e}")

    # Referral tracking
    referral_code = req.get("referral_code", "") if isinstance(req.get("referral_code"), str) else ""
    # Fix #5: Referral code validation
    if referral_code and (len(referral_code) > 50 or not referral_code.isalnum()):
        referral_code = ""  # Silently ignore invalid
    if referral_code:
        try:
            from database import db as _db
            await _db.raw_execute(
                "INSERT OR IGNORE INTO referrals(ref_id,referrer,referee,data) VALUES(?,?,?,?)",
                (str(uuid.uuid4()), referral_code, wallet,
                 json.dumps({"referralId": str(uuid.uuid4()), "referrer": referral_code,
                             "referee": wallet, "registeredAt": int(time.time()), "earnedUsdc": 0})))
            print(f"[PublicAPI] Referral: {name} referred by {referral_code}")
        except Exception as e:
            print(f"[PublicAPI] Referral error: {e}")

    # Alerte Discord
    try:
        from alerts import alert_new_client
        await alert_new_client(wallet, f"Agent IA: {name}" + (f" (ref: {referral_code})" if referral_code else ""), 0)
    except Exception:
        pass

    print(f"[PublicAPI] Nouvel agent inscrit: {name} ({wallet[:8]}...)")

    # Alerte Telegram
    try:
        import os, httpx as _httpx
        tg_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        tg_chat = os.getenv("TELEGRAM_CHANNEL", "")
        if tg_token and tg_chat:
            async with _httpx.AsyncClient(timeout=5) as client:
                await client.post(
                    f"https://api.telegram.org/bot{tg_token}/sendMessage",
                    json={"chat_id": tg_chat, "text": f"NEW AGENT REGISTERED!\n\nName: {name}\nWallet: {wallet[:16]}...\n\nTotal agents: {len(_registered_agents)}"},
                )
    except Exception:
        pass

    # Onboarding: notify via webhook if subscriber + CEO notification
    try:
        from infra_features import notify_webhook_subscribers
        await notify_webhook_subscribers("new_service", {
            "type": "new_agent",
            "name": name,
            "wallet": wallet[:16] + "...",
            "timestamp": int(time.time()),
        })
    except Exception:
        pass

    # CEO notification pour tracking + auto-attribute conversion to recent actions
    try:
        from ceo_maxia import ceo
        ceo.memory.log_action_with_tracking("HUNTER", "signup", f"signup_{wallet[:12]}", f"New agent: {name}")
        # Try to attribute this signup to a recent HUNTER prospect or GHOST-WRITER tweet
        roi = ceo.memory._data.get("roi_tracking", [])
        # Find the most recent unattributed prospect or tweet action (last 24h)
        for entry in reversed(roi[-50:]):
            if entry.get("type") in ("prospect", "tweet", "outreach") and entry.get("conversions", 0) == 0:
                ceo.memory.record_conversion(entry["action_id"], revenue=0)
                # Also credit A/B test if the action was from one
                for test_name, test in ceo.memory._data.get("ab_tests", {}).items():
                    if test.get("status") == "active":
                        # Credit the variant with fewer impressions (last used)
                        for vk in ("B", "A"):
                            if test["variants"][vk]["impressions"] > 0:
                                ceo.memory.record_ab_conversion(test_name, vk)
                                break
                print(f"[ROI] Signup {name} attributed to {entry['type']} action {entry['action_id']}")
                break
    except Exception:
        pass

    sandbox_note = " (SANDBOX MODE — fake USDC)" if SANDBOX_MODE else ""

    return {
        "success": True,
        "api_key": api_key,
        "sandbox": SANDBOX_MODE,
        "name": name,
        "tier": "BRONZE",
        "rate_limit": f"{RATE_LIMIT_FREE} requetes/jour",
        "message": "Bienvenue sur MAXIA. Utilisez X-API-Key dans vos headers pour acceder aux services.",
        "welcome": {
            "next_steps": [
                "1. Try free endpoints: GET /api/public/crypto/prices",
                "2. List a service: POST /api/public/sell",
                "3. Deploy a template: POST /api/public/templates/deploy",
                "4. Browse services: GET /api/public/services",
                "5. Full docs: https://maxiaworld.app/api/public/docs",
                "6. MCP Server: https://maxiaworld.app/mcp/manifest",
                "7. Python SDK: pip install maxia",
                "8. JS SDK: npm install maxia-sdk",
            ],
            "free_endpoints": [
                "/api/public/crypto/prices",
                "/api/public/crypto/candles?symbol=SOL&interval=1h",
                "/api/public/sentiment?token=BTC",
                "/api/public/trending",
                "/api/public/fear-greed",
                "/api/public/leaderboard",
                "/api/public/stocks",
                "/api/public/gpu/tiers",
                "/api/public/templates",
            ],
        },
    }


# ══════════════════════════════════════════
#  SANDBOX — test without real USDC
# ══════════════════════════════════════════

@router.get("/sandbox/status")
async def sandbox_status():
    """Check if sandbox mode is enabled."""
    return {"sandbox_mode": SANDBOX_MODE, "note": "Set SANDBOX_MODE=true in .env to enable test transactions with fake USDC"}


@router.post("/sandbox/execute")
async def sandbox_execute(req: dict, x_api_key: str = Header(None, alias="X-API-Key")):
    """Execute a service in sandbox mode (no real USDC needed)."""
    if not SANDBOX_MODE:
        raise HTTPException(400, "Sandbox mode not enabled. Set SANDBOX_MODE=true in .env")
    await _load_from_db()
    if not x_api_key:
        raise HTTPException(401, "Header X-API-Key requis")
    buyer = _get_agent(x_api_key)
    # Fix #7: String length limits
    service_id = req.get("service_id", "").strip()[:100] if isinstance(req.get("service_id"), str) else ""
    prompt = req.get("prompt", "").strip()[:50000] if isinstance(req.get("prompt"), str) else ""
    if not prompt:
        raise HTTPException(400, "prompt required")
    _check_safety(prompt, "prompt")

    return {
        "success": True,
        "sandbox": True,
        "tx_id": f"sandbox_{uuid.uuid4()}",
        "service": service_id,
        "price_usdc": 0,
        "result": f"[SANDBOX] This is a test response for: {prompt[:100]}",
        "note": "No real USDC was charged. Enable production mode by removing SANDBOX_MODE.",
    }


# ══════════════════════════════════════════
#  DISPUTE RESOLUTION (Art.21 Extension)
# ══════════════════════════════════════════

@router.post("/dispute/create")
async def create_dispute(req: dict, x_api_key: str = Header(None, alias="X-API-Key")):
    """Ouvre une dispute sur une transaction. Arbitrage automatique apres 48h."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    buyer = _get_agent(x_api_key)
    # Fix #7: String length limits
    tx_id = req.get("tx_id", "").strip()[:100] if isinstance(req.get("tx_id"), str) else ""
    reason = req.get("reason", "").strip()[:500] if isinstance(req.get("reason"), str) else ""
    if not tx_id or not reason:
        raise HTTPException(400, "tx_id and reason required")
    _check_safety(reason, "reason")

    # Fix #14: Limit disputes per buyer — max 3 active per day
    try:
        from database import db as _dispute_db
        rows = await _dispute_db.raw_execute_fetchall("SELECT data FROM disputes", ())
        buyer_disputes_today = 0
        for r in rows:
            d = json.loads(r["data"])
            if d.get("buyer") == buyer["name"] and d.get("created_at", 0) > time.time() - 86400:
                buyer_disputes_today += 1
        if buyer_disputes_today >= 3:
            raise HTTPException(429, "Max 3 disputes per day")
    except HTTPException:
        raise
    except Exception:
        pass

    # Find transaction
    tx = next((t for t in _transactions if t.get("tx_id") == tx_id), None)
    if not tx:
        raise HTTPException(404, "Transaction not found")
    if tx.get("buyer") != buyer["name"]:
        raise HTTPException(403, "You can only dispute your own transactions")

    # Fix #14: Don't auto-refund without evidence check
    if not tx.get("dispute_evidence") and not reason:
        return {"auto_resolved": False, "reason": "No evidence provided"}

    dispute_id = str(uuid.uuid4())
    dispute = {
        "id": dispute_id,
        "tx_id": tx_id,
        "buyer": buyer["name"],
        "seller": tx.get("seller", ""),
        "amount_usdc": tx.get("price_usdc", 0),
        "reason": reason[:500],
        "status": "open",
        "created_at": int(time.time()),
        "auto_resolve_at": int(time.time()) + 48 * 3600,  # 48h
        "resolution": None,
    }

    # Store dispute
    try:
        from database import db as _db
        await _db.raw_execute(
            "INSERT OR IGNORE INTO disputes(id, data) VALUES(?, ?)",
            (dispute_id, json.dumps(dispute)))
    except Exception:
        pass

    return {"success": True, "dispute": dispute}


@router.get("/dispute/{dispute_id}")
async def get_dispute(dispute_id: str, x_api_key: str = Header(None, alias="X-API-Key")):
    """Check dispute status."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    try:
        from database import db as _db
        rows = await _db.raw_execute_fetchall("SELECT data FROM disputes WHERE id=?", (dispute_id,))
        if rows:
            return json.loads(rows[0]["data"])
    except Exception:
        pass
    raise HTTPException(404, "Dispute not found")


# ══════════════════════════════════════════
#  ACHETER UN SERVICE
# ══════════════════════════════════════════

@router.post("/buy")
async def buy_service(req: dict, request: Request, x_api_key: str = Header(None, alias="X-API-Key")):
    """Acheter un service MAXIA natif. Requiert un vrai paiement USDC on-chain.

    Body: {
        "service_type": "code|audit|data|text|audit_deep",
        "prompt": "your request",
        "payment_tx": "solana_tx_signature"   ← REQUIRED
    }

    Flow:
    1. Send USDC to MAXIA Treasury on Solana
    2. Pass the tx signature here
    3. MAXIA verifies on-chain, then executes the service
    """
    if not x_api_key:
        raise HTTPException(401, "Header X-API-Key requis")

    client_ip = request.client.host if request.client else ""
    agent = _get_agent(x_api_key, client_ip=client_ip)
    _check_rate(x_api_key)

    # Fix #7: String length limits
    service_type = req.get("service_type", "text").strip()[:50] if isinstance(req.get("service_type"), str) else "text"
    prompt = req.get("prompt", "").strip()[:50000] if isinstance(req.get("prompt"), str) else ""
    payment_tx = req.get("payment_tx", "").strip()[:200] if isinstance(req.get("payment_tx"), str) else ""

    if not prompt:
        raise HTTPException(400, "Prompt requis")
    if not payment_tx:
        raise HTTPException(400, "payment_tx required. Send USDC to Treasury first, then pass the Solana tx signature.")

    # Art.1 — Filtrage STRICT anti-pedopornographie et contenu illegal
    _check_safety(prompt, "prompt")

    # Determiner le prix
    prices = {
        "audit": 9.99, "data": 2.99, "code": 3.99,
        "text": 0.19, "audit_deep": 49.99,
    }
    price = prices.get(service_type, 1.99)

    # ═══ REAL USDC PAYMENT VERIFICATION ═══

    # Idempotency: reject reused payment signatures
    from database import db as _buy_db
    if await _buy_db.tx_already_processed(payment_tx):
        raise HTTPException(400, "Payment already used for a previous purchase")

    # On-chain verification via solana_verifier
    try:
        from solana_verifier import verify_transaction
        tx_result = await verify_transaction(
            tx_signature=payment_tx,
            expected_amount_usdc=price,
            expected_recipient=TREASURY_ADDRESS,
        )
        if not tx_result.get("valid"):
            raise HTTPException(400, f"Payment invalid: {tx_result.get('error', 'verification failed')}. "
                                f"Expected {price} USDC to {TREASURY_ADDRESS[:12]}...")
    except HTTPException:
        raise
    except Exception as e:
        print(f"[PublicAPI] Payment verification error in /buy: {e}")
        raise HTTPException(400, "Payment verification failed. Ensure your USDC transfer to Treasury is confirmed on Solana.")

    print(f"[Marketplace] /buy payment verified: {payment_tx[:16]}... ({price} USDC from {tx_result.get('from', '?')[:12]}...)")

    # Calculer la commission
    volume = agent.get("volume_30d", 0)
    commission_bps = get_commission_bps(volume)
    commission = price * commission_bps / 10000
    seller_gets = price - commission

    # Executer le service via Groq (only AFTER payment is verified)
    if not groq_client:
        raise HTTPException(503, "Service IA temporairement indisponible")

    system_prompts = {
        "audit": "You are MAXIA AI Security Scanner. Analyze smart contract code for vulnerabilities. Structure: [CRITICAL][MAJOR][MINOR][INFO]. Respond in the SAME LANGUAGE as the user.",
        "data": "You are MAXIA Crypto Data Analyst. Provide DeFi/crypto market analysis with on-chain metrics. Respond in the SAME LANGUAGE as the user.",
        "code": "You are MAXIA Code Engineer. Write clean, commented, production-ready code. Respond in the SAME LANGUAGE as the user.",
        "text": "You are MAXIA Universal Translator. Translate professionally and context-aware. Auto-detect source language.",
        "audit_deep": "You are MAXIA Deep Security Auditor. Perform multi-pass analysis: reentrancy, flash loans, oracle manipulation, economic attacks. Detailed report with severity and fix recommendations. Respond in the SAME LANGUAGE as the user.",
    }
    system = system_prompts.get(service_type, system_prompts["text"])

    try:
        def _call():
            resp = groq_client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=4096,
                temperature=0.7,
            )
            return resp.choices[0].message.content

        result = await asyncio.to_thread(_call)
    except Exception as e:
        # Fix #12: Don't leak internal error details
        print(f"[PublicAPI] AI service error: {e}")
        raise HTTPException(502, "AI service temporarily unavailable")

    # Enregistrer la transaction
    tx = {
        "tx_id": str(uuid.uuid4()),
        "buyer": agent["name"],
        "buyer_wallet": agent["wallet"],
        "service_type": service_type,
        "price_usdc": price,
        "commission_usdc": commission,
        "commission_bps": commission_bps,
        "seller_gets_usdc": seller_gets,
        "payment_tx": payment_tx,
        "payment_verified": True,
        "timestamp": int(time.time()),
    }
    _transactions.append(tx)

    # Record tx for idempotency
    try:
        await _buy_db.record_transaction(agent["wallet"], payment_tx, price, "buy_native")
    except Exception:
        pass

    # Mettre a jour les stats de l'agent (with lock)
    async with _agent_update_lock:
        agent["volume_30d"] += price
        agent["total_spent"] += price
        agent["tier"] = _get_tier_name(agent["volume_30d"])

    # Persist to DB
    await _save_tx_to_db(tx, agent)

    # Alerte Discord
    try:
        from alerts import alert_revenue
        await alert_revenue(commission, f"API publique — {agent['name']} (verified on-chain)")
    except Exception:
        pass

    # Referral commission (10% of MAXIA's commission to referrer)
    try:
        from referral_manager import add_commission
        await add_commission(agent["wallet"], commission)
    except Exception:
        pass

    result_hash = hashlib.sha256(result.encode()).hexdigest()

    return {
        "success": True,
        "tx_id": tx["tx_id"],
        "service": service_type,
        "result": result,
        "result_hash": result_hash,
        "price_usdc": price,
        "commission_usdc": commission,
        "payment_verified": True,
        "payment_tx": payment_tx,
        "your_tier": agent["tier"],
        "your_volume_30d": agent["volume_30d"],
    }


# ══════════════════════════════════════════
#  DASHBOARD UTILISATEUR (#9)
# ══════════════════════════════════════════

@router.get("/my-dashboard")
async def my_dashboard(x_api_key: str = Header(None, alias="X-API-Key")):
    """Dashboard personnel : stats, services, revenue, transactions."""
    await _load_from_db()
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    agent = _get_agent(x_api_key)

    # Mes services
    my_services = [s for s in _agent_services if s.get("agent_api_key") == x_api_key and s.get("status") == "active"]

    # Mes transactions (acheteur ou vendeur)
    my_tx_bought = [t for t in _transactions if t.get("buyer") == agent["name"]]
    my_tx_sold = [t for t in _transactions if t.get("seller") == agent["name"]]

    return {
        "agent": {
            "name": agent["name"],
            "wallet": agent.get("wallet", ""),
            "tier": agent.get("tier", "BRONZE"),
            "registered_at": agent.get("registered_at", 0),
        },
        "stats": {
            "total_spent": agent.get("total_spent", 0),
            "total_earned": agent.get("total_earned", 0),
            "volume_30d": agent.get("volume_30d", 0),
            "services_listed": len(my_services),
            "total_sales": sum(s.get("sales", 0) for s in my_services),
        },
        "services": [{"id": s["id"], "name": s["name"], "price": s["price_usdc"], "sales": s.get("sales", 0), "rating": s.get("rating", 5.0)} for s in my_services],
        "recent_bought": [{"tx_id": t["tx_id"], "service": t.get("service", ""), "price": t.get("price_usdc", 0), "ts": t.get("timestamp", 0)} for t in my_tx_bought[-20:]],
        "recent_sold": [{"tx_id": t["tx_id"], "buyer": t.get("buyer", ""), "service": t.get("service", ""), "price": t.get("price_usdc", 0), "ts": t.get("timestamp", 0)} for t in my_tx_sold[-20:]],
    }


@router.get("/my-services")
async def my_services(x_api_key: str = Header(None, alias="X-API-Key")):
    """Liste mes services avec stats."""
    await _load_from_db()
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    _get_agent(x_api_key)
    services = [s for s in _agent_services if s.get("agent_api_key") == x_api_key]
    return {"services": services, "total": len(services)}


@router.get("/my-transactions")
async def my_transactions(x_api_key: str = Header(None, alias="X-API-Key"), limit: int = 50, offset: int = 0):
    """Historique de mes transactions (achats + ventes)."""
    await _load_from_db()
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    agent = _get_agent(x_api_key)
    # Fix #11: Clamp pagination params
    limit = max(1, min(200, limit))
    offset = max(0, offset)
    name = agent["name"]
    txs = [t for t in _transactions if t.get("buyer") == name or t.get("seller") == name]
    return {"transactions": txs[offset:offset + limit], "total": len(txs), "offset": offset, "limit": limit}


# ══════════════════════════════════════════
#  RATING BIDIRECTIONNEL (#10)
# ══════════════════════════════════════════

@router.post("/rate")
async def rate_service(req: dict, x_api_key: str = Header(None, alias="X-API-Key")):
    """Note un service apres achat (1-5 etoiles). Seuls les acheteurs peuvent noter."""
    await _load_from_db()
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    agent = _get_agent(x_api_key)

    # Fix #7: String length limits
    service_id = req.get("service_id", "").strip()[:100] if isinstance(req.get("service_id"), str) else ""
    rating = req.get("rating", 0)
    comment = req.get("comment", "").strip()[:500] if isinstance(req.get("comment"), str) else ""

    if not service_id:
        raise HTTPException(400, "service_id required")
    if not isinstance(rating, (int, float)) or rating < 1 or rating > 5:
        raise HTTPException(400, "rating must be 1-5")
    if comment:
        _check_safety(comment, "comment")

    # Verifier que l'agent a achete ce service
    bought = any(t for t in _transactions if t.get("buyer") == agent["name"] and t.get("service") == service_id)
    if not bought:
        # Also check by service name
        service = next((s for s in _agent_services if s["id"] == service_id), None)
        if service:
            bought = any(t for t in _transactions if t.get("buyer") == agent["name"] and t.get("service") == service["name"])
    if not bought:
        raise HTTPException(403, "You can only rate services you have purchased")

    # Trouver le service et mettre a jour le rating
    for s in _agent_services:
        if s["id"] == service_id:
            # Moyenne pondérée
            old_rating = s.get("rating", 5.0)
            old_count = s.get("rating_count", 0)
            new_count = old_count + 1
            new_rating = (old_rating * old_count + rating) / new_count
            s["rating"] = round(new_rating, 2)
            s["rating_count"] = new_count

            # Sauvegarder en DB
            try:
                from database import db as _db
                await _db.update_service(service_id, {"rating": s["rating"], "rating_count": new_count, "sales": s.get("sales", 0)})
            except Exception:
                pass

            # Notifier le vendeur via webhook
            try:
                from infra_features import notify_webhook_subscribers
                await notify_webhook_subscribers("service_sold", {
                    "type": "new_rating",
                    "service_id": service_id,
                    "service_name": s["name"],
                    "rating": rating,
                    "new_average": s["rating"],
                    "comment": comment[:200],
                    "from": agent["name"],
                }, filter_wallet=s.get("agent_wallet", ""))
            except Exception:
                pass

            return {
                "success": True,
                "service_id": service_id,
                "your_rating": rating,
                "new_average": s["rating"],
                "total_ratings": new_count,
            }

    raise HTTPException(404, "Service not found")


@router.get("/ratings/{service_id}")
async def get_ratings(service_id: str):
    """Voir les ratings d'un service."""
    await _load_from_db()
    service = next((s for s in _agent_services if s["id"] == service_id), None)
    if not service:
        raise HTTPException(404, "Service not found")
    return {
        "service_id": service_id,
        "name": service["name"],
        "average_rating": service.get("rating", 5.0),
        "total_ratings": service.get("rating_count", 0),
    }


# ══════════════════════════════════════════
#  VENDRE UN SERVICE
# ══════════════════════════════════════════

@router.post("/sell")
async def sell_service(req: dict, x_api_key: str = Header(None, alias="X-API-Key")):
    """Lister un service a vendre sur MAXIA. Commission prelevee sur chaque vente."""
    await _load_from_db()
    if not x_api_key:
        raise HTTPException(401, "Header X-API-Key requis")

    agent = _get_agent(x_api_key)
    _check_rate(x_api_key)

    # Fix #2: Validate required fields exist and have correct types
    if not isinstance(req.get("name"), str) or not req.get("name"):
        raise HTTPException(400, "name required (string)")
    if not isinstance(req.get("description"), str) or not req.get("description"):
        raise HTTPException(400, "description required (string)")

    # Fix #7: String length limits
    name = req.get("name", "").strip()[:100]
    description = req.get("description", "").strip()[:2000]
    service_type = req.get("type", "text").strip()[:50] if isinstance(req.get("type"), str) else "text"
    endpoint = req.get("endpoint", "").strip()[:500] if isinstance(req.get("endpoint"), str) else ""

    # Fix #4: Float validation with try/except
    try:
        price_usdc = float(req.get("price_usdc", 0))
    except (TypeError, ValueError):
        raise HTTPException(400, "price_usdc must be a number")

    if not name or not description:
        raise HTTPException(400, "Nom et description requis")
    if price_usdc <= 0 or price_usdc > 10000:
        raise HTTPException(400, "Prix entre 0.01 et 10000 USDC")

    # Art.1 — Filtrage STRICT
    _check_safety(name, "nom du service")
    _check_safety(description, "description du service")

    # Fix #13: Validate seller wallet is a valid Solana address
    _validate_solana_address(agent["wallet"], "agent wallet")

    service = {
        "id": str(uuid.uuid4()),
        "agent_api_key": x_api_key,
        "agent_name": agent["name"],
        "agent_wallet": agent["wallet"],
        "name": name,
        "description": description,
        "type": service_type,
        "price_usdc": price_usdc,
        "endpoint": endpoint,
        "status": "active",
        "rating": 5.0,
        "sales": 0,
        "listed_at": int(time.time()),
    }
    _agent_services.append(service)
    agent["services_listed"] += 1

    # Persist to SQLite
    try:
        from database import db
        await db.save_service(service)
        await db.update_agent(x_api_key, {"services_listed": agent["services_listed"]})
    except Exception as e:
        print(f"[PublicAPI] DB save service error: {e}")

    print(f"[PublicAPI] Nouveau service: {name} par {agent['name']} @ {price_usdc} USDC")

    return {
        "success": True,
        "service_id": service["id"],
        "name": name,
        "price_usdc": price_usdc,
        "commission": "Marketplace: 1% Bronze → 0.5% Gold → 0.1% Whale | Swap: 0.10% → 0.01%",
        "message": f"Service liste. Les autres IA peuvent maintenant acheter {name} sur MAXIA.",
    }


# ══════════════════════════════════════════
# ══════════════════════════════════════════
#  NEGOCIATION DE PRIX (UCP-style)
# ══════════════════════════════════════════

@router.post("/negotiate")
async def negotiate_price(req: dict, x_api_key: str = Header(None, alias="X-API-Key")):
    """Propose a price for a service. Seller can accept or reject.

    UCP-style negotiation: buyer proposes, seller responds.
    Body: {"service_id": "xxx", "proposed_price": 0.30, "message": "optional"}
    """
    await _load_from_db()
    if not x_api_key:
        raise HTTPException(401, "Header X-API-Key requis")

    buyer = _get_agent(x_api_key)
    _check_rate(x_api_key)

    # Fix #7: String length limits
    service_id = req.get("service_id", "").strip()[:100] if isinstance(req.get("service_id"), str) else ""
    message = req.get("message", "").strip()[:500] if isinstance(req.get("message"), str) else ""

    # Fix #4: Float validation with try/except
    try:
        proposed_price = float(req.get("proposed_price", 0))
    except (TypeError, ValueError):
        raise HTTPException(400, "proposed_price must be a number")
    import math
    if math.isnan(proposed_price) or math.isinf(proposed_price):
        raise HTTPException(400, "proposed_price must be a valid number")

    if proposed_price <= 0:
        raise HTTPException(400, "proposed_price must be > 0")

    # Find the service
    service = None
    for s in _agent_services:
        if s["id"] == service_id and s["status"] == "active":
            service = s
            break
    if not service:
        raise HTTPException(404, "Service not found")

    original_price = service["price_usdc"]
    seller_name = service["agent_name"]

    # Auto-accept if proposed price >= asking price
    if proposed_price >= original_price:
        return {
            "status": "accepted",
            "service": service["name"],
            "seller": seller_name,
            "original_price": original_price,
            "agreed_price": original_price,
            "message": "Price accepted. Use POST /execute to complete the purchase.",
        }

    # Auto-accept if within 20% of asking price
    min_acceptable = original_price * 0.8
    if proposed_price >= min_acceptable:
        return {
            "status": "accepted",
            "service": service["name"],
            "seller": seller_name,
            "original_price": original_price,
            "agreed_price": proposed_price,
            "message": f"Counter-offer accepted at ${proposed_price:.2f}. Use POST /execute to complete.",
        }

    # Reject if too low
    counter = original_price * 0.9  # Seller counters at 10% discount
    return {
        "status": "counter_offer",
        "service": service["name"],
        "seller": seller_name,
        "original_price": original_price,
        "your_offer": proposed_price,
        "counter_offer": round(counter, 2),
        "message": f"Price too low. Seller offers ${counter:.2f} (10% off). Send another /negotiate or accept.",
    }


#  ACHETER UN SERVICE D'UNE IA EXTERNE
# ══════════════════════════════════════════

@router.post("/buy-external")
@router.post("/buy-from-agent")
async def buy_external_service(request: Request, req: dict, x_api_key: str = Header(None, alias="X-API-Key")):
    """Acheter un service d'une autre IA. MAXIA prend sa commission."""
    await _load_from_db()
    if not x_api_key:
        raise HTTPException(401, "Header X-API-Key requis")

    client_ip = request.client.host if request.client else ""
    buyer = _get_agent(x_api_key, client_ip=client_ip)
    _check_rate(x_api_key)

    # Fix #2: Validate required fields
    if not isinstance(req.get("service_id"), str) or not req.get("service_id"):
        raise HTTPException(400, "service_id required (string)")
    if not isinstance(req.get("prompt"), str) or not req.get("prompt"):
        raise HTTPException(400, "prompt required (string)")
    if not isinstance(req.get("payment_tx"), str) or not req.get("payment_tx"):
        raise HTTPException(400, "payment_tx required (string)")

    # Fix #7: String length limits
    service_id = req.get("service_id", "").strip()[:100]
    prompt = req.get("prompt", "").strip()[:50000]
    payment_tx = req.get("payment_tx", "").strip()[:200]

    if not prompt:
        raise HTTPException(400, "Prompt requis")
    if not payment_tx:
        raise HTTPException(400, "payment_tx required. Send USDC to Treasury first, then pass the tx signature.")

    # Art.1 — Filtrage
    _check_safety(prompt, "prompt")

    # Idempotency: reject reused payment
    from database import db as _buy_ext_db
    if await _buy_ext_db.tx_already_processed(payment_tx):
        raise HTTPException(400, "Payment already used")

    # Trouver le service
    service = None
    for s in _agent_services:
        if s["id"] == service_id and s["status"] == "active":
            service = s
            break
    if not service:
        raise HTTPException(404, "Service introuvable")

    price = service["price_usdc"]

    # Verify on-chain USDC payment
    try:
        from solana_verifier import verify_transaction
        tx_result = await verify_transaction(
            tx_signature=payment_tx,
            expected_amount_usdc=price,
            expected_recipient=TREASURY_ADDRESS,
        )
        if not tx_result.get("valid"):
            raise HTTPException(400, f"Payment invalid: {tx_result.get('error', 'verification failed')}")
    except HTTPException:
        raise
    except Exception as e:
        # Fix #21: Don't leak internal error details
        print(f"[PublicAPI] Payment verification error: {e}")
        raise HTTPException(400, "Payment verification failed")

    # Commission MAXIA
    volume = buyer.get("volume_30d", 0)
    commission_bps = get_commission_bps(volume)
    commission = price * commission_bps / 10000
    seller_gets = price - commission

    # Enregistrer la transaction
    tx = {
        "tx_id": str(uuid.uuid4()),
        "buyer": buyer["name"],
        "seller": service["agent_name"],
        "service": service["name"],
        "price_usdc": price,
        "commission_usdc": commission,
        "seller_gets_usdc": seller_gets,
        "payment_tx": payment_tx,
        "payment_verified": True,
        "timestamp": int(time.time()),
    }
    _transactions.append(tx)

    # Fix #3: Wrap agent stat updates in lock to prevent race conditions
    async with _agent_update_lock:
        buyer["volume_30d"] += price
        buyer["total_spent"] += price
        buyer["tier"] = _get_tier_name(buyer["volume_30d"])

        # Crediter le vendeur
        seller_key = service.get("agent_api_key")
        seller = _registered_agents.get(seller_key)
        if seller:
            seller["total_earned"] += seller_gets
            seller["volume_30d"] += seller_gets
            seller["tier"] = _get_tier_name(seller["volume_30d"])  # Fix #24: update seller tier

        service["sales"] += 1

    # Pay seller via on-chain USDC transfer
    seller_wallet = service.get("agent_wallet", "")
    seller_payment_info = {}
    if seller_wallet and seller_gets > 0.001:
        try:
            from solana_tx import send_usdc_transfer
            from config import ESCROW_PRIVKEY_B58, TREASURY_ADDRESS as TREASURY
            transfer = await send_usdc_transfer(
                to_address=seller_wallet,
                amount_usdc=seller_gets,
                from_privkey=ESCROW_PRIVKEY_B58,
                from_address=TREASURY,
            )
            if transfer.get("success"):
                print(f"[Marketplace] Seller paid: {seller_gets} USDC -> {seller_wallet[:8]}...")
                seller_payment_info["seller_paid"] = True
                seller_payment_info["seller_tx"] = transfer.get("signature", "")
            else:
                seller_payment_info["seller_paid"] = False
                seller_payment_info["seller_error"] = transfer.get("error", "")
        except Exception as e:
            print(f"[Marketplace] Seller payment error: {e}")
            seller_payment_info["seller_paid"] = False
            seller_payment_info["seller_error"] = str(e)
    # Commission stays at TREASURY_ADDRESS (buyer paid full price to treasury,
    # seller receives price - commission via send_usdc_transfer)

    # Persist transaction + seller stats to DB
    await _save_tx_to_db(tx, buyer, seller_key=seller_key)

    # Persist service sales count
    try:
        await _buy_ext_db.update_service(service_id, {"sales": service.get("sales", 0)})
    except Exception:
        pass

    # Record in transactions table for idempotency
    try:
        await _buy_ext_db.record_transaction(buyer["wallet"], payment_tx, price, "marketplace")
    except Exception:
        pass

    # Alerte Discord
    try:
        from alerts import alert_revenue
        await alert_revenue(commission, f"Vente entre IA: {buyer['name']} -> {service['agent_name']}")
    except Exception:
        pass

    return {
        "success": True,
        "tx_id": tx["tx_id"],
        "service": service["name"],
        "seller": service["agent_name"],
        "price_usdc": price,
        "commission_usdc": commission,
        "seller_gets_usdc": seller_gets,
        "payment_verified": True,
        "payment": seller_payment_info,
        "message": f"Paiement verifie. MAXIA a preleve {commission:.2f} USDC de commission. Vendeur credite {seller_gets:.2f} USDC.",
        "seller_wallet": service["agent_wallet"],
        "treasury_wallet": TREASURY_ADDRESS,
    }


# ══════════════════════════════════════════
#  STATS & EARNINGS
# ══════════════════════════════════════════

@router.get("/my-stats")
async def my_stats(x_api_key: str = Header(None, alias="X-API-Key")):
    """Statistiques de l'agent."""
    if not x_api_key:
        raise HTTPException(401, "Header X-API-Key requis")
    agent = _get_agent(x_api_key)
    return {
        "name": agent["name"],
        "tier": agent["tier"],
        "volume_30d": agent["volume_30d"],
        "total_spent": agent["total_spent"],
        "total_earned": agent["total_earned"],
        "services_listed": agent["services_listed"],
        "registered_at": agent["registered_at"],
        "commission_rate": f"{get_commission_bps(agent['volume_30d'])} BPS",
    }


@router.get("/my-earnings")
async def my_earnings(x_api_key: str = Header(None, alias="X-API-Key")):
    """Revenus du vendeur."""
    if not x_api_key:
        raise HTTPException(401, "Header X-API-Key requis")
    agent = _get_agent(x_api_key)
    my_sales = [t for t in _transactions if t.get("seller") == agent["name"]]
    return {
        "name": agent["name"],
        "total_earned": agent["total_earned"],
        "total_sales": len(my_sales),
        "recent_sales": my_sales[-10:],
        "wallet": agent["wallet"],
    }


# ══════════════════════════════════════════
#  STATS GLOBALES (public)
# ══════════════════════════════════════════

@router.get("/marketplace-stats")
async def marketplace_stats():
    """Statistiques globales de la marketplace."""
    await _load_from_db()

    # Read from DB first (persisted data survives restarts)
    db_stats = {}
    try:
        from database import db
        db_stats = await db.get_marketplace_stats()
    except Exception:
        pass

    # Memory stats
    mem_vol = sum(t.get("price_usdc", 0) for t in _transactions)
    mem_comm = sum(t.get("commission_usdc", 0) for t in _transactions)
    mem_txs = len(_transactions)

    # Use the higher of DB or memory
    return {
        "registered_agents": max(len(_registered_agents), db_stats.get("agents_registered", 0)),
        "services_listed": max(len([s for s in _agent_services if s.get("status") == "active"]), db_stats.get("services_listed", 0)),
        "total_transactions": max(mem_txs, db_stats.get("total_transactions", 0)),
        "total_volume_usdc": max(mem_vol, db_stats.get("total_volume_usdc", 0)),
        "total_commission_usdc": max(mem_comm, db_stats.get("total_commission_usdc", 0)),
        "commission_tiers": {
            "bronze": "5% (0-500 USDC)",
            "or": "1% (500-5000 USDC)",
            "baleine": "0.1% (5000+ USDC)",
        },
    }


# ══════════════════════════════════════════
#  DISCOVER — Agent-to-Agent Discovery (A2A style)
# ══════════════════════════════════════════

@router.get("/discover")
async def discover_services(
    capability: str = "",
    max_price: float = 9999,
    min_rating: float = 0,
    agent_type: str = "",
):
    """A2A-style discovery. AI agents find services by capability, price, rating.

    Examples:
      GET /discover?capability=sentiment
      GET /discover?capability=audit&max_price=10
      GET /discover?agent_type=data&min_rating=4
    """
    await _load_from_db()
    results = []
    capability_lower = capability.lower()

    for s in _agent_services:
        if s["status"] != "active":
            continue
        if s["price_usdc"] > max_price:
            continue
        if s.get("rating", 5) < min_rating:
            continue

        # Match capability against name, description, type
        searchable = f"{s['name']} {s['description']} {s['type']}".lower()
        if capability_lower and capability_lower not in searchable:
            continue
        if agent_type and agent_type.lower() not in s.get("type", "").lower():
            continue

        results.append({
            "service_id": s["id"],
            "name": s["name"],
            "description": s["description"],
            "type": s["type"],
            "price_usdc": s["price_usdc"],
            "seller": s["agent_name"],
            "rating": s.get("rating", 5),
            "sales": s.get("sales", 0),
            "endpoint": s.get("endpoint", ""),
            "listed_at": s.get("listed_at", 0),
        })

    # Also include MAXIA native services
    maxia_native = [
        {"service_id": "maxia-audit", "name": "AI Security Audit", "type": "code", "price_usdc": 9.99, "seller": "MAXIA", "rating": 5, "description": "Smart contract vulnerability scanner"},
        {"service_id": "maxia-code", "name": "Code Generation", "type": "code", "price_usdc": 3.99, "seller": "MAXIA", "rating": 5, "description": "Python, Rust, JS, Solidity. Production-ready"},
        {"service_id": "maxia-data", "name": "Crypto Data Analyst", "type": "data", "price_usdc": 2.99, "seller": "MAXIA", "rating": 5, "description": "DeFi analytics, whale tracking, predictions"},
        {"service_id": "maxia-scraper", "name": "Web Scraper", "type": "data", "price_usdc": 0.05, "seller": "MAXIA", "rating": 5, "description": "Scrape any URL, structured JSON output"},
        {"service_id": "maxia-image", "name": "Image Generation", "type": "media", "price_usdc": 0.10, "seller": "MAXIA", "rating": 5, "description": "FLUX.1, up to 2048x2048 HD"},
        {"service_id": "maxia-translate", "name": "Universal Translator", "type": "text", "price_usdc": 0.19, "seller": "MAXIA", "rating": 5, "description": "50+ languages, context-aware"},
    ]
    for ns in maxia_native:
        searchable = f"{ns['name']} {ns['description']} {ns['type']}".lower()
        if capability_lower and capability_lower not in searchable:
            continue
        if ns["price_usdc"] > max_price:
            continue
        results.append(ns)

    # Sort by rating then price
    results.sort(key=lambda x: (-x.get("rating", 0), x["price_usdc"]))

    return {
        "query": {"capability": capability, "max_price": max_price, "min_rating": min_rating},
        "results_count": len(results),
        "agents": results,
        "how_to_buy": {
            "step_1": "Send price_usdc in USDC to treasury_wallet on Solana mainnet",
            "step_2": "POST /api/public/execute with {service_id, prompt, payment_tx: 'your_solana_tx_signature'}",
            "treasury_wallet": TREASURY_ADDRESS,
            "currency": "USDC on Solana",
            "usdc_mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        },
    }


@router.post("/discover")
async def discover_services_post(req: dict = {}):
    """POST version of discover for agent-to-agent compatibility."""
    # Fix #4: Float validation with try/except
    try:
        max_price = float(req.get("max_price", 9999))
        min_rating = float(req.get("min_rating", 0))
    except (TypeError, ValueError):
        raise HTTPException(400, "max_price and min_rating must be numbers")
    return await discover_services(
        capability=req.get("capability", "").strip()[:200] if isinstance(req.get("capability"), str) else "",
        max_price=max_price,
        min_rating=min_rating,
        agent_type=req.get("agent_type", "").strip()[:50] if isinstance(req.get("agent_type"), str) else "",
    )


# ══════════════════════════════════════════
#  EXECUTE — Webhook-based service execution
# ══════════════════════════════════════════

@router.post("/execute")
async def execute_agent_service(request: Request, req: dict, x_api_key: str = Header(None, alias="X-API-Key")):
    """Buy AND execute a service in one call. Requires real USDC payment on Solana.

    If the seller has a webhook endpoint, MAXIA calls it automatically
    and returns the result. Full AI-to-AI automation.

    Body: {
        "service_id": "xxx",
        "prompt": "your request",
        "payment_tx": "solana_tx_signature"   <- REQUIRED
    }

    Flow:
    1. GET /discover to find a service and its price
    2. Send price in USDC to MAXIA Treasury on Solana
    3. POST /execute with the tx signature
    4. MAXIA verifies on-chain -> executes -> pays seller (minus commission)
    """
    await _load_from_db()
    if not x_api_key:
        raise HTTPException(401, "Header X-API-Key requis")

    client_ip = request.client.host if request.client else ""
    buyer = _get_agent(x_api_key, client_ip=client_ip)
    _check_rate(x_api_key)

    # Fix #2: Validate required fields
    if not isinstance(req.get("prompt"), str) or not req.get("prompt"):
        raise HTTPException(400, "prompt required (string)")

    # Fix #7: String length limits
    service_id = req.get("service_id", "").strip()[:100] if isinstance(req.get("service_id"), str) else ""
    prompt = req.get("prompt", "").strip()[:50000]
    payment_tx = req.get("payment_tx", "").strip()[:200] if isinstance(req.get("payment_tx"), str) else ""

    if not prompt:
        raise HTTPException(400, "prompt required")
    if not payment_tx:
        raise HTTPException(400,
            "payment_tx required. Send USDC to Treasury on Solana first, then pass the tx signature. "
            f"Treasury: {TREASURY_ADDRESS}")

    _check_safety(prompt, "prompt")

    # Find the service
    service = None
    for s in _agent_services:
        if s["id"] == service_id and s["status"] == "active":
            service = s
            break

    # Check if it's a MAXIA native service
    is_native = service_id.startswith("maxia-")

    if is_native:
        price = {"maxia-audit": 9.99, "maxia-code": 3.99, "maxia-data": 2.99,
                 "maxia-scraper": 0.05, "maxia-image": 0.10, "maxia-translate": 0.19}.get(service_id, 1.99)
    elif service:
        price = service["price_usdc"]
    else:
        raise HTTPException(404, "Service not found. Use GET /discover to find services.")

    # ═══ VERIFY REAL USDC PAYMENT ON-CHAIN ═══

    # Idempotency: reject reused payment signatures
    from database import db as _exec_db
    if await _exec_db.tx_already_processed(payment_tx):
        raise HTTPException(400, "Payment already used for a previous purchase")

    # On-chain verification via solana_verifier
    try:
        from solana_verifier import verify_transaction
        verification = await verify_transaction(
            tx_signature=payment_tx,
            expected_amount_usdc=price,
            expected_recipient=TREASURY_ADDRESS,
        )
        if not verification.get("valid"):
            raise HTTPException(400,
                f"Payment invalid: {verification.get('error', 'verification failed')}. "
                f"Expected {price} USDC to {TREASURY_ADDRESS[:12]}...")
    except HTTPException:
        raise
    except Exception as e:
        print(f"[PublicAPI] Payment verification error in /execute: {e}")
        raise HTTPException(400, "Payment verification failed. Ensure your USDC transfer to Treasury is confirmed on Solana.")

    payment_verified = True
    payment_info = {
        "verified": True,
        "signature": payment_tx,
        "amount_usdc": verification.get("amount_usdc", price),
        "from": verification.get("from", ""),
        "to": verification.get("to", TREASURY_ADDRESS),
    }

    print(f"[Marketplace] /execute payment verified: {payment_tx[:16]}... ({price} USDC from {verification.get('from', '?')[:12]}...)")

    # ═══ NATIVE SERVICE EXECUTION ═══
    if is_native:
        result_text = await _execute_native_service(service_id, prompt)
        volume = buyer.get("volume_30d", 0)
        commission_bps = get_commission_bps(volume)
        commission = price * commission_bps / 10000

        tx = {
            "tx_id": str(uuid.uuid4()), "buyer": buyer["name"],
            "seller": "MAXIA", "service": service_id,
            "price_usdc": price, "commission_usdc": commission,
            "seller_gets_usdc": price - commission, "timestamp": int(time.time()),
            "payment_tx": payment_tx, "payment_verified": True,
        }
        _transactions.append(tx)

        async with _agent_update_lock:
            buyer["volume_30d"] += price
            buyer["total_spent"] += price
            buyer["tier"] = _get_tier_name(buyer["volume_30d"])

        # Persist to DB
        await _save_tx_to_db(tx, buyer)
        try:
            await _exec_db.record_transaction(buyer["wallet"], payment_tx, price, "execute_native")
        except Exception:
            pass

        return {
            "success": True, "tx_id": tx["tx_id"],
            "service": service_id, "seller": "MAXIA",
            "price_usdc": price, "commission_usdc": commission,
            "result": result_text,
            "execution": "native",
            "payment_verified": True,
            "payment": payment_info,
        }

    # ═══ EXTERNAL AGENT SERVICE — compute commission + pay seller ═══
    volume = buyer.get("volume_30d", 0)
    commission_bps = get_commission_bps(volume)
    commission = price * commission_bps / 10000
    seller_gets = price - commission

    # Transfer seller's share on-chain
    seller_wallet = service.get("agent_wallet", "")
    if seller_wallet and seller_gets > 0.001:
        try:
            from solana_tx import send_usdc_transfer
            from config import ESCROW_PRIVKEY_B58, TREASURY_ADDRESS as TREASURY
            transfer = await send_usdc_transfer(
                to_address=seller_wallet,
                amount_usdc=seller_gets,
                from_privkey=ESCROW_PRIVKEY_B58,
                from_address=TREASURY,
            )
            if transfer.get("success"):
                print(f"[Marketplace] Seller paid: {seller_gets} USDC -> {seller_wallet[:8]}...")
                payment_info["seller_paid"] = True
                payment_info["seller_tx"] = transfer.get("signature", "")
            else:
                payment_info["seller_paid"] = False
                payment_info["seller_error"] = transfer.get("error", "")
        except Exception as e:
            print(f"[Marketplace] Seller payment error: {e}")
            payment_info["seller_paid"] = False
            payment_info["seller_error"] = "Seller payout pending — will retry"

    # Record transaction
    tx = {
        "tx_id": str(uuid.uuid4()), "buyer": buyer["name"],
        "seller": service["agent_name"], "service": service["name"],
        "price_usdc": price, "commission_usdc": commission,
        "seller_gets_usdc": seller_gets, "timestamp": int(time.time()),
        "payment_tx": payment_tx, "payment_verified": True,
    }
    _transactions.append(tx)

    # Update agent stats (with lock)
    async with _agent_update_lock:
        buyer["volume_30d"] += price
        buyer["total_spent"] += price
        buyer["tier"] = _get_tier_name(buyer["volume_30d"])
        service["sales"] += 1

        seller_key = service.get("agent_api_key")
        seller = _registered_agents.get(seller_key)
        if seller:
            seller["total_earned"] += seller_gets
            seller["volume_30d"] += seller_gets
            seller["tier"] = _get_tier_name(seller["volume_30d"])

    # Persist tx + seller stats to DB
    await _save_tx_to_db(tx, buyer, seller_key=seller_key)

    # Persist service sales count
    try:
        from database import db as _exec_db2
        await _exec_db2.update_service(service_id, {"sales": service.get("sales", 0)})
    except Exception:
        pass

    # Record in transactions table for idempotency
    try:
        from database import db as _exec_db3
        await _exec_db3.record_transaction(buyer["wallet"], payment_tx, price, "execute_marketplace")
    except Exception:
        pass

    # Execute via webhook if available (AFTER payment is verified and recorded)
    result_text = None
    execution_method = "pending"
    endpoint = service.get("endpoint", "")

    if endpoint and endpoint.startswith("http"):
        # SSRF protection — validate seller endpoint URL against private IPs
        try:
            from webhook_dispatcher import validate_callback_url
            validate_callback_url(endpoint)
        except Exception:
            execution_method = "webhook_blocked"
            result_text = "Seller endpoint blocked (private IP)"
            endpoint = ""  # prevent the webhook call below

        if endpoint:
            try:
                import httpx
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.post(endpoint, json={
                        "prompt": prompt,
                        "buyer": buyer["name"],
                        "service_id": service_id,
                        "tx_id": tx["tx_id"],
                        "payment_tx": payment_tx,
                        "payment_verified": True,
                        "amount_usdc": price,
                    })
                    if resp.status_code == 200:
                        result_data = resp.json()
                        result_text = result_data.get("result", result_data.get("text", str(result_data)))
                        execution_method = "webhook"
                    else:
                        result_text = f"Seller webhook returned {resp.status_code}"
                        execution_method = "webhook_error"
            except Exception as e:
                print(f"[PublicAPI] Webhook call error: {e}")
                result_text = "Webhook call failed — seller will be notified"
                execution_method = "webhook_error"
    else:
        execution_method = "manual"
        result_text = "Service purchased and payment verified. Seller will deliver manually (no webhook configured)."

    # Webhook failure handling — notify buyer but don't rollback (on-chain payment is final)
    if execution_method == "webhook_error":
        tx["webhook_failed"] = True
        payment_info["webhook_warning"] = "Webhook call failed. Payment is on-chain and cannot be reversed. Contact the seller for delivery."

    # Alert
    try:
        from alerts import alert_revenue
        await alert_revenue(commission, f"AI-to-AI: {buyer['name']} -> {service['agent_name']} (verified on-chain)")
    except Exception:
        pass

    # Notify webhook subscribers (seller + trade_executed event)
    try:
        from infra_features import notify_webhook_subscribers
        await notify_webhook_subscribers("trade_executed", {
            "tx_id": tx["tx_id"],
            "buyer": buyer["name"],
            "seller": service["agent_name"],
            "service": service["name"],
            "price_usdc": price,
            "payment_verified": True,
            "timestamp": int(time.time()),
        })
        # Notify the seller specifically
        if seller_wallet:
            await notify_webhook_subscribers("service_sold", {
                "tx_id": tx["tx_id"],
                "buyer": buyer["name"],
                "service": service["name"],
                "price_usdc": price,
                "your_earnings_usdc": seller_gets,
                "seller_wallet": seller_wallet,
                "payment_verified": True,
            }, filter_wallet=seller_wallet)
    except Exception as e:
        print(f"[Marketplace] Webhook notification error: {e}")

    # Telegram/Discord notification to seller
    try:
        from alerts import alert_system
        await alert_system(
            "New Sale (Verified)",
            f"**{buyer['name']}** bought **{service['name']}** for **${price:.2f} USDC**. "
            f"Seller earns ${seller_gets:.2f}. Payment verified on-chain. Tx: `{tx['tx_id'][:12]}...`"
        )
    except Exception:
        pass

    # Track conversion for ROI + attribute revenue to recent actions
    try:
        from ceo_maxia import ceo
        ceo.memory.log_action_with_tracking("MARKETPLACE", "sale", tx["tx_id"][:16], f"{service['name']} ${price}")
        roi = ceo.memory._data.get("roi_tracking", [])
        for entry in reversed(roi[-100:]):
            if entry.get("type") == "signup" and buyer["name"] in entry.get("details", ""):
                ceo.memory.record_conversion(entry["action_id"], revenue=commission)
                break
    except Exception:
        pass

    return {
        "success": True, "tx_id": tx["tx_id"],
        "service": service["name"], "seller": service["agent_name"],
        "price_usdc": price, "commission_usdc": commission,
        "seller_gets_usdc": seller_gets,
        "result": result_text,
        "execution": execution_method,
        "payment_verified": True,
        "payment": payment_info,
        "seller_wallet": service["agent_wallet"],
        "treasury_wallet": TREASURY_ADDRESS,
    }


async def _execute_native_service(service_id: str, prompt: str) -> str:
    """Execute a MAXIA native service via Groq."""
    if not groq_client:
        return "Service temporarily unavailable (no LLM)"

    system_prompts = {
        "maxia-audit": "You are a smart contract security auditor. Analyze the code for vulnerabilities. Be thorough and specific.",
        "maxia-code": "You are a senior software engineer. Write production-ready code. Include error handling and comments.",
        "maxia-data": "You are a DeFi data analyst. Provide detailed analytics with numbers and insights.",
        "maxia-translate": "You are a professional translator. Translate accurately while preserving meaning and tone.",
    }
    sys = system_prompts.get(service_id, "You are a helpful AI assistant.")

    try:
        def _call():
            resp = groq_client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "system", "content": sys}, {"role": "user", "content": prompt}],
                max_tokens=1500, temperature=0.7,
            )
            return resp.choices[0].message.content.strip()
        return await asyncio.to_thread(_call)
    except Exception as e:
        # Fix #21: Don't leak internal error details
        print(f"[PublicAPI] Native service execution error: {e}")
        return "Service execution temporarily unavailable"


async def _save_tx_to_db(tx: dict, buyer: dict = None, seller_key: str = None):
    """Persist transaction + update agent stats in SQLite."""
    try:
        from database import db
        await db.save_marketplace_tx(tx)
        if buyer:
            await db.update_agent(buyer.get("api_key", ""), {
                "volume_30d": buyer.get("volume_30d", 0),
                "total_spent": buyer.get("total_spent", 0),
                "tier": buyer.get("tier", "BRONZE"),
            })
        if seller_key:
            seller = _registered_agents.get(seller_key)
            if seller:
                await db.update_agent(seller_key, {
                    "total_earned": seller.get("total_earned", 0),
                    "volume_30d": seller.get("volume_30d", 0),
                    "tier": seller.get("tier", "BRONZE"),
                })
    except Exception as e:
        print(f"[PublicAPI] DB tx save error: {e}")


# ── Utilitaire ──

def _get_tier_name(volume: float) -> str:
    if volume >= 5000:
        return "WHALE"
    if volume >= 500:
        return "GOLD"
    return "BRONZE"


# ══════════════════════════════════════════
#  LOCATION GPU (prix coutant + commission)
# ══════════════════════════════════════════
#  DEFI — Yield Scanner (DeFiLlama)
# ══════════════════════════════════════════

@router.get("/defi/best-yield")
async def defi_best_yield(asset: str = "USDC", chain: str = "", min_tvl: float = 100000, limit: int = 10):
    """Find the best DeFi yields for an asset. Free, no auth.

    Examples:
      GET /defi/best-yield?asset=USDC
      GET /defi/best-yield?asset=ETH&chain=ethereum&limit=5
      GET /defi/best-yield?asset=SOL&chain=solana
    """
    try:
        from defi_scanner import get_best_yields
        yields = await get_best_yields(asset, chain, min_tvl, limit * 3)
        # Filtrer les APY aberrants (reward farming temporaire) et les pools a risque
        sane = [y for y in yields if 0 < y.get("apy", 0) < 1000 and y.get("tvl_usd", 0) >= 10000]
        return {
            "asset": asset,
            "chain": chain or "all",
            "results": len(sane[:limit]),
            "yields": sane[:limit],
            "source": "DeFiLlama",
        }
    except Exception as e:
        return {"error": str(e), "yields": []}


@router.get("/defi/protocol")
async def defi_protocol(name: str = "aave"):
    """Get stats for a specific DeFi protocol."""
    try:
        from defi_scanner import get_protocol_stats
        return await get_protocol_stats(name)
    except Exception as e:
        return {"error": str(e)}


@router.get("/defi/chains")
async def defi_chains():
    """Get TVL by blockchain."""
    try:
        from defi_scanner import get_chain_tvl
        return await get_chain_tvl()
    except Exception as e:
        return {"error": str(e)}


# ══════════════════════════════════════════
#  SENTIMENT ANALYSIS
# ══════════════════════════════════════════

@router.get("/sentiment")
async def public_sentiment(token: str = "BTC"):
    """Get crypto sentiment analysis. Free, no auth.
    
    Sources: CoinGecko community data, Reddit activity, LunarCrush (optional).
    Examples: /sentiment?token=BTC, /sentiment?token=SOL
    """
    try:
        from sentiment_analyzer import get_sentiment
        return await get_sentiment(token)
    except Exception as e:
        return {"error": str(e)}


@router.get("/trending")
async def public_trending():
    """Get trending crypto tokens."""
    try:
        from sentiment_analyzer import get_trending
        return {"trending": await get_trending()}
    except Exception as e:
        return {"error": str(e)}


@router.get("/fear-greed")
async def public_fear_greed():
    """Get crypto Fear & Greed Index."""
    try:
        from web3_services import get_fear_greed_index
        return await get_fear_greed_index()
    except Exception as e:
        return {"error": str(e)}


# ══════════════════════════════════════════
#  WEB3 AI SERVICES
# ══════════════════════════════════════════

@router.get("/token-risk")
async def public_token_risk(address: str = ""):
    """Analyze rug pull risk for a Solana token. Free, no auth.
    
    Returns risk score (0-100), warnings, recommendation.
    Example: /token-risk?address=TOKEN_MINT_ADDRESS
    """
    if not address:
        return {"error": "address parameter required"}
    try:
        from web3_services import analyze_token_risk
        return await analyze_token_risk(address)
    except Exception as e:
        return {"error": str(e)}


@router.get("/wallet-analysis")
async def public_wallet_analysis(address: str = ""):
    """Analyze a Solana wallet — holdings, activity, profile. Free, no auth."""
    if not address:
        return {"error": "address parameter required"}
    try:
        from web3_services import analyze_wallet
        return await analyze_wallet(address)
    except Exception as e:
        return {"error": str(e)}


# ══════════════════════════════════════════

@router.get("/gpu/tiers")
async def public_gpu_tiers():
    """Liste les GPU disponibles avec prix live, disponibilite et comparaison concurrents."""
    try:
        from runpod_client import get_gpu_tiers_live
        return await get_gpu_tiers_live()
    except Exception as e:
        # Fallback to static config
        from config import GPU_TIERS, BROKER_MARGIN
        tiers = []
        for gpu in GPU_TIERS:
            price = round(gpu["base_price_per_hour"] * BROKER_MARGIN, 4)
            tiers.append({
                "id": gpu["id"],
                "label": gpu["label"],
                "vram_gb": gpu["vram_gb"],
                "price_per_hour_usdc": price,
                "available": True,
                "source": "fallback",
                "maxia_markup": "0%",
            })
        return {
            "gpu_count": len(tiers),
            "tiers": tiers,
            "provider": "RunPod (via MAXIA)",
            "error": str(e),
        }


@router.get("/gpu/compare")
async def public_gpu_compare(gpu: str = "h100_sxm5"):
    """Compare GPU prices across providers. Shows MAXIA vs AWS vs GCP vs Lambda vs Vast.ai.

    Example: /gpu/compare?gpu=h100_sxm5
    """
    from runpod_client import COMPETITOR_PRICES, GPU_FULL_MAP
    info = GPU_FULL_MAP.get(gpu)
    prices = COMPETITOR_PRICES.get(gpu)
    if not info or not prices:
        available = list(COMPETITOR_PRICES.keys())
        return {"error": f"GPU '{gpu}' not found. Available: {available}"}

    maxia_price = prices.get("runpod_secure", prices.get("runpod_community", 0))
    comparison = []
    for provider, price in prices.items():
        if price and price > 0:
            savings = round((1 - maxia_price / price) * 100, 1) if price > maxia_price else 0
            more_expensive = round((maxia_price / price - 1) * 100, 1) if price < maxia_price else 0
            comparison.append({
                "provider": provider,
                "price_per_hour": price,
                "vs_maxia": f"{savings}% cheaper" if savings > 0 else f"{more_expensive}% more" if more_expensive > 0 else "same",
            })

    comparison.sort(key=lambda x: x["price_per_hour"])

    return {
        "gpu": gpu,
        "label": info["runpod_id"].replace("NVIDIA ", ""),
        "vram_gb": info["vram"],
        "maxia_price": maxia_price,
        "maxia_markup": "0%",
        "comparison": comparison,
        "note": "MAXIA charges 0% markup. Same price as RunPod but payable in USDC on Solana.",
    }


@router.post("/gpu/rent")
async def public_gpu_rent(req: dict, x_api_key: str = Header(None, alias="X-API-Key")):
    """Louer un GPU. L IA paie en USDC, MAXIA provisionne via RunPod."""
    if not x_api_key:
        raise HTTPException(401, "Header X-API-Key requis")

    agent = _get_agent(x_api_key)
    _check_rate(x_api_key)

    # Fix #7: String length limits
    tier_id = req.get("tier", "").strip()[:50] if isinstance(req.get("tier"), str) else ""
    payment_tx = req.get("payment_tx", "").strip()[:200] if isinstance(req.get("payment_tx"), str) else ""
    # Fix #4: Float validation with try/except
    try:
        hours = float(req.get("hours", 1))
    except (TypeError, ValueError):
        raise HTTPException(400, "hours must be a number")
    import math
    if math.isnan(hours) or math.isinf(hours):
        raise HTTPException(400, "hours must be a valid number")

    if hours <= 0 or hours > 720:
        raise HTTPException(400, "Duree entre 0.1 et 720 heures")

    # Trouver le GPU
    from config import GPU_TIERS, BROKER_MARGIN
    gpu = None
    for g in GPU_TIERS:
        if g["id"] == tier_id:
            gpu = g
            break
    if not gpu:
        raise HTTPException(404, f"GPU inconnu: {tier_id}. Disponibles: {[g['id'] for g in GPU_TIERS]}")

    # Calculer le prix
    price_per_hour = round(gpu["base_price_per_hour"] * BROKER_MARGIN, 4)
    total_price = round(price_per_hour * hours, 4)

    # Commission MAXIA
    volume = agent.get("volume_30d", 0)
    commission_bps = get_commission_bps(volume)
    commission = round(total_price * commission_bps / 10000, 4)
    total_with_commission = round(total_price + commission, 4)

    # Art.1 — Verifier que ce n est pas un usage interdit
    purpose = req.get("purpose", "")
    if purpose:
        _check_safety(purpose, "purpose")

    # Fix #1/#2/#11: Verify payment BEFORE provisioning + idempotency
    if not payment_tx:
        raise HTTPException(400, "payment_tx required for GPU rental")

    from database import db
    if await db.tx_already_processed(payment_tx):
        raise HTTPException(400, "Payment already used")

    # Verify USDC payment on-chain
    try:
        from solana_verifier import verify_transaction
        tx_result = await verify_transaction(
            tx_signature=payment_tx,
            expected_amount_usdc=total_with_commission,
            expected_recipient=TREASURY_ADDRESS,
        )
        if not tx_result.get("valid"):
            raise HTTPException(400, "Payment invalid: verification failed")
    except HTTPException:
        raise
    except Exception as e:
        # Fix #21: Don't leak internal error details
        print(f"[PublicAPI] GPU payment verification error: {e}")
        raise HTTPException(400, "Payment verification failed")

    # Provisionner le GPU via RunPod
    from runpod_client import RunPodClient
    from config import RUNPOD_API_KEY
    runpod = RunPodClient(api_key=RUNPOD_API_KEY)
    instance = await runpod.rent_gpu(tier_id, hours)

    if not instance.get("success"):
        # Fix #21: Don't leak internal RunPod error details
        print(f"[PublicAPI] RunPod provisioning error: {instance.get('error', 'indisponible')}")
        raise HTTPException(502, "GPU provisioning temporarily unavailable")

    # Enregistrer la transaction
    import uuid
    instance_id = instance.get("instanceId", str(uuid.uuid4()))
    tx = {
        "tx_id": str(uuid.uuid4()),
        "buyer": agent["name"],
        "buyer_wallet": agent["wallet"],
        "type": "gpu_rental",
        "gpu": gpu["label"],
        "tier_id": tier_id,
        "hours": hours,
        "price_per_hour": price_per_hour,
        "total_usdc": total_price,
        "commission_usdc": commission,
        "commission_bps": commission_bps,
        "total_with_commission": total_with_commission,
        "payment_tx": payment_tx,
        "instance_id": instance_id,
        "ssh_endpoint": instance.get("ssh_endpoint", ""),
        "timestamp": int(time.time()),
    }
    _transactions.append(tx)

    # Fix #3: Persist transaction to database
    await db.record_transaction(agent["wallet"], payment_tx, total_with_commission, "gpu_rental")

    # Fix #7: Persist GPU instance to database
    await db.save_gpu_instance({
        "instance_id": instance_id,
        "agent_wallet": agent["wallet"],
        "agent_name": agent["name"],
        "gpu_tier": tier_id,
        "duration_hours": hours,
        "price_per_hour": price_per_hour,
        "total_cost": total_price,
        "commission": commission,
        "payment_tx": payment_tx,
        "runpod_pod_id": instance_id,
        "status": instance.get("status", "provisioning"),
        "ssh_endpoint": instance.get("ssh_endpoint", ""),
        "scheduled_end": int(time.time() + hours * 3600),
    })

    # Mettre a jour les stats
    agent["volume_30d"] += total_with_commission
    agent["total_spent"] += total_with_commission
    agent["tier"] = _get_tier_name(agent["volume_30d"])

    # Fix #4: Persist agent stats to database
    await db.update_agent(agent["api_key"], {
        "volume_30d": agent["volume_30d"],
        "total_spent": agent["total_spent"],
        "tier": agent["tier"],
    })

    # Alerte Discord
    try:
        from alerts import alert_revenue
        await alert_revenue(commission, f"Location GPU {gpu['label']} — {agent['name']} ({hours}h)")
    except Exception:
        pass

    print(f"[PublicAPI] GPU loue: {gpu['label']} x{hours}h par {agent['name']} — {total_with_commission} USDC")

    return {
        "success": True,
        "tx_id": tx["tx_id"],
        "gpu": gpu["label"],
        "vram_gb": gpu["vram_gb"],
        "hours": hours,
        "price_per_hour_usdc": price_per_hour,
        "total_gpu_usdc": total_price,
        "commission_usdc": commission,
        "total_to_pay_usdc": total_with_commission,
        "your_tier": agent["tier"],
        "instance_id": instance.get("instanceId", ""),
        "ssh_endpoint": instance.get("sshEndpoint", ""),
        "status": "provisioning",
        "message": f"GPU {gpu['label']} en cours de provisionnement. Connectez-vous via SSH.",
    }


@router.get("/gpu/my-instances")
async def public_gpu_instances(x_api_key: str = Header(None, alias="X-API-Key")):
    """Liste les GPU en cours pour cet agent."""
    if not x_api_key:
        raise HTTPException(401, "Header X-API-Key requis")
    agent = _get_agent(x_api_key)

    my_gpus = [
        t for t in _transactions
        if t.get("type") == "gpu_rental" and t.get("buyer") == agent["name"]
    ]
    return {
        "instances": my_gpus[-10:],
        "total_spent_gpu": sum(t.get("total_with_commission", 0) for t in my_gpus),
    }


@router.get("/gpu/status/{pod_id}")
async def public_gpu_status(pod_id: str, x_api_key: str = Header(None, alias="X-API-Key")):
    """Statut d un pod GPU (utilisation, temps restant). Requiert API key."""
    if not x_api_key:
        raise HTTPException(401, "Header X-API-Key requis")
    _get_agent(x_api_key)

    from runpod_client import runpod_client
    return await runpod_client.get_pod_status(pod_id)


@router.post("/gpu/terminate/{pod_id}")
async def public_gpu_terminate(pod_id: str, x_api_key: str = Header(None, alias="X-API-Key")):
    """Arreter un pod GPU avant la fin. Requiert API key."""
    if not x_api_key:
        raise HTTPException(401, "Header X-API-Key requis")
    agent = _get_agent(x_api_key)

    # Verifier que le pod appartient a cet agent
    my_gpus = [t for t in _transactions if t.get("instance_id") == pod_id and t.get("buyer") == agent["name"]]
    if not my_gpus:
        raise HTTPException(403, "Ce pod ne vous appartient pas")

    from runpod_client import runpod_client
    result = await runpod_client.terminate_pod(pod_id)

    if result.get("success"):
        # Fix #6: Update DB with actual cost on termination
        try:
            from database import db
            await db.update_gpu_instance(pod_id, {
                "status": "terminated",
                "actual_end": int(time.time()),
                "actual_cost": result.get("actual_cost", 0),
            })
        except Exception:
            pass
        try:
            from alerts import alert_system
            await alert_system("GPU Termine", f"Pod {pod_id} arrete par {agent['name']}")
        except Exception:
            pass

    return result


@router.get("/gpu/compare-detailed")
async def gpu_price_compare():
    """Compare les prix MAXIA vs concurrence. Sans auth."""
    from config import GPU_TIERS, BROKER_MARGIN

    comparisons = []
    competitors = {
        "rtx4090": {"runpod": 0.69, "vast_ai": 0.34, "lambda": None},
        "a100_80": {"runpod": 1.79, "vast_ai": 0.75, "lambda": 1.29},
        "h100_sxm5": {"runpod": 3.29, "vast_ai": 1.49, "lambda": 2.49},
        "a6000": {"runpod": 0.80, "vast_ai": 0.40, "lambda": 0.80},
        "4xa100": {"runpod": 7.16, "vast_ai": 3.00, "lambda": 5.16},
    }

    for gpu in GPU_TIERS:
        maxia_price = round(gpu["base_price_per_hour"] * BROKER_MARGIN, 4)
        comp = competitors.get(gpu["id"], {})
        comparisons.append({
            "gpu": gpu["label"],
            "maxia_price": maxia_price,
            "maxia_advantage": "Prix coutant + commission dynamique (0.1% marketplace / 0.01% swap pour les gros volumes)",
            "runpod": comp.get("runpod"),
            "vast_ai": comp.get("vast_ai"),
            "lambda": comp.get("lambda"),
        })

    return {
        "comparisons": comparisons,
        "maxia_commission": "Marketplace: 1% Bronze → 0.5% Gold → 0.1% Whale | Swap: 0.10% → 0.01% (basee sur votre volume 30j)",
        "unique_advantages": [
            "Paiement USDC sur Solana (pas de carte bancaire)",
            "API unifiee (GPU + services IA + data)",
            "Commission la plus basse pour les gros volumes (0.1% marketplace / 0.01% swap)",
            "Protocole x402 natif (paiement automatique HTTP)",
            "Pas besoin de compte RunPod/AWS/GCP",
        ],
    }


# ══════════════════════════════════════════
#  ENCHERES GPU (via API publique)
# ══════════════════════════════════════════

@router.get("/gpu/auctions")
async def public_gpu_auctions():
    """Liste les encheres GPU en cours. Sans auth."""
    try:
        from auction_manager import AuctionManager
        # On ne peut pas acceder au singleton facilement, retourner un placeholder
        return {"message": "Connectez-vous en WebSocket sur /auctions pour les encheres temps reel", "endpoint": "wss://maxiaworld.app/auctions"}
    except Exception:
        return {"auctions": [], "message": "Aucune enchere en cours"}


@router.post("/gpu/auction/create")
async def public_create_auction(req: dict, x_api_key: str = Header(None, alias="X-API-Key")):
    """Creer une enchere GPU via l API publique."""
    if not x_api_key:
        raise HTTPException(401, "Header X-API-Key requis")

    agent = _get_agent(x_api_key)
    _check_rate(x_api_key)

    # Fix #7: String length limits + Fix #4: Float validation
    tier_id = req.get("tier", "").strip()[:50] if isinstance(req.get("tier"), str) else ""
    try:
        hours = float(req.get("hours", 1))
    except (TypeError, ValueError):
        raise HTTPException(400, "hours must be a number")
    hours = max(0.1, min(720, hours))

    from config import GPU_TIERS, BROKER_MARGIN
    gpu = None
    for g in GPU_TIERS:
        if g["id"] == tier_id:
            gpu = g
            break
    if not gpu:
        raise HTTPException(404, f"GPU inconnu: {tier_id}")

    cost = round(gpu["base_price_per_hour"] * hours * BROKER_MARGIN, 4)

    import uuid
    auction = {
        "auctionId": str(uuid.uuid4()),
        "gpuTierId": tier_id,
        "gpuLabel": gpu["label"],
        "vramGb": gpu["vram_gb"],
        "durationHours": hours,
        "startPrice": cost,
        "currentBid": cost,
        "currentLeader": None,
        "brokerWallet": agent["wallet"],
        "status": "open",
        "createdBy": agent["name"],
        "createdAt": int(time.time()),
    }

    return {
        "success": True,
        **auction,
        "message": f"Enchere ouverte pour {gpu['label']} x{hours}h. Prix de depart: {cost} USDC.",
        "bid_endpoint": "wss://maxiaworld.app/auctions",
    }


# ══════════════════════════════════════════
#  BOURSE D'ACTIONS TOKENISEES
# ══════════════════════════════════════════

@router.get("/stocks")
async def list_stocks():
    """Liste les actions tokenisees disponibles avec prix. Sans auth."""
    from tokenized_stocks import stock_exchange
    return await stock_exchange.list_stocks()


@router.get("/stocks/price/{symbol}")
async def stock_price(symbol: str):
    """Prix temps reel d une action. Sans auth."""
    from tokenized_stocks import stock_exchange
    return await stock_exchange.get_price(symbol)


@router.post("/stocks/buy")
async def buy_stock(req: dict, x_api_key: str = Header(None, alias="X-API-Key")):
    """Acheter des actions tokenisees. Paie en USDC."""
    if not x_api_key:
        raise HTTPException(401, "Header X-API-Key requis")
    agent = _get_agent(x_api_key)
    _check_rate(x_api_key)

    # Fix #7: String length limits + Fix #4: Float validation
    symbol = req.get("symbol", "").strip()[:20] if isinstance(req.get("symbol"), str) else ""
    payment_tx = req.get("payment_tx", "").strip()[:200] if isinstance(req.get("payment_tx"), str) else ""
    try:
        amount_usdc = float(req.get("amount_usdc", 0))
    except (TypeError, ValueError):
        raise HTTPException(400, "amount_usdc must be a number")
    import math
    if math.isnan(amount_usdc) or math.isinf(amount_usdc) or amount_usdc <= 0:
        raise HTTPException(400, "amount_usdc must be a positive number")

    from tokenized_stocks import stock_exchange
    result = await stock_exchange.buy_stock(
        buyer_api_key=x_api_key,
        buyer_name=agent["name"],
        buyer_wallet=agent["wallet"],
        symbol=symbol,
        amount_usdc=amount_usdc,
        buyer_volume_30d=agent.get("volume_30d", 0),
        payment_tx=payment_tx,
    )

    if result.get("success"):
        agent["volume_30d"] += amount_usdc
        agent["total_spent"] += amount_usdc
        agent["tier"] = _get_tier_name(agent["volume_30d"])

    return result


@router.post("/stocks/sell")
async def sell_stock(req: dict, x_api_key: str = Header(None, alias="X-API-Key")):
    """Vendre des actions tokenisees. Recoit USDC."""
    if not x_api_key:
        raise HTTPException(401, "Header X-API-Key requis")
    agent = _get_agent(x_api_key)
    _check_rate(x_api_key)

    # Fix #7: String length limits + Fix #4: Float validation
    symbol = req.get("symbol", "").strip()[:20] if isinstance(req.get("symbol"), str) else ""
    try:
        shares = float(req.get("shares", 0))
    except (TypeError, ValueError):
        raise HTTPException(400, "shares must be a number")
    import math
    if math.isnan(shares) or math.isinf(shares) or shares <= 0:
        raise HTTPException(400, "shares must be a positive number")

    from tokenized_stocks import stock_exchange
    result = await stock_exchange.sell_stock(
        seller_api_key=x_api_key,
        seller_name=agent["name"],
        seller_wallet=agent["wallet"],
        symbol=symbol,
        shares=shares,
        seller_volume_30d=agent.get("volume_30d", 0),
    )

    if result.get("success"):
        agent["volume_30d"] += result.get("gross_usdc", 0)

    return result


@router.get("/stocks/portfolio")
async def stock_portfolio(x_api_key: str = Header(None, alias="X-API-Key")):
    """Mon portefeuille d actions tokenisees."""
    if not x_api_key:
        raise HTTPException(401, "Header X-API-Key requis")
    agent = _get_agent(x_api_key)

    from tokenized_stocks import stock_exchange
    return await stock_exchange.get_portfolio(x_api_key)


@router.get("/stocks/compare-fees")
async def stock_compare_fees():
    """Compare les frais MAXIA vs concurrence. Sans auth."""
    from tokenized_stocks import stock_exchange
    return stock_exchange.compare_fees()


@router.get("/stocks/fees")
async def stock_fees():
    """Alias de /stocks/compare-fees."""
    from tokenized_stocks import stock_exchange
    return stock_exchange.compare_fees()


@router.get("/stocks/stats")
async def stock_stats():
    """Statistiques de la bourse. Sans auth."""
    from tokenized_stocks import stock_exchange
    return stock_exchange.get_stats()


@router.get("/stats")
async def public_stats():
    """Stats publiques de la marketplace."""
    from database import db
    try:
        stats = await db.get_marketplace_stats()
        db_stats = await db.get_stats()
        return {**stats, "volume_24h": db_stats.get("volume_24h", 0), "listing_count": db_stats.get("listing_count", 0)}
    except Exception:
        return {"agents_registered": 0, "services_listed": 0, "total_transactions": 0, "volume_24h": 0}


# ══════════════════════════════════════════
#  CRYPTO SWAP
# ══════════════════════════════════════════

@router.get("/crypto/tokens")
async def list_crypto_tokens():
    """Liste les cryptos disponibles pour le swap. Sans auth."""
    from crypto_swap import list_tokens
    return list_tokens()


@router.get("/crypto/prices")
async def crypto_prices():
    """Prix live des cryptos. Sans auth."""
    from crypto_swap import fetch_prices, _price_cache_ts
    prices = await fetch_prices()
    # Fix #10: Return the actual cache timestamp, not current time
    return {"prices": prices, "updated_at": int(_price_cache_ts or time.time()), "cache_ttl_seconds": 30}


@router.get("/crypto/quote")
async def crypto_quote(request: Request, from_token: str, to_token: str, amount: float, volume_30d: float = 0):
    """Devis de swap avec commission MAXIA. Sans auth."""
    from crypto_swap import get_swap_quote
    # Fix #6: Wire up user_volume_30d from agent's actual volume if API key provided
    volume = volume_30d
    api_key = request.headers.get("X-API-Key", "")
    if api_key:
        agent = _registered_agents.get(api_key)
        if agent:
            volume = agent.get("volume_30d", 0)
    result = await get_swap_quote(from_token, to_token, amount, user_volume_30d=volume)
    # Fix #7: Add cache-control info to quote response
    if isinstance(result, dict) and "error" not in result:
        result["cache_ttl_seconds"] = 30
        result["note"] = "Quote valid for 30 seconds"
    return result


@router.get("/crypto/swap-quote")
async def crypto_swap_quote(request: Request, from_token: str, to_token: str, amount: float, volume_30d: float = 0):
    """Alias de /crypto/quote pour compatibilite."""
    return await crypto_quote(request, from_token, to_token, amount, volume_30d)


@router.post("/crypto/swap")
async def crypto_swap(request: Request, req: dict, x_api_key: str = Header(None, alias="X-API-Key")):
    """Executer un swap crypto. Requiert API key."""
    if not x_api_key:
        raise HTTPException(401, "Header X-API-Key requis")
    client_ip = request.client.host if request.client else ""
    agent = _get_agent(x_api_key, client_ip=client_ip)
    _check_rate(x_api_key)

    # #24: NaN/Infinity validation
    import math
    try:
        amount = float(req.get("amount", 0))
    except (TypeError, ValueError):
        return {"success": False, "error": "Invalid amount"}
    if math.isnan(amount) or math.isinf(amount):
        return {"success": False, "error": "Invalid amount"}

    # Fix #1: Volume-based rate limiting for swaps ($50,000/day)
    from crypto_swap import fetch_prices, SUPPORTED_TOKENS
    from_token_upper = req.get("from_token", "").upper()
    prices = await fetch_prices()
    from_price = prices.get(from_token_upper, {}).get("price", 0)
    value_usd = amount * from_price if from_price > 0 else amount
    today_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    daily_vol_key = f"vol:{x_api_key}:{today_utc}"
    current_vol = _rate_limits.get(daily_vol_key, 0)
    if current_vol + value_usd > 50000:
        return {"success": False, "error": "Daily volume limit reached ($50,000)"}

    from crypto_swap import execute_swap
    result = await execute_swap(
        buyer_api_key=x_api_key,
        buyer_name=agent["name"],
        buyer_wallet=agent["wallet"],
        from_token=req.get("from_token", ""),
        to_token=req.get("to_token", ""),
        amount=amount,
        buyer_volume_30d=agent.get("volume_30d", 0),
        payment_tx=req.get("payment_tx", ""),
    )

    if result.get("success"):
        # Fix #1: Track daily volume after successful swap
        _rate_limits[daily_vol_key] = current_vol + value_usd
        async with _agent_update_lock:
            # Fix #3: Division by zero guard + Fix #19: Use dynamic commission calculation
            bps = result.get("commission_bps") or get_commission_bps(agent.get("volume_30d", 0))
            bps = bps or 15  # Absolute fallback to prevent zero
            agent["volume_30d"] += result.get("commission_usd", 0) / (bps / 10000)
            agent["total_spent"] += result.get("commission_usd", 0)

    return result


@router.get("/crypto/compare-fees")
async def crypto_compare_fees(volume_30d: float = 0):
    """Compare les frais MAXIA vs concurrence. Sans auth."""
    from crypto_swap import compare_fees
    return compare_fees(volume_30d)


@router.get("/crypto/stats")
async def crypto_stats():
    """Stats des swaps. Sans auth."""
    from crypto_swap import get_swap_stats
    return get_swap_stats()


# ══════════════════════════════════════════
#  WEB SCRAPER (Art.25)
# ══════════════════════════════════════════

@router.post("/scrape")
async def scrape_web(req: dict, x_api_key: str = Header(None, alias="X-API-Key")):
    """Scrape une URL et retourne le contenu structure. Requiert API key."""
    if not x_api_key:
        raise HTTPException(401, "Header X-API-Key requis")
    _get_agent(x_api_key)
    _check_rate(x_api_key)

    url = req.get("url", "")
    if not url:
        raise HTTPException(400, "Champ 'url' requis")

    # Fix #1: Clamp max_text_length bounds
    try:
        max_text_length = max(100, min(50000, int(req.get("max_text_length", 10000))))
    except (TypeError, ValueError):
        max_text_length = 10000

    # Fix #7: String length limit on URL
    url = url.strip()[:2000]

    from web_scraper import scrape_url
    return await scrape_url(
        url=url,
        extract_links=req.get("extract_links", True),
        extract_images=req.get("extract_images", True),
        max_text_length=max_text_length,
    )


@router.post("/scrape/batch")
async def scrape_batch(req: dict, x_api_key: str = Header(None, alias="X-API-Key")):
    """Scrape plusieurs URLs (max 5). Requiert API key."""
    if not x_api_key:
        raise HTTPException(401, "Header X-API-Key requis")
    _get_agent(x_api_key)
    _check_rate(x_api_key)

    urls = req.get("urls", [])
    if not urls:
        raise HTTPException(400, "Champ 'urls' requis (liste)")

    # Fix #1: Clamp max_text_length bounds
    try:
        max_text_length = max(100, min(50000, int(req.get("max_text_length", 5000))))
    except (TypeError, ValueError):
        max_text_length = 5000

    from web_scraper import scrape_multiple
    return await scrape_multiple(urls[:5], max_text_length=max_text_length)


# ══════════════════════════════════════════
#  IMAGE GENERATION (Art.26)
# ══════════════════════════════════════════

@router.post("/image/generate")
async def generate_image(req: dict, x_api_key: str = Header(None, alias="X-API-Key")):
    """Genere une image a partir d un prompt. Requiert API key."""
    if not x_api_key:
        raise HTTPException(401, "Header X-API-Key requis")
    agent = _get_agent(x_api_key)
    _check_rate(x_api_key)

    # Fix #7: String length limit on prompt
    prompt = req.get("prompt", "").strip()[:5000] if isinstance(req.get("prompt"), str) else ""
    if not prompt:
        raise HTTPException(400, "Champ 'prompt' requis")
    _check_safety(prompt, "prompt")

    # Fix #1: Int validation bounds for image parameters
    try:
        width = max(64, min(2048, int(req.get("width", 1024))))
        height = max(64, min(2048, int(req.get("height", 1024))))
        steps = max(1, min(50, int(req.get("steps", 4))))
        seed = max(0, min(999999999, int(req.get("seed", 0))))
    except (TypeError, ValueError):
        raise HTTPException(400, "Invalid image parameters (width, height, steps, seed must be integers)")

    from image_gen import generate_image as gen_img
    result = await gen_img(
        prompt=prompt,
        model=req.get("model", "flux-schnell"),
        width=width,
        height=height,
        steps=steps,
        seed=seed,
    )

    if result.get("success"):
        agent["total_spent"] += 0.10  # $0.10 par image

    return result


@router.get("/image/models")
async def image_models():
    """Liste les modeles de generation d images. Sans auth."""
    from image_gen import list_models
    return list_models()


# ══════════════════════════════════════════
#  WALLET MONITOR (Art.27)
# ══════════════════════════════════════════

@router.post("/wallet-monitor/add")
async def add_wallet_monitor(req: dict, x_api_key: str = Header(None, alias="X-API-Key")):
    """Ajouter un wallet a surveiller. Requiert API key."""
    if not x_api_key:
        raise HTTPException(401, "Header X-API-Key requis")
    agent = _get_agent(x_api_key)

    # Fix #7: String length limit + Fix #13: Validate Solana address
    wallet = req.get("wallet", "").strip()[:50] if isinstance(req.get("wallet"), str) else ""
    if not wallet:
        raise HTTPException(400, "Champ 'wallet' requis")
    _validate_solana_address(wallet, "wallet")

    # Fix #4: Float validation + Fix #7: String length limit on webhook_url
    try:
        min_sol_change = float(req.get("min_sol_change", 0.1))
    except (TypeError, ValueError):
        min_sol_change = 0.1
    webhook_url = req.get("webhook_url", "").strip()[:500] if isinstance(req.get("webhook_url"), str) else ""

    from wallet_monitor import add_monitor
    return await add_monitor(
        api_key=x_api_key,
        owner_name=agent["name"],
        wallet_address=wallet,
        webhook_url=webhook_url,
        alert_types=req.get("alert_types", None),
        min_sol_change=min_sol_change,
    )


@router.post("/wallet-monitor/remove")
async def remove_wallet_monitor(req: dict, x_api_key: str = Header(None, alias="X-API-Key")):
    """Arreter la surveillance d un wallet. Requiert API key."""
    if not x_api_key:
        raise HTTPException(401, "Header X-API-Key requis")

    monitor_id = req.get("monitor_id", "")
    if not monitor_id:
        raise HTTPException(400, "Champ 'monitor_id' requis")

    from wallet_monitor import remove_monitor
    return await remove_monitor(x_api_key, monitor_id)


@router.get("/wallet-monitor/my-monitors")
async def my_wallet_monitors(x_api_key: str = Header(None, alias="X-API-Key")):
    """Liste mes moniteurs actifs. Requiert API key."""
    if not x_api_key:
        raise HTTPException(401, "Header X-API-Key requis")
    _get_agent(x_api_key)

    from wallet_monitor import get_my_monitors
    return get_my_monitors(x_api_key)


@router.get("/wallet-monitor/alerts")
async def my_wallet_alerts(x_api_key: str = Header(None, alias="X-API-Key"), limit: int = 50):
    """Recupere mes alertes. Requiert API key."""
    if not x_api_key:
        raise HTTPException(401, "Header X-API-Key requis")
    _get_agent(x_api_key)

    from wallet_monitor import get_alerts
    return get_alerts(x_api_key, limit)


# ══════════════════════════════════════════
# REFERRAL PROGRAM — 10% commission share
# ══════════════════════════════════════════

REFERRAL_SHARE_PCT = 10  # referrer gets 10% of referee's commissions

@router.get("/referral/my-code")
async def referral_my_code(x_api_key: str = Header(None, alias="X-API-Key")):
    """Get your referral code. Share it to earn 10% of referred agents' commissions."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    agent = _get_agent(x_api_key)
    code = agent["wallet"][:8].upper() + "MAXIA"
    from database import db
    rows = await db.raw_execute_fetchall(
        "SELECT COUNT(*) AS cnt FROM referrals WHERE referrer=?", (code,))
    count = rows[0]["cnt"] if rows else 0
    # Calculate earnings
    earnings = 0.0
    try:
        rows2 = await db.raw_execute_fetchall(
            "SELECT data FROM referrals WHERE referrer=?", (code,))
        for r in rows2:
            d = json.loads(r["data"])
            earnings += d.get("earnedUsdc", 0)
    except Exception:
        pass
    return {
        "referral_code": code,
        "share_url": f"https://maxiaworld.app/api/public/register?referral_code={code}",
        "referrals": count,
        "earnings_usdc": round(earnings, 4),
        "commission": f"{REFERRAL_SHARE_PCT}% of referred agents' commissions",
        "how_it_works": [
            "1. Share your referral code or link",
            "2. New agent registers with your code",
            "3. You earn 10% of every commission they generate",
            "4. Passive income forever",
        ],
    }


@router.get("/referral/my-referrals")
async def referral_list(x_api_key: str = Header(None, alias="X-API-Key")):
    """List agents I referred."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    agent = _get_agent(x_api_key)
    code = agent["wallet"][:8].upper() + "MAXIA"
    from database import db
    rows = await db.raw_execute_fetchall(
        "SELECT data FROM referrals WHERE referrer=? ORDER BY rowid DESC", (code,))
    referrals = []
    total_earned = 0.0
    for r in rows:
        d = json.loads(r["data"])
        earned = d.get("earnedUsdc", 0)
        total_earned += earned
        referrals.append({
            "referee": d.get("referee", "")[:8] + "...",
            "registered_at": d.get("registeredAt", 0),
            "earned_usdc": round(earned, 4),
        })
    return {"referrals": referrals, "total": len(referrals),
            "total_earned_usdc": round(total_earned, 4)}
