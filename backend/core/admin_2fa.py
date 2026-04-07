"""MAXIA Admin 2FA — TOTP (Google Authenticator) for admin routes.

When enabled, admin API calls require an X-Admin-TOTP header with a valid 6-digit code
in addition to the existing X-Admin-Key authentication.

Setup:
1. POST /api/admin/2fa/setup (with X-Admin-Key) → returns secret + QR provisioning URI
2. POST /api/admin/2fa/verify (with X-Admin-Key + X-Admin-TOTP) → confirms setup
3. All subsequent admin calls require both X-Admin-Key AND X-Admin-TOTP

The TOTP secret is stored in ADMIN_TOTP_SECRET env var or a local file.
"""
import json
import logging
import os
from pathlib import Path

import pyotp
from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/2fa", tags=["admin-2fa"])

_TOTP_FILE = Path(__file__).parent.parent / ".totp_secret"
_totp_secret: str = ""
_totp_enabled: bool = False


def _load_totp():
    """Load TOTP secret from env or file."""
    global _totp_secret, _totp_enabled

    # Priority: env var > file
    env_secret = os.getenv("ADMIN_TOTP_SECRET", "")
    if env_secret and len(env_secret) >= 16:
        _totp_secret = env_secret
        _totp_enabled = True
        return

    if _TOTP_FILE.exists():
        try:
            data = json.loads(_TOTP_FILE.read_text(encoding="utf-8"))
            if data.get("enabled") and data.get("secret"):
                _totp_secret = data["secret"]
                _totp_enabled = True
                return
        except (json.JSONDecodeError, OSError):
            pass

    _totp_enabled = False


def _save_totp(secret: str, enabled: bool):
    """Persist TOTP secret to local file."""
    global _totp_secret, _totp_enabled
    _totp_secret = secret
    _totp_enabled = enabled
    try:
        _TOTP_FILE.write_text(
            json.dumps({"secret": secret, "enabled": enabled}, indent=2),
            encoding="utf-8")
    except OSError as e:
        logger.error("[2FA] Failed to save TOTP secret: %s", e)


# Load on import
_load_totp()


def is_2fa_enabled() -> bool:
    """Check if 2FA is currently enabled for admin."""
    return _totp_enabled


def verify_totp(code: str) -> bool:
    """Verify a TOTP code. Returns True if valid."""
    if not _totp_enabled or not _totp_secret:
        return True  # 2FA not enabled, always pass
    if not code or len(code) != 6 or not code.isdigit():
        return False
    totp = pyotp.TOTP(_totp_secret)
    return totp.verify(code, valid_window=1)  # Allow 30s window


# ══════════════════════════════════════════
# API Endpoints
# ══════════════════════════════════════════

@router.post("/setup")
async def setup_2fa(request: Request):
    """Generate a new TOTP secret for admin 2FA setup.

    Returns the secret and a provisioning URI for Google Authenticator.
    Requires X-Admin-Key. Does NOT enable 2FA until /verify is called.
    """
    from core.security import require_admin
    require_admin(request)

    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret)
    provisioning_uri = totp.provisioning_uri(
        name="admin@maxiaworld.app",
        issuer_name="MAXIA")

    # Store but don't enable yet (need verification)
    _save_totp(secret, False)

    logger.info("[2FA] Setup initiated — awaiting verification")

    return {
        "status": "ok",
        "secret": secret,
        "provisioning_uri": provisioning_uri,
        "instructions": [
            "1. Open Google Authenticator (or any TOTP app)",
            "2. Scan the QR code or enter the secret manually",
            "3. POST /api/admin/2fa/verify with X-Admin-TOTP header to confirm",
        ],
    }


@router.post("/verify")
async def verify_2fa(request: Request):
    """Verify and enable 2FA by providing a valid TOTP code.

    Requires X-Admin-Key + X-Admin-TOTP headers.
    Once verified, all admin endpoints will require TOTP.
    """
    from core.security import require_admin
    require_admin(request)

    code = request.headers.get("X-Admin-TOTP", "")
    if not code or len(code) != 6 or not code.isdigit():
        raise HTTPException(400, "Provide X-Admin-TOTP header with 6-digit code")

    if not _totp_secret:
        raise HTTPException(400, "No TOTP secret configured. Call /setup first.")

    totp = pyotp.TOTP(_totp_secret)
    if not totp.verify(code, valid_window=1):
        raise HTTPException(401, "Invalid TOTP code. Check your authenticator app.")

    # Enable 2FA
    _save_totp(_totp_secret, True)

    logger.info("[2FA] Enabled successfully")

    return {
        "status": "ok",
        "message": "2FA enabled. All admin endpoints now require X-Admin-TOTP header.",
        "enabled": True,
    }


@router.post("/disable")
async def disable_2fa(request: Request):
    """Disable 2FA. Requires X-Admin-Key + valid X-Admin-TOTP."""
    from core.security import require_admin
    require_admin(request)

    code = request.headers.get("X-Admin-TOTP", "")
    if _totp_enabled:
        if not verify_totp(code):
            raise HTTPException(401, "Valid TOTP code required to disable 2FA")

    _save_totp("", False)
    logger.info("[2FA] Disabled")

    return {"status": "ok", "message": "2FA disabled.", "enabled": False}


@router.get("/status")
async def status_2fa(request: Request):
    """Check if 2FA is currently enabled. Requires X-Admin-Key."""
    from core.security import require_admin
    require_admin(request)
    return {"enabled": _totp_enabled}
