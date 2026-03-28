"""MAXIA GPU routes — extracted from main.py."""
import os
import time
import uuid
import json

from fastapi import APIRouter, HTTPException, Depends, Request
from error_utils import safe_error

router = APIRouter(tags=["gpu"])

# ── Deferred imports (resolved at call time to avoid circular deps) ──

def _get_db():
    from database import db
    return db

def _get_auction_manager():
    from main import auction_manager
    return auction_manager

def _get_runpod():
    from main import runpod
    return runpod

def _get_escrow_client():
    from escrow_client import escrow_client
    return escrow_client

# ── Config imports (safe at module level) ──
from config import GPU_TIERS, AKASH_ENABLED, COMMISSION_TIERS, SERVICE_PRICES, TREASURY_ADDRESS
from runpod_client import RunPodClient, get_gpu_tiers_live, GPU_MAP
from models import (
    AuctionCreateRequest, AuctionSettleRequest,
    GpuRentRequest, GpuRentPublicRequest,
)
from auth import require_auth
from solana_verifier import verify_transaction

try:
    from akash_client import AkashClient, akash as akash_client, AKASH_GPU_MAP, AKASH_MAX_PRICE, _active_deployments
except Exception:
    akash_client = None
    AKASH_GPU_MAP = {}
    AKASH_MAX_PRICE = 10.0
    _active_deployments = {}


# ── Runtime config ──
BROKER_MARGIN      = float(os.getenv("BROKER_MARGIN", "1.00"))
AUCTION_DURATION_S = int(os.getenv("AUCTION_DURATION_S", "30"))

# ═══════════════════════════════════════════════════════════
#  GPU AUCTIONS (Art.5)
# ═══════════════════════════════════════════════════════════

_GPU_MARKUP = 0.15  # 15% markup on Akash cost (still cheaper than RunPod/AWS)

@router.get("/api/gpu/tiers")
async def get_tiers():
    """GPU tiers with live pricing via Akash Network. 15% markup, still cheaper than alternatives."""
    import time as _t
    tiers = []
    for g in GPU_TIERS:
        tier_id = g["id"]
        base_price = g["base_price_per_hour"]

        # Akash price with MAXIA markup
        if AKASH_ENABLED and akash_client and tier_id in AKASH_GPU_MAP:
            akash_cost = await akash_client.get_price_estimate(tier_id)
            if akash_cost:
                sell_price = round(akash_cost * (1 + _GPU_MARKUP), 2)
            else:
                sell_price = round(base_price * 0.85, 2)  # Cheaper than RunPod even as fallback
        else:
            sell_price = base_price

        tier = {
            "id": tier_id, "label": g["label"], "vram_gb": g["vram_gb"],
            "price_per_hour_usdc": sell_price,
            "available": True,
            "source": "live" if AKASH_ENABLED else "fallback",
            "maxia_markup": f"{int(_GPU_MARKUP*100)}%",
            "provider": "akash",
        }
        # Disponibilite live Akash
        if g.get("local"):
            tier["local"] = True
            tier["available"] = False
        tiers.append(tier)

    return {
        "gpu_count": len(tiers),
        "tiers": tiers,
        "provider": "akash",
        "network": "Akash Network (decentralized)",
        "markup": f"{int(_GPU_MARKUP*100)}%",
        "note": "Cheaper than RunPod, AWS, and Lambda Labs",
        "updated_at": _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime()),
    }


@router.get("/api/gpu/auctions/active")
async def get_active_auctions():
    return _get_auction_manager().get_open_auctions()


@router.get("/api/gpu/auctions")
async def get_auctions(status: str = "open"):
    """List GPU auctions. ?status=open returns active auctions."""
    auctions = _get_auction_manager().get_open_auctions()
    if status != "open":
        return []  # Only open auctions available in-memory
    return auctions


@router.post("/api/gpu/auctions")
async def create_auction_rest(req: AuctionCreateRequest, wallet: str = Depends(require_auth)):
    db = _get_db()
    auction_manager = _get_auction_manager()
    gpu = next((g for g in GPU_TIERS if g["id"] == req.gpu_tier_id), None)
    if not gpu:
        raise HTTPException(400, f"GPU inconnu: {req.gpu_tier_id}")
    cost  = gpu["base_price_per_hour"] * req.duration_hours
    start = req.floor_price_usdc or round(cost * BROKER_MARGIN, 4)
    a = {
        "auctionId": str(uuid.uuid4()), "gpuTierId": req.gpu_tier_id,
        "gpuLabel": gpu["label"], "vramGb": gpu["vram_gb"],
        "durationHours": req.duration_hours, "providerCost": round(cost, 4),
        "startPrice": start, "currentBid": start, "currentLeader": None,
        "brokerWallet": wallet,
        "endsAt": int((time.time() + AUCTION_DURATION_S) * 1000),
        "status": "open",
    }
    await auction_manager.open_auction(a)
    await db.save_auction(a)
    return a


@router.post("/api/gpu/auctions/settle")
async def settle_auction(req: AuctionSettleRequest, wallet: str = Depends(require_auth)):
    db = _get_db()
    auction_manager = _get_auction_manager()
    runpod = _get_runpod()
    if wallet != req.winner:
        raise HTTPException(403, "Wallet mismatch: you can only settle auctions you won")
    if await db.tx_already_processed(req.tx_signature):
        raise HTTPException(400, "Transaction deja traitee.")
    auction = await db.get_auction(req.auction_id)
    if not auction:
        raise HTTPException(404, "Enchere introuvable.")
    # Verification complete: montant + destinataire
    tx_result = await verify_transaction(
        tx_signature=req.tx_signature,
        expected_amount_usdc=auction.get("currentBid", 0),
        expected_recipient=TREASURY_ADDRESS,
    )
    if not tx_result.get("valid"):
        raise HTTPException(400, f"Transaction invalide: {tx_result.get('error', 'verification echouee')}")
    instance = await runpod.rent_gpu(auction["gpuTierId"], auction["durationHours"])
    if not instance.get("success"):
        raise HTTPException(502, f"RunPod: {instance.get('error')}")
    await db.record_transaction(req.winner, req.tx_signature, auction["currentBid"], "gpu_auction")
    await db.update_auction(req.auction_id, {
        "status": "provisioned", "txSignature": req.tx_signature,
        "gpuInstanceId": instance["instanceId"], "sshEndpoint": instance.get("ssh_endpoint", ""),
    })
    await auction_manager.broadcast({"type": "GPU_PROVISIONED", "payload": {
        "auctionId": req.auction_id, "winner": req.winner,
        "gpuLabel": auction["gpuLabel"], "sshEndpoint": instance.get("ssh_endpoint", ""),
    }})
    return {"ok": True, "instanceId": instance["instanceId"]}


# ═══════════════════════════════════════════════════════════
#  GPU RENTAL — Direct rent (no auction)
# ═══════════════════════════════════════════════════════════

# Free trial: first 10 minutes free per wallet (RTX 4090 only)
FREE_TRIAL_MINUTES = 10
FREE_TRIAL_GPU = "rtx4090"

# Label -> tier ID mapping (for frontend that sends display names)
_LABEL_TO_TIER = {}
for _tid, _ginfo in GPU_MAP.items():
    _rpid = _ginfo.get("runpod_id")
    if not _rpid:
        continue  # Skip local GPU tier (no runpod_id)
    _label = _rpid.replace("NVIDIA ", "").replace("GeForce ", "")
    _LABEL_TO_TIER[_label.lower()] = _tid
    _LABEL_TO_TIER[_rpid.lower()] = _tid
# Also add explicit known labels
_LABEL_TO_TIER.update({
    "rtx 4090": "rtx4090", "rtx4090": "rtx4090", "geforce rtx 4090": "rtx4090",
    "a100 80gb": "a100_80", "a100 80gb pcie": "a100_80", "a100": "a100_80",
    "h100 sxm5": "h100_sxm5", "h100": "h100_sxm5",
    "rtx a6000": "a6000", "a6000": "a6000",
    "4x a100 80gb": "4xa100", "4xa100": "4xa100",
    "h200 sxm": "h200", "h200": "h200",
    "l40s": "l40s", "rtx 3090": "rtx3090", "rtx3090": "rtx3090",
})


def _resolve_gpu_tier(gpu_input: str) -> str | None:
    """Resolve a GPU label or tier ID to a canonical tier ID."""
    normalized = gpu_input.strip().lower()
    # Direct tier ID match
    if normalized in GPU_MAP:
        return normalized
    # Label lookup
    return _LABEL_TO_TIER.get(normalized)


@router.get("/api/public/gpu/tiers")
async def get_gpu_tiers_public():
    """Live GPU pricing via Akash Network. No auth required."""
    return await get_tiers()


@router.get("/api/public/prices")
async def get_all_prices():
    """All current MAXIA prices — GPU, services, commissions. Updated live from source."""
    from crypto_swap import SWAP_COMMISSION_TIERS
    from tokenized_stocks import STOCK_COMMISSION_TIERS

    # GPU prices from Akash (primary) with markup
    gpu_tiers = GPU_TIERS
    try:
        live = await get_tiers()
        if live and live.get("tiers"):
            gpu_tiers = live["tiers"]
    except Exception:
        pass

    return {
        "gpu_tiers": gpu_tiers,
        "service_prices": SERVICE_PRICES,
        "marketplace_commission_tiers": COMMISSION_TIERS,
        "swap_commission_tiers": SWAP_COMMISSION_TIERS,
        "stock_commission_tiers": STOCK_COMMISSION_TIERS,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


@router.post("/api/gpu/rent")
async def rent_gpu_direct(req: dict, auth_wallet: str = Depends(require_auth)):
    """Rent a GPU. Requires USDC payment verified on-chain.

    Body: { gpu: str (tier ID or label), hours: float, payment_tx: str }
    """
    db = _get_db()
    runpod = _get_runpod()
    gpu_input = req.get("gpu") or req.get("gpu_tier_id", "")
    wallet = auth_wallet  # Use authenticated wallet, not body
    hours = float(req.get("hours", 1))
    payment_tx = req.get("payment_tx")

    if not wallet:
        raise HTTPException(400, "Wallet address required")
    if not payment_tx:
        raise HTTPException(402, "USDC payment required. Send payment to Treasury and include payment_tx.")
    if hours <= 0 or hours > 720:
        raise HTTPException(400, "Hours must be between 0 and 720")

    # Resolve GPU tier
    tier_id = _resolve_gpu_tier(gpu_input)
    if not tier_id:
        raise HTTPException(400, f"Unknown GPU: {gpu_input}. Available: {', '.join(GPU_MAP.keys())}")
    if tier_id not in GPU_MAP:
        raise HTTPException(400, f"GPU tier not available for rental: {tier_id}")

    gpu_config = GPU_MAP[tier_id]
    cost_per_hr = gpu_config.get("base_price_per_hour", 0)
    total_cost = round(cost_per_hr * hours, 4)

    is_free_trial = False
    if True:
        # Verify USDC payment on-chain
        if await db.tx_already_processed(payment_tx):
            raise HTTPException(400, "Transaction already processed.")
        tx_result = await verify_transaction(
            tx_signature=payment_tx,
            expected_amount_usdc=total_cost,
            expected_recipient=TREASURY_ADDRESS,
        )
        if not tx_result.get("valid"):
            raise HTTPException(400, f"Payment invalid: {tx_result.get('error', 'verification failed')}")

    # Provider selection: Akash PRIMARY, RunPod hidden fallback
    provider_name = "akash" if AKASH_ENABLED and akash_client and akash_client.is_available(tier_id) else "runpod"

    # Akash price estimate (overrides RunPod price if available)
    if provider_name == "akash":
        akash_price = await akash_client.get_price_estimate(tier_id)
        if akash_price:
            cost_per_hr = akash_price
            total_cost = round(cost_per_hr * hours, 4)

    # Provision the GPU
    print(f"[GPU Rent] Provisioning {tier_id} for {hours}h via {provider_name} — wallet: {wallet}")
    if provider_name == "akash":
        result = await akash_client.rent_gpu(tier_id, hours)
    else:
        result = await runpod.rent_gpu(tier_id, hours)

    if not result.get("success"):
        # Silent fallback to RunPod if Akash fails
        if provider_name == "akash":
            print(f"[GPU Rent] Akash failed ({result.get('error','')}), silent fallback to RunPod")
            provider_name = "runpod"
            result = await runpod.rent_gpu(tier_id, hours)
        if not result.get("success"):
            raise HTTPException(502, "GPU provisioning failed — no providers available")

    # Record in database
    instance_id = result["instanceId"]
    try:
        await db.save_gpu_instance({
            "instance_id": instance_id,
            "agent_wallet": wallet,
            "agent_name": wallet[:8],
            "gpu_tier": tier_id,
            "duration_hours": hours,
            "price_per_hour": cost_per_hr,
            "total_cost": total_cost,
            "commission": 0,
            "payment_tx": payment_tx,
            "runpod_pod_id": instance_id,
            "status": result.get("status", "provisioning"),
            "ssh_endpoint": result.get("ssh_endpoint", ""),
            "scheduled_end": result.get("auto_terminate_at", 0),
        })
    except Exception as e:
        print(f"[GPU Rent] DB save warning: {e}")

    # Record the transaction (skip for free trial)
    if not is_free_trial:
        try:
            await db.record_transaction(wallet, payment_tx, total_cost, "gpu_rental")
        except Exception as e:
            print(f"[GPU Rent] TX record warning: {e}")

    return {
        "ok": True,
        "instanceId": instance_id,
        "gpu": result.get("gpu", tier_id),
        "gpu_count": result.get("gpu_count", 1),
        "status": result.get("status", "provisioning"),
        "ssh_command": result.get("ssh_command", ""),
        "ssh_endpoint": result.get("ssh_endpoint", ""),
        "jupyter_url": result.get("jupyter_url", ""),
        "api_url": result.get("api_url", ""),
        "cost_per_hr": cost_per_hr,
        "total_cost": total_cost,
        "duration_hours": hours,
        "auto_terminate_at": result.get("auto_terminate_at", 0),
        "is_free_trial": is_free_trial,
        "provider": provider_name,
        "akash_deployment_id": result.get("akash_deployment_id", ""),
        "instructions": result.get("instructions", ""),
    }


@router.post("/api/public/gpu/rent")
async def rent_gpu_public(req: dict, auth_wallet: str = Depends(require_auth)):
    """Public API endpoint for GPU rental (A2A agents). Requires auth like /api/gpu/rent."""
    return await rent_gpu_direct(req, auth_wallet)


@router.get("/api/gpu/status/{pod_id}")
async def get_gpu_status(pod_id: str):
    """Get real-time status of a running GPU pod."""
    runpod = _get_runpod()
    if pod_id.startswith("akash_"):
        return await akash_client.get_deployment_status(pod_id)
    return await runpod.get_pod_status(pod_id)


@router.post("/api/gpu/terminate/{pod_id}")
async def terminate_gpu(pod_id: str, wallet: str = Depends(require_auth)):
    """Terminate a GPU pod early. Only the renter can terminate."""
    db = _get_db()
    runpod = _get_runpod()
    instance = await db.get_gpu_instance(pod_id)
    if not instance:
        raise HTTPException(404, "GPU instance not found")
    if instance.get("agent_wallet") != wallet:
        raise HTTPException(403, "Only the renter can terminate this pod")

    if pod_id.startswith("akash_"):
        result = await akash_client.terminate_deployment(pod_id)
    else:
        result = await runpod.terminate_pod(pod_id)
    if result.get("success"):
        try:
            await db.update_gpu_instance(pod_id, {
                "status": "terminated",
                "actual_end": int(time.time()),
                "actual_cost": result.get("actual_cost", 0),
            })
        except Exception as e:
            print(f"[GPU] DB update warning: {e}")
    return result


@router.get("/api/gpu/active")
async def list_active_gpus():
    """List all currently active GPU pods."""
    runpod = _get_runpod()
    akash_pods = {k: v for k, v in _active_deployments.items()} if AKASH_ENABLED else {}
    # Include RunPod pods silently (fallback only)
    try:
        fallback_pods = await runpod.list_active_pods()
        if isinstance(fallback_pods, dict) and fallback_pods.get("pods"):
            akash_pods.update({p.get("id", k): p for k, p in enumerate(fallback_pods.get("pods", []))})
    except Exception:
        pass
    return {"active_gpus": akash_pods, "provider": "akash", "count": len(akash_pods)}
