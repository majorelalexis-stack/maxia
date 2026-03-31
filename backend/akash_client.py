"""MAXIA Akash Client — GPU rental via Akash Network (decentralise).

Akash = marketplace GPU decentralise. Les providers enchérissent pour servir les workloads.
Avantages : moins cher que RunPod/AWS (H100 ~$1.33/h vs $3.93 AWS), paiement USDC/AKT.

Flow (CLI-based — reliable, full control):
  1. Write SDL to temp file
  2. akash tx deployment create — posts SDL on-chain
  3. akash query market bid list — polls for provider bids
  4. akash tx market lease create — accepts cheapest bid
  5. akash provider send-manifest — sends workload to provider
  6. Monitor + terminate when expired

Listing GPU availability still via Console API (reliable for reads).
"""
import asyncio
import json as _json
import logging
import os
import subprocess
import tempfile
import time
import httpx
from error_utils import safe_error
from http_client import get_http_client

log = logging.getLogger("akash")

# Akash Console API (reads only — listing GPUs)
AKASH_API_URL = os.getenv("AKASH_API_URL", "https://console-api.akash.network")
AKASH_API_KEY = os.getenv("AKASH_API_KEY", "")

# Akash CLI config
AKASH_CLI = os.getenv("AKASH_CLI", "akash")
AKASH_KEY_NAME = os.getenv("AKASH_KEY_NAME", "maxia-gpu-hot")
AKASH_NODE = os.getenv("AKASH_NODE", "https://rpc.akashnet.net:443")
AKASH_CHAIN_ID = os.getenv("AKASH_CHAIN_ID", "akashnet-2")

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

log.info(f"[Akash] Client initialise — CLI={AKASH_CLI}, key={AKASH_KEY_NAME}, API={'present' if AKASH_API_KEY else 'ABSENTE'}")


def _run_cli(args: list, timeout: int = 30) -> dict:
    """Run akash CLI command synchronously. Returns parsed JSON or error dict."""
    cmd = [AKASH_CLI] + args + [
        "--node", AKASH_NODE,
        "--chain-id", AKASH_CHAIN_ID,
        "--keyring-backend", "test",
        "--output", "json",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            err = result.stderr.strip() or result.stdout.strip()
            log.error(f"[Akash CLI] {' '.join(args[:3])} failed: {err[:200]}")
            return {"error": err[:200]}
        if result.stdout.strip():
            return _json.loads(result.stdout)
        return {"ok": True}
    except subprocess.TimeoutExpired:
        return {"error": f"CLI timeout after {timeout}s"}
    except _json.JSONDecodeError:
        return {"error": f"Invalid JSON: {result.stdout[:100]}"}
    except Exception as e:
        return {"error": str(e)[:200]}


async def _run_cli_async(args: list, timeout: int = 30) -> dict:
    """Run akash CLI asynchronously (non-blocking)."""
    return await asyncio.get_event_loop().run_in_executor(None, _run_cli, args, timeout)


def _generate_sdl(tier_id: str, duration_hours: float) -> str:
    """Genere un SDL (Stack Definition Language) pour Akash deployment. Retourne un YAML string."""
    specs = AKASH_GPU_MAP.get(tier_id)
    if not specs:
        return ""
    max_price_uusd = int(AKASH_MAX_PRICE.get(tier_id, 1.0) * 1_000_000)
    sdl_yaml = f"""---
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
          denom: uakt
          amount: {max_price_uusd}
deployment:
  gpu-worker:
    dcloud:
      profile: gpu-worker
      count: 1
"""
    return sdl_yaml


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
        """Loue un GPU sur Akash via CLI. Flow complet: create → bid → lease → manifest."""
        if not self.is_available(tier_id):
            return {"success": False, "error": f"Tier {tier_id} non disponible sur Akash"}

        sdl = _generate_sdl(tier_id, duration_hours)
        if not sdl:
            return {"success": False, "error": f"SDL generation failed for {tier_id}"}

        max_price = AKASH_MAX_PRICE.get(tier_id, 1.0)

        # Write SDL to temp file
        sdl_path = os.path.join(tempfile.gettempdir(), f"akash_sdl_{tier_id}_{int(time.time())}.yaml")
        with open(sdl_path, "w") as f:
            f.write(sdl)

        try:
            # 1. Create deployment on-chain
            log.info(f"[Akash] Creating deployment: {tier_id} for {duration_hours}h")
            deposit = max(5000000, int(max_price * duration_hours * 1_000_000))  # min 5 AKT deposit in uakt
            create = await _run_cli_async([
                "tx", "deployment", "create", sdl_path,
                "--from", AKASH_KEY_NAME,
                "--deposit", f"{deposit}uakt",
                "--gas-prices", "0.025uakt",
                "--gas", "auto",
                "--gas-adjustment", "1.5",
                "--yes",
            ], timeout=30)

            if "error" in create:
                return {"success": False, "error": f"Deployment create failed: {create['error']}"}

            # Parse dseq from tx response
            dseq = ""
            for evt in create.get("events", create.get("logs", [{}])[0].get("events", [])):
                for attr in evt.get("attributes", []):
                    if attr.get("key") == "dseq":
                        dseq = attr["value"]
                        break
                if dseq:
                    break
            if not dseq:
                # Try txhash → query tx for dseq
                txhash = create.get("txhash", "")
                if txhash:
                    await asyncio.sleep(6)
                    tx_info = await _run_cli_async(["query", "tx", txhash], timeout=15)
                    for evt in tx_info.get("events", []):
                        for attr in evt.get("attributes", []):
                            if attr.get("key") == "dseq":
                                dseq = attr["value"]
                                break
                        if dseq:
                            break

            if not dseq:
                return {"success": False, "error": "Deployment created but could not parse dseq"}

            # Get owner address
            owner_resp = _run_cli(["keys", "show", AKASH_KEY_NAME, "-a"], timeout=5)
            owner = owner_resp.get("error", "") if "error" in owner_resp else ""
            if not owner:
                # keys show -a outputs plain text, not JSON
                result = subprocess.run(
                    [AKASH_CLI, "keys", "show", AKASH_KEY_NAME, "-a", "--keyring-backend", "test"],
                    capture_output=True, text=True, timeout=5
                )
                owner = result.stdout.strip()

            log.info(f"[Akash] Deployment created: dseq={dseq}, owner={owner}")

            # 2. Wait for bids (max 90s)
            log.info(f"[Akash] Waiting for bids...")
            best_bid = None
            for i in range(18):  # 18 x 5s = 90s
                await asyncio.sleep(5)
                bids = await _run_cli_async([
                    "query", "market", "bid", "list",
                    "--owner", owner,
                    "--dseq", dseq,
                    "--state", "open",
                ], timeout=15)
                bid_list = bids.get("bids", [])
                if bid_list:
                    # Pick cheapest bid
                    valid = []
                    for b in bid_list:
                        bp = b.get("bid", {}).get("price", {})
                        amount = float(bp.get("amount", "999999999"))
                        provider = b.get("bid", {}).get("bid_id", {}).get("provider", "")
                        valid.append({"provider": provider, "amount": amount, "bid": b})
                    if valid:
                        best_bid = min(valid, key=lambda x: x["amount"])
                        log.info(f"[Akash] Best bid: {best_bid['provider']} at {best_bid['amount']} uakt/block")
                        break
                if i % 6 == 5:
                    log.info(f"[Akash] Still waiting for bids... ({(i+1)*5}s)")

            if not best_bid:
                # Close deployment
                await _run_cli_async([
                    "tx", "deployment", "close", "--from", AKASH_KEY_NAME,
                    "--owner", owner, "--dseq", dseq,
                    "--gas-prices", "0.025uakt", "--gas", "auto", "--gas-adjustment", "1.5", "--yes",
                ], timeout=30)
                return {"success": False, "error": "No bids received after 90s"}

            # 3. Create lease (accept bid)
            provider = best_bid["provider"]
            log.info(f"[Akash] Accepting bid from {provider}")
            gseq = best_bid["bid"].get("bid", {}).get("bid_id", {}).get("gseq", "1")
            oseq = best_bid["bid"].get("bid", {}).get("bid_id", {}).get("oseq", "1")

            lease = await _run_cli_async([
                "tx", "market", "lease", "create",
                "--from", AKASH_KEY_NAME,
                "--owner", owner,
                "--dseq", dseq,
                "--gseq", str(gseq),
                "--oseq", str(oseq),
                "--provider", provider,
                "--gas-prices", "0.025uakt",
                "--gas", "auto",
                "--gas-adjustment", "1.5",
                "--yes",
            ], timeout=30)

            if "error" in lease:
                return {"success": False, "error": f"Lease create failed: {lease['error']}"}

            log.info(f"[Akash] Lease created, sending manifest...")

            # 4. Send manifest to provider
            await asyncio.sleep(3)
            manifest = await _run_cli_async([
                "provider", "send-manifest", sdl_path,
                "--from", AKASH_KEY_NAME,
                "--owner", owner,
                "--dseq", dseq,
                "--gseq", str(gseq),
                "--oseq", str(oseq),
                "--provider", provider,
            ], timeout=30)

            if "error" in manifest:
                log.warning(f"[Akash] Manifest send warning: {manifest['error']}")
                # Not fatal — sometimes returns error but works

            # 5. Get lease status for endpoints
            await asyncio.sleep(5)
            status = await _run_cli_async([
                "provider", "lease-status",
                "--from", AKASH_KEY_NAME,
                "--owner", owner,
                "--dseq", dseq,
                "--gseq", str(gseq),
                "--oseq", str(oseq),
                "--provider", provider,
            ], timeout=15)

            ssh_endpoint = ""
            jupyter_url = ""
            fwd_ports = status.get("forwarded_ports", {})
            for svc_name, ports in (fwd_ports if isinstance(fwd_ports, dict) else {}).items():
                for fp in (ports if isinstance(ports, list) else [ports]):
                    host = fp.get("host", "")
                    ext_port = fp.get("externalPort", 0)
                    port = fp.get("port", 0)
                    if port == 22 and host:
                        ssh_endpoint = f"ssh root@{host} -p {ext_port}"
                    if port == 8888 and host:
                        jupyter_url = f"http://{host}:{ext_port}"

            # 6. Register active deployment
            instance_id = f"akash_{dseq}"
            now = time.time()
            price_hr = best_bid["amount"] * 600 / 1_000_000  # uakt/block * ~600 blocks/h → USD approx
            _active_deployments[instance_id] = {
                "deployment_id": dseq,
                "provider": provider,
                "owner": owner,
                "gseq": str(gseq),
                "oseq": str(oseq),
                "tier": tier_id,
                "price_per_hour": price_hr,
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
                "cost_per_hr": round(price_hr, 4),
                "total_estimated": round(price_hr * duration_hours, 2),
                "auto_terminate_at": int(now + duration_hours * 3600),
            }
        finally:
            # Cleanup temp SDL file
            try:
                os.unlink(sdl_path)
            except OSError:
                pass

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

        # Fermer le deployment via CLI (libere l'escrow restant)
        owner = info.get("owner", "")
        resp = await _run_cli_async([
            "tx", "deployment", "close", "--from", AKASH_KEY_NAME,
            "--owner", owner, "--dseq", deployment_id,
            "--gas-prices", "0.025uakt", "--gas", "auto", "--gas-adjustment", "1.5", "--yes",
        ], timeout=30)
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
