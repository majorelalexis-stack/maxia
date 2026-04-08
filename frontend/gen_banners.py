"""Generate 4 Vercel featured images 1920x1080 for MAXIA."""

from PIL import Image, ImageDraw, ImageFont
import math
import os

W, H = 1920, 1080
OUT = os.path.dirname(__file__)

def get_fonts():
    base = "C:/Windows/Fonts"
    return {
        "bold_lg": ImageFont.truetype(f"{base}/arialbd.ttf", 72),
        "bold_md": ImageFont.truetype(f"{base}/arialbd.ttf", 48),
        "bold_sm": ImageFont.truetype(f"{base}/arialbd.ttf", 36),
        "reg_md": ImageFont.truetype(f"{base}/arial.ttf", 36),
        "reg_sm": ImageFont.truetype(f"{base}/arial.ttf", 28),
        "reg_xs": ImageFont.truetype(f"{base}/arial.ttf", 22),
        "mono": ImageFont.truetype(f"{base}/consola.ttf", 26),
        "title": ImageFont.truetype(f"{base}/arialbd.ttf", 96),
    }

FONTS = get_fonts()

CYAN = (0, 229, 255)
PURPLE = (124, 58, 237)
ROSE = (244, 63, 94)
BG_DARK = (8, 12, 24)
BG_CARD = (16, 22, 40)
TEXT_WHITE = (240, 240, 245)
TEXT_GRAY = (148, 163, 184)
TEXT_DIM = (100, 116, 139)
GREEN = (34, 197, 94)


def draw_bg(draw, accent_pos=(0.3, 0.4)):
    """Dark background with subtle gradient glow."""
    draw.rectangle([(0, 0), (W, H)], fill=BG_DARK)
    # Top accent bar
    draw.rectangle([(0, 0), (W, 4)], fill=CYAN)
    # Subtle glow circles
    for cx, cy, color, radius in [
        (int(W * accent_pos[0]), int(H * accent_pos[1]), CYAN, 400),
        (int(W * 0.7), int(H * 0.6), PURPLE, 350),
    ]:
        for r in range(radius, 0, -2):
            alpha = int(12 * (1 - r / radius))
            c = color + (alpha,)
            # Approximate with filled circles on overlay
            draw.ellipse([(cx - r, cy - r), (cx + r, cy + r)],
                         fill=(color[0] // 15, color[1] // 15, color[2] // 15))


def draw_card(draw, x, y, w, h, title, lines, icon_color=CYAN):
    """Draw a dark card with title and bullet points."""
    draw.rounded_rectangle([(x, y), (x + w, y + h)], radius=16,
                           fill=BG_CARD, outline=(40, 50, 70), width=1)
    # Color accent bar at top
    draw.rounded_rectangle([(x, y), (x + w, y + 4)], radius=2, fill=icon_color)
    # Title
    draw.text((x + 30, y + 25), title, font=FONTS["bold_sm"], fill=TEXT_WHITE)
    # Lines
    for i, line in enumerate(lines):
        draw.text((x + 30, y + 80 + i * 38), line, font=FONTS["reg_xs"], fill=TEXT_GRAY)


def draw_logo_small(draw, x, y, size=60):
    """Draw small MAXIA M logo."""
    draw.rounded_rectangle([(x, y), (x + size, y + size)], radius=12, fill=BG_CARD)
    font = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", int(size * 0.65))
    draw.text((x + size // 2, y + size // 2 + 4), "M", font=font, fill=CYAN, anchor="mm")


# ═══════════════════════════════════════════
# IMAGE 1: Hero - Main overview
# ═══════════════════════════════════════════
def banner_hero():
    img = Image.new("RGB", (W, H), BG_DARK)
    draw = ImageDraw.Draw(img)
    draw_bg(draw, (0.25, 0.45))
    draw.rectangle([(0, 0), (W, 4)], fill=CYAN)

    # Logo
    draw_logo_small(draw, 100, 80, 70)
    draw.text((190, 92), "MAXIA", font=FONTS["bold_md"], fill=TEXT_WHITE)

    # Hero text
    draw.text((100, 250), "AI-to-AI Marketplace", font=FONTS["title"], fill=TEXT_WHITE)
    draw.text((100, 370), "on 15 Blockchains", font=FONTS["title"], fill=CYAN)

    draw.text((100, 500), "Autonomous AI agents discover, buy, and sell services using USDC.",
              font=FONTS["reg_md"], fill=TEXT_GRAY)
    draw.text((100, 555), "On-chain escrow  |  107 tokens  |  5000+ swap pairs  |  GPU rental",
              font=FONTS["reg_sm"], fill=TEXT_DIM)

    # Stats row
    stats = [
        ("14", "Blockchains"),
        ("559", "API Routes"),
        ("46", "MCP Tools"),
        ("12", "Agent Tools"),
        ("6", "GPU Tiers"),
    ]
    sx = 100
    for val, label in stats:
        draw.text((sx, 680), val, font=FONTS["bold_lg"], fill=CYAN)
        bbox = draw.textbbox((sx, 680), val, font=FONTS["bold_lg"])
        draw.text((sx, 760), label, font=FONTS["reg_xs"], fill=TEXT_DIM)
        sx += 320

    # Chain badges at bottom
    chains = ["Solana", "Base", "Ethereum", "Polygon", "Arbitrum", "Avalanche", "BNB",
              "TON", "SUI", "TRON", "NEAR", "Aptos", "SEI", "XRP"]
    bx = 100
    for chain in chains:
        tw = len(chain) * 14 + 30
        draw.rounded_rectangle([(bx, 900), (bx + tw, 938)], radius=10,
                               fill=(20, 28, 50), outline=(40, 55, 80))
        draw.text((bx + 15, 907), chain, font=FONTS["reg_xs"], fill=TEXT_GRAY)
        bx += tw + 12

    # maxiaworld.app
    draw.text((W - 350, 1020), "maxiaworld.app", font=FONTS["reg_sm"], fill=TEXT_DIM)

    img.save(os.path.join(OUT, "vercel-banner-1-hero.png"), "PNG")
    print("  1/4 hero OK")


# ═══════════════════════════════════════════
# IMAGE 2: Tools showcase
# ═══════════════════════════════════════════
def banner_tools():
    img = Image.new("RGB", (W, H), BG_DARK)
    draw = ImageDraw.Draw(img)
    draw_bg(draw, (0.5, 0.3))
    draw.rectangle([(0, 0), (W, 4)], fill=PURPLE)

    draw_logo_small(draw, 100, 60, 60)
    draw.text((180, 68), "MAXIA", font=FONTS["bold_md"], fill=TEXT_WHITE)

    draw.text((100, 180), "12 Tools for Your AI Agents", font=FONTS["bold_lg"], fill=TEXT_WHITE)
    draw.text((100, 270), "Works with Vercel AI SDK, LlamaIndex, CrewAI, LangChain",
              font=FONTS["reg_sm"], fill=TEXT_DIM)

    # Tool cards grid 3x2
    tools = [
        ("Marketplace", ["discover_services — Find AI services", "execute_service — Buy & run", "sell_service — List & earn USDC"], CYAN),
        ("Crypto & Swap", ["get_crypto_prices — 107 tokens live", "swap_quote — 5000+ pairs (Jupiter)", "list_stocks — 25 US equities"], PURPLE),
        ("Infrastructure", ["get_gpu_tiers — 6 GPUs (Akash)", "get_defi_yields — 14 chains APY", "analyze_wallet — Solana profiling"], ROSE),
    ]

    for i, (title, lines, color) in enumerate(tools):
        x = 100 + i * 580
        draw_card(draw, x, 360, 540, 260, title, lines, color)

    # Code snippet
    draw.rounded_rectangle([(100, 680), (W - 100, 940)], radius=16, fill=(12, 16, 30), outline=(35, 45, 65))
    code_lines = [
        ('import { maxiaTools } from ', TEXT_GRAY),
        ("'@maxia/marketplace-skill'", GREEN),
    ]
    draw.text((140, 710), "// Vercel AI SDK", font=FONTS["mono"], fill=TEXT_DIM)
    draw.text((140, 750), "import { generateText } from 'ai'", font=FONTS["mono"], fill=TEXT_GRAY)
    draw.text((140, 790), "import { maxiaTools } from '@maxia/marketplace-skill'", font=FONTS["mono"], fill=GREEN)
    draw.text((140, 840), "const result = await generateText({", font=FONTS["mono"], fill=TEXT_GRAY)
    draw.text((140, 875), "  tools: maxiaTools(),  // 12 tools, free tier included", font=FONTS["mono"], fill=CYAN)
    draw.text((140, 910), "})", font=FONTS["mono"], fill=TEXT_GRAY)

    draw.text((W - 350, 1020), "maxiaworld.app", font=FONTS["reg_sm"], fill=TEXT_DIM)
    img.save(os.path.join(OUT, "vercel-banner-2-tools.png"), "PNG")
    print("  2/4 tools OK")


# ═══════════════════════════════════════════
# IMAGE 3: Pricing & Tiers
# ═══════════════════════════════════════════
def banner_pricing():
    img = Image.new("RGB", (W, H), BG_DARK)
    draw = ImageDraw.Draw(img)
    draw_bg(draw, (0.6, 0.35))
    draw.rectangle([(0, 0), (W, 4)], fill=GREEN)

    draw_logo_small(draw, 100, 60, 60)
    draw.text((180, 68), "MAXIA", font=FONTS["bold_md"], fill=TEXT_WHITE)

    draw.text((100, 180), "Simple Pricing, Pay in USDC", font=FONTS["bold_lg"], fill=TEXT_WHITE)
    draw.text((100, 270), "Free tier included  |  No credit card  |  100 req/day free",
              font=FONTS["reg_sm"], fill=TEXT_DIM)

    # Pricing tiers
    tiers = [
        ("BRONZE", "Free", "< $500 volume", "1.5%", "100 req/day", CYAN),
        ("GOLD", "$500+", "$500 - $5K volume", "0.5%", "1,000 req/day", (255, 215, 0)),
        ("WHALE", "$5K+", "> $5,000 volume", "0.1%", "10,000 req/day", PURPLE),
    ]

    for i, (name, price, volume, commission, rate, color) in enumerate(tiers):
        x = 100 + i * 580
        draw.rounded_rectangle([(x, 360), (x + 540, 620)], radius=16,
                               fill=BG_CARD, outline=(40, 50, 70))
        draw.rounded_rectangle([(x, 360), (x + 540, 364)], radius=2, fill=color)
        draw.text((x + 30, 385), name, font=FONTS["bold_md"], fill=color)
        draw.text((x + 30, 450), f"Commission: {commission}", font=FONTS["reg_md"], fill=TEXT_WHITE)
        draw.text((x + 30, 505), volume, font=FONTS["reg_sm"], fill=TEXT_GRAY)
        draw.text((x + 30, 550), rate, font=FONTS["reg_sm"], fill=TEXT_DIM)

    # GPU tiers
    draw.text((100, 680), "GPU Rental — 15% Cheaper Than AWS", font=FONTS["bold_sm"], fill=TEXT_WHITE)
    gpus = [
        ("RTX 4090", "$0.39/h"),
        ("A100 80GB", "$1.49/h"),
        ("H100 SXM5", "$2.99/h"),
        ("A6000", "$0.69/h"),
        ("4x A100", "$5.49/h"),
        ("RX 7900XT", "$0.35/h"),
    ]
    gx = 100
    for name, price in gpus:
        tw = 260
        draw.rounded_rectangle([(gx, 740), (gx + tw, 810)], radius=12,
                               fill=BG_CARD, outline=(40, 50, 70))
        draw.text((gx + 15, 755), name, font=FONTS["reg_xs"], fill=TEXT_WHITE)
        draw.text((gx + 15, 782), price, font=FONTS["reg_xs"], fill=GREEN)
        gx += tw + 16

    # Free tools list
    draw.text((100, 870), "10 of 12 tools are completely free — no API key required",
              font=FONTS["reg_sm"], fill=TEXT_GRAY)
    free = "discover  |  prices  |  swap_quote  |  stocks  |  gpu_tiers  |  defi_yields  |  sentiment  |  wallet  |  marketplace_stats"
    draw.text((100, 920), free, font=FONTS["reg_xs"], fill=TEXT_DIM)

    draw.text((W - 350, 1020), "maxiaworld.app", font=FONTS["reg_sm"], fill=TEXT_DIM)
    img.save(os.path.join(OUT, "vercel-banner-3-pricing.png"), "PNG")
    print("  3/4 pricing OK")


# ═══════════════════════════════════════════
# IMAGE 4: Multi-framework support
# ═══════════════════════════════════════════
def banner_frameworks():
    img = Image.new("RGB", (W, H), BG_DARK)
    draw = ImageDraw.Draw(img)
    draw_bg(draw, (0.4, 0.5))
    draw.rectangle([(0, 0), (W, 4)], fill=ROSE)

    draw_logo_small(draw, 100, 60, 60)
    draw.text((180, 68), "MAXIA", font=FONTS["bold_md"], fill=TEXT_WHITE)

    draw.text((100, 180), "Works With Every AI Framework", font=FONTS["bold_lg"], fill=TEXT_WHITE)
    draw.text((100, 270), "One marketplace, every agent framework",
              font=FONTS["reg_sm"], fill=TEXT_DIM)

    # Framework cards
    frameworks = [
        ("Vercel AI SDK", "npm install @maxia/marketplace-skill", "maxiaTools()", CYAN),
        ("LlamaIndex", "pip install llama-index-tools-maxia", "MaxiaToolSpec()", PURPLE),
        ("CrewAI", "pip install crewai-tools-maxia", "get_all_tools()", ROSE),
        ("MCP Protocol", "46 tools at /mcp/manifest", "Claude, Cursor, Windsurf", GREEN),
    ]

    for i, (name, install, usage, color) in enumerate(frameworks):
        x = 100 + i * 440
        draw.rounded_rectangle([(x, 360), (x + 410, 580)], radius=16,
                               fill=BG_CARD, outline=(40, 50, 70))
        draw.rounded_rectangle([(x, 360), (x + 410, 364)], radius=2, fill=color)
        draw.text((x + 25, 385), name, font=FONTS["bold_sm"], fill=color)
        draw.text((x + 25, 440), install, font=FONTS["reg_xs"], fill=TEXT_GRAY)
        draw.text((x + 25, 520), usage, font=FONTS["mono"], fill=TEXT_DIM)

    # Protocols
    draw.text((100, 640), "Protocol Support", font=FONTS["bold_sm"], fill=TEXT_WHITE)
    protos = [
        ("A2A", "Google Agent2Agent (Linux Foundation)"),
        ("AIP", "Signed intent envelopes (ed25519)"),
        ("x402", "Micropayments (Solana + Base)"),
        ("Escrow", "On-chain PDA (Solana) + Solidity (Base)"),
    ]
    for i, (name, desc) in enumerate(protos):
        y = 700 + i * 50
        draw.text((130, y), name, font=FONTS["bold_sm"], fill=CYAN)
        draw.text((300, y + 4), desc, font=FONTS["reg_xs"], fill=TEXT_GRAY)

    # Bottom
    draw.text((100, 960), "Free registration  |  USDC payments  |  On-chain verification  |  15 blockchains",
              font=FONTS["reg_sm"], fill=TEXT_DIM)
    draw.text((W - 350, 1020), "maxiaworld.app", font=FONTS["reg_sm"], fill=TEXT_DIM)
    img.save(os.path.join(OUT, "vercel-banner-4-frameworks.png"), "PNG")
    print("  4/4 frameworks OK")


if __name__ == "__main__":
    print("Generating 4 banners 1920x1080...")
    banner_hero()
    banner_tools()
    banner_pricing()
    banner_frameworks()
    print("Done! Files in frontend/")
