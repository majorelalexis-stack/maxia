"""MAXIA Scheduler V12 — Planificateur avec CEO Agent + tous les sous-systemes"""
import logging
import asyncio, time
from alerts import alert_system

logger = logging.getLogger(__name__)


def _safe_task(coro, name: str):
    """Wrap une coroutine pour qu'un crash n'affecte pas les autres tasks."""
    async def _wrapper():
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("Task '%s' crashed: %s — autres tasks continuent", name, e)
    return _wrapper()

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
        logger.info("V12 initialise (avec CEO Agent)")

    async def run(self, brain, growth_agent, agent_worker, db=None):
        self._running = True
        logger.info("Demarrage de tous les agents V12...")

        # Importer le CEO
        try:
            from ceo_maxia import ceo
            ceo_available = True
            logger.info("CEO MAXIA charge")
        except Exception as e:
            ceo_available = False
            logger.warning("CEO MAXIA non disponible: %s", e)

        tasks = [
            asyncio.create_task(_safe_task(brain.run(db), "brain")),
            asyncio.create_task(_safe_task(growth_agent.run(), "growth_agent")),
            asyncio.create_task(_safe_task(agent_worker.run(), "agent_worker")),
            asyncio.create_task(_safe_task(run_discord_bot(), "discord_bot")),
            asyncio.create_task(_safe_task(run_telegram_bot(), "telegram_bot")),
            asyncio.create_task(_safe_task(run_twitter_bot(), "twitter_bot")),
            asyncio.create_task(_safe_task(run_reddit_bot(), "reddit_bot")),
            asyncio.create_task(_safe_task(run_outreach_bot(), "outreach_bot")),
            asyncio.create_task(_safe_task(run_monitor_loop(), "monitor_loop")),
            asyncio.create_task(self._health_monitor(brain, growth_agent)),
        ]

        # Ajouter le CEO si disponible
        if ceo_available:
            tasks.append(asyncio.create_task(_safe_task(ceo.run(), "ceo")))

        # V13: Background tasks (PoD liveness, leaderboard, SLA, auctions)
        tasks.append(asyncio.create_task(_safe_task(self._v13_background_loop(), "v13_background")))

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

                logger.info(
                    "Brain:%s | Prospects:%s/%s | Budget:%.0f$ | "
                    "Pricing adj:%sbps | Workers:%s | Up:%s%s",
                    bs['tier'], gs['prospects_today'], gs['max_per_day'],
                    bs['budget_remaining'], dp.get('adjustment_bps', 0),
                    so.get('active_workers', 0), bs['uptime_human'], ceo_status
                )
            except Exception as e:
                logger.error("Health err: %s", e)
            await asyncio.sleep(300)


    async def _v13_background_loop(self):
        """Boucle periodique V13 : PoD, leaderboard, SLA, auctions, OFAC."""
        _cycle = 0
        while self._running:
            try:
                _cycle += 1

                # Toutes les 5 min : snapshot RPC stats pour /status/history
                try:
                    from chain_resilience import snapshot_chain_stats
                    snapshot_chain_stats()
                except Exception:
                    pass

                # Toutes les 5 min : check staleness oracle Pyth (alerte Telegram si >5min)
                try:
                    from pyth_oracle import check_oracle_health_alert
                    await check_oracle_health_alert()
                except Exception as e:
                    if "No module" not in str(e):
                        logger.error("[V13] Oracle health alert error: %s", e)

                # Toutes les 5 min : check liveness des deliveries (PoD auto-confirm)
                try:
                    from proof_of_delivery import check_liveness_expirations
                    await check_liveness_expirations()
                except Exception as e:
                    if "No module" not in str(e):
                        logger.error("[V13] PoD liveness error: %s", e)

                # Toutes les 10 min : expirer les encheres inversees
                if _cycle % 2 == 0:
                    try:
                        from reverse_auction import expire_old_requests
                        await expire_old_requests()
                    except Exception as e:
                        if "No module" not in str(e):
                            logger.error("[V13] Auction expire error: %s", e)

                # Toutes les heures (12 cycles de 5 min) : recalcul scores + SLA
                if _cycle % 12 == 0:
                    try:
                        from agent_leaderboard import recalculate_all_scores
                        await recalculate_all_scores()
                        logger.info("[V13] Leaderboard scores recalcules")
                    except Exception as e:
                        if "No module" not in str(e):
                            logger.error("[V13] Leaderboard error: %s", e)

                    try:
                        from sla_enforcer import enforce_sla_all
                        await enforce_sla_all()
                        logger.info("[V13] SLA enforcement complete")
                    except Exception as e:
                        if "No module" not in str(e):
                            logger.error("[V13] SLA error: %s", e)

                    # Recalcul badges (toutes les heures)
                    try:
                        from referral import recalculate_badges
                        await recalculate_badges()
                        logger.info("[V13+] Badges recalcules")
                    except Exception as e:
                        if "No module" not in str(e):
                            logger.error("[V13+] Badges error: %s", e)

                # Toutes les 24h (288 cycles) : refresh liste OFAC
                if _cycle % 288 == 0:
                    try:
                        from security import refresh_ofac_list
                        await refresh_ofac_list()
                        logger.info("[V13] OFAC list refreshed")
                    except Exception as e:
                        if "No module" not in str(e):
                            logger.error("[V13] OFAC refresh error: %s", e)

            except Exception as e:
                logger.error("[V13] Background loop error: %s", e)

            await asyncio.sleep(300)  # 5 min


scheduler = Scheduler()
