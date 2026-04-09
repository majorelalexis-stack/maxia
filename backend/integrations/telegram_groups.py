"""MAXIA Telegram bot — group mode dispatcher (P6 Plan CEO V7).

Separates allowed commands/behavior between:
    - private chats (DM): full feature set including /portfolio, /alerts
    - group / supergroup: read-only commands only (/price, /swap_quote, /gpu)
    - channel: ignored

Rate limits per (chat_type, chat_id):
    - group:       10 msg/hour (anti-spam)
    - supergroup:  10 msg/hour
    - private:     300 msg/hour (user is intentionally chatting with the bot)

Welcome message: sent once when the bot is added to a group via
``my_chat_member`` update. Multilingue via ``telegram_i18n``.

This module is framework-free: only pure functions + dataclass state.
The bot dispatcher (``telegram_bot.py``) can opt into these checks by
calling ``classify_chat``, ``is_command_allowed``, ``check_group_rate``,
and ``build_group_welcome``.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Final, Literal

from integrations.telegram_i18n import detect_lang, t

logger = logging.getLogger("maxia.telegram_groups")

ChatType = Literal["private", "group", "supergroup", "channel", "unknown"]

# ── Rate limits per hour, per chat ──
GROUP_RATE_LIMIT: Final[int] = 10
SUPERGROUP_RATE_LIMIT: Final[int] = 10
PRIVATE_RATE_LIMIT: Final[int] = 300
RATE_WINDOW_SECONDS: Final[int] = 3600

# ── Command whitelists ──
# Commands allowed in DM (private) — the full set.
PRIVATE_COMMANDS: Final[frozenset[str]] = frozenset({
    "/start", "/help", "/price", "/swap_quote", "/gpu",
    "/portfolio", "/alerts", "/wallet", "/signal",
})

# Commands allowed in groups / supergroups — strict read-only subset.
GROUP_COMMANDS: Final[frozenset[str]] = frozenset({
    "/start", "/help", "/price", "/swap_quote", "/gpu", "/signal",
})


def classify_chat(chat: object) -> ChatType:
    """Normalize the Telegram chat object to one of our canonical types."""
    if not isinstance(chat, dict):
        return "unknown"
    raw = str(chat.get("type", "")).strip().lower()
    if raw in ("private", "group", "supergroup", "channel"):
        return raw  # type: ignore[return-value]
    return "unknown"


def normalize_command(text: object) -> str:
    """Return the lowercase ``/command`` token from a message text.

    Handles both ``/cmd`` and ``/cmd@bot_name`` (Telegram adds the bot
    username when the command is used in a group). Returns empty string
    if the text does not start with a slash command.
    """
    if not isinstance(text, str):
        return ""
    stripped = text.lstrip()
    if not stripped.startswith("/"):
        return ""
    first = stripped.split(None, 1)[0]  # "/cmd@bot" or "/cmd"
    cmd = first.split("@", 1)[0].lower()
    return cmd


def is_command_allowed(command: str, chat_type: ChatType) -> bool:
    """Return True if the command is allowed in the given chat type."""
    if not command.startswith("/"):
        return False
    if chat_type == "private":
        return command in PRIVATE_COMMANDS
    if chat_type in ("group", "supergroup"):
        return command in GROUP_COMMANDS
    return False  # channel / unknown: silent ignore


# ── Rate limiter ──


@dataclass
class GroupRateLimiter:
    """Thread-safe hourly rate limiter keyed by (chat_type, chat_id).

    Uses a sliding window: each chat keeps a list of send timestamps,
    and entries older than ``RATE_WINDOW_SECONDS`` are pruned on every
    check. The list stays tiny because limits are small (<=300).
    """

    _buckets: dict[tuple[str, int], list[float]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @staticmethod
    def _limit_for(chat_type: ChatType) -> int:
        if chat_type == "private":
            return PRIVATE_RATE_LIMIT
        if chat_type == "supergroup":
            return SUPERGROUP_RATE_LIMIT
        if chat_type == "group":
            return GROUP_RATE_LIMIT
        return 0  # channel / unknown: never allowed

    def allow(
        self,
        chat_type: ChatType,
        chat_id: int,
        now: float | None = None,
    ) -> bool:
        """Return True if the chat is under its hourly limit, False otherwise.

        When ``True``, the current timestamp is recorded (reservation).
        """
        if not isinstance(chat_id, int):
            return False
        limit = self._limit_for(chat_type)
        if limit <= 0:
            return False

        current = float(now) if now is not None else time.time()
        cutoff = current - RATE_WINDOW_SECONDS
        key = (chat_type, chat_id)

        with self._lock:
            bucket = self._buckets.get(key, [])
            bucket = [ts for ts in bucket if ts > cutoff]
            if len(bucket) >= limit:
                self._buckets[key] = bucket
                return False
            bucket.append(current)
            self._buckets[key] = bucket
            return True

    def usage(
        self, chat_type: ChatType, chat_id: int, now: float | None = None,
    ) -> tuple[int, int]:
        """Return ``(used, limit)`` for the chat — read-only, does not reserve."""
        limit = self._limit_for(chat_type)
        current = float(now) if now is not None else time.time()
        cutoff = current - RATE_WINDOW_SECONDS
        key = (chat_type, chat_id)
        with self._lock:
            bucket = self._buckets.get(key, [])
            fresh = [ts for ts in bucket if ts > cutoff]
            self._buckets[key] = fresh
            return len(fresh), limit

    def reset(self, chat_type: ChatType | None = None, chat_id: int | None = None) -> None:
        """Clear limiter state — useful for tests or admin kill-switch."""
        with self._lock:
            if chat_type is None and chat_id is None:
                self._buckets.clear()
                return
            self._buckets = {
                k: v for k, v in self._buckets.items()
                if not (k[0] == chat_type and (chat_id is None or k[1] == chat_id))
            }


# ── Welcome message builder ──


def build_group_welcome(
    lang: object,
    bot_username: str,
) -> str:
    """Build a short welcome message when the bot is added to a group.

    Multilingue via the existing ``telegram_i18n`` helpers.
    """
    normalized_lang = detect_lang(lang)
    subtitle = t("welcome.subtitle", normalized_lang)
    price_cmd = t("welcome.cmd_price", normalized_lang)
    help_cmd = t("welcome.cmd_help", normalized_lang)
    safe_bot = str(bot_username or "MAXIA_AI_bot").lstrip("@")[:32]
    return (
        f"<b>MAXIA</b> — @{safe_bot}\n\n"
        f"{subtitle}\n\n"
        f"  {price_cmd}\n"
        f"  {help_cmd}"
    )


# ── Convenience dispatcher ──


@dataclass(frozen=True)
class DispatchDecision:
    """Result of deciding how to handle a group message."""
    chat_type: ChatType
    command: str
    allowed: bool
    rate_ok: bool
    reason: str = ""

    @property
    def should_respond(self) -> bool:
        return self.allowed and self.rate_ok


def decide_group_message(
    message: object,
    limiter: GroupRateLimiter,
    now: float | None = None,
) -> DispatchDecision:
    """Decide whether a Telegram message should be handled by the bot.

    Returns a ``DispatchDecision`` with all the context the caller needs
    to either respond, silently ignore, or reply with a rate-limit notice.
    """
    if not isinstance(message, dict):
        return DispatchDecision(
            chat_type="unknown", command="", allowed=False, rate_ok=False,
            reason="message is not a dict",
        )

    chat = message.get("chat", {})
    chat_type = classify_chat(chat)
    chat_id = chat.get("id") if isinstance(chat, dict) else None
    command = normalize_command(message.get("text", ""))

    # Non-command in non-private chat: silent ignore (do not spam groups).
    if not command:
        if chat_type == "private":
            return DispatchDecision(
                chat_type=chat_type, command="", allowed=True, rate_ok=True,
                reason="free-form chat in DM",
            )
        return DispatchDecision(
            chat_type=chat_type, command="", allowed=False, rate_ok=False,
            reason="non-command message in group — ignored",
        )

    if not is_command_allowed(command, chat_type):
        return DispatchDecision(
            chat_type=chat_type, command=command, allowed=False, rate_ok=False,
            reason=f"{command} not allowed in {chat_type}",
        )

    if not isinstance(chat_id, int):
        return DispatchDecision(
            chat_type=chat_type, command=command, allowed=False, rate_ok=False,
            reason="missing chat_id",
        )

    rate_ok = limiter.allow(chat_type, chat_id, now=now)
    return DispatchDecision(
        chat_type=chat_type, command=command,
        allowed=True, rate_ok=rate_ok,
        reason="ok" if rate_ok else "rate limited",
    )
