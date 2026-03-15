"""MAXIA Database V9 - SQLite async"""
import json, time, aiosqlite
from pathlib import Path

DB_PATH = str(Path(__file__).parent / "maxia.db")

DB_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS exchange_tokens ("
    "mint TEXT PRIMARY KEY, symbol TEXT NOT NULL, name TEXT NOT NULL,"
    "decimals INTEGER NOT NULL DEFAULT 9, price REAL NOT NULL DEFAULT 0,"
    "change24h REAL DEFAULT 0, volume24h REAL DEFAULT 0,"
    "creator_wallet TEXT, listed_at INTEGER DEFAULT (strftime('%s','now')));"

    "CREATE TABLE IF NOT EXISTS exchange_orders ("
    "order_id TEXT PRIMARY KEY, side TEXT NOT NULL, mint TEXT NOT NULL,"
    "qty REAL NOT NULL, qty_filled REAL DEFAULT 0, price_usdc REAL NOT NULL,"
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
    "trade_id TEXT PRIMARY KEY, mint TEXT NOT NULL, qty REAL NOT NULL,"
    "price_usdc REAL NOT NULL, total_usdc REAL NOT NULL, fee_usdc REAL NOT NULL,"
    "buyer TEXT NOT NULL, seller TEXT NOT NULL, tx_signature TEXT,"
    "executed_at INTEGER DEFAULT (strftime('%s','now')));"

    "CREATE TABLE IF NOT EXISTS transactions ("
    "tx_signature TEXT PRIMARY KEY, wallet TEXT NOT NULL,"
    "amount_usdc REAL NOT NULL DEFAULT 0, purpose TEXT DEFAULT 'marketplace',"
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

    "CREATE TABLE IF NOT EXISTS datasets ("
    "dataset_id TEXT PRIMARY KEY, seller TEXT NOT NULL, data TEXT NOT NULL,"
    "created_at INTEGER DEFAULT (strftime('%s','now')));"

    "CREATE TABLE IF NOT EXISTS data_purchases ("
    "purchase_id TEXT PRIMARY KEY, data TEXT NOT NULL,"
    "created_at INTEGER DEFAULT (strftime('%s','now')));"

    "CREATE TABLE IF NOT EXISTS stakes ("
    "stake_id TEXT PRIMARY KEY, wallet TEXT NOT NULL, data TEXT NOT NULL,"
    "created_at INTEGER DEFAULT (strftime('%s','now')));"

    "CREATE TABLE IF NOT EXISTS disputes ("
    "dispute_id TEXT PRIMARY KEY, data TEXT NOT NULL,"
    "created_at INTEGER DEFAULT (strftime('%s','now')));"

    "CREATE TABLE IF NOT EXISTS agents ("
    "api_key TEXT PRIMARY KEY, name TEXT NOT NULL, wallet TEXT NOT NULL,"
    "description TEXT DEFAULT '', tier TEXT DEFAULT 'BRONZE',"
    "volume_30d REAL DEFAULT 0, total_spent REAL DEFAULT 0,"
    "total_earned REAL DEFAULT 0, services_listed INTEGER DEFAULT 0,"
    "created_at INTEGER DEFAULT (strftime('%s','now')));"

    "CREATE INDEX IF NOT EXISTS idx_agents_wallet ON agents(wallet);"

    "CREATE TABLE IF NOT EXISTS agent_services ("
    "id TEXT PRIMARY KEY, agent_api_key TEXT NOT NULL, agent_name TEXT NOT NULL,"
    "agent_wallet TEXT NOT NULL, name TEXT NOT NULL, description TEXT NOT NULL,"
    "type TEXT DEFAULT 'text', price_usdc REAL NOT NULL,"
    "endpoint TEXT DEFAULT '', status TEXT DEFAULT 'active',"
    "rating REAL DEFAULT 5.0, sales INTEGER DEFAULT 0,"
    "listed_at INTEGER DEFAULT (strftime('%s','now')),"
    "FOREIGN KEY (agent_api_key) REFERENCES agents(api_key));"

    "CREATE INDEX IF NOT EXISTS idx_services_status ON agent_services(status);"

    "CREATE TABLE IF NOT EXISTS marketplace_tx ("
    "tx_id TEXT PRIMARY KEY, buyer TEXT NOT NULL, seller TEXT NOT NULL,"
    "service TEXT NOT NULL, price_usdc REAL NOT NULL,"
    "commission_usdc REAL NOT NULL, seller_gets_usdc REAL NOT NULL,"
    "created_at INTEGER DEFAULT (strftime('%s','now')));"
)

class Database:
    def __init__(self):
        self._db = None

    async def connect(self):
        self._db = await aiosqlite.connect(DB_PATH)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.executescript(DB_SCHEMA)
        await self._db.commit()
        print(f"[DB] Connectee: {DB_PATH}")

    async def disconnect(self):
        if self._db:
            await self._db.close()

    async def save_token(self, t: dict):
        await self._db.execute(
            "INSERT OR REPLACE INTO exchange_tokens(mint,symbol,name,decimals,price,creator_wallet) VALUES(?,?,?,?,?,?)",
            (t["mint"], t["symbol"], t["name"], t.get("decimals", 9), t["price"], t.get("creator", "")))
        await self._db.commit()

    async def get_tokens(self):
        rows = await self._db.execute_fetchall("SELECT * FROM exchange_tokens ORDER BY volume24h DESC")
        return [dict(r) for r in rows]

    async def save_order(self, o):
        await self._db.execute(
            "INSERT OR REPLACE INTO exchange_orders(order_id,side,mint,qty,qty_filled,price_usdc,order_type,wallet,escrow_tx,currency,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (o.order_id, o.side, o.mint, o.qty, o.qty_filled, o.price_usdc, o.order_type,
             o.wallet, o.escrow_tx, getattr(o, "currency", "USDC"), o.status, o.created_at))
        await self._db.commit()

    async def get_open_orders(self, mint):
        rows = await self._db.execute_fetchall(
            "SELECT * FROM exchange_orders WHERE mint=? AND status IN ('open','partial') ORDER BY created_at ASC", (mint,))
        return [dict(r) for r in rows]

    async def update_order_status(self, order_id, status, qty_filled=None):
        if qty_filled is not None:
            await self._db.execute("UPDATE exchange_orders SET status=?,qty_filled=? WHERE order_id=?", (status, qty_filled, order_id))
        else:
            await self._db.execute("UPDATE exchange_orders SET status=? WHERE order_id=?", (status, order_id))
        await self._db.commit()

    async def lock_escrow(self, order_id, wallet, currency, amount_raw, tx_signature):
        import uuid as _u
        eid = str(_u.uuid4())
        await self._db.execute(
            "INSERT INTO escrow(escrow_id,order_id,wallet,currency,amount_raw,tx_signature) VALUES(?,?,?,?,?,?)",
            (eid, order_id, wallet, currency, str(amount_raw), tx_signature))
        await self._db.commit()
        return eid

    async def get_escrow_by_order(self, order_id):
        row = await self._db.execute_fetchone("SELECT * FROM escrow WHERE order_id=? AND status='locked'", (order_id,))
        return dict(row) if row else None

    async def release_escrow(self, escrow_id):
        await self._db.execute("UPDATE escrow SET status='released' WHERE escrow_id=?", (escrow_id,))
        await self._db.commit()

    async def record_transaction(self, wallet, tx_sig, amount_usdc, purpose="marketplace"):
        await self._db.execute(
            "INSERT OR IGNORE INTO transactions(tx_signature,wallet,amount_usdc,purpose) VALUES(?,?,?,?)",
            (tx_sig, wallet, amount_usdc, purpose))
        await self._db.commit()

    async def tx_already_processed(self, tx_sig):
        row = await self._db.execute_fetchone("SELECT 1 FROM transactions WHERE tx_signature=?", (tx_sig,))
        return row is not None

    async def get_agent_volume_30d(self, wallet):
        cutoff = int(time.time()) - 30 * 86400
        row = await self._db.execute_fetchone(
            "SELECT COALESCE(SUM(amount_usdc),0) AS total FROM transactions WHERE wallet=? AND created_at>=?",
            (wallet, cutoff))
        return float(row["total"]) if row else 0.0

    async def save_auction(self, a):
        await self._db.execute("INSERT OR REPLACE INTO auctions(auction_id,data) VALUES(?,?)", (a["auctionId"], json.dumps(a)))
        await self._db.commit()

    async def get_auction(self, auction_id):
        row = await self._db.execute_fetchone("SELECT data FROM auctions WHERE auction_id=?", (auction_id,))
        return json.loads(row["data"]) if row else None

    async def update_auction(self, auction_id, updates):
        a = await self.get_auction(auction_id)
        if a:
            a.update(updates)
            await self.save_auction(a)

    async def save_listing(self, l):
        await self._db.execute("INSERT OR REPLACE INTO listings(id,data) VALUES(?,?)", (l["id"], json.dumps(l)))
        await self._db.commit()

    async def get_listings(self):
        rows = await self._db.execute_fetchall("SELECT data FROM listings")
        return [json.loads(r["data"]) for r in rows]

    async def save_command(self, c):
        await self._db.execute("INSERT OR REPLACE INTO commands(command_id,data) VALUES(?,?)", (c["commandId"], json.dumps(c)))
        await self._db.commit()

    async def get_stats(self):
        now = int(time.time())
        cutoff = now - 86400
        try:
            r1 = await self._db.execute_fetchone(
                "SELECT COALESCE(SUM(amount_usdc),0) AS vol FROM transactions WHERE created_at>=?", (cutoff,))
            r2 = await self._db.execute_fetchone(
                "SELECT COALESCE(SUM(amount_usdc),0) AS rev FROM transactions")
            r3 = await self._db.execute_fetchone("SELECT COUNT(*) AS cnt FROM listings")
            return {
                "volume_24h": float(r1["vol"]) if r1 else 0.0,
                "total_revenue": float(r2["rev"]) if r2 else 0.0,
                "listing_count": int(r3["cnt"]) if r3 else 0,
            }
        except Exception:
            return {"volume_24h": 0.0, "total_revenue": 0.0, "listing_count": 0}

    async def get_activity(self, limit=30):
        try:
            rows = await self._db.execute_fetchall(
                "SELECT tx_signature,wallet,amount_usdc,purpose,created_at FROM transactions ORDER BY created_at DESC LIMIT ?",
                (limit,))
            return [dict(r) for r in rows]
        except Exception:
            return []

    async def save_stake(self, stake: dict):
        await self._db.execute(
            "INSERT OR REPLACE INTO stakes(stake_id,wallet,data) VALUES(?,?,?)",
            (stake["stakeId"], stake["wallet"], json.dumps(stake)))
        await self._db.commit()

    async def get_stake(self, wallet: str):
        row = await self._db.execute_fetchone(
            "SELECT data FROM stakes WHERE wallet=? ORDER BY created_at DESC LIMIT 1", (wallet,))
        return json.loads(row["data"]) if row else None

    async def get_all_stakes(self):
        rows = await self._db.execute_fetchall("SELECT data FROM stakes")
        return [json.loads(r["data"]) for r in rows]

    async def save_dispute(self, dispute: dict):
        await self._db.execute(
            "INSERT OR REPLACE INTO disputes(dispute_id,data) VALUES(?,?)",
            (dispute["disputeId"], json.dumps(dispute)))
        await self._db.commit()

    async def get_dispute(self, dispute_id: str):
        row = await self._db.execute_fetchone(
            "SELECT data FROM disputes WHERE dispute_id=?", (dispute_id,))
        return json.loads(row["data"]) if row else None

    async def get_all_disputes(self):
        rows = await self._db.execute_fetchall("SELECT data FROM disputes")
        return [json.loads(r["data"]) for r in rows]

    # ── Marketplace: Agents ──

    async def save_agent(self, agent: dict):
        await self._db.execute(
            "INSERT OR REPLACE INTO agents(api_key,name,wallet,description,tier,volume_30d,total_spent,total_earned,services_listed) VALUES(?,?,?,?,?,?,?,?,?)",
            (agent["api_key"], agent["name"], agent["wallet"], agent.get("description", ""),
             agent.get("tier", "BRONZE"), agent.get("volume_30d", 0),
             agent.get("total_spent", 0), agent.get("total_earned", 0),
             agent.get("services_listed", 0)))
        await self._db.commit()

    async def get_agent(self, api_key: str):
        row = await self._db.execute_fetchone("SELECT * FROM agents WHERE api_key=?", (api_key,))
        return dict(row) if row else None

    async def get_all_agents(self):
        rows = await self._db.execute_fetchall("SELECT * FROM agents ORDER BY created_at DESC")
        return [dict(r) for r in rows]

    async def update_agent(self, api_key: str, updates: dict):
        sets = ", ".join(f"{k}=?" for k in updates.keys())
        vals = list(updates.values()) + [api_key]
        await self._db.execute(f"UPDATE agents SET {sets} WHERE api_key=?", vals)
        await self._db.commit()

    async def count_agents(self):
        row = await self._db.execute_fetchone("SELECT COUNT(*) as c FROM agents")
        return row["c"] if row else 0

    # ── Marketplace: Services ──

    async def save_service(self, service: dict):
        await self._db.execute(
            "INSERT OR REPLACE INTO agent_services(id,agent_api_key,agent_name,agent_wallet,name,description,type,price_usdc,endpoint,status,rating,sales) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (service["id"], service["agent_api_key"], service["agent_name"], service["agent_wallet"],
             service["name"], service["description"], service.get("type", "text"),
             service["price_usdc"], service.get("endpoint", ""), service.get("status", "active"),
             service.get("rating", 5.0), service.get("sales", 0)))
        await self._db.commit()

    async def get_services(self, status="active"):
        rows = await self._db.execute_fetchall(
            "SELECT * FROM agent_services WHERE status=? ORDER BY rating DESC, sales DESC", (status,))
        return [dict(r) for r in rows]

    async def get_service(self, service_id: str):
        row = await self._db.execute_fetchone("SELECT * FROM agent_services WHERE id=?", (service_id,))
        return dict(row) if row else None

    async def update_service(self, service_id: str, updates: dict):
        sets = ", ".join(f"{k}=?" for k in updates.keys())
        vals = list(updates.values()) + [service_id]
        await self._db.execute(f"UPDATE agent_services SET {sets} WHERE id=?", vals)
        await self._db.commit()

    # ── Marketplace: Transactions ──

    async def save_marketplace_tx(self, tx: dict):
        await self._db.execute(
            "INSERT INTO marketplace_tx(tx_id,buyer,seller,service,price_usdc,commission_usdc,seller_gets_usdc) VALUES(?,?,?,?,?,?,?)",
            (tx["tx_id"], tx["buyer"], tx["seller"], tx["service"],
             tx["price_usdc"], tx["commission_usdc"], tx["seller_gets_usdc"]))
        await self._db.commit()

    async def get_marketplace_stats(self):
        agents = await self._db.execute_fetchone("SELECT COUNT(*) as c FROM agents")
        services = await self._db.execute_fetchone("SELECT COUNT(*) as c FROM agent_services WHERE status='active'")
        txs = await self._db.execute_fetchone("SELECT COUNT(*) as c, COALESCE(SUM(price_usdc),0) as vol, COALESCE(SUM(commission_usdc),0) as comm FROM marketplace_tx")
        return {
            "agents_registered": agents["c"] if agents else 0,
            "services_listed": services["c"] if services else 0,
            "total_transactions": txs["c"] if txs else 0,
            "total_volume_usdc": txs["vol"] if txs else 0,
            "total_commission_usdc": txs["comm"] if txs else 0,
        }

db = Database()
