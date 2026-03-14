"""MAXIA Art.17 V11 — Reputation Staking (persiste en base de donnees)"""
import uuid, time, json
from config import STAKING_MIN_USDC, STAKING_SLASH_PCT, STAKING_DISPUTE_DELAY


class ReputationStaking:
    def __init__(self):
        self._db = None
        print(f"[Staking] Actif — min {STAKING_MIN_USDC} USDC, slash {STAKING_SLASH_PCT}%, delai {STAKING_DISPUTE_DELAY}h")

    def set_db(self, db):
        self._db = db

    async def stake(self, wallet: str, amount_usdc: float,
                    tx_signature: str) -> dict:
        if amount_usdc < STAKING_MIN_USDC:
            return {"success": False, "error": f"Minimum {STAKING_MIN_USDC} USDC requis"}

        from solana_verifier import verify_transaction
        tx_ok = await verify_transaction(tx_signature, wallet)
        if not tx_ok:
            return {"success": False, "error": "Transaction invalide"}

        stake_info = {
            "stakeId": str(uuid.uuid4()),
            "wallet": wallet,
            "amount": amount_usdc,
            "txSignature": tx_signature,
            "status": "active",
            "stakedAt": int(time.time()),
            "reputation": 100,
        }

        if self._db:
            await self._db.save_stake(stake_info)
            await self._db.record_transaction(wallet, tx_signature, amount_usdc, "reputation_stake")

        print(f"[Staking] Stake {amount_usdc} USDC par {wallet[:8]}...")
        return {"success": True, **stake_info}

    async def get_stake(self, wallet: str) -> dict:
        if self._db:
            stake = await self._db.get_stake(wallet)
            if stake:
                return stake
        return {"status": "none", "amount": 0}

    async def open_dispute(self, reporter_wallet: str, accused_wallet: str,
                           reason: str, evidence: str = "") -> dict:
        stake = await self.get_stake(accused_wallet)
        if not stake or stake.get("status") != "active":
            return {"success": False, "error": "Vendeur sans stake actif"}

        dispute = {
            "disputeId": str(uuid.uuid4()),
            "reporter": reporter_wallet,
            "accused": accused_wallet,
            "reason": reason,
            "evidence": evidence,
            "status": "pending",
            "openedAt": int(time.time()),
            "resolvesAt": int(time.time()) + STAKING_DISPUTE_DELAY * 3600,
            "slashAmount": stake["amount"] * STAKING_SLASH_PCT / 100,
        }

        if self._db:
            await self._db.save_dispute(dispute)

        print(f"[Staking] Dispute: {reporter_wallet[:8]}... vs {accused_wallet[:8]}...")
        return {"success": True, **dispute}

    async def resolve_dispute(self, dispute_id: str, slash: bool) -> dict:
        if not self._db:
            return {"success": False, "error": "DB non connectee"}

        dispute = await self._db.get_dispute(dispute_id)
        if not dispute:
            return {"success": False, "error": "Dispute introuvable"}
        if dispute["status"] != "pending":
            return {"success": False, "error": f"Dispute deja {dispute['status']}"}
        if time.time() < dispute["resolvesAt"] and slash:
            remaining_h = (dispute["resolvesAt"] - time.time()) / 3600
            return {"success": False, "error": f"Delai non ecoule — encore {remaining_h:.1f}h"}

        if slash:
            stake = await self._db.get_stake(dispute["accused"])
            if stake:
                slash_amount = stake["amount"] * STAKING_SLASH_PCT / 100
                stake["amount"] -= slash_amount
                stake["reputation"] = max(0, stake.get("reputation", 100) - 50)
                if stake["amount"] < STAKING_MIN_USDC:
                    stake["status"] = "insufficient"
                await self._db.save_stake(stake)
            dispute["status"] = "slashed"
        else:
            dispute["status"] = "dismissed"

        dispute["resolvedAt"] = int(time.time())
        await self._db.save_dispute(dispute)
        return {"success": True, "status": dispute["status"], "dispute": dispute}

    async def get_stats(self) -> dict:
        if not self._db:
            return {"total_stakers": 0, "total_staked_usdc": 0}
        try:
            stakes = await self._db.get_all_stakes()
            active = [s for s in stakes if s.get("status") == "active"]
            disputes = await self._db.get_all_disputes()
            pending = [d for d in disputes if d.get("status") == "pending"]
            slashed = [d for d in disputes if d.get("status") == "slashed"]
            return {
                "total_stakers": len(active),
                "total_staked_usdc": sum(s.get("amount", 0) for s in active),
                "pending_disputes": len(pending),
                "total_slashed": sum(d.get("slashAmount", 0) for d in slashed),
                "min_stake_usdc": STAKING_MIN_USDC,
                "slash_pct": STAKING_SLASH_PCT,
                "dispute_delay_h": STAKING_DISPUTE_DELAY,
            }
        except Exception:
            return {"total_stakers": 0, "total_staked_usdc": 0}


reputation_staking = ReputationStaking()
