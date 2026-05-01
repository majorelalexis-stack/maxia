"""MAXIA Hub R2 — Boost GitHub (.well-known/maxia.json + stars/forks/commits 90j).

Flux :
1. Fetch https://raw.githubusercontent.com/<owner>/<repo>/main/.well-known/maxia.json
2. Vérifie sig ed25519 = sign(hub_id + str(timestamp)), TTL 7 jours
3. Cross-check public_key == hub_agents.public_key
4. Fetch stats : stars, forks, commits sur 90 jours
5. Calcule boost 0-10, stocke dans hub_agents.score_r2_boost
"""
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

import base58
import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

from core.database import db

_GH_API = "https://api.github.com"
_GH_RAW = "https://raw.githubusercontent.com"
_HTTP_TIMEOUT = 12.0
_WELL_KNOWN_TTL_DAYS = 7

_STARS_SAT = 100
_FORKS_SAT = 30
_COMMITS_SAT = 50
_BOOST_MAX = 10.0
_W_STARS = 0.5
_W_FORKS = 0.3
_W_COMMITS = 0.2


# ─── Dataclass ───────────────────────────────────────────────────────────────

@dataclass
class GitHubActivity:
    owner: str
    repo: str
    stars: int
    forks: int
    commits_90d: int
    error: str | None = field(default=None)


# ─── ed25519 verify (copie locale — évite import circulaire) ─────────────────

def _verify_ed25519(public_key_b58: str, message: str, sig_b58: str) -> bool:
    try:
        VerifyKey(base58.b58decode(public_key_b58)).verify(
            message.encode(), base58.b58decode(sig_b58)
        )
        return True
    except (BadSignatureError, Exception):
        return False


# ─── verify_well_known ───────────────────────────────────────────────────────

def verify_well_known(data: dict, hub_id: str, public_key_b58: str) -> bool:
    """Vérifie le fichier .well-known/maxia.json :
    - hub_id correspond
    - timestamp dans la fenêtre TTL
    - sig valide pour hub_id + str(timestamp)
    """
    try:
        if not data.get("sig") or not data.get("timestamp"):
            return False
        if data.get("hub_id") != hub_id:
            return False
        ts = int(data["timestamp"])
        age_days = (time.time() - ts) / 86400
        if age_days > _WELL_KNOWN_TTL_DAYS:
            return False
        return _verify_ed25519(public_key_b58, hub_id + str(ts), data["sig"])
    except Exception:
        return False


# ─── Fetcher ─────────────────────────────────────────────────────────────────

class GitHubFetcher:
    async def fetch_well_known(
        self, github_repo: str, http_client: httpx.AsyncClient
    ) -> dict | None:
        url = f"{_GH_RAW}/{github_repo}/main/.well-known/maxia.json"
        try:
            resp = await http_client.get(url, timeout=_HTTP_TIMEOUT)
            if resp.status_code != 200:
                return None
            return resp.json()
        except Exception:
            return None

    async def fetch_repo_stats(
        self, owner: str, repo: str, http_client: httpx.AsyncClient
    ) -> GitHubActivity:
        url = f"{_GH_API}/repos/{owner}/{repo}"
        try:
            resp = await http_client.get(
                url,
                headers={"Accept": "application/vnd.github+json"},
                timeout=_HTTP_TIMEOUT,
            )
            if resp.status_code != 200:
                return GitHubActivity(owner=owner, repo=repo, stars=0, forks=0,
                                      commits_90d=0, error=f"http {resp.status_code}")
            data = resp.json()
            return GitHubActivity(
                owner=owner,
                repo=repo,
                stars=data.get("stargazers_count", 0),
                forks=data.get("forks_count", 0),
                commits_90d=0,  # rempli séparément
            )
        except Exception as exc:
            return GitHubActivity(owner=owner, repo=repo, stars=0, forks=0,
                                  commits_90d=0, error=str(exc))

    async def fetch_commits_90d(
        self, owner: str, repo: str, http_client: httpx.AsyncClient
    ) -> int:
        since = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        url = f"{_GH_API}/repos/{owner}/{repo}/commits"
        try:
            resp = await http_client.get(
                url,
                params={"since": since, "per_page": 100},
                headers={"Accept": "application/vnd.github+json"},
                timeout=_HTTP_TIMEOUT,
            )
            if resp.status_code != 200:
                return 0
            return len(resp.json())
        except Exception:
            return 0


# ─── Boost computation ───────────────────────────────────────────────────────

def compute_r2_boost(activity: GitHubActivity) -> float:
    if activity.error:
        return 0.0
    stars_score = min(1.0, activity.stars / _STARS_SAT)
    forks_score = min(1.0, activity.forks / _FORKS_SAT)
    commits_score = min(1.0, activity.commits_90d / _COMMITS_SAT)
    raw = (_W_STARS * stars_score + _W_FORKS * forks_score + _W_COMMITS * commits_score) * _BOOST_MAX
    return min(_BOOST_MAX, round(raw, 4))


# ─── Apply boost ─────────────────────────────────────────────────────────────

async def apply_r2_boost(
    db, hub_id: str, github_repo: str, http_client: httpx.AsyncClient
) -> dict:
    hub_row = await db._fetchone(
        "SELECT hub_id, public_key FROM hub_agents WHERE hub_id=?", (hub_id,)
    )
    if hub_row is None:
        raise HTTPException(status_code=404, detail="Hub agent not found")

    hub_row = dict(hub_row)
    fetcher = GitHubFetcher()

    well_known = await fetcher.fetch_well_known(github_repo, http_client)
    if well_known is None:
        raise HTTPException(
            status_code=422,
            detail=f".well-known/maxia.json not found in {github_repo}",
        )

    if not verify_well_known(well_known, hub_id, hub_row["public_key"]):
        raise HTTPException(status_code=401, detail="Invalid or expired .well-known signature")

    owner, repo = github_repo.split("/", 1)
    activity = await fetcher.fetch_repo_stats(owner, repo, http_client)
    commits = await fetcher.fetch_commits_90d(owner, repo, http_client)
    activity.commits_90d = commits

    boost = compute_r2_boost(activity)

    await db.raw_execute(
        "UPDATE hub_agents SET score_r2_boost=?, github_repo=? WHERE hub_id=?",
        (boost, github_repo, hub_id),
    )

    return {
        "hub_id": hub_id,
        "github_repo": github_repo,
        "stars": activity.stars,
        "forks": activity.forks,
        "commits_90d": activity.commits_90d,
        "boost": boost,
    }


# ─── Router ──────────────────────────────────────────────────────────────────

r2_router = APIRouter(prefix="/api/hub/r2", tags=["hub-r2"])


@r2_router.post("/refresh/{hub_id}", status_code=202)
async def refresh_r2(hub_id: str, github_repo: str, background_tasks: BackgroundTasks):
    """Déclenche le calcul du boost R2 en arrière-plan."""
    async def _task():
        async with httpx.AsyncClient() as client:
            await apply_r2_boost(db, hub_id, github_repo, client)

    background_tasks.add_task(_task)
    return {"status": "running", "hub_id": hub_id, "github_repo": github_repo}


@r2_router.get("/{hub_id}")
async def get_r2_detail(hub_id: str):
    """Retourne le boost R2 stocké pour un agent."""
    row = await db._fetchone(
        "SELECT hub_id, github_repo, score_r2_boost FROM hub_agents WHERE hub_id=?",
        (hub_id,),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Hub agent not found")
    r = dict(row)
    return {
        "hub_id": r["hub_id"],
        "github_repo": r.get("github_repo"),
        "score_r2_boost": r.get("score_r2_boost", 0.0),
    }
