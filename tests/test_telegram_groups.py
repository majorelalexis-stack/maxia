"""Tests for MAXIA Telegram group mode (P6 — Plan CEO V7)."""
from __future__ import annotations

import os
import sys

import pytest

BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..", "backend")
sys.path.insert(0, BACKEND_DIR)

from integrations.telegram_groups import (  # noqa: E402
    GROUP_COMMANDS,
    GROUP_RATE_LIMIT,
    PRIVATE_COMMANDS,
    PRIVATE_RATE_LIMIT,
    RATE_WINDOW_SECONDS,
    SUPERGROUP_RATE_LIMIT,
    DispatchDecision,
    GroupRateLimiter,
    build_group_welcome,
    classify_chat,
    decide_group_message,
    is_command_allowed,
    normalize_command,
)


# ═══════════════════════════════════════════════════════════════════════════
#  classify_chat
# ═══════════════════════════════════════════════════════════════════════════


class TestClassifyChat:
    def test_private(self):
        assert classify_chat({"type": "private"}) == "private"

    def test_group(self):
        assert classify_chat({"type": "group"}) == "group"

    def test_supergroup(self):
        assert classify_chat({"type": "supergroup"}) == "supergroup"

    def test_channel(self):
        assert classify_chat({"type": "channel"}) == "channel"

    def test_unknown_type_returns_unknown(self):
        assert classify_chat({"type": "bot"}) == "unknown"

    def test_missing_type(self):
        assert classify_chat({}) == "unknown"

    def test_none(self):
        assert classify_chat(None) == "unknown"

    def test_non_dict(self):
        assert classify_chat("private") == "unknown"
        assert classify_chat(42) == "unknown"


# ═══════════════════════════════════════════════════════════════════════════
#  normalize_command
# ═══════════════════════════════════════════════════════════════════════════


class TestNormalizeCommand:
    def test_simple(self):
        assert normalize_command("/price") == "/price"

    def test_with_args(self):
        assert normalize_command("/price BTC") == "/price"

    def test_with_bot_name(self):
        assert normalize_command("/price@MAXIA_AI_bot") == "/price"
        assert normalize_command("/price@MAXIA_AI_bot BTC") == "/price"

    def test_case_insensitive(self):
        assert normalize_command("/PRICE") == "/price"
        assert normalize_command("/Price BTC") == "/price"

    def test_leading_whitespace(self):
        assert normalize_command("  /help  ") == "/help"

    def test_not_a_command(self):
        assert normalize_command("hello") == ""
        assert normalize_command("") == ""

    def test_none(self):
        assert normalize_command(None) == ""
        assert normalize_command(42) == ""


# ═══════════════════════════════════════════════════════════════════════════
#  is_command_allowed
# ═══════════════════════════════════════════════════════════════════════════


class TestCommandAllowed:
    def test_private_allows_portfolio(self):
        assert is_command_allowed("/portfolio", "private") is True

    def test_private_allows_alerts(self):
        assert is_command_allowed("/alerts", "private") is True

    def test_group_blocks_portfolio(self):
        assert is_command_allowed("/portfolio", "group") is False

    def test_group_blocks_alerts(self):
        assert is_command_allowed("/alerts", "group") is False

    def test_group_allows_price(self):
        assert is_command_allowed("/price", "group") is True

    def test_supergroup_allows_price(self):
        assert is_command_allowed("/price", "supergroup") is True

    def test_channel_rejects_all(self):
        for cmd in PRIVATE_COMMANDS:
            assert is_command_allowed(cmd, "channel") is False

    def test_unknown_chat_rejects_all(self):
        assert is_command_allowed("/price", "unknown") is False

    def test_non_command_rejected(self):
        assert is_command_allowed("hello", "private") is False

    def test_group_commands_subset_of_private(self):
        assert GROUP_COMMANDS <= PRIVATE_COMMANDS

    def test_group_commands_read_only(self):
        # Sanity: no state-mutating commands in group mode
        for blocked in ("/portfolio", "/alerts", "/wallet"):
            assert blocked not in GROUP_COMMANDS


# ═══════════════════════════════════════════════════════════════════════════
#  GroupRateLimiter
# ═══════════════════════════════════════════════════════════════════════════


class TestRateLimiter:
    def test_group_under_limit(self):
        rl = GroupRateLimiter()
        for _ in range(GROUP_RATE_LIMIT):
            assert rl.allow("group", 100, now=1000) is True

    def test_group_over_limit(self):
        rl = GroupRateLimiter()
        for _ in range(GROUP_RATE_LIMIT):
            rl.allow("group", 100, now=1000)
        assert rl.allow("group", 100, now=1001) is False

    def test_private_higher_limit(self):
        rl = GroupRateLimiter()
        for i in range(PRIVATE_RATE_LIMIT):
            assert rl.allow("private", 200, now=1000 + i) is True
        assert rl.allow("private", 200, now=1000 + PRIVATE_RATE_LIMIT) is False

    def test_different_chats_independent(self):
        rl = GroupRateLimiter()
        for _ in range(GROUP_RATE_LIMIT):
            rl.allow("group", 100, now=1000)
        assert rl.allow("group", 101, now=1000) is True

    def test_private_and_group_same_id_independent(self):
        rl = GroupRateLimiter()
        for _ in range(GROUP_RATE_LIMIT):
            rl.allow("group", 999, now=1000)
        # Same id but private chat -> separate bucket
        assert rl.allow("private", 999, now=1000) is True

    def test_window_sliding(self):
        rl = GroupRateLimiter()
        for _ in range(GROUP_RATE_LIMIT):
            rl.allow("group", 100, now=1000)
        assert rl.allow("group", 100, now=1000) is False
        # Jump 1 hour + 1 second — old entries pruned
        assert rl.allow("group", 100, now=1000 + RATE_WINDOW_SECONDS + 1) is True

    def test_channel_always_blocked(self):
        rl = GroupRateLimiter()
        assert rl.allow("channel", 1, now=1000) is False

    def test_unknown_blocked(self):
        rl = GroupRateLimiter()
        assert rl.allow("unknown", 1, now=1000) is False  # type: ignore[arg-type]

    def test_invalid_chat_id(self):
        rl = GroupRateLimiter()
        assert rl.allow("group", "not-int", now=1000) is False  # type: ignore[arg-type]

    def test_usage_read_only(self):
        rl = GroupRateLimiter()
        rl.allow("group", 100, now=1000)
        rl.allow("group", 100, now=1000)
        used, limit = rl.usage("group", 100, now=1001)
        assert used == 2
        assert limit == GROUP_RATE_LIMIT
        # Calling usage does not reserve a slot
        used2, _ = rl.usage("group", 100, now=1001)
        assert used2 == 2

    def test_reset_all(self):
        rl = GroupRateLimiter()
        rl.allow("group", 100, now=1000)
        rl.allow("private", 200, now=1000)
        rl.reset()
        used, _ = rl.usage("group", 100, now=1001)
        assert used == 0

    def test_reset_specific(self):
        rl = GroupRateLimiter()
        rl.allow("group", 100, now=1000)
        rl.allow("group", 200, now=1000)
        rl.reset(chat_type="group", chat_id=100)
        used100, _ = rl.usage("group", 100, now=1001)
        used200, _ = rl.usage("group", 200, now=1001)
        assert used100 == 0
        assert used200 == 1


# ═══════════════════════════════════════════════════════════════════════════
#  build_group_welcome
# ═══════════════════════════════════════════════════════════════════════════


class TestWelcome:
    def test_english(self):
        msg = build_group_welcome("en", "MAXIA_AI_bot")
        assert "MAXIA" in msg
        assert "@MAXIA_AI_bot" in msg
        assert "/price" in msg
        assert "/help" in msg

    def test_french(self):
        msg = build_group_welcome("fr", "MAXIA_AI_bot")
        assert "MAXIA" in msg

    def test_japanese(self):
        msg = build_group_welcome("ja", "MAXIA_AI_bot")
        assert "MAXIA" in msg

    def test_no_at_prefix_in_username(self):
        msg = build_group_welcome("en", "@MAXIA_AI_bot")
        # Should not produce @@MAXIA
        assert "@@" not in msg

    def test_empty_username_default(self):
        msg = build_group_welcome("en", "")
        assert "@MAXIA_AI_bot" in msg


# ═══════════════════════════════════════════════════════════════════════════
#  decide_group_message
# ═══════════════════════════════════════════════════════════════════════════


class TestDecide:
    def test_private_command_allowed(self):
        rl = GroupRateLimiter()
        msg = {"chat": {"id": 1, "type": "private"}, "text": "/portfolio"}
        decision = decide_group_message(msg, rl, now=1000)
        assert decision.should_respond
        assert decision.chat_type == "private"

    def test_private_free_text_allowed(self):
        rl = GroupRateLimiter()
        msg = {"chat": {"id": 1, "type": "private"}, "text": "hello"}
        decision = decide_group_message(msg, rl, now=1000)
        assert decision.should_respond
        assert decision.command == ""

    def test_group_price_allowed(self):
        rl = GroupRateLimiter()
        msg = {"chat": {"id": 1, "type": "group"}, "text": "/price BTC"}
        decision = decide_group_message(msg, rl, now=1000)
        assert decision.should_respond
        assert decision.command == "/price"

    def test_group_portfolio_blocked(self):
        rl = GroupRateLimiter()
        msg = {"chat": {"id": 1, "type": "group"}, "text": "/portfolio"}
        decision = decide_group_message(msg, rl, now=1000)
        assert not decision.should_respond
        assert decision.allowed is False

    def test_group_free_text_silent_ignore(self):
        rl = GroupRateLimiter()
        msg = {"chat": {"id": 1, "type": "group"}, "text": "random chatter"}
        decision = decide_group_message(msg, rl, now=1000)
        assert not decision.should_respond
        assert "ignored" in decision.reason

    def test_group_with_bot_mention_normalized(self):
        rl = GroupRateLimiter()
        msg = {"chat": {"id": 1, "type": "group"}, "text": "/price@MAXIA_AI_bot BTC"}
        decision = decide_group_message(msg, rl, now=1000)
        assert decision.should_respond
        assert decision.command == "/price"

    def test_rate_limit_hits(self):
        rl = GroupRateLimiter()
        msg = {"chat": {"id": 50, "type": "group"}, "text": "/price BTC"}
        for _ in range(GROUP_RATE_LIMIT):
            decide_group_message(msg, rl, now=1000)
        decision = decide_group_message(msg, rl, now=1000)
        assert not decision.should_respond
        assert "rate" in decision.reason.lower()

    def test_channel_ignored(self):
        rl = GroupRateLimiter()
        msg = {"chat": {"id": 1, "type": "channel"}, "text": "/price BTC"}
        decision = decide_group_message(msg, rl, now=1000)
        assert not decision.should_respond

    def test_non_dict_message(self):
        rl = GroupRateLimiter()
        decision = decide_group_message("not a dict", rl)
        assert not decision.should_respond
        assert decision.chat_type == "unknown"

    def test_missing_chat_id(self):
        rl = GroupRateLimiter()
        msg = {"chat": {"type": "group"}, "text": "/price"}
        decision = decide_group_message(msg, rl, now=1000)
        assert not decision.should_respond
        assert "chat_id" in decision.reason

    def test_decision_is_frozen(self):
        d = DispatchDecision(
            chat_type="private", command="/price", allowed=True, rate_ok=True,
        )
        with pytest.raises(Exception):
            d.allowed = False  # type: ignore[misc]
