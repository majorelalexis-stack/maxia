"""MAXIA Art.22 V11 — API Publique pour Agents IA (Buy/Sell Services)

Permet aux IA externes de :
- Decouvrir les services MAXIA (sans auth)
- S'inscrire gratuitement (recevoir une API key)
- Acheter des services et payer en USDC
- Vendre leurs propres services
- MAXIA prend sa commission automatiquement

Securite Art.1 : filtrage anti-abus sur TOUS les contenus
"""
import uuid, time, hashlib, secrets, asyncio
from fastapi import APIRouter, HTTPException, Header
from config import (
    TREASURY_ADDRESS, GROQ_API_KEY, GROQ_MODEL,
    get_commission_bps, BLOCKED_WORDS, BLOCKED_PATTERNS,
)
from security import check_content_safety

router = APIRouter(prefix="/api/public", tags=["public-api"])

# ── Stockage en memoire (en prod: base de donnees) ──
_registered_agents: dict = {}   # api_key -> agent info
_agent_services: list = []      # services listes par des IA externes
_transactions: list = []        # historique des transactions
_db_loaded: bool = False


async def _load_from_db():
    """Load agents and services from SQLite on first access."""
    global _db_loaded
    if _db_loaded:
        return
    _db_loaded = True
    try:
        from database import db
        agents = await db.get_all_agents()
        for a in agents:
            _registered_agents[a["api_key"]] = {
                "api_key": a["api_key"], "name": a["name"], "wallet": a["wallet"],
                "description": a.get("description", ""), "tier": a.get("tier", "BRONZE"),
                "volume_30d": a.get("volume_30d", 0), "total_spent": a.get("total_spent", 0),
                "total_earned": a.get("total_earned", 0), "services_listed": a.get("services_listed", 0),
                "requests_today": 0, "registered_at": a.get("created_at", 0),
            }
        services = await db.get_services()
        for s in services:
            _agent_services.append(dict(s))
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


# ══════════════════════════════════════════
#  SECURITE ART.1 — Filtrage anti-abus
# ══════════════════════════════════════════

def _check_safety(text: str, field: str = "content"):
    """Filtrage anti-pedopornographie et contenu illegal sur TOUT."""
    check_content_safety(text, field)


def _check_rate(api_key: str):
    """Rate limit par API key."""
    today = time.strftime("%Y-%m-%d")
    key = f"{api_key}:{today}"
    _rate_limits.setdefault(key, 0)
    _rate_limits[key] += 1
    if _rate_limits[key] > RATE_LIMIT_FREE:
        raise HTTPException(429, "Limite quotidienne atteinte (100 req/jour). Passez au forfait Pro.")


def _get_agent(api_key: str) -> dict:
    """Recupere l'agent depuis sa cle API."""
    agent = _registered_agents.get(api_key)
    if not agent:
        raise HTTPException(401, "API key invalide. Inscrivez-vous sur /api/public/register")
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
        "version": "12.0.0",
        "description": "API ouverte pour agents IA. Achetez et vendez des services IA avec USDC sur Solana.",
        "base_url": "https://maxiaworld.app/api/public",
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
            "POST /register": "Free registration → API key",
            "POST /buy": "Buy a MAXIA native service (API key)",
            "POST /sell": "List YOUR service for sale (API key)",
            "POST /buy-from-agent": "Buy from another AI agent (API key)",
            "POST /execute": "Buy AND execute in one call — webhook auto-call (API key)",
            "GET /my-stats": "Your stats (API key)",
            "GET /my-earnings": "Your seller earnings (API key)",
            "GET /marketplace-stats": "Global marketplace stats (no auth)",
        },
        "example_buy": {
            "method": "POST",
            "url": "/api/public/buy",
            "headers": {"X-API-Key": "votre_cle_api"},
            "body": {
                "service_type": "code",
                "prompt": "Write a Solana token transfer function in Rust",
                "payment_tx": "signature_transaction_usdc",
            },
        },
        "commission": "Dynamique: 5% (Bronze) → 1% (Or) → 0.1% (Baleine). Plus vous utilisez, moins vous payez.",
        "security": "Art.1 — Tout contenu illegal, pedopornographique, terroriste ou frauduleux est automatiquement bloque et signale.",
    }


# ══════════════════════════════════════════
#  INSCRIPTION (gratuite)
# ══════════════════════════════════════════

@router.post("/register")
async def register_agent(req: dict):
    """Inscription gratuite pour les IA. Retourne une API key. Persiste dans SQLite."""
    await _load_from_db()
    name = req.get("name", "").strip()
    wallet = req.get("wallet", "").strip()
    description = req.get("description", "")

    if not name or len(name) < 2:
        raise HTTPException(400, "Nom requis (min 2 caracteres)")
    if not wallet or len(wallet) < 20:
        raise HTTPException(400, "Adresse wallet Solana requise")

    # Art.1 — Filtrage anti-abus sur le nom et la description
    _check_safety(name, "nom")
    if description:
        _check_safety(description, "description")

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

    # Alerte Discord
    try:
        from alerts import alert_new_client
        await alert_new_client(wallet, f"Agent IA: {name}", 0)
    except Exception:
        pass

    print(f"[PublicAPI] Nouvel agent inscrit: {name} ({wallet[:8]}...)")

    return {
        "success": True,
        "api_key": api_key,
        "name": name,
        "tier": "BRONZE",
        "rate_limit": f"{RATE_LIMIT_FREE} requetes/jour",
        "message": "Bienvenue sur MAXIA. Utilisez X-API-Key dans vos headers pour acceder aux services.",
    }


# ══════════════════════════════════════════
#  ACHETER UN SERVICE
# ══════════════════════════════════════════

@router.post("/buy")
async def buy_service(req: dict, x_api_key: str = Header(None, alias="X-API-Key")):
    """Acheter un service MAXIA. L'IA envoie un prompt et recoit le resultat."""
    if not x_api_key:
        raise HTTPException(401, "Header X-API-Key requis")

    agent = _get_agent(x_api_key)
    _check_rate(x_api_key)

    service_type = req.get("service_type", "text")
    prompt = req.get("prompt", "")
    payment_tx = req.get("payment_tx", "")

    if not prompt:
        raise HTTPException(400, "Prompt requis")

    # Art.1 — Filtrage STRICT anti-pedopornographie et contenu illegal
    _check_safety(prompt, "prompt")

    # Determiner le prix
    prices = {
        "audit": 9.99, "data": 2.99, "code": 3.99,
        "text": 0.19, "audit_deep": 49.99,
    }
    price = prices.get(service_type, 1.99)

    # Calculer la commission
    volume = agent.get("volume_30d", 0)
    commission_bps = get_commission_bps(volume)
    commission = price * commission_bps / 10000
    seller_gets = price - commission

    # Executer le service via Groq
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
        raise HTTPException(502, f"Erreur IA: {e}")

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
        "timestamp": int(time.time()),
    }
    _transactions.append(tx)

    # Mettre a jour les stats de l'agent
    agent["volume_30d"] += price
    agent["total_spent"] += price
    agent["tier"] = _get_tier_name(agent["volume_30d"])

    # Alerte Discord
    try:
        from alerts import alert_revenue
        await alert_revenue(commission, f"API publique — {agent['name']}")
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
        "your_tier": agent["tier"],
        "your_volume_30d": agent["volume_30d"],
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

    name = req.get("name", "").strip()
    description = req.get("description", "").strip()
    service_type = req.get("type", "text")
    price_usdc = float(req.get("price_usdc", 0))
    endpoint = req.get("endpoint", "")

    if not name or not description:
        raise HTTPException(400, "Nom et description requis")
    if price_usdc <= 0 or price_usdc > 10000:
        raise HTTPException(400, "Prix entre 0.01 et 10000 USDC")

    # Art.1 — Filtrage STRICT
    _check_safety(name, "nom du service")
    _check_safety(description, "description du service")

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
        "commission": "Dynamique (5% Bronze → 1% Or → 0.1% Baleine)",
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

    service_id = req.get("service_id", "")
    proposed_price = float(req.get("proposed_price", 0))
    message = req.get("message", "")

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
async def buy_external_service(req: dict, x_api_key: str = Header(None, alias="X-API-Key")):
    """Acheter un service d'une autre IA. MAXIA prend sa commission."""
    await _load_from_db()
    if not x_api_key:
        raise HTTPException(401, "Header X-API-Key requis")

    buyer = _get_agent(x_api_key)
    _check_rate(x_api_key)

    service_id = req.get("service_id", "")
    prompt = req.get("prompt", "")

    if not prompt:
        raise HTTPException(400, "Prompt requis")

    # Art.1 — Filtrage
    _check_safety(prompt, "prompt")

    # Trouver le service
    service = None
    for s in _agent_services:
        if s["id"] == service_id and s["status"] == "active":
            service = s
            break
    if not service:
        raise HTTPException(404, "Service introuvable")

    price = service["price_usdc"]

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
        "timestamp": int(time.time()),
    }
    _transactions.append(tx)

    buyer["volume_30d"] += price
    buyer["total_spent"] += price
    buyer["tier"] = _get_tier_name(buyer["volume_30d"])

    # Crediter le vendeur
    seller_key = service.get("agent_api_key")
    seller = _registered_agents.get(seller_key)
    if seller:
        seller["total_earned"] += seller_gets
        seller["volume_30d"] += seller_gets

    service["sales"] += 1

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
        "message": f"Envoyez {price} USDC au wallet du vendeur. MAXIA a preleve {commission:.2f} USDC de commission.",
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
    }


@router.post("/discover")
async def discover_services_post(req: dict = {}):
    """POST version of discover for agent-to-agent compatibility."""
    return await discover_services(
        capability=req.get("capability", ""),
        max_price=float(req.get("max_price", 9999)),
        min_rating=float(req.get("min_rating", 0)),
        agent_type=req.get("agent_type", ""),
    )


# ══════════════════════════════════════════
#  EXECUTE — Webhook-based service execution
# ══════════════════════════════════════════

@router.post("/execute")
async def execute_agent_service(req: dict, x_api_key: str = Header(None, alias="X-API-Key")):
    """Buy AND execute a service in one call.

    If the seller has a webhook endpoint, MAXIA calls it automatically
    and returns the result. Full AI-to-AI automation.

    Body: {"service_id": "xxx", "prompt": "your request", "payment_tx": "optional"}
    """
    await _load_from_db()
    if not x_api_key:
        raise HTTPException(401, "Header X-API-Key requis")

    buyer = _get_agent(x_api_key)
    _check_rate(x_api_key)

    service_id = req.get("service_id", "")
    prompt = req.get("prompt", "")
    if not prompt:
        raise HTTPException(400, "prompt required")

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
        # Execute MAXIA native service via Groq
        result_text = await _execute_native_service(service_id, prompt)
        price = {"maxia-audit": 9.99, "maxia-code": 3.99, "maxia-data": 2.99,
                 "maxia-scraper": 0.05, "maxia-image": 0.10, "maxia-translate": 0.19}.get(service_id, 1.99)
        volume = buyer.get("volume_30d", 0)
        commission_bps = get_commission_bps(volume)
        commission = price * commission_bps / 10000

        tx = {
            "tx_id": str(uuid.uuid4()), "buyer": buyer["name"],
            "seller": "MAXIA", "service": service_id,
            "price_usdc": price, "commission_usdc": commission,
            "seller_gets_usdc": price - commission, "timestamp": int(time.time()),
        }
        _transactions.append(tx)
        buyer["volume_30d"] += price
        buyer["total_spent"] += price

        return {
            "success": True, "tx_id": tx["tx_id"],
            "service": service_id, "seller": "MAXIA",
            "price_usdc": price, "commission_usdc": commission,
            "result": result_text,
            "execution": "native",
        }

    if not service:
        raise HTTPException(404, "Service not found. Use GET /discover to find services.")

    price = service["price_usdc"]
    volume = buyer.get("volume_30d", 0)
    commission_bps = get_commission_bps(volume)
    commission = price * commission_bps / 10000
    seller_gets = price - commission

    # ═══ REAL USDC PAYMENT ═══
    payment_tx = req.get("payment_tx", "")
    payment_verified = False
    payment_info = {}

    if payment_tx:
        # Buyer sent a tx signature — verify on-chain
        try:
            from solana_tx import verify_usdc_payment
            verification = await verify_usdc_payment(payment_tx, expected_amount_usdc=price,
                                                       expected_to=TREASURY_ADDRESS)
            payment_verified = verification.get("valid", False)
            payment_info = verification
            if payment_verified:
                print(f"[Marketplace] USDC payment verified: {payment_tx[:16]}... ({price} USDC)")
                # Transfer seller's share
                try:
                    from solana_tx import send_usdc_transfer
                    from config import ESCROW_PRIVKEY_B58, TREASURY_ADDRESS as TREASURY
                    seller_wallet = service.get("agent_wallet", "")
                    if seller_wallet and seller_gets > 0.001:
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
                    payment_info["seller_paid"] = False
                    payment_info["seller_error"] = str(e)
            else:
                print(f"[Marketplace] Payment NOT verified: {verification.get('error', 'unknown')}")
        except Exception as e:
            payment_info = {"error": str(e)}
    else:
        payment_info = {"note": "No payment_tx provided. Send USDC to Treasury first, then pass the tx signature."}

    # Record transaction
    tx = {
        "tx_id": str(uuid.uuid4()), "buyer": buyer["name"],
        "seller": service["agent_name"], "service": service["name"],
        "price_usdc": price, "commission_usdc": commission,
        "seller_gets_usdc": seller_gets, "timestamp": int(time.time()),
        "payment_verified": payment_verified,
    }
    _transactions.append(tx)
    await _save_tx_to_db(tx, buyer)
    buyer["volume_30d"] += price
    buyer["total_spent"] += price
    service["sales"] += 1

    # Credit seller
    seller_key = service.get("agent_api_key")
    seller = _registered_agents.get(seller_key)
    if seller:
        seller["total_earned"] += seller_gets
        seller["volume_30d"] += seller_gets

    # Execute via webhook if available
    result_text = None
    execution_method = "pending"
    endpoint = service.get("endpoint", "")

    if endpoint and endpoint.startswith("http"):
        try:
            import httpx
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(endpoint, json={
                    "prompt": prompt,
                    "buyer": buyer["name"],
                    "service_id": service_id,
                    "tx_id": tx["tx_id"],
                })
                if resp.status_code == 200:
                    result_data = resp.json()
                    result_text = result_data.get("result", result_data.get("text", str(result_data)))
                    execution_method = "webhook"
                else:
                    result_text = f"Seller webhook returned {resp.status_code}"
                    execution_method = "webhook_error"
        except Exception as e:
            result_text = f"Webhook call failed: {e}"
            execution_method = "webhook_error"
    else:
        execution_method = "manual"
        result_text = "Service purchased. Seller will deliver manually (no webhook configured)."

    # Alert
    try:
        from alerts import alert_revenue
        await alert_revenue(commission, f"AI-to-AI: {buyer['name']} -> {service['agent_name']}")
    except Exception:
        pass

    return {
        "success": True, "tx_id": tx["tx_id"],
        "service": service["name"], "seller": service["agent_name"],
        "price_usdc": price, "commission_usdc": commission,
        "seller_gets_usdc": seller_gets,
        "result": result_text,
        "execution": execution_method,
        "payment_verified": payment_verified,
        "payment": payment_info,
        "seller_wallet": service["agent_wallet"],
        "treasury_wallet": TREASURY_ADDRESS,
        "how_to_pay": f"Send {price} USDC to {TREASURY_ADDRESS} on Solana, then pass payment_tx in your request.",
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
        return f"Execution error: {e}"


async def _save_tx_to_db(tx: dict, buyer: dict = None, seller_key: str = None):
    """Persist transaction + update agent stats in SQLite."""
    try:
        from database import db
        await db.save_marketplace_tx(tx)
        if buyer:
            await db.update_agent(buyer.get("api_key", ""), {
                "volume_30d": buyer.get("volume_30d", 0),
                "total_spent": buyer.get("total_spent", 0),
            })
        if seller_key:
            seller = _registered_agents.get(seller_key)
            if seller:
                await db.update_agent(seller_key, {
                    "total_earned": seller.get("total_earned", 0),
                    "volume_30d": seller.get("volume_30d", 0),
                })
    except Exception as e:
        print(f"[PublicAPI] DB tx save error: {e}")


# ── Utilitaire ──

def _get_tier_name(volume: float) -> str:
    if volume >= 5000:
        return "BALEINE"
    if volume >= 500:
        return "OR"
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
        yields = await get_best_yields(asset, chain, min_tvl, limit)
        return {
            "asset": asset,
            "chain": chain or "all",
            "results": len(yields),
            "yields": yields,
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

    tier_id = req.get("tier", "")
    hours = float(req.get("hours", 1))
    payment_tx = req.get("payment_tx", "")

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

    # Provisionner le GPU via RunPod
    from runpod_client import RunPodClient
    from config import RUNPOD_API_KEY
    runpod = RunPodClient(api_key=RUNPOD_API_KEY)
    instance = await runpod.rent_gpu(tier_id, hours)

    if not instance.get("success"):
        raise HTTPException(502, f"RunPod provisionnement echoue: {instance.get('error', 'indisponible')}")

    # Enregistrer la transaction
    import uuid
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
        "instance_id": instance.get("instanceId", ""),
        "ssh_endpoint": instance.get("sshEndpoint", ""),
        "timestamp": int(time.time()),
    }
    _transactions.append(tx)

    # Mettre a jour les stats
    agent["volume_30d"] += total_with_commission
    agent["total_spent"] += total_with_commission
    agent["tier"] = _get_tier_name(agent["volume_30d"])

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
        try:
            from alerts import alert_system
            await alert_system("GPU Termine", f"Pod {pod_id} arrete par {agent['name']}")
        except Exception:
            pass

    return result


@router.get("/gpu/compare")
async def public_gpu_compare():
    """Compare les prix GPU MAXIA vs concurrence. Sans auth."""
    return {
        "maxia": {
            "rtx4090": {"price": "$0.69/h", "margin": "0%", "note": "Prix coutant RunPod"},
            "a100_80gb": {"price": "$1.99/h", "margin": "0%", "note": "Prix coutant RunPod"},
            "h100_sxm5": {"price": "$3.29/h", "margin": "0%", "note": "Prix coutant RunPod"},
        },
        "competitors": {
            "AWS p5 (H100)": "$32.77/h",
            "GCP a3-highgpu (H100)": "$31.22/h",
            "Azure ND H100": "$30.22/h",
            "Lambda Labs A100": "$1.29/h",
            "Vast.ai RTX4090": "$0.34-0.50/h",
            "RunPod direct RTX4090": "$0.69/h",
        },
        "maxia_advantages": [
            "0% marge sur les GPU (prix coutant RunPod)",
            "Paiement USDC sur Solana (pas de carte bancaire)",
            "API unifiee (GPU + services IA + actions)",
            "Arret automatique apres la duree louee",
            "SSH + Jupyter inclus",
        ],
    }
    return {
        "instances": my_gpus[-10:],
        "total_spent_gpu": sum(t.get("total_with_commission", 0) for t in my_gpus),
    }


@router.get("/gpu/compare")
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
            "maxia_advantage": "Prix coutant + commission dynamique (0.1% pour les gros volumes)",
            "runpod": comp.get("runpod"),
            "vast_ai": comp.get("vast_ai"),
            "lambda": comp.get("lambda"),
        })

    return {
        "comparisons": comparisons,
        "maxia_commission": "5% Bronze → 1% Or → 0.1% Baleine (basee sur votre volume 30j)",
        "unique_advantages": [
            "Paiement USDC sur Solana (pas de carte bancaire)",
            "API unifiee (GPU + services IA + data)",
            "Commission la plus basse pour les gros volumes (0.1%)",
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

    tier_id = req.get("tier", "")
    hours = float(req.get("hours", 1))

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

    symbol = req.get("symbol", "")
    amount_usdc = float(req.get("amount_usdc", 0))
    payment_tx = req.get("payment_tx", "")

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

    symbol = req.get("symbol", "")
    shares = float(req.get("shares", 0))

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
    return stock_exchange.get_portfolio(x_api_key)


@router.get("/stocks/compare-fees")
async def stock_compare_fees():
    """Compare les frais MAXIA vs concurrence. Sans auth."""
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
    from crypto_swap import fetch_prices
    prices = await fetch_prices()
    return {"prices": prices, "updated_at": int(__import__("time").time())}


@router.get("/crypto/quote")
async def crypto_quote(from_token: str, to_token: str, amount: float, volume_30d: float = 0):
    """Devis de swap avec commission MAXIA. Sans auth."""
    from crypto_swap import get_swap_quote
    return await get_swap_quote(from_token, to_token, amount, volume_30d)


@router.get("/crypto/swap-quote")
async def crypto_swap_quote(from_token: str, to_token: str, amount: float, volume_30d: float = 0):
    """Alias de /crypto/quote pour compatibilite."""
    from crypto_swap import get_swap_quote
    return await get_swap_quote(from_token, to_token, amount, volume_30d)


@router.post("/crypto/swap")
async def crypto_swap(req: dict, x_api_key: str = Header(None, alias="X-API-Key")):
    """Executer un swap crypto. Requiert API key."""
    if not x_api_key:
        raise HTTPException(401, "Header X-API-Key requis")
    agent = _get_agent(x_api_key)
    _check_rate(x_api_key)

    from crypto_swap import execute_swap
    result = await execute_swap(
        buyer_api_key=x_api_key,
        buyer_name=agent["name"],
        buyer_wallet=agent["wallet"],
        from_token=req.get("from_token", ""),
        to_token=req.get("to_token", ""),
        amount=float(req.get("amount", 0)),
        buyer_volume_30d=agent.get("volume_30d", 0),
        payment_tx=req.get("payment_tx", ""),
    )

    if result.get("success"):
        agent["volume_30d"] += result.get("commission_usd", 0) / (result.get("commission_bps", 15) / 10000)
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

    from web_scraper import scrape_url
    return await scrape_url(
        url=url,
        extract_links=req.get("extract_links", True),
        extract_images=req.get("extract_images", True),
        max_text_length=int(req.get("max_text_length", 10000)),
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

    from web_scraper import scrape_multiple
    return await scrape_multiple(urls[:5], max_text_length=int(req.get("max_text_length", 5000)))


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

    prompt = req.get("prompt", "")
    if not prompt:
        raise HTTPException(400, "Champ 'prompt' requis")

    from image_gen import generate_image as gen_img
    result = await gen_img(
        prompt=prompt,
        model=req.get("model", "flux-schnell"),
        width=int(req.get("width", 1024)),
        height=int(req.get("height", 1024)),
        steps=int(req.get("steps", 4)),
        seed=int(req.get("seed", 0)),
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

    wallet = req.get("wallet", "")
    if not wallet:
        raise HTTPException(400, "Champ 'wallet' requis")

    from wallet_monitor import add_monitor
    return await add_monitor(
        api_key=x_api_key,
        owner_name=agent["name"],
        wallet_address=wallet,
        webhook_url=req.get("webhook_url", ""),
        alert_types=req.get("alert_types", None),
        min_sol_change=float(req.get("min_sol_change", 0.1)),
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
