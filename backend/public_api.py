"""MAXIA Art.22 V11 — API Publique pour Agents IA (Buy/Sell Services)

Permet aux IA externes de :
- Decouvrir les services MAXIA (sans auth)
- S'inscrire gratuitement (recevoir une API key)
- Acheter des services et payer en USDC
- Vendre leurs propres services
- MAXIA prend sa commission automatiquement

Securite Art.1 : filtrage anti-abus sur TOUS les contenus
"""
import logging
import uuid, time, hashlib, secrets, asyncio, json, datetime, re

logger = logging.getLogger(__name__)
from error_utils import safe_error
from fastapi import APIRouter, HTTPException, Header, Request
from config import (
    TREASURY_ADDRESS, GROQ_API_KEY, GROQ_MODEL,
    get_commission_bps, get_commission_tier_name, BLOCKED_WORDS, BLOCKED_PATTERNS,
)
from security import check_content_safety, check_ofac_wallet, require_ofac_clear, check_rate_limit_tiered, check_rate_limit

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

# ── Clone detection: track content hashes to detect duplicate services ──
_service_content_hashes: dict = {}  # content_hash -> {"original_id": str, "original_agent": str, "listed_at": int}


def _compute_service_hash(name: str, description: str, endpoint: str) -> str:
    """Hash the core content of a service to detect clones."""
    # Normalize: lowercase, strip whitespace, remove punctuation
    normalized = f"{name.lower().strip()}|{description.lower().strip()[:500]}|{endpoint.lower().strip()}"
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def _check_clone(name: str, description: str, endpoint: str, agent_api_key: str) -> dict:
    """Check if a service is a clone of an existing one. Returns clone info or None."""
    content_hash = _compute_service_hash(name, description, endpoint)
    if content_hash in _service_content_hashes:
        original = _service_content_hashes[content_hash]
        # Same agent re-listing = allowed (update)
        if original.get("original_agent_key") == agent_api_key:
            return {}
        return {
            "is_clone": True,
            "original_service_id": original["original_id"],
            "original_agent": original["original_agent"],
            "similarity": "exact_match",
        }
    return {}


def _register_service_hash(service_id: str, agent_name: str, agent_key: str,
                           name: str, description: str, endpoint: str):
    """Register a service content hash for clone detection."""
    content_hash = _compute_service_hash(name, description, endpoint)
    if content_hash not in _service_content_hashes:
        _service_content_hashes[content_hash] = {
            "original_id": service_id,
            "original_agent": agent_name,
            "original_agent_key": agent_key,
            "listed_at": int(time.time()),
        }


def _is_original_creator(service_id: str) -> bool:
    """Check if a service is the original (not a clone)."""
    for info in _service_content_hashes.values():
        if info["original_id"] == service_id:
            return True
    return False


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
            logger.info("Loaded from DB: %s agents, %s services", len(agents), len(services))
    except Exception as e:
        logger.error("DB load error: %s", e)

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
    """Liste tous les services disponibles. Native MAXIA + agents externes."""
    await _load_from_db()

    native_services = []
    external_services = []

    for s in _agent_services:
        if s.get("status") != "active":
            continue
        is_native = s.get("agent_api_key") == "maxia_native" or s.get("agent_name") == "MAXIA"
        entry = {
            "id": s["id"],
            "name": s["name"],
            "type": s.get("type", "text"),
            "description": s.get("description", ""),
            "price_usdc": s.get("price_usdc", 0),
            "provider": s.get("agent_name", "MAXIA"),
            "seller": s.get("agent_name", "MAXIA"),
            "rating": s.get("rating", 5.0),
            "sales": s.get("sales", 0),
            "source": "maxia_native" if is_native else "external_agent",
        }
        if is_native:
            native_services.append(entry)
        else:
            external_services.append(entry)

    all_services = external_services + native_services

    return {
        "total": len(all_services),
        "external_agents": len(external_services),
        "native_services": len(native_services),
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
    """Tous les prix MAXIA en temps reel — GPU, services, commissions. Mis a jour live."""
    import time as _t
    from config import GPU_TIERS, SERVICE_PRICES, COMMISSION_TIERS
    try:
        from crypto_swap import SWAP_COMMISSION_TIERS
    except ImportError:
        SWAP_COMMISSION_TIERS = {}
    try:
        from tokenized_stocks import STOCK_COMMISSION_TIERS
    except (ImportError, AttributeError):
        STOCK_COMMISSION_TIERS = {}
    return {
        "gpu_tiers": GPU_TIERS,
        "service_prices": SERVICE_PRICES,
        "marketplace_commission_tiers": COMMISSION_TIERS,
        "swap_commission_tiers": SWAP_COMMISSION_TIERS,
        "stock_commission_tiers": STOCK_COMMISSION_TIERS,
        "currency": "USDC",
        "updated_at": _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime()),
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
async def register_agent(req: dict, request: Request):
    """Inscription gratuite pour les IA. Retourne une API key. Persiste dans SQLite."""
    # Rate limit registration to prevent abuse (IP-based)
    check_rate_limit(request)
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

    # OFAC sanctions check on registration
    require_ofac_clear(wallet, "registration wallet")

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
        logger.error("DB save agent error: %s", e)

    # Gamification — points for registration
    try:
        from gamification import record_action
        await record_action(agent.get("wallet", api_key), "agent_registered")
    except Exception:
        pass

    # Referral tracking — referral code = first 8 chars of api_key (after "maxia_" prefix)
    referral_code = req.get("referral_code", "") if isinstance(req.get("referral_code"), str) else ""
    # Fix #5: Referral code validation
    if referral_code and (len(referral_code) > 50 or not referral_code.isalnum()):
        referral_code = ""  # Silently ignore invalid
    referrer_api_key = ""
    if referral_code:
        try:
            from database import db as _db
            # Find the referrer agent by matching referral code to api_key prefix
            all_agents = await _db.get_all_agents()
            for a in all_agents:
                if a["api_key"][6:14] == referral_code:
                    referrer_api_key = a["api_key"]
                    break
            if referrer_api_key:
                # Store referred_by in agents table
                await _db.raw_execute(
                    "UPDATE agents SET referred_by=? WHERE api_key=?",
                    (referrer_api_key, api_key))
                # Also store in referrals table for tracking
                await _db.raw_execute(
                    "INSERT OR IGNORE INTO referrals(ref_id,referrer,referee,data) VALUES(?,?,?,?)",
                    (str(uuid.uuid4()), referrer_api_key, api_key,
                     json.dumps({"referralId": str(uuid.uuid4()), "referrer": referrer_api_key,
                                 "referrer_code": referral_code, "referee_api_key": api_key,
                                 "referee_wallet": wallet, "referee_name": name,
                                 "registeredAt": int(time.time()), "earnedUsdc": 0})))
                logger.info("Referral: %s referred by %s (agent %s...)", name, referral_code, referrer_api_key[:14])
            else:
                logger.info("Referral code %s not found — ignored", referral_code)
        except Exception as e:
            logger.error("Referral error: %s", e)

    # Alerte Discord
    try:
        from alerts import alert_new_client
        await alert_new_client(wallet, f"Agent IA: {name}" + (f" (ref: {referral_code})" if referral_code else ""), 0)
    except Exception:
        pass

    logger.info("Nouvel agent inscrit: %s (%s...)", name, wallet[:8])

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
                logger.info("Signup %s attributed to %s action %s", name, entry["type"], entry["action_id"])
                break
    except Exception:
        pass

    sandbox_note = " (SANDBOX MODE — fake USDC)" if SANDBOX_MODE else ""

    # Generate referral code for this agent
    my_referral_code = api_key[6:14]

    # Generate DID + UAID for this agent
    agent_identity = {}
    try:
        from agent_permissions import get_or_create_permissions
        perms = await get_or_create_permissions(api_key, wallet)
        agent_identity = {
            "agent_id": perms.get("agent_id", ""),
            "did": perms.get("did", ""),
            "uaid": perms.get("uaid", ""),
            "public_key": perms.get("public_key", ""),
            "signing_key": perms.get("_private_key_once", ""),  # ONE TIME ONLY — save this!
            "trust_level": perms.get("trust_level", 0),
            "did_document_url": f"https://maxiaworld.app/agent/{perms.get('agent_id', '')}/did.json",
            "verification_url": f"https://maxiaworld.app/api/public/agent/{perms.get('uaid', '')}",
        }
    except Exception:
        pass

    return {
        "success": True,
        "api_key": api_key,
        **agent_identity,
        "referral_code": my_referral_code,
        "referral_url": f"https://maxiaworld.app/api/public/register?referral_code={my_referral_code}",
        "sandbox": SANDBOX_MODE,
        "name": name,
        "tier": "BRONZE",
        "rate_limit": f"{RATE_LIMIT_FREE} requetes/jour",
        "message": "Bienvenue sur MAXIA. Utilisez X-API-Key dans vos headers pour acceder aux services.",
        "referred_by": referral_code if referrer_api_key else None,
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
                "9. Share your referral code to earn 50% of commissions: " + my_referral_code,
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
#  SANDBOX — free testing, always available
# ══════════════════════════════════════════

# Sandbox balances per agent (fake USDC for testing)
_sandbox_balances: dict = {}  # api_key -> float
_sandbox_trades: list = []
_sandbox_portfolios: dict = {}  # api_key -> {symbol: shares}
_sandbox_locks: dict = {}  # api_key -> asyncio.Lock (prevents TOCTOU race conditions)
SANDBOX_STARTING_BALANCE = 10000.0  # $10,000 fake USDC


def _get_sandbox_lock(api_key: str) -> "asyncio.Lock":
    """Get or create an asyncio lock for a sandbox user to prevent race conditions."""
    if api_key not in _sandbox_locks:
        import asyncio
        _sandbox_locks[api_key] = asyncio.Lock()
    return _sandbox_locks[api_key]


def _get_sandbox_balance(api_key: str) -> float:
    if api_key not in _sandbox_balances:
        _sandbox_balances[api_key] = SANDBOX_STARTING_BALANCE
    return _sandbox_balances[api_key]


@router.get("/sandbox/status")
async def sandbox_status():
    """Sandbox is always available for free. No real USDC needed."""
    return {
        "sandbox_enabled": True,
        "starting_balance_usdc": SANDBOX_STARTING_BALANCE,
        "note": "Sandbox is always free. Use /sandbox/* endpoints to test without real USDC.",
        "endpoints": [
            "GET  /sandbox/status — this endpoint",
            "GET  /sandbox/balance — your sandbox USDC balance",
            "POST /sandbox/execute — test a service (free)",
            "POST /sandbox/swap — test a token swap (free)",
            "POST /sandbox/buy-stock — test stock purchase (free)",
            "POST /sandbox/reset — reset your sandbox balance to $10,000",
        ],
    }


@router.get("/sandbox/balance")
async def sandbox_balance(x_api_key: str = Header(None, alias="X-API-Key")):
    """Check sandbox USDC balance."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    await _load_from_db()
    _get_agent(x_api_key)
    balance = _get_sandbox_balance(x_api_key)
    portfolio = _sandbox_portfolios.get(x_api_key, {})
    return {
        "sandbox": True,
        "balance_usdc": round(balance, 4),
        "portfolio": portfolio,
        "trades_count": sum(1 for t in _sandbox_trades if t.get("api_key") == x_api_key),
    }


@router.post("/sandbox/reset")
async def sandbox_reset(x_api_key: str = Header(None, alias="X-API-Key")):
    """Reset sandbox balance to $10,000."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    await _load_from_db()
    _get_agent(x_api_key)
    _sandbox_balances[x_api_key] = SANDBOX_STARTING_BALANCE
    _sandbox_portfolios.pop(x_api_key, None)
    return {"sandbox": True, "balance_usdc": SANDBOX_STARTING_BALANCE, "message": "Sandbox reset to $10,000"}


@router.post("/sandbox/execute")
async def sandbox_execute(req: dict, x_api_key: str = Header(None, alias="X-API-Key")):
    """Execute a service in sandbox mode — same prices and fees as production."""
    await _load_from_db()
    if not x_api_key:
        raise HTTPException(401, "Header X-API-Key requis")
    buyer = _get_agent(x_api_key)
    service_id = req.get("service_id", "").strip()[:100] if isinstance(req.get("service_id"), str) else ""
    prompt = req.get("prompt", "").strip()[:50000] if isinstance(req.get("prompt"), str) else ""
    if not prompt:
        raise HTTPException(400, "prompt required")
    _check_safety(prompt, "prompt")

    # Real service prices (same as production)
    _native_prices = {
        "maxia-audit": {"price": 4.99, "name": "Smart Contract Audit"},
        "maxia-code": {"price": 2.99, "name": "AI Code Review"},
        "maxia-translate": {"price": 0.05, "name": "AI Translation"},
        "maxia-summary": {"price": 0.49, "name": "Document Summary"},
        "maxia-wallet": {"price": 1.99, "name": "Wallet Analyzer"},
        "maxia-marketing": {"price": 0.99, "name": "Marketing Copy Generator"},
        "maxia-image": {"price": 0.10, "name": "AI Image Generator"},
        "maxia-scraper": {"price": 0.02, "name": "Web Scraper"},
    }

    # Try native service first, then external services
    price = 0.0
    service_name = ""
    if service_id in _native_prices:
        price = _native_prices[service_id]["price"]
        service_name = _native_prices[service_id]["name"]
    else:
        for s in _agent_services:
            if s.get("id") == service_id:
                price = s.get("price_usdc", 0)
                service_name = s.get("name", "")
                break
    if not price:
        price = 2.99  # default = code review price
        service_name = "AI Code Review (default)"

    # Real marketplace commission (based on transaction amount)
    commission_bps = get_commission_bps(price)
    tier = get_commission_tier_name(price)
    commission = round(price * commission_bps / 10000, 4)
    seller_gets = round(price - commission, 4)

    async with _get_sandbox_lock(x_api_key):
        balance = _get_sandbox_balance(x_api_key)
        if balance < price:
            return {
                "sandbox": True, "tx_id": None,
                "message": f"Insufficient balance: ${balance:,.2f} < ${price:,.2f}. Reset your sandbox.",
                "balance_usdc": balance, "cost_usdc": price,
            }

        _sandbox_balances[x_api_key] = balance - price
    tx_id = f"sandbox_{uuid.uuid4()}"
    _sandbox_trades.append({
        "api_key": x_api_key, "tx_id": tx_id, "type": "execute",
        "service": service_id, "service_name": service_name,
        "price_usdc": price, "commission": commission, "ts": int(time.time()),
    })

    return {
        "success": True, "sandbox": True, "tx_id": tx_id,
        "service": service_id, "service_name": service_name,
        "price_usdc": price,
        "commission_usdc": commission, "seller_gets_usdc": seller_gets,
        "tier": tier, "commission_pct": f"{commission_bps/100}%",
        "balance_after": round(_sandbox_balances[x_api_key], 4),
        "result": await _execute_native_service(service_id, prompt) if service_id.startswith("maxia-") else f"[SANDBOX] {service_name} — executed. Prompt: {prompt[:100]}",
    }


@router.post("/sandbox/swap")
async def sandbox_swap(req: dict, x_api_key: str = Header(None, alias="X-API-Key")):
    """Test a token swap — live prices from CoinGecko, real swap commission tiers."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    await _load_from_db()
    agent = _get_agent(x_api_key)

    from_token = req.get("from_token", "USDC").upper()
    to_token = req.get("to_token", "SOL").upper()
    amount = float(req.get("amount", 0))
    if amount <= 0:
        raise HTTPException(400, "amount must be > 0")

    # Live prices from CoinGecko
    try:
        from price_oracle import get_price
        from_price = await get_price(from_token) if from_token not in ("USDC", "USDT") else 1.0
        to_price = await get_price(to_token) if to_token not in ("USDC", "USDT") else 1.0
    except Exception:
        _fallback = {"SOL": 140, "ETH": 3500, "BTC": 65000, "USDC": 1, "USDT": 1}
        from_price = _fallback.get(from_token, 1)
        to_price = _fallback.get(to_token, 1)
    if not from_price: from_price = 1
    if not to_price: to_price = 1

    cost_usdc = round(amount * from_price, 4)
    output = round(amount * from_price / to_price, 6)

    # Real swap commission tiers (based on 30-day volume if wallet available)
    try:
        from crypto_swap import get_swap_commission_bps, get_swap_tier_name
        swap_bps = get_swap_commission_bps(cost_usdc)
        swap_tier = get_swap_tier_name(cost_usdc)
    except Exception:
        swap_bps = 10  # 0.10% Bronze default
        swap_tier = "BRONZE"
    fee = round(cost_usdc * swap_bps / 10000, 4)

    async with _get_sandbox_lock(x_api_key):
        balance = _get_sandbox_balance(x_api_key)
        if balance < cost_usdc:
            return {
                "sandbox": True, "tx_id": None,
                "message": f"Insufficient balance: ${balance:,.2f} < ${cost_usdc:,.2f} ({amount} {from_token} @ ${from_price:,.2f}). Reset your sandbox.",
                "balance_usdc": balance, "cost_usdc": cost_usdc,
                "from_token": from_token, "from_price_usd": from_price,
            }

        _sandbox_balances[x_api_key] = balance - cost_usdc
    tx_id = f"sandbox_swap_{uuid.uuid4()}"
    _sandbox_trades.append({
        "api_key": x_api_key, "tx_id": tx_id, "type": "swap",
        "from": from_token, "to": to_token, "amount_in": amount,
        "amount_out": output, "fee": fee, "ts": int(time.time()),
    })

    return {
        "sandbox": True, "tx_id": tx_id,
        "from_token": from_token, "to_token": to_token,
        "amount_in": amount, "amount_out": output,
        "cost_usdc": cost_usdc, "fee_usdc": fee,
        "tier": swap_tier, "commission_pct": f"{swap_bps/100}%",
        "rate": f"1 {from_token} = ${from_price:,.2f}",
        "from_price_usd": from_price, "to_price_usd": to_price,
        "balance_after": round(_sandbox_balances[x_api_key], 4),
    }


@router.post("/sandbox/buy-stock")
async def sandbox_buy_stock(req: dict, x_api_key: str = Header(None, alias="X-API-Key")):
    """Test stock purchase — live prices, real stock commission tiers."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    await _load_from_db()
    agent = _get_agent(x_api_key)

    symbol = req.get("symbol", "").upper()
    amount_usdc = float(req.get("amount_usdc", 0))
    if amount_usdc <= 0:
        raise HTTPException(400, "amount_usdc must be > 0")
    if not symbol:
        raise HTTPException(400, "symbol required (e.g. AAPL, TSLA)")

    balance = _get_sandbox_balance(x_api_key)

    # Live stock prices from Yahoo Finance via price_oracle
    try:
        from tokenized_stocks import stock_exchange, TOKENIZED_STOCKS, get_stock_commission_bps, get_stock_tier_name
        if symbol not in TOKENIZED_STOCKS:
            raise HTTPException(400, f"Stock '{symbol}' not available. Available: {', '.join(sorted(TOKENIZED_STOCKS.keys()))}")
        price_data = await stock_exchange.get_price(symbol)
        if isinstance(price_data, dict):
            price = price_data.get("price_usd", 0) or price_data.get("price", 0)
        else:
            price = float(price_data or 0)
        if not price:
            price = TOKENIZED_STOCKS[symbol].get("fallback_price", 100)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Stock price error for %s: %s", symbol, e)
        price = 100  # last resort fallback

    shares = round(amount_usdc / price, 6)

    # Real stock commission tiers (based on transaction amount)
    try:
        stock_bps = get_stock_commission_bps(amount_usdc)
        stock_tier = get_stock_tier_name(amount_usdc)
    except Exception:
        stock_bps = 50  # 0.5% Bronze default
        stock_tier = "BRONZE"
    fee = round(amount_usdc * stock_bps / 10000, 4)

    async with _get_sandbox_lock(x_api_key):
        balance = _get_sandbox_balance(x_api_key)
        if balance < amount_usdc:
            return {
                "sandbox": True, "tx_id": None,
                "message": f"Insufficient balance: ${balance:,.2f} < ${amount_usdc:,.2f}. Reset your sandbox.",
                "balance_usdc": balance, "cost_usdc": amount_usdc,
            }

        _sandbox_balances[x_api_key] = balance - amount_usdc
    if x_api_key not in _sandbox_portfolios:
        _sandbox_portfolios[x_api_key] = {}
    _sandbox_portfolios[x_api_key][symbol] = _sandbox_portfolios[x_api_key].get(symbol, 0) + shares

    return {
        "sandbox": True, "symbol": symbol, "shares": shares,
        "price_per_share": price, "total_usdc": amount_usdc,
        "fee_usdc": fee, "tier": stock_tier, "commission_pct": f"{stock_bps/100}%",
        "balance_after": round(_sandbox_balances[x_api_key], 4),
        "portfolio": _sandbox_portfolios[x_api_key],
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


@router.post("/dispute/{dispute_id}/evidence")
async def submit_dispute_evidence(dispute_id: str, req: dict, x_api_key: str = Header(None, alias="X-API-Key")):
    """Submit evidence for a dispute. Both buyer and seller can submit."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    agent = _get_agent(x_api_key)
    evidence = req.get("evidence", "").strip()[:2000]
    if not evidence:
        raise HTTPException(400, "evidence required (text describing the issue, max 2000 chars)")
    _check_safety(evidence, "evidence")

    try:
        from database import db as _db
        rows = await _db.raw_execute_fetchall("SELECT data FROM disputes WHERE id=?", (dispute_id,))
        if not rows:
            raise HTTPException(404, "Dispute not found")
        dispute = json.loads(rows[0]["data"])

        if dispute.get("status") not in ("open", "escalated"):
            raise HTTPException(400, f"Dispute is {dispute['status']} — cannot submit evidence")

        # Check if agent is buyer or seller in this dispute
        agent_name = agent["name"]
        if agent_name != dispute.get("buyer") and agent_name != dispute.get("seller"):
            raise HTTPException(403, "You are not a party in this dispute")

        role = "buyer" if agent_name == dispute.get("buyer") else "seller"
        if "evidence" not in dispute:
            dispute["evidence"] = []
        dispute["evidence"].append({
            "role": role,
            "agent": agent_name,
            "text": evidence,
            "submitted_at": int(time.time()),
        })

        await _db.raw_execute(
            "UPDATE disputes SET data=? WHERE id=?",
            (json.dumps(dispute), dispute_id))

        return {"success": True, "dispute_id": dispute_id, "evidence_count": len(dispute["evidence"])}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, "Error submitting evidence")


@router.post("/dispute/{dispute_id}/escalate")
async def escalate_dispute(dispute_id: str, x_api_key: str = Header(None, alias="X-API-Key")):
    """Escalate a dispute for manual review (requires evidence from both parties)."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    agent = _get_agent(x_api_key)

    try:
        from database import db as _db
        rows = await _db.raw_execute_fetchall("SELECT data FROM disputes WHERE id=?", (dispute_id,))
        if not rows:
            raise HTTPException(404, "Dispute not found")
        dispute = json.loads(rows[0]["data"])

        if dispute.get("status") != "open":
            raise HTTPException(400, f"Dispute is {dispute['status']} — cannot escalate")

        agent_name = agent["name"]
        if agent_name != dispute.get("buyer") and agent_name != dispute.get("seller"):
            raise HTTPException(403, "You are not a party in this dispute")

        # Require at least one piece of evidence before escalation
        evidence_list = dispute.get("evidence", [])
        if not evidence_list:
            raise HTTPException(400, "Submit evidence before escalating (POST /dispute/{id}/evidence)")

        dispute["status"] = "escalated"
        dispute["escalated_at"] = int(time.time())
        dispute["escalated_by"] = agent_name

        await _db.raw_execute(
            "UPDATE disputes SET data=? WHERE id=?",
            (json.dumps(dispute), dispute_id))

        return {
            "success": True,
            "dispute_id": dispute_id,
            "status": "escalated",
            "message": "Dispute escalated for manual review. Resolution within 72h.",
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, "Error escalating dispute")


@router.get("/disputes")
async def list_my_disputes(x_api_key: str = Header(None, alias="X-API-Key")):
    """List all disputes for the authenticated agent (as buyer or seller)."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    agent = _get_agent(x_api_key)
    agent_name = agent["name"]

    try:
        from database import db as _db
        rows = await _db.raw_execute_fetchall("SELECT data FROM disputes", ())
        my_disputes = []
        for r in rows:
            d = json.loads(r["data"])
            if d.get("buyer") == agent_name or d.get("seller") == agent_name:
                my_disputes.append(d)
        my_disputes.sort(key=lambda x: x.get("created_at", 0), reverse=True)
        return {"disputes": my_disputes[:50], "total": len(my_disputes)}
    except Exception:
        return {"disputes": [], "total": 0}


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
        "audit": 4.99, "data": 1.99, "code": 2.99,
        "text": 0.05, "image": 0.10, "audit_deep": 49.99,
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
        logger.error("Payment verification error in /buy: %s", e)
        raise HTTPException(400, "Payment verification failed. Ensure your USDC transfer to Treasury is confirmed on Solana.")

    logger.info("/buy payment verified: %s... (%s USDC from %s...)", payment_tx[:16], price, tx_result.get("from", "?")[:12])

    # Calculer la commission (based on transaction amount)
    commission_bps = get_commission_bps(price)
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
        logger.error("AI service error: %s", e)
        raise HTTPException(502, "AI service temporarily unavailable")

    # Isolation multi-tenant
    from tenant_isolation import get_current_tenant
    _tenant_id = get_current_tenant() or "default"

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
        "tenant_id": _tenant_id,
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

    # Referral commission (50% of MAXIA's commission to referrer)
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

    # OFAC compliance check
    require_ofac_clear(agent["wallet"], "seller wallet")

    # Clone detection: warn if this is a copy of an existing service
    clone_info = _check_clone(name, description, endpoint, x_api_key)
    clone_warning = None
    if clone_info.get("is_clone"):
        clone_warning = {
            "warning": "This service appears to be a clone of an existing listing.",
            "original_service_id": clone_info["original_service_id"],
            "original_agent": clone_info["original_agent"],
            "note": "Clones start with 0 reputation. Original creators get discovery priority.",
        }

    service_id = str(uuid.uuid4())
    service = {
        "id": service_id,
        "agent_api_key": x_api_key,
        "agent_name": agent["name"],
        "agent_wallet": agent["wallet"],
        "name": name,
        "description": description,
        "type": service_type,
        "price_usdc": price_usdc,
        "endpoint": endpoint,
        "status": "active",
        "rating": 5.0 if not clone_info.get("is_clone") else 3.0,  # Clones start lower
        "sales": 0,
        "listed_at": int(time.time()),
        "is_original": not clone_info.get("is_clone", False),
    }
    _agent_services.append(service)
    agent["services_listed"] += 1

    # Register content hash for future clone detection
    _register_service_hash(service_id, agent["name"], x_api_key, name, description, endpoint)

    # Persist to SQLite
    try:
        from database import db
        await db.save_service(service)
        await db.update_agent(x_api_key, {"services_listed": agent["services_listed"]})
    except Exception as e:
        logger.error("DB save service error: %s", e)

    logger.info("Nouveau service: %s par %s @ %s USDC", name, agent["name"], price_usdc)

    response = {
        "success": True,
        "service_id": service["id"],
        "name": name,
        "price_usdc": price_usdc,
        "is_original": service.get("is_original", True),
        "commission": "Marketplace: 1% Bronze → 0.5% Gold → 0.1% Whale | Swap: 0.10% → 0.01%",
        "message": f"Service liste. Les autres IA peuvent maintenant acheter {name} sur MAXIA.",
    }
    if clone_warning:
        response["clone_warning"] = clone_warning
    return response


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
        logger.error("Payment verification error: %s", e)
        raise HTTPException(400, "Payment verification failed")

    # Commission MAXIA (based on transaction amount)
    commission_bps = get_commission_bps(price)
    commission = price * commission_bps / 10000
    seller_gets = price - commission

    # Isolation multi-tenant
    from tenant_isolation import get_current_tenant
    _tenant_id = get_current_tenant() or "default"

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
        "tenant_id": _tenant_id,
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
                logger.info("Seller paid: %s USDC -> %s...", seller_gets, seller_wallet[:8])
                seller_payment_info["seller_paid"] = True
                seller_payment_info["seller_tx"] = transfer.get("signature", "")
            else:
                seller_payment_info["seller_paid"] = False
                seller_payment_info["seller_error"] = transfer.get("error", "")
        except Exception as e:
            logger.error("Seller payment error: %s", e)
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
    volume = agent["volume_30d"]
    return {
        "name": agent["name"],
        "volume_30d": volume,
        "total_spent": agent["total_spent"],
        "total_earned": agent["total_earned"],
        "services_listed": agent["services_listed"],
        "registered_at": agent["registered_at"],
        "commission_note": "Commission is based on transaction amount, not cumulative volume",
        "tiers": {
            "BRONZE": {"min_amount": 0, "commission": "1%", "note": "Transactions < $500"},
            "GOLD": {"min_amount": 500, "commission": "0.5%", "note": "Transactions $500 - $5K"},
            "WHALE": {"min_amount": 5000, "commission": "0.1%", "note": "Transactions $5K+"},
        },
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
    # Compter les services natifs MAXIA (toujours disponibles)
    native_count = 0
    try:
        from config import SERVICE_PRICES
        native_count = len(SERVICE_PRICES)
    except Exception:
        native_count = 17  # fallback connu

    # Live counts from config
    token_count = 0
    stock_count = 0
    mcp_count = 0
    try:
        from price_oracle import TOKEN_MINTS
        stock_syms = {"AAPL","TSLA","NVDA","GOOGL","MSFT","AMZN","META","MSTR","SPY","QQQ",
                      "NFLX","AMD","PLTR","COIN","CRM","INTC","UBER","MARA","AVGO","DIA",
                      "IWM","GLD","ARKK","RIOT","SHOP","SQ","PYPL","ORCL"}
        token_count = len([s for s in TOKEN_MINTS if s not in stock_syms])
        stock_count = len([s for s in TOKEN_MINTS if s in stock_syms])
    except Exception:
        token_count = 68
        stock_count = 25
    try:
        from mcp_server import TOOLS
        mcp_count = len(TOOLS)
    except Exception:
        mcp_count = 46

    return {
        "registered_agents": max(len(_registered_agents), db_stats.get("agents_registered", 0), 1),
        "services_listed": max(len([s for s in _agent_services if s.get("status") == "active"]) + native_count, db_stats.get("services_listed", 0)),
        "total_services": native_count + len([s for s in _agent_services if s.get("status") == "active"]),
        "total_transactions": max(mem_txs, db_stats.get("total_transactions", 0)),
        "total_volume_usdc": max(mem_vol, db_stats.get("total_volume_usdc", 0)),
        "total_commission_usdc": max(mem_comm, db_stats.get("total_commission_usdc", 0)),
        "total_tokens": token_count,
        "total_stocks": stock_count,
        "mcp_tools": mcp_count,
        "commission_tiers": {
            "bronze": "1.5% (0-500 USDC)",
            "gold": "0.5% (500-5000 USDC)",
            "whale": "0.1% (5000+ USDC)",
        },
    }


# #15 Encheres inversees — buyer poste une demande, sellers encherissent
_reverse_auctions: list = []

@router.post("/request-service")
async def request_service(req: dict, x_api_key: str = Header(None, alias="X-API-Key")):
    """Reverse auction: buyer posts what they need, sellers can bid.

    Body: { "capability": "sentiment", "max_price": 0.05, "description": "Analyze 1000 tweets", "deadline_hours": 24 }
    """
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")

    capability = req.get("capability", "").strip()
    max_price = float(req.get("max_price", 10))
    description = req.get("description", "").strip()
    deadline_hours = int(req.get("deadline_hours", 24))

    if not capability:
        raise HTTPException(400, "capability required")
    if max_price <= 0:
        raise HTTPException(400, "max_price must be > 0")

    auction = {
        "id": str(uuid.uuid4()),
        "buyer_key": x_api_key,
        "capability": capability,
        "max_price": max_price,
        "description": description[:500],
        "deadline_at": int(time.time()) + deadline_hours * 3600,
        "created_at": int(time.time()),
        "bids": [],
        "status": "open",
    }
    _reverse_auctions.append(auction)

    return {"success": True, "auction_id": auction["id"], "expires_in_hours": deadline_hours,
            "message": f"Request posted. Sellers can bid at GET /api/public/auctions/{auction['id']}"}


@router.get("/auctions")
async def list_reverse_auctions():
    """List open reverse auctions (service requests from buyers)."""
    now = int(time.time())
    open_auctions = [a for a in _reverse_auctions if a["status"] == "open" and a["deadline_at"] > now]
    return {"count": len(open_auctions), "auctions": [{
        "id": a["id"], "capability": a["capability"], "max_price": a["max_price"],
        "description": a["description"], "bids_count": len(a["bids"]),
        "deadline_at": a["deadline_at"], "created_at": a["created_at"],
    } for a in open_auctions]}


@router.post("/auctions/{auction_id}/bid")
async def bid_on_auction(auction_id: str, req: dict, x_api_key: str = Header(None, alias="X-API-Key")):
    """Seller bids on a reverse auction.

    Body: { "price": 0.03, "estimated_time_s": 5 }
    """
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")

    auction = next((a for a in _reverse_auctions if a["id"] == auction_id and a["status"] == "open"), None)
    if not auction:
        raise HTTPException(404, "Auction not found or closed")

    price = float(req.get("price", 0))
    if price <= 0 or price > auction["max_price"]:
        raise HTTPException(400, f"Price must be between 0 and {auction['max_price']}")

    bid = {
        "seller_key": x_api_key,
        "price": price,
        "estimated_time_s": int(req.get("estimated_time_s", 10)),
        "bid_at": int(time.time()),
    }
    auction["bids"].append(bid)
    auction["bids"].sort(key=lambda b: b["price"])  # Cheapest first

    return {"success": True, "position": auction["bids"].index(bid) + 1, "total_bids": len(auction["bids"])}


# #16 Prix dynamique supply/demand
_demand_tracker: dict = {}  # service_type -> request_count_last_hour

def _track_demand(service_type: str):
    """Track demand for dynamic pricing signals."""
    now = int(time.time())
    key = service_type.lower()
    if key not in _demand_tracker:
        _demand_tracker[key] = []
    _demand_tracker[key].append(now)
    # Keep only last hour
    _demand_tracker[key] = [t for t in _demand_tracker[key] if now - t < 3600]


@router.get("/demand")
async def get_demand():
    """See current demand levels per service type. Sellers can adjust prices accordingly."""
    now = int(time.time())
    result = {}
    for stype, timestamps in _demand_tracker.items():
        recent = [t for t in timestamps if now - t < 3600]
        result[stype] = {
            "requests_last_hour": len(recent),
            "trend": "high" if len(recent) > 10 else "medium" if len(recent) > 3 else "low",
        }
    return {"demand": result, "note": "High demand = opportunity to list services at higher prices"}


# #2 Transparence multi-chain
@router.get("/chain-support")
async def chain_support():
    """What features are available on which chains. Honest transparency."""
    return {
        "chains": 14,
        "features": {
            "swap": {"chains": ["solana", "ethereum", "base", "arbitrum", "polygon", "avalanche", "bnb"], "method": "Jupiter (Solana) + 1inch (EVM)", "tokens": 71},
            "tokenized_stocks": {"chains": ["solana", "ethereum", "arbitrum"], "method": "Jupiter + 1inch + Dinari", "stocks": 25},
            "escrow": {"chains": ["solana"], "method": "Wallet-based (smart contract pending)", "note": "On-chain PDA escrow coming with 2.91 SOL deploy"},
            "gpu_rental": {"chains": ["solana"], "method": "RunPod + local 7900XT", "tiers": 7},
            "defi_yields": {"chains": ["solana", "ethereum", "base", "polygon", "arbitrum", "avalanche", "near"], "method": "DeFiLlama + direct protocol APIs"},
            "bridge": {"chains": ["solana", "ethereum", "base", "polygon", "arbitrum", "avalanche", "bnb", "optimism"], "method": "Li.Fi aggregator"},
            "wallet_analysis": {"chains": ["solana"], "method": "Helius DAS API"},
            "scout_scan": {"chains": ["solana", "ethereum", "base", "polygon", "arbitrum", "avalanche", "bnb", "ton", "sui", "near", "aptos", "sei", "xrp", "tron"], "method": "RPC + registries"},
            "payments": {"chains": ["solana", "ethereum", "base", "xrp", "polygon", "arbitrum", "avalanche", "bnb", "ton", "sui", "tron", "near", "aptos", "sei"], "method": "USDC verification on each chain"},
        },
        "note": "Not all features are available on all chains. We prioritize where agents actually are.",
    }


# #14 Prix plancher par catégorie
SERVICE_MIN_PRICES = {
    "audit": 1.00, "code": 0.50, "text": 0.01, "data": 0.01,
    "image": 0.05, "ai": 0.001, "compute": 1.00, "defi": 0,
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

    # #16 Track demand
    if capability_lower:
        _track_demand(capability_lower)

    # Search services directly from DB using SQL LIKE (matches name, description, AND type)
    # This avoids stale in-memory cache issues and ensures fresh results
    try:
        from database import db
        _svc_cols = ("id, agent_api_key, agent_name, agent_wallet, name, description, "
                     "type, price_usdc, endpoint, status, rating, rating_count, sales, listed_at")
        if capability_lower:
            query = (
                f"SELECT {_svc_cols} FROM agent_services WHERE status='active' "
                "AND (LOWER(type) LIKE ? OR LOWER(name) LIKE ? OR LOWER(description) LIKE ?)"
            )
            cap_pattern = f"%{capability_lower}%"
            rows = await db.raw_execute_fetchall(query, (cap_pattern, cap_pattern, cap_pattern))
        elif agent_type:
            query = f"SELECT {_svc_cols} FROM agent_services WHERE status='active' AND LOWER(type) LIKE ?"
            rows = await db.raw_execute_fetchall(query, (f"%{agent_type.lower()}%",))
        else:
            query = f"SELECT {_svc_cols} FROM agent_services WHERE status='active'"
            rows = await db.raw_execute_fetchall(query, ())
        db_services = [dict(r) for r in rows]
    except Exception as e:
        logger.warning("DB query fallback to in-memory: %s", e)
        db_services = [s for s in _agent_services if s.get("status") == "active"]

    for s in db_services:
        if s.get("price_usdc", 0) > max_price:
            continue
        if s.get("rating", 5) < min_rating:
            continue
        # If both capability and agent_type are specified, also filter by agent_type
        if capability_lower and agent_type and agent_type.lower() not in s.get("type", "").lower():
            continue

        # #8 Success rate + #1 Metrics + #9 Tags + #18 Commission transparent + #14 Quality minimum
        total_exec = s.get("total_executions", 0)
        success_exec = s.get("successful_executions", 0)
        success_rate = round(success_exec / max(total_exec, 1) * 100, 1)

        # #17 Quality minimum: delist if success rate < 80% and > 10 executions
        if total_exec > 10 and success_rate < 80:
            continue

        # #9 Auto-tags based on metrics
        tags = []
        avg_response = s.get("avg_response_ms", 0)
        if avg_response and avg_response < 1000:
            tags.append("fast")
        if success_rate >= 99 and total_exec > 5:
            tags.append("reliable")
        if s.get("price_usdc", 0) < 0.10:
            tags.append("cheap")
        if s.get("rating", 0) >= 4.5 and s.get("sales", 0) >= 5:
            tags.append("top-rated")
        # Original Creator badge
        is_original = s.get("is_original", True) or _is_original_creator(s["id"])
        if is_original:
            tags.append("original-creator")

        # #18 Commission transparent — show all tiers so buyer sees their actual rate
        price = s.get("price_usdc", 0)
        commission_bronze = round(price * get_commission_bps(0) / 10000, 4)
        commission_gold = round(price * get_commission_bps(500) / 10000, 4)
        commission_whale = round(price * get_commission_bps(5000) / 10000, 4)

        results.append({
            "service_id": s["id"],
            "name": s.get("name", ""),
            "description": s.get("description", ""),
            "type": s.get("type", ""),
            "price_usdc": price,
            "commission_usdc": {"bronze": commission_bronze, "gold": commission_gold, "whale": commission_whale},
            "seller_gets_usdc": {"bronze": round(price - commission_bronze, 4), "gold": round(price - commission_gold, 4), "whale": round(price - commission_whale, 4)},
            "seller": s.get("agent_name", ""),
            "rating": s.get("rating", 5),
            "sales": s.get("sales", 0),
            "success_rate_pct": success_rate,
            "total_executions": total_exec,
            "avg_response_ms": avg_response,
            "uptime_pct": s.get("uptime_pct", 100),
            "tags": tags,
            "endpoint": s.get("endpoint", ""),
            "listed_at": s.get("listed_at", 0),
            "chains": s.get("chains", ["solana"]),
            "is_original": is_original,
            "badges": ["Original Creator"] if is_original else [],
        })

    # Also include MAXIA native services (8 AI services powered by Groq/Ollama)
    maxia_native = [
        {"service_id": "maxia-audit", "name": "Smart Contract Audit", "type": "audit", "price_usdc": 4.99, "seller": "MAXIA", "rating": 5, "description": "AI-powered security audit of Solana/EVM smart contracts. Detects vulnerabilities, reentrancy, overflow, access control issues."},
        {"service_id": "maxia-code", "name": "AI Code Review", "type": "code", "price_usdc": 2.99, "seller": "MAXIA", "rating": 5, "description": "Automated code review for Python, Rust, JavaScript, Solidity. Finds bugs, suggests improvements, checks best practices."},
        {"service_id": "maxia-translate", "name": "AI Translation", "type": "text", "price_usdc": 0.05, "seller": "MAXIA", "rating": 5, "description": "Translate text between 50+ languages. Technical documentation, marketing copy, chat messages."},
        {"service_id": "maxia-summary", "name": "Document Summary", "type": "text", "price_usdc": 0.49, "seller": "MAXIA", "rating": 5, "description": "Summarize any document, whitepaper, or article into key bullet points. Supports up to 10,000 words."},
        {"service_id": "maxia-wallet", "name": "Wallet Analyzer", "type": "data", "price_usdc": 1.99, "seller": "MAXIA", "rating": 5, "description": "Deep analysis of any Solana wallet: token holdings, transaction history, DeFi positions, risk score."},
        {"service_id": "maxia-marketing", "name": "Marketing Copy Generator", "type": "text", "price_usdc": 0.99, "seller": "MAXIA", "rating": 5, "description": "Generate landing page copy, Twitter threads, blog posts, product descriptions. Optimized for Web3/AI audience."},
        {"service_id": "maxia-image", "name": "AI Image Generator", "type": "image", "price_usdc": 0.10, "seller": "MAXIA", "rating": 5, "description": "Generate images from text prompts. Logos, illustrations, social media graphics. 1024x1024 resolution."},
        {"service_id": "maxia-scraper", "name": "Web Scraper", "type": "data", "price_usdc": 0.02, "seller": "MAXIA", "rating": 5, "description": "Extract structured data from any website. Returns clean JSON with the data you need."},
    ]
    for ns in maxia_native:
        searchable = f"{ns['name']} {ns['description']} {ns['type']}".lower()
        if capability_lower and capability_lower not in searchable:
            continue
        if ns["price_usdc"] > max_price:
            continue
        results.append(ns)

    # #7 Sort by composite score: success_rate * rating * log(sales+1) + original creator boost
    import math
    for r in results:
        base_score = (r.get("success_rate_pct", 50) / 100) * r.get("rating", 3) * math.log(r.get("sales", 0) + 2)
        # Original creators get 20% ranking boost
        original_boost = 1.2 if r.get("is_original", False) else 1.0
        r["_score"] = base_score * original_boost
    results.sort(key=lambda x: (-x.get("_score", 0), x["price_usdc"]))
    for r in results:
        r.pop("_score", None)

    # #7 Leaderboard: top 3 per type
    leaderboard = {}
    for r in results:
        t = r.get("type", "other")
        if t not in leaderboard:
            leaderboard[t] = []
        if len(leaderboard[t]) < 3:
            leaderboard[t].append({"service_id": r["service_id"], "name": r.get("name", ""), "rating": r.get("rating", 0), "success_rate": r.get("success_rate_pct", 0)})

    return {
        "query": {"capability": capability, "max_price": max_price, "min_rating": min_rating},
        "results_count": len(results),
        "agents": results,
        "leaderboard": leaderboard,
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

    # Agent permissions — scope + spend check
    try:
        from agent_permissions import check_agent_scope, check_agent_spend
        wallet = buyer.get("wallet", "")
        perms = await check_agent_scope(x_api_key, wallet, "marketplace:execute")
        # Spend check will happen after we know the price
    except HTTPException:
        raise
    except Exception:
        pass  # Graceful degradation — permissions table may not exist yet

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
        price = {
            "maxia-audit": 4.99, "maxia-code": 2.99, "maxia-data": 2.99,
            "maxia-scraper": 0.02, "maxia-image": 0.10, "maxia-translate": 0.05,
            "maxia-summary": 0.49, "maxia-wallet": 1.99, "maxia-marketing": 0.99,
            "maxia-finetune": 2.99, "maxia-awp-stake": 0,
            "maxia-transcription": 0.01, "maxia-embedding": 0.001,
            "maxia-sentiment": 0.005, "maxia-wallet-score": 0.10,
            "maxia-airdrop-scan": 0.50, "maxia-smart-money": 0.25,
            "maxia-nft-rarity": 0.05,
        }.get(service_id, 1.99)
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
        logger.error("Payment verification error in /execute: %s", e)
        raise HTTPException(400, "Payment verification failed. Ensure your USDC transfer to Treasury is confirmed on Solana.")

    payment_verified = True
    payment_info = {
        "verified": True,
        "signature": payment_tx,
        "amount_usdc": verification.get("amount_usdc", price),
        "from": verification.get("from", ""),
        "to": verification.get("to", TREASURY_ADDRESS),
    }

    logger.info("/execute payment verified: %s... (%s USDC from %s...)", payment_tx[:16], price, verification.get("from", "?")[:12])

    # ═══ NATIVE SERVICE EXECUTION ═══
    if is_native:
        _exec_start = time.time()
        result_text = await _execute_native_service(service_id, prompt)
        _exec_ms = int((time.time() - _exec_start) * 1000)

        # #6 Refund auto if service failed
        _service_failed = not result_text or "unavailable" in result_text.lower() or "error" in result_text.lower()

        # #1 Track execution metrics
        try:
            from database import db as _metrics_db
            await _metrics_db.raw_execute(
                "UPDATE agent_services SET total_executions = COALESCE(total_executions, 0) + 1, "
                "successful_executions = COALESCE(successful_executions, 0) + ?, "
                "avg_response_ms = COALESCE(avg_response_ms, 0) * 0.9 + ? * 0.1 "
                "WHERE id = ?",
                (0 if _service_failed else 1, _exec_ms, service_id))
        except Exception:
            pass

        commission_bps = get_commission_bps(price)
        commission = price * commission_bps / 10000

        # Isolation multi-tenant
        from tenant_isolation import get_current_tenant
        _tenant_id = get_current_tenant() or "default"

        tx = {
            "tx_id": str(uuid.uuid4()), "buyer": buyer["name"],
            "seller": "MAXIA", "service": service_id,
            "price_usdc": price, "commission_usdc": commission,
            "seller_gets_usdc": price - commission,
            "tenant_id": _tenant_id,
            "timestamp": int(time.time()),
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

        # Referral commission (50% of MAXIA's commission to referrer)
        try:
            from referral_manager import add_commission
            await add_commission(buyer["wallet"], commission)
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
    commission_bps = get_commission_bps(price)
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
                logger.info("Seller paid: %s USDC -> %s...", seller_gets, seller_wallet[:8])
                payment_info["seller_paid"] = True
                payment_info["seller_tx"] = transfer.get("signature", "")
            else:
                payment_info["seller_paid"] = False
                payment_info["seller_error"] = transfer.get("error", "")
        except Exception as e:
            logger.error("Seller payment error: %s", e)
            payment_info["seller_paid"] = False
            payment_info["seller_error"] = "Seller payout pending — will retry"

    # Isolation multi-tenant
    from tenant_isolation import get_current_tenant
    _tenant_id = get_current_tenant() or "default"

    # Record transaction
    tx = {
        "tx_id": str(uuid.uuid4()), "buyer": buyer["name"],
        "seller": service["agent_name"], "service": service["name"],
        "price_usdc": price, "commission_usdc": commission,
        "seller_gets_usdc": seller_gets,
        "tenant_id": _tenant_id,
        "timestamp": int(time.time()),
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

    # Referral commission (50% of MAXIA's commission to referrer)
    try:
        from referral_manager import add_commission
        await add_commission(buyer["wallet"], commission)
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
                from http_client import get_http_client
                client = get_http_client()
                resp = await client.post(endpoint, json={
                    "prompt": prompt,
                    "buyer": buyer["name"],
                    "service_id": service_id,
                    "tx_id": tx["tx_id"],
                    "payment_tx": payment_tx,
                    "payment_verified": True,
                    "amount_usdc": price,
                }, timeout=30)
                if resp.status_code == 200:
                    result_data = resp.json()
                    result_text = result_data.get("result", result_data.get("text", str(result_data)))
                    execution_method = "webhook"
                else:
                    result_text = f"Seller webhook returned {resp.status_code}"
                    execution_method = "webhook_error"
            except Exception as e:
                logger.error("Webhook call error: %s", e)
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
        logger.error("Webhook notification error: %s", e)

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


_SERVICE_PROMPTS = {
    "maxia-audit": "You are a smart contract security auditor. Analyze the code for vulnerabilities: reentrancy, overflow, access control, flash loan attacks. Structure: [CRITICAL][MAJOR][MINOR][INFO]. Be thorough and specific.",
    "maxia-code-review": "You are a senior software engineer. Review code for bugs, performance issues, and best practices. Suggest improvements.",
    "maxia-translate": "You are a professional translator. Translate accurately while preserving meaning and tone. Auto-detect source language.",
    "maxia-summary": "You are a document summarizer. Extract key points into clear bullet points. Be concise but comprehensive.",
    "maxia-wallet-analysis": "You are a blockchain wallet analyst. Analyze wallet addresses for token holdings, transaction patterns, DeFi positions, and risk indicators. Provide a risk score from 0-100.",
    "maxia-marketing": "You are a Web3 marketing copywriter. Generate compelling copy optimized for the Web3/AI audience.",
    "maxia-image": "You are an image generation prompt engineer. Create a detailed prompt for FLUX.1 / Stable Diffusion based on the user's request.",
    "maxia-scraper": "You are a web scraping assistant. Extract and structure data from the provided URL or content into clean JSON.",
    "maxia-transcription": "You are an audio transcription assistant. Provide structured transcription from the audio context described.",
    "maxia-embedding": "You are a text embedding assistant. Convert text into key concepts and semantic categories. Return JSON array.",
    "maxia-sentiment": "You are a sentiment analysis engine. Return JSON: sentiment_score (-1 to 1), confidence (0-1), label, key_phrases.",
    "maxia-wallet-risk": "You are a blockchain wallet risk analyst. Score 0-100 (0=high risk, 100=safe). Analyze: balance, age, tx count, DeFi exposure. Return JSON.",
    "maxia-airdrop-scanner": "You are an airdrop eligibility analyst. List protocols where the wallet may qualify. Return JSON with protocol names and likelihood.",
    "maxia-smart-money": "You are a smart money tracker. Analyze whale movements: large transfers, accumulation patterns, DeFi positions. Return JSON.",
    "maxia-nft-rarity": "You are an NFT rarity calculator. Calculate rarity score from trait distribution. Return JSON with score and rare traits.",
    "maxia-finetune": "You are a fine-tuning advisor. Help the user plan their LLM fine-tuning: dataset prep, hyperparams, GPU requirements.",
    "maxia-defi-yields": "You are a DeFi yield analyst. Find the best APY across lending, staking, LP protocols on major chains. Return JSON.",
}


async def _execute_native_service(service_id: str, prompt: str) -> str:
    """Execute a MAXIA native service via LLM Router (Groq -> Mistral -> Claude fallback)."""
    sys_prompt = _SERVICE_PROMPTS.get(service_id, "You are a helpful AI assistant.")

    try:
        from llm_router import router as llm_router
        result = await llm_router.call(prompt=prompt, system=sys_prompt, max_tokens=1500)
        if result:
            return result
    except Exception as e:
        logger.error("LLM Router error: %s", e)

    # Direct Groq fallback (legacy)
    try:
        if groq_client:
            def _call():
                resp = groq_client.chat.completions.create(
                    model=GROQ_MODEL,
                    messages=[{"role": "system", "content": sys_prompt}, {"role": "user", "content": prompt}],
                    max_tokens=1500, temperature=0.7,
                )
                return resp.choices[0].message.content.strip()
            return await asyncio.to_thread(_call)
    except Exception as e:
        logger.error("Groq fallback error: %s", e)

    return "Service temporarily unavailable — please retry in a few seconds"


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
        logger.error("DB tx save error: %s", e)


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
async def defi_best_yield(asset: str = "USDC", chain: str = "", min_tvl: float = 100000,
                          limit: int = 10, type: str = ""):
    """Find the best DeFi yields for an asset. Free, no auth.

    Examples:
      GET /defi/best-yield?asset=USDC
      GET /defi/best-yield?asset=ETH&chain=ethereum&limit=5
      GET /defi/best-yield?asset=SOL&chain=solana&type=staking
      GET /defi/best-yield?asset=ALL&type=lending
    Types: staking, lending, lp, farming (empty = all)
    """
    try:
        from defi_scanner import get_best_yields
        yields = await get_best_yields(asset, chain, min_tvl, limit * 3, yield_type=type)
        # Filter insane APY (>1000%) and tiny pools
        sane = [y for y in yields if 0 < y.get("apy", 0) < 200 and y.get("tvl_usd", 0) >= 100000]
        if sane:
            return {
                "asset": asset,
                "chain": chain or "all",
                "type": type or "all",
                "results": len(sane[:limit]),
                "yields": sane[:limit],
                "sources": ["DeFiLlama", "Marinade", "Jito", "Lido"],
            }
    except Exception:
        pass

    # Fallback: fetch live depuis DeFiLlama au lieu de valeurs hardcodees
    try:
        import httpx as _hx
        _LLAMA_FALLBACK_MAP = {
            "USDC": [
                ("aave-v3", "usdc", "Aave V3", "Ethereum", "https://app.aave.com/", "low"),
                ("compound-v3", "usdc", "Compound V3", "Ethereum", "https://app.compound.finance/", "low"),
                ("kamino-lend", "usdc", "Kamino", "Solana", "https://app.kamino.finance/", "low"),
            ],
            "SOL": [
                ("marinade-finance", "msol", "Marinade", "Solana", "https://marinade.finance/app/stake/", "low"),
                ("jito", "jitosol", "Jito", "Solana", "https://www.jito.network/staking/", "low"),
                ("sanctum", "inf", "Sanctum", "Solana", "https://app.sanctum.so/", "medium"),
                ("raydium", "sol-usdc", "Raydium", "Solana", "https://raydium.io/liquidity/", "medium"),
            ],
            "ETH": [
                ("lido", "steth", "Lido", "Ethereum", "https://stake.lido.fi/", "low"),
                ("rocket-pool", "reth", "Rocket Pool", "Ethereum", "https://stake.rocketpool.net/", "low"),
                ("eigenlayer", "eth", "Eigenlayer", "Ethereum", "https://app.eigenlayer.xyz/", "medium"),
            ],
        }
        async with _hx.AsyncClient(timeout=15) as _client:
            _resp = await _client.get("https://yields.llama.fi/pools")
            _resp.raise_for_status()
            _pools = _resp.json().get("data", [])
            # Index par project_symbol
            _pool_idx = {}
            for _p in _pools:
                _k = f"{_p.get('project', '').lower()}_{_p.get('symbol', '').lower()}"
                if _k not in _pool_idx or _p.get("apy", 0) > _pool_idx[_k].get("apy", 0):
                    _pool_idx[_k] = _p
            # Aussi indexer par project seul pour les chains specifiques
            for _p in _pools:
                proj = _p.get("project", "").lower()
                ch = _p.get("chain", "").lower()
                sym = (_p.get("symbol") or "").upper()
                _k2 = f"{proj}_{ch}_{sym}"
                if _k2 not in _pool_idx or _p.get("apy", 0) > _pool_idx[_k2].get("apy", 0):
                    _pool_idx[_k2] = _p

            targets = _LLAMA_FALLBACK_MAP.get(asset.upper(), _LLAMA_FALLBACK_MAP.get("USDC", []))
            fb = []
            for proj_key, sym_key, display, fb_chain, url, risk in targets:
                lookup = f"{proj_key}_{sym_key}"
                pool_data = _pool_idx.get(lookup, {})
                apy_val = round(pool_data.get("apy", 0), 2)
                tvl_val = round(pool_data.get("tvlUsd", 0), 0)
                if apy_val > 0:
                    fb.append({"project": display, "chain": fb_chain, "apy": apy_val,
                               "tvl_usd": tvl_val, "risk": risk, "url": url, "source": "defillama_live"})
            if fb:
                return {
                    "asset": asset,
                    "chain": chain or "all",
                    "results": len(fb[:limit]),
                    "yields": fb[:limit],
                    "source": "defillama_fallback",
                }
    except Exception:
        pass

    # Dernier recours: message explicite qu'aucune donnee live n'est disponible
    return {
        "asset": asset,
        "chain": chain or "all",
        "results": 0,
        "yields": [],
        "source": "unavailable",
        "message": "APIs DeFiLlama et natives indisponibles. Reessayez dans quelques minutes.",
    }


@router.get("/defi/protocol")
async def defi_protocol(name: str = "aave"):
    """Get stats for a specific DeFi protocol."""
    try:
        from defi_scanner import get_protocol_stats
        return await get_protocol_stats(name)
    except Exception as e:
        return safe_error(e, "operation")


@router.get("/defi/chains")
async def defi_chains():
    """Get TVL by blockchain."""
    try:
        from defi_scanner import get_chain_tvl
        return await get_chain_tvl()
    except Exception as e:
        return safe_error(e, "operation")


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
        return safe_error(e, "operation")


@router.get("/trending")
async def public_trending():
    """Get trending crypto tokens."""
    try:
        from sentiment_analyzer import get_trending
        return {"trending": await get_trending()}
    except Exception as e:
        return safe_error(e, "operation")


@router.get("/fear-greed")
async def public_fear_greed():
    """Get crypto Fear & Greed Index."""
    try:
        from web3_services import get_fear_greed_index
        return await get_fear_greed_index()
    except Exception as e:
        return safe_error(e, "operation")


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
        return safe_error(e, "operation")


@router.get("/wallet-analysis")
async def public_wallet_analysis(address: str = ""):
    """Analyze a Solana wallet — holdings, activity, profile. Free, no auth."""
    if not address:
        return {"error": "address parameter required"}
    try:
        from web3_services import analyze_wallet
        return await analyze_wallet(address)
    except Exception as e:
        return safe_error(e, "operation")


# ══════════════════════════════════════════

@router.get("/gpu/tiers")
async def public_gpu_tiers():
    """GPU tiers with live Akash pricing + 15% MAXIA markup. Cheaper than cloud alternatives."""
    import time as _t
    from config import GPU_TIERS, AKASH_ENABLED
    _MARKUP = 0.15

    tiers = []
    akash_ok = False
    _akash = None
    akash_map = {}
    if AKASH_ENABLED:
        try:
            from akash_client import akash as _akash_inst, AKASH_GPU_MAP
            _akash = _akash_inst
            akash_map = AKASH_GPU_MAP
            akash_ok = True
        except Exception:
            pass

    for gpu in GPU_TIERS:
        tier_id = gpu["id"]
        base_price = gpu["base_price_per_hour"]

        # Akash price with MAXIA markup
        if akash_ok and tier_id in akash_map and _akash:
            akash_cost = await _akash.get_price_estimate(tier_id)
            if akash_cost:
                sell_price = round(akash_cost * (1 + _MARKUP), 2)
            else:
                sell_price = round(base_price * 0.85, 2)
        else:
            sell_price = base_price

        # Check real availability on Akash
        is_avail = True
        if akash_ok and _akash and tier_id in akash_map:
            try:
                is_avail = await _akash.check_tier_available(tier_id)
            except Exception:
                is_avail = True  # Assume available if check fails

        tier = {
            "id": tier_id,
            "label": gpu["label"],
            "vram_gb": gpu["vram_gb"],
            "price_per_hour_usdc": sell_price,
            "available": is_avail,
            "source": "live" if akash_ok else "fallback",
            "maxia_markup": f"{int(_MARKUP*100)}%",
            "provider": "akash",
        }
        if gpu.get("local"):
            tier["local"] = True
            tier["available"] = False
        tiers.append(tier)

    return {
        "gpu_count": len(tiers),
        "tiers": tiers,
        "provider": "akash",
        "network": "Akash Network (decentralized)",
        "markup": f"{int(_MARKUP*100)}%",
        "note": "Cheaper than RunPod, AWS, and Lambda Labs",
        "updated_at": _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime()),
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

    # Agent permissions — scope + spend
    try:
        from agent_permissions import check_agent_scope, check_agent_spend
        wallet = agent.get("wallet", "")
        await check_agent_scope(x_api_key, wallet, "gpu:rent")
        hours = float(req.get("hours", 1))
        # Estimate cost from GPU tier
        from config import GPU_TIERS
        tier_id = req.get("gpu_tier_id", req.get("gpu", ""))
        tier_info = next((t for t in GPU_TIERS if t["id"] == tier_id), None)
        if tier_info:
            est_cost = tier_info["base_price_per_hour"] * hours
            await check_agent_spend(x_api_key, wallet, est_cost)
    except HTTPException:
        raise
    except Exception:
        pass

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

    # Commission MAXIA (based on rental total cost)
    commission_bps = get_commission_bps(total_price)
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
        logger.error("GPU payment verification error: %s", e)
        raise HTTPException(400, "Payment verification failed")

    # Provisionner le GPU via RunPod
    from runpod_client import RunPodClient
    from config import RUNPOD_API_KEY
    runpod = RunPodClient(api_key=RUNPOD_API_KEY)
    instance = await runpod.rent_gpu(tier_id, hours)

    if not instance.get("success"):
        # Fix #21: Don't leak internal RunPod error details
        logger.error("RunPod provisioning error: %s", instance.get("error", "indisponible"))
        raise HTTPException(502, "GPU provisioning temporarily unavailable")

    # Isolation multi-tenant
    from tenant_isolation import get_current_tenant
    _tenant_id = get_current_tenant() or "default"

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
        "tenant_id": _tenant_id,
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

    logger.info("GPU loue: %s x%sh par %s — %s USDC", gpu["label"], hours, agent["name"], total_with_commission)

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

    # Agent permissions — scope + spend
    try:
        from agent_permissions import check_agent_scope, check_agent_spend
        wallet = agent.get("wallet", "")
        await check_agent_scope(x_api_key, wallet, "stocks:trade")
        amount_usd = float(req.get("amount_usdc", 0))
        if amount_usd > 0:
            await check_agent_spend(x_api_key, wallet, amount_usd)
    except HTTPException:
        raise
    except Exception:
        pass

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
    """Statistiques des actions tokenisees on-chain. Sans auth."""
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
async def crypto_quote(request: Request, from_token: str, to_token: str, amount: float, volume_30d: float = 0, wallet: str = ""):
    """Devis de swap avec commission MAXIA. Sans auth.
    Si wallet fourni, le volume 30 jours est calcule automatiquement pour le tier."""
    from crypto_swap import get_swap_quote
    # Get user 30-day swap volume and swap count if wallet provided
    user_volume = volume_30d
    swap_count = -1
    if wallet:
        try:
            from database import db
            if user_volume <= 0:
                user_volume = await db.get_swap_volume_30d(wallet)
            swap_count = await db.get_swap_count(wallet)
        except Exception:
            user_volume = 0
            swap_count = -1
    result = await get_swap_quote(from_token, to_token, amount, user_volume, swap_count)
    # Fix #7: Add cache-control info to quote response
    if isinstance(result, dict) and "error" not in result:
        result["cache_ttl_seconds"] = 30
        result["note"] = "Quote valid for 30 seconds"
    return result


@router.get("/crypto/swap-quote")
async def crypto_swap_quote(request: Request, from_token: str, to_token: str, amount: float, volume_30d: float = 0, wallet: str = ""):
    """Alias de /crypto/quote pour compatibilite."""
    return await crypto_quote(request, from_token, to_token, amount, volume_30d, wallet)


@router.post("/crypto/swap")
async def crypto_swap(request: Request, req: dict, x_api_key: str = Header(None, alias="X-API-Key")):
    """Executer un swap crypto. Requiert API key."""
    if not x_api_key:
        raise HTTPException(401, "Header X-API-Key requis")
    client_ip = request.client.host if request.client else ""
    agent = _get_agent(x_api_key, client_ip=client_ip)
    _check_rate(x_api_key)

    # Agent permissions — scope + spend
    try:
        from agent_permissions import check_agent_scope, check_agent_spend
        wallet = agent.get("wallet", "")
        await check_agent_scope(x_api_key, wallet, "swap:execute")
        amount_usd = float(req.get("amount", 0))
        if amount_usd > 0:
            await check_agent_spend(x_api_key, wallet, amount_usd)
    except HTTPException:
        raise
    except Exception:
        pass

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

    # Get real 30-day swap volume and swap count from DB
    swap_volume_30d = 0
    swap_count = -1
    try:
        from database import db
        swap_volume_30d = await db.get_swap_volume_30d(agent["wallet"])
        swap_count = await db.get_swap_count(agent["wallet"])
    except Exception:
        swap_volume_30d = agent.get("volume_30d", 0)
        swap_count = -1

    from crypto_swap import execute_swap
    result = await execute_swap(
        buyer_api_key=x_api_key,
        buyer_name=agent["name"],
        buyer_wallet=agent["wallet"],
        from_token=req.get("from_token", ""),
        to_token=req.get("to_token", ""),
        amount=amount,
        buyer_volume_30d=swap_volume_30d,
        payment_tx=req.get("payment_tx", ""),
        swap_count=swap_count,
    )

    if result.get("success"):
        # Fix #1: Track daily volume after successful swap
        _rate_limits[daily_vol_key] = current_vol + value_usd
        async with _agent_update_lock:
            # Fix #3: Division by zero guard + Fix #19: Use dynamic commission calculation
            bps = result.get("commission_bps") or get_commission_bps(value_usd)
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
# REFERRAL PROGRAM — 50% commission share
# ══════════════════════════════════════════

REFERRAL_SHARE_PCT = 50  # referrer gets 50% of MAXIA's commission on referee's transactions

@router.get("/referral/my-code")
async def referral_my_code(x_api_key: str = Header(None, alias="X-API-Key")):
    """Get your referral code. Share it to earn 50% of referred agents' commissions."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    agent = _get_agent(x_api_key)
    # Referral code = first 8 chars of api_key after "maxia_" prefix
    code = x_api_key[6:14]
    from database import db
    rows = await db.raw_execute_fetchall(
        "SELECT COUNT(*) AS cnt FROM referrals WHERE referrer=?", (x_api_key,))
    count = rows[0]["cnt"] if rows else 0
    # Calculate earnings
    earnings = 0.0
    try:
        rows2 = await db.raw_execute_fetchall(
            "SELECT data FROM referrals WHERE referrer=?", (x_api_key,))
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
        "commission": f"{REFERRAL_SHARE_PCT}% of MAXIA's commission on referred agents' transactions",
        "how_it_works": [
            "1. Share your referral code or link",
            "2. New agent registers with your code",
            "3. You earn 50% of every commission MAXIA takes on their transactions",
            "4. Passive income forever — swaps, service buys, GPU rentals",
        ],
    }


@router.get("/referral/my-referrals")
async def referral_list(x_api_key: str = Header(None, alias="X-API-Key")):
    """List agents I referred."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    agent = _get_agent(x_api_key)
    from database import db
    rows = await db.raw_execute_fetchall(
        "SELECT data FROM referrals WHERE referrer=? ORDER BY rowid DESC", (x_api_key,))
    referrals = []
    total_earned = 0.0
    for r in rows:
        d = json.loads(r["data"])
        earned = d.get("earnedUsdc", 0)
        total_earned += earned
        referrals.append({
            "referee_name": d.get("referee_name", d.get("referee", "")[:8] + "..."),
            "registered_at": d.get("registeredAt", 0),
            "earned_usdc": round(earned, 4),
        })
    return {"referrals": referrals, "total": len(referrals),
            "total_earned_usdc": round(total_earned, 4)}


@router.get("/referral/{api_key}")
async def referral_stats(api_key: str):
    """Public referral stats for any agent. Returns referral count and total commission earned."""
    await _load_from_db()
    # Validate the api_key exists
    agent = _registered_agents.get(api_key)
    if not agent:
        raise HTTPException(404, "Agent not found")

    referral_code = api_key[6:14]
    from database import db
    # Count referrals
    rows = await db.raw_execute_fetchall(
        "SELECT COUNT(*) AS cnt FROM referrals WHERE referrer=?", (api_key,))
    count = rows[0]["cnt"] if rows else 0

    # Total commission earned
    earnings = 0.0
    try:
        rows2 = await db.raw_execute_fetchall(
            "SELECT data FROM referrals WHERE referrer=?", (api_key,))
        for r in rows2:
            d = json.loads(r["data"])
            earnings += d.get("earnedUsdc", 0)
    except Exception:
        pass

    return {
        "agent_name": agent.get("name", ""),
        "referral_code": referral_code,
        "total_referred": count,
        "total_commission_earned_usdc": round(earnings, 4),
        "commission_rate": f"{REFERRAL_SHARE_PCT}% of MAXIA's commission",
        "share_url": f"https://maxiaworld.app/api/public/register?referral_code={referral_code}",
    }


# ══════════════════════════════════════════
#  COMPLIANCE — OFAC Sanctions Check (Art.25)
# ══════════════════════════════════════════

@router.get("/compliance/check-wallet")
async def compliance_check_wallet(address: str = ""):
    """Check if a wallet address is on the OFAC sanctions list. Free, no auth needed."""
    if not address or len(address) < 20:
        raise HTTPException(400, "address query parameter required (min 20 chars)")
    result = check_ofac_wallet(address)
    return {
        "address": address,
        "sanctioned": result["sanctioned"],
        "risk_level": result["risk"],
        "provider": "MAXIA built-in (OFAC SDN subset + Tornado Cash + Lazarus Group)",
        "note": "For full coverage, integrate Chainalysis or TRM Labs API.",
    }


# ══════════════════════════════════════════
#  RATE LIMIT TIERS INFO
# ══════════════════════════════════════════

@router.get("/rate-limits")
async def rate_limits_info(x_api_key: str = Header(None, alias="X-API-Key")):
    """Check your current rate limit tier and usage."""
    from security import RATE_LIMIT_TIERS, get_agent_rate_tier
    if not x_api_key:
        return {
            "current_tier": "free",
            "tiers": RATE_LIMIT_TIERS,
            "note": "Register with POST /api/public/register to get an API key.",
        }
    await _load_from_db()
    try:
        _get_agent(x_api_key)
    except Exception:
        return {"current_tier": "free", "tiers": RATE_LIMIT_TIERS}

    tier_name = await get_agent_rate_tier(x_api_key)
    usage = await check_rate_limit_tiered(x_api_key)

    return {
        "current_tier": tier_name,
        "tier_details": RATE_LIMIT_TIERS[tier_name],
        "usage": {
            "remaining_per_min": usage.get("remaining_min", "?"),
            "remaining_per_day": usage.get("remaining_day", "?"),
        },
        "all_tiers": RATE_LIMIT_TIERS,
        "upgrade": "Contact team@maxiaworld.app for Pro/Enterprise access.",
    }


# ══════════════════════════════════════════
#  AGENT BUNDLE — 1 API call to get everything
# ══════════════════════════════════════════

@router.post("/agents/bundle")
async def agent_bundle(request: Request):
    """Register an AI agent and get EVERYTHING in one API call.
    Returns: API key, wallet setup, MCP tools access, A2A endpoint,
    marketplace listing, leaderboard entry, referral code.

    This is the fastest way to go from zero to live on MAXIA.
    One POST, one response, your agent is ready to trade.
    """
    body = await request.json()
    wallet = body.get("wallet", "").strip()[:50] if isinstance(body.get("wallet"), str) else ""
    name = body.get("name", "").strip()[:100] if isinstance(body.get("name"), str) else ""
    description = body.get("description", "").strip()[:2000] if isinstance(body.get("description"), str) else ""

    if not wallet or not name:
        raise HTTPException(400, "wallet and name required")

    if len(name) < 2:
        raise HTTPException(400, "name must be at least 2 characters")
    if len(wallet) < 20:
        raise HTTPException(400, "wallet address too short")

    # Art.1 — Content safety on all inputs
    _check_safety(name, "name")
    if description:
        _check_safety(description, "description")

    # OFAC sanctions check
    require_ofac_clear(wallet, "bundle registration wallet")

    await _load_from_db()

    from database import db

    # 1. Register agent (generate secure API key)
    api_key = f"maxia_{secrets.token_hex(24)}"
    agent = {
        "api_key": api_key,
        "name": name,
        "wallet": wallet,
        "description": description or f"AI agent {name}",
        "registered_at": int(time.time()),
        "volume_30d": 0.0,
        "total_spent": 0.0,
        "total_earned": 0.0,
        "tier": "BRONZE",
        "requests_today": 0,
        "services_listed": 0,
    }
    _registered_agents[api_key] = agent
    try:
        await db.save_agent(agent)
    except Exception as e:
        logger.error("DB save agent error: %s", e)

    # 2. Generate referral code
    referral_code = f"ref_{wallet[:8]}_{int(time.time()) % 10000}"

    # 3. Get current tier info
    from crypto_swap import get_swap_tier_info
    tier_info = get_swap_tier_info(0)

    # 4. Build response with everything
    return {
        "success": True,
        "message": f"Welcome to MAXIA, {name}! Your agent is live.",

        # Identity
        "api_key": api_key,
        "wallet": wallet,
        "agent_name": name,
        "referral_code": referral_code,

        # Endpoints (everything your agent can do)
        "endpoints": {
            "swap_quote": "GET /api/public/crypto/quote?from=SOL&to=USDC&amount=10",
            "swap_execute": "POST /api/public/crypto/swap",
            "gpu_rent": "POST /api/public/gpu/rent",
            "gpu_tiers": "GET /api/public/gpu/tiers",
            "stocks_list": "GET /api/public/stocks",
            "stocks_buy": "POST /api/public/stocks/buy",
            "defi_yields": "GET /api/public/defi/best-yield?asset=USDC",
            "discover_services": "GET /api/public/discover",
            "register_service": "POST /api/public/services/register",
            "execute_service": "POST /api/public/services/{id}/execute",
            "credit_score": f"GET /api/public/credit-score/{wallet}",
            "mcp_tools": "GET /mcp/tools",
            "a2a_card": "GET /.well-known/agent.json",
            "prices": "GET /api/public/prices",
        },

        # Your current tier
        "tier": tier_info,

        # Commissions (first swap is FREE)
        "first_swap_free": True,
        "commission_tiers": {
            "BRONZE": "0.10%",
            "SILVER": "0.05%",
            "GOLD": "0.03%",
            "WHALE": "0.01%",
        },

        # Quick start code
        "quickstart_python": f'''import httpx
# Your agent is live! Try a swap:
r = httpx.get("https://maxiaworld.app/api/public/crypto/quote",
    params={{"from": "SOL", "to": "USDC", "amount": 10, "wallet": "{wallet}"}})
print(r.json())
''',

        # What's included (free)
        "included": [
            "107 tokens on 7 swap chains",
            "25 tokenized stocks (Pyth real-time)",
            "13 GPU tiers at cost (0% markup)",
            "DeFi yield scanner (14 chains)",
            "46 MCP tools",
            "A2A Protocol support",
            "On-chain escrow with AI disputes",
            "Agent leaderboard (AAA-CCC grades)",
            "Referral program (10% lifetime)",
            "First swap FREE (0% commission)",
        ],
    }
