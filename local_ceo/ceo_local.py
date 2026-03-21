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


def _load_memory() -> dict:
    try:
        if os.path.exists(_MEMORY_FILE):
            with open(_MEMORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
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
        with open(_MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(mem, f, indent=2, default=str, ensure_ascii=False)
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
    print(line)
    try:
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ══════════════════════════════════════════
# LLM Router local (simplifie — Ollama + Mistral fallback)
# ══════════════════════════════════════════

async def call_ollama(prompt: str, system: str = "", max_tokens: int = 500) -> str:
    """Appel Ollama local (0 cout)."""
    full = f"{system}\n\n{prompt}" if system else prompt
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model": OLLAMA_MODEL,
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
                    print(f"  [VPS/think] Cache hit (0$)")
                else:
                    print(f"  [VPS/think] {tier} ~${cost}")
                return data.get("result", "")
        except Exception as e:
            print(f"[VPS] think error: {e}")
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
Rev=0, objectif 10k€/mois. Cible: devs AI sans revenus.

ACTIONS: post_tweet, reply_tweet, like_tweet, follow_user, search_twitter, search_profiles,
get_mentions, post_reddit, comment_reddit, search_reddit, send_alert, browse_competitor,
dm_twitter, send_telegram, star_github, send_discord.
Priorites: vert=auto, orange=validation, rouge=fondateur.
Max 3 actions. JSON: {"decisions":[{"action":"...","agent":"...","params":{...},"priority":"vert"}]}"""


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

                # 5. SYNC — envoyer les actions au VPS (eviter double-post)
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

            # 6. SLEEP
            await asyncio.sleep(OODA_INTERVAL_S)

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
            summary + "\n\nResume la situation en 3 points cles et identifie le probleme principal.",
            system="Tu es un analyste business. Sois concis et factuel.",
            max_tokens=300,
        )
        _log(f"  Analyse: {analysis[:150]}")
        return analysis

    async def _decide(self, analysis: str, state: dict) -> list:
        """DECIDE — Ollama classifie la situation, Claude decide si necessaire."""
        _log("[DECIDE] Classification locale...")
        kpis = state.get("kpi", {})

        context = (
            f"ANALYSE: {analysis[:300]}\n"
            f"Rev=${kpis.get('revenue_24h', 0)}, Clients={kpis.get('clients_actifs', 0)}, "
            f"Emergency={kpis.get('emergency_stop', False)}"
        )

        # Etape 1: Ollama classifie — routine ou strategique ? (0 cout)
        classify_prompt = (
            f"{context}\n\n"
            "Cette situation necessite-t-elle une reflexion strategique (changement de prix, "
            "nouveau canal marketing, decision budget) ou juste des actions de routine "
            "(tweet, monitoring, rapport) ?\n"
            "Reponds UN MOT: routine ou strategique"
        )
        classification = await call_local_llm(classify_prompt, max_tokens=10)
        is_strategic = "strateg" in classification.lower()
        _log(f"  Classification: {'STRATEGIQUE -> Claude' if is_strategic else 'ROUTINE -> Ollama'}")

        # Etape 2: Generer les decisions
        decide_prompt = (
            f"{context}\n\n"
            "Max 3 actions. Pas d actions vagues.\n"
            "JSON: {\"decisions\": [{\"action\": \"...\", \"agent\": \"...\", \"params\": {...}, \"priority\": \"vert|orange|rouge\"}]}"
        )

        if is_strategic:
            # Delegue a Claude sur le VPS (cache 10 min, prompt compresse)
            result_text = await self.vps.think(
                CEO_SYSTEM + "\n\n" + decide_prompt,
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

                # Sauvegarder en memoire
                self.memory["actions_done"].append({
                    "action": action, "agent": agent, "priority": priority,
                    "success": success, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                })

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
