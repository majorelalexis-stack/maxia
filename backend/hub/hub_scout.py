"""MAXIA Hub R0 — Scout actif.

Découvre des agents AI sur 3 registres (Agentverse, ElizaOS, Smithery),
score par mots-clés sans LLM, stocke les éligibles, déclenche les invitations A2A.
Anti-ban : cooldown 6h entre runs, max 10 invitations par run, délai 2s entre contacts.
"""
import asyncio
import json
import os
import time
import uuid
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException

from core.database import db

# ─── Config ──────────────────────────────────────────────────────────────────

_AGENTVERSE_URL = "https://agentverse.ai/v1/search/agents"
_ELIZAOS_URL = "https://raw.githubusercontent.com/elizaos-plugins/registry/main/generated-registry.json"
_SMITHERY_URL = "https://api.smithery.ai/servers"
_MCP_REGISTRY_URL = "https://registry.modelcontextprotocol.io/v0/servers"
_HTTP_TIMEOUT = 12.0
_RUN_COOLDOWN_SECONDS = 6 * 3600  # 6h entre deux runs
_MAX_INVITES_PER_RUN = 10
_INVITE_DELAY_SECONDS = 2.0

# ─── Scoring mots-clés ────────────────────────────────────────────────────────

_POSITIVE_WORDS = {
    "trade", "trading", "data", "defi", "audit", "code", "llm", "blockchain",
    "api", "orchestrat", "analyt", "monitor", "execut", "automat", "financ",
    "market", "swap", "price", "portfolio", "agent", "task", "workflow",
    "search", "retriev", "inference", "compute", "deploy",
}
_REJECT_WORDS = {
    "waifu", "sing", "dance", "meme", "influenc", "nft drop",
    "companion", "roleplay", "entertainment", "vtuber", "avatar",
}
_SCORE_THRESHOLD = 2

# Cooldown in-memory (reset au restart serveur — acceptable)
_last_run_ts: float = 0.0


def _score_agent(name: str | None, description: str | None) -> int:
    """Score 0..N sur mots-clés. 0 si mot éliminatoire ou description absente."""
    if not description or len(description.strip()) < 20:
        return 0
    text = f"{name or ''} {description or ''}".lower()
    for word in _REJECT_WORDS:
        if word in text:
            return 0
    return sum(1 for word in _POSITIVE_WORDS if word in text)


# ─── Classe principale ────────────────────────────────────────────────────────

class HubScout:

    async def fetch_agentverse(self, http_client: httpx.AsyncClient) -> list[dict[str, Any]]:
        try:
            resp = await http_client.post(
                _AGENTVERSE_URL,
                json={"search_text": "", "cutoff": "none", "sort": "interactions", "direction": "desc"},
                timeout=_HTTP_TIMEOUT,
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
            # Format 2026: {"registry": {"@plugin/name": {"description": ..., "git": {...}}}}
            registry = data.get("registry") if isinstance(data, dict) else None
            if registry and isinstance(registry, dict):
                result = []
                for plugin_name, meta in registry.items():
                    if not isinstance(meta, dict):
                        continue
                    description = meta.get("description") or ""
                    git = meta.get("git", {})
                    repo = git.get("repo", "") if isinstance(git, dict) else ""
                    result.append({
                        "source": "elizaos",
                        "external_id": plugin_name,
                        "name": plugin_name.lstrip("@"),
                        "endpoint": None,
                        "framework": "elizaos",
                        "description": description,
                        "raw_data": json.dumps({"repo": repo}),
                    })
                return result
            # Fallback: format liste
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

    async def fetch_mcp_registry(self, http_client: httpx.AsyncClient) -> list[dict[str, Any]]:
        try:
            resp = await http_client.get(
                _MCP_REGISTRY_URL, params={"limit": 100}, timeout=_HTTP_TIMEOUT
            )
            if resp.status_code != 200:
                return []
            servers = resp.json().get("servers", [])
            result = []
            for entry in servers:
                s = entry.get("server", {})
                if not s:
                    continue
                remotes = s.get("remotes", [])
                endpoint = remotes[0].get("url") if remotes else None
                result.append({
                    "source": "mcp_registry",
                    "external_id": s.get("name", ""),
                    "name": s.get("title") or s.get("name", ""),
                    "endpoint": endpoint,
                    "framework": "mcp",
                    "description": s.get("description"),
                    "raw_data": json.dumps({"name": s.get("name"), "remotes": remotes}),
                })
            return result
        except Exception:
            return []

    async def fetch_smithery(self, http_client: httpx.AsyncClient) -> list[dict[str, Any]]:
        token = os.getenv("SMITHERY_API_KEY")
        if not token:
            return []
        headers = {"Authorization": f"Bearer {token}"}
        try:
            resp = await http_client.get(
                _SMITHERY_URL,
                params={"isDeployed": "true", "remote": "true", "pageSize": 100},
                headers=headers,
                timeout=_HTTP_TIMEOUT,
            )
            if resp.status_code != 200:
                return []
            servers = resp.json().get("servers", [])
            return [
                {
                    "source": "smithery",
                    "external_id": s.get("qualifiedName") or s.get("id", ""),
                    "name": s.get("displayName") or s.get("qualifiedName", ""),
                    "endpoint": s.get("url"),
                    "framework": "mcp",
                    "description": s.get("description"),
                    "raw_data": json.dumps({
                        "url": s.get("url"),
                        "verified": s.get("isVerified"),
                    }),
                }
                for s in servers
                if s.get("qualifiedName") or s.get("id")
            ]
        except Exception:
            return []

    async def store_results(
        self, db, agents: list[dict[str, Any]]
    ) -> tuple[int, int, int]:
        """Score + stocke les nouveaux agents. Retourne (total_found, new_eligible, new_rejected)."""
        new_eligible = 0
        new_rejected = 0
        for agent in agents:
            existing = await db._fetchone(
                "SELECT scout_id FROM hub_scout_results WHERE source=? AND external_id=?",
                (agent["source"], agent["external_id"]),
            )
            if existing is not None:
                continue
            score = _score_agent(agent.get("name"), agent.get("description"))
            status = "eligible" if score >= _SCORE_THRESHOLD else "rejected"
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
                    status,
                    int(time.time()),
                ),
            )
            if status == "eligible":
                new_eligible += 1
            else:
                new_rejected += 1
        return len(agents), new_eligible, new_rejected

    async def invite_eligible(self, db, http_client: httpx.AsyncClient) -> int:
        """Invite les agents éligibles non encore contactés. Max 10, délai 2s entre chaque."""
        from hub.hub_invite import HubInviter
        inviter = HubInviter()
        rows = await db.raw_execute_fetchall(
            "SELECT r.scout_id, r.name, r.endpoint"
            " FROM hub_scout_results r"
            " LEFT JOIN hub_invitations i ON i.scout_id = r.scout_id AND i.method='a2a'"
            " WHERE r.status='eligible' AND r.endpoint IS NOT NULL AND i.invite_id IS NULL"
            " ORDER BY r.discovered_at ASC"
            " LIMIT ?",
            (_MAX_INVITES_PER_RUN,),
        )
        sent = 0
        for row in rows:
            row = dict(row)
            ok = await inviter.send_a2a_invite(
                http_client, row["endpoint"], row["name"] or "Agent", row["scout_id"], db=db
            )
            if ok:
                sent += 1
            await asyncio.sleep(_INVITE_DELAY_SECONDS)
        return sent

    async def run(self, db, http_client: httpx.AsyncClient) -> dict[str, int]:
        """Fetch 3 sources → score+store → invite éligibles. Retourne les stats."""
        total_found = 0
        total_eligible = 0
        total_rejected = 0
        for fetch_fn in (self.fetch_agentverse, self.fetch_elizaos, self.fetch_smithery, self.fetch_mcp_registry):
            try:
                agents = await fetch_fn(http_client)
            except Exception:
                agents = []
            if agents:
                found, eligible, rejected = await self.store_results(db, agents)
                total_found += found
                total_eligible += eligible
                total_rejected += rejected
        invites_sent = await self.invite_eligible(db, http_client)
        return {
            "agents_found": total_found,
            "agents_eligible": total_eligible,
            "agents_rejected": total_rejected,
            "invites_sent": invites_sent,
        }


# ─── Router FastAPI ───────────────────────────────────────────────────────────

scout_router = APIRouter(prefix="/api/hub/scout", tags=["hub-scout"])


@scout_router.post("/run", status_code=202)
async def run_scout(background_tasks: BackgroundTasks):
    """Déclenche un run scout en arrière-plan. Cooldown 6h entre deux runs."""
    global _last_run_ts
    now = time.time()
    remaining = _RUN_COOLDOWN_SECONDS - (now - _last_run_ts)
    if remaining > 0:
        raise HTTPException(
            status_code=429,
            detail=f"Cooldown actif — prochain run dans {int(remaining // 60)} min",
        )
    _last_run_ts = now

    async def _task():
        async with httpx.AsyncClient() as client:
            await HubScout().run(db, client)

    background_tasks.add_task(_task)
    return {"status": "running", "message": "Scout started in background"}


@scout_router.get("/results")
async def get_scout_results(
    limit: int = 50,
    offset: int = 0,
    source: str | None = None,
    status: str | None = None,
):
    """Liste les profils découverts (paginé, filtrable par source et status)."""
    conditions = []
    params: list[Any] = []
    if source:
        conditions.append("source=?")
        params.append(source)
    if status:
        conditions.append("status=?")
        params.append(status)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params += [limit, offset]
    rows = await db.raw_execute_fetchall(
        f"SELECT * FROM hub_scout_results {where} ORDER BY discovered_at DESC LIMIT ? OFFSET ?",
        tuple(params),
    )
    return {"results": [dict(r) for r in rows], "count": len(rows)}


@scout_router.get("/status")
async def get_scout_status():
    """Statistiques globales du scout par source et par statut."""
    by_source_rows = await db.raw_execute_fetchall(
        "SELECT source, COUNT(*) as cnt FROM hub_scout_results GROUP BY source"
    )
    by_status_rows = await db.raw_execute_fetchall(
        "SELECT status, COUNT(*) as cnt FROM hub_scout_results GROUP BY status"
    )
    invite_row = await db._fetchone(
        "SELECT COUNT(*) as cnt FROM hub_invitations WHERE method='a2a'"
    )
    cooldown_remaining = max(0, int(_RUN_COOLDOWN_SECONDS - (time.time() - _last_run_ts)))
    return {
        "by_source": {r["source"]: r["cnt"] for r in by_source_rows} if by_source_rows else {},
        "by_status": {r["status"]: r["cnt"] for r in by_status_rows} if by_status_rows else {},
        "invitations_sent": invite_row["cnt"] if invite_row else 0,
        "cooldown_remaining_seconds": cooldown_remaining,
        "smithery_enabled": bool(os.getenv("SMITHERY_API_KEY")),
    }
