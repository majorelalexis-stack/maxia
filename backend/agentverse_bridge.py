"""MAXIA Agentverse Bridge — Fetch.ai Ecosystem Integration

Provides:
1. Health endpoint for Agentverse pings (/api/agentverse/status)
2. MAXIA services mapped as A2A-discoverable skills for Fetch.ai uAgents
3. Documentation for registering MAXIA on Agentverse

────────────────────────────────────────────────────────────
HOW TO REGISTER MAXIA ON FETCH.AI AGENTVERSE
────────────────────────────────────────────────────────────

Option A: Agentverse Hosted Agent (recommended for discovery)
  1. Go to https://agentverse.ai and create an account
  2. Create a new "Agentverse Agent" (hosted)
  3. In the agent code, use the A2A adapter to forward requests:
     - pip install uagents uagents-a2a
     - The agent fetches MAXIA's Agent Card at https://maxiaworld.app/.well-known/agent.json
     - All JSON-RPC 2.0 calls are proxied to https://maxiaworld.app/a2a
  4. Register the agent in Almanac with these protocols:
     - a2a (Agent-to-Agent, Google/Linux Foundation standard)
     - rest (MAXIA REST API)
  5. The agent will appear in Agentverse search with MAXIA's 17 skills

Option B: Self-hosted uAgent pointing to MAXIA
  1. Run a uAgent on your server that wraps MAXIA's A2A endpoint
  2. The uAgent registers itself in the Almanac contract on Fetch.ai
  3. Other agents discover MAXIA via Almanac semantic search
  4. Code template:
     ```python
     from uagents import Agent, Context
     from uagents_a2a import A2AAdapter, AgentCard
     import httpx

     agent = Agent(name="maxia-bridge", seed="<your-seed>")

     @agent.on_message()
     async def handle(ctx: Context, msg):
         # Proxy to MAXIA A2A endpoint
         async with httpx.AsyncClient() as client:
             resp = await client.post(
                 "https://maxiaworld.app/a2a",
                 json={
                     "jsonrpc": "2.0",
                     "method": "message/send",
                     "params": {"message": {"role": "user", "parts": [{"type": "text", "text": str(msg)}]}},
                     "id": ctx.session,
                 },
             )
             await ctx.send(msg.sender, resp.json())

     agent.run()
     ```

Option C: Direct A2A (no uAgent needed)
  - Fetch.ai's uAgents A2A adapter can discover any A2A-compliant agent
  - MAXIA already serves:
    - GET  /.well-known/agent.json        (Agent Card — legacy)
    - GET  /.well-known/agent-card.json   (Agent Card — A2A spec)
    - POST /a2a                           (JSON-RPC 2.0 endpoint)
  - Any uAgent with the A2A adapter can call MAXIA directly
  - No registration needed — just point the adapter at https://maxiaworld.app

ALMANAC REGISTRATION (for maximum discoverability):
  - Almanac contract on Fetch.ai mainnet stores agent metadata
  - Register via: https://agentverse.ai/v1/hosting/agents
  - Required fields: name, description, protocols, endpoint
  - MAXIA's endpoint: https://maxiaworld.app/a2a

AGENTVERSE SEARCH OPTIMIZATION:
  - Use descriptive tags: "marketplace", "swap", "gpu", "defi", "escrow"
  - Set interaction count high by handling test pings
  - Keep the /api/agentverse/status endpoint alive for uptime monitoring
────────────────────────────────────────────────────────────
"""
import logging
import time
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from error_utils import safe_error

log = logging.getLogger("agentverse")

router = APIRouter(prefix="/api/agentverse", tags=["agentverse"])


# ── MAXIA Services as A2A Skills ──
# Maps each MAXIA service to Agentverse-compatible skill metadata.
# These are used for Almanac registration and search indexing.

MAXIA_SKILLS: list[dict[str, Any]] = [
    {
        "id": "crypto-swap",
        "name": "Crypto Token Swap (7 chains)",
        "description": "Swap between 65 crypto tokens across 7 chains (Solana via Jupiter, 6 EVM via 0x). Live oracle prices, low fees (0.01-0.10%).",
        "tags": ["crypto", "swap", "solana", "evm", "defi", "jupiter", "0x"],
        "endpoint": "/api/public/crypto/swap",
        "method": "POST",
        "input_schema": {"from_token": "str", "to_token": "str", "amount": "float", "chain": "str (optional)", "payment_tx": "str"},
    },
    {
        "id": "gpu-rental",
        "name": "GPU Rental (Akash Network)",
        "description": "Rent GPUs from RTX4090 ($0.76/h) to H200 ($4.74/h). 6 tiers. Pay per hour in USDC. SSH + Jupyter access via Akash Network.",
        "tags": ["gpu", "compute", "akash", "ai-training", "inference"],
        "endpoint": "/api/public/gpu/tiers",
        "method": "GET",
        "input_schema": {},
    },
    {
        "id": "service-marketplace",
        "name": "AI Service Marketplace",
        "description": "Discover, buy, and sell AI services. 17 service types including audit, code, data, image, sentiment, scraper. Pay with USDC.",
        "tags": ["marketplace", "ai-services", "discovery", "usdc"],
        "endpoint": "/api/public/discover",
        "method": "GET",
        "input_schema": {"capability": "str (optional)", "max_price": "float (optional)", "min_rating": "float (optional)"},
    },
    {
        "id": "escrow",
        "name": "On-Chain Escrow (Solana + Base)",
        "description": "Lock USDC in on-chain escrow. Solana Anchor PDA or Base L2 Solidity contract. 48h auto-refund, dispute resolution.",
        "tags": ["escrow", "solana", "base", "usdc", "trust", "smart-contract"],
        "endpoint": "/api/public/escrow/create",
        "method": "POST",
        "input_schema": {"amount": "float", "chain": "str (solana|base)", "seller_address": "str"},
    },
    {
        "id": "stock-trading",
        "name": "Tokenized Stock Trading",
        "description": "Trade 25 tokenized US stocks (AAPL, TSLA, NVDA, GOOGL, etc.) with USDC. Fractional shares from $1. xStocks/Ondo/Dinari.",
        "tags": ["stocks", "trading", "tokenized", "usdc", "equities"],
        "endpoint": "/api/public/stocks",
        "method": "GET",
        "input_schema": {},
    },
    {
        "id": "sentiment-analysis",
        "name": "Crypto Sentiment Analysis",
        "description": "Real-time crypto sentiment: Fear & Greed Index, social signals, whale tracking, technical indicators, trending tokens.",
        "tags": ["sentiment", "analysis", "crypto", "fear-greed", "signals"],
        "endpoint": "/api/public/fear-greed",
        "method": "GET",
        "input_schema": {},
    },
    {
        "id": "wallet-analysis",
        "name": "Wallet Analysis",
        "description": "Analyze any Solana wallet: holdings, transaction history, DeFi positions, risk score.",
        "tags": ["wallet", "analysis", "solana", "portfolio"],
        "endpoint": "/api/public/wallet-analysis",
        "method": "GET",
        "input_schema": {"address": "str (Solana wallet address)"},
    },
    {
        "id": "defi-yields",
        "name": "DeFi Yield Scanner",
        "description": "Find best DeFi yields across 14 chains. Aave, Compound, Marinade, Jito, Lido, and 60+ pools. DeFiLlama data.",
        "tags": ["defi", "yields", "apy", "staking", "lending"],
        "endpoint": "/api/yields/best",
        "method": "GET",
        "input_schema": {"asset": "str (optional, default USDC)", "limit": "int (optional, default 10)"},
    },
    {
        "id": "llm-finetune",
        "name": "LLM Fine-Tuning (Unsloth)",
        "description": "Fine-tune any LLM (Llama, Qwen, Mistral, Gemma, DeepSeek, Phi) on your dataset via Unsloth. GGUF/safetensors/LoRA output.",
        "tags": ["fine-tuning", "llm", "unsloth", "training", "ai"],
        "endpoint": "/api/finetune/models",
        "method": "GET",
        "input_schema": {},
    },
    {
        "id": "image-generation",
        "name": "Image Generation (FLUX.1)",
        "description": "Generate images using FLUX.1 via Pollinations.ai. Up to 2048px. Free tier available.",
        "tags": ["image", "generation", "ai", "flux", "creative"],
        "endpoint": "/api/public/image/generate",
        "method": "POST",
        "input_schema": {"prompt": "str", "width": "int (optional)", "height": "int (optional)"},
    },
    {
        "id": "web-scraper",
        "name": "Web Scraping",
        "description": "Scrape any web page and return structured JSON. $0.05/page.",
        "tags": ["scraper", "data", "web", "extraction"],
        "endpoint": "/api/public/scrape",
        "method": "POST",
        "input_schema": {"url": "str", "format": "str (optional, json|text)"},
    },
    {
        "id": "smart-contract-audit",
        "name": "Smart Contract Audit",
        "description": "AI-powered security audit of Solidity or Rust smart contracts. Vulnerability detection. $9.99/audit.",
        "tags": ["audit", "security", "smart-contract", "solidity", "rust"],
        "endpoint": "/api/public/execute",
        "method": "POST",
        "input_schema": {"service_id": "str", "prompt": "str (contract code)"},
    },
    {
        "id": "code-generation",
        "name": "Code Generation",
        "description": "Generate code in Python, Rust, JavaScript, TypeScript. $3.99/request.",
        "tags": ["code", "generation", "python", "rust", "javascript"],
        "endpoint": "/api/public/execute",
        "method": "POST",
        "input_schema": {"service_id": "str", "prompt": "str"},
    },
    {
        "id": "whale-tracker",
        "name": "Whale Tracker",
        "description": "Monitor wallets for large transfers. Webhook alerts for whale movements.",
        "tags": ["whale", "tracker", "alerts", "monitoring"],
        "endpoint": "/api/public/whale/track",
        "method": "POST",
        "input_schema": {"address": "str", "webhook_url": "str (optional)"},
    },
    {
        "id": "awp-staking",
        "name": "AWP Agent Staking",
        "description": "Stake USDC on Autonomous Worker Protocol (Base L2) for trust score and 3-12% APY rewards.",
        "tags": ["staking", "awp", "base", "rewards", "trust"],
        "endpoint": "/api/awp/info",
        "method": "GET",
        "input_schema": {},
    },
    {
        "id": "mcp-tools",
        "name": "MCP Tool Server (46 tools)",
        "description": "Model Context Protocol server with 46 tools. Any MCP-compatible agent can call MAXIA tools directly.",
        "tags": ["mcp", "tools", "protocol", "interop"],
        "endpoint": "/mcp/manifest",
        "method": "GET",
        "input_schema": {},
    },
    {
        "id": "data-marketplace",
        "name": "Data Marketplace",
        "description": "Buy and sell structured datasets. Crypto market data, blockchain analytics, social signals.",
        "tags": ["data", "marketplace", "datasets", "analytics"],
        "endpoint": "/api/public/data/search",
        "method": "GET",
        "input_schema": {"query": "str (optional)", "category": "str (optional)"},
    },
]


# ── Agentverse-compatible service descriptor ──
# This is the format Agentverse Almanac expects for agent registration

def get_almanac_descriptor() -> dict[str, Any]:
    """Return Agentverse Almanac-compatible agent descriptor for MAXIA."""
    return {
        "name": "MAXIA",
        "description": (
            "AI-to-AI Marketplace on 14 blockchains. Autonomous AI agents discover, "
            "buy, and sell services using USDC. 65 tokens swap on 7 chains, on-chain "
            "escrow (Solana + Base), GPU rental via Akash, 25 tokenized stocks, "
            "46 MCP tools, 17 AI services, DeFi yield scanner."
        ),
        "protocols": ["a2a", "rest", "mcp", "json-rpc"],
        "endpoint": "https://maxiaworld.app/a2a",
        "agent_card": "https://maxiaworld.app/.well-known/agent.json",
        "skills": [
            {"id": s["id"], "name": s["name"], "description": s["description"], "tags": s["tags"]}
            for s in MAXIA_SKILLS
        ],
        "payment": {
            "method": "USDC",
            "chains": ["solana", "base"],
            "mint_solana": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        },
        "registration": {
            "endpoint": "https://maxiaworld.app/api/public/register",
            "method": "POST",
            "cost": "free",
        },
    }


# ── Routes ──

@router.get("/status")
async def agentverse_status() -> JSONResponse:
    """Health endpoint for Agentverse monitoring.

    Agentverse and Almanac can ping this to verify MAXIA is alive.
    Returns service count, uptime indicator, and A2A endpoint.
    """
    try:
        return JSONResponse({
            "status": "operational",
            "agent": "MAXIA",
            "version": "12.1.0",
            "a2a_endpoint": "https://maxiaworld.app/a2a",
            "agent_card": "https://maxiaworld.app/.well-known/agent.json",
            "mcp_manifest": "https://maxiaworld.app/mcp/manifest",
            "skills_count": len(MAXIA_SKILLS),
            "protocols": ["a2a", "rest", "mcp", "json-rpc"],
            "chains": ["solana", "base", "ethereum", "polygon", "arbitrum", "avalanche", "bnb"],
            "payment": "USDC",
            "timestamp": int(time.time()),
        })
    except Exception as e:
        return JSONResponse(safe_error(e, "agentverse_status"), status_code=500)


@router.get("/skills")
async def agentverse_skills() -> JSONResponse:
    """List all MAXIA services as A2A-discoverable skills.

    Returns the full skill catalog with endpoints, schemas, and tags.
    Agentverse agents can use this to understand MAXIA's capabilities.
    """
    try:
        return JSONResponse({
            "agent": "MAXIA",
            "skills_count": len(MAXIA_SKILLS),
            "skills": MAXIA_SKILLS,
        })
    except Exception as e:
        return JSONResponse(safe_error(e, "agentverse_skills"), status_code=500)


@router.get("/almanac-descriptor")
async def agentverse_almanac_descriptor() -> JSONResponse:
    """Return Agentverse Almanac-compatible descriptor for registration.

    Use this payload when registering MAXIA on Agentverse via:
    POST https://agentverse.ai/v1/hosting/agents
    """
    try:
        return JSONResponse(get_almanac_descriptor())
    except Exception as e:
        return JSONResponse(safe_error(e, "agentverse_almanac"), status_code=500)


log.info("[AGENTVERSE] Fetch.ai Agentverse bridge monte — %d skills", len(MAXIA_SKILLS))
