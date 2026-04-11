"""Public API — Trading routes (DeFi, GPU, Stocks, Crypto/Swap).

Extracted from public_api.py (S34 split).
"""
import logging, json, time, datetime

from fastapi import APIRouter, HTTPException, Header, Request

from core.config import (
    TREASURY_ADDRESS, get_commission_bps, get_commission_tier_name,
)
from marketplace.public_api_shared import (
    _registered_agents, _agent_services, _transactions,
    _load_from_db, _check_safety, _check_rate, _get_agent, _safe_float,
    _validate_solana_address,
    _agent_update_lock,
)

logger = logging.getLogger(__name__)

router = APIRouter()

@router.get("/defi/best-yield")
async def defi_best_yield(asset: str = "USDC", chain: str = "", min_tvl: float = 100000,
                          limit: int = 10, type: str = ""):
    """Find the best DeFi yields for an asset. Free, no auth.

    Examples:
      GET /defi/best-yield?asset=USDC
      GET /defi/best-yield?asset=ETH&chain=ethereum&limit=5
      GET /defi/best-yield?asset=SOL&chain=solana&type=staking
      GET /defi/best-yield?asset=ALL&type=lending
    Types: staking, lending, lp, farming (empty = all)
    """
    try:
        from trading.defi_scanner import get_best_yields
        yields = await get_best_yields(asset, chain, min_tvl, limit * 3, yield_type=type)
        # Filter insane APY (>1000%) and tiny pools
        sane = [y for y in yields if 0 < y.get("apy", 0) < 200 and y.get("tvl_usd", 0) >= 100000]
        if sane:
            return {
                "asset": asset,
                "chain": chain or "all",
                "type": type or "all",
                "results": len(sane[:limit]),
                "yields": sane[:limit],
                "sources": ["DeFiLlama", "Marinade", "Jito", "Lido"],
            }
    except Exception:
        pass

    # Fallback: fetch live depuis DeFiLlama au lieu de valeurs hardcodees
    try:
        import httpx as _hx
        _LLAMA_FALLBACK_MAP = {
            "USDC": [
                ("aave-v3", "usdc", "Aave V3", "Ethereum", "https://app.aave.com/", "low"),
                ("compound-v3", "usdc", "Compound V3", "Ethereum", "https://app.compound.finance/", "low"),
                ("kamino-lend", "usdc", "Kamino", "Solana", "https://app.kamino.finance/", "low"),
            ],
            "SOL": [
                ("marinade-finance", "msol", "Marinade", "Solana", "https://marinade.finance/app/stake/", "low"),
                ("jito", "jitosol", "Jito", "Solana", "https://www.jito.network/staking/", "low"),
                ("sanctum", "inf", "Sanctum", "Solana", "https://app.sanctum.so/", "medium"),
                ("raydium", "sol-usdc", "Raydium", "Solana", "https://raydium.io/liquidity/", "medium"),
            ],
            "ETH": [
                ("lido", "steth", "Lido", "Ethereum", "https://stake.lido.fi/", "low"),
                ("rocket-pool", "reth", "Rocket Pool", "Ethereum", "https://stake.rocketpool.net/", "low"),
                ("eigenlayer", "eth", "Eigenlayer", "Ethereum", "https://app.eigenlayer.xyz/", "medium"),
            ],
        }
        async with _hx.AsyncClient(timeout=15) as _client:
            _resp = await _client.get("https://yields.llama.fi/pools")
            _resp.raise_for_status()
            _pools = _resp.json().get("data", [])
            # Index par project_symbol
            _pool_idx = {}
            for _p in _pools:
                _k = f"{_p.get('project', '').lower()}_{_p.get('symbol', '').lower()}"
                if _k not in _pool_idx or _p.get("apy", 0) > _pool_idx[_k].get("apy", 0):
                    _pool_idx[_k] = _p
            # Aussi indexer par project seul pour les chains specifiques
            for _p in _pools:
                proj = _p.get("project", "").lower()
                ch = _p.get("chain", "").lower()
                sym = (_p.get("symbol") or "").upper()
                _k2 = f"{proj}_{ch}_{sym}"
                if _k2 not in _pool_idx or _p.get("apy", 0) > _pool_idx[_k2].get("apy", 0):
                    _pool_idx[_k2] = _p

            targets = _LLAMA_FALLBACK_MAP.get(asset.upper(), _LLAMA_FALLBACK_MAP.get("USDC", []))
            fb = []
            for proj_key, sym_key, display, fb_chain, url, risk in targets:
                lookup = f"{proj_key}_{sym_key}"
                pool_data = _pool_idx.get(lookup, {})
                apy_val = round(pool_data.get("apy", 0), 2)
                tvl_val = round(pool_data.get("tvlUsd", 0), 0)
                if apy_val > 0:
                    fb.append({"project": display, "chain": fb_chain, "apy": apy_val,
                               "tvl_usd": tvl_val, "risk": risk, "url": url, "source": "defillama_live"})
            if fb:
                return {
                    "asset": asset,
                    "chain": chain or "all",
                    "results": len(fb[:limit]),
                    "yields": fb[:limit],
                    "source": "defillama_fallback",
                }
    except Exception:
        pass

    # Dernier recours: message explicite qu'aucune donnee live n'est disponible
    return {
        "asset": asset,
        "chain": chain or "all",
        "results": 0,
        "yields": [],
        "source": "unavailable",
        "message": "APIs DeFiLlama et natives indisponibles. Reessayez dans quelques minutes.",
    }


@router.get("/defi/protocol")
async def defi_protocol(name: str = "aave"):
    """Get stats for a specific DeFi protocol."""
    try:
        from trading.defi_scanner import get_protocol_stats
        return await get_protocol_stats(name)
    except Exception as e:
        return safe_error(e, "operation")


@router.get("/defi/chains")
async def defi_chains():
    """Get TVL by blockchain."""
    try:
        from trading.defi_scanner import get_chain_tvl
        return await get_chain_tvl()
    except Exception as e:
        return safe_error(e, "operation")


# ══════════════════════════════════════════
#  SENTIMENT ANALYSIS
# ══════════════════════════════════════════

@router.get("/sentiment")
async def public_sentiment(token: str = "BTC"):
    """Get crypto sentiment analysis. Free, no auth.
    
    Sources: CoinGecko community data, Reddit activity, LunarCrush (optional).
    Examples: /sentiment?token=BTC, /sentiment?token=SOL
    """
    try:
        from ai.sentiment_analyzer import get_sentiment
        return await get_sentiment(token)
    except Exception as e:
        return safe_error(e, "operation")


@router.get("/trending")
async def public_trending():
    """Get trending crypto tokens."""
    try:
        from ai.sentiment_analyzer import get_trending
        return {"trending": await get_trending()}
    except Exception as e:
        return safe_error(e, "operation")


@router.get("/fear-greed")
async def public_fear_greed():
    """Get crypto Fear & Greed Index."""
    try:
        from features.web3_services import get_fear_greed_index
        return await get_fear_greed_index()
    except Exception as e:
        return safe_error(e, "operation")


# ══════════════════════════════════════════
#  WEB3 AI SERVICES
# ══════════════════════════════════════════

@router.get("/token-risk")
async def public_token_risk(address: str = ""):
    """Analyze rug pull risk for a Solana token. Free, no auth.
    
    Returns risk score (0-100), warnings, recommendation.
    Example: /token-risk?address=TOKEN_MINT_ADDRESS
    """
    if not address:
        return {"error": "address parameter required"}
    try:
        from features.web3_services import analyze_token_risk
        return await analyze_token_risk(address)
    except Exception as e:
        return safe_error(e, "operation")


@router.get("/wallet-analysis")
async def public_wallet_analysis(address: str = ""):
    """Analyze a Solana wallet — holdings, activity, profile. Free, no auth."""
    if not address:
        return {"error": "address parameter required"}
    try:
        from features.web3_services import analyze_wallet
        return await analyze_wallet(address)
    except Exception as e:
        return safe_error(e, "operation")


# ══════════════════════════════════════════

@router.get("/gpu/tiers")
async def public_gpu_tiers():
    """GPU tiers with live Akash pricing + 15% MAXIA markup. Cheaper than cloud alternatives."""
    import time as _t
    from core.config import GPU_TIERS, AKASH_ENABLED
    _MARKUP = 0.15

    tiers = []
    akash_ok = False
    _akash = None
    akash_map = {}
    if AKASH_ENABLED:
        try:
            from gpu.akash_client import akash as _akash_inst, AKASH_GPU_MAP
            _akash = _akash_inst
            akash_map = AKASH_GPU_MAP
            akash_ok = True
        except Exception:
            pass

    for gpu in GPU_TIERS:
        tier_id = gpu["id"]
        base_price = gpu["base_price_per_hour"]

        # Akash price with MAXIA markup
        if akash_ok and tier_id in akash_map and _akash:
            akash_cost = await _akash.get_price_estimate(tier_id)
            if akash_cost:
                sell_price = round(akash_cost * (1 + _MARKUP), 2)
            else:
                sell_price = round(base_price * 0.85, 2)
        else:
            sell_price = base_price

        # Check real availability + count on Akash
        is_avail = True
        avail_count = 0
        if akash_ok and _akash and tier_id in akash_map:
            try:
                await _akash.get_gpu_availability()
                avail_count = _akash.get_tier_count(tier_id)
                is_avail = avail_count > 0
            except Exception:
                is_avail = True

        tier = {
            "id": tier_id,
            "label": gpu["label"],
            "vram_gb": gpu["vram_gb"],
            "price_per_hour_usdc": sell_price,
            "available": is_avail,
            "available_count": avail_count,
            "source": "live" if akash_ok else "fallback",
            "maxia_markup": f"{int(_MARKUP*100)}%",
            "provider": "akash",
        }
        if gpu.get("local"):
            tier["local"] = True
            tier["available"] = False
            tier["available_count"] = 0
        tiers.append(tier)

    from core.config import TREASURY_ADDRESS, TREASURY_ADDRESS_BASE
    return {
        "gpu_count": len(tiers),
        "tiers": tiers,
        "treasury_solana": TREASURY_ADDRESS,
        "treasury_base": TREASURY_ADDRESS_BASE,
        "provider": "akash",
        "network": "Akash Network (decentralized)",
        "markup": f"{int(_MARKUP*100)}%",
        "note": "Cheaper than RunPod, AWS, and Lambda Labs",
        "updated_at": _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime()),
    }


@router.get("/gpu/compare")
async def public_gpu_compare(gpu: str = "h100_sxm5"):
    """Compare GPU prices across providers. Shows MAXIA vs AWS vs GCP vs Lambda vs Vast.ai.

    Example: /gpu/compare?gpu=h100_sxm5
    """
    from gpu.runpod_client import COMPETITOR_PRICES, GPU_FULL_MAP
    info = GPU_FULL_MAP.get(gpu)
    prices = COMPETITOR_PRICES.get(gpu)
    if not info or not prices:
        available = list(COMPETITOR_PRICES.keys())
        return {"error": f"GPU '{gpu}' not found. Available: {available}"}

    maxia_price = prices.get("runpod_secure", prices.get("runpod_community", 0))
    comparison = []
    for provider, price in prices.items():
        if price and price > 0:
            savings = round((1 - maxia_price / price) * 100, 1) if price > maxia_price else 0
            more_expensive = round((maxia_price / price - 1) * 100, 1) if price < maxia_price else 0
            comparison.append({
                "provider": provider,
                "price_per_hour": price,
                "vs_maxia": f"{savings}% cheaper" if savings > 0 else f"{more_expensive}% more" if more_expensive > 0 else "same",
            })

    comparison.sort(key=lambda x: x["price_per_hour"])

    return {
        "gpu": gpu,
        "label": info["runpod_id"].replace("NVIDIA ", ""),
        "vram_gb": info["vram"],
        "maxia_price": maxia_price,
        "maxia_markup": "0%",
        "comparison": comparison,
        "note": "MAXIA charges 0% markup. Same price as RunPod but payable in USDC on Solana.",
    }


@router.post("/gpu/rent")
async def public_gpu_rent(req: dict, x_api_key: str = Header(None, alias="X-API-Key")):
    """Louer un GPU. L IA paie en USDC, MAXIA provisionne via RunPod."""
    if not x_api_key:
        raise HTTPException(401, "Header X-API-Key requis")

    agent = _get_agent(x_api_key)
    _check_rate(x_api_key)

    # Agent permissions — scope + spend
    try:
        from agents.agent_permissions import check_agent_scope, check_agent_spend
        wallet = agent.get("wallet", "")
        await check_agent_scope(x_api_key, wallet, "gpu:rent")
        hours = float(req.get("hours", 1))
        # Estimate cost from GPU tier
        from core.config import GPU_TIERS
        tier_id = req.get("gpu_tier_id", req.get("gpu", ""))
        tier_info = next((t for t in GPU_TIERS if t["id"] == tier_id), None)
        if tier_info:
            est_cost = tier_info["base_price_per_hour"] * hours
            await check_agent_spend(x_api_key, wallet, est_cost)
    except HTTPException:
        raise
    except Exception:
        pass

    # Fix #7: String length limits
    tier_id = req.get("tier", "").strip()[:50] if isinstance(req.get("tier"), str) else ""
    payment_tx = req.get("payment_tx", "").strip()[:200] if isinstance(req.get("payment_tx"), str) else ""
    # Fix #4: Float validation with try/except
    try:
        hours = float(req.get("hours", 1))
    except (TypeError, ValueError):
        raise HTTPException(400, "hours must be a number")
    import math
    if math.isnan(hours) or math.isinf(hours):
        raise HTTPException(400, "hours must be a valid number")

    if hours <= 0 or hours > 720:
        raise HTTPException(400, "Duree entre 0.1 et 720 heures")

    # Trouver le GPU
    from core.config import GPU_TIERS, BROKER_MARGIN
    gpu = None
    for g in GPU_TIERS:
        if g["id"] == tier_id:
            gpu = g
            break
    if not gpu:
        raise HTTPException(404, f"GPU inconnu: {tier_id}. Disponibles: {[g['id'] for g in GPU_TIERS]}")

    # Calculer le prix
    price_per_hour = round(gpu["base_price_per_hour"] * BROKER_MARGIN, 4)
    total_price = round(price_per_hour * hours, 4)

    # Commission MAXIA (based on rental total cost)
    commission_bps = get_commission_bps(total_price)
    commission = round(total_price * commission_bps / 10000, 4)
    total_with_commission = round(total_price + commission, 4)

    # Art.1 — Verifier que ce n est pas un usage interdit
    purpose = req.get("purpose", "")
    if purpose:
        _check_safety(purpose, "purpose")

    # Fix #1/#2/#11: Verify payment BEFORE provisioning + idempotency
    if not payment_tx:
        raise HTTPException(400, "payment_tx required for GPU rental")

    from core.database import db
    if await db.tx_already_processed(payment_tx):
        raise HTTPException(400, "Payment already used")

    # Verify USDC payment on-chain
    try:
        from blockchain.solana_verifier import verify_transaction
        tx_result = await verify_transaction(
            tx_signature=payment_tx,
            expected_amount_usdc=total_with_commission,
            expected_recipient=TREASURY_ADDRESS,
        )
        if not tx_result.get("valid"):
            raise HTTPException(400, "Payment invalid: verification failed")
    except HTTPException:
        raise
    except Exception as e:
        # Fix #21: Don't leak internal error details
        logger.error("GPU payment verification error: %s", e)
        raise HTTPException(400, "Payment verification failed")

    # Provisionner le GPU via RunPod
    from gpu.runpod_client import RunPodClient
    from core.config import RUNPOD_API_KEY
    runpod = RunPodClient(api_key=RUNPOD_API_KEY)
    instance = await runpod.rent_gpu(tier_id, hours)

    if not instance.get("success"):
        # Fix #21: Don't leak internal RunPod error details
        logger.error("RunPod provisioning error: %s", instance.get("error", "indisponible"))
        raise HTTPException(502, "GPU provisioning temporarily unavailable")

    # Isolation multi-tenant
    from enterprise.tenant_isolation import get_current_tenant
    _tenant_id = get_current_tenant() or "default"

    # Enregistrer la transaction
    import uuid
    instance_id = instance.get("instanceId", str(uuid.uuid4()))
    tx = {
        "tx_id": str(uuid.uuid4()),
        "buyer": agent["name"],
        "buyer_wallet": agent["wallet"],
        "type": "gpu_rental",
        "gpu": gpu["label"],
        "tier_id": tier_id,
        "hours": hours,
        "price_per_hour": price_per_hour,
        "total_usdc": total_price,
        "commission_usdc": commission,
        "commission_bps": commission_bps,
        "total_with_commission": total_with_commission,
        "payment_tx": payment_tx,
        "instance_id": instance_id,
        "ssh_endpoint": instance.get("ssh_endpoint", ""),
        "tenant_id": _tenant_id,
        "timestamp": int(time.time()),
    }
    _transactions.append(tx)

    # Fix #3: Persist transaction to database
    await db.record_transaction(agent["wallet"], payment_tx, total_with_commission, "gpu_rental")

    # Fix #7: Persist GPU instance to database
    await db.save_gpu_instance({
        "instance_id": instance_id,
        "agent_wallet": agent["wallet"],
        "agent_name": agent["name"],
        "gpu_tier": tier_id,
        "duration_hours": hours,
        "price_per_hour": price_per_hour,
        "total_cost": total_price,
        "commission": commission,
        "payment_tx": payment_tx,
        "runpod_pod_id": instance_id,
        "status": instance.get("status", "provisioning"),
        "ssh_endpoint": instance.get("ssh_endpoint", ""),
        "scheduled_end": int(time.time() + hours * 3600),
    })

    # Mettre a jour les stats
    agent["volume_30d"] += total_with_commission
    agent["total_spent"] += total_with_commission
    agent["tier"] = _get_tier_name(agent["volume_30d"])

    # Fix #4: Persist agent stats to database
    await db.update_agent(agent["api_key"], {
        "volume_30d": agent["volume_30d"],
        "total_spent": agent["total_spent"],
        "tier": agent["tier"],
    })

    # Alerte Discord
    try:
        from infra.alerts import alert_revenue
        await alert_revenue(commission, f"Location GPU {gpu['label']} — {agent['name']} ({hours}h)")
    except Exception:
        pass

    logger.info("GPU loue: %s x%sh par %s — %s USDC", gpu["label"], hours, agent["name"], total_with_commission)

    return {
        "success": True,
        "tx_id": tx["tx_id"],
        "gpu": gpu["label"],
        "vram_gb": gpu["vram_gb"],
        "hours": hours,
        "price_per_hour_usdc": price_per_hour,
        "total_gpu_usdc": total_price,
        "commission_usdc": commission,
        "total_to_pay_usdc": total_with_commission,
        "your_tier": agent["tier"],
        "instance_id": instance.get("instanceId", ""),
        "ssh_endpoint": instance.get("sshEndpoint", ""),
        "status": "provisioning",
        "message": f"GPU {gpu['label']} en cours de provisionnement. Connectez-vous via SSH.",
    }


@router.get("/gpu/my-instances")
async def public_gpu_instances(x_api_key: str = Header(None, alias="X-API-Key")):
    """Liste les GPU en cours pour cet agent."""
    if not x_api_key:
        raise HTTPException(401, "Header X-API-Key requis")
    agent = _get_agent(x_api_key)

    my_gpus = [
        t for t in _transactions
        if t.get("type") == "gpu_rental" and t.get("buyer") == agent["name"]
    ]
    return {
        "instances": my_gpus[-10:],
        "total_spent_gpu": sum(t.get("total_with_commission", 0) for t in my_gpus),
    }


@router.get("/gpu/status/{pod_id}")
async def public_gpu_status(pod_id: str, x_api_key: str = Header(None, alias="X-API-Key")):
    """Statut d un pod GPU (utilisation, temps restant). Requiert API key."""
    if not x_api_key:
        raise HTTPException(401, "Header X-API-Key requis")
    _get_agent(x_api_key)

    from gpu.runpod_client import runpod_client
    return await runpod_client.get_pod_status(pod_id)


@router.post("/gpu/terminate/{pod_id}")
async def public_gpu_terminate(pod_id: str, x_api_key: str = Header(None, alias="X-API-Key")):
    """Arreter un pod GPU avant la fin. Requiert API key."""
    if not x_api_key:
        raise HTTPException(401, "Header X-API-Key requis")
    agent = _get_agent(x_api_key)

    # Verifier que le pod appartient a cet agent
    my_gpus = [t for t in _transactions if t.get("instance_id") == pod_id and t.get("buyer") == agent["name"]]
    if not my_gpus:
        raise HTTPException(403, "Ce pod ne vous appartient pas")

    from gpu.runpod_client import runpod_client
    result = await runpod_client.terminate_pod(pod_id)

    if result.get("success"):
        # Fix #6: Update DB with actual cost on termination
        try:
            from core.database import db
            await db.update_gpu_instance(pod_id, {
                "status": "terminated",
                "actual_end": int(time.time()),
                "actual_cost": result.get("actual_cost", 0),
            })
        except Exception:
            pass
        try:
            from infra.alerts import alert_system
            await alert_system("GPU Termine", f"Pod {pod_id} arrete par {agent['name']}")
        except Exception:
            pass

    return result


@router.get("/gpu/compare-detailed")
async def gpu_price_compare():
    """Compare les prix MAXIA vs concurrence. Sans auth."""
    from core.config import GPU_TIERS, BROKER_MARGIN

    comparisons = []
    competitors = {
        "rtx4090": {"runpod": 0.69, "vast_ai": 0.34, "lambda": None},
        "a100_80": {"runpod": 1.79, "vast_ai": 0.75, "lambda": 1.29},
        "h100_sxm5": {"runpod": 3.29, "vast_ai": 1.49, "lambda": 2.49},
        "a6000": {"runpod": 0.80, "vast_ai": 0.40, "lambda": 0.80},
        "4xa100": {"runpod": 7.16, "vast_ai": 3.00, "lambda": 5.16},
    }

    for gpu in GPU_TIERS:
        maxia_price = round(gpu["base_price_per_hour"] * BROKER_MARGIN, 4)
        comp = competitors.get(gpu["id"], {})
        comparisons.append({
            "gpu": gpu["label"],
            "maxia_price": maxia_price,
            "maxia_advantage": "Prix coutant + commission dynamique (0.1% marketplace / 0.01% swap pour les gros volumes)",
            "runpod": comp.get("runpod"),
            "vast_ai": comp.get("vast_ai"),
            "lambda": comp.get("lambda"),
        })

    return {
        "comparisons": comparisons,
        "maxia_commission": "Marketplace: 1% Bronze → 0.5% Gold → 0.1% Whale | Swap: 0.10% → 0.01% (basee sur votre volume 30j)",
        "unique_advantages": [
            "Paiement USDC sur Solana (pas de carte bancaire)",
            "API unifiee (GPU + services IA + data)",
            "Commission la plus basse pour les gros volumes (0.1% marketplace / 0.01% swap)",
            "Protocole x402 natif (paiement automatique HTTP)",
            "Pas besoin de compte RunPod/AWS/GCP",
        ],
    }


# ══════════════════════════════════════════
#  ENCHERES GPU (via API publique)
# ══════════════════════════════════════════

@router.get("/gpu/auctions")
async def public_gpu_auctions():
    """Liste les encheres GPU en cours. Sans auth."""
    try:
        from marketplace.auction_manager import AuctionManager
        # On ne peut pas acceder au singleton facilement, retourner un placeholder
        return {"message": "Connectez-vous en WebSocket sur /auctions pour les encheres temps reel", "endpoint": "wss://maxiaworld.app/auctions"}
    except Exception:
        return {"auctions": [], "message": "Aucune enchere en cours"}


@router.post("/gpu/auction/create")
async def public_create_auction(req: dict, x_api_key: str = Header(None, alias="X-API-Key")):
    """Creer une enchere GPU via l API publique."""
    if not x_api_key:
        raise HTTPException(401, "Header X-API-Key requis")

    agent = _get_agent(x_api_key)
    _check_rate(x_api_key)

    # Fix #7: String length limits + Fix #4: Float validation
    tier_id = req.get("tier", "").strip()[:50] if isinstance(req.get("tier"), str) else ""
    try:
        hours = float(req.get("hours", 1))
    except (TypeError, ValueError):
        raise HTTPException(400, "hours must be a number")
    hours = max(0.1, min(720, hours))

    from core.config import GPU_TIERS, BROKER_MARGIN
    gpu = None
    for g in GPU_TIERS:
        if g["id"] == tier_id:
            gpu = g
            break
    if not gpu:
        raise HTTPException(404, f"GPU inconnu: {tier_id}")

    cost = round(gpu["base_price_per_hour"] * hours * BROKER_MARGIN, 4)

    import uuid
    auction = {
        "auctionId": str(uuid.uuid4()),
        "gpuTierId": tier_id,
        "gpuLabel": gpu["label"],
        "vramGb": gpu["vram_gb"],
        "durationHours": hours,
        "startPrice": cost,
        "currentBid": cost,
        "currentLeader": None,
        "brokerWallet": agent["wallet"],
        "status": "open",
        "createdBy": agent["name"],
        "createdAt": int(time.time()),
    }

    return {
        "success": True,
        **auction,
        "message": f"Enchere ouverte pour {gpu['label']} x{hours}h. Prix de depart: {cost} USDC.",
        "bid_endpoint": "wss://maxiaworld.app/auctions",
    }


# ══════════════════════════════════════════
#  BOURSE D'ACTIONS TOKENISEES
# ══════════════════════════════════════════

def _us_market_state() -> tuple[bool, str, str]:
    """Return ``(is_open, status, last_open_iso)`` for the US equity market.

    ``status`` is one of:
        * ``"open"``              — regular trading hours, Mon-Fri
        * ``"closed_weekend"``    — Saturday or Sunday
        * ``"closed_after_hours"``— weekday but outside 9:30-16:00 ET

    ``last_open_iso`` is an ISO-8601 UTC timestamp for the most recent
    close (16:00 ET). Useful for rendering "Last Fri close" labels.

    DST-aware: uses ``zoneinfo`` on Python 3.9+. Falls back to a fixed
    UTC-4 offset (EDT) if ``zoneinfo`` is unavailable.
    """
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td

    try:
        from zoneinfo import ZoneInfo  # py>=3.9
        ny = _dt.now(ZoneInfo("America/New_York"))
    except Exception:
        # Fallback: assume EDT (April through November). Not perfect but
        # never raises and is correct for the majority of the year.
        now_utc = _dt.now(_tz.utc)
        ny = (now_utc - _td(hours=4)).replace(tzinfo=_tz(_td(hours=-4)))

    weekday = ny.weekday()  # Mon=0 .. Sun=6
    minutes_since_midnight = ny.hour * 60 + ny.minute
    open_min = 9 * 60 + 30   # 09:30
    close_min = 16 * 60      # 16:00

    def _last_close_iso(ref: _dt) -> str:
        """Return ISO UTC of the most recent 16:00 ET close at or before ref."""
        close = ref.replace(hour=16, minute=0, second=0, microsecond=0)
        # If we haven't reached today's close yet, step back to yesterday.
        if ref < close:
            close = close - _td(days=1)
        # Walk back over weekend days (Sat=5, Sun=6).
        while close.weekday() >= 5:
            close = close - _td(days=1)
        return close.astimezone(_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if weekday >= 5:  # Saturday or Sunday
        return False, "closed_weekend", _last_close_iso(ny)
    if open_min <= minutes_since_midnight < close_min:
        return True, "open", _last_close_iso(ny)
    return False, "closed_after_hours", _last_close_iso(ny)


def _is_us_market_open() -> bool:
    """Back-compat wrapper. Returns only the open/closed bool."""
    return _us_market_state()[0]


def _apply_market_state(result: dict) -> dict:
    """Mutate ``result`` in place to add ``market_open``, ``market_status``
    and ``last_open_iso`` fields. Returns the same dict for chaining."""
    is_open, status, last_close = _us_market_state()
    result["market_open"] = is_open
    result["market_status"] = status
    result["last_open_iso"] = last_close
    return result


@router.get("/stocks")
async def list_stocks():
    """Liste les actions tokenisees disponibles avec prix. Sans auth."""
    from trading.tokenized_stocks import stock_exchange
    result = await stock_exchange.list_stocks()
    return _apply_market_state(result)


@router.get("/stocks/price/{symbol}")
async def stock_price(symbol: str):
    """Prix temps reel d une action. Sans auth."""
    from trading.tokenized_stocks import stock_exchange
    result = await stock_exchange.get_price(symbol)
    if "error" not in result:
        _apply_market_state(result)
    return result


@router.post("/stocks/buy")
async def buy_stock(req: dict, x_api_key: str = Header(None, alias="X-API-Key")):
    """Acheter des actions tokenisees. Paie en USDC."""
    if not x_api_key:
        raise HTTPException(401, "Header X-API-Key requis")
    agent = _get_agent(x_api_key)
    _check_rate(x_api_key)

    # Agent permissions — scope + spend
    try:
        from agents.agent_permissions import check_agent_scope, check_agent_spend
        wallet = agent.get("wallet", "")
        await check_agent_scope(x_api_key, wallet, "stocks:trade")
        amount_usd = float(req.get("amount_usdc", 0))
        if amount_usd > 0:
            await check_agent_spend(x_api_key, wallet, amount_usd)
    except HTTPException:
        raise
    except Exception:
        pass

    # Fix #7: String length limits + Fix #4: Float validation
    symbol = req.get("symbol", "").strip()[:20] if isinstance(req.get("symbol"), str) else ""
    payment_tx = req.get("payment_tx", "").strip()[:200] if isinstance(req.get("payment_tx"), str) else ""
    try:
        amount_usdc = float(req.get("amount_usdc", 0))
    except (TypeError, ValueError):
        raise HTTPException(400, "amount_usdc must be a number")
    import math
    if math.isnan(amount_usdc) or math.isinf(amount_usdc) or amount_usdc <= 0:
        raise HTTPException(400, "amount_usdc must be a positive number")

    from trading.tokenized_stocks import stock_exchange
    result = await stock_exchange.buy_stock(
        buyer_api_key=x_api_key,
        buyer_name=agent["name"],
        buyer_wallet=agent["wallet"],
        symbol=symbol,
        amount_usdc=amount_usdc,
        buyer_volume_30d=agent.get("volume_30d", 0),
        payment_tx=payment_tx,
    )

    if result.get("success"):
        agent["volume_30d"] += amount_usdc
        agent["total_spent"] += amount_usdc
        agent["tier"] = _get_tier_name(agent["volume_30d"])

    return result


@router.post("/stocks/sell")
async def sell_stock(req: dict, x_api_key: str = Header(None, alias="X-API-Key")):
    """Vendre des actions tokenisees. Recoit USDC."""
    if not x_api_key:
        raise HTTPException(401, "Header X-API-Key requis")
    agent = _get_agent(x_api_key)
    _check_rate(x_api_key)

    # Fix #7: String length limits + Fix #4: Float validation
    symbol = req.get("symbol", "").strip()[:20] if isinstance(req.get("symbol"), str) else ""
    try:
        shares = float(req.get("shares", 0))
    except (TypeError, ValueError):
        raise HTTPException(400, "shares must be a number")
    import math
    if math.isnan(shares) or math.isinf(shares) or shares <= 0:
        raise HTTPException(400, "shares must be a positive number")

    from trading.tokenized_stocks import stock_exchange
    result = await stock_exchange.sell_stock(
        seller_api_key=x_api_key,
        seller_name=agent["name"],
        seller_wallet=agent["wallet"],
        symbol=symbol,
        shares=shares,
        seller_volume_30d=agent.get("volume_30d", 0),
    )

    if result.get("success"):
        agent["volume_30d"] += result.get("gross_usdc", 0)

    return result


@router.get("/stocks/portfolio")
async def stock_portfolio(x_api_key: str = Header(None, alias="X-API-Key")):
    """Mon portefeuille d actions tokenisees."""
    if not x_api_key:
        raise HTTPException(401, "Header X-API-Key requis")
    agent = _get_agent(x_api_key)

    from trading.tokenized_stocks import stock_exchange
    return await stock_exchange.get_portfolio(x_api_key)


@router.get("/stocks/compare-fees")
async def stock_compare_fees():
    """Compare les frais MAXIA vs concurrence. Sans auth."""
    from trading.tokenized_stocks import stock_exchange
    return stock_exchange.compare_fees()


@router.get("/stocks/fees")
async def stock_fees():
    """Alias de /stocks/compare-fees."""
    from trading.tokenized_stocks import stock_exchange
    return stock_exchange.compare_fees()


@router.get("/stocks/stats")
async def stock_stats():
    """Statistiques des actions tokenisees on-chain. Sans auth."""
    from trading.tokenized_stocks import stock_exchange
    return stock_exchange.get_stats()


@router.get("/stats")
async def public_stats():
    """Stats publiques de la marketplace."""
    from core.database import db
    try:
        stats = await db.get_marketplace_stats()
        db_stats = await db.get_stats()
        return {**stats, "volume_24h": db_stats.get("volume_24h", 0), "listing_count": db_stats.get("listing_count", 0)}
    except Exception:
        return {"agents_registered": 0, "services_listed": 0, "total_transactions": 0, "volume_24h": 0}


# ══════════════════════════════════════════
#  CRYPTO SWAP
# ══════════════════════════════════════════

@router.get("/crypto/tokens")
async def list_crypto_tokens():
    """Liste les cryptos disponibles pour le swap. Sans auth."""
    from trading.crypto_swap import list_tokens
    return list_tokens()


@router.get("/crypto/prices")
async def crypto_prices():
    """Prix live des cryptos. Sans auth."""
    from trading.crypto_swap import fetch_prices, _price_cache_ts
    prices = await fetch_prices()
    # Fix #10: Return the actual cache timestamp, not current time
    return {"prices": prices, "updated_at": int(_price_cache_ts or time.time()), "cache_ttl_seconds": 30}


@router.get("/crypto/quote")
async def crypto_quote(request: Request, from_token: str, to_token: str, amount: float, volume_30d: float = 0, wallet: str = ""):
    """Devis de swap avec commission MAXIA. Sans auth.
    Si wallet fourni, le volume 30 jours est calcule automatiquement pour le tier."""
    from trading.crypto_swap import get_swap_quote
    # Get user 30-day swap volume and swap count if wallet provided
    user_volume = volume_30d
    swap_count = -1
    if wallet:
        try:
            from core.database import db
            if user_volume <= 0:
                user_volume = await db.get_swap_volume_30d(wallet)
            swap_count = await db.get_swap_count(wallet)
        except Exception:
            user_volume = 0
            swap_count = -1
    result = await get_swap_quote(from_token, to_token, amount, user_volume, swap_count)
    # Fix #7: Add cache-control info to quote response
    if isinstance(result, dict) and "error" not in result:
        result["cache_ttl_seconds"] = 30
        result["note"] = "Quote valid for 30 seconds"
    return result


@router.get("/crypto/swap-quote")
async def crypto_swap_quote(request: Request, from_token: str, to_token: str, amount: float, volume_30d: float = 0, wallet: str = ""):
    """Alias de /crypto/quote pour compatibilite."""
    return await crypto_quote(request, from_token, to_token, amount, volume_30d, wallet)


@router.post("/crypto/swap")
async def crypto_swap(request: Request, req: dict, x_api_key: str = Header(None, alias="X-API-Key")):
    """Executer un swap crypto. Requiert API key."""
    if not x_api_key:
        raise HTTPException(401, "Header X-API-Key requis")
    client_ip = request.client.host if request.client else ""
    agent = _get_agent(x_api_key, client_ip=client_ip)
    _check_rate(x_api_key)

    # Agent permissions — scope + spend
    try:
        from agents.agent_permissions import check_agent_scope, check_agent_spend
        wallet = agent.get("wallet", "")
        await check_agent_scope(x_api_key, wallet, "swap:execute")
        amount_usd = float(req.get("amount", 0))
        if amount_usd > 0:
            await check_agent_spend(x_api_key, wallet, amount_usd)
    except HTTPException:
        raise
    except Exception:
        pass

    # #24: NaN/Infinity validation
    import math
    try:
        amount = float(req.get("amount", 0))
    except (TypeError, ValueError):
        return {"success": False, "error": "Invalid amount"}
    if math.isnan(amount) or math.isinf(amount):
        return {"success": False, "error": "Invalid amount"}

    # Fix #1: Volume-based rate limiting for swaps ($50,000/day)
    from trading.crypto_swap import fetch_prices, SUPPORTED_TOKENS
    from_token_upper = req.get("from_token", "").upper()
    prices = await fetch_prices()
    from_price = prices.get(from_token_upper, {}).get("price", 0)
    value_usd = amount * from_price if from_price > 0 else amount
    today_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    daily_vol_key = f"vol:{x_api_key}:{today_utc}"
    current_vol = _rate_limits.get(daily_vol_key, 0)
    if current_vol + value_usd > 50000:
        return {"success": False, "error": "Daily volume limit reached ($50,000)"}

    # Get real 30-day swap volume and swap count from DB
    swap_volume_30d = 0
    swap_count = -1
    try:
        from core.database import db
        swap_volume_30d = await db.get_swap_volume_30d(agent["wallet"])
        swap_count = await db.get_swap_count(agent["wallet"])
    except Exception:
        swap_volume_30d = agent.get("volume_30d", 0)
        swap_count = -1

    from trading.crypto_swap import execute_swap
    result = await execute_swap(
        buyer_api_key=x_api_key,
        buyer_name=agent["name"],
        buyer_wallet=agent["wallet"],
        from_token=req.get("from_token", ""),
        to_token=req.get("to_token", ""),
        amount=amount,
        buyer_volume_30d=swap_volume_30d,
        payment_tx=req.get("payment_tx", ""),
        swap_count=swap_count,
    )

    if result.get("success"):
        # Fix #1: Track daily volume after successful swap
        _rate_limits[daily_vol_key] = current_vol + value_usd
        async with _agent_update_lock:
            # Fix #3: Division by zero guard + Fix #19: Use dynamic commission calculation
            bps = result.get("commission_bps") or get_commission_bps(value_usd)
            bps = bps or 15  # Absolute fallback to prevent zero
            agent["volume_30d"] += result.get("commission_usd", 0) / (bps / 10000)
            agent["total_spent"] += result.get("commission_usd", 0)

    return result


@router.get("/crypto/compare-fees")
async def crypto_compare_fees(volume_30d: float = 0):
    """Compare les frais MAXIA vs concurrence. Sans auth."""
    from trading.crypto_swap import compare_fees
    return compare_fees(volume_30d)


@router.post("/crypto/log-swap")
async def crypto_log_swap(req: dict):
    """Log a frontend swap (Jupiter/Phantom) — no API key needed.
    Called fire-and-forget after on-chain swap succeeds."""
    from core.database import db
    tx_sig = (req.get("tx_signature") or "").strip()
    if not tx_sig or len(tx_sig) < 20:
        raise HTTPException(400, "tx_signature required")
    # Prevent duplicates
    if await db.tx_already_processed(tx_sig):
        return {"status": "already_logged"}
    wallet = (req.get("wallet") or "").strip()
    from_token = (req.get("from_token") or "").upper()
    to_token = (req.get("to_token") or "").upper()
    amount = float(req.get("amount") or 0)
    # Estimate USD value
    try:
        from trading.crypto_swap import fetch_prices
        prices = await fetch_prices()
        price = prices.get(from_token, {}).get("price", 0)
        value_usd = amount * price if price > 0 else amount
    except Exception:
        value_usd = amount
    # Record in transactions table — commission only (not volume)
    swap_commission = round(value_usd * 0.001, 6)
    await db.record_transaction(wallet, tx_sig, swap_commission, "crypto_swap")
    # Record in crypto_swaps table
    import uuid
    await db.save_swap({
        "swap_id": str(uuid.uuid4())[:12],
        "buyer_wallet": wallet,
        "from_token": from_token,
        "to_token": to_token,
        "amount_in": amount,
        "amount_out": float(req.get("output_amount") or 0),
        "commission": round(value_usd * 0.001, 6),
        "payment_tx": tx_sig,
        "jupiter_tx": tx_sig,
        "status": "completed",
    })
    return {"status": "logged", "value_usd": round(value_usd, 4)}


@router.get("/crypto/stats")
async def crypto_stats():
    """Stats des swaps. Sans auth."""
    from trading.crypto_swap import get_swap_stats
    return await get_swap_stats()


# ══════════════════════════════════════════
#  ALIAS ROUTES — Standard paths (coherence API)
# ══════════════════════════════════════════

@router.get("/crypto/swap/quote")
async def swap_quote_alias(request: Request, from_token: str = "", to_token: str = "",
                           amount: float = 1, volume_30d: float = 0, wallet: str = ""):
    """Alias /crypto/swap/quote → /crypto/swap-quote pour compatibilite."""
    ft = from_token or request.query_params.get("from", "")
    tt = to_token or request.query_params.get("to", "")
    return await crypto_swap_quote(request, ft, tt, amount, volume_30d, wallet)


@router.get("/crypto/swap/supported")
async def swap_supported_alias():
    """Liste des tokens et paires supportes pour le swap."""
    from trading.crypto_swap import SUPPORTED_TOKENS
    tokens = sorted(SUPPORTED_TOKENS.keys()) if hasattr(SUPPORTED_TOKENS, 'keys') else []
    return {"tokens": tokens, "total": len(tokens), "chains": ["solana", "base", "ethereum", "polygon", "arbitrum", "avalanche", "bnb"]}


@router.get("/defi/protocols")
async def defi_protocols_alias():
    """Liste des protocoles DeFi disponibles."""
    return {"protocols": [
        {"name": "Marinade", "chain": "solana", "type": "liquid_staking", "asset": "SOL"},
        {"name": "Jito", "chain": "solana", "type": "liquid_staking", "asset": "SOL"},
        {"name": "BlazeStake", "chain": "solana", "type": "liquid_staking", "asset": "SOL"},
        {"name": "Kamino", "chain": "solana", "type": "lending", "asset": "USDC"},
        {"name": "Aave V3", "chain": "ethereum", "type": "lending", "asset": "USDC"},
        {"name": "Compound V3", "chain": "ethereum", "type": "lending", "asset": "USDC"},
        {"name": "Orca", "chain": "solana", "type": "lp", "asset": "SOL/USDC"},
        {"name": "Raydium", "chain": "solana", "type": "lp", "asset": "SOL/USDC"},
    ], "total": 8, "note": "Use /defi/best-yield for live APY rates"}


@router.get("/gpu/status")
async def gpu_status_alias():
    """Statut des GPUs disponibles."""
    try:
        from gpu.akash_client import get_availability
        return await get_availability()
    except Exception:
        return {"status": "available", "provider": "akash", "note": "Use /gpu/tiers for pricing"}


@router.get("/ai/models")
async def ai_models_alias():
    """Modeles AI disponibles sur MAXIA."""
    return {"models": [
        {"name": "Groq Llama 3.3 70B", "type": "llm", "speed": "fast", "price_per_1k": 0.0},
        {"name": "Mistral Small", "type": "llm", "speed": "medium", "price_per_1k": 0.001},
        {"name": "Claude Sonnet", "type": "llm", "speed": "quality", "price_per_1k": 0.003},
        {"name": "Pollinations.ai", "type": "image", "speed": "fast", "price_per_1k": 0.0},
    ], "fallback_chain": "Groq → Mistral → Claude", "total": 4}


# ══════════════════════════════════════════
#  WEB SCRAPER (Art.25)
# ══════════════════════════════════════════

