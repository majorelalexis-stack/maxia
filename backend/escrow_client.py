"""MAXIA Art.21 V12 — Escrow Client (wallet-based avec persistance DB)
Production-hardened: race-condition locks, DB-as-truth, input validation,
safe SQL, audit trail, int amounts, releasing-state crash recovery.

SECURITE ESCROW WALLET :
  - ESCROW_ADDRESS tient les fonds clients, SEPARE du treasury
  - ESCROW_PRIVKEY_B58 ne doit JAMAIS etre dans le code, uniquement en .env
  - En prod : utiliser un hardware wallet ou KMS (AWS/GCP)
  - Verification au demarrage : adresse valide, cle correspond a l'adresse
"""
import uuid, time, json, asyncio, re
import httpx
import base58
from config import (
    get_rpc_url, TREASURY_ADDRESS, ESCROW_ADDRESS, ESCROW_PRIVKEY_B58,
)
from alerts import alert_system, alert_error

USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

# #2: Global asyncio lock — prevents double-spend race conditions
_escrow_lock = asyncio.Lock()

# Solana base58 address regex (#3 / #13)
_SOLANA_ADDR_RE = re.compile(r'^[1-9A-HJ-NP-Za-km-z]{32,44}$')


def _verify_escrow_config():
    """Verifie au demarrage que le wallet escrow est correctement configure.
    CRITIQUE : sans ca, les fonds clients pourraient etre perdus."""
    errors = []
    if not ESCROW_ADDRESS:
        errors.append("ESCROW_ADDRESS non defini dans .env")
    elif not _SOLANA_ADDR_RE.match(ESCROW_ADDRESS):
        errors.append(f"ESCROW_ADDRESS invalide: {ESCROW_ADDRESS[:10]}...")
    if not ESCROW_PRIVKEY_B58:
        errors.append("ESCROW_PRIVKEY_B58 non defini dans .env")
    else:
        # Verifier que la cle privee correspond a l'adresse
        try:
            from nacl.signing import SigningKey
            privkey_bytes = base58.b58decode(ESCROW_PRIVKEY_B58)
            if len(privkey_bytes) == 64:
                signing_key = SigningKey(privkey_bytes[:32])
            else:
                signing_key = SigningKey(privkey_bytes)
            pubkey = base58.b58encode(bytes(signing_key.verify_key)).decode()
            if ESCROW_ADDRESS and pubkey != ESCROW_ADDRESS:
                errors.append(f"ESCROW_PRIVKEY ne correspond PAS a ESCROW_ADDRESS (got {pubkey[:10]}... vs {ESCROW_ADDRESS[:10]}...)")
        except ImportError:
            pass  # nacl non installe, skip la verification
        except Exception as e:
            errors.append(f"ESCROW_PRIVKEY invalide: {e}")
    if ESCROW_ADDRESS and TREASURY_ADDRESS and ESCROW_ADDRESS == TREASURY_ADDRESS:
        errors.append("ESCROW_ADDRESS == TREASURY_ADDRESS — ils doivent etre DIFFERENTS pour la securite")
    if errors:
        for e in errors:
            print(f"[ESCROW] ERREUR CRITIQUE: {e}")
    else:
        print(f"[ESCROW] Config OK: {ESCROW_ADDRESS[:8]}... (separe du treasury)")
    return errors


# Verification au chargement du module
_escrow_errors = _verify_escrow_config()


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
                "SELECT data FROM escrow_records WHERE status IN ('locked', 'releasing')")
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

    # #10: Load single escrow from DB (source of truth)
    async def _load_escrow_from_db(self, escrow_id: str) -> dict | None:
        """Reload a single escrow from DB to get latest state."""
        if not self._db:
            return None
        try:
            rows = await self._db.raw_execute_fetchall(
                "SELECT data FROM escrow_records WHERE escrow_id = ?",
                (escrow_id,))
            if rows:
                return json.loads(rows[0]["data"])
        except Exception:
            pass
        return None

    async def create_escrow(self, buyer_wallet: str, seller_wallet: str,
                             amount_usdc: float, service_id: str,
                             tx_signature: str, timeout_hours: int = 72) -> dict:
        """
        Cree un escrow — verifie que les USDC ont ete envoyes au wallet escrow.
        """
        if not ESCROW_ADDRESS:
            return {"success": False, "error": "ESCROW_ADDRESS non configure"}

        # #3 / #13: Validate seller_wallet (Solana address format)
        if not seller_wallet or len(seller_wallet) < 32 or len(seller_wallet) > 44:
            return {"success": False, "error": "Invalid seller wallet address"}
        if not _SOLANA_ADDR_RE.match(seller_wallet):
            return {"success": False, "error": "Invalid Solana address format"}
        if seller_wallet == buyer_wallet:
            return {"success": False, "error": "Seller cannot be the same as buyer"}
        if seller_wallet == ESCROW_ADDRESS:
            return {"success": False, "error": "Seller cannot be the escrow address"}

        # #6: Validate timeout_hours (1-168h = 7 days max)
        if not isinstance(timeout_hours, (int, float)) or timeout_hours < 1 or timeout_hours > 168:
            return {"success": False, "error": "timeout_hours must be between 1 and 168 (7 days max)"}

        # #8: Convert to int micro-USDC for precision
        amount_raw = int(round(amount_usdc * 1_000_000))
        if amount_raw <= 0:
            return {"success": False, "error": "amount_usdc must be positive"}

        # V-10: Use tx_already_processed (indexed column) instead of LIKE on JSON
        if self._db:
            try:
                if await self._db.tx_already_processed(tx_signature):
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

        # #9: Use the VERIFIED on-chain amount, not the requested amount
        verified_amount = pay_ok.get("amount_usdc", amount_usdc)
        verified_raw = int(round(verified_amount * 1_000_000))

        escrow = {
            "escrowId": escrow_id,
            "buyer": buyer_wallet,
            "seller": seller_wallet,
            "amount_usdc": verified_amount,       # #9: on-chain verified amount
            "amount_raw": verified_raw,            # #8: int micro-USDC
            "serviceId": service_id,
            "txSignature": tx_signature,
            "status": "locked",
            "createdAt": int(time.time()),
            "timeoutAt": int(time.time()) + int(timeout_hours) * 3600,
            "timeoutHours": int(timeout_hours),
            "verified_amount": verified_amount,
            "verified_from": pay_ok.get("from", ""),
        }
        await self._save_escrow(escrow)

        print(f"[EscrowClient] Escrow cree: {verified_amount} USDC | {buyer_wallet[:8]}... -> {seller_wallet[:8]}...")
        await alert_system(
            "Nouvel Escrow",
            f"**{verified_amount} USDC** verrouilles\n"
            f"Buyer: `{buyer_wallet[:8]}...`\n"
            f"Seller: `{seller_wallet[:8]}...`\n"
            f"Service: {service_id}\n"
            f"Timeout: {timeout_hours}h",
        )

        return {"success": True, **escrow}

    async def confirm_delivery(self, escrow_id: str, buyer_wallet: str) -> dict:
        """Buyer confirme la livraison -> USDC liberes au seller."""
        # #2: Lock to prevent race condition / double-spend
        async with _escrow_lock:
            # #10: Reload from DB (source of truth) before acting
            db_escrow = await self._load_escrow_from_db(escrow_id)
            if db_escrow:
                escrow = db_escrow
                self._escrows[escrow_id] = escrow
            else:
                escrow = self._escrows.get(escrow_id)

            if not escrow:
                return {"success": False, "error": "Escrow introuvable"}
            if escrow["status"] != "locked":
                return {"success": False, "error": f"Status invalide: {escrow['status']}"}
            if escrow["buyer"] != buyer_wallet:
                return {"success": False, "error": "Seul le buyer peut confirmer"}

            # #5: Save "releasing" state BEFORE sending transfer (crash recovery)
            escrow["status"] = "releasing"
            await self._save_escrow(escrow)

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
                # #5: Revert to locked on failure
                escrow["status"] = "locked"
                await self._save_escrow(escrow)
                # #14 / #16: Log and audit failed transfers
                error_msg = result.get("error", "unknown")
                print(f"[Escrow] FAILED transfer {escrow_id}: {error_msg}")
                from security import audit_log
                audit_log("escrow_transfer_failed", "system", f"escrow={escrow_id} error={error_msg}")
                return {"success": False, "error": f"Transfer echoue: {error_msg}"}

    async def reclaim_timeout(self, escrow_id: str, buyer_wallet: str) -> dict:
        """Buyer reclame ses fonds apres timeout."""
        # #2: Lock to prevent race condition / double-spend
        async with _escrow_lock:
            # #10: Reload from DB (source of truth) before acting
            db_escrow = await self._load_escrow_from_db(escrow_id)
            if db_escrow:
                escrow = db_escrow
                self._escrows[escrow_id] = escrow
            else:
                escrow = self._escrows.get(escrow_id)

            if not escrow:
                return {"success": False, "error": "Escrow introuvable"}
            if escrow["status"] != "locked":
                return {"success": False, "error": f"Status invalide: {escrow['status']}"}
            if escrow["buyer"] != buyer_wallet:
                return {"success": False, "error": "Seul le buyer peut reclamer"}

            # #12: Validate timeoutAt exists and is valid
            timeout_at = escrow.get("timeoutAt")
            if not timeout_at or not isinstance(timeout_at, (int, float)):
                return {"success": False, "error": "Invalid escrow state: missing timeout"}

            if time.time() < timeout_at:
                remaining = (timeout_at - time.time()) / 3600
                return {"success": False, "error": f"Timeout non atteint — encore {remaining:.1f}h"}

            # #5: Save "releasing" state BEFORE sending transfer
            escrow["status"] = "releasing"
            await self._save_escrow(escrow)

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

            # Revert to locked on failure
            escrow["status"] = "locked"
            await self._save_escrow(escrow)
            error_msg = result.get("error", "unknown")
            print(f"[Escrow] FAILED refund {escrow_id}: {error_msg}")
            from security import audit_log
            audit_log("escrow_refund_failed", "system", f"escrow={escrow_id} error={error_msg}")
            return {"success": False, "error": f"Refund echoue: {error_msg}"}

    async def resolve_dispute(self, escrow_id: str, release_to_seller: bool) -> dict:
        """Admin resout un litige."""
        # #2: Lock to prevent race condition / double-spend
        async with _escrow_lock:
            # #10: Reload from DB (source of truth)
            db_escrow = await self._load_escrow_from_db(escrow_id)
            if db_escrow:
                escrow = db_escrow
                self._escrows[escrow_id] = escrow
            else:
                escrow = self._escrows.get(escrow_id)

            if not escrow:
                return {"success": False, "error": "Escrow introuvable"}
            if escrow["status"] != "locked":
                return {"success": False, "error": f"Status invalide: {escrow['status']}"}

            # #5: Save "releasing" state BEFORE transfer
            escrow["status"] = "releasing"
            await self._save_escrow(escrow)

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

            # Revert to locked on failure
            escrow["status"] = "locked"
            await self._save_escrow(escrow)
            error_msg = result.get("error", "unknown")
            print(f"[Escrow] FAILED resolution {escrow_id}: {error_msg}")
            from security import audit_log
            audit_log("escrow_resolution_failed", "system", f"escrow={escrow_id} to={'seller' if release_to_seller else 'buyer'} error={error_msg}")
            return {"success": False, "error": f"Resolution echouee: {error_msg}"}

    def get_escrow(self, escrow_id: str) -> dict:
        return self._escrows.get(escrow_id, {"error": "Escrow introuvable"})

    def get_stats(self) -> dict:
        locked = [e for e in self._escrows.values() if e["status"] in ("locked", "releasing")]
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
