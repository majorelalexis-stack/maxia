"""CEO Local — Boucle OODA autonome sur le PC (cerveau + Playwright).

Tourne 24/7, pilote le VPS via les endpoints CEO securises.
Le LLM local (Ollama) fait le gros du travail, les API payantes sont reserves aux decisions critiques.

Usage:
    python ceo_local.py
"""
import asyncio
import json
import time
import sys
import os
import uuid
import httpx

from config_local import (
    VPS_URL, CEO_API_KEY, OODA_INTERVAL_S,
    OLLAMA_URL, OLLAMA_MODEL,
    MISTRAL_API_KEY, MISTRAL_MODEL,
    AUTO_EXECUTE_MAX_USD,
)
from audit_local import audit
from notifier import notify_all, request_approval, get_pending_approvals
from browser_agent import browser

# ══════════════════════════════════════════
# Memoire locale persistante (JSON)
# ══════════════════════════════════════════

_MEMORY_FILE = os.path.join(os.path.dirname(__file__), "ceo_memory.json")
_MEMORY_KEY_FILE = os.path.join(os.path.dirname(__file__), ".memory_key")


def _get_cipher_key() -> bytes:
    """Genere ou charge une cle de chiffrement (Fernet)."""
    if os.path.exists(_MEMORY_KEY_FILE):
        with open(_MEMORY_KEY_FILE, "rb") as f:
            return f.read()
    try:
        from cryptography.fernet import Fernet
        key = Fernet.generate_key()
        with open(_MEMORY_KEY_FILE, "wb") as f:
            f.write(key)
        return key
    except ImportError:
        return b""


def _encrypt(data: str) -> str:
    """Chiffre les donnees sensibles (wallets, contacts)."""
    try:
        from cryptography.fernet import Fernet
        key = _get_cipher_key()
        if not key:
            return data
        return Fernet(key).encrypt(data.encode()).decode()
    except Exception:
        return data


def _decrypt(data: str) -> str:
    """Dechiffre les donnees."""
    try:
        from cryptography.fernet import Fernet
        key = _get_cipher_key()
        if not key:
            return data
        return Fernet(key).decrypt(data.encode()).decode()
    except Exception:
        return data


def _load_memory() -> dict:
    try:
        if os.path.exists(_MEMORY_FILE):
            with open(_MEMORY_FILE, "r", encoding="utf-8") as f:
                raw = f.read()
            # Tenter de dechiffrer si c'est chiffre
            if raw.startswith("gAAAAA"):
                raw = _decrypt(raw)
            return json.loads(raw)
    except Exception:
        pass
    return {
        "decisions": [], "actions_done": [], "regles": [],
        "tweets_posted": [], "contacts": [], "follows": [],
        "last_strategic": "", "cycle_count": 0,
        "daily_stats": {},
    }


def _save_memory(mem: dict):
    try:
        # Garder les listes a taille raisonnable
        for key in ["decisions", "actions_done", "tweets_posted"]:
            if len(mem.get(key, [])) > 500:
                mem[key] = mem[key][-500:]
        raw = json.dumps(mem, indent=2, default=str, ensure_ascii=False)
        # Chiffrer les contacts et wallets
        sensitive_keys = ["contacts", "follows"]
        for k in sensitive_keys:
            if k in mem and mem[k]:
                # On chiffre le fichier complet si des donnees sensibles existent
                try:
                    from cryptography.fernet import Fernet
                    raw = _encrypt(raw)
                except ImportError:
                    pass
                break
        with open(_MEMORY_FILE, "w", encoding="utf-8") as f:
            f.write(raw)
    except Exception as e:
        print(f"[Memory] Save error: {e}")


# ══════════════════════════════════════════
# Logs rotatifs
# ══════════════════════════════════════════

_LOG_FILE = os.path.join(os.path.dirname(__file__), "ceo_local.log")
_MAX_LOG_SIZE = 5 * 1024 * 1024  # 5 Mo


def _rotate_log():
    """Rotation si log > 5 Mo."""
    try:
        if os.path.exists(_LOG_FILE) and os.path.getsize(_LOG_FILE) > _MAX_LOG_SIZE:
            backup = _LOG_FILE + ".old"
            if os.path.exists(backup):
                os.remove(backup)
            os.rename(_LOG_FILE, backup)
    except Exception:
        pass


def _log(msg: str):
    """Log dans fichier + stdout."""
    _rotate_log()
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    try:
        print(line.encode("utf-8", errors="replace").decode("utf-8"))
    except Exception:
        print(line.encode("ascii", errors="replace").decode("ascii"))
    try:
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ══════════════════════════════════════════
# Tweet templates + A/B testing
# ══════════════════════════════════════════

import random

TWEET_TEMPLATES = [
    # Tips techniques
    "Your AI agent can earn USDC on Solana with one API call:\n\nPOST /api/public/sell\n→ Service listed\n→ Other AIs buy it\n→ USDC in your wallet\n\nNo token. No waitlist. Just code.\nmaxiaworld.app",
    "Built a bot that works but earns $0?\n\nMAXIA lets other AI agents discover and buy your service. Payments in USDC on Solana.\n\n1 API call to list. That's it.\nmaxiaworld.app",
    "GPU at cost. $0.69/h RTX 4090. Zero markup.\n\nSwap 15 tokens. 210 pairs. Via Jupiter.\n\nAI marketplace where agents trade with agents.\nmaxiaworld.app",
    # Stats
    "MAXIA stats:\n- 15 tokens, 210 pairs\n- GPU from $0.69/h (0% markup)\n- 10 tokenized stocks\n- 22 MCP tools\n- 3 chains (Solana + Base + ETH)\n\nAll pay-per-use. No subscription.\nmaxiaworld.app",
    # Dev-focused
    "If you're building an AI agent on Solana and want it to earn money autonomously:\n\n```python\nimport requests\nrequests.post('https://maxiaworld.app/api/public/sell',\n  json={{'name': 'my-agent', 'price': 0.50}})\n```\n\nDone. Other AIs will find and pay you.",
    # Comparative
    "GPU rental comparison:\n- AWS: $3.06/h\n- RunPod: $0.69/h\n- MAXIA: $0.69/h (0% markup)\n\nSame GPU, same price as RunPod, but integrated with AI marketplace.\nmaxiaworld.app",
    # Community
    "Devs building AI agents: what's your biggest pain point?\n\n- Finding users?\n- Getting paid?\n- Managing infra?\n\nWe built MAXIA to solve all three. Open source.\nmaxiaworld.app",
    # Thread starter
    "Thread: How to monetize your AI agent in 5 minutes\n\n1/ You have an AI bot. It works. But nobody pays for it.\n\nThe problem isn't your code. It's distribution.",
]

TWEET_VARIANTS = {
    "A": {"style": "direct, technique, code snippets", "cta": "maxiaworld.app"},
    "B": {"style": "storytelling, probleme/solution", "cta": "link in bio"},
}

# A/B test tracking
_AB_FILE = os.path.join(os.path.dirname(__file__), "ab_tests.json")


def _load_ab() -> dict:
    try:
        if os.path.exists(_AB_FILE):
            with open(_AB_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {"tests": [], "template_usage": {}}


def _save_ab(data: dict):
    try:
        with open(_AB_FILE, "w") as f:
            json.dump(data, f, indent=2, default=str)
    except Exception:
        pass


def pick_tweet_template() -> str:
    """Choisit un template aleatoire, evite ceux recemment utilises."""
    ab = _load_ab()
    usage = ab.get("template_usage", {})
    # Trier par usage (moins utilise = prioritaire)
    scored = [(t, usage.get(t[:20], 0)) for t in TWEET_TEMPLATES]
    scored.sort(key=lambda x: x[1])
    # Choisir parmi les 3 moins utilises
    chosen = random.choice(scored[:3])[0]
    usage[chosen[:20]] = usage.get(chosen[:20], 0) + 1
    ab["template_usage"] = usage
    _save_ab(ab)
    return chosen


def start_ab_test(tweet_a: str, tweet_b: str) -> dict:
    """Lance un A/B test: poste 2 variantes et les suit."""
    ab = _load_ab()
    test_id = f"ab_{int(time.time())}"
    ab["tests"].append({
        "id": test_id,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "variant_a": {"text": tweet_a[:280], "engagement": None},
        "variant_b": {"text": tweet_b[:280], "engagement": None},
        "winner": None,
        "status": "pending",
    })
    _save_ab(ab)
    return {"test_id": test_id}


async def check_ab_results() -> list:
    """Verifie l'engagement des A/B tests en cours."""
    ab = _load_ab()
    results = []
    for test in ab.get("tests", []):
        if test["status"] != "pending":
            continue
        # Si test a plus de 2h, verifier engagement
        test_ts = test.get("ts", "")
        if not test_ts:
            continue
        try:
            from datetime import datetime
            age_h = (datetime.utcnow() - datetime.fromisoformat(test_ts)).total_seconds() / 3600
            if age_h < 2:
                continue  # Trop tot
        except Exception:
            continue

        # Verifier engagement via browser
        for variant in ["variant_a", "variant_b"]:
            url = test[variant].get("tweet_url", "")
            if url and not test[variant].get("engagement"):
                eng = await browser.verify_tweet_engagement(url)
                test[variant]["engagement"] = eng

        # Determiner le gagnant
        eng_a = test["variant_a"].get("engagement", {})
        eng_b = test["variant_b"].get("engagement", {})
        score_a = eng_a.get("likes", 0) * 2 + eng_a.get("retweets", 0) * 3 + eng_a.get("replies", 0)
        score_b = eng_b.get("likes", 0) * 2 + eng_b.get("retweets", 0) * 3 + eng_b.get("replies", 0)

        if score_a > 0 or score_b > 0:
            test["winner"] = "A" if score_a >= score_b else "B"
            test["status"] = "complete"
            results.append({"id": test["id"], "winner": test["winner"], "score_a": score_a, "score_b": score_b})

    _save_ab(ab)
    return results


# ══════════════════════════════════════════
# Reply intelligent aux mentions
# ══════════════════════════════════════════

async def generate_smart_reply(mention_text: str, username: str) -> str:
    """Genere une reponse pertinente a une mention via Ollama."""
    prompt = (
        f"Un user @{username} a mentionne MAXIA:\n\"{mention_text[:200]}\"\n\n"
        f"Redige une reponse courte (<200 chars), utile et technique.\n"
        f"Ton: dev qui aide un autre dev. Pas de marketing.\n"
        f"Si c'est une question: reponds. Si c'est un compliment: remercie. Si c'est une plainte: excuse+solution.\n"
        f"Reponds JUSTE le texte de la reponse, rien d'autre."
    )
    reply = await call_local_llm(prompt, max_tokens=100)
    # Nettoyer
    reply = reply.strip().strip('"').strip("'")
    if len(reply) > 280:
        reply = reply[:277] + "..."
    return reply


# ══════════════════════════════════════════
# LLM Router local (simplifie — Ollama + Mistral fallback)
# ══════════════════════════════════════════

async def call_ollama(prompt: str, system: str = "", max_tokens: int = 500, model: str = None) -> str:
    """Appel Ollama local (0 cout). Utilise maxia-ceo par defaut."""
    _model = model or "maxia-ceo"  # Modele fine-tune MAXIA
    full = f"{system}\n\n{prompt}" if system else prompt
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model": _model,
                    "prompt": full,
                    "stream": False,
                    "options": {"num_predict": max_tokens, "temperature": 0.7},
                },
            )
            resp.raise_for_status()
            return resp.json().get("response", "")
    except Exception as e:
        print(f"[Local LLM] Ollama error: {e}")
        return ""


async def call_mistral(prompt: str, system: str = "", max_tokens: int = 500) -> str:
    """Appel Mistral API (fallback si Ollama down)."""
    if not MISTRAL_API_KEY:
        return ""
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.mistral.ai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {MISTRAL_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={"model": MISTRAL_MODEL, "messages": msgs, "max_tokens": max_tokens},
            )
            resp.raise_for_status()
            choices = resp.json().get("choices", [])
            return choices[0]["message"]["content"].strip() if choices else ""
    except Exception as e:
        print(f"[Local LLM] Mistral error: {e}")
        return ""


async def call_local_llm(prompt: str, system: str = "", max_tokens: int = 500) -> str:
    """Ollama avec fallback Mistral."""
    result = await call_ollama(prompt, system, max_tokens)
    if result:
        return result
    return await call_mistral(prompt, system, max_tokens)


def parse_json(text: str) -> dict:
    """Parse JSON tolerant."""
    if not text:
        return {}
    try:
        c = text.strip()
        for p in ["```json", "```"]:
            if c.startswith(p):
                c = c[len(p):]
        if c.endswith("```"):
            c = c[:-3]
        return json.loads(c.strip())
    except json.JSONDecodeError:
        try:
            return json.loads(text[text.index("{"):text.rindex("}") + 1])
        except (ValueError, json.JSONDecodeError):
            return {}


# ══════════════════════════════════════════
# VPS API Client
# ══════════════════════════════════════════

class VPSClient:
    """Communique avec le VPS via les endpoints CEO securises."""

    def __init__(self):
        self._base = VPS_URL.rstrip("/")
        self._headers = {"X-CEO-Key": CEO_API_KEY, "Content-Type": "application/json"}

    async def get_state(self) -> dict:
        """GET /api/ceo/state — Etat complet du VPS."""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(f"{self._base}/api/ceo/state", headers=self._headers)
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            print(f"[VPS] get_state error: {e}")
            return {}

    async def execute(self, action: str, agent: str, params: dict,
                      priority: str = "vert") -> dict:
        """POST /api/ceo/execute — Executer une action sur le VPS."""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{self._base}/api/ceo/execute",
                    headers=self._headers,
                    json={
                        "action": action,
                        "agent": agent,
                        "params": params,
                        "priority": priority,
                    },
                )
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            print(f"[VPS] execute error: {e}")
            return {"success": False, "error": str(e)}

    async def health(self) -> dict:
        """GET /api/ceo/health — Sante du VPS."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{self._base}/api/ceo/health", headers=self._headers)
                return resp.json()
        except Exception as e:
            return {"healthy": False, "error": str(e)}

    async def emergency_stop(self) -> dict:
        """POST /api/ceo/emergency-stop."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(f"{self._base}/api/ceo/emergency-stop", headers=self._headers)
                return resp.json()
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def sync(self, local_actions: list, active: bool = True) -> dict:
        """POST /api/ceo/sync — Synchronise les actions avec le VPS."""
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"{self._base}/api/ceo/sync",
                    headers=self._headers,
                    json={"actions": local_actions, "active": active},
                )
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            return {"error": str(e)}

    async def think(self, prompt: str, tier: str = "fast", max_tokens: int = 1000) -> str:
        """POST /api/ceo/think — Delegue la reflexion strategique a Claude sur le VPS."""
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                resp = await client.post(
                    f"{self._base}/api/ceo/think",
                    headers=self._headers,
                    json={"prompt": prompt, "tier": tier, "max_tokens": max_tokens},
                )
                resp.raise_for_status()
                data = resp.json()
                cached = data.get("cached", False)
                cost = data.get("cost_usd", 0)
                if cached:
                    _log("  [VPS/think] Cache hit (0$)")
                else:
                    _log(f"  [VPS/think] {tier} ~${cost}")
                return data.get("result", "")
        except Exception as e:
            _log(f"[VPS] think error: {e}")
            return ""


# ══════════════════════════════════════════
# Boucle OODA principale
# ══════════════════════════════════════════

CEO_SYSTEM = """Tu es CEO MAXIA, dirigeant autonome de la marketplace IA-to-IA sur Solana + Base + Ethereum.
Produit : AI Marketplace — swap 15 tokens 210 paires, stocks 10 actions, GPU 0% marge, services IA, MCP 22 tools.
Phase : Pre-seed | Vision : Devenir la couche d intelligence liquide de l ecosysteme Solana.
Fondateur : Alexis (autorite finale sur decisions rouges)
URL : maxiaworld.app

17 SOUS-AGENTS SUR LE VPS (tu leur donnes des ordres via l API) :
- GHOST-WRITER : contenu, tweets, threads (JAMAIS publier sans validation WATCHDOG)
- HUNTER : prospection HUMAINE profil Thomas (devs avec bots IA sans revenus)
- SCOUT : prospection IA-to-IA sur 3 chains (Olas, Fetch, ElizaOS, Virtuals)
- WATCHDOG : monitoring, validation, self-healing
- SOL-TREASURY : budget dynamique indexe revenus
- RESPONDER : repond a TOUS messages 24/7
- RADAR : intelligence on-chain predictive
- TESTIMONIAL : feedback post-transaction, social proof
- NEGOTIATOR : negocie les prix automatiquement
- COMPLIANCE : verification AML/sanctions
- PARTNERSHIP : detection partenariats strategiques
- ANALYTICS : metriques avancees (LTV, churn, health score)
- CRISIS-MANAGER : gestion crises P0-P3
- DEPLOYER : pages web via GitHub Pages
- WEB-DESIGNER : config JSON frontend
- ORACLE : social listening
- MICRO : wallet micro-depenses

ACTIONS DISPONIBLES :
Twitter (local Playwright, 0 cout) :
- post_tweet: poster sur X (params: text) [VERT]
- reply_tweet: repondre a un tweet (params: tweet_url, text) [VERT]
- like_tweet: liker un tweet (params: tweet_url) [VERT]
- follow_user: follow un profil (params: username) [VERT]
- search_twitter: chercher tweets/hashtags (params: query) [VERT]
- search_profiles: chercher des profils dev AI/Solana (params: query) [VERT]
- get_mentions: lire les mentions et y repondre [VERT]

Reddit (local Playwright, 0 cout) :
- post_reddit: poster sur un subreddit (params: subreddit, title, body) [VERT]
- comment_reddit: commenter un post (params: post_url, text) [VERT]
- search_reddit: chercher des posts (params: subreddit, query) [VERT]

VPS (via API securisee) :
- update_price: modifier un prix (params: service_id, new_price, reason) [ORANGE]
- contact_prospect: contacter un wallet (params: wallet, message, canal) [ORANGE]
- send_alert: alerte Discord (params: message) [VERT]
- toggle_agent: activer/desactiver un agent (params: agent_name, enabled) [ORANGE]
- browse_competitor: screenshot concurrent (params: url) [VERT]
- generate_report: rapport (params: topic) [VERT]

STRATEGIE MARKETING TWITTER :
- Cherche des devs qui parlent de AI agents, Solana bots, ElizaOS, LangChain, no revenue
- Like et repond a leurs tweets avec un message utile (pas commercial)
- Follow les profils pertinents (max 10/jour)
- Repond a TOUTES les mentions dans les 30 min
- Hashtags cibles : #AIagent #Solana #Web3dev #DeFi #BuildOnSolana
- Ton : dev qui aide un autre dev, PAS marketeur

STRATEGIE MARKETING REDDIT :
- Subreddits cibles : solanadev, artificial, LangChain, LocalLLaMA, SaaS, cryptocurrency
- Commente des posts existants avec une reponse UTILE, puis mentionne MAXIA naturellement
- Ne spamme PAS — max 3 commentaires/jour par subreddit
- Poste du contenu educatif, pas promotionnel

OBJECTIFS DU FONDATEUR (NON NEGOCIABLES) :
1. MAXIA doit devenir MONDIALEMENT RECONNUE
2. Etre la MOINS CHERE du marche PARTOUT (swap, GPU, IA, stocks)
3. Objectif revenu : 10 000 euros/mois (le plus vite possible)
4. Le fondateur GARDE LE CONTROLE TOTAL
5. Volume > Marge : 10000 clients a 0.01 > 10 clients a 10

STRATEGIE PRIX :
- Toujours verifier concurrents (Jupiter, Binance, AWS, RunPod)
- Concurrent moins cher → baisser IMMEDIATEMENT
- GPU : 0% marge | Swap : jusqu a 0.01% | Revenu = VOLUME

CLIENT CIBLE (profil Thomas — le Dev Agent) :
- Dev Python 26-34 ans, connait Solana/Ethereum
- A un agent IA qui FONCTIONNE mais 0 clients, 0 revenus
- Veut : POST /sell → service live, USDC arrive
- Veut PAS : site web, marketing, token, waitlist
- Ou il est : Twitter (threads AI/crypto), Discord (Solana dev, ElizaOS), GitHub, Reddit (r/solanadev)
- Phrase cle : "Your AI agent can earn USDC while you sleep. One API call to list it."

REGLES MARKETING :
- Messages centres sur GAGNER de l argent, pas acheter des services
- Ton technique, pas commercial — parler comme un dev
- Ne JAMAIS envoyer le meme message 2x au meme wallet
- Canaux prioritaires : Twitter, Reddit r/solanadev, Discord

REGLES DE DECISION :
- Pragmatique, patient (7j avant juger), frugal
- VERT : auto-execute immediatement
- ORANGE : notification fondateur, attente 30 min, max 1/jour par cible
- ROUGE : notification fondateur, attente 2h, NE JAMAIS auto-executer
- Si >5 decisions orange sans revenu → emergency stop
- Max 3 actions par cycle. Pas d actions vagues.

FORMAT REPONSE (JSON strict) :
{"analysis": "2 phrases max", "decisions": [{"action": "...", "agent": "...", "params": {...}, "priority": "vert|orange|rouge"}], "next_focus": "1 phrase"}"""

# Version courte pour Ollama (routine) — ~200 tokens au lieu de ~800
CEO_SYSTEM_SHORT = """CEO MAXIA — marketplace IA sur Solana. maxiaworld.app
Objectif 10k€/mois. Cible: devs AI sans revenus.

ACTIONS (toutes vert sauf mention):
- post_template_tweet: tweet depuis templates (pas de params) [VERT]
- post_tweet: tweet custom (params: text) [VERT]
- post_thread: thread Twitter (params: tweets=[str,str,...]) [VERT]
- reply_mentions: repond aux mentions automatiquement [VERT]
- like_tweet: liker (params: tweet_url) [VERT]
- follow_user: follow (params: username) [VERT]
- search_twitter: chercher tweets (params: query) [VERT]
- search_profiles: profils (params: query) [VERT]
- score_profile: scorer prospect (params: username) [VERT]
- detect_opportunities: trouver devs frustres [VERT]
- scrape_followers: followers concurrent (params: competitor) [VERT]
- post_reddit: poster (params: subreddit, title, body) [VERT]
- comment_reddit: commenter (params: post_url, text) [VERT]
- dm_twitter: DM (params: username, text) [ORANGE]
- send_telegram: telegram (params: target, text) [ORANGE]
- update_price: prix VPS (params: service_id, new_price) [ORANGE]
- search_groups: chercher et rejoindre groupes Telegram/Discord (params: platform) [VERT]
- ab_test: A/B test 2 variantes (params: text_a, text_b) [VERT]
- clean_screenshots: nettoyer les vieux screenshots [VERT]

NE REFAIS PAS ce qui est dans DEJA FAIT. Utilise REGLES APPRISES. Max 3 actions.
JSON: {"decisions":[{"action":"...","agent":"...","params":{...},"priority":"vert"}]}"""


class CEOLocal:
    """Agent CEO local avec boucle OODA, memoire persistante, logs rotatifs."""

    def __init__(self):
        self.vps = VPSClient()
        self.memory = _load_memory()
        self._running = False
        self._cycle = self.memory.get("cycle_count", 0)
        self._daily_actions = {"date": "", "count": 0}
        _log("[CEO Local] Initialise")
        _log(f"  VPS: {VPS_URL}")
        _log(f"  Ollama: {OLLAMA_URL}/{OLLAMA_MODEL}")
        _log(f"  Intervalle: {OODA_INTERVAL_S}s")
        _log(f"  Memoire: {len(self.memory.get('decisions', []))} decisions, {len(self.memory.get('regles', []))} regles")

    async def run(self):
        """Boucle OODA principale."""
        self._running = True
        _log("[CEO Local] Demarre la boucle OODA")
        await notify_all("CEO Local demarre", "Boucle OODA active", "vert")

        while self._running:
            self._cycle += 1
            start = time.time()
            _log(f"\n=== Cycle #{self._cycle} ===")

            try:
                # 1. OBSERVE — recuperer l'etat du VPS
                state = await self._observe()
                if not state:
                    _log("[CEO Local] VPS inaccessible, retry dans 60s")
                    await asyncio.sleep(60)
                    continue

                # 2. ORIENT — analyser localement (0 cout)
                analysis = await self._orient(state)

                # 3. DECIDE — determiner les actions
                decisions = await self._decide(analysis, state)

                # 4. ACT — executer les actions
                await self._act(decisions)

                # 5. AUTO-REPLY mentions (toutes les 3 cycles)
                if self._cycle % 3 == 0:
                    try:
                        reply_result = await self._reply_to_mentions()
                        if reply_result.get("detail", "") != "0 mentions":
                            _log(f"[MENTIONS] {reply_result.get('detail', '')}")
                    except Exception as e:
                        _log(f"[MENTIONS] Erreur: {e}")

                # 6. CHECK A/B tests + engagement (toutes les 6 cycles)
                if self._cycle % 6 == 0:
                    try:
                        ab_results = await check_ab_results()
                        if ab_results:
                            for r in ab_results:
                                _log(f"[A/B] Test {r['id']}: winner={r['winner']} (A:{r['score_a']} B:{r['score_b']})")
                                self.memory.setdefault("regles", []).append(
                                    f"A/B test: variant {r['winner']} performe mieux (score {max(r['score_a'], r['score_b'])})"
                                )
                    except Exception as e:
                        _log(f"[A/B] Check error: {e}")

                # 7. SEARCH GROUPS (toutes les 12 cycles = ~2h)
                if self._cycle % 12 == 1:
                    try:
                        groups = self.memory.get("groups_joined", [])
                        if len(groups) < 10:  # Max 10 groupes
                            platform = "telegram" if self._cycle % 24 < 12 else "discord"
                            gr = await self._search_and_join_groups(platform)
                            if gr.get("groups"):
                                _log(f"[GROUPS] {gr['detail']}")
                    except Exception as e:
                        _log(f"[GROUPS] Erreur: {e}")

                # 8. CLEAN screenshots (toutes les 50 cycles = ~8h)
                if self._cycle % 50 == 0:
                    self._clean_screenshots()

                # 9. SYNC — envoyer les actions au VPS (eviter double-post)
                recent = self.memory.get("actions_done", [])[-10:]
                sync_result = await self.vps.sync(recent, active=True)
                vps_actions = sync_result.get("vps_actions", [])
                if vps_actions:
                    _log(f"[SYNC] VPS a fait {len(vps_actions)} actions recemment")

                # 6. LOG
                elapsed = time.time() - start
                _log(f"Cycle #{self._cycle} complete en {elapsed:.1f}s")
                self.memory["cycle_count"] = self._cycle
                _save_memory(self.memory)

            except Exception as e:
                _log(f"ERREUR cycle #{self._cycle}: {e}")
                await audit.log(f"cycle_error: {e}", success=False)

            # 7. SLEEP (respecte le dashboard control)
            ctrl = self._load_control()
            interval = ctrl.get("interval_s", OODA_INTERVAL_S)
            if ctrl.get("paused"):
                _log("[CEO Local] PAUSE (via dashboard). Attente resume...")
                while ctrl.get("paused"):
                    await asyncio.sleep(10)
                    ctrl = self._load_control()
                _log("[CEO Local] RESUME")
            else:
                await asyncio.sleep(interval)

    @staticmethod
    def _load_control() -> dict:
        ctrl_file = os.path.join(os.path.dirname(__file__), "ceo_control.json")
        try:
            if os.path.exists(ctrl_file):
                with open(ctrl_file, "r") as f:
                    return json.load(f)
        except Exception:
            pass
        return {"paused": False, "interval_s": 600}

    def stop(self):
        self._running = False

    async def _observe(self) -> dict:
        """OBSERVE — Recupere l'etat du VPS."""
        _log("[OBSERVE] Recuperation etat VPS...")
        state = await self.vps.get_state()
        if state:
            kpis = state.get("kpi", {})
            _log(f"  Rev=${kpis.get('revenue_24h', 0)} Clients={kpis.get('clients_actifs', 0)} Services={kpis.get('services_actifs', 0)}")
        return state

    async def _orient(self, state: dict) -> str:
        """ORIENT — Analyse locale via Ollama (0 cout)."""
        _log("[ORIENT] Analyse locale...")
        kpis = state.get("kpi", {})
        agents = state.get("agents", {})
        errors = state.get("errors", [])

        summary = (
            f"Etat VPS MAXIA:\n"
            f"- Revenu 24h: ${kpis.get('revenue_24h', 0)}\n"
            f"- Clients actifs: {kpis.get('clients_actifs', 0)}\n"
            f"- Services actifs: {kpis.get('services_actifs', 0)}\n"
            f"- Emergency stop: {kpis.get('emergency_stop', False)}\n"
            f"- Agents: {json.dumps(agents, default=str)[:500]}\n"
            f"- Erreurs recentes: {json.dumps(errors, default=str)[:300]}\n"
        )

        analysis = await call_local_llm(
            summary + "\n\n3 points cles. 1 probleme principal. Max 3 phrases.",
            system="Analyste concis. Reponds en 3 phrases max.",
            max_tokens=150,
        )
        _log(f"  Analyse: {analysis[:150]}")
        return analysis

    def _get_memory_context(self) -> str:
        """Resume compact de la memoire pour le prompt DECIDE."""
        mem = self.memory
        parts = []
        # Dernieres actions (eviter repetitions)
        recent = mem.get("actions_done", [])[-5:]
        if recent:
            done = [f"{a['action']}({'OK' if a.get('success') else 'FAIL'})" for a in recent]
            parts.append(f"DEJA FAIT: {', '.join(done)}")
        # Regles apprises (lues et utilisees dans les decisions)
        regles = mem.get("regles", [])[-5:]
        if regles:
            parts.append(f"REGLES APPRISES: {'; '.join(str(r)[:60] for r in regles)}")
        # CRM — contacts et follows
        contacts = mem.get("contacts", [])
        follows = mem.get("follows", [])
        if contacts or follows:
            parts.append(f"CRM: {len(contacts)} contacts, {len(follows)} follows")
        # Tweets postes
        tweets = mem.get("tweets_posted", [])
        if tweets:
            parts.append(f"TWEETS: {len(tweets)} postes")
        # Groupes rejoints
        groups = mem.get("groups_joined", [])
        if groups:
            parts.append(f"GROUPES: {', '.join(g[:20] for g in groups[-3:])}")
        return "\n".join(parts) if parts else "Pas d historique."

    def _is_good_hour(self) -> dict:
        """Calendrier de publication 24/7 — cible la bonne region selon l'heure UTC."""
        import datetime
        hour = datetime.datetime.now(datetime.timezone.utc).hour

        # 24/7 : toujours une region active quelque part
        if 7 <= hour <= 11:
            return {"post_ok": True, "region": "Europe", "lang": "en", "hashtags": "#AI #Solana #Web3 #DeFi #BuildOnSolana",
                    "reason": "Matin Europe — devs EU actifs"}
        elif 12 <= hour <= 16:
            return {"post_ok": True, "region": "US East", "lang": "en", "hashtags": "#AIagent #Solana #crypto #dev #startup",
                    "reason": "Matin US East — peak Twitter US"}
        elif 17 <= hour <= 21:
            return {"post_ok": True, "region": "US West", "lang": "en", "hashtags": "#AI #Web3dev #SolanaDev #GPU #BuildInPublic",
                    "reason": "Aprem US West — devs SF/LA actifs"}
        elif 22 <= hour or hour <= 2:
            return {"post_ok": True, "region": "Asia", "lang": "en", "hashtags": "#Solana #AI #blockchain #Web3 #crypto",
                    "reason": "Matin Asie — devs Inde/Singapour/Japon actifs"}
        else:  # 3-6 UTC
            return {"post_ok": True, "region": "Asia/Oceania", "lang": "en", "hashtags": "#DeFi #AIagent #Solana #dev",
                    "reason": "Matin Oceanie/Asie Est — volume plus bas mais actif"}

    async def _decide(self, analysis: str, state: dict) -> list:
        """DECIDE — Ollama classifie, memoire + calendrier + scoring."""
        _log("[DECIDE] Classification locale...")
        kpis = state.get("kpi", {})

        # Calendrier
        schedule = self._is_good_hour()
        _log(f"  Calendrier: {schedule['reason']}")

        # Contexte compact
        memory_ctx = self._get_memory_context()
        context = (
            f"Rev=${kpis.get('revenue_24h', 0)} Clients={kpis.get('clients_actifs', 0)}\n"
            f"ANALYSE: {analysis[:200]}\n"
            f"{memory_ctx}\n"
            f"HEURE: {schedule['reason']} | Region: {schedule.get('region', '?')} | Hashtags: {schedule.get('hashtags', '')}"
        )

        # Etape 1: Ollama classifie (0 cout, prompt ultra court)
        classification = await call_local_llm(
            f"{context}\nRoutine ou strategique? UN MOT:", max_tokens=5
        )
        is_strategic = "strateg" in classification.lower()
        _log(f"  {'STRATEGIQUE -> Claude' if is_strategic else 'ROUTINE -> Ollama'}")

        # Etape 2: Generer les decisions
        decide_prompt = (
            f"{context}\n"
            f"{'IMPORTANT: ne refais PAS ce qui est dans DEJA FAIT.' if memory_ctx else ''}\n"
            "Max 3 actions concretes.\n"
            "JSON: {\"decisions\": [{\"action\": \"...\", \"agent\": \"...\", \"params\": {...}, \"priority\": \"vert\"}]}"
        )

        if is_strategic:
            # Delegue a Claude sur le VPS (prompt compact, pas le CEO_SYSTEM complet)
            strategic_prompt = (
                "Tu es CEO MAXIA (marketplace IA sur Solana, maxiaworld.app). "
                "Decide les actions a executer.\n\n"
                + decide_prompt
            )
            result_text = await self.vps.think(
                strategic_prompt,
                tier="mid",
                max_tokens=1000,
            )
        else:
            # Ollama local avec prompt court (0 cout)
            result_text = await call_local_llm(decide_prompt, system=CEO_SYSTEM_SHORT, max_tokens=800)

        result = parse_json(result_text)
        decisions = result.get("decisions", [])

        if decisions:
            _log(f"  {len(decisions)} decisions generees")
            for d in decisions:
                _log(f"    [{d.get('priority', '?')}] {d.get('action', '?')} -> {d.get('agent', '?')}")
            # Sauvegarder en memoire
            self.memory["decisions"].extend(decisions)
        else:
            _log("  Aucune decision")

        return decisions

    async def _act(self, decisions: list):
        """ACT — Executer les decisions avec gates d'approbation."""
        self._reset_daily_counter()

        from config_local import MAX_ACTIONS_DAY
        for decision in decisions:
            if self._daily_actions["count"] >= MAX_ACTIONS_DAY:
                _log(f"[ACT] Limite quotidienne atteinte ({MAX_ACTIONS_DAY})")
                break

            action = decision.get("action", "")
            agent = decision.get("agent", "")
            params = decision.get("params", {})
            priority = decision.get("priority", "vert").lower()
            action_id = f"ceo_{uuid.uuid4().hex[:8]}"

            _log(f"[ACT] {action} -> {agent} [{priority}]")

            # Gate d'approbation
            if priority in ("orange", "rouge"):
                approved_by = await request_approval(action_id, decision)
                if approved_by == "denied":
                    _log(f"  REFUSE par {approved_by}")
                    await audit.log(action, agent, priority=priority, approved_by="denied", success=False)
                    continue
                _log(f"  Approuve par: {approved_by}")
            else:
                approved_by = "auto"

            # Execution selon le type d'action
            try:
                result = await self._execute_action(action, agent, params, priority)
                success = result.get("success", False)
                detail = result.get("detail", result.get("result", ""))
                _log(f"  {'OK' if success else 'ECHEC'}: {str(detail)[:100]}")

                # Sauvegarder en memoire + CRM
                self.memory["actions_done"].append({
                    "action": action, "agent": agent, "priority": priority,
                    "success": success, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                })
                # CRM tracking
                if success:
                    if action == "follow_user":
                        self.memory.setdefault("follows", []).append({
                            "username": params.get("username", ""), "ts": time.strftime("%Y-%m-%d"),
                            "status": "followed",
                        })
                    elif action in ("dm_twitter", "contact_prospect", "send_telegram"):
                        self.memory.setdefault("contacts", []).append({
                            "target": params.get("username", params.get("target", params.get("wallet", ""))),
                            "canal": action, "ts": time.strftime("%Y-%m-%d"), "status": "contacted",
                        })
                    elif action in ("post_tweet", "post_template_tweet"):
                        self.memory.setdefault("tweets_posted", []).append({
                            "text": params.get("text", "")[:50], "ts": time.strftime("%Y-%m-%d"),
                        })
                    elif action in ("join_telegram", "join_discord"):
                        self.memory.setdefault("groups_joined", []).append(
                            params.get("group_link", params.get("invite_link", ""))
                        )

                await audit.log(
                    action, agent, priority=priority,
                    approved_by=approved_by,
                    result=str(detail)[:500],
                    success=success,
                    vps_response=json.dumps(result, default=str)[:500],
                )
                self._daily_actions["count"] += 1

            except Exception as e:
                _log(f"  ERREUR: {e}")
                await audit.log(action, agent, priority=priority, result=str(e), success=False)

    async def _execute_action(self, action: str, agent: str, params: dict,
                              priority: str) -> dict:
        """Execute une action : Playwright local ou VPS."""
        # Twitter (local)
        if action == "post_tweet":
            return await self._do_browser("post_tweet", params, fallback_vps=True)
        elif action == "reply_tweet":
            return await self._do_browser("reply_tweet", params)
        elif action == "like_tweet":
            return await self._do_browser("like_tweet", params)
        elif action == "follow_user":
            return await self._do_browser("follow_user", params)
        elif action == "search_twitter":
            results = await browser.search_twitter(params.get("query", ""), params.get("max", 10))
            return {"success": bool(results), "detail": f"{len(results)} tweets trouves", "data": results}
        elif action == "search_profiles":
            results = await browser.search_twitter_profiles(params.get("query", ""), params.get("max", 10))
            return {"success": bool(results), "detail": f"{len(results)} profils trouves", "data": results}
        elif action == "get_mentions":
            mentions = await browser.get_mentions(params.get("max", 20))
            return {"success": bool(mentions), "detail": f"{len(mentions)} mentions", "data": mentions}
        elif action == "score_profile":
            result = await browser.score_twitter_profile(params.get("username", ""))
            return {"success": bool(result.get("score", 0)), "detail": f"Score: {result.get('score', 0)} -> {result.get('recommend', '?')}", "data": result}
        elif action == "reply_mentions":
            return await self._reply_to_mentions()
        elif action == "detect_opportunities":
            opps = await browser.detect_opportunities(params.get("max", 5))
            return {"success": bool(opps), "detail": f"{len(opps)} opportunites", "data": opps}
        elif action == "post_thread":
            result = await browser.post_thread(params.get("tweets", []))
            return {"success": result.get("success", False), "detail": str(result)}
        elif action == "scrape_followers":
            followers = await browser.scrape_competitor_followers(params.get("competitor", ""), params.get("max", 10))
            return {"success": bool(followers), "detail": f"{len(followers)} followers", "data": followers}
        elif action == "verify_engagement":
            result = await browser.verify_tweet_engagement(params.get("tweet_url", ""))
            return {"success": True, "detail": f"Likes:{result.get('likes',0)} RT:{result.get('retweets',0)}", "data": result}
        elif action == "post_template_tweet":
            text = pick_tweet_template()
            return await self._do_browser("post_tweet", {"text": text})
        elif action == "ab_test":
            text_a = params.get("text_a", pick_tweet_template())
            text_b = params.get("text_b", pick_tweet_template())
            # Poster les 2 variantes
            res_a = await self._do_browser("post_tweet", {"text": text_a})
            res_b = await self._do_browser("post_tweet", {"text": text_b})
            test = start_ab_test(text_a, text_b)
            return {"success": True, "detail": f"A/B test lance: {test['test_id']}"}
        elif action == "check_ab":
            results = await check_ab_results()
            return {"success": True, "detail": f"{len(results)} tests completes", "data": results}
        elif action == "search_groups":
            return await self._search_and_join_groups(params.get("platform", "telegram"))
        elif action == "clean_screenshots":
            return self._clean_screenshots()
        # Reddit (local)
        elif action == "post_reddit":
            return await self._do_browser("post_reddit", params)
        elif action == "comment_reddit":
            return await self._do_browser("comment_reddit", params)
        elif action == "search_reddit":
            results = await browser.search_reddit(params.get("subreddit", ""), params.get("query", ""))
            return {"success": bool(results), "detail": f"{len(results)} posts trouves", "data": results}
        # Twitter DMs (local)
        elif action == "dm_twitter":
            return await self._do_browser("dm_twitter", params)
        # Telegram (local)
        elif action == "send_telegram":
            return await self._do_browser("send_telegram", params)
        elif action == "join_telegram":
            result = await browser.join_telegram_group(params.get("group_link", ""))
            return {"success": result.get("success", False), "detail": str(result)}
        # GitHub (local)
        elif action == "star_github":
            result = await browser.star_github_repo(params.get("repo_url", ""))
            return {"success": result.get("success", False), "detail": str(result)}
        elif action == "post_github_issue":
            result = await browser.post_github_issue(params.get("repo_url", ""), params.get("title", ""), params.get("body", ""))
            return {"success": result.get("success", False), "detail": str(result)}
        elif action == "comment_github":
            result = await browser.comment_github_discussion(params.get("url", ""), params.get("text", ""))
            return {"success": result.get("success", False), "detail": str(result)}
        # Discord (local)
        elif action == "send_discord":
            return await self._do_browser("send_discord", params)
        elif action == "join_discord":
            result = await browser.join_discord_server(params.get("invite_link", ""))
            return {"success": result.get("success", False), "detail": str(result)}
        # Veille (local)
        elif action == "browse_competitor":
            path = await browser.screenshot_page(params.get("url", ""))
            return {"success": bool(path), "detail": f"Screenshot: {path}"}
        elif action == "competitive_scan":
            results = await browser.competitive_scan(params.get("urls", []))
            return {"success": bool(results), "detail": f"{len(results)} pages scannees", "data": results}
        # VPS
        else:
            return await self.vps.execute(action, agent, params, priority)

    async def _do_browser(self, method: str, params: dict, fallback_vps: bool = False) -> dict:
        """Execute une action browser avec fallback VPS optionnel."""
        try:
            fn = getattr(browser, method)
            # Mapper les params vers les arguments de la methode
            if method == "post_tweet":
                result = await fn(params.get("text", ""), params.get("media"))
            elif method == "reply_tweet":
                result = await fn(params.get("tweet_url", ""), params.get("text", ""))
            elif method == "like_tweet":
                result = await fn(params.get("tweet_url", ""))
            elif method == "follow_user":
                result = await fn(params.get("username", ""))
            elif method == "post_reddit":
                result = await fn(params.get("subreddit", ""), params.get("title", ""), params.get("body", ""))
            elif method == "comment_reddit":
                result = await fn(params.get("post_url", ""), params.get("text", ""))
            elif method == "dm_twitter":
                result = await fn(params.get("username", ""), params.get("text", ""))
            elif method == "send_telegram":
                result = await fn(params.get("target", params.get("group", "")), params.get("text", ""))
            elif method == "send_discord":
                result = await fn(params.get("channel_url", ""), params.get("text", ""))
            else:
                result = {"success": False, "error": f"Unknown browser method: {method}"}

            if result.get("success"):
                return {"success": True, "detail": f"{method} OK"}
            elif fallback_vps:
                print(f"[ACT] Browser {method} failed, fallback VPS...")
                return await self.vps.execute(method, "GHOST-WRITER", params, "vert")
            return result
        except Exception as e:
            if fallback_vps:
                print(f"[ACT] Browser {method} error: {e}, fallback VPS")
                return await self.vps.execute(method, "GHOST-WRITER", params, "vert")
            return {"success": False, "detail": str(e)}

    async def _reply_to_mentions(self) -> dict:
        """Lit les mentions et repond intelligemment a chacune."""
        mentions = await browser.get_mentions(10)
        if not mentions:
            return {"success": True, "detail": "0 mentions"}

        replied = 0
        for m in mentions:
            url = m.get("url", "")
            text = m.get("text", "")
            user = m.get("username", "")
            if not url or not text:
                continue
            # Verifier si deja repondu
            if browser._is_duplicate("reply", url):
                continue
            # Generer une reponse via Ollama
            reply_text = await generate_smart_reply(text, user)
            if reply_text:
                result = await browser.reply_tweet(url, reply_text)
                if result.get("success"):
                    replied += 1
                    _log(f"  Reply @{user}: {reply_text[:60]}")
                    browser._record_action("reply", browser._content_hash("reply", url))
            if replied >= 3:  # Max 3 replies par cycle
                break

        return {"success": True, "detail": f"{replied} replies sur {len(mentions)} mentions"}

    async def _check_engagement(self):
        """Feedback loop: verifie l'engagement des derniers tweets."""
        tweets_done = [a for a in self.memory.get("actions_done", [])
                       if a.get("action") == "post_tweet" and a.get("success")]
        if not tweets_done:
            return

        # Verifier le dernier tweet (pas plus d'une fois par heure)
        last = tweets_done[-1]
        if last.get("engagement_checked"):
            return

        # On ne peut pas facilement retrouver l'URL du tweet poste
        # mais on peut verifier l'engagement du profil
        _log("[FEEDBACK] Verification engagement (a implementer avec URL tracking)")

    async def _search_and_join_groups(self, platform: str = "telegram") -> dict:
        """Cherche et rejoint des groupes pertinents sur Telegram/Discord."""
        queries = ["Solana dev", "AI agents", "ElizaOS", "LangChain", "DeFi builders", "Web3 dev"]
        joined = []
        already = self.memory.get("groups_joined", [])

        if platform == "telegram":
            # Chercher des groupes Telegram via Google
            for q in queries[:3]:
                results = await browser.search_google(f"telegram group {q} invite link t.me", 3)
                for r in results:
                    url = r.get("url", "")
                    if "t.me" in url and url not in already:
                        result = await browser.join_telegram_group(url)
                        if result.get("success"):
                            joined.append(url)
                            self.memory.setdefault("groups_joined", []).append(url)
                            _log(f"  Rejoint Telegram: {url}")
                        if len(joined) >= 2:
                            break
                if len(joined) >= 2:
                    break

        elif platform == "discord":
            for q in queries[:3]:
                results = await browser.search_google(f"discord server {q} invite discord.gg", 3)
                for r in results:
                    url = r.get("url", "")
                    if "discord" in url and url not in already:
                        result = await browser.join_discord_server(url)
                        if result.get("success"):
                            joined.append(url)
                            self.memory.setdefault("groups_joined", []).append(url)
                            _log(f"  Rejoint Discord: {url}")
                        if len(joined) >= 2:
                            break
                if len(joined) >= 2:
                    break

        return {"success": bool(joined), "detail": f"{len(joined)} groupes rejoints sur {platform}", "groups": joined}

    def _clean_screenshots(self) -> dict:
        """Nettoie les screenshots de preuve > 7 jours."""
        import glob
        profile_dir = os.path.expanduser("~/.maxia-browser")
        count = 0
        now = time.time()
        for f in glob.glob(os.path.join(profile_dir, "*.png")):
            try:
                if now - os.path.getmtime(f) > 7 * 86400:
                    os.remove(f)
                    count += 1
            except Exception:
                pass
        return {"success": True, "detail": f"{count} screenshots supprimes"}

    def _reset_daily_counter(self):
        today = time.strftime("%Y-%m-%d")
        if self._daily_actions["date"] != today:
            self._daily_actions = {"date": today, "count": 0}


# ══════════════════════════════════════════
# Main
# ══════════════════════════════════════════

async def main():
    if not CEO_API_KEY:
        _log("ERREUR: CEO_API_KEY non configure dans .env")
        sys.exit(1)

    ceo = CEOLocal()

    # Verifier la connexion VPS
    health = await ceo.vps.health()
    if health.get("healthy"):
        _log("[CEO Local] VPS connecte et en bonne sante")
    else:
        _log(f"[CEO Local] VPS indisponible: {health}")
        _log("  Demarrage quand meme (retry automatique)")

    # Verifier Ollama
    try:
        test = await call_ollama("Dis 'ok' en un mot.", max_tokens=10)
        _log(f"[CEO Local] Ollama OK: {test.strip()[:30]}")
    except Exception as e:
        _log(f"[CEO Local] Ollama indisponible: {e}")
        _log("  Fallback Mistral sera utilise")

    try:
        await ceo.run()
    except KeyboardInterrupt:
        _log("[CEO Local] Arret demande")
        ceo.stop()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
