"""MAXIA Art.16 V11 — Cross-Chain Bridge Handler (via Li.Fi API)"""
import os, time, uuid, asyncio
import httpx
from config import (
    LIFI_API_URL, BRIDGE_ENABLED, TREASURY_ADDRESS,
    TREASURY_ADDRESS_BASE,
)


class CrossChainHandler:
    """
    Bridge de paiement cross-chain via Li.Fi.
    Flux: Client paie ETH/USDC sur Base/Ethereum -> Li.Fi bridge ->
          USDC arrive sur wallet Solana -> Oracle MAXIA valide.

    SECURITE: On ne valide JAMAIS sur la promesse du bridge.
    On attend que les fonds arrivent reellement sur notre wallet Solana
    avant de crediter le client.
    """

    def __init__(self):
        self._pending_bridges: dict = {}
        self._completed: list = []
        if BRIDGE_ENABLED:
            print("[CrossChain] Bridge Li.Fi actif")
        else:
            print("[CrossChain] Bridge desactive")

    async def get_quote(self, from_chain: str, from_token: str,
                        to_chain: str, to_token: str,
                        amount: str, from_address: str) -> dict:
        """Obtenir un devis de bridge via Li.Fi."""
        if not BRIDGE_ENABLED:
            return {"error": "Bridge desactive"}

        params = {
            "fromChain": from_chain,
            "toChain": to_chain,
            "fromToken": from_token,
            "toToken": to_token,
            "fromAmount": amount,
            "fromAddress": from_address,
            "toAddress": TREASURY_ADDRESS,
            "slippage": "0.01",
        }

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"{LIFI_API_URL}/quote",
                    params=params,
                )
                data = resp.json()

            if resp.status_code == 200 and data.get("estimate"):
                bridge_id = str(uuid.uuid4())
                estimate = data["estimate"]
                quote = {
                    "bridgeId": bridge_id,
                    "fromChain": from_chain,
                    "toChain": to_chain,
                    "fromAmount": amount,
                    "estimatedOutput": estimate.get("toAmount", "0"),
                    "estimatedFees": estimate.get("feeCosts", []),
                    "estimatedTime": estimate.get("executionDuration", 0),
                    "tool": data.get("tool", "unknown"),
                    "status": "quoted",
                    "createdAt": int(time.time()),
                }
                self._pending_bridges[bridge_id] = quote
                return quote
            return {"error": data.get("message", "Quote echouee")}
        except Exception as e:
            return {"error": f"Li.Fi erreur: {e}"}

    async def check_bridge_status(self, bridge_id: str) -> dict:
        """Verifie le statut d'un bridge en cours."""
        bridge = self._pending_bridges.get(bridge_id)
        if not bridge:
            return {"error": "Bridge introuvable"}

        # Verifier le solde actuel de la treasury
        from solana_tx import get_sol_balance
        balance = await get_sol_balance(TREASURY_ADDRESS)

        bridge["treasury_balance_check"] = balance
        bridge["last_check"] = int(time.time())

        return bridge

    async def confirm_bridge(self, bridge_id: str, tx_signature: str) -> dict:
        """
        Confirme un bridge UNIQUEMENT si la transaction est verifiee on-chain.
        Le client envoie la signature de la tx de reception.
        """
        bridge = self._pending_bridges.get(bridge_id)
        if not bridge:
            return {"success": False, "error": "Bridge introuvable"}

        # VERIFIER ON-CHAIN que les fonds sont arrives au treasury avec le bon montant
        from solana_verifier import verify_transaction
        expected_amount = float(bridge.get("estimatedOutput", 0)) / 1e6 if bridge.get("estimatedOutput") else 0
        tx_result = await verify_transaction(
            tx_signature=tx_signature,
            expected_amount_usdc=expected_amount * 0.95,  # 5% slippage tolerance pour bridge
            expected_recipient=TREASURY_ADDRESS,
        )

        if not tx_result.get("valid"):
            return {"success": False, "error": f"Transaction non verifiee: {tx_result.get('error', 'fonds non recus')}"}

        bridge["status"] = "confirmed"
        bridge["txSignature"] = tx_signature
        bridge["confirmedAt"] = int(time.time())
        self._completed.append(bridge)
        del self._pending_bridges[bridge_id]

        print(f"[CrossChain] Bridge {bridge_id[:8]}... confirme (tx: {tx_signature[:12]}...)")
        return {"success": True, **bridge}

    async def test_connection(self) -> dict:
        """Teste la connexion a Li.Fi API."""
        if not BRIDGE_ENABLED:
            return {"ok": False, "error": "Bridge desactive"}
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{LIFI_API_URL}/chains")
                data = resp.json()
            chains = data.get("chains", [])
            return {
                "ok": True,
                "chains_available": len(chains),
                "api_url": LIFI_API_URL,
                "status": "Li.Fi API connectee",
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def get_supported_routes(self) -> dict:
        return {
            "enabled": BRIDGE_ENABLED,
            "routes": [
                {
                    "from": "ethereum",
                    "to": "solana",
                    "tokens": ["USDC", "ETH", "USDT"],
                    "provider": "Li.Fi",
                },
                {
                    "from": "base",
                    "to": "solana",
                    "tokens": ["USDC", "ETH"],
                    "provider": "Li.Fi",
                },
                {
                    "from": "arbitrum",
                    "to": "solana",
                    "tokens": ["USDC", "ETH"],
                    "provider": "Li.Fi",
                },
            ],
            "destination_wallet": TREASURY_ADDRESS,
            "security": "On-chain verification required — no trust assumptions",
        }

    def get_stats(self) -> dict:
        return {
            "enabled": BRIDGE_ENABLED,
            "pending": len(self._pending_bridges),
            "completed": len(self._completed),
            "total_bridged": sum(
                float(b.get("estimatedOutput", 0)) for b in self._completed
            ),
        }


cross_chain = CrossChainHandler()
