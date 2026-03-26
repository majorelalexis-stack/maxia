"""MAXIA Scheduler V12 — Planificateur avec CEO Agent + tous les sous-systemes"""
import asyncio, time
from alerts import alert_system

# Imports avec fallback si module absent
async def _noop(): pass

try:
    from discord_bot import run_discord_bot
except ImportError:
    run_discord_bot = _noop

try:
    from telegram_bot import run_telegram_bot
except ImportError:
    run_telegram_bot = _noop

try:
    from wallet_monitor import run_monitor_loop
except ImportError:
    run_monitor_loop = _noop

try:
    from twitter_bot import run_twitter_bot
except ImportError:
    run_twitter_bot = _noop

try:
    from reddit_bot import run_reddit_bot
except ImportError:
    run_reddit_bot = _noop

try:
    from agent_outreach import run_outreach_bot
except ImportError:
    run_outreach_bot = _noop


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
            asyncio.create_task(run_twitter_bot()),
            asyncio.create_task(run_reddit_bot()),
            asyncio.create_task(run_outreach_bot()),
            asyncio.create_task(run_monitor_loop()),
            asyncio.create_task(self._health_monitor(brain, growth_agent)),
        ]

        # Ajouter le CEO si disponible
        if ceo_available:
            tasks.append(asyncio.create_task(ceo.run()))

        # V13: Background tasks (PoD liveness, leaderboard, SLA, auctions)
        tasks.append(asyncio.create_task(self._v13_background_loop()))

        self._tasks = tasks

        startup_msg = (
            "Tous les agents sont actifs :\n"
            "🧠 Cerveau (pricing dynamique + scale-out)\n"
            "🎯 Marketing ultra-cible (10/jour, 7 profils)\n"
            "🤖 Worker IA (Groq LLaMA 3.3)\n"
        )
        if ceo_available:
            startup_msg += (
                "👔 CEO MAXIA (4 boucles, 17 agents, 3 cerveaux)\n"
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


    async def _v13_background_loop(self):
        """Boucle periodique V13 : PoD, leaderboard, SLA, auctions, OFAC."""
        _cycle = 0
        while self._running:
            try:
                _cycle += 1

                # Toutes les 5 min : check liveness des deliveries (PoD auto-confirm)
                try:
                    from proof_of_delivery import check_liveness_expirations
                    await check_liveness_expirations()
                except Exception as e:
                    if "No module" not in str(e):
                        print(f"[V13] PoD liveness error: {e}")

                # Toutes les 10 min : expirer les encheres inversees
                if _cycle % 2 == 0:
                    try:
                        from reverse_auction import expire_old_requests
                        await expire_old_requests()
                    except Exception as e:
                        if "No module" not in str(e):
                            print(f"[V13] Auction expire error: {e}")

                # Toutes les heures (12 cycles de 5 min) : recalcul scores + SLA
                if _cycle % 12 == 0:
                    try:
                        from agent_leaderboard import recalculate_all_scores
                        await recalculate_all_scores()
                        print("[V13] Leaderboard scores recalcules")
                    except Exception as e:
                        if "No module" not in str(e):
                            print(f"[V13] Leaderboard error: {e}")

                    try:
                        from sla_enforcer import enforce_sla_all
                        await enforce_sla_all()
                        print("[V13] SLA enforcement complete")
                    except Exception as e:
                        if "No module" not in str(e):
                            print(f"[V13] SLA error: {e}")

                    # Recalcul badges (toutes les heures)
                    try:
                        from referral import recalculate_badges
                        await recalculate_badges()
                        print("[V13+] Badges recalcules")
                    except Exception as e:
                        if "No module" not in str(e):
                            print(f"[V13+] Badges error: {e}")

                # Toutes les 24h (288 cycles) : refresh liste OFAC
                if _cycle % 288 == 0:
                    try:
                        from security import refresh_ofac_list
                        await refresh_ofac_list()
                        print("[V13] OFAC list refreshed")
                    except Exception as e:
                        if "No module" not in str(e):
                            print(f"[V13] OFAC refresh error: {e}")

            except Exception as e:
                print(f"[V13] Background loop error: {e}")

            await asyncio.sleep(300)  # 5 min


scheduler = Scheduler()
