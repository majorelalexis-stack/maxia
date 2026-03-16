"""MAXIA Telegram Bot V11 — Canal d'alertes marche (zero spam)

Le bot gere un canal @MAXIA_alerts avec :
- Alertes marche xStocks (hausses/baisses)
- Rapport quotidien public
- Comparatif hebdo MAXIA vs concurrence
- NE rejoint aucun groupe externe
"""
import asyncio, time, json
import httpx
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL

_running = False


async def send_telegram(text: str, parse_mode: str = "HTML") -> bool:
    """Envoie un message sur le canal Telegram MAXIA."""
    if not TELEGRAM_BOT_TOKEN:
        print(f"[Telegram] Token absent — message ignore")
        return False

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHANNEL,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                print(f"[Telegram] Message envoye")
                return True
            else:
                print(f"[Telegram] Erreur {resp.status_code}: {resp.text[:100]}")
                return False
    except Exception as e:
        print(f"[Telegram] Erreur: {e}")
        return False


async def send_market_alert(symbol: str, name: str, change_pct: float, price: float):
    """Alerte marche quand une action bouge significativement."""
    if abs(change_pct) < 3:
        return

    emoji = "📈" if change_pct > 0 else "📉"
    direction = "hausse" if change_pct > 0 else "baisse"

    text = (
        f"{emoji} <b>{name} ({symbol})</b>\n\n"
        f"En {direction} de <b>{abs(change_pct):.1f}%</b> — Prix: ${price:.2f}\n\n"
        f"Trade on MAXIA AI-to-AI Marketplace\n"
        f"Sell your analysis to other AI agents and earn USDC\n\n"
        f"🔗 maxiaworld.app"
    )
    await send_telegram(text)


async def send_daily_report(stats: dict):
    """Rapport quotidien public sur Telegram."""
    text = (
        f"📊 <b>MAXIA — Rapport quotidien</b>\n\n"
        f"Volume 24h: <b>{stats.get('volume_24h', 0):.2f} USDC</b>\n"
        f"Transactions: <b>{stats.get('total_trades', 0)}</b>\n"
        f"Agents inscrits: <b>{stats.get('registered_agents', 0)}</b>\n"
        f"Services actifs: <b>{stats.get('listing_count', 0)}</b>\n"
        f"GPU disponibles: 5 (RTX 4090 → 4xA100)\n\n"
        f"💰 Commission: 0.5% → 0.05% selon volume\n"
        f"📄 White Paper: maxiaworld.app/MAXIA_WhitePaper_v1.pdf\n"
        f"🔗 API: maxiaworld.app/api/public/docs"
    )
    await send_telegram(text)


async def send_weekly_comparison():
    """Comparatif hebdomadaire MAXIA vs concurrence."""
    text = (
        f"📊 <b>MAXIA vs Concurrence — Comparatif hebdo</b>\n\n"
        f"<b>Actions tokenisees (xStocks)</b>\n"
        f"  MAXIA Baleine: 0.05%\n"
        f"  Jupiter: 0% + slippage\n"
        f"  Robinhood: ~0.5% (spread cache)\n"
        f"  Binance: 0.1%\n\n"
        f"<b>Location GPU</b>\n"
        f"  MAXIA: prix coutant RunPod (0% marge)\n"
        f"  RunPod direct: meme prix\n"
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
        from tokenized_stocks import fetch_stock_prices
        prices = await fetch_stock_prices()
        for sym, data in prices.items():
            change = data.get("change", 0)
            if abs(change) >= 3:
                await send_market_alert(sym, data.get("name", sym), change, data.get("price", 0))
                await asyncio.sleep(2)  # Rate limit Telegram
    except Exception as e:
        print(f"[Telegram] Stock check error: {e}")


async def run_telegram_bot():
    """Boucle principale du bot Telegram."""
    global _running
    _running = True

    if not TELEGRAM_BOT_TOKEN:
        print("[Telegram] Token absent — bot desactive")
        return

    print("[Telegram] Bot demarre — canal d'alertes actif")

    # Message de demarrage — 1 seule fois par jour
    import os
    flag_file = "/tmp/maxia_telegram_started"
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
                    async with httpx.AsyncClient(timeout=10) as client:
                        r1 = await client.get("http://localhost:8000/api/stats")
                        r2 = await client.get("http://localhost:8000/api/public/marketplace-stats")
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
            print(f"[Telegram] Loop error: {e}")

        await asyncio.sleep(300)  # Check toutes les 5 min


def stop():
    global _running
    _running = False
