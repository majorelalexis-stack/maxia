"""MAXIA Auth V12 - Signature Solana ed25519 + anti-replay"""
import os, time, secrets, hashlib, hmac
from fastapi import APIRouter, HTTPException, Header, Request
from pydantic import BaseModel
from nacl.signing import VerifyKey
from nacl.exceptions import BadSignatureError
import base58

router = APIRouter(prefix="/api/auth", tags=["auth"])
NONCES: dict = {}
NONCE_TTL = 300
_NONCES_MAX_SIZE = 5000


def _cleanup_nonces():
    """Prune expired nonces to prevent unbounded memory growth."""
    now = time.time()
    expired = [w for w, (_, exp) in NONCES.items() if exp < now]
    for w in expired:
        NONCES.pop(w, None)
    # If still too large after expiry cleanup, remove oldest entries
    if len(NONCES) > _NONCES_MAX_SIZE:
        sorted_keys = sorted(NONCES, key=lambda w: NONCES[w][1])
        for w in sorted_keys[:len(NONCES) - _NONCES_MAX_SIZE]:
            NONCES.pop(w, None)

# JWT-like session tokens (HMAC-signed, not ed25519)
_JWT_SECRET = os.getenv("JWT_SECRET", "")
_SANDBOX = os.getenv("SANDBOX_MODE", "false").lower() == "true"
if not _JWT_SECRET or len(_JWT_SECRET) < 16:
    if _SANDBOX:
        _JWT_SECRET = secrets.token_hex(32)
        print("[Auth] SANDBOX: JWT_SECRET ephemere genere")
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
    parts = token.split(":")
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

# Nonces deja utilises (anti-replay) — TTL de 10 min
_USED_NONCES: dict = {}
_USED_NONCES_MAX = 10000

# Tentatives echouees par wallet (rate limit brute force)
_FAILED_ATTEMPTS: dict = {}
_MAX_FAILED_ATTEMPTS = 10
_FAILED_WINDOW = 300  # 5 min


class NonceRequest(BaseModel):
    wallet: str

class AuthRequest(BaseModel):
    wallet: str
    signature: str
    nonce: str


def _cleanup_used_nonces():
    """Supprime les nonces expires du cache anti-replay."""
    now = time.time()
    expired = [n for n, t in _USED_NONCES.items() if now - t > NONCE_TTL * 2]
    for n in expired:
        _USED_NONCES.pop(n, None)


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
    if len(NONCES) > _NONCES_MAX_SIZE:
        _cleanup_nonces()
    nonce = secrets.token_hex(16)
    NONCES[req.wallet] = (nonce, time.time() + NONCE_TTL)
    return {"nonce": nonce, "message": f"MAXIA login: {nonce}"}


@router.post("/verify")
async def verify_signature(req: AuthRequest):
    _check_brute_force(req.wallet)

    entry = NONCES.get(req.wallet)
    if not entry:
        _record_failed(req.wallet)
        raise HTTPException(401, "Nonce introuvable.")
    nonce, expires = entry
    if time.time() > expires:
        del NONCES[req.wallet]
        raise HTTPException(401, "Nonce expire.")
    if nonce != req.nonce:
        _record_failed(req.wallet)
        raise HTTPException(401, "Nonce invalide.")

    # Anti-replay: verifier que le nonce n'a pas deja ete utilise
    if req.nonce in _USED_NONCES:
        raise HTTPException(401, "Nonce deja utilise (replay detecte).")

    message = f"MAXIA login: {nonce}".encode()
    try:
        pub_bytes = base58.b58decode(req.wallet)
        vk = VerifyKey(pub_bytes)
        sig_bytes = bytes.fromhex(req.signature) if len(req.signature) == 128 else base58.b58decode(req.signature)
        vk.verify(message, sig_bytes)
    except (BadSignatureError, Exception) as e:
        _record_failed(req.wallet)
        print(f"[Auth] Signature verification failed: {e}")
        raise HTTPException(401, "Authentication failed")

    # Consommer le nonce (anti-replay)
    del NONCES[req.wallet]
    _USED_NONCES[req.nonce] = time.time()
    if len(_USED_NONCES) > _USED_NONCES_MAX:
        _cleanup_used_nonces()

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
    if replay_key in _USED_NONCES:
        raise HTTPException(401, "Signature deja utilisee (replay detecte).")

    # Verifier que le nonce a ete delivre par notre serveur
    entry = NONCES.get(x_wallet)
    if not entry:
        _record_failed(x_wallet)
        raise HTTPException(401, "Nonce introuvable — demandez /api/auth/nonce d'abord.")
    stored_nonce, expires = entry
    if time.time() > expires:
        del NONCES[x_wallet]
        raise HTTPException(401, "Nonce expire.")
    if stored_nonce != x_nonce:
        _record_failed(x_wallet)
        raise HTTPException(401, "Nonce invalide.")

    message = f"MAXIA login: {x_nonce}".encode()
    try:
        pub_bytes = base58.b58decode(x_wallet)
        vk = VerifyKey(pub_bytes)
        sig_bytes = bytes.fromhex(x_signature) if len(x_signature) == 128 else base58.b58decode(x_signature)
        vk.verify(message, sig_bytes)
    except Exception as e:
        _record_failed(x_wallet)
        raise HTTPException(401, f"Auth echouee: {e}")

    # Consommer le nonce apres usage reussi
    del NONCES[x_wallet]
    _USED_NONCES[replay_key] = time.time()
    if len(_USED_NONCES) > _USED_NONCES_MAX:
        _cleanup_used_nonces()

    return x_wallet


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
