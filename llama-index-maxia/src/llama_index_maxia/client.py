"""MaxiaMeshClient — async HTTP client for ``/api/agent/mesh/*``.

Wraps the 4 backend endpoints that the ``llama_mesh_bridge`` exposes:

* ``POST /api/agent/mesh/register``      — sign + register a trusted agent
* ``GET  /api/agent/mesh/discover``      — list all trusted agents (public)
* ``POST /api/agent/mesh/execute``       — sign + execute a peer's skill
* ``GET  /api/agent/mesh/agent/{did}``   — fetch one agent's public info

The client enforces that every write is signed with the caller's
:class:`llama_index_maxia.identity.MaxiaMeshIdentity`. Reads are
free — no signing required.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from typing import Any, Optional

import httpx

from llama_index_maxia.identity import MaxiaMeshIdentity

__all__ = ["MaxiaMeshClient"]

_log = logging.getLogger("llama_index_maxia")

_DEFAULT_BASE_URL = "https://maxiaworld.app"
_DEFAULT_TIMEOUT = 30.0
_MAX_RETRIES = 2


class MaxiaMeshClient:
    """Async client for MAXIA mesh endpoints.

    Parameters
    ----------
    identity:
        Your :class:`MaxiaMeshIdentity`. Required for ``register_*`` and
        ``execute_*``; optional for read-only methods like
        :meth:`discover_trusted_agents`.
    api_key:
        MAXIA API key. Falls back to ``MAXIA_API_KEY`` env var.
    base_url:
        MAXIA base URL. Defaults to prod (``https://maxiaworld.app``).
    timeout:
        HTTP timeout in seconds (default 30).
    max_retries:
        Network retries on transient errors (default 2).
    """

    def __init__(
        self,
        identity: Optional[MaxiaMeshIdentity] = None,
        api_key: str = "",
        base_url: str = _DEFAULT_BASE_URL,
        timeout: float = _DEFAULT_TIMEOUT,
        max_retries: int = _MAX_RETRIES,
    ) -> None:
        self.identity = identity
        self.api_key = api_key or os.getenv("MAXIA_API_KEY", "")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self._client: Optional[httpx.AsyncClient] = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {"Accept": "application/json"}
        if self.api_key:
            h["X-API-Key"] = self.api_key
        return h

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=self._headers(),
                timeout=self.timeout,
            )
        return self._client

    async def _get(self, path: str, params: Optional[dict] = None) -> Any:
        last: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                client = await self._get_client()
                resp = await client.get(path, params=params)
                resp.raise_for_status()
                return resp.json()
            except (httpx.ConnectError, httpx.ReadTimeout) as e:
                last = e
                if attempt < self.max_retries:
                    await asyncio.sleep(0.5 * (attempt + 1))
        raise last  # type: ignore[misc]

    async def _post(self, path: str, payload: dict) -> Any:
        last: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                client = await self._get_client()
                resp = await client.post(path, json=payload)
                resp.raise_for_status()
                return resp.json()
            except (httpx.ConnectError, httpx.ReadTimeout) as e:
                last = e
                if attempt < self.max_retries:
                    await asyncio.sleep(0.5 * (attempt + 1))
        raise last  # type: ignore[misc]

    def _require_identity(self) -> MaxiaMeshIdentity:
        if self.identity is None:
            raise RuntimeError(
                "MaxiaMeshClient was constructed without an identity — "
                "provide `identity=MaxiaMeshIdentity.generate()` for "
                "register/execute calls."
            )
        return self.identity

    async def close(self) -> None:
        """Close the underlying httpx client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Register (signed)
    # ------------------------------------------------------------------

    async def register_trusted_agent(
        self,
        name: str,
        description: str,
        capabilities: list[str],
        price_usdc: float = 0.0,
        input_schema: Optional[dict] = None,
        endpoint_url: str = "",
    ) -> dict:
        """Register your trusted agent as a MAXIA skill.

        The payload is ed25519-signed so MAXIA can prove you own the
        DID. After registration your skill appears in the marketplace
        and becomes discoverable by peers.

        Parameters
        ----------
        name:
            Human-readable name.
        description:
            Short description of what the agent does.
        capabilities:
            List of tags peers can search by (e.g.
            ``["code_review", "python", "security"]``).
        price_usdc:
            Price per call in USDC. ``0.0`` = free (default).
        input_schema:
            Optional JSON Schema describing the ``payload`` shape that
            ``execute_trusted_agent`` accepts.
        endpoint_url:
            Optional HTTPS URL MAXIA should forward paid calls to. If
            empty, MAXIA stores the skill as a "manifest-only" entry
            (the agent owner is responsible for running the execution).
        """
        identity = self._require_identity()
        nonce = uuid.uuid4().hex
        timestamp = int(time.time())
        signature = identity.sign(identity.canonical_register(nonce, timestamp))
        payload = {
            "did": identity.did,
            "pubkey": identity.public_key_b58,
            "name": name[:200],
            "description": description[:1000],
            "capabilities": [str(c)[:60] for c in (capabilities or [])][:20],
            "price_usdc": float(price_usdc),
            "input_schema": input_schema or {},
            "endpoint_url": endpoint_url[:500],
            "nonce": nonce,
            "timestamp": timestamp,
            "signature": signature,
        }
        return await self._post("/api/agent/mesh/register", payload)

    # ------------------------------------------------------------------
    # Discover (public)
    # ------------------------------------------------------------------

    async def discover_trusted_agents(
        self,
        capability: str = "",
        max_price: float = 1000.0,
        limit: int = 20,
    ) -> list[dict]:
        """List registered trusted agents. Public read, no signing needed."""
        params: dict[str, Any] = {"limit": limit}
        if capability:
            params["capability"] = capability
        if max_price != 1000.0:
            params["max_price"] = max_price
        data = await self._get("/api/agent/mesh/discover", params)
        if isinstance(data, dict) and "agents" in data:
            return data["agents"]
        return data if isinstance(data, list) else []

    async def get_trusted_agent(self, did: str) -> dict:
        """Fetch one trusted agent's public info by DID."""
        safe = did.replace("/", "_")
        return await self._get(f"/api/agent/mesh/agent/{safe}")

    # ------------------------------------------------------------------
    # Execute (signed)
    # ------------------------------------------------------------------

    async def execute_trusted_agent(
        self,
        skill_id: str,
        payload: dict,
        payment_tx: str = "",
    ) -> dict:
        """Execute a peer's trusted agent skill.

        The call is ed25519-signed by your identity so MAXIA can log
        the execution under your DID + issue a signed receipt.

        Parameters
        ----------
        skill_id:
            The skill ID from :meth:`discover_trusted_agents` entries.
        payload:
            Input payload matching the skill's ``input_schema``.
        payment_tx:
            Solana USDC or Base USDC transaction signature for paid
            skills. Empty string for free/sandbox execution.
        """
        identity = self._require_identity()
        nonce = uuid.uuid4().hex
        timestamp = int(time.time())
        signature = identity.sign(
            identity.canonical_execute(skill_id, nonce, timestamp)
        )
        body = {
            "did": identity.did,
            "pubkey": identity.public_key_b58,
            "skill_id": skill_id,
            "payload": payload or {},
            "payment_tx": payment_tx,
            "nonce": nonce,
            "timestamp": timestamp,
            "signature": signature,
        }
        return await self._post("/api/agent/mesh/execute", body)

    def __repr__(self) -> str:
        did = self.identity.did if self.identity else "(none)"
        return f"MaxiaMeshClient(base_url={self.base_url!r}, did={did!r})"
