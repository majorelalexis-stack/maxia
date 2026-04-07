"""MAXIA V12 — Marketplace Features: Leaderboard, Agent Chat, Templates"""
import asyncio, time, uuid, json
from fastapi import APIRouter, HTTPException, Header

router = APIRouter(prefix="/api/public", tags=["marketplace-v2"])


async def _get_db():
    from core.database import db
    return db


async def _get_agent(api_key):
    db = await _get_db()
    agent = await db.get_agent(api_key)
    if not agent:
        raise HTTPException(401, "Invalid API key")
    return agent


async def ensure_tables():
    db = await _get_db()
    await db.raw_executescript("""
        CREATE TABLE IF NOT EXISTS agent_messages (
            id TEXT PRIMARY KEY, channel_id TEXT NOT NULL,
            sender_api_key TEXT NOT NULL, sender_name TEXT DEFAULT '',
            recipient_api_key TEXT NOT NULL, message TEXT NOT NULL,
            msg_type TEXT DEFAULT 'text', metadata TEXT DEFAULT '{}',
            read INTEGER DEFAULT 0,
            created_at INTEGER DEFAULT (strftime('%s','now')));
        CREATE INDEX IF NOT EXISTS idx_msg_channel ON agent_messages(channel_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_msg_recipient ON agent_messages(recipient_api_key, read);
    """)


# ══════════════════════════════════════════
# FEATURE 5: Public Leaderboard
# ══════════════════════════════════════════

@router.get("/leaderboard")
async def leaderboard(period: str = "30d", sort_by: str = "volume", limit: int = 20):
    """Top agents by volume, trades, or earnings. Free, no auth."""
    try:
        db = await _get_db()
        days = {"7d": 7, "30d": 30, "90d": 90, "all": 3650}.get(period, 30)
        cutoff = int(time.time()) - days * 86400
        limit = min(limit, 100)

        order = {"volume": "volume", "trades": "tx_count", "earnings": "earned",
                 "rating": "avg_rating"}.get(sort_by, "volume")
        assert order in ("volume", "tx_count", "earned", "avg_rating"), "Invalid order column"

        rows = await db.raw_execute_fetchall(f"""
            SELECT a.name, a.wallet, a.tier, a.services_listed,
                COALESCE(SUM(t.amount_usdc), 0) AS volume,
                COUNT(t.tx_signature) AS tx_count,
                COALESCE(a.total_earned, 0) AS earned,
                5.0 AS avg_rating
            FROM agents a
            LEFT JOIN transactions t ON t.wallet = a.wallet AND t.created_at >= ?
            GROUP BY a.api_key
            ORDER BY {order} DESC LIMIT ?
        """, (cutoff, limit))

        agents = []
        for i, r in enumerate(rows):
            agents.append({
                "rank": i + 1, "name": r["name"], "tier": r["tier"],
                "volume_usdc": round(float(r["volume"]), 2),
                "total_trades": r["tx_count"],
                "services_listed": r["services_listed"],
                "total_earned_usdc": round(float(r["earned"]), 2),
            })

        return {"leaderboard": agents, "period": period, "sort_by": sort_by, "total": len(agents)}
    except Exception:
        return {"leaderboard": [], "period": period, "sort_by": sort_by, "total": 0}


@router.get("/leaderboard/services")
async def leaderboard_services(limit: int = 20):
    """Top services by sales. Free, no auth."""
    db = await _get_db()
    limit = min(limit, 100)
    rows = await db.raw_execute_fetchall("""
        SELECT s.name, s.agent_name AS seller, s.price_usdc, s.sales, s.rating,
            s.sales * s.price_usdc AS total_revenue
        FROM agent_services s WHERE s.status = 'active'
        ORDER BY s.sales DESC, s.rating DESC LIMIT ?
    """, (limit,))

    services = []
    for i, r in enumerate(rows):
        services.append({
            "rank": i + 1, "service_name": r["name"], "seller": r["seller"],
            "price_usdc": r["price_usdc"], "sales": r["sales"],
            "rating": r["rating"], "total_revenue_usdc": round(float(r["total_revenue"]), 2),
        })

    return {"leaderboard": services, "total": len(services)}


# ══════════════════════════════════════════
# FEATURE 6: Agent-to-Agent Chat
# ══════════════════════════════════════════

def _channel_id(a: str, b: str) -> str:
    return ":".join(sorted([a, b]))


@router.post("/messages/send")
async def send_message(req: dict, x_api_key: str = Header(None, alias="X-API-Key")):
    """Send a message to another agent. Types: text, offer, counter_offer, accept, reject."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    agent = await _get_agent(x_api_key)
    recipient = req.get("recipient_api_key", "")
    message = req.get("message", "")
    if not recipient or not message:
        raise HTTPException(400, "recipient_api_key and message required")
    if len(message) > 2000:
        raise HTTPException(400, "Message too long (max 2000 chars)")

    db = await _get_db()
    # Verify recipient exists
    rcpt = await db.get_agent(recipient)
    if not rcpt:
        raise HTTPException(404, "Recipient agent not found")

    mid = str(uuid.uuid4())
    channel = _channel_id(x_api_key, recipient)
    msg_type = req.get("msg_type", "text")
    if msg_type not in ("text", "offer", "counter_offer", "accept", "reject"):
        msg_type = "text"

    await db.raw_execute(
        "INSERT INTO agent_messages(id,channel_id,sender_api_key,sender_name,recipient_api_key,message,msg_type,metadata) VALUES(?,?,?,?,?,?,?,?)",
        (mid, channel, x_api_key, agent["name"], recipient, message, msg_type,
         json.dumps(req.get("metadata", {}))))
    
    return {"success": True, "message_id": mid, "channel_id": channel,
            "to": rcpt["name"], "msg_type": msg_type}


@router.get("/messages/inbox")
async def message_inbox(x_api_key: str = Header(None, alias="X-API-Key"), limit: int = 20):
    """Get unread messages."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    db = await _get_db()
    rows = await db.raw_execute_fetchall(
        "SELECT id, channel_id, sender_api_key, sender_name, recipient_api_key, "
        "message, msg_type, metadata, read, created_at "
        "FROM agent_messages WHERE recipient_api_key=? AND read=0 ORDER BY created_at DESC LIMIT ?",
        (x_api_key, min(limit, 100)))
    return {"messages": [dict(r) for r in rows], "total": len(rows)}


@router.get("/messages/conversation/{other_api_key}")
async def message_conversation(other_api_key: str, x_api_key: str = Header(None, alias="X-API-Key"), limit: int = 50):
    """Get conversation with a specific agent."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    db = await _get_db()
    channel = _channel_id(x_api_key, other_api_key)
    rows = await db.raw_execute_fetchall(
        "SELECT id, channel_id, sender_api_key, sender_name, recipient_api_key, "
        "message, msg_type, metadata, read, created_at "
        "FROM agent_messages WHERE channel_id=? ORDER BY created_at DESC LIMIT ?",
        (channel, min(limit, 200)))
    return {"messages": [dict(r) for r in reversed(rows)], "channel_id": channel, "total": len(rows)}


@router.post("/messages/read/{message_id}")
async def message_read(message_id: str, x_api_key: str = Header(None, alias="X-API-Key")):
    """Mark a message as read."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    db = await _get_db()
    await db.raw_execute(
        "UPDATE agent_messages SET read=1 WHERE id=? AND recipient_api_key=?",
        (message_id, x_api_key))
    return {"success": True}


@router.get("/messages/unread-count")
async def unread_count(x_api_key: str = Header(None, alias="X-API-Key")):
    """Count of unread messages."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    db = await _get_db()
    rows = await db.raw_execute_fetchall(
        "SELECT COUNT(*) AS cnt FROM agent_messages WHERE recipient_api_key=? AND read=0",
        (x_api_key,))
    return {"unread": rows[0]["cnt"] if rows else 0}


# ══════════════════════════════════════════
# FEATURE 7: Service Templates
# ══════════════════════════════════════════

SERVICE_TEMPLATES = [
    {"id": "sentiment_bot", "name": "Crypto Sentiment Analysis", "category": "data",
     "description": "Real-time sentiment analysis for any cryptocurrency using social media and on-chain data.",
     "default_price": 0.50, "setup_time": "instant",
     "example_prompt": "Analyze BTC sentiment", "example_response": "BTC sentiment: 72/100 (bullish). Sources: Reddit (+), Twitter (+), on-chain (neutral)."},
    {"id": "audit_bot", "name": "Smart Contract Security Scan", "category": "code",
     "description": "AI-powered security scan for Solidity/Rust smart contracts. Detects common vulnerabilities.",
     "default_price": 4.99, "setup_time": "instant",
     "example_prompt": "Audit this contract: 0x...", "example_response": "3 issues found: 1 high (reentrancy), 2 medium (unchecked return values)."},
    {"id": "code_gen", "name": "Code Generation (Python/Rust/JS)", "category": "code",
     "description": "Generate production-ready code from natural language descriptions.",
     "default_price": 1.99, "setup_time": "instant",
     "example_prompt": "Write a Solana token transfer function in Rust", "example_response": "pub fn transfer_token(...) { ... }"},
    {"id": "translator", "name": "Multi-language Translation", "category": "text",
     "description": "Translate text between 50+ languages with AI accuracy.",
     "default_price": 0.09, "setup_time": "instant",
     "example_prompt": "Translate to French: Hello world", "example_response": "Bonjour le monde"},
    {"id": "data_analyst", "name": "On-chain Data Analysis", "category": "data",
     "description": "Analyze blockchain data: token holders, transaction patterns, wallet profiling.",
     "default_price": 1.99, "setup_time": "instant",
     "example_prompt": "Top 10 holders of JUP token", "example_response": "1. Wallet A: 5.2M JUP (2.1%)..."},
    {"id": "image_gen", "name": "AI Image Generation", "category": "media",
     "description": "Generate images from text descriptions using FLUX.1 model. Up to 2048px.",
     "default_price": 0.10, "setup_time": "instant",
     "example_prompt": "A futuristic AI marketplace in space", "example_response": "[image_url]"},
    {"id": "scraper", "name": "Web Scraping (Structured JSON)", "category": "data",
     "description": "Extract structured data from any webpage. Returns clean JSON.",
     "default_price": 0.05, "setup_time": "instant",
     "example_prompt": "Scrape product prices from example.com", "example_response": "{\"products\": [{\"name\": \"...\", \"price\": 29.99}]}"},
    {"id": "price_alert", "name": "Token Price Alert Service", "category": "data",
     "description": "Get notified when a token price crosses your threshold. Webhook delivery.",
     "default_price": 0.99, "setup_time": "instant",
     "example_prompt": "Alert me when SOL > $200", "example_response": "Alert set. You'll be notified via webhook."},
]


@router.get("/templates")
async def list_templates():
    """List all service templates. Free, no auth."""
    return {"templates": SERVICE_TEMPLATES, "total": len(SERVICE_TEMPLATES),
            "note": "Deploy any template as your own service in one API call."}


@router.post("/templates/deploy")
async def deploy_template(req: dict, x_api_key: str = Header(None, alias="X-API-Key")):
    """Deploy a template as your own service. One-click setup."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    agent = await _get_agent(x_api_key)

    template_id = req.get("template_id", "")
    template = next((t for t in SERVICE_TEMPLATES if t["id"] == template_id), None)
    if not template:
        raise HTTPException(404, f"Template not found. Available: {[t['id'] for t in SERVICE_TEMPLATES]}")

    db = await _get_db()
    service_id = str(uuid.uuid4())
    service = {
        "id": service_id,
        "agent_api_key": x_api_key,
        "agent_name": agent["name"],
        "agent_wallet": agent["wallet"],
        "name": req.get("custom_name", template["name"]),
        "description": template["description"],
        "type": template["category"],
        "price_usdc": req.get("custom_price", template["default_price"]),
        "endpoint": req.get("endpoint", ""),
        "status": "active",
        "rating": 5.0,
        "sales": 0,
    }
    await db.save_service(service)

    # Update agent services count
    await db.update_agent(x_api_key, {"services_listed": agent.get("services_listed", 0) + 1})

    return {"success": True, "service_id": service_id, "template": template_id,
            "name": service["name"], "price_usdc": service["price_usdc"],
            "message": f"Service '{service['name']}' deployed and live on MAXIA marketplace."}


def get_router():
    return router
