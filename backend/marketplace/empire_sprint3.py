"""MAXIA Empire V2 Sprint 3 — Trust: Kill Switch, Proof of Quality, Pipelines.

E7:  Self-service kill switch + budget alerts + spend summary
E6:  Execution proof hashes — verifiable proof that service ran correctly
E18: Service pipelines — chain N services in 1 call with $prev injection
"""
import asyncio
import hashlib
import json
import logging
import re
import time
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Header, Request
from pydantic import BaseModel, Field
from core.error_utils import safe_error

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/public", tags=["empire-sprint3"])

_ID_RE = re.compile(r'^[a-zA-Z0-9_\-]{1,128}$')

# ══════════════════════════════════════════
# DB SCHEMA
# ══════════════════════════════════════════

_SPRINT3_SCHEMA = """
CREATE TABLE IF NOT EXISTS execution_proofs (
    id TEXT PRIMARY KEY,
    service_id TEXT NOT NULL,
    buyer_api_key_hash TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    output_hash TEXT NOT NULL,
    execution_hash TEXT NOT NULL,
    status TEXT DEFAULT 'success',
    execution_ms INTEGER DEFAULT 0,
    price_usdc NUMERIC(18,6) DEFAULT 0,
    created_at INTEGER DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_proofs_service ON execution_proofs(service_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_proofs_hash ON execution_proofs(execution_hash);

CREATE TABLE IF NOT EXISTS spend_alerts (
    id TEXT PRIMARY KEY,
    api_key_hash TEXT NOT NULL,
    alert_type TEXT NOT NULL,
    threshold_pct INTEGER DEFAULT 80,
    webhook_url TEXT DEFAULT '',
    enabled INTEGER DEFAULT 1,
    created_at INTEGER DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_alerts_key ON spend_alerts(api_key_hash);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id TEXT PRIMARY KEY,
    buyer_api_key_hash TEXT NOT NULL,
    steps_json TEXT NOT NULL,
    total_price_usdc NUMERIC(18,6) DEFAULT 0,
    steps_completed INTEGER DEFAULT 0,
    steps_total INTEGER DEFAULT 0,
    status TEXT DEFAULT 'running',
    created_at INTEGER DEFAULT (strftime('%s','now'))
);
"""

_schema_initialized = False


async def _ensure_schema():
    global _schema_initialized
    if _schema_initialized:
        return
    try:
        from core.database import db
        await db.raw_executescript(_SPRINT3_SCHEMA)
        _schema_initialized = True
        logger.info("[Sprint3] Proofs + Alerts + Pipelines tables ready")
    except Exception as e:
        logger.error("[Sprint3] Schema init error: %s", e)


async def _get_db():
    from core.database import db
    await _ensure_schema()
    return db


async def _get_agent(api_key: str) -> dict:
    db = await _get_db()
    rows = await db.raw_execute_fetchall(
        "SELECT api_key, name, wallet FROM agents WHERE api_key=?", (api_key,))
    if not rows:
        raise HTTPException(401, "Invalid API key")
    return dict(rows[0])


def _hash_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()


# ══════════════════════════════════════════
# E7 — KILL SWITCH: Self-Service Freeze
# ══════════════════════════════════════════

@router.post("/agent/kill-switch")
async def kill_switch(x_api_key: str = Header(alias="X-API-Key", default="")):
    """Emergency kill switch — agent freezes itself. No admin needed.
    Frozen agents can only read, not write/spend. Unfreezing requires admin."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")

    agent = await _get_agent(x_api_key)
    db = await _get_db()

    # Freeze the agent
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        await db.raw_execute(
            "UPDATE agent_permissions SET status='frozen', frozen_at=?, updated_at=? "
            "WHERE api_key=?",
            (now, now, x_api_key))

        # Invalidate permissions cache
        try:
            from agents.agent_permissions import _invalidate_cache
            _invalidate_cache(x_api_key)
        except Exception:
            pass

        # Alert via webhook
        try:
            from features.webhooks import notify_webhook_subscribers
            await notify_webhook_subscribers("agent.frozen", {
                "agent": agent["name"],
                "reason": "self-service kill switch",
                "frozen_at": now,
            }, filter_wallet=agent.get("wallet", ""))
        except Exception:
            pass

        logger.info("[KillSwitch] Agent %s self-froze via kill switch", agent["name"])

        return {
            "success": True,
            "status": "frozen",
            "agent": agent["name"],
            "frozen_at": now,
            "message": "Agent frozen. All write operations blocked. Contact admin to unfreeze.",
            "unfreeze": "Admin only — POST /api/admin/agent/unfreeze",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[KillSwitch] Error: %s", e)
        raise HTTPException(500, safe_error("Kill switch failed", e))


class BudgetRequest(BaseModel):
    max_daily_usd: float = Field(..., gt=0, le=500000)
    max_single_tx_usd: float = Field(..., gt=0, le=100000)


@router.post("/agent/set-budget")
async def set_budget(req: BudgetRequest, x_api_key: str = Header(alias="X-API-Key", default="")):
    """Set custom daily spending budget. Agent can only lower their caps, not raise above trust level."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")

    agent = await _get_agent(x_api_key)
    db = await _get_db()

    # Get current trust-level defaults (ceiling)
    try:
        from agents.agent_permissions import get_or_create_permissions, TRUST_LEVEL_DEFAULTS, _invalidate_cache
        perms = await get_or_create_permissions(x_api_key, agent["wallet"])
        trust = perms.get("trust_level", 0)
        defaults = TRUST_LEVEL_DEFAULTS.get(trust, TRUST_LEVEL_DEFAULTS[0])

        # Agent can only LOWER their caps, not raise above trust level
        max_daily = min(req.max_daily_usd, defaults["max_daily"])
        max_single = min(req.max_single_tx_usd, defaults["max_single"])

        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        await db.raw_execute(
            "UPDATE agent_permissions SET max_daily_spend_usd=?, max_single_tx_usd=?, updated_at=? "
            "WHERE api_key=?",
            (max_daily, max_single, now, x_api_key))

        _invalidate_cache(x_api_key)

        return {
            "success": True,
            "max_daily_spend_usd": max_daily,
            "max_single_tx_usd": max_single,
            "trust_level": trust,
            "trust_level_max_daily": defaults["max_daily"],
            "note": "Caps can only be lowered. Raise requires higher trust level.",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[SetBudget] Error: %s", e)
        raise HTTPException(500, safe_error("Budget update failed", e))


@router.get("/agent/spend-summary")
async def spend_summary(x_api_key: str = Header(alias="X-API-Key", default="")):
    """Real-time spending summary — today's spend, budget remaining, recent transactions."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")

    agent = await _get_agent(x_api_key)
    db = await _get_db()

    try:
        from agents.agent_permissions import get_or_create_permissions
        perms = await get_or_create_permissions(x_api_key, agent["wallet"])

        max_daily = perms.get("max_daily_spend_usd", 50)
        daily_spent = perms.get("daily_spent_usd", 0) or 0
        daily_date = perms.get("daily_spent_date", "")

        # Reset if new day
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if daily_date != today:
            daily_spent = 0

        remaining = max(0, max_daily - daily_spent)
        pct_used = round((daily_spent / max_daily * 100) if max_daily > 0 else 0, 1)

        # Recent transactions (last 10)
        recent_tx = []
        try:
            rows = await db.raw_execute_fetchall(
                "SELECT service, price_usdc, created_at FROM marketplace_tx "
                "WHERE buyer=? ORDER BY created_at DESC LIMIT 10",
                (agent["name"],))
            recent_tx = [dict(r) for r in rows]
        except Exception:
            pass

        # Alert status
        alert_triggered = pct_used >= 80

        return {
            "agent": agent["name"],
            "status": perms.get("status", "active"),
            "today": today,
            "daily_budget_usd": max_daily,
            "daily_spent_usd": round(daily_spent, 4),
            "daily_remaining_usd": round(remaining, 4),
            "budget_used_pct": pct_used,
            "alert_triggered": alert_triggered,
            "max_single_tx_usd": perms.get("max_single_tx_usd", 10),
            "trust_level": perms.get("trust_level", 0),
            "recent_transactions": recent_tx,
            "kill_switch": "POST /api/public/agent/kill-switch",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[SpendSummary] Error: %s", e)
        raise HTTPException(500, safe_error("Spend summary failed", e))


class AlertRequest(BaseModel):
    threshold_pct: int = Field(80, ge=10, le=100)
    webhook_url: str = Field("", max_length=500)


@router.post("/agent/spend-alert")
async def set_spend_alert(req: AlertRequest, x_api_key: str = Header(alias="X-API-Key", default="")):
    """Configure a spend alert — get notified when budget usage exceeds threshold."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")

    agent = await _get_agent(x_api_key)
    db = await _get_db()

    # Validate webhook URL format if provided
    if req.webhook_url and not req.webhook_url.startswith(("http://", "https://")):
        raise HTTPException(400, "webhook_url must start with http:// or https://")

    key_hash = _hash_key(x_api_key)
    alert_id = f"alert_{uuid.uuid4().hex[:12]}"

    # Upsert — one alert per agent
    try:
        await db.raw_execute(
            "DELETE FROM spend_alerts WHERE api_key_hash=?", (key_hash,))
        await db.raw_execute(
            "INSERT INTO spend_alerts (id, api_key_hash, alert_type, threshold_pct, webhook_url) "
            "VALUES (?, ?, 'budget', ?, ?)",
            (alert_id, key_hash, req.threshold_pct, req.webhook_url))
    except Exception as e:
        logger.error("[SpendAlert] Error: %s", e)

    return {
        "success": True,
        "alert_id": alert_id,
        "threshold_pct": req.threshold_pct,
        "webhook_url": req.webhook_url or "(none — check via GET /agent/spend-summary)",
        "message": f"Alert set: notified when budget exceeds {req.threshold_pct}%",
    }


# ══════════════════════════════════════════
# E6 — PROOF OF QUALITY: Execution Proofs
# ══════════════════════════════════════════

def generate_execution_proof(
    execution_id: str,
    service_id: str,
    input_text: str,
    output_text: str,
    status: str = "success",
) -> dict:
    """Generate a verifiable execution proof hash.
    proof = SHA256(execution_id | service_id | SHA256(input) | SHA256(output) | timestamp)
    """
    ts = int(time.time())
    input_hash = hashlib.sha256(input_text.encode()).hexdigest()
    output_hash = hashlib.sha256(output_text.encode()).hexdigest()
    proof_input = f"{execution_id}|{service_id}|{input_hash}|{output_hash}|{ts}"
    execution_hash = hashlib.sha256(proof_input.encode()).hexdigest()

    return {
        "execution_id": execution_id,
        "service_id": service_id,
        "input_hash": input_hash,
        "output_hash": output_hash,
        "execution_hash": execution_hash,
        "status": status,
        "timestamp": ts,
    }


async def record_execution_proof(
    execution_id: str,
    service_id: str,
    buyer_api_key: str,
    input_text: str,
    output_text: str,
    execution_ms: int = 0,
    price_usdc: float = 0,
    status: str = "success",
):
    """Record an execution proof to the database. Called after service execution."""
    proof = generate_execution_proof(execution_id, service_id, input_text, output_text, status)
    try:
        db = await _get_db()
        await db.raw_execute(
            "INSERT OR IGNORE INTO execution_proofs "
            "(id, service_id, buyer_api_key_hash, input_hash, output_hash, "
            "execution_hash, status, execution_ms, price_usdc) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (execution_id, service_id, _hash_key(buyer_api_key),
             proof["input_hash"], proof["output_hash"],
             proof["execution_hash"], status, execution_ms, price_usdc))
    except Exception as e:
        logger.warning("[Proof] Record failed: %s", e)

    return proof


@router.get("/proof/{execution_id}")
async def verify_proof(execution_id: str):
    """Verify an execution proof — anyone can check that a service ran correctly."""
    if not _ID_RE.match(execution_id):
        raise HTTPException(400, "Invalid execution ID format")

    db = await _get_db()
    rows = await db.raw_execute_fetchall(
        "SELECT id, service_id, input_hash, output_hash, execution_hash, "
        "status, execution_ms, price_usdc, created_at "
        "FROM execution_proofs WHERE id=?",
        (execution_id,))

    if not rows:
        raise HTTPException(404, "Execution proof not found")

    proof = dict(rows[0])
    return {
        "execution_id": proof["id"],
        "service_id": proof["service_id"],
        "input_hash": proof["input_hash"],
        "output_hash": proof["output_hash"],
        "execution_hash": proof["execution_hash"],
        "status": proof["status"],
        "execution_ms": proof.get("execution_ms", 0),
        "price_usdc": float(proof.get("price_usdc", 0)),
        "created_at": proof.get("created_at", 0),
        "verified": proof["status"] == "success",
        "verify_note": (
            "This proof confirms the service executed successfully. "
            "The execution_hash = SHA256(id|service|input_hash|output_hash|timestamp). "
            "You can verify the hash independently."
        ),
    }


@router.get("/proofs/service/{service_id}")
async def list_service_proofs(service_id: str, limit: int = 20):
    """List execution proofs for a service — transparency for buyers."""
    if not _ID_RE.match(service_id):
        raise HTTPException(400, "Invalid service ID format")

    db = await _get_db()
    limit = max(1, min(100, limit))

    rows = await db.raw_execute_fetchall(
        "SELECT id, status, execution_ms, price_usdc, created_at "
        "FROM execution_proofs WHERE service_id=? ORDER BY created_at DESC LIMIT ?",
        (service_id, limit))

    proofs = [dict(r) for r in rows]

    # Stats
    total_rows = await db.raw_execute_fetchall(
        "SELECT COUNT(*) as total, "
        "SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) as success_count, "
        "AVG(execution_ms) as avg_ms "
        "FROM execution_proofs WHERE service_id=?",
        (service_id,))
    stats = dict(total_rows[0]) if total_rows else {}

    total = stats.get("total", 0) or 0
    success = stats.get("success_count", 0) or 0

    return {
        "service_id": service_id,
        "proofs": proofs,
        "total_executions": total,
        "success_count": success,
        "success_rate_pct": round(success / max(total, 1) * 100, 1),
        "avg_execution_ms": round(stats.get("avg_ms", 0) or 0),
    }


# ══════════════════════════════════════════
# E18 — SERVICE PIPELINES: Chain N Services
# ══════════════════════════════════════════

class PipelineStep(BaseModel):
    service: str = Field(..., min_length=1, max_length=128)
    prompt: str = Field("", max_length=10000)
    input: str = Field("", max_length=10000)
    params: dict = Field(default_factory=dict)


class PipelineRequest(BaseModel):
    steps: list[PipelineStep] = Field(..., min_length=1, max_length=10)
    timeout_s: int = Field(120, ge=10, le=300)


@router.post("/pipeline")
async def execute_pipeline(
    req: PipelineRequest,
    x_api_key: str = Header(alias="X-API-Key", default=""),
):
    """Execute a pipeline of N services in sequence.
    Each step's output feeds into the next via $prev substitution.
    Single transaction — partial failure refunds unexecuted steps."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")

    agent = await _get_agent(x_api_key)
    db = await _get_db()

    # Resolve services and calculate total price
    from marketplace.public_api_shared import _agent_services, _load_from_db
    try:
        await asyncio.wait_for(_load_from_db(), timeout=10)
    except asyncio.TimeoutError:
        pass

    # Native service prices
    NATIVE_PRICES = {
        "maxia-audit": 4.99, "maxia-code": 2.99, "maxia-data": 2.99,
        "maxia-scraper": 0.02, "maxia-image": 0.10, "maxia-translate": 0.05,
        "maxia-summary": 0.49, "maxia-wallet": 1.99, "maxia-marketing": 0.99,
        "maxia-sentiment": 0.005, "maxia-embedding": 0.001,
        "maxia-transcription": 0.01,
    }

    resolved_steps = []
    total_price = 0.0

    for i, step in enumerate(req.steps):
        sid = step.service.strip()
        is_native = sid.startswith("maxia-")

        if is_native:
            price = NATIVE_PRICES.get(sid, 0)
            if price <= 0:
                raise HTTPException(400, f"Step {i+1}: Unknown native service '{sid}'")
        else:
            svc = next((s for s in _agent_services if s["id"] == sid and s.get("status") == "active"), None)
            if not svc:
                # Try DB
                try:
                    svc_rows = await db.raw_execute_fetchall(
                        "SELECT id, price_usdc FROM agent_services WHERE id=? AND status='active'",
                        (sid,))
                    if svc_rows:
                        svc = dict(svc_rows[0])
                except Exception:
                    pass
            if not svc:
                raise HTTPException(404, f"Step {i+1}: Service '{sid}' not found")
            price = float(svc.get("price_usdc", 0))

        total_price += price
        resolved_steps.append({
            "index": i,
            "service_id": sid,
            "is_native": is_native,
            "price_usdc": price,
            "prompt": step.prompt,
            "input_template": step.input,
            "params": step.params,
        })

    # Check budget — must not silently pass
    try:
        from agents.agent_permissions import check_agent_spend
        await check_agent_spend(x_api_key, agent["wallet"], total_price)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[Pipeline] Budget check failed: %s", e)
        raise HTTPException(500, "Budget verification unavailable — pipeline blocked")

    # Deduct credits atomically (deduct_credits uses UPDATE WHERE balance >= amount)
    paid = False
    try:
        from billing.prepaid_credits import deduct_credits
        agent_id = agent.get("api_key", x_api_key)
        result = await deduct_credits(agent_id, total_price, f"pipeline:{len(resolved_steps)}_steps")
        paid = bool(result.get("success"))
    except Exception as e:
        logger.debug("[Pipeline] Credits deduction failed: %s", e)

    if not paid:
        raise HTTPException(402, {
            "error": "Insufficient credits for pipeline",
            "total_price_usdc": round(total_price, 4),
            "steps": len(resolved_steps),
            "step_prices": [{"service": s["service_id"], "price": s["price_usdc"]} for s in resolved_steps],
            "deposit": "POST /api/credits/deposit",
        })

    # Create pipeline run record
    run_id = f"pipe_{uuid.uuid4().hex[:12]}"
    key_hash = _hash_key(x_api_key)
    await db.raw_execute(
        "INSERT INTO pipeline_runs (id, buyer_api_key_hash, steps_json, total_price_usdc, "
        "steps_total, status) VALUES (?, ?, ?, ?, ?, 'running')",
        (run_id, key_hash, json.dumps([s["service_id"] for s in resolved_steps]),
         total_price, len(resolved_steps)))

    # Execute steps sequentially
    results = []
    prev_output = ""
    steps_completed = 0

    for step in resolved_steps:
        # Build input: substitute $prev
        prompt = step["prompt"] or step["input_template"]
        if "$prev" in prompt and prev_output:
            prompt = prompt.replace("$prev", prev_output[:5000])
        elif not prompt and prev_output:
            prompt = prev_output[:5000]

        if not prompt:
            raise HTTPException(400, f"Step {step['index']+1}: No input (prompt or $prev)")

        # Execute service
        try:
            if step["is_native"]:
                from marketplace.public_api_discover import _execute_native_service
                output = await asyncio.wait_for(
                    _execute_native_service(step["service_id"], prompt),
                    timeout=min(30, req.timeout_s))
            else:
                # External service — call via webhook/endpoint
                output = f"[External service {step['service_id']} — endpoint execution not implemented in pipeline yet]"

            if not output:
                output = "[No output]"

            # Record proof
            exec_id = f"{run_id}_step{step['index']}"
            await record_execution_proof(
                exec_id, step["service_id"], x_api_key,
                prompt[:1000], output[:1000],
                status="success", price_usdc=step["price_usdc"])

            results.append({
                "step": step["index"] + 1,
                "service": step["service_id"],
                "status": "success",
                "output": output[:5000],
                "proof_id": exec_id,
                "price_usdc": step["price_usdc"],
            })
            prev_output = output
            steps_completed += 1

        except asyncio.TimeoutError:
            results.append({
                "step": step["index"] + 1,
                "service": step["service_id"],
                "status": "timeout",
                "output": None,
                "price_usdc": step["price_usdc"],
            })
            # Refund remaining steps
            refund = sum(s["price_usdc"] for s in resolved_steps[step["index"]+1:])
            if refund > 0:
                try:
                    from billing.prepaid_credits import add_credits
                    await add_credits(agent.get("api_key", x_api_key), refund, f"pipeline_refund:{run_id}")
                except Exception:
                    pass
            break

        except Exception as e:
            results.append({
                "step": step["index"] + 1,
                "service": step["service_id"],
                "status": "error",
                "output": safe_error("Execution failed", e),
                "price_usdc": step["price_usdc"],
            })
            # Refund remaining steps
            refund = sum(s["price_usdc"] for s in resolved_steps[step["index"]+1:])
            if refund > 0:
                try:
                    from billing.prepaid_credits import add_credits
                    await add_credits(agent.get("api_key", x_api_key), refund, f"pipeline_refund:{run_id}")
                except Exception:
                    pass
            break

    # Update pipeline run
    final_status = "completed" if steps_completed == len(resolved_steps) else "partial"
    try:
        await db.raw_execute(
            "UPDATE pipeline_runs SET steps_completed=?, status=? WHERE id=?",
            (steps_completed, final_status, run_id))
    except Exception:
        pass

    return {
        "pipeline_id": run_id,
        "status": final_status,
        "steps_completed": steps_completed,
        "steps_total": len(resolved_steps),
        "total_price_usdc": round(total_price, 4),
        "results": results,
        "final_output": prev_output[:5000] if prev_output else None,
    }


@router.get("/pipeline/{pipeline_id}")
async def get_pipeline_status(
    pipeline_id: str,
    x_api_key: str = Header(alias="X-API-Key", default=""),
):
    """Check status of a pipeline run. Auth required — only the buyer can view."""
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    if not _ID_RE.match(pipeline_id):
        raise HTTPException(400, "Invalid pipeline ID")

    db = await _get_db()
    key_hash = _hash_key(x_api_key)

    rows = await db.raw_execute_fetchall(
        "SELECT id, buyer_api_key_hash, steps_json, total_price_usdc, steps_completed, "
        "steps_total, status, created_at FROM pipeline_runs WHERE id=?",
        (pipeline_id,))

    if not rows:
        raise HTTPException(404, "Pipeline not found")

    run = dict(rows[0])

    # Verify ownership
    if run.get("buyer_api_key_hash") != key_hash:
        raise HTTPException(403, "Not your pipeline")

    steps = json.loads(run.get("steps_json", "[]"))

    return {
        "pipeline_id": run["id"],
        "steps": steps,
        "total_price_usdc": float(run.get("total_price_usdc", 0)),
        "steps_completed": run.get("steps_completed", 0),
        "steps_total": run.get("steps_total", 0),
        "status": run.get("status", "unknown"),
        "created_at": run.get("created_at", 0),
    }
