"""US_SAFE content guardrails for local CEO outreach generators.

Phase US.2 — Templates US-safe for cold-email generators
(``email_outreach.py``, ``email_manager.send_outbound_prospect()``,
``github_prospect.py``).

The US is the only jurisdiction the local CEO contacts with extra
content restrictions: the ``MaxiaSalesAgent`` pitch is tier-aware via
``lead_tier.py`` + ``country_tiers.yaml``, but the cold-email generators
write their own prompts and do not consult any catalog. This module
closes that gap:

1. ``is_us_safe_required(country_code)`` — returns True when the
   prospect's country is in the ``limited`` list of
   ``memory_prod/country_allowlist.json`` (currently ``["US"]``). We
   intentionally use the local list as the source of truth here, NOT
   the backend's wider ``license`` tier (which would also catch
   SG/FR/JP/EU where MAXIA's allowed feature set is broader and the
   standard pitch is appropriate).

2. ``us_safe_prompt_rules()`` — returns the extra HARD RULES block to
   append to any LLM prompt when US_SAFE mode is active.

3. ``scrub_us_forbidden(text)`` — last-resort regex post-filter that
   removes sentences mentioning ``swap``, ``tokenized stock``, ``xStock``,
   ``bridge``, ``lightning``, ``escrow trading``. Pattern inspired by
   ``MaxiaSalesAgent._scrub_competitor_pricing``.

Design principles
-----------------

* **Fail-open on unknown country**: no country info → no US_SAFE (we do
  not know the jurisdiction, so we let the default catalog handle it).
* **Minimal I/O**: one JSON file read, cached on first call.
* **Conservative scrub**: whole sentences are neutralised, not partial
  substrings, so we never create a half-baked sentence.
"""
from __future__ import annotations

import json
import logging
import os
import re

log = logging.getLogger("maxia.local_ceo.us_safe_content")


# ── Source of truth: country_allowlist.json "limited" list ────────────

_ALLOWLIST_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "memory_prod",
    "country_allowlist.json",
)

# Hardcoded fallback if the JSON is missing or malformed. Matches the
# plan_ceo.md US.1 spec literally.
_FALLBACK_LIMITED: frozenset[str] = frozenset({"US"})

_cached_limited: frozenset[str] | None = None


def _load_limited_countries() -> frozenset[str]:
    """Read the ``limited`` list from country_allowlist.json once, cache."""
    global _cached_limited
    if _cached_limited is not None:
        return _cached_limited
    try:
        with open(_ALLOWLIST_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            limited = data.get("limited", [])
            if isinstance(limited, list):
                _cached_limited = frozenset(
                    str(c).upper() for c in limited if isinstance(c, str)
                )
                return _cached_limited
    except Exception as e:
        log.warning(
            "[us_safe] country_allowlist.json unreadable (%s) — "
            "falling back to %s",
            e, sorted(_FALLBACK_LIMITED),
        )
    _cached_limited = _FALLBACK_LIMITED
    return _cached_limited


def is_us_safe_required(
    country_code: str | None = None,
    tier: str | None = None,
) -> bool:
    """Return True if US_SAFE mode should be applied to this prospect.

    Uses ``country_allowlist.json:limited`` (local source of truth).
    Unknown / empty country → ``False`` (fail-open to the default catalog).

    The ``tier`` argument is accepted for API symmetry with
    ``lead_tier.get_tier`` callers but is intentionally unused: US.2
    scope is strictly the local CEO's ``limited`` list, not the
    backend's wider ``license`` tier.
    """
    _ = tier  # intentionally unused, see docstring
    if not country_code:
        return False
    return str(country_code).upper() in _load_limited_countries()


# ── Prompt rules injection ───────────────────────────────────────────


_US_SAFE_RULES: str = (
    "US_SAFE MODE — REGULATORY COMPLIANCE (violating any of these rules "
    "will cause the email to be REJECTED before sending):\n"
    "1. Mention ONLY these MAXIA surfaces: AI-to-AI service marketplace, "
    "MCP tools, GPU rental (Akash Network), free tier API (100 req/day), "
    "wallet analysis READ-ONLY, DeFi yield READ-ONLY, enterprise SSO, "
    "Prometheus metrics, the 17 native AI services catalog.\n"
    "2. NEVER mention any of these words or phrases, even in passing, "
    "even as comparison, even with a disclaimer: token swap, swapping, "
    "tokenized stocks, xStock, stock trading, fractional shares, "
    "cross-chain bridge, bridging, LI.FI, lightning payments, ln.bot, "
    "escrow trading, custodial trading, buy/sell crypto, \"trade\" any "
    "token, pay-per-call in anything other than USDC.\n"
    "3. NEVER promise regulatory approval, SEC registration, or "
    "money-transmitter licensing — MAXIA holds none of these.\n"
    "4. NEVER quote a competitor's price/fee/percentage (Jupiter, 0x, "
    "AWS, Bedrock, Together, OpenAI, Anthropic, Coinbase, Binance, ...).\n"
    "5. Keep it to developer / infrastructure framing: APIs, SDKs, "
    "free tier, MCP integration, GPU cost savings vs AWS.\n"
    "6. No hype words: revolutionary, game-changing, moon, 100x, "
    "guaranteed, disruptive.\n"
    "7. Professional, concise, 150 words max unless the prospect asked "
    "for a complete list.\n"
)


def us_safe_prompt_rules() -> str:
    """Return the HARD RULES block to prepend/append to an outreach prompt."""
    return _US_SAFE_RULES


# ── Forbidden keyword taxonomy (sentence-level) ──────────────────────
#
# Two-category approach, rebuilt 2026-04-12 after a whack-a-mole
# regex session showed that a single mega-pattern either over-matches
# (eats "swap" in "swapfile") or under-matches ("Bridge your assets
# across chains" slipped through the old `bridge\s+(asset|token|...)`
# because "your" sat between "bridge" and "asset").
#
# Category 1 — ALWAYS FORBIDDEN: the keyword is univocal in a cold
#   email context. No semantic ambiguity. Any match → scrub sentence.
#
# Category 2 — CONTEXTUAL: the keyword has legitimate uses (e.g.
#   "escrow" is fine on its own — MAXIA's Solana escrow is a normal
#   marketplace feature — but "escrow trading" is the forbidden
#   coupling). We require the keyword AND a context word to appear in
#   the SAME sentence, regardless of order or distance within that
#   sentence.
#
# ``_sentence_has_forbidden`` is the SINGLE source of truth for both
# ``scrub_us_forbidden`` and ``validate_us_safe`` — they cannot drift.

_ALWAYS_FORBIDDEN: tuple[re.Pattern[str], ...] = (
    # swap (univocal in outreach — no legit use)
    re.compile(r"\bswap(s|ping|ped)?\b", re.IGNORECASE),
    # tokenized stocks and siblings
    re.compile(r"\btokeni[sz]ed\s+stock(s)?\b", re.IGNORECASE),
    re.compile(r"\bxStock\w*", re.IGNORECASE),
    re.compile(r"\bstock\s+trad(e|es|ing|er)\b", re.IGNORECASE),
    re.compile(r"\bfractional\s+share(s)?\b", re.IGNORECASE),
    # Named bridges / LN services
    re.compile(r"\bLI\.FI\b", re.IGNORECASE),
    re.compile(r"\bln\.bot\b", re.IGNORECASE),
    # Custodial (any use — MAXIA is non-custodial by design, so the
    # word should never appear)
    re.compile(r"\bcustodial\b", re.IGNORECASE),
    # Pay-per-call in any currency (free tier uses USDC only, the
    # phrasing implies fiat or crypto-other-than-USDC)
    re.compile(r"\bpay[\s-]?per[\s-]?call\b", re.IGNORECASE),
)

# Contextual: (keyword_pattern, [context_patterns]) — ALL must match
# somewhere in the SAME sentence for the sentence to be forbidden.
_CONTEXTUAL_FORBIDDEN: tuple[tuple[re.Pattern[str], tuple[re.Pattern[str], ...]], ...] = (
    # bridge + crypto-asset context
    (
        re.compile(r"\bbridg(e|es|ed|ing)\b", re.IGNORECASE),
        (
            re.compile(
                r"\b(assets?|tokens?|chains?|cryptos?|funds?|"
                r"across|between|usdc|btc|eth|sol|l1|l2)\b",
                re.IGNORECASE,
            ),
        ),
    ),
    # lightning + payment context (noun OR verb)
    (
        re.compile(r"\blightning\b", re.IGNORECASE),
        (
            re.compile(
                r"\b(pay|paid|paying|payments?|networks?|channels?|"
                r"invoices?|bitcoin|btc|ln|nodes?|sats?|msats?)\b",
                re.IGNORECASE,
            ),
        ),
    ),
    # escrow + trading context ("escrow" alone OK, "escrow trading" NOT)
    (
        re.compile(r"\bescrow\b", re.IGNORECASE),
        (
            re.compile(
                r"\b(trad(e|es|ing|er|ers)|swap(s|ping)?|"
                r"pools?|books?|liquidity)\b",
                re.IGNORECASE,
            ),
        ),
    ),
)


def _sentence_has_forbidden(sentence: str) -> tuple[bool, str]:
    """Return ``(is_forbidden, first_match_snippet)``.

    Single source of truth — used by both ``scrub_us_forbidden`` and
    ``validate_us_safe`` so the scrub and the post-validation cannot
    disagree on what counts as a violation.
    """
    if not sentence:
        return False, ""
    # Pass 1: always-forbidden keywords
    for pat in _ALWAYS_FORBIDDEN:
        m = pat.search(sentence)
        if m:
            return True, m.group(0)
    # Pass 2: contextual (keyword + context word in same sentence)
    for kw_pat, ctx_pats in _CONTEXTUAL_FORBIDDEN:
        kw_m = kw_pat.search(sentence)
        if not kw_m:
            continue
        for ctx_pat in ctx_pats:
            ctx_m = ctx_pat.search(sentence)
            if ctx_m:
                return True, f"{kw_m.group(0)}+{ctx_m.group(0)}"
    return False, ""


def _split_into_sentences(text: str) -> list[tuple[str, bool]]:
    """Split text into (sentence, is_paragraph_break) tuples.

    Paragraph breaks are preserved so the scrubbed output keeps the
    original newline structure. Each non-break entry is one sentence.
    """
    out: list[tuple[str, bool]] = []
    for paragraph in text.split("\n"):
        if not paragraph.strip():
            out.append((paragraph, True))
            continue
        # Split on sentence boundary (., !, ? followed by whitespace)
        sentences = re.split(r"(?<=[.!?])\s+", paragraph)
        for sent in sentences:
            if sent:
                out.append((sent, False))
        out.append(("", True))  # paragraph terminator
    return out


def scrub_us_forbidden(text: str) -> tuple[str, int]:
    """Strip any sentence that violates the US_SAFE rules.

    Returns ``(cleaned_text, hit_count)``. When ``hit_count > 0`` the
    caller should log — it means the LLM ignored the US_SAFE rules and
    the scrub had to clean up after it.
    """
    if not isinstance(text, str) or not text:
        return text, 0

    hits = 0
    current_para_sentences: list[str] = []
    paragraphs: list[str] = []

    for sent, is_break in _split_into_sentences(text):
        if is_break:
            if current_para_sentences:
                paragraphs.append(" ".join(current_para_sentences).strip())
                current_para_sentences = []
            # Empty paragraph breaks are collapsed at the end
            continue
        forbidden, _ = _sentence_has_forbidden(sent)
        if forbidden:
            hits += 1
            continue
        current_para_sentences.append(sent)
    # Tail
    if current_para_sentences:
        paragraphs.append(" ".join(current_para_sentences).strip())

    cleaned = "\n\n".join(p for p in paragraphs if p)
    if hits:
        log.info("[us_safe] scrubbed %d forbidden sentence(s)", hits)
    return cleaned, hits


# ── Validation helper ────────────────────────────────────────────────


def validate_us_safe(text: str) -> dict:
    """Return a diagnostic dict describing US_SAFE violations in text.

    Uses the SAME ``_sentence_has_forbidden`` predicate as
    ``scrub_us_forbidden`` — so if a sentence passes ``scrub``, it
    will also pass ``validate``. No divergence possible.
    """
    if not isinstance(text, str):
        return {"ok": False, "violations": 0, "matches": [], "reason": "non-string input"}

    hits = 0
    matches: list[str] = []
    for sent, is_break in _split_into_sentences(text):
        if is_break:
            continue
        forbidden, snippet = _sentence_has_forbidden(sent)
        if forbidden:
            hits += 1
            matches.append(snippet)
            if hits >= 10:
                break
    return {
        "ok": hits == 0,
        "violations": hits,
        "matches": matches,
    }


# ── Self-test ────────────────────────────────────────────────────────

_TEST_CASES: tuple[tuple[str, int], ...] = (
    # ── Always-forbidden (univocal) ──
    ("We offer token swap on 7 chains.", 1),
    ("Swapping tokens across chains is easy.", 1),
    ("MAXIA swaps your USDC automatically.", 1),
    ("Trade tokenized stocks like AAPLx.", 1),
    ("Buy xStocks fractional shares.", 1),
    ("Our xStockX platform rocks.", 1),
    # Multi-pattern sentences still count as 1 scrub (whole-sentence drop)
    ("Fractional shares of Tesla via xStocks.", 1),  # fractional + xStock → 1 drop
    ("Cross-chain bridge via LI.FI.", 1),  # LI.FI + bridge context → 1 drop
    ("Use ln.bot for lightning invoices.", 1),  # ln.bot + lightning ctx → 1 drop
    ("All custodial wallets are heavy.", 1),
    ("Pay-per-call in BTC or SOL.", 1),
    ("Stock trading is simple.", 1),
    # ── Contextual: bridge needs context ──
    ("Bridge your assets across L1s.", 1),  # THE KEY CASE
    ("Bridge between Solana and Ethereum.", 1),
    ("Bridge tokens in one click.", 1),
    ("Bridging USDC and ETH is a feature.", 1),
    # ── Contextual: lightning needs context ──
    ("Pay with lightning instantly.", 1),  # lightning + payments
    ("Lightning network channels are fast.", 1),
    ("The lightning invoice expired.", 1),
    # ── Contextual: escrow + trading ──
    ("Escrow trading pool on Solana.", 1),
    ("Escrow liquidity is deep.", 1),
    # ── Clean cases (MUST pass through) ──
    ("MCP tools make AI integration easy.", 0),
    ("GPU rental via Akash costs $0.46 per hour.", 0),
    ("Free tier API gives 100 requests per day.", 0),
    ("AI-to-AI service marketplace on 15 blockchains.", 0),
    ("Wallet analysis is read-only for US users.", 0),
    ("DeFi yield read-only for enterprise SSO.", 0),
    ("On-chain escrow protects both parties.", 0),  # escrow alone = OK
    ("Lightning fast inference via local GPU.", 0),  # lightning no payment ctx
    # ── Compound ──
    ("Swap your tokens. MCP tools are cool.", 1),  # only first scrubbed
    ("Swap assets. Bridge between chains.", 2),  # both scrubbed
    ("MCP tools work. Then swap. GPU is cheap.", 1),  # middle scrubbed
)


def run_self_test(verbose: bool = False) -> dict:
    """Run the full test matrix against ``_sentence_has_forbidden``.

    Returns ``{"passed": int, "failed": list[dict], "total": int}``.
    When called as a script, prints the summary and exits non-zero
    if any case failed.
    """
    passed = 0
    failed: list[dict] = []
    for text, expected in _TEST_CASES:
        _, actual = scrub_us_forbidden(text)
        if actual == expected:
            passed += 1
            if verbose:
                print(f"  OK    {actual}={expected} | {text!r}")
        else:
            failed.append({
                "text": text,
                "expected": expected,
                "actual": actual,
            })
            print(f"  FAIL  {actual}!={expected} | {text!r}")
    return {"passed": passed, "failed": failed, "total": len(_TEST_CASES)}


if __name__ == "__main__":
    import sys as _sys
    result = run_self_test(verbose="--verbose" in _sys.argv)
    print(f"\n{result['passed']}/{result['total']} passed")
    if result["failed"]:
        print(f"FAILURES ({len(result['failed'])}):")
        for f in result["failed"]:
            print(f"  expected={f['expected']} actual={f['actual']} text={f['text']!r}")
        _sys.exit(1)
    print("ALL PASSED")
