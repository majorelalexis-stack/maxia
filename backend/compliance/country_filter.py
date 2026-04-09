"""MAXIA country filter — Plan CEO V7 compliance layer.

Three tiers:
    ALLOWED            — outreach + marketing + trading + payments OK
    GEO_BLOCKED        — outreach BLOCKED, marketing read-only OK (legal risk)
    BLOCKED            — all features BLOCKED (sanctions / illegal)

Sanctions sources:
    - OFAC SDN (US Treasury)
    - UN Security Council
    - EU consolidated list
    - Telegram ToS restrictions

Legal risk sources:
    - RBI + FIU-IND (India — VASP registration required)
    - FSC Korea (real-name banking for crypto)

Usage::

    from compliance import is_allowed

    result = is_allowed("SG", feature="trading")
    if not result.allowed:
        return {"error": result.reason, "code": result.code}
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

# 28 allowed countries — Plan CEO V7 outreach whitelist.
# India removed: RBI + FIU-IND require full VASP registration + KYC.
ALLOWED_COUNTRIES: Final[frozenset[str]] = frozenset({
    # Asie (11 — India removed)
    "SG", "HK", "KR", "TW", "TH", "VN", "MY", "ID", "PH", "AE", "IL",
    # Japon (1)
    "JP",
    # Oceanie (2)
    "AU", "NZ",
    # Afrique (8)
    "NG", "ZA", "KE", "EG", "GH", "MA", "TN", "SN",
    # Amerique du Sud (6)
    "BR", "AR", "MX", "CO", "CL", "PE",
})

# Geo-blocked: outreach forbidden, legal risk. Keep read-only access.
GEO_BLOCKED_COUNTRIES: Final[frozenset[str]] = frozenset({
    "IN",  # RBI + FIU-IND KYC obligation
})

# Hard-blocked: sanctions, illegal, or MAXIA ToS excluded.
BLOCKED_COUNTRIES: Final[frozenset[str]] = frozenset({
    # OFAC + UN + EU sanctions
    "CN",  # Chinese mainland — crypto forbidden
    "KP",  # North Korea
    "IR",  # Iran
    "SY",  # Syria
    "CU",  # Cuba
    "MM",  # Myanmar
    "AF",  # Afghanistan
    "RU",  # Russia (sanctions)
    "BY",  # Belarus (sanctions)
    # US excluded by MAXIA ToS (unregistered securities risk)
    "US",
})

# Feature gates — what each feature requires.
Feature = Literal[
    "discovery",    # read-only browsing, public pages
    "marketing",    # outreach, email, DMs, cold contact
    "payment",      # fiat-crypto rails, Stars, subscriptions
    "trading",      # swap, escrow, DeFi
    "withdrawal",   # fiat/crypto withdrawals
]

ALL_FEATURES: Final[frozenset[Feature]] = frozenset({
    "discovery", "marketing", "payment", "trading", "withdrawal",
})


@dataclass(frozen=True)
class ComplianceResult:
    """Immutable result of a compliance check."""
    allowed: bool
    country: str
    feature: str
    code: str                # OK | BLOCKED | GEO_BLOCKED | UNKNOWN
    reason: str


_OK_FEATURES_FOR_GEO_BLOCKED: Final[frozenset[Feature]] = frozenset({"discovery"})


def normalize_country_code(raw: object) -> str:
    """Return ISO 3166-1 alpha-2 upper-case, or empty string if invalid."""
    if not isinstance(raw, str):
        return ""
    cleaned = raw.strip().upper()
    if len(cleaned) != 2 or not cleaned.isalpha():
        return ""
    return cleaned


def is_allowed(country_code: object, feature: str = "trading") -> ComplianceResult:
    """Check if a country is allowed to use a given MAXIA feature.

    Unknown countries (not in any list) are allowed by default for discovery
    only, and blocked for everything else — fail-safe posture.
    """
    country = normalize_country_code(country_code)
    feat = str(feature).strip().lower()
    if feat not in ALL_FEATURES:
        return ComplianceResult(
            allowed=False,
            country=country,
            feature=feat,
            code="UNKNOWN",
            reason=f"Unknown feature: {feat}",
        )

    if not country:
        # No country info -> allow discovery, block everything else.
        allowed = feat == "discovery"
        return ComplianceResult(
            allowed=allowed,
            country="",
            feature=feat,
            code="UNKNOWN",
            reason="Country unknown" if not allowed else "Discovery OK (no country)",
        )

    if country in BLOCKED_COUNTRIES:
        return ComplianceResult(
            allowed=False,
            country=country,
            feature=feat,
            code="BLOCKED",
            reason=f"{country} is sanctioned or excluded by MAXIA ToS.",
        )

    if country in GEO_BLOCKED_COUNTRIES:
        allowed = feat in _OK_FEATURES_FOR_GEO_BLOCKED
        return ComplianceResult(
            allowed=allowed,
            country=country,
            feature=feat,
            code="GEO_BLOCKED",
            reason=(
                f"{country} requires local VASP registration — "
                f"only read-only discovery is permitted."
            ),
        )

    if country in ALLOWED_COUNTRIES:
        return ComplianceResult(
            allowed=True,
            country=country,
            feature=feat,
            code="OK",
            reason="Country in allowlist.",
        )

    # Unknown country (not blocked, not allowed): discovery only.
    allowed = feat == "discovery"
    return ComplianceResult(
        allowed=allowed,
        country=country,
        feature=feat,
        code="UNKNOWN",
        reason=(
            "Country not in MAXIA allowlist — discovery only. "
            "Contact support to request coverage."
        ),
    )
