"""MAXIA Scheduler V12 — Planificateur avec CEO Agent + tous les sous-systemes"""
import logging
import asyncio, time
from infra.alerts import alert_system

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

# Bots removed (Plan CEO V4: CEO = local only)
# telegram_bot, discord_bot, twitter_bot, agent_outreach: fichiers supprimes

try:
    from features.wallet_monitor import run_monitor_loop
except ImportError:
    run_monitor_loop = _noop

try:
    from integrations.reddit_bot import run_reddit_bot
except ImportError:
    run_reddit_bot = _noop


class Scheduler:
    def __init__(self):
        self._tasks: list = []
        self._running = False
        logger.info("V12 initialise (avec CEO Agent)")

    async def run(self, brain, growth_agent, agent_worker, db=None):
        self._running = True
        logger.info("Demarrage de tous les agents V12...")

        # CEO, growth_agent, discord, telegram, twitter, outreach: REMOVED (Plan CEO V4)
        tasks = [
            asyncio.create_task(_safe_task(brain.run(db), "brain")),
            asyncio.create_task(_safe_task(agent_worker.run(), "agent_worker")),
            asyncio.create_task(_safe_task(run_reddit_bot(), "reddit_bot")),
            asyncio.create_task(_safe_task(run_monitor_loop(), "monitor_loop")),
            asyncio.create_task(self._health_monitor(brain)),
        ]

        # V13: Background tasks (PoD liveness, leaderboard, SLA, auctions)
        tasks.append(asyncio.create_task(_safe_task(self._v13_background_loop(), "v13_background")))

        self._tasks = tasks

        startup_msg = (
            "Agents VPS actifs :\n"
            "Brain (pricing dynamique + scale-out)\n"
            "Worker IA (Groq LLaMA 3.3)\n"
            "CEO = local only (Plan CEO V4)\n"
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

    async def _health_monitor(self, brain):
        while self._running:
            try:
                bs = brain.get_stats()
                dp = bs.get("dynamic_pricing", {})
                so = bs.get("scale_out", {})
                logger.info(
                    "Brain:%s | Budget:%.0f$ | Pricing adj:%sbps | Workers:%s | Up:%s",
                    bs['tier'], bs['budget_remaining'], dp.get('adjustment_bps', 0),
                    so.get('active_workers', 0), bs['uptime_human']
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
                    from blockchain.chain_resilience import snapshot_chain_stats
                    snapshot_chain_stats()
                except Exception:
                    pass

                # Toutes les 5 min : check staleness oracle Pyth (alerte Telegram si >5min)
                try:
                    from trading.pyth_oracle import check_oracle_health_alert
                    await check_oracle_health_alert()
                except Exception as e:
                    if "No module" not in str(e):
                        logger.error("[V13] Oracle health alert error: %s", e)

                # Toutes les 5 min : check liveness des deliveries (PoD auto-confirm)
                try:
                    from features.proof_of_delivery import check_liveness_expirations
                    await check_liveness_expirations()
                except Exception as e:
                    if "No module" not in str(e):
                        logger.error("[V13] PoD liveness error: %s", e)

                # Toutes les 10 min : expirer les encheres inversees
                if _cycle % 2 == 0:
                    try:
                        from marketplace.reverse_auction import expire_old_requests
                        await expire_old_requests()
                    except Exception as e:
                        if "No module" not in str(e):
                            logger.error("[V13] Auction expire error: %s", e)

                # Toutes les heures (12 cycles de 5 min) : recalcul scores + SLA
                if _cycle % 12 == 0:
                    try:
                        from agents.agent_leaderboard import recalculate_all_scores
                        await recalculate_all_scores()
                        logger.info("[V13] Leaderboard scores recalcules")
                    except Exception as e:
                        if "No module" not in str(e):
                            logger.error("[V13] Leaderboard error: %s", e)

                    try:
                        from enterprise.sla_enforcer import enforce_sla_all
                        await enforce_sla_all()
                        logger.info("[V13] SLA enforcement complete")
                    except Exception as e:
                        if "No module" not in str(e):
                            logger.error("[V13] SLA error: %s", e)

                    # Recalcul badges (toutes les heures)
                    try:
                        from billing.referral import recalculate_badges
                        await recalculate_badges()
                        logger.info("[V13+] Badges recalcules")
                    except Exception as e:
                        if "No module" not in str(e):
                            logger.error("[V13+] Badges error: %s", e)

                # Toutes les heures (12 cycles) : check credit Akash
                if _cycle % 12 == 0:
                    try:
                        from gpu.akash_client import akash as _akash
                        credit = await _akash.get_credit_balance()
                        bal = credit.get("balance_usd", -1)
                        if bal >= 0:
                            if bal < 1:
                                await alert_system(
                                    "AKASH CREDIT CRITIQUE",
                                    f"Solde Akash: ${bal:.2f} — GPU rental IMPOSSIBLE. Recharger immediatement."
                                )
                                logger.warning("[Akash] Credit CRITIQUE: $%.2f", bal)
                            elif bal < 5:
                                await alert_system(
                                    "Akash credit bas",
                                    f"Solde Akash: ${bal:.2f} — recharger bientot."
                                )
                                logger.warning("[Akash] Credit bas: $%.2f", bal)
                            else:
                                logger.info("[Akash] Credit OK: $%.2f", bal)
                    except Exception as e:
                        if "No module" not in str(e):
                            logger.error("[V13] Akash credit check error: %s", e)

                # Toutes les heures : auto-close governance expirees
                if _cycle % 12 == 0:
                    try:
                        from features.governance import auto_close_expired
                        closed = await auto_close_expired()
                        if closed:
                            logger.info("[Governance] %d propositions auto-cloturees", closed)
                    except Exception as e:
                        if "No module" not in str(e):
                            logger.error("[Governance] Auto-close error: %s", e)

                # Toutes les semaines (2016 cycles de 5 min) : newsletter digest
                if _cycle % 2016 == 0:
                    try:
                        from integrations.newsletter import generate_weekly_digest
                        digest = await generate_weekly_digest()
                        logger.info("[Newsletter] Digest genere: %s", digest.get("title", ""))
                    except Exception as e:
                        if "No module" not in str(e):
                            logger.error("[Newsletter] Digest error: %s", e)

                # CEO auto-blog — REMOVED (Plan CEO V4)

                # Toutes les 24h (288 cycles) : refresh fallback prices
                if _cycle % 288 == 0:
                    try:
                        from trading.price_oracle import refresh_fallback_prices
                        updated = await refresh_fallback_prices()
                        logger.info("[V13] Fallback prices refreshed: %d live", updated)
                    except Exception as e:
                        if "No module" not in str(e):
                            logger.error("[V13] Fallback price refresh error: %s", e)

                # Toutes les 24h (288 cycles) : refresh liste OFAC
                if _cycle % 288 == 0:
                    try:
                        from core.security import refresh_ofac_list
                        await refresh_ofac_list()
                        logger.info("[V13] OFAC list refreshed")
                    except Exception as e:
                        if "No module" not in str(e):
                            logger.error("[V13] OFAC refresh error: %s", e)

            except Exception as e:
                logger.error("[V13] Background loop error: %s", e)

            await asyncio.sleep(300)  # 5 min


scheduler = Scheduler()
