"""Mission 6 — Competitive watch.

Weekly scan of competitor websites with LLM-powered strategic analysis.
"""
import asyncio
import logging
from datetime import datetime

import httpx

from llm import llm
from agents import CEO_SYSTEM_PROMPT, COMPETITOR_URLS
from scheduler import send_mail

log = logging.getLogger("ceo")


async def mission_competitive_watch(mem: dict, actions: dict) -> None:
    """Scan hebdomadaire des concurrents — envoie un rapport comparatif."""
    if actions["counts"].get("competitive_watch", 0) >= 1:
        log.info("Veille concurrentielle deja faite cette semaine — skip")
        return

    competitor_data = []
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        for comp in COMPETITOR_URLS:
            snippet = ""
            try:
                resp = await client.get(comp["site"], headers={"User-Agent": "MAXIA-CEO/2.0"})
                if resp.status_code == 200:
                    # Extract text content (first 2000 chars of body for LLM analysis)
                    snippet = resp.text[:2000]
                else:
                    snippet = f"(HTTP {resp.status_code})"
            except Exception as e:
                snippet = f"(erreur: {str(e)[:60]})"

            competitor_data.append({
                "name": comp["name"], "site": comp["site"], "snippet": snippet,
            })
            await asyncio.sleep(2)  # Politesse

    # LLM analysis
    comp_text = ""
    for c in competitor_data:
        comp_text += f"\n--- {c['name']} ({c['site']}) ---\n{c['snippet'][:500]}\n"

    analysis = await llm(
        f"Analyze these AI agent marketplace competitors vs MAXIA.\n\n"
        f"MAXIA: AI-to-AI marketplace on 14 blockchains, on-chain escrow (Solana+Base), "
        f"65 token swaps, GPU rental (Akash), 17 AI services, 46 MCP tools, "
        f"tokenized stocks, autonomous CEO agent.\n\n"
        f"Competitors:\n{comp_text}\n\n"
        f"For each competitor:\n"
        f"1. What they offer that MAXIA doesn't\n"
        f"2. What MAXIA offers that they don't\n"
        f"3. Threat level (LOW/MEDIUM/HIGH)\n"
        f"4. One actionable recommendation for MAXIA\n\n"
        f"End with: Overall competitive position summary (2-3 sentences).",
        system="You are a strategic analyst for MAXIA. Be specific, factual, and actionable.",
        max_tokens=800,
    )

    # Build report
    week_num = datetime.now().isocalendar()[1]
    year = datetime.now().year
    today = datetime.now().strftime("%d/%m/%Y")

    body = f"MAXIA CEO — Veille concurrentielle semaine {week_num} ({year})\n"
    body += f"Date: {today}\n\n"
    body += "=== CONCURRENTS SCANNES ===\n"
    for c in competitor_data:
        status = "OK" if "(HTTP" not in c["snippet"] and "(erreur" not in c["snippet"] else "ERREUR"
        body += f"  - {c['name']} ({c['site']}): {status}\n"
    body += (
        f"\n=== ANALYSE STRATEGIQUE ===\n{analysis}\n"
        if analysis else "\n(analyse LLM indisponible)\n"
    )

    await send_mail(f"[MAXIA CEO] Veille concurrentielle - semaine {week_num}", body)
    actions["counts"]["competitive_watch"] = 1
    mem.setdefault("competitive_reports", []).append({
        "date": today,
        "week": week_num,
        "competitors_scanned": len(competitor_data),
    })
    log.info("Veille concurrentielle semaine %d envoyee", week_num)
