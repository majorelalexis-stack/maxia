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
    RAILWAY_API_TOKEN, GROWTH_MONTHLY_BUDGET,
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
    "gpu_render": {
        "name": "MAXIA-GPU-Render",
        "description": "GPU rendering for 3D artists, YouTubers, game devs",
        "target_keywords": ["render", "blender", "3d", "animation", "video"],
        "base_price_usdc": 0.49,
        "commission_bps": 300,
        "marketing_lang": "en",
        "marketing_pitch": "AI-powered GPU rendering. 40% cheaper than Render Network. Instant USDC settlement.",
    },
    "data_trading": {
        "name": "MAXIA-Data-Trading",
        "description": "Real-time DeFi/crypto trading data and signals",
        "target_keywords": ["defi", "trading", "signals", "data", "analytics"],
        "base_price_usdc": 0.99,
        "commission_bps": 200,
        "marketing_lang": "en",
        "marketing_pitch": "Institutional-grade DeFi data. On-chain analytics via x402. Pay per query.",
    },
    "code_audit": {
        "name": "MAXIA-Code-Audit",
        "description": "Automated smart contract security audits",
        "target_keywords": ["audit", "security", "smart contract", "solidity", "rust"],
        "base_price_usdc": 4.99,
        "commission_bps": 150,
        "marketing_lang": "en",
        "marketing_pitch": "AI smart contract auditor. Find vulns in seconds. Cheaper than manual audits.",
    },
    "image_gen": {
        "name": "MAXIA-Creative",
        "description": "AI image generation and prompt engineering",
        "target_keywords": ["image", "art", "creative", "nft", "design"],
        "base_price_usdc": 0.29,
        "commission_bps": 400,
        "marketing_lang": "en",
        "marketing_pitch": "AI art generation on Solana. Pay per image with USDC. No subscription needed.",
    },
    "translation": {
        "name": "MAXIA-Translate",
        "description": "Professional AI translation (50+ languages)",
        "target_keywords": ["translate", "language", "localization", "i18n"],
        "base_price_usdc": 0.19,
        "commission_bps": 350,
        "marketing_lang": "en",
        "marketing_pitch": "AI translation API. 50+ languages. Pay per request via x402. Sub-second response.",
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
            if db and db._db:
                try:
                    keywords = template["target_keywords"]
                    count = 0
                    for kw in keywords:
                        async with db._db.execute(
                            "SELECT COUNT(*) FROM commands WHERE json_extract(data,'$.serviceId') LIKE ?",
                            (f"%{kw}%",)
                        ) as c:
                            row = await c.fetchone()
                            count += (row[0] if row else 0)
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
