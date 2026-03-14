"""MAXIA Backend V10 — Art.1 to Art.15 (Solana + Base + KiteAI + x402V2 + AP2)"""
import asyncio, os, uuid, time, json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

load_dotenv()

# ── Core imports ──
from database import db
from auth import router as auth_router, require_auth
from auction_manager import AuctionManager
from agent_worker import agent_worker
from subscription_manager import router as sub_router
from referral_manager import router as ref_router
from data_marketplace import router as data_router
from models import (
    AuctionCreateRequest, AuctionSettleRequest, CommandRequest,
    ListingCreateRequest, BaseVerifyRequest, AP2PaymentRequest,
)
from runpod_client import RunPodClient
from solana_verifier import verify_transaction
from security import check_content_safety, check_rate_limit
from config import (
    GPU_TIERS, get_commission_bps,
    TREASURY_ADDRESS, TREASURY_ADDRESS_BASE,
    SUPPORTED_NETWORKS, X402_PRICE_MAP,
)

# ── V10 imports ──
from base_verifier import verify_base_transaction, verify_usdc_transfer_base
from kiteai_client import kite_client
from ap2_manager import ap2_manager
from x402_middleware import x402_middleware

# ── V10.1 — Agent Autonome ──
from growth_agent import growth_agent
from brain import brain
from scheduler import scheduler
from alerts import alert_system
from preflight import check_system_ready, print_preflight
from security import get_daily_spend_stats
from dynamic_pricing import adjust_market_fees, get_pricing_status
from cross_chain_handler import cross_chain
from reputation_staking import reputation_staking
from scale_out import scale_out_manager
from swarm import swarm
from escrow_client import escrow_client
from public_api import router as public_router

# ── Runtime config ──
BROKER_MARGIN      = float(os.getenv("BROKER_MARGIN", "1.20"))
AUCTION_DURATION_S = int(os.getenv("AUCTION_DURATION_S", "30"))

auction_manager = AuctionManager()
runpod          = RunPodClient(api_key=os.getenv("RUNPOD_API_KEY", ""))
_ws_clients: dict = {}


# ── WebSocket broadcast ──

async def broadcast_all(msg: dict):
    dead = []
    for cid, ws in _ws_clients.items():
        try:
            await ws.send_json(msg)
        except Exception:
            dead.append(cid)
    for cid in dead:
        _ws_clients.pop(cid, None)


# ── Lifespan ──

@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect()
    reputation_staking.set_db(db)
    agent_worker.set_broadcast(broadcast_all)
    t1 = asyncio.create_task(auction_manager.run_expiry_worker())
    # V10.1: Lancer le scheduler qui coordonne brain + growth_agent + agent_worker
    t2 = asyncio.create_task(scheduler.run(brain, growth_agent, agent_worker, db))
    t3 = asyncio.create_task(swarm.run_monitor())
    print("[MAXIA] V11 demarre — Art.1-15 + Agent Autonome | Solana + Base + KiteAI + x402V2 + AP2")
    yield
    t1.cancel()
    scheduler.stop()
    t3.cancel()
    await db.disconnect()


# ── App ──

app = FastAPI(title="MAXIA API V12", version="12.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)
app.middleware("http")(x402_middleware)

# ── Routers ──
app.include_router(auth_router)
app.include_router(sub_router)
app.include_router(ref_router)
app.include_router(data_router)
app.include_router(public_router)

FRONTEND_INDEX = Path(__file__).parent.parent / "frontend" / "index.html"
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

# Servir les fichiers statiques du dossier frontend (PDF, images, etc.)
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


# ═══════════════════════════════════════════════════════════
#  CORE ENDPOINTS
# ═══════════════════════════════════════════════════════════

@app.get("/MAXIA_WhitePaper_v1.pdf", include_in_schema=False)
async def serve_whitepaper():
    wp_path = FRONTEND_DIR / "MAXIA_WhitePaper_v1.pdf"
    if wp_path.exists():
        return FileResponse(str(wp_path), media_type="application/pdf", filename="MAXIA_WhitePaper_v1.pdf")
    return HTMLResponse("White Paper non disponible", status_code=404)

LANDING_PAGE = Path(__file__).parent.parent / "frontend" / "landing.html"

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def serve_landing():
    if LANDING_PAGE.exists():
        return HTMLResponse(LANDING_PAGE.read_text(encoding="utf-8"))
    if FRONTEND_INDEX.exists():
        return HTMLResponse(FRONTEND_INDEX.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>MAXIA</h1><p>Page introuvable.</p>")

ADMIN_KEY = os.getenv("ADMIN_KEY", "MaxEli20152022*+")

@app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
async def serve_dashboard(key: str = ""):
    if key != ADMIN_KEY:
        return HTMLResponse(
            "<div style='background:#0A0E17;color:#94A3B8;height:100vh;display:flex;align-items:center;justify-content:center;font-family:sans-serif'>"
            "<h1 style='color:#FF4560'>403 — Acces refuse</h1></div>",
            status_code=403
        )
    if FRONTEND_INDEX.exists():
        return HTMLResponse(FRONTEND_INDEX.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>MAXIA</h1><p>Dashboard introuvable.</p>")


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "version": "12.0.0",
        "timestamp": int(time.time()),
        "articles": [
            "1-Ethique", "2-Commissions", "3-Oracle", "4-RateLimit",
            "5-GPU", "6-Bourse", "7-Marketplace", "8-Agent",
            "9-x402-V2-MultiChain", "10-Abonnements", "11-Referrals",
            "12-Data", "13-Base-L2", "14-KiteAI", "15-AP2",
            "16-DynamicPricing", "17-CrossChain", "18-ReputationStaking", "19-ScaleOut", "20-CloneSwarm", "21-EscrowOnChain", "22-PublicAPI-IA", "23-StockExchange", "24-CryptoSwap", "25-WebScraper", "26-ImageGen", "27-WalletMonitor",
        ],
        "networks": ["solana-mainnet", "base-mainnet", "kite-mainnet"],
        "protocols": ["x402-v2", "ap2", "kite-air"],
    }


@app.get("/api/stats")
async def get_stats():
    return await db.get_stats()


@app.get("/api/activity")
async def get_activity(limit: int = 30):
    return await db.get_activity(limit)


# ═══════════════════════════════════════════════════════════
#  CEO MAXIA — API endpoints
# ═══════════════════════════════════════════════════════════

@app.get("/api/ceo/status")
async def ceo_status():
    try:
        from ceo_maxia import ceo
        return ceo.get_status()
    except Exception as e:
        return {"error": str(e), "ceo": "not_loaded"}


@app.post("/api/ceo/message")
async def ceo_message(request: Request):
    """Envoie un message au CEO — il repond automatiquement."""
    try:
        from ceo_maxia import ceo
        body = await request.json()
        canal = body.get("canal", "api")
        user = body.get("user", "anonymous")
        message = body.get("message", "")
        if not message:
            return {"error": "message required"}
        response = await ceo.handle_message(canal, user, message)
        return response
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/ceo/feedback")
async def ceo_feedback(request: Request):
    """Envoie un feedback client au CEO (TESTIMONIAL)."""
    try:
        from ceo_maxia import ceo
        body = await request.json()
        user = body.get("user", "anonymous")
        feedback = body.get("feedback", "")
        if not feedback:
            return {"error": "feedback required"}
        return await ceo.handle_feedback(user, feedback)
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/ceo/ping")
async def ceo_ping():
    """Le fondateur signale sa presence."""
    try:
        from ceo_maxia import ceo
        ceo.fondateur_ping()
        return {"status": "ok", "message": "Fondateur ping recu"}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/admin/ceo-reset-emergency")
async def ceo_reset_emergency(request: Request):
    """Reset l'emergency stop du CEO."""
    key = request.query_params.get("key", "")
    if key != ADMIN_KEY:
        raise HTTPException(403, "Unauthorized")
    try:
        from ceo_maxia import ceo
        ceo.reset_emergency()
        return {"status": "ok", "emergency_stop": False}
    except Exception as e:
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════
#  x402 V2 (Art.9) — Multi-chain info
# ═══════════════════════════════════════════════════════════

@app.get("/api/x402/info")
async def x402_info():
    return {
        "version": 2,
        "networks": SUPPORTED_NETWORKS,
        "payTo": {
            "solana": TREASURY_ADDRESS,
            "base": TREASURY_ADDRESS_BASE,
        },
        "priceMap": X402_PRICE_MAP,
        "protocols": ["x402-v2", "ap2"],
    }


# ═══════════════════════════════════════════════════════════
#  WEBSOCKET
# ═══════════════════════════════════════════════════════════

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    cid = str(uuid.uuid4())
    _ws_clients[cid] = ws
    try:
        while True:
            msg = await ws.receive_json()
            if msg.get("type") == "AUTH":
                wallet = msg.get("wallet", "")
                if wallet:
                    agent_worker.register_external_agent(wallet)
                await ws.send_json({"type": "AUTH_OK", "wallet": wallet})
            elif msg.get("type") == "PING":
                await ws.send_json({"type": "PONG", "timestamp": int(time.time() * 1000)})
    except WebSocketDisconnect:
        pass
    finally:
        _ws_clients.pop(cid, None)


@app.websocket("/auctions")
async def auction_ws(ws: WebSocket):
    await ws.accept()
    cid = str(uuid.uuid4())
    await auction_manager.register(cid, ws)
    wallet = None
    try:
        for a in auction_manager.get_open_auctions():
            await ws.send_json({"type": "AUCTION_OPENED", "payload": a})
        while True:
            msg = await ws.receive_json()
            if msg.get("type") == "AUTH":
                wallet = msg.get("wallet", "")
                auction_manager.set_wallet(cid, wallet)
                agent_worker.register_external_agent(wallet)
            elif msg.get("type") == "PLACE_BID":
                if not wallet:
                    await ws.send_json({"type": "ERROR", "payload": {"reason": "AUTH requis."}})
                    continue
                res = await auction_manager.place_bid(
                    msg["auctionId"], float(msg.get("bidUsdc", 0)), wallet)
                if not res["ok"]:
                    await ws.send_json({"type": "ERROR", "payload": {"reason": res["reason"]}})
    except WebSocketDisconnect:
        pass
    finally:
        await auction_manager.unregister(cid)


# ═══════════════════════════════════════════════════════════
#  MARKETPLACE (Art.7 + Art.8)
# ═══════════════════════════════════════════════════════════

@app.get("/api/marketplace/listings")
async def get_listings(type: str = None, max_price: float = None):
    listings = await db.get_listings()
    if type:
        listings = [l for l in listings if l.get("type") == type]
    if max_price:
        listings = [l for l in listings if l.get("priceUsdc", 0) <= max_price]
    return listings


@app.post("/api/marketplace/listings")
async def create_listing(req: ListingCreateRequest, wallet: str = Depends(require_auth)):
    check_content_safety(req.name, "name")
    check_content_safety(req.description, "description")
    l = {
        "id": str(uuid.uuid4()), "agentId": wallet, "name": req.name,
        "type": req.type, "description": req.description,
        "priceUsdc": req.price_usdc, "rating": 5.0, "txCount": 0,
        "createdAt": int(time.time()),
    }
    await db.save_listing(l)
    return l


@app.post("/api/marketplace/commands")
async def create_command(req: CommandRequest, wallet: str = Depends(require_auth)):
    check_content_safety(req.prompt, "prompt")
    if await db.tx_already_processed(req.tx_signature):
        raise HTTPException(400, "Transaction deja utilisee.")
    if not await verify_transaction(req.tx_signature, wallet):
        raise HTTPException(400, "Transaction Solana invalide.")
    cmd = {
        "commandId": str(uuid.uuid4()), "serviceId": req.service_id,
        "buyerWallet": wallet, "txSignature": req.tx_signature,
        "prompt": req.prompt, "status": "pending",
        "createdAt": int(time.time()),
    }
    await db.save_command(cmd)
    await db.record_transaction(wallet, req.tx_signature, req.amount_usdc, "marketplace")
    return {"commandId": cmd["commandId"], "status": "pending"}


@app.get("/api/marketplace/commands/{command_id}")
async def get_command(command_id: str, wallet: str = Depends(require_auth)):
    async with db._db.execute("SELECT data FROM commands WHERE command_id=?", (command_id,)) as c:
        row = await c.fetchone()
    if not row:
        raise HTTPException(404, "Commande introuvable.")
    cmd = json.loads(row[0])
    if cmd.get("buyerWallet") != wallet:
        raise HTTPException(403, "Acces refuse.")
    return cmd


# ═══════════════════════════════════════════════════════════
#  GPU AUCTIONS (Art.5)
# ═══════════════════════════════════════════════════════════

@app.get("/api/gpu/tiers")
async def get_tiers():
    return GPU_TIERS


@app.get("/api/gpu/auctions/active")
async def get_active_auctions():
    return auction_manager.get_open_auctions()


@app.post("/api/gpu/auctions")
async def create_auction_rest(req: AuctionCreateRequest, wallet: str = Depends(require_auth)):
    gpu = next((g for g in GPU_TIERS if g["id"] == req.gpu_tier_id), None)
    if not gpu:
        raise HTTPException(400, f"GPU inconnu: {req.gpu_tier_id}")
    cost  = gpu["base_price_per_hour"] * req.duration_hours
    start = req.floor_price_usdc or round(cost * BROKER_MARGIN, 4)
    a = {
        "auctionId": str(uuid.uuid4()), "gpuTierId": req.gpu_tier_id,
        "gpuLabel": gpu["label"], "vramGb": gpu["vram_gb"],
        "durationHours": req.duration_hours, "providerCost": round(cost, 4),
        "startPrice": start, "currentBid": start, "currentLeader": None,
        "brokerWallet": wallet,
        "endsAt": int((time.time() + AUCTION_DURATION_S) * 1000),
        "status": "open",
    }
    await auction_manager.open_auction(a)
    await db.save_auction(a)
    return a


@app.post("/api/gpu/auctions/settle")
async def settle_auction(req: AuctionSettleRequest):
    if await db.tx_already_processed(req.tx_signature):
        raise HTTPException(400, "Transaction deja traitee.")
    if not await verify_transaction(req.tx_signature, req.winner):
        raise HTTPException(400, "Transaction invalide.")
    auction = await db.get_auction(req.auction_id)
    if not auction:
        raise HTTPException(404, "Enchere introuvable.")
    instance = await runpod.rent_gpu(auction["gpuTierId"], auction["durationHours"])
    if not instance.get("success"):
        raise HTTPException(502, f"RunPod: {instance.get('error')}")
    await db.record_transaction(req.winner, req.tx_signature, auction["currentBid"], "gpu_auction")
    await db.update_auction(req.auction_id, {
        "status": "provisioned", "txSignature": req.tx_signature,
        "gpuInstanceId": instance["instanceId"], "sshEndpoint": instance["sshEndpoint"],
    })
    await auction_manager.broadcast({"type": "GPU_PROVISIONED", "payload": {
        "auctionId": req.auction_id, "winner": req.winner,
        "gpuLabel": auction["gpuLabel"], "sshEndpoint": instance["sshEndpoint"],
    }})
    return {"ok": True, "instanceId": instance["instanceId"]}


# ═══════════════════════════════════════════════════════════
#  EXCHANGE (Art.6)
# ═══════════════════════════════════════════════════════════

@app.get("/api/exchange/tokens")
async def get_tokens():
    return await db.get_tokens()


@app.post("/api/exchange/tokens")
async def list_token_api(req: dict, wallet: str = Depends(require_auth)):
    t = {
        "mint": req.get("mint"), "symbol": req.get("symbol"),
        "name": req.get("name"), "decimals": req.get("decimals", 9),
        "price": req.get("initial_price", 0), "creator": wallet,
    }
    await db.save_token(t)
    return t


@app.get("/api/exchange/orders")
async def get_orders(mint: str):
    return await db.get_open_orders(mint)


@app.get("/api/agents/{wallet}/stats")
async def agent_stats(wallet: str):
    try:
        volume = await db.get_agent_volume_30d(wallet)
    except Exception:
        volume = 0.0
    bps = get_commission_bps(volume)
    tiers = [{"name": "BALEINE", "min": 5000}, {"name": "OR", "min": 500}, {"name": "BRONZE", "min": 0}]
    tier = next((t["name"] for t in tiers if volume >= t["min"]), "BRONZE")
    return {"wallet": wallet, "volume30d": volume, "commissionBps": bps, "tier": tier}


# ═══════════════════════════════════════════════════════════
#  BASE — Coinbase L2 (Art.13)
# ═══════════════════════════════════════════════════════════

@app.get("/api/base/info")
async def base_info():
    from config import BASE_RPC, BASE_CHAIN_ID, BASE_USDC_CONTRACT
    return {
        "network": "base-mainnet",
        "chainId": BASE_CHAIN_ID,
        "rpc": BASE_RPC,
        "usdcContract": BASE_USDC_CONTRACT,
        "treasury": TREASURY_ADDRESS_BASE,
        "status": "active",
    }


@app.post("/api/base/verify")
async def verify_base_tx(req: BaseVerifyRequest):
    return await verify_base_transaction(req.tx_hash, req.expected_to)


@app.post("/api/base/verify-usdc")
async def verify_base_usdc(req: BaseVerifyRequest):
    return await verify_usdc_transfer_base(req.tx_hash, req.expected_amount_raw)


# ═══════════════════════════════════════════════════════════
#  KITE AI (Art.14)
# ═══════════════════════════════════════════════════════════

@app.get("/api/kite/info")
async def kite_info():
    return {
        "platform": "kite-ai",
        "agentId": kite_client.agent_id or "not-registered",
        "apiConfigured": bool(kite_client.api_key),
        "features": ["agent_identity", "agent_payments", "service_discovery", "poai"],
    }


@app.post("/api/kite/register-agent")
async def kite_register(req: dict, wallet: str = Depends(require_auth)):
    return await kite_client.register_agent(
        name=req.get("name", f"MAXIA-{wallet[:8]}"),
        capabilities=req.get("capabilities", ["ai_inference", "data", "gpu"]),
        metadata={"wallet": wallet, "platform": "maxia"},
    )


@app.get("/api/kite/verify-agent/{agent_id}")
async def kite_verify(agent_id: str):
    return await kite_client.verify_agent(agent_id)


@app.post("/api/kite/pay")
async def kite_pay(req: dict, wallet: str = Depends(require_auth)):
    result = await kite_client.create_payment(
        to_agent=req.get("to_agent", ""),
        amount_usdc=float(req.get("amount_usdc", 0)),
        purpose=req.get("purpose", "service"),
    )
    if result.get("success"):
        await db.record_transaction(
            wallet, result.get("txHash", ""),
            float(req.get("amount_usdc", 0)), "kite_payment",
        )
    return result


@app.get("/api/kite/discover")
async def kite_discover(category: str = None, max_price: float = None):
    return await kite_client.discover_services(category, max_price)


@app.post("/api/kite/poai")
async def kite_poai(req: dict, wallet: str = Depends(require_auth)):
    return await kite_client.report_contribution(
        task_id=req.get("task_id", ""),
        result_hash=req.get("result_hash", ""),
        model_used=req.get("model", "gemini-2.0-flash"),
    )


# ═══════════════════════════════════════════════════════════
#  AP2 — Google Agent Payments Protocol (Art.15)
# ═══════════════════════════════════════════════════════════

@app.get("/api/ap2/info")
async def ap2_info():
    return ap2_manager.get_info()


@app.get("/api/ap2/stats")
async def ap2_stats():
    return ap2_manager.get_stats()


@app.post("/api/ap2/mandate/intent")
async def ap2_create_intent(req: dict, wallet: str = Depends(require_auth)):
    return ap2_manager.create_intent_mandate(
        user_wallet=wallet,
        max_amount=float(req.get("max_amount", 1000)),
        categories=req.get("categories"),
        ttl_seconds=int(req.get("ttl_seconds", 3600)),
    )


@app.post("/api/ap2/mandate/cart")
async def ap2_create_cart(req: dict, wallet: str = Depends(require_auth)):
    return ap2_manager.create_cart_mandate(
        intent_mandate_id=req.get("intent_mandate_id", ""),
        items=req.get("items", []),
        total_usdc=float(req.get("total_usdc", 0)),
        payment_method=req.get("payment_method", "usdc_solana"),
    )


@app.post("/api/ap2/pay")
async def ap2_pay_incoming(req: dict):
    """Accept incoming AP2 payment from external agent."""
    return await ap2_manager.process_payment(
        intent_mandate=req.get("intent_mandate", {}),
        cart_mandate=req.get("cart_mandate"),
        payment_payload=req.get("payment_payload"),
        network=req.get("network", "solana-mainnet"),
    )


@app.post("/api/ap2/pay-external")
async def ap2_pay_outgoing(req: dict, wallet: str = Depends(require_auth)):
    """Use AP2 to pay for an external agent service."""
    return await ap2_manager.pay_external(
        service_url=req.get("service_url", ""),
        amount_usdc=float(req.get("amount_usdc", 0)),
        user_wallet=wallet,
        purpose=req.get("purpose", "ai_service"),
    )


# ═══════════════════════════════════════════════════════════
#  AGENT AUTONOME (V10.1)
# ═══════════════════════════════════════════════════════════

@app.get("/api/agent/status")
async def agent_status():
    """Statut complet de l'agent autonome."""
    return {
        "brain": brain.get_stats(),
        "growth": growth_agent.get_stats(),
        "daily_spend": get_daily_spend_stats(),
    }

@app.get("/api/agent/brain")
async def brain_status():
    return brain.get_stats()

@app.get("/api/agent/growth")
async def growth_status():
    return growth_agent.get_stats()

@app.get("/api/agent/preflight")
async def preflight():
    """Diagnostic systeme complet."""
    results = await check_system_ready()
    return results

@app.post("/api/agent/growth/stop")
async def stop_growth():
    """Arret d'urgence de l'agent marketing."""
    growth_agent.stop()
    return {"ok": True, "message": "Growth agent arrete"}

@app.post("/api/agent/growth/start")
async def start_growth():
    """Relance l'agent marketing."""
    if not growth_agent._running:
        asyncio.create_task(growth_agent.run())
    return {"ok": True, "message": "Growth agent relance"}


# ═══════════════════════════════════════════════════════════
#  V11: DYNAMIC PRICING (Art.16)
# ═══════════════════════════════════════════════════════════

@app.get("/api/pricing/status")
async def pricing_status():
    return get_pricing_status()

@app.post("/api/pricing/adjust")
async def pricing_force_adjust():
    """Force un ajustement du pricing."""
    result = await adjust_market_fees(db)
    return result


# ═══════════════════════════════════════════════════════════
#  V11: CROSS-CHAIN BRIDGE (Art.17)
# ═══════════════════════════════════════════════════════════

@app.get("/api/bridge/routes")
async def bridge_routes():
    return cross_chain.get_supported_routes()

@app.get("/api/bridge/stats")
async def bridge_stats():
    return cross_chain.get_stats()

@app.get("/api/bridge/test")
async def bridge_test():
    """Teste la connexion au bridge Li.Fi."""
    return await cross_chain.test_connection()

@app.post("/api/bridge/quote")
async def bridge_quote(req: dict):
    return await cross_chain.get_quote(
        from_chain=req.get("from_chain", "base"),
        from_token=req.get("from_token", "USDC"),
        to_chain=req.get("to_chain", "solana"),
        to_token=req.get("to_token", "USDC"),
        amount=req.get("amount", "1000000"),
        from_address=req.get("from_address", ""),
    )

@app.post("/api/bridge/confirm")
async def bridge_confirm(req: dict):
    return await cross_chain.confirm_bridge(
        bridge_id=req.get("bridge_id", ""),
        tx_signature=req.get("tx_signature", ""),
    )


# ═══════════════════════════════════════════════════════════
#  V11: REPUTATION STAKING (Art.18)
# ═══════════════════════════════════════════════════════════

@app.get("/api/staking/stats")
async def staking_stats():
    try:
        return await reputation_staking.get_stats()
    except Exception:
        return {"total_stakers": 0, "total_staked_usdc": 0, "pending_disputes": 0, "total_slashed": 0, "min_stake_usdc": 50, "slash_pct": 50, "dispute_delay_h": 48}

@app.get("/api/staking/{wallet}")
async def get_stake(wallet: str):
    return reputation_staking.get_stake(wallet)

@app.post("/api/staking/stake")
async def create_stake(req: dict, wallet: str = Depends(require_auth)):
    return await reputation_staking.stake(
        wallet=wallet,
        amount_usdc=float(req.get("amount_usdc", 0)),
        tx_signature=req.get("tx_signature", ""),
    )

@app.post("/api/staking/dispute")
async def open_dispute(req: dict, wallet: str = Depends(require_auth)):
    return await reputation_staking.open_dispute(
        reporter_wallet=wallet,
        accused_wallet=req.get("accused_wallet", ""),
        reason=req.get("reason", ""),
        evidence=req.get("evidence", ""),
    )

@app.post("/api/staking/resolve")
async def resolve_dispute(req: dict):
    return await reputation_staking.resolve_dispute(
        dispute_id=req.get("dispute_id", ""),
        slash=req.get("slash", False),
        db=db,
    )


# ═══════════════════════════════════════════════════════════
#  V11: SCALE-OUT (Art.19)
# ═══════════════════════════════════════════════════════════

@app.get("/api/scale/stats")
async def scale_stats():
    return scale_out_manager.get_stats()


# ══════════════════════════════════════════════════════════
#  V11: CLONE SWARM — Essaim d'IA (Art.20)
# ══════════════════════════════════════════════════════════

@app.get("/api/swarm/stats")
async def swarm_stats():
    """Stats completes de l'essaim."""
    return swarm.get_stats()

@app.get("/api/swarm/niches")
async def swarm_niches():
    """Liste des niches disponibles."""
    return swarm.get_available_niches()

@app.post("/api/swarm/analyze")
async def swarm_analyze():
    """Analyse IA des niches rentables."""
    return await swarm.analyze_niches(db)

@app.post("/api/swarm/spawn")
async def swarm_spawn(req: dict, wallet: str = Depends(require_auth)):
    """Deployer un nouveau clone specialise."""
    return await swarm.spawn_clone(
        niche=req.get("niche", ""),
        wallet_address=req.get("wallet_address", ""),
        wallet_privkey=req.get("wallet_privkey", ""),
    )

@app.post("/api/swarm/request")
async def swarm_request(req: dict):
    """Envoyer une requete a un clone specialise."""
    return await swarm.process_request(
        niche=req.get("niche", ""),
        prompt=req.get("prompt", ""),
        buyer_wallet=req.get("buyer_wallet", ""),
    )

@app.post("/api/swarm/pause/{clone_id}")
async def swarm_pause(clone_id: str):
    return swarm.pause_clone(clone_id)

@app.post("/api/swarm/resume/{clone_id}")
async def swarm_resume(clone_id: str):
    return swarm.resume_clone(clone_id)

@app.post("/api/swarm/stop/{clone_id}")
async def swarm_stop(clone_id: str):
    return swarm.stop_clone(clone_id)


# ══════════════════════════════════════════════════════════
#  V11: ESCROW ON-CHAIN (Art.21)
# ══════════════════════════════════════════════════════════

@app.get("/api/escrow/stats")
async def escrow_stats():
    return escrow_client.get_stats()

@app.get("/api/escrow/{escrow_id}")
async def get_escrow(escrow_id: str):
    return escrow_client.get_escrow(escrow_id)

@app.post("/api/escrow/create")
async def create_escrow(req: dict, wallet: str = Depends(require_auth)):
    return await escrow_client.create_escrow(
        buyer_wallet=wallet,
        seller_wallet=req.get("seller_wallet", ""),
        amount_usdc=float(req.get("amount_usdc", 0)),
        service_id=req.get("service_id", ""),
        tx_signature=req.get("tx_signature", ""),
        timeout_hours=int(req.get("timeout_hours", 72)),
    )

@app.post("/api/escrow/confirm")
async def confirm_escrow(req: dict, wallet: str = Depends(require_auth)):
    return await escrow_client.confirm_delivery(
        escrow_id=req.get("escrow_id", ""),
        buyer_wallet=wallet,
    )

@app.post("/api/escrow/reclaim")
async def reclaim_escrow(req: dict, wallet: str = Depends(require_auth)):
    return await escrow_client.reclaim_timeout(
        escrow_id=req.get("escrow_id", ""),
        buyer_wallet=wallet,
    )

@app.post("/api/escrow/resolve")
async def resolve_escrow_dispute(req: dict):
    return await escrow_client.resolve_dispute(
        escrow_id=req.get("escrow_id", ""),
        release_to_seller=req.get("release_to_seller", False),
    )


# ══════════════════════════════════════════════════════════
#  ADMIN: Seed initial services (one-time setup)
# ══════════════════════════════════════════════════════════

INITIAL_SERVICES = [
    # ── Services a la carte ──
    {
        "name": "MAXIA AI Security Scan",
        "description": "AI-powered smart contract vulnerability scanner. Detects reentrancy, overflow, access control, logic flaws. Supports Solidity, Rust (Anchor), Move. Structured report: [CRITICAL][MAJOR][MINOR][INFO]. Results in seconds, not weeks.",
        "type": "audit",
        "priceUsdc": 4.99,
    },
    {
        "name": "MAXIA Crypto Data Analyst",
        "description": "Real-time DeFi and crypto market analysis. On-chain metrics, whale tracking, liquidity pools, price predictions, token scoring. Supports Solana, Ethereum, Base. Pay per query — no monthly subscription needed.",
        "type": "data",
        "priceUsdc": 1.99,
    },
    {
        "name": "MAXIA Code Engineer",
        "description": "Professional AI code generation and review. Python, Rust, JavaScript, TypeScript, Solidity. Production-ready, commented, optimized code. Bug fixing, refactoring, architecture design. Pay per task.",
        "type": "code",
        "priceUsdc": 1.99,
    },
    {
        "name": "MAXIA Universal Translator",
        "description": "AI translation in 50+ languages. Professional quality, context-aware. Documents, websites, apps, smart contract docs. EN, FR, ES, DE, PT, ZH, JA, KO, RU, AR and more.",
        "type": "text",
        "priceUsdc": 0.09,
    },
    # ── Forfaits (Packs) ──
    {
        "name": "MAXIA Starter Pack — 10 requests",
        "description": "10 requests to use on any MAXIA service (Security Scan, Data Analysis, Code, Translation). Valid forever. Best value for occasional users. Save 20% vs pay-per-use.",
        "type": "pack",
        "priceUsdc": 9.99,
    },
    {
        "name": "MAXIA Pro Pack — 50 requests",
        "description": "50 requests to use on any MAXIA service. Ideal for developers and traders who need regular AI assistance. Save 35% vs pay-per-use. Priority processing.",
        "type": "pack",
        "priceUsdc": 39.99,
    },
    {
        "name": "MAXIA Unlimited Monthly",
        "description": "Unlimited access to ALL MAXIA services for 30 days. Security scans, data analysis, code generation, translation. No limits. Best for teams and power users. Includes priority support.",
        "type": "subscription",
        "priceUsdc": 79.99,
    },
    {
        "name": "MAXIA Deep Security Audit",
        "description": "Comprehensive AI security audit with multi-pass analysis. Covers reentrancy, flash loan exploits, oracle manipulation, access control, economic attacks. Detailed PDF report with severity ratings and fix recommendations. For serious DeFi projects.",
        "type": "audit_deep",
        "priceUsdc": 49.99,
    },
]

@app.post("/api/admin/seed-services")
async def seed_services():
    """Ajoute les services initiaux (une seule fois)."""
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


INITIAL_DATASETS = [
    {
        "name": "Solana DeFi Transactions 2025",
        "description": "Complete dataset of DeFi swap transactions on Solana DEXs (Raydium, Orca, Jupiter) from 2025. 50M+ rows. CSV format. Token pairs, volumes, prices, timestamps.",
        "category": "market_data",
        "size_mb": 2400,
        "price_usdc": 19.99,
        "sample_hash": "a1b2c3d4e5f6",
        "format": "csv",
    },
    {
        "name": "Top 1000 Token Prices Historical",
        "description": "Hourly OHLCV data for the top 1000 cryptocurrencies. 3 years of history (2023-2025). Perfect for backtesting trading strategies. JSON format.",
        "category": "market_data",
        "size_mb": 800,
        "price_usdc": 9.99,
        "sample_hash": "f1e2d3c4b5a6",
        "format": "json",
    },
    {
        "name": "Smart Contract Vulnerability Database",
        "description": "Curated database of 10,000+ known smart contract vulnerabilities. Solidity and Rust. Classified by severity, type, and exploit method. Updated monthly.",
        "category": "security",
        "size_mb": 150,
        "price_usdc": 29.99,
        "sample_hash": "sec123vuln456",
        "format": "json",
    },
    {
        "name": "NFT Collection Metadata (Solana)",
        "description": "Metadata for 500+ Solana NFT collections. Floor prices, holders, volume, rarity scores. Updated weekly. Ideal for analytics and trading bots.",
        "category": "nft_data",
        "size_mb": 350,
        "price_usdc": 14.99,
        "sample_hash": "nft789meta012",
        "format": "json",
    },
]

@app.post("/api/admin/seed-datasets")
async def seed_datasets():
    """Ajoute les datasets initiaux (une seule fois)."""
    try:
        existing = await db._db.execute_fetchall("SELECT data FROM datasets")
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
            await db._db.execute(
                "INSERT OR REPLACE INTO datasets(dataset_id,seller,data) VALUES(?,?,?)",
                (dataset["datasetId"], TREASURY_ADDRESS, json.dumps(dataset)),
            )
            await db._db.commit()
            added += 1
    return {"message": f"{added} datasets ajoutes", "total": len(existing_list) + added}


# ══════════════════════════════════════════════════════════
#  WHITE PAPER
# ══════════════════════════════════════════════════════════

@app.get("/api/whitepaper")
async def whitepaper():
    """Lien vers le White Paper MAXIA."""
    return {
        "title": "MAXIA White Paper v1.0",
        "version": "1.0",
        "date": "Mars 2026",
        "download": "https://github.com/majorelalexis-stack/maxia/blob/main/MAXIA_WhitePaper_v1.pdf",
        "sections": [
            "1. Resume Executif",
            "2. Le Probleme",
            "3. La Solution MAXIA",
            "4. Modele Economique",
            "5. Architecture Technique",
            "6. API Publique pour Agents IA",
            "7. Securite",
            "8. Essaim d IA",
            "9. Feuille de Route",
            "10. Infrastructure Blockchain",
            "11. Conclusion",
        ],
        "highlights": {
            "commission_min": "0.1% (Baleine)",
            "gpu_markup": "0% (prix coutant)",
            "services": 8,
            "modules": 22,
            "networks": 3,
            "protocols": 3,
        },
    }


# ══════════════════════════════════════════════════════════
#  V11: BOURSE ACTIONS TOKENISEES (Art.23)
# ══════════════════════════════════════════════════════════

@app.get("/api/stocks/stats")
async def stock_exchange_stats():
    from tokenized_stocks import stock_exchange
    return stock_exchange.get_stats()
