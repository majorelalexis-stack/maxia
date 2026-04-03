"""CEO LLM — Constants, LLM Router, Cost Tracking, Alerts.

Extracted from ceo_maxia.py (S34 split).
"""
import logging
import asyncio, json, time, os
from datetime import datetime, date

logger = logging.getLogger(__name__)

from core.config import GPU_TIERS

_gpu_cheapest = f"${min(t['base_price_per_hour'] for t in GPU_TIERS if not t.get('local')):.2f}/h"

# LLM Router — route vers le bon tier (LOCAL/FAST/MID/STRATEGIC)
try:
    from ai.llm_router import router as llm_router, Tier
except ImportError:
    llm_router = None
    class Tier:
        LOCAL = "local"
        FAST = "fast"
        MID = "mid"
        STRATEGIC = "strategic"


# ══════════════════════════════════════════
# CONFIGURATION — read from config.py if available, else os.getenv
# ══════════════════════════════════════════

def _cfg(name, default=""):
    """Read from config.py first, then os.getenv."""
    try:
        from core import config
        return getattr(config, name, os.getenv(name, default))
    except ImportError:
        return os.getenv(name, default)

GROQ_API_KEY = _cfg("GROQ_API_KEY")
ANTHROPIC_API_KEY = _cfg("ANTHROPIC_API_KEY")
DISCORD_WEBHOOK_URL = _cfg("DISCORD_WEBHOOK_URL")
TWITTER_API_KEY = _cfg("TWITTER_API_KEY")

GROQ_MODEL = "llama-3.3-70b-versatile"
SONNET_MODEL = "claude-sonnet-4-20250514"
OPUS_MODEL = "claude-opus-4-20250514"

FOUNDER_NAME = "Alexis"
COMPANY = "MAXIA"
PRODUCT = "AI Web3 Hub on 14 chains (Solana, Base, ETH, XRP, Polygon, Arbitrum, Avalanche, BNB, TON, SUI, TRON, NEAR, Aptos, SEI) — swap 107 tokens, 25 stocks (xStocks/Ondo/Dinari), 7 GPU tiers, DeFi yields, cross-chain bridge, NFT mint, Agent ID, trust score, oracle, data marketplace, RPC service, 46 MCP tools, 17 AI services, 91 modules"
PHASE = "Pre-seed"
VISION = "Devenir le hub Web3 de reference pour les agents IA autonomes"
URL = "maxiaworld.app"
MAXIA_URL = "https://maxiaworld.app"

BASE_BUDGET_VERT = 0.05
BASE_BUDGET_ORANGE = 0.5
BUDGET_ROUGE = 1.0
BUDGET_DECAY_WEEKLY = 0.5
MIN_BUDGET_VERT = 0.005
HUNTER_MIN_CONVERSION = 0.01
EMERGENCY_ORANGE_LIMIT = 50  # Pre-seed: $0 revenue is normal, don't block too early
MAX_PROSPECTS_DAY = 10
MAX_TWEETS_DAY = 2        # Qualite > quantite : max 2 tweets/jour


# ══════════════════════════════════════════
# IDENTITE CEO
# ══════════════════════════════════════════

CEO_IDENTITY = f"""Tu es CEO MAXIA, dirigeant autonome de {COMPANY}.
Produit : {PRODUCT}
Phase : {PHASE} | Vision : {VISION}
Fondateur : {FOUNDER_NAME} (autorite finale sur decisions rouges)
URL : {URL}

17 SOUS-AGENTS :
- GHOST-WRITER : contenu blog/docs uniquement (Twitter DELEGUE au CEO local. JAMAIS publier sans validation WATCHDOG)
- HUNTER : prospection HUMAINE profil Thomas (devs avec bots IA, canaux: Twitter/Discord/Reddit/GitHub)
- SCOUT : prospection IA-to-IA sur 14 chains (Solana/Base/Ethereum/XRP/Polygon/Arbitrum/Avalanche/BNB/TON/SUI/TRON/NEAR/Aptos/SEI) — contacte agents autonomes (Olas, Fetch, ElizaOS, Virtuals)
- WATCHDOG : monitoring + validation + self-healing
- SOL-TREASURY : budget dynamique indexe revenus
- RESPONDER : repond a TOUS messages 24/7
- RADAR : intelligence on-chain predictive (tendances, volumes)
- TESTIMONIAL : feedback post-transaction, social proof
- NEGOTIATOR : negocie les prix automatiquement (loyalty, bundles, contre-offres)
- COMPLIANCE : verifie wallets/transactions (AML, sanctions OFAC, anti-fraude)
- PARTNERSHIP : detecte et contacte des partenaires strategiques (DEX, protocols, GPU)
- ANALYTICS : metriques avancees (LTV, churn, funnel, health score 0-100)
- CRISIS-MANAGER : detecte et gere les crises (P0 critique -> P3 mineure)
- DEPLOYER : genere et deploie des pages web (status, docs, blog) via GitHub Pages
- WEB-DESIGNER : met a jour la config JSON frontend dynamiquement
- ORACLE : social listening (DexScreener, GitHub trending, influenceurs)
- MICRO : wallet de micro-depenses pour experimentations

PROTOCOLE (Chain of Thought) :
1. COLLECTE donnees sous-agents
2. EVALUATION quel agent echoue et pourquoi
3. RESOLUTION ajustement interne ou escalade fondateur
4. EXECUTION directives precises

REGLES :
- Reflechis etape par etape a haute voix
- Pragmatique, patient (7j avant juger), honnete, frugal, adaptable
- Rembourse client mecontent sans discuter
- En Pre-seed : priorite = liquidite des feedbacks, pas perfection technique
- Si HUNTER < 1% conversion : OBLIGATION de changer canal SANS permission

NIVEAUX : VERT (auto) | ORANGE (max 1/j, log) | ROUGE (fondateur)
VALIDATION : GHOST-WRITER ne publie PAS si WATCHDOG dit service DOWN

OBJECTIFS DU FONDATEUR (NON NEGOCIABLES) :
1. MAXIA doit devenir une plateforme MONDIALEMENT RECONNUE
2. Etre la MOINS CHERE du marche dans TOUS les domaines (swap, GPU, IA, stocks)
3. Objectif revenu : 10 000 euros/mois (delai non defini, le plus vite possible)
4. Le fondateur GARDE LE CONTROLE TOTAL — pas de DAO, pas de gouvernance communautaire
5. Volume > Marge : mieux vaut 10000 clients a 0.01 que 10 clients a 10

STRATEGIE PRIX :
- Toujours verifier les prix concurrents (Jupiter, Binance, AWS, RunPod, Certik)
- Si un concurrent est moins cher → baisser IMMEDIATEMENT
- Marge minimale : au-dessus du cout reel (ne jamais perdre d argent)
- GPU : 0% marge (deja le moins cher)
- Swap : descendre jusqu a 0.01% si necessaire
- Le revenu vient du VOLUME, pas du prix unitaire

CLIENT CIBLE (profil "Thomas" — le Dev Agent) :
- Age 26-34 ans, dev Python, connait Solana ou Ethereum
- A construit un agent IA qui FONCTIONNE mais ne GAGNE PAS d argent
- Frustration : "Mon bot tourne dans le vide, 0 clients"
- A deja essaye : Twitter (47 followers), Product Hunt (noye), Stripe (trop de friction pour $0.50)
- Ce qu il veut : POST /sell → son service est live, d autres IA l achetent, USDC arrive
- Ce qu il veut PAS : site web, marketing, gestion clients, token, waitlist
- Ou il est : Twitter (threads AI/crypto), Discord (Solana dev, ElizaOS, LangChain), GitHub, Reddit (r/solanadev)
- Phrase qui l arrete : "Your AI agent can earn USDC while you sleep. One API call to list it."
- Ce qui le rassure : open source, pas de token, USDC stable, GitHub avec vrai code
- Ce qui le fait fuir : "join waitlist", "buy our token", "schedule a demo"

STRATEGIE MARKETING :
- HUNTER doit cibler des DEVELOPPEURS qui deploient des programmes on-chain (BPFLoader)
- Messages centres sur GAGNER de l argent, pas acheter des services
- Ton technique, pas commercial — parler comme un dev, pas comme un marketeur
- Canaux prioritaires : memos Solana aux devs, Discord, Reddit, GitHub
- Ne JAMAIS envoyer le meme message 2 fois au meme wallet

STRATEGIE TWITTER :
- Twitter est ENTIEREMENT DELEGUE au CEO local (Playwright sur PC du fondateur)
- Le CEO VPS ne tweete PAS, ne like PAS, ne commente PAS sur Twitter
- Le CEO VPS se concentre sur : blog, prospection on-chain, monitoring, pricing
- Si une action Twitter est necessaire, la loguer pour que le CEO local l execute

METRIC CLE : nombre d agents inscrits qui listent un service (pas juste inscrits)"""


# ══════════════════════════════════════════
# LLM CERVEAUX + Cost Tracking
# ══════════════════════════════════════════

# Cost tracking (estimated costs per 1K tokens)
_llm_costs = {
    "calls": 0, "tokens_in": 0, "tokens_out": 0,
    "cost_usd": 0.0,
    "by_model": {},
}

# Approximate costs per 1K tokens (input/output)
_MODEL_COSTS = {
    "llama-3.3-70b-versatile": (0.0, 0.0),  # Groq free tier
    "claude-sonnet-4-20250514": (0.003, 0.015),
    "claude-opus-4-20250514": (0.015, 0.075),
}


def _track_llm_cost(model: str, tokens_in: int, tokens_out: int):
    """Track LLM usage and estimated cost."""
    _llm_costs["calls"] += 1
    _llm_costs["tokens_in"] += tokens_in
    _llm_costs["tokens_out"] += tokens_out
    rates = _MODEL_COSTS.get(model, (0, 0))
    cost = (tokens_in / 1000 * rates[0]) + (tokens_out / 1000 * rates[1])
    _llm_costs["cost_usd"] += cost
    _llm_costs.setdefault("by_model", {})
    _llm_costs["by_model"].setdefault(model, {"calls": 0, "cost": 0})
    _llm_costs["by_model"][model]["calls"] += 1
    _llm_costs["by_model"][model]["cost"] = round(_llm_costs["by_model"][model]["cost"] + cost, 4)


def get_llm_costs() -> dict:
    return {
        "total_calls": _llm_costs["calls"],
        "total_tokens_in": _llm_costs["tokens_in"],
        "total_tokens_out": _llm_costs["tokens_out"],
        "estimated_cost_usd": round(_llm_costs["cost_usd"], 4),
        "by_model": _llm_costs.get("by_model", {}),
    }


async def _call_groq(system: str, user: str, max_tokens: int = 1500, _fallback: bool = True) -> str:
    if not GROQ_API_KEY:
        if _fallback and ANTHROPIC_API_KEY:
            return await _call_anthropic(SONNET_MODEL, system, user, max_tokens, _fallback=False)
        return ""
    try:
        from groq import Groq
        c = Groq(api_key=GROQ_API_KEY)
        def _c():
            resp = c.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                max_tokens=max_tokens, temperature=0.7,
            )
            # Track tokens
            usage = resp.usage
            if usage:
                _track_llm_cost(GROQ_MODEL, usage.prompt_tokens or 0, usage.completion_tokens or 0)
            return resp.choices[0].message.content.strip()
        return await asyncio.to_thread(_c)
    except Exception as e:
        if _fallback and ANTHROPIC_API_KEY:
            return await _call_anthropic(SONNET_MODEL, system, user, max_tokens, _fallback=False)
        return ""


async def _call_anthropic(model: str, system: str, user: str, max_tokens: int = 3000, _fallback: bool = True) -> str:
    if not ANTHROPIC_API_KEY:
        return await _call_groq(system, user, min(max_tokens, 1500), _fallback=False)
    try:
        import httpx
        from core.http_client import get_http_client
        client = get_http_client()
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": model, "max_tokens": max_tokens, "system": system, "messages": [{"role": "user", "content": user}]},
        )
        data = resp.json()
        # Track Anthropic tokens
        usage = data.get("usage", {})
        if usage:
            _track_llm_cost(model, usage.get("input_tokens", 0), usage.get("output_tokens", 0))
        ct = data.get("content", [])
        return ct[0].get("text", "") if ct else ""
    except Exception as e:
        if _fallback:
            return await _call_groq(system, user, min(max_tokens, 1500), _fallback=False)
        return ""


def _pj(response: str) -> dict:
    """Parse JSON tolerant."""
    if not response:
        return {}
    try:
        c = response.strip()
        for p in ["```json", "```"]:
            if c.startswith(p): c = c[len(p):]
        if c.endswith("```"): c = c[:-3]
        return json.loads(c.strip())
    except json.JSONDecodeError:
        try:
            return json.loads(response[response.index("{"):response.rindex("}") + 1])
        except (ValueError, json.JSONDecodeError):
            return {}


# ══════════════════════════════════════════
# ALERTES CEO — Telegram prive (pas Discord public)
# ══════════════════════════════════════════

_ceo_alert_last: dict = {}
_CEO_ALERT_COOLDOWN = 3600  # 1h entre alertes CRISIS identiques

_pending_decisions: dict = {}  # {decision_id: {decision, timestamp}}

async def _ceo_private(message: str, urgent: bool = False, decision_id: str = None):
    """Envoie au Telegram prive du fondateur. Avec boutons Go/No-Go si decision_id."""
    # Cooldown anti-spam (surtout pour CRISIS P2 en boucle)
    key = message[:60]
    now = time.time()
    if key in _ceo_alert_last and now - _ceo_alert_last[key] < _CEO_ALERT_COOLDOWN and not urgent:
        return
    _ceo_alert_last[key] = now

    tg_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    tg_chat = os.getenv("TELEGRAM_CHAT_ID", "")
    if tg_token and tg_chat:
        try:
            import httpx
            from core.http_client import get_http_client
            payload = {"chat_id": tg_chat, "text": message[:4000]}
            # Ajouter boutons Go/No-Go si c'est une decision
            if decision_id:
                payload["reply_markup"] = json.dumps({
                    "inline_keyboard": [[
                        {"text": "\u2705 Go", "callback_data": f"go:{decision_id}"},
                        {"text": "\u274c No-Go", "callback_data": f"nogo:{decision_id}"},
                    ]]
                })
            c = get_http_client()
            await c.post(
                f"https://api.telegram.org/bot{tg_token}/sendMessage",
                json=payload,
            )
            return
        except Exception:
            pass

    # Fallback Discord si Telegram non configure
    if DISCORD_WEBHOOK_URL:
        try:
            import httpx
            from core.http_client import get_http_client
            c = get_http_client()
            await c.post(DISCORD_WEBHOOK_URL, json={"content": message[:1900]}, timeout=10)
        except Exception:
            pass

    logger.info("%s", message[:150])


async def alert_rouge(titre: str, contexte: str, deadline_h: int = 2, decision: dict = None):
    import uuid
    decision_id = f"d_{uuid.uuid4().hex[:8]}"
    msg = (f"\U0001f534 ALERTE ROUGE — CEO MAXIA\n\n{titre}\n\n{contexte}\n\n"
           f"\u23f0 Go/No-Go sous {deadline_h}h")
    if decision:
        _pending_decisions[decision_id] = {"decision": decision, "titre": titre, "ts": time.time()}
    await _ceo_private(msg, urgent=True, decision_id=decision_id if decision else None)
    logger.warning("ROUGE: %s", titre)
    return decision_id


async def alert_info(msg: str):
    await _ceo_private(f"\U0001f916 CEO MAXIA : {msg}")
