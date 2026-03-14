"""MAXIA Scheduler V12 — Planificateur avec CEO Agent + tous les sous-systemes"""
import asyncio, time
from alerts import alert_system


class Scheduler:
    def __init__(self):
        self._tasks: list = []
        self._running = False
        print("[Scheduler] V12 initialise (avec CEO Agent)")

    async def run(self, brain, growth_agent, agent_worker, db=None):
        self._running = True
        print("[Scheduler] Demarrage de tous les agents V12...")

        # Importer le CEO
        try:
            from ceo_maxia import ceo
            ceo_available = True
            print("[Scheduler] CEO MAXIA charge")
        except Exception as e:
            ceo_available = False
            print(f"[Scheduler] CEO MAXIA non disponible: {e}")

        tasks = [
            asyncio.create_task(brain.run(db)),
            asyncio.create_task(growth_agent.run()),
            asyncio.create_task(agent_worker.run()),
            asyncio.create_task(run_discord_bot()),
            asyncio.create_task(run_telegram_bot()),
            asyncio.create_task(run_monitor_loop()),
            asyncio.create_task(self._health_monitor(brain, growth_agent)),
        ]

        # Ajouter le CEO si disponible
        if ceo_available:
            tasks.append(asyncio.create_task(ceo.run()))

        self._tasks = tasks

        startup_msg = (
            "Tous les agents sont actifs :\n"
            "🧠 Cerveau (pricing dynamique + scale-out)\n"
            "🎯 Marketing ultra-cible (10/jour, 7 profils)\n"
            "🤖 Worker IA (Groq LLaMA 3.3)\n"
        )
        if ceo_available:
            startup_msg += (
                "👔 CEO MAXIA (4 boucles, 11 agents, 3 cerveaux)\n"
                "   Objectif: 10 000 euros/mois | Moins cher partout"
            )

        await alert_system("MAXIA V12 Systeme demarre", startup_msg)

        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError:
            pass

    def stop(self):
        self._running = False
        for t in self._tasks:
            t.cancel()

    async def _health_monitor(self, brain, growth_agent):
        while self._running:
            try:
                bs = brain.get_stats()
                gs = growth_agent.get_stats()
                dp = bs.get("dynamic_pricing", {})
                so = bs.get("scale_out", {})

                # CEO status
                ceo_status = ""
                try:
                    from ceo_maxia import ceo
                    cs = ceo.get_status()
                    ceo_status = (
                        f" | CEO:cycle#{cs['cycle']}"
                        f" rev:${cs['stats']['revenue']}"
                        f" clients:{cs['stats']['clients']}"
                    )
                except Exception:
                    pass

                print(
                    f"[Scheduler] Brain:{bs['tier']} | "
                    f"Prospects:{gs['prospects_today']}/{gs['max_per_day']} | "
                    f"Budget:{bs['budget_remaining']:.0f}$ | "
                    f"Pricing adj:{dp.get('adjustment_bps', 0)}bps | "
                    f"Workers:{so.get('active_workers', 0)} | "
                    f"Up:{bs['uptime_human']}{ceo_status}"
                )
            except Exception as e:
                print(f"[Scheduler] Health err: {e}")
            await asyncio.sleep(300)


scheduler = Scheduler()
