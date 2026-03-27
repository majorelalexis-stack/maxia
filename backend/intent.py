"""MAXIA Signed Intent Envelopes — AIP-inspired cryptographic proof of agent actions.

Un agent signe ses intentions AVANT execution. MAXIA verifie la signature.
Non-repudiable, expire, limite (max_slippage, max_amount).

Usage:
  Agent-side:  intent = sign_intent(action, params, private_key_hex)
  Server-side: payload = verify_intent(intent_json, expected_did)
"""
import json
import time
import hashlib
from datetime import datetime, timezone
from nacl.signing import SigningKey, VerifyKey
from nacl.exceptions import BadSignatureError
import base58


def sign_intent(action: str, params: dict, private_key_hex: str,
                did: str, expires_s: int = 300) -> dict:
    """Cree un signed intent envelope (cote agent).

    Args:
        action: ex "swap", "gpu_rent", "escrow_lock"
        params: ex {"from": "USDC", "to": "SOL", "amount": 100}
        private_key_hex: cle privee ed25519 (hex)
        did: DID de l'agent
        expires_s: duree de validite en secondes (default 5min)

    Returns:
        dict: intent envelope avec signature
    """
    now = datetime.now(timezone.utc)
    nonce = hashlib.sha256(f"{did}:{time.time_ns()}".encode()).hexdigest()[:16]
    expires = datetime.fromtimestamp(
        now.timestamp() + expires_s, tz=timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Payload canonique (sorted, no whitespace)
    payload = {
        "action": action,
        "did": did,
        "expires": expires,
        "nonce": nonce,
        "params": params,
        "v": 1,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))

    # Signer avec ed25519
    sk = SigningKey(bytes.fromhex(private_key_hex))
    sig = sk.sign(canonical.encode()).signature
    sig_b58 = base58.b58encode(sig).decode()

    return {**payload, "sig": sig_b58}


def verify_intent(intent: dict, expected_public_key_b58: str = None) -> dict:
    """Verifie un signed intent envelope (cote serveur).

    Args:
        intent: dict avec v, did, action, params, nonce, expires, sig
        expected_public_key_b58: cle publique attendue (optionnel si on resout via DID)

    Returns:
        dict: {"valid": True, "payload": {...}} ou {"valid": False, "error": "..."}
    """
    try:
        # Verifier les champs requis
        required = ["v", "did", "action", "params", "nonce", "expires", "sig"]
        for field in required:
            if field not in intent:
                return {"valid": False, "error": f"Missing field: {field}"}

        if intent["v"] != 1:
            return {"valid": False, "error": f"Unsupported intent version: {intent['v']}"}

        # Verifier l'expiration
        try:
            expires_dt = datetime.fromisoformat(intent["expires"].replace("Z", "+00:00"))
            if datetime.now(timezone.utc) > expires_dt:
                return {"valid": False, "error": "Intent expired"}
        except (ValueError, TypeError):
            return {"valid": False, "error": "Invalid expires format"}

        # Reconstruire le payload canonique (sans sig)
        payload = {
            "action": intent["action"],
            "did": intent["did"],
            "expires": intent["expires"],
            "nonce": intent["nonce"],
            "params": intent["params"],
            "v": intent["v"],
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))

        # Verifier la signature si on a la cle publique
        if expected_public_key_b58:
            sig_bytes = base58.b58decode(intent["sig"])
            pk_bytes = base58.b58decode(expected_public_key_b58)
            vk = VerifyKey(pk_bytes)
            vk.verify(canonical.encode(), sig_bytes)

        return {
            "valid": True,
            "did": intent["did"],
            "action": intent["action"],
            "params": intent["params"],
            "nonce": intent["nonce"],
            "expires": intent["expires"],
        }

    except BadSignatureError:
        return {"valid": False, "error": "Invalid signature — intent tampered or wrong key"}
    except Exception as e:
        return {"valid": False, "error": f"Verification failed: {str(e)[:100]}"}


async def verify_intent_with_did(intent: dict) -> dict:
    """Verifie un intent en resolvant la cle publique depuis le DID en DB.
    Utilise pour la verification cote serveur automatique."""
    did = intent.get("did", "")
    if not did:
        return {"valid": False, "error": "No DID in intent"}

    # Resoudre la cle publique depuis la DB
    try:
        from agent_permissions import _get_db
        db = await _get_db()
        rows = await db.raw_execute_fetchall(
            "SELECT public_key, status FROM agent_permissions WHERE did=?", (did,))
        if not rows:
            return {"valid": False, "error": f"DID not found: {did}"}

        public_key = rows[0].get("public_key", "")
        status = rows[0].get("status", "active")

        if status == "revoked":
            return {"valid": False, "error": "Agent revoked"}
        if not public_key:
            return {"valid": False, "error": "No public key for this agent"}

        result = verify_intent(intent, public_key)
        if result["valid"]:
            result["agent_status"] = status
        return result

    except Exception as e:
        return {"valid": False, "error": f"DID resolution failed: {str(e)[:100]}"}
