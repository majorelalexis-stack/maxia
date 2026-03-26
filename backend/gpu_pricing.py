"""GPU Pricing Live — fetch les prix RunPod en temps reel via API GraphQL.

0% markup : MAXIA facture exactement le prix RunPod Community Cloud.
Les prix sont rafraichis toutes les 30 minutes.
Fallback sur les prix statiques si l'API est down.

Usage:
    from gpu_pricing import refresh_gpu_prices, get_gpu_tiers
    await refresh_gpu_prices()  # Au demarrage + toutes les 30 min
    tiers = get_gpu_tiers()     # Toujours a jour
"""
import asyncio
import time
import httpx
from config import RUNPOD_API_KEY, GPU_TIERS, GPU_TIERS_FALLBACK

# Cache des prix
_last_refresh: float = 0
_REFRESH_INTERVAL: float = 1800  # 30 minutes

# Mapping RunPod GPU ID → notre tier ID
_RUNPOD_TO_MAXIA = {
    "NVIDIA GeForce RTX 3090": {"id": "rtx3090", "label": "RTX 3090", "vram_gb": 24},
    "NVIDIA GeForce RTX 4090": {"id": "rtx4090", "label": "RTX 4090", "vram_gb": 24},
    "NVIDIA GeForce RTX 5090": {"id": "rtx5090", "label": "RTX 5090", "vram_gb": 32},
    "NVIDIA RTX A6000": {"id": "a6000", "label": "RTX A6000", "vram_gb": 48},
    "NVIDIA L4": {"id": "l4", "label": "L4", "vram_gb": 24},
    "NVIDIA L40S": {"id": "l40s", "label": "L40S", "vram_gb": 48},
    "NVIDIA RTX PRO 6000": {"id": "rtx_pro6000", "label": "RTX Pro 6000", "vram_gb": 96},
    "NVIDIA A100 80GB PCIe": {"id": "a100_80", "label": "A100 80GB", "vram_gb": 80},
    "NVIDIA A100-SXM4-80GB": {"id": "a100_80", "label": "A100 80GB SXM", "vram_gb": 80},
    "NVIDIA H100 SXM5": {"id": "h100_sxm", "label": "H100 SXM", "vram_gb": 80},
    "NVIDIA H100 NVL": {"id": "h100_nvl", "label": "H100 NVL", "vram_gb": 94},
    "NVIDIA H100 80GB HBM3": {"id": "h100_sxm", "label": "H100 SXM", "vram_gb": 80},
    "NVIDIA H200 SXM": {"id": "h200", "label": "H200 SXM", "vram_gb": 141},
    "NVIDIA B200": {"id": "b200", "label": "B200", "vram_gb": 180},
}


async def _fetch_runpod_gpu_types() -> list:
    """Fetch les types de GPU disponibles et leurs prix via l'API GraphQL RunPod."""
    if not RUNPOD_API_KEY:
        print("[GPU Pricing] RUNPOD_API_KEY absent — utilisation prix fallback")
        return []

    query = """
    query {
        gpuTypes {
            id
            displayName
            memoryInGb
            communityPrice
            securePrice
        }
    }
    """
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.runpod.io/graphql",
                json={"query": query},
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {RUNPOD_API_KEY}",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            errors = data.get("errors")
            if errors:
                print(f"[GPU Pricing] RunPod API error: {errors[0].get('message', '')}")
                return []
            gpu_types = data.get("data", {}).get("gpuTypes", [])
            print(f"[GPU Pricing] {len(gpu_types)} GPU types fetches depuis RunPod")
            return gpu_types
    except Exception as e:
        print(f"[GPU Pricing] Fetch error: {e}")
        return []


def _build_tiers_from_runpod(gpu_types: list) -> list:
    """Construit la liste GPU_TIERS a partir des donnees live RunPod."""
    tiers = []
    seen_ids = set()

    # 1. GPU local (toujours present, pas RunPod)
    tiers.append({
        "id": "local_7900xt", "label": "Local RX 7900XT",
        "vram_gb": 20, "base_price_per_hour": 0.35, "local": True,
    })
    seen_ids.add("local_7900xt")

    # 2. GPU RunPod — prix Community Cloud (le moins cher, 0% markup)
    for gpu in gpu_types:
        gpu_id = gpu.get("id", "")
        display = gpu.get("displayName", gpu_id)
        mapping = _RUNPOD_TO_MAXIA.get(gpu_id)
        if not mapping:
            continue

        tier_id = mapping["id"]
        if tier_id in seen_ids:
            # Garder le prix le plus bas entre community et secure
            existing = next((t for t in tiers if t["id"] == tier_id), None)
            if existing:
                community = gpu.get("communityPrice") or 999
                if community < existing["base_price_per_hour"]:
                    existing["base_price_per_hour"] = round(community, 2)
            continue

        # Prix Community Cloud (le plus bas)
        community = gpu.get("communityPrice")
        secure = gpu.get("securePrice")
        price = community or secure or 0
        if price <= 0:
            continue

        tiers.append({
            "id": tier_id,
            "label": mapping["label"],
            "vram_gb": mapping["vram_gb"],
            "base_price_per_hour": round(price, 2),
            "runpod_id": gpu_id,
            "runpod_display": display,
            "live_price": True,
        })
        seen_ids.add(tier_id)

    # 3. Multi-GPU (4x A100) — calculer a partir du prix unitaire
    a100_tier = next((t for t in tiers if t["id"] == "a100_80"), None)
    if a100_tier and "4xa100" not in seen_ids:
        tiers.append({
            "id": "4xa100", "label": "4x A100 80GB",
            "vram_gb": 320,
            "base_price_per_hour": round(a100_tier["base_price_per_hour"] * 4, 2),
            "live_price": True,
        })
        seen_ids.add("4xa100")

    # 4. Ajouter les GPUs du fallback qui ne sont PAS dans les resultats live
    # (ex: H200, RTX Pro 6000 si RunPod ne les retourne pas)
    for fb in GPU_TIERS_FALLBACK:
        if fb["id"] not in seen_ids and not fb.get("local"):
            tiers.append({**fb, "live_price": False})
            seen_ids.add(fb["id"])

    # Trier par prix
    tiers.sort(key=lambda t: t["base_price_per_hour"])
    return tiers


async def refresh_gpu_prices() -> list:
    """Rafraichit les prix GPU depuis RunPod. Appeler au demarrage + toutes les 30 min.
    Met a jour config.GPU_TIERS directement."""
    global _last_refresh

    gpu_types = await _fetch_runpod_gpu_types()

    if gpu_types:
        new_tiers = _build_tiers_from_runpod(gpu_types)
        if new_tiers and len(new_tiers) >= 3:
            # Mettre a jour GPU_TIERS in-place (toutes les refs le voient)
            GPU_TIERS.clear()
            GPU_TIERS.extend(new_tiers)
            _last_refresh = time.time()
            # Log les prix
            for t in new_tiers:
                src = "LIVE" if t.get("live_price") else ("LOCAL" if t.get("local") else "CALC")
                print(f"  [GPU] {t['label']:20s} {t['vram_gb']:>3d}GB  ${t['base_price_per_hour']:.2f}/h  [{src}]")
            print(f"[GPU Pricing] {len(new_tiers)} tiers mis a jour (prix live RunPod, 0% markup)")
            return new_tiers
        else:
            print("[GPU Pricing] Pas assez de tiers construits — garde les prix actuels")
    else:
        # Fallback — utiliser les prix statiques si pas encore charges
        if not _last_refresh:
            GPU_TIERS.clear()
            GPU_TIERS.extend(GPU_TIERS_FALLBACK)
            print("[GPU Pricing] Utilisation prix fallback (API RunPod indisponible)")

    return GPU_TIERS


def get_gpu_tiers() -> list:
    """Retourne les tiers GPU actuels (toujours a jour)."""
    return GPU_TIERS


def needs_refresh() -> bool:
    """Verifie si un refresh est necessaire (>30 min depuis le dernier)."""
    return time.time() - _last_refresh > _REFRESH_INTERVAL


async def auto_refresh_loop():
    """Boucle background qui rafraichit les prix toutes les 30 min."""
    while True:
        try:
            await refresh_gpu_prices()
        except Exception as e:
            print(f"[GPU Pricing] Auto-refresh error: {e}")
        await asyncio.sleep(_REFRESH_INTERVAL)
