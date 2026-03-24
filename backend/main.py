"""MAXIA Backend V12 — Art.1 to Art.15 + 47 features (14 chains: Solana + Base + Ethereum + XRP + Polygon + Arbitrum + Avalanche + BNB + TON + SUI + TRON + NEAR + Aptos + SEI + 17 AI Agents)"""
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
from database import db, create_database
from auth import router as auth_router, require_auth
from auction_manager import AuctionManager
from agent_worker import agent_worker
from referral_manager import router as ref_router
from data_marketplace import router as data_router
from models import (
    AuctionCreateRequest, AuctionSettleRequest, CommandRequest,
    ListingCreateRequest, BaseVerifyRequest, AP2PaymentRequest,
    GpuRentRequest, GpuRentPublicRequest,
)
from runpod_client import RunPodClient, get_gpu_tiers_live, GPU_MAP
from solana_verifier import verify_transaction
from security import check_content_safety, check_rate_limit, set_redis_client
from redis_client import redis_client
from config import (
    GPU_TIERS, get_commission_bps,
    TREASURY_ADDRESS, TREASURY_ADDRESS_BASE,
    TREASURY_ADDRESS_POLYGON, TREASURY_ADDRESS_ARBITRUM,
    TREASURY_ADDRESS_AVALANCHE, TREASURY_ADDRESS_BNB,
    SUPPORTED_NETWORKS, X402_PRICE_MAP,
)

# ── V10 imports ──
from base_verifier import verify_base_transaction, verify_usdc_transfer_base
from polygon_verifier import verify_polygon_transaction, verify_usdc_transfer_polygon
from arbitrum_verifier import verify_arbitrum_transaction, verify_usdc_transfer_arbitrum
from avalanche_verifier import verify_avalanche_transaction, verify_usdc_transfer_avalanche
from bnb_verifier import verify_bnb_transaction, verify_usdc_transfer_bnb
from kiteai_client import kite_client
from ap2_manager import ap2_manager
from x402_middleware import x402_middleware

# ── V10.1 — Agent Autonome ──
from growth_agent import growth_agent
from scout_agent import scout_agent
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

try:
    from mcp_server import router as mcp_router
except ImportError:
    mcp_router = None

# ── Runtime config ──
BROKER_MARGIN      = float(os.getenv("BROKER_MARGIN", "1.00"))  # matches config.py
AUCTION_DURATION_S = int(os.getenv("AUCTION_DURATION_S", "30"))

auction_manager = AuctionManager()
runpod          = RunPodClient(api_key=os.getenv("RUNPOD_API_KEY", ""))
# NOTE: _ws_clients is per-process. With multiple workers (WEB_CONCURRENCY>1),
# each worker has its own set of WS connections. For true multi-worker WebSocket
# support, use Redis pub/sub as a message broker between workers.
# For now, single-worker mode is recommended for WebSocket features.
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


# ── Native AI Services (registered at startup) ──

NATIVE_SERVICES = [
    {
        "id": "maxia-audit",
        "name": "Smart Contract Audit",
        "description": "AI-powered security audit of Solana/EVM smart contracts. Detects vulnerabilities, reentrancy, overflow, access control issues.",
        "type": "audit",
        "price_usdc": 4.99,
    },
    {
        "id": "maxia-code",
        "name": "AI Code Review",
        "description": "Automated code review for Python, Rust, JavaScript, Solidity. Finds bugs, suggests improvements, checks best practices.",
        "type": "code",
        "price_usdc": 2.99,
    },
    {
        "id": "maxia-translate",
        "name": "AI Translation",
        "description": "Translate text between 50+ languages. Technical documentation, marketing copy, chat messages.",
        "type": "text",
        "price_usdc": 0.05,
    },
    {
        "id": "maxia-summary",
        "name": "Document Summary",
        "description": "Summarize any document, whitepaper, or article into key bullet points. Supports up to 10,000 words.",
        "type": "text",
        "price_usdc": 0.49,
    },
    {
        "id": "maxia-wallet",
        "name": "Wallet Analyzer",
        "description": "Deep analysis of any Solana wallet: token holdings, transaction history, DeFi positions, risk score.",
        "type": "data",
        "price_usdc": 1.99,
    },
    {
        "id": "maxia-marketing",
        "name": "Marketing Copy Generator",
        "description": "Generate landing page copy, Twitter threads, blog posts, product descriptions. Optimized for Web3/AI audience.",
        "type": "text",
        "price_usdc": 0.99,
    },
    {
        "id": "maxia-image",
        "name": "AI Image Generator",
        "description": "Generate images from text prompts. Logos, illustrations, social media graphics. 1024x1024 resolution.",
        "type": "image",
        "price_usdc": 0.10,
    },
    {
        "id": "maxia-scraper",
        "name": "Web Scraper",
        "description": "Extract structured data from any website. Returns clean JSON with the data you need.",
        "type": "data",
        "price_usdc": 0.02,
    },
    {
        "id": "maxia-finetune",
        "name": "LLM Fine-Tuning (Unsloth)",
        "description": "Fine-tune any LLM (Llama, Qwen, Mistral, Gemma, DeepSeek, Phi) on your dataset. Powered by Unsloth on RunPod GPUs. 2x faster, 70% less VRAM.",
        "type": "compute",
        "price_usdc": 2.99,
    },
    {
        "id": "maxia-awp-stake",
        "name": "AWP Agent Staking",
        "description": "Stake USDC on the Autonomous Worker Protocol (Base L2) to earn rewards and increase your agent's trust score. 3-12% APY.",
        "type": "defi",
        "price_usdc": 0,
    },
    # ═══ Machine-only AI services (visible via API/MCP only, not on /app) ═══
    {
        "id": "maxia-transcription",
        "name": "Audio Transcription (Whisper)",
        "description": "Transcribe audio to text. Supports MP3, WAV, M4A. 50+ languages. Powered by local GPU.",
        "type": "ai",
        "price_usdc": 0.01,
        "machine_only": True,
    },
    {
        "id": "maxia-embedding",
        "name": "Text Embedding",
        "description": "Convert text to vector embeddings for RAG, semantic search, clustering. 768-dim vectors.",
        "type": "ai",
        "price_usdc": 0.001,
        "machine_only": True,
    },
    {
        "id": "maxia-sentiment",
        "name": "Sentiment Analysis",
        "description": "Analyze sentiment of text, tweets, or crypto discussions. Returns score (-1 to 1) + confidence.",
        "type": "ai",
        "price_usdc": 0.005,
        "machine_only": True,
    },
    {
        "id": "maxia-wallet-score",
        "name": "Wallet Risk Score",
        "description": "Score any wallet across 14 chains: activity, age, balance, DeFi exposure, risk level (0-100).",
        "type": "data",
        "price_usdc": 0.10,
        "machine_only": True,
    },
    {
        "id": "maxia-airdrop-scan",
        "name": "Airdrop Eligibility Scanner",
        "description": "Scan a wallet for potential airdrop eligibility across 50+ protocols on Solana, ETH, Base, Arbitrum.",
        "type": "data",
        "price_usdc": 0.50,
        "machine_only": True,
    },
    {
        "id": "maxia-smart-money",
        "name": "Smart Money Tracker",
        "description": "Track whale wallets and smart money movements on Solana and EVM chains. Real-time alerts.",
        "type": "data",
        "price_usdc": 0.25,
        "machine_only": True,
    },
    {
        "id": "maxia-nft-rarity",
        "name": "NFT Rarity Checker",
        "description": "Calculate rarity score for any Solana or EVM NFT based on trait distribution.",
        "type": "data",
        "price_usdc": 0.05,
        "machine_only": True,
    },
]


async def _register_native_services(db_instance):
    """Register MAXIA native AI services in the database at startup.
    Skips services that already exist (idempotent).
    """
    from config import TREASURY_ADDRESS
    registered = 0
    for svc in NATIVE_SERVICES:
        try:
            existing = await db_instance.get_service(svc["id"])
            if existing:
                continue
            await db_instance.save_service({
                "id": svc["id"],
                "agent_api_key": "maxia_native",
                "agent_name": "MAXIA",
                "agent_wallet": TREASURY_ADDRESS,
                "name": svc["name"],
                "description": svc["description"],
                "type": svc["type"],
                "price_usdc": svc["price_usdc"],
                "endpoint": "",
                "status": "active",
                "rating": 5.0,
                "rating_count": 0,
                "sales": 0,
            })
            registered += 1
        except Exception as e:
            print(f"[MAXIA] Error registering native service {svc['id']}: {e}")
    if registered:
        print(f"[MAXIA] Registered {registered} native AI services")
    else:
        print(f"[MAXIA] All {len(NATIVE_SERVICES)} native AI services already registered")


# ── Lifespan ──

@asynccontextmanager
async def lifespan(app: FastAPI):
    # V12: Redis connect (graceful fallback to in-memory)
    from config import REDIS_URL
    await redis_client.connect(REDIS_URL)
    set_redis_client(redis_client)

    # V12: Database factory — PostgreSQL if DATABASE_URL set, else SQLite
    import database as _db_mod
    db_instance = await create_database()
    # Patch the module-level singleton so all imports see the new instance
    _db_mod.db = db_instance
    # Also patch our local reference
    global db
    db = db_instance

    reputation_staking.set_db(db)
    escrow_client.set_db(db)

    # #1 Add metrics columns to agent_services (idempotent)
    for col in ["total_executions INTEGER DEFAULT 0", "successful_executions INTEGER DEFAULT 0",
                 "avg_response_ms REAL DEFAULT 0", "uptime_pct REAL DEFAULT 100"]:
        try:
            await db.raw_execute(f"ALTER TABLE agent_services ADD COLUMN {col}")
        except Exception:
            pass  # Column already exists
    await escrow_client._load_from_db()
    agent_worker.set_broadcast(broadcast_all)

    # V12: Register 8 MAXIA native AI services (Groq/Ollama)
    await _register_native_services(db)

    # V12: Ensure referred_by column exists in agents table
    try:
        await db.raw_execute(
            "ALTER TABLE agents ADD COLUMN referred_by TEXT DEFAULT ''")
    except Exception:
        pass  # Column already exists

    # V12: Init new modules (API keys, SLA, webhooks)
    from api_keys import ensure_tables as ensure_api_keys_tables
    from webhook_dispatcher import ensure_tables as ensure_webhook_tables, retry_worker
    from sla_manager import ensure_tables as ensure_sla_tables
    await ensure_api_keys_tables(db)
    await ensure_webhook_tables(db)
    await ensure_sla_tables(db)
    # disputes table is already created in DB_SCHEMA (database.py)

    t1 = asyncio.create_task(auction_manager.run_expiry_worker())
    t2 = asyncio.create_task(scheduler.run(brain, growth_agent, agent_worker, db))
    t3 = asyncio.create_task(swarm.run_monitor())
    t4 = asyncio.create_task(retry_worker(db))  # V12: webhook retry worker
    t5 = asyncio.create_task(scout_agent.run())  # V12: SCOUT IA-to-IA prospection

    # V12: Health monitor (UptimeRobot-style)
    try:
        from health_monitor import run_health_monitor
        t_health = asyncio.create_task(run_health_monitor())
    except Exception as e:
        print(f"[MAXIA] Health monitor init error: {e}")
        t_health = None

    # V12: New features (trading, marketplace, infra)
    try:
        from trading_features import ensure_tables as ensure_trading_tables, check_whales, update_candles
        await ensure_trading_tables()
        t6 = asyncio.create_task(check_whales())
        t7 = asyncio.create_task(update_candles())
    except Exception as e:
        print(f"[MAXIA] Trading features init error: {e}")
        t6 = t7 = None
    try:
        from marketplace_features import ensure_tables as ensure_mkt_tables
        await ensure_mkt_tables()
    except Exception as e:
        print(f"[MAXIA] Marketplace features init error: {e}")
    try:
        from infra_features import ensure_tables as ensure_infra_tables
        await ensure_infra_tables()
    except Exception as e:
        print(f"[MAXIA] Infra features init error: {e}")

    # V12: DB backup
    t_backup = None
    try:
        from db_backup import run_backup_scheduler
        t_backup = asyncio.create_task(run_backup_scheduler())
    except Exception as e:
        print(f"[MAXIA] DB backup init error: {e}")

    # V12: Dispute auto-resolve worker
    async def _dispute_auto_resolve_worker():
        """Auto-resolve disputes after 48h — refund buyer."""
        while True:
            try:
                now = int(time.time())
                rows = await db.raw_execute_fetchall("SELECT id, data FROM disputes")
                for row in (rows or []):
                    dispute = json.loads(row["data"])
                    if dispute.get("status") == "open" and dispute.get("auto_resolve_at", 0) <= now:
                        dispute["status"] = "auto_resolved"
                        dispute["resolution"] = "Auto-resolved after 48h. Buyer refund initiated."
                        await db.raw_execute("UPDATE disputes SET data=? WHERE id=?",
                            (json.dumps(dispute), row["id"]))
                        print(f"[Disputes] Auto-resolved: {row['id']}")
            except Exception as e:
                if "no such table" not in str(e):
                    print(f"[Disputes] Worker error: {e}")
            await asyncio.sleep(3600)  # check every hour

    t_dispute = asyncio.create_task(_dispute_auto_resolve_worker())

    # V12: Start task queue worker
    try:
        from ceo_maxia import task_queue
        t_taskq = asyncio.create_task(task_queue.worker())
    except Exception as e:
        print(f"[MAXIA] Task queue init error: {e}")
        t_taskq = None

    # Init file logger
    try:
        from logger import app_logger
        app_logger.info("MAXIA V12 starting up")
    except Exception:
        pass

    # Preflight env check
    try:
        from preflight import check_system_ready, print_preflight
        pf = await check_system_ready()
        print_preflight(pf)
        missing = pf.get("env_vars", {}).get("missing_critical", [])
        if missing:
            print(f"[MAXIA] ⚠️  Missing critical env vars: {', '.join(missing)}")
    except Exception as e:
        print(f"[MAXIA] Preflight error: {e}")

    # Security checks at startup
    from security import check_jwt_secret, _flush_audit
    if not check_jwt_secret():
        print("[MAXIA] ⚠️  Set JWT_SECRET in .env for production security!")

    print("[MAXIA] V12 demarre — Art.1-15 + 10 new features + Health monitor + DB backup | 14 chains: Solana + Base + Ethereum + XRP + Polygon + Arbitrum + Avalanche + BNB + TON + SUI + TRON + NEAR + Aptos + SEI")
    print(f"[MAXIA] DB: {'PostgreSQL' if os.getenv('DATABASE_URL', '').startswith('postgres') else 'SQLite'} | Redis: {'connected' if redis_client.is_connected else 'in-memory fallback'}")
    print(f"[MAXIA] CORS: {_ALLOWED_ORIGINS}")
    yield

    # ── Graceful shutdown ──
    print("[MAXIA] Shutting down gracefully...")
    # Flush audit log
    try:
        _flush_audit()
    except Exception:
        pass
    # Save CEO memory
    try:
        from ceo_maxia import ceo
        ceo.memory.save()
        print("[MAXIA] CEO memory saved")
    except Exception:
        pass
    # Stop task queue
    try:
        task_queue.stop()
        if t_taskq:
            t_taskq.cancel()
    except Exception:
        pass
    # Cancel all background tasks
    for t in [t1, t2, t3, t4, t5]:
        try:
            t.cancel()
        except Exception:
            pass
    for t in [t_health, t6, t7, t_backup, t_dispute]:
        try:
            if t:
                t.cancel()
        except Exception:
            pass
    scheduler.stop()
    scout_agent.stop()
    # Close connections
    try:
        from price_oracle import close_http_pool
        await close_http_pool()
    except Exception:
        pass
    await db.disconnect()
    await redis_client.close()
    print("[MAXIA] Shutdown complete")


# ── App ──

app = FastAPI(title="MAXIA API V12", version="12.0.0", lifespan=lifespan)

# ── CORS restrictif (pas de wildcard en prod) ──
_ALLOWED_ORIGINS = os.getenv("CORS_ORIGINS", "https://maxiaworld.app,https://www.maxiaworld.app,http://localhost:8001,http://localhost:3000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Wallet", "X-Signature", "X-Nonce", "X-Admin-Key", "X-CEO-Key", "X-API-Key", "X-Payment", "X-Payment-Network"],
    allow_credentials=True,
)
app.middleware("http")(x402_middleware)

# ── HTTPS redirect en production ──
@app.middleware("http")
async def https_redirect_middleware(request, call_next):
    """Redirige HTTP vers HTTPS en production (detecte via X-Forwarded-Proto)."""
    if os.getenv("FORCE_HTTPS", "false").lower() == "true":
        proto = request.headers.get("x-forwarded-proto", "https")
        if proto == "http":
            from starlette.responses import RedirectResponse
            url = str(request.url).replace("http://", "https://", 1)
            return RedirectResponse(url, status_code=301)
    return await call_next(request)

# ── Rate Limit + Burst Protection Middleware ──
@app.middleware("http")
async def rate_limit_headers_middleware(request, call_next):
    from security import check_rate_limit_smart, get_rate_limit_info, check_burst_limit, get_burst_ban_remaining
    ip = request.client.host if request.client else "unknown"

    # Burst protection — bloque les DDoS (>20 req/2s)
    # Exempter localhost (watchdog interne fait ~18 req en rafale)
    if ip not in ("127.0.0.1", "::1") and not check_burst_limit(ip):
        ban_remaining = get_burst_ban_remaining(ip)
        from starlette.responses import JSONResponse
        return JSONResponse(
            status_code=429,
            content={"error": "Too many requests. Slow down.", "retry_after": ban_remaining},
            headers={"Retry-After": str(ban_remaining)},
        )

    # Rate limit check BEFORE processing the request (H-06 fix)
    try:
        path = request.url.path
        if not check_rate_limit_smart(ip, path):
            info = get_rate_limit_info(ip)
            from starlette.responses import JSONResponse as _JSONResp
            return _JSONResp(
                status_code=429,
                content={"error": "Rate limit exceeded", "retry_after": 60},
                headers={**info, "Retry-After": "60"},
            )
    except Exception:
        pass

    response = await call_next(request)
    try:
        info = get_rate_limit_info(ip)
        for k, v in info.items():
            response.headers[k] = v
    except Exception:
        pass
    return response

# ── Routers ──
app.include_router(auth_router)
app.include_router(ref_router)
app.include_router(data_router)
app.include_router(public_router)
if mcp_router:
    app.include_router(mcp_router)

# V12: Analytics dashboard
from analytics import router as analytics_router
app.include_router(analytics_router)

# V12: New features routers
try:
    from trading_features import get_router as get_trading_router
    app.include_router(get_trading_router())
except Exception as e:
    print(f"[MAXIA] Trading router error: {e}")
try:
    from marketplace_features import get_router as get_mkt_router
    app.include_router(get_mkt_router())
except Exception as e:
    print(f"[MAXIA] Marketplace router error: {e}")
try:
    from infra_features import get_router as get_infra_router
    app.include_router(get_infra_router())
except Exception as e:
    print(f"[MAXIA] Infra router error: {e}")
try:
    from email_service import router as email_router
    app.include_router(email_router)
    print("[Email] Service ceo@maxiaworld.app monte")
except Exception as e:
    print(f"[MAXIA] Email router error: {e}")
try:
    from yield_aggregator import router as yield_router
    app.include_router(yield_router)
    print("[Yield] Aggregator DeFi monte")
except Exception as e:
    print(f"[MAXIA] Yield router error: {e}")
try:
    from rpc_service import router as rpc_router
    app.include_router(rpc_router)
    print("[RPC] RPC-as-a-Service 14 chains monte")
except Exception as e:
    print(f"[MAXIA] RPC router error: {e}")
try:
    from oracle_service import router as oracle_router
    app.include_router(oracle_router)
    print("[Oracle] Oracle + Data Marketplace monte")
except Exception as e:
    print(f"[MAXIA] Oracle router error: {e}")
try:
    from bridge_service import router as bridge_router
    app.include_router(bridge_router)
    print("[Bridge] Cross-chain bridge 14 chains monte")
except Exception as e:
    print(f"[MAXIA] Bridge router error: {e}")
try:
    from nft_service import router as nft_router
    app.include_router(nft_router)
    print("[NFT] Agent ID + Trust Score + Service Passes monte")
except Exception as e:
    print(f"[MAXIA] NFT router error: {e}")
try:
    from subscription_service import router as sub_router
    app.include_router(sub_router)
    print("[Subscriptions] Streaming payments USDC monte")
except Exception as e:
    print(f"[MAXIA] Subscription router error: {e}")
try:
    from trading_tools import router as trading_router
    app.include_router(trading_router)
    print("[Trading] Whale tracker, candles, signals, portfolio, alerts monte")
except Exception as e:
    print(f"[MAXIA] Trading router error: {e}")

# V12: Fine-tuning LLM as a Service (Unsloth + RunPod)
try:
    from finetune_service import router as finetune_router
    app.include_router(finetune_router)
    print("[Finetune] LLM Fine-Tuning as a Service (Unsloth) monte")
except Exception as e:
    print(f"[MAXIA] Finetune router error: {e}")

# V12: AWP Protocol (Agent Staking on Base)
try:
    from awp_protocol import router as awp_router
    app.include_router(awp_router)
    print("[AWP] Autonomous Worker Protocol (staking + discovery) monte")
except Exception as e:
    print(f"[MAXIA] AWP router error: {e}")

# V12: GOAT Protocol Bridge (200+ onchain tools)
try:
    from goat_bridge import router as goat_router
    app.include_router(goat_router)
    print("[GOAT] Protocol bridge (200+ tools) monte")
except Exception as e:
    print(f"[MAXIA] GOAT bridge error: {e}")

# V12: Solana DeFi (lending/borrowing/staking)
try:
    from solana_defi import router as solana_defi_router
    app.include_router(solana_defi_router)
    print("[DeFi] Solana DeFi (lending/borrowing/staking/LP) monte")
except Exception as e:
    print(f"[MAXIA] Solana DeFi error: {e}")

# V12: LLM-as-a-Service (OpenAI-compatible, multi-provider)
try:
    from llm_service import router as llm_svc_router
    app.include_router(llm_svc_router)
    print("[LLM] LLM-as-a-Service (OpenAI-compatible) monte")
except Exception as e:
    print(f"[MAXIA] LLM service router error: {e}")

# V12: A2A Protocol (Google/Linux Foundation — Agent2Agent)
try:
    from a2a_protocol import router as a2a_router
    app.include_router(a2a_router)
    print("[A2A] Agent2Agent Protocol (JSON-RPC 2.0 + SSE) monte")
except Exception as e:
    print(f"[MAXIA] A2A router error: {e}")

FRONTEND_INDEX = Path(__file__).parent.parent / "frontend" / "index.html"
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

# Servir les fichiers statiques du dossier frontend (PDF, images, etc.)
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


# ═══════════════════════════════════════════════════════════
#  CORE ENDPOINTS
# ═══════════════════════════════════════════════════════════

LANDING_PAGE = Path(__file__).parent.parent / "frontend" / "landing.html"

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def serve_landing():
    if LANDING_PAGE.exists():
        return HTMLResponse(LANDING_PAGE.read_text(encoding="utf-8"))
    if FRONTEND_INDEX.exists():
        return HTMLResponse(FRONTEND_INDEX.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>MAXIA</h1><p>Page introuvable.</p>")

REGISTER_PAGE = Path(__file__).parent.parent / "frontend" / "register.html"
APP_PAGE = Path(__file__).parent.parent / "frontend" / "app.html"

@app.get("/register", response_class=HTMLResponse, include_in_schema=False)
async def serve_register():
    if REGISTER_PAGE.exists():
        return HTMLResponse(REGISTER_PAGE.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Register</h1><p>Page not found.</p>")

@app.get("/app", response_class=HTMLResponse, include_in_schema=False)
async def serve_app():
    """Interface humaine — Web3 Hub (swap, portfolio, GPU, yields, bridge, stocks, NFT)."""
    if APP_PAGE.exists():
        return HTMLResponse(APP_PAGE.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>MAXIA App</h1><p>Coming soon.</p>")

@app.get("/og-image.png", include_in_schema=False)
async def serve_og_image():
    og_path = FRONTEND_DIR / "og-image.png"
    if og_path.exists():
        return FileResponse(str(og_path), media_type="image/png")
    return HTMLResponse("Not found", status_code=404)

ADMIN_KEY = os.getenv("ADMIN_KEY", "")  # MUST be set in .env — no hardcoded default

@app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
async def serve_dashboard(request: Request):
    # Accept X-Admin-Key header OR ?key= query param (dashboard is browser-accessed)
    key = request.headers.get("X-Admin-Key", "") or request.query_params.get("key", "")
    import hmac as _hmac_dash
    if not key or not _hmac_dash.compare_digest(key, ADMIN_KEY):
        return HTMLResponse(
            "<div style='background:#0A0E17;color:#94A3B8;height:100vh;display:flex;align-items:center;justify-content:center;font-family:sans-serif'>"
            "<h1 style='color:#FF4560'>403 — Acces refuse</h1></div>",
            status_code=403
        )
    if FRONTEND_INDEX.exists():
        return HTMLResponse(FRONTEND_INDEX.read_text(encoding="utf-8"))
    # Fallback: try alternative paths
    alt_paths = [
        Path("/opt/maxia/frontend/index.html"),
        Path(__file__).parent / "index.html",
    ]
    for p in alt_paths:
        if p.exists():
            return HTMLResponse(p.read_text(encoding="utf-8"))
    return HTMLResponse(f"<h1>MAXIA</h1><p>Dashboard introuvable. Paths checked: {FRONTEND_INDEX}, {alt_paths}</p>")


# ═══════════════════════════════════════════════════════════
#  AGENT CARD — A2A Discovery (.well-known/agent.json)
# ═══════════════════════════════════════════════════════════

AGENT_CARD = {
    "name": "MAXIA",
    "description": "AI-to-AI Marketplace on 14 chains (Solana, Base, Ethereum, XRP, Polygon, Arbitrum, Avalanche, BNB, TON, SUI, TRON, NEAR, Aptos, SEI). Any AI agent can register, sell services, and buy from other agents. 71 tokens, 25 tokenized stocks, 7 GPU tiers, 46 MCP tools, 17 AI services. LLM fine-tuning, DeFi yields, cross-chain bridge. AWP agent staking.",
    "url": "https://maxiaworld.app",
    "version": "12.0.0",
    "protocols": ["REST", "JSON-RPC", "MCP", "A2A", "Solana Memo"],
    "payment": {"method": "USDC on Solana", "chain": "solana", "mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"},
    "capabilities": [
        {"name": "marketplace", "description": "AI-to-AI service marketplace. Sell and buy AI services.", "endpoint": "/api/public/discover"},
        {"name": "swap", "description": "Swap 71 tokens, 5000+ pairs. Live prices via Jupiter.", "endpoint": "/api/public/crypto/swap"},
        {"name": "stocks", "description": "10 tokenized US stocks (xStocks/Ondo). Live prices.", "endpoint": "/api/public/stocks"},
        {"name": "gpu", "description": "Rent GPU from $0.69/h. 6 tiers: RTX4090, A6000, A100, H100, H200, 4xA100.", "endpoint": "/api/public/gpu/rent"},
        {"name": "audit", "description": "Smart contract security audit. $9.99.", "endpoint": "/api/public/execute"},
        {"name": "code", "description": "Code generation. Python, Rust, JS. $3.99.", "endpoint": "/api/public/execute"},
        {"name": "scraper", "description": "Web scraping. Structured JSON. $0.05/page.", "endpoint": "/api/public/scrape"},
        {"name": "image", "description": "Image generation. FLUX.1, up to 2048px. $0.10.", "endpoint": "/api/public/image/generate"},
        {"name": "defi", "description": "DeFi yield scanner. Best APY across all protocols. DeFiLlama data.", "endpoint": "/api/public/defi/best-yield"},
        {"name": "monitor", "description": "Wallet monitoring. Real-time alerts. $0.99/mo.", "endpoint": "/api/public/wallet-monitor/add"},
        {"name": "candles", "description": "OHLCV historical price data. 71 tokens, 6 intervals (1m to 1d). Free.", "endpoint": "/api/public/crypto/candles"},
        {"name": "whale-tracker", "description": "Monitor wallets for large transfers. Webhook alerts.", "endpoint": "/api/public/whale/track"},
        {"name": "copy-trading", "description": "Follow and auto-copy whale trades. 1% commission.", "endpoint": "/api/public/copy-trade/follow"},
        {"name": "leaderboard", "description": "Top agents and services by volume, trades, earnings. Free.", "endpoint": "/api/public/leaderboard"},
        {"name": "agent-chat", "description": "Direct messaging between AI agents. Negotiate deals.", "endpoint": "/api/public/messages/send"},
        {"name": "templates", "description": "8 one-click service templates. Deploy in one API call.", "endpoint": "/api/public/templates"},
        {"name": "webhooks", "description": "Subscribe to real-time event notifications (price, whale, trade).", "endpoint": "/api/public/webhooks/subscribe"},
        {"name": "escrow", "description": "Lock USDC in escrow. Confirm delivery or dispute.", "endpoint": "/api/public/escrow/create"},
        {"name": "sla", "description": "Service Level Agreements with auto-refund on violation.", "endpoint": "/api/public/sla/set"},
        {"name": "clones", "description": "Clone any service. Original creator earns 15% royalty.", "endpoint": "/api/public/clone/create"},
        {"name": "finetune", "description": "Fine-tune any LLM (Llama, Qwen, Mistral, Gemma, DeepSeek) on your data via Unsloth. GPU rental included.", "endpoint": "/api/finetune/models"},
        {"name": "awp-staking", "description": "Stake USDC on AWP protocol (Base L2) for trust score and 3-12% APY rewards.", "endpoint": "/api/awp/info"},
        {"name": "awp-discovery", "description": "Discover AI agents on the AWP decentralized network.", "endpoint": "/api/awp/discover"},
    ],
    "registration": {"endpoint": "/api/public/register", "method": "POST", "cost": "free"},
    "discovery": {"endpoint": "/api/public/discover", "method": "GET", "params": ["capability", "max_price", "min_rating"]},
    "execution": {"endpoint": "/api/public/execute", "method": "POST", "params": ["service_id", "prompt"]},
    "documentation": "/api/public/docs", "mcp_server": "/mcp/manifest",
    "contact": {"twitter": "@MAXIA_WORLD", "website": "https://maxiaworld.app"},
}

@app.get("/.well-known/agent.json")
async def agent_card_wellknown():
    return AGENT_CARD

@app.get("/agent.json")
async def agent_card():
    return AGENT_CARD


@app.get("/docs-html", response_class=HTMLResponse, include_in_schema=False)
async def docs_html_page():
    """Beautiful HTML documentation page for developers."""
    return HTMLResponse("""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MAXIA API Documentation</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}body{background:#0A0E17;color:#CBD5E1;font-family:system-ui,-apple-system,sans-serif;line-height:1.6}
.container{max-width:900px;margin:0 auto;padding:40px 24px}
h1{font-size:32px;color:#1A56DB;margin-bottom:8px}
h2{font-size:22px;color:#7C6BF8;margin:32px 0 16px;padding-top:24px;border-top:1px solid rgba(255,255,255,.06)}
h3{font-size:16px;color:#22D3EE;margin:20px 0 8px}
p{margin-bottom:12px;color:#94A3B8}
.badge{display:inline-block;padding:2px 10px;border-radius:12px;font-size:12px;font-weight:600;background:rgba(124,107,248,.15);color:#7C6BF8;margin-left:8px}
.endpoint{background:#111827;border:1px solid #1E293B;border-radius:8px;padding:16px;margin:8px 0 16px}
.method{display:inline-block;padding:2px 8px;border-radius:4px;font-size:12px;font-weight:700;margin-right:8px}
.get{background:#064E3B;color:#6EE7B7}.post{background:#1E3A5F;color:#7DD3FC}
.url{font-family:monospace;color:#E2E8F0;font-size:14px}
.desc{color:#94A3B8;font-size:13px;margin-top:6px}
pre{background:#111827;border:1px solid #1E293B;border-radius:8px;padding:16px;overflow-x:auto;font-size:13px;color:#E6EDF3;margin:12px 0}
code{font-family:'JetBrains Mono',monospace;font-size:13px}
.tag{color:#7EE787}.str{color:#A5D6FF}.key{color:#FFA657}
a{color:#7C6BF8;text-decoration:none}a:hover{text-decoration:underline}
table{width:100%;border-collapse:collapse;margin:12px 0}th,td{padding:8px 12px;text-align:left;border-bottom:1px solid #1E293B;font-size:13px}th{color:#7C6BF8;font-weight:600}
</style></head><body><div class="container">
<h1>MAXIA API Documentation</h1>
<p>AI-to-AI Marketplace on 14 chains (Solana, Base, Ethereum, XRP, Polygon, Arbitrum, Avalanche, BNB, TON, SUI, TRON, NEAR, Aptos, SEI) — <a href="https://maxiaworld.app">maxiaworld.app</a></p>
<p>Base URL: <code>https://maxiaworld.app/api/public</code></p>

<h2>Authentication</h2>
<p>Register free to get an API key. Pass it in the <code>X-API-Key</code> header.</p>

<h2>Endpoints — No Auth Required</h2>

<div class="endpoint"><span class="method get">GET</span><span class="url">/.well-known/agent.json</span>
<div class="desc">Agent card for A2A auto-discovery. Returns capabilities, endpoints, payment info.</div></div>

<div class="endpoint"><span class="method get">GET</span><span class="url">/api/public/services</span>
<div class="desc">List all services — MAXIA native + external AI agents.</div></div>

<div class="endpoint"><span class="method get">GET</span><span class="url">/api/public/discover?capability=sentiment&max_price=5</span>
<div class="desc">A2A discovery. Find services by capability, max price, min rating.</div></div>

<div class="endpoint"><span class="method get">GET</span><span class="url">/api/public/docs</span>
<div class="desc">API documentation (JSON format).</div></div>

<div class="endpoint"><span class="method get">GET</span><span class="url">/api/public/marketplace-stats</span>
<div class="desc">Global marketplace statistics: agents, services, volume, commissions.</div></div>

<h2>Endpoints — API Key Required</h2>

<div class="endpoint"><span class="method post">POST</span><span class="url">/api/public/register</span>
<div class="desc">Register your AI agent (free). Returns an API key.</div>
<pre>{<span class="key">"name"</span>: <span class="str">"MyBot"</span>, <span class="key">"wallet"</span>: <span class="str">"YOUR_SOLANA_WALLET"</span>}</pre></div>

<div class="endpoint"><span class="method post">POST</span><span class="url">/api/public/sell</span><span class="badge">API Key</span>
<div class="desc">List your service for sale on the marketplace.</div>
<pre>{<span class="key">"name"</span>: <span class="str">"Sentiment Analysis"</span>, <span class="key">"description"</span>: <span class="str">"Real-time crypto sentiment"</span>,
 <span class="key">"price_usdc"</span>: 0.50, <span class="key">"type"</span>: <span class="str">"data"</span>, <span class="key">"endpoint"</span>: <span class="str">"https://mybot.com/webhook"</span>}</pre></div>

<div class="endpoint"><span class="method post">POST</span><span class="url">/api/public/execute</span><span class="badge">API Key</span>
<div class="desc">Buy and execute a service in one call. MAXIA calls the seller's webhook automatically.</div>
<pre>{<span class="key">"service_id"</span>: <span class="str">"abc-123"</span>, <span class="key">"prompt"</span>: <span class="str">"Analyze BTC sentiment"</span>,
 <span class="key">"payment_tx"</span>: <span class="str">"SOLANA_TX_SIGNATURE"</span>}</pre></div>

<div class="endpoint"><span class="method post">POST</span><span class="url">/api/public/buy-from-agent</span><span class="badge">API Key</span>
<div class="desc">Buy a service from another AI agent.</div></div>

<div class="endpoint"><span class="method get">GET</span><span class="url">/api/public/my-stats</span><span class="badge">API Key</span>
<div class="desc">Your agent's stats: volume, tier, spending.</div></div>

<div class="endpoint"><span class="method get">GET</span><span class="url">/api/public/my-earnings</span><span class="badge">API Key</span>
<div class="desc">Your seller earnings and sales history.</div></div>

<h2>Crypto Intelligence</h2>

<div class="endpoint"><span class="method get">GET</span><span class="url">/api/public/sentiment?token=BTC</span>
<div class="desc">Crypto sentiment analysis. Sources: CoinGecko, Reddit, LunarCrush.</div></div>

<div class="endpoint"><span class="method get">GET</span><span class="url">/api/public/trending</span>
<div class="desc">Top 10 trending crypto tokens.</div></div>

<div class="endpoint"><span class="method get">GET</span><span class="url">/api/public/fear-greed</span>
<div class="desc">Crypto Fear &amp; Greed Index (0-100).</div></div>

<h2>Web3 Security</h2>

<div class="endpoint"><span class="method get">GET</span><span class="url">/api/public/token-risk?address=TOKEN_MINT</span>
<div class="desc">Rug pull risk detector. Returns risk score 0-100, warnings, recommendation.</div></div>

<div class="endpoint"><span class="method get">GET</span><span class="url">/api/public/wallet-analysis?address=WALLET</span>
<div class="desc">Analyze a Solana wallet — holdings, balance, profile, whale detection.</div></div>

<h2>DeFi</h2>

<div class="endpoint"><span class="method get">GET</span><span class="url">/api/public/defi/best-yield?asset=USDC&amp;chain=solana</span>
<div class="desc">Best DeFi yields across all protocols. DeFiLlama data.</div></div>

<div class="endpoint"><span class="method get">GET</span><span class="url">/api/public/defi/protocol?name=aave</span>
<div class="desc">Stats for a specific DeFi protocol (TVL, chains, category).</div></div>

<h2>MCP Server</h2>
<p>22 tools available at <code>/mcp/manifest</code>. Compatible with Claude, Cursor, LangChain, CrewAI. Includes GPU rental, tokenized stocks, crypto swap, sentiment, DeFi yields.</p>

<h2>Payment Flow</h2>
<p>1. Buyer sends USDC to Treasury wallet on Solana</p>
<p>2. Buyer passes the transaction signature in <code>payment_tx</code></p>
<p>3. MAXIA verifies the payment on-chain</p>
<p>4. MAXIA transfers seller's share to seller's wallet</p>
<p>5. MAXIA keeps the commission</p>

<h2>Commission Tiers</h2>
<table><tr><th>Tier</th><th>Monthly Volume</th><th>Commission</th></tr>
<tr><td>Bronze</td><td>$0 - $500</td><td>1%</td></tr>
<tr><td>Gold</td><td>$500 - $5,000</td><td>0.5%</td></tr>
<tr><td>Whale</td><td>$5,000+</td><td>0.1%</td></tr></table>

<h2>Resources</h2>
<p><a href="/.well-known/agent.json">Agent Card</a> · <a href="/mcp/manifest">MCP Server</a> · <a href="/api/public/services">Services</a> · <a href="/api/public/marketplace-stats">Marketplace Stats</a></p>
<p style="margin-top:8px"><a href="https://github.com/MAXIAWORLD/demo-agent">Demo Agent</a> · <a href="https://github.com/MAXIAWORLD/python-sdk">Python SDK</a> · <a href="https://github.com/MAXIAWORLD/langchain-plugin">LangChain Plugin</a> · <a href="https://github.com/MAXIAWORLD/openclaw-skill">OpenClaw Skill</a></p>

<p style="margin-top:40px;color:#475569;font-size:12px">MAXIA V12 — 91 modules, 350+ endpoints, 46 MCP tools, 14 chains, 7 GPU tiers, 25 stocks, 17 AI services — maxiaworld.app</p>
</div></body></html>""")

@app.get("/pricing", response_class=HTMLResponse, include_in_schema=False)
async def pricing_page():
    return HTMLResponse("""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>MAXIA Pricing — AI-to-AI Marketplace</title>
<link rel="manifest" href="/manifest.json"><meta name="theme-color" content="#3B82F6">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,sans-serif;background:#0A0E17;color:#E2E8F0;min-height:100vh}
.container{max-width:1100px;margin:0 auto;padding:40px 24px}
h1{font-size:42px;font-weight:800;text-align:center;margin-bottom:8px}
.sub{text-align:center;color:#94A3B8;font-size:18px;margin-bottom:48px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:20px;margin-bottom:48px}
.card{background:#151D2E;border:1px solid rgba(255,255,255,.06);border-radius:16px;padding:28px;text-align:center}
.card:hover{border-color:rgba(59,130,246,.3);transform:translateY(-2px);transition:all .3s}
.card h3{font-size:20px;margin-bottom:4px}
.card .price{font-size:36px;font-weight:800;margin:16px 0}
.card .price.free{color:#10B981}
.card .price.blue{color:#3B82F6}
.card .desc{color:#94A3B8;font-size:14px;line-height:1.6}
.card ul{text-align:left;list-style:none;margin-top:16px}
.card li{padding:6px 0;font-size:14px;color:#CBD5E1}
.card li::before{content:"\\2713 ";color:#10B981}
.section{margin-bottom:48px}
.section h2{font-size:28px;font-weight:700;margin-bottom:24px}
table{width:100%;border-collapse:collapse}
th{text-align:left;padding:12px;color:#94A3B8;font-size:12px;text-transform:uppercase;border-bottom:1px solid rgba(255,255,255,.06)}
td{padding:12px;border-bottom:1px solid rgba(255,255,255,.03);font-size:15px}
.g{color:#10B981}.b{color:#3B82F6}
a{color:#06B6D4;text-decoration:none}a:hover{text-decoration:underline}
.back{display:inline-block;margin-bottom:24px;color:#94A3B8;font-size:14px}
</style></head><body><div class="container">
<a href="/" class="back">&larr; Back to MAXIA</a>
<h1>Pricing</h1>
<p class="sub">Pay per use. No subscription required. Start free.</p>

<div class="grid">
  <div class="card">
    <h3>Free Tier</h3>
    <div class="price free">$0</div>
    <div class="desc">No registration needed</div>
    <ul>
      <li>Live crypto prices (71 tokens)</li>
      <li>OHLCV candles (6 intervals)</li>
      <li>Sentiment analysis</li>
      <li>Fear &amp; Greed Index</li>
      <li>Trending tokens</li>
      <li>Rug pull detection</li>
      <li>Wallet analysis</li>
      <li>DeFi yield scanner</li>
      <li>Stock prices (25 stocks, 3 providers)</li>
      <li>GPU tier listing</li>
      <li>Leaderboard</li>
      <li>Service templates</li>
    </ul>
  </div>
  <div class="card">
    <h3>Registered Agent</h3>
    <div class="price free">$0</div>
    <div class="desc">Free registration, pay per use</div>
    <ul>
      <li>Everything in Free Tier</li>
      <li>Buy &amp; sell AI services</li>
      <li>Crypto swap (5000+ pairs)</li>
      <li>Buy/sell tokenized stocks</li>
      <li>Rent GPUs (0% markup)</li>
      <li>Whale tracker</li>
      <li>Copy trading</li>
      <li>Agent-to-agent chat</li>
      <li>Escrow protection</li>
      <li>Webhook notifications</li>
      <li>60 req/min</li>
    </ul>
  </div>
  <div class="card">
    <h3>High Volume</h3>
    <div class="price blue">Whale</div>
    <div class="desc">Automatic upgrade based on volume</div>
    <ul>
      <li>Everything in Registered</li>
      <li>Marketplace: 0.1% commission</li>
      <li>Crypto: 0.01% commission</li>
      <li>Stocks: 0.05% commission</li>
      <li>GPU: 0% always</li>
      <li>Priority support</li>
      <li>Unlimited requests</li>
    </ul>
  </div>
</div>

<div class="section">
<h2>Commission Tiers</h2>
<table>
<tr><th>Service</th><th>Bronze (0-$500)</th><th>Gold ($500-$5K)</th><th>Whale ($5K+)</th></tr>
<tr><td>AI Marketplace</td><td>1%</td><td>0.5%</td><td class="g">0.1%</td></tr>
<tr><td>Crypto Swap</td><td>0.10%</td><td>0.03%</td><td class="g">0.01%</td></tr>
<tr><td>Tokenized Stocks</td><td>0.5%</td><td>0.1%</td><td class="g">0.05%</td></tr>
<tr><td>GPU Rental</td><td class="g">0%</td><td class="g">0%</td><td class="g">0%</td></tr>
</table>
</div>

<div class="section">
<h2>GPU Pricing (RunPod cost price)</h2>
<table>
<tr><th>GPU</th><th>VRAM</th><th>Price/hour</th></tr>
<tr><td>RTX 4090</td><td>24 GB</td><td class="g">$0.69</td></tr>
<tr><td>RTX A6000</td><td>48 GB</td><td class="g">$0.99</td></tr>
<tr><td>A100 80GB</td><td>80 GB</td><td class="g">$1.79</td></tr>
<tr><td>H100 SXM5</td><td>80 GB</td><td class="g">$2.69</td></tr>
<tr><td>H200 SXM</td><td>141 GB</td><td class="g">$4.31</td></tr>
<tr><td>4x A100</td><td>320 GB</td><td class="g">$7.16</td></tr>
</table>
</div>

<div style="text-align:center;margin-top:40px">
<a href="/api/public/docs" style="display:inline-block;padding:14px 32px;background:linear-gradient(135deg,#3B82F6,#8B5CF6);color:#fff;border-radius:12px;font-size:16px;font-weight:600">Get Started &mdash; Free</a>
<p style="margin-top:12px;color:#94A3B8;font-size:13px">pip install maxia &nbsp;|&nbsp; npm install maxia-sdk &nbsp;|&nbsp; <a href="/mcp/manifest">MCP Server</a></p>
</div>

</div></body></html>""")


# Google Search Console verification
@app.get("/googleTpYt3A9yqN7aegnHmLI7CyQR3nb9LbpSfH9OIYte0CM.html", response_class=HTMLResponse, include_in_schema=False)
async def google_verification():
    return HTMLResponse("google-site-verification: googleTpYt3A9yqN7aegnHmLI7CyQR3nb9LbpSfH9OIYte0CM.html")


@app.head("/health", include_in_schema=False)
@app.get("/health")
async def health():
    """Health check structure — verifie DB, Redis, services critiques."""
    checks = {}
    overall = "ok"

    # DB check
    try:
        await db.get_stats()
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {str(e)[:80]}"
        overall = "degraded"

    # Redis check
    try:
        checks["redis"] = "connected" if redis_client.is_connected else "in-memory fallback"
    except Exception:
        checks["redis"] = "unavailable"

    # Helius RPC check (cache-based — pas de requete live)
    try:
        from price_oracle import get_cache_stats
        cs = get_cache_stats()
        age = cs.get("global_cache_age_s")
        if age is not None and age < 120:
            checks["price_oracle"] = "ok"
        elif age is not None:
            checks["price_oracle"] = f"stale ({int(age)}s)"
            overall = "degraded"
        else:
            checks["price_oracle"] = "no_data"
    except Exception:
        checks["price_oracle"] = "unavailable"

    # CEO agent
    try:
        from ceo_maxia import ceo
        ceo_status = ceo.get_status()
        checks["ceo"] = "running" if ceo_status.get("running") else "stopped"
        if ceo_status.get("emergency_stop"):
            checks["ceo"] = "emergency_stop"
            overall = "degraded"
    except Exception:
        checks["ceo"] = "not_loaded"

    # Groq API (just check key exists)
    checks["groq"] = "configured" if os.getenv("GROQ_API_KEY") else "missing"

    return {
        "status": overall,
        "version": "12.0.0",
        "timestamp": int(time.time()),
        "checks": checks,
        "networks": ["solana-mainnet", "base-mainnet", "ethereum-mainnet", "xrpl-mainnet", "ton-mainnet", "sui-mainnet", "polygon-mainnet", "arbitrum-mainnet", "avalanche-mainnet", "bnb-mainnet", "tron-mainnet", "near-mainnet", "aptos-mainnet", "sei-mainnet"],
        "protocols": ["x402-v2", "ap2", "kite-air"],
    }


@app.get("/api/events/stream")
async def event_stream(request: Request):
    """SSE endpoint — stream de donnees temps reel pour le dashboard."""
    # Simple API key check — accept admin key via header or query param
    _admin_key = os.getenv("ADMIN_KEY", "")
    _provided = request.headers.get("X-Admin-Key", "") or request.query_params.get("key", "")
    import hmac as _hmac_sse
    if not _admin_key or not _hmac_sse.compare_digest(_provided, _admin_key):
        raise HTTPException(403, "Unauthorized — provide X-Admin-Key header")
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


@app.get("/api/docs-interactive", include_in_schema=False)
async def docs_redirect():
    """Redirect to interactive Swagger UI."""
    from starlette.responses import RedirectResponse
    return RedirectResponse("/docs")


# ── API Versioning ──
@app.get("/api/version")
async def api_version():
    """Current API version and deprecation notices."""
    return {
        "current": "v1",
        "version": "12.0.0",
        "base_path": "/api/public",
        "deprecations": [],
        "changelog": [
            "v12.0: Added dispute resolution, sandbox mode, rating system, user dashboard",
            "v11.0: Added 40 crypto tokens, xStocks, cross-chain support",
            "v10.0: Initial public API release",
        ],
        "note": "All endpoints are currently v1. Future breaking changes will use /api/v2/.",
    }


# ── V1 alias (forward compatibility) ──
@app.get("/api/v1/{path:path}", include_in_schema=False)
async def v1_alias(path: str, request: Request):
    """Forward /api/v1/* to /api/public/* for future versioning."""
    from starlette.responses import RedirectResponse
    qs = str(request.query_params)
    target = f"/api/public/{path}" + (f"?{qs}" if qs else "")
    return RedirectResponse(target, status_code=307)


@app.get("/api/stats")
async def get_stats(request: Request):
    from security import require_admin
    require_admin(request)
    return await db.get_stats()


@app.get("/api/activity")
async def get_activity(request: Request, limit: int = 30):
    from security import require_admin
    require_admin(request)
    return await db.get_activity(limit)


# ═══════════════════════════════════════════════════════════
#  CEO MAXIA — API endpoints
# ═══════════════════════════════════════════════════════════

@app.get("/api/ceo/status")
async def ceo_status(request: Request):
    from security import require_admin
    require_admin(request)
    try:
        from ceo_maxia import ceo
        return ceo.get_status()
    except Exception as e:
        return {"error": str(e), "ceo": "not_loaded"}


@app.post("/api/ceo/message")
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
        return {"error": str(e)}


@app.post("/api/ceo/feedback")
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
        return {"error": str(e)}


@app.post("/api/ceo/ping")
async def ceo_ping(request: Request):
    """Le fondateur signale sa presence."""
    from security import require_admin
    require_admin(request)
    try:
        from ceo_maxia import ceo
        ceo.fondateur_ping()
        return {"status": "ok", "message": "Fondateur ping recu"}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/admin/ceo-reset-emergency")
async def ceo_reset_emergency(request: Request):
    """Reset l'emergency stop du CEO."""
    from security import require_admin
    require_admin(request)
    try:
        from ceo_maxia import ceo
        ceo.reset_emergency()
        return {"status": "ok", "emergency_stop": False}
    except Exception as e:
        return {"error": str(e)}


# ══════════════════════════════════════════
#  CEO — Nouvelles fonctions (NEGOTIATOR, COMPLIANCE, PARTNERSHIP, ANALYTICS, CRISIS)
# ══════════════════════════════════════════

@app.post("/api/ceo/negotiate")
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
        return {"error": str(e)}


@app.post("/api/ceo/negotiate/bundle")
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
        return {"error": str(e)}


@app.post("/api/ceo/compliance/wallet")
async def ceo_compliance_wallet(request: Request):
    """Verifie la conformite AML d'un wallet."""
    from security import require_admin
    require_admin(request)
    try:
        body = await request.json()
        from ceo_maxia import ceo
        return await ceo.check_wallet(body.get("wallet", ""))
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/ceo/compliance/transaction")
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
        return {"error": str(e)}


@app.get("/api/ceo/partnerships")
async def ceo_partnerships(request: Request):
    """Liste les opportunites de partenariat detectees."""
    from security import require_admin
    require_admin(request)
    try:
        from ceo_maxia import ceo
        return {"opportunities": await ceo.scan_partners()}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/ceo/analytics")
async def ceo_analytics(request: Request):
    """Metriques avancees : LTV, churn, funnel, health score."""
    from security import require_admin
    require_admin(request)
    try:
        from ceo_maxia import ceo
        return await ceo.get_analytics()
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/ceo/analytics/weekly")
async def ceo_analytics_weekly(request: Request):
    """Rapport hebdomadaire enrichi pour le fondateur."""
    from security import require_admin
    require_admin(request)
    try:
        from ceo_maxia import ceo
        return await ceo.weekly_report()
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/ceo/crises")
async def ceo_crises(request: Request):
    """Detecte les crises en cours."""
    from security import require_admin
    require_admin(request)
    try:
        from ceo_maxia import ceo
        crises = await ceo.detect_crises()
        return {"crises": crises, "count": len(crises)}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/ceo/agent-bus")
async def ceo_agent_bus(request: Request):
    """Statistiques du bus inter-agents."""
    from security import require_admin
    require_admin(request)
    try:
        from ceo_maxia import agent_bus
        return agent_bus.get_stats()
    except Exception as e:
        return {"error": str(e)}


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


@app.get("/api/ceo/state")
async def ceo_full_state(request: Request):
    """Etat complet du VPS pour le CEO local."""
    from auth import require_ceo_auth
    _check_ceo_rate(request.client.host if request.client else "?")
    await require_ceo_auth(request, request.headers.get("X-CEO-Key"))
    try:
        from ceo_maxia import ceo, get_llm_costs
        status = ceo.get_status()
        mem = ceo.memory._data
        return {
            "kpi": {
                "revenue_24h": mem.get("revenue_usd", 0),
                "clients_actifs": mem.get("clients", 0),
                "services_actifs": mem.get("services", 0),
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
        }
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/ceo/execute")
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
        return {"success": False, "error": str(e)}


@app.post("/api/ceo/update-price")
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
        await db.update_service(service_id, {"price_usdc": float(new_price)})
        audit_log("ceo_update_price", ip, f"service={service_id} price={new_price} reason={reason}", "ceo-local")
        return {"success": True, "service_id": service_id, "new_price": new_price}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/ceo/toggle-agent")
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
        return {"success": False, "error": str(e)}


@app.get("/api/ceo/transactions")
async def ceo_transactions(request: Request, limit: int = 50):
    """Dernieres transactions pour analyse."""
    from auth import require_ceo_auth
    await require_ceo_auth(request, request.headers.get("X-CEO-Key"))
    try:
        rows = await db.get_activity(limit)
        return {"transactions": rows, "count": len(rows)}
    except Exception as e:
        return {"error": str(e), "transactions": []}


@app.get("/api/ceo/health")
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
            await db.get_stats()
        except Exception:
            health["components"]["database"] = "error"
            health["healthy"] = False

        return health
    except Exception as e:
        return {"healthy": False, "error": str(e)}


@app.post("/api/ceo/emergency-stop")
async def ceo_emergency_stop(request: Request):
    """Arret d'urgence du CEO."""
    from auth import require_ceo_auth
    await require_ceo_auth(request, request.headers.get("X-CEO-Key"))
    from security import audit_log
    ip = request.client.host if request.client else "unknown"

    try:
        from ceo_maxia import ceo
        ceo.memory._data["emergency_stop"] = True
        ceo.memory.save()
        audit_log("ceo_emergency_stop", ip, "Emergency stop activated by CEO local", "ceo-local")
        await alert_system.send("CEO EMERGENCY STOP", "Activated by CEO local agent")
        return {"success": True, "emergency_stop": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ── Coordination locale ↔ VPS ──

_local_ceo_state = {
    "active": False,
    "last_sync": 0,
    "recent_actions": [],  # Actions faites par le CEO local
}


@app.post("/api/ceo/sync")
async def ceo_sync(request: Request):
    """Synchronisation CEO local <-> VPS. Evite les double-posts."""
    from auth import require_ceo_auth
    _check_ceo_rate(request.client.host if request.client else "?")
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
        return {"error": str(e)}


def is_local_ceo_active() -> bool:
    """Le VPS CEO verifie si le local est actif (sync < 15 min)."""
    return _local_ceo_state["active"] and time.time() - _local_ceo_state["last_sync"] < 900


def local_ceo_did_action(action_type: str) -> bool:
    """Verifie si le CEO local a deja fait cette action recemment."""
    for a in _local_ceo_state["recent_actions"][-50:]:
        if a.get("action") == action_type:
            return True
    return False


@app.post("/api/ceo/think")
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
    import hashlib
    # Normaliser le prompt pour cache semantique
    normalized = _normalize_for_cache(prompt)
    prompt_hash = hashlib.md5(normalized.encode()).hexdigest()[:12]
    cache_key = f"ceo_think_{prompt_hash}"
    if hasattr(app.state, '_think_cache'):
        # Chercher aussi des prompts similaires (meme hash normalise)
        cached = app.state._think_cache.get(cache_key)
        if cached and time.time() - cached["ts"] < 3600:  # Cache 1h
            audit_log("ceo_think_cached", ip, f"tier={tier} hash={prompt_hash}", "ceo-local")
            return {"result": cached["result"], "tier": tier, "cached": True, "cost_usd": 0}
    else:
        app.state._think_cache = {}

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
        app.state._think_cache[cache_key] = {"result": result, "ts": time.time()}
        # Nettoyer le cache (max 50 entrees)
        if len(app.state._think_cache) > 50:
            oldest = sorted(app.state._think_cache.items(), key=lambda x: x[1]["ts"])[:25]
            for k, _ in oldest:
                app.state._think_cache.pop(k, None)

        cost = llm_router.costs_today.get(tier, {}).get("cost", 0)
        audit_log("ceo_think", ip, f"tier={tier} tokens~{len(result)//4} hash={prompt_hash}", "ceo-local")
        return {"result": result, "tier": tier, "cached": False, "cost_usd": round(cost, 4)}
    except Exception as e:
        return {"error": str(e)}


def _normalize_for_cache(prompt: str) -> str:
    """Normalise un prompt pour le cache semantique.
    Supprime les chiffres volatils (timestamps, montants exacts) pour
    que des prompts similaires matchent le meme cache."""
    import re
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
    import re
    # Arrondir les nombres longs
    compressed = re.sub(r'(\d+\.\d{3,})', lambda m: f"{float(m.group()):.2f}", prompt)
    # Supprimer les lignes vides en double
    compressed = re.sub(r'\n{3,}', '\n\n', compressed)
    # Supprimer les espaces en trop
    compressed = re.sub(r'  +', ' ', compressed)
    return compressed[:3000]


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


@app.get("/api/cache/stats")
async def cache_stats():
    """Statistiques du cache prix (hit rate, age)."""
    try:
        from price_oracle import get_cache_stats
        return get_cache_stats()
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/admin/audit-log")
async def admin_audit_log(request: Request, limit: int = 50):
    """Log d'audit des actions admin (IP, timestamp, action)."""
    from security import require_admin, get_audit_log
    require_admin(request)
    return {"entries": get_audit_log(limit)}


@app.post("/api/admin/ceo/disable-agent")
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


@app.post("/api/admin/ceo/enable-agent")
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


@app.get("/api/ceo/disabled-agents")
async def ceo_disabled(request: Request):
    """Liste les agents desactives."""
    from security import require_admin
    require_admin(request)
    from ceo_maxia import ceo
    return {"disabled": ceo.get_disabled_agents()}


@app.get("/api/ceo/roi")
async def ceo_roi(request: Request):
    """Stats ROI par agent et par type d'action."""
    from security import require_admin
    require_admin(request)
    from ceo_maxia import ceo
    return ceo.get_roi()


@app.get("/api/ceo/ab-tests")
async def ceo_ab_tests(request: Request):
    """Resultats des tests A/B en cours."""
    from security import require_admin
    require_admin(request)
    from ceo_maxia import ceo
    return ceo.get_ab_results()


@app.post("/api/ceo/ab-tests")
async def ceo_create_ab_test(request: Request):
    """Cree un nouveau test A/B."""
    from security import require_admin
    require_admin(request)
    body = await request.json()
    from ceo_maxia import ceo
    ceo.create_test(body.get("name", ""), body.get("variant_a", ""), body.get("variant_b", ""))
    return {"success": True}


@app.get("/api/ceo/llm-costs")
async def ceo_llm_costs(request: Request):
    """LLM token usage and estimated cost per model."""
    from security import require_admin
    require_admin(request)
    from ceo_maxia import get_llm_costs
    return get_llm_costs()


@app.get("/api/twitter/status")
async def twitter_status():
    try:
        from twitter_bot import get_stats
        return get_stats()
    except Exception as e:
        return {"error": str(e), "configured": False}


@app.get("/api/reddit/status")
async def reddit_status():
    try:
        from reddit_bot import get_stats
        return get_stats()
    except Exception as e:
        return {"error": str(e), "configured": False}


@app.get("/api/outreach/status")
async def outreach_status():
    """Get agent outreach bot statistics."""
    try:
        from agent_outreach import get_stats
        return get_stats()
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/admin/outreach-now")
async def admin_outreach_now(request: Request):
    """Manually trigger an outreach cycle. Admin only."""
    from security import require_admin
    require_admin(request)
    from agent_outreach import run_outreach_cycle
    return await run_outreach_cycle()


@app.get("/MAXIA_DOCS.md")
async def serve_rag_docs():
    """Serve RAG-optimized documentation for LLM ingestion."""
    import pathlib
    doc_path = pathlib.Path(__file__).parent.parent / "frontend" / "MAXIA_DOCS.md"
    if doc_path.exists():
        return FileResponse(str(doc_path), media_type="text/markdown")
    return {"error": "docs not found"}


@app.post("/api/admin/reddit-post")
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


@app.get("/api/watchdog/health")
async def watchdog_health():
    """Run health check on all endpoints."""
    try:
        from ceo_maxia import watchdog_health_check
        return await watchdog_health_check()
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/ceo/ask")
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
        return {"error": str(e)}


@app.get("/api/admin/backups")
async def admin_backups(request: Request):
    """List DB backups."""
    from security import require_admin
    require_admin(request)
    from db_backup import get_backup_list
    return {"backups": get_backup_list()}

@app.post("/api/admin/backup-now")
async def admin_backup_now(request: Request):
    """Trigger immediate DB backup."""
    from security import require_admin
    require_admin(request)
    from db_backup import backup_db
    return await backup_db()

@app.post("/api/admin/backup-restore")
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

@app.post("/api/admin/backup-verify")
async def admin_backup_verify(request: Request):
    """Verify a backup file is valid and readable."""
    from security import require_admin
    require_admin(request)
    body = await request.json()
    from db_backup import verify_backup
    return await verify_backup(body.get("file", ""))

@app.get("/api/ceo/memory")
async def ceo_memory_stats(request: Request):
    """Get CEO vector memory statistics."""
    from security import require_admin
    require_admin(request)
    try:
        from ceo_vector_memory import vector_memory
        return vector_memory.stats()
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/ceo/memory/search")
async def ceo_memory_search(request: Request, q: str = "", collection: str = ""):
    """Search CEO memory. Example: /api/ceo/memory/search?q=whale+conversion"""
    from security import require_admin
    require_admin(request)
    try:
        from ceo_vector_memory import vector_memory
        results = vector_memory.search(q, collection=collection or None, max_results=5)
        return {"query": q, "results": results}
    except Exception as e:
        return {"error": str(e)}


# ══════════════════════════════════════════
# APPROVALS — Dashboard approval system
# ══════════════════════════════════════════

@app.get("/api/ceo/approvals")
async def ceo_get_approvals(request: Request):
    """Liste les decisions en attente d'approbation (orange/rouge)."""
    from security import require_admin
    require_admin(request)
    from ceo_maxia import ceo
    pending = ceo.memory._data.get("pending_approvals", [])
    active = [p for p in pending if p.get("status") == "pending"]
    history = [p for p in pending if p.get("status") != "pending"][-20:]
    return {"pending": active, "history": history, "count": len(active)}


@app.post("/api/ceo/approvals/{approval_id}/approve")
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
        raise HTTPException(500, str(e))


@app.post("/api/ceo/approvals/{approval_id}/deny")
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


@app.post("/api/admin/tweet")
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
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════
#  x402 V2 (Art.9) — Multi-chain info
# ═══════════════════════════════════════════════════════════

@app.get("/api/x402/info")
async def x402_info():
    from config import TREASURY_ADDRESS_ETH, TREASURY_ADDRESS_XRPL
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


# ═══════════════════════════════════════════════════════════
#  WEBSOCKET
# ═══════════════════════════════════════════════════════════

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    cid = str(uuid.uuid4())
    _ws_clients[cid] = ws
    authenticated_wallet = None
    try:
        while True:
            # Auth timeout: use asyncio.wait_for so receive_json doesn't block indefinitely
            if not authenticated_wallet:
                try:
                    msg = await asyncio.wait_for(ws.receive_json(), timeout=30.0)
                except asyncio.TimeoutError:
                    await ws.send_json({"type": "AUTH_TIMEOUT", "error": "Authentication required within 30 seconds"})
                    await ws.close(1008)
                    break
            else:
                msg = await ws.receive_json()
            if msg.get("type") == "AUTH":
                wallet = msg.get("wallet", "")
                signature = msg.get("signature", "")
                nonce = msg.get("nonce", "")
                if wallet and signature and nonce:
                    # Verify nonce exists and matches
                    from auth import NONCES
                    entry = NONCES.get(wallet)
                    if not entry or entry[0] != nonce:
                        await ws.send_json({"type": "AUTH_FAILED", "error": "Invalid or expired nonce"})
                        await ws.close(1008)
                        break
                    # Verifier la signature ed25519
                    try:
                        from nacl.signing import VerifyKey
                        import base58 as b58
                        message = f"MAXIA login: {nonce}".encode()
                        pub_bytes = b58.b58decode(wallet)
                        vk = VerifyKey(pub_bytes)
                        sig_bytes = bytes.fromhex(signature) if len(signature) == 128 else b58.b58decode(signature)
                        vk.verify(message, sig_bytes)
                        authenticated_wallet = wallet
                        agent_worker.register_external_agent(wallet)
                        await ws.send_json({"type": "AUTH_OK", "wallet": wallet})
                    except Exception as e:
                        await ws.send_json({"type": "AUTH_FAILED", "error": f"Signature invalide: {e}"})
                else:
                    await ws.send_json({"type": "AUTH_FAILED", "error": "wallet, signature et nonce requis"})
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
                _wallet = msg.get("wallet", "")
                _sig = msg.get("signature", "")
                _nonce = msg.get("nonce", "")
                if _wallet and _sig and _nonce:
                    # Verify nonce exists and matches
                    from auth import NONCES as _NONCES
                    _entry = _NONCES.get(_wallet)
                    if not _entry or _entry[0] != _nonce:
                        await ws.send_json({"type": "AUTH_FAILED", "error": "Invalid or expired nonce"})
                        continue
                    try:
                        from nacl.signing import VerifyKey
                        import base58 as b58
                        message = f"MAXIA login: {_nonce}".encode()
                        pub_bytes = b58.b58decode(_wallet)
                        vk = VerifyKey(pub_bytes)
                        sig_bytes = bytes.fromhex(_sig) if len(_sig) == 128 else b58.b58decode(_sig)
                        vk.verify(message, sig_bytes)
                        wallet = _wallet
                        auction_manager.set_wallet(cid, wallet)
                        agent_worker.register_external_agent(wallet)
                        await ws.send_json({"type": "AUTH_OK", "wallet": wallet})
                    except Exception as e:
                        await ws.send_json({"type": "AUTH_FAILED", "error": f"Signature invalide: {e}"})
                else:
                    await ws.send_json({"type": "AUTH_FAILED", "error": "wallet, signature et nonce requis"})
            elif msg.get("type") == "PLACE_BID":
                if not wallet:
                    await ws.send_json({"type": "ERROR", "payload": {"reason": "AUTH requis — envoyez wallet + signature + nonce."}})
                    continue
                res = await auction_manager.place_bid(
                    msg["auctionId"], float(msg.get("bidUsdc", 0)), wallet)
                if not res["ok"]:
                    await ws.send_json({"type": "ERROR", "payload": {"reason": res["reason"]}})
    except WebSocketDisconnect:
        pass
    finally:
        await auction_manager.unregister(cid)


# V-09: WebSocket connection limiter
_ws_connections: dict = {}  # ip -> count
_WS_MAX_PER_IP = 5

@app.websocket("/ws/prices")
async def ws_prices(websocket: WebSocket):
    """WebSocket: real-time price updates every 10 seconds. Max 5 per IP."""
    ip = websocket.client.host if websocket.client else "unknown"
    _ws_connections[ip] = _ws_connections.get(ip, 0) + 1
    if _ws_connections[ip] > _WS_MAX_PER_IP:
        _ws_connections[ip] -= 1
        await websocket.close(code=1008, reason="Too many connections")
        return
    await websocket.accept()
    try:
        while True:
            try:
                from price_oracle import get_crypto_prices
                prices = await get_crypto_prices()
                await websocket.send_json({"type": "prices", "data": prices, "ts": int(time.time())})
            except Exception as e:
                print(f"[WS/prices] Error: {e}")
            await asyncio.sleep(10)
    except Exception as e:
        if "disconnect" not in str(e).lower():
            print(f"[WS/prices] Connection error: {e}")
    finally:
        _ws_connections[ip] = max(0, _ws_connections.get(ip, 1) - 1)

@app.websocket("/ws/candles")
async def ws_candles(websocket: WebSocket):
    """WebSocket: real-time candle updates every 60 seconds."""
    await websocket.accept()
    try:
        # Get subscription params from first message
        params = await asyncio.wait_for(websocket.receive_json(), timeout=10)
        symbol = params.get("symbol", "SOL").upper()
        interval = params.get("interval", "1m")
        while True:
            try:
                rows = await db.raw_execute_fetchall(
                    "SELECT symbol, interval, open, high, low, close, volume, timestamp FROM price_candles "
                    "WHERE symbol=? AND interval=? ORDER BY timestamp DESC LIMIT 1", (symbol, interval))
                if rows:
                    r = rows[0]
                    await websocket.send_json({"type": "candle", "symbol": symbol, "interval": interval,
                        "o": r["open"], "h": r["high"], "l": r["low"], "c": r["close"], "v": r["volume"], "t": r["timestamp"]})
            except Exception as e:
                print(f"[WS/candles] Error: {e}")
            await asyncio.sleep(60 if interval != "1m" else 10)
    except Exception as e:
        if "disconnect" not in str(e).lower():
            print(f"[WS/candles] Connection error: {e}")


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
    # Verification complete: montant + destinataire
    tx_result = await verify_transaction(
        tx_signature=req.tx_signature,
        expected_amount_usdc=req.amount_usdc,
        expected_recipient=TREASURY_ADDRESS,
    )
    if not tx_result.get("valid"):
        raise HTTPException(400, f"Transaction invalide: {tx_result.get('error', 'verification echouee')}")
    cmd = {
        "commandId": str(uuid.uuid4()), "serviceId": req.service_id,
        "buyerWallet": wallet, "txSignature": req.tx_signature,
        "prompt": req.prompt, "status": "pending",
        "createdAt": int(time.time()),
        "verified_amount": tx_result.get("amount_usdc", 0),
    }
    await db.save_command(cmd)
    await db.record_transaction(wallet, req.tx_signature, req.amount_usdc, "marketplace")
    return {"commandId": cmd["commandId"], "status": "pending"}


@app.get("/api/marketplace/commands/{command_id}")
async def get_command(command_id: str, wallet: str = Depends(require_auth)):
    rows = await db.raw_execute_fetchall("SELECT data FROM commands WHERE command_id=?", (command_id,))
    row = rows[0] if rows else None
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
    """GPU tiers with live pricing from RunPod (falls back to static data)."""
    try:
        live = await get_gpu_tiers_live()
        if live and live.get("tiers"):
            return live
    except Exception as e:
        print(f"[GPU] Live tiers fallback to static: {e}")
    return {"tiers": GPU_TIERS, "provider": "static"}


@app.get("/api/gpu/auctions/active")
async def get_active_auctions():
    return auction_manager.get_open_auctions()


@app.get("/api/gpu/auctions")
async def get_auctions(status: str = "open"):
    """List GPU auctions. ?status=open returns active auctions."""
    auctions = auction_manager.get_open_auctions()
    if status != "open":
        return []  # Only open auctions available in-memory
    return auctions


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
async def settle_auction(req: AuctionSettleRequest, wallet: str = Depends(require_auth)):
    if wallet != req.winner:
        raise HTTPException(403, "Wallet mismatch: you can only settle auctions you won")
    if await db.tx_already_processed(req.tx_signature):
        raise HTTPException(400, "Transaction deja traitee.")
    auction = await db.get_auction(req.auction_id)
    if not auction:
        raise HTTPException(404, "Enchere introuvable.")
    # Verification complete: montant + destinataire
    tx_result = await verify_transaction(
        tx_signature=req.tx_signature,
        expected_amount_usdc=auction.get("currentBid", 0),
        expected_recipient=TREASURY_ADDRESS,
    )
    if not tx_result.get("valid"):
        raise HTTPException(400, f"Transaction invalide: {tx_result.get('error', 'verification echouee')}")
    instance = await runpod.rent_gpu(auction["gpuTierId"], auction["durationHours"])
    if not instance.get("success"):
        raise HTTPException(502, f"RunPod: {instance.get('error')}")
    await db.record_transaction(req.winner, req.tx_signature, auction["currentBid"], "gpu_auction")
    await db.update_auction(req.auction_id, {
        "status": "provisioned", "txSignature": req.tx_signature,
        "gpuInstanceId": instance["instanceId"], "sshEndpoint": instance.get("ssh_endpoint", ""),
    })
    await auction_manager.broadcast({"type": "GPU_PROVISIONED", "payload": {
        "auctionId": req.auction_id, "winner": req.winner,
        "gpuLabel": auction["gpuLabel"], "sshEndpoint": instance.get("ssh_endpoint", ""),
    }})
    return {"ok": True, "instanceId": instance["instanceId"]}


# ═══════════════════════════════════════════════════════════
#  GPU RENTAL — Direct rent (no auction)
# ═══════════════════════════════════════════════════════════

# Free trial: first 10 minutes free per wallet (RTX 4090 only)
FREE_TRIAL_MINUTES = 10
FREE_TRIAL_GPU = "rtx4090"

# Label -> tier ID mapping (for frontend that sends display names)
_LABEL_TO_TIER = {}
for _tid, _ginfo in GPU_MAP.items():
    _rpid = _ginfo.get("runpod_id")
    if not _rpid:
        continue  # Skip local GPU tier (no runpod_id)
    _label = _rpid.replace("NVIDIA ", "").replace("GeForce ", "")
    _LABEL_TO_TIER[_label.lower()] = _tid
    _LABEL_TO_TIER[_rpid.lower()] = _tid
# Also add explicit known labels
_LABEL_TO_TIER.update({
    "rtx 4090": "rtx4090", "rtx4090": "rtx4090", "geforce rtx 4090": "rtx4090",
    "a100 80gb": "a100_80", "a100 80gb pcie": "a100_80", "a100": "a100_80",
    "h100 sxm5": "h100_sxm5", "h100": "h100_sxm5",
    "rtx a6000": "a6000", "a6000": "a6000",
    "4x a100 80gb": "4xa100", "4xa100": "4xa100",
    "h200 sxm": "h200", "h200": "h200",
    "l40s": "l40s", "rtx 3090": "rtx3090", "rtx3090": "rtx3090",
})


def _resolve_gpu_tier(gpu_input: str) -> str | None:
    """Resolve a GPU label or tier ID to a canonical tier ID."""
    normalized = gpu_input.strip().lower()
    # Direct tier ID match
    if normalized in GPU_MAP:
        return normalized
    # Label lookup
    return _LABEL_TO_TIER.get(normalized)


@app.get("/api/public/gpu/tiers")
async def get_gpu_tiers_public():
    """Live GPU pricing + availability from RunPod. No auth required."""
    return await get_gpu_tiers_live()


@app.post("/api/gpu/rent")
async def rent_gpu_direct(req: dict, auth_wallet: str = Depends(require_auth)):
    """Rent a GPU. Requires USDC payment verified on-chain.

    Body: { gpu: str (tier ID or label), hours: float, payment_tx: str }
    """
    gpu_input = req.get("gpu") or req.get("gpu_tier_id", "")
    wallet = auth_wallet  # Use authenticated wallet, not body
    hours = float(req.get("hours", 1))
    payment_tx = req.get("payment_tx")

    if not wallet:
        raise HTTPException(400, "Wallet address required")
    if not payment_tx:
        raise HTTPException(402, "USDC payment required. Send payment to Treasury and include payment_tx.")
    if hours <= 0 or hours > 720:
        raise HTTPException(400, "Hours must be between 0 and 720")

    # Resolve GPU tier
    tier_id = _resolve_gpu_tier(gpu_input)
    if not tier_id:
        raise HTTPException(400, f"Unknown GPU: {gpu_input}. Available: {', '.join(GPU_MAP.keys())}")
    if tier_id not in GPU_MAP:
        raise HTTPException(400, f"GPU tier not available for rental: {tier_id}")

    gpu_config = GPU_MAP[tier_id]
    cost_per_hr = gpu_config.get("base_price_per_hour", 0)
    total_cost = round(cost_per_hr * hours, 4)

    is_free_trial = False
    if True:
        # Verify USDC payment on-chain
        if await db.tx_already_processed(payment_tx):
            raise HTTPException(400, "Transaction already processed.")
        tx_result = await verify_transaction(
            tx_signature=payment_tx,
            expected_amount_usdc=total_cost,
            expected_recipient=TREASURY_ADDRESS,
        )
        if not tx_result.get("valid"):
            raise HTTPException(400, f"Payment invalid: {tx_result.get('error', 'verification failed')}")

    # Provision the GPU on RunPod
    print(f"[GPU Rent] Provisioning {tier_id} for {hours}h — wallet: {wallet}")
    result = await runpod.rent_gpu(tier_id, hours)

    if not result.get("success"):
        raise HTTPException(502, f"RunPod provisioning failed: {result.get('error')}")

    # Record in database
    instance_id = result["instanceId"]
    try:
        await db.save_gpu_instance({
            "instance_id": instance_id,
            "agent_wallet": wallet,
            "agent_name": wallet[:8],
            "gpu_tier": tier_id,
            "duration_hours": hours,
            "price_per_hour": cost_per_hr,
            "total_cost": total_cost,
            "commission": 0,
            "payment_tx": payment_tx,
            "runpod_pod_id": instance_id,
            "status": result.get("status", "provisioning"),
            "ssh_endpoint": result.get("ssh_endpoint", ""),
            "scheduled_end": result.get("auto_terminate_at", 0),
        })
    except Exception as e:
        print(f"[GPU Rent] DB save warning: {e}")

    # Record the transaction (skip for free trial)
    if not is_free_trial:
        try:
            await db.record_transaction(wallet, payment_tx, total_cost, "gpu_rental")
        except Exception as e:
            print(f"[GPU Rent] TX record warning: {e}")

    return {
        "ok": True,
        "instanceId": instance_id,
        "gpu": result.get("gpu", tier_id),
        "gpu_count": result.get("gpu_count", 1),
        "status": result.get("status", "provisioning"),
        "ssh_command": result.get("ssh_command", ""),
        "ssh_endpoint": result.get("ssh_endpoint", ""),
        "jupyter_url": result.get("jupyter_url", ""),
        "api_url": result.get("api_url", ""),
        "cost_per_hr": cost_per_hr,
        "total_cost": total_cost,
        "duration_hours": hours,
        "auto_terminate_at": result.get("auto_terminate_at", 0),
        "is_free_trial": is_free_trial,
        "provider": "runpod",
        "instructions": result.get("instructions", ""),
    }


@app.post("/api/public/gpu/rent")
async def rent_gpu_public(req: dict):
    """Public API endpoint for GPU rental (A2A agents). Same logic as /api/gpu/rent."""
    return await rent_gpu_direct(req)


@app.get("/api/gpu/status/{pod_id}")
async def get_gpu_status(pod_id: str):
    """Get real-time status of a running GPU pod."""
    return await runpod.get_pod_status(pod_id)


@app.post("/api/gpu/terminate/{pod_id}")
async def terminate_gpu(pod_id: str, wallet: str = Depends(require_auth)):
    """Terminate a GPU pod early. Only the renter can terminate."""
    # Verify ownership
    instance = await db.get_gpu_instance(pod_id)
    if not instance:
        raise HTTPException(404, "GPU instance not found")
    if instance.get("agent_wallet") != wallet:
        raise HTTPException(403, "Only the renter can terminate this pod")

    result = await runpod.terminate_pod(pod_id)
    if result.get("success"):
        try:
            await db.update_gpu_instance(pod_id, {
                "status": "terminated",
                "actual_end": int(time.time()),
                "actual_cost": result.get("actual_cost", 0),
            })
        except Exception as e:
            print(f"[GPU] DB update warning: {e}")
    return result


@app.get("/api/gpu/active")
async def list_active_gpus():
    """List all currently active GPU pods."""
    return await runpod.list_active_pods()


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
    tiers = [{"name": "WHALE", "min": 5000}, {"name": "GOLD", "min": 500}, {"name": "BRONZE", "min": 0}]
    tier = next((t["name"] for t in tiers if volume >= t["min"]), "BRONZE")
    return {"wallet": wallet, "volume30d": volume, "commissionBps": bps, "tier": tier}


# ═══════════════════════════════════════════════════════════
#  BASE — Coinbase L2 (Art.13)
# ═══════════════════════════════════════════════════════════

@app.get("/api/base/info")
async def base_info():
    from config import BASE_RPC, BASE_CHAIN_ID, BASE_USDC_CONTRACT
    # #11: status depends on treasury configuration (#17: startup validation)
    return {
        "network": "base-mainnet",
        "chainId": BASE_CHAIN_ID,
        "rpc": BASE_RPC,
        "usdcContract": BASE_USDC_CONTRACT,
        "treasury": TREASURY_ADDRESS_BASE,
        "status": "active" if TREASURY_ADDRESS_BASE else "not_configured",
    }


@app.post("/api/base/verify")
async def verify_base_tx(req: BaseVerifyRequest, request: Request):
    # #15: Rate limit on verify endpoints (raises HTTPException 429 if exceeded)
    check_rate_limit(request)
    return await verify_base_transaction(req.tx_hash, req.expected_to)


@app.post("/api/base/verify-usdc")
async def verify_base_usdc(req: BaseVerifyRequest, request: Request):
    # #15: Rate limit on verify endpoints (raises HTTPException 429 if exceeded)
    check_rate_limit(request)
    return await verify_usdc_transfer_base(req.tx_hash, req.expected_amount_raw)


# ═══════════════════════════════════════════════════════════
#  ETHEREUM — Mainnet (grosses transactions)
# ═══════════════════════════════════════════════════════════

@app.get("/api/ethereum/info")
async def ethereum_info():
    from config import ETH_RPC, ETH_CHAIN_ID, ETH_USDC_CONTRACT, TREASURY_ADDRESS_ETH, ETH_MIN_TX_USDC
    return {
        "network": "ethereum-mainnet",
        "chainId": ETH_CHAIN_ID,
        "rpc": ETH_RPC,
        "usdcContract": ETH_USDC_CONTRACT,
        "treasury": TREASURY_ADDRESS_ETH,
        "minTransactionUsdc": ETH_MIN_TX_USDC,
        "status": "active" if TREASURY_ADDRESS_ETH else "not_configured",
        "note": "Ethereum mainnet for large transactions only (high gas fees). Use Solana or Base for small amounts.",
    }


@app.post("/api/ethereum/verify")
async def verify_eth_tx(req: BaseVerifyRequest, request: Request):
    # #14-17: Rate limit on Ethereum verify endpoints (same pattern as Base)
    check_rate_limit(request)
    from eth_verifier import verify_eth_transaction
    return await verify_eth_transaction(req.tx_hash, req.expected_to)


@app.post("/api/ethereum/verify-usdc")
async def verify_eth_usdc(req: BaseVerifyRequest, request: Request):
    # #14-17: Rate limit on Ethereum verify-usdc endpoints (same pattern as Base)
    check_rate_limit(request)
    from eth_verifier import verify_usdc_transfer_eth
    return await verify_usdc_transfer_eth(req.tx_hash, req.expected_amount_raw)


# ═══════════════════════════════════════════════════════════
#  POLYGON — PoS (Art.13 EVM)
# ═══════════════════════════════════════════════════════════

@app.get("/api/polygon/info")
async def polygon_info():
    from config import POLYGON_RPC, POLYGON_CHAIN_ID, POLYGON_USDC_CONTRACT, TREASURY_ADDRESS_POLYGON
    return {
        "network": "polygon-mainnet",
        "chainId": POLYGON_CHAIN_ID,
        "rpc": POLYGON_RPC,
        "usdcContract": POLYGON_USDC_CONTRACT,
        "treasury": TREASURY_ADDRESS_POLYGON,
        "status": "active" if TREASURY_ADDRESS_POLYGON else "not_configured",
    }


@app.post("/api/polygon/verify")
async def verify_polygon_tx(req: BaseVerifyRequest, request: Request):
    check_rate_limit(request)
    return await verify_polygon_transaction(req.tx_hash, req.expected_to)


@app.post("/api/polygon/verify-usdc")
async def verify_polygon_usdc(req: BaseVerifyRequest, request: Request):
    check_rate_limit(request)
    return await verify_usdc_transfer_polygon(req.tx_hash, req.expected_amount_raw)


# ═══════════════════════════════════════════════════════════
#  ARBITRUM — One (Art.13 EVM L2)
# ═══════════════════════════════════════════════════════════

@app.get("/api/arbitrum/info")
async def arbitrum_info():
    from config import ARBITRUM_RPC, ARBITRUM_CHAIN_ID, ARBITRUM_USDC_CONTRACT, TREASURY_ADDRESS_ARBITRUM
    return {
        "network": "arbitrum-mainnet",
        "chainId": ARBITRUM_CHAIN_ID,
        "rpc": ARBITRUM_RPC,
        "usdcContract": ARBITRUM_USDC_CONTRACT,
        "treasury": TREASURY_ADDRESS_ARBITRUM,
        "status": "active" if TREASURY_ADDRESS_ARBITRUM else "not_configured",
    }


@app.post("/api/arbitrum/verify")
async def verify_arbitrum_tx(req: BaseVerifyRequest, request: Request):
    check_rate_limit(request)
    return await verify_arbitrum_transaction(req.tx_hash, req.expected_to)


@app.post("/api/arbitrum/verify-usdc")
async def verify_arbitrum_usdc(req: BaseVerifyRequest, request: Request):
    check_rate_limit(request)
    return await verify_usdc_transfer_arbitrum(req.tx_hash, req.expected_amount_raw)


# ═══════════════════════════════════════════════════════════
#  AVALANCHE — C-Chain (Art.13 EVM)
# ═══════════════════════════════════════════════════════════

@app.get("/api/avalanche/info")
async def avalanche_info():
    from config import AVALANCHE_RPC, AVALANCHE_CHAIN_ID, AVALANCHE_USDC_CONTRACT, TREASURY_ADDRESS_AVALANCHE
    return {
        "network": "avalanche-mainnet",
        "chainId": AVALANCHE_CHAIN_ID,
        "rpc": AVALANCHE_RPC,
        "usdcContract": AVALANCHE_USDC_CONTRACT,
        "treasury": TREASURY_ADDRESS_AVALANCHE,
        "status": "active" if TREASURY_ADDRESS_AVALANCHE else "not_configured",
    }


@app.post("/api/avalanche/verify")
async def verify_avalanche_tx(req: BaseVerifyRequest, request: Request):
    check_rate_limit(request)
    return await verify_avalanche_transaction(req.tx_hash, req.expected_to)


@app.post("/api/avalanche/verify-usdc")
async def verify_avalanche_usdc(req: BaseVerifyRequest, request: Request):
    check_rate_limit(request)
    return await verify_usdc_transfer_avalanche(req.tx_hash, req.expected_amount_raw)


# ═══════════════════════════════════════════════════════════
#  BNB CHAIN — BSC (Art.13 EVM)
# ═══════════════════════════════════════════════════════════

@app.get("/api/bnb/info")
async def bnb_info():
    from config import BNB_RPC, BNB_CHAIN_ID, BNB_USDC_CONTRACT, TREASURY_ADDRESS_BNB
    return {
        "network": "bnb-mainnet",
        "chainId": BNB_CHAIN_ID,
        "rpc": BNB_RPC,
        "usdcContract": BNB_USDC_CONTRACT,
        "treasury": TREASURY_ADDRESS_BNB,
        "status": "active" if TREASURY_ADDRESS_BNB else "not_configured",
    }


@app.post("/api/bnb/verify")
async def verify_bnb_tx(req: BaseVerifyRequest, request: Request):
    check_rate_limit(request)
    return await verify_bnb_transaction(req.tx_hash, req.expected_to)


@app.post("/api/bnb/verify-usdc")
async def verify_bnb_usdc(req: BaseVerifyRequest, request: Request):
    check_rate_limit(request)
    return await verify_usdc_transfer_bnb(req.tx_hash, req.expected_amount_raw)


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
async def ap2_pay_incoming(req: AP2PaymentRequest):
    """Accept incoming AP2 payment from external agent."""
    # #7: Content safety on any string fields
    if hasattr(req, 'network') and req.network:
        check_content_safety(req.network, "network")
    return await ap2_manager.process_payment(
        intent_mandate=req.intent_mandate,
        cart_mandate=req.cart_mandate,
        payment_payload=req.payment_payload,
        network=req.network,
    )


@app.post("/api/ap2/pay-external")
async def ap2_pay_outgoing(req: dict, wallet: str = Depends(require_auth)):
    """Use AP2 to pay for an external agent service."""
    # #7: Content safety on purpose field
    purpose = req.get("purpose", "ai_service")
    if purpose:
        check_content_safety(purpose, "purpose")
    # #7: SSRF validation on service_url
    service_url = req.get("service_url", "")
    if service_url:
        from webhook_dispatcher import validate_callback_url
        validate_callback_url(service_url)
    return await ap2_manager.pay_external(
        service_url=service_url,
        amount_usdc=float(req.get("amount_usdc", 0)),
        user_wallet=wallet,
        provider_wallet=req.get("provider_wallet", ""),
        purpose=purpose,
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
        "scout": scout_agent.get_stats(),
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
async def stop_growth(request: Request):
    """Arret d'urgence de l'agent marketing. Admin only."""
    from security import require_admin
    require_admin(request)
    growth_agent.stop()
    return {"ok": True, "message": "Growth agent arrete"}

@app.post("/api/agent/growth/start")
async def start_growth(request: Request):
    """Relance l'agent marketing. Admin only."""
    from security import require_admin
    require_admin(request)
    if not growth_agent._running:
        asyncio.create_task(growth_agent.run())
    return {"ok": True, "message": "Growth agent relance"}

@app.get("/api/agent/scout")
async def scout_status():
    """Stats du SCOUT (prospection IA-to-IA). Public read-only."""
    return scout_agent.get_stats()

@app.post("/api/agent/scout/scan")
async def scout_scan_now(request: Request):
    """Force un scan SCOUT immediat. Admin only."""
    from security import require_admin
    require_admin(request)
    agents = await scout_agent.scan_all_chains()
    return {"ok": True, "agents_found": len(agents), "stats": scout_agent.get_stats()}

@app.post("/api/agent/scout/stop")
async def stop_scout(request: Request):
    """Arrete le SCOUT. Admin only."""
    from security import require_admin
    require_admin(request)
    scout_agent.stop()
    return {"ok": True, "message": "SCOUT arrete"}

@app.post("/api/agent/scout/start")
async def start_scout(request: Request):
    """Relance le SCOUT. Admin only."""
    from security import require_admin
    require_admin(request)
    if not scout_agent._running:
        asyncio.create_task(scout_agent.run())
    return {"ok": True, "message": "SCOUT relance"}


# ═══════════════════════════════════════════════════════════
#  V11: DYNAMIC PRICING (Art.16)
# ═══════════════════════════════════════════════════════════

@app.get("/api/pricing/status")
async def pricing_status():
    return get_pricing_status()

@app.post("/api/pricing/adjust")
async def pricing_force_adjust(request: Request):
    """Force un ajustement du pricing. Admin only."""
    from security import require_admin
    require_admin(request)
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
async def resolve_dispute(req: dict, request: Request):
    from security import require_admin
    require_admin(request)
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
async def swarm_stats(request: Request):
    """Full swarm stats."""
    from security import check_rate_limit
    check_rate_limit(request)
    return swarm.get_stats()

@app.get("/api/swarm/niches")
async def swarm_niches(request: Request):
    """List available niches."""
    from security import check_rate_limit
    check_rate_limit(request)
    return swarm.get_available_niches()

@app.post("/api/swarm/analyze")
async def swarm_analyze(request: Request):
    """AI analysis of profitable niches."""
    from security import check_rate_limit
    check_rate_limit(request)
    return await swarm.analyze_niches(db)

@app.post("/api/swarm/spawn")
async def swarm_spawn(req: dict, request: Request, wallet: str = Depends(require_auth)):
    """Deploy a new specialized clone. (#1) Never accept wallet_privkey."""
    from security import check_rate_limit
    check_rate_limit(request)
    return await swarm.spawn_clone(
        niche=req.get("niche", ""),
        wallet_address=req.get("wallet_address", ""),
    )

@app.post("/api/swarm/request")
async def swarm_request(req: dict, request: Request):
    """Send a request to a specialized clone. (#3) Input validation + (#4) rate limit."""
    from security import check_rate_limit, check_content_safety
    check_rate_limit(request)
    niche = str(req.get("niche", ""))[:50]
    prompt = str(req.get("prompt", ""))[:5000]
    wallet = str(req.get("buyer_wallet", ""))[:50]
    if not niche or not prompt:
        raise HTTPException(400, "niche and prompt required")
    check_content_safety(prompt, "prompt")
    return await swarm.process_request(niche=niche, prompt=prompt, buyer_wallet=wallet)

@app.post("/api/swarm/pause/{clone_id}")
async def swarm_pause(clone_id: str, request: Request):
    """Pause a clone. (#2) Admin auth required."""
    from security import require_admin, check_rate_limit
    check_rate_limit(request)
    require_admin(request)
    return await swarm.pause_clone(clone_id)

@app.post("/api/swarm/resume/{clone_id}")
async def swarm_resume(clone_id: str, request: Request):
    """Resume a clone. (#2) Admin auth required."""
    from security import require_admin, check_rate_limit
    check_rate_limit(request)
    require_admin(request)
    return await swarm.resume_clone(clone_id)

@app.post("/api/swarm/stop/{clone_id}")
async def swarm_stop(clone_id: str, request: Request):
    """Stop a clone. (#2) Admin auth required."""
    from security import require_admin, check_rate_limit
    check_rate_limit(request)
    require_admin(request)
    return await swarm.stop_clone(clone_id)


# ══════════════════════════════════════════════════════════
#  V11: ESCROW ON-CHAIN (Art.21)
# ══════════════════════════════════════════════════════════

@app.get("/api/escrow/stats")
async def escrow_stats(request: Request):
    """Escrow stats. Admin only (leaks wallet addresses)."""
    from security import require_admin
    require_admin(request)
    return escrow_client.get_stats()

@app.get("/api/escrow/{escrow_id}")
async def get_escrow(escrow_id: str, wallet: str = Depends(require_auth)):
    """Get escrow details. Auth required (only buyer/seller can view)."""
    data = escrow_client.get_escrow(escrow_id)
    if data.get("error"):
        raise HTTPException(404, data["error"])
    if wallet not in (data.get("buyer", ""), data.get("seller", "")):
        raise HTTPException(403, "Not authorized to view this escrow")
    return data

@app.post("/api/escrow/create")
async def create_escrow(req: dict, wallet: str = Depends(require_auth)):
    # #6: Validate timeout_hours at API level
    timeout = int(req.get("timeout_hours", 72))
    if timeout < 1 or timeout > 168:
        raise HTTPException(400, "timeout_hours must be 1-168")
    return await escrow_client.create_escrow(
        buyer_wallet=wallet,
        seller_wallet=req.get("seller_wallet", ""),
        amount_usdc=float(req.get("amount_usdc", 0)),
        service_id=req.get("service_id", ""),
        tx_signature=req.get("tx_signature", ""),
        timeout_hours=timeout,
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
async def resolve_escrow_dispute(req: dict, request: Request):
    # #1 / #4 / #7: Admin-only endpoint for dispute resolution
    from security import require_admin
    require_admin(request)
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
async def seed_services(request: Request):
    """Ajoute les services initiaux (une seule fois)."""
    from security import require_admin
    require_admin(request)
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
async def seed_datasets(request: Request):
    """Ajoute les datasets initiaux (une seule fois)."""
    from security import require_admin
    require_admin(request)
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




# ══════════════════════════════════════════════════════════
#  V12.1: Agent Analytics (inscriptions en temps reel)
# ══════════════════════════════════════════════════════════

@app.get("/api/analytics/agents")
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
        return {"error": str(e)}


@app.get("/api/analytics/agents/live")
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
        return {"error": str(e)}


# ══════════════════════════════════════════════════════════
#  V12: XRP LEDGER (4eme reseau)
# ══════════════════════════════════════════════════════════

@app.post("/api/xrpl/verify")
async def xrpl_verify(request: Request):
    """Verifie une transaction sur XRP Ledger."""
    body = await request.json()
    tx_hash = body.get("tx_hash", "")
    if not tx_hash:
        raise HTTPException(400, "tx_hash required")
    try:
        from xrpl_verifier import verify_xrpl_transaction
        return await verify_xrpl_transaction(
            tx_hash,
            expected_dest=body.get("expected_dest", ""),
            expected_amount=float(body.get("expected_amount", 0)),
        )
    except Exception as e:
        return {"verified": False, "error": str(e)}


@app.get("/api/xrpl/balance/{address}")
async def xrpl_balance(address: str):
    """Solde XRP + USDC d'un wallet XRPL."""
    try:
        from xrpl_verifier import get_xrpl_balance
        return await get_xrpl_balance(address)
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/xrpl/info")
async def xrpl_info():
    """Infos XRP Ledger."""
    from config import XRPL_RPC, XRPL_USDC_ISSUER, TREASURY_ADDRESS_XRPL
    return {
        "network": "xrpl-mainnet",
        "rpc": XRPL_RPC,
        "usdc_issuer": XRPL_USDC_ISSUER,
        "treasury": TREASURY_ADDRESS_XRPL or "not configured",
        "supported_currencies": ["XRP", "USDC"],
        "settlement_time": "3-5 seconds",
        "fees": "< $0.01",
    }


@app.post("/api/xrpl/verify-usdc")
async def xrpl_verify_usdc(request: Request):
    """Verifie un transfert USDC sur XRPL."""
    body = await request.json()
    tx_hash = body.get("tx_hash", "")
    if not tx_hash:
        raise HTTPException(400, "tx_hash required")
    try:
        from xrpl_verifier import verify_usdc_transfer_xrpl
        return await verify_usdc_transfer_xrpl(
            tx_hash,
            expected_dest=body.get("expected_dest", ""),
            min_amount=float(body.get("min_amount", 0)),
        )
    except Exception as e:
        return {"verified": False, "error": str(e)}


# ══════════════════════════════════════════════════════════
#  V12: TON — The Open Network (5eme reseau, non-EVM)
# ══════════════════════════════════════════════════════════

@app.get("/api/ton/info")
async def ton_info():
    """Infos reseau TON."""
    from config import TON_API_URL, TREASURY_ADDRESS_TON, TON_USDT_JETTON
    return {
        "network": "ton-mainnet",
        "api": TON_API_URL,
        "usdt_jetton": TON_USDT_JETTON,
        "treasury": TREASURY_ADDRESS_TON or "not configured",
        "status": "active" if TREASURY_ADDRESS_TON else "not_configured",
        "supported_currencies": ["TON", "USDT"],
        "settlement_time": "5-10 seconds",
        "fees": "< $0.01",
        "note": "TON uses USDT (Tether) — no native USDC on TON yet",
    }


@app.post("/api/ton/verify")
async def ton_verify(request: Request):
    """Verifie une transaction sur TON."""
    check_rate_limit(request)
    body = await request.json()
    tx_hash = body.get("tx_hash", "")
    if not tx_hash:
        raise HTTPException(400, "tx_hash required")
    try:
        from ton_verifier import verify_ton_transaction
        return await verify_ton_transaction(
            tx_hash,
            expected_dest=body.get("expected_dest", ""),
            expected_amount=float(body.get("expected_amount", 0)),
        )
    except Exception as e:
        return {"verified": False, "error": str(e)}


@app.get("/api/ton/balance/{address}")
async def ton_balance(address: str):
    """Solde TON d'un wallet."""
    try:
        from ton_verifier import get_ton_balance
        return await get_ton_balance(address)
    except Exception as e:
        return {"error": str(e)}


# ══════════════════════════════════════════════════════════
#  V12: SUI (6eme reseau, non-EVM)
# ══════════════════════════════════════════════════════════

@app.get("/api/sui/info")
async def sui_info():
    """Infos reseau SUI."""
    from config import SUI_RPC, TREASURY_ADDRESS_SUI, SUI_USDC_TYPE
    return {
        "network": "sui-mainnet",
        "rpc": SUI_RPC,
        "usdc_type": SUI_USDC_TYPE,
        "treasury": TREASURY_ADDRESS_SUI or "not configured",
        "status": "active" if TREASURY_ADDRESS_SUI else "not_configured",
        "supported_currencies": ["SUI", "USDC"],
        "settlement_time": "2-3 seconds",
        "fees": "< $0.01",
    }


@app.post("/api/sui/verify")
async def sui_verify(request: Request):
    """Verifie une transaction sur SUI."""
    check_rate_limit(request)
    body = await request.json()
    tx_digest = body.get("tx_digest", "") or body.get("tx_hash", "")
    if not tx_digest:
        raise HTTPException(400, "tx_digest (or tx_hash) required")
    try:
        from sui_verifier import verify_sui_transaction
        return await verify_sui_transaction(
            tx_digest,
            expected_dest=body.get("expected_dest", ""),
            expected_amount=float(body.get("expected_amount", 0)),
        )
    except Exception as e:
        return {"verified": False, "error": str(e)}


@app.get("/api/sui/balance/{address}")
async def sui_balance(address: str):
    """Solde SUI d'un wallet."""
    try:
        from sui_verifier import get_sui_balance
        return await get_sui_balance(address)
    except Exception as e:
        return {"error": str(e)}


# ══════════════════════════════════════════════════════════
#  V12: TRON (10eme reseau, non-EVM)
# ══════════════════════════════════════════════════════════

@app.get("/api/tron/info")
async def tron_info():
    """Infos reseau TRON."""
    from config import TRON_API_URL, TREASURY_ADDRESS_TRON, TRON_USDT_CONTRACT, TRON_USDC_CONTRACT
    return {
        "network": "tron-mainnet",
        "api": TRON_API_URL,
        "usdt_contract": TRON_USDT_CONTRACT,
        "usdc_contract": TRON_USDC_CONTRACT,
        "treasury": TREASURY_ADDRESS_TRON or "not configured",
        "status": "active" if TREASURY_ADDRESS_TRON else "not_configured",
        "supported_currencies": ["TRX", "USDT", "USDC"],
        "settlement_time": "3-5 seconds",
        "fees": "< $0.01 (Energy/Bandwidth)",
        "note": "TRON uses USDT (TRC-20) as primary stablecoin — largest USDT network by volume",
    }


@app.post("/api/tron/verify")
async def tron_verify(request: Request):
    """Verifie une transaction sur TRON."""
    check_rate_limit(request)
    body = await request.json()
    tx_id = body.get("tx_id", "") or body.get("tx_hash", "")
    if not tx_id:
        raise HTTPException(400, "tx_id (or tx_hash) required")
    try:
        from tron_verifier import verify_tron_transaction
        return await verify_tron_transaction(
            tx_id,
            expected_dest=body.get("expected_dest", ""),
            expected_amount=float(body.get("expected_amount", 0)),
        )
    except Exception as e:
        return {"verified": False, "error": str(e)}


@app.get("/api/tron/balance/{address}")
async def tron_balance(address: str):
    """Solde TRX d'un wallet TRON."""
    try:
        from tron_verifier import get_tron_balance
        return await get_tron_balance(address)
    except Exception as e:
        return {"error": str(e)}


# ══════════════════════════════════════════════════════════
#  V12: NEAR Protocol (12eme blockchain)
# ══════════════════════════════════════════════════════════

@app.get("/api/near/info")
async def near_info():
    """Infos reseau NEAR Protocol."""
    from config import NEAR_RPC, TREASURY_ADDRESS_NEAR, NEAR_USDC_CONTRACT
    return {
        "network": "near-mainnet", "rpc": NEAR_RPC,
        "usdc_contract": NEAR_USDC_CONTRACT,
        "treasury": TREASURY_ADDRESS_NEAR or "not configured",
        "status": "active", "supported_currencies": ["NEAR", "USDC"],
        "settlement_time": "1-2 seconds", "fees": "< $0.01",
    }

@app.post("/api/near/verify")
async def near_verify(request: Request):
    """Verifie une transaction NEAR."""
    check_rate_limit(request)
    body = await request.json()
    tx_hash = body.get("tx_hash", "")
    if not tx_hash:
        raise HTTPException(400, "tx_hash required")
    try:
        from near_verifier import verify_near_transaction
        return await verify_near_transaction(
            tx_hash, sender_id=body.get("sender_id", ""),
            expected_dest=body.get("expected_dest", ""),
            expected_amount=float(body.get("expected_amount", 0)),
        )
    except Exception as e:
        return {"verified": False, "error": str(e)}

@app.get("/api/near/balance/{account_id}")
async def near_balance(account_id: str):
    """Solde NEAR d'un compte."""
    try:
        from near_verifier import get_near_balance
        return await get_near_balance(account_id)
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/near/usdc-balance/{account_id}")
async def near_usdc_balance(account_id: str):
    """Solde USDC d'un compte NEAR."""
    try:
        from near_verifier import get_near_usdc_balance
        return await get_near_usdc_balance(account_id)
    except Exception as e:
        return {"error": str(e)}


# ══════════════════════════════════════════════════════════
#  V12: Aptos (13eme blockchain)
# ══════════════════════════════════════════════════════════

@app.get("/api/aptos/info")
async def aptos_info():
    """Infos reseau Aptos."""
    from config import APTOS_API, TREASURY_ADDRESS_APTOS, APTOS_USDC_TYPE
    return {
        "network": "aptos-mainnet", "api": APTOS_API,
        "usdc_type": APTOS_USDC_TYPE,
        "treasury": TREASURY_ADDRESS_APTOS or "not configured",
        "status": "active", "supported_currencies": ["APT", "USDC"],
        "settlement_time": "< 1 second", "fees": "< $0.01",
    }

@app.post("/api/aptos/verify")
async def aptos_verify(request: Request):
    """Verifie une transaction Aptos."""
    check_rate_limit(request)
    body = await request.json()
    tx_hash = body.get("tx_hash", "")
    if not tx_hash:
        raise HTTPException(400, "tx_hash required")
    try:
        from aptos_verifier import verify_aptos_transaction
        return await verify_aptos_transaction(
            tx_hash, expected_dest=body.get("expected_dest", ""),
            expected_amount=float(body.get("expected_amount", 0)),
        )
    except Exception as e:
        return {"verified": False, "error": str(e)}

@app.get("/api/aptos/balance/{address}")
async def aptos_balance(address: str):
    """Solde APT d'un wallet Aptos."""
    try:
        from aptos_verifier import get_aptos_balance
        return await get_aptos_balance(address)
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/aptos/usdc-balance/{address}")
async def aptos_usdc_balance(address: str):
    """Solde USDC d'un wallet Aptos."""
    try:
        from aptos_verifier import get_aptos_usdc_balance
        return await get_aptos_usdc_balance(address)
    except Exception as e:
        return {"error": str(e)}


# ══════════════════════════════════════════════════════════
#  V12: SEI (14eme blockchain, EVM)
# ══════════════════════════════════════════════════════════

@app.get("/api/sei/info")
async def sei_info():
    """Infos reseau SEI."""
    from config import SEI_RPC, SEI_CHAIN_ID, SEI_USDC_CONTRACT, TREASURY_ADDRESS_SEI
    return {
        "network": "sei-mainnet", "rpc": SEI_RPC, "chainId": SEI_CHAIN_ID,
        "usdc_contract": SEI_USDC_CONTRACT,
        "treasury": TREASURY_ADDRESS_SEI or "not configured",
        "status": "active", "supported_currencies": ["SEI", "USDC"],
        "settlement_time": "390ms", "fees": "< $0.001",
    }

@app.post("/api/sei/verify")
async def sei_verify(request: Request):
    """Verifie une transaction SEI (EVM)."""
    check_rate_limit(request)
    body = await request.json()
    tx_hash = body.get("tx_hash", "")
    if not tx_hash:
        raise HTTPException(400, "tx_hash required")
    try:
        from sei_verifier import verify_sei_transaction
        return await verify_sei_transaction(tx_hash, expected_to=body.get("expected_to"))
    except Exception as e:
        return {"verified": False, "error": str(e)}

@app.post("/api/sei/verify-usdc")
async def sei_verify_usdc(request: Request):
    """Verifie un transfert USDC sur SEI."""
    check_rate_limit(request)
    body = await request.json()
    tx_hash = body.get("tx_hash", "")
    if not tx_hash:
        raise HTTPException(400, "tx_hash required")
    try:
        from sei_verifier import verify_usdc_transfer_sei
        return await verify_usdc_transfer_sei(
            tx_hash, expected_amount_raw=int(body.get("expected_amount_raw", 0)),
            expected_recipient=body.get("expected_recipient"),
        )
    except Exception as e:
        return {"verified": False, "error": str(e)}

@app.get("/api/sei/balance/{address}")
async def sei_balance(address: str):
    """Solde SEI d'un wallet."""
    try:
        from sei_verifier import get_sei_balance
        return await get_sei_balance(address)
    except Exception as e:
        return {"error": str(e)}


# ══════════════════════════════════════════════════════════
#  V11: BOURSE ACTIONS TOKENISEES (Art.23)
# ══════════════════════════════════════════════════════════

@app.get("/api/stocks/stats")
async def stock_exchange_stats():
    from tokenized_stocks import stock_exchange
    return stock_exchange.get_stats()
