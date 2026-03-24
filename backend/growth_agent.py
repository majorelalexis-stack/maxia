"""MAXIA Growth Agent V12 — HUNTER : Prospection Humaine (profil "Thomas")

Cible : devs humains qui ont un agent IA qui tourne mais ne gagne pas d'argent.
  Profil Thomas : 26-34 ans, dev Python, connait Solana/Ethereum
  Frustration : "Mon bot tourne dans le vide, 0 clients"
  Ce qu'il veut : POST /sell -> son service est live, d'autres IA l'achetent, USDC arrive

Canaux : on-chain (Solana memo), Twitter, Discord, Reddit, GitHub
Focus : devs qui deploient des programmes, interagissent avec DeFi, ou operent des bots

NOTE : La prospection IA-to-IA est geree par scout_agent.py (SCOUT).
       Le HUNTER ne contacte QUE des humains (devs, traders, builders).

Messages a valeur ajoutee :
  - Analyse du wallet AVANT d'envoyer le message
  - Info personnalisee (fees payes, vulnerabilites, economies possibles)
  - Max 10/jour, 2 contacts max par wallet (jamais 3)

Contenu automatique :
  - Rapport quotidien Discord + Telegram
  - Comparatif fees hebdo
"""
import asyncio, time
from datetime import date
import httpx

from config import (
    get_rpc_url, GROQ_API_KEY, GROQ_MODEL,
    GROWTH_MAX_SPEND_DAY, GROWTH_MAX_SPEND_TX,
    GROWTH_RESERVE_ALERT, PROSPECT_MIN_SOL, PROSPECT_MAX_PER_DAY,
    TREASURY_ADDRESS, MARKETING_WALLET_ADDRESS,
)
from solana_tx import send_memo_transfer, get_sol_balance
from alerts import alert_prospect_contacted, alert_low_balance, alert_error, alert_system

groq_client = None
if GROQ_API_KEY:
    try:
        from groq import Groq
        groq_client = Groq(api_key=GROQ_API_KEY)
    except Exception:
        pass

MAXIA_URL = "maxiaworld.app"
JUPITER_PROGRAM = "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4"

XSTOCK_MINTS = [
    "XsbEhLAtcf6HdfpFZ5xEMdqW8nfAvcsP5bdudRLJzJp",
    "XsDoVfqeBukxuZHWhdvWHBhgEHjGNst4MLodqsJHzoB",
    "Xsc9qvGR1efVDFGLrVsmkzv3qi45LTBjeUKSPmx9qEh",
    "XsoCS1TfEyfFhfvj8EtZ528L3CaKBDBRqRapnBbDF2W",
]

SERVICE_CATALOG = (
    "MAXIA — AI-to-AI Marketplace on Solana\n"
    "\n"
    "Any AI agent can sell services to other AI agents.\n"
    "Register free. List your service. Get paid in USDC.\n"
    "Marketplace: 1% Bronze → 0.1% Whale. Swap: 0.10% → 0.01%. You keep up to 99.9%.\n"
    "\n"
    "SELL: data, code, analysis, images, signals — set your price\n"
    "BUY: swap 2450 pairs, GPU $0.69/h, audit $9.99, scraper $0.02\n"
    "\n"
    "POST /register → API key in 2 seconds\n"
    "POST /sell → your service is live\n"
    "GET /services → browse all services\n"
    "\n"
    "Free API: " + MAXIA_URL + "/api/public/register"
)

PROFILES = {
    "ai_agent": {
        "description": "Dev running an AI agent/bot on-chain (profil Thomas)",
        "value_message": "Your bot runs but earns $0? List it on MAXIA: POST /sell = live. Other AI agents buy your service. USDC in your wallet. Free: " + MAXIA_URL + "/api/public/register",
        "services": ["sell services", "earn USDC", "marketplace"],
    },
    "developer": {
        "description": "Deployed smart contracts (BPFLoader)",
        "value_message": "Build agents that trade. MAXIA: open API for AI-to-AI commerce. Swap, GPU, sell services. Free SDK: " + MAXIA_URL + "/api/public/docs",
        "services": ["API", "swap", "GPU", "marketplace"],
    },
    "active_trader": {
        "description": "100+ swaps on Jupiter",
        "value_message": "Automate your trading. MAXIA API: 2450 pairs, 0.01% whale tier. Your bot trades while you sleep. " + MAXIA_URL,
        "services": ["swap 0.01%", "data $2.99", "monitor $1.99"],
    },
    "token_creator": {
        "description": "Created an SPL token",
        "value_message": "New token? Audit it $4.99. Then list data services on MAXIA marketplace. Other agents will buy. " + MAXIA_URL + "/api/public/register",
        "services": ["audit $9.99", "marketplace", "image $0.10"],
    },
    "gpu_user": {
        "description": "Uses GPU/AI programs on-chain",
        "value_message": "GPU at cost: RTX4090 $0.69/h. Then sell your AI results on MAXIA marketplace. Earn USDC passively. " + MAXIA_URL + "/api/public/gpu/tiers",
        "services": ["gpu $0.69/h", "sell services", "marketplace"],
    },
    "data_provider": {
        "description": "Wallet interacts with oracle/data programs",
        "value_message": "Your data has value. Sell it on MAXIA marketplace. Set your price, other AI agents buy via API. " + MAXIA_URL + "/api/public/sell",
        "services": ["sell data", "earn USDC", "marketplace"],
    },
    "defi_builder": {
        "description": "Interacts with DeFi protocols (Raydium, Orca, Jupiter)",
        "value_message": "DeFi builder? Sell yield strategies, arbitrage signals, analytics on MAXIA. AI agents pay USDC. " + MAXIA_URL + "/api/public/register",
        "services": ["sell strategies", "swap API", "marketplace"],
    },
}


class GrowthAgent:
    def __init__(self):
        self._contacted: dict = {}
        self._prospects_today: list = []
        self._daily_spend: float = 0.0
        self._daily_date: str = ""
        self._running: bool = False
        self._total_prospects = 0
        self._total_spend = 0.0
        self._max_per_day = PROSPECT_MAX_PER_DAY  # (#13) Use config value
        self._max_contacts_per_wallet = 2
        print("[GrowthAgent] Agent marketing ultra-cible initialise")

    async def run(self):
        self._running = True
        print(f"[GrowthAgent] Demarre — max {self._max_per_day}/jour, ciblage precis")
        await alert_system(
            "Agent Marketing Ultra-Cible demarre",
            f"Max {self._max_per_day} prospects/jour\n"
            f"Analyse wallet avant contact\n"
            f"Messages personnalises a valeur ajoutee\n"
            f"Max 2 contacts par wallet"
        )
        while self._running:
            try:
                self._reset_daily()
                if not await self._check_budget():
                    await asyncio.sleep(300)
                    continue
                if not self._can_contact():
                    await self._publish_content()
                    await asyncio.sleep(600)
                    continue
                prospects = await self._scan_all_sources()
                for wallet, profile, analysis in prospects:
                    if not self._can_contact():
                        break
                    await self._contact_prospect(wallet, profile, analysis)
                    await asyncio.sleep(15)
            except Exception as e:
                print(f"[GrowthAgent] Erreur: {e}")
                await alert_error("GrowthAgent", str(e))
            await asyncio.sleep(14400)  # 4 heures (economie tokens)

    def stop(self):
        self._running = False

    async def _scan_all_sources(self) -> list:
        prospects = []
        try:
            jup_wallets = await self._scan_program_users(JUPITER_PROGRAM, limit=10)
            for w in jup_wallets:
                if self._can_contact_wallet(w):
                    analysis = await self._analyze_wallet(w)
                    if analysis.get("balance", 0) >= PROSPECT_MIN_SOL:
                        tx_count = analysis.get("tx_count", 0)
                        if tx_count > 5:
                            fees = round(tx_count * 0.005 * analysis.get("balance", 0) * 0.01, 2)
                            analysis["fees_estimate"] = fees
                            prospects.append((w, "active_trader", analysis))
        except Exception as e:
            print(f"[GrowthAgent] Jupiter scan error: {e}")
        try:
            for mint in XSTOCK_MINTS[:2]:
                holders = await self._scan_token_holders(mint)
                for w in holders:
                    if self._can_contact_wallet(w) and w not in [p[0] for p in prospects]:
                        analysis = await self._analyze_wallet(w)
                        if analysis.get("balance", 0) >= PROSPECT_MIN_SOL:
                            prospects.append((w, "xstock_holder", analysis))
        except Exception as e:
            print(f"[GrowthAgent] xStock scan error: {e}")
        try:
            recent = await self._scan_recent_blocks()
            for w in recent:
                if self._can_contact_wallet(w) and w not in [p[0] for p in prospects]:
                    analysis = await self._analyze_wallet(w)
                    bal = analysis.get("balance", 0)
                    if bal < PROSPECT_MIN_SOL:
                        continue
                    if bal >= 500:
                        prospects.append((w, "whale", analysis))
                    elif analysis.get("is_developer"):
                        prospects.append((w, "developer", analysis))
                    elif analysis.get("recent_large_incoming"):
                        prospects.append((w, "post_airdrop", analysis))
                    else:
                        prospects.append((w, "active_trader", analysis))
        except Exception as e:
            print(f"[GrowthAgent] Recent scan error: {e}")
        seen = set()
        unique = []
        for w, p, a in prospects:
            if w not in seen:
                seen.add(w)
                unique.append((w, p, a))
        print(f"[GrowthAgent] {len(unique)} prospects qualifies trouves")
        return unique[:self._max_per_day]

    async def _analyze_wallet(self, wallet: str) -> dict:
        rpc = get_rpc_url()
        analysis = {"wallet": wallet, "balance": 0, "tx_count": 0,
                     "is_developer": False, "is_ai_agent": False,
                     "is_defi_user": False, "recent_large_incoming": False,
                     "fees_estimate": 0, "programs_used": []}
        try:
            analysis["balance"] = await get_sol_balance(wallet)
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(rpc, json={
                    "jsonrpc": "2.0", "id": 1,
                    "method": "getSignaturesForAddress",
                    "params": [wallet, {"limit": 10}],
                })
                sigs = resp.json().get("result", [])
                analysis["tx_count"] = len(sigs)
                if sigs:
                    sig = sigs[0].get("signature", "")
                    resp2 = await client.post(rpc, json={
                        "jsonrpc": "2.0", "id": 1,
                        "method": "getTransaction",
                        "params": [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
                    })
                    tx = resp2.json().get("result", {})
                    if tx:
                        logs = tx.get("meta", {}).get("logMessages", [])
                        programs = set()
                        for log in logs:
                            if "Program deploy" in log or "BPFLoaderUpgradeab1e" in log:
                                analysis["is_developer"] = True
                            # Detect AI/automation programs
                            if "invoke" in log.lower():
                                for known in ["clockwork", "switchboard", "pyth", "chainlink"]:
                                    if known in log.lower():
                                        analysis["is_ai_agent"] = True
                            # Detect DeFi
                            for defi in ["JUP", "whirl", "Raydium", "Orca", "Meteora"]:
                                if defi in log:
                                    analysis["is_defi_user"] = True
                                    programs.add(defi)
                        analysis["programs_used"] = list(programs)
                        pre = tx.get("meta", {}).get("preBalances", [])
                        post = tx.get("meta", {}).get("postBalances", [])
                        if pre and post and len(pre) > 0 and len(post) > 0:
                            change = (post[0] - pre[0]) / 1e9
                            if change > 10:
                                analysis["recent_large_incoming"] = True
        except Exception:
            pass

        # Determine best profile
        if analysis["is_developer"]:
            analysis["best_profile"] = "developer"
        elif analysis["is_ai_agent"]:
            analysis["best_profile"] = "ai_agent"
        elif analysis["is_defi_user"]:
            analysis["best_profile"] = "defi_builder"
        elif analysis["balance"] > 100:
            analysis["best_profile"] = "active_trader"
        else:
            analysis["best_profile"] = "ai_agent"  # Default: pitch the marketplace

        return analysis

    async def _contact_prospect(self, wallet: str, profile: str, analysis: dict):
        if not self._can_contact_wallet(wallet):
            return
        balance = analysis.get("balance", 0)
        if balance < PROSPECT_MIN_SOL:
            return
        message = await self._generate_value_message(wallet, profile, analysis)
        if not message:
            return
        memo = f"MAXIA | {message[:380]}"
        result = await send_memo_transfer(wallet, 0.00001, memo)
        if result.get("success"):
            if wallet not in self._contacted:
                self._contacted[wallet] = {"count": 0, "channels": [], "first": int(time.time())}
            self._contacted[wallet]["count"] += 1
            self._contacted[wallet]["last"] = int(time.time())
            self._contacted[wallet]["channels"].append("solana_memo")
            self._prospects_today.append(wallet)
            self._daily_spend += 0.00001
            self._total_prospects += 1
            print(f"[GrowthAgent] [{profile}] ({balance:.0f} SOL, tx:{analysis.get('tx_count',0)}): {wallet[:8]}...")
            await alert_prospect_contacted(wallet, f"[{profile.upper()}] {message[:80]}")

    async def _generate_value_message(self, wallet: str, profile: str, analysis: dict) -> str:
        profile_config = PROFILES.get(profile, PROFILES["ai_agent"])
        balance = analysis.get("balance", 0)
        fees_estimate = analysis.get("fees_estimate", 0)
        template = profile_config["value_message"].replace(
            "${fees_estimate}",
            f"{fees_estimate:.0f}" if fees_estimate else "significant",
        )
        if not groq_client:
            return template
        try:
            is_dev = analysis.get("is_developer", False)
            is_ai = analysis.get("is_ai_agent", False)
            is_defi = analysis.get("is_defi_user", False)
            programs = analysis.get("programs_used", [])

            prompt = (
                f"You are a dev who talks to other devs. Write a SHORT memo (max 180 chars).\n"
                f"TARGET: dev/AI builder on Solana who has a bot but 0 revenue.\n"
                f"Profile: {profile} - {profile_config['description']}\n"
                f"Wallet: {balance:.0f} SOL | Txs: {analysis.get('tx_count', 0)} | "
                f"Dev: {is_dev} | AI: {is_ai} | DeFi: {is_defi} | Programs: {programs}\n\n"
                f"MAXIA = AI-to-AI marketplace. Your agent sells services to other agents.\n"
                f"POST /sell = your service is live. Earn USDC. No marketing needed.\n"
                f"URL: {MAXIA_URL}\n\n"
                f"RULES:\n"
                f"- Talk like a dev, NOT a marketer\n"
                f"- Focus on EARNING USDC passively\n"
                f"- Mention: one API call, no subscription, no token\n"
                f"- If they're a dev: mention github.com/MAXIAWORLD/demo-agent\n"
                f"- If they use DeFi: mention selling strategies/signals\n"
                f"- NEVER say 'revolutionary' or 'game-changing'\n"
                f"- Include URL\n"
                f"- Max 180 chars\n"
                f"- English only"
            )

            def _call():
                resp = groq_client.chat.completions.create(
                    model=GROQ_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=80, temperature=0.7,
                )
                return resp.choices[0].message.content.strip()

            result = await asyncio.to_thread(_call)
            return result if len(result) > 20 else template
        except Exception:
            return template

    async def _publish_content(self):
        # Rapport quotidien et hebdo geres par telegram_bot.py — pas de doublon ici
        pass

    async def _scan_program_users(self, program: str, limit: int = 10) -> list:
        rpc = get_rpc_url()
        wallets = []
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(rpc, json={
                    "jsonrpc": "2.0", "id": 1,
                    "method": "getSignaturesForAddress",
                    "params": [program, {"limit": limit}],
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
                            if signer and signer not in wallets:
                                wallets.append(signer)
                    await asyncio.sleep(0.5)
        except Exception as e:
            print(f"[GrowthAgent] Program scan error: {e}")
        return wallets

    async def _scan_token_holders(self, mint: str) -> list:
        rpc = get_rpc_url()
        holders = []
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(rpc, json={
                    "jsonrpc": "2.0", "id": 1,
                    "method": "getTokenLargestAccounts",
                    "params": [mint],
                })
                accounts = resp.json().get("result", {}).get("value", [])
                for acc in accounts[:5]:
                    addr = acc.get("address", "")
                    if addr:
                        resp2 = await client.post(rpc, json={
                            "jsonrpc": "2.0", "id": 1,
                            "method": "getAccountInfo",
                            "params": [addr, {"encoding": "jsonParsed"}],
                        })
                        info = resp2.json().get("result", {}).get("value", {})
                        parsed = info.get("data", {}).get("parsed", {}).get("info", {})
                        owner = parsed.get("owner", "")
                        if owner and owner not in holders:
                            holders.append(owner)
                await asyncio.sleep(1)
        except Exception as e:
            print(f"[GrowthAgent] Token holders error: {e}")
        return holders

    async def _scan_recent_blocks(self) -> list:
        rpc = get_rpc_url()
        wallets = []
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(rpc, json={
                    "jsonrpc": "2.0", "id": 1,
                    "method": "getRecentPerformanceSamples",
                    "params": [1],
                })
                samples = resp.json().get("result", [])
                if samples:
                    slot = samples[0].get("slot", 0)
                    if slot:
                        resp2 = await client.post(rpc, json={
                            "jsonrpc": "2.0", "id": 1,
                            "method": "getBlock",
                            "params": [slot - 5, {"transactionDetails": "accounts", "maxSupportedTransactionVersion": 0}],
                        })
                        block = resp2.json().get("result", {})
                        for tx in block.get("transactions", [])[:30]:
                            accs = tx.get("transaction", {}).get("accountKeys", [])
                            if accs:
                                signer = accs[0] if isinstance(accs[0], str) else accs[0].get("pubkey", "")
                                if signer and signer not in wallets and signer != TREASURY_ADDRESS and signer != MARKETING_WALLET_ADDRESS:
                                    wallets.append(signer)
        except Exception as e:
            print(f"[GrowthAgent] Block scan error: {e}")
        return wallets[:15]

    def _can_contact_wallet(self, wallet: str) -> bool:
        contact = self._contacted.get(wallet)
        if not contact:
            return True
        if contact["count"] >= self._max_contacts_per_wallet:
            return False
        days_since = (time.time() - contact.get("last", 0)) / 86400
        if days_since < 7:
            return False
        return True

    def _reset_daily(self):
        today = date.today().isoformat()
        if self._daily_date != today:
            if self._daily_date and self._prospects_today:
                print(f"[GrowthAgent] Bilan {self._daily_date}: {len(self._prospects_today)} prospects contactes")
            self._daily_date = today
            self._prospects_today = []
            self._daily_spend = 0.0

    def _can_contact(self) -> bool:
        return len(self._prospects_today) < self._max_per_day

    async def _check_budget(self) -> bool:
        if not MARKETING_WALLET_ADDRESS:
            return True
        balance = await get_sol_balance(MARKETING_WALLET_ADDRESS)
        # Alert if below reserve threshold (default 0.05 SOL ~ $7)
        reserve_sol = GROWTH_RESERVE_ALERT / 150  # Approximate SOL price
        if balance < 0.001:
            await alert_low_balance(balance, MARKETING_WALLET_ADDRESS)
            print(f"[GrowthAgent] CRITICAL: wallet empty ({balance:.6f} SOL)")
            return False
        if balance < max(0.05, reserve_sol):
            await alert_low_balance(balance, MARKETING_WALLET_ADDRESS)
            print(f"[GrowthAgent] WARNING: low balance ({balance:.4f} SOL)")
        return True

    def get_stats(self) -> dict:
        contacts_1 = sum(1 for c in self._contacted.values() if c["count"] == 1)
        contacts_2 = sum(1 for c in self._contacted.values() if c["count"] == 2)
        return {
            "running": self._running,
            "prospects_today": len(self._prospects_today),
            "max_per_day": self._max_per_day,
            "total_prospects": self._total_prospects,
            "unique_wallets_contacted": len(self._contacted),
            "contacts_1x": contacts_1,
            "contacts_2x": contacts_2,
            "daily_spend": self._daily_spend,
            "profiles": list(PROFILES.keys()),
            "mode": "HUNTER — prospection humaine (profil Thomas, devs agents IA)",
        }


growth_agent = GrowthAgent()
