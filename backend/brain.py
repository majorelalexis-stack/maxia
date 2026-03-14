"""MAXIA Brain V11 — Cerveau central avec Dynamic Pricing + Scale-Out"""
import asyncio, time
from config import GROWTH_MONTHLY_BUDGET, GROWTH_RESERVE_ALERT
from alerts import alert_system, alert_low_balance, alert_daily_report
from dynamic_pricing import adjust_market_fees, get_pricing_status
from scale_out import scale_out_manager


class Brain:
    """
    Cerveau V11 — coordonne tous les agents et sous-systemes.
    - Dynamic Pricing: ajuste les commissions en temps reel
    - Scale-Out: deploie des workers si surcharge
    - Budget: gere tiers survie/operationnel/croissance
    - Reporting: rapport quotidien Discord
    """

    def __init__(self):
        self._started_at = 0
        self._running = False
        self._tier = "survival"
        self._monthly_spend = 0.0
        self._monthly_revenue = 0.0
        self._db = None
        print("[Brain] Orchestrateur V11 initialise (Dynamic Pricing + Scale-Out)")

    async def run(self, db=None):
        self._running = True
        self._started_at = int(time.time())
        self._db = db
        print("[Brain] Cerveau V12 demarre — mode survie")
        await alert_system("🧠 Cerveau MAXIA V12 demarre", "Pricing dynamique + Scale-Out actifs\nMode : survie (beta)")

        while self._running:
            try:
                await self._tick()
            except Exception as e:
                print(f"[Brain] Erreur: {e}")
            await asyncio.sleep(60)

    def stop(self):
        self._running = False

    async def _tick(self):
        self._update_tier()

        # V11: Dynamic Pricing toutes les 5 min
        if self._db:
            await adjust_market_fees(self._db)

        # V11: Verifier la charge pour scale-out
        queue_size = await self._get_queue_size()
        if queue_size > 0:
            await scale_out_manager.check_and_scale(queue_size)

        # Rapport quotidien a 23h
        await self._check_daily_report()

    async def _get_queue_size(self) -> int:
        if self._db is None or self._db._db is None:
            return 0
        try:
            async with self._db._db.execute(
                "SELECT COUNT(*) as cnt FROM commands WHERE json_extract(data,'$.status')='pending'"
            ) as c:
                row = await c.fetchone()
            return int(row[0]) if row else 0
        except Exception:
            return 0

    def _update_tier(self):
        if self._monthly_revenue > 500:
            if self._tier != "growth":
                self._tier = "growth"
                print("[Brain] Upgrade -> CROISSANCE (500+ USDC/mois)")
        elif self._monthly_revenue > 100:
            if self._tier != "operational":
                self._tier = "operational"
                print("[Brain] Upgrade -> OPERATIONNEL (250 USDC/mois)")
        else:
            self._tier = "survival"

    async def _check_daily_report(self):
        now = time.localtime()
        if now.tm_hour == 23 and now.tm_min == 0:
            stats = self.get_stats()
            await alert_daily_report(stats)

    def record_revenue(self, amount: float):
        self._monthly_revenue += amount

    def record_spend(self, amount: float):
        self._monthly_spend += amount

    def get_budget_remaining(self) -> float:
        limits = {"survival": 100, "operational": 250, "growth": 500}
        return max(0, limits.get(self._tier, 100) - self._monthly_spend)

    def can_spend(self, amount: float) -> bool:
        return amount <= self.get_budget_remaining()

    def get_stats(self) -> dict:
        uptime = int(time.time()) - self._started_at if self._started_at else 0
        return {
            "running": self._running,
            "tier": self._tier,
            "uptime_seconds": uptime,
            "uptime_human": f"{uptime // 3600}h {(uptime % 3600) // 60}m",
            "monthly_revenue": self._monthly_revenue,
            "monthly_spend": self._monthly_spend,
            "profits": self._monthly_revenue - self._monthly_spend,
            "budget_remaining": self.get_budget_remaining(),
            "treasury_balance": 0,
            "prospects": 0,
            "conversions": 0,
            "agent_spend": self._monthly_spend,
            "dynamic_pricing": get_pricing_status(),
            "scale_out": scale_out_manager.get_stats(),
        }


brain = Brain()
