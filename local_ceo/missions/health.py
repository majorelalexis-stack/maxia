"""Missions 7 & 16 — Health check (every 5 min) + Health report (daily at 8h).

Mission 7: Pings critical endpoints, alerts on failure.
Mission 16: Intelligent daily health report with LLM analysis of trends.
"""
import logging
import time
from datetime import datetime

import httpx

from config_local import VPS_URL
from llm import llm
from agents import CEO_SYSTEM_PROMPT
from scheduler import send_mail

log = logging.getLogger("ceo")

# Backoff state for health checks (429 rate limit)
_health_backoff_until: float = 0


async def mission_health_check(mem: dict) -> None:
    """Ping le site et verifie les endpoints critiques (GET uniquement)."""
    global _health_backoff_until

    # Backoff si on a recu 429 recemment
    if time.time() < _health_backoff_until:
        log.info("Health check skipped (backoff until %s)",
                 datetime.fromtimestamp(_health_backoff_until).strftime("%H:%M"))
        return

    # GET checks only — les POST gaspillent 2 requetes pour rien
    get_checks = {
        "site": f"{VPS_URL}/",
        "prices": f"{VPS_URL}/api/public/crypto/prices",
    }
    failures = []

    async with httpx.AsyncClient(timeout=8) as client:
        for name, url in get_checks.items():
            try:
                resp = await client.get(url)
                if resp.status_code == 429:
                    # Rate limited — backoff 30 min
                    _health_backoff_until = time.time() + 1800
                    log.warning("Health %s: 429 rate limited — backoff 30min", name)
                    return
                if resp.status_code != 200:
                    failures.append(f"{name}: HTTP {resp.status_code}")
            except Exception as e:
                failures.append(f"{name}: {str(e)[:50]}")

    if failures:
        alert = (
            "MAXIA DOWN — Endpoints en erreur:\n\n"
            + "\n".join(f"  - {f}" for f in failures)
            + f"\n\nTimestamp: {datetime.now().isoformat()}"
        )

        # Eviter le spam d'alertes (max 1 par 10 min)
        last_alert = (
            mem.get("health_alerts", [{}])[-1].get("ts", 0)
            if mem.get("health_alerts") else 0
        )
        if time.time() - last_alert > 600:
            await send_mail("[MAXIA CEO] SITE DOWN - maxiaworld.app", alert)
            mem["health_alerts"].append({"ts": time.time(), "failures": failures})
            log.error("ALERTE: %s", ", ".join(failures))
            try:
                from memory import log_action
                log_action(
                    "health_alert",
                    target="maxiaworld.app",
                    details=", ".join(failures),
                )
            except Exception as _e:
                log.debug("[health] log_action failed: %s", _e)
    else:
        log.info("Health OK — tous les endpoints repondent")


async def mission_health_report(mem: dict, actions: dict) -> None:
    """Analyse les tendances sante des dernieres 24h et envoie un rapport intelligent."""
    if actions["counts"].get("health_report_sent", 0) >= 1:
        return

    # Collecter les alertes des dernieres 24h
    recent_alerts = [
        a for a in mem.get("health_alerts", [])
        if time.time() - a.get("ts", 0) < 86400
    ]

    # Health check live
    endpoints = {
        "site": f"{VPS_URL}/",
        "prices": f"{VPS_URL}/api/public/crypto/prices",
        "forum": f"{VPS_URL}/api/public/forum",
        "stats": f"{VPS_URL}/api/public/stats",
        "mcp": f"{VPS_URL}/mcp/manifest",
    }
    results = {}
    async with httpx.AsyncClient(timeout=8) as client:
        for name, url in endpoints.items():
            try:
                t0 = time.time()
                resp = await client.get(url)
                latency = (time.time() - t0) * 1000
                results[name] = {"status": resp.status_code, "latency_ms": round(latency)}
            except Exception as e:
                results[name] = {"status": "DOWN", "latency_ms": 0, "error": str(e)[:50]}

    # Demander au LLM d'analyser les tendances
    alert_summary = f"{len(recent_alerts)} alertes dans les dernieres 24h"
    if recent_alerts:
        alert_details = "\n".join(f"- {a.get('failures', [])}" for a in recent_alerts[-5:])
        alert_summary += f":\n{alert_details}"

    results_text = "\n".join(
        f"- {k}: HTTP {v['status']}, {v['latency_ms']}ms" for k, v in results.items()
    )

    analysis = await llm(
        f"Tu es le CEO de MAXIA. Analyse ce rapport de sante du site:\n\n"
        f"Endpoints live:\n{results_text}\n\n"
        f"Alertes 24h: {alert_summary}\n\n"
        f"Donne:\n"
        f"1. Etat general (VERT/ORANGE/ROUGE)\n"
        f"2. Tendances (amelioration, degradation, stable)\n"
        f"3. Si probleme: cause probable et recommandation\n"
        f"4. Impact utilisateurs estime\n"
        f"Sois concis et factuel.",
        system=CEO_SYSTEM_PROMPT,
        max_tokens=400,
    )

    today = datetime.now().strftime("%d/%m/%Y %H:%M")
    body = f"MAXIA CEO — Health Report du {today}\n\n"
    body += "=== ENDPOINTS ===\n"
    for k, v in results.items():
        icon = "OK" if v["status"] == 200 else "DOWN"
        body += f"  [{icon}] {k:12s} HTTP {v['status']:>4}  {v['latency_ms']:>4}ms\n"
    body += f"\n=== ALERTES 24H ===\n  {alert_summary}\n"
    body += f"\n=== ANALYSE CEO ===\n{analysis}\n" if analysis else ""

    await send_mail(f"[MAXIA CEO] Health Report - {today}", body)
    actions["counts"]["health_report_sent"] = 1
    log.info("Health report intelligent envoye")
