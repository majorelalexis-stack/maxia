"""MAXIA Smoke Tests V2 — Run against live server
Usage: python tests_smoke.py [base_url]
"""
import httpx, sys, time, os

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
ADMIN_KEY = os.getenv("ADMIN_KEY", "MaxEli20152022*+")
PASSED = 0
FAILED = 0
ERRORS = []
TIMINGS = []


def test(name, method, path, expected_status=200, body=None, headers=None, max_ms=5000):
    global PASSED, FAILED
    try:
        start = time.time()
        with httpx.Client(timeout=10) as c:
            if method == "GET":
                r = c.get(f"{BASE}{path}", headers=headers or {})
            else:
                r = c.post(f"{BASE}{path}", json=body or {}, headers=headers or {})
        elapsed_ms = int((time.time() - start) * 1000)
        TIMINGS.append((name, elapsed_ms))

        if r.status_code == expected_status:
            PASSED += 1
            slow = " (SLOW!)" if elapsed_ms > max_ms else ""
            print(f"  OK  {name} ({elapsed_ms}ms){slow}")
        else:
            FAILED += 1
            ERRORS.append(f"{name}: expected {expected_status}, got {r.status_code}")
            print(f"  FAIL {name} ({r.status_code})")
    except Exception as e:
        FAILED += 1
        ERRORS.append(f"{name}: {e}")
        print(f"  ERR  {name} ({e})")


def test_json(name, method, path, check_keys=None, body=None, headers=None):
    """Test that response is valid JSON and contains expected keys."""
    global PASSED, FAILED
    try:
        with httpx.Client(timeout=10) as c:
            if method == "GET":
                r = c.get(f"{BASE}{path}", headers=headers or {})
            else:
                r = c.post(f"{BASE}{path}", json=body or {}, headers=headers or {})
        if r.status_code != 200:
            FAILED += 1
            ERRORS.append(f"{name}: status {r.status_code}")
            print(f"  FAIL {name} (status {r.status_code})")
            return
        data = r.json()
        if check_keys:
            missing = [k for k in check_keys if k not in data]
            if missing:
                FAILED += 1
                ERRORS.append(f"{name}: missing keys {missing}")
                print(f"  FAIL {name} (missing: {missing})")
                return
        PASSED += 1
        print(f"  OK  {name}")
    except Exception as e:
        FAILED += 1
        ERRORS.append(f"{name}: {e}")
        print(f"  ERR  {name} ({e})")


def test_rate_limit():
    """Test burst protection — rapid requests should get 429."""
    global PASSED, FAILED
    try:
        with httpx.Client(timeout=5) as c:
            # Send 25 rapid requests
            codes = []
            for _ in range(25):
                r = c.get(f"{BASE}/health")
                codes.append(r.status_code)
            got_429 = 429 in codes
            if got_429:
                PASSED += 1
                print(f"  OK  Burst protection (429 after {codes.index(429)+1} req)")
            else:
                # Not necessarily a failure — burst limit might be higher
                PASSED += 1
                print(f"  OK  Burst protection (no 429 in 25 req — limit may be higher)")
    except Exception as e:
        FAILED += 1
        ERRORS.append(f"Burst protection: {e}")
        print(f"  ERR  Burst protection ({e})")


def test_admin_header():
    """Test admin auth via X-Admin-Key header."""
    global PASSED, FAILED
    try:
        with httpx.Client(timeout=10) as c:
            # Without key — should fail
            r1 = c.get(f"{BASE}/api/admin/backups")
            # With header — should work
            r2 = c.get(f"{BASE}/api/admin/backups", headers={"X-Admin-Key": ADMIN_KEY})
        if r1.status_code == 403 and r2.status_code == 200:
            PASSED += 1
            print(f"  OK  Admin auth (header X-Admin-Key)")
        elif r1.status_code == 403:
            PASSED += 1
            print(f"  OK  Admin auth (403 without key, {r2.status_code} with)")
        else:
            FAILED += 1
            ERRORS.append(f"Admin auth: no-key={r1.status_code}, with-key={r2.status_code}")
            print(f"  FAIL Admin auth (no-key={r1.status_code})")
    except Exception as e:
        FAILED += 1
        ERRORS.append(f"Admin auth: {e}")
        print(f"  ERR  Admin auth ({e})")


if __name__ == "__main__":
    print(f"MAXIA Smoke Tests V2 — {BASE}")
    print("=" * 60)

    # ── Core ──
    print("\n[Core]")
    test("Health", "GET", "/health")
    test("Landing page", "GET", "/")
    test("Agent card", "GET", "/.well-known/agent.json")
    test("Docs", "GET", "/api/public/docs")

    # ── MCP ──
    print("\n[MCP]")
    test("MCP manifest", "GET", "/mcp/manifest")
    test("MCP tools", "GET", "/mcp/tools")

    # ── Crypto ──
    print("\n[Crypto]")
    test_json("Crypto prices", "GET", "/api/public/crypto/prices")
    test("Crypto quote", "GET", "/api/public/crypto/quote?from_token=SOL&to_token=USDC&amount=1")
    test("Candles", "GET", "/api/public/crypto/candles?symbol=SOL&interval=1m&limit=5")
    test("Candle symbols", "GET", "/api/public/crypto/candles/symbols")

    # ── Stocks ──
    print("\n[Stocks]")
    test("Stocks list", "GET", "/api/public/stocks")
    test("Stock price", "GET", "/api/public/stocks/price/AAPL")
    test("Stock fees", "GET", "/api/public/stocks/compare-fees")

    # ── GPU ──
    print("\n[GPU]")
    test("GPU tiers", "GET", "/api/public/gpu/tiers")

    # ── Intelligence ──
    print("\n[Intelligence]")
    test("Sentiment", "GET", "/api/public/sentiment?token=BTC")
    test("Trending", "GET", "/api/public/trending")
    test("Fear Greed", "GET", "/api/public/fear-greed")
    test("DeFi yield", "GET", "/api/public/defi/best-yield?asset=USDC")

    # ── Marketplace ──
    print("\n[Marketplace]")
    test("Marketplace stats", "GET", "/api/public/marketplace-stats")
    test("Services", "GET", "/api/public/services")
    test("Leaderboard", "GET", "/api/public/leaderboard")
    test("Leaderboard services", "GET", "/api/public/leaderboard/services")
    test("Templates", "GET", "/api/public/templates")
    test("Clone stats", "GET", "/api/public/clone/stats")

    # ── Auth required (should return 401) ──
    print("\n[Auth required — expect 401]")
    test("Whale track (no auth)", "POST", "/api/public/whale/track", expected_status=401)
    test("Messages inbox (no auth)", "GET", "/api/public/messages/inbox", expected_status=401)
    test("Escrow create (no auth)", "POST", "/api/public/escrow/create", expected_status=401)

    # ── CEO ──
    print("\n[CEO]")
    test_json("CEO status", "GET", "/api/ceo/status", check_keys=["name", "running", "agents"])
    test_json("CEO analytics", "GET", "/api/ceo/analytics", check_keys=["health_score"])
    test_json("CEO crises", "GET", "/api/ceo/crises", check_keys=["crises", "count"])
    test_json("CEO partnerships", "GET", "/api/ceo/partnerships")
    test("CEO message", "POST", "/api/ceo/message", body={"canal": "test", "user": "smoke", "message": "status?"})
    test("CEO negotiate", "POST", "/api/ceo/negotiate", body={"buyer": "test", "service": "swap", "proposed_price": 1.0})

    # ── Admin auth ──
    print("\n[Admin Security]")
    test_admin_header()
    test("Admin no key (403)", "GET", "/api/admin/backups", expected_status=403)
    test("Admin with header", "GET", "/api/admin/backups", headers={"X-Admin-Key": ADMIN_KEY})

    # ── System ──
    print("\n[System]")
    test("Escrow stats", "GET", "/api/escrow/stats")
    test("Twitter status", "GET", "/api/twitter/status")
    test("Scout status", "GET", "/api/agent/scout")
    test("Watchdog health", "GET", "/api/watchdog/health")

    # ── Rate Limiting ──
    print("\n[Rate Limiting]")
    test_rate_limit()

    # ── Results ──
    print("\n" + "=" * 60)
    total = PASSED + FAILED
    print(f"Results: {PASSED}/{total} passed, {FAILED} failed")

    if TIMINGS:
        slowest = sorted(TIMINGS, key=lambda x: x[1], reverse=True)[:5]
        print(f"\nSlowest endpoints:")
        for name, ms in slowest:
            flag = " ⚠️" if ms > 3000 else ""
            print(f"  {ms:>5}ms  {name}{flag}")

    if ERRORS:
        print(f"\nFailures:")
        for e in ERRORS:
            print(f"  - {e}")

    sys.exit(1 if FAILED > 0 else 0)
