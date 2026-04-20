"""TDD — v2-clean: all pages clean, no dead API calls, no regulated sidebar links.

RED phase covers: sidebar (37 files), buy.html fiat API, miniapp.html dead calls,
sniper.html sniper API, pricing.html stocks API, index.html stocks API.
"""
import os
import re

import pytest

FRONTEND = os.path.join(os.path.dirname(__file__), "..", "frontend")
HTML_FILES = [
    f for f in os.listdir(FRONTEND)
    if f.endswith(".html") and os.path.isfile(os.path.join(FRONTEND, f))
]


def _read(rel_path: str) -> str:
    with open(os.path.join(FRONTEND, rel_path), encoding="utf-8") as f:
        return f.read()


# ── Sidebar: ALL HTML files must not have Trade section with removed links ────

SIDEBAR_BANNED = [
    '<a href="/app#swap" class="sidebar-link">',
    '<a href="/app#stocks" class="sidebar-link">',
    '<a href="/app#yields" class="sidebar-link">',
    '<a href="/sniper" class="sidebar-link">',
    # Trade section header (replaced by AI Services)
    '<div class="sidebar-section">Trade</div>',
]


@pytest.mark.parametrize("banned_str", SIDEBAR_BANNED)
def test_no_html_file_has_banned_sidebar_link(banned_str):
    """Every HTML file must have no Trade sidebar with removed features."""
    offenders = [
        f for f in HTML_FILES
        if banned_str in _read(f)
    ]
    assert not offenders, (
        f"Banned sidebar string '{banned_str}' still found in: {offenders}"
    )


# ── buy.html: fiat API calls must be stubbed ─────────────────────────────────

def test_buy_html_no_fiat_providers_call():
    content = _read("buy.html")
    assert "/api/fiat/providers" not in content, (
        "buy.html still calls /api/fiat/providers (route deleted in v2)"
    )


def test_buy_html_no_fiat_onramp_call():
    content = _read("buy.html")
    assert "/api/fiat/onramp" not in content, (
        "buy.html still calls /api/fiat/onramp (route deleted in v2)"
    )


# ── miniapp.html: all dead API calls must be gone ────────────────────────────

def test_miniapp_no_defi_yield_call():
    content = _read("miniapp.html")
    assert "/api/public/defi/best-yield" not in content, (
        "miniapp.html still calls /api/public/defi/best-yield"
    )


def test_miniapp_no_sniper_call():
    content = _read("miniapp.html")
    assert "/api/sniper/new-tokens" not in content, (
        "miniapp.html still calls /api/sniper/new-tokens"
    )


def test_miniapp_no_fiat_call():
    content = _read("miniapp.html")
    assert "/api/fiat/onramp" not in content, (
        "miniapp.html still calls /api/fiat/onramp"
    )


def test_miniapp_no_swap_quote_call():
    content = _read("miniapp.html")
    assert "/api/public/crypto/quote" not in content, (
        "miniapp.html still calls /api/public/crypto/quote (swap removed)"
    )


# ── sniper.html: sniper API calls must be gone ────────────────────────────────

def test_sniper_html_no_sniper_stats():
    content = _read("sniper.html")
    assert "/api/sniper/stats" not in content, (
        "sniper.html still calls /api/sniper/stats (sniper removed in v2)"
    )


def test_sniper_html_no_sniper_tokens():
    content = _read("sniper.html")
    assert "/api/sniper/new-tokens" not in content, (
        "sniper.html still calls /api/sniper/new-tokens (sniper removed in v2)"
    )


# ── pricing.html: stocks API call must be gone ────────────────────────────────

def test_pricing_html_no_stocks_call():
    content = _read("pricing.html")
    assert "/api/public/stocks" not in content, (
        "pricing.html still calls /api/public/stocks"
    )


# ── index.html: stocks API calls must be gone ─────────────────────────────────

def test_index_html_no_stocks_call():
    content = _read("index.html")
    assert "/api/public/stocks" not in content, (
        "index.html still calls /api/public/stocks"
    )


def test_index_html_no_stock_stats_call():
    content = _read("index.html")
    assert "/api/stocks/stats" not in content, (
        "index.html still calls /api/stocks/stats (route deleted in v2)"
    )


# ── docs.html: removed endpoint sections must be gone ────────────────────────

def test_docs_html_no_defi_yield_section():
    content = _read("docs.html")
    assert "/api/public/defi/best-yield" not in content, (
        "docs.html still documents /api/public/defi/best-yield"
    )


def test_docs_html_no_swap_section():
    content = _read("docs.html")
    assert "/api/public/crypto/swap" not in content, (
        "docs.html still documents /api/public/crypto/swap"
    )


def test_docs_html_no_stocks_section():
    content = _read("docs.html")
    assert "/api/public/stocks" not in content, (
        "docs.html still documents /api/public/stocks"
    )
