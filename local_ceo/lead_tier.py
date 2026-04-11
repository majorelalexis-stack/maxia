"""Lead tier detection for the MAXIA local CEO.

Thin wrapper around ``backend.compliance.country_filter`` that maps a
prospect's country code to:

* a ``tier`` : ``"allowed" | "license" | "caution" | "hard" | "unknown"``
* a ``pitch_mode`` : ``"full" | "developer" | "readonly" | "blocked"``
* a ``score_bonus`` : integer delta applied to prospect ranking
* a set of ``allowed_features`` : the product surface legal in that tier

The CEO uses this module to:

1. **Route sales pitches** — the sales agent picks the catalog blob that
   matches the tier's pitch mode. Allowed jurisdictions get the full
   crypto-trading pitch; LICENSE jurisdictions get the developer /
   marketplace / API pitch without any custodial trading language.

2. **Rank prospects** — github_prospect boosts allowed-country leads
   and penalises caution-country leads so the outreach queue naturally
   prioritises the easiest wins.

3. **Block outreach** — HARD tier prospects are never contacted.

Design principles
-----------------

* **Safe by default**: if the backend import fails at CEO startup
  (detached shell, missing sys.path, YAML not loaded yet), every
  public function returns the ``"unknown"`` tier with ``"full"``
  pitch and ``0`` bonus — the CEO keeps running, never crashes.

* **One source of truth**: the 128-country list lives in
  ``backend/compliance/country_tiers.yaml``. This module never
  duplicates it.

* **Free of I/O**: all lookups are in-memory after the first call.
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Any

log = logging.getLogger("maxia.local_ceo.lead_tier")


# ── Lazy import of backend.compliance.country_filter ──────────────────

_country_filter: Any | None = None
_import_attempted: bool = False


def _load_country_filter() -> Any | None:
    """Import ``backend.compliance.country_filter`` once, adding the
    repo root to ``sys.path`` if needed. Returns the module or None
    on failure. Never raises."""
    global _country_filter, _import_attempted
    if _country_filter is not None or _import_attempted:
        return _country_filter
    _import_attempted = True

    try:
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        backend_dir = os.path.join(repo_root, "backend")
        if backend_dir not in sys.path:
            sys.path.insert(0, backend_dir)
        from compliance import country_filter as cf  # type: ignore
        _country_filter = cf
        log.info("[lead_tier] loaded backend.compliance.country_filter")
    except Exception as e:
        log.warning("[lead_tier] country_filter import failed: %s", e)
        _country_filter = None
    return _country_filter


# ── Static mapping (tier → pitch / score / features) ──────────────────

_PITCH_MODE_BY_TIER: dict[str, str] = {
    "allowed": "full",      # full crypto-trading pitch, no restrictions
    "license": "developer", # marketplace / GPU / API / enterprise only
    "caution": "readonly",  # read-only API / sandbox / docs
    "hard":    "blocked",   # never contact
    "unknown": "full",      # benefit of the doubt (allowed_for_outreach == False
                            # already filters this out upstream)
}

_SCORE_BONUS_BY_TIER: dict[str, int] = {
    "allowed": 30,
    "license": 15,
    "caution": -20,
    "hard":    -100,
    "unknown": 0,
}

# Features safe to mention per pitch mode. These strings match sections
# of ``sales_agent._build_catalog_blob`` — kept in sync manually.
_ALLOWED_FEATURES_BY_PITCH: dict[str, frozenset[str]] = {
    "full": frozenset({
        "marketplace_fees", "swap_fees", "escrow", "trading",
        "tokenized_stocks", "defi_yields", "gpu_rental",
        "ai_services", "enterprise_features", "security",
        "free_tier", "differentiators", "how_to_start",
    }),
    "developer": frozenset({
        "marketplace_fees",
        # NO swap_fees — custodial trading language removed
        # NO escrow — trading-coupled language removed
        "gpu_rental",
        "ai_services",
        "enterprise_features",
        "security",
        "free_tier",
        "differentiators",
        "how_to_start",
    }),
    "readonly": frozenset({
        "ai_services",     # public read of service catalog
        "free_tier",       # 100 req/day API key
        "how_to_start",    # sandbox onboarding
        "security",
    }),
    "blocked": frozenset(),
}


# ── Public API ─────────────────────────────────────────────────────────

def get_tier(country_code: str | None) -> str:
    """Return the tier string for a country code. Safe on any input."""
    if not country_code:
        return "unknown"
    cf = _load_country_filter()
    if cf is None:
        return "unknown"
    try:
        return str(cf.get_tier(country_code)).lower()
    except Exception as e:
        log.debug("[lead_tier] get_tier(%r) failed: %s", country_code, e)
        return "unknown"


def get_pitch_mode(tier: str) -> str:
    """Return the pitch mode for a tier string."""
    return _PITCH_MODE_BY_TIER.get((tier or "").lower(), "full")


def score_bonus(tier: str) -> int:
    """Return the ranking delta for a tier string."""
    return _SCORE_BONUS_BY_TIER.get((tier or "").lower(), 0)


def allowed_features_for(pitch_mode: str) -> frozenset[str]:
    """Return the set of feature keys the pitch mode is allowed to mention."""
    return _ALLOWED_FEATURES_BY_PITCH.get(
        (pitch_mode or "").lower(), _ALLOWED_FEATURES_BY_PITCH["full"]
    )


def get_tier_for_country(country_code: str | None) -> dict[str, Any]:
    """Bundled lookup — returns everything the CEO needs about a prospect."""
    tier = get_tier(country_code)
    pitch_mode = get_pitch_mode(tier)
    return {
        "country": (country_code or "").upper() or None,
        "tier": tier,
        "pitch_mode": pitch_mode,
        "score_bonus": score_bonus(tier),
        "allowed_features": sorted(allowed_features_for(pitch_mode)),
        "blocked": pitch_mode == "blocked",
    }


def infer_country(
    *,
    language_code: str | None = None,
    location_text: str | None = None,
    profile_country: str | None = None,
) -> str:
    """Best-effort country inference from whatever fields a prospect has.

    Priority order:
        1. ``profile_country`` if already an ISO-2 code (GitHub API returns this)
        2. ``language_code`` — parse BCP-47 region suffix, else fall back to
           a small language → likely-country map
        3. ``location_text`` keyword match against a small city/country map

    Returns an uppercase ISO-2 code or empty string.
    """
    # 1. Profile country (GitHub API puts one here)
    if profile_country:
        pc = str(profile_country).strip().upper()
        if len(pc) == 2 and pc.isalpha():
            return pc

    # 2. Language code — inlined logic so we don't depend on
    #    ``local_ceo.missions`` (which has side-effectful __init__).
    if language_code:
        lc = str(language_code).strip().lower()
        if lc:
            if "-" in lc:
                lang, region = lc.split("-", 1)
                region = region.strip().upper()
                if len(region) == 2 and region.isalpha():
                    return region
                lc = lang
            if lc in _LANG_TO_LIKELY_COUNTRY:
                return _LANG_TO_LIKELY_COUNTRY[lc]

    # 3. Location text keyword scan (last-ditch, conservative)
    if location_text:
        loc = str(location_text).lower()
        for keyword, cc in _LOCATION_KEYWORDS.items():
            if keyword in loc:
                return cc

    return ""


# Inlined subset of the telegram_chat _LANG_TO_LIKELY_COUNTRY map.
# Only high-signal single-language cases — for languages spoken across
# many countries (es, en, fr, ar, pt, de, ru), we return the largest
# market by convention.
_LANG_TO_LIKELY_COUNTRY: dict[str, str] = {
    "en": "US",   # anglosphere default
    "fr": "FR",   # francosphere default
    "de": "DE",
    "es": "ES",   # Spain default — LATAM needs an explicit region
    "pt": "PT",   # Portugal default — BR is explicit via pt-BR
    "it": "IT",
    "nl": "NL",
    "ja": "JP",
    "ko": "KR",
    "zh": "CN",
    "zh-cn": "CN",
    "zh-tw": "TW",
    "zh-hk": "HK",
    "ru": "RU",
    "ar": "SA",   # Arabic → Saudi default; UAE / EG explicit via region
    "tr": "TR",
    "id": "ID",
    "ms": "MY",
    "th": "TH",
    "vi": "VN",
    "hi": "IN",
    "bn": "BD",
    "fa": "IR",
    "he": "IL",
    "pl": "PL",
    "uk": "UA",
    "cs": "CZ",
    "sv": "SE",
    "no": "NO",
    "fi": "FI",
    "da": "DK",
    "hu": "HU",
    "el": "GR",
    "ro": "RO",
}


# Tiny keyword map — conservative, only high-signal city/country names.
# We don't try to be exhaustive; infer_country falls back to "" on miss.
_LOCATION_KEYWORDS: dict[str, str] = {
    "dubai": "AE", "uae": "AE", "emirates": "AE",
    "singapore": "SG",
    "hong kong": "HK",
    "tokyo": "JP", "japan": "JP",
    "seoul": "KR", "korea": "KR",
    "london": "GB", "uk": "GB", "united kingdom": "GB", "england": "GB",
    "paris": "FR", "france": "FR",
    "berlin": "DE", "germany": "DE", "münchen": "DE", "munich": "DE",
    "madrid": "ES", "spain": "ES", "barcelona": "ES",
    "rome": "IT", "italy": "IT", "milan": "IT",
    "amsterdam": "NL", "netherlands": "NL",
    "zurich": "CH", "geneva": "CH", "switzerland": "CH",
    "stockholm": "SE", "sweden": "SE",
    "oslo": "NO", "norway": "NO",
    "helsinki": "FI", "finland": "FI",
    "copenhagen": "DK", "denmark": "DK",
    "new york": "US", "san francisco": "US", "usa": "US",
    "united states": "US", "california": "US", "texas": "US",
    "toronto": "CA", "canada": "CA", "vancouver": "CA", "montreal": "CA",
    "sydney": "AU", "melbourne": "AU", "australia": "AU",
    "auckland": "NZ", "new zealand": "NZ",
    "mumbai": "IN", "bangalore": "IN", "delhi": "IN", "india": "IN",
    "jakarta": "ID", "indonesia": "ID",
    "manila": "PH", "philippines": "PH",
    "bangkok": "TH", "thailand": "TH",
    "istanbul": "TR", "turkey": "TR",
    "lagos": "NG", "nigeria": "NG",
    "nairobi": "KE", "kenya": "KE",
    "cape town": "ZA", "johannesburg": "ZA", "south africa": "ZA",
    "sao paulo": "BR", "são paulo": "BR", "brazil": "BR", "brasil": "BR",
    "buenos aires": "AR", "argentina": "AR",
    "mexico city": "MX", "mexico": "MX",
    "santiago": "CL", "chile": "CL",
    "bogota": "CO", "colombia": "CO",
    "lima": "PE", "peru": "PE",
    "tel aviv": "IL", "israel": "IL",
    "riyadh": "SA", "saudi": "SA",
}
