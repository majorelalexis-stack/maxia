"""MAXIA RunPod Client V12 — Location GPU complete (production-ready)

Fonctionnalites :
- Creer un pod GPU (provisionnement reel)
- Recuperer les credentials SSH/API
- Verifier le statut du pod
- Arreter le pod apres la duree louee (persistent monitor)
- Estimer le cout final
- Retry logic on terminate
- Database persistence for GPU instances
"""
import asyncio, time, logging
import httpx
from config import RUNPOD_API_KEY

log = logging.getLogger("runpod")

BASE_URL = "https://api.runpod.io/graphql"

# Mapping tier -> RunPod GPU ID (base_price_per_hour = fallback when RunPod returns 0)
GPU_MAP = {
    "rtx4090":   {"runpod_id": "NVIDIA GeForce RTX 4090", "cloud_type": "SECURE", "base_price_per_hour": 0.69},
    "a100_80":   {"runpod_id": "NVIDIA A100 80GB PCIe", "cloud_type": "SECURE", "base_price_per_hour": 1.79},
    "h100_sxm5": {"runpod_id": "NVIDIA H100 SXM5", "cloud_type": "SECURE", "base_price_per_hour": 2.69},
    "a6000":     {"runpod_id": "NVIDIA RTX A6000", "cloud_type": "SECURE", "base_price_per_hour": 0.99},
    "4xa100":    {"runpod_id": "NVIDIA A100 80GB PCIe", "cloud_type": "SECURE", "gpu_count": 4, "base_price_per_hour": 7.16},
}

# Pods actifs (en memoire)
_active_pods: dict = {}  # pod_id -> {buyer, tier, start_time, hours, ...}

print(f"[RunPod] Client initialise — API key {'present' if RUNPOD_API_KEY else 'ABSENTE'}")


class RunPodClient:
    def __init__(self, api_key: str = ""):
        self.api_key = api_key or RUNPOD_API_KEY
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        self._monitor_started = False

    async def _query(self, query: str) -> dict:
        """Execute une requete GraphQL RunPod."""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(BASE_URL, json={"query": query}, headers=self.headers)
                return resp.json()
        except Exception as e:
            return {"errors": [{"message": str(e)}]}

    async def rent_gpu(self, gpu_tier_id: str, duration_hours: float) -> dict:
        """Cree un pod GPU sur RunPod et retourne les credentials."""
        if not self.api_key:
            return {"success": False, "error": "RUNPOD_API_KEY non configuree"}

        gpu_config = GPU_MAP.get(gpu_tier_id)
        if not gpu_config:
            return {"success": False, "error": f"GPU tier inconnu: {gpu_tier_id}"}

        gpu_id = gpu_config["runpod_id"]
        cloud_type = gpu_config.get("cloud_type", "SECURE")
        gpu_count = gpu_config.get("gpu_count", 1)

        # 1. Creer le pod
        query = (
            "mutation { podFindAndDeployOnDemand( input: {"
            f" cloudType: {cloud_type}"
            f" gpuCount: {gpu_count}"
            " volumeInGb: 50"
            " containerDiskInGb: 20"
            " minVcpuCount: 4"
            " minMemoryInGb: 16"
            f' gpuTypeId: "{gpu_id}"'
            ' imageName: "runpod/pytorch:2.1.0-py3.10-cuda11.8.0-devel-ubuntu22.04"'
            ' dockerArgs: ""'
            ' ports: "22/tcp,8888/http"'
            ' volumeMountPath: "/workspace"'
            " }) { id imageName machine { podHostId } desiredStatus costPerHr } }"
        )

        data = await self._query(query)
        errors = data.get("errors")
        if errors:
            err_msg = errors[0].get("message", str(errors))
            print(f"[RunPod] Create error: {err_msg}")
            return {"success": False, "error": err_msg}

        pod = data.get("data", {}).get("podFindAndDeployOnDemand", {})
        if not pod or not pod.get("id"):
            return {"success": False, "error": "RunPod n'a pas retourne de pod"}

        pod_id = pod["id"]
        host_id = pod.get("machine", {}).get("podHostId", "")
        # Fix #10: Fallback if RunPod doesn't return cost
        cost_per_hr = pod.get("costPerHr", 0) or gpu_config.get("base_price_per_hour", 0)

        print(f"[RunPod] Pod cree: {pod_id} ({gpu_id}) — host: {host_id}")

        # 2. Attendre que le pod soit pret (max 60 secondes)
        credentials = await self._wait_for_ready(pod_id, timeout=60)

        # 3. Enregistrer le pod actif avec scheduled_termination
        scheduled_end = int(time.time() + duration_hours * 3600)
        _active_pods[pod_id] = {
            "pod_id": pod_id,
            "gpu_tier": gpu_tier_id,
            "gpu_name": gpu_id,
            "host_id": host_id,
            "start_time": int(time.time()),
            "duration_hours": duration_hours,
            "end_time": scheduled_end,
            "scheduled_termination": scheduled_end,
            "cost_per_hr": cost_per_hr,
            "status": credentials.get("status", "provisioning"),
        }

        # 4. Start persistent monitor (replaces fire-and-forget task)
        self._ensure_monitor_started()

        return {
            "success": True,
            "instanceId": pod_id,
            "gpu": gpu_id,
            "gpu_count": gpu_count,
            "status": credentials.get("status", "provisioning"),
            "ssh_endpoint": credentials.get("ssh", f"ssh root@{host_id}-{pod_id[:8]}.proxy.runpod.net"),
            "ssh_command": credentials.get("ssh_command", f"ssh root@{host_id}-{pod_id[:8]}.proxy.runpod.net -i ~/.ssh/id_ed25519"),
            "jupyter_url": credentials.get("jupyter", f"https://{pod_id}-8888.proxy.runpod.net"),
            "api_url": f"https://{pod_id}-8888.proxy.runpod.net",
            "cost_per_hr": cost_per_hr,
            "duration_hours": duration_hours,
            "auto_terminate_at": int(time.time() + duration_hours * 3600),
            "provider": "runpod",
            "instructions": (
                f"Votre GPU {gpu_id} est pret.\n"
                f"SSH: ssh root@{host_id}-{pod_id[:8]}.proxy.runpod.net\n"
                f"Jupyter: https://{pod_id}-8888.proxy.runpod.net\n"
                f"Le pod sera automatiquement arrete apres {duration_hours}h.\n"
                f"Workspace: /workspace (persistant)"
            ),
        }

    async def _wait_for_ready(self, pod_id: str, timeout: int = 60) -> dict:
        """Attend que le pod soit pret et retourne les credentials."""
        start = time.time()
        while time.time() - start < timeout:
            query = f'{{ pod(input: {{ podId: "{pod_id}" }}) {{ id desiredStatus runtime {{ uptimeInSeconds ports {{ ip isIpPublic privatePort publicPort type }} }} }} }}'
            data = await self._query(query)
            pod = data.get("data", {}).get("pod", {})

            if pod:
                runtime = pod.get("runtime", {})
                ports = runtime.get("ports", [])
                uptime = runtime.get("uptimeInSeconds", 0)

                if uptime and uptime > 0:
                    # Pod est pret
                    ssh_port = ""
                    jupyter_url = ""
                    for port in ports:
                        ip = port.get("ip", "")
                        public_port = port.get("publicPort", "")
                        private_port = port.get("privatePort", 0)
                        if private_port == 22:
                            ssh_port = f"ssh root@{ip} -p {public_port}"
                        elif private_port == 8888:
                            jupyter_url = f"https://{ip}:{public_port}"

                    print(f"[RunPod] Pod {pod_id} pret — uptime: {uptime}s")
                    return {
                        "status": "running",
                        "ssh": ssh_port or f"ssh root@{pod_id}.proxy.runpod.net",
                        "ssh_command": ssh_port or f"ssh root@{pod_id}.proxy.runpod.net",
                        "jupyter": jupyter_url or f"https://{pod_id}-8888.proxy.runpod.net",
                    }

            await asyncio.sleep(5)

        print(f"[RunPod] Pod {pod_id} timeout apres {timeout}s — status: provisioning")
        return {"status": "provisioning"}

    def _ensure_monitor_started(self):
        """Start the persistent pod monitor if not already running."""
        if not self._monitor_started:
            self._monitor_started = True
            asyncio.ensure_future(self._monitor_pods())

    async def _monitor_pods(self):
        """Fix #5: Persistent background loop that checks every 60s for expired pods."""
        log.info("[RunPod] Pod monitor started")
        print("[RunPod] Pod monitor started")
        while True:
            try:
                now = int(time.time())
                for pod_id, pod_info in list(_active_pods.items()):
                    if pod_info.get("status") in ("terminated", "failed"):
                        continue
                    scheduled = pod_info.get("scheduled_termination", 0)
                    if scheduled and now >= scheduled:
                        print(f"[RunPod] Monitor: auto-terminating pod {pod_id} (expired)")
                        result = await self.terminate_pod(pod_id)
                        if not result.get("success"):
                            log.warning(f"[RunPod] Monitor: failed to terminate {pod_id}: {result.get('error')}")
                        else:
                            # Update DB with termination info
                            try:
                                from database import db
                                await db.update_gpu_instance(pod_id, {
                                    "status": "terminated",
                                    "actual_end": int(time.time()),
                                    "actual_cost": result.get("actual_cost", 0),
                                })
                            except Exception as e:
                                log.warning(f"[RunPod] Monitor: DB update failed for {pod_id}: {e}")
            except Exception as e:
                log.error(f"[RunPod] Monitor error: {e}")
            await asyncio.sleep(60)

    async def terminate_pod(self, pod_id: str) -> dict:
        """Arrete et supprime un pod. Fix #5: 3-attempt retry. Fix #6: returns actual_cost."""
        last_error = None
        for attempt in range(3):
            query = f'mutation {{ podTerminate(input: {{ podId: "{pod_id}" }}) }}'
            data = await self._query(query)
            errors = data.get("errors")
            if not errors:
                break
            last_error = errors[0].get("message", str(errors))
            log.warning(f"[RunPod] Terminate attempt {attempt + 1}/3 failed for {pod_id}: {last_error}")
            if attempt < 2:
                await asyncio.sleep(5 * (attempt + 1))
        else:
            # All 3 attempts failed
            log.error(f"[RunPod] Failed to terminate pod {pod_id} after 3 attempts: {last_error}")
            if pod_id in _active_pods:
                _active_pods[pod_id]["status"] = "failed"
            return {"success": False, "error": last_error, "pod_id": pod_id}

        # Calculate actual cost
        actual_cost = 0
        if pod_id in _active_pods:
            pod_info = _active_pods[pod_id]
            elapsed_hours = (time.time() - pod_info["start_time"]) / 3600
            actual_cost = round(elapsed_hours * pod_info.get("cost_per_hr", 0), 4)
            _active_pods[pod_id]["status"] = "terminated"
            _active_pods[pod_id]["actual_hours"] = round(elapsed_hours, 2)
            _active_pods[pod_id]["actual_cost"] = actual_cost
            print(f"[RunPod] Pod {pod_id} termine — {elapsed_hours:.1f}h, cout: ${actual_cost}")

        return {"success": True, "pod_id": pod_id, "status": "terminated", "actual_cost": actual_cost}

    async def get_pod_status(self, pod_id: str) -> dict:
        """Statut d'un pod."""
        query = f'{{ pod(input: {{ podId: "{pod_id}" }}) {{ id desiredStatus runtime {{ uptimeInSeconds gpus {{ id gpuUtilPercent memoryUtilPercent }} }} costPerHr }} }}'
        data = await self._query(query)
        pod = data.get("data", {}).get("pod", {})

        if not pod:
            return {"error": "Pod introuvable"}

        runtime = pod.get("runtime", {})
        gpus = runtime.get("gpus", [])
        uptime = runtime.get("uptimeInSeconds", 0)

        # Infos locales
        local = _active_pods.get(pod_id, {})
        elapsed_hours = (time.time() - local.get("start_time", time.time())) / 3600
        remaining_hours = max(0, local.get("duration_hours", 0) - elapsed_hours)

        return {
            "pod_id": pod_id,
            "status": pod.get("desiredStatus", "unknown"),
            "uptime_seconds": uptime,
            "uptime_hours": round(uptime / 3600, 2) if uptime else 0,
            "gpu_utilization": gpus[0].get("gpuUtilPercent", 0) if gpus else 0,
            "memory_utilization": gpus[0].get("memoryUtilPercent", 0) if gpus else 0,
            "cost_per_hr": pod.get("costPerHr", 0),
            "elapsed_hours": round(elapsed_hours, 2),
            "remaining_hours": round(remaining_hours, 2),
            "auto_terminate": local.get("end_time", 0),
        }

    async def list_active_pods(self) -> dict:
        """Liste les pods actifs."""
        active = [
            p for p in _active_pods.values()
            if p.get("status") != "terminated"
        ]
        return {
            "active_pods": len(active),
            "pods": active,
        }


# Instance globale
runpod_client = RunPodClient()


# ══════════════════════════════════════════
# LIVE GPU PRICING & AVAILABILITY
# ══════════════════════════════════════════

_gpu_price_cache: dict = {}
_gpu_cache_ts: float = 0
_GPU_CACHE_TTL = 300  # 5 minutes

# Extended GPU map with all models
GPU_FULL_MAP = {
    "rtx4090":   {"runpod_id": "NVIDIA GeForce RTX 4090", "vram": 24, "category": "consumer"},
    "a100_80":   {"runpod_id": "NVIDIA A100 80GB PCIe", "vram": 80, "category": "datacenter"},
    "h100_sxm5": {"runpod_id": "NVIDIA H100 SXM5", "vram": 80, "category": "datacenter"},
    "h200":      {"runpod_id": "NVIDIA H200 SXM", "vram": 141, "category": "datacenter"},
    "a6000":     {"runpod_id": "NVIDIA RTX A6000", "vram": 48, "category": "datacenter"},
    "l40s":      {"runpod_id": "NVIDIA L40S", "vram": 48, "category": "datacenter"},
    "rtx3090":   {"runpod_id": "NVIDIA GeForce RTX 3090", "vram": 24, "category": "consumer"},
    "4xa100":    {"runpod_id": "NVIDIA A100 80GB PCIe", "vram": 320, "category": "multi", "gpu_count": 4},
}

# Competitor pricing (manually updated, March 2026)
COMPETITOR_PRICES = {
    "rtx4090": {
        "runpod_community": 0.69, "runpod_secure": 0.74,
        "aws": None, "gcp": None, "lambda": 0.75, "vast_ai": 0.40,
    },
    "a100_80": {
        "runpod_community": 1.64, "runpod_secure": 1.79,
        "aws": 4.10, "gcp": 3.67, "lambda": 1.29, "vast_ai": 0.90,
    },
    "h100_sxm5": {
        "runpod_community": 2.49, "runpod_secure": 2.69,
        "aws": 32.77, "gcp": 12.00, "lambda": 2.49, "vast_ai": 2.20,
    },
    "h200": {
        "runpod_community": 3.99, "runpod_secure": 4.31,
        "aws": None, "gcp": None, "lambda": 3.99, "vast_ai": None,
    },
    "a6000": {
        "runpod_community": 0.79, "runpod_secure": 0.99,
        "aws": None, "gcp": None, "lambda": 0.80, "vast_ai": 0.50,
    },
    "l40s": {
        "runpod_community": 0.99, "runpod_secure": 1.14,
        "aws": 1.84, "gcp": 1.70, "lambda": 0.99, "vast_ai": 0.85,
    },
}


async def fetch_live_gpu_prices() -> dict:
    """Fetch real-time GPU pricing and availability from RunPod GraphQL API."""
    import time as _time
    global _gpu_price_cache, _gpu_cache_ts

    if _time.time() - _gpu_cache_ts < _GPU_CACHE_TTL and _gpu_price_cache:
        return _gpu_price_cache

    if not RUNPOD_API_KEY:
        return {}

    query = """
    query {
        gpuTypes {
            id
            displayName
            memoryInGb
            secureCloud
            communityCloud
            lowestPrice {
                minimumBidPrice
                uninterruptablePrice
            }
        }
    }
    """

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                BASE_URL,
                headers={"Authorization": f"Bearer {RUNPOD_API_KEY}"},
                json={"query": query},
            )
            if r.status_code == 200:
                data = r.json()
                gpu_types = data.get("data", {}).get("gpuTypes", [])
                prices = {}
                for gpu in gpu_types:
                    name = gpu.get("displayName", "")
                    lowest = gpu.get("lowestPrice", {})
                    prices[name] = {
                        "display_name": name,
                        "vram_gb": gpu.get("memoryInGb", 0),
                        "secure_cloud": gpu.get("secureCloud", False),
                        "community_cloud": gpu.get("communityCloud", False),
                        "min_price": lowest.get("minimumBidPrice", 0),
                        "on_demand_price": lowest.get("uninterruptablePrice", 0),
                        "available": gpu.get("secureCloud", False) or gpu.get("communityCloud", False),
                    }
                _gpu_price_cache = prices
                _gpu_cache_ts = _time.time()
                print(f"[RunPod] Live prices fetched: {len(prices)} GPU types")
                return prices
    except Exception as e:
        print(f"[RunPod] Live pricing error: {e}")

    return {}


async def get_gpu_tiers_live() -> dict:
    """Get all GPU tiers with live pricing, availability, and competitor comparison."""
    live_prices = await fetch_live_gpu_prices()

    tiers = []
    for tier_id, info in GPU_FULL_MAP.items():
        runpod_name = info["runpod_id"]

        # Try to find live price
        live = live_prices.get(runpod_name, {})
        if live:
            price = live.get("on_demand_price", 0)
            available = live.get("available", False)
            source = "runpod_live"
        else:
            # Fallback to competitor prices
            comp = COMPETITOR_PRICES.get(tier_id, {})
            price = comp.get("runpod_secure", comp.get("runpod_community", 0))
            available = True  # Assume available if no live data
            source = "cached"

        # Get competitor comparison
        competitors = COMPETITOR_PRICES.get(tier_id, {})
        comparison = {}
        for provider, comp_price in competitors.items():
            if comp_price and comp_price > 0 and provider != "runpod_community" and provider != "runpod_secure":
                savings = round((1 - price / comp_price) * 100, 0) if comp_price > price else 0
                comparison[provider] = {
                    "price": comp_price,
                    "savings_pct": savings,
                }

        gpu_count = info.get("gpu_count", 1)
        tiers.append({
            "id": tier_id,
            "label": runpod_name.replace("NVIDIA ", ""),
            "vram_gb": info["vram"],
            "gpu_count": gpu_count,
            "category": info["category"],
            "price_per_hour_usdc": round(price, 2),
            "available": available,
            "source": source,
            "maxia_markup": "0%",
            "competitors": comparison,
        })

    # Sort by price
    tiers.sort(key=lambda x: x["price_per_hour_usdc"])

    # Calculate cheapest for each category
    cheapest_overall = min(tiers, key=lambda x: x["price_per_hour_usdc"]) if tiers else {}

    return {
        "gpu_count": len(tiers),
        "tiers": tiers,
        "provider": "RunPod (via MAXIA)",
        "maxia_markup": "0% — same price as RunPod, pay with USDC",
        "cheapest": cheapest_overall.get("label", ""),
        "updated": int(time.time()),
        "advantage": "Pay with USDC on Solana. No RunPod account needed. AI agents can rent via API.",
    }

