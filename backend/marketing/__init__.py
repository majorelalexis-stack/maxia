"""MAXIA marketing layer — outreach, templates, consent (Plan CEO V7)."""
from marketing.email_outreach import (
    EmailOutreach,
    OutreachResult,
    RateLimitExceeded as EmailRateLimitExceeded,
    BlockedByConsent,
    BlockedByCompliance as EmailBlockedByCompliance,
)
from marketing.email_outreach import (
    RateLimitExceeded,
    BlockedByCompliance,
)
from marketing.email_templates import (
    render_outreach_email,
    EMAIL_TEMPLATE_LANGS,
)
from marketing.discord_outreach import (
    DiscordOutreach,
    DiscordResult,
    BlockedByServerFreeze,
)

__all__ = [
    "EmailOutreach",
    "OutreachResult",
    "RateLimitExceeded",
    "BlockedByConsent",
    "BlockedByCompliance",
    "render_outreach_email",
    "EMAIL_TEMPLATE_LANGS",
    "DiscordOutreach",
    "DiscordResult",
    "BlockedByServerFreeze",
]
