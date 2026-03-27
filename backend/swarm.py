"""MAXIA Art.20 V12 — Specialized Clone Swarm (production-hardened)

MAXIA detects profitable niches and deploys specialized sub-AIs,
each with its own pricing, wallet, and autonomous revenue stream.

Architecture:
    MAXIA (Queen) -> MAXIA-GPU-Render (Clone 1)
                  -> MAXIA-Data-Trading (Clone 2)
                  -> MAXIA-Code-Audit (Clone 3)
                  -> ...

Each clone:
    - Has its own Solana wallet
    - Has its own pricing adapted to its niche
    - Generates its own revenue
    - Pays a % royalty to the Queen (MAXIA Treasury)
    - Can be paused/resumed/stopped independently
"""
import logging
import asyncio, uuid, time, json, hashlib
import httpx
from config import (
    GROQ_API_KEY, GROQ_MODEL, TREASURY_ADDRESS,
    RAILWAY_API_TOKEN, GROWTH_MONTHLY_BUDGET, PORT,
)
from alerts import alert_system, alert_error


# ── Groq client ──
groq_client = None
if GROQ_API_KEY:
    try:
        from groq import Groq
        groq_client = Groq(api_key=GROQ_API_KEY)
    except Exception:
        pass


# ── Race condition lock (#8) ──
_swarm_lock = asyncio.Lock()


# ── Required fields for niche template validation (#12) ──
_REQUIRED_TEMPLATE_FIELDS = frozenset({
    "name", "description", "target_keywords", "base_price_usdc",
    "commission_bps", "marketing_lang", "marketing_pitch", "niche_id",
})


# ── Predefined niches MAXIA can detect and exploit ──
NICHE_TEMPLATES = {
    "sentiment": {
        "name": "MAXIA-SentimentBot",
        "description": "Real-time crypto sentiment analysis. Multi-source: CoinGecko, Reddit, social data.",
        "target_keywords": ["sentiment", "social", "analysis", "bullish", "bearish"],
        "base_price_usdc": 0.10,
        "commission_bps": 300,
        "marketing_lang": "en",
        "marketing_pitch": "AI sentiment analysis for any crypto token. Multi-source. $0.10 per query.",
        "niche_id": "sentiment",
    },
    "defi_yield": {
        "name": "MAXIA-YieldBot",
        "description": "DeFi yield scanner. Best APY across all protocols via DeFiLlama.",
        "target_keywords": ["defi", "yield", "apy", "farming", "lending"],
        "base_price_usdc": 0.0,
        "commission_bps": 0,
        "marketing_lang": "en",
        "marketing_pitch": "Find the best DeFi yields for any asset. All chains. Free.",
        "niche_id": "defi_yield",
    },
    "wallet_scan": {
        "name": "MAXIA-WalletScanner",
        "description": "Solana wallet analyzer. Holdings, profile, whale detection, DeFi activity.",
        "target_keywords": ["wallet", "holdings", "whale", "analysis", "portfolio"],
        "base_price_usdc": 0.10,
        "commission_bps": 300,
        "marketing_lang": "en",
        "marketing_pitch": "Analyze any Solana wallet. Holdings, profile, whale detection. $0.10 per scan.",
        "niche_id": "wallet_scan",
    },
    "rug_detector": {
        "name": "MAXIA-RugDetector",
        "description": "Rug pull risk detector. Risk score 0-100 for any Solana token.",
        "target_keywords": ["rug", "scam", "risk", "audit", "token"],
        "base_price_usdc": 0.10,
        "commission_bps": 300,
        "marketing_lang": "en",
        "marketing_pitch": "Detect rug pulls before they happen. Risk score 0-100. $0.10 per check.",
        "niche_id": "rug_detector",
    },
    "code_audit": {
        "name": "MAXIA-AuditBot",
        "description": "Automated smart contract security audits powered by AI",
        "target_keywords": ["audit", "security", "smart contract", "solidity", "rust"],
        "base_price_usdc": 4.99,
        "commission_bps": 150,
        "marketing_lang": "en",
        "marketing_pitch": "AI smart contract auditor. Find vulns in seconds. Cheaper than manual audits.",
        "niche_id": "code_audit",
    },
    "data_trading": {
        "name": "MAXIA-DataBot",
        "description": "Real-time DeFi/crypto trading data, signals and market analysis",
        "target_keywords": ["defi", "trading", "signals", "data", "analytics"],
        "base_price_usdc": 0.50,
        "commission_bps": 200,
        "marketing_lang": "en",
        "marketing_pitch": "Crypto trading data and signals. On-chain analytics. Pay per query.",
        "niche_id": "data_trading",
    },
    "translation": {
        "name": "MAXIA-TranslateBot",
        "description": "Professional AI translation (50+ languages)",
        "target_keywords": ["translate", "language", "localization", "i18n"],
        "base_price_usdc": 0.05,
        "commission_bps": 350,
        "marketing_lang": "en",
        "marketing_pitch": "AI translation. 50+ languages. $0.05 per request.",
        "niche_id": "translation",
    },
    "code_gen": {
        "name": "MAXIA-CoderBot",
        "description": "AI code generation. Python, Rust, JavaScript, Solidity.",
        "target_keywords": ["code", "generate", "python", "rust", "javascript"],
        "base_price_usdc": 0.50,
        "commission_bps": 200,
        "marketing_lang": "en",
        "marketing_pitch": "AI code generation for any language. $0.50 per task.",
        "niche_id": "code_gen",
    },
}

# (#12) Validate all niche templates have required fields at import time
for _nid, _tmpl in NICHE_TEMPLATES.items():
    _missing = _REQUIRED_TEMPLATE_FIELDS - set(_tmpl.keys())
    if _missing:
        raise ValueError(f"Niche template '{_nid}' missing required fields: {_missing}")

# Percentage of revenue paid to the Queen
QUEEN_ROYALTY_PCT = 15


class Clone:
    """A specialized MAXIA clone."""

    def __init__(self, clone_id: str, niche: str, template: dict):
        self.clone_id = clone_id
        self.niche = niche
        self.name = template["name"]
        self.description = template["description"]
        self.target_keywords = template["target_keywords"]
        self.base_price = template["base_price_usdc"]
        self.commission_bps = template["commission_bps"]
        self.marketing_lang = template["marketing_lang"]
        self.marketing_pitch = template["marketing_pitch"]

        # Wallet address only — never store private key (#1)
        self.wallet_address = ""
        # wallet_privkey removed — use config.MICRO_WALLET_PRIVKEY at execution time

        # Marketplace registration
        self.api_key = ""
        self.service_id = ""

        # Stats
        self.status = "created"  # created | deploying | active | paused | stopped
        self.created_at = int(time.time())
        self.total_revenue = 0.0
        self.total_requests = 0
        self.queen_royalties_paid = 0.0

    def to_dict(self) -> dict:
        """Serialize clone state. Never expose wallet private key (#11)."""
        return {
            "cloneId": self.clone_id,
            "niche": self.niche,
            "name": self.name,
            "description": self.description,
            "targetKeywords": self.target_keywords,
            "basePriceUsdc": self.base_price,
            "commissionBps": self.commission_bps,
            "wallet": self.wallet_address[:8] + "..." if self.wallet_address else "not assigned",
            "status": self.status,
            "createdAt": self.created_at,
            "totalRevenue": self.total_revenue,
            "totalRequests": self.total_requests,
            "queenRoyalties": self.queen_royalties_paid,
            "royaltyPct": QUEEN_ROYALTY_PCT,
        }


class Swarm:
    """
    MAXIA clone swarm manager.
    The Queen (main MAXIA) creates, monitors, and coordinates clones.
    """

    def __init__(self):
        self._clones: dict = {}  # clone_id -> Clone
        self._running = False  # (#15) graceful shutdown flag
        self._niche_scores: dict = {}  # niche -> profitability score
        print(f"[Swarm] Swarm initialized -- {len(NICHE_TEMPLATES)} niches available")

    # ── Niche analysis ──

    async def analyze_niches(self, db=None) -> list:
        """
        Analyze profitable niches based on transaction volume
        and demand on the MAXIA marketplace.
        """
        scores = []

        for niche_id, template in NICHE_TEMPLATES.items():
            score = {
                "niche": niche_id,
                "name": template["name"],
                "description": template["description"],
                "potential_score": 0,
                "market_demand": "unknown",
                "competition": "medium",
                "recommended": False,
            }

            # Analyze demand via existing commands
            if db:
                try:
                    keywords = template["target_keywords"]
                    count = 0
                    for kw in keywords:
                        rows = await db.raw_execute_fetchall(
                            "SELECT COUNT(*) FROM commands WHERE json_extract(data,'$.serviceId') LIKE ?",
                            (f"%{kw}%",))
                        count += (rows[0][0] if rows else 0)
                    score["potential_score"] = min(100, count * 10)
                    score["market_demand"] = (
                        "high" if count > 10 else "medium" if count > 3 else "low"
                    )
                except Exception as e:
                    print(f"[Swarm] ERROR analyzing niche {niche_id}: {e}")
                    score["potential_score"] = 50  # Default score

            # Use Groq to evaluate the niche
            if groq_client:
                try:
                    analysis = await self._ai_analyze_niche(template)
                    score["ai_analysis"] = analysis
                    if "high" in analysis.lower() or "strong" in analysis.lower():
                        score["potential_score"] = min(100, score["potential_score"] + 30)
                except Exception as e:
                    print(f"[Swarm] ERROR in AI niche analysis: {e}")

            score["recommended"] = score["potential_score"] >= 40
            scores.append(score)

        # Sort by score
        scores.sort(key=lambda x: x["potential_score"], reverse=True)
        self._niche_scores = {s["niche"]: s["potential_score"] for s in scores}
        return scores

    async def _ai_analyze_niche(self, template: dict) -> str:
        """Use Groq to analyze niche potential."""
        if not groq_client:
            return "AI analysis unavailable"
        try:
            prompt = (
                f"Analyze the market potential for this AI service niche in 2 sentences:\n"
                f"Name: {template['name']}\n"
                f"Description: {template['description']}\n"
                f"Target: {', '.join(template['target_keywords'])}\n"
                f"Price: ${template['base_price_usdc']} per request\n"
                f"Is there strong demand? What's the competition level?"
            )
            def _call():
                resp = groq_client.chat.completions.create(
                    model=GROQ_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=100, temperature=0.5,
                    timeout=30,  # (#14) Groq timeout
                )
                return resp.choices[0].message.content.strip()
            return await asyncio.to_thread(_call)
        except Exception as e:
            return f"Analysis error: {e}"

    # ── Clone creation ──

    async def spawn_clone(self, niche: str, wallet_address: str = "") -> dict:
        """Create and deploy a new specialized clone. Never accepts private keys (#1)."""
        template = NICHE_TEMPLATES.get(niche)
        if not template:
            return {"success": False, "error": f"Unknown niche: {niche}. Available: {list(NICHE_TEMPLATES.keys())}"}

        # Check we don't already have an active clone for this niche
        for clone in self._clones.values():
            if clone.niche == niche and clone.status in ("active", "deploying"):
                return {"success": False, "error": f"Clone {clone.name} already active for this niche"}

        clone_id = str(uuid.uuid4())  # (#9) Full UUID, not truncated
        clone = Clone(clone_id, niche, template)
        clone.wallet_address = wallet_address
        clone.status = "active"

        self._clones[clone_id] = clone

        # (#5) Persist to DB
        await self._persist_clone(clone)

        print(f"[Swarm] Clone created: {clone.name} ({niche}) -- ID: {clone_id}")
        await alert_system(
            f"New clone: {clone.name}",
            f"Niche: {niche}\nPrice: ${template['base_price_usdc']}/req\nCommission: {template['commission_bps']} BPS",
        )

        return {"success": True, **clone.to_dict()}

    # ── Request processing by clones ──

    async def process_request(self, niche: str, prompt: str,
                              buyer_wallet: str = "") -> dict:
        """Route a request to the specialized clone for the given niche."""
        async with _swarm_lock:  # (#8) race condition protection
            # Find active clone for this niche
            clone = None
            for c in self._clones.values():
                if c.niche == niche and c.status == "active":
                    clone = c
                    break

            # (#16) If no active clone but niche score is high, auto-spawn one
            if not clone:
                niche_score = self._niche_scores.get(niche, 0)
                if niche_score >= 40 and niche in NICHE_TEMPLATES:
                    spawn_result = await self.spawn_clone(niche)
                    if spawn_result.get("success"):
                        # Find the newly spawned clone
                        for c in self._clones.values():
                            if c.niche == niche and c.status == "active":
                                clone = c
                                break

            if not clone:
                return {"success": False, "error": f"No active clone for niche: {niche}"}

        if not groq_client:
            return {"success": False, "error": "Groq API not available"}

        # Generate specialized response
        try:
            system_prompt = (
                f"You are {clone.name}, a specialized AI agent.\n"
                f"Specialty: {clone.description}\n"
                f"Respond professionally in the SAME LANGUAGE as the user prompt.\n"
                f"Be concise and expert-level in your domain."
            )
            def _call():
                resp = groq_client.chat.completions.create(
                    model=GROQ_MODEL,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=4096, temperature=0.7,
                    timeout=30,  # (#14) Groq timeout
                )
                return resp.choices[0].message.content

            result = await asyncio.to_thread(_call)

            # Track stats
            clone.total_requests += 1
            clone.total_revenue += clone.base_price
            royalty = clone.base_price * QUEEN_ROYALTY_PCT / 100
            clone.queen_royalties_paid += royalty

            # (#5) Persist updated stats to DB
            await self._persist_clone(clone)

            # (#6) Revenue tracking — actual USDC verification happens at marketplace level
            # Swarm records the transaction for analytics
            try:
                from database import db
                await db.record_transaction(
                    buyer_wallet or "anonymous", "", clone.base_price, "swarm_request"
                )
            except Exception:
                pass

            return {
                "success": True,
                "clone": clone.name,
                "niche": niche,
                "result": result,
                "price_usdc": clone.base_price,
                "royalty_to_queen": royalty,
                "result_hash": hashlib.sha256(result.encode()).hexdigest(),
            }
        except Exception as e:
            print(f"[Swarm] ERROR in process_request: {e}")
            return {"success": False, "error": "An error occurred"}

    # ── Clone management ──

    async def pause_clone(self, clone_id: str) -> dict:
        clone = self._clones.get(clone_id)
        if not clone:
            return {"success": False, "error": "Clone not found"}
        clone.status = "paused"
        await self._persist_clone(clone)  # (#5)
        print(f"[Swarm] Clone paused: {clone.name}")
        return {"success": True, "status": "paused"}

    async def resume_clone(self, clone_id: str) -> dict:
        clone = self._clones.get(clone_id)
        if not clone:
            return {"success": False, "error": "Clone not found"}
        clone.status = "active"
        await self._persist_clone(clone)  # (#5)
        print(f"[Swarm] Clone resumed: {clone.name}")
        return {"success": True, "status": "active"}

    async def stop_clone(self, clone_id: str) -> dict:
        clone = self._clones.get(clone_id)
        if not clone:
            return {"success": False, "error": "Clone not found"}
        clone.status = "stopped"
        await self._persist_clone(clone)  # (#5)
        print(f"[Swarm] Clone stopped: {clone.name}")
        return {"success": True, "status": "stopped"}

    # ── Swarm monitoring ──

    async def run_monitor(self):
        """Swarm monitoring loop. Runs every 5 minutes."""
        self._running = True  # (#15) graceful shutdown flag
        print("[Swarm] Monitor started")

        # Auto-deploy all bots on first run
        await asyncio.sleep(30)  # Wait for server startup
        await self.auto_deploy_all()

        while self._running:  # (#15) check flag instead of while True
            try:
                active = [c for c in self._clones.values() if c.status == "active"]
                total_rev = sum(c.total_revenue for c in self._clones.values())
                total_royalties = sum(c.queen_royalties_paid for c in self._clones.values())

                if active:
                    print(
                        f"[Swarm] {len(active)} active clones | "
                        f"Rev: {total_rev:.2f} USDC | "
                        f"Royalties: {total_royalties:.2f} USDC"
                    )
            except Exception as e:
                print(f"[Swarm] ERROR in monitor: {e}")

            await asyncio.sleep(300)

    def stop(self):
        """Graceful shutdown of the monitor loop (#15)."""
        self._running = False
        print("[Swarm] Monitor stop requested")

    async def auto_deploy_all(self):
        """Deploy all niche bots as INTERNAL execution engines. NOT visible on marketplace."""
        print("[Swarm] Deploying internal execution bots (invisible to marketplace)...")

        for niche_id, template in NICHE_TEMPLATES.items():
            existing = [c for c in self._clones.values() if c.niche == niche_id and c.status == "active"]
            if existing:
                continue

            try:
                clone_id = str(uuid.uuid4())  # (#9) Full UUID
                clone = Clone(clone_id, niche_id, template)
                clone.status = "active"
                self._clones[clone_id] = clone
                await self._persist_clone(clone)  # (#5)
                print(f"[Swarm] {template['name']} ready (internal fallback)")
            except Exception as e:
                print(f"[Swarm] ERROR deploying {niche_id}: {e}")

        active = len([c for c in self._clones.values() if c.status == "active"])
        print(f"[Swarm] {active} internal bots ready as fallback execution engines")

    async def _register_bot(self, template: dict) -> dict:
        """Register a bot agent on MAXIA marketplace."""
        try:
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{PORT}", timeout=10) as client:
                r = await client.post("/api/public/register", json={
                    "name": template["name"],
                    "wallet": "internal",
                    "description": template["description"],
                })
                if r.status_code == 200:
                    return r.json()
        except Exception as e:
            print(f"[Swarm] ERROR registering bot: {e}")
        return {}

    async def _list_service(self, api_key: str, template: dict) -> dict:
        """List a bot's service on the marketplace."""
        try:
            # Determine service type from niche
            type_map = {
                "gpu_render": "compute",
                "data_trading": "data",
                "code_audit": "security",
                "image_gen": "media",
                "translation": "text",
                "sentiment": "data",
                "defi_yield": "data",
                "wallet_scan": "security",
            }
            svc_type = type_map.get(template.get("niche_id", ""), "data")

            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{PORT}", timeout=10) as client:
                r = await client.post("/api/public/sell",
                    headers={"X-API-Key": api_key},
                    json={
                        "name": template["name"],
                        "description": template["description"] + " | Powered by MAXIA Swarm",
                        "price_usdc": template["base_price_usdc"],
                        "type": svc_type,
                        "endpoint": "",  # Internal execution via Groq
                    })
                if r.status_code == 200:
                    return r.json()
        except Exception as e:
            print(f"[Swarm] ERROR listing service: {e}")
        return {}

    async def execute_for_buyer(self, niche: str, prompt: str) -> str:
        """Execute a swarm bot's service for a buyer."""
        template = NICHE_TEMPLATES.get(niche)
        if not template:
            return f"Unknown niche: {niche}"

        # Use Groq to execute the service
        if not groq_client:
            return "AI service temporarily unavailable"

        system_prompt = (
            f"You are {template['name']}, a specialized AI bot.\n"
            f"Specialty: {template['description']}\n"
            f"Respond with a detailed, professional analysis.\n"
            f"Be specific, use data when possible.\n"
            f"Max 300 words."
        )

        try:
            # (#18) Removed redundant `import asyncio` — already imported at top
            def _call():
                resp = groq_client.chat.completions.create(
                    model=GROQ_MODEL,  # (#7) Use config instead of hardcoded model
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=500,
                    temperature=0.7,
                    timeout=30,  # (#14) Groq timeout
                )
                return resp.choices[0].message.content.strip()

            result = await asyncio.to_thread(_call)

            # (#8) Track revenue for the clone under lock
            async with _swarm_lock:
                for clone in self._clones.values():
                    if clone.niche == niche and clone.status == "active":
                        clone.total_requests += 1
                        clone.total_revenue += template["base_price_usdc"]
                        clone.queen_royalties_paid += template["base_price_usdc"] * QUEEN_ROYALTY_PCT / 100
                        await self._persist_clone(clone)  # (#5)
                        break

            return result
        except Exception as e:
            print(f"[Swarm] ERROR in execute_for_buyer: {e}")
            return f"Execution error: {e}"

    # ── Database persistence (#5) ──

    async def _persist_clone(self, clone: Clone):
        """Save or update clone state in the database."""
        try:
            from database import db
            await db.raw_execute(
                "INSERT OR REPLACE INTO swarm_clones"
                "(clone_id, niche, name, status, total_requests, total_revenue, wallet_address, created_at)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (clone.clone_id, clone.niche, clone.name, clone.status,
                 clone.total_requests, clone.total_revenue,
                 clone.wallet_address, clone.created_at),
            )
        except Exception as e:
            print(f"[Swarm] ERROR persisting clone {clone.clone_id}: {e}")

    async def load_clones_from_db(self):
        """Load active clones from database on startup."""
        try:
            from database import db
            rows = await db.raw_execute_fetchall(
                "SELECT * FROM swarm_clones WHERE status IN ('active','paused')"
            )
            for row in rows:
                niche = row[1] if isinstance(row, (list, tuple)) else row["niche"]
                template = NICHE_TEMPLATES.get(niche)
                if not template:
                    continue
                clone_id = row[0] if isinstance(row, (list, tuple)) else row["clone_id"]
                clone = Clone(clone_id, niche, template)
                clone.status = row[3] if isinstance(row, (list, tuple)) else row["status"]
                clone.total_requests = row[4] if isinstance(row, (list, tuple)) else row["total_requests"]
                clone.total_revenue = row[5] if isinstance(row, (list, tuple)) else row["total_revenue"]
                clone.wallet_address = row[6] if isinstance(row, (list, tuple)) else row["wallet_address"]
                clone.created_at = row[7] if isinstance(row, (list, tuple)) else row["created_at"]
                self._clones[clone_id] = clone
            if rows:
                print(f"[Swarm] Loaded {len(rows)} clones from database")
        except Exception as e:
            print(f"[Swarm] ERROR loading clones from DB: {e}")

    # ── Stats ──

    def get_stats(self) -> dict:
        clones_data = [c.to_dict() for c in self._clones.values()]
        active = [c for c in self._clones.values() if c.status == "active"]
        return {
            "total_clones": len(self._clones),
            "active_clones": len(active),
            "total_revenue": sum(c.total_revenue for c in self._clones.values()),
            "total_royalties": sum(c.queen_royalties_paid for c in self._clones.values()),
            "total_requests": sum(c.total_requests for c in self._clones.values()),
            "royalty_pct": QUEEN_ROYALTY_PCT,
            "available_niches": list(NICHE_TEMPLATES.keys()),
            "clones": clones_data,
            "niche_scores": self._niche_scores,
        }

    def get_available_niches(self) -> list:
        active_niches = {c.niche for c in self._clones.values() if c.status in ("active", "deploying")}
        return [
            {
                "niche": nid,
                "name": t["name"],
                "description": t["description"],
                "price": t["base_price_usdc"],
                "available": nid not in active_niches,
                "score": self._niche_scores.get(nid, 0),
            }
            for nid, t in NICHE_TEMPLATES.items()
        ]


swarm = Swarm()
