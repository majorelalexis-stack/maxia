"""Wrapper to expose a LlamaIndex ``TrustedAgentWorker`` on MAXIA.

The upstream ``llama-index-agent-agentmesh 0.2.0`` package ships a
``TrustedAgentWorker`` class that runs a LlamaIndex agent with an
ed25519 identity and capability-gated execution. This wrapper takes
that worker, reuses its identity (or issues one via
:class:`MaxiaMeshIdentity`), and registers the worker as a MAXIA
skill so peers can discover and pay for it.

The wrapper is intentionally dependency-soft: the ``llama-index-agent-
agentmesh`` import is lazy. If you don't have it installed, you can
still use :class:`TrustedWorkerAdapter` with a custom worker that
exposes ``run(payload: dict) -> Any``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional, Protocol

from llama_index_maxia.client import MaxiaMeshClient
from llama_index_maxia.identity import MaxiaMeshIdentity

__all__ = ["TrustedWorkerAdapter", "wrap_trusted_worker", "WorkerLike"]

_log = logging.getLogger("llama_index_maxia")


class WorkerLike(Protocol):
    """Duck-typed interface any worker can satisfy."""

    async def run(self, payload: dict) -> Any: ...  # pragma: no cover


@dataclass
class TrustedWorkerAdapter:
    """Bridge between a LlamaIndex worker and MAXIA's mesh endpoints.

    Attributes
    ----------
    worker:
        Any object with an async ``run(payload) -> Any`` method. The
        upstream ``TrustedAgentWorker`` satisfies this.
    client:
        A :class:`MaxiaMeshClient` configured with the worker's identity.
    name / description / capabilities / price_usdc / input_schema:
        Metadata forwarded to MAXIA at registration time.
    """

    worker: Any
    client: MaxiaMeshClient
    name: str
    description: str
    capabilities: list[str]
    price_usdc: float = 0.0
    input_schema: Optional[dict] = None
    endpoint_url: str = ""
    _registered_skill_id: Optional[str] = None

    async def register_on_maxia(self) -> dict:
        """Register the wrapped worker as a MAXIA skill.

        Returns the backend's registration response. The returned
        ``skill_id`` is cached on the adapter for later calls.
        """
        result = await self.client.register_trusted_agent(
            name=self.name,
            description=self.description,
            capabilities=self.capabilities,
            price_usdc=self.price_usdc,
            input_schema=self.input_schema,
            endpoint_url=self.endpoint_url,
        )
        skill = (result or {}).get("skill") or {}
        self._registered_skill_id = skill.get("id") or skill.get("skill_id")
        _log.info(
            "[llama_index_maxia] registered worker %s on MAXIA as skill=%s",
            self.name, self._registered_skill_id,
        )
        return result

    async def run_locally(self, payload: dict) -> Any:
        """Run the wrapped worker in-process without touching MAXIA.

        Useful for unit tests or when MAXIA only acts as a discovery
        layer and the actual execution happens on your own infrastructure.
        """
        return await self.worker.run(payload or {})

    async def close(self) -> None:
        await self.client.close()

    @property
    def skill_id(self) -> Optional[str]:
        return self._registered_skill_id


def wrap_trusted_worker(
    worker: Any,
    *,
    name: Optional[str] = None,
    description: str = "",
    capabilities: Optional[list[str]] = None,
    price_usdc: float = 0.0,
    input_schema: Optional[dict] = None,
    endpoint_url: str = "",
    identity: Optional[MaxiaMeshIdentity] = None,
    api_key: str = "",
    base_url: str = "https://maxiaworld.app",
) -> TrustedWorkerAdapter:
    """Factory: wrap a worker in a :class:`TrustedWorkerAdapter`.

    Parameters
    ----------
    worker:
        Any object with an async ``run(payload) -> Any`` method. If the
        worker comes from ``llama-index-agent-agentmesh`` and has an
        ``identity`` attribute (``CMVKIdentity`` or similar), we will
        auto-extract its DID + private key to reuse as the MAXIA
        identity — so a single key pair works on both sides.
    identity:
        Override the auto-extraction with an explicit identity.
    name:
        Defaults to ``getattr(worker, "name", "trusted-worker")``.
    """
    derived_name = name or getattr(worker, "name", None) or "trusted-worker"

    if identity is None:
        identity = _try_extract_identity(worker) or MaxiaMeshIdentity.generate(
            agent_name=derived_name,
        )

    client = MaxiaMeshClient(
        identity=identity,
        api_key=api_key,
        base_url=base_url,
    )

    return TrustedWorkerAdapter(
        worker=worker,
        client=client,
        name=str(derived_name),
        description=description or f"Trusted LlamaIndex worker {derived_name}",
        capabilities=list(capabilities or []),
        price_usdc=float(price_usdc),
        input_schema=input_schema,
        endpoint_url=endpoint_url,
    )


def _try_extract_identity(worker: Any) -> Optional[MaxiaMeshIdentity]:
    """Best-effort: pull an ed25519 key from a LlamaIndex worker.

    The upstream AgentMesh worker stores identity under different names
    depending on the release. We try a few common attribute paths and
    return ``None`` if nothing matches — the caller will then generate
    a fresh identity.
    """
    candidates = [
        getattr(worker, "identity", None),
        getattr(worker, "_identity", None),
        getattr(worker, "cmvk_identity", None),
        getattr(worker, "key", None),
    ]
    for candidate in candidates:
        if candidate is None:
            continue
        key_hex = _extract_private_key_hex(candidate)
        if not key_hex:
            continue
        did = _extract_did(candidate) or (
            f"did:web:maxiaworld.app:agent:{getattr(worker, 'name', 'imported')}"
        )
        return MaxiaMeshIdentity(
            did=did,
            private_key_hex=key_hex,
            agent_name=getattr(worker, "name", "imported") or "imported",
        )
    return None


def _extract_private_key_hex(obj: Any) -> Optional[str]:
    """Poke common attribute names to find a 64-char hex or bytes."""
    for attr in ("private_key_hex", "private_key", "secret_key", "_private_key"):
        val = getattr(obj, attr, None)
        if isinstance(val, str) and len(val) == 64:
            try:
                int(val, 16)
                return val
            except ValueError:
                pass
        if isinstance(val, (bytes, bytearray)) and len(val) == 32:
            return bytes(val).hex()
    # nacl SigningKey path
    try:
        from nacl.signing import SigningKey
        if isinstance(obj, SigningKey):
            return obj.encode().hex()
    except Exception:
        pass
    return None


def _extract_did(obj: Any) -> Optional[str]:
    for attr in ("did", "identifier", "_did"):
        val = getattr(obj, attr, None)
        if isinstance(val, str) and val.startswith("did:"):
            return val
    return None
