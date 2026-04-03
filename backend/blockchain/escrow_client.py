"""MAXIA Art.21 V12 — Escrow Client (wallet-based avec persistance DB)
Production-hardened: race-condition locks, DB-as-truth, input validation,
safe SQL, audit trail, int amounts, releasing-state crash recovery.

SECURITE ESCROW WALLET :
  - ESCROW_ADDRESS tient les fonds clients, SEPARE du treasury
  - ESCROW_PRIVKEY_B58 ne doit JAMAIS etre dans le code, uniquement en .env
  - En prod : utiliser un hardware wallet ou KMS (AWS/GCP)
  - Verification au demarrage : adresse valide, cle correspond a l'adresse
"""
import uuid, time, json, asyncio, re, logging
import httpx
import base58

logger = logging.getLogger(__name__)
from core.config import (
    get_rpc_url, TREASURY_ADDRESS, ESCROW_ADDRESS, ESCROW_PRIVKEY_B58,
)
from infra.alerts import alert_system, alert_error

USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

# S1 audit fix: distributed lock via Redis (multi-worker safe)
# Falls back to asyncio.Lock if Redis unavailable
_escrow_lock_local = asyncio.Lock()


async def _acquire_escrow_lock(escrow_id: str, timeout_ms: int = 10000) -> bool:
    """Acquire a distributed lock for an escrow operation via Redis SET NX PX.

    E-4 fix: if Redis unavailable, use local asyncio.Lock (NOT return True blindly).
    """
    try:
        from core.redis_client import redis_client
        if redis_client and redis_client.is_connected:
            key = f"escrow_lock:{escrow_id}"
            acquired = await redis_client._redis.set(key, "1", nx=True, px=timeout_ms)
            return bool(acquired)
    except Exception as e:
        logger.warning("[Escrow] Redis lock unavailable: %s — using local lock", e)
    # Fallback: local asyncio lock (safe for single worker, best-effort for multi)
    await _escrow_lock_local.acquire()
    return True


async def _release_escrow_lock(escrow_id: str):
    """Release the distributed escrow lock (Redis + local fallback)."""
    try:
        from core.redis_client import redis_client
        if redis_client and redis_client.is_connected:
            await redis_client._redis.delete(f"escrow_lock:{escrow_id}")
    except Exception as e:
        logger.warning("[Escrow] Redis lock release failed: %s", e)
    # Also release local lock if held
    if _escrow_lock_local.locked():
        try:
            _escrow_lock_local.release()
        except RuntimeError:
            pass


class _DistributedEscrowLock:
    """Async context manager: Redis distributed lock with local fallback."""
    def __init__(self, escrow_id: str):
        self._id = escrow_id
        self._use_local = False

    async def __aenter__(self):
        acquired = await _acquire_escrow_lock(self._id)
        if not acquired:
            raise RuntimeError("Escrow operation in progress, please retry")
        return self

    async def __aexit__(self, *args):
        await _release_escrow_lock(self._id)


# Drop-in replacement: _escrow_lock usage changes from
#   async with _escrow_lock:  →  async with _DistributedEscrowLock(escrow_id):

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
            logger.critical("ERREUR CRITIQUE: %s", e)
    else:
        logger.info("Config OK: %s... (separe du treasury)", ESCROW_ADDRESS[:8])
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
        self._disabled = bool(_escrow_errors)
        if self._disabled:
            logger.error("DESACTIVE — %d erreur(s) de config. Aucune operation escrow possible.", len(_escrow_errors))
        else:
            logger.info("Initialise (wallet: %s...)", ESCROW_ADDRESS[:16]) if ESCROW_ADDRESS else logger.warning("ATTENTION: ESCROW_ADDRESS non configure")

    def _check_enabled(self) -> dict | None:
        """Retourne une erreur si l'escrow est desactive, None sinon."""
        if self._disabled:
            return {"success": False, "error": "Escrow desactive: config invalide. Verifiez ESCROW_ADDRESS et ESCROW_PRIVKEY_B58 dans .env."}

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
            logger.info("%d escrows actifs charges depuis DB", len(self._escrows))
        except Exception as e:
            logger.warning("Escrow DB load failed (table may not exist yet): %s", e)

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
                logger.error("Erreur sauvegarde DB: %s", e)

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
        except Exception as e:
            logger.warning("Escrow DB reload failed for %s: %s", escrow_id, e)
        return None

    async def create_escrow(self, buyer_wallet: str, seller_wallet: str,
                             amount_usdc: float, service_id: str,
                             tx_signature: str, timeout_hours: int = 72) -> dict:
        """
        Cree un escrow — verifie que les USDC ont ete envoyes au wallet escrow.
        """
        err = self._check_enabled()
        if err:
            return err
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
        # BUG 1 fix: also check escrow_records + NEVER skip on exception
        if self._db:
            try:
                if await self._db.tx_already_processed(tx_signature):
                    return {"success": False, "error": f"Escrow already exists for tx {tx_signature[:16]}..."}
                # Also check escrow_records table for tx_signature
                dup = await self._db._fetchone(
                    "SELECT 1 FROM escrow_records WHERE json_extract(data, '$.tx_signature')=?",
                    (tx_signature,)
                )
                if dup:
                    return {"success": False, "error": f"Escrow already exists for tx {tx_signature[:16]}..."}
            except Exception as e:
                import logging
                logging.getLogger(__name__).error("Escrow idempotency check failed: %s", e)
                return {"success": False, "error": "Escrow verification temporarily unavailable"}

        escrow_id = str(uuid.uuid4())

        # Verifier la transaction de paiement avec montant + destinataire
        from blockchain.solana_verifier import verify_transaction
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

        # Isolation multi-tenant
        from enterprise.tenant_isolation import get_current_tenant
        _tenant_id = get_current_tenant() or "default"

        escrow = {
            "escrowId": escrow_id,
            "buyer": buyer_wallet,
            "seller": seller_wallet,
            "amount_usdc": verified_amount,       # #9: on-chain verified amount
            "amount_raw": verified_raw,            # #8: int micro-USDC
            "serviceId": service_id,
            "txSignature": tx_signature,
            "status": "locked",
            "tenant_id": _tenant_id,
            "createdAt": int(time.time()),
            "timeoutAt": int(time.time()) + int(timeout_hours) * 3600,
            "timeoutHours": int(timeout_hours),
            "verified_amount": verified_amount,
            "verified_from": pay_ok.get("from", ""),
        }
        await self._save_escrow(escrow)

        logger.info("Escrow cree: %s USDC | %s... -> %s...", verified_amount, buyer_wallet[:8], seller_wallet[:8])
        await alert_system(
            "Nouvel Escrow",
            f"**{verified_amount} USDC** verrouilles\n"
            f"Buyer: `{buyer_wallet[:8]}...`\n"
            f"Seller: `{seller_wallet[:8]}...`\n"
            f"Service: {service_id}\n"
            f"Timeout: {timeout_hours}h",
        )

        # Avertissement risque gel USDC par Circle
        return {
            "success": True,
            **escrow,
            "usdc_risk_notice": (
                "USDC is issued by Circle and can be frozen by regulatory order. "
                "In case of a freeze, funds in escrow may be temporarily unavailable. "
                "MAXIA will assist in resolution."
            ),
        }

    async def confirm_delivery(self, escrow_id: str, buyer_wallet: str) -> dict:
        """Buyer confirme la livraison -> USDC liberes au seller."""
        err = self._check_enabled()
        if err:
            return err
        # S1 fix: distributed lock (Redis) — multi-worker safe
        async with _DistributedEscrowLock(escrow_id):
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

            # Commission MAXIA (Art.2)
            from core.config import get_commission_bps, get_commission_tier_name
            amount = escrow["amount_usdc"]
            commission_bps = get_commission_bps(amount)
            commission_usdc = round(amount * commission_bps / 10000, 6)
            seller_gets = round(amount - commission_usdc, 6)
            tier_name = get_commission_tier_name(amount)

            # Envoyer les USDC au seller (montant APRES commission)
            from blockchain.solana_tx import send_usdc_transfer
            result = await send_usdc_transfer(
                to_address=escrow["seller"],
                amount_usdc=seller_gets,
                from_privkey=ESCROW_PRIVKEY_B58,
                from_address=ESCROW_ADDRESS,
            )

            # Envoyer la commission au treasury (si > 0)
            commission_tx = ""
            if result.get("success") and commission_usdc > 0 and TREASURY_ADDRESS:
                try:
                    comm_result = await send_usdc_transfer(
                        to_address=TREASURY_ADDRESS,
                        amount_usdc=commission_usdc,
                        from_privkey=ESCROW_PRIVKEY_B58,
                        from_address=ESCROW_ADDRESS,
                    )
                    commission_tx = comm_result.get("signature", "")
                except Exception as comm_err:
                    # E-2 fix: alert on commission failure (real money lost)
                    logger.error("COMMISSION LOST: %s USDC for escrow %s: %s", commission_usdc, escrow_id, comm_err)
                    try:
                        from infra.alerts import _send_private
                        await _send_private(
                            f"\U0001f534 <b>Commission Lost</b>\n"
                            f"Escrow: <code>{escrow_id[:16]}</code>\n"
                            f"Amount: <code>{commission_usdc} USDC</code>\n"
                            f"Error: <code>{str(comm_err)[:100]}</code>")
                    except Exception as e2:
                        logger.warning("Commission loss alert failed: %s", e2)

            if result.get("success"):
                escrow["status"] = "released"
                escrow["releasedAt"] = int(time.time())
                escrow["releaseTx"] = result.get("signature", "")
                escrow["commission_usdc"] = commission_usdc
                escrow["commission_bps"] = commission_bps
                escrow["commission_tier"] = tier_name
                escrow["seller_gets_usdc"] = seller_gets
                escrow["commission_tx"] = commission_tx
                await self._save_escrow(escrow)
                logger.info("Released: %s USDC -> %s... (commission: %s USDC %s)", seller_gets, escrow['seller'][:8], commission_usdc, tier_name)
                await alert_system(
                    "Escrow libere",
                    f"**{seller_gets} USDC** envoyes au seller `{escrow['seller'][:8]}...`\n"
                    f"Commission: {commission_usdc} USDC ({tier_name} {commission_bps/100}%)",
                )
                return {"success": True, **escrow}
            else:
                # #5: Revert to locked on failure
                escrow["status"] = "locked"
                await self._save_escrow(escrow)
                # #14 / #16: Log and audit failed transfers
                error_msg = result.get("error", "unknown")
                logger.error("FAILED transfer %s: %s", escrow_id, error_msg)
                from core.security import audit_log
                audit_log("escrow_transfer_failed", "system", f"escrow={escrow_id} error={error_msg}")
                return {"success": False, "error": f"Transfer echoue: {error_msg}"}

    async def reclaim_timeout(self, escrow_id: str, buyer_wallet: str) -> dict:
        """Buyer reclame ses fonds apres timeout."""
        err = self._check_enabled()
        if err:
            return err
        # S1 fix: distributed lock (Redis) — multi-worker safe
        async with _DistributedEscrowLock(escrow_id):
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
            from blockchain.solana_tx import send_usdc_transfer
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
                logger.info("Refunded: %s USDC -> %s...", escrow['amount_usdc'], escrow['buyer'][:8])
                return {"success": True, **escrow}

            # Revert to locked on failure
            escrow["status"] = "locked"
            await self._save_escrow(escrow)
            error_msg = result.get("error", "unknown")
            logger.error("FAILED refund %s: %s", escrow_id, error_msg)
            from core.security import audit_log
            audit_log("escrow_refund_failed", "system", f"escrow={escrow_id} error={error_msg}")
            return {"success": False, "error": f"Refund echoue: {error_msg}"}

    async def resolve_dispute(self, escrow_id: str, release_to_seller: bool) -> dict:
        """Admin resout un litige."""
        err = self._check_enabled()
        if err:
            return err
        # S1 fix: distributed lock (Redis) — multi-worker safe
        async with _DistributedEscrowLock(escrow_id):
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

            from blockchain.solana_tx import send_usdc_transfer
            from core.config import get_commission_bps, get_commission_tier_name
            target = escrow["seller"] if release_to_seller else escrow["buyer"]
            amount = escrow["amount_usdc"]

            # Commission only when releasing to seller (not on refunds)
            commission_usdc = 0.0
            seller_gets = amount
            if release_to_seller:
                commission_bps = get_commission_bps(amount)
                commission_usdc = round(amount * commission_bps / 10000, 6)
                seller_gets = round(amount - commission_usdc, 6)

            result = await send_usdc_transfer(
                to_address=target,
                amount_usdc=seller_gets if release_to_seller else amount,
                from_privkey=ESCROW_PRIVKEY_B58,
                from_address=ESCROW_ADDRESS,
            )

            # Send commission to treasury if applicable
            if result.get("success") and commission_usdc > 0 and TREASURY_ADDRESS:
                try:
                    await send_usdc_transfer(
                        to_address=TREASURY_ADDRESS,
                        amount_usdc=commission_usdc,
                        from_privkey=ESCROW_PRIVKEY_B58,
                        from_address=ESCROW_ADDRESS,
                    )
                except Exception:
                    logger.warning("Commission transfer failed in resolve (non-blocking)")

            if result.get("success"):
                escrow["status"] = "released" if release_to_seller else "refunded"
                escrow["resolvedAt"] = int(time.time())
                escrow["resolvedTo"] = "seller" if release_to_seller else "buyer"
                if release_to_seller:
                    escrow["commission_usdc"] = commission_usdc
                    escrow["seller_gets_usdc"] = seller_gets
                await self._save_escrow(escrow)
                return {"success": True, **escrow}

            # Revert to locked on failure
            escrow["status"] = "locked"
            await self._save_escrow(escrow)
            error_msg = result.get("error", "unknown")
            logger.error("FAILED resolution %s: %s", escrow_id, error_msg)
            from core.security import audit_log
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
