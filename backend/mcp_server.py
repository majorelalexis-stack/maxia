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
"""
import json, time
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

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
            },
            "required": ["api_key", "service_id", "prompt"],
        },
    },
    {
        "name": "maxia_swap_quote",
        "description": "Get a crypto swap quote on Solana. 15 tokens, 210 pairs. Returns price and commission.",
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
        "description": "Get live cryptocurrency prices. 15 tokens + 10 US stocks. Updated every 30 seconds.",
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
async def mcp_list_tools():
    """List all available MCP tools."""
    return {"tools": MCP_TOOLS}


@router.post("/tools/call")
async def mcp_call_tool(request: Request):
    """Execute an MCP tool call."""
    body = await request.json()
    tool_name = body.get("name", "")
    args = body.get("arguments", {})

    try:
        result = await _execute_tool(tool_name, args)
        return {
            "content": [{"type": "text", "text": json.dumps(result, indent=2)}],
            "isError": False,
        }
    except Exception as e:
        return {
            "content": [{"type": "text", "text": f"Error: {str(e)}"}],
            "isError": True,
        }


async def _execute_tool(name: str, args: dict) -> dict:
    """Route tool call to the right MAXIA function."""
    import httpx

    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=30) as client:

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
            r = await client.post("/api/public/execute",
                headers={"X-API-Key": args["api_key"]},
                json={
                    "service_id": args["service_id"],
                    "prompt": args["prompt"],
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

        else:
            return {"error": f"Unknown tool: {name}", "available": [t["name"] for t in MCP_TOOLS]}


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
        "tools": MCP_TOOLS,
        "authentication": {
            "type": "api_key",
            "header": "X-API-Key",
            "register_url": f"{MAXIA_URL}/api/public/register",
        },
        "capabilities": ["discover", "register", "sell", "execute", "swap", "prices", "defi", "sentiment", "token-risk", "wallet-analysis", "trending", "fear-greed"],
    }
