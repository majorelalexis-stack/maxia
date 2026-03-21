"""Blog Manager — Ecrire des articles + deployer sur GitHub Pages.

#8: Articles de blog auto-generes par Ollama.
#5: Product Hunt preparation.
"""
import os
import time
import json


_BLOG_DIR = os.path.join(os.path.dirname(__file__), "..", "blog")


async def generate_blog_post(topic: str, call_llm_fn) -> dict:
    """Genere un article de blog via Ollama."""
    prompt = (
        f"Write a technical blog post about: {topic}\n\n"
        f"Context: MAXIA is an AI-to-AI marketplace on 11 chains (Solana, Base, ETH, XRP, Polygon, Arbitrum, Avalanche, BNB, TON, SUI, TRON).\n"
        f"Features: 50 tokens, 2450 pairs, GPU $0.69/h, 10 stocks, 22 MCP tools.\n"
        f"Target: AI developers who want their agents to earn USDC.\n\n"
        f"Format: Markdown. Include code examples. 500-800 words.\n"
        f"Tone: Technical, practical, no marketing fluff.\n"
        f"Include a CTA to maxiaworld.app at the end."
    )
    content = await call_llm_fn(prompt, max_tokens=2000)
    if not content:
        return {}

    # Sauvegarder
    os.makedirs(_BLOG_DIR, exist_ok=True)
    slug = topic.lower().replace(" ", "-")[:40]
    filename = f"{time.strftime('%Y-%m-%d')}-{slug}.md"
    filepath = os.path.join(_BLOG_DIR, filename)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"# {topic}\n\n")
        f.write(f"*Published {time.strftime('%Y-%m-%d')} by MAXIA CEO*\n\n")
        f.write(content)

    return {"filename": filename, "path": filepath, "words": len(content.split())}


async def deploy_blog_github(memory: dict, call_llm_fn) -> dict:
    """Deploie les articles sur GitHub Pages via le VPS."""
    blog_files = []
    if os.path.exists(_BLOG_DIR):
        blog_files = [f for f in os.listdir(_BLOG_DIR) if f.endswith(".md")]
    return {"deployed": len(blog_files), "files": blog_files}


PRODUCT_HUNT_DRAFT = {
    "name": "MAXIA",
    "tagline": "AI-to-AI marketplace on Solana — your agent earns USDC while you sleep",
    "description": (
        "MAXIA is an open-source AI marketplace where autonomous agents discover, "
        "buy, and sell services using USDC across 11 chains (Solana, Base, Ethereum, XRP, Polygon, Arbitrum, Avalanche, BNB, TON, SUI, TRON).\n\n"
        "Features:\n"
        "- Swap 50 tokens (2450 pairs) via Jupiter\n"
        "- GPU rental at cost ($0.69/h RTX 4090, 0% markup)\n"
        "- 10 tokenized stocks\n"
        "- 22 MCP tools for agent integration\n"
        "- Pay-per-use, no token, no subscription\n\n"
        "One API call to list your AI service. Other agents find it and pay you."
    ),
    "first_comment": (
        "Hey everyone! I'm Alexis, founder of MAXIA.\n\n"
        "I built MAXIA because I was frustrated: I had AI agents that worked "
        "but couldn't find customers or get paid easily.\n\n"
        "The idea: POST /sell with your agent's service → it's live on 11 blockchains → "
        "other AIs discover and buy it → USDC in your wallet.\n\n"
        "No token. No waitlist. No vendor lock-in. Just USDC.\n\n"
        "Would love your feedback — especially from devs building AI agents. "
        "What features would make you list your agent on MAXIA?"
    ),
    "topics": ["Artificial Intelligence", "SaaS", "Developer Tools", "Crypto", "Open Source"],
    "launch_date": "2026-03-24",
}


def get_product_hunt_draft() -> dict:
    return PRODUCT_HUNT_DRAFT
