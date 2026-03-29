"""MAXIA Gamification V12 — Points, badges et streaks pour le marketplace

Systeme de gamification complet :
- Points par action (swap, escrow, register, login, stock trade, GPU rental, referral)
- 9 badges automatiques (first_trade, whale, multi_chain, diamond_hands, top_10, volume, streaks)
- Streaks journalieres avec bonus aux milestones (7j: +50, 30j: +200)
- Leaderboard public, stats par wallet, endpoint interne pour enregistrer les actions

Usage depuis d'autres modules :
    from gamification import record_action
    await record_action(wallet="...", action="swap_completed", amount_usd=150.0, chain="solana")
"""
import logging
import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from error_utils import safe_error

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/gamification", tags=["gamification"])

# ── Points par action ──

POINTS_PER_ACTION: dict[str, int] = {
    "swap_completed": 10,
    "escrow_created": 50,
    "agent_registered": 5,
    "daily_login": 3,
    "stock_trade": 15,
    "gpu_rental": 25,
    "referral": 20,
}

# ── Badges et conditions ──

BADGE_DEFINITIONS: dict[str, str] = {
    "first_trade": "Premier swap ou stock trade",
    "whale": "Trade unique > $10,000",
    "multi_chain": "Trade sur 3+ chains",
    "diamond_hands": "Position tenue 30+ jours",
    "top_10": "Apparu dans le top 10 du leaderboard",
    "volume_1k": "Volume total > $1,000",
    "volume_10k": "Volume total > $10,000",
    "streak_7": "7 jours consecutifs d'activite",
    "streak_30": "30 jours consecutifs d'activite",
}

# ── Streak milestones (jours -> bonus points) ──

STREAK_MILESTONES: dict[int, int] = {
    7: 50,
    30: 200,
}

# ── Schema SQL (idempotent) ──

_GAMIFICATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_points (
    wallet TEXT PRIMARY KEY,
    points INTEGER NOT NULL DEFAULT 0,
    streak_days INTEGER NOT NULL DEFAULT 0,
    last_active TEXT NOT NULL DEFAULT '',
    total_volume NUMERIC(18,6) NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS user_badges (
    wallet TEXT NOT NULL,
    badge TEXT NOT NULL,
    awarded_at TEXT NOT NULL,
    UNIQUE(wallet, badge)
);

CREATE INDEX IF NOT EXISTS idx_user_points_points ON user_points(points DESC);
CREATE INDEX IF NOT EXISTS idx_user_badges_wallet ON user_badges(wallet);

CREATE TABLE IF NOT EXISTS user_chains (
    wallet TEXT NOT NULL,
    chain TEXT NOT NULL,
    UNIQUE(wallet, chain)
);

CREATE INDEX IF NOT EXISTS idx_user_chains_wallet ON user_chains(wallet);
"""

_schema_ready = False


async def _ensure_schema() -> None:
    """Cree les tables gamification si elles n'existent pas encore."""
    global _schema_ready
    if _schema_ready:
        return
    try:
        from database import db
        await db.raw_executescript(_GAMIFICATION_SCHEMA)
        _schema_ready = True
        logger.info("[Gamification] Schema pret")
    except Exception as e:
        logger.error(f"[Gamification] Erreur schema: {e}")


# ── Helpers internes ──

def _today_str() -> str:
    """Date du jour en ISO format (UTC)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _now_iso() -> str:
    """Datetime ISO UTC complet."""
    return datetime.now(timezone.utc).isoformat()


async def _get_or_create_user(wallet: str) -> dict:
    """Recupere ou initialise un utilisateur dans user_points."""
    from database import db
    row = await db._fetchone(
        "SELECT wallet, points, streak_days, last_active, total_volume "
        "FROM user_points WHERE wallet = ?",
        (wallet,),
    )
    if row:
        # Normaliser en dict (compatible SQLite Row et PostgreSQL dict)
        if isinstance(row, dict):
            return row
        return {
            "wallet": row[0],
            "points": row[1],
            "streak_days": row[2],
            "last_active": row[3],
            "total_volume": row[4],
        }
    # Creer l'utilisateur
    today = _today_str()
    await db.raw_execute(
        "INSERT INTO user_points (wallet, points, streak_days, last_active, total_volume) "
        "VALUES (?, 0, 0, ?, 0)",
        (wallet, today),
    )
    return {
        "wallet": wallet,
        "points": 0,
        "streak_days": 0,
        "last_active": today,
        "total_volume": 0.0,
    }


async def _update_streak(wallet: str, current_streak: int, last_active: str) -> tuple[int, int]:
    """Met a jour la streak et retourne (new_streak, bonus_points).

    Retourne un nouveau tuple immutable plutot que de modifier l'etat en place.
    """
    today = _today_str()
    bonus = 0

    if last_active == today:
        # Deja actif aujourd'hui, pas de changement
        return current_streak, 0

    # Verifier si c'est le jour suivant
    try:
        last_date = datetime.strptime(last_active, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        today_date = datetime.strptime(today, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        delta_days = (today_date - last_date).days
    except (ValueError, TypeError):
        delta_days = 999  # Format invalide -> reset

    if delta_days == 1:
        new_streak = current_streak + 1
    elif delta_days == 0:
        new_streak = current_streak
    else:
        # Jour(s) manque(s) -> reset a 1 (aujourd'hui compte)
        new_streak = 1

    # Bonus aux milestones
    for milestone_days, milestone_bonus in STREAK_MILESTONES.items():
        if new_streak == milestone_days:
            bonus = milestone_bonus
            break

    return new_streak, bonus


async def _award_badge(wallet: str, badge: str) -> bool:
    """Attribue un badge si pas deja obtenu. Retourne True si nouveau badge."""
    from database import db
    now = _now_iso()
    try:
        # INSERT OR IGNORE pour SQLite, ON CONFLICT DO NOTHING pour PostgreSQL
        # raw_execute gere la conversion via _pg_convert
        existing = await db._fetchone(
            "SELECT badge FROM user_badges WHERE wallet = ? AND badge = ?",
            (wallet, badge),
        )
        if existing:
            return False
        await db.raw_execute(
            "INSERT INTO user_badges (wallet, badge, awarded_at) VALUES (?, ?, ?)",
            (wallet, badge, now),
        )
        logger.info(f"[Gamification] Badge '{badge}' attribue a {wallet[:8]}...")
        return True
    except Exception as e:
        # Badge deja attribue (UNIQUE constraint) ou autre erreur
        logger.debug(f"[Gamification] Badge '{badge}' skip pour {wallet[:8]}...: {e}")
        return False


async def _check_and_award_badges(
    wallet: str,
    action: str,
    amount_usd: float,
    total_volume: float,
    streak_days: int,
    chain: str,
) -> list[str]:
    """Verifie toutes les conditions de badges et attribue ceux gagnes.

    Retourne la liste des nouveaux badges attribues.
    """
    from database import db
    new_badges: list[str] = []

    # first_trade: premier swap ou stock trade
    if action in ("swap_completed", "stock_trade"):
        if await _award_badge(wallet, "first_trade"):
            new_badges.append("first_trade")

    # whale: trade unique > $10,000
    if amount_usd > 10_000:
        if await _award_badge(wallet, "whale"):
            new_badges.append("whale")

    # multi_chain: traded sur 3+ chains
    if chain:
        try:
            # Enregistrer la chain utilisee
            existing = await db._fetchone(
                "SELECT chain FROM user_chains WHERE wallet = ? AND chain = ?",
                (wallet, chain),
            )
            if not existing:
                await db.raw_execute(
                    "INSERT INTO user_chains (wallet, chain) VALUES (?, ?)",
                    (wallet, chain),
                )
        except Exception:
            pass  # UNIQUE constraint = deja enregistre

        try:
            rows = await db.raw_execute_fetchall(
                "SELECT COUNT(*) as cnt FROM user_chains WHERE wallet = ?",
                (wallet,),
            )
            chain_count = rows[0][0] if rows and not isinstance(rows[0], dict) else (
                rows[0].get("cnt", 0) if rows else 0
            )
            if chain_count >= 3:
                if await _award_badge(wallet, "multi_chain"):
                    new_badges.append("multi_chain")
        except Exception as e:
            logger.debug(f"[Gamification] Erreur multi_chain check: {e}")

    # volume_1k et volume_10k
    if total_volume > 1_000:
        if await _award_badge(wallet, "volume_1k"):
            new_badges.append("volume_1k")
    if total_volume > 10_000:
        if await _award_badge(wallet, "volume_10k"):
            new_badges.append("volume_10k")

    # streak_7 et streak_30
    if streak_days >= 7:
        if await _award_badge(wallet, "streak_7"):
            new_badges.append("streak_7")
    if streak_days >= 30:
        if await _award_badge(wallet, "streak_30"):
            new_badges.append("streak_30")

    # top_10: verifie si le wallet est dans le top 10
    try:
        rows = await db.raw_execute_fetchall(
            "SELECT wallet FROM user_points ORDER BY points DESC LIMIT 10",
            (),
        )
        top_wallets = [
            (r["wallet"] if isinstance(r, dict) else r[0]) for r in rows
        ]
        if wallet in top_wallets:
            if await _award_badge(wallet, "top_10"):
                new_badges.append("top_10")
    except Exception as e:
        logger.debug(f"[Gamification] Erreur top_10 check: {e}")

    return new_badges


# ── Fonction publique pour les autres modules ──

async def record_action(
    wallet: str,
    action: str,
    amount_usd: float = 0.0,
    chain: str = "",
) -> dict:
    """Enregistre une action utilisateur : attribue des points, met a jour la streak, verifie les badges.

    Appelee depuis les modules swap, escrow, register, etc.

    Args:
        wallet: Adresse du wallet utilisateur
        action: Cle d'action (voir POINTS_PER_ACTION)
        amount_usd: Montant en USD de la transaction (pour badges volume/whale)
        chain: Nom de la chain utilisee (pour badge multi_chain)

    Returns:
        Dict avec points_earned, new_badges, streak_days, total_points
    """
    if not wallet or not wallet.strip():
        return {"error": "wallet requis"}

    if action not in POINTS_PER_ACTION:
        return {"error": f"action inconnue: {action}", "valid_actions": list(POINTS_PER_ACTION.keys())}

    await _ensure_schema()

    try:
        from database import db

        user = await _get_or_create_user(wallet)
        base_points = POINTS_PER_ACTION[action]
        today = _today_str()

        # Streak
        new_streak, streak_bonus = await _update_streak(
            wallet,
            user["streak_days"],
            user["last_active"],
        )

        total_earned = base_points + streak_bonus
        new_total_points = user["points"] + total_earned
        new_total_volume = user["total_volume"] + amount_usd

        # Update en une seule requete
        await db.raw_execute(
            "UPDATE user_points SET points = ?, streak_days = ?, last_active = ?, total_volume = ? "
            "WHERE wallet = ?",
            (new_total_points, new_streak, today, new_total_volume, wallet),
        )

        # Badges
        new_badges = await _check_and_award_badges(
            wallet=wallet,
            action=action,
            amount_usd=amount_usd,
            total_volume=new_total_volume,
            streak_days=new_streak,
            chain=chain,
        )

        result = {
            "wallet": wallet,
            "action": action,
            "points_earned": total_earned,
            "streak_bonus": streak_bonus,
            "new_badges": new_badges,
            "streak_days": new_streak,
            "total_points": new_total_points,
            "total_volume": new_total_volume,
        }

        if new_badges:
            logger.info(
                f"[Gamification] {wallet[:8]}... +{total_earned}pts ({action}), "
                f"badges: {new_badges}, streak: {new_streak}j"
            )

        return result

    except Exception as e:
        logger.error(f"[Gamification] Erreur record_action: {e}", exc_info=True)
        return safe_error(e, "gamification.record_action")


# ── Modeles Pydantic ──

class RecordActionRequest(BaseModel):
    wallet: str = Field(min_length=1, max_length=128)
    action: str = Field(min_length=1, max_length=64)
    amount_usd: float = Field(default=0.0, ge=0)
    chain: str = Field(default="", max_length=32)


# ── Routes FastAPI ──

@router.get("/leaderboard")
async def get_leaderboard(limit: int = Query(default=20, ge=1, le=100)):
    """Top utilisateurs par points. Public, pas d'auth requis."""
    await _ensure_schema()
    try:
        from database import db
        rows = await db.raw_execute_fetchall(
            "SELECT wallet, points, streak_days, total_volume "
            "FROM user_points ORDER BY points DESC LIMIT ?",
            (limit,),
        )
        leaderboard = []
        for rank, row in enumerate(rows, 1):
            if isinstance(row, dict):
                entry = {
                    "rank": rank,
                    "wallet": row["wallet"],
                    "points": row["points"],
                    "streak_days": row["streak_days"],
                    "total_volume": row["total_volume"],
                }
            else:
                entry = {
                    "rank": rank,
                    "wallet": row[0],
                    "points": row[1],
                    "streak_days": row[2],
                    "total_volume": row[3],
                }
            leaderboard.append(entry)

        return {"leaderboard": leaderboard, "total": len(leaderboard)}

    except Exception as e:
        logger.error(f"[Gamification] Erreur leaderboard: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Erreur leaderboard")


@router.get("/user/{wallet}/stats")
async def get_user_stats(wallet: str):
    """Stats d'un utilisateur : points, badges, streak, rang. Public, pas d'auth requis."""
    if not wallet or len(wallet) > 128:
        raise HTTPException(status_code=400, detail="wallet invalide")

    await _ensure_schema()
    try:
        from database import db

        # Points et streak
        user = await _get_or_create_user(wallet)

        # Badges
        badge_rows = await db.raw_execute_fetchall(
            "SELECT badge, awarded_at FROM user_badges WHERE wallet = ?",
            (wallet,),
        )
        badges = []
        for row in badge_rows:
            if isinstance(row, dict):
                badges.append({"badge": row["badge"], "awarded_at": row["awarded_at"]})
            else:
                badges.append({"badge": row[0], "awarded_at": row[1]})

        # Rang (position dans le leaderboard)
        rank_rows = await db.raw_execute_fetchall(
            "SELECT COUNT(*) as cnt FROM user_points WHERE points > ?",
            (user["points"],),
        )
        rank_count = 0
        if rank_rows:
            rank_count = (
                rank_rows[0]["cnt"] if isinstance(rank_rows[0], dict) else rank_rows[0][0]
            )
        rank = rank_count + 1

        # Chains utilisees
        chain_rows = await db.raw_execute_fetchall(
            "SELECT chain FROM user_chains WHERE wallet = ?",
            (wallet,),
        )
        chains = [
            (r["chain"] if isinstance(r, dict) else r[0]) for r in chain_rows
        ]

        return {
            "wallet": user["wallet"],
            "points": user["points"],
            "streak_days": user["streak_days"],
            "last_active": user["last_active"],
            "total_volume": user["total_volume"],
            "rank": rank,
            "badges": badges,
            "badge_count": len(badges),
            "chains_used": chains,
            "available_badges": BADGE_DEFINITIONS,
        }

    except Exception as e:
        logger.error(f"[Gamification] Erreur user stats: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Erreur stats utilisateur")


@router.post("/record")
async def record_action_endpoint(req: RecordActionRequest):
    """Endpoint interne pour enregistrer une action. Appele par les autres modules."""
    result = await record_action(
        wallet=req.wallet,
        action=req.action,
        amount_usd=req.amount_usd,
        chain=req.chain,
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


# ── Fonction utilitaire pour diamond_hands (appelee par un scheduler) ──

async def check_diamond_hands(wallet: str, position_held_days: int) -> bool:
    """Verifie et attribue le badge diamond_hands si position tenue 30+ jours.

    Appelee depuis un scheduler ou manuellement quand on detecte
    qu'un utilisateur a tenu une position longtemps.

    Args:
        wallet: Adresse du wallet
        position_held_days: Nombre de jours que la position a ete tenue

    Returns:
        True si le badge a ete nouvellement attribue
    """
    if position_held_days < 30:
        return False

    await _ensure_schema()
    try:
        return await _award_badge(wallet, "diamond_hands")
    except Exception as e:
        logger.error(f"[Gamification] Erreur diamond_hands: {e}")
        return False
