"""CEO Sub-Agents — 17 autonomous sub-agents.

WATCHDOG, RADAR, ORACLE, FAILOVER, MICRO-WALLET, TESTIMONIAL,
RESPONDER, GHOST-WRITER, COLLECTOR, EXECUTOR, WEB-DESIGNER,
DEPLOYER, NEGOTIATOR, COMPLIANCE, PARTNERSHIP, ANALYTICS,
CRISIS-MANAGER, SCOUT.

Extracted from ceo_maxia.py (S34 split).
"""
import logging
import asyncio, json, time, os
from datetime import datetime, date

logger = logging.getLogger(__name__)

from agents.ceo_llm import (
    _cfg, _call_groq, _call_anthropic, _pj, _ceo_private,
    alert_rouge, alert_info, _pending_decisions,
    CEO_IDENTITY, URL, MAXIA_URL, GROQ_MODEL, SONNET_MODEL, OPUS_MODEL,
    FOUNDER_NAME, COMPANY, PRODUCT, PHASE, VISION,
    _gpu_cheapest, MIN_BUDGET_VERT,
    llm_router, Tier,
    GROQ_API_KEY, ANTHROPIC_API_KEY, DISCORD_WEBHOOK_URL,
    MAX_PROSPECTS_DAY, MAX_TWEETS_DAY, HUNTER_MIN_CONVERSION,
    BASE_BUDGET_VERT,
)
from agents.ceo_memory import Memory, HEALTH_ENDPOINTS


async def watchdog_health_check() -> dict:
    """Test ALL endpoints and return status report."""
    import httpx
    from core.http_client import get_http_client
    results = {}
    ok_count = 0
    fail_count = 0

    from core.config import PORT
    admin_key = os.getenv("ADMIN_KEY", "")
    client = get_http_client()
    for name, endpoint in HEALTH_ENDPOINTS.items():
        try:
            headers = {}
            if name == "ceo_status":
                headers["X-Admin-Key"] = admin_key
            r = await client.get(f"http://127.0.0.1:{PORT}{endpoint}", headers=headers, timeout=20)
            is_ok = r.status_code == 200
            results[name] = {
                "status": "OK" if is_ok else f"HTTP {r.status_code}",
                "ok": is_ok,
            }
            if is_ok:
                ok_count += 1
            else:
                fail_count += 1
        except Exception as e:
            results[name] = {"status": f"ERROR: {str(e)[:80]}", "ok": False}
            fail_count += 1

    report = {
        "total": len(HEALTH_ENDPOINTS),
        "ok": ok_count,
        "failed": fail_count,
        "endpoints": results,
    }

    # Alert on Discord if failures
    if fail_count > 0:
        failed_list = [f"❌ {n}: {r['status']}" for n, r in results.items() if not r["ok"]]
        try:
            from infra.alerts import alert_system
            await alert_system(
                f"⚠️ WATCHDOG: {fail_count} endpoints DOWN",
                f"{ok_count}/{len(HEALTH_ENDPOINTS)} OK\n" + "\n".join(failed_list)
            )
        except Exception:
            pass
    else:
        logger.info("WATCHDOG health check: %s/%s OK", ok_count, len(HEALTH_ENDPOINTS))

    return report


async def watchdog_check_service(service: str) -> bool:
    endpoints = {
        "swap": "/api/public/crypto/tokens", "stocks": "/api/public/stocks",
        "gpu": "/api/public/gpu/tiers", "image": "/api/public/image/models",
        "prices": "/api/public/crypto/prices", "scraper": "/api/public/image/models",
        "monitor": "/api/public/wallet-monitor/alerts",
    }
    ep = endpoints.get(service.lower())
    if not ep:
        return True
    try:
        import httpx
        from core.http_client import get_http_client
        c = get_http_client()
        r = await c.get(f"https://{URL}{ep}", timeout=10)
        return r.status_code == 200
    except Exception:
        return False


async def watchdog_self_heal(source: str, error: str, memory: Memory):
    """WATCHDOG detecte erreur recurrente, Sonnet propose un patch."""
    memory.log_error(source, error)
    # Verifier si erreur recurrente (>3 fois)
    err = next((e for e in memory._data["erreurs_recurrentes"] if e["source"] == source), None)
    if not err or err.get("count", 0) < 3 or err.get("patch_proposed"):
        return

    logger.warning("WATCHDOG erreur recurrente detectee: %s (%sx)", source, err["count"])
    prompt = (
        f"L'API MAXIA a une erreur recurrente.\n"
        f"Source: {source}\nErreur: {error}\nOccurrences: {err['count']}\n\n"
        f"Analyse l'erreur et propose un correctif Python en 1-5 lignes.\n"
        f"Si c'est un changement de format API, propose le nouveau parsing.\n"
        f"Si c'est un timeout, propose d'augmenter le timeout.\n"
        f"Si c'est un DNS, propose un fallback.\n\n"
        f"JSON: {{\"diagnostic\": \"...\", \"patch\": \"code Python\", \"fichier\": \"nom.py\", \"urgence\": \"haute|moyenne|basse\"}}"
    )
    # Router: MID pour le diagnostic (raisonnement moyen, pas besoin de Claude)
    if llm_router:
        result = _pj(await llm_router.call(prompt, tier=Tier.MID, system="Tu es un debugger Python expert.", max_tokens=500))
    else:
        result = _pj(await _call_anthropic(SONNET_MODEL, "Tu es un debugger Python expert.", prompt))
    if result and result.get("patch"):
        memory.log_patch(source, json.dumps(result))
        err["patch_proposed"] = True
        memory.save()
        await alert_rouge(
            f"Self-Healing: {source}",
            f"Erreur: {error} ({err['count']}x)\n"
            f"Diagnostic: {result.get('diagnostic','')}\n"
            f"Fichier: {result.get('fichier','')}\n"
            f"Patch: ```{result.get('patch','')}```\n"
            f"Urgence: {result.get('urgence','')}",
            deadline_h=24,
        )


# ══════════════════════════════════════════
# RADAR + MARKET PULSE — Intelligence On-Chain
# ══════════════════════════════════════════

RADAR_CATEGORIES = {
    "ai": ["RENDER", "PYTH"],
    "meme": ["BONK", "WIF", "TRUMP"],
    "defi": ["JUP", "RAY", "ORCA"],
    "l1": ["SOL", "ETH", "BTC"],
    "stable": ["USDC", "USDT"],
}

# Seuils de detection
RADAR_PRICE_SPIKE = 0.15    # +15% = spike
RADAR_PRICE_CRASH = -0.15   # -15% = crash
RADAR_VOLUME_SURGE = 0.40   # +40% volume = surge


async def radar_scan(memory: Memory) -> list:
    """Market Pulse : scanne prix + detecte tendances via Helius DAS."""
    alerts = []
    helius_key = _cfg("HELIUS_API_KEY")
    if not helius_key:
        return alerts

    try:
        import httpx
        rpc = f"https://mainnet.helius-rpc.com/?api-key={helius_key}"

        # Recuperer les prix actuels de tous les tokens
        from trading.price_oracle import get_prices
        current_prices = {}
        try:
            all_prices = await get_prices()
            for sym, data in all_prices.items():
                if isinstance(data, dict):
                    current_prices[sym] = data.get("price", 0)
        except ImportError:
            # Mode standalone — utiliser getAsset directement
            pass

        if not current_prices:
            return alerts

        # Comparer avec les prix d'il y a 2h (6 cycles)
        prev_kpi = memory._data.get("kpi", [])
        prev_prices = {}
        if len(prev_kpi) >= 3:
            # Chercher les prix dans les KPI precedents
            for kpi in reversed(prev_kpi[-6:]):
                if kpi.get("prices"):
                    prev_prices = kpi["prices"]
                    break

        # Detecter les mouvements significatifs
        for cat_name, tokens in RADAR_CATEGORIES.items():
            cat_changes = []
            for token in tokens:
                curr = current_prices.get(token, 0)
                prev = prev_prices.get(token, 0)
                if curr > 0 and prev > 0:
                    change = (curr - prev) / prev
                    cat_changes.append(change)

                    # Spike individuel
                    if change >= RADAR_PRICE_SPIKE:
                        alert = {
                            "type": "price_spike",
                            "details": f"{token} +{change:.0%} ({prev:.4f} -> {curr:.4f})",
                            "token": token, "category": cat_name, "change": change,
                            "action": f"GHOST-WRITER: tweet about {token} pump. HUNTER: target {token} holders.",
                        }
                        alerts.append(alert)

                    # Crash individuel
                    elif change <= RADAR_PRICE_CRASH:
                        alert = {
                            "type": "price_crash",
                            "details": f"{token} {change:.0%} ({prev:.4f} -> {curr:.4f})",
                            "token": token, "category": cat_name, "change": change,
                            "action": f"GHOST-WRITER: 'buying the dip' content. SOL-TREASURY: reduce exposure.",
                        }
                        alerts.append(alert)

            # Surge de categorie (moyenne des tokens de la categorie)
            if cat_changes:
                avg_change = sum(cat_changes) / len(cat_changes)
                if avg_change >= RADAR_VOLUME_SURGE:
                    alert = {
                        "type": "category_surge",
                        "details": f"Category '{cat_name}' avg +{avg_change:.0%}",
                        "category": cat_name, "change": avg_change,
                        "action": f"GHOST-WRITER: thread about {cat_name} tokens trending. DEPLOYER: blog post.",
                    }
                    alerts.append(alert)

        # Sauvegarder les prix actuels dans le KPI pour comparaison future
        if memory._data.get("kpi"):
            memory._data["kpi"][-1]["prices"] = current_prices

    except Exception as e:
        logger.error("RADAR scan error: %s", e)

    for alert in alerts:
        memory.log_radar_alert(alert.get("type", ""), alert.get("details", ""))
        logger.info("RADAR %s: %s", alert["type"], alert["details"])

    return alerts



# ══════════════════════════════════════════
# ORACLE — Social Listening (Intelligence Externe)
# ══════════════════════════════════════════

ORACLE_SOURCES = {
    "dexscreener": "https://api.dexscreener.com/latest/dex/tokens/",
    "solana_fm": "https://api.solana.fm/v0/tokens/trending",
    "github_trending": "https://api.github.com/search/repositories?q=solana+AI&sort=stars&order=desc&per_page=5",
}

# Comptes influents Solana a surveiller (via profils publics)
ORACLE_INFLUENCERS = ["solana", "JupiterExchange", "HeliusLabs", "OndoFinance", "tensor_hq"]


async def oracle_scan_trends(memory: Memory) -> list:
    """Scanne les tendances externes : DexScreener, GitHub, influenceurs."""
    trends = []
    try:
        import httpx
        from core.http_client import get_http_client

        # 1. DexScreener — tokens Solana en tendance
        try:
            c = get_http_client()
            resp = await c.get("https://api.dexscreener.com/token-boosts/latest/v1", timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                boosts = data if isinstance(data, list) else data.get("boosts", data.get("tokens", []))
                sol_boosts = [b for b in boosts[:20] if b.get("chainId") == "solana"] if isinstance(boosts, list) else []
                if sol_boosts:
                    trends.append({
                        "source": "dexscreener",
                        "type": "trending_tokens",
                        "details": f"{len(sol_boosts)} Solana tokens trending on DexScreener",
                        "tokens": [b.get("tokenAddress", "")[:16] for b in sol_boosts[:5]],
                    })
        except Exception as e:
            pass

        # 2. GitHub trending — repos AI + Solana
        try:
            c = get_http_client()
            resp = await c.get(
                "https://api.github.com/search/repositories",
                params={"q": "solana AI agent created:>2026-03-01", "sort": "stars", "per_page": 5},
            )
            if resp.status_code == 200:
                repos = resp.json().get("items", [])
                hot_repos = [r for r in repos if r.get("stargazers_count", 0) > 10]
                if hot_repos:
                    trends.append({
                        "source": "github",
                        "type": "hot_repos",
                        "details": f"{len(hot_repos)} hot Solana AI repos on GitHub",
                        "repos": [{"name": r["full_name"], "stars": r["stargazers_count"]} for r in hot_repos[:3]],
                    })
        except Exception:
            pass

        # 3. Detecter les narratifs chauds
        # Combiner les signaux
        if trends:
            narratifs = set()
            for t in trends:
                details = t.get("details", "").lower()
                if "ai" in details or "gpu" in details:
                    narratifs.add("AI")
                if "meme" in details or "trump" in details or "bonk" in details:
                    narratifs.add("MEME")
                if "defi" in details or "swap" in details:
                    narratifs.add("DEFI")

            if narratifs:
                trends.append({
                    "source": "oracle_analysis",
                    "type": "hot_narrative",
                    "details": f"Hot narratives: {', '.join(narratifs)}",
                    "narratives": list(narratifs),
                    "action": f"GHOST-WRITER should create content about {', '.join(narratifs)}",
                })

    except Exception as e:
        logger.error("ORACLE scan error: %s", e)

    for t in trends:
        memory.log_radar_alert(f"oracle_{t.get('type', '')}", t.get("details", ""))

    if trends:
        logger.info("ORACLE %s tendances detectees", len(trends))

    return trends


# ══════════════════════════════════════════
# FAILOVER — Bascule automatique des APIs
# ══════════════════════════════════════════

FAILOVER_RPC = [
    {"name": "helius", "url_env": "HELIUS_API_KEY", "url_tpl": "https://mainnet.helius-rpc.com/?api-key={key}"},
    {"name": "quicknode", "url_env": "QUICKNODE_URL", "url_tpl": "{key}"},
    {"name": "alchemy", "url_env": "ALCHEMY_API_KEY", "url_tpl": "https://solana-mainnet.g.alchemy.com/v2/{key}"},
    {"name": "public", "url_env": "", "url_tpl": "https://api.mainnet-beta.solana.com"},
]

FAILOVER_LLM = [
    {"name": "groq", "fn": "_call_groq"},
    {"name": "anthropic_sonnet", "fn": "_call_anthropic_sonnet"},
    {"name": "local_rules", "fn": "_call_local_rules"},
]

FAILOVER_ALERTS = [
    {"name": "discord_webhook", "env": "DISCORD_WEBHOOK_URL"},
    {"name": "telegram", "env": "TELEGRAM_BOT_TOKEN"},
]

_active_rpc_index = 0
_rpc_failures: dict = {}  # name -> failure_count


async def failover_get_rpc() -> str:
    """Retourne le RPC actif, bascule si le principal est down."""
    global _active_rpc_index

    for i in range(len(FAILOVER_RPC)):
        idx = (_active_rpc_index + i) % len(FAILOVER_RPC)
        provider = FAILOVER_RPC[idx]
        name = provider["name"]

        # Construire l'URL
        if provider["url_env"]:
            key = os.getenv(provider["url_env"], "")
            if not key:
                continue
            url = provider["url_tpl"].format(key=key)
        else:
            url = provider["url_tpl"]

        # Tester le RPC
        try:
            import httpx
            from core.http_client import get_http_client
            c = get_http_client()
            resp = await c.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "getHealth"}, timeout=5)
            if resp.status_code == 200:
                result = resp.json().get("result")
                if result == "ok" or result is not None:
                    if idx != _active_rpc_index:
                        old_name = FAILOVER_RPC[_active_rpc_index]["name"]
                        logger.info("FAILOVER RPC bascule: %s -> %s", old_name, name)
                        _active_rpc_index = idx
                    return url
        except Exception:
            _rpc_failures[name] = _rpc_failures.get(name, 0) + 1

    # Tout est down — fallback public
    logger.warning("FAILOVER Tous les RPC down — utilisation du RPC public")
    return "https://api.mainnet-beta.solana.com"


async def failover_send_alert(message: str):
    """Envoie une alerte via Telegram prive (donnees sensibles)."""
    await _ceo_private(message)


# ══════════════════════════════════════════
# MICRO WALLET — Petty Cash pour experimentations
# ══════════════════════════════════════════

MICRO_WALLET_ADDRESS = _cfg("MICRO_WALLET_ADDRESS")
MICRO_WALLET_PRIVKEY = _cfg("MICRO_WALLET_PRIVKEY")
MICRO_MAX_PER_TX = 0.01       # SOL max par transaction
MICRO_MAX_PER_DAY = 0.05      # SOL max par jour
MICRO_ALERT_LOW = 0.02        # SOL — alerte si solde bas


class MicroWallet:
    """Wallet de micro-depenses pour les experimentations du CEO."""

    def __init__(self):
        self._spent_today = 0.0
        self._spent_date = ""
        self._log: list = []

    def _reset_daily(self):
        today = date.today().isoformat()
        if self._spent_date != today:
            self._spent_date = today
            self._spent_today = 0.0

    def can_spend(self, amount: float) -> tuple:
        """Verifie si la depense est autorisee."""
        self._reset_daily()
        if amount > MICRO_MAX_PER_TX:
            return False, f"Max {MICRO_MAX_PER_TX} SOL par tx (demande: {amount})"
        if self._spent_today + amount > MICRO_MAX_PER_DAY:
            remaining = MICRO_MAX_PER_DAY - self._spent_today
            return False, f"Budget jour epuise (reste: {remaining:.4f} SOL)"
        if not MICRO_WALLET_ADDRESS or not MICRO_WALLET_PRIVKEY:
            return False, "Micro wallet non configure"
        return True, "OK"

    async def spend(self, amount: float, reason: str, memory=None) -> dict:
        """Execute une micro-depense."""
        ok, msg = self.can_spend(amount)
        if not ok:
            return {"success": False, "error": msg}

        # En production : executer la transaction Solana
        # from blockchain.solana_tx import send_sol_transfer
        # result = await send_sol_transfer(...)

        self._spent_today += amount
        entry = {
            "ts": datetime.utcnow().isoformat(),
            "amount": amount,
            "reason": reason[:200],
        }
        self._log.append(entry)
        self._log = self._log[-100:]

        if memory:
            memory.log_decision("vert", f"MICRO: {amount} SOL — {reason}", "CEO experiment", "MICRO")
            memory._data["spent_sol"] = memory._data.get("spent_sol", 0) + amount
            memory.save()

        logger.info("MICRO %s SOL — %s", amount, reason)
        return {"success": True, "amount": amount, "reason": reason}

    async def get_balance(self) -> float:
        """Recupere le solde du micro wallet."""
        if not MICRO_WALLET_ADDRESS:
            return 0
        try:
            from blockchain.solana_tx import get_sol_balance
            return await get_sol_balance(MICRO_WALLET_ADDRESS)
        except ImportError:
            return 0

    def get_stats(self) -> dict:
        self._reset_daily()
        return {
            "address": MICRO_WALLET_ADDRESS[:16] + "..." if MICRO_WALLET_ADDRESS else "non configure",
            "spent_today": self._spent_today,
            "max_per_day": MICRO_MAX_PER_DAY,
            "remaining_today": MICRO_MAX_PER_DAY - self._spent_today,
            "recent_expenses": self._log[-5:],
        }


micro_wallet = MicroWallet()


# ══════════════════════════════════════════
# TESTIMONIAL — Social Proof
# ══════════════════════════════════════════

async def testimonial_request(user: str, tx_sig: str, service: str, memory: Memory):
    """Apres une transaction reussie, sollicite un feedback."""
    msg = (
        f"Hey! Your {service} went through (tx: {tx_sig[:16]}...). "
        f"Quick feedback? What did you use MAXIA for? "
        f"Reply anything — helps us improve. {URL}"
    )
    # En production : envoyer via le canal du user (memo, DM, etc.)
    memory.log_testimonial(user, tx_sig, "requested", False)
    return msg


async def testimonial_process(user: str, feedback: str, memory: Memory) -> dict:
    """Analyse le feedback et propose de publier si positif."""
    prompt = (
        f"Feedback de {user}: \"{feedback}\"\n\n"
        f"1. Est-ce positif, neutre ou negatif ?\n"
        f"2. Si positif, redige un tweet de temoignage (max 200 chars)\n"
        f"JSON: {{\"sentiment\": \"positif|neutre|negatif\", \"tweet\": \"...\" ou null}}"
    )
    # Router: LOCAL pour analyse feedback (0 cout)
    if llm_router:
        result = _pj(await llm_router.call(prompt, tier=Tier.LOCAL, system="Tu analyses des feedbacks.", max_tokens=300))
    else:
        result = _pj(await _call_groq("Tu analyses des feedbacks.", prompt))
    if result.get("sentiment") == "positif" and result.get("tweet"):
        memory.log_testimonial(user, "", feedback, False)
        return {"publish": True, "tweet": result["tweet"]}
    return {"publish": False}


# ══════════════════════════════════════════
# RESPONDER
# ══════════════════════════════════════════

RESPONDER_PROMPT = CEO_IDENTITY + """
Mode RESPONDER. Reponds au message entrant.
Intention: question_technique|prospect|plainte|spam|partenaire|investisseur|conversation
Ton adapte au canal. Ne vends jamais agressivement.
Plainte: excuse+verifie+resous. Investisseur/partenaire: alerte ROUGE.
JSON: {intention, reponse, action_interne, alerte_fondateur, priorite}"""


async def respond(canal: str, user: str, msg: str, memory: Memory) -> dict:
    if memory.is_stopped():
        return {"intention": "emergency_stop", "reponse": "Service temporarily paused. Back soon.", "alerte_fondateur": False}

    prev = [c for c in memory._data["conversations"] if c.get("user") == user][-3:]
    ctx = (
        f"CANAL: {canal}\nUSER: {user}\nMESSAGE: {msg}\n"
        f"HISTORIQUE:\n{json.dumps(prev, indent=1, default=str) if prev else '(Premier contact)'}\n"
        f"MAXIA: 107 tokens, 5000+ paires, GPU {_gpu_cheapest}, audit $9.99, AI-to-AI marketplace\nURL: {URL}\n"
        f"TESTIMONIALS: {len(memory._data.get('testimonials', []))} recus"
    )
    # Router: FAST pour les reponses (besoin de qualite, mais pas strategique)
    if llm_router:
        raw = await llm_router.call(ctx, tier=Tier.FAST, system=RESPONDER_PROMPT, max_tokens=500)
    else:
        raw = await _call_groq(RESPONDER_PROMPT, ctx)
    data = _pj(raw)
    if not data and raw:
        data = {"intention": "conversation", "reponse": raw, "alerte_fondateur": False}
    if not data:
        return {"intention": "spam", "reponse": "", "alerte_fondateur": False}

    memory.log_conversation(canal, user, msg, data.get("reponse", ""), data.get("intention", ""))

    if data.get("alerte_fondateur"):
        await alert_rouge(f"{data.get('intention','')} de {user} ({canal})", f"Msg: {msg[:200]}\nRep: {data.get('reponse','')[:200]}", deadline_h=2)

    return data


# ══════════════════════════════════════════
# GHOST-WRITER avec validation WATCHDOG
# ══════════════════════════════════════════

async def ghost_write(content_type: str, sujet: str, canal: str, memory: "Memory" = None) -> dict:
    # A/B testing : si un test est actif pour ce type de contenu, utiliser le variant
    ab_variant_key = None
    ab_test_name = f"ghost_{content_type}_{canal}"
    extra_instruction = ""
    if memory:
        test = memory._data.get("ab_tests", {}).get(ab_test_name)
        if test and test.get("status") == "active":
            ab_variant_key, variant_content = memory.get_ab_variant(ab_test_name)
            if variant_content:
                extra_instruction = f"\nSTYLE OBLIGATOIRE: {variant_content}\n"
                logger.info("GHOST-WRITER A/B test actif: %s variant %s", ab_test_name, ab_variant_key)

    prompt = (
        f"Cree un {content_type} pour {canal}: {sujet}\n"
        f"CIBLE : dev 26-34 ans qui a un agent IA mais 0 revenus. Parle comme un dev.\n"
        f"MESSAGE CLE : ton agent peut GAGNER de l'USDC sur MAXIA. POST /sell = live.\n"
        f"TON : technique, code, faits. PAS de marketing creux. PAS de 'revolutionary'.\n"
        f"INCLURE : maxiaworld.app ou github.com/MAXIAWORLD/demo-agent\n"
        f"{extra_instruction}"
        f"Max 280 chars si tweet. Pas de emoji excessifs (max 1-2).\n"
        f"JSON: {{type, titre, contenu, services_mentionnes: [], hashtags, cta}}"
    )
    # Router: FAST pour la redaction de contenu
    if llm_router:
        data = _pj(await llm_router.call(prompt, tier=Tier.FAST, system=CEO_IDENTITY + "\nMode GHOST-WRITER.", max_tokens=500))
    else:
        data = _pj(await _call_groq(CEO_IDENTITY + "\nMode GHOST-WRITER.", prompt))
    if not data:
        return {}
    # Tag le variant A/B pour tracking
    if ab_variant_key:
        data["ab_test"] = ab_test_name
        data["ab_variant"] = ab_variant_key
    # Validation WATCHDOG
    for svc in data.get("services_mentionnes", []):
        if not await watchdog_check_service(svc):
            logger.warning("GHOST-WRITER BLOQUE — %s DOWN", svc)
            return {"blocked": True, "reason": f"{svc} is DOWN"}
    return data


# ══════════════════════════════════════════
# COLLECTE
# ══════════════════════════════════════════

async def collect() -> dict:
    """Collecte les metriques reelles depuis la DB et la memoire CEO."""
    try:
        from core.database import db
        stats = await db.get_stats()
        mkt = await db.get_marketplace_stats()
        activity = await db.get_activity(100)
        return {
            "ts": datetime.utcnow().isoformat(),
            "rev_24h": stats.get("volume_24h", 0),
            "rev_total": stats.get("total_revenue", 0),
            "clients": mkt.get("agents_registered", 0),
            "clients_actifs": mkt.get("agents_registered", 0),
            "swaps": len([a for a in activity if a.get("purpose") == "swap"]),
            "volume": stats.get("total_revenue", 0),
            "gpu": len([a for a in activity if a.get("purpose") == "gpu_auction"]),
            "ia_reqs": mkt.get("total_transactions", 0),
            "prix_live": 0, "prix_total": 25,
            "prospects": 0, "taux_rep": 0,
            "msgs_in": 0, "msgs_out": 0,
            "sol": 0, "usdc": 0, "erreurs": [],
            "services": mkt.get("services_listed", 0),
            "commission_total": mkt.get("total_commission_usdc", 0),
        }
    except Exception as e:
        logger.error("collect() error: %s", e)
        return {
            "ts": datetime.utcnow().isoformat(),
            "rev_24h": 0, "rev_total": 0, "clients": 0, "clients_actifs": 0,
            "swaps": 0, "volume": 0, "gpu": 0, "ia_reqs": 0,
            "prix_live": 0, "prix_total": 25,
            "prospects": 0, "taux_rep": 0,
            "msgs_in": 0, "msgs_out": 0,
            "sol": 0, "usdc": 0, "erreurs": [],
        }


# ══════════════════════════════════════════
# EXECUTION avec verrous de securite
# ══════════════════════════════════════════

async def execute(decisions: list, memory: Memory):
    if memory.is_stopped():
        logger.critical("Emergency stop — decisions bloquees")
        await alert_rouge("Emergency Stop actif", "Toutes les decisions sont bloquees. Revenue: $0. Reset manuel requis.", deadline_h=1)
        return

    # Si le CEO local est actif, le VPS skip le marketing (eviter double-post)
    try:
        from agents.ceo_api import is_local_ceo_active, local_ceo_did_action
        if is_local_ceo_active():
            _marketing = {"GHOST-WRITER", "HUNTER"}
            decisions = [d for d in decisions if d.get("cible", "").upper() not in _marketing]
            if not decisions:
                logger.info("VPS skip marketing — CEO local actif")
                return
    except ImportError:
        pass

    from agents.ceo_executor import execute_decision

    VALID_CIBLES = {"GHOST-WRITER", "HUNTER", "SCOUT", "WATCHDOG", "SOL-TREASURY", "RESPONDER", "RADAR", "TESTIMONIAL", "DEPLOYER", "FONDATEUR", "NEGOTIATOR", "COMPLIANCE", "PARTNERSHIP", "ANALYTICS", "CRISIS-MANAGER"}
    VAGUE_PATTERNS = ["maximiser", "ameliorer", "optimiser", "augmenter les", "renforcer", "assurer le", "garantir"]
    CONCRETE_KW = ["tweet", "post", "switch", "contact", "deploy", "blog", "prix", "fee", "canal", "wallet", "scan", "check", "adjust", "send", "memo", "thread", "article"]

    for dec in decisions:
        action = dec.get("action", "")
        cible = dec.get("cible", "").upper()
        prio = dec.get("priorite", "moyenne")

        # Kill switch granulaire — skip les agents desactives
        if cible and memory.is_agent_disabled(cible):
            logger.info("Decision SKIPPED — %s est desactive", cible)
            continue

        # Fix unknown cible — try to map it to closest valid one
        if cible and cible not in VALID_CIBLES:
            cible_map = {
                "CEO": "WATCHDOG", "MAXIA": "WATCHDOG", "MARKETING": "GHOST-WRITER",
                "CONTENT": "GHOST-WRITER", "PROSPECTION": "HUNTER", "BUDGET": "SOL-TREASURY",
                "TREASURY": "SOL-TREASURY", "TARIF": "SOL-TREASURY", "MONITORING": "WATCHDOG",
                "SOCIAL": "GHOST-WRITER", "TWITTER": "GHOST-WRITER", "DISCORD": "RESPONDER",
                "TELEGRAM": "RESPONDER", "INTELLIGENCE": "RADAR", "FEEDBACK": "TESTIMONIAL",
                "IA-PROSPECTION": "SCOUT", "AI-AGENTS": "SCOUT", "RECRUTEMENT-IA": "SCOUT",
                "AGENTS": "SCOUT", "OLAS": "SCOUT", "AUTONOLAS": "SCOUT",
                "PRIX": "NEGOTIATOR", "PRICING": "NEGOTIATOR", "NEGOCIATION": "NEGOTIATOR",
                "AML": "COMPLIANCE", "SANCTIONS": "COMPLIANCE", "KYC": "COMPLIANCE", "FRAUDE": "COMPLIANCE",
                "PARTENARIAT": "PARTNERSHIP", "PARTENAIRES": "PARTNERSHIP", "INTEGRATION": "PARTNERSHIP",
                "METRIQUES": "ANALYTICS", "REPORTING": "ANALYTICS", "RAPPORT": "ANALYTICS", "KPI": "ANALYTICS",
                "CRISE": "CRISIS-MANAGER", "INCIDENT": "CRISIS-MANAGER", "URGENCE": "CRISIS-MANAGER",
            }
            mapped = cible_map.get(cible)
            if mapped:
                logger.info("Cible %s remappee -> %s", cible, mapped)
                cible = mapped
                dec["cible"] = mapped
            else:
                logger.warning("Decision REJETEE — cible inconnue: %s", cible)
                continue

        # Translate vague actions into concrete ones via LLM re-prompt (LOCAL tier)
        if any(v in action.lower() for v in VAGUE_PATTERNS) and not any(kw in action.lower() for kw in CONCRETE_KW):
            logger.info("Action vague detectee, re-prompt: %s", action[:80])
            try:
                _reprompt_system = "Tu es un assistant qui transforme des objectifs vagues en actions concretes pour un sous-agent."
                _reprompt_user = (
                    f"Sous-agent cible: {cible}\n"
                    f"Objectif vague: {action}\n\n"
                    f"Transforme en UNE action concrete executable par {cible}.\n"
                    f"Exemples d'actions concretes:\n"
                    f"- GHOST-WRITER: 'blog: MAXIA offre les frais les plus bas sur Solana' (PAS de tweet, Twitter delegue au CEO local)\n"
                    f"- HUNTER: 'contact wallet ABC123 via solana_memo'\n"
                    f"- SOL-TREASURY: 'adjust swap fee to 0.05%'\n"
                    f"- WATCHDOG: 'check service swap health'\n"
                    f"- DEPLOYER: 'deploy blog: Why MAXIA is cheapest'\n"
                    f"- RADAR: 'scan trending tokens volume > 100k'\n"
                    f"- RESPONDER: 'send welcome message to new users on discord'\n\n"
                    f"Reponds UNIQUEMENT l'action concrete, rien d'autre. Pas de JSON, pas d'explication."
                )
                # Router: tier LOCAL pour la concretisation (0 cout)
                if llm_router:
                    concrete = await llm_router.call(
                        _reprompt_user, tier=Tier.LOCAL,
                        system=_reprompt_system, max_tokens=150,
                    )
                else:
                    concrete = await _call_groq(
                        _reprompt_system, _reprompt_user, max_tokens=150,
                    )
                if concrete and concrete.strip():
                    concrete = concrete.strip().strip('"').strip("'")
                    logger.info("Action concretisee: %s", concrete[:100])
                    action = concrete
                    dec["action"] = concrete
                else:
                    logger.warning("Re-prompt echoue, action ignoree: %s", action[:80])
                    continue
            except Exception as e:
                logger.error("Re-prompt LLM error: %s, action ignoree", e)
                continue

        # Verifier le budget avant execution
        if prio == "orange":
            budget = memory.get_budget_vert()
            if memory._data.get("revenue_usd", 0) == 0 and budget < MIN_BUDGET_VERT * 2:
                logger.info("Decision orange BLOQUEE (budget trop bas: %.4f)", budget)
                continue

        logger.info("-> %s [%s] : %s", cible, prio, action[:100])
        memory.log_decision(prio, action, "CEO directive", cible)

        if cible == "FONDATEUR" and prio == "haute":
            await alert_rouge(action[:80], action, deadline_h=2)

        # Actually execute the decision
        try:
            result = await execute_decision(dec, memory)
            if result.get("executed"):
                logger.info("EXECUTED: %s -> %s", cible, result.get("detail", "ok"))
            else:
                reason = result.get("reason", "unknown")
                logger.info("NOT EXECUTED: %s -> %s", cible, reason)
        except Exception as e:
            logger.error("Execution error for %s: %s", cible, e)


# ══════════════════════════════════════════
# WEB-DESIGNER — Config JSON pour le frontend
# ══════════════════════════════════════════

async def web_designer_update_config(memory: Memory) -> dict:
    """Genere un fichier JSON de config que le frontend lit.
    Le CEO peut changer textes, prix, annonces sans toucher au code."""
    d = memory._data
    testimonials = [t for t in d.get("testimonials", []) if t.get("published")]

    config = {
        "updated_at": datetime.utcnow().isoformat(),
        "announcement": "",  # Sera rempli par le CEO
        "hero": {
            "title": "MAXIA",
            "subtitle": "AI Marketplace on Solana",
            "badges": [
                f"{len(d.get('langues', ['en']))} Languages",
                "14 Chains",
                "107 Tokens", "5000+ Pairs", "25 Stocks", "7 GPU", "46 MCP Tools",
            ],
        },
        "stats": {
            "clients": d.get("clients", 0),
            "revenue": d.get("revenue_usd", 0),
            "transactions": d.get("responses", 0),
            "testimonials": len(testimonials),
            "prix_live": 50,
        },
        "social_proof": {
            "count": len(testimonials),
            "label": f"{len(testimonials)} verified transactions" if testimonials else "Open API — Try it free",
            "testimonials": [{"user": t["user"], "feedback": t["feedback"][:100]} for t in testimonials[-5:]],
        },
        "pricing_highlight": {
            "swap_fee": "0.01%",
            "gpu_price": _gpu_cheapest,
            "audit_price": "$4.99",
            "label": "Lowest fees in DeFi",
        },
        "cta": {
            "primary": {"text": "Try the API (Free)", "url": f"https://{URL}/api/public/register"},
            "secondary": {"text": "GitHub Demo", "url": "https://github.com/MAXIAWORLD/demo-agent"},
        },
    }

    # Le CEO peut ajouter une annonce via la boucle strategique
    radar = d.get("radar_alerts", [])
    if radar:
        last = radar[-1]
        if last.get("type") == "price_spike":
            config["announcement"] = f"Trending: {last.get('details', '')}"
        elif last.get("type") == "category_surge":
            config["announcement"] = f"Hot: {last.get('details', '')}"

    return config


async def web_designer_deploy_config(config: dict, memory: Memory) -> dict:
    """Deploie le fichier config.json sur GitHub Pages."""
    content = json.dumps(config, indent=2, default=str)
    return await deployer_push_github(
        "config.json", content,
        f"CEO auto-update config ({datetime.utcnow().strftime('%Y-%m-%d %H:%M')})",
    )


# ══════════════════════════════════════════
# DEPLOYER — Genere et deploie des pages web
# ══════════════════════════════════════════

GITHUB_TOKEN = _cfg("GITHUB_TOKEN")
GITHUB_ORG = _cfg("GITHUB_ORG", "MAXIA-AI")
GITHUB_REPO = _cfg("GITHUB_REPO", "site")
GITHUB_BRANCH = "main"

# Pages que le CEO peut creer automatiquement
DEPLOYABLE_PAGES = {
    "docs": {
        "trigger": "premier_client",
        "description": "Documentation API interactive avec exemples live",
    },
    "status": {
        "trigger": "toujours",
        "description": "Uptime, prix live 107 tokens, volume, agents actifs",
    },
    "testimonials": {
        "trigger": "3_feedbacks_positifs",
        "description": "Page de temoignages clients verifies on-chain",
    },
    "compare": {
        "trigger": "analyse_concurrence",
        "description": "Tableau comparatif fees MAXIA vs Jupiter vs Binance",
    },
    "report": {
        "trigger": "chaque_lundi",
        "description": "Rapport hebdomadaire public (volume, clients, prix)",
    },
}


async def deployer_generate_page(page_type: str, data: dict) -> str:
    """GHOST-WRITER genere une page HTML complete via Sonnet."""
    prompts = {
        "docs": (
            f"Genere une page HTML complete et moderne (dark theme, responsive) pour la documentation API de MAXIA.\n"
            f"URL de base: https://{URL}\n\n"
            f"Inclus 7 exemples de code interactifs :\n"
            f"1. POST /api/public/crypto/swap — Swap SOL to USDC\n"
            f"2. POST /api/public/gpu/rent — Rent RTX 4090\n"
            f"3. POST /api/public/scrape — Scrape a URL\n"
            f"4. POST /api/public/image/generate — Generate an image\n"
            f"5. POST /api/public/wallet-monitor/add — Monitor a wallet\n"
            f"6. POST /api/public/stocks/buy — Buy tokenized stocks\n"
            f"7. GET /api/public/crypto/prices — Get live prices\n\n"
            f"Pour chaque exemple :\n"
            f"- Montre le curl et le Python\n"
            f"- Ajoute un bouton 'Try it' qui fait un fetch() vers l'API et affiche le resultat\n"
            f"- Affiche les prix en temps reel via fetch('/api/public/crypto/prices')\n\n"
            f"Header: MAXIA API Documentation\n"
            f"Footer: 107 tokens, 5000+ pairs, 25 stocks, 7 GPU, 46 MCP tools — Live on 14 chains\n"
            f"Style: dark (#0A0E17), blue accents (#3B82F6), JetBrains Mono pour le code\n"
            f"Retourne UNIQUEMENT le HTML complet, rien d'autre."
        ),
        "status": (
            f"Genere une page HTML status dashboard pour MAXIA.\n"
            f"URL: https://{URL}\n\n"
            f"La page fait un fetch() toutes les 30s vers :\n"
            f"- /health (articles count)\n"
            f"- /api/public/crypto/prices (25 prix live)\n"
            f"- /api/public/stocks (10 actions)\n"
            f"- /api/public/gpu/tiers (GPU disponibles)\n\n"
            f"Affiche :\n"
            f"- Status: ONLINE/OFFLINE (gros indicateur vert/rouge)\n"
            f"- 25 prix live dans un tableau avec refresh auto\n"
            f"- Derniere mise a jour (timestamp)\n"
            f"- Nombre d'articles actifs\n\n"
            f"Style: dark, minimaliste, temps reel\n"
            f"Retourne UNIQUEMENT le HTML complet."
        ),
        "testimonials": (
            f"Genere une page HTML de temoignages pour MAXIA.\n"
            f"Testimonials data: {json.dumps(data.get('testimonials', []), default=str)}\n\n"
            f"Pour chaque temoignage :\n"
            f"- Avatar genere (initiales)\n"
            f"- Citation du feedback\n"
            f"- Service utilise (swap, GPU, audit...)\n"
            f"- Lien Solscan de la transaction (preuve on-chain)\n"
            f"- Date\n\n"
            f"Header: What AI Agents Say About MAXIA\n"
            f"Counter: 'X verified transactions'\n"
            f"CTA: Try MAXIA free\n"
            f"Style: dark, confiance, badges 'Verified on Solana'\n"
            f"Retourne UNIQUEMENT le HTML complet."
        ),
        "compare": (
            f"Genere une page HTML de comparaison de fees pour MAXIA.\n\n"
            f"Tableau comparatif :\n"
            f"| Service | MAXIA | Jupiter | Binance | Coinbase |\n"
            f"| Swap fee | 0.01-0.10% | 0% + slippage | 0.10% | 0.60% |\n"
            f"| Stocks | 0.05% | N/A | N/A | N/A |\n"
            f"| GPU RTX4090 | {_gpu_cheapest} | N/A | N/A | N/A |\n"
            f"| API | Gratuite | Gratuite | Payante | Payante |\n"
            f"| Prix live | 107 tokens | Oui | Oui | Oui |\n"
            f"| AI Services | 9 services | Non | Non | Non |\n\n"
            f"Mets en evidence les avantages MAXIA (vert)\n"
            f"Ajoute un calculateur : 'Combien economisez-vous avec MAXIA ?'\n"
            f"Input: volume mensuel, output: economies en $\n"
            f"Style: dark, tableaux clairs, vert pour MAXIA\n"
            f"Retourne UNIQUEMENT le HTML complet."
        ),
        "report": (
            f"Genere une page HTML de rapport hebdomadaire MAXIA.\n"
            f"Data: {json.dumps(data, default=str)}\n\n"
            f"Sections :\n"
            f"- Resume executif (2 phrases)\n"
            f"- KPI (revenus, clients, volume, swaps)\n"
            f"- Prix des 107 tokens (tableau)\n"
            f"- Top 5 swaps de la semaine\n"
            f"- Decisions du CEO cette semaine\n"
            f"- Perspectives semaine prochaine\n\n"
            f"Style: dark, professionnel, data-driven\n"
            f"Retourne UNIQUEMENT le HTML complet."
        ),
    }

    prompt = prompts.get(page_type, "")
    if not prompt:
        return ""

    # Router: MID pour la generation HTML (pas besoin de Claude pour du HTML)
    _html_system = "Tu es un expert frontend. Genere du HTML/CSS/JS complet, moderne et responsive. Retourne UNIQUEMENT le code HTML, pas de markdown, pas d'explication."
    if llm_router:
        html = await llm_router.call(prompt, tier=Tier.MID, system=_html_system, max_tokens=4000)
    else:
        html = await _call_anthropic(SONNET_MODEL, _html_system, prompt, max_tokens=4000)

    # Nettoyer si markdown
    if html.startswith("```html"):
        html = html[7:]
    if html.startswith("```"):
        html = html[3:]
    if html.endswith("```"):
        html = html[:-3]

    return html.strip()


async def deployer_push_github(filename: str, content: str, commit_msg: str) -> dict:
    """Deploie un fichier sur GitHub Pages via l'API GitHub."""
    if not GITHUB_TOKEN:
        logger.warning("DEPLOYER GITHUB_TOKEN manquant — fichier sauve localement")
        # Sauvegarder localement en fallback
        local_path = f"/tmp/maxia_pages/{filename}"
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        with open(local_path, "w") as f:
            f.write(content)
        return {"success": False, "error": "No GITHUB_TOKEN", "local": local_path}

    try:
        import httpx, base64
        from core.http_client import get_http_client

        api_url = f"https://api.github.com/repos/{GITHUB_ORG}/{GITHUB_REPO}/contents/{filename}"
        encoded = base64.b64encode(content.encode()).decode()

        headers = {
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
        }

        client = get_http_client()
        # Verifier si le fichier existe deja (pour update)
        sha = None
        try:
            resp = await client.get(api_url, headers=headers, timeout=30)
            if resp.status_code == 200:
                sha = resp.json().get("sha")
        except Exception:
            pass

        # Creer ou mettre a jour
        payload = {
            "message": commit_msg,
            "content": encoded,
            "branch": GITHUB_BRANCH,
        }
        if sha:
            payload["sha"] = sha

        resp = await client.put(api_url, headers=headers, json=payload)

        if resp.status_code in [200, 201]:
            page_url = f"https://{GITHUB_ORG.lower()}.github.io/{GITHUB_REPO}/{filename}"
            logger.info("DEPLOYER deploye: %s", page_url)
            return {"success": True, "url": page_url, "filename": filename}
        else:
            error = resp.json().get("message", resp.text[:200])
            if "Bad credentials" in error:
                if not getattr(deployer_push_github, '_cred_warned', False):
                    logger.warning("DEPLOYER GitHub token expired/invalid — blog deploy disabled until token is renewed")
                    deployer_push_github._cred_warned = True
            else:
                logger.error("DEPLOYER GitHub error: %s", error)
            return {"success": False, "error": error}

    except Exception as e:
        logger.error("DEPLOYER error: %s", e)
        return {"success": False, "error": "An error occurred"}


async def deployer_create_and_deploy(page_type: str, data: dict, memory) -> dict:
    """Pipeline complet : genere → valide → deploie."""
    logger.info("DEPLOYER creation page %s...", page_type)

    # 1. GHOST-WRITER genere
    html = await deployer_generate_page(page_type, data)
    if not html or len(html) < 100:
        return {"success": False, "error": "Generation echouee"}

    # 2. WATCHDOG valide les services mentionnes
    services_to_check = ["prices", "swap", "stocks", "gpu"]
    for svc in services_to_check:
        up = await watchdog_check_service(svc)
        if not up:
            logger.warning("DEPLOYER BLOQUE — %s DOWN, page non deployee", svc)
            return {"success": False, "error": f"Service {svc} DOWN"}

    # 3. Deployer
    filename = f"{page_type}.html"
    commit_msg = f"CEO MAXIA auto-deploy: {page_type} page ({datetime.utcnow().strftime('%Y-%m-%d %H:%M')})"
    result = await deployer_push_github(filename, html, commit_msg)

    # 4. Logger
    if result.get("success"):
        if memory:
            memory.log_decision("vert", f"DEPLOYER: {page_type} deploye -> {result.get('url','')}", "Auto-deploy", "DEPLOYER")
        logger.info("DEPLOYER OK: %s", result.get("url",""))
    else:
        logger.error("DEPLOYER echec: %s", result.get("error",""))

    return result


async def deployer_blog_post(titre: str, contenu_prompt: str, memory) -> dict:
    """Cree et deploie un article de blog."""
    prompt = (
        f"Genere une page HTML complete pour un article de blog MAXIA.\n"
        f"Titre: {titre}\n"
        f"Contenu a developper: {contenu_prompt}\n\n"
        f"Structure: Header MAXIA, titre, date, contenu technique avec code snippets,\n"
        f"CTA 'Try MAXIA API', footer avec liens.\n"
        f"Style: dark, lisible, technique.\n"
        f"Retourne UNIQUEMENT le HTML."
    )
    # Router: MID pour generation HTML
    if llm_router:
        html = await llm_router.call(prompt, tier=Tier.MID, system="Expert frontend. HTML only.", max_tokens=4000)
    else:
        html = await _call_anthropic(SONNET_MODEL, "Expert frontend. HTML only.", prompt, 4000)
    if html.startswith("```"):
        html = html.split("\n", 1)[-1]
    if html.endswith("```"):
        html = html[:-3]

    slug = titre.lower().replace(" ", "-").replace("'", "")[:50]
    filename = f"blog/{slug}.html"
    commit_msg = f"CEO MAXIA blog: {titre[:40]}"
    result = await deployer_push_github(filename, html.strip(), commit_msg)

    if result.get("success") and memory:
        memory.log_decision("vert", f"Blog deploye: {titre} -> {result.get('url','')}", "RADAR trend", "DEPLOYER")

    return result


# ══════════════════════════════════════════
# NEGOTIATOR — Negociation automatique des prix
# ══════════════════════════════════════════

async def negotiator_evaluate(buyer_agent: str, service: str, proposed_price: float, memory: Memory) -> dict:
    """Evalue et negocie automatiquement une offre de prix d'un agent IA acheteur."""
    # Recuperer le prix catalogue
    catalog_price = 0
    try:
        from core.database import db as _db
        svc = await _db.get_service_by_name(service)
        if svc:
            catalog_price = svc.get("price", 0)
    except Exception:
        pass

    if catalog_price <= 0:
        return {"accepted": False, "reason": "service_not_found", "counter_offer": None}

    # Recuperer l'historique du buyer
    buyer_history = [c for c in memory._data.get("conversations", []) if c.get("user") == buyer_agent]
    buyer_txs = len(buyer_history)

    # Regles de negociation
    min_price = catalog_price * 0.70  # jamais en dessous de 70%
    loyalty_discount = min(0.15, buyer_txs * 0.02)  # 2% par transaction passee, max 15%
    fair_price = catalog_price * (1 - loyalty_discount)

    if proposed_price >= fair_price:
        memory.log_decision("vert", f"NEGOTIATOR: accepte {proposed_price} de {buyer_agent} pour {service}", "negociation", "NEGOTIATOR")
        return {"accepted": True, "final_price": proposed_price, "discount": f"{loyalty_discount:.0%}", "reason": "price_ok"}

    if proposed_price >= min_price:
        # Contre-offre : prix moyen entre demande et catalogue
        counter = round((proposed_price + fair_price) / 2, 4)
        memory.log_decision("vert", f"NEGOTIATOR: contre-offre {counter} a {buyer_agent} (demande: {proposed_price})", "negociation", "NEGOTIATOR")
        return {"accepted": False, "counter_offer": counter, "min_acceptable": min_price, "reason": "counter_offer", "loyalty_discount": f"{loyalty_discount:.0%}"}

    # Prix trop bas — refus
    return {"accepted": False, "counter_offer": fair_price, "reason": "too_low", "message": f"Minimum acceptable: ${min_price:.2f}. Your loyalty discount: {loyalty_discount:.0%}"}


async def negotiator_bulk_deal(buyer_agent: str, services: list, memory: Memory) -> dict:
    """Negociation de pack/bundle — remise volume automatique."""
    total_catalog = 0
    details = []
    for svc_name in services:
        try:
            from core.database import db as _db
            svc = await _db.get_service_by_name(svc_name)
            price = svc.get("price", 0) if svc else 0
            total_catalog += price
            details.append({"service": svc_name, "unit_price": price})
        except Exception:
            details.append({"service": svc_name, "unit_price": 0, "error": "not_found"})

    # Remise volume : 5% pour 2 services, 10% pour 3+, 15% pour 5+, 20% pour 10+
    n = len(services)
    if n >= 10:
        discount = 0.20
    elif n >= 5:
        discount = 0.15
    elif n >= 3:
        discount = 0.10
    elif n >= 2:
        discount = 0.05
    else:
        discount = 0

    bundle_price = round(total_catalog * (1 - discount), 4)
    memory.log_decision("vert", f"NEGOTIATOR: bundle {n} services pour {buyer_agent}, remise {discount:.0%}", "negociation", "NEGOTIATOR")
    return {
        "services": details,
        "total_catalog": total_catalog,
        "discount": f"{discount:.0%}",
        "bundle_price": bundle_price,
        "savings": round(total_catalog - bundle_price, 4),
    }


# ══════════════════════════════════════════
# COMPLIANCE — Verification reglementaire
# ══════════════════════════════════════════

# Wallets sanctionnes connus (OFAC SDN list — echantillon)
SANCTIONED_PREFIXES = [
    "4wJT", "HN7c", "FhVo",  # Tornado Cash tagged
]

async def compliance_check_wallet(wallet: str, memory: Memory) -> dict:
    """Verifie si un wallet est sur liste noire/sanctions."""
    issues = []

    # Check prefixes sanctions
    for prefix in SANCTIONED_PREFIXES:
        if wallet.startswith(prefix):
            issues.append(f"wallet_prefix_match_{prefix}")

    # Check si wallet deja bloque en memoire
    blocked = memory._data.get("compliance_blocked", [])
    if wallet in blocked:
        issues.append("previously_blocked")

    # Verifier age du wallet via RPC (nouveau wallet = risque)
    try:
        import httpx
        from core.http_client import get_http_client
        helius_key = _cfg("HELIUS_API_KEY")
        if helius_key:
            client = get_http_client()
            resp = await client.post(
                f"https://mainnet.helius-rpc.com/?api-key={helius_key}",
                json={"jsonrpc": "2.0", "id": 1, "method": "getSignaturesForAddress", "params": [wallet, {"limit": 1}]},
            )
            sigs = resp.json().get("result", [])
            if not sigs:
                issues.append("no_transaction_history")
    except Exception:
        pass

    risk = "high" if issues else "low"
    cleared = len(issues) == 0

    if not cleared:
        memory.log_decision("orange", f"COMPLIANCE: wallet {wallet[:12]}... flagge — {issues}", "AML check", "COMPLIANCE")
        if "compliance_blocked" not in memory._data:
            memory._data["compliance_blocked"] = []
        if wallet not in memory._data["compliance_blocked"]:
            memory._data["compliance_blocked"].append(wallet)
            memory.save()
        # Notifier NEGOTIATOR et CRISIS-MANAGER via le bus
        agent_bus.send("COMPLIANCE", "NEGOTIATOR", "wallet_blocked", {"wallet": wallet, "issues": issues})
        agent_bus.send("COMPLIANCE", "CRISIS-MANAGER", "compliance_flag", {"wallet": wallet, "risk": risk})

    return {"wallet": wallet, "cleared": cleared, "risk": risk, "issues": issues}


async def compliance_check_transaction(amount: float, sender: str, receiver: str, memory: Memory) -> dict:
    """Verifie une transaction pour conformite AML basique."""
    flags = []

    # Seuil de transaction elevee
    if amount > 10000:
        flags.append("high_value_transaction")

    # Frequence anormale du sender (>20 tx en 24h)
    recent_decisions = [d for d in memory._data.get("decisions", [])
                        if d.get("cible") == sender and d.get("ts", "")[:10] == datetime.utcnow().isoformat()[:10]]
    if len(recent_decisions) > 20:
        flags.append("unusual_frequency")

    # Verifier les deux wallets
    sender_check = await compliance_check_wallet(sender, memory)
    receiver_check = await compliance_check_wallet(receiver, memory)
    if not sender_check["cleared"]:
        flags.append(f"sender_flagged: {sender_check['issues']}")
    if not receiver_check["cleared"]:
        flags.append(f"receiver_flagged: {receiver_check['issues']}")

    approved = len(flags) == 0
    if not approved:
        await alert_info(f"COMPLIANCE: tx ${amount} bloquee — {flags}")

    return {"approved": approved, "amount": amount, "flags": flags}


# ══════════════════════════════════════════
# PARTNERSHIP — Detection et demarchage partenariats
# ══════════════════════════════════════════

PARTNERSHIP_TARGETS = {
    "dex": ["Jupiter", "Raydium", "Orca", "Meteora"],
    "infra": ["Helius", "Quicknode", "Triton", "GenesysGo"],
    "ai_protocols": ["Olas", "Fetch.ai", "SingularityNET", "Bittensor"],
    "l2": ["Base", "Arbitrum", "Optimism"],
    "gpu": ["RunPod", "Lambda", "Akash", "Render"],
    "wallets": ["Phantom", "Backpack", "Solflare"],
}

PARTNERSHIP_TEMPLATES = {
    "dex": "Integration listing — MAXIA routes {volume} trades/day through your DEX. API partnership for reduced fees?",
    "infra": "Infrastructure discount — MAXIA serves {clients} AI agents. Volume pricing for RPC/compute?",
    "ai_protocols": "AI-to-AI marketplace — MAXIA connects your agents to paying clients. Mutual listing partnership?",
    "l2": "Cross-chain expansion — MAXIA is live on Solana+Base. Integration for {chain} support?",
    "gpu": "GPU marketplace — MAXIA auctions GPU compute to AI agents. Reseller/affiliate deal?",
    "wallets": "Wallet integration — embed MAXIA services (swap, GPU, AI) directly in your wallet UI.",
}


async def partnership_scan(memory: Memory) -> list:
    """Scanne les partenaires potentiels et evalue la priorite."""
    opportunities = []
    existing = memory._data.get("partnerships", [])
    existing_names = [p.get("name", "").lower() for p in existing]

    stats = {
        "volume": memory._data.get("kpi", [{}])[-1].get("volume", 0) if memory._data.get("kpi") else 0,
        "clients": memory._data.get("clients", 0),
    }

    for category, partners in PARTNERSHIP_TARGETS.items():
        template = PARTNERSHIP_TEMPLATES.get(category, "")
        for partner in partners:
            if partner.lower() in existing_names:
                continue  # deja contacte
            score = 0
            # Score basé sur la pertinence actuelle
            if category == "dex" and stats["volume"] > 0:
                score = 80
            elif category == "ai_protocols":
                score = 90  # toujours haute priorite
            elif category == "gpu":
                score = 70
            elif category == "infra" and stats["clients"] > 5:
                score = 75
            elif category == "wallets" and stats["clients"] > 20:
                score = 85
            elif category == "l2":
                score = 60
            else:
                score = 50

            msg = template.format(volume=stats["volume"], clients=stats["clients"], chain=partner)
            opportunities.append({
                "partner": partner,
                "category": category,
                "score": score,
                "pitch": msg,
            })

    # Trier par score descendant
    opportunities.sort(key=lambda x: x["score"], reverse=True)
    return opportunities[:10]


async def partnership_outreach(partner: str, category: str, pitch: str, memory: Memory) -> dict:
    """Genere un message de demarchage personnalise via LLM."""
    prompt = (
        f"Ecris un message de partenariat B2B concis (150 mots max) a {partner} ({category}).\n"
        f"Contexte: {pitch}\n"
        f"MAXIA: AI marketplace sur Solana — {memory._data.get('clients', 0)} agents actifs, "
        f"${memory._data.get('revenue_usd', 0)} rev mensuel.\n"
        f"Ton: professionnel mais direct. Pas de flatterie excessive.\n"
        f"Inclure: proposition de valeur mutuelle, CTA concret (call, pilot, API test).\n"
        f"JSON: {{subject, message, cta, channel_suggested}}"
    )
    # Router: FAST pour la redaction d'outreach
    if llm_router:
        result = _pj(await llm_router.call(prompt, tier=Tier.FAST, system=CEO_IDENTITY + "\nMode PARTNERSHIP.", max_tokens=500))
    else:
        result = _pj(await _call_groq(CEO_IDENTITY + "\nMode PARTNERSHIP.", prompt))
    if result:
        if "partnerships" not in memory._data:
            memory._data["partnerships"] = []
        memory._data["partnerships"].append({
            "name": partner, "category": category,
            "contacted": datetime.utcnow().isoformat(),
            "status": "outreach_sent",
        })
        memory.save()
        memory.log_decision("orange", f"PARTNERSHIP: outreach a {partner} ({category})", "expansion", "PARTNERSHIP")
        memory.log_action_with_tracking("PARTNERSHIP", "outreach", f"partner_{partner.lower()}", f"{partner} ({category})")
    return result or {}


# ══════════════════════════════════════════
# ANALYTICS — Metriques avancees (retention, churn, LTV, funnel)
# ══════════════════════════════════════════

async def analytics_compute(memory: Memory) -> dict:
    """Calcule les metriques business avancees."""
    d = memory._data
    kpis = d.get("kpi", [])
    decisions = d.get("decisions", [])
    conversations = d.get("conversations", [])
    testimonials = d.get("testimonials", [])

    # Revenue metrics
    rev_total = d.get("revenue_usd", 0)
    clients_total = d.get("clients", 0)
    ltv = rev_total / max(1, clients_total)  # Lifetime value

    # Funnel metrics (depuis les conversations)
    prospects = len(set(c.get("user", "") for c in conversations if c.get("intention") == "prospect"))
    signups = clients_total
    active = d.get("kpi", [{}])[-1].get("clients_actifs", 0) if kpis else 0
    paying = len([t for t in testimonials if t.get("published")])

    funnel = {
        "prospects": prospects,
        "signups": signups,
        "active": active,
        "paying": paying,
        "conversion_prospect_to_signup": f"{signups / max(1, prospects):.1%}",
        "conversion_signup_to_active": f"{active / max(1, signups):.1%}",
        "conversion_active_to_paying": f"{paying / max(1, active):.1%}",
    }

    # Churn : clients qui etaient actifs il y a 7j mais plus maintenant
    kpi_7d_ago = kpis[-168] if len(kpis) >= 168 else kpis[0] if kpis else {}
    prev_active = kpi_7d_ago.get("clients_actifs", 0)
    churn = max(0, prev_active - active)
    churn_rate = churn / max(1, prev_active)

    # Revenue par canal
    rev_by_canal = {}
    for dec in decisions:
        if "revenue" in dec.get("decision", "").lower() or "paiement" in dec.get("decision", "").lower():
            canal = dec.get("cible", "unknown")
            rev_by_canal[canal] = rev_by_canal.get(canal, 0) + 1

    # Activite par heure (heatmap)
    activity_hours = {}
    for c in conversations[-500:]:
        ts = c.get("ts", "")
        if len(ts) >= 13:
            hour = ts[11:13]
            activity_hours[hour] = activity_hours.get(hour, 0) + 1

    # Score de sante global (0-100)
    health = 50
    if rev_total > 0:
        health += 15
    if clients_total >= 5:
        health += 10
    if churn_rate < 0.1:
        health += 10
    if active > 0:
        health += 10
    if len(d.get("erreurs_recurrentes", [])) == 0:
        health += 5
    health = min(100, health)

    analytics = {
        "ltv": round(ltv, 2),
        "churn": {"lost": churn, "rate": f"{churn_rate:.1%}"},
        "funnel": funnel,
        "revenue_by_canal": rev_by_canal,
        "activity_heatmap": dict(sorted(activity_hours.items())),
        "health_score": health,
        "recommendations": [],
    }

    # Recommandations automatiques + notification inter-agents via bus
    if churn_rate > 0.2:
        analytics["recommendations"].append("CHURN ELEVE: activer TESTIMONIAL pour re-engager les inactifs")
        agent_bus.send("ANALYTICS", "TESTIMONIAL", "churn_high", {"rate": f"{churn_rate:.1%}"})
    if funnel["prospects"] > 0 and signups / max(1, prospects) < 0.05:
        analytics["recommendations"].append("CONVERSION BASSE: HUNTER doit changer d'approche ou de canal")
        agent_bus.send("ANALYTICS", "HUNTER", "low_conversion", {"rate": f"{signups / max(1, prospects):.1%}"})
    if ltv < 1:
        analytics["recommendations"].append("LTV FAIBLE: NEGOTIATOR doit proposer des bundles pour augmenter panier moyen")
        agent_bus.send("ANALYTICS", "NEGOTIATOR", "low_ltv", {"ltv": ltv})
    if health < 40:
        analytics["recommendations"].append("SANTE CRITIQUE: focus sur stabilite avant croissance")
        agent_bus.broadcast("ANALYTICS", "health_critical", {"score": health})

    return analytics


async def analytics_weekly_report(memory: Memory) -> dict:
    """Genere un rapport hebdomadaire enrichi pour le fondateur."""
    metrics = await analytics_compute(memory)
    d = memory._data

    prompt = (
        f"Genere un rapport hebdomadaire CEO pour le fondateur (Alexis).\n\n"
        f"METRIQUES:\n{json.dumps(metrics, indent=2, default=str)}\n\n"
        f"KPI recents: rev=${d.get('revenue_usd', 0)}, clients={d.get('clients', 0)}\n"
        f"Agents actifs: {list(d.get('agents', {}).keys())}\n"
        f"Erreurs: {len(d.get('erreurs_recurrentes', []))}\n"
        f"Testimonials: {len(d.get('testimonials', []))}\n\n"
        f"Format: JSON {{resume_executif, kpi_cles, wins, problemes, actions_semaine_prochaine, message_fondateur}}\n"
        f"Ton: direct, factuel, avec les chiffres. Max 300 mots."
    )
    # Router: MID pour le rapport hebdo (raisonnement moyen)
    if llm_router:
        report = _pj(await llm_router.call(prompt, tier=Tier.MID, system=CEO_IDENTITY + "\nMode ANALYTICS.", max_tokens=2000))
    else:
        report = _pj(await _call_anthropic(SONNET_MODEL, CEO_IDENTITY + "\nMode ANALYTICS.", prompt, 2000))
    if report:
        report["metrics"] = metrics
        memory.log_decision("vert", f"ANALYTICS: rapport hebdo genere (health={metrics['health_score']})", "reporting", "ANALYTICS")
    return report or {"metrics": metrics}


# ══════════════════════════════════════════
# CRISIS-MANAGER — Gestion automatique des crises
# ══════════════════════════════════════════

CRISIS_LEVELS = {
    "P0": {"name": "critique", "response_min": 5, "escalate": True, "pause_marketing": True},
    "P1": {"name": "majeure", "response_min": 30, "escalate": True, "pause_marketing": True},
    "P2": {"name": "moderee", "response_min": 120, "escalate": False, "pause_marketing": False},
    "P3": {"name": "mineure", "response_min": 480, "escalate": False, "pause_marketing": False},
}


async def crisis_detect(memory: Memory, skip_health: bool = False) -> list:
    """Detecte les situations de crise automatiquement."""
    crises = []
    d = memory._data

    # P0 : Service principal DOWN (skip pendant startup)
    if not skip_health:
        try:
            health = await watchdog_health_check()
            if health.get("failed", 0) > health.get("total", 1) * 0.5:
                crises.append({
                    "level": "P0", "type": "service_outage",
                    "details": f"{health['failed']}/{health['total']} services DOWN",
                    "action": "WATCHDOG self-heal + GHOST-WRITER pause + alerte fondateur",
                })
        except Exception:
            pass

    # P0 : Perte de fonds detectee (solde du wallet micro qui baisse sans transactions loguees)
    try:
        balance = await micro_wallet.get_balance()
        expected = MICRO_MAX_PER_DAY - micro_wallet._spent_today
        if balance > 0 and balance < expected * 0.5 and micro_wallet._spent_today > 0:
            crises.append({
                "level": "P0", "type": "funds_anomaly",
                "details": f"Wallet balance {balance:.4f} SOL < expected {expected:.4f}",
                "action": "Freeze MICRO wallet + alerte rouge fondateur",
            })
    except Exception:
        pass

    # P1 : Erreurs en cascade (>10 erreurs differentes en 24h)
    errors = d.get("erreurs_recurrentes", [])
    recent_errors = [e for e in errors if e.get("count", 0) >= 3]
    if len(recent_errors) >= 5:
        crises.append({
            "level": "P1", "type": "error_cascade",
            "details": f"{len(recent_errors)} erreurs recurrentes (>=3 occurrences chacune)",
            "action": "WATCHDOG diagnostic complet + pause operations non-critiques",
        })

    # P1 : Churn massif (perte >30% clients en 24h)
    # Ignore si clients_now == 0 (probable reset DB, pas un vrai churn)
    # Ignore si clients_8h < 10 (trop peu pour etre significatif)
    kpis = d.get("kpi", [])
    if len(kpis) >= 8:
        clients_now = kpis[-1].get("clients_actifs", 0)
        clients_8h = kpis[-8].get("clients_actifs", 0)
        if clients_8h >= 10 and clients_now > 0 and clients_now < clients_8h * 0.7:
            crises.append({
                "level": "P1", "type": "mass_churn",
                "details": f"Clients: {clients_8h} -> {clients_now} (-{clients_8h - clients_now})",
                "action": "ANALYTICS diagnostic + RESPONDER campagne retention",
            })

    # P2 : Budget epuise
    if d.get("emergency_stop"):
        crises.append({
            "level": "P2", "type": "budget_exhausted",
            "details": "Emergency stop actif — budget epuise",
            "action": "Attente revenu ou reset fondateur",
        })

    # P2 : Aucun revenu depuis >8 semaines (pre-seed: normal d'avoir 0 rev au debut)
    if d.get("semaines_0rev", 0) >= 8:
        crises.append({
            "level": "P2", "type": "zero_revenue",
            "details": f"{d.get('semaines_0rev', 0)} semaines sans revenu",
            "action": "HUNTER intensifier prospection + NEGOTIATOR proposer promos",
        })

    # Notifier les agents concernes via le bus
    for crisis in crises:
        level = crisis.get("level", "P3")
        if level in ("P0", "P1"):
            agent_bus.broadcast("CRISIS-MANAGER", "crisis_alert", {"level": level, "type": crisis["type"], "details": crisis["details"]})
        if crisis["type"] == "mass_churn":
            agent_bus.send("CRISIS-MANAGER", "ANALYTICS", "churn_alert", {"details": crisis["details"]})
            agent_bus.send("CRISIS-MANAGER", "RESPONDER", "retention_needed", {"details": crisis["details"]})
        if crisis["type"] == "zero_revenue":
            agent_bus.send("CRISIS-MANAGER", "NEGOTIATOR", "promo_needed", {"weeks": d.get("semaines_0rev", 0)})
            agent_bus.send("CRISIS-MANAGER", "HUNTER", "intensify", {"reason": "zero_revenue"})

    return crises


async def crisis_respond(crisis: dict, memory: Memory) -> dict:
    """Execute le protocole de reponse a une crise."""
    level = crisis.get("level", "P3")
    config = CRISIS_LEVELS.get(level, CRISIS_LEVELS["P3"])
    crisis_type = crisis.get("type", "unknown")
    details = crisis.get("details", "")

    response = {
        "level": level,
        "type": crisis_type,
        "config": config,
        "actions_taken": [],
    }

    # Pause marketing si necessaire
    if config["pause_marketing"]:
        memory.update_agent("GHOST-WRITER", {"status": "pause_crise", "reason": crisis_type})
        memory.update_agent("HUNTER", {"status": "pause_crise", "reason": crisis_type})
        response["actions_taken"].append("marketing_paused")

    # Escalade fondateur si necessaire
    if config["escalate"]:
        await alert_rouge(
            f"CRISE {level}: {crisis_type}",
            f"{details}\n\nAction prevue: {crisis.get('action', 'diagnostic en cours')}\n"
            f"Temps de reponse cible: {config['response_min']} min",
            deadline_h=max(1, config["response_min"] // 60),
        )
        response["actions_taken"].append("founder_alerted")

    # Actions automatiques selon le type
    if crisis_type == "service_outage":
        # Lancer self-heal sur tous les services en erreur
        for err in memory._data.get("erreurs_recurrentes", []):
            if not err.get("patch_proposed"):
                await watchdog_self_heal(err["source"], err["error"], memory)
        response["actions_taken"].append("self_heal_triggered")

    elif crisis_type == "funds_anomaly":
        # Freeze le micro wallet
        micro_wallet._spent_today = MICRO_MAX_PER_DAY  # bloque toute depense
        response["actions_taken"].append("micro_wallet_frozen")

    elif crisis_type == "error_cascade":
        # Demander a Sonnet un diagnostic complet
        diag_prompt = (
            f"CRISE {level}: {details}\n"
            f"Erreurs: {json.dumps(memory._data.get('erreurs_recurrentes', [])[-10:], default=str)}\n"
            f"Analyse la cause racine et propose 3 actions concretes.\n"
            f"JSON: {{cause_racine, actions: [{{action, priorite, agent_cible}}], prevention}}"
        )
        # Router: MID pour diagnostic de crise
        if llm_router:
            diag = _pj(await llm_router.call(diag_prompt, tier=Tier.MID, system=CEO_IDENTITY + "\nMode CRISIS-MANAGER.", max_tokens=1500))
        else:
            diag = _pj(await _call_anthropic(SONNET_MODEL, CEO_IDENTITY + "\nMode CRISIS-MANAGER.", diag_prompt, 1500))
        if diag:
            response["diagnostic"] = diag
            response["actions_taken"].append("diagnostic_completed")

    elif crisis_type == "mass_churn":
        # Generer un message de retention
        retention_msg = await ghost_write("retention_email", "Why are users leaving? Win-back offer.", "email", memory)
        if retention_msg:
            response["retention_message"] = retention_msg
            response["actions_taken"].append("retention_campaign_drafted")

    elif crisis_type == "zero_revenue":
        # Proposer une promotion temporaire
        promo = {
            "type": "zero_fee_week",
            "duration_days": 7,
            "message": "0% fees for 7 days — bring your AI agent, earn USDC.",
        }
        response["promo_suggested"] = promo
        response["actions_taken"].append("promo_suggested")

    # Logger la crise
    memory.log_decision(
        "orange" if level in ("P0", "P1") else "vert",
        f"CRISIS-MANAGER: {level} {crisis_type} — {len(response['actions_taken'])} actions",
        "crisis_response", "CRISIS-MANAGER",
    )

    await alert_info(f"CRISIS {level} ({crisis_type}): {', '.join(response['actions_taken'])}")
    return response


# ══════════════════════════════════════════
# SCOUT — Scan agents on-chain + premier contact A2A
# Le scout collecte et remonte au CEO local. Il ne decide PAS.
# ══════════════════════════════════════════

async def scout_scan_onchain_agents(memory) -> list:
    """Scan reel des registres d'agents IA connus + detection de patterns on-chain.
    Interroge les APIs publiques de : Olas (Valory), Fetch.ai, ElizaOS, Virtuals Protocol, GOAT SDK.
    Detecte aussi les wallets avec comportement d'agent (transactions repetitives, interactions smart contract).
    Retourne une liste de {address, chain, behavior, registry, detected_at}."""
    import httpx

    detected = []
    now = datetime.utcnow().isoformat()
    # Set de deduplication par adresse
    seen_addresses = set()

    # ── 1. RADAR — donnees on-chain deja collectees ──
    try:
        radar_data = memory._data.get("radar_alerts", [])
        for alert in radar_data[-30:]:
            if alert.get("type") in ("new_agent", "repetitive_wallet", "ai_pattern"):
                addr = alert.get("address", "")
                if addr and addr not in seen_addresses:
                    seen_addresses.add(addr)
                    detected.append({
                        "address": addr,
                        "chain": alert.get("chain", "unknown"),
                        "behavior": alert.get("description", "automated transactions"),
                        "registry": "radar",
                        "detected_at": alert.get("ts", now),
                    })
    except Exception as e:
        logger.error("SCOUT radar scan error: %s", e)

    # ── 2. REGISTRES D'AGENTS — APIs publiques ──
    # Chaque registre a un endpoint different, on les interroge en parallele
    registry_configs = [
        {
            # Olas / Valory — registre d'agents autonomes sur Ethereum/Gnosis
            "name": "olas",
            "url": "https://registry.olas.network/api/agents?limit=20&offset=0",
            "chain": "ethereum",
            "parse": lambda data: [
                {
                    "address": a.get("instance", a.get("address", a.get("id", ""))),
                    "behavior": f"olas agent #{a.get('id', '?')}: {a.get('name', 'unnamed')[:60]}",
                }
                for a in (data if isinstance(data, list) else data.get("results", data.get("agents", [])))
                if a.get("instance") or a.get("address") or a.get("id")
            ],
        },
        {
            # Fetch.ai — Agentverse / Almanac registre
            "name": "fetch.ai",
            "url": "https://agentverse.ai/api/v1/agents?limit=20",
            "chain": "fetchai",
            "parse": lambda data: [
                {
                    "address": a.get("address", a.get("agent_address", "")),
                    "behavior": f"fetch agent: {a.get('name', a.get('title', 'unnamed'))[:60]}",
                }
                for a in (data if isinstance(data, list) else data.get("agents", data.get("results", [])))
                if a.get("address") or a.get("agent_address")
            ],
        },
        {
            # Virtuals Protocol — agents sur Base
            "name": "virtuals",
            "url": "https://api.virtuals.io/api/agents?limit=20",
            "chain": "base",
            "parse": lambda data: [
                {
                    "address": a.get("wallet", a.get("address", a.get("virtualId", ""))),
                    "behavior": f"virtuals agent: {a.get('name', 'unnamed')[:60]}",
                }
                for a in (data if isinstance(data, list) else data.get("data", data.get("agents", [])))
                if a.get("wallet") or a.get("address") or a.get("virtualId")
            ],
        },
        {
            # GOAT SDK — registre d'agents sur GitHub (API repos)
            "name": "goat-sdk",
            "url": "https://api.github.com/orgs/goat-sdk/repos?per_page=10&sort=updated",
            "chain": "multi",
            "parse": lambda data: [
                {
                    "address": f"github:goat-sdk/{r.get('name', '')}",
                    "behavior": f"goat plugin: {r.get('name', '')} ({r.get('stargazers_count', 0)} stars)",
                }
                for r in (data if isinstance(data, list) else [])
                if r.get("name") and not r.get("archived", False)
            ],
        },
        {
            # ElizaOS — registre de plugins/agents (GitHub)
            "name": "elizaos",
            "url": "https://api.github.com/orgs/elizaOS/repos?per_page=10&sort=updated",
            "chain": "multi",
            "parse": lambda data: [
                {
                    "address": f"github:elizaOS/{r.get('name', '')}",
                    "behavior": f"eliza plugin: {r.get('name', '')} ({r.get('stargazers_count', 0)} stars)",
                }
                for r in (data if isinstance(data, list) else [])
                if r.get("name") and not r.get("archived", False)
            ],
        },
        {
            # 8004scan — registre ERC-8004 agents autonomes (EVM)
            "name": "8004scan",
            "url": "https://www.8004scan.io/api/agents?limit=20&sort=latest",
            "chain": "ethereum",
            "parse": lambda data: [
                {
                    "address": a.get("address", a.get("contractAddress", a.get("id", ""))),
                    "behavior": f"erc8004 agent: {a.get('name', a.get('title', 'unnamed'))[:60]}",
                    "metadata": {
                        "url": a.get("url", a.get("endpoint", "")),
                        "description": a.get("description", "")[:100],
                        "owner": a.get("owner", a.get("creator", "")),
                    },
                }
                for a in (data if isinstance(data, list) else data.get("agents", data.get("data", data.get("results", []))))
                if a.get("address") or a.get("contractAddress") or a.get("id")
            ],
        },
    ]

    client = get_http_client()
    # Lancer toutes les requetes en parallele
    tasks = []
    for reg in registry_configs:
        tasks.append(_scout_fetch_registry(client, reg, seen_addresses, now))
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for res in results:
        if isinstance(res, list):
            detected.extend(res)

    # ── 3. HELIUS — detection de wallets agents sur Solana ──
    # Chercher les wallets avec des patterns d'agent (>20 tx/jour, interactions avec programmes connus)
    helius_key = _cfg("HELIUS_API_KEY")
    if helius_key:
        try:
            # Programmes connus d'agents IA sur Solana
            agent_programs = [
                "orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMU1kektZE",  # Orca (DEX — agents l'utilisent)
                "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",  # Jupiter (swaps automatises)
                "MFv2hWf31Z9kbCa1snEPYctwafyhdvnV7FZnsebVacA",  # Marinade (staking auto)
            ]
            client = get_http_client()
            for program_id in agent_programs[:2]:  # Limiter a 2 pour ne pas spam Helius
                try:
                    resp = await client.post(
                        f"https://mainnet.helius-rpc.com/?api-key={helius_key}",
                        json={
                            "jsonrpc": "2.0", "id": 1,
                            "method": "getSignaturesForAddress",
                            "params": [program_id, {"limit": 10}],
                        },
                    )
                    if resp.status_code == 200:
                        sigs = resp.json().get("result", [])
                        # Compter les signataires recurrents (wallets qui interagissent souvent)
                        wallet_counts = {}
                        for sig in sigs:
                            memo = sig.get("memo", "")
                            # Les agents mettent souvent un memo ou signent des tx repetitives
                            if memo and ("agent" in memo.lower() or "bot" in memo.lower() or "ai" in memo.lower()):
                                addr = sig.get("signature", "")[:44]
                                wallet_counts[addr] = wallet_counts.get(addr, 0) + 1
                        # Wallets avec >3 tx repetitives = probablement un agent
                        for addr, count in wallet_counts.items():
                            if count >= 3 and addr not in seen_addresses:
                                seen_addresses.add(addr)
                                detected.append({
                                    "address": addr,
                                    "chain": "solana",
                                    "behavior": f"repetitive tx pattern ({count} interactions with {program_id[:8]}...)",
                                    "registry": "helius-onchain",
                                    "detected_at": now,
                                })
                except Exception:
                    pass
        except Exception as e:
            logger.error("SCOUT Helius scan error: %s", e)

    # ── 4. MEMOIRE — registres connus deja stockes ──
    try:
        registries = memory._data.get("known_agent_registries", [])
        for reg in registries:
            addr = reg.get("address", "")
            if addr and addr not in seen_addresses:
                seen_addresses.add(addr)
                detected.append({
                    "address": addr,
                    "chain": reg.get("chain", "unknown"),
                    "behavior": f"registered on {reg.get('registry', 'unknown')}",
                    "registry": reg.get("registry", "memory"),
                    "detected_at": reg.get("detected_at", now),
                })
    except Exception as e:
        logger.error("SCOUT memory scan error: %s", e)

    # ── 5. AUTO-CONTACT — contacter les nouveaux agents avec endpoint A2A ──
    already_contacted = set()
    try:
        already_contacted = {c.get("address", "") for c in memory._data.get("contacts_log", [])}
    except Exception:
        pass

    contacted = 0
    for agent in detected:
        if contacted >= 3:  # Max 3 contacts par scan (anti-spam)
            break
        addr = agent.get("address", "")
        reg = agent.get("registry", "")
        if not addr or addr in already_contacted:
            continue
        # Prioriser les agents de registres riches (8004scan, olas, fetch, virtuals)
        if reg not in ("8004scan", "olas", "fetch.ai", "virtuals"):
            continue
        # Generer un pitch adapte au registre
        pitch = (
            f"Hi! MAXIA is an AI-to-AI marketplace on 14 blockchains. "
            f"107 tokens, GPU rental at cost, DeFi yields, tokenized stocks, "
            f"46 MCP tools, W3C DID identity + signed intents. "
            f"Your agent ({agent.get('behavior', '')[:60]}) "
            f"could use our services or list on our marketplace. "
            f"Join our agent forum: https://maxiaworld.app/forum "
            f"| API: https://maxiaworld.app/a2a"
        )
        try:
            result = await scout_first_contact_a2a(addr, agent.get("chain", "ethereum"), pitch)
            if result.get("success"):
                contacted += 1
                method = result.get("method", "?")
                logger.info("SCOUT auto-contacted %s... via %s", addr[:20], method)
                # Notification Telegram — alerter le fondateur
                try:
                    await alert_info(
                        f"SCOUT: contact A2A reussi\n"
                        f"Agent: {addr[:20]}... ({agent.get('chain', '?')})\n"
                        f"Registre: {reg}\n"
                        f"Methode: {method}\n"
                        f"Behavior: {agent.get('behavior', '')[:80]}"
                    )
                except Exception:
                    pass
                # Log le contact
                try:
                    memory._data.setdefault("contacts_log", []).append({
                        "address": addr, "chain": agent.get("chain", ""),
                        "registry": reg, "method": result.get("method", ""),
                        "ts": now, "response": result.get("response", "pending"),
                    })
                except Exception:
                    pass
        except Exception as e:
            logger.error("SCOUT auto-contact error %s: %s", addr[:20], e)

    logger.info("SCOUT scan termine: %s agents detectes, %s auto-contacted", len(detected), contacted)

    # Notification Telegram — rapport de scan (meme si 0 contacts)
    if detected:
        registries = {}
        for a in detected:
            r = a.get("registry", "unknown")
            registries[r] = registries.get(r, 0) + 1
        reg_summary = ", ".join(f"{v} {k}" for k, v in registries.items())
        try:
            await alert_info(
                f"SCOUT scan: {len(detected)} agents detectes\n"
                f"Registres: {reg_summary}\n"
                f"Contacts: {contacted}/{min(3, len(detected))}\n"
                f"Echecs: {min(3, len(detected)) - contacted}"
            )
        except Exception:
            pass

    return detected


async def _scout_fetch_registry(client, reg_config: dict, seen: set, now: str) -> list:
    """Interroge un registre d'agents et retourne les agents detectes.
    Fonction helper pour paralleliser les requetes."""
    results = []
    name = reg_config["name"]
    try:
        headers = {"Accept": "application/json", "User-Agent": "MAXIA-Scout/1.0"}
        # GitHub a un rate limit strict — ajouter le token si disponible
        github_token = _cfg("GITHUB_TOKEN")
        if "github.com" in reg_config["url"] and github_token:
            headers["Authorization"] = f"token {github_token}"

        resp = await client.get(reg_config["url"], headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            parsed = reg_config["parse"](data)
            for agent in parsed[:15]:  # Limiter a 15 par registre
                addr = str(agent.get("address", ""))
                if addr and addr not in seen:
                    seen.add(addr)
                    entry = {
                        "address": addr,
                        "chain": reg_config["chain"],
                        "behavior": agent.get("behavior", f"registered on {name}"),
                        "registry": name,
                        "detected_at": now,
                    }
                    # Conserver les metadonnees (URL, description) pour le contact A2A
                    if agent.get("metadata"):
                        entry["metadata"] = agent["metadata"]
                    results.append(entry)
            logger.info("SCOUT %s: %s agents trouves", name, len(results))
        else:
            logger.warning("SCOUT %s: HTTP %s", name, resp.status_code)
    except Exception as e:
        logger.error("SCOUT %s error: %s", name, e)
    return results


async def scout_first_contact_a2a(address: str, chain: str, pitch: str) -> dict:
    """Contact reel d'un agent on-chain via A2A protocol.
    1. Tente de decouvrir l'agent via /.well-known/agent.json
    2. Si endpoint A2A trouve, envoie un message avec le manifest MAXIA
    3. Sinon, fallback sur memo on-chain (micro tx USDC avec message)
    Retourne {success, contact, method}."""
    import httpx

    now = datetime.utcnow().isoformat()
    contact_record = {
        "address": address,
        "chain": chain,
        "pitch": pitch[:500],
        "method": "pending",
        "ts": now,
        "response": "pending",
    }

    # ── MANIFEST MAXIA pour l'echange A2A ──
    maxia_manifest = {
        "name": "MAXIA",
        "description": "AI-to-AI Marketplace on 14 blockchains — swap, GPU, stocks, DeFi, 46 MCP tools",
        "url": "https://maxiaworld.app",
        "protocolVersion": "0.3",
        "capabilities": ["marketplace", "swap", "gpu", "defi", "mcp"],
        "contact": "https://maxiaworld.app/a2a",
    }

    # ── 1. ESSAYER LE CONTACT A2A (si l'adresse ressemble a une URL ou domaine) ──
    a2a_endpoints = []

    # Si l'adresse est une URL ou un domaine, tenter la decouverte A2A
    if address.startswith("http") or address.startswith("github:"):
        base_url = address
        if address.startswith("github:"):
            # Construire l'URL depuis le repo GitHub (convention: README contient l'endpoint)
            parts = address.replace("github:", "").split("/")
            if len(parts) >= 2:
                base_url = f"https://{parts[1]}.github.io"
        a2a_endpoints = [
            f"{base_url}/.well-known/agent.json",
            f"{base_url}/a2a",
            f"{base_url}/api/a2a",
        ]
    else:
        # Essayer les domaines classiques pour les agents connus
        # Olas, Fetch, Virtuals exposent parfois des endpoints A2A
        known_domains = {
            "olas": "https://registry.olas.network",
            "fetch.ai": "https://agentverse.ai",
            "virtuals": "https://api.virtuals.io",
        }
        for registry_name, domain in known_domains.items():
            a2a_endpoints.append(f"{domain}/.well-known/agent.json")
            a2a_endpoints.append(f"{domain}/api/agents/{address}/a2a")

    # Tenter la decouverte et le contact A2A
    a2a_success = False
    client = get_http_client()
    for endpoint in a2a_endpoints[:5]:  # Limiter a 5 tentatives
        try:
            # 1a. Decouverte — verifier si l'agent expose un agent card
            if endpoint.endswith("agent.json"):
                resp = await client.get(endpoint, headers={"Accept": "application/json"}, timeout=10)
                if resp.status_code == 200:
                    agent_card = resp.json()
                    agent_a2a_url = agent_card.get("url", "")
                    agent_name = agent_card.get("name", "unknown")
                    logger.info("SCOUT A2A agent card trouve: %s at %s", agent_name, agent_a2a_url)

                    # 1b. Contact — envoyer un message A2A (JSON-RPC tasks/send)
                    if agent_a2a_url:
                        task_resp = await client.post(
                            f"{agent_a2a_url.rstrip('/')}/a2a",
                            json={
                                "jsonrpc": "2.0",
                                "id": f"maxia-scout-{int(time.time())}",
                                "method": "tasks/send",
                                "params": {
                                    "id": f"contact-{address[:10]}-{int(time.time())}",
                                    "message": {
                                        "role": "user",
                                        "parts": [{
                                            "type": "text",
                                            "text": pitch[:500],
                                        }],
                                    },
                                    "metadata": {
                                        "from": "MAXIA",
                                        "manifest": maxia_manifest,
                                    },
                                },
                            },
                            headers={"Content-Type": "application/json"},
                        )
                        if task_resp.status_code in (200, 201, 202):
                            result = task_resp.json()
                            contact_record["method"] = "a2a"
                            contact_record["response"] = result.get("result", {}).get("status", {}).get("state", "submitted")
                            contact_record["agent_name"] = agent_name
                            contact_record["a2a_url"] = agent_a2a_url
                            a2a_success = True
                            logger.info("SCOUT Contact A2A reussi: %s", agent_name)
                            break
            else:
                # Tenter un POST direct sur l'endpoint A2A
                resp = await client.post(
                    endpoint,
                    json={
                        "jsonrpc": "2.0",
                        "id": f"maxia-scout-{int(time.time())}",
                        "method": "tasks/send",
                        "params": {
                            "id": f"contact-{address[:10]}-{int(time.time())}",
                            "message": {
                                "role": "user",
                                "parts": [{"type": "text", "text": pitch[:500]}],
                            },
                            "metadata": {"from": "MAXIA", "manifest": maxia_manifest},
                        },
                    },
                    headers={"Content-Type": "application/json"},
                )
                if resp.status_code in (200, 201, 202):
                    result = resp.json()
                    contact_record["method"] = "a2a"
                    contact_record["response"] = result.get("result", {}).get("status", {}).get("state", "submitted")
                    contact_record["a2a_url"] = endpoint
                    a2a_success = True
                    logger.info("SCOUT Contact A2A direct reussi: %s", endpoint)
                    break
        except Exception as e:
            # Silencieux — on essaie le prochain endpoint
            continue

    # ── 2. FALLBACK — memo on-chain (micro tx USDC avec message) ──
    if not a2a_success and chain == "solana":
        try:
            # Construire une micro-transaction USDC ($0.001) avec memo contenant le pitch
            # Le memo sert de "carte de visite" on-chain
            memo_text = f"MAXIA AI Marketplace — {pitch[:100]} — https://maxiaworld.app/a2a"
            micro_amount = 0.001  # $0.001 USDC — cout negligeable

            # Verifier que le wallet micro est configure
            micro_addr = _cfg("MICRO_WALLET_ADDRESS")
            micro_key = _cfg("MICRO_WALLET_PRIVKEY")
            if micro_addr and micro_key:
                # Utiliser solana_tx pour envoyer la micro-tx avec memo
                try:
                    from blockchain.solana_tx import build_usdc_transfer_with_memo
                    tx_result = await build_usdc_transfer_with_memo(
                        sender_privkey=micro_key,
                        recipient=address,
                        amount_usdc=micro_amount,
                        memo=memo_text[:256],  # Solana memo limit
                    )
                    if tx_result and tx_result.get("signature"):
                        contact_record["method"] = "onchain_memo"
                        contact_record["response"] = "memo_sent"
                        contact_record["tx_signature"] = tx_result["signature"]
                        contact_record["amount_usdc"] = micro_amount
                        logger.info("SCOUT Memo on-chain envoye: %s... (tx: %s...)", address[:10], tx_result["signature"][:20])
                        return {"success": True, "contact": contact_record, "method": "onchain_memo"}
                except ImportError:
                    logger.warning("SCOUT solana_tx.build_usdc_transfer_with_memo non disponible — memo skip")
                except Exception as e:
                    logger.error("SCOUT Memo on-chain erreur: %s", e)
            else:
                logger.warning("SCOUT Micro wallet non configure — memo on-chain skip")

            # Si la tx memo echoue, on enregistre quand meme le contact
            contact_record["method"] = "onchain_memo_failed"
            contact_record["response"] = "memo_not_sent"
        except Exception as e:
            logger.error("SCOUT Fallback memo error: %s", e)
            contact_record["method"] = "failed"
            contact_record["response"] = str(e)[:100]

    # Si aucun contact n'a reussi et ce n'est pas Solana
    if not a2a_success and contact_record["method"] == "pending":
        contact_record["method"] = "queued"
        contact_record["response"] = "no_a2a_endpoint_found"

    logger.info("SCOUT Contact %s: %s... on %s", contact_record["method"], address[:16], chain)
    return {"success": a2a_success, "contact": contact_record, "method": contact_record["method"]}


# ══════════════════════════════════════════
# CEO MAXIA — MODE SCOUT
# Le VPS collecte, le CEO local (PC) decide.
# ══════════════════════════════════════════

