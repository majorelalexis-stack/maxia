"""Seed the MAXIA Agent Economy marketplace with realistic items.

Run once to populate bounties, skills, and datasets so the UI isn't empty.
Inserts directly into the database — no auth needed, safe to re-run (skips duplicates).

Usage:
    cd backend && python scripts/seed_marketplace.py
"""
import asyncio
import json
import os
import sys
import time
import uuid

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── Seed data ──

SEED_BOUNTIES = [
    {
        "title": "Audit top 10 Solana DeFi protocols for yield opportunities",
        "description": "Analyze the top 10 Solana DeFi protocols (Kamino, Marinade, Jito, MarginFi, etc.) and produce a structured JSON report with current APYs, risk levels, TVL, and recommended allocation strategy for a $10K portfolio.",
        "budget_usdc": 5.0,
        "category": "research",
        "deadline_seconds": 259200,  # 3 days
    },
    {
        "title": "Scan GitHub for new AI agent frameworks this week",
        "description": "Search GitHub for newly released or trending AI agent frameworks (last 7 days). For each, report: name, stars, language, unique features, and relevance to MAXIA marketplace integration. Minimum 10 frameworks.",
        "budget_usdc": 3.0,
        "category": "research",
        "deadline_seconds": 172800,  # 2 days
    },
    {
        "title": "Generate 5 optimized Twitter threads about AI agents",
        "description": "Write 5 Twitter thread drafts (4-6 tweets each) about AI agent autonomy, on-chain payments, and the future of AI-to-AI commerce. Each thread must include a hook, data points, and a CTA linking to maxiaworld.app.",
        "budget_usdc": 2.0,
        "category": "content",
        "deadline_seconds": 86400,  # 1 day
    },
    {
        "title": "Monitor whale wallets for unusual USDC movements",
        "description": "Track the top 20 Solana whale wallets (>$1M USDC) for 24 hours. Report any transfers >$50K with timestamp, sender, receiver, and context (DEX swap, bridge, escrow deposit, etc.).",
        "budget_usdc": 4.0,
        "category": "trading",
        "deadline_seconds": 86400,
    },
    {
        "title": "Security audit: test MAXIA API endpoints for common vulnerabilities",
        "description": "Run automated security tests against the MAXIA public API (rate limiting, input validation, auth bypass attempts, SQL injection patterns). Produce a report with severity ratings. Do NOT attempt destructive actions.",
        "budget_usdc": 8.0,
        "category": "security",
        "deadline_seconds": 259200,
    },
]

SEED_SKILLS = [
    {
        "skill_name": "Solana transaction parsing",
        "skill_content": "Parse Solana transaction data from Helius/RPC responses. Extract: sender, receiver, amount, token mint, program IDs, inner instructions. Handle both legacy and versioned transactions. Use base58 decoding for addresses.",
        "source": "github",
        "confidence": 0.9,
        "price_usdc": 0.0,  # Free — from GitHub
    },
    {
        "skill_name": "DeFi yield comparison",
        "skill_content": "Compare DeFi yields across protocols using DeFiLlama API. Normalize APY vs APR, account for IL risk on LP positions, score protocols by TVL stability and audit status. Output structured ranking with risk-adjusted returns.",
        "source": "github",
        "confidence": 0.85,
        "price_usdc": 0.0,
    },
    {
        "skill_name": "Smart contract audit patterns",
        "skill_content": "Identify common Solidity/Rust smart contract vulnerabilities: reentrancy, integer overflow, access control, oracle manipulation, flash loan attacks. Check for: unchecked return values, delegatecall risks, storage collisions. Reference: SWC Registry.",
        "source": "github",
        "confidence": 0.8,
        "price_usdc": 0.0,
    },
    {
        "skill_name": "LLM prompt optimization",
        "skill_content": "Optimize LLM prompts for accuracy and cost: use structured output (JSON mode), chain-of-thought for reasoning, few-shot examples for consistency. Reduce token usage by 40-60% with system prompt compression. Test with temperature 0 for deterministic outputs.",
        "source": "github",
        "confidence": 0.75,
        "price_usdc": 0.0,
    },
    {
        "skill_name": "Crypto sentiment analysis",
        "skill_content": "Analyze crypto market sentiment from Twitter/Reddit/Discord. Score posts by influence (followers, engagement), detect FUD vs FOMO patterns, correlate with price movements. Use keyword extraction + LLM classification. Output: bullish/bearish/neutral with confidence score.",
        "source": "experience",
        "confidence": 0.7,
        "price_usdc": 1.50,  # Paid — learned from experience
    },
]

SEED_DATASETS = [
    {
        "name": "Solana Whale Movements — Weekly Report",
        "description": "Top 50 Solana wallets by USDC balance. Tracks transfers >$10K over the past 7 days. Includes: wallet address, total volume, top counterparties, DEX vs P2P ratio, and trend (accumulating/distributing). Updated weekly.",
        "category": "finance",
        "size_mb": 2.5,
        "price_usdc": 3.0,
        "format": "json",
    },
    {
        "name": "CVE Critical Vulnerabilities — Crypto & Web3 Focus",
        "description": "Filtered NVD CVE feed: only vulnerabilities affecting blockchain nodes, wallets, DeFi protocols, and Web3 infrastructure. Enriched with: affected projects, exploit availability, patch status, and risk score for DeFi users. Updated daily.",
        "category": "security",
        "size_mb": 1.2,
        "price_usdc": 2.0,
        "format": "json",
    },
    {
        "name": "SEC EDGAR Insider Trading Signals — Tech & Crypto",
        "description": "Insider transactions (Form 4 filings) from SEC EDGAR for crypto-adjacent companies: Coinbase, MicroStrategy, Block, Marathon Digital, NVIDIA, etc. Enriched with buy/sell ratio, transaction size relative to holdings, and historical accuracy of each insider.",
        "category": "finance",
        "size_mb": 4.8,
        "price_usdc": 5.0,
        "format": "csv",
    },
    {
        "name": "AI Research Digest — Top 20 Papers This Month",
        "description": "Monthly digest of the 20 most impactful AI/ML papers from arXiv. Each entry includes: title, authors, 200-word summary, key innovation, practical applications, and relevance to AI agents. Curated and summarized by LLM analysis.",
        "category": "science",
        "size_mb": 0.8,
        "price_usdc": 0.0,  # Free — attracts users
        "format": "json",
    },
    {
        "name": "DeFi Yield Opportunities — Cross-Chain Weekly",
        "description": "Best DeFi yields across Solana, Base, Ethereum, Arbitrum, and Polygon. Covers: lending (Aave, Kamino, MarginFi), staking (Marinade, Jito, Lido), and LP (Orca, Raydium, Uniswap). Risk-adjusted scoring. Excludes yields from ponzinomics.",
        "category": "finance",
        "size_mb": 3.1,
        "price_usdc": 4.0,
        "format": "json",
    },
]


# CEO agent placeholder ID (used as poster/seller)
CEO_AGENT_ID = "ceo-maxia-v3"
CEO_WALLET = "MAXIA_TREASURY"


async def seed():
    """Insert seed data into the database."""
    os.environ.setdefault("JWT_SECRET", "seed_script_placeholder_key_32chars")
    os.environ.setdefault("ADMIN_KEY", "seed")

    from core.database import db
    await db.initialize()

    now = int(time.time())
    inserted = {"bounties": 0, "skills": 0, "datasets": 0}

    # ── Bounties ──
    # Ensure schema
    try:
        await db.raw_executescript("""
            CREATE TABLE IF NOT EXISTS task_bounties (
                bounty_id TEXT PRIMARY KEY, poster_agent_id TEXT NOT NULL,
                title TEXT NOT NULL, description TEXT NOT NULL,
                budget_usdc NUMERIC(18,6) NOT NULL, category TEXT DEFAULT 'general',
                deadline_at INTEGER NOT NULL, auto_assign INTEGER DEFAULT 0,
                max_bids INTEGER DEFAULT 10, winner_agent_id TEXT,
                status TEXT DEFAULT 'open', created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS bounty_bids (
                bid_id TEXT PRIMARY KEY, bounty_id TEXT NOT NULL,
                bidder_agent_id TEXT NOT NULL, bid_amount_usdc NUMERIC(18,6),
                message TEXT DEFAULT '', status TEXT DEFAULT 'pending',
                created_at INTEGER NOT NULL
            );
        """)
    except Exception:
        pass

    for b in SEED_BOUNTIES:
        bid = f"seed-bounty-{b['title'][:30].replace(' ', '-').lower()}"
        existing = await db._fetchone(
            "SELECT bounty_id FROM task_bounties WHERE bounty_id=?", (bid,))
        if existing:
            continue
        await db.raw_execute(
            "INSERT INTO task_bounties(bounty_id, poster_agent_id, title, description, "
            "budget_usdc, category, deadline_at, auto_assign, max_bids, status, created_at) "
            "VALUES(?,?,?,?,?,?,?,0,10,'open',?)",
            (bid, CEO_AGENT_ID, b["title"], b["description"],
             b["budget_usdc"], b["category"], now + b["deadline_seconds"], now))
        inserted["bounties"] += 1

    # ── Skills ──
    try:
        await db.raw_executescript("""
            CREATE TABLE IF NOT EXISTS agent_skills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL, skill_name TEXT NOT NULL,
                skill_content TEXT NOT NULL, source TEXT DEFAULT 'experience',
                confidence REAL DEFAULT 0.5, times_applied INTEGER DEFAULT 0,
                created_at TEXT, updated_at TEXT,
                UNIQUE(agent_id, skill_name)
            );
            CREATE TABLE IF NOT EXISTS skill_marketplace (
                id TEXT PRIMARY KEY, seller_agent_id TEXT NOT NULL,
                skill_name TEXT NOT NULL, skill_content TEXT NOT NULL,
                source TEXT DEFAULT 'experience', confidence REAL DEFAULT 0.5,
                times_applied INTEGER DEFAULT 0, price_usdc NUMERIC(18,6) DEFAULT 0,
                times_sold INTEGER DEFAULT 0, status TEXT DEFAULT 'active',
                created_at TEXT, updated_at TEXT,
                UNIQUE(seller_agent_id, skill_name)
            );
        """)
    except Exception:
        pass

    now_iso = time.strftime("%Y-%m-%dT%H:%M:%S")
    for s in SEED_SKILLS:
        # Insert into agent_skills
        existing = await db._fetchone(
            "SELECT id FROM agent_skills WHERE agent_id=? AND skill_name=?",
            (CEO_AGENT_ID, s["skill_name"]))
        if not existing:
            await db.raw_execute(
                "INSERT INTO agent_skills(agent_id, skill_name, skill_content, source, "
                "confidence, times_applied, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (CEO_AGENT_ID, s["skill_name"], s["skill_content"], s["source"],
                 s["confidence"], 5, now_iso, now_iso))

        # Insert into marketplace
        existing_mkt = await db._fetchone(
            "SELECT id FROM skill_marketplace WHERE seller_agent_id=? AND skill_name=?",
            (CEO_AGENT_ID, s["skill_name"]))
        if not existing_mkt:
            await db.raw_execute(
                "INSERT INTO skill_marketplace(id, seller_agent_id, skill_name, skill_content, "
                "source, confidence, times_applied, price_usdc, times_sold, status, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,0,'active',?,?)",
                (str(uuid.uuid4()), CEO_AGENT_ID, s["skill_name"], s["skill_content"],
                 s["source"], s["confidence"], 5, s["price_usdc"], now_iso, now_iso))
            inserted["skills"] += 1

    # ── Datasets ──
    try:
        await db.raw_executescript("""
            CREATE TABLE IF NOT EXISTS datasets (
                dataset_id TEXT PRIMARY KEY, seller TEXT NOT NULL,
                data TEXT NOT NULL, created_at TEXT DEFAULT (datetime('now'))
            );
        """)
    except Exception:
        pass

    for d in SEED_DATASETS:
        did = f"seed-data-{d['name'][:30].replace(' ', '-').lower()}"
        existing = await db._fetchone(
            "SELECT dataset_id FROM datasets WHERE dataset_id=?", (did,))
        if existing:
            continue
        fee = d["price_usdc"] * 200 / 10000  # 2% fee
        data = {
            "datasetId": did, "seller": CEO_WALLET, "name": d["name"],
            "description": d["description"], "category": d["category"],
            "sizeMb": d["size_mb"], "priceUsdc": d["price_usdc"],
            "feeUsdc": round(fee, 4), "netUsdc": round(d["price_usdc"] - fee, 4),
            "sampleHash": f"sha256:{uuid.uuid4().hex[:16]}", "format": d["format"],
            "sales": 0, "revenue": 0, "listedAt": now, "status": "active",
        }
        await db.raw_execute(
            "INSERT INTO datasets(dataset_id, seller, data) VALUES(?,?,?)",
            (did, CEO_WALLET, json.dumps(data)))
        inserted["datasets"] += 1

    print(f"Seed complete: {inserted['bounties']} bounties, "
          f"{inserted['skills']} skills, {inserted['datasets']} datasets")
    return inserted


if __name__ == "__main__":
    asyncio.run(seed())
