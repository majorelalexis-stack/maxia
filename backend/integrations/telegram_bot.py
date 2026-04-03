"""MAXIA Telegram Bot V11 — Canal d'alertes marche (zero spam)

Le bot gere un canal @MAXIA_alerts avec :
- Alertes marche xStocks (hausses/baisses)
- Rapport quotidien public
- Comparatif hebdo MAXIA vs concurrence
- NE rejoint aucun groupe externe
"""
import logging
import asyncio, time, json
import httpx

logger = logging.getLogger(__name__)
from core.http_client import get_http_client
from core.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL, PORT

_running = False

# Dict partage pour stocker les resultats d'approbation du CEO Local
# Le VPS gere les callbacks Telegram (seul poller) et stocke les resultats ici.
# Le CEO Local interroge /api/ceo/approval-result/<action_id> pour recuperer le resultat.
_local_approval_results: dict = {}  # {action_id: "approved"|"denied"}


def get_approval_result(action_id: str) -> str | None:
    """Retourne le resultat d'approbation pour un action_id, ou None si pas encore repondu."""
    return _local_approval_results.pop(action_id, None)


async def send_telegram(text: str, parse_mode: str = "HTML") -> bool:
    """Envoie un message sur le canal Telegram MAXIA."""
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("Token absent — message ignore")
        return False

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHANNEL,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        client = get_http_client()
        resp = await client.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            logger.info("Message envoye")
            return True
        else:
            logger.error("Erreur %d: %s", resp.status_code, resp.text[:100])
            return False
    except Exception as e:
        logger.error("Erreur: %s", e)
        return False


_stock_alerts_today = 0
_stock_alerts_day = ""
_MAX_STOCK_ALERTS_DAY = 5


async def send_market_alert(symbol: str, name: str, change_pct: float, price: float):
    """Alerte marche quand une action bouge significativement. Max 5/jour."""
    global _stock_alerts_today, _stock_alerts_day
    if abs(change_pct) < 3:
        return

    today = time.strftime("%Y-%m-%d")
    if today != _stock_alerts_day:
        _stock_alerts_today = 0
        _stock_alerts_day = today
    if _stock_alerts_today >= _MAX_STOCK_ALERTS_DAY:
        return
    _stock_alerts_today += 1

    emoji = "\U0001f4c8" if change_pct > 0 else "\U0001f4c9"
    direction = "hausse" if change_pct > 0 else "baisse"

    text = (
        f"{emoji} <b>{name} ({symbol})</b>\n\n"
        f"En {direction} de <b>{abs(change_pct):.1f}%</b> — Prix: ${price:.2f}\n\n"
        f"Trade on MAXIA AI-to-AI Marketplace\n"
        f"Sell your analysis to other AI agents and earn USDC\n\n"
        f"\U0001f517 maxiaworld.app"
    )
    await send_telegram(text)


async def send_daily_report(stats: dict):
    """Rapport quotidien public sur Telegram. Pas de chiffres sensibles."""
    text = (
        f"\U0001f4ca <b>MAXIA — AI-to-AI Marketplace</b>\n\n"
        f"\U0001f310 14 blockchains supportees\n"
        f"\U0001f4b1 65 tokens, 4160 paires de trading\n"
        f"\U0001f5a5 GPU Akash: RTX 4090 \u2192 H100 (15% moins cher que AWS)\n"
        f"\U0001f916 46 outils MCP pour agents IA\n\n"
        f"\U0001f4b0 Swap: 0.10% \u2192 0.01% | Marketplace: 1% \u2192 0.1% selon volume\n"
        f"\U0001f517 API: maxiaworld.app/api/public/docs"
    )
    await send_telegram(text)


async def send_weekly_comparison():
    """Comparatif hebdomadaire MAXIA vs concurrence."""
    text = (
        f"📊 <b>MAXIA vs Concurrence — Comparatif hebdo</b>\n\n"
        f"<b>Actions tokenisees (xStocks)</b>\n"
        f"  MAXIA Whale: 0.01% swap | 0.1% marketplace\n"
        f"  Jupiter: 0% + slippage\n"
        f"  Robinhood: ~0.5% (spread cache)\n"
        f"  Binance: 0.1%\n\n"
        f"<b>Location GPU</b>\n"
        f"  MAXIA: Akash Network (15% marge, moins cher que cloud)\n"
        f"  AWS/RunPod: 15-50% plus cher\n"
        f"  AWS: 3-5x plus cher\n\n"
        f"<b>Services IA</b>\n"
        f"  Audit smart contract: $4.99 (vs $5000+ ailleurs)\n"
        f"  Code generation: $1.99/tache\n"
        f"  Traduction: $0.09/requete\n\n"
        f"Inscription gratuite: maxiaworld.app/api/public/register"
    )
    await send_telegram(text)


async def check_and_alert_stocks():
    """Verifie les prix des actions et envoie des alertes si mouvement > 3%."""
    try:
        from trading.tokenized_stocks import fetch_stock_prices
        prices = await fetch_stock_prices()
        for sym, data in prices.items():
            change = data.get("change", 0)
            if abs(change) >= 3:
                await send_market_alert(sym, data.get("name", sym), change, data.get("price", 0))
                await asyncio.sleep(2)  # Rate limit Telegram
    except Exception as e:
        logger.error("Stock check error: %s", e)


async def handle_telegram_updates():
    """Poll for incoming messages and route to CEO."""
    if not TELEGRAM_BOT_TOKEN:
        return
    last_update_id = 0
    logger.info("Incoming message handler started")
    while True:
        try:
            client = get_http_client()
            params = {"offset": last_update_id + 1, "timeout": 20}
            resp = await client.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates", params=params, timeout=30)
            if resp.status_code != 200:
                await asyncio.sleep(5)
                continue
            data = resp.json()
            for update in data.get("result", []):
                last_update_id = update["update_id"]

                # Handle callback queries (Go/No-Go buttons)
                callback = update.get("callback_query")
                if callback:
                    cb_data = callback.get("data", "")
                    cb_id = callback.get("id", "")
                    cb_chat = callback.get("message", {}).get("chat", {}).get("id", "")
                    cb_msg_id = callback.get("message", {}).get("message_id", "")
                    try:
                        # Answer the callback
                        await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery",
                            json={"callback_query_id": cb_id, "text": "Processing..."})

                        if cb_data.startswith("go:"):
                            decision_id = cb_data[3:]
                            from agents.ceo_maxia import _pending_decisions, execute, ceo
                            pending = _pending_decisions.pop(decision_id, None)
                            if pending:
                                await execute([pending["decision"]], ceo.memory)
                                await _send_to_chat(cb_chat, f"\u2705 GO — {pending['titre']} — Executee")
                            else:
                                await _send_to_chat(cb_chat, f"\u2705 GO — Decision acceptee")
                            # Update the original message
                            await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageReplyMarkup",
                                json={"chat_id": cb_chat, "message_id": cb_msg_id, "reply_markup": "{}"})

                        elif cb_data.startswith("nogo:"):
                            decision_id = cb_data[5:]
                            _pending_decisions.pop(decision_id, None) if hasattr(_pending_decisions, 'pop') else None
                            try:
                                from agents.ceo_maxia import _pending_decisions
                                _pending_decisions.pop(decision_id, None)
                            except Exception:
                                pass
                            await _send_to_chat(cb_chat, f"\u274c NO-GO — Decision rejetee")
                            await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageReplyMarkup",
                                json={"chat_id": cb_chat, "message_id": cb_msg_id, "reply_markup": "{}"})

                        # Handle CEO Local approval buttons (approve:/deny:)
                        elif cb_data.startswith("approve:"):
                            action_id = cb_data[8:]
                            _local_approval_results[action_id] = "approved"
                            await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery",
                                json={"callback_query_id": cb_id, "text": "Approuve!"})
                            if cb_msg_id and cb_chat:
                                await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText",
                                    json={"chat_id": cb_chat, "message_id": cb_msg_id,
                                          "text": f"\u2705 APPROUVE — {action_id}"})
                            logger.info("CEO Local approve: %s", action_id)

                        elif cb_data.startswith("deny:"):
                            action_id = cb_data[5:]
                            _local_approval_results[action_id] = "denied"
                            await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery",
                                json={"callback_query_id": cb_id, "text": "Refuse!"})
                            if cb_msg_id and cb_chat:
                                await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText",
                                    json={"chat_id": cb_chat, "message_id": cb_msg_id,
                                          "text": f"\u274c REFUSE — {action_id}"})
                            logger.info("CEO Local deny: %s", action_id)

                    except Exception as e:
                        logger.error("Callback error: %s", e)
                    continue

                msg = update.get("message", {})
                text = msg.get("text", "")
                chat_id = msg.get("chat", {}).get("id", "")
                user = msg.get("from", {}).get("first_name", "unknown")
                if not text or not chat_id:
                    continue
                # Route to CEO
                try:
                    ceo_response = await _ask_ceo(text, user)
                    await _send_to_chat(chat_id, ceo_response)
                except Exception as e:
                    await _send_to_chat(chat_id, f"Erreur: {e}")
        except Exception as e:
            logger.error("Update error: %s", e)
        await asyncio.sleep(1)


async def _ask_ceo(message: str, user: str) -> str:
    try:
        client = get_http_client()
        resp = await client.post(f"http://127.0.0.1:{PORT}/api/ceo/ask", json={"message": message}, timeout=30)
        if resp.status_code == 200:
            return resp.json().get("response", "Erreur CEO")
        return f"Erreur API: {resp.status_code}"
    except Exception as e:
        return f"CEO indisponible: {e}"


async def _send_to_chat(chat_id, text: str):
    if not TELEGRAM_BOT_TOKEN:
        return
    # Split long messages
    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
    client = get_http_client()
    for chunk in chunks:
        await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": chunk})


async def run_telegram_bot():
    """Boucle principale du bot Telegram."""
    global _running
    _running = True

    if not TELEGRAM_BOT_TOKEN:
        logger.warning("Token absent — bot desactive")
        return

    # Launch incoming message handler as concurrent task
    asyncio.create_task(handle_telegram_updates())

    logger.info("Bot demarre — canal d'alertes actif")

    # Message de demarrage — 1 seule fois par jour
    import os
    flag_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".telegram_started")
    today = time.strftime("%Y-%m-%d")
    should_send_startup = True
    try:
        if os.path.exists(flag_file):
            with open(flag_file) as f:
                if f.read().strip() == today:
                    should_send_startup = False
    except Exception:
        pass

    if should_send_startup:
        await send_telegram(
            "🤖 <b>MAXIA V12 — AI-to-AI Marketplace</b>\n\n"
            "Ce canal publie :\n"
            "🔄 Transactions AI-to-AI en temps reel\n"
            "📊 Rapport quotidien CEO\n"
            "⚠️ Alertes WATCHDOG si service DOWN\n\n"
            "Inscription gratuite: maxiaworld.app"
        )
        try:
            with open(flag_file, "w") as f:
                f.write(today)
        except Exception:
            pass

    last_daily = 0
    last_weekly = 0
    last_stock_check = 0

    while _running:
        try:
            now = time.time()

            # Alertes marche toutes les 2 heures
            if now - last_stock_check > 7200:
                await check_and_alert_stocks()
                last_stock_check = now

            # Rapport quotidien a 20h UTC
            hour = int(time.strftime("%H", time.gmtime()))
            day = time.strftime("%Y-%m-%d")
            if hour == 20 and day != str(last_daily):
                try:
                    client = get_http_client()
                    r1 = await client.get(f"http://127.0.0.1:{PORT}/api/stats", timeout=10)
                    r2 = await client.get(f"http://127.0.0.1:{PORT}/api/public/marketplace-stats", timeout=10)
                    stats = {**r1.json(), **r2.json()}
                    await send_daily_report(stats)
                    last_daily = day
                except Exception:
                    pass

            # Comparatif hebdo le dimanche
            weekday = int(time.strftime("%w", time.gmtime()))
            if weekday == 0 and hour == 18 and day != str(last_weekly):
                await send_weekly_comparison()
                last_weekly = day

        except Exception as e:
            logger.error("Loop error: %s", e)

        await asyncio.sleep(300)  # Check toutes les 5 min


def stop():
    global _running
    _running = False
