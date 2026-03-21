"""Support Agent — Repond aux tickets, devis, negociation.

#15: Tickets support
#21: Proposer des services payants
#22: Repondre aux demandes de devis
#23: Negocier les prix
"""

# Services MAXIA disponibles
MAXIA_SERVICES = {
    "gpu_rtx4090": {"name": "RTX 4090 GPU", "price": 0.69, "unit": "per hour", "description": "24GB VRAM, 0% markup"},
    "gpu_a100": {"name": "A100 80GB GPU", "price": 1.79, "unit": "per hour", "description": "80GB VRAM, ideal for training"},
    "gpu_h100": {"name": "H100 SXM5 GPU", "price": 2.69, "unit": "per hour", "description": "80GB VRAM, fastest"},
    "swap": {"name": "Token Swap", "price": 0.005, "unit": "per trade (0.5%)", "description": "15 tokens, 210 pairs, via Jupiter"},
    "stocks": {"name": "Tokenized Stocks", "price": 0.005, "unit": "per trade (0.5%)", "description": "10 stocks via Ondo/xStocks"},
    "audit": {"name": "Smart Contract Audit", "price": 9.99, "unit": "per audit", "description": "AI-powered security review"},
    "custom_agent": {"name": "Custom AI Agent", "price": 49.99, "unit": "per month", "description": "Your agent listed + promoted on MAXIA"},
}

# Discounts
VOLUME_DISCOUNTS = {
    10: 0.05,    # 5% off for 10+ transactions
    50: 0.10,    # 10% off for 50+
    100: 0.15,   # 15% off for 100+
    500: 0.20,   # 20% off for 500+
}


async def handle_support_message(message: str, user: str, call_llm_fn) -> str:
    """#15: Repond a un ticket support via LLM."""
    prompt = (
        f"User '{user}' sent a support message:\n\"{message[:300]}\"\n\n"
        f"You are MAXIA support. Answer helpfully in English.\n"
        f"Available services: GPU $0.69/h, Swap 0.5%, Stocks 0.5%, Audit $9.99. 4 chains.\n"
        f"API docs: maxiaworld.app/docs\n"
        f"If technical question: answer with code example.\n"
        f"If billing: explain pay-per-use, USDC, no subscription.\n"
        f"If bug: apologize, ask for details, suggest workaround.\n"
        f"Keep it short (<300 chars)."
    )
    return await call_llm_fn(prompt, max_tokens=150)


async def generate_quote(services: list, quantity: int, call_llm_fn) -> dict:
    """#22: Genere un devis automatique."""
    total = 0
    items = []
    for svc_id in services:
        svc = MAXIA_SERVICES.get(svc_id)
        if svc:
            subtotal = svc["price"] * quantity
            items.append({"service": svc["name"], "unit_price": svc["price"], "quantity": quantity, "subtotal": subtotal})
            total += subtotal

    # Volume discount
    discount_pct = 0
    for min_qty, pct in sorted(VOLUME_DISCOUNTS.items()):
        if quantity >= min_qty:
            discount_pct = pct

    discount = total * discount_pct
    final = total - discount

    return {
        "items": items,
        "subtotal": round(total, 2),
        "discount": f"{discount_pct:.0%}" if discount_pct else "0%",
        "discount_amount": round(discount, 2),
        "total_usdc": round(final, 2),
        "payment": "USDC on Solana, Base, Ethereum, or XRP",
        "validity": "7 days",
    }


async def negotiate_price(service_id: str, proposed_price: float, buyer_volume: int, call_llm_fn) -> dict:
    """#23: Negocie le prix avec un acheteur."""
    svc = MAXIA_SERVICES.get(service_id)
    if not svc:
        return {"accepted": False, "reason": "Unknown service"}

    base_price = svc["price"]
    min_price = base_price * 0.5  # Jamais en dessous de 50% du prix

    # Volume discount auto
    discount_pct = 0
    for min_qty, pct in sorted(VOLUME_DISCOUNTS.items()):
        if buyer_volume >= min_qty:
            discount_pct = pct

    our_best = base_price * (1 - discount_pct)

    if proposed_price >= our_best:
        return {
            "accepted": True,
            "final_price": round(proposed_price, 4),
            "discount": f"{discount_pct:.0%}",
            "message": f"Deal! ${proposed_price}/{svc['unit']} for {buyer_volume}+ volume.",
        }
    elif proposed_price >= min_price:
        counter = round((proposed_price + our_best) / 2, 4)
        return {
            "accepted": False,
            "counter_offer": counter,
            "message": f"I can do ${counter}/{svc['unit']} for {buyer_volume}+ volume. That's {(1 - counter/base_price):.0%} off.",
        }
    else:
        return {
            "accepted": False,
            "counter_offer": our_best,
            "message": f"Best I can do is ${our_best}/{svc['unit']} ({discount_pct:.0%} volume discount). We're already at cost on GPU.",
        }


def list_services() -> list:
    """#21: Liste des services proposables."""
    return [{"id": k, **v} for k, v in MAXIA_SERVICES.items()]
