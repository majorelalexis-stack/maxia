"""CEO MAXIA — Agent Autonome V4 (Final)

Architecture hybride : Groq (tactique) + Sonnet (strategie) + Opus (vision/expansion)
Un seul agent, une seule memoire, 4 boucles, 17 sous-agents, 5 mecanismes internes.

SOUS-AGENTS :
  GHOST-WRITER   : Contenu, tweets, threads (valide par WATCHDOG avant publication)
  HUNTER         : Prospection HUMAINE profil Thomas (devs avec bots IA sans revenus)
  SCOUT          : Prospection IA-to-IA sur 14 chains (Olas, Fetch, ElizaOS, Virtuals)
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
  1. TACTIQUE     (horaire)    -- Groq    -- decisions rapides
  2. STRATEGIQUE  (quotidienne)-- Sonnet  -- SWOT + Red Teaming (avocat du diable)
  3. VISION       (hebdo)      -- Opus    -- OKR, roadmap, produits, compaction memoire
  4. EXPANSION    (mensuelle)  -- Opus    -- marche mondial, multi-chain, multi-langue

MODULES :
  ceo_llm.py        -- Constants, LLM Router, Cost Tracking, Alerts
  ceo_memory.py     -- Persistent Memory class
  ceo_subagents.py  -- 17 autonomous sub-agents
  ceo_core.py       -- CEOMaxia class with 4 decision loops
"""
import logging
import asyncio, json
from datetime import datetime

logger = logging.getLogger(__name__)


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
        logger.info("BUS: %s -> %s: %s", sender, receiver, msg_type)

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
            logger.warning("TaskQueue FULL — dropping %s", task_name)

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
                    logger.error("TaskQueue error in %s: %s", task_name, e)
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
# RE-EXPORTS — backward compatibility
# All external modules import from ceo_maxia (unchanged).
# ══════════════════════════════════════════

# --- ceo_llm.py ---
from ceo_llm import (  # noqa: F401, E402
    _cfg, CEO_IDENTITY, get_llm_costs, _call_groq, _call_anthropic, _pj,
    alert_rouge, alert_info, _ceo_private, _pending_decisions,
    GROQ_API_KEY, ANTHROPIC_API_KEY, DISCORD_WEBHOOK_URL, TWITTER_API_KEY,
    GROQ_MODEL, SONNET_MODEL, OPUS_MODEL,
    FOUNDER_NAME, COMPANY, PRODUCT, PHASE, VISION, URL, MAXIA_URL,
    BASE_BUDGET_VERT, BASE_BUDGET_ORANGE, BUDGET_ROUGE,
    BUDGET_DECAY_WEEKLY, MIN_BUDGET_VERT,
    HUNTER_MIN_CONVERSION, EMERGENCY_ORANGE_LIMIT,
    MAX_PROSPECTS_DAY, MAX_TWEETS_DAY,
    _gpu_cheapest, llm_router, Tier,
)

# --- ceo_memory.py ---
from ceo_memory import Memory, HEALTH_ENDPOINTS  # noqa: F401, E402

# --- ceo_subagents.py ---
from ceo_subagents import (  # noqa: F401, E402
    watchdog_health_check, watchdog_check_service, watchdog_self_heal,
    radar_scan, oracle_scan_trends,
    failover_get_rpc, failover_send_alert,
    MicroWallet, micro_wallet,
    testimonial_request, testimonial_process,
    respond, ghost_write, collect, execute,
    web_designer_update_config, web_designer_deploy_config,
    deployer_generate_page, deployer_push_github,
    deployer_create_and_deploy, deployer_blog_post,
    negotiator_evaluate, negotiator_bulk_deal,
    compliance_check_wallet, compliance_check_transaction,
    partnership_scan, partnership_outreach,
    analytics_compute, analytics_weekly_report,
    crisis_detect, crisis_respond,
    scout_scan_onchain_agents, scout_first_contact_a2a,
    FAILOVER_RPC, _active_rpc_index, _rpc_failures,
    GITHUB_TOKEN, GITHUB_ORG, GITHUB_REPO,
)

# --- ceo_core.py ---
from ceo_core import CEOMaxia  # noqa: F401, E402


# ══════════════════════════════════════════
# SINGLETON
# ══════════════════════════════════════════

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
        c = await ghost_write("tweet", "MAXIA 107 tokens live", "twitter")
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
        blog = await ceo.deploy_blog("AI Trading on Solana", "How AI agents use MAXIA API to trade 107 tokens")
        print(f"  Blog: {blog.get('success', False)} | {blog.get('url', blog.get('error', ''))}")

        # STATUS
        print("\n--- STATUS ---")
        print(json.dumps(ceo.get_status(), indent=2, default=str))

    asyncio.run(test())
