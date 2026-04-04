"""MAXIA Starter Agent: Sentiment Trader

Analyzes crypto market sentiment via MAXIA API and generates trading signals.
Combines sentiment score + price data for confidence-weighted decisions.

Usage:
    export MAXIA_API_KEY="maxia_..."
    python sentiment_trader.py
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
TOKENS = os.getenv("TOKENS", "BTC,ETH,SOL,BONK").split(",")
SIGNAL_INTERVAL_SEC = int(os.getenv("SIGNAL_INTERVAL", "600"))  # Every 10 min
# Sentiment thresholds (0-100 scale)
BULLISH_THRESHOLD = int(os.getenv("BULLISH_THRESHOLD", "70"))
BEARISH_THRESHOLD = int(os.getenv("BEARISH_THRESHOLD", "30"))


def analyze_signal(sentiment_score: float, price_change_24h: float) -> dict:
    """Generate trading signal from sentiment + price momentum."""
    # Combine sentiment and momentum
    if sentiment_score >= BULLISH_THRESHOLD and price_change_24h > 0:
        signal = "STRONG_BUY"
        confidence = min(95, sentiment_score + abs(price_change_24h))
    elif sentiment_score >= BULLISH_THRESHOLD:
        signal = "BUY"
        confidence = sentiment_score * 0.8
    elif sentiment_score <= BEARISH_THRESHOLD and price_change_24h < 0:
        signal = "STRONG_SELL"
        confidence = min(95, (100 - sentiment_score) + abs(price_change_24h))
    elif sentiment_score <= BEARISH_THRESHOLD:
        signal = "SELL"
        confidence = (100 - sentiment_score) * 0.8
    else:
        signal = "HOLD"
        confidence = 50

    return {"signal": signal, "confidence": round(confidence, 1)}


def run_sentiment_trader():
    """Main loop: fetch sentiment, generate signals."""
    m = Maxia()

    print(f"[Sentiment Trader] Starting — tokens={','.join(TOKENS)}")
    print(f"[Sentiment Trader] Bullish>{BULLISH_THRESHOLD}, Bearish<{BEARISH_THRESHOLD}")
    print()

    cycle = 0
    while True:
        cycle += 1
        print(f"--- Signals #{cycle} ({time.strftime('%H:%M:%S')}) ---")

        # Get prices first for momentum data
        prices = {}
        try:
            price_data = m.prices()
            if isinstance(price_data, dict):
                prices = price_data.get("prices", price_data)
        except Exception:
            pass

        for token in TOKENS:
            token = token.strip().upper()
            try:
                # Get sentiment
                sent = m.sentiment(token)
                score = 50  # default neutral
                if isinstance(sent, dict):
                    score = sent.get("score", sent.get("sentiment_score", 50)) or 50

                # Get price change
                price_change = 0.0
                token_price = prices.get(token.lower(), prices.get(token, {}))
                if isinstance(token_price, dict):
                    price_change = token_price.get("change_24h", 0) or 0
                    current = token_price.get("price", token_price.get("usd", 0))
                else:
                    current = 0

                # Generate signal
                result = analyze_signal(score, price_change)

                emoji_map = {
                    "STRONG_BUY": "++", "BUY": "+", "HOLD": "=",
                    "SELL": "-", "STRONG_SELL": "--",
                }
                indicator = emoji_map.get(result["signal"], "?")

                price_str = f"${current:,.2f}" if current else "N/A"
                print(f"  {token:6s} [{indicator}] {result['signal']:12s} "
                      f"confidence={result['confidence']:.0f}% "
                      f"sentiment={score:.0f} price={price_str} 24h={price_change:+.1f}%")

            except Exception as e:
                print(f"  {token:6s} [?] Error: {e}")

        print()

        if os.getenv("SINGLE_RUN"):
            break

        time.sleep(SIGNAL_INTERVAL_SEC)


if __name__ == "__main__":
    run_sentiment_trader()
