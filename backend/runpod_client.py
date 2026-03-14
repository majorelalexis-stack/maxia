"""MAXIA RunPod Client V11 — Location GPU complete

Fonctionnalites :
- Creer un pod GPU (provisionnement reel)
- Recuperer les credentials SSH/API
- Verifier le statut du pod
- Arreter le pod apres la duree louee
- Estimer le cout final
"""
import asyncio, time
import httpx
from config import RUNPOD_API_KEY

BASE_URL = "https://api.runpod.io/graphql"

# Mapping tier -> RunPod GPU ID
GPU_MAP = {
    "rtx4090":   {"runpod_id": "NVIDIA GeForce RTX 4090", "cloud_type": "SECURE"},
    "a100_80":   {"runpod_id": "NVIDIA A100 80GB PCIe", "cloud_type": "SECURE"},
    "h100_sxm5": {"runpod_id": "NVIDIA H100 SXM5", "cloud_type": "SECURE"},
    "a6000":     {"runpod_id": "NVIDIA RTX A6000", "cloud_type": "SECURE"},
    "4xa100":    {"runpod_id": "NVIDIA A100 80GB PCIe", "cloud_type": "SECURE", "gpu_count": 4},
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
        cost_per_hr = pod.get("costPerHr", 0)

        print(f"[RunPod] Pod cree: {pod_id} ({gpu_id}) — host: {host_id}")

        # 2. Attendre que le pod soit pret (max 60 secondes)
        credentials = await self._wait_for_ready(pod_id, timeout=60)

        # 3. Enregistrer le pod actif
        _active_pods[pod_id] = {
            "pod_id": pod_id,
            "gpu_tier": gpu_tier_id,
            "gpu_name": gpu_id,
            "host_id": host_id,
            "start_time": int(time.time()),
            "duration_hours": duration_hours,
            "end_time": int(time.time() + duration_hours * 3600),
            "cost_per_hr": cost_per_hr,
            "status": credentials.get("status", "provisioning"),
        }

        # 4. Programmer l'arret automatique
        asyncio.create_task(self._auto_terminate(pod_id, duration_hours))

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

    async def _auto_terminate(self, pod_id: str, duration_hours: float):
        """Arrete automatiquement le pod apres la duree louee."""
        await asyncio.sleep(duration_hours * 3600)
        print(f"[RunPod] Auto-terminate pod {pod_id} apres {duration_hours}h")
        await self.terminate_pod(pod_id)

    async def terminate_pod(self, pod_id: str) -> dict:
        """Arrete et supprime un pod."""
        query = f'mutation {{ podTerminate(input: {{ podId: "{pod_id}" }}) }}'
        data = await self._query(query)

        if pod_id in _active_pods:
            pod_info = _active_pods[pod_id]
            elapsed_hours = (time.time() - pod_info["start_time"]) / 3600
            actual_cost = round(elapsed_hours * pod_info.get("cost_per_hr", 0), 4)
            _active_pods[pod_id]["status"] = "terminated"
            _active_pods[pod_id]["actual_hours"] = round(elapsed_hours, 2)
            _active_pods[pod_id]["actual_cost"] = actual_cost
            print(f"[RunPod] Pod {pod_id} termine — {elapsed_hours:.1f}h, cout: ${actual_cost}")

        errors = data.get("errors")
        if errors:
            return {"success": False, "error": errors[0].get("message", str(errors))}

        return {"success": True, "pod_id": pod_id, "status": "terminated"}

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
