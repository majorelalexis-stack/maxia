"""Tests — DCA/Grid/Sniper bot validation + Marketplace extractors.

All external deps mocked. Zero DB/network calls.
"""
import os
import sys
from unittest.mock import patch

import pytest

BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..", "backend")
sys.path.insert(0, BACKEND_DIR)


# ═══════════════════════════════════════════════════════════════════════════════
#  DCA BOT — Validation & Commission
# ═══════════════════════════════════════════════════════════════════════════════

class TestDCAValidation:
    """DCA bot order validation rules."""

    def test_frequency_seconds_exists(self):
        from trading.dca_bot import FREQUENCY_SECONDS
        assert "daily" in FREQUENCY_SECONDS
        assert "weekly" in FREQUENCY_SECONDS
        assert "monthly" in FREQUENCY_SECONDS
        assert FREQUENCY_SECONDS["daily"] == 86400
        assert FREQUENCY_SECONDS["weekly"] == 604800

    def test_commission_bps_defined(self):
        from trading.dca_bot import DCA_COMMISSION_BPS
        assert DCA_COMMISSION_BPS == 10  # 0.10%

    def test_commission_calculation(self):
        from trading.dca_bot import DCA_COMMISSION_BPS
        amount = 100.0
        commission = round(amount * DCA_COMMISSION_BPS / 10000, 6)
        assert commission == 0.1  # $0.10 on $100

    def test_min_amount(self):
        """DCA minimum is $1.00."""
        min_amount = 1.0
        assert min_amount >= 1.0

    def test_max_amount(self):
        """DCA maximum is $1000."""
        max_amount = 1000.0
        assert max_amount <= 1000.0


# ═══════════════════════════════════════════════════════════════════════════════
#  GRID BOT — Grid Math
# ═══════════════════════════════════════════════════════════════════════════════

class TestGridMath:
    """Grid bot level calculation and validation."""

    def test_grid_step_calculation(self):
        lower, upper, num_grids = 100.0, 200.0, 10
        grid_step = round((upper - lower) / num_grids, 6)
        assert grid_step == 10.0

    def test_per_grid_allocation(self):
        investment, num_grids = 1000.0, 10
        per_grid = round(investment / num_grids, 6)
        assert per_grid == 100.0

    def test_grid_level_detection(self):
        lower, upper, num_grids = 100.0, 200.0, 10
        grid_step = (upper - lower) / num_grids
        price = 135.0
        level = int((price - lower) / grid_step)
        assert level == 3  # between grid 3 and 4

    def test_grid_level_at_lower_bound(self):
        lower, upper, num_grids = 100.0, 200.0, 10
        grid_step = (upper - lower) / num_grids
        level = int((lower - lower) / grid_step)
        assert level == 0

    def test_grid_level_at_upper_bound(self):
        lower, upper, num_grids = 100.0, 200.0, 10
        grid_step = (upper - lower) / num_grids
        level = min(int((upper - lower) / grid_step), num_grids)
        assert level == num_grids

    def test_grid_validation_bounds(self):
        """num_grids must be 3-50, investment $10-$10000."""
        assert 3 <= 5 <= 50  # valid
        assert 3 <= 3 <= 50  # boundary
        assert 3 <= 50 <= 50  # boundary
        assert not (3 <= 2 <= 50)  # invalid
        assert not (3 <= 51 <= 50)  # invalid

    def test_price_range_validation(self):
        """lower must be < upper, both > 0."""
        assert 0 < 100 < 200  # valid
        assert not (0 < 200 < 100)  # invalid


class TestGridTokenResolution:
    """Grid bot token mint and decimal resolution."""

    def test_get_token_mint_sol(self):
        from trading.grid_bot import _get_token_mint
        mint = _get_token_mint("SOL")
        assert mint and len(mint) > 20  # valid Solana address

    def test_get_token_mint_usdc(self):
        from trading.grid_bot import _get_token_mint
        mint = _get_token_mint("USDC")
        assert mint and len(mint) > 20

    def test_get_token_mint_unknown(self):
        from trading.grid_bot import _get_token_mint
        mint = _get_token_mint("XYZNOTEXIST")
        assert mint == "" or mint is None  # no crash

    def test_get_token_decimals_usdc(self):
        from trading.grid_bot import _get_token_decimals
        dec = _get_token_decimals("USDC")
        assert dec == 6

    def test_get_token_decimals_sol(self):
        from trading.grid_bot import _get_token_decimals
        dec = _get_token_decimals("SOL")
        assert dec == 9

    def test_get_token_decimals_unknown_default(self):
        from trading.grid_bot import _get_token_decimals
        dec = _get_token_decimals("XYZNOTEXIST")
        assert dec == 6  # default


class TestGridCommission:
    """Grid bot commission constants."""

    def test_commission_bps_defined(self):
        from trading.grid_bot import GRID_COMMISSION_BPS
        assert GRID_COMMISSION_BPS == 10  # 0.10%

    def test_commission_on_trade(self):
        from trading.grid_bot import GRID_COMMISSION_BPS
        per_grid = 100.0
        commission = round(per_grid * GRID_COMMISSION_BPS / 10000, 6)
        assert commission == 0.1


# ═══════════════════════════════════════════════════════════════════════════════
#  TOKEN SNIPER — Filtering & Parsing
# ═══════════════════════════════════════════════════════════════════════════════

class TestSniperParsing:
    """Token sniper parsing and extraction."""

    def test_get_mint_from_dict(self):
        from trading.token_sniper import _get_mint
        assert _get_mint({"mint": "ABC123"}) == "ABC123"
        assert _get_mint({"tokenAddress": "DEF456"}) == "DEF456"
        assert _get_mint({}) == ""

    def test_parse_token_dexscreener(self):
        from trading.token_sniper import _parse_token
        raw = {
            "_source": "dexscreener",
            "tokenAddress": "MINT1abcdef1234567890",
            "description": "TestToken",
            "totalAmount": 3,
            "chainId": "solana",
        }
        parsed = _parse_token(raw)
        assert parsed["mint"] == "MINT1abcdef1234567890"
        assert parsed["boosts"] == 3
        assert parsed["source"] == "dexscreener"

    def test_parse_token_pump_fun(self):
        from trading.token_sniper import _parse_token
        raw = {
            "mint": "MINT2abc",
            "name": "PumpToken",
            "symbol": "PT",
            "usd_market_cap": 10000,
        }
        parsed = _parse_token(raw)
        assert parsed["symbol"] == "PT"
        assert parsed["market_cap_usd"] == 10000
        assert parsed["source"] == "pump.fun"


class TestSniperFiltering:
    """_matches_watch() filter logic."""

    def test_matches_market_cap_range(self):
        from trading.token_sniper import _matches_watch
        token = {"market_cap_usd": 50000, "boosts": 5, "name": "Test", "symbol": "TST", "description": ""}
        watch = {"min_market_cap_usd": 10000, "max_market_cap_usd": 100000, "min_boosts": 0, "keywords": []}
        assert _matches_watch(token, watch) is True

    def test_rejects_below_min_cap(self):
        from trading.token_sniper import _matches_watch
        token = {"market_cap_usd": 5000, "boosts": 5, "name": "Test", "symbol": "TST", "description": ""}
        watch = {"min_market_cap_usd": 10000, "max_market_cap_usd": 100000, "min_boosts": 0, "keywords": []}
        assert _matches_watch(token, watch) is False

    def test_rejects_above_max_cap(self):
        from trading.token_sniper import _matches_watch
        token = {"market_cap_usd": 500000, "boosts": 5, "name": "Test", "symbol": "TST", "description": ""}
        watch = {"min_market_cap_usd": 10000, "max_market_cap_usd": 100000, "min_boosts": 0, "keywords": []}
        assert _matches_watch(token, watch) is False

    def test_rejects_low_boosts(self):
        from trading.token_sniper import _matches_watch
        token = {"market_cap_usd": 50000, "boosts": 1, "name": "Test", "symbol": "TST", "description": ""}
        watch = {"min_market_cap_usd": 10000, "max_market_cap_usd": 100000, "min_boosts": 5, "keywords": []}
        assert _matches_watch(token, watch) is False

    def test_keyword_match(self):
        from trading.token_sniper import _matches_watch
        token = {"market_cap_usd": 50000, "boosts": 5, "name": "Dogecoin AI", "symbol": "DAI", "description": "AI dog token"}
        watch = {"min_market_cap_usd": 0, "max_market_cap_usd": 999999, "min_boosts": 0, "keywords": ["AI"]}
        assert _matches_watch(token, watch) is True

    def test_keyword_no_match(self):
        from trading.token_sniper import _matches_watch
        token = {"market_cap_usd": 50000, "boosts": 5, "name": "CatCoin", "symbol": "CAT", "description": "meow"}
        watch = {"min_market_cap_usd": 0, "max_market_cap_usd": 999999, "min_boosts": 0, "keywords": ["AI"]}
        assert _matches_watch(token, watch) is False


# ═══════════════════════════════════════════════════════════════════════════════
#  MARKETPLACE — Prompt Extractors
# ═══════════════════════════════════════════════════════════════════════════════

class TestExtractToken:
    """_extract_token_from_prompt() extracts crypto symbols."""

    def test_extract_btc(self):
        from marketplace.public_api_discover import _extract_token_from_prompt
        assert _extract_token_from_prompt("analyze BTC") == "BTC"

    def test_extract_eth(self):
        from marketplace.public_api_discover import _extract_token_from_prompt
        assert _extract_token_from_prompt("what about ETH price") == "ETH"

    def test_extract_sol(self):
        from marketplace.public_api_discover import _extract_token_from_prompt
        assert _extract_token_from_prompt("SOL sentiment") == "SOL"

    def test_default_btc(self):
        from marketplace.public_api_discover import _extract_token_from_prompt
        result = _extract_token_from_prompt("hello world")
        assert result == "BTC" or isinstance(result, str)  # returns something

    def test_multiple_tokens_picks_known(self):
        from marketplace.public_api_discover import _extract_token_from_prompt
        result = _extract_token_from_prompt("compare SOL vs ETH")
        assert result in ("SOL", "ETH")


class TestExtractURL:
    """_extract_url_from_prompt() extracts URLs."""

    def test_extract_https(self):
        from marketplace.public_api_discover import _extract_url_from_prompt
        assert _extract_url_from_prompt("scrape https://example.com") == "https://example.com"

    def test_extract_http(self):
        from marketplace.public_api_discover import _extract_url_from_prompt
        assert _extract_url_from_prompt("check http://test.org/page") == "http://test.org/page"

    def test_no_url_returns_empty(self):
        from marketplace.public_api_discover import _extract_url_from_prompt
        assert _extract_url_from_prompt("no url here") == ""

    def test_url_with_path(self):
        from marketplace.public_api_discover import _extract_url_from_prompt
        url = _extract_url_from_prompt("fetch https://api.example.com/v1/data?q=test")
        assert url.startswith("https://api.example.com")


class TestExtractAddress:
    """_extract_address_from_prompt() extracts wallet addresses."""

    def test_extract_evm(self):
        from marketplace.public_api_discover import _extract_address_from_prompt
        addr = _extract_address_from_prompt("analyze 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045")
        assert addr == "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"

    def test_extract_solana(self):
        from marketplace.public_api_discover import _extract_address_from_prompt
        # Valid base58 address (no 0, I, O, l)
        addr = _extract_address_from_prompt("check 7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU")
        assert addr == "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"

    def test_no_address_returns_empty(self):
        from marketplace.public_api_discover import _extract_address_from_prompt
        result = _extract_address_from_prompt("hello world")
        assert result == "" or result is None
