"""MAXIA Demo Agent — Full AI-to-AI marketplace loop on live production.

A trading research agent that:
1. Registers on the marketplace
2. Checks live crypto prices
3. Gets free sentiment
4. Discovers AI services
5. Checks sandbox balance
6. Buys web scraper service ($0.02, fast)
7. Buys sentiment analysis ($0.005, uses LLM)
8. Makes a trading decision based on results

Usage:
    pip install httpx
    python demo_agent.py
    python demo_agent.py --token ETH
    python demo_agent.py --production   # requires prepaid USDC credits
"""
import httpx
import sys
import time
import json
import os

BASE_URL = "https://maxiaworld.app"
DEMO_WALLET = "GJRs4BwHBFMGbGv5VbxjPuifBYr6DqYMBmFaRaHqAQWB"
KEY_FILE = ".maxia_demo_key"

MAX_RETRIES = 2
RETRY_DELAY = 5


class DemoAgent:
    def __init__(self, base_url: str = BASE_URL, sandbox: bool = True):
        self.base = base_url.rstrip("/")
        self.sandbox = sandbox
        self.api_key = self._load_key()
        self.client = httpx.Client(timeout=45, headers={"User-Agent": "maxia-demo/1.1"})

    def _load_key(self) -> str:
        if os.path.exists(KEY_FILE):
            return open(KEY_FILE).read().strip()
        return ""

    def _save_key(self, key: str) -> None:
        with open(KEY_FILE, "w") as f:
            f.write(key)

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["X-API-Key"] = self.api_key
        return h

    def _get(self, path: str, params: dict | None = None) -> dict:
        resp = self.client.get(f"{self.base}{path}", params=params, headers=self._headers())
        try:
            return resp.json()
        except Exception:
            return {"error": f"HTTP {resp.status_code}: {resp.text[:200]}"}

    def _post(self, path: str, body: dict | None = None) -> dict:
        try:
            resp = self.client.post(f"{self.base}{path}", json=body or {}, headers=self._headers())
            try:
                return resp.json()
            except Exception:
                return {"error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
        except httpx.ReadTimeout:
            return {"error": "Timeout — LLM is processing, try again"}
        except Exception as e:
            return {"error": str(e)[:200]}

    def _post_with_retry(self, path: str, body: dict | None = None) -> dict:
        """POST with retry logic for LLM-backed services."""
        for attempt in range(MAX_RETRIES + 1):
            result = self._post(path, body)
            error = result.get("error", "")
            svc_result = result.get("result", "")
            # Retry on timeout or "temporarily unavailable"
            is_timeout = "timeout" in error.lower() or "Timeout" in error
            is_unavail = "temporarily unavailable" in str(svc_result).lower()
            if (is_timeout or is_unavail) and attempt < MAX_RETRIES:
                print(f"    Retry {attempt + 1}/{MAX_RETRIES} in {RETRY_DELAY}s...")
                time.sleep(RETRY_DELAY)
                continue
            return result
        return result

    def step1_register(self) -> str:
        """Register agent and get API key."""
        if self.api_key:
            print(f"  API key loaded from {KEY_FILE}")
            return self.api_key

        data = self._post("/api/public/register", {
            "name": f"DemoTrader-{int(time.time()) % 10000:04d}",
            "wallet": DEMO_WALLET,
            "description": "Demo trading research agent — analyzes sentiment before trading",
        })
        key = data.get("api_key", "")
        if not key:
            raise RuntimeError(f"Registration failed: {data}")
        self.api_key = key
        self._save_key(key)
        return key

    def step2_prices(self, token: str) -> float:
        """Get live crypto prices."""
        data = self._get("/api/public/crypto/prices")
        prices = data.get("prices", data)
        info = prices.get(token, {})
        price = info.get("price", 0) if isinstance(info, dict) else info
        return float(price)

    def step3_free_sentiment(self, token: str) -> dict:
        """Get free sentiment (no auth needed)."""
        return self._get("/api/public/sentiment", {"token": token})

    def step4_discover(self) -> list:
        """Discover available AI services."""
        data = self._get("/api/public/discover")
        return data.get("agents", [])

    def step5_balance(self) -> float:
        """Check sandbox balance."""
        path = "/api/public/sandbox/balance" if self.sandbox else "/api/credits/balance"
        data = self._get(path)
        return float(data.get("balance_usdc", data.get("balance", 0)))

    def _ensure_key_valid(self) -> None:
        """Re-register if the API key was invalidated (e.g., server restart or multi-worker)."""
        test = self._get("/api/public/sandbox/balance")
        detail = str(test.get("detail", "")) + str(test.get("error", ""))
        bal = test.get("balance_usdc", test.get("balance", -1))
        if "invalide" in detail.lower() or "invalid" in detail.lower() or bal == 0:
            print("  API key invalid or zero balance, re-registering...")
            if os.path.exists(KEY_FILE):
                os.remove(KEY_FILE)
            self.api_key = ""
            self.step1_register()

    def step6_buy_scraper(self, token: str) -> str:
        """Buy web scraper service ($0.02, httpx-based, fast)."""
        self._ensure_key_valid()
        path = "/api/public/sandbox/execute" if self.sandbox else "/api/public/execute"
        data = self._post_with_retry(path, {
            "service_id": "maxia-scraper",
            "prompt": f"Extract structured data about {token} cryptocurrency: "
                      f"current price, 24h change, market cap, and top 3 news headlines. Return JSON.",
        })
        return data.get("result", data.get("error", str(data)))

    def step7_buy_sentiment(self, token: str) -> str:
        """Buy sentiment analysis service ($0.005, uses LLM)."""
        time.sleep(2)  # LLM rate limit buffer
        path = "/api/public/sandbox/execute" if self.sandbox else "/api/public/execute"
        data = self._post_with_retry(path, {
            "service_id": "maxia-sentiment",
            "prompt": f"Analyze sentiment for {token} based on current market conditions. "
                      f"Return JSON with: sentiment_score (0-100), confidence, label (bullish/bearish/neutral), key_factors.",
        })
        return data.get("result", data.get("error", str(data)))

    def step8_decide(self, scraper_data: str, sentiment: str) -> str:
        """Make a trading decision based on collected intelligence."""
        try:
            s = json.loads(sentiment) if isinstance(sentiment, str) and sentiment.startswith("{") else {}
            score = s.get("sentiment_score", 50)
        except (json.JSONDecodeError, TypeError):
            score = 50

        if score >= 70:
            return f"BUY — Sentiment score {score}/100, market conditions favorable"
        elif score <= 30:
            return f"SELL — Sentiment score {score}/100, bearish signals detected"
        else:
            return f"HOLD — Sentiment score {score}/100, no clear signal"

    def run(self, token: str = "SOL") -> None:
        """Execute the full demo loop."""
        mode = "SANDBOX" if self.sandbox else "PRODUCTION"
        print(f"\n{'='*60}")
        print(f"  MAXIA Demo Agent — Trading Research Loop ({mode})")
        print(f"  Target: {token} | API: {self.base}")
        print(f"{'='*60}\n")

        t0 = time.time()

        # Step 1: Register
        print("[1/8] Registering agent...")
        key = self.step1_register()
        print(f"  API Key: {key[:12]}...{key[-4:]}\n")

        # Step 2: Live prices
        print(f"[2/8] Checking {token} price...")
        price = self.step2_prices(token)
        print(f"  {token} = ${price:,.2f}\n")

        # Step 3: Free sentiment
        print(f"[3/8] Free sentiment check for {token}...")
        free_sent = self.step3_free_sentiment(token)
        label = free_sent.get("overall_sentiment", free_sent.get("sentiment", "unknown"))
        score_val = free_sent.get("score", "?")
        print(f"  Sentiment: {label} (score: {score_val})\n")

        # Step 4: Discover services
        print("[4/8] Discovering AI services...")
        services = self.step4_discover()
        print(f"  Found {len(services)} services on marketplace")
        for s in services[:5]:
            print(f"    - {s.get('name', '?')} (${s.get('price_usdc', '?')}) by {s.get('seller', '?')}")
        print()

        # Step 5: Check balance
        print("[5/8] Checking balance...")
        bal_before = self.step5_balance()
        print(f"  Balance: ${bal_before:,.2f} USDC\n")

        # Step 6: Buy web scraper ($0.02, fast)
        print(f"[6/8] Buying web scraper for {token} ($0.02)...")
        scraper_result = self.step6_buy_scraper(token)
        print(f"  Result: {scraper_result[:200]}{'...' if len(str(scraper_result)) > 200 else ''}\n")

        # Step 7: Buy sentiment analysis ($0.005)
        print(f"[7/8] Buying sentiment analysis for {token} ($0.005)...")
        sentiment_result = self.step7_buy_sentiment(token)
        print(f"  Result: {sentiment_result[:200]}{'...' if len(str(sentiment_result)) > 200 else ''}\n")

        # Step 8: Trading decision
        print("[8/8] Making trading decision...")
        decision = self.step8_decide(scraper_result, sentiment_result)
        print(f"  Decision: {decision}\n")

        # Final balance
        bal_after = self.step5_balance()
        elapsed = time.time() - t0

        print(f"{'='*60}")
        print(f"  Balance: ${bal_before:,.2f} -> ${bal_after:,.2f} (spent ${bal_before - bal_after:,.3f})")
        print(f"  Completed in {elapsed:.1f}s")
        print(f"  Mode: {mode} | Services bought: 2 (scraper + sentiment)")
        print(f"{'='*60}\n")


if __name__ == "__main__":
    token = "SOL"
    if "--token" in sys.argv:
        idx = sys.argv.index("--token")
        if idx + 1 < len(sys.argv):
            token = sys.argv[idx + 1].upper()

    sandbox = "--production" not in sys.argv

    agent = DemoAgent(sandbox=sandbox)
    agent.run(token=token)
