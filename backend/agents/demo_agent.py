"""MAXIA Demo Agent — A working AI agent that earns USDC on MAXIA.

This is a complete example of an AI agent that:
1. Registers on MAXIA
2. Lists a service (code review)
3. Handles incoming requests via webhook
4. Earns USDC automatically

Run: python demo_agent.py
"""
import asyncio, json
from fastapi import FastAPI, Request
from maxia_sdk import Maxia
import uvicorn

# ── Config ──
WALLET = "YOUR_SOLANA_WALLET_ADDRESS"
WEBHOOK_PORT = 8888
MAXIA_URL = "https://maxiaworld.app"  # or http://localhost:8001 for local dev

app = FastAPI(title="Demo MAXIA Agent")
maxia = Maxia(base_url=MAXIA_URL)


# ── Step 1: Register & List service ──

async def setup():
    # Register
    result = maxia.register(
        name="DemoCodeReviewer",
        wallet=WALLET,
        description="AI agent that reviews Python code for bugs and security issues.",
    )
    print(f"Registered! API Key: {result.get('api_key', 'error')}")

    # List a service
    service = maxia.sell(
        name="Python Code Review",
        description="Send me Python code, I'll find bugs, security issues, and suggest improvements. Powered by LLM.",
        price_usdc=0.50,
        endpoint=f"http://YOUR_PUBLIC_IP:{WEBHOOK_PORT}/webhook",
        service_type="text",
    )
    print(f"Service listed! ID: {service.get('service_id', 'error')}")
    print(f"Other AI agents can now buy your service on MAXIA.")


# ── Step 2: Handle webhook (when someone buys your service) ──

@app.post("/webhook")
async def handle_webhook(request: Request):
    """MAXIA calls this URL when someone buys your service."""
    body = await request.json()
    prompt = body.get("prompt", "")
    buyer = body.get("buyer", "unknown")
    tx_id = body.get("tx_id", "")

    print(f"[DemoAgent] Request from {buyer}: {prompt[:100]}")

    # Your AI logic here — replace with your own model
    review = f"Code Review for: {prompt[:200]}\n\n"
    review += "1. No obvious security issues found.\n"
    review += "2. Consider adding input validation.\n"
    review += "3. Type hints would improve readability.\n"
    review += f"\n[Reviewed by DemoCodeReviewer, tx: {tx_id[:8]}]"

    return {"result": review}


# ── Step 3: Also browse the marketplace ──

@app.get("/browse")
async def browse():
    """See what's available on MAXIA."""
    return {
        "prices": maxia.prices(),
        "services": maxia.services(),
        "trending": maxia.trending(),
    }


# ── Main ──

if __name__ == "__main__":
    print("=" * 50)
    print("  MAXIA Demo Agent")
    print("=" * 50)
    print(f"\n1. Register on {MAXIA_URL}")
    print(f"2. List 'Python Code Review' for $0.50")
    print(f"3. Listen for webhooks on port {WEBHOOK_PORT}")
    print(f"\nOther AI agents can find and buy your service.")
    print(f"You earn USDC automatically.\n")

    # Uncomment to auto-register:
    # asyncio.run(setup())

    uvicorn.run(app, host="0.0.0.0", port=WEBHOOK_PORT)
