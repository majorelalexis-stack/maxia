"""Spectator accounts — OAuth-first signup without a real wallet.

A spectator user signs in with Google (or GitHub) and gets a full
MAXIA account without installing Phantom or connecting any wallet.
They can browse, read docs, receive notifications, and see every
marketplace listing — but cannot execute trades, hold custody, or
touch any CASP-regulated feature until they upgrade to a real wallet.

Upgrade path: a spectator user can later connect a real Solana
wallet via the existing /api/auth/verify flow. The backend merges
their spectator row into a wallet row (keeping linked_providers,
notification history, agent_id continuity).

Storage model
-------------

Spectator accounts reuse the existing ``agent_permissions`` table:

* ``account_type``    = "spectator_google" | "spectator_github"
                         (default "wallet" for existing rows)
* ``wallet``          = synthetic ID "spec:<provider>:<hash12>" so the
                         NOT NULL constraint is satisfied and the
                         uniqueness guarantees still hold
* ``agent_id``        = "spectator-<provider>-<hash12>"
* ``api_key``         = standard maxia_<uuid> — used as the session
                         token (cookie or X-API-Key header)
* ``did``             = did:web:maxiaworld.app:agent:spectator-*
* ``trust_level``     = 0 (lowest)
* ``status``          = "active"
* ``max_daily_spend_usd`` = 0 (no spending allowed until upgrade)
* ``max_single_tx_usd``   = 0
* ``scopes``          = "read:public,notifications:receive"
* ``linked_providers``= [{provider, provider_user_id, email, verified, ...}]
* ``notification_email`` = from OAuth, auto-verified if provider says so

Idempotency
-----------

:func:`create_or_get_spectator` is idempotent by ``(provider, provider_user_id)``:
if a row with the same synthetic wallet already exists, we update the
``last_seen`` / ``linked_providers`` without creating a duplicate.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from typing import Any, Optional

log = logging.getLogger("maxia.auth.spectator")


def _synthetic_wallet(provider: str, provider_user_id: str) -> str:
    """Return a deterministic synthetic wallet identifier.

    Format: ``spec:<provider>:<sha1-hex-12chars>``. The first 12 hex
    chars of SHA-1(provider + provider_user_id) is enough to avoid
    collisions at any realistic user count (~2^48 namespace).
    """
    digest = hashlib.sha1(
        f"{provider}:{provider_user_id}".encode("utf-8")
    ).hexdigest()[:12]
    return f"spec:{provider}:{digest}"


def _spectator_agent_id(provider: str, provider_user_id: str) -> str:
    """Return a DNS-safe agent id for the spectator row."""
    digest = hashlib.sha1(
        f"{provider}:{provider_user_id}".encode("utf-8")
    ).hexdigest()[:12]
    return f"spectator-{provider}-{digest}"


def _spectator_did(provider: str, provider_user_id: str) -> str:
    """Return a DID for the spectator account."""
    return f"did:web:maxiaworld.app:agent:{_spectator_agent_id(provider, provider_user_id)}"


async def _row_by_wallet(wallet: str) -> Optional[dict[str, Any]]:
    from core.database import db
    rows = await db.raw_execute_fetchall(
        "SELECT agent_id, api_key, wallet, account_type, did, "
        "notification_email, notification_email_verified, linked_providers "
        "FROM agent_permissions WHERE wallet = ?",
        (wallet,),
    )
    return dict(rows[0]) if rows else None


async def _row_by_provider_uid(
    provider: str, provider_user_id: str,
) -> Optional[dict[str, Any]]:
    """Find any ``agent_permissions`` row whose ``linked_providers`` JSON
    contains this (provider, provider_user_id) pair. Returns the most
    recently updated match — typically a wallet row that previously
    merged a spectator via /api/account/link-wallet.

    The query is a substring match on the JSON text which is fine for
    idempotent signin lookups. The provider_user_id is a stable
    provider-issued string that is unlikely to collide.
    """
    from core.database import db
    needle = f'"provider_user_id": "{provider_user_id}"'
    rows = await db.raw_execute_fetchall(
        "SELECT agent_id, api_key, wallet, account_type, did, "
        "notification_email, notification_email_verified, linked_providers "
        "FROM agent_permissions "
        "WHERE linked_providers LIKE ? "
        "ORDER BY updated_at DESC, created_at DESC",
        (f"%{needle}%",),
    )
    if not rows:
        return None
    # Double-check: the JSON must actually contain a matching object
    # for this exact (provider, provider_user_id) pair — LIKE can
    # over-match on substring collisions.
    for r in rows:
        try:
            raw = r.get("linked_providers") or "[]"
            data = json.loads(raw) if isinstance(raw, str) else (raw or [])
            if not isinstance(data, list):
                continue
            for p in data:
                if (
                    isinstance(p, dict)
                    and str(p.get("provider", "")).lower() == provider.lower()
                    and str(p.get("provider_user_id", "")) == provider_user_id
                ):
                    return dict(r)
        except Exception:
            continue
    return None


async def create_or_get_spectator(
    *,
    provider: str,
    provider_user_id: str,
    email: str,
    username: str = "",
    email_verified: bool = True,
) -> dict[str, Any]:
    """Create a spectator ``agent_permissions`` row or return the
    existing one for this (provider, provider_user_id) pair.

    Returns a dict with at least ``agent_id``, ``api_key``, ``wallet``,
    ``account_type``, ``did``, ``notification_email``, and
    ``linked_providers``. Safe to call on every sign-in — idempotent.
    """
    from core.database import db

    provider = str(provider).lower().strip()
    provider_user_id = str(provider_user_id).strip()
    if not provider or not provider_user_id:
        raise ValueError("provider and provider_user_id are required")

    wallet = _synthetic_wallet(provider, provider_user_id)
    agent_id = _spectator_agent_id(provider, provider_user_id)
    did = _spectator_did(provider, provider_user_id)
    account_type = f"spectator_{provider}"

    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    provider_entry = {
        "provider": provider,
        "provider_user_id": provider_user_id,
        "email": email,
        "username": username,
        "email_verified": bool(email_verified),
        "linked_at": int(time.time()),
    }

    # First, look up by the deterministic synthetic wallet (fast
    # indexed lookup). If the spectator row exists, we just update it.
    existing = await _row_by_wallet(wallet)

    # Fallback: the spectator may have been merged into a real wallet
    # row via /api/account/link-wallet — in that case the synthetic
    # wallet row no longer exists but the ``linked_providers`` of the
    # wallet row still carries the OAuth link. Return that row so
    # repeat signins land on the canonical wallet account instead of
    # recreating a stale spectator.
    if existing is None:
        merged = await _row_by_provider_uid(provider, provider_user_id)
        if merged is not None:
            log.info(
                "[spectator] OAuth link already attached to wallet agent_id=%s — returning wallet row",
                merged.get("agent_id"),
            )
            try:
                raw = merged.get("linked_providers") or "[]"
                providers_list = (
                    json.loads(raw) if isinstance(raw, str) else (raw or [])
                )
                if not isinstance(providers_list, list):
                    providers_list = []
            except Exception:
                providers_list = []
            return {
                "agent_id": merged.get("agent_id"),
                "api_key": merged.get("api_key"),
                "wallet": merged.get("wallet"),
                "account_type": merged.get("account_type") or "wallet",
                "did": merged.get("did"),
                "notification_email": merged.get("notification_email") or email,
                "notification_email_verified": bool(merged.get("notification_email_verified")),
                "linked_providers": providers_list,
                "new": False,
            }

    if existing is not None:
        log.info(
            "[spectator] existing account found for %s:%s (agent_id=%s)",
            provider, provider_user_id[:12], existing.get("agent_id"),
        )
        try:
            raw = existing.get("linked_providers") or "[]"
            providers_list = (
                json.loads(raw) if isinstance(raw, str) else (raw or [])
            )
            if not isinstance(providers_list, list):
                providers_list = []
        except Exception:
            providers_list = []
        providers_list = [
            p for p in providers_list
            if isinstance(p, dict) and p.get("provider") != provider
        ]
        providers_list.append(provider_entry)
        notif_email = existing.get("notification_email") or email
        notif_verified = bool(existing.get("notification_email_verified")) or bool(email_verified)
        await db.raw_execute(
            "UPDATE agent_permissions SET "
            "linked_providers = ?, "
            "notification_email = ?, "
            "notification_email_verified = ?, "
            "updated_at = ? "
            "WHERE wallet = ?",
            (
                json.dumps(providers_list),
                notif_email,
                1 if notif_verified else 0,
                now_iso,
                wallet,
            ),
        )
        return {
            "agent_id": existing["agent_id"],
            "api_key": existing["api_key"],
            "wallet": wallet,
            "account_type": existing.get("account_type") or account_type,
            "did": existing.get("did") or did,
            "notification_email": notif_email,
            "notification_email_verified": notif_verified,
            "linked_providers": providers_list,
            "new": False,
        }

    api_key = f"maxia_{uuid.uuid4().hex}"
    public_key = ""  # spectators have no keypair — they can't sign txs

    await db.raw_execute(
        "INSERT INTO agent_permissions "
        "(agent_id, api_key, wallet, trust_level, status, scopes, "
        "max_daily_spend_usd, max_single_tx_usd, daily_spent_usd, daily_spent_date, "
        "did, uaid, public_key, "
        "linked_providers, notification_email, notification_email_verified, "
        "notification_channels, account_type, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            agent_id,
            api_key,
            wallet,
            0,            # trust_level
            "active",
            "read:public,notifications:receive",
            0,            # max_daily_spend_usd — ZERO until wallet upgrade
            0,            # max_single_tx_usd
            0,            # daily_spent_usd
            "",           # daily_spent_date
            did,
            "",           # uaid
            public_key,
            json.dumps([provider_entry]),
            email,
            1 if email_verified else 0,
            json.dumps(["email"] if email_verified else []),
            account_type,
            now_iso,
            now_iso,
        ),
    )
    log.info(
        "[spectator] created new account provider=%s email=%s agent_id=%s",
        provider, email, agent_id,
    )
    return {
        "agent_id": agent_id,
        "api_key": api_key,
        "wallet": wallet,
        "account_type": account_type,
        "did": did,
        "notification_email": email,
        "notification_email_verified": email_verified,
        "linked_providers": [provider_entry],
        "new": True,
    }


async def get_spectator_by_api_key(api_key: str) -> Optional[dict[str, Any]]:
    """Look up a spectator account by its api_key. Returns None if
    not found or the api_key belongs to a real wallet account."""
    from core.database import db
    rows = await db.raw_execute_fetchall(
        "SELECT agent_id, api_key, wallet, account_type, did, "
        "notification_email, notification_email_verified, linked_providers, "
        "status, trust_level "
        "FROM agent_permissions WHERE api_key = ?",
        (api_key,),
    )
    if not rows:
        return None
    row = dict(rows[0])
    if not str(row.get("account_type", "")).startswith("spectator_"):
        return None
    return row


async def is_spectator(api_key: str) -> bool:
    """Return True if the api_key belongs to a spectator account."""
    row = await get_spectator_by_api_key(api_key)
    return row is not None
