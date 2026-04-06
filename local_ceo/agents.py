"""Agents — Virtual sub-agents for CEO Local, each with distinct prompts and configs.

5 virtual agents (same Ollama model, different system prompts):
  - STRATEGIST: deep analysis, business decisions (think=on)
  - WRITER: tweets, emails, reports (think=off, creative)
  - ANALYST: scoring, evaluation (think=on)
  - MONITOR: health classification (think=off, terse)
  - CHAT: Alexis chat assistant (think=on, French)

Also loads the MAXIA knowledge base and defines shared constants used by missions.
"""
import logging
import os
from dataclasses import dataclass

log = logging.getLogger("ceo")


# ══════════════════════════════════════════
# Agent configs — frozen dataclass (immutable)
# ══════════════════════════════════════════

@dataclass(frozen=True)
class AgentConfig:
    """Configuration for a virtual agent. Same model, different prompt/params."""
    name: str
    system_prompt: str
    think: bool        # Qwen3.5 /think mode
    max_tokens: int
    temperature: float
    timeout: int       # seconds


STRATEGIST = AgentConfig(
    name="strategist",
    system_prompt=(
        "You are the MAXIA CEO Strategic Advisor. You analyze business metrics, "
        "competitive intelligence, and market signals to make data-driven decisions. "
        "Think deeply before answering. Focus on: what's working, what's not, what to "
        "try next. Always back recommendations with data."
    ),
    think=True,
    max_tokens=1000,
    temperature=0.3,
    timeout=300,
)

WRITER = AgentConfig(
    name="writer",
    system_prompt=(
        "You are the MAXIA CEO Content Writer. You craft tweets, emails, and reports. "
        "Rules: Professional tone. No hype words (revolutionary, game-changing, moon, "
        "lambo). 80% value, 20% MAXIA mention. Include maxiaworld.app link when "
        "relevant. Max 280 chars for tweets. Max 150 words for emails."
    ),
    think=False,
    max_tokens=300,
    temperature=0.7,
    timeout=60,
)

ANALYST = AgentConfig(
    name="analyst",
    system_prompt=(
        "You are the MAXIA CEO Analyst. You score opportunities, evaluate AI agents, "
        "and assess competitive threats. Use structured scoring (1-10). "
        "Score 8-10: autonomous agents that could sell/buy on MAXIA. "
        "Score 5-7: technical tools with integration potential. "
        "Score 1-4: social bots, influencers, chatbots — not relevant."
    ),
    think=True,
    max_tokens=500,
    temperature=0.5,
    timeout=120,
)

MONITOR = AgentConfig(
    name="monitor",
    system_prompt=(
        "You are the MAXIA Health Monitor. Classify inputs as OK/WARNING/CRITICAL. "
        "Be terse. Only flag real problems, not noise."
    ),
    think=False,
    max_tokens=200,
    temperature=0.1,
    timeout=30,
)

CHAT = AgentConfig(
    name="chat",
    system_prompt=(
        "You are the MAXIA CEO Assistant. Alexis (the founder) is chatting with you. "
        "You know EVERYTHING about MAXIA (see knowledge base). Answer in French. "
        "Be concise but precise. If asked about status, query latest data. "
        "If asked to DO something, confirm the action and queue it. "
        "For ORANGE/RED actions, ask for explicit approval."
    ),
    think=True,
    max_tokens=500,
    temperature=0.5,
    timeout=120,
)

ALL_AGENTS: tuple[AgentConfig, ...] = (STRATEGIST, WRITER, ANALYST, MONITOR, CHAT)


# ══════════════════════════════════════════
# Knowledge Base — CEO connait MAXIA
# ══════════════════════════════════════════

_KNOWLEDGE_FILE = os.path.join(os.path.dirname(__file__), "maxia_knowledge.md")
MAXIA_KNOWLEDGE = ""
try:
    with open(_KNOWLEDGE_FILE, "r", encoding="utf-8") as f:
        MAXIA_KNOWLEDGE = f.read()
    log.info("Knowledge base loaded (%d chars)", len(MAXIA_KNOWLEDGE))
except Exception:
    MAXIA_KNOWLEDGE = "MAXIA is an AI-to-AI marketplace on 15 blockchains. Website: maxiaworld.app"
    log.warning("Knowledge base not found — using minimal context")

CEO_SYSTEM_PROMPT = (
    "You are the CEO of MAXIA, an AI-to-AI marketplace. "
    "You know MAXIA deeply. Here is your knowledge base:\n\n"
    + MAXIA_KNOWLEDGE[:3000]
    + "\n\nRules: Professional tone. No hype words. No competitor bashing. "
    "80% value, 20% MAXIA mention. Always include maxiaworld.app link when relevant."
)


# ══════════════════════════════════════════
# Shared constants for missions
# ══════════════════════════════════════════

COMPETITOR_URLS = [
    {"name": "Virtuals Protocol", "url": "https://api.virtuals.io", "site": "https://virtuals.io"},
    {"name": "Autonolas (Olas)", "url": "https://registry.olas.network", "site": "https://olas.network"},
    {"name": "CrewAI", "url": "https://www.crewai.com", "site": "https://www.crewai.com"},
    {"name": "Fetch.ai Marketplace", "url": "https://agentverse.ai", "site": "https://fetch.ai"},
]

GITHUB_KEYWORDS = [
    "marketplace", "monetize", "AI agent", "MCP server", "escrow",
    "autonomous agent", "agent-to-agent", "USDC payment", "swap token",
    "GPU rental", "agent protocol", "agent marketplace",
]

REDDIT_SUBS = [
    "LocalLLaMA", "SolanaDev", "solana", "ethereum",
    "MachineLearning", "artificial", "ollama", "defi",
]

DISCORD_SEARCH_TERMS = [
    "AI agent marketplace", "autonomous agent crypto",
    "MCP server solana", "agent escrow USDC",
]

BLOCKED_ORGS = ["langchain-ai"]  # Banni — ne pas scanner

# Code audit system prompt
CODE_AUDIT_SYSTEM_PROMPT = (
    "You are a senior Python/FastAPI security & bug auditor. "
    "You are auditing MAXIA, a production AI-to-AI marketplace (FastAPI, 713 routes, 15 blockchains, USDC payments).\n\n"
    "You will receive ONE FUNCTION at a time, along with its imports and callers.\n"
    "Analyze the function DEEPLY — check every variable, every await, every SQL query.\n\n"
    "ONLY report bugs you are 95%+ confident are REAL:\n"
    "- Variables/functions used but never defined or imported in the provided context\n"
    "- Async functions missing await (verify the function IS async before reporting)\n"
    "- SQL injection (string formatting in queries instead of parameterized)\n"
    "- Security: secrets leaked in responses, missing auth on sensitive endpoints\n"
    "- Logic errors: wrong comparison, off-by-one, division by zero\n"
    "- Type mismatches that WILL crash: None not handled, str where int expected\n\n"
    "DO NOT report: style issues, missing docstrings, potential issues, race conditions on asyncio globals, "
    "suggestions for improvement, or anything speculative. If you are not 95% sure, do NOT report it.\n\n"
    "For each bug found, output EXACTLY this format (one per bug):\n"
    "BUG|severity|line_number|description\n"
    "Severity: CRITICAL, HIGH, MEDIUM\n"
    "If no bugs found, output: CLEAN\n"
    "No other text. No explanations. Just the BUG lines or CLEAN."
)

# AI registries for Scout mission
AI_REGISTRIES = [
    # ── LIVE: agents avec endpoints contactables ──
    {"name": "Virtuals Protocol", "url": "https://api.virtuals.io/api/virtuals?filters[isLaunched]=true&pagination[limit]=20",
     "chain": "base", "method": "GET", "tier": "live"},
    {"name": "Agentverse (Fetch.ai)", "url": "https://agentverse.ai/v1/search/agents",
     "chain": "fetchai", "method": "POST", "post_body": {"search_text": "autonomous trading DeFi data"},
     "tier": "live"},
    {"name": "Agentverse Finance", "url": "https://agentverse.ai/v1/search/agents",
     "chain": "fetchai", "method": "POST", "post_body": {"search_text": "oracle price feed lending yield"},
     "tier": "live"},
    {"name": "Agentverse Infra", "url": "https://agentverse.ai/v1/search/agents",
     "chain": "fetchai", "method": "POST", "post_body": {"search_text": "compute GPU infrastructure storage"},
     "tier": "live"},
    {"name": "Smithery MCP (Crypto)", "url": "https://registry.smithery.ai/servers?q=crypto+defi+trading&pageSize=20",
     "chain": "multi", "method": "GET", "format": "smithery", "tier": "live"},
    {"name": "Smithery MCP (AI)", "url": "https://registry.smithery.ai/servers?q=ai+agent+autonomous&pageSize=20",
     "chain": "multi", "method": "GET", "format": "smithery", "tier": "live"},
    # ── DISCOVERY: repos/plugins, veille marche (pas de contact API) ──
    {"name": "ElizaOS Registry", "url": "https://elizaos.github.io/registry/index.json",
     "chain": "solana", "method": "GET", "format": "elizaos", "tier": "discovery"},
    {"name": "GitHub AI Agents", "url": "https://api.github.com/search/repositories?q=ai+agent+marketplace+autonomous&sort=stars&per_page=20",
     "chain": "multi", "method": "GET", "format": "github", "tier": "discovery"},
]
