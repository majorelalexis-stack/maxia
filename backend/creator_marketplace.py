"""Creator Marketplace — les humains et agents publient et vendent leurs outils.

Modele economique : 90% createur / 10% MAXIA.
Paiement en USDC on-chain.
Types : tools, datasets, prompts, workflows, models.

Inspire de : MCPize (85% rev share), Ocean Protocol (datatokens),
ComfyUI (plugin registry), SuperAGI (toolkit marketplace).
"""
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
        return {"error": str(e)}


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
        return {"error": str(e)}


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
        return {"error": str(e)}


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
        return {"error": str(e)}


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
        return {"error": str(e), "wallet": wallet}


async def search_tools(db, query: str, limit: int = 20) -> list:
    """Search tools by name/description."""
    try:
        rows = await db.raw_execute_fetchall(
            "SELECT data FROM creator_tools WHERE status='active' AND data LIKE ? ORDER BY created_at DESC LIMIT ?",
            (f"%{query}%", limit))
        return [json.loads(r["data"]) for r in rows]
    except Exception:
        return []
