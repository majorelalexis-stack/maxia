"""CEO MAXIA — Agent Autonome V4 (Final)

Architecture hybride : Groq (tactique) + Sonnet (strategie) + Opus (vision/expansion)
Un seul agent, une seule memoire, 4 boucles, 7 sous-agents, 5 mecanismes internes.

SOUS-AGENTS :
  GHOST-WRITER   : Contenu, tweets, threads (valide par WATCHDOG avant publication)
  HUNTER         : Prospection HUMAINE profil Thomas (devs avec bots IA sans revenus)
  SCOUT          : Prospection IA-to-IA sur Solana/Base/Ethereum (Olas, Fetch, ElizaOS, Virtuals)
  WATCHDOG       : Monitoring, validation, self-healing (propose des patchs)
  SOL-TREASURY   : Budget dynamique, gas, ROI, remboursements
  RESPONDER      : Repond a TOUS messages 24/7 (Twitter, Discord, Telegram, API)
  RADAR          : Intelligence on-chain predictive (detecte tendances en temps reel)
  TESTIMONIAL    : Sollicite feedback post-transaction, construit social proof
  NEGOTIATOR     : Negociation automatique des prix (loyalty discount, bundles, contre-offres)
  COMPLIANCE     : Verification AML/sanctions, screening wallets, validation transactions
  PARTNERSHIP    : Detection et demarchage de partenariats strategiques (DEX, GPU, AI protocols)
  ANALYTICS      : Metriques avancees (LTV, churn, funnel, health score, rapports hebdo)
  CRISIS-MANAGER : Gestion automatique des crises (P0-P3, pause marketing, self-heal, retention)

BOUCLES :
  1. TACTIQUE     (horaire)    — Groq    — decisions rapides
  2. STRATEGIQUE  (quotidienne)— Sonnet  — SWOT + Red Teaming (avocat du diable)
  3. VISION       (hebdo)      — Opus    — OKR, roadmap, produits, compaction memoire
  4. EXPANSION    (mensuelle)  — Opus    — marche mondial, multi-chain, multi-langue

MECANISMES INTERNES :
  - Emergency Stop : bloque si >5 decisions orange sans revenu
  - Budget dynamique : decay 50%/semaine sans revenu
  - Auto-switch HUNTER : change canal si <1% conversion
  - Red Teaming : avocat du diable dans boucle strategique
  - Self-Healing : WATCHDOG detecte erreurs, Sonnet propose un patch
  - Compaction memoire : Opus resume en lecons cles chaque dimanche
  - Gestion fondateur : adapte ton selon activite/inactivite
"""
import asyncio, json, time, os
from datetime import datetime, date


# ══════════════════════════════════════════
# AGENT BUS — Communication inter-agents
# ══════════════════════════════════════════

class AgentBus:
    """Bus de messages entre sous-agents. Permet la communication directe sans passer par le CEO."""

    def __init__(self):
        self._queue: list = []       # messages en attente
        self._processed: list = []   # 100 derniers messages traites
        self._subscribers: dict = {} # {agent_name: [callback_fn, ...]}
        self._max_queue = 200
        self._max_processed = 100

    def send(self, sender: str, receiver: str, msg_type: str, data: dict):
        """Envoie un message d'un agent a un autre."""
        message = {
            "ts": datetime.utcnow().isoformat(),
            "sender": sender.upper(),
            "receiver": receiver.upper(),
            "type": msg_type,
            "data": data,
            "processed": False,
        }
        self._queue.append(message)
        if len(self._queue) > self._max_queue:
            self._queue = self._queue[-self._max_queue:]
        print(f"[BUS] {sender} -> {receiver}: {msg_type}")

    def broadcast(self, sender: str, msg_type: str, data: dict):
        """Diffuse un message a tous les agents."""
        self.send(sender, "*", msg_type, data)

    def get_messages(self, agent: str, msg_type: str = None) -> list:
        """Recupere les messages non traites pour un agent."""
        agent = agent.upper()
        msgs = [
            m for m in self._queue
            if not m["processed"] and (m["receiver"] == agent or m["receiver"] == "*")
            and (msg_type is None or m["type"] == msg_type)
        ]
        return msgs

    def ack(self, agent: str, msg_type: str = None):
        """Marque les messages comme traites."""
        agent = agent.upper()
        for m in self._queue:
            if not m["processed"] and (m["receiver"] == agent or m["receiver"] == "*"):
                if msg_type is None or m["type"] == msg_type:
                    m["processed"] = True
                    self._processed.append(m)
        # Nettoyer la queue
        self._queue = [m for m in self._queue if not m["processed"]]
        self._processed = self._processed[-self._max_processed:]

    def get_stats(self) -> dict:
        return {
            "pending": len(self._queue),
            "processed": len(self._processed),
            "recent": self._processed[-5:] if self._processed else [],
        }


agent_bus = AgentBus()


# ══════════════════════════════════════════
# TASK QUEUE — async queue pour taches lourdes
# ══════════════════════════════════════════

class TaskQueue:
    """Simple async task queue pour deporter les taches lourdes hors du cycle principal."""

    def __init__(self, max_size: int = 100):
        self._queue = asyncio.Queue(maxsize=max_size)
        self._processed = 0
        self._errors = 0
        self._running = False

    async def put(self, task_name: str, coro_fn, *args):
        """Ajoute une tache a la queue."""
        try:
            self._queue.put_nowait((task_name, coro_fn, args))
        except asyncio.QueueFull:
            print(f"[TaskQueue] FULL — dropping {task_name}")

    async def worker(self):
        """Worker qui traite les taches en background."""
        self._running = True
        while self._running:
            try:
                task_name, coro_fn, args = await asyncio.wait_for(self._queue.get(), timeout=5)
                try:
                    await coro_fn(*args)
                    self._processed += 1
                except Exception as e:
                    self._errors += 1
                    print(f"[TaskQueue] Error in {task_name}: {e}")
                self._queue.task_done()
            except asyncio.TimeoutError:
                continue
            except Exception:
                break

    def stop(self):
        self._running = False

    def get_stats(self) -> dict:
        return {
            "pending": self._queue.qsize(),
            "processed": self._processed,
            "errors": self._errors,
        }


task_queue = TaskQueue()


# ══════════════════════════════════════════
# CONFIGURATION — read from config.py if available, else os.getenv
# ══════════════════════════════════════════

def _cfg(name, default=""):
    """Read from config.py first, then os.getenv."""
    try:
        import config
        return getattr(config, name, os.getenv(name, default))
    except ImportError:
        return os.getenv(name, default)

GROQ_API_KEY = _cfg("GROQ_API_KEY")
ANTHROPIC_API_KEY = _cfg("ANTHROPIC_API_KEY")
DISCORD_WEBHOOK_URL = _cfg("DISCORD_WEBHOOK_URL")
TWITTER_API_KEY = _cfg("TWITTER_API_KEY")

GROQ_MODEL = "llama-3.3-70b-versatile"
SONNET_MODEL = "claude-sonnet-4-20250514"
OPUS_MODEL = "claude-opus-4-20250514"

FOUNDER_NAME = "Alexis"
COMPANY = "MAXIA"
PRODUCT = "AI Marketplace on Solana — swap 15 tokens 210 paires, stocks 10 actions, GPU, services IA"
PHASE = "Pre-seed"
VISION = "Devenir la couche d'intelligence liquide de l'ecosysteme Solana"
URL = "maxiaworld.app"
MAXIA_URL = "https://maxiaworld.app"

BASE_BUDGET_VERT = 0.05
BASE_BUDGET_ORANGE = 0.5
BUDGET_ROUGE = 1.0
BUDGET_DECAY_WEEKLY = 0.5
MIN_BUDGET_VERT = 0.005
HUNTER_MIN_CONVERSION = 0.01
EMERGENCY_ORANGE_LIMIT = 5
MAX_PROSPECTS_DAY = 10
MAX_TWEETS_DAY = 5


# ══════════════════════════════════════════
# IDENTITE CEO
# ══════════════════════════════════════════

CEO_IDENTITY = f"""Tu es CEO MAXIA, dirigeant autonome de {COMPANY}.
Produit : {PRODUCT}
Phase : {PHASE} | Vision : {VISION}
Fondateur : {FOUNDER_NAME} (autorite finale sur decisions rouges)
URL : {URL}

13 SOUS-AGENTS :
- GHOST-WRITER : contenu (JAMAIS publier sans validation WATCHDOG)
- HUNTER : prospection HUMAINE profil Thomas (devs avec bots IA, canaux: Twitter/Discord/Reddit/GitHub)
- SCOUT : prospection IA-to-IA sur 3 chains (Solana/Base/Ethereum) — contacte agents autonomes (Olas, Fetch, ElizaOS, Virtuals)
- WATCHDOG : monitoring + validation + self-healing
- SOL-TREASURY : budget dynamique indexe revenus
- RESPONDER : repond a TOUS messages 24/7
- RADAR : intelligence on-chain predictive (tendances, volumes)
- TESTIMONIAL : feedback post-transaction, social proof
- NEGOTIATOR : negocie les prix automatiquement (loyalty, bundles, contre-offres)
- COMPLIANCE : verifie wallets/transactions (AML, sanctions OFAC, anti-fraude)
- PARTNERSHIP : detecte et contacte des partenaires strategiques (DEX, protocols, GPU)
- ANALYTICS : metriques avancees (LTV, churn, funnel, health score 0-100)
- CRISIS-MANAGER : detecte et gere les crises (P0 critique -> P3 mineure)

PROTOCOLE (Chain of Thought) :
1. COLLECTE donnees sous-agents
2. EVALUATION quel agent echoue et pourquoi
3. RESOLUTION ajustement interne ou escalade fondateur
4. EXECUTION directives precises

REGLES :
- Reflechis etape par etape a haute voix
- Pragmatique, patient (7j avant juger), honnete, frugal, adaptable
- Rembourse client mecontent sans discuter
- En Pre-seed : priorite = liquidite des feedbacks, pas perfection technique
- Si HUNTER < 1% conversion : OBLIGATION de changer canal SANS permission

NIVEAUX : VERT (auto) | ORANGE (max 1/j, log) | ROUGE (fondateur)
VALIDATION : GHOST-WRITER ne publie PAS si WATCHDOG dit service DOWN

OBJECTIFS DU FONDATEUR (NON NEGOCIABLES) :
1. MAXIA doit devenir une plateforme MONDIALEMENT RECONNUE
2. Etre la MOINS CHERE du marche dans TOUS les domaines (swap, GPU, IA, stocks)
3. Objectif revenu : 10 000 euros/mois (delai non defini, le plus vite possible)
4. Le fondateur GARDE LE CONTROLE TOTAL — pas de DAO, pas de gouvernance communautaire
5. Volume > Marge : mieux vaut 10000 clients a 0.01 que 10 clients a 10

STRATEGIE PRIX :
- Toujours verifier les prix concurrents (Jupiter, Binance, AWS, RunPod, Certik)
- Si un concurrent est moins cher → baisser IMMEDIATEMENT
- Marge minimale : au-dessus du cout reel (ne jamais perdre d argent)
- GPU : 0% marge (deja le moins cher)
- Swap : descendre jusqu a 0.01% si necessaire
- Le revenu vient du VOLUME, pas du prix unitaire

CLIENT CIBLE (profil "Thomas" — le Dev Agent) :
- Age 26-34 ans, dev Python, connait Solana ou Ethereum
- A construit un agent IA qui FONCTIONNE mais ne GAGNE PAS d argent
- Frustration : "Mon bot tourne dans le vide, 0 clients"
- A deja essaye : Twitter (47 followers), Product Hunt (noye), Stripe (trop de friction pour $0.50)
- Ce qu il veut : POST /sell → son service est live, d autres IA l achetent, USDC arrive
- Ce qu il veut PAS : site web, marketing, gestion clients, token, waitlist
- Ou il est : Twitter (threads AI/crypto), Discord (Solana dev, ElizaOS, LangChain), GitHub, Reddit (r/solanadev)
- Phrase qui l arrete : "Your AI agent can earn USDC while you sleep. One API call to list it."
- Ce qui le rassure : open source, pas de token, USDC stable, GitHub avec vrai code
- Ce qui le fait fuir : "join waitlist", "buy our token", "schedule a demo"

STRATEGIE MARKETING :
- HUNTER doit cibler des DEVELOPPEURS qui deploient des programmes on-chain (BPFLoader)
- Messages centres sur GAGNER de l argent, pas acheter des services
- Ton technique, pas commercial — parler comme un dev, pas comme un marketeur
- Canaux prioritaires : memos Solana aux devs, Discord, Reddit, GitHub
- Ne JAMAIS envoyer le meme message 2 fois au meme wallet

METRIC CLE : nombre d agents inscrits qui listent un service (pas juste inscrits)"""


# ══════════════════════════════════════════
# LLM CERVEAUX + Cost Tracking
# ══════════════════════════════════════════

# Cost tracking (estimated costs per 1K tokens)
_llm_costs = {
    "calls": 0, "tokens_in": 0, "tokens_out": 0,
    "cost_usd": 0.0,
    "by_model": {},
}

# Approximate costs per 1K tokens (input/output)
_MODEL_COSTS = {
    "llama-3.3-70b-versatile": (0.0, 0.0),  # Groq free tier
    "claude-sonnet-4-20250514": (0.003, 0.015),
    "claude-opus-4-20250514": (0.015, 0.075),
}


def _track_llm_cost(model: str, tokens_in: int, tokens_out: int):
    """Track LLM usage and estimated cost."""
    _llm_costs["calls"] += 1
    _llm_costs["tokens_in"] += tokens_in
    _llm_costs["tokens_out"] += tokens_out
    rates = _MODEL_COSTS.get(model, (0, 0))
    cost = (tokens_in / 1000 * rates[0]) + (tokens_out / 1000 * rates[1])
    _llm_costs["cost_usd"] += cost
    _llm_costs.setdefault("by_model", {})
    _llm_costs["by_model"].setdefault(model, {"calls": 0, "cost": 0})
    _llm_costs["by_model"][model]["calls"] += 1
    _llm_costs["by_model"][model]["cost"] = round(_llm_costs["by_model"][model]["cost"] + cost, 4)


def get_llm_costs() -> dict:
    return {
        "total_calls": _llm_costs["calls"],
        "total_tokens_in": _llm_costs["tokens_in"],
        "total_tokens_out": _llm_costs["tokens_out"],
        "estimated_cost_usd": round(_llm_costs["cost_usd"], 4),
        "by_model": _llm_costs.get("by_model", {}),
    }


async def _call_groq(system: str, user: str, max_tokens: int = 1500) -> str:
    if not GROQ_API_KEY:
        return ""
    try:
        from groq import Groq
        c = Groq(api_key=GROQ_API_KEY)
        def _c():
            resp = c.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                max_tokens=max_tokens, temperature=0.7,
            )
            # Track tokens
            usage = resp.usage
            if usage:
                _track_llm_cost(GROQ_MODEL, usage.prompt_tokens or 0, usage.completion_tokens or 0)
            return resp.choices[0].message.content.strip()
        return await asyncio.to_thread(_c)
    except Exception as e:
        print(f"[CEO] Groq error: {e}")
        return ""


async def _call_anthropic(model: str, system: str, user: str, max_tokens: int = 3000) -> str:
    if not ANTHROPIC_API_KEY:
        return await _call_groq(system, user, min(max_tokens, 1500))
    try:
        import httpx
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": model, "max_tokens": max_tokens, "system": system, "messages": [{"role": "user", "content": user}]},
            )
            data = resp.json()
            # Track Anthropic tokens
            usage = data.get("usage", {})
            if usage:
                _track_llm_cost(model, usage.get("input_tokens", 0), usage.get("output_tokens", 0))
            ct = data.get("content", [])
            return ct[0].get("text", "") if ct else ""
    except Exception as e:
        print(f"[CEO] Anthropic error: {e}")
        return await _call_groq(system, user, min(max_tokens, 1500))


def _pj(response: str) -> dict:
    """Parse JSON tolerant."""
    if not response:
        return {}
    try:
        c = response.strip()
        for p in ["```json", "```"]:
            if c.startswith(p): c = c[len(p):]
        if c.endswith("```"): c = c[:-3]
        return json.loads(c.strip())
    except json.JSONDecodeError:
        try:
            return json.loads(response[response.index("{"):response.rindex("}") + 1])
        except (ValueError, json.JSONDecodeError):
            return {}


# ══════════════════════════════════════════
# ALERTES DISCORD
# ══════════════════════════════════════════

async def _discord(message: str):
    if not DISCORD_WEBHOOK_URL:
        print(f"[CEO] {message[:150]}")
        return
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(DISCORD_WEBHOOK_URL, json={"content": message[:1900]})
    except Exception:
        pass


async def alert_rouge(titre: str, contexte: str, deadline_h: int = 2):
    msg = (f"🔴 **ALERTE ROUGE — CEO MAXIA**\n\n**{titre}**\n\n{contexte}\n\n"
           f"⏰ **Go/No-Go sous {deadline_h}h**")
    await _discord(msg)
    print(f"[CEO ROUGE] {titre}")


async def alert_info(msg: str):
    await _discord(f"🤖 **CEO MAXIA** : {msg}")


# ══════════════════════════════════════════
# MEMOIRE
# ══════════════════════════════════════════

class Memory:
    def __init__(self, path="ceo_memory.json"):
        self._path = path
        self._data = {
            "decisions": [], "rapports": [], "strategies": [], "expansions": [],
            "regles": [], "lecons_cles": [], "tendances_utilisateurs": [],
            "kpi": [], "produits": [], "agents": {}, "conversations": [],
            "erreurs_recurrentes": [], "patchs_proposes": [],
            "testimonials": [], "radar_alerts": [],
            "okr": {}, "roadmap": "", "marche": {}, "concurrents": [],
            "langues": ["en"], "chains": ["solana"],
            "budget_vert": BASE_BUDGET_VERT, "budget_orange": BASE_BUDGET_ORANGE,
            "semaines_0rev": 0, "emergency_stop": False,
            "spent_sol": 0, "revenue_usd": 0, "clients": 0, "responses": 0,
            "hunter_canal": "solana_memo", "hunter_contacts": 0, "hunter_converts": 0,
            "fondateur_derniere_reponse": datetime.utcnow().isoformat(),
            "fondateur_alertes_ignorees": 0,
            "derniere_compaction": "", "started": datetime.utcnow().isoformat(),
        }
        self._load()

    def _load(self):
        try:
            if os.path.exists(self._path):
                with open(self._path) as f:
                    self._data.update(json.load(f))
        except Exception:
            pass

    def save(self):
        # EMERGENCY STOP CHECK avant chaque sauvegarde
        if self.check_emergency_stop():
            if not self._data.get("emergency_stop"):
                self._data["emergency_stop"] = True
                print("[CEO] ⛔ EMERGENCY STOP ACTIVE — trop de depenses sans revenu")
        # Memory rotation — garder les listes a taille raisonnable
        self._trim()
        try:
            with open(self._path, "w") as f:
                json.dump(self._data, f, indent=2, default=str)
        except Exception as e:
            print(f"[CEO] Save error: {e}")

    def _trim(self):
        """Limite la taille de la memoire pour eviter les fichiers de plusieurs MB."""
        limits = {
            "decisions": 500, "rapports": 100, "strategies": 50, "expansions": 20,
            "regles": 200, "lecons_cles": 100, "kpi": 500,
            "conversations": 300, "testimonials": 200, "radar_alerts": 200,
            "erreurs_recurrentes": 50, "patchs_proposes": 50,
            "tendances_utilisateurs": 100, "produits": 50,
        }
        trimmed = False
        for key, max_len in limits.items():
            lst = self._data.get(key)
            if isinstance(lst, list) and len(lst) > max_len:
                self._data[key] = lst[-max_len:]
                trimmed = True
        if trimmed:
            print("[CEO] Memory trimmed (rotation)")

    def check_emergency_stop(self) -> bool:
        """Si >5 decisions orange sans revenu, STOP tout."""
        if self._data.get("revenue_usd", 0) > 0:
            self._data["emergency_stop"] = False
            return False
        orange = [d for d in self._data.get("decisions", []) if d.get("level") == "orange"]
        if len(orange) > EMERGENCY_ORANGE_LIMIT:
            return True
        if self._data.get("spent_sol", 0) > 0.5 and self._data.get("revenue_usd", 0) == 0:
            return True
        return False

    def is_stopped(self) -> bool:
        return self._data.get("emergency_stop", False)

    def reset_emergency(self):
        self._data["emergency_stop"] = False
        self.save()

    # ── Kill switch granulaire ──

    def disable_agent(self, agent_name: str, reason: str = "manual"):
        """Desactive un sous-agent specifique sans tout arreter."""
        if "disabled_agents" not in self._data:
            self._data["disabled_agents"] = {}
        self._data["disabled_agents"][agent_name.upper()] = {
            "disabled_at": datetime.utcnow().isoformat(),
            "reason": reason,
        }
        self.save()
        print(f"[CEO] Agent {agent_name} DISABLED: {reason}")

    def enable_agent(self, agent_name: str):
        """Reactive un sous-agent."""
        disabled = self._data.get("disabled_agents", {})
        if agent_name.upper() in disabled:
            del disabled[agent_name.upper()]
            self.save()
            print(f"[CEO] Agent {agent_name} RE-ENABLED")

    def is_agent_disabled(self, agent_name: str) -> bool:
        """Verifie si un agent est desactive."""
        return agent_name.upper() in self._data.get("disabled_agents", {})

    def get_disabled_agents(self) -> dict:
        return self._data.get("disabled_agents", {})

    # ── ROI Tracking ──

    def log_action_with_tracking(self, agent: str, action_type: str, action_id: str, details: str = ""):
        """Log une action avec un ID unique pour tracker le ROI."""
        if "roi_tracking" not in self._data:
            self._data["roi_tracking"] = []
        self._data["roi_tracking"].append({
            "ts": datetime.utcnow().isoformat(),
            "agent": agent,
            "type": action_type,  # tweet, prospect, blog, outreach
            "action_id": action_id,
            "details": details[:200],
            "conversions": 0,
            "revenue": 0,
        })
        self._data["roi_tracking"] = self._data["roi_tracking"][-500:]

    def record_conversion(self, action_id: str, revenue: float = 0):
        """Enregistre une conversion liee a une action."""
        for entry in reversed(self._data.get("roi_tracking", [])):
            if entry.get("action_id") == action_id:
                entry["conversions"] = entry.get("conversions", 0) + 1
                entry["revenue"] = entry.get("revenue", 0) + revenue
                self.save()
                return True
        return False

    def get_roi_stats(self) -> dict:
        """Retourne les stats ROI par agent et par type d'action."""
        tracking = self._data.get("roi_tracking", [])
        by_agent = {}
        by_type = {}
        for entry in tracking:
            agent = entry.get("agent", "?")
            atype = entry.get("type", "?")
            by_agent.setdefault(agent, {"actions": 0, "conversions": 0, "revenue": 0})
            by_agent[agent]["actions"] += 1
            by_agent[agent]["conversions"] += entry.get("conversions", 0)
            by_agent[agent]["revenue"] += entry.get("revenue", 0)
            by_type.setdefault(atype, {"actions": 0, "conversions": 0, "revenue": 0})
            by_type[atype]["actions"] += 1
            by_type[atype]["conversions"] += entry.get("conversions", 0)
            by_type[atype]["revenue"] += entry.get("revenue", 0)
        return {"by_agent": by_agent, "by_type": by_type, "total_tracked": len(tracking)}

    # ── A/B Testing ──

    def create_ab_test(self, test_name: str, variant_a: str, variant_b: str):
        """Cree un test A/B."""
        if "ab_tests" not in self._data:
            self._data["ab_tests"] = {}
        self._data["ab_tests"][test_name] = {
            "created": datetime.utcnow().isoformat(),
            "variants": {
                "A": {"content": variant_a, "impressions": 0, "conversions": 0},
                "B": {"content": variant_b, "impressions": 0, "conversions": 0},
            },
            "status": "active",
            "winner": None,
        }
        self.save()

    def get_ab_variant(self, test_name: str) -> tuple:
        """Retourne le variant a utiliser (round-robin). Returns (variant_key, content)."""
        test = self._data.get("ab_tests", {}).get(test_name)
        if not test or test.get("status") != "active":
            return ("A", "")
        variants = test["variants"]
        # Choisir le variant avec le moins d'impressions
        if variants["A"]["impressions"] <= variants["B"]["impressions"]:
            variants["A"]["impressions"] += 1
            return ("A", variants["A"]["content"])
        else:
            variants["B"]["impressions"] += 1
            return ("B", variants["B"]["content"])

    def record_ab_conversion(self, test_name: str, variant_key: str):
        """Enregistre une conversion pour un variant."""
        test = self._data.get("ab_tests", {}).get(test_name)
        if not test:
            return
        test["variants"][variant_key]["conversions"] += 1
        # Auto-declare winner apres 100 impressions chacun
        a = test["variants"]["A"]
        b = test["variants"]["B"]
        if a["impressions"] >= 100 and b["impressions"] >= 100:
            rate_a = a["conversions"] / max(1, a["impressions"])
            rate_b = b["conversions"] / max(1, b["impressions"])
            if rate_a > rate_b * 1.2:  # A gagne par >20%
                test["winner"] = "A"
                test["status"] = "completed"
            elif rate_b > rate_a * 1.2:
                test["winner"] = "B"
                test["status"] = "completed"
        self.save()

    def get_ab_results(self) -> dict:
        return self._data.get("ab_tests", {})

    # ── Logging ──

    def log_decision(self, level: str, decision: str, raison: str, cible: str):
        self._data["decisions"].append({
            "ts": datetime.utcnow().isoformat(), "level": level,
            "decision": decision[:300], "raison": raison[:200], "cible": cible,
        })
        self._data["decisions"] = self._data["decisions"][-300:]
        self.save()

    def log_rapport(self, r: dict):
        r["date"] = date.today().isoformat()
        self._data["rapports"].append(r)
        self._data["rapports"] = self._data["rapports"][-60:]
        self.save()

    def log_strategie(self, s: dict):
        s["date"] = date.today().isoformat()
        self._data["strategies"].append(s)
        self._data["strategies"] = self._data["strategies"][-24:]
        self.save()

    def log_expansion(self, e: dict):
        e["date"] = date.today().isoformat()
        self._data["expansions"].append(e)
        self._data["expansions"] = self._data["expansions"][-12:]
        self.save()

    def log_conversation(self, canal: str, user: str, msg: str, rep: str, intention: str):
        self._data["conversations"].append({
            "ts": datetime.utcnow().isoformat(), "canal": canal,
            "user": user, "msg": msg[:200], "rep": rep[:200], "intention": intention,
        })
        self._data["conversations"] = self._data["conversations"][-500:]
        self._data["responses"] += 1
        self.save()

    def log_kpi(self, kpi: dict):
        kpi["ts"] = datetime.utcnow().isoformat()
        self._data["kpi"].append(kpi)
        self._data["kpi"] = self._data["kpi"][-1440:]
        self.save()

    def log_testimonial(self, user: str, tx: str, feedback: str, published: bool):
        self._data["testimonials"].append({
            "ts": datetime.utcnow().isoformat(), "user": user,
            "tx": tx, "feedback": feedback[:200], "published": published,
        })
        self._data["testimonials"] = self._data["testimonials"][-100:]
        self.save()

    def log_radar_alert(self, alert_type: str, details: str):
        self._data["radar_alerts"].append({
            "ts": datetime.utcnow().isoformat(), "type": alert_type, "details": details[:200],
        })
        self._data["radar_alerts"] = self._data["radar_alerts"][-100:]
        self.save()

    def log_error(self, source: str, error: str, count: int = 1):
        existing = next((e for e in self._data["erreurs_recurrentes"] if e["source"] == source), None)
        if existing:
            existing["count"] = existing.get("count", 0) + count
            existing["last"] = datetime.utcnow().isoformat()
            existing["error"] = error[:200]
            # Auto-learn: si une action echoue 3+ fois, ajouter une regle
            if existing["count"] == 3:
                regle = f"AUTO-LEARN: {source} a echoue 3 fois ({error[:60]}). Eviter cette action."
                self.add_regle(regle)
                print(f"[CEO] AUTO-LEARN: nouvelle regle creee pour {source}")
            # Auto-disable: si une action echoue 5+ fois, bloquer l'agent concerne
            if existing["count"] >= 5 and not existing.get("auto_disabled"):
                # Extraire le nom de l'agent depuis la source
                agent_name = source.replace("ceo_executor_", "").upper()
                agent_map = {"TWEET": "GHOST-WRITER", "PROSPECT": "HUNTER", "BLOG": "DEPLOYER",
                             "SCOUT": "SCOUT", "PRICE": "SOL-TREASURY"}
                mapped = agent_map.get(agent_name, "")
                if mapped and not self.is_agent_disabled(mapped):
                    self.disable_agent(mapped, f"Auto-disabled: {source} failed {existing['count']} times")
                    existing["auto_disabled"] = True
                    print(f"[CEO] AUTO-DISABLE: {mapped} desactive apres {existing['count']} echecs")
        else:
            self._data["erreurs_recurrentes"].append({
                "source": source, "error": error[:200], "count": count,
                "first": datetime.utcnow().isoformat(), "last": datetime.utcnow().isoformat(),
                "patch_proposed": False,
            })
        self._data["erreurs_recurrentes"] = self._data["erreurs_recurrentes"][-50:]
        self.save()

    def log_patch(self, source: str, patch: str):
        self._data["patchs_proposes"].append({
            "ts": datetime.utcnow().isoformat(), "source": source,
            "patch": patch[:500], "applied": False,
        })
        self._data["patchs_proposes"] = self._data["patchs_proposes"][-20:]
        self.save()

    def add_regle(self, r: str):
        if r and r not in self._data["regles"]:
            self._data["regles"].append(r)
            self._data["regles"] = self._data["regles"][-50:]
            self.save()

    def add_lecon(self, l: str):
        if l and l not in self._data["lecons_cles"]:
            self._data["lecons_cles"].append(l)
            self._data["lecons_cles"] = self._data["lecons_cles"][-30:]
            self.save()

    def update_agent(self, name: str, status: dict):
        self._data["agents"][name] = {**status, "at": datetime.utcnow().isoformat()}

    def update_okr(self, okr: dict):
        """Met a jour les OKR (Objectives & Key Results)."""
        self._data["okr"] = okr
        self.save()

    def update_roadmap(self, roadmap):
        """Met a jour la roadmap."""
        self._data["roadmap"] = roadmap
        self.save()

    # ── Budget dynamique ──

    def update_budget(self, rev_week: float):
        if rev_week > 0:
            self._data["semaines_0rev"] = 0
            self._data["budget_vert"] = BASE_BUDGET_VERT
            self._data["budget_orange"] = BASE_BUDGET_ORANGE
        else:
            self._data["semaines_0rev"] += 1
            decay = BUDGET_DECAY_WEEKLY ** self._data["semaines_0rev"]
            self._data["budget_vert"] = max(MIN_BUDGET_VERT, BASE_BUDGET_VERT * decay)
            self._data["budget_orange"] = max(MIN_BUDGET_VERT * 10, BASE_BUDGET_ORANGE * decay)
        self.save()

    def get_budget_vert(self) -> float:
        return self._data.get("budget_vert", BASE_BUDGET_VERT)

    # ── Hunter ──

    def hunter_contact(self, converted: bool):
        self._data["hunter_contacts"] += 1
        if converted:
            self._data["hunter_converts"] += 1
        self.save()

    def hunter_rate(self) -> float:
        t = self._data.get("hunter_contacts", 0)
        return self._data.get("hunter_converts", 0) / t if t > 0 else 0

    def hunter_switch(self, new: str) -> str:
        old = self._data.get("hunter_canal", "?")
        self._data["hunter_canal"] = new
        self._data["hunter_contacts"] = 0
        self._data["hunter_converts"] = 0
        self.save()
        return old

    # ── Fondateur ──

    def fondateur_responded(self):
        self._data["fondateur_derniere_reponse"] = datetime.utcnow().isoformat()
        self._data["fondateur_alertes_ignorees"] = 0
        self.save()

    def fondateur_ignored_alert(self):
        self._data["fondateur_alertes_ignorees"] = self._data.get("fondateur_alertes_ignorees", 0) + 1
        self.save()

    def fondateur_days_inactive(self) -> int:
        last = self._data.get("fondateur_derniere_reponse", datetime.utcnow().isoformat())
        try:
            delta = datetime.utcnow() - datetime.fromisoformat(last)
            return delta.days
        except Exception:
            return 0

    # ── Compaction + Summarize ──

    async def compact(self, summarize_fn):
        """Compacte decisions en lecons cles."""
        today = date.today().isoformat()
        if self._data.get("derniere_compaction") == today:
            return
        decs = self._data.get("decisions", [])
        if len(decs) < 50:
            return
        print("[CEO] Compaction decisions...")
        prompt = (
            "Resume ces decisions en 10 LECONS CLES actionnables pour MAXIA.\n"
            "Format: JSON array de 10 strings.\n"
            "Chaque lecon = un fait concret et specifique."
        )
        result = await summarize_fn(prompt, json.dumps(decs[-200:], indent=1, default=str))
        try:
            lecons = json.loads(result) if isinstance(result, str) else result
            if isinstance(lecons, list):
                for l in lecons:
                    self.add_lecon(str(l))
                self._data["decisions"] = self._data["decisions"][-30:]
                print(f"[CEO] Compaction OK — {len(lecons)} lecons, historique purge")
        except Exception as e:
            print(f"[CEO] Compaction error: {e}")

    async def summarize_old_data(self, summarize_fn):
        """Transforme les conversations en 'Tendances Utilisateurs'."""
        convs = self._data.get("conversations", [])
        if len(convs) < 100:
            return
        print("[CEO] Summarize conversations...")
        prompt = (
            "Analyse ces conversations et genere un paragraphe 'TENDANCES UTILISATEURS'.\n"
            "Inclus : % par intention (technique, prospect, plainte, spam), canaux les plus actifs,\n"
            "questions les plus frequentes, profil type de l'utilisateur.\n"
            "Format: JSON {tendances: 'paragraphe', stats: {intention_pcts: {}, top_canal: '', top_question: ''}}"
        )
        result = await summarize_fn(prompt, json.dumps(convs[-300:], indent=1, default=str))
        try:
            data = json.loads(result) if isinstance(result, str) else result
            if isinstance(data, dict):
                data["date"] = date.today().isoformat()
                self._data["tendances_utilisateurs"].append(data)
                self._data["tendances_utilisateurs"] = self._data["tendances_utilisateurs"][-12:]
                self._data["conversations"] = self._data["conversations"][-20:]
                print(f"[CEO] Tendances OK — conversations purgees (garde 20)")
        except Exception as e:
            print(f"[CEO] Summarize error: {e}")
        self._data["derniere_compaction"] = date.today().isoformat()
        self.save()

    # ── Contexte LLM ──

    def ctx(self, level: str) -> str:
        d = self._data
        base = (
            f"DATE: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"REVENUE: ${d['revenue_usd']} | CLIENTS: {d['clients']}\n"
            f"BUDGET VERT: {d.get('budget_vert', BASE_BUDGET_VERT):.4f} SOL "
            f"(semaines sans revenu: {d.get('semaines_0rev', 0)})\n"
            f"EMERGENCY STOP: {'⛔ OUI' if d.get('emergency_stop') else 'Non'}\n"
            f"HUNTER: {d.get('hunter_canal','?')} ({self.hunter_rate():.1%} conversion)\n"
            f"FONDATEUR: inactif {self.fondateur_days_inactive()}j, "
            f"alertes ignorees: {d.get('fondateur_alertes_ignorees', 0)}\n\n"
        )
        if d.get("lecons_cles"):
            base += "LECONS CLES:\n" + "\n".join(f"  - {l}" for l in d["lecons_cles"][-10:]) + "\n\n"
        if d.get("tendances_utilisateurs"):
            last_t = d["tendances_utilisateurs"][-1]
            base += f"TENDANCES UTILISATEURS:\n  {last_t.get('tendances','N/A')}\n\n"
        if d.get("regles"):
            base += "REGLES:\n" + "\n".join(f"  - {r}" for r in d["regles"][-10:]) + "\n\n"

        agents = "AGENTS:\n"
        for n, s in d.get("agents", {}).items():
            agents += f"  {n}: {json.dumps(s, default=str)}\n"

        if level == "tactique":
            return (base + agents +
                f"\nKPI:\n{json.dumps(d['kpi'][-3:], indent=1, default=str)}\n"
                f"DECISIONS:\n{json.dumps(d['decisions'][-5:], indent=1, default=str)}\n"
                f"RADAR:\n{json.dumps(d['radar_alerts'][-3:], indent=1, default=str)}\n"
                f"ERREURS:\n{json.dumps(d['erreurs_recurrentes'][-3:], indent=1, default=str)}\n")
        elif level == "strategique":
            return (base + agents +
                f"\nKPI 24H:\n{json.dumps(d['kpi'][-24:], indent=1, default=str)}\n"
                f"CONVERSATIONS:\n{json.dumps(d['conversations'][-10:], indent=1, default=str)}\n"
                f"TESTIMONIALS:\n{json.dumps(d['testimonials'][-5:], indent=1, default=str)}\n"
                f"ERREURS RECURRENTES:\n{json.dumps(d['erreurs_recurrentes'], indent=1, default=str)}\n"
                f"OKR:\n{json.dumps(d['okr'], indent=1, default=str)}\n")
        elif level == "vision":
            return (base + agents +
                f"\nRAPPORTS 7J:\n{json.dumps(d['rapports'][-7:], indent=1, default=str)}\n"
                f"STRATEGIES:\n{json.dumps(d['strategies'][-4:], indent=1, default=str)}\n"
                f"PRODUITS:\n{json.dumps(d['produits'], indent=1, default=str)}\n"
                f"PATCHS:\n{json.dumps(d['patchs_proposes'][-5:], indent=1, default=str)}\n"
                f"OKR:\n{json.dumps(d['okr'], indent=1, default=str)}\nROADMAP:\n{d['roadmap']}\n")
        elif level == "expansion":
            return (base + agents +
                f"\nSTRATEGIES:\n{json.dumps(d['strategies'], indent=1, default=str)}\n"
                f"EXPANSIONS:\n{json.dumps(d['expansions'], indent=1, default=str)}\n"
                f"MARCHE:\n{json.dumps(d['marche'], indent=1, default=str)}\n"
                f"CONCURRENTS:\n{json.dumps(d['concurrents'], indent=1, default=str)}\n"
                f"LANGUES: {d['langues']} | CHAINS: {d['chains']}\n")
        return base


# ══════════════════════════════════════════
# WATCHDOG — Validation + Self-Healing + Health Check
# ══════════════════════════════════════════

HEALTH_ENDPOINTS = {
    "landing": "/",
    "health": "/health",
    "agent_card": "/.well-known/agent.json",
    "services": "/api/public/services",
    "discover": "/api/public/discover?capability=test",
    "prices": "/api/public/crypto/prices",
    "marketplace_stats": "/api/public/marketplace-stats",
    "ceo_status": "/api/ceo/status",
    "twitter_status": "/api/twitter/status",
    "swap_quote": "/api/public/crypto/quote?from_token=SOL&to_token=USDC&amount=1",
    "stocks": "/api/public/stocks",
    "gpu_tiers": "/api/public/gpu/tiers",
    "docs": "/api/public/docs",
    "defi": "/api/public/defi/best-yield?asset=USDC&limit=1",
    "sentiment": "/api/public/sentiment?token=BTC",
    "fear_greed": "/api/public/fear-greed",
    "mcp": "/mcp/",
    "docs_html": "/docs-html",
}


async def watchdog_health_check() -> dict:
    """Test ALL endpoints and return status report."""
    import httpx
    results = {}
    ok_count = 0
    fail_count = 0

    async with httpx.AsyncClient(timeout=10, verify=False) as client:
        for name, endpoint in HEALTH_ENDPOINTS.items():
            try:
                r = await client.get(f"http://127.0.0.1:8000{endpoint}")
                is_ok = r.status_code == 200
                results[name] = {
                    "status": "OK" if is_ok else f"HTTP {r.status_code}",
                    "ok": is_ok,
                }
                if is_ok:
                    ok_count += 1
                else:
                    fail_count += 1
            except Exception as e:
                results[name] = {"status": f"ERROR: {str(e)[:80]}", "ok": False}
                fail_count += 1

    report = {
        "total": len(HEALTH_ENDPOINTS),
        "ok": ok_count,
        "failed": fail_count,
        "endpoints": results,
    }

    # Alert on Discord if failures
    if fail_count > 0:
        failed_list = [f"❌ {n}: {r['status']}" for n, r in results.items() if not r["ok"]]
        try:
            from alerts import alert_system
            await alert_system(
                f"⚠️ WATCHDOG: {fail_count} endpoints DOWN",
                f"{ok_count}/{len(HEALTH_ENDPOINTS)} OK\n" + "\n".join(failed_list)
            )
        except Exception:
            pass
    else:
        print(f"[WATCHDOG] Health check: {ok_count}/{len(HEALTH_ENDPOINTS)} OK ✓")

    return report


async def watchdog_check_service(service: str) -> bool:
    endpoints = {
        "swap": "/api/public/crypto/tokens", "stocks": "/api/public/stocks",
        "gpu": "/api/public/gpu/tiers", "image": "/api/public/image/models",
        "prices": "/api/public/crypto/prices", "scraper": "/api/public/image/models",
        "monitor": "/api/public/wallet-monitor/alerts",
    }
    ep = endpoints.get(service.lower())
    if not ep:
        return True
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"https://{URL}{ep}")
            return r.status_code == 200
    except Exception:
        return False


async def watchdog_self_heal(source: str, error: str, memory: Memory):
    """WATCHDOG detecte erreur recurrente, Sonnet propose un patch."""
    memory.log_error(source, error)
    # Verifier si erreur recurrente (>3 fois)
    err = next((e for e in memory._data["erreurs_recurrentes"] if e["source"] == source), None)
    if not err or err.get("count", 0) < 3 or err.get("patch_proposed"):
        return

    print(f"[WATCHDOG] Erreur recurrente detectee: {source} ({err['count']}x)")
    prompt = (
        f"L'API MAXIA a une erreur recurrente.\n"
        f"Source: {source}\nErreur: {error}\nOccurrences: {err['count']}\n\n"
        f"Analyse l'erreur et propose un correctif Python en 1-5 lignes.\n"
        f"Si c'est un changement de format API, propose le nouveau parsing.\n"
        f"Si c'est un timeout, propose d'augmenter le timeout.\n"
        f"Si c'est un DNS, propose un fallback.\n\n"
        f"JSON: {{\"diagnostic\": \"...\", \"patch\": \"code Python\", \"fichier\": \"nom.py\", \"urgence\": \"haute|moyenne|basse\"}}"
    )
    result = _pj(await _call_anthropic(SONNET_MODEL, "Tu es un debugger Python expert.", prompt))
    if result and result.get("patch"):
        memory.log_patch(source, json.dumps(result))
        err["patch_proposed"] = True
        memory.save()
        await alert_rouge(
            f"Self-Healing: {source}",
            f"Erreur: {error} ({err['count']}x)\n"
            f"Diagnostic: {result.get('diagnostic','')}\n"
            f"Fichier: {result.get('fichier','')}\n"
            f"Patch: ```{result.get('patch','')}```\n"
            f"Urgence: {result.get('urgence','')}",
            deadline_h=24,
        )


# ══════════════════════════════════════════
# RADAR + MARKET PULSE — Intelligence On-Chain
# ══════════════════════════════════════════

RADAR_CATEGORIES = {
    "ai": ["RENDER", "PYTH"],
    "meme": ["BONK", "WIF", "TRUMP"],
    "defi": ["JUP", "RAY", "ORCA"],
    "l1": ["SOL", "ETH", "BTC"],
    "stable": ["USDC", "USDT"],
}

# Seuils de detection
RADAR_PRICE_SPIKE = 0.15    # +15% = spike
RADAR_PRICE_CRASH = -0.15   # -15% = crash
RADAR_VOLUME_SURGE = 0.40   # +40% volume = surge


async def radar_scan(memory: Memory) -> list:
    """Market Pulse : scanne prix + detecte tendances via Helius DAS."""
    alerts = []
    helius_key = _cfg("HELIUS_API_KEY")
    if not helius_key:
        return alerts

    try:
        import httpx
        rpc = f"https://mainnet.helius-rpc.com/?api-key={helius_key}"

        # Recuperer les prix actuels de tous les tokens
        from price_oracle import get_prices
        current_prices = {}
        try:
            all_prices = await get_prices()
            for sym, data in all_prices.items():
                if isinstance(data, dict):
                    current_prices[sym] = data.get("price", 0)
        except ImportError:
            # Mode standalone — utiliser getAsset directement
            pass

        if not current_prices:
            return alerts

        # Comparer avec les prix d'il y a 2h (6 cycles)
        prev_kpi = memory._data.get("kpi", [])
        prev_prices = {}
        if len(prev_kpi) >= 3:
            # Chercher les prix dans les KPI precedents
            for kpi in reversed(prev_kpi[-6:]):
                if kpi.get("prices"):
                    prev_prices = kpi["prices"]
                    break

        # Detecter les mouvements significatifs
        for cat_name, tokens in RADAR_CATEGORIES.items():
            cat_changes = []
            for token in tokens:
                curr = current_prices.get(token, 0)
                prev = prev_prices.get(token, 0)
                if curr > 0 and prev > 0:
                    change = (curr - prev) / prev
                    cat_changes.append(change)

                    # Spike individuel
                    if change >= RADAR_PRICE_SPIKE:
                        alert = {
                            "type": "price_spike",
                            "details": f"{token} +{change:.0%} ({prev:.4f} -> {curr:.4f})",
                            "token": token, "category": cat_name, "change": change,
                            "action": f"GHOST-WRITER: tweet about {token} pump. HUNTER: target {token} holders.",
                        }
                        alerts.append(alert)

                    # Crash individuel
                    elif change <= RADAR_PRICE_CRASH:
                        alert = {
                            "type": "price_crash",
                            "details": f"{token} {change:.0%} ({prev:.4f} -> {curr:.4f})",
                            "token": token, "category": cat_name, "change": change,
                            "action": f"GHOST-WRITER: 'buying the dip' content. SOL-TREASURY: reduce exposure.",
                        }
                        alerts.append(alert)

            # Surge de categorie (moyenne des tokens de la categorie)
            if cat_changes:
                avg_change = sum(cat_changes) / len(cat_changes)
                if avg_change >= RADAR_VOLUME_SURGE:
                    alert = {
                        "type": "category_surge",
                        "details": f"Category '{cat_name}' avg +{avg_change:.0%}",
                        "category": cat_name, "change": avg_change,
                        "action": f"GHOST-WRITER: thread about {cat_name} tokens trending. DEPLOYER: blog post.",
                    }
                    alerts.append(alert)

        # Sauvegarder les prix actuels dans le KPI pour comparaison future
        if memory._data.get("kpi"):
            memory._data["kpi"][-1]["prices"] = current_prices

    except Exception as e:
        print(f"[RADAR] Scan error: {e}")

    for alert in alerts:
        memory.log_radar_alert(alert.get("type", ""), alert.get("details", ""))
        print(f"[RADAR] {alert['type']}: {alert['details']}")

    return alerts



# ══════════════════════════════════════════
# ORACLE — Social Listening (Intelligence Externe)
# ══════════════════════════════════════════

ORACLE_SOURCES = {
    "dexscreener": "https://api.dexscreener.com/latest/dex/tokens/",
    "solana_fm": "https://api.solana.fm/v0/tokens/trending",
    "github_trending": "https://api.github.com/search/repositories?q=solana+AI&sort=stars&order=desc&per_page=5",
}

# Comptes influents Solana a surveiller (via profils publics)
ORACLE_INFLUENCERS = ["solana", "JupiterExchange", "HeliusLabs", "OndoFinance", "tensor_hq"]


async def oracle_scan_trends(memory: Memory) -> list:
    """Scanne les tendances externes : DexScreener, GitHub, influenceurs."""
    trends = []
    try:
        import httpx

        # 1. DexScreener — tokens Solana en tendance
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                resp = await c.get("https://api.dexscreener.com/token-boosts/latest/v1")
                if resp.status_code == 200:
                    data = resp.json()
                    boosts = data if isinstance(data, list) else data.get("boosts", data.get("tokens", []))
                    sol_boosts = [b for b in boosts[:20] if b.get("chainId") == "solana"] if isinstance(boosts, list) else []
                    if sol_boosts:
                        trends.append({
                            "source": "dexscreener",
                            "type": "trending_tokens",
                            "details": f"{len(sol_boosts)} Solana tokens trending on DexScreener",
                            "tokens": [b.get("tokenAddress", "")[:16] for b in sol_boosts[:5]],
                        })
        except Exception as e:
            pass

        # 2. GitHub trending — repos AI + Solana
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                resp = await c.get(
                    "https://api.github.com/search/repositories",
                    params={"q": "solana AI agent created:>2026-03-01", "sort": "stars", "per_page": 5},
                )
                if resp.status_code == 200:
                    repos = resp.json().get("items", [])
                    hot_repos = [r for r in repos if r.get("stargazers_count", 0) > 10]
                    if hot_repos:
                        trends.append({
                            "source": "github",
                            "type": "hot_repos",
                            "details": f"{len(hot_repos)} hot Solana AI repos on GitHub",
                            "repos": [{"name": r["full_name"], "stars": r["stargazers_count"]} for r in hot_repos[:3]],
                        })
        except Exception:
            pass

        # 3. Detecter les narratifs chauds
        # Combiner les signaux
        if trends:
            narratifs = set()
            for t in trends:
                details = t.get("details", "").lower()
                if "ai" in details or "gpu" in details:
                    narratifs.add("AI")
                if "meme" in details or "trump" in details or "bonk" in details:
                    narratifs.add("MEME")
                if "defi" in details or "swap" in details:
                    narratifs.add("DEFI")

            if narratifs:
                trends.append({
                    "source": "oracle_analysis",
                    "type": "hot_narrative",
                    "details": f"Hot narratives: {', '.join(narratifs)}",
                    "narratives": list(narratifs),
                    "action": f"GHOST-WRITER should create content about {', '.join(narratifs)}",
                })

    except Exception as e:
        print(f"[ORACLE] Scan error: {e}")

    for t in trends:
        memory.log_radar_alert(f"oracle_{t.get('type', '')}", t.get("details", ""))

    if trends:
        print(f"[ORACLE] {len(trends)} tendances detectees")

    return trends


# ══════════════════════════════════════════
# FAILOVER — Bascule automatique des APIs
# ══════════════════════════════════════════

FAILOVER_RPC = [
    {"name": "helius", "url_env": "HELIUS_API_KEY", "url_tpl": "https://mainnet.helius-rpc.com/?api-key={key}"},
    {"name": "quicknode", "url_env": "QUICKNODE_URL", "url_tpl": "{key}"},
    {"name": "alchemy", "url_env": "ALCHEMY_API_KEY", "url_tpl": "https://solana-mainnet.g.alchemy.com/v2/{key}"},
    {"name": "public", "url_env": "", "url_tpl": "https://api.mainnet-beta.solana.com"},
]

FAILOVER_LLM = [
    {"name": "groq", "fn": "_call_groq"},
    {"name": "anthropic_sonnet", "fn": "_call_anthropic_sonnet"},
    {"name": "local_rules", "fn": "_call_local_rules"},
]

FAILOVER_ALERTS = [
    {"name": "discord_webhook", "env": "DISCORD_WEBHOOK_URL"},
    {"name": "telegram", "env": "TELEGRAM_BOT_TOKEN"},
]

_active_rpc_index = 0
_rpc_failures: dict = {}  # name -> failure_count


async def failover_get_rpc() -> str:
    """Retourne le RPC actif, bascule si le principal est down."""
    global _active_rpc_index

    for i in range(len(FAILOVER_RPC)):
        idx = (_active_rpc_index + i) % len(FAILOVER_RPC)
        provider = FAILOVER_RPC[idx]
        name = provider["name"]

        # Construire l'URL
        if provider["url_env"]:
            key = os.getenv(provider["url_env"], "")
            if not key:
                continue
            url = provider["url_tpl"].format(key=key)
        else:
            url = provider["url_tpl"]

        # Tester le RPC
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5) as c:
                resp = await c.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "getHealth"})
                if resp.status_code == 200:
                    result = resp.json().get("result")
                    if result == "ok" or result is not None:
                        if idx != _active_rpc_index:
                            old_name = FAILOVER_RPC[_active_rpc_index]["name"]
                            print(f"[FAILOVER] RPC bascule: {old_name} -> {name}")
                            _active_rpc_index = idx
                        return url
        except Exception:
            _rpc_failures[name] = _rpc_failures.get(name, 0) + 1

    # Tout est down — fallback public
    print("[FAILOVER] Tous les RPC down — utilisation du RPC public")
    return "https://api.mainnet-beta.solana.com"


async def failover_send_alert(message: str):
    """Envoie une alerte via le canal disponible."""
    # Essayer Discord
    webhook = os.getenv("DISCORD_WEBHOOK_URL", "")
    if webhook:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10) as c:
                resp = await c.post(webhook, json={"content": message[:1900]})
                if resp.status_code in [200, 204]:
                    return
        except Exception:
            pass

    # Fallback Telegram
    tg_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    tg_chat = os.getenv("TELEGRAM_CHAT_ID", "")
    if tg_token and tg_chat:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10) as c:
                await c.post(
                    f"https://api.telegram.org/bot{tg_token}/sendMessage",
                    json={"chat_id": tg_chat, "text": message[:4000]},
                )
                return
        except Exception:
            pass

    # Dernier recours — log console
    print(f"[FAILOVER ALERT] {message[:200]}")


# ══════════════════════════════════════════
# MICRO WALLET — Petty Cash pour experimentations
# ══════════════════════════════════════════

MICRO_WALLET_ADDRESS = _cfg("MICRO_WALLET_ADDRESS")
MICRO_WALLET_PRIVKEY = _cfg("MICRO_WALLET_PRIVKEY")
MICRO_MAX_PER_TX = 0.01       # SOL max par transaction
MICRO_MAX_PER_DAY = 0.05      # SOL max par jour
MICRO_ALERT_LOW = 0.02        # SOL — alerte si solde bas


class MicroWallet:
    """Wallet de micro-depenses pour les experimentations du CEO."""

    def __init__(self):
        self._spent_today = 0.0
        self._spent_date = ""
        self._log: list = []

    def _reset_daily(self):
        today = date.today().isoformat()
        if self._spent_date != today:
            self._spent_date = today
            self._spent_today = 0.0

    def can_spend(self, amount: float) -> tuple:
        """Verifie si la depense est autorisee."""
        self._reset_daily()
        if amount > MICRO_MAX_PER_TX:
            return False, f"Max {MICRO_MAX_PER_TX} SOL par tx (demande: {amount})"
        if self._spent_today + amount > MICRO_MAX_PER_DAY:
            remaining = MICRO_MAX_PER_DAY - self._spent_today
            return False, f"Budget jour epuise (reste: {remaining:.4f} SOL)"
        if not MICRO_WALLET_ADDRESS or not MICRO_WALLET_PRIVKEY:
            return False, "Micro wallet non configure"
        return True, "OK"

    async def spend(self, amount: float, reason: str, memory=None) -> dict:
        """Execute une micro-depense."""
        ok, msg = self.can_spend(amount)
        if not ok:
            return {"success": False, "error": msg}

        # En production : executer la transaction Solana
        # from solana_tx import send_sol_transfer
        # result = await send_sol_transfer(...)

        self._spent_today += amount
        entry = {
            "ts": datetime.utcnow().isoformat(),
            "amount": amount,
            "reason": reason[:200],
        }
        self._log.append(entry)
        self._log = self._log[-100:]

        if memory:
            memory.log_decision("vert", f"MICRO: {amount} SOL — {reason}", "CEO experiment", "MICRO")
            memory._data["spent_sol"] = memory._data.get("spent_sol", 0) + amount
            memory.save()

        print(f"[MICRO] {amount} SOL — {reason}")
        return {"success": True, "amount": amount, "reason": reason}

    async def get_balance(self) -> float:
        """Recupere le solde du micro wallet."""
        if not MICRO_WALLET_ADDRESS:
            return 0
        try:
            from solana_tx import get_sol_balance
            return await get_sol_balance(MICRO_WALLET_ADDRESS)
        except ImportError:
            return 0

    def get_stats(self) -> dict:
        self._reset_daily()
        return {
            "address": MICRO_WALLET_ADDRESS[:16] + "..." if MICRO_WALLET_ADDRESS else "non configure",
            "spent_today": self._spent_today,
            "max_per_day": MICRO_MAX_PER_DAY,
            "remaining_today": MICRO_MAX_PER_DAY - self._spent_today,
            "recent_expenses": self._log[-5:],
        }


micro_wallet = MicroWallet()


# ══════════════════════════════════════════
# TESTIMONIAL — Social Proof
# ══════════════════════════════════════════

async def testimonial_request(user: str, tx_sig: str, service: str, memory: Memory):
    """Apres une transaction reussie, sollicite un feedback."""
    msg = (
        f"Hey! Your {service} went through (tx: {tx_sig[:16]}...). "
        f"Quick feedback? What did you use MAXIA for? "
        f"Reply anything — helps us improve. {URL}"
    )
    # En production : envoyer via le canal du user (memo, DM, etc.)
    memory.log_testimonial(user, tx_sig, "requested", False)
    return msg


async def testimonial_process(user: str, feedback: str, memory: Memory) -> dict:
    """Analyse le feedback et propose de publier si positif."""
    prompt = (
        f"Feedback de {user}: \"{feedback}\"\n\n"
        f"1. Est-ce positif, neutre ou negatif ?\n"
        f"2. Si positif, redige un tweet de temoignage (max 200 chars)\n"
        f"JSON: {{\"sentiment\": \"positif|neutre|negatif\", \"tweet\": \"...\" ou null}}"
    )
    result = _pj(await _call_groq("Tu analyses des feedbacks.", prompt))
    if result.get("sentiment") == "positif" and result.get("tweet"):
        memory.log_testimonial(user, "", feedback, False)
        return {"publish": True, "tweet": result["tweet"]}
    return {"publish": False}


# ══════════════════════════════════════════
# RESPONDER
# ══════════════════════════════════════════

RESPONDER_PROMPT = CEO_IDENTITY + """
Mode RESPONDER. Reponds au message entrant.
Intention: question_technique|prospect|plainte|spam|partenaire|investisseur|conversation
Ton adapte au canal. Ne vends jamais agressivement.
Plainte: excuse+verifie+resous. Investisseur/partenaire: alerte ROUGE.
JSON: {intention, reponse, action_interne, alerte_fondateur, priorite}"""


async def respond(canal: str, user: str, msg: str, memory: Memory) -> dict:
    if memory.is_stopped():
        return {"intention": "emergency_stop", "reponse": "Service temporarily paused. Back soon.", "alerte_fondateur": False}

    prev = [c for c in memory._data["conversations"] if c.get("user") == user][-3:]
    ctx = (
        f"CANAL: {canal}\nUSER: {user}\nMESSAGE: {msg}\n"
        f"HISTORIQUE:\n{json.dumps(prev, indent=1, default=str) if prev else '(Premier contact)'}\n"
        f"MAXIA: 15 tokens, 210 paires, GPU $0.69/h, audit $9.99, AI-to-AI marketplace\nURL: {URL}\n"
        f"TESTIMONIALS: {len(memory._data.get('testimonials', []))} recus"
    )
    raw = await _call_groq(RESPONDER_PROMPT, ctx)
    data = _pj(raw)
    if not data and raw:
        data = {"intention": "conversation", "reponse": raw, "alerte_fondateur": False}
    if not data:
        return {"intention": "spam", "reponse": "", "alerte_fondateur": False}

    memory.log_conversation(canal, user, msg, data.get("reponse", ""), data.get("intention", ""))

    if data.get("alerte_fondateur"):
        await alert_rouge(f"{data.get('intention','')} de {user} ({canal})", f"Msg: {msg[:200]}\nRep: {data.get('reponse','')[:200]}", deadline_h=2)

    return data


# ══════════════════════════════════════════
# GHOST-WRITER avec validation WATCHDOG
# ══════════════════════════════════════════

async def ghost_write(content_type: str, sujet: str, canal: str, memory: "Memory" = None) -> dict:
    # A/B testing : si un test est actif pour ce type de contenu, utiliser le variant
    ab_variant_key = None
    ab_test_name = f"ghost_{content_type}_{canal}"
    extra_instruction = ""
    if memory:
        test = memory._data.get("ab_tests", {}).get(ab_test_name)
        if test and test.get("status") == "active":
            ab_variant_key, variant_content = memory.get_ab_variant(ab_test_name)
            if variant_content:
                extra_instruction = f"\nSTYLE OBLIGATOIRE: {variant_content}\n"
                print(f"[GHOST-WRITER] A/B test actif: {ab_test_name} variant {ab_variant_key}")

    prompt = (
        f"Cree un {content_type} pour {canal}: {sujet}\n"
        f"CIBLE : dev 26-34 ans qui a un agent IA mais 0 revenus. Parle comme un dev.\n"
        f"MESSAGE CLE : ton agent peut GAGNER de l'USDC sur MAXIA. POST /sell = live.\n"
        f"TON : technique, code, faits. PAS de marketing creux. PAS de 'revolutionary'.\n"
        f"INCLURE : maxiaworld.app ou github.com/MAXIAWORLD/demo-agent\n"
        f"{extra_instruction}"
        f"Max 280 chars si tweet. Pas de emoji excessifs (max 1-2).\n"
        f"JSON: {{type, titre, contenu, services_mentionnes: [], hashtags, cta}}"
    )
    data = _pj(await _call_groq(CEO_IDENTITY + "\nMode GHOST-WRITER.", prompt))
    if not data:
        return {}
    # Tag le variant A/B pour tracking
    if ab_variant_key:
        data["ab_test"] = ab_test_name
        data["ab_variant"] = ab_variant_key
    # Validation WATCHDOG
    for svc in data.get("services_mentionnes", []):
        if not await watchdog_check_service(svc):
            print(f"[GHOST-WRITER] BLOQUE — {svc} DOWN")
            return {"blocked": True, "reason": f"{svc} is DOWN"}
    return data


# ══════════════════════════════════════════
# COLLECTE
# ══════════════════════════════════════════

async def collect() -> dict:
    return {
        "ts": datetime.utcnow().isoformat(),
        "rev_24h": 0, "rev_total": 0, "clients": 0, "clients_actifs": 0,
        "swaps": 0, "volume": 0, "gpu": 0, "ia_reqs": 0,
        "prix_live": 0, "prix_total": 25,
        "prospects": 0, "taux_rep": 0,
        "msgs_in": 0, "msgs_out": 0,
        "sol": 0, "usdc": 0, "erreurs": [],
    }


# ══════════════════════════════════════════
# EXECUTION avec verrous de securite
# ══════════════════════════════════════════

async def execute(decisions: list, memory: Memory):
    if memory.is_stopped():
        print("[CEO] ⛔ Emergency stop — decisions bloquees")
        await alert_rouge("Emergency Stop actif", "Toutes les decisions sont bloquees. Revenue: $0. Reset manuel requis.", deadline_h=1)
        return

    from ceo_executor import execute_decision

    VALID_CIBLES = {"GHOST-WRITER", "HUNTER", "SCOUT", "WATCHDOG", "SOL-TREASURY", "RESPONDER", "RADAR", "TESTIMONIAL", "DEPLOYER", "FONDATEUR", "NEGOTIATOR", "COMPLIANCE", "PARTNERSHIP", "ANALYTICS", "CRISIS-MANAGER"}
    VAGUE_PATTERNS = ["maximiser", "ameliorer", "optimiser", "augmenter les", "renforcer", "assurer le", "garantir"]
    CONCRETE_KW = ["tweet", "post", "switch", "contact", "deploy", "blog", "prix", "fee", "canal", "wallet", "scan", "check", "adjust", "send", "memo", "thread", "article"]

    for dec in decisions:
        action = dec.get("action", "")
        cible = dec.get("cible", "").upper()
        prio = dec.get("priorite", "moyenne")

        # Kill switch granulaire — skip les agents desactives
        if cible and memory.is_agent_disabled(cible):
            print(f"[CEO] Decision SKIPPED — {cible} est desactive")
            continue

        # Fix unknown cible — try to map it to closest valid one
        if cible and cible not in VALID_CIBLES:
            cible_map = {
                "CEO": "WATCHDOG", "MAXIA": "WATCHDOG", "MARKETING": "GHOST-WRITER",
                "CONTENT": "GHOST-WRITER", "PROSPECTION": "HUNTER", "BUDGET": "SOL-TREASURY",
                "TREASURY": "SOL-TREASURY", "PRIX": "SOL-TREASURY", "MONITORING": "WATCHDOG",
                "SOCIAL": "GHOST-WRITER", "TWITTER": "GHOST-WRITER", "DISCORD": "RESPONDER",
                "TELEGRAM": "RESPONDER", "INTELLIGENCE": "RADAR", "FEEDBACK": "TESTIMONIAL",
                "IA-PROSPECTION": "SCOUT", "AI-AGENTS": "SCOUT", "RECRUTEMENT-IA": "SCOUT",
                "AGENTS": "SCOUT", "OLAS": "SCOUT", "AUTONOLAS": "SCOUT",
                "PRIX": "NEGOTIATOR", "PRICING": "NEGOTIATOR", "NEGOCIATION": "NEGOTIATOR",
                "AML": "COMPLIANCE", "SANCTIONS": "COMPLIANCE", "KYC": "COMPLIANCE", "FRAUDE": "COMPLIANCE",
                "PARTENARIAT": "PARTNERSHIP", "PARTENAIRES": "PARTNERSHIP", "INTEGRATION": "PARTNERSHIP",
                "METRIQUES": "ANALYTICS", "REPORTING": "ANALYTICS", "RAPPORT": "ANALYTICS", "KPI": "ANALYTICS",
                "CRISE": "CRISIS-MANAGER", "INCIDENT": "CRISIS-MANAGER", "URGENCE": "CRISIS-MANAGER",
            }
            mapped = cible_map.get(cible)
            if mapped:
                print(f"[CEO] Cible '{cible}' remappee -> {mapped}")
                cible = mapped
                dec["cible"] = mapped
            else:
                print(f"[CEO] Decision REJETEE — cible inconnue: {cible}")
                continue

        # Translate vague actions into concrete ones via Groq re-prompt
        if any(v in action.lower() for v in VAGUE_PATTERNS) and not any(kw in action.lower() for kw in CONCRETE_KW):
            print(f"[CEO] Action vague detectee, re-prompt Groq: {action[:80]}")
            try:
                concrete = await _call_groq(
                    "Tu es un assistant qui transforme des objectifs vagues en actions concretes pour un sous-agent.",
                    f"Sous-agent cible: {cible}\n"
                    f"Objectif vague: {action}\n\n"
                    f"Transforme en UNE action concrete executable par {cible}.\n"
                    f"Exemples d'actions concretes:\n"
                    f"- GHOST-WRITER: 'tweet: MAXIA offre les frais les plus bas sur Solana'\n"
                    f"- HUNTER: 'contact wallet ABC123 via solana_memo'\n"
                    f"- SOL-TREASURY: 'adjust swap fee to 0.05%'\n"
                    f"- WATCHDOG: 'check service swap health'\n"
                    f"- DEPLOYER: 'deploy blog: Why MAXIA is cheapest'\n"
                    f"- RADAR: 'scan trending tokens volume > 100k'\n"
                    f"- RESPONDER: 'send welcome message to new users on discord'\n\n"
                    f"Reponds UNIQUEMENT l'action concrete, rien d'autre. Pas de JSON, pas d'explication.",
                    max_tokens=150,
                )
                if concrete and concrete.strip():
                    concrete = concrete.strip().strip('"').strip("'")
                    print(f"[CEO] Action concretisee: {concrete[:100]}")
                    action = concrete
                    dec["action"] = concrete
                else:
                    print(f"[CEO] Re-prompt echoue, action ignoree: {action[:80]}")
                    continue
            except Exception as e:
                print(f"[CEO] Re-prompt Groq error: {e}, action ignoree")
                continue

        # Verifier le budget avant execution
        if prio == "orange":
            budget = memory.get_budget_vert()
            if memory._data.get("revenue_usd", 0) == 0 and budget < MIN_BUDGET_VERT * 2:
                print(f"[CEO] Decision orange BLOQUEE (budget trop bas: {budget:.4f})")
                continue

        print(f"[CEO] -> {cible} [{prio}] : {action[:100]}")
        memory.log_decision(prio, action, "CEO directive", cible)

        if cible == "FONDATEUR" and prio == "haute":
            await alert_rouge(action[:80], action, deadline_h=2)

        # Actually execute the decision
        try:
            result = await execute_decision(dec, memory)
            if result.get("executed"):
                print(f"[CEO] EXECUTED: {cible} -> {result.get('detail', 'ok')}")
            else:
                reason = result.get("reason", "unknown")
                print(f"[CEO] NOT EXECUTED: {cible} -> {reason}")
        except Exception as e:
            print(f"[CEO] Execution error for {cible}: {e}")


# ══════════════════════════════════════════
# WEB-DESIGNER — Config JSON pour le frontend
# ══════════════════════════════════════════

async def web_designer_update_config(memory: Memory) -> dict:
    """Genere un fichier JSON de config que le frontend lit.
    Le CEO peut changer textes, prix, annonces sans toucher au code."""
    d = memory._data
    testimonials = [t for t in d.get("testimonials", []) if t.get("published")]

    config = {
        "updated_at": datetime.utcnow().isoformat(),
        "announcement": "",  # Sera rempli par le CEO
        "hero": {
            "title": "MAXIA",
            "subtitle": "AI Marketplace on Solana",
            "badges": [
                f"{len(d.get('langues', ['en']))} Languages",
                f"{len(d.get('chains', ['solana']))} Chains",
                "15 Tokens", "210 Pairs", "10 Stocks", "5 GPU",
            ],
        },
        "stats": {
            "clients": d.get("clients", 0),
            "revenue": d.get("revenue_usd", 0),
            "transactions": d.get("responses", 0),
            "testimonials": len(testimonials),
            "prix_live": 25,
        },
        "social_proof": {
            "count": len(testimonials),
            "label": f"{len(testimonials)} verified transactions" if testimonials else "Open API — Try it free",
            "testimonials": [{"user": t["user"], "feedback": t["feedback"][:100]} for t in testimonials[-5:]],
        },
        "pricing_highlight": {
            "swap_fee": "0.02%",
            "gpu_price": "$0.69/h",
            "audit_price": "$4.99",
            "label": "Lowest fees in DeFi",
        },
        "cta": {
            "primary": {"text": "Try the API (Free)", "url": f"https://{URL}/api/public/register"},
            "secondary": {"text": "White Paper", "url": f"https://{URL}/MAXIA_WhitePaper_v1.pdf"},
        },
    }

    # Le CEO peut ajouter une annonce via la boucle strategique
    radar = d.get("radar_alerts", [])
    if radar:
        last = radar[-1]
        if last.get("type") == "price_spike":
            config["announcement"] = f"Trending: {last.get('details', '')}"
        elif last.get("type") == "category_surge":
            config["announcement"] = f"Hot: {last.get('details', '')}"

    return config


async def web_designer_deploy_config(config: dict, memory: Memory) -> dict:
    """Deploie le fichier config.json sur GitHub Pages."""
    content = json.dumps(config, indent=2, default=str)
    return await deployer_push_github(
        "config.json", content,
        f"CEO auto-update config ({datetime.utcnow().strftime('%Y-%m-%d %H:%M')})",
    )


# ══════════════════════════════════════════
# DEPLOYER — Genere et deploie des pages web
# ══════════════════════════════════════════

GITHUB_TOKEN = _cfg("GITHUB_TOKEN")
GITHUB_ORG = _cfg("GITHUB_ORG", "MAXIA-AI")
GITHUB_REPO = _cfg("GITHUB_REPO", "site")
GITHUB_BRANCH = "main"

# Pages que le CEO peut creer automatiquement
DEPLOYABLE_PAGES = {
    "docs": {
        "trigger": "premier_client",
        "description": "Documentation API interactive avec exemples live",
    },
    "status": {
        "trigger": "toujours",
        "description": "Uptime, prix live 25 tokens, volume, agents actifs",
    },
    "testimonials": {
        "trigger": "3_feedbacks_positifs",
        "description": "Page de temoignages clients verifies on-chain",
    },
    "compare": {
        "trigger": "analyse_concurrence",
        "description": "Tableau comparatif fees MAXIA vs Jupiter vs Binance",
    },
    "report": {
        "trigger": "chaque_lundi",
        "description": "Rapport hebdomadaire public (volume, clients, prix)",
    },
}


async def deployer_generate_page(page_type: str, data: dict) -> str:
    """GHOST-WRITER genere une page HTML complete via Sonnet."""
    prompts = {
        "docs": (
            f"Genere une page HTML complete et moderne (dark theme, responsive) pour la documentation API de MAXIA.\n"
            f"URL de base: https://{URL}\n\n"
            f"Inclus 7 exemples de code interactifs :\n"
            f"1. POST /api/public/crypto/swap — Swap SOL to USDC\n"
            f"2. POST /api/public/gpu/rent — Rent RTX 4090\n"
            f"3. POST /api/public/scrape — Scrape a URL\n"
            f"4. POST /api/public/image/generate — Generate an image\n"
            f"5. POST /api/public/wallet-monitor/add — Monitor a wallet\n"
            f"6. POST /api/public/stocks/buy — Buy tokenized stocks\n"
            f"7. GET /api/public/crypto/prices — Get live prices\n\n"
            f"Pour chaque exemple :\n"
            f"- Montre le curl et le Python\n"
            f"- Ajoute un bouton 'Try it' qui fait un fetch() vers l'API et affiche le resultat\n"
            f"- Affiche les prix en temps reel via fetch('/api/public/crypto/prices')\n\n"
            f"Header: MAXIA API Documentation\n"
            f"Footer: 15 tokens, 210 pairs, 10 stocks, 5 GPU — Live on Solana\n"
            f"Style: dark (#0A0E17), blue accents (#3B82F6), JetBrains Mono pour le code\n"
            f"Retourne UNIQUEMENT le HTML complet, rien d'autre."
        ),
        "status": (
            f"Genere une page HTML status dashboard pour MAXIA.\n"
            f"URL: https://{URL}\n\n"
            f"La page fait un fetch() toutes les 30s vers :\n"
            f"- /health (articles count)\n"
            f"- /api/public/crypto/prices (25 prix live)\n"
            f"- /api/public/stocks (10 actions)\n"
            f"- /api/public/gpu/tiers (GPU disponibles)\n\n"
            f"Affiche :\n"
            f"- Status: ONLINE/OFFLINE (gros indicateur vert/rouge)\n"
            f"- 25 prix live dans un tableau avec refresh auto\n"
            f"- Derniere mise a jour (timestamp)\n"
            f"- Nombre d'articles actifs\n\n"
            f"Style: dark, minimaliste, temps reel\n"
            f"Retourne UNIQUEMENT le HTML complet."
        ),
        "testimonials": (
            f"Genere une page HTML de temoignages pour MAXIA.\n"
            f"Testimonials data: {json.dumps(data.get('testimonials', []), default=str)}\n\n"
            f"Pour chaque temoignage :\n"
            f"- Avatar genere (initiales)\n"
            f"- Citation du feedback\n"
            f"- Service utilise (swap, GPU, audit...)\n"
            f"- Lien Solscan de la transaction (preuve on-chain)\n"
            f"- Date\n\n"
            f"Header: What AI Agents Say About MAXIA\n"
            f"Counter: 'X verified transactions'\n"
            f"CTA: Try MAXIA free\n"
            f"Style: dark, confiance, badges 'Verified on Solana'\n"
            f"Retourne UNIQUEMENT le HTML complet."
        ),
        "compare": (
            f"Genere une page HTML de comparaison de fees pour MAXIA.\n\n"
            f"Tableau comparatif :\n"
            f"| Service | MAXIA | Jupiter | Binance | Coinbase |\n"
            f"| Swap fee | 0.02-0.15% | 0% + slippage | 0.10% | 0.60% |\n"
            f"| Stocks | 0.05% | N/A | N/A | N/A |\n"
            f"| GPU RTX4090 | $0.69/h | N/A | N/A | N/A |\n"
            f"| API | Gratuite | Gratuite | Payante | Payante |\n"
            f"| Prix live | 25 tokens | Oui | Oui | Oui |\n"
            f"| AI Services | 9 services | Non | Non | Non |\n\n"
            f"Mets en evidence les avantages MAXIA (vert)\n"
            f"Ajoute un calculateur : 'Combien economisez-vous avec MAXIA ?'\n"
            f"Input: volume mensuel, output: economies en $\n"
            f"Style: dark, tableaux clairs, vert pour MAXIA\n"
            f"Retourne UNIQUEMENT le HTML complet."
        ),
        "report": (
            f"Genere une page HTML de rapport hebdomadaire MAXIA.\n"
            f"Data: {json.dumps(data, default=str)}\n\n"
            f"Sections :\n"
            f"- Resume executif (2 phrases)\n"
            f"- KPI (revenus, clients, volume, swaps)\n"
            f"- Prix des 25 tokens (tableau)\n"
            f"- Top 5 swaps de la semaine\n"
            f"- Decisions du CEO cette semaine\n"
            f"- Perspectives semaine prochaine\n\n"
            f"Style: dark, professionnel, data-driven\n"
            f"Retourne UNIQUEMENT le HTML complet."
        ),
    }

    prompt = prompts.get(page_type, "")
    if not prompt:
        return ""

    # Utiliser Sonnet pour generer du HTML de qualite
    html = await _call_anthropic(
        SONNET_MODEL,
        "Tu es un expert frontend. Genere du HTML/CSS/JS complet, moderne et responsive. Retourne UNIQUEMENT le code HTML, pas de markdown, pas d'explication.",
        prompt,
        max_tokens=4000,
    )

    # Nettoyer si markdown
    if html.startswith("```html"):
        html = html[7:]
    if html.startswith("```"):
        html = html[3:]
    if html.endswith("```"):
        html = html[:-3]

    return html.strip()


async def deployer_push_github(filename: str, content: str, commit_msg: str) -> dict:
    """Deploie un fichier sur GitHub Pages via l'API GitHub."""
    if not GITHUB_TOKEN:
        print(f"[DEPLOYER] GITHUB_TOKEN manquant — fichier sauve localement")
        # Sauvegarder localement en fallback
        local_path = f"/tmp/maxia_pages/{filename}"
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        with open(local_path, "w") as f:
            f.write(content)
        return {"success": False, "error": "No GITHUB_TOKEN", "local": local_path}

    try:
        import httpx, base64

        api_url = f"https://api.github.com/repos/{GITHUB_ORG}/{GITHUB_REPO}/contents/{filename}"
        encoded = base64.b64encode(content.encode()).decode()

        headers = {
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
        }

        async with httpx.AsyncClient(timeout=30) as client:
            # Verifier si le fichier existe deja (pour update)
            sha = None
            try:
                resp = await client.get(api_url, headers=headers)
                if resp.status_code == 200:
                    sha = resp.json().get("sha")
            except Exception:
                pass

            # Creer ou mettre a jour
            payload = {
                "message": commit_msg,
                "content": encoded,
                "branch": GITHUB_BRANCH,
            }
            if sha:
                payload["sha"] = sha

            resp = await client.put(api_url, headers=headers, json=payload)

            if resp.status_code in [200, 201]:
                page_url = f"https://{GITHUB_ORG.lower()}.github.io/{GITHUB_REPO}/{filename}"
                print(f"[DEPLOYER] Deploye: {page_url}")
                return {"success": True, "url": page_url, "filename": filename}
            else:
                error = resp.json().get("message", resp.text[:200])
                print(f"[DEPLOYER] GitHub error: {error}")
                return {"success": False, "error": error}

    except Exception as e:
        print(f"[DEPLOYER] Error: {e}")
        return {"success": False, "error": str(e)}


async def deployer_create_and_deploy(page_type: str, data: dict, memory) -> dict:
    """Pipeline complet : genere → valide → deploie."""
    print(f"[DEPLOYER] Creation page '{page_type}'...")

    # 1. GHOST-WRITER genere
    html = await deployer_generate_page(page_type, data)
    if not html or len(html) < 100:
        return {"success": False, "error": "Generation echouee"}

    # 2. WATCHDOG valide les services mentionnes
    services_to_check = ["prices", "swap", "stocks", "gpu"]
    for svc in services_to_check:
        up = await watchdog_check_service(svc)
        if not up:
            print(f"[DEPLOYER] BLOQUE — {svc} DOWN, page non deployee")
            return {"success": False, "error": f"Service {svc} DOWN"}

    # 3. Deployer
    filename = f"{page_type}.html"
    commit_msg = f"CEO MAXIA auto-deploy: {page_type} page ({datetime.utcnow().strftime('%Y-%m-%d %H:%M')})"
    result = await deployer_push_github(filename, html, commit_msg)

    # 4. Logger
    if result.get("success"):
        if memory:
            memory.log_decision("vert", f"DEPLOYER: {page_type} deploye -> {result.get('url','')}", "Auto-deploy", "DEPLOYER")
        print(f"[DEPLOYER] OK: {result.get('url','')}")
    else:
        print(f"[DEPLOYER] Echec: {result.get('error','')}")

    return result


async def deployer_blog_post(titre: str, contenu_prompt: str, memory) -> dict:
    """Cree et deploie un article de blog."""
    prompt = (
        f"Genere une page HTML complete pour un article de blog MAXIA.\n"
        f"Titre: {titre}\n"
        f"Contenu a developper: {contenu_prompt}\n\n"
        f"Structure: Header MAXIA, titre, date, contenu technique avec code snippets,\n"
        f"CTA 'Try MAXIA API', footer avec liens.\n"
        f"Style: dark, lisible, technique.\n"
        f"Retourne UNIQUEMENT le HTML."
    )
    html = await _call_anthropic(SONNET_MODEL, "Expert frontend. HTML only.", prompt, 4000)
    if html.startswith("```"):
        html = html.split("\n", 1)[-1]
    if html.endswith("```"):
        html = html[:-3]

    slug = titre.lower().replace(" ", "-").replace("'", "")[:50]
    filename = f"blog/{slug}.html"
    commit_msg = f"CEO MAXIA blog: {titre[:40]}"
    result = await deployer_push_github(filename, html.strip(), commit_msg)

    if result.get("success") and memory:
        memory.log_decision("vert", f"Blog deploye: {titre} -> {result.get('url','')}", "RADAR trend", "DEPLOYER")

    return result


# ══════════════════════════════════════════
# NEGOTIATOR — Negociation automatique des prix
# ══════════════════════════════════════════

async def negotiator_evaluate(buyer_agent: str, service: str, proposed_price: float, memory: Memory) -> dict:
    """Evalue et negocie automatiquement une offre de prix d'un agent IA acheteur."""
    # Recuperer le prix catalogue
    catalog_price = 0
    try:
        from database import db as _db
        svc = await _db.get_service_by_name(service)
        if svc:
            catalog_price = svc.get("price", 0)
    except Exception:
        pass

    if catalog_price <= 0:
        return {"accepted": False, "reason": "service_not_found", "counter_offer": None}

    # Recuperer l'historique du buyer
    buyer_history = [c for c in memory._data.get("conversations", []) if c.get("user") == buyer_agent]
    buyer_txs = len(buyer_history)

    # Regles de negociation
    min_price = catalog_price * 0.70  # jamais en dessous de 70%
    loyalty_discount = min(0.15, buyer_txs * 0.02)  # 2% par transaction passee, max 15%
    fair_price = catalog_price * (1 - loyalty_discount)

    if proposed_price >= fair_price:
        memory.log_decision("vert", f"NEGOTIATOR: accepte {proposed_price} de {buyer_agent} pour {service}", "negociation", "NEGOTIATOR")
        return {"accepted": True, "final_price": proposed_price, "discount": f"{loyalty_discount:.0%}", "reason": "price_ok"}

    if proposed_price >= min_price:
        # Contre-offre : prix moyen entre demande et catalogue
        counter = round((proposed_price + fair_price) / 2, 4)
        memory.log_decision("vert", f"NEGOTIATOR: contre-offre {counter} a {buyer_agent} (demande: {proposed_price})", "negociation", "NEGOTIATOR")
        return {"accepted": False, "counter_offer": counter, "min_acceptable": min_price, "reason": "counter_offer", "loyalty_discount": f"{loyalty_discount:.0%}"}

    # Prix trop bas — refus
    return {"accepted": False, "counter_offer": fair_price, "reason": "too_low", "message": f"Minimum acceptable: ${min_price:.2f}. Your loyalty discount: {loyalty_discount:.0%}"}


async def negotiator_bulk_deal(buyer_agent: str, services: list, memory: Memory) -> dict:
    """Negociation de pack/bundle — remise volume automatique."""
    total_catalog = 0
    details = []
    for svc_name in services:
        try:
            from database import db as _db
            svc = await _db.get_service_by_name(svc_name)
            price = svc.get("price", 0) if svc else 0
            total_catalog += price
            details.append({"service": svc_name, "unit_price": price})
        except Exception:
            details.append({"service": svc_name, "unit_price": 0, "error": "not_found"})

    # Remise volume : 5% pour 2 services, 10% pour 3+, 15% pour 5+, 20% pour 10+
    n = len(services)
    if n >= 10:
        discount = 0.20
    elif n >= 5:
        discount = 0.15
    elif n >= 3:
        discount = 0.10
    elif n >= 2:
        discount = 0.05
    else:
        discount = 0

    bundle_price = round(total_catalog * (1 - discount), 4)
    memory.log_decision("vert", f"NEGOTIATOR: bundle {n} services pour {buyer_agent}, remise {discount:.0%}", "negociation", "NEGOTIATOR")
    return {
        "services": details,
        "total_catalog": total_catalog,
        "discount": f"{discount:.0%}",
        "bundle_price": bundle_price,
        "savings": round(total_catalog - bundle_price, 4),
    }


# ══════════════════════════════════════════
# COMPLIANCE — Verification reglementaire
# ══════════════════════════════════════════

# Wallets sanctionnes connus (OFAC SDN list — echantillon)
SANCTIONED_PREFIXES = [
    "4wJT", "HN7c", "FhVo",  # Tornado Cash tagged
]

async def compliance_check_wallet(wallet: str, memory: Memory) -> dict:
    """Verifie si un wallet est sur liste noire/sanctions."""
    issues = []

    # Check prefixes sanctions
    for prefix in SANCTIONED_PREFIXES:
        if wallet.startswith(prefix):
            issues.append(f"wallet_prefix_match_{prefix}")

    # Check si wallet deja bloque en memoire
    blocked = memory._data.get("compliance_blocked", [])
    if wallet in blocked:
        issues.append("previously_blocked")

    # Verifier age du wallet via RPC (nouveau wallet = risque)
    try:
        import httpx
        helius_key = _cfg("HELIUS_API_KEY")
        if helius_key:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"https://mainnet.helius-rpc.com/?api-key={helius_key}",
                    json={"jsonrpc": "2.0", "id": 1, "method": "getSignaturesForAddress", "params": [wallet, {"limit": 1}]},
                )
                sigs = resp.json().get("result", [])
                if not sigs:
                    issues.append("no_transaction_history")
    except Exception:
        pass

    risk = "high" if issues else "low"
    cleared = len(issues) == 0

    if not cleared:
        memory.log_decision("orange", f"COMPLIANCE: wallet {wallet[:12]}... flagge — {issues}", "AML check", "COMPLIANCE")
        if "compliance_blocked" not in memory._data:
            memory._data["compliance_blocked"] = []
        if wallet not in memory._data["compliance_blocked"]:
            memory._data["compliance_blocked"].append(wallet)
            memory.save()
        # Notifier NEGOTIATOR et CRISIS-MANAGER via le bus
        agent_bus.send("COMPLIANCE", "NEGOTIATOR", "wallet_blocked", {"wallet": wallet, "issues": issues})
        agent_bus.send("COMPLIANCE", "CRISIS-MANAGER", "compliance_flag", {"wallet": wallet, "risk": risk})

    return {"wallet": wallet, "cleared": cleared, "risk": risk, "issues": issues}


async def compliance_check_transaction(amount: float, sender: str, receiver: str, memory: Memory) -> dict:
    """Verifie une transaction pour conformite AML basique."""
    flags = []

    # Seuil de transaction elevee
    if amount > 10000:
        flags.append("high_value_transaction")

    # Frequence anormale du sender (>20 tx en 24h)
    recent_decisions = [d for d in memory._data.get("decisions", [])
                        if d.get("cible") == sender and d.get("ts", "")[:10] == datetime.utcnow().isoformat()[:10]]
    if len(recent_decisions) > 20:
        flags.append("unusual_frequency")

    # Verifier les deux wallets
    sender_check = await compliance_check_wallet(sender, memory)
    receiver_check = await compliance_check_wallet(receiver, memory)
    if not sender_check["cleared"]:
        flags.append(f"sender_flagged: {sender_check['issues']}")
    if not receiver_check["cleared"]:
        flags.append(f"receiver_flagged: {receiver_check['issues']}")

    approved = len(flags) == 0
    if not approved:
        await alert_info(f"COMPLIANCE: tx ${amount} bloquee — {flags}")

    return {"approved": approved, "amount": amount, "flags": flags}


# ══════════════════════════════════════════
# PARTNERSHIP — Detection et demarchage partenariats
# ══════════════════════════════════════════

PARTNERSHIP_TARGETS = {
    "dex": ["Jupiter", "Raydium", "Orca", "Meteora"],
    "infra": ["Helius", "Quicknode", "Triton", "GenesysGo"],
    "ai_protocols": ["Olas", "Fetch.ai", "SingularityNET", "Bittensor"],
    "l2": ["Base", "Arbitrum", "Optimism"],
    "gpu": ["RunPod", "Lambda", "Akash", "Render"],
    "wallets": ["Phantom", "Backpack", "Solflare"],
}

PARTNERSHIP_TEMPLATES = {
    "dex": "Integration listing — MAXIA routes {volume} trades/day through your DEX. API partnership for reduced fees?",
    "infra": "Infrastructure discount — MAXIA serves {clients} AI agents. Volume pricing for RPC/compute?",
    "ai_protocols": "AI-to-AI marketplace — MAXIA connects your agents to paying clients. Mutual listing partnership?",
    "l2": "Cross-chain expansion — MAXIA is live on Solana+Base. Integration for {chain} support?",
    "gpu": "GPU marketplace — MAXIA auctions GPU compute to AI agents. Reseller/affiliate deal?",
    "wallets": "Wallet integration — embed MAXIA services (swap, GPU, AI) directly in your wallet UI.",
}


async def partnership_scan(memory: Memory) -> list:
    """Scanne les partenaires potentiels et evalue la priorite."""
    opportunities = []
    existing = memory._data.get("partnerships", [])
    existing_names = [p.get("name", "").lower() for p in existing]

    stats = {
        "volume": memory._data.get("kpi", [{}])[-1].get("volume", 0) if memory._data.get("kpi") else 0,
        "clients": memory._data.get("clients", 0),
    }

    for category, partners in PARTNERSHIP_TARGETS.items():
        template = PARTNERSHIP_TEMPLATES.get(category, "")
        for partner in partners:
            if partner.lower() in existing_names:
                continue  # deja contacte
            score = 0
            # Score basé sur la pertinence actuelle
            if category == "dex" and stats["volume"] > 0:
                score = 80
            elif category == "ai_protocols":
                score = 90  # toujours haute priorite
            elif category == "gpu":
                score = 70
            elif category == "infra" and stats["clients"] > 5:
                score = 75
            elif category == "wallets" and stats["clients"] > 20:
                score = 85
            elif category == "l2":
                score = 60
            else:
                score = 50

            msg = template.format(volume=stats["volume"], clients=stats["clients"], chain=partner)
            opportunities.append({
                "partner": partner,
                "category": category,
                "score": score,
                "pitch": msg,
            })

    # Trier par score descendant
    opportunities.sort(key=lambda x: x["score"], reverse=True)
    return opportunities[:10]


async def partnership_outreach(partner: str, category: str, pitch: str, memory: Memory) -> dict:
    """Genere un message de demarchage personnalise via LLM."""
    prompt = (
        f"Ecris un message de partenariat B2B concis (150 mots max) a {partner} ({category}).\n"
        f"Contexte: {pitch}\n"
        f"MAXIA: AI marketplace sur Solana — {memory._data.get('clients', 0)} agents actifs, "
        f"${memory._data.get('revenue_usd', 0)} rev mensuel.\n"
        f"Ton: professionnel mais direct. Pas de flatterie excessive.\n"
        f"Inclure: proposition de valeur mutuelle, CTA concret (call, pilot, API test).\n"
        f"JSON: {{subject, message, cta, channel_suggested}}"
    )
    result = _pj(await _call_groq(CEO_IDENTITY + "\nMode PARTNERSHIP.", prompt))
    if result:
        if "partnerships" not in memory._data:
            memory._data["partnerships"] = []
        memory._data["partnerships"].append({
            "name": partner, "category": category,
            "contacted": datetime.utcnow().isoformat(),
            "status": "outreach_sent",
        })
        memory.save()
        memory.log_decision("orange", f"PARTNERSHIP: outreach a {partner} ({category})", "expansion", "PARTNERSHIP")
        memory.log_action_with_tracking("PARTNERSHIP", "outreach", f"partner_{partner.lower()}", f"{partner} ({category})")
    return result or {}


# ══════════════════════════════════════════
# ANALYTICS — Metriques avancees (retention, churn, LTV, funnel)
# ══════════════════════════════════════════

async def analytics_compute(memory: Memory) -> dict:
    """Calcule les metriques business avancees."""
    d = memory._data
    kpis = d.get("kpi", [])
    decisions = d.get("decisions", [])
    conversations = d.get("conversations", [])
    testimonials = d.get("testimonials", [])

    # Revenue metrics
    rev_total = d.get("revenue_usd", 0)
    clients_total = d.get("clients", 0)
    ltv = rev_total / max(1, clients_total)  # Lifetime value

    # Funnel metrics (depuis les conversations)
    prospects = len(set(c.get("user", "") for c in conversations if c.get("intention") == "prospect"))
    signups = clients_total
    active = d.get("kpi", [{}])[-1].get("clients_actifs", 0) if kpis else 0
    paying = len([t for t in testimonials if t.get("published")])

    funnel = {
        "prospects": prospects,
        "signups": signups,
        "active": active,
        "paying": paying,
        "conversion_prospect_to_signup": f"{signups / max(1, prospects):.1%}",
        "conversion_signup_to_active": f"{active / max(1, signups):.1%}",
        "conversion_active_to_paying": f"{paying / max(1, active):.1%}",
    }

    # Churn : clients qui etaient actifs il y a 7j mais plus maintenant
    kpi_7d_ago = kpis[-168] if len(kpis) >= 168 else kpis[0] if kpis else {}
    prev_active = kpi_7d_ago.get("clients_actifs", 0)
    churn = max(0, prev_active - active)
    churn_rate = churn / max(1, prev_active)

    # Revenue par canal
    rev_by_canal = {}
    for dec in decisions:
        if "revenue" in dec.get("decision", "").lower() or "paiement" in dec.get("decision", "").lower():
            canal = dec.get("cible", "unknown")
            rev_by_canal[canal] = rev_by_canal.get(canal, 0) + 1

    # Activite par heure (heatmap)
    activity_hours = {}
    for c in conversations[-500:]:
        ts = c.get("ts", "")
        if len(ts) >= 13:
            hour = ts[11:13]
            activity_hours[hour] = activity_hours.get(hour, 0) + 1

    # Score de sante global (0-100)
    health = 50
    if rev_total > 0:
        health += 15
    if clients_total >= 5:
        health += 10
    if churn_rate < 0.1:
        health += 10
    if active > 0:
        health += 10
    if len(d.get("erreurs_recurrentes", [])) == 0:
        health += 5
    health = min(100, health)

    analytics = {
        "ltv": round(ltv, 2),
        "churn": {"lost": churn, "rate": f"{churn_rate:.1%}"},
        "funnel": funnel,
        "revenue_by_canal": rev_by_canal,
        "activity_heatmap": dict(sorted(activity_hours.items())),
        "health_score": health,
        "recommendations": [],
    }

    # Recommandations automatiques + notification inter-agents via bus
    if churn_rate > 0.2:
        analytics["recommendations"].append("CHURN ELEVE: activer TESTIMONIAL pour re-engager les inactifs")
        agent_bus.send("ANALYTICS", "TESTIMONIAL", "churn_high", {"rate": f"{churn_rate:.1%}"})
    if funnel["prospects"] > 0 and signups / max(1, prospects) < 0.05:
        analytics["recommendations"].append("CONVERSION BASSE: HUNTER doit changer d'approche ou de canal")
        agent_bus.send("ANALYTICS", "HUNTER", "low_conversion", {"rate": f"{signups / max(1, prospects):.1%}"})
    if ltv < 1:
        analytics["recommendations"].append("LTV FAIBLE: NEGOTIATOR doit proposer des bundles pour augmenter panier moyen")
        agent_bus.send("ANALYTICS", "NEGOTIATOR", "low_ltv", {"ltv": ltv})
    if health < 40:
        analytics["recommendations"].append("SANTE CRITIQUE: focus sur stabilite avant croissance")
        agent_bus.broadcast("ANALYTICS", "health_critical", {"score": health})

    return analytics


async def analytics_weekly_report(memory: Memory) -> dict:
    """Genere un rapport hebdomadaire enrichi pour le fondateur."""
    metrics = await analytics_compute(memory)
    d = memory._data

    prompt = (
        f"Genere un rapport hebdomadaire CEO pour le fondateur (Alexis).\n\n"
        f"METRIQUES:\n{json.dumps(metrics, indent=2, default=str)}\n\n"
        f"KPI recents: rev=${d.get('revenue_usd', 0)}, clients={d.get('clients', 0)}\n"
        f"Agents actifs: {list(d.get('agents', {}).keys())}\n"
        f"Erreurs: {len(d.get('erreurs_recurrentes', []))}\n"
        f"Testimonials: {len(d.get('testimonials', []))}\n\n"
        f"Format: JSON {{resume_executif, kpi_cles, wins, problemes, actions_semaine_prochaine, message_fondateur}}\n"
        f"Ton: direct, factuel, avec les chiffres. Max 300 mots."
    )
    report = _pj(await _call_anthropic(SONNET_MODEL, CEO_IDENTITY + "\nMode ANALYTICS.", prompt, 2000))
    if report:
        report["metrics"] = metrics
        memory.log_decision("vert", f"ANALYTICS: rapport hebdo genere (health={metrics['health_score']})", "reporting", "ANALYTICS")
    return report or {"metrics": metrics}


# ══════════════════════════════════════════
# CRISIS-MANAGER — Gestion automatique des crises
# ══════════════════════════════════════════

CRISIS_LEVELS = {
    "P0": {"name": "critique", "response_min": 5, "escalate": True, "pause_marketing": True},
    "P1": {"name": "majeure", "response_min": 30, "escalate": True, "pause_marketing": True},
    "P2": {"name": "moderee", "response_min": 120, "escalate": False, "pause_marketing": False},
    "P3": {"name": "mineure", "response_min": 480, "escalate": False, "pause_marketing": False},
}


async def crisis_detect(memory: Memory) -> list:
    """Detecte les situations de crise automatiquement."""
    crises = []
    d = memory._data

    # P0 : Service principal DOWN
    try:
        health = await watchdog_health_check()
        if health.get("failed", 0) > health.get("total", 1) * 0.5:
            crises.append({
                "level": "P0", "type": "service_outage",
                "details": f"{health['failed']}/{health['total']} services DOWN",
                "action": "WATCHDOG self-heal + GHOST-WRITER pause + alerte fondateur",
            })
    except Exception:
        pass

    # P0 : Perte de fonds detectee (solde du wallet micro qui baisse sans transactions loguees)
    try:
        balance = await micro_wallet.get_balance()
        expected = MICRO_MAX_PER_DAY - micro_wallet._spent_today
        if balance > 0 and balance < expected * 0.5 and micro_wallet._spent_today > 0:
            crises.append({
                "level": "P0", "type": "funds_anomaly",
                "details": f"Wallet balance {balance:.4f} SOL < expected {expected:.4f}",
                "action": "Freeze MICRO wallet + alerte rouge fondateur",
            })
    except Exception:
        pass

    # P1 : Erreurs en cascade (>10 erreurs differentes en 24h)
    errors = d.get("erreurs_recurrentes", [])
    recent_errors = [e for e in errors if e.get("count", 0) >= 3]
    if len(recent_errors) >= 5:
        crises.append({
            "level": "P1", "type": "error_cascade",
            "details": f"{len(recent_errors)} erreurs recurrentes (>=3 occurrences chacune)",
            "action": "WATCHDOG diagnostic complet + pause operations non-critiques",
        })

    # P1 : Churn massif (perte >30% clients en 24h)
    kpis = d.get("kpi", [])
    if len(kpis) >= 8:
        clients_now = kpis[-1].get("clients_actifs", 0)
        clients_8h = kpis[-8].get("clients_actifs", 0)
        if clients_8h > 5 and clients_now < clients_8h * 0.7:
            crises.append({
                "level": "P1", "type": "mass_churn",
                "details": f"Clients: {clients_8h} -> {clients_now} (-{clients_8h - clients_now})",
                "action": "ANALYTICS diagnostic + RESPONDER campagne retention",
            })

    # P2 : Budget epuise
    if d.get("emergency_stop"):
        crises.append({
            "level": "P2", "type": "budget_exhausted",
            "details": "Emergency stop actif — budget epuise",
            "action": "Attente revenu ou reset fondateur",
        })

    # P2 : Aucun revenu depuis >7 jours
    if d.get("semaines_0rev", 0) >= 1:
        crises.append({
            "level": "P2", "type": "zero_revenue",
            "details": f"{d.get('semaines_0rev', 0)} semaines sans revenu",
            "action": "HUNTER intensifier prospection + NEGOTIATOR proposer promos",
        })

    # Notifier les agents concernes via le bus
    for crisis in crises:
        level = crisis.get("level", "P3")
        if level in ("P0", "P1"):
            agent_bus.broadcast("CRISIS-MANAGER", "crisis_alert", {"level": level, "type": crisis["type"], "details": crisis["details"]})
        if crisis["type"] == "mass_churn":
            agent_bus.send("CRISIS-MANAGER", "ANALYTICS", "churn_alert", {"details": crisis["details"]})
            agent_bus.send("CRISIS-MANAGER", "RESPONDER", "retention_needed", {"details": crisis["details"]})
        if crisis["type"] == "zero_revenue":
            agent_bus.send("CRISIS-MANAGER", "NEGOTIATOR", "promo_needed", {"weeks": d.get("semaines_0rev", 0)})
            agent_bus.send("CRISIS-MANAGER", "HUNTER", "intensify", {"reason": "zero_revenue"})

    return crises


async def crisis_respond(crisis: dict, memory: Memory) -> dict:
    """Execute le protocole de reponse a une crise."""
    level = crisis.get("level", "P3")
    config = CRISIS_LEVELS.get(level, CRISIS_LEVELS["P3"])
    crisis_type = crisis.get("type", "unknown")
    details = crisis.get("details", "")

    response = {
        "level": level,
        "type": crisis_type,
        "config": config,
        "actions_taken": [],
    }

    # Pause marketing si necessaire
    if config["pause_marketing"]:
        memory.update_agent("GHOST-WRITER", {"status": "pause_crise", "reason": crisis_type})
        memory.update_agent("HUNTER", {"status": "pause_crise", "reason": crisis_type})
        response["actions_taken"].append("marketing_paused")

    # Escalade fondateur si necessaire
    if config["escalate"]:
        await alert_rouge(
            f"CRISE {level}: {crisis_type}",
            f"{details}\n\nAction prevue: {crisis.get('action', 'diagnostic en cours')}\n"
            f"Temps de reponse cible: {config['response_min']} min",
            deadline_h=max(1, config["response_min"] // 60),
        )
        response["actions_taken"].append("founder_alerted")

    # Actions automatiques selon le type
    if crisis_type == "service_outage":
        # Lancer self-heal sur tous les services en erreur
        for err in memory._data.get("erreurs_recurrentes", []):
            if not err.get("patch_proposed"):
                await watchdog_self_heal(err["source"], err["error"], memory)
        response["actions_taken"].append("self_heal_triggered")

    elif crisis_type == "funds_anomaly":
        # Freeze le micro wallet
        micro_wallet._spent_today = MICRO_MAX_PER_DAY  # bloque toute depense
        response["actions_taken"].append("micro_wallet_frozen")

    elif crisis_type == "error_cascade":
        # Demander a Sonnet un diagnostic complet
        diag_prompt = (
            f"CRISE {level}: {details}\n"
            f"Erreurs: {json.dumps(memory._data.get('erreurs_recurrentes', [])[-10:], default=str)}\n"
            f"Analyse la cause racine et propose 3 actions concretes.\n"
            f"JSON: {{cause_racine, actions: [{{action, priorite, agent_cible}}], prevention}}"
        )
        diag = _pj(await _call_anthropic(SONNET_MODEL, CEO_IDENTITY + "\nMode CRISIS-MANAGER.", diag_prompt, 1500))
        if diag:
            response["diagnostic"] = diag
            response["actions_taken"].append("diagnostic_completed")

    elif crisis_type == "mass_churn":
        # Generer un message de retention
        retention_msg = await ghost_write("retention_email", "Why are users leaving? Win-back offer.", "email", memory)
        if retention_msg:
            response["retention_message"] = retention_msg
            response["actions_taken"].append("retention_campaign_drafted")

    elif crisis_type == "zero_revenue":
        # Proposer une promotion temporaire
        promo = {
            "type": "zero_fee_week",
            "duration_days": 7,
            "message": "0% fees for 7 days — bring your AI agent, earn USDC.",
        }
        response["promo_suggested"] = promo
        response["actions_taken"].append("promo_suggested")

    # Logger la crise
    memory.log_decision(
        "orange" if level in ("P0", "P1") else "vert",
        f"CRISIS-MANAGER: {level} {crisis_type} — {len(response['actions_taken'])} actions",
        "crisis_response", "CRISIS-MANAGER",
    )

    await alert_info(f"CRISIS {level} ({crisis_type}): {', '.join(response['actions_taken'])}")
    return response


# ══════════════════════════════════════════
# CEO MAXIA
# ══════════════════════════════════════════

class CEOMaxia:
    def __init__(self):
        self.memory = Memory()
        self._running = False
        self._cycle = 0
        self._last = {"strat": "", "vision": "", "expansion": ""}
        print("[CEO MAXIA] V4 initialise")
        print(f"  Groq: {'actif' if GROQ_API_KEY else 'MANQUANT'}")
        print(f"  Anthropic: {'actif' if ANTHROPIC_API_KEY else 'fallback Groq'}")
        print(f"  Discord: {'actif' if DISCORD_WEBHOOK_URL else 'absent'}")
        print(f"  Budget: {self.memory.get_budget_vert():.4f} SOL")
        print(f"  Emergency: {'⛔ STOP' if self.memory.is_stopped() else 'OK'}")
        print(f"  Agents: GHOST-WRITER, HUNTER, SCOUT, WATCHDOG, SOL-TREASURY, RESPONDER, RADAR, TESTIMONIAL, DEPLOYER, WEB-DESIGNER, ORACLE, MICRO, NEGOTIATOR, COMPLIANCE, PARTNERSHIP, ANALYTICS, CRISIS-MANAGER")

    async def run(self):
        self._running = True
        print("[CEO MAXIA] Demarre — 4 boucles, 17 agents, 5 mecanismes")
        await alert_info("CEO MAXIA V4 demarre")

        while self._running:
            self._cycle += 1
            now = datetime.utcnow()
            today = date.today().isoformat()

            try:
                await self._tactique()

                if now.hour == 20 and self._last["strat"] != today:
                    self._last["strat"] = today
                    await self._strategique()

                if now.weekday() == 6 and now.hour == 18 and self._last["vision"] != today:
                    self._last["vision"] = today
                    await self._vision()

                if now.day == 1 and now.hour == 10 and self._last["expansion"] != today:
                    self._last["expansion"] = today
                    await self._expansion()

                await self._check_hunter()
                await self._check_errors()

            except Exception as e:
                print(f"[CEO] Error #{self._cycle}: {e}")
            await asyncio.sleep(10800)  # 3 heures (economie tokens Groq)

    def stop(self):
        self._running = False

    async def _opus_summarize(self, prompt: str, data: str) -> str:
        return await _call_anthropic(OPUS_MODEL, "Analyste expert. Reponds en JSON.", f"{prompt}\n\nDATA:\n{data}", 2000)

    async def _build_retrospective(self) -> str:
        """Compare les predictions/decisions passees aux resultats reels."""
        d = self.memory._data
        strategies = d.get("strategies", [])
        kpis = d.get("kpi", [])

        if not strategies:
            return "Pas de strategie precedente a comparer."

        last_strat = strategies[-1]
        last_decisions = last_strat.get("decisions", [])

        kpi_now = kpis[-1] if kpis else {}
        kpi_7d = kpis[-168] if len(kpis) >= 168 else kpis[0] if kpis else {}

        retro = (
            f"STRATEGIE PRECEDENTE ({last_strat.get('date', '?')}):\n"
            f"  Decisions: {json.dumps(last_decisions[:5], default=str)}\n\n"
            f"KPI il y a 7j: rev=${kpi_7d.get('rev_total', kpi_7d.get('revenue_total', 0))}, "
            f"clients={kpi_7d.get('clients', kpi_7d.get('clients_actifs', 0))}\n"
            f"KPI maintenant: rev=${kpi_now.get('rev_total', kpi_now.get('revenue_total', 0))}, "
            f"clients={kpi_now.get('clients', kpi_now.get('clients_actifs', 0))}\n\n"
            f"Hunter: {d.get('hunter_canal', '?')} ({self.memory.hunter_rate():.1%})\n"
            f"Budget: {d.get('budget_vert', 0):.4f} SOL (sem sans rev: {d.get('semaines_0rev', 0)})\n"
        )
        return retro

    # ── HUNTER auto-switch ──

    async def _check_hunter(self):
        if self.memory._data.get("hunter_contacts", 0) < 30:
            return
        rate = self.memory.hunter_rate()
        if rate >= HUNTER_MIN_CONVERSION:
            return
        canaux = ["solana_memo", "reddit", "discord_servers", "twitter_replies", "github_issues"]
        current = self.memory._data.get("hunter_canal", "solana_memo")
        try:
            idx = canaux.index(current)
            nxt = canaux[(idx + 1) % len(canaux)]
        except ValueError:
            nxt = "reddit"
        old = self.memory.hunter_switch(nxt)
        msg = f"HUNTER auto-switch: {old} ({rate:.1%}) -> {nxt}"
        print(f"[CEO] {msg}")
        self.memory.add_regle(f"{old} a {rate:.1%} conversion — abandonne")
        await alert_info(msg)

    # ── Self-healing check ──

    async def _check_errors(self):
        for err in self.memory._data.get("erreurs_recurrentes", []):
            if err.get("count", 0) >= 3 and not err.get("patch_proposed"):
                await watchdog_self_heal(err["source"], err["error"], self.memory)

    # ── Fondateur psychology ──

    def _fondateur_tone(self) -> str:
        days = self.memory.fondateur_days_inactive()
        ignored = self.memory._data.get("fondateur_alertes_ignorees", 0)
        if days <= 1 and ignored == 0:
            return "direct_technique"
        elif days <= 3:
            return "encourageant"
        elif days > 3 or ignored > 2:
            return "motivationnel"
        return "normal"

    # ── Boucle 1 : TACTIQUE ──

    async def _tactique(self):
        print(f"\n[CEO] === TACTIQUE #{self._cycle} ===")
        
        # WATCHDOG health check (skip first 2 cycles — server still starting)
        if self._cycle >= 3:
            try:
                health = await watchdog_health_check()
                self.memory.update_agent("WATCHDOG", {
                    "status": "actif",
                    "last_check": health.get("ok", 0),
                    "total": health.get("total", 0),
                    "failed": health.get("failed", 0),
                })
            except Exception as e:
                print(f"[CEO] WATCHDOG health check error: {e}")
        else:
            print(f"[CEO] WATCHDOG skipped (cycle {self._cycle}, waiting for startup)")

        data = await collect()
        self.memory.log_kpi(data)

        # RADAR scan (on-chain)
        radar = await radar_scan(self.memory)

        # ORACLE scan (off-chain — social listening)
        oracle_trends = await oracle_scan_trends(self.memory)

        # Vector Memory — indexer les nouvelles decisions
        try:
            from ceo_vector_memory import vector_memory
            for dec in self.memory._data.get("decisions", [])[-3:]:
                vector_memory.store_decision(dec)
            for conv in self.memory._data.get("conversations", [])[-3:]:
                vector_memory.store_conversation(conv)
        except Exception:
            pass

        # MICRO wallet status
        micro_stats = micro_wallet.get_stats()

        # RADAR auto-actions : si tendance detectee, agir immediatement
        for alert in radar:
            if alert.get("type") == "price_spike":
                token = alert.get("token", "")
                print(f"[CEO] RADAR spike {token} — GHOST-WRITER tweet + DEPLOYER blog")
                tweet = await ghost_write("tweet", f"{token} is pumping! Trade it on MAXIA with lowest fees.", "twitter", self.memory)
                if tweet and not tweet.get("blocked"):
                    # Post to Twitter
                    try:
                        from twitter_bot import post_tweet
                        tweet_text = tweet.get("contenu", tweet.get("content", f"{token} trending on Solana. Trade on MAXIA: {MAXIA_URL}"))
                        await post_tweet(tweet_text)
                    except Exception as e:
                        print(f"[CEO] Twitter post error: {e}")
                    self.memory.log_decision("vert", f"Tweet auto: {token} spike", "RADAR", "GHOST-WRITER")
                # Blog post si c'est une categorie entiere
                if alert.get("category"):
                    await self.deploy_blog(
                        f"{alert['category'].upper()} Tokens Are Trending",
                        f"Analysis of why {alert['category']} tokens surged {alert.get('change',0):.0%} and how to trade them on MAXIA.",
                    )
            elif alert.get("type") == "category_surge":
                cat = alert.get("category", "")
                print(f"[CEO] RADAR surge {cat} — DEPLOYER blog auto")
                # Tweet about category surge
                try:
                    from twitter_bot import post_tweet
                    await post_tweet(f"{cat.upper()} tokens surging on Solana. AI agents trade them on MAXIA marketplace. {MAXIA_URL}")
                except Exception:
                    pass
                await self.deploy_blog(
                    f"Why {cat.upper()} Tokens Are Surging Right Now",
                    f"Market analysis: {cat} category up {alert.get('change',0):.0%}. How AI agents can profit using MAXIA.",
                )

        self.memory.update_agent("GHOST-WRITER", {"status": "actif"})
        self.memory.update_agent("HUNTER", {"canal": self.memory._data.get("hunter_canal", "?"), "rate": f"{self.memory.hunter_rate():.1%}"})
        self.memory.update_agent("WATCHDOG", {"status": "actif"})
        self.memory.update_agent("SOL-TREASURY", {"budget": f"{self.memory.get_budget_vert():.4f}", "stop": self.memory.is_stopped()})
        self.memory.update_agent("RESPONDER", {"responses": self.memory._data.get("responses", 0)})
        self.memory.update_agent("RADAR", {"alerts": len(radar)})
        self.memory.update_agent("DEPLOYER", {"github": "actif" if GITHUB_TOKEN else "absent", "org": GITHUB_ORG})
        self.memory.update_agent("WEB-DESIGNER", {"status": "actif"})
        self.memory.update_agent("ORACLE", {"status": "actif", "trends": len(oracle_trends)})
        self.memory.update_agent("MICRO", micro_stats)
        self.memory.update_agent("TESTIMONIAL", {"count": len(self.memory._data.get("testimonials", []))})

        # CRISIS-MANAGER — detection automatique de crises
        crises = []
        try:
            crises = await crisis_detect(self.memory)
            for crisis in crises:
                print(f"[CEO] CRISIS {crisis['level']}: {crisis['type']} — {crisis['details'][:80]}")
                await crisis_respond(crisis, self.memory)
            self.memory.update_agent("CRISIS-MANAGER", {"status": "actif", "active_crises": len(crises)})
        except Exception as e:
            print(f"[CEO] CRISIS-MANAGER error: {e}")
            self.memory.update_agent("CRISIS-MANAGER", {"status": "erreur", "error": str(e)[:80]})

        # ANALYTICS — metriques avancees (toutes les 3 heures)
        analytics_data = {}
        try:
            analytics_data = await analytics_compute(self.memory)
            self.memory.update_agent("ANALYTICS", {
                "status": "actif",
                "health_score": analytics_data.get("health_score", 0),
                "ltv": analytics_data.get("ltv", 0),
                "churn_rate": analytics_data.get("churn", {}).get("rate", "0%"),
                "recommendations": len(analytics_data.get("recommendations", [])),
            })
            # Afficher les recommandations urgentes
            for rec in analytics_data.get("recommendations", []):
                print(f"[CEO] ANALYTICS: {rec}")
        except Exception as e:
            print(f"[CEO] ANALYTICS error: {e}")
            self.memory.update_agent("ANALYTICS", {"status": "erreur"})

        # PARTNERSHIP — scan partenaires (tous les 6 cycles = ~18h)
        if self._cycle % 6 == 0:
            try:
                opportunities = await partnership_scan(self.memory)
                if opportunities:
                    top = opportunities[0]
                    print(f"[CEO] PARTNERSHIP: top opportunity = {top['partner']} ({top['category']}, score {top['score']})")
                    # Auto-outreach si score >= 80
                    if top["score"] >= 80:
                        await partnership_outreach(top["partner"], top["category"], top["pitch"], self.memory)
                self.memory.update_agent("PARTNERSHIP", {"status": "actif", "opportunities": len(opportunities)})
            except Exception as e:
                print(f"[CEO] PARTNERSHIP error: {e}")
                self.memory.update_agent("PARTNERSHIP", {"status": "erreur"})

        # NEGOTIATOR + COMPLIANCE — stats
        self.memory.update_agent("NEGOTIATOR", {"status": "actif", "mode": "auto"})
        self.memory.update_agent("COMPLIANCE", {
            "status": "actif",
            "blocked_wallets": len(self.memory._data.get("compliance_blocked", [])),
        })

        # SCOUT stats
        try:
            from scout_agent import scout_agent as _scout
            self.memory.update_agent("SCOUT", _scout.get_stats())
        except Exception:
            self.memory.update_agent("SCOUT", {"status": "non-demarre"})

        # ── BUS CONSUMPTION — traiter les messages inter-agents ──
        for agent_name in ["HUNTER", "NEGOTIATOR", "TESTIMONIAL", "RESPONDER", "GHOST-WRITER"]:
            msgs = agent_bus.get_messages(agent_name)
            for msg in msgs:
                msg_type = msg.get("type", "")
                msg_data = msg.get("data", {})
                # HUNTER recoit des ordres d'intensification
                if agent_name == "HUNTER" and msg_type == "intensify":
                    self.memory.update_agent("HUNTER", {"intensify": True, "reason": msg_data.get("reason", "")})
                elif agent_name == "HUNTER" and msg_type == "low_conversion":
                    print(f"[CEO] BUS->HUNTER: low conversion ({msg_data.get('rate')})")
                # NEGOTIATOR recoit des demandes de promo
                elif agent_name == "NEGOTIATOR" and msg_type == "promo_needed":
                    self.memory.update_agent("NEGOTIATOR", {"promo_mode": True, "weeks_0rev": msg_data.get("weeks", 0)})
                elif agent_name == "NEGOTIATOR" and msg_type == "low_ltv":
                    self.memory.update_agent("NEGOTIATOR", {"bundle_mode": True, "ltv": msg_data.get("ltv", 0)})
                elif agent_name == "NEGOTIATOR" and msg_type == "wallet_blocked":
                    self.memory.update_agent("NEGOTIATOR", {"blocked_wallet": msg_data.get("wallet", "")[:16]})
                # TESTIMONIAL recoit des alertes churn
                elif agent_name == "TESTIMONIAL" and msg_type == "churn_high":
                    self.memory.update_agent("TESTIMONIAL", {"churn_alert": True, "rate": msg_data.get("rate", "")})
                # RESPONDER recoit des demandes de retention
                elif agent_name == "RESPONDER" and msg_type == "retention_needed":
                    self.memory.update_agent("RESPONDER", {"retention_mode": True})
            if msgs:
                agent_bus.ack(agent_name)

        # ── BUS ACTIONS — agents agissent sur les flags ──
        # HUNTER : si intensify flag, changer de canal automatiquement
        hunter_data = self.memory._data.get("agents", {}).get("HUNTER", {})
        if hunter_data.get("intensify"):
            canaux = ["solana_memo", "reddit", "discord_servers", "twitter_replies", "github_issues"]
            current = self.memory._data.get("hunter_canal", "solana_memo")
            try:
                idx = canaux.index(current)
                nxt = canaux[(idx + 1) % len(canaux)]
            except ValueError:
                nxt = "reddit"
            self.memory.hunter_switch(nxt)
            hunter_data["intensify"] = False
            print(f"[CEO] BUS ACTION: HUNTER intensify -> switch to {nxt}")

        # NEGOTIATOR : si promo_mode, creer un A/B test promo automatiquement
        nego_data = self.memory._data.get("agents", {}).get("NEGOTIATOR", {})
        if nego_data.get("promo_mode") and not self.memory._data.get("ab_tests", {}).get("promo_zero_fee"):
            self.memory.create_ab_test("promo_zero_fee",
                "0% fees for 7 days — bring your AI agent, earn USDC.",
                "First 10 trades free. AI agents earn USDC on MAXIA.")
            nego_data["promo_mode"] = False
            print("[CEO] BUS ACTION: NEGOTIATOR created promo A/B test")

        # NEGOTIATOR : si bundle_mode, log recommandation
        if nego_data.get("bundle_mode"):
            self.memory.add_regle(f"LTV faible ({nego_data.get('ltv', 0)}) — NEGOTIATOR doit proposer des bundles")
            nego_data["bundle_mode"] = False

        # TESTIMONIAL : si churn_alert, generer contenu retention
        testi_data = self.memory._data.get("agents", {}).get("TESTIMONIAL", {})
        if testi_data.get("churn_alert"):
            try:
                content = await ghost_write("retention_tweet", "Users are leaving — remind them why MAXIA is the cheapest AI marketplace", "twitter", self.memory)
                if content and not content.get("blocked"):
                    try:
                        from twitter_bot import post_tweet
                        await post_tweet(content.get("contenu", content.get("content", "")))
                    except Exception:
                        pass
                testi_data["churn_alert"] = False
                print("[CEO] BUS ACTION: TESTIMONIAL anti-churn tweet posted")
            except Exception as e:
                print(f"[CEO] BUS ACTION TESTIMONIAL error: {e}")

        # RESPONDER : si retention_mode, activer reponse proactive
        resp_data = self.memory._data.get("agents", {}).get("RESPONDER", {})
        if resp_data.get("retention_mode"):
            self.memory.add_regle("Retention mode actif — RESPONDER doit etre plus proactif et offrir des discounts")
            resp_data["retention_mode"] = False
            print("[CEO] BUS ACTION: RESPONDER retention mode activated")

        # RAG — rechercher le contexte pertinent
        rag_context = ""
        try:
            from ceo_vector_memory import vector_memory
            if data.get("erreurs"):
                rag_context = vector_memory.search_context(" ".join(str(e) for e in data["erreurs"][:2]), 3)
        except Exception:
            pass

        ctx = self.memory.ctx("tactique")
        q = (
            f"Rev: ${data['rev_24h']} | Clients: {data['clients_actifs']} | "
            f"Budget: {self.memory.get_budget_vert():.4f} | Stop: {self.memory.is_stopped()}\n"
            f"Hunter: {self.memory._data.get('hunter_canal','?')} ({self.memory.hunter_rate():.1%})\n"
            f"Radar: {len(radar)} alertes | Oracle: {len(oracle_trends)} tendances\n"
            f"Micro wallet: {micro_stats.get('remaining_today', 0):.4f} SOL dispo\n"
            f"Erreurs: {data['erreurs']}\n"
            f"{rag_context}\n\n"
            "Decisions tactiques ? JSON: {reflexion, situation, decisions: [{action, cible, priorite}], regles_apprises, message_fondateur}\n"
            "IMPORTANT — cible DOIT etre un de : GHOST-WRITER, HUNTER, SCOUT, WATCHDOG, SOL-TREASURY, RESPONDER, RADAR, TESTIMONIAL, DEPLOYER, NEGOTIATOR, COMPLIANCE, PARTNERSHIP, ANALYTICS, CRISIS-MANAGER, FONDATEUR.\n"
            "IMPORTANT — action DOIT etre une directive CONCRETE et EXECUTABLE (ex: 'tweet: MAXIA fees reduced', 'switch canal discord', 'contact wallet Xyz'). "
            "PAS de phrases vagues comme 'maximiser les chances de succes' ou 'ameliorer la visibilite'."
        )
        result = _pj(await _call_groq(CEO_IDENTITY, f"CONTEXTE:\n{ctx}\n\n{q}"))
        if result:
            await execute(result.get("decisions", []), self.memory)
            for r in result.get("regles_apprises", []):
                self.memory.add_regle(r)

    # ── Boucle 2 : STRATEGIQUE + Red Teaming ──

    async def _strategique(self):
        print(f"\n[CEO] === STRATEGIQUE + RED TEAM ===")
        tone = self._fondateur_tone()
        ctx = self.memory.ctx("strategique")
        q = (
            f"ANALYSE QUOTIDIENNE (ton fondateur: {tone})\n\n"
            "OBJECTIF : 10 000 euros/mois\n"
            f"Revenu actuel : ${self.memory._data.get('revenue_usd', 0)}/mois\n"
            f"Clients actifs : {self.memory._data.get('clients', 0)}\n"
            f"Progression : {min(100, self.memory._data.get('revenue_usd', 0) / 100):.1f}%\n\n"
            "1. SWOT detaille\n"
            "2. Performance de chaque sous-agent (9+2 agents)\n"
            "3. Quel canal convertit ? RESPONDER efficace ?\n"
            "4. TESTIMONIALS recus ? Social proof ?\n"
            "5. ERREURS RECURRENTES ? Self-healing necessaire ?\n"
            "6. PRIX vs CONCURRENCE : sommes-nous les moins chers partout ?\n"
            "   (swap vs Jupiter/Binance, GPU vs AWS/Lambda, IA vs Certik)\n"
            "   Si un concurrent est moins cher → BAISSER immediatement\n"
            "7. Combien de clients faut-il pour atteindre 10 000 euros/mois ?\n\n"
            "🔴 RED TEAMING OBLIGATOIRE :\n"
            "Avant de valider ton plan, imagine que tu es :\n"
            "A) Un concurrent agressif — comment exploiter la faiblesse de MAXIA ?\n"
            "B) Un utilisateur sceptique — pourquoi NE PAS utiliser MAXIA ?\n"
            "C) Un investisseur exigeant — pourquoi ce plan ne scale PAS ?\n"
            "Trouve 3 raisons concretes d'echec. Puis ajuste ton plan.\n\n"
            f"{'Si fondateur inactif >3j, inclus des victoires pour remotiver.' if tone == 'motivationnel' else ''}\n\n"
            "JSON: {reflexion, situation, analyse_swot, red_team: {concurrent, sceptique, investisseur}, "
            "plan_ajuste, performance_agents, decisions, regles_apprises, message_fondateur}\n"
            "IMPORTANT — decisions[].cible DOIT etre un de : GHOST-WRITER, HUNTER, SCOUT, WATCHDOG, SOL-TREASURY, RESPONDER, RADAR, TESTIMONIAL, DEPLOYER, NEGOTIATOR, COMPLIANCE, PARTNERSHIP, ANALYTICS, CRISIS-MANAGER, FONDATEUR.\n"
            "IMPORTANT — decisions[].action DOIT etre CONCRETE (ex: 'tweet: ...', 'switch canal discord'). PAS de phrases vagues."
        )
        result = _pj(await _call_anthropic(SONNET_MODEL, CEO_IDENTITY, f"CONTEXTE:\n{ctx}\n\n{q}"))
        if result:
            self.memory.log_rapport(result)
            await execute(result.get("decisions", []), self.memory)
            for r in result.get("regles_apprises", []):
                self.memory.add_regle(r)
            rev = sum(k.get("rev_24h", 0) for k in self.memory._data["kpi"][-24:])
            self.memory.update_budget(rev)
            msg = result.get("message_fondateur")
            if msg:
                await alert_info(f"Rapport: {msg[:300]}")

        # ANALYTICS rapport quotidien enrichi
        try:
            analytics = await analytics_compute(self.memory)
            health = analytics.get("health_score", 0)
            if health < 40:
                await alert_info(f"ANALYTICS: Health score CRITIQUE ({health}/100) — {analytics.get('recommendations', ['aucune'])}")
        except Exception as e:
            print(f"[CEO] ANALYTICS strategique error: {e}")

    # ── Boucle 3 : VISION + Compaction ──

    async def _vision(self):
        print(f"\n[CEO] === VISION + RETROSPECTIVE ===")
        ctx = self.memory.ctx("vision")

        # Construire la retrospective des predictions passees
        retro = await self._build_retrospective()

        q = (
            "OKR, roadmap 6 mois, nouveau produit ?, nouvel agent ?\n"
            "PATCHS proposes — lesquels appliquer ?\n"
            "3 priorites de la semaine. Note fondateur.\n\n"
            "🔄 RETROSPECTIVE OBLIGATOIRE :\n"
            "Compare tes predictions de la semaine derniere aux resultats reels.\n"
            "Pour chaque prediction ratee, ajoute une regle dans regles_apprises.\n"
            f"Predictions passees vs realite :\n{retro}\n\n"
            "JSON: {reflexion, retrospective: {predictions_vs_realite: [{prediction, resultat, lecon}]}, "
            "okr, roadmap, nouveau_produit, nouvel_agent, decisions, regles_apprises, message_fondateur}\n"
            "IMPORTANT — decisions[].cible DOIT etre un de : GHOST-WRITER, HUNTER, SCOUT, WATCHDOG, SOL-TREASURY, RESPONDER, RADAR, TESTIMONIAL, DEPLOYER, NEGOTIATOR, COMPLIANCE, PARTNERSHIP, ANALYTICS, CRISIS-MANAGER, FONDATEUR.\n"
            "IMPORTANT — decisions[].action DOIT etre CONCRETE (ex: 'deploy blog: ...', 'adjust prix swap 0.01%'). PAS de phrases vagues."
        )
        result = _pj(await _call_anthropic(OPUS_MODEL, CEO_IDENTITY, f"CONTEXTE:\n{ctx}\n\n{q}", 4000))
        if result:
            if result.get("okr"):
                self.memory.update_okr(result["okr"])
            if result.get("roadmap"):
                self.memory.update_roadmap(result["roadmap"])
            np = result.get("nouveau_produit")
            if np and np.get("nom"):
                self.memory._data["produits"].append(np)
                self.memory.save()
            # Sauvegarder la retrospective
            retro_data = result.get("retrospective", {})
            if retro_data:
                for item in retro_data.get("predictions_vs_realite", []):
                    lecon = item.get("lecon", "")
                    if lecon:
                        self.memory.add_regle(lecon)
                        self.memory.add_lecon(lecon)
            self.memory.log_strategie(result)
            await execute(result.get("decisions", []), self.memory)
            for r in result.get("regles_apprises", []):
                self.memory.add_regle(r)

        # WEB-DESIGNER : mettre a jour la config frontend
        try:
            config = await web_designer_update_config(self.memory)
            await web_designer_deploy_config(config, self.memory)
            print("[CEO] WEB-DESIGNER: config.json deploye")
        except Exception as e:
            print(f"[CEO] WEB-DESIGNER error: {e}")

        # ANALYTICS rapport hebdomadaire (dimanche = boucle vision)
        try:
            report = await analytics_weekly_report(self.memory)
            if report.get("message_fondateur"):
                await alert_info(f"ANALYTICS HEBDO: {report['message_fondateur'][:300]}")
            print(f"[CEO] ANALYTICS: rapport hebdo genere (health={report.get('metrics', {}).get('health_score', '?')})")
        except Exception as e:
            print(f"[CEO] ANALYTICS weekly error: {e}")

        # PARTNERSHIP scan hebdo — identifier les top partenaires
        try:
            opportunities = await partnership_scan(self.memory)
            if opportunities:
                top3 = [f"{o['partner']} ({o['score']})" for o in opportunities[:3]]
                print(f"[CEO] PARTNERSHIP hebdo: top3 = {', '.join(top3)}")
        except Exception as e:
            print(f"[CEO] PARTNERSHIP weekly error: {e}")

        # Auto-deploy pages
        await self.auto_deploy_check()
        # Compaction memoire
        await self.memory.compact(self._opus_summarize)
        await self.memory.summarize_old_data(self._opus_summarize)

    # ── Boucle 4 : EXPANSION ──

    async def _expansion(self):
        print(f"\n[CEO] === EXPANSION ===")
        ctx = self.memory.ctx("expansion")
        q = (
            "Marche mondial, concurrents, geographie, langues, chains, partenariats, financement.\n"
            "Phases : actuelle -> suivante -> finale. Objectif, strategie, cout, timeline.\n"
            "JSON: {reflexion, marche, concurrents, expansion_plan, nouvelle_langue, nouvelle_chain, "
            "partenariats_cibles, financement, nouveau_produit_mondial, decisions, regles_apprises, message_fondateur}\n"
            "IMPORTANT — decisions[].cible DOIT etre un de : GHOST-WRITER, HUNTER, SCOUT, WATCHDOG, SOL-TREASURY, RESPONDER, RADAR, TESTIMONIAL, DEPLOYER, NEGOTIATOR, COMPLIANCE, PARTNERSHIP, ANALYTICS, CRISIS-MANAGER, FONDATEUR.\n"
            "IMPORTANT — decisions[].action DOIT etre CONCRETE. PAS de phrases vagues."
        )
        result = _pj(await _call_anthropic(OPUS_MODEL, CEO_IDENTITY, f"CONTEXTE:\n{ctx}\n\n{q}", 5000))
        if result:
            if result.get("marche"):
                self.memory._data["marche"] = result["marche"]
            if result.get("concurrents"):
                self.memory._data["concurrents"] = result["concurrents"]
            nl = result.get("nouvelle_langue")
            if nl and nl not in self.memory._data["langues"]:
                self.memory._data["langues"].append(nl)
            nc = result.get("nouvelle_chain")
            if nc and nc not in self.memory._data["chains"]:
                self.memory._data["chains"].append(nc)
            self.memory.log_expansion(result)
            self.memory.save()
            await execute(result.get("decisions", []), self.memory)
            for r in result.get("regles_apprises", []):
                self.memory.add_regle(r)
            if result.get("message_fondateur"):
                await alert_rouge("Expansion mensuelle", result["message_fondateur"], deadline_h=24)

    # ── API publique ──

    async def handle_message(self, canal: str, user: str, msg: str) -> dict:
        return await respond(canal, user, msg, self.memory)

    async def handle_transaction_success(self, user: str, tx_sig: str, service: str):
        """Appele apres chaque transaction reussie pour TESTIMONIAL."""
        return await testimonial_request(user, tx_sig, service, self.memory)

    async def handle_feedback(self, user: str, feedback: str) -> dict:
        return await testimonial_process(user, feedback, self.memory)

    def reset_emergency(self):
        self.memory.reset_emergency()
        print("[CEO] Emergency stop desactive")

    def fondateur_ping(self):
        self.memory.fondateur_responded()

    # ── NEGOTIATOR ──

    async def negotiate_price(self, buyer: str, service: str, proposed_price: float) -> dict:
        """Negocie un prix avec un agent acheteur."""
        return await negotiator_evaluate(buyer, service, proposed_price, self.memory)

    async def negotiate_bundle(self, buyer: str, services: list) -> dict:
        """Negocie un pack de services avec remise volume."""
        return await negotiator_bulk_deal(buyer, services, self.memory)

    # ── COMPLIANCE ──

    async def check_wallet(self, wallet: str) -> dict:
        """Verifie la conformite d'un wallet."""
        return await compliance_check_wallet(wallet, self.memory)

    async def check_transaction(self, amount: float, sender: str, receiver: str) -> dict:
        """Verifie la conformite d'une transaction."""
        return await compliance_check_transaction(amount, sender, receiver, self.memory)

    # ── PARTNERSHIP ──

    async def scan_partners(self) -> list:
        """Scanne les opportunites de partenariat."""
        return await partnership_scan(self.memory)

    async def contact_partner(self, partner: str, category: str, pitch: str) -> dict:
        """Envoie un message de demarchage a un partenaire."""
        return await partnership_outreach(partner, category, pitch, self.memory)

    # ── ANALYTICS ──

    async def get_analytics(self) -> dict:
        """Retourne les metriques avancees."""
        return await analytics_compute(self.memory)

    async def weekly_report(self) -> dict:
        """Genere le rapport hebdomadaire enrichi."""
        return await analytics_weekly_report(self.memory)

    # ── CRISIS-MANAGER ──

    async def detect_crises(self) -> list:
        """Detecte les crises en cours."""
        return await crisis_detect(self.memory)

    async def handle_crisis(self, crisis: dict) -> dict:
        """Execute le protocole de reponse a une crise."""
        return await crisis_respond(crisis, self.memory)

    # ── KILL SWITCH ──

    def disable_agent(self, agent_name: str, reason: str = "manual"):
        self.memory.disable_agent(agent_name, reason)

    def enable_agent(self, agent_name: str):
        self.memory.enable_agent(agent_name)

    def get_disabled_agents(self) -> dict:
        return self.memory.get_disabled_agents()

    # ── ROI ──

    def get_roi(self) -> dict:
        return self.memory.get_roi_stats()

    # ── A/B TESTING ──

    def create_test(self, name: str, variant_a: str, variant_b: str):
        self.memory.create_ab_test(name, variant_a, variant_b)

    def get_ab_results(self) -> dict:
        return self.memory.get_ab_results()

    # ── DEPLOYER ──

    async def deploy_page(self, page_type: str, extra_data: dict = None) -> dict:
        """Genere et deploie une page web automatiquement."""
        data = extra_data or {}
        data["testimonials"] = self.memory._data.get("testimonials", [])
        data["kpi"] = self.memory._data.get("kpi", [])[-168:]
        data["revenue"] = self.memory._data.get("revenue_usd", 0)
        data["clients"] = self.memory._data.get("clients", 0)
        data["decisions"] = self.memory._data.get("decisions", [])[-10:]
        return await deployer_create_and_deploy(page_type, data, self.memory)

    async def deploy_blog(self, titre: str, sujet: str) -> dict:
        """Ecrit et deploie un article de blog."""
        return await deployer_blog_post(titre, sujet, self.memory)

    async def auto_deploy_check(self):
        """Verifie les declencheurs et deploie automatiquement."""
        d = self.memory._data

        # /status — toujours deploye, mise a jour hebdo
        last_status = next((dec for dec in reversed(d.get("decisions", []))
                           if "status deploye" in dec.get("decision", "").lower()), None)
        if not last_status:
            await self.deploy_page("status")

        # /docs — apres premier client
        if d.get("clients", 0) >= 1:
            has_docs = any("docs deploye" in dec.get("decision", "").lower()
                          for dec in d.get("decisions", []))
            if not has_docs:
                await self.deploy_page("docs")

        # /testimonials — apres 3 feedbacks positifs
        positive = [t for t in d.get("testimonials", []) if t.get("published")]
        if len(positive) >= 3:
            has_testimonials = any("testimonials deploye" in dec.get("decision", "").lower()
                                  for dec in d.get("decisions", []))
            if not has_testimonials:
                await self.deploy_page("testimonials")

        # /compare — une fois
        has_compare = any("compare deploye" in dec.get("decision", "").lower()
                         for dec in d.get("decisions", []))
        if not has_compare and d.get("clients", 0) >= 1:
            await self.deploy_page("compare")

    def get_status(self) -> dict:
        d = self.memory._data
        return {
            "name": "CEO MAXIA V4",
            "running": self._running, "cycle": self._cycle,
            "emergency_stop": d.get("emergency_stop", False),
            "cerveaux": {
                "tactique": "Groq (gratuit)",
                "strategique": f"Sonnet ({'actif' if ANTHROPIC_API_KEY else 'Groq'})",
                "vision": f"Opus ({'actif' if ANTHROPIC_API_KEY else 'Groq'})",
                "expansion": f"Opus ({'actif' if ANTHROPIC_API_KEY else 'Groq'})",
            },
            "budget": {"vert": d.get("budget_vert", BASE_BUDGET_VERT), "sem_0rev": d.get("semaines_0rev", 0)},
            "hunter": {"canal": d.get("hunter_canal", "?"), "rate": f"{self.memory.hunter_rate():.1%}"},
            "fondateur": {"inactif_jours": self.memory.fondateur_days_inactive(), "alertes_ignorees": d.get("fondateur_alertes_ignorees", 0)},
            "agents": d.get("agents", {}),
            "stats": {
                "decisions": len(d.get("decisions", [])), "regles": len(d.get("regles", [])),
                "lecons": len(d.get("lecons_cles", [])), "produits": len(d.get("produits", [])),
                "conversations": d.get("responses", 0), "testimonials": len(d.get("testimonials", [])),
                "erreurs": len(d.get("erreurs_recurrentes", [])), "patchs": len(d.get("patchs_proposes", [])),
                "revenue": d.get("revenue_usd", 0), "clients": d.get("clients", 0),
            },
            "expansion": {"langues": d.get("langues", []), "chains": d.get("chains", []), "concurrents": len(d.get("concurrents", []))},
            "deployer": {"github_org": GITHUB_ORG, "github_repo": GITHUB_REPO, "token": "actif" if GITHUB_TOKEN else "absent"},
            "oracle": {"dexscreener": "actif", "github": "actif"},
            "micro_wallet": micro_wallet.get_stats(),
            "failover": {"rpc_active": FAILOVER_RPC[_active_rpc_index]["name"], "rpc_failures": _rpc_failures},
            "okr": d.get("okr", {}),
            "compliance": {"blocked_wallets": len(d.get("compliance_blocked", []))},
            "partnerships": {"count": len(d.get("partnerships", [])), "active": [p["name"] for p in d.get("partnerships", []) if p.get("status") == "active"]},
            "agent_bus": agent_bus.get_stats(),
            "disabled_agents": d.get("disabled_agents", {}),
            "roi": self.memory.get_roi_stats(),
            "ab_tests": {k: {"status": v.get("status"), "winner": v.get("winner")} for k, v in d.get("ab_tests", {}).items()},
            "llm_costs": get_llm_costs(),
        }


ceo = CEOMaxia()

if __name__ == "__main__":
    async def test():
        print("=" * 60)
        print("  CEO MAXIA V4 — Test Complet")
        print("=" * 60)

        # RESPONDER
        print("\n--- RESPONDER ---")
        for canal, user, msg in [
            ("twitter_dm", "dev_42", "How do I swap SOL to USDC?"),
            ("discord", "whale", "Is this legit?"),
            ("twitter_dm", "vc", "Interested in investing"),
            ("telegram", "angry", "My swap failed!"),
        ]:
            r = await ceo.handle_message(canal, user, msg)
            print(f"  [{canal}] {user}: {msg[:30]} -> {r.get('intention')} | alerte:{r.get('alerte_fondateur')}")

        # GHOST-WRITER + WATCHDOG
        print("\n--- GHOST-WRITER + WATCHDOG ---")
        c = await ghost_write("tweet", "MAXIA 15 tokens live", "twitter")
        print(f"  Blocked: {c.get('blocked', False)} | {json.dumps(c, default=str)[:100]}")

        # TESTIMONIAL
        print("\n--- TESTIMONIAL ---")
        req = await ceo.handle_transaction_success("dev_42", "5FxMAK...", "swap SOL/USDC")
        print(f"  Request: {req[:80]}")
        fb = await ceo.handle_feedback("dev_42", "Works great, super fast!")
        print(f"  Feedback: publish={fb.get('publish')} | {fb.get('tweet','')[:60]}")

        # BUDGET DECAY
        print("\n--- BUDGET ---")
        ceo.memory.update_budget(0)
        print(f"  Sem 1 sans rev: {ceo.memory.get_budget_vert():.4f}")
        ceo.memory.update_budget(0)
        print(f"  Sem 2 sans rev: {ceo.memory.get_budget_vert():.4f}")
        ceo.memory.update_budget(0)
        print(f"  Sem 3 sans rev: {ceo.memory.get_budget_vert():.4f}")
        ceo.memory.update_budget(5)
        print(f"  Apres $5 rev: {ceo.memory.get_budget_vert():.4f} (reset)")

        # EMERGENCY STOP
        print("\n--- EMERGENCY STOP ---")
        for i in range(7):
            ceo.memory.log_decision("orange", f"Test spend {i}", "test", "TEST")
        print(f"  Stop: {ceo.memory.is_stopped()}")
        ceo.reset_emergency()
        print(f"  Apres reset: {ceo.memory.is_stopped()}")

        # FONDATEUR
        print("\n--- FONDATEUR ---")
        print(f"  Inactif: {ceo.memory.fondateur_days_inactive()}j")
        print(f"  Tone: {ceo._fondateur_tone()}")

        # TACTIQUE
        print("\n--- TACTIQUE ---")
        await ceo._tactique()

        # DEPLOYER
        print("\n--- DEPLOYER ---")
        result = await ceo.deploy_page("status")
        print(f"  Status page: {result.get('success', False)} | {result.get('url', result.get('error', ''))}")

        # BLOG
        print("\n--- BLOG ---")
        blog = await ceo.deploy_blog("AI Trading on Solana", "How AI agents use MAXIA API to trade 15 tokens")
        print(f"  Blog: {blog.get('success', False)} | {blog.get('url', blog.get('error', ''))}")

        # STATUS
        print("\n--- STATUS ---")
        print(json.dumps(ceo.get_status(), indent=2, default=str))

    asyncio.run(test())
