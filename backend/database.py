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

db = Database()
