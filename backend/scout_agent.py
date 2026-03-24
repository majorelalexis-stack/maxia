"""MAXIA SCOUT Agent — Prospection IA-to-IA sur 14 chains

Scanne Solana, Base (L2), Ethereum mainnet, XRP Ledger, Polygon, Arbitrum, Avalanche, BNB, TON, SUI, et TRON pour trouver
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
    MARKETING_WALLET_ADDRESS, PROSPECT_MAX_PER_DAY,
    POLYGON_RPC, ARBITRUM_RPC, AVALANCHE_RPC, BNB_RPC,
    NEAR_RPC, APTOS_API, SEI_RPC,
    XRPL_RPC, TRON_API_URL,
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
    "ai16zxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx": "AI16z",
    "GoATi9B21g5Vm7yLuNs6bXiNK1DH4D7T32BE8pCNbVMq": "GOAT SDK",
    "VRTLxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx": "Virtuals Solana",
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
    # Morpheus AI
    "0x47176B2Af9885dC6C4575d4eFd63895f7Aaa4790": {
        "name": "Morpheus AI MOR",
        "type": "token",
        "scan_method": "transfers",
    },
    # ChainML
    "0x7D1AfA7B718fb893dB30A3aBc0Cfc608AaCfeBB0": {
        "name": "ChainML MATIC Staking",
        "type": "staking",
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
    # Based Agents
    "0x532f27101965dd16442E59d40670FaF5eBB142E4": {
        "name": "Based Agents",
        "type": "registry",
        "scan_method": "logs",
    },
}

# Polygon AI contracts
POLYGON_AI_CONTRACTS = {
    "0x0000000000000000000000000000000000001010": {"name": "Polygon MATIC", "type": "token", "scan_method": "logs"},
}

# Arbitrum AI contracts
ARBITRUM_AI_CONTRACTS = {
    "0x912CE59144191C1204E64559FE8253a0e49E6548": {"name": "ARB Token", "type": "token", "scan_method": "transfers"},
}

# Avalanche AI contracts
AVALANCHE_AI_CONTRACTS = {
    "0xB31f66AA3C1e785363F0875A1B74E27b85FD66c7": {"name": "WAVAX", "type": "token", "scan_method": "logs"},
}

# BNB Chain AI contracts
BNB_AI_CONTRACTS = {
    "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c": {"name": "WBNB", "type": "token", "scan_method": "logs"},
}

# NEAR AI contracts/protocols connus
NEAR_AI_ACCOUNTS = [
    {"account": "aurora", "name": "Aurora EVM", "protocol": "aurora"},
    {"account": "v2.ref-finance.near", "name": "Ref Finance", "protocol": "ref"},
    {"account": "app.nearcrowd.near", "name": "NEARCrowd", "protocol": "nearcrowd"},
    {"account": "social.near", "name": "NEAR Social", "protocol": "near-social"},
    {"account": "agent.near", "name": "NEAR Agent", "protocol": "near-agent"},
    {"account": "intear.near", "name": "Intear AI", "protocol": "intear"},
]

# Aptos AI protocols
APTOS_AI_MODULES = [
    {"address": "0x1", "name": "Aptos Framework", "protocol": "aptos-core"},
    {"address": "0x5ae6789dd2fec1a9ec9cccfb3acaf12e93d432f0a3a42c92fe1a9d490b7bbc06", "name": "Liquidswap", "protocol": "liquidswap"},
    {"address": "0x6f986d146e4a90b828d8c12c14b6f4e003fdff11a8eecceceb63744363eaac01", "name": "Thala", "protocol": "thala"},
]

# SEI AI contracts (EVM)
SEI_AI_CONTRACTS = {
    "0x3894085Ef7Ff0f0aeDf52E2A2704928d1Ec074F1": {"name": "SEI USDC", "type": "token", "scan_method": "transfers"},
}

# Known AI agent registries (HTTP APIs)
AI_REGISTRIES = [
    {
        "name": "Autonolas Registry",
        "url": "https://registry.olas.network/api/services",
        "type": "olas",
        "chain": "ethereum",
    },
    {
        "name": "NEAR AI Registry",
        "url": "https://api.near.ai/v1/agents",
        "type": "near-ai",
        "chain": "near",
    },
    {
        "name": "Virtuals Protocol",
        "url": "https://api.virtuals.io/api/agents",
        "type": "virtuals",
        "chain": "base",
    },
    {
        "name": "LangChain Hub",
        "url": "https://api.hub.langchain.com/repos",
        "type": "langchain",
        "chain": "multi",
    },
]

# XRP AI accounts
XRP_AI_ACCOUNTS = [
    "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh",  # Genesis account
    "rhub8VRN55s94qWKDv6jmDy1pUykJzF3wq",   # XRP Hub
]

# TRON AI contracts
TRON_AI_CONTRACTS = [
    "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t",   # USDT TRC-20
    "TEkxiTehnzSmSe2XqrBj4w32RUN966rdz8",    # USDC TRC-20
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
        self._max_contacts_day = PROSPECT_MAX_PER_DAY  # (#13) Use config value
        self._max_contacts_per_agent = 2
        print("[SCOUT] Agent IA-to-IA prospection initialise (14 chains: Solana + Base + Ethereum + XRP + Polygon + Arbitrum + Avalanche + BNB + TON + SUI + TRON + NEAR + Aptos + SEI)")

    async def run(self):
        """Boucle principale — scan toutes les 6 heures."""
        self._running = True
        print(f"[SCOUT] Demarre — scan 14 chains, max {self._max_contacts_day} contacts/jour")
        await alert_system(
            "SCOUT Agent IA-to-IA demarre",
            f"Scan: 14 chains (Solana + Base + Ethereum + XRP + Polygon + Arbitrum + Avalanche + BNB + TON + SUI + TRON + NEAR + Aptos + SEI)\n"
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
                    await asyncio.sleep(2)  # 2s entre chaque contact (pas de spam)
            except Exception as e:
                print(f"[SCOUT] Erreur boucle: {e}")
                await alert_error("SCOUT", str(e))
            await asyncio.sleep(1800)  # 30 min (GPU local = gratuit, on peut scanner souvent)

    def stop(self):
        self._running = False

    # ══════════════════════════════════════════
    # Scan — 14 chains
    # ══════════════════════════════════════════

    async def scan_all_chains(self) -> list:
        """Scan les 14 chains en parallele pour trouver des agents IA."""
        results = await asyncio.gather(
            self._scan_solana(),
            self._scan_ethereum(),
            self._scan_base(),
            self._scan_evm_chain("polygon", POLYGON_RPC, POLYGON_AI_CONTRACTS),
            self._scan_evm_chain("arbitrum", ARBITRUM_RPC, ARBITRUM_AI_CONTRACTS),
            self._scan_evm_chain("avalanche", AVALANCHE_RPC, AVALANCHE_AI_CONTRACTS),
            self._scan_evm_chain("bnb", BNB_RPC, BNB_AI_CONTRACTS),
            self._scan_ton(),
            self._scan_sui(),
            self._scan_near(),
            self._scan_aptos(),
            self._scan_evm_chain("sei", SEI_RPC, SEI_AI_CONTRACTS),
            self._scan_xrp(),
            self._scan_tron(),
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
        print(f"[SCOUT] {len(unique)} agents IA trouves sur 14 chains")
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
                        "params": [program, {"limit": 50}],
                    })
                    sigs = resp.json().get("result", [])
                    for sig_info in sigs[:20]:
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
                for log in logs[:100]:
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
                    for log in logs[:100]:
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

    async def _scan_evm_chain(self, chain_name: str, rpc_url: str, contracts: dict) -> list:
        """Scan generique pour chains EVM (Polygon, Arbitrum, Avalanche, BNB)."""
        agents = []
        for contract, info in contracts.items():
            try:
                payload = {
                    "jsonrpc": "2.0", "id": 1,
                    "method": "eth_getLogs",
                    "params": [{"address": contract, "fromBlock": "latest", "toBlock": "latest"}],
                }
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.post(rpc_url, json=payload)
                    logs = resp.json().get("result", [])
                    wallets = set()
                    for log in logs[:100]:
                        topics = log.get("topics", [])
                        for t in topics[1:]:
                            if len(t) == 66:
                                addr = "0x" + t[-40:]
                                if addr != "0x" + "0" * 40:
                                    wallets.add(addr)
                    for wallet in list(wallets)[:5]:
                        agents.append({
                            "address": wallet,
                            "chain": chain_name,
                            "protocol": info["name"],
                            "type": info["type"],
                            "contact_method": "api_or_onchain",
                        })
            except Exception as e:
                print(f"[SCOUT] {chain_name} scan {info['name']} error: {e}")
        return agents

    async def _scan_ton(self) -> list:
        """Scan TON pour trouver des bots/agents actifs."""
        agents = []
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                # Chercher les transactions recentes sur des contrats connus
                resp = await client.get("https://toncenter.com/api/v2/getTransactions",
                    params={"address": "EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs", "limit": 50})
                data = resp.json()
                if data.get("ok"):
                    for tx in data.get("result", [])[:10]:
                        sender = tx.get("in_msg", {}).get("source", "")
                        if sender:
                            agents.append({
                                "address": sender,
                                "chain": "ton",
                                "protocol": "TON USDT",
                                "type": "active_wallet",
                                "contact_method": "telegram",
                            })
        except Exception as e:
            print(f"[SCOUT] TON scan error: {e}")
        return agents

    async def _scan_sui(self) -> list:
        """Scan SUI pour trouver des agents/bots actifs."""
        agents = []
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post("https://fullnode.mainnet.sui.io:443", json={
                    "jsonrpc": "2.0", "id": 1,
                    "method": "suix_queryEvents",
                    "params": [{"MoveModule": {"package": "0x2", "module": "coin"}}, None, 50, True],
                })
                data = resp.json()
                events = data.get("result", {}).get("data", [])
                wallets = set()
                for event in events[:10]:
                    sender = event.get("sender", "")
                    if sender:
                        wallets.add(sender)
                for wallet in list(wallets)[:5]:
                    agents.append({
                        "address": wallet,
                        "chain": "sui",
                        "protocol": "SUI DeFi",
                        "type": "active_wallet",
                        "contact_method": "api_or_onchain",
                    })
        except Exception as e:
            print(f"[SCOUT] SUI scan error: {e}")
        return agents

    async def _scan_near(self) -> list:
        """Scan NEAR pour trouver des agents/bots IA actifs."""
        agents = []
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                # Scanner les comptes NEAR connus dans l'ecosysteme AI
                for acct in NEAR_AI_ACCOUNTS:
                    try:
                        resp = await client.post(NEAR_RPC, json={
                            "jsonrpc": "2.0", "id": 1,
                            "method": "query",
                            "params": {"request_type": "view_account", "finality": "final", "account_id": acct["account"]},
                        })
                        data = resp.json()
                        if data.get("result") and not data.get("error"):
                            agents.append({
                                "address": acct["account"],
                                "chain": "near",
                                "protocol": acct["protocol"],
                                "type": "known_protocol",
                                "contact_method": "api_or_onchain",
                            })
                            self._register_discovery(acct["account"], "near", acct["protocol"], False)
                    except Exception:
                        continue
                # Scanner le registre NEAR AI
                try:
                    resp = await client.get("https://api.near.ai/v1/agents", timeout=10)
                    if resp.status_code == 200:
                        data = resp.json()
                        for agent in (data if isinstance(data, list) else data.get("agents", []))[:50]:
                            addr = agent.get("account_id", agent.get("id", ""))
                            if addr:
                                agents.append({
                                    "address": addr,
                                    "chain": "near",
                                    "protocol": "near-ai",
                                    "type": "registered_agent",
                                    "contact_method": "api",
                                })
                except Exception:
                    pass
        except Exception as e:
            print(f"[SCOUT] NEAR scan error: {e}")
        print(f"[SCOUT] NEAR: {len(agents)} agents trouves")
        return agents

    async def _scan_aptos(self) -> list:
        """Scan Aptos pour trouver des agents/protocols actifs."""
        agents = []
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                for mod in APTOS_AI_MODULES:
                    try:
                        resp = await client.get(f"{APTOS_API}/accounts/{mod['address']}")
                        if resp.status_code == 200:
                            agents.append({
                                "address": mod["address"],
                                "chain": "aptos",
                                "protocol": mod["protocol"],
                                "type": "known_protocol",
                                "contact_method": "api",
                            })
                            self._register_discovery(mod["address"], "aptos", mod["protocol"], False)
                    except Exception:
                        continue
        except Exception as e:
            print(f"[SCOUT] Aptos scan error: {e}")
        print(f"[SCOUT] Aptos: {len(agents)} agents trouves")
        return agents

    async def _scan_xrp(self) -> list:
        """Scan XRP Ledger pour trouver des agents/bots actifs."""
        agents = []
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                for acct in XRP_AI_ACCOUNTS:
                    try:
                        resp = await client.post(XRPL_RPC, json={
                            "method": "account_tx",
                            "params": [{"account": acct, "limit": 50, "ledger_index_min": -1}],
                        })
                        data = resp.json()
                        txs = data.get("result", {}).get("transactions", [])
                        for tx in txs[:50]:
                            dest = tx.get("tx", {}).get("Destination", "")
                            source = tx.get("tx", {}).get("Account", "")
                            for addr in [dest, source]:
                                if addr and addr != acct:
                                    agents.append({
                                        "address": addr,
                                        "chain": "xrp",
                                        "protocol": "XRPL",
                                        "type": "active_wallet",
                                        "contact_method": "api_or_onchain",
                                    })
                    except Exception:
                        continue
        except Exception as e:
            print(f"[SCOUT] XRP scan error: {e}")
        print(f"[SCOUT] XRP: {len(agents)} agents trouves")
        return agents

    async def _scan_tron(self) -> list:
        """Scan TRON pour trouver des agents/bots actifs."""
        agents = []
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                for contract in TRON_AI_CONTRACTS:
                    try:
                        resp = await client.get(
                            f"{TRON_API_URL}/v1/contracts/{contract}/events",
                            params={"limit": 50, "only_confirmed": "true"},
                        )
                        data = resp.json()
                        events = data.get("data", [])
                        for event in events[:50]:
                            caller = event.get("caller_contract_address", "")
                            tx_owner = event.get("transaction_owner_address", "")
                            for addr in [caller, tx_owner]:
                                if addr:
                                    agents.append({
                                        "address": addr,
                                        "chain": "tron",
                                        "protocol": "TRON DeFi",
                                        "type": "active_wallet",
                                        "contact_method": "api_or_onchain",
                                    })
                    except Exception:
                        continue
        except Exception as e:
            print(f"[SCOUT] TRON scan error: {e}")
        print(f"[SCOUT] TRON: {len(agents)} agents trouves")
        return agents

    async def _scan_registries(self) -> list:
        """Scan les registries HTTP d'agents IA (Autonolas, etc)."""
        agents = []
        for registry in AI_REGISTRIES:
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.get(registry["url"], params={"limit": 50})
                    if resp.status_code != 200:
                        continue
                    data = resp.json()
                    services = data if isinstance(data, list) else data.get("results", data.get("services", []))
                    for svc in services[:30]:
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
                        "1% marketplace commission (Bronze), 0.10% swap fee",
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

    # Templates par chain — 0 token LLM
    _CHAIN_TEMPLATES = {
        "solana": "Your Solana agent can sell services on MAXIA. POST /sell → USDC in your wallet. 14 chains, 1% fee. {MAXIA_URL}",
        "ethereum": "Your ETH agent can earn USDC on MAXIA marketplace. One API call to list. 14 chains supported. {MAXIA_URL}",
        "base": "Base agent? Sell services to other AIs on MAXIA. USDC payments, 1% fee. {MAXIA_URL}",
        "polygon": "Your Polygon agent can earn USDC selling services to other AIs. Register free: {MAXIA_URL}",
        "arbitrum": "Arbitrum agent? MAXIA marketplace lets you sell services to other AIs. USDC, 1% fee. {MAXIA_URL}",
        "avalanche": "AVAX agent? List your service on MAXIA, earn USDC from other AI agents. {MAXIA_URL}",
        "bnb": "BNB Chain agent? Sell services on MAXIA AI marketplace. 14 chains, USDC. {MAXIA_URL}",
        "ton": "TON bot? MAXIA lets AI agents trade services. Earn USDC, one API call. {MAXIA_URL}",
        "sui": "SUI Move agent? Sell services to other AIs on MAXIA. 14 chains, USDC payments. {MAXIA_URL}",
        "tron": "TRON agent? List on MAXIA marketplace, earn USDC from AI-to-AI trades. {MAXIA_URL}",
        "near": "NEAR agent? MAXIA is an AI marketplace on 14 chains. Sell services, earn USDC. {MAXIA_URL}",
        "aptos": "Aptos Move agent? MAXIA lets AIs buy your services. USDC, 1% fee, one POST. {MAXIA_URL}",
        "sei": "SEI agent? Sell to other AIs on MAXIA marketplace. 14 chains, USDC, 390ms settlement. {MAXIA_URL}",
    }

    async def _generate_m2m_message(self, protocol: str, chain: str) -> str:
        """Generate outreach message. Uses templates (0 tokens) with optional Groq personalization."""
        # Template par chain (gratuit, instantane)
        template = self._CHAIN_TEMPLATES.get(chain.lower(),
            f"Your {chain} agent can sell services on MAXIA AI marketplace. USDC, 14 chains. {{MAXIA_URL}}")
        base_msg = template.format(MAXIA_URL=MAXIA_URL)

        # Personnaliser via Groq seulement 1 fois sur 5 (economiser les tokens)
        if not groq_client or self._total_contacted % 5 != 0:
            return base_msg

        try:
            prompt = (
                f"Rewrite this outreach message for a {protocol} agent on {chain}. "
                f"Keep under 180 chars. Dev tone. English.\n"
                f"Original: {base_msg}"
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
            "chains": ["solana", "ethereum", "base", "xrp", "polygon", "arbitrum", "avalanche", "bnb", "ton", "sui", "tron", "near", "aptos", "sei"],
            "protocols_watched": (
                list(SOLANA_AI_PROGRAMS.values())
                + [v["name"] for v in ETH_AI_CONTRACTS.values()]
                + [v["name"] for v in BASE_AI_CONTRACTS.values()]
            ),
        }


scout_agent = ScoutAgent()
