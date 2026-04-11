"""Notification opt-in endpoints — direct email (no OAuth).

This router complements ``oauth_routes.py``:

* **OAuth flow** (Google/GitHub/Discord/Microsoft) returns an email
  that is often already verified — no extra step needed. The OAuth
  callback writes the email straight into ``notification_email`` with
  ``notification_email_verified=1``.

* **Direct email flow** (this file) is for users who don't want to
  link any social account. They submit an email address, receive a
  verification token by email, click the link, and their email becomes
  verified.

The wallet itself remains the primary key at all times. These
endpoints require the wallet to be authenticated via the existing
``core.auth.require_auth`` dependency.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse

from core.auth import require_auth
from core.error_utils import safe_error

log = logging.getLogger("maxia.notifications")

router = APIRouter(prefix="/api/notifications", tags=["notifications"])

_VERIFY_SECRET: bytes = os.getenv(
    "NOTIFICATION_VERIFY_SECRET",
    "maxia-notification-verify-2026-rotate-yearly",
).encode()

_VERIFY_TTL = 86400  # 24 hours


# ══════════════════════════════════════════
# Verification token signing
# ══════════════════════════════════════════

def _sign_verify_token(wallet: str, email: str) -> str:
    payload = {
        "wallet": wallet,
        "email": email.lower(),
        "nonce": secrets.token_urlsafe(12),
        "ts": int(time.time()),
    }
    payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    payload_b64 = base64.urlsafe_b64encode(payload_json.encode()).rstrip(b"=")
    sig = hmac.new(_VERIFY_SECRET, payload_b64, hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).rstrip(b"=")
    return f"{payload_b64.decode()}.{sig_b64.decode()}"


def _verify_token(token: str) -> Optional[dict]:
    if not token or "." not in token:
        return None
    try:
        payload_b64, sig_b64 = token.rsplit(".", 1)
        expected = hmac.new(
            _VERIFY_SECRET, payload_b64.encode(), hashlib.sha256,
        ).digest()
        actual = base64.urlsafe_b64decode(sig_b64 + "==")
        if not hmac.compare_digest(expected, actual):
            return None
        payload = json.loads(
            base64.urlsafe_b64decode(payload_b64 + "==").decode()
        )
        if not isinstance(payload, dict):
            return None
        if int(time.time()) - int(payload.get("ts", 0)) > _VERIFY_TTL:
            return None
        return payload
    except Exception as e:
        log.debug("[notifications] token verify failed: %s", e)
        return None


# ══════════════════════════════════════════
# Email sender (stub — integrates with existing SMTP infra)
# ══════════════════════════════════════════

async def _send_verification_email(wallet: str, email: str, token: str) -> bool:
    """Send a verification link to ``email`` for ``wallet``.

    Uses the existing backend mail infra when available, otherwise
    logs the link so the user can recover it from the console.
    """
    from urllib.parse import urlencode
    base = os.getenv("PUBLIC_URL", "https://maxiaworld.app").rstrip("/")
    verify_url = f"{base}/api/notifications/email/verify?{urlencode({'token': token})}"
    subject = "MAXIA — confirm your notification email"
    body = (
        "Hi,\n\n"
        "You asked to receive MAXIA notifications at this email address. "
        "Click the link below within 24 hours to confirm:\n\n"
        f"  {verify_url}\n\n"
        "If you did not make this request, ignore this message — "
        "nothing will be sent to you.\n\n"
        "— MAXIA"
    )
    try:
        # Try the existing backend email manager first
        try:
            from features.email_manager import send_email
            await send_email(to=email, subject=subject, body=body)
            log.info("[notifications] verification sent to %s via email_manager", email)
            return True
        except ImportError:
            pass
        # Fallback: direct SMTP via backend/infra/alert_service if present
        try:
            from infra.alert_service import send_mail as alert_send_mail  # type: ignore
            await alert_send_mail(subject, body, to=email)
            log.info("[notifications] verification sent to %s via alert_service", email)
            return True
        except Exception:
            pass
        # Last resort: log the link (dev environment)
        log.warning(
            "[notifications] no SMTP backend available — verify URL for %s: %s",
            email, verify_url,
        )
        return False
    except Exception as e:
        log.error("[notifications] send failed for %s: %s", email, e)
        return False


# ══════════════════════════════════════════
# Routes
# ══════════════════════════════════════════

@router.get("/status")
async def notifications_status(request: Request) -> JSONResponse:
    """Return the current notification state for the authenticated wallet."""
    try:
        wallet = await require_auth(
            x_wallet=request.headers.get("X-Wallet", ""),
            x_signature=request.headers.get("X-Signature", ""),
            x_nonce=request.headers.get("X-Nonce", ""),
        )
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"auth failed: {e}")

    try:
        from auth.notification_store import get_notification_state
        state = await get_notification_state(wallet=wallet)
        return JSONResponse({"ok": True, **state})
    except Exception as e:
        return JSONResponse(safe_error(e, "notifications_status"), status_code=500)


@router.post("/email/subscribe")
async def email_subscribe(
    request: Request,
    email: str = Query(..., min_length=5, max_length=255),
) -> JSONResponse:
    """Subscribe an email address for notifications. Sends a verification
    link — the email stays ``unverified`` until the user clicks it."""
    try:
        wallet = await require_auth(
            x_wallet=request.headers.get("X-Wallet", ""),
            x_signature=request.headers.get("X-Signature", ""),
            x_nonce=request.headers.get("X-Nonce", ""),
        )
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"auth failed: {e}")

    email = email.strip().lower()
    if "@" not in email or " " in email:
        raise HTTPException(status_code=400, detail="Invalid email format")

    try:
        from auth.notification_store import set_notification_email
        await set_notification_email(
            wallet=wallet, email=email, verified=False, source="direct",
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        return JSONResponse(safe_error(e, "email_subscribe"), status_code=500)

    token = _sign_verify_token(wallet, email)
    sent = await _send_verification_email(wallet, email, token)

    return JSONResponse({
        "ok": True,
        "wallet": wallet,
        "email": email,
        "verified": False,
        "email_sent": sent,
        "note": (
            "Check your inbox for a verification link."
            if sent else
            "Could not send verification email (SMTP unavailable). "
            "Contact support."
        ),
    })


@router.get("/email/verify")
async def email_verify(
    token: str = Query(..., min_length=10, max_length=512),
) -> JSONResponse:
    """Verify a subscription email. Called via the link in the email."""
    payload = _verify_token(token)
    if payload is None:
        raise HTTPException(status_code=400, detail="Invalid or expired token")

    wallet = str(payload.get("wallet", ""))
    email = str(payload.get("email", "")).lower()
    if not wallet or not email:
        raise HTTPException(status_code=400, detail="Malformed token")

    try:
        from auth.notification_store import mark_email_verified
        ok = await mark_email_verified(wallet=wallet, email=email)
    except Exception as e:
        return JSONResponse(safe_error(e, "email_verify"), status_code=500)

    if not ok:
        raise HTTPException(
            status_code=409,
            detail=(
                "Email on file does not match the token. "
                "You may have changed your subscription since you "
                "requested the verification link."
            ),
        )

    log.info("[notifications] verified %s for wallet %s...", email, wallet[:10])
    return JSONResponse({
        "ok": True,
        "wallet": wallet,
        "email": email,
        "verified": True,
    })


@router.post("/email/unsubscribe")
async def email_unsubscribe(request: Request) -> JSONResponse:
    """Remove the notification email for the authenticated wallet."""
    try:
        wallet = await require_auth(
            x_wallet=request.headers.get("X-Wallet", ""),
            x_signature=request.headers.get("X-Signature", ""),
            x_nonce=request.headers.get("X-Nonce", ""),
        )
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"auth failed: {e}")

    try:
        from auth.notification_store import unsubscribe_email
        removed = await unsubscribe_email(wallet=wallet)
        return JSONResponse({"ok": True, "wallet": wallet, "removed": removed})
    except Exception as e:
        return JSONResponse(safe_error(e, "email_unsubscribe"), status_code=500)


log.info("[notifications] router mounted — 4 endpoints")
