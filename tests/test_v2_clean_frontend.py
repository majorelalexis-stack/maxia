"""TDD — v2-clean frontend: verify dead API calls and broken pages are removed.

RED phase (before fixes):
  - test_app_html_no_active_swap_page         FAILS (page-swap exists)
  - test_app_html_no_active_stocks_page        FAILS (page-stocks exists)
  - test_app_html_no_active_yields_page        FAILS (page-yields exists)
  - test_app_features_js_no_defi_yield_call    FAILS (/api/public/defi/best-yield)
  - test_app_trading_js_no_dca_api_call        FAILS (/api/dca/pending/)
  - test_app_init_js_no_stocks_call            FAILS (/api/public/stocks)
  - test_landing_no_swap_sidebar_link          FAILS (#swap sidebar link)
  - test_landing_no_stocks_sidebar_link        FAILS (#stocks sidebar link)

GREEN phase (after fixes): all pass.
"""
import os
import re

import pytest

FRONTEND = os.path.join(os.path.dirname(__file__), "..", "frontend")
JS = os.path.join(FRONTEND, "static", "js")


def _read(rel_path: str) -> str:
    with open(os.path.join(FRONTEND, rel_path), encoding="utf-8") as f:
        return f.read()


def _read_js(filename: str) -> str:
    with open(os.path.join(JS, filename), encoding="utf-8") as f:
        return f.read()


# ── app.html: removed active pages ───────────────────────────────────────────

def test_app_html_no_active_swap_page():
    content = _read("app.html")
    assert 'id="page-swap"' not in content, (
        'app.html still has <div id="page-swap"> — swap page must be removed in v2'
    )


def test_app_html_no_active_stocks_page():
    content = _read("app.html")
    assert 'id="page-stocks"' not in content, (
        'app.html still has <div id="page-stocks"> — stocks page must be removed in v2'
    )


def test_app_html_no_active_yields_page():
    content = _read("app.html")
    assert 'id="page-yields"' not in content, (
        'app.html still has <div id="page-yields"> — defi yields page must be removed in v2'
    )


def test_app_html_no_showpage_swap():
    content = _read("app.html")
    assert "showPage('swap')" not in content and 'showPage("swap")' not in content, (
        "app.html nav still calls showPage('swap')"
    )


def test_app_html_no_showpage_stocks():
    content = _read("app.html")
    assert "showPage('stocks')" not in content and 'showPage("stocks")' not in content, (
        "app.html nav still calls showPage('stocks')"
    )


def test_app_html_no_showpage_yields():
    content = _read("app.html")
    assert "showPage('yields')" not in content and 'showPage("yields")' not in content, (
        "app.html nav still calls showPage('yields')"
    )


# ── app-features.js: removed API calls ───────────────────────────────────────

def test_app_features_js_no_defi_yield_call():
    content = _read_js("app-features.js")
    assert "/api/public/defi/best-yield" not in content, (
        "app-features.js still calls /api/public/defi/best-yield (route deleted in v2)"
    )


def test_app_features_js_no_stocks_call():
    content = _read_js("app-features.js")
    assert "/api/public/stocks" not in content, (
        "app-features.js still calls /api/public/stocks (route deleted in v2)"
    )


# ── app-trading.js: removed API calls ────────────────────────────────────────

def test_app_trading_js_no_dca_api_call():
    content = _read_js("app-trading.js")
    assert "/api/dca/pending/" not in content, (
        "app-trading.js still calls /api/dca/pending/ (route deleted in v2)"
    )


def test_app_trading_js_no_grid_api_call():
    content = _read_js("app-trading.js")
    assert "/api/grid/pending/" not in content, (
        "app-trading.js still calls /api/grid/pending/ (route deleted in v2)"
    )


def test_app_trading_js_no_sniper_pending_call():
    content = _read_js("app-trading.js")
    assert "/api/sniper/pending" not in content, (
        "app-trading.js still calls /api/sniper/pending (route deleted in v2)"
    )


# ── app-init.js: removed API calls ───────────────────────────────────────────

def test_app_init_js_no_stocks_call():
    content = _read_js("app-init.js")
    assert "/api/public/stocks" not in content, (
        "app-init.js still calls /api/public/stocks (route deleted in v2)"
    )


# ── landing.html: sidebar nav links ──────────────────────────────────────────

def test_landing_no_swap_sidebar_link():
    content = _read("landing.html")
    # sidebar links specifically (not just any mention)
    sidebar = re.search(r'class="sidebar".*?</nav>', content, re.DOTALL)
    sidebar_text = sidebar.group(0) if sidebar else content
    assert '/app#swap' not in sidebar_text, (
        "landing.html sidebar still links to /app#swap"
    )


def test_landing_no_stocks_sidebar_link():
    content = _read("landing.html")
    sidebar = re.search(r'class="sidebar".*?</nav>', content, re.DOTALL)
    sidebar_text = sidebar.group(0) if sidebar else content
    assert '/app#stocks' not in sidebar_text, (
        "landing.html sidebar still links to /app#stocks"
    )


def test_landing_no_yields_sidebar_link():
    content = _read("landing.html")
    sidebar = re.search(r'class="sidebar".*?</nav>', content, re.DOTALL)
    sidebar_text = sidebar.group(0) if sidebar else content
    assert '/app#yields' not in sidebar_text, (
        "landing.html sidebar still links to /app#yields"
    )


# ── terms.html: US eligibility block must be gone ───────────────────────────

def test_terms_no_us_eligibility_block():
    content = _read("terms.html")
    assert "resident or citizen of the United States" not in content, (
        "terms.html still blocks US residents — US is open in v2"
    )


# ── terms.html: financial disclaimer must exist ──────────────────────────────

def test_terms_has_not_financial_advice_disclaimer():
    content = _read("terms.html")
    assert "Not financial advice" in content or "not a financial" in content.lower(), (
        "terms.html missing 'Not financial advice' disclaimer required for v2"
    )
