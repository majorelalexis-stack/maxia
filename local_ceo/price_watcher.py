"""Price Watcher — Veille prix concurrents + auto-adjust.

#10: Surveille Jupiter, RunPod, Certik. Baisse si concurrent moins cher.
#11: Analyse tendances tokens + sujets qui buzzent.
"""
import asyncio
import httpx
import time
import json

# Prix MAXIA actuels (a synchroniser avec le VPS)
MAXIA_PRICES = {
    "gpu_rtx4090": 0.69,      # $/h
    "gpu_a100": 1.79,
    "gpu_h100": 2.69,
    "swap_fee_bps": 50,        # 0.5%
    "audit_basic": 9.99,
}

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
                    maxia_price = MAXIA_PRICES.get(f"gpu_rtx4090", 999)
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
