"""MAXIA Art.21 V12 — Escrow Client (wallet-based avec persistance DB)"""
import uuid, time, json, asyncio
import httpx
import base58
from config import (
    get_rpc_url, TREASURY_ADDRESS, ESCROW_ADDRESS, ESCROW_PRIVKEY_B58,
)
from alerts import alert_system, alert_error

USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


class EscrowClient:
    """
    Escrow wallet-based: les USDC sont verouilles sur le wallet escrow
    et liberes au seller apres confirmation du buyer.
    Persistance en DB pour survivre aux redemarrages.
    """

    def __init__(self):
        self._db = None
        self._escrows: dict = {}  # cache local, synchronise avec DB
        print(f"[EscrowClient] Initialise (wallet: {ESCROW_ADDRESS[:16]}...)" if ESCROW_ADDRESS else "[EscrowClient] ATTENTION: ESCROW_ADDRESS non configure")

    def set_db(self, db):
        self._db = db

    async def _load_from_db(self):
        """Charge les escrows actifs depuis la DB au demarrage."""
        if not self._db:
            return
        try:
            rows = await self._db.raw_execute_fetchall(
                "SELECT data FROM escrow_records WHERE status='locked'")
            for row in rows:
                escrow = json.loads(row["data"])
                self._escrows[escrow["escrowId"]] = escrow
            print(f"[EscrowClient] {len(self._escrows)} escrows actifs charges depuis DB")
        except Exception:
            pass  # Table pas encore creee

    async def _save_escrow(self, escrow: dict):
        """Persiste un escrow en DB."""
        self._escrows[escrow["escrowId"]] = escrow
        if self._db:
            try:
                await self._db.raw_execute(
                    "INSERT OR REPLACE INTO escrow_records(escrow_id, buyer, seller, status, data) VALUES(?,?,?,?,?)",
                    (escrow["escrowId"], escrow["buyer"], escrow["seller"],
                     escrow["status"], json.dumps(escrow)))
            except Exception as e:
                print(f"[EscrowClient] Erreur sauvegarde DB: {e}")

    async def create_escrow(self, buyer_wallet: str, seller_wallet: str,
                             amount_usdc: float, service_id: str,
                             tx_signature: str, timeout_hours: int = 72) -> dict:
        """
        Cree un escrow — verifie que les USDC ont ete envoyes au wallet escrow.
        """
        if not ESCROW_ADDRESS:
            return {"success": False, "error": "ESCROW_ADDRESS non configure"}

        # D-02: Uniqueness check — prevent duplicate escrows for the same tx
        if self._db:
            try:
                existing = await self._db.raw_execute_fetchall(
                    "SELECT escrow_id FROM escrow_records WHERE data LIKE ?",
                    (f'%"txSignature": "{tx_signature}"%',))
                if existing:
                    return {"success": False, "error": f"Escrow already exists for tx {tx_signature[:16]}..."}
            except Exception:
                pass  # Table may not exist yet

        escrow_id = str(uuid.uuid4())

        # Verifier la transaction de paiement avec montant + destinataire
        from solana_verifier import verify_transaction
        pay_ok = await verify_transaction(
            tx_signature=tx_signature,
            expected_amount_usdc=amount_usdc,
            expected_recipient=ESCROW_ADDRESS,
        )
        if not pay_ok.get("valid"):
            return {"success": False, "error": f"Paiement non verifie: {pay_ok.get('error')}"}

        escrow = {
            "escrowId": escrow_id,
            "buyer": buyer_wallet,
            "seller": seller_wallet,
            "amount_usdc": amount_usdc,
            "amount_raw": int(amount_usdc * 1e6),
            "serviceId": service_id,
            "txSignature": tx_signature,
            "status": "locked",
            "createdAt": int(time.time()),
            "timeoutAt": int(time.time()) + timeout_hours * 3600,
            "timeoutHours": timeout_hours,
            "verified_amount": pay_ok.get("amount_usdc", 0),
            "verified_from": pay_ok.get("from", ""),
        }
        await self._save_escrow(escrow)

        print(f"[EscrowClient] Escrow cree: {amount_usdc} USDC | {buyer_wallet[:8]}... -> {seller_wallet[:8]}...")
        await alert_system(
            "Nouvel Escrow",
            f"**{amount_usdc} USDC** verrouilles\n"
            f"Buyer: `{buyer_wallet[:8]}...`\n"
            f"Seller: `{seller_wallet[:8]}...`\n"
            f"Service: {service_id}\n"
            f"Timeout: {timeout_hours}h",
        )

        return {"success": True, **escrow}

    async def confirm_delivery(self, escrow_id: str, buyer_wallet: str) -> dict:
        """Buyer confirme la livraison -> USDC liberes au seller."""
        escrow = self._escrows.get(escrow_id)
        if not escrow:
            return {"success": False, "error": "Escrow introuvable"}
        if escrow["status"] != "locked":
            return {"success": False, "error": f"Status invalide: {escrow['status']}"}
        if escrow["buyer"] != buyer_wallet:
            return {"success": False, "error": "Seul le buyer peut confirmer"}

        # Envoyer les USDC au seller
        from solana_tx import send_usdc_transfer
        result = await send_usdc_transfer(
            to_address=escrow["seller"],
            amount_usdc=escrow["amount_usdc"],
            from_privkey=ESCROW_PRIVKEY_B58,
            from_address=ESCROW_ADDRESS,
        )

        if result.get("success"):
            escrow["status"] = "released"
            escrow["releasedAt"] = int(time.time())
            escrow["releaseTx"] = result.get("signature", "")
            await self._save_escrow(escrow)
            print(f"[EscrowClient] Released: {escrow['amount_usdc']} USDC -> {escrow['seller'][:8]}...")
            await alert_system(
                "Escrow libere",
                f"**{escrow['amount_usdc']} USDC** envoyes au seller `{escrow['seller'][:8]}...`",
            )
            return {"success": True, **escrow}
        else:
            return {"success": False, "error": f"Transfer echoue: {result.get('error')}"}

    async def reclaim_timeout(self, escrow_id: str, buyer_wallet: str) -> dict:
        """Buyer reclame ses fonds apres timeout."""
        escrow = self._escrows.get(escrow_id)
        if not escrow:
            return {"success": False, "error": "Escrow introuvable"}
        if escrow["status"] != "locked":
            return {"success": False, "error": f"Status invalide: {escrow['status']}"}
        if escrow["buyer"] != buyer_wallet:
            return {"success": False, "error": "Seul le buyer peut reclamer"}
        if time.time() < escrow["timeoutAt"]:
            remaining = (escrow["timeoutAt"] - time.time()) / 3600
            return {"success": False, "error": f"Timeout non atteint — encore {remaining:.1f}h"}

        # Refund USDC au buyer
        from solana_tx import send_usdc_transfer
        result = await send_usdc_transfer(
            to_address=escrow["buyer"],
            amount_usdc=escrow["amount_usdc"],
            from_privkey=ESCROW_PRIVKEY_B58,
            from_address=ESCROW_ADDRESS,
        )

        if result.get("success"):
            escrow["status"] = "refunded"
            escrow["refundedAt"] = int(time.time())
            await self._save_escrow(escrow)
            print(f"[EscrowClient] Refunded: {escrow['amount_usdc']} USDC -> {escrow['buyer'][:8]}...")
            return {"success": True, **escrow}
        return {"success": False, "error": f"Refund echoue: {result.get('error')}"}

    async def resolve_dispute(self, escrow_id: str, release_to_seller: bool) -> dict:
        """Admin resout un litige."""
        escrow = self._escrows.get(escrow_id)
        if not escrow:
            return {"success": False, "error": "Escrow introuvable"}
        if escrow["status"] != "locked":
            return {"success": False, "error": f"Status invalide: {escrow['status']}"}

        from solana_tx import send_usdc_transfer
        target = escrow["seller"] if release_to_seller else escrow["buyer"]
        result = await send_usdc_transfer(
            to_address=target,
            amount_usdc=escrow["amount_usdc"],
            from_privkey=ESCROW_PRIVKEY_B58,
            from_address=ESCROW_ADDRESS,
        )

        if result.get("success"):
            escrow["status"] = "released" if release_to_seller else "refunded"
            escrow["resolvedAt"] = int(time.time())
            escrow["resolvedTo"] = "seller" if release_to_seller else "buyer"
            await self._save_escrow(escrow)
            return {"success": True, **escrow}
        return {"success": False, "error": f"Resolution echouee: {result.get('error')}"}

    def get_escrow(self, escrow_id: str) -> dict:
        return self._escrows.get(escrow_id, {"error": "Escrow introuvable"})

    def get_stats(self) -> dict:
        locked = [e for e in self._escrows.values() if e["status"] == "locked"]
        released = [e for e in self._escrows.values() if e["status"] == "released"]
        refunded = [e for e in self._escrows.values() if e["status"] == "refunded"]
        return {
            "total_escrows": len(self._escrows),
            "locked": len(locked),
            "locked_usdc": sum(e["amount_usdc"] for e in locked),
            "released": len(released),
            "released_usdc": sum(e["amount_usdc"] for e in released),
            "refunded": len(refunded),
            "escrow_wallet": ESCROW_ADDRESS,
        }


escrow_client = EscrowClient()
