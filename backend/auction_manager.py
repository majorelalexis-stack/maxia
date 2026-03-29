"""MAXIA Auction Manager V9"""
import asyncio, json, logging, time
from fastapi import WebSocket

logger = logging.getLogger(__name__)

class AuctionManager:
    def __init__(self):
        self._clients: dict = {}
        self._auctions: dict = {}

    async def register(self, cid: str, ws: WebSocket):
        self._clients[cid] = {"ws": ws, "wallet": None}

    async def unregister(self, cid: str):
        self._clients.pop(cid, None)

    def set_wallet(self, cid: str, wallet: str):
        if cid in self._clients:
            self._clients[cid]["wallet"] = wallet

    async def open_auction(self, auction: dict):
        self._auctions[auction["auctionId"]] = auction
        await self.broadcast({"type": "AUCTION_OPENED", "payload": auction})

    def get_open_auctions(self):
        return [a for a in self._auctions.values() if a.get("status") == "open"]

    async def place_bid(self, auction_id: str, bid_usdc: float, wallet: str) -> dict:
        a = self._auctions.get(auction_id)
        if not a:
            return {"ok": False, "reason": "Enchere introuvable."}
        if a.get("status") != "open":
            return {"ok": False, "reason": "Enchere cloturee."}
        if bid_usdc <= a.get("currentBid", 0):
            return {"ok": False, "reason": f"Offre trop basse. Min: {a['currentBid']:.2f}"}
        if a.get("currentLeader") == wallet:
            return {"ok": False, "reason": "Vous etes deja en tete."}
        a["currentBid"] = bid_usdc
        a["currentLeader"] = wallet
        await self.broadcast({"type": "BID_PLACED", "payload": {
            "auctionId": auction_id, "bidUsdc": bid_usdc,
            "leader": wallet[:8] + "...", "timestamp": int(time.time() * 1000)
        }})
        # Fix #12: Persist bid to database
        try:
            from database import db
            await db.raw_execute(
                "UPDATE auctions SET data=? WHERE auction_id=?",
                (json.dumps(a), auction_id))
        except Exception:
            pass
        return {"ok": True}

    async def broadcast(self, msg: dict):
        dead = []
        for cid, client in self._clients.items():
            try:
                await client["ws"].send_json(msg)
            except Exception:
                dead.append(cid)
        for cid in dead:
            self._clients.pop(cid, None)

    async def run_expiry_worker(self):
        logger.info("[AuctionManager] Worker demarre")
        while True:
            now = int(time.time() * 1000)
            for aid, a in list(self._auctions.items()):
                if a.get("status") == "open" and a.get("endsAt", 0) < now:
                    a["status"] = "closed"
                    await self.broadcast({"type": "AUCTION_CLOSED", "payload": {
                        "auctionId": aid, "winner": a.get("currentLeader"),
                        "finalBid": a.get("currentBid")
                    }})
            await asyncio.sleep(2)
