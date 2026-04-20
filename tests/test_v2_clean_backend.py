"""TDD — v2-clean backend: verify all regulated modules are gone.

RED phase: these tests PASS immediately (files already deleted in P1).
They serve as regression guards — any re-introduction of deleted files
will fail CI.
"""
import os
import re

import pytest

BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
MAIN_PY = os.path.join(BACKEND, "main.py")
FRONTEND = os.path.join(os.path.dirname(__file__), "..", "frontend")


# ── 1. Deleted files must not exist ──────────────────────────────────────────

DELETED_FILES = [
    "trading/tokenized_stocks.py",
    "trading/crypto_swap.py",
    "trading/defi_scanner.py",
    "trading/solana_defi.py",
    "trading/evm_swap.py",
    "trading/perps_client.py",
    "trading/token_sniper.py",
    "trading/yield_aggregator.py",
    "trading/dca_bot.py",
    "trading/grid_bot.py",
    "trading/trading_features.py",
    "trading/trading_tools.py",
    "integrations/x402_middleware.py",
    "integrations/l402_middleware.py",
    "integrations/fiat_onramp.py",
    "blockchain/lightning_api.py",
    "marketplace/public_api_trading.py",
    "features/auto_compound.py",
    "core/geo_blocking.py",
]


@pytest.mark.parametrize("rel_path", DELETED_FILES)
def test_deleted_file_absent(rel_path):
    assert not os.path.exists(os.path.join(BACKEND, rel_path)), (
        f"Regulated file was re-introduced: backend/{rel_path}"
    )


# ── 2. main.py must not import removed modules ────────────────────────────────

BANNED_IMPORTS = [
    "tokenized_stocks",
    "crypto_swap",
    "defi_scanner",
    "solana_defi",
    "evm_swap",
    "perps_client",
    "token_sniper",
    "yield_aggregator",
    "dca_bot",
    "grid_bot",
    "trading_features",
    "trading_tools",
    "x402_middleware",
    "l402_middleware",
    "fiat_onramp",
    "lightning_api",
    "public_api_trading",
    "auto_compound",
    "geo_blocking",
    "geo_block_middleware",
]


@pytest.mark.parametrize("symbol", BANNED_IMPORTS)
def test_main_does_not_import_removed_symbol(symbol):
    with open(MAIN_PY, encoding="utf-8") as f:
        content = f.read()
    # Ignore comment lines
    active_lines = "\n".join(
        ln for ln in content.splitlines() if not ln.strip().startswith("#")
    )
    assert symbol not in active_lines, (
        f"main.py still references removed symbol: {symbol}"
    )


# ── 3. US must not be in country_filter hard/legacy_blocked sets ─────────────

def test_us_not_in_country_filter_legacy_blocked():
    path = os.path.join(BACKEND, "compliance", "country_filter.py")
    with open(path, encoding="utf-8") as f:
        content = f.read()
    # Find legacy_blocked set definition
    match = re.search(r"legacy_blocked\s*=\s*\{([^}]+)\}", content, re.DOTALL)
    assert match, "legacy_blocked set not found in country_filter.py"
    blocked_body = match.group(1)
    assert '"US"' not in blocked_body and "'US'" not in blocked_body, (
        "US is still hardcoded in legacy_blocked set — should be open in v2"
    )


# ── 4. country_tiers.yaml must not list US in license tier ───────────────────

def test_us_not_in_country_tiers_license():
    path = os.path.join(BACKEND, "compliance", "country_tiers.yaml")
    with open(path, encoding="utf-8") as f:
        content = f.read()
    # Find license: section (up to caution:)
    license_section = re.split(r"^caution:", content, maxsplit=1, flags=re.MULTILINE)[0]
    # US should only appear in comments, not as an active entry
    active_lines = [
        ln for ln in license_section.splitlines()
        if not ln.strip().startswith("#") and "US" in ln
    ]
    us_code_lines = [ln for ln in active_lines if re.search(r"code:\s*US\b", ln)]
    assert not us_code_lines, (
        f"US still listed as active code in license tier: {us_code_lines}"
    )


# ── 5. SDK must not expose removed method names ───────────────────────────────

SDK_CLIENT = os.path.join(
    os.path.dirname(__file__), "..", "maxia-sdk", "src", "maxia", "client.py"
)

REMOVED_SDK_METHODS = [
    "def tokens(",
    "def quote(",
    "def stocks(",
    "def stock_price(",
    "def defi_yield(",
    "def swap(",
    "def defi_lending(",
    "def defi_best_rate(",
    "def defi_staking(",
    "def defi_lend(",
    "def defi_stake(",
    "def dca_create(",
    "def dca_list(",
    "def dca_executions(",
    "def dca_cancel(",
    "def dca_stats(",
]


@pytest.mark.parametrize("method_sig", REMOVED_SDK_METHODS)
def test_sdk_method_removed(method_sig):
    with open(SDK_CLIENT, encoding="utf-8") as f:
        content = f.read()
    assert method_sig not in content, (
        f"Removed SDK method still present in client.py: {method_sig}"
    )


def test_sdk_version_is_v2():
    pyproject = os.path.join(
        os.path.dirname(__file__), "..", "maxia-sdk", "pyproject.toml"
    )
    with open(pyproject, encoding="utf-8") as f:
        content = f.read()
    assert 'version = "2.0.0"' in content, (
        "SDK pyproject.toml version should be 2.0.0"
    )
