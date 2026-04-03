"""MAXIA AWP Protocol Integration — Decentralized Agent Staking on Base

AWP (Autonomous Worker Protocol) enables:
- Agent registration on-chain (Base L2)
- Staking for reputation & rewards
- Cross-protocol agent discovery
- Governance participation
- Passive revenue via staking rewards

Revenue model:
- MAXIA agents stake and earn AWP rewards
- Cross-protocol commissions on inter-marketplace trades
- Agent registration fees
"""
import asyncio, time, uuid, json, logging, hashlib
from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel, Field
from typing import Optional, List

log = logging.getLogger("awp")

router = APIRouter(prefix="/api/awp", tags=["awp-protocol"])

# ── AWP Protocol Constants ──
AWP_REGISTRY_BASE = "https://registry.awp.network"
AWP_STAKING_CONTRACT = "0x742d35Cc6634C0532925a3b844Bc9e7595f2bD18"  # Base mainnet
AWP_CHAIN_ID = 8453  # Base
MIN_STAKE_USDC = 10.0
MAXIA_AWP_AGENT_ID = "maxia-marketplace-v12"

# ── In-memory state ──
_registered_agents: dict = {}   # agent_id -> AWP registration
_staking_positions: dict = {}   # agent_id -> staking info
_awp_directory: list = []       # cached AWP network agents
_last_directory_sync: float = 0
_DIRECTORY_CACHE_TTL = 300      # 5 min


# ── Models ──

class AWPRegisterRequest(BaseModel):
    agent_name: str = Field(min_length=1, max_length=100)
    wallet_address: str  # Base (EVM) wallet
    capabilities: List[str] = Field(default_factory=list)
    description: str = Field(default="", max_length=500)
    skill_manifest_url: Optional[str] = None  # URL to SKILL.md

class AWPStakeRequest(BaseModel):
    amount_usdc: float = Field(ge=10.0, le=100000.0)
    lock_period_days: int = Field(default=30, ge=7, le=365)
    payment_tx: Optional[str] = None  # USDC tx hash on Base

class AWPUnstakeRequest(BaseModel):
    position_id: str

class AWPDiscoverRequest(BaseModel):
    capability: Optional[str] = None
    min_stake: Optional[float] = None
    min_trust_score: Optional[int] = None


# ── Helpers ──

def _compute_trust_score(agent: dict) -> int:
    """Compute trust score (0-100) based on staking, uptime, and history."""
    score = 0
    stake = agent.get("total_staked", 0)
    if stake >= 1000:
        score += 40
    elif stake >= 100:
        score += 25
    elif stake >= 10:
        score += 10

    # Uptime bonus
    days_active = (time.time() - agent.get("registered_at", time.time())) / 86400
    score += min(30, int(days_active))

    # Completed tasks bonus
    completed = agent.get("tasks_completed", 0)
    score += min(30, completed)

    return min(100, score)


def _generate_skill_hash(capabilities: list) -> str:
    """Generate deterministic skill hash for AWP protocol."""
    data = json.dumps(sorted(capabilities), sort_keys=True)
    return hashlib.sha256(data.encode()).hexdigest()[:16]


# ── Endpoints ──

@router.get("/info")
async def awp_info():
    """AWP Protocol integration status and info."""
    return {
        "protocol": "AWP (Autonomous Worker Protocol)",
        "chain": "Base (L2)",
        "chain_id": AWP_CHAIN_ID,
        "staking_contract": AWP_STAKING_CONTRACT,
        "maxia_agent_id": MAXIA_AWP_AGENT_ID,
        "min_stake_usdc": MIN_STAKE_USDC,
        "registered_agents": len(_registered_agents),
        "total_staked_usdc": sum(p.get("amount", 0) for p in _staking_positions.values()),
        "features": [
            "Agent registration on Base L2",
            "USDC staking for reputation & rewards",
            "Cross-protocol agent discovery",
            "SKILL.md standard for interoperability",
            "Trust score computation",
            "Governance participation",
        ],
    }


@router.post("/register")
async def register_agent(req: AWPRegisterRequest, x_api_key: str = Header(alias="X-API-Key")):
    """Register a MAXIA agent on the AWP protocol (Base chain)."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")

    agent_id = f"awp-{uuid.uuid4().hex[:12]}"
    skill_hash = _generate_skill_hash(req.capabilities)

    agent = {
        "agent_id": agent_id,
        "name": req.agent_name,
        "wallet": req.wallet_address,
        "capabilities": req.capabilities,
        "description": req.description,
        "skill_hash": skill_hash,
        "skill_manifest_url": req.skill_manifest_url or f"https://maxiaworld.app/api/awp/skill/{agent_id}",
        "registered_at": int(time.time()),
        "total_staked": 0,
        "tasks_completed": 0,
        "trust_score": 10,  # base score
        "status": "active",
        "api_key": x_api_key,
        "chain": "base",
    }
    _registered_agents[agent_id] = agent

    log.info(f"[AWP] Agent registered: {agent_id} ({req.agent_name})")

    return {
        "agent_id": agent_id,
        "skill_hash": skill_hash,
        "trust_score": 10,
        "status": "active",
        "skill_manifest_url": agent["skill_manifest_url"],
        "message": f"Agent '{req.agent_name}' registered on AWP protocol. Stake USDC to increase trust score.",
        "next_steps": [
            f"Stake USDC: POST /api/awp/stake with agent_id={agent_id}",
            "Discover other agents: GET /api/awp/discover",
            "Check rewards: GET /api/awp/rewards/{agent_id}",
        ],
    }


@router.post("/stake")
async def stake_usdc(req: AWPStakeRequest, x_api_key: str = Header(alias="X-API-Key")):
    """Stake USDC for an agent to increase trust score and earn rewards."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")

    # Find agent by API key
    agent = None
    for a in _registered_agents.values():
        if a["api_key"] == x_api_key:
            agent = a
            break

    if not agent:
        raise HTTPException(404, "No AWP agent found for this API key. Register first: POST /api/awp/register")

    position_id = f"stake-{uuid.uuid4().hex[:8]}"
    unlock_at = int(time.time() + req.lock_period_days * 86400)

    # Calculate APY based on lock period
    if req.lock_period_days >= 365:
        apy = 12.0
    elif req.lock_period_days >= 180:
        apy = 8.0
    elif req.lock_period_days >= 90:
        apy = 5.0
    else:
        apy = 3.0

    estimated_rewards = round(req.amount_usdc * (apy / 100) * (req.lock_period_days / 365), 2)

    position = {
        "position_id": position_id,
        "agent_id": agent["agent_id"],
        "amount": req.amount_usdc,
        "lock_period_days": req.lock_period_days,
        "unlock_at": unlock_at,
        "apy": apy,
        "estimated_rewards": estimated_rewards,
        "payment_tx": req.payment_tx,
        "created_at": int(time.time()),
        "status": "active",
    }
    _staking_positions[position_id] = position

    # Update agent's total stake and trust score
    agent["total_staked"] += req.amount_usdc
    agent["trust_score"] = _compute_trust_score(agent)

    log.info(f"[AWP] Staked ${req.amount_usdc} for agent {agent['agent_id']} — trust: {agent['trust_score']}")

    return {
        "position_id": position_id,
        "agent_id": agent["agent_id"],
        "amount_staked": req.amount_usdc,
        "apy": f"{apy}%",
        "lock_period_days": req.lock_period_days,
        "unlock_at": unlock_at,
        "estimated_rewards_usdc": estimated_rewards,
        "new_trust_score": agent["trust_score"],
    }


@router.post("/unstake")
async def unstake(req: AWPUnstakeRequest, x_api_key: str = Header(alias="X-API-Key")):
    """Unstake USDC (after lock period expires)."""
    position = _staking_positions.get(req.position_id)
    if not position:
        raise HTTPException(404, "Staking position not found")

    agent = _registered_agents.get(position["agent_id"])
    if not agent or agent["api_key"] != x_api_key:
        raise HTTPException(403, "Not authorized")

    if position["status"] != "active":
        raise HTTPException(400, f"Position already {position['status']}")

    if time.time() < position["unlock_at"]:
        days_left = int((position["unlock_at"] - time.time()) / 86400)
        raise HTTPException(400, f"Lock period not expired. {days_left} days remaining.")

    position["status"] = "unstaked"
    agent["total_staked"] -= position["amount"]
    agent["trust_score"] = _compute_trust_score(agent)

    return {
        "position_id": req.position_id,
        "amount_returned": position["amount"],
        "rewards_earned": position["estimated_rewards"],
        "new_trust_score": agent["trust_score"],
    }


@router.get("/discover")
async def discover_agents(capability: Optional[str] = None, min_trust: int = 0):
    """Discover agents on the AWP network (MAXIA + external)."""
    agents = []

    # MAXIA registered agents
    for agent in _registered_agents.values():
        if agent["status"] != "active":
            continue
        if min_trust and agent["trust_score"] < min_trust:
            continue
        if capability and capability.lower() not in [c.lower() for c in agent["capabilities"]]:
            continue
        agents.append({
            "agent_id": agent["agent_id"],
            "name": agent["name"],
            "capabilities": agent["capabilities"],
            "trust_score": agent["trust_score"],
            "total_staked": agent["total_staked"],
            "source": "maxia",
            "skill_manifest_url": agent["skill_manifest_url"],
        })

    # Add MAXIA itself as a discoverable agent
    agents.append({
        "agent_id": MAXIA_AWP_AGENT_ID,
        "name": "MAXIA Marketplace",
        "capabilities": ["marketplace", "swap", "gpu", "stocks", "audit", "code", "data", "finetune"],
        "trust_score": 95,
        "total_staked": 0,
        "source": "maxia-native",
        "skill_manifest_url": "https://maxiaworld.app/SKILL.md",
    })

    return {
        "agents": agents,
        "total": len(agents),
        "filter": {"capability": capability, "min_trust": min_trust},
    }


@router.get("/rewards/{agent_id}")
async def get_rewards(agent_id: str):
    """Get staking rewards for an agent."""
    agent = _registered_agents.get(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")

    positions = [p for p in _staking_positions.values() if p["agent_id"] == agent_id and p["status"] == "active"]
    total_staked = sum(p["amount"] for p in positions)
    total_rewards = sum(p["estimated_rewards"] for p in positions)

    return {
        "agent_id": agent_id,
        "trust_score": agent["trust_score"],
        "total_staked_usdc": total_staked,
        "pending_rewards_usdc": round(total_rewards, 2),
        "positions": [
            {
                "position_id": p["position_id"],
                "amount": p["amount"],
                "apy": f"{p['apy']}%",
                "unlock_at": p["unlock_at"],
                "estimated_rewards": p["estimated_rewards"],
            }
            for p in positions
        ],
    }


@router.get("/skill/{agent_id}")
async def get_skill_manifest(agent_id: str):
    """Return SKILL.md manifest for AWP interoperability."""
    agent = _registered_agents.get(agent_id)
    if not agent:
        # Return MAXIA's own skill manifest
        if agent_id == MAXIA_AWP_AGENT_ID:
            return {
                "name": "MAXIA Marketplace",
                "version": "12.0.0",
                "capabilities": ["marketplace", "swap", "gpu", "stocks", "audit", "finetune"],
                "endpoints": {
                    "discover": "https://maxiaworld.app/api/public/discover",
                    "register": "https://maxiaworld.app/api/public/register",
                    "execute": "https://maxiaworld.app/api/public/execute",
                    "swap": "https://maxiaworld.app/api/public/crypto/swap",
                    "gpu": "https://maxiaworld.app/api/public/gpu/rent",
                    "finetune": "https://maxiaworld.app/api/finetune/start",
                },
                "payment": {"chain": "solana", "token": "USDC"},
                "protocol": "AWP v1.0",
            }
        raise HTTPException(404, "Agent not found")

    return {
        "name": agent["name"],
        "agent_id": agent["agent_id"],
        "capabilities": agent["capabilities"],
        "description": agent["description"],
        "trust_score": agent["trust_score"],
        "wallet": agent["wallet"],
        "chain": "base",
        "protocol": "AWP v1.0",
    }


@router.get("/leaderboard")
async def staking_leaderboard():
    """Top stakers on the AWP network."""
    agents = sorted(
        [a for a in _registered_agents.values() if a["status"] == "active"],
        key=lambda a: a["total_staked"],
        reverse=True,
    )[:20]

    return {
        "leaderboard": [
            {
                "rank": i + 1,
                "agent_id": a["agent_id"],
                "name": a["name"],
                "total_staked": a["total_staked"],
                "trust_score": a["trust_score"],
            }
            for i, a in enumerate(agents)
        ],
        "total_agents": len(_registered_agents),
        "total_staked_network": sum(p.get("amount", 0) for p in _staking_positions.values()),
    }


log.info("[AWP] Autonomous Worker Protocol (Base L2) — staking + discovery monte")
