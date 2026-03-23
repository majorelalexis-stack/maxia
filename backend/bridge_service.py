"""MAXIA Bridge Cross-Chain — Transferts de tokens entre 14 blockchains."""
import os, time, uuid, logging, random
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

logger = logging.getLogger("maxia.bridge_service")

router = APIRouter(prefix="/api/bridge", tags=["bridge"])

# ── Chains supportees ──
SUPPORTED_CHAINS = [
    "solana", "base", "ethereum", "xrp", "polygon", "arbitrum",
    "avalanche", "bnb", "ton", "sui", "tron", "near", "aptos", "sei",
]

EVM_CHAINS = {"base", "polygon", "arbitrum", "avalanche", "bnb", "sei", "ethereum"}

# ── Protocoles de bridge et leurs routes ──
BRIDGE_PROTOCOLS = {
    "wormhole": {
        "name": "Wormhole",
        "fee_range_usd": (0.20, 0.50),
        "time_range_min": (15, 20),
        "routes": [
            ("solana", "ethereum"), ("ethereum", "solana"),
            ("solana", "base"), ("base", "solana"),
            ("solana", "polygon"), ("polygon", "solana"),
            ("solana", "avalanche"), ("avalanche", "solana"),
            ("solana", "bnb"), ("bnb", "solana"),
            ("solana", "arbitrum"), ("arbitrum", "solana"),
            ("solana", "sui"), ("sui", "solana"),
            ("solana", "aptos"), ("aptos", "solana"),
            ("solana", "near"), ("near", "solana"),
        ],
    },
    "layerzero": {
        "name": "LayerZero/Stargate",
        "fee_range_usd": (0.10, 0.30),
        "time_range_min": (5, 10),
        "routes": [
            # EVM <-> EVM routes
            ("base", "polygon"), ("polygon", "base"),
            ("base", "arbitrum"), ("arbitrum", "base"),
            ("base", "avalanche"), ("avalanche", "base"),
            ("base", "bnb"), ("bnb", "base"),
            ("base", "sei"), ("sei", "base"),
            ("base", "ethereum"), ("ethereum", "base"),
            ("polygon", "arbitrum"), ("arbitrum", "polygon"),
            ("polygon", "avalanche"), ("avalanche", "polygon"),
            ("polygon", "bnb"), ("bnb", "polygon"),
            ("polygon", "sei"), ("sei", "polygon"),
            ("polygon", "ethereum"), ("ethereum", "polygon"),
            ("arbitrum", "avalanche"), ("avalanche", "arbitrum"),
            ("arbitrum", "bnb"), ("bnb", "arbitrum"),
            ("arbitrum", "sei"), ("sei", "arbitrum"),
            ("arbitrum", "ethereum"), ("ethereum", "arbitrum"),
            ("avalanche", "bnb"), ("bnb", "avalanche"),
            ("avalanche", "sei"), ("sei", "avalanche"),
            ("avalanche", "ethereum"), ("ethereum", "avalanche"),
            ("bnb", "sei"), ("sei", "bnb"),
            ("bnb", "ethereum"), ("ethereum", "bnb"),
            ("sei", "ethereum"), ("ethereum", "sei"),
        ],
    },
    "portal": {
        "name": "Portal (Wormhole Token Bridge)",
        "fee_range_usd": (0.15, 0.40),
        "time_range_min": (10, 15),
        "routes": [
            ("solana", "ethereum"), ("ethereum", "solana"),
            ("solana", "base"), ("base", "solana"),
            ("solana", "polygon"), ("polygon", "solana"),
            ("solana", "arbitrum"), ("arbitrum", "solana"),
            ("solana", "avalanche"), ("avalanche", "solana"),
            ("solana", "bnb"), ("bnb", "solana"),
            ("solana", "sui"), ("sui", "solana"),
            ("solana", "aptos"), ("aptos", "solana"),
            ("solana", "near"), ("near", "solana"),
            ("solana", "ton"), ("ton", "solana"),
            ("solana", "tron"), ("tron", "solana"),
            ("solana", "xrp"), ("xrp", "solana"),
            ("solana", "sei"), ("sei", "solana"),
            # EVM <-> non-EVM via Portal
            ("ethereum", "ton"), ("ton", "ethereum"),
            ("ethereum", "tron"), ("tron", "ethereum"),
            ("ethereum", "xrp"), ("xrp", "ethereum"),
            ("ethereum", "sui"), ("sui", "ethereum"),
            ("ethereum", "aptos"), ("aptos", "ethereum"),
            ("ethereum", "near"), ("near", "ethereum"),
            ("base", "ton"), ("ton", "base"),
            ("base", "tron"), ("tron", "base"),
            ("base", "xrp"), ("xrp", "base"),
        ],
    },
}

# Index rapide: (from, to) -> meilleur protocole
_route_index: dict[tuple[str, str], str] = {}
# Priorite: layerzero (EVM<->EVM rapide) > wormhole > portal
for proto_key in ["layerzero", "wormhole", "portal"]:
    proto = BRIDGE_PROTOCOLS[proto_key]
    for src, dst in proto["routes"]:
        pair = (src, dst)
        if pair not in _route_index:
            _route_index[pair] = proto_key

# ── Tokens supportes par chain ──
CHAIN_TOKENS = {
    "solana":    ["USDC", "SOL", "USDT"],
    "base":      ["USDC", "ETH", "USDT"],
    "ethereum":  ["USDC", "ETH", "USDT"],
    "xrp":       ["USDC", "XRP"],
    "polygon":   ["USDC", "MATIC", "USDT"],
    "arbitrum":  ["USDC", "ETH", "USDT"],
    "avalanche": ["USDC", "AVAX", "USDT"],
    "bnb":       ["USDC", "BNB", "USDT"],
    "ton":       ["USDC", "TON", "USDT"],
    "sui":       ["USDC", "SUI"],
    "tron":      ["USDC", "TRX", "USDT"],
    "near":      ["USDC", "NEAR"],
    "aptos":     ["USDC", "APT"],
    "sei":       ["USDC", "SEI"],
}

# ── Stockage en memoire des bridges ──
_pending_bridges: dict[str, dict] = {}
_completed_bridges: list[dict] = []


# ── Pydantic models ──

class BridgeInitiateRequest(BaseModel):
    """Requete pour initier un bridge cross-chain."""
    from_chain: str = Field(..., description="Blockchain source")
    to_chain: str = Field(..., description="Blockchain destination")
    token: str = Field(default="USDC", description="Token a bridger")
    amount: float = Field(..., gt=0, description="Montant a bridger")
    sender_wallet: str = Field(..., description="Adresse du wallet source")
    recipient_wallet: str = Field(..., description="Adresse du wallet destination")
    preferred_protocol: Optional[str] = Field(default=None, description="Protocole prefere (wormhole, layerzero, portal)")


# ── Helpers ──

def _normalize_chain(chain: str) -> str:
    """Normalise le nom de la chain."""
    chain = chain.lower().strip()
    aliases = {
        "eth": "ethereum", "sol": "solana", "matic": "polygon",
        "arb": "arbitrum", "avax": "avalanche", "bsc": "bnb",
        "xrpl": "xrp", "ripple": "xrp", "trc": "tron",
    }
    return aliases.get(chain, chain)


def _find_best_protocol(from_chain: str, to_chain: str, preferred: Optional[str] = None) -> Optional[str]:
    """Trouve le meilleur protocole de bridge pour une paire de chains."""
    pair = (from_chain, to_chain)

    # Si un protocole est prefere et supporte cette route
    if preferred and preferred in BRIDGE_PROTOCOLS:
        proto = BRIDGE_PROTOCOLS[preferred]
        if pair in [(s, d) for s, d in proto["routes"]]:
            return preferred

    return _route_index.get(pair)


def _calculate_quote(from_chain: str, to_chain: str, token: str,
                     amount: float, protocol_key: str) -> dict:
    """Calcule un devis de bridge base sur les frais connus."""
    proto = BRIDGE_PROTOCOLS[protocol_key]
    fee_min, fee_max = proto["fee_range_usd"]
    time_min, time_max = proto["time_range_min"]

    # Frais proportionnels au montant (petit montant = frais min, gros = frais max)
    if amount <= 100:
        bridge_fee = fee_min
        est_time = time_max  # petits montants prennent le max de temps
    elif amount <= 10000:
        ratio = (amount - 100) / 9900
        bridge_fee = fee_min + ratio * (fee_max - fee_min)
        est_time = time_max - ratio * (time_max - time_min)
    else:
        bridge_fee = fee_max
        est_time = time_min  # gros montants: priorite haute

    # EVM <-> EVM est plus rapide via LayerZero
    if from_chain in EVM_CHAINS and to_chain in EVM_CHAINS and protocol_key == "layerzero":
        est_time = max(3, est_time * 0.7)

    bridge_fee = round(bridge_fee, 2)
    est_time = round(est_time, 1)

    # MAXIA fee: 0.05% du montant, plafonne a $0.10 — toujours moins cher que les bridges
    maxia_fee = round(min(amount * 0.0005, 0.10), 4)
    estimated_output = round(amount - bridge_fee - maxia_fee, 2)

    return {
        "from_chain": from_chain,
        "to_chain": to_chain,
        "token": token,
        "amount": amount,
        "estimated_output": estimated_output,
        "bridge_fee_usd": bridge_fee,
        "maxia_fee_usd": maxia_fee,
        "estimated_time_min": est_time,
        "bridge_protocol": protocol_key,
        "route": [from_chain, proto["name"].split("/")[0].split(" ")[0].lower(), to_chain],
        "status": "quote_ready",
        "fee_comparison": {
            "maxia": f"${maxia_fee:.4f} (0.05%, capped $0.10)",
            "wormhole_direct": f"${proto['fee_range_usd'][0]:.2f}-${proto['fee_range_usd'][1]:.2f}",
            "maxia_savings": f"${proto['fee_range_usd'][0] - maxia_fee:.2f} cheaper than direct bridge",
        },
    }


# ── Endpoints ──

@router.get("/quote")
async def get_bridge_quote(
    from_chain: str = Query(..., description="Blockchain source"),
    to_chain: str = Query(..., description="Blockchain destination"),
    token: str = Query("USDC", description="Token a bridger"),
    amount: float = Query(..., gt=0, description="Montant a bridger"),
    preferred_protocol: Optional[str] = Query(None, description="Protocole prefere"),
):
    """Obtenir un devis de bridge cross-chain."""
    from_chain = _normalize_chain(from_chain)
    to_chain = _normalize_chain(to_chain)
    token = token.upper()

    # Validation des chains
    if from_chain not in SUPPORTED_CHAINS:
        raise HTTPException(400, f"Chain source non supportee: {from_chain}. Supportees: {SUPPORTED_CHAINS}")
    if to_chain not in SUPPORTED_CHAINS:
        raise HTTPException(400, f"Chain destination non supportee: {to_chain}. Supportees: {SUPPORTED_CHAINS}")
    if from_chain == to_chain:
        raise HTTPException(400, "Source et destination doivent etre differentes")

    # Validation du token
    if token not in CHAIN_TOKENS.get(from_chain, []):
        raise HTTPException(400, f"Token {token} non supporte sur {from_chain}. Disponibles: {CHAIN_TOKENS[from_chain]}")

    # Trouver le protocole
    protocol_key = _find_best_protocol(from_chain, to_chain, preferred_protocol)
    if not protocol_key:
        raise HTTPException(
            404,
            f"Aucune route de bridge trouvee: {from_chain} -> {to_chain}. "
            f"Essayez un bridge en 2 etapes via Solana ou Ethereum.",
        )

    quote = _calculate_quote(from_chain, to_chain, token, amount, protocol_key)
    logger.info(f"[Bridge] Quote: {amount} {token} {from_chain}->{to_chain} via {protocol_key} (fee: ${quote['bridge_fee_usd']})")
    return quote


@router.post("/initiate")
async def initiate_bridge(req: BridgeInitiateRequest):
    """Initier un transfert cross-chain. Retourne les instructions a suivre."""
    from_chain = _normalize_chain(req.from_chain)
    to_chain = _normalize_chain(req.to_chain)
    token = req.token.upper()

    # Validation
    if from_chain not in SUPPORTED_CHAINS:
        raise HTTPException(400, f"Chain source non supportee: {from_chain}")
    if to_chain not in SUPPORTED_CHAINS:
        raise HTTPException(400, f"Chain destination non supportee: {to_chain}")
    if from_chain == to_chain:
        raise HTTPException(400, "Source et destination doivent etre differentes")
    if token not in CHAIN_TOKENS.get(from_chain, []):
        raise HTTPException(400, f"Token {token} non supporte sur {from_chain}")

    preferred = req.preferred_protocol
    protocol_key = _find_best_protocol(from_chain, to_chain, preferred)
    if not protocol_key:
        raise HTTPException(404, f"Aucune route de bridge: {from_chain} -> {to_chain}")

    # Calculer le devis
    quote = _calculate_quote(from_chain, to_chain, token, req.amount, protocol_key)

    bridge_id = str(uuid.uuid4())
    now = int(time.time())

    bridge_record = {
        "bridge_id": bridge_id,
        **quote,
        "sender_wallet": req.sender_wallet,
        "recipient_wallet": req.recipient_wallet,
        "status": "pending",
        "created_at": now,
        "updated_at": now,
        "tx_hash_source": None,
        "tx_hash_destination": None,
        "confirmations": 0,
    }

    _pending_bridges[bridge_id] = bridge_record

    # Instructions specifiques par protocole
    instructions = _build_instructions(protocol_key, from_chain, to_chain, token, req.amount, req.sender_wallet)

    logger.info(f"[Bridge] Initiated {bridge_id[:8]}... : {req.amount} {token} {from_chain}->{to_chain} via {protocol_key}")

    return {
        "bridge_id": bridge_id,
        "status": "pending",
        "quote": quote,
        "instructions": instructions,
        "message": f"Bridge initie. Envoyez {req.amount} {token} selon les instructions ci-dessous.",
    }


@router.get("/status/{bridge_id}")
async def get_bridge_status(bridge_id: str):
    """Verifier le statut d'un bridge en cours."""
    # Chercher dans les bridges en cours
    bridge = _pending_bridges.get(bridge_id)
    if bridge:
        # Simuler la progression pour les bridges en cours
        elapsed = time.time() - bridge["created_at"]
        est_time_sec = bridge.get("estimated_time_min", 15) * 60

        if elapsed > est_time_sec:
            bridge["status"] = "awaiting_confirmation"
            bridge["progress_pct"] = 100
        else:
            bridge["progress_pct"] = min(99, round(elapsed / est_time_sec * 100))
            if bridge["progress_pct"] > 70:
                bridge["status"] = "finalizing"
            elif bridge["progress_pct"] > 30:
                bridge["status"] = "bridging"
            else:
                bridge["status"] = "pending"

        bridge["updated_at"] = int(time.time())
        return bridge

    # Chercher dans les bridges completes
    for b in _completed_bridges:
        if b.get("bridge_id") == bridge_id:
            return b

    raise HTTPException(404, f"Bridge {bridge_id} introuvable")


@router.get("/routes")
async def list_bridge_routes():
    """Lister toutes les routes de bridge supportees entre les 14 chains."""
    routes = []
    seen = set()

    for proto_key, proto in BRIDGE_PROTOCOLS.items():
        for src, dst in proto["routes"]:
            pair_key = f"{src}-{dst}-{proto_key}"
            if pair_key in seen:
                continue
            seen.add(pair_key)

            # Tokens disponibles = intersection des tokens des 2 chains
            src_tokens = set(CHAIN_TOKENS.get(src, []))
            dst_tokens = set(CHAIN_TOKENS.get(dst, []))
            common_tokens = sorted(src_tokens & dst_tokens)
            if not common_tokens:
                common_tokens = ["USDC"]  # USDC est toujours bridgeable

            routes.append({
                "from_chain": src,
                "to_chain": dst,
                "protocol": proto_key,
                "protocol_name": proto["name"],
                "supported_tokens": common_tokens,
                "estimated_fee_usd": f"${proto['fee_range_usd'][0]:.2f}-${proto['fee_range_usd'][1]:.2f}",
                "estimated_time_min": f"{proto['time_range_min'][0]}-{proto['time_range_min'][1]}",
            })

    # Trier par chain source puis destination
    routes.sort(key=lambda r: (r["from_chain"], r["to_chain"], r["protocol"]))

    return {
        "total_routes": len(routes),
        "supported_chains": SUPPORTED_CHAINS,
        "supported_protocols": list(BRIDGE_PROTOCOLS.keys()),
        "routes": routes,
    }


@router.get("/history")
async def get_bridge_history(
    wallet: str = Query(..., description="Adresse wallet pour filtrer l'historique"),
    limit: int = Query(50, ge=1, le=200, description="Nombre max de resultats"),
):
    """Historique des bridges pour un wallet donne."""
    wallet_lower = wallet.lower()

    # Chercher dans les bridges en cours et completes
    results = []

    for b in _pending_bridges.values():
        sender = (b.get("sender_wallet") or "").lower()
        recipient = (b.get("recipient_wallet") or "").lower()
        if sender == wallet_lower or recipient == wallet_lower:
            results.append(b)

    for b in _completed_bridges:
        sender = (b.get("sender_wallet") or "").lower()
        recipient = (b.get("recipient_wallet") or "").lower()
        if sender == wallet_lower or recipient == wallet_lower:
            results.append(b)

    # Trier par date de creation (plus recent en premier)
    results.sort(key=lambda r: r.get("created_at", 0), reverse=True)

    return {
        "wallet": wallet,
        "total": len(results),
        "bridges": results[:limit],
    }


# ── Helper: construire les instructions par protocole ──

def _build_instructions(protocol_key: str, from_chain: str, to_chain: str,
                        token: str, amount: float, sender: str) -> dict:
    """Genere les instructions specifiques pour effectuer le bridge."""
    proto = BRIDGE_PROTOCOLS[protocol_key]

    base_instructions = {
        "protocol": proto["name"],
        "steps": [],
    }

    if protocol_key == "wormhole":
        base_instructions["portal_url"] = "https://wormhole.com/bridge"
        base_instructions["steps"] = [
            f"1. Rendez-vous sur https://portalbridge.com ou https://wormhole.com/bridge",
            f"2. Connectez votre wallet {from_chain} ({sender[:8]}...)",
            f"3. Selectionnez {from_chain.upper()} comme source et {to_chain.upper()} comme destination",
            f"4. Choisissez {token} et entrez {amount}",
            f"5. Approuvez la transaction et attendez ~{proto['time_range_min'][0]}-{proto['time_range_min'][1]} minutes",
            f"6. Reclamez vos tokens sur {to_chain.upper()} une fois le VAA genere",
        ]
    elif protocol_key == "layerzero":
        base_instructions["portal_url"] = "https://stargate.finance/transfer"
        base_instructions["steps"] = [
            f"1. Rendez-vous sur https://stargate.finance/transfer",
            f"2. Connectez votre wallet EVM ({sender[:8]}...)",
            f"3. Selectionnez {from_chain.upper()} -> {to_chain.upper()}",
            f"4. Entrez {amount} {token}",
            f"5. Approuvez et signez la transaction",
            f"6. Les tokens arrivent automatiquement en ~{proto['time_range_min'][0]}-{proto['time_range_min'][1]} minutes",
        ]
    elif protocol_key == "portal":
        base_instructions["portal_url"] = "https://portalbridge.com"
        base_instructions["steps"] = [
            f"1. Rendez-vous sur https://portalbridge.com",
            f"2. Connectez votre wallet {from_chain} ({sender[:8]}...)",
            f"3. Source: {from_chain.upper()}, Destination: {to_chain.upper()}",
            f"4. Token: {token}, Montant: {amount}",
            f"5. Approuvez la transaction (peut necessiter 2 etapes: approve + transfer)",
            f"6. Attendez ~{proto['time_range_min'][0]}-{proto['time_range_min'][1]} minutes et reclamez sur {to_chain.upper()}",
        ]

    base_instructions["note"] = (
        "MAXIA ne facture aucun frais supplementaire sur les bridges. "
        "Seuls les frais du protocole de bridge et le gas de la chain s'appliquent."
    )

    return base_instructions
