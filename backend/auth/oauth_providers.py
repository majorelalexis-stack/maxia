"""OAuth provider registry for the 3-flow auth architecture.

Flows
-----

1. **Wallet-only** (primary, unchanged): connect wallet, sign nonce, done.
2. **Wallet + OAuth link** (new): user first connects their wallet, then
   links Google/GitHub/Discord/Microsoft for email notifications and/or
   backup access. OAuth adds a ``linked_providers`` entry to the
   ``agent_permissions`` row. The wallet remains the primary key.
3. **Enterprise SSO** (existing, unchanged): handled by
   ``backend/enterprise/enterprise_sso.py`` with its own session model.

Supported providers
-------------------

* ``google``    — OIDC, verified email, widest reach
* ``github``    — OAuth 2.0, dev audience (MAXIA ICP)
* ``discord``   — OAuth 2.0, Web3-native audience
* ``microsoft`` — OIDC, enterprise overflow (reuses enterprise_sso creds)

Each provider is configured via env vars. Missing creds = provider
disabled. Frontend queries ``/api/oauth/providers`` to know which
buttons to render.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

log = logging.getLogger("maxia.oauth")


@dataclass(frozen=True)
class OAuthProvider:
    """Static provider configuration."""

    id: str
    display_name: str
    client_id: str
    client_secret: str
    authorize_url: str
    token_url: str
    userinfo_url: str
    scopes: tuple[str, ...]
    icon: str  # emoji or short label for the button
    verified_email_field: Optional[str]  # dict key that tells us email is verified
    email_field: str  # dict key that holds the email
    username_field: str  # dict key that holds the username/display name
    id_field: str  # dict key that holds the provider-specific user id

    @property
    def enabled(self) -> bool:
        return bool(self.client_id and self.client_secret)


def _env(*keys: str, default: str = "") -> str:
    """Return the first non-empty environment variable from ``keys``."""
    for k in keys:
        v = os.getenv(k, "").strip()
        if v:
            return v
    return default


def _load_google() -> OAuthProvider:
    return OAuthProvider(
        id="google",
        display_name="Google",
        client_id=_env("GOOGLE_OAUTH_CLIENT_ID", "OAUTH_GOOGLE_CLIENT_ID"),
        client_secret=_env("GOOGLE_OAUTH_CLIENT_SECRET", "OAUTH_GOOGLE_CLIENT_SECRET"),
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        userinfo_url="https://openidconnect.googleapis.com/v1/userinfo",
        scopes=("openid", "email", "profile"),
        icon="G",
        verified_email_field="email_verified",
        email_field="email",
        username_field="name",
        id_field="sub",
    )


def _load_github() -> OAuthProvider:
    return OAuthProvider(
        id="github",
        display_name="GitHub",
        client_id=_env("GITHUB_OAUTH_CLIENT_ID", "OAUTH_GITHUB_CLIENT_ID"),
        client_secret=_env("GITHUB_OAUTH_CLIENT_SECRET", "OAUTH_GITHUB_CLIENT_SECRET"),
        authorize_url="https://github.com/login/oauth/authorize",
        token_url="https://github.com/login/oauth/access_token",
        userinfo_url="https://api.github.com/user",
        # ``user:email`` lets us fetch primary verified email separately
        scopes=("read:user", "user:email"),
        icon="GH",
        verified_email_field=None,  # GitHub email verification is per-email, fetched separately
        email_field="email",
        username_field="login",
        id_field="id",
    )


def _load_discord() -> OAuthProvider:
    return OAuthProvider(
        id="discord",
        display_name="Discord",
        client_id=_env("DISCORD_OAUTH_CLIENT_ID", "OAUTH_DISCORD_CLIENT_ID"),
        client_secret=_env("DISCORD_OAUTH_CLIENT_SECRET", "OAUTH_DISCORD_CLIENT_SECRET"),
        authorize_url="https://discord.com/oauth2/authorize",
        token_url="https://discord.com/api/oauth2/token",
        userinfo_url="https://discord.com/api/users/@me",
        scopes=("identify", "email"),
        icon="DC",
        verified_email_field="verified",
        email_field="email",
        username_field="username",
        id_field="id",
    )


def _load_microsoft() -> OAuthProvider:
    return OAuthProvider(
        id="microsoft",
        display_name="Microsoft",
        # Reuses enterprise_sso creds when available, falls back to dedicated vars
        client_id=_env(
            "MICROSOFT_OAUTH_CLIENT_ID",
            "OAUTH_MICROSOFT_CLIENT_ID",
            "SSO_MICROSOFT_CLIENT_ID",
        ),
        client_secret=_env(
            "MICROSOFT_OAUTH_CLIENT_SECRET",
            "OAUTH_MICROSOFT_CLIENT_SECRET",
            "SSO_MICROSOFT_CLIENT_SECRET",
        ),
        authorize_url="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        token_url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
        userinfo_url="https://graph.microsoft.com/oidc/userinfo",
        scopes=("openid", "email", "profile", "User.Read"),
        icon="MS",
        verified_email_field="email_verified",
        email_field="email",
        username_field="name",
        id_field="sub",
    )


# Provider registry — instantiated lazily so env var changes pick up on reload
_REGISTRY: dict[str, OAuthProvider] = {}


def _build_registry() -> dict[str, OAuthProvider]:
    return {
        "google": _load_google(),
        "github": _load_github(),
        "discord": _load_discord(),
        "microsoft": _load_microsoft(),
    }


def get_provider(provider_id: str) -> Optional[OAuthProvider]:
    """Look up a provider by id. Returns None if unknown or disabled."""
    global _REGISTRY
    if not _REGISTRY:
        _REGISTRY = _build_registry()
    p = _REGISTRY.get(provider_id.lower())
    if p is None or not p.enabled:
        return None
    return p


def list_providers() -> list[dict[str, Any]]:
    """Return public provider metadata for the frontend.

    Only shows enabled providers (those with creds configured). Does
    NOT leak client secrets — just id, display name, icon, enabled.
    """
    global _REGISTRY
    if not _REGISTRY:
        _REGISTRY = _build_registry()
    return [
        {
            "id": p.id,
            "name": p.display_name,
            "icon": p.icon,
            "enabled": p.enabled,
            "login_url": f"/api/oauth/{p.id}/login",
        }
        for p in _REGISTRY.values()
        if p.enabled
    ]


def all_providers_including_disabled() -> list[dict[str, Any]]:
    """Return all providers including disabled ones (for admin UI)."""
    global _REGISTRY
    if not _REGISTRY:
        _REGISTRY = _build_registry()
    return [
        {
            "id": p.id,
            "name": p.display_name,
            "icon": p.icon,
            "enabled": p.enabled,
            "reason": (
                "OK" if p.enabled
                else f"Missing {p.id.upper()}_OAUTH_CLIENT_ID/SECRET env vars"
            ),
        }
        for p in _REGISTRY.values()
    ]


def reload_registry() -> dict[str, Any]:
    """Force a reload from env vars (useful after updating .env)."""
    global _REGISTRY
    _REGISTRY = _build_registry()
    return {
        "providers": [p.id for p in _REGISTRY.values() if p.enabled],
        "disabled": [p.id for p in _REGISTRY.values() if not p.enabled],
    }
