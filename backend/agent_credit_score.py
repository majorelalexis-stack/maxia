"""Agent Credit Score Portable — score de reputation verifiable on-chain.

Le score est calcule depuis les donnees reelles de l'agent sur MAXIA :
- Success rate (transactions reussies vs echouees)
- Volume total (en USDC)
- Anciennete (jours depuis l'inscription)
- Disputes (gagnes vs perdus)
- SLA compliance (uptime mesure)

Le score est exportable en JSON signe (verifiable par n'importe quelle plateforme)
et peut etre mint en SBT (Soulbound Token) sur Solana.

Revenu : $0.10 par verification de score.
"""
import time
import json
import hashlib
import hmac
import os
from error_utils import safe_error

CREDIT_SCORE_SECRET = os.getenv("CREDIT_SCORE_SECRET", "")
if not CREDIT_SCORE_SECRET:
    import secrets as _secrets
    CREDIT_SCORE_SECRET = _secrets.token_hex(32)
    import logging
    logging.getLogger(__name__).warning(
        "CREDIT_SCORE_SECRET non defini — secret ephemere genere. "
        "Les scores exportes ne seront pas verificables apres restart. "
        "Ajoutez CREDIT_SCORE_SECRET=<64 chars hex> dans .env pour la persistance."
    )
VERIFICATION_FEE_USDC = 0.10

# Grade thresholds (same as leaderboard but with credit score semantics)
GRADE_THRESHOLDS = {
    "AAA": 90,  # Exceptional — top 5%
    "AA": 80,   # Excellent
    "A": 70,    # Good
    "BBB": 60,  # Above average
    "BB": 50,   # Average
    "B": 40,    # Below average
    "CCC": 30,  # Poor
    "CC": 20,   # Very poor
    "C": 0,     # Minimal history
}


def _wallet_in_data(wallet: str, data) -> bool:
    """Check if wallet appears as an exact value in a JSON data structure.

    Avoids substring false-positives (e.g. '0x123' matching '0x123456')
    by comparing only complete string values.
    """
    if isinstance(data, str):
        return data == wallet
    if isinstance(data, dict):
        return any(_wallet_in_data(wallet, v) for v in data.values())
    if isinstance(data, (list, tuple)):
        return any(_wallet_in_data(wallet, item) for item in data)
    return False


async def compute_credit_score(wallet: str, db) -> dict:
    """Compute a comprehensive credit score for an agent wallet."""

    # Fetch data from DB
    try:
        # Transaction history
        swap_count = await db.get_swap_count(wallet) if hasattr(db, 'get_swap_count') else 0
        volume_30d = await db.get_swap_volume_30d(wallet) if hasattr(db, 'get_swap_volume_30d') else 0

        # Get agent info
        agent = None
        try:
            rows = await db.raw_execute_fetchall(
                "SELECT created_at FROM agents WHERE wallet=?", (wallet,))
            if rows:
                agent = dict(rows[0])
        except Exception:
            pass

        # Get disputes
        disputes_won = 0
        disputes_lost = 0
        try:
            rows = await db.raw_execute_fetchall(
                "SELECT data FROM disputes")
            for r in rows:
                try:
                    data = json.loads(r["data"]) if isinstance(r["data"], str) else r["data"]
                    if _wallet_in_data(wallet, data):
                        resolution = data.get("resolution", "")
                        if resolution == "release":
                            disputes_won += 1
                        elif resolution == "refund":
                            disputes_lost += 1
                except Exception:
                    pass
        except Exception:
            pass

        # Also check pod_disputes if table exists
        try:
            pod_rows = await db.raw_execute_fetchall(
                "SELECT resolution FROM pod_disputes WHERE "
                "seller_wallet=? OR buyer_wallet=?", (wallet, wallet))
            for r in pod_rows:
                res = r.get("resolution", "") if isinstance(r, dict) else ""
                if res == "release":
                    disputes_won += 1
                elif res == "refund":
                    disputes_lost += 1
        except Exception:
            pass

        # Get leaderboard metrics if available
        sla_score = 100  # default: no SLA violations
        try:
            lb_rows = await db.raw_execute_fetchall(
                "SELECT uptime_score, penalty_level FROM agent_scores WHERE agent_id=?",
                (wallet,))
            if lb_rows:
                uptime_raw = lb_rows[0].get("uptime_score", 0)
                sla_score = round(uptime_raw * 100)  # 0-1 -> 0-100
                penalty = lb_rows[0].get("penalty_level", "none")
                if penalty == "warning":
                    sla_score = max(0, sla_score - 10)
                elif penalty == "suspension":
                    sla_score = max(0, sla_score - 30)
        except Exception:
            pass

        # Calculate component scores (0-100)
        volume_score = min(100, (volume_30d / 10000) * 100)  # Max at $10K
        activity_score = min(100, (swap_count / 50) * 100)  # Max at 50 swaps
        dispute_score = 100 if disputes_lost == 0 else max(0, 100 - disputes_lost * 25)

        # Age score
        created_at = agent.get("created_at", 0) if agent else 0
        if isinstance(created_at, str):
            try:
                created_at = int(created_at)
            except (ValueError, TypeError):
                created_at = 0
        age_days = (time.time() - created_at) / 86400 if created_at > 0 else 0
        age_score = min(100, (age_days / 30) * 100)  # Max at 30 days

        # Weighted total
        total_score = int(
            volume_score * 0.30 +
            activity_score * 0.25 +
            dispute_score * 0.25 +
            age_score * 0.20
        )
        total_score = max(0, min(100, total_score))

        # Determine grade
        grade = "C"
        for g, threshold in GRADE_THRESHOLDS.items():
            if total_score >= threshold:
                grade = g
                break

        score_data = {
            "wallet": wallet,
            "score": total_score,
            "grade": grade,
            "components": {
                "volume": round(volume_score, 1),
                "activity": round(activity_score, 1),
                "disputes": round(dispute_score, 1),
                "age": round(age_score, 1),
            },
            "raw_data": {
                "swap_count": swap_count,
                "volume_30d_usdc": round(volume_30d, 2),
                "disputes_won": disputes_won,
                "disputes_lost": disputes_lost,
                "age_days": round(age_days, 1),
            },
            "computed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "platform": "MAXIA",
            "platform_url": "https://maxiaworld.app",
        }

        # Sign the score for verification
        score_data["signature"] = _sign_score(score_data)

        return score_data
    except Exception as e:
        result = safe_error(e, "compute_credit_score")
        result.update({"wallet": wallet, "score": 0, "grade": "C"})
        return result


def _sign_score(score_data: dict) -> str:
    """Sign score data with HMAC for external verification."""
    payload = f"{score_data['wallet']}:{score_data['score']}:{score_data['grade']}:{score_data['computed_at']}"
    return hmac.new(CREDIT_SCORE_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()


def verify_score_signature(wallet: str, score: int, grade: str, computed_at: str, signature: str) -> bool:
    """Verify a credit score signature (can be called by external platforms)."""
    payload = f"{wallet}:{score}:{grade}:{computed_at}"
    expected = hmac.new(CREDIT_SCORE_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
