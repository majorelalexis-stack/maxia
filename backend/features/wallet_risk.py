"""MAXIA E35 — Wallet Risk Scoring Service

Multi-chain wallet risk assessment using on-chain heuristics.
Inspired by januusio/cryptowallet_risk_scoring methodology:
  3 dimensions (reputation, fraud, financial_health), 0-100 scale,
  composite risk_score, risk_level thresholds (LOW/MEDIUM/HIGH/CRITICAL).

Supported: 7 EVM chains + Solana. Price: $0.10/call.
"""
from __future__ import annotations
import logging, os, re, time
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from core.http_client import get_http_client

logger = logging.getLogger("maxia.wallet_risk")
router = APIRouter(prefix="/api/risk", tags=["risk"])

# ── Chain RPC config ──
_EVM_CHAINS: dict[str, dict] = {
    "ethereum":  {"rpc_env": "ETH_RPC",      "rpc_default": "https://eth.llamarpc.com",                "symbol": "ETH"},
    "base":      {"rpc_env": "BASE_RPC",      "rpc_default": "https://mainnet.base.org",               "symbol": "ETH"},
    "polygon":   {"rpc_env": "POLYGON_RPC",   "rpc_default": "https://polygon-rpc.com",                "symbol": "MATIC"},
    "arbitrum":  {"rpc_env": "ARBITRUM_RPC",   "rpc_default": "https://arb1.arbitrum.io/rpc",           "symbol": "ETH"},
    "avalanche": {"rpc_env": "AVALANCHE_RPC",  "rpc_default": "https://api.avax.network/ext/bc/C/rpc",  "symbol": "AVAX"},
    "bnb":       {"rpc_env": "BNB_RPC",        "rpc_default": "https://bsc-dataseed.binance.org",       "symbol": "BNB"},
    "sei":       {"rpc_env": "SEI_RPC",        "rpc_default": "https://evm-rpc.sei-apis.com",           "symbol": "SEI"},
}
SUPPORTED_CHAINS = list(_EVM_CHAINS.keys()) + ["solana"]

# ── OFAC sanctioned addresses (loaded once, O(1) lookup) ──
_ofac_addresses: set[str] = set()
_ofac_loaded: bool = False

def _load_ofac() -> set[str]:
    global _ofac_addresses, _ofac_loaded
    if _ofac_loaded:
        return _ofac_addresses
    ofac_path = Path(__file__).parent.parent / ".ofac_addresses.txt"
    if ofac_path.exists():
        try:
            raw = ofac_path.read_text(encoding="utf-8", errors="replace")
            for line in raw.splitlines():
                addr = line.strip().lower()
                if addr and not addr.startswith("#"):
                    _ofac_addresses.add(addr)
            logger.info("[WalletRisk] Loaded %d OFAC addresses", len(_ofac_addresses))
        except Exception as exc:
            logger.warning("[WalletRisk] Failed to load OFAC list: %s", exc)
    _ofac_loaded = True
    return _ofac_addresses

# Known mixer/tumbler contracts (Tornado Cash, Railgun)
KNOWN_MIXERS: set[str] = {
    "0xd90e2f925da726b50c4ed8d0fb90ad053324f31b",
    "0x722122df12d4e14e13ac3b6895a86e84145b6967",
    "0x12d66f87a04a9e220743712ce6d9bb1b5616b8fc",
    "0x47ce0c6ed5b0ce3d3a51fdb1c52dc66a7c3c2936",
    "0x910cbd523d972eb0a6f4cae4618ad62622b39dbf",
    "0xfa7093cdd9ee6932b4eb2c9e1cde7ce00b1fa4b9",
}

# ── Cache (1h TTL) ──
_CACHE_TTL_S: int = 3600
_score_cache: dict[str, tuple[float, dict]] = {}

def _cache_get(address: str, chain: str) -> Optional[dict]:
    key = f"{chain}:{address.lower()}"
    entry = _score_cache.get(key)
    if entry is None:
        return None
    ts, result = entry
    if time.time() - ts > _CACHE_TTL_S:
        _score_cache.pop(key, None)
        return None
    return result

def _cache_set(address: str, chain: str, result: dict) -> None:
    _score_cache[f"{chain}:{address.lower()}"] = (time.time(), result)

# ── Validation ──
_EVM_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_SOL_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

def _validate_address(address: str, chain: str) -> bool:
    return bool(_SOL_RE.match(address)) if chain == "solana" else bool(_EVM_RE.match(address))

# ── RPC helpers ──
def _get_evm_rpc(chain: str) -> str:
    cfg = _EVM_CHAINS[chain]
    return os.getenv(cfg["rpc_env"], cfg["rpc_default"])

def _get_solana_rpc() -> str:
    try:
        from core.config import get_rpc_url
        return get_rpc_url()
    except Exception:
        return os.getenv("SOLANA_RPC", "https://api.mainnet-beta.solana.com")

async def _evm_rpc(rpc_url: str, method: str, params: list) -> Optional[dict]:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    try:
        resp = await get_http_client().post(rpc_url, json=payload, timeout=12.0)
        if resp.status_code == 200:
            return resp.json()
    except Exception as exc:
        logger.warning("[WalletRisk] EVM %s failed: %s", method, exc)
    return None

async def _sol_rpc(method: str, params: list) -> Optional[dict]:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    try:
        resp = await get_http_client().post(_get_solana_rpc(), json=payload, timeout=12.0)
        if resp.status_code == 200:
            return resp.json()
    except Exception as exc:
        logger.warning("[WalletRisk] Solana %s failed: %s", method, exc)
    return None

# ── On-chain data fetchers ──
async def _fetch_evm_data(address: str, chain: str) -> dict:
    rpc_url = _get_evm_rpc(chain)
    addr = address.lower()
    tx_count, balance_wei = 0, 0
    data = await _evm_rpc(rpc_url, "eth_getTransactionCount", [addr, "latest"])
    if data and "result" in data:
        try: tx_count = int(data["result"], 16)
        except (ValueError, TypeError): pass
    data = await _evm_rpc(rpc_url, "eth_getBalance", [addr, "latest"])
    if data and "result" in data:
        try: balance_wei = int(data["result"], 16)
        except (ValueError, TypeError): pass
    return {
        "tx_count": tx_count,
        "balance_native": balance_wei / 1e18,
        "symbol": _EVM_CHAINS[chain]["symbol"],
    }

async def _fetch_solana_data(address: str) -> dict:
    balance_lamports, tx_count, oldest_slot, newest_slot = 0, 0, 0, 0
    data = await _sol_rpc("getBalance", [address])
    if data and "result" in data:
        val = data["result"]
        balance_lamports = val.get("value", 0) if isinstance(val, dict) else (val if isinstance(val, int) else 0)
    data = await _sol_rpc("getSignaturesForAddress", [address, {"limit": 100}])
    if data and "result" in data:
        sigs = data["result"]
        if isinstance(sigs, list):
            tx_count = len(sigs)
            slots = [s.get("slot", 0) for s in sigs if s.get("slot")]
            if slots:
                oldest_slot, newest_slot = min(slots), max(slots)
    return {
        "tx_count": tx_count, "balance_native": balance_lamports / 1e9,
        "symbol": "SOL", "oldest_slot": oldest_slot, "newest_slot": newest_slot,
    }

# ── Scoring (januusio-inspired, 3 dimensions, 0-100 each) ──
def _score_reputation(tx_count: int, balance: float) -> tuple[int, list[str]]:
    """0-100 (lower = better). Factors: tx count, balance level."""
    score, reasons = 30, []  # neutral baseline per januusio
    if tx_count == 0:
        score += 40; reasons.append("No transaction history")
    elif tx_count < 5:
        score += 25; reasons.append(f"Very low activity ({tx_count} txs)")
    elif tx_count < 20:
        score += 10; reasons.append(f"Moderate activity ({tx_count} txs)")
    elif tx_count < 100:
        score -= 10; reasons.append(f"Good activity ({tx_count} txs)")
    else:
        score -= 20; reasons.append(f"Highly active ({tx_count}+ txs)")
    if balance <= 0:
        score += 15; reasons.append("Zero balance")
    elif balance < 0.01:
        score += 10; reasons.append("Dust-level balance")
    elif balance < 1.0:
        score -= 5; reasons.append("Small but non-trivial balance")
    elif balance < 100.0:
        score -= 10; reasons.append("Healthy balance")
    else:
        score -= 15; reasons.append("Substantial balance")
    return max(0, min(100, score)), reasons

def _score_fraud(address: str, chain: str, tx_count: int) -> tuple[int, list[str]]:
    """0-100 (lower = better). Factors: OFAC, mixers, heuristics."""
    score, reasons = 10, []
    addr_lower = address.lower()
    if addr_lower in _load_ofac():
        return 95, ["OFAC sanctioned address — direct match"]
    if chain != "solana" and addr_lower in KNOWN_MIXERS:
        score += 60; reasons.append("Known mixer/tumbler contract")
    if tx_count == 0:
        score += 10; reasons.append("No outgoing txs — possibly single-use")
    if chain != "solana" and addr_lower.startswith("0x0000"):
        score += 5; reasons.append("Unusual address prefix")
    return max(0, min(100, score)), reasons

def _score_financial(
    balance: float, tx_count: int, chain: str,
    oldest_slot: int = 0, newest_slot: int = 0,
) -> tuple[int, list[str]]:
    """0-100 (lower = healthier). Factors: balance, maturity, dust patterns."""
    score, reasons = 25, []
    if balance <= 0:
        score += 30; reasons.append("Empty wallet")
    elif balance < 0.001:
        score += 20; reasons.append("Dust-level balance")
    elif balance < 0.1:
        score += 5; reasons.append("Minimal balance")
    elif balance >= 10.0:
        score -= 15; reasons.append("Strong balance")
    else:
        score -= 5; reasons.append("Adequate balance")
    # Age estimation
    if chain == "solana" and oldest_slot > 0 and newest_slot > 0:
        age_days = (newest_slot - oldest_slot) / 2 / 86400  # ~2 slots/sec
        if age_days > 180:
            score -= 15; reasons.append(f"Mature wallet (~{int(age_days)}d)")
        elif age_days > 30:
            score -= 5; reasons.append(f"Active ~{int(age_days)} days")
        elif age_days < 1 and tx_count > 10:
            score += 15; reasons.append("Burst of activity in <24h")
    else:
        if tx_count > 200:
            score -= 10; reasons.append("Long-established (high tx count)")
        elif tx_count > 50:
            score -= 5; reasons.append("Moderate lifetime txs")
    if tx_count > 20 and balance < 0.001:
        score += 15; reasons.append("Many txs but near-zero balance — drain pattern")
    return max(0, min(100, score)), reasons

def _composite(rep: int, fraud: int, fin: int) -> tuple[int, str]:
    """Weighted composite: fraud 50%, reputation 25%, financial 25%."""
    w = int(fraud * 0.50 + rep * 0.25 + fin * 0.25)
    if fraud >= 60 or rep >= 80:
        w = max(w, 60)  # failing dimension override (januusio rule)
    w = max(0, min(100, w))
    if w < 25: return w, "LOW"
    if w < 45: return w, "MEDIUM"
    if w < 60: return w, "HIGH"
    return w, "CRITICAL"

# ── Pydantic models ──
class WalletScoreResponse(BaseModel):
    address: str
    chain: str
    risk_score: int = Field(ge=0, le=100, description="0=safe, 100=max risk")
    risk_level: str
    reputation_score: int = Field(ge=0, le=100)
    fraud_score: int = Field(ge=0, le=100)
    financial_health_score: int = Field(ge=0, le=100)
    reasons: list[str]
    on_chain: dict = Field(default_factory=dict)
    cached: bool = False
    price_usd: float = 0.10

class BatchRequest(BaseModel):
    addresses: list[dict] = Field(..., min_length=1, max_length=20)

class BatchResponse(BaseModel):
    results: list[WalletScoreResponse]
    total: int
    errors: int

def _error_result(addr: str, chain: str, msg: str) -> dict:
    return {
        "address": addr, "chain": chain, "risk_score": -1, "risk_level": "ERROR",
        "reputation_score": -1, "fraud_score": -1, "financial_health_score": -1,
        "reasons": [msg], "on_chain": {}, "cached": False, "price_usd": 0.10,
    }

# ── Core scoring ──
async def score_wallet(address: str, chain: str) -> dict:
    """Score a wallet's risk across 3 dimensions."""
    ch = chain.lower()
    if ch not in SUPPORTED_CHAINS:
        raise HTTPException(400, f"Unsupported chain: {chain}. Supported: {SUPPORTED_CHAINS}")
    if not _validate_address(address, ch):
        raise HTTPException(400, f"Invalid {ch} address format")
    cached = _cache_get(address, ch)
    if cached is not None:
        return {**cached, "cached": True}
    on_chain = await (_fetch_solana_data(address) if ch == "solana" else _fetch_evm_data(address, ch))
    tx = on_chain.get("tx_count", 0)
    bal = on_chain.get("balance_native", 0.0)
    rep, rep_r = _score_reputation(tx, bal)
    fraud, fraud_r = _score_fraud(address, ch, tx)
    fin, fin_r = _score_financial(bal, tx, ch, on_chain.get("oldest_slot", 0), on_chain.get("newest_slot", 0))
    composite, level = _composite(rep, fraud, fin)
    result = {
        "address": address, "chain": ch,
        "risk_score": composite, "risk_level": level,
        "reputation_score": rep, "fraud_score": fraud, "financial_health_score": fin,
        "reasons": rep_r + fraud_r + fin_r,
        "on_chain": {"tx_count": tx, "balance": round(bal, 6), "symbol": on_chain.get("symbol", "")},
        "cached": False, "price_usd": 0.10,
    }
    _cache_set(address, ch, result)
    logger.info("[WalletRisk] %s on %s -> score=%d %s (rep=%d fraud=%d fin=%d)",
                address[:12], ch, composite, level, rep, fraud, fin)
    return result

# ── Endpoints ──
@router.get("/wallet-score", response_model=WalletScoreResponse)
async def get_wallet_score(
    address: str = Query(..., min_length=20, max_length=64, description="Wallet address"),
    chain: str = Query("ethereum", description=f"Chain: {', '.join(SUPPORTED_CHAINS)}"),
) -> dict:
    """Score a single wallet's risk (3 dimensions, on-chain heuristics, 1h cache)."""
    return await score_wallet(address, chain)

@router.post("/batch", response_model=BatchResponse)
async def batch_wallet_scores(body: BatchRequest) -> dict:
    """Score up to 20 wallets. Body: {"addresses": [{"address":"...","chain":"..."}]}"""
    results, errors = [], 0
    for item in body.addresses:
        addr, ch = item.get("address", ""), item.get("chain", "ethereum")
        try:
            results.append(await score_wallet(addr, ch))
        except HTTPException:
            errors += 1
            results.append(_error_result(addr, ch, f"Scoring failed for {addr} on {ch}"))
        except Exception as exc:
            errors += 1
            logger.warning("[WalletRisk] Batch error %s: %s", addr, exc)
            results.append(_error_result(addr, ch, "Internal scoring error"))
    return {"results": results, "total": len(results), "errors": errors}

@router.get("/supported-chains")
async def get_supported_chains() -> dict:
    """List supported chains for wallet risk scoring."""
    return {
        "chains": SUPPORTED_CHAINS, "evm_chains": list(_EVM_CHAINS.keys()),
        "non_evm_chains": ["solana"], "cache_ttl_seconds": _CACHE_TTL_S,
        "price_per_call_usd": 0.10,
    }

@router.get("/stats")
async def get_risk_stats() -> dict:
    """Cache size, OFAC count, supported chains."""
    return {
        "cache_entries": len(_score_cache), "cache_ttl_seconds": _CACHE_TTL_S,
        "ofac_addresses_loaded": len(_load_ofac()), "supported_chains": len(SUPPORTED_CHAINS),
        "mixer_contracts_tracked": len(KNOWN_MIXERS),
    }
