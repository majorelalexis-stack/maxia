"""MAXIA country filter — Plan Tier 1 Global geofencing (4 tiers).

Single source of truth for all geofencing decisions. Data comes from
``country_tiers.yaml`` (hot-reloaded on mtime change) and exposes both
a new 4-tier API and the legacy 3-tier API for backwards compat with
code that imports ``is_allowed(country, feature)``.

Tiers
-----

* ``HARD`` — sanctioned, zero access (even to marketing). HTTP 451 on
  every route. OFAC + UN + EU + MAXIA ToS.
* ``LICENSE`` — MAXIA does not hold the required CASP license for this
  jurisdiction. Trading/custody/payment blocked, but marketing and docs
  remain accessible so we do not unnecessarily limit brand reach.
* ``CAUTION`` — legal grey zone. Trading allowed but the user gets a
  first-visit modal asking them to acknowledge local-law responsibility.
  Cold outreach to CAUTION zones is forbidden (active solicitation rule).
* ``ALLOWED`` — fully open, no gate.

Countries not present in any tier default to ``UNKNOWN`` which behaves
like a strict LICENSE block (fail-safe posture).

Legacy API
----------

``is_allowed(country, feature=...) -> ComplianceResult`` is preserved
unchanged so that :mod:`backend.core.geo_blocking` and
:mod:`local_ceo.missions.github_prospect` keep working without edits.
The legacy 3-tier model maps as follows::

    HARD     -> BLOCKED
    LICENSE  -> GEO_BLOCKED
    CAUTION  -> ALLOWED (with banner at the frontend layer)
    ALLOWED  -> ALLOWED

Usage
-----

    from compliance.country_filter import (
        get_tier,
        is_allowed,
        is_country_allowed_for_outreach,
    )

    get_tier("FR")               # -> "license"
    is_allowed("FR", "trading")  # -> ComplianceResult(allowed=False, code="GEO_BLOCKED", ...)
    is_country_allowed_for_outreach("FR")  # -> False
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, Optional

log = logging.getLogger("maxia.compliance")

# ══════════════════════════════════════════
# Types
# ══════════════════════════════════════════

Tier = Literal["hard", "license", "caution", "allowed", "unknown"]
Feature = Literal[
    "discovery",      # public browsing, landing page
    "docs",           # documentation pages
    "pricing_data",   # /api/public/crypto/prices etc.
    "trading",        # swap, execute, grid, dca, sniper
    "custody",        # escrow, credits, wallet-held balances
    "payment",        # stream, onramp, invoices
    "withdrawal",     # cashout to fiat or external wallet
    "marketing",      # generic marketing umbrella
    "email_outreach", # cold email outbound
]

# Legacy feature names for backwards compat with old callers
_LEGACY_FEATURE_MAP: Final[dict[str, Feature]] = {
    "discovery": "discovery",
    "marketing": "marketing",
    "payment": "payment",
    "trading": "trading",
    "withdrawal": "withdrawal",
}

ALL_FEATURES: Final[frozenset[Feature]] = frozenset({
    "discovery", "docs", "pricing_data",
    "trading", "custody", "payment", "withdrawal",
    "marketing", "email_outreach",
})

# Legacy code values preserved for backwards compat
LegacyCode = Literal["OK", "BLOCKED", "GEO_BLOCKED", "UNKNOWN"]


@dataclass(frozen=True)
class ComplianceResult:
    """Immutable result of a compliance check.

    ``code`` uses the legacy 3-tier labels for backwards compat. Newer
    callers should use :func:`get_tier` + :func:`check_feature` instead
    of parsing this field.
    """

    allowed: bool
    country: str
    feature: str
    code: str       # OK | BLOCKED | GEO_BLOCKED | UNKNOWN
    reason: str
    tier: Tier = "unknown"  # NEW: 4-tier label for new callers


@dataclass(frozen=True)
class CountryEntry:
    """Metadata for one country in the YAML data."""

    code: str
    name: str
    tier: Tier
    reason: str = ""
    regulator: str = ""
    notes: str = ""


# ══════════════════════════════════════════
# Data loading — YAML source of truth
# ══════════════════════════════════════════

_DATA_PATH: Final[Path] = Path(__file__).parent / "country_tiers.yaml"
_data_lock = threading.Lock()
_country_index: dict[str, CountryEntry] = {}
_feature_gates: dict[str, dict[str, str]] = {}
_data_mtime: float = 0.0
_data_loaded: bool = False


def _default_feature_gates() -> dict[str, dict[str, str]]:
    """Hard-coded fallback if YAML is missing or malformed.

    Keeps the middleware functional in dev environments where the YAML
    may not yet be in place. Production should always have the YAML.
    """
    return {
        "discovery":     {"hard": "deny", "license": "allow", "caution": "allow", "allowed": "allow", "unknown": "allow"},
        "docs":          {"hard": "deny", "license": "allow", "caution": "allow", "allowed": "allow", "unknown": "allow"},
        "pricing_data":  {"hard": "deny", "license": "allow", "caution": "allow", "allowed": "allow", "unknown": "allow"},
        "trading":       {"hard": "deny", "license": "deny",  "caution": "allow_with_banner", "allowed": "allow", "unknown": "deny"},
        "custody":       {"hard": "deny", "license": "deny",  "caution": "allow_with_banner", "allowed": "allow", "unknown": "deny"},
        "payment":       {"hard": "deny", "license": "deny",  "caution": "allow_with_banner", "allowed": "allow", "unknown": "deny"},
        "withdrawal":    {"hard": "deny", "license": "deny",  "caution": "allow_with_banner", "allowed": "allow", "unknown": "deny"},
        "marketing":     {"hard": "deny", "license": "deny",  "caution": "deny",              "allowed": "allow", "unknown": "deny"},
        "email_outreach":{"hard": "deny", "license": "deny",  "caution": "deny",              "allowed": "allow", "unknown": "deny"},
    }


def _load_yaml_data() -> None:
    """Load or reload the YAML data file into the process-global index.

    Safe to call from any thread. Falls back to legacy hardcoded lists
    if the YAML is unreadable, so the module is never fully broken.
    """
    global _country_index, _feature_gates, _data_mtime, _data_loaded

    with _data_lock:
        if not _DATA_PATH.exists():
            if not _data_loaded:
                log.warning(
                    "[compliance] country_tiers.yaml not found at %s — "
                    "falling back to legacy hardcoded lists",
                    _DATA_PATH,
                )
                _load_legacy_fallback()
                _data_loaded = True
            return

        try:
            mtime = _DATA_PATH.stat().st_mtime
        except OSError as e:
            log.warning("[compliance] stat %s failed: %s", _DATA_PATH, e)
            if not _data_loaded:
                _load_legacy_fallback()
                _data_loaded = True
            return

        if _data_loaded and mtime == _data_mtime:
            return  # up-to-date

        try:
            import yaml  # type: ignore
        except ImportError:
            log.warning(
                "[compliance] PyYAML not installed — falling back to "
                "legacy hardcoded lists. Install with: pip install PyYAML"
            )
            if not _data_loaded:
                _load_legacy_fallback()
                _data_loaded = True
            return

        try:
            with open(_DATA_PATH, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except (OSError, yaml.YAMLError) as e:
            log.error("[compliance] YAML load error: %s — keeping previous data", e)
            if not _data_loaded:
                _load_legacy_fallback()
                _data_loaded = True
            return

        new_index: dict[str, CountryEntry] = {}
        for tier in ("hard", "license", "caution", "allowed"):
            for raw in data.get(tier, []) or []:
                if not isinstance(raw, dict):
                    continue
                code = normalize_country_code(raw.get("code"))
                if not code:
                    continue
                new_index[code] = CountryEntry(
                    code=code,
                    name=str(raw.get("name", code)),
                    tier=tier,  # type: ignore[arg-type]
                    reason=str(raw.get("reason", "")),
                    regulator=str(raw.get("regulator", "")),
                    notes=str(raw.get("notes", "")),
                )

        new_gates = data.get("feature_gates") or {}
        if not isinstance(new_gates, dict):
            new_gates = _default_feature_gates()

        _country_index = new_index
        _feature_gates = new_gates
        _data_mtime = mtime
        _data_loaded = True
        log.info(
            "[compliance] loaded %d countries from %s (mtime=%d)",
            len(new_index), _DATA_PATH.name, int(mtime),
        )


def _load_legacy_fallback() -> None:
    """Populate ``_country_index`` from the legacy hardcoded frozensets.

    Mirrors the previous 3-tier file so that the 4-tier API remains
    functional even if the YAML file is missing entirely.
    """
    global _country_index, _feature_gates

    legacy_allowed = {
        "SG", "HK", "KR", "TW", "TH", "VN", "MY", "ID", "PH", "AE", "IL",
        "JP", "AU", "NZ",
        "NG", "ZA", "KE", "EG", "GH", "MA", "TN", "SN",
        "BR", "AR", "MX", "CO", "CL", "PE",
    }
    legacy_geo_blocked = {"IN"}
    legacy_blocked = {
        "CN", "KP", "IR", "SY", "CU", "MM", "AF", "RU", "BY",
    }

    idx: dict[str, CountryEntry] = {}
    for c in legacy_blocked:
        idx[c] = CountryEntry(code=c, name=c, tier="hard", reason="Legacy hardcoded block")
    for c in legacy_geo_blocked:
        idx[c] = CountryEntry(code=c, name=c, tier="caution", reason="Legacy geo-blocked")
    for c in legacy_allowed:
        idx[c] = CountryEntry(code=c, name=c, tier="allowed", reason="Legacy allowlist")
    _country_index = idx
    _feature_gates = _default_feature_gates()


# ══════════════════════════════════════════
# Public API — 4-tier (new callers)
# ══════════════════════════════════════════

def normalize_country_code(raw: object) -> str:
    """Return ISO 3166-1 alpha-2 upper-case, or empty string if invalid."""
    if not isinstance(raw, str):
        return ""
    cleaned = raw.strip().upper()
    if len(cleaned) != 2 or not cleaned.isalpha():
        return ""
    return cleaned


def get_tier(country_code: object) -> Tier:
    """Return the tier ('hard'/'license'/'caution'/'allowed'/'unknown')
    for a country code. ``unknown`` is the fail-safe default.
    """
    _load_yaml_data()
    code = normalize_country_code(country_code)
    if not code:
        return "unknown"
    entry = _country_index.get(code)
    if entry is None:
        return "unknown"
    return entry.tier


def get_country_entry(country_code: object) -> Optional[CountryEntry]:
    """Return the full :class:`CountryEntry` metadata or ``None``."""
    _load_yaml_data()
    code = normalize_country_code(country_code)
    if not code:
        return None
    return _country_index.get(code)


def check_feature(
    country_code: object,
    feature: Feature,
) -> Literal["allow", "allow_with_banner", "deny"]:
    """Low-level decision: what should the system do for this
    (country, feature) pair? Returns one of three verbs that the
    middleware + admin + frontend all understand identically.
    """
    _load_yaml_data()
    tier = get_tier(country_code)
    gate = _feature_gates.get(str(feature), {})
    decision = gate.get(tier, "deny")
    if decision not in ("allow", "allow_with_banner", "deny"):
        return "deny"
    return decision  # type: ignore[return-value]


# ══════════════════════════════════════════
# Public API — legacy 3-tier (existing callers)
# ══════════════════════════════════════════

# Legacy frozensets exposed for any code that imports them directly.
# These are computed lazily from the YAML the first time they are
# accessed, then kept in sync via ``_load_yaml_data``.

def _legacy_lists() -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """Return (ALLOWED, GEO_BLOCKED, BLOCKED) frozensets derived from
    the current 4-tier index. Mapping:

    * HARD    -> BLOCKED
    * LICENSE -> GEO_BLOCKED  (trading blocked, marketing readable)
    * CAUTION -> GEO_BLOCKED  (stricter than before: no cold outreach)
    * ALLOWED -> ALLOWED
    """
    _load_yaml_data()
    allowed = frozenset(
        c for c, e in _country_index.items() if e.tier == "allowed"
    )
    geo_blocked = frozenset(
        c for c, e in _country_index.items() if e.tier in ("license", "caution")
    )
    blocked = frozenset(
        c for c, e in _country_index.items() if e.tier == "hard"
    )
    return allowed, geo_blocked, blocked


ALLOWED_COUNTRIES: frozenset[str] = frozenset()
GEO_BLOCKED_COUNTRIES: frozenset[str] = frozenset()
BLOCKED_COUNTRIES: frozenset[str] = frozenset()


def _refresh_legacy_globals() -> None:
    """Refresh the module-level legacy frozensets. Called on every
    ``is_allowed`` call so they stay in sync with the YAML hot-reload."""
    global ALLOWED_COUNTRIES, GEO_BLOCKED_COUNTRIES, BLOCKED_COUNTRIES
    ALLOWED_COUNTRIES, GEO_BLOCKED_COUNTRIES, BLOCKED_COUNTRIES = _legacy_lists()


def is_allowed(country_code: object, feature: str = "trading") -> ComplianceResult:
    """Check if a country is allowed to use a given MAXIA feature.

    Backwards-compatible wrapper around :func:`check_feature`. Legacy
    callers that expect a :class:`ComplianceResult` with
    ``code in {"OK","BLOCKED","GEO_BLOCKED","UNKNOWN"}`` keep working
    unchanged. New callers should prefer :func:`check_feature` directly.
    """
    _load_yaml_data()
    _refresh_legacy_globals()

    country = normalize_country_code(country_code)
    feat_raw = str(feature).strip().lower()
    if feat_raw not in ALL_FEATURES:
        return ComplianceResult(
            allowed=False,
            country=country,
            feature=feat_raw,
            code="UNKNOWN",
            reason=f"Unknown feature: {feat_raw}",
            tier="unknown",
        )

    feat: Feature = feat_raw  # type: ignore[assignment]
    tier = get_tier(country)
    decision = check_feature(country, feat)

    # Map to legacy code
    if not country:
        allowed = decision == "allow"
        return ComplianceResult(
            allowed=allowed,
            country="",
            feature=feat,
            code="UNKNOWN",
            reason=(
                "Discovery OK (no country detected)" if allowed
                else "Country unknown — fail-safe deny"
            ),
            tier="unknown",
        )

    entry = _country_index.get(country)
    if tier == "hard":
        return ComplianceResult(
            allowed=False,
            country=country,
            feature=feat,
            code="BLOCKED",
            reason=(entry.reason if entry else "Sanctioned or excluded by MAXIA ToS"),
            tier=tier,
        )
    if tier == "license":
        return ComplianceResult(
            allowed=(decision == "allow"),
            country=country,
            feature=feat,
            code="GEO_BLOCKED",
            reason=(
                entry.reason if entry
                else f"{country} requires a local license MAXIA does not hold."
            ),
            tier=tier,
        )
    if tier == "caution":
        return ComplianceResult(
            allowed=(decision in {"allow", "allow_with_banner"}),
            country=country,
            feature=feat,
            code="GEO_BLOCKED" if decision == "deny" else "OK",
            reason=(
                entry.reason if entry
                else f"{country} is a grey-zone jurisdiction."
            ),
            tier=tier,
        )
    if tier == "allowed":
        return ComplianceResult(
            allowed=(decision == "allow"),
            country=country,
            feature=feat,
            code="OK",
            reason="Country in allowlist.",
            tier=tier,
        )

    # Unknown country — fail-safe
    allowed = decision == "allow"
    return ComplianceResult(
        allowed=allowed,
        country=country,
        feature=feat,
        code="UNKNOWN",
        reason=(
            "Country not in MAXIA tiers — fail-safe deny. "
            "Contact support to request coverage."
        ),
        tier="unknown",
    )


# ══════════════════════════════════════════
# Convenience helpers for outreach gating
# ══════════════════════════════════════════

def is_country_allowed_for_outreach(country_code: object) -> bool:
    """Return True if MAXIA is allowed to send cold email / DM / push
    notifications to a resident of this country.

    Used by local_ceo missions (email_outreach, github_prospect,
    telegram_chat prospect handler, sales_agent) to enforce the active
    solicitation rule: outreach to HARD, LICENSE, CAUTION or UNKNOWN
    is forbidden. Only ALLOWED is open.
    """
    return get_tier(country_code) == "allowed"


def is_allowed_for_trading(country_code: object) -> bool:
    """Return True if a user in this country can hit trading routes."""
    return check_feature(country_code, "trading") in {"allow", "allow_with_banner"}


def caution_banner_required(country_code: object) -> bool:
    """Return True if the user should see a 'you are responsible for
    local-law compliance' modal at first visit."""
    return get_tier(country_code) == "caution"


# ══════════════════════════════════════════
# Hot reload trigger (admin endpoint)
# ══════════════════════════════════════════

def reload_data() -> dict:
    """Force a full reload of the YAML data. Returns basic stats for
    audit logging / admin UI."""
    global _data_loaded, _data_mtime
    with _data_lock:
        _data_loaded = False
        _data_mtime = 0.0
    _load_yaml_data()
    return {
        "loaded": _data_loaded,
        "countries": len(_country_index),
        "mtime": _data_mtime,
        "source": str(_DATA_PATH),
        "fallback": not _DATA_PATH.exists(),
    }


def stats() -> dict:
    """Return summary stats for the admin dashboard."""
    _load_yaml_data()
    by_tier: dict[str, int] = {"hard": 0, "license": 0, "caution": 0, "allowed": 0}
    for entry in _country_index.values():
        by_tier[entry.tier] = by_tier.get(entry.tier, 0) + 1
    return {
        "total": len(_country_index),
        "by_tier": by_tier,
        "source": str(_DATA_PATH),
        "mtime": _data_mtime,
        "fallback": not _DATA_PATH.exists(),
    }


# Eager load at import so the module is self-consistent from the start
try:
    _load_yaml_data()
    _refresh_legacy_globals()
except Exception as _e:  # pragma: no cover
    log.error("[compliance] initial load failed: %s — fallback in use", _e)
    _load_legacy_fallback()
    _refresh_legacy_globals()
