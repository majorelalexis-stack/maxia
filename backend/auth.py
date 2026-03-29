"""MAXIA Auth V12 - Signature Solana ed25519 + anti-replay (Redis-backed nonces)"""
import logging
import os, time, secrets, hashlib, hmac

logger = logging.getLogger(__name__)
from fastapi import APIRouter, HTTPException, Header, Request
from pydantic import BaseModel
from nacl.signing import VerifyKey
from nacl.exceptions import BadSignatureError
import base58

router = APIRouter(prefix="/api/auth", tags=["auth"])
NONCE_TTL = 300

# Redis-backed nonce storage (with in-memory fallback via RedisClient)
_rc = None  # Lazy init to avoid circular import


def _get_rc():
    """Lazy-load redis_client singleton."""
    global _rc
    if _rc is None:
        from redis_client import redis_client
        _rc = redis_client
    return _rc


async def _nonce_set(wallet: str, nonce: str):
    """Store active nonce for a wallet (TTL = NONCE_TTL)."""
    rc = _get_rc()
    await rc.cache_set(f"nonce:{wallet}", nonce, ttl=NONCE_TTL)


async def _nonce_get(wallet: str) -> str | None:
    """Retrieve active nonce for a wallet."""
    rc = _get_rc()
    return await rc.cache_get(f"nonce:{wallet}")


async def _nonce_delete(wallet: str):
    """Delete active nonce after use."""
    rc = _get_rc()
    await rc.cache_delete(f"nonce:{wallet}")


async def _nonce_mark_used(nonce: str):
    """Mark nonce as used for anti-replay (TTL = 2x NONCE_TTL)."""
    rc = _get_rc()
    await rc.cache_set(f"used:{nonce}", "1", ttl=NONCE_TTL * 2)


async def _nonce_is_used(nonce: str) -> bool:
    """Check if nonce was already used (replay detection)."""
    rc = _get_rc()
    return (await rc.cache_get(f"used:{nonce}")) is not None

# JWT-like session tokens (HMAC-signed, not ed25519)
_JWT_SECRET = os.getenv("JWT_SECRET", "")
_SANDBOX = os.getenv("SANDBOX_MODE", "false").lower() == "true"
if not _JWT_SECRET or len(_JWT_SECRET) < 16:
    if _SANDBOX:
        _JWT_SECRET = secrets.token_hex(32)
        logger.info("SANDBOX: JWT_SECRET ephemere genere")
    else:
        raise RuntimeError(
            "JWT_SECRET absent ou trop court (<16 chars) en mode production. "
            "Ajoutez JWT_SECRET=<32+ chars aleatoires> dans .env. "
            "Sans ca, les sessions sont perdues a chaque restart."
        )


def create_session_token(wallet: str) -> str:
    """Cree un token de session signe HMAC-SHA256."""
    payload = f"{wallet}:{int(time.time()) + 86400}"  # 24h expiry
    sig = hmac.new(_JWT_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"


def verify_session_token(token: str) -> str:
    """Verifie un token de session. Retourne le wallet ou raise."""
    parts = token.rsplit(":", 2)
    if len(parts) != 3:
        raise HTTPException(401, "Token invalide")
    wallet, expiry_str, sig = parts
    payload = f"{wallet}:{expiry_str}"
    expected = hmac.new(_JWT_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        raise HTTPException(401, "Token signature invalide")
    if int(expiry_str) < int(time.time()):
        raise HTTPException(401, "Token expire")
    return wallet

# Tentatives echouees par wallet (rate limit brute force — kept in-memory, non-critical)
_FAILED_ATTEMPTS: dict = {}
_MAX_FAILED_ATTEMPTS = 10
_FAILED_WINDOW = 300  # 5 min


class NonceRequest(BaseModel):
    wallet: str

class AuthRequest(BaseModel):
    wallet: str
    signature: str
    nonce: str


def _check_brute_force(wallet: str):
    """Bloque apres trop de tentatives echouees."""
    now = time.time()
    attempts = _FAILED_ATTEMPTS.get(wallet, [])
    attempts = [t for t in attempts if now - t < _FAILED_WINDOW]
    _FAILED_ATTEMPTS[wallet] = attempts
    if len(attempts) >= _MAX_FAILED_ATTEMPTS:
        raise HTTPException(429, "Trop de tentatives echouees. Reessayez dans 5 minutes.")


def _record_failed(wallet: str):
    _FAILED_ATTEMPTS.setdefault(wallet, []).append(time.time())


@router.post("/nonce")
async def get_nonce(req: NonceRequest):
    nonce = secrets.token_hex(16)
    await _nonce_set(req.wallet, nonce)
    return {"nonce": nonce, "message": f"MAXIA login: {nonce}"}


@router.post("/verify")
async def verify_signature(req: AuthRequest):
    _check_brute_force(req.wallet)

    stored_nonce = await _nonce_get(req.wallet)
    if not stored_nonce:
        _record_failed(req.wallet)
        raise HTTPException(401, "Nonce introuvable.")
    if stored_nonce != req.nonce:
        _record_failed(req.wallet)
        raise HTTPException(401, "Nonce invalide.")

    # Anti-replay: verifier que le nonce n'a pas deja ete utilise
    if await _nonce_is_used(req.nonce):
        raise HTTPException(401, "Nonce deja utilise (replay detecte).")

    message = f"MAXIA login: {stored_nonce}".encode()
    try:
        pub_bytes = base58.b58decode(req.wallet)
        vk = VerifyKey(pub_bytes)
        sig_bytes = bytes.fromhex(req.signature) if len(req.signature) == 128 else base58.b58decode(req.signature)
        vk.verify(message, sig_bytes)
    except (BadSignatureError, Exception) as e:
        _record_failed(req.wallet)
        logger.warning("Signature verification failed: %s", e)
        raise HTTPException(401, "Authentication failed")

    # Consommer le nonce (anti-replay) — Redis TTL handles cleanup automatically
    await _nonce_delete(req.wallet)
    await _nonce_mark_used(req.nonce)

    # Detecter premier login (nouveau wallet)
    first_login = False
    try:
        from database import db
        rows = await db.raw_execute_fetchall(
            "SELECT 1 FROM agents WHERE wallet=? LIMIT 1", (req.wallet,)
        )
        if not rows:
            first_login = True
            # Notifier Alexis via Telegram
            try:
                from alerts import _send_private
                await _send_private(
                    f"\U0001f195 <b>Nouveau wallet connecte</b>\n\n"
                    f"Wallet : <code>{req.wallet[:8]}...{req.wallet[-4:]}</code>\n"
                    f"Premier login detecte."
                )
            except Exception:
                pass
    except Exception:
        pass

    token = create_session_token(req.wallet)
    return {"ok": True, "wallet": req.wallet, "session_token": token, "first_login": first_login}


async def require_auth(
    x_wallet: str = Header(None, alias="X-Wallet"),
    x_signature: str = Header(None, alias="X-Signature"),
    x_nonce: str = Header(None, alias="X-Nonce"),
) -> str:
    if not x_wallet or not x_signature or not x_nonce:
        raise HTTPException(401, "Headers manquants: X-Wallet, X-Signature, X-Nonce")

    _check_brute_force(x_wallet)

    # Anti-replay: verifier que ce nonce+signature n'a pas deja ete utilise
    replay_key = f"{x_wallet}:{x_nonce}"
    if await _nonce_is_used(replay_key):
        raise HTTPException(401, "Signature deja utilisee (replay detecte).")

    # Verifier que le nonce a ete delivre par notre serveur
    stored_nonce = await _nonce_get(x_wallet)
    if not stored_nonce:
        _record_failed(x_wallet)
        raise HTTPException(401, "Nonce introuvable — demandez /api/auth/nonce d'abord.")
    if stored_nonce != x_nonce:
        _record_failed(x_wallet)
        raise HTTPException(401, "Nonce invalide.")

    message = f"MAXIA login: {x_nonce}".encode()
    try:
        pub_bytes = base58.b58decode(x_wallet)
        vk = VerifyKey(pub_bytes)
        sig_bytes = bytes.fromhex(x_signature) if len(x_signature) == 128 else base58.b58decode(x_signature)
        vk.verify(message, sig_bytes)
    except Exception:
        _record_failed(x_wallet)
        raise HTTPException(401, "Authentication failed")

    # Consommer le nonce apres usage reussi — Redis TTL handles cleanup
    await _nonce_delete(x_wallet)
    await _nonce_mark_used(replay_key)

    return x_wallet


# ── Flexible Auth: JWT session OR DID signature ──

async def require_auth_flexible(
    request: "Request" = None,
    authorization: str = Header(None, alias="Authorization"),
    x_wallet: str = Header(None, alias="X-Wallet"),
    x_agent_did: str = Header(None, alias="X-Agent-DID"),
    x_agent_sig: str = Header(None, alias="X-Agent-Sig"),
    x_agent_ts: str = Header(None, alias="X-Agent-Ts"),
    x_api_key: str = Header(None, alias="X-API-Key"),
) -> dict:
    """Auth flexible pour les endpoints publics.
    Accepte (par ordre de priorite) :
      1) DID signature (X-Agent-DID + X-Agent-Sig + X-Agent-Ts)
      2) Bearer session token (Authorization: Bearer ...)
      3) X-API-Key (sandbox/public API)
      4) Wallet signature (X-Wallet + X-Signature + X-Nonce) [legacy]

    Returns: {"wallet": str, "did": str|None, "auth_method": str}
    """
    # 1) DID signature auth
    if x_agent_did and x_agent_sig and x_agent_ts:
        agent_info = await require_agent_sig_auth(
            x_agent_did=x_agent_did,
            x_agent_sig=x_agent_sig,
            x_agent_ts=x_agent_ts,
        )
        return {
            "wallet": agent_info["wallet"],
            "did": agent_info["did"],
            "agent_id": agent_info["agent_id"],
            "auth_method": "did_signature",
        }

    # 2) Bearer session token
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1]
        wallet = verify_session_token(token)
        return {"wallet": wallet, "did": None, "auth_method": "session_token"}

    # 3) X-API-Key (for public API / sandbox) — verify key exists in DB
    if x_api_key:
        try:
            from database import db
            rows = await db.raw_execute_fetchall(
                "SELECT wallet, agent_id FROM agents WHERE api_key=? AND status='active' LIMIT 1",
                (x_api_key,)
            )
            if rows:
                agent = dict(rows[0])
                return {
                    "wallet": agent.get("wallet", x_api_key),
                    "did": None,
                    "agent_id": agent.get("agent_id"),
                    "auth_method": "api_key",
                }
            # Key not found in DB — reject
            raise HTTPException(401, "Invalid API key")
        except HTTPException:
            raise
        except Exception:
            # DB unavailable — fallback to sandbox mode only
            from config import SANDBOX_MODE
            if SANDBOX_MODE:
                return {"wallet": x_api_key, "did": None, "auth_method": "api_key"}
            raise HTTPException(401, "API key verification unavailable")

    raise HTTPException(401,
        "Authentication required. Use one of: "
        "Authorization: Bearer <token>, "
        "X-Agent-DID + X-Agent-Sig + X-Agent-Ts, "
        "or X-API-Key")


# ── CEO API Auth (PC local <-> VPS) ──

async def require_ceo_auth(
    request: "Request" = None,
    x_ceo_key: str = Header(None, alias="X-CEO-Key"),
) -> str:
    """Verifie l'authentification CEO (cle partagee PC <-> VPS + IP whitelist)."""
    from config import CEO_API_KEY, CEO_ALLOWED_IPS

    if not CEO_API_KEY:
        raise HTTPException(500, "CEO_API_KEY not configured on server")
    if not x_ceo_key:
        raise HTTPException(401, "Missing X-CEO-Key header")
    if not hmac.compare_digest(x_ceo_key, CEO_API_KEY):
        raise HTTPException(403, "Invalid CEO API key")

    # IP whitelist (optionnel)
    if CEO_ALLOWED_IPS and request:
        ip = request.client.host if request.client else ""
        allowed = [i.strip() for i in CEO_ALLOWED_IPS.split(",") if i.strip()]
        if allowed and ip not in allowed:
            raise HTTPException(403, f"IP {ip} not in CEO whitelist")

    return "ceo"


async def require_session_auth(
    authorization: str = Header(None, alias="Authorization"),
) -> str:
    """Dependency that accepts Authorization: Bearer <session_token> and validates it."""
    if not authorization:
        raise HTTPException(401, "Missing Authorization header")
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(401, "Invalid Authorization format. Expected: Bearer <token>")
    token = parts[1]
    return verify_session_token(token)


# ── Agent DID Signature Auth (AIP-inspired) ──

async def require_agent_sig_auth(
    x_agent_did: str = Header(None, alias="X-Agent-DID"),
    x_agent_sig: str = Header(None, alias="X-Agent-Sig"),
    x_agent_ts: str = Header(None, alias="X-Agent-Ts"),
) -> dict:
    """Auth par signature ed25519 — alternative a X-API-Key.
    L'agent signe le message '{did}:{timestamp}' avec sa cle privee.
    Le serveur verifie avec la cle publique stockee dans le DID Document.

    Headers requis:
      X-Agent-DID: did:web:maxiaworld.app:agent:abc123
      X-Agent-Sig: base58(ed25519_signature)
      X-Agent-Ts: 2026-03-27T12:00:00Z

    Returns: dict avec agent_id, did, api_key, wallet
    """
    if not x_agent_did or not x_agent_sig or not x_agent_ts:
        raise HTTPException(401,
            "Missing headers: X-Agent-DID, X-Agent-Sig, X-Agent-Ts required for signature auth")

    # Verifier le timestamp (max 60s de skew)
    try:
        from datetime import datetime, timezone
        ts_dt = datetime.fromisoformat(x_agent_ts.replace("Z", "+00:00"))
        age = abs((datetime.now(timezone.utc) - ts_dt).total_seconds())
        if age > 60:
            raise HTTPException(401, f"Timestamp too old ({int(age)}s). Max 60s skew.")
    except ValueError:
        raise HTTPException(401, "Invalid X-Agent-Ts format. Expected ISO 8601.")

    # Resoudre le DID → cle publique
    try:
        from database import db
        rows = await db.raw_execute_fetchall(
            "SELECT agent_id, api_key, wallet, public_key, status FROM agent_permissions WHERE did=?",
            (x_agent_did,))
        if not rows:
            raise HTTPException(401, f"DID not found: {x_agent_did}")

        agent = dict(rows[0])
        if agent.get("status") == "revoked":
            raise HTTPException(403, "Agent revoked")
        if not agent.get("public_key"):
            raise HTTPException(401, "No public key registered for this agent. Use X-API-Key instead.")
    except HTTPException:
        raise
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"DID resolution error: {e}")
        raise HTTPException(500, "DID resolution failed")

    # Verifier la signature
    message = f"{x_agent_did}:{x_agent_ts}".encode()
    try:
        sig_bytes = base58.b58decode(x_agent_sig)
        pk_bytes = base58.b58decode(agent["public_key"])
        vk = VerifyKey(pk_bytes)
        vk.verify(message, sig_bytes)
    except Exception:
        _record_failed(x_agent_did)
        raise HTTPException(401, "Signature verification failed")

    return {
        "agent_id": agent["agent_id"],
        "did": x_agent_did,
        "api_key": agent["api_key"],
        "wallet": agent["wallet"],
    }
