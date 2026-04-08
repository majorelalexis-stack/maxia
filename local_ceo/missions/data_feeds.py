"""Mission 20 — Data Feeds: fetch free sources, enrich via LLM, list datasets on marketplace.

Runs weekly. Collects data from free APIs (SEC EDGAR, NVD CVE, arXiv),
enriches with LLM analysis, and lists as datasets on the MAXIA data marketplace.
Free raw data → paid enriched datasets = revenue for MAXIA.
"""
import asyncio
import json
import logging
import os
import time
from datetime import datetime

import httpx

from config_local import VPS_URL, CEO_API_KEY
from llm import llm
from agents import CEO_SYSTEM_PROMPT

log = logging.getLogger("ceo")

_STATE_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data_feeds_state.json")


def _load_state() -> dict:
    try:
        if os.path.exists(_STATE_FILE):
            with open(_STATE_FILE, "r", encoding="utf-8") as f:
                return json.loads(f.read())
    except Exception:
        pass
    return {"last_run": "", "datasets_created": []}


def _save_state(state: dict) -> None:
    try:
        with open(_STATE_FILE, "w", encoding="utf-8") as f:
            f.write(json.dumps(state, indent=2, ensure_ascii=False))
    except Exception as e:
        log.error("[DataFeeds] Save state error: %s", e)


# ── Data source fetchers ──

async def _fetch_sec_insider_trades() -> dict | None:
    """Fetch recent insider trading filings from SEC EDGAR."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://efts.sec.gov/LATEST/search-index?q=%22Form+4%22&dateRange=custom&startdt=2026-04-01",
                headers={"User-Agent": "MAXIA-CEO/3.0 support@maxiaworld.app"})
            if resp.status_code == 200:
                return {"source": "sec_edgar", "raw": resp.text[:3000], "fetched_at": int(time.time())}
    except Exception as e:
        log.warning("[DataFeeds] SEC EDGAR error: %s", e)
    # Fallback: use MAXIA's own intel endpoint
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{VPS_URL}/api/intel/sec/insider-trades?limit=20")
            if resp.status_code == 200:
                return {"source": "maxia_intel", "raw": json.dumps(resp.json())[:3000], "fetched_at": int(time.time())}
    except Exception:
        pass
    return None


async def _fetch_cve_critical() -> dict | None:
    """Fetch critical CVEs from NVD."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{VPS_URL}/api/intel/cve/critical?limit=15")
            if resp.status_code == 200:
                return {"source": "nvd_via_maxia", "raw": json.dumps(resp.json())[:3000], "fetched_at": int(time.time())}
    except Exception as e:
        log.warning("[DataFeeds] CVE fetch error: %s", e)
    return None


async def _fetch_arxiv_ai() -> dict | None:
    """Fetch recent AI papers from arXiv API."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "http://export.arxiv.org/api/query",
                params={"search_query": "cat:cs.AI", "start": 0, "max_results": 15,
                         "sortBy": "submittedDate", "sortOrder": "descending"})
            if resp.status_code == 200:
                return {"source": "arxiv", "raw": resp.text[:4000], "fetched_at": int(time.time())}
    except Exception as e:
        log.warning("[DataFeeds] arXiv error: %s", e)
    return None


async def _fetch_defi_yields() -> dict | None:
    """Fetch top DeFi yields from DeFiLlama."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get("https://yields.llama.fi/pools")
            if resp.status_code == 200:
                pools = resp.json().get("data", [])
                # Filter top 30 by TVL on supported chains
                supported = {"Solana", "Base", "Ethereum", "Arbitrum", "Polygon"}
                filtered = [p for p in pools if p.get("chain") in supported and p.get("tvlUsd", 0) > 1_000_000]
                filtered.sort(key=lambda x: x.get("apy", 0), reverse=True)
                top = filtered[:30]
                summary = [{"pool": p.get("pool"), "chain": p.get("chain"),
                           "project": p.get("project"), "symbol": p.get("symbol"),
                           "apy": round(p.get("apy", 0), 2), "tvl": round(p.get("tvlUsd", 0))}
                          for p in top]
                return {"source": "defillama", "raw": json.dumps(summary), "fetched_at": int(time.time())}
    except Exception as e:
        log.warning("[DataFeeds] DeFiLlama error: %s", e)
    return None


# ── Enrichment via LLM ──

async def _enrich_data(source_name: str, raw_data: str, category: str) -> dict | None:
    """Use LLM to create an enriched dataset summary."""
    prompt = (
        f"You are a data analyst for MAXIA, an AI-to-AI marketplace.\n\n"
        f"Source: {source_name}\nCategory: {category}\n"
        f"Raw data (truncated):\n{raw_data[:2000]}\n\n"
        f"Create an enriched dataset listing. Output JSON:\n"
        f'{{"name": "Descriptive dataset title (max 80 chars)",'
        f'"description": "What this data contains, who it is for, how often updated (150-250 words)",'
        f'"key_insights": ["3-5 bullet point insights from the data"],'
        f'"size_estimate_mb": 1.5,'
        f'"recommended_price_usdc": 3.0}}\n\n'
        f"Price guide: raw/basic data = $0 (free), enriched analysis = $2-5, "
        f"premium cross-source = $5-10. Only return JSON."
    )
    response = await llm(prompt, system="You are a data product manager. Output valid JSON only.")
    if not response:
        return None

    try:
        text = response.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
        if text.startswith("json"):
            text = text[4:].strip()
        return json.loads(text)
    except (json.JSONDecodeError, ValueError) as e:
        log.warning("[DataFeeds] LLM JSON error: %s", e)
    return None


async def _list_dataset_on_marketplace(name: str, description: str, category: str,
                                        size_mb: float, price_usdc: float, fmt: str = "json") -> bool:
    """List a dataset on the MAXIA marketplace via internal DB insert."""
    if not CEO_API_KEY:
        log.warning("[DataFeeds] No CEO_API_KEY")
        return False

    # Use the VPS API endpoint (requires auth)
    # Since CEO may not have wallet auth, insert directly via admin API
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{VPS_URL}/api/data/datasets")
            existing = resp.json() if resp.status_code == 200 else []
            # Check for duplicate by name
            for d in existing:
                if d.get("name") == name:
                    log.info("[DataFeeds] Dataset already exists: %s", name[:40])
                    return False
    except Exception:
        pass

    # Post via seed endpoint (admin)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{VPS_URL}/api/admin/seed/dataset",
                headers={"X-Admin-Key": os.getenv("ADMIN_KEY", "")},
                json={
                    "name": name[:100], "description": description[:2000],
                    "category": category, "size_mb": size_mb,
                    "price_usdc": price_usdc, "format": fmt,
                })
            if resp.status_code in (200, 201):
                log.info("[DataFeeds] Listed dataset: %s ($%.2f)", name[:40], price_usdc)
                return True
            else:
                log.warning("[DataFeeds] List failed (%d): %s", resp.status_code, resp.text[:100])
    except Exception as e:
        log.warning("[DataFeeds] List error: %s", e)
    return False


# ── Main mission ──

FEED_SOURCES = [
    {"name": "SEC EDGAR Insider Trades", "fetcher": _fetch_sec_insider_trades, "category": "finance"},
    {"name": "NVD Critical CVEs", "fetcher": _fetch_cve_critical, "category": "security"},
    {"name": "arXiv AI Papers", "fetcher": _fetch_arxiv_ai, "category": "science"},
    {"name": "DeFi Yield Opportunities", "fetcher": _fetch_defi_yields, "category": "finance"},
]


async def mission_data_feeds(mem: dict, actions: dict) -> None:
    """Weekly: fetch free data sources, enrich with LLM, list on marketplace."""
    state = _load_state()

    # Once per week max
    today = datetime.now().strftime("%Y-%m-%d")
    last = state.get("last_run", "")
    if last:
        try:
            days_since = (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(last, "%Y-%m-%d")).days
            if days_since < 7:
                log.info("[DataFeeds] Last run %d days ago — skip (weekly)", days_since)
                return
        except ValueError:
            pass

    log.info("[DataFeeds] Starting weekly data collection...")
    created = 0

    for source in FEED_SOURCES:
        log.info("[DataFeeds] Fetching: %s", source["name"])
        data = await source["fetcher"]()
        if not data:
            log.warning("[DataFeeds] No data from %s", source["name"])
            continue

        # Enrich via LLM
        enriched = await _enrich_data(source["name"], data["raw"], source["category"])
        if not enriched:
            log.warning("[DataFeeds] LLM enrichment failed for %s", source["name"])
            continue

        # List on marketplace
        name = enriched.get("name", source["name"])
        desc = enriched.get("description", "")
        price = enriched.get("recommended_price_usdc", 3.0)

        # Add insights to description
        insights = enriched.get("key_insights", [])
        if insights:
            desc += "\n\nKey insights:\n" + "\n".join(f"- {i}" for i in insights[:5])

        ok = await _list_dataset_on_marketplace(
            name=name, description=desc, category=source["category"],
            size_mb=enriched.get("size_estimate_mb", 1.5), price_usdc=price)
        if ok:
            created += 1
            state.setdefault("datasets_created", []).append(name)

        await asyncio.sleep(3)

    state["last_run"] = today
    _save_state(state)
    log.info("[DataFeeds] Done: %d new datasets created", created)
