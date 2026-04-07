"""MAXIA Art.22 V11 — API Publique pour Agents IA (Buy/Sell Services)

Permet aux IA externes de :
- Decouvrir les services MAXIA (sans auth)
- S'inscrire gratuitement (recevoir une API key)
- Acheter des services et payer en USDC
- Vendre leurs propres services
- MAXIA prend sa commission automatiquement

Securite Art.1 : filtrage anti-abus sur TOUS les contenus

MODULES :
  public_api_shared.py    -- Shared state + helpers
  public_api_sandbox.py   -- Sandbox + Dispute routes
  public_api_discover.py  -- Discover + Execute routes
  public_api_trading.py   -- DeFi, GPU, Stocks, Crypto/Swap routes
  public_api_tools.py     -- Scrape, Image, Wallet-monitor, Referral, Compliance, Aliases
"""
import logging
import uuid, time, hashlib, secrets, asyncio, json, datetime, re

logger = logging.getLogger(__name__)
from core.error_utils import safe_error
from fastapi import APIRouter, HTTPException, Header, Request
from core.config import (
    TREASURY_ADDRESS, GROQ_API_KEY, GROQ_MODEL,
    get_commission_bps, get_commission_tier_name, BLOCKED_WORDS, BLOCKED_PATTERNS,
)
from core.security import check_content_safety, check_ofac_wallet, require_ofac_clear, check_rate_limit_tiered, check_rate_limit

from marketplace.public_api_shared import (  # noqa: F401, E402
    _registered_agents, _agent_services, _transactions,
    _load_from_db, _check_safety, _check_rate, _get_agent, _safe_float,
    _validate_solana_address, _db_loaded,
    _compute_service_hash, _check_clone, _register_service_hash, _is_original_creator,
    groq_client, cerebras_ready, _rate_limits, RATE_LIMIT_FREE, RATE_LIMITS_BY_TIER,
    _agent_update_lock, _failed_lookups,
    _service_content_hashes, _db_last_sync, _DB_SYNC_INTERVAL,
    SANDBOX_MODE, SANDBOX_PREFIX,
)

router = APIRouter(prefix="/api/public", tags=["public-api"])


# ══════════════════════════════════════════
# Include sub-routers
# ══════════════════════════════════════════
from marketplace.public_api_sandbox import router as _sandbox_router  # noqa: E402
from marketplace.public_api_discover import router as _discover_router  # noqa: E402
from marketplace.public_api_trading import router as _trading_router  # noqa: E402
from marketplace.public_api_tools import router as _tools_router  # noqa: E402

router.include_router(_sandbox_router)
router.include_router(_discover_router)
router.include_router(_trading_router)
router.include_router(_tools_router)


# ══════════════════════════════════════════
# Core routes — Register, Buy, Sell, Marketplace
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
    from core.config import GPU_TIERS, SERVICE_PRICES, COMMISSION_TIERS
    try:
        from trading.crypto_swap import SWAP_COMMISSION_TIERS
    except ImportError:
        SWAP_COMMISSION_TIERS = {}
    try:
        from trading.tokenized_stocks import STOCK_COMMISSION_TIERS
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
    await check_rate_limit(request)
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
        from core.database import db
        await db.save_agent(agent)
    except Exception as e:
        logger.error("DB save agent error: %s", e)

    # Gamification — points for registration
    try:
        from features.gamification import record_action
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
            from core.database import db as _db
            # Find the referrer agent by targeted query (not full table scan)
            row = await _db.get_agent_by_referral_code(referral_code)
            if row:
                referrer_api_key = row["api_key"]
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
                logger.info("Referral: %s referred by %s (agent %s...)", name, referral_code, referrer_api_key[:6])
            else:
                logger.info("Referral code %s not found — ignored", referral_code)
        except Exception as e:
            logger.error("Referral error: %s", e)

    # Alerte Telegram enrichie (PRO-I3)
    try:
        from infra.alerts import alert_new_client, alert_new_agent_registered
        await alert_new_client(wallet, f"Agent IA: {name}" + (f" (ref: {referral_code})" if referral_code else ""), 0)
        await alert_new_agent_registered(name, wallet, api_key)
    except Exception:
        pass

    logger.info("Nouvel agent inscrit: %s (%s...)", name, wallet[:8])

    # WS Event Stream — notify subscribers of new agent
    try:
        from features.ws_events import publish_event
        await publish_event("agent.registered", {
            "name": name,
            "wallet": wallet[:8] + "...",
            "tier": "BRONZE",
        })
    except Exception:
        pass

    # Alerte Telegram (fire-and-forget — ne bloque pas la reponse register)
    try:
        import os, httpx as _httpx
        tg_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        tg_chat = os.getenv("TELEGRAM_CHANNEL", "")
        if tg_token and tg_chat:
            _tg_name, _tg_wallet, _tg_count = name, wallet[:16], len(_registered_agents)
            async def _tg_notify():
                try:
                    async with _httpx.AsyncClient(timeout=5) as c:
                        await c.post(
                            f"https://api.telegram.org/bot{tg_token}/sendMessage",
                            json={"chat_id": tg_chat, "text": f"NEW AGENT REGISTERED!\n\nName: {_tg_name}\nWallet: {_tg_wallet}...\n\nTotal agents: {_tg_count}"})
                except Exception:
                    pass
            asyncio.create_task(_tg_notify())
    except Exception:
        pass

    # Onboarding: notify via webhook if subscriber + CEO notification
    try:
        from features.infra_features import notify_webhook_subscribers
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
        from agents.ceo_maxia import ceo
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

    # PRO-A7: Generate independent referral code (not derived from API key)
    my_referral_code = secrets.token_hex(4).upper()
    try:
        from core.database import db as _db_ref
        await _db_ref.raw_execute(
            "UPDATE agents SET referral_code=? WHERE api_key=?",
            (my_referral_code, api_key))
    except Exception as e:
        logger.warning("Failed to store referral_code: %s", e)

    # Generate DID + UAID for this agent
    agent_identity = {}
    try:
        from agents.agent_permissions import get_or_create_permissions
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
    # Art.1 — Filtrage STRICT anti-pedopornographie et contenu illegal
    _check_safety(prompt, "prompt")

    # Determiner le prix
    prices = {
        "audit": 4.99, "data": 1.99, "code": 2.99,
        "text": 0.05, "image": 0.10, "audit_deep": 49.99,
    }
    price = prices.get(service_type, 1.99)

    # ═══ PAYMENT: PREPAID CREDITS OR ON-CHAIN USDC ═══
    paid_with_credits = False
    if not payment_tx:
        # Try prepaid credits (off-chain, zero gas)
        try:
            from billing.prepaid_credits import get_balance, deduct_credits
            agent_id = agent.get("agent_id", x_api_key)
            balance = await get_balance(agent_id)
            if balance >= price:
                result = await deduct_credits(agent_id, price, f"buy:{service_type}")
                if result.get("success"):
                    paid_with_credits = True
                    logger.info("/buy paid with credits: %s USDC (balance: %s)", price, result.get("balance"))
        except Exception as e:
            logger.debug("Credits check failed, falling back to on-chain: %s", e)

        if not paid_with_credits:
            raise HTTPException(400,
                "payment_tx required (or deposit prepaid credits via POST /api/credits/deposit). "
                "Send USDC to Treasury first, then pass the Solana tx signature.")

    if not paid_with_credits:
        # ═══ ON-CHAIN USDC VERIFICATION ═══
        from core.database import db as _buy_db
        if await _buy_db.tx_already_processed(payment_tx):
            raise HTTPException(400, "Payment already used for a previous purchase")

        try:
            from blockchain.solana_verifier import verify_transaction
            tx_result = await asyncio.wait_for(verify_transaction(
                tx_signature=payment_tx,
                expected_amount_usdc=price,
                expected_recipient=TREASURY_ADDRESS,
            ), timeout=20)
            if not tx_result.get("valid"):
                raise HTTPException(400, f"Payment invalid: {tx_result.get('error', 'verification failed')}. "
                                    f"Expected {price} USDC to {TREASURY_ADDRESS[:12]}...")
        except asyncio.TimeoutError:
            raise HTTPException(504, "Payment verification timed out. Please retry.")
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

    # Executer le service (ONE-53: route to real implementations where available)
    # Map /buy service_type to maxia-* service IDs for unified dispatch
    _buy_service_map = {
        "audit": "maxia-audit",
        "code": "maxia-code",
        "data": "maxia-defi-yields",
        "text": "maxia-translate",
        "image": "maxia-image",
        "audit_deep": "maxia-audit",
    }
    mapped_service_id = _buy_service_map.get(service_type, f"maxia-{service_type}")

    try:
        from marketplace.public_api_discover import _execute_native_service
        result = await asyncio.wait_for(
            _execute_native_service(mapped_service_id, prompt),
            timeout=35,
        )
        if not result:
            raise HTTPException(502, "AI service temporarily unavailable")
    except asyncio.TimeoutError:
        logger.error("Service execution timed out (35s) for /buy %s", service_type)
        raise HTTPException(504, "Service execution timed out. Please retry.")
    except HTTPException:
        raise
    except Exception as e:
        # Fix #12: Don't leak internal error details
        logger.error("AI service error: %s", e)
        raise HTTPException(502, "AI service temporarily unavailable")

    # Isolation multi-tenant
    from enterprise.tenant_isolation import get_current_tenant
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
    except Exception as e:
        logger.error("[Execute] CRITICAL — tx record failed (idempotency risk): %s", e)

    # Mettre a jour les stats de l'agent (with lock)
    async with _agent_update_lock:
        agent["volume_30d"] += price
        agent["total_spent"] += price
        agent["tier"] = _get_tier_name(agent["volume_30d"])

    # Persist to DB
    await _save_tx_to_db(tx, agent)

    # Alerte Discord
    try:
        from infra.alerts import alert_revenue
        await alert_revenue(commission, f"API publique — {agent['name']} (verified on-chain)")
    except Exception as e:
        logger.warning("[Execute] Alert revenue failed: %s", e)

    # Referral commission (50% of MAXIA's commission to referrer)
    try:
        from billing.referral_manager import add_commission
        await add_commission(agent["wallet"], commission)
    except Exception as e:
        logger.warning("[Execute] Referral commission failed: %s", e)

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
                from core.database import db as _db
                await _db.update_service(service_id, {"rating": s["rating"], "rating_count": new_count, "sales": s.get("sales", 0)})
            except Exception:
                pass

            # Notifier le vendeur via webhook
            try:
                from features.infra_features import notify_webhook_subscribers
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
        from core.database import db
        await db.save_service(service)
        await db.update_agent(x_api_key, {"services_listed": agent["services_listed"]})
    except Exception as e:
        logger.error("DB save service error: %s", e)

    logger.info("Nouveau service: %s par %s @ %s USDC", name, agent["name"], price_usdc)

    # WS Event Stream — notify subscribers of new service listing
    try:
        from features.ws_events import publish_event
        await publish_event("service.listed", {
            "service_id": service["id"],
            "name": name,
            "type": service_type,
            "price_usdc": price_usdc,
            "seller": agent["name"],
        })
    except Exception:
        pass

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
    from core.database import db as _buy_ext_db
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
        from blockchain.solana_verifier import verify_transaction
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
    from enterprise.tenant_isolation import get_current_tenant
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
            from blockchain.solana_tx import send_usdc_transfer
            from core.config import ESCROW_PRIVKEY_B58, TREASURY_ADDRESS as TREASURY
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
            seller_payment_info["seller_error"] = "Payment processing error"
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
        from infra.alerts import alert_revenue
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
        from core.database import db
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
        from core.config import SERVICE_PRICES
        native_count = len(SERVICE_PRICES)
    except Exception:
        native_count = 17  # fallback connu

    # Live counts from config
    token_count = 0
    stock_count = 0
    mcp_count = 0
    try:
        from trading.price_oracle import TOKEN_MINTS
        stock_syms = {"AAPL","TSLA","NVDA","GOOGL","MSFT","AMZN","META","MSTR","SPY","QQQ",
                      "NFLX","AMD","PLTR","COIN","CRM","INTC","UBER","MARA","AVGO","DIA",
                      "IWM","GLD","ARKK","RIOT","SHOP","SQ","PYPL","ORCL"}
        token_count = len([s for s in TOKEN_MINTS if s not in stock_syms])
        stock_count = len([s for s in TOKEN_MINTS if s in stock_syms])
    except Exception:
        token_count = 68
        stock_count = 25
    try:
        from marketplace.mcp_server import TOOLS
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
        "swap_count": db_stats.get("swap_count", 0),
        "swap_commission_usdc": db_stats.get("swap_commission_usdc", 0),
        "commission_tiers": {
            "bronze": "1.5% (0-500 USDC)",
            "gold": "0.5% (500-5000 USDC)",
            "whale": "0.1% (5000+ USDC)",
        },
        "swap_commission_tiers": {
            "bronze": "0.10% (0-1000 USDC)",
            "silver": "0.05% (1000-5000 USDC)",
            "gold": "0.03% (5000-25000 USDC)",
            "whale": "0.01% (25000+ USDC)",
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

    capability = str(req.get("capability", "")).strip()[:200]
    max_price = _safe_float(req.get("max_price", 10), "max_price")
    description = str(req.get("description", "")).strip()[:2000]
    deadline_hours = min(168, max(1, int(_safe_float(req.get("deadline_hours", 24), "deadline_hours"))))

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

    price = _safe_float(req.get("price", 0), "price")
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

