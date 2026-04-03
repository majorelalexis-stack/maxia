"""MAXIA RPC-as-a-Service — Proxy RPC sur 14 blockchains avec metering."""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx
from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel

from core.config import get_rpc_url

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/rpc", tags=["RPC Proxy"])

# ---------------------------------------------------------------------------
# Chain registry
# ---------------------------------------------------------------------------

CHAINS: dict[str, dict[str, Any]] = {
    "solana": {
        "rpc": get_rpc_url(),
        "type": "jsonrpc",
        "name": "Solana",
    },
    "ethereum": {
        "rpc": "https://eth.llamarpc.com",
        "type": "jsonrpc",
        "name": "Ethereum",
    },
    "base": {
        "rpc": "https://mainnet.base.org",
        "type": "jsonrpc",
        "name": "Base",
    },
    "polygon": {
        "rpc": "https://polygon-rpc.com",
        "type": "jsonrpc",
        "name": "Polygon",
    },
    "arbitrum": {
        "rpc": "https://arb1.arbitrum.io/rpc",
        "type": "jsonrpc",
        "name": "Arbitrum",
    },
    "avalanche": {
        "rpc": "https://api.avax.network/ext/bc/C/rpc",
        "type": "jsonrpc",
        "name": "Avalanche",
    },
    "bnb": {
        "rpc": "https://bsc-dataseed.binance.org",
        "type": "jsonrpc",
        "name": "BNB Chain",
    },
    "sei": {
        "rpc": "https://evm-rpc.sei-apis.com",
        "type": "jsonrpc",
        "name": "Sei",
    },
    "near": {
        "rpc": "https://rpc.mainnet.near.org",
        "type": "jsonrpc",
        "name": "NEAR",
    },
    "aptos": {
        "rpc": "https://fullnode.mainnet.aptoslabs.com/v1",
        "type": "rest",
        "name": "Aptos",
    },
    "sui": {
        "rpc": "https://fullnode.mainnet.sui.io:443",
        "type": "jsonrpc",
        "name": "SUI",
    },
    "ton": {
        "rpc": "https://toncenter.com/api/v2",
        "type": "rest",
        "name": "TON",
    },
    "tron": {
        "rpc": "https://api.trongrid.io",
        "type": "rest",
        "name": "TRON",
    },
    "xrp": {
        "rpc": "https://s2.ripple.com:51234",
        "type": "jsonrpc",
        "name": "XRP Ledger",
    },
}

# ---------------------------------------------------------------------------
# Rate-limiting / metering  (in-memory)
# ---------------------------------------------------------------------------

FREE_CALLS_PER_DAY = 100
COST_PER_CALL_USDC = 0.001

# {api_key: {"date": "YYYY-MM-DD", "count": int, "total": int}}
_usage: dict[str, dict[str, Any]] = {}


def _get_usage(api_key: str) -> dict[str, Any]:
    """Retourne (et initialise si besoin) le compteur pour une clé API."""
    today = date.today().isoformat()
    entry = _usage.get(api_key)
    if entry is None or entry["date"] != today:
        _usage[api_key] = {"date": today, "count": 0, "total": entry["total"] if entry else 0}
    return _usage[api_key]


def _record_call(api_key: str) -> dict[str, Any]:
    """Enregistre un appel et retourne le statut de metering."""
    usage = _get_usage(api_key)
    usage["count"] += 1
    usage["total"] += 1
    is_free = usage["count"] <= FREE_CALLS_PER_DAY
    return {
        "calls_today": usage["count"],
        "free_remaining": max(0, FREE_CALLS_PER_DAY - usage["count"]),
        "billable": not is_free,
        "cost_usdc": 0.0 if is_free else COST_PER_CALL_USDC,
    }


# ---------------------------------------------------------------------------
# Shared HTTP client
# ---------------------------------------------------------------------------

_http_client: httpx.AsyncClient | None = None


def _client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(timeout=httpx.Timeout(15.0))
    return _http_client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_api_key(
    header_key: str | None,
    query_key: str | None,
) -> str:
    """Extrait la clé API depuis le header ou le query param."""
    key = header_key or query_key
    if not key:
        raise HTTPException(status_code=401, detail="Missing API key (header X-API-Key or query param api_key)")
    return key


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


class RPCRequest(BaseModel):
    """Corps JSON-RPC générique (ou payload REST libre)."""
    jsonrpc: str | None = "2.0"
    method: str | None = None
    params: Any = None
    id: int | str | None = 1


@router.get("/chains")
async def list_chains():
    """Liste toutes les blockchains disponibles avec leur statut."""
    result = []
    for chain_id, info in CHAINS.items():
        result.append({
            "chain": chain_id,
            "name": info["name"],
            "rpc": info["rpc"],
            "type": info["type"],
            "status": "active",
        })
    return {"chains": result, "count": len(result)}


@router.get("/usage")
async def get_usage(
    api_key: str = Query(..., description="Clé API pour consulter l'usage"),
):
    """Retourne les statistiques d'usage pour une clé API."""
    usage = _get_usage(api_key)
    return {
        "api_key": api_key,
        "date": usage["date"],
        "calls_today": usage["count"],
        "total_calls": usage["total"],
        "free_limit": FREE_CALLS_PER_DAY,
        "free_remaining": max(0, FREE_CALLS_PER_DAY - usage["count"]),
        "cost_per_call_usdc": COST_PER_CALL_USDC,
        "billable_calls_today": max(0, usage["count"] - FREE_CALLS_PER_DAY),
        "estimated_cost_usdc": round(max(0, usage["count"] - FREE_CALLS_PER_DAY) * COST_PER_CALL_USDC, 6),
    }


@router.post("/{chain}")
async def proxy_rpc(
    chain: str,
    request: Request,
    x_api_key: str | None = Header(None),
    api_key: str | None = Query(None),
):
    """Proxy un appel RPC vers la blockchain spécifiée."""

    # --- Validate chain ---
    chain_lower = chain.lower()
    if chain_lower not in CHAINS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported chain '{chain}'. Available: {', '.join(sorted(CHAINS))}",
        )

    # --- API key & metering ---
    key = _resolve_api_key(x_api_key, api_key)
    meter = _record_call(key)

    chain_info = CHAINS[chain_lower]
    rpc_url = chain_info["rpc"]
    chain_type = chain_info["type"]

    # --- Read raw body ---
    try:
        body = await request.json()

        # Security: whitelist read-only RPC methods
        _BLOCKED_METHODS = {
            "eth_sendTransaction", "eth_sendRawTransaction", "eth_sign",
            "personal_sign", "personal_sendTransaction", "personal_unlockAccount",
            "admin_startRPC", "admin_stopRPC", "admin_addPeer", "admin_removePeer",
            "debug_traceTransaction", "miner_start", "miner_stop",
            "sendTransaction", "signTransaction",
        }
        method = body.get("method", "") if isinstance(body, dict) else ""
        if method in _BLOCKED_METHODS:
            raise HTTPException(403, f"Method '{method}' is blocked for security. Read-only methods only.")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # --- Forward request ---
    try:
        client = _client()

        if chain_type == "jsonrpc":
            # Standard JSON-RPC POST
            resp = await client.post(
                rpc_url,
                json=body,
                headers={"Content-Type": "application/json"},
            )
        else:
            # REST-style chains (Aptos, TON, TRON): forward as-is
            method = body.get("method", "")
            params = body.get("params", {})

            # Build REST URL from method name (e.g. "getAccount" -> /getAccount)
            if method:
                url = f"{rpc_url.rstrip('/')}/{method}"
            else:
                url = rpc_url

            if isinstance(params, dict) and params:
                resp = await client.post(
                    url,
                    json=params,
                    headers={"Content-Type": "application/json"},
                )
            elif isinstance(params, list) and params:
                resp = await client.post(
                    url,
                    json=params,
                    headers={"Content-Type": "application/json"},
                )
            else:
                resp = await client.get(url)

    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail=f"RPC call to {chain_info['name']} timed out (15s)")
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"RPC error for {chain_info['name']}: {exc}")

    # --- Return response ---
    try:
        rpc_response = resp.json()
    except Exception:
        rpc_response = resp.text

    return {
        "chain": chain_lower,
        "rpc_response": rpc_response,
        "metering": meter,
    }
