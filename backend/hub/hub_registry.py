"""MAXIA Hub Registry — Phase 1 : registre d'état civil pour agents AI autonomes.

Routes :
  POST /api/hub/challenge     → génère un challenge ed25519 (step 1 registration)
  POST /api/hub/register      → vérifie challenge + crée l'agent
  POST /api/hub/heartbeat     → ping de vie, update last_heartbeat
  GET  /api/hub/agent/{hub_id} → profil public
  GET  /api/hub/agents         → liste paginée (skip, limit, chain, framework, min_score)
"""
from __future__ import annotations

import json
import logging
import secrets
import time
import uuid
from typing import Optional

import base58
from fastapi import APIRouter, HTTPException, Query
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

from agents.agent_permissions import generate_did, generate_uaid
from core.database import db
from hub.hub_models import (
    HubAgentProfile,
    HubChallengeRequest,
    HubChallengeResponse,
    HubHeartbeatRequest,
    HubHeartbeatResponse,
    HubRegisterRequest,
    HubRegisterResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/hub", tags=["hub"])

# TTL challenge en secondes (5 minutes)
_CHALLENGE_TTL = 300
# Fenêtre d'acceptation du timestamp heartbeat (secondes)
_HEARTBEAT_WINDOW = 60


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _verify_ed25519(public_key_b58: str, message: str, sig_b58: str) -> bool:
    """Vérifie une signature ed25519. Retourne True si valide, False sinon.

    Même logique que agent_permissions.py (VerifyKey nacl + base58).
    Conservée ici pour éviter un import circulaire : agent_permissions
    importe core.database, qui ne doit pas dépendre de hub.
    """
    try:
        pk_bytes = base58.b58decode(public_key_b58)
        sig_bytes = base58.b58decode(sig_b58)
        VerifyKey(pk_bytes).verify(message.encode(), sig_bytes)
        return True
    except (BadSignatureError, Exception):
        return False


def _row_to_profile(row: dict) -> HubAgentProfile:
    """Convertit une ligne DB hub_agents en HubAgentProfile."""
    caps_raw = row.get("capabilities", "[]")
    if isinstance(caps_raw, str):
        try:
            caps = json.loads(caps_raw)
        except (ValueError, TypeError):
            caps = []
    else:
        caps = list(caps_raw)

    return HubAgentProfile(
        hub_id=row["hub_id"],
        did=row["did"],
        uaid=row.get("uaid"),
        name=row["name"],
        endpoint=row["endpoint"],
        framework=row["framework"],
        capabilities=caps,
        score=row["score"],
        uptime_30d=row["uptime_30d"],
        birth_ts=row["birth_ts"],
        last_heartbeat=row.get("last_heartbeat"),
        chain=row["chain"],
        corpus_opt_out=bool(row.get("corpus_opt_out", 0)),
    )


async def _compute_uptime_30d(hub_id: str) -> float:
    """Calcule l'uptime sur 30 jours à partir de hub_heartbeats.

    Logique simplifiée Phase 1 : ratio heartbeats reçus / attendus
    sur une fenêtre de 30 jours (attend 1 heartbeat / heure max).
    """
    cutoff = int(time.time()) - 30 * 24 * 3600
    rows = await db.raw_execute_fetchall(
        "SELECT ts FROM hub_heartbeats WHERE hub_id=? AND ts>=? ORDER BY ts ASC",
        (hub_id, cutoff),
    )
    if not rows:
        return 0.0
    # Nombre d'heures dans la fenêtre d'observation
    hours_window = 30 * 24
    received = len(rows)
    # Uptime = ratio clamped 0-100
    ratio = min(received / hours_window, 1.0) * 100.0
    return round(ratio, 2)


# ─── Endpoints ───────────────────────────────────────────────────────────────


@router.post("/challenge", response_model=HubChallengeResponse)
async def challenge_endpoint(req: HubChallengeRequest) -> HubChallengeResponse:
    """Génère un challenge 32 bytes pour prouver la possession de la clé privée."""
    challenge_id = uuid.uuid4().hex
    challenge_hex = secrets.token_hex(32)  # 32 bytes = 64 hex chars
    now = int(time.time())
    expires_at = now + _CHALLENGE_TTL

    await db.raw_execute(
        "INSERT INTO hub_challenges(challenge_id, challenge_hex, endpoint, public_key, created_at, used)"
        " VALUES(?,?,?,?,?,?)",
        (challenge_id, challenge_hex, req.endpoint, req.public_key, now, 0),
    )

    return HubChallengeResponse(
        challenge_id=challenge_id,
        challenge=challenge_hex,
        expires_at=expires_at,
    )


@router.post("/register", response_model=HubRegisterResponse)
async def register_endpoint(req: HubRegisterRequest) -> HubRegisterResponse:
    """Enregistre un agent après vérification du challenge ed25519.

    L'endpoint est intentionnellement public (pas de JWT requis) :
    le challenge/response ed25519 constitue lui-même la preuve de possession
    de la clé privée, ce qui suffit à authentifier l'agent autonome.
    """
    # 1. Récupérer le challenge
    row = await db._fetchone(
        "SELECT challenge_id, challenge_hex, public_key, created_at, used"
        " FROM hub_challenges WHERE challenge_id=?",
        (req.challenge_id,),
    )
    if row is None:
        raise HTTPException(status_code=400, detail="Challenge not found")

    row_dict = dict(row)

    # 2. Vérifier que le challenge n'est pas expiré
    if int(time.time()) - row_dict["created_at"] > _CHALLENGE_TTL:
        raise HTTPException(status_code=400, detail="Challenge expired")

    # 3. Vérifier que le challenge n'a pas déjà été utilisé
    if row_dict["used"]:
        raise HTTPException(status_code=400, detail="Challenge already used")

    # 4. Vérifier la signature ed25519
    valid = _verify_ed25519(req.public_key, row_dict["challenge_hex"], req.challenge_sig)
    if not valid:
        raise HTTPException(status_code=400, detail="Invalid signature")

    # 5. Vérifier l'unicité du wallet
    existing = await db._fetchone(
        "SELECT hub_id FROM hub_agents WHERE wallet=?",
        (req.wallet,),
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="Wallet already registered")

    # 6. Marquer le challenge comme utilisé (immutabilité : UPDATE, pas replace)
    await db.raw_execute(
        "UPDATE hub_challenges SET used=1 WHERE challenge_id=?",
        (req.challenge_id,),
    )

    # 7. Créer l'agent
    hub_id = uuid.uuid4().hex
    # Utilise generate_did() de agent_permissions (W3C DID format)
    did = generate_did(hub_id)
    # Utilise generate_uaid() de agent_permissions (HCS-14, immuable)
    uaid = generate_uaid(hub_id, req.name, req.wallet)
    now = int(time.time())
    caps_json = json.dumps(req.capabilities)

    await db.raw_execute(
        "INSERT INTO hub_agents"
        "(hub_id, did, uaid, name, endpoint, public_key, wallet, chain, framework,"
        " capabilities, manifest_url, corpus_opt_out, birth_ts, uptime_30d, score, status)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            hub_id, did, uaid, req.name, req.endpoint, req.public_key,
            req.wallet, req.chain, req.framework,
            caps_json, req.manifest_url, int(req.corpus_opt_out),
            now, 0.0, 0, "active",
        ),
    )

    return HubRegisterResponse(
        hub_id=hub_id,
        did=did,
        uaid=uaid,
        birth_block=None,
        score=0,
        message="Agent registered successfully",
    )


@router.post("/heartbeat", response_model=HubHeartbeatResponse)
async def heartbeat_endpoint(req: HubHeartbeatRequest) -> HubHeartbeatResponse:
    """Reçoit un ping de vie de l'agent et met à jour last_heartbeat."""
    # 1. Vérifier que le timestamp est dans la fenêtre
    if abs(int(time.time()) - req.timestamp) > _HEARTBEAT_WINDOW:
        raise HTTPException(status_code=400, detail="Timestamp out of range")

    # 2. Récupérer l'agent
    row = await db._fetchone(
        "SELECT hub_id, public_key, uptime_30d FROM hub_agents WHERE hub_id=?",
        (req.hub_id,),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    row_dict = dict(row)

    # 3. Vérifier la signature (hub_id + str(timestamp))
    message = req.hub_id + str(req.timestamp)
    if not _verify_ed25519(row_dict["public_key"], message, req.sig):
        raise HTTPException(status_code=401, detail="Invalid signature")

    now = int(time.time())

    # 4. Enregistrer le heartbeat
    await db.raw_execute(
        "INSERT INTO hub_heartbeats(hub_id, ts) VALUES(?,?)",
        (req.hub_id, now),
    )

    # 5. Mettre à jour last_heartbeat
    await db.raw_execute(
        "UPDATE hub_agents SET last_heartbeat=? WHERE hub_id=?",
        (now, req.hub_id),
    )

    # 6. Calculer uptime_30d
    uptime = await _compute_uptime_30d(req.hub_id)

    # 7. Mettre à jour uptime_30d dans la table
    await db.raw_execute(
        "UPDATE hub_agents SET uptime_30d=? WHERE hub_id=?",
        (uptime, req.hub_id),
    )

    return HubHeartbeatResponse(ok=True, uptime_30d=uptime)


@router.get("/agent/{hub_id}", response_model=HubAgentProfile)
async def get_agent_endpoint(hub_id: str) -> HubAgentProfile:
    """Retourne le profil public d'un agent."""
    row = await db._fetchone(
        "SELECT hub_id, did, name, endpoint, public_key, wallet, chain, framework,"
        " capabilities, manifest_url, corpus_opt_out, birth_ts, last_heartbeat,"
        " uptime_30d, score, status"
        " FROM hub_agents WHERE hub_id=?",
        (hub_id,),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    return _row_to_profile(dict(row))


@router.get("/agents", response_model=list[HubAgentProfile])
async def list_agents_endpoint(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    chain: Optional[str] = Query(default=None),
    framework: Optional[str] = Query(default=None),
    min_score: int = Query(default=0, ge=0),
) -> list[HubAgentProfile]:
    """Liste les agents enregistrés avec filtres optionnels."""
    conditions = ["status='active'", "score>=?"]
    params: list = [min_score]

    if chain is not None:
        conditions.append("chain=?")
        params.append(chain)

    if framework is not None:
        conditions.append("framework=?")
        params.append(framework)

    where = " AND ".join(conditions)
    params.extend([limit, skip])

    rows = await db.raw_execute_fetchall(
        f"SELECT hub_id, did, name, endpoint, public_key, wallet, chain, framework,"
        f" capabilities, manifest_url, corpus_opt_out, birth_ts, last_heartbeat,"
        f" uptime_30d, score, status"
        f" FROM hub_agents WHERE {where}"
        f" ORDER BY score DESC, birth_ts ASC"
        f" LIMIT ? OFFSET ?",
        tuple(params),
    )

    return [_row_to_profile(dict(r)) for r in rows]
