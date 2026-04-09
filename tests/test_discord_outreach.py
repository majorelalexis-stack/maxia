"""Tests for MAXIA Discord outreach engine (P8 — Plan CEO V7)."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

import pytest

BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..", "backend")
sys.path.insert(0, BACKEND_DIR)

from marketing.discord_outreach import (  # noqa: E402
    BlockedByCompliance,
    BlockedByServerFreeze,
    DiscordOutreach,
    DiscordResult,
    InvalidMessage,
    MIN_SPACING_SECONDS,
    PER_SERVER_DAILY,
    RateLimitExceeded,
    TOTAL_DAILY,
    WARMING_DAYS,
)


# ═══════════════════════════════════════════════════════════════════════════
#  Fakes
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class FakeClock:
    # Start at a known UTC midnight so warming math is predictable
    now: float = 1_700_006_400.0  # 2023-11-15 00:00:00 UTC

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@dataclass
class FakeDiscord:
    calls: list[dict] = field(default_factory=list)
    raise_on_send: bool = False

    async def __call__(self, *, server_id, channel_id, content):
        if self.raise_on_send:
            raise RuntimeError("Discord API down")
        self.calls.append({
            "server_id": server_id,
            "channel_id": channel_id,
            "content": content,
        })


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def sender() -> FakeDiscord:
    return FakeDiscord()


@pytest.fixture
def engine(clock, sender) -> DiscordOutreach:
    # Override warming_start_ts so per_server cap is full by default
    return DiscordOutreach(
        send_fn=sender, clock=clock,
        warming_start_ts=clock.now - (WARMING_DAYS + 1) * 86400,
    )


# ═══════════════════════════════════════════════════════════════════════════
#  Happy path
# ═══════════════════════════════════════════════════════════════════════════


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_simple_send(self, engine, sender):
        result = await engine.send(
            server_id="solana_sea", channel_id="general",
            content="Hello crypto SG", country="SG",
        )
        assert isinstance(result, DiscordResult)
        assert result.success
        assert result.total_count_today == 1
        assert len(sender.calls) == 1
        assert sender.calls[0]["content"] == "Hello crypto SG"

    @pytest.mark.asyncio
    async def test_stats(self, engine):
        await engine.send(
            server_id="s1", channel_id="c1",
            content="Hi", country="BR",
        )
        stats = engine.stats()
        assert stats["total_today"] == 1


# ═══════════════════════════════════════════════════════════════════════════
#  Rate limits
# ═══════════════════════════════════════════════════════════════════════════


class TestRateLimits:
    @pytest.mark.asyncio
    async def test_spacing_blocks_fast_second_send(self, engine):
        await engine.send(server_id="s1", channel_id="c1", content="a", country="SG")
        with pytest.raises(RateLimitExceeded, match="spacing"):
            await engine.send(server_id="s2", channel_id="c2", content="b", country="SG")

    @pytest.mark.asyncio
    async def test_spacing_elapsed_allows(self, engine, clock):
        await engine.send(server_id="s1", channel_id="c1", content="a", country="SG")
        clock.advance(MIN_SPACING_SECONDS + 1)
        await engine.send(server_id="s2", channel_id="c2", content="b", country="SG")

    @pytest.mark.asyncio
    async def test_per_server_cap(self, engine, clock):
        for _ in range(PER_SERVER_DAILY):
            await engine.send(
                server_id="ss", channel_id="c", content="hi", country="SG",
            )
            clock.advance(MIN_SPACING_SECONDS + 1)
        with pytest.raises(RateLimitExceeded, match="server"):
            await engine.send(
                server_id="ss", channel_id="c", content="hi", country="SG",
            )

    @pytest.mark.asyncio
    async def test_total_cap_across_servers(self, engine, clock):
        # 3 servers x 10 = 30 = TOTAL_DAILY
        for server_idx in range(3):
            for _ in range(PER_SERVER_DAILY):
                await engine.send(
                    server_id=f"s{server_idx}", channel_id="c",
                    content="hi", country="SG",
                )
                clock.advance(MIN_SPACING_SECONDS + 1)
        assert engine.stats()["total_today"] == TOTAL_DAILY
        # 31st must fail
        with pytest.raises(RateLimitExceeded, match="total"):
            await engine.send(
                server_id="s_extra", channel_id="c",
                content="hi", country="SG",
            )


# ═══════════════════════════════════════════════════════════════════════════
#  Warming ramp
# ═══════════════════════════════════════════════════════════════════════════


class TestWarming:
    @pytest.mark.asyncio
    async def test_day_0_cap_is_1(self, clock, sender):
        # warming_start_ts = now -> day 0
        engine = DiscordOutreach(
            send_fn=sender, clock=clock, warming_start_ts=clock.now,
        )
        # 1 send allowed, 2nd should hit per_server cap (even without spacing issue)
        await engine.send(
            server_id="s1", channel_id="c1", content="hi", country="SG",
        )
        # After spacing elapses
        clock.advance(MIN_SPACING_SECONDS + 1)
        with pytest.raises(RateLimitExceeded, match="server"):
            await engine.send(
                server_id="s1", channel_id="c1", content="hi", country="SG",
            )

    @pytest.mark.asyncio
    async def test_day_14_full_cap(self, clock, sender):
        engine = DiscordOutreach(
            send_fn=sender, clock=clock,
            warming_start_ts=clock.now - WARMING_DAYS * 86400,
        )
        # Full cap of 10
        for _ in range(PER_SERVER_DAILY):
            await engine.send(
                server_id="s1", channel_id="c1", content="hi", country="SG",
            )
            clock.advance(MIN_SPACING_SECONDS + 1)


# ═══════════════════════════════════════════════════════════════════════════
#  Compliance
# ═══════════════════════════════════════════════════════════════════════════


class TestCompliance:
    @pytest.mark.asyncio
    async def test_cn_blocked(self, engine):
        with pytest.raises(BlockedByCompliance):
            await engine.send(
                server_id="s1", channel_id="c1",
                content="hi", country="CN",
            )

    @pytest.mark.asyncio
    async def test_in_geo_blocked(self, engine):
        with pytest.raises(BlockedByCompliance):
            await engine.send(
                server_id="s1", channel_id="c1",
                content="hi", country="IN",
            )

    @pytest.mark.asyncio
    async def test_us_blocked(self, engine):
        with pytest.raises(BlockedByCompliance):
            await engine.send(
                server_id="s1", channel_id="c1",
                content="hi", country="US",
            )


# ═══════════════════════════════════════════════════════════════════════════
#  Server freeze
# ═══════════════════════════════════════════════════════════════════════════


class TestFreeze:
    @pytest.mark.asyncio
    async def test_frozen_blocks(self, engine):
        engine.freeze_server("bad_server", hours=24)
        with pytest.raises(BlockedByServerFreeze):
            await engine.send(
                server_id="bad_server", channel_id="c",
                content="hi", country="SG",
            )

    @pytest.mark.asyncio
    async def test_unfreeze(self, engine):
        engine.freeze_server("s1", hours=1)
        engine.unfreeze_server("s1")
        result = await engine.send(
            server_id="s1", channel_id="c",
            content="hi", country="SG",
        )
        assert result.success

    @pytest.mark.asyncio
    async def test_freeze_expires(self, clock, engine):
        engine.freeze_server("s1", hours=1)
        clock.advance(3601)
        result = await engine.send(
            server_id="s1", channel_id="c",
            content="hi", country="SG",
        )
        assert result.success

    def test_is_frozen_check(self, clock, engine):
        assert not engine.is_frozen("s1")
        engine.freeze_server("s1", hours=1)
        assert engine.is_frozen("s1")


# ═══════════════════════════════════════════════════════════════════════════
#  Validation
# ═══════════════════════════════════════════════════════════════════════════


class TestValidation:
    @pytest.mark.asyncio
    async def test_mass_mention_rejected(self, engine):
        with pytest.raises(InvalidMessage, match="mass mentions"):
            await engine.send(
                server_id="s1", channel_id="c1",
                content="Hey @everyone check this out", country="SG",
            )

    @pytest.mark.asyncio
    async def test_here_mention_rejected(self, engine):
        with pytest.raises(InvalidMessage):
            await engine.send(
                server_id="s1", channel_id="c1",
                content="Hey @here", country="SG",
            )

    @pytest.mark.asyncio
    async def test_empty_content(self, engine):
        with pytest.raises(InvalidMessage):
            await engine.send(
                server_id="s1", channel_id="c1",
                content="  ", country="SG",
            )

    @pytest.mark.asyncio
    async def test_too_long(self, engine):
        with pytest.raises(InvalidMessage):
            await engine.send(
                server_id="s1", channel_id="c1",
                content="x" * 2000, country="SG",
            )

    @pytest.mark.asyncio
    async def test_control_chars(self, engine):
        with pytest.raises(InvalidMessage):
            await engine.send(
                server_id="s1", channel_id="c1",
                content="hi\u202ereverse", country="SG",
            )

    @pytest.mark.asyncio
    async def test_invalid_server_id(self, engine):
        with pytest.raises(InvalidMessage):
            await engine.send(
                server_id="bad id!", channel_id="c1",
                content="hi", country="SG",
            )


# ═══════════════════════════════════════════════════════════════════════════
#  Rollback on send failure
# ═══════════════════════════════════════════════════════════════════════════


class TestRollback:
    @pytest.mark.asyncio
    async def test_send_failure_rollsback(self, clock):
        sender = FakeDiscord(raise_on_send=True)
        engine = DiscordOutreach(
            send_fn=sender, clock=clock,
            warming_start_ts=clock.now - (WARMING_DAYS + 1) * 86400,
        )
        with pytest.raises(RuntimeError, match="Discord"):
            await engine.send(
                server_id="s1", channel_id="c1",
                content="hi", country="SG",
            )
        stats = engine.stats()
        assert stats["total_today"] == 0
