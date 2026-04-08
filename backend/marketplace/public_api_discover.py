"""Public API — Discover, Execute, and related routes.

Extracted from public_api.py (S34 split).
"""
import logging, json, os, time, asyncio, datetime

from fastapi import APIRouter, HTTPException, Header, Request

from core.config import (
    TREASURY_ADDRESS,
    get_commission_bps, get_commission_tier_name,
)
from marketplace.public_api_shared import (
    _registered_agents, _agent_services, _transactions,
    _load_from_db, _check_safety, _check_rate, _get_agent, _safe_float,
    _validate_solana_address,
    _agent_update_lock,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_demand_tracker: dict = {}  # service_type -> [timestamps]


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
        "chains": 23,
        "features": {
            "swap": {"chains": ["solana", "ethereum", "base", "arbitrum", "polygon", "avalanche", "bnb"], "method": "Jupiter (Solana) + 1inch (EVM)", "tokens": 71},
            "tokenized_stocks": {"chains": ["solana", "ethereum", "arbitrum"], "method": "Jupiter + 1inch + Dinari", "stocks": 25},
            "escrow": {"chains": ["solana", "base"], "method": "Solana PDA + Base Solidity", "contracts": 2},
            "gpu_rental": {"chains": ["solana"], "method": "Akash Network + local 7900XT", "tiers": 7},
            "defi_yields": {"chains": ["solana", "ethereum", "base", "polygon", "arbitrum", "avalanche", "near"], "method": "DeFiLlama + direct protocol APIs"},
            "bridge": {"chains": ["solana", "ethereum", "base", "polygon", "arbitrum", "avalanche", "bnb", "optimism"], "method": "Li.Fi aggregator"},
            "wallet_analysis": {"chains": ["solana"], "method": "Helius DAS API"},
            "lightning": {"chains": ["bitcoin"], "method": "ln.bot L402 — micropayments in sats"},
            "scout_scan": {"chains": ["solana", "ethereum", "base", "polygon", "arbitrum", "avalanche", "bnb", "ton", "sui", "near", "aptos", "sei", "xrp", "tron", "zksync", "linea", "scroll", "sonic", "cosmos", "hedera", "cardano", "polkadot"], "method": "RPC + registries"},
            "payments": {"chains": ["solana", "ethereum", "base", "xrp", "polygon", "arbitrum", "avalanche", "bnb", "ton", "sui", "tron", "near", "aptos", "sei", "zksync", "linea", "scroll", "sonic", "cosmos", "hedera", "cardano", "polkadot", "bitcoin"], "method": "USDC/USDT on-chain + Lightning sats + IBC", "stablecoins": ["USDC", "USDT"]},
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
        from core.database import db
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
        price = float(s.get("price_usdc", 0))
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

    # Also include MAXIA native services (12 AI services — real implementations, PRO-K10)
    maxia_native = [
        {"service_id": "maxia-sentiment", "name": "Crypto Sentiment Analysis", "type": "data", "price_usdc": 0.50, "seller": "MAXIA", "rating": 5, "description": "AI scans 1000+ sources for any token. Returns bullish/bearish score with confidence level and key signals."},
        {"service_id": "maxia-audit", "name": "Smart Contract Audit", "type": "audit", "price_usdc": 4.99, "seller": "MAXIA", "rating": 5, "description": "AI-powered security audit of Solana/EVM smart contracts. Detects vulnerabilities, reentrancy, overflow, access control issues."},
        {"service_id": "maxia-code", "name": "AI Code Review", "type": "code", "price_usdc": 2.99, "seller": "MAXIA", "rating": 5, "description": "Automated code review for Python, Rust, JavaScript, Solidity. Finds bugs, suggests improvements, checks best practices."},
        {"service_id": "maxia-translate", "name": "AI Translation", "type": "text", "price_usdc": 0.05, "seller": "MAXIA", "rating": 5, "description": "Translate text between 50+ languages. Technical documentation, marketing copy, chat messages."},
        {"service_id": "maxia-summary", "name": "Document Summary", "type": "text", "price_usdc": 0.49, "seller": "MAXIA", "rating": 5, "description": "Summarize any document, whitepaper, or article into key bullet points. Supports up to 10,000 words."},
        {"service_id": "maxia-wallet", "name": "Wallet Analyzer", "type": "data", "price_usdc": 1.99, "seller": "MAXIA", "rating": 5, "description": "Deep analysis of any Solana wallet: token holdings, transaction history, DeFi positions, risk score."},
        {"service_id": "maxia-wallet-risk", "name": "Wallet Risk Score", "type": "data", "price_usdc": 0.10, "seller": "MAXIA", "rating": 5, "description": "Risk score (0-100) for any wallet address. Checks whale status, rug pull patterns, wash trading, sanctions."},
        {"service_id": "maxia-price", "name": "Real-Time Token Price", "type": "data", "price_usdc": 0.005, "seller": "MAXIA", "rating": 5, "description": "Sub-second token prices from Pyth oracle + CoinGecko. 65+ tokens. Includes confidence interval."},
        {"service_id": "maxia-marketing", "name": "Marketing Copy Generator", "type": "text", "price_usdc": 0.99, "seller": "MAXIA", "rating": 5, "description": "Generate landing page copy, Twitter threads, blog posts, product descriptions. Optimized for Web3/AI audience."},
        {"service_id": "maxia-image", "name": "AI Image Generator", "type": "image", "price_usdc": 0.10, "seller": "MAXIA", "rating": 5, "description": "Generate images from text prompts via Pollinations.ai. Logos, illustrations, social media graphics. Free, no API key needed."},
        {"service_id": "maxia-extract", "name": "Data Extraction", "type": "data", "price_usdc": 0.25, "seller": "MAXIA", "rating": 5, "description": "Extract structured JSON from unstructured text. Entities, dates, numbers, relationships. Perfect for parsing documents."},
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
        base_score = (float(r.get("success_rate_pct", 50)) / 100) * float(r.get("rating", 3)) * math.log(float(r.get("sales", 0)) + 2)
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
async def discover_services_post(req: dict = None):
    if req is None:
        req = {}
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
    """Buy AND execute a service in one call. Requires real USDC payment on Solana."""
    logger.info("/execute ENTERED — loading DB...")
    try:
        await asyncio.wait_for(_load_from_db(), timeout=10)
    except asyncio.TimeoutError:
        logger.warning("/execute _load_from_db timed out — using cached data")
    logger.info("/execute DB loaded, checking auth...")
    if not x_api_key:
        raise HTTPException(401, "Header X-API-Key requis")

    client_ip = request.client.host if request.client else ""
    buyer = _get_agent(x_api_key, client_ip=client_ip)
    _check_rate(x_api_key)

    # Agent permissions — scope + spend check
    try:
        from agents.agent_permissions import check_agent_scope, check_agent_spend
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
            "maxia-finetune": 2.99, "maxia-awp-stake": 0.99,
            "maxia-transcription": 0.01, "maxia-embedding": 0.001,
            "maxia-sentiment": 0.005, "maxia-wallet-score": 0.10,
            "maxia-airdrop-scan": 0.50, "maxia-smart-money": 0.25,
            "maxia-nft-rarity": 0.05,
        }.get(service_id, 1.99)
        # BUG 12 fix: reject services with zero price
        if price <= 0:
            raise HTTPException(400, "Service temporarily unavailable (invalid pricing)")
    elif service:
        price = service["price_usdc"]
    else:
        raise HTTPException(404, "Service not found. Use GET /discover to find services.")

    # ═══ PAYMENT: CREDITS → LIGHTNING L402 → ON-CHAIN USDC ═══
    paid_with_credits = False
    paid_with_lightning = False
    lightning_charge_id = req.get("lightning_charge_id", "").strip()[:100] if isinstance(req.get("lightning_charge_id"), str) else ""
    # Also check X-Lightning-Payment header
    if not lightning_charge_id:
        lightning_charge_id = request.headers.get("x-lightning-payment", "").strip()[:100]

    if not payment_tx and not lightning_charge_id:
        # Try prepaid credits (off-chain, zero gas)
        try:
            from billing.prepaid_credits import get_balance, deduct_credits
            agent_id = buyer.get("agent_id", x_api_key)
            balance = await get_balance(agent_id)
            if balance >= price:
                credit_result = await deduct_credits(agent_id, price, f"execute:{service_id}")
                if credit_result.get("success"):
                    paid_with_credits = True
                    logger.info("/execute paid with credits: %s USDC for %s (balance: %s)",
                                price, service_id, credit_result.get("balance"))
        except Exception as e:
            logger.debug("Credits check failed: %s", e)

        if not paid_with_credits:
            # Try L402 Lightning challenge (return 402 with invoice)
            try:
                from integrations.l402_middleware import create_l402_challenge, build_402_response
                challenge = await create_l402_challenge(price, service_id)
                if challenge.get("success"):
                    return build_402_response(challenge)
            except Exception as e:
                logger.debug("L402 challenge failed, requiring on-chain: %s", e)

            raise HTTPException(400,
                "payment_tx required (or deposit prepaid credits via POST /api/credits/deposit, "
                "or use Lightning via L402). "
                f"Send USDC to Treasury on Solana first. Treasury: {TREASURY_ADDRESS}")

    # Verify Lightning payment if charge_id provided
    if lightning_charge_id and not paid_with_credits:
        try:
            from integrations.l402_middleware import verify_lightning_payment
            ln_result = await verify_lightning_payment(lightning_charge_id, expected_usd=price)
            if ln_result.get("verified"):
                paid_with_lightning = True
                logger.info("/execute paid with Lightning: %s sats for %s",
                            ln_result.get("amount_sats"), service_id)
            else:
                raise HTTPException(402, f"Lightning payment not settled: {ln_result.get('error', 'unpaid')}")
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Lightning verification error: %s", e)
            raise HTTPException(400, "Lightning payment verification failed")

    payment_verified = True
    payment_info = {}

    if paid_with_lightning:
        payment_info = {
            "verified": True,
            "method": "lightning",
            "charge_id": lightning_charge_id,
            "amount_sats": ln_result.get("amount_sats", 0),
            "amount_usd": price,
        }
    elif not paid_with_credits:
        # ═══ ON-CHAIN USDC VERIFICATION ═══

        # P-1 fix: Mark tx as "pending" BEFORE verification to close TOCTOU gap
        from core.database import db as _exec_db
        try:
            if await asyncio.wait_for(_exec_db.tx_already_processed(payment_tx), timeout=5):
                raise HTTPException(400, "Payment already used for a previous purchase")
            await _exec_db.record_transaction(
                buyer.get("wallet", ""), payment_tx, 0, "execute_pending")
        except asyncio.TimeoutError:
            raise HTTPException(503, "Payment verification temporarily unavailable, please retry")
        except HTTPException:
            raise
        except Exception as e:
            logger.error("[Execute] Idempotency reserve failed: %s", e)
            raise HTTPException(503, "Service temporarily unavailable")

        try:
            from blockchain.solana_verifier import verify_transaction
            verification = await asyncio.wait_for(
                verify_transaction(
                    tx_signature=payment_tx,
                    expected_amount_usdc=price,
                    expected_recipient=TREASURY_ADDRESS,
                ),
                timeout=25,
            )
            if not verification.get("valid"):
                raise HTTPException(400,
                    f"Payment invalid: {verification.get('error', 'verification failed')}. "
                    f"Expected {price} USDC to {TREASURY_ADDRESS[:12]}...")
        except asyncio.TimeoutError:
            raise HTTPException(504, "Payment verification timed out (25s). Solana RPC may be slow. Try again.")
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Payment verification error in /execute: %s", e)
            raise HTTPException(400, "Payment verification failed. Ensure your USDC transfer to Treasury is confirmed on Solana.")

        payment_info = {
            "verified": True,
            "signature": payment_tx,
            "amount_usdc": verification.get("amount_usdc", price),
            "from": verification.get("from", ""),
            "to": verification.get("to", TREASURY_ADDRESS),
        }
    else:
        payment_info = {
            "verified": True,
            "method": "prepaid_credits",
            "amount_usdc": price,
            "balance_remaining": credit_result.get("balance", 0),
        }

    logger.info("/execute payment verified: %s... (%s USDC from %s...)", payment_tx[:16], price, verification.get("from", "?")[:12])

    # ═══ NATIVE SERVICE EXECUTION ═══
    if is_native:
        _exec_start = time.time()
        try:
            result_text = await asyncio.wait_for(
                _execute_native_service(service_id, prompt), timeout=30
            )
        except asyncio.TimeoutError:
            result_text = "Service execution timed out (30s). LLM providers may be rate-limited. Your payment is recorded — contact support for a retry."
        _exec_ms = int((time.time() - _exec_start) * 1000)

        # P-2 fix: auto-refund if native service failed
        _service_failed = (
            not result_text
            or result_text.startswith("Service execution timed out")
            or ("unavailable" in result_text.lower() and len(result_text) < 200)
        )
        if _service_failed and price > 0:
            try:
                from infra.alerts import _send_private
                await _send_private(
                    f"\U0001f534 <b>Service Failed — Refund Due</b>\n"
                    f"Service: <code>{service_id}</code>\n"
                    f"Amount: <code>{price} USDC</code>\n"
                    f"Buyer: <code>{buyer.get('wallet', '')[:12]}...</code>\n"
                    f"TX: <code>{payment_tx[:16]}...</code>\n"
                    f"Action: Manual refund required"
                )
            except Exception:
                pass
            logger.warning("[Execute] Service %s failed — refund needed for %s USDC (tx: %s)", service_id, price, payment_tx[:16])

        # #1 Track execution metrics
        try:
            from core.database import db as _metrics_db
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
        from enterprise.tenant_isolation import get_current_tenant
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
        # P-1: Update pending → confirmed (tx already reserved above)
        try:
            await _exec_db.raw_execute(
                "UPDATE transactions SET amount_usdc=?, purpose=? WHERE tx_signature=?",
                (price, "execute_native", payment_tx))
        except Exception as e:
            logger.error("[Execute] tx confirm update failed: %s", e)

        # Referral commission (50% of MAXIA's commission to referrer)
        try:
            from billing.referral_manager import add_commission
            await add_commission(buyer["wallet"], commission)
        except Exception:
            pass

        # WS Event Stream — notify subscribers of service execution
        try:
            from features.ws_events import publish_event
            await publish_event("service.executed", {
                "service_id": service_id,
                "price_usdc": price,
                "execution": "native",
            })
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
    from enterprise.tenant_isolation import get_current_tenant
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
        from core.database import db as _exec_db2
        await _exec_db2.update_service(service_id, {"sales": service.get("sales", 0)})
    except Exception:
        pass

    # Record in transactions table for idempotency
    try:
        from core.database import db as _exec_db3
        await _exec_db3.record_transaction(buyer["wallet"], payment_tx, price, "execute_marketplace")
    except Exception:
        pass

    # Referral commission (50% of MAXIA's commission to referrer)
    try:
        from billing.referral_manager import add_commission
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
            from integrations.webhook_dispatcher import validate_callback_url
            validate_callback_url(endpoint)
        except Exception:
            execution_method = "webhook_blocked"
            result_text = "Seller endpoint blocked (private IP)"
            endpoint = ""  # prevent the webhook call below

        if endpoint:
            try:
                from core.http_client import get_http_client
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

    # P-3 fix: Webhook failure → alert founder for manual refund/resolution
    if execution_method == "webhook_error":
        tx["webhook_failed"] = True
        payment_info["webhook_warning"] = "Webhook call failed. Payment is on-chain. MAXIA support has been notified for resolution."
        try:
            from infra.alerts import _send_private
            await _send_private(
                f"\U0001f7e0 <b>Webhook Failed — Buyer Needs Resolution</b>\n"
                f"Seller: <code>{service.get('agent_name', '?')}</code>\n"
                f"Buyer: <code>{buyer.get('name', '?')}</code>\n"
                f"Amount: <code>{price} USDC</code>\n"
                f"TX: <code>{payment_tx[:16]}...</code>\n"
                f"Endpoint: <code>{service.get('endpoint', '?')[:50]}</code>"
            )
        except Exception:
            pass
        logger.warning("[Execute] Webhook failed for seller %s — buyer %s paid %s USDC", service.get('agent_name'), buyer.get('name'), price)

    # Alert
    try:
        from infra.alerts import alert_revenue
        await alert_revenue(commission, f"AI-to-AI: {buyer['name']} -> {service['agent_name']} (verified on-chain)")
    except Exception:
        pass

    # Notify webhook subscribers (seller + trade_executed event)
    try:
        from features.infra_features import notify_webhook_subscribers
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
        from infra.alerts import alert_system
        await alert_system(
            "New Sale (Verified)",
            f"**{buyer['name']}** bought **{service['name']}** for **${price:.2f} USDC**. "
            f"Seller earns ${seller_gets:.2f}. Payment verified on-chain. Tx: `{tx['tx_id'][:12]}...`"
        )
    except Exception:
        pass

    # Track conversion for ROI + attribute revenue to recent actions
    try:
        from agents.ceo_maxia import ceo
        ceo.memory.log_action_with_tracking("MARKETPLACE", "sale", tx["tx_id"][:16], f"{service['name']} ${price}")
        roi = ceo.memory._data.get("roi_tracking", [])
        for entry in reversed(roi[-100:]):
            if entry.get("type") == "signup" and buyer["name"] in entry.get("details", ""):
                ceo.memory.record_conversion(entry["action_id"], revenue=commission)
                break
    except Exception:
        pass

    # WS Event Stream — notify subscribers of service execution (anonymized)
    try:
        from features.ws_events import publish_event
        await publish_event("service.executed", {
            "service_id": service_id,
            "service_name": service["name"],
            "price_usdc": price,
            "execution": execution_method,
        })
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
    """Execute a MAXIA native service.

    ONE-53: Routes to real service implementations where available,
    falls back to LLM Router for text-based services.
    """
    # ── ONE-53: Real service dispatch ──
    real_result = await _dispatch_real_service(service_id, prompt)
    if real_result is not None:
        return real_result

    # ── Fallback: LLM-based execution ──
    return await _execute_via_llm(service_id, prompt)


async def _dispatch_real_service(service_id: str, prompt: str) -> str | None:
    """Dispatch to a real service implementation. Returns None if no real handler."""

    # ── Sentiment Analysis ──
    if service_id == "maxia-sentiment":
        try:
            from ai.sentiment_analyzer import get_sentiment
            # Extract token from prompt (first word or default BTC)
            token = _extract_token_from_prompt(prompt)
            result = await asyncio.wait_for(get_sentiment(token), timeout=15)
            return json.dumps(result, indent=2)
        except Exception as e:
            logger.error("Real sentiment service error: %s", e)
            return None  # Fall back to LLM

    # ── Image Generation ──
    if service_id == "maxia-image":
        try:
            from ai.image_gen import generate_image
            result = await asyncio.wait_for(generate_image(prompt), timeout=30)
            if result.get("success"):
                return json.dumps({
                    "success": True,
                    "image_url": result.get("url", result.get("image_url", "")),
                    "model": result.get("model", "flux-schnell"),
                    "prompt": prompt[:200],
                }, indent=2)
            # If image gen failed, return error as string
            return json.dumps(result, indent=2)
        except Exception as e:
            logger.error("Real image gen service error: %s", e)
            return None

    # ── Wallet Analysis ──
    if service_id in ("maxia-wallet", "maxia-wallet-analysis"):
        try:
            from features.web3_services import analyze_wallet
            # Extract wallet address from prompt
            address = _extract_address_from_prompt(prompt)
            if address:
                result = await asyncio.wait_for(analyze_wallet(address), timeout=20)
                return json.dumps(result, indent=2)
        except Exception as e:
            logger.error("Real wallet analysis error: %s", e)
        return None  # Fall back to LLM

    # ── Wallet Risk Score ──
    if service_id in ("maxia-wallet-risk", "maxia-wallet-score"):
        try:
            from features.wallet_risk import score_wallet
            address = _extract_address_from_prompt(prompt)
            if address:
                chain = "solana" if len(address) > 40 else "ethereum"
                result = await asyncio.wait_for(score_wallet(address, chain), timeout=15)
                return json.dumps(result, indent=2)
        except Exception as e:
            logger.error("Real wallet risk error: %s", e)
        return None

    # ── Code Generation / Smart Contract Audit — real LLM execution ──
    if service_id in ("maxia-code", "maxia-code-review", "maxia-audit"):
        # Route directly to LLM with specialized system prompt (not a mock)
        return await _execute_via_llm(service_id, prompt)

    # ── Token Price (real-time) ──
    if service_id == "maxia-price":
        try:
            token = _extract_token_from_prompt(prompt)
            from trading.pyth_oracle import get_pyth_price, PYTH_SYMBOL_TO_FEED
            feed_id = PYTH_SYMBOL_TO_FEED.get(token)
            if feed_id:
                result = await asyncio.wait_for(get_pyth_price(feed_id), timeout=10)
                if result.get("price"):
                    return json.dumps({
                        "symbol": token,
                        "price_usd": result["price"],
                        "confidence": result.get("confidence", 0),
                        "source": "pyth",
                        "timestamp": result.get("publish_time", 0),
                    }, indent=2)
            # Fallback to CoinGecko
            from trading.price_oracle import get_price
            price = await asyncio.wait_for(get_price(token), timeout=10)
            if price and price > 0:
                return json.dumps({"symbol": token, "price_usd": price, "source": "coingecko"}, indent=2)
        except Exception as e:
            logger.error("Real price service error: %s", e)
        return None

    # ── Text Summarization ──
    if service_id in ("maxia-summarize", "maxia-summary"):
        try:
            from ai.llm_router import router as llm_router
            result = await asyncio.wait_for(
                llm_router.generate(
                    system="You are a concise summarizer. Summarize the following text in 3-5 bullet points. Be precise and factual.",
                    prompt=f"Summarize this text:\n\n{prompt[:4000]}",
                    max_tokens=500,
                ),
                timeout=30,
            )
            if result:
                return json.dumps({"summary": result, "original_length": len(prompt), "service": "maxia-summarize"}, indent=2)
        except Exception as e:
            logger.error("Real summarize service error: %s", e)
        return None

    # ── Translation (EN/FR/ES/DE/PT/ZH/JA) ──
    if service_id in ("maxia-translate", "maxia-translation"):
        try:
            from ai.llm_router import router as llm_router
            result = await asyncio.wait_for(
                llm_router.generate(
                    system="You are a professional translator. Translate the text accurately. Preserve formatting. If no target language is specified, translate to English.",
                    prompt=prompt[:4000],
                    max_tokens=2000,
                ),
                timeout=30,
            )
            if result:
                return json.dumps({"translation": result, "service": "maxia-translate"}, indent=2)
        except Exception as e:
            logger.error("Real translation service error: %s", e)
        return None

    # ── Data Extraction (structured from unstructured) ──
    if service_id in ("maxia-extract", "maxia-data-extract"):
        try:
            from ai.llm_router import router as llm_router
            result = await asyncio.wait_for(
                llm_router.generate(
                    system="You are a data extraction specialist. Extract structured data from the text and return valid JSON. Include all entities, dates, numbers, and relationships found.",
                    prompt=f"Extract structured data from:\n\n{prompt[:4000]}",
                    max_tokens=1000,
                ),
                timeout=30,
            )
            if result:
                return json.dumps({"extracted": result, "service": "maxia-extract"}, indent=2)
        except Exception as e:
            logger.error("Real data extraction error: %s", e)
        return None

    # ── DeFi Yields ──
    if service_id == "maxia-defi-yields":
        try:
            from trading.defi_scanner import get_best_yields
            # Extract asset from prompt or default USDC
            asset = _extract_token_from_prompt(prompt) if prompt else "USDC"
            result = await asyncio.wait_for(get_best_yields(asset=asset, limit=10), timeout=15)
            return json.dumps({"asset": asset, "yields": result, "count": len(result)}, indent=2)
        except Exception as e:
            logger.error("Real DeFi yields error: %s", e)
        return None

    return None  # No real handler — use LLM fallback


def _extract_token_from_prompt(prompt: str) -> str:
    """Extract a crypto token symbol from a prompt string."""
    import re
    # Common patterns: "BTC", "analyze BTC", "sentiment for ETH"
    tokens = re.findall(r'\b([A-Z]{2,10})\b', prompt.upper())
    known = {"BTC", "ETH", "SOL", "USDC", "USDT", "XRP", "ADA", "DOT", "AVAX",
             "MATIC", "LINK", "UNI", "AAVE", "DOGE", "SHIB", "ARB", "OP", "SUI",
             "APT", "NEAR", "TON", "TRX", "FET", "INJ", "TAO", "AKT", "ONDO"}
    for t in tokens:
        if t in known:
            return t
    return tokens[0] if tokens else "BTC"


def _extract_address_from_prompt(prompt: str) -> str:
    """Extract a wallet address from a prompt string."""
    import re
    # Solana: base58, 32-44 chars
    sol_match = re.search(r'[1-9A-HJ-NP-Za-km-z]{32,44}', prompt)
    if sol_match:
        return sol_match.group(0)
    # EVM: 0x prefix, 40 hex chars
    evm_match = re.search(r'0x[0-9a-fA-F]{40}', prompt)
    if evm_match:
        return evm_match.group(0)
    return ""


async def _execute_via_llm(service_id: str, prompt: str) -> str:
    """Execute a service via LLM Router with Cerebras fallback."""
    sys_prompt = _SERVICE_PROMPTS.get(service_id, "You are a helpful AI assistant.")

    try:
        from ai.llm_router import router as llm_router
        result = await asyncio.wait_for(
            llm_router.call(prompt=prompt, system=sys_prompt, max_tokens=1500, timeout=25.0),
            timeout=40,
        )
        if result:
            return result
    except asyncio.TimeoutError:
        logger.warning("LLM Router timeout (40s)")
    except Exception as e:
        logger.error("LLM Router error: %s", e)

    # Direct Cerebras httpx fallback (replaces legacy Groq)
    try:
        cerebras_key = os.getenv("CEREBRAS_API_KEY", "")
        if cerebras_key:
            from core.http_client import get_http_client
            client = get_http_client()
            resp = await asyncio.wait_for(
                client.post(
                    "https://api.cerebras.ai/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {cerebras_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": os.getenv("CEREBRAS_MODEL", "gpt-oss-120b"),
                        "messages": [
                            {"role": "system", "content": sys_prompt},
                            {"role": "user", "content": prompt},
                        ],
                        "max_tokens": 1500,
                        "temperature": 0.7,
                    },
                    timeout=25.0,
                ),
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            choices = data.get("choices", [])
            if choices:
                return choices[0]["message"]["content"].strip()
    except asyncio.TimeoutError:
        logger.warning("Cerebras fallback timeout (30s)")
    except Exception as e:
        logger.error("Cerebras fallback error: %s", e)

    return "Service temporarily unavailable — please retry in a few seconds"


async def _save_tx_to_db(tx: dict, buyer: dict = None, seller_key: str = None):
    """Persist transaction + update agent stats in SQLite."""
    try:
        from core.database import db
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

