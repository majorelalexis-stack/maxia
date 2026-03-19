"""MAXIA Auth V12 - Signature Solana ed25519 + anti-replay"""
import os, time, secrets
from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel
from nacl.signing import VerifyKey
from nacl.exceptions import BadSignatureError
import base58

router = APIRouter(prefix="/api/auth", tags=["auth"])
NONCES: dict = {}
NONCE_TTL = 300

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
        raise HTTPException(401, f"Signature invalide: {e}")

    # Consommer le nonce (anti-replay)
    del NONCES[req.wallet]
    _USED_NONCES[req.nonce] = time.time()
    if len(_USED_NONCES) > _USED_NONCES_MAX:
        _cleanup_used_nonces()

    return {"ok": True, "wallet": req.wallet}


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
