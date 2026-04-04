"""MAXIA Starter Agent: Whale Tracker

Monitors large Solana wallets and alerts on significant transfers.
Uses MAXIA wallet analysis API for on-chain intelligence.

Usage:
    export MAXIA_API_KEY="maxia_..."  # or omit for sandbox
    python whale_tracker.py
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
# Add whale wallets to monitor (Solana addresses)
WHALE_WALLETS = [
    # Example: large known Solana wallets (replace with real ones)
    os.getenv("WHALE_WALLET_1", ""),
    os.getenv("WHALE_WALLET_2", ""),
]
WHALE_WALLETS = [w for w in WHALE_WALLETS if w]  # Remove empty

CHECK_INTERVAL_SEC = int(os.getenv("CHECK_INTERVAL", "120"))  # Every 2 minutes
ALERT_THRESHOLD_USD = float(os.getenv("ALERT_THRESHOLD", "10000"))  # Alert if > $10K move


def run_whale_tracker():
    """Main loop: check whale wallets for large movements."""
    m = Maxia()

    print(f"[Whale Tracker] Starting — threshold=${ALERT_THRESHOLD_USD:,.0f}")
    print(f"[Whale Tracker] Monitoring {len(WHALE_WALLETS)} wallets")

    if not WHALE_WALLETS:
        print("[Whale Tracker] No wallets configured!")
        print("  Set WHALE_WALLET_1 and WHALE_WALLET_2 environment variables.")
        print("  Example: export WHALE_WALLET_1='7xKXtg...'")
        return

    # Track previous balances for delta detection
    prev_balances: dict[str, float] = {}

    cycle = 0
    while True:
        cycle += 1
        print(f"\n--- Check #{cycle} ({time.strftime('%H:%M:%S')}) ---")

        for wallet in WHALE_WALLETS:
            short = wallet[:8] + "..."
            try:
                analysis = m._get(f"/api/public/wallet-analysis?address={wallet}")

                if isinstance(analysis, dict):
                    balance = analysis.get("sol_balance", 0) or 0
                    usd_value = analysis.get("estimated_usd", 0) or 0
                    token_count = analysis.get("token_count", 0) or 0

                    # Check for large movements
                    prev = prev_balances.get(wallet, balance)
                    delta = abs(balance - prev)
                    delta_usd = delta * (usd_value / balance if balance > 0 else 0)

                    if delta_usd > ALERT_THRESHOLD_USD and prev_balances.get(wallet) is not None:
                        direction = "IN" if balance > prev else "OUT"
                        print(f"  ALERT {short}: {direction} ~${delta_usd:,.0f} ({delta:.2f} SOL)")
                    else:
                        print(f"  {short}: {balance:.2f} SOL (~${usd_value:,.0f}), {token_count} tokens")

                    prev_balances[wallet] = balance
                else:
                    print(f"  {short}: Unexpected response format")

            except Exception as e:
                print(f"  {short}: Error — {e}")

        if os.getenv("SINGLE_RUN"):
            break

        time.sleep(CHECK_INTERVAL_SEC)


if __name__ == "__main__":
    run_whale_tracker()
