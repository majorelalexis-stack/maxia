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
        # #8: Bounded in-memory storage limits
        self._max_mandates = 10000
        self._max_completed = 5000
        # #5: Warn if AP2_SIGNING_KEY is not set
        if not AP2_SIGNING_KEY:
            print("[AP2] WARNING: AP2_SIGNING_KEY not set — signatures will be weak")
        if AP2_ENABLED:
            print(f"[AP2] Manager active (agent: {AP2_AGENT_ID})")
        else:
            print("[AP2] Manager disabled")

    # ── Intent Mandates ──

    def create_intent_mandate(self, user_wallet: str, max_amount: float = 1000.0,
                              categories: list = None,
                              ttl_seconds: int = 3600) -> dict:
        # #8: Evict expired mandates when storage limit exceeded
        if len(self._active_mandates) > self._max_mandates:
            now = int(time.time())
            expired = [k for k, v in self._active_mandates.items()
                       if v.get("constraints", {}).get("validUntil", 0) < now]
            for k in expired:
                del self._active_mandates[k]

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
        # #13: Network validation
        from config import SUPPORTED_NETWORKS
        if network not in SUPPORTED_NETWORKS:
            return {"success": False, "error": "Unsupported network"}

        # 1. validate signature (#10: generic error message)
        if not self._verify_sig(intent_mandate):
            return {"success": False, "error": "Invalid signature"}

        # 2. check expiry (#11: 5 second grace period for race conditions)
        constraints = intent_mandate.get("constraints", {})
        if int(time.time()) > constraints.get("validUntil", 0) + 5:
            return {"success": False, "error": "Mandate expired"}

        # 3. validate cart
        amount = 0.0
        if cart_mandate:
            # #2/#3: Verify cart signature
            if not self._verify_sig(cart_mandate):
                return {"success": False, "error": "Invalid cart signature"}
            if cart_mandate.get("intentMandateId") != intent_mandate.get("mandateId"):
                # #10: Generic error message
                return {"success": False, "error": "Invalid request"}
            amount = cart_mandate.get("totalUsdc", 0)
            if amount > constraints.get("maxAmount", 0):
                return {"success": False, "error": "Cart exceeds mandate limit"}

        # #4: Zero amount validation
        if amount <= 0:
            return {"success": False, "error": "Payment amount must be positive"}

        # #9: Fraud prevention — validate intent creator matches payment wallet
        intent_wallet = intent_mandate.get("userId", "")
        payment_wallet = payment_payload.get("wallet", "") if isinstance(payment_payload, dict) else ""
        if intent_wallet and payment_wallet and intent_wallet != payment_wallet:
            return {"success": False, "error": "Payment wallet does not match intent creator"}

        # 4. verify on-chain payment
        tx_payload = payment_payload if isinstance(payment_payload, str) else (payment_payload.get("txHash", "") if isinstance(payment_payload, dict) else "")
        pay_ok = await self._verify_onchain(tx_payload, network, amount)
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
        # #8: Trim completed list when exceeding limit
        if len(self._completed) > self._max_completed:
            self._completed = self._completed[-self._max_completed:]
        return {"success": True, **completion}

    # ── Outgoing AP2 ──

    async def pay_external(self, service_url: str, amount_usdc: float,
                           user_wallet: str, provider_wallet: str = "",
                           purpose: str = "ai_service",
                           from_privkey: str = "") -> dict:
        """Paiement AP2 sortant avec vraie transaction on-chain."""
        # #1: Validate provider_wallet is specified
        if not provider_wallet:
            return {"success": False, "error": "provider_wallet required"}

        # #14: Use treasury/micro wallet if no privkey provided
        if not from_privkey:
            from config import MICRO_WALLET_PRIVKEY, MICRO_WALLET_ADDRESS
            if MICRO_WALLET_PRIVKEY:
                from_privkey = MICRO_WALLET_PRIVKEY
                user_wallet = MICRO_WALLET_ADDRESS
            else:
                return {"success": False, "error": "No wallet configured for outgoing payments"}

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
        # #1: Send to provider_wallet, not user_wallet
        from solana_tx import send_usdc_transfer
        tx_result = await send_usdc_transfer(
            to_address=provider_wallet,
            amount_usdc=amount_usdc,
            from_privkey=from_privkey,
            from_address=user_wallet,
        )
        if not tx_result.get("success"):
            return {"success": False, "error": f"Transaction echouee: {tx_result.get('error')}"}
        tx_signature = tx_result.get("signature", "")

        if not tx_signature:
            return {"success": False, "error": "Transaction did not return a signature"}

        try:
            # #12: Reduced timeout to 15s for external calls
            async with httpx.AsyncClient(timeout=15) as client:
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
            "supportedNetworks": ["solana-mainnet", "base-mainnet", "ethereum-mainnet"],
            "supportedCurrencies": ["USDC", "SOL", "ETH"],
            "capabilities": [
                "ai_inference", "gpu_compute", "data_marketplace",
                "code_audit", "image_generation",
            ],
            # #17: ETH threshold / min amounts in info
            "constraints": {
                "ethereum_min_usdc": 10,
            },
            "active": AP2_ENABLED,
            "activeMandates": len(self._active_mandates),
            "completedPayments": len(self._completed),
        }

    # #16: Stats with pagination limit parameter
    def get_stats(self, limit: int = 10) -> dict:
        total_vol = sum(c.get("amount", 0) for c in self._completed)
        return {
            "activeMandates": len(self._active_mandates),
            "completedPayments": len(self._completed),
            "totalVolumeUsdc": total_vol,
            "recentPayments": self._completed[-limit:],
        }

    # ── Internal ──

    def _sign(self, data: dict) -> str:
        if not AP2_SIGNING_KEY:
            raise ValueError("AP2_SIGNING_KEY requis pour signer les mandats AP2")
        key = AP2_SIGNING_KEY.encode()
        payload = json.dumps(data, sort_keys=True, default=str).encode()
        return hmac.new(key, payload, hashlib.sha256).hexdigest()

    # #15: Thread-safe signature verification — copy before popping
    def _verify_sig(self, mandate: dict) -> bool:
        mandate_copy = dict(mandate)
        sig = mandate_copy.pop("signature", mandate_copy.pop("merchantSignature", ""))
        if not sig:
            return False
        expected = self._sign(mandate_copy)
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
                # #10: Explicit error if Base treasury is not configured
                if not TREASURY_ADDRESS_BASE:
                    return {"valid": False, "error": "Base L2 treasury not configured (TREASURY_ADDRESS_BASE missing)"}
                result = await verify_usdc_transfer_base(
                    tx_hash=payment_payload,
                    expected_amount_raw=int(expected * 1e6) if expected else None,
                    expected_recipient=TREASURY_ADDRESS_BASE,
                )
                result["txHash"] = payment_payload
                return result
            if "ethereum" in network:
                from eth_verifier import verify_usdc_transfer_eth
                from config import TREASURY_ADDRESS_ETH
                result = await verify_usdc_transfer_eth(
                    tx_hash=payment_payload,
                    expected_amount_raw=int(expected * 1e6) if expected else None,
                    expected_recipient=TREASURY_ADDRESS_ETH,
                )
                result["txHash"] = payment_payload
                return result
            return {"valid": False, "error": "Unsupported network"}
        except Exception as e:
            return {"valid": False, "error": str(e)}


ap2_manager = AP2Manager()
