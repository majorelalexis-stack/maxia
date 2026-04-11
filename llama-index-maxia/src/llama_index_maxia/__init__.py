"""llama-index-maxia — MAXIA × LlamaIndex AgentMesh integration.

Public API::

    from llama_index_maxia import (
        MaxiaMeshIdentity,    # ed25519 identity + DID
        MaxiaMeshClient,      # async HTTP client for /api/agent/mesh/*
        wrap_trusted_worker,  # adapter for llama-index-agent-agentmesh
    )

See ``README.md`` for usage examples.
"""
from __future__ import annotations

from llama_index_maxia.identity import MaxiaMeshIdentity
from llama_index_maxia.client import MaxiaMeshClient
from llama_index_maxia.wrapper import wrap_trusted_worker, TrustedWorkerAdapter

__version__ = "0.1.0"

__all__ = [
    "MaxiaMeshIdentity",
    "MaxiaMeshClient",
    "wrap_trusted_worker",
    "TrustedWorkerAdapter",
    "__version__",
]
