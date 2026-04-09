"""MAXIA — GitHub public-profile prospector for email outreach (Plan V8 / P4).

Scrapes GitHub's public REST API to find crypto / AI developer accounts
with a **public** email address, then hands the curated list to the
existing ``EmailOutreach`` engine. Completely legal: we only read data
that users have explicitly published on their GitHub profile.

Design principles:
- **Never** collect emails that are not public on GitHub.
- Respect GitHub rate limits (5000 req/h with token, 60 req/h without).
- Deduplicate per email across runs via a persistent cache.
- Country is inferred from ``location`` field when possible — used to
  route through the compliance filter before any outreach.
- Language defaults to English unless the profile bio hints otherwise.

Usage::

    from marketing.github_prospector import GithubProspector

    prospector = GithubProspector()
    prospects = await prospector.search(
        topics=["solana-sdk", "ai-agent"],
        max_results=50,
    )

    # Hand off to EmailOutreach
    from marketing import EmailOutreach, render_outreach_email
    engine = EmailOutreach()
    for p in prospects:
        if not p.email:
            continue
        subject, text, html = render_outreach_email(
            lang=p.lang, name=p.name, cta_link="...", unsubscribe_link="...",
        )
        try:
            await engine.send(
                to=p.email, subject=subject, body_text=text, body_html=html,
                lang=p.lang, country=p.country,
            )
        except Exception as e:
            print(f"skip {p.email}: {e}")

Environment:
    GITHUB_TOKEN — optional but strongly recommended (raises limit 60 -> 5000/h)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("maxia.marketing.github_prospector")

GITHUB_API = "https://api.github.com"

# ── Country code normalization from GitHub location strings ──
#
# GitHub "location" is free text ("Paris, France", "SG", "San Francisco").
# We only need to infer ISO 3166-1 alpha-2 for compliance filtering. A
# tight whitelist of common signals avoids false positives.
_COUNTRY_HINTS: dict[str, str] = {
    # Asia allowed
    "singapore": "SG", "sg": "SG",
    "hong kong": "HK", "hongkong": "HK", "hk": "HK",
    "south korea": "KR", "korea": "KR", "seoul": "KR",
    "taiwan": "TW", "taipei": "TW",
    "thailand": "TH", "bangkok": "TH",
    "vietnam": "VN", "hanoi": "VN", "saigon": "VN",
    "malaysia": "MY", "kuala lumpur": "MY",
    "indonesia": "ID", "jakarta": "ID",
    "philippines": "PH", "manila": "PH",
    "uae": "AE", "dubai": "AE", "abu dhabi": "AE",
    "israel": "IL", "tel aviv": "IL",
    # Japan
    "japan": "JP", "tokyo": "JP", "osaka": "JP",
    # Oceania
    "australia": "AU", "sydney": "AU", "melbourne": "AU",
    "new zealand": "NZ", "auckland": "NZ",
    # Africa allowed
    "nigeria": "NG", "lagos": "NG",
    "south africa": "ZA", "cape town": "ZA", "johannesburg": "ZA",
    "kenya": "KE", "nairobi": "KE",
    "egypt": "EG", "cairo": "EG",
    "ghana": "GH", "accra": "GH",
    "morocco": "MA", "casablanca": "MA",
    "tunisia": "TN", "tunis": "TN",
    "senegal": "SN", "dakar": "SN",
    # Latin America allowed
    "brazil": "BR", "brasil": "BR", "sao paulo": "BR", "rio": "BR",
    "argentina": "AR", "buenos aires": "AR",
    "mexico": "MX", "cdmx": "MX",
    "colombia": "CO", "bogota": "BO",
    "chile": "CL", "santiago": "CL",
    "peru": "PE", "lima": "PE",
    # Europe (not in allowlist but often targeted)
    "france": "FR", "paris": "FR",
    "germany": "DE", "berlin": "DE",
    "uk": "GB", "united kingdom": "GB", "london": "GB",
    "spain": "ES", "madrid": "ES", "barcelona": "ES",
    # Blocked / geo-blocked (explicitly set so we skip them)
    "india": "IN", "bangalore": "IN", "mumbai": "IN", "delhi": "IN",
    "china": "CN", "beijing": "CN", "shanghai": "CN", "shenzhen": "CN",
    "russia": "RU", "moscow": "RU",
    "iran": "IR", "tehran": "IR",
    "usa": "US", "united states": "US", "san francisco": "US", "new york": "US",
    "nyc": "US", "los angeles": "US", "seattle": "US", "boston": "US",
}

_LANG_BY_COUNTRY: dict[str, str] = {
    "SG": "en", "HK": "en", "MY": "en", "PH": "en", "AE": "en", "IL": "en",
    "AU": "en", "NZ": "en", "NG": "en", "ZA": "en", "KE": "en", "EG": "en",
    "GH": "en", "SN": "fr",
    "JP": "ja", "KR": "ko", "TW": "zh-tw", "TH": "th", "VN": "vi", "ID": "id",
    "MA": "fr", "TN": "fr",
    "BR": "pt-br", "AR": "es", "MX": "es", "CO": "es", "CL": "es", "PE": "es",
    "FR": "fr", "DE": "en", "GB": "en", "ES": "es",
}


@dataclass(frozen=True)
class GithubProspect:
    """Immutable record of a GitHub user we consider contacting."""
    login: str
    name: str
    email: str
    bio: str
    location: str
    country: str   # ISO 3166-1 alpha-2 or ""
    lang: str      # canonical i18n key (en, fr, ja, ...)
    profile_url: str
    public_repos: int
    followers: int
    topics_matched: tuple[str, ...]


@dataclass
class GithubProspector:
    """Async GitHub search + profile fetcher.

    Respects rate limits by pausing between requests. Dedupes by lowercase
    email using a JSON cache file (``~/.maxia_github_prospects.json`` by
    default). Only emails returned by the GitHub API under ``user.email``
    are collected — never scraped from repository commits, issues, or any
    other non-profile source.
    """
    token: str = field(default_factory=lambda: os.getenv("GITHUB_TOKEN", ""))
    cache_path: str = field(default_factory=lambda: os.path.expanduser(
        "~/.maxia_github_prospects.json"
    ))
    max_per_topic: int = 30
    min_followers: int = 5
    min_public_repos: int = 3

    _seen_emails: set[str] = field(default_factory=set, init=False)

    def __post_init__(self) -> None:
        self._load_cache()

    def _load_cache(self) -> None:
        try:
            with open(self.cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._seen_emails = set(str(e).lower() for e in data.get("emails", []))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            self._seen_emails = set()

    def _save_cache(self) -> None:
        try:
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump({"emails": sorted(self._seen_emails)}, f)
        except OSError as e:
            logger.warning("[github] cache save failed: %s", e)

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "MAXIA-Prospector/1.0 (+https://maxiaworld.app)",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    @staticmethod
    def _infer_country(location: str) -> str:
        if not isinstance(location, str) or not location:
            return ""
        lower = location.lower()
        # Direct substring match, longest key first
        for key in sorted(_COUNTRY_HINTS.keys(), key=len, reverse=True):
            if key in lower:
                return _COUNTRY_HINTS[key]
        return ""

    @staticmethod
    def _lang_for(country: str) -> str:
        return _LANG_BY_COUNTRY.get(country, "en")

    @staticmethod
    def _is_valid_email(email: object) -> bool:
        if not isinstance(email, str) or not email:
            return False
        # Simple RFC-ish regex, strict enough for outreach
        return bool(re.match(r"^[\w.+\-]+@[\w\-]+\.[\w\-.]+$", email))

    async def _http_get(self, client, path: str, params: dict | None = None) -> Optional[dict]:
        import httpx as _httpx
        url = f"{GITHUB_API}{path}"
        try:
            resp = await client.get(url, params=params, headers=self._headers(), timeout=15)
        except _httpx.HTTPError as e:
            logger.warning("[github] http error %s: %s", path, e)
            return None

        if resp.status_code == 403 and "rate limit" in resp.text.lower():
            reset = int(resp.headers.get("X-RateLimit-Reset", "0"))
            wait = max(5, reset - int(__import__("time").time()))
            logger.warning("[github] rate limited, sleeping %ds", wait)
            await asyncio.sleep(min(wait, 120))
            return None
        if resp.status_code == 404:
            return None
        if not 200 <= resp.status_code < 300:
            logger.warning("[github] HTTP %d on %s: %s",
                           resp.status_code, path, resp.text[:200])
            return None
        try:
            return resp.json()
        except Exception as e:
            logger.warning("[github] json decode error: %s", e)
            return None

    async def _search_topic(self, client, topic: str) -> list[str]:
        """Return up to ``max_per_topic`` repo full_names matching a topic."""
        params = {
            "q": f"topic:{topic}",
            "sort": "stars",
            "order": "desc",
            "per_page": self.max_per_topic,
        }
        data = await self._http_get(client, "/search/repositories", params=params)
        if not data:
            return []
        items = data.get("items", []) or []
        return [item.get("full_name", "") for item in items if item.get("full_name")]

    async def _fetch_repo_owner(self, client, full_name: str) -> Optional[GithubProspect]:
        if "/" not in full_name:
            return None
        owner = full_name.split("/", 1)[0]
        user = await self._http_get(client, f"/users/{owner}")
        if not user:
            return None

        email = user.get("email", "") or ""
        name = user.get("name", "") or owner
        bio = user.get("bio", "") or ""
        location = user.get("location", "") or ""
        country = self._infer_country(location)
        lang = self._lang_for(country)

        if not self._is_valid_email(email):
            return None
        if email.lower() in self._seen_emails:
            return None

        followers = int(user.get("followers", 0) or 0)
        public_repos = int(user.get("public_repos", 0) or 0)
        if followers < self.min_followers or public_repos < self.min_public_repos:
            return None

        return GithubProspect(
            login=owner,
            name=name,
            email=email,
            bio=bio[:300],
            location=location[:100],
            country=country,
            lang=lang,
            profile_url=str(user.get("html_url", "")),
            public_repos=public_repos,
            followers=followers,
            topics_matched=(),
        )

    async def search(
        self,
        topics: list[str],
        max_results: int = 50,
    ) -> list[GithubProspect]:
        """Search GitHub for repo owners matching the given topics.

        Returns a deduped list (by lowercase email) filtered by min
        followers, min public repos, and valid RFC-5322 email.
        """
        import httpx

        prospects: list[GithubProspect] = []
        async with httpx.AsyncClient() as client:
            for topic in topics:
                if len(prospects) >= max_results:
                    break
                repos = await self._search_topic(client, topic)
                for repo in repos:
                    if len(prospects) >= max_results:
                        break
                    prospect = await self._fetch_repo_owner(client, repo)
                    if prospect is None:
                        continue
                    # Stamp the topic that brought them in
                    prospect = GithubProspect(
                        **{**prospect.__dict__, "topics_matched": (topic,)}
                    )
                    prospects.append(prospect)
                    self._seen_emails.add(prospect.email.lower())
                    # Gentle pacing between profile fetches
                    await asyncio.sleep(0.25)
                # Gentle pacing between topics
                await asyncio.sleep(0.5)

        self._save_cache()
        logger.info("[github] found %d new prospects across %d topics",
                    len(prospects), len(topics))
        return prospects
