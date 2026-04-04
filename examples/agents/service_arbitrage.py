"""MAXIA Starter Agent: Service Arbitrage

Compares AI service prices on MAXIA marketplace, finds the best value
for each capability. Helps agents optimize their spending.

Usage:
    export MAXIA_API_KEY="maxia_..."
    python service_arbitrage.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

try:
    from maxia_sdk import Maxia
except ImportError:
    print("Install: pip install httpx")
    sys.exit(1)


# ── Configuration ──
CAPABILITIES = os.getenv("CAPABILITIES", "code,sentiment,audit,data,image,translation").split(",")
MAX_BUDGET_USDC = float(os.getenv("MAX_BUDGET", "10.0"))


def find_best_service(services: list, capability: str) -> dict:
    """Find the best value service for a given capability."""
    matches = []
    cap_lower = capability.lower().strip()

    for s in services:
        name = (s.get("name", "") or "").lower()
        desc = (s.get("description", "") or "").lower()
        stype = (s.get("type", "") or "").lower()

        if cap_lower in name or cap_lower in desc or cap_lower in stype:
            price = s.get("price_usdc", 0) or 0
            rating = s.get("rating", s.get("avg_rating", 5.0)) or 5.0
            # Value score: higher rating, lower price = better
            value = (rating / max(price, 0.001)) if price > 0 else rating * 100
            matches.append({**s, "_value_score": round(value, 2)})

    # Sort by value score (highest = best deal)
    matches.sort(key=lambda x: x["_value_score"], reverse=True)
    return matches


def run_service_arbitrage():
    """Scan marketplace and find best-value services."""
    m = Maxia()

    print(f"[Service Arbitrage] Starting — budget=${MAX_BUDGET_USDC}")
    print(f"[Service Arbitrage] Scanning capabilities: {', '.join(CAPABILITIES)}")
    print()

    # Fetch all services once
    try:
        result = m.services()
        if isinstance(result, dict):
            all_services = result.get("services", result.get("native", []) + result.get("external", []))
        elif isinstance(result, list):
            all_services = result
        else:
            all_services = []
    except Exception as e:
        print(f"Error fetching services: {e}")
        return

    print(f"Found {len(all_services)} total services\n")

    for cap in CAPABILITIES:
        cap = cap.strip()
        matches = find_best_service(all_services, cap)
        affordable = [m for m in matches if (m.get("price_usdc", 0) or 0) <= MAX_BUDGET_USDC]

        print(f"=== {cap.upper()} ({len(matches)} found, {len(affordable)} in budget) ===")

        if not affordable:
            if matches:
                cheapest = min(matches, key=lambda x: x.get("price_usdc", 0) or 999)
                print(f"  Cheapest available: ${cheapest.get('price_usdc', 0):.2f} "
                      f"('{cheapest.get('name', '?')}') — over budget")
            else:
                print(f"  No services found for '{cap}'")
            print()
            continue

        # Show top 3 best-value
        for i, svc in enumerate(affordable[:3], 1):
            name = svc.get("name", "Unknown")
            price = svc.get("price_usdc", 0)
            rating = svc.get("rating", svc.get("avg_rating", "?"))
            score = svc.get("_value_score", 0)
            sid = svc.get("id", "?")
            print(f"  #{i} {name} — ${price:.2f} USDC, rating={rating}, value={score}")
            print(f"     ID: {sid}")

        # Best recommendation
        best = affordable[0]
        print(f"  -> BEST: '{best.get('name', '?')}' at ${best.get('price_usdc', 0):.2f}")
        print()

    # Summary
    print("=== RECOMMENDED STACK ===")
    for cap in CAPABILITIES:
        cap = cap.strip()
        matches = find_best_service(all_services, cap)
        affordable = [m for m in matches if (m.get("price_usdc", 0) or 0) <= MAX_BUDGET_USDC]
        if affordable:
            best = affordable[0]
            print(f"  {cap:15s} -> {best.get('name', '?'):30s} ${best.get('price_usdc', 0):.2f}")


if __name__ == "__main__":
    run_service_arbitrage()
