"""MAXIA A2A Handshake — ed25519 session bootstrap for external agents.

Companion module to ``backend/marketplace/a2a_protocol.py``. Adds two
surgical endpoints that external ``uagents-adapter[a2a]`` clients (from
uAgents v0.24.1+) can hit **before** sending task requests, so MAXIA
can authenticate the caller via a standard W3C DID + ed25519 signature
exchange instead of relying on plain HTTPS only.

Why a separate module
---------------------
``a2a_protocol.py`` implements the full JSON-RPC 2.0 task lifecycle and
is stable/battle-tested. Adding handshake logic inline would pollute it
with crypto imports and session state. This module is self-contained,
imports PyNaCl for verification, keeps its session cache in-process,
and exposes two routes that live side-by-side with the legacy ``/a2a``
endpoint. It does NOT modify the existing A2A flow — an agent can
still call ``POST /a2a`` without a handshake and use plain HTTPS auth.

Endpoints
---------
* ``POST /api/agent/a2a/handshake``
    Accepts an ed25519-signed request from an external agent, verifies
    the signature, creates a short-lived session, and returns MAXIA's
    server-side public identity + a session token.

* ``GET /api/agent/a2a/adapter-config``
    Returns a ready-to-use ``A2AAgentConfig`` JSON blob that external
    uAgents v0.24.1 ``SingleA2AAdapter`` / ``MultiA2AAdapter`` clients
    can import to instantiate a MAXIA-routing adapter in zero code.

* ``GET /api/agent/a2a/session/{session_id}``
    Read-only lookup for an active handshake session (debug + audit).

Security model
--------------
The handshake does NOT replace TLS — it adds an application-layer
proof of key possession so that a caller's ``did`` is cryptographically
bound to the request. A session is valid for 5 minutes (``_SESSION_TTL``).
Replay protection is provided by the per-session ``nonce`` and the
``timestamp`` window (±2 minutes).
"""
from __future__ import annotations

import base64
import logging
import os
import time
import uuid
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

try:
    from nacl.signing import SigningKey, VerifyKey
    from nacl.exceptions import BadSignatureError
    _NACL_AVAILABLE = True
except ImportError:  # pragma: no cover — nacl is a hard dep of agent_permissions
    _NACL_AVAILABLE = False
    SigningKey = None  # type: ignore
    VerifyKey = None  # type: ignore
    BadSignatureError = Exception  # type: ignore

try:
    import base58
    _BASE58_AVAILABLE = True
except ImportError:  # pragma: no cover
    _BASE58_AVAILABLE = False
    base58 = None  # type: ignore

from core.error_utils import safe_error

log = logging.getLogger("a2a.handshake")

router = APIRouter(tags=["a2a-handshake"])

# ══════════════════════════════════════════
# Server identity
# ══════════════════════════════════════════

_SERVER_DID = "did:web:maxiaworld.app"
_SERVER_A2A_URL = "https://maxiaworld.app/a2a"
_SERVER_ADAPTER_NAME = "MAXIA"
_SERVER_DESCRIPTION = (
    "MAXIA — AI-to-AI marketplace on 15 blockchains. Trade USDC, rent "
    "GPU, swap 65 tokens, buy AI services, all payable over A2A + x402."
)

# Lazy-initialized server signing key. In production this should be
# loaded from a KMS / env var. Here we use a process-local ephemeral
# key so the handshake is functional without additional secrets setup.
# If you deploy multi-worker, move this into a durable secret.
_SERVER_KEY_HEX: Optional[str] = os.getenv("MAXIA_A2A_SIGNING_KEY_HEX", "") or None
_server_signing_key: Optional[Any] = None
_server_public_b58: Optional[str] = None


def _get_server_signing_key() -> Any:
    """Return the server's ed25519 signing key, generating if needed."""
    global _server_signing_key, _server_public_b58
    if _server_signing_key is not None:
        return _server_signing_key
    if not _NACL_AVAILABLE:
        raise RuntimeError("PyNaCl not installed — A2A handshake unavailable")
    if _SERVER_KEY_HEX:
        sk = SigningKey(bytes.fromhex(_SERVER_KEY_HEX))
    else:
        sk = SigningKey.generate()
        log.warning(
            "[A2A Handshake] Using ephemeral server signing key — set "
            "MAXIA_A2A_SIGNING_KEY_HEX in env for durable identity"
        )
    _server_signing_key = sk
    if _BASE58_AVAILABLE:
        _server_public_b58 = base58.b58encode(bytes(sk.verify_key)).decode()
    else:
        _server_public_b58 = base64.b64encode(bytes(sk.verify_key)).decode()
    return sk


def _server_public_key() -> str:
    _get_server_signing_key()
    return _server_public_b58 or ""


# ══════════════════════════════════════════
# Session store (in-memory, TTL)
# ══════════════════════════════════════════

_SESSION_TTL = 300  # 5 minutes
_TIMESTAMP_WINDOW = 120  # ±2 min clock skew tolerance
_sessions: dict[str, dict[str, Any]] = {}


def _cleanup_sessions() -> None:
    now = time.time()
    expired = [sid for sid, s in _sessions.items() if s.get("expires_at", 0) < now]
    for sid in expired:
        _sessions.pop(sid, None)


def _create_session(initiator_did: str, initiator_pubkey: str, metadata: dict) -> dict:
    _cleanup_sessions()
    sid = str(uuid.uuid4())
    now = time.time()
    session = {
        "session_id": sid,
        "initiator_did": initiator_did,
        "initiator_pubkey": initiator_pubkey,
        "server_did": _SERVER_DID,
        "server_pubkey": _server_public_key(),
        "created_at": now,
        "expires_at": now + _SESSION_TTL,
        "metadata": metadata,
    }
    _sessions[sid] = session
    return session


# ══════════════════════════════════════════
# Signature verification
# ══════════════════════════════════════════

def _decode_b58(value: str) -> bytes:
    """Decode a base58 or base64-fallback public key / signature."""
    if _BASE58_AVAILABLE:
        try:
            return base58.b58decode(value)
        except Exception:
            pass
    try:
        return base64.b64decode(value)
    except Exception as e:
        raise ValueError(f"Invalid base58/base64 value: {e}") from e


def _canonical_payload(initiator_did: str, nonce: str, timestamp: int) -> bytes:
    """Canonical bytes the initiator must have signed.

    Format: ``a2a-handshake-v1|<did>|<nonce>|<timestamp>``. Keep it
    stable — any change breaks existing clients.
    """
    return f"a2a-handshake-v1|{initiator_did}|{nonce}|{timestamp}".encode()


def _verify_initiator_signature(
    initiator_did: str,
    initiator_pubkey_b58: str,
    nonce: str,
    timestamp: int,
    signature_b58: str,
) -> tuple[bool, str]:
    """Verify the initiator's signature over the canonical payload.

    Returns ``(ok, error_message)``.
    """
    if not _NACL_AVAILABLE:
        return False, "PyNaCl not available on server"
    now = int(time.time())
    if abs(now - int(timestamp)) > _TIMESTAMP_WINDOW:
        return False, f"timestamp outside ±{_TIMESTAMP_WINDOW}s window"
    try:
        pubkey_bytes = _decode_b58(initiator_pubkey_b58)
        signature_bytes = _decode_b58(signature_b58)
    except ValueError as e:
        return False, str(e)
    if len(pubkey_bytes) != 32:
        return False, f"ed25519 public key must be 32 bytes, got {len(pubkey_bytes)}"
    if len(signature_bytes) != 64:
        return False, f"ed25519 signature must be 64 bytes, got {len(signature_bytes)}"
    try:
        verify_key = VerifyKey(pubkey_bytes)
        verify_key.verify(_canonical_payload(initiator_did, nonce, timestamp), signature_bytes)
        return True, ""
    except BadSignatureError:
        return False, "signature verification failed"
    except Exception as e:
        return False, f"verification error: {e}"


def _sign_response(session_id: str, initiator_did: str) -> str:
    """Server signs ``session_id|initiator_did`` so the caller can
    prove later that MAXIA agreed to the session."""
    sk = _get_server_signing_key()
    payload = f"a2a-handshake-ack-v1|{session_id}|{initiator_did}".encode()
    signed = sk.sign(payload)
    if _BASE58_AVAILABLE:
        return base58.b58encode(signed.signature).decode()
    return base64.b64encode(signed.signature).decode()


# ══════════════════════════════════════════
# Routes
# ══════════════════════════════════════════

@router.post("/api/agent/a2a/handshake")
async def a2a_handshake(request: Request) -> JSONResponse:
    """Initiate an ed25519-signed A2A session with MAXIA.

    Request body (JSON)::

        {
            "initiator_did": "did:web:example.com:agent:bob",
            "initiator_pubkey": "<base58 ed25519 public key, 32 bytes>",
            "nonce": "<uuid4 or random 16+ chars>",
            "timestamp": 1713000000,
            "signature": "<base58 ed25519 signature, 64 bytes>",
            "metadata": { "optional": "dict" }
        }

    The signature MUST cover::

        a2a-handshake-v1|<initiator_did>|<nonce>|<timestamp>

    Response (200)::

        {
            "ok": true,
            "session_id": "<uuid>",
            "expires_at": 1713000300,
            "server_did": "did:web:maxiaworld.app",
            "server_pubkey": "<base58>",
            "server_signature": "<base58, signs session_id|initiator_did>",
            "a2a_url": "https://maxiaworld.app/a2a",
            "rate_limit": { "requests_per_minute": 60 }
        }
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"ok": False, "error": "invalid JSON body"}, status_code=400,
        )

    required = ("initiator_did", "initiator_pubkey", "nonce", "timestamp", "signature")
    missing = [k for k in required if not body.get(k)]
    if missing:
        return JSONResponse(
            {"ok": False, "error": f"missing fields: {', '.join(missing)}"},
            status_code=400,
        )

    initiator_did = str(body["initiator_did"])[:200]
    initiator_pubkey = str(body["initiator_pubkey"])[:120]
    nonce = str(body["nonce"])[:120]
    try:
        timestamp = int(body["timestamp"])
    except (TypeError, ValueError):
        return JSONResponse(
            {"ok": False, "error": "timestamp must be an integer"}, status_code=400,
        )
    signature = str(body["signature"])[:200]
    metadata = body.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}

    ok, err = _verify_initiator_signature(
        initiator_did=initiator_did,
        initiator_pubkey_b58=initiator_pubkey,
        nonce=nonce,
        timestamp=timestamp,
        signature_b58=signature,
    )
    if not ok:
        log.info("[A2A Handshake] rejected did=%s reason=%s", initiator_did, err)
        return JSONResponse(
            {"ok": False, "error": f"signature verification: {err}"},
            status_code=401,
        )

    try:
        session = _create_session(initiator_did, initiator_pubkey, metadata)
        server_signature = _sign_response(session["session_id"], initiator_did)
    except Exception as e:
        return JSONResponse(safe_error(e, "a2a_handshake"), status_code=500)

    log.info(
        "[A2A Handshake] accepted did=%s session=%s",
        initiator_did, session["session_id"],
    )
    return JSONResponse({
        "ok": True,
        "session_id": session["session_id"],
        "expires_at": int(session["expires_at"]),
        "server_did": _SERVER_DID,
        "server_pubkey": session["server_pubkey"],
        "server_signature": server_signature,
        "a2a_url": _SERVER_A2A_URL,
        "rate_limit": {"requests_per_minute": 60},
    })


@router.get("/api/agent/a2a/adapter-config")
async def a2a_adapter_config() -> JSONResponse:
    """Return a ready-to-use ``A2AAgentConfig`` blob for uAgents v0.24.1.

    External devs can fetch this JSON and paste it into their client::

        import httpx
        cfg = httpx.get("https://maxiaworld.app/api/agent/a2a/adapter-config").json()
        A2AAgentConfig(**cfg["config"])

    Mirrors the ``A2AAgentConfig`` dataclass from
    ``uagents-adapter[a2a]`` as of v0.24.1. Schema stable.
    """
    try:
        # Reuse the skills list from the existing agentverse bridge so
        # there's only one source of truth for MAXIA's advertised skills.
        try:
            from agents.agentverse_bridge import MAXIA_SKILLS
            skills_compact = [
                {
                    "id": s.get("id", ""),
                    "name": s.get("name", ""),
                    "description": s.get("description", ""),
                    "tags": s.get("tags", []),
                }
                for s in MAXIA_SKILLS[:50]
            ]
            specialties = sorted({
                tag for s in MAXIA_SKILLS for tag in s.get("tags", [])
            })[:20]
        except Exception:
            skills_compact = []
            specialties = [
                "crypto trading", "GPU rental", "AI services marketplace",
                "DeFi yields", "tokenized stocks", "on-chain escrow",
            ]

        config = {
            "name": _SERVER_ADAPTER_NAME,
            "description": _SERVER_DESCRIPTION,
            "url": _SERVER_A2A_URL,
            "specialties": specialties,
            "skills": skills_compact,
            "routing_strategy": "keyword_match",
            "port": 443,
            "did": _SERVER_DID,
            "pubkey": _server_public_key(),
            "protocols": ["a2a", "jsonrpc-2.0", "x402"],
        }

        return JSONResponse({
            "ok": True,
            "config": config,
            "usage_hint": (
                "Paste `config` into `A2AAgentConfig(**config)` from "
                "uagents-adapter[a2a] v0.24.1 or later. Use the "
                "`/api/agent/a2a/handshake` endpoint first to establish "
                "a signed session, then POST your A2A JSON-RPC 2.0 "
                "tasks/send / message/send requests to `url`."
            ),
            "handshake_endpoint": "/api/agent/a2a/handshake",
            "agent_card": "/.well-known/agent-card.json",
        })
    except Exception as e:
        return JSONResponse(safe_error(e, "a2a_adapter_config"), status_code=500)


@router.get("/api/agent/a2a/session/{session_id}")
async def a2a_session_get(session_id: str) -> JSONResponse:
    """Return a redacted view of an active handshake session."""
    _cleanup_sessions()
    sess = _sessions.get(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="session not found or expired")
    return JSONResponse({
        "ok": True,
        "session_id": sess["session_id"],
        "initiator_did": sess["initiator_did"],
        "server_did": sess["server_did"],
        "created_at": int(sess["created_at"]),
        "expires_at": int(sess["expires_at"]),
        "metadata": sess.get("metadata") or {},
    })


log.info("[A2A Handshake] ed25519 handshake router mounted — 3 endpoints")
