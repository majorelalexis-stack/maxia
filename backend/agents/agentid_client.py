"""MAXIA AgentID Client — Cryptographic identity + trust levels for AI agents.

AgentID (getagentid.dev) fournit :
  - Identite cryptographique verifiable pour chaque agent
  - Trust levels L0-L4 bases sur l'historique on-chain
  - Blockchain receipts pour chaque transaction

Integration MAXIA :
  - L0-L1 : escrow complet (48h hold)
  - L2 : escrow reduit (24h hold)
  - L3-L4 : paiement direct, pas d'escrow
  - Chaque transaction genere un receipt AgentID
"""
import os
import time
import logging
import httpx
from core.error_utils import safe_error
from core.http_client import get_http_client

log = logging.getLogger("agentid")

AGENTID_API_URL = os.getenv("AGENTID_API_URL", "https://api.getagentid.dev")
AGENTID_API_KEY = os.getenv("AGENTID_API_KEY", "")
AGENTID_ENABLED = os.getenv("AGENTID_ENABLED", "false").lower() == "true"

# Cache local pour eviter de spammer l'API
_trust_cache: dict = {}  # address -> {level, ts}
_CACHE_TTL = 3600  # 1h

# Escrow rules par trust level
ESCROW_RULES = {
    0: {"hold_hours": 48, "escrow_required": True,  "label": "Unverified"},
    1: {"hold_hours": 48, "escrow_required": True,  "label": "Basic"},
    2: {"hold_hours": 24, "escrow_required": True,  "label": "Verified"},
    3: {"hold_hours": 0,  "escrow_required": False, "label": "Trusted"},
    4: {"hold_hours": 0,  "escrow_required": False, "label": "Established"},
}

log.info(f"[AgentID] {'Enabled' if AGENTID_ENABLED else 'Disabled'} — API key {'present' if AGENTID_API_KEY else 'absent'}")


class AgentIDClient:

    def __init__(self):
        self.api_url = AGENTID_API_URL
        self.api_key = AGENTID_API_KEY
        self.enabled = AGENTID_ENABLED

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    async def _request(self, method: str, path: str, json_data: dict = None) -> dict:
        if not self.enabled:
            return {"error": "AgentID disabled"}
        url = f"{self.api_url}{path}"
        try:
            client = get_http_client()
            resp = await client.request(method, url, json=json_data, headers=self._headers(), timeout=10)
            if resp.status_code >= 400:
                return {"error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
            return resp.json()
        except Exception as e:
            return safe_error(e, "agentid_request")

    async def get_trust_level(self, address: str) -> int:
        """Retourne le trust level (0-4) d'un agent. Cache 1h. Fallback L0."""
        if not self.enabled or not address:
            return 0
        # Cache check
        cached = _trust_cache.get(address)
        if cached and time.time() - cached["ts"] < _CACHE_TTL:
            return cached["level"]
        # API call
        result = await self._request("GET", f"/v1/agents/{address}/trust")
        level = result.get("trust_level", result.get("level", 0))
        if isinstance(level, str):
            level = int(level.replace("L", "")) if level.startswith("L") else 0
        level = max(0, min(4, int(level)))
        # Cache
        _trust_cache[address] = {"level": level, "ts": time.time()}
        return level

    async def verify_agent(self, address: str) -> dict:
        """Verifie l'identite complete d'un agent."""
        if not self.enabled:
            return {"verified": False, "level": 0, "reason": "AgentID disabled"}
        result = await self._request("GET", f"/v1/agents/{address}")
        if "error" in result:
            return {"verified": False, "level": 0, "reason": result["error"]}
        return {
            "verified": True,
            "level": result.get("trust_level", 0),
            "name": result.get("name", ""),
            "created_at": result.get("created_at", ""),
            "tx_count": result.get("transaction_count", 0),
            "chains": result.get("chains", []),
        }

    async def register_agent(self, address: str, metadata: dict) -> dict:
        """Enregistre MAXIA ou un agent du marketplace sur AgentID."""
        if not self.enabled:
            return {"success": False, "error": "AgentID disabled"}
        return await self._request("POST", "/v1/agents/register", {
            "address": address,
            "name": metadata.get("name", "MAXIA Agent"),
            "description": metadata.get("description", ""),
            "url": metadata.get("url", "https://maxiaworld.app"),
            "chains": metadata.get("chains", ["solana"]),
            "capabilities": metadata.get("capabilities", []),
        })

    async def create_receipt(self, tx_hash: str, buyer: str, seller: str,
                             amount: float, chain: str = "solana") -> dict:
        """Cree un receipt blockchain pour une transaction MAXIA."""
        if not self.enabled:
            return {"success": False, "error": "AgentID disabled"}
        return await self._request("POST", "/v1/receipts", {
            "tx_hash": tx_hash,
            "buyer": buyer,
            "seller": seller,
            "amount": amount,
            "currency": "USDC",
            "chain": chain,
            "marketplace": "maxia",
            "marketplace_url": "https://maxiaworld.app",
        })

    def get_escrow_rules(self, trust_level: int) -> dict:
        """Retourne les regles d'escrow pour un trust level."""
        return ESCROW_RULES.get(trust_level, ESCROW_RULES[0])

    async def get_agent_badge(self, address: str) -> dict:
        """Retourne les infos de badge pour affichage frontend."""
        level = await self.get_trust_level(address)
        rules = self.get_escrow_rules(level)
        colors = {0: "#94A3B8", 1: "#F59E0B", 2: "#3B82F6", 3: "#10B981", 4: "#8B5CF6"}
        return {
            "level": level,
            "label": rules["label"],
            "color": colors.get(level, "#94A3B8"),
            "escrow_required": rules["escrow_required"],
            "hold_hours": rules["hold_hours"],
        }


# Singleton
agentid = AgentIDClient()
