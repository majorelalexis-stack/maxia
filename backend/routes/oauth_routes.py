"""OAuth endpoints — two flows sharing one callback.

Flow 1: LINK (wallet already authenticated)
-------------------------------------------

1. User clicks "Link Google" from their wallet-authenticated session.
2. Browser hits ``GET /api/oauth/google/login?wallet=<addr>`` — we
   generate a signed ``state`` containing ``{flow: "link", wallet, ...}``.
3. Provider redirects to ``/api/oauth/google/callback?code&state``.
4. Callback dispatches on ``state.flow == "link"`` → persist the
   provider entry into ``agent_permissions.linked_providers`` for
   ``wallet`` via ``notification_store.link_provider``.

Flow 2: SIGNIN (spectator account — no wallet yet)
--------------------------------------------------

1. User clicks "Sign in with Google" on the public landing page.
2. Browser hits ``GET /api/oauth/google/signin`` — we generate a
   signed ``state`` containing ``{flow: "signin"}`` (no wallet).
3. Provider redirects to ``/api/oauth/google/callback?code&state``.
4. Callback dispatches on ``state.flow == "signin"`` → call
   ``spectator.create_or_get_spectator`` which returns a full
   ``agent_permissions`` row with ``account_type=spectator_google``,
   trust_level 0, spend caps 0, and a fresh ``api_key``.
5. Success redirect includes the api_key / agent_id in the URL fragment
   so the frontend JS can store it in ``localStorage`` without the
   value ever reaching an intermediate server log.

Security
--------

* ``state`` is a HMAC-signed JSON blob (wallet, flow, nonce, ts,
  provider) — tampering invalidates the signature.
* ``ts`` enforces a 10-minute TTL to prevent replay attacks.
* Spectator rows have ``max_daily_spend_usd=0`` and scopes
  ``read:public,notifications:receive`` so they cannot trade or touch
  any CASP-regulated feature until they upgrade to a real wallet.
* All writes are idempotent: re-linking a provider overwrites the
  previous entry; re-signing in as a spectator updates last_seen and
  returns the same api_key.
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

def _sign_state(*, wallet: str, provider: str, flow: str) -> str:
    """Produce an HMAC-signed state token carrying wallet + flow + nonce + ts.

    ``flow`` is either ``"link"`` (wallet already authenticated, we're
    adding a provider to an existing row) or ``"signin"`` (OAuth-first
    spectator signup, no wallet yet). The two flows share one callback
    which dispatches on this field.
    """
    if flow not in ("link", "signin"):
        raise ValueError(f"invalid flow: {flow}")
    payload = {
        "wallet": wallet,
        "provider": provider,
        "flow": flow,
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


def _authorize_url(provider: Any, state: str) -> str:
    """Build the provider's authorize URL with standard params."""
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
    return f"{provider.authorize_url}?{urlencode(params)}"


@router.get("/{provider_id}/login")
async def oauth_login(
    provider_id: str,
    wallet: str = Query(..., min_length=26, max_length=64),
) -> Response:  # type: ignore[name-defined]
    """Start the LINK flow — attach a provider to an existing wallet."""
    from fastapi.responses import Response  # local import to avoid shadow

    provider = get_provider(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail=f"Provider {provider_id} not enabled")

    state = _sign_state(wallet=wallet, provider=provider.id, flow="link")
    return RedirectResponse(url=_authorize_url(provider, state), status_code=302)


@router.get("/{provider_id}/signin")
async def oauth_signin(provider_id: str) -> Response:  # type: ignore[name-defined]
    """Start the SIGNIN flow — spectator signup, no wallet required.

    The user lands on the public landing page, clicks "Sign in with
    Google", and this endpoint kicks off OAuth with ``flow=signin`` in
    the signed state. On callback we create (or return) a spectator
    row and hand the api_key back to the browser via URL fragment.
    """
    from fastapi.responses import Response  # local import to avoid shadow

    provider = get_provider(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail=f"Provider {provider_id} not enabled")

    state = _sign_state(wallet="", provider=provider.id, flow="signin")
    return RedirectResponse(url=_authorize_url(provider, state), status_code=302)


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

    flow = str(payload.get("flow", "link")).lower()
    if flow not in ("link", "signin"):
        return RedirectResponse(
            url=f"{OAUTH_FAILURE_REDIRECT}&provider={provider_id}&reason=invalid_flow",
            status_code=302,
        )

    wallet = str(payload.get("wallet", ""))[:64]
    if flow == "link" and not wallet:
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

    # 4. Persist — dispatch on flow
    if flow == "link":
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
        sep = "&" if "?" in OAUTH_SUCCESS_REDIRECT else "?"
        return RedirectResponse(
            url=f"{OAUTH_SUCCESS_REDIRECT}{sep}provider={provider.id}",
            status_code=302,
        )

    # flow == "signin" — spectator signup
    try:
        from auth.spectator import create_or_get_spectator
        spec = await create_or_get_spectator(
            provider=provider.id,
            provider_user_id=provider_user_id,
            email=email,
            username=username,
            email_verified=verified,
        )
    except Exception as e:
        log.error("[oauth] spectator create failed %s: %s", provider_id, e)
        return RedirectResponse(
            url=f"{OAUTH_FAILURE_REDIRECT}&provider={provider_id}&reason=spectator_failed",
            status_code=302,
        )

    log.info(
        "[oauth] spectator signin provider=%s email=%s new=%s agent_id=%s",
        provider.id, email, spec.get("new"), spec.get("agent_id"),
    )

    # Hand api_key back via URL fragment — never logged by intermediate
    # servers, never sent back to the origin. Frontend JS reads
    # ``window.location.hash`` and stores the value in localStorage.
    fragment_params = {
        "signin": "ok",
        "provider": provider.id,
        "api_key": spec.get("api_key", ""),
        "agent_id": spec.get("agent_id", ""),
        "account_type": spec.get("account_type", ""),
        "new": "1" if spec.get("new") else "0",
    }
    fragment = urlencode(fragment_params)
    base_redirect = os.getenv(
        "OAUTH_SIGNIN_REDIRECT",
        "https://maxiaworld.app/account",
    )
    return RedirectResponse(url=f"{base_redirect}#{fragment}", status_code=302)


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
