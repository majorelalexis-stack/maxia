"""MAXIA Signed Intent Envelopes — powered by AIP Protocol (pip install aip-protocol).

Utilise aip-protocol pour la signature et verification d'intents.
Fallback sur implementation maison si aip-protocol pas installe.

AIP = Agent Identity Protocol (github.com/theaniketgiri/aip)
- Enveloppe standard, framework-agnostic (LangChain, AutoGen, CrewAI)
- ed25519 signatures via cryptography lib
- Anti-replay nonce intégré (chaque envelope ne peut etre verifiee qu'une fois)
- Verification sub-milliseconde

Usage:
  Agent-side:  envelope = create_maxia_intent(passport, "swap", {"from":"USDC","to":"SOL","amount":100})
  Server-side: result = verify_maxia_intent(envelope, public_key)
"""
import logging

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════
# AIP Protocol integration (v0.3.0)
# ══════════════════════════════════════════

try:
    from aip_protocol import (
        generate_keypair as aip_generate_keypair,
        create_envelope as aip_create_envelope,
        sign_envelope as aip_sign_envelope,
        verify_intent as aip_verify_intent,
        AgentPassport, AgentIdentity, Principal, Boundaries, MonetaryLimit,
        IntentEnvelope,
    )
    AIP_AVAILABLE = True
    logger.info("[AIP] aip-protocol v0.3.0 loaded — signed intents enabled")
except ImportError:
    AIP_AVAILABLE = False
    logger.warning("[AIP] aip-protocol not installed — using fallback. pip install aip-protocol")


# ══════════════════════════════════════════
# AIP default actions for MAXIA agents
# ══════════════════════════════════════════

MAXIA_DEFAULT_ACTIONS = [
    "swap", "gpu_rent", "gpu_terminate", "escrow_lock",
    "escrow_confirm", "stocks_buy", "stocks_sell",
    "marketplace_execute", "defi_deposit",
]


def create_agent_passport(agent_id: str, name: str, did: str,
                          max_tx_usd: float = 1000,
                          max_daily_usd: float = 5000,
                          allowed_actions: list = None,
                          private_key=None, public_key=None):
    """Create an AIP AgentPassport for a MAXIA agent.

    Args:
        agent_id: internal MAXIA agent ID
        name: human-readable agent name
        did: W3C DID (did:web:maxiaworld.app:agent:{agent_id})
        max_tx_usd: max spend per transaction
        max_daily_usd: max spend per day
        allowed_actions: list of permitted actions
        private_key: cryptography Ed25519PrivateKey
        public_key: cryptography Ed25519PublicKey

    Returns:
        AgentPassport or None if AIP not available
    """
    if not AIP_AVAILABLE:
        return None

    actions = allowed_actions or MAXIA_DEFAULT_ACTIONS

    identity = AgentIdentity(id=did)
    principal = Principal(type="agent", id=did)
    boundaries = Boundaries(
        allowed_actions=actions,
        monetary_limit=MonetaryLimit(
            per_transaction=max_tx_usd,
            per_day=max_daily_usd,
            currency="USDC",
        ),
    )

    return AgentPassport(
        identity=identity,
        principal=principal,
        boundaries=boundaries,
        private_key=private_key,
        public_key=public_key,
    )


def create_maxia_intent(passport, action: str, parameters: dict,
                        target: str = "maxiaworld.app", ttl: int = 300):
    """Create and sign a MAXIA intent envelope using AIP protocol.

    Args:
        passport: AIP AgentPassport (from create_agent_passport)
        action: "swap", "gpu_rent", "escrow_lock", etc.
        parameters: {"from": "USDC", "to": "SOL", "amount": 100}
        target: endpoint target
        ttl: time-to-live in seconds

    Returns:
        Signed IntentEnvelope or None if AIP not available
    """
    if not AIP_AVAILABLE or not passport:
        return None

    envelope = aip_create_envelope(
        passport=passport,
        action=action,
        target=target,
        parameters=parameters,
        ttl=ttl,
    )
    if passport.private_key:
        envelope = aip_sign_envelope(envelope, passport.private_key)
    return envelope


def verify_maxia_intent(envelope, public_key) -> dict:
    """Verify a signed intent envelope using AIP protocol.

    Note: AIP has built-in anti-replay protection. Each envelope
    can only be verified ONCE (nonce is consumed on first verify).

    Args:
        envelope: AIP IntentEnvelope
        public_key: cryptography Ed25519PublicKey

    Returns:
        {"valid": True/False, "action": str, "trust_score": float, ...}
    """
    if not AIP_AVAILABLE:
        return {"valid": False, "error": "aip-protocol not installed"}

    try:
        result = aip_verify_intent(envelope, public_key)
        response = {
            "valid": result.valid,
            "action": envelope.intent.action,
            "trust_score": result.trust_score,
            "protocol": "aip-protocol",
            "version": "0.3.0",
        }
        if not result.valid:
            response["errors"] = [e.value for e in result.errors]
            response["detail"] = result.detail
        return response
    except Exception as e:
        logger.error(f"[AIP] verify error: {e}", exc_info=True)
        return {"valid": False, "error": "Verification failed"}


def generate_aip_keypair():
    """Generate an AIP-compatible ed25519 keypair (cryptography lib).

    Returns:
        (Ed25519PrivateKey, Ed25519PublicKey) or (None, None) if AIP unavailable
    """
    if not AIP_AVAILABLE:
        return None, None
    return aip_generate_keypair()


def pub_key_to_base58(public_key) -> str:
    """Convert a cryptography Ed25519PublicKey to base58 string for DB storage."""
    import base58
    return base58.b58encode(public_key.public_bytes_raw()).decode()


def base58_to_pub_key(pub_b58: str):
    """Convert a base58 string back to cryptography Ed25519PublicKey."""
    import base58
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    return Ed25519PublicKey.from_public_bytes(base58.b58decode(pub_b58))


def nacl_pub_to_crypto_pub(nacl_verify_key):
    """Bridge: convert a nacl VerifyKey to cryptography Ed25519PublicKey.
    Needed because auth.py uses PyNaCl but AIP uses cryptography."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    return Ed25519PublicKey.from_public_bytes(bytes(nacl_verify_key))


# ══════════════════════════════════════════
# Server-side verification for API endpoints
# ══════════════════════════════════════════

async def verify_intent_from_request(intent_data: dict) -> dict:
    """Verify an AIP intent from an API request body.
    Resolves the agent's public key from the DID stored in the envelope."""
    if not AIP_AVAILABLE:
        return {"valid": False, "error": "aip-protocol not installed on server"}

    try:
        envelope = IntentEnvelope(**intent_data)

        # Resolve public key from DID
        did = envelope.agent.id
        if not did:
            return {"valid": False, "error": "No agent DID in envelope"}

        from database import db
        rows = await db.raw_execute_fetchall(
            "SELECT public_key, status FROM agent_permissions WHERE did=?", (did,))

        if not rows:
            return {"valid": False, "error": f"DID not found: {did}"}
        if rows[0].get("status") == "revoked":
            return {"valid": False, "error": "Agent revoked"}

        pub_key_b58 = rows[0].get("public_key", "")
        if not pub_key_b58:
            return {"valid": False, "error": "No public key registered for this DID"}

        public_key = base58_to_pub_key(pub_key_b58)
        result = aip_verify_intent(envelope, public_key)

        response = {
            "valid": result.valid,
            "did": did,
            "action": envelope.intent.action,
            "protocol": "aip-protocol",
        }
        if not result.valid:
            response["errors"] = [e.value for e in result.errors]
            response["detail"] = result.detail
        return response

    except Exception as e:
        logger.error(f"[AIP] verify_from_request error: {e}", exc_info=True)
        return {"valid": False, "error": "Verification failed"}


# ══════════════════════════════════════════
# Legacy fallback (if aip-protocol not installed)
# ══════════════════════════════════════════

import json
import hashlib
from datetime import datetime, timezone


def sign_intent_legacy(action: str, params: dict, private_key_hex: str,
                       did: str, expires_s: int = 300) -> dict:
    """Legacy MAXIA-specific signed intent (fallback if aip-protocol not available)."""
    import time
    from nacl.signing import SigningKey
    import base58 as b58

    nonce = hashlib.sha256(f"{did}:{time.time_ns()}".encode()).hexdigest()[:16]
    now = datetime.now(timezone.utc)
    expires = datetime.fromtimestamp(
        now.timestamp() + expires_s, tz=timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    payload = json.dumps({
        "action": action, "did": did, "expires": expires,
        "nonce": nonce, "params": params, "v": 1,
    }, sort_keys=True, separators=(",", ":"))

    sk = SigningKey(bytes.fromhex(private_key_hex))
    sig = sk.sign(payload.encode()).signature
    sig_b58 = b58.b58encode(sig).decode()

    return {"v": 1, "did": did, "action": action, "params": params,
            "nonce": nonce, "expires": expires, "sig": sig_b58}


def verify_intent_legacy(intent: dict, public_key_b58: str) -> dict:
    """Legacy verification (fallback)."""
    try:
        from nacl.signing import VerifyKey
        from nacl.exceptions import BadSignatureError
        import base58 as b58

        required = ["v", "did", "action", "params", "nonce", "expires", "sig"]
        for f in required:
            if f not in intent:
                return {"valid": False, "error": f"Missing: {f}"}

        expires_dt = datetime.fromisoformat(intent["expires"].replace("Z", "+00:00"))
        if datetime.now(timezone.utc) > expires_dt:
            return {"valid": False, "error": "Expired"}

        payload = json.dumps({
            "action": intent["action"], "did": intent["did"],
            "expires": intent["expires"], "nonce": intent["nonce"],
            "params": intent["params"], "v": intent["v"],
        }, sort_keys=True, separators=(",", ":"))

        sig_bytes = b58.b58decode(intent["sig"])
        pk_bytes = b58.b58decode(public_key_b58)
        VerifyKey(pk_bytes).verify(payload.encode(), sig_bytes)
        return {"valid": True, "did": intent["did"], "action": intent["action"]}

    except BadSignatureError:
        return {"valid": False, "error": "Invalid signature"}
    except Exception as e:
        logger.error(f"[Intent] Legacy verify error: {e}", exc_info=True)
        return {"valid": False, "error": "Verification failed"}
