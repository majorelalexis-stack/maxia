"""MAXIA Akash Client — GPU rental via Akash Network (decentralise).

Akash = marketplace GPU decentralise. Les providers enchérissent pour servir les workloads.
Avantages : moins cher que RunPod/AWS (H100 ~$1.33/h vs $3.93 AWS), paiement USDC/AKT.

Flow :
  1. Creer un deployment (SDL manifest avec GPU specs)
  2. Attendre les bids des providers
  3. Accepter le bid le moins cher (sous un plafond)
  4. Le provider provisionne le GPU
  5. Monitor + terminate quand expire

Utilise l'Akash Console API (api.cloudmos.io) — pas besoin de CLI.
"""
import asyncio
import logging
import os
import time
import httpx
from error_utils import safe_error
from http_client import get_http_client

log = logging.getLogger("akash")

# Akash Console API (Cloudmos/Akash Console backend)
AKASH_API_URL = os.getenv("AKASH_API_URL", "https://console-api.akash.network")
AKASH_API_KEY = os.getenv("AKASH_API_KEY", "")
AKASH_WALLET = os.getenv("AKASH_WALLET", "")

# Mapping MAXIA tier -> Akash GPU model + specs
AKASH_GPU_MAP = {
    "rtx3090":     {"model": "rtx3090",     "vram": 24,  "cpu": 8,  "ram": 16,  "disk": 50},
    "rtx4090":     {"model": "rtx4090",     "vram": 24,  "cpu": 8,  "ram": 16,  "disk": 50},
    "rtx5090":     {"model": "rtx5090",     "vram": 32,  "cpu": 8,  "ram": 32,  "disk": 100},
    "a6000":       {"model": "a6000",       "vram": 48,  "cpu": 8,  "ram": 32,  "disk": 100},
    "l4":          {"model": "t4",          "vram": 16,  "cpu": 4,  "ram": 16,  "disk": 50},
    "l40s":        {"model": "l40s",        "vram": 48,  "cpu": 8,  "ram": 32,  "disk": 100},
    "rtx_pro6000": {"model": "pro6000se",   "vram": 96,  "cpu": 8,  "ram": 64,  "disk": 200},
    "a100_80":     {"model": "a100",        "vram": 80,  "cpu": 16, "ram": 64,  "disk": 200},
    "h100_sxm":    {"model": "h100",        "vram": 80,  "cpu": 16, "ram": 64,  "disk": 200},
    "h100_nvl":    {"model": "h100",        "vram": 94,  "cpu": 16, "ram": 64,  "disk": 200},
    "h200":        {"model": "h200",        "vram": 141, "cpu": 16, "ram": 128, "disk": 200},
    "b200":        {"model": "h200",        "vram": 180, "cpu": 32, "ram": 256, "disk": 400},
    "4xa100":      {"model": "a100",        "vram": 320, "cpu": 64, "ram": 256, "disk": 400},
}

# Prix plafond par tier — on n'accepte pas de bid au-dessus
AKASH_MAX_PRICE = {
    "rtx3090":     0.25,
    "rtx4090":     0.50,
    "rtx5090":     0.80,
    "a6000":       0.40,
    "l4":          0.50,
    "l40s":        0.85,
    "rtx_pro6000": 1.80,
    "a100_80":     1.30,
    "h100_sxm":    2.80,
    "h100_nvl":    2.70,
    "h200":        3.80,
    "b200":        6.00,
    "4xa100":      5.00,
}

# Deployments actifs
_active_deployments: dict = {}

print(f"[Akash] Client initialise — API key {'present' if AKASH_API_KEY else 'ABSENTE'}")


def _generate_sdl(tier_id: str, duration_hours: float) -> dict:
    """Genere un SDL (Stack Definition Language) pour Akash deployment."""
    specs = AKASH_GPU_MAP.get(tier_id)
    if not specs:
        return {}
    return {
        "version": "2.0",
        "services": {
            "gpu-worker": {
                "image": "nvidia/cuda:12.4.0-runtime-ubuntu22.04",
                "expose": [
                    {"port": 22, "as": 22, "to": [{"global": True}]},     # SSH
                    {"port": 8888, "as": 8888, "to": [{"global": True}]},  # Jupyter
                ],
            }
        },
        "profiles": {
            "compute": {
                "gpu-worker": {
                    "resources": {
                        "cpu": {"units": specs["cpu"]},
                        "memory": {"size": f"{specs['ram']}Gi"},
                        "storage": [{"size": f"{specs['disk']}Gi"}],
                        "gpu": {
                            "units": 1,
                            "attributes": {"vendor": {"nvidia": [{"model": specs["model"]}]}},
                        },
                    }
                }
            },
            "placement": {
                "global": {
                    "pricing": {
                        "gpu-worker": {
                            "denom": "uusd",
                            "amount": int(AKASH_MAX_PRICE.get(tier_id, 1.0) * 1_000_000),
                        }
                    }
                }
            }
        },
        "deployment": {
            "gpu-worker": {"global": {"profile": "gpu-worker", "count": 1}}
        },
    }


class AkashClient:
    """Client pour Akash Network GPU marketplace."""

    def __init__(self):
        self.api_url = AKASH_API_URL
        self.api_key = AKASH_API_KEY
        self.wallet = AKASH_WALLET
        self._monitor_started = False

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
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
        """Verifie si un tier est disponible sur Akash."""
        return tier_id in AKASH_GPU_MAP and self.api_key

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
        """Estime le prix/heure pour un tier sur Akash (basee sur les bids recents)."""
        if tier_id not in AKASH_GPU_MAP:
            return None
        specs = AKASH_GPU_MAP[tier_id]
        try:
            result = await self._request("GET", f"/v1/pricing/gpu/{specs['model']}")
            if "error" not in result:
                # Retourner le prix median des bids recents
                prices = result.get("prices", [])
                if prices:
                    median = sorted(prices)[len(prices) // 2]
                    return round(median, 4)
        except Exception:
            pass
        # Fallback : retourner le prix plafond avec 20% de reduction typique
        return round(AKASH_MAX_PRICE.get(tier_id, 1.0) * 0.8, 4)

    async def rent_gpu(self, tier_id: str, duration_hours: float) -> dict:
        """Loue un GPU sur Akash. Cree un deployment + accepte le meilleur bid."""
        if not self.is_available(tier_id):
            return {"success": False, "error": f"Tier {tier_id} non disponible sur Akash"}

        sdl = _generate_sdl(tier_id, duration_hours)
        if not sdl:
            return {"success": False, "error": f"SDL generation failed for {tier_id}"}

        max_price = AKASH_MAX_PRICE.get(tier_id, 1.0)

        # 1. Creer le deployment
        log.info(f"[Akash] Creating deployment: {tier_id} for {duration_hours}h (max ${max_price}/h)")
        create_resp = await self._request("POST", "/v1/deployments", {
            "sdl": sdl,
            "deposit": int(max_price * duration_hours * 1_000_000),  # uUSD
        })
        if "error" in create_resp:
            return {"success": False, "error": f"Deployment creation failed: {create_resp['error']}"}

        deployment_id = create_resp.get("dseq") or create_resp.get("deploymentId", "")
        if not deployment_id:
            return {"success": False, "error": "No deployment ID returned"}

        # 2. Attendre les bids (max 90s)
        log.info(f"[Akash] Deployment {deployment_id} created, waiting for bids...")
        best_bid = None
        for _ in range(18):  # 18 x 5s = 90s
            await asyncio.sleep(5)
            bids_resp = await self._request("GET", f"/v1/deployments/{deployment_id}/bids")
            if "error" in bids_resp:
                continue
            bids = bids_resp.get("bids", [])
            if not bids:
                continue
            # Filtrer les bids sous le plafond et prendre le moins cher
            valid_bids = [b for b in bids if b.get("price", 999) <= max_price]
            if valid_bids:
                best_bid = min(valid_bids, key=lambda b: b.get("price", 999))
                break

        if not best_bid:
            # Pas de bid acceptable — fermer le deployment
            await self._request("DELETE", f"/v1/deployments/{deployment_id}")
            return {"success": False, "error": f"No bids under ${max_price}/h after 90s"}

        # 3. Accepter le bid
        bid_id = best_bid.get("bidId") or best_bid.get("id", "")
        provider = best_bid.get("provider", "")
        price = best_bid.get("price", max_price)
        log.info(f"[Akash] Accepting bid {bid_id} from {provider} at ${price}/h")

        accept_resp = await self._request("POST", f"/v1/deployments/{deployment_id}/accept", {
            "bidId": bid_id,
            "provider": provider,
        })
        if "error" in accept_resp:
            return {"success": False, "error": f"Bid accept failed: {accept_resp['error']}"}

        # 4. Attendre que le lease soit actif (max 60s)
        lease_info = {}
        for _ in range(12):
            await asyncio.sleep(5)
            status = await self._request("GET", f"/v1/deployments/{deployment_id}/status")
            if status.get("state") in ("active", "running"):
                lease_info = status
                break

        # Extraire les endpoints
        services = lease_info.get("services", {})
        forwarded = lease_info.get("forwarded_ports", {})
        ssh_endpoint = ""
        jupyter_url = ""
        for svc_name, svc_info in (forwarded if isinstance(forwarded, dict) else {}).items():
            for port_info in (svc_info if isinstance(svc_info, list) else [svc_info]):
                ext_port = port_info.get("externalPort", 0)
                host = port_info.get("host", "")
                if port_info.get("port") == 22 and host:
                    ssh_endpoint = f"ssh root@{host} -p {ext_port}"
                if port_info.get("port") == 8888 and host:
                    jupyter_url = f"http://{host}:{ext_port}"

        # 5. Enregistrer le deployment actif
        instance_id = f"akash_{deployment_id}"
        now = time.time()
        _active_deployments[instance_id] = {
            "deployment_id": deployment_id,
            "provider": provider,
            "tier": tier_id,
            "price_per_hour": price,
            "start_time": now,
            "hours": duration_hours,
            "scheduled_end": now + duration_hours * 3600,
        }

        # Demarrer le monitor si pas deja fait
        self._ensure_monitor_started()

        return {
            "success": True,
            "instanceId": instance_id,
            "gpu": tier_id,
            "provider": "akash",
            "akash_deployment_id": deployment_id,
            "akash_provider": provider,
            "status": "running",
            "ssh_endpoint": ssh_endpoint,
            "jupyter_url": jupyter_url,
            "cost_per_hr": price,
            "total_estimated": round(price * duration_hours, 2),
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

        # Fermer le deployment sur Akash (libere l'escrow restant)
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
