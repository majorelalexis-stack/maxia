"""CEO Memory — Persistent memory for CEO agent.

Extracted from ceo_maxia.py (S34 split).
"""
import logging
import json, os
from datetime import datetime, date

logger = logging.getLogger(__name__)

from agents.ceo_llm import (
    BASE_BUDGET_VERT, BASE_BUDGET_ORANGE, BUDGET_ROUGE,
    BUDGET_DECAY_WEEKLY, MIN_BUDGET_VERT, EMERGENCY_ORANGE_LIMIT,
)


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
            "semaines_0rev": 0, "last_0rev_increment": "", "emergency_stop": False,
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
                logger.critical("EMERGENCY STOP ACTIVE — trop de depenses sans revenu")
        # Memory rotation — garder les listes a taille raisonnable
        self._trim()
        try:
            with open(self._path, "w") as f:
                json.dump(self._data, f, indent=2, default=str)
        except Exception as e:
            logger.error("Save error: %s", e)

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
            logger.info("Memory trimmed (rotation)")

    def check_emergency_stop(self) -> bool:
        """Si >5 decisions orange sans revenu, STOP tout."""
        if self._data.get("revenue_usd", 0) > 0:
            self._data["emergency_stop"] = False
            return False
        orange = [d for d in self._data.get("decisions", []) if d.get("level") == "orange"]
        if len(orange) > EMERGENCY_ORANGE_LIMIT:
            return True
        if self._data.get("spent_sol", 0) > 2.0 and self._data.get("revenue_usd", 0) == 0:
            return True  # Only stop if spent >2 SOL with zero revenue
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
        logger.warning("Agent %s DISABLED: %s", agent_name, reason)

    def enable_agent(self, agent_name: str):
        """Reactive un sous-agent."""
        disabled = self._data.get("disabled_agents", {})
        if agent_name.upper() in disabled:
            del disabled[agent_name.upper()]
            self.save()
            logger.info("Agent %s RE-ENABLED", agent_name)

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
                logger.info("AUTO-LEARN: nouvelle regle creee pour %s", source)
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
                    logger.warning("AUTO-DISABLE: %s desactive apres %s echecs", mapped, existing["count"])
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
        self.save()

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
            self._data["last_0rev_increment"] = ""
            self._data["budget_vert"] = BASE_BUDGET_VERT
            self._data["budget_orange"] = BASE_BUDGET_ORANGE
        else:
            # Only increment semaines_0rev once per 7 days (not every 3h loop)
            last_inc = self._data.get("last_0rev_increment", "")
            now = datetime.utcnow()
            should_increment = True
            if last_inc:
                try:
                    last_dt = datetime.fromisoformat(last_inc)
                    if (now - last_dt).total_seconds() < 7 * 86400:
                        should_increment = False
                except Exception:
                    pass
            if should_increment:
                self._data["semaines_0rev"] += 1
                self._data["last_0rev_increment"] = now.isoformat()
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
        logger.info("Compaction decisions...")
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
                logger.info("Compaction OK — %s lecons, historique purge", len(lecons))
        except Exception as e:
            logger.error("Compaction error: %s", e)

    async def summarize_old_data(self, summarize_fn):
        """Transforme les conversations en 'Tendances Utilisateurs'."""
        convs = self._data.get("conversations", [])
        if len(convs) < 100:
            return
        logger.info("Summarize conversations...")
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
                logger.info("Tendances OK — conversations purgees (garde 20)")
        except Exception as e:
            logger.error("Summarize error: %s", e)
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
    "swap_tokens": "/api/public/crypto/tokens",
    "stocks": "/api/public/stocks",
    "gpu_tiers": "/api/public/gpu/tiers",
    "docs": "/api/public/docs",
    "defi": "/api/public/defi/best-yield?asset=USDC&limit=1",
    "sentiment": "/api/public/sentiment?token=BTC",
    "fear_greed": "/api/public/fear-greed",
    "mcp": "/mcp/",
    "docs_html": "/docs-html",
}

