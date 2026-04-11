"""Account routes — profile lookup + spectator-to-wallet upgrade.

Endpoints
---------

- ``GET  /api/account/me`` — returns the account profile for an
  ``X-API-Key``. Works for both spectator and wallet rows.

- ``POST /api/account/link-wallet`` — upgrades an existing spectator
  account to a real wallet account in-place. The caller passes:
    * ``X-API-Key``   — the spectator's api_key
    * ``X-Wallet``    — the real Solana wallet address
    * ``X-Signature`` — ed25519 signature of the nonce
    * ``X-Nonce``     — the nonce previously obtained from /api/auth/nonce

  On success, the spectator row's ``wallet``, ``public_key``,
  ``account_type``, ``trust_level``, ``max_daily_spend_usd``,
  ``max_single_tx_usd`` and ``scopes`` are upgraded to the wallet tier.
  The api_key is preserved so the browser's ``localStorage`` doesn't
  need to be updated.

  Conflict handling: if another ``agent_permissions`` row already has
  this wallet (i.e. the user previously signed in wallet-first), we
  return HTTP 409 with ``reason=wallet_already_linked``. The frontend
  shows a "Sign out + sign in with wallet" CTA in that case.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException

log = logging.getLogger("maxia.account.routes")

router = APIRouter(prefix="/api/account", tags=["account"])


# ══════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════

async def _row_by_api_key(api_key: str) -> Optional[dict[str, Any]]:
    from core.database import db
    rows = await db.raw_execute_fetchall(
        "SELECT agent_id, api_key, wallet, account_type, did, "
        "trust_level, status, scopes, "
        "max_daily_spend_usd, max_single_tx_usd, daily_spent_usd, "
        "notification_email, notification_email_verified, "
        "linked_providers, public_key, created_at, updated_at "
        "FROM agent_permissions WHERE api_key = ?",
        (api_key,),
    )
    return dict(rows[0]) if rows else None


async def _row_by_wallet(wallet: str) -> Optional[dict[str, Any]]:
    """Return the most recently-updated ``agent_permissions`` row
    for a given wallet. Legacy data may contain duplicate rows for
    the same wallet — we pick the freshest one as the merge target."""
    from core.database import db
    rows = await db.raw_execute_fetchall(
        "SELECT agent_id, api_key, wallet, account_type, did, "
        "trust_level, status, scopes, "
        "max_daily_spend_usd, max_single_tx_usd, daily_spent_usd, "
        "notification_email, notification_email_verified, "
        "linked_providers, public_key, created_at, updated_at "
        "FROM agent_permissions WHERE wallet = ? "
        "ORDER BY updated_at DESC, created_at DESC",
        (wallet,),
    )
    if not rows:
        return None
    if len(rows) > 1:
        log.warning(
            "[account] %d duplicate agent_permissions rows for wallet %s — picking freshest",
            len(rows), wallet[:10] + "...",
        )
    return dict(rows[0])


def _merge_provider_lists(
    target: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge two ``linked_providers`` lists, deduped by ``provider``.
    Incoming entries overwrite target entries for the same provider."""
    by_provider: dict[str, dict[str, Any]] = {}
    for p in target:
        if isinstance(p, dict) and p.get("provider"):
            by_provider[str(p["provider"]).lower()] = p
    for p in incoming:
        if isinstance(p, dict) and p.get("provider"):
            by_provider[str(p["provider"]).lower()] = p
    return list(by_provider.values())


def _parse_providers(raw: Any) -> list[dict[str, Any]]:
    try:
        if isinstance(raw, str):
            data = json.loads(raw or "[]")
        else:
            data = raw or []
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _to_profile(row: dict[str, Any]) -> dict[str, Any]:
    """Render a DB row as a JSON-safe public profile."""
    try:
        raw = row.get("linked_providers") or "[]"
        providers = json.loads(raw) if isinstance(raw, str) else (raw or [])
        if not isinstance(providers, list):
            providers = []
    except Exception:
        providers = []
    provider_summary = [
        {
            "provider": p.get("provider", ""),
            "email": p.get("email", ""),
            "username": p.get("username", ""),
            "verified": bool(p.get("email_verified")),
        }
        for p in providers
        if isinstance(p, dict)
    ]
    account_type = str(row.get("account_type") or "wallet")
    is_spectator = account_type.startswith("spectator_")
    scopes_csv = str(row.get("scopes") or "")
    return {
        "ok": True,
        "agent_id": row.get("agent_id"),
        "wallet": row.get("wallet"),
        "did": row.get("did"),
        "account_type": account_type,
        "is_spectator": is_spectator,
        "can_trade": (not is_spectator) and row.get("status") == "active",
        "trust_level": int(row.get("trust_level") or 0),
        "status": row.get("status"),
        "scopes": [s.strip() for s in scopes_csv.split(",") if s.strip()],
        "max_daily_spend_usd": float(row.get("max_daily_spend_usd") or 0),
        "max_single_tx_usd": float(row.get("max_single_tx_usd") or 0),
        "daily_spent_usd": float(row.get("daily_spent_usd") or 0),
        "notification_email": row.get("notification_email") or "",
        "notification_email_verified": bool(row.get("notification_email_verified")),
        "linked_providers": provider_summary,
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


# ══════════════════════════════════════════
# GET /api/account/me
# ══════════════════════════════════════════

@router.get("/me")
async def account_me(
    x_api_key: str = Header(None, alias="X-API-Key"),
):
    """Return the caller's account profile. Used by the frontend to
    render ``/account`` after an OAuth spectator signin or a wallet
    session — both store the api_key in localStorage."""
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header")
    row = await _row_by_api_key(x_api_key)
    if row is None:
        raise HTTPException(status_code=401, detail="Invalid api_key")
    return _to_profile(row)


# ══════════════════════════════════════════
# POST /api/account/link-wallet
# ══════════════════════════════════════════

@router.post("/link-wallet")
async def account_link_wallet(
    x_api_key: str = Header(None, alias="X-API-Key"),
    x_wallet: str = Header(None, alias="X-Wallet"),
    x_signature: str = Header(None, alias="X-Signature"),
    x_nonce: str = Header(None, alias="X-Nonce"),
):
    """Upgrade a spectator account to a wallet account in-place.

    The user signs the usual ``MAXIA login: <nonce>`` message with
    their Solana keypair, and we patch the existing spectator row with
    the real wallet. The api_key stays the same so the browser
    doesn't need to re-authenticate.
    """
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header")
    if not (x_wallet and x_signature and x_nonce):
        raise HTTPException(
            status_code=400,
            detail="Missing X-Wallet / X-Signature / X-Nonce",
        )

    # 1. Resolve the api_key → must be an existing spectator row
    row = await _row_by_api_key(x_api_key)
    if row is None:
        raise HTTPException(status_code=401, detail="Invalid api_key")

    account_type = str(row.get("account_type") or "wallet")
    if not account_type.startswith("spectator_"):
        raise HTTPException(
            status_code=400,
            detail=f"account_type={account_type} cannot be upgraded (already a wallet)",
        )

    # 2. Verify the wallet signature — reuse the existing auth dependency.
    #    This checks the Redis-backed nonce, anti-replay, ed25519 sig.
    try:
        from core.auth import require_auth
        verified_wallet = await require_auth(
            x_wallet=x_wallet,
            x_signature=x_signature,
            x_nonce=x_nonce,
        )
    except HTTPException as e:
        log.warning(
            "[account.link-wallet] auth rejected: status=%s detail=%s wallet=%s sig_len=%d nonce=%s",
            e.status_code, e.detail, x_wallet[:10] + "...",
            len(x_signature or ""), (x_nonce or "")[:12],
        )
        raise
    except Exception as e:
        log.warning("[account.link-wallet] require_auth failed: %s", e)
        raise HTTPException(status_code=401, detail="Wallet signature verification failed")

    if verified_wallet != x_wallet:
        raise HTTPException(status_code=401, detail="Wallet mismatch after verification")

    # 3. Two code paths:
    #    a) No existing wallet row → patch the spectator row in place
    #       (same agent_id, same api_key, same did — the browser
    #       localStorage needs zero changes).
    #    b) Existing wallet row → merge the spectator's OAuth link
    #       into the wallet row, then delete the spectator row. The
    #       frontend swaps localStorage to the wallet row's api_key.
    from core.database import db
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    existing = await _row_by_wallet(verified_wallet)

    # Parse the spectator's current OAuth links / email once
    spec_providers = _parse_providers(row.get("linked_providers"))
    spec_email = row.get("notification_email") or ""
    spec_email_verified = bool(row.get("notification_email_verified"))

    if existing is None or existing.get("agent_id") == row.get("agent_id"):
        # ── Path A: in-place upgrade (no collision) ──
        new_scopes = "read:public,notifications:receive,trade:basic"
        await db.raw_execute(
            "UPDATE agent_permissions SET "
            "wallet = ?, "
            "public_key = ?, "
            "account_type = ?, "
            "trust_level = ?, "
            "max_daily_spend_usd = ?, "
            "max_single_tx_usd = ?, "
            "scopes = ?, "
            "updated_at = ? "
            "WHERE agent_id = ?",
            (
                verified_wallet,
                verified_wallet,  # Solana address == ed25519 public key
                "wallet",
                1,        # trust_level
                50.0,     # max_daily_spend_usd — matches config.py default
                10.0,     # max_single_tx_usd
                new_scopes,
                now_iso,
                row.get("agent_id"),
            ),
        )
        log.info(
            "[account.link-wallet] upgraded-in-place agent_id=%s from %s to wallet %s",
            row.get("agent_id"), account_type, verified_wallet[:10] + "...",
        )
        updated = await _row_by_api_key(x_api_key)
        if updated is None:
            raise HTTPException(status_code=500, detail="Row vanished after upgrade")
        profile = _to_profile(updated)
        profile["merged"] = False
        # api_key unchanged — frontend localStorage stays valid
        profile["api_key"] = x_api_key
        return profile

    # ── Path B: merge into the existing wallet row ──
    target_id = existing.get("agent_id")
    target_api_key = existing.get("api_key")
    target_providers = _parse_providers(existing.get("linked_providers"))
    merged_providers = _merge_provider_lists(target_providers, spec_providers)

    # Notification email: keep target's if set, else adopt spectator's
    new_email = existing.get("notification_email") or spec_email
    new_email_verified = bool(existing.get("notification_email_verified")) or spec_email_verified

    await db.raw_execute(
        "UPDATE agent_permissions SET "
        "linked_providers = ?, "
        "notification_email = ?, "
        "notification_email_verified = ?, "
        "updated_at = ? "
        "WHERE agent_id = ?",
        (
            json.dumps(merged_providers),
            new_email,
            1 if new_email_verified else 0,
            now_iso,
            target_id,
        ),
    )

    # Delete the spectator row — the wallet row is now authoritative.
    await db.raw_execute(
        "DELETE FROM agent_permissions WHERE agent_id = ?",
        (row.get("agent_id"),),
    )

    log.info(
        "[account.link-wallet] merged spectator agent_id=%s into wallet agent_id=%s (providers=%d)",
        row.get("agent_id"), target_id, len(merged_providers),
    )

    # Return the wallet row's refreshed profile + its api_key so the
    # frontend can swap localStorage from the spectator's key to the
    # wallet's canonical key.
    merged = await _row_by_wallet(verified_wallet)
    if merged is None:
        raise HTTPException(status_code=500, detail="Row vanished after merge")
    profile = _to_profile(merged)
    profile["merged"] = True
    profile["api_key"] = target_api_key
    return profile


# ══════════════════════════════════════════
# POST /api/account/signout
# ══════════════════════════════════════════

@router.post("/signout")
async def account_signout(
    x_api_key: str = Header(None, alias="X-API-Key"),
):
    """Client-side signout stub. The api_key itself is not revoked
    (it's a long-lived identifier), but we log the event for audit
    trails and return ok=true so the frontend can clear localStorage.
    """
    if x_api_key:
        row = await _row_by_api_key(x_api_key)
        if row is not None:
            log.info("[account.signout] agent_id=%s", row.get("agent_id"))
    return {"ok": True}


log.info("[account] routes mounted — me / link-wallet / signout")
