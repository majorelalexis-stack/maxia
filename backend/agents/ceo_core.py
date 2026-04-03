"""CEO Core — CEOMaxia main class with 4 decision loops.

Loops: tactique (hourly), strategique (daily), vision (weekly), expansion (monthly).

Extracted from ceo_maxia.py (S34 split).
"""
import logging
import asyncio, json
from datetime import datetime, date

logger = logging.getLogger(__name__)

from agents.ceo_llm import (
    _call_groq, _call_anthropic, _pj,
    alert_rouge, alert_info,
    CEO_IDENTITY, URL, GROQ_MODEL, SONNET_MODEL, OPUS_MODEL,
    GROQ_API_KEY, ANTHROPIC_API_KEY, DISCORD_WEBHOOK_URL,
    llm_router, Tier,
    get_llm_costs,
)
from agents.ceo_memory import Memory
from agents.ceo_subagents import (
    watchdog_health_check, watchdog_self_heal,
    radar_scan, oracle_scan_trends,
    collect, execute,
    crisis_detect, crisis_respond,
    analytics_compute,
    partnership_scan, partnership_outreach,
    scout_scan_onchain_agents,
    micro_wallet, FAILOVER_RPC, _active_rpc_index, _rpc_failures,
    GITHUB_TOKEN, GITHUB_ORG, GITHUB_REPO,
    respond, ghost_write, testimonial_request, testimonial_process,
    web_designer_update_config, web_designer_deploy_config,
    deployer_create_and_deploy, deployer_blog_post,
    negotiator_evaluate, negotiator_bulk_deal,
    compliance_check_wallet, compliance_check_transaction,
)


class CEOMaxia:
    def __init__(self):
        self.memory = Memory()
        self.router = llm_router  # LLM Router pour optimiser les couts
        self._running = False
        self._cycle = 0
        self._last = {"strat": "", "vision": "", "expansion": ""}
        self._last_crisis_check = ""  # ISO date of last crisis detection run
        # Pre-seed mode: no revenue yet, product just launched — reduce crisis noise
        self._pre_seed_mode = (self.memory._data.get("revenue_usd", 0) == 0)
        # Scout: agents on-chain detectes et contacts
        self._scout_contacts = []
        self._onchain_agents = []
        logger.info("CEO MAXIA V5 SCOUT MODE initialise")
        logger.info("  Role: Scout (collecte metriques + scan agents + premier contact A2A)")
        logger.info("  Decisions: DELEGUEES au CEO Local (PC + GPU)")
        logger.info("  Router: %s", "actif" if self.router else "desactive (direct Groq/Claude)")
        logger.info("  Groq: %s", "actif" if GROQ_API_KEY else "MANQUANT")
        logger.info("  Anthropic: %s", "actif" if ANTHROPIC_API_KEY else "fallback Groq")
        logger.info("  Discord: %s", "actif" if DISCORD_WEBHOOK_URL else "absent")
        logger.info("  Budget: %.4f SOL", self.memory.get_budget_vert())
        logger.info("  Emergency: %s", "STOP" if self.memory.is_stopped() else "OK")
        logger.info("  Agents: SCOUT, WATCHDOG, RADAR, COMPLIANCE, ANALYTICS, CRISIS-MANAGER")

    async def run(self):
        self._running = True
        logger.info("CEO MAXIA Demarre — MODE SCOUT (collecte + scan + contact A2A)")
        logger.info("CEO MAXIA Strategie et outreach delegues au CEO Local (PC + GPU)")
        # Alerte Telegram une seule fois par jour (pas a chaque restart)
        _today = date.today().isoformat()
        if getattr(self, '_last_start_alert', '') != _today:
            self._last_start_alert = _today
            await alert_info("CEO VPS V5 SCOUT — collecte metriques + scan agents on-chain")

        from agents.agentops_integration import start_trace, end_trace, record_error as ao_record_error

        while self._running:
            self._cycle += 1
            now = datetime.utcnow()
            today = date.today().isoformat()

            try:
                # Mode minimal : monitoring, RADAR, CRISIS, ANALYTICS
                # PAS d'outreach social (delegue au CEO local avec browser-use)
                await self._run_tracked("tactique", self._tactique)

                # Strategique 1x/jour (20h UTC) — garder pour le pricing et scaling
                if now.hour == 20 and self._last["strat"] != today:
                    self._last["strat"] = today
                    await self._run_tracked("strategique", self._strategique)

                # Vision hebdo (dimanche 18h) — garder pour la retrospective
                if now.weekday() == 6 and now.hour == 18 and self._last["vision"] != today:
                    self._last["vision"] = today
                    await self._run_tracked("vision", self._vision)

                # Expansion mensuelle — garder
                if now.day == 1 and now.hour == 10 and self._last["expansion"] != today:
                    self._last["expansion"] = today
                    await self._run_tracked("expansion", self._expansion)

                await self._check_hunter()
                await self._check_errors()

            except Exception as e:
                logger.error("Error #%s: %s", self._cycle, e)
                ao_record_error(f"ceo_cycle_{self._cycle}", e)
            await asyncio.sleep(10800)  # 3 heures

    async def _run_tracked(self, loop_name: str, coro_fn):
        """Execute une boucle CEO avec trace AgentOps."""
        from agents.agentops_integration import start_trace, end_trace
        trace = start_trace(tags=["ceo", loop_name, f"cycle-{self._cycle}"])
        try:
            await coro_fn()
            end_trace(trace, "Success", f"{loop_name} cycle {self._cycle}")
        except Exception:
            end_trace(trace, "Fail", f"{loop_name} cycle {self._cycle} failed")
            raise

    def stop(self):
        self._running = False

    async def _opus_summarize(self, prompt: str, data: str) -> str:
        if self.router:
            return await self.router.call(
                f"{prompt}\n\nDATA:\n{data}",
                tier=Tier.STRATEGIC, system="Analyste expert. Reponds en JSON.", max_tokens=2000,
            )
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
        logger.info("%s", msg)
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
        logger.info("=== TACTIQUE #%s ===", self._cycle)
        
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
                logger.error("WATCHDOG health check error: %s", e)
        else:
            logger.info("WATCHDOG skipped (cycle %s, waiting for startup)", self._cycle)

        data = await collect()
        self.memory.log_kpi(data)

        # RADAR scan (on-chain)
        radar = await radar_scan(self.memory)

        # SCOUT — scan agents on-chain pour le CEO local
        try:
            self._onchain_agents = await scout_scan_onchain_agents(self.memory)
            if self._onchain_agents:
                logger.info("SCOUT %s agents on-chain detectes", len(self._onchain_agents))
                self.memory.update_agent("SCOUT", {
                    "status": "actif",
                    "agents_detected": len(self._onchain_agents),
                    "contacts_sent": len(self._scout_contacts),
                })
        except Exception as e:
            logger.error("SCOUT scan error: %s", e)

        # ORACLE scan (off-chain — social listening)
        oracle_trends = await oracle_scan_trends(self.memory)

        # Vector Memory — indexer les nouvelles decisions
        try:
            from agents.ceo_vector_memory import vector_memory
            for dec in self.memory._data.get("decisions", [])[-3:]:
                vector_memory.store_decision(dec)
            for conv in self.memory._data.get("conversations", [])[-3:]:
                vector_memory.store_conversation(conv)
        except Exception:
            pass

        # MICRO wallet status
        micro_stats = micro_wallet.get_stats()

        # RADAR auto-actions : si tendance detectee, agir immediatement
        # Twitter est DELEGUE au CEO local (Playwright) — VPS ne tweete plus
        for alert in radar:
            if alert.get("type") == "price_spike":
                token = alert.get("token", "")
                logger.info("RADAR spike %s — blog auto (Twitter delegue au CEO local)", token)
                self.memory.log_decision("vert", f"RADAR spike {token} — blog only, Twitter delegue au local", "RADAR", "GHOST-WRITER")
                # Blog post si c'est une categorie entiere
                if alert.get("category"):
                    await self.deploy_blog(
                        f"{alert['category'].upper()} Tokens Are Trending",
                        f"Analysis of why {alert['category']} tokens surged {alert.get('change',0):.0%} and how to trade them on MAXIA.",
                    )
            elif alert.get("type") == "category_surge":
                cat = alert.get("category", "")
                logger.info("RADAR surge %s — blog auto (Twitter delegue au CEO local)", cat)
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
        # In pre-seed mode (no revenue yet), only run crisis detection once per day
        # instead of every 3h loop to avoid P2 spam
        today = date.today().isoformat()
        run_crisis = True
        if self._pre_seed_mode and self._last_crisis_check == today:
            run_crisis = False
            logger.info("CRISIS-MANAGER skipped (pre-seed mode, already checked today)")

        crises = []
        if run_crisis:
            try:
                crises = await crisis_detect(self.memory, skip_health=(self._cycle < 3))
                self._last_crisis_check = today
                for crisis in crises:
                    logger.warning("CRISIS %s: %s — %s", crisis["level"], crisis["type"], crisis["details"][:80])
                    await crisis_respond(crisis, self.memory)
                self.memory.update_agent("CRISIS-MANAGER", {"status": "actif", "active_crises": len(crises)})
            except Exception as e:
                logger.error("CRISIS-MANAGER error: %s", e)
                self.memory.update_agent("CRISIS-MANAGER", {"status": "erreur", "error": "An error occurred"[:80]})
            # Update pre-seed mode — exit it once revenue appears
            if self.memory._data.get("revenue_usd", 0) > 0:
                self._pre_seed_mode = False

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
                logger.info("ANALYTICS: %s", rec)
        except Exception as e:
            logger.error("ANALYTICS error: %s", e)
            self.memory.update_agent("ANALYTICS", {"status": "erreur"})

        # PARTNERSHIP — scan partenaires (tous les 6 cycles = ~18h)
        if self._cycle % 6 == 0:
            try:
                opportunities = await partnership_scan(self.memory)
                if opportunities:
                    top = opportunities[0]
                    logger.info("PARTNERSHIP: top opportunity = %s (%s, score %s)", top["partner"], top["category"], top["score"])
                    # Auto-outreach si score >= 80
                    if top["score"] >= 80:
                        await partnership_outreach(top["partner"], top["category"], top["pitch"], self.memory)
                self.memory.update_agent("PARTNERSHIP", {"status": "actif", "opportunities": len(opportunities)})
            except Exception as e:
                logger.error("PARTNERSHIP error: %s", e)
                self.memory.update_agent("PARTNERSHIP", {"status": "erreur"})

        # NEGOTIATOR + COMPLIANCE — stats
        self.memory.update_agent("NEGOTIATOR", {"status": "actif", "mode": "auto"})
        self.memory.update_agent("COMPLIANCE", {
            "status": "actif",
            "blocked_wallets": len(self.memory._data.get("compliance_blocked", [])),
        })

        # SCOUT stats
        try:
            from agents.scout_agent import scout_agent as _scout
            self.memory.update_agent("SCOUT", _scout.get_stats())
        except Exception:
            self.memory.update_agent("SCOUT", {"status": "non-demarre"})

        # ── BUS CONSUMPTION — traiter les messages inter-agents ──
        from agents.ceo_maxia import agent_bus
        for agent_name in ["HUNTER", "NEGOTIATOR", "TESTIMONIAL", "RESPONDER", "GHOST-WRITER"]:
            msgs = agent_bus.get_messages(agent_name)
            for msg in msgs:
                msg_type = msg.get("type", "")
                msg_data = msg.get("data", {})
                # HUNTER recoit des ordres d'intensification
                if agent_name == "HUNTER" and msg_type == "intensify":
                    self.memory.update_agent("HUNTER", {"intensify": True, "reason": msg_data.get("reason", "")})
                elif agent_name == "HUNTER" and msg_type == "low_conversion":
                    logger.info("BUS->HUNTER: low conversion (%s)", msg_data.get("rate"))
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
            logger.info("BUS ACTION: HUNTER intensify -> switch to %s", nxt)

        # NEGOTIATOR : si promo_mode, creer un A/B test promo automatiquement
        nego_data = self.memory._data.get("agents", {}).get("NEGOTIATOR", {})
        if nego_data.get("promo_mode") and not self.memory._data.get("ab_tests", {}).get("promo_zero_fee"):
            self.memory.create_ab_test("promo_zero_fee",
                "0% fees for 7 days — bring your AI agent, earn USDC.",
                "First 10 trades free. AI agents earn USDC on MAXIA.")
            nego_data["promo_mode"] = False
            logger.info("BUS ACTION: NEGOTIATOR created promo A/B test")

        # NEGOTIATOR : si bundle_mode, log recommandation
        if nego_data.get("bundle_mode"):
            self.memory.add_regle(f"LTV faible ({nego_data.get('ltv', 0)}) — NEGOTIATOR doit proposer des bundles")
            nego_data["bundle_mode"] = False

        # TESTIMONIAL : si churn_alert, generer contenu retention
        testi_data = self.memory._data.get("agents", {}).get("TESTIMONIAL", {})
        if testi_data.get("churn_alert"):
            try:
                # Twitter delegue au CEO local — VPS ne tweete plus
                # On log l'alerte churn pour que le CEO local puisse agir
                self.memory.log_decision("vert", "Churn alert — retention tweet delegue au CEO local", "TESTIMONIAL", "GHOST-WRITER")
                testi_data["churn_alert"] = False
                logger.info("BUS ACTION: TESTIMONIAL churn alert logged (Twitter delegue au CEO local)")
            except Exception as e:
                logger.error("BUS ACTION TESTIMONIAL error: %s", e)

        # RESPONDER : si retention_mode, activer reponse proactive
        resp_data = self.memory._data.get("agents", {}).get("RESPONDER", {})
        if resp_data.get("retention_mode"):
            self.memory.add_regle("Retention mode actif — RESPONDER doit etre plus proactif et offrir des discounts")
            resp_data["retention_mode"] = False
            logger.info("BUS ACTION: RESPONDER retention mode activated")

        # RAG — rechercher le contexte pertinent
        rag_context = ""
        try:
            from agents.ceo_vector_memory import vector_memory
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
            "IMPORTANT — action DOIT etre une directive CONCRETE et EXECUTABLE (ex: 'blog: MAXIA fees reduced', 'switch canal discord', 'contact wallet Xyz'). PAS de tweet (Twitter delegue au CEO local). "
            "PAS de phrases vagues comme 'maximiser les chances de succes' ou 'ameliorer la visibilite'."
        )
        # Router: tier FAST pour la generation de decisions tactiques
        if self.router:
            result = _pj(await self.router.call(
                f"CONTEXTE:\n{ctx}\n\n{q}",
                tier=Tier.FAST, system=CEO_IDENTITY, max_tokens=1500,
            ))
        else:
            result = _pj(await _call_groq(CEO_IDENTITY, f"CONTEXTE:\n{ctx}\n\n{q}"))
        if result:
            await execute(result.get("decisions", []), self.memory)
            for r in result.get("regles_apprises", []):
                self.memory.add_regle(r)

    # ── Boucle 2 : STRATEGIQUE + Red Teaming ──

    async def _strategique(self):
        logger.info("=== STRATEGIQUE + RED TEAM ===")
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
            "IMPORTANT — decisions[].action DOIT etre CONCRETE (ex: 'blog: ...', 'switch canal discord', 'baisser prix swap'). PAS de phrases vagues.\n"
            "IMPORTANT — Twitter est DELEGUE au CEO local. Ne JAMAIS proposer de tweet. Actions VPS uniquement: blog, prix, prospection on-chain, monitoring."
        )
        # Router: tier MID pour le SWOT, STRATEGIC seulement pour red teaming
        if self.router:
            result = _pj(await self.router.call(
                f"CONTEXTE:\n{ctx}\n\n{q}",
                tier=Tier.MID, system=CEO_IDENTITY, max_tokens=2000,
            ))
        else:
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
            logger.error("ANALYTICS strategique error: %s", e)

    # ── Boucle 3 : VISION + Compaction ──

    async def _vision(self):
        logger.info("=== VISION + RETROSPECTIVE ===")
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
        # Router: tier STRATEGIC pour vision (inchange — c'est hebdo)
        if self.router:
            result = _pj(await self.router.call(
                f"CONTEXTE:\n{ctx}\n\n{q}",
                tier=Tier.STRATEGIC, system=CEO_IDENTITY, max_tokens=4000,
            ))
        else:
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
            logger.info("WEB-DESIGNER: config.json deploye")
        except Exception as e:
            logger.error("WEB-DESIGNER error: %s", e)

        # ANALYTICS rapport hebdomadaire (dimanche = boucle vision)
        try:
            report = await analytics_weekly_report(self.memory)
            if report.get("message_fondateur"):
                await alert_info(f"ANALYTICS HEBDO: {report['message_fondateur'][:300]}")
            logger.info("ANALYTICS: rapport hebdo genere (health=%s)", report.get("metrics", {}).get("health_score", "?"))
        except Exception as e:
            logger.error("ANALYTICS weekly error: %s", e)

        # PARTNERSHIP scan hebdo — identifier les top partenaires
        try:
            opportunities = await partnership_scan(self.memory)
            if opportunities:
                top3 = [f"{o['partner']} ({o['score']})" for o in opportunities[:3]]
                logger.info("PARTNERSHIP hebdo: top3 = %s", ", ".join(top3))
        except Exception as e:
            logger.error("PARTNERSHIP weekly error: %s", e)

        # Auto-deploy pages
        await self.auto_deploy_check()
        # Compaction memoire
        await self.memory.compact(self._opus_summarize)
        await self.memory.summarize_old_data(self._opus_summarize)

    # ── Boucle 4 : EXPANSION ──

    async def _expansion(self):
        logger.info("=== EXPANSION ===")
        ctx = self.memory.ctx("expansion")
        q = (
            "Marche mondial, concurrents, geographie, langues, chains, partenariats, financement.\n"
            "Phases : actuelle -> suivante -> finale. Objectif, strategie, cout, timeline.\n"
            "JSON: {reflexion, marche, concurrents, expansion_plan, nouvelle_langue, nouvelle_chain, "
            "partenariats_cibles, financement, nouveau_produit_mondial, decisions, regles_apprises, message_fondateur}\n"
            "IMPORTANT — decisions[].cible DOIT etre un de : GHOST-WRITER, HUNTER, SCOUT, WATCHDOG, SOL-TREASURY, RESPONDER, RADAR, TESTIMONIAL, DEPLOYER, NEGOTIATOR, COMPLIANCE, PARTNERSHIP, ANALYTICS, CRISIS-MANAGER, FONDATEUR.\n"
            "IMPORTANT — decisions[].action DOIT etre CONCRETE. PAS de phrases vagues."
        )
        # Router: tier STRATEGIC pour expansion (inchange — c'est mensuel)
        if self.router:
            result = _pj(await self.router.call(
                f"CONTEXTE:\n{ctx}\n\n{q}",
                tier=Tier.STRATEGIC, system=CEO_IDENTITY, max_tokens=5000,
            ))
        else:
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
        logger.info("Emergency stop desactive")

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
        from agents.ceo_maxia import agent_bus
        d = self.memory._data
        return {
            "name": "CEO MAXIA V4",
            "running": self._running, "cycle": self._cycle,
            "emergency_stop": d.get("emergency_stop", False),
            "cerveaux": {
                "tactique": "Router:FAST" if self.router else "Groq (gratuit)",
                "strategique": "Router:MID" if self.router else f"Sonnet ({'actif' if ANTHROPIC_API_KEY else 'Groq'})",
                "vision": "Router:STRATEGIC" if self.router else f"Opus ({'actif' if ANTHROPIC_API_KEY else 'Groq'})",
                "expansion": "Router:STRATEGIC" if self.router else f"Opus ({'actif' if ANTHROPIC_API_KEY else 'Groq'})",
            },
            "router_stats": self.router.get_stats() if self.router else None,
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


