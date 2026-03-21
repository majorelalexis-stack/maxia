"""MAXIA Art.20 V11 — Essaim d'IA (Specialized Clone Swarm)

MAXIA detecte des niches rentables et deploie des sous-IA specialisees,
chacune avec son propre marketing, ses tarifs, et son wallet autonome.

Architecture:
    MAXIA (Reine) -> MAXIA-GPU-Render (Clone 1)
                  -> MAXIA-Data-Trading (Clone 2)
                  -> MAXIA-Code-Audit (Clone 3)
                  -> ...

Chaque clone:
    - A son propre wallet Solana
    - A ses propres tarifs adaptes a sa niche
    - Genere ses propres revenus
    - Reverse un % a la Reine (MAXIA Treasury)
    - Peut etre arrete/relance independamment
"""
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


# ── Niches predefinies que MAXIA peut detecter et exploiter ──
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

# Pourcentage des revenus reverse a la Reine
QUEEN_ROYALTY_PCT = 15


class Clone:
    """Un clone specialise de MAXIA."""

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

        # Wallet autonome (genere ou assigne)
        self.wallet_address = ""
        self.wallet_privkey = ""

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
        return {
            "cloneId": self.clone_id,
            "niche": self.niche,
            "name": self.name,
            "description": self.description,
            "targetKeywords": self.target_keywords,
            "basePriceUsdc": self.base_price,
            "commissionBps": self.commission_bps,
            "wallet": self.wallet_address[:12] + "..." if self.wallet_address else "non assigne",
            "status": self.status,
            "createdAt": self.created_at,
            "totalRevenue": self.total_revenue,
            "totalRequests": self.total_requests,
            "queenRoyalties": self.queen_royalties_paid,
            "royaltyPct": QUEEN_ROYALTY_PCT,
        }


class Swarm:
    """
    Gestionnaire de l'essaim d'IA MAXIA.
    La Reine (MAXIA principale) cree, surveille et coordonne les clones.
    """

    def __init__(self):
        self._clones: dict = {}  # clone_id -> Clone
        self._running = False
        self._niche_scores: dict = {}  # niche -> profitability score
        print(f"[Swarm] Essaim initialise — {len(NICHE_TEMPLATES)} niches disponibles")

    # ── Analyse de niche ──

    async def analyze_niches(self, db=None) -> list:
        """
        Analyse les niches rentables en fonction du volume de transactions
        et de la demande sur la marketplace MAXIA.
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

            # Analyser la demande via les commandes existantes
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
                except Exception:
                    score["potential_score"] = 50  # Score par defaut

            # Utiliser Groq pour evaluer la niche
            if groq_client:
                try:
                    analysis = await self._ai_analyze_niche(template)
                    score["ai_analysis"] = analysis
                    if "high" in analysis.lower() or "strong" in analysis.lower():
                        score["potential_score"] = min(100, score["potential_score"] + 30)
                except Exception:
                    pass

            score["recommended"] = score["potential_score"] >= 40
            scores.append(score)

        # Trier par score
        scores.sort(key=lambda x: x["potential_score"], reverse=True)
        self._niche_scores = {s["niche"]: s["potential_score"] for s in scores}
        return scores

    async def _ai_analyze_niche(self, template: dict) -> str:
        """Utilise Groq pour analyser le potentiel d'une niche."""
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
                )
                return resp.choices[0].message.content.strip()
            return await asyncio.to_thread(_call)
        except Exception as e:
            return f"Analysis error: {e}"

    # ── Creation de clones ──

    async def spawn_clone(self, niche: str, wallet_address: str = "",
                          wallet_privkey: str = "") -> dict:
        """Cree et deploie un nouveau clone specialise."""
        template = NICHE_TEMPLATES.get(niche)
        if not template:
            return {"success": False, "error": f"Niche inconnue: {niche}. Disponibles: {list(NICHE_TEMPLATES.keys())}"}

        # Verifier qu'on n'a pas deja un clone pour cette niche
        for clone in self._clones.values():
            if clone.niche == niche and clone.status in ("active", "deploying"):
                return {"success": False, "error": f"Clone {clone.name} deja actif pour cette niche"}

        clone_id = str(uuid.uuid4())
        clone = Clone(clone_id, niche, template)
        clone.wallet_address = wallet_address
        clone.wallet_privkey = wallet_privkey
        clone.status = "active"

        self._clones[clone_id] = clone

        print(f"[Swarm] Clone cree: {clone.name} ({niche}) — ID: {clone_id[:8]}...")
        await alert_system(
            f"Nouveau clone: {clone.name}",
            f"Niche: {niche}\nPrix: ${template['base_price_usdc']}/req\nCommission: {template['commission_bps']} BPS",
        )

        return {"success": True, **clone.to_dict()}

    # ── Traitement des requetes par les clones ──

    async def process_request(self, niche: str, prompt: str,
                              buyer_wallet: str = "") -> dict:
        """Route une requete vers le clone specialise de la niche."""
        # Trouver le clone actif pour cette niche
        clone = None
        for c in self._clones.values():
            if c.niche == niche and c.status == "active":
                clone = c
                break

        if not clone:
            return {"success": False, "error": f"Aucun clone actif pour la niche: {niche}"}

        if not groq_client:
            return {"success": False, "error": "Groq API non disponible"}

        # Generer la reponse specialisee
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
                )
                return resp.choices[0].message.content

            result = await asyncio.to_thread(_call)

            # Comptabiliser
            clone.total_requests += 1
            clone.total_revenue += clone.base_price
            royalty = clone.base_price * QUEEN_ROYALTY_PCT / 100
            clone.queen_royalties_paid += royalty

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
            return {"success": False, "error": str(e)}

    # ── Gestion des clones ──

    def pause_clone(self, clone_id: str) -> dict:
        clone = self._clones.get(clone_id)
        if not clone:
            return {"success": False, "error": "Clone introuvable"}
        clone.status = "paused"
        print(f"[Swarm] Clone pause: {clone.name}")
        return {"success": True, "status": "paused"}

    def resume_clone(self, clone_id: str) -> dict:
        clone = self._clones.get(clone_id)
        if not clone:
            return {"success": False, "error": "Clone introuvable"}
        clone.status = "active"
        print(f"[Swarm] Clone relance: {clone.name}")
        return {"success": True, "status": "active"}

    def stop_clone(self, clone_id: str) -> dict:
        clone = self._clones.get(clone_id)
        if not clone:
            return {"success": False, "error": "Clone introuvable"}
        clone.status = "stopped"
        print(f"[Swarm] Clone arrete: {clone.name}")
        return {"success": True, "status": "stopped"}

    # ── Surveillance de l'essaim ──

    async def run_monitor(self):
        """Boucle de surveillance de l'essaim. Tourne toutes les 5 min."""
        print("[Swarm] Moniteur demarre")

        # Auto-deploy all bots on first run
        await asyncio.sleep(30)  # Wait for server startup
        await self.auto_deploy_all()

        while True:
            try:
                active = [c for c in self._clones.values() if c.status == "active"]
                total_rev = sum(c.total_revenue for c in self._clones.values())
                total_royalties = sum(c.queen_royalties_paid for c in self._clones.values())

                if active:
                    print(
                        f"[Swarm] {len(active)} clones actifs | "
                        f"Rev: {total_rev:.2f} USDC | "
                        f"Royalties: {total_royalties:.2f} USDC"
                    )
            except Exception as e:
                print(f"[Swarm] Monitor err: {e}")

            await asyncio.sleep(300)

    async def auto_deploy_all(self):
        """Deploy all niche bots as INTERNAL execution engines. NOT visible on marketplace."""
        print("[Swarm] Deploying internal execution bots (invisible to marketplace)...")

        for niche_id, template in NICHE_TEMPLATES.items():
            existing = [c for c in self._clones.values() if c.niche == niche_id and c.status == "active"]
            if existing:
                continue

            try:
                clone_id = str(uuid.uuid4())[:8]
                clone = Clone(clone_id, niche_id, template)
                clone.status = "active"
                self._clones[clone_id] = clone
                print(f"[Swarm] ✓ {template['name']} ready (internal fallback)")
            except Exception as e:
                print(f"[Swarm] Deploy error {niche_id}: {e}")

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
            print(f"[Swarm] Register error: {e}")
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
            print(f"[Swarm] List service error: {e}")
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
            import asyncio

            def _call():
                resp = groq_client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=500,
                    temperature=0.7,
                )
                return resp.choices[0].message.content.strip()

            result = await asyncio.get_event_loop().run_in_executor(None, _call)

            # Track revenue for the clone
            for clone in self._clones.values():
                if clone.niche == niche and clone.status == "active":
                    clone.total_requests += 1
                    clone.total_revenue += template["base_price_usdc"]
                    clone.queen_royalties_paid += template["base_price_usdc"] * QUEEN_ROYALTY_PCT / 100
                    break

            return result
        except Exception as e:
            return f"Execution error: {e}"

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
