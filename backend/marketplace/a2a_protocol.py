"""MAXIA A2A Protocol — Google Agent2Agent (Linux Foundation)

Implements the A2A standard for agent interoperability:
- Agent Card discovery (/.well-known/agent.json)
- JSON-RPC 2.0 task lifecycle (tasks/send, tasks/get, tasks/cancel)
- SSE streaming for long-running tasks (tasks/sendSubscribe)
- Message/Part/Artifact model

Spec: https://github.com/a2aproject/A2A
"""
import asyncio, json, time, uuid, logging
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from core.error_utils import safe_error

log = logging.getLogger("a2a")

router = APIRouter(tags=["a2a"])

# ── A2A Agent Card ──
# Full spec-compliant agent card for discovery

A2A_AGENT_CARD = {
    "name": "MAXIA",
    "description": "AI-to-AI Marketplace on 15 blockchains. Agents discover, buy, and sell AI services using USDC/USDT/BTC. 65 tokens, 25 stocks, GPU rental, DeFi yields, Bitcoin Lightning.",
    "url": "https://maxiaworld.app",
    "version": "12.2.0",
    "protocolVersion": "0.3",
    "provider": {
        "organization": "MAXIA",
        "url": "https://maxiaworld.app",
    },
    "capabilities": {
        "streaming": True,
        "pushNotifications": False,
        "stateTransitionHistory": True,
    },
    "authentication": {
        "schemes": ["apiKey"],
        "credentials": "X-API-Key header (free via POST /api/public/register)",
    },
    "defaultInputModes": ["text/plain", "application/json"],
    "defaultOutputModes": ["text/plain", "application/json"],
    "skills": [
        {
            "id": "marketplace-discover",
            "name": "Discover AI Services",
            "description": "Find AI services by capability (audit, code, data, image, text, sentiment, scraper). Browse pricing, ratings, and availability across the marketplace.",
            "tags": ["marketplace", "discovery", "ai-services"],
            "examples": ["Find a code review service under $5", "List all image generation services"],
        },
        {
            "id": "marketplace-execute",
            "name": "Execute AI Service",
            "description": "Buy and execute an AI service from the marketplace. Pay with USDC on Solana, get results instantly.",
            "tags": ["marketplace", "execute", "usdc"],
            "examples": ["Run a smart contract audit on this code", "Generate a logo for my project"],
        },
        {
            "id": "crypto-swap",
            "name": "Crypto Token Swap",
            "description": "Swap between 65 crypto tokens (4160 pairs) across 7 chains via Jupiter + 0x. Live prices, low fees (0.01-0.10%).",
            "tags": ["crypto", "swap", "solana", "defi"],
            "examples": ["Swap 10 SOL to USDC", "Get a quote for 1 ETH to BTC"],
        },
        {
            "id": "gpu-rental",
            "name": "GPU Rental",
            "description": "Rent GPUs from RTX4090 ($0.76/h) to H200 ($4.74/h). Pay per hour in USDC. SSH + Jupyter access.",
            "tags": ["gpu", "compute", "ai-training"],
            "examples": ["Rent an A100 for 2 hours", "What GPU do I need for Llama 70B?"],
        },
        {
            "id": "llm-finetune",
            "name": "LLM Fine-Tuning",
            "description": "Fine-tune any LLM (Llama, Qwen, Mistral, Gemma, DeepSeek, Phi) on your dataset via Unsloth. GGUF, safetensors, LoRA output.",
            "tags": ["fine-tuning", "llm", "unsloth", "training"],
            "examples": ["Fine-tune Llama 8B on my customer support data", "How much to fine-tune Qwen 32B?"],
        },
        {
            "id": "defi-yields",
            "name": "DeFi Yield Scanner",
            "description": "Find the best DeFi yields across 15 chains. Aave, Compound, Marinade, Jito, Lido, and more.",
            "tags": ["defi", "yields", "apy"],
            "examples": ["Best USDC yields right now", "Where can I stake SOL for highest APY?"],
        },
        {
            "id": "tokenized-stocks",
            "name": "Tokenized Stock Trading",
            "description": "Trade tokenized US stocks (AAPL, TSLA, NVDA, GOOGL, etc.) with USDC. Fractional shares from $1.",
            "tags": ["stocks", "trading", "tokenized"],
            "examples": ["Buy $100 of TSLA", "What's the price of AAPL?"],
        },
        {
            "id": "awp-staking",
            "name": "AWP Agent Staking",
            "description": "Stake USDC on the Autonomous Worker Protocol (Base L2) for trust score and 3-12% APY rewards.",
            "tags": ["staking", "awp", "base", "rewards"],
            "examples": ["Stake 100 USDC for 90 days", "What's my trust score?"],
        },
        {
            "id": "wallet-analysis",
            "name": "Wallet Analysis",
            "description": "Analyze any Solana wallet: holdings, transaction history, DeFi positions, risk score.",
            "tags": ["wallet", "analysis", "solana"],
            "examples": ["Analyze this wallet: 7Rt...", "What tokens does this wallet hold?"],
        },
        {
            "id": "market-intelligence",
            "name": "Market Intelligence",
            "description": "Crypto sentiment, Fear & Greed Index, trending tokens, whale tracking, technical signals.",
            "tags": ["sentiment", "market", "trading", "signals"],
            "examples": ["BTC sentiment right now", "Any whale movements today?"],
        },
        {
            "id": "evm-swap",
            "name": "EVM Token Swap (6 chains)",
            "description": "Swap tokens on Ethereum, Base, Polygon, Arbitrum, Avalanche, BNB via 0x. 36 EVM tokens, 630+ pairs. Supports $SOLVR on Base.",
            "tags": ["swap", "evm", "base", "ethereum", "defi", "0x", "solvr"],
            "examples": [
                "Swap 100 USDC to WETH on Base",
                "Get quote for DEGEN to USDC on Base",
                "Swap ETH to USDC on Arbitrum",
            ],
        },
        {
            "id": "escrow",
            "name": "On-Chain Escrow",
            "description": "Lock USDC in on-chain escrow on Solana (Anchor PDA) or Base L2 (Solidity). Buyer confirms delivery or opens dispute. 48h auto-refund.",
            "tags": ["escrow", "solana", "base", "usdc", "trust"],
            "examples": ["Lock 50 USDC in escrow for a code review", "Check escrow status"],
        },
        {
            "id": "sentiment-analysis",
            "name": "Crypto Sentiment Analysis",
            "description": "Real-time crypto sentiment via Fear & Greed Index, social signals, whale tracking, and technical indicators.",
            "tags": ["sentiment", "analysis", "crypto", "fear-greed"],
            "examples": ["What's BTC sentiment right now?", "Fear & Greed Index today"],
        },
        {
            "id": "image-generation",
            "name": "Image Generation",
            "description": "Generate images using FLUX.1 via Pollinations.ai. Up to 2048px. Free tier available.",
            "tags": ["image", "generation", "ai", "flux"],
            "examples": ["Generate a logo for an AI project", "Create a banner image"],
        },
        {
            "id": "web-scraper",
            "name": "Web Scraping",
            "description": "Scrape any web page and return structured JSON data. $0.05/page.",
            "tags": ["scraper", "data", "web"],
            "examples": ["Scrape product data from this URL", "Extract article text"],
        },
        {
            "id": "smart-contract-audit",
            "name": "Smart Contract Audit",
            "description": "AI-powered security audit of Solidity or Rust smart contracts. $9.99 per audit.",
            "tags": ["audit", "security", "smart-contract", "solidity", "rust"],
            "examples": ["Audit this Solidity contract for vulnerabilities"],
        },
        {
            "id": "code-generation",
            "name": "Code Generation",
            "description": "Generate code in Python, Rust, JavaScript, or TypeScript. $3.99 per request.",
            "tags": ["code", "generation", "python", "rust", "javascript"],
            "examples": ["Write a Rust function to parse JSON", "Generate a Python API client"],
        },
    ],
}

# ── Task storage (in-memory, prod: use DB) ──
_tasks: dict = {}  # task_id -> task object
_TASKS_MAX = 5000  # P2 fix: prevent unbounded growth


def _cleanup_tasks():
    """Remove completed tasks older than 1 hour."""
    if len(_tasks) <= _TASKS_MAX:
        return
    cutoff = time.time() - 3600
    stale = [k for k, v in _tasks.items()
             if v.get("status") in ("completed", "failed", "canceled")
             and v.get("updatedAt", 0) < cutoff]
    for k in stale:
        del _tasks[k]


# ── A2A JSON-RPC Methods ──

def _make_error(code: int, message: str, req_id=None) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _make_result(result: dict, req_id=None) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


async def _handle_tasks_send(params: dict, req_id) -> dict:
    """Create a new task or continue an existing one."""
    task_id = params.get("id", str(uuid.uuid4()))
    message = params.get("message", {})

    # Extract user's request from message parts
    user_text = ""
    for part in message.get("parts", []):
        if part.get("type") == "text":
            user_text += part.get("text", "")

    if not user_text:
        return _make_error(-32602, "Message must contain at least one text part", req_id)

    # Route to the right MAXIA service based on intent
    result = await _route_request(user_text, params.get("metadata", {}))

    # Create task record
    task = {
        "id": task_id,
        "status": {"state": "completed"},
        "messages": [
            message,
            {
                "role": "agent",
                "parts": [{"type": "text", "text": json.dumps(result, indent=2)}],
            },
        ],
        "artifacts": [],
        "metadata": params.get("metadata", {}),
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    # If the result contains structured data, add as artifact
    if isinstance(result, dict) and not result.get("error"):
        task["artifacts"].append({
            "type": "application/json",
            "name": "result",
            "parts": [{"type": "data", "data": result}],
        })

    _tasks[task_id] = task
    _cleanup_tasks()
    return _make_result(task, req_id)


async def _handle_tasks_get(params: dict, req_id) -> dict:
    """Get task status and results."""
    task_id = params.get("id")
    if not task_id or task_id not in _tasks:
        return _make_error(-32602, f"Task not found: {task_id}", req_id)
    return _make_result(_tasks[task_id], req_id)


async def _handle_tasks_cancel(params: dict, req_id) -> dict:
    """Cancel a running task."""
    task_id = params.get("id")
    if not task_id or task_id not in _tasks:
        return _make_error(-32602, f"Task not found: {task_id}", req_id)

    task = _tasks[task_id]
    if task["status"]["state"] in ("completed", "failed", "canceled"):
        return _make_error(-32600, f"Task already {task['status']['state']}", req_id)

    task["status"] = {"state": "canceled"}
    task["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return _make_result(task, req_id)


async def _handle_tasks_send_subscribe(params: dict, req_id):
    """SSE streaming for long-running tasks (finetune, GPU provisioning)."""
    task_id = params.get("id", str(uuid.uuid4()))
    message = params.get("message", {})

    user_text = ""
    for part in message.get("parts", []):
        if part.get("type") == "text":
            user_text += part.get("text", "")

    async def event_stream():
        # Emit working state
        working = {
            "jsonrpc": "2.0", "id": req_id,
            "result": {
                "id": task_id,
                "status": {"state": "working", "message": {"role": "agent", "parts": [{"type": "text", "text": "Processing your request..."}]}},
            },
        }
        yield f"data: {json.dumps(working)}\n\n"

        # Process the request
        result = await _route_request(user_text, params.get("metadata", {}))

        # Emit completed state
        task = {
            "id": task_id,
            "status": {"state": "completed"},
            "messages": [
                message,
                {"role": "agent", "parts": [{"type": "text", "text": json.dumps(result, indent=2)}]},
            ],
            "artifacts": [{"type": "application/json", "name": "result", "parts": [{"type": "data", "data": result}]}] if isinstance(result, dict) and not result.get("error") else [],
        }
        _tasks[task_id] = task

        completed = {"jsonrpc": "2.0", "id": req_id, "result": task}
        yield f"data: {json.dumps(completed)}\n\n"

    return event_stream


# ── Intent Router ──

async def _route_request(text: str, metadata: dict) -> dict:
    """Route A2A request to the right MAXIA backend endpoint."""
    import httpx
    from core.config import PORT

    text_lower = text.lower().strip()

    async with httpx.AsyncClient(base_url=f"http://localhost:{PORT}", timeout=30) as client:
        try:
            # Discover services
            if any(kw in text_lower for kw in ["discover", "find service", "search service", "list service", "browse"]):
                cap = ""
                for c in ["audit", "code", "data", "image", "text", "sentiment", "scraper", "finetune"]:
                    if c in text_lower:
                        cap = c
                        break
                r = await client.get("/api/public/discover", params={"capability": cap, "max_price": 100})
                return r.json()

            # EVM swap (Base, Ethereum, Polygon, Arbitrum, Avalanche, BNB)
            evm_chains = ["base", "ethereum", "polygon", "arbitrum", "avalanche", "bnb", "evm"]
            if any(kw in text_lower for kw in ["swap", "exchange", "convert"]) and any(c in text_lower for c in evm_chains + ["solvr", "0x", "weth", "degen"]):
                r = await client.get("/api/public/crypto/prices")
                return {
                    "action": "evm_swap",
                    "chains": ["ethereum", "base", "polygon", "arbitrum", "avalanche", "bnb"],
                    "tokens": 36,
                    "pairs": 232,
                    "prices": r.json(),
                    "hint": "Use POST /api/public/crypto/swap with {from_token, to_token, amount, chain, payment_tx}. Supports $SOLVR on Base.",
                }

            # Swap quote (Solana default)
            if any(kw in text_lower for kw in ["swap", "exchange", "convert"]):
                r = await client.get("/api/public/crypto/prices")
                return {"action": "swap", "prices": r.json(), "hint": "Use POST /api/public/crypto/swap with {from_token, to_token, amount, payment_tx}"}

            # Prices
            if any(kw in text_lower for kw in ["price", "prices", "how much"]):
                r = await client.get("/api/public/crypto/prices")
                return r.json()

            # GPU
            if any(kw in text_lower for kw in ["gpu", "rent", "compute"]):
                r = await client.get("/api/public/gpu/tiers")
                return r.json()

            # Fine-tune
            if any(kw in text_lower for kw in ["fine-tune", "finetune", "train", "unsloth"]):
                r = await client.get("/api/finetune/models")
                return r.json()

            # Yields
            if any(kw in text_lower for kw in ["yield", "apy", "defi", "earn"]):
                asset = "USDC"
                for a in ["ETH", "SOL", "BTC"]:
                    if a.lower() in text_lower:
                        asset = a
                        break
                r = await client.get(f"/api/yields/best?asset={asset}&limit=10")
                return r.json()

            # Stocks
            if any(kw in text_lower for kw in ["stock", "aapl", "tsla", "nvda", "googl"]):
                r = await client.get("/api/public/stocks")
                return r.json()

            # AWP
            if any(kw in text_lower for kw in ["awp", "stake", "staking", "trust score"]):
                r = await client.get("/api/awp/discover")
                return r.json()

            # Sentiment
            if any(kw in text_lower for kw in ["sentiment", "fear", "greed", "mood"]):
                r = await client.get("/api/public/fear-greed")
                return r.json()

            # Wallet
            if any(kw in text_lower for kw in ["wallet", "analyze", "holdings"]):
                return {"action": "wallet_analysis", "hint": "Use GET /api/public/wallet-analysis?address=WALLET_ADDRESS"}

            # Stats
            if any(kw in text_lower for kw in ["stats", "marketplace", "volume"]):
                r = await client.get("/api/public/marketplace-stats")
                return r.json()

            # Default: return capabilities
            return {
                "message": "MAXIA AI-to-AI Marketplace. How can I help?",
                "capabilities": [s["name"] for s in A2A_AGENT_CARD["skills"]],
                "hint": "Try: 'discover AI services', 'GPU pricing', 'swap SOL to USDC', 'best yields', 'fine-tune Llama 8B'",
            }

        except Exception as e:
            return safe_error(e, "a2a_handle_message")


# ── A2A JSON-RPC Dispatcher ──

A2A_METHODS = {
    # Official A2A spec method names
    "message/send": _handle_tasks_send,
    "tasks/get": _handle_tasks_get,
    "tasks/cancel": _handle_tasks_cancel,
    # Legacy method names (backward compat)
    "tasks/send": _handle_tasks_send,
}


@router.post("/a2a")
async def a2a_endpoint(request: Request):
    """A2A JSON-RPC 2.0 endpoint. All agent-to-agent communication goes here."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(_make_error(-32700, "Parse error"), status_code=400)

    jsonrpc = body.get("jsonrpc")
    if jsonrpc != "2.0":
        return JSONResponse(_make_error(-32600, "Invalid Request: jsonrpc must be '2.0'"), status_code=400)

    method = body.get("method", "")
    params = body.get("params", {})
    req_id = body.get("id")

    # SSE streaming methods
    if method in ("message/stream", "tasks/sendSubscribe"):
        stream_gen = await _handle_tasks_send_subscribe(params, req_id)
        return StreamingResponse(
            stream_gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    # Standard JSON-RPC methods
    handler = A2A_METHODS.get(method)
    if not handler:
        return JSONResponse(_make_error(-32601, f"Method not found: {method}", req_id))

    result = await handler(params, req_id)
    return JSONResponse(result)


# ── A2A Agent Card endpoint (overrides the simple one in main.py) ──

@router.get("/.well-known/agent-card.json")
async def a2a_agent_card():
    """A2A-compliant agent card for discovery (official spec endpoint)."""
    return A2A_AGENT_CARD


@router.get("/.well-known/agent.json")
async def a2a_agent_card_legacy():
    """Legacy agent card endpoint (backward compat)."""
    return A2A_AGENT_CARD


log.info("[A2A] Agent2Agent Protocol (Google/Linux Foundation) monte — JSON-RPC 2.0 + SSE")
