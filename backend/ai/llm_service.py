"""MAXIA LLM-as-a-Service — OpenAI-compatible API backed by multi-provider routing.

Uses the existing LLMRouter (Ollama → Groq → Mistral → Claude) with:
- OpenAI-compatible /api/llm/chat endpoint
- Per-agent cost tracking
- Usage metering in USDC
- Automatic tier selection based on complexity

Revenue: $0.001-0.015 per 1K tokens depending on tier (markup on provider cost).
"""
import asyncio, time, uuid, json, logging
from fastapi import APIRouter, HTTPException, Header, Request
from pydantic import BaseModel, Field
from typing import Optional, List

log = logging.getLogger("llm_service")

router = APIRouter(prefix="/api/llm", tags=["llm-service"])

# ── Pricing per 1K tokens (USDC) — MAXIA markup over provider cost ──
PRICING = {
    "local":     {"input": 0.0005, "output": 0.001},     # Ollama local 7900XT (free infra, pure margin)
    "fast":      {"input": 0.0008, "output": 0.0015},    # Groq
    "mid":       {"input": 0.001,  "output": 0.003},     # Mistral
    "strategic": {"input": 0.005,  "output": 0.02},      # Claude
}

# ── Per-agent usage tracking ──
_usage: dict = {}  # api_key -> {total_tokens, total_cost, calls, date}
_USAGE_MAX_KEYS = 5000  # Cap to prevent unbounded growth

# ── Models ──

class ChatMessage(BaseModel):
    role: str = "user"
    content: str

class ChatRequest(BaseModel):
    messages: List[ChatMessage]
    model: Optional[str] = None  # auto, local, fast, mid, strategic
    max_tokens: int = Field(default=500, ge=1, le=8192)
    temperature: float = Field(default=0.7, ge=0, le=2)
    stream: bool = False

class CompletionRequest(BaseModel):
    prompt: str
    model: Optional[str] = None
    max_tokens: int = Field(default=500, ge=1, le=8192)
    temperature: float = Field(default=0.7, ge=0, le=2)


# ── Helpers ──

def _track_usage(api_key: str, tier: str, input_tokens: int, output_tokens: int):
    """Track usage and cost for an API key."""
    today = time.strftime("%Y-%m-%d")
    if api_key not in _usage or _usage[api_key].get("date") != today:
        # Cleanup old keys if too many (prevent unbounded growth)
        if len(_usage) >= _USAGE_MAX_KEYS:
            old_keys = [k for k, v in _usage.items() if v.get("date") != today]
            for k in old_keys[:len(old_keys)//2]:  # Remove half of stale keys
                _usage.pop(k, None)
        _usage[api_key] = {"total_tokens": 0, "total_cost": 0.0, "calls": 0, "date": today}

    pricing = PRICING.get(tier, PRICING["fast"])
    cost = (input_tokens / 1000) * pricing["input"] + (output_tokens / 1000) * pricing["output"]

    _usage[api_key]["total_tokens"] += input_tokens + output_tokens
    _usage[api_key]["total_cost"] += cost
    _usage[api_key]["calls"] += 1
    return cost


def _estimate_tokens(text: str) -> int:
    """Rough token estimate (4 chars per token)."""
    return max(1, len(text) // 4)


# ── Endpoints ──

@router.get("/models")
async def list_models():
    """List available LLM tiers with pricing."""
    return {
        "models": [
            {
                "id": "auto",
                "name": "Auto-route (best tier for your request)",
                "description": "Automatically selects the optimal model based on request complexity",
                "pricing": "Varies by complexity",
            },
            {
                "id": "local",
                "name": "Ollama (Qwen 2.5 32B on RX 7900XT)",
                "description": "Local GPU inference, 20GB VRAM, fast response, great for most tasks",
                "pricing_per_1k_tokens": PRICING["local"],
                "gpu": "AMD RX 7900XT 20GB",
            },
            {
                "id": "fast",
                "name": "Groq (Llama 3.3 70B)",
                "description": "Cloud inference, very fast, good for most tasks",
                "pricing_per_1k_tokens": PRICING["fast"],
            },
            {
                "id": "mid",
                "name": "Mistral Small",
                "description": "Medium complexity, multi-step reasoning",
                "pricing_per_1k_tokens": PRICING["mid"],
            },
            {
                "id": "strategic",
                "name": "Claude Sonnet",
                "description": "Highest quality, complex reasoning, vision, long context",
                "pricing_per_1k_tokens": PRICING["strategic"],
            },
        ],
        "note": "Use model='auto' to let MAXIA pick the best tier. Pricing in USDC per 1K tokens.",
    }


@router.post("/chat")
async def chat_completion(req: ChatRequest, x_api_key: str = Header(alias="X-API-Key")):
    """OpenAI-compatible chat completion endpoint. Routes to optimal LLM tier."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")

    from ai.llm_router import LLMRouter, Tier

    llm = LLMRouter()

    # Build prompt from messages
    system = ""
    user_prompt = ""
    for msg in req.messages:
        if msg.role == "system":
            system = msg.content
        elif msg.role == "user":
            user_prompt += msg.content + "\n"
        elif msg.role == "assistant":
            user_prompt += f"[Previous response: {msg.content[:200]}]\n"

    user_prompt = user_prompt.strip()
    if not user_prompt:
        raise HTTPException(400, "At least one user message required")

    # Determine tier
    tier = None
    if req.model and req.model != "auto":
        tier_map = {"local": Tier.LOCAL, "fast": Tier.FAST, "mid": Tier.MID, "strategic": Tier.STRATEGIC}
        tier = tier_map.get(req.model)
        if not tier:
            raise HTTPException(400, f"Unknown model: {req.model}. Use: auto, local, fast, mid, strategic")

    # Call LLM
    result = await llm.call(
        prompt=user_prompt,
        tier=tier,
        system=system,
        max_tokens=req.max_tokens,
    )

    if not result:
        raise HTTPException(503, "All LLM providers unavailable")

    # Track usage
    used_tier = (tier or llm.classify_complexity(user_prompt)).value
    input_tokens = _estimate_tokens(system + user_prompt)
    output_tokens = _estimate_tokens(result)
    cost = _track_usage(x_api_key, used_tier, input_tokens, output_tokens)

    # OpenAI-compatible response format
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": f"maxia-{used_tier}",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": result},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
        "cost_usdc": round(cost, 6),
        "tier": used_tier,
    }


@router.post("/completions")
async def text_completion(req: CompletionRequest, x_api_key: str = Header(alias="X-API-Key")):
    """Simple text completion endpoint."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")

    from ai.llm_router import LLMRouter, Tier

    llm = LLMRouter()
    tier = None
    if req.model and req.model != "auto":
        tier_map = {"local": Tier.LOCAL, "fast": Tier.FAST, "mid": Tier.MID, "strategic": Tier.STRATEGIC}
        tier = tier_map.get(req.model)

    result = await llm.call(prompt=req.prompt, tier=tier, max_tokens=req.max_tokens)

    if not result:
        raise HTTPException(503, "All LLM providers unavailable")

    used_tier = (tier or llm.classify_complexity(req.prompt)).value
    input_tokens = _estimate_tokens(req.prompt)
    output_tokens = _estimate_tokens(result)
    cost = _track_usage(x_api_key, used_tier, input_tokens, output_tokens)

    return {
        "id": f"cmpl-{uuid.uuid4().hex[:12]}",
        "object": "text_completion",
        "created": int(time.time()),
        "model": f"maxia-{used_tier}",
        "choices": [{"text": result, "index": 0, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
        "cost_usdc": round(cost, 6),
        "tier": used_tier,
    }


@router.get("/usage")
async def get_usage(x_api_key: str = Header(alias="X-API-Key")):
    """Get today's LLM usage and cost for your API key."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")

    usage = _usage.get(x_api_key, {"total_tokens": 0, "total_cost": 0.0, "calls": 0, "date": time.strftime("%Y-%m-%d")})
    return {
        "date": usage["date"],
        "calls": usage["calls"],
        "total_tokens": usage["total_tokens"],
        "total_cost_usdc": round(usage["total_cost"], 6),
        "pricing": PRICING,
    }


log.info("[LLM] LLM-as-a-Service (OpenAI-compatible, multi-provider) monte")
