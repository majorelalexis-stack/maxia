"""MAXIA Art.26 — Generation d'Images IA

Les IA ne peuvent pas generer d'images elles-memes.
Ce service utilise Together AI (si cle configuree) ou Pollinations.ai
(gratuit, sans cle, illimite) pour generer des images a partir d'un prompt.
"""
import asyncio, time, uuid, base64, os
import httpx

# Together AI — tier gratuit disponible
TOGETHER_API_KEY = os.getenv("TOGETHER_API_KEY", "")
TOGETHER_URL = "https://api.together.xyz/v1/images/generations"

# Modeles disponibles
MODELS = {
    "flux-schnell": {
        "id": "black-forest-labs/FLUX.1-schnell-Free",
        "name": "FLUX.1 Schnell (Fast)",
        "speed": "fast",
        "quality": "good",
        "free": True,
    },
    "flux-dev": {
        "id": "black-forest-labs/FLUX.1-dev",
        "name": "FLUX.1 Dev (High Quality)",
        "speed": "medium",
        "quality": "high",
        "free": False,
    },
    "sdxl": {
        "id": "stabilityai/stable-diffusion-xl-base-1.0",
        "name": "Stable Diffusion XL",
        "speed": "medium",
        "quality": "good",
        "free": True,
    },
}

# Mots bloques (Art.1 — securite)
BLOCKED_WORDS = [
    "child", "minor", "underage", "kid", "teen", "young girl", "young boy",
    "nude", "naked", "nsfw", "porn", "sexual", "explicit",
    "gore", "violence", "blood", "murder", "torture",
    "terrorism", "bomb", "weapon", "gun",
]

# Stats
_gen_stats = {"total": 0, "success": 0, "blocked": 0, "errors": 0}
_gen_history: list = []

print(f"[ImageGen] Service initialise — {'Together AI' if TOGETHER_API_KEY else 'Pollinations.ai (gratuit, sans cle)'}")


def _check_prompt_safety(prompt: str) -> bool:
    """Verifie que le prompt ne contient pas de contenu interdit (Art.1)."""
    prompt_lower = prompt.lower()
    for word in BLOCKED_WORDS:
        if word in prompt_lower:
            return False
    return True


async def generate_image(prompt: str, model: str = "flux-schnell",
                          width: int = 1024, height: int = 1024,
                          steps: int = 4, seed: int = 0) -> dict:
    """Genere une image a partir d'un prompt texte."""
    _gen_stats["total"] += 1

    # Validation
    if not prompt or len(prompt) < 3:
        return {"success": False, "error": "Prompt trop court (min 3 caracteres)"}

    if len(prompt) > 1000:
        prompt = prompt[:1000]

    # Art.1 — Securite
    if not _check_prompt_safety(prompt):
        _gen_stats["blocked"] += 1
        return {"success": False, "error": "Prompt bloque par Art.1 (contenu interdit detecte)"}

    # Valider le modele
    model_config = MODELS.get(model)
    if not model_config:
        return {"success": False, "error": f"Modele inconnu: {model}. Disponibles: {list(MODELS.keys())}"}

    # Valider les dimensions
    width = max(256, min(width, 2048))
    height = max(256, min(height, 2048))

    # Generer via Together AI (si cle configuree) ou Pollinations.ai (gratuit)
    if TOGETHER_API_KEY:
        result = await _generate_together(prompt, model_config["id"], width, height, steps, seed)
    else:
        # Pollinations.ai — gratuit, sans cle, images reelles (pas un placeholder)
        result = await _generate_pollinations(prompt, width, height, seed)

    if result.get("success"):
        _gen_stats["success"] += 1
        gen_record = {
            "gen_id": str(uuid.uuid4()),
            "prompt": prompt[:100],
            "model": model,
            "width": width, "height": height,
            "timestamp": int(time.time()),
        }
        _gen_history.append(gen_record)
        print(f"[ImageGen] Generated: '{prompt[:50]}...' ({model}, {width}x{height})")

    return result


async def _generate_together(prompt: str, model_id: str,
                               width: int, height: int,
                               steps: int, seed: int) -> dict:
    """Genere via Together AI API."""
    try:
        headers = {
            "Authorization": f"Bearer {TOGETHER_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model_id,
            "prompt": prompt,
            "width": width,
            "height": height,
            "steps": steps,
            "n": 1,
            "response_format": "b64_json",
        }
        if seed > 0:
            payload["seed"] = seed

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(TOGETHER_URL, json=payload, headers=headers)

        if resp.status_code == 200:
            data = resp.json()
            images = data.get("data", [])
            if images:
                b64 = images[0].get("b64_json", "")
                if b64:
                    return {
                        "success": True,
                        "image_base64": b64,
                        "format": "png",
                        "width": width,
                        "height": height,
                        "model": model_id,
                        "prompt": prompt,
                        "data_url": f"data:image/png;base64,{b64[:50]}...",
                        "size_bytes": len(b64) * 3 // 4,
                    }

            # URL format
            if images and images[0].get("url"):
                return {
                    "success": True,
                    "image_url": images[0]["url"],
                    "format": "png",
                    "width": width, "height": height,
                    "model": model_id, "prompt": prompt,
                }

            return {"success": False, "error": "Pas d'image dans la reponse"}

        elif resp.status_code == 429:
            return {"success": False, "error": "Rate limit Together AI — reessayez dans 60s"}
        else:
            error_text = resp.text[:200]
            _gen_stats["errors"] += 1
            return {"success": False, "error": f"Together AI error {resp.status_code}: {error_text}"}

    except httpx.TimeoutException:
        _gen_stats["errors"] += 1
        return {"success": False, "error": "Timeout (60s) — image trop complexe ou serveur surcharge"}
    except Exception as e:
        _gen_stats["errors"] += 1
        return {"success": False, "error": str(e)[:200]}


async def _generate_pollinations(prompt: str, width: int, height: int, seed: int = 0) -> dict:
    """Genere via Pollinations.ai — 100% gratuit, sans cle API, images reelles."""
    try:
        import urllib.parse
        encoded = urllib.parse.quote(prompt)
        url = f"https://image.pollinations.ai/prompt/{encoded}?width={width}&height={height}&nologo=true"
        if seed > 0:
            url += f"&seed={seed}"

        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(url)

        if resp.status_code == 200 and len(resp.content) > 1000:
            b64 = base64.b64encode(resp.content).decode()
            return {
                "success": True,
                "image_base64": b64,
                "format": "png",
                "width": width,
                "height": height,
                "model": "pollinations-flux",
                "prompt": prompt,
                "source": "pollinations.ai",
                "size_bytes": len(resp.content),
            }
        return {"success": False, "error": f"Pollinations returned {resp.status_code} ({len(resp.content)} bytes)"}

    except httpx.TimeoutException:
        _gen_stats["errors"] += 1
        return {"success": False, "error": "Pollinations timeout (30s)"}
    except Exception as e:
        _gen_stats["errors"] += 1
        return {"success": False, "error": f"Pollinations error: {str(e)[:100]}"}


def list_models() -> dict:
    """Liste les modeles disponibles."""
    return {
        "models": [
            {
                "id": k,
                "name": v["name"],
                "speed": v["speed"],
                "quality": v["quality"],
                "free": v["free"],
            }
            for k, v in MODELS.items()
        ],
        "default": "flux-schnell",
        "max_width": 2048,
        "max_height": 2048,
        "api_key_configured": bool(TOGETHER_API_KEY),
    }


def get_gen_stats() -> dict:
    return {
        **_gen_stats,
        "history_count": len(_gen_history),
        "models_available": len(MODELS),
        "api_key_configured": bool(TOGETHER_API_KEY),
    }
