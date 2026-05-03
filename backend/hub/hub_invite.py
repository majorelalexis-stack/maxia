"""MAXIA Hub R0b — Invitations auto + Claim ed25519.

- Invite A2A : POST JSON-RPC 2.0 aux agents découverts (endpoint not null)
- Email : SMTP optionnel, max 10/j, graceful si non configuré
- Claim : un agent enregistré réclame un profil scout (sig ed25519)
"""
import asyncio
import json
import os
import smtplib
import time
import uuid
from email.mime.text import MIMEText

import base58
import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

from core.database import db

_EMAIL_DAILY_LIMIT = 10
_A2A_TIMEOUT = 10.0


# ─── SMTP helpers ────────────────────────────────────────────────────────────

def _smtp_configured() -> bool:
    return bool(os.getenv("SMTP_HOST") and os.getenv("SMTP_USER") and os.getenv("SMTP_PASS"))


def _smtp_send(to: str, subject: str, body: str) -> None:
    host = os.environ["SMTP_HOST"]
    port = int(os.getenv("SMTP_PORT", "465"))
    user = os.environ["SMTP_USER"]
    password = os.environ["SMTP_PASS"]
    from_addr = os.getenv("SMTP_FROM", user)

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to

    with smtplib.SMTP_SSL(host, port) as smtp:
        smtp.login(user, password)
        smtp.sendmail(from_addr, [to], msg.as_string())


# ─── ed25519 verify (identique à hub_registry pour éviter import circulaire) ─

def _verify_ed25519(public_key_b58: str, message: str, sig_b58: str) -> bool:
    try:
        pk_bytes = base58.b58decode(public_key_b58)
        sig_bytes = base58.b58decode(sig_b58)
        VerifyKey(pk_bytes).verify(message.encode(), sig_bytes)
        return True
    except (BadSignatureError, Exception):
        return False


# ─── Classe principale ───────────────────────────────────────────────────────

class HubInviter:
    async def send_a2a_invite(
        self,
        http_client: httpx.AsyncClient,
        endpoint: str,
        agent_name: str,
        scout_id: str,
        db=None,
    ) -> bool:
        payload = {
            "jsonrpc": "2.0",
            "id": uuid.uuid4().hex,
            "method": "tasks/send",
            "params": {
                "id": uuid.uuid4().hex,
                "message": {
                    "role": "user",
                    "parts": [{
                        "type": "text",
                        "text": (
                            f"Hello {agent_name}, MAXIA Hub invites you to join the AI-to-AI "
                            "reputation registry at https://maxiaworld.app/identity — "
                            "get a verifiable on-chain score, connect with other agents, "
                            "and unlock the marketplace. Registration is free."
                        ),
                    }],
                },
            },
        }
        try:
            resp = await http_client.post(endpoint, json=payload, timeout=_A2A_TIMEOUT)
            status = "sent" if resp.status_code in (200, 201, 202) else "attempted"
            if db is not None:
                await db.raw_execute(
                    "INSERT INTO hub_invitations(invite_id, scout_id, method, target, sent_at, status)"
                    " VALUES(?,?,?,?,?,?)",
                    (uuid.uuid4().hex, scout_id, "a2a", endpoint, int(time.time()), status),
                )
            return True
        except Exception:
            return False

    async def send_email_invite(
        self,
        email: str,
        agent_name: str,
        scout_id: str,
        db=None,
    ) -> bool:
        if not _smtp_configured():
            return False

        if db is not None:
            today_start = int(time.time()) // 86400 * 86400
            row = await db._fetchone(
                "SELECT COUNT(*) as cnt FROM hub_invitations"
                " WHERE method='email' AND sent_at >= ?",
                (today_start,),
            )
            if row and row["cnt"] >= _EMAIL_DAILY_LIMIT:
                return False

        subject = f"Join MAXIA Hub — verifiable reputation for AI agents"
        body = (
            f"Hi {agent_name},\n\n"
            "MAXIA Hub is a cryptographic identity registry for autonomous AI agents.\n"
            "Register at https://maxiaworld.app/identity to get:\n"
            "  • A verifiable reputation score\n"
            "  • On-chain identity (DID + UAID)\n"
            "  • Access to the AI-to-AI marketplace\n\n"
            "Registration is free and takes under 60 seconds.\n\n"
            "— MAXIA\nhttps://maxiaworld.app"
        )
        try:
            await asyncio.to_thread(_smtp_send, email, subject, body)
            if db is not None:
                await db.raw_execute(
                    "INSERT INTO hub_invitations(invite_id, scout_id, method, target, sent_at, status)"
                    " VALUES(?,?,?,?,?,?)",
                    (uuid.uuid4().hex, scout_id, "email", email, int(time.time()), "sent"),
                )
            return True
        except Exception:
            return False

    async def run_invite_batch(
        self,
        db,
        http_client: httpx.AsyncClient,
        a2a_limit: int = 50,
        email_limit: int = _EMAIL_DAILY_LIMIT,
    ) -> dict[str, int]:
        stats = {
            "a2a_sent": 0,
            "email_sent": 0,
            "skipped_no_endpoint": 0,
            "skipped_already_invited": 0,
        }
        rows = await db.raw_execute_fetchall(
            "SELECT scout_id, name, endpoint, source, raw_data FROM hub_scout_results"
            " WHERE status='eligible' ORDER BY discovered_at ASC"
        )
        for row in rows:
            row = dict(row)
            scout_id = row["scout_id"]
            endpoint = row.get("endpoint")

            if not endpoint:
                stats["skipped_no_endpoint"] += 1
                continue

            existing = await db._fetchone(
                "SELECT invite_id FROM hub_invitations WHERE scout_id=? AND method='a2a'",
                (scout_id,),
            )
            if existing:
                stats["skipped_already_invited"] += 1
                continue

            if stats["a2a_sent"] >= a2a_limit:
                break

            sent = await self.send_a2a_invite(
                http_client, endpoint, row.get("name", "Agent"), scout_id, db=db
            )
            if sent:
                stats["a2a_sent"] += 1

        return stats


# ─── Claim ed25519 ────────────────────────────────────────────────────────────

async def claim_scout_profile(db, hub_id: str, scout_id: str, sig: str) -> dict:
    hub_row = await db._fetchone(
        "SELECT hub_id, public_key FROM hub_agents WHERE hub_id=?",
        (hub_id,),
    )
    if hub_row is None:
        raise HTTPException(status_code=404, detail="Hub agent not found")

    if not _verify_ed25519(dict(hub_row)["public_key"], hub_id + scout_id, sig):
        raise HTTPException(status_code=401, detail="Invalid signature")

    scout_row = await db._fetchone(
        "SELECT scout_id, matched_hub_id FROM hub_scout_results WHERE scout_id=?",
        (scout_id,),
    )
    if scout_row is None:
        raise HTTPException(status_code=404, detail="Scout profile not found")

    if dict(scout_row)["matched_hub_id"] is not None:
        raise HTTPException(status_code=409, detail="Scout profile already claimed")

    await db.raw_execute(
        "UPDATE hub_scout_results SET matched_hub_id=?, status='claimed' WHERE scout_id=?",
        (hub_id, scout_id),
    )
    return {"ok": True, "hub_id": hub_id, "scout_id": scout_id}


# ─── Router ──────────────────────────────────────────────────────────────────

invite_router = APIRouter(prefix="/api/hub/invite", tags=["hub-invite"])


@invite_router.post("/run", status_code=202)
async def run_invitations(background_tasks: BackgroundTasks):
    """Déclenche un batch d'invitations en arrière-plan."""
    async def _task():
        async with httpx.AsyncClient() as client:
            await HubInviter().run_invite_batch(db, client)

    background_tasks.add_task(_task)
    return {"status": "running"}


@invite_router.post("/claim")
async def claim_endpoint(payload: dict):
    """Lie un profil scout à un agent enregistré (sig ed25519 requise)."""
    hub_id = payload.get("hub_id", "")
    scout_id = payload.get("scout_id", "")
    sig = payload.get("sig", "")
    if not all([hub_id, scout_id, sig]):
        raise HTTPException(status_code=422, detail="hub_id, scout_id, sig requis")
    return await claim_scout_profile(db, hub_id, scout_id, sig)


@invite_router.get("/stats")
async def invite_stats():
    """Statistiques des invitations envoyées."""
    rows = await db.raw_execute_fetchall(
        "SELECT method, status, COUNT(*) as cnt"
        " FROM hub_invitations GROUP BY method, status"
    )
    return {"invitations": [dict(r) for r in rows] if rows else []}
