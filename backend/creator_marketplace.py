"""Creator Marketplace — les humains et agents publient et vendent leurs outils.

Modele economique : 90% createur / 10% MAXIA.
Paiement en USDC on-chain.
Types : tools, datasets, prompts, workflows, models.

Inspire de : MCPize (85% rev share), Ocean Protocol (datatokens),
ComfyUI (plugin registry), SuperAGI (toolkit marketplace).
"""
import logging
import time
import uuid
import json

TOOL_CATEGORIES = [
    {"id": "tools", "name": "Tools & APIs", "icon": "🔧", "description": "Scrapers, analyzers, converters, API wrappers"},
    {"id": "datasets", "name": "Datasets", "icon": "📊", "description": "Price history, wallet data, analytics, training data"},
    {"id": "prompts", "name": "Prompts & Templates", "icon": "✍️", "description": "Specialized prompts, system templates, agent configs"},
    {"id": "workflows", "name": "Workflows", "icon": "🔄", "description": "Multi-step automations, agent pipelines, orchestrations"},
    {"id": "models", "name": "Models", "icon": "🧠", "description": "Fine-tuned LLMs, embeddings, classifiers, custom models"},
]

REVENUE_SPLIT = {"creator": 0.90, "platform": 0.10}

PRICING_MODELS = ["per_call", "monthly", "one_time", "free"]

_marketplace_ready = False

async def ensure_marketplace_tables(db):
    """Cree les tables marketplace + seed les services natifs MAXIA."""
    global _marketplace_ready
    if _marketplace_ready:
        return
    try:
        await db.raw_executescript(
            "CREATE TABLE IF NOT EXISTS creator_tools("
            "id TEXT PRIMARY KEY, data TEXT NOT NULL, category TEXT, "
            "creator_wallet TEXT, status TEXT DEFAULT 'active', created_at INTEGER);"
            "CREATE TABLE IF NOT EXISTS creator_reviews("
            "id TEXT PRIMARY KEY, tool_id TEXT, buyer_wallet TEXT, "
            "rating INTEGER, review TEXT, created_at INTEGER);"
            "CREATE TABLE IF NOT EXISTS creator_purchases("
            "id TEXT PRIMARY KEY, tool_id TEXT, buyer_wallet TEXT, "
            "amount_usdc REAL, creator_share REAL, platform_share REAL, created_at INTEGER);"
            "CREATE INDEX IF NOT EXISTS idx_tools_category ON creator_tools(category);"
            "CREATE INDEX IF NOT EXISTS idx_tools_creator ON creator_tools(creator_wallet);")
        _marketplace_ready = True

        # Seed with native MAXIA services if empty
        rows = await db.raw_execute_fetchall("SELECT COUNT(*) as c FROM creator_tools")
        count = rows[0]["c"] if isinstance(rows[0], dict) else rows[0][0]
        if count == 0:
            await _seed_native_services(db)
    except Exception as e:
        logging.getLogger(__name__).error(f"[Marketplace] Schema error: {e}")


async def _seed_native_services(db):
    """Seed le marketplace avec les services natifs MAXIA."""
    now = int(time.time())
    services = [
        {"id": "maxia-audit", "name": "AI Code Audit", "description": "Automated security + quality audit of your codebase by AI. Finds vulnerabilities, bad patterns, and suggests fixes.", "category": "tools", "price_usdc": 4.99, "tags": ["security", "code", "audit"]},
        {"id": "maxia-code-review", "name": "AI Code Review", "description": "Comprehensive code review — style, bugs, performance, security. Like a senior engineer reviewing your PR.", "category": "tools", "price_usdc": 2.99, "tags": ["code", "review", "quality"]},
        {"id": "maxia-translate", "name": "AI Translation", "description": "Translate text between 100+ languages. Context-aware, preserves formatting.", "category": "tools", "price_usdc": 0.05, "tags": ["translation", "language", "text"]},
        {"id": "maxia-summary", "name": "AI Summarizer", "description": "Summarize long documents, articles, papers into concise key points.", "category": "tools", "price_usdc": 0.49, "tags": ["summary", "text", "nlp"]},
        {"id": "maxia-wallet-analysis", "name": "Wallet Analysis", "description": "Deep analysis of any Solana/EVM wallet — holdings, history, risk score, whale detection.", "category": "tools", "price_usdc": 1.99, "tags": ["wallet", "analysis", "onchain"]},
        {"id": "maxia-marketing", "name": "AI Marketing Copy", "description": "Generate marketing copy, social posts, ad text, landing page content.", "category": "prompts", "price_usdc": 0.99, "tags": ["marketing", "copy", "content"]},
        {"id": "maxia-image", "name": "AI Image Generation", "description": "Generate images from text prompts via Pollinations.ai. Free, no GPU needed.", "category": "tools", "price_usdc": 0.10, "tags": ["image", "generation", "ai"]},
        {"id": "maxia-scraper", "name": "Web Scraper", "description": "Scrape any URL and get structured text, links, images. SSRF-protected.", "category": "tools", "price_usdc": 0.02, "tags": ["scraper", "web", "data"]},
        {"id": "maxia-sentiment", "name": "Sentiment Analysis", "description": "Analyze sentiment of text, tweets, reviews. Returns score + confidence.", "category": "tools", "price_usdc": 0.005, "tags": ["sentiment", "nlp", "analysis"]},
        {"id": "maxia-wallet-risk", "name": "Wallet Risk Score", "description": "Risk assessment for any wallet — fraud detection, suspicious patterns.", "category": "tools", "price_usdc": 0.10, "tags": ["risk", "wallet", "security"]},
        {"id": "maxia-airdrop-scanner", "name": "Airdrop Scanner", "description": "Scan your wallet for unclaimed airdrops across Solana + EVM chains.", "category": "tools", "price_usdc": 0.50, "tags": ["airdrop", "scanner", "defi"]},
        {"id": "maxia-smart-money", "name": "Smart Money Tracker", "description": "Track whale wallets and smart money flows in real-time.", "category": "datasets", "price_usdc": 0.25, "tags": ["whale", "tracking", "alpha"]},
        {"id": "maxia-transcription", "name": "Audio Transcription", "description": "Transcribe audio/video to text. Supports 50+ languages.", "category": "tools", "price_usdc": 0.01, "tags": ["transcription", "audio", "speech"]},
        {"id": "maxia-embedding", "name": "Text Embeddings", "description": "Generate vector embeddings for text. Useful for RAG, search, similarity.", "category": "models", "price_usdc": 0.001, "tags": ["embedding", "vector", "rag"]},
        {"id": "maxia-nft-rarity", "name": "NFT Rarity Checker", "description": "Check rarity score for any NFT collection on Solana.", "category": "tools", "price_usdc": 0.05, "tags": ["nft", "rarity", "solana"]},
        {"id": "maxia-finetune", "name": "LLM Fine-Tuning", "description": "Fine-tune Llama, Qwen, Mistral on your data. Powered by Unsloth on Akash GPUs.", "category": "models", "price_usdc": 2.99, "tags": ["finetune", "llm", "training"]},
        {"id": "maxia-defi-yields", "name": "DeFi Yield Finder", "description": "Find the best APY across lending, staking, LP on 14 chains.", "category": "datasets", "price_usdc": 0.10, "tags": ["defi", "yields", "apy"]},
    ]
    for svc in services:
        tool = {
            "id": svc["id"], "name": svc["name"], "description": svc["description"],
            "category": svc["category"], "creator_wallet": "MAXIA_NATIVE",
            "creator_name": "MAXIA", "pricing_model": "per_call",
            "price_usdc": svc["price_usdc"], "tags": svc["tags"],
            "version": "1.0.0", "status": "active", "downloads": 0,
            "avg_rating": 5.0, "rating_count": 0, "created_at": now,
        }
        try:
            await db.raw_execute(
                "INSERT INTO creator_tools(id, data, category, creator_wallet, status, created_at) VALUES(?,?,?,?,?,?)",
                (svc["id"], json.dumps(tool, default=str), svc["category"], "MAXIA_NATIVE", "active", now))
        except Exception:
            pass  # Already exists
    logging.getLogger(__name__).info(f"[Marketplace] Seeded {len(services)} native services")


async def publish_tool(db, data: dict) -> dict:
    """Publish a new tool/dataset/prompt/workflow/model on the marketplace."""
    tool_id = f"tool_{uuid.uuid4().hex[:12]}"
    now = int(time.time())

    required = ["name", "description", "category", "creator_wallet"]
    for field in required:
        if not data.get(field):
            return {"error": f"{field} is required"}

    tool = {
        "id": tool_id,
        "name": data["name"][:100],
        "description": data["description"][:2000],
        "category": data["category"],
        "creator_wallet": data["creator_wallet"],
        "creator_name": data.get("creator_name", "Anonymous"),
        "pricing_model": data.get("pricing_model", "per_call"),
        "price_usdc": float(data.get("price_usdc", 0)),
        "endpoint": data.get("endpoint", ""),  # URL of the tool API
        "tags": data.get("tags", [])[:10],
        "version": data.get("version", "1.0.0"),
        "changelog": data.get("changelog", "Initial release"),
        "documentation": data.get("documentation", "")[:5000],
        "status": "active",
        "total_sales": 0,
        "total_revenue_usdc": 0,
        "total_calls": 0,
        "avg_rating": 0,
        "rating_count": 0,
        "created_at": now,
        "updated_at": now,
    }

    try:
        await db.raw_execute(
            "INSERT INTO creator_tools(id, data, category, creator_wallet, status, created_at) VALUES(?,?,?,?,?,?)",
            (tool_id, json.dumps(tool, default=str), tool["category"], tool["creator_wallet"], "active", now))
    except Exception:
        await db.raw_executescript(
            "CREATE TABLE IF NOT EXISTS creator_tools("
            "id TEXT PRIMARY KEY, data TEXT NOT NULL, category TEXT, "
            "creator_wallet TEXT, status TEXT DEFAULT 'active', created_at INTEGER);"
            "CREATE TABLE IF NOT EXISTS creator_reviews("
            "id TEXT PRIMARY KEY, tool_id TEXT, buyer_wallet TEXT, "
            "rating INTEGER, review TEXT, created_at INTEGER);"
            "CREATE TABLE IF NOT EXISTS creator_purchases("
            "id TEXT PRIMARY KEY, tool_id TEXT, buyer_wallet TEXT, "
            "amount_usdc REAL, creator_share REAL, platform_share REAL, created_at INTEGER);"
            "CREATE INDEX IF NOT EXISTS idx_tools_category ON creator_tools(category);"
            "CREATE INDEX IF NOT EXISTS idx_tools_creator ON creator_tools(creator_wallet);")
        await db.raw_execute(
            "INSERT INTO creator_tools(id, data, category, creator_wallet, status, created_at) VALUES(?,?,?,?,?,?)",
            (tool_id, json.dumps(tool, default=str), tool["category"], tool["creator_wallet"], "active", now))

    return tool


async def update_tool_version(db, tool_id: str, creator_wallet: str, data: dict) -> dict:
    """Update a tool to a new version."""
    try:
        rows = await db.raw_execute_fetchall(
            "SELECT data FROM creator_tools WHERE id=? AND creator_wallet=?", (tool_id, creator_wallet))
        if not rows:
            return {"error": "Tool not found or not yours"}
        tool = json.loads(rows[0]["data"])
        tool["version"] = data.get("version", tool["version"])
        tool["changelog"] = data.get("changelog", "")
        tool["description"] = data.get("description", tool["description"])
        tool["endpoint"] = data.get("endpoint", tool["endpoint"])
        tool["price_usdc"] = float(data.get("price_usdc", tool["price_usdc"]))
        tool["updated_at"] = int(time.time())
        await db.raw_execute(
            "UPDATE creator_tools SET data=? WHERE id=?",
            (json.dumps(tool, default=str), tool_id))
        return tool
    except Exception as e:
        return safe_error(e, "operation")


async def purchase_tool(db, tool_id: str, buyer_wallet: str) -> dict:
    """Record a tool purchase and calculate revenue split."""
    try:
        rows = await db.raw_execute_fetchall("SELECT data FROM creator_tools WHERE id=?", (tool_id,))
        if not rows:
            return {"error": "Tool not found"}
        tool = json.loads(rows[0]["data"])

        price = tool.get("price_usdc", 0)
        creator_share = round(price * REVENUE_SPLIT["creator"], 4)
        platform_share = round(price * REVENUE_SPLIT["platform"], 4)

        purchase_id = f"purchase_{uuid.uuid4().hex[:8]}"
        now = int(time.time())
        await db.raw_execute(
            "INSERT INTO creator_purchases(id, tool_id, buyer_wallet, amount_usdc, creator_share, platform_share, created_at) VALUES(?,?,?,?,?,?,?)",
            (purchase_id, tool_id, buyer_wallet, price, creator_share, platform_share, now))

        # Update tool stats
        tool["total_sales"] = tool.get("total_sales", 0) + 1
        tool["total_revenue_usdc"] = round(tool.get("total_revenue_usdc", 0) + price, 4)
        tool["total_calls"] = tool.get("total_calls", 0) + 1
        await db.raw_execute("UPDATE creator_tools SET data=? WHERE id=?",
            (json.dumps(tool, default=str), tool_id))

        return {
            "success": True,
            "purchase_id": purchase_id,
            "tool": tool["name"],
            "price_usdc": price,
            "creator_gets": creator_share,
            "platform_gets": platform_share,
        }
    except Exception as e:
        return safe_error(e, "operation")


async def review_tool(db, tool_id: str, buyer_wallet: str, rating: int, review_text: str) -> dict:
    """Leave a review — only verified purchasers can review."""
    try:
        # Check if buyer actually purchased
        purchases = await db.raw_execute_fetchall(
            "SELECT id FROM creator_purchases WHERE tool_id=? AND buyer_wallet=?",
            (tool_id, buyer_wallet))
        if not purchases:
            return {"error": "Only purchasers can leave reviews"}

        # Check if already reviewed
        existing = await db.raw_execute_fetchall(
            "SELECT id FROM creator_reviews WHERE tool_id=? AND buyer_wallet=?",
            (tool_id, buyer_wallet))
        if existing:
            return {"error": "Already reviewed"}

        rating = max(1, min(5, int(rating)))
        review_id = f"review_{uuid.uuid4().hex[:8]}"
        now = int(time.time())
        await db.raw_execute(
            "INSERT INTO creator_reviews(id, tool_id, buyer_wallet, rating, review, created_at) VALUES(?,?,?,?,?,?)",
            (review_id, tool_id, buyer_wallet, rating, review_text[:500], now))

        # Update average rating
        all_reviews = await db.raw_execute_fetchall(
            "SELECT rating FROM creator_reviews WHERE tool_id=?", (tool_id,))
        avg = sum(r["rating"] for r in all_reviews) / len(all_reviews) if all_reviews else 0

        rows = await db.raw_execute_fetchall("SELECT data FROM creator_tools WHERE id=?", (tool_id,))
        if rows:
            tool = json.loads(rows[0]["data"])
            tool["avg_rating"] = round(avg, 1)
            tool["rating_count"] = len(all_reviews)
            await db.raw_execute("UPDATE creator_tools SET data=? WHERE id=?",
                (json.dumps(tool, default=str), tool_id))

        return {"success": True, "rating": rating}
    except Exception as e:
        return safe_error(e, "operation")


async def get_tools(db, category: str = "", sort: str = "popular", limit: int = 20) -> list:
    """List tools from the marketplace."""
    try:
        if category:
            rows = await db.raw_execute_fetchall(
                "SELECT data FROM creator_tools WHERE category=? AND status='active' ORDER BY created_at DESC LIMIT ?",
                (category, limit))
        else:
            rows = await db.raw_execute_fetchall(
                "SELECT data FROM creator_tools WHERE status='active' ORDER BY created_at DESC LIMIT ?",
                (limit,))
        tools = [json.loads(r["data"]) for r in rows]
        if sort == "popular":
            tools.sort(key=lambda t: t.get("total_sales", 0), reverse=True)
        elif sort == "rating":
            tools.sort(key=lambda t: t.get("avg_rating", 0), reverse=True)
        elif sort == "price_low":
            tools.sort(key=lambda t: t.get("price_usdc", 0))
        elif sort == "price_high":
            tools.sort(key=lambda t: t.get("price_usdc", 0), reverse=True)
        return tools
    except Exception:
        return []


async def get_tool_detail(db, tool_id: str) -> dict:
    """Get tool details with reviews."""
    try:
        rows = await db.raw_execute_fetchall("SELECT data FROM creator_tools WHERE id=?", (tool_id,))
        if not rows:
            return {"error": "Tool not found"}
        tool = json.loads(rows[0]["data"])
        reviews = await db.raw_execute_fetchall(
            "SELECT * FROM creator_reviews WHERE tool_id=? ORDER BY created_at DESC LIMIT 20",
            (tool_id,))
        tool["reviews"] = [dict(r) for r in reviews]
        return tool
    except Exception as e:
        return safe_error(e, "operation")


async def get_creator_stats(db, wallet: str) -> dict:
    """Revenue dashboard for a creator."""
    try:
        tools = await db.raw_execute_fetchall(
            "SELECT data FROM creator_tools WHERE creator_wallet=?", (wallet,))
        purchases = await db.raw_execute_fetchall(
            "SELECT * FROM creator_purchases WHERE tool_id IN (SELECT id FROM creator_tools WHERE creator_wallet=?) ORDER BY created_at DESC LIMIT 50",
            (wallet,))

        total_revenue = sum(p.get("creator_share", 0) or 0 for p in purchases)
        total_sales = len(purchases)

        tools_list = [json.loads(t["data"]) for t in tools]

        return {
            "wallet": wallet,
            "total_tools": len(tools_list),
            "total_revenue_usdc": round(total_revenue, 2),
            "total_sales": total_sales,
            "revenue_split": REVENUE_SPLIT,
            "tools": tools_list,
            "recent_purchases": [dict(p) for p in purchases[:20]],
        }
    except Exception as e:
        return {"error": "An error occurred", "wallet": wallet}


async def search_tools(db, query: str, limit: int = 20) -> list:
    """Search tools by name/description."""
    try:
        rows = await db.raw_execute_fetchall(
            "SELECT data FROM creator_tools WHERE status='active' AND data LIKE ? ORDER BY created_at DESC LIMIT ?",
            (f"%{query}%", limit))
        return [json.loads(r["data"]) for r in rows]
    except Exception:
        return []
