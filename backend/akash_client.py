"""MAXIA Akash Client — GPU rental via Akash Network (decentralise).

Akash = marketplace GPU decentralise. Les providers enchérissent pour servir les workloads.
Avantages : moins cher que RunPod/AWS (H100 ~$1.33/h vs $3.93 AWS), paiement USDC/AKT.

Flow (Console API — $120 USD credits):
  1. POST /v1/deployments with SDL + deposit (Console handles on-chain tx)
  2. Poll GET /v1/deployments/{dseq} until leases[] is populated
  3. Console auto-selects provider, creates lease, sends manifest
  4. Return endpoints (SSH/Jupyter) to user
  5. DELETE /v1/deployments/{dseq} to terminate

Console API for everything — no CLI needed for deployments.
"""
import asyncio
import logging
import os
import time
import httpx
from error_utils import safe_error
from http_client import get_http_client

log = logging.getLogger("akash")

# Akash Console API
AKASH_API_URL = os.getenv("AKASH_API_URL", "https://console-api.akash.network")
AKASH_API_KEY = os.getenv("AKASH_API_KEY", "")

# Mapping MAXIA tier -> Akash GPU model + specs
# Models must match Akash network names exactly (from /v1/gpu endpoint)
AKASH_GPU_MAP = {
    "rtx3090":     {"model": "rtx3090ti",   "vram": 24,  "cpu": 8,  "ram": 16,  "disk": 50},
    "rtx4090":     {"model": "rtx4090",     "vram": 24,  "cpu": 8,  "ram": 16,  "disk": 50},
    "rtx_pro6000": {"model": "pro6000se",   "vram": 96,  "cpu": 8,  "ram": 64,  "disk": 200},
    "a100_80":     {"model": "a100",        "vram": 80,  "cpu": 16, "ram": 64,  "disk": 200},
    "h100_sxm":    {"model": "h100",        "vram": 80,  "cpu": 16, "ram": 64,  "disk": 200},
    "h200":        {"model": "h200",        "vram": 141, "cpu": 16, "ram": 128, "disk": 200},
}

# Prix plafond par tier — on n'accepte pas de bid au-dessus
AKASH_MAX_PRICE = {
    "rtx3090":     0.25,
    "rtx4090":     0.50,
    "rtx_pro6000": 1.80,
    "a100_80":     1.50,
    "h100_sxm":    3.50,
    "h200":        4.50,
}

# Deployments actifs
_active_deployments: dict = {}

log.info(f"[Akash] Client initialise — API key {'present' if AKASH_API_KEY else 'ABSENTE'}")


def _generate_sdl(tier_id: str) -> str:
    """Genere un SDL pour Akash deployment. YAML string, pricing en uusd (Console credits)."""
    specs = AKASH_GPU_MAP.get(tier_id)
    if not specs:
        return ""
    max_price_uusd = int(AKASH_MAX_PRICE.get(tier_id, 1.0) * 1_000_000)
    return f"""---
version: "2.0"
services:
  gpu-worker:
    image: nvidia/cuda:12.4.0-runtime-ubuntu22.04
    expose:
      - port: 22
        as: 22
        to:
          - global: true
      - port: 8888
        as: 8888
        to:
          - global: true
profiles:
  compute:
    gpu-worker:
      resources:
        cpu:
          units: {specs['cpu']}
        memory:
          size: {specs['ram']}Gi
        storage:
          - size: {specs['disk']}Gi
        gpu:
          units: 1
          attributes:
            vendor:
              nvidia:
                - model: {specs['model']}
  placement:
    dcloud:
      pricing:
        gpu-worker:
          denom: uusd
          amount: {max_price_uusd}
deployment:
  gpu-worker:
    dcloud:
      profile: gpu-worker
      count: 1
"""


class AkashClient:
    """Client pour Akash Network GPU marketplace."""

    def __init__(self):
        self.api_url = AKASH_API_URL
        self.api_key = AKASH_API_KEY
        self._monitor_started = False

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["x-api-key"] = self.api_key
        return h

    async def _request(self, method: str, path: str, json_data: dict = None) -> dict:
        """HTTP request vers Akash Console API."""
        url = f"{self.api_url}{path}"
        try:
            client = get_http_client()
            resp = await client.request(method, url, json=json_data, headers=self._headers(), timeout=30)
            if resp.status_code >= 400:
                log.error(f"[Akash] {method} {path} → {resp.status_code}: {resp.text[:200]}")
                return {"error": resp.text[:200], "status": resp.status_code}
            return resp.json()
        except Exception as e:
            return safe_error(e, "akash_request")

    def is_available(self, tier_id: str) -> bool:
        """Verifie si un tier est dans le mapping."""
        return tier_id in AKASH_GPU_MAP

    # Cache dispo GPU (refresh toutes les 5 min)
    _gpu_avail_cache: dict = {}
    _gpu_avail_ts: float = 0

    async def get_gpu_availability(self) -> dict:
        """Recupere la disponibilite GPU sur le reseau Akash avec cache 5 min."""
        now = time.time()
        if AkashClient._gpu_avail_cache and now - AkashClient._gpu_avail_ts < 300:
            return AkashClient._gpu_avail_cache

        result = await self._request("GET", "/v1/gpu")
        if "error" in result:
            return {"available": False, "error": result["error"]}

        # Parser les GPU dispo par modele
        avail = {}
        details = result.get("gpus", result).get("details", {}).get("nvidia", [])
        for gpu in details:
            model = (gpu.get("model") or "").lower()
            free = gpu.get("allocatable", 0) - gpu.get("allocated", 0)
            avail[model] = max(0, free)

        AkashClient._gpu_avail_cache = {"available": True, "models": avail}
        AkashClient._gpu_avail_ts = now
        return AkashClient._gpu_avail_cache

    async def check_tier_available(self, tier_id: str) -> bool:
        """Verifie si un tier specifique a des GPU dispo sur Akash."""
        if tier_id not in AKASH_GPU_MAP:
            return False
        specs = AKASH_GPU_MAP[tier_id]
        model = specs["model"].lower()
        avail = await self.get_gpu_availability()
        models = avail.get("models", {})
        return models.get(model, 0) > 0

    async def get_price_estimate(self, tier_id: str) -> float | None:
        """Prix/heure pour un tier sur Akash — basé sur le plafond avec réduction typique réseau."""
        if tier_id not in AKASH_GPU_MAP:
            return None
        return round(AKASH_MAX_PRICE.get(tier_id, 1.0) * 0.8, 4)

    async def rent_gpu(self, tier_id: str, duration_hours: float) -> dict:
        """Loue un GPU via Console API. Create → poll bids → create lease + manifest → endpoints."""
        if not self.is_available(tier_id):
            return {"success": False, "error": f"Tier {tier_id} non disponible sur Akash"}

        sdl = _generate_sdl(tier_id)
        if not sdl:
            return {"success": False, "error": f"SDL generation failed for {tier_id}"}

        max_price = AKASH_MAX_PRICE.get(tier_id, 1.0)
        deposit = round(max(5.0, max_price * duration_hours * 1.5), 2)

        # 1. Create deployment
        log.info(f"[Akash] Creating deployment: {tier_id} for {duration_hours}h (deposit ${deposit})")
        create_resp = await self._request("POST", "/v1/deployments", {
            "data": {"sdl": sdl, "deposit": deposit}
        })
        if "error" in create_resp:
            return {"success": False, "error": f"Create failed: {create_resp['error']}"}

        resp_data = create_resp.get("data", create_resp)
        dseq = resp_data.get("dseq", "")
        manifest = resp_data.get("manifest", "")
        if not dseq:
            return {"success": False, "error": "No deployment ID returned"}

        log.info(f"[Akash] Deployment {dseq} created (manifest={'yes' if manifest else 'no'})")

        # 2. Poll bids via GET /v1/bids/{dseq}
        best_bid = None
        for i in range(24):  # 24 x 5s = 2 min
            await asyncio.sleep(5)
            bids_resp = await self._request("GET", f"/v1/bids/{dseq}")
            bids = bids_resp.get("data", bids_resp)
            if isinstance(bids, list) and bids:
                # Pick cheapest bid
                best_bid = min(bids, key=lambda b: float(
                    b.get("bid", {}).get("price", {}).get("amount", "999999999")
                ))
                bid_id = best_bid.get("bid", {}).get("bid_id", {})
                log.info(f"[Akash] Bid received from {bid_id.get('provider', '?')} after {(i+1)*5}s")
                break
            if i % 4 == 3:
                log.info(f"[Akash] Waiting for bids... ({(i+1)*5}s)")

        if not best_bid:
            await self._request("DELETE", f"/v1/deployments/{dseq}")
            return {"success": False, "error": "No bids after 2 min — deployment closed"}

        # 3. Create lease + send manifest via POST /v1/leases
        bid_id = best_bid.get("bid", {}).get("bid_id", {})
        provider = bid_id.get("provider", "")
        gseq = bid_id.get("gseq", 1)
        oseq = bid_id.get("oseq", 1)

        log.info(f"[Akash] Creating lease: provider={provider}, gseq={gseq}, oseq={oseq}")
        lease_resp = await self._request("POST", "/v1/leases", {
            "manifest": manifest,
            "leases": [{"dseq": str(dseq), "gseq": gseq, "oseq": oseq, "provider": provider}],
        })
        if "error" in lease_resp:
            await self._request("DELETE", f"/v1/deployments/{dseq}")
            return {"success": False, "error": f"Lease failed: {lease_resp['error']}"}

        log.info(f"[Akash] Lease created + manifest sent!")

        # 4. Poll for forwarded ports (container startup ~10-30s)
        ssh_endpoint = ""
        jupyter_url = ""
        for attempt in range(12):  # 12 x 5s = 60s
            await asyncio.sleep(5)
            dep = await self._request("GET", f"/v1/deployments/{dseq}")
            dep_data = dep.get("data", dep)
            for lease in dep_data.get("leases", []):
                fwd = lease.get("forwarded_ports", lease.get("forwardedPorts", []))
                for fp in (fwd if isinstance(fwd, list) else []):
                    host = fp.get("host", "")
                    ext_port = fp.get("externalPort", 0)
                    port = fp.get("port", 0)
                    if port == 22 and host:
                        ssh_endpoint = f"ssh root@{host} -p {ext_port}"
                    if port == 8888 and host:
                        jupyter_url = f"http://{host}:{ext_port}"
            if ssh_endpoint or jupyter_url:
                break

        # 5. Register active deployment
        instance_id = f"akash_{dseq}"
        now = time.time()
        _active_deployments[instance_id] = {
            "deployment_id": dseq,
            "provider": provider,
            "tier": tier_id,
            "price_per_hour": max_price,
            "start_time": now,
            "hours": duration_hours,
            "scheduled_end": now + duration_hours * 3600,
        }
        self._ensure_monitor_started()

        return {
            "success": True,
            "instanceId": instance_id,
            "gpu": tier_id,
            "provider": "akash",
            "akash_deployment_id": dseq,
            "akash_provider": provider,
            "status": "running",
            "ssh_endpoint": ssh_endpoint,
            "jupyter_url": jupyter_url,
            "cost_per_hr": max_price,
            "total_estimated": round(max_price * duration_hours, 2),
            "auto_terminate_at": int(now + duration_hours * 3600),
        }

    async def get_deployment_status(self, instance_id: str) -> dict:
        """Statut d'un deployment Akash."""
        info = _active_deployments.get(instance_id)
        if not info:
            return {"status": "unknown", "error": "Deployment not found in active list"}

        deployment_id = info["deployment_id"]
        status_resp = await self._request("GET", f"/v1/deployments/{deployment_id}/status")
        if "error" in status_resp:
            return {"status": "error", "error": status_resp["error"]}

        elapsed = time.time() - info["start_time"]
        remaining = max(0, info["hours"] - elapsed / 3600)
        return {
            "status": status_resp.get("state", "unknown"),
            "uptime_hours": round(elapsed / 3600, 2),
            "remaining_hours": round(remaining, 2),
            "cost_per_hr": info["price_per_hour"],
            "cost_so_far": round(info["price_per_hour"] * elapsed / 3600, 2),
            "provider": "akash",
            "akash_provider": info.get("provider", ""),
        }

    async def terminate_deployment(self, instance_id: str) -> dict:
        """Termine un deployment Akash et ferme l'escrow."""
        info = _active_deployments.pop(instance_id, None)
        if not info:
            return {"success": False, "error": "Deployment not found"}

        deployment_id = info["deployment_id"]
        elapsed = time.time() - info["start_time"]
        actual_hours = round(elapsed / 3600, 2)
        actual_cost = round(info["price_per_hour"] * actual_hours, 2)

        # Fermer le deployment via Console API
        resp = await self._request("DELETE", f"/v1/deployments/{deployment_id}")
        success = "error" not in resp

        log.info(f"[Akash] Terminated {instance_id}: {actual_hours}h, ${actual_cost}")
        return {
            "success": success,
            "actual_hours": actual_hours,
            "actual_cost": actual_cost,
            "refunded": success,
        }

    def _ensure_monitor_started(self):
        if not self._monitor_started:
            self._monitor_started = True
            asyncio.ensure_future(self._monitor_deployments())
            log.info("[Akash] Deployment monitor started")

    async def _monitor_deployments(self):
        """Monitor en boucle : termine les deployments expires."""
        while True:
            await asyncio.sleep(60)
            now = time.time()
            expired = [k for k, v in _active_deployments.items() if now >= v["scheduled_end"]]
            for instance_id in expired:
                log.info(f"[Akash] Auto-terminating expired deployment: {instance_id}")
                try:
                    await self.terminate_deployment(instance_id)
                except Exception as e:
                    log.error(f"[Akash] Auto-terminate error {instance_id}: {e}")


# Singleton
akash = AkashClient()
