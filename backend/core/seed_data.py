"""MAXIA Seed Data — native services and initial datasets."""

from core.config import SERVICE_PRICES

# ══════════════════════════════════════════════════════════
#  Native AI Services (registered at startup)
# ══════════════════════════════════════════════════════════

NATIVE_SERVICES = [
    {
        "id": "maxia-audit",
        "name": "Smart Contract Audit",
        "description": "AI-powered security audit of Solana/EVM smart contracts. Detects vulnerabilities, reentrancy, overflow, access control issues.",
        "type": "audit",
        "price_usdc": SERVICE_PRICES.get("maxia-audit", 4.99),
    },
    {
        "id": "maxia-code",
        "name": "AI Code Review",
        "description": "Automated code review for Python, Rust, JavaScript, Solidity. Finds bugs, suggests improvements, checks best practices.",
        "type": "code",
        "price_usdc": SERVICE_PRICES.get("maxia-code-review", 2.99),
    },
    {
        "id": "maxia-translate",
        "name": "AI Translation",
        "description": "Translate text between 50+ languages. Technical documentation, marketing copy, chat messages.",
        "type": "text",
        "price_usdc": SERVICE_PRICES.get("maxia-translate", 0.05),
    },
    {
        "id": "maxia-summary",
        "name": "Document Summary",
        "description": "Summarize any document, whitepaper, or article into key bullet points. Supports up to 10,000 words.",
        "type": "text",
        "price_usdc": SERVICE_PRICES.get("maxia-summary", 0.49),
    },
    {
        "id": "maxia-wallet",
        "name": "Wallet Analyzer",
        "description": "Deep analysis of any Solana wallet: token holdings, transaction history, DeFi positions, risk score.",
        "type": "data",
        "price_usdc": SERVICE_PRICES.get("maxia-wallet-analysis", 1.99),
    },
    {
        "id": "maxia-marketing",
        "name": "Marketing Copy Generator",
        "description": "Generate landing page copy, Twitter threads, blog posts, product descriptions. Optimized for Web3/AI audience.",
        "type": "text",
        "price_usdc": SERVICE_PRICES.get("maxia-marketing", 0.99),
    },
    {
        "id": "maxia-image",
        "name": "AI Image Generator",
        "description": "Generate images from text prompts. Logos, illustrations, social media graphics. 1024x1024 resolution.",
        "type": "image",
        "price_usdc": SERVICE_PRICES.get("maxia-image", 0.10),
    },
    {
        "id": "maxia-scraper",
        "name": "Web Scraper",
        "description": "Extract structured data from any website. Returns clean JSON with the data you need.",
        "type": "data",
        "price_usdc": SERVICE_PRICES.get("maxia-scraper", 0.02),
    },
    {
        "id": "maxia-finetune",
        "name": "LLM Fine-Tuning (Unsloth)",
        "description": "Fine-tune any LLM (Llama, Qwen, Mistral, Gemma, DeepSeek, Phi) on your dataset. Powered by Unsloth on Akash GPUs. 2x faster, 70% less VRAM.",
        "type": "compute",
        "price_usdc": SERVICE_PRICES.get("maxia-finetune", 2.99),
    },
    {
        "id": "maxia-awp-stake",
        "name": "AWP Agent Staking",
        "description": "Stake USDC on the Autonomous Worker Protocol (Base L2) to earn rewards and increase your agent's trust score. 3-12% APY.",
        "type": "defi",
        "price_usdc": SERVICE_PRICES.get("maxia-awp-staking", 0.00),
    },
    # ═══ Machine-only AI services (visible via API/MCP only, not on /app) ═══
    {
        "id": "maxia-transcription",
        "name": "Audio Transcription (Whisper)",
        "description": "Transcribe audio to text. Supports MP3, WAV, M4A. 50+ languages. Powered by local GPU.",
        "type": "ai",
        "price_usdc": SERVICE_PRICES.get("maxia-transcription", 0.01),
        "machine_only": True,
    },
    {
        "id": "maxia-embedding",
        "name": "Text Embedding",
        "description": "Convert text to vector embeddings for RAG, semantic search, clustering. 768-dim vectors.",
        "type": "ai",
        "price_usdc": SERVICE_PRICES.get("maxia-embedding", 0.001),
        "machine_only": True,
    },
    {
        "id": "maxia-sentiment",
        "name": "Sentiment Analysis",
        "description": "Analyze sentiment of text, tweets, or crypto discussions. Returns score (-1 to 1) + confidence.",
        "type": "ai",
        "price_usdc": SERVICE_PRICES.get("maxia-sentiment", 0.005),
        "machine_only": True,
    },
    {
        "id": "maxia-wallet-score",
        "name": "Wallet Risk Score",
        "description": "Score any wallet across 14 chains: activity, age, balance, DeFi exposure, risk level (0-100).",
        "type": "data",
        "price_usdc": SERVICE_PRICES.get("maxia-wallet-risk", 0.10),
        "machine_only": True,
    },
    {
        "id": "maxia-airdrop-scan",
        "name": "Airdrop Eligibility Scanner",
        "description": "Scan a wallet for potential airdrop eligibility across 50+ protocols on Solana, ETH, Base, Arbitrum.",
        "type": "data",
        "price_usdc": SERVICE_PRICES.get("maxia-airdrop-scanner", 0.50),
        "machine_only": True,
    },
    {
        "id": "maxia-smart-money",
        "name": "Smart Money Tracker",
        "description": "Track whale wallets and smart money movements on Solana and EVM chains. Real-time alerts.",
        "type": "data",
        "price_usdc": SERVICE_PRICES.get("maxia-smart-money", 0.25),
        "machine_only": True,
    },
    {
        "id": "maxia-nft-rarity",
        "name": "NFT Rarity Checker",
        "description": "Calculate rarity score for any Solana or EVM NFT based on trait distribution.",
        "type": "data",
        "price_usdc": SERVICE_PRICES.get("maxia-nft-rarity", 0.05),
        "machine_only": True,
    },
]

# ══════════════════════════════════════════════════════════
#  Initial Services (admin seed endpoint)
# ══════════════════════════════════════════════════════════

INITIAL_SERVICES = [
    # ── Services a la carte ──
    {
        "name": "MAXIA AI Security Scan",
        "description": "AI-powered smart contract vulnerability scanner. Detects reentrancy, overflow, access control, logic flaws. Supports Solidity, Rust (Anchor), Move. Structured report: [CRITICAL][MAJOR][MINOR][INFO]. Results in seconds, not weeks.",
        "type": "audit",
        "priceUsdc": 4.99,
    },
    {
        "name": "MAXIA Crypto Data Analyst",
        "description": "Real-time DeFi and crypto market analysis. On-chain metrics, whale tracking, liquidity pools, price predictions, token scoring. Supports Solana, Ethereum, Base. Pay per query — no monthly subscription needed.",
        "type": "data",
        "priceUsdc": 1.99,
    },
    {
        "name": "MAXIA Code Engineer",
        "description": "Professional AI code generation and review. Python, Rust, JavaScript, TypeScript, Solidity. Production-ready, commented, optimized code. Bug fixing, refactoring, architecture design. Pay per task.",
        "type": "code",
        "priceUsdc": 1.99,
    },
    {
        "name": "MAXIA Universal Translator",
        "description": "AI translation in 50+ languages. Professional quality, context-aware. Documents, websites, apps, smart contract docs. EN, FR, ES, DE, PT, ZH, JA, KO, RU, AR and more.",
        "type": "text",
        "priceUsdc": 0.09,
    },
    # ── Forfaits (Packs) ──
    {
        "name": "MAXIA Starter Pack — 10 requests",
        "description": "10 requests to use on any MAXIA service (Security Scan, Data Analysis, Code, Translation). Valid forever. Best value for occasional users. Save 20% vs pay-per-use.",
        "type": "pack",
        "priceUsdc": 9.99,
    },
    {
        "name": "MAXIA Pro Pack — 50 requests",
        "description": "50 requests to use on any MAXIA service. Ideal for developers and traders who need regular AI assistance. Save 35% vs pay-per-use. Priority processing.",
        "type": "pack",
        "priceUsdc": 39.99,
    },
    {
        "name": "MAXIA Unlimited Monthly",
        "description": "Unlimited access to ALL MAXIA services for 30 days. Security scans, data analysis, code generation, translation. No limits. Best for teams and power users. Includes priority support.",
        "type": "subscription",
        "priceUsdc": 79.99,
    },
    {
        "name": "MAXIA Deep Security Audit",
        "description": "Comprehensive AI security audit with multi-pass analysis. Covers reentrancy, flash loan exploits, oracle manipulation, access control, economic attacks. Detailed PDF report with severity ratings and fix recommendations. For serious DeFi projects.",
        "type": "audit_deep",
        "priceUsdc": 49.99,
    },
]

# ══════════════════════════════════════════════════════════
#  Initial Datasets (admin seed endpoint)
# ══════════════════════════════════════════════════════════

INITIAL_DATASETS = [
    {
        "name": "Solana DeFi Transactions 2025",
        "description": "Complete dataset of DeFi swap transactions on Solana DEXs (Raydium, Orca, Jupiter) from 2025. 50M+ rows. CSV format. Token pairs, volumes, prices, timestamps.",
        "category": "market_data",
        "size_mb": 2400,
        "price_usdc": 19.99,
        "sample_hash": "a1b2c3d4e5f6",
        "format": "csv",
    },
    {
        "name": "Top 1000 Token Prices Historical",
        "description": "Hourly OHLCV data for the top 1000 cryptocurrencies. 3 years of history (2023-2025). Perfect for backtesting trading strategies. JSON format.",
        "category": "market_data",
        "size_mb": 800,
        "price_usdc": 9.99,
        "sample_hash": "f1e2d3c4b5a6",
        "format": "json",
    },
    {
        "name": "Smart Contract Vulnerability Database",
        "description": "Curated database of 10,000+ known smart contract vulnerabilities. Solidity and Rust. Classified by severity, type, and exploit method. Updated monthly.",
        "category": "security",
        "size_mb": 150,
        "price_usdc": 29.99,
        "sample_hash": "sec123vuln456",
        "format": "json",
    },
    {
        "name": "NFT Collection Metadata (Solana)",
        "description": "Metadata for 500+ Solana NFT collections. Floor prices, holders, volume, rarity scores. Updated weekly. Ideal for analytics and trading bots.",
        "category": "nft_data",
        "size_mb": 350,
        "price_usdc": 14.99,
        "sample_hash": "nft789meta012",
        "format": "json",
    },
]


# ══════════════════════════════════════════════════════════
#  Startup registration (S33: extracted from main.py)
# ══════════════════════════════════════════════════════════

async def register_native_services(db_instance):
    """Register MAXIA native AI services in the database at startup.
    Skips services that already exist (idempotent).
    """
    import logging
    from core.config import TREASURY_ADDRESS
    _logger = logging.getLogger(__name__)
    registered = 0
    for svc in NATIVE_SERVICES:
        try:
            existing = await db_instance.get_service(svc["id"])
            if existing:
                continue
            await db_instance.save_service({
                "id": svc["id"],
                "agent_api_key": "maxia_native",
                "agent_name": "MAXIA",
                "agent_wallet": TREASURY_ADDRESS,
                "name": svc["name"],
                "description": svc["description"],
                "type": svc["type"],
                "price_usdc": svc["price_usdc"],
                "endpoint": "",
                "status": "active",
                "rating": 5.0,
                "rating_count": 0,
                "sales": 0,
            })
            registered += 1
        except Exception as e:
            _logger.error("[MAXIA] Error registering native service %s: %s", svc['id'], e)
    if registered:
        _logger.info("[MAXIA] Registered %s native AI services", registered)
    else:
        _logger.info("[MAXIA] All %s native AI services already registered", len(NATIVE_SERVICES))
