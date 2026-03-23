"""MAXIA Art.16 V11 — Cross-Chain Bridge Handler (via Li.Fi API)"""
import os, time, uuid, asyncio, logging
import httpx
from config import (
    LIFI_API_URL, BRIDGE_ENABLED, TREASURY_ADDRESS,
    TREASURY_ADDRESS_BASE, TREASURY_ADDRESS_ETH,
)

logger = logging.getLogger("maxia.cross_chain")

# #8: Bridge slippage configurable via env var (default 1%)
BRIDGE_SLIPPAGE_PCT = float(os.getenv("BRIDGE_SLIPPAGE_PCT", "1.0"))


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

        # Pick the right treasury based on destination chain
        dest_chain = to_chain.lower()
        if dest_chain in ("ethereum", "eth", "1"):
            to_address = TREASURY_ADDRESS_ETH
        elif dest_chain in ("base", "8453"):
            to_address = TREASURY_ADDRESS_BASE
        else:
            to_address = TREASURY_ADDRESS

        params = {
            "fromChain": from_chain,
            "toChain": to_chain,
            "fromToken": from_token,
            "toToken": to_token,
            "fromAmount": amount,
            "fromAddress": from_address,
            "toAddress": to_address,
            "slippage": str(BRIDGE_SLIPPAGE_PCT / 100),  # #8: configurable slippage
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

        # Verifier le solde actuel de la treasury Solana
        from solana_tx import get_sol_balance
        balance = await get_sol_balance(TREASURY_ADDRESS)
        bridge["treasury_balance_check"] = balance

        # #12: Also check Base treasury balance if configured
        if TREASURY_ADDRESS_BASE:
            try:
                from base_verifier import _rpc_post
                payload = {
                    "jsonrpc": "2.0", "id": 1,
                    "method": "eth_getBalance",
                    "params": [TREASURY_ADDRESS_BASE, "latest"],
                }
                data = await _rpc_post(payload)
                base_balance_wei = int(data.get("result", "0x0"), 16)
                bridge["base_treasury_balance_eth"] = base_balance_wei / 1e18
            except Exception as e:
                logger.warning(f"[CrossChain] Failed to check Base treasury balance: {e}")
                bridge["base_treasury_balance_eth"] = None

        bridge["last_check"] = int(time.time())

        return bridge

    async def confirm_bridge(self, bridge_id: str, tx_signature: str) -> dict:
        """
        Confirme un bridge UNIQUEMENT si la transaction est verifiee on-chain.
        Le client envoie la signature de la tx de reception.
        Supporte verification sur Solana, Base, et Ethereum.
        """
        bridge = self._pending_bridges.get(bridge_id)
        if not bridge:
            return {"success": False, "error": "Bridge introuvable"}

        to_chain = bridge.get("toChain", "solana").lower()
        expected_amount = float(bridge.get("estimatedOutput", 0)) / 1e6 if bridge.get("estimatedOutput") else 0

        if to_chain in ("ethereum", "eth", "1"):
            # #13: Validate ETH treasury is configured before attempting verification
            if not TREASURY_ADDRESS_ETH:
                return {"success": False, "error": "ETH treasury not configured (TREASURY_ADDRESS_ETH missing). Cannot verify Ethereum bridge."}
            # Verify on Ethereum mainnet
            from eth_verifier import verify_usdc_transfer_eth
            tx_result = await verify_usdc_transfer_eth(
                tx_hash=tx_signature,
                expected_amount_raw=int(expected_amount * 0.95 * 1e6),
                expected_recipient=TREASURY_ADDRESS_ETH,
            )
        elif to_chain in ("base", "8453"):
            # Verify on Base L2
            from base_verifier import verify_usdc_transfer_base
            tx_result = await verify_usdc_transfer_base(
                tx_hash=tx_signature,
                expected_amount_raw=int(expected_amount * 0.95 * 1e6),
                expected_recipient=TREASURY_ADDRESS_BASE,
            )
        elif to_chain in ("near",):
            from near_verifier import verify_near_transaction
            tx_result = await verify_near_transaction(
                tx_hash=tx_signature, sender_id="",
                expected_dest=os.getenv("TREASURY_ADDRESS_NEAR", ""),
                expected_amount=expected_amount * 0.95,
            )
        elif to_chain in ("aptos",):
            from aptos_verifier import verify_aptos_transaction
            tx_result = await verify_aptos_transaction(
                tx_hash=tx_signature,
                expected_dest=os.getenv("TREASURY_ADDRESS_APTOS", ""),
                expected_amount=expected_amount * 0.95,
            )
        elif to_chain in ("sei", "1329"):
            from sei_verifier import verify_usdc_transfer_sei
            tx_result = await verify_usdc_transfer_sei(
                tx_hash=tx_signature,
                expected_amount_raw=int(expected_amount * 0.95 * 1e6),
                expected_recipient=os.getenv("TREASURY_ADDRESS_SEI", ""),
            )
        else:
            # Default: verify on Solana
            from solana_verifier import verify_transaction
            tx_result = await verify_transaction(
                tx_signature=tx_signature,
                expected_amount_usdc=expected_amount * 0.95,
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
                {"from": "ethereum", "to": "solana", "tokens": ["USDC", "ETH", "USDT"], "provider": "Li.Fi"},
                {"from": "base", "to": "solana", "tokens": ["USDC", "ETH"], "provider": "Li.Fi"},
                {"from": "arbitrum", "to": "solana", "tokens": ["USDC", "ETH"], "provider": "Li.Fi"},
                {"from": "solana", "to": "ethereum", "tokens": ["USDC", "SOL"], "provider": "Li.Fi"},
                {"from": "solana", "to": "base", "tokens": ["USDC", "SOL"], "provider": "Li.Fi"},
                {"from": "base", "to": "ethereum", "tokens": ["USDC", "ETH"], "provider": "Li.Fi"},
                {"from": "ethereum", "to": "base", "tokens": ["USDC", "ETH"], "provider": "Li.Fi"},
            ],
            "treasury_wallets": {
                "solana": TREASURY_ADDRESS,
                "base": TREASURY_ADDRESS_BASE,
                "ethereum": TREASURY_ADDRESS_ETH,
            },
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
