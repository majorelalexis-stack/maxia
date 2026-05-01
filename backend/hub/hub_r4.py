"""MAXIA Hub R4 — Boost registres externes (Agentverse + ElizaOS).

Signal binaire (présent/absent) + ancienneté depuis première découverte R0.
Boost plafonné à +5 points (2.5 max par registre).
Aucun LLM. Réutilise hub_scout_results pour la date de première découverte.
"""
import time
from dataclasses import dataclass, field

import httpx
from fastapi import APIRouter, HTTPException

from core.database import db

_AGENTVERSE_AGENT_URL = "https://agentverse.ai/v1/agents/{agent_id}"
_ELIZAOS_REGISTRY_URL = "https://elizaos.github.io/registry/registry.json"
_HTTP_TIMEOUT = 12.0

_PRESENCE_BONUS = 1.0           # bonus fixe si présent
_SENIORITY_MAX = 1.5            # bonus seniority max par registre
_SENIORITY_DAYS_FULL = 120      # jours pour atteindre seniority max
_BOOST_MAX_PER_REGISTRY = 2.5   # _PRESENCE_BONUS + _SENIORITY_MAX
_BOOST_MAX = 5.0


# ─── Dataclass ───────────────────────────────────────────────────────────────

@dataclass
class ExternalPresence:
    source: str
    external_id: str
    present: bool
    first_seen_days: int
    error: str | None = field(default=None)


# ─── Checker ─────────────────────────────────────────────────────────────────

class ExternalRegistryChecker:
    async def check_agentverse(
        self,
        external_id: str,
        http_client: httpx.AsyncClient,
        first_seen_days: int = 0,
    ) -> ExternalPresence:
        url = _AGENTVERSE_AGENT_URL.format(agent_id=external_id)
        try:
            resp = await http_client.get(url, timeout=_HTTP_TIMEOUT)
            present = resp.status_code == 200
            return ExternalPresence(
                source="agentverse",
                external_id=external_id,
                present=present,
                first_seen_days=first_seen_days,
            )
        except Exception as exc:
            return ExternalPresence(source="agentverse", external_id=external_id,
                                    present=False, first_seen_days=0, error=str(exc))

    async def check_elizaos(
        self,
        external_id: str,
        http_client: httpx.AsyncClient,
        first_seen_days: int = 0,
    ) -> ExternalPresence:
        try:
            resp = await http_client.get(_ELIZAOS_REGISTRY_URL, timeout=_HTTP_TIMEOUT)
            if resp.status_code != 200:
                return ExternalPresence(source="elizaos", external_id=external_id,
                                        present=False, first_seen_days=0,
                                        error=f"http {resp.status_code}")
            data = resp.json()
            agents: list[dict] = data if isinstance(data, list) else data.get("agents", [])
            present = any(
                a.get("id") == external_id or a.get("name") == external_id
                for a in agents
            )
            return ExternalPresence(
                source="elizaos",
                external_id=external_id,
                present=present,
                first_seen_days=first_seen_days,
            )
        except Exception as exc:
            return ExternalPresence(source="elizaos", external_id=external_id,
                                    present=False, first_seen_days=0, error=str(exc))


# ─── Boost computation ───────────────────────────────────────────────────────

def compute_r4_boost(presences: list[ExternalPresence]) -> float:
    total = 0.0
    for p in presences:
        if not p.present or p.error:
            continue
        seniority = min(_SENIORITY_MAX, (p.first_seen_days / _SENIORITY_DAYS_FULL) * _SENIORITY_MAX)
        total += _PRESENCE_BONUS + seniority
    return min(_BOOST_MAX, round(total, 4))


# ─── Apply boost ─────────────────────────────────────────────────────────────

def _days_since(ts: int) -> int:
    return max(0, int((time.time() - ts) / 86400))


async def apply_r4_boost(
    db,
    hub_id: str,
    agentverse_id: str | None,
    elizaos_id: str | None,
    http_client: httpx.AsyncClient,
) -> dict:
    hub_row = await db._fetchone(
        "SELECT hub_id FROM hub_agents WHERE hub_id=?", (hub_id,)
    )
    if hub_row is None:
        raise HTTPException(status_code=404, detail="Hub agent not found")

    checker = ExternalRegistryChecker()
    presences: list[ExternalPresence] = []

    if agentverse_id:
        scout_row = await db._fetchone(
            "SELECT discovered_at FROM hub_scout_results"
            " WHERE source='agentverse' AND external_id=?",
            (agentverse_id,),
        )
        days = _days_since(dict(scout_row)["discovered_at"]) if scout_row else 0
        p = await checker.check_agentverse(agentverse_id, http_client, first_seen_days=days)
        presences.append(p)
        await db.raw_execute(
            "INSERT INTO hub_r4_presence(hub_id, source, external_id, present,"
            " first_seen_days, checked_at)"
            " VALUES(?,?,?,?,?,?)"
            " ON CONFLICT(hub_id, source) DO UPDATE SET"
            " present=excluded.present, first_seen_days=excluded.first_seen_days,"
            " checked_at=excluded.checked_at",
            (hub_id, "agentverse", agentverse_id, int(p.present), days, int(time.time())),
        )

    if elizaos_id:
        scout_row = await db._fetchone(
            "SELECT discovered_at FROM hub_scout_results"
            " WHERE source='elizaos' AND external_id=?",
            (elizaos_id,),
        )
        days = _days_since(dict(scout_row)["discovered_at"]) if scout_row else 0
        p = await checker.check_elizaos(elizaos_id, http_client, first_seen_days=days)
        presences.append(p)
        await db.raw_execute(
            "INSERT INTO hub_r4_presence(hub_id, source, external_id, present,"
            " first_seen_days, checked_at)"
            " VALUES(?,?,?,?,?,?)"
            " ON CONFLICT(hub_id, source) DO UPDATE SET"
            " present=excluded.present, first_seen_days=excluded.first_seen_days,"
            " checked_at=excluded.checked_at",
            (hub_id, "elizaos", elizaos_id, int(p.present), days, int(time.time())),
        )

    boost = compute_r4_boost(presences)

    await db.raw_execute(
        "UPDATE hub_agents SET score_r4_ext=? WHERE hub_id=?", (boost, hub_id)
    )

    av = next((p for p in presences if p.source == "agentverse"), None)
    el = next((p for p in presences if p.source == "elizaos"), None)
    return {
        "hub_id": hub_id,
        "boost": boost,
        "agentverse_present": av.present if av else None,
        "elizaos_present": el.present if el else None,
    }


# ─── Router ──────────────────────────────────────────────────────────────────

r4_router = APIRouter(prefix="/api/hub/r4", tags=["hub-r4"])


@r4_router.post("/link")
async def link_external(payload: dict):
    """Lie les IDs externes d'un agent Hub et calcule le boost R4."""
    hub_id = payload.get("hub_id", "")
    if not hub_id:
        raise HTTPException(status_code=422, detail="hub_id requis")
    async with httpx.AsyncClient() as client:
        return await apply_r4_boost(
            db, hub_id,
            agentverse_id=payload.get("agentverse_id"),
            elizaos_id=payload.get("elizaos_id"),
            http_client=client,
        )


@r4_router.get("/{hub_id}")
async def get_r4_detail(hub_id: str):
    """Retourne le boost R4 et les présences externes d'un agent."""
    row = await db._fetchone(
        "SELECT hub_id, score_r4_ext FROM hub_agents WHERE hub_id=?", (hub_id,)
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Hub agent not found")
    presences = await db.raw_execute_fetchall(
        "SELECT source, external_id, present, first_seen_days, checked_at"
        " FROM hub_r4_presence WHERE hub_id=?",
        (hub_id,),
    )
    r = dict(row)
    return {
        "hub_id": r["hub_id"],
        "score_r4_ext": r.get("score_r4_ext", 0.0),
        "presences": [dict(p) for p in presences],
    }
