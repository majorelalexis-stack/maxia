"""MAXIA Art.14 — Kite AI Integration (Agent Identity + Payments + PoAI)"""
import logging
import os, time, hashlib
import httpx, asyncio
from core.config import KITE_API_URL, KITE_API_KEY, KITE_AGENT_ID, KITE_AIR_URL
from core.http_client import get_http_client

logger = logging.getLogger(__name__)


class KiteAIClient:
    """
    Client for Kite AI — The First AI Payment Blockchain.
    - Agent Passport (cryptographic on-chain identity)
    - Agent-to-agent payments (USDC / KITE)
    - Service discovery (Agent App Store)
    - Proof of Attributed Intelligence (PoAI)
    """

    def __init__(self):
        self.api_key = KITE_API_KEY
        self.agent_id = KITE_AGENT_ID
        self._headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}" if self.api_key else "",
        }
        if self.api_key:
            label = f"{self.agent_id[:12]}..." if self.agent_id else "no-agent-id"
            logger.info(f"[KiteAI] Client active ({label})")
        else:
            logger.info("[KiteAI] KITE_API_KEY absent — client inactif")

    # ── Agent Identity (Kite AIR) ──

    async def register_agent(self, name: str, capabilities: list,
                             metadata: dict = None) -> dict:
        payload = {
            "name": name,
            "capabilities": capabilities,
            "platform": "maxia",
            "version": "10.0.0",
            "metadata": metadata or {},
            "permissions": {
                "canTransact": True,
                "maxSpendPerTx": 1000,
                "allowedServices": ["ai_inference", "data_marketplace", "gpu_compute"],
            },
        }
        try:
            client = get_http_client()
            resp = await client.post(
                f"{KITE_AIR_URL}/agents/register",
                json=payload, headers=self._headers,
                timeout=30,
            )
            data = resp.json()
            if resp.status_code in (200, 201) and data.get("agentId"):
                self.agent_id = data["agentId"]
                logger.info(f"[KiteAI] Agent registered: {self.agent_id}")
                return {"success": True, "agentId": self.agent_id, "passport": data.get("passport")}
            return {"success": False, "error": data.get("error", "Registration failed")}
        except Exception as e:
            return {"success": False, "error": "An error occurred"}

    async def verify_agent(self, agent_id: str) -> dict:
        try:
            client = get_http_client()
            resp = await client.get(
                f"{KITE_AIR_URL}/agents/{agent_id}/verify",
                headers=self._headers,
            )
            data = resp.json()
            return {
                "verified": data.get("verified", False),
                "agentId": agent_id,
                "name": data.get("name"),
                "reputation": data.get("reputation", 0),
                "capabilities": data.get("capabilities", []),
            }
        except Exception as e:
            return {"verified": False, "error": "An error occurred"}

    # ── Payments ──

    async def create_payment(self, to_agent: str, amount_usdc: float,
                             purpose: str = "service") -> dict:
        payload = {
            "fromAgent": self.agent_id,
            "toAgent": to_agent,
            "amount": str(amount_usdc),
            "currency": "USDC",
            "purpose": purpose,
            "metadata": {"platform": "maxia", "timestamp": int(time.time())},
        }
        try:
            client = get_http_client()
            resp = await client.post(
                f"{KITE_API_URL}/payments/create",
                json=payload, headers=self._headers,
                timeout=30,
            )
            data = resp.json()
            if resp.status_code in (200, 201):
                return {
                    "success": True,
                    "paymentId": data.get("paymentId"),
                    "txHash": data.get("txHash", ""),
                    "status": data.get("status", "pending"),
                    "network": "kite-mainnet",
                }
            return {"success": False, "error": data.get("error", "Payment failed")}
        except Exception as e:
            return {"success": False, "error": "An error occurred"}

    async def verify_payment(self, payment_id: str) -> dict:
        try:
            client = get_http_client()
            resp = await client.get(
                f"{KITE_API_URL}/payments/{payment_id}",
                headers=self._headers,
            )
            data = resp.json()
            return {
                "valid": data.get("status") == "confirmed",
                "paymentId": payment_id,
                "status": data.get("status"),
                "amount": data.get("amount"),
                "network": "kite-mainnet",
            }
        except Exception as e:
            return {"valid": False, "error": "An error occurred"}

    # ── Service Discovery ──

    async def discover_services(self, category: str = None,
                                max_price: float = None) -> list:
        params: dict = {}
        if category:
            params["category"] = category
        if max_price is not None:
            params["maxPrice"] = str(max_price)
        try:
            client = get_http_client()
            resp = await client.get(
                f"{KITE_API_URL}/services/discover",
                params=params, headers=self._headers,
            )
            data = resp.json()
            return data.get("services", []) if resp.status_code == 200 else []
        except Exception:
            return []

    async def register_service(self, name: str, description: str,
                               price_usdc: float,
                               category: str = "ai_inference") -> dict:
        payload = {
            "agentId": self.agent_id,
            "name": name,
            "description": description,
            "priceUsdc": price_usdc,
            "category": category,
            "platform": "maxia",
            "endpoint": os.getenv("MAXIA_PUBLIC_URL", "http://localhost:8001"),
        }
        try:
            client = get_http_client()
            resp = await client.post(
                f"{KITE_API_URL}/services/register",
                json=payload, headers=self._headers,
            )
            data = resp.json()
            return {"success": resp.status_code in (200, 201), **data}
        except Exception as e:
            return {"success": False, "error": "An error occurred"}

    # ── PoAI (Proof of Attributed Intelligence) ──

    async def report_contribution(self, task_id: str, result_hash: str,
                                  model_used: str = "gemini-2.0-flash") -> dict:
        payload = {
            "agentId": self.agent_id,
            "taskId": task_id,
            "resultHash": result_hash,
            "model": model_used,
            "timestamp": int(time.time()),
            "platform": "maxia",
        }
        try:
            client = get_http_client()
            resp = await client.post(
                f"{KITE_API_URL}/poai/contribute",
                json=payload, headers=self._headers,
            )
            data = resp.json()
            return {"success": resp.status_code in (200, 201), **data}
        except Exception as e:
            return {"success": False, "error": "An error occurred"}


kite_client = KiteAIClient()
