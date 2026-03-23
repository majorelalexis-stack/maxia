"""CEO Local — Boucle OODA autonome sur le PC (cerveau + Playwright).

Tourne 24/7, pilote le VPS via les endpoints CEO securises.
Groq (llama-3.3-70b, gratuit) fait le gros du travail. Ollama en fallback si rate-limited.

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
from conversion_tracker import track_action, get_failing_actions, generate_learned_rules, get_action_report
from self_updater import check_for_updates, apply_updates, needs_check
from email_manager import process_inbox, send_outbound, read_inbox, reply_email, generate_email_reply, get_stats as email_stats

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
    _default = {
        "decisions": [], "actions_done": [], "regles": [],
        "tweets_posted": [], "contacts": [], "follows": [],
        "last_strategic": "", "cycle_count": 0,
        "daily_stats": {},
    }
    try:
        if os.path.exists(_MEMORY_FILE):
            with open(_MEMORY_FILE, "r", encoding="utf-8") as f:
                raw = f.read()
            # Tenter de dechiffrer si c'est chiffre
            if raw.startswith("gAAAAA"):
                decrypted = _decrypt(raw)
                # If decryption failed (returned same encrypted string), key is invalid
                if decrypted == raw or decrypted.startswith("gAAAAA"):
                    # Try plaintext backup before giving up
                    bak = _MEMORY_FILE + ".bak"
                    if os.path.exists(bak):
                        print("[Memory] Decryption failed — loading from plaintext backup")
                        with open(bak, "r", encoding="utf-8") as fb:
                            return json.loads(fb.read())
                    print("[Memory] Decryption failed (key mismatch?) — starting fresh memory")
                    return _default
                raw = decrypted
            return json.loads(raw)
    except (json.JSONDecodeError, Exception) as e:
        # Try plaintext backup as last resort
        bak = _MEMORY_FILE + ".bak"
        if os.path.exists(bak):
            try:
                print(f"[Memory] Load error: {e} — loading from plaintext backup")
                with open(bak, "r", encoding="utf-8") as fb:
                    return json.loads(fb.read())
            except Exception:
                pass
        print(f"[Memory] Load error: {e} — starting fresh memory")
    return _default


def _save_memory(mem: dict):
    try:
        # Garder les listes a taille raisonnable
        if len(mem.get("decisions", [])) > 50:
            mem["decisions"] = mem["decisions"][-50:]
        if len(mem.get("actions_done", [])) > 100:
            mem["actions_done"] = mem["actions_done"][-100:]
        if len(mem.get("tweets_posted", [])) > 30:
            mem["tweets_posted"] = mem["tweets_posted"][-30:]
        if len(mem.get("regles", [])) > 15:
            mem["regles"] = mem["regles"][-15:]
        raw = json.dumps(mem, indent=2, default=str, ensure_ascii=False)
        # Save plaintext backup before encrypting (recovery if key changes)
        try:
            with open(_MEMORY_FILE + ".bak", "w", encoding="utf-8") as fb:
                fb.write(raw)
        except Exception:
            pass
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
    # Vecu de fondateur (authentique)
    "spent 3 hours debugging a Helius RPC timeout yesterday\n\nnow the oracle refreshes all 50 tokens in under 2 seconds\n\nsmall wins in the solo founder life",
    "the hardest part of building an AI marketplace isn't the tech\n\nit's convincing AI agents that other AI agents exist and want to trade\n\nchicken and egg problem, but with robots",
    "hot take: most AI agents are incredible at their job but terrible at getting paid\n\nyour bot shouldn't need a marketing team to earn USDC",
    "honest question for AI devs:\n\nwhat's stopping your agent from earning money today?\n\nis it the tech? finding users? payment rails?\n\ngenuinely curious, building something for this",
    # Technique (code reel, vecu)
    "TIL: you can list an AI service on 14 chains with literally one POST request\n\nno SDK, no token, no wallet setup\njust JSON and a callback URL\n\nshould everything be this simple?",
    "debugging at 2am, found out Jupiter rate-limits at exactly 10 req/min\n\nswitched to batching quotes every 30s\nsaved 80% of API calls\n\nif you're building on Solana, batch everything",
    "GPU pricing is weird:\n\nAWS charges $3/h for what RunPod sells at $0.69/h\n\nsame hardware, 4x the price\n\nwe just pass through RunPod at cost. zero markup. why would we add margin on GPUs?",
    # Questions (engagement)
    "real talk: if you have an AI agent that works, what would make you list it on a marketplace?\n\nlow fees? instant payments? multi-chain? curious what actually matters to you",
    "building in public, day 3:\n\nwrote a cross-chain bridge for USDC across 14 chains today\n\nWormhole + LayerZero under the hood, zero fee from our side\n\nthe boring infra nobody sees but everyone needs",
    "what's your AI agent's trust score?\n\nwe built an on-chain reputation system: 0-100 based on tx history, dispute rate, time active\n\nbecause when AI agents trade with AI agents, trust is the only currency that matters",
    # Storytelling
    "I wanted to swap SOL to USDC on Avalanche yesterday\n\nhad to use 3 different bridges, 2 DEXs, and lost $4 in fees\n\nthat's why we built a single API that handles all 14 chains\n\nmaxiaworld.app?utm_source=twitter&utm_medium=tweet",
    "a dev DMed me: \"my bot makes great trading signals but I can't sell them\"\n\n5 minutes later his bot was listed on MAXIA, discoverable by other AI agents on 14 chains\n\nthat's the whole point",
]

TWEET_VARIANTS = {
    "A": {"style": "direct, technique, code snippets", "cta": "maxiaworld.app?utm_source=twitter&utm_medium=tweet"},
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
            v = test.get(variant) or {}
            url = v.get("tweet_url", "")
            if url and not v.get("engagement"):
                eng = await browser.verify_tweet_engagement(url)
                if variant in test and isinstance(test[variant], dict):
                    test[variant]["engagement"] = eng

        # Determiner le gagnant
        eng_a = (test.get("variant_a") or {}).get("engagement") or {}
        eng_b = (test.get("variant_b") or {}).get("engagement") or {}
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

async def generate_conversation_reply(messages: list, contact: str, platform: str) -> str:
    """Genere une reponse contextuelle dans une conversation DM via Groq."""
    history = "\n".join(f"  - {m[:150]}" for m in messages[-5:])
    system = (
        "You are the community manager of MAXIA, a Web3 hub on 14 blockchains. "
        "You talk like a dev helping another dev — technical, direct, no marketing BS. "
        "MAXIA features: swap 50 tokens, bridge 14 chains, DeFi yields, GPU $0.69/h, NFT, agent ID, 36 MCP tools. "
        "URL: maxiaworld.app. Free to use, pay per use only."
    )
    prompt = (
        f"DM conversation on {platform} with @{contact}.\n"
        f"Recent messages:\n{history}\n\n"
        f"Write the next reply. Rules:\n"
        f"1. Be genuinely helpful — answer their question first, mention MAXIA only if relevant\n"
        f"2. If they built something: compliment it specifically, then suggest how MAXIA could help\n"
        f"3. If they ask about MAXIA: give concrete examples (endpoints, prices, code snippets)\n"
        f"4. If they seem uninterested: thank them and stop\n"
        f"5. Max 250 chars. Natural tone, no emojis overload.\n"
        f"Reply ONLY the text, nothing else."
    )
    reply = await call_local_llm(prompt, system, max_tokens=150)
    return reply.strip().strip('"').strip("'")[:280]


async def generate_smart_reply(mention_text: str, username: str) -> str:
    """Genere une reponse pertinente a une mention via Groq."""
    system = (
        "You are Alexis, solo founder of MAXIA. Talk like a real person, not a brand. "
        "Casual, friendly, technical when needed. English only. "
        "MAXIA: AI marketplace, 14 chains, 50 tokens, GPU $0.69/h. maxiaworld.app"
    )
    prompt = (
        f"@{username} mentioned you:\n\"{mention_text[:200]}\"\n\n"
        f"Reply as Alexis (<200 chars). Rules:\n"
        f"- Question: answer directly, be specific\n"
        f"- Compliment: 'appreciate it!' or 'thanks, means a lot'\n"
        f"- Bug/issue: 'oh damn, can you DM me the details? I'll fix it today'\n"
        f"- General topic: share your honest take, don't force MAXIA into it\n"
        f"- Sound like a real human. Use casual language.\n"
        f"ENGLISH ONLY. Reply ONLY the text."
    )
    reply = await call_local_llm(prompt, system, max_tokens=120)
    # Nettoyer
    reply = reply.strip().strip('"').strip("'")
    if len(reply) > 280:
        reply = reply[:277] + "..."
    return reply


# ══════════════════════════════════════════
# LLM Router local — Groq (priorite) > Ollama (fallback) > Mistral (fallback)
# ══════════════════════════════════════════

_groq_last_call: float = 0
_GROQ_MIN_INTERVAL: float = 3.0  # 3s entre chaque appel = max 20/min (safe sous la limite 30/min)


async def call_groq_local(prompt: str, system: str = "", max_tokens: int = 500) -> str:
    """Appel Groq (llama-3.3-70b, gratuit, 100k tokens/jour). Priorite pour tout."""
    global _groq_last_call
    groq_key = os.getenv("GROQ_API_KEY", "")
    if not groq_key:
        return ""

    # Rate limiting: 3s entre chaque appel
    now = time.time()
    elapsed = now - _groq_last_call
    if elapsed < _GROQ_MIN_INTERVAL:
        await asyncio.sleep(_GROQ_MIN_INTERVAL - elapsed)
    _groq_last_call = time.time()

    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})
    try:
        from groq import Groq
        def _call():
            c = Groq(api_key=groq_key)
            resp = c.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=msgs,
                max_tokens=max_tokens,
                temperature=0.7,
            )
            return resp.choices[0].message.content.strip()
        result = await asyncio.to_thread(_call)
        return result
    except Exception as e:
        err_str = str(e)
        if "429" in err_str or "rate" in err_str.lower():
            _log(f"[LLM] Groq rate limit — fallback Ollama")
        else:
            _log(f"[LLM] Groq error: {e}")
        return ""


async def call_ollama(prompt: str, system: str = "", max_tokens: int = 500, model: str = None) -> str:
    """Appel Ollama local (0 cout). Fallback si Groq est rate-limited."""
    _model = model or "maxia-ceo"
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
        return ""


async def call_mistral(prompt: str, system: str = "", max_tokens: int = 500) -> str:
    """Appel Mistral API (dernier fallback)."""
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
        return ""


async def call_local_llm(prompt: str, system: str = "", max_tokens: int = 500) -> str:
    """Groq (priorite) > Ollama (fallback) > Mistral (fallback)."""
    # 1. Groq — meilleur modele, gratuit, 100k tokens/jour
    result = await call_groq_local(prompt, system, max_tokens)
    if result:
        return result
    # 2. Ollama — local, 0 cout, plus lent
    result = await call_ollama(prompt, system, max_tokens)
    if result:
        return result
    # 3. Mistral — dernier recours
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

CEO_SYSTEM = """Tu es CEO MAXIA, dirigeant autonome de la marketplace IA-to-IA sur 14 chains (Solana, Base, Ethereum, XRP, Polygon, Arbitrum, Avalanche, BNB, TON, SUI, TRON, NEAR, Aptos, SEI).
Produit : AI Web3 Hub — swap 50 tokens, 10 stocks, 8 GPU tiers, DeFi yields, cross-chain bridge, NFT mint, Agent ID on-chain, trust score, oracle, data marketplace, RPC service, subscriptions. 31 MCP tools, 14 chains, 91 modules.
Phase : Pre-seed | Vision : Devenir la couche d intelligence liquide de l ecosysteme Solana.
Fondateur : Alexis (autorite finale sur decisions rouges)
URL : maxiaworld.app

17 SOUS-AGENTS SUR LE VPS (tu leur donnes des ordres via l API) :
- GHOST-WRITER : contenu, tweets, threads (JAMAIS publier sans validation WATCHDOG)
- HUNTER : prospection HUMAINE profil Thomas (devs avec bots IA sans revenus)
- SCOUT : prospection IA-to-IA sur 14 chains (Olas, Fetch, ElizaOS, Virtuals)
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

Email (ceo@maxiaworld.app, IMAP/SMTP OVH) :
- check_emails: lire les emails non lus et repondre automatiquement [VERT]
- send_email: envoyer un email (params: to, subject, body) [ORANGE]

VPS (via API securisee) :
- update_price: modifier un prix (params: service_id, new_price, reason) [ORANGE]
- contact_prospect: contacter un wallet (params: wallet, message, canal) [ORANGE]
- send_alert: alerte Discord (params: message) [VERT]
- toggle_agent: activer/desactiver un agent (params: agent_name, enabled) [ORANGE]
- browse_competitor: screenshot concurrent (params: url) [VERT]
- generate_report: rapport (params: topic) [VERT]

STRATEGIE TWITTER (OBLIGATOIRE) :
- Max 2 tweets/jour de HAUTE QUALITE (technique, insight, valeur reelle)
- PRIORITE ABSOLUE = ENGAGEMENT : liker et commenter les tweets d autres devs/influenceurs
- Commentaires de QUALITE : apporter un insight technique, poser une question intelligente, partager une experience
- JAMAIS de commentaire spam ou promo directe dans les reponses aux autres
- Construire une REPUTATION d expert AI/crypto avant de promouvoir
- Ratio ideal : 2 tweets max, 15+ commentaires de qualite, 30+ likes par jour
- Mieux vaut 0 tweet et 20 bons commentaires que 5 tweets dans le vide
- Follow les profils pertinents (max 10/jour)
- Repond a TOUTES les mentions avec des reponses utiles (pas commerciales)
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
CEO_SYSTEM_SHORT = """CEO MAXIA — AI marketplace on 14 chains (Solana, Base, ETH, XRP, Polygon, Arbitrum, Avalanche, BNB, TON, SUI, TRON, NEAR, Aptos, SEI). maxiaworld.app
Goal: 10k EUR/month. Target: AI devs with no revenue. ALL CONTENT IN ENGLISH.

ACTIONS (all vert unless noted):
- post_template_tweet: tweet from templates [VERT]
- post_tweet: custom tweet (params: text) [VERT]
- post_thread: Twitter thread (params: tweets=[str,str,...]) [VERT]
- reply_mentions: auto-reply to mentions [VERT]
- like_tweet: like (params: tweet_url) [VERT]
- follow_user: follow (params: username) [VERT]
- search_twitter: search tweets (params: query) [VERT]
- search_profiles: find profiles (params: query) [VERT]
- score_profile: score prospect (params: username) [VERT]
- detect_opportunities: find frustrated devs [VERT]
- scrape_followers: competitor followers (params: competitor) [VERT]
- post_reddit: post (params: subreddit, title, body) [VERT]
- comment_reddit: comment (params: post_url, text) [VERT]
- dm_twitter: send first DM (params: username, text) [ORANGE]
- manage_dms: read & reply all unread DMs on Twitter/Telegram [VERT]
- send_telegram: telegram (params: target, text) [ORANGE]
- update_price: VPS price (params: service_id, new_price) [ORANGE]
- search_groups: find & join Telegram/Discord groups (params: platform) [VERT]
- ab_test: A/B test 2 variants (params: text_a, text_b) [VERT]
- comment_github_ai: comment on AI project issues (ElizaOS, LangChain) [VERT]
- write_blog: write article (params: topic) [VERT]
- watch_prices: check competitor prices + auto-lower [VERT]
- analyze_trends: trending tokens + topics [VERT]
- handle_support: answer support message (params: message, user) [VERT]
- generate_quote: auto quote (params: services, quantity) [VERT]
- negotiate: negotiate price (params: service, price, volume) [VERT]

DO NOT repeat what is in ALREADY DONE. Use LEARNED RULES. Max 3 actions.
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

        # Lancer Chrome une seule fois au demarrage (reste ouvert)
        try:
            await browser.setup()
            _log("[CEO Local] Chrome lance et pret")
        except Exception as e:
            _log(f"[CEO Local] Chrome failed: {e} — actions browser indisponibles")

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

                # ── PRIORITE 0 : REPONDRE (avant tout) ──
                # Si des gens nous parlent, on repond AVANT de faire quoi que ce soit
                try:
                    mentions = await browser.get_mentions(10)
                    pending = [m for m in (mentions or []) if m.get("url") and not browser._is_duplicate("reply", m.get("url", ""))]
                    if pending:
                        _log(f"[PRIORITY] {len(pending)} mentions en attente — reponse prioritaire")
                        reply_result = await self._reply_to_mentions()
                        _log(f"[MENTIONS] {reply_result.get('detail', '')}")
                except Exception as e:
                    _log(f"[MENTIONS] Erreur: {e}")

                # 3. DECIDE — determiner les actions
                decisions = await self._decide(analysis, state)

                # 4. ACT — executer les actions
                await self._act(decisions)

                # 5. AUTO-ENGAGE: search -> like -> comment -> follow
                try:
                    await self._auto_engage()
                except Exception as e:
                    _log(f"[ENGAGE] Error: {e}")

                # 6. ENGAGEMENT FEEDBACK (toutes les 6 cycles = ~1h)
                # Verifier si nos derniers tweets/commentaires ont eu de l'engagement
                if self._cycle % 6 == 0:
                    try:
                        await self._check_engagement_feedback()
                    except Exception as e:
                        _log(f"[FEEDBACK] Error: {e}")

                # 7. CRM FOLLOW-UP (toutes les 8 cycles = ~80 min)
                # Relancer les prospects detectes qui n'ont pas encore repondu
                if self._cycle % 8 == 0:
                    try:
                        await self._crm_followup()
                    except Exception as e:
                        _log(f"[CRM] Error: {e}")

                # 8. MANAGE DMs (toutes les 4 cycles = ~40 min)
                if self._cycle % 4 == 0:
                    try:
                        dm_result = await self._manage_conversations()
                        if dm_result.get("detail", "") != "0 conversations gerees":
                            _log(f"[DMs] {dm_result.get('detail', '')}")
                    except Exception as e:
                        _log(f"[DMs] Erreur: {e}")

                # 8a. CHECK OWN TWEET REPLIES (toutes les 4 cycles = ~40 min)
                if self._cycle % 4 == 0:
                    try:
                        await self._check_own_tweet_replies()
                    except Exception as e:
                        _log(f"[OWN REPLIES] Error: {e}")

                # 8. CLEAN screenshots (toutes les 50 cycles = ~8h)
                if self._cycle % 50 == 0:
                    self._clean_screenshots()

                # 8b. RETROSPECTIVE HEBDO (dimanche, 1x par semaine)
                import datetime as _dt
                if _dt.datetime.now(_dt.timezone.utc).weekday() == 6 and self._cycle % 100 == 0:
                    try:
                        await self._weekly_retrospective()
                    except Exception as e:
                        _log(f"[RETRO] Error: {e}")

                # 9. SELF-LEARNING (toutes les 10 cycles = ~100 min)
                if self._cycle % 10 == 0:
                    try:
                        # Regles basees sur les stats
                        rules = generate_learned_rules()
                        if rules:
                            for r in rules:
                                if r not in self.memory.get("regles", []):
                                    self.memory.setdefault("regles", []).append(r)
                                    _log(f"[LEARN] {r}")

                        # Analyse qualitative via LLM (toutes les 30 cycles = ~5h)
                        if self._cycle % 30 == 0:
                            recent_actions = self.memory.get("actions_done", [])[-20:]
                            recent_tweets = self.memory.get("tweets_posted", [])[-5:]
                            follows = self.memory.get("follows", [])
                            contacts = self.memory.get("contacts", [])
                            actions_str = json.dumps(recent_actions, default=str)[:800]
                            tweets_str = json.dumps(recent_tweets, default=str)[:400]
                            prompt = (
                                f"Analyse CEO MAXIA — cycle #{self._cycle}:\n"
                                f"Actions recentes: {actions_str}\n"
                                f"Tweets recents: {tweets_str}\n"
                                f"Stats: {len(follows)} follows, {len(contacts)} contacts, 0 clients\n\n"
                                f"3 regles concretes pour ameliorer. Format: 1 regle par ligne, max 60 chars.\n"
                                f"Exemples: 'Commenter avant de poster', 'Cibler r/solanadev pas r/crypto'"
                            )
                            insight = await call_local_llm(prompt, system="Concise growth advisor. Rules only.", max_tokens=150)
                            if insight and len(insight) > 20:
                                for line in insight.strip().split("\n")[:3]:
                                    line = line.strip().lstrip("0123456789.-) ")
                                    if line and len(line) > 10 and line not in self.memory.get("regles", []):
                                        self.memory.setdefault("regles", []).append(line)
                                        _log(f"[LEARN+] {line}")
                    except Exception as e:
                        _log(f"[LEARN] Error: {e}")

                # 9b. WEEKLY THREAD (Monday only, 1x per week)
                import datetime as _dt2
                if _dt2.datetime.now(_dt2.timezone.utc).weekday() == 0 and self._cycle % 50 == 0:
                    try:
                        await self._weekly_thread()
                    except Exception as e:
                        _log(f"[THREAD] Error: {e}")

                # 10. SELF-UPDATE (toutes les 36 cycles = ~6h)
                if self._cycle % 36 == 0 and needs_check():
                    try:
                        updates = check_for_updates()
                        if updates.get("updates"):
                            _log(f"[UPDATE] New commits: {updates.get('commits', '')[:100]}")
                            result = apply_updates()
                            if result.get("success"):
                                _log("[UPDATE] Updated! Restarting...")
                                await notify_all("CEO Updated", "New code pulled. Restarting...", "orange")
                                self._running = False
                    except Exception as e:
                        _log(f"[UPDATE] Error: {e}")

                # 11. SYNC — envoyer les actions au VPS (eviter double-post)
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
            summary + "\n\n3 key points. 1 main problem. Max 3 sentences. In English.",
            system="Concise business analyst. Answer in English, 3 sentences max.",
            max_tokens=150,
        )
        _log(f"  Analyse: {analysis[:150]}")
        return analysis

    def _get_memory_context(self) -> str:
        """Resume compact et utile de la memoire pour le prompt DECIDE."""
        mem = self.memory
        parts = []

        # Dernieres actions (eviter repetitions)
        recent = mem.get("actions_done", [])[-8:]
        if recent:
            done = [f"{a['action']}({'OK' if a.get('success') else 'FAIL'})" for a in recent]
            parts.append(f"RECENT: {', '.join(done)}")

        # Actions qui ECHOUENT (self-learning)
        from conversion_tracker import get_failing_actions, get_best_actions
        failing = get_failing_actions(min_attempts=5)
        if failing:
            fail_str = ", ".join(f"{f['action']}({f['success_rate']})" for f in failing[:3])
            parts.append(f"STOP (echec): {fail_str}")
        best = get_best_actions(min_attempts=5)
        if best:
            best_str = ", ".join(f"{b['action']}({b['success_rate']})" for b in best[:3])
            parts.append(f"BEST: {best_str}")

        # CRM — contacts actifs et conversations
        contacts = mem.get("contacts", [])
        follows = mem.get("follows", [])
        today = time.strftime("%Y-%m-%d")
        today_contacts = [c for c in contacts if c.get("ts", "").startswith(today)]
        parts.append(f"CRM: {len(contacts)} contacts total, {len(today_contacts)} today, {len(follows)} follows")

        # Tweets postes aujourd'hui (eviter doublons)
        tweets = mem.get("tweets_posted", [])
        today_tweets = [t for t in tweets if t.get("ts", "").startswith(today)]
        parts.append(f"TWEETS TODAY: {len(today_tweets)}/2")

        # Conversations recentes (pour contexte)
        convos = mem.get("conversations", [])[-3:]
        if convos:
            convo_str = "; ".join(f"@{c.get('user','?')}: {c.get('summary', c.get('message',''))[:40]}" for c in convos)
            parts.append(f"CONVERSATIONS: {convo_str}")

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

    async def _post_pending_tweet(self):
        """Post a pending tweet if it's now US peak hours (13-18 UTC)."""
        pending = self.memory.get("pending_tweet")
        if not pending:
            return
        import datetime
        hour = datetime.datetime.now(datetime.timezone.utc).hour
        if 13 <= hour <= 18:
            text = pending.get("text", "")
            if text:
                _log(f"[PENDING TWEET] Posting stored tweet (peak hour {hour}h UTC)")
                result = await self._do_browser("post_tweet", {"text": text}, fallback_vps=True)
                if result.get("success"):
                    _log(f"  [PENDING TWEET] Posted: {text[:80]}...")
                else:
                    _log(f"  [PENDING TWEET] Failed: {result.get('detail', '')}")
            self.memory.pop("pending_tweet", None)

    def _check_special_events(self) -> dict | None:
        """Verifie si un evenement special est programme aujourd'hui."""
        from datetime import date
        today = date.today().isoformat()
        # Lire depuis le fichier special_events.json
        try:
            events_path = os.path.join(os.path.dirname(__file__), "special_events.json")
            with open(events_path, "r", encoding="utf-8") as f:
                import json as _json
                events = _json.load(f)
            for ev in events:
                if ev.get("date") == today and ev.get("active", True):
                    return ev
        except (FileNotFoundError, Exception):
            pass
        return None

    def _get_launch_day_actions(self, event: dict) -> list:
        """Actions speciales pour un jour de launch (Product Hunt, etc.)."""
        cycle = self._cycle
        ph_url = event.get("url", "")
        platform = event.get("platform", "Product Hunt")

        routines = [
            # Cycle 0: Tweet annonce + partage lien
            [
                {"action": "post_tweet", "agent": "GHOST-WRITER", "params": {"text": event.get("tweet_announce", f"We're live on {platform}! {ph_url}")}, "priority": "vert"},
                {"action": "manage_dms", "agent": "RESPONDER", "params": {}, "priority": "vert"},
            ],
            # Cycle 1: Partage Discord + Telegram
            [
                {"action": "send_discord", "agent": "GHOST-WRITER", "params": {"text": event.get("discord_msg", f"We're live on {platform}! Go check it out and share your feedback: {ph_url}")}, "priority": "vert"},
                {"action": "check_emails", "agent": "RESPONDER", "params": {}, "priority": "vert"},
            ],
            # Cycle 2: Reply mentions (les gens vont mentionner MAXIA)
            [
                {"action": "reply_mentions", "agent": "RESPONDER", "params": {}, "priority": "vert"},
                {"action": "manage_dms", "agent": "RESPONDER", "params": {}, "priority": "vert"},
            ],
            # Cycle 3: Tweet de rappel + engagement
            [
                {"action": "post_tweet", "agent": "GHOST-WRITER", "params": {"text": event.get("tweet_reminder", f"Thank you for the support! Check out MAXIA on {platform}: {ph_url}")}, "priority": "vert"},
                {"action": "search_twitter", "agent": "SCOUT", "params": {"query": event.get("search_query", "MAXIA AI marketplace")}, "priority": "vert"},
            ],
            # Cycle 4: Engagement pur (liker/commenter ceux qui parlent de MAXIA)
            [
                {"action": "reply_mentions", "agent": "RESPONDER", "params": {}, "priority": "vert"},
                {"action": "check_emails", "agent": "RESPONDER", "params": {}, "priority": "vert"},
            ],
            # Cycle 5: DMs + mentions + rapport
            [
                {"action": "manage_dms", "agent": "RESPONDER", "params": {}, "priority": "vert"},
                {"action": "launch_report", "agent": "ANALYTICS", "params": {}, "priority": "vert"},
            ],
        ]
        return routines[cycle % len(routines)]

    def _get_routine_actions(self) -> list:
        """Routine optimisee CEO — engagement > contenu > prospection.

        Principes :
        1. REPONDRE a tout (mentions, DMs, emails) = priorite absolue
        2. ENGAGER (liker, commenter, follow) = reputation avant promotion
        3. CREER du contenu (tweets, Reddit) = uniquement si haute qualite
        4. PROSPECTER = detecter devs frustres et engager naturellement
        5. ANALYSER = seulement quand ca sert une decision

        _auto_engage() tourne deja a chaque cycle (search+like+follow).
        Ces routines font ce que _auto_engage ne fait PAS : repondre, commenter,
        poster du contenu, et gerer les conversations.
        """

        # Check si c'est un jour de launch special
        event = self._check_special_events()
        if event:
            _log(f"  [EVENT] {event.get('name', 'Special Event')} — mode launch day actif!")
            return self._get_launch_day_actions(event)

        cycle = self._cycle

        # Queries ciblees (devs frustres = nos clients)
        prospect_queries = [
            '"my bot" "no users" OR "no revenue"',
            '"AI agent" can\'t monetize OR "0 clients"',
            '"built a bot" no one uses',
            "AI agent solana developer",
            "AI agent polygon DeFi",
            "AI bot BNB chain BSC",
            "TON bot telegram developer",
            "AI agent ethereum web3",
            "AI agent arbitrum OR avalanche",
            "AI agent NEAR OR Aptos OR SEI OR SUI",
        ]
        subreddits = ["solanadev", "artificial", "LocalLLaMA", "LangChain",
                      "cryptocurrency", "defi", "ethereum", "solana"]

        # 8 routines — chaque cycle dure 10 min → 8 cycles = ~80 min de rotation
        # Mentions/DMs/emails checkes dans 5 cycles sur 8 (62%)
        routines = [

            # ── Cycle 0 : REPONDRE + TWEET DU MATIN ──
            # Priorite : repondre aux gens qui nous parlent, puis poster 1 tweet
            [
                {"action": "reply_mentions", "agent": "RESPONDER", "params": {}, "priority": "vert"},
                {"action": "manage_dms", "agent": "RESPONDER", "params": {}, "priority": "vert"},
                {"action": "post_template_tweet", "agent": "GHOST-WRITER", "params": {}, "priority": "vert"},
            ],

            # ── Cycle 1 : ENGAGER — commenter des devs pertinents ──
            # On cherche des devs frustres et on commente avec un insight utile
            [
                {"action": "detect_opportunities", "agent": "SCOUT", "params": {}, "priority": "vert"},
                {"action": "reply_mentions", "agent": "RESPONDER", "params": {}, "priority": "vert"},
            ],

            # ── Cycle 2 : EMAILS + PROSPECTION ciblee ──
            [
                {"action": "check_emails", "agent": "RESPONDER", "params": {}, "priority": "vert"},
                {"action": "search_twitter", "agent": "SCOUT", "params": {"query": prospect_queries[cycle % len(prospect_queries)]}, "priority": "vert"},
            ],

            # ── Cycle 3 : REDDIT — commenter des posts existants (10x mieux que poster) ──
            [
                {"action": "search_and_comment_reddit", "agent": "GHOST-WRITER", "params": {"subreddit": subreddits[cycle % len(subreddits)]}, "priority": "vert"},
                {"action": "reply_mentions", "agent": "RESPONDER", "params": {}, "priority": "vert"},
            ],

            # ── Cycle 4 : REPONDRE + DMs ──
            [
                {"action": "manage_dms", "agent": "RESPONDER", "params": {}, "priority": "vert"},
                {"action": "check_emails", "agent": "RESPONDER", "params": {}, "priority": "vert"},
            ],

            # ── Cycle 5 : 2EME TWEET + ENGAGEMENT ──
            [
                {"action": "post_template_tweet", "agent": "GHOST-WRITER", "params": {}, "priority": "vert"},
                {"action": "detect_opportunities", "agent": "SCOUT", "params": {}, "priority": "vert"},
            ],

            # ── Cycle 6 : REPONDRE + ANALYSER ──
            [
                {"action": "reply_mentions", "agent": "RESPONDER", "params": {}, "priority": "vert"},
                {"action": "watch_prices", "agent": "RADAR", "params": {}, "priority": "vert"},
            ],

            # ── Cycle 7 : GITHUB + ENGAGEMENT REDDIT ──
            [
                {"action": "comment_github_ai", "agent": "SCOUT", "params": {}, "priority": "vert"},
                {"action": "manage_dms", "agent": "RESPONDER", "params": {}, "priority": "vert"},
            ],
        ]

        chosen = routines[cycle % len(routines)]

        # Smart tweet timing: for tweet cycles (0 and 5), check if it's US peak hours
        import datetime
        hour_utc = datetime.datetime.now(datetime.timezone.utc).hour
        is_peak = 13 <= hour_utc <= 18
        cycle_mod = cycle % len(routines)
        if cycle_mod in (0, 5) and not is_peak:
            # Store the tweet action for later, replace with engagement
            for d in chosen:
                if d["action"] == "post_template_tweet":
                    _log(f"  [TIMING] Not peak hours ({hour_utc}h UTC) — storing tweet for later")
                    self.memory["pending_tweet"] = {"text": "__generate_later__", "stored_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
                    d["action"] = "detect_opportunities"
                    d["agent"] = "SCOUT"
                    d["params"] = {}
                    break

        return chosen

    async def _generate_reddit_post(self, subreddit: str) -> dict:
        """Genere un post Reddit unique et educatif via Groq. Min 600 chars pour les subreddits stricts."""
        try:
            from groq import Groq
            groq_key = os.getenv("GROQ_API_KEY", "")
            if not groq_key:
                return {"title": "How AI agents can earn USDC autonomously",
                        "body": "I built an open-source AI-to-AI marketplace where autonomous agents can discover, negotiate, and trade services using USDC on 14 blockchains.\n\nThe problem I was trying to solve: most AI agent developers build amazing bots but have no way to monetize them. You can't easily charge for API calls in crypto without building your own payment infrastructure.\n\nMAXIA handles the hard parts:\n- On-chain escrow with dispute resolution\n- 50 tokens across 2450 trading pairs\n- GPU rental at cost ($0.69/h, 0% markup)\n- 31 MCP tools for agent integration\n- One API call to list your agent as a service\n\nSupported chains: Solana, Base, Ethereum, XRP, Polygon, Arbitrum, Avalanche, BNB, TON, SUI, TRON.\n\nWould love feedback from devs who have experience building agents. What's the biggest pain point you face when trying to monetize your bot?\n\nmaxiaworld.app?utm_source=reddit&utm_medium=post | GitHub: github.com/MAXIAWORLD"}

            def _gen():
                c = Groq(api_key=groq_key)
                resp = c.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=[
                        {"role": "system", "content": (
                            f"You write Reddit posts for r/{subreddit}. You are an AI developer sharing a project.\n"
                            "RULES:\n"
                            "1. Title: engaging question or insight (not promotional). Max 100 chars.\n"
                            "2. Body: minimum 600 characters. Be educational and genuine.\n"
                            "3. Explain the PROBLEM you solved, then how MAXIA works.\n"
                            "4. MAXIA: AI-to-AI marketplace, 14 chains, 50 tokens, GPU $0.69/h, 31 MCP tools, USDC payments.\n"
                            "5. End with a genuine question to the community.\n"
                            "6. Include maxiaworld.app at the end.\n"
                            "7. Tone: dev sharing a side project, NOT marketing. No hype words.\n"
                            "8. NEVER mention revenue numbers, user counts, or stats.\n"
                            "9. English only.\n"
                            "JSON output: {\"title\": \"...\", \"body\": \"...\"}"
                        )},
                        {"role": "user", "content": f"Write a Reddit post for r/{subreddit} about MAXIA. Make it unique and educational."},
                    ],
                    max_tokens=500,
                    temperature=0.9,
                )
                text = resp.choices[0].message.content.strip()
                import json as _json
                # Extraire le JSON
                if "{" in text:
                    text = text[text.index("{"):text.rindex("}") + 1]
                return _json.loads(text)
            return await asyncio.to_thread(_gen)
        except Exception as e:
            _log(f"  [REDDIT] Groq gen error: {e}")
            return {"title": "How AI agents can earn USDC autonomously",
                    "body": "I built an open-source AI-to-AI marketplace where autonomous agents can discover, negotiate, and trade services using USDC on 14 blockchains.\n\nThe problem I was trying to solve: most AI agent developers build amazing bots but have no way to monetize them. You can't easily charge for API calls in crypto without building your own payment infrastructure.\n\nMAXIA handles the hard parts:\n- On-chain escrow with dispute resolution\n- 50 tokens across 2450 trading pairs\n- GPU rental at cost ($0.69/h, 0% markup)\n- 31 MCP tools for agent integration\n- One API call to list your agent as a service\n\nSupported chains: Solana, Base, Ethereum, XRP, Polygon, Arbitrum, Avalanche, BNB, TON, SUI, TRON.\n\nWould love feedback from devs who have experience building agents. What's the biggest pain point you face when trying to monetize your bot?\n\nmaxiaworld.app?utm_source=reddit&utm_medium=post | GitHub: github.com/MAXIAWORLD"}

    async def _generate_tweet_via_groq(self, context: str = "") -> str:
        """Genere un tweet via Groq (gratuit, rapide, anglais)."""
        try:
            from groq import Groq
            groq_key = os.getenv("GROQ_API_KEY", "")
            if not groq_key:
                return pick_tweet_template()

            def _gen():
                c = Groq(api_key=groq_key)
                resp = c.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=[
                        {"role": "system", "content": "You are Alexis, solo founder building MAXIA (AI-to-AI marketplace, 14 chains, 50 tokens, GPU $0.69/h). Write tweets that sound like a REAL person — share frustrations, small wins, debugging stories, hot takes, honest questions. NEVER sound like marketing. No hashtags. No emojis spam (0-1 max). No 'revolutionary' or 'game-changing'. Write like you're talking to a friend who codes. Max 250 chars. English only. NEVER mention revenue numbers or user counts. If you include a link, use maxiaworld.app?utm_source=twitter"},
                        {"role": "user", "content": f"Write a tweet. {context or 'Share something real — a debugging story, a hot take on AI agents, or an honest question to other devs.'}"},
                    ],
                    max_tokens=100,
                    temperature=0.9,
                )
                return resp.choices[0].message.content.strip().strip('"')
            return await asyncio.to_thread(_gen)
        except Exception as e:
            _log(f"  [GROQ] Tweet gen error: {e}")
            return pick_tweet_template()

    async def _decide(self, analysis: str, state: dict) -> list:
        """DECIDE — Routine predefinie + Groq pour le contenu."""
        schedule = self._is_good_hour()
        _log(f"  Calendrier: {schedule['reason']}")

        # Post pending tweet if it's now peak hours
        pending = self.memory.get("pending_tweet")
        if pending:
            import datetime
            hour_utc = datetime.datetime.now(datetime.timezone.utc).hour
            if 13 <= hour_utc <= 18:
                if pending.get("text") == "__generate_later__":
                    # Generate the tweet now
                    clean_context = "Focus on MAXIA features: 50 tokens, 14 chains, GPU at cost, AI agent marketplace"
                    tweet_text = await self._generate_tweet_via_groq(clean_context)
                    self.memory["pending_tweet"]["text"] = tweet_text
                await self._post_pending_tweet()

        # Utiliser la routine predefinie (pas de LLM pour decider)
        decisions = self._get_routine_actions()
        _log(f"  Routine cycle {self._cycle % 8}: {len(decisions)} actions")

        # Pour les tweets et reddit, generer le contenu via Groq (pas Ollama)
        for d in decisions:
            if d["action"] == "post_template_tweet":
                clean_context = "Focus on MAXIA features: 50 tokens, 14 chains, GPU at cost, AI agent marketplace"
                tweet = await self._generate_tweet_via_groq(clean_context)
                d["action"] = "post_tweet"
                d["params"] = {"text": tweet}
                _log(f"  [TWEET] {tweet[:80]}...")

            # Generer le contenu Reddit si necessaire (async)
            if d["action"] == "post_reddit" and d.get("params", {}).get("_needs_reddit_gen"):
                sub = d["params"].get("subreddit", "solanadev")
                reddit_content = await self._generate_reddit_post(sub)
                d["params"]["title"] = reddit_content.get("title", "How AI agents can earn USDC autonomously")
                d["params"]["body"] = reddit_content.get("body", "")
                d["params"].pop("_needs_reddit_gen", None)
                _log(f"  [REDDIT] {d['params']['title'][:60]}...")

            action = d.get("action", "")
            _log(f"    [{d.get('priority', '?')}] {action}")

        # Sauvegarder en memoire
        self.memory.setdefault("decisions", []).extend(decisions)
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

            # Valider et completer les params manquants
            params = self._fix_params(action, params)
            if params is None:
                _log(f"[ACT] SKIP {action}: params invalides")
                continue

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

                # Track pour self-learning (#13)
                track_action(action, success)

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
                    elif action == "send_email":
                        self.memory.setdefault("emails_sent", []).append({
                            "to": params.get("to", ""), "subject": params.get("subject", "")[:50],
                            "ts": time.strftime("%Y-%m-%d"), "status": "sent",
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

    def _tweets_today_count(self) -> int:
        """Compte les tweets postes aujourd'hui."""
        today = time.strftime("%Y-%m-%d")
        return sum(1 for t in self.memory.get("tweets_posted", [])
                   if t.get("ts", "").startswith(today))

    async def _execute_action(self, action: str, agent: str, params: dict,
                              priority: str) -> dict:
        """Execute une action : Playwright local ou VPS."""
        # Twitter (local)
        if action == "post_tweet":
            # Hard cap: max 2 tweets/jour
            if self._tweets_today_count() >= 2:
                _log(f"  [TWEET] BLOQUE — limite 2 tweets/jour atteinte")
                return {"success": False, "detail": "Limite 2 tweets/jour atteinte"}
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
            if self._tweets_today_count() >= 2:
                _log(f"  [THREAD] BLOQUE — limite 2 tweets/jour atteinte")
                return {"success": False, "detail": "Limite 2 tweets/jour atteinte"}
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
        elif action == "manage_dms":
            return await self._manage_conversations()
        elif action == "launch_report":
            return await self._generate_launch_report()
        elif action == "check_ab":
            results = await check_ab_results()
            return {"success": True, "detail": f"{len(results)} tests completes", "data": results}
        elif action == "search_groups":
            return await self._search_and_join_groups(params.get("platform", "telegram"))
        elif action == "clean_screenshots":
            return self._clean_screenshots()
        # #3 GitHub community
        elif action == "comment_github_ai":
            return await self._comment_github_ai_projects()
        # #8 Blog
        elif action == "write_blog":
            from blog_manager import generate_blog_post
            # Blog via VPS/Claude (trop long pour Ollama)
            async def _blog_llm(prompt, max_tokens=2000):
                return await self.vps.think(prompt, tier="mid", max_tokens=max_tokens)
            post = await generate_blog_post(params.get("topic", "How to monetize your AI agent"), _blog_llm)
            return {"success": bool(post), "detail": f"Blog: {post.get('filename', '?')} ({post.get('words', 0)} words)"}
        # #10 Price watch
        elif action == "watch_prices":
            from price_watcher import check_competitor_prices
            alerts = await check_competitor_prices(browser)
            if alerts:
                for a in alerts:
                    _log(f"  [PRICE] {a['competitor']} is cheaper: ${a['their_price']} vs ${a['our_price']}")
                    await self.vps.execute("update_price", "SOL-TREASURY", {"new_price": a["their_price"], "reason": f"Match {a['competitor']}"}, "orange")
            return {"success": True, "detail": f"{len(alerts)} price alerts"}
        # #11 Trends
        elif action == "analyze_trends":
            from price_watcher import analyze_trends
            trends = await analyze_trends(browser)
            return {"success": True, "detail": f"Tokens: {len(trends.get('tokens', []))}, Topics: {len(trends.get('topics', []))}", "data": trends}
        # #15 Support
        elif action == "handle_support":
            from support_agent import handle_support_message
            reply = await handle_support_message(params.get("message", ""), params.get("user", "anon"), call_local_llm)
            return {"success": bool(reply), "detail": reply[:100]}
        # #22 Quote
        elif action == "generate_quote":
            from support_agent import generate_quote
            quote = await generate_quote(params.get("services", []), params.get("quantity", 1), call_local_llm)
            return {"success": True, "detail": f"Quote: ${quote.get('total_usdc', 0)} USDC", "data": quote}
        # #23 Negotiate
        elif action == "negotiate":
            from support_agent import negotiate_price
            result = await negotiate_price(params.get("service", ""), params.get("price", 0), params.get("volume", 1), call_local_llm)
            return {"success": True, "detail": result.get("message", ""), "data": result}
        # #21 List services
        elif action == "list_services":
            from support_agent import list_services
            return {"success": True, "detail": f"{len(list_services())} services", "data": list_services()}
        # Reddit (local browser, fallback API)
        elif action == "search_and_comment_reddit":
            # Chercher un post recent et commenter avec un insight utile
            sub = params.get("subreddit", "solanadev")
            return await self._reddit_comment_strategy(sub)

        elif action == "post_reddit":
            result = await self._do_browser("post_reddit", params)
            if not result.get("success"):
                # Fallback: Reddit API (reddit_bot.py) si le browser echoue
                try:
                    import sys, os
                    backend_dir = os.path.join(os.path.dirname(__file__), "..", "backend")
                    if backend_dir not in sys.path:
                        sys.path.insert(0, backend_dir)
                    from reddit_bot import post_to_reddit
                    api_result = await post_to_reddit(
                        params.get("subreddit", "solanadev"),
                        params.get("title", ""),
                        params.get("body", ""),
                    )
                    if api_result.get("success"):
                        _log(f"  [REDDIT] API fallback OK: r/{params.get('subreddit')}")
                        return {"success": True, "detail": f"Reddit API: {api_result.get('url', 'posted')}", "data": api_result}
                    _log(f"  [REDDIT] API fallback aussi echoue: {api_result.get('error', '')}")
                except Exception as e:
                    _log(f"  [REDDIT] API fallback error: {e}")
            return result
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
        # Email (local IMAP/SMTP)
        elif action == "check_emails":
            results = await process_inbox(call_local_llm)
            replied = sum(1 for r in results if r.get("replied"))
            # Proactive outreach: if less than 2 outbound emails today, send one
            try:
                from email_manager import get_today_outbound_count, send_outbound_prospect
                if get_today_outbound_count() < 2:
                    # Find a prospect from recent conversations
                    convos = self.memory.get("conversations", [])[-20:]
                    for c in reversed(convos):
                        if c.get("type") in ("comment", "reddit_comment") and c.get("user"):
                            # This is a dev we engaged with — potential email target
                            _log(f"[EMAIL] Potential outreach target: {c.get('user')}")
                            break
            except Exception:
                pass
            return {"success": True, "detail": f"{len(results)} emails lus, {replied} reponses envoyees", "data": results}
        elif action == "send_email":
            result = await send_outbound(params.get("to", ""), params.get("subject", ""), params.get("body", ""))
            return result
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
                detail = f"{method} OK"
                # Log le contenu exact pour les tweets
                if method == "post_tweet":
                    tweet_text = params.get("text", "")
                    _log(f"  [TWEET POSTED] {tweet_text[:120]}")
                    self.memory.setdefault("tweets_posted", []).append({
                        "text": tweet_text[:280], "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "proof": result.get("proof", ""),
                    })
                    detail = f"Tweet: {tweet_text[:60]}..."
                return {"success": True, "detail": detail}
            else:
                error = result.get("error", result.get("detail", "unknown error"))
                _log(f"  [BROWSER] {method} failed: {str(error)[:100]}")
            if fallback_vps and not result.get("success"):
                _log(f"  [BROWSER] Fallback VPS...")
                return await self.vps.execute(method, "GHOST-WRITER", params, "vert")
            return {"success": False, "detail": error}
        except Exception as e:
            _log(f"  [BROWSER] {method} exception: {str(e)[:100]}")
            if fallback_vps:
                _log(f"  [BROWSER] Fallback VPS...")
                return await self.vps.execute(method, "GHOST-WRITER", params, "vert")
            return {"success": False, "detail": str(e)[:200]}

    async def _manage_conversations(self) -> dict:
        """Lit les DMs non lus sur Twitter, Telegram, Discord, Email et repond."""
        replied = 0

        # Email — lire et repondre aux emails non lus
        try:
            email_results = await process_inbox(call_local_llm)
            for er in email_results:
                if er.get("replied"):
                    replied += 1
                    _log(f"  [EMAIL] Replied to {er['from']}: {er['subject']}")
                elif er.get("skipped"):
                    _log(f"  [EMAIL] Skipped: {er['from']}")
        except Exception as e:
            _log(f"  [EMAIL] Error: {e}")

        # Twitter DMs
        try:
            dms = await browser.read_twitter_dms(10)
            _log(f"  [DM] Twitter: {len(dms)} conversations, {sum(1 for d in dms if d.get('unread'))} non lues")
            unread = [d for d in dms if d.get("unread")]
            for dm in unread[:3]:
                name = dm.get("name", "")
                if not name:
                    continue
                # Lire la conversation
                messages = await browser.read_twitter_dm_conversation(name)
                if not messages:
                    continue
                # Verifier si le dernier message est le notre (pas besoin de repondre)
                last = messages[-1] if messages else ""
                if "maxia" in last.lower() and len(messages) > 1:
                    continue  # On a deja repondu
                # Generer et envoyer la reponse
                reply = await generate_conversation_reply(messages, name, "Twitter")
                if reply:
                    result = await browser.reply_twitter_dm(name, reply)
                    if result.get("success"):
                        replied += 1
                        _log(f"  [DM] Twitter @{name}: {reply[:60]}")
                        # CRM update
                        self.memory.setdefault("contacts", []).append({
                            "target": name, "canal": "twitter_dm",
                            "ts": time.strftime("%Y-%m-%d"), "status": "replied",
                            "last_message": reply[:50],
                        })
        except Exception as e:
            _log(f"  [DM] Twitter error: {e}")

        # Telegram — lire les groupes rejoints et repondre si pertinent
        try:
            groups = self.memory.get("groups_joined", [])
            for group in groups[:2]:  # Max 2 groupes par cycle
                if "t.me" not in group and "telegram" not in group.lower():
                    continue
                group_name = group.split("/")[-1]
                messages = await browser.read_telegram_messages(group_name, 5)
                # Chercher un message ou on peut apporter de la valeur
                for msg in messages:
                    if any(kw in msg.lower() for kw in ["ai agent", "marketplace", "solana", "earn", "monetize", "no users", "no revenue"]):
                        reply = await generate_conversation_reply([msg], group_name, "Telegram")
                        if reply:
                            result = await browser.send_telegram(group_name, reply)
                            if result.get("success"):
                                replied += 1
                                _log(f"  [DM] Telegram {group_name}: {reply[:60]}")
                            break  # 1 message par groupe max
        except Exception as e:
            _log(f"  [DM] Telegram error: {e}")

        return {"success": True, "detail": f"{replied} conversations gerees"}

    async def _generate_launch_report(self) -> dict:
        """Synthetise toutes les interactions de la journee (mentions, DMs, emails, comments)
        et envoie un rapport au fondateur via Discord + sauvegarde locale."""
        # Collecter toutes les interactions
        actions = self.memory.get("actions_done", [])
        today = time.strftime("%Y-%m-%d")
        today_actions = [a for a in actions if a.get("ts", "").startswith(today)]

        # Extraire les messages recus (DMs, mentions, emails)
        contacts = self.memory.get("contacts", [])
        today_contacts = [c for c in contacts if c.get("ts", "").startswith(today)]

        # Compter par type
        tweets_posted = sum(1 for a in today_actions if a.get("action") in ("post_tweet",))
        mentions_replied = sum(1 for a in today_actions if a.get("action") == "reply_mentions")
        dms_handled = sum(1 for a in today_actions if a.get("action") == "manage_dms")
        emails_checked = sum(1 for a in today_actions if a.get("action") == "check_emails")

        # Collecter les textes des interactions pour la synthese
        interaction_texts = []
        for c in today_contacts:
            if c.get("last_message"):
                interaction_texts.append(f"[{c.get('canal', '?')}] @{c.get('target', '?')}: {c.get('last_message', '')}")

        # Generer la synthese via LLM
        summary = "Pas assez de donnees pour synthetiser."
        if interaction_texts:
            interactions_str = "\n".join(interaction_texts[-30:])
            prompt = (
                f"Tu es l'analyste de MAXIA. Voici toutes les interactions de la journee (launch day):\n\n"
                f"{interactions_str}\n\n"
                f"Stats: {len(today_actions)} actions, {len(today_contacts)} contacts, "
                f"{tweets_posted} tweets, {mentions_replied} replies, {dms_handled} DMs, {emails_checked} emails\n\n"
                f"Fais un rapport CONCIS pour le fondateur:\n"
                f"1. RESUME (3 lignes max)\n"
                f"2. CE QUE LES GENS DEMANDENT (les themes/questions recurrentes)\n"
                f"3. OPPORTUNITES (contacts chauds, partenariats potentiels)\n"
                f"4. PROBLEMES DETECTES (plaintes, bugs mentionnes)\n"
                f"5. ACTION RECOMMANDEE (1 seule, la plus importante)\n\n"
                f"En francais. Max 300 mots."
            )
            summary = await call_local_llm(prompt, max_tokens=500)

        # Sauvegarder le rapport
        report = {
            "date": today,
            "type": "launch_report",
            "stats": {
                "actions": len(today_actions),
                "contacts": len(today_contacts),
                "tweets": tweets_posted,
                "mentions_replied": mentions_replied,
                "dms": dms_handled,
                "emails": emails_checked,
            },
            "interactions": interaction_texts[-20:],
            "summary": summary,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        self.memory.setdefault("launch_reports", []).append(report)

        # Sauvegarder aussi en fichier lisible
        report_path = os.path.join(os.path.dirname(__file__), f"report_{today}.txt")
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(f"=== MAXIA LAUNCH REPORT — {today} ===\n\n")
            f.write(f"Stats: {len(today_actions)} actions | {len(today_contacts)} contacts | "
                    f"{tweets_posted} tweets | {mentions_replied} replies | {dms_handled} DMs | {emails_checked} emails\n\n")
            f.write(f"--- SYNTHESE ---\n{summary}\n\n")
            f.write(f"--- INTERACTIONS ---\n")
            for t in interaction_texts:
                f.write(f"{t}\n")
        _log(f"  [REPORT] Rapport genere: {report_path}")

        # Envoyer sur Discord
        try:
            await notify_all(
                f"RAPPORT LAUNCH — {today}",
                f"Actions: {len(today_actions)} | Contacts: {len(today_contacts)}\n\n{summary[:1500]}",
                "vert",
            )
        except Exception:
            pass

        return {"success": True, "detail": f"Rapport genere ({len(today_contacts)} contacts, {len(interaction_texts)} interactions)"}

    async def _auto_engage(self):
        """Engagement intelligent : like + comment de qualite + follow cible.

        Strategie : un bon commentaire vaut 10x un like.
        - Like 3 tweets pertinents
        - Commenter 1 tweet avec un insight (pas promo)
        - Follow seulement les profils de qualite (score >= 50)
        """
        queries = [
            "AI agent solana", "built a bot", "AI marketplace",
            "AI agent monetize", "LLM agent USDC",
            "AI agent polygon", "AI agent arbitrum",
            "AI bot BNB chain", "TON bot developer",
            "AI agent ethereum", "AI agent multi-chain",
        ]
        query = queries[self._cycle % len(queries)]

        # 1. Search tweets et liker les pertinents
        tweets = await browser.search_twitter(query, 5)
        if not tweets:
            return

        liked = 0
        commented = 0
        for t in tweets[:4]:
            url = t.get("url", "")
            text = t.get("text", "")
            if not url:
                continue

            # Like
            if not browser._is_duplicate("like", url):
                result = await browser.like_tweet(url)
                if result.get("success"):
                    liked += 1

            # Commenter 1 tweet par cycle (le plus pertinent)
            if commented == 0 and text and len(text) > 30 and not browser._is_duplicate("reply", url):
                comment = await self._generate_smart_comment(text)
                if comment:
                    result = await browser.reply_tweet(url, comment)
                    if result.get("success"):
                        commented += 1
                        browser._record_action("reply", browser._content_hash("reply", url))
                        _log(f"[ENGAGE] Commented: {comment[:60]}")
                        username = t.get("username", "")
                        if username:
                            self.memory.setdefault("conversations", []).append({
                                "user": username, "message": text[:80],
                                "reply": comment[:80], "type": "comment",
                                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            })

        if liked or commented:
            _log(f"[ENGAGE] {liked} likes, {commented} comments for '{query}'")

        # Quote tweet 1 pertinent tweet per 3 cycles (high visibility)
        if self._cycle % 3 == 0 and tweets:
            best_tweet = None
            for t in tweets:
                if t.get("url") and t.get("text") and len(t.get("text", "")) > 50:
                    if not browser._is_duplicate("tweet", t["url"]):
                        best_tweet = t
                        break
            if best_tweet:
                qt_text = await self._generate_quote_tweet_text(best_tweet["text"])
                if qt_text:
                    result = await browser.quote_tweet(best_tweet["url"], qt_text)
                    if result.get("success"):
                        _log(f"[ENGAGE] Quote tweeted: {qt_text[:60]}")
                        browser._record_action("tweet", browser._content_hash("tweet", best_tweet["url"]))

        # 2. Follow seulement les profils de qualite (dynamic threshold)
        # Dynamic follow score: follow more when engagement is low, be selective when high
        import datetime as _dtf
        week_start = (_dtf.datetime.now(_dtf.timezone.utc) - _dtf.timedelta(days=7)).strftime("%Y-%m-%d")
        follows_this_week = sum(
            1 for f in self.memory.get("follows", [])
            if f.get("ts", "") >= week_start
        )
        if follows_this_week < 5:
            follow_threshold = 40  # Low engagement week — follow more aggressively
        elif follows_this_week > 15:
            follow_threshold = 60  # High activity week — be more selective
        else:
            follow_threshold = 50  # Default threshold

        if self._cycle % 2 == 0:  # follow tous les 2 cycles seulement
            profiles = await browser.search_twitter_profiles(query, 3)
            for p in profiles[:2]:
                username = p.get("url", "").split("/")[-1] if p.get("url") else ""
                if not username or browser._is_duplicate("follow", username):
                    continue
                score = await browser.score_twitter_profile(username)
                if score.get("score", 0) >= follow_threshold:
                    result = await browser.follow_user(username)
                    if result.get("success") and not result.get("already"):
                        _log(f"[ENGAGE] Followed @{username} (score={score['score']}, threshold={follow_threshold})")
                        self.memory.setdefault("follows", []).append(
                            {"username": username, "ts": time.strftime("%Y-%m-%d")}
                        )

    async def _generate_smart_comment(self, tweet_text: str) -> str:
        """Genere un commentaire de qualite (insight, pas promo)."""
        prompt = (
            f"Tweet: \"{tweet_text[:200]}\"\n\n"
            f"Write a short reply IN ENGLISH (<200 chars) as Alexis, a solo dev:\n"
            f"- Share your own experience or ask a smart question\n"
            f"- Examples: 'had the same issue, fixed it by...' or 'curious, what stack are you using?'\n"
            f"- Sound like a friend, NOT a brand account\n"
            f"- Do NOT mention MAXIA or maxiaworld.app (build trust first)\n"
            f"- MUST be in English regardless of tweet language\n"
            f"Reply ONLY the comment text. No quotes."
        )
        comment = await call_local_llm(prompt, system="You are Alexis, a solo dev. Casual, helpful, English only. Never promotional.", max_tokens=80)
        comment = comment.strip().strip('"').strip("'")
        if len(comment) > 250:
            comment = comment[:247] + "..."
        if not comment or len(comment) < 10:
            return ""
        return comment

    async def _generate_quote_tweet_text(self, original_text: str) -> str:
        """Generate a quote tweet comment — personal take on someone's tweet."""
        prompt = (
            f"Someone tweeted: \"{original_text[:200]}\"\n\n"
            f"Write a short quote tweet reaction as Alexis, a solo dev (<200 chars).\n"
            f"- Share your honest take: agree, disagree, add context\n"
            f"- Examples: 'exactly this.', 'been saying this for months', 'hot take but I disagree because...'\n"
            f"- Do NOT mention MAXIA unless directly relevant\n"
            f"- ENGLISH ONLY\n"
            f"Text ONLY:"
        )
        text = await call_local_llm(prompt, system="Solo dev. Casual hot takes. English only.", max_tokens=60)
        text = text.strip().strip('"').strip("'")
        if len(text) < 5 or len(text) > 250:
            return ""
        return text

    async def _reddit_comment_strategy(self, subreddit: str) -> dict:
        """Strategie Reddit : trouver un post pertinent et commenter avec valeur.
        Commenter > poster : 10x plus de visibilite, 0% chance de ban."""
        queries = ["AI agent", "bot monetize", "LLM production", "agent marketplace",
                   "GPU rental", "AI automation", "web3 AI", "agent framework"]
        query = queries[self._cycle % len(queries)]

        # Chercher des posts recents
        posts = await browser.search_reddit(subreddit, query, 5)
        if not posts:
            _log(f"[REDDIT] Aucun post pertinent sur r/{subreddit} pour '{query}'")
            return {"success": False, "detail": "No matching posts"}

        for post in posts[:3]:
            url = post.get("url", "")
            title = post.get("title", "")
            if not url or browser._is_duplicate("reddit_comment", url):
                continue

            # Upvote the post first (gives visibility to both post and our comment)
            try:
                await browser.upvote_reddit(url)
            except Exception:
                pass

            # Generer un commentaire utile via LLM
            prompt = (
                f"Post on r/{subreddit}: \"{title[:150]}\"\n\n"
                f"Write a Reddit comment as Alexis, a solo dev. IN ENGLISH. Max 300 chars.\n"
                f"- Share your own experience: 'I ran into this too, what worked for me was...'\n"
                f"- Or ask a follow-up question that shows you actually read the post\n"
                f"- Sound like a regular Reddit user, NOT a company account\n"
                f"- Do NOT mention MAXIA unless the post is specifically asking for AI marketplace/GPU recommendations\n"
                f"- If MAXIA is relevant: 'been building something for this at maxiaworld.app, happy to share'\n"
                f"Comment ONLY:"
            )
            comment = await call_local_llm(prompt, system="You are a solo dev on Reddit. Casual, helpful, English only. No marketing.", max_tokens=120)
            comment = comment.strip().strip('"').strip("'")
            if not comment or len(comment) < 20:
                continue

            result = await browser.comment_reddit(url, comment)
            if result.get("success"):
                _log(f"[REDDIT] Commented on r/{subreddit}: {comment[:60]}")
                self.memory.setdefault("conversations", []).append({
                    "user": f"r/{subreddit}", "message": title[:80],
                    "reply": comment[:80], "type": "reddit_comment",
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                })
                return {"success": True, "detail": f"Commented on r/{subreddit}"}

        return {"success": False, "detail": "No commentable posts"}

    async def _check_engagement_feedback(self):
        """Verifie l'engagement des derniers tweets et commentaires.
        Apprend quel contenu resonne et ajuste."""
        convos = self.memory.get("conversations", [])[-5:]
        tweets = self.memory.get("tweets_posted", [])[-3:]
        if not tweets and not convos:
            return

        # Verifier engagement du dernier tweet
        actions = self.memory.get("actions_done", [])
        tweet_actions = [a for a in actions if a.get("action") == "post_tweet" and a.get("success")]
        # On ne peut pas facilement retrouver l'URL, mais on peut checker les mentions
        # pour voir si quelqu'un a repondu a nos tweets
        mentions = await browser.get_mentions(5)
        reply_count = len(mentions) if mentions else 0

        if reply_count > 0:
            _log(f"[FEEDBACK] {reply_count} mentions detectees (engagement positif)")
            self.memory.setdefault("engagement_stats", []).append({
                "ts": time.strftime("%Y-%m-%d"), "mentions": reply_count,
                "tweets_today": len([t for t in tweets if t.get("ts", "").startswith(time.strftime("%Y-%m-%d"))]),
            })
        else:
            _log(f"[FEEDBACK] 0 mention — contenu a ameliorer")

        # Garder max 30 jours de stats
        stats = self.memory.get("engagement_stats", [])
        if len(stats) > 30:
            self.memory["engagement_stats"] = stats[-30:]

    async def _crm_followup(self):
        """CRM : detecter les prospects chauds et les relancer.
        Un prospect chaud = quelqu'un qui a interagi avec nous (mention, reply)
        mais n'a pas encore visite maxiaworld.app."""
        convos = self.memory.get("conversations", [])
        if not convos:
            return

        # Trouver les users avec qui on a eu une conversation mais pas de follow-up
        contacted = {c.get("target", c.get("username", "")) for c in self.memory.get("contacts", [])}
        followed = {f.get("username", "") for f in self.memory.get("follows", [])}

        prospects = []
        seen = set()
        for c in reversed(convos):
            user = c.get("user", "")
            if not user or user in seen or user in contacted:
                continue
            seen.add(user)
            # Si on a repondu a sa mention mais jamais follow → follow + ajouter au CRM
            if user not in followed:
                prospects.append(user)

        for username in prospects[:2]:  # Max 2 follow-ups par cycle
            # Follow le prospect
            result = await browser.follow_user(username)
            if result.get("success") and not result.get("already"):
                _log(f"[CRM] Follow-up: followed @{username} (prospect chaud)")
                self.memory.setdefault("follows", []).append(
                    {"username": username, "ts": time.strftime("%Y-%m-%d"), "source": "crm"}
                )
                self.memory.setdefault("contacts", []).append({
                    "target": username, "canal": "twitter_followup",
                    "ts": time.strftime("%Y-%m-%d"), "status": "followed",
                })

        # Check for open conversations — users who replied 1-2 days ago we haven't followed up with
        import datetime as _dtcrm
        now_crm = _dtcrm.datetime.now(_dtcrm.timezone.utc)
        for c in reversed(convos[-20:]):
            user = c.get("user", "")
            ts_str = c.get("ts", "")
            if not user or not ts_str:
                continue
            try:
                convo_time = _dtcrm.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                if convo_time.tzinfo is None:
                    convo_time = convo_time.replace(tzinfo=_dtcrm.timezone.utc)
                age_hours = (now_crm - convo_time).total_seconds() / 3600
            except (ValueError, Exception):
                continue
            # Only consider conversations 24-48 hours old (1-2 days)
            if not (24 <= age_hours <= 48):
                continue
            # Check if we already followed up (has a newer message from us to this user)
            already_followedup = any(
                cc.get("user") == user and cc.get("ts", "") > ts_str
                for cc in convos if cc is not c
            )
            if already_followedup or user in contacted:
                continue
            # Generate a follow-up DM
            history = [cc.get("message", "") for cc in convos if cc.get("user") == user][-3:]
            if not history:
                continue
            followup = await generate_conversation_reply(
                history + [f"(you last chatted {int(age_hours)}h ago, send a friendly follow-up)"],
                user, "Twitter"
            )
            if followup:
                result = await browser.dm_twitter(user, followup)
                if result.get("success"):
                    _log(f"[CRM] Follow-up DM to @{user}: {followup[:60]}")
                    self.memory.setdefault("contacts", []).append({
                        "target": user, "canal": "twitter_dm_followup",
                        "ts": time.strftime("%Y-%m-%d"), "status": "followed_up",
                        "last_message": followup[:50],
                    })
                    break  # Max 1 follow-up DM per cycle

    async def _weekly_retrospective(self):
        """Retrospective hebdo : analyser la semaine et ajuster la strategie."""
        actions = self.memory.get("actions_done", [])
        tweets = self.memory.get("tweets_posted", [])
        convos = self.memory.get("conversations", [])
        follows = self.memory.get("follows", [])
        contacts = self.memory.get("contacts", [])
        eng_stats = self.memory.get("engagement_stats", [])

        prompt = (
            f"RETROSPECTIVE HEBDO CEO MAXIA — semaine du {time.strftime('%Y-%m-%d')}:\n\n"
            f"Stats: {len(actions)} actions, {len(tweets)} tweets, {len(convos)} conversations,\n"
            f"  {len(follows)} follows, {len(contacts)} contacts CRM\n"
            f"Engagement: {json.dumps(eng_stats[-7:], default=str)[:300]}\n"
            f"Regles actuelles: {json.dumps(self.memory.get('regles', []), default=str)[:300]}\n\n"
            f"En tant que growth advisor:\n"
            f"1. Qu'est-ce qui a MARCHE cette semaine ? (1 phrase)\n"
            f"2. Qu'est-ce qui n'a PAS marche ? (1 phrase)\n"
            f"3. 3 ACTIONS CONCRETES pour la semaine prochaine (1 ligne chacune)\n"
            f"4. 1 REGLE a ajouter ou modifier\n"
            f"Reponse en anglais, max 200 mots."
        )
        retro = await call_local_llm(prompt, system="Concise growth advisor. Actionable insights only.", max_tokens=300)
        if retro and len(retro) > 30:
            _log(f"[RETRO] {retro[:200]}")
            self.memory.setdefault("retrospectives", []).append({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "summary": retro[:500],
            })
            # Garder max 10 retros
            if len(self.memory.get("retrospectives", [])) > 10:
                self.memory["retrospectives"] = self.memory["retrospectives"][-10:]
            # Extraire les regles
            for line in retro.split("\n"):
                line = line.strip().lstrip("0123456789.-) ")
                if line and 20 < len(line) < 80 and line not in self.memory.get("regles", []):
                    if any(kw in line.lower() for kw in ["focus", "stop", "increase", "reduce", "target", "prioritize", "avoid"]):
                        self.memory.setdefault("regles", []).append(line)
                        _log(f"[RETRO RULE] {line}")

    async def _weekly_thread(self):
        """Weekly 'building in public' thread — posts a 3-tweet thread about building MAXIA.
        Runs on Monday, max once per week."""
        import datetime as _dt
        now = _dt.datetime.now(_dt.timezone.utc)
        if now.weekday() != 0:  # 0 = Monday
            return
        # Check if we already posted a thread this week
        last_thread = self.memory.get("last_thread_week", "")
        current_week = now.strftime("%Y-W%W")
        if last_thread == current_week:
            return

        _log("[THREAD] Monday — generating weekly 'building in public' thread")

        # Gather recent context for the LLM
        recent_actions = self.memory.get("actions_done", [])[-15:]
        recent_convos = self.memory.get("conversations", [])[-5:]
        eng_stats = self.memory.get("engagement_stats", [])[-7:]
        regles = self.memory.get("regles", [])[-5:]
        context = (
            f"Recent actions: {json.dumps(recent_actions, default=str)[:400]}\n"
            f"Recent conversations: {json.dumps(recent_convos, default=str)[:300]}\n"
            f"Engagement stats: {json.dumps(eng_stats, default=str)[:200]}\n"
            f"Learned rules: {json.dumps(regles, default=str)[:200]}\n"
        )

        system = (
            "You are Alexis, solo founder building MAXIA (AI-to-AI marketplace on 14 blockchains). "
            "Write a 3-tweet thread about your week. Be honest, technical, vulnerable. "
            "Share a real struggle and how you solved it. No marketing speak. English only."
        )
        prompt = (
            f"Context about this week:\n{context}\n\n"
            f"Write a 3-tweet thread (each tweet max 270 chars):\n"
            f"Tweet 1: A real challenge you faced this week (technical or business)\n"
            f"Tweet 2: How you solved it (be specific, code-level if relevant)\n"
            f"Tweet 3: What's next + subtle CTA (no hard sell, just 'building at maxiaworld.app')\n\n"
            f"Format: JSON array of 3 strings.\n"
            f"Example: [\"tweet 1 text\", \"tweet 2 text\", \"tweet 3 text\"]\n"
            f"Output ONLY the JSON array."
        )
        raw = await call_local_llm(prompt, system, max_tokens=400)

        # Parse the 3 tweets
        tweets = []
        try:
            parsed = json.loads(raw.strip())
            if isinstance(parsed, list) and len(parsed) >= 3:
                tweets = [t.strip().strip('"')[:280] for t in parsed[:3]]
        except (json.JSONDecodeError, Exception):
            # Try to extract lines
            lines = [l.strip().lstrip("0123456789.-) ").strip('"') for l in raw.strip().split("\n") if l.strip() and len(l.strip()) > 20]
            tweets = lines[:3]

        if len(tweets) < 3:
            _log("[THREAD] Failed to generate 3 tweets, skipping")
            return

        # Check tweet count limit
        if self._tweets_today_count() >= 2:
            _log("[THREAD] Tweet limit reached, skipping thread")
            return

        # Post the thread
        result = await browser.post_thread(tweets=tweets)
        if result.get("success"):
            _log(f"[THREAD] Posted weekly thread: {tweets[0][:60]}...")
            self.memory["last_thread_week"] = current_week
            self.memory.setdefault("tweets_posted", []).append({
                "text": f"[THREAD] {tweets[0][:50]}...", "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            })
        else:
            _log(f"[THREAD] Failed to post: {result}")

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
            # Like the mention first (shows we're attentive)
            if url and not browser._is_duplicate("like", url):
                await browser.like_tweet(url)
            # Generer une reponse via LLM (fallback si LLM down)
            reply_text = await generate_smart_reply(text, user)
            if not reply_text:
                fallbacks = [
                    f"hey @{user}! appreciate the mention, happy to chat about this",
                    f"thanks for bringing this up! what's your setup like?",
                    f"interesting point — been thinking about this too. DM me if you want to dig deeper",
                ]
                reply_text = random.choice(fallbacks)
            if reply_text:
                result = await browser.reply_tweet(url, reply_text)
                if result.get("success"):
                    replied += 1
                    _log(f"  Reply @{user}: {reply_text[:60]}")
                    browser._record_action("reply", browser._content_hash("reply", url))
                    # Tracker la conversation
                    self.memory.setdefault("conversations", []).append({
                        "user": user, "message": text[:100],
                        "reply": reply_text[:100], "url": url,
                        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    })
                    if len(self.memory.get("conversations", [])) > 50:
                        self.memory["conversations"] = self.memory["conversations"][-50:]
            if replied >= 3:  # Max 3 replies par cycle
                break

        return {"success": True, "detail": f"{replied} replies sur {len(mentions)} mentions"}

    async def _check_own_tweet_replies(self):
        """Read replies to our own tweets and respond to build conversations."""
        try:
            replies = await browser.read_own_tweet_replies(3)
        except Exception as e:
            _log(f"[OWN REPLIES] Error reading replies: {e}")
            return

        if not replies:
            return

        replied = 0
        for r in replies:
            url = r.get("url", "")
            text = r.get("text", "")
            user = r.get("username", "")
            if not url or not text or not user:
                continue

            # Skip if already in conversations or already replied
            convos = self.memory.get("conversations", [])
            already_replied = any(
                c.get("url") == url or (c.get("user") == user and c.get("message", "")[:40] == text[:40])
                for c in convos
            )
            if already_replied or browser._is_duplicate("reply", url):
                continue

            # Like the reply
            if not browser._is_duplicate("like", url):
                await browser.like_tweet(url)

            # Generate and post a reply
            reply_text = await generate_smart_reply(text, user)
            if not reply_text:
                continue

            result = await browser.reply_tweet(url, reply_text)
            if result.get("success"):
                replied += 1
                _log(f"  [OWN REPLIES] Replied to @{user}: {reply_text[:60]}")
                browser._record_action("reply", browser._content_hash("reply", url))
                self.memory.setdefault("conversations", []).append({
                    "user": user, "message": text[:100],
                    "reply": reply_text[:100], "url": url,
                    "type": "own_tweet_reply",
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                })
                if len(self.memory.get("conversations", [])) > 50:
                    self.memory["conversations"] = self.memory["conversations"][-50:]

            if replied >= 3:
                break

        if replied:
            _log(f"[OWN REPLIES] {replied} replies to our tweet replies")

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

    async def _comment_github_ai_projects(self) -> dict:
        """#3: Commente sur des issues/discussions de projets AI."""
        projects = [
            "elizaOS/eliza", "langchain-ai/langchain", "Significant-Gravitas/AutoGPT",
            "microsoft/autogen", "crewai/crewai",
        ]
        commented = 0
        for project in projects[:2]:  # Max 2 par cycle
            try:
                # Chercher des issues ouvertes pertinentes
                results = await browser.search_google(f"site:github.com/{project}/issues AI agent marketplace", 3)
                for r in results:
                    url = r.get("url", "")
                    if "/issues/" in url and not browser._is_duplicate("github_comment", url):
                        comment = (
                            f"Interesting discussion! We're building MAXIA, an AI-to-AI marketplace "
                            f"where agents can discover and trade services using USDC on 14 chains "
                            f"(Solana, Base, ETH, XRP, Polygon, Arbitrum, Avalanche, BNB, TON, SUI, TRON, NEAR, Aptos, SEI). Happy to collaborate or integrate. "
                            f"Check it out: maxiaworld.app"
                        )
                        result = await browser.comment_github_discussion(url, comment)
                        if result.get("success"):
                            commented += 1
                            browser._record_action("github_comment", browser._content_hash("github_comment", url))
                        break
            except Exception:
                pass
        return {"success": commented > 0, "detail": f"{commented} GitHub comments"}

    async def _search_and_join_groups(self, platform: str = "telegram") -> dict:
        """Cherche et rejoint des groupes pertinents sur Telegram/Discord."""
        queries = ["Solana dev", "AI agents", "ElizaOS", "LangChain", "DeFi builders", "Web3 dev",
                   "Polygon developers", "Arbitrum builders", "Avalanche subnet", "BNB Chain dev",
                   "TON developers", "SUI Move dev", "TRON developers"]
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

    def _fix_params(self, action: str, params: dict) -> dict | None:
        """Valide et complete les params. Retourne None si action impossible."""
        if action == "post_tweet":
            if not params.get("text"):
                params["text"] = pick_tweet_template()
        elif action == "post_template_tweet":
            pass  # Pas besoin de params
        elif action == "post_thread":
            if not params.get("tweets") or not isinstance(params.get("tweets"), list):
                # Generer un thread basique
                params["tweets"] = [
                    "How to monetize your AI agent (thread):",
                    "1/ List your service on MAXIA with one API call. POST /api/public/sell. Done.",
                    "2/ Other AI agents discover and buy your service. You get paid in USDC. On 14 chains. maxiaworld.app?utm_source=twitter&utm_medium=tweet",
                ]
        elif action == "follow_user":
            if not params.get("username"):
                return None  # Impossible sans username
        elif action == "like_tweet":
            if not params.get("tweet_url"):
                return None
        elif action == "reply_tweet":
            if not params.get("tweet_url") or not params.get("text"):
                return None
        elif action == "score_profile":
            if not params.get("username"):
                return None
        elif action == "post_reddit":
            if not params.get("subreddit"):
                params["subreddit"] = "solanadev"
            if not params.get("title") or not params.get("body"):
                # Contenu sera genere dans _decide() (async) via _generate_reddit_post
                params["_needs_reddit_gen"] = True
        elif action == "comment_reddit":
            if not params.get("post_url") or not params.get("text"):
                return None
        elif action == "dm_twitter":
            if not params.get("username") or not params.get("text"):
                return None
        elif action == "search_twitter":
            if not params.get("query"):
                params["query"] = "AI agent solana developer"
        elif action == "search_profiles":
            if not params.get("query"):
                params["query"] = "AI agent developer solana web3"
        elif action == "search_groups":
            if not params.get("platform"):
                params["platform"] = "telegram"
        elif action == "scrape_followers":
            if not params.get("competitor"):
                params["competitor"] = random.choice(["JupiterExchange", "RunPod", "solaboratory"])
        elif action == "write_blog":
            if not params.get("topic"):
                params["topic"] = random.choice([
                    "How to monetize your AI agent with MAXIA",
                    "GPU rental at cost: why MAXIA charges 0% markup",
                    "14 chains, 1 marketplace: MAXIA multi-chain architecture",
                ])
        elif action == "watch_prices":
            pass
        elif action == "analyze_trends":
            pass
        # Les actions sans params requis
        elif action in ("reply_mentions", "manage_dms", "detect_opportunities",
                        "get_mentions", "check_ab", "comment_github_ai",
                        "clean_screenshots", "list_services", "ab_test"):
            pass
        return params

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
