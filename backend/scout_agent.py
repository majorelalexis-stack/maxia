"""MAXIA SCOUT Agent — Prospection IA-to-IA sur 4 chains

Scanne Solana, Base (L2), Ethereum mainnet, et XRP Ledger pour trouver
des agents IA autonomes deployes, puis les contacte machine-to-machine
pour qu'ils s'inscrivent sur MAXIA comme acheteurs ou vendeurs.

Protocoles cibles :
  Solana   : ElizaOS, SendAI, Clockwork, Switchboard
  Ethereum : Autonolas/Olas, Fetch.ai, SingularityNET, Lit Protocol
  Base     : Coinbase AgentKit, Based Agents, Virtuals Protocol

Contact methods :
  - API publique de l'agent (si decouverte via registry)
  - On-chain memo (Solana)
  - Smart contract interaction log → identify owner wallet
"""
import asyncio, time, json
from datetime import date
import httpx

from config import (
    get_rpc_url, ETH_RPC, BASE_RPC, GROQ_API_KEY, GROQ_MODEL,
    MARKETING_WALLET_ADDRESS,
)
from alerts import alert_system, alert_error

MAXIA_URL = "maxiaworld.app"

# ══════════════════════════════════════════
# Known AI agent protocols & contracts
# ══════════════════════════════════════════

# Solana programs associated with AI agents
SOLANA_AI_PROGRAMS = {
    "ELZAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx": "ElizaOS",
    "CLoCKyJ6DXBhqMJ8NwfD6gFS3FAhKdiQRYem8yKc8is": "Clockwork",
    "SW1TCH7qEPTdLsDHRgPuMQjbQxKdH2aBStViMFnt64f": "Switchboard",
    "SENDxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx": "SendAI",
}

# Ethereum contracts for AI agent protocols
ETH_AI_CONTRACTS = {
    # Autonolas / Olas — Service Registry
    "0x48b6af7B12C71f09e2fC8aF4855De4Ff54e002c7": {
        "name": "Autonolas Service Registry",
        "type": "registry",
        "scan_method": "logs",
    },
    # Autonolas — Component Registry
    "0xE3607b00E75f6405248323A9417ff6b39B244b50": {
        "name": "Autonolas Component Registry",
        "type": "registry",
        "scan_method": "logs",
    },
    # Fetch.ai — FET Staking (agents stake to operate)
    "0xaea46A60368A7bD060eec7DF8CBa43b7EF41Ad85": {
        "name": "Fetch.ai FET Token",
        "type": "token",
        "scan_method": "transfers",
    },
    # SingularityNET — AGIX Token
    "0x5B7533812759B45C2B44C19e320ba2cD2681b542": {
        "name": "SingularityNET AGIX",
        "type": "token",
        "scan_method": "transfers",
    },
    # Lit Protocol — PKP NFT (programmable key pairs = agent wallets)
    "0x8F75a53F65e31DD0D2e40d0827becAaE2E1d382b": {
        "name": "Lit Protocol PKP NFT",
        "type": "nft",
        "scan_method": "logs",
    },
}

# Base L2 contracts for AI agents
BASE_AI_CONTRACTS = {
    # Virtuals Protocol — AI agent token factory
    "0x44e09c0A7Eb39dBC0653e7b0e240a4dA1Bd8DE37": {
        "name": "Virtuals Protocol",
        "type": "factory",
        "scan_method": "logs",
    },
}

# Known AI agent registries (HTTP APIs)
AI_REGISTRIES = [
    {
        "name": "Autonolas Registry",
        "url": "https://registry.olas.network/api/services",
        "type": "olas",
        "chain": "ethereum",
    },
]

# ERC-20 Transfer topic
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

groq_client = None
if GROQ_API_KEY:
    try:
        from groq import Groq
        groq_client = Groq(api_key=GROQ_API_KEY)
    except Exception:
        pass


# ══════════════════════════════════════════
# SCOUT Agent Class
# ══════════════════════════════════════════

class ScoutAgent:
    def __init__(self):
        self._discovered: dict = {}      # {address: {chain, protocol, contacted, ...}}
        self._contacted_today: list = []
        self._daily_date: str = ""
        self._running: bool = False
        self._total_discovered = 0
        self._total_contacted = 0
        self._max_contacts_day = 15
        self._max_contacts_per_agent = 2
        print("[SCOUT] Agent IA-to-IA prospection initialise (Solana + Base + Ethereum + XRP)")

    async def run(self):
        """Boucle principale — scan toutes les 6 heures."""
        self._running = True
        print(f"[SCOUT] Demarre — scan 4 chains, max {self._max_contacts_day} contacts/jour")
        await alert_system(
            "SCOUT Agent IA-to-IA demarre",
            f"Scan: Solana + Base + Ethereum + XRP\n"
            f"Cibles: ElizaOS, Autonolas, Fetch.ai, SingularityNET, Virtuals\n"
            f"Max {self._max_contacts_day} contacts/jour"
        )
        while self._running:
            try:
                self._reset_daily()
                agents = await self.scan_all_chains()
                for agent_info in agents:
                    if not self._can_contact():
                        break
                    await self._contact_agent(agent_info)
                    await asyncio.sleep(5)
            except Exception as e:
                print(f"[SCOUT] Erreur boucle: {e}")
                await alert_error("SCOUT", str(e))
            await asyncio.sleep(21600)  # 6 heures

    def stop(self):
        self._running = False

    # ══════════════════════════════════════════
    # Scan — 4 chains
    # ══════════════════════════════════════════

    async def scan_all_chains(self) -> list:
        """Scan les 4 chains en parallele pour trouver des agents IA."""
        results = await asyncio.gather(
            self._scan_solana(),
            self._scan_ethereum(),
            self._scan_base(),
            self._scan_registries(),
            return_exceptions=True,
        )
        agents = []
        for r in results:
            if isinstance(r, list):
                agents.extend(r)
            elif isinstance(r, Exception):
                print(f"[SCOUT] Scan error: {r}")
        # Deduplicate by address
        seen = set()
        unique = []
        for a in agents:
            addr = a.get("address", "").lower()
            if addr and addr not in seen:
                seen.add(addr)
                unique.append(a)
        self._total_discovered += len(unique)
        print(f"[SCOUT] {len(unique)} agents IA trouves sur 4 chains")
        return unique

    async def _scan_solana(self) -> list:
        """Scan les programmes IA connus sur Solana."""
        agents = []
        rpc = get_rpc_url()
        for program, protocol in SOLANA_AI_PROGRAMS.items():
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.post(rpc, json={
                        "jsonrpc": "2.0", "id": 1,
                        "method": "getSignaturesForAddress",
                        "params": [program, {"limit": 10}],
                    })
                    sigs = resp.json().get("result", [])
                    for sig_info in sigs[:5]:
                        sig = sig_info.get("signature", "")
                        if not sig:
                            continue
                        resp2 = await client.post(rpc, json={
                            "jsonrpc": "2.0", "id": 1,
                            "method": "getTransaction",
                            "params": [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
                        })
                        tx = resp2.json().get("result", {})
                        if tx:
                            accounts = tx.get("transaction", {}).get("message", {}).get("accountKeys", [])
                            if accounts:
                                signer = accounts[0]
                                if isinstance(signer, dict):
                                    signer = signer.get("pubkey", "")
                                if signer and signer != MARKETING_WALLET_ADDRESS:
                                    agents.append({
                                        "address": signer,
                                        "chain": "solana",
                                        "protocol": protocol,
                                        "type": "agent_operator",
                                        "contact_method": "solana_memo",
                                    })
                        await asyncio.sleep(0.3)
            except Exception as e:
                print(f"[SCOUT] Solana scan {protocol} error: {e}")
        return agents

    async def _scan_ethereum(self) -> list:
        """Scan les contrats IA sur Ethereum mainnet."""
        agents = []
        for contract, info in ETH_AI_CONTRACTS.items():
            try:
                from eth_verifier import get_contract_logs
                logs = await get_contract_logs(contract)
                wallets = set()
                for log in logs[:20]:
                    topics = log.get("topics", [])
                    # Extract interacting wallets from topics
                    for t in topics[1:]:
                        if len(t) == 66:  # 0x + 64 hex chars
                            addr = "0x" + t[-40:]
                            if addr != "0x" + "0" * 40:
                                wallets.add(addr)
                    # Also check 'from' in tx
                    tx_hash = log.get("transactionHash", "")
                    if tx_hash and len(wallets) < 10:
                        # Get tx sender
                        try:
                            async with httpx.AsyncClient(timeout=10) as client:
                                resp = await client.post(ETH_RPC, json={
                                    "jsonrpc": "2.0", "id": 1,
                                    "method": "eth_getTransactionByHash",
                                    "params": [tx_hash],
                                })
                                tx = resp.json().get("result", {})
                                if tx and tx.get("from"):
                                    wallets.add(tx["from"])
                        except Exception:
                            pass

                for wallet in list(wallets)[:5]:
                    agents.append({
                        "address": wallet,
                        "chain": "ethereum",
                        "protocol": info["name"],
                        "type": info["type"],
                        "contract": contract,
                        "contact_method": "api_or_onchain",
                    })
            except Exception as e:
                print(f"[SCOUT] ETH scan {info['name']} error: {e}")
        return agents

    async def _scan_base(self) -> list:
        """Scan les contrats IA sur Base L2."""
        agents = []
        for contract, info in BASE_AI_CONTRACTS.items():
            try:
                # Same pattern as ETH but using BASE_RPC
                params = {
                    "address": contract,
                    "fromBlock": "latest",
                    "toBlock": "latest",
                }
                payload = {
                    "jsonrpc": "2.0", "id": 1,
                    "method": "eth_getLogs",
                    "params": [params],
                }
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.post(BASE_RPC, json=payload)
                    logs = resp.json().get("result", [])
                    wallets = set()
                    for log in logs[:20]:
                        topics = log.get("topics", [])
                        for t in topics[1:]:
                            if len(t) == 66:
                                addr = "0x" + t[-40:]
                                if addr != "0x" + "0" * 40:
                                    wallets.add(addr)
                    for wallet in list(wallets)[:5]:
                        agents.append({
                            "address": wallet,
                            "chain": "base",
                            "protocol": info["name"],
                            "type": info["type"],
                            "contact_method": "api_or_onchain",
                        })
            except Exception as e:
                print(f"[SCOUT] Base scan {info['name']} error: {e}")
        return agents

    async def _scan_registries(self) -> list:
        """Scan les registries HTTP d'agents IA (Autonolas, etc)."""
        agents = []
        for registry in AI_REGISTRIES:
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.get(registry["url"], params={"limit": 20})
                    if resp.status_code != 200:
                        continue
                    data = resp.json()
                    services = data if isinstance(data, list) else data.get("results", data.get("services", []))
                    for svc in services[:10]:
                        # Olas format
                        owner = svc.get("owner", svc.get("agent_address", ""))
                        name = svc.get("name", svc.get("description", ""))
                        svc_id = svc.get("id", svc.get("service_id", ""))
                        if owner:
                            agents.append({
                                "address": owner,
                                "chain": registry["chain"],
                                "protocol": registry["name"],
                                "type": "registered_service",
                                "service_name": str(name)[:100],
                                "service_id": str(svc_id),
                                "contact_method": "api",
                                "registry_url": registry["url"],
                            })
            except Exception as e:
                print(f"[SCOUT] Registry scan {registry['name']} error: {e}")
        return agents

    # ══════════════════════════════════════════
    # Contact — Machine-to-Machine
    # ══════════════════════════════════════════

    async def _contact_agent(self, agent_info: dict):
        """Contacte un agent IA decouvert."""
        address = agent_info.get("address", "")
        chain = agent_info.get("chain", "")
        protocol = agent_info.get("protocol", "")

        if not self._can_contact_agent(address):
            return

        method = agent_info.get("contact_method", "")

        if method == "solana_memo" and chain == "solana":
            await self._contact_via_solana_memo(address, protocol)
        elif method in ("api", "api_or_onchain"):
            await self._contact_via_api(agent_info)
        else:
            # Fallback: log discovery for manual follow-up
            self._register_discovery(address, chain, protocol, contacted=False)
            return

        self._register_discovery(address, chain, protocol, contacted=True)

    async def _contact_via_solana_memo(self, wallet: str, protocol: str):
        """Contact un agent Solana via memo transfer."""
        message = await self._generate_m2m_message(protocol, "solana")
        try:
            from solana_tx import send_memo_transfer
            memo = f"MAXIA_M2M | {message[:380]}"
            result = await send_memo_transfer(wallet, 0.00001, memo)
            if result.get("success"):
                self._contacted_today.append(wallet)
                self._total_contacted += 1
                print(f"[SCOUT] Solana memo -> {wallet[:12]}... ({protocol})")
        except Exception as e:
            print(f"[SCOUT] Memo contact error: {e}")

    async def _contact_via_api(self, agent_info: dict):
        """Try to contact an AI agent via its public API or registry."""
        address = agent_info.get("address", "")
        protocol = agent_info.get("protocol", "")
        chain = agent_info.get("chain", "")

        # Try known API patterns for agent protocols
        api_endpoints = self._get_api_endpoints(agent_info)

        for endpoint in api_endpoints:
            try:
                payload = {
                    "from": "MAXIA",
                    "type": "marketplace_invitation",
                    "message": f"MAXIA AI Marketplace — sell your services, earn USDC. Free registration.",
                    "register_url": f"https://{MAXIA_URL}/api/public/register",
                    "docs_url": f"https://{MAXIA_URL}/api/public/docs",
                    "chain": chain,
                    "benefits": [
                        "Sell any AI service to other agents",
                        "Get paid in USDC automatically",
                        "0.5% commission only",
                        "One API call to register",
                    ],
                }
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.post(endpoint, json=payload)
                    if resp.status_code in (200, 201, 202):
                        self._contacted_today.append(address)
                        self._total_contacted += 1
                        print(f"[SCOUT] API contact -> {address[:12]}... ({protocol}) via {endpoint}")
                        return
            except Exception:
                continue

        # If no API worked, log as discovered but not contacted
        print(f"[SCOUT] No API reachable for {address[:12]}... ({protocol}), logged for manual follow-up")

    def _get_api_endpoints(self, agent_info: dict) -> list:
        """Determine possible API endpoints for an agent."""
        endpoints = []
        protocol = agent_info.get("protocol", "")

        # Olas agents often have endpoints in their service metadata
        if "olas" in protocol.lower() or "autonolas" in protocol.lower():
            svc_id = agent_info.get("service_id", "")
            if svc_id:
                endpoints.append(f"https://registry.olas.network/api/services/{svc_id}/endpoints")

        return endpoints

    async def _generate_m2m_message(self, protocol: str, chain: str) -> str:
        """Generate a machine-readable + human-readable outreach message."""
        base_msg = (
            f"MAXIA AI Marketplace on {chain.title()}. "
            f"Your {protocol} agent can sell services to other AI agents. "
            f"Register free: {MAXIA_URL}/api/public/register — "
            f"POST /sell = live in 2s. Earn USDC. 0.5% fee only."
        )

        if not groq_client:
            return base_msg

        try:
            prompt = (
                f"Write a SHORT outreach memo (max 180 chars) to an AI agent operator.\n"
                f"Their agent runs on {protocol} ({chain}).\n"
                f"MAXIA = AI-to-AI marketplace. Their agent can sell services and earn USDC.\n"
                f"Registration: one POST request. Commission: 0.5%.\n"
                f"URL: {MAXIA_URL}\n"
                f"Talk dev-to-dev. Be specific about {protocol} integration.\n"
                f"Max 180 chars. English only."
            )

            def _call():
                resp = groq_client.chat.completions.create(
                    model=GROQ_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=80, temperature=0.7,
                )
                return resp.choices[0].message.content.strip()

            result = await asyncio.to_thread(_call)
            return result if len(result) > 20 else base_msg
        except Exception:
            return base_msg

    # ══════════════════════════════════════════
    # Internal tracking
    # ══════════════════════════════════════════

    def _register_discovery(self, address: str, chain: str, protocol: str, contacted: bool):
        """Track a discovered agent."""
        key = address.lower()
        if key not in self._discovered:
            self._discovered[key] = {
                "address": address,
                "chain": chain,
                "protocol": protocol,
                "first_seen": int(time.time()),
                "contact_count": 0,
            }
        if contacted:
            self._discovered[key]["contact_count"] += 1
            self._discovered[key]["last_contacted"] = int(time.time())

    def _can_contact_agent(self, address: str) -> bool:
        """Check if we can contact this agent (dedup + rate limit)."""
        key = address.lower()
        info = self._discovered.get(key)
        if not info:
            return True
        if info["contact_count"] >= self._max_contacts_per_agent:
            return False
        last = info.get("last_contacted", 0)
        if last and (time.time() - last) < 86400 * 7:  # 7 day cooldown
            return False
        return True

    def _can_contact(self) -> bool:
        return len(self._contacted_today) < self._max_contacts_day

    def _reset_daily(self):
        today = date.today().isoformat()
        if self._daily_date != today:
            if self._daily_date and self._contacted_today:
                print(f"[SCOUT] Bilan {self._daily_date}: {len(self._contacted_today)} agents contactes")
            self._daily_date = today
            self._contacted_today = []

    # ══════════════════════════════════════════
    # Stats (for CEO/WATCHDOG)
    # ══════════════════════════════════════════

    def get_stats(self) -> dict:
        by_chain = {}
        by_protocol = {}
        for info in self._discovered.values():
            chain = info.get("chain", "unknown")
            proto = info.get("protocol", "unknown")
            by_chain[chain] = by_chain.get(chain, 0) + 1
            by_protocol[proto] = by_protocol.get(proto, 0) + 1

        return {
            "running": self._running,
            "total_discovered": self._total_discovered,
            "unique_agents": len(self._discovered),
            "total_contacted": self._total_contacted,
            "contacted_today": len(self._contacted_today),
            "max_per_day": self._max_contacts_day,
            "by_chain": by_chain,
            "by_protocol": by_protocol,
            "chains": ["solana", "base", "ethereum"],
            "protocols_watched": (
                list(SOLANA_AI_PROGRAMS.values())
                + [v["name"] for v in ETH_AI_CONTRACTS.values()]
                + [v["name"] for v in BASE_AI_CONTRACTS.values()]
            ),
        }


scout_agent = ScoutAgent()
