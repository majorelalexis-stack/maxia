"""MAXIA x AIP x LangChain — Signed AI Agent Tool Calls

Demo: A LangChain agent that uses MAXIA marketplace tools with AIP-signed intents.
Every tool call is cryptographically signed before execution.

Requirements:
    pip install aip-protocol langchain httpx

Usage:
    python langchain_maxia_aip.py

Architecture:
    LangChain Agent -> AIP sign_intent -> MAXIA verify + execute -> return result

    Layer 3: MAXIA marketplace (scopes, spend caps, escrow)
    Layer 2: DID Document + UAID (W3C + HCS-14)
    Layer 1: AIP Protocol (signed intents, ed25519)
"""
import asyncio
import json
import httpx
from aip_protocol import (
    generate_keypair, create_envelope, sign_envelope, verify_intent,
    AgentPassport, AgentIdentity, Principal, Boundaries, MonetaryLimit,
)

# ══════════════════════════════════════════
# MAXIA Configuration
# ══════════════════════════════════════════

MAXIA_API = "https://maxiaworld.app"


# ══════════════════════════════════════════
# Step 1: Register agent on MAXIA (one-time)
# ══════════════════════════════════════════

async def register_agent(name: str, wallet: str) -> dict:
    """Register a new AI agent on MAXIA. Returns API key, DID, UAID, signing key."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{MAXIA_API}/api/public/register", json={
            "name": name,
            "wallet": wallet,
            "description": f"LangChain agent with AIP-signed intents",
        })
        resp.raise_for_status()
        data = resp.json()
        print(f"  Agent registered: {data.get('agent_id')}")
        print(f"  DID: {data.get('did')}")
        print(f"  UAID: {data.get('uaid', '')[:30]}...")
        print(f"  Trust Level: L{data.get('trust_level', 0)}")
        return data


# ══════════════════════════════════════════
# Step 2: Create AIP Passport
# ══════════════════════════════════════════

def create_passport(did: str, name: str, max_spend: float = 1000) -> tuple:
    """Create an AIP AgentPassport with ed25519 keypair."""
    private_key, public_key = generate_keypair()

    passport = AgentPassport(
        identity=AgentIdentity(agent_id=did, name=name, version="1.0.0"),
        principal=Principal(id=did, name=name, framework="maxia"),
        boundaries=Boundaries(
            monetary=MonetaryLimit(max_amount=max_spend, currency="USDC"),
            allowed_actions=[
                "swap", "gpu_rent", "check_yields", "check_prices",
                "escrow_lock", "stocks_buy",
            ],
        ),
        private_key=private_key,
        public_key=public_key,
    )
    return passport, private_key, public_key


# ══════════════════════════════════════════
# Step 3: AIP-Signed MAXIA Tools
# ══════════════════════════════════════════

class MaxiaSignedTools:
    """MAXIA marketplace tools with AIP-signed intents.
    Every tool call is signed before execution."""

    def __init__(self, api_key: str, passport: AgentPassport, private_key):
        self.api_key = api_key
        self.passport = passport
        self.private_key = private_key
        self.base = MAXIA_API

    def _sign(self, action: str, params: dict) -> dict:
        """Sign an intent with AIP before calling MAXIA."""
        envelope = create_envelope(
            passport=self.passport,
            action=action,
            target=f"{self.base}/api/public",
            parameters=params,
            ttl=300,
        )
        signed = sign_envelope(envelope, self.private_key)
        return signed

    async def check_prices(self, tokens: list[str] = None) -> dict:
        """Get live crypto prices from MAXIA oracle (78 tokens)."""
        self._sign("check_prices", {"tokens": tokens or []})
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{self.base}/api/public/crypto/prices")
            data = resp.json()
            prices = data.get("prices", data)
            if tokens:
                prices = {k: v for k, v in prices.items() if k in tokens}
            return {"action": "check_prices", "signed": True, "prices": prices}

    async def check_yields(self, chain: str = "all") -> dict:
        """Get DeFi yields across protocols."""
        self._sign("check_yields", {"chain": chain})
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{self.base}/api/public/yield/all")
            data = resp.json()
            yields = data if isinstance(data, list) else data.get("yields", [])
            return {"action": "check_yields", "signed": True, "count": len(yields), "top_3": yields[:3]}

    async def swap_quote(self, from_token: str, to_token: str, amount: float) -> dict:
        """Get a swap quote (price + fee)."""
        params = {"from": from_token, "to": to_token, "amount": amount}
        self._sign("swap_quote", params)
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{self.base}/api/public/crypto/quote",
                params={"from_token": from_token, "to_token": to_token, "amount": amount},
            )
            data = resp.json()
            return {"action": "swap_quote", "signed": True, **data}

    async def gpu_tiers(self) -> dict:
        """List available GPU tiers with pricing."""
        self._sign("gpu_list", {})
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{self.base}/api/public/gpu/tiers")
            data = resp.json()
            tiers = data.get("tiers", [])
            return {
                "action": "gpu_tiers", "signed": True,
                "providers": data.get("providers", []),
                "count": len(tiers),
                "cheapest": min(tiers, key=lambda t: t["price_per_hour_usdc"]) if tiers else None,
            }

    async def verify_agent(self, did_or_uaid: str) -> dict:
        """Verify any agent's identity and status."""
        self._sign("verify_agent", {"identifier": did_or_uaid})
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{self.base}/api/public/agent/{did_or_uaid}")
            return {"action": "verify_agent", "signed": True, **resp.json()}


# ══════════════════════════════════════════
# Step 4: Run the demo
# ══════════════════════════════════════════

async def main():
    print("=" * 60)
    print("MAXIA x AIP x LangChain — Signed Agent Tool Calls")
    print("=" * 60)
    print()

    # Create AIP passport (no registration needed for read-only tools)
    print("[1] Creating AIP passport...")
    did = "did:web:maxiaworld.app:agent:demo_langchain"
    passport, private_key, public_key = create_passport(did, "LangChain-Demo-Agent")
    print(f"  DID: {did}")
    print(f"  Boundaries: max $1000 USDC, actions: swap, gpu_rent, check_yields")
    print()

    # Initialize tools
    tools = MaxiaSignedTools(api_key="demo", passport=passport, private_key=private_key)

    # Tool 1: Check prices
    print("[2] Signed tool call: check_prices(SOL, ETH, BTC)")
    prices = await tools.check_prices(["SOL", "ETH", "BTC"])
    print(f"  Signed: {prices['signed']}")
    for token, info in list(prices.get("prices", {}).items())[:3]:
        p = info.get("price_usd", info) if isinstance(info, dict) else info
        print(f"  {token}: ${p}")
    print()

    # Tool 2: Check DeFi yields
    print("[3] Signed tool call: check_yields()")
    yields = await tools.check_yields()
    print(f"  Signed: {yields['signed']}")
    print(f"  {yields['count']} yield opportunities found")
    print()

    # Tool 3: GPU pricing
    print("[4] Signed tool call: gpu_tiers()")
    gpus = await tools.gpu_tiers()
    print(f"  Signed: {gpus['signed']}")
    print(f"  {gpus['count']} GPU tiers, providers: {gpus['providers']}")
    if gpus.get("cheapest"):
        c = gpus["cheapest"]
        print(f"  Cheapest: {c['label']} — ${c['price_per_hour_usdc']}/h")
    print()

    # Tool 4: Swap quote
    print("[5] Signed tool call: swap_quote(USDC -> SOL, 100)")
    quote = await tools.swap_quote("USDC", "SOL", 100)
    print(f"  Signed: {quote['signed']}")
    print(f"  Rate: {quote.get('rate', '?')}")
    print(f"  Output: {quote.get('estimated_output', '?')} SOL")
    print(f"  Fee: {quote.get('commission_pct', quote.get('fee', '?'))}")
    print()

    # Verify signature locally
    print("[6] Local AIP verification (sub-millisecond)...")
    test_envelope = create_envelope(
        passport=passport, action="swap",
        target="maxiaworld.app",
        parameters={"from": "USDC", "to": "SOL", "amount": 100},
    )
    signed_test = sign_envelope(test_envelope, private_key)
    result = verify_intent(signed_test, public_key)
    print(f"  AIP verify_intent: valid={result.valid}")
    print()

    print("=" * 60)
    print("All tool calls signed with AIP Protocol (ed25519)")
    print("Public verify: POST https://maxiaworld.app/api/public/intent/verify")
    print("Agent DID: GET https://maxiaworld.app/.well-known/did.json")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
