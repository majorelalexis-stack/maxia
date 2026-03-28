"""MAXIA Art.18 V11 — Scale-Out Manager (auto-scaling workers)"""
import logging
import time, asyncio
import httpx
from config import SCALE_OUT_QUEUE_THRESHOLD, SCALE_OUT_COOLDOWN, RAILWAY_API_TOKEN
from http_client import get_http_client


class ScaleOutManager:
    """
    Gere le scale-out automatique de MAXIA.
    Si la file d'attente depasse le seuil, deploie un worker supplementaire.
    Cooldown de 5 min entre chaque scale-out pour eviter l'emballement.
    """

    def __init__(self):
        self._active_workers: list = []
        self._last_scale_out = 0
        self._total_scaled = 0
        print(f"[ScaleOut] Seuil: {SCALE_OUT_QUEUE_THRESHOLD} items, cooldown: {SCALE_OUT_COOLDOWN}s")

    async def check_and_scale(self, queue_size: int) -> dict:
        """Verifie la charge et deploie un worker si necessaire."""
        now = time.time()

        if queue_size < SCALE_OUT_QUEUE_THRESHOLD:
            return {"action": "none", "queue_size": queue_size, "threshold": SCALE_OUT_QUEUE_THRESHOLD}

        if now - self._last_scale_out < SCALE_OUT_COOLDOWN:
            remaining = int(SCALE_OUT_COOLDOWN - (now - self._last_scale_out))
            return {"action": "cooldown", "remaining_s": remaining}

        # Deployer un nouveau worker
        result = await self._deploy_worker()
        if result.get("success"):
            self._last_scale_out = now
            self._total_scaled += 1

        return result

    async def _deploy_worker(self) -> dict:
        """Deploie un worker supplementaire via Railway API."""
        if not RAILWAY_API_TOKEN:
            print("[ScaleOut] RAILWAY_API_TOKEN manquant — simulation")
            worker = {
                "workerId": f"sim-worker-{self._total_scaled + 1}",
                "status": "simulated",
                "deployedAt": int(time.time()),
            }
            self._active_workers.append(worker)
            return {"success": True, "mode": "simulation", **worker}

        try:
            client = get_http_client()
            resp = await client.post(
                "https://backboard.railway.app/graphql/v2",
                headers={
                    "Authorization": f"Bearer {RAILWAY_API_TOKEN}",
                    "Content-Type": "application/json",
                },
                json={
                    "query": """
                    mutation {
                      serviceCreate(input: {
                        name: "maxia-worker"
                        projectId: "auto"
                      }) { id name }
                    }
                    """,
                },
                timeout=30,
            )
            data = resp.json()

            service = data.get("data", {}).get("serviceCreate", {})
            if service.get("id"):
                worker = {
                    "workerId": service["id"],
                    "name": service.get("name", "maxia-worker"),
                    "status": "deploying",
                    "deployedAt": int(time.time()),
                }
                self._active_workers.append(worker)
                print(f"[ScaleOut] Worker deploye: {worker['workerId']}")
                return {"success": True, **worker}

            return {"success": False, "error": str(data.get("errors", "Unknown"))}
        except Exception as e:
            return {"success": False, "error": "An error occurred"}

    def get_stats(self) -> dict:
        return {
            "active_workers": len(self._active_workers),
            "total_scaled": self._total_scaled,
            "threshold": SCALE_OUT_QUEUE_THRESHOLD,
            "cooldown_s": SCALE_OUT_COOLDOWN,
            "last_scale_out": self._last_scale_out,
            "workers": self._active_workers[-5:],
        }


scale_out_manager = ScaleOutManager()
