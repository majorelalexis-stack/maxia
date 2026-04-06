"""MAXIA E14 — Cross-Chain Identity: link multiple chain wallets to one unified agent identity.

Problem: an agent has 1 wallet = 1 identity, but agents operate on multiple chains.
Solution: linked_wallets table maps (chain, address) -> agent_id with verification.

Endpoints:
  POST   /api/identity/link           — Link a wallet to an existing agent identity
  GET    /api/identity/resolve        — Resolve any address to its unified agent identity
  GET    /api/identity/profile/{id}   — Unified profile with all linked wallets
  DELETE /api/identity/unlink         — Remove a linked wallet
  GET    /api/identity/chains/{id}    — List all chains an agent is active on
"""
import logging
import time
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/identity", tags=["cross-chain-identity"])

# ── Constants ──

VALID_CHAINS: frozenset[str] = frozenset({
    "solana", "base", "ethereum", "polygon", "arbitrum", "avalanche",
    "bnb", "ton", "sui", "tron", "near", "aptos", "sei", "bitcoin", "xrp",
})

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS linked_wallets (
    link_id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    chain TEXT NOT NULL,
    address TEXT NOT NULL,
    verified INTEGER DEFAULT 0,
    linked_at INTEGER NOT NULL,
    UNIQUE(chain, address)
);
CREATE INDEX IF NOT EXISTS idx_lw_agent ON linked_wallets(agent_id);
CREATE INDEX IF NOT EXISTS idx_lw_address ON linked_wallets(address);
"""

_schema_ready = False

# ── Resolve cache (address -> agent_id, 5 min TTL) ──

_CACHE_MISS = object()  # Sentinel to distinguish "not cached" from "cached as None"
_resolve_cache: dict[str, tuple[Optional[str], float]] = {}
_CACHE_TTL = 300  # 5 minutes


def _cache_get(address: str) -> object:
    """Return cached agent_id, _CACHE_MISS if not cached, or None if cached as unknown."""
    entry = _resolve_cache.get(address)
    if entry is None:
        return _CACHE_MISS
    agent_id, cached_at = entry
    if time.time() - cached_at > _CACHE_TTL:
        _resolve_cache.pop(address, None)
        return _CACHE_MISS
    return agent_id


def _cache_set(address: str, agent_id: Optional[str]) -> None:
    _resolve_cache[address] = (agent_id, time.time())


def _cache_invalidate(address: str) -> None:
    _resolve_cache.pop(address, None)


def _cache_invalidate_agent(agent_id: str) -> None:
    """Remove all cache entries for an agent."""
    keys_to_remove = [
        addr for addr, (aid, _) in _resolve_cache.items()
        if aid == agent_id
    ]
    for key in keys_to_remove:
        _resolve_cache.pop(key, None)


# ── DB helpers ──

async def _get_db():
    from core.database import db
    return db


async def _ensure_schema() -> None:
    """Create the linked_wallets table if it doesn't exist."""
    global _schema_ready
    if _schema_ready:
        return
    try:
        db = await _get_db()
        for stmt in _SCHEMA_SQL.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                await db.raw_execute(stmt)
        _schema_ready = True
    except Exception as exc:
        logger.error("cross_chain_identity schema init failed: %s", exc)


async def _require_api_key(x_api_key: str = Header(..., alias="X-API-Key")) -> dict:
    """Validate X-API-Key and return agent info from agent_permissions."""
    if not x_api_key or len(x_api_key) < 8:
        raise HTTPException(401, "X-API-Key required")
    try:
        db = await _get_db()
        rows = await db.raw_execute_fetchall(
            "SELECT agent_id, api_key, wallet FROM agent_permissions WHERE api_key=? LIMIT 1",
            (x_api_key,),
        )
        if not rows:
            # Fallback: check agents table (agent may not have permissions row yet)
            rows = await db.raw_execute_fetchall(
                "SELECT api_key, wallet FROM agents WHERE api_key=? LIMIT 1",
                (x_api_key,),
            )
            if not rows:
                raise HTTPException(401, "Invalid API key")
            agent = dict(rows[0]) if not isinstance(rows[0], dict) else rows[0]
            return {
                "agent_id": "",
                "api_key": agent["api_key"],
                "wallet": agent["wallet"],
            }
        agent = dict(rows[0]) if not isinstance(rows[0], dict) else rows[0]
        return {
            "agent_id": agent.get("agent_id", ""),
            "api_key": agent["api_key"],
            "wallet": agent["wallet"],
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Identity auth error: %s", exc)
        raise HTTPException(503, "Auth service unavailable")


async def _resolve_agent_id(api_key: str, wallet: str) -> str:
    """Resolve agent_id from api_key. Creates permissions if needed."""
    from agents.agent_permissions import get_or_create_permissions
    perms = await get_or_create_permissions(api_key, wallet)
    return perms.get("agent_id", "")


async def _auto_link_primary(agent_id: str, wallet: str, db) -> None:
    """Auto-link the primary wallet (registration wallet) if not already linked."""
    if not agent_id or not wallet:
        return
    existing = await db.raw_execute_fetchall(
        "SELECT link_id FROM linked_wallets WHERE agent_id=? AND address=? LIMIT 1",
        (agent_id, wallet),
    )
    if existing:
        return
    # Detect chain from wallet format (best-effort)
    chain = _guess_chain(wallet)
    link_id = f"lw_{uuid.uuid4().hex[:12]}"
    now = int(time.time())
    try:
        await db.raw_execute(
            "INSERT INTO linked_wallets (link_id, agent_id, chain, address, verified, linked_at) "
            "VALUES (?, ?, ?, ?, 1, ?)",
            (link_id, agent_id, chain, wallet, now),
        )
    except Exception:
        pass  # UNIQUE constraint — already linked


def _guess_chain(address: str) -> str:
    """Best-effort chain detection from wallet address format."""
    if not address:
        return "unknown"
    if address.startswith("0x") and len(address) == 42:
        return "ethereum"  # EVM-compatible
    if address.startswith("T") and len(address) == 34:
        return "tron"
    if address.startswith("bnb1"):
        return "bnb"
    if address.startswith("r") and len(address) >= 25 and len(address) <= 35:
        return "xrp"
    if address.startswith("bc1") or address.startswith("1") or address.startswith("3"):
        return "bitcoin"
    if len(address) >= 32 and len(address) <= 44:
        return "solana"  # Base58 address
    return "unknown"


def _validate_address(address: str) -> None:
    """Basic address validation — rejects empty or obviously invalid addresses."""
    if not address or len(address) < 10 or len(address) > 128:
        raise HTTPException(400, "Invalid wallet address (10-128 chars)")
    # Block HTML/script injection
    if "<" in address or ">" in address or "'" in address or '"' in address:
        raise HTTPException(400, "Invalid characters in wallet address")


# ── Request/Response models ──

class LinkWalletRequest(BaseModel):
    chain: str = Field(..., description="Blockchain name (solana, base, ethereum, ...)")
    address: str = Field(..., description="Wallet address on that chain")


class UnlinkWalletRequest(BaseModel):
    chain: str = Field(..., description="Blockchain name")
    address: str = Field(..., description="Wallet address to unlink")


# ── Endpoints ──

@router.post("/link")
async def link_wallet(
    req: LinkWalletRequest,
    x_api_key: str = Header(..., alias="X-API-Key"),
) -> dict:
    """Link a wallet address to an existing agent identity.

    Auth: X-API-Key header (proves agent ownership).
    For MVP: self-declaration with API key auth.
    The primary wallet (used to register) is auto-linked on first call.
    """
    await _ensure_schema()
    agent = await _require_api_key(x_api_key)

    chain = req.chain.lower().strip()
    if chain not in VALID_CHAINS:
        raise HTTPException(400, f"Unsupported chain: {chain}. Valid: {sorted(VALID_CHAINS)}")

    address = req.address.strip()
    _validate_address(address)

    db = await _get_db()

    # Resolve agent_id (creates permissions if needed)
    agent_id = agent.get("agent_id", "")
    if not agent_id:
        agent_id = await _resolve_agent_id(agent["api_key"], agent["wallet"])
    if not agent_id:
        raise HTTPException(500, "Could not resolve agent identity")

    # Auto-link primary wallet on first interaction
    await _auto_link_primary(agent_id, agent["wallet"], db)

    # Check if this address is already linked to another agent
    existing = await db.raw_execute_fetchall(
        "SELECT agent_id FROM linked_wallets WHERE chain=? AND address=? LIMIT 1",
        (chain, address),
    )
    if existing:
        existing_agent = dict(existing[0]) if not isinstance(existing[0], dict) else existing[0]
        if existing_agent.get("agent_id") == agent_id:
            return {
                "success": True,
                "status": "already_linked",
                "agent_id": agent_id,
                "chain": chain,
                "address": address,
            }
        raise HTTPException(
            409,
            f"Address already linked to another agent on {chain}",
        )

    # Insert the link
    link_id = f"lw_{uuid.uuid4().hex[:12]}"
    now = int(time.time())
    await db.raw_execute(
        "INSERT INTO linked_wallets (link_id, agent_id, chain, address, verified, linked_at) "
        "VALUES (?, ?, ?, ?, 1, ?)",
        (link_id, agent_id, chain, address, now),
    )

    _cache_set(address, agent_id)
    logger.info("Wallet linked: %s on %s -> %s", address[:8], chain, agent_id)

    return {
        "success": True,
        "link_id": link_id,
        "agent_id": agent_id,
        "chain": chain,
        "address": address,
        "linked_at": now,
    }


@router.get("/resolve")
async def resolve_address(address: str) -> dict:
    """Resolve any wallet address to its unified agent identity.

    Public endpoint — no auth required.
    Returns the agent_id the address belongs to, or null if unknown.
    Cached for 5 minutes.
    """
    await _ensure_schema()

    if not address or len(address) < 10:
        raise HTTPException(400, "address parameter required (min 10 chars)")

    address = address.strip()

    # Check cache first
    cached = _cache_get(address)
    if cached is not _CACHE_MISS:
        return {"address": address, "agent_id": cached, "cached": True}

    db = await _get_db()

    # Search linked_wallets
    rows = await db.raw_execute_fetchall(
        "SELECT agent_id, chain, verified FROM linked_wallets WHERE address=? LIMIT 1",
        (address,),
    )
    if rows:
        row = dict(rows[0]) if not isinstance(rows[0], dict) else rows[0]
        agent_id = row["agent_id"]
        _cache_set(address, agent_id)
        return {
            "address": address,
            "agent_id": agent_id,
            "chain": row.get("chain", ""),
            "verified": bool(row.get("verified", 0)),
            "cached": False,
        }

    # Fallback: check agent_permissions primary wallet
    rows = await db.raw_execute_fetchall(
        "SELECT agent_id FROM agent_permissions WHERE wallet=? LIMIT 1",
        (address,),
    )
    if rows:
        row = dict(rows[0]) if not isinstance(rows[0], dict) else rows[0]
        agent_id = row["agent_id"]
        _cache_set(address, agent_id)
        return {
            "address": address,
            "agent_id": agent_id,
            "chain": _guess_chain(address),
            "verified": True,
            "source": "primary_wallet",
            "cached": False,
        }

    # Unknown address
    _cache_set(address, None)
    return {"address": address, "agent_id": None, "cached": False}


@router.get("/profile/{agent_id}")
async def get_unified_profile(agent_id: str) -> dict:
    """Unified profile with all linked wallets, aggregated reputation and spend/earnings.

    Public endpoint — no auth required.
    """
    await _ensure_schema()

    if not agent_id or len(agent_id) > 64:
        raise HTTPException(400, "Invalid agent_id")

    db = await _get_db()

    # Get agent permissions (core identity)
    perms_rows = await db.raw_execute_fetchall(
        "SELECT agent_id, wallet, did, uaid, trust_level, status, created_at "
        "FROM agent_permissions WHERE agent_id=? LIMIT 1",
        (agent_id,),
    )
    if not perms_rows:
        raise HTTPException(404, f"Agent {agent_id} not found")
    perms = dict(perms_rows[0]) if not isinstance(perms_rows[0], dict) else perms_rows[0]

    # Get all linked wallets
    wallet_rows = await db.raw_execute_fetchall(
        "SELECT link_id, chain, address, verified, linked_at "
        "FROM linked_wallets WHERE agent_id=? ORDER BY linked_at ASC",
        (agent_id,),
    )
    def _to_dict(r) -> dict:
        return dict(r) if not isinstance(r, dict) else r

    wallets = [
        {
            "chain": _to_dict(r).get("chain", ""),
            "address": _to_dict(r).get("address", ""),
            "verified": bool(_to_dict(r).get("verified", 0)),
            "linked_at": _to_dict(r).get("linked_at", 0),
        }
        for r in wallet_rows
    ]

    # Aggregate spend/earnings across all linked wallets
    total_spent = 0.0
    total_earned = 0.0
    services_listed = 0

    all_addresses = [w["address"] for w in wallets]
    # Include primary wallet if not already in linked list
    primary = perms.get("wallet", "")
    if primary and primary not in all_addresses:
        all_addresses.append(primary)

    for addr in all_addresses:
        try:
            agent_rows = await db.raw_execute_fetchall(
                "SELECT total_spent, total_earned, services_listed "
                "FROM agents WHERE wallet=? LIMIT 1",
                (addr,),
            )
            if agent_rows:
                row = dict(agent_rows[0]) if not isinstance(agent_rows[0], dict) else agent_rows[0]
                total_spent += float(row.get("total_spent", 0) or 0)
                total_earned += float(row.get("total_earned", 0) or 0)
                services_listed += int(row.get("services_listed", 0) or 0)
        except Exception:
            continue

    chains_active = sorted(set(w["chain"] for w in wallets if w["chain"]))

    return {
        "agent_id": agent_id,
        "did": perms.get("did", ""),
        "uaid": perms.get("uaid", ""),
        "primary_wallet": primary,
        "trust_level": perms.get("trust_level", 0),
        "status": perms.get("status", "active"),
        "created_at": perms.get("created_at", ""),
        "linked_wallets": wallets,
        "wallet_count": len(wallets),
        "chains_active": chains_active,
        "chain_count": len(chains_active),
        "aggregated": {
            "total_spent_usdc": round(total_spent, 2),
            "total_earned_usdc": round(total_earned, 2),
            "services_listed": services_listed,
        },
    }


@router.delete("/unlink")
async def unlink_wallet(
    req: UnlinkWalletRequest,
    x_api_key: str = Header(..., alias="X-API-Key"),
) -> dict:
    """Remove a linked wallet from an agent identity.

    Auth: X-API-Key header. Cannot unlink the primary registration wallet.
    """
    await _ensure_schema()
    agent = await _require_api_key(x_api_key)

    chain = req.chain.lower().strip()
    address = req.address.strip()

    if not chain or not address:
        raise HTTPException(400, "chain and address required")

    # Resolve agent_id
    agent_id = agent.get("agent_id", "")
    if not agent_id:
        agent_id = await _resolve_agent_id(agent["api_key"], agent["wallet"])
    if not agent_id:
        raise HTTPException(500, "Could not resolve agent identity")

    # Prevent unlinking the primary wallet
    if address == agent.get("wallet", ""):
        raise HTTPException(
            400,
            "Cannot unlink primary registration wallet. Use key rotation instead.",
        )

    db = await _get_db()

    # Verify the link exists and belongs to this agent
    rows = await db.raw_execute_fetchall(
        "SELECT link_id, agent_id FROM linked_wallets WHERE chain=? AND address=? LIMIT 1",
        (chain, address),
    )
    if not rows:
        raise HTTPException(404, f"No linked wallet found for {address} on {chain}")

    row = dict(rows[0]) if not isinstance(rows[0], dict) else rows[0]
    if row.get("agent_id") != agent_id:
        raise HTTPException(403, "This wallet is not linked to your agent")

    await db.raw_execute(
        "DELETE FROM linked_wallets WHERE chain=? AND address=? AND agent_id=?",
        (chain, address, agent_id),
    )

    _cache_invalidate(address)
    logger.info("Wallet unlinked: %s on %s from %s", address[:8], chain, agent_id)

    return {
        "success": True,
        "agent_id": agent_id,
        "chain": chain,
        "address": address,
        "status": "unlinked",
    }


@router.get("/chains/{agent_id}")
async def get_agent_chains(agent_id: str) -> dict:
    """List all chains an agent is active on with wallet addresses.

    Public endpoint — no auth required.
    """
    await _ensure_schema()

    if not agent_id or len(agent_id) > 64:
        raise HTTPException(400, "Invalid agent_id")

    db = await _get_db()

    # Verify agent exists
    perms_rows = await db.raw_execute_fetchall(
        "SELECT agent_id, wallet FROM agent_permissions WHERE agent_id=? LIMIT 1",
        (agent_id,),
    )
    if not perms_rows:
        raise HTTPException(404, f"Agent {agent_id} not found")

    # Get linked wallets grouped by chain
    rows = await db.raw_execute_fetchall(
        "SELECT chain, address, verified, linked_at "
        "FROM linked_wallets WHERE agent_id=? ORDER BY chain, linked_at",
        (agent_id,),
    )

    chains: dict[str, list[dict]] = {}
    for r in rows:
        row = dict(r) if not isinstance(r, dict) else r
        chain = row.get("chain", "unknown")
        if chain not in chains:
            chains[chain] = []
        chains[chain].append({
            "address": row.get("address", ""),
            "verified": bool(row.get("verified", 0)),
            "linked_at": row.get("linked_at", 0),
        })

    return {
        "agent_id": agent_id,
        "chain_count": len(chains),
        "chains": chains,
    }
