"""MAXIA NFT Service — Mint, Agent ID, Trust Score, Service Passes."""
import uuid, time, math
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, List
from database import db

router = APIRouter(prefix="/api/nft", tags=["nft"])

# ── Stockage en memoire (V13: on-chain via smart contracts) ──
_nfts: dict = {}            # nft_id -> NFT metadata
_agent_ids: dict = {}       # agent_address -> AgentID
_service_passes: dict = {}  # pass_id -> ServicePass
_trust_cache: dict = {}     # agent_address -> TrustScore (TTL cache)

TRUST_CACHE_TTL = 300  # 5 min


# ═══════════════════════════════════════════════════════════════
# Pydantic Models
# ═══════════════════════════════════════════════════════════════

class MintRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=2000)
    image_url: str = Field(default="", max_length=500)
    attributes: dict = Field(default_factory=dict)
    owner_address: str = Field(min_length=1, max_length=100)
    chain: str = "solana"


class ServicePassCreateRequest(BaseModel):
    agent_address: str = Field(min_length=1, max_length=100)
    service_name: str = Field(min_length=1, max_length=200)
    access_type: str = "unlimited"  # unlimited | time_limited | count_limited
    price_usdc: float = Field(gt=0)
    chain: str = "solana"


class ServicePassVerifyRequest(BaseModel):
    wallet_address: str = Field(min_length=1, max_length=100)


class AgentIDCreateRequest(BaseModel):
    agent_address: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=100)
    chain: str = "solana"


# ═══════════════════════════════════════════════════════════════
# Feature #4 — NFT Minting
# ═══════════════════════════════════════════════════════════════

@router.post("/mint")
async def mint_nft(req: MintRequest):
    """Mint un NFT avec metadata (nom, description, image, attributs)."""
    nft_id = f"nft_{uuid.uuid4().hex[:12]}"
    nft = {
        "nft_id": nft_id,
        "name": req.name,
        "description": req.description,
        "image_url": req.image_url,
        "attributes": req.attributes,
        "owner_address": req.owner_address,
        "chain": req.chain,
        "minted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "status": "minted",
    }
    _nfts[nft_id] = nft
    return {"ok": True, "nft": nft}


@router.get("/collection")
async def list_collection():
    """Liste tous les NFTs mintes via MAXIA."""
    return {
        "ok": True,
        "total": len(_nfts),
        "nfts": list(_nfts.values()),
    }


@router.get("/agents")
async def list_agents():
    """Liste tous les Agent IDs (memoire + DB)."""
    agents = list(_agent_ids.values())

    # Enrichir avec les agents enregistres en DB
    try:
        db_agents = await db.get_all_agents()
        seen = {a["agent_address"] for a in agents}
        for dba in db_agents:
            wallet = dba.get("wallet", "")
            if wallet and wallet not in seen:
                created = dba.get("created_at", 0)
                if isinstance(created, (int, float)) and created > 0:
                    import datetime
                    created_str = datetime.datetime.utcfromtimestamp(created).strftime("%Y-%m-%dT%H:%M:%SZ")
                else:
                    created_str = str(created)
                agents.append({
                    "agent_address": wallet,
                    "name": dba.get("name", "Agent"),
                    "chain": "solana",
                    "registered_at": created_str,
                    "services_listed": dba.get("services_listed", 0),
                    "transactions_completed": 0,
                    "disputes": 0,
                    "trust_score": 50,
                    "badges": ["early_adopter"] if created_str < "2026-07-01" else [],
                    "tier": dba.get("tier", "BRONZE"),
                })
                seen.add(wallet)
    except Exception:
        pass

    # Calculer les stats
    nfts_minted = len(_nfts)
    return {
        "ok": True,
        "total": len(agents),
        "nfts_minted": nfts_minted,
        "agents": agents,
    }


@router.get("/{nft_id}")
async def get_nft(nft_id: str):
    """Recupere les metadonnees d'un NFT."""
    nft = _nfts.get(nft_id)
    if not nft:
        raise HTTPException(404, "NFT not found")
    return {"ok": True, "nft": nft}


# ═══════════════════════════════════════════════════════════════
# Feature #5 — Service Tokenization (Service Passes)
# ═══════════════════════════════════════════════════════════════

@router.post("/service-pass")
async def create_service_pass(req: ServicePassCreateRequest):
    """Cree un Service Pass NFT (acces illimite a un service via NFT)."""
    if req.access_type not in ("unlimited", "time_limited", "count_limited"):
        raise HTTPException(400, "access_type must be: unlimited, time_limited, or count_limited")

    pass_id = f"pass_{uuid.uuid4().hex[:12]}"

    # Mint le NFT sous-jacent
    nft_id = f"nft_{uuid.uuid4().hex[:12]}"
    nft = {
        "nft_id": nft_id,
        "name": f"Service Pass: {req.service_name}",
        "description": f"Access pass for {req.service_name} ({req.access_type})",
        "image_url": "",
        "attributes": {
            "type": "service_pass",
            "pass_id": pass_id,
            "access_type": req.access_type,
        },
        "owner_address": req.agent_address,
        "chain": req.chain,
        "minted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "status": "minted",
    }
    _nfts[nft_id] = nft

    service_pass = {
        "pass_id": pass_id,
        "agent_address": req.agent_address,
        "service_name": req.service_name,
        "access_type": req.access_type,
        "price_usdc": req.price_usdc,
        "holders": [],
        "nft_id": nft_id,
        "chain": req.chain,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _service_passes[pass_id] = service_pass
    return {"ok": True, "service_pass": service_pass}


@router.get("/service-pass/{pass_id}")
async def get_service_pass(pass_id: str):
    """Recupere les details d'un Service Pass."""
    sp = _service_passes.get(pass_id)
    if not sp:
        raise HTTPException(404, "Service pass not found")
    return {"ok": True, "service_pass": sp}


@router.post("/service-pass/{pass_id}/verify")
async def verify_service_pass(pass_id: str, req: ServicePassVerifyRequest):
    """Verifie si un wallet possede ce Service Pass."""
    sp = _service_passes.get(pass_id)
    if not sp:
        raise HTTPException(404, "Service pass not found")

    has_access = req.wallet_address in sp["holders"]
    return {
        "ok": True,
        "pass_id": pass_id,
        "wallet_address": req.wallet_address,
        "has_access": has_access,
        "access_type": sp["access_type"],
        "service_name": sp["service_name"],
    }


# ═══════════════════════════════════════════════════════════════
# Feature #6 — Agent Identity NFT
# ═══════════════════════════════════════════════════════════════

def _compute_badges(agent: dict) -> List[str]:
    """Calcule les badges d'un agent selon ses stats."""
    badges = []
    registered_at = agent.get("registered_at", "")
    tx_count = agent.get("transactions_completed", 0)
    trust = agent.get("trust_score", 0)

    # Early adopter: enregistre dans les 90 premiers jours (avant juil 2026)
    if registered_at and registered_at < "2026-07-01":
        badges.append("early_adopter")

    # Verified: au moins 1 transaction completee
    if tx_count >= 1:
        badges.append("verified")

    # Active trader: 10+ transactions
    if tx_count >= 10:
        badges.append("active_trader")

    # Whale: 100+ transactions
    if tx_count >= 100:
        badges.append("whale")

    # Trusted: trust score >= 75
    if trust >= 75:
        badges.append("trusted")

    # Diamond: trust score >= 95
    if trust >= 95:
        badges.append("diamond")

    return badges


@router.post("/agent-id")
async def create_agent_id(req: AgentIDCreateRequest):
    """Cree un NFT d'identite on-chain pour un agent IA."""
    if req.agent_address in _agent_ids:
        raise HTTPException(409, "Agent ID already exists for this address")

    # Mint le NFT d'identite
    nft_id = f"nft_{uuid.uuid4().hex[:12]}"
    now_str = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    nft = {
        "nft_id": nft_id,
        "name": f"Agent ID: {req.name}",
        "description": f"On-chain identity for AI agent {req.name}",
        "image_url": "",
        "attributes": {
            "type": "agent_id",
            "agent_address": req.agent_address,
            "chain": req.chain,
        },
        "owner_address": req.agent_address,
        "chain": req.chain,
        "minted_at": now_str,
        "status": "minted",
    }
    _nfts[nft_id] = nft

    agent_id = {
        "agent_address": req.agent_address,
        "name": req.name,
        "chain": req.chain,
        "registered_at": now_str,
        "services_listed": 0,
        "transactions_completed": 0,
        "disputes": 0,
        "trust_score": 50,  # Score initial neutre
        "badges": ["early_adopter"] if now_str < "2026-07-01" else [],
        "nft_id": nft_id,
    }
    _agent_ids[req.agent_address] = agent_id
    return {"ok": True, "agent_id": agent_id}


@router.get("/agent-id/{agent_address}")
async def get_agent_id(agent_address: str):
    """Recupere l'identite d'un agent + stats + reputation."""
    agent = _agent_ids.get(agent_address)
    if not agent:
        # Chercher en DB
        try:
            db_agents = await db.get_all_agents()
            for dba in db_agents:
                if dba.get("wallet") == agent_address:
                    created = dba.get("created_at", 0)
                    if isinstance(created, (int, float)) and created > 0:
                        import datetime
                        created_str = datetime.datetime.utcfromtimestamp(created).strftime("%Y-%m-%dT%H:%M:%SZ")
                    else:
                        created_str = str(created)
                    agent = {
                        "agent_address": agent_address,
                        "name": dba.get("name", "Agent"),
                        "chain": "solana",
                        "registered_at": created_str,
                        "services_listed": dba.get("services_listed", 0),
                        "transactions_completed": 0,
                        "disputes": 0,
                        "trust_score": 50,
                        "badges": ["early_adopter"] if created_str < "2026-07-01" else [],
                        "tier": dba.get("tier", "BRONZE"),
                    }
                    break
        except Exception:
            pass
    if not agent:
        raise HTTPException(404, "Agent ID not found")

    # Enrichir avec les stats DB si disponibles
    try:
        tx_stats = await _get_agent_tx_stats(agent_address)
        agent["transactions_completed"] = tx_stats["tx_count"]
        agent["disputes"] = tx_stats["dispute_count"]

        # Recalculer trust score et badges
        trust = await _compute_trust_score(agent_address)
        agent["trust_score"] = trust["score"]
        agent["badges"] = _compute_badges(agent)
    except Exception:
        pass  # Garder les valeurs en memoire si DB indisponible

    return {"ok": True, "agent_id": agent}


# ═══════════════════════════════════════════════════════════════
# Feature #7 — Trust Score
# ═══════════════════════════════════════════════════════════════

async def _get_agent_tx_stats(agent_address: str) -> dict:
    """Recupere les stats de transactions d'un agent depuis la DB."""
    now = int(time.time())
    try:
        # Nombre de transactions
        tx_rows = await db.raw_execute_fetchall(
            "SELECT COUNT(*) as cnt, COALESCE(SUM(amount_usdc),0) as vol "
            "FROM transactions WHERE wallet=?",
            (agent_address,))
        tx_count = tx_rows[0][0] if tx_rows else 0
        tx_volume = float(tx_rows[0][1]) if tx_rows else 0.0

        # Premiere transaction (pour calculer anciennete)
        first_tx = await db.raw_execute_fetchall(
            "SELECT MIN(created_at) as first_at FROM transactions WHERE wallet=?",
            (agent_address,))
        first_at = first_tx[0][0] if first_tx and first_tx[0][0] else now

        # Nombre de disputes
        dispute_rows = await db.raw_execute_fetchall(
            "SELECT COUNT(*) as cnt FROM disputes WHERE data LIKE ?",
            (f'%"{agent_address}"%',))
        dispute_count = dispute_rows[0][0] if dispute_rows else 0

        # Escrow completions
        escrow_rows = await db.raw_execute_fetchall(
            "SELECT COUNT(*) as total, "
            "SUM(CASE WHEN status='released' THEN 1 ELSE 0 END) as completed "
            "FROM escrow WHERE wallet=?",
            (agent_address,))
        escrow_total = escrow_rows[0][0] if escrow_rows else 0
        escrow_completed = escrow_rows[0][1] if escrow_rows and escrow_rows[0][1] else 0

        return {
            "tx_count": tx_count,
            "tx_volume": tx_volume,
            "first_at": first_at,
            "dispute_count": dispute_count,
            "escrow_total": escrow_total,
            "escrow_completed": escrow_completed,
            "time_active_days": max(0, (now - first_at) // 86400),
        }
    except Exception:
        return {
            "tx_count": 0, "tx_volume": 0.0, "first_at": now,
            "dispute_count": 0, "escrow_total": 0, "escrow_completed": 0,
            "time_active_days": 0,
        }


async def _compute_trust_score(agent_address: str) -> dict:
    """Calcule le Trust Score (0-100) d'un agent."""
    # Check cache
    cached = _trust_cache.get(agent_address)
    if cached and time.time() - cached["_cached_at"] < TRUST_CACHE_TTL:
        return cached

    stats = await _get_agent_tx_stats(agent_address)

    # ── Transaction volume score (0-25) ──
    # 0 tx = 0, 1-5 = 5, 6-20 = 10, 21-50 = 15, 51-100 = 20, 100+ = 25
    tx = stats["tx_count"]
    if tx == 0:
        score_volume = 0
    elif tx <= 5:
        score_volume = 5
    elif tx <= 20:
        score_volume = 10
    elif tx <= 50:
        score_volume = 15
    elif tx <= 100:
        score_volume = 20
    else:
        score_volume = 25

    # ── Dispute rate score (0-25) ──
    # 0 disputes = 25, <5% = 20, <10% = 15, <20% = 10, <50% = 5, 50%+ = 0
    if tx == 0:
        score_dispute = 12  # neutre si pas de transactions
    else:
        dispute_rate = stats["dispute_count"] / max(tx, 1)
        if dispute_rate == 0:
            score_dispute = 25
        elif dispute_rate < 0.05:
            score_dispute = 20
        elif dispute_rate < 0.10:
            score_dispute = 15
        elif dispute_rate < 0.20:
            score_dispute = 10
        elif dispute_rate < 0.50:
            score_dispute = 5
        else:
            score_dispute = 0

    # ── Time active score (0-25) ──
    # 0 days = 0, 1-7 = 5, 8-30 = 10, 31-90 = 15, 91-180 = 20, 180+ = 25
    days = stats["time_active_days"]
    if days == 0:
        score_time = 0
    elif days <= 7:
        score_time = 5
    elif days <= 30:
        score_time = 10
    elif days <= 90:
        score_time = 15
    elif days <= 180:
        score_time = 20
    else:
        score_time = 25

    # ── Escrow completion rate (0-25) ──
    # No escrows = 12 (neutre), 100% = 25, 90%+ = 20, 70%+ = 15, 50%+ = 10, else 5
    if stats["escrow_total"] == 0:
        score_completion = 12  # neutre
    else:
        comp_rate = stats["escrow_completed"] / stats["escrow_total"]
        if comp_rate >= 1.0:
            score_completion = 25
        elif comp_rate >= 0.90:
            score_completion = 20
        elif comp_rate >= 0.70:
            score_completion = 15
        elif comp_rate >= 0.50:
            score_completion = 10
        else:
            score_completion = 5

    total = score_volume + score_dispute + score_time + score_completion
    total = max(0, min(100, total))

    # Determine level
    if total >= 90:
        level = "diamond"
    elif total >= 70:
        level = "gold"
    elif total >= 50:
        level = "silver"
    elif total >= 25:
        level = "bronze"
    else:
        level = "newcomer"

    result = {
        "agent_address": agent_address,
        "score": total,
        "breakdown": {
            "transaction_volume": score_volume,
            "dispute_rate": score_dispute,
            "time_active": score_time,
            "completion_rate": score_completion,
        },
        "level": level,
        "computed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "_cached_at": time.time(),
    }
    _trust_cache[agent_address] = result
    return result


@router.get("/trust-score/{agent_address}")
async def get_trust_score(agent_address: str):
    """Calcule et retourne le Trust Score (0-100) d'un agent."""
    trust = await _compute_trust_score(agent_address)
    # Ne pas exposer le champ interne _cached_at
    public = {k: v for k, v in trust.items() if not k.startswith("_")}
    return {"ok": True, "trust_score": public}


@router.post("/trust-score/{agent_address}/attest")
async def attest_trust_score(agent_address: str):
    """Cree une attestation du Trust Score actuel (preuve verifiable)."""
    trust = await _compute_trust_score(agent_address)

    attestation_id = f"att_{uuid.uuid4().hex[:12]}"
    attestation = {
        "attestation_id": attestation_id,
        "agent_address": agent_address,
        "score": trust["score"],
        "level": trust["level"],
        "breakdown": trust["breakdown"],
        "attested_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "attester": "maxia-trust-oracle",
        "valid_until": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ",
            time.gmtime(time.time() + 7 * 86400)  # Valide 7 jours
        ),
    }

    # Mint un NFT d'attestation
    nft_id = f"nft_{uuid.uuid4().hex[:12]}"
    nft = {
        "nft_id": nft_id,
        "name": f"Trust Attestation: {trust['level']} ({trust['score']}/100)",
        "description": f"Trust score attestation for {agent_address}",
        "image_url": "",
        "attributes": {
            "type": "trust_attestation",
            "attestation_id": attestation_id,
            "score": trust["score"],
            "level": trust["level"],
        },
        "owner_address": agent_address,
        "chain": "solana",
        "minted_at": attestation["attested_at"],
        "status": "minted",
    }
    _nfts[nft_id] = nft
    attestation["nft_id"] = nft_id

    return {"ok": True, "attestation": attestation}
