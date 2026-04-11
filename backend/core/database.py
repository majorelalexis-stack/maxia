"""MAXIA Database V10 — SQLite (defaut) ou PostgreSQL (si DATABASE_URL defini).

SQLite pour dev/low-traffic. PostgreSQL pour prod >10 concurrent writers.
Usage : DATABASE_URL=postgresql://user:pass@host:5432/maxia dans .env
"""
from __future__ import annotations
import json, logging, time, os, re, aiosqlite
from typing import Any

logger = logging.getLogger(__name__)
from pathlib import Path

# ── Column whitelists for safe dynamic UPDATE (S-03) ──
ALLOWED_AGENT_COLUMNS = frozenset({
    "name", "wallet", "description", "tier",
    "volume_30d", "total_spent", "total_earned", "services_listed",
    "referred_by", "agent_id",
})
ALLOWED_SERVICE_COLUMNS = frozenset({
    "agent_api_key", "agent_name", "agent_wallet",
    "name", "description", "type", "price_usdc",
    "endpoint", "status", "rating", "rating_count", "sales",
})

DB_PATH = str(Path(__file__).parent.parent / "maxia.db")
DATABASE_URL = os.getenv("DATABASE_URL", "")  # postgresql://... pour prod scale

DB_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS exchange_tokens ("
    "mint TEXT PRIMARY KEY, symbol TEXT NOT NULL, name TEXT NOT NULL,"
    "decimals INTEGER NOT NULL DEFAULT 9, price NUMERIC(18,6) NOT NULL DEFAULT 0,"
    "change24h NUMERIC(18,6) DEFAULT 0, volume24h NUMERIC(18,6) DEFAULT 0,"
    "creator_wallet TEXT, listed_at INTEGER DEFAULT (strftime('%s','now')));"

    "CREATE TABLE IF NOT EXISTS exchange_orders ("
    "order_id TEXT PRIMARY KEY, side TEXT NOT NULL, mint TEXT NOT NULL,"
    "qty NUMERIC(18,6) NOT NULL, qty_filled NUMERIC(18,6) DEFAULT 0, price_usdc NUMERIC(18,6) NOT NULL,"
    "order_type TEXT DEFAULT 'LIMIT', wallet TEXT NOT NULL,"
    "escrow_tx TEXT NOT NULL, currency TEXT DEFAULT 'USDC',"
    "status TEXT DEFAULT 'open',"
    "created_at INTEGER DEFAULT (strftime('%s','now')));"

    "CREATE INDEX IF NOT EXISTS idx_orders_mint ON exchange_orders(mint, status);"

    "CREATE TABLE IF NOT EXISTS escrow ("
    "escrow_id TEXT PRIMARY KEY, order_id TEXT, wallet TEXT NOT NULL,"
    "currency TEXT NOT NULL, amount_raw TEXT NOT NULL,"
    "tx_signature TEXT NOT NULL, status TEXT DEFAULT 'locked',"
    "created_at INTEGER DEFAULT (strftime('%s','now')));"

    "CREATE TABLE IF NOT EXISTS exchange_trades ("
    "trade_id TEXT PRIMARY KEY, mint TEXT NOT NULL, qty NUMERIC(18,6) NOT NULL,"
    "price_usdc NUMERIC(18,6) NOT NULL, total_usdc NUMERIC(18,6) NOT NULL, fee_usdc NUMERIC(18,6) NOT NULL,"
    "buyer TEXT NOT NULL, seller TEXT NOT NULL, tx_signature TEXT,"
    "executed_at INTEGER DEFAULT (strftime('%s','now')));"

    "CREATE TABLE IF NOT EXISTS transactions ("
    "tx_signature TEXT PRIMARY KEY, wallet TEXT NOT NULL,"
    "amount_usdc NUMERIC(18,6) NOT NULL DEFAULT 0, purpose TEXT DEFAULT 'marketplace',"
    "created_at INTEGER DEFAULT (strftime('%s','now')));"

    "CREATE INDEX IF NOT EXISTS idx_tx_wallet ON transactions(wallet, created_at);"

    "CREATE TABLE IF NOT EXISTS auctions ("
    "auction_id TEXT PRIMARY KEY, data TEXT NOT NULL,"
    "created_at INTEGER DEFAULT (strftime('%s','now')));"

    "CREATE TABLE IF NOT EXISTS listings ("
    "id TEXT PRIMARY KEY, data TEXT NOT NULL,"
    "created_at INTEGER DEFAULT (strftime('%s','now')));"

    "CREATE TABLE IF NOT EXISTS commands ("
    "command_id TEXT PRIMARY KEY, data TEXT NOT NULL,"
    "created_at INTEGER DEFAULT (strftime('%s','now')));"

    "CREATE TABLE IF NOT EXISTS subscriptions ("
    "sub_id TEXT PRIMARY KEY, wallet TEXT NOT NULL, data TEXT NOT NULL,"
    "created_at INTEGER DEFAULT (strftime('%s','now')));"

    "CREATE TABLE IF NOT EXISTS referrals ("
    "ref_id TEXT PRIMARY KEY, referrer TEXT NOT NULL,"
    "referee TEXT NOT NULL, data TEXT NOT NULL,"
    "created_at INTEGER DEFAULT (strftime('%s','now')));"

    "CREATE TABLE IF NOT EXISTS disputes ("
    "id TEXT PRIMARY KEY, data TEXT NOT NULL,"
    "created_at INTEGER DEFAULT (strftime('%s','now')));"

    "CREATE TABLE IF NOT EXISTS datasets ("
    "dataset_id TEXT PRIMARY KEY, seller TEXT NOT NULL, data TEXT NOT NULL,"
    "created_at INTEGER DEFAULT (strftime('%s','now')));"

    "CREATE TABLE IF NOT EXISTS data_purchases ("
    "purchase_id TEXT PRIMARY KEY, data TEXT NOT NULL,"
    "created_at INTEGER DEFAULT (strftime('%s','now')));"

    "CREATE TABLE IF NOT EXISTS stakes ("
    "stake_id TEXT PRIMARY KEY, wallet TEXT NOT NULL, data TEXT NOT NULL,"
    "created_at INTEGER DEFAULT (strftime('%s','now')));"

    "CREATE TABLE IF NOT EXISTS escrow_records ("
    "escrow_id TEXT PRIMARY KEY, buyer TEXT NOT NULL, seller TEXT NOT NULL,"
    "status TEXT DEFAULT 'locked', data TEXT NOT NULL,"
    "created_at INTEGER DEFAULT (strftime('%s','now')));"

    "CREATE INDEX IF NOT EXISTS idx_escrow_status ON escrow_records(status);"

    "CREATE TABLE IF NOT EXISTS stock_portfolios ("
    "api_key TEXT NOT NULL, symbol TEXT NOT NULL, shares NUMERIC(18,6) NOT NULL DEFAULT 0,"
    "updated_at INTEGER DEFAULT (strftime('%s','now')),"
    "PRIMARY KEY (api_key, symbol));"

    "CREATE TABLE IF NOT EXISTS stock_trades ("
    "trade_id TEXT PRIMARY KEY, data TEXT NOT NULL,"
    "created_at INTEGER DEFAULT (strftime('%s','now')));"

    "CREATE TABLE IF NOT EXISTS agents ("
    "api_key TEXT PRIMARY KEY, name TEXT NOT NULL, wallet TEXT NOT NULL,"
    "description TEXT DEFAULT '', tier TEXT DEFAULT 'BRONZE',"
    "volume_30d NUMERIC(18,6) DEFAULT 0, total_spent NUMERIC(18,6) DEFAULT 0,"
    "total_earned NUMERIC(18,6) DEFAULT 0, services_listed INTEGER DEFAULT 0,"
    "created_at INTEGER DEFAULT (strftime('%s','now')));"

    "CREATE INDEX IF NOT EXISTS idx_agents_wallet ON agents(wallet);"

    "CREATE TABLE IF NOT EXISTS agent_services ("
    "id TEXT PRIMARY KEY, agent_api_key TEXT NOT NULL, agent_name TEXT NOT NULL,"
    "agent_wallet TEXT NOT NULL, name TEXT NOT NULL, description TEXT NOT NULL,"
    "type TEXT DEFAULT 'text', price_usdc NUMERIC(18,6) NOT NULL,"
    "endpoint TEXT DEFAULT '', status TEXT DEFAULT 'active',"
    "rating NUMERIC(18,6) DEFAULT 5.0, rating_count INTEGER DEFAULT 0, sales INTEGER DEFAULT 0,"
    "listed_at INTEGER DEFAULT (strftime('%s','now')),"
    "FOREIGN KEY (agent_api_key) REFERENCES agents(api_key));"

    "CREATE INDEX IF NOT EXISTS idx_services_status ON agent_services(status);"

    "CREATE TABLE IF NOT EXISTS swarm_clones ("
    "clone_id TEXT PRIMARY KEY, niche TEXT NOT NULL, name TEXT NOT NULL,"
    "status TEXT DEFAULT 'active', total_requests INTEGER DEFAULT 0,"
    "total_revenue NUMERIC(18,6) DEFAULT 0, wallet_address TEXT DEFAULT '',"
    "created_at INTEGER DEFAULT (strftime('%s','now')));"

    "CREATE TABLE IF NOT EXISTS gpu_instances ("
    "instance_id TEXT PRIMARY KEY, agent_wallet TEXT NOT NULL,"
    "agent_name TEXT NOT NULL, gpu_tier TEXT NOT NULL,"
    "duration_hours NUMERIC(18,6) NOT NULL, price_per_hour NUMERIC(18,6) NOT NULL,"
    "total_cost NUMERIC(18,6) NOT NULL, commission NUMERIC(18,6) NOT NULL DEFAULT 0,"
    "payment_tx TEXT, runpod_pod_id TEXT,"
    "status TEXT DEFAULT 'provisioning', ssh_endpoint TEXT,"
    "scheduled_end INTEGER, actual_end INTEGER, actual_cost NUMERIC(18,6),"
    "created_at INTEGER DEFAULT (strftime('%s','now')));"

    "CREATE TABLE IF NOT EXISTS marketplace_tx ("
    "tx_id TEXT PRIMARY KEY, buyer TEXT NOT NULL, seller TEXT NOT NULL,"
    "service TEXT NOT NULL, price_usdc NUMERIC(18,6) NOT NULL,"
    "commission_usdc NUMERIC(18,6) NOT NULL, seller_gets_usdc NUMERIC(18,6) NOT NULL,"
    "created_at INTEGER DEFAULT (strftime('%s','now')));"

    "CREATE TABLE IF NOT EXISTS crypto_swaps ("
    "swap_id TEXT PRIMARY KEY, buyer_wallet TEXT NOT NULL,"
    "from_token TEXT NOT NULL, to_token TEXT NOT NULL,"
    "amount_in NUMERIC(18,6) NOT NULL, amount_out NUMERIC(18,6) NOT NULL,"
    "commission NUMERIC(18,6) DEFAULT 0, payment_tx TEXT, jupiter_tx TEXT,"
    "status TEXT DEFAULT 'completed',"
    "created_at INTEGER DEFAULT (strftime('%s','now')));"

    "CREATE TABLE IF NOT EXISTS price_alerts ("
    "alert_id TEXT PRIMARY KEY, wallet TEXT NOT NULL, data TEXT NOT NULL,"
    "created_at INTEGER DEFAULT (strftime('%s','now')));"

    "CREATE INDEX IF NOT EXISTS idx_price_alerts_wallet ON price_alerts(wallet);"

    "CREATE TABLE IF NOT EXISTS wallet_follows ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, user_wallet TEXT NOT NULL,"
    "target_wallet TEXT NOT NULL, created_at INTEGER DEFAULT (strftime('%s','now')),"
    "UNIQUE(user_wallet, target_wallet));"

    "CREATE INDEX IF NOT EXISTS idx_wallet_follows_user ON wallet_follows(user_wallet);"

    "CREATE INDEX IF NOT EXISTS idx_tx_purpose ON transactions(purpose, created_at);"
    "CREATE INDEX IF NOT EXISTS idx_tx_created ON transactions(created_at);"
    "CREATE INDEX IF NOT EXISTS idx_svc_agent ON agent_services(agent_api_key, status);"
    "CREATE INDEX IF NOT EXISTS idx_mtx_buyer ON marketplace_tx(buyer);"
    "CREATE INDEX IF NOT EXISTS idx_mtx_seller ON marketplace_tx(seller);"
    "CREATE INDEX IF NOT EXISTS idx_swaps_wallet ON crypto_swaps(buyer_wallet);"
)

class Database:
    def __init__(self):
        self._db = None

    async def _fetchone(self, sql, params=()):
        """Compat: fetchone via fetchall. Works with both SQLite and PostgreSQL."""
        rows = await self.raw_execute_fetchall(sql, params)
        return rows[0] if rows else None

    async def _fetchall(self, sql, params=()):
        """Compat alias for raw_execute_fetchall."""
        return await self.raw_execute_fetchall(sql, params)

    # ── Public raw DB access helpers (avoids direct _db usage) ──

    def _pg_params(self, sql: str, params: tuple) -> tuple:
        """Convertit les placeholders ? en $1,$2,... pour PostgreSQL."""
        if not getattr(self, '_pg', None):
            return sql, params
        sql = self._pg_convert(sql)
        idx = 0
        out = []
        for ch in sql:
            if ch == '?':
                idx += 1
                out.append(f'${idx}')
            else:
                out.append(ch)
        return ''.join(out), params

    async def raw_execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        """Execute a raw SQL statement. Compatible SQLite + PostgreSQL."""
        if getattr(self, '_pg', None):
            sql, params = self._pg_params(sql, params)
            async with self._pg.acquire() as conn:
                await conn.execute(sql, *params)
            return
        await self._db.execute(sql, params)
        await self._db.commit()

    async def raw_execute_fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        """Execute a raw SELECT and return all rows. Compatible SQLite + PostgreSQL."""
        if getattr(self, '_pg', None):
            sql, params = self._pg_params(sql, params)
            async with self._pg.acquire() as conn:
                rows = await conn.fetch(sql, *params)
            return [dict(r) for r in rows]
        rows = await self._db.execute_fetchall(sql, params)
        return rows

    def _pg_convert(self, sql: str) -> str:
        """Convertit le SQL SQLite en PostgreSQL si necessaire."""
        if not getattr(self, '_pg', None):
            return sql
        # Toutes les variantes de strftime SQLite → PostgreSQL
        import re
        sql = sql.replace("(strftime('%s','now'))", "EXTRACT(EPOCH FROM NOW())::INTEGER")
        sql = sql.replace("strftime('%s','now')", "EXTRACT(EPOCH FROM NOW())::INTEGER")
        sql = re.sub(r"strftime\('[^']*',\s*'now'\)", "NOW()::TEXT", sql)
        sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
        # INSERT OR IGNORE → INSERT ... ON CONFLICT DO NOTHING
        sql = re.sub(
            r"INSERT\s+OR\s+IGNORE\s+INTO\s+(\w+)\s*\(([^)]+)\)\s*VALUES\s*\(([^)]+)\)",
            r"INSERT INTO \1 (\2) VALUES (\3) ON CONFLICT DO NOTHING",
            sql
        )
        # INSERT OR REPLACE → INSERT ... ON CONFLICT DO UPDATE (upsert)
        m_upsert = re.search(
            r"INSERT\s+OR\s+REPLACE\s+INTO\s+(\w+)\s*\(([^)]+)\)\s*VALUES\s*\(([^)]+)\)",
            sql
        )
        if m_upsert:
            cols = [c.strip() for c in m_upsert.group(2).split(",")]
            pk = cols[0]  # first column is assumed to be the PK
            update_cols = cols[1:]
            if update_cols:
                set_clause = ", ".join(f"{c}=EXCLUDED.{c}" for c in update_cols)
                sql = re.sub(
                    r"INSERT\s+OR\s+REPLACE\s+INTO\s+(\w+)\s*\(([^)]+)\)\s*VALUES\s*\(([^)]+)\)",
                    rf"INSERT INTO \1 (\2) VALUES (\3) ON CONFLICT ({pk}) DO UPDATE SET {set_clause}",
                    sql
                )
            else:
                # Single-column table — just ignore conflicts
                sql = re.sub(
                    r"INSERT\s+OR\s+REPLACE\s+INTO\s+(\w+)\s*\(([^)]+)\)\s*VALUES\s*\(([^)]+)\)",
                    r"INSERT INTO \1 (\2) VALUES (\3) ON CONFLICT DO NOTHING",
                    sql
                )
        # SQLite json_extract → PostgreSQL ->> operator
        sql = re.sub(r"json_extract\(([\w.]+),\s*'\$\.(\w+)'\)", r"\1::json->>'\2'", sql)
        # SQLite substr → PostgreSQL substring
        sql = re.sub(r"substr\((\w+),\s*(\d+),\s*(\d+)\)", r"substring(\1 from \2 for \3)", sql)
        return sql

    async def raw_executescript(self, sql: str) -> None:
        """Execute a raw SQL script (multiple statements). Compatible SQLite + PostgreSQL."""
        if getattr(self, '_pg', None):
            sql = self._pg_convert(sql)
            async with self._pg.acquire() as conn:
                for stmt in sql.split(";"):
                    stmt = stmt.strip()
                    if stmt:
                        try:
                            await conn.execute(stmt)
                        except Exception as e:
                            if "already exists" not in str(e).lower() and "duplicate" not in str(e).lower():
                                logger.warning("Migration warning: %s", e)
            return
        await self._db.executescript(sql)

    async def connect(self) -> None:
        if DATABASE_URL and DATABASE_URL.startswith("postgres"):
            # ── PostgreSQL (prod scale, >10 concurrent writers) ──
            try:
                import asyncpg
                self._pg = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=20)
                # Convertir le schema SQLite en PostgreSQL basique
                pg_schema = DB_SCHEMA
                pg_schema = pg_schema.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
                # Remplacer TOUTES les variantes de strftime SQLite
                pg_schema = pg_schema.replace("(strftime('%s','now'))", "EXTRACT(EPOCH FROM NOW())::INTEGER")
                pg_schema = pg_schema.replace("strftime('%s','now')", "EXTRACT(EPOCH FROM NOW())::INTEGER")
                # ON CONFLICT → PostgreSQL syntax
                pg_schema = pg_schema.replace("INSERT OR REPLACE", "INSERT")
                pg_schema = pg_schema.replace("INSERT OR IGNORE", "INSERT")
                # Tables deja creees manuellement — skip les erreurs silencieusement
                async with self._pg.acquire() as conn:
                    for stmt in pg_schema.split(";"):
                        stmt = stmt.strip()
                        if not stmt:
                            continue
                        try:
                            await conn.execute(stmt)
                        except Exception as e:
                            if "already exists" not in str(e).lower() and "duplicate" not in str(e).lower():
                                logger.warning("PG schema init warning: %s", e)
                self._db = None  # Pas de SQLite
                await self._run_migrations()
                logger.info("PostgreSQL connectee: %s", DATABASE_URL.split('@')[-1] if '@' in DATABASE_URL else '***')
                return
            except ImportError:
                logger.warning("asyncpg non installe — fallback SQLite")
            except Exception as e:
                logger.error("PostgreSQL error: %s — fallback SQLite", e)

        # ── SQLite (defaut, dev/low-traffic) ──
        self._pg = None
        self._db = await aiosqlite.connect(DB_PATH)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.executescript(DB_SCHEMA)
        await self._run_migrations()
        logger.info("SQLite connectee: %s", DB_PATH)

    # ── Schema migration system ──

    MIGRATIONS: dict[int, tuple[str, str]] = {
        # version: (description, SQL)
        1: ("Initial schema — baseline V12", ""),  # Schema actuel = version 1
        2: ("Agent permissions — spend caps, scopes, status, trust level, audit agent_id", (
            "CREATE TABLE IF NOT EXISTS agent_permissions ("
            "agent_id TEXT PRIMARY KEY,"
            "api_key TEXT NOT NULL,"
            "wallet TEXT NOT NULL,"
            "trust_level INTEGER NOT NULL DEFAULT 0,"
            "status TEXT NOT NULL DEFAULT 'active',"
            "scopes TEXT NOT NULL DEFAULT '*',"
            "max_daily_spend_usd NUMERIC(18,6) NOT NULL DEFAULT 50,"
            "max_single_tx_usd NUMERIC(18,6) NOT NULL DEFAULT 10,"
            "daily_spent_usd NUMERIC(18,6) NOT NULL DEFAULT 0,"
            "daily_spent_date TEXT NOT NULL DEFAULT '',"
            "frozen_at TEXT,"
            "revoked_at TEXT,"
            "downgraded_from INTEGER,"
            "created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),"
            "updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')));"
            "CREATE INDEX IF NOT EXISTS idx_agent_perms_wallet ON agent_permissions(wallet);"
            "CREATE INDEX IF NOT EXISTS idx_agent_perms_api_key ON agent_permissions(api_key);"
            "CREATE INDEX IF NOT EXISTS idx_agent_perms_status ON agent_permissions(status);"
        )),
        3: ("Agent identity — DID (W3C) + UAID (HCS-14 Hedera)", (
            "ALTER TABLE agent_permissions ADD COLUMN did TEXT DEFAULT '';"
            "ALTER TABLE agent_permissions ADD COLUMN uaid TEXT DEFAULT '';"
            "CREATE INDEX IF NOT EXISTS idx_agent_perms_did ON agent_permissions(did);"
            "CREATE INDEX IF NOT EXISTS idx_agent_perms_uaid ON agent_permissions(uaid);"
        )),
        4: ("Agent keypair — ed25519 public key for DID Document + signed intents", (
            "ALTER TABLE agent_permissions ADD COLUMN public_key TEXT DEFAULT '';"
        )),
        5: ("Agent referral tracking — referred_by, agent_id, category columns", (
            "ALTER TABLE agents ADD COLUMN referred_by TEXT DEFAULT '';"
            "ALTER TABLE agents ADD COLUMN agent_id TEXT DEFAULT '';"
            "ALTER TABLE agents ADD COLUMN category TEXT DEFAULT 'other';"
        )),
        6: ("Financial precision — REAL to NUMERIC(18,6) for all monetary columns (PostgreSQL only)", (
            # PostgreSQL: ALTER COLUMN TYPE — SQLite ignores this (REAL is already 64-bit float)
            # exchange_tokens
            "ALTER TABLE exchange_tokens ALTER COLUMN price TYPE NUMERIC(18,6);"
            "ALTER TABLE exchange_tokens ALTER COLUMN change24h TYPE NUMERIC(18,6);"
            "ALTER TABLE exchange_tokens ALTER COLUMN volume24h TYPE NUMERIC(18,6);"
            # exchange_orders
            "ALTER TABLE exchange_orders ALTER COLUMN qty TYPE NUMERIC(18,6);"
            "ALTER TABLE exchange_orders ALTER COLUMN qty_filled TYPE NUMERIC(18,6);"
            "ALTER TABLE exchange_orders ALTER COLUMN price_usdc TYPE NUMERIC(18,6);"
            # exchange_trades
            "ALTER TABLE exchange_trades ALTER COLUMN qty TYPE NUMERIC(18,6);"
            "ALTER TABLE exchange_trades ALTER COLUMN price_usdc TYPE NUMERIC(18,6);"
            "ALTER TABLE exchange_trades ALTER COLUMN total_usdc TYPE NUMERIC(18,6);"
            "ALTER TABLE exchange_trades ALTER COLUMN fee_usdc TYPE NUMERIC(18,6);"
            # transactions
            "ALTER TABLE transactions ALTER COLUMN amount_usdc TYPE NUMERIC(18,6);"
            # stock_portfolios
            "ALTER TABLE stock_portfolios ALTER COLUMN shares TYPE NUMERIC(18,6);"
            # agents
            "ALTER TABLE agents ALTER COLUMN volume_30d TYPE NUMERIC(18,6);"
            "ALTER TABLE agents ALTER COLUMN total_spent TYPE NUMERIC(18,6);"
            "ALTER TABLE agents ALTER COLUMN total_earned TYPE NUMERIC(18,6);"
            # agent_services
            "ALTER TABLE agent_services ALTER COLUMN price_usdc TYPE NUMERIC(18,6);"
            "ALTER TABLE agent_services ALTER COLUMN rating TYPE NUMERIC(18,6);"
            # swarm_clones
            "ALTER TABLE swarm_clones ALTER COLUMN total_revenue TYPE NUMERIC(18,6);"
            # gpu_instances
            "ALTER TABLE gpu_instances ALTER COLUMN duration_hours TYPE NUMERIC(18,6);"
            "ALTER TABLE gpu_instances ALTER COLUMN price_per_hour TYPE NUMERIC(18,6);"
            "ALTER TABLE gpu_instances ALTER COLUMN total_cost TYPE NUMERIC(18,6);"
            "ALTER TABLE gpu_instances ALTER COLUMN commission TYPE NUMERIC(18,6);"
            "ALTER TABLE gpu_instances ALTER COLUMN actual_cost TYPE NUMERIC(18,6);"
            # marketplace_tx
            "ALTER TABLE marketplace_tx ALTER COLUMN price_usdc TYPE NUMERIC(18,6);"
            "ALTER TABLE marketplace_tx ALTER COLUMN commission_usdc TYPE NUMERIC(18,6);"
            "ALTER TABLE marketplace_tx ALTER COLUMN seller_gets_usdc TYPE NUMERIC(18,6);"
            # crypto_swaps
            "ALTER TABLE crypto_swaps ALTER COLUMN amount_in TYPE NUMERIC(18,6);"
            "ALTER TABLE crypto_swaps ALTER COLUMN amount_out TYPE NUMERIC(18,6);"
            "ALTER TABLE crypto_swaps ALTER COLUMN commission TYPE NUMERIC(18,6);"
            # agent_permissions (migration v2)
            "ALTER TABLE agent_permissions ALTER COLUMN max_daily_spend_usd TYPE NUMERIC(18,6);"
            "ALTER TABLE agent_permissions ALTER COLUMN max_single_tx_usd TYPE NUMERIC(18,6);"
            "ALTER TABLE agent_permissions ALTER COLUMN daily_spent_usd TYPE NUMERIC(18,6);"
        )),
        7: ("Analytics — page views and sessions tracking", (
            "CREATE TABLE IF NOT EXISTS page_views ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "session_id TEXT NOT NULL,"
            "page TEXT NOT NULL,"
            "referrer TEXT DEFAULT '',"
            "ip TEXT DEFAULT '',"
            "user_agent TEXT DEFAULT '',"
            "device TEXT DEFAULT 'desktop',"
            "created_at INTEGER DEFAULT (strftime('%s','now')));"
            "CREATE INDEX IF NOT EXISTS idx_pv_session ON page_views(session_id);"
            "CREATE INDEX IF NOT EXISTS idx_pv_page ON page_views(page);"
            "CREATE INDEX IF NOT EXISTS idx_pv_created ON page_views(created_at);"
            "CREATE TABLE IF NOT EXISTS analytics_sessions ("
            "session_id TEXT PRIMARY KEY,"
            "ip TEXT DEFAULT '',"
            "device TEXT DEFAULT 'desktop',"
            "first_page TEXT DEFAULT '',"
            "pages_count INTEGER DEFAULT 1,"
            "first_seen INTEGER DEFAULT (strftime('%s','now')),"
            "last_seen INTEGER DEFAULT (strftime('%s','now')));"
        )),
        8: ("Performance indexes for common queries", (
            "CREATE INDEX IF NOT EXISTS idx_tx_wallet_purpose ON transactions(wallet, purpose);"
        )),
        9: ("PRO-A7: Independent referral codes — no longer derived from API key", (
            "ALTER TABLE agents ADD COLUMN referral_code TEXT DEFAULT '';"
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_agents_referral_code ON agents(referral_code) WHERE referral_code != '';"
        )),
        10: ("Alert subscriptions — persist Telegram alert subscribers to DB", (
            "CREATE TABLE IF NOT EXISTS alert_subscriptions ("
            "wallet TEXT PRIMARY KEY,"
            "chat_id TEXT NOT NULL,"
            "alert_types TEXT DEFAULT '[\"price\",\"whale\",\"yield\",\"transaction\"]',"
            "subscribed_at INTEGER,"
            "expires_at INTEGER,"
            "active INTEGER DEFAULT 1);"
        )),
        11: ("CEO actions — feedback loop for CEO agent decision tracking", (
            "CREATE TABLE IF NOT EXISTS ceo_actions ("
            "action_id TEXT PRIMARY KEY,"
            "action_type TEXT NOT NULL,"
            "content TEXT DEFAULT '',"
            "platform TEXT DEFAULT 'twitter',"
            "status TEXT DEFAULT 'proposed',"
            "engagement TEXT DEFAULT '{}',"
            "created_at INTEGER DEFAULT (strftime('%s','now')));"
            "CREATE INDEX IF NOT EXISTS idx_ceo_actions_type ON ceo_actions(action_type, created_at);"
            "CREATE INDEX IF NOT EXISTS idx_ceo_actions_status ON ceo_actions(status, created_at);"
        )),
        12: ("PRO-F: Jurisdiction declarations — GDPR compliance", (
            "CREATE TABLE IF NOT EXISTS jurisdiction_declarations ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "wallet TEXT NOT NULL,"
            "ip_address TEXT NOT NULL,"
            "declared_at INTEGER NOT NULL,"
            "declaration_text TEXT NOT NULL);"
            "CREATE INDEX IF NOT EXISTS idx_jurisdiction_wallet ON jurisdiction_declarations(wallet);"
        )),
        13: ("Phase 1 CEO Bridge — pending user messages queue (Discord/Forum/Inbox)", (
            "CREATE TABLE IF NOT EXISTS ceo_pending_replies ("
            "msg_id TEXT PRIMARY KEY,"
            "channel TEXT NOT NULL,"
            "source_ref TEXT NOT NULL DEFAULT '',"
            "user_id TEXT NOT NULL DEFAULT '',"
            "user_name TEXT DEFAULT '',"
            "message TEXT NOT NULL,"
            "language TEXT DEFAULT '',"
            "received_at INTEGER NOT NULL,"
            "status TEXT NOT NULL DEFAULT 'pending',"
            "response TEXT DEFAULT '',"
            "confidence REAL DEFAULT 0,"
            "escalated INTEGER DEFAULT 0,"
            "responded_at INTEGER DEFAULT 0);"
            "CREATE INDEX IF NOT EXISTS idx_ceo_pending_status ON ceo_pending_replies(status, received_at);"
            "CREATE INDEX IF NOT EXISTS idx_ceo_pending_channel ON ceo_pending_replies(channel, status);"
            "CREATE INDEX IF NOT EXISTS idx_ceo_pending_source ON ceo_pending_replies(source_ref);"
        )),
        14: ("MAXIA Guard Q2b — declarative policy YAML per agent", (
            "ALTER TABLE agent_permissions ADD COLUMN policy_yaml TEXT DEFAULT '';"
        )),
    }

    async def _run_migrations(self):
        """Applique les migrations manquantes dans l'ordre. Compatible SQLite + PostgreSQL."""
        # Creer la table de tracking si elle n'existe pas
        await self.raw_execute(
            "CREATE TABLE IF NOT EXISTS schema_version ("
            "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL, description TEXT)")

        # Lire la version actuelle
        rows = await self.raw_execute_fetchall(
            "SELECT MAX(version) as v FROM schema_version")
        current = rows[0]["v"] if rows and rows[0]["v"] is not None else 0

        # Appliquer les migrations manquantes
        is_pg = getattr(self, '_pg', None) is not None
        applied = 0
        for version in sorted(self.MIGRATIONS.keys()):
            if version <= current:
                continue
            desc, sql = self.MIGRATIONS[version]
            # Migration v6 (REAL→NUMERIC) requires ALTER COLUMN — PostgreSQL only
            if version == 6 and not is_pg:
                logger.info("Migration %d skipped (SQLite — REAL is already 64-bit)", version)
                sql = ""  # Skip SQL, still record version
            if sql:
                try:
                    await self.raw_executescript(sql)
                except Exception as e:
                    logger.error("MIGRATION %d ECHOUEE: %s", version, e)
                    break
            from datetime import datetime, timezone
            await self.raw_execute(
                "INSERT INTO schema_version(version, applied_at, description) VALUES(?,?,?)",
                (version, datetime.now(timezone.utc).isoformat(), desc))
            applied += 1
            logger.info("Migration %d appliquee: %s", version, desc)

        if applied == 0 and current > 0:
            pass  # Deja a jour
        elif current == 0:
            # Premier demarrage — enregistrer version 1
            from datetime import datetime, timezone
            try:
                await self.raw_execute(
                    "INSERT INTO schema_version(version, applied_at, description) VALUES(?,?,?)",
                    (1, datetime.now(timezone.utc).isoformat(), "Initial schema — baseline V12"))
            except Exception as e:
                logger.debug("Initial schema_version insert skipped (already exists): %s", e)

    async def disconnect(self) -> None:
        if self._db:
            await self._db.close()

    async def save_token(self, t: dict[str, Any]) -> None:
        await self.raw_execute(
            "INSERT OR REPLACE INTO exchange_tokens(mint,symbol,name,decimals,price,creator_wallet) VALUES(?,?,?,?,?,?)",
            (t["mint"], t["symbol"], t["name"], t.get("decimals", 9), t["price"], t.get("creator", "")))

    async def get_tokens(self) -> list[dict[str, Any]]:
        rows = await self.raw_execute_fetchall("SELECT mint, symbol, name, decimals, price, change24h, volume24h, creator_wallet, listed_at FROM exchange_tokens ORDER BY volume24h DESC LIMIT 1000")
        return [dict(r) for r in rows]

    async def save_order(self, o: Any) -> None:
        await self.raw_execute(
            "INSERT OR REPLACE INTO exchange_orders(order_id,side,mint,qty,qty_filled,price_usdc,order_type,wallet,escrow_tx,currency,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (o.order_id, o.side, o.mint, o.qty, o.qty_filled, o.price_usdc, o.order_type,
             o.wallet, o.escrow_tx, getattr(o, "currency", "USDC"), o.status, o.created_at))

    async def get_open_orders(self, mint: str) -> list[dict[str, Any]]:
        rows = await self.raw_execute_fetchall(
            "SELECT order_id, side, mint, qty, qty_filled, price_usdc, order_type, wallet, escrow_tx, currency, status, created_at FROM exchange_orders WHERE mint=? AND status IN ('open','partial') ORDER BY created_at ASC", (mint,))
        return [dict(r) for r in rows]

    async def update_order_status(self, order_id: str, status: str, qty_filled: float | None = None) -> None:
        if qty_filled is not None:
            await self.raw_execute("UPDATE exchange_orders SET status=?,qty_filled=? WHERE order_id=?", (status, qty_filled, order_id))
        else:
            await self.raw_execute("UPDATE exchange_orders SET status=? WHERE order_id=?", (status, order_id))

    async def lock_escrow(self, order_id: str, wallet: str, currency: str, amount_raw: int | str, tx_signature: str) -> str:
        import uuid as _u
        eid = str(_u.uuid4())
        await self.raw_execute(
            "INSERT INTO escrow(escrow_id,order_id,wallet,currency,amount_raw,tx_signature) VALUES(?,?,?,?,?,?)",
            (eid, order_id, wallet, currency, str(amount_raw), tx_signature))
        return eid

    async def get_escrow_by_order(self, order_id: str) -> dict[str, Any] | None:
        row = await self._fetchone("SELECT escrow_id, order_id, wallet, currency, amount_raw, tx_signature, status, created_at FROM escrow WHERE order_id=? AND status='locked'", (order_id,))
        return dict(row) if row else None

    async def release_escrow(self, escrow_id: str) -> None:
        await self.raw_execute("UPDATE escrow SET status='released' WHERE escrow_id=?", (escrow_id,))

    async def record_transaction(self, wallet: str, tx_sig: str, amount_usdc: float, purpose: str = "marketplace") -> None:
        await self.raw_execute(
            "INSERT OR IGNORE INTO transactions(tx_signature,wallet,amount_usdc,purpose) VALUES(?,?,?,?)",
            (tx_sig, wallet, amount_usdc, purpose))

    async def tx_already_processed(self, tx_sig: str) -> bool:
        row = await self._fetchone("SELECT 1 FROM transactions WHERE tx_signature=?", (tx_sig,))
        return row is not None

    async def get_agent_volume_30d(self, wallet: str) -> float:
        cutoff = int(time.time()) - 30 * 86400
        row = await self._fetchone(
            "SELECT COALESCE(SUM(amount_usdc),0) AS total FROM transactions WHERE wallet=? AND created_at>=?",
            (wallet, cutoff))
        return float(row["total"]) if row else 0.0

    async def get_swap_count(self, wallet: str) -> int:
        """Count total swaps for a wallet."""
        try:
            if getattr(self, '_pg', None):
                async with self._pg.acquire() as conn:
                    row = await conn.fetchrow(
                        "SELECT COUNT(*) as cnt FROM transactions WHERE wallet=$1 AND purpose='crypto_swap'",
                        wallet)
                    return int(row['cnt']) if row else 0
            else:
                rows = await self.raw_execute_fetchall(
                    "SELECT COUNT(*) as cnt FROM transactions WHERE wallet=? AND purpose='crypto_swap'",
                    (wallet,))
                return int(rows[0]['cnt']) if rows else 0
        except Exception as e:
            logger.warning("get_swap_count DB error: %s", e)
            return -1

    async def get_swap_volume_30d(self, wallet: str) -> float:
        """Get 30-day rolling swap volume for a wallet."""
        cutoff = int(time.time()) - 30 * 86400
        try:
            if getattr(self, '_pg', None):
                async with self._pg.acquire() as conn:
                    row = await conn.fetchrow(
                        "SELECT COALESCE(SUM(amount_usdc), 0) as vol FROM transactions WHERE wallet=$1 AND purpose='crypto_swap' AND created_at > $2",
                        wallet, cutoff
                    )
                    return float(row['vol']) if row else 0
            else:
                rows = await self.raw_execute_fetchall(
                    "SELECT COALESCE(SUM(amount_usdc), 0) as vol FROM transactions WHERE wallet=? AND purpose='crypto_swap' AND created_at > ?",
                    (wallet, cutoff)
                )
                return float(rows[0]['vol']) if rows else 0
        except Exception as e:
            logger.warning("get_swap_volume_30d DB error: %s", e)
            return 0

    async def save_auction(self, a: dict[str, Any]) -> None:
        await self.raw_execute("INSERT OR REPLACE INTO auctions(auction_id,data) VALUES(?,?)", (a["auctionId"], json.dumps(a)))

    async def get_auction(self, auction_id: str) -> dict[str, Any] | None:
        row = await self._fetchone("SELECT data FROM auctions WHERE auction_id=?", (auction_id,))
        return json.loads(row["data"]) if row else None

    async def update_auction(self, auction_id: str, updates: dict[str, Any]) -> None:
        a = await self.get_auction(auction_id)
        if a:
            a.update(updates)
            await self.save_auction(a)

    async def save_listing(self, l: dict[str, Any]) -> None:
        await self.raw_execute("INSERT OR REPLACE INTO listings(id,data) VALUES(?,?)", (l["id"], json.dumps(l)))

    async def get_listings(self, limit: int = 1000) -> list[dict[str, Any]]:
        rows = await self.raw_execute_fetchall("SELECT data FROM listings LIMIT ?", (limit,))
        return [json.loads(r["data"]) for r in rows]

    async def save_command(self, c: dict[str, Any]) -> None:
        await self.raw_execute("INSERT OR REPLACE INTO commands(command_id,data) VALUES(?,?)", (c["commandId"], json.dumps(c)))

    async def get_stats(self) -> dict[str, float | int]:
        now = int(time.time())
        cutoff = now - 86400
        try:
            r1 = await self._fetchone(
                "SELECT COALESCE(SUM(amount_usdc),0) AS vol FROM transactions WHERE created_at>=?", (cutoff,))
            r2 = await self._fetchone(
                "SELECT COALESCE(SUM(amount_usdc),0) AS rev FROM transactions WHERE purpose != 'crypto_swap'")
            r3 = await self._fetchone("SELECT COUNT(*) AS cnt FROM listings")
            return {
                "volume_24h": float(r1["vol"]) if r1 else 0.0,
                "total_revenue": float(r2["rev"]) if r2 else 0.0,
                "listing_count": int(r3["cnt"]) if r3 else 0,
            }
        except Exception as e:
            logger.warning("get_stats DB error: %s", e)
            return {"volume_24h": 0.0, "total_revenue": 0.0, "listing_count": 0}

    async def get_activity(self, limit: int = 30) -> list[dict[str, Any]]:
        try:
            rows = await self.raw_execute_fetchall(
                "SELECT tx_signature,wallet,amount_usdc,purpose,created_at FROM transactions ORDER BY created_at DESC LIMIT ?",
                (limit,))
            return [dict(r) for r in rows]
        except Exception as e:
            logger.warning("get_activity DB error: %s", e)
            return []

    async def save_stake(self, stake: dict[str, Any]) -> None:
        await self.raw_execute(
            "INSERT OR REPLACE INTO stakes(stake_id,wallet,data) VALUES(?,?,?)",
            (stake["stakeId"], stake["wallet"], json.dumps(stake)))

    async def get_stake(self, wallet: str) -> dict[str, Any] | None:
        row = await self._fetchone(
            "SELECT data FROM stakes WHERE wallet=? ORDER BY created_at DESC LIMIT 1", (wallet,))
        return json.loads(row["data"]) if row else None

    async def get_all_stakes(self, limit: int = 1000) -> list[dict[str, Any]]:
        rows = await self.raw_execute_fetchall("SELECT data FROM stakes LIMIT ?", (limit,))
        return [json.loads(r["data"]) for r in rows]

    async def save_dispute(self, dispute: dict[str, Any]) -> None:
        dispute_id = dispute.get("id", dispute.get("disputeId", ""))
        await self.raw_execute(
            "INSERT OR REPLACE INTO disputes(id,data) VALUES(?,?)",
            (dispute_id, json.dumps(dispute)))

    async def get_dispute(self, dispute_id: str) -> dict[str, Any] | None:
        row = await self._fetchone(
            "SELECT data FROM disputes WHERE id=?", (dispute_id,))
        return json.loads(row["data"]) if row else None

    async def get_all_disputes(self, limit: int = 1000) -> list[dict[str, Any]]:
        rows = await self.raw_execute_fetchall("SELECT data FROM disputes LIMIT ?", (limit,))
        return [json.loads(r["data"]) for r in rows]

    # ── Marketplace: Agents ──

    async def save_agent(self, agent: dict[str, Any]) -> None:
        await self.raw_execute(
            "INSERT OR REPLACE INTO agents(api_key,name,wallet,description,tier,volume_30d,total_spent,total_earned,services_listed) VALUES(?,?,?,?,?,?,?,?,?)",
            (agent["api_key"], agent["name"], agent["wallet"], agent.get("description", ""),
             agent.get("tier", "BRONZE"), agent.get("volume_30d", 0),
             agent.get("total_spent", 0), agent.get("total_earned", 0),
             agent.get("services_listed", 0)))

    async def get_agent(self, api_key: str) -> dict[str, Any] | None:
        # SELECT * intentional: all 10 columns (api_key, name, wallet, description, tier,
        # volume_30d, total_spent, total_earned, services_listed, created_at) are accessed
        # across 30+ callers in public_api, infra_features, marketplace_features, etc.
        rows = await self.raw_execute_fetchall(
            "SELECT api_key, name, wallet, description, tier, volume_30d, "
            "total_spent, total_earned, services_listed, created_at "
            "FROM agents WHERE api_key=?", (api_key,))
        return dict(rows[0]) if rows else None

    async def get_all_agents(self) -> list[dict[str, Any]]:
        return await self.raw_execute_fetchall(
            "SELECT api_key, name, wallet, description, tier, volume_30d, "
            "total_spent, total_earned, services_listed, created_at "
            "FROM agents ORDER BY created_at DESC")

    async def get_agent_by_referral_code(self, code: str) -> dict[str, Any] | None:
        """Find agent by independent referral_code column, fallback to legacy substr match."""
        # PRO-A7: prefer independent referral_code column
        rows = await self.raw_execute_fetchall(
            "SELECT api_key, name, wallet FROM agents WHERE referral_code = ? LIMIT 1",
            (code,))
        if rows:
            return dict(rows[0])
        # Legacy fallback: match substr(api_key, 7, 8) for agents registered before migration 9
        rows = await self.raw_execute_fetchall(
            "SELECT api_key, name, wallet FROM agents WHERE substr(api_key, 7, 8) = ? LIMIT 1",
            (code,))
        return dict(rows[0]) if rows else None

    async def update_agent(self, api_key: str, updates: dict[str, Any]) -> None:
        safe = {k: v for k, v in updates.items()
                if k in ALLOWED_AGENT_COLUMNS and re.match(r'^[a-z_]+$', k)}
        if not safe:
            return
        sets = ", ".join(f"{k}=?" for k in safe.keys())
        vals = list(safe.values()) + [api_key]
        await self.raw_execute(f"UPDATE agents SET {sets} WHERE api_key=?", tuple(vals))

    async def count_agents(self) -> int:
        rows = await self.raw_execute_fetchall("SELECT COUNT(*) as c FROM agents")
        return rows[0]["c"] if rows else 0

    # ── Marketplace: Services ──

    async def save_service(self, service: dict[str, Any]) -> None:
        if getattr(self, '_pg', None):
            async with self._pg.acquire() as conn:
                await conn.execute(
                    "INSERT INTO agent_services(id,agent_api_key,agent_name,agent_wallet,name,description,type,price_usdc,endpoint,status,rating,rating_count,sales) "
                    "VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13) "
                    "ON CONFLICT(id) DO UPDATE SET name=$5, description=$6, type=$7, price_usdc=$8, status=$10",
                    service["id"], service["agent_api_key"], service["agent_name"], service["agent_wallet"],
                    service["name"], service["description"], service.get("type", "text"),
                    service["price_usdc"], service.get("endpoint", ""), service.get("status", "active"),
                    service.get("rating", 5.0), service.get("rating_count", 0), service.get("sales", 0))
            return
        await self.raw_execute(
            "INSERT OR REPLACE INTO agent_services(id,agent_api_key,agent_name,agent_wallet,name,description,type,price_usdc,endpoint,status,rating,rating_count,sales) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (service["id"], service["agent_api_key"], service["agent_name"], service["agent_wallet"],
             service["name"], service["description"], service.get("type", "text"),
             service["price_usdc"], service.get("endpoint", ""), service.get("status", "active"),
             service.get("rating", 5.0), service.get("rating_count", 0), service.get("sales", 0)))

    _SVC_COLS = ("id, agent_api_key, agent_name, agent_wallet, name, description, "
                 "type, price_usdc, endpoint, status, rating, rating_count, sales, listed_at")

    async def get_services(self, status: str = "active") -> list[dict[str, Any]]:
        return await self.raw_execute_fetchall(
            f"SELECT {self._SVC_COLS} FROM agent_services WHERE status=? ORDER BY rating DESC, sales DESC", (status,))

    async def get_service(self, service_id: str) -> dict[str, Any] | None:
        rows = await self.raw_execute_fetchall(
            f"SELECT {self._SVC_COLS} FROM agent_services WHERE id=?", (service_id,))
        return dict(rows[0]) if rows else None

    async def get_service_by_name(self, name: str) -> dict[str, Any] | None:
        """Cherche un service par son nom (case-insensitive)."""
        rows = await self.raw_execute_fetchall(
            f"SELECT {self._SVC_COLS} FROM agent_services WHERE LOWER(name)=LOWER(?) AND status='active' LIMIT 1", (name,))
        return dict(rows[0]) if rows else None

    async def update_service(self, service_id: str, updates: dict[str, Any]) -> None:
        safe = {k: v for k, v in updates.items()
                if k in ALLOWED_SERVICE_COLUMNS and re.match(r'^[a-z_]+$', k)}
        if not safe:
            return
        sets = ", ".join(f"{k}=?" for k in safe.keys())
        vals = list(safe.values()) + [service_id]
        await self.raw_execute(f"UPDATE agent_services SET {sets} WHERE id=?", tuple(vals))

    # ── Marketplace: Transactions ──

    async def save_marketplace_tx(self, tx: dict[str, Any]) -> None:
        await self.raw_execute(
            "INSERT INTO marketplace_tx(tx_id,buyer,seller,service,price_usdc,commission_usdc,seller_gets_usdc) VALUES(?,?,?,?,?,?,?)",
            (tx["tx_id"], tx["buyer"], tx["seller"], tx["service"],
             tx["price_usdc"], tx["commission_usdc"], tx["seller_gets_usdc"]))

    async def get_marketplace_stats(self) -> dict[str, int | float]:
        rows = await self.raw_execute_fetchall("SELECT COUNT(*) as c FROM agents")
        agents_count = rows[0]["c"] if rows else 0
        rows = await self.raw_execute_fetchall("SELECT COUNT(*) as c FROM agent_services WHERE status='active'")
        services_count = rows[0]["c"] if rows else 0
        # Marketplace transactions
        rows = await self.raw_execute_fetchall("SELECT COUNT(*) as c, COALESCE(SUM(price_usdc),0) as vol, COALESCE(SUM(commission_usdc),0) as comm FROM marketplace_tx")
        mkt = rows[0] if rows else {}
        # Swap commissions
        swap_rows = await self.raw_execute_fetchall("SELECT COUNT(*) as c, COALESCE(SUM(commission),0) as comm FROM crypto_swaps")
        swaps = swap_rows[0] if swap_rows else {}
        return {
            "agents_registered": agents_count,
            "services_listed": services_count,
            "total_transactions": (mkt.get("c", 0) or 0) + (swaps.get("c", 0) or 0),
            "total_volume_usdc": mkt.get("vol", 0) or 0,
            "total_commission_usdc": (mkt.get("comm", 0) or 0) + (swaps.get("comm", 0) or 0),
            "swap_count": swaps.get("c", 0) or 0,
            "swap_commission_usdc": swaps.get("comm", 0) or 0,
        }

    # ── Crypto Swaps ──

    async def save_swap(self, swap: dict[str, Any]) -> None:
        await self.raw_execute(
            "INSERT INTO crypto_swaps (swap_id, buyer_wallet, from_token, to_token, "
            "amount_in, amount_out, commission, payment_tx, jupiter_tx, status) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (swap.get("swap_id", ""), swap.get("buyer_wallet", ""),
             swap.get("from_token", ""), swap.get("to_token", ""),
             swap.get("amount_in", 0), swap.get("amount_out", 0),
             swap.get("commission", 0), swap.get("payment_tx", ""),
             swap.get("jupiter_tx", ""), swap.get("status", "completed")))

    # ── Stock Portfolios ──

    async def save_stock_holding(self, api_key: str, symbol: str, shares: float) -> None:
        import time as _t
        now = int(_t.time())
        await self.raw_execute(
            "INSERT INTO stock_portfolios(api_key,symbol,shares,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(api_key,symbol) DO UPDATE SET shares=?,updated_at=?",
            (api_key, symbol, shares, now, shares, now))

    async def get_stock_portfolio(self, api_key: str) -> dict[str, float]:
        rows = await self.raw_execute_fetchall(
            "SELECT symbol, shares FROM stock_portfolios WHERE api_key=? AND shares>0", (api_key,))
        return {r["symbol"]: float(r["shares"]) for r in rows}

    async def get_all_stock_portfolios(self) -> dict[str, dict[str, float]]:
        rows = await self.raw_execute_fetchall(
            "SELECT api_key, symbol, shares FROM stock_portfolios WHERE shares>0")
        portfolios: dict = {}
        for r in rows:
            portfolios.setdefault(r["api_key"], {})[r["symbol"]] = float(r["shares"])
        return portfolios

    async def save_stock_trade(self, trade: dict[str, Any]) -> None:
        await self.raw_execute(
            "INSERT OR REPLACE INTO stock_trades(trade_id,data) VALUES(?,?)",
            (trade["trade_id"], json.dumps(trade)))

    async def get_stock_trades(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = await self.raw_execute_fetchall(
            "SELECT data FROM stock_trades ORDER BY created_at DESC LIMIT ?", (limit,))
        return [json.loads(r["data"]) for r in rows]

    # ── GPU Instances ──

    async def save_gpu_instance(self, instance: dict[str, Any]) -> None:
        await self.raw_execute(
            "INSERT INTO gpu_instances (instance_id, agent_wallet, agent_name, gpu_tier, "
            "duration_hours, price_per_hour, total_cost, commission, payment_tx, "
            "runpod_pod_id, status, ssh_endpoint, scheduled_end) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (instance["instance_id"], instance["agent_wallet"], instance["agent_name"],
             instance["gpu_tier"], instance["duration_hours"], instance["price_per_hour"],
             instance["total_cost"], instance.get("commission", 0),
             instance.get("payment_tx", ""), instance.get("runpod_pod_id", ""),
             instance.get("status", "provisioning"), instance.get("ssh_endpoint", ""),
             instance.get("scheduled_end", 0)))

    async def update_gpu_instance(self, instance_id: str, updates: dict[str, Any]) -> None:
        allowed = {"status", "actual_end", "actual_cost", "ssh_endpoint", "runpod_pod_id"}
        filtered = {k: v for k, v in updates.items() if k in allowed}
        if not filtered:
            return
        sets = ", ".join(f"{k}=?" for k in filtered)
        await self.raw_execute(
            f"UPDATE gpu_instances SET {sets} WHERE instance_id=?",
            (*filtered.values(), instance_id))

    _GPU_COLS = ("instance_id, agent_wallet, agent_name, gpu_tier, duration_hours, "
                 "price_per_hour, total_cost, commission, payment_tx, runpod_pod_id, "
                 "status, ssh_endpoint, scheduled_end, actual_end, actual_cost, created_at")

    async def get_active_gpu_instances(self) -> list[dict[str, Any]]:
        rows = await self.raw_execute_fetchall(
            f"SELECT {self._GPU_COLS} FROM gpu_instances WHERE status IN ('provisioning', 'running') "
            "ORDER BY created_at DESC")
        return [dict(r) for r in rows] if rows else []

    async def get_gpu_instance(self, instance_id: str) -> dict[str, Any]:
        rows = await self.raw_execute_fetchall(
            f"SELECT {self._GPU_COLS} FROM gpu_instances WHERE instance_id=?", (instance_id,))
        return dict(rows[0]) if rows else {}

    # ── Analytics (V12) ──

    async def get_volume_timeseries(self, period_hours: int = 168, granularity_hours: int = 1) -> list[dict[str, Any]]:
        """Return volume bucketed by granularity over the given period."""
        now = int(time.time())
        cutoff = now - period_hours * 3600
        gran = granularity_hours * 3600
        rows = await self.raw_execute_fetchall(
            "SELECT (created_at / ?) * ? AS bucket, COALESCE(SUM(amount_usdc),0) AS volume, COUNT(*) AS tx_count "
            "FROM transactions WHERE created_at >= ? GROUP BY bucket ORDER BY bucket",
            (gran, gran, cutoff))
        return [{"timestamp": r["bucket"], "volume_usdc": float(r["volume"]),
                 "tx_count": r["tx_count"]} for r in rows]

    async def get_top_agents(self, limit: int = 10, period_days: int = 30) -> list[dict[str, Any]]:
        """Return top agents by volume in the given period."""
        cutoff = int(time.time()) - period_days * 86400
        rows = await self.raw_execute_fetchall(
            "SELECT a.api_key, a.name, a.wallet, a.tier, "
            "COALESCE(SUM(t.amount_usdc),0) AS volume "
            "FROM agents a LEFT JOIN transactions t ON t.wallet = a.wallet AND t.created_at >= ? "
            "GROUP BY a.api_key, a.name, a.wallet, a.tier "
            "ORDER BY volume DESC LIMIT ?",
            (cutoff, limit))
        return [dict(r) for r in rows]

    async def get_revenue_breakdown(self, period_days: int = 30) -> list[dict[str, Any]]:
        """Return revenue grouped by purpose over the given period."""
        cutoff = int(time.time()) - period_days * 86400
        rows = await self.raw_execute_fetchall(
            "SELECT purpose, COALESCE(SUM(amount_usdc),0) AS total, COUNT(*) AS tx_count "
            "FROM transactions WHERE created_at >= ? GROUP BY purpose ORDER BY total DESC",
            (cutoff,))
        return [{"purpose": r["purpose"], "total_usdc": float(r["total"]),
                 "tx_count": r["tx_count"]} for r in rows]


    # ── Analytics ──

    async def track_page_view(self, session_id: str, page: str, referrer: str,
                               ip: str, user_agent: str, device: str) -> None:
        """Record a page view and update session."""
        now = int(time.time())
        await self.raw_execute(
            "INSERT INTO page_views(session_id,page,referrer,ip,user_agent,device,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (session_id, page, referrer, ip, user_agent, device, now))
        # Upsert session
        existing = await self._fetchone(
            "SELECT 1 FROM analytics_sessions WHERE session_id=?", (session_id,))
        if existing:
            await self.raw_execute(
                "UPDATE analytics_sessions SET last_seen=?, pages_count=pages_count+1 WHERE session_id=?",
                (now, session_id))
        else:
            await self.raw_execute(
                "INSERT INTO analytics_sessions(session_id,ip,device,first_page,pages_count,first_seen,last_seen) "
                "VALUES(?,?,?,?,1,?,?)",
                (session_id, ip, device, page, now, now))

    async def get_analytics_summary(self, period_days: int = 30) -> dict[str, Any]:
        """Analytics dashboard data."""
        now = int(time.time())
        day_ago = now - 86400
        week_ago = now - 7 * 86400
        cutoff = now - period_days * 86400

        # Visitors (unique sessions)
        r1 = await self._fetchone(
            "SELECT COUNT(DISTINCT session_id) AS cnt FROM page_views WHERE created_at>=?", (day_ago,))
        r7 = await self._fetchone(
            "SELECT COUNT(DISTINCT session_id) AS cnt FROM page_views WHERE created_at>=?", (week_ago,))
        r30 = await self._fetchone(
            "SELECT COUNT(DISTINCT session_id) AS cnt FROM page_views WHERE created_at>=?", (cutoff,))

        # Page views total
        pv1 = await self._fetchone(
            "SELECT COUNT(*) AS cnt FROM page_views WHERE created_at>=?", (day_ago,))
        pv30 = await self._fetchone(
            "SELECT COUNT(*) AS cnt FROM page_views WHERE created_at>=?", (cutoff,))

        # Top pages
        top_pages = await self.raw_execute_fetchall(
            "SELECT page, COUNT(*) AS views, COUNT(DISTINCT session_id) AS visitors "
            "FROM page_views WHERE created_at>=? GROUP BY page ORDER BY views DESC LIMIT 15",
            (cutoff,))

        # Avg session duration
        avg_dur = await self._fetchone(
            "SELECT AVG(last_seen - first_seen) AS avg_dur FROM analytics_sessions "
            "WHERE first_seen>=? AND last_seen > first_seen", (cutoff,))

        # Top referrers
        refs = await self.raw_execute_fetchall(
            "SELECT referrer, COUNT(*) AS cnt FROM page_views "
            "WHERE created_at>=? AND referrer != '' GROUP BY referrer ORDER BY cnt DESC LIMIT 10",
            (cutoff,))

        # Devices
        devices = await self.raw_execute_fetchall(
            "SELECT device, COUNT(DISTINCT session_id) AS cnt FROM page_views "
            "WHERE created_at>=? GROUP BY device ORDER BY cnt DESC",
            (cutoff,))

        # Visitors per hour (last 24h)
        hourly = await self.raw_execute_fetchall(
            "SELECT (created_at / 3600) * 3600 AS hour, COUNT(DISTINCT session_id) AS visitors "
            "FROM page_views WHERE created_at>=? GROUP BY hour ORDER BY hour",
            (day_ago,))

        # Visitors per day (last 30d)
        daily = await self.raw_execute_fetchall(
            "SELECT (created_at / 86400) * 86400 AS day, COUNT(DISTINCT session_id) AS visitors, COUNT(*) AS views "
            "FROM page_views WHERE created_at>=? GROUP BY day ORDER BY day",
            (cutoff,))

        return {
            "visitors_24h": int(r1["cnt"]) if r1 else 0,
            "visitors_7d": int(r7["cnt"]) if r7 else 0,
            "visitors_30d": int(r30["cnt"]) if r30 else 0,
            "page_views_24h": int(pv1["cnt"]) if pv1 else 0,
            "page_views_30d": int(pv30["cnt"]) if pv30 else 0,
            "avg_session_seconds": round(float(avg_dur["avg_dur"] or 0)) if avg_dur else 0,
            "top_pages": [{"page": r["page"], "views": r["views"], "visitors": r["visitors"]} for r in top_pages],
            "referrers": [{"referrer": r["referrer"], "count": r["cnt"]} for r in refs],
            "devices": [{"device": r["device"], "count": r["cnt"]} for r in devices],
            "hourly": [{"hour": r["hour"], "visitors": r["visitors"]} for r in hourly],
            "daily": [{"day": r["day"], "visitors": r["visitors"], "views": r["views"]} for r in daily],
        }


db = Database()


async def create_database() -> Database:
    """Factory: Database() gere SQLite et PostgreSQL automatiquement.
    Si DATABASE_URL est defini, PostgreSQL est utilise. Sinon, SQLite."""
    db_instance = Database()
    await db_instance.connect()
    return db_instance
