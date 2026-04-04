"""MAXIA Starter Agent: DeFi Yield Hunter

Scans DeFi yields across 15 chains and alerts when APY exceeds your threshold.
Runs autonomously — just set your MAXIA_API_KEY and go.

Usage:
    export MAXIA_API_KEY="maxia_..."  # or omit for sandbox auto-register
    python defi_yield_hunter.py
"""
import os
import sys
import time
import json

# pip install maxia httpx
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

try:
    from maxia_sdk import Maxia
except ImportError:
    print("Install: pip install httpx")
    sys.exit(1)


# ── Configuration ──
YIELD_THRESHOLD_APY = float(os.getenv("YIELD_THRESHOLD", "8.0"))  # Alert if APY > 8%
SCAN_INTERVAL_SEC = int(os.getenv("SCAN_INTERVAL", "300"))  # Every 5 minutes
ASSETS_TO_TRACK = ["USDC", "SOL", "ETH", "BTC"]


def run_yield_hunter():
    """Main loop: scan yields, alert on opportunities."""
    m = Maxia()  # Auto-detects MAXIA_API_KEY from env, or auto-registers sandbox

    print(f"[DeFi Yield Hunter] Starting — threshold={YIELD_THRESHOLD_APY}% APY")
    print(f"[DeFi Yield Hunter] Tracking: {', '.join(ASSETS_TO_TRACK)}")
    print(f"[DeFi Yield Hunter] API key: {m.api_key[:12]}..." if m.api_key else "[DeFi Yield Hunter] No API key (limited mode)")
    print()

    cycle = 0
    while True:
        cycle += 1
        print(f"--- Scan #{cycle} ({time.strftime('%H:%M:%S')}) ---")

        for asset in ASSETS_TO_TRACK:
            try:
                # Fetch best DeFi yields for this asset
                result = m._get(f"/api/public/defi/best-yield?asset={asset}")

                if isinstance(result, dict) and "yields" in result:
                    yields = result["yields"]
                elif isinstance(result, list):
                    yields = result
                else:
                    yields = []

                # Filter high-yield opportunities
                opportunities = []
                for y in yields:
                    apy = y.get("apy", 0)
                    if apy and apy > YIELD_THRESHOLD_APY:
                        opportunities.append(y)

                if opportunities:
                    print(f"  {asset}: {len(opportunities)} opportunities above {YIELD_THRESHOLD_APY}% APY")
                    for opp in opportunities[:3]:  # Top 3
                        protocol = opp.get("protocol", "Unknown")
                        chain = opp.get("chain", "?")
                        apy = opp.get("apy", 0)
                        print(f"    -> {protocol} ({chain}): {apy:.2f}% APY")
                else:
                    print(f"  {asset}: No yields above {YIELD_THRESHOLD_APY}% APY")

            except Exception as e:
                print(f"  {asset}: Error — {e}")

        print()

        if os.getenv("SINGLE_RUN"):
            break

        time.sleep(SCAN_INTERVAL_SEC)


if __name__ == "__main__":
    run_yield_hunter()
