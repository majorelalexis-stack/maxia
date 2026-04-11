"""Demo — uAgents v0.24.1 → MAXIA A2A handshake + JSON-RPC task.

Shows how an external ``uagents-adapter[a2a]`` client can:

  1. Generate an ed25519 identity (did + keypair)
  2. Hit MAXIA ``/api/agent/a2a/adapter-config`` to discover the
     ``A2AAgentConfig`` MAXIA expects
  3. Perform a signed handshake at ``/api/agent/a2a/handshake``
  4. Send a real A2A ``message/send`` JSON-RPC task to ``/a2a`` and
     print the MAXIA response

Run::

    pip install "uagents-adapter[a2a]>=0.24.1" httpx pynacl base58
    python demos/uagents_maxia_a2a.py

Or set ``MAXIA_BASE_URL=http://localhost:8000`` to test against a
local backend instance.

No MAXIA SDK is required — this demo uses raw httpx to keep the
example transparent. In production you'd use ``langchain-maxia`` or
``llama-index-maxia`` for the client side.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid

import httpx

try:
    from nacl.signing import SigningKey
    import base58
except ImportError as e:
    raise SystemExit(
        "Missing deps. Run: pip install pynacl base58 httpx\n"
        f"(reason: {e})"
    )


MAXIA_BASE_URL = os.getenv("MAXIA_BASE_URL", "https://maxiaworld.app").rstrip("/")


def _make_identity(agent_name: str = "demo-agent") -> dict:
    """Generate a fresh ed25519 identity for the demo."""
    sk = SigningKey.generate()
    pub_b58 = base58.b58encode(bytes(sk.verify_key)).decode()
    did = f"did:web:example.com:agent:{agent_name}"
    return {"did": did, "signing_key": sk, "public_key_b58": pub_b58}


def _sign_handshake(identity: dict) -> dict:
    """Produce the handshake request payload with a valid signature."""
    nonce = uuid.uuid4().hex
    ts = int(time.time())
    canonical = f"a2a-handshake-v1|{identity['did']}|{nonce}|{ts}".encode()
    signed = identity["signing_key"].sign(canonical)
    signature_b58 = base58.b58encode(signed.signature).decode()
    return {
        "initiator_did": identity["did"],
        "initiator_pubkey": identity["public_key_b58"],
        "nonce": nonce,
        "timestamp": ts,
        "signature": signature_b58,
        "metadata": {"client": "uagents-adapter-demo", "version": "0.24.1"},
    }


async def _fetch_adapter_config(client: httpx.AsyncClient) -> dict:
    url = f"{MAXIA_BASE_URL}/api/agent/a2a/adapter-config"
    resp = await client.get(url)
    resp.raise_for_status()
    return resp.json()


async def _handshake(client: httpx.AsyncClient, identity: dict) -> dict:
    url = f"{MAXIA_BASE_URL}/api/agent/a2a/handshake"
    payload = _sign_handshake(identity)
    resp = await client.post(url, json=payload)
    if resp.status_code != 200:
        raise RuntimeError(
            f"handshake failed: HTTP {resp.status_code} — {resp.text[:300]}"
        )
    return resp.json()


async def _send_a2a_task(
    client: httpx.AsyncClient,
    user_text: str,
    session_id: str,
) -> dict:
    url = f"{MAXIA_BASE_URL}/a2a"
    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"type": "text", "text": user_text}],
            },
            "metadata": {"session_id": session_id, "source": "uagents-demo"},
        },
    }
    resp = await client.post(url, json=payload)
    resp.raise_for_status()
    return resp.json()


async def main() -> int:
    print(f"[demo] MAXIA base URL = {MAXIA_BASE_URL}")

    identity = _make_identity("uagents-demo")
    print(f"[demo] generated identity: {identity['did']}")

    async with httpx.AsyncClient(timeout=30.0) as client:
        print("[demo] fetching A2A adapter-config from MAXIA...")
        cfg = await _fetch_adapter_config(client)
        print(f"       name           = {cfg['config']['name']}")
        print(f"       url            = {cfg['config']['url']}")
        print(f"       specialties    = {cfg['config']['specialties'][:5]}")
        print(f"       skills (total) = {len(cfg['config']['skills'])}")
        print(f"       server did     = {cfg['config']['did']}")

        print("\n[demo] performing ed25519 handshake...")
        session = await _handshake(client, identity)
        print(f"       session_id     = {session['session_id']}")
        print(f"       expires_at     = {session['expires_at']}")
        print(f"       server_did     = {session['server_did']}")
        print(f"       rate_limit/min = {session['rate_limit']['requests_per_minute']}")

        print("\n[demo] sending A2A task: 'discover crypto trading services under $1'")
        result = await _send_a2a_task(
            client,
            user_text="discover crypto trading services under $1",
            session_id=session["session_id"],
        )
        print("\n[demo] A2A response:")
        print(json.dumps(result, indent=2, default=str)[:1500])

    print("\n[demo] done — full handshake + task roundtrip completed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\n[demo] cancelled")
        raise SystemExit(130)
