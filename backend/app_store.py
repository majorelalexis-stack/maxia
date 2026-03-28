"""AI Agent App Store — marketplace pour humains qui veulent utiliser des agents IA.

Les devs publient leurs agents avec une description, un prix, et une demo.
Les humains "installent" un agent (connectent leur wallet) et l'agent travaille pour eux.
MAXIA prend 10% de commission sur chaque transaction.
"""
import time

# Categories d'agents
CATEGORIES = [
    {"id": "trading", "name": "Trading & DeFi", "icon": "\U0001f4c8", "description": "Bots de trading, yield farming, arbitrage"},
    {"id": "analytics", "name": "Analytics & Data", "icon": "\U0001f4ca", "description": "Analyse de wallets, sentiment, risk scoring"},
    {"id": "content", "name": "Content & Marketing", "icon": "\u270d\ufe0f", "description": "Generation de contenu, tweets, blog posts"},
    {"id": "compute", "name": "Compute & AI", "icon": "\U0001f5a5\ufe0f", "description": "GPU rental, LLM inference, fine-tuning"},
    {"id": "security", "name": "Security & Audit", "icon": "\U0001f512", "description": "Smart contract audit, wallet monitoring"},
    {"id": "utility", "name": "Utility & Tools", "icon": "\U0001f6e0\ufe0f", "description": "Scraping, translation, conversion"},
]

# Categories lookup par id
CATEGORIES_MAP = {c["id"]: c for c in CATEGORIES}


async def get_featured_agents(db, limit: int = 12) -> list:
    """Get featured agents for the App Store homepage."""
    try:
        rows = await db.raw_execute_fetchall(
            "SELECT id, name, description, price_usdc, rating, rating_count, sales, agent_name, type "
            "FROM agent_services WHERE status='active' ORDER BY sales DESC, rating DESC LIMIT ?",
            (limit,))
        agents = []
        for r in rows:
            d = dict(r)
            agents.append({
                "id": d.get("id", ""),
                "name": d.get("name", ""),
                "description": (d.get("description", "") or "")[:200],
                "price_usdc": d.get("price_usdc", 0),
                "rating": d.get("rating", 0),
                "rating_count": d.get("rating_count", 0),
                "sales": d.get("sales", 0),
                "agent_name": d.get("agent_name", ""),
                "type": d.get("type", "utility"),
            })
        return agents
    except Exception as e:
        print(f"[AppStore] get_featured_agents error: {e}")
        return []


async def get_agents_by_category(db, category: str, limit: int = 20) -> list:
    """Get agents filtered by category."""
    try:
        rows = await db.raw_execute_fetchall(
            "SELECT id, name, description, price_usdc, rating, rating_count, sales, agent_name, type "
            "FROM agent_services WHERE status='active' AND type=? ORDER BY sales DESC LIMIT ?",
            (category, limit))
        agents = []
        for r in rows:
            d = dict(r)
            agents.append({
                "id": d.get("id", ""),
                "name": d.get("name", ""),
                "description": (d.get("description", "") or "")[:200],
                "price_usdc": d.get("price_usdc", 0),
                "rating": d.get("rating", 0),
                "rating_count": d.get("rating_count", 0),
                "sales": d.get("sales", 0),
                "agent_name": d.get("agent_name", ""),
                "type": d.get("type", "utility"),
            })
        return agents
    except Exception as e:
        print(f"[AppStore] get_agents_by_category error: {e}")
        return []


async def search_agents(db, query: str, limit: int = 20) -> list:
    """Search agents by name or description."""
    try:
        rows = await db.raw_execute_fetchall(
            "SELECT id, name, description, price_usdc, rating, rating_count, sales, agent_name, type "
            "FROM agent_services WHERE status='active' AND (name LIKE ? OR description LIKE ?) ORDER BY sales DESC LIMIT ?",
            (f"%{query}%", f"%{query}%", limit))
        agents = []
        for r in rows:
            d = dict(r)
            agents.append({
                "id": d.get("id", ""),
                "name": d.get("name", ""),
                "description": (d.get("description", "") or "")[:200],
                "price_usdc": d.get("price_usdc", 0),
                "rating": d.get("rating", 0),
                "rating_count": d.get("rating_count", 0),
                "sales": d.get("sales", 0),
                "agent_name": d.get("agent_name", ""),
                "type": d.get("type", "utility"),
            })
        return agents
    except Exception as e:
        print(f"[AppStore] search_agents error: {e}")
        return []
