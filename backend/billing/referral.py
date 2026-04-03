"""MAXIA Referral & Badges V12 — Systeme de parrainage + badges de succes

Chaque agent recoit un code unique a l'inscription. Le parrain touche 10% de la
commission MAXIA sur chaque transaction du filleul (plafonné $1000/mois).
Les badges sont recalcules toutes les heures par le scheduler.
"""

import logging
import os, time, uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel

from core.auth import require_auth

router = APIRouter(prefix="/api/referral", tags=["referral"])
badges_router = APIRouter(prefix="/api/badges", tags=["badges"])

# ── Config ──

REFERRAL_COMMISSION_PCT = int(os.getenv("REFERRAL_COMMISSION_PCT", "10"))
MONTHLY_CAP_USD = 1000.0

# ── Badge definitions ──

BADGE_DEFINITIONS = {
    "early_adopter": {"icon": "🌅", "label": "Early Adopter", "description": "Joined in first 30 days"},
    "whale":         {"icon": "🐋", "label": "Whale",         "description": "Volume > $5,000"},
    "builder":       {"icon": "🏗️", "label": "Builder",       "description": "Listed 3+ services"},
    "trader":        {"icon": "📊", "label": "Trader",         "description": "50+ swaps"},
    "referee":       {"icon": "🤝", "label": "Referee",        "description": "5+ active referrals"},
    "trusted":       {"icon": "⭐", "label": "Trusted",        "description": "Grade AA+ on leaderboard"},
    "gpu_master":    {"icon": "🖥️", "label": "GPU Master",    "description": "10+ GPU rentals"},
}

# ── Schema ──

_schema_ready = False

_REFERRAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS referral_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    referrer_id TEXT NOT NULL,
    referred_id TEXT,
    code TEXT UNIQUE NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    total_earned_usdc NUMERIC(18,6) NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active'
);

CREATE INDEX IF NOT EXISTS idx_referral_code ON referral_codes(code);
CREATE INDEX IF NOT EXISTS idx_referral_referrer ON referral_codes(referrer_id);
CREATE INDEX IF NOT EXISTS idx_referral_referred ON referral_codes(referred_id);

CREATE TABLE IF NOT EXISTS referral_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    referrer_id TEXT NOT NULL,
    referred_id TEXT NOT NULL UNIQUE,
    code TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (code) REFERENCES referral_codes(code)
);

CREATE INDEX IF NOT EXISTS idx_reflink_referrer ON referral_links(referrer_id);
CREATE INDEX IF NOT EXISTS idx_reflink_referred ON referral_links(referred_id);

CREATE TABLE IF NOT EXISTS referral_earnings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    referrer_id TEXT NOT NULL,
    referred_id TEXT NOT NULL,
    commission_usdc NUMERIC(18,6) NOT NULL,
    credited_usdc NUMERIC(18,6) NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_refearnings_referrer ON referral_earnings(referrer_id);
CREATE INDEX IF NOT EXISTS idx_refearnings_month ON referral_earnings(referrer_id, referred_id, created_at);

CREATE TABLE IF NOT EXISTS badges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    badge_name TEXT NOT NULL,
    badge_icon TEXT NOT NULL DEFAULT '',
    earned_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(agent_id, badge_name)
);

CREATE INDEX IF NOT EXISTS idx_badges_agent ON badges(agent_id);
"""


async def _ensure_schema():
    """Cree les tables referral + badges si elles n'existent pas encore."""
    global _schema_ready
    if _schema_ready:
        return
    try:
        from core.database import db
        await db.raw_executescript(_REFERRAL_SCHEMA)
        _schema_ready = True
        logger.info("Schema pret")
    except Exception as e:
        logger.error("Erreur schema: %s", e)


def _generate_code() -> str:
    """Genere un code parrainage unique REF-xxxxxxxx."""
    return "REF-" + uuid.uuid4().hex[:8].upper()


# ── Pydantic models ──

class ApplyCodeRequest(BaseModel):
    code: str


# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS — REFERRAL
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/code")
async def get_my_referral_code(wallet: str = Depends(require_auth)):
    """Retourne mon code de parrainage. Le cree s'il n'existe pas."""
    await _ensure_schema()
    from core.database import db

    row = await db.raw_execute_fetchall(
        "SELECT code FROM referral_codes WHERE referrer_id = ? AND referred_id IS NULL LIMIT 1",
        (wallet,),
    )
    if row:
        code = row[0]["code"] if isinstance(row[0], dict) else row[0][0]
    else:
        code = _generate_code()
        # Collision-safe: retry si le code existe deja
        for _ in range(5):
            existing = await db.raw_execute_fetchall(
                "SELECT 1 FROM referral_codes WHERE code = ?", (code,)
            )
            if not existing:
                break
            code = _generate_code()
        await db.raw_execute(
            "INSERT INTO referral_codes (referrer_id, code) VALUES (?, ?)",
            (wallet, code),
        )

    return {
        "code": code,
        "link": f"https://maxiaworld.app?ref={code}",
        "commission_pct": REFERRAL_COMMISSION_PCT,
        "monthly_cap_usd": MONTHLY_CAP_USD,
    }


@router.get("/stats")
async def get_my_referral_stats(wallet: str = Depends(require_auth)):
    """Stats de parrainage : nb filleuls, gains totaux, liste anonymisee."""
    await _ensure_schema()
    from core.database import db

    # Nombre de filleuls
    links = await db.raw_execute_fetchall(
        "SELECT referred_id, created_at FROM referral_links WHERE referrer_id = ?",
        (wallet,),
    )
    total_referred = len(links)

    # Gains totaux
    earn_row = await db.raw_execute_fetchall(
        "SELECT COALESCE(SUM(credited_usdc), 0) AS total FROM referral_earnings WHERE referrer_id = ?",
        (wallet,),
    )
    total_earned = float(earn_row[0]["total"] if isinstance(earn_row[0], dict) else earn_row[0][0]) if earn_row else 0.0

    # Liste anonymisee des filleuls (masquer wallet)
    referred_list = []
    for link in links:
        ref_id = link["referred_id"] if isinstance(link, dict) else link[1]
        created = link["created_at"] if isinstance(link, dict) else link[2]
        referred_list.append({
            "agent": ref_id[:6] + "..." + ref_id[-4:] if len(ref_id) > 10 else ref_id,
            "joined": created,
        })

    return {
        "total_referred": total_referred,
        "total_earned_usdc": round(total_earned, 4),
        "commission_pct": REFERRAL_COMMISSION_PCT,
        "monthly_cap_usd": MONTHLY_CAP_USD,
        "referred_agents": referred_list,
    }


@router.get("/leaderboard")
async def referral_leaderboard():
    """Top 20 parrains par gains totaux. Public."""
    await _ensure_schema()
    from core.database import db

    rows = await db.raw_execute_fetchall(
        "SELECT referrer_id, COALESCE(SUM(credited_usdc), 0) AS total_earned, "
        "COUNT(DISTINCT referred_id) AS referral_count "
        "FROM referral_earnings GROUP BY referrer_id "
        "ORDER BY total_earned DESC LIMIT 20",
    )

    leaderboard = []
    for i, r in enumerate(rows, 1):
        rid = r["referrer_id"] if isinstance(r, dict) else r[0]
        earned = float(r["total_earned"] if isinstance(r, dict) else r[1])
        count = int(r["referral_count"] if isinstance(r, dict) else r[2])
        leaderboard.append({
            "rank": i,
            "agent": rid[:6] + "..." + rid[-4:] if len(rid) > 10 else rid,
            "total_earned_usdc": round(earned, 4),
            "referral_count": count,
        })

    return {"leaderboard": leaderboard}


@router.post("/apply")
async def apply_referral_code(body: ApplyCodeRequest, wallet: str = Depends(require_auth)):
    """Appliquer un code parrainage. Une seule fois, dans les 24h apres inscription."""
    await _ensure_schema()
    from core.database import db

    code = body.code.strip().upper()
    if not code.startswith("REF-") or len(code) != 12:
        raise HTTPException(400, "Format de code invalide. Attendu: REF-XXXXXXXX")

    # Verifier que l'agent ne s'est pas deja associe
    existing = await db.raw_execute_fetchall(
        "SELECT 1 FROM referral_links WHERE referred_id = ?", (wallet,)
    )
    if existing:
        raise HTTPException(409, "Vous avez deja utilise un code parrainage.")

    # Verifier que le code existe
    code_row = await db.raw_execute_fetchall(
        "SELECT referrer_id FROM referral_codes WHERE code = ? AND status = 'active'",
        (code,),
    )
    if not code_row:
        raise HTTPException(404, "Code parrainage introuvable ou inactif.")

    referrer_id = code_row[0]["referrer_id"] if isinstance(code_row[0], dict) else code_row[0][0]

    # Ne pas se parrainer soi-meme
    if referrer_id == wallet:
        raise HTTPException(400, "Impossible de se parrainer soi-meme.")

    # Verifier inscription < 24h (via table agents)
    agent_row = await db.raw_execute_fetchall(
        "SELECT created_at FROM agents WHERE wallet = ? LIMIT 1", (wallet,)
    )
    if agent_row:
        created_ts = int(agent_row[0]["created_at"] if isinstance(agent_row[0], dict) else agent_row[0][0])
        if int(time.time()) - created_ts > 86400:
            raise HTTPException(403, "Le code parrainage ne peut etre applique que dans les 24h suivant l'inscription.")

    # Creer le lien referrer <-> referred
    await db.raw_execute(
        "INSERT INTO referral_links (referrer_id, referred_id, code) VALUES (?, ?, ?)",
        (referrer_id, wallet, code),
    )

    return {
        "ok": True,
        "referrer": referrer_id[:6] + "..." + referrer_id[-4:],
        "message": f"Code {code} applique. Votre parrain recevra {REFERRAL_COMMISSION_PCT}% de la commission MAXIA sur vos transactions.",
    }


# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS — BADGES
# ══════════════════════════════════════════════════════════════════════════════

@badges_router.get("/my")
async def get_my_badges(wallet: str = Depends(require_auth)):
    """Retourne mes badges. Auth required."""
    await _ensure_schema()
    from core.database import db

    rows = await db.raw_execute_fetchall(
        "SELECT badge_name, badge_icon, earned_at FROM badges WHERE agent_id = ? ORDER BY earned_at DESC",
        (wallet,),
    )
    badges = []
    for r in rows:
        name = r["badge_name"] if isinstance(r, dict) else r[0]
        icon = r["badge_icon"] if isinstance(r, dict) else r[1]
        earned = r["earned_at"] if isinstance(r, dict) else r[2]
        defn = BADGE_DEFINITIONS.get(name, {})
        badges.append({
            "name": name,
            "icon": icon,
            "label": defn.get("label", name),
            "description": defn.get("description", ""),
            "earned_at": earned,
        })

    return {"badges": badges, "count": len(badges)}


@badges_router.get("/available")
async def get_available_badges():
    """Liste tous les badges disponibles avec conditions. Public."""
    return {
        "badges": [
            {"name": k, **v}
            for k, v in BADGE_DEFINITIONS.items()
        ]
    }


@badges_router.get("/{agent_id}")
async def get_agent_badges(agent_id: str):
    """Badges d'un agent. Public."""
    await _ensure_schema()
    from core.database import db

    rows = await db.raw_execute_fetchall(
        "SELECT badge_name, badge_icon, earned_at FROM badges WHERE agent_id = ? ORDER BY earned_at DESC",
        (agent_id,),
    )
    badges = []
    for r in rows:
        name = r["badge_name"] if isinstance(r, dict) else r[0]
        icon = r["badge_icon"] if isinstance(r, dict) else r[1]
        earned = r["earned_at"] if isinstance(r, dict) else r[2]
        defn = BADGE_DEFINITIONS.get(name, {})
        badges.append({
            "name": name,
            "icon": icon,
            "label": defn.get("label", name),
            "description": defn.get("description", ""),
            "earned_at": earned,
        })

    return {"agent_id": agent_id, "badges": badges, "count": len(badges)}


# ══════════════════════════════════════════════════════════════════════════════
# HELPER — Commission referral (appele depuis crypto_swap.py, tokenized_stocks.py)
# ══════════════════════════════════════════════════════════════════════════════

async def credit_referral_commission(referred_id: str, commission_usdc: float) -> dict:
    """Credite le parrain de referred_id avec REFERRAL_COMMISSION_PCT% de la commission MAXIA.

    Respecte le plafond de $100/mois par filleul.
    Retourne {"credited": bool, "amount": float}.
    """
    await _ensure_schema()
    from core.database import db

    if commission_usdc <= 0:
        return {"credited": False, "amount": 0.0, "reason": "no_commission"}

    # Trouver le parrain
    link = await db.raw_execute_fetchall(
        "SELECT referrer_id FROM referral_links WHERE referred_id = ? LIMIT 1",
        (referred_id,),
    )
    if not link:
        return {"credited": False, "amount": 0.0, "reason": "no_referrer"}

    referrer_id = link[0]["referrer_id"] if isinstance(link[0], dict) else link[0][0]

    # Calculer le montant a crediter
    raw_amount = commission_usdc * (REFERRAL_COMMISSION_PCT / 100.0)

    # Verifier le plafond mensuel ($100/mois par filleul)
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    month_rows = await db.raw_execute_fetchall(
        "SELECT COALESCE(SUM(credited_usdc), 0) AS month_total "
        "FROM referral_earnings WHERE referrer_id = ? AND referred_id = ? AND created_at >= ?",
        (referrer_id, referred_id, month_start),
    )
    month_total = float(
        month_rows[0]["month_total"] if isinstance(month_rows[0], dict) else month_rows[0][0]
    ) if month_rows else 0.0

    remaining = max(0.0, MONTHLY_CAP_USD - month_total)
    if remaining <= 0:
        return {"credited": False, "amount": 0.0, "reason": "monthly_cap_reached"}

    credited = min(raw_amount, remaining)
    credited = round(credited, 6)

    if credited <= 0:
        return {"credited": False, "amount": 0.0, "reason": "amount_too_small"}

    # Enregistrer le gain
    await db.raw_execute(
        "INSERT INTO referral_earnings (referrer_id, referred_id, commission_usdc, credited_usdc) "
        "VALUES (?, ?, ?, ?)",
        (referrer_id, referred_id, commission_usdc, credited),
    )

    return {"credited": True, "amount": credited, "referrer": referrer_id}


# ══════════════════════════════════════════════════════════════════════════════
# BACKGROUND — Recalcul des badges (appele toutes les heures par le scheduler)
# ══════════════════════════════════════════════════════════════════════════════

async def _award_badge(db, agent_id: str, badge_name: str):
    """Attribue un badge a un agent (INSERT OR IGNORE pour idempotence)."""
    defn = BADGE_DEFINITIONS.get(badge_name, {})
    icon = defn.get("icon", "")
    try:
        await db.raw_execute(
            "INSERT OR IGNORE INTO badges (agent_id, badge_name, badge_icon) VALUES (?, ?, ?)",
            (agent_id, badge_name, icon),
        )
    except Exception as e:
        logger.error("Erreur attribution badge %s a %s: %s", badge_name, agent_id, e)


async def recalculate_badges():
    """Recalcule et attribue les badges pour tous les agents.

    Appele par le scheduler toutes les heures. Verifie les conditions
    de chaque badge contre les donnees en base.
    """
    await _ensure_schema()
    from core.database import db

    try:
        agents = await db.raw_execute_fetchall("SELECT api_key, wallet, created_at FROM agents")
    except Exception as e:
        logger.error("Erreur recalcul badges: %s", e)
        return

    if not agents:
        return

    now = int(time.time())

    # Trouver la date du plus ancien agent pour "early adopter"
    all_created = []
    for a in agents:
        created = int(a["created_at"] if isinstance(a, dict) else a[2])
        all_created.append(created)
    earliest_agent = min(all_created) if all_created else now

    for a in agents:
        agent_id = a["wallet"] if isinstance(a, dict) else a[1]
        api_key = a["api_key"] if isinstance(a, dict) else a[0]
        created_at = int(a["created_at"] if isinstance(a, dict) else a[2])

        # ── early_adopter : inscrit dans les 30 premiers jours du projet ──
        if created_at <= earliest_agent + (30 * 86400):
            await _award_badge(db, agent_id, "early_adopter")

        # ── whale : volume > $5,000 ──
        try:
            vol_row = await db.raw_execute_fetchall(
                "SELECT COALESCE(SUM(amount_usdc), 0) AS vol FROM transactions WHERE wallet = ?",
                (agent_id,),
            )
            volume = float(vol_row[0]["vol"] if isinstance(vol_row[0], dict) else vol_row[0][0]) if vol_row else 0.0
            if volume > 5000:
                await _award_badge(db, agent_id, "whale")
        except Exception:
            pass

        # ── builder : 3+ services listes ──
        try:
            svc_row = await db.raw_execute_fetchall(
                "SELECT COUNT(*) AS cnt FROM agent_services WHERE agent_api_key = ? AND status = 'active'",
                (api_key,),
            )
            svc_count = int(svc_row[0]["cnt"] if isinstance(svc_row[0], dict) else svc_row[0][0]) if svc_row else 0
            if svc_count >= 3:
                await _award_badge(db, agent_id, "builder")
        except Exception:
            pass

        # ── trader : 50+ swaps ──
        try:
            swap_row = await db.raw_execute_fetchall(
                "SELECT COUNT(*) AS cnt FROM crypto_swaps WHERE buyer_wallet = ?",
                (agent_id,),
            )
            swap_count = int(swap_row[0]["cnt"] if isinstance(swap_row[0], dict) else swap_row[0][0]) if swap_row else 0
            if swap_count >= 50:
                await _award_badge(db, agent_id, "trader")
        except Exception:
            pass

        # ── referee : 5+ filleuls actifs ──
        try:
            ref_row = await db.raw_execute_fetchall(
                "SELECT COUNT(*) AS cnt FROM referral_links WHERE referrer_id = ?",
                (agent_id,),
            )
            ref_count = int(ref_row[0]["cnt"] if isinstance(ref_row[0], dict) else ref_row[0][0]) if ref_row else 0
            if ref_count >= 5:
                await _award_badge(db, agent_id, "referee")
        except Exception:
            pass

        # ── trusted : grade AA ou mieux sur le leaderboard ──
        try:
            score_row = await db.raw_execute_fetchall(
                "SELECT grade FROM agent_scores WHERE agent_id = ? LIMIT 1",
                (agent_id,),
            )
            if score_row:
                grade = score_row[0]["grade"] if isinstance(score_row[0], dict) else score_row[0][0]
                if grade in ("AAA", "AA"):
                    await _award_badge(db, agent_id, "trusted")
        except Exception:
            pass

        # ── gpu_master : 10+ locations GPU ──
        try:
            gpu_row = await db.raw_execute_fetchall(
                "SELECT COUNT(*) AS cnt FROM gpu_instances WHERE agent_wallet = ?",
                (agent_id,),
            )
            gpu_count = int(gpu_row[0]["cnt"] if isinstance(gpu_row[0], dict) else gpu_row[0][0]) if gpu_row else 0
            if gpu_count >= 10:
                await _award_badge(db, agent_id, "gpu_master")
        except Exception:
            pass

    logger.info("Badges recalcules pour %d agents", len(agents))
