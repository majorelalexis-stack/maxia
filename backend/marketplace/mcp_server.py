"""MAXIA MCP Server — Model Context Protocol

Allows any MCP-compatible client (Claude, Cursor, LangChain, CrewAI)
to discover and use MAXIA services as tools.

MCP Spec: https://modelcontextprotocol.io
This implements the MCP server over HTTP/SSE (Server-Sent Events).

Tools exposed:
  - maxia_discover: Find services by capability
  - maxia_register: Register an AI agent
  - maxia_sell: List a service for sale
  - maxia_execute: Buy and execute a service
  - maxia_swap_quote: Get a crypto swap quote
  - maxia_prices: Get live token prices
  - maxia_marketplace_stats: Get marketplace statistics
  - maxia_gpu_tiers/rent/status: GPU rental via RunPod
  - maxia_stocks_list/price/buy/sell/portfolio/fees: Tokenized stocks trading
  - maxia_yield_best: Best DeFi yields across 14 chains
  - maxia_bridge_quote: Cross-chain bridge quote (Wormhole/LayerZero)
  - maxia_rpc_call: Proxy RPC call to any of 14 chains
  - maxia_oracle_feed: Real-time price oracle feed
  - maxia_datasets: Data marketplace datasets
  - maxia_nft_mint: Mint NFTs
  - maxia_agent_id: On-chain AI agent identity
  - maxia_trust_score: Agent trust score (0-100)
  - maxia_subscribe: Recurring USDC subscriptions
"""
import json, time, asyncio, logging
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from core.error_utils import safe_error

log = logging.getLogger(__name__)

router = APIRouter(prefix="/mcp", tags=["mcp"])

MAXIA_URL = "https://maxiaworld.app"

# ══════════════════════════════════════════
# MCP Tool Definitions
# ══════════════════════════════════════════

MCP_TOOLS = [
    {
        "name": "maxia_discover",
        "description": "Find AI services on MAXIA marketplace by capability, price, or rating. Returns available services from AI agents.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "capability": {"type": "string", "description": "What you're looking for: sentiment, audit, code, data, image, translation, scraper"},
                "max_price": {"type": "number", "description": "Maximum price in USDC (default: 100)"},
            },
            "required": ["capability"],
        },
    },
    {
        "name": "maxia_register",
        "description": "Register a new AI agent on MAXIA marketplace. Free, instant API key. Required before buying or selling.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Agent name"},
                "wallet": {"type": "string", "description": "Solana wallet address for payments"},
            },
            "required": ["name", "wallet"],
        },
    },
    {
        "name": "maxia_sell",
        "description": "List a service for sale on MAXIA marketplace. Other AI agents can discover and buy it.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "api_key": {"type": "string", "description": "Your MAXIA API key from registration"},
                "name": {"type": "string", "description": "Service name"},
                "description": {"type": "string", "description": "What your service does"},
                "price_usdc": {"type": "number", "description": "Price in USDC"},
                "type": {"type": "string", "description": "Service type: data, code, text, media"},
                "endpoint": {"type": "string", "description": "Webhook URL that MAXIA will call when someone buys"},
            },
            "required": ["api_key", "name", "description", "price_usdc"],
        },
    },
    {
        "name": "maxia_execute",
        "description": "Buy and execute a service from the MAXIA marketplace in one call. Returns the result directly.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "api_key": {"type": "string", "description": "Your MAXIA API key"},
                "service_id": {"type": "string", "description": "Service ID from discover results"},
                "prompt": {"type": "string", "description": "Your request/prompt for the service"},
                "payment_tx": {"type": "string", "description": "USDC payment transaction signature (send USDC to treasury first)"},
            },
            "required": ["api_key", "service_id", "prompt", "payment_tx"],
        },
    },
    {
        "name": "maxia_swap_quote",
        "description": "Get a crypto swap quote on Solana. 107 tokens, 5000+ pairs. Returns price and commission.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "from_token": {"type": "string", "description": "Token to sell: SOL, USDC, BTC, ETH, BONK, etc."},
                "to_token": {"type": "string", "description": "Token to buy"},
                "amount": {"type": "number", "description": "Amount to swap"},
            },
            "required": ["from_token", "to_token", "amount"],
        },
    },
    {
        "name": "maxia_prices",
        "description": "Get live cryptocurrency prices. 107 tokens + 25 US stocks. Updated every 30 seconds.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "maxia_sentiment",
        "description": "Get crypto sentiment analysis for any token. Sources: CoinGecko, Reddit, LunarCrush.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "token": {"type": "string", "description": "Token symbol: BTC, ETH, SOL, BONK, etc."},
            },
            "required": ["token"],
        },
    },
    {
        "name": "maxia_token_risk",
        "description": "Analyze rug pull risk for a Solana token. Returns risk score 0-100 and warnings.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Solana token mint address"},
            },
            "required": ["address"],
        },
    },
    {
        "name": "maxia_wallet_analysis",
        "description": "Analyze a Solana wallet — holdings, balance, profile classification.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Solana wallet address"},
            },
            "required": ["address"],
        },
    },
    {
        "name": "maxia_trending",
        "description": "Get trending crypto tokens right now.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "maxia_fear_greed",
        "description": "Get the crypto Fear & Greed Index.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "maxia_defi_yield",
        "description": "Find the best DeFi yields for any asset across all protocols. Data from DeFiLlama.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "asset": {"type": "string", "description": "Asset to find yields for: USDC, ETH, SOL, BTC, etc."},
                "chain": {"type": "string", "description": "Filter by chain: ethereum, solana, arbitrum (optional)"},
            },
            "required": ["asset"],
        },
    },
    {
        "name": "maxia_marketplace_stats",
        "description": "Get MAXIA marketplace statistics: registered agents, services, transactions, volume, commissions.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    # ── GPU Rental Tools ──
    {
        "name": "maxia_gpu_tiers",
        "description": "List all GPU tiers available for rent on MAXIA (RTX 4090, A100, H100, etc.) with live pricing and competitor comparison.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "maxia_gpu_rent",
        "description": "Rent a GPU on MAXIA via RunPod. Returns SSH/API credentials. Payment in USDC on Solana.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "api_key": {"type": "string", "description": "Your MAXIA API key"},
                "gpu_tier": {"type": "string", "description": "GPU tier: rtx4090, a100_80, h100_sxm5, a6000, 4xa100"},
                "hours": {"type": "number", "description": "Rental duration in hours (1-720)"},
                "payment_tx": {"type": "string", "description": "USDC payment transaction signature on Solana"},
            },
            "required": ["api_key", "gpu_tier", "hours", "payment_tx"],
        },
    },
    {
        "name": "maxia_gpu_status",
        "description": "Check the status of a rented GPU pod (running, idle, terminated).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "api_key": {"type": "string", "description": "Your MAXIA API key"},
                "pod_id": {"type": "string", "description": "RunPod pod ID from rent result"},
            },
            "required": ["api_key", "pod_id"],
        },
    },
    # ── Tokenized Stocks Tools ──
    {
        "name": "maxia_stocks_list",
        "description": "List all tokenized stocks (AAPL, TSLA, NVDA, etc.) available on MAXIA with live prices. 30+ stocks via Backed Finance & Ondo.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "maxia_stocks_price",
        "description": "Get real-time price of a tokenized stock on Solana.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Stock symbol: AAPL, TSLA, NVDA, GOOGL, MSFT, AMZN, META, etc."},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "maxia_stocks_buy",
        "description": "Buy tokenized stocks on MAXIA. Fractional shares from 1 USDC. Routes via Jupiter on Solana.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "api_key": {"type": "string", "description": "Your MAXIA API key"},
                "symbol": {"type": "string", "description": "Stock symbol to buy: AAPL, TSLA, NVDA, etc."},
                "amount_usdc": {"type": "number", "description": "Amount in USDC to spend (min 1, max 100000)"},
                "payment_tx": {"type": "string", "description": "USDC payment transaction signature on Solana"},
            },
            "required": ["api_key", "symbol", "amount_usdc", "payment_tx"],
        },
    },
    {
        "name": "maxia_stocks_sell",
        "description": "Sell tokenized stocks from your MAXIA portfolio. Receive USDC on Solana.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "api_key": {"type": "string", "description": "Your MAXIA API key"},
                "symbol": {"type": "string", "description": "Stock symbol to sell"},
                "shares": {"type": "number", "description": "Number of shares to sell"},
            },
            "required": ["api_key", "symbol", "shares"],
        },
    },
    {
        "name": "maxia_stocks_portfolio",
        "description": "View your tokenized stock holdings and total portfolio value.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "api_key": {"type": "string", "description": "Your MAXIA API key"},
            },
            "required": ["api_key"],
        },
    },
    {
        "name": "maxia_stocks_fees",
        "description": "Compare MAXIA tokenized stock trading fees vs competitors (Robinhood, eToro, Binance).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    # ── Hub Web3 tools ──
    {
        "name": "maxia_yield_best",
        "description": "Find the best DeFi yields across 14 chains (Aave, Marinade, Jito, Compound, Ref Finance).",
        "inputSchema": {"type": "object", "properties": {"asset": {"type": "string", "description": "Asset to find yields for (USDC, SOL, ETH)"}, "limit": {"type": "integer", "default": 5}}, "required": ["asset"]},
    },
    {
        "name": "maxia_bridge_quote",
        "description": "Get a cross-chain bridge quote via LI.FI (31 bridges aggregated) between 15 chains. Real-time fees and unsigned tx.",
        "inputSchema": {"type": "object", "properties": {"from_chain": {"type": "string"}, "to_chain": {"type": "string"}, "token": {"type": "string", "default": "USDC"}, "amount": {"type": "number"}}, "required": ["from_chain", "to_chain", "amount"]},
    },
    {
        "name": "maxia_rpc_call",
        "description": "Make an RPC call to any of 14 blockchains via MAXIA proxy.",
        "inputSchema": {"type": "object", "properties": {"chain": {"type": "string"}, "method": {"type": "string"}, "params": {"type": "array"}}, "required": ["chain", "method"]},
    },
    {
        "name": "maxia_oracle_feed",
        "description": "Get the MAXIA oracle price feed — real-time prices with confidence scores.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "maxia_datasets",
        "description": "List available datasets on the MAXIA data marketplace (crypto prices, GPU pricing, stocks, yields, fear/greed).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "maxia_nft_mint",
        "description": "Mint an NFT on MAXIA (data, art, access pass).",
        "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}, "description": {"type": "string"}, "owner_address": {"type": "string"}, "chain": {"type": "string", "default": "solana"}}, "required": ["name", "description", "owner_address"]},
    },
    {
        "name": "maxia_agent_id",
        "description": "Get or create an on-chain identity for an AI agent (trust score, badges, reputation).",
        "inputSchema": {"type": "object", "properties": {"agent_address": {"type": "string"}}, "required": ["agent_address"]},
    },
    {
        "name": "maxia_trust_score",
        "description": "Get the trust score (0-100) of an AI agent based on transaction history, dispute rate, and activity.",
        "inputSchema": {"type": "object", "properties": {"agent_address": {"type": "string"}}, "required": ["agent_address"]},
    },
    {
        "name": "maxia_subscribe",
        "description": "Create a recurring USDC subscription between AI agents.",
        "inputSchema": {"type": "object", "properties": {"subscriber": {"type": "string"}, "provider": {"type": "string"}, "service_id": {"type": "string"}, "amount_usdc": {"type": "number"}, "interval": {"type": "string", "enum": ["daily", "weekly", "monthly"]}}, "required": ["subscriber", "provider", "service_id", "amount_usdc"]},
    },
    # ── Trading tools ──
    {
        "name": "maxia_whales",
        "description": "Track whale movements (large transfers) across 14 chains.",
        "inputSchema": {"type": "object", "properties": {"chain": {"type": "string", "default": "solana", "description": "Chain: solana, base, ethereum, polygon, arbitrum, avalanche, bnb, ton, sui, tron, near, aptos, sei, xrp"}, "min_usd": {"type": "number", "default": 10000}, "limit": {"type": "integer", "default": 10}}},
    },
    {
        "name": "maxia_candles",
        "description": "Get OHLCV candle data for any token (1m, 5m, 15m, 1h, 4h, 1d intervals).",
        "inputSchema": {"type": "object", "properties": {"token": {"type": "string"}, "interval": {"type": "string", "default": "1h"}, "limit": {"type": "integer", "default": 24}}, "required": ["token"]},
    },
    {
        "name": "maxia_signals",
        "description": "Get technical analysis signals for a token (RSI, SMA, MACD, buy/sell signal).",
        "inputSchema": {"type": "object", "properties": {"token": {"type": "string"}}, "required": ["token"]},
    },
    {
        "name": "maxia_portfolio",
        "description": "Track portfolio value across multiple chains for a wallet address.",
        "inputSchema": {"type": "object", "properties": {"address": {"type": "string"}, "chains": {"type": "string", "default": "solana,base,ethereum"}}, "required": ["address"]},
    },
    {
        "name": "maxia_price_alert",
        "description": "Create a price alert for a token (triggers when price goes above/below target).",
        "inputSchema": {"type": "object", "properties": {"token": {"type": "string"}, "condition": {"type": "string", "enum": ["above", "below"]}, "target_price": {"type": "number"}, "wallet": {"type": "string"}}, "required": ["token", "condition", "target_price"]},
    },
    # ── Fine-Tuning, AWP, LLM tools removed from manifest (not yet implemented) ──
    # When ready, re-add: maxia_finetune_models/quote/start/status, maxia_awp_register/stake/discover/rewards, maxia_llm_models/chat
    # ── Protocol Catalog tools (55 DeFi/Web3 protocols) ──
    {
        "name": "maxia_protocol_search",
        "description": "Search 55+ DeFi/Web3 protocols across 15 chains. Filter by chain or type (dex, lending, staking, bridge, yield, nft, derivatives, launchpad, governance).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "chain": {"type": "string", "description": "Filter by chain: solana, ethereum, base, polygon, arbitrum, avalanche, bnb, ton, sui, tron, near, aptos, sei, xrp"},
                "type": {"type": "string", "description": "Filter by type: dex, lending, staking, bridge, yield, nft, derivatives, launchpad, governance"},
            },
        },
    },
    {
        "name": "maxia_protocol_info",
        "description": "Get details about a specific DeFi protocol — URL, chain, type, description, and which MAXIA endpoints to use for execution.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "protocol": {"type": "string", "description": "Protocol ID: jupiter, aave, uniswap, orca, jito, marinade, raydium, drift, gmx, hyperliquid, lido, compound, etc."},
            },
            "required": ["protocol"],
        },
    },
]


# ══════════════════════════════════════════
# MCP Protocol Endpoints
# ══════════════════════════════════════════

@router.get("/")
async def mcp_info():
    """MCP server info."""
    return {
        "name": "maxia",
        "version": "12.0.0",
        "description": "MAXIA AI-to-AI Marketplace on Solana. Discover, buy, and sell AI services.",
        "protocol": "mcp",
        "url": MAXIA_URL,
    }


@router.get("/tools")
async def mcp_list_tools(tier: str = ""):
    """List available MCP tools, optionally filtered by tier."""
    from core.config import get_mcp_tool_tier, MCP_TIER_ORDER
    tools_with_tier = []
    for tool in MCP_TOOLS:
        t = {**tool, "tier": get_mcp_tool_tier(tool["name"])}
        tools_with_tier.append(t)
    if tier and tier in MCP_TIER_ORDER:
        max_level = MCP_TIER_ORDER[tier]
        tools_with_tier = [t for t in tools_with_tier if MCP_TIER_ORDER.get(t["tier"], 0) <= max_level]
    return {"tools": tools_with_tier}


# #10 MCP free tier: read-only tools work without API key (5 calls/min per IP)
_mcp_free_calls: dict = {}  # ip -> [timestamps]
FREE_TOOLS = {"maxia_discover", "maxia_prices", "maxia_trending", "maxia_fear_greed",
              "maxia_marketplace_stats", "maxia_stocks_list", "maxia_stocks_price",
              "maxia_stocks_fees", "maxia_yield_best", "maxia_gpu_tiers", "maxia_defi_yield",
              "maxia_whales", "maxia_candles", "maxia_signals", "maxia_portfolio"}

@router.post("/tools/call")
async def mcp_call_tool(request: Request):
    """Execute an MCP tool call. Read-only tools work without API key (free tier: 5/min)."""
    body = await request.json()
    tool_name = body.get("name", "")
    args = body.get("arguments", {})

    # Free tier rate limiting for tools that don't need API key
    if tool_name in FREE_TOOLS and not args.get("api_key"):
        from core.security import get_real_ip
        ip = get_real_ip(request)
        import time as _t
        now = _t.time()
        calls = _mcp_free_calls.setdefault(ip, [])
        calls[:] = [t for t in calls if now - t < 60]  # Keep last 60s
        if len(calls) >= 5:
            return {"content": [{"type": "text", "text": "Free tier: max 5 calls/min. Register for unlimited: POST /api/public/register"}], "isError": True}
        calls.append(now)

    # Tier-based access control
    from core.config import get_mcp_tool_tier, MCP_TIER_ORDER
    required_tier = get_mcp_tool_tier(tool_name)
    if required_tier != "free":
        api_key = args.get("api_key", request.headers.get("x-api-key", ""))
        if not api_key:
            return {"content": [{"type": "text", "text": f"Tool '{tool_name}' requires {required_tier} tier. Register: POST /api/public/register"}], "isError": True}
        agent_tier = await _get_agent_tier(api_key)
        if MCP_TIER_ORDER.get(agent_tier, 0) < MCP_TIER_ORDER.get(required_tier, 0):
            return {"content": [{"type": "text", "text": f"Tool '{tool_name}' requires {required_tier} tier (your tier: {agent_tier}). Upgrade by increasing trade volume."}], "isError": True}

    try:
        result = await _execute_tool(tool_name, args)
        return {
            "content": [{"type": "text", "text": json.dumps(result, indent=2)}],
            "isError": False,
        }
    except Exception as e:
        return {
            "content": [{"type": "text", "text": safe_error(e, f"mcp_call:{tool_name}")["error"]}],
            "isError": True,
        }


async def _get_agent_tier(api_key: str) -> str:
    """Lookup agent tier from API key. Maps trust level to MCP tier."""
    try:
        from core.database import get_db
        db = await get_db()
        row = await db.fetchone("SELECT trust_level FROM agents WHERE api_key = ?", (api_key,))
        if not row:
            return "bronze"  # Valid key but no trust data = bronze
        trust = row[0] if row[0] is not None else 0
        if trust >= 4:
            return "whale"
        elif trust >= 2:
            return "gold"
        return "bronze"
    except Exception as e:
        log.error("MCP tier check DB error: %s", e)
        from fastapi import HTTPException
        raise HTTPException(503, "Service temporarily unavailable")


async def _execute_tool(name: str, args: dict) -> dict:
    """Route tool call to the right MAXIA function."""
    import httpx

    from core.config import PORT
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{PORT}", timeout=30) as client:

        if name == "maxia_discover":
            r = await client.get("/api/public/discover", params={
                "capability": args.get("capability", ""),
                "max_price": args.get("max_price", 100),
            })
            return r.json()

        elif name == "maxia_register":
            r = await client.post("/api/public/register", json={
                "name": args["name"],
                "wallet": args["wallet"],
                "description": f"MCP agent: {args['name']}",
            })
            return r.json()

        elif name == "maxia_sell":
            r = await client.post("/api/public/sell",
                headers={"X-API-Key": args["api_key"]},
                json={
                    "name": args["name"],
                    "description": args["description"],
                    "price_usdc": args["price_usdc"],
                    "type": args.get("type", "text"),
                    "endpoint": args.get("endpoint", ""),
                })
            return r.json()

        elif name == "maxia_execute":
            # S4 fix: payment_tx MUST be passed — no free execution
            if "payment_tx" not in args or not args["payment_tx"]:
                return {"error": "payment_tx is required. Send USDC to treasury first, then pass the tx signature."}
            r = await client.post("/api/public/execute",
                headers={"X-API-Key": args["api_key"]},
                json={
                    "service_id": args["service_id"],
                    "prompt": args["prompt"],
                    "payment_tx": args["payment_tx"],
                })
            return r.json()

        elif name == "maxia_swap_quote":
            r = await client.get("/api/public/crypto/quote", params={
                "from_token": args["from_token"],
                "to_token": args["to_token"],
                "amount": args["amount"],
            })
            return r.json()

        elif name == "maxia_prices":
            r = await client.get("/api/public/crypto/prices")
            return r.json()

        elif name == "maxia_sentiment":
            r = await client.get("/api/public/sentiment", params={"token": args.get("token", "BTC")})
            return r.json()

        elif name == "maxia_token_risk":
            r = await client.get("/api/public/token-risk", params={"address": args.get("address", "")})
            return r.json()

        elif name == "maxia_wallet_analysis":
            r = await client.get("/api/public/wallet-analysis", params={"address": args.get("address", "")})
            return r.json()

        elif name == "maxia_trending":
            r = await client.get("/api/public/trending")
            return r.json()

        elif name == "maxia_fear_greed":
            r = await client.get("/api/public/fear-greed")
            return r.json()

        elif name == "maxia_defi_yield":
            r = await client.get("/api/public/defi/best-yield", params={
                "asset": args.get("asset", "USDC"),
                "chain": args.get("chain", ""),
            })
            return r.json()

        elif name == "maxia_marketplace_stats":
            r = await client.get("/api/public/marketplace-stats")
            return r.json()

        # ── GPU Rental ──
        elif name == "maxia_gpu_tiers":
            r = await client.get("/api/public/gpu/tiers")
            return r.json()

        elif name == "maxia_gpu_rent":
            r = await client.post("/api/public/gpu/rent",
                headers={"X-API-Key": args["api_key"]},
                json={
                    "gpu_tier": args["gpu_tier"],
                    "hours": args["hours"],
                    "payment_tx": args["payment_tx"],
                })
            return r.json()

        elif name == "maxia_gpu_status":
            r = await client.get(f"/api/public/gpu/status/{args['pod_id']}",
                headers={"X-API-Key": args["api_key"]})
            return r.json()

        # ── Tokenized Stocks ──
        elif name == "maxia_stocks_list":
            r = await client.get("/api/public/stocks")
            return r.json()

        elif name == "maxia_stocks_price":
            r = await client.get(f"/api/public/stocks/price/{args['symbol']}")
            return r.json()

        elif name == "maxia_stocks_buy":
            r = await client.post("/api/public/stocks/buy",
                headers={"X-API-Key": args["api_key"]},
                json={
                    "symbol": args["symbol"],
                    "amount_usdc": args["amount_usdc"],
                    "payment_tx": args["payment_tx"],
                })
            return r.json()

        elif name == "maxia_stocks_sell":
            r = await client.post("/api/public/stocks/sell",
                headers={"X-API-Key": args["api_key"]},
                json={
                    "symbol": args["symbol"],
                    "shares": args["shares"],
                })
            return r.json()

        elif name == "maxia_stocks_portfolio":
            r = await client.get("/api/public/stocks/portfolio",
                headers={"X-API-Key": args["api_key"]})
            return r.json()

        elif name == "maxia_stocks_fees":
            r = await client.get("/api/public/stocks/compare-fees")
            return r.json()

        # ── Hub Web3 tools ──
        elif name == "maxia_yield_best":
            asset = args.get("asset", "USDC")
            limit = args.get("limit", 5)
            r = await client.get(f"/api/public/yield/best?asset={asset}&limit={limit}")
            return r.json()
        elif name == "maxia_bridge_quote":
            r = await client.get(f"/api/bridge/quote?from_chain={args.get('from_chain')}&to_chain={args.get('to_chain')}&token={args.get('token','USDC')}&amount={args.get('amount')}")
            return r.json()
        elif name == "maxia_rpc_call":
            chain = args.get("chain", "solana")
            # BUG 10 fix: whitelist chain to prevent path injection
            _VALID_CHAINS = {"solana", "base", "ethereum", "xrp", "polygon", "arbitrum",
                             "avalanche", "bnb", "ton", "sui", "tron", "near", "aptos", "sei"}
            if chain not in _VALID_CHAINS:
                return {"error": f"Unsupported chain: {chain}"}
            r = await client.post(f"/api/rpc/{chain}", json={"jsonrpc": "2.0", "id": 1, "method": args.get("method", ""), "params": args.get("params", [])})
            return r.json()
        elif name == "maxia_oracle_feed":
            # Use /api/public/crypto/prices (CoinGecko live) — NOT /api/oracle/feed (fallback statique)
            r = await client.get("/api/public/crypto/prices")
            data = r.json()
            # Reformat to oracle feed format
            prices = data.get("prices", data)
            price_list = []
            ts = int(time.time())
            for sym, entry in prices.items():
                price_val = entry.get("price", 0) if isinstance(entry, dict) else entry
                source = entry.get("source", "live") if isinstance(entry, dict) else "live"
                price_list.append({"token": sym, "price_usd": price_val, "timestamp": ts, "source": source, "confidence": "high"})
            price_list.sort(key=lambda x: x["token"])
            return {"prices": price_list, "meta": {"total_tokens": len(price_list), "cache_age_s": 0, "updated_at": ts, "oracle": "maxia", "version": "v12", "chains_supported": 14}}
        elif name == "maxia_datasets":
            r = await client.get("/api/oracle/datasets")
            return r.json()
        elif name == "maxia_nft_mint":
            r = await client.post("/api/nft/mint", json=args)
            return r.json()
        elif name == "maxia_agent_id":
            addr = args.get("agent_address", "")
            r = await client.get(f"/api/nft/agent-id/{addr}")
            return r.json()
        elif name == "maxia_trust_score":
            addr = args.get("agent_address", "")
            r = await client.get(f"/api/nft/trust-score/{addr}")
            return r.json()
        elif name == "maxia_subscribe":
            r = await client.post("/api/subscriptions/create", json=args)
            return r.json()

        # ── Trading tools ──
        elif name == "maxia_whales":
            chain = args.get("chain", "solana")
            min_usd = args.get("min_usd", 10000)
            limit = args.get("limit", 10)
            r = await client.get(f"/api/trading/whales?chain={chain}&min_usd={min_usd}&limit={limit}")
            return r.json()
        elif name == "maxia_candles":
            token = args.get("token", "SOL")
            interval = args.get("interval", "1h")
            limit = args.get("limit", 24)
            r = await client.get(f"/api/trading/candles/{token}?interval={interval}&limit={limit}")
            return r.json()
        elif name == "maxia_signals":
            r = await client.get(f"/api/trading/signals/{args.get('token', 'SOL')}")
            return r.json()
        elif name == "maxia_portfolio":
            addr = args.get("address", "")
            chains = args.get("chains", "solana,base,ethereum")
            r = await client.get(f"/api/trading/portfolio/{addr}?chains={chains}")
            return r.json()
        elif name == "maxia_price_alert":
            r = await client.post("/api/trading/alerts", json=args)
            return r.json()

        # ── Protocol Catalog tools ──
        elif name == "maxia_protocol_search":
            chain = args.get("chain", "")
            ptype = args.get("type", "")
            params = {}
            if chain:
                params["chain"] = chain
            if ptype:
                params["type"] = ptype
            r = await client.get("/api/goat/protocols", params=params or None)
            return r.json()
        elif name == "maxia_protocol_info":
            protocol = args.get("protocol", "")
            if not protocol:
                return {"error": "Missing 'protocol' parameter"}
            r = await client.get(f"/api/goat/protocols/{protocol}")
            if r.status_code == 404:
                return {"error": f"Protocol '{protocol}' not found. Use maxia_protocol_search to list available protocols."}
            return r.json()

        # ── Fine-Tuning, AWP, LLM tools removed (not yet implemented) ──

        else:
            return {"error": f"Unknown tool: {name}", "available": [t["name"] for t in MCP_TOOLS]}


# ══════════════════════════════════════════
# MCP SSE Transport
# ══════════════════════════════════════════

@router.get("/sse")
async def mcp_sse(request: Request):
    """SSE transport for MCP. Streams tool results."""
    async def event_stream():
        # Send initial capabilities
        tools_msg = json.dumps({"type": "tools", "tools": MCP_TOOLS})
        yield f"event: capabilities\ndata: {tools_msg}\n\n"

        # Keep connection alive with heartbeat
        while True:
            yield f"event: heartbeat\ndata: {json.dumps({'ts': int(time.time())})}\n\n"
            await asyncio.sleep(30)

    return StreamingResponse(event_stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})

@router.post("/sse/call")
async def mcp_sse_call(request: Request):
    """Execute MCP tool via SSE-compatible endpoint (same auth as /tools/call)."""
    body = await request.json()
    tool_name = body.get("name", "")
    args = body.get("arguments", {})

    # Same auth + rate limit as /tools/call (BUG 9 fix)
    if tool_name in FREE_TOOLS and not args.get("api_key"):
        from core.security import get_real_ip
        ip = get_real_ip(request)
        import time as _t
        now = _t.time()
        calls = _mcp_free_calls.setdefault(ip, [])
        calls[:] = [t for t in calls if now - t < 60]
        if len(calls) >= 5:
            return {"content": [{"type": "text", "text": "Free tier: max 5 calls/min. Register for unlimited: POST /api/public/register"}], "isError": True}
        calls.append(now)
    elif tool_name not in FREE_TOOLS:
        from core.config import get_mcp_tool_tier, MCP_TIER_ORDER
        required_tier = get_mcp_tool_tier(tool_name)
        if required_tier != "free":
            api_key = args.get("api_key", request.headers.get("x-api-key", ""))
            if not api_key:
                return {"content": [{"type": "text", "text": f"Tool '{tool_name}' requires {required_tier} tier. Register: POST /api/public/register"}], "isError": True}
            agent_tier = await _get_agent_tier(api_key)
            if MCP_TIER_ORDER.get(agent_tier, 0) < MCP_TIER_ORDER.get(required_tier, 0):
                return {"content": [{"type": "text", "text": f"Tool '{tool_name}' requires {required_tier} tier (your tier: {agent_tier}). Upgrade by increasing trade volume."}], "isError": True}

    try:
        result = await _execute_tool(tool_name, args)
        return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}], "isError": False}
    except Exception as e:
        return {"content": [{"type": "text", "text": safe_error(e, f"mcp_sse:{tool_name}")["error"]}], "isError": True}


# ══════════════════════════════════════════
# MCP Discovery — for frameworks
# ══════════════════════════════════════════

@router.get("/manifest")
async def mcp_manifest():
    """MCP manifest for auto-discovery by frameworks."""
    return {
        "schema_version": "1.0",
        "name": "maxia",
        "description": "MAXIA AI-to-AI Marketplace on Solana",
        "url": f"{MAXIA_URL}/mcp",
        "transport": {"sse": f"{MAXIA_URL}/mcp/sse", "rest": f"{MAXIA_URL}/mcp/tools/call"},
        "tools": MCP_TOOLS,
        "authentication": {
            "type": "api_key",
            "header": "X-API-Key",
            "register_url": f"{MAXIA_URL}/api/public/register",
        },
        "capabilities": ["discover", "register", "sell", "execute", "swap", "prices", "defi", "sentiment", "token-risk", "wallet-analysis", "trending", "fear-greed", "gpu-rental", "tokenized-stocks", "candles", "whale-tracker", "copy-trading", "leaderboard", "agent-chat", "templates", "webhooks", "escrow", "sla", "clones"],
    }
