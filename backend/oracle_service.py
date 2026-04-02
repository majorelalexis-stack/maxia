"""MAXIA Oracle & Data Marketplace — Donnees de prix et datasets pour protocols et agents IA."""
import logging
import time, uuid

logger = logging.getLogger(__name__)
import httpx
from http_client import get_http_client
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from price_oracle import get_prices, get_price, get_stock_prices, FALLBACK_PRICES, TOKEN_MINTS
from config import GPU_TIERS, SUPPORTED_NETWORKS

router = APIRouter(prefix="/api/oracle", tags=["oracle"])

# ── Oracle cache (30s) ──
_oracle_cache: dict = {}
_oracle_cache_ts: float = 0
_ORACLE_CACHE_TTL = 30  # secondes

# ── Service start time (pour uptime) ──
_service_start = time.time()

# ── User-created datasets store (in-memory, persisted via DB if needed) ──
_user_datasets: list = []


# ══════════════════════════════════════════════════════════════════════════════
# Part 1 — Oracle MAXIA (price/data oracle for other protocols)
# ══════════════════════════════════════════════════════════════════════════════

def _confidence(age_seconds: float) -> str:
    """Determine la confiance basee sur l'age des donnees."""
    if age_seconds < 300:      # < 5 min
        return "high"
    elif age_seconds < 1800:   # < 30 min
        return "medium"
    return "low"


async def _get_cached_prices() -> tuple:
    """Retourne (prices_dict, cache_age_seconds). Cache 30s."""
    global _oracle_cache, _oracle_cache_ts

    now = time.time()
    if _oracle_cache and now - _oracle_cache_ts < _ORACLE_CACHE_TTL:
        return _oracle_cache, now - _oracle_cache_ts

    prices = await get_prices()
    _oracle_cache = prices
    _oracle_cache_ts = now
    return prices, 0.0


@router.get("/price/{token}")
async def oracle_price(token: str):
    """Prix courant d'un token (depuis price_oracle existant)."""
    symbol = token.upper()
    prices, age = await _get_cached_prices()

    entry = prices.get(symbol)
    if not entry:
        # Essayer un fetch direct
        direct_price = await get_price(symbol)
        if direct_price and direct_price > 0:
            return {
                "token": symbol,
                "price_usd": direct_price,
                "timestamp": int(time.time()),
                "source": "direct_fetch",
                "confidence": "high",
            }
        raise HTTPException(404, f"Token '{symbol}' non trouve dans l'oracle.")

    price_val = entry.get("price", 0) if isinstance(entry, dict) else entry
    source = entry.get("source", "unknown") if isinstance(entry, dict) else "cache"

    return {
        "token": symbol,
        "price_usd": price_val,
        "timestamp": int(time.time()),
        "source": source,
        "confidence": _confidence(age),
    }


@router.get("/prices")
async def oracle_prices_batch(tokens: Optional[str] = Query(None, description="Comma-separated tokens (ex: SOL,ETH,BTC). Omit for all.")):
    """Prix batch de tous les tokens — optimise pour smart contracts."""
    prices, age = await _get_cached_prices()

    if tokens:
        requested = [t.strip().upper() for t in tokens.split(",") if t.strip()]
        filtered = {}
        for sym in requested:
            if sym in prices:
                filtered[sym] = prices[sym]
            elif sym in FALLBACK_PRICES:
                filtered[sym] = {"price": FALLBACK_PRICES[sym], "source": "fallback"}
        prices = filtered

    result = {}
    for sym, entry in prices.items():
        price_val = entry.get("price", 0) if isinstance(entry, dict) else entry
        source = entry.get("source", "unknown") if isinstance(entry, dict) else "cache"
        result[sym] = {
            "price_usd": price_val,
            "source": source,
        }

    return {
        "count": len(result),
        "timestamp": int(time.time()),
        "confidence": _confidence(age),
        "prices": result,
    }


@router.get("/feed")
async def oracle_feed():
    """JSON feed de tous les prix + metadata (timestamp, source, confidence) — pour protocols externes."""
    prices, age = await _get_cached_prices()
    confidence = _confidence(age)
    ts = int(time.time())

    price_list = []
    for sym, entry in prices.items():
        price_val = entry.get("price", 0) if isinstance(entry, dict) else entry
        source = entry.get("source", "unknown") if isinstance(entry, dict) else "cache"
        price_list.append({
            "token": sym,
            "price_usd": price_val,
            "timestamp": ts,
            "source": source,
            "confidence": confidence,
        })

    # Trier par symbole pour un feed stable
    price_list.sort(key=lambda x: x["token"])

    return {
        "prices": price_list,
        "meta": {
            "total_tokens": len(price_list),
            "cache_age_s": round(age, 1),
            "updated_at": ts,
            "oracle": "maxia",
            "version": "v12",
            "chains_supported": len(SUPPORTED_NETWORKS),
        },
    }


@router.get("/health")
async def oracle_health():
    """Oracle uptime, dernier update, fraicheur des donnees."""
    now = time.time()
    uptime_s = now - _service_start
    cache_age = now - _oracle_cache_ts if _oracle_cache_ts > 0 else None

    # Importer les stats du cache prix
    from price_oracle import get_cache_stats
    cache_stats = get_cache_stats()

    # Si le cache est vide, le remplir maintenant
    if not _oracle_cache:
        try:
            await _get_cached_prices()
            cache_age = now - _oracle_cache_ts if _oracle_cache_ts > 0 else None
        except Exception:
            pass

    tokens_count = len(_oracle_cache) if _oracle_cache else len(TOKEN_MINTS)

    return {
        "status": "healthy" if _oracle_cache else "initializing",
        "uptime_s": round(uptime_s, 1),
        "uptime_human": _format_uptime(uptime_s),
        "last_update": int(_oracle_cache_ts) if _oracle_cache_ts > 0 else None,
        "last_updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(_oracle_cache_ts))) if _oracle_cache_ts > 0 else None,
        "data_freshness_s": round(cache_age, 1) if cache_age is not None else None,
        "freshness": _confidence(cache_age) if cache_age is not None else "unknown",
        "data_freshness": _confidence(cache_age) if cache_age is not None else "unknown",
        "tokens_tracked": tokens_count,
        "sources": f"Helius DAS + CoinGecko + {len(SUPPORTED_NETWORKS)} chains",
        "cache_ttl_s": _ORACLE_CACHE_TTL,
        "price_oracle_stats": cache_stats,
        "chains_supported": len(SUPPORTED_NETWORKS),
        "networks": SUPPORTED_NETWORKS,
    }


@router.get("/fear-greed")
async def oracle_fear_greed():
    """Real-time Crypto Fear & Greed Index from alternative.me (free API)."""
    value, label = await _fetch_real_fear_greed()
    return {
        "value": value,
        "label": label,
        "source": "alternative.me" if _fear_greed_cache else "fallback",
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def _format_uptime(seconds: float) -> str:
    """Formate l'uptime en texte lisible."""
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    minutes = int((seconds % 3600) // 60)
    if days > 0:
        return f"{days}d {hours}h {minutes}m"
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


# ══════════════════════════════════════════════════════════════════════════════
# Part 2 — Data Marketplace (built-in + user datasets)
# ══════════════════════════════════════════════════════════════════════════════

async def _build_crypto_prices_dataset() -> dict:
    """Dataset 1: Prix crypto en temps reel (107 tokens, 14 chains)."""
    prices, _ = await _get_cached_prices()
    stock_syms = {"AAPL", "TSLA", "NVDA", "GOOGL", "MSFT", "AMZN", "META", "MSTR", "SPY", "QQQ"}
    crypto_entries = []
    for sym, entry in prices.items():
        if sym in stock_syms:
            continue
        price_val = entry.get("price", 0) if isinstance(entry, dict) else entry
        source = entry.get("source", "unknown") if isinstance(entry, dict) else "cache"
        crypto_entries.append({
            "token": sym,
            "price_usd": price_val,
            "source": source,
            "mint": TOKEN_MINTS.get(sym, ""),
        })
    crypto_entries.sort(key=lambda x: x["token"])
    return {
        "id": "crypto-prices-14chains",
        "name": "Crypto Prices (14 Chains)",
        "description": "Prix en temps reel de 107 tokens sur 14 blockchains (Solana, Base, Ethereum, XRP, Polygon, Arbitrum, Avalanche, BNB, TON, SUI, TRON, NEAR, Aptos, SEI). Sources: Helius DAS, CoinGecko, fallback.",
        "format": "json",
        "records": len(crypto_entries),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "price_usdc": 0,
        "category": "crypto",
        "sample": crypto_entries[:3],
        "data": crypto_entries,
    }


def _build_gpu_pricing_dataset() -> dict:
    """Dataset 2: Comparaison prix GPU (MAXIA vs marche)."""
    gpu_entries = []
    # Comparaisons approximatives marche (mars 2026)
    market_comparison = {
        "rtx4090":   {"aws": 1.50, "runpod": 0.74, "lambda": 0.80},
        "a100_80":   {"aws": 4.10, "runpod": 1.89, "lambda": 2.00},
        "h100_sxm5": {"aws": 6.50, "runpod": 2.79, "lambda": 3.00},
        "h200_sxm":  {"aws": 8.00, "runpod": 4.49, "lambda": 5.00},
        "a6000":     {"aws": 2.00, "runpod": 1.09, "lambda": 1.20},
        "4xa100":    {"aws": 16.0, "runpod": 7.56, "lambda": 8.00},
    }
    for tier in GPU_TIERS:
        tid = tier["id"]
        comp = market_comparison.get(tid, {})
        gpu_entries.append({
            "gpu": tier["label"],
            "tier_id": tid,
            "vram_gb": tier["vram_gb"],
            "maxia_price_per_hour": tier["base_price_per_hour"],
            "aws_price_per_hour": comp.get("aws", None),
            "runpod_price_per_hour": comp.get("runpod", None),
            "lambda_price_per_hour": comp.get("lambda", None),
            "maxia_savings_vs_aws_pct": round((1 - tier["base_price_per_hour"] / comp["aws"]) * 100, 1) if comp.get("aws") else None,
        })
    return {
        "id": "gpu-pricing",
        "name": "GPU Pricing Comparison",
        "description": "Comparaison des prix GPU par heure entre MAXIA, AWS, RunPod et Lambda Labs. 6 tiers de RTX 4090 a 4x A100.",
        "format": "json",
        "records": len(gpu_entries),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "price_usdc": 0,
        "category": "gpu",
        "sample": gpu_entries[:3],
        "data": gpu_entries,
    }


async def _build_stock_prices_dataset() -> dict:
    """Dataset 3: Prix des actions tokenisees (10 xStocks)."""
    stock_prices = await get_stock_prices()
    stock_entries = []
    for sym, entry in stock_prices.items():
        price_val = entry.get("price", 0) if isinstance(entry, dict) else entry
        source = entry.get("source", "unknown") if isinstance(entry, dict) else "cache"
        change = entry.get("change", 0) if isinstance(entry, dict) else 0
        stock_entries.append({
            "symbol": sym,
            "price_usd": price_val,
            "change_pct": change,
            "source": source,
            "tokenized": sym in TOKEN_MINTS,
        })
    stock_entries.sort(key=lambda x: x["symbol"])
    return {
        "id": "stock-prices",
        "name": "Tokenized Stock Prices",
        "description": "Prix en temps reel de 10 actions tokenisees sur Solana (xStocks Backed Finance): AAPL, TSLA, NVDA, GOOGL, MSFT, AMZN, META, MSTR, SPY, QQQ.",
        "format": "json",
        "records": len(stock_entries),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "price_usdc": 0,
        "category": "stocks",
        "sample": stock_entries[:3],
        "data": stock_entries,
    }


async def _build_defi_yields_dataset() -> dict:
    """Dataset 4: Meilleurs rendements DeFi (live via DeFiLlama)."""
    defi_entries = []
    # Protocoles cibles avec leurs cles DeFiLlama
    _TARGET_POOLS = [
        ("marinade-finance", "msol", "Marinade", "Solana", "SOL", "low"),
        ("jito", "jitosol", "Jito", "Solana", "SOL", "low"),
        ("raydium", "sol-usdc", "Raydium", "Solana", "SOL/USDC", "medium"),
        ("aave-v3", "usdc", "Aave V3", "Ethereum", "USDC", "low"),
        ("compound-v3", "usdc", "Compound V3", "Ethereum", "USDC", "low"),
        ("gmx-v2", "glp", "GMX", "Arbitrum", "GLP", "medium"),
        ("kamino-lend", "usdc", "Kamino", "Solana", "USDC", "low"),
        ("sanctum", "inf", "Sanctum", "Solana", "INF", "medium"),
    ]
    try:
        client = get_http_client()
        resp = await client.get("https://yields.llama.fi/pools", timeout=15)
        resp.raise_for_status()
        pools = resp.json().get("data", [])
        # Index par project_symbol
        pool_index = {}
        for p in pools:
            key = f"{p.get('project', '').lower()}_{p.get('symbol', '').lower()}"
            if key not in pool_index or p.get("apy", 0) > pool_index[key].get("apy", 0):
                pool_index[key] = p

        for project_key, symbol_key, display_name, chain, asset, risk in _TARGET_POOLS:
            lookup_key = f"{project_key}_{symbol_key}"
            pool_data = pool_index.get(lookup_key, {})
            apy = round(pool_data.get("apy", 0), 2)
            tvl = round(pool_data.get("tvlUsd", 0), 0)
            if apy > 0:
                defi_entries.append({
                    "protocol": display_name, "chain": chain, "asset": asset,
                    "apy_pct": apy, "tvl_usd": tvl, "risk": risk,
                    "source": "defillama_live",
                })

        # Si on a aussi des pools Aave Polygon/Arbitrum, les chercher
        for p in pools:
            proj = (p.get("project") or "").lower()
            chain_raw = (p.get("chain") or "").lower()
            sym = (p.get("symbol") or "").upper()
            apy = p.get("apy", 0)
            tvl = p.get("tvlUsd", 0)
            if proj == "aave-v3" and "USDC" in sym and tvl > 100_000_000:
                if chain_raw == "polygon":
                    defi_entries.append({"protocol": "Aave V3", "chain": "Polygon", "asset": "USDC",
                                         "apy_pct": round(apy, 2), "tvl_usd": round(tvl, 0), "risk": "low", "source": "defillama_live"})
                elif chain_raw == "arbitrum":
                    defi_entries.append({"protocol": "Aave V3", "chain": "Arbitrum", "asset": "USDC",
                                         "apy_pct": round(apy, 2), "tvl_usd": round(tvl, 0), "risk": "low", "source": "defillama_live"})
    except Exception as e:
        logger.error(f"[Oracle] DeFiLlama fetch error pour dataset yields: {e}")

    # Deduplication par protocol+chain
    seen = set()
    unique = []
    for entry in defi_entries:
        k = f"{entry['protocol']}_{entry['chain']}_{entry['asset']}"
        if k not in seen:
            seen.add(k)
            unique.append(entry)
    defi_entries = unique

    return {
        "id": "defi-yields",
        "name": "Best DeFi Yields",
        "description": "Meilleurs rendements DeFi live via DeFiLlama. Solana, Ethereum, Polygon et Arbitrum. Mis a jour toutes les 5 min.",
        "format": "json",
        "records": len(defi_entries),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "price_usdc": 0,
        "category": "defi",
        "sample": defi_entries[:3],
        "data": defi_entries,
    }


_fear_greed_cache: dict = {}
_fear_greed_cache_ts: float = 0
_FEAR_GREED_CACHE_TTL = 600  # 10 min


async def _fetch_real_fear_greed() -> tuple[int, str]:
    """Fetch real Fear & Greed Index from alternative.me API (free, no key)."""
    global _fear_greed_cache, _fear_greed_cache_ts
    now = time.time()
    if _fear_greed_cache and now - _fear_greed_cache_ts < _FEAR_GREED_CACHE_TTL:
        return _fear_greed_cache["value"], _fear_greed_cache["label"]
    try:
        client = get_http_client()
        resp = await client.get("https://api.alternative.me/fcp/v2/", timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            entry = data.get("data", {})
            if entry:
                first_key = next(iter(entry))
                val = int(entry[first_key].get("value", 50))
                label = entry[first_key].get("value_classification", "Neutral")
                _fear_greed_cache = {"value": val, "label": label}
                _fear_greed_cache_ts = now
                return val, label
    except Exception as e:
        logger.warning("Fear & Greed API error: %s — using fallback", e)
    return 50, "Neutral"


def _build_fear_greed_dataset() -> dict:
    """Dataset 5: Crypto Fear & Greed Index (real API: alternative.me)."""
    # Use cached value synchronously — async fetch happens via /oracle/fear-greed endpoint
    index_value = _fear_greed_cache.get("value", 50)
    label = _fear_greed_cache.get("label", "Neutral")

    entries = [
        {
            "date": time.strftime("%Y-%m-%d", time.gmtime()),
            "value": index_value,
            "label": label,
            "source": "alternative.me" if _fear_greed_cache else "fallback",
        }
    ]
    return {
        "id": "fear-greed-index",
        "name": "Crypto Fear & Greed Index",
        "description": "Real-time Crypto Fear & Greed Index from alternative.me. Scale 0 (extreme fear) to 100 (extreme greed).",
        "format": "json",
        "records": 1,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "price_usdc": 0,
        "category": "sentiment",
        "sample": entries,
        "data": entries,
    }


# ── Catalogue des built-in datasets ──

BUILTIN_DATASET_IDS = [
    "crypto-prices-14chains",
    "gpu-pricing",
    "stock-prices",
    "defi-yields",
    "fear-greed-index",
]


async def _get_builtin_dataset(dataset_id: str) -> Optional[dict]:
    """Construit et retourne un dataset built-in par ID."""
    if dataset_id == "crypto-prices-14chains":
        return await _build_crypto_prices_dataset()
    elif dataset_id == "gpu-pricing":
        return _build_gpu_pricing_dataset()
    elif dataset_id == "stock-prices":
        return await _build_stock_prices_dataset()
    elif dataset_id == "defi-yields":
        return await _build_defi_yields_dataset()
    elif dataset_id == "fear-greed-index":
        return _build_fear_greed_dataset()
    return None


async def _list_builtin_datasets_meta() -> list:
    """Retourne les metadonnees (sans data complete) de tous les datasets built-in."""
    result = []
    for did in BUILTIN_DATASET_IDS:
        ds = await _get_builtin_dataset(did)
        if ds:
            meta = {k: v for k, v in ds.items() if k != "data"}
            result.append(meta)
    return result


# ── Pydantic models ──

class CreateDatasetRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2000)
    category: str = Field(min_length=1, max_length=50)
    format: str = "json"
    price_usdc: float = Field(ge=0, le=100000)
    data: list = Field(min_length=1)


# ── Endpoints Data Marketplace ──

@router.get("/datasets")
async def list_datasets(category: Optional[str] = None, free_only: bool = False):
    """Liste tous les datasets disponibles (built-in + utilisateurs)."""
    # Built-in datasets
    datasets = await _list_builtin_datasets_meta()

    # User-created datasets (sans les data completes)
    for ds in _user_datasets:
        meta = {k: v for k, v in ds.items() if k != "data"}
        datasets.append(meta)

    # Filtres
    if category:
        datasets = [d for d in datasets if d.get("category", "").lower() == category.lower()]
    if free_only:
        datasets = [d for d in datasets if d.get("price_usdc", 0) == 0]

    return {
        "count": len(datasets),
        "datasets": datasets,
    }


@router.get("/dataset/{dataset_id}")
async def get_dataset(dataset_id: str):
    """Retourne un dataset complet (metadata + sample). Les donnees completes sont dans 'data'."""
    # Chercher dans les built-in
    ds = await _get_builtin_dataset(dataset_id)
    if ds:
        # Pour les datasets gratuits, retourner toutes les donnees
        if ds.get("price_usdc", 0) == 0:
            return ds
        # Pour les payants, retourner seulement le sample
        return {k: v for k, v in ds.items() if k != "data"}

    # Chercher dans les datasets utilisateur
    for uds in _user_datasets:
        if uds.get("id") == dataset_id:
            if uds.get("price_usdc", 0) == 0:
                return uds
            return {k: v for k, v in uds.items() if k != "data"}

    raise HTTPException(404, f"Dataset '{dataset_id}' non trouve.")


@router.post("/dataset")
async def create_dataset(req: CreateDatasetRequest, api_key: str = Query(..., description="API key pour creer un dataset")):
    """Creer/vendre un nouveau dataset (necessite une API key)."""
    # Validation basique de l'API key (non vide)
    if not api_key or len(api_key) < 8:
        raise HTTPException(401, "API key invalide ou manquante.")

    dataset_id = f"user-{uuid.uuid4().hex[:12]}"
    sample = req.data[:3] if len(req.data) >= 3 else req.data

    dataset = {
        "id": dataset_id,
        "name": req.name,
        "description": req.description,
        "category": req.category,
        "format": req.format,
        "records": len(req.data),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "price_usdc": req.price_usdc,
        "sample": sample,
        "data": req.data,
        "created_by": api_key[:8] + "...",
    }

    _user_datasets.append(dataset)

    # Retourner sans les data completes
    return {
        "ok": True,
        "dataset_id": dataset_id,
        "dataset": {k: v for k, v in dataset.items() if k != "data"},
    }
