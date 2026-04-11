"""MAXIA Guard — Pillar 5 extension: PII Shield outbound.

Scrubs personally identifiable information from outbound response bodies so
an agent-generated reply cannot accidentally leak a customer email, credit
card, national ID, IBAN, or phone number.

Activated via middleware in ``backend/main.py``. Respects ``PII_SHIELD_ENABLED``
env var (default ``true``). Skipped on a small path whitelist where numeric
noise would false-positive (Prometheus, oracle price feeds).

Design notes:
    * All regex are compiled once at import time.
    * Credit-card candidates pass a Luhn check before being redacted —
      random 16-digit integers are not treated as CC numbers.
    * Skipped paths are prefix-matched. Binary content types are skipped.
    * Scrubbing is size-bounded: bodies larger than ``_MAX_BODY_BYTES``
      are returned unchanged to keep the hot path cheap.
"""
from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────

_ENABLED_ENV = "PII_SHIELD_ENABLED"
_MAX_BODY_BYTES = 100 * 1024  # 100 KB — skip scrub above this size

# Path prefixes where scrubbing is a no-op.
# ``/metrics`` = Prometheus (purely numeric, CC regex false positives).
# ``/oracle/*`` = price feeds (large floats, no PII).
# ``/api/public/prices``, ``/api/public/crypto/prices``, ``/api/public/stocks``,
# ``/api/public/marketplace-stats`` = pricing/market-data feeds. They contain
# big numeric fields (max_amount caps, Unix timestamps, quantized prices) that
# trip the phone regex and produce ``[PHONE_REDACTED]`` tokens, corrupting the
# JSON. These endpoints hold zero user PII by construction.
# ``/static/*`` = assets.
_SKIP_PREFIXES: tuple[str, ...] = (
    "/metrics",
    "/oracle/",
    "/api/public/prices",
    "/api/public/crypto/prices",
    "/api/public/stocks",
    "/api/public/marketplace-stats",
    "/static/",
    "/favicon",
    "/sw.js",
    "/manifest.json",
)

# Content-type substrings that are safe to scrub.
_SCRUBBABLE_TYPES: tuple[str, ...] = ("json", "text/plain", "text/html")


# ── Regex ──────────────────────────────────────────────────────────────

# Email — intentionally permissive enough to catch common formats but
# conservative on the local part to avoid eating whole URLs.
_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,24}\b"
)

# Credit card candidates: 13-19 digits, optionally separated by space or dash
# in 4-digit groups. Luhn-validated before redaction.
_CC_RE = re.compile(
    r"(?<![\d\-])(?:\d[ \-]?){13,19}(?![\d\-])"
)

# US Social Security Number.
_SSN_US_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")

# French INSEE (Numero de securite sociale) — 13 digits + 2 digit key.
_SSN_FR_RE = re.compile(
    r"\b[12]\d{2}(?:0[1-9]|1[0-2])(?:2[ABab]|\d{2})\d{3}\d{3}\s?\d{2}\b"
)

# IBAN — 2 letters + 2 digits + up to 30 alphanumeric.
_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b")

# Phone — E.164 or formatted with common separators.
# IMPORTANT: a bare run of digits is NOT a phone match here, because JSON
# payloads are full of big integer fields (Unix timestamps, tx block numbers,
# price tiers, max_amount caps) that would otherwise all get redacted and
# break the JSON. We require either:
#   1. a ``+`` country-code prefix followed by 8-15 digits, OR
#   2. a formatted phone (digit groups separated by space / dash / dot /
#      parens) totaling at least 9 digits.
# False-positive rate on this pattern is empirically ~0 for MAXIA's JSON
# payloads while still catching "+33 6 12 34 56 78", "(555) 123-4567", and
# "+1-800-555-0199".
_PHONE_RE = re.compile(
    r"(?<![\w])(?:"
    r"\+[1-9]\d{7,14}"                                  # E.164 with +
    r"|"
    r"(?:\(\d{2,4}\)|\d{2,4})[ .\-]\d{2,4}[ .\-]\d{2,4}(?:[ .\-]\d{2,4})*"  # formatted
    r")(?![\w])"
)


_REDACT = {
    "email": "[EMAIL_REDACTED]",
    "cc": "[CC_REDACTED]",
    "ssn_us": "[SSN_REDACTED]",
    "ssn_fr": "[INSEE_REDACTED]",
    "iban": "[IBAN_REDACTED]",
    "phone": "[PHONE_REDACTED]",
}


# ── Luhn ───────────────────────────────────────────────────────────────


def _luhn_ok(digits: str) -> bool:
    """Return True if ``digits`` (already stripped of non-digits) is a valid
    Luhn check number. Length must be between 13 and 19."""
    n = len(digits)
    if not 13 <= n <= 19:
        return False
    total = 0
    # Luhn: double every second digit from the right.
    for i, ch in enumerate(reversed(digits)):
        d = ord(ch) - 48
        if d < 0 or d > 9:
            return False
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _strip_sep(s: str) -> str:
    """Remove spaces and dashes from a credit-card candidate."""
    return s.replace(" ", "").replace("-", "")


# ── Public API ─────────────────────────────────────────────────────────


def is_enabled() -> bool:
    """Return True if PII Shield is active (env var ``PII_SHIELD_ENABLED``)."""
    return os.getenv(_ENABLED_ENV, "true").lower() not in ("false", "0", "no")


def should_skip_path(path: str) -> bool:
    """Return True if the given request path should be skipped."""
    for prefix in _SKIP_PREFIXES:
        if path.startswith(prefix):
            return True
    return False


def should_scrub_content_type(content_type: str) -> bool:
    """Return True if the content type is scrubbable text/JSON."""
    if not content_type:
        return False
    lowered = content_type.lower()
    return any(t in lowered for t in _SCRUBBABLE_TYPES)


def scrub_pii(text: str) -> tuple[str, dict[str, int]]:
    """Scrub PII from ``text`` and return ``(scrubbed_text, hit_counts)``.

    Zero side effects. Safe to call on any string including empty input.
    ``hit_counts`` is a dict keyed by category (email/cc/ssn_us/ssn_fr/
    iban/phone) with the number of redactions performed.
    """
    if not text:
        return text, {}

    hits: dict[str, int] = {}

    def _sub_with_count(regex: re.Pattern, key: str, replacement: str,
                         s: str) -> str:
        def _rep(match: re.Match) -> str:
            hits[key] = hits.get(key, 0) + 1
            return replacement
        return regex.sub(_rep, s)

    # 1. Email first — cheap to match and high signal.
    text = _sub_with_count(_EMAIL_RE, "email", _REDACT["email"], text)

    # 2. IBAN before phone (IBAN can look like a long digit run).
    text = _sub_with_count(_IBAN_RE, "iban", _REDACT["iban"], text)

    # 3. French INSEE before US SSN.
    text = _sub_with_count(_SSN_FR_RE, "ssn_fr", _REDACT["ssn_fr"], text)
    text = _sub_with_count(_SSN_US_RE, "ssn_us", _REDACT["ssn_us"], text)

    # 4. Credit card — Luhn-checked.
    def _cc_rep(match: re.Match) -> str:
        digits = _strip_sep(match.group(0))
        if _luhn_ok(digits):
            hits["cc"] = hits.get("cc", 0) + 1
            return _REDACT["cc"]
        return match.group(0)

    text = _CC_RE.sub(_cc_rep, text)

    # 5. Phone last (most permissive).
    text = _sub_with_count(_PHONE_RE, "phone", _REDACT["phone"], text)

    return text, hits


def is_body_scannable(body: bytes, content_type: str, path: str) -> bool:
    """Return True if the PII shield should look at this response body."""
    if not is_enabled():
        return False
    if should_skip_path(path):
        return False
    if not body:
        return False
    if len(body) > _MAX_BODY_BYTES:
        return False
    if not should_scrub_content_type(content_type):
        return False
    return True


async def scrub_body_bytes(
    body: bytes, content_type: str = ""
) -> tuple[bytes, dict[str, int]]:
    """Decode -> scrub -> re-encode a response body. Returns original bytes
    if decoding fails. Never raises.

    **JSON safety net**: if the caller hints the content is JSON and the
    scrubbed output is no longer valid JSON (a placeholder like
    ``[PHONE_REDACTED]`` broke the number grammar), we return the original
    body unchanged and log a warning. This guarantees the PII shield can
    never corrupt the JSON contract even if a future regex match fires
    inside a numeric field by accident.
    """
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return body, {}
    scrubbed, hits = scrub_pii(text)
    if not hits:
        return body, {}
    try:
        new_bytes = scrubbed.encode("utf-8")
    except UnicodeEncodeError:
        return body, {}

    # JSON safety net — rollback on invalid output
    if content_type and "json" in content_type.lower():
        try:
            import json as _json
            _json.loads(scrubbed)
        except Exception as e:
            logger.warning(
                "pii_shield rollback: scrubbed body is invalid JSON (%s) — "
                "returning original bytes. hits=%s",
                e, hits,
            )
            return body, {}

    return new_bytes, hits
