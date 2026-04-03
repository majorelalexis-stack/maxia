"""MAXIA Protocol Catalog — Annuaire de 50+ protocoles DeFi/Web3 sur 14 chains.

Repertorie les protocoles DeFi, DEX, NFT, Staking, Bridges, Yield, Derivatives,
Launchpads et Governance accessibles depuis MAXIA. Chaque entree inclut chain,
type, URL, description et source TVL (DefiLlama pool ID si disponible).

Endpoints:
  GET  /api/goat/protocols            — liste filtrable (chain, type)
  GET  /api/goat/protocols/{id}       — detail d'un protocole
  GET  /api/goat/categories           — types avec compteurs
  GET  /api/goat/chains               — chains avec compteurs
  POST /api/goat/execute              — guidance d'execution (URL + endpoints MAXIA natifs)

Alias: /api/protocols/* miroir de /api/goat/*
"""
import logging
from fastapi import APIRouter, HTTPException, Header
from typing import Optional

log = logging.getLogger("protocol_catalog")

# ── Routers ──
# Conserve /api/goat pour backward compatibility, ajoute alias /api/protocols
router = APIRouter(prefix="/api/goat", tags=["protocol-catalog"])
router_alias = APIRouter(prefix="/api/protocols", tags=["protocol-catalog"])

# ── Supported chains (14 chains MAXIA) ──
SUPPORTED_CHAINS = (
    "solana", "ethereum", "base", "polygon", "arbitrum",
    "avalanche", "bnb", "ton", "sui", "tron",
    "near", "aptos", "sei", "xrp",
)

# ── Protocol types ──
PROTOCOL_TYPES = (
    "dex", "lending", "staking", "bridge", "yield",
    "nft", "derivatives", "launchpad", "governance",
)

# ── Protocol catalog — 55 protocoles ──
# Chaque entree: name, chain (csv), type, url, description, tvl_source (opt), native (opt)
PROTOCOLS: dict[str, dict] = {
    # ═══════════════════════════════════════════
    # DEX (16)
    # ═══════════════════════════════════════════
    "jupiter": {
        "name": "Jupiter",
        "chain": "solana",
        "type": "dex",
        "url": "https://jup.ag",
        "description": "Solana DEX aggregator. Best swap routes across all Solana DEXs.",
        "tvl_source": "jupiter",
        "native": True,
    },
    "orca": {
        "name": "Orca",
        "chain": "solana",
        "type": "dex",
        "url": "https://www.orca.so",
        "description": "Concentrated liquidity AMM on Solana (Whirlpools).",
        "tvl_source": "orca",
    },
    "raydium": {
        "name": "Raydium",
        "chain": "solana",
        "type": "dex",
        "url": "https://raydium.io",
        "description": "AMM + CLMM + order book hybrid on Solana.",
        "tvl_source": "raydium",
    },
    "uniswap": {
        "name": "Uniswap",
        "chain": "ethereum,base,polygon,arbitrum",
        "type": "dex",
        "url": "https://app.uniswap.org",
        "description": "Largest EVM DEX. V3/V4 concentrated liquidity.",
        "tvl_source": "uniswap",
    },
    "sushiswap": {
        "name": "SushiSwap",
        "chain": "ethereum,polygon,arbitrum,avalanche,bnb",
        "type": "dex",
        "url": "https://www.sushi.com",
        "description": "Multi-chain DEX with yield farming and Trident AMM.",
        "tvl_source": "sushi",
    },
    "pancakeswap": {
        "name": "PancakeSwap",
        "chain": "bnb,ethereum,base",
        "type": "dex",
        "url": "https://pancakeswap.finance",
        "description": "Leading BNB Chain DEX. V3 concentrated liquidity.",
        "tvl_source": "pancakeswap",
    },
    "curve": {
        "name": "Curve Finance",
        "chain": "ethereum,polygon,arbitrum,avalanche,base",
        "type": "dex",
        "url": "https://curve.fi",
        "description": "Stablecoin-optimized AMM. Low slippage swaps.",
        "tvl_source": "curve-dex",
    },
    "trader_joe": {
        "name": "Trader Joe",
        "chain": "avalanche,arbitrum,bnb",
        "type": "dex",
        "url": "https://traderjoexyz.com",
        "description": "Liquidity Book AMM. Leading Avalanche DEX.",
        "tvl_source": "trader-joe",
    },
    "cetus": {
        "name": "Cetus",
        "chain": "sui,aptos",
        "type": "dex",
        "url": "https://www.cetus.zone",
        "description": "Concentrated liquidity DEX on Sui and Aptos.",
        "tvl_source": "cetus-amm",
    },
    "turbos": {
        "name": "Turbos Finance",
        "chain": "sui",
        "type": "dex",
        "url": "https://www.turbos.finance",
        "description": "Concentrated liquidity DEX on Sui with limit orders.",
        "tvl_source": "turbos-finance",
    },
    "stonfi": {
        "name": "STON.fi",
        "chain": "ton",
        "type": "dex",
        "url": "https://ston.fi",
        "description": "Leading DEX on TON blockchain. AMM with low fees.",
        "tvl_source": "ston.fi",
    },
    "dedust": {
        "name": "DeDust",
        "chain": "ton",
        "type": "dex",
        "url": "https://dedust.io",
        "description": "Scalable DEX on TON with stableswap pools.",
        "tvl_source": "dedust",
    },
    "ref_finance": {
        "name": "Ref Finance",
        "chain": "near",
        "type": "dex",
        "url": "https://www.ref.finance",
        "description": "Multi-purpose DEX on NEAR. AMM + order book.",
        "tvl_source": "ref-finance",
    },
    "sunswap": {
        "name": "SunSwap",
        "chain": "tron",
        "type": "dex",
        "url": "https://sun.io",
        "description": "Leading DEX on TRON. V3 concentrated liquidity.",
        "tvl_source": "sunswap",
    },
    "astroport": {
        "name": "Astroport",
        "chain": "sei",
        "type": "dex",
        "url": "https://astroport.fi",
        "description": "Multi-pool DEX on Sei. Constant product + stableswap.",
        "tvl_source": "astroport",
    },
    "dragonswap": {
        "name": "DragonSwap",
        "chain": "sei",
        "type": "dex",
        "url": "https://dragonswap.app",
        "description": "AMM DEX on Sei with farming rewards.",
        "tvl_source": "dragonswap",
    },
    # ═══════════════════════════════════════════
    # LENDING (8)
    # ═══════════════════════════════════════════
    "aave": {
        "name": "Aave",
        "chain": "ethereum,polygon,arbitrum,avalanche,base",
        "type": "lending",
        "url": "https://app.aave.com",
        "description": "Decentralized lending/borrowing. Flash loans. V3 multi-chain.",
        "tvl_source": "aave",
    },
    "compound": {
        "name": "Compound",
        "chain": "ethereum,base,polygon,arbitrum",
        "type": "lending",
        "url": "https://compound.finance",
        "description": "Algorithmic money market. Compound III (Comet).",
        "tvl_source": "compound-finance",
    },
    "kamino": {
        "name": "Kamino",
        "chain": "solana",
        "type": "lending",
        "url": "https://app.kamino.finance",
        "description": "Automated liquidity + lending on Solana. K-Lend.",
        "tvl_source": "kamino-lending",
    },
    "marginfi": {
        "name": "marginfi",
        "chain": "solana",
        "type": "lending",
        "url": "https://www.marginfi.com",
        "description": "Decentralized lending on Solana. Isolated risk pools.",
        "tvl_source": "marginfi",
    },
    "morpho": {
        "name": "Morpho",
        "chain": "ethereum,base",
        "type": "lending",
        "url": "https://app.morpho.org",
        "description": "Peer-to-peer lending optimizer on top of Aave/Compound.",
        "tvl_source": "morpho",
    },
    "scallop": {
        "name": "Scallop",
        "chain": "sui",
        "type": "lending",
        "url": "https://www.scallop.io",
        "description": "Leading lending protocol on Sui. Dynamic interest rates.",
        "tvl_source": "scallop-lend",
    },
    "navi": {
        "name": "NAVI Protocol",
        "chain": "sui",
        "type": "lending",
        "url": "https://www.naviprotocol.io",
        "description": "Lending and borrowing on Sui with auto-compounding.",
        "tvl_source": "navi-lending",
    },
    "evaa": {
        "name": "EVAA",
        "chain": "ton",
        "type": "lending",
        "url": "https://evaa.finance",
        "description": "Lending protocol on TON. Supply and borrow assets.",
        "tvl_source": "evaa",
    },
    # ═══════════════════════════════════════════
    # STAKING (7)
    # ═══════════════════════════════════════════
    "marinade": {
        "name": "Marinade",
        "chain": "solana",
        "type": "staking",
        "url": "https://marinade.finance",
        "description": "Liquid staking for SOL (mSOL). Native + liquid modes.",
        "tvl_source": "marinade-finance",
    },
    "jito": {
        "name": "Jito",
        "chain": "solana",
        "type": "staking",
        "url": "https://www.jito.network",
        "description": "MEV-powered liquid staking (jitoSOL). Highest SOL yield.",
        "tvl_source": "jito",
    },
    "lido": {
        "name": "Lido",
        "chain": "ethereum,polygon",
        "type": "staking",
        "url": "https://lido.fi",
        "description": "Largest liquid staking protocol (stETH). 30%+ ETH staked.",
        "tvl_source": "lido",
    },
    "rocketpool": {
        "name": "Rocket Pool",
        "chain": "ethereum",
        "type": "staking",
        "url": "https://rocketpool.net",
        "description": "Decentralized ETH staking (rETH). Permissionless nodes.",
        "tvl_source": "rocket-pool",
    },
    "eigenlayer": {
        "name": "EigenLayer",
        "chain": "ethereum",
        "type": "staking",
        "url": "https://www.eigenlayer.xyz",
        "description": "Restaking protocol. Stake ETH to secure multiple services.",
        "tvl_source": "eigenlayer",
    },
    "stakestone": {
        "name": "StakeStone",
        "chain": "ethereum,bnb,sei",
        "type": "staking",
        "url": "https://stakestone.io",
        "description": "Omnichain liquid staking. STONE yield-bearing ETH.",
        "tvl_source": "stakestone",
    },
    "tonstakers": {
        "name": "Tonstakers",
        "chain": "ton",
        "type": "staking",
        "url": "https://tonstakers.com",
        "description": "Liquid staking for TON (tsTON). Largest TON staking.",
        "tvl_source": "tonstakers",
    },
    # ═══════════════════════════════════════════
    # BRIDGE (5)
    # ═══════════════════════════════════════════
    "wormhole": {
        "name": "Wormhole",
        "chain": "solana,ethereum,base,polygon,arbitrum,avalanche,bnb,sui,aptos,near,sei",
        "type": "bridge",
        "url": "https://wormhole.com",
        "description": "Cross-chain messaging + token bridge. 35+ chains.",
        "tvl_source": "wormhole",
    },
    "allbridge": {
        "name": "Allbridge",
        "chain": "solana,ethereum,base,polygon,arbitrum,avalanche,bnb,tron",
        "type": "bridge",
        "url": "https://allbridge.io",
        "description": "Stablecoin-focused cross-chain bridge. CCTP integration.",
        "tvl_source": "allbridge",
    },
    "layerzero": {
        "name": "LayerZero",
        "chain": "ethereum,base,polygon,arbitrum,avalanche,bnb,aptos,sei",
        "type": "bridge",
        "url": "https://layerzero.network",
        "description": "Omnichain interoperability protocol. OFT token standard.",
        "tvl_source": "layerzero",
    },
    "debridge": {
        "name": "deBridge",
        "chain": "solana,ethereum,base,polygon,arbitrum,avalanche,bnb",
        "type": "bridge",
        "url": "https://debridge.finance",
        "description": "High-performance cross-chain bridge. DLN for limit orders.",
        "tvl_source": "debridge",
    },
    "orbiter": {
        "name": "Orbiter Finance",
        "chain": "ethereum,base,polygon,arbitrum",
        "type": "bridge",
        "url": "https://www.orbiter.finance",
        "description": "Fast L2 bridge. Optimized for Ethereum rollups.",
        "tvl_source": "orbiter-finance",
    },
    # ═══════════════════════════════════════════
    # YIELD (5)
    # ═══════════════════════════════════════════
    "yearn": {
        "name": "Yearn Finance",
        "chain": "ethereum,polygon,arbitrum",
        "type": "yield",
        "url": "https://yearn.fi",
        "description": "Automated yield optimization vaults. V3 multi-strategy.",
        "tvl_source": "yearn-finance",
    },
    "beefy": {
        "name": "Beefy Finance",
        "chain": "ethereum,base,polygon,arbitrum,avalanche,bnb",
        "type": "yield",
        "url": "https://beefy.com",
        "description": "Multi-chain yield optimizer. 1000+ auto-compounding vaults.",
        "tvl_source": "beefy",
    },
    "pendle": {
        "name": "Pendle",
        "chain": "ethereum,arbitrum,bnb",
        "type": "yield",
        "url": "https://www.pendle.finance",
        "description": "Yield tokenization. Trade future yield. Fixed/variable rates.",
        "tvl_source": "pendle",
    },
    "meteora": {
        "name": "Meteora",
        "chain": "solana",
        "type": "yield",
        "url": "https://www.meteora.ag",
        "description": "Dynamic liquidity on Solana. DLMM + dynamic vaults.",
        "tvl_source": "meteora",
    },
    "convex": {
        "name": "Convex Finance",
        "chain": "ethereum",
        "type": "yield",
        "url": "https://www.convexfinance.com",
        "description": "Boost Curve rewards without locking CRV. Auto-compounding.",
        "tvl_source": "convex-finance",
    },
    # ═══════════════════════════════════════════
    # NFT (4)
    # ═══════════════════════════════════════════
    "opensea": {
        "name": "OpenSea",
        "chain": "ethereum,polygon,base,solana,arbitrum,avalanche,bnb",
        "type": "nft",
        "url": "https://opensea.io",
        "description": "Largest NFT marketplace. Buy, sell, and discover NFTs.",
    },
    "magiceden": {
        "name": "Magic Eden",
        "chain": "solana,ethereum,polygon,base",
        "type": "nft",
        "url": "https://magiceden.io",
        "description": "Multi-chain NFT marketplace. Solana-native, now cross-chain.",
    },
    "tensor": {
        "name": "Tensor",
        "chain": "solana",
        "type": "nft",
        "url": "https://www.tensor.trade",
        "description": "Pro NFT trading on Solana with AMM pools and analytics.",
    },
    "blur": {
        "name": "Blur",
        "chain": "ethereum",
        "type": "nft",
        "url": "https://blur.io",
        "description": "Pro NFT marketplace on Ethereum. Bid pools + lending via Blend.",
    },
    # ═══════════════════════════════════════════
    # DERIVATIVES (4)
    # ═══════════════════════════════════════════
    "drift": {
        "name": "Drift Protocol",
        "chain": "solana",
        "type": "derivatives",
        "url": "https://www.drift.trade",
        "description": "Perpetual futures on Solana. Up to 20x leverage. vAMM.",
        "tvl_source": "drift",
    },
    "gmx": {
        "name": "GMX",
        "chain": "arbitrum,avalanche",
        "type": "derivatives",
        "url": "https://gmx.io",
        "description": "Decentralized perpetual exchange. Up to 100x leverage.",
        "tvl_source": "gmx",
    },
    "hyperliquid": {
        "name": "Hyperliquid",
        "chain": "arbitrum",
        "type": "derivatives",
        "url": "https://app.hyperliquid.xyz",
        "description": "Perpetual futures L1. On-chain order book. Sub-second.",
        "tvl_source": "hyperliquid",
    },
    "synthetix": {
        "name": "Synthetix",
        "chain": "ethereum,base",
        "type": "derivatives",
        "url": "https://synthetix.io",
        "description": "Synthetic assets protocol. Perps, options, spot synths.",
        "tvl_source": "synthetix",
    },
    # ═══════════════════════════════════════════
    # LAUNCHPAD (3)
    # ═══════════════════════════════════════════
    "pump_fun": {
        "name": "pump.fun",
        "chain": "solana",
        "type": "launchpad",
        "url": "https://pump.fun",
        "description": "Memecoin launchpad on Solana. Bonding curve token creation.",
    },
    "pinksale": {
        "name": "PinkSale",
        "chain": "bnb,ethereum,polygon,arbitrum,avalanche,base",
        "type": "launchpad",
        "url": "https://www.pinksale.finance",
        "description": "Multi-chain launchpad. Fair launch + presale + token lock.",
    },
    "movepump": {
        "name": "MovePump",
        "chain": "sui",
        "type": "launchpad",
        "url": "https://movepump.com",
        "description": "Token launchpad on Sui. Bonding curve fair launch.",
    },
    # ═══════════════════════════════════════════
    # XRP / XRPL (1)
    # ═══════════════════════════════════════════
    "xrpl_dex": {
        "name": "XRPL DEX",
        "chain": "xrp",
        "type": "dex",
        "url": "https://xrpl.org",
        "description": "Native on-ledger DEX on XRP Ledger. Order book built into the protocol.",
    },
    # ═══════════════════════════════════════════
    # GOVERNANCE (3)
    # ═══════════════════════════════════════════
    "realms": {
        "name": "Realms",
        "chain": "solana",
        "type": "governance",
        "url": "https://realms.today",
        "description": "DAO governance on Solana. SPL Governance framework.",
    },
    "snapshot": {
        "name": "Snapshot",
        "chain": "ethereum,polygon,arbitrum,base,bnb,avalanche",
        "type": "governance",
        "url": "https://snapshot.org",
        "description": "Off-chain gasless voting. Used by 30K+ DAOs.",
    },
    "tally": {
        "name": "Tally",
        "chain": "ethereum,polygon,arbitrum,base,avalanche",
        "type": "governance",
        "url": "https://www.tally.xyz",
        "description": "On-chain governance dashboard. Governor contracts.",
    },
}


# ── Native MAXIA endpoint mapping ──
_NATIVE_ENDPOINTS: dict[str, dict[str, str]] = {
    "jupiter": {
        "quote": "GET /api/public/crypto/quote",
        "swap": "POST /api/public/crypto/swap",
        "prices": "GET /api/public/crypto/prices",
        "candles": "GET /api/public/crypto/candles",
    },
    "wormhole": {
        "routes": "GET /api/bridge/routes",
        "quote": "POST /api/bridge/quote",
        "confirm": "POST /api/bridge/confirm",
        "stats": "GET /api/bridge/stats",
    },
    "allbridge": {
        "routes": "GET /api/bridge/routes",
        "quote": "POST /api/bridge/quote",
        "confirm": "POST /api/bridge/confirm",
    },
}

# Protocoles couverts par les endpoints generiques MAXIA (swap, yield, staking)
_GENERIC_ENDPOINTS: dict[str, dict[str, str]] = {
    "dex": {
        "swap": "POST /api/public/crypto/swap",
        "quote": "GET /api/public/crypto/quote",
        "prices": "GET /api/public/crypto/prices",
    },
    "yield": {
        "best_yield": "GET /api/public/defi/best-yield",
    },
    "bridge": {
        "routes": "GET /api/bridge/routes",
        "quote": "POST /api/bridge/quote",
    },
    "staking": {
        "stats": "GET /api/staking/stats",
        "stake": "POST /api/staking/stake",
    },
}


def _get_native_endpoints(protocol_id: str, protocol_type: str) -> dict[str, str]:
    """Return MAXIA native endpoints for a protocol (direct mapping or generic by type)."""
    direct = _NATIVE_ENDPOINTS.get(protocol_id)
    if direct:
        return direct
    return _GENERIC_ENDPOINTS.get(protocol_type, {})


def _build_protocol_entry(pid: str, info: dict) -> dict:
    """Build a full protocol response dict from catalog entry."""
    return {"id": pid, **info}


def _filter_protocols(
    chain: Optional[str] = None,
    protocol_type: Optional[str] = None,
) -> list[dict]:
    """Filter catalog by chain and/or type. Returns list of protocol dicts."""
    results: list[dict] = []
    chain_lower = chain.lower().strip() if chain else None
    type_lower = protocol_type.lower().strip() if protocol_type else None

    for pid, info in PROTOCOLS.items():
        if chain_lower and chain_lower not in info["chain"].lower():
            continue
        if type_lower and type_lower != info["type"].lower():
            continue
        results.append(_build_protocol_entry(pid, info))

    return results


def _count_by_field(field: str) -> dict[str, int]:
    """Count protocols grouped by a field (splitting csv chain values)."""
    counts: dict[str, int] = {}
    for info in PROTOCOLS.values():
        values = [v.strip() for v in info[field].split(",")]
        for val in values:
            counts[val] = counts.get(val, 0) + 1
    return dict(sorted(counts.items()))


# ══════════════════════════════════════════════
# Endpoints
# ══════════════════════════════════════════════

@router.get("/protocols")
@router_alias.get("/list")
async def list_protocols(
    chain: Optional[str] = None,
    type: Optional[str] = None,
) -> dict:
    """List all protocols in the catalog with optional chain/type filters."""
    results = _filter_protocols(chain=chain, protocol_type=type)
    return {
        "protocols": results,
        "total": len(results),
        "catalog_size": len(PROTOCOLS),
        "categories": sorted({p["type"] for p in PROTOCOLS.values()}),
        "chains": sorted({
            c.strip()
            for p in PROTOCOLS.values()
            for c in p["chain"].split(",")
        }),
    }


@router.get("/categories")
@router_alias.get("/categories")
async def list_categories() -> dict:
    """List all protocol types with counts."""
    counts = _count_by_field("type")
    return {
        "categories": [
            {"type": t, "count": c} for t, c in counts.items()
        ],
        "total_types": len(counts),
        "total_protocols": len(PROTOCOLS),
    }


@router.get("/chains")
@router_alias.get("/chains")
async def list_chains() -> dict:
    """List all chains with protocol counts."""
    counts = _count_by_field("chain")
    return {
        "chains": [
            {"chain": ch, "count": c} for ch, c in counts.items()
        ],
        "total_chains": len(counts),
        "total_protocols": len(PROTOCOLS),
    }


@router.get("/protocols/{protocol_id}")
@router_alias.get("/{protocol_id}")
async def get_protocol(protocol_id: str) -> dict:
    """Get details about a specific protocol."""
    info = PROTOCOLS.get(protocol_id)
    if not info:
        raise HTTPException(
            404,
            f"Protocol not found: {protocol_id}. "
            "Use GET /api/goat/protocols for the full list.",
        )
    entry = _build_protocol_entry(protocol_id, info)
    entry["maxia_endpoints"] = _get_native_endpoints(protocol_id, info["type"])
    return entry


@router.post("/execute")
@router_alias.post("/execute")
async def execute_protocol_action(
    request: dict,
    x_api_key: str = Header(alias="X-API-Key"),
) -> dict:
    """Lookup a protocol and return execution guidance with URLs + MAXIA endpoints."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")

    protocol_id = request.get("protocol", "").strip()
    action = request.get("action", "").strip()

    if not protocol_id:
        raise HTTPException(400, "Missing 'protocol' field in request body")

    if protocol_id not in PROTOCOLS:
        raise HTTPException(400, f"Unknown protocol: {protocol_id}")

    info = PROTOCOLS[protocol_id]
    native_endpoints = _get_native_endpoints(protocol_id, info["type"])

    # Protocoles avec integration native MAXIA — rediriger vers endpoints internes
    if info.get("native"):
        return {
            "protocol": protocol_id,
            "action": action,
            "status": "native",
            "message": f"{info['name']} is natively integrated. Use MAXIA endpoints directly.",
            "url": info["url"],
            "maxia_endpoints": native_endpoints,
        }

    # Tous les autres — retourner URL du protocole + endpoints MAXIA generiques
    return {
        "protocol": protocol_id,
        "action": action,
        "status": "external",
        "url": info["url"],
        "description": info["description"],
        "maxia_endpoints": native_endpoints,
        "note": (
            f"Execute via {info['name']} at {info['url']}. "
            "MAXIA endpoints listed above cover generic operations for this protocol type."
            if native_endpoints
            else f"Execute directly via {info['name']} at {info['url']}."
        ),
    }


log.info(
    "[ProtocolCatalog] Mounted — %d protocols, %d types, %d chains",
    len(PROTOCOLS),
    len({p["type"] for p in PROTOCOLS.values()}),
    len({c.strip() for p in PROTOCOLS.values() for c in p["chain"].split(",")}),
)
