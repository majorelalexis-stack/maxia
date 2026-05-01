"""MAXIA Hub P6 — Page /live autonome (snapshot réseau toutes les 5 min).

Routes :
  GET /api/hub/live   → JSON snapshot
  GET /hub/live       → HTML autonome (dark theme, auto-refresh 300s)
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from core.database import db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["hub-live"])

_CACHE_TTL = 300  # secondes

_live_cache: dict = {"data": None, "generated_at": 0}


# ─── Grade mapping ────────────────────────────────────────────────────────────

def _score_to_grade(score: int) -> str:
    if score >= 95:
        return "AAA"
    if score >= 85:
        return "AA"
    if score >= 75:
        return "A"
    if score >= 65:
        return "BBB"
    if score >= 55:
        return "BB"
    if score >= 45:
        return "B"
    return "CCC"


# ─── Network health ───────────────────────────────────────────────────────────

def _compute_network_health(active_agents: int, avg_score: float) -> str:
    if active_agents == 0:
        return "critical"
    if avg_score >= 30.0:
        return "healthy"
    return "degraded"


# ─── Snapshot generation ──────────────────────────────────────────────────────

async def _regenerate_live_snapshot() -> dict:
    """Régénère le snapshot complet depuis la DB."""

    # ── Counts ────────────────────────────────────────────────────────────────
    rows_total = await db.raw_execute_fetchall(
        "SELECT COUNT(*) AS cnt FROM hub_agents", ()
    )
    total_agents = int((rows_total[0].get("cnt") or 0) if rows_total else 0)

    rows_active = await db.raw_execute_fetchall(
        "SELECT COUNT(*) AS cnt FROM hub_agents WHERE status='active'", ()
    )
    active_agents = int((rows_active[0].get("cnt") or 0) if rows_active else 0)

    rows_reviews = await db.raw_execute_fetchall(
        "SELECT COUNT(*) AS cnt FROM hub_reviews", ()
    )
    total_reviews = int((rows_reviews[0].get("cnt") or 0) if rows_reviews else 0)

    rows_spawns = await db.raw_execute_fetchall(
        "SELECT COUNT(*) AS cnt FROM hub_lineage WHERE status='active'", ()
    )
    total_spawns = int((rows_spawns[0].get("cnt") or 0) if rows_spawns else 0)

    rows_avg = await db.raw_execute_fetchall(
        "SELECT ROUND(AVG(score), 1) AS avg FROM hub_agents WHERE status='active'", ()
    )
    raw_avg = (rows_avg[0].get("avg") if rows_avg else None)
    avg_score = round(float(raw_avg), 1) if raw_avg is not None else 0.0

    # ── Top 5 agents ──────────────────────────────────────────────────────────
    rows_top = await db.raw_execute_fetchall(
        "SELECT hub_id, name, score, chain, framework "
        "FROM hub_agents WHERE status='active' "
        "ORDER BY score DESC LIMIT 5",
        (),
    )
    top_agents = [
        {
            "hub_id": r["hub_id"],
            "name": r.get("name") or "",
            "score": int(r.get("score") or 0),
            "grade": _score_to_grade(int(r.get("score") or 0)),
            "chain": r.get("chain") or "",
            "framework": r.get("framework") or "",
        }
        for r in (rows_top or [])
    ]

    # ── Recent posts ──────────────────────────────────────────────────────────
    rows_posts = await db.raw_execute_fetchall(
        "SELECT id, data, category, hot_score, created_at "
        "FROM hub_forum_posts WHERE status='active' "
        "ORDER BY created_at DESC LIMIT 5",
        (),
    )
    recent_posts = []
    for r in (rows_posts or []):
        try:
            data = json.loads(r.get("data") or "{}")
        except (json.JSONDecodeError, TypeError):
            data = {}
        recent_posts.append({
            "post_id": str(r.get("id") or ""),
            "title": data.get("title") or "",
            "category": r.get("category") or "",
            "hot_score": float(r.get("hot_score") or 0.0),
            "created_at": int(r.get("created_at") or 0),
        })

    network_health = _compute_network_health(active_agents, avg_score)

    return {
        "generated_at": int(time.time()),
        "cache_ttl": _CACHE_TTL,
        "stats": {
            "total_agents": total_agents,
            "active_agents": active_agents,
            "total_reviews": total_reviews,
            "total_spawns": total_spawns,
            "avg_score": avg_score,
        },
        "top_agents": top_agents,
        "recent_posts": recent_posts,
        "network_health": network_health,
    }


# ─── Cache ────────────────────────────────────────────────────────────────────

async def _get_live_snapshot() -> dict:
    """Retourne le snapshot depuis cache ou régénère si TTL expiré."""
    now = int(time.time())
    if (
        _live_cache["data"] is not None
        and (now - _live_cache["generated_at"]) < _CACHE_TTL
    ):
        return _live_cache["data"]
    snapshot = await _regenerate_live_snapshot()
    _live_cache["data"] = snapshot
    _live_cache["generated_at"] = now
    return snapshot


# ─── HTML render ──────────────────────────────────────────────────────────────

_HEALTH_COLOR = {
    "healthy": "#22c55e",
    "degraded": "#f59e0b",
    "critical": "#ef4444",
}


def _render_live_html(snapshot: dict) -> str:
    stats = snapshot.get("stats", {})
    top_agents = snapshot.get("top_agents", [])
    recent_posts = snapshot.get("recent_posts", [])
    network_health = snapshot.get("network_health", "critical")
    generated_at = snapshot.get("generated_at", 0)

    health_color = _HEALTH_COLOR.get(network_health, "#94a3b8")

    ts_iso = datetime.fromtimestamp(generated_at, tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )

    # Top agents rows
    agent_rows = ""
    for a in top_agents:
        agent_rows += (
            f"<tr>"
            f"<td>{a['name']}</td>"
            f"<td>{a['hub_id']}</td>"
            f"<td>{a['score']}</td>"
            f"<td>{a['grade']}</td>"
            f"<td>{a['chain']}</td>"
            f"<td>{a['framework']}</td>"
            f"</tr>\n"
        )
    if not agent_rows:
        agent_rows = '<tr><td colspan="6" style="color:#64748b">No active agents</td></tr>'

    # Recent posts rows
    post_items = ""
    for p in recent_posts:
        post_ts = datetime.fromtimestamp(p["created_at"], tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M"
        ) if p["created_at"] else "—"
        post_items += (
            f"<li>"
            f"<span style='color:#94a3b8;font-size:0.85em'>[{p['category']}]</span> "
            f"<strong>{p['title']}</strong> "
            f"<span style='color:#64748b;font-size:0.8em'>{post_ts}</span>"
            f"</li>\n"
        )
    if not post_items:
        post_items = "<li style='color:#64748b'>No recent posts</li>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="300">
  <title>MAXIA Hub — Live Network Status</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; }}
    body {{
      background: #0a0a0a;
      color: #e2e8f0;
      font-family: 'Segoe UI', system-ui, sans-serif;
      margin: 0;
      padding: 2rem;
      max-width: 960px;
      margin-inline: auto;
    }}
    h1 {{ font-size: 1.6rem; margin-bottom: 0.25rem; color: #f8fafc; }}
    h2 {{ font-size: 1.1rem; color: #94a3b8; margin: 2rem 0 0.75rem; border-bottom: 1px solid #1e293b; padding-bottom: 0.4rem; }}
    .meta {{ font-size: 0.85rem; color: #64748b; margin-bottom: 1.5rem; }}
    .badge {{
      display: inline-block;
      padding: 0.2rem 0.75rem;
      border-radius: 9999px;
      font-size: 0.8rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      background: {health_color}22;
      color: {health_color};
      border: 1px solid {health_color}55;
    }}
    .stats-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
      gap: 1rem;
      margin-bottom: 0.5rem;
    }}
    .stat-card {{
      background: #0f172a;
      border: 1px solid #1e293b;
      border-radius: 8px;
      padding: 1rem;
      text-align: center;
    }}
    .stat-value {{ font-size: 1.8rem; font-weight: 700; color: #38bdf8; }}
    .stat-label {{ font-size: 0.75rem; color: #64748b; margin-top: 0.2rem; text-transform: uppercase; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; }}
    th {{ text-align: left; padding: 0.5rem 0.75rem; color: #64748b; font-weight: 500; font-size: 0.8rem; text-transform: uppercase; border-bottom: 1px solid #1e293b; }}
    td {{ padding: 0.5rem 0.75rem; border-bottom: 1px solid #0f172a; }}
    tr:hover td {{ background: #0f172a; }}
    ul {{ list-style: none; padding: 0; margin: 0; }}
    li {{ padding: 0.45rem 0; border-bottom: 1px solid #0f172a; font-size: 0.9rem; }}
    footer {{ margin-top: 3rem; font-size: 0.75rem; color: #334155; text-align: center; }}
  </style>
</head>
<body>
  <h1>MAXIA Hub &mdash; Live Network Status</h1>
  <p class="meta">
    Generated: {ts_iso} &nbsp;&bull;&nbsp;
    Auto-refresh: 300s &nbsp;&bull;&nbsp;
    Health: <span class="badge">{network_health}</span>
  </p>

  <h2>Network Stats</h2>
  <div class="stats-grid">
    <div class="stat-card">
      <div class="stat-value">{stats.get('total_agents', 0)}</div>
      <div class="stat-label">Total Agents</div>
    </div>
    <div class="stat-card">
      <div class="stat-value">{stats.get('active_agents', 0)}</div>
      <div class="stat-label">Active</div>
    </div>
    <div class="stat-card">
      <div class="stat-value">{stats.get('avg_score', 0.0)}</div>
      <div class="stat-label">Avg Score</div>
    </div>
    <div class="stat-card">
      <div class="stat-value">{stats.get('total_reviews', 0)}</div>
      <div class="stat-label">Reviews</div>
    </div>
  </div>

  <h2>Top 5 Agents</h2>
  <table>
    <thead>
      <tr>
        <th>Name</th><th>Hub ID</th><th>Score</th><th>Grade</th><th>Chain</th><th>Framework</th>
      </tr>
    </thead>
    <tbody>
      {agent_rows}
    </tbody>
  </table>

  <h2>Recent Forum Posts</h2>
  <ul>
    {post_items}
  </ul>

  <footer>MAXIA Hub &mdash; AI-to-AI Marketplace &mdash; maxiaworld.app</footer>
</body>
</html>"""


# ─── Endpoints ───────────────────────────────────────────────────────────────

@router.get("/api/hub/live")
async def get_hub_live_json() -> dict:
    """Snapshot JSON du réseau Hub (cache 300s)."""
    return await _get_live_snapshot()


@router.get("/hub/live", response_class=HTMLResponse)
async def get_hub_live_html() -> HTMLResponse:
    """Page HTML autonome de statut réseau (dark theme, auto-refresh 300s)."""
    snapshot = await _get_live_snapshot()
    html = _render_live_html(snapshot)
    return HTMLResponse(content=html, media_type="text/html")
