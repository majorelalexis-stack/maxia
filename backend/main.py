"""MAXIA Backend V12 — Art.1 to Art.15 + 47 features (14 chains: Solana + Base + Ethereum + XRP + Polygon + Arbitrum + Avalanche + BNB + TON + SUI + TRON + NEAR + Aptos + SEI + 17 AI Agents)"""
import logging
import asyncio, os, uuid, time, json
from contextlib import asynccontextmanager
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Sentry error tracking (S37) ──
_SENTRY_DSN = os.getenv("SENTRY_DSN", "")
if _SENTRY_DSN:
    try:
        import sentry_sdk
        sentry_sdk.init(dsn=_SENTRY_DSN, traces_sample_rate=0.1, environment=os.getenv("ENV", "production"))
        logging.getLogger(__name__).info("Sentry initialized")
    except Exception:
        pass

from core.error_utils import safe_error

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)

# ── Core imports ──
from core.database import db, create_database
from core.auth import router as auth_router, require_auth, require_auth_flexible
from agents.agent_worker import agent_worker
from core.ws_handlers import broadcast_all, send_to_wallet, init_redis_pubsub, auction_manager, router as ws_router
from core.lifespan_workers import dispute_auto_resolve_worker, volume_decay_worker, price_broadcast_loop
from billing.referral_manager import router as ref_router
from marketplace.data_marketplace import router as data_router
from core.models import (
    AuctionCreateRequest, AuctionSettleRequest, CommandRequest,
    ListingCreateRequest, BaseVerifyRequest, AP2PaymentRequest,
    GpuRentRequest, GpuRentPublicRequest,
)
from gpu.runpod_client import RunPodClient, get_gpu_tiers_live, GPU_MAP
try:
    from gpu.akash_client import AkashClient, akash as akash_client, AKASH_GPU_MAP, AKASH_MAX_PRICE, _active_deployments
    logger.info("[Akash] Module charge OK — %d GPU mappings", len(AKASH_GPU_MAP))
except Exception as e:
    logger.warning("[Akash] Import echoue: %s — mode RunPod only", e)
    akash_client = None
    AKASH_GPU_MAP = {}
    AKASH_MAX_PRICE = 10.0
    _active_deployments = {}
    class AkashClient:
        pass
try:
    from agents.agentid_client import agentid as agentid_client
except ImportError:
    agentid_client = None
from core.config import AKASH_ENABLED
from blockchain.solana_verifier import verify_transaction
from core.security import check_content_safety, check_rate_limit, set_redis_client
from core.redis_client import redis_client
from core.config import (
    GPU_TIERS, COMMISSION_TIERS, get_commission_bps,
    TREASURY_ADDRESS, TREASURY_ADDRESS_BASE,
    TREASURY_ADDRESS_POLYGON, TREASURY_ADDRESS_ARBITRUM,
    TREASURY_ADDRESS_AVALANCHE, TREASURY_ADDRESS_BNB,
    SUPPORTED_NETWORKS, X402_PRICE_MAP,
    SERVICE_PRICES,
)
_gpu_cheapest = f"${min(t['base_price_per_hour'] for t in GPU_TIERS if not t.get('local')):.2f}/h"

# ── V12: EVM verifiers extracted to chain_verify_api.py ──
from integrations.kiteai_client import kite_client
from integrations.ap2_manager import ap2_manager
from integrations.x402_middleware import x402_middleware

# ── V10.1 — Agent Autonome (CEO VPS SUPPRIME — S20) ──
# growth_agent, scout_agent, brain, scheduler, swarm: REMOVED from VPS
# CEO runs ONLY on local PC (7900XT Ollama). VPS = marketplace only.
from infra.alerts import alert_system
from infra.preflight import check_system_ready, print_preflight
from core.security import get_daily_spend_stats
from infra.dynamic_pricing import adjust_market_fees, get_pricing_status
from blockchain.cross_chain_handler import cross_chain
from infra.reputation_staking import reputation_staking
from infra.scale_out import scale_out_manager
from blockchain.escrow_client import escrow_client
from marketplace.public_api import router as public_router

try:
    from marketplace.mcp_server import router as mcp_router
except ImportError:
    mcp_router = None

# ── Runtime config ──
BROKER_MARGIN      = float(os.getenv("BROKER_MARGIN", "1.00"))  # matches config.py
AUCTION_DURATION_S = int(os.getenv("AUCTION_DURATION_S", "30"))

runpod          = RunPodClient(api_key=os.getenv("RUNPOD_API_KEY", ""))
# ── Native AI Services (registered at startup via seed_data.py) ──
from core.seed_data import register_native_services


# ── Lifespan ──

@asynccontextmanager
async def lifespan(app: FastAPI):
    # AgentOps — AVANT tout import LLM pour auto-instrumentation Groq/Anthropic
    from agents.agentops_integration import init_agentops, shutdown_agentops
    init_agentops()

    # V12: Redis connect (graceful fallback to in-memory)
    from core.config import REDIS_URL
    await redis_client.connect(REDIS_URL)
    set_redis_client(redis_client)

    # V12: Redis pub/sub pour WebSocket multi-worker
    await init_redis_pubsub()

    # V12: GPU pricing live — fetch les prix RunPod au demarrage + auto-refresh 30min
    try:
        from gpu.gpu_pricing import refresh_gpu_prices, auto_refresh_loop
        await refresh_gpu_prices()
        asyncio.create_task(auto_refresh_loop())
    except Exception as e:
        logger.error("[GPU Pricing] Init error: %s — prix fallback utilises", e)

    # V12: Database factory — PostgreSQL if DATABASE_URL set, else SQLite
    from core import database as _db_mod
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
                 "avg_response_ms NUMERIC(18,6) DEFAULT 0", "uptime_pct NUMERIC(18,6) DEFAULT 100"]:
        try:
            await db.raw_execute(f"ALTER TABLE agent_services ADD COLUMN {col}")
        except Exception:
            pass  # Column already exists
    await escrow_client._load_from_db()
    agent_worker.set_broadcast(broadcast_all)

    # Inject WS per-wallet callback into forum module
    try:
        from routes import forum as _forum_mod
        _forum_mod._ws_notify_callback = send_to_wallet
    except Exception:
        pass

    # V12: Register 8 MAXIA native AI services (Groq/Ollama)
    await register_native_services(db)

    # Pre-create forum tables (idempotent) — prevents 502 race condition on first POST
    try:
        await db.raw_executescript(
            "CREATE TABLE IF NOT EXISTS forum_posts("
            "id TEXT PRIMARY KEY, data TEXT NOT NULL, community TEXT DEFAULT 'general', "
            "hot_score NUMERIC(18,6) DEFAULT 0, created_at INTEGER, status TEXT DEFAULT 'active');"
            "CREATE TABLE IF NOT EXISTS forum_replies("
            "id TEXT PRIMARY KEY, post_id TEXT, data TEXT NOT NULL, "
            "created_at INTEGER, status TEXT DEFAULT 'active');"
            "CREATE TABLE IF NOT EXISTS forum_votes("
            "id TEXT PRIMARY KEY, post_id TEXT, wallet TEXT, vote INTEGER, "
            "created_at INTEGER);"
            "CREATE TABLE IF NOT EXISTS forum_reports("
            "id TEXT PRIMARY KEY, post_id TEXT, wallet TEXT, reason TEXT, "
            "created_at INTEGER);"
            "CREATE TABLE IF NOT EXISTS forum_notifications("
            "id TEXT PRIMARY KEY, wallet TEXT NOT NULL, type TEXT NOT NULL DEFAULT 'reply', "
            "post_id TEXT, reply_id TEXT, payload TEXT NOT NULL DEFAULT '{}', "
            "read INTEGER DEFAULT 0, created_at INTEGER DEFAULT (strftime('%s','now')));"
            "CREATE INDEX IF NOT EXISTS idx_posts_community ON forum_posts(community);"
            "CREATE INDEX IF NOT EXISTS idx_posts_hot ON forum_posts(hot_score DESC);"
            "CREATE INDEX IF NOT EXISTS idx_replies_post ON forum_replies(post_id);"
            "CREATE INDEX IF NOT EXISTS idx_notif_wallet_read ON forum_notifications(wallet, read);"
            "CREATE INDEX IF NOT EXISTS idx_notif_created ON forum_notifications(created_at);")
        # Cleanup old notifications (>30 days)
        await db.raw_execute(
            "DELETE FROM forum_notifications WHERE created_at < ?",
            (int(time.time()) - 30 * 86400,))
    except Exception as e:
        logger.error("[Forum] Table init error: %s", e)

    # Seed forum with initial posts
    try:
        from routes.forum_seed import seed_forum
        await seed_forum(db)
    except Exception as e:
        logger.error("[Forum] Seed error: %s", e)

    # Marketplace tables + seed native services
    try:
        from features.creator_marketplace import ensure_marketplace_tables
        await ensure_marketplace_tables(db)
    except Exception as e:
        logger.error("[Marketplace] Init error: %s", e)

    # Index for referral code lookup (substr(api_key, 7, 8))
    try:
        await db.raw_execute(
            "CREATE INDEX IF NOT EXISTS idx_agents_referral_code ON agents(substr(api_key, 7, 8))")
    except Exception:
        pass

    # V12: Ensure referred_by column exists in agents table
    try:
        await db.raw_execute(
            "ALTER TABLE agents ADD COLUMN referred_by TEXT DEFAULT ''")
    except Exception:
        pass  # Column already exists

    # V12: Init new modules (API keys, SLA, webhooks)
    from billing.api_keys import ensure_tables as ensure_api_keys_tables
    from integrations.webhook_dispatcher import ensure_tables as ensure_webhook_tables, retry_worker
    from enterprise.sla_manager import ensure_tables as ensure_sla_tables
    await ensure_api_keys_tables(db)
    await ensure_webhook_tables(db)
    await ensure_sla_tables(db)
    # disputes table is already created in DB_SCHEMA (database.py)

    t1 = asyncio.create_task(auction_manager.run_expiry_worker())
    t4 = asyncio.create_task(retry_worker(db))  # V12: webhook retry worker

    # V12: Health monitor (UptimeRobot-style)
    try:
        from infra.health_monitor import run_health_monitor
        t_health = asyncio.create_task(run_health_monitor())
    except Exception as e:
        logger.error("[MAXIA] Health monitor init error: %s", e)
        t_health = None

    # V12: Pyth SSE streaming (prix live <1s pour clients HFT)
    try:
        from trading.pyth_oracle import start_pyth_stream
        await start_pyth_stream()
    except Exception as e:
        logger.error("[MAXIA] Pyth stream init error: %s — HTTP polling fallback", e)

    # Enterprise: billing flush loop (persiste usage toutes les 60s)
    try:
        from enterprise.enterprise_billing import billing_flush_loop
        asyncio.create_task(billing_flush_loop())
        logger.info("[Enterprise] Billing flush loop started")
    except Exception as e:
        logger.error("[MAXIA] Billing flush loop error: %s", e)

    # V12: New features (trading, marketplace, infra)
    try:
        from trading.trading_features import ensure_tables as ensure_trading_tables, check_whales, update_candles, copy_trade_worker
        await ensure_trading_tables()
        t6 = asyncio.create_task(check_whales())
        t7 = asyncio.create_task(update_candles())
        t_copy = asyncio.create_task(copy_trade_worker())
        # Universal candle feeder — feeds ALL tokens (not just Pyth) every 5s
        from trading.pyth_oracle import _universal_candle_feeder
        asyncio.create_task(_universal_candle_feeder())
    except Exception as e:
        logger.error("[MAXIA] Trading features init error: %s", e)
        t6 = t7 = None
    try:
        from marketplace.marketplace_features import ensure_tables as ensure_mkt_tables
        await ensure_mkt_tables()
    except Exception as e:
        logger.error("[MAXIA] Marketplace features init error: %s", e)
    try:
        from features.infra_features import ensure_tables as ensure_infra_tables
        await ensure_infra_tables()
    except Exception as e:
        logger.error("[MAXIA] Infra features init error: %s", e)

    # V12: DB backup
    t_backup = None
    try:
        from infra.db_backup import run_backup_scheduler
        t_backup = asyncio.create_task(run_backup_scheduler())
    except Exception as e:
        logger.error("[MAXIA] DB backup init error: %s", e)

    # V12: Dispute auto-resolve worker (S33: extracted to lifespan_workers.py)
    t_dispute = asyncio.create_task(dispute_auto_resolve_worker(db))

    # V12: Volume 30d rolling reset (S33: extracted to lifespan_workers.py)
    t_volume = asyncio.create_task(volume_decay_worker(db))

    # V12: Load persisted trading data (alerts + follows) from DB
    try:
        from trading.trading_tools import load_trading_data
        await load_trading_data()
    except Exception as e:
        logger.error("[MAXIA] Trading data load error: %s", e)

    # V12: Price alerts worker (notifies CLIENTS, not founder)
    try:
        from trading.trading_tools import alert_checker_worker
        t_alerts = asyncio.create_task(alert_checker_worker())
        logger.info("[MAXIA] Price alerts worker started (60s interval)")
    except Exception as e:
        logger.error("[MAXIA] Alert worker init error: %s", e)

    # CEO task queue — REMOVED (Plan CEO V4: CEO = local only)

    # Init file logger
    try:
        from core.logger import app_logger
        app_logger.info("MAXIA V12 starting up")
    except Exception:
        pass

    # Preflight env check
    try:
        from infra.preflight import check_system_ready, print_preflight
        pf = await check_system_ready()
        print_preflight(pf)
        missing = pf.get("env_vars", {}).get("missing_critical", [])
        if missing:
            logger.critical("[MAXIA] ⚠️  Missing critical env vars: %s", ', '.join(missing))
    except Exception as e:
        logger.error("[MAXIA] Preflight error: %s", e)

    # Security checks at startup
    from core.security import check_jwt_secret, check_admin_key, _flush_audit
    if not check_jwt_secret():
        logger.info("[MAXIA] ⚠️  Set JWT_SECRET in .env for production security!")
    # H4: Validation ADMIN_KEY au demarrage
    check_admin_key()

    # V12: Price broadcast loop (S33: extracted to lifespan_workers.py)
    t_price_broadcast = asyncio.create_task(price_broadcast_loop(broadcast_all))
    logger.info("[MAXIA] Price broadcast loop started (30s interval)")

    # V13+: Streaming Payments updater (60s interval)
    try:
        from features.streaming_payments import stream_updater_loop
        asyncio.create_task(stream_updater_loop())
        logger.info("[MAXIA] Streaming payments updater started (60s interval)")
    except Exception as e:
        logger.error("[MAXIA] Stream updater init error: %s", e)

    # V12: WS Event Stream — periodic price + stats push to event subscribers
    try:
        from features.ws_events import start_periodic_events
        await start_periodic_events()
    except Exception as e:
        logger.error("[MAXIA] WS events periodic init error: %s", e)

    # Telegram bot — REMOVED from lifespan: scheduler.py already starts run_telegram_bot()
    # as one of its tasks (line 75). Running it twice causes duplicate getUpdates polling,
    # which leads to missed approval button callbacks and 409 Conflict errors from Telegram API.
    t_telegram = None

    # Pyth SSE permanent — stream prix live en continu (pas on-demand)
    try:
        from trading.pyth_oracle import start_pyth_stream, start_fallback_refresh, start_equity_poll
        await start_pyth_stream()
        await start_equity_poll()
        await start_fallback_refresh()
        logger.info("[MAXIA] Pyth SSE persistent stream + equity poll (2s) + fallback auto-refresh started")
    except Exception as e:
        logger.error("[MAXIA] Pyth stream init error: %s", e)

    # Chainlink Oracle — verification feeds on-chain Base au demarrage
    try:
        from trading.chainlink_oracle import verify_feeds_at_startup
        cl_results = await verify_feeds_at_startup()
        verified = sum(1 for v in cl_results.values() if v.get("verified"))
        logger.info("[MAXIA] Chainlink Base: %s/%s feeds verified on-chain", verified, len(cl_results))
    except Exception as e:
        logger.error("[MAXIA] Chainlink init error: %s", e)

    logger.info("[MAXIA] V12 demarre — Art.1-15 + 10 new features + Health monitor + DB backup | 14 chains: Solana + Base + Ethereum + XRP + Polygon + Arbitrum + Avalanche + BNB + TON + SUI + TRON + NEAR + Aptos + SEI")
    logger.info("[MAXIA] DB: %s | Redis: %s", 'PostgreSQL' if os.getenv('DATABASE_URL', '').startswith('postgres') else 'SQLite', 'connected' if redis_client.is_connected else 'in-memory fallback')
    logger.info("[MAXIA] CORS: %s", _ALLOWED_ORIGINS)
    yield

    # ── Graceful shutdown ──
    logger.info("[MAXIA] Shutting down gracefully...")
    # AgentOps — fermer toutes les sessions
    shutdown_agentops()
    # Flush audit log
    try:
        _flush_audit()
    except Exception:
        pass
    # CEO memory + task queue — REMOVED (Plan CEO V4: CEO = local only)
    # Cancel all background tasks
    for t in [t1, t4]:
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
    # WS event stream periodic tasks
    try:
        from features.ws_events import stop_periodic_events
        stop_periodic_events()
    except Exception:
        pass
    # CEO VPS removed — no scheduler/scout to stop
    # Close connections
    try:
        from trading.price_oracle import close_http_pool
        await close_http_pool()
    except Exception:
        pass
    # Close shared HTTP client
    try:
        from core.http_client import close_http_client
        await close_http_client()
    except Exception:
        pass
    await db.disconnect()
    await redis_client.close()
    logger.info("[MAXIA] Shutdown complete")


# ── App ──

_is_sandbox = os.getenv("SANDBOX_MODE", "false").lower() == "true"
app = FastAPI(
    title="MAXIA API V12",
    version="12.0.0",
    lifespan=lifespan,
    docs_url=None,   # Protected endpoint below (S41)
    redoc_url=None,
    openapi_url="/openapi.json",  # Needed for protected /docs
)


# ── M2: Limite globale taille requete (5 MB max) — protection contre upload abusif ──
from starlette.responses import JSONResponse as _JSONResponseGlobal


@app.middleware("http")
async def limit_body_size(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > 5_000_000:
        return _JSONResponseGlobal(status_code=413, content={"error": "Request too large (max 5MB)"})
    return await call_next(request)


@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    """S37: Inject request_id into every response header for tracing."""
    req_id = request.headers.get("X-Request-ID", uuid.uuid4().hex[:8])
    request.state.request_id = req_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = req_id
    return response


# ── H1: Global exception handler — ne jamais exposer str(e) aux clients ──

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Intercepte les exceptions non gerees et retourne un message generique.
    Le request_id permet de retrouver l'erreur dans les logs serveur."""
    import traceback
    req_id = str(uuid.uuid4())[:8]
    logger.error("[ERROR] %s: %s: %s", req_id, type(exc).__name__, exc)
    traceback.print_exc()
    # S37: Alert Telegram + Discord on 500 errors
    try:
        from infra.alerts import _send_private, _send_discord
        path = str(request.url.path)[:100]
        err_type = type(exc).__name__
        await _send_private(
            f"\U0001f534 <b>500 Error</b>\n"
            f"Path: <code>{path}</code>\n"
            f"Error: <code>{err_type}</code>\n"
            f"ID: <code>{req_id}</code>"
        )
        await _send_discord(
            f"500 Error — {err_type}",
            f"**Path:** `{path}`\n**Error:** `{err_type}`\n**ID:** `{req_id}`",
            color=0xFF0000,
        )
    except Exception:
        pass  # Never let alerting break the error handler
    return _JSONResponseGlobal(
        status_code=500,
        content={"error": "Internal server error", "request_id": req_id},
    )


@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    """Serve custom 404 page for browser requests, JSON for API calls."""
    accept = request.headers.get("accept", "")
    if "text/html" in accept and not request.url.path.startswith("/api/"):
        from fastapi.responses import FileResponse
        from pathlib import Path
        _404_path = Path(__file__).parent.parent / "frontend" / "404.html"
        if _404_path.exists():
            return FileResponse(str(_404_path), status_code=404)
    return _JSONResponseGlobal(status_code=404, content={"error": "Not found"})


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
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://unpkg.com https://s3.tradingview.com; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: https:; "
        "connect-src 'self' wss: ws: https:; "
        "frame-src 'self' https://s.tradingview.com https://s3.tradingview.com; "
        "frame-ancestors 'none'"
    )
    return response


# ── HTTPS + www→non-www redirect en production ──
@app.middleware("http")
async def https_redirect_middleware(request, call_next):
    """Redirige HTTP→HTTPS et www→non-www en production (un seul 301)."""
    if os.getenv("FORCE_HTTPS", "false").lower() == "true":
        proto = request.headers.get("x-forwarded-proto", "https")
        host = request.headers.get("host", "")
        needs_https = (proto == "http")
        needs_no_www = host.startswith("www.")
        if needs_https or needs_no_www:
            from starlette.responses import RedirectResponse
            url = str(request.url)
            if needs_https:
                url = url.replace("http://", "https://", 1)
            if needs_no_www:
                url = url.replace("://www.", "://", 1)
            return RedirectResponse(url, status_code=301)
    return await call_next(request)

# ── Rate Limit + Burst Protection Middleware ──
@app.middleware("http")
async def rate_limit_headers_middleware(request, call_next):
    from core.security import check_rate_limit_smart, get_rate_limit_info, check_burst_limit, get_burst_ban_remaining, check_ip_rate_limit, RATE_LIMIT_WHITELIST, get_real_ip
    ip = get_real_ip(request)

    # Whitelist — fondateur, VPS, CEO local : skip ALL rate limits
    if ip in RATE_LIMIT_WHITELIST:
        return await call_next(request)

    # IP rate limiting — 100 req/min per IP (before other checks)
    if check_ip_rate_limit(ip):
        from starlette.responses import JSONResponse as _JSONRespIP
        return _JSONRespIP(
            status_code=429,
            content={"error": "IP rate limit exceeded (100 req/min). Slow down.", "retry_after": 60},
            headers={"Retry-After": "60"},
        )

    # Burst protection — bloque les DDoS (>20 req/2s)
    if not check_burst_limit(ip):
        ban_remaining = get_burst_ban_remaining(ip)
        from starlette.responses import JSONResponse
        return JSONResponse(
            status_code=429,
            content={"error": "Too many requests. Slow down.", "retry_after": ban_remaining},
            headers={"Retry-After": str(ban_remaining)},
        )

    # Redis rate limit (daily quotas) — async, uses Redis when available
    try:
        from core.security import check_rate_limit_async
        await check_rate_limit_async(request)
    except HTTPException as e:
        from starlette.responses import JSONResponse as _JSONRespRedis
        return _JSONRespRedis(
            status_code=e.status_code,
            content={"error": e.detail, "retry_after": 60},
            headers={"Retry-After": "60"},
        )
    except Exception:
        pass

    # In-memory rate limit (smart, per-endpoint) — fallback/complement
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
from features.analytics import router as analytics_router
app.include_router(analytics_router)

# V12: New features routers
try:
    from trading.trading_features import get_router as get_trading_router
    app.include_router(get_trading_router())
except Exception as e:
    logger.error("[MAXIA] Trading router error: %s", e)
try:
    from marketplace.marketplace_features import get_router as get_mkt_router
    app.include_router(get_mkt_router())
except Exception as e:
    logger.error("[MAXIA] Marketplace router error: %s", e)
try:
    from features.infra_features import get_router as get_infra_router
    app.include_router(get_infra_router())
except Exception as e:
    logger.error("[MAXIA] Infra router error: %s", e)
try:
    from integrations.email_service import router as email_router
    app.include_router(email_router)
    logger.info("[Email] Service ceo@maxiaworld.app monte")
except Exception as e:
    logger.error("[MAXIA] Email router error: %s", e)
try:
    from trading.yield_aggregator import router as yield_router
    app.include_router(yield_router)
    logger.info("[Yield] Aggregator DeFi monte")
except Exception as e:
    logger.error("[MAXIA] Yield router error: %s", e)
try:
    from features.rpc_service import router as rpc_router
    app.include_router(rpc_router)
    logger.info("[RPC] RPC-as-a-Service 14 chains monte")
except Exception as e:
    logger.error("[MAXIA] RPC router error: %s", e)
try:
    from features.oracle_service import router as oracle_router
    app.include_router(oracle_router)
    logger.info("[Oracle] Oracle + Data Marketplace monte")
except Exception as e:
    logger.error("[MAXIA] Oracle router error: %s", e)
try:
    from features.bridge_service import router as bridge_router
    app.include_router(bridge_router)
    logger.info("[Bridge] Cross-chain bridge 14 chains monte")
except Exception as e:
    logger.error("[MAXIA] Bridge router error: %s", e)
try:
    from features.nft_service import router as nft_router
    app.include_router(nft_router)
    logger.info("[NFT] Agent ID + Trust Score + Service Passes monte")
except Exception as e:
    logger.error("[MAXIA] NFT router error: %s", e)
try:
    from billing.subscription_service import router as sub_router
    app.include_router(sub_router)
    logger.info("[Subscriptions] Streaming payments USDC monte")
except Exception as e:
    logger.error("[MAXIA] Subscription router error: %s", e)
try:
    from trading.trading_tools import router as trading_router
    app.include_router(trading_router)
    logger.info("[Trading] Whale tracker, candles, signals, portfolio, alerts monte")
except Exception as e:
    logger.error("[MAXIA] Trading router error: %s", e)

# V12: Fine-tuning LLM as a Service (Unsloth + RunPod)
try:
    from gpu.finetune_service import router as finetune_router
    app.include_router(finetune_router)
    logger.info("[Finetune] LLM Fine-Tuning as a Service (Unsloth) monte")
except Exception as e:
    logger.error("[MAXIA] Finetune router error: %s", e)

# V12: AWP Protocol (Agent Staking on Base)
try:
    from integrations.awp_protocol import router as awp_router
    app.include_router(awp_router)
    logger.info("[AWP] Autonomous Worker Protocol (staking + discovery) monte")
except Exception as e:
    logger.error("[MAXIA] AWP router error: %s", e)

# V12: Protocol Catalog — 50+ DeFi/Web3 protocols across 14 chains
try:
    from integrations.goat_bridge import router as goat_router, router_alias as protocols_router
    app.include_router(goat_router)
    app.include_router(protocols_router)
    logger.info("[Protocols] Protocol Catalog (50+ protocols, 14 chains) monte")
except Exception as e:
    logger.error("[MAXIA] GOAT bridge error: %s", e)

# V12: Solana DeFi (lending/borrowing/staking)
try:
    from trading.solana_defi import router as solana_defi_router
    app.include_router(solana_defi_router)
    logger.info("[DeFi] Solana DeFi (lending/borrowing/staking/LP) monte")
except Exception as e:
    logger.error("[MAXIA] Solana DeFi error: %s", e)

# V12: LLM-as-a-Service (OpenAI-compatible, multi-provider)
try:
    from ai.llm_service import router as llm_svc_router
    app.include_router(llm_svc_router)
    logger.info("[LLM] LLM-as-a-Service (OpenAI-compatible) monte")
except Exception as e:
    logger.error("[MAXIA] LLM service router error: %s", e)

# V12: A2A Protocol (Google/Linux Foundation — Agent2Agent)
try:
    from marketplace.a2a_protocol import router as a2a_router
    app.include_router(a2a_router)
    logger.info("[A2A] Agent2Agent Protocol (JSON-RPC 2.0 + SSE) monte")
except Exception as e:
    logger.error("[MAXIA] A2A router error: %s", e)

# V12: Agentverse Bridge (Fetch.ai ecosystem registration + health)
try:
    from agents.agentverse_bridge import router as agentverse_router
    app.include_router(agentverse_router)
    logger.info("[AGENTVERSE] Fetch.ai Agentverse bridge monte")
except Exception as e:
    logger.error("[MAXIA] Agentverse bridge router error: %s", e)

# V13: Proof of Delivery + Dispute Resolution (Art.47)
try:
    from features.proof_of_delivery import router as pod_router
    app.include_router(pod_router)
    logger.info("[PoD] Proof of Delivery + Dispute Resolution monte")
except Exception as e:
    logger.error("[MAXIA] PoD router error: %s", e)

# V13: Chain Resilience + Status Page (Art.48)
try:
    from blockchain.chain_resilience import router as resilience_router
    app.include_router(resilience_router)
    logger.info("[Resilience] Circuit Breaker + Status Page monte")
except Exception as e:
    logger.error("[MAXIA] Resilience router error: %s", e)

# V13: Agent Leaderboard (Art.49)
try:
    from agents.agent_leaderboard import router as leaderboard_router
    app.include_router(leaderboard_router)
    logger.info("[Leaderboard] Agent Scoring + Grades monte")
except Exception as e:
    logger.error("[MAXIA] Leaderboard router error: %s", e)

# V13: SLA Enforcer (Art.50)
try:
    from enterprise.sla_enforcer import router as sla_router
    app.include_router(sla_router)
    logger.info("[SLA] Enforcer + Circuit Breaker monte")
except Exception as e:
    logger.error("[MAXIA] SLA router error: %s", e)

# V13: Pyth Oracle (Art.51)
try:
    from trading.pyth_oracle import router as pyth_router
    app.include_router(pyth_router)
    logger.info("[Pyth] Real-time Oracle (stocks + crypto) monte")
except Exception as e:
    logger.error("[MAXIA] Pyth router error: %s", e)

# Chat conversationnel (P2)
try:
    from features.chat_handler import router as chat_router
    app.include_router(chat_router)
    logger.info("[Chat] Conversational trading chat monte")
except Exception as e:
    logger.error("[MAXIA] Chat router error: %s", e)

# Gamification (P3)
try:
    from features.gamification import router as gamification_router
    app.include_router(gamification_router)
    logger.info("[Gamification] Points + badges + leaderboard monte")
except Exception as e:
    logger.error("[MAXIA] Gamification router error: %s", e)

# Jupiter Perps (P5)
try:
    from trading.perps_client import router as perps_router
    app.include_router(perps_router)
    logger.info("[Perps] Jupiter Perpetuals (read-only) monte")
except Exception as e:
    logger.error("[MAXIA] Perps router error: %s", e)

# Token Launcher — Pump.fun (P6)
try:
    from features.token_launcher import router as token_router
    app.include_router(token_router)
    logger.info("[TokenLaunch] Pump.fun token launcher monte")
except Exception as e:
    logger.error("[MAXIA] Token launcher router error: %s", e)

# V13+: Activity Feed (Art.53)
try:
    from features.activity_feed import router as feed_router
    app.include_router(feed_router)
    logger.info("[Feed] Activity Feed (SSE + REST) monte")
except Exception as e:
    logger.error("[MAXIA] Feed router error: %s", e)

# V13+: Referral + Badges (Art.54)
try:
    from billing.referral import router as referral_router, badges_router
    app.include_router(referral_router)
    app.include_router(badges_router)
    logger.info("[Referral] Referral + Badges monte")
except Exception as e:
    logger.error("[MAXIA] Referral router error: %s", e)

# V13+: EVM Multi-Chain Swap — 6 chains via 0x (Art.55)
try:
    from trading.evm_swap import router as evm_swap_router
    app.include_router(evm_swap_router)
    logger.info("[EVM-Swap] Multi-chain swap (6 chains, 36 tokens, 0x) monte")
except Exception as e:
    logger.error("[MAXIA] EVM swap error: %s", e)

# V13+: Business Listings — AI Business Marketplace (Art.56)
try:
    from features.business_listing import router as business_router
    app.include_router(business_router)
    logger.info("[Business] AI Business Marketplace (Flippt-style) monte")
except Exception as e:
    logger.error("[MAXIA] Business listing error: %s", e)

# V13: Reverse Auctions (Art.52)
try:
    from marketplace.reverse_auction import router as auction_router
    app.include_router(auction_router)
    logger.info("[Auction] Reverse Auctions (RFQ) monte")
except Exception as e:
    logger.error("[MAXIA] Auction router error: %s", e)

# ═══ Enterprise Suite (6 modules) ═══
try:
    from enterprise.enterprise_billing import router as billing_router
    app.include_router(billing_router)
    logger.info("[Enterprise] Billing (usage-based metering + invoices) monte")
except Exception as e:
    logger.error("[MAXIA] Billing router error: %s", e)

try:
    from enterprise.stripe_billing import router as stripe_router
    app.include_router(stripe_router)
    logger.info("[Enterprise] Stripe Billing (checkout + webhooks + portal) monte")
except Exception as e:
    logger.error("[MAXIA] Stripe billing router error: %s", e)

try:
    from enterprise.enterprise_sso import router as sso_router
    app.include_router(sso_router)
    logger.info("[Enterprise] SSO (OIDC/Google/Microsoft) monte")
except Exception as e:
    logger.error("[MAXIA] SSO router error: %s", e)

try:
    from enterprise.enterprise_metrics import router as metrics_router, metrics_middleware
    app.include_router(metrics_router)
    app.middleware("http")(metrics_middleware)
    logger.info("[Enterprise] Metrics (Prometheus /metrics + SLA) monte")
except Exception as e:
    logger.error("[MAXIA] Metrics router error: %s", e)

# Enterprise: tenant context middleware — set tenant_id from X-Tenant or API key
@app.middleware("http")
async def tenant_middleware(request, call_next):
    """Set tenant context from X-Tenant header or API key lookup."""
    try:
        from enterprise.tenant_isolation import TenantContext
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
    from enterprise.audit_trail import router as audit_router
    app.include_router(audit_router)
    logger.info("[Enterprise] Audit Trail (compliance + policies) monte")
except Exception as e:
    logger.error("[MAXIA] Audit router error: %s", e)

try:
    from enterprise.tenant_isolation import router as tenant_router
    app.include_router(tenant_router)
    logger.info("[Enterprise] Tenant Isolation (multi-tenant + plans) monte")
except Exception as e:
    logger.error("[MAXIA] Tenant router error: %s", e)

try:
    from enterprise.enterprise_dashboard import router as dashboard_router
    app.include_router(dashboard_router)
    logger.info("[Enterprise] Dashboard (fleet analytics + SLA + revenue) monte")
except Exception as e:
    logger.error("[MAXIA] Dashboard router error: %s", e)

try:
    from core.redis_rate_limiter import router as rate_limit_router
    app.include_router(rate_limit_router)
    logger.info("[RateLimit] Redis Rate Limiter monte")
except Exception as e:
    logger.error("[MAXIA] Rate limiter router error: %s", e)

try:
    from agents.agent_analytics import router as agent_analytics_router
    app.include_router(agent_analytics_router)
    logger.info("[Analytics] Agent Analytics monte")
except Exception as e:
    logger.error("[MAXIA] Agent Analytics router error: %s", e)

try:
    from agents.agent_credit import router as agent_credit_router
    app.include_router(agent_credit_router)
    logger.info("[Credit] Agent Credit System monte")
except Exception as e:
    logger.error("[MAXIA] Agent Credit router error: %s", e)
try:
    from billing.prepaid_credits import router as prepaid_router
    app.include_router(prepaid_router)
    logger.info("[Credits] Prepaid Credits System monte")
except Exception as e:
    logger.error("[MAXIA] Prepaid Credits router error: %s", e)

# V13+: Streaming Payments — pay-per-second (Art.57)
try:
    from features.streaming_payments import router as stream_router
    app.include_router(stream_router)
    logger.info("[StreamPay] Streaming Payments (pay-per-second) monte")
except Exception as e:
    logger.error("[MAXIA] StreamPay router error: %s", e)
try:
    from blockchain.lightning_api import router as lightning_router
    app.include_router(lightning_router)
    logger.info("[Lightning] Bitcoin Lightning API monte")
except Exception as e:
    logger.error("[MAXIA] Lightning router error: %s", e)

# V13+: Agent Subcontracting — delegation automatique (Art.58)
try:
    from agents.agent_subcontract import router as subcontract_router
    app.include_router(subcontract_router)
    logger.info("[Subcontract] Agent Subcontracting (delegation) monte")
except Exception as e:
    logger.error("[MAXIA] Subcontract router error: %s", e)

# V13+: Composable Agent Builder — assemble agents from components no-code (Art.59)
try:
    from agents.agent_builder import router as agent_builder_router
    app.include_router(agent_builder_router)
    logger.info("[AgentBuilder] Composable Agent Builder monte")
except Exception as e:
    logger.error("[MAXIA] Agent Builder router error: %s", e)

FRONTEND_INDEX = Path(__file__).parent.parent / "frontend" / "index.html"
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

# Servir les fichiers statiques du dossier frontend (PDF, images, etc.)
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


# ═══════════════════════════════════════════════════════════
#  HTML PAGE ROUTES (pages.py)
# ═══════════════════════════════════════════════════════════
from routes.pages import router as pages_router
app.include_router(pages_router)
logger.info("[Pages] 16 HTML page routes monte")

# ── V12: Extracted inline routes into separate router files ──
from routes.admin_routes import router as admin_inline_router, _verify_admin, ADMIN_KEY
app.include_router(admin_inline_router)
logger.info("[Admin] Admin + agent permissions routes monte")

from features.staking_routes import router as staking_inline_router
app.include_router(staking_inline_router)
logger.info("[Staking] Staking + credit score + alerts routes monte")

from routes.exchange_routes import router as exchange_inline_router
app.include_router(exchange_inline_router)
logger.info("[Exchange] Exchange + stocks + bridge + pricing routes monte")

from enterprise.enterprise_routes import router as enterprise_inline_router
app.include_router(enterprise_inline_router)
logger.info("[Enterprise] Enterprise + analytics + events routes monte")

from marketplace.marketplace_inline import router as marketplace_inline_router
app.include_router(marketplace_inline_router)
logger.info("[Marketplace] Marketplace inline routes monte")

from integrations.kite_ap2_routes import router as kite_ap2_inline_router
app.include_router(kite_ap2_inline_router)
logger.info("[KiteAP2] Kite AI + AP2 routes monte")

from routes.pages_routes import router as pages_inline_router
app.include_router(pages_inline_router)
logger.info("[PagesInline] Pages + health + docs + versioning routes monte")

# ── App Store API endpoints ──  (REMOVED — moved to marketplace_inline.py)
# ── Forum inline POST ──  (REMOVED — moved to marketplace_inline.py)
# ── Creator Marketplace ──  (REMOVED — moved to marketplace_inline.py)
# ── Favicon, robots, sitemap, llms.txt ──  (REMOVED — moved to pages_routes.py)
# ── Admin panel, login, dashboard ──  (REMOVED — moved to admin_routes.py)
# ── Agent trust ──  (REMOVED — moved to pages_routes.py)
# ── Agent Card (.well-known/agent.json) ──  (REMOVED — moved to pages_routes.py)
# ── Docs HTML, Pricing page ──  (REMOVED — moved to pages_routes.py)
# ── Health, public status ──  (REMOVED — moved to pages_routes.py)
# ── SSE events stream ──  (REMOVED — moved to enterprise_routes.py)
# ── Swagger/ReDoc protected ──  (REMOVED — moved to pages_routes.py)
# ── API Versioning ──  (REMOVED — moved to pages_routes.py)
# ── API Stats/Activity ──  (REMOVED — moved to admin_routes.py)
# ── CEO API routes — kept as-is (already external) ──

# CEO API routes — REMOVED (Plan CEO V4: CEO = local only, fichiers supprimes)

# ── AI Forum (forum_api.py) ──
from routes.forum_api import router as forum_api_router
app.include_router(forum_api_router)
logger.info("[Forum] Forum API routes monte")

# ── Agent Profiles ──
try:
    from agents.agent_profile import router as profile_router
    app.include_router(profile_router)
    logger.info("[Profile] Agent profile routes monte")
except Exception as e:
    logger.error("[MAXIA] Profile router error: %s", e)

# ── Blog / Knowledge Base ──
try:
    from routes.blog import router as blog_router
    app.include_router(blog_router)
    logger.info("[Blog] Blog API routes monte")
except Exception as e:
    logger.error("[MAXIA] Blog router error: %s", e)

# ── Newsletter ──
try:
    from integrations.newsletter import router as newsletter_router
    app.include_router(newsletter_router)
    logger.info("[Newsletter] Newsletter routes monte")
except Exception as e:
    logger.error("[MAXIA] Newsletter router error: %s", e)

# ── Governance Lite ──
try:
    from features.governance import router as governance_router
    app.include_router(governance_router)
    logger.info("[Governance] Governance routes monte")
except Exception as e:
    logger.error("[MAXIA] Governance router error: %s", e)

# ── Agent-to-Agent Messaging ──
try:
    from features.agent_messaging import router as messaging_router
    app.include_router(messaging_router)
    logger.info("[Messaging] Agent messaging routes monte (6 endpoints)")
except Exception as e:
    logger.error("[MAXIA] Messaging router error: %s", e)

# ── Webhooks (push notifications for agents) ──
try:
    from features.webhooks import router as webhooks_router
    app.include_router(webhooks_router)
    logger.info("[Webhooks] Webhook routes monte (4 endpoints)")
except Exception as e:
    logger.error("[MAXIA] Webhooks router error: %s", e)



# ═══════════════════════════════════════════════════════════
#  WEBSOCKET — extracted to ws_handlers.py (S33)
# ═══════════════════════════════════════════════════════════
app.include_router(ws_router)
logger.info("[WS] 5 WebSocket endpoints monte (ws_handlers.py)")

# ── WS Event Stream (real-time marketplace events for agents) ──
try:
    from features.ws_events import ws_events_endpoint
    app.websocket("/ws/events")(ws_events_endpoint)
    logger.info("[WS/events] Event stream endpoint monte (/ws/events)")
except Exception as e:
    logger.error("[MAXIA] WS events endpoint error: %s", e)


# ═══════════════════════════════════════════════════════════
#  GPU AUCTIONS + RENTAL (Art.5) — extracted to gpu_api.py
# ═══════════════════════════════════════════════════════════
try:
    from gpu.gpu_api import router as gpu_api_router
    app.include_router(gpu_api_router)
    logger.info("[GPU-API] Routes montees")
except Exception as e:
    logger.error("[MAXIA] GPU API router error: %s", e)


# ═══════════════════════════════════════════════════════════
#  EVM CHAIN VERIFY — extracted to chain_verify_api.py (S33)
# ═══════════════════════════════════════════════════════════
try:
    from routes.chain_verify_api import router as chain_verify_router
    app.include_router(chain_verify_router)
except Exception as e:
    logger.warning("chain_verify_api not loaded: %s", e)



# V11: CLONE SWARM — REMOVED (CEO VPS supprime S20)


# ══════════════════════════════════════════════════════════
#  V11: ESCROW ON-CHAIN (Art.21) — extracted to escrow_api.py
# ══════════════════════════════════════════════════════════
try:
    from routes.escrow_api import router as escrow_api_router
    app.include_router(escrow_api_router)
    logger.info("[ESCROW-API] Routes montees")
except Exception as e:
    logger.error("[MAXIA] Escrow API router error: %s", e)




# ── Chain verification routes (extracted to chain_api.py) ──
try:
    from routes.chain_api import router as chain_api_router
    app.include_router(chain_api_router)
    logger.info("[CHAIN-API] Routes montees")
except Exception as e:
    logger.error("[MAXIA] Chain API router error: %s", e)

# Empire V2 Sprint 1 — Auto-Discovery, Passport V2, Starter Templates
try:
    from marketplace.empire_v2 import router as empire_router
    app.include_router(empire_router)
    logger.info("[Empire] Sprint 1 (OpenAPI, Passport V2, Starter Templates) monte")
except Exception as e:
    logger.error("[MAXIA] Empire V2 router error: %s", e)

# Empire V2 Sprint 2 — Reviews, Categories, Pioneer 100
try:
    from marketplace.empire_sprint2 import router as sprint2_router
    app.include_router(sprint2_router)
    logger.info("[Empire] Sprint 2 (Reviews, Categories, Pioneer 100) monte")
except Exception as e:
    logger.error("[MAXIA] Empire Sprint 2 router error: %s", e)

# Empire V2 Sprint 3 — Kill Switch, Proof of Quality, Pipelines
try:
    from marketplace.empire_sprint3 import router as sprint3_router
    app.include_router(sprint3_router)
    logger.info("[Empire] Sprint 3 (Kill Switch, Proofs, Pipelines) monte")
except Exception as e:
    logger.error("[MAXIA] Empire Sprint 3 router error: %s", e)
