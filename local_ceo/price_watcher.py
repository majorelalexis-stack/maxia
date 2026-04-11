"""Price Watcher — Veille prix concurrents + auto-adjust.

#10: Surveille Jupiter, RunPod, Certik. Baisse si concurrent moins cher.
#11: Analyse tendances tokens + sujets qui buzzent.
"""
import asyncio
import httpx
import time
import json

# Conservative fallback prices. The real values come from the VPS via
# ``local_ceo.live_prices.get_live_maxia_prices()`` on every call to
# ``check_competitor_prices``. These are only used if the VPS is
# unreachable at the moment of the check (so competitor alerts always
# compare against *something* sensible instead of crashing).
_HARDCODED_FALLBACK = {
    "gpu_rtx4090": 0.46,       # $/h (last observed live value, 2026-04-11)
    "gpu_a100": 1.19,
    "gpu_h100": 2.69,
    "swap_fee_bps": 10,        # 0.10% (bronze tier)
    "audit_basic": 4.99,
}

MAXIA_PRICES = dict(_HARDCODED_FALLBACK)  # kept for legacy imports


async def _load_maxia_prices() -> dict:
    """Return live MAXIA prices if available, else the hardcoded
    fallback. Never raises."""
    try:
        from local_ceo.live_prices import get_live_maxia_prices  # type: ignore
        live = await get_live_maxia_prices()
        if live:
            return live
    except Exception:
        pass
    return dict(_HARDCODED_FALLBACK)

# Sources concurrentes a surveiller
COMPETITORS = {
    "gpu": [
        {"name": "RunPod", "url": "https://www.runpod.io/gpu-cloud", "type": "scrape"},
        {"name": "Lambda", "url": "https://lambdalabs.com/service/gpu-cloud", "type": "scrape"},
    ],
    "swap": [
        {"name": "Jupiter", "url": "https://jup.ag", "type": "api"},
    ],
}


async def check_competitor_prices(browser) -> list:
    """Scrape les prix concurrents et compare avec MAXIA."""
    live_prices = await _load_maxia_prices()
    alerts = []
    for category, sources in COMPETITORS.items():
        for source in sources:
            try:
                text = await browser.browse_and_extract(source["url"], "main")
                # Chercher des prix dans le texte
                import re
                prices = re.findall(r'\$(\d+\.?\d*)/h', text)
                if prices:
                    lowest = min(float(p) for p in prices)
                    maxia_price = float(live_prices.get("gpu_rtx4090", 999))
                    if lowest < maxia_price:
                        alerts.append({
                            "competitor": source["name"],
                            "category": category,
                            "their_price": lowest,
                            "our_price": maxia_price,
                            "action": f"Lower {category} to ${lowest}",
                        })
            except Exception:
                pass
    return alerts


async def analyze_trends(browser) -> dict:
    """Analyse les tendances: tokens trending, sujets qui buzzent."""
    trends = {"tokens": [], "topics": [], "opportunities": []}

    try:
        # DexScreener trending
        text = await browser.browse_and_extract("https://dexscreener.com/solana", "main")
        import re
        tokens = re.findall(r'([A-Z]{2,6})\s*\$[\d.]+', text[:2000])
        trends["tokens"] = list(set(tokens))[:10]
    except Exception:
        pass

    # Twitter trending — DISABLED (Plan CEO V9, Twitter removed 2026-04-09)
    trends["topics"] = []

    return trends
