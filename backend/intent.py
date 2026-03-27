"""MAXIA Signed Intent Envelopes — powered by AIP Protocol (pip install aip-protocol).

Utilise aip-protocol pour la signature et verification d'intents.
Fallback sur implementation maison si aip-protocol pas installe.

AIP = Agent Identity Protocol (github.com/theaniketgiri/aip)
- Enveloppe standard, framework-agnostic
- ed25519 signatures
- Verification sub-milliseconde

Usage:
  Agent-side:  envelope = create_maxia_intent(passport, "swap", {"from":"USDC","to":"SOL","amount":100})
  Server-side: result = verify_maxia_intent(envelope, public_key)
"""

# ══════════════════════════════════════════
# AIP Protocol integration
# ══════════════════════════════════════════

try:
    from aip_protocol import (
        generate_keypair as aip_generate_keypair,
        create_envelope as aip_create_envelope,
        sign_envelope as aip_sign_envelope,
        verify_intent as aip_verify_intent,
        AgentPassport, AgentIdentity, Principal, Boundaries, MonetaryLimit,
    )
    AIP_AVAILABLE = True
    print("[AIP] aip-protocol v0.3.0 loaded — signed intents enabled")
except ImportError:
    AIP_AVAILABLE = False
    print("[AIP] aip-protocol not installed — using fallback. pip install aip-protocol")


def create_agent_passport(agent_id: str, name: str, did: str,
                          max_amount_usd: float = 1000,
                          allowed_actions: list = None,
                          private_key=None, public_key=None):
    """Create an AIP AgentPassport for a MAXIA agent."""
    if not AIP_AVAILABLE:
        return None

    identity = AgentIdentity(
        agent_id=did,
        name=name or agent_id,
        version="1.0.0",
    )
    actions = allowed_actions or [
        "swap", "gpu_rent", "gpu_terminate", "escrow_lock",
        "escrow_confirm", "stocks_buy", "stocks_sell",
        "marketplace_execute", "defi_deposit",
    ]
    return AgentPassport(
        identity=identity,
        principal=Principal(id=did, name=name or agent_id, framework="maxia"),
        boundaries=Boundaries(
            monetary=MonetaryLimit(max_amount=max_amount_usd, currency="USDC"),
            allowed_actions=actions,
        ),
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

    Args:
        envelope: AIP IntentEnvelope
        public_key: ed25519 public key

    Returns:
        {"valid": True/False, "action": "...", "error": "..."}
    """
    if not AIP_AVAILABLE:
        return {"valid": False, "error": "aip-protocol not installed"}

    try:
        result = aip_verify_intent(envelope, public_key)
        return {
            "valid": result.valid,
            "action": envelope.intent.action if hasattr(envelope, 'intent') else "",
            "protocol": "aip-protocol",
            "version": "0.3.0",
        }
    except Exception as e:
        return {"valid": False, "error": str(e)[:200]}


def generate_aip_keypair():
    """Generate an AIP-compatible ed25519 keypair.
    Returns (private_key, public_key) objects."""
    if not AIP_AVAILABLE:
        return None, None
    return aip_generate_keypair()


# ══════════════════════════════════════════
# Server-side verification for API endpoints
# ══════════════════════════════════════════

async def verify_intent_from_request(intent_data: dict) -> dict:
    """Verify an intent from an API request body.
    Resolves the agent's public key from the DID in the envelope."""
    if not AIP_AVAILABLE:
        return {"valid": False, "error": "aip-protocol not installed on server"}

    try:
        # Reconstruct envelope from dict
        from aip_protocol import IntentEnvelope
        envelope = IntentEnvelope(**intent_data)

        # Resolve public key from DID
        did = envelope.agent.agent_id if hasattr(envelope, 'agent') else ""
        if not did:
            return {"valid": False, "error": "No agent DID in envelope"}

        from agent_permissions import _get_db
        db = await _get_db()
        rows = await db.raw_execute_fetchall(
            "SELECT public_key, status FROM agent_permissions WHERE did=?", (did,))

        if not rows:
            return {"valid": False, "error": f"DID not found: {did}"}
        if rows[0].get("status") == "revoked":
            return {"valid": False, "error": "Agent revoked"}

        pub_key_b58 = rows[0].get("public_key", "")
        if not pub_key_b58:
            return {"valid": False, "error": "No public key registered"}

        # Reconstruct public key from base58
        import base58
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        pub_bytes = base58.b58decode(pub_key_b58)
        public_key = Ed25519PublicKey.from_public_bytes(pub_bytes)

        result = aip_verify_intent(envelope, public_key)
        return {
            "valid": result.valid,
            "did": did,
            "action": envelope.intent.action if hasattr(envelope, 'intent') else "",
            "protocol": "aip-protocol",
        }

    except Exception as e:
        return {"valid": False, "error": f"Verification failed: {str(e)[:200]}"}


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
        return {"valid": False, "error": str(e)[:100]}
