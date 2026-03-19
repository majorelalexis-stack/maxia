"""MAXIA Art.15 — Google AP2 (Agent Payments Protocol) Manager"""
import os, uuid, time, json, hashlib, hmac
import httpx, asyncio
from config import AP2_ENABLED, AP2_AGENT_ID, AP2_SIGNING_KEY


class AP2Manager:
    """
    Google AP2 — open protocol for AI-agent commerce.
    - Intent Mandates  : cryptographic proof of user authorization
    - Cart Mandates    : final purchase approval with item details
    - Multi-rail       : Solana, Base, fiat-compatible
    - Interoperable    : works alongside x402, A2A, MCP
    """

    def __init__(self):
        self._active_mandates: dict = {}
        self._completed: list = []
        if AP2_ENABLED:
            print(f"[AP2] Manager active (agent: {AP2_AGENT_ID})")
        else:
            print("[AP2] Manager disabled")

    # ── Intent Mandates ──

    def create_intent_mandate(self, user_wallet: str, max_amount: float = 1000.0,
                              categories: list = None,
                              ttl_seconds: int = 3600) -> dict:
        mandate = {
            "mandateId": str(uuid.uuid4()),
            "agentId": AP2_AGENT_ID,
            "userId": user_wallet,
            "action": "purchase",
            "constraints": {
                "maxAmount": max_amount,
                "currency": "USDC",
                "allowedCategories": categories or ["ai_service", "gpu_compute", "data"],
                "validUntil": int(time.time()) + ttl_seconds,
            },
            "createdAt": int(time.time()),
        }
        mandate["signature"] = self._sign(mandate)
        self._active_mandates[mandate["mandateId"]] = mandate
        return mandate

    # ── Cart Mandates ──

    def create_cart_mandate(self, intent_mandate_id: str, items: list,
                            total_usdc: float,
                            payment_method: str = "usdc_solana") -> dict:
        intent = self._active_mandates.get(intent_mandate_id)
        if not intent:
            return {"error": "Intent mandate not found"}
        constraints = intent["constraints"]
        if total_usdc > constraints["maxAmount"]:
            return {"error": f"Amount {total_usdc} exceeds limit {constraints['maxAmount']}"}
        if int(time.time()) > constraints["validUntil"]:
            return {"error": "Intent mandate expired"}

        cart = {
            "mandateId": str(uuid.uuid4()),
            "intentMandateId": intent_mandate_id,
            "merchantId": "maxia-marketplace",
            "items": items,
            "totalUsdc": total_usdc,
            "paymentMethod": payment_method,
            "createdAt": int(time.time()),
        }
        cart["merchantSignature"] = self._sign(cart)
        return cart

    # ── Process Incoming AP2 Payment ──

    async def process_payment(self, intent_mandate: dict,
                              cart_mandate: dict = None,
                              payment_payload: str = None,
                              network: str = "solana-mainnet") -> dict:
        # 1. validate signature
        if not self._verify_sig(intent_mandate):
            return {"success": False, "error": "Invalid intent mandate signature"}

        # 2. check expiry
        constraints = intent_mandate.get("constraints", {})
        if int(time.time()) > constraints.get("validUntil", 0):
            return {"success": False, "error": "Mandate expired"}

        # 3. validate cart
        amount = 0.0
        if cart_mandate:
            if cart_mandate.get("intentMandateId") != intent_mandate.get("mandateId"):
                return {"success": False, "error": "Cart does not reference intent"}
            amount = cart_mandate.get("totalUsdc", 0)
            if amount > constraints.get("maxAmount", 0):
                return {"success": False, "error": "Cart exceeds mandate limit"}

        # 4. verify on-chain payment
        pay_ok = await self._verify_onchain(payment_payload, network, amount)
        if not pay_ok.get("valid"):
            return {"success": False, "error": pay_ok.get("error", "Payment verification failed")}

        # 5. record
        completion = {
            "completionId": str(uuid.uuid4()),
            "intentMandateId": intent_mandate.get("mandateId"),
            "agentId": intent_mandate.get("agentId"),
            "amount": amount,
            "network": network,
            "txHash": pay_ok.get("txHash", ""),
            "completedAt": int(time.time()),
        }
        self._completed.append(completion)
        return {"success": True, **completion}

    # ── Outgoing AP2 ──

    async def pay_external(self, service_url: str, amount_usdc: float,
                           user_wallet: str, from_privkey: str = "",
                           purpose: str = "ai_service") -> dict:
        """Paiement AP2 sortant avec vraie transaction on-chain."""
        intent = self.create_intent_mandate(
            user_wallet=user_wallet,
            max_amount=amount_usdc * 1.1,
            categories=[purpose],
        )
        cart = self.create_cart_mandate(
            intent_mandate_id=intent["mandateId"],
            items=[{"service": service_url, "amount": amount_usdc}],
            total_usdc=amount_usdc,
        )
        if "error" in cart:
            return {"success": False, **cart}

        # Creer une vraie transaction USDC on-chain
        tx_signature = ""
        if from_privkey:
            from solana_tx import send_usdc_transfer
            tx_result = await send_usdc_transfer(
                to_address=user_wallet,
                amount_usdc=amount_usdc,
                from_privkey=from_privkey,
                from_address=user_wallet,
            )
            if not tx_result.get("success"):
                return {"success": False, "error": f"Transaction echouee: {tx_result.get('error')}"}
            tx_signature = tx_result.get("signature", "")

        if not tx_signature:
            return {"success": False, "error": "Cle privee requise pour creer une transaction reelle"}

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    service_url,
                    json={
                        "ap2Version": "1.0",
                        "intentMandate": intent,
                        "cartMandate": cart,
                        "paymentPayload": tx_signature,
                        "network": "solana-mainnet",
                    },
                )
                data = resp.json()
            return {"success": resp.status_code in (200, 201), "txSignature": tx_signature, "response": data}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── Info / Stats ──

    def get_info(self) -> dict:
        return {
            "ap2Version": "1.0",
            "agentId": AP2_AGENT_ID,
            "platform": "maxia",
            "supportedNetworks": ["solana-mainnet", "base-mainnet"],
            "supportedCurrencies": ["USDC", "SOL"],
            "capabilities": [
                "ai_inference", "gpu_compute", "data_marketplace",
                "code_audit", "image_generation",
            ],
            "active": AP2_ENABLED,
            "activeMandates": len(self._active_mandates),
            "completedPayments": len(self._completed),
        }

    def get_stats(self) -> dict:
        total_vol = sum(c.get("amount", 0) for c in self._completed)
        return {
            "activeMandates": len(self._active_mandates),
            "completedPayments": len(self._completed),
            "totalVolumeUsdc": total_vol,
            "recentPayments": self._completed[-10:],
        }

    # ── Internal ──

    def _sign(self, data: dict) -> str:
        if not AP2_SIGNING_KEY:
            raise ValueError("AP2_SIGNING_KEY requis pour signer les mandats AP2")
        key = AP2_SIGNING_KEY.encode()
        payload = json.dumps(data, sort_keys=True, default=str).encode()
        return hmac.new(key, payload, hashlib.sha256).hexdigest()

    def _verify_sig(self, mandate: dict) -> bool:
        sig = mandate.pop("signature", "")
        if not sig:
            return False
        expected = self._sign(mandate)
        mandate["signature"] = sig          # restore
        return hmac.compare_digest(sig, expected)

    async def _verify_onchain(self, payment_payload: str,
                               network: str, expected: float) -> dict:
        if not payment_payload:
            return {"valid": False, "error": "No payment payload"}
        try:
            if "solana" in network:
                from solana_verifier import verify_transaction
                from config import TREASURY_ADDRESS
                result = await verify_transaction(
                    tx_signature=payment_payload,
                    expected_amount_usdc=expected,
                    expected_recipient=TREASURY_ADDRESS,
                )
                result["txHash"] = payment_payload
                return result
            if "base" in network:
                from base_verifier import verify_usdc_transfer_base
                from config import TREASURY_ADDRESS_BASE
                result = await verify_usdc_transfer_base(
                    tx_hash=payment_payload,
                    expected_amount_raw=int(expected * 1e6) if expected else None,
                    expected_recipient=TREASURY_ADDRESS_BASE,
                )
                result["txHash"] = payment_payload
                return result
            return {"valid": False, "error": f"Unsupported network: {network}"}
        except Exception as e:
            return {"valid": False, "error": str(e)}


ap2_manager = AP2Manager()
