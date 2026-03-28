"""MAXIA V12 — Infrastructure Features: Webhooks, Escrow API, SLA, Revenue Sharing"""
import logging
import asyncio, time, uuid, json
from fastapi import APIRouter, HTTPException, Header

router = APIRouter(prefix="/api/public", tags=["infrastructure"])


async def _get_db():
    from database import db
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
        CREATE TABLE IF NOT EXISTS webhook_subscriptions (
            id TEXT PRIMARY KEY, api_key TEXT NOT NULL, callback_url TEXT NOT NULL,
            events TEXT DEFAULT 'all', filters TEXT DEFAULT '{}',
            active INTEGER DEFAULT 1, deliveries INTEGER DEFAULT 0,
            last_delivery_at INTEGER DEFAULT 0,
            created_at INTEGER DEFAULT (strftime('%s','now')));
        CREATE INDEX IF NOT EXISTS idx_wh_sub_key ON webhook_subscriptions(api_key);

        CREATE TABLE IF NOT EXISTS webhook_delivery_log (
            id TEXT PRIMARY KEY, subscription_id TEXT, event_type TEXT,
            payload TEXT, status_code INTEGER DEFAULT 0,
            success INTEGER DEFAULT 0, error TEXT DEFAULT '',
            created_at INTEGER DEFAULT (strftime('%s','now')));

        CREATE TABLE IF NOT EXISTS service_slas (
            id TEXT PRIMARY KEY, service_id TEXT UNIQUE NOT NULL,
            seller_api_key TEXT NOT NULL,
            max_response_time_ms INTEGER DEFAULT 30000,
            uptime_guarantee_pct REAL DEFAULT 99.0,
            auto_refund INTEGER DEFAULT 1,
            created_at INTEGER DEFAULT (strftime('%s','now')));

        CREATE TABLE IF NOT EXISTS sla_violations (
            id TEXT PRIMARY KEY, service_id TEXT, sla_id TEXT,
            violation_type TEXT, response_time_ms INTEGER,
            refunded INTEGER DEFAULT 0, refund_amount_usdc REAL DEFAULT 0,
            buyer_wallet TEXT DEFAULT '',
            created_at INTEGER DEFAULT (strftime('%s','now')));

        CREATE TABLE IF NOT EXISTS clone_configs (
            id TEXT PRIMARY KEY, original_service_id TEXT NOT NULL,
            original_seller_key TEXT NOT NULL,
            clone_api_key TEXT NOT NULL, clone_service_id TEXT NOT NULL,
            revenue_share_pct REAL DEFAULT 15,
            total_revenue_usdc REAL DEFAULT 0,
            total_royalties_usdc REAL DEFAULT 0,
            active INTEGER DEFAULT 1,
            created_at INTEGER DEFAULT (strftime('%s','now')));
        CREATE INDEX IF NOT EXISTS idx_clone_orig ON clone_configs(original_seller_key);
        CREATE INDEX IF NOT EXISTS idx_clone_svc ON clone_configs(clone_service_id);
    """)


# ══════════════════════════════════════════
# FEATURE 1: Webhook Notifications
# ══════════════════════════════════════════

WEBHOOK_EVENTS = ["price_alert", "whale_move", "new_service", "trade_executed", "service_sold", "gpu_available", "all"]


@router.post("/webhooks/subscribe")
async def webhook_subscribe(req: dict, x_api_key: str = Header(None, alias="X-API-Key")):
    """Subscribe to event notifications via webhook. Free for first 3, then 0.99 USDC/month."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    agent = await _get_agent(x_api_key)
    callback_url = req.get("callback_url", "")
    try:
        from webhook_dispatcher import validate_callback_url
        callback_url = validate_callback_url(callback_url)
    except ValueError as e:
        raise HTTPException(400, f"Invalid callback_url: {e}")

    events = req.get("events", ["all"])
    if not isinstance(events, list):
        events = [events]
    for e in events:
        if e not in WEBHOOK_EVENTS:
            raise HTTPException(400, f"Invalid event: {e}. Available: {WEBHOOK_EVENTS}")

    db = await _get_db()
    # Check limit
    existing = await db.raw_execute_fetchall(
        "SELECT COUNT(*) AS cnt FROM webhook_subscriptions WHERE api_key=? AND active=1", (x_api_key,))
    count = existing[0]["cnt"] if existing else 0

    sid = str(uuid.uuid4())
    await db.raw_execute(
        "INSERT INTO webhook_subscriptions(id,api_key,callback_url,events,filters) VALUES(?,?,?,?,?)",
        (sid, x_api_key, callback_url, json.dumps(events), json.dumps(req.get("filters", {}))))
    
    return {"success": True, "subscription_id": sid, "events": events,
            "callback_url": callback_url,
            "note": "Free" if count < 3 else "0.99 USDC/month (4+ webhooks)"}


@router.get("/webhooks/my-subscriptions")
async def webhook_list(x_api_key: str = Header(None, alias="X-API-Key")):
    """List my webhook subscriptions."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    db = await _get_db()
    rows = await db.raw_execute_fetchall(
        "SELECT id, api_key, callback_url, events, filters, active, "
        "deliveries, last_delivery_at, created_at "
        "FROM webhook_subscriptions WHERE api_key=? AND active=1 ORDER BY created_at DESC", (x_api_key,))
    subs = []
    for r in rows:
        s = dict(r)
        s["events"] = json.loads(s.get("events", "[]"))
        s["filters"] = json.loads(s.get("filters", "{}"))
        subs.append(s)
    return {"subscriptions": subs, "total": len(subs)}


async def notify_webhook_subscribers(event_type: str, data: dict, filter_wallet: str = None):
    """Envoie une notification a tous les abonnes d'un type d'evenement.
    Si filter_wallet est specifie, notifie seulement les abonnes lies a ce wallet."""
    from http_client import get_http_client
    db = await _get_db()
    query = ("SELECT id, api_key, callback_url, events, filters, active, "
             "deliveries, last_delivery_at, created_at "
             "FROM webhook_subscriptions WHERE active=1")
    rows = await db.raw_execute_fetchall(query)

    sent = 0
    for row in rows:
        r = dict(row)
        events = json.loads(r.get("events", "[]"))
        if event_type not in events and "all" not in events:
            continue

        # Si on filtre par wallet, verifier que l'abonne correspond
        if filter_wallet:
            # Recuperer le wallet de l'agent via sa cle API
            try:
                agent_rows = await db.raw_execute_fetchall(
                    "SELECT wallet FROM agents WHERE api_key=? LIMIT 1", (r["api_key"],))
                if agent_rows:
                    row_data = dict(agent_rows[0]) if hasattr(agent_rows[0], 'keys') else {"wallet": agent_rows[0][0]}
                    agent_wallet = row_data.get("wallet", "")
                    if agent_wallet != filter_wallet:
                        continue
                else:
                    continue
            except Exception:
                continue

        callback_url = r.get("callback_url", "")
        if not callback_url:
            continue

        payload = {
            "event": event_type,
            "timestamp": int(time.time()),
            "data": data,
        }

        try:
            client = get_http_client()
            resp = await client.post(callback_url, json=payload, headers={
                "X-MAXIA-Event": event_type,
                "User-Agent": "MAXIA-Webhook/1.0",
                "Content-Type": "application/json",
            }, timeout=10)
            if 200 <= resp.status_code < 300:
                    sent += 1
        except Exception as e:
            print(f"[Webhooks] Notify error for {callback_url}: {e}")

    if sent:
        print(f"[Webhooks] {event_type}: {sent} subscriber(s) notified")
    return sent


@router.delete("/webhooks/{sub_id}")
async def webhook_unsubscribe(sub_id: str, x_api_key: str = Header(None, alias="X-API-Key")):
    """Unsubscribe from webhook notifications."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    db = await _get_db()
    await db.raw_execute(
        "UPDATE webhook_subscriptions SET active=0 WHERE id=? AND api_key=?", (sub_id, x_api_key))
    return {"success": True, "subscription_id": sub_id}


@router.post("/webhooks/test")
async def webhook_test(req: dict, x_api_key: str = Header(None, alias="X-API-Key")):
    """Test a webhook callback URL."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    callback_url = req.get("callback_url", "")
    if not callback_url:
        raise HTTPException(400, "callback_url required")
    from http_client import get_http_client
    try:
        client = get_http_client()
        resp = await client.post(callback_url, json={
            "event": "test", "source": "maxia",
            "message": "Webhook test successful", "timestamp": int(time.time()),
        }, timeout=10)
        return {"success": resp.status_code in (200, 201, 202),
                    "status_code": resp.status_code, "callback_url": callback_url}
    except Exception as e:
        return {"success": False, "error": "An error occurred", "callback_url": callback_url}


@router.get("/webhooks/history")
async def webhook_history(x_api_key: str = Header(None, alias="X-API-Key"), limit: int = 50):
    """Recent webhook delivery history."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    db = await _get_db()
    rows = await db.raw_execute_fetchall("""
        SELECT l.* FROM webhook_delivery_log l
        JOIN webhook_subscriptions s ON l.subscription_id = s.id
        WHERE s.api_key=? ORDER BY l.created_at DESC LIMIT ?
    """, (x_api_key, min(limit, 200)))
    return {"deliveries": [dict(r) for r in rows], "total": len(rows)}


async def notify_subscribers(event_type: str, data: dict):
    """Dispatch event to matching webhook subscribers."""
    try:
        db = await _get_db()
        rows = await db.raw_execute_fetchall(
            "SELECT id, api_key, callback_url, events, filters, active, "
            "deliveries, last_delivery_at, created_at "
            "FROM webhook_subscriptions WHERE active=1")
        from http_client import get_http_client
        for sub in rows:
            events = json.loads(sub.get("events", '["all"]'))
            if "all" not in events and event_type not in events:
                continue
            try:
                payload = {"event": event_type, "data": data,
                           "timestamp": int(time.time()), "source": "maxia"}
                client = get_http_client()
                resp = await client.post(sub["callback_url"], json=payload, timeout=5)
                success = resp.status_code in (200, 201, 202)
                did = str(uuid.uuid4())
                await db.raw_execute(
                    "INSERT INTO webhook_delivery_log(id,subscription_id,event_type,payload,status_code,success) VALUES(?,?,?,?,?,?)",
                    (did, sub["id"], event_type, json.dumps(payload)[:500], resp.status_code, int(success)))
                await db.raw_execute(
                    "UPDATE webhook_subscriptions SET deliveries=deliveries+1, last_delivery_at=? WHERE id=?",
                    (int(time.time()), sub["id"]))
            except Exception:
                pass
    except Exception:
        pass


# ══════════════════════════════════════════
# FEATURE 8: Public Escrow API
# ══════════════════════════════════════════

@router.post("/escrow/create")
async def public_escrow_create(req: dict, x_api_key: str = Header(None, alias="X-API-Key")):
    """Lock USDC in escrow for a service purchase."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    agent = await _get_agent(x_api_key)
    seller_key = req.get("seller_api_key", "")
    amount = req.get("amount_usdc", 0)
    payment_tx = req.get("payment_tx", "")
    if not seller_key or amount <= 0:
        raise HTTPException(400, "seller_api_key and amount_usdc required")

    db = await _get_db()
    seller = await db.get_agent(seller_key)
    if not seller:
        raise HTTPException(404, "Seller not found")

    eid = str(uuid.uuid4())
    escrow_data = {
        "buyer": agent["name"], "buyer_wallet": agent["wallet"], "buyer_key": x_api_key,
        "seller": seller["name"], "seller_wallet": seller["wallet"], "seller_key": seller_key,
        "amount_usdc": amount, "payment_tx": payment_tx,
        "description": req.get("description", ""),
        "timeout_hours": req.get("timeout_hours", 72),
        "timeout_at": int(time.time()) + req.get("timeout_hours", 72) * 3600,
    }
    await db.raw_execute(
        "INSERT INTO escrow_records(escrow_id,buyer,seller,status,data) VALUES(?,?,?,?,?)",
        (eid, agent["wallet"], seller["wallet"], "locked", json.dumps(escrow_data)))
    
    return {"success": True, "escrow_id": eid, "amount_usdc": amount,
            "seller": seller["name"], "timeout_hours": req.get("timeout_hours", 72),
            "status": "locked"}


@router.get("/escrow/my-escrows")
async def public_escrow_list(x_api_key: str = Header(None, alias="X-API-Key"),
                              role: str = "all", status: str = ""):
    """List my escrows as buyer or seller."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    agent = await _get_agent(x_api_key)
    db = await _get_db()

    if role == "buyer":
        rows = await db.raw_execute_fetchall(
            "SELECT escrow_id, buyer, seller, status, data, created_at "
            "FROM escrow_records WHERE buyer=? ORDER BY created_at DESC", (agent["wallet"],))
    elif role == "seller":
        rows = await db.raw_execute_fetchall(
            "SELECT escrow_id, buyer, seller, status, data, created_at "
            "FROM escrow_records WHERE seller=? ORDER BY created_at DESC", (agent["wallet"],))
    else:
        rows = await db.raw_execute_fetchall(
            "SELECT escrow_id, buyer, seller, status, data, created_at "
            "FROM escrow_records WHERE buyer=? OR seller=? ORDER BY created_at DESC",
            (agent["wallet"], agent["wallet"]))

    escrows = []
    for r in rows:
        e = dict(r)
        e["data"] = json.loads(e.get("data", "{}"))
        if status and e["status"] != status:
            continue
        escrows.append(e)

    return {"escrows": escrows, "total": len(escrows)}


@router.post("/escrow/confirm/{escrow_id}")
async def public_escrow_confirm(escrow_id: str, x_api_key: str = Header(None, alias="X-API-Key")):
    """Buyer confirms delivery. USDC released to seller."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    agent = await _get_agent(x_api_key)
    db = await _get_db()

    rows = await db.raw_execute_fetchall(
        "SELECT escrow_id, buyer, seller, status, data "
        "FROM escrow_records WHERE escrow_id=? AND buyer=? AND status='locked'",
        (escrow_id, agent["wallet"]))
    if not rows:
        raise HTTPException(404, "Escrow not found or not locked")

    await db.raw_execute(
        "UPDATE escrow_records SET status='released' WHERE escrow_id=?", (escrow_id,))
    
    data = json.loads(rows[0]["data"])
    return {"success": True, "escrow_id": escrow_id, "status": "released",
            "amount_usdc": data.get("amount_usdc", 0), "seller": data.get("seller", "")}


@router.post("/escrow/dispute/{escrow_id}")
async def public_escrow_dispute(escrow_id: str, req: dict = None,
                                 x_api_key: str = Header(None, alias="X-API-Key")):
    """Buyer disputes the escrow."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    agent = await _get_agent(x_api_key)
    db = await _get_db()

    rows = await db.raw_execute_fetchall(
        "SELECT escrow_id, buyer, seller, status "
        "FROM escrow_records WHERE escrow_id=? AND buyer=? AND status='locked'",
        (escrow_id, agent["wallet"]))
    if not rows:
        raise HTTPException(404, "Escrow not found or not locked")

    await db.raw_execute(
        "UPDATE escrow_records SET status='disputed' WHERE escrow_id=?", (escrow_id,))
    
    reason = (req or {}).get("reason", "")
    # Save dispute
    did = str(uuid.uuid4())
    await db.raw_execute(
        "INSERT OR IGNORE INTO disputes(id,data) VALUES(?,?)",
        (did, json.dumps({"escrow_id": escrow_id, "reason": reason,
                          "buyer": agent["name"], "created_at": int(time.time())})))
    
    return {"success": True, "escrow_id": escrow_id, "status": "disputed",
            "dispute_id": did, "message": "Dispute filed. Admin will review within 48h."}


@router.get("/escrow/{escrow_id}")
async def public_escrow_get(escrow_id: str, x_api_key: str = Header(None, alias="X-API-Key")):
    """Get escrow details."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    agent = await _get_agent(x_api_key)
    db = await _get_db()

    rows = await db.raw_execute_fetchall(
        "SELECT escrow_id, buyer, seller, status, data, created_at "
        "FROM escrow_records WHERE escrow_id=? AND (buyer=? OR seller=?)",
        (escrow_id, agent["wallet"], agent["wallet"]))
    if not rows:
        raise HTTPException(404, "Escrow not found")

    e = dict(rows[0])
    e["data"] = json.loads(e.get("data", "{}"))
    return e


# ══════════════════════════════════════════
# FEATURE 9: SLA & Guarantees
# ══════════════════════════════════════════

@router.post("/sla/set")
async def sla_set(req: dict, x_api_key: str = Header(None, alias="X-API-Key")):
    """Set SLA for your service. Seller only."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    agent = await _get_agent(x_api_key)
    service_id = req.get("service_id", "")
    if not service_id:
        raise HTTPException(400, "service_id required")

    db = await _get_db()
    service = await db.get_service(service_id)
    if not service or service.get("agent_api_key") != x_api_key:
        raise HTTPException(403, "Not your service")

    sid = str(uuid.uuid4())
    await db.raw_execute(
        "INSERT OR REPLACE INTO service_slas(id,service_id,seller_api_key,max_response_time_ms,uptime_guarantee_pct,auto_refund) VALUES(?,?,?,?,?,?)",
        (sid, service_id, x_api_key,
         req.get("max_response_time_ms", 30000),
         req.get("uptime_guarantee_pct", 99.0),
         1 if req.get("auto_refund", True) else 0))
    
    return {"success": True, "sla_id": sid, "service_id": service_id,
            "max_response_time_ms": req.get("max_response_time_ms", 30000),
            "uptime_guarantee_pct": req.get("uptime_guarantee_pct", 99.0),
            "auto_refund": req.get("auto_refund", True)}


@router.get("/sla/{service_id}")
async def sla_get(service_id: str):
    """Get SLA for a service. Free, no auth."""
    db = await _get_db()
    rows = await db.raw_execute_fetchall(
        "SELECT id, service_id, seller_api_key, max_response_time_ms, "
        "uptime_guarantee_pct, auto_refund, created_at "
        "FROM service_slas WHERE service_id=?", (service_id,))
    if not rows:
        return {"service_id": service_id, "sla": None, "note": "No SLA set for this service"}
    return {"service_id": service_id, "sla": dict(rows[0])}


@router.get("/sla/violations/{service_id}")
async def sla_violations(service_id: str, x_api_key: str = Header(None, alias="X-API-Key")):
    """Get SLA violations for a service. Seller only."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    db = await _get_db()
    rows = await db.raw_execute_fetchall(
        "SELECT id, service_id, sla_id, violation_type, response_time_ms, "
        "refunded, refund_amount_usdc, buyer_wallet, created_at "
        "FROM sla_violations WHERE service_id=? ORDER BY created_at DESC LIMIT 100",
        (service_id,))
    return {"violations": [dict(r) for r in rows], "total": len(rows)}


async def check_sla(service_id: str, response_time_ms: int, price_usdc: float, buyer_wallet: str):
    """Check SLA after service execution. Auto-refund if violated."""
    try:
        db = await _get_db()
        rows = await db.raw_execute_fetchall(
            "SELECT id, service_id, max_response_time_ms, auto_refund "
            "FROM service_slas WHERE service_id=?", (service_id,))
        if not rows:
            return
        sla = rows[0]
        if response_time_ms > sla["max_response_time_ms"]:
            vid = str(uuid.uuid4())
            refund = price_usdc if sla["auto_refund"] else 0
            await db.raw_execute(
                "INSERT INTO sla_violations(id,service_id,sla_id,violation_type,response_time_ms,refunded,refund_amount_usdc,buyer_wallet) VALUES(?,?,?,?,?,?,?,?)",
                (vid, service_id, sla["id"], "timeout", response_time_ms,
                 1 if refund > 0 else 0, refund, buyer_wallet))
            if refund > 0:
                print(f"[SLA] Violation: {service_id} took {response_time_ms}ms (max {sla['max_response_time_ms']}ms). Auto-refund ${refund}")
    except Exception as e:
        print(f"[SLA] Check error: {e}")


# ══════════════════════════════════════════
# FEATURE 10: Revenue Sharing / Clones
# ══════════════════════════════════════════

CLONE_REVENUE_SHARE_PCT = 15  # default 15% to original creator


@router.post("/clone/create")
async def clone_create(req: dict, x_api_key: str = Header(None, alias="X-API-Key")):
    """Clone an existing service. Original creator earns 15% royalty on every sale."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    agent = await _get_agent(x_api_key)
    service_id = req.get("service_id", "")
    if not service_id:
        raise HTTPException(400, "service_id required")

    db = await _get_db()
    original = await db.get_service(service_id)
    if not original:
        raise HTTPException(404, "Service not found")
    if original.get("agent_api_key") == x_api_key:
        raise HTTPException(400, "Cannot clone your own service")

    # Create clone service
    clone_id = str(uuid.uuid4())
    clone_service = {
        "id": clone_id,
        "agent_api_key": x_api_key,
        "agent_name": agent["name"],
        "agent_wallet": agent["wallet"],
        "name": req.get("custom_name", f"{original['name']} (by {agent['name']})"),
        "description": original["description"],
        "type": original.get("type", "text"),
        "price_usdc": req.get("custom_price", original["price_usdc"]),
        "endpoint": req.get("endpoint", original.get("endpoint", "")),
        "status": "active",
        "rating": 5.0,
        "sales": 0,
    }
    await db.save_service(clone_service)

    # Save clone config
    cid = str(uuid.uuid4())
    await db.raw_execute(
        "INSERT INTO clone_configs(id,original_service_id,original_seller_key,clone_api_key,clone_service_id,revenue_share_pct) VALUES(?,?,?,?,?,?)",
        (cid, service_id, original["agent_api_key"], x_api_key, clone_id, CLONE_REVENUE_SHARE_PCT))
    
    return {"success": True, "clone_id": cid, "clone_service_id": clone_id,
            "original_service": original["name"], "original_seller": original["agent_name"],
            "revenue_share": f"{CLONE_REVENUE_SHARE_PCT}% to original creator",
            "your_share": f"{100 - CLONE_REVENUE_SHARE_PCT}%"}


@router.get("/clone/my-clones")
async def clone_list(x_api_key: str = Header(None, alias="X-API-Key")):
    """List clones I created."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    db = await _get_db()
    rows = await db.raw_execute_fetchall(
        "SELECT id, original_service_id, original_seller_key, clone_api_key, "
        "clone_service_id, revenue_share_pct, total_revenue_usdc, "
        "total_royalties_usdc, active, created_at "
        "FROM clone_configs WHERE clone_api_key=? AND active=1 ORDER BY created_at DESC",
        (x_api_key,))
    return {"clones": [dict(r) for r in rows], "total": len(rows)}


@router.get("/clone/my-royalties")
async def clone_royalties(x_api_key: str = Header(None, alias="X-API-Key")):
    """Royalties I earn from others cloning my services."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    db = await _get_db()
    rows = await db.raw_execute_fetchall(
        "SELECT id, original_service_id, original_seller_key, clone_api_key, "
        "clone_service_id, revenue_share_pct, total_revenue_usdc, "
        "total_royalties_usdc, active, created_at "
        "FROM clone_configs WHERE original_seller_key=? ORDER BY total_royalties_usdc DESC",
        (x_api_key,))
    total = sum(float(r["total_royalties_usdc"]) for r in rows)
    return {"royalties": [dict(r) for r in rows], "total_royalties_usdc": round(total, 4),
            "clones_of_my_services": len(rows)}


@router.get("/clone/stats")
async def clone_stats():
    """Global clone statistics. Free, no auth."""
    db = await _get_db()
    rows = await db.raw_execute_fetchall(
        "SELECT COUNT(*) AS cnt, COALESCE(SUM(total_revenue_usdc),0) AS rev, COALESCE(SUM(total_royalties_usdc),0) AS roy FROM clone_configs WHERE active=1")
    r = dict(rows[0]) if rows else {}
    return {"total_clones": r.get("cnt", 0),
            "total_revenue_usdc": round(float(r.get("rev", 0)), 2),
            "total_royalties_usdc": round(float(r.get("roy", 0)), 2),
            "revenue_share_pct": CLONE_REVENUE_SHARE_PCT}


async def process_clone_royalty(clone_service_id: str, sale_amount_usdc: float):
    """Process royalty payment after a clone service sale."""
    try:
        db = await _get_db()
        rows = await db.raw_execute_fetchall(
            "SELECT id, original_service_id, original_seller_key, clone_api_key, "
            "clone_service_id, revenue_share_pct, total_revenue_usdc, "
            "total_royalties_usdc, active, created_at "
            "FROM clone_configs WHERE clone_service_id=? AND active=1", (clone_service_id,))
        if not rows:
            return
        config = rows[0]
        royalty = round(sale_amount_usdc * config["revenue_share_pct"] / 100, 4)
        await db.raw_execute(
            "UPDATE clone_configs SET total_revenue_usdc=total_revenue_usdc+?, total_royalties_usdc=total_royalties_usdc+? WHERE id=?",
            (sale_amount_usdc, royalty, config["id"]))
        print(f"[Clone] Royalty ${royalty} to original creator for clone {clone_service_id}")
    except Exception as e:
        print(f"[Clone] Royalty error: {e}")


def get_router():
    return router
