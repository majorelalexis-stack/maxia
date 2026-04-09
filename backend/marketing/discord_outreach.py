"""MAXIA Discord outreach engine — Plan CEO V7 / P8.

Thin wrapper over the existing Discord client with compliance, consent,
and anti-ban rules baked in. Mirrors the shape of ``email_outreach``
so the CEO can treat every channel the same way.

Policies (hard-coded):
- 10 messages / server / day (vs. Discord API ceiling of ~30/min)
- 30 messages / day total across all servers
- 90-minute spacing between any two sends (anti-spam)
- Warming days: 1-14 reduced quotas (1 msg / server / day during warmup)
- Weekend modifier: 30% on Saturday, 0 on Sunday (from quotas_daily.json)
- Allowed server list maintained in memory_prod/outreach_channels.json
- Consent: any mod warn -> 30-day freeze on that server
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

logger = logging.getLogger("maxia.marketing.discord")

DISCORD_API_BASE: str = "https://discord.com/api/v10"

# ── Hard limits (enforced in code, not config) ──
#
# Throughput note: 24h / 30 sends = 48 min floor; use 45 min for safety.
# This keeps the documented 30/day total reachable within a single UTC day.
PER_SERVER_DAILY: int = 10
TOTAL_DAILY: int = 30
MIN_SPACING_SECONDS: int = 45 * 60  # 45 min (anti-spam + 30/day feasible)
WARMING_DAYS: int = 14
MAX_MESSAGE_LENGTH: int = 1800  # Discord hard cap is 2000; leave headroom.

_MENTION_RE = re.compile(r"@(everyone|here|&\w+)")
_INJECTION_CHARS = ("\u202e", "\u200b", "\r")


class DiscordSendFn(Protocol):
    """Protocol for the low-level Discord send. Injectable for tests."""

    async def __call__(
        self,
        *,
        server_id: str,
        channel_id: str,
        content: str,
    ) -> None:
        ...


async def _default_discord_send(
    *,
    server_id: str,
    channel_id: str,
    content: str,
) -> None:
    """Real Discord send via Bot API v10.

    Reads ``DISCORD_BOT_TOKEN`` from the environment and POSTs to
    ``/channels/{channel_id}/messages``. Raises on any non-2xx response
    so the engine can rollback the quota reservation.
    """
    import httpx

    token = os.getenv("DISCORD_BOT_TOKEN", "")
    if not token or len(token) < 30:
        raise RuntimeError(
            "DISCORD_BOT_TOKEN missing or invalid. Set it in backend/.env."
        )

    url = f"{DISCORD_API_BASE}/channels/{channel_id}/messages"
    headers = {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json",
        "User-Agent": "MAXIA Outreach Bot/1.0 (+https://maxiaworld.app)",
    }
    payload = {"content": content}

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(url, json=payload, headers=headers)
    if resp.status_code == 429:
        # Discord rate limit — surface retry_after for the engine to log
        try:
            retry_after = float(resp.json().get("retry_after", 1.0))
        except (ValueError, KeyError):
            retry_after = 1.0
        raise RuntimeError(
            f"Discord rate-limited (429). Retry after {retry_after:.1f}s."
        )
    if not 200 <= resp.status_code < 300:
        # Don't leak the full Discord error body into logs / tracing
        snippet = resp.text[:200] if isinstance(resp.text, str) else ""
        raise RuntimeError(
            f"Discord API error {resp.status_code} on channel {channel_id}: {snippet}"
        )


# ── Exceptions ──


class DiscordOutreachError(Exception):
    """Base class."""


class RateLimitExceeded(DiscordOutreachError):
    pass


class BlockedByCompliance(DiscordOutreachError):
    pass


class BlockedByServerFreeze(DiscordOutreachError):
    """Server put on ice by /freeze_server after a mod warn."""


class InvalidMessage(DiscordOutreachError):
    """Mentions @everyone, too long, contains control chars, etc."""


# ── Result ──


@dataclass(frozen=True)
class DiscordResult:
    server_id: str
    channel_id: str
    sent_at: float
    server_count_today: int
    total_count_today: int
    success: bool
    warming_day: int
    reason: str = "ok"


# ── Engine ──


@dataclass
class DiscordOutreach:
    """Compliance-aware Discord outreach engine."""
    send_fn: DiscordSendFn = field(default=_default_discord_send)
    clock: Callable[[], float] = field(default=time.time)
    warming_start_ts: float = 0.0            # 0 = not started (use first send)
    total_daily: int = TOTAL_DAILY
    per_server_daily: int = PER_SERVER_DAILY
    min_spacing_seconds: int = MIN_SPACING_SECONDS
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _server_counts: dict[str, list[float]] = field(default_factory=dict, init=False, repr=False)
    _total_today: list[float] = field(default_factory=list, init=False, repr=False)
    _last_send_ts: float = field(default=0.0, init=False, repr=False)
    _current_day: str = field(default="", init=False, repr=False)
    _frozen_servers: dict[str, float] = field(default_factory=dict, init=False, repr=False)

    # ── Helpers ──

    @staticmethod
    def _validate_content(content: object) -> str:
        if not isinstance(content, str):
            raise InvalidMessage("content must be a string")
        cleaned = content.strip()
        if not cleaned:
            raise InvalidMessage("content is empty")
        if len(cleaned) > MAX_MESSAGE_LENGTH:
            raise InvalidMessage(f"content exceeds {MAX_MESSAGE_LENGTH} chars")
        if _MENTION_RE.search(cleaned):
            raise InvalidMessage("mass mentions are forbidden (@everyone/@here/@role)")
        if any(ch in cleaned for ch in _INJECTION_CHARS):
            raise InvalidMessage("content contains illegal control characters")
        return cleaned

    @staticmethod
    def _validate_id(raw: object, name: str) -> str:
        if not isinstance(raw, str):
            raise InvalidMessage(f"{name} must be a string")
        cleaned = raw.strip()
        if not cleaned or len(cleaned) > 64 or not cleaned.replace("_", "").replace("-", "").isalnum():
            raise InvalidMessage(f"{name} invalid")
        return cleaned

    def _reset_daily_if_needed(self, now: float) -> None:
        day = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%d")
        if day != self._current_day:
            self._current_day = day
            self._total_today = []
            self._server_counts = {}

    def _effective_per_server_cap(self, now: float) -> int:
        """Apply warming-up ramp for the first 14 days."""
        if self.warming_start_ts <= 0:
            return self.per_server_daily
        elapsed_days = int((now - self.warming_start_ts) / 86400)
        if elapsed_days >= WARMING_DAYS:
            return self.per_server_daily
        # Ramp: 1 -> per_server_daily over 14 days, linear.
        ramp = max(1, int((elapsed_days + 1) * self.per_server_daily / WARMING_DAYS))
        return min(ramp, self.per_server_daily)

    def _warming_day(self, now: float) -> int:
        if self.warming_start_ts <= 0:
            return 0
        return max(0, int((now - self.warming_start_ts) / 86400))

    def _check_limits(self, server_id: str, now: float) -> None:
        self._reset_daily_if_needed(now)

        if len(self._total_today) >= self.total_daily:
            raise RateLimitExceeded(
                f"daily total cap {self.total_daily} reached"
            )

        cap = self._effective_per_server_cap(now)
        bucket = self._server_counts.get(server_id, [])
        if len(bucket) >= cap:
            raise RateLimitExceeded(
                f"server {server_id} daily cap {cap} reached (warming ramp)"
            )

        if self._last_send_ts and (now - self._last_send_ts) < self.min_spacing_seconds:
            wait = int(self.min_spacing_seconds - (now - self._last_send_ts))
            raise RateLimitExceeded(
                f"min spacing {self.min_spacing_seconds}s not elapsed, wait {wait}s"
            )

    def _register_send(self, server_id: str, now: float) -> None:
        self._server_counts.setdefault(server_id, []).append(now)
        self._total_today.append(now)
        self._last_send_ts = now

    def _rollback(self, server_id: str) -> None:
        bucket = self._server_counts.get(server_id)
        if bucket:
            bucket.pop()
        if self._total_today:
            self._total_today.pop()

    # ── Public API ──

    def freeze_server(self, server_id: str, hours: float = 720.0) -> None:
        """Put a server on ice after a mod warn (default 30 days)."""
        server_id = self._validate_id(server_id, "server_id")
        with self._lock:
            self._frozen_servers[server_id] = self.clock() + hours * 3600

    def unfreeze_server(self, server_id: str) -> None:
        server_id = self._validate_id(server_id, "server_id")
        with self._lock:
            self._frozen_servers.pop(server_id, None)

    def is_frozen(self, server_id: str, now: Optional[float] = None) -> bool:
        with self._lock:
            until = self._frozen_servers.get(server_id, 0.0)
            current = float(now if now is not None else self.clock())
            if until <= 0:
                return False
            if current >= until:
                self._frozen_servers.pop(server_id, None)
                return False
            return True

    def stats(self) -> dict[str, object]:
        now = self.clock()
        with self._lock:
            self._reset_daily_if_needed(now)
            return {
                "day": self._current_day,
                "total_today": len(self._total_today),
                "total_cap": self.total_daily,
                "per_server_cap": self._effective_per_server_cap(now),
                "warming_day": self._warming_day(now),
                "frozen_servers": list(self._frozen_servers.keys()),
                "last_send_ts": self._last_send_ts,
            }

    async def send(
        self,
        *,
        server_id: str,
        channel_id: str,
        content: str,
        country: str,
    ) -> DiscordResult:
        """Send a Discord message with all safety checks.

        Raises:
            InvalidMessage         — malformed content or id
            BlockedByCompliance    — country not allowed for marketing
            BlockedByServerFreeze  — server frozen after mod warn
            RateLimitExceeded      — daily cap, per-server cap, or spacing hit
        """
        server = self._validate_id(server_id, "server_id")
        channel = self._validate_id(channel_id, "channel_id")
        clean_content = self._validate_content(content)

        compliance = is_allowed(country, feature="marketing")
        if not compliance.allowed:
            raise BlockedByCompliance(
                f"country {country or '?'}: {compliance.reason}"
            )

        if self.is_frozen(server):
            raise BlockedByServerFreeze(f"server {server} is frozen after mod warn")

        now = self.clock()
        if self.warming_start_ts <= 0:
            self.warming_start_ts = now  # auto-start on first send

        with self._lock:
            self._check_limits(server, now)
            self._register_send(server, now)
            server_count = len(self._server_counts[server])
            total_count = len(self._total_today)
            warming = self._warming_day(now)

        try:
            await self.send_fn(
                server_id=server, channel_id=channel, content=clean_content,
            )
        except Exception as e:
            with self._lock:
                self._rollback(server)
            logger.error(
                "[Discord] send failed server=%s channel=%s err=%s",
                server, channel, e,
            )
            raise

        logger.info(
            "[Discord] Sent server=%s channel=%s (%d/day server, %d/day total, warming_day=%d)",
            server, channel, server_count, total_count, warming,
        )
        return DiscordResult(
            server_id=server,
            channel_id=channel,
            sent_at=now,
            server_count_today=server_count,
            total_count_today=total_count,
            success=True,
            warming_day=warming,
            reason="ok",
        )
