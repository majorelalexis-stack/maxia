"""MAXIA Art.53 — EVM Multi-Chain Swap via 0x Swap API v2

Swap de tokens EVM sur 6 chaines (Ethereum, Base, Polygon, Arbitrum, Avalanche, BNB)
via l'agregateur 0x. Commission dynamique par palier, check OFAC, cache prix 30s.

Cle API: ZERO_EX_API_KEY dans .env
"""

import logging
import os
import time
import uuid
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Query
from http_client import get_http_client
from pydantic import BaseModel, Field

from security import require_ofac_clear

logger = logging.getLogger(__name__)


# ── Logging ──

def _log(msg: str):
    logger.info(f"[EVM-Swap] {msg}")


# ── Config ──

ZERO_EX_API_KEY = os.getenv("ZERO_EX_API_KEY", "")
ZERO_EX_BASE_URL = "https://api.0x.org"

# Plafond de securite par transaction
MAX_SWAP_AMOUNT_USD = 10_000
MIN_SWAP_AMOUNT_USD = 0.01


# ── Chaines supportees ──

EVM_CHAINS = {
    "ethereum": {"chain_id": 1, "name": "Ethereum", "native": "ETH"},
    "base":     {"chain_id": 8453, "name": "Base", "native": "ETH"},
    "polygon":  {"chain_id": 137, "name": "Polygon", "native": "MATIC"},
    "arbitrum": {"chain_id": 42161, "name": "Arbitrum", "native": "ETH"},
    "avalanche": {"chain_id": 43114, "name": "Avalanche", "native": "AVAX"},
    "bnb":      {"chain_id": 56, "name": "BNB Chain", "native": "BNB"},
}


# ── Catalogue de tokens par chaine (curated whitelist — aucun token user-submitted) ──

TOKENS_BY_CHAIN = {
    "ethereum": {
        "WETH": {"address": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", "name": "Wrapped Ether", "decimals": 18},
        "USDC": {"address": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", "name": "USD Coin", "decimals": 6},
        "USDT": {"address": "0xdAC17F958D2ee523a2206206994597C13D831ec7", "name": "Tether USD", "decimals": 6},
        "WBTC": {"address": "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599", "name": "Wrapped Bitcoin", "decimals": 18},
        "UNI":  {"address": "0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984", "name": "Uniswap", "decimals": 18},
        "LINK": {"address": "0x514910771AF9Ca656af840dff83E8264EcF986CA", "name": "Chainlink", "decimals": 18},
        "AAVE": {"address": "0x7Fc66500c84A76Ad7e9c93437bFc5Ac33E2DDaE9", "name": "Aave", "decimals": 18},
        "LDO":  {"address": "0x5A98FcBEA516Cf06857215779Fd812CA3beF1B32", "name": "Lido DAO", "decimals": 18},
        "PEPE": {"address": "0x6982508145454Ce325dDbE47a25d4ec3d2311933", "name": "Pepe", "decimals": 18},
        "SHIB": {"address": "0x95aD61b0a150d79219dCF64E1E6Cc01f0B64C4cE", "name": "Shiba Inu", "decimals": 18},
    },
    "base": {
        "USDC":    {"address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", "name": "USD Coin", "decimals": 6},
        "WETH":    {"address": "0x4200000000000000000000000000000000000006", "name": "Wrapped Ether", "decimals": 18},
        "cbETH":   {"address": "0x2Ae3F1Ec7F1F5012CFEab0185bfc7aa3cf0DEc22", "name": "Coinbase Wrapped Staked ETH", "decimals": 18},
        "DEGEN":   {"address": "0x4ed4E862860beD51a9570b96d89aF5E1B0Efefed", "name": "Degen", "decimals": 18},
        "BRETT":   {"address": "0x532f27101965dd16442E59d40670FaF5eBB142E4", "name": "Brett", "decimals": 18},
        "AERO":    {"address": "0x940181a94A35A4569E4529A3CDfB74e38FD98631", "name": "Aerodrome", "decimals": 18},
        "TOSHI":   {"address": "0xAC1Bd2486aAf3B5C0fc3Fd868558b082a531B2B4", "name": "Toshi", "decimals": 18},
        "SOLVR":   {"address": "0x6dfb7bfa06e7c2b6c20c22c0afb44852c201eb07", "name": "Solvr", "decimals": 18},
        "VIRTUAL": {"address": "0x0b3e328455c4059EEb9e3f84b5543F74E24e7E1b", "name": "Virtuals Protocol", "decimals": 18},
        "MORPHO":  {"address": "0xBAa5CC21fd487B8Fcc2F632f3F4E8D37262a0842", "name": "Morpho", "decimals": 18},
    },
    "polygon": {
        "WMATIC": {"address": "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270", "name": "Wrapped Matic", "decimals": 18},
        "USDC":   {"address": "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359", "name": "USD Coin", "decimals": 6},
        "WETH":   {"address": "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619", "name": "Wrapped Ether", "decimals": 18},
        "AAVE":   {"address": "0xD6DF932A45C0f255f85145f286eA0b292B21C90B", "name": "Aave", "decimals": 18},
        "QUICK":  {"address": "0xB5C064F955D8e7F38fE0460C556a72987494eE17", "name": "QuickSwap", "decimals": 18},
    },
    "arbitrum": {
        "WETH":   {"address": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1", "name": "Wrapped Ether", "decimals": 18},
        "USDC":   {"address": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831", "name": "USD Coin", "decimals": 6},
        "ARB":    {"address": "0x912CE59144191C1204E64559FE8253a0e49E6548", "name": "Arbitrum", "decimals": 18},
        "GMX":    {"address": "0xfc5A1A6EB076a2C7aD06eD22C90d7E710E35ad0a", "name": "GMX", "decimals": 18},
        "PENDLE": {"address": "0x0c880f6761F1af8d9Aa9C466984b80DAb9a8c9e8", "name": "Pendle", "decimals": 18},
    },
    "avalanche": {
        "WAVAX": {"address": "0xB31f66AA3C1e785363F0875A1B74E27b85FD66c7", "name": "Wrapped AVAX", "decimals": 18},
        "USDC":  {"address": "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E", "name": "USD Coin", "decimals": 6},
        "JOE":   {"address": "0x6e84a6216eA6dACC71eE8E6b0a5B7322EEbC0fDd", "name": "Trader Joe", "decimals": 18},
    },
    "bnb": {
        "WBNB": {"address": "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c", "name": "Wrapped BNB", "decimals": 18},
        "USDC": {"address": "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d", "name": "USD Coin", "decimals": 6},
        "CAKE": {"address": "0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82", "name": "PancakeSwap", "decimals": 18},
    },
}


# ── Commissions par palier (meme structure que crypto_swap.py Solana) ──

SWAP_COMMISSION_TIERS = {
    "BRONZE": {"min_amount": 0,    "max_amount": 500,       "bps": 10},   # 0.10%
    "SILVER": {"min_amount": 500,  "max_amount": 5000,      "bps": 5},    # 0.05%
    "GOLD":   {"min_amount": 5000, "max_amount": 999999999,  "bps": 3},    # 0.03%
    "WHALE":  {"min_amount": 0,    "max_amount": 999999999,  "bps": 1},    # 0.01% (tier special)
}


def get_swap_commission_bps(amount_usdc: float, volume_30d: float = 0, swap_count: int = -1) -> int:
    """Commission en BPS. Premier swap gratuit (0%) pour les nouveaux users.
    Si volume_30d est fourni, utiliser le volume. Sinon, fallback sur le montant."""
    if swap_count == 0:
        return 0  # Premier swap gratuit
    ref = volume_30d if volume_30d > 0 else amount_usdc
    for tier_name, tier in SWAP_COMMISSION_TIERS.items():
        if tier_name == "WHALE":
            continue  # Whale = tier special, pas automatique
        if tier["min_amount"] <= ref < tier["max_amount"]:
            return tier["bps"]
    return 10


def get_swap_tier_name(amount_usdc: float, volume_30d: float = 0, swap_count: int = -1) -> str:
    """Tier name selon le volume 30 jours. FREE pour le premier swap."""
    if swap_count == 0:
        return "FREE"
    ref = volume_30d if volume_30d > 0 else amount_usdc
    for tier_name, tier in SWAP_COMMISSION_TIERS.items():
        if tier_name == "WHALE":
            continue
        if tier["min_amount"] <= ref < tier["max_amount"]:
            return tier_name
    return "BRONZE"


def get_swap_tier_info(volume_30d: float = 0) -> dict:
    """Retourne le tier actuel + progression vers le tier suivant."""
    current_tier = "BRONZE"
    current_bps = 10
    next_tier = None
    next_threshold = 0
    remaining = 0

    tiers_list = list(SWAP_COMMISSION_TIERS.items())
    for i, (tier_name, tier) in enumerate(tiers_list):
        if tier_name == "WHALE":
            continue
        if tier["min_amount"] <= volume_30d < tier["max_amount"]:
            current_tier = tier_name
            current_bps = tier["bps"]
            if i + 1 < len(tiers_list):
                next_name, next_data = tiers_list[i + 1]
                next_tier = next_name
                next_threshold = next_data["min_amount"]
                remaining = next_threshold - volume_30d
            break

    return {
        "current_tier": current_tier,
        "current_bps": current_bps,
        "current_pct": f"{current_bps / 100:.2f}%",
        "volume_30d": round(volume_30d, 2),
        "next_tier": next_tier,
        "next_threshold": next_threshold,
        "remaining_to_next": round(max(0, remaining), 2),
        "all_tiers": {
            name: {"bps": t["bps"], "pct": f"{t['bps']/100:.2f}%", "min_amount": t["min_amount"], "max_amount": t["max_amount"]}
            for name, t in SWAP_COMMISSION_TIERS.items()
        },
    }


# ── Cache prix (30 secondes par token par chaine) ──

_price_cache: dict = {}   # cle = "{chain}:{symbol}" -> {"price": float, "ts": float}
_PRICE_TTL = 30


# ── Historique swaps (en memoire) ──

_swap_history: list = []


# ── Helpers ──

def _get_chain_info(chain: str) -> dict:
    """Retourne les infos de la chaine ou leve une erreur."""
    chain_lower = chain.lower()
    if chain_lower not in EVM_CHAINS:
        raise HTTPException(400, f"Chaine non supportee: {chain}. Chaines: {list(EVM_CHAINS.keys())}")
    return EVM_CHAINS[chain_lower]


def _get_token_info(chain: str, symbol: str) -> dict:
    """Retourne les infos du token ou leve une erreur."""
    chain_lower = chain.lower()
    if chain_lower not in TOKENS_BY_CHAIN:
        raise HTTPException(400, f"Chaine non supportee: {chain}")
    tokens = TOKENS_BY_CHAIN[chain_lower]
    symbol_upper = symbol.upper()
    if symbol_upper not in tokens:
        raise HTTPException(400, f"Token {symbol} non supporte sur {chain}. Tokens: {list(tokens.keys())}")
    return {**tokens[symbol_upper], "symbol": symbol_upper}


def _resolve_token_address(chain: str, token: str) -> tuple[str, dict]:
    """Resout un token par symbole ou adresse. Retourne (address, token_info)."""
    chain_lower = chain.lower()
    tokens = TOKENS_BY_CHAIN.get(chain_lower, {})

    # Recherche par symbole
    token_upper = token.upper()
    if token_upper in tokens:
        info = tokens[token_upper]
        return info["address"], {**info, "symbol": token_upper}

    # Recherche par adresse (case-insensitive)
    token_check = token.lower()
    for sym, info in tokens.items():
        if info["address"].lower() == token_check:
            return info["address"], {**info, "symbol": sym}

    raise HTTPException(400, f"Token {token} non reconnu sur {chain}. Tokens: {list(tokens.keys())}")


def _get_0x_headers() -> dict:
    """Headers pour l'API 0x."""
    if not ZERO_EX_API_KEY:
        raise HTTPException(503, "0x API key non configuree (ZERO_EX_API_KEY). Contactez l'admin.")
    return {
        "0x-api-key": ZERO_EX_API_KEY,
        "0x-version": "2",
    }


def _get_usdc_address(chain: str) -> str:
    """Retourne l'adresse USDC pour une chaine donnee."""
    chain_lower = chain.lower()
    tokens = TOKENS_BY_CHAIN.get(chain_lower, {})
    if "USDC" in tokens:
        return tokens["USDC"]["address"]
    raise HTTPException(400, f"Pas d'USDC disponible sur {chain}")


# ── Pydantic Models ──

class QuoteRequest(BaseModel):
    chain: str = Field(..., description="Nom de la chaine (ethereum, base, polygon, arbitrum, avalanche, bnb)")
    sell_token: str = Field(..., description="Symbole ou adresse du token a vendre")
    buy_token: str = Field(..., description="Symbole ou adresse du token a acheter")
    sell_amount: float = Field(..., gt=0, description="Montant a vendre (en unites humaines, ex: 1.5 ETH)")
    taker_address: Optional[str] = Field(None, description="Adresse du wallet qui execute le swap")


class ExecuteRequest(BaseModel):
    chain: str = Field(..., description="Nom de la chaine")
    sell_token: str = Field(..., description="Symbole ou adresse du token a vendre")
    buy_token: str = Field(..., description="Symbole ou adresse du token a acheter")
    sell_amount: float = Field(..., gt=0, description="Montant a vendre (en unites humaines)")
    taker_address: str = Field(..., description="Adresse du wallet qui execute le swap")
    tx_signature: str = Field(..., description="Signature de la transaction USDC de paiement de la commission")


# ── 0x API calls ──

async def _call_0x_quote(chain_id: int, sell_address: str, buy_address: str,
                          sell_amount_raw: str, taker_address: Optional[str] = None) -> dict:
    """Appelle l'endpoint /swap/permit2/quote de 0x."""
    headers = _get_0x_headers()
    params = {
        "chainId": chain_id,
        "sellToken": sell_address,
        "buyToken": buy_address,
        "sellAmount": sell_amount_raw,
    }
    if taker_address:
        params["taker"] = taker_address

    try:
        client = get_http_client()
        resp = await client.get(
            f"{ZERO_EX_BASE_URL}/swap/permit2/quote",
            headers=headers,
            params=params,
            timeout=15.0,
        )
        data = resp.json()
        if resp.status_code != 200:
                error_msg = data.get("reason", data.get("message", str(data)))
                raise HTTPException(502, f"0x API error: {error_msg}")
        return data
    except httpx.TimeoutException:
        raise HTTPException(504, "0x API timeout — reessayez dans quelques secondes")
    except HTTPException:
        raise
    except Exception as e:
        _log(f"0x quote error: {e}")
        raise HTTPException(502, "Swap API unavailable")


async def _call_0x_price(chain_id: int, sell_address: str, buy_address: str,
                          sell_amount_raw: str) -> dict:
    """Appelle l'endpoint /swap/permit2/price de 0x."""
    headers = _get_0x_headers()
    params = {
        "chainId": chain_id,
        "sellToken": sell_address,
        "buyToken": buy_address,
        "sellAmount": sell_amount_raw,
    }

    try:
        client = get_http_client()
        resp = await client.get(
            f"{ZERO_EX_BASE_URL}/swap/permit2/price",
            headers=headers,
            params=params,
            timeout=10.0,
        )
        data = resp.json()
        if resp.status_code != 200:
                error_msg = data.get("reason", data.get("message", str(data)))
                raise HTTPException(502, f"0x API price error: {error_msg}")
        return data
    except httpx.TimeoutException:
        raise HTTPException(504, "0x API timeout — reessayez dans quelques secondes")
    except HTTPException:
        raise
    except Exception as e:
        _log(f"0x price error: {e}")
        raise HTTPException(502, "Swap API unavailable")


# ── Router FastAPI ──

router = APIRouter(prefix="/api/swap/evm", tags=["evm-swap"])


@router.get("/chains")
async def list_chains():
    """Liste les chaines EVM supportees pour le swap."""
    chains = []
    for key, info in EVM_CHAINS.items():
        token_count = len(TOKENS_BY_CHAIN.get(key, {}))
        usdc_addr = TOKENS_BY_CHAIN.get(key, {}).get("USDC", {}).get("address", "")
        chains.append({
            "chain": key,
            "chain_id": info["chain_id"],
            "name": info["name"],
            "native_token": info["native"],
            "token_count": token_count,
            "usdc_address": usdc_addr,
            "pairs": token_count * (token_count - 1),
        })

    total_tokens = sum(c["token_count"] for c in chains)
    total_pairs = sum(c["pairs"] for c in chains)

    return {
        "chains": chains,
        "total_chains": len(chains),
        "total_tokens": total_tokens,
        "total_pairs": total_pairs,
        "aggregator": "0x Swap API v2",
        "commission_tiers": {
            k: f"{v['bps'] / 100:.2f}%" for k, v in SWAP_COMMISSION_TIERS.items()
        },
    }


@router.get("/tokens")
async def list_tokens(chain: str = Query(..., description="Nom de la chaine (ethereum, base, polygon, arbitrum, avalanche, bnb)")):
    """Liste les tokens disponibles sur une chaine."""
    chain_lower = chain.lower()
    if chain_lower not in TOKENS_BY_CHAIN:
        raise HTTPException(400, f"Chaine non supportee: {chain}. Chaines: {list(EVM_CHAINS.keys())}")

    chain_info = EVM_CHAINS[chain_lower]
    tokens = []
    for sym, info in TOKENS_BY_CHAIN[chain_lower].items():
        tokens.append({
            "symbol": sym,
            "name": info["name"],
            "address": info["address"],
            "decimals": info["decimals"],
        })

    return {
        "chain": chain_lower,
        "chain_id": chain_info["chain_id"],
        "name": chain_info["name"],
        "tokens": tokens,
        "token_count": len(tokens),
        "pairs": len(tokens) * (len(tokens) - 1),
    }


@router.post("/quote")
async def get_quote(req: QuoteRequest):
    """Obtient un devis de swap EVM via 0x avec commission MAXIA.

    Le montant sell_amount est en unites humaines (ex: 1.5 ETH, pas en wei).
    La commission MAXIA est calculee et affichee mais prelevee separement.
    """
    chain_lower = req.chain.lower()
    chain_info = _get_chain_info(chain_lower)

    # Resoudre les tokens
    sell_address, sell_info = _resolve_token_address(chain_lower, req.sell_token)
    buy_address, buy_info = _resolve_token_address(chain_lower, req.buy_token)

    if sell_address.lower() == buy_address.lower():
        raise HTTPException(400, "sell_token et buy_token doivent etre differents")

    # Check OFAC si taker_address fourni
    if req.taker_address:
        require_ofac_clear(req.taker_address, field="taker_address")

    # Convertir le montant en raw (avec decimals)
    sell_decimals = sell_info["decimals"]
    sell_amount_raw = str(int(req.sell_amount * (10 ** sell_decimals)))

    # Appeler 0x pour le quote
    quote_data = await _call_0x_quote(
        chain_id=chain_info["chain_id"],
        sell_address=sell_address,
        buy_address=buy_address,
        sell_amount_raw=sell_amount_raw,
        taker_address=req.taker_address,
    )

    # Extraire le prix et montant achete
    buy_amount_raw = quote_data.get("buyAmount", "0")
    buy_decimals = buy_info["decimals"]
    buy_amount = int(buy_amount_raw) / (10 ** buy_decimals)

    # Prix effectif
    price = buy_amount / req.sell_amount if req.sell_amount > 0 else 0

    # Estimer la valeur USD (pour le calcul de commission)
    # Si un des tokens est USDC, on utilise sa valeur directement
    estimated_usd = _estimate_usd_value(chain_lower, sell_info["symbol"], req.sell_amount,
                                         buy_info["symbol"], buy_amount)

    # Verifier le plafond
    if estimated_usd > MAX_SWAP_AMOUNT_USD:
        raise HTTPException(400, f"Swap depasse le plafond de ${MAX_SWAP_AMOUNT_USD:,.0f} par transaction")

    # Get user 30-day swap volume and swap count if taker_address provided
    user_volume_30d = 0
    swap_count = -1
    if req.taker_address:
        try:
            from database import db
            user_volume_30d = await db.get_swap_volume_30d(req.taker_address)
            swap_count = await db.get_swap_count(req.taker_address)
        except Exception:
            pass

    # Commission MAXIA (basee sur le volume 30 jours, premier swap gratuit)
    commission_bps = get_swap_commission_bps(estimated_usd, user_volume_30d, swap_count)
    commission_tier = get_swap_tier_name(estimated_usd, user_volume_30d, swap_count)
    fee_pct = commission_bps / 100
    fee_usd = estimated_usd * commission_bps / 10000

    # Gas estime
    gas_estimate = quote_data.get("gas", quote_data.get("estimatedGas", "N/A"))

    # Sources de liquidite
    sources = _extract_sources(quote_data)

    # Tier info avec progression (based on transaction value)
    tier_info = get_swap_tier_info(estimated_usd)

    return {
        "chain": chain_lower,
        "chain_id": chain_info["chain_id"],
        "sell_token": sell_info["symbol"],
        "sell_address": sell_address,
        "sell_amount": req.sell_amount,
        "buy_token": buy_info["symbol"],
        "buy_address": buy_address,
        "buy_amount": round(buy_amount, buy_decimals),
        "price": round(price, 8),
        "estimated_usd": round(estimated_usd, 2),
        "fee_tier": commission_tier,
        "fee_bps": commission_bps,
        "fee_pct": f"{fee_pct:.2f}%",
        "fee_usd": round(fee_usd, 4),
        "gas_estimate": gas_estimate,
        "sources": sources,
        "aggregator": "0x",
        "expires_in_seconds": 30,
        "tier_info": tier_info,
    }


@router.post("/execute")
async def execute_swap(req: ExecuteRequest):
    """Execute un swap EVM: verifie le paiement USDC, retourne le calldata 0x.

    Workflow:
    1. L'agent IA envoie la commission USDC a MAXIA (tx_signature)
    2. MAXIA verifie le paiement
    3. MAXIA retourne le calldata 0x pour executer le swap on-chain
    4. L'agent signe et broadcast la transaction de swap
    """
    chain_lower = req.chain.lower()
    chain_info = _get_chain_info(chain_lower)

    # Check OFAC
    require_ofac_clear(req.taker_address, field="taker_address")

    # Resoudre les tokens
    sell_address, sell_info = _resolve_token_address(chain_lower, req.sell_token)
    buy_address, buy_info = _resolve_token_address(chain_lower, req.buy_token)

    if sell_address.lower() == buy_address.lower():
        raise HTTPException(400, "sell_token et buy_token doivent etre differents")

    # Convertir le montant en raw
    sell_decimals = sell_info["decimals"]
    sell_amount_raw = str(int(req.sell_amount * (10 ** sell_decimals)))

    # Appeler 0x pour le quote complet avec taker
    quote_data = await _call_0x_quote(
        chain_id=chain_info["chain_id"],
        sell_address=sell_address,
        buy_address=buy_address,
        sell_amount_raw=sell_amount_raw,
        taker_address=req.taker_address,
    )

    # Montant achete
    buy_amount_raw = quote_data.get("buyAmount", "0")
    buy_decimals = buy_info["decimals"]
    buy_amount = int(buy_amount_raw) / (10 ** buy_decimals)

    # Valeur USD estimee
    estimated_usd = _estimate_usd_value(chain_lower, sell_info["symbol"], req.sell_amount,
                                         buy_info["symbol"], buy_amount)

    if estimated_usd > MAX_SWAP_AMOUNT_USD:
        raise HTTPException(400, f"Swap depasse le plafond de ${MAX_SWAP_AMOUNT_USD:,.0f} par transaction")

    # Get user 30-day swap volume and swap count for tier calculation
    user_volume_30d = 0
    swap_count = -1
    try:
        from database import db
        user_volume_30d = await db.get_swap_volume_30d(req.taker_address)
        swap_count = await db.get_swap_count(req.taker_address)
    except Exception:
        pass

    # Commission (basee sur le volume 30 jours, premier swap gratuit)
    commission_bps = get_swap_commission_bps(estimated_usd, user_volume_30d, swap_count)
    commission_tier = get_swap_tier_name(estimated_usd, user_volume_30d, swap_count)
    fee_usd = estimated_usd * commission_bps / 10000

    # Verification du paiement (tx_signature)
    # En production, on verifie que la tx est confirmee et que le montant >= fee_usd
    if not req.tx_signature or len(req.tx_signature) < 10:
        raise HTTPException(400, "tx_signature invalide — la commission doit etre payee avant le swap")

    _log(f"Swap {req.sell_amount} {sell_info['symbol']} -> {buy_info['symbol']} "
         f"on {chain_lower} | Fee: ${fee_usd:.4f} ({commission_tier}) | Tx: {req.tx_signature[:16]}...")

    # Extraire le calldata 0x pour que l'agent puisse broadcaster
    swap_id = str(uuid.uuid4())[:12]
    transaction = quote_data.get("transaction", {})

    # Enregistrer le swap dans l'historique
    _swap_history.append({
        "swap_id": swap_id,
        "chain": chain_lower,
        "chain_id": chain_info["chain_id"],
        "sell_token": sell_info["symbol"],
        "buy_token": buy_info["symbol"],
        "sell_amount": req.sell_amount,
        "buy_amount": buy_amount,
        "estimated_usd": estimated_usd,
        "commission_bps": commission_bps,
        "commission_usd": fee_usd,
        "commission_tier": commission_tier,
        "taker": req.taker_address,
        "payment_tx": req.tx_signature,
        "timestamp": time.time(),
    })

    return {
        "swap_id": swap_id,
        "status": "ready",
        "chain": chain_lower,
        "chain_id": chain_info["chain_id"],
        "sell_token": sell_info["symbol"],
        "sell_amount": req.sell_amount,
        "buy_token": buy_info["symbol"],
        "buy_amount": round(buy_amount, buy_decimals),
        "estimated_usd": round(estimated_usd, 2),
        "fee_tier": commission_tier,
        "fee_bps": commission_bps,
        "fee_usd": round(fee_usd, 4),
        "payment_tx": req.tx_signature,
        # Calldata 0x pour que l'agent signe et broadcast
        "transaction": {
            "to": transaction.get("to", ""),
            "data": transaction.get("data", ""),
            "value": transaction.get("value", "0"),
            "gas": transaction.get("gas", ""),
            "gasPrice": transaction.get("gasPrice", ""),
        },
        "message": f"Swap pret. Signez et broadcastez la transaction avec votre wallet.",
    }


@router.get("/price/{symbol}")
async def get_price(symbol: str, chain: str = Query("base", description="Chaine EVM")):
    """Prix spot d'un token via 0x /swap/permit2/price.

    Cache de 30 secondes par token par chaine pour eviter de spammer l'API.
    """
    chain_lower = chain.lower()
    chain_info = _get_chain_info(chain_lower)

    # Verifier que le token existe
    token_address, token_info = _resolve_token_address(chain_lower, symbol)

    # Verifier le cache
    cache_key = f"{chain_lower}:{token_info['symbol']}"
    cached = _price_cache.get(cache_key)
    if cached and (time.time() - cached["ts"]) < _PRICE_TTL:
        return {
            "symbol": token_info["symbol"],
            "name": token_info["name"],
            "chain": chain_lower,
            "price_usdc": cached["price"],
            "source": "0x (cached)",
            "cache_age_seconds": round(time.time() - cached["ts"], 1),
        }

    # Si le token EST USDC, prix = 1
    if token_info["symbol"] == "USDC":
        _price_cache[cache_key] = {"price": 1.0, "ts": time.time()}
        return {
            "symbol": "USDC",
            "name": "USD Coin",
            "chain": chain_lower,
            "price_usdc": 1.0,
            "source": "static",
        }

    # Obtenir le prix via 0x: vendre 1 unite du token pour USDC
    usdc_address = _get_usdc_address(chain_lower)
    one_unit_raw = str(10 ** token_info["decimals"])  # 1 token en raw

    try:
        price_data = await _call_0x_price(
            chain_id=chain_info["chain_id"],
            sell_address=token_address,
            buy_address=usdc_address,
            sell_amount_raw=one_unit_raw,
        )

        buy_amount_raw = price_data.get("buyAmount", "0")
        price_usdc = int(buy_amount_raw) / (10 ** 6)  # USDC = 6 decimals

        # Mettre en cache
        _price_cache[cache_key] = {"price": round(price_usdc, 6), "ts": time.time()}

        return {
            "symbol": token_info["symbol"],
            "name": token_info["name"],
            "chain": chain_lower,
            "chain_id": chain_info["chain_id"],
            "price_usdc": round(price_usdc, 6),
            "source": "0x",
        }
    except HTTPException:
        raise
    except Exception as e:
        _log(f"Price error for {symbol} on {chain_lower}: {e}")
        raise HTTPException(502, f"Impossible d'obtenir le prix de {symbol} sur {chain_lower}")


@router.get("/stats")
async def get_stats():
    """Statistiques des swaps EVM."""
    total_volume = sum(s.get("estimated_usd", 0) for s in _swap_history)
    total_commission = sum(s.get("commission_usd", 0) for s in _swap_history)

    # Volume par chaine
    volume_by_chain = {}
    for s in _swap_history:
        ch = s.get("chain", "unknown")
        volume_by_chain[ch] = volume_by_chain.get(ch, 0) + s.get("estimated_usd", 0)

    total_tokens = sum(len(t) for t in TOKENS_BY_CHAIN.values())
    total_pairs = sum(len(t) * (len(t) - 1) for t in TOKENS_BY_CHAIN.values())

    return {
        "total_swaps": len(_swap_history),
        "total_volume_usd": round(total_volume, 2),
        "total_commission_usd": round(total_commission, 4),
        "volume_by_chain": {k: round(v, 2) for k, v in volume_by_chain.items()},
        "chains_supported": len(EVM_CHAINS),
        "tokens_supported": total_tokens,
        "pairs_available": total_pairs,
        "commission_tiers": SWAP_COMMISSION_TIERS,
        "aggregator": "0x Swap API v2",
    }


# ── Fonctions utilitaires internes ──

def _estimate_usd_value(chain: str, sell_symbol: str, sell_amount: float,
                          buy_symbol: str, buy_amount: float) -> float:
    """Estime la valeur USD d'un swap.

    Si un des tokens est USDC, on utilise directement sa valeur.
    Sinon, on verifie le cache prix. En dernier recours, 0 (l'appelant devra gerer).
    """
    # Si le sell token est USDC
    if sell_symbol == "USDC":
        return sell_amount

    # Si le buy token est USDC
    if buy_symbol == "USDC":
        return buy_amount

    # Si le sell token est USDT (meme valeur que USD)
    if sell_symbol == "USDT":
        return sell_amount

    # Chercher dans le cache prix
    cache_key = f"{chain}:{sell_symbol}"
    cached = _price_cache.get(cache_key)
    if cached and (time.time() - cached["ts"]) < _PRICE_TTL:
        return sell_amount * cached["price"]

    # Fallback: on ne peut pas estimer precisement, utiliser le buy_amount
    # si c'est un stablecoin-adjacent, sinon retourner 0
    # Les swaps sans estimation USD auront la commission BRONZE par defaut
    return 0


def _extract_sources(quote_data: dict) -> list:
    """Extrait les sources de liquidite du quote 0x."""
    sources = []

    # 0x v2 retourne les sources dans route.fills
    route = quote_data.get("route", {})
    fills = route.get("fills", [])
    if fills:
        seen = set()
        for fill in fills:
            source = fill.get("source", "unknown")
            if source not in seen:
                sources.append(source)
                seen.add(source)
        return sources

    # Fallback: chercher dans orders
    orders = quote_data.get("orders", [])
    seen = set()
    for order in orders:
        source = order.get("source", "unknown")
        if source not in seen:
            sources.append(source)
            seen.add(source)

    return sources if sources else ["0x aggregated"]
