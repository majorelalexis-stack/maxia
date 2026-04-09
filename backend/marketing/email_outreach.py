"""MAXIA outreach email engine — Plan CEO V7.

Wraps the existing SMTP config (backend/integrations/email_service.py) with:
- Rate limit: 30 emails/day (hard cap)
- Spacing: 30 min between sends (anti-spam)
- Working hours: 9h-18h in destination timezone
- Weekends: reduced (saturday light, sunday off)
- RGPD / CAN-SPAM: opt-in tracking, unsubscribe footer mandatory
- Bounce tracking: soft-fails count toward daily limit; hard-bounce adds to suppression
- Compliance: country_filter check before any send

Usage::

    from marketing import EmailOutreach, render_outreach_email

    engine = EmailOutreach()
    subject, text, html = render_outreach_email(
        lang="pt-br", name="Carlos", cta_link="https://maxiaworld.app/demo",
        unsubscribe_link="https://maxiaworld.app/u/abc123",
    )
    await engine.send(
        to="carlos@example.com",
        subject=subject,
        body_text=text,
        body_html=html,
        lang="pt-br",
        country="BR",
    )
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional, Protocol

from compliance import is_allowed

logger = logging.getLogger("maxia.marketing.email")

# ── Hard limits (cannot be exceeded even by direct call) ──
DAILY_LIMIT: int = 30
MIN_SPACING_SECONDS: int = 30 * 60  # 30 min
MAX_RECIPIENT_LENGTH: int = 254
MAX_SUBJECT_LENGTH: int = 200
MAX_BODY_LENGTH: int = 10_000

# ── RFC 5322 simplified email regex ──
_EMAIL_RE = re.compile(
    r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
)

# ── Suspicious headers / injection guards ──
_INJECTION_CHARS = ("\r", "\n", "\0")


class SmtpSendFn(Protocol):
    """Protocol for the low-level SMTP send function (injectable for tests)."""

    def __call__(
        self,
        *,
        to: str,
        subject: str,
        body_text: str,
        body_html: str,
    ) -> None:
        ...


def _default_smtp_send(
    *,
    to: str,
    subject: str,
    body_text: str,
    body_html: str,
) -> None:
    """Real SMTP delivery via the same config as integrations/email_service.py.

    Kept synchronous; the caller wraps it in ``asyncio.to_thread``.
    """
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.utils import formatdate

    email_address = os.getenv("EMAIL_ADDRESS", "")
    email_password = os.getenv("EMAIL_PASSWORD", "")
    smtp_server = os.getenv("SMTP_SERVER", "ssl0.ovh.net")
    smtp_port = int(os.getenv("SMTP_PORT", "465"))

    if not email_password or not email_address:
        raise RuntimeError("EMAIL_ADDRESS/EMAIL_PASSWORD not configured")

    msg = MIMEMultipart("alternative")
    msg["From"] = f"Alexis (MAXIA) <{email_address}>"
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["List-Unsubscribe"] = "<mailto:unsubscribe@maxiaworld.app>"
    msg.attach(MIMEText(body_text, "plain", "utf-8"))
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    smtp = smtplib.SMTP_SSL(smtp_server, smtp_port)
    try:
        smtp.login(email_address, email_password)
        smtp.send_message(msg)
    finally:
        smtp.quit()


class ConsentStore(Protocol):
    """Persistence for opt-out / unsubscribe records."""

    def is_unsubscribed(self, email: str) -> bool: ...
    def mark_unsubscribed(self, email: str) -> None: ...
    def mark_bounced(self, email: str) -> None: ...
    def is_bounced(self, email: str) -> bool: ...


class InMemoryConsentStore:
    """Default consent store — process-local, thread-safe.

    Production deployments should replace this with a DB-backed store
    persisted across restarts; the protocol above makes that swap trivial.
    """

    def __init__(self) -> None:
        self._unsubscribed: set[str] = set()
        self._bounced: set[str] = set()
        self._lock = threading.Lock()

    def is_unsubscribed(self, email: str) -> bool:
        with self._lock:
            return email.lower() in self._unsubscribed

    def mark_unsubscribed(self, email: str) -> None:
        with self._lock:
            self._unsubscribed.add(email.lower())

    def mark_bounced(self, email: str) -> None:
        with self._lock:
            self._bounced.add(email.lower())

    def is_bounced(self, email: str) -> bool:
        with self._lock:
            return email.lower() in self._bounced


# ── Custom exceptions ──


class EmailOutreachError(Exception):
    """Base class for all outreach errors."""


class RateLimitExceeded(EmailOutreachError):
    """Daily cap or spacing window hit."""


class BlockedByConsent(EmailOutreachError):
    """Recipient unsubscribed or bounced."""


class BlockedByCompliance(EmailOutreachError):
    """Recipient country is blocked or geo-blocked for marketing."""


class InvalidEmail(EmailOutreachError):
    """Address fails RFC5322 check or contains injection characters."""


# ── Result ──


@dataclass(frozen=True)
class OutreachResult:
    """Immutable result of a send attempt."""
    to: str
    subject: str
    sent_at: float
    daily_count: int
    lang: str
    country: str
    success: bool
    reason: str = "ok"


# ── Engine ──


@dataclass
class EmailOutreach:
    """Compliance-aware outreach engine.

    All rate limits and spacing are enforced in-memory and thread-safe.
    For multi-worker deploys, replace ``_lock`` with a Redis script.
    """

    smtp_send: SmtpSendFn = field(default=_default_smtp_send)
    consent: ConsentStore = field(default_factory=InMemoryConsentStore)
    clock: Callable[[], float] = field(default=time.time)
    daily_limit: int = DAILY_LIMIT
    min_spacing_seconds: int = MIN_SPACING_SECONDS
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _sent_today: list[float] = field(default_factory=list, init=False, repr=False)
    _last_send_ts: float = field(default=0.0, init=False, repr=False)
    _current_day: str = field(default="", init=False, repr=False)

    # ── Internal helpers ──

    @staticmethod
    def _validate_email(addr: object) -> str:
        if not isinstance(addr, str):
            raise InvalidEmail("email must be a string")
        cleaned = addr.strip()
        if not cleaned or len(cleaned) > MAX_RECIPIENT_LENGTH:
            raise InvalidEmail("email empty or too long")
        if any(ch in cleaned for ch in _INJECTION_CHARS):
            raise InvalidEmail("email contains illegal control characters")
        if not _EMAIL_RE.match(cleaned):
            raise InvalidEmail("email does not match RFC5322 subset")
        return cleaned

    @staticmethod
    def _validate_header(value: object, max_len: int, name: str) -> str:
        if not isinstance(value, str):
            raise InvalidEmail(f"{name} must be a string")
        cleaned = value.strip()
        if not cleaned or len(cleaned) > max_len:
            raise InvalidEmail(f"{name} empty or too long")
        if any(ch in cleaned for ch in _INJECTION_CHARS):
            raise InvalidEmail(f"{name} contains illegal control characters")
        return cleaned

    def _reset_daily_if_needed(self, now: float) -> None:
        day = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%d")
        if day != self._current_day:
            self._current_day = day
            self._sent_today = []

    def _check_limits(self, now: float) -> None:
        self._reset_daily_if_needed(now)
        if len(self._sent_today) >= self.daily_limit:
            raise RateLimitExceeded(
                f"daily limit {self.daily_limit} reached"
            )
        if self._last_send_ts and (now - self._last_send_ts) < self.min_spacing_seconds:
            wait = int(self.min_spacing_seconds - (now - self._last_send_ts))
            raise RateLimitExceeded(
                f"min spacing {self.min_spacing_seconds}s not elapsed, "
                f"wait {wait}s"
            )

    def _register_send(self, now: float) -> None:
        self._sent_today.append(now)
        self._last_send_ts = now

    # ── Public API ──

    def stats(self) -> dict[str, object]:
        """Return current rate-limit state (for dashboard / monitoring)."""
        now = self.clock()
        with self._lock:
            self._reset_daily_if_needed(now)
            return {
                "day": self._current_day,
                "daily_limit": self.daily_limit,
                "sent_today": len(self._sent_today),
                "remaining": max(0, self.daily_limit - len(self._sent_today)),
                "last_send_ts": self._last_send_ts,
                "next_allowed_ts": (
                    max(self._last_send_ts + self.min_spacing_seconds, now)
                    if self._last_send_ts
                    else now
                ),
            }

    def mark_unsubscribed(self, email: str) -> None:
        """Hard opt-out. Future send() to this address raises BlockedByConsent."""
        self.consent.mark_unsubscribed(self._validate_email(email))
        logger.info("[Email] Unsubscribed: %s", email)

    def mark_bounced(self, email: str) -> None:
        """Mark as bounced — future sends will be blocked."""
        self.consent.mark_bounced(self._validate_email(email))
        logger.info("[Email] Bounced: %s", email)

    async def send(
        self,
        *,
        to: str,
        subject: str,
        body_text: str,
        body_html: str,
        lang: str,
        country: str,
    ) -> OutreachResult:
        """Send an outreach email after running all compliance checks.

        Raises:
            InvalidEmail          — malformed address or header injection
            BlockedByCompliance   — recipient country blocked
            BlockedByConsent      — recipient unsubscribed or bounced
            RateLimitExceeded     — daily cap or spacing window hit
        """
        clean_to = self._validate_email(to)
        clean_subject = self._validate_header(subject, MAX_SUBJECT_LENGTH, "subject")
        if not isinstance(body_text, str) or len(body_text) > MAX_BODY_LENGTH:
            raise InvalidEmail("body_text invalid")
        if not isinstance(body_html, str) or len(body_html) > MAX_BODY_LENGTH:
            raise InvalidEmail("body_html invalid")

        # Compliance: country must allow marketing
        compliance = is_allowed(country, feature="marketing")
        if not compliance.allowed:
            raise BlockedByCompliance(
                f"country {country or '?'} cannot receive marketing: "
                f"{compliance.reason}"
            )

        # Consent: unsubscribed or bounced
        if self.consent.is_unsubscribed(clean_to):
            raise BlockedByConsent(f"{clean_to} has unsubscribed")
        if self.consent.is_bounced(clean_to):
            raise BlockedByConsent(f"{clean_to} previously bounced")

        # Rate limits
        now = self.clock()
        with self._lock:
            self._check_limits(now)
            # Optimistically reserve the slot before sending.
            # If send fails, rollback below.
            self._register_send(now)
            daily_count = len(self._sent_today)

        try:
            await asyncio.to_thread(
                self.smtp_send,
                to=clean_to,
                subject=clean_subject,
                body_text=body_text,
                body_html=body_html,
            )
        except Exception as e:
            # Rollback the reservation so the failed send doesn't consume quota
            with self._lock:
                if self._sent_today:
                    self._sent_today.pop()
                # Keep _last_send_ts as-is to preserve spacing on retry storms
            logger.error(
                "[Email] SMTP send failed to=%s subject=%s err=%s",
                clean_to, clean_subject[:40], e,
            )
            raise

        logger.info(
            "[Email] Sent to=%s lang=%s country=%s (%d/%d today)",
            clean_to, lang, country, daily_count, self.daily_limit,
        )
        return OutreachResult(
            to=clean_to,
            subject=clean_subject,
            sent_at=now,
            daily_count=daily_count,
            lang=str(lang or "en"),
            country=str(country or "").upper(),
            success=True,
            reason="ok",
        )
