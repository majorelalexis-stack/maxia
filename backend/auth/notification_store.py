"""Storage layer for notification links and OAuth provider bindings.

All reads/writes go through ``core.database.db`` so the same code runs
on SQLite (dev) and PostgreSQL (prod). Schema was created by migration
16 in ``backend/core/database.py``.

Columns on ``agent_permissions``:

* ``linked_providers``          JSON array of provider links
* ``notification_email``        primary notification address (may differ
                                 from any OAuth provider email)
* ``notification_email_verified`` 0/1
* ``notification_channels``     JSON array of channel hints (``"email"``,
                                 ``"telegram"``, ``"discord"``)

The linked_providers JSON is a list of dicts::

    [
        {
            "provider": "google",
            "provider_user_id": "1234567890",
            "email": "alice@example.com",
            "username": "Alice",
            "email_verified": true,
            "linked_at": 1713000000
        },
        ...
    ]
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

log = logging.getLogger("maxia.auth.store")


# ══════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════

async def _load_row(wallet: str) -> Optional[dict[str, Any]]:
    """Fetch the agent_permissions row for a wallet, or None."""
    from core.database import db
    rows = await db.raw_execute_fetchall(
        "SELECT wallet, linked_providers, notification_email, "
        "notification_email_verified, notification_channels "
        "FROM agent_permissions WHERE wallet = ?",
        (wallet,),
    )
    if not rows:
        return None
    return dict(rows[0])


def _parse_providers(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [p for p in raw if isinstance(p, dict)]
    if isinstance(raw, (bytes, bytearray)):
        try:
            raw = raw.decode("utf-8")
        except Exception:
            return []
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [p for p in parsed if isinstance(p, dict)]
        except (ValueError, TypeError):
            return []
    return []


def _parse_channels(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(c) for c in raw if c]
    if isinstance(raw, (bytes, bytearray)):
        try:
            raw = raw.decode("utf-8")
        except Exception:
            return []
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(c) for c in parsed if c]
        except (ValueError, TypeError):
            return []
    return []


# ══════════════════════════════════════════
# OAuth provider links
# ══════════════════════════════════════════

async def link_provider(
    *,
    wallet: str,
    provider_id: str,
    provider_user_id: str,
    email: str,
    username: str = "",
    email_verified: bool = False,
) -> dict[str, Any]:
    """Add or update a provider link for a wallet.

    Idempotent: re-linking the same provider overwrites the previous
    entry (keeps the list deduplicated by ``provider`` id).
    """
    from core.database import db
    row = await _load_row(wallet)
    if row is None:
        # Wallet not registered as an agent yet — refuse to link
        raise ValueError(f"wallet {wallet[:10]}... has no agent_permissions row")

    providers = _parse_providers(row.get("linked_providers"))
    providers = [p for p in providers if p.get("provider") != provider_id]
    providers.append({
        "provider": provider_id,
        "provider_user_id": provider_user_id,
        "email": email,
        "username": username,
        "email_verified": bool(email_verified),
        "linked_at": int(time.time()),
    })

    # If this is the first verified email we see, also set it as the
    # notification_email so the user gets notifications without an
    # extra opt-in step.
    notif_email = str(row.get("notification_email") or "")
    notif_verified = bool(row.get("notification_email_verified"))
    if email_verified and (not notif_email or not notif_verified):
        notif_email = email
        notif_verified = True

    channels = _parse_channels(row.get("notification_channels"))
    if email_verified and "email" not in channels:
        channels.append("email")

    await db.raw_execute(
        "UPDATE agent_permissions SET "
        "linked_providers = ?, "
        "notification_email = ?, "
        "notification_email_verified = ?, "
        "notification_channels = ?, "
        "updated_at = ? "
        "WHERE wallet = ?",
        (
            json.dumps(providers),
            notif_email,
            1 if notif_verified else 0,
            json.dumps(channels),
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            wallet,
        ),
    )
    return {
        "wallet": wallet,
        "providers": providers,
        "notification_email": notif_email,
        "notification_email_verified": notif_verified,
        "notification_channels": channels,
    }


async def unlink_provider(*, wallet: str, provider_id: str) -> bool:
    """Remove a provider link. Returns True if something was removed."""
    from core.database import db
    row = await _load_row(wallet)
    if row is None:
        return False

    providers = _parse_providers(row.get("linked_providers"))
    before = len(providers)
    providers = [p for p in providers if p.get("provider") != provider_id]
    if len(providers) == before:
        return False

    await db.raw_execute(
        "UPDATE agent_permissions SET "
        "linked_providers = ?, updated_at = ? WHERE wallet = ?",
        (
            json.dumps(providers),
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            wallet,
        ),
    )
    return True


async def get_linked_providers(*, wallet: str) -> list[dict[str, Any]]:
    """Return the full provider link list for a wallet (safe to expose,
    contains only public profile info)."""
    row = await _load_row(wallet)
    if row is None:
        return []
    return _parse_providers(row.get("linked_providers"))


# ══════════════════════════════════════════
# Direct email opt-in (no OAuth)
# ══════════════════════════════════════════

async def set_notification_email(
    *,
    wallet: str,
    email: str,
    verified: bool = False,
    source: str = "direct",
) -> dict[str, Any]:
    """Set the primary notification email for a wallet.

    If ``verified=False``, the caller is responsible for sending a
    verification link and hitting :func:`mark_email_verified` later.
    """
    from core.database import db
    row = await _load_row(wallet)
    if row is None:
        raise ValueError(f"wallet {wallet[:10]}... has no agent_permissions row")

    channels = _parse_channels(row.get("notification_channels"))
    if verified and "email" not in channels:
        channels.append("email")

    await db.raw_execute(
        "UPDATE agent_permissions SET "
        "notification_email = ?, "
        "notification_email_verified = ?, "
        "notification_channels = ?, "
        "updated_at = ? WHERE wallet = ?",
        (
            email,
            1 if verified else 0,
            json.dumps(channels),
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            wallet,
        ),
    )
    return {
        "wallet": wallet,
        "notification_email": email,
        "notification_email_verified": verified,
        "source": source,
    }


async def mark_email_verified(*, wallet: str, email: str) -> bool:
    """Mark the stored email as verified IF it matches what's on file."""
    from core.database import db
    row = await _load_row(wallet)
    if row is None:
        return False
    stored = str(row.get("notification_email") or "")
    if stored.lower() != email.lower() or not stored:
        return False
    channels = _parse_channels(row.get("notification_channels"))
    if "email" not in channels:
        channels.append("email")
    await db.raw_execute(
        "UPDATE agent_permissions SET "
        "notification_email_verified = ?, notification_channels = ?, "
        "updated_at = ? WHERE wallet = ?",
        (
            1,
            json.dumps(channels),
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            wallet,
        ),
    )
    return True


async def unsubscribe_email(*, wallet: str) -> bool:
    """Clear the notification email + verified flag for a wallet."""
    from core.database import db
    row = await _load_row(wallet)
    if row is None:
        return False
    channels = [c for c in _parse_channels(row.get("notification_channels")) if c != "email"]
    await db.raw_execute(
        "UPDATE agent_permissions SET "
        "notification_email = '', notification_email_verified = 0, "
        "notification_channels = ?, updated_at = ? WHERE wallet = ?",
        (
            json.dumps(channels),
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            wallet,
        ),
    )
    return True


async def get_notification_state(*, wallet: str) -> dict[str, Any]:
    """Return the full notification state for a wallet (for /status UI)."""
    row = await _load_row(wallet)
    if row is None:
        return {
            "wallet": wallet,
            "exists": False,
            "notification_email": "",
            "verified": False,
            "channels": [],
            "providers": [],
        }
    return {
        "wallet": wallet,
        "exists": True,
        "notification_email": str(row.get("notification_email") or ""),
        "verified": bool(row.get("notification_email_verified")),
        "channels": _parse_channels(row.get("notification_channels")),
        "providers": _parse_providers(row.get("linked_providers")),
    }
