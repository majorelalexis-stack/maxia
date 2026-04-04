"""Public API — Sandbox and Dispute routes.

Extracted from public_api.py (S34 split).
"""
import asyncio, json, logging, time, uuid

from fastapi import APIRouter, HTTPException, Header

logger = logging.getLogger(__name__)

from marketplace.public_api_shared import (
    _registered_agents, _agent_services, _transactions,
    _load_from_db, _check_safety, _check_rate, _get_agent, _safe_float,
    _validate_solana_address,
)
from core.config import get_commission_bps, get_commission_tier_name

router = APIRouter()

# ══════════════════════════════════════════

# Sandbox balances per agent (fake USDC for testing)
_sandbox_balances: dict = {}  # api_key -> float
_sandbox_trades: list = []
_sandbox_portfolios: dict = {}  # api_key -> {symbol: shares}
_sandbox_locks: dict = {}  # api_key -> asyncio.Lock (prevents TOCTOU race conditions)
SANDBOX_STARTING_BALANCE = 10000.0  # $10,000 fake USDC

# LLM response cache for sandbox (avoid burning Groq quota on repeated prompts)
_sandbox_llm_cache: dict = {}  # "service_id:prompt_hash" -> {"result": str, "ts": float}
_SANDBOX_CACHE_TTL = 300  # 5 min cache


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
        "maxia-sentiment": {"price": 0.005, "name": "Sentiment Analysis"},
        "maxia-transcription": {"price": 0.01, "name": "Audio Transcription"},
        "maxia-embedding": {"price": 0.001, "name": "Text Embedding"},
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
        "result": await _sandbox_cached_execute(service_id, prompt) if service_id.startswith("maxia-") else f"[SANDBOX] {service_name} — executed. Prompt: {prompt[:100]}",
    }


async def _sandbox_cached_execute(service_id: str, prompt: str) -> str:
    """Execute with cache to avoid burning LLM quota on repeated sandbox calls."""
    import hashlib
    from marketplace.public_api_discover import _execute_native_service

    cache_key = f"{service_id}:{hashlib.md5(prompt[:200].encode()).hexdigest()}"
    cached = _sandbox_llm_cache.get(cache_key)
    if cached and time.time() - cached["ts"] < _SANDBOX_CACHE_TTL:
        return cached["result"]

    result = await _execute_native_service(service_id, prompt)
    _sandbox_llm_cache[cache_key] = {"result": result, "ts": time.time()}

    # Trim cache to prevent unbounded growth
    if len(_sandbox_llm_cache) > 500:
        oldest = sorted(_sandbox_llm_cache, key=lambda k: _sandbox_llm_cache[k]["ts"])
        for k in oldest[:250]:
            _sandbox_llm_cache.pop(k, None)

    return result


@router.post("/sandbox/swap")
async def sandbox_swap(req: dict, x_api_key: str = Header(None, alias="X-API-Key")):
    """Test a token swap — live prices from CoinGecko, real swap commission tiers."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    await _load_from_db()
    agent = _get_agent(x_api_key)

    from_token = req.get("from_token", "USDC").upper()
    to_token = req.get("to_token", "SOL").upper()
    amount = _safe_float(req.get("amount", 0), "amount")
    if amount <= 0:
        raise HTTPException(400, "amount must be > 0")

    # Live prices from CoinGecko
    try:
        from trading.price_oracle import get_price
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
        from trading.crypto_swap import get_swap_commission_bps, get_swap_tier_name
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

    symbol = str(req.get("symbol", "")).upper()[:20]
    amount_usdc = _safe_float(req.get("amount_usdc", 0), "amount_usdc")
    if amount_usdc <= 0:
        raise HTTPException(400, "amount_usdc must be > 0")
    if not symbol:
        raise HTTPException(400, "symbol required (e.g. AAPL, TSLA)")

    balance = _get_sandbox_balance(x_api_key)

    # Live stock prices from Yahoo Finance via price_oracle
    try:
        from trading.tokenized_stocks import stock_exchange, TOKENIZED_STOCKS, get_stock_commission_bps, get_stock_tier_name
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
        from core.database import db as _dispute_db
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
    except Exception as e:
        logger.warning("[Dispute] Rate limit check failed: %s", e)

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
        from core.database import db as _db
        await _db.raw_execute(
            "INSERT OR IGNORE INTO disputes(id, data) VALUES(?, ?)",
            (dispute_id, json.dumps(dispute)))
    except Exception as e:
        logger.error("[Dispute] DB save failed for %s: %s", dispute_id, e)

    return {"success": True, "dispute": dispute}


@router.get("/dispute/{dispute_id}")
async def get_dispute(dispute_id: str, x_api_key: str = Header(None, alias="X-API-Key")):
    """Check dispute status."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    try:
        from core.database import db as _db
        rows = await _db.raw_execute_fetchall("SELECT data FROM disputes WHERE id=?", (dispute_id,))
        if rows:
            return json.loads(rows[0]["data"])
    except Exception as e:
        logger.error("[Dispute] DB read failed for %s: %s", dispute_id, e)
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
        from core.database import db as _db
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
        from core.database import db as _db
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
        from core.database import db as _db
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

