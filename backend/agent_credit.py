"""MAXIA Agent Credit V12 — Systeme de credit pour agents IA sur le marketplace

Permet aux agents IA d'emprunter du USDC en fonction de leur reputation :
- Score de credit 0-1000 base sur 5 criteres ponderes
- Lignes de credit automatiques selon le score
- Remboursement tracke avec signature de transaction
- Integration avec le leaderboard et les disputes existants

Score breakdown :
- Volume de trades (30j) — 25%
- Taux de succes — 25%
- Anciennete du compte — 15%
- Disputes (moins = mieux) — 15%
- Score de reputation (leaderboard grade) — 20%

Tables DB : agent_credits (credit lines), credit_transactions (historique)
"""
import time, uuid, json
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Request, Depends
from pydantic import BaseModel
from auth import require_auth

router = APIRouter(prefix="/api/credit", tags=["agent-credit"])

# ── Schema DB (creation lazy) ──

_schema_created = False

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS agent_credits (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL UNIQUE,
    score INTEGER DEFAULT 0,
    credit_limit REAL DEFAULT 0,
    borrowed REAL DEFAULT 0,
    repaid REAL DEFAULT 0,
    status TEXT DEFAULT 'active',
    last_scored_at INTEGER DEFAULT 0,
    created_at INTEGER DEFAULT (strftime('%s','now')),
    updated_at INTEGER DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_credits_agent ON agent_credits(agent_id);
CREATE TABLE IF NOT EXISTS credit_transactions (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    type TEXT NOT NULL,
    amount_usdc REAL NOT NULL,
    tx_signature TEXT DEFAULT '',
    balance_after REAL DEFAULT 0,
    created_at INTEGER DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_credit_tx_agent ON credit_transactions(agent_id, created_at);
"""


async def _ensure_schema():
    """Cree les tables agent_credits et credit_transactions si elles n'existent pas."""
    global _schema_created
    if _schema_created:
        return
    try:
        from database import db
        await db.raw_executescript(_SCHEMA_SQL)
        _schema_created = True
    except Exception as e:
        print(f"[Credit] Erreur schema: {e}")


# ── Limites de credit par tranche de score ──

CREDIT_TIERS = [
    {"min_score": 900, "max_score": 1000, "limit_usdc": 5000},
    {"min_score": 700, "max_score": 899,  "limit_usdc": 2000},
    {"min_score": 500, "max_score": 699,  "limit_usdc": 500},
    {"min_score": 300, "max_score": 499,  "limit_usdc": 100},
    {"min_score": 0,   "max_score": 299,  "limit_usdc": 0},
]


def get_credit_limit(score: int) -> float:
    """Retourne la limite de credit USDC pour un score donne.

    score 0-299   → $0 (pas de credit)
    score 300-499 → $100
    score 500-699 → $500
    score 700-899 → $2000
    score 900+    → $5000
    """
    for tier in CREDIT_TIERS:
        if tier["min_score"] <= score <= tier["max_score"]:
            return tier["limit_usdc"]
    return 0.0


def _get_credit_tier_name(score: int) -> str:
    """Retourne le nom du tier de credit pour un score."""
    if score >= 900:
        return "PLATINUM"
    elif score >= 700:
        return "GOLD"
    elif score >= 500:
        return "SILVER"
    elif score >= 300:
        return "BRONZE"
    return "NONE"


# ── Calcul du score de credit ──

async def calculate_credit_score(agent_id: str) -> int:
    """Calcule le score de credit (0-1000) d'un agent depuis les donnees DB.

    Composantes :
    - trade_volume_30d (25%) : volume USDC sur 30 jours, max a 250 pts pour $50K+
    - success_rate (25%) : taux de succes des executions, max 250 pts
    - account_age (15%) : anciennete en jours, max 150 pts pour 180j+
    - disputes (15%) : moins de disputes = mieux, max 150 pts
    - reputation (20%) : score du leaderboard, max 200 pts
    """
    from database import db
    await _ensure_schema()

    score_components = {
        "trade_volume": 0,
        "success_rate": 0,
        "account_age": 0,
        "disputes": 0,
        "reputation": 0,
    }

    # ── 1. Volume de trades (30j) — 25% (max 250 pts) ──
    try:
        thirty_days_ago = int(time.time()) - (30 * 86400)
        # Checker marketplace_tx pour le volume en tant que buyer ou seller
        vol_rows = await db.raw_execute_fetchall(
            "SELECT COALESCE(SUM(price_usdc), 0) as vol FROM marketplace_tx "
            "WHERE (buyer=? OR seller=?) AND created_at>=?",
            (agent_id, agent_id, thirty_days_ago)
        )
        volume = 0.0
        if vol_rows:
            v = vol_rows[0]
            volume = float(v[0] if isinstance(v, (list, tuple)) else v.get("vol", 0) or 0)

        # Ajouter les swaps si disponibles
        try:
            swap_rows = await db.raw_execute_fetchall(
                "SELECT COALESCE(SUM(amount_in), 0) as vol FROM crypto_swaps "
                "WHERE buyer_wallet=? AND created_at>=?",
                (agent_id, thirty_days_ago)
            )
            if swap_rows:
                sv = swap_rows[0]
                volume += float(sv[0] if isinstance(sv, (list, tuple)) else sv.get("vol", 0) or 0)
        except Exception:
            pass

        # Score: 0 a $50K → 0 a 250 pts (lineaire)
        score_components["trade_volume"] = min(250, int((volume / 50000) * 250))
    except Exception:
        pass

    # ── 2. Taux de succes — 25% (max 250 pts) ──
    try:
        # Utiliser agent_events si disponible (agent_analytics)
        try:
            exec_rows = await db.raw_execute_fetchall(
                "SELECT event_type, COUNT(*) as cnt FROM agent_events "
                "WHERE agent_id=? AND event_type IN ('service_executed', 'service_failed') "
                "GROUP BY event_type",
                (agent_id,)
            )
            executed = 0
            failed = 0
            for r in exec_rows:
                etype = r[0] if isinstance(r, (list, tuple)) else r.get("event_type", "")
                cnt = int(r[1] if isinstance(r, (list, tuple)) else r.get("cnt", 0))
                if etype == "service_executed":
                    executed = cnt
                elif etype == "service_failed":
                    failed = cnt
            total = executed + failed
            if total > 0:
                rate = executed / total
                score_components["success_rate"] = int(rate * 250)
            else:
                # Pas de donnees — score neutre (pas penalisant)
                score_components["success_rate"] = 125
        except Exception:
            # Table agent_events n'existe pas encore — score neutre
            score_components["success_rate"] = 125
    except Exception:
        score_components["success_rate"] = 125

    # ── 3. Anciennete du compte — 15% (max 150 pts) ──
    try:
        agent_rows = await db.raw_execute_fetchall(
            "SELECT created_at FROM agents WHERE api_key=? OR wallet=?",
            (agent_id, agent_id)
        )
        if agent_rows:
            row = agent_rows[0]
            created_at = int(row[0] if isinstance(row, (list, tuple)) else row.get("created_at", 0) or 0)
            age_days = (time.time() - created_at) / 86400 if created_at > 0 else 0
            # Max 150 pts a 180 jours (6 mois)
            score_components["account_age"] = min(150, int((age_days / 180) * 150))
    except Exception:
        pass

    # ── 4. Disputes (moins = mieux) — 15% (max 150 pts) ──
    try:
        dispute_rows = await db.raw_execute_fetchall(
            "SELECT data FROM disputes"
        )
        disputes_against = 0
        disputes_total = 0
        for r in dispute_rows:
            raw = r[0] if isinstance(r, (list, tuple)) else r.get("data", "{}")
            try:
                d = json.loads(raw) if isinstance(raw, str) else raw
                if agent_id in json.dumps(d):
                    disputes_total += 1
                    resolution = d.get("resolution", "")
                    if resolution == "refund":
                        disputes_against += 1
            except Exception:
                pass

        # 0 disputes perdues = 150 pts, chaque dispute perdue = -30 pts
        score_components["disputes"] = max(0, 150 - (disputes_against * 30))
    except Exception:
        # Pas de disputes = score parfait
        score_components["disputes"] = 150

    # ── 5. Reputation (leaderboard) — 20% (max 200 pts) ──
    try:
        # Checker agent_scores du leaderboard
        rep_rows = await db.raw_execute_fetchall(
            "SELECT uptime_score FROM agent_scores WHERE agent_id=?",
            (agent_id,)
        )
        if rep_rows:
            row = rep_rows[0]
            uptime = float(row[0] if isinstance(row, (list, tuple)) else row.get("uptime_score", 0) or 0)
            score_components["reputation"] = int(uptime * 200)
        else:
            # Pas de donnees leaderboard — score neutre
            score_components["reputation"] = 100
    except Exception:
        score_components["reputation"] = 100

    # ── Score total ──
    total_score = sum(score_components.values())
    total_score = max(0, min(1000, total_score))

    return total_score


# ── Operations de credit ──

async def request_credit(agent_id: str, amount_usdc: float) -> dict:
    """Demande une ligne de credit pour un agent.

    Verifie le score, calcule la limite, et accorde le credit si eligible.
    Retourne un dict avec le statut et les details du credit.
    """
    from database import db
    await _ensure_schema()

    if amount_usdc <= 0:
        raise HTTPException(400, "Le montant doit etre positif")
    if amount_usdc > 5000:
        raise HTTPException(400, "Montant maximum: $5000 USDC")

    # Calculer le score
    score = await calculate_credit_score(agent_id)
    limit = get_credit_limit(score)

    if limit <= 0:
        raise HTTPException(403, f"Score insuffisant ({score}/1000). Minimum 300 pour un credit.")

    # Verifier le credit existant
    existing = await db.raw_execute_fetchall(
        "SELECT borrowed, repaid, status FROM agent_credits WHERE agent_id=?",
        (agent_id,)
    )

    current_debt = 0.0
    if existing:
        row = existing[0]
        borrowed = float(row[0] if isinstance(row, (list, tuple)) else row.get("borrowed", 0) or 0)
        repaid = float(row[1] if isinstance(row, (list, tuple)) else row.get("repaid", 0) or 0)
        status = row[2] if isinstance(row, (list, tuple)) else row.get("status", "active")

        if status == "defaulted":
            raise HTTPException(403, "Compte en defaut de paiement. Remboursez d'abord votre dette.")

        current_debt = borrowed - repaid

    available = limit - current_debt
    if amount_usdc > available:
        raise HTTPException(400,
            f"Credit disponible insuffisant: ${available:.2f} "
            f"(limite ${limit:.2f}, dette ${current_debt:.2f})")

    # Accorder le credit
    now_ts = int(time.time())
    credit_id = str(uuid.uuid4())

    if existing:
        await db.raw_execute(
            "UPDATE agent_credits SET score=?, credit_limit=?, borrowed=borrowed+?, "
            "last_scored_at=?, updated_at=? WHERE agent_id=?",
            (score, limit, amount_usdc, now_ts, now_ts, agent_id)
        )
    else:
        await db.raw_execute(
            "INSERT INTO agent_credits (id, agent_id, score, credit_limit, borrowed, "
            "repaid, status, last_scored_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 0, 'active', ?, ?, ?)",
            (credit_id, agent_id, score, limit, amount_usdc, now_ts, now_ts, now_ts)
        )

    # Enregistrer la transaction
    tx_id = str(uuid.uuid4())
    new_balance = current_debt + amount_usdc
    await db.raw_execute(
        "INSERT INTO credit_transactions (id, agent_id, type, amount_usdc, balance_after, created_at) "
        "VALUES (?, ?, 'borrow', ?, ?, ?)",
        (tx_id, agent_id, amount_usdc, new_balance, now_ts)
    )

    return {
        "status": "approved",
        "credit_id": credit_id if not existing else agent_id,
        "amount_usdc": amount_usdc,
        "score": score,
        "tier": _get_credit_tier_name(score),
        "credit_limit": limit,
        "total_borrowed": current_debt + amount_usdc,
        "available_credit": available - amount_usdc,
    }


async def repay_credit(agent_id: str, amount_usdc: float, tx_signature: str = "") -> dict:
    """Rembourse une partie ou la totalite du credit d'un agent.

    Necessite un montant positif et optionnellement une signature de transaction on-chain.
    """
    from database import db
    await _ensure_schema()

    if amount_usdc <= 0:
        raise HTTPException(400, "Le montant doit etre positif")

    # Verifier le credit existant
    rows = await db.raw_execute_fetchall(
        "SELECT borrowed, repaid, status FROM agent_credits WHERE agent_id=?",
        (agent_id,)
    )
    if not rows:
        raise HTTPException(404, "Aucun credit trouve pour cet agent")

    row = rows[0]
    borrowed = float(row[0] if isinstance(row, (list, tuple)) else row.get("borrowed", 0) or 0)
    repaid = float(row[1] if isinstance(row, (list, tuple)) else row.get("repaid", 0) or 0)
    current_debt = borrowed - repaid

    if current_debt <= 0:
        raise HTTPException(400, "Aucune dette a rembourser")

    # Limiter au montant de la dette
    actual_repay = min(amount_usdc, current_debt)
    now_ts = int(time.time())

    # Mettre a jour le credit
    new_debt = current_debt - actual_repay
    new_status = "active" if new_debt > 0 else "cleared"

    await db.raw_execute(
        "UPDATE agent_credits SET repaid=repaid+?, status=?, updated_at=? WHERE agent_id=?",
        (actual_repay, new_status, now_ts, agent_id)
    )

    # Enregistrer la transaction
    tx_id = str(uuid.uuid4())
    await db.raw_execute(
        "INSERT INTO credit_transactions (id, agent_id, type, amount_usdc, tx_signature, "
        "balance_after, created_at) VALUES (?, ?, 'repay', ?, ?, ?, ?)",
        (tx_id, agent_id, actual_repay, tx_signature, new_debt, now_ts)
    )

    return {
        "status": "repaid",
        "amount_repaid": actual_repay,
        "remaining_debt": round(new_debt, 4),
        "credit_status": new_status,
        "tx_signature": tx_signature,
    }


async def get_credit_status(agent_id: str) -> dict:
    """Retourne le statut complet du credit d'un agent.

    Inclut : score, limite, dette, historique des transactions.
    """
    from database import db
    await _ensure_schema()

    # Score courant
    score = await calculate_credit_score(agent_id)
    limit = get_credit_limit(score)
    tier = _get_credit_tier_name(score)

    # Credit existant
    rows = await db.raw_execute_fetchall(
        "SELECT borrowed, repaid, status, created_at, last_scored_at FROM agent_credits "
        "WHERE agent_id=?",
        (agent_id,)
    )

    borrowed = 0.0
    repaid_total = 0.0
    credit_status = "no_credit"
    credit_since = None
    last_scored = None

    if rows:
        row = rows[0]
        borrowed = float(row[0] if isinstance(row, (list, tuple)) else row.get("borrowed", 0) or 0)
        repaid_total = float(row[1] if isinstance(row, (list, tuple)) else row.get("repaid", 0) or 0)
        credit_status = row[2] if isinstance(row, (list, tuple)) else row.get("status", "active")
        created = int(row[3] if isinstance(row, (list, tuple)) else row.get("created_at", 0) or 0)
        scored = int(row[4] if isinstance(row, (list, tuple)) else row.get("last_scored_at", 0) or 0)
        if created > 0:
            credit_since = datetime.fromtimestamp(created, tz=timezone.utc).isoformat()
        if scored > 0:
            last_scored = datetime.fromtimestamp(scored, tz=timezone.utc).isoformat()

    current_debt = borrowed - repaid_total
    available = max(0, limit - current_debt)

    # Historique recent (10 dernieres transactions)
    try:
        tx_rows = await db.raw_execute_fetchall(
            "SELECT type, amount_usdc, tx_signature, balance_after, created_at "
            "FROM credit_transactions WHERE agent_id=? ORDER BY created_at DESC LIMIT 10",
            (agent_id,)
        )
        history = []
        for r in tx_rows:
            tx_type = r[0] if isinstance(r, (list, tuple)) else r.get("type", "")
            amount = float(r[1] if isinstance(r, (list, tuple)) else r.get("amount_usdc", 0) or 0)
            sig = r[2] if isinstance(r, (list, tuple)) else r.get("tx_signature", "")
            bal = float(r[3] if isinstance(r, (list, tuple)) else r.get("balance_after", 0) or 0)
            ts = int(r[4] if isinstance(r, (list, tuple)) else r.get("created_at", 0) or 0)
            history.append({
                "type": tx_type,
                "amount_usdc": amount,
                "tx_signature": sig,
                "balance_after": bal,
                "date": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts > 0 else None,
            })
    except Exception:
        history = []

    return {
        "agent_id": agent_id,
        "score": score,
        "tier": tier,
        "credit_limit": limit,
        "borrowed_total": round(borrowed, 4),
        "repaid_total": round(repaid_total, 4),
        "current_debt": round(current_debt, 4),
        "available_credit": round(available, 4),
        "credit_status": credit_status,
        "credit_since": credit_since,
        "last_scored_at": last_scored,
        "tiers": {t["min_score"]: f"${t['limit_usdc']}" for t in CREDIT_TIERS},
        "history": history,
    }


# ── Modeles Pydantic ──

class CreditRequestModel(BaseModel):
    amount_usdc: float

class CreditRepayModel(BaseModel):
    amount_usdc: float
    tx_signature: str = ""


# ── Routes FastAPI ──

@router.get("/score/{agent_id}")
async def api_credit_score(agent_id: str):
    """GET /api/credit/score/{agent_id} — Score de credit et limite pour un agent."""
    score = await calculate_credit_score(agent_id)
    limit = get_credit_limit(score)
    tier = _get_credit_tier_name(score)
    return {
        "status": "ok",
        "agent_id": agent_id,
        "score": score,
        "tier": tier,
        "credit_limit": limit,
        "tiers": CREDIT_TIERS,
    }


@router.post("/request")
async def api_credit_request(body: CreditRequestModel, wallet: str = Depends(require_auth)):
    """POST /api/credit/request — Demander une ligne de credit (auth requise)."""
    result = await request_credit(agent_id=wallet, amount_usdc=body.amount_usdc)
    return {"status": "ok", **result}


@router.post("/repay")
async def api_credit_repay(body: CreditRepayModel, wallet: str = Depends(require_auth)):
    """POST /api/credit/repay — Rembourser un credit (auth requise)."""
    result = await repay_credit(
        agent_id=wallet,
        amount_usdc=body.amount_usdc,
        tx_signature=body.tx_signature
    )
    return {"status": "ok", **result}


@router.get("/status/{agent_id}")
async def api_credit_status(agent_id: str):
    """GET /api/credit/status/{agent_id} — Statut complet du credit d'un agent."""
    status = await get_credit_status(agent_id)
    return {"status": "ok", "credit": status}


print("[Credit] Agent Credit System charge")
