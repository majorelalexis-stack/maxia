"""MAXIA Phase P2 — Pre-built Agent Templates (No-Code Launch)

6 ready-to-use agent presets. Users pick a template, customize config,
and launch an agent in 3 clicks. Uses agent_builder under the hood.

Tables: preset_templates (static catalog), preset_launches (user launches).
"""
import logging
import json
import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel, Field

from core.error_utils import safe_error

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/presets", tags=["presets"])

_SCHEMA = """
CREATE TABLE IF NOT EXISTS preset_launches (
    id TEXT PRIMARY KEY,
    preset_id TEXT NOT NULL,
    owner_wallet TEXT NOT NULL,
    custom_config TEXT NOT NULL DEFAULT '{}',
    agent_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at INTEGER DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_pl_owner ON preset_launches(owner_wallet, status);
CREATE INDEX IF NOT EXISTS idx_pl_preset ON preset_launches(preset_id);
"""

_schema_ready = False


async def _ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    from core.database import db
    await db.raw_executescript(_SCHEMA)
    _schema_ready = True
    logger.info("[Presets] Schema pret")


# ── Preset Catalog (static, no DB) ──

PRESET_CATALOG: list[dict[str, Any]] = [
    {
        "id": "trading-bot",
        "name": "Trading Bot",
        "category": "trading",
        "description": "Monitors token prices and executes swaps when thresholds are reached. Configurable tokens, direction, and amounts.",
        "icon": "chart_with_upwards_trend",
        "services_used": ["price_oracle", "crypto_swap", "alerts"],
        "default_config": {
            "tokens": ["SOL", "ETH", "BTC"],
            "check_interval_min": 5,
            "alert_threshold_pct": 5.0,
            "auto_swap": False,
            "max_swap_usd": 50.0,
        },
        "config_schema": {
            "tokens": {"type": "array", "description": "Token symbols to monitor", "required": True},
            "check_interval_min": {"type": "number", "description": "Check interval in minutes", "required": True},
            "alert_threshold_pct": {"type": "number", "description": "Alert when price moves X%", "required": True},
            "auto_swap": {"type": "boolean", "description": "Enable automatic swaps", "required": False},
            "max_swap_usd": {"type": "number", "description": "Max swap amount in USD", "required": False},
        },
        "popularity": 0,
    },
    {
        "id": "sentiment-analyzer",
        "name": "Sentiment Analyzer",
        "category": "analysis",
        "description": "Scans social media for keywords and delivers sentiment reports. Tracks bullish/bearish signals across Twitter and Reddit.",
        "icon": "bar_chart",
        "services_used": ["sentiment_analyzer", "llm_service"],
        "default_config": {
            "keywords": ["solana", "ethereum", "bitcoin"],
            "sources": ["twitter", "reddit"],
            "report_frequency": "daily",
            "min_confidence": 0.6,
        },
        "config_schema": {
            "keywords": {"type": "array", "description": "Keywords to track", "required": True},
            "sources": {"type": "array", "description": "Data sources", "required": True},
            "report_frequency": {"type": "string", "description": "daily, hourly, or weekly", "required": True},
            "min_confidence": {"type": "number", "description": "Minimum confidence threshold (0-1)", "required": False},
        },
        "popularity": 0,
    },
    {
        "id": "content-creator",
        "name": "Content Creator",
        "category": "content",
        "description": "Generates social media posts and images on a schedule. Uses AI for text and Pollinations.ai for images.",
        "icon": "art",
        "services_used": ["image_gen", "llm_service"],
        "default_config": {
            "topic": "crypto market analysis",
            "style": "professional",
            "post_frequency": "daily",
            "include_images": True,
        },
        "config_schema": {
            "topic": {"type": "string", "description": "Content topic", "required": True},
            "style": {"type": "string", "description": "Tone: professional, casual, technical", "required": True},
            "post_frequency": {"type": "string", "description": "daily, twice_daily, weekly", "required": True},
            "include_images": {"type": "boolean", "description": "Generate images with posts", "required": False},
        },
        "popularity": 0,
    },
    {
        "id": "defi-yield-hunter",
        "name": "DeFi Yield Hunter",
        "category": "defi",
        "description": "Scans DeFi pools for high-yield opportunities. Monitors lending rates, LP yields, and staking APYs across Solana protocols.",
        "icon": "gem",
        "services_used": ["defi_scanner", "solana_defi"],
        "default_config": {
            "min_apy_pct": 5.0,
            "protocols": ["kamino", "marinade", "orca"],
            "scan_interval_min": 30,
            "max_tvl_usd": 0,
        },
        "config_schema": {
            "min_apy_pct": {"type": "number", "description": "Minimum APY to alert (%)", "required": True},
            "protocols": {"type": "array", "description": "Protocols to scan", "required": True},
            "scan_interval_min": {"type": "number", "description": "Scan interval in minutes", "required": True},
            "max_tvl_usd": {"type": "number", "description": "Max TVL filter (0=no limit)", "required": False},
        },
        "popularity": 0,
    },
    {
        "id": "data-researcher",
        "name": "Data Researcher",
        "category": "research",
        "description": "Scrapes specified sources and produces structured AI-summarized reports. Great for competitive analysis and market research.",
        "icon": "mag",
        "services_used": ["web_scraper", "llm_service"],
        "default_config": {
            "urls": [],
            "report_type": "summary",
            "schedule": "daily",
            "max_pages": 5,
        },
        "config_schema": {
            "urls": {"type": "array", "description": "URLs to scrape", "required": True},
            "report_type": {"type": "string", "description": "summary, detailed, or bullet_points", "required": True},
            "schedule": {"type": "string", "description": "daily, weekly, or on_demand", "required": True},
            "max_pages": {"type": "number", "description": "Max pages to scrape per run", "required": False},
        },
        "popularity": 0,
    },
    {
        "id": "portfolio-manager",
        "name": "Portfolio Manager",
        "category": "trading",
        "description": "Tracks wallet holdings, calculates P&L, and sends daily portfolio reports. Supports multi-wallet aggregation.",
        "icon": "briefcase",
        "services_used": ["price_oracle", "wallet_monitor"],
        "default_config": {
            "wallets": [],
            "report_frequency": "daily",
            "alert_loss_pct": 10.0,
            "alert_gain_pct": 20.0,
        },
        "config_schema": {
            "wallets": {"type": "array", "description": "Wallet addresses to track", "required": True},
            "report_frequency": {"type": "string", "description": "daily, weekly, or hourly", "required": True},
            "alert_loss_pct": {"type": "number", "description": "Alert on portfolio loss (%)", "required": False},
            "alert_gain_pct": {"type": "number", "description": "Alert on portfolio gain (%)", "required": False},
        },
        "popularity": 0,
    },
    {
        "id": "token-sniper",
        "name": "Token Sniper Bot",
        "category": "trading",
        "description": "Scans new Solana token launches, filters by age, volume, and buy/sell ratio, then executes Jupiter swaps automatically. Budget-capped for risk control.",
        "icon": "dart",
        "services_used": ["price_oracle", "crypto_swap", "jupiter_router"],
        "default_config": {
            "max_token_age_hours": 6,
            "min_volume_24h_usd": 10000,
            "min_buy_sell_ratio": 1.4,
            "trade_size_sol": 0.01,
            "max_budget_sol": 0.1,
            "max_buys_per_run": 2,
            "scan_interval_min": 15,
        },
        "config_schema": {
            "max_token_age_hours": {"type": "number", "description": "Max token age in hours (default 6)", "required": True},
            "min_volume_24h_usd": {"type": "number", "description": "Min 24h volume in USD", "required": True},
            "min_buy_sell_ratio": {"type": "number", "description": "Min buy/sell ratio (1.4 = organic pressure)", "required": True},
            "trade_size_sol": {"type": "number", "description": "SOL per trade (default 0.01)", "required": True},
            "max_budget_sol": {"type": "number", "description": "Total SOL budget cap (default 0.1)", "required": True},
            "max_buys_per_run": {"type": "number", "description": "Max buys per scan cycle", "required": False},
            "scan_interval_min": {"type": "number", "description": "Scan interval in minutes", "required": False},
        },
        "popularity": 0,
    },
]

PRESET_MAP = {p["id"]: p for p in PRESET_CATALOG}


# ── Endpoints ──

@router.get("/catalog")
async def list_presets():
    """List all available agent presets."""
    return {"presets": PRESET_CATALOG, "count": len(PRESET_CATALOG)}


@router.get("/catalog/{preset_id}")
async def get_preset(preset_id: str):
    """Get details of a specific preset including config schema."""
    preset = PRESET_MAP.get(preset_id)
    if not preset:
        raise HTTPException(404, f"Preset '{preset_id}' not found")
    return preset


@router.get("/launches")
async def list_launches(wallet: str = Query(..., min_length=8)):
    """List agents launched by a wallet."""
    await _ensure_schema()
    from core.database import db
    rows = await db._fetchall(
        "SELECT * FROM preset_launches WHERE owner_wallet = ? ORDER BY created_at DESC LIMIT 50",
        (wallet,),
    )
    return {
        "launches": [
            {
                "id": r["id"],
                "preset_id": r["preset_id"],
                "agent_name": r["agent_name"],
                "custom_config": json.loads(r["custom_config"]) if r["custom_config"] else {},
                "status": r["status"],
                "created_at": r["created_at"],
            }
            for r in rows
        ],
    }


class LaunchRequest(BaseModel):
    preset_id: str = Field(..., min_length=1)
    wallet: str = Field(..., min_length=8)
    agent_name: str = Field(..., min_length=2, max_length=100)
    config: dict[str, Any] = Field(default_factory=dict)


@router.post("/launch")
async def launch_preset(req: LaunchRequest):
    """Launch an agent from a preset template."""
    await _ensure_schema()

    preset = PRESET_MAP.get(req.preset_id)
    if not preset:
        raise HTTPException(404, f"Preset '{req.preset_id}' not found")

    # Merge default config with user overrides
    merged_config = {**preset["default_config"], **req.config}

    # Validate required fields
    for field, schema in preset["config_schema"].items():
        if schema.get("required") and field not in merged_config:
            raise HTTPException(400, f"Missing required config field: {field}")

    launch_id = uuid.uuid4().hex[:12]
    from core.database import db
    await db.raw_execute(
        "INSERT INTO preset_launches (id, preset_id, owner_wallet, agent_name, custom_config) VALUES (?, ?, ?, ?, ?)",
        (launch_id, req.preset_id, req.wallet, req.agent_name, json.dumps(merged_config)),
    )

    logger.info("[Presets] Agent '%s' launched from preset '%s' by %s", req.agent_name, req.preset_id, req.wallet[:16])

    return {
        "id": launch_id,
        "preset_id": req.preset_id,
        "agent_name": req.agent_name,
        "config": merged_config,
        "status": "active",
        "message": f"Agent '{req.agent_name}' launched from template '{preset['name']}'",
    }


@router.post("/launches/{launch_id}/stop")
async def stop_launch(launch_id: str):
    """Stop a launched agent."""
    await _ensure_schema()
    from core.database import db
    row = await db._fetchone("SELECT * FROM preset_launches WHERE id = ?", (launch_id,))
    if not row:
        raise HTTPException(404, "Launch not found")
    if row["status"] == "stopped":
        raise HTTPException(400, "Already stopped")
    await db.raw_execute("UPDATE preset_launches SET status = 'stopped' WHERE id = ?", (launch_id,))
    return {"id": launch_id, "status": "stopped"}


@router.get("/preview/{preset_id}")
async def preview_preset(preset_id: str):
    """Preview what an agent from this preset will do (dry run)."""
    preset = PRESET_MAP.get(preset_id)
    if not preset:
        raise HTTPException(404, f"Preset '{preset_id}' not found")
    return {
        "preset": preset["name"],
        "description": preset["description"],
        "services": preset["services_used"],
        "default_config": preset["default_config"],
        "actions": [
            f"Connect to {s}" for s in preset["services_used"]
        ] + [
            f"Run every {preset['default_config'].get('check_interval_min', preset['default_config'].get('scan_interval_min', 'N/A'))} minutes"
            if any(k.endswith('_min') for k in preset['default_config']) else
            f"Report {preset['default_config'].get('report_frequency', 'on demand')}"
        ],
    }
