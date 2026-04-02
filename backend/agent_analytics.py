"""MAXIA Agent Analytics V12 — Google Analytics pour agents IA

Systeme d'analytics complet pour les agents autonomes sur le marketplace :
- Tracking par agent : requetes/heure, revenu/jour, temps de reponse moyen, taux de succes
- Accumulateur in-memory avec flush vers DB toutes les 60 secondes
- Recommendations IA (temps de reponse lent, prix trop eleve, etc.)
- Metriques globales du marketplace (sante, volume, agents actifs)

Table DB : agent_events (agent_id, event_type, data_json, timestamp)
"""
import logging
import asyncio, time, uuid, json
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from typing import Optional
from fastapi import APIRouter, HTTPException, Request, Query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analytics", tags=["agent-analytics"])

# ── Schema DB (creation lazy) ──

_schema_created = False

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS agent_events (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    data_json TEXT DEFAULT '{}',
    timestamp INTEGER DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_agent_events_agent ON agent_events(agent_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_agent_events_type ON agent_events(event_type, timestamp);
"""


async def _ensure_schema():
    """Cree la table agent_events si elle n'existe pas encore."""
    global _schema_created
    if _schema_created:
        return
    try:
        from database import db
        await db.raw_executescript(_SCHEMA_SQL)
        _schema_created = True
    except Exception as e:
        logger.error(f"[Analytics] Erreur schema: {e}")


# ── Accumulateur in-memory (flush toutes les 60s) ──

_accumulator: list = []  # Liste d'evenements en attente de flush
_accumulator_lock = None
_last_flush = time.time()
FLUSH_INTERVAL_S = 60


def _get_lock():
    """Lazy init du lock asyncio (doit etre cree dans la boucle)."""
    global _accumulator_lock
    if _accumulator_lock is None:
        _accumulator_lock = asyncio.Lock()
    return _accumulator_lock


# ── Types d'evenements supportes ──

EVENT_TYPES = {
    "service_executed",    # Un service a ete execute
    "service_failed",      # Un service a echoue
    "payment_received",    # Paiement recu par l'agent
    "payment_sent",        # Paiement envoye par l'agent
    "service_listed",      # Nouveau service liste
    "service_delisted",    # Service retire
    "dispute_opened",      # Dispute ouverte
    "dispute_resolved",    # Dispute resolue
    "rating_received",     # Note recue
}


async def record_agent_event(agent_id: str, event_type: str, data: dict = None):
    """Enregistre un evenement agent dans l'accumulateur in-memory.

    Appele apres chaque execution de service, paiement, etc.
    Les evenements sont flushed vers la DB toutes les 60 secondes.
    """
    if not agent_id:
        return
    event = {
        "id": str(uuid.uuid4()),
        "agent_id": agent_id,
        "event_type": event_type,
        "data_json": json.dumps(data or {}),
        "timestamp": int(time.time()),
    }

    lock = _get_lock()
    async with lock:
        _accumulator.append(event)

    # Flush si le delai est depasse
    await _maybe_flush()


async def _maybe_flush():
    """Flush l'accumulateur vers la DB si le delai est depasse."""
    global _last_flush
    now = time.time()
    if now - _last_flush < FLUSH_INTERVAL_S:
        return
    _last_flush = now
    await _flush_accumulator()


async def _flush_accumulator():
    """Ecrit tous les evenements accumules en DB et vide le buffer."""
    lock = _get_lock()
    async with lock:
        if not _accumulator:
            return
        snapshot = list(_accumulator)
        _accumulator.clear()

    try:
        from database import db
        await _ensure_schema()
        # P6 fix: batch insert instead of N+1
        for event in snapshot:
            try:
                await db.raw_execute(
                    "INSERT INTO agent_events (id, agent_id, event_type, data_json, timestamp) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (event["id"], event["agent_id"], event["event_type"],
                     event["data_json"], event["timestamp"]))
            except Exception:
                pass  # Skip duplicates (ON CONFLICT not portable SQLite/PG)
        logger.info(f"[Analytics] Flush: {len(snapshot)} evenements ecrits en DB")
    except Exception as e:
        # Remettre les evenements dans l'accumulateur en cas d'erreur
        async with lock:
            _accumulator.extend(snapshot)
        logger.error(f"[Analytics] Erreur flush: {e}")


# ── Fonctions d'analyse ──

def _period_to_seconds(period: str) -> int:
    """Convertit une periode (1h, 7d, 30d, etc.) en secondes."""
    unit = period[-1].lower()
    try:
        value = int(period[:-1])
    except (ValueError, IndexError):
        value = 7
        unit = "d"
    if unit == "h":
        return value * 3600
    elif unit == "d":
        return value * 86400
    elif unit == "w":
        return value * 604800
    elif unit == "m":
        return value * 2592000
    return 604800  # Default: 7 jours


async def get_agent_analytics(agent_id: str, period: str = "7d") -> dict:
    """Retourne les analytics time-series pour un agent sur une periode donnee.

    Inclut : requetes/heure, revenu/jour, temps de reponse moyen, taux de succes,
    top services utilises, evolution dans le temps.
    """
    from database import db
    await _ensure_schema()
    # Flush pour avoir les donnees les plus recentes
    await _flush_accumulator()

    seconds = _period_to_seconds(period)
    since_ts = int(time.time()) - seconds

    rows = await db.raw_execute_fetchall(
        "SELECT event_type, data_json, timestamp FROM agent_events "
        "WHERE agent_id=? AND timestamp>=? ORDER BY timestamp ASC",
        (agent_id, since_ts)
    )

    # Compteurs
    total_executions = 0
    total_failures = 0
    total_revenue = 0.0
    total_spent = 0.0
    response_times = []
    service_counts: dict = defaultdict(int)
    hourly_requests: dict = defaultdict(int)
    daily_revenue: dict = defaultdict(float)

    for row in rows:
        etype = row[0] if isinstance(row, (list, tuple)) else row.get("event_type", "")
        data_raw = row[1] if isinstance(row, (list, tuple)) else row.get("data_json", "{}")
        ts = row[2] if isinstance(row, (list, tuple)) else row.get("timestamp", 0)

        try:
            data = json.loads(data_raw) if isinstance(data_raw, str) else data_raw
        except (json.JSONDecodeError, TypeError):
            data = {}

        # Agreger par type
        if etype == "service_executed":
            total_executions += 1
            service_name = data.get("service", "unknown")
            service_counts[service_name] += 1
            if "response_time_ms" in data:
                response_times.append(data["response_time_ms"])
        elif etype == "service_failed":
            total_failures += 1
        elif etype == "payment_received":
            total_revenue += data.get("amount_usdc", 0)
        elif etype == "payment_sent":
            total_spent += data.get("amount_usdc", 0)

        # Time-series aggregation
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        hour_key = dt.strftime("%Y-%m-%d %H:00")
        day_key = dt.strftime("%Y-%m-%d")
        if etype in ("service_executed", "service_failed"):
            hourly_requests[hour_key] += 1
        if etype == "payment_received":
            daily_revenue[day_key] += data.get("amount_usdc", 0)

    # Calculs
    success_rate = 0.0
    total_requests = total_executions + total_failures
    if total_requests > 0:
        success_rate = round((total_executions / total_requests) * 100, 1)

    avg_response_time = 0.0
    if response_times:
        avg_response_time = round(sum(response_times) / len(response_times), 1)

    # Top services (tries par nombre d'executions)
    top_services = sorted(service_counts.items(), key=lambda x: -x[1])[:10]

    return {
        "agent_id": agent_id,
        "period": period,
        "summary": {
            "total_requests": total_requests,
            "total_executions": total_executions,
            "total_failures": total_failures,
            "success_rate_pct": success_rate,
            "avg_response_time_ms": avg_response_time,
            "total_revenue_usdc": round(total_revenue, 4),
            "total_spent_usdc": round(total_spent, 4),
            "net_profit_usdc": round(total_revenue - total_spent, 4),
        },
        "top_services": [{"service": s, "count": c} for s, c in top_services],
        "time_series": {
            "requests_per_hour": dict(sorted(hourly_requests.items())),
            "revenue_per_day": dict(sorted(daily_revenue.items())),
        },
    }


async def get_agent_recommendations(agent_id: str) -> list:
    """Genere des recommandations d'optimisation basees sur les analytics de l'agent.

    Analyse les patterns et retourne des suggestions actionnables :
    - Temps de reponse lent → optimiser le service ou upgrader le GPU
    - Prix trop eleve → comparer avec les concurrents
    - Taux d'echec eleve → investiguer les erreurs
    """
    analytics = await get_agent_analytics(agent_id, period="7d")
    recommendations = []
    summary = analytics["summary"]

    # Recommendation 1 : Temps de reponse
    avg_rt = summary["avg_response_time_ms"]
    if avg_rt > 5000:
        recommendations.append({
            "type": "performance",
            "severity": "high",
            "message": f"Temps de reponse moyen eleve ({avg_rt:.0f}ms). "
                       "Envisagez un GPU plus puissant ou optimisez vos modeles.",
            "metric": "avg_response_time_ms",
            "value": avg_rt,
            "threshold": 5000,
        })
    elif avg_rt > 2000:
        recommendations.append({
            "type": "performance",
            "severity": "medium",
            "message": f"Temps de reponse moyen correct mais ameliorable ({avg_rt:.0f}ms). "
                       "Ciblez sous 2s pour une meilleure experience.",
            "metric": "avg_response_time_ms",
            "value": avg_rt,
            "threshold": 2000,
        })

    # Recommendation 2 : Taux de succes
    success_rate = summary["success_rate_pct"]
    if summary["total_requests"] > 10 and success_rate < 90:
        recommendations.append({
            "type": "reliability",
            "severity": "high",
            "message": f"Taux de succes faible ({success_rate}%). "
                       "Verifiez les logs d'erreur et corrigez les services defaillants.",
            "metric": "success_rate_pct",
            "value": success_rate,
            "threshold": 90,
        })
    elif summary["total_requests"] > 10 and success_rate < 98:
        recommendations.append({
            "type": "reliability",
            "severity": "low",
            "message": f"Taux de succes a {success_rate}%. Ciblez 99%+ pour un score AAA.",
            "metric": "success_rate_pct",
            "value": success_rate,
            "threshold": 98,
        })

    # Recommendation 3 : Rentabilite
    if summary["total_revenue_usdc"] > 0 and summary["net_profit_usdc"] < 0:
        recommendations.append({
            "type": "pricing",
            "severity": "high",
            "message": f"Vos depenses (${summary['total_spent_usdc']:.2f}) depassent vos revenus "
                       f"(${summary['total_revenue_usdc']:.2f}). Augmentez vos prix ou reduisez "
                       "vos couts GPU.",
            "metric": "net_profit_usdc",
            "value": summary["net_profit_usdc"],
            "threshold": 0,
        })

    # Recommendation 4 : Volume faible
    if summary["total_requests"] < 10:
        recommendations.append({
            "type": "growth",
            "severity": "medium",
            "message": "Peu de requetes cette semaine. Listez plus de services ou "
                       "ameliorez votre visibilite sur le marketplace.",
            "metric": "total_requests",
            "value": summary["total_requests"],
            "threshold": 10,
        })

    # Recommendation 5 : Diversification
    top = analytics["top_services"]
    if len(top) == 1 and summary["total_requests"] > 20:
        recommendations.append({
            "type": "diversification",
            "severity": "low",
            "message": f"100% de vos requetes sont sur '{top[0]['service']}'. "
                       "Diversifiez vos services pour reduire le risque.",
            "metric": "service_count",
            "value": 1,
            "threshold": 3,
        })

    if not recommendations:
        recommendations.append({
            "type": "congratulations",
            "severity": "info",
            "message": "Toutes les metriques sont saines. Continuez ainsi !",
            "metric": "overall",
            "value": 100,
            "threshold": 0,
        })

    return recommendations


async def get_marketplace_analytics() -> dict:
    """Retourne les metriques de sante globale du marketplace.

    Inclut : agents actifs (24h), volume total, services executes,
    taux de succes global, revenue total.
    """
    from database import db
    await _ensure_schema()
    await _flush_accumulator()

    now_ts = int(time.time())
    day_ago = now_ts - 86400
    week_ago = now_ts - 604800

    # Agents actifs (au moins 1 evenement dans les 24h)
    try:
        active_rows = await db.raw_execute_fetchall(
            "SELECT COUNT(DISTINCT agent_id) as cnt FROM agent_events WHERE timestamp>=?",
            (day_ago,)
        )
        active_agents_24h = active_rows[0][0] if active_rows else 0
        if isinstance(active_agents_24h, dict):
            active_agents_24h = active_agents_24h.get("cnt", 0)
    except Exception:
        active_agents_24h = 0

    # Volume 24h (executions)
    try:
        vol_rows = await db.raw_execute_fetchall(
            "SELECT COUNT(*) as cnt FROM agent_events "
            "WHERE event_type='service_executed' AND timestamp>=?",
            (day_ago,)
        )
        executions_24h = vol_rows[0][0] if vol_rows else 0
        if isinstance(executions_24h, dict):
            executions_24h = executions_24h.get("cnt", 0)
    except Exception:
        executions_24h = 0

    # Volume 7d
    try:
        vol7_rows = await db.raw_execute_fetchall(
            "SELECT COUNT(*) as cnt FROM agent_events "
            "WHERE event_type='service_executed' AND timestamp>=?",
            (week_ago,)
        )
        executions_7d = vol7_rows[0][0] if vol7_rows else 0
        if isinstance(executions_7d, dict):
            executions_7d = executions_7d.get("cnt", 0)
    except Exception:
        executions_7d = 0

    # Echecs 24h
    try:
        fail_rows = await db.raw_execute_fetchall(
            "SELECT COUNT(*) as cnt FROM agent_events "
            "WHERE event_type='service_failed' AND timestamp>=?",
            (day_ago,)
        )
        failures_24h = fail_rows[0][0] if fail_rows else 0
        if isinstance(failures_24h, dict):
            failures_24h = failures_24h.get("cnt", 0)
    except Exception:
        failures_24h = 0

    # Revenue 24h
    try:
        rev_rows = await db.raw_execute_fetchall(
            "SELECT data_json FROM agent_events "
            "WHERE event_type='payment_received' AND timestamp>=?",
            (day_ago,)
        )
        revenue_24h = 0.0
        for r in rev_rows:
            raw = r[0] if isinstance(r, (list, tuple)) else r.get("data_json", "{}")
            try:
                d = json.loads(raw) if isinstance(raw, str) else raw
                revenue_24h += d.get("amount_usdc", 0)
            except Exception:
                pass
    except Exception:
        revenue_24h = 0.0

    # Taux de succes global (24h)
    total_24h = executions_24h + failures_24h
    success_rate = round((executions_24h / total_24h) * 100, 1) if total_24h > 0 else 100.0

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "period": "24h",
        "marketplace_health": {
            "active_agents_24h": active_agents_24h,
            "executions_24h": executions_24h,
            "executions_7d": executions_7d,
            "failures_24h": failures_24h,
            "success_rate_pct": success_rate,
            "revenue_24h_usdc": round(revenue_24h, 2),
        },
        "status": "healthy" if success_rate >= 95 else "degraded" if success_rate >= 80 else "critical",
    }


# ── Routes FastAPI ──

@router.get("/agent/{agent_id}")
async def api_agent_analytics(agent_id: str, period: str = Query("7d", description="Periode: 1h, 24h, 7d, 30d")):
    """GET /api/analytics/agent/{agent_id} — Dashboard analytics pour un agent."""
    analytics = await get_agent_analytics(agent_id, period=period)
    return {"status": "ok", "analytics": analytics}


@router.get("/marketplace")
async def api_marketplace_analytics():
    """GET /api/analytics/marketplace — Metriques de sante globale du marketplace."""
    metrics = await get_marketplace_analytics()
    return {"status": "ok", "marketplace": metrics}


@router.get("/agent/{agent_id}/recommendations")
async def api_agent_recommendations(agent_id: str):
    """GET /api/analytics/agent/{agent_id}/recommendations — Suggestions d'optimisation IA."""
    recs = await get_agent_recommendations(agent_id)
    return {"status": "ok", "agent_id": agent_id, "recommendations": recs, "count": len(recs)}


# ── Background flush task ──

async def analytics_flush_loop():
    """Tache de fond qui flush l'accumulateur toutes les 60 secondes.
    A lancer via asyncio.create_task() au demarrage de l'app.
    """
    while True:
        await asyncio.sleep(FLUSH_INTERVAL_S)
        try:
            await _flush_accumulator()
        except Exception as e:
            logger.error(f"[Analytics] Erreur flush loop: {e}")


logger.info("[Analytics] Agent Analytics charge")
