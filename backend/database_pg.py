"""MAXIA Database V12 — PostgreSQL async adapter (mirrors database.py API)"""
import json, time, os
import asyncpg


class PostgresDatabase:
    """Drop-in replacement for the SQLite Database class, backed by PostgreSQL."""

    def __init__(self, database_url: str = ""):
        self._url = database_url or os.getenv("DATABASE_URL", "")
        self._pool: asyncpg.Pool | None = None

    # ── helpers ──

    async def _fetchone(self, sql: str, params: tuple = ()):
        async with self._pool.acquire() as conn:
            return await conn.fetchrow(sql, *params)

    async def _fetchall(self, sql: str, params: tuple = ()):
        async with self._pool.acquire() as conn:
            return await conn.fetch(sql, *params)

    async def _execute(self, sql: str, params: tuple = ()):
        async with self._pool.acquire() as conn:
            await conn.execute(sql, *params)

    # ── lifecycle ──

    async def connect(self):
        self._pool = await asyncpg.create_pool(
            self._url,
            min_size=2,
            max_size=20,
            command_timeout=30,
        )
        await self._create_schema()
        print(f"[DB-PG] Connected to PostgreSQL")

    async def disconnect(self):
        if self._pool:
            await self._pool.close()

    async def _create_schema(self):
        async with self._pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS exchange_tokens (
                    mint TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    name TEXT NOT NULL,
                    decimals INTEGER NOT NULL DEFAULT 9,
                    price DOUBLE PRECISION NOT NULL DEFAULT 0,
                    change24h DOUBLE PRECISION DEFAULT 0,
                    volume24h DOUBLE PRECISION DEFAULT 0,
                    creator_wallet TEXT,
                    listed_at BIGINT DEFAULT (EXTRACT(EPOCH FROM NOW())::BIGINT)
                );

                CREATE TABLE IF NOT EXISTS exchange_orders (
                    order_id TEXT PRIMARY KEY,
                    side TEXT NOT NULL,
                    mint TEXT NOT NULL,
                    qty DOUBLE PRECISION NOT NULL,
                    qty_filled DOUBLE PRECISION DEFAULT 0,
                    price_usdc DOUBLE PRECISION NOT NULL,
                    order_type TEXT DEFAULT 'LIMIT',
                    wallet TEXT NOT NULL,
                    escrow_tx TEXT NOT NULL,
                    currency TEXT DEFAULT 'USDC',
                    status TEXT DEFAULT 'open',
                    created_at BIGINT DEFAULT (EXTRACT(EPOCH FROM NOW())::BIGINT)
                );

                CREATE INDEX IF NOT EXISTS idx_orders_mint ON exchange_orders(mint, status);

                CREATE TABLE IF NOT EXISTS escrow (
                    escrow_id TEXT PRIMARY KEY,
                    order_id TEXT,
                    wallet TEXT NOT NULL,
                    currency TEXT NOT NULL,
                    amount_raw TEXT NOT NULL,
                    tx_signature TEXT NOT NULL,
                    status TEXT DEFAULT 'locked',
                    created_at BIGINT DEFAULT (EXTRACT(EPOCH FROM NOW())::BIGINT)
                );

                CREATE TABLE IF NOT EXISTS exchange_trades (
                    trade_id TEXT PRIMARY KEY,
                    mint TEXT NOT NULL,
                    qty DOUBLE PRECISION NOT NULL,
                    price_usdc DOUBLE PRECISION NOT NULL,
                    total_usdc DOUBLE PRECISION NOT NULL,
                    fee_usdc DOUBLE PRECISION NOT NULL,
                    buyer TEXT NOT NULL,
                    seller TEXT NOT NULL,
                    tx_signature TEXT,
                    executed_at BIGINT DEFAULT (EXTRACT(EPOCH FROM NOW())::BIGINT)
                );

                CREATE TABLE IF NOT EXISTS transactions (
                    tx_signature TEXT PRIMARY KEY,
                    wallet TEXT NOT NULL,
                    amount_usdc DOUBLE PRECISION NOT NULL DEFAULT 0,
                    purpose TEXT DEFAULT 'marketplace',
                    created_at BIGINT DEFAULT (EXTRACT(EPOCH FROM NOW())::BIGINT)
                );

                CREATE INDEX IF NOT EXISTS idx_tx_wallet ON transactions(wallet, created_at);

                CREATE TABLE IF NOT EXISTS auctions (
                    auction_id TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    created_at BIGINT DEFAULT (EXTRACT(EPOCH FROM NOW())::BIGINT)
                );

                CREATE TABLE IF NOT EXISTS listings (
                    id TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    created_at BIGINT DEFAULT (EXTRACT(EPOCH FROM NOW())::BIGINT)
                );

                CREATE TABLE IF NOT EXISTS commands (
                    command_id TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    created_at BIGINT DEFAULT (EXTRACT(EPOCH FROM NOW())::BIGINT)
                );

                CREATE TABLE IF NOT EXISTS subscriptions (
                    sub_id TEXT PRIMARY KEY,
                    wallet TEXT NOT NULL,
                    data TEXT NOT NULL,
                    created_at BIGINT DEFAULT (EXTRACT(EPOCH FROM NOW())::BIGINT)
                );

                CREATE TABLE IF NOT EXISTS referrals (
                    ref_id TEXT PRIMARY KEY,
                    referrer TEXT NOT NULL,
                    referee TEXT NOT NULL,
                    data TEXT NOT NULL,
                    created_at BIGINT DEFAULT (EXTRACT(EPOCH FROM NOW())::BIGINT)
                );

                CREATE TABLE IF NOT EXISTS datasets (
                    dataset_id TEXT PRIMARY KEY,
                    seller TEXT NOT NULL,
                    data TEXT NOT NULL,
                    created_at BIGINT DEFAULT (EXTRACT(EPOCH FROM NOW())::BIGINT)
                );

                CREATE TABLE IF NOT EXISTS data_purchases (
                    purchase_id TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    created_at BIGINT DEFAULT (EXTRACT(EPOCH FROM NOW())::BIGINT)
                );

                CREATE TABLE IF NOT EXISTS stakes (
                    stake_id TEXT PRIMARY KEY,
                    wallet TEXT NOT NULL,
                    data TEXT NOT NULL,
                    created_at BIGINT DEFAULT (EXTRACT(EPOCH FROM NOW())::BIGINT)
                );

                CREATE TABLE IF NOT EXISTS disputes (
                    dispute_id TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    created_at BIGINT DEFAULT (EXTRACT(EPOCH FROM NOW())::BIGINT)
                );

                CREATE TABLE IF NOT EXISTS escrow_records (
                    escrow_id TEXT PRIMARY KEY,
                    buyer TEXT NOT NULL,
                    seller TEXT NOT NULL,
                    status TEXT DEFAULT 'locked',
                    data TEXT NOT NULL,
                    created_at BIGINT DEFAULT (EXTRACT(EPOCH FROM NOW())::BIGINT)
                );

                CREATE INDEX IF NOT EXISTS idx_escrow_status ON escrow_records(status);

                CREATE TABLE IF NOT EXISTS agents (
                    api_key TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    wallet TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    tier TEXT DEFAULT 'BRONZE',
                    volume_30d DOUBLE PRECISION DEFAULT 0,
                    total_spent DOUBLE PRECISION DEFAULT 0,
                    total_earned DOUBLE PRECISION DEFAULT 0,
                    services_listed INTEGER DEFAULT 0,
                    created_at BIGINT DEFAULT (EXTRACT(EPOCH FROM NOW())::BIGINT)
                );

                CREATE INDEX IF NOT EXISTS idx_agents_wallet ON agents(wallet);

                CREATE TABLE IF NOT EXISTS agent_services (
                    id TEXT PRIMARY KEY,
                    agent_api_key TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    agent_wallet TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    type TEXT DEFAULT 'text',
                    price_usdc DOUBLE PRECISION NOT NULL,
                    endpoint TEXT DEFAULT '',
                    status TEXT DEFAULT 'active',
                    rating DOUBLE PRECISION DEFAULT 5.0,
                    sales INTEGER DEFAULT 0,
                    listed_at BIGINT DEFAULT (EXTRACT(EPOCH FROM NOW())::BIGINT),
                    CONSTRAINT fk_agent FOREIGN KEY (agent_api_key) REFERENCES agents(api_key)
                );

                CREATE INDEX IF NOT EXISTS idx_services_status ON agent_services(status);

                CREATE TABLE IF NOT EXISTS marketplace_tx (
                    tx_id TEXT PRIMARY KEY,
                    buyer TEXT NOT NULL,
                    seller TEXT NOT NULL,
                    service TEXT NOT NULL,
                    price_usdc DOUBLE PRECISION NOT NULL,
                    commission_usdc DOUBLE PRECISION NOT NULL,
                    seller_gets_usdc DOUBLE PRECISION NOT NULL,
                    created_at BIGINT DEFAULT (EXTRACT(EPOCH FROM NOW())::BIGINT)
                );

                CREATE TABLE IF NOT EXISTS stock_portfolios (
                    api_key TEXT NOT NULL, symbol TEXT NOT NULL, shares DOUBLE PRECISION NOT NULL DEFAULT 0,
                    updated_at BIGINT DEFAULT (EXTRACT(EPOCH FROM NOW())::BIGINT),
                    PRIMARY KEY (api_key, symbol)
                );

                CREATE TABLE IF NOT EXISTS stock_trades (
                    trade_id TEXT PRIMARY KEY, data TEXT NOT NULL,
                    created_at BIGINT DEFAULT (EXTRACT(EPOCH FROM NOW())::BIGINT)
                );
            """)

    # ── Exchange: Tokens ──

    async def save_token(self, t: dict):
        await self._execute(
            """INSERT INTO exchange_tokens(mint, symbol, name, decimals, price, creator_wallet)
               VALUES($1, $2, $3, $4, $5, $6)
               ON CONFLICT (mint) DO UPDATE SET
                   symbol = EXCLUDED.symbol, name = EXCLUDED.name,
                   decimals = EXCLUDED.decimals, price = EXCLUDED.price,
                   creator_wallet = EXCLUDED.creator_wallet""",
            (t["mint"], t["symbol"], t["name"], t.get("decimals", 9),
             t["price"], t.get("creator", "")))

    async def get_tokens(self):
        rows = await self._fetchall("SELECT * FROM exchange_tokens ORDER BY volume24h DESC")
        return [dict(r) for r in rows]

    # ── Exchange: Orders ──

    async def save_order(self, o):
        await self._execute(
            """INSERT INTO exchange_orders(order_id, side, mint, qty, qty_filled, price_usdc,
                   order_type, wallet, escrow_tx, currency, status, created_at)
               VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
               ON CONFLICT (order_id) DO UPDATE SET
                   side=EXCLUDED.side, mint=EXCLUDED.mint, qty=EXCLUDED.qty,
                   qty_filled=EXCLUDED.qty_filled, price_usdc=EXCLUDED.price_usdc,
                   order_type=EXCLUDED.order_type, wallet=EXCLUDED.wallet,
                   escrow_tx=EXCLUDED.escrow_tx, currency=EXCLUDED.currency,
                   status=EXCLUDED.status, created_at=EXCLUDED.created_at""",
            (o.order_id, o.side, o.mint, o.qty, o.qty_filled, o.price_usdc,
             o.order_type, o.wallet, o.escrow_tx,
             getattr(o, "currency", "USDC"), o.status, o.created_at))

    async def get_open_orders(self, mint):
        rows = await self._fetchall(
            "SELECT * FROM exchange_orders WHERE mint=$1 AND status IN ('open','partial') ORDER BY created_at ASC",
            (mint,))
        return [dict(r) for r in rows]

    async def update_order_status(self, order_id, status, qty_filled=None):
        if qty_filled is not None:
            await self._execute(
                "UPDATE exchange_orders SET status=$1, qty_filled=$2 WHERE order_id=$3",
                (status, qty_filled, order_id))
        else:
            await self._execute(
                "UPDATE exchange_orders SET status=$1 WHERE order_id=$2",
                (status, order_id))

    # ── Escrow ──

    async def lock_escrow(self, order_id, wallet, currency, amount_raw, tx_signature):
        import uuid as _u
        eid = str(_u.uuid4())
        await self._execute(
            "INSERT INTO escrow(escrow_id, order_id, wallet, currency, amount_raw, tx_signature) VALUES($1,$2,$3,$4,$5,$6)",
            (eid, order_id, wallet, currency, str(amount_raw), tx_signature))
        return eid

    async def get_escrow_by_order(self, order_id):
        row = await self._fetchone(
            "SELECT * FROM escrow WHERE order_id=$1 AND status='locked'", (order_id,))
        return dict(row) if row else None

    async def release_escrow(self, escrow_id):
        await self._execute(
            "UPDATE escrow SET status='released' WHERE escrow_id=$1", (escrow_id,))

    # ── Transactions ──

    async def record_transaction(self, wallet, tx_sig, amount_usdc, purpose="marketplace"):
        await self._execute(
            """INSERT INTO transactions(tx_signature, wallet, amount_usdc, purpose)
               VALUES($1,$2,$3,$4)
               ON CONFLICT (tx_signature) DO NOTHING""",
            (tx_sig, wallet, amount_usdc, purpose))

    async def tx_already_processed(self, tx_sig):
        row = await self._fetchone(
            "SELECT 1 FROM transactions WHERE tx_signature=$1", (tx_sig,))
        return row is not None

    async def get_agent_volume_30d(self, wallet):
        cutoff = int(time.time()) - 30 * 86400
        row = await self._fetchone(
            "SELECT COALESCE(SUM(amount_usdc),0) AS total FROM transactions WHERE wallet=$1 AND created_at>=$2",
            (wallet, cutoff))
        return float(row["total"]) if row else 0.0

    # ── Auctions ──

    async def save_auction(self, a):
        await self._execute(
            """INSERT INTO auctions(auction_id, data) VALUES($1, $2)
               ON CONFLICT (auction_id) DO UPDATE SET data = EXCLUDED.data""",
            (a["auctionId"], json.dumps(a)))

    async def get_auction(self, auction_id):
        row = await self._fetchone(
            "SELECT data FROM auctions WHERE auction_id=$1", (auction_id,))
        return json.loads(row["data"]) if row else None

    async def update_auction(self, auction_id, updates):
        a = await self.get_auction(auction_id)
        if a:
            a.update(updates)
            await self.save_auction(a)

    # ── Listings ──

    async def save_listing(self, l):
        await self._execute(
            """INSERT INTO listings(id, data) VALUES($1, $2)
               ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data""",
            (l["id"], json.dumps(l)))

    async def get_listings(self):
        rows = await self._fetchall("SELECT data FROM listings")
        return [json.loads(r["data"]) for r in rows]

    # ── Commands ──

    async def save_command(self, c):
        await self._execute(
            """INSERT INTO commands(command_id, data) VALUES($1, $2)
               ON CONFLICT (command_id) DO UPDATE SET data = EXCLUDED.data""",
            (c["commandId"], json.dumps(c)))

    # ── Stats ──

    async def get_stats(self):
        now = int(time.time())
        cutoff = now - 86400
        try:
            r1 = await self._fetchone(
                "SELECT COALESCE(SUM(amount_usdc),0) AS vol FROM transactions WHERE created_at>=$1",
                (cutoff,))
            r2 = await self._fetchone(
                "SELECT COALESCE(SUM(amount_usdc),0) AS rev FROM transactions")
            r3 = await self._fetchone("SELECT COUNT(*) AS cnt FROM listings")
            return {
                "volume_24h": float(r1["vol"]) if r1 else 0.0,
                "total_revenue": float(r2["rev"]) if r2 else 0.0,
                "listing_count": int(r3["cnt"]) if r3 else 0,
            }
        except Exception:
            return {"volume_24h": 0.0, "total_revenue": 0.0, "listing_count": 0}

    async def get_activity(self, limit=30):
        try:
            rows = await self._fetchall(
                "SELECT tx_signature, wallet, amount_usdc, purpose, created_at FROM transactions ORDER BY created_at DESC LIMIT $1",
                (limit,))
            return [dict(r) for r in rows]
        except Exception:
            return []

    # ── Stakes ──

    async def save_stake(self, stake: dict):
        await self._execute(
            """INSERT INTO stakes(stake_id, wallet, data) VALUES($1, $2, $3)
               ON CONFLICT (stake_id) DO UPDATE SET wallet = EXCLUDED.wallet, data = EXCLUDED.data""",
            (stake["stakeId"], stake["wallet"], json.dumps(stake)))

    async def get_stake(self, wallet: str):
        row = await self._fetchone(
            "SELECT data FROM stakes WHERE wallet=$1 ORDER BY created_at DESC LIMIT 1",
            (wallet,))
        return json.loads(row["data"]) if row else None

    async def get_all_stakes(self):
        rows = await self._fetchall("SELECT data FROM stakes")
        return [json.loads(r["data"]) for r in rows]

    # ── Disputes ──

    async def save_dispute(self, dispute: dict):
        await self._execute(
            """INSERT INTO disputes(dispute_id, data) VALUES($1, $2)
               ON CONFLICT (dispute_id) DO UPDATE SET data = EXCLUDED.data""",
            (dispute["disputeId"], json.dumps(dispute)))

    async def get_dispute(self, dispute_id: str):
        row = await self._fetchone(
            "SELECT data FROM disputes WHERE dispute_id=$1", (dispute_id,))
        return json.loads(row["data"]) if row else None

    async def get_all_disputes(self):
        rows = await self._fetchall("SELECT data FROM disputes")
        return [json.loads(r["data"]) for r in rows]

    # ── Marketplace: Agents ──

    async def save_agent(self, agent: dict):
        await self._execute(
            """INSERT INTO agents(api_key, name, wallet, description, tier,
                   volume_30d, total_spent, total_earned, services_listed)
               VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)
               ON CONFLICT (api_key) DO UPDATE SET
                   name=EXCLUDED.name, wallet=EXCLUDED.wallet,
                   description=EXCLUDED.description, tier=EXCLUDED.tier,
                   volume_30d=EXCLUDED.volume_30d, total_spent=EXCLUDED.total_spent,
                   total_earned=EXCLUDED.total_earned, services_listed=EXCLUDED.services_listed""",
            (agent["api_key"], agent["name"], agent["wallet"],
             agent.get("description", ""), agent.get("tier", "BRONZE"),
             agent.get("volume_30d", 0), agent.get("total_spent", 0),
             agent.get("total_earned", 0), agent.get("services_listed", 0)))

    async def get_agent(self, api_key: str):
        row = await self._fetchone("SELECT * FROM agents WHERE api_key=$1", (api_key,))
        return dict(row) if row else None

    async def get_all_agents(self):
        rows = await self._fetchall("SELECT * FROM agents ORDER BY created_at DESC")
        return [dict(r) for r in rows]

    async def update_agent(self, api_key: str, updates: dict):
        if not updates:
            return
        parts = []
        vals = []
        for i, (k, v) in enumerate(updates.items(), 1):
            parts.append(f"{k}=${i}")
            vals.append(v)
        vals.append(api_key)
        sql = f"UPDATE agents SET {', '.join(parts)} WHERE api_key=${len(vals)}"
        await self._execute(sql, tuple(vals))

    async def count_agents(self):
        row = await self._fetchone("SELECT COUNT(*) AS c FROM agents")
        return row["c"] if row else 0

    # ── Marketplace: Services ──

    async def save_service(self, service: dict):
        await self._execute(
            """INSERT INTO agent_services(id, agent_api_key, agent_name, agent_wallet,
                   name, description, type, price_usdc, endpoint, status, rating, sales)
               VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
               ON CONFLICT (id) DO UPDATE SET
                   agent_api_key=EXCLUDED.agent_api_key, agent_name=EXCLUDED.agent_name,
                   agent_wallet=EXCLUDED.agent_wallet, name=EXCLUDED.name,
                   description=EXCLUDED.description, type=EXCLUDED.type,
                   price_usdc=EXCLUDED.price_usdc, endpoint=EXCLUDED.endpoint,
                   status=EXCLUDED.status, rating=EXCLUDED.rating, sales=EXCLUDED.sales""",
            (service["id"], service["agent_api_key"], service["agent_name"],
             service["agent_wallet"], service["name"], service["description"],
             service.get("type", "text"), service["price_usdc"],
             service.get("endpoint", ""), service.get("status", "active"),
             service.get("rating", 5.0), service.get("sales", 0)))

    async def get_services(self, status="active"):
        rows = await self._fetchall(
            "SELECT * FROM agent_services WHERE status=$1 ORDER BY rating DESC, sales DESC",
            (status,))
        return [dict(r) for r in rows]

    async def get_service(self, service_id: str):
        row = await self._fetchone(
            "SELECT * FROM agent_services WHERE id=$1", (service_id,))
        return dict(row) if row else None

    async def update_service(self, service_id: str, updates: dict):
        if not updates:
            return
        parts = []
        vals = []
        for i, (k, v) in enumerate(updates.items(), 1):
            parts.append(f"{k}=${i}")
            vals.append(v)
        vals.append(service_id)
        sql = f"UPDATE agent_services SET {', '.join(parts)} WHERE id=${len(vals)}"
        await self._execute(sql, tuple(vals))

    # ── Marketplace: Transactions ──

    async def save_marketplace_tx(self, tx: dict):
        await self._execute(
            """INSERT INTO marketplace_tx(tx_id, buyer, seller, service,
                   price_usdc, commission_usdc, seller_gets_usdc)
               VALUES($1,$2,$3,$4,$5,$6,$7)""",
            (tx["tx_id"], tx["buyer"], tx["seller"], tx["service"],
             tx["price_usdc"], tx["commission_usdc"], tx["seller_gets_usdc"]))

    async def get_marketplace_stats(self):
        row_agents = await self._fetchone("SELECT COUNT(*) AS c FROM agents")
        agents_count = row_agents["c"] if row_agents else 0
        row_svc = await self._fetchone(
            "SELECT COUNT(*) AS c FROM agent_services WHERE status='active'")
        services_count = row_svc["c"] if row_svc else 0
        row_tx = await self._fetchone(
            "SELECT COUNT(*) AS c, COALESCE(SUM(price_usdc),0) AS vol, COALESCE(SUM(commission_usdc),0) AS comm FROM marketplace_tx")
        return {
            "agents_registered": agents_count,
            "services_listed": services_count,
            "total_transactions": row_tx["c"] if row_tx else 0,
            "total_volume_usdc": float(row_tx["vol"]) if row_tx else 0,
            "total_commission_usdc": float(row_tx["comm"]) if row_tx else 0,
        }

    # ── Analytics (V12) ──

    async def get_volume_timeseries(self, period_hours: int = 168, granularity_hours: int = 1):
        now = int(time.time())
        cutoff = now - period_hours * 3600
        gran = granularity_hours * 3600
        rows = await self._fetchall(
            """SELECT (created_at / $1) * $1 AS bucket,
                      COALESCE(SUM(amount_usdc), 0) AS volume,
                      COUNT(*) AS tx_count
               FROM transactions
               WHERE created_at >= $2
               GROUP BY bucket ORDER BY bucket""",
            (gran, cutoff))
        return [{"timestamp": r["bucket"], "volume_usdc": float(r["volume"]),
                 "tx_count": r["tx_count"]} for r in rows]

    async def get_top_agents(self, limit: int = 10, period_days: int = 30):
        cutoff = int(time.time()) - period_days * 86400
        rows = await self._fetchall(
            """SELECT a.api_key, a.name, a.wallet, a.tier,
                      COALESCE(SUM(t.amount_usdc), 0) AS volume
               FROM agents a
               LEFT JOIN transactions t ON t.wallet = a.wallet AND t.created_at >= $1
               GROUP BY a.api_key, a.name, a.wallet, a.tier
               ORDER BY volume DESC LIMIT $2""",
            (cutoff, limit))
        return [dict(r) for r in rows]

    async def get_revenue_breakdown(self, period_days: int = 30):
        cutoff = int(time.time()) - period_days * 86400
        rows = await self._fetchall(
            """SELECT purpose, COALESCE(SUM(amount_usdc), 0) AS total,
                      COUNT(*) AS tx_count
               FROM transactions WHERE created_at >= $1
               GROUP BY purpose ORDER BY total DESC""",
            (cutoff,))
        return [{"purpose": r["purpose"], "total_usdc": float(r["total"]),
                 "tx_count": r["tx_count"]} for r in rows]
