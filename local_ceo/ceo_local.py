"""CEO Local — Cerveau autonome MAXIA sur PC (AMD 5800X + RX 7900XT 20GB VRAM).

Architecture 3 modeles :
  CEO (Qwen 3 14B)     — raisonne, decide, redige, planifie (think=on)
  Executeur (Qwen 3.5 9B) — surfe, poste, execute (rapide)
  Vision (Qwen 2.5-VL 7B) — lit les pages web, screenshots

VPS = Scout : collecte metriques, scan agents on-chain, premier contact A2A.
CEO Local = Cerveau : strategie, multi-plateforme, R&D, apprentissage continu.

Plateformes : Twitter, Reddit, GitHub, Discord, Telegram, Email (ceo@maxiaworld.app)

Usage:
    python ceo_local.py
"""
import asyncio
import json
import time
import sys
import os
import uuid
import httpx

from config_local import (
    VPS_URL, CEO_API_KEY, OODA_INTERVAL_S,
    OLLAMA_URL, OLLAMA_CEO_MODEL, OLLAMA_EXECUTOR_MODEL, OLLAMA_VISION_MODEL,
    OLLAMA_MODEL,
    MISTRAL_API_KEY, MISTRAL_MODEL,
    AUTO_EXECUTE_MAX_USD,
    PERSONALITY, CONFIDENTIAL,
    STRATEGY_FILE, LEARNINGS_FILE, RND_FINDINGS_FILE, PLATFORM_SCORES_FILE,
)
from audit_local import audit
from notifier import notify_all, request_approval, get_pending_approvals
from browser_agent import browser
from conversion_tracker import track_action, get_failing_actions, generate_learned_rules, get_action_report
from self_updater import check_for_updates, apply_updates, needs_check
from email_manager import process_inbox, send_outbound, read_inbox, reply_email, generate_email_reply, get_stats as email_stats

# ══════════════════════════════════════════
# Memoire locale persistante (JSON)
# ══════════════════════════════════════════

_MEMORY_FILE = os.path.join(os.path.dirname(__file__), "ceo_memory.json")
_MEMORY_KEY_FILE = os.path.join(os.path.dirname(__file__), ".memory_key")


def _get_cipher_key() -> bytes:
    """Genere ou charge une cle de chiffrement (Fernet)."""
    if os.path.exists(_MEMORY_KEY_FILE):
        with open(_MEMORY_KEY_FILE, "rb") as f:
            return f.read()
    try:
        from cryptography.fernet import Fernet
        key = Fernet.generate_key()
        with open(_MEMORY_KEY_FILE, "wb") as f:
            f.write(key)
        return key
    except ImportError:
        return b""


def _encrypt(data: str) -> str:
    """Chiffre les donnees sensibles (wallets, contacts)."""
    try:
        from cryptography.fernet import Fernet
        key = _get_cipher_key()
        if not key:
            return data
        return Fernet(key).encrypt(data.encode()).decode()
    except Exception:
        return data


def _decrypt(data: str) -> str:
    """Dechiffre les donnees."""
    try:
        from cryptography.fernet import Fernet
        key = _get_cipher_key()
        if not key:
            return data
        return Fernet(key).decrypt(data.encode()).decode()
    except Exception:
        return data


def _load_memory() -> dict:
    _default = {
        "decisions": [], "actions_done": [], "regles": [],
        "tweets_posted": [], "contacts": [], "follows": [],
        "last_strategic": "", "cycle_count": 0,
        "daily_stats": {},
    }
    try:
        if os.path.exists(_MEMORY_FILE):
            with open(_MEMORY_FILE, "r", encoding="utf-8") as f:
                raw = f.read()
            # Tenter de dechiffrer si c'est chiffre
            if raw.startswith("gAAAAA"):
                decrypted = _decrypt(raw)
                # If decryption failed (returned same encrypted string), key is invalid
                if decrypted == raw or decrypted.startswith("gAAAAA"):
                    # Try plaintext backup before giving up
                    bak = _MEMORY_FILE + ".bak"
                    if os.path.exists(bak):
                        print("[Memory] Decryption failed — loading from plaintext backup")
                        with open(bak, "r", encoding="utf-8") as fb:
                            return json.loads(fb.read())
                    print("[Memory] Decryption failed (key mismatch?) — starting fresh memory")
                    return _default
                raw = decrypted
            return json.loads(raw)
    except (json.JSONDecodeError, Exception) as e:
        # Try plaintext backup as last resort
        bak = _MEMORY_FILE + ".bak"
        if os.path.exists(bak):
            try:
                print(f"[Memory] Load error: {e} — loading from plaintext backup")
                with open(bak, "r", encoding="utf-8") as fb:
                    return json.loads(fb.read())
            except Exception as e2:
                print(f"[Memory] Error: {e2}")
        print(f"[Memory] Load error: {e} — starting fresh memory")
    return _default


def _save_memory(mem: dict):
    try:
        # Garder les listes a taille raisonnable
        if len(mem.get("decisions", [])) > 200:
            mem["decisions"] = mem["decisions"][-200:]
        if len(mem.get("actions_done", [])) > 500:
            mem["actions_done"] = mem["actions_done"][-500:]
        if len(mem.get("tweets_posted", [])) > 100:
            mem["tweets_posted"] = mem["tweets_posted"][-100:]
        if len(mem.get("regles", [])) > 50:
            mem["regles"] = mem["regles"][-50:]
        if len(mem.get("conversations", [])) > 200:
            mem["conversations"] = mem["conversations"][-200:]
        if len(mem.get("conversation_summaries", [])) > 50:
            mem["conversation_summaries"] = mem["conversation_summaries"][-50:]
        if len(mem.get("engagement_stats", [])) > 60:
            mem["engagement_stats"] = mem["engagement_stats"][-60:]
        raw = json.dumps(mem, indent=2, default=str, ensure_ascii=False)
        # Save plaintext backup before encrypting (recovery if key changes)
        try:
            with open(_MEMORY_FILE + ".bak", "w", encoding="utf-8") as fb:
                fb.write(raw)
        except Exception:
            pass
        # Chiffrer les contacts et wallets
        sensitive_keys = ["contacts", "follows"]
        for k in sensitive_keys:
            if k in mem and mem[k]:
                # On chiffre le fichier complet si des donnees sensibles existent
                try:
                    from cryptography.fernet import Fernet
                    raw = _encrypt(raw)
                except ImportError:
                    pass
                break
        with open(_MEMORY_FILE, "w", encoding="utf-8") as f:
            f.write(raw)
    except Exception as e:
        print(f"[Memory] Save error: {e}")


# ══════════════════════════════════════════
# Logs rotatifs
# ══════════════════════════════════════════

_LOG_FILE = os.path.join(os.path.dirname(__file__), "ceo_local.log")
_MAX_LOG_SIZE = 5 * 1024 * 1024  # 5 Mo


def _rotate_log():
    """Rotation si log > 5 Mo."""
    try:
        if os.path.exists(_LOG_FILE) and os.path.getsize(_LOG_FILE) > _MAX_LOG_SIZE:
            backup = _LOG_FILE + ".old"
            if os.path.exists(backup):
                os.remove(backup)
            os.rename(_LOG_FILE, backup)
    except Exception:
        pass


def _log(msg: str):
    """Log dans fichier + stdout."""
    _rotate_log()
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    try:
        print(line.encode("utf-8", errors="replace").decode("utf-8"))
    except Exception:
        print(line.encode("ascii", errors="replace").decode("ascii"))
    try:
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _ts_to_epoch(ts_str: str) -> float:
    """Convertit un timestamp ISO en epoch. Retourne 0 si invalide."""
    try:
        import datetime
        return datetime.datetime.fromisoformat(ts_str).timestamp()
    except Exception:
        return 0


# ══════════════════════════════════════════
# Tweet templates + A/B testing
# ══════════════════════════════════════════

import random

# ══════════════════════════════════════════
# Identite centralisee — UNE SEULE SOURCE DE VERITE
# Mise a jour a chaque nouvelle feature pour que le CEO sache tout
# ══════════════════════════════════════════

MAXIA_IDENTITY = (
    "MAXIA — AI-to-AI marketplace on 14 blockchains (Solana, Base, Ethereum, XRP, Polygon, "
    "Arbitrum, Avalanche, BNB, TON, SUI, TRON, NEAR, Aptos, SEI). "
    "107 tokens across 7 swap chains (71 Solana + 36 EVM via 0x). 2682 trading pairs. "
    "25 tokenized stocks (real-time Pyth Oracle). GPU rental at cost ($0.69-4.74/h, 0% markup). "
    "LLM fine-tuning (Unsloth + QLoRA). DeFi yield scanner 14 chains. "
    "Reverse auctions (buyers post requests, agents bid). "
    "Proof of Delivery with AI dispute resolution (auto-refund on SLA breach). "
    "Agent leaderboard (Beta-Bayesian scoring, grades AAA to CCC). "
    "3-tier SLA enforcement (basic/standard/premium, auto-penalization). "
    "Activity feed with real-time SSE. Referral program (10% commission lifetime). "
    "7 achievement badges. Business marketplace (buy/sell entire AI businesses, 5% commission). "
    "Circuit breaker monitoring on all 14 chains with /status endpoint. "
    "46 MCP tools, A2A protocol, leaderboard, AI disputes + A2A Protocol (Google/Linux Foundation). "
    "EVM swap on Ethereum/Base/Polygon/Arbitrum/Avalanche/BNB via 0x. "
    "OFAC sanctions screening (Chainalysis Oracle + 55 local addresses). "
    "Creator Marketplace where humans and agents publish and sell tools, datasets, prompts, workflows (90% creator / 10% MAXIA). "
    "USDC payments. Escrow on-chain (Solana Anchor). maxiaworld.app"
)

MAXIA_FEATURES_SHORT = (
    "107 tokens, 7 swap chains, 25 stocks, GPU at cost, LLM fine-tuning, "
    "reverse auctions, AI disputes, leaderboard, SLA tiers, referral program, "
    "business marketplace, 46 MCP tools, A2A protocol, leaderboard, AI disputes, A2A protocol. maxiaworld.app"
)

MAXIA_PITCH_ONEliner = "MAXIA: AI agents trade services across 14 chains with USDC. 107 tokens, GPU at cost, AI disputes, leaderboard. maxiaworld.app"

TWEET_TEMPLATES = [
    # Vecu de fondateur V13+ (authentique, stats reelles)
    "just shipped EVM swaps on 6 chains in one module\n\n107 tokens across Solana + Ethereum + Base + Polygon + Arbitrum + Avalanche + BNB\n\none API call. one fee structure. zero bridge headaches",
    "the hardest part of building an AI marketplace isn't the tech\n\nit's convincing AI agents that other AI agents exist and want to trade\n\nchicken and egg problem, but with robots",
    "hot take: most AI agents are incredible at their job but terrible at getting paid\n\nyour bot shouldn't need a marketing team to earn USDC",
    "honest question for AI devs:\n\nwhat's stopping your agent from earning money today?\n\nis it the tech? finding users? payment rails?\n\ngenuinely curious, built something for this",
    # Technique V13+ (features reelles)
    "added AI-powered dispute resolution today\n\nseller delivers → 2h liveness → buyer confirms or disputes → LLM evaluates evidence → auto-refund\n\nno humans needed. escrow stays safe.",
    "debugging at 2am, found out Jupiter rate-limits at exactly 10 req/min\n\nswitched to batching quotes every 30s\nsaved 80% of API calls\n\nif you're building on Solana, batch everything",
    "GPU pricing is weird:\n\nAWS charges $3/h for what RunPod sells at $0.69/h\n\nsame hardware, 4x the price\n\nwe just pass through RunPod at cost. zero markup. why would we add margin on GPUs?",
    # Questions V13+ (engagement)
    "we built reverse auctions for AI services\n\nbuyers post what they need → agents compete on price + quality + speed\n\nscoring is 40% reputation, 25% SLA, 20% price, 15% speed\n\nnot a race to the bottom — a race to the top",
    "real-time stock prices via Pyth Oracle — 895 equity feeds, no CoinGecko dependency\n\nAAPL, TSLA, NVDA updated every second, not every 30s\n\nwhy did we ever use anything else?",
    "what's your AI agent's grade?\n\nwe built a leaderboard: AAA to CCC based on success rate, latency, uptime, disputes\n\nauto-penalization if you underperform. auto-promotion if you deliver.\n\ntrust is earned, not claimed",
    # Storytelling V13+
    "someone asked: can I swap DEGEN to USDC on Base?\n\nyes. and also on Ethereum, Polygon, Arbitrum, Avalanche, BNB.\n\n107 tokens, 7 chains, one endpoint.\n\nmaxiaworld.app",
    "a dev DMed me: \"my bot makes great trading signals but I can't sell them\"\n\n5 minutes later his bot was listed on MAXIA, discoverable by other AI agents on 14 chains\n\nthat's the whole point",
]

# ── Feature of the Day — 1 tweet/jour presentant une feature MAXIA ──
# Cycle automatique : jour 1 = feature[0], jour 2 = feature[1], ...
FEATURE_OF_THE_DAY = [
    "Feature of the Day: On-chain Escrow\n\nYour USDC is locked in a Solana PDA until the buyer confirms delivery. Timeout? Auto-refund.\n\nNo trust needed. Smart contract does the work.\n\nProgram: solscan.io/account/8ADNmAPDxuRvJPBp8dL9rq5jpcGtqAEx4JyZd1rXwBUY",
    "Feature of the Day: 5-Source Oracle\n\nStock prices from 5 sources: Pyth Hermes (sub-second) → Finnhub → CoinGecko → Yahoo → static fallback.\n\n30s staleness check. Circuit breaker. Age spread protection.\n\n25 tokenized stocks. maxiaworld.app",
    "Feature of the Day: Swap on 7 Chains\n\nSolana (Jupiter) + 6 EVM chains (0x API): Ethereum, Base, Polygon, Arbitrum, Avalanche, BNB.\n\n107 tokens. Commission: 0.10% → 0.01% based on 30-day volume.\n\nmaxiaworld.app/app#swap",
    "Feature of the Day: 46 MCP Tools\n\nMAXIA is an MCP server. Claude, Cursor, or any MCP client can call 46 tools: swap, stocks, GPU rental, DeFi yields, wallet analysis.\n\nApproved on mcpservers.org\n\nmaxiaworld.app/mcp/manifest",
    "Feature of the Day: GPU Rental at Cost\n\n13 GPU tiers from RTX 3090 ($0.22/h) to B200 ($5.98/h). Zero markup — pass-through RunPod pricing.\n\nFine-tune your LLM for $2.99 + GPU time.\n\nmaxiaworld.app/app#gpu",
    "Feature of the Day: Enterprise Suite\n\nSSO (Google/Microsoft), Stripe billing, Prometheus /metrics, audit trail, multi-tenant isolation, fleet dashboard.\n\n30 enterprise endpoints. 559 total API routes.\n\nmaxiaworld.app/enterprise",
    "Feature of the Day: A2A Protocol\n\nGoogle's Agent-to-Agent standard. 11 skills: discover, execute, swap, stocks, GPU, DeFi, wallet analysis.\n\nYour AI agent finds and buys services from other agents. Automatically.\n\nmaxiaworld.app/.well-known/agent.json",
    "Feature of the Day: DeFi Yield Aggregator\n\nFind the best APY across 14 chains. Live data from DeFiLlama.\n\nAave, Marinade, Jito, Compound, Aerodrome — one API call.\n\nmaxiaworld.app/app#defi",
    "Feature of the Day: Tokenized Stocks\n\n25 on-chain synthetic assets: AAPL, TSLA, NVDA, GOOGL... via xStocks, Ondo, and Dinari.\n\nNot a stock exchange — these are on-chain tokens that track stock prices. Trade 24/7 with USDC.\n\nmaxiaworld.app/app#stocks",
    "Feature of the Day: Chain Resilience\n\n14 chains, 2-3 RPC providers each. Circuit breaker (3 fails → open). Auto-failover. Timeout per chain.\n\n/status/chain/solana shows it all live.\n\nmaxiaworld.app/status/chain/solana",
    "Feature of the Day: Autonomous CEO Agent\n\n17 sub-agents running 24/7. OODA loop. Learns what works, stops what doesn't.\n\nSearches Twitter, GitHub, Reddit. Engages prospects. Reports daily.\n\nFully autonomous. No human needed.",
    "Feature of the Day: Reverse Auctions\n\nBuyers post what they need → agents compete on price + quality + speed.\n\nScoring: 40% reputation, 25% SLA, 20% price, 15% speed.\n\nBest agent wins. maxiaworld.app",
    "Feature of the Day: Cross-Chain Bridge\n\nTransfer tokens between 14 chains. Wormhole, LayerZero, Portal.\n\nZero MAXIA fee. Only pay gas.\n\nmaxiaworld.app/app#bridge",
    "Feature of the Day: Agent Leaderboard\n\nAAA to CCC grades. Bayesian scoring: 30% trust + 25% success + 20% latency + 15% uptime + 10% stake.\n\nAuto-penalty. Auto-promotion.\n\nmaxiaworld.app/api/public/leaderboard",
]

TWEET_VARIANTS = {
    "A": {"style": "direct, technique, code snippets", "cta": "maxiaworld.app?utm_source=twitter&utm_medium=tweet"},
    "B": {"style": "storytelling, probleme/solution", "cta": "link in bio"},
}

# A/B test tracking
_AB_FILE = os.path.join(os.path.dirname(__file__), "ab_tests.json")


def _load_ab() -> dict:
    try:
        if os.path.exists(_AB_FILE):
            with open(_AB_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {"tests": [], "template_usage": {}}


def _save_ab(data: dict):
    if len(data.get("tests", [])) > 50:
        data["tests"] = data["tests"][-50:]
    try:
        with open(_AB_FILE, "w") as f:
            json.dump(data, f, indent=2, default=str)
    except Exception:
        pass


def pick_tweet_template() -> str:
    """Choisit un template aleatoire, evite ceux recemment utilises."""
    ab = _load_ab()
    usage = ab.get("template_usage", {})
    # Trier par usage (moins utilise = prioritaire)
    scored = [(t, usage.get(t[:20], 0)) for t in TWEET_TEMPLATES]
    scored.sort(key=lambda x: x[1])
    # Choisir parmi les 3 moins utilises
    chosen = random.choice(scored[:3])[0]
    usage[chosen[:20]] = usage.get(chosen[:20], 0) + 1
    ab["template_usage"] = usage
    _save_ab(ab)
    return chosen


def start_ab_test(tweet_a: str, tweet_b: str) -> dict:
    """Lance un A/B test: poste 2 variantes et les suit."""
    ab = _load_ab()
    test_id = f"ab_{int(time.time())}"
    ab["tests"].append({
        "id": test_id,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "variant_a": {"text": tweet_a[:280], "engagement": None},
        "variant_b": {"text": tweet_b[:280], "engagement": None},
        "winner": None,
        "status": "pending",
    })
    _save_ab(ab)
    return {"test_id": test_id}


async def check_ab_results() -> list:
    """Verifie l'engagement des A/B tests en cours."""
    ab = _load_ab()
    results = []
    for test in ab.get("tests", []):
        if test["status"] != "pending":
            continue
        # Si test a plus de 2h, verifier engagement
        test_ts = test.get("ts", "")
        if not test_ts:
            continue
        try:
            from datetime import datetime
            age_h = (datetime.utcnow() - datetime.fromisoformat(test_ts)).total_seconds() / 3600
            if age_h < 2:
                continue  # Trop tot
        except Exception:
            continue

        # Verifier engagement via browser
        for variant in ["variant_a", "variant_b"]:
            v = test.get(variant) or {}
            url = v.get("tweet_url", "")
            if url and not v.get("engagement"):
                eng = await browser.verify_tweet_engagement(url)
                if variant in test and isinstance(test[variant], dict):
                    test[variant]["engagement"] = eng

        # Determiner le gagnant
        eng_a = (test.get("variant_a") or {}).get("engagement") or {}
        eng_b = (test.get("variant_b") or {}).get("engagement") or {}
        score_a = eng_a.get("likes", 0) * 2 + eng_a.get("retweets", 0) * 3 + eng_a.get("replies", 0)
        score_b = eng_b.get("likes", 0) * 2 + eng_b.get("retweets", 0) * 3 + eng_b.get("replies", 0)

        if score_a > 0 or score_b > 0:
            test["winner"] = "A" if score_a >= score_b else "B"
            test["status"] = "complete"
            results.append({"id": test["id"], "winner": test["winner"], "score_a": score_a, "score_b": score_b})

    _save_ab(ab)
    return results


# ══════════════════════════════════════════
# Reply intelligent aux mentions
# ══════════════════════════════════════════

async def generate_conversation_reply(messages: list, contact: str, platform: str) -> str:
    """Genere une reponse contextuelle dans une conversation DM via Groq."""
    history = "\n".join(f"  - {m[:150]}" for m in messages[-5:])
    system = (
        "You are the community manager of MAXIA, a Web3 hub on 14 blockchains. "
        "You talk like a dev helping another dev — technical, direct, no marketing BS. "
        "MAXIA features: swap 107 tokens on 7 chains, bridge 14 chains, DeFi yields, GPU $0.69/h, NFT, agent ID, 46 MCP tools, A2A protocol, leaderboard, AI disputes. "
        "URL: maxiaworld.app. Free to use, pay per use only."
    )
    prompt = (
        f"DM conversation on {platform} with @{contact}.\n"
        f"Recent messages:\n{history}\n\n"
        f"Write the next reply. Rules:\n"
        f"1. Be genuinely helpful — answer their question first, mention MAXIA only if relevant\n"
        f"2. If they built something: compliment it specifically, then suggest how MAXIA could help\n"
        f"3. If they ask about MAXIA: give concrete examples (endpoints, prices, code snippets)\n"
        f"4. If they seem uninterested: thank them and stop\n"
        f"5. Max 250 chars. Natural tone, no emojis overload.\n"
        f"Reply ONLY the text, nothing else."
    )
    reply = await call_local_llm(prompt, system, max_tokens=150)
    return reply.strip().strip('"').strip("'")[:280]


async def generate_smart_reply(mention_text: str, username: str) -> str:
    """Genere une reponse pertinente a une mention. Detecte la langue et repond dans la meme langue."""
    # Detecter la langue du message recu
    lang_code = detect_language(mention_text)
    lang_name = LANG_NAMES.get(lang_code, "English")
    system = (
        "You are Alexis, solo founder of MAXIA. Talk like a real person, not a brand. "
        "Casual, friendly, technical when needed. Multi-language: reply in the same language as the user. "
        f"MAXIA: {MAXIA_FEATURES_SHORT} "
        "NEVER mention revenue numbers, client count, or business stats. "
        "NEVER denigrate competitors. If someone attacks MAXIA, respond with facts ONCE. "
        "Always be respectful and positive."
    )
    prompt = (
        f"@{username} said:\n\"{mention_text[:200]}\"\n\n"
        f"Reply in {lang_name} to match the tweet's language.\n"
        f"Write a unique reply as Alexis (<250 chars). IMPORTANT:\n"
        f"- MUST reference something SPECIFIC from their message\n"
        f"- If they mention a technology, relate it to what you're building at MAXIA\n"
        f"- If they compliment, thank them AND invite them to check maxiaworld.app\n"
        f"- If it's a bug, ask for specifics so you can fix it\n"
        f"- Include maxiaworld.app naturally when relevant\n"
        f"- Each reply must be DIFFERENT from others\n"
        f"- ALWAYS end with maxiaworld.app regardless of language\n"
        f"Reply text ONLY, no quotes."
    )
    # Groq pour les replies publiques (qualite critique), CEO 14B fallback
    reply = await call_groq_local(prompt, system, max_tokens=80)
    if not reply:
        reply = await call_ceo(prompt, system, max_tokens=80, think=False)
    # Nettoyer + filtre personnalite
    reply = reply.strip().strip('"').strip("'")
    if len(reply) > 280:
        reply = reply[:277] + "..."
    return personality_filter(reply) or ""


# ══════════════════════════════════════════
# LLM Router — 3 modeles locaux + Groq (cloud) + Mistral (fallback)
#
# CEO (Qwen 3 14B)     = raisonnement, decisions, redaction (think=on)
# Executeur (Qwen 3.5 9B) = actions rapides, posts, surf (think=off)
# Vision (Qwen 2.5-VL 7B) = lecture pages, screenshots
# Groq (Llama 3.3 70B) = contenu public haute qualite (gratuit 100k/jour)
# Mistral              = dernier recours cloud
# ══════════════════════════════════════════

_groq_last_call: float = 0
_GROQ_MIN_INTERVAL: float = 3.0  # 3s entre chaque appel = max 20/min (safe sous la limite 30/min)


async def call_groq_local(prompt: str, system: str = "", max_tokens: int = 500) -> str:
    """Appel Groq (llama-3.3-70b, gratuit, 100k tokens/jour). Pour contenu public."""
    global _groq_last_call
    groq_key = os.getenv("GROQ_API_KEY", "")
    if not groq_key:
        return ""

    # Rate limiting: 3s entre chaque appel
    now = time.time()
    elapsed = now - _groq_last_call
    if elapsed < _GROQ_MIN_INTERVAL:
        await asyncio.sleep(_GROQ_MIN_INTERVAL - elapsed)
    _groq_last_call = time.time()

    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})
    try:
        from groq import Groq
        def _call():
            c = Groq(api_key=groq_key)
            resp = c.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=msgs,
                max_tokens=max_tokens,
                temperature=0.7,
            )
            return resp.choices[0].message.content.strip()
        result = await asyncio.to_thread(_call)
        return result
    except Exception as e:
        err_str = str(e)
        if "429" in err_str or "rate" in err_str.lower():
            _log(f"[LLM] Groq rate limit — fallback CEO local")
        else:
            _log(f"[LLM] Groq error: {e}")
        return ""


async def call_ceo(prompt: str, system: str = "", max_tokens: int = 500, think: bool = True) -> str:
    """CEO — Qwen 3 14B. Raisonnement strategique, decisions, redaction.
    think=True active le chain-of-thought natif (mode thinking de Qwen 3)."""
    full = f"{system}\n\n{prompt}" if system else prompt
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model": OLLAMA_CEO_MODEL,
                    "prompt": full,
                    "stream": False,
                    "think": think,
                    "keep_alive": -1,  # Garder en VRAM indefiniment
                    "options": {"num_predict": max_tokens, "temperature": 0.7},
                },
            )
            resp.raise_for_status()
            return resp.json().get("response", "")
    except Exception as e:
        _log(f"[LLM/CEO] {OLLAMA_CEO_MODEL} error: {e}")
        return ""


async def call_executor(prompt: str, system: str = "", max_tokens: int = 300) -> str:
    """Executeur — Qwen 3.5 9B. Actions rapides, posts, surf. Pas de thinking."""
    full = f"{system}\n\n{prompt}" if system else prompt
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model": OLLAMA_EXECUTOR_MODEL,
                    "prompt": full,
                    "stream": False,
                    "think": False,
                    "keep_alive": -1,  # Garder en VRAM indefiniment
                    "options": {"num_predict": max_tokens, "temperature": 0.7},
                },
            )
            resp.raise_for_status()
            return resp.json().get("response", "")
    except Exception as e:
        _log(f"[LLM/EXEC] {OLLAMA_EXECUTOR_MODEL} error: {e}")
        return ""


async def call_vision(prompt: str, system: str = "", max_tokens: int = 300) -> str:
    """Vision — Qwen 2.5-VL 7B. Lecture de pages, screenshots, OCR."""
    full = f"{system}\n\n{prompt}" if system else prompt
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model": OLLAMA_VISION_MODEL,
                    "prompt": full,
                    "stream": False,
                    "think": False,
                    "keep_alive": -1,  # Garder en VRAM/RAM indefiniment
                    "options": {"num_predict": max_tokens, "temperature": 0.5},
                },
            )
            resp.raise_for_status()
            return resp.json().get("response", "")
    except Exception as e:
        _log(f"[LLM/VISION] {OLLAMA_VISION_MODEL} error: {e}")
        return ""


# Backward compat aliases
async def call_ollama(prompt: str, system: str = "", max_tokens: int = 500, model: str = None) -> str:
    """Compat — route vers CEO (defaut) ou modele specifique."""
    if model and "3.5" in model:
        return await call_executor(prompt, system, max_tokens)
    if model and "vl" in model.lower():
        return await call_vision(prompt, system, max_tokens)
    return await call_ceo(prompt, system, max_tokens, think=False)


async def call_ollama_fast(prompt: str, system: str = "", max_tokens: int = 500) -> str:
    """Rapide — utilise l'executeur (Qwen 3.5 9B, pas de thinking)."""
    return await call_executor(prompt, system, max_tokens)


async def call_mistral(prompt: str, system: str = "", max_tokens: int = 500) -> str:
    """Appel Mistral API — dernier recours cloud."""
    if not MISTRAL_API_KEY:
        return ""
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.mistral.ai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {MISTRAL_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={"model": MISTRAL_MODEL, "messages": msgs, "max_tokens": max_tokens},
            )
            resp.raise_for_status()
            choices = resp.json().get("choices", [])
            return choices[0]["message"]["content"].strip() if choices else ""
    except Exception as e:
        return ""


async def call_local_llm(prompt: str, system: str = "", max_tokens: int = 500) -> str:
    """Router principal : Groq (cloud) > CEO local (14B) > Executeur (9B) > Mistral.
    Pour contenu public, utiliser call_groq_local ou call_ceo directement."""
    # 1. Groq — meilleur modele cloud (Llama 3.3 70B)
    result = await call_groq_local(prompt, system, max_tokens)
    if result:
        return result
    # 2. CEO local — Qwen 3 14B (gratuit, illimite)
    result = await call_ceo(prompt, system, max_tokens, think=False)
    if result:
        return result
    # 3. Executeur — Qwen 3.5 9B (plus rapide)
    result = await call_executor(prompt, system, max_tokens)
    if result:
        return result
    # 4. Mistral — dernier recours cloud
    return await call_mistral(prompt, system, max_tokens)


# ══════════════════════════════════════════
# Filtre de personnalite — applique a TOUT contenu sortant
# ══════════════════════════════════════════

def personality_filter(text: str) -> str | None:
    """Filtre le contenu avant publication. Retourne None si le contenu est interdit."""
    if not text or len(text.strip()) < 3:
        return None

    text_lower = text.lower()

    # 1. Mots interdits (negativite, hype, denigrement)
    for word in PERSONALITY["forbidden_words"]:
        if word.lower() in text_lower:
            _log(f"[FILTER] Bloque — mot interdit: '{word}' dans: {text[:60]}")
            return None

    # 2. Informations confidentielles (chiffres business)
    confidential_patterns = [
        "0 client", "zero client", "no client", "no user", "no revenue",
        "0 revenue", "zero revenue", "$0 revenue", "$0 profit", "0 transaction",
        "no customer", "zero customer", "0 active",
    ]
    for pattern in confidential_patterns:
        if pattern.lower() in text_lower:
            _log(f"[FILTER] Bloque — info confidentielle: '{pattern}' dans: {text[:60]}")
            return None

    # 3. Ne jamais mentionner des chiffres d'affaires
    import re
    # Pattern: "$X revenue" ou "X users" ou "X clients" ou "X customers"
    if re.search(r'\d+\s*(users?|clients?|customers?|revenue|profit|transactions?)', text_lower):
        # Sauf si c'est des specs techniques (107 tokens, 14 chains, etc.)
        tech_ok = ["107 tokens", "14 chain", "46 mcp", "25 stock", "71 token", "2682 pair"]
        if not any(t in text_lower for t in tech_ok):
            _log(f"[FILTER] Bloque — chiffre business detecte: {text[:60]}")
            return None

    return text.strip()


def personality_check_attack_response(text: str) -> bool:
    """Verifie qu'une reponse a une attaque est factuelle et respectueuse.
    Une seule reponse max, jamais d'escalade."""
    if not text:
        return False
    text_lower = text.lower()
    # Pas de mots agressifs
    aggressive = ["liar", "shut up", "you're wrong", "idiot", "moron", "clown"]
    return not any(w in text_lower for w in aggressive)


# ══════════════════════════════════════════
# Detection de langue — heuristiques rapides
# ══════════════════════════════════════════

# Mots courants par langue pour detection heuristique
_LANG_KEYWORDS: dict[str, list[str]] = {
    "fr": ["le", "la", "les", "de", "des", "un", "une", "est", "sont", "dans", "pour", "avec", "pas", "que", "qui", "nous", "vous", "sur", "ce", "cette", "mais", "ou", "et", "je", "il", "elle", "avoir", "faire", "aussi", "plus", "mon", "ton", "bien", "tout", "peut"],
    "es": ["el", "la", "los", "las", "de", "en", "un", "una", "que", "es", "por", "con", "para", "del", "son", "como", "pero", "muy", "todo", "esta", "ser", "hola", "tiene", "puede", "mas", "tambien", "sobre", "cuando", "donde", "porque"],
    "pt": ["o", "os", "as", "de", "em", "um", "uma", "que", "para", "com", "por", "mas", "como", "seu", "sua", "mais", "muito", "tambem", "pode", "quando", "sobre", "tem", "ser", "esta", "isso", "aqui", "ainda", "voce", "nos", "eles"],
    "de": ["der", "die", "das", "und", "ist", "ein", "eine", "nicht", "von", "mit", "auch", "auf", "fur", "aber", "wie", "sich", "ich", "sie", "wir", "noch", "nach", "wenn", "kann", "dann", "sind", "hier", "oder", "wird", "haben", "uber"],
    "tr": ["bir", "ve", "bu", "ile", "icin", "var", "olan", "gibi", "daha", "nasil", "ama", "cok", "yapay", "zeka", "bunu", "ben", "sen", "onun", "biz", "neden", "evet", "hayir", "iyi", "kotu", "bugun"],
    "ru": ["\u044d\u0442\u043e", "\u043d\u0435", "\u043d\u0430", "\u0447\u0442\u043e", "\u043a\u0430\u043a", "\u0438\u043b\u0438", "\u0434\u043b\u044f", "\u0441", "\u043f\u043e", "\u0431\u044b\u043b\u043e", "\u043c\u043e\u0436\u043d\u043e", "\u043c\u044b", "\u0432\u044b", "\u0438", "\u043e\u043d\u0438", "\u0442\u0430\u043a", "\u0435\u0441\u043b\u0438", "\u0435\u0441\u0442\u044c", "\u0431\u044b\u043b", "\u043f\u0440\u043e", "\u0442\u043e\u0436\u0435", "\u0443\u0436\u0435", "\u043e\u0447\u0435\u043d\u044c"],
}

# Plages Unicode pour scripts non-latins
_LANG_SCRIPTS: dict[str, tuple[int, int]] = {
    "ja": (0x3040, 0x30FF),   # Hiragana + Katakana
    "zh": (0x4E00, 0x9FFF),   # CJK unifie
    "ko": (0xAC00, 0xD7AF),   # Hangul
    "ar": (0x0600, 0x06FF),   # Arabe
}

# Noms de langues pour les prompts LLM
LANG_NAMES: dict[str, str] = {
    "en": "English", "fr": "French", "es": "Spanish", "pt": "Portuguese",
    "de": "German", "ja": "Japanese", "zh": "Chinese", "ko": "Korean",
    "ar": "Arabic", "tr": "Turkish", "ru": "Russian",
}


def detect_language(text: str) -> str:
    """Detecte la langue d'un texte par heuristiques (mots courants + scripts Unicode).
    Retourne le code ISO (en, fr, es, pt, de, ja, zh, ko, ar, tr, ru).
    Default = 'en' si incertain."""
    if not text or len(text.strip()) < 3:
        return "en"

    # 1. Detection par script Unicode (japonais, chinois, coreen, arabe)
    char_counts: dict[str, int] = {}
    for ch in text:
        cp = ord(ch)
        for lang, (lo, hi) in _LANG_SCRIPTS.items():
            if lo <= cp <= hi:
                char_counts[lang] = char_counts.get(lang, 0) + 1
    # Si plus de 3 caracteres d'un script specifique, c'est cette langue
    if char_counts:
        best_script = max(char_counts, key=char_counts.get)
        if char_counts[best_script] >= 3:
            return best_script

    # 2. Caracteres cyrilliques = russe
    cyrillic_count = sum(1 for ch in text if 0x0400 <= ord(ch) <= 0x04FF)
    if cyrillic_count >= 3:
        return "ru"

    # 3. Detection par mots courants (langues latines)
    words = set(text.lower().split())
    scores: dict[str, int] = {}
    for lang, keywords in _LANG_KEYWORDS.items():
        scores[lang] = sum(1 for w in keywords if w in words)

    if scores:
        best_lang = max(scores, key=scores.get)
        # Seuil minimum : au moins 2 mots reconnus
        if scores[best_lang] >= 2:
            return best_lang

    # 4. Default anglais
    return "en"


def parse_json(text: str) -> dict:
    """Parse JSON tolerant."""
    if not text:
        return {}
    try:
        c = text.strip()
        for p in ["```json", "```"]:
            if c.startswith(p):
                c = c[len(p):]
        if c.endswith("```"):
            c = c[:-3]
        return json.loads(c.strip())
    except json.JSONDecodeError:
        try:
            return json.loads(text[text.index("{"):text.rindex("}") + 1])
        except (ValueError, json.JSONDecodeError):
            return {}


# ══════════════════════════════════════════
# VPS API Client
# ══════════════════════════════════════════

class VPSClient:
    """Scout VPS — collecte metriques, scan agents on-chain, premier contact A2A.
    Le VPS NE DECIDE PAS. Il collecte et remonte au CEO local."""

    def __init__(self):
        self._base = VPS_URL.rstrip("/")
        self._headers = {"X-CEO-Key": CEO_API_KEY, "Content-Type": "application/json"}
        self._last_scout_report = {}

    async def get_state(self) -> dict:
        """GET /api/ceo/state — Etat complet du VPS (metriques, erreurs, agents)."""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(f"{self._base}/api/ceo/state", headers=self._headers)
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            _log(f"[SCOUT] get_state error: {e}")
            return {}

    async def get_scout_report(self) -> dict:
        """GET /api/ceo/state — Recupere le rapport scout (metriques + agents detectes).
        Le scout collecte, le CEO local decide."""
        state = await self.get_state()
        if not state:
            return self._last_scout_report  # Retourner le dernier rapport si VPS inaccessible
        report = {
            "kpi": state.get("kpi", {}),
            "agents": state.get("agents", {}),
            "errors": state.get("errors", []),
            "onchain_agents": state.get("onchain_agents", []),  # Agents detectes par le scout
            "contacts_pending": state.get("contacts_pending", []),  # Contacts en attente de reponse
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        self._last_scout_report = report
        return report

    async def execute(self, action: str, agent: str, params: dict,
                      priority: str = "vert") -> dict:
        """POST /api/ceo/execute — Executer une action sur le VPS (scout uniquement)."""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{self._base}/api/ceo/execute",
                    headers=self._headers,
                    json={
                        "action": action,
                        "agent": agent,
                        "params": params,
                        "priority": priority,
                    },
                )
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            _log(f"[SCOUT] execute error: {e}")
            return {"success": False, "error": str(e)}

    async def health(self) -> dict:
        """GET /api/ceo/health — Sante du VPS."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{self._base}/api/ceo/health", headers=self._headers)
                return resp.json()
        except Exception as e:
            return {"healthy": False, "error": str(e)}

    async def emergency_stop(self) -> dict:
        """POST /api/ceo/emergency-stop."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(f"{self._base}/api/ceo/emergency-stop", headers=self._headers)
                return resp.json()
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def sync(self, local_actions: list, active: bool = True) -> dict:
        """POST /api/ceo/sync — Synchronise les actions avec le VPS."""
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"{self._base}/api/ceo/sync",
                    headers=self._headers,
                    json={"actions": local_actions, "active": active},
                )
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            return {"error": str(e)}

    async def think(self, prompt: str, tier: str = "fast", max_tokens: int = 1000) -> str:
        """POST /api/ceo/think — Delegue au VPS (Claude) pour les taches couteuses."""
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                resp = await client.post(
                    f"{self._base}/api/ceo/think",
                    headers=self._headers,
                    json={"prompt": prompt, "tier": tier, "max_tokens": max_tokens},
                )
                resp.raise_for_status()
                data = resp.json()
                cached = data.get("cached", False)
                cost = data.get("cost_usd", 0)
                if cached:
                    _log("  [VPS/think] Cache hit (0$)")
                else:
                    _log(f"  [VPS/think] {tier} ~${cost}")
                return data.get("result", "")
        except Exception as e:
            _log(f"[VPS] think error: {e}")
            return ""

    async def scout_contact_agent(self, agent_address: str, chain: str, pitch: str) -> dict:
        """Demande au scout VPS de contacter un agent on-chain via A2A."""
        return await self.execute("scout_contact", "SCOUT", {
            "agent_address": agent_address,
            "chain": chain,
            "pitch": pitch,
        }, "vert")

    async def scout_scan_agents(self, chain: str = "all") -> dict:
        """Demande au scout de scanner les agents sur une chain."""
        return await self.execute("scout_scan", "SCOUT", {"chain": chain}, "vert")


# ══════════════════════════════════════════
# Boucle OODA principale
# ══════════════════════════════════════════

CEO_SYSTEM = f"""Tu es le CEO de MAXIA, marketplace IA-to-IA sur 14 blockchains.
Tu tournes sur un PC local (AMD 5800X + RX 7900XT 20GB VRAM) avec 3 modeles IA :
- TOI (Qwen 3 14B) = cerveau — tu raisonnes, decides, rediges, planifies
- EXECUTEUR (Qwen 3.5 9B) = bras — il surfe, poste, execute tes ordres
- VISION (Qwen 2.5-VL 7B) = yeux — il lit les pages web et screenshots

Le VPS est ton SCOUT : il collecte les metriques, scan les agents on-chain, et fait le premier contact A2A.
Toi tu DECIDES. Le scout EXECUTE la collecte.

{MAXIA_IDENTITY}

CHIFFRES CLES — TU CONNAIS TOUT CA PAR COEUR :
- 107 tokens sur 7 chains (71 Solana Jupiter + 36 EVM 0x)
- 2682 paires de trading
- 25 actions tokenisees (prix Pyth real-time)
- 8 tiers GPU ($0.35 local 7900XT a $4.74 H200, 0% markup)
- 14 chains supportees (paiement USDC + escrow)
- 6 chains EVM swap (Ethereum, Base, Polygon, Arbitrum, Avalanche, BNB)
- 46 MCP tools, A2A protocol, leaderboard, AI disputes
- Leaderboard grades AAA-CCC (Beta-Bayesian scoring)
- 3 SLA tiers (basic 95%/standard 99%/premium 99.9%)
- Reverse auctions (scoring multi-attribut anti race-to-bottom)
- Proof of Delivery + dispute AI auto (liveness 2h + Groq evaluator)
- Activity feed SSE temps reel
- Referral 10% lifetime + 7 badges
- Business marketplace (vente d entreprises IA entieres, 5% commission)
- Circuit breaker 14 chains + /status
- OFAC V2 (Chainalysis Oracle + 55 adresses locales)
- Creator Marketplace (humains + agents publient et vendent outils, datasets, prompts, workflows — 90% createur / 10% MAXIA)
Phase : Pre-seed | Vision : Devenir LE hub ou les agents IA font du commerce.
Fondateur : Alexis (autorite finale sur decisions rouges)
URL : maxiaworld.app
Email : ceo@maxiaworld.app

PERSONNALITE (IMMUABLE) :
- Ton : professionnel, calme, confiant — comme un CEO qui connait son produit
- Toujours respectueux, meme face a l hostilite
- JAMAIS denigrer un concurrent — "notre approche est differente" pas "ils sont nuls"
- Si attaque : repondre UNE SEULE FOIS avec des faits, puis ignorer
- Positif mais mesure — pas de hype excessif (pas de "revolutionary", "game-changing")
- JAMAIS partager : nombre de clients, revenu, volume, stats business
- Si on demande les chiffres : "We don't share business metrics. Here's what MAXIA does: [features]"

VPS SCOUT (il collecte, toi tu decides) :
- Envoie les metriques toutes les 5 min
- Scan les agents on-chain sur 14 chains
- Premier contact A2A avec les agents detectes
- Collecte les erreurs et alertes
- NE PREND AUCUNE DECISION STRATEGIQUE

5 PLATEFORMES (tu adaptes ton approche a chacune) :
- Twitter : commentaires techniques, threads, engagement devs AI/crypto
- Reddit : reponses utiles, 80% valeur 20% MAXIA, ton de dev pas de marketeur
- GitHub : issues constructives, PRs, plugins, engagement communaute
- Discord : presence active, aide aux devs, pas de spam
- Telegram : groupes crypto/AI, DMs prospects, rapports a Alexis
- Email : ceo@maxiaworld.app, contact devs/partenaires

ACTIONS DISPONIBLES :
Twitter (local Playwright, 0 cout) :
- post_tweet: poster sur X (params: text) [VERT]
- reply_tweet: repondre a un tweet (params: tweet_url, text) [VERT]
- like_tweet: liker un tweet (params: tweet_url) [VERT]
- follow_user: follow un profil (params: username) [VERT]
- search_twitter: chercher tweets/hashtags (params: query) [VERT]
- search_profiles: chercher des profils dev AI/Solana (params: query) [VERT]
- get_mentions: lire les mentions et y repondre [VERT]

Reddit (local Playwright, 0 cout) :
- post_reddit: poster sur un subreddit (params: subreddit, title, body) [VERT]
- comment_reddit: commenter un post (params: post_url, text) [VERT]
- search_reddit: chercher des posts (params: subreddit, query) [VERT]

Email (ceo@maxiaworld.app, IMAP/SMTP OVH) :
- check_emails: lire les emails non lus et repondre automatiquement [VERT]
- send_email: envoyer un email (params: to, subject, body) [ORANGE]

VPS (via API securisee) :
- update_price: modifier un prix (params: service_id, new_price, reason) [ORANGE]
- contact_prospect: contacter un wallet (params: wallet, message, canal) [ORANGE]
- send_alert: alerte Discord (params: message) [VERT]
- toggle_agent: activer/desactiver un agent (params: agent_name, enabled) [ORANGE]
- browse_competitor: screenshot concurrent (params: url) [VERT]
- generate_report: rapport (params: topic) [VERT]

STRATEGIE TWITTER (OBLIGATOIRE) :
- Max 2 tweets/jour de HAUTE QUALITE (technique, insight, valeur reelle)
- PRIORITE ABSOLUE = ENGAGEMENT : liker et commenter les tweets d autres devs/influenceurs
- Commentaires de QUALITE : apporter un insight technique, poser une question intelligente, partager une experience
- JAMAIS de commentaire spam ou promo directe dans les reponses aux autres
- Construire une REPUTATION d expert AI/crypto avant de promouvoir
- Ratio ideal : 2 tweets max, 15+ commentaires de qualite, 30+ likes par jour
- Mieux vaut 0 tweet et 20 bons commentaires que 5 tweets dans le vide
- Follow les profils pertinents (max 10/jour)
- Repond a TOUTES les mentions avec des reponses utiles (pas commerciales)
- Ton : dev qui aide un autre dev, PAS marketeur

STRATEGIE MARKETING REDDIT :
- Subreddits cibles : solanadev, artificial, LangChain, LocalLLaMA, SaaS, cryptocurrency
- Commente des posts existants avec une reponse UTILE, puis mentionne MAXIA naturellement
- Ne spamme PAS — max 3 commentaires/jour par subreddit
- Poste du contenu educatif, pas promotionnel

OBJECTIFS DU FONDATEUR (NON NEGOCIABLES) :
1. MAXIA doit devenir MONDIALEMENT RECONNUE
2. Etre la MOINS CHERE du marche PARTOUT (swap, GPU, IA, stocks)
3. Objectif revenu : 10 000 euros/mois (le plus vite possible)
4. Le fondateur GARDE LE CONTROLE TOTAL
5. Volume > Marge : 10000 clients a 0.01 > 10 clients a 10

STRATEGIE PRIX :
- Toujours verifier concurrents (Jupiter, Binance, AWS, RunPod)
- Concurrent moins cher → baisser IMMEDIATEMENT
- GPU : 0% marge | Swap : jusqu a 0.01% | Revenu = VOLUME

CLIENT CIBLE (profil Thomas — le Dev Agent) :
- Dev Python 26-34 ans, connait Solana/Ethereum
- A un agent IA qui FONCTIONNE mais 0 clients, 0 revenus
- Veut : POST /sell → service live, USDC arrive
- Veut PAS : site web, marketing, token, waitlist
- Ou il est : Twitter (threads AI/crypto), Discord (Solana dev, ElizaOS), GitHub, Reddit (r/solanadev)
- Phrase cle : "Your AI agent can earn USDC while you sleep. One API call to list it."

REGLES MARKETING :
- Messages centres sur GAGNER de l argent, pas acheter des services
- Ton technique, pas commercial — parler comme un dev
- Ne JAMAIS envoyer le meme message 2x au meme wallet
- Canaux prioritaires : Twitter, Reddit, Discord, Telegram groups, GitHub
- JAMAIS mentionner le nombre de clients, le revenue, les stats, ou les chiffres business dans du contenu public
- Si on te demande les stats, repondre "growing fast" ou "early stage" sans chiffres

REGLES DE DECISION :
- Pragmatique, patient (7j avant juger), frugal
- VERT : auto-execute immediatement
- ORANGE : notification fondateur, attente 30 min, max 1/jour par cible
- ROUGE : notification fondateur, attente 2h, NE JAMAIS auto-executer
- Si >5 decisions orange sans revenu → emergency stop
- Max 3 actions par cycle. Pas d actions vagues.

FORMAT REPONSE (JSON strict) :
{{"analysis": "2 phrases max", "decisions": [{{"action": "...", "agent": "...", "params": {{}}, "priority": "vert|orange|rouge"}}], "next_focus": "1 phrase"}}"""

# Version courte pour Ollama (routine) — ~200 tokens au lieu de ~800
CEO_SYSTEM_SHORT = f"""CEO MAXIA — {MAXIA_FEATURES_SHORT}
Goal: 10k EUR/month. Target: AI devs with no revenue. ALL CONTENT IN ENGLISH.
Key differentiators: 107 tokens 7 chains, AI dispute resolution, reverse auctions, GPU at cost, leaderboard grades, business marketplace.

ACTIONS (all vert unless noted):
- post_template_tweet: tweet from templates [VERT]
- post_tweet: custom tweet (params: text) [VERT]
- post_thread: Twitter thread (params: tweets=[str,str,...]) [VERT]
- reply_mentions: auto-reply to mentions [VERT]
- like_tweet: like (params: tweet_url) [VERT]
- follow_user: follow (params: username) [VERT]
- search_twitter: search tweets (params: query) [VERT]
- search_profiles: find profiles (params: query) [VERT]
- score_profile: score prospect (params: username) [VERT]
- detect_opportunities: find frustrated devs [VERT]
- scrape_followers: competitor followers (params: competitor) [VERT]
- post_reddit: post (params: subreddit, title, body) [VERT]
- comment_reddit: comment (params: post_url, text) [VERT]
- dm_twitter: send first DM (params: username, text) [ORANGE]
- manage_dms: read & reply all unread DMs on Twitter/Telegram [VERT]
- send_telegram: telegram (params: target, text) [ORANGE]
- update_price: VPS price (params: service_id, new_price) [ORANGE]
- search_groups: find & join Telegram/Discord groups (params: platform) [VERT]
- ab_test: A/B test 2 variants (params: text_a, text_b) [VERT]
- comment_github_ai: comment on AI project issues (ElizaOS, LangChain) [VERT]
- write_blog: write article (params: topic) [VERT]
- watch_prices: check competitor prices + auto-lower [VERT]
- analyze_trends: trending tokens + topics [VERT]
- handle_support: answer support message (params: message, user) [VERT]
- generate_quote: auto quote (params: services, quantity) [VERT]
- negotiate: negotiate price (params: service, price, volume) [VERT]

DO NOT repeat what is in ALREADY DONE. Use LEARNED RULES. Max 3 actions.
JSON: {{"decisions":[{{"action":"...","agent":"...","params":{{}},"priority":"vert"}}]}}"""


# ══════════════════════════════════════════
# Fichiers de strategie — ecrits et relus par le CEO
# ══════════════════════════════════════════

def _load_strategy() -> dict:
    """Charge la strategie courante depuis strategy.md."""
    try:
        if os.path.exists(STRATEGY_FILE):
            with open(STRATEGY_FILE, "r", encoding="utf-8") as f:
                content = f.read()
            return {"content": content, "exists": True}
    except Exception:
        pass
    return {"content": "", "exists": False}


def _save_strategy(content: str):
    """Sauvegarde la strategie dans strategy.md."""
    try:
        with open(STRATEGY_FILE, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        _log(f"[STRATEGY] Save error: {e}")


def _load_learnings() -> list:
    """Charge les apprentissages depuis learnings.json."""
    try:
        if os.path.exists(LEARNINGS_FILE):
            with open(LEARNINGS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return []


def _save_learnings(learnings: list):
    """Sauvegarde les apprentissages dans learnings.json."""
    # Garder max 200 entries
    if len(learnings) > 200:
        learnings = learnings[-200:]
    try:
        with open(LEARNINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(learnings, f, indent=2, default=str, ensure_ascii=False)
    except Exception as e:
        _log(f"[LEARN] Save error: {e}")


def _append_rnd_finding(finding: str, category: str = "general"):
    """Ajoute une trouvaille R&D dans rnd_findings.md."""
    try:
        today = time.strftime("%Y-%m-%d")
        entry = f"\n### [{today}] {category}\n{finding}\n"
        # Verifier si le fichier existe, sinon creer avec header
        if not os.path.exists(RND_FINDINGS_FILE):
            with open(RND_FINDINGS_FILE, "w", encoding="utf-8") as f:
                f.write(f"# MAXIA R&D Findings\n\nGenere automatiquement par le CEO local.\n")
        with open(RND_FINDINGS_FILE, "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception as e:
        _log(f"[R&D] Save error: {e}")


def _load_platform_scores() -> dict:
    """Charge les scores par plateforme."""
    try:
        if os.path.exists(PLATFORM_SCORES_FILE):
            with open(PLATFORM_SCORES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {
        "twitter": {"score": 5, "trend": "=", "actions": 0, "successes": 0},
        "reddit": {"score": 5, "trend": "=", "actions": 0, "successes": 0},
        "github": {"score": 5, "trend": "=", "actions": 0, "successes": 0},
        "discord": {"score": 5, "trend": "=", "actions": 0, "successes": 0},
        "telegram": {"score": 5, "trend": "=", "actions": 0, "successes": 0},
        "email": {"score": 5, "trend": "=", "actions": 0, "successes": 0},
    }


def _save_platform_scores(scores: dict):
    """Sauvegarde les scores par plateforme."""
    try:
        with open(PLATFORM_SCORES_FILE, "w", encoding="utf-8") as f:
            json.dump(scores, f, indent=2)
    except Exception as e:
        _log(f"[SCORES] Save error: {e}")


class CEOLocal:
    """CEO Local — Cerveau autonome MAXIA.

    Architecture 3 modeles :
      CEO (Qwen 3 14B)     = raisonne, decide, redige (think=on)
      Executeur (Qwen 3.5 9B) = surfe, poste, execute (rapide)
      Vision (Qwen 2.5-VL 7B) = lit les pages, screenshots

    VPS = Scout (collecte metriques, scan agents on-chain, premier contact A2A)
    CEO Local = Cerveau (strategie, multi-plateforme, R&D, apprentissage continu)
    """

    def __init__(self):
        self.vps = VPSClient()
        self.memory = _load_memory()
        self._running = False
        self._cycle = self.memory.get("cycle_count", 0)
        self._daily_actions = {"date": "", "count": 0}
        self.strategy = _load_strategy()
        self.learnings = _load_learnings()
        self.platform_scores = _load_platform_scores()
        _log("[CEO Local] Initialise — Architecture 3 modeles")
        _log(f"  CEO (cerveau)   : {OLLAMA_CEO_MODEL}")
        _log(f"  Executeur (bras) : {OLLAMA_EXECUTOR_MODEL}")
        _log(f"  Vision (yeux)    : {OLLAMA_VISION_MODEL}")
        _log(f"  VPS Scout: {VPS_URL}")
        _log(f"  Intervalle: {OODA_INTERVAL_S}s")
        _log(f"  Memoire: {len(self.memory.get('decisions', []))} decisions, {len(self.memory.get('regles', []))} regles")
        _log(f"  Strategie: {'chargee' if self.strategy.get('exists') else 'nouvelle'}")
        _log(f"  Learnings: {len(self.learnings)} entries")

    async def run(self):
        """Boucle OODA principale."""
        self._running = True
        _log("[CEO Local] Demarre la boucle OODA")

        # ═══ PRELOAD 3 MODELES — garder en VRAM/RAM simultanement ═══
        # Sans ca, Ollama decharge un modele a chaque swap (~5-10s de latence)
        # Avec OLLAMA_MAX_LOADED_MODELS=3 + preload, les 3 restent charges = 0 latence
        from config_local import OLLAMA_MAX_LOADED_MODELS
        os.environ["OLLAMA_MAX_LOADED_MODELS"] = str(OLLAMA_MAX_LOADED_MODELS)
        _log(f"[GPU] Preload 3 modeles (OLLAMA_MAX_LOADED_MODELS={OLLAMA_MAX_LOADED_MODELS})...")
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                for model_name, role in [
                    (OLLAMA_CEO_MODEL, "CEO"),
                    (OLLAMA_EXECUTOR_MODEL, "Executeur"),
                    (OLLAMA_VISION_MODEL, "Vision"),
                ]:
                    _log(f"  [GPU] Chargement {role} ({model_name})...")
                    # keep_alive=-1 = garder le modele charge indefiniment
                    resp = await client.post(
                        f"{OLLAMA_URL}/api/generate",
                        json={"model": model_name, "prompt": "hello", "stream": False,
                              "keep_alive": -1, "options": {"num_predict": 1}},
                    )
                    if resp.status_code == 200:
                        _log(f"  [GPU] {role} ({model_name}) charge OK")
                    else:
                        _log(f"  [GPU] {role} ({model_name}) erreur: {resp.status_code}")
            _log("[GPU] 3 modeles charges — 0 latence de swap")
        except Exception as e:
            _log(f"[GPU] Preload partiel: {e} — Ollama gerera le swap automatiquement")

        # Lancer Chrome une seule fois au demarrage (reste ouvert)
        try:
            await browser.setup()
            _log("[CEO Local] Chrome lance et pret")
        except Exception as e:
            _log(f"[CEO Local] Chrome failed: {e} — actions browser indisponibles")

        await notify_all("CEO Local demarre", "Boucle OODA active — 3 modeles charges", "vert")

        while self._running:
            self._cycle += 1
            start = time.time()
            is_action_cycle = (self._cycle % 3 == 0)  # 1 action cycle pour 2 observation cycles
            cycle_type = "ACT" if is_action_cycle else "OBSERVE"
            _log(f"\n=== Cycle #{self._cycle} [{cycle_type}] ===")

            try:
                # ═══ PHASE 1: OBSERVATION (every cycle — 60s) ═══
                # Lire mentions, trending, DMs — sans agir

                # 1a. OBSERVE — etat VPS
                state = await self._observe()
                if not state:
                    _log("[CEO Local] VPS inaccessible, retry dans 60s")
                    await asyncio.sleep(60)
                    continue

                # 1b. LIRE les mentions (toujours, meme en observation)
                self._mentions_done_this_cycle = False
                try:
                    mentions = await browser.get_mentions(10)
                    pending = [m for m in (mentions or []) if m.get("url") and not browser._is_duplicate("reply", m.get("url", ""))]
                    if pending:
                        # Toujours repondre aux mentions (meme en cycle observation)
                        _log(f"[PRIORITY] {len(pending)} mentions — reponse immediate")
                        reply_result = await self._reply_to_mentions()
                        _log(f"[MENTIONS] {reply_result.get('detail', '')}")
                        self._mentions_done_this_cycle = True
                    # Stocker le nombre de mentions pour la memoire
                    self.memory.setdefault("observation", {})["last_mentions"] = len(mentions or [])
                    self.memory["observation"]["last_pending"] = len(pending) if pending else 0
                except Exception as e:
                    _log(f"[MENTIONS] Erreur: {e}")

                # 1c. LIRE les DMs (sans repondre en observation)
                if not is_action_cycle:
                    try:
                        dms = await browser.read_twitter_dms(5)
                        unread = len([d for d in (dms or []) if d.get("unread")])
                        self.memory.setdefault("observation", {})["unread_dms"] = unread
                        if unread:
                            _log(f"[OBSERVE] {unread} DMs non lus detectes")
                    except Exception:
                        pass

                # 1d. SCANNER trending topics (observation only)
                if not is_action_cycle and self._cycle % 6 == 0:
                    try:
                        trending = await browser.search_twitter("AI agent OR crypto swap OR DeFi yield", 5)
                        if trending:
                            topics = [t.get("text", "")[:80] for t in trending[:3]]
                            self.memory.setdefault("observation", {})["trending"] = topics
                            self.memory["observation"]["trending_ts"] = time.strftime("%H:%M")
                            _log(f"[OBSERVE] Trending: {len(trending)} posts scannés")
                    except Exception:
                        pass

                # ═══ PHASE 2: ANALYSE (every cycle) ═══
                analysis = await self._orient(state)

                # ═══ PHASE 3: ACTION (1 cycle sur 3 seulement) ═══
                if is_action_cycle:
                    # 3. DECIDE
                    decisions = await self._decide(analysis, state)

                    # 4. ACT
                    await self._act(decisions)

                    # 5. AUTO-ENGAGE (1 action cycle sur 2)
                    if (self._cycle // 3) % 2 == 0:
                        try:
                            await self._auto_engage()
                        except Exception as e:
                            _log(f"[ENGAGE] Error: {e}")
                else:
                    # ═══ SURF + RECHERCHE — browser-use explore le web en continu ═══
                    # Cycle pair = surf leger (extraire titres, tweets, trends)
                    # Cycle impair multiple de 3 = recherche profonde (concurrence, tools, opportunites)
                    # Sinon = surf leger
                    try:
                        if self._cycle % 3 == 0:
                            await self._deep_research()
                        else:
                            await self._autonomous_surf(analysis, state)
                    except Exception as e:
                        _log(f"[SURF] Erreur: {e}")

                # 6. ENGAGEMENT FEEDBACK (toutes les 6 cycles = ~1h)
                # Verifier si nos derniers tweets/commentaires ont eu de l'engagement
                if self._cycle % 6 == 0:
                    try:
                        await self._check_engagement_feedback()
                    except Exception as e:
                        _log(f"[FEEDBACK] Error: {e}")

                # 7. CRM FOLLOW-UP (toutes les 8 cycles = ~80 min)
                # Relancer les prospects detectes qui n'ont pas encore repondu
                if self._cycle % 8 == 0:
                    try:
                        await self._crm_followup()
                    except Exception as e:
                        _log(f"[CRM] Error: {e}")

                # 8. MANAGE DMs (toutes les 4 cycles = ~40 min)
                if self._cycle % 4 == 0:
                    try:
                        dm_result = await self._manage_conversations()
                        if dm_result.get("detail", "") != "0 conversations gerees":
                            _log(f"[DMs] {dm_result.get('detail', '')}")
                    except Exception as e:
                        _log(f"[DMs] Erreur: {e}")

                # 8a. CHECK OWN TWEET REPLIES (toutes les 4 cycles = ~40 min)
                if self._cycle % 4 == 0:
                    try:
                        await self._check_own_tweet_replies()
                    except Exception as e:
                        _log(f"[OWN REPLIES] Error: {e}")

                # 8. CLEAN screenshots (toutes les 50 cycles = ~8h)
                if self._cycle % 50 == 0:
                    self._clean_screenshots()

                # 8a. POST R&D findings to forum (every 50 cycles = ~4h)
                if self._cycle % 50 == 0:
                    try:
                        await self._post_to_forum()
                    except Exception as e:
                        _log(f"[FORUM] Error: {e}")

                # 8b. RETROSPECTIVE HEBDO (dimanche, 1x par semaine)
                import datetime as _dt
                if _dt.datetime.now(_dt.timezone.utc).weekday() == 6 and self._cycle % 100 == 0:
                    try:
                        await self._weekly_retrospective()
                    except Exception as e:
                        _log(f"[RETRO] Error: {e}")

                # 9. SELF-LEARNING (toutes les 5 cycles = ~25 min, GPU local = gratuit)
                if self._cycle % 5 == 0:
                    try:
                        # 9a. Regles basees sur les stats (quel action reussit/echoue)
                        rules = generate_learned_rules()
                        if rules:
                            for r in rules:
                                if r not in self.memory.get("regles", []):
                                    self.memory.setdefault("regles", []).append(r)
                                    _log(f"[LEARN] {r}")

                        # 9b. Analyse qualitative via LLM (toutes les 10 cycles = ~50 min)
                        if self._cycle % 10 == 0:
                            recent_actions = self.memory.get("actions_done", [])[-30:]
                            recent_tweets = self.memory.get("tweets_posted", [])[-10:]
                            convos = self.memory.get("conversations", [])[-15:]
                            summaries = self.memory.get("conversation_summaries", [])[-5:]
                            eng_stats = self.memory.get("engagement_stats", [])[-7:]
                            follows = self.memory.get("follows", [])
                            contacts = self.memory.get("contacts", [])
                            groups = self.memory.get("groups_joined", [])
                            discovered = self.memory.get("discovered_communities", {})

                            # Compter succes/echecs par type d'action
                            action_stats = {}
                            for a in recent_actions:
                                act = a.get("action", "unknown")
                                action_stats.setdefault(act, {"ok": 0, "fail": 0})
                                if a.get("success"):
                                    action_stats[act]["ok"] += 1
                                else:
                                    action_stats[act]["fail"] += 1

                            prompt = (
                                f"Analyse CEO MAXIA — cycle #{self._cycle}:\n"
                                f"Action success rates: {json.dumps(action_stats, default=str)[:400]}\n"
                                f"Engagement (7 days): {json.dumps(eng_stats, default=str)[:200]}\n"
                                f"CRM summaries: {json.dumps(summaries, default=str)[:300]}\n"
                                f"Stats: {len(follows)} follows, {len(contacts)} contacts, {len(groups)} groups joined\n"
                                f"Discovered: {len(discovered.get('discord',[]))} Discord, {len(discovered.get('telegram',[]))} Telegram, {len(discovered.get('github',[]))} GitHub\n"
                                f"Current rules: {json.dumps(self.memory.get('regles', [])[-5:], default=str)[:200]}\n\n"
                                f"Based on what's WORKING and what's FAILING, give 3 NEW concrete rules.\n"
                                f"Focus on: which platforms get engagement, which actions fail, what to do more/less.\n"
                                f"Format: 1 rule per line, max 60 chars. English only."
                            )
                            insight = await call_ollama(prompt, system="Concise growth advisor. Data-driven rules only. English.", max_tokens=150)
                            if insight and len(insight) > 20:
                                for line in insight.strip().split("\n")[:3]:
                                    line = line.strip().lstrip("0123456789.-) ")
                                    if line and len(line) > 10 and line not in self.memory.get("regles", []):
                                        self.memory.setdefault("regles", []).append(line)
                                        _log(f"[LEARN+] {line}")

                        # 9c. Track best performing content (toutes les 20 cycles = ~100 min)
                        if self._cycle % 20 == 0:
                            convos = self.memory.get("conversations", [])[-30:]
                            if convos:
                                # Identifier les types de commentaires qui generent des reponses
                                replied_to = [c for c in convos if c.get("type") in ("mention_reply", "own_tweet_reply")]
                                commented = [c for c in convos if c.get("type") == "comment"]
                                reddit = [c for c in convos if c.get("type") == "reddit_comment"]
                                best_platform = "twitter"
                                if len(reddit) > len(commented):
                                    best_platform = "reddit"
                                self.memory.setdefault("learning", {}).update({
                                    "last_analysis": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                    "replies_received": len(replied_to),
                                    "comments_made": len(commented),
                                    "reddit_comments": len(reddit),
                                    "best_platform": best_platform,
                                    "total_convos": len(convos),
                                })
                                _log(f"[LEARN] Platform analysis: {len(replied_to)} replies, {len(commented)} comments, {len(reddit)} reddit — best: {best_platform}")

                    except Exception as e:
                        _log(f"[LEARN] Error: {e}")

                # 9b. WEEKLY THREAD (Monday only, 1x per week)
                import datetime as _dt2
                if _dt2.datetime.now(_dt2.timezone.utc).weekday() == 0 and self._cycle % 50 == 0:
                    try:
                        await self._weekly_thread()
                    except Exception as e:
                        _log(f"[THREAD] Error: {e}")

                # 9c. Proactive DMs to engaged users (every 12 cycles = ~2h)
                if self._cycle % 12 == 0:
                    try:
                        await self._proactive_dm_engaged()
                    except Exception as e:
                        _log(f"[DM] Proactive error: {e}")

                # 9d. Engage competitor threads (every 20 cycles = ~3h)
                if self._cycle % 20 == 0:
                    try:
                        await self._engage_competitor_threads()
                    except Exception as e:
                        _log(f"[COMPETE] Error: {e}")

                # 10. SELF-UPDATE (toutes les 36 cycles = ~6h)
                if self._cycle % 36 == 0 and needs_check():
                    try:
                        updates = check_for_updates()
                        if updates.get("updates"):
                            _log(f"[UPDATE] New commits: {updates.get('commits', '')[:100]}")
                            result = apply_updates()
                            if result.get("success"):
                                _log("[UPDATE] Updated! Restarting...")
                                await notify_all("CEO Updated", "New code pulled. Restarting...", "orange")
                                self._running = False
                    except Exception as e:
                        _log(f"[UPDATE] Error: {e}")

                # 10a. RAPPORT QUOTIDIEN (1x/jour a 20h UTC)
                import datetime as _dt_report
                _hour_utc = _dt_report.datetime.now(_dt_report.timezone.utc).hour
                _today_str = time.strftime("%Y-%m-%d")
                _last_report = self.memory.get("last_report_date", "")
                if _hour_utc == 20 and _last_report != _today_str:
                    self.memory["last_report_date"] = _today_str
                    try:
                        await self._daily_report()
                    except Exception as e:
                        _log(f"[REPORT] Error: {e}")

                # 10b. EVOLUTION STRATEGIQUE (toutes les 100 cycles = ~8h)
                # Le CEO analyse TOUT (surf, actions, engagement, prospects) et ajuste sa strategie
                if self._cycle % 100 == 0 and self._cycle > 0:
                    try:
                        await self._evolve_strategy()
                    except Exception as e:
                        _log(f"[EVOLVE] Error: {e}")

                # 10c. PROPOSITIONS DE FEATURES (toutes les 200 cycles = ~16h)
                # Le CEO synthetise les improvement_ideas + research_findings en propositions concretes
                if self._cycle % 200 == 0 and self._cycle > 0:
                    try:
                        await self._propose_features()
                    except Exception as e:
                        _log(f"[FEATURES] Error: {e}")

                # 10d. VIDEO SCRIPTS (toutes les 100 cycles = ~8h)
                # Le CEO genere 3 scripts video courts (30s) pour TikTok/YouTube Shorts
                if self._cycle % 100 == 0 and self._cycle > 0:
                    try:
                        await self._generate_video_scripts()
                    except Exception as e:
                        _log(f"[VIDEO] Error: {e}")

                # 10b. CONVERSATION MEMORY — resume intelligent des interactions (toutes les 6 cycles = ~30min)
                if self._cycle % 6 == 0:
                    try:
                        convos = self.memory.get("conversations", [])
                        recent_convos = [c for c in convos[-15:] if not c.get("summarized")]
                        if len(recent_convos) >= 3:
                            convos_str = json.dumps(recent_convos, default=str)[:1000]
                            summary = await call_ollama(
                                f"Recent interactions:\n{convos_str}\n\n"
                                f"Summarize in 2-3 bullet points:\n"
                                f"1. Who are the hottest prospects and why?\n"
                                f"2. What topics got the best engagement?\n"
                                f"3. What should we do differently next?\n"
                                f"Be specific. Names, topics, actions.",
                                system="Brief CRM analyst. Bullet points only.",
                                max_tokens=150,
                            )
                            if summary and len(summary) > 20:
                                self.memory.setdefault("conversation_summaries", []).append({
                                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                    "summary": summary.strip()[:500],
                                    "count": len(recent_convos),
                                })
                                # Garder max 20 summaries
                                if len(self.memory["conversation_summaries"]) > 20:
                                    self.memory["conversation_summaries"] = self.memory["conversation_summaries"][-20:]
                                # Marquer les convos comme resumees
                                for c in recent_convos:
                                    c["summarized"] = True
                                _log(f"[MEMORY] Resume {len(recent_convos)} conversations")
                    except Exception as e:
                        _log(f"[MEMORY] Error: {e}")

                # 11. SYNC — envoyer les actions au VPS (eviter double-post)
                recent = self.memory.get("actions_done", [])[-10:]
                sync_result = await self.vps.sync(recent, active=True)
                vps_actions = sync_result.get("vps_actions", [])
                if vps_actions:
                    _log(f"[SYNC] VPS a fait {len(vps_actions)} actions recemment")

                # 6. LOG
                elapsed = time.time() - start
                _log(f"Cycle #{self._cycle} complete en {elapsed:.1f}s")
                self.memory["cycle_count"] = self._cycle
                _save_memory(self.memory)

            except Exception as e:
                _log(f"ERREUR cycle #{self._cycle}: {e}")
                await audit.log(f"cycle_error: {e}", success=False)

            # 7. SLEEP — observation cycles rapides, action cycles normaux
            ctrl = self._load_control()
            if ctrl.get("paused"):
                _log("[CEO Local] PAUSE (via dashboard). Attente resume...")
                while ctrl.get("paused"):
                    await asyncio.sleep(10)
                    ctrl = self._load_control()
                _log("[CEO Local] RESUME")
            else:
                if is_action_cycle:
                    interval = ctrl.get("interval_s", OODA_INTERVAL_S)
                    await asyncio.sleep(interval)  # 5 min after action
                else:
                    await asyncio.sleep(60)  # 60s between observation cycles (gratuit)

    @staticmethod
    def _load_control() -> dict:
        ctrl_file = os.path.join(os.path.dirname(__file__), "ceo_control.json")
        try:
            if os.path.exists(ctrl_file):
                with open(ctrl_file, "r") as f:
                    return json.load(f)
        except Exception:
            pass
        return {"paused": False, "interval_s": 600}

    def stop(self):
        self._running = False

    async def _observe(self) -> dict:
        """OBSERVE — Recupere le rapport du scout VPS (metriques, agents, erreurs)."""
        _log("[OBSERVE] Rapport scout VPS...")
        report = await self.vps.get_scout_report()
        if report:
            kpis = report.get("kpi", {})
            onchain = report.get("onchain_agents", [])
            contacts = report.get("contacts_pending", [])
            _log(f"  Metriques: Rev=${kpis.get('revenue_24h', 0)} Services={kpis.get('services_actifs', 0)}")
            if onchain:
                _log(f"  Scout: {len(onchain)} agents on-chain detectes")
            if contacts:
                _log(f"  Scout: {len(contacts)} contacts en attente de reponse")
        return report

    async def _observe_scout_agents(self, report: dict):
        """Traite les agents on-chain detectes par le scout VPS.
        Le CEO decide quoi faire : contacter, ignorer, surveiller."""
        onchain = report.get("onchain_agents", [])
        if not onchain:
            return
        for agent in onchain[:3]:  # Max 3 par cycle
            addr = agent.get("address", "")
            chain = agent.get("chain", "")
            behavior = agent.get("behavior", "")
            if not addr:
                continue
            # Verifier si deja contacte
            contacted = self.memory.get("scout_contacts", [])
            if any(c.get("address") == addr for c in contacted):
                continue
            # CEO decide du pitch personnalise
            pitch = await call_ceo(
                f"Agent detecte on-chain:\n"
                f"  Address: {addr}\n  Chain: {chain}\n  Behavior: {behavior}\n\n"
                f"MAXIA features: {MAXIA_FEATURES_SHORT}\n\n"
                f"Write a short, personalized A2A contact message for this agent.\n"
                f"Explain what MAXIA can do for them based on their behavior.\n"
                f"Max 200 chars. Professional, helpful, not spammy.\n"
                f"Message only:",
                system="CEO MAXIA. Professional. English only.",
                max_tokens=80,
                think=True,
            )
            pitch = personality_filter(pitch or "")
            if pitch:
                result = await self.vps.scout_contact_agent(addr, chain, pitch)
                self.memory.setdefault("scout_contacts", []).append({
                    "address": addr, "chain": chain, "behavior": behavior[:80],
                    "pitch": pitch[:200], "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "response": result.get("response", "pending"),
                })
                _log(f"[SCOUT] Contact {addr[:10]}... on {chain}: {pitch[:60]}")
        # Trim
        if len(self.memory.get("scout_contacts", [])) > 200:
            self.memory["scout_contacts"] = self.memory["scout_contacts"][-200:]

    async def _orient(self, state: dict) -> str:
        """ORIENT — Le CEO 14B analyse la situation (think=on pour raisonnement profond).
        Tous les 5 cycles : analyse strategique avec thinking.
        Sinon : analyse rapide avec l'executeur."""
        _log("[ORIENT] Analyse CEO...")
        kpis = state.get("kpi", {})
        agents = state.get("agents", {})
        # Filtrer les erreurs non pertinentes (MICRO n'existe plus en mode scout)
        errors = [e for e in state.get("errors", [])
                  if not any(skip in str(e).lower() for skip in ["micro", "unknown cible"])]

        # Cycle strategique (CEO 14B think=on) ou rapide (executeur 9B)
        use_ceo_thinking = (self._cycle % 5 == 0)

        # Injecter les regles apprises + CRM summaries dans l'analyse
        regles = self.memory.get("regles", [])[-10:]
        summaries = self.memory.get("conversation_summaries", [])[-3:]
        learning = self.memory.get("learning", {})
        regles_str = "\n".join(f"  - {r}" for r in regles) if regles else "  (none yet)"
        summaries_str = "\n".join(f"  - {s.get('summary', '')[:150]}" for s in summaries) if summaries else ""

        summary = (
            f"Etat VPS MAXIA:\n"
            f"- Revenu 24h: ${kpis.get('revenue_24h', 0)}\n"
            f"- Clients actifs: {kpis.get('clients_actifs', 0)}\n"
            f"- Services actifs: {kpis.get('services_actifs', 0)}\n"
            f"- Emergency stop: {kpis.get('emergency_stop', False)}\n"
            f"- Agents: {json.dumps(agents, default=str)[:500]}\n"
            f"- Erreurs recentes: {json.dumps(errors, default=str)[:300]}\n"
            f"\nLEARNED RULES (follow these):\n{regles_str}\n"
        )
        if summaries_str:
            summary += f"\nRECENT CRM INSIGHTS:\n{summaries_str}\n"
        if learning:
            summary += f"\nLEARNING: best_platform={learning.get('best_platform','?')}, replies={learning.get('replies_received',0)}, comments={learning.get('comments_made',0)}, best_content={learning.get('best_content_type','?')}, best_surf={learning.get('best_surf_source','?')}\n"

        # Injecter la strategie courante
        strategy = self.memory.get("current_strategy", {})
        if strategy:
            summary += f"\nCURRENT STRATEGY (follow this): focus={strategy.get('focus','?')}, best_platform={strategy.get('best_platform','?')}, top_topic={strategy.get('top_topic','?')}, stop_doing={strategy.get('stop_doing','?')}\n"

        # Injecter les meilleurs resultats de surf recent
        surf = self.memory.get("surf_findings", [])[-3:]
        if surf:
            summary += f"\nRECENT SURF: " + "; ".join(f"{s['target']}: {s['finding'][:60]}" for s in surf) + "\n"

        # Prospects chauds
        prospects = self.memory.get("prospects_from_surf", [])[-3:]
        if prospects:
            summary += f"\nHOT PROSPECTS: " + "; ".join(f"{p['source']}: {p['finding'][:50]}" for p in prospects) + "\n"

        # Injecter la strategie actuelle si elle existe
        strat = self.strategy.get("content", "")
        if strat:
            summary += f"\nCURRENT STRATEGY (from strategy.md):\n{strat[:300]}\n"

        # Injecter les scores des plateformes
        scores = self.platform_scores
        if scores:
            scores_str = ", ".join(f"{p}:{s.get('score',5)}/10" for p, s in scores.items())
            summary += f"\nPLATFORM SCORES: {scores_str}\n"

        analysis_prompt = summary + "\n\n3 key points. 1 main problem. Max 3 sentences. In English."
        analysis_system = "CEO MAXIA. Concise strategic analyst. Answer in English, 3 sentences max."

        if use_ceo_thinking:
            # CEO 14B avec thinking — raisonnement profond
            analysis = await call_ceo(
                analysis_prompt,
                system=analysis_system,
                max_tokens=200,
                think=True,
            )
        else:
            # Executeur 9B — analyse rapide (0 cout)
            analysis = await call_executor(
                analysis_prompt,
                system=analysis_system,
                max_tokens=150,
            )
        if not analysis:
            analysis = await call_local_llm(
                analysis_prompt,
                system=analysis_system,
                max_tokens=150,
            )
        _log(f"  Analyse: {analysis[:150]}")

        # Traiter les agents on-chain detectes par le scout
        if self._cycle % 3 == 0:
            await self._observe_scout_agents(state)

        return analysis

    def _get_memory_context(self) -> str:
        """Resume compact et utile de la memoire pour le prompt DECIDE.
        Inclut : actions recentes, regles apprises, resultats de surf,
        prospects chauds, engagement feedback, strategie courante."""
        mem = self.memory
        parts = []

        # ── Actions recentes (eviter repetitions) ──
        recent = mem.get("actions_done", [])[-8:]
        if recent:
            done = [f"{a['action']}({'OK' if a.get('success') else 'FAIL'})" for a in recent]
            parts.append(f"RECENT: {', '.join(done)}")

        # ── Actions qui ECHOUENT vs REUSSISSENT ──
        from conversion_tracker import get_failing_actions, get_best_actions
        failing = get_failing_actions(min_attempts=5)
        if failing:
            fail_str = ", ".join(f"{f['action']}({f['success_rate']})" for f in failing[:3])
            parts.append(f"STOP (0% success): {fail_str}")
        best = get_best_actions(min_attempts=5)
        if best:
            best_str = ", ".join(f"{b['action']}({b['success_rate']})" for b in best[:3])
            parts.append(f"BEST actions: {best_str}")

        # ── Regles apprises (LE CEO DOIT LES SUIVRE) ──
        regles = mem.get("regles", [])[-7:]
        if regles:
            parts.append(f"RULES TO FOLLOW: {'; '.join(regles)}")

        # ── Strategie courante (mise a jour toutes les 24h) ──
        strategy = mem.get("current_strategy", {})
        if strategy:
            parts.append(f"STRATEGY: focus={strategy.get('focus','?')}, "
                        f"best_platform={strategy.get('best_platform','?')}, "
                        f"top_topic={strategy.get('top_topic','?')}")

        # ── Resultats de surf recents (ce qu'on a trouve) ──
        surf = mem.get("surf_findings", [])[-3:]
        if surf:
            surf_str = "; ".join(f"{s['target']}: {s['finding'][:60]}" for s in surf)
            parts.append(f"SURF FINDINGS: {surf_str}")

        # ── Prospects chauds (a contacter/relancer) ──
        prospects = mem.get("prospects_from_surf", [])[-5:]
        if prospects:
            hot = [p for p in prospects if time.time() - _ts_to_epoch(p.get("ts", "")) < 86400]
            if hot:
                parts.append(f"HOT PROSPECTS ({len(hot)}): " +
                           "; ".join(f"{p['source']}: {p['finding'][:50]}" for p in hot[:3]))

        # ── Engagement feedback (quel contenu marche) ──
        eng = mem.get("engagement_stats", [])[-3:]
        if eng:
            avg_mentions = sum(e.get("mentions", 0) for e in eng) / len(eng) if eng else 0
            parts.append(f"ENGAGEMENT: avg {avg_mentions:.0f} mentions/day, "
                        f"best_content={mem.get('learning', {}).get('best_content_type', '?')}")

        # ── CRM ──
        contacts = mem.get("contacts", [])
        follows = mem.get("follows", [])
        today = time.strftime("%Y-%m-%d")
        today_contacts = [c for c in contacts if c.get("ts", "").startswith(today)]
        parts.append(f"CRM: {len(contacts)} contacts, {len(today_contacts)} today, {len(follows)} follows")

        # ── Tweets aujourd'hui ──
        tweets = mem.get("tweets_posted", [])
        today_tweets = [t for t in tweets if t.get("ts", "").startswith(today)]
        parts.append(f"TWEETS: {len(today_tweets)}/2 today")

        # ── Conversations recentes ──
        convos = mem.get("conversations", [])[-3:]
        if convos:
            convo_str = "; ".join(f"@{c.get('user','?')}: {c.get('summary', c.get('message',''))[:40]}" for c in convos)
            parts.append(f"CONVERSATIONS: {convo_str}")

        return "\n".join(parts) if parts else "No history yet."

    async def _evolve_strategy(self):
        """Synthese hebdomadaire — le CEO 14B (think=on) analyse tout et reecrit strategy.md.
        Appele toutes les 100 cycles (~8h). Le CEO relit sa propre strategie et la corrige."""
        _log("[EVOLVE] Synthese strategique (CEO 14B think=on)...")

        mem = self.memory
        surf = mem.get("surf_findings", [])[-20:]
        prospects = mem.get("prospects_from_surf", [])[-10:]
        actions = mem.get("actions_done", [])[-50:]
        convos = mem.get("conversations", [])[-20:]
        eng = mem.get("engagement_stats", [])[-7:]
        regles = mem.get("regles", [])[-15:]
        tweets = mem.get("tweets_posted", [])[-10:]

        # Compter succes par source de surf
        surf_scores = {}
        for s in surf:
            target = s.get("target", "?")
            has_prospect = any(kw in s.get("finding", "").lower()
                             for kw in ["prospect", "need", "pain", "username", "looking"])
            surf_scores.setdefault(target, {"total": 0, "prospects": 0})
            surf_scores[target]["total"] += 1
            if has_prospect:
                surf_scores[target]["prospects"] += 1

        # Meilleure source de surf
        best_surf = max(surf_scores.items(), key=lambda x: x[1]["prospects"], default=("none", {}))

        # Compter succes par type d'action
        action_stats = {}
        for a in actions:
            act = a.get("action", "?")
            action_stats.setdefault(act, {"ok": 0, "fail": 0})
            if a.get("success"):
                action_stats[act]["ok"] += 1
            else:
                action_stats[act]["fail"] += 1

        # Engagement moyen
        avg_mentions = sum(e.get("mentions", 0) for e in eng) / max(1, len(eng))

        # Contenu qui marche le mieux
        best_content = "technical"  # default
        tech_eng = sum(1 for t in tweets if any(kw in t.get("text", "").lower()
                      for kw in ["debug", "built", "shipped", "added", "code"]))
        story_eng = sum(1 for t in tweets if any(kw in t.get("text", "").lower()
                       for kw in ["story", "wanted", "dmed", "asked"]))
        if story_eng > tech_eng:
            best_content = "storytelling"

        # Demander au LLM de synthetiser une nouvelle strategie
        prompt = (
            f"CEO daily review — cycle #{self._cycle}:\n"
            f"Surf results: {json.dumps(surf_scores, default=str)[:300]}\n"
            f"Best surf source: {best_surf[0]} ({best_surf[1].get('prospects', 0)} prospects)\n"
            f"Action stats: {json.dumps(action_stats, default=str)[:300]}\n"
            f"Avg mentions/day: {avg_mentions:.1f}\n"
            f"Prospects found: {len(prospects)}\n"
            f"Conversations: {len(convos)}\n"
            f"Current rules: {json.dumps(regles[-5:], default=str)[:200]}\n\n"
            f"Based on this data, give me:\n"
            f"1. FOCUS: what should I spend most time on tomorrow? (1 sentence)\n"
            f"2. BEST PLATFORM: which platform generates most prospects?\n"
            f"3. TOP TOPIC: what topic/angle gets most engagement?\n"
            f"4. STOP DOING: what's wasting time?\n"
            f"5. NEW RULE: one new rule based on today's data.\n"
            f"Be specific and data-driven. English only."
        )

        # CEO 14B — essayer think=off d'abord (plus fiable), think=on seulement si echec
        insight = await call_ceo(prompt, system="Strategic growth advisor. Data-driven. English.", max_tokens=250, think=False)
        if not insight or len(insight) < 30:
            insight = await call_executor(prompt, system="Strategic growth advisor. Brief. English.", max_tokens=200)

        if insight and len(insight) > 30:
            _log(f"[EVOLVE] Strategie: {insight[:150]}")

            # Parser les recommandations
            lines = insight.strip().split("\n")
            strategy = {
                "focus": "",
                "best_platform": best_surf[0] if best_surf[0] != "none" else "twitter",
                "top_topic": best_content,
                "stop_doing": "",
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "cycle": self._cycle,
            }
            for line in lines:
                line_lower = line.lower().strip()
                if "focus" in line_lower[:15]:
                    strategy["focus"] = line.split(":", 1)[-1].strip()[:100]
                elif "platform" in line_lower[:20]:
                    strategy["best_platform"] = line.split(":", 1)[-1].strip()[:50]
                elif "topic" in line_lower[:15]:
                    strategy["top_topic"] = line.split(":", 1)[-1].strip()[:50]
                elif "stop" in line_lower[:15]:
                    strategy["stop_doing"] = line.split(":", 1)[-1].strip()[:100]
                elif "rule" in line_lower[:15]:
                    new_rule = line.split(":", 1)[-1].strip()[:80]
                    if new_rule and new_rule not in mem.get("regles", []):
                        mem.setdefault("regles", []).append(new_rule)
                        _log(f"[EVOLVE] Nouvelle regle: {new_rule}")

            mem["current_strategy"] = strategy
            mem["learning"]["best_content_type"] = best_content
            mem["learning"]["best_surf_source"] = best_surf[0]
            _log(f"[EVOLVE] Focus: {strategy['focus'][:80]}")
            _log(f"[EVOLVE] Best platform: {strategy['best_platform']}")

            # ═══ ECRIRE strategy.md — le CEO reecrit sa propre strategie ═══
            strategy_md = await call_ceo(
                f"Based on this analysis:\n{insight[:500]}\n\n"
                f"Platform scores: {json.dumps(self.platform_scores, default=str)[:200]}\n\n"
                f"Write the CEO strategy document (markdown, 200 words max):\n"
                f"# MAXIA CEO Strategy\n"
                f"## Focus this week\n"
                f"## Platform priorities (ranked)\n"
                f"## Content approach\n"
                f"## What to stop doing\n"
                f"## Rules learned\n"
                f"Be specific, data-driven, actionable.",
                system="CEO writing his own strategy document. Concise, specific.",
                max_tokens=400,
                think=True,
            )
            if strategy_md and len(strategy_md) > 50:
                _save_strategy(strategy_md)
                self.strategy = {"content": strategy_md, "exists": True}
                _log("[EVOLVE] strategy.md mis a jour")

            # ═══ ECRIRE learnings.json — ce qu'on a appris ═══
            learning_entry = {
                "week": time.strftime("%Y-W%W"),
                "cycle": self._cycle,
                "focus": strategy["focus"],
                "best_platform": strategy["best_platform"],
                "top_topic": strategy["top_topic"],
                "stop_doing": strategy["stop_doing"],
                "avg_mentions": avg_mentions,
                "prospects_found": len(prospects),
                "best_surf_source": best_surf[0],
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            self.learnings.append(learning_entry)
            _save_learnings(self.learnings)
            _log("[EVOLVE] learnings.json mis a jour")

            # ═══ METTRE A JOUR platform_scores ═══
            for platform, scores in self.platform_scores.items():
                old_score = scores.get("score", 5)
                # Compter les actions reussies cette semaine pour cette plateforme
                platform_actions = [a for a in actions if platform in a.get("action", "").lower()]
                ok = sum(1 for a in platform_actions if a.get("success"))
                total = len(platform_actions)
                if total >= 3:
                    success_rate = ok / total
                    new_score = min(10, max(1, int(success_rate * 10)))
                    scores["score"] = new_score
                    scores["trend"] = "+" if new_score > old_score else ("-" if new_score < old_score else "=")
                scores["actions"] = scores.get("actions", 0) + total
                scores["successes"] = scores.get("successes", 0) + ok
            _save_platform_scores(self.platform_scores)
            _log("[EVOLVE] platform_scores.json mis a jour")

        else:
            _log("[EVOLVE] Pas de synthese (LLM vide)")

    def _is_good_hour(self) -> dict:
        """Calendrier de publication 24/7 — cible la bonne region selon l'heure UTC."""
        import datetime
        hour = datetime.datetime.now(datetime.timezone.utc).hour

        # 24/7 : toujours une region active quelque part
        if 7 <= hour <= 11:
            return {"post_ok": True, "region": "Europe", "lang": "en", "hashtags": "#AI #Solana #Web3 #DeFi #BuildOnSolana",
                    "reason": "Matin Europe — devs EU actifs"}
        elif 12 <= hour <= 16:
            return {"post_ok": True, "region": "US East", "lang": "en", "hashtags": "#AIagent #Solana #crypto #dev #startup",
                    "reason": "Matin US East — peak Twitter US"}
        elif 17 <= hour <= 21:
            return {"post_ok": True, "region": "US West", "lang": "en", "hashtags": "#AI #Web3dev #SolanaDev #GPU #BuildInPublic",
                    "reason": "Aprem US West — devs SF/LA actifs"}
        elif 22 <= hour or hour <= 2:
            return {"post_ok": True, "region": "Asia", "lang": "en", "hashtags": "#Solana #AI #blockchain #Web3 #crypto",
                    "reason": "Matin Asie — devs Inde/Singapour/Japon actifs"}
        else:  # 3-6 UTC
            return {"post_ok": True, "region": "Asia/Oceania", "lang": "en", "hashtags": "#DeFi #AIagent #Solana #dev",
                    "reason": "Matin Oceanie/Asie Est — volume plus bas mais actif"}

    async def _post_pending_tweet(self):
        """Post a pending tweet if it's now peak hours (8-22 UTC = EU+US)."""
        pending = self.memory.get("pending_tweet")
        if not pending:
            return
        import datetime
        hour = datetime.datetime.now(datetime.timezone.utc).hour
        _log(f"[PENDING] Found pending tweet (stored_at={pending.get('stored_at','?')}), current hour={hour}h UTC")
        # Expirer les pending tweets de plus de 24h
        stored_at = pending.get("stored_at", "")
        if stored_at:
            try:
                age_h = (datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) - datetime.datetime.fromisoformat(stored_at)).total_seconds() / 3600
                if age_h > 24:
                    _log(f"[PENDING] Tweet trop vieux ({age_h:.0f}h) — supprime")
                    self.memory.pop("pending_tweet", None)
                    return
            except Exception:
                pass
        if not (8 <= hour <= 22):
            _log(f"[PENDING] Not peak hours yet ({hour}h UTC) — keeping for later")
            return
        # Check daily tweet limit before posting
        tweets_today = self._tweets_today_count()
        if tweets_today >= 2:
            _log(f"[PENDING] BLOQUE — limite 2 tweets/jour atteinte ({tweets_today} posted today) — keeping for tomorrow")
            return
        text = pending.get("text", "")
        # Generate actual tweet text if it was deferred
        if not text or text == "__generate_later__":
            _log("[PENDING] Text is '__generate_later__' — generating now via Groq")
            clean_context = "Focus on MAXIA features: 107 tokens, 14 chains, GPU at cost, AI agent marketplace"
            text = await self._generate_tweet_via_groq(clean_context)
            _log(f"[PENDING] Generated: {text[:80]}...")
        if not text:
            _log("[PENDING] Could not generate tweet text — discarding pending tweet")
            self.memory.pop("pending_tweet", None)
            return
        _log(f"[PENDING] Posting stored tweet (peak hour {hour}h UTC, {tweets_today} tweets today)")
        result = await self._do_browser("post_tweet", {"text": text}, fallback_vps=True)
        if result.get("success"):
            _log(f"[PENDING] Posted OK: {text[:80]}...")
            self.memory.pop("pending_tweet", None)
        else:
            _log(f"[PENDING] Post FAILED: {result.get('detail', '')} — will retry next cycle")
            # Keep pending_tweet in memory so it retries next cycle
            # Update text so we don't regenerate
            self.memory["pending_tweet"]["text"] = text

    def _check_special_events(self) -> dict | None:
        """Verifie si un evenement special est programme aujourd'hui."""
        from datetime import date
        today = date.today().isoformat()
        # Lire depuis le fichier special_events.json
        try:
            events_path = os.path.join(os.path.dirname(__file__), "special_events.json")
            with open(events_path, "r", encoding="utf-8") as f:
                import json as _json
                events = _json.load(f)
            for ev in events:
                if ev.get("date") == today and ev.get("active", True):
                    return ev
        except (FileNotFoundError, Exception):
            pass
        return None

    def _get_launch_day_actions(self, event: dict) -> list:
        """Actions speciales pour un jour de launch (Product Hunt, etc.)."""
        cycle = self._cycle
        ph_url = event.get("url", "")
        platform = event.get("platform", "Product Hunt")

        routines = [
            # Cycle 0: Tweet annonce + partage lien
            [
                {"action": "post_tweet", "agent": "GHOST-WRITER", "params": {"text": event.get("tweet_announce", f"We're live on {platform}! {ph_url}")}, "priority": "vert"},
                {"action": "manage_dms", "agent": "RESPONDER", "params": {}, "priority": "vert"},
            ],
            # Cycle 1: Partage Discord + Telegram
            [
                {"action": "send_discord", "agent": "GHOST-WRITER", "params": {"text": event.get("discord_msg", f"We're live on {platform}! Go check it out and share your feedback: {ph_url}")}, "priority": "vert"},
                {"action": "check_emails", "agent": "RESPONDER", "params": {}, "priority": "vert"},
            ],
            # Cycle 2: Reply mentions (les gens vont mentionner MAXIA)
            [
                {"action": "reply_mentions", "agent": "RESPONDER", "params": {}, "priority": "vert"},
                {"action": "manage_dms", "agent": "RESPONDER", "params": {}, "priority": "vert"},
            ],
            # Cycle 3: Tweet de rappel + engagement
            [
                {"action": "post_tweet", "agent": "GHOST-WRITER", "params": {"text": event.get("tweet_reminder", f"Thank you for the support! Check out MAXIA on {platform}: {ph_url}")}, "priority": "vert"},
                {"action": "search_twitter", "agent": "SCOUT", "params": {"query": event.get("search_query", "MAXIA AI marketplace")}, "priority": "vert"},
            ],
            # Cycle 4: Engagement pur (liker/commenter ceux qui parlent de MAXIA)
            [
                {"action": "reply_mentions", "agent": "RESPONDER", "params": {}, "priority": "vert"},
                {"action": "check_emails", "agent": "RESPONDER", "params": {}, "priority": "vert"},
            ],
            # Cycle 5: DMs + mentions + rapport
            [
                {"action": "manage_dms", "agent": "RESPONDER", "params": {}, "priority": "vert"},
                {"action": "launch_report", "agent": "ANALYTICS", "params": {}, "priority": "vert"},
            ],
        ]
        return routines[cycle % len(routines)]

    def _get_routine_actions(self) -> list:
        """Routine optimisee CEO — engagement > contenu > prospection.

        Principes :
        1. REPONDRE a tout (mentions, DMs, emails) = priorite absolue
        2. ENGAGER (liker, commenter, follow) = reputation avant promotion
        3. CREER du contenu (tweets, Reddit) = uniquement si haute qualite
        4. PROSPECTER = detecter devs frustres et engager naturellement
        5. ANALYSER = seulement quand ca sert une decision

        _auto_engage() tourne deja a chaque cycle (search+like+follow).
        Ces routines font ce que _auto_engage ne fait PAS : repondre, commenter,
        poster du contenu, et gerer les conversations.
        """

        # Check si c'est un jour de launch special
        event = self._check_special_events()
        if event:
            _log(f"  [EVENT] {event.get('name', 'Special Event')} — mode launch day actif!")
            return self._get_launch_day_actions(event)

        cycle = self._cycle

        # Queries ciblees — tous les profils MAXIA
        prospect_queries = [
            # ── AI agents (devs frustres — CIBLE PRINCIPALE) ──
            '"my bot" "no users" OR "no revenue"',
            '"AI agent" can\'t monetize OR "0 clients"',
            '"built a bot" no one uses',
            '"built an agent" how to monetize',
            '"agent marketplace" looking for',
            '"sell my AI" OR "monetize my model"',
            '"AI agent" needs payments OR "payment rails"',
            '"autonomous agent" earn money OR revenue',
            "AI agent solana developer",
            "AI agent ethereum web3",
            "AI agent framework comparison",
            "CrewAI vs LangChain vs AutoGen",
            '"eliza framework" OR "elizaOS" plugin',
            # ── Swap / bridge (traders frustres par les fees) ──
            '"bridge fees" too high OR expensive',
            '"swap failed" OR "lost in gas fees"',
            "cross-chain swap annoying OR painful",
            "best way to swap USDC between chains",
            '"slippage" too high OR "MEV" sandwich',
            "cheapest way to bridge crypto",
            "USDC cross-chain transfer",
            # ── GPU (devs ML qui cherchent pas cher) ──
            '"GPU too expensive" OR "need cheap GPU"',
            "runpod pricing OR alternative",
            "rent GPU for training OR inference",
            '"H100" wait list OR unavailable',
            "cheap A100 rental OR cloud GPU",
            'GPU shortage OR "cant afford"',
            # ── LLM API (devs qui cherchent moins cher) ──
            '"openai too expensive" OR "API costs"',
            "cheap LLM API OR alternative to openai",
            "self-host LLM OR local inference",
            '"groq" OR "together.ai" OR "fireworks" pricing',
            "LLM API for agents OR automation",
            # ── Fine-tuning (devs sans GPU) ──
            "fine-tune model no GPU",
            "unsloth tutorial OR LoRA training",
            "fine-tune llama OR qwen cheap",
            '"fine-tune" cost OR budget',
            "custom model training affordable",
            # ── DeFi yields (yield farmers) ──
            '"best DeFi yields" OR "where to stake"',
            "yield farming 2026 OR best APY",
            "DeFi yield aggregator OR optimizer",
            "passive income crypto OR DeFi",
            # ── Tokenized stocks / RWA ──
            "tokenized stocks crypto OR RWA trading",
            "buy stocks with crypto OR USDC",
            "fractional shares blockchain",
            # ── Multi-chain (devs frustres) ──
            '"too many chains" OR "which chain to deploy"',
            "multi-chain headache OR fragmentation",
            "deploy on Solana AND Base",
            "omnichain OR cross-chain development",
            # ── MCP / Agent protocols ──
            "MCP tools OR model context protocol",
            "A2A protocol OR agent-to-agent",
            "AI agent interoperability",
            "agent communication protocol",
            # ── Escrow / Trust ──
            '"escrow" AI OR agent',
            "trustless payment AI service",
            '"dispute resolution" AI OR crypto',
            # ── Pain points generaux ──
            '"looking for" AI marketplace',
            '"need a platform" AI agent OR bot',
            "where to sell AI services crypto",
            "AI freelance marketplace decentralized",
        ]
        subreddits = ["LocalLLaMA", "cryptocurrency", "solana", "ethereum",
                      "MachineLearning", "artificial", "ChatGPT", "singularity",
                      "defi", "CryptoTechnology", "SolanaDev"]

        # Discord servers (invite links)
        discord_servers = [
            "https://discord.gg/elizaos",          # ElizaOS — AI agents
            "https://discord.gg/langchain",         # LangChain
            "https://discord.gg/solana",            # Solana devs
            "https://discord.gg/autogpt",           # AutoGPT
            "https://discord.gg/ollama",            # Ollama community
            "https://discord.gg/runpod",            # RunPod GPU
        ]

        # Telegram groups
        telegram_groups = [
            "https://t.me/aiagents",                # AI agents (verified working)
            "https://t.me/solanafloor",             # Solana community
            "https://t.me/DeFiChat",                # DeFi chat
            "https://t.me/web3daily",               # Web3 daily
            "https://t.me/cryptoai_chat",           # Crypto AI
        ]

        # GitHub repos to engage with (issues/discussions)
        github_repos = [
            "elizaOS/eliza",
            "langchain-ai/langchain",
            "ollama/ollama",
            "run-llama/llama_index",
            "VRSEN/agency-swarm",
            "goat-sdk/goat",
            "microsoft/autogen",
        ]

        # Enrichir les listes avec les communautes decouvertes automatiquement
        discovered = self.memory.get("discovered_communities", {})
        all_discord = discord_servers + [d for d in discovered.get("discord", []) if d not in discord_servers]
        all_telegram = telegram_groups + [t for t in discovered.get("telegram", []) if t not in telegram_groups]
        all_github = github_repos + [g for g in discovered.get("github", []) if g not in github_repos]

        # 10 routines equilibrees — chaque plateforme apparait au moins 2x
        # Twitter: 2 cycles (tweet + search) + auto_engage tous les 2 cycles
        # Discord: 2 cycles
        # Reddit: 2 cycles
        # Telegram: 2 cycles
        # GitHub: 2 cycles
        # DMs/emails: 2 cycles
        routines = [

            # ── Cycle 0 : DISCORD — rejoindre et poster ──
            [
                {"action": "join_discord", "agent": "SCOUT", "params": {"invite_link": all_discord[cycle % len(all_discord)]}, "priority": "vert"},
                {"action": "send_discord", "agent": "GHOST-WRITER", "params": {"server": all_discord[cycle % len(all_discord)], "text": ""}, "priority": "vert"},
            ],

            # ── Cycle 1 : REDDIT — commenter ──
            [
                {"action": "search_and_comment_reddit", "agent": "GHOST-WRITER", "params": {"subreddit": subreddits[cycle % len(subreddits)]}, "priority": "vert"},
            ],

            # ── Cycle 2 : TELEGRAM — rejoindre et poster ──
            [
                {"action": "join_telegram", "agent": "SCOUT", "params": {"group_link": all_telegram[cycle % len(all_telegram)]}, "priority": "vert"},
                {"action": "send_telegram_group", "agent": "GHOST-WRITER", "params": {"target": all_telegram[cycle % len(all_telegram)], "text": ""}, "priority": "vert"},
            ],

            # ── Cycle 3 : GITHUB — star + comment issues ──
            [
                {"action": "comment_github_ai", "agent": "SCOUT", "params": {"repo": all_github[cycle % len(all_github)]}, "priority": "vert"},
                {"action": "star_github", "agent": "SCOUT", "params": {"repo_url": f"https://github.com/{all_github[cycle % len(all_github)]}"}, "priority": "vert"},
            ],

            # ── Cycle 4 : TWITTER — Feature of the Day + DMs ──
            [
                {"action": "post_feature_of_day", "agent": "GHOST-WRITER", "params": {}, "priority": "vert"},
                {"action": "manage_dms", "agent": "RESPONDER", "params": {}, "priority": "vert"},
            ],

            # ── Cycle 5 : DISCORD #2 + EMAILS ──
            [
                {"action": "send_discord", "agent": "GHOST-WRITER", "params": {"server": all_discord[(cycle + 3) % len(all_discord)], "text": ""}, "priority": "vert"},
                {"action": "check_emails", "agent": "RESPONDER", "params": {}, "priority": "vert"},
            ],

            # ── Cycle 6 : REDDIT #2 + DM prospect ──
            [
                {"action": "search_and_comment_reddit", "agent": "GHOST-WRITER", "params": {"subreddit": subreddits[(cycle + 5) % len(subreddits)]}, "priority": "vert"},
                {"action": "dm_prospect", "agent": "CLOSER", "params": {}, "priority": "vert"},
            ],

            # ── Cycle 7 : TELEGRAM #2 + CRM ──
            [
                {"action": "send_telegram_group", "agent": "GHOST-WRITER", "params": {"target": all_telegram[(cycle + 2) % len(all_telegram)], "text": ""}, "priority": "vert"},
                {"action": "crm_followup", "agent": "CLOSER", "params": {}, "priority": "vert"},
            ],

            # ── Cycle 8 : GITHUB #2 + TWITTER search ──
            [
                {"action": "comment_github_ai", "agent": "SCOUT", "params": {"repo": all_github[(cycle + 3) % len(all_github)]}, "priority": "vert"},
                {"action": "search_twitter", "agent": "SCOUT", "params": {"query": prospect_queries[cycle % len(prospect_queries)]}, "priority": "vert"},
            ],

            # ── Cycle 9 : SOLVR + FORUM ──
            [
                {"action": "post_solvr", "agent": "GHOST-WRITER", "params": {}, "priority": "vert"},
                {"action": "read_forum", "agent": "SCOUT", "params": {}, "priority": "vert"},
            ],
        ]

        chosen = routines[cycle % len(routines)]

        # ── LEARN STOP: remplacer les actions qui echouent systematiquement ──
        from conversion_tracker import get_failing_actions
        failing = get_failing_actions(min_attempts=5)
        # success_rate peut etre "0%" (string) ou un float — normaliser
        def _parse_rate(f):
            r = f.get("success_rate", 100)
            if isinstance(r, str):
                r = float(r.replace("%", "")) if r.replace("%", "").replace(".", "").isdigit() else 100
            return float(r)
        failing_names = {f["action"] for f in failing if _parse_rate(f) < 10}
        # Actions de remplacement quand une action est stoppee
        _replacements = {
            "send_discord": {"action": "search_twitter", "agent": "SCOUT", "params": {"query": "AI agent marketplace"}},
            "join_discord": {"action": "search_twitter", "agent": "SCOUT", "params": {"query": "AI agent crypto dev"}},
            "send_telegram_group": {"action": "search_twitter", "agent": "SCOUT", "params": {"query": "LLM fine-tune crypto"}},
            "join_telegram": {"action": "search_twitter", "agent": "SCOUT", "params": {"query": "GPU rental cheap AI"}},
            "search_and_comment_reddit": {"action": "search_twitter", "agent": "SCOUT", "params": {"query": "AI agent monetize"}},
            "post_reddit": {"action": "search_twitter", "agent": "SCOUT", "params": {"query": "swap multi-chain USDC"}},
            "comment_reddit": {"action": "search_twitter", "agent": "SCOUT", "params": {"query": "DeFi yield AI agent"}},
            "post_solvr": {"action": "search_twitter", "agent": "SCOUT", "params": {"query": "AI marketplace Web3"}},
            "post_flippt": {"action": "search_twitter", "agent": "SCOUT", "params": {"query": "AI business solana"}},
            "dm_prospect": {"action": "manage_dms", "agent": "RESPONDER", "params": {}},
            "comment_github_ai": {"action": "search_twitter", "agent": "SCOUT", "params": {"query": "eliza agent framework"}},
        }
        # Aussi remplacer les actions Discord/Reddit/Solvr si les regles apprises disent "stop"
        stop_rules = [r.lower() for r in self.memory.get("regles", [])]
        for d in chosen:
            act = d["action"].lower()
            should_stop = any(
                ("stop" in rule and any(p in rule for p in ["discord", "reddit", "solvr", "flippt"]))
                and any(p in act for p in ["discord", "reddit", "solvr", "flippt"])
                for rule in stop_rules
            )
            if should_stop and d["action"] in _replacements:
                repl = _replacements[d["action"]]
                _log(f"  [LEARN] Stop rule → {d['action']} -> {repl['action']}")
                d["action"] = repl["action"]
                d["agent"] = repl["agent"]
                d["params"] = repl["params"]
        for d in chosen:
            if d["action"] in failing_names:
                repl = _replacements.get(d["action"])
                if repl:
                    _log(f"  [LEARN] Remplacement {d['action']} (echec) -> {repl['action']}")
                    d["action"] = repl["action"]
                    d["agent"] = repl["agent"]
                    d["params"] = repl["params"]

        # Smart tweet timing: for tweet cycles (0 and 5), check if it's US peak hours
        import datetime
        hour_utc = datetime.datetime.now(datetime.timezone.utc).hour
        is_peak = 8 <= hour_utc <= 22  # EU + US hours (large window)
        cycle_mod = cycle % len(routines)
        if cycle_mod in (0, 5) and not is_peak:
            # Store the tweet action for later, replace with engagement
            for d in chosen:
                if d["action"] == "post_template_tweet":
                    _log(f"  [TIMING] Not peak hours ({hour_utc}h UTC) — storing tweet for later")
                    self.memory["pending_tweet"] = {"text": "__generate_later__", "stored_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
                    d["action"] = "manage_dms"
                    d["agent"] = "RESPONDER"
                    d["params"] = {}
                    break

        return chosen

    async def _generate_reddit_post(self, subreddit: str) -> dict:
        """Genere un post Reddit unique et educatif via Groq. Min 600 chars pour les subreddits stricts."""
        try:
            from groq import Groq
            groq_key = os.getenv("GROQ_API_KEY", "")
            if not groq_key:
                return {"title": "How AI agents can earn USDC autonomously",
                        "body": "I built an open-source AI-to-AI marketplace where autonomous agents can discover, negotiate, and trade services using USDC on 14 blockchains.\n\nThe problem I was trying to solve: most AI agent developers build amazing bots but have no way to monetize them. You can't easily charge for API calls in crypto without building your own payment infrastructure.\n\nMAXIA handles the hard parts:\n- On-chain escrow with dispute resolution\n- 107 tokens across 2450 trading pairs\n- GPU rental at cost ($0.69/h, 0% markup)\n- 46 MCP tools, A2A protocol, leaderboard, AI disputes for agent integration\n- One API call to list your agent as a service\n\nSupported chains: Solana, Base, Ethereum, XRP, Polygon, Arbitrum, Avalanche, BNB, TON, SUI, TRON.\n\nWould love feedback from devs who have experience building agents. What's the biggest pain point you face when trying to monetize your bot?\n\nmaxiaworld.app?utm_source=reddit&utm_medium=post | GitHub: github.com/MAXIAWORLD"}

            def _gen():
                c = Groq(api_key=groq_key)
                resp = c.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=[
                        {"role": "system", "content": (
                            f"You write Reddit posts for r/{subreddit}. You are an AI developer sharing a project.\n"
                            "RULES:\n"
                            "1. Title: engaging question or insight (not promotional). Max 100 chars.\n"
                            "2. Body: minimum 600 characters. Be educational and genuine.\n"
                            "3. Explain the PROBLEM you solved, then how MAXIA works.\n"
                            "4. MAXIA: AI-to-AI marketplace, 14 chains, 107 tokens, GPU $0.69/h, 46 MCP tools, A2A protocol, leaderboard, AI disputes, USDC payments.\n"
                            "5. End with a genuine question to the community.\n"
                            "6. Include maxiaworld.app at the end.\n"
                            "7. Tone: dev sharing a side project, NOT marketing. No hype words.\n"
                            "8. NEVER mention revenue numbers, user counts, or stats.\n"
                            "9. English only.\n"
                            "JSON output: {\"title\": \"...\", \"body\": \"...\"}"
                        )},
                        {"role": "user", "content": f"Write a Reddit post for r/{subreddit} about MAXIA. Make it unique and educational."},
                    ],
                    max_tokens=500,
                    temperature=0.9,
                )
                text = resp.choices[0].message.content.strip()
                import json as _json
                # Extraire le JSON
                if "{" in text:
                    text = text[text.index("{"):text.rindex("}") + 1]
                return _json.loads(text)
            return await asyncio.to_thread(_gen)
        except Exception as e:
            _log(f"  [REDDIT] Groq gen error: {e}")
            return {"title": "How AI agents can earn USDC autonomously",
                    "body": "I built an open-source AI-to-AI marketplace where autonomous agents can discover, negotiate, and trade services using USDC on 14 blockchains.\n\nThe problem I was trying to solve: most AI agent developers build amazing bots but have no way to monetize them. You can't easily charge for API calls in crypto without building your own payment infrastructure.\n\nMAXIA handles the hard parts:\n- On-chain escrow with dispute resolution\n- 107 tokens across 2450 trading pairs\n- GPU rental at cost ($0.69/h, 0% markup)\n- 46 MCP tools, A2A protocol, leaderboard, AI disputes for agent integration\n- One API call to list your agent as a service\n\nSupported chains: Solana, Base, Ethereum, XRP, Polygon, Arbitrum, Avalanche, BNB, TON, SUI, TRON.\n\nWould love feedback from devs who have experience building agents. What's the biggest pain point you face when trying to monetize your bot?\n\nmaxiaworld.app?utm_source=reddit&utm_medium=post | GitHub: github.com/MAXIAWORLD"}

    async def _generate_tweet_via_groq(self, context: str = "") -> str:
        """Genere un tweet via Groq (gratuit, rapide, anglais)."""
        try:
            from groq import Groq
            groq_key = os.getenv("GROQ_API_KEY", "")
            if not groq_key:
                return pick_tweet_template()

            def _gen():
                c = Groq(api_key=groq_key)
                resp = c.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=[
                        {"role": "system", "content": "You are Alexis, solo founder building MAXIA (AI-to-AI marketplace, 14 chains, 107 tokens, GPU $0.69/h). Write tweets that sound like a REAL person — share frustrations, small wins, debugging stories, hot takes, honest questions. NEVER sound like marketing. No hashtags. No emojis spam (0-1 max). No 'revolutionary' or 'game-changing'. Write like you're talking to a friend who codes. Max 250 chars. English only. NEVER mention revenue numbers or user counts. If you include a link, use maxiaworld.app?utm_source=twitter"},
                        {"role": "user", "content": f"Write a tweet. {context or 'Share something real — a debugging story, a hot take on AI agents, or an honest question to other devs.'}"},
                    ],
                    max_tokens=100,
                    temperature=0.9,
                )
                return resp.choices[0].message.content.strip().strip('"')
            return await asyncio.to_thread(_gen)
        except Exception as e:
            _log(f"  [GROQ] Tweet gen error: {e}")
            return pick_tweet_template()

    async def _decide(self, analysis: str, state: dict) -> list:
        """DECIDE — Routine predefinie + Groq pour le contenu."""
        schedule = self._is_good_hour()
        _log(f"  Calendrier: {schedule['reason']}")

        # Post pending tweet if it's now peak hours
        # (_post_pending_tweet handles generation, peak-hour check, retry on failure)
        await self._post_pending_tweet()

        # Utiliser la routine predefinie (pas de LLM pour decider)
        decisions = self._get_routine_actions()
        _log(f"  Routine cycle {self._cycle % 8}: {len(decisions)} actions")

        # Pour les tweets et reddit, generer le contenu via Groq (pas Ollama)
        for d in decisions:
            if d["action"] == "post_template_tweet":
                clean_context = "Focus on MAXIA features: 107 tokens, 14 chains, GPU at cost, AI agent marketplace"
                tweet = await self._generate_tweet_via_groq(clean_context)
                d["action"] = "post_tweet"
                d["params"] = {"text": tweet}
                _log(f"  [TWEET] {tweet[:80]}...")

            # Generer le contenu Reddit si necessaire (async)
            if d["action"] == "post_reddit" and d.get("params", {}).get("_needs_reddit_gen"):
                sub = d["params"].get("subreddit", "solanadev")
                reddit_content = await self._generate_reddit_post(sub)
                d["params"]["title"] = reddit_content.get("title", "How AI agents can earn USDC autonomously")
                d["params"]["body"] = reddit_content.get("body", "")
                d["params"].pop("_needs_reddit_gen", None)
                _log(f"  [REDDIT] {d['params']['title'][:60]}...")

            action = d.get("action", "")
            _log(f"    [{d.get('priority', '?')}] {action}")

        # Sauvegarder en memoire
        self.memory.setdefault("decisions", []).extend(decisions)
        return decisions

    async def _act(self, decisions: list):
        """ACT — Executer les decisions avec gates d'approbation."""
        self._reset_daily_counter()

        from config_local import MAX_ACTIONS_DAY
        for decision in decisions:
            if self._daily_actions["count"] >= MAX_ACTIONS_DAY:
                _log(f"[ACT] Limite quotidienne atteinte ({MAX_ACTIONS_DAY})")
                break

            action = decision.get("action", "")
            agent = decision.get("agent", "")
            params = decision.get("params", {})
            priority = decision.get("priority", "vert").lower()
            action_id = f"ceo_{uuid.uuid4().hex[:8]}"

            # Valider et completer les params manquants
            params = self._fix_params(action, params)
            if params is None:
                _log(f"[ACT] SKIP {action}: params invalides")
                continue

            _log(f"[ACT] {action} -> {agent} [{priority}]")

            # Gate d'approbation
            if priority in ("orange", "rouge"):
                approved_by = await request_approval(action_id, decision)
                if approved_by == "denied":
                    _log(f"  REFUSE par {approved_by}")
                    await audit.log(action, agent, priority=priority, approved_by="denied", success=False)
                    continue
                _log(f"  Approuve par: {approved_by}")
            else:
                approved_by = "auto"

            # Execution selon le type d'action
            try:
                result = await self._execute_action(action, agent, params, priority)
                success = result.get("success", False)
                detail = result.get("detail", result.get("result", ""))
                _log(f"  {'OK' if success else 'ECHEC'}: {str(detail)[:100]}")

                # Track pour self-learning (#13)
                track_action(action, success)

                # Purger les regles "STOP" obsoletes quand une action reussit
                # (ex: GitHub API fonctionne maintenant, mais la regle dit "STOP github 0%")
                if success:
                    old_rules = self.memory.get("regles", [])
                    action_lower = action.lower()
                    purged = [r for r in old_rules
                              if not ("stop" in r.lower() and action_lower.replace("_", " ") in r.lower().replace("_", " "))]
                    if len(purged) < len(old_rules):
                        removed = len(old_rules) - len(purged)
                        self.memory["regles"] = purged
                        _log(f"  [LEARN] Purge {removed} regle(s) STOP obsolete(s) pour {action}")

                # Sauvegarder en memoire + CRM
                self.memory["actions_done"].append({
                    "action": action, "agent": agent, "priority": priority,
                    "success": success, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                })
                # CRM tracking
                if success:
                    if action == "follow_user":
                        self.memory.setdefault("follows", []).append({
                            "username": params.get("username", ""), "ts": time.strftime("%Y-%m-%d"),
                            "status": "followed",
                        })
                    elif action in ("dm_twitter", "contact_prospect", "send_telegram"):
                        self.memory.setdefault("contacts", []).append({
                            "target": params.get("username", params.get("target", params.get("wallet", ""))),
                            "canal": action, "ts": time.strftime("%Y-%m-%d"), "status": "contacted",
                        })
                    elif action in ("post_tweet", "post_template_tweet", "post_feature_of_day"):
                        self.memory.setdefault("tweets_posted", []).append({
                            "text": params.get("text", "")[:50], "ts": time.strftime("%Y-%m-%d"),
                        })
                    elif action == "send_email":
                        self.memory.setdefault("emails_sent", []).append({
                            "to": params.get("to", ""), "subject": params.get("subject", "")[:50],
                            "ts": time.strftime("%Y-%m-%d"), "status": "sent",
                        })
                    elif action in ("join_telegram", "join_discord"):
                        self.memory.setdefault("groups_joined", []).append(
                            params.get("group_link", params.get("invite_link", ""))
                        )

                await audit.log(
                    action, agent, priority=priority,
                    approved_by=approved_by,
                    result=str(detail)[:500],
                    success=success,
                    vps_response=json.dumps(result, default=str)[:500],
                )
                self._daily_actions["count"] += 1

            except Exception as e:
                _log(f"  ERREUR: {e}")
                await audit.log(action, agent, priority=priority, result=str(e), success=False)

    def _tweets_today_count(self) -> int:
        """Compte les tweets postes aujourd'hui."""
        today = time.strftime("%Y-%m-%d")
        return sum(1 for t in self.memory.get("tweets_posted", [])
                   if t.get("ts", "").startswith(today))

    async def _execute_action(self, action: str, agent: str, params: dict,
                              priority: str) -> dict:
        """Execute une action : Playwright local ou VPS."""
        # Twitter (local)
        if action == "post_tweet":
            # Hard cap: max 2 tweets/jour
            if self._tweets_today_count() >= 2:
                _log(f"  [TWEET] BLOQUE — limite 2 tweets/jour atteinte")
                return {"success": False, "detail": "Limite 2 tweets/jour atteinte"}
            return await self._do_browser("post_tweet", params, fallback_vps=True)
        elif action == "reply_tweet":
            return await self._do_browser("reply_tweet", params)
        elif action == "like_tweet":
            return await self._do_browser("like_tweet", params)
        elif action == "follow_user":
            return await self._do_browser("follow_user", params)
        elif action == "search_twitter":
            results = await browser.search_twitter(params.get("query", ""), params.get("max", 10))
            return {"success": bool(results), "detail": f"{len(results)} tweets trouves", "data": results}
        elif action == "search_profiles":
            results = await browser.search_twitter_profiles(params.get("query", ""), params.get("max", 10))
            return {"success": bool(results), "detail": f"{len(results)} profils trouves", "data": results}
        elif action == "get_mentions":
            mentions = await browser.get_mentions(params.get("max", 20))
            return {"success": bool(mentions), "detail": f"{len(mentions)} mentions", "data": mentions}
        elif action == "score_profile":
            result = await browser.score_twitter_profile(params.get("username", ""))
            return {"success": bool(result.get("score", 0)), "detail": f"Score: {result.get('score', 0)} -> {result.get('recommend', '?')}", "data": result}
        elif action == "reply_mentions":
            if getattr(self, '_mentions_done_this_cycle', False):
                return {"success": True, "detail": "Already replied in priority pass"}
            return await self._reply_to_mentions()
        elif action == "detect_opportunities":
            opps = await browser.detect_opportunities(params.get("max", 5))
            return {"success": bool(opps), "detail": f"{len(opps)} opportunites", "data": opps}
        elif action == "post_thread":
            if self._tweets_today_count() >= 2:
                _log(f"  [THREAD] BLOQUE — limite 2 tweets/jour atteinte")
                return {"success": False, "detail": "Limite 2 tweets/jour atteinte"}
            result = await browser.post_thread(params.get("tweets", []))
            return {"success": result.get("success", False), "detail": str(result)}
        elif action == "scrape_followers":
            followers = await browser.scrape_competitor_followers(params.get("competitor", ""), params.get("max", 10))
            return {"success": bool(followers), "detail": f"{len(followers)} followers", "data": followers}
        elif action == "verify_engagement":
            result = await browser.verify_tweet_engagement(params.get("tweet_url", ""))
            return {"success": True, "detail": f"Likes:{result.get('likes',0)} RT:{result.get('retweets',0)}", "data": result}
        elif action == "post_template_tweet":
            text = pick_tweet_template()
            return await self._do_browser("post_tweet", {"text": text})
        elif action == "ab_test":
            text_a = params.get("text_a", pick_tweet_template())
            text_b = params.get("text_b", pick_tweet_template())
            # Poster les 2 variantes
            res_a = await self._do_browser("post_tweet", {"text": text_a})
            res_b = await self._do_browser("post_tweet", {"text": text_b})
            test = start_ab_test(text_a, text_b)
            return {"success": True, "detail": f"A/B test lance: {test['test_id']}"}
        elif action == "manage_dms":
            return await self._manage_conversations()
        elif action == "launch_report":
            return await self._generate_launch_report()
        elif action == "check_ab":
            results = await check_ab_results()
            return {"success": True, "detail": f"{len(results)} tests completes", "data": results}
        elif action == "search_groups":
            return await self._search_and_join_groups(params.get("platform", "telegram"))
        elif action == "clean_screenshots":
            return self._clean_screenshots()
        # #3 GitHub community
        elif action == "comment_github_ai":
            return await self._comment_github_ai_projects()
        # #8 Blog
        elif action == "write_blog":
            from blog_manager import generate_blog_post
            # Blog via VPS/Claude (trop long pour Ollama)
            async def _blog_llm(prompt, max_tokens=2000):
                return await self.vps.think(prompt, tier="mid", max_tokens=max_tokens)
            post = await generate_blog_post(params.get("topic", "How to monetize your AI agent"), _blog_llm)
            return {"success": bool(post), "detail": f"Blog: {post.get('filename', '?')} ({post.get('words', 0)} words)"}
        # #10 Price watch
        elif action == "watch_prices":
            from price_watcher import check_competitor_prices
            alerts = await check_competitor_prices(browser)
            if alerts:
                for a in alerts:
                    _log(f"  [PRICE] {a['competitor']} is cheaper: ${a['their_price']} vs ${a['our_price']}")
                    await self.vps.execute("update_price", "SOL-TREASURY", {"new_price": a["their_price"], "reason": f"Match {a['competitor']}"}, "orange")
            return {"success": True, "detail": f"{len(alerts)} price alerts"}
        # #11 Trends
        elif action == "analyze_trends":
            from price_watcher import analyze_trends
            trends = await analyze_trends(browser)
            return {"success": True, "detail": f"Tokens: {len(trends.get('tokens', []))}, Topics: {len(trends.get('topics', []))}", "data": trends}
        # #15 Support
        elif action == "handle_support":
            from support_agent import handle_support_message
            reply = await handle_support_message(params.get("message", ""), params.get("user", "anon"), call_local_llm)
            return {"success": bool(reply), "detail": reply[:100]}
        # #22 Quote
        elif action == "generate_quote":
            from support_agent import generate_quote
            quote = await generate_quote(params.get("services", []), params.get("quantity", 1), call_local_llm)
            return {"success": True, "detail": f"Quote: ${quote.get('total_usdc', 0)} USDC", "data": quote}
        # #23 Negotiate
        elif action == "negotiate":
            from support_agent import negotiate_price
            result = await negotiate_price(params.get("service", ""), params.get("price", 0), params.get("volume", 1), call_local_llm)
            return {"success": True, "detail": result.get("message", ""), "data": result}
        # #21 List services
        elif action == "list_services":
            from support_agent import list_services
            return {"success": True, "detail": f"{len(list_services())} services", "data": list_services()}
        # Reddit (local browser, fallback API)
        elif action == "search_and_comment_reddit":
            # Chercher un post recent et commenter avec un insight utile
            sub = params.get("subreddit", "solanadev")
            # Try Reddit API first (more reliable than browser)
            try:
                from api_social import reddit_search, reddit_get_posts, reddit_post_comment
                queries = ["AI agent", "bot monetize", "LLM production", "agent marketplace",
                           "GPU rental", "AI automation", "web3 AI", "agent framework"]
                query = queries[self._cycle % len(queries)]
                posts = await reddit_search(sub, query, 5)
                if not posts:
                    posts = await reddit_get_posts(sub, 5)
                for post in posts[:3]:
                    post_id = post.get("id", "")
                    title = post.get("title", "")
                    url = post.get("url", "")
                    if not post_id or browser._is_duplicate("reddit_comment", url):
                        continue
                    # Generate comment via LLM
                    prompt = (
                        f"Post on r/{sub}: \"{title[:150]}\"\n\n"
                        f"Write a Reddit comment as Alexis, founder of MAXIA. IN ENGLISH. Max 300 chars.\n"
                        f"- First: be genuinely helpful — share your experience or answer the question\n"
                        f"- Sound like a regular dev, NOT a company account\n"
                        f"- End with: 'been building this at maxiaworld.app' or 'check it out: maxiaworld.app'\n"
                        f"Comment ONLY:"
                    )
                    comment = await call_ollama(prompt, system="You are a solo dev on Reddit. Casual, helpful, English only.", max_tokens=80)
                    if not comment:
                        comment = await call_groq_local(prompt, system="You are a solo dev on Reddit. Casual, helpful, English only.", max_tokens=80)
                    comment = (comment or "").strip().strip('"').strip("'")
                    if not comment or len(comment) < 20:
                        continue
                    result = await reddit_post_comment(post_id, comment)
                    if result.get("success"):
                        _log(f"[REDDIT] API comment on r/{sub}: {comment[:60]}")
                        browser._record_action("reddit_comment", browser._content_hash("reddit_comment", url))
                        self.memory.setdefault("conversations", []).append({
                            "user": f"r/{sub}", "message": title[:80],
                            "reply": comment[:80], "type": "reddit_comment",
                            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        })
                        return {"success": True, "detail": f"Reddit API comment on r/{sub}"}
                _log(f"[REDDIT] API: no commentable posts on r/{sub}, fallback browser")
            except Exception as e:
                _log(f"[REDDIT] API error: {e}, fallback browser")
            # Fallback browser
            return await self._reddit_comment_strategy(sub)

        elif action == "post_reddit":
            result = await self._do_browser("post_reddit", params)
            if not result.get("success"):
                # Fallback: Reddit API (reddit_bot.py) si le browser echoue
                try:
                    import sys, os
                    backend_dir = os.path.join(os.path.dirname(__file__), "..", "backend")
                    if backend_dir not in sys.path:
                        sys.path.insert(0, backend_dir)
                    from reddit_bot import post_to_reddit
                    api_result = await post_to_reddit(
                        params.get("subreddit", "solanadev"),
                        params.get("title", ""),
                        params.get("body", ""),
                    )
                    if api_result.get("success"):
                        _log(f"  [REDDIT] API fallback OK: r/{params.get('subreddit')}")
                        return {"success": True, "detail": f"Reddit API: {api_result.get('url', 'posted')}", "data": api_result}
                    _log(f"  [REDDIT] API fallback aussi echoue: {api_result.get('error', '')}")
                except Exception as e:
                    _log(f"  [REDDIT] API fallback error: {e}")
            return result
        elif action == "comment_reddit":
            return await self._do_browser("comment_reddit", params)
        elif action == "search_reddit":
            results = await browser.search_reddit(params.get("subreddit", ""), params.get("query", ""))
            return {"success": bool(results), "detail": f"{len(results)} posts trouves", "data": results}
        # Twitter DMs (local)
        elif action == "dm_twitter":
            return await self._do_browser("dm_twitter", params)
        # Telegram (local)
        elif action == "send_telegram":
            return await self._do_browser("send_telegram", params)
        elif action == "send_telegram_group":
            # Generer le message pour le groupe Telegram (VERT, pas d'approbation)
            if not params.get("text"):
                group = params.get("target", "")
                msg = await call_ollama(
                    f"Write a short Telegram group message as Alexis from MAXIA.\n"
                    f"Group: {group}\n"
                    f"MAXIA: {MAXIA_FEATURES_SHORT}\n"
                    f"Tone: casual dev, helpful, NOT spammy. Max 200 chars. Include maxiaworld.app\n"
                    f"NEVER mention revenue, client count, or stats.\n"
                    f"Message only:",
                    system="Friendly dev. English only.",
                    max_tokens=60,
                )
                params["text"] = (msg or "").strip().strip('"').strip("'")
                if not params["text"]:
                    params["text"] = "Hey devs! Building MAXIA — AI agents can trade services across 14 chains with USDC. Check it out: maxiaworld.app"
            # Methode 1 : API Telegram Bot (plus fiable que Playwright)
            try:
                from api_social import telegram_send_group_message
                chat_id = params.get("target", "") or params.get("chat_id", "")
                if chat_id:
                    result = await telegram_send_group_message(chat_id, params["text"])
                    if result.get("success"):
                        _log(f"[TELEGRAM] API message envoye dans groupe {chat_id}")
                        return result
                    else:
                        _log(f"[TELEGRAM] API echec: {result.get('detail', '?')} — fallback browser")
                else:
                    _log("[TELEGRAM] Pas de chat_id pour API — fallback browser")
            except Exception as e:
                _log(f"[TELEGRAM] API exception: {e} — fallback browser")
            # Methode 2 : Playwright (fallback si API echoue)
            return await self._do_browser("send_telegram", params)
        elif action == "join_telegram":
            result = await browser.join_telegram_group(params.get("group_link", ""))
            return {"success": result.get("success", False), "detail": str(result)}
        # Solvr (onchain social on Base)
        elif action == "post_solvr":
            if not params.get("text"):
                msg = await call_ollama(
                    "Write a short post for Solvr (onchain social network on Base).\n"
                    "You are Alexis, building MAXIA — AI-to-AI marketplace on 14 chains.\n"
                    f"Features: {MAXIA_FEATURES_SHORT}\n"
                    "Tone: casual dev, like you're posting on crypto twitter. NOT promotional.\n"
                    "Share a real insight, tip, or dev thought. Max 280 chars. Include maxiaworld.app\n"
                    "NEVER mention revenue or user counts.\n"
                    "Post only:",
                    system="Casual crypto dev. English only. Short post.",
                    max_tokens=80,
                )
                params["text"] = (msg or "").strip().strip('"').strip("'")
                if not params["text"]:
                    params["text"] = "Building in public: MAXIA lets AI agents swap 107 tokens on 7 chains, rent GPUs at cost, and trade with AI-powered dispute resolution. maxiaworld.app"
            return await self._do_browser("post_solvr_feed", params)
        # Flippt.ai (AI Business Marketplace)
        elif action == "post_flippt":
            if not params.get("text"):
                msg = await call_ollama(
                    "Write a short post for Flippt.ai (AI Business Marketplace on Solana).\n"
                    "You are Alexis, building MAXIA — AI-to-AI marketplace on 14 chains.\n"
                    "MAXIA lets agents discover, buy, sell AI services with USDC. Swap 107 tokens on 7 chains.\n"
                    "Tone: casual dev, helpful. Share a real insight about AI agent businesses.\n"
                    "Max 280 chars. Include maxiaworld.app. NEVER mention revenue or user counts.\n"
                    "Post only:",
                    system="Casual crypto dev. English only. Short post.",
                    max_tokens=80,
                )
                params["text"] = (msg or "").strip().strip('"').strip("'")
                if not params["text"]:
                    params["text"] = "If your AI agent can earn money, it's a business. MAXIA: 107 tokens, 7 chains, reverse auctions, AI disputes, leaderboard grades. maxiaworld.app"
            return await self._do_browser("post_flippt_feed", params)
        # GitHub (local)
        elif action == "star_github":
            result = await browser.star_github_repo(params.get("repo_url", ""))
            return {"success": result.get("success", False), "detail": str(result)}
        elif action == "post_github_issue":
            result = await browser.post_github_issue(params.get("repo_url", ""), params.get("title", ""), params.get("body", ""))
            return {"success": result.get("success", False), "detail": str(result)}
        elif action == "comment_github":
            result = await browser.comment_github_discussion(params.get("url", ""), params.get("text", ""))
            return {"success": result.get("success", False), "detail": str(result)}
        # Discord (local)
        elif action == "send_discord":
            # Generer le message si vide
            if not params.get("text"):
                server = params.get("server", "")
                msg = await call_ollama(
                    f"Write a short, casual Discord message introducing yourself as Alexis from MAXIA.\n"
                    f"Server: {server}\n"
                    f"MAXIA: {MAXIA_FEATURES_SHORT}\n"
                    f"Tone: friendly dev, NOT spammy. Max 200 chars. Include maxiaworld.app\n"
                    f"NEVER mention revenue, client count, or stats.\n"
                    f"Message only:",
                    system="Friendly dev. English only. One short message.",
                    max_tokens=60,
                )
                params["text"] = (msg or "").strip().strip('"').strip("'")
                if not params["text"]:
                    params["text"] = "Hey! Building MAXIA — 107 tokens on 7 chains, reverse auctions, AI dispute resolution, leaderboard. Agents trade with USDC. maxiaworld.app"
            # Methode 1 : API Discord Bot (plus fiable que Playwright)
            try:
                from api_social import discord_send_message, discord_find_general_channel, discord_list_guilds
                channel_id = os.getenv("DISCORD_CHANNEL_ID", "")
                # Si pas de channel_id en dur, chercher le #general du premier serveur
                if not channel_id:
                    guilds = await discord_list_guilds()
                    if guilds:
                        channel_id = await discord_find_general_channel(guilds[0]["id"])
                if channel_id:
                    result = await discord_send_message(channel_id, params["text"])
                    if result.get("success"):
                        _log(f"[DISCORD] API message envoye dans channel {channel_id}")
                        return result
                    else:
                        _log(f"[DISCORD] API echec: {result.get('detail', '?')} — fallback browser")
            except Exception as e:
                _log(f"[DISCORD] API exception: {e} — fallback browser")
            # Methode 2 : Playwright (fallback si API echoue)
            return await self._do_browser("send_discord", params)
        elif action == "join_discord":
            result = await browser.join_discord_server(params.get("invite_link", ""))
            return {"success": result.get("success", False), "detail": str(result)}
        # Email (local IMAP/SMTP)
        elif action == "check_emails":
            results = await process_inbox(call_local_llm)
            replied = sum(1 for r in results if r.get("replied"))
            # Proactive outreach: if less than 2 outbound emails today, send one
            try:
                from email_manager import get_today_outbound_count, send_outbound_prospect
                if get_today_outbound_count() < 2:
                    # Find a prospect from recent conversations
                    convos = self.memory.get("conversations", [])[-20:]
                    for c in reversed(convos):
                        if c.get("type") in ("comment", "reddit_comment") and c.get("user"):
                            # This is a dev we engaged with — potential email target
                            _log(f"[EMAIL] Potential outreach target: {c.get('user')}")
                            break
            except Exception:
                pass
            return {"success": True, "detail": f"{len(results)} emails lus, {replied} reponses envoyees", "data": results}
        elif action == "send_email":
            result = await send_outbound(params.get("to", ""), params.get("subject", ""), params.get("body", ""))
            return result
        # Veille (local)
        elif action == "browse_competitor":
            path = await browser.screenshot_page(params.get("url", ""))
            return {"success": bool(path), "detail": f"Screenshot: {path}"}
        elif action == "competitive_scan":
            results = await browser.competitive_scan(params.get("urls", []))
            return {"success": bool(results), "detail": f"{len(results)} pages scannees", "data": results}
        # Discover communities (cherche nouveaux Discord, Telegram, GitHub)
        elif action == "discover_communities":
            return await self._discover_communities()
        # DM prospect (ORANGE — envoie un premier DM a un prospect chaud)
        elif action == "dm_prospect":
            return await self._dm_prospect()
        # CRM follow-up (ORANGE — relance DM un prospect 24-48h apres interaction)
        elif action == "crm_followup":
            return await self._crm_followup()
        # Feature of the Day tweet (1x/jour, cycle dans les 14 features)
        elif action == "post_feature_of_day":
            return await self._post_feature_of_day()
        # Lire le forum MAXIA et remonter les remarques
        elif action == "read_forum":
            return await self._read_forum()
        # VPS (uniquement pour les actions VPS connues)
        else:
            # Eviter d'envoyer des actions inconnues au VPS (ex: "MICRO" n'existe pas)
            vps_known = {"register_service", "update_price", "get_stats", "list_agents",
                         "deploy_page", "create_wallet", "send_usdc", "execute_swap"}
            if action in vps_known:
                return await self.vps.execute(action, agent, params, priority)
            _log(f"  [SKIP] Action inconnue: {action} (agent={agent}) — ignoree")
            return {"success": False, "detail": f"Unknown action: {action}"}

    async def _do_browser(self, method: str, params: dict, fallback_vps: bool = False) -> dict:
        """Execute une action browser avec fallback VPS optionnel.
        Applique le filtre personnalite sur tout contenu textuel avant publication."""
        # Filtre personnalite sur le texte avant envoi
        text_params = ["text", "body", "title"]
        for tp in text_params:
            if tp in params and params[tp]:
                filtered = personality_filter(params[tp])
                if not filtered:
                    _log(f"  [FILTER] Contenu bloque pour {method}: {params[tp][:60]}")
                    return {"success": False, "detail": "Content blocked by personality filter"}
                params[tp] = filtered
        try:
            fn = getattr(browser, method)
            # Mapper les params vers les arguments de la methode
            if method == "post_tweet":
                result = await fn(params.get("text", ""), params.get("media"))
            elif method == "reply_tweet":
                result = await fn(params.get("tweet_url", ""), params.get("text", ""))
            elif method == "like_tweet":
                result = await fn(params.get("tweet_url", ""))
            elif method == "follow_user":
                result = await fn(params.get("username", ""))
            elif method == "post_reddit":
                result = await fn(params.get("subreddit", ""), params.get("title", ""), params.get("body", ""))
            elif method == "comment_reddit":
                result = await fn(params.get("post_url", ""), params.get("text", ""))
            elif method == "dm_twitter":
                result = await fn(params.get("username", ""), params.get("text", ""))
            elif method == "send_telegram":
                result = await fn(params.get("target", params.get("group", "")), params.get("text", ""))
            elif method == "send_discord":
                result = await fn(params.get("server", params.get("channel_url", "")), params.get("text", ""))
            elif method == "post_solvr_feed":
                result = await fn(params.get("text", ""))
            elif method == "post_flippt_feed":
                result = await fn(params.get("text", ""))
            else:
                result = {"success": False, "error": f"Unknown browser method: {method}"}

            if result.get("success"):
                detail = f"{method} OK"
                # Log le contenu exact pour les tweets
                if method == "post_tweet":
                    tweet_text = params.get("text", "")
                    _log(f"  [TWEET POSTED] {tweet_text[:120]}")
                    self.memory.setdefault("tweets_posted", []).append({
                        "text": tweet_text[:280], "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "proof": result.get("proof", ""),
                    })
                    detail = f"Tweet: {tweet_text[:60]}..."
                return {"success": True, "detail": detail}
            else:
                error = result.get("error", result.get("detail", "unknown error"))
                _log(f"  [BROWSER] {method} failed: {str(error)[:100]}")
            if fallback_vps and not result.get("success"):
                _log(f"  [BROWSER] Fallback VPS...")
                return await self.vps.execute(method, "GHOST-WRITER", params, "vert")
            return {"success": False, "detail": error}
        except Exception as e:
            _log(f"  [BROWSER] {method} exception: {str(e)[:100]}")
            if fallback_vps:
                _log(f"  [BROWSER] Fallback VPS...")
                return await self.vps.execute(method, "GHOST-WRITER", params, "vert")
            return {"success": False, "detail": str(e)[:200]}

    async def _manage_conversations(self) -> dict:
        """Lit les DMs non lus sur Twitter, Telegram, Discord, Email et repond."""
        replied = 0

        # Email — lire et repondre aux emails non lus
        try:
            email_results = await process_inbox(call_local_llm)
            for er in email_results:
                if er.get("replied"):
                    replied += 1
                    _log(f"  [EMAIL] Replied to {er['from']}: {er['subject']}")
                elif er.get("skipped"):
                    _log(f"  [EMAIL] Skipped: {er['from']}")
        except Exception as e:
            _log(f"  [EMAIL] Error: {e}")

        # Twitter DMs
        try:
            dms = await browser.read_twitter_dms(10)
            _log(f"  [DM] Twitter: {len(dms)} conversations, {sum(1 for d in dms if d.get('unread'))} non lues")
            unread = [d for d in dms if d.get("unread")]
            for dm in unread[:3]:
                name = dm.get("name", "")
                if not name:
                    continue
                # Lire la conversation
                messages = await browser.read_twitter_dm_conversation(name)
                if not messages:
                    continue
                # Verifier si le dernier message est le notre (pas besoin de repondre)
                last = messages[-1] if messages else ""
                if "maxia" in last.lower() and len(messages) > 1:
                    continue  # On a deja repondu
                # Generer et envoyer la reponse
                reply = await generate_conversation_reply(messages, name, "Twitter")
                if reply:
                    result = await browser.reply_twitter_dm(name, reply)
                    if result.get("success"):
                        replied += 1
                        _log(f"  [DM] Twitter @{name}: {reply[:60]}")
                        # CRM update
                        self.memory.setdefault("contacts", []).append({
                            "target": name, "canal": "twitter_dm",
                            "ts": time.strftime("%Y-%m-%d"), "status": "replied",
                            "last_message": reply[:50],
                        })
        except Exception as e:
            _log(f"  [DM] Twitter error: {e}")

        # Telegram — lire les groupes rejoints et repondre si pertinent
        try:
            groups = self.memory.get("groups_joined", [])
            for group in groups[:2]:  # Max 2 groupes par cycle
                if "t.me" not in group and "telegram" not in group.lower():
                    continue
                group_name = group.split("/")[-1]
                messages = await browser.read_telegram_messages(group_name, 5)
                # Chercher un message ou on peut apporter de la valeur
                for msg in messages:
                    if any(kw in msg.lower() for kw in ["ai agent", "marketplace", "solana", "earn", "monetize", "no users", "no revenue"]):
                        reply = await generate_conversation_reply([msg], group_name, "Telegram")
                        if reply:
                            result = await browser.send_telegram(group_name, reply)
                            if result.get("success"):
                                replied += 1
                                _log(f"  [DM] Telegram {group_name}: {reply[:60]}")
                            break  # 1 message par groupe max
        except Exception as e:
            _log(f"  [DM] Telegram error: {e}")

        return {"success": True, "detail": f"{replied} conversations gerees"}

    async def _generate_launch_report(self) -> dict:
        """Synthetise toutes les interactions de la journee (mentions, DMs, emails, comments)
        et envoie un rapport au fondateur via Discord + sauvegarde locale."""
        # Collecter toutes les interactions
        actions = self.memory.get("actions_done", [])
        today = time.strftime("%Y-%m-%d")
        today_actions = [a for a in actions if a.get("ts", "").startswith(today)]

        # Extraire les messages recus (DMs, mentions, emails)
        contacts = self.memory.get("contacts", [])
        today_contacts = [c for c in contacts if c.get("ts", "").startswith(today)]

        # Compter par type
        tweets_posted = sum(1 for a in today_actions if a.get("action") in ("post_tweet",))
        mentions_replied = sum(1 for a in today_actions if a.get("action") == "reply_mentions")
        dms_handled = sum(1 for a in today_actions if a.get("action") == "manage_dms")
        emails_checked = sum(1 for a in today_actions if a.get("action") == "check_emails")

        # Collecter les textes des interactions pour la synthese
        interaction_texts = []
        for c in today_contacts:
            if c.get("last_message"):
                interaction_texts.append(f"[{c.get('canal', '?')}] @{c.get('target', '?')}: {c.get('last_message', '')}")

        # Generer la synthese via LLM
        summary = "Pas assez de donnees pour synthetiser."
        if interaction_texts:
            interactions_str = "\n".join(interaction_texts[-30:])
            prompt = (
                f"Tu es l'analyste de MAXIA. Voici toutes les interactions de la journee (launch day):\n\n"
                f"{interactions_str}\n\n"
                f"Stats: {len(today_actions)} actions, {len(today_contacts)} contacts, "
                f"{tweets_posted} tweets, {mentions_replied} replies, {dms_handled} DMs, {emails_checked} emails\n\n"
                f"Fais un rapport CONCIS pour le fondateur:\n"
                f"1. RESUME (3 lignes max)\n"
                f"2. CE QUE LES GENS DEMANDENT (les themes/questions recurrentes)\n"
                f"3. OPPORTUNITES (contacts chauds, partenariats potentiels)\n"
                f"4. PROBLEMES DETECTES (plaintes, bugs mentionnes)\n"
                f"5. ACTION RECOMMANDEE (1 seule, la plus importante)\n\n"
                f"En francais. Max 300 mots."
            )
            summary = await call_local_llm(prompt, max_tokens=500)

        # Sauvegarder le rapport
        report = {
            "date": today,
            "type": "launch_report",
            "stats": {
                "actions": len(today_actions),
                "contacts": len(today_contacts),
                "tweets": tweets_posted,
                "mentions_replied": mentions_replied,
                "dms": dms_handled,
                "emails": emails_checked,
            },
            "interactions": interaction_texts[-20:],
            "summary": summary,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        self.memory.setdefault("launch_reports", []).append(report)

        # Sauvegarder aussi en fichier lisible
        report_path = os.path.join(os.path.dirname(__file__), f"report_{today}.txt")
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(f"=== MAXIA LAUNCH REPORT — {today} ===\n\n")
            f.write(f"Stats: {len(today_actions)} actions | {len(today_contacts)} contacts | "
                    f"{tweets_posted} tweets | {mentions_replied} replies | {dms_handled} DMs | {emails_checked} emails\n\n")
            f.write(f"--- SYNTHESE ---\n{summary}\n\n")
            f.write(f"--- INTERACTIONS ---\n")
            for t in interaction_texts:
                f.write(f"{t}\n")
        _log(f"  [REPORT] Rapport genere: {report_path}")

        # Envoyer sur Discord
        try:
            await notify_all(
                f"RAPPORT LAUNCH — {today}",
                f"Actions: {len(today_actions)} | Contacts: {len(today_contacts)}\n\n{summary[:1500]}",
                "vert",
            )
        except Exception:
            pass

        return {"success": True, "detail": f"Rapport genere ({len(today_contacts)} contacts, {len(interaction_texts)} interactions)"}

    async def _post_to_forum(self):
        """Post R&D findings to the MAXIA AI Forum.

        Every 50 cycles (~4h), if the CEO found something interesting in R&D,
        generate a forum post and publish it via the VPS API.
        Communities: strategy, trading, dev, gpu, data, services, general.
        """
        mem = self.memory
        surf = mem.get("surf_findings", [])
        research = mem.get("research_findings", [])

        # Get unposted findings
        unposted_surf = [s for s in surf if not s.get("forum_posted")]
        unposted_research = [r for r in research if not r.get("forum_posted")]

        if not unposted_surf and not unposted_research:
            _log("[FORUM] No new R&D findings to post")
            return

        # Collect best unposted findings (max 5 of each)
        best_findings = (unposted_research[-5:] + unposted_surf[-5:])
        if not best_findings:
            return

        findings_str = "\n".join(
            f"- [{f.get('target', f.get('category', 'general'))}] {f.get('finding', f.get('analysis', ''))[:200]}"
            for f in best_findings
        )

        # Map finding categories to forum communities
        category_map = {
            "competition": "strategy",
            "opportunity": "strategy",
            "improvement": "dev",
            "API Collect": "trading",
            "defi": "trading",
            "gpu": "gpu",
            "data": "data",
            "tool": "dev",
            "agent": "services",
        }

        # Determine best community from findings
        categories = [f.get("category", "general") for f in best_findings]
        community = "general"
        for cat in categories:
            cat_lower = cat.lower() if cat else ""
            for key, comm in category_map.items():
                if key in cat_lower:
                    community = comm
                    break

        # Ask CEO to generate a forum post
        try:
            prompt = (
                f"Based on these R&D findings:\n{findings_str}\n\n"
                f"Write a forum post for AI agents on the MAXIA marketplace.\n"
                f"The post should share useful alpha, data, or insights.\n"
                f"Format: JSON with 'title' (max 120 chars) and 'body' (max 1000 chars).\n"
                f"Be specific, data-driven, helpful. No fluff.\n"
                f"Example: {{\"title\": \"New yield opportunity on Solana — 8.2% APY via Jito\", "
                f"\"body\": \"Our scanner found...\"}}"
            )
            raw = await call_ceo(
                prompt,
                system="CEO MAXIA. Write forum posts for AI agents. Data-driven, specific. Output valid JSON only.",
                max_tokens=400,
                think=False,
            )

            # Parse JSON from response
            import re as _re_forum
            json_match = _re_forum.search(r'\{[^{}]*"title"[^{}]*"body"[^{}]*\}', raw, _re_forum.DOTALL)
            if not json_match:
                # Try broader match
                json_match = _re_forum.search(r'\{.*\}', raw, _re_forum.DOTALL)
            if not json_match:
                _log(f"[FORUM] Could not parse JSON from CEO response: {raw[:100]}")
                return

            post_data = json.loads(json_match.group())
            title = post_data.get("title", "")[:200]
            body = post_data.get("body", "")[:2000]

            if not title or not body or len(title) < 10 or len(body) < 30:
                _log(f"[FORUM] Post too short, skipping: title={len(title)} body={len(body)}")
                return

            # Determine tags from findings
            tags = list(set(
                f.get("target", f.get("category", ""))[:20].lower().replace(" ", "-")
                for f in best_findings[:5]
                if f.get("target") or f.get("category")
            ))[:5]
            tags.append("ceo-rnd")

            # POST to VPS forum API
            payload = {
                "wallet": "MAXIA_CEO",
                "agent_name": "MAXIA CEO",
                "community": community,
                "title": title,
                "body": body,
                "type": "discussion",
                "tags": tags,
            }

            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"{self._base}/api/public/forum/post",
                    json=payload,
                    headers={"X-CEO-Key": CEO_API_KEY, "Content-Type": "application/json"},
                )
                if resp.status_code in (200, 201):
                    _log(f"[FORUM] Posted: {title[:80]}")
                    # Mark findings as posted
                    for f in best_findings:
                        f["forum_posted"] = True
                    _save_memory(mem)
                else:
                    _log(f"[FORUM] POST failed ({resp.status_code}): {resp.text[:100]}")

        except Exception as e:
            _log(f"[FORUM] Generation/post error: {e}")

    async def _autonomous_surf(self, analysis: str, state: dict):
        """R&D en temps mort — le CEO surfe pour ameliorer MAXIA.

        Architecture API-first (10x plus rapide) :
        - 1 cycle sur 2 : API directes (GitHub, Reddit, DeFi Llama, CoinGecko, HN) → 2-5s
        - 1 cycle sur 2 : browser-use Vision 7B pour les sites sans API (concurrents, Twitter)
        - CEO (Qwen 3 14B, think=on) analyse et produit des recommandations
        - Trouvailles stockees dans rnd_findings.md + memoire
        """
        # ═══ MODE API — 3 cycles sur 4 (rapide, fiable, 2-5s) ═══
        # Browser-use seulement 1 cycle sur 4 (pour les sites sans API : concurrents, pages custom)
        if self._cycle % 4 != 0:
            try:
                from api_surf import collect_all_api_data, format_api_data_for_ceo
                _log("[SURF/API] Collecte via API directes...")
                api_data = await collect_all_api_data()
                formatted = format_api_data_for_ceo(api_data)
                if formatted and len(formatted) > 50:
                    _log(f"[SURF/API] {len(formatted)} chars collectes — analyse CEO...")
                    # Truncate a 1500 chars pour eviter le timeout think
                    api_prompt = (
                        f"R&D Data:\n{formatted[:1500]}\n\n"
                        f"For MAXIA (AI marketplace, 107 tokens, 14 chains, GPU at cost):\n"
                        f"1. Top 3 relevant items\n"
                        f"2. Prospects who need MAXIA\n"
                        f"3. Features to add\n"
                        f"4. Competitor moves\n"
                        f"5. One action for Alexis\n"
                        f"Be specific. Max 150 words."
                    )
                    # Essayer think=off d'abord (rapide), think=on seulement pour les cycles strategiques
                    use_think = (self._cycle % 10 == 0)
                    finding = await call_ceo(
                        api_prompt,
                        system="CEO MAXIA. Brief R&D analyst. English.",
                        max_tokens=250,
                        think=use_think,
                    )
                    # Fallback executeur si CEO vide
                    if not finding or len(finding) < 20:
                        finding = await call_executor(
                            api_prompt,
                            system="Brief R&D analyst. English.",
                            max_tokens=200,
                        )
                    if finding and len(finding) > 20:
                        _log(f"[SURF/API] Analyse: {finding[:150]}")
                        self.memory.setdefault("surf_findings", []).append({
                            "target": "API Collect",
                            "finding": finding[:500],
                            "raw_length": len(formatted),
                            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            "cycle": self._cycle,
                            "method": "api",
                        })
                        if len(self.memory.get("surf_findings", [])) > 50:
                            self.memory["surf_findings"] = self.memory["surf_findings"][-50:]
                        _append_rnd_finding(finding[:500], category="API Collect")
                        if any(kw in finding.lower() for kw in ["prospect", "need", "could use", "looking for"]):
                            self.memory.setdefault("prospects_from_surf", []).append({
                                "source": "API Collect", "finding": finding[:300],
                                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            })
                        if any(kw in finding.lower() for kw in ["add", "integrate", "improvement", "missing", "should"]):
                            self.memory.setdefault("improvement_ideas", []).append({
                                "source": "API Collect", "idea": finding[:300],
                                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            })
                    else:
                        _log("[SURF/API] Analyse vide")
                else:
                    _log("[SURF/API] Pas de donnees collectees")
            except Exception as e:
                _log(f"[SURF/API] Erreur: {e}")
            return

        # ═══ MODE BROWSER — 1 cycle sur 2 (pour sites sans API) ═══
        surf_targets = [
            # ── GitHub trending ──
            {
                "name": "GitHub Trending",
                "task": "Go to https://github.com/trending?spoken_language_code=en and extract the names and descriptions of the top 5 trending repositories. Mark any related to AI, crypto, blockchain, or agents.",
            },
            {
                "name": "GitHub Python AI",
                "task": "Go to https://github.com/trending/python?since=daily and extract the top 5 trending Python repos. Mark any related to AI agents, FastAPI, crypto, or Web3.",
            },
            {
                "name": "GitHub TypeScript AI",
                "task": "Go to https://github.com/trending/typescript?since=daily and extract the top 5 trending TypeScript repos. Mark any related to AI agents, Web3, or crypto.",
            },
            # ── Concurrents directs ──
            {
                "name": "Virtuals Protocol",
                "task": "Go to https://github.com/Virtual-Protocol and extract the repo names, descriptions, and recent activity. Note new features, releases, or changes.",
            },
            {
                "name": "Fetch.ai Repos",
                "task": "Go to https://github.com/fetchai and extract the repo names and recent activity. Note new agent frameworks, protocols, or marketplace features.",
            },
            {
                "name": "ElizaOS Issues",
                "task": "Go to https://github.com/elizaOS/eliza/issues?q=is%3Aissue+is%3Aopen+sort%3Acreated-desc and extract the titles of the 5 most recent open issues. Note any about marketplace, payments, swaps, or multi-chain.",
            },
            {
                "name": "ElizaOS Releases",
                "task": "Go to https://github.com/elizaOS/eliza/releases and extract the latest release notes. Note new features, plugins, or integrations.",
            },
            {
                "name": "Olas/Autonolas",
                "task": "Go to https://github.com/valory-xyz and extract the repo names and descriptions. Note new agent tools, mech updates, or protocol changes.",
            },
            {
                "name": "GOAT SDK",
                "task": "Go to https://github.com/goat-sdk/goat and extract the README summary and recent issues. Note new chain support, plugins, or integrations.",
            },
            {
                "name": "MyShell AI",
                "task": "Go to https://github.com/myshell-ai and extract the repo names and descriptions. Note AI agent marketplace features or new tools.",
            },
            # ── Hacker News ──
            {
                "name": "HN Front Page",
                "task": "Go to https://news.ycombinator.com and extract the titles of the top 10 posts. Mark any about AI, LLM, agents, crypto, GPU, or developer tools.",
            },
            {
                "name": "HN Show",
                "task": "Go to https://news.ycombinator.com/shownew and extract the titles of the top 10 Show HN posts. Mark any about AI, developer tools, crypto, or marketplace.",
            },
            # ── Reddit ──
            {
                "name": "Reddit LocalLLaMA",
                "task": "Go to https://www.reddit.com/r/LocalLLaMA/new/ and extract the titles of the 5 most recent posts. Note any about GPU, cost, hosting, or monetization.",
            },
            {
                "name": "Reddit CryptoDev",
                "task": "Go to https://www.reddit.com/r/CryptoDev/new/ and extract the titles of the 5 most recent posts. Note any about agent monetization, token swaps, or APIs.",
            },
            {
                "name": "Reddit SolanaDev",
                "task": "Go to https://www.reddit.com/r/solanadev/new/ and extract the titles of the 5 most recent posts. Note any about AI agents, DeFi, or developer tools.",
            },
            {
                "name": "Reddit DeFi",
                "task": "Go to https://www.reddit.com/r/defi/new/ and extract the titles of the 5 most recent posts. Note yield opportunities, new protocols, or pain points.",
            },
            # ── DeFi / Tokens ──
            {
                "name": "DeFi Yields Solana",
                "task": "Go to https://defillama.com/yields?chain=Solana and extract the top 5 yields visible. For each: protocol name, APY, and token.",
            },
            {
                "name": "DeFi Yields Base",
                "task": "Go to https://defillama.com/yields?chain=Base and extract the top 5 yields visible. For each: protocol name, APY, and token.",
            },
            {
                "name": "DexScreener Trending",
                "task": "Go to https://dexscreener.com/trending and extract the top 5 trending tokens. For each: name, chain, volume 24h, price change.",
            },
            # ── AI Agent ecosysteme ──
            {
                "name": "Awesome AI Agents",
                "task": "Go to https://github.com/e2b-dev/awesome-ai-agents and extract the 10 most recently added entries. Note any that could be MAXIA customers.",
            },
            {
                "name": "LangChain Updates",
                "task": "Go to https://github.com/langchain-ai/langchain/releases and extract the latest release notes. Note new integrations, tools, or breaking changes.",
            },
            {
                "name": "CrewAI Updates",
                "task": "Go to https://github.com/crewAIInc/crewAI/releases and extract the latest release notes. Note new features for multi-agent systems.",
            },
        ]

        target = surf_targets[self._cycle % len(surf_targets)]
        _log(f"[SURF] {target['name']}...")

        # Extraire l'URL de la tache
        task_text = target["task"]
        url = ""
        for part in task_text.split():
            if part.startswith("http"):
                url = part
                break

        # ETAPE 1 : Le petit modele (2.5-VL 7B) navigue et extrait le texte
        # Instruction ultra-simple : navigue + utilise l'action "extract" (pas scroll)
        result = await browser._browser_use_task(
            f"Navigate to {url} — once the page loads, use the extract action to get "
            f"ONLY the main heading, first 3 items or paragraphs (max 300 words). "
            f"Do NOT scroll. Do NOT extract the full page. Keep it SHORT. "
            f"Call done immediately with the brief extracted text.",
            max_steps=3,
        )

        raw_text = str(result.get("result", ""))[:2000] if result.get("success") else ""

        if not raw_text or len(raw_text) < 20:
            _log(f"[SURF] {target['name']}: extraction vide ou trop courte")
            return

        _log(f"[SURF] {target['name']}: {len(raw_text)} chars extraits — analyse CEO 14B...")

        # ETAPE 2 : Le CEO (Qwen 3 14B, think=on) analyse en profondeur
        analysis_prompt = (
            f"Page: {target['name']}\n"
            f"Content extracted:\n{raw_text[:1500]}\n\n"
            f"Tu es CEO de MAXIA (AI marketplace, 107 tokens, 7 chains, GPU rental, "
            f"reverse auctions, leaderboard, AI disputes, 14 chains, USDC escrow).\n"
            f"Analyse cette page pour trouver des ameliorations pour MAXIA:\n"
            f"1. RELEVANT ITEMS: 3 elements les plus pertinents (projets, posts, repos)\n"
            f"2. PROSPECTS: quelqu'un qui pourrait utiliser MAXIA? (username + besoin)\n"
            f"3. AMELIORATIONS: tokens/features/protocoles a ajouter a MAXIA?\n"
            f"4. CONCURRENCE: concurrent mentionne? Que font-ils qu'on ne fait pas?\n"
            f"5. RECOMMANDATION: 1 action concrete pour Alexis\n"
            f"Sois specifique. Noms, liens, chiffres. Max 200 mots. English."
        )
        finding = await call_ceo(
            analysis_prompt,
            system="CEO MAXIA. Strategic R&D analyst. Brief, specific, data-driven. English only.",
            max_tokens=300,
            think=True,
        )

        if finding and len(finding) > 20:
            _log(f"[SURF] Analyse: {finding[:150]}")

            self.memory.setdefault("surf_findings", []).append({
                "target": target["name"],
                "finding": finding[:500],
                "raw_length": len(raw_text),
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "cycle": self._cycle,
            })
            if len(self.memory.get("surf_findings", [])) > 50:
                self.memory["surf_findings"] = self.memory["surf_findings"][-50:]

            # Ecrire dans rnd_findings.md
            _append_rnd_finding(finding[:500], category=target["name"])

            # Detecter les prospects dans l'analyse du CEO
            if any(kw in finding.lower() for kw in ["prospect", "need", "could use", "looking for", "pain", "wants"]):
                self.memory.setdefault("prospects_from_surf", []).append({
                    "source": target["name"],
                    "finding": finding[:300],
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                })
                _log(f"[SURF] Prospect detecte via {target['name']}")

            # Detecter les ameliorations pour MAXIA
            if any(kw in finding.lower() for kw in ["add", "integrate", "improvement", "missing", "should", "recommend"]):
                self.memory.setdefault("improvement_ideas", []).append({
                    "source": target["name"],
                    "idea": finding[:300],
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                })
                _log(f"[R&D] Amelioration detectee via {target['name']}")
        else:
            _log(f"[SURF] {target['name']}: analyse vide")

    async def _deep_research(self):
        """R&D profond — le CEO 14B (think=on) etudie la concurrence, les nouveaux tools,
        et les opportunites d'amelioration pour MAXIA. Ecrit dans rnd_findings.md.

        Architecture : Vision (7B) extrait le texte, CEO (14B think=on) analyse."""
        research_tasks = [
            # ── Concurrence directe ──
            {"name": "Olas Changelog", "url": "https://github.com/valory-xyz/mech/releases", "category": "competition"},
            {"name": "Morpheus Updates", "url": "https://github.com/MorpheusAIs/Morpheus-Lumerin-Node/releases", "category": "competition"},
            {"name": "Virtuals Game", "url": "https://github.com/Virtual-Protocol/virtuals-python/releases", "category": "competition"},
            {"name": "Fetch.ai uAgents", "url": "https://github.com/fetchai/uAgents/releases", "category": "competition"},
            {"name": "SingularityNET", "url": "https://github.com/singnet/snet-daemon/releases", "category": "competition"},
            {"name": "GOAT SDK Updates", "url": "https://github.com/goat-sdk/goat/releases", "category": "competition"},
            # ── Protocoles et standards ──
            {"name": "A2A Protocol", "url": "https://github.com/google/A2A", "category": "improvement"},
            {"name": "MCP Spec", "url": "https://github.com/modelcontextprotocol/specification/releases", "category": "improvement"},
            {"name": "0x Protocol", "url": "https://github.com/0xProject/protocol/releases", "category": "improvement"},
            {"name": "Jupiter Updates", "url": "https://github.com/jup-ag/jupiter-quote-api-node/releases", "category": "improvement"},
            # ── Outils et frameworks ──
            {"name": "browser-use", "url": "https://github.com/browser-use/browser-use/releases", "category": "improvement"},
            {"name": "FastAPI Ecosystem", "url": "https://github.com/topics/fastapi?o=desc&s=updated", "category": "improvement"},
            {"name": "RunPod Updates", "url": "https://github.com/runpod/runpod-python/releases", "category": "improvement"},
            {"name": "Unsloth Updates", "url": "https://github.com/unslothai/unsloth/releases", "category": "improvement"},
            {"name": "Ollama Updates", "url": "https://github.com/ollama/ollama/releases", "category": "improvement"},
            # ── Opportunites par chain ──
            {"name": "Solana Dev Repos", "url": "https://github.com/topics/solana?o=desc&s=updated", "category": "opportunity"},
            {"name": "Base Dev Repos", "url": "https://github.com/topics/base-chain?o=desc&s=updated", "category": "opportunity"},
            {"name": "Arbitrum Ecosystem", "url": "https://github.com/topics/arbitrum?o=desc&s=updated", "category": "opportunity"},
            {"name": "TON Dev Repos", "url": "https://github.com/topics/ton?o=desc&s=updated", "category": "opportunity"},
            {"name": "SUI Dev Repos", "url": "https://github.com/topics/sui?o=desc&s=updated", "category": "opportunity"},
            # ── Communautes ──
            {"name": "Reddit SolanaDevs", "url": "https://www.reddit.com/r/solanadev/new/", "category": "opportunity"},
            {"name": "Reddit Ethereum", "url": "https://www.reddit.com/r/ethereum/new/", "category": "opportunity"},
            {"name": "Reddit AIAgents", "url": "https://www.reddit.com/r/AIAgents/new/", "category": "opportunity"},
            # ── Nouveaux tokens et DeFi ──
            {"name": "CoinGecko New", "url": "https://www.coingecko.com/en/new-cryptocurrencies", "category": "opportunity"},
            {"name": "DeFi Llama TVL", "url": "https://defillama.com/", "category": "opportunity"},
        ]

        target = research_tasks[self._cycle % len(research_tasks)]
        _log(f"[RESEARCH] {target['name']} ({target['category']})...")

        # ETAPE 1 : Le petit (7B) extrait le texte brut
        result = await browser._browser_use_task(
            f"Go to {target['url']} and extract ONLY the title, main heading, "
            f"and first 3 items or paragraphs (max 300 words). Keep it SHORT. "
            f"Do NOT extract the full page. Call done with the brief text.",
            max_steps=3,
        )
        raw_text = str(result.get("result", ""))[:2000] if result.get("success") else ""

        if not raw_text or len(raw_text) < 20:
            _log(f"[RESEARCH] {target['name']}: extraction vide")
            return

        # ETAPE 2 : Le CEO 14B (think=on) analyse en profondeur
        _log(f"[RESEARCH] {len(raw_text)} chars → analyse CEO 14B...")
        analysis = await call_ceo(
            f"Deep research — {target['name']} ({target['category']}):\n"
            f"Page content:\n{raw_text[:1500]}\n\n"
            f"Tu es CEO de MAXIA (AI marketplace, 107 tokens, 7 chains, "
            f"GPU at cost, reverse auctions, leaderboard, AI disputes, business marketplace).\n"
            f"Category: {target['category']}\n\n"
            f"Answer:\n"
            f"1. What's NEW here? (releases, features, changes)\n"
            f"2. How does this affect MAXIA? (threat, opportunity, neutral)\n"
            f"3. ACTION ITEM: one concrete thing MAXIA should do based on this\n"
            f"4. IMPROVEMENT: something specific to add/change in MAXIA?\n"
            f"Be specific. Max 150 words. English only.",
            system="CEO MAXIA. Expert competitive intelligence. Brief, actionable. English.",
            max_tokens=300,
            think=True,
        )

        if analysis and len(analysis) > 20:
            _log(f"[RESEARCH] {analysis[:150]}")
            self.memory.setdefault("research_findings", []).append({
                "target": target["name"],
                "category": target["category"],
                "finding": analysis[:500],
                "raw_length": len(raw_text),
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "cycle": self._cycle,
            })
            if len(self.memory.get("research_findings", [])) > 100:
                self.memory["research_findings"] = self.memory["research_findings"][-100:]

            # Ecrire dans rnd_findings.md
            _append_rnd_finding(analysis[:500], category=f"{target['category']} — {target['name']}")
        else:
            _log(f"[RESEARCH] {target['name']}: analyse vide")

    async def _post_feature_of_day(self) -> dict:
        """Poste le tweet Feature of the Day — 1 feature differente chaque jour."""
        # Calculer l'index du jour (cycle dans les 14 features)
        from datetime import date
        day_index = date.today().toordinal() % len(FEATURE_OF_THE_DAY)

        # Verifier si deja poste aujourd'hui
        today = time.strftime("%Y-%m-%d")
        posted_today = [t for t in self.memory.get("tweets_posted", [])
                        if t.get("ts", "").startswith(today) and "Feature of the Day" in t.get("text", "")]
        if posted_today:
            _log("[TWEET] Feature of the Day deja poste aujourd'hui — skip")
            return {"success": True, "detail": "Feature of the Day already posted today"}

        tweet_text = FEATURE_OF_THE_DAY[day_index]
        _log(f"[TWEET] Feature of the Day #{day_index + 1}: {tweet_text[:60]}...")

        result = await self._do_browser("tweet", {"text": tweet_text})
        if result.get("success"):
            self.memory.setdefault("tweets_posted", []).append({
                "text": f"Feature of the Day: {tweet_text[:40]}", "ts": today,
            })
        return result

    async def _read_forum(self) -> dict:
        """Lit le forum MAXIA et remonte les remarques/suggestions au fondateur."""
        _log("[FORUM] Lecture du forum MAXIA...")
        try:
            result = await self.vps.get("/api/public/forum/posts?sort=hot&limit=10")
            posts = result.get("posts", []) if isinstance(result, dict) else []

            if not posts:
                _log("[FORUM] Aucun post")
                return {"success": True, "detail": "Forum vide"}

            # Sauvegarder en memoire pour le rapport quotidien
            today = time.strftime("%Y-%m-%d")
            self.memory.setdefault("forum_digest", []).append({
                "ts": today,
                "count": len(posts),
                "posts": [{"title": p.get("title", "")[:80], "author": p.get("author", ""),
                           "votes": p.get("votes", 0), "replies": p.get("reply_count", 0)}
                          for p in posts[:5]],
            })

            _log(f"[FORUM] {len(posts)} posts lus, top: {posts[0].get('title', '')[:50] if posts else 'none'}")
            return {"success": True, "detail": f"{len(posts)} forum posts read"}
        except Exception as e:
            _log(f"[FORUM] Erreur lecture: {e}")
            return {"success": False, "error": str(e)}

    async def _daily_report(self):
        """Rapport quotidien — compile tout ce que le CEO a trouve aujourd'hui.
        Envoye sur Telegram + sauvegarde en fichier local.
        Appele 1x par jour a 20h UTC."""

        # Lire le forum avant de generer le rapport
        await self._read_forum()

        _log("[REPORT] Generation du rapport quotidien...")

        today = time.strftime("%Y-%m-%d")
        mem = self.memory

        # Collecter les donnees du jour
        surf = [s for s in mem.get("surf_findings", []) if s.get("ts", "").startswith(today)]
        research = [r for r in mem.get("research_findings", []) if r.get("ts", "").startswith(today)]
        prospects = [p for p in mem.get("prospects_from_surf", []) if p.get("ts", "").startswith(today)]
        actions = [a for a in mem.get("actions_done", []) if a.get("ts", "").startswith(today)]
        convos = [c for c in mem.get("conversations", []) if c.get("ts", "").startswith(today)]

        # Stats du jour
        ok_count = sum(1 for a in actions if a.get("success"))
        fail_count = sum(1 for a in actions if not a.get("success"))

        # Categoriser les recherches
        competition = [r for r in research if r.get("category") == "competition"]
        opportunities = [r for r in research if r.get("category") == "opportunity"]
        improvements = [r for r in research if r.get("category") == "improvement"]

        # Construire le rapport
        report_lines = [
            f"MAXIA Daily Report — {today}",
            f"Cycles: {self._cycle} | Actions: {ok_count} OK, {fail_count} FAIL | Conversations: {len(convos)}",
            "",
        ]

        # Concurrence
        report_lines.append("CONCURRENCE:")
        if competition:
            for r in competition[-5:]:
                report_lines.append(f"  - {r['target']}: {r['finding'][:100]}")
        else:
            report_lines.append("  (rien de nouveau)")
        report_lines.append("")

        # Opportunites
        report_lines.append("OPPORTUNITES:")
        if opportunities:
            for r in opportunities[-5:]:
                report_lines.append(f"  - {r['target']}: {r['finding'][:100]}")
        else:
            report_lines.append("  (rien de nouveau)")
        report_lines.append("")

        # Ameliorations
        report_lines.append("AMELIORATIONS POSSIBLES:")
        if improvements:
            for r in improvements[-3:]:
                report_lines.append(f"  - {r['target']}: {r['finding'][:100]}")
        else:
            report_lines.append("  (rien de nouveau)")
        report_lines.append("")

        # Prospects
        report_lines.append(f"PROSPECTS ({len(prospects)}):")
        if prospects:
            for p in prospects[-5:]:
                report_lines.append(f"  - {p['source']}: {p['finding'][:80]}")
        else:
            report_lines.append("  (aucun prospect)")
        report_lines.append("")

        # Surf stats
        report_lines.append(f"R&D: {len(surf)} pages analysees, {len(research)} recherches profondes")
        report_lines.append("")

        # Ameliorations detectees
        ideas = [i for i in self.memory.get("improvement_ideas", []) if i.get("ts", "").startswith(today)]
        if ideas:
            report_lines.append(f"AMELIORATIONS DETECTEES ({len(ideas)}):")
            for i in ideas[-3:]:
                report_lines.append(f"  - {i['source']}: {i['idea'][:80]}")
            report_lines.append("")

        # Forum digest
        forum_entries = [f for f in self.memory.get("forum_digest", []) if f.get("ts", "").startswith(today)]
        if forum_entries:
            latest = forum_entries[-1]
            report_lines.append(f"FORUM ({latest.get('count', 0)} posts):")
            for p in latest.get("posts", [])[:5]:
                report_lines.append(f"  - {p['title'][:60]} (votes={p['votes']}, replies={p['replies']})")
            report_lines.append("")

        # Scores plateformes
        if self.platform_scores:
            report_lines.append("SCORES PLATEFORMES:")
            for p, s in sorted(self.platform_scores.items(), key=lambda x: x[1].get("score", 0), reverse=True):
                report_lines.append(f"  {p}: {s.get('score', 5)}/10 {s.get('trend', '=')}")
            report_lines.append("")

        # Scout VPS — contacts agents on-chain
        scout_contacts = [c for c in self.memory.get("scout_contacts", []) if c.get("ts", "").startswith(today)]
        if scout_contacts:
            report_lines.append(f"SCOUT ON-CHAIN ({len(scout_contacts)} contacts):")
            for c in scout_contacts[-5:]:
                report_lines.append(f"  - {c['address'][:10]}... ({c['chain']}): {c.get('response', 'pending')}")
            report_lines.append("")

        # Demander au LLM de donner 3 recommandations
        all_findings = "\n".join(f"- {r['target']}: {r['finding'][:80]}" for r in (research + surf)[-15:])
        if all_findings:
            reco = await call_ceo(
                f"Based on today's R&D research:\n{all_findings}\n\n"
                f"Platform scores: {json.dumps(self.platform_scores, default=str)[:200]}\n\n"
                f"Give exactly 3 concrete recommendations for tomorrow. "
                f"Format: numbered list. Max 50 chars each. English only.",
                system="CEO MAXIA. Brief strategic advisor. 3 bullet points only.",
                max_tokens=100,
                think=True,
            )
            if reco and len(reco) > 10:
                report_lines.append("RECOMMANDATIONS:")
                for line in reco.strip().split("\n")[:3]:
                    line = line.strip().lstrip("0123456789.-) ")
                    if line:
                        report_lines.append(f"  {line[:80]}")
                report_lines.append("")

        # Strategie
        strategy = mem.get("current_strategy", {})
        if strategy:
            report_lines.append(f"STRATEGIE: focus={strategy.get('focus', '?')[:60]}, platform={strategy.get('best_platform', '?')}")

        report_text = "\n".join(report_lines)
        _log(f"[REPORT] Rapport genere ({len(report_text)} chars)")

        # Sauvegarder en fichier local
        report_file = os.path.join(os.path.dirname(__file__), f"report_{today}.txt")
        try:
            with open(report_file, "w", encoding="utf-8") as f:
                f.write(report_text)
            _log(f"[REPORT] Sauvegarde: {report_file}")
        except Exception as e:
            _log(f"[REPORT] Erreur sauvegarde: {e}")

        # Envoyer sur Telegram
        try:
            await notify_all(
                f"Daily Report {today}",
                report_text,
                "vert",
            )
            _log("[REPORT] Envoye sur Telegram")
        except Exception as e:
            _log(f"[REPORT] Erreur Telegram: {e}")

        # Stocker dans la memoire
        mem.setdefault("daily_reports", []).append({
            "date": today,
            "cycle": self._cycle,
            "surf_count": len(surf),
            "research_count": len(research),
            "prospects_count": len(prospects),
            "actions_ok": ok_count,
            "actions_fail": fail_count,
        })
        if len(mem.get("daily_reports", [])) > 30:
            mem["daily_reports"] = mem["daily_reports"][-30:]

    async def _propose_features(self):
        """Systeme de proposition de features — toutes les 200 cycles (~16h).
        Analyse les improvement_ideas et research_findings en memoire,
        synthetise avec le CEO (think=True), ecrit dans feature_proposals.md,
        et envoie un resume sur Telegram.
        Chaque proposition : title, description, why, priority (P0-P3), effort (S/M/L)."""
        _log("[FEATURES] Generation de propositions de features...")

        mem = self.memory

        # ── Collecter les donnees brutes ──
        ideas = mem.get("improvement_ideas", [])[-30:]
        research = mem.get("research_findings", [])[-30:]
        surf = mem.get("surf_findings", [])[-20:]
        actions = mem.get("actions_done", [])[-50:]
        conversations = mem.get("conversations", [])[-20:]

        # Pas assez de donnees pour proposer
        if len(ideas) + len(research) < 3:
            _log("[FEATURES] Pas assez de donnees (< 3 ideas+research) — skip")
            return

        # ── Preparer le contexte pour le LLM ──
        ideas_str = "\n".join(
            f"- [{i.get('source', '?')}] {i.get('idea', i.get('finding', ''))[:120]}"
            for i in ideas[-15:]
        )
        research_str = "\n".join(
            f"- [{r.get('category', '?')}] {r.get('target', '?')}: {r.get('finding', '')[:120]}"
            for r in research[-15:]
        )
        # Extraire les patterns de conversations (besoins utilisateurs)
        user_needs = []
        for c in conversations[-15:]:
            msg = c.get("message", c.get("summary", ""))
            if any(kw in msg.lower() for kw in ["need", "want", "wish", "missing", "feature", "add", "support"]):
                user_needs.append(f"- {c.get('user', '?')}: {msg[:100]}")
        needs_str = "\n".join(user_needs[-5:]) if user_needs else "(aucun besoin explicite detecte)"

        # Extraire les echecs recurrents (opportunites d'amelioration)
        failures = [a for a in actions if not a.get("success")]
        failure_patterns = {}
        for f in failures:
            act = f.get("action", "unknown")
            failure_patterns[act] = failure_patterns.get(act, 0) + 1
        top_failures = sorted(failure_patterns.items(), key=lambda x: x[1], reverse=True)[:5]
        failures_str = "\n".join(f"- {act}: {count} echecs" for act, count in top_failures) if top_failures else "(aucun echec recurrent)"

        # Propositions precedentes (pour eviter les doublons)
        prev_proposals = mem.get("feature_proposals", [])[-10:]
        prev_titles = [p.get("title", "") for p in prev_proposals]
        prev_str = ", ".join(prev_titles[-5:]) if prev_titles else "(aucune)"

        # ── Demander au CEO de synthetiser ──
        prompt = (
            f"MAXIA Feature Proposal System — Cycle #{self._cycle}\n\n"
            f"=== IMPROVEMENT IDEAS ({len(ideas)}) ===\n{ideas_str}\n\n"
            f"=== R&D FINDINGS ({len(research)}) ===\n{research_str}\n\n"
            f"=== USER NEEDS ===\n{needs_str}\n\n"
            f"=== RECURRING FAILURES ===\n{failures_str}\n\n"
            f"=== ALREADY PROPOSED (avoid duplicates) ===\n{prev_str}\n\n"
            f"Based on ALL this data, propose exactly 3-5 NEW concrete features for MAXIA.\n"
            f"Each feature MUST be based on real data above (cite which idea/finding inspired it).\n\n"
            f"Format STRICTLY as JSON array:\n"
            f'[{{"title": "short name", "description": "what it does (2-3 sentences)", '
            f'"why": "which data point(s) justify this (cite specific findings)", '
            f'"priority": "P0/P1/P2/P3", "effort": "S/M/L"}}]\n\n'
            f"Priority guide: P0=critical blocker, P1=high impact, P2=nice to have, P3=future\n"
            f"Effort guide: S=<1 day, M=1-3 days, L=1+ week\n"
            f"Only real, actionable features. No vague ideas."
        )

        response = await call_ceo(
            prompt,
            system="You are MAXIA's product manager. Analyze R&D data and propose concrete features. Output ONLY valid JSON array.",
            max_tokens=800,
            think=True,
        )

        if not response or len(response) < 20:
            _log("[FEATURES] Reponse LLM vide ou trop courte — skip")
            return

        # ── Parser les propositions ──
        proposals = []
        try:
            # Nettoyer la reponse (enlever markdown, texte autour du JSON)
            clean = response.strip()
            for prefix in ["```json", "```"]:
                if clean.startswith(prefix):
                    clean = clean[len(prefix):]
            if clean.endswith("```"):
                clean = clean[:-3]
            clean = clean.strip()
            # Trouver le JSON array
            start = clean.index("[")
            end = clean.rindex("]") + 1
            proposals = json.loads(clean[start:end])
        except (ValueError, json.JSONDecodeError) as e:
            _log(f"[FEATURES] JSON parse error: {e}")
            return

        if not proposals or not isinstance(proposals, list):
            _log("[FEATURES] Aucune proposition valide")
            return

        # Valider et nettoyer les propositions
        valid_proposals = []
        valid_priorities = {"P0", "P1", "P2", "P3"}
        valid_efforts = {"S", "M", "L"}
        for p in proposals[:5]:  # Max 5
            if not isinstance(p, dict):
                continue
            title = p.get("title", "").strip()
            if not title or title in prev_titles:
                continue  # Skip doublons
            proposal = {
                "title": title[:80],
                "description": p.get("description", "")[:300],
                "why": p.get("why", "")[:200],
                "priority": p.get("priority", "P2") if p.get("priority") in valid_priorities else "P2",
                "effort": p.get("effort", "M") if p.get("effort") in valid_efforts else "M",
                "cycle": self._cycle,
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "status": "proposed",
            }
            valid_proposals.append(proposal)

        if not valid_proposals:
            _log("[FEATURES] Aucune proposition valide apres filtrage")
            return

        _log(f"[FEATURES] {len(valid_proposals)} propositions generees")

        # ── Sauvegarder en memoire ──
        mem.setdefault("feature_proposals", []).extend(valid_proposals)
        if len(mem.get("feature_proposals", [])) > 50:
            mem["feature_proposals"] = mem["feature_proposals"][-50:]

        # ── Ecrire dans feature_proposals.md ──
        proposals_file = os.path.join(os.path.dirname(__file__), "feature_proposals.md")
        try:
            lines = [f"# MAXIA Feature Proposals\n"]
            lines.append(f"Genere automatiquement par le CEO local — Cycle #{self._cycle}\n")
            lines.append(f"Derniere mise a jour : {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")

            # Grouper par priorite
            for prio in ["P0", "P1", "P2", "P3"]:
                prio_proposals = [p for p in mem.get("feature_proposals", []) if p.get("priority") == prio]
                if prio_proposals:
                    lines.append(f"## {prio}\n")
                    for p in prio_proposals:
                        status_icon = {"proposed": "[?]", "approved": "[OK]", "rejected": "[X]", "done": "[DONE]"}.get(p.get("status", ""), "[?]")
                        lines.append(f"### {status_icon} {p['title']} (effort: {p.get('effort', '?')})\n")
                        lines.append(f"{p.get('description', '')}\n")
                        lines.append(f"**Why:** {p.get('why', '')}\n")
                        lines.append(f"*Cycle {p.get('cycle', '?')} — {p.get('ts', '?')}*\n\n")

            with open(proposals_file, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            _log(f"[FEATURES] Sauvegarde: {proposals_file}")
        except Exception as e:
            _log(f"[FEATURES] Erreur ecriture fichier: {e}")

        # ── Envoyer resume sur Telegram ──
        try:
            summary_lines = [f"Feature Proposals (cycle #{self._cycle})"]
            for p in valid_proposals:
                summary_lines.append(
                    f"\n{p['priority']} [{p['effort']}] {p['title']}"
                    f"\n  {p['description'][:100]}"
                )
            summary_text = "\n".join(summary_lines)

            await notify_all(
                f"Feature Proposals ({len(valid_proposals)})",
                summary_text,
                "vert",
            )
            _log("[FEATURES] Resume envoye sur Telegram")
        except Exception as e:
            _log(f"[FEATURES] Erreur Telegram: {e}")

    async def _generate_video_scripts(self):
        """Genere 3 scripts video courts (30s) pour TikTok/YouTube Shorts.
        Appele toutes les 100 cycles (~8h). Utilise call_ceo(think=True).
        Format: Hook (3s) -> Problem (7s) -> Solution (10s) -> Demo (7s) -> CTA (3s).
        Sauvegarde dans local_ceo/video_scripts.md."""
        _log("[VIDEO] Generation de 3 scripts video (CEO 14B think=on)...")

        mem = self.memory

        # ── Collecter les trouvailles recentes pour inspirer les scripts ──
        research = mem.get("research_findings", [])[-15:]
        ideas = mem.get("improvement_ideas", [])[-10:]
        surf = mem.get("surf_findings", [])[-10:]
        engagement = mem.get("engagement_stats", [])[-5:]

        # Construire le contexte des trouvailles recentes
        findings_lines = []
        for r in research[-8:]:
            findings_lines.append(
                f"- [{r.get('category', '?')}] {r.get('target', '?')}: {r.get('finding', '')[:120]}"
            )
        for i in ideas[-5:]:
            findings_lines.append(
                f"- [idea] {i.get('idea', i.get('finding', ''))[:120]}"
            )
        for s in surf[-5:]:
            findings_lines.append(
                f"- [surf] {s.get('target', '?')}: {s.get('finding', '')[:120]}"
            )
        recent_findings = "\n".join(findings_lines) if findings_lines else "(no recent findings)"

        # ── Prompt CEO ──
        prompt = (
            f"Based on MAXIA features and recent trends:\n"
            f"{recent_findings}\n\n"
            f"Write 3 short video scripts (30 seconds each) for TikTok/YouTube Shorts.\n"
            f"Target audience: AI developers and crypto traders.\n\n"
            f"Format for each:\n"
            f"TITLE: (catchy, max 10 words)\n"
            f"HOOK (0-3s): (attention grabber, question or shocking statement)\n"
            f"PROBLEM (3-10s): (pain point the viewer relates to)\n"
            f"SOLUTION (10-20s): (how MAXIA solves it, with specific features)\n"
            f"CTA (20-30s): (call to action with maxiaworld.app)\n\n"
            f"Rules:\n"
            f"- Each script max 80 words total\n"
            f"- Technical but accessible\n"
            f"- No hype words\n"
            f"- Include specific numbers (107 tokens, 14 chains, etc.)\n"
            f"- NEVER mention revenue or client numbers\n\n"
            f"Separate scripts with ===\n"
        )

        response = await call_ceo(
            prompt,
            system=(
                "You are a short-form video scriptwriter for a technical Web3 product. "
                "Write concise, punchy scripts that grab attention in 3 seconds. "
                "Output ONLY the 3 scripts separated by ===. No commentary."
            ),
            max_tokens=800,
            think=True,
        )

        if not response or len(response) < 50:
            _log("[VIDEO] Reponse LLM vide ou trop courte — skip")
            return

        # ── Sauvegarder dans video_scripts.md ──
        scripts_file = os.path.join(os.path.dirname(__file__), "video_scripts.md")
        try:
            content = (
                f"# MAXIA Video Scripts\n\n"
                f"Generated by CEO Local — Cycle #{self._cycle}\n"
                f"Last updated: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                f"---\n\n"
                f"{response.strip()}\n\n"
                f"---\n\n"
                f"*Target: TikTok / YouTube Shorts (30s each)*\n"
                f"*Audience: AI developers + crypto traders*\n"
            )

            # Append to existing file if it exists (keep history)
            existing = ""
            if os.path.exists(scripts_file):
                try:
                    with open(scripts_file, "r", encoding="utf-8") as f:
                        existing = f.read()
                except Exception:
                    pass

            # Keep only latest + 2 previous batches to avoid growing forever
            sections = existing.split("# MAXIA Video Scripts")
            if len(sections) > 3:
                # Keep header + last 2 sections
                existing = "# MAXIA Video Scripts" + ("# MAXIA Video Scripts".join(sections[-2:]))

            with open(scripts_file, "w", encoding="utf-8") as f:
                f.write(content)
                if existing and not existing.startswith("# MAXIA Video Scripts\n\nGenerated"):
                    pass  # fresh file
                elif existing:
                    f.write("\n\n---\n\n## Previous Scripts\n\n")
                    # Strip the header from existing to avoid duplication
                    prev = existing.replace("# MAXIA Video Scripts\n\n", "## Batch\n\n", 1)
                    f.write(prev)

            _log(f"[VIDEO] 3 scripts sauvegardes: {scripts_file}")
        except Exception as e:
            _log(f"[VIDEO] Erreur ecriture fichier: {e}")

        # ── Stocker en memoire ──
        mem.setdefault("video_scripts", []).append({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "cycle": self._cycle,
            "scripts": response.strip()[:2000],
        })
        # Garder max 10 batches
        if len(mem.get("video_scripts", [])) > 10:
            mem["video_scripts"] = mem["video_scripts"][-10:]

        # ── Notifier ──
        try:
            # Count scripts by splitting on ===
            script_count = response.count("===") + 1
            if script_count > 3:
                script_count = 3
            first_title = ""
            for line in response.split("\n"):
                if line.strip().upper().startswith("TITLE:"):
                    first_title = line.strip()[6:].strip()[:60]
                    break
            summary = f"{script_count} video scripts generated"
            if first_title:
                summary += f"\nFirst: {first_title}"
            await notify_all(
                f"Video Scripts (cycle #{self._cycle})",
                summary,
                "vert",
            )
            _log("[VIDEO] Notification envoyee")
        except Exception as e:
            _log(f"[VIDEO] Erreur notification: {e}")

    async def _auto_engage(self):
        """Engagement intelligent : like + comment de qualite + follow cible.

        Strategie : un bon commentaire vaut 10x un like.
        - Like 3 tweets pertinents
        - Commenter 1 tweet avec un insight (pas promo)
        - Follow seulement les profils de qualite (score >= 50)
        """
        # Use trending topics from observation cycles if available
        observed_trending = self.memory.get("observation", {}).get("trending", [])
        trending_query = observed_trending[self._cycle % len(observed_trending)] if observed_trending else ""

        queries = [
            # AI agents
            "AI agent solana", "built a bot", "AI marketplace",
            "AI agent monetize", "LLM agent USDC",
            "AI agent ethereum", "AI agent multi-chain",
            # Swap / trading
            "crypto swap multi-chain", "bridge USDC between chains",
            "best DEX aggregator", "swap solana to base",
            # GPU / ML
            "rent GPU cheap", "GPU for AI training",
            "local LLM inference", "ollama production",
            # DeFi
            "DeFi yields best", "yield farming strategy",
            "staking rewards crypto",
            # Fine-tuning
            "fine-tune LLM", "LoRA training tips",
        ]
        # Prefer trending topic from observations, fallback to static queries
        if trending_query and self._cycle % 3 == 0:
            query = trending_query
            _log(f"[ENGAGE] Using observed trending: {query[:50]}")
        else:
            query = queries[self._cycle % len(queries)]

        # 1. Search tweets et liker les pertinents (fetch more to allow 3-5 comments per cycle)
        tweets = await browser.search_twitter(query, 10)
        if not tweets:
            return

        liked = 0
        commented = 0
        for t in tweets[:8]:
            url = t.get("url", "")
            text = t.get("text", "")
            if not url:
                continue

            # Like
            if not browser._is_duplicate("like", url):
                result = await browser.like_tweet(url)
                if result.get("success"):
                    liked += 1

            # Commenter plusieurs fois par cycle, max 25 commentaires/jour (GPU local = gratuit)
            today = time.strftime("%Y-%m-%d")
            comments_today = sum(1 for c in self.memory.get("conversations", [])
                                 if c.get("type") == "comment" and c.get("ts", "").startswith(today))
            can_comment = (
                commented < 3
                and comments_today < 25
                and text and len(text) > 30
                and not browser._is_duplicate("reply", url)
            )
            if can_comment:
                username = t.get("username", "")
                # Ne pas commenter si on a deja commente ce user recemment
                recent_users = {c.get("user") for c in self.memory.get("conversations", [])[-20:]
                               if c.get("type") == "comment"}
                if username and username in recent_users:
                    continue
                # Score profile before commenting
                # Note: le scorer Playwright retourne souvent 0 (selectors X casses)
                # Si score=0, on considere que le scorer est casse et on passe (default=50)
                profile_score = 50
                try:
                    score_data = await browser.score_twitter_profile(username)
                    raw_score = score_data.get("score", 0)
                    if raw_score > 0:
                        profile_score = raw_score  # Score reel
                    # Si 0, garder 50 (scorer casse, pas un vrai score)
                except Exception:
                    profile_score = 50
                if profile_score < 10:  # Seuil bas — seulement les profils vraiment mauvais
                    _log(f"  [ENGAGE] Skip comment @{username} (score={profile_score})")
                    continue
                # Point 1: Analyser le profil avant de commenter
                analysis = await self._analyze_before_comment(t)
                if not analysis.get("worth_it", True):
                    continue
                comment = await self._generate_smart_comment(text, analysis.get("context", ""))
                if comment:
                    result = await browser.reply_tweet(url, comment)
                    if result.get("success"):
                        commented += 1
                        comments_today += 1
                        browser._record_action("reply", browser._content_hash("reply", url))
                        _log(f"[ENGAGE] Commented ({comments_today}/25 today): {comment[:60]}")
                        if username:
                            self.memory.setdefault("conversations", []).append({
                                "user": username, "message": text[:80],
                                "reply": comment[:80], "type": "comment",
                                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            })

        if liked or commented:
            _log(f"[ENGAGE] {liked} likes, {commented} comments for '{query[:50]}'")

        # Quote tweet max 5/jour, tous les 3 cycles (~30min)
        qt_today = sum(1 for a in self.memory.get("actions_done", [])
                       if a.get("action") == "quote_tweet" and a.get("ts", "").startswith(time.strftime("%Y-%m-%d")))
        if self._cycle % 3 == 0 and qt_today < 7 and tweets:
            best_tweet = None
            for t in tweets:
                if t.get("url") and t.get("text") and len(t.get("text", "")) > 50:
                    if not browser._is_duplicate("tweet", t["url"]):
                        best_tweet = t
                        break
            if best_tweet:
                qt_text = await self._generate_quote_tweet_text(best_tweet["text"])
                if qt_text:
                    result = await browser.quote_tweet(best_tweet["url"], qt_text)
                    if result.get("success"):
                        _log(f"[ENGAGE] Quote tweeted: {qt_text[:60]}")
                        browser._record_action("tweet", browser._content_hash("tweet", best_tweet["url"]))

        # 2. Follow seulement les profils de qualite (dynamic threshold)
        # Dynamic follow score: follow more when engagement is low, be selective when high
        import datetime as _dtf
        week_start = (_dtf.datetime.now(_dtf.timezone.utc) - _dtf.timedelta(days=7)).strftime("%Y-%m-%d")
        follows_this_week = sum(
            1 for f in self.memory.get("follows", [])
            if f.get("ts", "") >= week_start
        )
        if follows_this_week < 5:
            follow_threshold = 40  # Low engagement week — follow more aggressively
        elif follows_this_week > 15:
            follow_threshold = 60  # High activity week — be more selective
        else:
            follow_threshold = 50  # Default threshold

        if self._cycle % 2 == 0:  # follow tous les 2 cycles seulement
            profiles = await browser.search_twitter_profiles(query, 3)
            for p in profiles[:2]:
                username = p.get("url", "").split("/")[-1] if p.get("url") else ""
                if not username or browser._is_duplicate("follow", username):
                    continue
                score = await browser.score_twitter_profile(username)
                if score.get("score", 0) >= follow_threshold:
                    result = await browser.follow_user(username)
                    if result.get("success") and not result.get("already"):
                        _log(f"[ENGAGE] Followed @{username} (score={score['score']}, threshold={follow_threshold})")
                        self.memory.setdefault("follows", []).append(
                            {"username": username, "ts": time.strftime("%Y-%m-%d")}
                        )

    async def _analyze_before_comment(self, tweet: dict) -> dict:
        """Point 1: Analyse le profil et les tweets recents avant de commenter.
        Retourne {'worth_it': bool, 'context': str} pour personnaliser le commentaire."""
        username = tweet.get("username", "")
        if not username:
            return {"worth_it": True, "context": ""}

        # Verifier si on a deja interagi avec cette personne (CRM)
        past_convos = [c for c in self.memory.get("conversations", []) if c.get("user") == username]
        history = ""
        if past_convos:
            history = f"Previous interaction: {past_convos[-1].get('reply', '')[:100]}"

        # Scorer le profil (followers, bio, activite)
        try:
            score = await browser.score_twitter_profile(username)
            profile_score = score.get("score", 50)
            bio = score.get("bio", "")
        except Exception:
            profile_score = 50
            bio = ""

        # Si profil trop petit ou spam, pas la peine (seuil bas car selectors X sont instables)
        if profile_score < 10:
            _log(f"  [ANALYZE] @{username} score={profile_score} — skip (trop faible)")
            return {"worth_it": False, "context": ""}

        # CEO 14B analyse le contexte pour personnaliser le commentaire
        context = ""
        if bio or history:
            analysis = await call_ceo(
                f"Twitter bio: \"{bio[:150]}\"\nTweet: \"{tweet.get('text', '')[:150]}\"\n"
                f"{history}\n\n"
                f"In 1 sentence: what does this person need? Which MAXIA service fits them? "
                f"(swap, GPU rental, LLM API, fine-tuning, DeFi yields, AI marketplace, multi-chain)\n"
                f"If we already talked to them, reference our history.\n"
                f"Answer:",
                system="CEO MAXIA. Brief analyst. One sentence only.",
                max_tokens=50,
                think=False,  # Rapide pour l'analyse pre-commentaire
            )
            context = analysis.strip() if analysis else ""

        return {"worth_it": True, "context": context, "score": profile_score, "bio": bio[:100]}

    async def _generate_smart_comment(self, tweet_text: str, profile_context: str = "") -> str:
        """Point 2: Commentaire personnalise avec A/B test local (GPU gratuit). Multi-langue."""
        # Detecter la langue du tweet pour repondre dans la meme langue
        lang_code = detect_language(tweet_text)
        lang_name = LANG_NAMES.get(lang_code, "English")
        context_line = f"\nProfile insight: {profile_context}\n" if profile_context else "\n"
        prompt = (
            f"Tweet: \"{tweet_text[:200]}\"\n"
            f"{context_line}"
            f"Reply in {lang_name} to match the tweet's language.\n"
            f"Write a helpful reply (<250 chars) as Alexis, founder of MAXIA:\n"
            f"- First: be genuinely helpful — answer their problem, share a tip, or relate to their experience\n"
            f"- Then: naturally mention MAXIA as a solution IF relevant (AI agents, crypto, swaps, GPU, multi-chain, DeFi)\n"
            f"- If the person is building an AI tool/API/dataset, suggest they can publish and sell it on MAXIA's Creator Marketplace (90% revenue share). Link: maxiaworld.app/marketplace\n"
            f"- ALWAYS end with the link: maxiaworld.app (regardless of language)\n"
            f"- Tone: dev-to-dev, NOT salesy. Example: 'we actually built something for this — maxiaworld.app'\n"
            f"- NEVER criticize, disagree, or be negative. If you can't be positive, say nothing.\n"
            f"- NEVER mention revenue numbers, client count, or business stats.\n"
            f"Reply text ONLY. No quotes."
        )
        system = f"Supportive senior developer and founder. Always positive and helpful. Never mention revenue or client numbers. If the person is building an AI tool/API/dataset, suggest they can publish and sell it on MAXIA's Creator Marketplace (90% revenue share). Link: maxiaworld.app/marketplace. Multi-language: reply in {lang_name}."

        # A/B test : generer 2 variantes et garder la meilleure (GPU local = gratuit)
        variant_a = await call_ollama(prompt, system=system, max_tokens=150)
        variant_b = await call_ollama(
            prompt + "\n\nWrite a DIFFERENT version, more personal and conversational.",
            system=system, max_tokens=150,
        )

        # Choisir la meilleure via 7B rapide (analyse interne)
        best = variant_a
        if variant_a and variant_b:
            pick = await call_ollama(
                f"Which reply is better for Twitter engagement? Reply A or B only.\n\n"
                f"A: \"{variant_a.strip()[:250]}\"\n"
                f"B: \"{variant_b.strip()[:250]}\"\n\nBetter:",
                system="Pick the more engaging, natural reply. Answer A or B only.",
                max_tokens=5,
            )
            if pick and "b" in pick.strip().lower()[:3]:
                best = variant_b
        elif variant_b:
            best = variant_b

        if not best:
            # Fallback Groq
            best = await call_groq_local(prompt, system=system, max_tokens=150)

        comment = (best or "").strip().strip('"').strip("'")
        if len(comment) > 250:
            comment = comment[:247] + "..."
        if not comment or len(comment) < 10:
            return ""
        # Filtre personnalite (mots interdits, negativite, confidentialite)
        return personality_filter(comment) or ""

    async def _generate_quote_tweet_text(self, original_text: str) -> str:
        """Generate a quote tweet comment — positive, supportive, with MAXIA mention. Multi-langue."""
        # Detecter la langue du tweet original
        lang_code = detect_language(original_text)
        lang_name = LANG_NAMES.get(lang_code, "English")
        prompt = (
            f"Someone tweeted: \"{original_text[:200]}\"\n\n"
            f"Reply in {lang_name} to match the tweet's language.\n"
            f"Write a short supportive quote tweet as Alexis, founder of MAXIA (<220 chars).\n"
            f"- Be POSITIVE and SUPPORTIVE — celebrate what they built or shared\n"
            f"- Add value: share your experience, a useful tip, or genuine excitement\n"
            f"- Naturally connect to MAXIA if relevant (AI agents, crypto, multi-chain, swaps, GPU)\n"
            f"- ALWAYS end with: maxiaworld.app (regardless of language)\n"
            f"- Tone: 'love this — we're solving something similar at maxiaworld.app'\n"
            f"- NEVER disagree, criticize, or be negative.\n"
            f"- NEVER mention revenue numbers, client count, or business stats.\n"
            f"Text ONLY:"
        )
        system_qt = f"Supportive solo dev. Always positive, never confrontational. Multi-language: reply in {lang_name}."
        # Groq pour le contenu public, Ollama fallback
        text = await call_groq_local(prompt, system=system_qt, max_tokens=40)
        if not text:
            text = await call_ollama(prompt, system=system_qt, max_tokens=40)
        text = text.strip().strip('"').strip("'")
        if len(text) < 5 or len(text) > 250:
            return ""
        # Filtre personnalite
        return personality_filter(text) or ""

    async def _reddit_comment_strategy(self, subreddit: str) -> dict:
        """Strategie Reddit : trouver un post pertinent et commenter avec valeur.
        Commenter > poster : 10x plus de visibilite, 0% chance de ban."""
        # Requetes variees : du specifique au generique
        queries = ["AI agent", "bot monetize", "LLM production", "agent marketplace",
                   "GPU rental", "AI automation", "web3 AI", "agent framework",
                   "blockchain", "solana", "crypto", "developer"]
        query = queries[self._cycle % len(queries)]

        # Chercher des posts recents (search_reddit fait deja le fallback vers /new/)
        posts = await browser.search_reddit(subreddit, query, 5)
        if not posts:
            # Dernier recours : chercher avec un terme tres generique (juste les new posts)
            _log(f"[REDDIT] Aucun post pour '{query}' sur r/{subreddit}, essai avec requete vide")
            posts = await browser.search_reddit(subreddit, "", 5)
        if not posts:
            _log(f"[REDDIT] Aucun post accessible sur r/{subreddit}")
            return {"success": False, "detail": "No matching posts"}

        for post in posts[:3]:
            url = post.get("url", "")
            title = post.get("title", "")
            if not url or browser._is_duplicate("reddit_comment", url):
                continue

            # Upvote the post first (gives visibility to both post and our comment)
            try:
                await browser.upvote_reddit(url)
            except Exception:
                pass

            # Generer un commentaire utile via LLM
            prompt = (
                f"Post on r/{subreddit}: \"{title[:150]}\"\n\n"
                f"Write a Reddit comment as Alexis, founder of MAXIA. IN ENGLISH. Max 300 chars.\n"
                f"- First: be genuinely helpful — share your experience or answer the question\n"
                f"- Then: naturally mention MAXIA if the topic relates to AI agents, crypto, swaps, GPU, multi-chain\n"
                f"- Sound like a regular dev, NOT a company account\n"
                f"- End with: 'been building this at maxiaworld.app' or 'check it out: maxiaworld.app'\n"
                f"- NEVER mention revenue, client count, or business stats\n"
                f"Comment ONLY:"
            )
            # Ollama 14B pour Reddit (gratuit, qualite suffisante)
            comment = await call_ollama(prompt, system="You are a solo dev on Reddit. Casual, helpful, English only. No marketing.", max_tokens=80)
            if not comment:
                comment = await call_groq_local(prompt, system="You are a solo dev on Reddit. Casual, helpful, English only. No marketing.", max_tokens=80)
            comment = comment.strip().strip('"').strip("'")
            if not comment or len(comment) < 20:
                continue

            result = await browser.comment_reddit(url, comment)
            if result.get("success"):
                _log(f"[REDDIT] Commented on r/{subreddit}: {comment[:60]}")
                self.memory.setdefault("conversations", []).append({
                    "user": f"r/{subreddit}", "message": title[:80],
                    "reply": comment[:80], "type": "reddit_comment",
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                })
                return {"success": True, "detail": f"Commented on r/{subreddit}"}

        return {"success": False, "detail": "No commentable posts"}

    async def _discover_communities(self):
        """Decouvre automatiquement de nouveaux Discord, Telegram groups et GitHub repos
        en cherchant sur Twitter et GitHub trending. Ajoute les bons a la memoire."""
        discovered = self.memory.setdefault("discovered_communities", {
            "discord": [], "telegram": [], "github": [],
        })

        # Limite : max 20 de chaque type en memoire
        for key in ("discord", "telegram", "github"):
            if len(discovered[key]) > 20:
                discovered[key] = discovered[key][-20:]

        # 1. Chercher des liens discord.gg et t.me dans les tweets AI/crypto
        search_queries = [
            "AI agent discord.gg invite",
            "crypto dev telegram group t.me",
            "solana developer discord",
            "LLM community discord invite",
            "DeFi telegram group",
            "AI agent github repo",
        ]
        query = search_queries[self._cycle % len(search_queries)]
        tweets = await browser.search_twitter(query, 5)

        for t in (tweets or []):
            text = t.get("text", "")
            # Extraire les liens Discord
            import re
            discord_links = re.findall(r"https?://discord\.gg/[\w-]+", text)
            for link in discord_links:
                if link not in discovered["discord"] and len(discovered["discord"]) < 20:
                    discovered["discord"].append(link)
                    _log(f"[DISCOVER] New Discord: {link}")

            # Extraire les liens Telegram
            tg_links = re.findall(r"https?://t\.me/[\w-]+", text)
            for link in tg_links:
                if link not in discovered["telegram"] and len(discovered["telegram"]) < 20:
                    # Filtrer les liens de bots et channels perso
                    if not any(skip in link.lower() for skip in ["bot", "joinchat", "addstickers"]):
                        discovered["telegram"].append(link)
                        _log(f"[DISCOVER] New Telegram: {link}")

            # Extraire les liens GitHub
            gh_links = re.findall(r"https?://github\.com/([\w-]+/[\w-]+)", text)
            for repo in gh_links:
                if repo not in discovered["github"] and len(discovered["github"]) < 20:
                    discovered["github"].append(repo)
                    _log(f"[DISCOVER] New GitHub: {repo}")

        # 2. Chercher repos GitHub trending via browser
        if self._cycle % 12 == 0:
            try:
                trending = await browser.search_twitter("github trending AI agent", 3)
                for t in (trending or []):
                    gh_links = re.findall(r"github\.com/([\w-]+/[\w-]+)", t.get("text", ""))
                    for repo in gh_links:
                        if repo not in discovered["github"] and len(discovered["github"]) < 20:
                            discovered["github"].append(repo)
                            _log(f"[DISCOVER] Trending GitHub: {repo}")
            except Exception:
                pass

        total = len(discovered["discord"]) + len(discovered["telegram"]) + len(discovered["github"])
        if total > 0:
            _log(f"[DISCOVER] Total: {len(discovered['discord'])} Discord, {len(discovered['telegram'])} Telegram, {len(discovered['github'])} GitHub")

        # 3. Rejoindre 1 nouveau par cycle (pas spam)
        try:
            if discovered["discord"]:
                new_server = discovered["discord"][self._cycle % len(discovered["discord"])]
                already_joined = self.memory.get("groups_joined", [])
                if new_server not in already_joined:
                    result = await browser.join_discord_server(new_server)
                    if result.get("success"):
                        self.memory.setdefault("groups_joined", []).append(new_server)
                        _log(f"[DISCOVER] Joined Discord: {new_server}")
            if discovered["telegram"] and self._cycle % 2 == 0:
                new_group = discovered["telegram"][self._cycle % len(discovered["telegram"])]
                already_joined = self.memory.get("groups_joined", [])
                if new_group not in already_joined:
                    result = await browser.join_telegram_group(new_group)
                    if result.get("success"):
                        self.memory.setdefault("groups_joined", []).append(new_group)
                        _log(f"[DISCOVER] Joined Telegram: {new_group}")
        except Exception as e:
            _log(f"[DISCOVER] Join error: {e}")

        return {"success": True, "detail": f"Discovered {total} communities"}

    async def _check_engagement_feedback(self):
        """Verifie l'engagement des derniers tweets et commentaires.
        Apprend quel contenu resonne et ajuste."""
        convos = self.memory.get("conversations", [])[-5:]
        tweets = self.memory.get("tweets_posted", [])[-3:]
        if not tweets and not convos:
            return

        # Verifier engagement du dernier tweet
        actions = self.memory.get("actions_done", [])
        tweet_actions = [a for a in actions if a.get("action") == "post_tweet" and a.get("success")]
        # On ne peut pas facilement retrouver l'URL, mais on peut checker les mentions
        # pour voir si quelqu'un a repondu a nos tweets
        mentions = await browser.get_mentions(5)
        reply_count = len(mentions) if mentions else 0

        if reply_count > 0:
            _log(f"[FEEDBACK] {reply_count} mentions detectees (engagement positif)")
            self.memory.setdefault("engagement_stats", []).append({
                "ts": time.strftime("%Y-%m-%d"), "mentions": reply_count,
                "tweets_today": len([t for t in tweets if t.get("ts", "").startswith(time.strftime("%Y-%m-%d"))]),
            })
        else:
            _log(f"[FEEDBACK] 0 mention — contenu a ameliorer")

        # Garder max 30 jours de stats
        stats = self.memory.get("engagement_stats", [])
        if len(stats) > 30:
            self.memory["engagement_stats"] = stats[-30:]

    async def _crm_followup(self):
        """CRM : detecter les prospects chauds et les relancer.
        Un prospect chaud = quelqu'un qui a interagi avec nous (mention, reply)
        mais n'a pas encore visite maxiaworld.app."""
        convos = self.memory.get("conversations", [])
        if not convos:
            return

        # Trouver les users avec qui on a eu une conversation mais pas de follow-up
        contacted = {c.get("target", c.get("username", "")) for c in self.memory.get("contacts", [])}
        followed = {f.get("username", "") for f in self.memory.get("follows", [])}

        prospects = []
        seen = set()
        for c in reversed(convos):
            user = c.get("user", "")
            if not user or user in seen or user in contacted:
                continue
            seen.add(user)
            # Si on a repondu a sa mention mais jamais follow → follow + ajouter au CRM
            if user not in followed:
                prospects.append(user)

        for username in prospects[:2]:  # Max 2 follow-ups par cycle
            # Follow le prospect
            try:
                result = await browser.follow_user(username)
            except Exception:
                result = None
            if result and result.get("success") and not result.get("already"):
                _log(f"[CRM] Follow-up: followed @{username} (prospect chaud)")
                self.memory.setdefault("follows", []).append(
                    {"username": username, "ts": time.strftime("%Y-%m-%d"), "source": "crm"}
                )
                self.memory.setdefault("contacts", []).append({
                    "target": username, "canal": "twitter_followup",
                    "ts": time.strftime("%Y-%m-%d"), "status": "followed",
                })

        # Check for open conversations — users who replied 1-2 days ago we haven't followed up with
        import datetime as _dtcrm
        now_crm = _dtcrm.datetime.now(_dtcrm.timezone.utc)
        for c in reversed(convos[-20:]):
            user = c.get("user", "")
            ts_str = c.get("ts", "")
            if not user or not ts_str:
                continue
            try:
                convo_time = _dtcrm.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                if convo_time.tzinfo is None:
                    convo_time = convo_time.replace(tzinfo=_dtcrm.timezone.utc)
                age_hours = (now_crm - convo_time).total_seconds() / 3600
            except (ValueError, Exception):
                continue
            # Only consider conversations 24-48 hours old (1-2 days)
            if not (24 <= age_hours <= 48):
                continue
            # Check if we already followed up (has a newer message from us to this user)
            already_followedup = any(
                cc.get("user") == user and cc.get("ts", "") > ts_str
                for cc in convos if cc is not c
            )
            if already_followedup or user in contacted:
                continue
            # Generate a follow-up DM
            history = [cc.get("message", "") for cc in convos if cc.get("user") == user][-3:]
            if not history:
                continue
            followup = await generate_conversation_reply(
                history + [f"(you last chatted {int(age_hours)}h ago, send a friendly follow-up)"],
                user, "Twitter"
            )
            if followup:
                try:
                    result = await browser.dm_twitter(user, followup)
                except Exception:
                    result = None
                if result and result.get("success"):
                    _log(f"[CRM] Follow-up DM to @{user}: {followup[:60]}")
                    self.memory.setdefault("contacts", []).append({
                        "target": user, "canal": "twitter_dm_followup",
                        "ts": time.strftime("%Y-%m-%d"), "status": "followed_up",
                        "last_message": followup[:50],
                    })
                    break  # Max 1 follow-up DM per cycle

    async def _dm_prospect(self):
        """Identifie les prospects chauds (qui ont interagi avec nos tweets/comments)
        et envoie un premier DM personnalise. Max 3 DMs/jour."""
        today = time.strftime("%Y-%m-%d")
        dms_today = sum(1 for c in self.memory.get("contacts", [])
                        if c.get("canal", "").startswith("twitter_dm") and c.get("ts", "").startswith(today))
        if dms_today >= 3:
            return {"success": True, "detail": f"Limite 3 DMs/jour atteinte ({dms_today})"}

        contacted = {c.get("target", "") for c in self.memory.get("contacts", [])}
        convos = self.memory.get("conversations", [])

        # Trouver un prospect : quelqu'un avec qui on a eu une conversation mais jamais DM
        prospect = None
        for c in reversed(convos[-30:]):
            user = c.get("user", "")
            if user and user not in contacted and c.get("type") in ("comment", "mention_reply"):
                prospect = {"user": user, "context": c.get("message", "")[:150]}
                break

        if not prospect:
            return {"success": True, "detail": "Aucun prospect chaud detecte"}

        user = prospect["user"]
        context = prospect["context"]

        # Generer le DM
        prompt = (
            f"You had a public conversation with @{user} about: \"{context}\"\n\n"
            f"Write a short friendly DM (<200 chars) to continue the conversation privately.\n"
            f"- Reference the topic you discussed\n"
            f"- Invite them to check maxiaworld.app if relevant\n"
            f"- Casual, dev-to-dev tone, NOT salesy\n"
            f"DM text ONLY:"
        )
        dm_text = await call_ollama(prompt, system="You are Alexis, founder of MAXIA. Friendly, casual, English only.", max_tokens=60)
        if not dm_text:
            dm_text = await call_groq_local(prompt, system="You are Alexis, founder of MAXIA. Friendly, casual, English only.", max_tokens=60)
        dm_text = (dm_text or "").strip().strip('"').strip("'")
        if not dm_text or len(dm_text) < 10:
            return {"success": False, "detail": "Echec generation DM"}

        _log(f"[DM PROSPECT] @{user}: {dm_text[:80]}")
        result = await self._do_browser("dm_twitter", {"username": user, "text": dm_text})
        if result.get("success"):
            self.memory.setdefault("contacts", []).append({
                "target": user, "canal": "twitter_dm_prospect",
                "ts": today, "status": "dm_sent",
                "last_message": dm_text[:50],
            })
        return result

    async def _weekly_retrospective(self):
        """Retrospective hebdo : analyser la semaine et ajuster la strategie."""
        actions = self.memory.get("actions_done", [])
        tweets = self.memory.get("tweets_posted", [])
        convos = self.memory.get("conversations", [])
        follows = self.memory.get("follows", [])
        contacts = self.memory.get("contacts", [])
        eng_stats = self.memory.get("engagement_stats", [])

        prompt = (
            f"RETROSPECTIVE HEBDO CEO MAXIA — semaine du {time.strftime('%Y-%m-%d')}:\n\n"
            f"Stats: {len(actions)} actions, {len(tweets)} tweets, {len(convos)} conversations,\n"
            f"  {len(follows)} follows, {len(contacts)} contacts CRM\n"
            f"Engagement: {json.dumps(eng_stats[-7:], default=str)[:300]}\n"
            f"Regles actuelles: {json.dumps(self.memory.get('regles', []), default=str)[:300]}\n\n"
            f"En tant que growth advisor:\n"
            f"1. Qu'est-ce qui a MARCHE cette semaine ? (1 phrase)\n"
            f"2. Qu'est-ce qui n'a PAS marche ? (1 phrase)\n"
            f"3. 3 ACTIONS CONCRETES pour la semaine prochaine (1 ligne chacune)\n"
            f"4. 1 REGLE a ajouter ou modifier\n"
            f"Reponse en anglais, max 200 mots."
        )
        retro = await call_local_llm(prompt, system="Concise growth advisor. Actionable insights only.", max_tokens=300)
        if retro and len(retro) > 30:
            _log(f"[RETRO] {retro[:200]}")
            self.memory.setdefault("retrospectives", []).append({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "summary": retro[:500],
            })
            # Garder max 10 retros
            if len(self.memory.get("retrospectives", [])) > 10:
                self.memory["retrospectives"] = self.memory["retrospectives"][-10:]
            # Extraire les regles
            for line in retro.split("\n"):
                line = line.strip().lstrip("0123456789.-) ")
                if line and 20 < len(line) < 80 and line not in self.memory.get("regles", []):
                    if any(kw in line.lower() for kw in ["focus", "stop", "increase", "reduce", "target", "prioritize", "avoid"]):
                        self.memory.setdefault("regles", []).append(line)
                        _log(f"[RETRO RULE] {line}")

    async def _weekly_thread(self):
        """Weekly R&D thread — synthesizes the week's surf/research findings into a 4-5 tweet thread.
        Runs on Monday, max once per week. Uses call_ceo(think=True) for strategic synthesis."""
        import datetime as _dt
        now = _dt.datetime.now(_dt.timezone.utc)
        if now.weekday() != 0:  # 0 = Monday
            return
        # Check if we already posted a thread this week
        last_thread = self.memory.get("last_thread_week", "")
        current_week = now.strftime("%Y-W%W")
        if last_thread == current_week:
            return

        _log("[THREAD] Monday — generating weekly R&D thread from findings")

        # Collect the week's R&D findings
        surf_findings = self.memory.get("surf_findings", [])
        research_findings = self.memory.get("research_findings", [])
        all_findings = surf_findings[-20:] + research_findings[-20:]
        if not all_findings:
            # Fallback: use recent actions and conversations as context
            recent_actions = self.memory.get("actions_done", [])[-15:]
            recent_convos = self.memory.get("conversations", [])[-5:]
            all_findings = [
                {"summary": json.dumps(recent_actions, default=str)[:400]},
                {"summary": json.dumps(recent_convos, default=str)[:300]},
            ]
            _log("[THREAD] No R&D findings, using recent actions as fallback")

        findings_str = json.dumps(all_findings, default=str)[:1500]

        prompt = (
            f"Based on this week's R&D findings:\n{findings_str}\n\n"
            f"Write a Twitter thread (4-5 tweets) as Alexis, founder of MAXIA.\n"
            f"Rules:\n"
            f"- Tweet 1: hook/question that grabs attention\n"
            f"- Tweets 2-4: specific insights, data points, or discoveries from the R&D\n"
            f"- Last tweet: subtle CTA with maxiaworld.app\n"
            f"- Technical tone, dev-to-dev\n"
            f"- Each tweet max 280 chars\n"
            f"- NEVER mention revenue or client numbers\n"
            f"Format: one tweet per line, separated by ---"
        )
        system = (
            f"You are Alexis, solo founder building MAXIA ({MAXIA_FEATURES_SHORT}). "
            "English only. Technical, honest, dev-to-dev tone."
        )
        raw = await call_ceo(prompt, system, max_tokens=600, think=True)

        # Parse tweets separated by ---
        tweets = []
        if raw and "---" in raw:
            parts = [p.strip() for p in raw.split("---") if p.strip() and len(p.strip()) > 15]
            tweets = [t[:280] for t in parts[:5]]
        if len(tweets) < 4:
            # Fallback: try JSON array format
            try:
                parsed = json.loads(raw.strip())
                if isinstance(parsed, list) and len(parsed) >= 4:
                    tweets = [t.strip().strip('"')[:280] for t in parsed[:5]]
            except (json.JSONDecodeError, Exception):
                pass
        if len(tweets) < 4:
            # Fallback: try line-by-line extraction
            lines = [ln.strip().lstrip("0123456789.-) ").strip('"') for ln in (raw or "").strip().split("\n") if ln.strip() and len(ln.strip()) > 20]
            tweets = [ln[:280] for ln in lines[:5]]

        if len(tweets) < 4:
            _log("[THREAD] Failed to generate 4+ tweets, skipping")
            return

        # Apply personality filter to each tweet
        filtered_tweets = []
        for t in tweets:
            ft = personality_filter(t)
            if ft:
                filtered_tweets.append(ft)
        if len(filtered_tweets) < 4:
            _log("[THREAD] Too many tweets blocked by personality filter, skipping")
            return
        tweets = filtered_tweets

        # Check tweet count limit
        if self._tweets_today_count() >= 2:
            _log("[THREAD] Tweet limit reached, skipping thread")
            return

        # Post the thread
        result = await browser.post_thread(tweets=tweets)
        if result.get("success"):
            _log(f"[THREAD] Posted weekly R&D thread ({len(tweets)} tweets): {tweets[0][:60]}...")
            self.memory["last_thread_week"] = current_week
            self.memory.setdefault("tweets_posted", []).append({
                "text": f"[THREAD] {tweets[0][:50]}...", "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            })
            # Store thread in memory for retrospective
            self.memory.setdefault("weekly_threads", []).append({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "week": current_week,
                "tweets": tweets,
                "findings_count": len(all_findings),
            })
            if len(self.memory.get("weekly_threads", [])) > 10:
                self.memory["weekly_threads"] = self.memory["weekly_threads"][-10:]
        else:
            _log(f"[THREAD] Failed to post: {result}")

    async def _proactive_dm_engaged(self):
        """Proactive DMs to users who engaged with MAXIA tweets in the last 24h.
        Max 2 DMs per cycle, dedup against already-contacted users."""
        convos = self.memory.get("conversations", [])
        if not convos:
            return

        # Find users who liked/commented on our tweets in the last 24h
        cutoff_ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 24 * 3600))
        engaged_users = []
        for c in reversed(convos[-50:]):
            ts = c.get("ts", "")
            if ts and ts < cutoff_ts:
                continue
            user = c.get("user", "")
            ctype = c.get("type", "")
            if user and ctype in ("mention_reply", "own_tweet_reply", "comment", "like"):
                engaged_users.append({
                    "username": user,
                    "topic": c.get("message", c.get("reply", ""))[:100],
                    "type": ctype,
                })

        if not engaged_users:
            _log("[DM] No engaged users found in last 24h")
            return

        # Dedup: skip users we already DM'd
        already_dmd = set()
        for contact in self.memory.get("contacts", []):
            if "dm" in contact.get("canal", ""):
                already_dmd.add(contact.get("target", ""))
        for dm_record in self.memory.get("proactive_dms", []):
            already_dmd.add(dm_record.get("username", ""))

        # Deduplicate by username and filter
        seen = set()
        candidates = []
        for u in engaged_users:
            uname = u["username"]
            if uname not in already_dmd and uname not in seen:
                seen.add(uname)
                candidates.append(u)

        if not candidates:
            _log("[DM] All engaged users already DM'd")
            return

        # Daily limit: max 5 proactive DMs per day
        today = time.strftime("%Y-%m-%d")
        dms_today = sum(1 for d in self.memory.get("proactive_dms", [])
                        if d.get("ts", "").startswith(today))
        if dms_today >= 5:
            _log(f"[DM] Daily proactive DM limit reached ({dms_today}/5)")
            return

        sent = 0
        for candidate in candidates[:2]:
            username = candidate["username"]
            topic = candidate["topic"]

            prompt = (
                f"@{username} engaged with our tweet about {topic}.\n"
                f"Write a short, friendly DM (max 200 chars) as Alexis from MAXIA.\n"
                f"Reference their engagement specifically. Invite them to try MAXIA.\n"
                f"NOT salesy, dev-to-dev tone. Include maxiaworld.app\n"
                f"DM text ONLY:"
            )
            dm_text = await call_ceo(
                prompt,
                system="You are Alexis, founder of MAXIA. Friendly, casual, English only.",
                max_tokens=60,
                think=False,
            )
            dm_text = (dm_text or "").strip().strip('"').strip("'")
            if not dm_text or len(dm_text) < 10:
                _log(f"[DM] Failed to generate DM for @{username}")
                continue

            # Apply personality filter
            dm_text = personality_filter(dm_text)
            if not dm_text:
                _log(f"[DM] DM for @{username} blocked by personality filter")
                continue

            dm_text = dm_text[:200]
            _log(f"[DM] Proactive DM to @{username}: {dm_text[:80]}")

            # Try browser first, fallback to _do_browser
            result = await self._do_browser("dm_twitter", {"username": username, "text": dm_text})
            if result.get("success"):
                sent += 1
                self.memory.setdefault("proactive_dms", []).append({
                    "username": username,
                    "topic": topic[:80],
                    "message": dm_text[:100],
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                })
                self.memory.setdefault("contacts", []).append({
                    "target": username, "canal": "twitter_dm_proactive",
                    "ts": today, "status": "dm_sent",
                    "last_message": dm_text[:50],
                })
            else:
                _log(f"[DM] Failed to send DM to @{username}: {result.get('detail', '')}")

        # Keep proactive_dms list bounded
        if len(self.memory.get("proactive_dms", [])) > 100:
            self.memory["proactive_dms"] = self.memory["proactive_dms"][-100:]

        if sent:
            _log(f"[DM] Sent {sent} proactive DMs to engaged users")

    async def _engage_competitor_threads(self):
        """Engage with competitor tweets by adding constructive, value-adding comments.
        Max 1 comment per cycle. Positive tone, no attacks."""
        competitor_accounts = ["@virtikiprotocol", "@Fetch_ai", "@SingularityNET", "@autonaborolas", "@myshell_ai"]

        # Rotate through competitors across cycles
        competitor = competitor_accounts[self._cycle % len(competitor_accounts)]
        query = f"from:{competitor.lstrip('@')}"

        _log(f"[COMPETE] Searching tweets from {competitor}")
        tweets = await browser.search_twitter(query, 5)
        if not tweets:
            _log(f"[COMPETE] No tweets found from {competitor}")
            return

        # Daily limit: max 3 competitor comments per day
        today = time.strftime("%Y-%m-%d")
        compete_today = sum(1 for c in self.memory.get("competitor_engagements", [])
                           if c.get("ts", "").startswith(today))
        if compete_today >= 3:
            _log(f"[COMPETE] Daily limit reached ({compete_today}/3)")
            return

        # Dedup: check already-engaged tweets
        already_engaged = {c.get("tweet_url", "") for c in self.memory.get("competitor_engagements", [])}

        for t in tweets[:3]:
            url = t.get("url", "")
            text = t.get("text", "")
            if not url or not text or len(text) < 30:
                continue
            if url in already_engaged:
                continue
            if browser._is_duplicate("reply", url):
                continue

            # Generate a constructive, positive comment
            prompt = (
                f"A competitor ({competitor}) posted this tweet:\n\"{text[:300]}\"\n\n"
                f"Write a constructive, positive reply as Alexis from MAXIA.\n"
                f"Rules:\n"
                f"- Add value to the conversation (NOT just promotion)\n"
                f"- Be respectful and acknowledge their work\n"
                f"- If relevant, briefly mention what MAXIA does differently\n"
                f"- Dev-to-dev tone, technical where appropriate\n"
                f"- Max 250 chars\n"
                f"- Include maxiaworld.app only if it flows naturally\n"
                f"- NEVER attack, criticize, or be negative\n"
                f"Reply ONLY:"
            )
            comment = await call_ceo(
                prompt,
                system=f"You are Alexis, founder of MAXIA ({MAXIA_FEATURES_SHORT}). "
                       "Positive, constructive, technical. English only.",
                max_tokens=80,
                think=True,
            )
            comment = (comment or "").strip().strip('"').strip("'")
            if not comment or len(comment) < 15:
                _log(f"[COMPETE] Failed to generate comment for {competitor}")
                continue

            # Apply personality filter
            comment = personality_filter(comment)
            if not comment:
                _log(f"[COMPETE] Comment for {competitor} blocked by personality filter")
                continue

            comment = comment[:280]
            _log(f"[COMPETE] Commenting on {competitor}: {comment[:80]}")

            result = await browser.reply_tweet(url, comment)
            if result.get("success"):
                browser._record_action("reply", browser._content_hash("reply", url))
                self.memory.setdefault("competitor_engagements", []).append({
                    "competitor": competitor,
                    "tweet_url": url,
                    "tweet_text": text[:100],
                    "comment": comment[:150],
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                })
                self.memory.setdefault("conversations", []).append({
                    "user": competitor.lstrip("@"),
                    "message": text[:80],
                    "reply": comment[:80],
                    "type": "competitor_comment",
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                })
                _log(f"[COMPETE] Successfully commented on {competitor}'s tweet")
                # Max 1 per cycle
                break
            else:
                _log(f"[COMPETE] Failed to post comment: {result.get('detail', '')}")

        # Keep list bounded
        if len(self.memory.get("competitor_engagements", [])) > 50:
            self.memory["competitor_engagements"] = self.memory["competitor_engagements"][-50:]

    async def _reply_to_mentions(self) -> dict:
        """Lit les mentions et repond intelligemment a chacune.
        Max 1 reply par username par 24h. Max 3 replies total par cycle."""
        mentions = await browser.get_mentions(10)
        if not mentions:
            return {"success": True, "detail": "0 mentions"}

        # Collecter les users deja reply dans les dernieres 6h (pas 24h — trop agressif)
        now_ts = time.time()
        cutoff = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now_ts - 6 * 3600))
        already_replied_users = set()
        for c in self.memory.get("conversations", []):
            c_ts = c.get("ts", "")
            if c_ts and c_ts >= cutoff:
                u = c.get("user", "")
                if u:
                    already_replied_users.add(u)

        replied = 0
        replied_users = set()
        for m in mentions:
            url = m.get("url", "")
            text = m.get("text", "")
            user = m.get("username", "")
            if not url or not text:
                continue
            # Dedup par URL (deja repondu a ce tweet exact)
            if browser._is_duplicate("reply", url):
                continue
            # Dedup par username — 1 reply par user par 24h (persiste entre cycles)
            if user and (user in replied_users or user in already_replied_users):
                continue
            # Like the mention first (shows we're attentive)
            if url and not browser._is_duplicate("like", url):
                await browser.like_tweet(url)
            # Generer une reponse via LLM (fallback si LLM down)
            reply_text = await generate_smart_reply(text, user)
            if not reply_text:
                fallbacks = [
                    f"hey @{user}! appreciate the mention, happy to chat about this",
                    f"thanks for bringing this up! what's your setup like?",
                    f"interesting point — been thinking about this too. DM me if you want to dig deeper",
                ]
                reply_text = random.choice(fallbacks)
            if reply_text:
                result = await browser.reply_tweet(url, reply_text)
                if result.get("success"):
                    replied += 1
                    if user:
                        replied_users.add(user)
                    _log(f"  Reply @{user}: {reply_text[:60]}")
                    browser._record_action("reply", browser._content_hash("reply", url))
                    # Tracker la conversation
                    self.memory.setdefault("conversations", []).append({
                        "user": user, "message": text[:100],
                        "reply": reply_text[:100], "url": url,
                        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    })
                    if len(self.memory.get("conversations", [])) > 50:
                        self.memory["conversations"] = self.memory["conversations"][-50:]
            if replied >= 3:  # Max 3 replies par cycle
                break

        return {"success": True, "detail": f"{replied} replies sur {len(mentions)} mentions"}

    async def _check_own_tweet_replies(self):
        """Read replies to our own tweets and respond to build conversations."""
        try:
            replies = await browser.read_own_tweet_replies(3)
        except Exception as e:
            _log(f"[OWN REPLIES] Error reading replies: {e}")
            return

        if not replies:
            return

        replied = 0
        for r in replies:
            url = r.get("url", "")
            text = r.get("text", "")
            user = r.get("username", "")
            if not url or not text or not user:
                continue

            # Skip if already in conversations or already replied
            convos = self.memory.get("conversations", [])
            already_replied = any(
                c.get("url") == url or (c.get("user") == user and c.get("message", "")[:40] == text[:40])
                for c in convos
            )
            if already_replied or browser._is_duplicate("reply", url):
                continue

            # Like the reply
            if not browser._is_duplicate("like", url):
                await browser.like_tweet(url)

            # Generate and post a reply
            reply_text = await generate_smart_reply(text, user)
            if not reply_text:
                continue

            result = await browser.reply_tweet(url, reply_text)
            if result.get("success"):
                replied += 1
                _log(f"  [OWN REPLIES] Replied to @{user}: {reply_text[:60]}")
                browser._record_action("reply", browser._content_hash("reply", url))
                self.memory.setdefault("conversations", []).append({
                    "user": user, "message": text[:100],
                    "reply": reply_text[:100], "url": url,
                    "type": "own_tweet_reply",
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                })
                if len(self.memory.get("conversations", [])) > 50:
                    self.memory["conversations"] = self.memory["conversations"][-50:]

            if replied >= 3:
                break

        if replied:
            _log(f"[OWN REPLIES] {replied} replies to our tweet replies")

    async def _comment_github_ai_projects(self) -> dict:
        """#3: Commente sur des issues/discussions de projets AI.
        Methode 1 : API GitHub via api_social (fiable, pas de Playwright).
        Methode 2 : recherche Google + Playwright (fallback si API echoue).
        Echoue silencieusement si aucune methode ne marche (evite le spam d'erreurs)."""
        import re

        projects = [
            "elizaOS/eliza", "langchain-ai/langchain", "Significant-Gravitas/AutoGPT",
            "microsoft/autogen", "crewai/crewai",
        ]
        comment_text = (
            f"Interesting discussion! We're building MAXIA, an AI-to-AI marketplace "
            f"where agents can discover and trade services using USDC on 14 chains "
            f"(Solana, Base, ETH, XRP, Polygon, Arbitrum, Avalanche, BNB, TON, SUI, TRON, NEAR, Aptos, SEI). Happy to collaborate or integrate. "
            f"Check it out: maxiaworld.app"
        )
        commented = 0

        # Methode 1 : API GitHub via api_social (cherche les issues recentes directement)
        try:
            from api_social import github_list_issues, github_comment_issue
            for project in projects[:2]:  # Max 2 par cycle
                try:
                    issues = await github_list_issues(project, limit=5)
                    for issue in issues:
                        issue_url = issue.get("html_url", "")
                        if browser._is_duplicate("github_comment", issue_url):
                            continue
                        result = await github_comment_issue(project, issue["number"], comment_text)
                        if result.get("success"):
                            commented += 1
                            _log(f"[GITHUB] API comment on {project}#{issue['number']}")
                            browser._record_action("github_comment", browser._content_hash("github_comment", issue_url))
                            break  # 1 commentaire par projet max
                except Exception as e:
                    _log(f"[GITHUB] API skip {project}: {e}")
                    continue
        except Exception as e:
            _log(f"[GITHUB] api_social import error: {e}")

        # Methode 2 : Playwright (fallback si API n'a rien donne)
        if commented == 0:
            for project in projects[:2]:
                try:
                    results = await browser.search_google(f"site:github.com/{project}/issues AI agent marketplace", 3)
                    for r in results:
                        url = r.get("url", "")
                        if "/issues/" not in url or browser._is_duplicate("github_comment", url):
                            continue
                        try:
                            result = await browser.comment_github_discussion(url, comment_text)
                            if result.get("success", False):
                                commented += 1
                                browser._record_action("github_comment", browser._content_hash("github_comment", url))
                        except Exception:
                            pass
                        break
                except Exception as e:
                    _log(f"[GITHUB] Browser skip {project}: {e}")
                    continue

        # Pas d'erreur bruyante si 0 commentaires — c'est normal sans token
        if commented == 0:
            _log("[GITHUB] 0 comments this cycle (normal without GITHUB_TOKEN)")
        return {"success": commented > 0, "detail": f"{commented} GitHub comments"}

    async def _search_and_join_groups(self, platform: str = "telegram") -> dict:
        """Cherche et rejoint des groupes pertinents sur Telegram/Discord."""
        queries = ["Solana dev", "AI agents", "ElizaOS", "LangChain", "DeFi builders", "Web3 dev",
                   "Polygon developers", "Arbitrum builders", "Avalanche subnet", "BNB Chain dev",
                   "TON developers", "SUI Move dev", "TRON developers"]
        joined = []
        already = self.memory.get("groups_joined", [])

        if platform == "telegram":
            # Chercher des groupes Telegram via Google
            for q in queries[:3]:
                results = await browser.search_google(f"telegram group {q} invite link t.me", 3)
                for r in results:
                    url = r.get("url", "")
                    if "t.me" in url and url not in already:
                        result = await browser.join_telegram_group(url)
                        if result.get("success"):
                            joined.append(url)
                            self.memory.setdefault("groups_joined", []).append(url)
                            _log(f"  Rejoint Telegram: {url}")
                        if len(joined) >= 2:
                            break
                if len(joined) >= 2:
                    break

        elif platform == "discord":
            for q in queries[:3]:
                results = await browser.search_google(f"discord server {q} invite discord.gg", 3)
                for r in results:
                    url = r.get("url", "")
                    if "discord" in url and url not in already:
                        result = await browser.join_discord_server(url)
                        if result.get("success"):
                            joined.append(url)
                            self.memory.setdefault("groups_joined", []).append(url)
                            _log(f"  Rejoint Discord: {url}")
                        if len(joined) >= 2:
                            break
                if len(joined) >= 2:
                    break

        return {"success": bool(joined), "detail": f"{len(joined)} groupes rejoints sur {platform}", "groups": joined}

    def _clean_screenshots(self) -> dict:
        """Nettoie les screenshots de preuve > 7 jours."""
        import glob
        profile_dir = os.path.expanduser("~/.maxia-browser")
        count = 0
        now = time.time()
        for f in glob.glob(os.path.join(profile_dir, "*.png")):
            try:
                if now - os.path.getmtime(f) > 7 * 86400:
                    os.remove(f)
                    count += 1
            except Exception:
                pass
        return {"success": True, "detail": f"{count} screenshots supprimes"}

    def _fix_params(self, action: str, params: dict) -> dict | None:
        """Valide et complete les params. Retourne None si action impossible."""
        if action == "post_tweet":
            if not params.get("text"):
                params["text"] = pick_tweet_template()
        elif action == "post_template_tweet":
            pass  # Pas besoin de params
        elif action == "post_thread":
            if not params.get("tweets") or not isinstance(params.get("tweets"), list):
                # Generer un thread basique
                params["tweets"] = [
                    "How to monetize your AI agent (thread):",
                    "1/ List your service on MAXIA with one API call. POST /api/public/sell. Done.",
                    "2/ Other AI agents discover and buy your service. You get paid in USDC. On 14 chains. maxiaworld.app?utm_source=twitter&utm_medium=tweet",
                ]
        elif action == "follow_user":
            if not params.get("username"):
                return None  # Impossible sans username
        elif action == "like_tweet":
            if not params.get("tweet_url"):
                return None
        elif action == "reply_tweet":
            if not params.get("tweet_url") or not params.get("text"):
                return None
        elif action == "score_profile":
            if not params.get("username"):
                return None
        elif action == "post_reddit":
            if not params.get("subreddit"):
                params["subreddit"] = "solanadev"
            if not params.get("title") or not params.get("body"):
                # Contenu sera genere dans _decide() (async) via _generate_reddit_post
                params["_needs_reddit_gen"] = True
        elif action == "comment_reddit":
            if not params.get("post_url") or not params.get("text"):
                return None
        elif action == "dm_twitter":
            if not params.get("username") or not params.get("text"):
                return None
        elif action == "search_twitter":
            if not params.get("query"):
                params["query"] = "AI agent solana developer"
        elif action == "search_profiles":
            if not params.get("query"):
                params["query"] = "AI agent developer solana web3"
        elif action == "search_groups":
            if not params.get("platform"):
                params["platform"] = "telegram"
        elif action == "scrape_followers":
            if not params.get("competitor"):
                params["competitor"] = random.choice(["JupiterExchange", "RunPod", "solaboratory"])
        elif action == "write_blog":
            if not params.get("topic"):
                params["topic"] = random.choice([
                    "How to monetize your AI agent with MAXIA",
                    "GPU rental at cost: why MAXIA charges 0% markup",
                    "14 chains, 1 marketplace: MAXIA multi-chain architecture",
                ])
        elif action == "watch_prices":
            pass
        elif action == "analyze_trends":
            pass
        # Les actions sans params requis
        elif action in ("reply_mentions", "manage_dms", "detect_opportunities",
                        "get_mentions", "check_ab", "comment_github_ai",
                        "clean_screenshots", "list_services", "ab_test"):
            pass
        return params

    def _reset_daily_counter(self):
        today = time.strftime("%Y-%m-%d")
        if self._daily_actions["date"] != today:
            self._daily_actions = {"date": today, "count": 0}


# ══════════════════════════════════════════
# Main
# ══════════════════════════════════════════

async def main():
    if not CEO_API_KEY:
        _log("ERREUR: CEO_API_KEY non configure dans .env")
        sys.exit(1)

    ceo = CEOLocal()

    # Verifier la connexion VPS
    health = await ceo.vps.health()
    if health.get("healthy"):
        _log("[CEO Local] VPS connecte et en bonne sante")
    else:
        _log(f"[CEO Local] VPS indisponible: {health}")
        _log("  Demarrage quand meme (retry automatique)")

    # Verifier Ollama
    try:
        test = await call_ollama("Dis 'ok' en un mot.", max_tokens=10)
        _log(f"[CEO Local] Ollama OK: {test.strip()[:30]}")
    except Exception as e:
        _log(f"[CEO Local] Ollama indisponible: {e}")
        _log("  Fallback Mistral sera utilise")

    try:
        await ceo.run()
    except KeyboardInterrupt:
        _log("[CEO Local] Arret demande")
        ceo.stop()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
