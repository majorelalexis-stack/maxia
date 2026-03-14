"""MAXIA Auth V9 - Signature Solana ed25519"""
import os, time, secrets
from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel
from nacl.signing import VerifyKey
from nacl.exceptions import BadSignatureError
import base58

router = APIRouter(prefix="/api/auth", tags=["auth"])
NONCES: dict = {}
NONCE_TTL = 300

class NonceRequest(BaseModel):
    wallet: str

class AuthRequest(BaseModel):
    wallet: str
    signature: str
    nonce: str

@router.post("/nonce")
async def get_nonce(req: NonceRequest):
    nonce = secrets.token_hex(16)
    NONCES[req.wallet] = (nonce, time.time() + NONCE_TTL)
    return {"nonce": nonce, "message": f"MAXIA login: {nonce}"}

@router.post("/verify")
async def verify_signature(req: AuthRequest):
    entry = NONCES.get(req.wallet)
    if not entry:
        raise HTTPException(401, "Nonce introuvable.")
    nonce, expires = entry
    if time.time() > expires:
        del NONCES[req.wallet]
        raise HTTPException(401, "Nonce expire.")
    if nonce != req.nonce:
        raise HTTPException(401, "Nonce invalide.")
    message = f"MAXIA login: {nonce}".encode()
    try:
        pub_bytes = base58.b58decode(req.wallet)
        vk = VerifyKey(pub_bytes)
        sig_bytes = bytes.fromhex(req.signature) if len(req.signature) == 128 else base58.b58decode(req.signature)
        vk.verify(message, sig_bytes)
    except (BadSignatureError, Exception) as e:
        raise HTTPException(401, f"Signature invalide: {e}")
    del NONCES[req.wallet]
    return {"ok": True, "wallet": req.wallet}

async def require_auth(
    x_wallet: str = Header(None, alias="X-Wallet"),
    x_signature: str = Header(None, alias="X-Signature"),
    x_nonce: str = Header(None, alias="X-Nonce"),
) -> str:
    if not x_wallet or not x_signature or not x_nonce:
        raise HTTPException(401, "Headers manquants: X-Wallet, X-Signature, X-Nonce")
    message = f"MAXIA login: {x_nonce}".encode()
    try:
        pub_bytes = base58.b58decode(x_wallet)
        vk = VerifyKey(pub_bytes)
        sig_bytes = bytes.fromhex(x_signature) if len(x_signature) == 128 else base58.b58decode(x_signature)
        vk.verify(message, sig_bytes)
    except Exception as e:
        raise HTTPException(401, f"Auth echouee: {e}")
    return x_wallet
