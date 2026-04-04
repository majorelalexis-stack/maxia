"""MAXIA Starter Agent: Auto Researcher

Pipeline agent that chains MAXIA services: scrape -> summarize -> translate.
Demonstrates multi-service orchestration in a single autonomous flow.

Usage:
    export MAXIA_API_KEY="maxia_..."
    python auto_researcher.py "artificial intelligence agents 2026"
"""
import os
import sys
import time
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

try:
    from maxia_sdk import Maxia
except ImportError:
    print("Install: pip install httpx")
    sys.exit(1)


# ── Configuration ──
TARGET_LANGUAGE = os.getenv("TARGET_LANGUAGE", "french")
MAX_BUDGET_USDC = float(os.getenv("MAX_BUDGET", "1.0"))


def find_service_by_type(services: list, service_type: str) -> dict:
    """Find the cheapest active service matching a type."""
    matches = []
    for s in services:
        name = (s.get("name", "") or "").lower()
        desc = (s.get("description", "") or "").lower()
        stype = (s.get("type", "") or "").lower()
        if service_type in name or service_type in desc or service_type in stype:
            matches.append(s)

    if not matches:
        return {}

    # Sort by price (cheapest first)
    matches.sort(key=lambda x: x.get("price_usdc", 0) or 999)
    return matches[0]


def run_research_pipeline(query: str):
    """Execute the research pipeline: scrape -> summarize -> translate."""
    m = Maxia()

    print(f"[Auto Researcher] Query: '{query}'")
    print(f"[Auto Researcher] Target language: {TARGET_LANGUAGE}")
    print(f"[Auto Researcher] Budget: ${MAX_BUDGET_USDC}")
    print()

    # Step 0: Discover available services
    print("Step 0: Discovering services...")
    try:
        result = m.services()
        if isinstance(result, dict):
            all_services = result.get("services", result.get("native", []) + result.get("external", []))
        elif isinstance(result, list):
            all_services = result
        else:
            all_services = []
        print(f"  Found {len(all_services)} services")
    except Exception as e:
        print(f"  Error: {e}")
        return

    total_cost = 0.0

    # Step 1: Scrape / Search
    print("\nStep 1: Web scraping...")
    scraper = find_service_by_type(all_services, "scrap")
    if scraper:
        price = scraper.get("price_usdc", 0) or 0
        print(f"  Using: '{scraper['name']}' (${price})")
        try:
            scrape_result = m.execute(scraper["id"], f"Search and scrape: {query}")
            total_cost += price
            scrape_text = ""
            if isinstance(scrape_result, dict):
                scrape_text = scrape_result.get("result", scrape_result.get("output", str(scrape_result)))
            else:
                scrape_text = str(scrape_result)
            print(f"  Scraped {len(scrape_text)} chars")
        except Exception as e:
            print(f"  Scrape failed: {e}")
            scrape_text = f"Research query: {query}. Unable to scrape, please summarize the topic from your knowledge."
    else:
        print("  No scraper found — using query as input")
        scrape_text = query

    # Step 2: Summarize
    print("\nStep 2: AI summarization...")
    summarizer = find_service_by_type(all_services, "summar")
    if not summarizer:
        summarizer = find_service_by_type(all_services, "code")  # Fallback to code gen
    if summarizer:
        price = summarizer.get("price_usdc", 0) or 0
        if total_cost + price <= MAX_BUDGET_USDC:
            print(f"  Using: '{summarizer['name']}' (${price})")
            try:
                summary_result = m.execute(
                    summarizer["id"],
                    f"Summarize this research in 3-5 key points:\n\n{scrape_text[:2000]}"
                )
                total_cost += price
                summary = ""
                if isinstance(summary_result, dict):
                    summary = summary_result.get("result", summary_result.get("output", str(summary_result)))
                else:
                    summary = str(summary_result)
                print(f"  Summary: {len(summary)} chars")
            except Exception as e:
                print(f"  Summary failed: {e}")
                summary = scrape_text[:500]
        else:
            print(f"  Over budget (${total_cost + price:.2f} > ${MAX_BUDGET_USDC})")
            summary = scrape_text[:500]
    else:
        print("  No summarizer found")
        summary = scrape_text[:500]

    # Step 3: Translate
    print(f"\nStep 3: Translation to {TARGET_LANGUAGE}...")
    translator = find_service_by_type(all_services, "translat")
    if translator:
        price = translator.get("price_usdc", 0) or 0
        if total_cost + price <= MAX_BUDGET_USDC:
            print(f"  Using: '{translator['name']}' (${price})")
            try:
                trans_result = m.execute(
                    translator["id"],
                    f"Translate to {TARGET_LANGUAGE}:\n\n{summary[:1500]}"
                )
                total_cost += price
                translated = ""
                if isinstance(trans_result, dict):
                    translated = trans_result.get("result", trans_result.get("output", str(trans_result)))
                else:
                    translated = str(trans_result)
                print(f"  Translated: {len(translated)} chars")
            except Exception as e:
                print(f"  Translation failed: {e}")
                translated = summary
        else:
            print(f"  Over budget — skipping translation")
            translated = summary
    else:
        print(f"  No translator found — returning English summary")
        translated = summary

    # Final output
    print("\n" + "=" * 60)
    print("RESEARCH RESULT")
    print("=" * 60)
    print(translated[:2000] if translated else summary[:2000])
    print("=" * 60)
    print(f"\nTotal cost: ${total_cost:.2f} USDC")
    print(f"Services used: {3 - [scraper, summarizer, translator].count({})}/3")


if __name__ == "__main__":
    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Latest AI agent marketplace trends 2026"
    run_research_pipeline(query)
