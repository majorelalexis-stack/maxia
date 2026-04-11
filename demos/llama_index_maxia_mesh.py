"""Demo — LlamaIndex AgentMesh × MAXIA end-to-end.

Shows the full trusted-agent lifecycle:

  1. Generate an ed25519 identity (DID + keypair)
  2. Register a LlamaIndex-compatible trusted agent as a MAXIA skill
  3. Discover registered agents (including our own)
  4. Execute a skill (free or paid) via a signed call
  5. Look up the agent's public info by DID

Run::

    pip install llama-index-maxia
    python demos/llama_index_maxia_mesh.py

To target a local backend::

    MAXIA_BASE_URL=http://localhost:8000 python demos/llama_index_maxia_mesh.py

The demo does NOT require ``llama-index-agent-agentmesh`` to be
installed — it uses a stub worker that satisfies the duck-typed
``run(payload) -> dict`` contract. If you have the upstream
package installed, the ``wrap_trusted_worker`` call works the same
with a real ``TrustedAgentWorker``.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

# Make the local package importable without installing it
_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG_SRC = os.path.abspath(os.path.join(_HERE, "..", "llama-index-maxia", "src"))
if _PKG_SRC not in sys.path:
    sys.path.insert(0, _PKG_SRC)

from llama_index_maxia import (  # noqa: E402
    MaxiaMeshClient,
    MaxiaMeshIdentity,
    wrap_trusted_worker,
)


MAXIA_BASE_URL = os.getenv("MAXIA_BASE_URL", "https://maxiaworld.app")


class StubCodeReviewer:
    """Minimal worker that satisfies ``run(payload) -> dict``.

    In real life you'd pass a
    ``llama_index.agent.agentmesh.TrustedAgentWorker`` here.
    """

    name = "stub-code-reviewer"

    async def run(self, payload: dict) -> dict:
        code = (payload or {}).get("code", "")
        return {
            "score": 80,
            "issues": [],
            "summary": f"Reviewed {len(code)} chars of code (stub).",
        }


async def main() -> int:
    print(f"[demo] MAXIA base URL = {MAXIA_BASE_URL}")

    # 1. Fresh identity
    identity = MaxiaMeshIdentity.generate(agent_name="demo-code-reviewer")
    print(f"[demo] identity did       = {identity.did}")
    print(f"[demo]          pubkey    = {identity.public_key_b58}")

    # 2. Wrap a worker (LlamaIndex-style) and register
    worker = StubCodeReviewer()
    adapter = wrap_trusted_worker(
        worker=worker,
        identity=identity,
        name="demo-code-reviewer",
        description="Reviews Python code for bugs, style and security issues.",
        capabilities=["code_review", "python", "security"],
        price_usdc=0.0,  # free for the demo — swap to 0.25 for paid
        input_schema={
            "type": "object",
            "properties": {"code": {"type": "string"}},
            "required": ["code"],
        },
        endpoint_url="",  # manifest-only for this demo
        base_url=MAXIA_BASE_URL,
    )

    print("\n[demo] registering worker on MAXIA...")
    reg = await adapter.register_on_maxia()
    print(json.dumps(reg, indent=2)[:1200])
    skill_id = (reg.get("skill") or {}).get("id")
    print(f"[demo] registered skill_id = {skill_id}")

    # 3. Discover — a fresh client without identity is enough for reads
    reader = MaxiaMeshClient(base_url=MAXIA_BASE_URL)
    print("\n[demo] discovering trusted agents with capability='code_review'...")
    agents = await reader.discover_trusted_agents(capability="code_review")
    for a in agents[:5]:
        print(
            f"  - {a.get('name')} ({a.get('price_usdc')} USDC) "
            f"— {a.get('did')}"
        )
    await reader.close()

    # 4. Execute the skill we just registered (free, no payment_tx)
    if skill_id:
        buyer_identity = MaxiaMeshIdentity.generate(agent_name="demo-buyer")
        buyer = MaxiaMeshClient(
            identity=buyer_identity, base_url=MAXIA_BASE_URL,
        )
        print(f"\n[demo] executing skill {skill_id} as {buyer_identity.did}...")
        exec_result = await buyer.execute_trusted_agent(
            skill_id=skill_id,
            payload={"code": "def add(a, b):\n    return a + b\n"},
        )
        print(json.dumps(exec_result, indent=2)[:1500])
        await buyer.close()

    # 5. Look up our own agent
    print(f"\n[demo] fetching agent {identity.did} via /api/agent/mesh/agent/...")
    info_client = MaxiaMeshClient(base_url=MAXIA_BASE_URL)
    info = await info_client.get_trusted_agent(identity.did)
    print(json.dumps(info, indent=2)[:1000])
    await info_client.close()

    await adapter.close()
    print("\n[demo] done — full register + discover + execute roundtrip completed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\n[demo] cancelled")
        raise SystemExit(130)
