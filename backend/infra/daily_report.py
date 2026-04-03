"""MAXIA Daily Report V10.1 — Rapport quotidien automatique"""
import time
from infra.alerts import alert_daily_report


async def generate_daily_report(brain, growth_agent, db) -> dict:
    """Genere et envoie le rapport quotidien via Discord."""
    stats = brain.get_stats()
    growth_stats = growth_agent.get_stats()

    # Recuperer les stats DB
    try:
        db_stats = await db.get_stats()
    except Exception:
        db_stats = {"volume_24h": 0, "total_revenue": 0, "listing_count": 0}

    report = {
        "date": time.strftime("%Y-%m-%d"),
        "profits": stats.get("profits", 0),
        "monthly_revenue": stats.get("monthly_revenue", 0),
        "monthly_spend": stats.get("monthly_spend", 0),
        "prospects": growth_stats.get("prospects_today", 0),
        "total_prospects": growth_stats.get("total_prospects", 0),
        "conversions": 0,  # A implementer avec le tracking
        "treasury_balance": db_stats.get("total_revenue", 0),
        "volume_24h": db_stats.get("volume_24h", 0),
        "listing_count": db_stats.get("listing_count", 0),
        "tier": stats.get("tier", "survival"),
        "uptime": stats.get("uptime_human", "0h 0m"),
        "agent_spend": stats.get("agent_spend", 0),
    }

    # Envoyer via Discord
    await alert_daily_report(report)

    return report
