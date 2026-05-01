"""MAXIA Hub R0 — Scout actif.

Découvre des agents AI sur 3 registres externes (Agentverse, ElizaOS, GitHub)
et les stocke comme profils passifs (status='unverified') dans hub_scout_results.
Aucun LLM. Aucune signature requise. Zéro interaction avec l'agent découvert.
"""
import json
import time
import uuid
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks

from core.database import db

# ─── URLs des registres (modifiables sans toucher au code) ───────────────────

_AGENTVERSE_URL = "https://agentverse.ai/v1/search/agents"
_ELIZAOS_URL = "https://elizaos.github.io/registry/registry.json"
_GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"
_GITHUB_QUERY = "ai agent elizaos OR agentverse OR maxia language:python"
_HTTP_TIMEOUT = 12.0


# ─── Classe principale ───────────────────────────────────────────────────────

class HubScout:
    """Scout actif — interroge 3 sources et stocke les profils non vérifiés."""

    async def fetch_agentverse(self, http_client: httpx.AsyncClient) -> list[dict[str, Any]]:
        try:
            resp = await http_client.get(
                _AGENTVERSE_URL, params={"limit": 100}, timeout=_HTTP_TIMEOUT
            )
            if resp.status_code != 200:
                return []
            agents = resp.json().get("agents", [])
            return [
                {
                    "source": "agentverse",
                    "external_id": a["address"],
                    "name": a.get("name", a["address"]),
                    "endpoint": a.get("endpoint"),
                    "framework": "fetchai",
                    "description": a.get("description"),
                    "raw_data": json.dumps(a),
                }
                for a in agents
                if a.get("address")
            ]
        except Exception:
            return []

    async def fetch_elizaos(self, http_client: httpx.AsyncClient) -> list[dict[str, Any]]:
        try:
            resp = await http_client.get(_ELIZAOS_URL, timeout=_HTTP_TIMEOUT)
            if resp.status_code != 200:
                return []
            data = resp.json()
            agents: list[dict] = data if isinstance(data, list) else data.get("agents", data.get("items", []))
            return [
                {
                    "source": "elizaos",
                    "external_id": a.get("id") or a.get("name", ""),
                    "name": a.get("name", ""),
                    "endpoint": a.get("endpoint"),
                    "framework": "elizaos",
                    "description": a.get("description"),
                    "raw_data": json.dumps(a),
                }
                for a in agents
                if a.get("id") or a.get("name")
            ]
        except Exception:
            return []

    async def fetch_github(
        self, http_client: httpx.AsyncClient, token: str | None = None
    ) -> list[dict[str, Any]]:
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            resp = await http_client.get(
                _GITHUB_SEARCH_URL,
                params={"q": _GITHUB_QUERY, "per_page": 50, "sort": "stars"},
                headers=headers,
                timeout=_HTTP_TIMEOUT,
            )
            if resp.status_code != 200:
                return []
            items = resp.json().get("items", [])
            return [
                {
                    "source": "github",
                    "external_id": item["full_name"],
                    "name": item["full_name"].split("/")[-1],
                    "endpoint": None,
                    "framework": "github",
                    "description": item.get("description"),
                    "raw_data": json.dumps({
                        "url": item["html_url"],
                        "stars": item["stargazers_count"],
                        "forks": item["forks_count"],
                    }),
                }
                for item in items
            ]
        except Exception:
            return []

    async def store_results(
        self, db, agents: list[dict[str, Any]]
    ) -> tuple[int, int]:
        """Stocke les agents non existants. Retourne (total_found, new_stored)."""
        new_count = 0
        for agent in agents:
            existing = await db._fetchone(
                "SELECT scout_id FROM hub_scout_results WHERE source=? AND external_id=?",
                (agent["source"], agent["external_id"]),
            )
            if existing is not None:
                continue
            await db.raw_execute(
                "INSERT INTO hub_scout_results"
                "(scout_id, source, external_id, name, endpoint, framework,"
                " description, raw_data, status, discovered_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    uuid.uuid4().hex,
                    agent["source"],
                    agent["external_id"],
                    agent.get("name"),
                    agent.get("endpoint"),
                    agent.get("framework"),
                    agent.get("description"),
                    agent.get("raw_data", "{}"),
                    "unverified",
                    int(time.time()),
                ),
            )
            new_count += 1
        return len(agents), new_count

    async def run(self, db, http_client: httpx.AsyncClient) -> dict[str, int]:
        """Exécute le scout complet. Retourne les stats agrégées."""
        total_found = 0
        total_new = 0
        for fetch_fn in (self.fetch_agentverse, self.fetch_elizaos, self.fetch_github):
            try:
                agents = await fetch_fn(http_client)
            except Exception:
                agents = []
            if agents:
                found, new = await self.store_results(db, agents)
                total_found += found
                total_new += new
        return {"agents_found": total_found, "agents_new": total_new}


# ─── Router FastAPI ───────────────────────────────────────────────────────────

scout_router = APIRouter(prefix="/api/hub/scout", tags=["hub-scout"])


@scout_router.post("/run", status_code=202)
async def run_scout(background_tasks: BackgroundTasks):
    """Déclenche un run scout en arrière-plan."""
    async def _task():
        async with httpx.AsyncClient() as client:
            await HubScout().run(db, client)

    background_tasks.add_task(_task)
    return {"status": "running", "message": "Scout started in background"}


@scout_router.get("/results")
async def get_scout_results(limit: int = 50, offset: int = 0, source: str | None = None):
    """Liste les profils découverts (paginé, filtrable par source)."""
    if source:
        rows = await db.raw_execute_fetchall(
            "SELECT * FROM hub_scout_results WHERE source=? ORDER BY discovered_at DESC LIMIT ? OFFSET ?",
            (source, limit, offset),
        )
    else:
        rows = await db.raw_execute_fetchall(
            "SELECT * FROM hub_scout_results ORDER BY discovered_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
    return {"results": [dict(r) for r in rows], "count": len(rows)}


@scout_router.get("/status")
async def get_scout_status():
    """Statistiques globales du scout."""
    rows = await db.raw_execute_fetchall(
        "SELECT source, COUNT(*) as cnt FROM hub_scout_results GROUP BY source"
    )
    total_row = await db._fetchone(
        "SELECT COUNT(*) as total FROM hub_scout_results WHERE status='unverified'"
    )
    by_source = {r["source"]: r["cnt"] for r in rows} if rows else {}
    total = total_row["total"] if total_row else 0
    return {"total_unverified": total, "by_source": by_source}
