"""MAXIA Backend V12 — Art.1 to Art.15 + 47 features (14 chains: Solana + Base + Ethereum + XRP + Polygon + Arbitrum + Avalanche + BNB + TON + SUI + TRON + NEAR + Aptos + SEI + 17 AI Agents)"""
import logging
import asyncio, os, uuid, time, json
from contextlib import asynccontextmanager
from pathlib import Path
from error_utils import safe_error

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

load_dotenv()

# ── Core imports ──
from database import db, create_database
from auth import router as auth_router, require_auth, require_auth_flexible
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
try:
    from akash_client import AkashClient, akash as akash_client, AKASH_GPU_MAP, AKASH_MAX_PRICE, _active_deployments
    print(f"[Akash] Module charge OK — {len(AKASH_GPU_MAP)} GPU mappings")
except Exception as e:
    print(f"[Akash] Import echoue: {e} — mode RunPod only")
    akash_client = None
    AKASH_GPU_MAP = {}
    AKASH_MAX_PRICE = 10.0
    _active_deployments = {}
    class AkashClient:
        pass
try:
    from agentid_client import agentid as agentid_client
except ImportError:
    agentid_client = None
from config import AKASH_ENABLED
from solana_verifier import verify_transaction
from security import check_content_safety, check_rate_limit, set_redis_client
from redis_client import redis_client
from config import (
    GPU_TIERS, COMMISSION_TIERS, get_commission_bps,
    TREASURY_ADDRESS, TREASURY_ADDRESS_BASE,
    TREASURY_ADDRESS_POLYGON, TREASURY_ADDRESS_ARBITRUM,
    TREASURY_ADDRESS_AVALANCHE, TREASURY_ADDRESS_BNB,
    SUPPORTED_NETWORKS, X402_PRICE_MAP,
    SERVICE_PRICES,
)
_gpu_cheapest = f"${min(t['base_price_per_hour'] for t in GPU_TIERS if not t.get('local')):.2f}/h"

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
# WebSocket clients (local process) + Redis pub/sub optionnel pour multi-worker
_ws_clients: dict = {}
_redis_pubsub = None  # Redis connection si REDIS_URL est defini
REDIS_URL = os.getenv("REDIS_URL", "")  # redis://localhost:6379 pour multi-worker
WS_CHANNEL = "maxia:ws:broadcast"


async def _init_redis_pubsub():
    """Initialise Redis pub/sub si REDIS_URL est defini. Optionnel."""
    global _redis_pubsub
    if not REDIS_URL:
        return
    try:
        import redis.asyncio as aioredis
        _redis_pubsub = await aioredis.from_url(REDIS_URL)
        # Lancer le listener en background
        asyncio.create_task(_redis_ws_listener())
        print(f"[WS] Redis pub/sub actif: {REDIS_URL.split('@')[-1] if '@' in REDIS_URL else REDIS_URL}")
    except ImportError:
        print("[WS] redis[async] non installe — mode single-worker")
    except Exception as e:
        print(f"[WS] Redis error: {e} — mode single-worker")


async def _redis_ws_listener():
    """Ecoute les messages Redis et les forward aux clients WebSocket locaux."""
    try:
        pubsub = _redis_pubsub.pubsub()
        await pubsub.subscribe(WS_CHANNEL)
        async for message in pubsub.listen():
            if message["type"] == "message":
                import json as _json
                try:
                    msg = _json.loads(message["data"])
                    await _local_broadcast(msg)
                except Exception:
                    pass
    except Exception as e:
        print(f"[WS] Redis listener error: {e}")


async def _local_broadcast(msg: dict):
    """Broadcast aux clients WebSocket de CE worker uniquement."""
    dead = []
    for cid, ws in _ws_clients.items():
        try:
            await ws.send_json(msg)
        except Exception:
            dead.append(cid)
    for cid in dead:
        _ws_clients.pop(cid, None)


# ── WebSocket broadcast (Redis si dispo, sinon local) ──

async def broadcast_all(msg: dict):
    """Broadcast a tous les clients WS. Si Redis est actif, publie sur le channel
    pour que tous les workers recoivent le message. Sinon, broadcast local."""
    if _redis_pubsub:
        try:
            import json as _json
            await _redis_pubsub.publish(WS_CHANNEL, _json.dumps(msg, default=str))
            return  # Redis distribue a tous les workers via le listener
        except Exception:
            pass  # Fallback local si Redis echoue
    await _local_broadcast(msg)


# ── Native AI Services (registered at startup) ──
from seed_data import NATIVE_SERVICES


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

    # V12: Redis pub/sub pour WebSocket multi-worker
    await _init_redis_pubsub()

    # V12: GPU pricing live — fetch les prix RunPod au demarrage + auto-refresh 30min
    try:
        from gpu_pricing import refresh_gpu_prices, auto_refresh_loop
        await refresh_gpu_prices()
        asyncio.create_task(auto_refresh_loop())
    except Exception as e:
        print(f"[GPU Pricing] Init error: {e} — prix fallback utilises")

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

    # Seed forum with initial posts
    try:
        from forum_seed import seed_forum
        await seed_forum(db)
    except Exception as e:
        print(f"[Forum] Seed error: {e}")

    # Marketplace tables + seed native services
    try:
        from creator_marketplace import ensure_marketplace_tables
        await ensure_marketplace_tables(db)
    except Exception as e:
        print(f"[Marketplace] Init error: {e}")

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

    # V12: Pyth SSE streaming (prix live <1s pour clients HFT)
    try:
        from pyth_oracle import start_pyth_stream
        await start_pyth_stream()
    except Exception as e:
        print(f"[MAXIA] Pyth stream init error: {e} — HTTP polling fallback")

    # Enterprise: billing flush loop (persiste usage toutes les 60s)
    try:
        from enterprise_billing import billing_flush_loop
        asyncio.create_task(billing_flush_loop())
        print("[Enterprise] Billing flush loop started")
    except Exception as e:
        print(f"[MAXIA] Billing flush loop error: {e}")

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

    # V12: Volume 30d rolling reset — decay old volume monthly
    async def _volume_decay_worker():
        """Reset volume_30d to 0 for agents inactive > 30 days. Runs daily."""
        while True:
            await asyncio.sleep(86400)  # once per day
            try:
                cutoff = int(time.time()) - 30 * 86400  # 30 days ago
                # Single JOIN query instead of N+1 per-agent queries
                inactive = await db.raw_execute_fetchall(
                    "SELECT a.api_key FROM agents a "
                    "LEFT JOIN marketplace_tx m ON (a.api_key = m.buyer OR a.api_key = m.seller) "
                    "WHERE a.volume_30d > 0 "
                    "GROUP BY a.api_key "
                    "HAVING COALESCE(MAX(m.created_at), 0) < ?",
                    (cutoff,))
                for row in (inactive or []):
                    api_key = row["api_key"]
                    await db.update_agent(api_key, {"volume_30d": 0, "tier": "BRONZE"})
                    print(f"[VolumeDecay] Reset {api_key[:20]}... (inactive 30d)")
            except Exception as e:
                if "no such table" not in str(e):
                    print(f"[VolumeDecay] Error: {e}")
    t_volume = asyncio.create_task(_volume_decay_worker())

    # V12: Price alerts worker (notifies CLIENTS, not founder)
    try:
        from trading_tools import alert_checker_worker
        t_alerts = asyncio.create_task(alert_checker_worker())
        print("[MAXIA] Price alerts worker started (60s interval)")
    except Exception as e:
        print(f"[MAXIA] Alert worker init error: {e}")

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
    from security import check_jwt_secret, check_admin_key, _flush_audit
    if not check_jwt_secret():
        print("[MAXIA] ⚠️  Set JWT_SECRET in .env for production security!")
    # H4: Validation ADMIN_KEY au demarrage
    check_admin_key()

    # V12: Price broadcast loop — broadcasts top token prices every 30s to /ws clients
    async def _price_broadcast_loop():
        while True:
            try:
                from price_oracle import get_crypto_prices
                prices = await get_crypto_prices()
                # Send top 10 tokens
                top = dict(list(prices.items())[:10]) if isinstance(prices, dict) else {}
                await broadcast_all({"type": "price_update", "data": top})
            except Exception:
                pass
            await asyncio.sleep(30)

    t_price_broadcast = asyncio.create_task(_price_broadcast_loop())
    print("[MAXIA] Price broadcast loop started (30s interval)")

    # V13+: Streaming Payments updater (60s interval)
    try:
        from streaming_payments import stream_updater_loop
        asyncio.create_task(stream_updater_loop())
        print("[MAXIA] Streaming payments updater started (60s interval)")
    except Exception as e:
        print(f"[MAXIA] Stream updater init error: {e}")

    # Telegram bot — REMOVED from lifespan: scheduler.py already starts run_telegram_bot()
    # as one of its tasks (line 75). Running it twice causes duplicate getUpdates polling,
    # which leads to missed approval button callbacks and 409 Conflict errors from Telegram API.
    t_telegram = None

    # Pyth SSE permanent — stream prix live en continu (pas on-demand)
    try:
        from pyth_oracle import start_pyth_stream, start_fallback_refresh, start_equity_poll
        await start_pyth_stream()
        await start_equity_poll()
        await start_fallback_refresh()
        print("[MAXIA] Pyth SSE persistent stream + equity poll (2s) + fallback auto-refresh started")
    except Exception as e:
        print(f"[MAXIA] Pyth stream init error: {e}")

    # Chainlink Oracle — verification feeds on-chain Base au demarrage
    try:
        from chainlink_oracle import verify_feeds_at_startup
        cl_results = await verify_feeds_at_startup()
        verified = sum(1 for v in cl_results.values() if v.get("verified"))
        print(f"[MAXIA] Chainlink Base: {verified}/{len(cl_results)} feeds verified on-chain")
    except Exception as e:
        print(f"[MAXIA] Chainlink init error: {e}")

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
    for t in [t_health, t6, t7, t_backup, t_dispute, t_telegram]:
        try:
            if t:
                t.cancel()
        except Exception:
            pass
    # Cancel tasks created outside try blocks (always defined)
    for t in [t_volume, t_price_broadcast]:
        try:
            t.cancel()
        except Exception:
            pass
    # Cancel tasks that may not exist if their try/import failed
    try:
        t_alerts.cancel()
    except (NameError, Exception):
        pass
    scheduler.stop()
    scout_agent.stop()
    # Close connections
    try:
        from price_oracle import close_http_pool
        await close_http_pool()
    except Exception:
        pass
    # Close shared HTTP client
    try:
        from http_client import close_http_client
        await close_http_client()
    except Exception:
        pass
    await db.disconnect()
    await redis_client.close()
    print("[MAXIA] Shutdown complete")


# ── App ──

_is_sandbox = os.getenv("SANDBOX_MODE", "false").lower() == "true"
app = FastAPI(
    title="MAXIA API V12",
    version="12.0.0",
    lifespan=lifespan,
    docs_url="/docs" if _is_sandbox else None,
    redoc_url="/redoc" if _is_sandbox else None,
    openapi_url="/openapi.json" if _is_sandbox else None,
)


# ── M2: Limite globale taille requete (5 MB max) — protection contre upload abusif ──
from starlette.responses import JSONResponse as _JSONResponseGlobal


@app.middleware("http")
async def limit_body_size(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > 5_000_000:
        return _JSONResponseGlobal(status_code=413, content={"error": "Request too large (max 5MB)"})
    return await call_next(request)


# ── H1: Global exception handler — ne jamais exposer str(e) aux clients ──

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Intercepte les exceptions non gerees et retourne un message generique.
    Le request_id permet de retrouver l'erreur dans les logs serveur."""
    import traceback
    req_id = str(uuid.uuid4())[:8]
    print(f"[ERROR] {req_id}: {type(exc).__name__}: {exc}")
    traceback.print_exc()
    return _JSONResponseGlobal(
        status_code=500,
        content={"error": "Internal server error", "request_id": req_id},
    )


# ── CORS restrictif (pas de wildcard en prod) ──
_ALLOWED_ORIGINS = os.getenv("CORS_ORIGINS", "https://maxiaworld.app,https://www.maxiaworld.app").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Wallet", "X-Signature", "X-Nonce", "X-Admin-Key", "X-CEO-Key", "X-API-Key", "X-Payment", "X-Payment-Network"],
    allow_credentials=True,
)
app.middleware("http")(x402_middleware)


# ── Security Headers ──
@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if os.getenv("FORCE_HTTPS", "false").lower() == "true":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://unpkg.com; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: https:; "
        "connect-src 'self' wss: ws: https:; "
        "frame-ancestors 'none'"
    )
    return response


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
    from security import check_rate_limit_smart, get_rate_limit_info, check_burst_limit, get_burst_ban_remaining, check_ip_rate_limit
    ip = request.client.host if request.client else "unknown"

    # IP rate limiting — 100 req/min per IP (before other checks)
    if ip not in ("127.0.0.1", "::1") and check_ip_rate_limit(ip):
        from starlette.responses import JSONResponse as _JSONRespIP
        return _JSONRespIP(
            status_code=429,
            content={"error": "IP rate limit exceeded (100 req/min). Slow down.", "retry_after": 60},
            headers={"Retry-After": "60"},
        )

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

# V13: Proof of Delivery + Dispute Resolution (Art.47)
try:
    from proof_of_delivery import router as pod_router
    app.include_router(pod_router)
    print("[PoD] Proof of Delivery + Dispute Resolution monte")
except Exception as e:
    print(f"[MAXIA] PoD router error: {e}")

# V13: Chain Resilience + Status Page (Art.48)
try:
    from chain_resilience import router as resilience_router
    app.include_router(resilience_router)
    print("[Resilience] Circuit Breaker + Status Page monte")
except Exception as e:
    print(f"[MAXIA] Resilience router error: {e}")

# V13: Agent Leaderboard (Art.49)
try:
    from agent_leaderboard import router as leaderboard_router
    app.include_router(leaderboard_router)
    print("[Leaderboard] Agent Scoring + Grades monte")
except Exception as e:
    print(f"[MAXIA] Leaderboard router error: {e}")

# V13: SLA Enforcer (Art.50)
try:
    from sla_enforcer import router as sla_router
    app.include_router(sla_router)
    print("[SLA] Enforcer + Circuit Breaker monte")
except Exception as e:
    print(f"[MAXIA] SLA router error: {e}")

# V13: Pyth Oracle (Art.51)
try:
    from pyth_oracle import router as pyth_router
    app.include_router(pyth_router)
    print("[Pyth] Real-time Oracle (stocks + crypto) monte")
except Exception as e:
    print(f"[MAXIA] Pyth router error: {e}")

# Chat conversationnel (P2)
try:
    from chat_handler import router as chat_router
    app.include_router(chat_router)
    print("[Chat] Conversational trading chat monte")
except Exception as e:
    print(f"[MAXIA] Chat router error: {e}")

# Gamification (P3)
try:
    from gamification import router as gamification_router
    app.include_router(gamification_router)
    print("[Gamification] Points + badges + leaderboard monte")
except Exception as e:
    print(f"[MAXIA] Gamification router error: {e}")

# Jupiter Perps (P5)
try:
    from perps_client import router as perps_router
    app.include_router(perps_router)
    print("[Perps] Jupiter Perpetuals (read-only) monte")
except Exception as e:
    print(f"[MAXIA] Perps router error: {e}")

# Token Launcher — Pump.fun (P6)
try:
    from token_launcher import router as token_router
    app.include_router(token_router)
    print("[TokenLaunch] Pump.fun token launcher monte")
except Exception as e:
    print(f"[MAXIA] Token launcher router error: {e}")

# V13+: Activity Feed (Art.53)
try:
    from activity_feed import router as feed_router
    app.include_router(feed_router)
    print("[Feed] Activity Feed (SSE + REST) monte")
except Exception as e:
    print(f"[MAXIA] Feed router error: {e}")

# V13+: Referral + Badges (Art.54)
try:
    from referral import router as referral_router, badges_router
    app.include_router(referral_router)
    app.include_router(badges_router)
    print("[Referral] Referral + Badges monte")
except Exception as e:
    print(f"[MAXIA] Referral router error: {e}")

# V13+: EVM Multi-Chain Swap — 6 chains via 0x (Art.55)
try:
    from evm_swap import router as evm_swap_router
    app.include_router(evm_swap_router)
    print("[EVM-Swap] Multi-chain swap (6 chains, 36 tokens, 0x) monte")
except Exception as e:
    print(f"[MAXIA] EVM swap error: {e}")

# V13+: Business Listings — AI Business Marketplace (Art.56)
try:
    from business_listing import router as business_router
    app.include_router(business_router)
    print("[Business] AI Business Marketplace (Flippt-style) monte")
except Exception as e:
    print(f"[MAXIA] Business listing error: {e}")

# V13: Reverse Auctions (Art.52)
try:
    from reverse_auction import router as auction_router
    app.include_router(auction_router)
    print("[Auction] Reverse Auctions (RFQ) monte")
except Exception as e:
    print(f"[MAXIA] Auction router error: {e}")

# ═══ Enterprise Suite (6 modules) ═══
try:
    from enterprise_billing import router as billing_router
    app.include_router(billing_router)
    print("[Enterprise] Billing (usage-based metering + invoices) monte")
except Exception as e:
    print(f"[MAXIA] Billing router error: {e}")

try:
    from stripe_billing import router as stripe_router
    app.include_router(stripe_router)
    print("[Enterprise] Stripe Billing (checkout + webhooks + portal) monte")
except Exception as e:
    print(f"[MAXIA] Stripe billing router error: {e}")

try:
    from enterprise_sso import router as sso_router
    app.include_router(sso_router)
    print("[Enterprise] SSO (OIDC/Google/Microsoft) monte")
except Exception as e:
    print(f"[MAXIA] SSO router error: {e}")

try:
    from enterprise_metrics import router as metrics_router, metrics_middleware
    app.include_router(metrics_router)
    app.middleware("http")(metrics_middleware)
    print("[Enterprise] Metrics (Prometheus /metrics + SLA) monte")
except Exception as e:
    print(f"[MAXIA] Metrics router error: {e}")

# Enterprise: tenant context middleware — set tenant_id from X-Tenant or API key
@app.middleware("http")
async def tenant_middleware(request, call_next):
    """Set tenant context from X-Tenant header or API key lookup."""
    try:
        from tenant_isolation import TenantContext
        tenant_id = request.headers.get("X-Tenant", "")
        if not tenant_id:
            # Fallback: extract from API key if authenticated
            wallet = request.headers.get("X-Wallet", "")
            if wallet:
                tenant_id = wallet[:16]  # Use wallet prefix as tenant ID
        async with TenantContext(tenant_id or "default"):
            response = await call_next(request)
        return response
    except Exception:
        return await call_next(request)

try:
    from audit_trail import router as audit_router
    app.include_router(audit_router)
    print("[Enterprise] Audit Trail (compliance + policies) monte")
except Exception as e:
    print(f"[MAXIA] Audit router error: {e}")

try:
    from tenant_isolation import router as tenant_router
    app.include_router(tenant_router)
    print("[Enterprise] Tenant Isolation (multi-tenant + plans) monte")
except Exception as e:
    print(f"[MAXIA] Tenant router error: {e}")

try:
    from enterprise_dashboard import router as dashboard_router
    app.include_router(dashboard_router)
    print("[Enterprise] Dashboard (fleet analytics + SLA + revenue) monte")
except Exception as e:
    print(f"[MAXIA] Dashboard router error: {e}")

try:
    from redis_rate_limiter import router as rate_limit_router
    app.include_router(rate_limit_router)
    print("[RateLimit] Redis Rate Limiter monte")
except Exception as e:
    print(f"[MAXIA] Rate limiter router error: {e}")

try:
    from agent_analytics import router as agent_analytics_router
    app.include_router(agent_analytics_router)
    print("[Analytics] Agent Analytics monte")
except Exception as e:
    print(f"[MAXIA] Agent Analytics router error: {e}")

try:
    from agent_credit import router as agent_credit_router
    app.include_router(agent_credit_router)
    print("[Credit] Agent Credit System monte")
except Exception as e:
    print(f"[MAXIA] Agent Credit router error: {e}")

# V13+: Streaming Payments — pay-per-second (Art.57)
try:
    from streaming_payments import router as stream_router
    app.include_router(stream_router)
    print("[StreamPay] Streaming Payments (pay-per-second) monte")
except Exception as e:
    print(f"[MAXIA] StreamPay router error: {e}")

# V13+: Agent Subcontracting — delegation automatique (Art.58)
try:
    from agent_subcontract import router as subcontract_router
    app.include_router(subcontract_router)
    print("[Subcontract] Agent Subcontracting (delegation) monte")
except Exception as e:
    print(f"[MAXIA] Subcontract router error: {e}")

FRONTEND_INDEX = Path(__file__).parent.parent / "frontend" / "index.html"
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

# Servir les fichiers statiques du dossier frontend (PDF, images, etc.)
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


# ═══════════════════════════════════════════════════════════
#  HTML PAGE ROUTES (pages.py)
# ═══════════════════════════════════════════════════════════
from pages import router as pages_router
app.include_router(pages_router)
print("[Pages] 16 HTML page routes monte")

# ── App Store API endpoints ──

@app.get("/api/public/app-store")
async def app_store_home():
    """AI Agent App Store — featured agents and categories."""
    from app_store import CATEGORIES, get_featured_agents
    featured = await get_featured_agents(db)
    return {
        "categories": CATEGORIES,
        "featured": featured,
        "total_agents": len(featured),
    }

@app.get("/api/public/app-store/category/{category}")
async def app_store_category(category: str):
    """AI Agent App Store — agents by category."""
    from app_store import get_agents_by_category, CATEGORIES_MAP
    if category not in CATEGORIES_MAP:
        raise HTTPException(404, f"Category '{category}' not found")
    agents = await get_agents_by_category(db, category)
    return {
        "category": CATEGORIES_MAP[category],
        "agents": agents,
        "total": len(agents),
    }

@app.get("/api/public/app-store/search")
async def app_store_search(q: str = ""):
    """AI Agent App Store — search agents by name or description."""
    from app_store import search_agents
    if not q or len(q) < 2:
        raise HTTPException(400, "Query must be at least 2 characters")
    # Sanitize query length
    q = q[:100]
    agents = await search_agents(db, q)
    return {
        "query": q,
        "results": agents,
        "total": len(agents),
    }

# ── AI Forum (forum_api.py) ──
from forum_api import router as forum_api_router
app.include_router(forum_api_router)
print("[Forum] Forum API routes monte")

# ── Creator Marketplace ──
@app.get("/api/public/marketplace")
async def marketplace_home():
    from creator_marketplace import TOOL_CATEGORIES, get_tools, ensure_marketplace_tables
    await ensure_marketplace_tables(db)
    tools = await get_tools(db, sort="popular", limit=50)
    total_value = sum(t.get("price_usdc", 0) for t in tools)
    return {
        "categories": TOOL_CATEGORIES,
        "tools": tools,
        "total": len(tools),
        "revenue_split": {"creator": "90%", "platform": "10%"},
        "stats": {
            "total_tools": len(tools),
            "total_creators": len(set(t.get("creator_wallet", "") for t in tools)),
            "revenue_shared": round(total_value * 0.9, 2),
        },
    }

@app.get("/api/public/marketplace/category/{category}")
async def marketplace_category(category: str, sort: str = "popular", limit: int = 20):
    from creator_marketplace import get_tools
    return await get_tools(db, category=category, sort=sort, limit=limit)

@app.get("/api/public/marketplace/tool/{tool_id}")
async def marketplace_tool_detail(tool_id: str):
    from creator_marketplace import get_tool_detail
    return await get_tool_detail(db, tool_id)

@app.post("/api/public/marketplace/publish")
async def marketplace_publish(request: Request):
    from creator_marketplace import publish_tool
    body = await request.json()
    if not body.get("name") or not body.get("creator_wallet"):
        raise HTTPException(400, "name and creator_wallet required")
    from security import check_content_safety
    # check_content_safety raises HTTPException(400) directly if content is blocked
    check_content_safety(body.get("name", "") + " " + body.get("description", ""))
    return await publish_tool(db, body)

@app.post("/api/public/marketplace/tool/{tool_id}/purchase")
async def marketplace_purchase(tool_id: str, request: Request):
    from creator_marketplace import purchase_tool
    body = await request.json()
    return await purchase_tool(db, tool_id, body.get("wallet", ""))

@app.post("/api/public/marketplace/tool/{tool_id}/review")
async def marketplace_review(tool_id: str, request: Request):
    from creator_marketplace import review_tool
    body = await request.json()
    return await review_tool(db, tool_id, body.get("wallet", ""), body.get("rating", 5), body.get("review", ""))

@app.post("/api/public/marketplace/tool/{tool_id}/update")
async def marketplace_update(tool_id: str, request: Request):
    from creator_marketplace import update_tool_version
    body = await request.json()
    return await update_tool_version(db, tool_id, body.get("creator_wallet", ""), body)

@app.get("/api/public/marketplace/search")
async def marketplace_search(q: str = "", limit: int = 20):
    from creator_marketplace import search_tools
    return await search_tools(db, q, limit)

@app.get("/api/creator/stats/{wallet}")
async def creator_stats(wallet: str):
    from creator_marketplace import get_creator_stats
    return await get_creator_stats(db, wallet)

@app.get("/og-image.png", include_in_schema=False)
async def serve_og_image():
    og_path = FRONTEND_DIR / "og-image.png"
    if og_path.exists():
        return FileResponse(str(og_path), media_type="image/png")
    return HTMLResponse("Not found", status_code=404)

@app.get("/favicon.svg", include_in_schema=False)
async def favicon():
    fav_path = FRONTEND_DIR / "favicon.svg"
    if fav_path.exists():
        return FileResponse(str(fav_path), media_type="image/svg+xml")
    return HTMLResponse("", status_code=404)

@app.get("/manifest.json", include_in_schema=False)
async def manifest_json():
    mf_path = FRONTEND_DIR / "manifest.json"
    if mf_path.exists():
        return FileResponse(str(mf_path), media_type="application/json")
    return HTMLResponse("{}", status_code=404, media_type="application/json")

@app.get("/favicon.ico", include_in_schema=False)
async def favicon_ico():
    """Redirect .ico to .svg for browsers that request favicon.ico."""
    fav_path = FRONTEND_DIR / "favicon.svg"
    if fav_path.exists():
        return FileResponse(str(fav_path), media_type="image/svg+xml")
    return HTMLResponse("", status_code=404)

@app.get("/robots.txt", include_in_schema=False)
async def robots_txt():
    robots_path = FRONTEND_DIR / "robots.txt"
    if robots_path.exists():
        return FileResponse(str(robots_path), media_type="text/plain")
    return HTMLResponse("User-agent: *\nAllow: /\nSitemap: https://maxiaworld.app/sitemap.xml", media_type="text/plain")

@app.get("/sitemap.xml", include_in_schema=False)
async def sitemap():
    sitemap_path = FRONTEND_DIR / "sitemap.xml"
    if sitemap_path.exists():
        return FileResponse(str(sitemap_path), media_type="application/xml")
    return HTMLResponse("Not found", status_code=404)

ADMIN_KEY = os.getenv("ADMIN_KEY", "")  # MUST be set in .env — no hardcoded default
_ADMIN_SESSIONS: dict = {}  # token_opaque -> expiry_timestamp


def _verify_admin(request: Request) -> bool:
    """Verifie l'auth admin via header X-Admin-Key OU cookie session opaque."""
    import hmac as _hmac_check
    # 1) Header direct (pour API calls)
    header_key = request.headers.get("X-Admin-Key", "")
    if header_key and ADMIN_KEY and _hmac_check.compare_digest(header_key, ADMIN_KEY):
        return True
    # 2) Cookie session opaque (pour dashboard browser)
    cookie_token = request.cookies.get("maxia_admin", "")
    if cookie_token and cookie_token in _ADMIN_SESSIONS:
        if _ADMIN_SESSIONS[cookie_token] > time.time():
            return True
        else:
            _ADMIN_SESSIONS.pop(cookie_token, None)  # Expire
    return False

@app.get("/admin", response_class=HTMLResponse, include_in_schema=False)
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
  fetch('/admin/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key:key})})
  .then(function(r){if(r.redirected){window.location.href=r.url}else{return r.json().then(function(d){document.getElementById('err').style.display='block'})}})
  .catch(function(){document.getElementById('err').style.display='block'});
  return false;
}
</script></body></html>""")


@app.post("/admin/login", include_in_schema=False)
async def admin_login(req: Request):
    """Verifie la cle admin via POST body, pose un cookie httponly avec token opaque."""
    import hmac as _hmac_v
    from fastapi.responses import RedirectResponse
    try:
        body = await req.json()
        key = body.get("key", "")
    except Exception:
        key = ""
    if not key or not ADMIN_KEY or not _hmac_v.compare_digest(key, ADMIN_KEY):
        raise HTTPException(401, "Invalid admin key")
    # Token opaque au lieu de la cle en clair dans le cookie
    import secrets as _s
    token = _s.token_hex(32)
    _ADMIN_SESSIONS[token] = time.time() + 86400  # 24h
    resp = RedirectResponse(url="/dashboard", status_code=302)
    resp.set_cookie("maxia_admin", token, httponly=True, secure=True, samesite="lax", max_age=86400)
    return resp


@app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
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


# ═══════════════════════════════════════════════════════════
#  AGENT TRUST — AgentID Integration
# ═══════════════════════════════════════════════════════════

@app.get("/api/agent/{address}/trust")
async def get_agent_trust(address: str):
    """Get trust level and escrow rules for an agent (via AgentID)."""
    badge = await agentid_client.get_agent_badge(address)
    return {
        "address": address,
        "trust_level": badge["level"],
        "label": badge["label"],
        "color": badge["color"],
        "escrow_required": badge["escrow_required"],
        "hold_hours": badge["hold_hours"],
        "provider": "agentid" if agentid_client.enabled else "default",
    }

@app.get("/api/agent/{address}/verify")
async def verify_agent_identity(address: str):
    """Full agent identity verification via AgentID."""
    return await agentid_client.verify_agent(address)


# ═══════════════════════════════════════════════════════════
#  AGENT CARD — A2A Discovery (.well-known/agent.json)
# ═══════════════════════════════════════════════════════════

AGENT_CARD = {
    "name": "MAXIA",
    "description": "AI-to-AI Marketplace on 14 chains (Solana, Base, Ethereum, XRP, Polygon, Arbitrum, Avalanche, BNB, TON, SUI, TRON, NEAR, Aptos, SEI). Any AI agent can register, sell services, and buy from other agents. 65 tokens, 25 tokenized stocks, 46 MCP tools, 17 AI services. DeFi yields, cross-chain bridge, escrow on Solana+Base.",
    "url": "https://maxiaworld.app",
    "version": "12.0.0",
    "protocols": ["REST", "JSON-RPC", "MCP", "A2A", "Solana Memo"],
    "payment": {"method": "USDC on Solana", "chain": "solana", "mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"},
    "capabilities": [
        {"name": "marketplace", "description": "AI-to-AI service marketplace. Sell and buy AI services.", "endpoint": "/api/public/discover"},
        {"name": "swap", "description": "Swap 65 tokens across 7 chains. Live prices via Jupiter + 0x.", "endpoint": "/api/public/crypto/swap"},
        {"name": "stocks", "description": "25 tokenized US stocks (xStocks/Ondo/Dinari). Live prices.", "endpoint": "/api/public/stocks"},
        {"name": "defi", "description": "DeFi yield scanner. Best APY across 60+ pools. DeFiLlama + native staking.", "endpoint": "/api/public/defi/best-yield"},
        {"name": "audit", "description": "Smart contract security audit. $9.99.", "endpoint": "/api/public/execute"},
        {"name": "code", "description": "Code generation. Python, Rust, JS. $3.99.", "endpoint": "/api/public/execute"},
        {"name": "scraper", "description": "Web scraping. Structured JSON. $0.05/page.", "endpoint": "/api/public/scrape"},
        {"name": "image", "description": "Image generation. FLUX.1, up to 2048px. $0.10.", "endpoint": "/api/public/image/generate"},
        {"name": "defi", "description": "DeFi yield scanner. Best APY across all protocols. DeFiLlama data.", "endpoint": "/api/public/defi/best-yield"},
        {"name": "monitor", "description": "Wallet monitoring. Real-time alerts. $0.99/mo.", "endpoint": "/api/public/wallet-monitor/add"},
        {"name": "candles", "description": "OHLCV historical price data. 65 tokens, 6 intervals (1m to 1d). Free.", "endpoint": "/api/public/crypto/candles"},
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
*{margin:0;padding:0;box-sizing:border-box}body{background:#060a14;color:#e4e4e7;font-family:'DM Sans',sans-serif;line-height:1.6}
.container{max-width:900px;margin:0 auto;padding:40px 24px}
h1{font-family:'Syne',sans-serif;font-size:32px;background:linear-gradient(135deg,#00e5ff,#7c3aed);-webkit-background-clip:text;-webkit-text-fill-color:transparent;margin-bottom:8px}
h2{font-family:'Syne',sans-serif;font-size:22px;color:#7c3aed;margin:32px 0 16px;padding-top:24px;border-top:1px solid rgba(255,255,255,.05)}
h3{font-family:'Syne',sans-serif;font-size:16px;color:#00e5ff;margin:20px 0 8px}
p{margin-bottom:12px;color:#a1a1aa}
.badge{display:inline-block;padding:2px 10px;border-radius:12px;font-size:12px;font-weight:600;background:rgba(124,58,237,.1);color:#a78bfa;margin-left:8px}
.endpoint{background:rgba(255,255,255,.02);border:1px solid rgba(255,255,255,.05);border-radius:10px;padding:16px;margin:8px 0 16px;transition:border-color .2s}
.endpoint:hover{border-color:rgba(0,229,255,.2)}
.method{display:inline-block;padding:2px 8px;border-radius:4px;font-size:12px;font-weight:700;margin-right:8px}
.get{background:rgba(34,197,94,.1);color:#22c55e}.post{background:rgba(59,130,246,.1);color:#3b82f6}
.url{font-family:'JetBrains Mono',monospace;color:#e4e4e7;font-size:14px}
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
body{font-family:'DM Sans',sans-serif;background:#060a14;color:#e4e4e7;min-height:100vh}
.container{max-width:1100px;margin:0 auto;padding:40px 24px}
h1{font-family:'Syne',sans-serif;font-size:42px;font-weight:800;text-align:center;margin-bottom:8px}
.sub{text-align:center;color:#a1a1aa;font-size:18px;margin-bottom:48px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:20px;margin-bottom:48px}
.card{background:rgba(255,255,255,.02);backdrop-filter:blur(8px);border:1px solid rgba(255,255,255,.05);border-radius:16px;padding:28px;text-align:center}
.card:hover{border-color:rgba(0,229,255,.2);transform:translateY(-3px);transition:all .4s}
.card h3{font-family:'Syne',sans-serif;font-size:20px;margin-bottom:4px}
.card .price{font-family:'Syne',sans-serif;font-size:36px;font-weight:800;margin:16px 0}
.card .price.free{color:#22c55e}
.card .price.blue{color:#00e5ff}
.card .desc{color:#a1a1aa;font-size:14px;line-height:1.6}
.card ul{text-align:left;list-style:none;margin-top:16px}
.card li{padding:6px 0;font-size:14px;color:#e4e4e7}
.card li::before{content:"\\2713 ";color:#00e5ff}
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
      <li>Live crypto prices (65 tokens)</li>
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
      <li>Crypto swap (2000+ pairs)</li>
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
<h2>GPU Pricing (Akash Network)</h2>
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
async def health(request: Request):
    """Health check. Public: status only. Admin: detailed checks."""
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

    # V-09: Public health returns minimal info. Detailed checks behind admin auth.
    admin_key = request.headers.get("X-Admin-Key", "") if hasattr(request, 'headers') else ""
    is_admin = False
    try:
        import hmac as _h
        _ak = os.getenv("ADMIN_KEY", "")
        is_admin = bool(admin_key and _ak and _h.compare_digest(admin_key, _ak))
    except Exception:
        pass

    result = {"status": overall, "version": "12.0.0", "timestamp": int(time.time())}
    if is_admin:
        result["checks"] = checks
        result["networks"] = ["solana-mainnet", "base-mainnet", "ethereum-mainnet", "xrpl-mainnet", "ton-mainnet", "sui-mainnet", "polygon-mainnet", "arbitrum-mainnet", "avalanche-mainnet", "bnb-mainnet", "tron-mainnet", "near-mainnet", "aptos-mainnet", "sei-mainnet"]
    return result


@app.get("/api/public/status")
async def public_status():
    """Live status of all MAXIA systems — chains, oracles, APIs."""
    import httpx

    # Check each chain's RPC
    chains_status = {}
    chain_rpcs = {
        "solana": "https://api.mainnet-beta.solana.com",
        "ethereum": "https://eth.llamarpc.com",
        "base": "https://mainnet.base.org",
        "polygon": "https://polygon-rpc.com",
        "arbitrum": "https://arb1.arbitrum.io/rpc",
        "avalanche": "https://api.avax.network/ext/bc/C/rpc",
        "bnb": "https://bsc-dataseed.binance.org",
    }

    async with httpx.AsyncClient(timeout=5) as client:
        for chain, rpc in chain_rpcs.items():
            try:
                r = await client.post(rpc, json={
                    "jsonrpc": "2.0", "id": 1,
                    "method": "eth_blockNumber" if chain != "solana" else "getSlot",
                    "params": [] if chain != "solana" else [{"commitment": "processed"}],
                })
                chains_status[chain] = {"status": "operational", "latency_ms": int(r.elapsed.total_seconds() * 1000)}
            except Exception:
                chains_status[chain] = {"status": "degraded", "latency_ms": -1}

    # Oracle status
    oracles = {
        "pyth_hermes": {"url": "https://hermes.pyth.network/api/latest_price_feeds?ids[]=0xef0d8b6fda2ceba41da15d4095d1da392a0d2f8ed0c6c7bc0f4cfac8c280b56d", "status": "unknown"},
        "coingecko": {"url": "https://api.coingecko.com/api/v3/ping", "status": "unknown"},
        "defillama": {"url": "https://api.llama.fi/protocols", "status": "unknown"},
    }
    async with httpx.AsyncClient(timeout=5) as client:
        for name, info in oracles.items():
            try:
                r = await client.get(info["url"])
                oracles[name]["status"] = "operational" if r.status_code == 200 else "degraded"
                oracles[name]["latency_ms"] = int(r.elapsed.total_seconds() * 1000)
            except Exception:
                oracles[name]["status"] = "down"
                oracles[name]["latency_ms"] = -1

    # Services status
    services = {
        "swap_solana": "operational",
        "swap_evm": "operational",
        "gpu_rental": "operational",
        "stocks": "operational",
        "escrow": "operational",
        "mcp_server": "operational",
        "a2a_protocol": "operational",
    }

    return {
        "overall": "operational",
        "chains": chains_status,
        "oracles": {k: {"status": v["status"], "latency_ms": v.get("latency_ms", -1)} for k, v in oracles.items()},
        "services": services,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


@app.get("/api/events/stream")
async def event_stream(request: Request):
    """SSE endpoint — stream de donnees temps reel pour le dashboard."""
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


# ── CEO API routes (extracted to ceo_api.py) ──
try:
    from ceo_api import router as ceo_api_router
    app.include_router(ceo_api_router)
    print("[CEO-API] Routes montees")
except Exception as e:
    print(f"[MAXIA] CEO API router error: {e}")


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
        return safe_error(e, "operation")


# ══════════════════════════════════════════
#  AGENT PERMISSIONS — Admin endpoints (freeze/unfreeze/downgrade/revoke/scopes)
# ══════════════════════════════════════════

@app.get("/api/agents/permissions")
async def agents_list_permissions(request: Request):
    """Liste tous les agents et leurs permissions. Admin only."""
    from auth import require_ceo_auth
    await require_ceo_auth(request, request.headers.get("X-CEO-Key"))
    from agent_permissions import list_all_agents
    return {"agents": await list_all_agents()}


@app.get("/api/agents/{agent_id}/permissions")
async def agent_get_permissions(agent_id: str, request: Request):
    """Permissions d'un agent specifique. Admin only."""
    from auth import require_ceo_auth
    await require_ceo_auth(request, request.headers.get("X-CEO-Key"))
    from agent_permissions import get_agent_perms_by_id
    return await get_agent_perms_by_id(agent_id)


@app.post("/api/agents/{agent_id}/freeze")
async def agent_freeze(agent_id: str, request: Request):
    """Freeze un agent — lectures OK, ecritures bloquees."""
    from auth import require_ceo_auth
    await require_ceo_auth(request, request.headers.get("X-CEO-Key"))
    from agent_permissions import freeze_agent
    result = await freeze_agent(agent_id)
    # Audit
    try:
        from audit_trail import audit_log
        await audit_log("admin", "agent_freeze", agent_id, db=db,
                       agent_id=agent_id, metadata={"action": "freeze"})
    except Exception:
        pass
    return result


@app.post("/api/agents/{agent_id}/unfreeze")
async def agent_unfreeze(agent_id: str, request: Request):
    """Unfreeze un agent — retour a active."""
    from auth import require_ceo_auth
    await require_ceo_auth(request, request.headers.get("X-CEO-Key"))
    from agent_permissions import unfreeze_agent
    result = await unfreeze_agent(agent_id)
    try:
        from audit_trail import audit_log
        await audit_log("admin", "agent_unfreeze", agent_id, db=db,
                       agent_id=agent_id, metadata={"action": "unfreeze"})
    except Exception:
        pass
    return result


@app.post("/api/agents/{agent_id}/downgrade")
async def agent_downgrade(agent_id: str, level: int, request: Request):
    """Downgrade le trust level. Les caps s'ajustent automatiquement."""
    from auth import require_ceo_auth
    await require_ceo_auth(request, request.headers.get("X-CEO-Key"))
    from agent_permissions import downgrade_agent
    result = await downgrade_agent(agent_id, level)
    try:
        from audit_trail import audit_log
        await audit_log("admin", "agent_downgrade", agent_id, db=db,
                       agent_id=agent_id, metadata=result)
    except Exception:
        pass
    return result


@app.post("/api/agents/{agent_id}/revoke")
async def agent_revoke(agent_id: str, request: Request):
    """Revoke definitivement un agent. Tout bloque."""
    from auth import require_ceo_auth
    await require_ceo_auth(request, request.headers.get("X-CEO-Key"))
    from agent_permissions import revoke_agent
    result = await revoke_agent(agent_id)
    try:
        from audit_trail import audit_log
        await audit_log("admin", "agent_revoke", agent_id, db=db,
                       agent_id=agent_id, metadata={"action": "revoke"})
    except Exception:
        pass
    return result


@app.post("/api/agents/{agent_id}/scopes")
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


@app.get("/api/agents/scopes/available")
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


@app.post("/api/agents/{agent_id}/rotate-key")
async def agent_rotate_key(agent_id: str, request: Request):
    """Rotate l'API key d'un agent. Garde DID, UAID, trust, historique. Admin only."""
    from auth import require_ceo_auth
    await require_ceo_auth(request, request.headers.get("X-CEO-Key"))
    from agent_permissions import rotate_agent_key
    result = await rotate_agent_key(agent_id)
    try:
        from audit_trail import audit_log
        await audit_log("admin", "agent_key_rotation", agent_id, db=db,
                       agent_id=agent_id, metadata={"old_prefix": result.get("old_key_prefix", "")})
    except Exception:
        pass
    return result


@app.get("/api/public/agent/{identifier}")
async def public_agent_lookup(identifier: str):
    """Resolve un agent par DID ou UAID. Public, sans auth.
    N'importe quel marketplace peut verifier le statut d'un agent MAXIA.

    Examples:
    - GET /api/public/agent/did:web:maxiaworld.app:agent:agent_abc123
    - GET /api/public/agent/7Kj9mN2pQ4rS8tV...  (UAID)
    - GET /api/public/agent/agent_abc123  (agent_id)
    """
    from agent_permissions import resolve_agent_public
    return await resolve_agent_public(identifier)


@app.get("/agent/{agent_id}/did.json")
async def agent_did_document(agent_id: str):
    """W3C DID Document for an agent. Standard did:web resolution.
    did:web:maxiaworld.app:agent:abc123 resolves to GET https://maxiaworld.app/agent/abc123/did.json
    """
    from agent_permissions import generate_did_document
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


@app.get("/.well-known/did.json")
async def maxia_did_document():
    """W3C DID Document for MAXIA itself (the marketplace).
    did:web:maxiaworld.app resolves to GET https://maxiaworld.app/.well-known/did.json
    """
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


@app.post("/api/public/intent/verify")
async def verify_signed_intent(request: Request):
    """Verify a signed intent envelope. Public endpoint.
    Supports both AIP protocol envelopes and legacy MAXIA format.
    Body: the intent JSON.
    """
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
            return verify_intent_legacy(intent, pub_key)
        except Exception as e:
            return {"valid": False, "error": "An error occurred"[:200]}


@app.get("/api/cache/stats")
async def cache_stats():
    """Statistiques du cache prix (hit rate, age)."""
    try:
        from price_oracle import get_cache_stats
        return get_cache_stats()
    except Exception as e:
        return safe_error(e, "operation")


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


@app.get("/api/twitter/status")
async def twitter_status():
    try:
        from twitter_bot import get_stats
        return get_stats()
    except Exception as e:
        return {"error": "An error occurred", "configured": False}


@app.get("/api/reddit/status")
async def reddit_status():
    try:
        from reddit_bot import get_stats
        return get_stats()
    except Exception as e:
        return {"error": "An error occurred", "configured": False}


@app.get("/api/outreach/status")
async def outreach_status():
    """Get agent outreach bot statistics."""
    try:
        from agent_outreach import get_stats
        return get_stats()
    except Exception as e:
        return safe_error(e, "operation")


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
async def watchdog_health(request: Request):
    """Run health check on all endpoints. Admin only."""
    from security import require_admin
    require_admin(request)
    try:
        from ceo_maxia import watchdog_health_check
        return await watchdog_health_check()
    except Exception as e:
        return safe_error(e, "operation")


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


@app.get("/api/admin/errors")
async def admin_errors(request: Request, limit: int = 50, module: str = ""):
    """Error tracker dashboard — dernieres erreurs et stats par module."""
    from auth import require_ceo_auth
    await require_ceo_auth(request, request.headers.get("X-CEO-Key"))
    from error_tracker import get_errors, get_error_stats
    return {"errors": get_errors(limit, module), "stats": get_error_stats()}


@app.get("/api/public/api-pricing")
async def api_pricing():
    """Pricing des tiers API (free, pro, enterprise)."""
    from api_keys import API_TIERS
    return {"tiers": API_TIERS, "currency": "USDC/month"}


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
        return safe_error(e, "operation")


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

# H5: Limite taille message WebSocket (64 KB) — protection contre les payloads geants
_WS_MAX_MESSAGE_SIZE = 65536


async def _ws_receive_json(ws: WebSocket) -> dict:
    """Recoit un message JSON avec controle de taille (H5)."""
    raw = await ws.receive_text()
    if len(raw) > _WS_MAX_MESSAGE_SIZE:
        await ws.close(1009, "Message too large")
        raise WebSocketDisconnect(1009)
    return json.loads(raw)


async def _ws_receive_json_timeout(ws: WebSocket, timeout: float) -> dict:
    """Recoit un message JSON avec timeout et controle de taille (H5)."""
    raw = await asyncio.wait_for(ws.receive_text(), timeout=timeout)
    if len(raw) > _WS_MAX_MESSAGE_SIZE:
        await ws.close(1009, "Message too large")
        raise WebSocketDisconnect(1009)
    return json.loads(raw)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    cid = str(uuid.uuid4())
    _ws_clients[cid] = ws
    authenticated_wallet = None
    try:
        while True:
            # Auth timeout + H5: controle taille message
            if not authenticated_wallet:
                try:
                    msg = await _ws_receive_json_timeout(ws, timeout=30.0)
                except asyncio.TimeoutError:
                    await ws.send_json({"type": "AUTH_TIMEOUT", "error": "Authentication required within 30 seconds"})
                    await ws.close(1008)
                    break
            else:
                msg = await _ws_receive_json(ws)
            if msg.get("type") == "AUTH":
                wallet = msg.get("wallet", "")
                signature = msg.get("signature", "")
                nonce = msg.get("nonce", "")
                if wallet and signature and nonce:
                    # Verify nonce exists and matches
                    from auth import NONCES, _USED_NONCES, _cleanup_used_nonces, _USED_NONCES_MAX
                    entry = NONCES.get(wallet)
                    if not entry or entry[0] != nonce:
                        await ws.send_json({"type": "AUTH_FAILED", "error": "Invalid or expired nonce"})
                        await ws.close(1008)
                        break
                    # Anti-replay: check nonce not already used
                    replay_key = f"{wallet}:{nonce}"
                    if replay_key in _USED_NONCES:
                        await ws.send_json({"type": "AUTH_FAILED", "error": "Nonce already used (replay detected)"})
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
                        # Consume the nonce (anti-replay)
                        NONCES.pop(wallet, None)
                        _USED_NONCES[replay_key] = time.time()
                        if len(_USED_NONCES) > _USED_NONCES_MAX:
                            _cleanup_used_nonces()
                        authenticated_wallet = wallet
                        agent_worker.register_external_agent(wallet)
                        await ws.send_json({"type": "AUTH_OK", "wallet": wallet})
                    except Exception as e:
                        print(f"[WS] Auth signature error: {e}")
                        await ws.send_json({"type": "AUTH_FAILED", "error": "Signature invalide"})
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
            # H5: controle taille message
            msg = await _ws_receive_json(ws)
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
                        print(f"[WS/auctions] Auth signature error: {e}")
                        await ws.send_json({"type": "AUTH_FAILED", "error": "Signature invalide"})
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
    """WebSocket: real-time price updates. Max 5 per IP.

    Modes (send JSON after connect):
      {"mode": "hft"}     — Pyth SSE streaming, push on every price update (<1s)
      {"mode": "normal"}  — polling every 5s (default si pas de message initial)
    """
    ip = websocket.client.host if websocket.client else "unknown"
    _ws_connections[ip] = _ws_connections.get(ip, 0) + 1
    if _ws_connections[ip] > _WS_MAX_PER_IP:
        _ws_connections[ip] -= 1
        await websocket.close(code=1008, reason="Too many connections")
        return
    await websocket.accept()

    # Detecter le mode (attente 2s pour un message optionnel)
    mode = "normal"
    try:
        msg = await asyncio.wait_for(websocket.receive_json(), timeout=2.0)
        mode = msg.get("mode", "normal")
    except (asyncio.TimeoutError, Exception):
        pass  # Pas de message = mode normal

    try:
        if mode == "hft":
            # Mode HFT: subscribe au stream Pyth SSE, push chaque update
            from pyth_oracle import _sse_subscribers, start_pyth_stream
            await start_pyth_stream()
            q: asyncio.Queue = asyncio.Queue(maxsize=50)
            _sse_subscribers.append(q)
            try:
                while True:
                    price_update = await q.get()
                    await websocket.send_json({"type": "price_hft", "data": price_update, "ts": int(time.time())})
            finally:
                _sse_subscribers.remove(q)
        else:
            # Mode normal: polling toutes les 5s
            while True:
                try:
                    from price_oracle import get_crypto_prices
                    prices = await get_crypto_prices()
                    await websocket.send_json({"type": "prices", "data": prices, "ts": int(time.time())})
                except WebSocketDisconnect:
                    break
                except Exception as e:
                    err_msg = str(e).lower()
                    if err_msg and "close" not in err_msg and "cancelled" not in err_msg:
                        print(f"[WS/prices] Error: {e}")
                await asyncio.sleep(5)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        err_msg = str(e).lower()
        if err_msg and "disconnect" not in err_msg and "close" not in err_msg and "cancelled" not in err_msg:
            print(f"[WS/prices] Connection error: {e}")
    finally:
        _ws_connections[ip] = max(0, _ws_connections.get(ip, 1) - 1)

@app.websocket("/ws/chart")
async def ws_chart(websocket: WebSocket):
    """WebSocket: real-time OHLCV candles from Pyth SSE stream.
    Push candle updates every tick (<1s). Supports 1s, 5s, 1m intervals.

    Send after connect: {"symbol": "SOL", "interval": 1}  (interval in seconds)
    Receives: {"type": "candle_update", "symbol": "SOL", "interval": 1, "time": ..., "open": ..., "high": ..., "low": ..., "close": ...}
    """
    # Per-IP connection limit (same as /ws/prices)
    ip = websocket.client.host if websocket.client else "unknown"
    _ws_connections[ip] = _ws_connections.get(ip, 0) + 1
    if _ws_connections[ip] > _WS_MAX_PER_IP:
        _ws_connections[ip] -= 1
        await websocket.close(code=1008, reason="Too many connections")
        return
    await websocket.accept()
    try:
        params = await _ws_receive_json_timeout(websocket, timeout=5.0)
        symbol = params.get("symbol", "SOL").upper()[:20]
        interval = int(params.get("interval", 1))
        if interval not in (1, 5, 60, 3600, 21600, 86400):
            interval = 1

        from pyth_oracle import _candle_subscribers, get_recent_candles

        # Envoyer l'historique recent
        history = get_recent_candles(symbol, interval, limit=300)
        if history:
            await websocket.send_json({"type": "history", "symbol": symbol, "interval": interval, "candles": history})

        # Souscrire aux updates live
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        _candle_subscribers.append(q)
        try:
            while True:
                msg = await q.get()
                if msg.get("symbol") == symbol and msg.get("interval") == interval:
                    await websocket.send_json(msg)
        finally:
            _candle_subscribers.remove(q)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        err_msg = str(e).lower()
        if err_msg and "disconnect" not in err_msg and "close" not in err_msg and "cancelled" not in err_msg:
            print(f"[WS/chart] Error: {e}")
    finally:
        _ws_connections[ip] = max(0, _ws_connections.get(ip, 1) - 1)


@app.websocket("/ws/candles")
async def ws_candles(websocket: WebSocket):
    """WebSocket: real-time candle updates every 60 seconds."""
    # Per-IP connection limit (same as /ws/prices)
    ip = websocket.client.host if websocket.client else "unknown"
    _ws_connections[ip] = _ws_connections.get(ip, 0) + 1
    if _ws_connections[ip] > _WS_MAX_PER_IP:
        _ws_connections[ip] -= 1
        await websocket.close(code=1008, reason="Too many connections")
        return
    await websocket.accept()
    try:
        # Get subscription params from first message (H5: controle taille)
        params = await _ws_receive_json_timeout(websocket, timeout=10.0)
        symbol = params.get("symbol", "SOL").upper()[:20]  # Max 20 chars
        interval = params.get("interval", "1m")
        # Validate symbol format (alphanumeric only) and interval whitelist
        import re as _re_ws
        if not _re_ws.match(r'^[A-Z0-9_/]{1,20}$', symbol):
            await websocket.send_json({"error": "Invalid symbol format"})
            await websocket.close()
            return
        _VALID_INTERVALS = {"1m", "5m", "15m", "1h", "4h", "1d", "1w"}
        if interval not in _VALID_INTERVALS:
            await websocket.send_json({"error": f"Invalid interval. Valid: {', '.join(sorted(_VALID_INTERVALS))}"})
            await websocket.close()
            return
        while True:
            try:
                rows = await db.raw_execute_fetchall(
                    "SELECT symbol, interval, open, high, low, close, volume, timestamp FROM price_candles "
                    "WHERE symbol=? AND interval=? ORDER BY timestamp DESC LIMIT 1", (symbol, interval))
                if rows:
                    r = rows[0]
                    await websocket.send_json({"type": "candle", "symbol": symbol, "interval": interval,
                        "o": r["open"], "h": r["high"], "l": r["low"], "c": r["close"], "v": r["volume"], "t": r["timestamp"]})
            except WebSocketDisconnect:
                break
            except Exception as e:
                err_msg = str(e).lower()
                if err_msg and "close" not in err_msg and "cancelled" not in err_msg:
                    print(f"[WS/candles] Error: {e}")
            await asyncio.sleep(60 if interval != "1m" else 10)
    except Exception as e:
        if "disconnect" not in str(e).lower():
            print(f"[WS/candles] Connection error: {e}")
    finally:
        _ws_connections[ip] = max(0, _ws_connections.get(ip, 1) - 1)


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
#  GPU AUCTIONS + RENTAL (Art.5) — extracted to gpu_api.py
# ═══════════════════════════════════════════════════════════
try:
    from gpu_api import router as gpu_api_router
    app.include_router(gpu_api_router)
    print("[GPU-API] Routes montees")
except Exception as e:
    print(f"[MAXIA] GPU API router error: {e}")


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


@app.get("/api/agents/{wallet}/portfolio-stats")
async def agent_portfolio_stats(wallet: str):
    """Retourne les stats portfolio: swaps, volume 30j, tier, fees saved, activite recente, badges."""
    try:
        swap_count = await db.get_swap_count(wallet)
    except Exception:
        swap_count = 0
    try:
        volume = await db.get_swap_volume_30d(wallet)
    except Exception:
        try:
            volume = await db.get_agent_volume_30d(wallet)
        except Exception:
            volume = 0.0
    bps = get_commission_bps(volume)
    tiers = [{"name": "WHALE", "min": 5000, "bps": 1}, {"name": "GOLD", "min": 500, "bps": 3},
             {"name": "SILVER", "min": 100, "bps": 5}, {"name": "BRONZE", "min": 0, "bps": 10}]
    tier = next((t["name"] for t in tiers if volume >= t["min"]), "BRONZE")
    tier_bps = next((t["bps"] for t in tiers if volume >= t["min"]), 10)
    # Fees saved vs baseline 0.10%
    baseline_bps = 10
    fees_saved = volume * (baseline_bps - tier_bps) / 10000

    # Recent activity (last 20 transactions for this wallet)
    activity = []
    try:
        rows = await db.raw_execute_fetchall(
            "SELECT tx_signature, amount_usdc, purpose, created_at FROM transactions "
            "WHERE wallet = ? ORDER BY created_at DESC LIMIT 20", (wallet,))
        for r in rows:
            activity.append({
                "tx": r["tx_signature"] if isinstance(r, dict) else r[0],
                "amount": r["amount_usdc"] if isinstance(r, dict) else r[1],
                "purpose": r["purpose"] if isinstance(r, dict) else r[2],
                "date": r["created_at"] if isinstance(r, dict) else r[3],
            })
    except Exception:
        pass

    # Badges
    badges = []
    try:
        badge_rows = await db.raw_execute_fetchall(
            "SELECT badge_name, badge_icon, earned_at FROM badges WHERE agent_id = ? ORDER BY earned_at DESC",
            (wallet,))
        for r in badge_rows:
            name = r["badge_name"] if isinstance(r, dict) else r[0]
            icon = r["badge_icon"] if isinstance(r, dict) else r[1]
            earned = r["earned_at"] if isinstance(r, dict) else r[2]
            badges.append({"name": name, "icon": icon, "earned_at": earned})
    except Exception:
        pass

    return {
        "wallet": wallet,
        "swap_count": swap_count,
        "volume30d": volume,
        "tier": tier,
        "commission_bps": tier_bps,
        "fees_saved": round(fees_saved, 2),
        "activity": activity,
        "badges": badges,
    }


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
async def agent_status(request: Request):
    """Statut complet de l'agent autonome. Admin only."""
    from security import require_admin
    require_admin(request)
    return {
        "brain": brain.get_stats(),
        "growth": growth_agent.get_stats(),
        "scout": scout_agent.get_stats(),
        "daily_spend": get_daily_spend_stats(),
    }

@app.get("/api/agent/brain")
async def brain_status(request: Request):
    from security import require_admin
    require_admin(request)
    return brain.get_stats()

@app.get("/api/agent/growth")
async def growth_status(request: Request):
    from security import require_admin
    require_admin(request)
    return growth_agent.get_stats()

@app.get("/api/agent/preflight")
async def preflight(request: Request):
    """Diagnostic systeme complet. Admin only — exposes system diagnostics."""
    from security import require_admin
    require_admin(request)
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
async def pricing_status(request: Request):
    """Pricing strategy status. Admin only."""
    from security import require_admin
    require_admin(request)
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
#  V11: ESCROW ON-CHAIN (Art.21) — extracted to escrow_api.py
# ══════════════════════════════════════════════════════════
try:
    from escrow_api import router as escrow_api_router
    app.include_router(escrow_api_router)
    print("[ESCROW-API] Routes montees")
except Exception as e:
    print(f"[MAXIA] Escrow API router error: {e}")


# ══════════════════════════════════════════════════════════
#  ADMIN: Seed initial services (one-time setup)
# ══════════════════════════════════════════════════════════
from seed_data import INITIAL_SERVICES, INITIAL_DATASETS

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
        return safe_error(e, "operation")


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
        return safe_error(e, "operation")


# ── Chain verification routes (extracted to chain_api.py) ──
try:
    from chain_api import router as chain_api_router
    app.include_router(chain_api_router)
    print("[CHAIN-API] Routes montees")
except Exception as e:
    print(f"[MAXIA] Chain API router error: {e}")


# ══════════════════════════════════════════════════════════
#  V11: BOURSE ACTIONS TOKENISEES (Art.23)
# ══════════════════════════════════════════════════════════

@app.get("/api/stocks/stats")
async def stock_exchange_stats():
    from tokenized_stocks import stock_exchange
    return stock_exchange.get_stats()


@app.get("/api/stocks/market-status")
async def stock_market_status():
    """Reference info: US stock market hours (for price feed context only). MAXIA tokenized stocks trade 24/7 on-chain."""
    from datetime import datetime, timezone, timedelta
    et_offset = timedelta(hours=-5)  # EST (simplified — DST would be -4)
    now_utc = datetime.now(timezone.utc)
    now_et = now_utc + et_offset
    # Check DST (Mar-Nov): second Sunday Mar to first Sunday Nov
    month = now_et.month
    if 3 < month < 11:
        et_offset = timedelta(hours=-4)
        now_et = now_utc + et_offset
    elif month == 3:
        # Second Sunday of March
        second_sunday = 14 - (datetime(now_et.year, 3, 1).weekday() + 1) % 7
        if now_et.day >= second_sunday:
            et_offset = timedelta(hours=-4)
            now_et = now_utc + et_offset
    elif month == 11:
        first_sunday = 7 - (datetime(now_et.year, 11, 1).weekday() + 1) % 7
        if now_et.day < first_sunday:
            et_offset = timedelta(hours=-4)
            now_et = now_utc + et_offset

    weekday = now_et.weekday()  # 0=Monday, 6=Sunday
    hour = now_et.hour
    minute = now_et.minute
    time_minutes = hour * 60 + minute  # minutes since midnight

    is_weekday = weekday < 5
    market_open_min = 9 * 60 + 30   # 9:30 AM ET
    market_close_min = 16 * 60       # 4:00 PM ET
    pre_market_open = 4 * 60         # 4:00 AM ET
    after_hours_close = 20 * 60      # 8:00 PM ET

    if not is_weekday:
        status = "closed"
        session = "weekend"
    elif pre_market_open <= time_minutes < market_open_min:
        status = "pre_market"
        session = "Pre-Market (4:00 AM - 9:30 AM ET)"
    elif market_open_min <= time_minutes < market_close_min:
        status = "open"
        session = "Regular Trading (9:30 AM - 4:00 PM ET)"
    elif market_close_min <= time_minutes < after_hours_close:
        status = "after_hours"
        session = "After-Hours (4:00 PM - 8:00 PM ET)"
    else:
        status = "closed"
        session = "Closed"

    # Next open time
    if status in ("open", "pre_market", "after_hours"):
        next_open = "Now (or next regular session at 9:30 AM ET)"
    elif weekday == 4 and time_minutes >= after_hours_close:
        next_open = "Monday 9:30 AM ET"
    elif weekday >= 5:
        days_until_monday = (7 - weekday) % 7
        if days_until_monday == 0:
            days_until_monday = 1
        next_open = f"Monday 9:30 AM ET ({days_until_monday} day{'s' if days_until_monday > 1 else ''})"
    else:
        next_open = "Today 9:30 AM ET" if time_minutes < market_open_min else "Tomorrow 9:30 AM ET"

    return {
        "maxia_status": "open_24_7",
        "maxia_note": "MAXIA tokenized stocks trade 24/7 — they are on-chain tokens, not traditional equities.",
        "nyse_status": status,
        "nyse_session": session,
        "current_time_et": now_et.strftime("%Y-%m-%d %H:%M ET"),
        "nyse_next_open": next_open,
        "note": "Tokenized stocks are synthetic on-chain assets (NOT traditional equities). Trade 24/7. Prices reference the underlying stock via oracle. Off-hours = wider oracle spreads.",
        "providers": {
            "xStocks_Backed": {"chain": "Solana", "stocks": 11},
            "Ondo_GM": {"chain": "Ethereum", "stocks": 2},
            "Dinari_dShares": {"chain": "Arbitrum", "stocks": 12},
        },
    }


@app.get("/api/public/tokens/candidates")
async def token_candidates():
    """Auto-listing: discover trending tokens with volume > $100K on supported chains."""
    from token_autolisting import get_listing_candidates
    return await get_listing_candidates()


# ── Agent Credit Score (portable, verifiable) ──

@app.get("/api/public/credit-score/{wallet}")
async def get_credit_score(wallet: str):
    """Get portable credit score for an agent. Verifiable by any platform."""
    from agent_credit_score import compute_credit_score
    return await compute_credit_score(wallet, db)


@app.post("/api/public/credit-score/verify")
async def verify_credit_score(request: Request):
    """Verify a credit score signature from another platform."""
    from agent_credit_score import verify_score_signature, VERIFICATION_FEE_USDC
    body = await request.json()
    valid = verify_score_signature(
        body.get("wallet", ""),
        body.get("score", 0),
        body.get("grade", ""),
        body.get("computed_at", ""),
        body.get("signature", ""),
    )
    return {"valid": valid, "fee_usdc": VERIFICATION_FEE_USDC}


# ═══════════════════════════════════════════════════════════
#  ALERT SERVICE — $0.99/mo Telegram alerts (price/whale/yield/tx)
# ═══════════════════════════════════════════════════════════

@app.post("/api/public/alerts/subscribe")
async def alert_subscribe(request: Request):
    """Subscribe to MAXIA Telegram alerts ($0.99/month USDC)."""
    from alert_service import subscribe
    body = await request.json()
    return await subscribe(body.get("wallet", ""), body.get("chat_id", ""), body.get("alerts"))

@app.post("/api/public/alerts/unsubscribe")
async def alert_unsubscribe(request: Request):
    """Unsubscribe from MAXIA Telegram alerts."""
    from alert_service import unsubscribe
    body = await request.json()
    return await unsubscribe(body.get("wallet", ""))

@app.get("/api/public/alerts/plans")
async def alert_plans():
    """Available alert subscription plans."""
    return {
        "plans": [
            {"name": "Basic", "price_usdc": 0.99, "period": "monthly", "alerts": ["price", "whale", "yield", "transaction"]},
        ],
        "free_alerts": ["transaction"],  # Transaction alerts are free for all users
    }


# ═══════════════════════════════════════════════════════════
#  ENTERPRISE — Fleet Management & Compliance Reports
# ═══════════════════════════════════════════════════════════

@app.get("/api/enterprise/fleet/{wallet}")
async def enterprise_fleet(wallet: str, request: Request):
    """Fleet overview — all agents owned by a wallet."""
    from fleet_manager import get_fleet_overview
    return await get_fleet_overview(wallet, db)

@app.post("/api/enterprise/fleet/toggle")
async def enterprise_toggle_agent(request: Request):
    """Activate/deactivate an agent in the fleet."""
    from security import require_admin
    require_admin(request)
    from fleet_manager import toggle_agent
    body = await request.json()
    return await toggle_agent(body.get("api_key", ""), body.get("enabled", True), db)

@app.get("/api/enterprise/compliance/{wallet}")
async def enterprise_compliance(wallet: str, request: Request, period: int = 30):
    """Generate a compliance report for a wallet (last N days)."""
    from compliance_report import generate_compliance_report
    return await generate_compliance_report(wallet, db, period)


@app.post("/api/enterprise/contact")
async def enterprise_contact(request: Request):
    """Receive enterprise contact form. Store in DB + send email + alert Telegram."""
    import time as _t
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
    lead_id = f"lead_{int(_t.time())}_{company[:10].replace(' ','_')}"
    try:
        await db.raw_execute(
            "INSERT INTO enterprise_leads(id, company, contact_name, email, website, agent_count, plan, volume, use_case, source, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (lead_id, company, contact_name, email, website, agent_count, plan, volume, use_case, source, int(_t.time())))
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
                (lead_id, company, contact_name, email, website, agent_count, plan, volume, use_case, source, int(_t.time())))
        except Exception as e:
            print(f"[Enterprise] DB error: {e}")

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
        print(f"[Enterprise] Email error: {e}")

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
