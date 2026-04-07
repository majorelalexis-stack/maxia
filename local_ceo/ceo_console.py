"""CEO Console — Terminal interactif pour parler au CEO MAXIA.

Texte + screenshots. Dual-model routing:
  - Texte pur → OLLAMA_MODEL (Qwen 3.5 27B)
  - Image    → VISION_MODEL (Qwen 2.5-VL 7B) → description → OLLAMA_MODEL

Usage: python ceo_console.py
Commandes:
  /img <chemin>      Envoyer un screenshot au CEO (vision)
  /status            Etat du CEO (actions today, memoire, modele)
  /tweet <texte>     Poster un tweet (ou laisser le CEO generer)
  /rapport           Generer le rapport 24h
  /strategie <x>     Changer la strategie CEO
  /history           Derniers echanges
  /models            Voir les modeles charges dans Ollama
  /clear             Effacer l'historique conversation
  /quit              Quitter
"""
import asyncio
import base64
import json
import os
import sys
import time
import logging

import httpx

from config_local import (
    OLLAMA_URL, OLLAMA_MODEL, VISION_MODEL,
    VPS_URL, ADMIN_KEY, ALEXIS_EMAIL,
    MAXIA_FEATURES,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [CONSOLE] %(message)s")
log = logging.getLogger("console")

# ══════════════════════════════════════════
# Memoire conversation (session)
# ══════════════════════════════════════════

_DIR = os.path.dirname(__file__)
_MEMORY_FILE = os.path.join(_DIR, "ceo_memory.json")
_ACTIONS_FILE = os.path.join(_DIR, "actions_today.json")
_KNOWLEDGE_FILE = os.path.join(_DIR, "maxia_knowledge.md")

# Charger la knowledge base
MAXIA_KNOWLEDGE = ""
try:
    with open(_KNOWLEDGE_FILE, "r", encoding="utf-8") as f:
        MAXIA_KNOWLEDGE = f.read()[:3000]
except Exception:
    MAXIA_KNOWLEDGE = "MAXIA is an AI-to-AI marketplace on 14 blockchains."

CEO_SYSTEM = (
    "Tu es le CEO de MAXIA, un marketplace AI-to-AI sur 14 blockchains. "
    "Alexis (le fondateur) te parle directement via cette console. "
    "Reponds en francais, sois concis et actionnable. "
    "Tu connais MAXIA en detail:\n\n" + MAXIA_KNOWLEDGE +
    "\n\nRegles: Ton professionnel. Pas de mots hype. "
    "Si Alexis te donne un ordre, confirme et execute. "
    "Si il te montre un screenshot, analyse-le en detail."
)

# Historique conversation (garde les 20 derniers echanges)
conversation: list[dict] = []
MAX_HISTORY = 20


# ══════════════════════════════════════════
# LLM calls
# ══════════════════════════════════════════

async def llm_text(prompt: str, system: str = "", max_tokens: int = 1500) -> str:
    """Appel au modele texte principal."""
    full = f"{system}\n\n{prompt}" if system else prompt
    try:
        async with httpx.AsyncClient(timeout=180) as client:
            resp = await client.post(f"{OLLAMA_URL}/api/generate", json={
                "model": OLLAMA_MODEL,
                "prompt": full,
                "stream": False,
                "think": False,
                "options": {"num_predict": max_tokens, "temperature": 0.7},
            })
            resp.raise_for_status()
            return resp.json().get("response", "").strip()
    except Exception as e:
        return f"[ERREUR LLM] {e}"


async def llm_vision(image_path: str, prompt: str = "Decris cette image en detail.") -> str:
    """Appel au modele vision pour analyser un screenshot."""
    if not os.path.exists(image_path):
        return f"[ERREUR] Fichier introuvable: {image_path}"

    try:
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode("utf-8")
    except Exception as e:
        return f"[ERREUR] Lecture image: {e}"

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(f"{OLLAMA_URL}/api/generate", json={
                "model": VISION_MODEL,
                "prompt": prompt,
                "images": [img_b64],
                "stream": False,
                "options": {"num_predict": 800, "temperature": 0.3},
            })
            resp.raise_for_status()
            return resp.json().get("response", "").strip()
    except Exception as e:
        return f"[ERREUR VISION] {e}"


async def chat(user_input: str, image_path: str | None = None) -> str:
    """Envoie un message au CEO avec contexte conversation."""
    # Si image, d'abord la decrire via le modele vision
    image_desc = ""
    if image_path:
        print(f"  [vision] Analyse de {os.path.basename(image_path)}...")
        image_desc = await llm_vision(image_path, "Describe this screenshot in detail. What do you see? Any errors, issues, or notable elements?")
        if image_desc.startswith("[ERREUR"):
            return image_desc

    # Construire le prompt avec historique
    history_text = ""
    if conversation:
        recent = conversation[-10:]  # 10 derniers echanges
        for msg in recent:
            role = "Alexis" if msg["role"] == "user" else "CEO"
            history_text += f"{role}: {msg['content'][:300]}\n"

    prompt_parts = []
    if history_text:
        prompt_parts.append(f"Historique recent:\n{history_text}\n---")
    if image_desc:
        prompt_parts.append(f"[Screenshot analyse par vision AI]:\n{image_desc}\n---")
    prompt_parts.append(f"Alexis: {user_input}")

    full_prompt = "\n".join(prompt_parts)

    # Appel au modele principal
    response = await llm_text(full_prompt, system=CEO_SYSTEM)

    # Sauvegarder dans l'historique
    user_content = user_input
    if image_desc:
        user_content += f" [image: {image_desc[:100]}...]"
    conversation.append({"role": "user", "content": user_content})
    conversation.append({"role": "assistant", "content": response})

    # Trim historique
    while len(conversation) > MAX_HISTORY * 2:
        conversation.pop(0)

    return response


# ══════════════════════════════════════════
# Commandes speciales
# ══════════════════════════════════════════

async def cmd_status() -> str:
    """Affiche l'etat du CEO."""
    parts = ["\n=== CEO STATUS ==="]

    # Modeles
    parts.append(f"Modele texte:  {OLLAMA_MODEL}")
    parts.append(f"Modele vision: {VISION_MODEL}")

    # Modeles charges dans Ollama
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{OLLAMA_URL}/api/tags")
            if resp.status_code == 200:
                models = resp.json().get("models", [])
                parts.append(f"Modeles installes: {len(models)}")
                for m in models:
                    size_gb = m.get("size", 0) / (1024**3)
                    parts.append(f"  - {m['name']} ({size_gb:.1f}GB)")
    except Exception:
        parts.append("Ollama: non accessible")

    # Actions today
    try:
        with open(_ACTIONS_FILE, "r", encoding="utf-8") as f:
            actions = json.load(f)
        parts.append(f"\nActions aujourd'hui ({actions.get('date', '?')}):")
        for k, v in actions.get("counts", {}).items():
            parts.append(f"  {k}: {v}")
    except Exception:
        parts.append("Actions: fichier non trouve")

    # Memoire
    try:
        with open(_MEMORY_FILE, "r", encoding="utf-8") as f:
            mem = json.load(f)
        parts.append(f"\nMemoire CEO:")
        parts.append(f"  Tweets postes: {len(mem.get('tweets_posted', []))}")
        parts.append(f"  Opportunites envoyees: {len(mem.get('opportunities_sent', []))}")
        parts.append(f"  Agents vus: {len(mem.get('agents_seen', []))}")
        parts.append(f"  Feature index: {mem.get('feature_index', 0)}")
    except Exception:
        parts.append("Memoire: fichier non trouve")

    # Conversation
    parts.append(f"\nConversation: {len(conversation)} messages")

    return "\n".join(parts)


async def cmd_models() -> str:
    """Liste les modeles Ollama avec details."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{OLLAMA_URL}/api/tags")
            if resp.status_code != 200:
                return f"Ollama HTTP {resp.status_code}"
            models = resp.json().get("models", [])
            if not models:
                return "Aucun modele installe"
            parts = ["\n=== MODELES OLLAMA ==="]
            for m in models:
                size_gb = m.get("size", 0) / (1024**3)
                modified = m.get("modified_at", "")[:19]
                parts.append(f"  {m['name']:40s} {size_gb:6.1f}GB  ({modified})")

            # Modeles en cours d'execution
            resp2 = await client.get(f"{OLLAMA_URL}/api/ps")
            if resp2.status_code == 200:
                running = resp2.json().get("models", [])
                if running:
                    parts.append("\nEn VRAM:")
                    for r in running:
                        vram_gb = r.get("size_vram", 0) / (1024**3)
                        parts.append(f"  {r['name']:40s} {vram_gb:6.1f}GB VRAM")
                else:
                    parts.append("\nAucun modele charge en VRAM")

            return "\n".join(parts)
    except Exception as e:
        return f"Erreur Ollama: {e}"


async def cmd_tweet(text: str) -> str:
    """Generer un tweet et l'afficher pour que Alexis le poste manuellement."""
    if not text.strip():
        # Generer un tweet automatiquement
        text = await llm_text(
            "Write a short tweet (max 250 chars) presenting a random MAXIA feature. "
            "Include https://maxiaworld.app. End with #MAXIA #AI #Web3",
            system=CEO_SYSTEM,
            max_tokens=100,
        )
    # Mode PROPOSE: show the tweet for manual posting, do not post directly
    try:
        from config_local import PROPOSE_DONT_POST
        if PROPOSE_DONT_POST:
            return (
                f"=== TWEET PROPOSE (copie et poste manuellement sur X) ===\n\n"
                f"{text}\n\n"
                f"=== FIN ==="
            )
    except ImportError:
        pass

    # Legacy: direct posting (should not be reached with default config)
    try:
        from browser_agent import browser
        await browser.post_tweet(text)
        return f"Tweet poste: {text}"
    except Exception as e:
        return f"Erreur tweet: {e}\n\nTexte genere (a poster manuellement):\n{text}"


async def cmd_rapport() -> str:
    """Generer un mini-rapport live."""
    parts = ["=== RAPPORT LIVE ===\n"]

    # Health check
    endpoints = {
        "site": f"{VPS_URL}/",
        "prices": f"{VPS_URL}/api/public/crypto/prices",
        "forum": f"{VPS_URL}/api/public/forum",
    }

    async with httpx.AsyncClient(timeout=8) as client:
        for name, url in endpoints.items():
            try:
                t0 = time.time()
                resp = await client.get(url)
                latency = (time.time() - t0) * 1000
                status = "OK" if resp.status_code == 200 else f"HTTP {resp.status_code}"
                parts.append(f"  {name:12s} {status:8s} {latency:6.0f}ms")
            except Exception as e:
                parts.append(f"  {name:12s} DOWN     ({str(e)[:40]})")

    # Agents inscrits
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{VPS_URL}/api/public/leaderboard")
            if resp.status_code == 200:
                data = resp.json()
                agents = data.get("agents", data.get("leaderboard", []))
                parts.append(f"\nAgents inscrits: {len(agents)}")
    except Exception:
        parts.append("\nAgents: erreur API")

    return "\n".join(parts)


async def cmd_strategie(new_strategy: str) -> str:
    """Changer la strategie du CEO."""
    strategy_file = os.path.join(_DIR, "strategy.md")
    try:
        old = ""
        if os.path.exists(strategy_file):
            with open(strategy_file, "r", encoding="utf-8") as f:
                old = f.read()

        # Demander au CEO de reformuler
        reformulated = await llm_text(
            f"Alexis veut changer ta strategie. Nouvelle directive:\n{new_strategy}\n\n"
            f"Ancienne strategie:\n{old[:500]}\n\n"
            f"Reformule la nouvelle strategie en 5 points clairs et actionnables. "
            f"Format: 1. ... 2. ... etc.",
            system=CEO_SYSTEM,
            max_tokens=500,
        )

        # Sauvegarder
        from datetime import datetime
        header = f"# Strategie CEO — mise a jour {datetime.now().strftime('%d/%m/%Y %H:%M')}\n\n"
        header += f"Directive Alexis: {new_strategy}\n\n"
        with open(strategy_file, "w", encoding="utf-8") as f:
            f.write(header + reformulated)

        return f"Strategie mise a jour:\n\n{reformulated}"
    except Exception as e:
        return f"Erreur: {e}"


# ══════════════════════════════════════════
# Boucle principale
# ══════════════════════════════════════════

BANNER = """
 ╔══════════════════════════════════════════╗
 ║       MAXIA CEO Console v1.0            ║
 ║  Texte + Screenshots | Dual-model       ║
 ╠══════════════════════════════════════════╣
 ║  /img <path>   → envoyer screenshot     ║
 ║  /status       → etat CEO               ║
 ║  /tweet [txt]  → poster un tweet        ║
 ║  /rapport      → rapport live           ║
 ║  /strategie X  → changer strategie      ║
 ║  /models       → modeles Ollama         ║
 ║  /history      → derniers echanges      ║
 ║  /clear        → reset conversation     ║
 ║  /quit         → quitter                ║
 ╚══════════════════════════════════════════╝
"""


_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tiff")


def _extract_image_and_text(user_input: str) -> tuple[str | None, str]:
    """Extrait un chemin d'image et le texte restant depuis l'input.

    Supporte:
      "C:\\path\\img.png" c'est quoi?
      C:\\path\\img.png
      /img C:\\path\\img.png analyse ca
    """
    import re
    # 1. Chemin entre guillemets
    match = re.search(r'"([^"]+\.(png|jpg|jpeg|bmp|gif|webp|tiff))"', user_input, re.IGNORECASE)
    if match:
        path = match.group(1)
        if os.path.exists(path):
            remaining = user_input[:match.start()] + user_input[match.end():]
            remaining = remaining.strip().strip('"').strip("'").strip()
            return path, remaining or "Analyse ce screenshot et dis-moi ce que tu vois."

    # 2. Chemin avec espaces — cherche un pattern qui finit par une extension image
    match = re.search(r'([A-Za-z]:\\[^\n]*?\.(png|jpg|jpeg|bmp|gif|webp|tiff))', user_input, re.IGNORECASE)
    if match:
        path = match.group(1).strip()
        if os.path.exists(path):
            remaining = user_input[:match.start()] + user_input[match.end():]
            remaining = remaining.strip().strip('"').strip("'").strip()
            return path, remaining or "Analyse ce screenshot et dis-moi ce que tu vois."

    # 3. Input entier = chemin simple
    clean = user_input.strip().strip('"').strip("'")
    if os.path.exists(clean):
        ext = os.path.splitext(clean)[1].lower()
        if ext in _IMAGE_EXTS:
            return clean, "Analyse ce screenshot et dis-moi ce que tu vois."

    return None, user_input


async def main():
    print(BANNER)
    print(f"  Modele texte:  {OLLAMA_MODEL}")
    print(f"  Modele vision: {VISION_MODEL}")
    print(f"  VPS: {VPS_URL}")
    print()

    # Warmup — verifier Ollama
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{OLLAMA_URL}/api/tags")
            if resp.status_code == 200:
                models = [m["name"] for m in resp.json().get("models", [])]
                print(f"  Ollama OK — {len(models)} modeles installes")
                if OLLAMA_MODEL not in models and not any(OLLAMA_MODEL in m for m in models):
                    print(f"  [WARN] {OLLAMA_MODEL} non installe! Lancer: ollama pull {OLLAMA_MODEL}")
                if VISION_MODEL not in models and not any(VISION_MODEL in m for m in models):
                    print(f"  [WARN] {VISION_MODEL} non installe! Lancer: ollama pull {VISION_MODEL}")
            else:
                print(f"  [WARN] Ollama HTTP {resp.status_code}")
    except Exception:
        print("  [ERREUR] Ollama non accessible sur", OLLAMA_URL)

    print("\n  Pret. Tape ton message ou une commande.\n")

    while True:
        try:
            user_input = input("CEO> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Au revoir.")
            break

        if not user_input:
            continue

        # Commandes speciales
        if user_input.lower() in ("/quit", "/exit", "/q"):
            print("  Au revoir.")
            break

        if user_input.lower() == "/clear":
            conversation.clear()
            print("  Conversation effacee.")
            continue

        if user_input.lower() == "/history":
            if not conversation:
                print("  Aucun historique.")
            else:
                for msg in conversation[-10:]:
                    role = "Alexis" if msg["role"] == "user" else "CEO"
                    print(f"  {role}: {msg['content'][:200]}")
            continue

        if user_input.lower() == "/status":
            print(await cmd_status())
            continue

        if user_input.lower() == "/models":
            print(await cmd_models())
            continue

        if user_input.lower() == "/rapport":
            print("  Generation du rapport...")
            print(await cmd_rapport())
            continue

        if user_input.lower().startswith("/tweet"):
            text = user_input[6:].strip()
            print("  Envoi tweet...")
            print(await cmd_tweet(text))
            continue

        if user_input.lower().startswith("/strategie"):
            strat = user_input[10:].strip()
            if not strat:
                print("  Usage: /strategie <nouvelle directive>")
                continue
            print("  Mise a jour strategie...")
            print(await cmd_strategie(strat))
            continue

        if user_input.lower().startswith("/img"):
            rest = user_input[4:].strip()
            img_path, prompt = _extract_image_and_text(rest)
            if not img_path:
                print("  Usage: /img <chemin_image> [question]")
                continue
            print(f"  Analyse image + envoi au CEO...")
            response = await chat(prompt, image_path=img_path)
            print(f"\n  CEO: {response}\n")
            continue

        # Detection auto d'image (chemin dans l'input avec ou sans texte)
        img_path, remaining_text = _extract_image_and_text(user_input)
        if img_path:
            print(f"  Image detectee: {os.path.basename(img_path)}")
            response = await chat(remaining_text, image_path=img_path)
            print(f"\n  CEO: {response}\n")
            continue

        # Message texte normal
        response = await chat(user_input)
        print(f"\n  CEO: {response}\n")


if __name__ == "__main__":
    asyncio.run(main())
