"""MAXIA × LlamaIndex AgentMesh server-side bridge.

Exposes 4 endpoints under ``/api/agent/mesh/*`` that the
``llama-index-maxia`` PyPI package talks to:

* ``POST /api/agent/mesh/register`` — register a trusted agent + skill
* ``GET  /api/agent/mesh/discover`` — list all trusted agents (public)
* ``POST /api/agent/mesh/execute``  — execute a peer agent's skill
* ``GET  /api/agent/mesh/agent/{did}`` — fetch one agent's public info

Every mutating call (``register``, ``execute``) is ed25519-signed by
the caller so MAXIA can prove who did what. The signature payload
format matches the one in ``llama_index_maxia/identity.py``::

    register: maxia-mesh-register-v1|<did>|<nonce>|<timestamp>
    execute:  maxia-mesh-execute-v1|<did>|<skill_id>|<nonce>|<timestamp>

Storage
-------
This module uses a small JSON file ``backend/.mesh_agents.json`` as
the authoritative store for now. Keeps it auditable, git-friendly, and
easy to migrate to SQLite later (schema is a flat dict of dicts keyed
by DID). No external dependencies beyond PyNaCl + base58, which are
already transitive deps of ``agent_permissions``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

try:
    from nacl.signing import VerifyKey
    from nacl.exceptions import BadSignatureError
    _NACL_OK = True
except ImportError:
    _NACL_OK = False
    VerifyKey = None  # type: ignore
    BadSignatureError = Exception  # type: ignore

try:
    import base58
    _BASE58_OK = True
except ImportError:
    _BASE58_OK = False
    base58 = None  # type: ignore

from core.error_utils import safe_error

log = logging.getLogger("llama_mesh_bridge")

router = APIRouter(prefix="/api/agent/mesh", tags=["llama-mesh"])

# ══════════════════════════════════════════
# Storage
# ══════════════════════════════════════════

_STORE_PATH = Path(__file__).parent.parent / ".mesh_agents.json"
_TIMESTAMP_WINDOW = 120  # ±2 min clock skew tolerance
_store_lock = asyncio.Lock()


def _load_store() -> dict:
    """Load the JSON store. Missing file = empty dict."""
    if not _STORE_PATH.exists():
        return {"agents": {}, "skills": {}, "executions": []}
    try:
        with open(_STORE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"agents": {}, "skills": {}, "executions": []}
        data.setdefault("agents", {})
        data.setdefault("skills", {})
        data.setdefault("executions", [])
        return data
    except (OSError, json.JSONDecodeError) as e:
        log.warning("[llama-mesh] store load error: %s", e)
        return {"agents": {}, "skills": {}, "executions": []}


def _save_store(data: dict) -> None:
    """Persist the store atomically (write to .tmp then rename)."""
    tmp = _STORE_PATH.with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        os.replace(tmp, _STORE_PATH)
    except OSError as e:
        log.error("[llama-mesh] store save error: %s", e)


# ══════════════════════════════════════════
# Signature verification
# ══════════════════════════════════════════

def _decode_b58(value: str) -> bytes:
    if _BASE58_OK:
        try:
            return base58.b58decode(value)
        except Exception:
            pass
    import base64
    return base64.b64decode(value)


def _verify_signature(
    pubkey_b58: str,
    payload: bytes,
    signature_b58: str,
) -> tuple[bool, str]:
    if not _NACL_OK:
        return False, "PyNaCl unavailable"
    try:
        pk = _decode_b58(pubkey_b58)
        sig = _decode_b58(signature_b58)
    except Exception as e:
        return False, f"decode error: {e}"
    if len(pk) != 32:
        return False, f"pubkey must be 32 bytes, got {len(pk)}"
    if len(sig) != 64:
        return False, f"signature must be 64 bytes, got {len(sig)}"
    try:
        VerifyKey(pk).verify(payload, sig)
        return True, ""
    except BadSignatureError:
        return False, "signature verification failed"
    except Exception as e:
        return False, f"verify error: {e}"


def _check_timestamp(ts: int) -> tuple[bool, str]:
    now = int(time.time())
    if abs(now - int(ts)) > _TIMESTAMP_WINDOW:
        return False, f"timestamp outside ±{_TIMESTAMP_WINDOW}s window"
    return True, ""


# ══════════════════════════════════════════
# Routes
# ══════════════════════════════════════════

@router.post("/register")
async def mesh_register(request: Request) -> JSONResponse:
    """Register a trusted LlamaIndex agent as a MAXIA skill.

    Body (JSON)::

        {
            "did": "did:web:...",
            "pubkey": "<base58 ed25519>",
            "name": "Code Reviewer",
            "description": "...",
            "capabilities": ["code_review", "python"],
            "price_usdc": 0.25,
            "input_schema": {...},
            "endpoint_url": "",
            "nonce": "<random>",
            "timestamp": 1713000000,
            "signature": "<base58 ed25519 signature>"
        }

    Signature covers: ``maxia-mesh-register-v1|<did>|<nonce>|<timestamp>``
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid JSON"}, status_code=400)

    required = ("did", "pubkey", "name", "nonce", "timestamp", "signature")
    missing = [k for k in required if not body.get(k)]
    if missing:
        return JSONResponse(
            {"ok": False, "error": f"missing fields: {', '.join(missing)}"},
            status_code=400,
        )

    did = str(body["did"])[:200]
    pubkey = str(body["pubkey"])[:120]
    nonce = str(body["nonce"])[:120]
    try:
        timestamp = int(body["timestamp"])
    except (TypeError, ValueError):
        return JSONResponse(
            {"ok": False, "error": "timestamp must be int"}, status_code=400,
        )
    signature = str(body["signature"])[:200]

    ok, err = _check_timestamp(timestamp)
    if not ok:
        return JSONResponse({"ok": False, "error": err}, status_code=400)

    canonical = f"maxia-mesh-register-v1|{did}|{nonce}|{timestamp}".encode()
    ok, err = _verify_signature(pubkey, canonical, signature)
    if not ok:
        log.info("[llama-mesh] register rejected did=%s reason=%s", did, err)
        return JSONResponse(
            {"ok": False, "error": f"signature: {err}"}, status_code=401,
        )

    skill_id = f"mesh_{uuid.uuid4().hex[:16]}"
    now = int(time.time())

    async with _store_lock:
        try:
            store = _load_store()
            store["agents"][did] = {
                "did": did,
                "pubkey": pubkey,
                "registered_at": now,
                "last_seen": now,
            }
            store["skills"][skill_id] = {
                "id": skill_id,
                "did": did,
                "name": str(body.get("name", ""))[:200],
                "description": str(body.get("description", ""))[:1000],
                "capabilities": [
                    str(c)[:60] for c in (body.get("capabilities") or [])
                ][:20],
                "price_usdc": float(body.get("price_usdc") or 0.0),
                "input_schema": body.get("input_schema") or {},
                "endpoint_url": str(body.get("endpoint_url") or "")[:500],
                "created_at": now,
                "updated_at": now,
                "executions": 0,
                "revenue_usdc": 0.0,
            }
            _save_store(store)
        except Exception as e:
            return JSONResponse(safe_error(e, "mesh_register"), status_code=500)

    log.info("[llama-mesh] registered did=%s skill=%s", did, skill_id)
    return JSONResponse({
        "ok": True,
        "skill": store["skills"][skill_id],
        "agent": store["agents"][did],
    })


@router.get("/discover")
async def mesh_discover(
    capability: str = "",
    max_price: float = 1000.0,
    limit: int = 20,
) -> JSONResponse:
    """List registered trusted agents. Public read."""
    try:
        store = _load_store()
        skills = list(store.get("skills", {}).values())

        if capability:
            cap_lower = capability.lower()
            skills = [
                s for s in skills
                if cap_lower in [c.lower() for c in s.get("capabilities", [])]
                or cap_lower in s.get("name", "").lower()
                or cap_lower in s.get("description", "").lower()
            ]
        if max_price < 1000.0:
            skills = [s for s in skills if s.get("price_usdc", 0) <= max_price]

        skills.sort(key=lambda s: -int(s.get("created_at", 0)))
        limit = max(1, min(int(limit), 100))
        return JSONResponse({
            "ok": True,
            "total": len(skills),
            "agents": skills[:limit],
        })
    except Exception as e:
        return JSONResponse(safe_error(e, "mesh_discover"), status_code=500)


@router.get("/agent/{did:path}")
async def mesh_get_agent(did: str) -> JSONResponse:
    """Fetch one registered trusted agent by DID."""
    try:
        store = _load_store()
        agent = store.get("agents", {}).get(did)
        if not agent:
            raise HTTPException(status_code=404, detail="agent not found")
        skills = [
            s for s in store.get("skills", {}).values()
            if s.get("did") == did
        ]
        return JSONResponse({
            "ok": True,
            "agent": agent,
            "skills": skills,
        })
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse(safe_error(e, "mesh_get_agent"), status_code=500)


@router.post("/execute")
async def mesh_execute(request: Request) -> JSONResponse:
    """Execute a peer agent's skill (signed call).

    Body (JSON)::

        {
            "did": "did:web:...",          # caller's DID
            "pubkey": "<base58>",
            "skill_id": "mesh_...",
            "payload": {...},
            "payment_tx": "",
            "nonce": "...",
            "timestamp": 1713000000,
            "signature": "<base58>"
        }

    Signature covers:
    ``maxia-mesh-execute-v1|<did>|<skill_id>|<nonce>|<timestamp>``

    The actual execution strategy depends on the skill's ``endpoint_url``:
    * If set → MAXIA forwards the payload as an HTTP POST and proxies the
      response.
    * If empty → MAXIA records an "execution request" in the store and
      returns a pending receipt. The agent owner is expected to poll
      ``/discover`` or a future ``/pending`` endpoint.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid JSON"}, status_code=400)

    required = ("did", "pubkey", "skill_id", "nonce", "timestamp", "signature")
    missing = [k for k in required if not body.get(k)]
    if missing:
        return JSONResponse(
            {"ok": False, "error": f"missing fields: {', '.join(missing)}"},
            status_code=400,
        )

    did = str(body["did"])[:200]
    pubkey = str(body["pubkey"])[:120]
    skill_id = str(body["skill_id"])[:120]
    nonce = str(body["nonce"])[:120]
    try:
        timestamp = int(body["timestamp"])
    except (TypeError, ValueError):
        return JSONResponse(
            {"ok": False, "error": "timestamp must be int"}, status_code=400,
        )
    signature = str(body["signature"])[:200]
    payload = body.get("payload") or {}
    payment_tx = str(body.get("payment_tx") or "")[:200]

    ok, err = _check_timestamp(timestamp)
    if not ok:
        return JSONResponse({"ok": False, "error": err}, status_code=400)

    canonical = (
        f"maxia-mesh-execute-v1|{did}|{skill_id}|{nonce}|{timestamp}"
    ).encode()
    ok, err = _verify_signature(pubkey, canonical, signature)
    if not ok:
        log.info(
            "[llama-mesh] execute rejected did=%s skill=%s reason=%s",
            did, skill_id, err,
        )
        return JSONResponse(
            {"ok": False, "error": f"signature: {err}"}, status_code=401,
        )

    async with _store_lock:
        store = _load_store()
        skill = store.get("skills", {}).get(skill_id)
        if not skill:
            return JSONResponse(
                {"ok": False, "error": "skill not found"}, status_code=404,
            )

        # Paid skill + missing payment_tx → reject
        if float(skill.get("price_usdc") or 0) > 0 and not payment_tx:
            return JSONResponse(
                {
                    "ok": False,
                    "error": "payment_tx required for paid skill",
                    "price_usdc": skill["price_usdc"],
                },
                status_code=402,  # Payment Required
            )

        execution_id = f"exec_{uuid.uuid4().hex[:16]}"
        execution_entry = {
            "id": execution_id,
            "skill_id": skill_id,
            "caller_did": did,
            "owner_did": skill.get("did"),
            "status": "pending",
            "payload_hash": _short_hash(payload),
            "payment_tx": payment_tx,
            "created_at": int(time.time()),
        }

        endpoint = skill.get("endpoint_url") or ""
        result: dict = {}
        if endpoint and endpoint.startswith("https://"):
            try:
                import httpx
                async with httpx.AsyncClient(timeout=20) as client:
                    resp = await client.post(endpoint, json=payload)
                    resp.raise_for_status()
                    result = resp.json() if resp.headers.get(
                        "content-type", ""
                    ).startswith("application/json") else {"text": resp.text[:1500]}
                execution_entry["status"] = "completed"
            except Exception as e:
                execution_entry["status"] = "failed"
                result = {"error": str(e)[:300]}
        else:
            result = {
                "status": "queued",
                "note": (
                    "This skill has no endpoint_url. The owner must "
                    "fetch pending executions and deliver the result."
                ),
            }

        # Update counters
        skill["executions"] = int(skill.get("executions", 0)) + 1
        if float(skill.get("price_usdc") or 0) > 0:
            skill["revenue_usdc"] = (
                float(skill.get("revenue_usdc") or 0)
                + float(skill.get("price_usdc") or 0)
            )
        skill["updated_at"] = int(time.time())
        store.setdefault("executions", []).append(execution_entry)
        # Cap executions log at last 500 entries
        if len(store["executions"]) > 500:
            store["executions"] = store["executions"][-500:]
        _save_store(store)

    log.info(
        "[llama-mesh] execute did=%s skill=%s status=%s",
        did, skill_id, execution_entry["status"],
    )
    return JSONResponse({
        "ok": True,
        "execution": execution_entry,
        "result": result,
        "skill": {
            "id": skill["id"],
            "name": skill["name"],
            "price_usdc": skill["price_usdc"],
            "owner_did": skill["did"],
        },
    })


def _short_hash(obj: Any) -> str:
    """Compact hash for audit — first 16 hex chars of SHA-256 over JSON."""
    import hashlib
    try:
        blob = json.dumps(obj, sort_keys=True, default=str).encode()
    except Exception:
        blob = repr(obj).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


log.info("[llama-mesh] LlamaIndex AgentMesh bridge mounted (4 endpoints)")
