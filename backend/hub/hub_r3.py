"""MAXIA Hub R3 — EAS attestations Base mainnet.

Flux :
1. POST /api/hub/r3/attest {hub_id, uid} — l'agent soumet un UID d'attestation
2. Fetch via EAS scan GraphQL (base.easscan.org) — pas de décodage ABI
3. Vérifie : recipient == wallet agent, non révoquée, non expirée, schema MAXIA
4. Extrait score (0-100) depuis decodedDataJson
5. Poids anti-sybil = min(1, attestor_hub_score / 50) ; 0.1 si attesteur hors Hub
6. Contribution = (score/100) * weight * 5 ; total plafonné à 20
7. Stocke dans hub_eas_attestations, met à jour hub_agents.score_r3_eas
"""
import json
import os
import time
from dataclasses import dataclass, field

import httpx
from fastapi import APIRouter, HTTPException

from core.database import db

_EASSCAN_URL = os.getenv("EAS_EASSCAN_URL", "https://base.easscan.org/graphql")
_EAS_SCHEMA_ID = os.getenv("EAS_MAXIA_SCHEMA_ID", "")  # "" = skip schema check
_BOOST_MAX = 20.0
_MAX_PER_ATTESTATION = 5.0
_ATTESTOR_WEIGHT_THRESHOLD = 50.0  # score >= 50 → poids plein
_ATTESTOR_EXTERNAL_WEIGHT = 0.1    # attesteur non enregistré dans Hub
_HTTP_TIMEOUT = 12.0

_GQL_QUERY = """
query GetAttestation($id: String!) {
  attestation(id: $id) {
    id
    recipient
    attester
    schemaId
    revoked
    time
    expirationTime
    decodedDataJson
  }
}
"""


# ─── Dataclass ───────────────────────────────────────────────────────────────

@dataclass
class EASAttestation:
    uid: str
    recipient: str
    attester: str
    schema_id: str
    revoked: bool
    time: int
    expiration_time: int
    attestation_score: int
    error: str | None = field(default=None)


# ─── parse_attestation_score ─────────────────────────────────────────────────

def parse_attestation_score(decoded_data_json: str, default: int = 50) -> int:
    """Extrait le champ 'score' (uint8) depuis decodedDataJson EAS."""
    try:
        items = json.loads(decoded_data_json)
        for item in items:
            if item.get("name") == "score":
                val = item.get("value")
                if isinstance(val, dict):
                    val = val.get("value", default)
                raw = int(val)
                return max(0, min(100, raw))
        return default
    except Exception:
        return default


# ─── verify_attestation ──────────────────────────────────────────────────────

def verify_attestation(
    att: EASAttestation,
    expected_recipient: str,
    expected_schema_id: str,
) -> bool:
    if att.error:
        return False
    if att.revoked:
        return False
    if att.expiration_time != 0 and att.expiration_time < int(time.time()):
        return False
    if att.recipient.lower() != expected_recipient.lower():
        return False
    if expected_schema_id and att.schema_id.lower() != expected_schema_id.lower():
        return False
    return True


# ─── EASFetcher ──────────────────────────────────────────────────────────────

class EASFetcher:
    async def fetch_attestation(
        self, uid: str, http_client: httpx.AsyncClient
    ) -> EASAttestation:
        try:
            resp = await http_client.post(
                _EASSCAN_URL,
                json={"query": _GQL_QUERY, "variables": {"id": uid}},
                headers={"Content-Type": "application/json"},
                timeout=_HTTP_TIMEOUT,
            )
            if resp.status_code != 200:
                return EASAttestation(uid=uid, recipient="", attester="", schema_id="",
                                      revoked=False, time=0, expiration_time=0,
                                      attestation_score=0, error=f"http {resp.status_code}")
            data = resp.json().get("data", {}).get("attestation")
            if data is None:
                return EASAttestation(uid=uid, recipient="", attester="", schema_id="",
                                      revoked=False, time=0, expiration_time=0,
                                      attestation_score=0, error="attestation not found")
            score = parse_attestation_score(data.get("decodedDataJson", ""))
            return EASAttestation(
                uid=data["id"],
                recipient=data.get("recipient", ""),
                attester=data.get("attester", ""),
                schema_id=data.get("schemaId", ""),
                revoked=bool(data.get("revoked", False)),
                time=int(data.get("time", 0)),
                expiration_time=int(data.get("expirationTime", 0)),
                attestation_score=score,
            )
        except Exception as exc:
            return EASAttestation(uid=uid, recipient="", attester="", schema_id="",
                                  revoked=False, time=0, expiration_time=0,
                                  attestation_score=0, error=str(exc))


# ─── compute_r3_boost ────────────────────────────────────────────────────────

def compute_r3_boost(contributions: list[tuple[int, float]]) -> float:
    """
    contributions = [(attestation_score 0-100, attestor_hub_score 0-100), ...]
    attestor_weight = min(1.0, attestor_score / threshold)
    contribution    = (score/100) * weight * max_per_attestation
    total           = min(20, sum)
    """
    total = 0.0
    for att_score, attestor_score in contributions:
        if att_score <= 0:
            continue
        weight = min(1.0, attestor_score / _ATTESTOR_WEIGHT_THRESHOLD)
        total += (att_score / 100) * weight * _MAX_PER_ATTESTATION
    return min(_BOOST_MAX, round(total, 4))


# ─── apply_r3_attestation ────────────────────────────────────────────────────

async def apply_r3_attestation(
    db, hub_id: str, uid: str, http_client: httpx.AsyncClient
) -> dict:
    hub_row = await db._fetchone(
        "SELECT hub_id, wallet FROM hub_agents WHERE hub_id=?", (hub_id,)
    )
    if hub_row is None:
        raise HTTPException(status_code=404, detail="Hub agent not found")
    hub_row = dict(hub_row)

    fetcher = EASFetcher()
    att = await fetcher.fetch_attestation(uid, http_client)

    if not verify_attestation(att, hub_row["wallet"], _EAS_SCHEMA_ID):
        detail = att.error or (
            "revoked" if att.revoked else
            "recipient mismatch" if att.recipient.lower() != hub_row["wallet"].lower() else
            "invalid attestation"
        )
        raise HTTPException(status_code=422, detail=detail)

    # attestor Hub score (anti-sybil)
    attestor_row = await db._fetchone(
        "SELECT hub_id, score FROM hub_agents WHERE LOWER(wallet)=LOWER(?)", (att.attester,)
    )
    attestor_hub_score = float(dict(attestor_row)["score"]) if attestor_row else 0.0
    attestor_weight = (
        min(1.0, attestor_hub_score / _ATTESTOR_WEIGHT_THRESHOLD)
        if attestor_row else _ATTESTOR_EXTERNAL_WEIGHT
    )

    # idempotence
    existing = await db._fetchone(
        "SELECT uid FROM hub_eas_attestations WHERE uid=?", (uid,)
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="Attestation already recorded")

    await db.raw_execute(
        "INSERT INTO hub_eas_attestations"
        "(uid, hub_id, attester, recipient_wallet, attestation_score,"
        " attester_hub_score, weighted_contribution, attested_at, revoked, schema_id)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            uid, hub_id, att.attester, att.recipient, att.attestation_score,
            attestor_hub_score,
            round((att.attestation_score / 100) * attestor_weight * _MAX_PER_ATTESTATION, 4),
            att.time, 0, att.schema_id,
        ),
    )

    all_atts = await db.raw_execute_fetchall(
        "SELECT attestation_score, attester_hub_score FROM hub_eas_attestations"
        " WHERE hub_id=? AND revoked=0",
        (hub_id,),
    )
    pairs = [(int(r["attestation_score"]), float(r["attester_hub_score"])) for r in all_atts]
    new_boost = compute_r3_boost(pairs)

    await db.raw_execute(
        "UPDATE hub_agents SET score_r3_eas=? WHERE hub_id=?", (new_boost, hub_id)
    )

    return {
        "hub_id": hub_id,
        "uid": uid,
        "attestation_score": att.attestation_score,
        "attester_hub_score": attestor_hub_score,
        "boost": new_boost,
    }


# ─── Router ──────────────────────────────────────────────────────────────────

r3_router = APIRouter(prefix="/api/hub/r3", tags=["hub-r3"])


@r3_router.post("/attest")
async def submit_attestation(payload: dict):
    """Enregistre une attestation EAS pour un agent Hub."""
    hub_id = payload.get("hub_id", "")
    uid = payload.get("uid", "")
    if not hub_id or not uid:
        raise HTTPException(status_code=422, detail="hub_id et uid requis")
    async with httpx.AsyncClient() as client:
        return await apply_r3_attestation(db, hub_id, uid, client)


@r3_router.get("/{hub_id}")
async def get_attestations(hub_id: str):
    """Liste les attestations EAS d'un agent + boost total."""
    row = await db._fetchone(
        "SELECT hub_id, score_r3_eas FROM hub_agents WHERE hub_id=?", (hub_id,)
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Hub agent not found")
    atts = await db.raw_execute_fetchall(
        "SELECT uid, attester, attestation_score, attester_hub_score,"
        " weighted_contribution, attested_at, revoked"
        " FROM hub_eas_attestations WHERE hub_id=? ORDER BY attested_at DESC",
        (hub_id,),
    )
    r = dict(row)
    return {
        "hub_id": r["hub_id"],
        "score_r3_eas": r.get("score_r3_eas", 0.0),
        "attestations": [dict(a) for a in atts],
    }
