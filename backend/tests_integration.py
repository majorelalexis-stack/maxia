"""MAXIA Integration Tests — Full flow testing against live server"""
import httpx, sys, time, uuid

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001"
PASSED = 0
FAILED = 0
ERRORS = []

def ok(name):
    global PASSED; PASSED += 1; print(f"  OK  {name}")
def fail(name, reason):
    global FAILED; FAILED += 1; ERRORS.append(f"{name}: {reason}"); print(f"  FAIL {name} ({reason})")

if __name__ == "__main__":
    print("MAXIA Integration Tests")
    print("=" * 50)
    c = httpx.Client(base_url=BASE, timeout=15)

    # Flow 1: Register → Sell → Discover → Execute
    print("\n--- Flow 1: Marketplace ---")
    wallet = f"Test{uuid.uuid4().hex}{uuid.uuid4().hex[:6]}"  # 38 chars, valid Solana-like length
    r = c.post("/api/public/register", json={"name": f"TestBot_{wallet}", "wallet": wallet})
    d = r.json()
    if d.get("api_key"):
        api_key = d["api_key"]; ok(f"Register ({d.get('tier','?')})")
    else:
        fail("Register", str(d)); sys.exit(1)

    headers = {"X-API-Key": api_key}

    r = c.post("/api/public/sell", headers=headers, json={"name": "Test Service", "description": "Integration test", "price_usdc": 0.01})
    d = r.json()
    if d.get("success") or d.get("service_id"):
        svc_id = d.get("service_id", ""); ok(f"Sell service ({svc_id[:8]})")
    else:
        fail("Sell", str(d))

    r = c.get("/api/public/discover", params={"capability": "test"})
    d = r.json()
    ok(f"Discover ({len(d.get('agents',[]))} results)")

    r = c.get("/api/public/services")
    d = r.json()
    services = d.get("services", d) if isinstance(d, dict) else d
    ok(f"Services ({len(services)} listed)")

    # Flow 2: Crypto quote
    print("\n--- Flow 2: Crypto ---")
    r = c.get("/api/public/crypto/quote", params={"from_token": "SOL", "to_token": "USDC", "amount": 1})
    d = r.json()
    if d.get("output_amount") or d.get("estimated_output"):
        ok(f"Swap quote (SOL->USDC: {d.get('output_amount', d.get('estimated_output','?'))})")
    else:
        fail("Swap quote", str(d)[:100])

    r = c.get("/api/public/crypto/candles", params={"symbol": "SOL", "interval": "1m", "limit": 5})
    d = r.json()
    ok(f"Candles ({d.get('count',0)} candles)")

    # Flow 3: Stocks
    print("\n--- Flow 3: Stocks ---")
    r = c.get("/api/public/stocks")
    d = r.json()
    ok(f"Stocks ({d.get('total',0)} stocks)")

    r = c.get("/api/public/stocks/price/AAPL")
    d = r.json()
    ok(f"AAPL price (${d.get('price_usd',0):.2f})")

    # Flow 4: Templates → Deploy
    print("\n--- Flow 4: Templates ---")
    r = c.get("/api/public/templates")
    d = r.json()
    ok(f"Templates ({d.get('total',0)})")

    r = c.post("/api/public/templates/deploy", headers=headers, json={"template_id": "sentiment_bot"})
    d = r.json()
    if d.get("success"):
        ok(f"Deploy template ({d.get('name','')})")
    else:
        fail("Deploy template", str(d)[:100])

    # Flow 5: Messages
    print("\n--- Flow 5: Agent Chat ---")
    r = c.get("/api/public/messages/unread-count", headers=headers)
    d = r.json()
    ok(f"Unread count ({d.get('unread',0)})")

    # Flow 6: Leaderboard
    print("\n--- Flow 6: Leaderboard ---")
    r = c.get("/api/public/leaderboard")
    d = r.json()
    ok(f"Leaderboard ({d.get('total',0)} agents)")

    # Flow 7: Webhooks
    print("\n--- Flow 7: Webhooks ---")
    r = c.post("/api/public/webhooks/subscribe", headers=headers, json={"callback_url": "https://maxiaworld.app/health", "events": ["all"]})
    d = r.json()
    if d.get("success"):
        ok(f"Webhook subscribe ({d.get('subscription_id','')[:8]})")
    else:
        fail("Webhook subscribe", str(d)[:100])

    # Flow 8: MCP
    print("\n--- Flow 8: MCP ---")
    r = c.get("/mcp/manifest")
    d = r.json()
    ok(f"MCP manifest ({len(d.get('capabilities',[]))} caps)")

    r = c.post("/mcp/sse/call", json={"name": "maxia_prices", "arguments": {}})
    d = r.json()
    ok(f"MCP SSE call (isError: {d.get('isError')})")

    print("\n" + "=" * 50)
    print(f"Results: {PASSED} passed, {FAILED} failed")
    if ERRORS:
        print("Failures:")
        for e in ERRORS: print(f"  - {e}")
    sys.exit(1 if FAILED > 0 else 0)
