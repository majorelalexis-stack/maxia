"""MAXIA Guard Q2d — Credential Vault (AES-256 via Fernet).

Scaffold-only today: the vault has no consumer yet, but the API is stable
so that when an agent needs to store a third-party API key (OpenAI,
Anthropic, a bespoke service), the encryption path is already in place.

Design:
    * Master key is read from ``VAULT_MASTER_KEY`` (base64 Fernet key).
      In production this should come from a secret manager / KMS; in dev
      a fresh key can be generated with ``generate_master_key()`` and
      written to ``.env``.
    * Plaintext is only ever held in memory during encrypt/decrypt calls.
      Ciphertext is what goes to the DB (future ``agent_secrets`` table).
    * Fernet provides authenticated symmetric encryption (AES-128-CBC +
      HMAC-SHA256). For 256-bit use the optional ``MultiFernet`` key
      rotation chain by passing a list via ``VAULT_MASTER_KEYS``.
    * The module degrades gracefully: if ``cryptography`` is not installed
      or the master key is missing, ``is_available()`` returns False and
      ``encrypt_secret`` raises ``VaultUnavailable``.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_VAULT_ENV = "VAULT_MASTER_KEY"
_VAULT_MULTI_ENV = "VAULT_MASTER_KEYS"  # comma-separated for rotation

# Lazy-loaded Fernet instance.
_fernet = None
_init_attempted = False


class VaultUnavailable(RuntimeError):
    """Raised when the vault cannot encrypt/decrypt (missing key or lib)."""


def generate_master_key() -> str:
    """Return a fresh base64 Fernet key. For one-time bootstrap only."""
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode("ascii")


def _load_fernet():
    """Lazy-load the Fernet/MultiFernet instance from env vars."""
    global _fernet, _init_attempted
    if _init_attempted:
        return _fernet
    _init_attempted = True

    try:
        from cryptography.fernet import Fernet, MultiFernet
    except Exception as e:
        logger.debug("vault: cryptography library unavailable: %s", e)
        return None

    rotation = os.getenv(_VAULT_MULTI_ENV, "").strip()
    if rotation:
        keys = [Fernet(k.strip().encode("ascii"))
                for k in rotation.split(",") if k.strip()]
        if keys:
            _fernet = MultiFernet(keys)
            return _fernet

    single = os.getenv(_VAULT_ENV, "").strip()
    if not single:
        logger.debug("vault: %s not set, vault unavailable", _VAULT_ENV)
        return None

    try:
        _fernet = Fernet(single.encode("ascii"))
    except Exception as e:
        logger.warning("vault: invalid %s: %s", _VAULT_ENV, e)
        return None

    return _fernet


def is_available() -> bool:
    """Return True if the vault has a usable master key."""
    return _load_fernet() is not None


def encrypt_secret(plaintext: str, *, context: str = "") -> str:
    """Encrypt a secret and return the base64 ciphertext (Fernet token).

    ``context`` is an optional tag for logging (not stored). Raises
    ``VaultUnavailable`` if no master key is configured.
    """
    f = _load_fernet()
    if f is None:
        raise VaultUnavailable(
            f"vault not configured: set {_VAULT_ENV} in .env "
            f"(generate one with generate_master_key())"
        )
    if plaintext is None:
        plaintext = ""
    token = f.encrypt(plaintext.encode("utf-8"))
    if context:
        logger.debug("vault: encrypted secret (context=%s, len=%d)",
                     context, len(plaintext))
    return token.decode("ascii")


def decrypt_secret(token: str, *, context: str = "") -> str:
    """Decrypt a Fernet token. Raises ``VaultUnavailable`` if no master key,
    or ``cryptography.fernet.InvalidToken`` on tamper / wrong key."""
    f = _load_fernet()
    if f is None:
        raise VaultUnavailable(f"vault not configured: set {_VAULT_ENV}")
    plain = f.decrypt(token.encode("ascii")).decode("utf-8")
    if context:
        logger.debug("vault: decrypted secret (context=%s)", context)
    return plain


def rotate_key() -> None:
    """Hot-reload the master key(s) from env. Call after updating .env
    without restarting the process."""
    global _fernet, _init_attempted
    _fernet = None
    _init_attempted = False
    _load_fernet()
