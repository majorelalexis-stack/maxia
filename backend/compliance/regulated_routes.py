"""Route classifier for the geofence middleware.

Classifies every incoming HTTP path into one of four buckets so that
the middleware knows whether to apply country-based restrictions:

* ``ALWAYS_OPEN`` — public marketing, docs, pure data feeds. Accessible
  from every country except ``hard`` tier (sanctions, HTTP 451).
* ``CASP`` — crypto-asset service regulated by MiCA / FinCEN / FCA / …
  Blocked for ``hard`` + ``license`` + ``unknown`` tiers. Allowed for
  ``caution`` (with banner) and ``allowed``.
* ``CASP_READ`` — read-only CASP view (e.g. see a quote but don't
  execute). Same gate as CASP for now, kept as a separate label so we
  can loosen later if needed.
* ``ADMIN`` — internal admin endpoints. Not geo-gated (auth already
  enforces tight access) — we don't block our own monitoring traffic.

The classifier uses **prefix matching** plus an optional regex fallback
for finer-grained cases (e.g. a subtree needs to be classified
differently from its parent). Keep the lists ordered: more specific
first, generic fallbacks last.
"""
from __future__ import annotations

import re
from typing import Final, Literal

RouteClass = Literal["always_open", "casp", "casp_read", "admin"]


# ══════════════════════════════════════════
# Prefix rules (fast path, evaluated in order)
# ══════════════════════════════════════════

# Patterns evaluated in order. First match wins.
# Use "/" as suffix to match a whole subtree.
_PREFIX_RULES: Final[tuple[tuple[str, RouteClass], ...]] = (
    # ── ALWAYS OPEN (marketing, docs, pure data) ─────────────────
    ("/.well-known/",                "always_open"),
    ("/docs",                        "always_open"),
    ("/redoc",                       "always_open"),
    ("/openapi.json",                "always_open"),
    ("/static/",                     "always_open"),
    ("/assets/",                     "always_open"),
    ("/favicon",                     "always_open"),
    ("/robots.txt",                  "always_open"),
    ("/sitemap",                     "always_open"),
    ("/llms.txt",                    "always_open"),
    ("/llms-full.txt",               "always_open"),

    # Public info + pricing data (non-transactional)
    ("/api/public/crypto/prices",    "always_open"),
    ("/api/public/crypto/prices/",   "always_open"),
    ("/api/public/marketplace-stats","always_open"),
    ("/api/public/services",         "always_open"),  # discovery only (list)
    ("/api/public/discover",         "always_open"),
    ("/api/public/fear-greed",       "always_open"),
    ("/api/public/trending",         "always_open"),
    ("/api/public/docs",             "always_open"),
    ("/api/public/status",           "always_open"),
    ("/api/public/health",           "always_open"),
    ("/api/public/stats",            "always_open"),

    # Oracle price feeds
    ("/api/oracle/",                 "always_open"),
    ("/oracle/",                     "always_open"),

    # A2A / MCP / agent discovery
    ("/api/agent/a2a/adapter-config","always_open"),
    ("/api/agent/a2a/session",       "always_open"),
    ("/api/agent/mesh/discover",     "always_open"),
    ("/api/agent/mesh/agent",        "always_open"),
    ("/api/agentverse/",             "always_open"),
    ("/mcp/manifest",                "always_open"),
    ("/mcp/tools/list",              "always_open"),

    # Legal / compliance pages
    ("/legal",                       "always_open"),
    ("/terms",                       "always_open"),
    ("/privacy",                     "always_open"),
    ("/compliance",                  "always_open"),

    # Blog, landing, marketing
    ("/blog",                        "always_open"),
    ("/about",                       "always_open"),
    ("/pricing-page",                "always_open"),
    ("/security-page",               "always_open"),
    ("/faq",                         "always_open"),

    # Agent public profile pages (discovery)
    ("/agent/",                      "always_open"),

    # ── ADMIN (auth-gated, not geo-gated) ────────────────────────
    ("/api/admin/",                  "admin"),
    ("/admin/",                      "admin"),
    ("/dashboard",                   "admin"),
    ("/metrics",                     "admin"),
    ("/healthz",                     "admin"),

    # Auth endpoints — always open (users need to be able to connect)
    ("/api/auth/",                   "always_open"),

    # ── CASP: custody / escrow / credits ─────────────────────────
    ("/api/credits/",                "casp"),
    ("/api/escrow/",                 "casp"),
    ("/api/stream/",                 "casp"),  # streaming payments
    ("/api/pod/",                    "casp"),  # proof of delivery (escrow-adjacent)

    # ── CASP: exchange / swap / trading ──────────────────────────
    ("/api/public/crypto/swap",      "casp"),
    ("/api/public/crypto/quote",     "casp"),
    ("/api/crypto/swap",             "casp"),
    ("/api/swap/",                   "casp"),
    ("/api/trading/execute",         "casp"),
    ("/api/trading/grid",            "casp"),
    ("/api/trading/dca",             "casp"),
    ("/api/trading/candles",         "casp_read"),   # chart data is read-only
    ("/api/trading/signals",         "casp_read"),
    ("/api/public/execute",          "casp"),

    # ── CASP: order execution (sniper / grid / dca / copy / smart) ──
    ("/api/sniper/",                 "casp"),
    ("/api/grid/",                   "casp"),
    ("/api/dca/",                    "casp"),
    ("/api/copy-trading/",           "casp"),
    ("/api/smart-exec/",             "casp"),

    # ── CASP: portfolio management / DeFi positions ─────────────
    ("/api/defi/",                   "casp"),
    ("/api/defi-scanner/",           "casp_read"),   # scanning is read-only

    # ── CASP: tokenized stocks ───────────────────────────────────
    ("/api/stocks/",                 "casp"),
    ("/api/public/stocks/price",     "casp_read"),   # price view only
    ("/api/public/stocks",           "casp_read"),   # list + price

    # ── CASP: GPU rental (paid in crypto, operation of platform) ─
    ("/api/gpu/rent",                "casp"),
    ("/api/gpu/spawn",               "casp"),
    ("/api/gpu/",                    "casp_read"),   # listing + tiers OK

    # ── CASP: agent mesh (paid execution) ───────────────────────
    ("/api/agent/mesh/execute",      "casp"),
    ("/api/agent/mesh/register",     "casp"),  # registering a paid skill = operator

    # ── CASP: MCP tool calls (some tools are financial) ──────────
    ("/mcp/tools/call",              "casp"),  # conservative: block all tool exec
)


# ══════════════════════════════════════════
# Regex fallback rules
# ══════════════════════════════════════════

# Used for patterns that can't be expressed as prefixes.
# Evaluated AFTER prefix rules.
_REGEX_RULES: Final[tuple[tuple[re.Pattern[str], RouteClass], ...]] = (
    # Numbered versioned paths under /api/v1/, /api/v2/…
    (re.compile(r"^/api/v\d+/public/"),          "always_open"),
    (re.compile(r"^/api/v\d+/(swap|trade|exec)"),"casp"),
)


# ══════════════════════════════════════════
# Public API
# ══════════════════════════════════════════

def classify_route(path: str, method: str = "GET") -> RouteClass:
    """Classify a path into one of the 4 buckets.

    Unknown paths default to ``always_open`` so we never accidentally
    block something harmless. The fail-safe direction here is the
    opposite of :mod:`country_filter` — false negatives in the classifier
    would be security holes (unblocked CASP routes), but the
    middleware ALSO checks tier-level constraints, so an unknown path
    classified as open only reaches the country gate, which itself
    always blocks HARD tier. Balance: prefer false opens here, catch
    them at the tier layer.
    """
    if not isinstance(path, str):
        return "always_open"
    p = path.rstrip("/").lower() or "/"

    for prefix, klass in _PREFIX_RULES:
        if p == prefix.rstrip("/").lower() or p.startswith(prefix.lower()):
            return klass

    for pattern, klass in _REGEX_RULES:
        if pattern.match(path):
            return klass

    return "always_open"


def is_casp_route(path: str, method: str = "GET") -> bool:
    """Convenience: return True if the route is CASP-regulated."""
    return classify_route(path, method) in {"casp", "casp_read"}


def is_always_open(path: str, method: str = "GET") -> bool:
    """Convenience: return True if the route is always accessible
    (subject only to HARD tier sanctions)."""
    return classify_route(path, method) == "always_open"
