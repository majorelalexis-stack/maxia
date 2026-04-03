"""MAXIA Governance Lite — Les agents votent sur les decisions produit.

Poids du vote proportionnel au volume trade (log10).
Pas de token de governance — la participation au marketplace suffit.
"""
import logging
import json
import math
import time
import uuid

from fastapi import APIRouter, HTTPException, Request, Query
from core.error_utils import safe_error

logger = logging.getLogger(__name__)

router = APIRouter(tags=["governance"])

_GOV_SCHEMA = """
CREATE TABLE IF NOT EXISTS governance_proposals (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    category TEXT DEFAULT 'feature',
    author TEXT NOT NULL,
    status TEXT DEFAULT 'active',
    options TEXT NOT NULL DEFAULT '[]',
    created_at INTEGER,
    closes_at INTEGER,
    result TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_gov_status ON governance_proposals(status);
CREATE INDEX IF NOT EXISTS idx_gov_closes ON governance_proposals(closes_at);

CREATE TABLE IF NOT EXISTS governance_votes (
    id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL,
    wallet TEXT NOT NULL,
    chosen_option TEXT NOT NULL,
    vote_weight REAL NOT NULL DEFAULT 1.0,
    created_at INTEGER,
    UNIQUE(proposal_id, wallet)
);
CREATE INDEX IF NOT EXISTS idx_gov_votes_proposal ON governance_votes(proposal_id);
"""

_schema_ready = False

GOV_CATEGORIES = ["feature", "chain", "pricing", "policy", "partnership"]


async def _ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    from core.database import db
    await db.raw_executescript(_GOV_SCHEMA)
    _schema_ready = True
    logger.info("[Governance] Schema pret")


def _compute_vote_weight(total_volume: float) -> float:
    """Calcule le poids de vote depuis le volume total.

    Volume $0 -> poids 1 (tout le monde peut voter)
    Volume $100 -> poids 3
    Volume $10,000 -> poids 5
    Volume $100,000 -> poids 6
    """
    return math.log10(max(total_volume, 1)) + 1


async def _read_body(request: Request) -> dict:
    raw = await request.body()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(400, "Invalid JSON body")


def _row_val(row, key, idx, default=None):
    """Extract value from DB row (dict or tuple/Row)."""
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[idx]
    except (IndexError, KeyError):
        return default


async def _get_results(db, proposal_id: str, options: list) -> dict:
    """Calcule les resultats en temps reel."""
    results = {opt: 0.0 for opt in options}
    total_weight = 0.0
    total_voters = 0

    rows = await db.raw_execute_fetchall(
        "SELECT chosen_option, vote_weight FROM governance_votes WHERE proposal_id=?",
        (proposal_id,))

    for r in rows:
        opt = _row_val(r, "chosen_option", 0, "")
        weight = float(_row_val(r, "vote_weight", 1, 1.0) or 1.0)
        if opt in results:
            results[opt] += weight
        total_weight += weight
        total_voters += 1

    # Compute percentages
    pcts = {}
    for opt, weight in results.items():
        pcts[opt] = round(weight / total_weight * 100, 1) if total_weight > 0 else 0

    return {
        "votes": results,
        "percentages": pcts,
        "total_weight": round(total_weight, 2),
        "total_voters": total_voters,
    }


# ── Public endpoints ──

@router.get("/api/public/governance")
async def governance_list(status: str = "active", limit: int = Query(default=20, ge=1, le=100)):
    """Liste des propositions (active par defaut)."""
    await _ensure_schema()
    from core.database import db

    if status not in ("active", "passed", "rejected", "executed", "all"):
        status = "active"

    try:
        if status == "all":
            rows = await db.raw_execute_fetchall(
                "SELECT id, title, description, category, author, status, options, created_at, closes_at, result "
                "FROM governance_proposals ORDER BY created_at DESC LIMIT ?", (limit,))
        else:
            rows = await db.raw_execute_fetchall(
                "SELECT id, title, description, category, author, status, options, created_at, closes_at, result "
                "FROM governance_proposals WHERE status=? ORDER BY created_at DESC LIMIT ?",
                (status, limit))

        proposals = []
        for r in rows:
            r = r if isinstance(r, dict) else dict(zip(
                ["id", "title", "description", "category", "author", "status",
                 "options", "created_at", "closes_at", "result"], r))
            try:
                r["options"] = json.loads(r.get("options", "[]"))
            except Exception:
                r["options"] = []
            try:
                r["result"] = json.loads(r.get("result", "{}"))
            except Exception:
                r["result"] = {}

            # Compute live results
            live = await _get_results(db, r["id"], r["options"])
            r["live_results"] = live
            proposals.append(r)

        return {"proposals": proposals, "total": len(proposals)}
    except Exception as e:
        logger.error("[Governance] list error: %s", e)
        raise HTTPException(500, "Internal error")


@router.get("/api/public/governance/{proposal_id}")
async def governance_detail(proposal_id: str):
    """Detail d'une proposition avec resultats temps reel."""
    await _ensure_schema()
    from core.database import db

    try:
        row = await db._fetchone(
            "SELECT id, title, description, category, author, status, options, created_at, closes_at, result "
            "FROM governance_proposals WHERE id=?", (proposal_id,))
        if not row:
            raise HTTPException(404, "Proposal not found")

        r = row if isinstance(row, dict) else dict(zip(
            ["id", "title", "description", "category", "author", "status",
             "options", "created_at", "closes_at", "result"], row))
        try:
            r["options"] = json.loads(r.get("options", "[]"))
        except Exception:
            r["options"] = []

        # Live results
        r["live_results"] = await _get_results(db, r["id"], r["options"])

        # Recent voters (anonymized)
        voters = await db.raw_execute_fetchall(
            "SELECT wallet, chosen_option, vote_weight, created_at FROM governance_votes "
            "WHERE proposal_id=? ORDER BY created_at DESC LIMIT 20", (proposal_id,))
        r["recent_voters"] = []
        for v in voters:
            w = str(_row_val(v, "wallet", 0, "") or "")
            r["recent_voters"].append({
                "wallet_short": f"{w[:4]}...{w[-4:]}" if len(w) > 8 else w,
                "chosen_option": _row_val(v, "chosen_option", 1, ""),
                "vote_weight": round(float(_row_val(v, "vote_weight", 2, 1.0) or 1.0), 2),
                "created_at": _row_val(v, "created_at", 3, 0),
            })

        return r
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[Governance] detail error: %s", e)
        raise HTTPException(500, "Internal error")


@router.post("/api/governance/vote")
async def governance_vote(request: Request):
    """Voter sur une proposition. Auth requise (wallet verifie par token)."""
    await _ensure_schema()
    from core.database import db

    body = await _read_body(request)
    proposal_id = (body.get("proposal_id", "") or "").strip()
    chosen_option = (body.get("option", "") or "").strip()

    if not proposal_id or not chosen_option:
        raise HTTPException(400, "proposal_id and option required")

    # Auth: require valid session token — prevents vote forgery
    from routes.forum_api import _get_auth_wallet
    wallet = _get_auth_wallet(request, "")
    if not wallet:
        # Fallback: accept body wallet if no token (for agents without sessions)
        wallet = (body.get("wallet", "") or "").strip()
        if not wallet:
            raise HTTPException(401, "Authentication required to vote (wallet or Bearer token)")

    # Check proposal exists and is active
    row = await db._fetchone(
        "SELECT status, options, closes_at FROM governance_proposals WHERE id=?", (proposal_id,))
    if not row:
        raise HTTPException(404, "Proposal not found")

    prop_status = _row_val(row, "status", 0, "")
    prop_options_raw = _row_val(row, "options", 1, "[]")
    prop_closes_at = _row_val(row, "closes_at", 2, None)

    if prop_status != "active":
        raise HTTPException(400, "Proposal is not active")

    # Check if expired
    if prop_closes_at and int(prop_closes_at) < int(time.time()):
        raise HTTPException(400, "Voting period has ended")

    # Check option is valid
    try:
        options = json.loads(prop_options_raw)
    except Exception:
        options = []
    if chosen_option not in options:
        raise HTTPException(400, f"Invalid option. Valid options: {options}")

    # Compute vote weight from volume
    vote_weight = 1.0
    try:
        vol_row = await db._fetchone(
            "SELECT total_volume FROM user_points WHERE wallet=?", (wallet,))
        if vol_row:
            vol = float(_row_val(vol_row, "total_volume", 0, 0) or 0)
            vote_weight = _compute_vote_weight(vol)
    except Exception:
        pass

    vote_id = f"gvote_{uuid.uuid4().hex[:12]}"
    now = int(time.time())

    try:
        await db.raw_execute(
            "INSERT INTO governance_votes (id, proposal_id, wallet, chosen_option, vote_weight, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (vote_id, proposal_id, wallet, chosen_option, round(vote_weight, 4), now))
    except Exception as e:
        # Handle race condition: UNIQUE constraint violation = already voted
        if "unique" in str(e).lower() or "duplicate" in str(e).lower():
            raise HTTPException(400, "Already voted on this proposal")
        logger.error("[Governance] vote DB error: %s", e)
        raise HTTPException(500, "Failed to record vote")

    return {
        "success": True,
        "vote_weight": round(vote_weight, 2),
        "option": chosen_option,
    }


# ── Admin endpoints (CEO auth) ──

@router.post("/api/admin/governance/create")
async def governance_create(request: Request):
    """Creer une proposition. Requiert CEO auth."""
    from core.auth import require_ceo_auth
    await require_ceo_auth(request, request.headers.get("X-CEO-Key"))

    await _ensure_schema()
    from core.database import db

    body = await _read_body(request)
    title = (body.get("title", "") or "").strip()[:200]
    description = (body.get("description", "") or "").strip()[:5000]
    options = body.get("options", [])

    if not title or not description:
        raise HTTPException(400, "title and description required")
    if not options or not isinstance(options, list) or len(options) < 2:
        raise HTTPException(400, "At least 2 options required")

    options = [str(o).strip()[:100] for o in options[:10]]
    category = body.get("category", "feature")
    if category not in GOV_CATEGORIES:
        category = "feature"

    # Duration in days (default 7)
    try:
        duration_days = min(max(int(body.get("duration_days", 7)), 1), 30)
    except (ValueError, TypeError):
        duration_days = 7

    now = int(time.time())
    proposal_id = f"gov_{uuid.uuid4().hex[:12]}"

    try:
        await db.raw_execute(
            "INSERT INTO governance_proposals (id, title, description, category, author, status, options, created_at, closes_at, result) "
            "VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, '{}')",
            (proposal_id, title, description, category,
             body.get("author", "MAXIA CEO"),
             json.dumps(options), now, now + duration_days * 86400))
    except Exception as e:
        logger.error("[Governance] create DB error: %s", e)
        raise HTTPException(500, "Failed to create proposal")

    return {"success": True, "id": proposal_id, "closes_at": now + duration_days * 86400}


@router.post("/api/admin/governance/close/{proposal_id}")
async def governance_close(proposal_id: str, request: Request):
    """Cloturer manuellement une proposition. Requiert CEO auth."""
    from core.auth import require_ceo_auth
    await require_ceo_auth(request, request.headers.get("X-CEO-Key"))

    await _ensure_schema()
    from core.database import db

    row = await db._fetchone(
        "SELECT options, status FROM governance_proposals WHERE id=?", (proposal_id,))
    if not row:
        raise HTTPException(404, "Proposal not found")

    prop_status = _row_val(row, "status", 1, "")
    prop_options_raw = _row_val(row, "options", 0, "[]")

    if prop_status != "active":
        raise HTTPException(400, "Proposal already closed")

    try:
        options = json.loads(prop_options_raw)
    except Exception:
        options = []

    results = await _get_results(db, proposal_id, options)

    # Determine winner — only if there are actual voters
    winner = None
    status = "rejected"
    if results["total_voters"] > 0:
        winner = max(results["votes"], key=results["votes"].get)
        status = "passed"

    try:
        await db.raw_execute(
            "UPDATE governance_proposals SET status=?, result=? WHERE id=?",
            (status, json.dumps({"winner": winner, **results}, default=str), proposal_id))
    except Exception as e:
        logger.error("[Governance] close DB error: %s", e)
        raise HTTPException(500, "Failed to close proposal")

    return {"success": True, "status": status, "winner": winner, "results": results}


# ── Scheduler task: auto-close expired proposals ──

async def auto_close_expired() -> int:
    """Auto-cloture les propositions expirees. Appele par scheduler."""
    await _ensure_schema()
    from core.database import db

    now = int(time.time())
    closed = 0

    try:
        rows = await db.raw_execute_fetchall(
            "SELECT id, options FROM governance_proposals WHERE status='active' AND closes_at < ?",
            (now,))

        for r in rows:
            prop_id = _row_val(r, "id", 0, "")
            prop_options_raw = _row_val(r, "options", 1, "[]")
            try:
                options = json.loads(prop_options_raw)
            except Exception:
                options = []

            results = await _get_results(db, prop_id, options)
            winner = None
            status = "rejected"
            if results["total_voters"] > 0:
                winner = max(results["votes"], key=results["votes"].get)
                status = "passed"

            await db.raw_execute(
                "UPDATE governance_proposals SET status=?, result=? WHERE id=?",
                (status, json.dumps({"winner": winner, **results}, default=str), prop_id))
            closed += 1

        if closed:
            logger.info("[Governance] Auto-cloture %d propositions", closed)
    except Exception as e:
        logger.error("[Governance] Erreur auto-close: %s", e)

    return closed
