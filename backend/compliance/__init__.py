"""MAXIA compliance layer — country filtering, OFAC checks, disclaimers."""
from compliance.country_filter import (
    ALLOWED_COUNTRIES,
    BLOCKED_COUNTRIES,
    GEO_BLOCKED_COUNTRIES,
    ComplianceResult,
    is_allowed,
    normalize_country_code,
)
from compliance.disclaimers import (
    SUPPORTED_LANGUAGES,
    get_disclaimer,
    get_short_disclaimer,
)

__all__ = [
    "ALLOWED_COUNTRIES",
    "BLOCKED_COUNTRIES",
    "GEO_BLOCKED_COUNTRIES",
    "ComplianceResult",
    "is_allowed",
    "normalize_country_code",
    "SUPPORTED_LANGUAGES",
    "get_disclaimer",
    "get_short_disclaimer",
]
