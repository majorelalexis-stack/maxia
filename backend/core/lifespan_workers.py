"""MAXIA V12 — Lifespan background workers (extracted from main.py, S33)"""
import asyncio
import json
import logging
import time

logger = logging.getLogger(__name__)


async def dispute_auto_resolve_worker(db):
    """Auto-resolve disputes after 48h — refund buyer."""
    while True:
        try:
            now = int(time.time())
            rows = await db.raw_execute_fetchall("SELECT id, data FROM disputes")
            for row in (rows or []):
                dispute = json.loads(row["data"])
                if dispute.get("status") == "open" and dispute.get("auto_resolve_at", 0) <= now:
                    dispute["status"] = "auto_resolved"
                    dispute["resolution"] = "Auto-resolved after 48h. Buyer refund initiated."
                    await db.raw_execute("UPDATE disputes SET data=? WHERE id=?",
                        (json.dumps(dispute), row["id"]))
                    logger.info("[Disputes] Auto-resolved: %s", row['id'])
        except Exception as e:
            if "no such table" not in str(e):
                logger.error("[Disputes] Worker error: %s", e)
        await asyncio.sleep(3600)  # check every hour


async def volume_decay_worker(db):
    """Reset volume_30d to 0 for agents inactive > 30 days. Runs daily."""
    while True:
        await asyncio.sleep(86400)  # once per day
        try:
            cutoff = int(time.time()) - 30 * 86400  # 30 days ago
            # Single batch UPDATE — no N+1 loop
            await db.raw_execute(
                "UPDATE agents SET volume_30d = 0, tier = 'BRONZE' "
                "WHERE volume_30d > 0 AND api_key IN ("
                "  SELECT a.api_key FROM agents a "
                "  LEFT JOIN marketplace_tx m ON (a.api_key = m.buyer OR a.api_key = m.seller) "
                "  GROUP BY a.api_key "
                "  HAVING COALESCE(MAX(m.created_at), 0) < ?"
                ")", (cutoff,))
            logger.info("[VolumeDecay] Batch reset inactive agents (cutoff: %s)", cutoff)
        except Exception as e:
            if "no such table" not in str(e):
                logger.error("[VolumeDecay] Error: %s", e)


async def price_broadcast_loop(broadcast_fn):
    """Broadcasts top token prices every 30s to /ws clients."""
    while True:
        try:
            from trading.price_oracle import get_crypto_prices
            prices = await get_crypto_prices()
            # Send top 10 tokens
            top = dict(list(prices.items())[:10]) if isinstance(prices, dict) else {}
            await broadcast_fn({"type": "price_update", "data": top})
        except Exception:
            pass
        await asyncio.sleep(30)
