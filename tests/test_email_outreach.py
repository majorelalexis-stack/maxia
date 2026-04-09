"""Tests for MAXIA outreach email engine (P3 — Plan CEO V7).

Covers:
- 13 language templates rendering
- Rate limit (30/day) + spacing (30 min)
- RGPD consent (unsubscribe, bounce)
- Compliance country filter integration
- Injection / header validation
- Rollback on SMTP failure
- Daily reset on day change
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from unittest.mock import MagicMock

import pytest

BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..", "backend")
sys.path.insert(0, BACKEND_DIR)

from marketing import (  # noqa: E402
    BlockedByCompliance,
    BlockedByConsent,
    EmailOutreach,
    OutreachResult,
    RateLimitExceeded,
    render_outreach_email,
    EMAIL_TEMPLATE_LANGS,
)
from marketing.email_outreach import (  # noqa: E402
    InMemoryConsentStore,
    InvalidEmail,
    DAILY_LIMIT,
    MIN_SPACING_SECONDS,
)


# ═══════════════════════════════════════════════════════════════════════════
#  Templates
# ═══════════════════════════════════════════════════════════════════════════


class TestEmailTemplates:
    def test_all_13_languages_render(self):
        for lang in EMAIL_TEMPLATE_LANGS:
            subject, text, html = render_outreach_email(
                lang=lang,
                name="Taro",
                cta_link="https://maxiaworld.app/demo",
                unsubscribe_link="https://maxiaworld.app/u/abc",
            )
            assert isinstance(subject, str) and len(subject) > 0
            assert "MAXIA" in subject or "MAXIA" in text or "MAXIA" in html
            assert "Taro" in text or "Taro" in html
            assert "https://maxiaworld.app/demo" in text
            assert "https://maxiaworld.app/u/abc" in text

    def test_count_is_13(self):
        assert len(EMAIL_TEMPLATE_LANGS) == 13

    def test_unknown_lang_falls_back_to_english(self):
        s1, t1, h1 = render_outreach_email(
            lang="klingon", name="X", cta_link="https://a", unsubscribe_link="https://b",
        )
        s2, t2, h2 = render_outreach_email(
            lang="en", name="X", cta_link="https://a", unsubscribe_link="https://b",
        )
        assert s1 == s2

    def test_alias_zh_cn_maps_to_zh_tw(self):
        s1, _, _ = render_outreach_email(
            lang="zh-CN", name="X", cta_link="https://a", unsubscribe_link="https://b",
        )
        s2, _, _ = render_outreach_email(
            lang="zh-tw", name="X", cta_link="https://a", unsubscribe_link="https://b",
        )
        assert s1 == s2

    def test_empty_name_defaults_to_there(self):
        _, text, _ = render_outreach_email(
            lang="en", name="", cta_link="https://a", unsubscribe_link="https://b",
        )
        assert "there" in text

    def test_unsubscribe_always_present(self):
        for lang in EMAIL_TEMPLATE_LANGS:
            _, text, html = render_outreach_email(
                lang=lang,
                name="Test",
                cta_link="https://maxiaworld.app",
                unsubscribe_link="https://maxiaworld.app/unsub/XYZ",
            )
            assert "https://maxiaworld.app/unsub/XYZ" in text
            assert "https://maxiaworld.app/unsub/XYZ" in html


# ═══════════════════════════════════════════════════════════════════════════
#  Engine fixtures + helpers
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class FakeClock:
    # Start at a known UTC midnight so 30×(30min+1s) = 15h stays inside the day
    now: float = 1_700_006_400.0  # 2023-11-15 00:00:00 UTC exactly

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@dataclass
class FakeSmtp:
    calls: list[dict] = field(default_factory=list)
    raise_on_send: bool = False

    def __call__(self, *, to, subject, body_text, body_html):
        if self.raise_on_send:
            raise RuntimeError("SMTP connection refused")
        self.calls.append({
            "to": to, "subject": subject,
            "body_text": body_text, "body_html": body_html,
        })


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def smtp() -> FakeSmtp:
    return FakeSmtp()


@pytest.fixture
def engine(clock: FakeClock, smtp: FakeSmtp) -> EmailOutreach:
    return EmailOutreach(
        smtp_send=smtp,
        consent=InMemoryConsentStore(),
        clock=clock,
    )


# ═══════════════════════════════════════════════════════════════════════════
#  Happy path + rate limits
# ═══════════════════════════════════════════════════════════════════════════


class TestEngineHappyPath:
    @pytest.mark.asyncio
    async def test_simple_send(self, engine, smtp):
        result = await engine.send(
            to="carlos@example.com",
            subject="MAXIA",
            body_text="hi",
            body_html="<p>hi</p>",
            lang="pt-br",
            country="BR",
        )
        assert isinstance(result, OutreachResult)
        assert result.success is True
        assert result.to == "carlos@example.com"
        assert result.daily_count == 1
        assert len(smtp.calls) == 1

    @pytest.mark.asyncio
    async def test_stats_after_send(self, engine, clock):
        await engine.send(
            to="a@example.com", subject="s", body_text="t", body_html="h",
            lang="en", country="SG",
        )
        stats = engine.stats()
        assert stats["sent_today"] == 1
        assert stats["remaining"] == DAILY_LIMIT - 1


class TestRateLimits:
    @pytest.mark.asyncio
    async def test_spacing_blocks_fast_second_send(self, engine, clock):
        await engine.send(
            to="a@example.com", subject="s", body_text="t", body_html="h",
            lang="en", country="SG",
        )
        with pytest.raises(RateLimitExceeded, match="spacing"):
            await engine.send(
                to="b@example.com", subject="s", body_text="t", body_html="h",
                lang="en", country="SG",
            )

    @pytest.mark.asyncio
    async def test_spacing_elapsed_allows_second(self, engine, clock):
        await engine.send(
            to="a@example.com", subject="s", body_text="t", body_html="h",
            lang="en", country="SG",
        )
        clock.advance(MIN_SPACING_SECONDS + 1)
        result = await engine.send(
            to="b@example.com", subject="s", body_text="t", body_html="h",
            lang="en", country="SG",
        )
        assert result.daily_count == 2

    @pytest.mark.asyncio
    async def test_daily_cap_30(self, engine, clock):
        for i in range(DAILY_LIMIT):
            await engine.send(
                to=f"user{i}@example.com", subject="s", body_text="t",
                body_html="h", lang="en", country="SG",
            )
            clock.advance(MIN_SPACING_SECONDS + 1)
        # 31st call must fail
        with pytest.raises(RateLimitExceeded, match="daily limit"):
            await engine.send(
                to="extra@example.com", subject="s", body_text="t",
                body_html="h", lang="en", country="SG",
            )

    @pytest.mark.asyncio
    async def test_daily_reset_at_new_day(self, engine, clock):
        for i in range(DAILY_LIMIT):
            await engine.send(
                to=f"u{i}@example.com", subject="s", body_text="t",
                body_html="h", lang="en", country="SG",
            )
            clock.advance(MIN_SPACING_SECONDS + 1)
        # Jump 24h ahead
        clock.advance(86400)
        result = await engine.send(
            to="fresh@example.com", subject="s", body_text="t",
            body_html="h", lang="en", country="SG",
        )
        assert result.daily_count == 1  # reset


# ═══════════════════════════════════════════════════════════════════════════
#  Consent (RGPD / CAN-SPAM)
# ═══════════════════════════════════════════════════════════════════════════


class TestConsent:
    @pytest.mark.asyncio
    async def test_unsubscribed_blocked(self, engine):
        engine.mark_unsubscribed("gone@example.com")
        with pytest.raises(BlockedByConsent, match="unsubscribed"):
            await engine.send(
                to="gone@example.com", subject="s", body_text="t",
                body_html="h", lang="en", country="SG",
            )

    @pytest.mark.asyncio
    async def test_bounced_blocked(self, engine):
        engine.mark_bounced("bad@example.com")
        with pytest.raises(BlockedByConsent, match="bounced"):
            await engine.send(
                to="bad@example.com", subject="s", body_text="t",
                body_html="h", lang="en", country="SG",
            )

    @pytest.mark.asyncio
    async def test_unsubscribe_case_insensitive(self, engine):
        engine.mark_unsubscribed("Mike@Example.Com")
        with pytest.raises(BlockedByConsent):
            await engine.send(
                to="mike@example.com", subject="s", body_text="t",
                body_html="h", lang="en", country="SG",
            )


# ═══════════════════════════════════════════════════════════════════════════
#  Compliance integration
# ═══════════════════════════════════════════════════════════════════════════


class TestCompliance:
    @pytest.mark.asyncio
    async def test_blocked_country_cn_blocked(self, engine):
        with pytest.raises(BlockedByCompliance):
            await engine.send(
                to="a@example.com", subject="s", body_text="t",
                body_html="h", lang="zh-tw", country="CN",
            )

    @pytest.mark.asyncio
    async def test_geo_blocked_in_blocked_for_marketing(self, engine):
        with pytest.raises(BlockedByCompliance):
            await engine.send(
                to="a@example.com", subject="s", body_text="t",
                body_html="h", lang="hi", country="IN",
            )

    @pytest.mark.asyncio
    async def test_allowed_country_sg_ok(self, engine, smtp):
        result = await engine.send(
            to="a@example.com", subject="s", body_text="t",
            body_html="h", lang="en", country="SG",
        )
        assert result.success
        assert len(smtp.calls) == 1

    @pytest.mark.asyncio
    async def test_us_blocked(self, engine):
        with pytest.raises(BlockedByCompliance):
            await engine.send(
                to="a@example.com", subject="s", body_text="t",
                body_html="h", lang="en", country="US",
            )


# ═══════════════════════════════════════════════════════════════════════════
#  Input validation / injection
# ═══════════════════════════════════════════════════════════════════════════


class TestValidation:
    @pytest.mark.asyncio
    async def test_invalid_email(self, engine):
        with pytest.raises(InvalidEmail):
            await engine.send(
                to="not-an-email", subject="s", body_text="t",
                body_html="h", lang="en", country="SG",
            )

    @pytest.mark.asyncio
    async def test_email_with_newline_rejected(self, engine):
        with pytest.raises(InvalidEmail):
            await engine.send(
                to="a@example.com\nBcc: evil@e.com",
                subject="s", body_text="t", body_html="h",
                lang="en", country="SG",
            )

    @pytest.mark.asyncio
    async def test_subject_with_newline_rejected(self, engine):
        with pytest.raises(InvalidEmail):
            await engine.send(
                to="a@example.com",
                subject="s\r\nBcc: evil@e.com",
                body_text="t", body_html="h",
                lang="en", country="SG",
            )

    @pytest.mark.asyncio
    async def test_body_too_long_rejected(self, engine):
        with pytest.raises(InvalidEmail):
            await engine.send(
                to="a@example.com", subject="s",
                body_text="x" * 20_000, body_html="h",
                lang="en", country="SG",
            )

    @pytest.mark.asyncio
    async def test_empty_subject_rejected(self, engine):
        with pytest.raises(InvalidEmail):
            await engine.send(
                to="a@example.com", subject="",
                body_text="t", body_html="h",
                lang="en", country="SG",
            )


# ═══════════════════════════════════════════════════════════════════════════
#  Rollback on SMTP failure
# ═══════════════════════════════════════════════════════════════════════════


class TestRollback:
    @pytest.mark.asyncio
    async def test_smtp_failure_rollsback_quota(self, clock):
        smtp = FakeSmtp(raise_on_send=True)
        engine = EmailOutreach(smtp_send=smtp, clock=clock)

        with pytest.raises(RuntimeError, match="SMTP"):
            await engine.send(
                to="a@example.com", subject="s", body_text="t", body_html="h",
                lang="en", country="SG",
            )

        stats = engine.stats()
        # Failed send should NOT consume daily quota
        assert stats["sent_today"] == 0

    @pytest.mark.asyncio
    async def test_smtp_success_consumes_quota(self, engine, smtp):
        await engine.send(
            to="a@example.com", subject="s", body_text="t", body_html="h",
            lang="en", country="SG",
        )
        stats = engine.stats()
        assert stats["sent_today"] == 1
        assert len(smtp.calls) == 1
