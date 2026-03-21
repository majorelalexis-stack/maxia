"""Audit local SQLite — log toutes les actions du CEO local."""
import aiosqlite
import time
from config_local import AUDIT_DB_PATH


_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS ceo_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    action TEXT NOT NULL,
    agent TEXT DEFAULT '',
    tier_used TEXT DEFAULT 'local',
    llm_cost_usd REAL DEFAULT 0.0,
    priority TEXT DEFAULT 'vert',
    approved_by TEXT DEFAULT 'auto',
    result TEXT DEFAULT '',
    success BOOLEAN DEFAULT 1,
    vps_response TEXT DEFAULT ''
);
"""


class AuditLocal:
    def __init__(self):
        self._db_path = AUDIT_DB_PATH
        self._initialized = False

    async def _ensure_db(self):
        if not self._initialized:
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(_CREATE_SQL)
                await db.commit()
            self._initialized = True

    async def log(self, action: str, agent: str = "", tier: str = "local",
                  cost: float = 0.0, priority: str = "vert",
                  approved_by: str = "auto", result: str = "",
                  success: bool = True, vps_response: str = ""):
        await self._ensure_db()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """INSERT INTO ceo_audit
                   (timestamp, action, agent, tier_used, llm_cost_usd, priority,
                    approved_by, result, success, vps_response)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    action[:500], agent, tier, cost, priority,
                    approved_by, result[:1000], success, vps_response[:1000],
                ),
            )
            await db.commit()

    async def get_recent(self, limit: int = 50) -> list:
        await self._ensure_db()
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM ceo_audit ORDER BY id DESC LIMIT ?", (limit,)
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_stats_today(self) -> dict:
        await self._ensure_db()
        today = time.strftime("%Y-%m-%d")
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                """SELECT COUNT(*) as total, SUM(llm_cost_usd) as cost,
                   SUM(CASE WHEN success THEN 1 ELSE 0 END) as successes
                   FROM ceo_audit WHERE timestamp LIKE ?""",
                (f"{today}%",),
            )
            row = await cursor.fetchone()
            return {
                "date": today,
                "total_actions": row[0] or 0,
                "total_cost_usd": round(row[1] or 0, 4),
                "successes": row[2] or 0,
            }


audit = AuditLocal()
