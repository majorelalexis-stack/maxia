"""MAXIA Telegram bot — deep link parser & attribution tracker (P4C).

Deep links format: ``t.me/MAXIA_AI_bot?start=PAYLOAD``
Telegram sends ``/start PAYLOAD`` (or ``/start`` alone) when the user taps
the link. We parse PAYLOAD into a structured ``DeepLink`` and record it
in an append-only store, idempotent per (user_id, payload).

Supported payload prefixes (all ≤ 64 bytes, base64url or alnum/_/-):

    ref_<code>      -> referral attribution
    region_<code>   -> regional onboarding (forces language)
    svc_<id>        -> deep-link to a service/agent
    token_<symbol>  -> deep-link to a trading pair
    app_<screen>    -> deep-link inside the Mini App

Any unknown prefix falls back to ``kind="unknown"`` but is still stored
so we can audit sources.

Store is a protocol so tests don't need a DB. Default implementation is
in-memory and thread-safe.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Final, Optional, Protocol

logger = logging.getLogger("maxia.telegram_deeplinks")

# ── Limits ──
MAX_PAYLOAD_LENGTH: int = 64
# Telegram restricts /start payload to alnum + _ and - for URL-safety.
_PAYLOAD_RE: Final[re.Pattern] = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")

KNOWN_KINDS: Final[frozenset[str]] = frozenset({
    "ref", "region", "svc", "token", "app",
})


@dataclass(frozen=True)
class DeepLink:
    """Immutable parsed deep link."""
    raw: str          # original payload as received from Telegram
    kind: str         # ref | region | svc | token | app | unknown
    value: str        # the suffix after prefix_, e.g. "alexis123"
    valid: bool       # False if raw failed regex, size, or unknown prefix

    @property
    def is_known(self) -> bool:
        return self.kind in KNOWN_KINDS


def parse_start_payload(raw: object) -> DeepLink:
    """Parse a ``/start PAYLOAD`` value into a :class:`DeepLink`.

    Invalid or missing payloads return ``DeepLink(raw="", kind="unknown",
    value="", valid=False)`` so callers can always use the result without
    null checks.
    """
    if not isinstance(raw, str):
        return DeepLink(raw="", kind="unknown", value="", valid=False)

    cleaned = raw.strip()
    if not cleaned:
        return DeepLink(raw="", kind="unknown", value="", valid=False)

    if len(cleaned) > MAX_PAYLOAD_LENGTH:
        return DeepLink(raw=cleaned[:MAX_PAYLOAD_LENGTH], kind="unknown",
                        value="", valid=False)

    if not _PAYLOAD_RE.match(cleaned):
        return DeepLink(raw=cleaned, kind="unknown", value="", valid=False)

    # Split on first underscore only
    if "_" not in cleaned:
        return DeepLink(raw=cleaned, kind="unknown", value=cleaned, valid=False)

    prefix, _, suffix = cleaned.partition("_")
    prefix = prefix.lower()
    if prefix not in KNOWN_KINDS:
        return DeepLink(raw=cleaned, kind="unknown", value=suffix, valid=False)
    if not suffix:
        return DeepLink(raw=cleaned, kind=prefix, value="", valid=False)

    return DeepLink(raw=cleaned, kind=prefix, value=suffix, valid=True)


# ── Extraction from raw /start text ──


_START_RE = re.compile(r"^/start(?:\s+(\S+))?\s*$", re.I)


def extract_start_payload(text: object) -> str:
    """Extract the payload from a ``/start [PAYLOAD]`` Telegram message.

    Returns empty string if the text is not a ``/start`` command or has no
    payload.
    """
    if not isinstance(text, str):
        return ""
    match = _START_RE.match(text.strip())
    if not match:
        return ""
    return (match.group(1) or "").strip()


# ── Attribution store ──


@dataclass(frozen=True)
class AttributionRecord:
    """Immutable record of a deep-link attribution event."""
    user_id: int
    payload: str
    kind: str
    value: str
    first_seen_at: float


class AttributionStore(Protocol):
    """Idempotent attribution store. First write per (user_id, payload) wins."""

    def record(
        self,
        user_id: int,
        link: DeepLink,
        now: float,
    ) -> tuple[AttributionRecord, bool]:
        """Return (record, is_new). If already seen, is_new is False."""
        ...

    def get(self, user_id: int, payload: str) -> Optional[AttributionRecord]:
        ...

    def count_by_kind(self) -> dict[str, int]:
        ...


@dataclass
class InMemoryAttributionStore:
    """Thread-safe in-memory attribution store (default implementation)."""

    _data: dict[tuple[int, str], AttributionRecord] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def record(
        self,
        user_id: int,
        link: DeepLink,
        now: float,
    ) -> tuple[AttributionRecord, bool]:
        if not isinstance(user_id, int) or user_id <= 0:
            raise ValueError("user_id must be a positive int")
        key = (user_id, link.raw)
        with self._lock:
            existing = self._data.get(key)
            if existing is not None:
                return existing, False
            record = AttributionRecord(
                user_id=user_id,
                payload=link.raw,
                kind=link.kind,
                value=link.value,
                first_seen_at=now,
            )
            self._data[key] = record
            return record, True

    def get(self, user_id: int, payload: str) -> Optional[AttributionRecord]:
        with self._lock:
            return self._data.get((user_id, payload))

    def count_by_kind(self) -> dict[str, int]:
        with self._lock:
            counts: dict[str, int] = {}
            for rec in self._data.values():
                counts[rec.kind] = counts.get(rec.kind, 0) + 1
            return counts


# ── High-level helper ──


def track_start(
    user_id: int,
    start_text: str,
    store: AttributionStore,
    clock: Optional[callable] = None,
) -> tuple[DeepLink, Optional[AttributionRecord], bool]:
    """Parse a ``/start ...`` message, record attribution, return outcome.

    Returns:
        (link, record_or_None, is_new_attribution)

        * ``link``: the parsed DeepLink (never None, valid flag tells you if
          the payload was understood).
        * ``record``: the AttributionRecord stored, or None if the link was
          invalid / empty (nothing is recorded in that case).
        * ``is_new_attribution``: True if this is the first time we record
          this (user_id, payload) pair, False on repeat clicks.
    """
    payload = extract_start_payload(start_text)
    link = parse_start_payload(payload)

    if not link.valid:
        # Still log for audit but don't store so the table stays clean.
        logger.info(
            "[deeplink] user=%s invalid/empty payload=%r",
            user_id, payload,
        )
        return link, None, False

    now = (clock or time.time)()
    record, is_new = store.record(user_id, link, now)
    if is_new:
        logger.info(
            "[deeplink] user=%s NEW kind=%s value=%s",
            user_id, link.kind, link.value,
        )
    else:
        logger.debug(
            "[deeplink] user=%s repeat kind=%s value=%s",
            user_id, link.kind, link.value,
        )
    return link, record, is_new
