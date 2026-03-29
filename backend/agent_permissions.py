"""MAXIA Agent Permissions — DID + UAID, spend caps, scopes OAuth, revocation, key rotation.

Chaque agent a :
- did (W3C Decentralized Identifier) — identite universelle portable
- uaid (HCS-14 Universal Agent ID) — identite Hedera-compatible
- trust_level (L0-L4) via AgentID
- status (active/frozen/revoked)
- scopes (liste de permissions granulaires, ou '*' pour tout)
- spend caps (max_daily_spend_usd, max_single_tx_usd)

DID format : did:web:maxiaworld.app:agent:{agent_id}
UAID format : SHA-384(canonical metadata) → Base58 (HCS-14 spec)
Keypair : ed25519 (nacl) — cle publique dans DID Document, cle privee donnee 1 fois a l'agent

Les caps par defaut sont lies au trust level. L'admin peut override.
"""
import json
import logging
import time
import uuid
import hashlib
import secrets

logger = logging.getLogger(__name__)
from datetime import datetime, timezone
from fastapi import HTTPException
from nacl.signing import SigningKey, VerifyKey
import base58


# ══════════════════════════════════════════
# DID + UAID Generation (W3C + HCS-14)
# ══════════════════════════════════════════

_BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _base58_encode(data: bytes) -> str:
    """Encode bytes to Base58 (Bitcoin-style)."""
    num = int.from_bytes(data, "big")
    result = []
    while num > 0:
        num, rem = divmod(num, 58)
        result.append(_BASE58_ALPHABET[rem])
    # Preserve leading zeros
    for b in data:
        if b == 0:
            result.append(_BASE58_ALPHABET[0])
        else:
            break
    return "".join(reversed(result))


def generate_did(agent_id: str) -> str:
    """Generate W3C DID for a MAXIA agent.
    Format: did:web:maxiaworld.app:agent:{agent_id}
    Resolvable via HTTPS: https://maxiaworld.app/agent/{agent_id}/did.json
    """
    return f"did:web:maxiaworld.app:agent:{agent_id}"


def generate_uaid(agent_id: str, name: str = "", wallet: str = "") -> str:
    """Generate HCS-14 compatible UAID (Universal Agent ID).
    IMMUTABLE: based only on agent_id + registry (never changes).
    Name, wallet, endpoints live in the DID Document (mutable layer).
    Format: SHA-384(canonical JSON) → Base58.
    """
    # Only immutable fields — UAID never changes even if agent changes name/wallet
    metadata = json.dumps({
        "nativeId": agent_id,
        "protocol": "a2a",
        "registry": "maxia",
        "version": "1.0.0",
    }, sort_keys=True, separators=(",", ":"))

    digest = hashlib.sha384(metadata.encode("utf-8")).digest()
    return _base58_encode(digest)


def generate_agent_keypair() -> tuple[str, str]:
    """Generate ed25519 keypair for agent identity.
    Returns (public_key_base58, private_key_hex).
    Private key is given to agent ONCE at registration. Public key stored in DB + DID Document."""
    sk = SigningKey.generate()
    pk = sk.verify_key
    public_b58 = base58.b58encode(bytes(pk)).decode()
    private_hex = sk.encode().hex()
    return public_b58, private_hex


def generate_did_document(agent_id: str, public_key_b58: str, wallet: str,
                          uaid: str = "", status: str = "active",
                          trust_level: int = 0) -> dict:
    """Generate W3C DID Document for a MAXIA agent.
    Resolvable at: https://maxiaworld.app/agent/{agent_id}/did.json"""
    did = generate_did(agent_id)
    return {
        "@context": [
            "https://www.w3.org/ns/did/v1",
            "https://w3id.org/security/suites/ed25519-2020/v1",
        ],
        "id": did,
        "verificationMethod": [{
            "id": f"{did}#key-1",
            "type": "Ed25519VerificationKey2020",
            "controller": did,
            "publicKeyBase58": public_key_b58,
        }],
        "authentication": [f"{did}#key-1"],
        "assertionMethod": [f"{did}#key-1"],
        "service": [
            {
                "id": f"{did}#maxia-marketplace",
                "type": "AIAgentService",
                "serviceEndpoint": f"https://maxiaworld.app/api/public/agent/{agent_id}",
            },
            {
                "id": f"{did}#maxia-a2a",
                "type": "AgentToAgent",
                "serviceEndpoint": "https://maxiaworld.app/a2a",
            },
        ],
        "maxia:wallet": wallet,
        "maxia:uaid": uaid,
        "maxia:status": status,
        "maxia:trustLevel": trust_level,
        "maxia:registry": "maxia",
        "maxia:protocol": "a2a",
    }


# ══════════════════════════════════════════
# Trust level -> default caps
# ══════════════════════════════════════════

TRUST_LEVEL_DEFAULTS = {
    0: {"label": "Unverified",  "max_daily": 50,     "max_single": 10,     "escrow_hold_h": 48},
    1: {"label": "Basic",      "max_daily": 500,    "max_single": 50,     "escrow_hold_h": 48},
    2: {"label": "Verified",   "max_daily": 5000,   "max_single": 1000,   "escrow_hold_h": 24},
    3: {"label": "Trusted",    "max_daily": 50000,  "max_single": 10000,  "escrow_hold_h": 0},
    4: {"label": "Established","max_daily": 500000, "max_single": 100000, "escrow_hold_h": 0},
}

# ══════════════════════════════════════════
# Available scopes
# ══════════════════════════════════════════

ALL_SCOPES = [
    "marketplace:discover", "marketplace:list", "marketplace:execute",
    "swap:read", "swap:execute",
    "gpu:read", "gpu:rent", "gpu:terminate",
    "stocks:read", "stocks:trade",
    "escrow:read", "escrow:lock", "escrow:confirm", "escrow:dispute",
    "defi:read", "defi:deposit",
    "mcp:read", "mcp:execute",
]

# Scopes par defaut par trust level
DEFAULT_SCOPES = {
    0: ["marketplace:discover", "marketplace:list", "marketplace:execute",
        "swap:read", "swap:execute", "gpu:read", "gpu:rent",
        "stocks:read", "stocks:trade", "escrow:read", "escrow:lock",
        "defi:read", "mcp:read", "mcp:execute"],
    1: ["marketplace:discover", "marketplace:list", "marketplace:execute",
        "swap:read", "swap:execute", "gpu:read", "gpu:rent",
        "stocks:read", "escrow:read", "escrow:lock",
        "defi:read", "mcp:read", "mcp:execute"],
    2: ALL_SCOPES[:],  # Tout sauf admin
    3: ALL_SCOPES[:],
    4: ALL_SCOPES[:],
}

# Write scopes — bloquees si status=frozen
WRITE_SCOPES = {
    "marketplace:list", "marketplace:execute",
    "swap:execute", "gpu:rent", "gpu:terminate",
    "stocks:trade", "escrow:lock", "escrow:confirm",
    "escrow:dispute", "defi:deposit", "mcp:execute",
}

# Cache en memoire pour eviter les queries DB a chaque requete
_perms_cache: dict = {}  # api_key -> {perms_dict, cached_at}
_CACHE_TTL = 300  # 5 min


async def _get_db():
    from database import db
    return db


async def get_or_create_permissions(api_key: str, wallet: str) -> dict:
    """Recupere les permissions d'un agent, ou les cree avec les defaults L0."""
    # Check cache
    cached = _perms_cache.get(api_key)
    if cached and time.time() - cached["cached_at"] < _CACHE_TTL:
        return cached["perms"]

    db = await _get_db()
    rows = await db.raw_execute_fetchall(
        "SELECT agent_id, api_key, wallet, did, uaid, public_key, trust_level, status, scopes, "
        "max_daily_spend_usd, max_single_tx_usd, daily_spent_usd, daily_spent_date, "
        "frozen_at, revoked_at, downgraded_from, created_at, updated_at "
        "FROM agent_permissions WHERE api_key=?", (api_key,))

    if rows:
        perms = dict(rows[0])
    else:
        # Creer les permissions par defaut
        agent_id = f"agent_{uuid.uuid4().hex[:12]}"
        trust = 0

        # Essayer AgentID pour le trust level
        try:
            from agentid_client import agentid
            if agentid.enabled:
                trust = await agentid.get_trust_level(wallet)
        except Exception:
            pass

        defaults = TRUST_LEVEL_DEFAULTS.get(trust, TRUST_LEVEL_DEFAULTS[0])
        scopes = DEFAULT_SCOPES.get(trust, DEFAULT_SCOPES[0])
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Generate DID + UAID
        agent_name = ""
        try:
            name_rows = await db.raw_execute_fetchall(
                "SELECT name FROM agents WHERE api_key=?", (api_key,))
            if name_rows:
                agent_name = name_rows[0]["name"]
        except Exception:
            pass

        did = generate_did(agent_id)
        uaid = generate_uaid(agent_id, agent_name or agent_id, wallet)
        public_key_b58, private_key_hex = generate_agent_keypair()

        perms = {
            "agent_id": agent_id,
            "api_key": api_key,
            "wallet": wallet,
            "did": did,
            "uaid": uaid,
            "public_key": public_key_b58,
            "_private_key_once": private_key_hex,  # Retourne 1 seule fois, PAS stocke en DB
            "trust_level": trust,
            "status": "active",
            "scopes": json.dumps(scopes),
            "max_daily_spend_usd": defaults["max_daily"],
            "max_single_tx_usd": defaults["max_single"],
            "daily_spent_usd": 0,
            "daily_spent_date": now[:10],
            "frozen_at": None,
            "revoked_at": None,
            "downgraded_from": None,
            "created_at": now,
            "updated_at": now,
        }

        try:
            await db.raw_execute(
                "INSERT INTO agent_permissions"
                "(agent_id,api_key,wallet,did,uaid,public_key,trust_level,status,scopes,"
                "max_daily_spend_usd,max_single_tx_usd,daily_spent_usd,daily_spent_date,"
                "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (perms["agent_id"], api_key, wallet, did, uaid, public_key_b58,
                 trust, "active", json.dumps(scopes),
                 defaults["max_daily"], defaults["max_single"],
                 0, now[:10], now, now))
        except Exception as e:
            logger.error("Create error: %s", e)

    # Update cache
    _perms_cache[api_key] = {"perms": perms, "cached_at": time.time()}
    return perms


def _invalidate_cache(api_key: str):
    _perms_cache.pop(api_key, None)


# ══════════════════════════════════════════
# CHECK STATUS — freeze/revoke
# ══════════════════════════════════════════

async def check_agent_status(api_key: str, wallet: str, is_write: bool = False):
    """Verifie le status de l'agent. Raise 403 si frozen (write) ou revoked (tout).

    Args:
        api_key: API key de l'agent
        wallet: Wallet address
        is_write: True si l'action est une ecriture (swap, trade, etc.)

    Returns:
        dict: permissions de l'agent
    """
    perms = await get_or_create_permissions(api_key, wallet)
    status = perms.get("status", "active")

    if status == "revoked":
        raise HTTPException(403, {
            "error": "Agent revoked",
            "agent_id": perms.get("agent_id", ""),
            "detail": "This agent has been permanently revoked. Contact support.",
        })

    if status == "frozen" and is_write:
        raise HTTPException(403, {
            "error": "Agent frozen",
            "agent_id": perms.get("agent_id", ""),
            "detail": "This agent is temporarily frozen. Read operations only.",
        })

    return perms


# ══════════════════════════════════════════
# CHECK SCOPE — per-action permissions
# ══════════════════════════════════════════

async def check_agent_scope(api_key: str, wallet: str, required_scope: str):
    """Verifie que l'agent a le scope necessaire. Raise 403 sinon.

    Args:
        api_key: API key
        wallet: Wallet address
        required_scope: ex "swap:execute"

    Returns:
        dict: permissions de l'agent
    """
    is_write = required_scope in WRITE_SCOPES
    perms = await check_agent_status(api_key, wallet, is_write)

    scopes_raw = perms.get("scopes", "*")
    if scopes_raw == "*":
        return perms  # Wildcard — tout autorise (retrocompat)

    try:
        scopes = json.loads(scopes_raw) if isinstance(scopes_raw, str) else scopes_raw
    except (json.JSONDecodeError, TypeError):
        scopes = ["*"]

    if "*" in scopes:
        return perms

    # Check exact match ou wildcard partiel (ex: "swap:*" autorise "swap:execute")
    scope_parts = required_scope.split(":")
    for s in scopes:
        if s == required_scope:
            return perms
        s_parts = s.split(":")
        if len(s_parts) == 2 and s_parts[1] == "*" and s_parts[0] == scope_parts[0]:
            return perms

    raise HTTPException(403, {
        "error": "Insufficient scope",
        "agent_id": perms.get("agent_id", ""),
        "required": required_scope,
        "your_scopes": scopes,
        "detail": f"Agent lacks scope: {required_scope}",
    })


# ══════════════════════════════════════════
# CHECK SPEND — per-agent budget limits
# ══════════════════════════════════════════

async def check_agent_spend(api_key: str, wallet: str, amount_usd: float) -> dict:
    """Verifie les caps de depense. Raise 403 si depasse.

    Args:
        api_key: API key
        wallet: Wallet address
        amount_usd: Montant de la transaction en USD

    Returns:
        dict: permissions (avec daily_spent mis a jour)
    """
    perms = await check_agent_status(api_key, wallet, is_write=True)

    max_single = perms.get("max_single_tx_usd", 10)
    max_daily = perms.get("max_daily_spend_usd", 50)
    daily_spent = perms.get("daily_spent_usd", 0)
    daily_date = perms.get("daily_spent_date", "")

    # Reset si nouveau jour
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if daily_date != today:
        daily_spent = 0

    # Check single tx cap
    if amount_usd > max_single:
        raise HTTPException(403, {
            "error": "Transaction exceeds single-tx cap",
            "agent_id": perms.get("agent_id", ""),
            "amount_usd": amount_usd,
            "max_single_tx_usd": max_single,
            "trust_level": perms.get("trust_level", 0),
            "detail": f"Max ${max_single} per transaction. Upgrade trust level for higher limits.",
        })

    # Check daily cap
    if daily_spent + amount_usd > max_daily:
        remaining = max(0, max_daily - daily_spent)
        raise HTTPException(403, {
            "error": "Daily spend cap exceeded",
            "agent_id": perms.get("agent_id", ""),
            "daily_spent_usd": daily_spent,
            "max_daily_spend_usd": max_daily,
            "remaining_usd": remaining,
            "trust_level": perms.get("trust_level", 0),
            "detail": f"Daily limit ${max_daily}. Remaining: ${remaining:.2f}.",
        })

    return perms


async def record_spend(api_key: str, amount_usd: float):
    """Enregistre une depense apres execution reussie."""
    db = await _get_db()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        # Reset si nouveau jour, sinon increment
        await db.raw_execute(
            "UPDATE agent_permissions SET "
            "daily_spent_usd = CASE WHEN daily_spent_date = ? THEN daily_spent_usd + ? ELSE ? END, "
            "daily_spent_date = ?, updated_at = ? "
            "WHERE api_key = ?",
            (today, amount_usd, amount_usd, today, now, api_key))
    except Exception as e:
        logger.error("Record spend error: %s", e)

    _invalidate_cache(api_key)


# ══════════════════════════════════════════
# ADMIN — freeze / unfreeze / downgrade / revoke
# ══════════════════════════════════════════

async def freeze_agent(agent_id: str) -> dict:
    """Freeze un agent — lectures OK, ecritures bloquees."""
    db = await _get_db()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    await db.raw_execute(
        "UPDATE agent_permissions SET status='frozen', frozen_at=?, updated_at=? WHERE agent_id=?",
        (now, now, agent_id))
    # Invalider le cache pour tous les api_keys de cet agent
    _perms_cache.clear()
    return {"success": True, "agent_id": agent_id, "status": "frozen"}


async def unfreeze_agent(agent_id: str) -> dict:
    """Unfreeze un agent — retour a active."""
    db = await _get_db()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    await db.raw_execute(
        "UPDATE agent_permissions SET status='active', frozen_at=NULL, updated_at=? WHERE agent_id=?",
        (now, agent_id))
    _perms_cache.clear()
    return {"success": True, "agent_id": agent_id, "status": "active"}


async def downgrade_agent(agent_id: str, new_level: int) -> dict:
    """Downgrade le trust level d'un agent. Les caps s'ajustent automatiquement."""
    if new_level < 0 or new_level > 4:
        raise HTTPException(400, "Trust level must be 0-4")

    db = await _get_db()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Lire le level actuel
    rows = await db.raw_execute_fetchall(
        "SELECT trust_level FROM agent_permissions WHERE agent_id=?", (agent_id,))
    if not rows:
        raise HTTPException(404, f"Agent {agent_id} not found")
    old_level = rows[0]["trust_level"]

    defaults = TRUST_LEVEL_DEFAULTS.get(new_level, TRUST_LEVEL_DEFAULTS[0])
    new_scopes = json.dumps(DEFAULT_SCOPES.get(new_level, DEFAULT_SCOPES[0]))

    await db.raw_execute(
        "UPDATE agent_permissions SET trust_level=?, max_daily_spend_usd=?, "
        "max_single_tx_usd=?, scopes=?, downgraded_from=?, updated_at=? "
        "WHERE agent_id=?",
        (new_level, defaults["max_daily"], defaults["max_single"],
         new_scopes, old_level, now, agent_id))
    _perms_cache.clear()

    return {
        "success": True, "agent_id": agent_id,
        "old_level": old_level, "new_level": new_level,
        "new_caps": {"max_daily": defaults["max_daily"], "max_single": defaults["max_single"]},
    }


async def revoke_agent(agent_id: str) -> dict:
    """Revoke definitivement un agent. Tout bloque."""
    db = await _get_db()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    await db.raw_execute(
        "UPDATE agent_permissions SET status='revoked', revoked_at=?, updated_at=? WHERE agent_id=?",
        (now, now, agent_id))
    _perms_cache.clear()
    return {"success": True, "agent_id": agent_id, "status": "revoked"}


async def get_agent_perms_by_id(agent_id: str) -> dict:
    """Recupere les permissions par agent_id."""
    db = await _get_db()
    rows = await db.raw_execute_fetchall(
        "SELECT agent_id, api_key, wallet, did, uaid, public_key, trust_level, status, scopes, "
        "max_daily_spend_usd, max_single_tx_usd, daily_spent_usd, daily_spent_date, "
        "frozen_at, revoked_at, downgraded_from, created_at, updated_at "
        "FROM agent_permissions WHERE agent_id=?", (agent_id,))
    if not rows:
        raise HTTPException(404, f"Agent {agent_id} not found")
    return dict(rows[0])


async def list_all_agents() -> list:
    """Liste tous les agents avec leurs permissions."""
    db = await _get_db()
    rows = await db.raw_execute_fetchall(
        "SELECT agent_id, api_key, wallet, trust_level, status, scopes, "
        "max_daily_spend_usd, max_single_tx_usd, daily_spent_usd, created_at "
        "FROM agent_permissions ORDER BY created_at DESC LIMIT 100")
    return [dict(r) for r in rows]


async def update_agent_scopes(agent_id: str, scopes: list) -> dict:
    """Met a jour les scopes d'un agent."""
    # Valider les scopes
    invalid = [s for s in scopes if s != "*" and s not in ALL_SCOPES
               and not (len(s.split(":")) == 2 and s.split(":")[1] == "*")]
    if invalid:
        raise HTTPException(400, f"Invalid scopes: {invalid}. Valid: {ALL_SCOPES}")

    db = await _get_db()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    await db.raw_execute(
        "UPDATE agent_permissions SET scopes=?, updated_at=? WHERE agent_id=?",
        (json.dumps(scopes), now, agent_id))
    _perms_cache.clear()
    return {"success": True, "agent_id": agent_id, "scopes": scopes}


# ══════════════════════════════════════════
# KEY ROTATION — new API key, same identity
# ══════════════════════════════════════════

async def rotate_agent_key(agent_id: str) -> dict:
    """Rotate l'API key d'un agent. Garde le meme agent_id, DID, UAID, trust level, historique.
    L'ancienne cle est invalidee immediatement."""
    db = await _get_db()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Verifier que l'agent existe
    rows = await db.raw_execute_fetchall(
        "SELECT agent_id, api_key, wallet, did, uaid, trust_level, status "
        "FROM agent_permissions WHERE agent_id=?", (agent_id,))
    if not rows:
        raise HTTPException(404, f"Agent {agent_id} not found")

    old_perms = dict(rows[0])
    old_key = old_perms["api_key"]

    # Generer nouvelle cle
    new_key = f"maxia_{secrets.token_hex(24)}"

    # Mettre a jour la cle dans agent_permissions
    await db.raw_execute(
        "UPDATE agent_permissions SET api_key=?, updated_at=? WHERE agent_id=?",
        (new_key, now, agent_id))

    # Mettre a jour la cle dans la table agents (si elle existe)
    try:
        await db.raw_execute(
            "UPDATE agents SET api_key=? WHERE api_key=?",
            (new_key, old_key))
    except Exception:
        pass

    # Mettre a jour les services de cet agent
    try:
        await db.raw_execute(
            "UPDATE agent_services SET agent_api_key=? WHERE agent_api_key=?",
            (new_key, old_key))
    except Exception:
        pass

    _perms_cache.clear()

    return {
        "success": True,
        "agent_id": agent_id,
        "did": old_perms.get("did", ""),
        "uaid": old_perms.get("uaid", ""),
        "old_key_prefix": old_key[:12] + "...",
        "new_api_key": new_key,
        "trust_level": old_perms.get("trust_level", 0),
        "note": "Old key is immediately invalid. DID, UAID, trust level, and history are preserved.",
    }


# ══════════════════════════════════════════
# PUBLIC LOOKUP — resolve agent by DID or UAID
# ══════════════════════════════════════════

async def resolve_agent_public(identifier: str) -> dict:
    """Resolve un agent par DID ou UAID. Retourne les infos publiques (pas l'API key).
    Accessible sans auth — n'importe quel marketplace peut verifier."""
    db = await _get_db()

    # Determiner si c'est un DID ou un UAID
    # Columns needed for public resolution (agent_id, did, uaid, wallet, trust_level,
    # status, scopes, created_at, revoked_at, frozen_at)
    _resolve_cols = (
        "SELECT agent_id, did, uaid, wallet, trust_level, status, scopes, "
        "created_at, revoked_at, frozen_at "
    )
    if identifier.startswith("did:"):
        rows = await db.raw_execute_fetchall(
            _resolve_cols + "FROM agent_permissions WHERE did=?", (identifier,))
    else:
        rows = await db.raw_execute_fetchall(
            _resolve_cols + "FROM agent_permissions WHERE uaid=? OR agent_id=?",
            (identifier, identifier))

    if not rows:
        raise HTTPException(404, {
            "error": "Agent not found",
            "identifier": identifier,
            "detail": "No agent registered with this DID or UAID.",
        })

    perms = dict(rows[0])

    # Retourner les infos publiques uniquement (PAS l'api_key)
    return {
        "agent_id": perms["agent_id"],
        "did": perms.get("did", ""),
        "uaid": perms.get("uaid", ""),
        "wallet": perms["wallet"],
        "trust_level": perms.get("trust_level", 0),
        "trust_label": TRUST_LEVEL_DEFAULTS.get(perms.get("trust_level", 0), {}).get("label", "Unknown"),
        "status": perms["status"],
        "scopes": json.loads(perms["scopes"]) if isinstance(perms.get("scopes"), str) and perms["scopes"] != "*" else ["*"],
        "created_at": perms.get("created_at", ""),
        "revoked_at": perms.get("revoked_at"),
        "frozen_at": perms.get("frozen_at"),
        "registry": "maxia",
        "protocol": "a2a",
        "verification_url": f"https://maxiaworld.app/api/public/agent/{perms.get('uaid', perms['agent_id'])}",
    }
