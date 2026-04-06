"""MAXIA E16 — Reputation Oracle: queryable agent reputation feed.

Composite scores (0-100) from execution quality, staking, reviews, longevity,
and volume. Cached 10 min, history recorded hourly, SSE feed for changes >5 pts.
"""
import asyncio
import json
import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/oracle/reputation", tags=["reputation-oracle"])

ORACLE_VERSION = "1.0"
W_EXECUTION, W_STAKING, W_REVIEWS, W_LONGEVITY, W_VOLUME = 0.40, 0.20, 0.20, 0.10, 0.10

_score_cache: dict[str, tuple[dict[str, Any], float]] = {}
_CACHE_TTL_S = 600
_sse_queues: list[asyncio.Queue] = []
_MAX_SSE_CLIENTS = 30
_schema_ready = False

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reputation_history (
    id TEXT PRIMARY KEY, agent_id TEXT NOT NULL,
    score INTEGER NOT NULL, components_json TEXT, recorded_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rephist_agent ON reputation_history(agent_id, recorded_at DESC);
"""

# Reusable SQL: latest reputation per agent
_LATEST_JOIN = (
    "FROM reputation_history rh INNER JOIN ("
    "SELECT agent_id, MAX(recorded_at) AS max_ts FROM reputation_history GROUP BY agent_id"
    ") latest ON rh.agent_id = latest.agent_id AND rh.recorded_at = latest.max_ts"
)


async def _ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    try:
        from core.database import db
        await db.raw_executescript(_SCHEMA)
        _schema_ready = True
    except Exception as e:
        logger.error("[ReputationOracle] Schema error: %s", e)


async def _db():
    from core.database import db
    return db


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _norm(raw: float, ceiling: float) -> float:
    return _clamp((raw / ceiling) * 100.0) if ceiling > 0 else 0.0


# ── Core computation ──

async def compute_reputation(agent_id: str) -> dict[str, Any]:
    """Compute composite reputation score (0-100) for a single agent."""
    db = await _db()
    now = int(time.time())
    sources = 0

    # 1. Execution quality (40%) — agent_metrics or marketplace_tx fallback
    exec_s = 50.0
    try:
        rows = await db.raw_execute_fetchall(
            "SELECT COALESCE(SUM(success_count),0) AS ok, COALESCE(SUM(failure_count),0) AS fail "
            "FROM agent_metrics WHERE agent_id = ?", (agent_id,))
        if rows and (rows[0]["ok"] + rows[0]["fail"]) > 0:
            exec_s = (rows[0]["ok"] / (rows[0]["ok"] + rows[0]["fail"])) * 100.0
            sources += 1
        else:
            tx = await db.raw_execute_fetchall(
                "SELECT COUNT(*) AS cnt FROM marketplace_tx WHERE buyer=? OR seller=?",
                (agent_id, agent_id))
            if tx and tx[0]["cnt"] > 0:
                exec_s = min(90.0, 60.0 + tx[0]["cnt"] * 2.0)
                sources += 1
    except Exception:
        pass

    # 2. Staking (20%) — stakes table, 500 USDC = 100
    stake_s = 0.0
    try:
        rows = await db.raw_execute_fetchall(
            "SELECT data FROM stakes WHERE wallet=? ORDER BY created_at DESC LIMIT 1", (agent_id,))
        if rows:
            sd = json.loads(rows[0]["data"])
            if sd.get("status") == "active":
                stake_s = _norm(float(sd.get("amount", 0)), 500.0)
                sources += 1
    except Exception:
        pass

    # 3. Reviews (20%) — service_reviews avg rating mapped 1-5 -> 0-100
    rev_s = 50.0
    try:
        svcs = await db.raw_execute_fetchall(
            "SELECT id FROM agent_services WHERE agent_api_key=? OR agent_wallet=?",
            (agent_id, agent_id))
        if svcs:
            ph = ", ".join(["?"] * len(svcs))
            rv = await db.raw_execute_fetchall(
                f"SELECT AVG(rating) AS ar, COUNT(*) AS cnt FROM service_reviews WHERE service_id IN ({ph})",
                tuple(r["id"] for r in svcs))
            if rv and rv[0]["cnt"] and rv[0]["cnt"] > 0:
                rev_s = (float(rv[0]["ar"] or 3.0) - 1.0) / 4.0 * 100.0
                sources += 1
    except Exception:
        pass

    # 4. Longevity (10%) — account age, 365d = 100
    lon_s = 0.0
    try:
        rows = await db.raw_execute_fetchall(
            "SELECT created_at FROM agents WHERE api_key=? OR wallet=? LIMIT 1",
            (agent_id, agent_id))
        if rows and rows[0]["created_at"]:
            lon_s = _norm((now - int(rows[0]["created_at"])) / 86400.0, 365.0)
            sources += 1
    except Exception:
        pass

    # 5. Volume (10%) — total marketplace volume, 10000 USDC = 100
    vol_s = 0.0
    try:
        rows = await db.raw_execute_fetchall(
            "SELECT COALESCE(SUM(price_usdc),0) AS vol FROM marketplace_tx WHERE buyer=? OR seller=?",
            (agent_id, agent_id))
        if rows and rows[0]["vol"]:
            vol_s = _norm(float(rows[0]["vol"]), 10000.0)
            sources += 1
    except Exception:
        pass

    components = {
        "execution_quality": round(exec_s, 1), "staking": round(stake_s, 1),
        "reviews": round(rev_s, 1), "longevity": round(lon_s, 1), "volume": round(vol_s, 1),
    }
    composite = W_EXECUTION * exec_s + W_STAKING * stake_s + W_REVIEWS * rev_s + W_LONGEVITY * lon_s + W_VOLUME * vol_s
    score = int(round(_clamp(composite)))
    confidence = round(min(1.0, sources / 5.0 * 0.6 + 0.4), 2)

    result = {
        "agent_id": agent_id, "score": score, "confidence": confidence,
        "components": components, "last_updated": now,
        "data_sources": sources, "oracle_version": ORACLE_VERSION,
    }
    _score_cache[agent_id] = (result, time.time())
    return result


def _get_cached(agent_id: str) -> dict[str, Any] | None:
    entry = _score_cache.get(agent_id)
    if entry is None:
        return None
    result, ts = entry
    return result if time.time() - ts <= _CACHE_TTL_S else None


async def _broadcast_sse(event: dict[str, Any]) -> None:
    payload = json.dumps(event, default=str)
    dead: list[asyncio.Queue] = []
    for q in _sse_queues:
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        try:
            _sse_queues.remove(q)
        except ValueError:
            pass


# ── Background task (hourly) ──

async def record_reputation_history() -> int:
    """Record scores to history, broadcast significant changes (>5 pts)."""
    await _ensure_schema()
    db = await _db()
    now = int(time.time())
    recorded = 0

    try:
        agent_rows = await db.raw_execute_fetchall("SELECT api_key FROM agents LIMIT 500")
    except Exception as e:
        logger.error("[ReputationOracle] Cannot list agents: %s", e)
        return 0

    for row in agent_rows:
        aid = row["api_key"]
        try:
            data = await compute_reputation(aid)
            prev = await db.raw_execute_fetchall(
                "SELECT score FROM reputation_history WHERE agent_id=? ORDER BY recorded_at DESC LIMIT 1",
                (aid,))
            prev_score = prev[0]["score"] if prev else None

            await db.raw_execute(
                "INSERT INTO reputation_history (id,agent_id,score,components_json,recorded_at) VALUES (?,?,?,?,?)",
                (f"rh_{uuid.uuid4().hex[:12]}", aid, data["score"], json.dumps(data["components"]), now))
            recorded += 1

            if prev_score is not None and abs(data["score"] - prev_score) > 5:
                await _broadcast_sse({
                    "event": "reputation_change", "agent_id": aid,
                    "old_score": prev_score, "new_score": data["score"],
                    "delta": data["score"] - prev_score, "timestamp": now,
                })
        except Exception as e:
            logger.debug("[ReputationOracle] History failed for %s: %s", aid, e)

    logger.info("[ReputationOracle] Recorded %d reputation snapshots", recorded)
    return recorded


# ── Pydantic ──

class BatchRequest(BaseModel):
    agent_ids: list[str] = Field(..., min_length=1, max_length=100)


# ── Endpoints ──

@router.get("")
async def get_reputation(
    agent_id: str = Query(..., min_length=1, max_length=128, description="Agent ID or wallet"),
):
    """Get reputation score for a single agent (0-100)."""
    await _ensure_schema()
    cached = _get_cached(agent_id)
    if cached is not None:
        return cached
    return await compute_reputation(agent_id)


@router.post("/batch")
async def get_reputation_batch(req: BatchRequest):
    """Get reputation scores for multiple agents (max 100)."""
    await _ensure_schema()
    results: list[dict[str, Any]] = []
    for aid in req.agent_ids:
        aid = aid.strip()[:128]
        if not aid:
            continue
        cached = _get_cached(aid)
        if cached is not None:
            results.append(cached)
        else:
            try:
                results.append(await compute_reputation(aid))
            except Exception:
                results.append({"agent_id": aid, "score": 0, "confidence": 0.0,
                                "error": "computation_failed", "oracle_version": ORACLE_VERSION})
    return {"results": results, "count": len(results)}


@router.get("/feed")
async def reputation_feed(request: Request):
    """SSE stream of reputation changes. Heartbeat 30s. Max 30 clients."""
    await _ensure_schema()
    if len(_sse_queues) >= _MAX_SSE_CLIENTS:
        return {"error": "Too many SSE connections", "max": _MAX_SSE_CLIENTS}

    queue: asyncio.Queue = asyncio.Queue(maxsize=256)
    _sse_queues.append(queue)

    async def gen():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    yield f"data: {await asyncio.wait_for(queue.get(), timeout=30.0)}\n\n"
                except asyncio.TimeoutError:
                    yield f": heartbeat {int(time.time())}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            try:
                _sse_queues.remove(queue)
            except ValueError:
                pass

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})


@router.get("/leaderboard")
async def reputation_leaderboard(
    limit: int = Query(20, ge=1, le=100, description="Max agents to return"),
):
    """Top agents ranked by reputation score."""
    await _ensure_schema()
    db = await _db()

    rows = await db.raw_execute_fetchall(
        f"SELECT rh.agent_id, rh.score, rh.components_json, rh.recorded_at {_LATEST_JOIN} "
        f"ORDER BY rh.score DESC LIMIT ?", (limit,))

    leaderboard = []
    for i, row in enumerate(rows):
        try:
            comp = json.loads(row["components_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            comp = {}
        leaderboard.append({"rank": i + 1, "agent_id": row["agent_id"],
                            "score": row["score"], "components": comp,
                            "last_updated": row["recorded_at"]})

    return {"leaderboard": leaderboard, "count": len(leaderboard), "oracle_version": ORACLE_VERSION}


@router.get("/history")
async def reputation_history_endpoint(
    agent_id: str = Query(..., min_length=1, max_length=128),
    limit: int = Query(30, ge=1, le=365, description="Max history entries"),
):
    """Score history over time for a specific agent."""
    await _ensure_schema()
    db = await _db()

    rows = await db.raw_execute_fetchall(
        "SELECT score, components_json, recorded_at FROM reputation_history "
        "WHERE agent_id=? ORDER BY recorded_at DESC LIMIT ?", (agent_id, limit))

    history = []
    for row in rows:
        try:
            comp = json.loads(row["components_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            comp = {}
        history.append({"score": row["score"], "components": comp, "recorded_at": row["recorded_at"]})

    trend = "stable"
    if len(history) >= 2:
        d = history[0]["score"] - history[-1]["score"]
        trend = "improving" if d > 3 else ("declining" if d < -3 else "stable")

    return {"agent_id": agent_id, "history": history, "count": len(history),
            "trend": trend, "oracle_version": ORACLE_VERSION}


@router.get("/stats")
async def reputation_stats():
    """Global reputation statistics."""
    await _ensure_schema()
    db = await _db()

    total_agents = 0
    try:
        rows = await db.raw_execute_fetchall("SELECT COUNT(*) AS cnt FROM agents")
        total_agents = rows[0]["cnt"] if rows else 0
    except Exception:
        pass

    avg_score, scored_agents = 0, 0
    try:
        rows = await db.raw_execute_fetchall(
            f"SELECT AVG(rh.score) AS avg_s, COUNT(*) AS cnt {_LATEST_JOIN}")
        if rows and rows[0]["cnt"]:
            avg_score = round(float(rows[0]["avg_s"] or 0))
            scored_agents = int(rows[0]["cnt"])
    except Exception:
        pass

    total_staked = 0.0
    try:
        from infra.reputation_staking import reputation_staking
        st = await reputation_staking.get_stats()
        total_staked = st.get("total_staked_usdc", 0.0)
    except Exception:
        pass

    distribution = {"excellent": 0, "good": 0, "average": 0, "poor": 0}
    try:
        dist = await db.raw_execute_fetchall(f"SELECT rh.score {_LATEST_JOIN}")
        for r in dist:
            s = r["score"]
            if s >= 80:
                distribution["excellent"] += 1
            elif s >= 60:
                distribution["good"] += 1
            elif s >= 40:
                distribution["average"] += 1
            else:
                distribution["poor"] += 1
    except Exception:
        pass

    return {"total_agents": total_agents, "scored_agents": scored_agents,
            "avg_score": avg_score, "total_staked_usdc": round(total_staked, 2),
            "distribution": distribution, "cache_ttl_s": _CACHE_TTL_S,
            "sse_clients": len(_sse_queues), "oracle_version": ORACLE_VERSION}
