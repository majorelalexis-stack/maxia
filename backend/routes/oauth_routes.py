"""OAuth linking endpoints — wallet + social provider pairing.

Flow
----

1. User with an authenticated wallet session clicks "Link Google" on
   the frontend.
2. Browser hits ``GET /api/oauth/google/login?wallet=<addr>`` — we
   generate a signed ``state`` token containing the wallet address and
   a nonce, then redirect to the provider's authorize URL.
3. Provider redirects back to
   ``GET /api/oauth/google/callback?code=...&state=...`` — we verify
   the state, exchange the code for a token, fetch userinfo, and
   persist the link into ``agent_permissions.linked_providers`` JSON.
4. Frontend sees the success redirect and shows "Google linked".

Security
--------

* ``state`` is a HMAC-signed JSON blob with ``wallet``, ``nonce``,
  ``ts``, ``provider`` — any tampering invalidates it.
* ``ts`` enforces a 10-minute TTL to prevent replay attacks.
* The wallet address in ``state`` must match an existing
  ``agent_permissions`` row before we persist the link. Missing row =
  reject.
* All writes are idempotent: re-linking the same provider overwrites
  the previous entry.
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
from typing import Any, Optional
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse

from auth.oauth_providers import get_provider, list_providers
from core.error_utils import safe_error

log = logging.getLogger("maxia.oauth.routes")

router = APIRouter(prefix="/api/oauth", tags=["oauth"])

# ══════════════════════════════════════════
# Configuration
# ══════════════════════════════════════════

OAUTH_REDIRECT_BASE: str = os.getenv(
    "OAUTH_REDIRECT_BASE",
    "https://maxiaworld.app",
).rstrip("/")

OAUTH_SUCCESS_REDIRECT: str = os.getenv(
    "OAUTH_SUCCESS_REDIRECT",
    "https://maxiaworld.app/account?oauth=linked",
)

OAUTH_FAILURE_REDIRECT: str = os.getenv(
    "OAUTH_FAILURE_REDIRECT",
    "https://maxiaworld.app/account?oauth=error",
)

_STATE_SECRET: bytes = os.getenv(
    "OAUTH_STATE_SECRET",
    "maxia-oauth-state-2026-rotate-yearly-or-set-via-env",
).encode()

_STATE_TTL = 600  # 10 minutes


# ══════════════════════════════════════════
# State signing / verification
# ══════════════════════════════════════════

def _sign_state(wallet: str, provider: str) -> str:
    """Produce an HMAC-signed state token carrying wallet + nonce + ts."""
    payload = {
        "wallet": wallet,
        "provider": provider,
        "nonce": secrets.token_urlsafe(16),
        "ts": int(time.time()),
    }
    payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    payload_b64 = base64.urlsafe_b64encode(payload_json.encode()).rstrip(b"=")
    sig = hmac.new(_STATE_SECRET, payload_b64, hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).rstrip(b"=")
    return f"{payload_b64.decode()}.{sig_b64.decode()}"


def _verify_state(state: str) -> Optional[dict]:
    """Verify an HMAC-signed state token and return the payload or None."""
    if not state or "." not in state:
        return None
    try:
        payload_b64, sig_b64 = state.rsplit(".", 1)
        expected_sig = hmac.new(
            _STATE_SECRET,
            payload_b64.encode(),
            hashlib.sha256,
        ).digest()
        actual_sig = base64.urlsafe_b64decode(sig_b64 + "==")
        if not hmac.compare_digest(expected_sig, actual_sig):
            return None
        payload_json = base64.urlsafe_b64decode(payload_b64 + "==").decode()
        payload = json.loads(payload_json)
        if not isinstance(payload, dict):
            return None
        if int(time.time()) - int(payload.get("ts", 0)) > _STATE_TTL:
            return None
        return payload
    except Exception as e:
        log.debug("[oauth] state verify failed: %s", e)
        return None


# ══════════════════════════════════════════
# Routes
# ══════════════════════════════════════════

@router.get("/providers")
async def oauth_providers() -> JSONResponse:
    """Public endpoint — list enabled OAuth providers for the frontend."""
    try:
        providers = list_providers()
        return JSONResponse({
            "ok": True,
            "providers": providers,
            "count": len(providers),
        })
    except Exception as e:
        return JSONResponse(safe_error(e, "oauth_providers"), status_code=500)


@router.get("/{provider_id}/login")
async def oauth_login(
    provider_id: str,
    wallet: str = Query(..., min_length=26, max_length=64),
) -> Response:  # type: ignore[name-defined]
    """Start the OAuth flow for a given provider + wallet binding."""
    from fastapi.responses import Response  # local import to avoid shadow

    provider = get_provider(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail=f"Provider {provider_id} not enabled")

    state = _sign_state(wallet=wallet, provider=provider.id)
    redirect_uri = f"{OAUTH_REDIRECT_BASE}/api/oauth/{provider.id}/callback"
    params = {
        "client_id": provider.client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(provider.scopes),
        "state": state,
    }
    if provider.id == "google":
        params["access_type"] = "offline"
        params["prompt"] = "consent"
    if provider.id == "discord":
        params["prompt"] = "consent"

    authorize_url = f"{provider.authorize_url}?{urlencode(params)}"
    return RedirectResponse(url=authorize_url, status_code=302)


@router.get("/{provider_id}/callback")
async def oauth_callback(
    provider_id: str,
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
) -> Response:  # type: ignore[name-defined]
    """OAuth callback — exchange code, fetch userinfo, persist link."""
    from fastapi.responses import Response  # local import

    if error:
        log.info("[oauth] provider %s returned error=%s", provider_id, error)
        return RedirectResponse(
            url=f"{OAUTH_FAILURE_REDIRECT}&provider={provider_id}&reason={error}",
            status_code=302,
        )

    if not code or not state:
        return RedirectResponse(
            url=f"{OAUTH_FAILURE_REDIRECT}&provider={provider_id}&reason=missing_code_or_state",
            status_code=302,
        )

    payload = _verify_state(state)
    if payload is None:
        return RedirectResponse(
            url=f"{OAUTH_FAILURE_REDIRECT}&provider={provider_id}&reason=invalid_state",
            status_code=302,
        )

    if payload.get("provider") != provider_id:
        return RedirectResponse(
            url=f"{OAUTH_FAILURE_REDIRECT}&provider={provider_id}&reason=provider_mismatch",
            status_code=302,
        )

    wallet = str(payload.get("wallet", ""))[:64]
    if not wallet:
        return RedirectResponse(
            url=f"{OAUTH_FAILURE_REDIRECT}&provider={provider_id}&reason=missing_wallet",
            status_code=302,
        )

    provider = get_provider(provider_id)
    if provider is None:
        return RedirectResponse(
            url=f"{OAUTH_FAILURE_REDIRECT}&provider={provider_id}&reason=provider_disabled",
            status_code=302,
        )

    # 1. Exchange code for access token
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            token_resp = await client.post(
                provider.token_url,
                data={
                    "client_id": provider.client_id,
                    "client_secret": provider.client_secret,
                    "code": code,
                    "redirect_uri": f"{OAUTH_REDIRECT_BASE}/api/oauth/{provider.id}/callback",
                    "grant_type": "authorization_code",
                },
                headers={"Accept": "application/json"},
            )
            token_resp.raise_for_status()
            token_data = token_resp.json()
    except Exception as e:
        log.warning("[oauth] token exchange failed %s: %s", provider_id, e)
        return RedirectResponse(
            url=f"{OAUTH_FAILURE_REDIRECT}&provider={provider_id}&reason=token_exchange",
            status_code=302,
        )

    access_token = token_data.get("access_token")
    if not access_token:
        return RedirectResponse(
            url=f"{OAUTH_FAILURE_REDIRECT}&provider={provider_id}&reason=no_access_token",
            status_code=302,
        )

    # 2. Fetch userinfo
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            ui_resp = await client.get(
                provider.userinfo_url,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/json",
                },
            )
            ui_resp.raise_for_status()
            user_info = ui_resp.json()

            # GitHub: primary email is fetched separately
            if provider.id == "github" and not user_info.get("email"):
                em_resp = await client.get(
                    "https://api.github.com/user/emails",
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Accept": "application/vnd.github+json",
                    },
                )
                if em_resp.status_code == 200:
                    emails = em_resp.json() or []
                    primary = next(
                        (e for e in emails if e.get("primary") and e.get("verified")),
                        None,
                    )
                    if primary:
                        user_info["email"] = primary.get("email")
                        user_info["_github_email_verified"] = True
    except Exception as e:
        log.warning("[oauth] userinfo fetch failed %s: %s", provider_id, e)
        return RedirectResponse(
            url=f"{OAUTH_FAILURE_REDIRECT}&provider={provider_id}&reason=userinfo_fetch",
            status_code=302,
        )

    # 3. Extract the fields we care about
    email = str(user_info.get(provider.email_field, "")).strip()
    username = str(user_info.get(provider.username_field, "")).strip()
    provider_user_id = str(user_info.get(provider.id_field, "")).strip()

    verified = False
    if provider.verified_email_field:
        v = user_info.get(provider.verified_email_field)
        verified = bool(v) if not isinstance(v, str) else v.lower() == "true"
    elif provider.id == "github":
        verified = bool(user_info.get("_github_email_verified"))

    if not email or not provider_user_id:
        return RedirectResponse(
            url=f"{OAUTH_FAILURE_REDIRECT}&provider={provider_id}&reason=missing_email_or_uid",
            status_code=302,
        )

    # 4. Persist the link
    try:
        from auth.notification_store import link_provider
        await link_provider(
            wallet=wallet,
            provider_id=provider.id,
            provider_user_id=provider_user_id,
            email=email,
            username=username,
            email_verified=verified,
        )
    except Exception as e:
        log.error("[oauth] persist link failed %s: %s", provider_id, e)
        return RedirectResponse(
            url=f"{OAUTH_FAILURE_REDIRECT}&provider={provider_id}&reason=persist_failed",
            status_code=302,
        )

    log.info(
        "[oauth] linked wallet=%s provider=%s email=%s verified=%s",
        wallet[:10] + "...", provider.id, email, verified,
    )
    return RedirectResponse(
        url=f"{OAUTH_SUCCESS_REDIRECT}&provider={provider.id}",
        status_code=302,
    )


@router.post("/{provider_id}/unlink")
async def oauth_unlink(
    provider_id: str,
    request: Request,
) -> JSONResponse:
    """Remove a provider link from the wallet's agent_permissions row.

    Requires the caller to authenticate via the existing wallet session
    mechanism (``require_auth`` dependency) so only the wallet owner can
    unlink their own providers.
    """
    try:
        from core.auth import require_auth
        wallet = await require_auth(
            x_wallet=request.headers.get("X-Wallet", ""),
            x_signature=request.headers.get("X-Signature", ""),
            x_nonce=request.headers.get("X-Nonce", ""),
        )
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"auth failed: {e}")

    try:
        from auth.notification_store import unlink_provider
        ok = await unlink_provider(wallet=wallet, provider_id=provider_id.lower())
        return JSONResponse({"ok": bool(ok), "provider": provider_id, "wallet": wallet})
    except Exception as e:
        return JSONResponse(safe_error(e, "oauth_unlink"), status_code=500)


log.info("[oauth] routes mounted — providers + login/callback/unlink")
