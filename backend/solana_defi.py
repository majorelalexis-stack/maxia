"""MAXIA Solana DeFi — Lending, Borrowing, Staking via on-chain transactions.

Exposes DeFi operations from Solana protocols:
- Lending (Kamino, Solend, MarginFi) — via protocol APIs
- Borrowing (Kamino, Solend) — via protocol APIs
- Liquid Staking (Marinade, Jito, BlazeStake) — via Marinade API / Jupiter swaps
- LP Positions (Orca, Raydium) — read-only rates

Pattern: MAXIA builds unsigned tx → returns base64 → user signs with wallet.
Fallback: manual instructions if API/RPC call fails.
"""
import asyncio
import base64
import json
import logging
import time
from typing import Any, Optional

import httpx
from fastapi import APIRouter, HTTPException, Header

from config import get_rpc_url, SOLANA_RPC_URLS
from http_client import get_http_client

log = logging.getLogger("solana_defi")

router = APIRouter(prefix="/api/defi", tags=["solana-defi"])

# ── Well-known Solana mints ──
SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT_MINT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
MSOL_MINT = "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So"
JITOSOL_MINT = "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn"
BSOL_MINT = "bSo13r4TkiE4KumL71LsHTPpL2euBYLFx6h9HP3piy1"

# Asset → mint lookup for lending protocols
ASSET_MINTS: dict[str, str] = {
    "SOL": SOL_MINT,
    "USDC": USDC_MINT,
    "USDT": USDT_MINT,
    "ETH": "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs",
    "BTC": "3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh",
    "mSOL": MSOL_MINT,
    "MSOL": MSOL_MINT,
    "jitoSOL": JITOSOL_MINT,
    "JITOSOL": JITOSOL_MINT,
    "bSOL": BSOL_MINT,
    "BSOL": BSOL_MINT,
}

# Staking token → receive mint
STAKING_OUTPUT_MINTS: dict[str, str] = {
    "marinade": MSOL_MINT,
    "jito": JITOSOL_MINT,
    "blazestake": BSOL_MINT,
}

# Jupiter swap API endpoints (same as jupiter_router.py)
JUPITER_QUOTE_URL = "https://lite-api.jup.ag/swap/v1/quote"
JUPITER_SWAP_URL = "https://lite-api.jup.ag/swap/v1/swap"

# Marinade Finance API
MARINADE_API_BASE = "https://api.marinade.finance/v1"

# Safety limits
MAX_DEFI_AMOUNT_USD = 50000
MAX_STAKE_SOL = 100000
MIN_AMOUNT = 0.000001

# ── Cache pour les rates DeFiLlama (refresh toutes les 5 min) ──
_rates_cache: dict = {}
_rates_cache_ts: float = 0
_RATES_TTL = 300  # 5 minutes


# ── Solana JSON-RPC helpers ──

async def _solana_rpc_call(method: str, params: list[Any] | None = None) -> dict:
    """Call Solana JSON-RPC with failover across configured RPC URLs."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params or [],
    }
    client = get_http_client()
    rpc_urls = SOLANA_RPC_URLS if SOLANA_RPC_URLS else [get_rpc_url()]
    last_error = ""

    for rpc_url in rpc_urls[:3]:
        try:
            resp = await client.post(rpc_url, json=payload, timeout=12)
            data = resp.json()
            if "result" in data:
                return data["result"]
            err = data.get("error", {})
            last_error = err.get("message", str(err))
        except Exception as e:
            last_error = str(e)
            continue

    raise RuntimeError(f"All Solana RPCs failed: {last_error}")


async def _get_latest_blockhash() -> dict:
    """Fetch latest blockhash from Solana RPC."""
    result = await _solana_rpc_call(
        "getLatestBlockhash",
        [{"commitment": "finalized"}],
    )
    value = result.get("value", {})
    return {
        "blockhash": value.get("blockhash", ""),
        "lastValidBlockHeight": value.get("lastValidBlockHeight", 0),
    }


# ── Jupiter swap helper (for staking via DEX) ──

async def _jupiter_get_quote(
    input_mint: str,
    output_mint: str,
    amount_raw: int,
    slippage_bps: int = 100,
) -> dict:
    """Get a Jupiter quote for a swap."""
    params = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": str(amount_raw),
        "slippageBps": slippage_bps,
        "restrictIntermediateTokens": "true",
    }
    client = get_http_client()
    urls = [
        JUPITER_QUOTE_URL,
        "https://api.jup.ag/swap/v1/quote",
    ]
    last_error = ""

    for url in urls:
        for attempt in range(3):
            try:
                resp = await client.get(url, params=params, timeout=15)
                if resp.status_code == 200:
                    return {"success": True, "data": resp.json()}
                if resp.status_code == 429:
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                last_error = f"Jupiter {resp.status_code}: {resp.text[:200]}"
                break
            except Exception as e:
                last_error = str(e)
                break

    return {"success": False, "error": last_error or "Jupiter unavailable"}


async def _jupiter_get_swap_tx(
    quote_data: dict,
    user_pubkey: str,
) -> dict:
    """Get an unsigned swap transaction from Jupiter."""
    body = {
        "quoteResponse": quote_data,
        "userPublicKey": user_pubkey,
        "wrapAndUnwrapSol": True,
        "dynamicComputeUnitLimit": True,
        "prioritizationFeeLamports": "auto",
    }
    client = get_http_client()
    urls = [
        JUPITER_SWAP_URL,
        "https://api.jup.ag/swap/v1/swap",
    ]
    last_error = ""

    for url in urls:
        try:
            resp = await client.post(url, json=body, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "success": True,
                    "swapTransaction": data.get("swapTransaction", ""),
                    "lastValidBlockHeight": data.get("lastValidBlockHeight", 0),
                }
            last_error = f"Jupiter swap {resp.status_code}: {resp.text[:200]}"
        except Exception as e:
            last_error = str(e)
            continue

    return {"success": False, "error": last_error}


# ── Marinade Finance API helper ──

async def _marinade_build_stake_tx(
    amount_sol: float,
    user_pubkey: str,
) -> dict:
    """Build a Marinade staking transaction via their official API.

    Marinade API returns a serialized unsigned transaction for SOL → mSOL staking.
    Endpoint: POST https://api.marinade.finance/v1/deposit
    """
    client = get_http_client()
    lamports = int(amount_sol * 1_000_000_000)

    # Try Marinade's deposit API
    try:
        body = {
            "amountLamports": lamports,
            "userPublicKey": user_pubkey,
        }
        resp = await client.post(
            f"{MARINADE_API_BASE}/deposit",
            json=body,
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            tx_b64 = data.get("transaction", "")
            if tx_b64:
                return {
                    "success": True,
                    "transaction_b64": tx_b64,
                    "method": "marinade_api",
                }
    except Exception as e:
        log.warning(f"[DeFi] Marinade API deposit failed: {e}")

    # Fallback: use Jupiter SOL → mSOL swap (equivalent to Marinade staking)
    log.info("[DeFi] Marinade API unavailable, falling back to Jupiter SOL→mSOL swap")
    return await _build_staking_via_jupiter(
        amount_sol=amount_sol,
        user_pubkey=user_pubkey,
        output_mint=MSOL_MINT,
        protocol_name="Marinade (via Jupiter)",
    )


async def _build_staking_via_jupiter(
    amount_sol: float,
    user_pubkey: str,
    output_mint: str,
    protocol_name: str,
) -> dict:
    """Build a liquid staking transaction using Jupiter swap (SOL → LST token).

    This works for all liquid staking tokens (mSOL, jitoSOL, bSOL) because
    Jupiter routes through the native staking pools automatically.
    """
    lamports = int(amount_sol * 1_000_000_000)

    quote = await _jupiter_get_quote(
        input_mint=SOL_MINT,
        output_mint=output_mint,
        amount_raw=lamports,
        slippage_bps=50,
    )
    if not quote.get("success"):
        return {"success": False, "error": quote.get("error", "Quote failed")}

    swap = await _jupiter_get_swap_tx(
        quote_data=quote["data"],
        user_pubkey=user_pubkey,
    )
    if not swap.get("success"):
        return {"success": False, "error": swap.get("error", "Swap TX build failed")}

    quote_data = quote["data"]
    out_amount_raw = int(quote_data.get("outAmount", "0"))

    return {
        "success": True,
        "transaction_b64": swap["swapTransaction"],
        "method": f"jupiter_swap ({protocol_name})",
        "out_amount_raw": out_amount_raw,
        "price_impact_pct": quote_data.get("priceImpactPct", "0"),
        "lastValidBlockHeight": swap.get("lastValidBlockHeight", 0),
    }


# ── Lending/borrowing transaction builders ──

async def _build_kamino_lend_tx(
    asset: str,
    amount: float,
    user_pubkey: str,
    action: str = "lend",
) -> dict:
    """Build a Kamino Finance lending/borrowing transaction.

    Kamino exposes a transaction builder API. We request an unsigned TX
    for the user's wallet to sign.
    """
    client = get_http_client()
    asset_upper = asset.upper()
    mint = ASSET_MINTS.get(asset_upper, ASSET_MINTS.get(asset, ""))
    if not mint:
        return {"success": False, "error": f"Unknown asset: {asset}"}

    # Kamino's public API for building deposit/borrow transactions
    kamino_api = "https://api.kamino.finance"

    try:
        # Kamino API: POST /transactions/deposit or /transactions/borrow
        endpoint = "deposit" if action == "lend" else "borrow"
        decimals_map = {
            "SOL": 9, "USDC": 6, "USDT": 6, "ETH": 8, "BTC": 8,
            "mSOL": 9, "MSOL": 9, "jitoSOL": 9, "JITOSOL": 9,
        }
        decimals = decimals_map.get(asset_upper, 6)
        amount_raw = int(amount * (10 ** decimals))

        body = {
            "mint": mint,
            "amount": str(amount_raw),
            "userPublicKey": user_pubkey,
        }
        resp = await client.post(
            f"{kamino_api}/v2/transactions/{endpoint}",
            json=body,
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            tx_b64 = data.get("transaction", "")
            if tx_b64:
                return {
                    "success": True,
                    "transaction_b64": tx_b64,
                    "method": f"kamino_api_{endpoint}",
                }
            # Some Kamino responses return serializedTransaction
            tx_b64 = data.get("serializedTransaction", "")
            if tx_b64:
                return {
                    "success": True,
                    "transaction_b64": tx_b64,
                    "method": f"kamino_api_{endpoint}",
                }

        log.warning(f"[DeFi] Kamino API {endpoint} returned {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        log.warning(f"[DeFi] Kamino API failed for {action} {asset}: {e}")

    return {"success": False, "error": f"Kamino {action} transaction build failed"}


async def _build_solend_lend_tx(
    asset: str,
    amount: float,
    user_pubkey: str,
    action: str = "lend",
) -> dict:
    """Build a Solend lending/borrowing transaction via their SDK API.

    Solend provides a transaction builder endpoint for deposits and borrows.
    """
    client = get_http_client()
    asset_upper = asset.upper()
    mint = ASSET_MINTS.get(asset_upper, ASSET_MINTS.get(asset, ""))
    if not mint:
        return {"success": False, "error": f"Unknown asset: {asset}"}

    solend_api = "https://api.solend.fi"

    try:
        decimals_map = {
            "SOL": 9, "USDC": 6, "USDT": 6, "ETH": 8, "BTC": 8,
            "mSOL": 9, "MSOL": 9, "stSOL": 9,
        }
        decimals = decimals_map.get(asset_upper, 6)
        amount_raw = int(amount * (10 ** decimals))

        endpoint = "deposit" if action == "lend" else "borrow"
        body = {
            "mint": mint,
            "amount": str(amount_raw),
            "userPublicKey": user_pubkey,
            "market": "production",  # main market
        }
        resp = await client.post(
            f"{solend_api}/v1/transactions/{endpoint}",
            json=body,
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            tx_b64 = data.get("transaction", "") or data.get("serializedTransaction", "")
            if tx_b64:
                return {
                    "success": True,
                    "transaction_b64": tx_b64,
                    "method": f"solend_api_{endpoint}",
                }

        log.warning(f"[DeFi] Solend API {endpoint} returned {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        log.warning(f"[DeFi] Solend API failed for {action} {asset}: {e}")

    return {"success": False, "error": f"Solend {action} transaction build failed"}


async def _build_marginfi_lend_tx(
    asset: str,
    amount: float,
    user_pubkey: str,
    action: str = "lend",
) -> dict:
    """Build a MarginFi lending/borrowing transaction via their API.

    MarginFi has a transaction builder for deposit/borrow operations.
    """
    client = get_http_client()
    asset_upper = asset.upper()
    mint = ASSET_MINTS.get(asset_upper, ASSET_MINTS.get(asset, ""))
    if not mint:
        return {"success": False, "error": f"Unknown asset: {asset}"}

    marginfi_api = "https://api.marginfi.com"

    try:
        decimals_map = {
            "SOL": 9, "USDC": 6, "USDT": 6,
            "mSOL": 9, "MSOL": 9, "jitoSOL": 9, "JITOSOL": 9,
        }
        decimals = decimals_map.get(asset_upper, 6)
        amount_raw = int(amount * (10 ** decimals))

        endpoint = "deposit" if action == "lend" else "borrow"
        body = {
            "mint": mint,
            "amount": str(amount_raw),
            "userPublicKey": user_pubkey,
        }
        resp = await client.post(
            f"{marginfi_api}/v1/transactions/{endpoint}",
            json=body,
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            tx_b64 = data.get("transaction", "") or data.get("serializedTransaction", "")
            if tx_b64:
                return {
                    "success": True,
                    "transaction_b64": tx_b64,
                    "method": f"marginfi_api_{endpoint}",
                }

        log.warning(f"[DeFi] MarginFi API {endpoint} returned {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        log.warning(f"[DeFi] MarginFi API failed for {action} {asset}: {e}")

    return {"success": False, "error": f"MarginFi {action} transaction build failed"}


# Protocol → builder dispatch
_LEND_BUILDERS: dict[str, Any] = {
    "kamino": _build_kamino_lend_tx,
    "solend": _build_solend_lend_tx,
    "marginfi": _build_marginfi_lend_tx,
}


# ── Manual instruction fallback builder ──

def _build_manual_instructions(
    protocol_data: dict,
    action: str,
    asset: str,
    amount: float,
    apy: float,
    collateral_asset: str = "",
    collateral_amount: float = 0,
) -> dict:
    """Build fallback manual instructions when on-chain TX build fails."""
    p_name = protocol_data["name"]
    p_url = protocol_data["url"]

    if action == "lend":
        return {
            "status": "requires_manual_execution",
            "execution_mode": "instruction",
            "reason": "Protocol API unavailable — follow manual steps below",
            "instruction": {
                "protocol": p_name,
                "protocol_url": p_url,
                "action": "lend",
                "asset": asset,
                "amount": amount,
                "current_apy_percent": apy,
                "estimated_apy": f"{apy}%",
                "yearly_yield": round(amount * apy / 100, 2),
            },
            "steps": [
                f"1. Go to {p_url} and connect your wallet",
                f"2. Select {asset} and enter the amount: {amount}",
                "3. Approve the transaction in your wallet",
                "4. Return to MAXIA to track your position",
            ],
        }

    if action == "borrow":
        return {
            "status": "requires_manual_execution",
            "execution_mode": "instruction",
            "reason": "Protocol API unavailable — follow manual steps below",
            "instruction": {
                "protocol": p_name,
                "protocol_url": p_url,
                "action": "borrow",
                "borrow_asset": asset,
                "borrow_amount": amount,
                "current_borrow_apy_percent": apy,
                "borrow_apy": f"{apy}%",
                "collateral_asset": collateral_asset,
                "collateral_amount": collateral_amount,
                "yearly_cost": round(amount * apy / 100, 2),
            },
            "steps": [
                f"1. Go to {p_url} and connect your wallet",
                f"2. Supply {collateral_amount} {collateral_asset} as collateral",
                f"3. Borrow {amount} {asset} against your collateral",
                "4. Approve the transaction in your wallet",
                "5. Return to MAXIA to track your position",
            ],
        }

    # action == "stake"
    token = protocol_data.get("token", "LST")
    return {
        "status": "requires_manual_execution",
        "execution_mode": "instruction",
        "reason": "Protocol API unavailable — follow manual steps below",
        "instruction": {
            "protocol": p_name,
            "protocol_url": p_url,
            "action": "stake",
            "amount_sol": amount,
            "receive_token": token,
            "current_apy_percent": apy,
            "estimated_apy": f"{apy}%",
            "yearly_yield_sol": round(amount * apy / 100, 2),
            "description": protocol_data.get("description", ""),
        },
        "steps": [
            f"1. Go to {p_url} and connect your wallet",
            f"2. Enter the amount to stake: {amount} SOL",
            "3. Approve the transaction in your wallet",
            f"4. You will receive {token} in your wallet",
            "5. Return to MAXIA to track your position",
        ],
    }


# ── DeFiLlama rate refresh (unchanged) ──

async def _refresh_defi_rates() -> dict:
    """Fetch les rates live depuis DeFiLlama et met a jour les protocoles."""
    global _rates_cache, _rates_cache_ts, LENDING_PROTOCOLS, STAKING_PROTOCOLS, LP_PROTOCOLS
    if _rates_cache and time.time() - _rates_cache_ts < _RATES_TTL:
        return _rates_cache
    try:
        client = get_http_client()
        resp = await client.get("https://yields.llama.fi/pools", timeout=15)
        resp.raise_for_status()
        pools = resp.json().get("data", [])
        # Index par project+chain+symbol
        idx: dict[str, dict] = {}
        for p in pools:
            proj = p.get("project", "").lower()
            chain = p.get("chain", "").lower()
            sym = (p.get("symbol") or "").upper()
            apy = p.get("apy", 0) or 0
            tvl = p.get("tvlUsd", 0) or 0
            key = f"{proj}_{chain}_{sym}"
            if key not in idx or apy > (idx[key].get("apy", 0) or 0):
                idx[key] = {"apy": apy, "tvl": tvl, "project": proj, "symbol": sym}

        # Mise a jour lending protocols
        _lending_map = {
            "solend": {"project": "solend", "chain": "solana"},
            "kamino": {"project": "kamino-lend", "chain": "solana"},
            "marginfi": {"project": "marginfi", "chain": "solana"},
        }
        for pid, meta in _lending_map.items():
            if pid not in LENDING_PROTOCOLS:
                continue
            for asset in list(LENDING_PROTOCOLS[pid]["supply_apy"].keys()):
                key = f"{meta['project']}_{meta['chain']}_{asset}"
                pool_data = idx.get(key, {})
                live_apy = pool_data.get("apy", 0)
                if live_apy > 0:
                    LENDING_PROTOCOLS[pid]["supply_apy"][asset] = round(live_apy, 2)
                    # Borrow APY generalement ~1.5x le supply
                    if asset in LENDING_PROTOCOLS[pid].get("borrow_apy", {}):
                        LENDING_PROTOCOLS[pid]["borrow_apy"][asset] = round(live_apy * 1.5, 2)
                live_tvl = pool_data.get("tvl", 0)
                if live_tvl > 0:
                    LENDING_PROTOCOLS[pid]["tvl_usd"] = round(live_tvl, 0)

        # Mise a jour staking protocols
        _staking_map = {
            "marinade": "marinade-finance_solana_MSOL",
            "jito": "jito_solana_JITOSOL",
            "blazestake": "blazestake_solana_BSOL",
        }
        for pid, key in _staking_map.items():
            pool_data = idx.get(key, {})
            live_apy = pool_data.get("apy", 0)
            live_tvl = pool_data.get("tvl", 0)
            if live_apy > 0 and pid in STAKING_PROTOCOLS:
                STAKING_PROTOCOLS[pid]["apy"] = round(live_apy, 2)
            if live_tvl > 0 and pid in STAKING_PROTOCOLS:
                STAKING_PROTOCOLS[pid]["tvl_usd"] = round(live_tvl, 0)

        # Mise a jour LP protocols
        _lp_map = {
            "orca": [
                ("SOL/USDC", "orca_solana_SOL-USDC"),
                ("mSOL/SOL", "orca_solana_MSOL-SOL"),
                ("BONK/SOL", "orca_solana_BONK-SOL"),
            ],
            "raydium": [
                ("SOL/USDC", "raydium_solana_SOL-USDC"),
                ("RAY/USDC", "raydium_solana_RAY-USDC"),
            ],
        }
        for pid, pairs in _lp_map.items():
            if pid not in LP_PROTOCOLS:
                continue
            for i, (pair_name, key) in enumerate(pairs):
                pool_data = idx.get(key, {})
                live_apy = pool_data.get("apy", 0)
                live_tvl = pool_data.get("tvl", 0)
                if i < len(LP_PROTOCOLS[pid]["top_pools"]):
                    if live_apy > 0:
                        LP_PROTOCOLS[pid]["top_pools"][i]["apy"] = round(live_apy, 2)
                    if live_tvl > 0:
                        LP_PROTOCOLS[pid]["top_pools"][i]["tvl"] = round(live_tvl, 0)

        _rates_cache = idx
        _rates_cache_ts = time.time()
        log.info(f"[DeFi] Rates live mis a jour: {len(idx)} pools indexes")
        return idx
    except Exception as e:
        log.warning(f"[DeFi] Impossible de rafraichir les rates live: {e}")
        return _rates_cache or {}


# ── DeFi Protocol Data (valeurs initiales, rafraichies par _refresh_defi_rates) ──
LENDING_PROTOCOLS = {
    "solend": {
        "name": "Solend",
        "chain": "solana",
        "tvl_usd": 0,
        "assets": ["SOL", "USDC", "USDT", "ETH", "BTC", "mSOL", "stSOL"],
        "supply_apy": {"USDC": 0, "SOL": 0, "USDT": 0, "ETH": 0},
        "borrow_apy": {"USDC": 0, "SOL": 0, "USDT": 0},
        "url": "https://solend.fi",
    },
    "kamino": {
        "name": "Kamino Finance",
        "chain": "solana",
        "tvl_usd": 0,
        "assets": ["SOL", "USDC", "USDT", "jitoSOL", "mSOL", "ETH", "BTC"],
        "supply_apy": {"USDC": 0, "SOL": 0, "jitoSOL": 0, "USDT": 0},
        "borrow_apy": {"USDC": 0, "SOL": 0, "USDT": 0},
        "url": "https://app.kamino.finance",
    },
    "marginfi": {
        "name": "MarginFi",
        "chain": "solana",
        "tvl_usd": 0,
        "assets": ["SOL", "USDC", "USDT", "mSOL", "jitoSOL"],
        "supply_apy": {"USDC": 0, "SOL": 0, "jitoSOL": 0},
        "borrow_apy": {"USDC": 0, "SOL": 0},
        "url": "https://app.marginfi.com",
    },
}

STAKING_PROTOCOLS = {
    "marinade": {
        "name": "Marinade Finance",
        "chain": "solana",
        "token": "mSOL",
        "apy": 0,
        "tvl_usd": 0,
        "min_stake": 0.01,
        "url": "https://marinade.finance/app/stake",
        "description": "Liquid staking for SOL. Receive mSOL, earn staking rewards + MEV.",
    },
    "jito": {
        "name": "Jito",
        "chain": "solana",
        "token": "jitoSOL",
        "apy": 0,
        "tvl_usd": 0,
        "min_stake": 0.01,
        "url": "https://www.jito.network/staking/",
        "description": "MEV-powered liquid staking. Highest SOL staking yields via MEV redistribution.",
    },
    "blazestake": {
        "name": "BlazeStake",
        "chain": "solana",
        "token": "bSOL",
        "apy": 0,
        "tvl_usd": 0,
        "min_stake": 0.01,
        "url": "https://stake.solblaze.org",
        "description": "Decentralized liquid staking pool for SOL.",
    },
}

LP_PROTOCOLS = {
    "orca": {
        "name": "Orca Whirlpools",
        "chain": "solana",
        "type": "concentrated_liquidity",
        "description": "Concentrated liquidity AMM. Provide liquidity to earn trading fees.",
        "top_pools": [
            {"pair": "SOL/USDC", "apy": 0, "tvl": 0},
            {"pair": "mSOL/SOL", "apy": 0, "tvl": 0},
            {"pair": "BONK/SOL", "apy": 0, "tvl": 0},
        ],
    },
    "raydium": {
        "name": "Raydium",
        "chain": "solana",
        "type": "amm",
        "description": "AMM + CLOB hybrid. Provide liquidity or concentrated positions.",
        "top_pools": [
            {"pair": "SOL/USDC", "apy": 0, "tvl": 0},
            {"pair": "RAY/USDC", "apy": 0, "tvl": 0},
        ],
    },
}


# ── GET Endpoints (unchanged) ──

@router.get("/lending")
async def list_lending_protocols() -> dict:
    """List all lending/borrowing protocols on Solana with current APYs."""
    await _refresh_defi_rates()
    return {
        "protocols": [
            {
                "id": pid,
                "name": p["name"],
                "tvl_usd": p["tvl_usd"],
                "supply_apy": p["supply_apy"],
                "borrow_apy": p["borrow_apy"],
                "assets": p["assets"],
                "url": p["url"],
            }
            for pid, p in LENDING_PROTOCOLS.items()
        ],
        "best_supply": _find_best("supply"),
        "best_borrow": _find_best("borrow"),
    }


@router.get("/lending/best")
async def best_lending_rates(asset: str = "USDC") -> dict:
    """Find the best lending/borrowing rates for a specific asset across all protocols."""
    await _refresh_defi_rates()
    asset = asset.upper()
    supply_rates = []
    borrow_rates = []

    for pid, p in LENDING_PROTOCOLS.items():
        if asset in p["supply_apy"]:
            supply_rates.append({"protocol": p["name"], "apy": p["supply_apy"][asset], "url": p["url"]})
        if asset in p.get("borrow_apy", {}):
            borrow_rates.append({"protocol": p["name"], "apy": p["borrow_apy"][asset], "url": p["url"]})

    supply_rates.sort(key=lambda x: x["apy"], reverse=True)
    borrow_rates.sort(key=lambda x: x["apy"])

    return {
        "asset": asset,
        "best_supply": supply_rates[0] if supply_rates else None,
        "best_borrow": borrow_rates[0] if borrow_rates else None,
        "all_supply_rates": supply_rates,
        "all_borrow_rates": borrow_rates,
    }


@router.get("/staking")
async def list_staking_protocols() -> dict:
    """List all liquid staking protocols on Solana."""
    await _refresh_defi_rates()
    return {
        "protocols": [
            {"id": pid, **{k: v for k, v in p.items()}}
            for pid, p in STAKING_PROTOCOLS.items()
        ],
        "best_apy": max(STAKING_PROTOCOLS.values(), key=lambda p: p["apy"]),
    }


@router.get("/lp")
async def list_lp_opportunities() -> dict:
    """List LP (liquidity providing) opportunities on Solana."""
    await _refresh_defi_rates()
    return {
        "protocols": [
            {"id": pid, "name": p["name"], "type": p["type"], "top_pools": p["top_pools"]}
            for pid, p in LP_PROTOCOLS.items()
        ],
    }


# ── POST Endpoints (real on-chain transactions) ──

@router.post("/lend")
async def lend_asset(
    request: dict,
    x_api_key: str = Header(alias="X-API-Key"),
) -> dict:
    """Supply an asset to a lending protocol to earn interest.

    Builds an unsigned Solana transaction (base64) that the user's wallet must sign.
    Falls back to manual instructions if the protocol API is unavailable.
    """
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    await _refresh_defi_rates()

    protocol = request.get("protocol", "kamino")
    asset = request.get("asset", "USDC")
    amount = request.get("amount", 0)
    wallet = request.get("wallet", "")

    if protocol not in LENDING_PROTOCOLS:
        raise HTTPException(400, f"Unknown lending protocol: {protocol}")
    if not isinstance(amount, (int, float)) or amount <= MIN_AMOUNT:
        raise HTTPException(400, "Amount must be > 0")
    if amount > MAX_DEFI_AMOUNT_USD:
        raise HTTPException(400, f"Amount exceeds safety limit (${MAX_DEFI_AMOUNT_USD})")
    if not wallet or len(wallet) < 32:
        raise HTTPException(400, "Valid Solana wallet address required in 'wallet' field")

    p = LENDING_PROTOCOLS[protocol]
    apy = p["supply_apy"].get(asset.upper(), 0)

    # Try to build a real on-chain transaction
    builder = _LEND_BUILDERS.get(protocol)
    if builder:
        try:
            result = await builder(
                asset=asset,
                amount=amount,
                user_pubkey=wallet,
                action="lend",
            )
            if result.get("success") and result.get("transaction_b64"):
                log.info(
                    f"[DeFi] Lend TX built: {protocol} {amount} {asset} "
                    f"for {wallet[:8]}... via {result.get('method', 'unknown')}"
                )
                return {
                    "status": "transaction_ready",
                    "execution_mode": "wallet_sign",
                    "transaction_b64": result["transaction_b64"],
                    "method": result.get("method", protocol),
                    "protocol": p["name"],
                    "action": "lend",
                    "asset": asset,
                    "amount": amount,
                    "current_apy_percent": apy,
                    "estimated_apy": f"{apy}%",
                    "yearly_yield": round(amount * apy / 100, 2),
                    "instructions": [
                        "1. Review the transaction details in your wallet",
                        "2. Sign the transaction to deposit into the lending pool",
                        f"3. You will start earning ~{apy}% APY on your {asset}",
                    ],
                    "note": "Sign this transaction with your Solana wallet (Phantom, Solflare, etc.)",
                }
        except Exception as e:
            log.warning(f"[DeFi] Failed to build lend TX for {protocol}: {e}")

    # Fallback to manual instructions
    log.info(f"[DeFi] Lend fallback to manual instructions: {protocol} {amount} {asset}")
    return _build_manual_instructions(
        protocol_data=p,
        action="lend",
        asset=asset,
        amount=amount,
        apy=apy,
    )


@router.post("/borrow")
async def borrow_asset(
    request: dict,
    x_api_key: str = Header(alias="X-API-Key"),
) -> dict:
    """Borrow an asset from a lending protocol.

    Builds an unsigned Solana transaction (base64) that the user's wallet must sign.
    Falls back to manual instructions if the protocol API is unavailable.
    """
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    await _refresh_defi_rates()

    protocol = request.get("protocol", "kamino")
    asset = request.get("asset", "USDC")
    amount = request.get("amount", 0)
    collateral_asset = request.get("collateral_asset", "SOL")
    collateral_amount = request.get("collateral_amount", 0)
    wallet = request.get("wallet", "")

    if protocol not in LENDING_PROTOCOLS:
        raise HTTPException(400, f"Unknown protocol: {protocol}")
    if not isinstance(amount, (int, float)) or amount <= MIN_AMOUNT:
        raise HTTPException(400, "Amount must be > 0")
    if amount > MAX_DEFI_AMOUNT_USD:
        raise HTTPException(400, f"Amount exceeds safety limit (${MAX_DEFI_AMOUNT_USD})")
    if not wallet or len(wallet) < 32:
        raise HTTPException(400, "Valid Solana wallet address required in 'wallet' field")

    p = LENDING_PROTOCOLS[protocol]
    borrow_apy = p.get("borrow_apy", {}).get(asset.upper(), 0)

    # Try to build a real on-chain transaction
    builder = _LEND_BUILDERS.get(protocol)
    if builder:
        try:
            result = await builder(
                asset=asset,
                amount=amount,
                user_pubkey=wallet,
                action="borrow",
            )
            if result.get("success") and result.get("transaction_b64"):
                log.info(
                    f"[DeFi] Borrow TX built: {protocol} {amount} {asset} "
                    f"collateral {collateral_amount} {collateral_asset} "
                    f"for {wallet[:8]}... via {result.get('method', 'unknown')}"
                )
                return {
                    "status": "transaction_ready",
                    "execution_mode": "wallet_sign",
                    "transaction_b64": result["transaction_b64"],
                    "method": result.get("method", protocol),
                    "protocol": p["name"],
                    "action": "borrow",
                    "borrow_asset": asset,
                    "borrow_amount": amount,
                    "current_borrow_apy_percent": borrow_apy,
                    "borrow_apy": f"{borrow_apy}%",
                    "collateral_asset": collateral_asset,
                    "collateral_amount": collateral_amount,
                    "yearly_cost": round(amount * borrow_apy / 100, 2),
                    "instructions": [
                        "1. Review the transaction details in your wallet",
                        f"2. This will borrow {amount} {asset} against your collateral",
                        f"3. Borrow cost: ~{borrow_apy}% APY",
                        "4. Monitor your health factor to avoid liquidation",
                    ],
                    "note": "Sign this transaction with your Solana wallet (Phantom, Solflare, etc.)",
                }
        except Exception as e:
            log.warning(f"[DeFi] Failed to build borrow TX for {protocol}: {e}")

    # Fallback to manual instructions
    log.info(f"[DeFi] Borrow fallback to manual instructions: {protocol} {amount} {asset}")
    return _build_manual_instructions(
        protocol_data=p,
        action="borrow",
        asset=asset,
        amount=amount,
        apy=borrow_apy,
        collateral_asset=collateral_asset,
        collateral_amount=collateral_amount,
    )


@router.post("/stake")
async def stake_sol(
    request: dict,
    x_api_key: str = Header(alias="X-API-Key"),
) -> dict:
    """Stake SOL via liquid staking protocols.

    Builds an unsigned Solana transaction (base64) that the user's wallet must sign.
    For Marinade: uses their official deposit API (SOL → mSOL).
    For Jito/BlazeStake: uses Jupiter swap (SOL → jitoSOL/bSOL) which routes
    through the native staking pools automatically.
    Falls back to manual instructions if all API calls fail.
    """
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    await _refresh_defi_rates()

    protocol = request.get("protocol", "jito")
    amount = request.get("amount", 0)
    wallet = request.get("wallet", "")

    if protocol not in STAKING_PROTOCOLS:
        raise HTTPException(400, f"Unknown staking protocol: {protocol}")
    if not isinstance(amount, (int, float)) or amount <= MIN_AMOUNT:
        raise HTTPException(400, "Amount must be > 0")
    if amount > MAX_STAKE_SOL:
        raise HTTPException(400, f"Stake amount exceeds safety limit ({MAX_STAKE_SOL} SOL)")
    if not wallet or len(wallet) < 32:
        raise HTTPException(400, "Valid Solana wallet address required in 'wallet' field")

    p = STAKING_PROTOCOLS[protocol]
    apy = p["apy"]
    output_mint = STAKING_OUTPUT_MINTS.get(protocol, "")

    # Build real staking transaction
    try:
        if protocol == "marinade":
            result = await _marinade_build_stake_tx(
                amount_sol=amount,
                user_pubkey=wallet,
            )
        else:
            # Jito and BlazeStake: use Jupiter swap SOL → LST token
            result = await _build_staking_via_jupiter(
                amount_sol=amount,
                user_pubkey=wallet,
                output_mint=output_mint,
                protocol_name=p["name"],
            )

        if result.get("success") and result.get("transaction_b64"):
            out_amount_raw = result.get("out_amount_raw", 0)
            price_impact = result.get("price_impact_pct", "0")

            log.info(
                f"[DeFi] Stake TX built: {protocol} {amount} SOL → {p['token']} "
                f"for {wallet[:8]}... via {result.get('method', 'unknown')}"
            )
            response: dict[str, Any] = {
                "status": "transaction_ready",
                "execution_mode": "wallet_sign",
                "transaction_b64": result["transaction_b64"],
                "method": result.get("method", protocol),
                "protocol": p["name"],
                "action": "stake",
                "amount_sol": amount,
                "receive_token": p["token"],
                "current_apy_percent": apy,
                "estimated_apy": f"{apy}%",
                "yearly_yield_sol": round(amount * apy / 100, 2),
                "description": p["description"],
                "instructions": [
                    "1. Review the transaction details in your wallet",
                    f"2. This will stake {amount} SOL and you will receive {p['token']}",
                    f"3. Estimated APY: {apy}%",
                    f"4. Your {p['token']} is liquid — you can trade or unstake anytime",
                ],
                "note": "Sign this transaction with your Solana wallet (Phantom, Solflare, etc.)",
            }
            if out_amount_raw:
                response["estimated_output_raw"] = out_amount_raw
            if price_impact and price_impact != "0":
                response["price_impact_pct"] = price_impact
            if result.get("lastValidBlockHeight"):
                response["lastValidBlockHeight"] = result["lastValidBlockHeight"]

            return response

    except Exception as e:
        log.warning(f"[DeFi] Failed to build stake TX for {protocol}: {e}")

    # Fallback to manual instructions
    log.info(f"[DeFi] Stake fallback to manual instructions: {protocol} {amount} SOL")
    return _build_manual_instructions(
        protocol_data=p,
        action="stake",
        asset="SOL",
        amount=amount,
        apy=apy,
    )


# ── Helpers ──

def _find_best(action: str) -> dict:
    """Find best rate across all protocols."""
    best: dict[str, Any] = {"asset": "", "protocol": "", "apy": 0}
    key = "supply_apy" if action == "supply" else "borrow_apy"
    for pid, p in LENDING_PROTOCOLS.items():
        for asset, apy in p.get(key, {}).items():
            if action == "supply" and apy > best["apy"]:
                best = {"asset": asset, "protocol": p["name"], "apy": apy}
            elif action == "borrow" and (best["apy"] == 0 or apy < best["apy"]):
                best = {"asset": asset, "protocol": p["name"], "apy": apy}
    return best


log.info(
    f"[DeFi] Solana DeFi (lending/borrowing/staking/LP) monte — "
    f"{len(LENDING_PROTOCOLS)} lending + {len(STAKING_PROTOCOLS)} staking + "
    f"{len(LP_PROTOCOLS)} LP — on-chain TX via protocol APIs + Jupiter, "
    f"rates live via DeFiLlama"
)
