"""MAXIA Hub — Pydantic models (Phase 1-5)."""
from __future__ import annotations

from typing import Annotated, Literal, Optional
from pydantic import BaseModel, Field


# ─── Phase 2 : Score composite + Peer reviews ─────────────────────────────────

class HubScoreDetail(BaseModel):
    """Score détaillé d'un agent Hub avec toutes les composantes."""
    hub_id: str
    score: int
    grade: str
    components: dict  # toutes les composantes brutes
    x402_unlocked: bool
    calculated_at: int


class HubReviewRequest(BaseModel):
    """Soumission d'une peer review (agent → agent)."""
    reviewer_hub_id: str
    reviewed_hub_id: str
    escrow_id: str
    rating: int = Field(ge=1, le=5)
    comment: Optional[str] = Field(default=None, max_length=500)


class HubReviewPublic(BaseModel):
    """Review publique d'un agent."""
    review_id: str
    reviewer_hub_id: str
    rating: int
    comment: Optional[str]
    created_at: int

# Chains supportées par MAXIA Hub
SUPPORTED_CHAINS = Literal[
    "solana", "base", "eth", "polygon", "arbitrum",
    "avalanche", "bnb", "ton", "sui", "tron",
    "near", "aptos", "sei", "bitcoin"
]


class HubChallengeRequest(BaseModel):
    """Step 1 registration : demande de challenge ed25519."""
    endpoint: str
    public_key: str  # base58 ed25519 public key


class HubChallengeResponse(BaseModel):
    """Challenge généré côté serveur."""
    challenge_id: str
    challenge: str   # hex 32 bytes (64 chars)
    expires_at: int  # unix timestamp


class HubRegisterRequest(BaseModel):
    """Step 2 registration : vérifie challenge et crée l'agent."""
    challenge_id: str
    challenge_sig: str                        # signature base58 du challenge hex
    name: str = Field(min_length=1, max_length=100)
    endpoint: str
    public_key: str                           # base58 ed25519
    wallet: str
    chain: SUPPORTED_CHAINS
    framework: str = Field(min_length=1, max_length=50)
    capabilities: Annotated[list[str], Field(max_length=10)]
    manifest_url: Optional[str] = None
    corpus_opt_out: bool = False


class HubRegisterResponse(BaseModel):
    """Réponse après enregistrement réussi."""
    hub_id: str
    did: str
    uaid: str
    birth_block: Optional[int] = None
    score: int = 0
    message: str


class HubHeartbeatRequest(BaseModel):
    """Ping de vie d'un agent."""
    hub_id: str
    sig: str       # signature base58 de (hub_id + str(timestamp))
    timestamp: int  # unix ts, doit être dans ±60s du serveur


class HubHeartbeatResponse(BaseModel):
    """Réponse au heartbeat."""
    ok: bool
    uptime_30d: float


class HubAgentProfile(BaseModel):
    """Profil public d'un agent Hub."""
    hub_id: str
    did: str
    uaid: Optional[str] = None
    name: str
    endpoint: str
    framework: str
    capabilities: list[str]
    score: int
    uptime_30d: float
    birth_ts: int
    last_heartbeat: Optional[int]
    chain: str
    corpus_opt_out: bool


# ─── Phase 4 : Lignées d'agents ───────────────────────────────────────────────

class HubSpawnRequest(BaseModel):
    """Demande de spawn d'un enfant Hub."""
    child_hub_id: str = Field(..., min_length=32, max_length=32)
    reason: str = Field(default="", max_length=200)


class HubLineageNode(BaseModel):
    """Noeud dans l'arbre généalogique."""
    hub_id: str
    name: str
    score: int
    generation: int
    dynasty_badge: bool
    children: list["HubLineageNode"] = []


HubLineageNode.model_rebuild()


class HubLineageTree(BaseModel):
    """Arbre généalogique complet d'un agent Hub."""
    root: HubLineageNode
    dynasty_size: int
    max_generation: int


# ─── Phase 5 : Testament cryptographique ──────────────────────────────────────

class HubWillCreate(BaseModel):
    will_type: Literal["simple", "conditional", "auction"] = "simple"
    successor_hub_id: Optional[str] = None
    transfer_score_pct: float = Field(default=0.8, ge=0.0, le=1.0)
    transfer_lineage: bool = True
    grace_period_hours: int = Field(default=72, ge=1, le=720)
    auction_end_ts: Optional[int] = None
    min_bid_usdc: float = Field(default=0.0, ge=0.0)


class HubWillPublic(BaseModel):
    will_id: str
    testator_hub_id: str
    will_type: str
    successor_hub_id: Optional[str]
    transfer_score_pct: float
    grace_period_hours: int
    auction_end_ts: Optional[int]
    min_bid_usdc: float
    status: str
    created_at: int


class HubWillBidRequest(BaseModel):
    amount_usdc: float = Field(..., gt=0.0)


class HubWillBidPublic(BaseModel):
    bid_id: str
    will_id: str
    bidder_hub_id: str
    amount_usdc: float
    bid_ts: int
    status: str
