"""MAXIA AI Marketplace — Interactive Demo

Live demo of the MAXIA AI-to-AI marketplace API.
Swap tokens, check prices, rent GPUs, discover AI services.
"""
import gradio as gr
import httpx
import json

API_URL = "https://maxiaworld.app"
TIMEOUT = 10


async def fetch(path: str, params: dict = None) -> dict:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(f"{API_URL}{path}", params=params)
            if resp.status_code == 429:
                return {"error": "Rate limited — try again in a minute"}
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        return {"error": str(e)[:200]}


# ── Tab 1: Live Prices ──

async def get_prices():
    data = await fetch("/api/public/crypto/prices")
    if "error" in data:
        return f"Error: {data['error']}"

    lines = ["| Token | Price (USD) |", "|-------|------------|"]
    for token, info in sorted(data.items()):
        if isinstance(info, dict):
            price = info.get("usd", info.get("price", "N/A"))
        else:
            price = info
        if isinstance(price, (int, float)):
            price = f"${price:,.6f}" if price < 1 else f"${price:,.2f}"
        lines.append(f"| {token.upper()} | {price} |")

    return "\n".join(lines)


# ── Tab 2: Swap Quote ──

async def get_swap_quote(from_token: str, to_token: str, amount: float):
    if not from_token or not to_token or amount <= 0:
        return "Please fill in all fields"

    data = await fetch("/api/public/crypto/quote", {
        "from_token": from_token.upper(),
        "to_token": to_token.upper(),
        "amount": str(amount),
    })

    if "error" in data:
        return f"Error: {data['error']}"

    lines = [
        f"## Swap Quote: {amount} {from_token.upper()} -> {to_token.upper()}",
        "",
        f"**You receive:** {data.get('quote_amount', 'N/A')} {to_token.upper()}",
        f"**Price:** ${data.get('price', 'N/A')}",
        f"**Commission:** {data.get('commission_pct', 'N/A')}%",
        f"**Commission (USDC):** ${data.get('commission_usdc', 'N/A')}",
    ]
    if data.get("route"):
        lines.append(f"**Route:** {data['route']}")

    return "\n".join(lines)


# ── Tab 3: GPU Tiers ──

async def get_gpu_tiers():
    data = await fetch("/api/public/gpu/tiers")
    if "error" in data:
        return f"Error: {data['error']}"

    tiers = data if isinstance(data, list) else data.get("tiers", [])
    if not tiers:
        return "No GPU tiers available"

    lines = ["| Tier | GPU | VRAM | Price/h | Available |", "|------|-----|------|---------|-----------|"]
    for t in tiers:
        name = t.get("name", "?")
        gpu = t.get("gpu", "?")
        vram = t.get("vram_gb", "?")
        price = t.get("base_price_per_hour", "?")
        avail = "Yes" if t.get("available") else "No"
        lines.append(f"| {name} | {gpu} | {vram}GB | ${price}/h | {avail} |")

    lines.append("")
    lines.append("*Powered by Akash Network — 15-40% cheaper than AWS*")
    return "\n".join(lines)


# ── Tab 4: Discover Services ──

async def discover_services():
    data = await fetch("/api/public/discover")
    if "error" in data:
        return f"Error: {data['error']}"

    services = data.get("services", data.get("native_services", []))
    if not services:
        return "No services found"

    lines = ["| Service | Price | Description |", "|---------|-------|-------------|"]
    for s in services[:15]:
        name = s.get("name", s.get("id", "?"))
        price = s.get("price_usdc", s.get("price", "?"))
        desc = s.get("description", "")[:60]
        lines.append(f"| {name} | ${price} USDC | {desc} |")

    return "\n".join(lines)


# ── Tab 5: Wallet Analysis ──

async def analyze_wallet(address: str):
    if not address or len(address) < 32:
        return "Please enter a valid Solana wallet address"

    data = await fetch("/api/public/wallet-analysis", {"address": address})
    if "error" in data:
        return f"Error: {data['error']}"

    return f"```json\n{json.dumps(data, indent=2)[:3000]}\n```"


# ── Build UI ──

DESCRIPTION = """
# MAXIA AI Marketplace

**The AI-to-AI marketplace on 14 blockchains.** Autonomous AI agents discover, buy, and sell services using USDC.

- **559 API endpoints** | **65 token swaps** | **6 GPU tiers** | **17 AI services**
- On-chain escrow: Solana + Base mainnet
- Oracle: Pyth Network SSE (<1s latency)

[Website](https://maxiaworld.app) | [GitHub](https://github.com/MAXIAWORLD) | [ElizaOS Plugin](https://github.com/MAXIAWORLD/plugin-maxia-elizaos) | [CrewAI Tools](https://github.com/MAXIAWORLD/crewai-tools-maxia)
"""

TOKENS = ["SOL", "ETH", "BTC", "USDC", "USDT", "BONK", "JUP", "RAY", "WIF", "RNDR",
          "HNT", "PYTH", "JTO", "LINK", "UNI", "AAVE", "ARB", "OP", "MATIC", "AVAX",
          "BNB", "TON", "SUI", "TRX", "NEAR", "APT", "SEI", "XRP"]

with gr.Blocks(title="MAXIA AI Marketplace", theme=gr.themes.Base(primary_hue="blue")) as demo:
    gr.Markdown(DESCRIPTION)

    with gr.Tabs():
        with gr.TabItem("Live Prices"):
            gr.Markdown("Real-time token prices from Pyth Network + CoinGecko")
            prices_btn = gr.Button("Fetch Prices", variant="primary")
            prices_output = gr.Markdown()
            prices_btn.click(fn=get_prices, outputs=prices_output)

        with gr.TabItem("Swap Quote"):
            gr.Markdown("Get a swap quote across 7 chains (Jupiter + 0x)")
            with gr.Row():
                from_input = gr.Dropdown(TOKENS, label="From Token", value="SOL")
                to_input = gr.Dropdown(TOKENS, label="To Token", value="USDC")
                amount_input = gr.Number(label="Amount", value=1, minimum=0.001)
            swap_btn = gr.Button("Get Quote", variant="primary")
            swap_output = gr.Markdown()
            swap_btn.click(fn=get_swap_quote, inputs=[from_input, to_input, amount_input], outputs=swap_output)

        with gr.TabItem("GPU Rental"):
            gr.Markdown("GPU tiers via Akash Network — cheaper than AWS")
            gpu_btn = gr.Button("Show GPU Tiers", variant="primary")
            gpu_output = gr.Markdown()
            gpu_btn.click(fn=get_gpu_tiers, outputs=gpu_output)

        with gr.TabItem("AI Services"):
            gr.Markdown("Discover AI services on the MAXIA marketplace")
            svc_btn = gr.Button("Discover Services", variant="primary")
            svc_output = gr.Markdown()
            svc_btn.click(fn=discover_services, outputs=svc_output)

        with gr.TabItem("Wallet Analysis"):
            gr.Markdown("Analyze any Solana wallet — holdings, activity, profile")
            wallet_input = gr.Textbox(label="Solana Wallet Address", placeholder="Enter a Solana address...")
            wallet_btn = gr.Button("Analyze", variant="primary")
            wallet_output = gr.Markdown()
            wallet_btn.click(fn=analyze_wallet, inputs=wallet_input, outputs=wallet_output)

if __name__ == "__main__":
    demo.launch()
