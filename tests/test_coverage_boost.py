"""MAXIA Coverage Boost — ~40 tests for 5 large under-covered modules.

Modules targeted:
  1. backend/trading/evm_swap.py       — constants, pure functions, endpoints
  2. backend/trading/trading_tools.py   — constants, TA helpers, endpoints
  3. backend/trading/token_sniper.py    — model validation, pure functions, endpoints
  4. backend/trading/solana_defi.py     — protocol constants, endpoints
  5. backend/marketplace/public_api_discover.py — discover, demand, chain-support
"""

import os
import sys

# Env vars MUST be set before any backend import
os.environ.setdefault("JWT_SECRET", "ci-test-secret-key-32chars-minimum")
os.environ.setdefault("SANDBOX_MODE", "true")
os.environ.setdefault("ADMIN_KEY", "ci-admin-key-32chars-minimum-here")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

import httpx
from httpx import AsyncClient, ASGITransport
from main import app


# ══════════════════════════════════════════════════════════════════
#  Shared ASGI client fixture
# ══════════════════════════════════════════════════════════════════

@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


# ══════════════════════════════════════════════════════════════════
#  MODULE 1: evm_swap.py — constants + pure functions + endpoints
# ══════════════════════════════════════════════════════════════════

class TestEvmSwapConstants:
    """Test EVM_CHAINS dict and TOKENS_BY_CHAIN catalogue."""

    def test_evm_chains_has_all_six_chains(self):
        from trading.evm_swap import EVM_CHAINS
        expected = {"ethereum", "base", "polygon", "arbitrum", "avalanche", "bnb"}
        assert expected == set(EVM_CHAINS.keys())

    def test_evm_chains_have_chain_ids(self):
        from trading.evm_swap import EVM_CHAINS
        for chain, info in EVM_CHAINS.items():
            assert "chain_id" in info, f"{chain} missing chain_id"
            assert isinstance(info["chain_id"], int)
            assert "name" in info
            assert "native" in info

    def test_tokens_by_chain_keys_match_evm_chains(self):
        from trading.evm_swap import EVM_CHAINS, TOKENS_BY_CHAIN
        for chain in EVM_CHAINS:
            assert chain in TOKENS_BY_CHAIN, f"{chain} missing from TOKENS_BY_CHAIN"
            assert len(TOKENS_BY_CHAIN[chain]) >= 3, f"{chain} has too few tokens"

    def test_every_chain_has_usdc(self):
        from trading.evm_swap import TOKENS_BY_CHAIN
        for chain, tokens in TOKENS_BY_CHAIN.items():
            assert "USDC" in tokens, f"{chain} missing USDC"
            assert tokens["USDC"]["decimals"] == 6

    def test_tokens_have_required_fields(self):
        from trading.evm_swap import TOKENS_BY_CHAIN
        for chain, tokens in TOKENS_BY_CHAIN.items():
            for sym, info in tokens.items():
                assert "address" in info, f"{chain}/{sym} missing address"
                assert "name" in info, f"{chain}/{sym} missing name"
                assert "decimals" in info, f"{chain}/{sym} missing decimals"
                assert info["address"].startswith("0x"), f"{chain}/{sym} invalid address"


class TestEvmSwapCommissions:
    """Test get_swap_commission_bps and related tier functions."""

    def test_first_swap_free(self):
        from trading.evm_swap import get_swap_commission_bps
        assert get_swap_commission_bps(100, swap_count=0) == 0

    def test_bronze_tier(self):
        from trading.evm_swap import get_swap_commission_bps
        assert get_swap_commission_bps(100) == 10  # 0.10%

    def test_silver_tier(self):
        from trading.evm_swap import get_swap_commission_bps
        assert get_swap_commission_bps(600) == 5  # 0.05%

    def test_gold_tier(self):
        from trading.evm_swap import get_swap_commission_bps
        assert get_swap_commission_bps(6000) == 3  # 0.03%

    def test_tier_name_free(self):
        from trading.evm_swap import get_swap_tier_name
        assert get_swap_tier_name(100, swap_count=0) == "FREE"

    def test_tier_name_bronze(self):
        from trading.evm_swap import get_swap_tier_name
        assert get_swap_tier_name(100) == "BRONZE"

    def test_tier_info_returns_all_fields(self):
        from trading.evm_swap import get_swap_tier_info
        info = get_swap_tier_info(0)
        assert "current_tier" in info
        assert "current_bps" in info
        assert "current_pct" in info
        assert "volume_30d" in info
        assert "all_tiers" in info
        assert "BRONZE" in info["all_tiers"]


class TestEvmSwapEndpoints:
    """Test GET /api/swap/evm/chains and /tokens via ASGI."""

    @pytest.mark.asyncio
    async def test_list_chains(self, client):
        resp = await client.get("/api/swap/evm/chains")
        assert resp.status_code == 200
        data = resp.json()
        assert "chains" in data
        assert data["total_chains"] == 6
        assert data["aggregator"] == "0x Swap API v2"
        chain_keys = [c["chain"] for c in data["chains"]]
        assert "base" in chain_keys
        assert "ethereum" in chain_keys

    @pytest.mark.asyncio
    async def test_list_tokens_base(self, client):
        resp = await client.get("/api/swap/evm/tokens", params={"chain": "base"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["chain"] == "base"
        assert data["token_count"] >= 3
        symbols = [t["symbol"] for t in data["tokens"]]
        assert "USDC" in symbols

    @pytest.mark.asyncio
    async def test_list_tokens_invalid_chain(self, client):
        resp = await client.get("/api/swap/evm/tokens", params={"chain": "fakenet"})
        assert resp.status_code == 400


# ══════════════════════════════════════════════════════════════════
#  MODULE 2: trading_tools.py — constants, TA helpers, endpoints
# ══════════════════════════════════════════════════════════════════

class TestTradingToolsConstants:
    """Test constants and symbol mappings."""

    def test_supported_chains_list(self):
        from trading.trading_tools import SUPPORTED_CHAINS
        assert "solana" in SUPPORTED_CHAINS
        assert "base" in SUPPORTED_CHAINS
        assert "ethereum" in SUPPORTED_CHAINS
        assert len(SUPPORTED_CHAINS) >= 14

    def test_coingecko_id_mapping_has_sol(self):
        from trading.trading_tools import _SYM_TO_COINGECKO_ID
        assert _SYM_TO_COINGECKO_ID["SOL"] == "solana"
        assert _SYM_TO_COINGECKO_ID["BTC"] == "bitcoin"

    def test_coinpaprika_id_mapping_has_sol(self):
        from trading.trading_tools import _SYM_TO_COINPAPRIKA_ID
        assert _SYM_TO_COINPAPRIKA_ID["SOL"] == "sol-solana"
        assert _SYM_TO_COINPAPRIKA_ID["ETH"] == "eth-ethereum"


class TestTradingToolsTAHelpers:
    """Test pure technical analysis functions."""

    def test_calc_sma_basic(self):
        from trading.trading_tools import _calc_sma
        result = _calc_sma([10, 20, 30, 40, 50], 3)
        assert result is not None
        assert abs(result - 40.0) < 0.001

    def test_calc_sma_insufficient_data(self):
        from trading.trading_tools import _calc_sma
        assert _calc_sma([10, 20], 5) is None

    def test_calc_ema_basic(self):
        from trading.trading_tools import _calc_ema
        prices = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20]
        result = _calc_ema(prices, 5)
        assert result is not None
        assert result > 0

    def test_calc_ema_insufficient_data(self):
        from trading.trading_tools import _calc_ema
        assert _calc_ema([10, 20], 5) is None

    def test_calc_rsi_overbought(self):
        from trading.trading_tools import _calc_rsi
        # Monotonically rising = RSI near 100
        prices = list(range(1, 30))
        result = _calc_rsi(prices, 14)
        assert result is not None
        assert result > 70

    def test_calc_rsi_oversold(self):
        from trading.trading_tools import _calc_rsi
        # Monotonically falling = RSI near 0
        prices = list(range(30, 0, -1))
        result = _calc_rsi(prices, 14)
        assert result is not None
        assert result < 30

    def test_calc_rsi_insufficient_data(self):
        from trading.trading_tools import _calc_rsi
        assert _calc_rsi([10, 20, 30], 14) is None

    def test_calc_macd_basic(self):
        from trading.trading_tools import _calc_macd
        # Need >= 26 data points
        prices = [100 + i * 0.5 for i in range(40)]
        result = _calc_macd(prices)
        assert result is not None
        assert "macd_line" in result
        assert "signal_line" in result
        assert "histogram" in result

    def test_calc_macd_insufficient_data(self):
        from trading.trading_tools import _calc_macd
        assert _calc_macd([10, 20, 30]) is None

    def test_calc_bollinger_basic(self):
        from trading.trading_tools import _calc_bollinger
        prices = [100 + i for i in range(25)]
        result = _calc_bollinger(prices, 20)
        assert result is not None
        assert "upper" in result
        assert "middle" in result
        assert "lower" in result
        assert result["upper"] > result["middle"] > result["lower"]

    def test_calc_bollinger_insufficient_data(self):
        from trading.trading_tools import _calc_bollinger
        assert _calc_bollinger([10, 20], 20) is None

    def test_determine_signal_strong_buy(self):
        from trading.trading_tools import _determine_signal
        # RSI oversold, MACD bullish, golden cross, price above SMA50, price below lower BB
        result = _determine_signal(
            rsi=25,
            sma_20=110,
            sma_50=100,
            macd={"histogram": 0.5, "macd_line": 1.0, "signal_line": 0.5},
            bollinger={"upper": 120, "middle": 105, "lower": 115},
            current_price=90,
        )
        assert result["signal"] in ("STRONG_BUY", "BUY")
        assert "confidence" in result
        assert "reasons" in result
        assert len(result["reasons"]) > 0

    def test_determine_signal_neutral(self):
        from trading.trading_tools import _determine_signal
        result = _determine_signal(
            rsi=50,
            sma_20=None,
            sma_50=None,
            macd=None,
            bollinger=None,
            current_price=100,
        )
        assert result["signal"] == "NEUTRAL"


class TestTradingToolsEndpoints:
    """Test read-only GET endpoints."""

    @pytest.mark.asyncio
    async def test_trading_stats(self, client):
        resp = await client.get("/api/trading/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "alerts_active" in data
        assert "supported_chains" in data
        assert isinstance(data["supported_chains"], list)

    @pytest.mark.asyncio
    async def test_copy_wallets(self, client):
        resp = await client.get("/api/trading/copy/wallets")
        assert resp.status_code == 200
        data = resp.json()
        assert "wallets" in data
        assert "count" in data
        assert data["count"] > 0
        wallet = data["wallets"][0]
        assert "address" in wallet
        assert "pnl_30d_pct" in wallet
        assert "win_rate" in wallet

    @pytest.mark.asyncio
    async def test_copy_wallet_detail(self, client):
        resp = await client.get("/api/trading/copy/wallet/FakeWalletAddress12345678901234567890")
        assert resp.status_code == 200
        data = resp.json()
        assert "trades" in data
        assert "trades_count" in data
        assert data["simulated"] is True


# ══════════════════════════════════════════════════════════════════
#  MODULE 3: token_sniper.py — model validation, pure functions
# ══════════════════════════════════════════════════════════════════

class TestSniperModels:
    """Test WatchRequest pydantic model validation."""

    def test_watch_request_valid(self):
        from trading.token_sniper import WatchRequest
        req = WatchRequest(
            wallet="A" * 44,
            min_market_cap_usd=1000,
            max_market_cap_usd=50000,
            webhook_url="https://example.com/hook",
        )
        assert req.wallet == "A" * 44
        assert req.auto_buy_usdc == 0

    def test_watch_request_auto_buy_max(self):
        from trading.token_sniper import WatchRequest
        req = WatchRequest(
            wallet="B" * 44,
            auto_buy_usdc=1000,
            telegram_chat_id="12345",
        )
        assert req.auto_buy_usdc == 1000

    def test_watch_request_auto_buy_over_max_rejected(self):
        from trading.token_sniper import WatchRequest
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            WatchRequest(wallet="C" * 44, auto_buy_usdc=1001, webhook_url="https://x.com")

    def test_watch_request_negative_market_cap_rejected(self):
        from trading.token_sniper import WatchRequest
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            WatchRequest(wallet="D" * 44, min_market_cap_usd=-1, webhook_url="https://x.com")

    def test_watch_request_wallet_too_short_rejected(self):
        from trading.token_sniper import WatchRequest
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            WatchRequest(wallet="ABC", webhook_url="https://x.com")


class TestSniperPureFunctions:
    """Test _parse_token, _matches_watch, _get_mint."""

    def test_get_mint_from_mint_field(self):
        from trading.token_sniper import _get_mint
        assert _get_mint({"mint": "abc123"}) == "abc123"

    def test_get_mint_from_token_address(self):
        from trading.token_sniper import _get_mint
        assert _get_mint({"tokenAddress": "def456"}) == "def456"

    def test_get_mint_empty(self):
        from trading.token_sniper import _get_mint
        assert _get_mint({}) == ""

    def test_parse_token_dexscreener(self):
        from trading.token_sniper import _parse_token
        raw = {
            "_source": "dexscreener",
            "tokenAddress": "0xABC123",
            "description": "Test token",
            "chainId": "base",
            "totalAmount": 5,
        }
        t = _parse_token(raw)
        assert t["mint"] == "0xABC123"
        assert t["source"] == "dexscreener"
        assert t["chain"] == "base"
        assert t["boosts"] == 5

    def test_parse_token_pumpfun(self):
        from trading.token_sniper import _parse_token
        raw = {
            "_source": "pump.fun",
            "mint": "SolMint123",
            "name": "TestCoin",
            "symbol": "TC",
            "usd_market_cap": 50000,
            "reply_count": 42,
        }
        t = _parse_token(raw)
        assert t["mint"] == "SolMint123"
        assert t["source"] == "pump.fun"
        assert t["market_cap_usd"] == 50000
        assert t["boosts"] == 42
        assert t["chain"] == "solana"

    def test_matches_watch_basic_match(self):
        from trading.token_sniper import _matches_watch
        token = {"market_cap_usd": 50000, "boosts": 10, "name": "Test", "symbol": "T", "description": ""}
        watch = {"min_market_cap_usd": 1000, "max_market_cap_usd": 100000, "min_boosts": 5, "keywords": []}
        assert _matches_watch(token, watch) is True

    def test_matches_watch_below_min_cap(self):
        from trading.token_sniper import _matches_watch
        token = {"market_cap_usd": 500, "boosts": 10, "name": "", "symbol": "", "description": ""}
        watch = {"min_market_cap_usd": 1000, "max_market_cap_usd": 100000, "min_boosts": 0, "keywords": []}
        assert _matches_watch(token, watch) is False

    def test_matches_watch_above_max_cap(self):
        from trading.token_sniper import _matches_watch
        token = {"market_cap_usd": 200000, "boosts": 0, "name": "", "symbol": "", "description": ""}
        watch = {"min_market_cap_usd": 0, "max_market_cap_usd": 100000, "min_boosts": 0, "keywords": []}
        assert _matches_watch(token, watch) is False

    def test_matches_watch_keyword_match(self):
        from trading.token_sniper import _matches_watch
        token = {"market_cap_usd": 5000, "boosts": 0, "name": "DogeCoin", "symbol": "DOGE", "description": "a dog token"}
        watch = {"min_market_cap_usd": 0, "max_market_cap_usd": 100000, "min_boosts": 0, "keywords": ["dog"]}
        assert _matches_watch(token, watch) is True

    def test_matches_watch_keyword_no_match(self):
        from trading.token_sniper import _matches_watch
        token = {"market_cap_usd": 5000, "boosts": 0, "name": "CatCoin", "symbol": "CAT", "description": "meow"}
        watch = {"min_market_cap_usd": 0, "max_market_cap_usd": 100000, "min_boosts": 0, "keywords": ["dog"]}
        assert _matches_watch(token, watch) is False

    def test_matches_watch_insufficient_boosts(self):
        from trading.token_sniper import _matches_watch
        token = {"market_cap_usd": 5000, "boosts": 2, "name": "", "symbol": "", "description": ""}
        watch = {"min_market_cap_usd": 0, "max_market_cap_usd": 100000, "min_boosts": 5, "keywords": []}
        assert _matches_watch(token, watch) is False


class TestSniperEndpoints:
    """Test sniper endpoints."""

    @pytest.mark.asyncio
    async def test_sniper_stats(self, client):
        resp = await client.get("/api/sniper/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_detected" in data
        assert "active_watches" in data
        assert "scan_interval_s" in data

    @pytest.mark.asyncio
    async def test_sniper_watchlist_requires_wallet(self, client):
        resp = await client.get("/api/sniper/watchlist", params={"wallet": "A" * 44})
        assert resp.status_code == 200
        data = resp.json()
        assert "watches" in data
        assert data["wallet"] == "A" * 44


# ══════════════════════════════════════════════════════════════════
#  MODULE 4: solana_defi.py — protocol constants + endpoints
# ══════════════════════════════════════════════════════════════════

class TestSolanaDefiConstants:
    """Test protocol data structures."""

    def test_lending_protocols_have_required_keys(self):
        from trading.solana_defi import LENDING_PROTOCOLS
        assert "solend" in LENDING_PROTOCOLS
        assert "kamino" in LENDING_PROTOCOLS
        assert "marginfi" in LENDING_PROTOCOLS
        for pid, p in LENDING_PROTOCOLS.items():
            assert "name" in p
            assert "supply_apy" in p
            assert "assets" in p
            assert "url" in p

    def test_staking_protocols_have_required_keys(self):
        from trading.solana_defi import STAKING_PROTOCOLS
        assert "marinade" in STAKING_PROTOCOLS
        assert "jito" in STAKING_PROTOCOLS
        assert "blazestake" in STAKING_PROTOCOLS
        for pid, p in STAKING_PROTOCOLS.items():
            assert "name" in p
            assert "token" in p
            assert "apy" in p or p.get("apy") == 0
            assert "url" in p

    def test_lp_protocols_have_required_keys(self):
        from trading.solana_defi import LP_PROTOCOLS
        assert "orca" in LP_PROTOCOLS
        assert "raydium" in LP_PROTOCOLS
        for pid, p in LP_PROTOCOLS.items():
            assert "name" in p
            assert "top_pools" in p

    def test_asset_mints_contain_sol_and_usdc(self):
        from trading.solana_defi import ASSET_MINTS, SOL_MINT, USDC_MINT
        assert ASSET_MINTS["SOL"] == SOL_MINT
        assert ASSET_MINTS["USDC"] == USDC_MINT

    def test_staking_output_mints(self):
        from trading.solana_defi import STAKING_OUTPUT_MINTS, MSOL_MINT, JITOSOL_MINT, BSOL_MINT
        assert STAKING_OUTPUT_MINTS["marinade"] == MSOL_MINT
        assert STAKING_OUTPUT_MINTS["jito"] == JITOSOL_MINT
        assert STAKING_OUTPUT_MINTS["blazestake"] == BSOL_MINT

    def test_find_best_supply(self):
        from trading.solana_defi import _find_best
        result = _find_best("supply")
        assert "asset" in result
        assert "protocol" in result
        assert "apy" in result

    def test_find_best_borrow(self):
        from trading.solana_defi import _find_best
        result = _find_best("borrow")
        assert "asset" in result
        assert "protocol" in result
        assert "apy" in result


class TestSolanaDefiEndpoints:
    """Test DeFi GET endpoints (mock DeFiLlama to avoid external calls)."""

    @pytest.mark.asyncio
    async def test_staking_endpoint(self, client):
        with patch("trading.solana_defi._refresh_defi_rates", new_callable=AsyncMock):
            resp = await client.get("/api/defi/staking")
            assert resp.status_code == 200
            data = resp.json()
            assert "protocols" in data
            assert "best_apy" in data
            names = [p["name"] for p in data["protocols"]]
            assert "Marinade Finance" in names

    @pytest.mark.asyncio
    async def test_lending_endpoint(self, client):
        with patch("trading.solana_defi._refresh_defi_rates", new_callable=AsyncMock):
            resp = await client.get("/api/defi/lending")
            assert resp.status_code == 200
            data = resp.json()
            assert "protocols" in data
            assert "best_supply" in data
            assert "best_borrow" in data

    @pytest.mark.asyncio
    async def test_lending_best_endpoint(self, client):
        with patch("trading.solana_defi._refresh_defi_rates", new_callable=AsyncMock):
            resp = await client.get("/api/defi/lending/best", params={"asset": "USDC"})
            assert resp.status_code == 200
            data = resp.json()
            assert data["asset"] == "USDC"
            assert "all_supply_rates" in data
            assert "all_borrow_rates" in data

    @pytest.mark.asyncio
    async def test_lp_endpoint(self, client):
        with patch("trading.solana_defi._refresh_defi_rates", new_callable=AsyncMock):
            resp = await client.get("/api/defi/lp")
            assert resp.status_code == 200
            data = resp.json()
            assert "protocols" in data
            names = [p["name"] for p in data["protocols"]]
            assert "Orca Whirlpools" in names


# ══════════════════════════════════════════════════════════════════
#  MODULE 5: public_api_discover.py — discover, demand, chain-support
# ══════════════════════════════════════════════════════════════════

class TestPublicApiDiscoverEndpoints:
    """Test public discovery endpoints."""

    @pytest.mark.asyncio
    async def test_discover_returns_services(self, client):
        resp = await client.get("/api/public/discover")
        assert resp.status_code == 200
        data = resp.json()
        assert "agents" in data
        assert "results_count" in data
        assert "how_to_buy" in data
        assert "treasury_wallet" in data["how_to_buy"]

    @pytest.mark.asyncio
    async def test_discover_with_capability_filter(self, client):
        resp = await client.get("/api/public/discover", params={"capability": "audit"})
        assert resp.status_code == 200
        data = resp.json()
        assert "agents" in data
        # MAXIA native audit service should be returned
        names = [a.get("name", "") for a in data["agents"]]
        assert any("audit" in n.lower() or "Audit" in n for n in names)

    @pytest.mark.asyncio
    async def test_discover_with_max_price_filter(self, client):
        resp = await client.get("/api/public/discover", params={"max_price": 0.01})
        assert resp.status_code == 200
        data = resp.json()
        for agent in data["agents"]:
            assert agent["price_usdc"] <= 0.01

    @pytest.mark.asyncio
    async def test_discover_post(self, client):
        resp = await client.post("/api/public/discover", json={"capability": "image"})
        assert resp.status_code == 200
        data = resp.json()
        assert "agents" in data

    @pytest.mark.asyncio
    async def test_chain_support(self, client):
        resp = await client.get("/api/public/chain-support")
        assert resp.status_code == 200
        data = resp.json()
        assert "features" in data
        assert "swap" in data["features"]
        assert "escrow" in data["features"]
        assert data["chains"] >= 15

    @pytest.mark.asyncio
    async def test_demand_endpoint(self, client):
        resp = await client.get("/api/public/demand")
        assert resp.status_code == 200
        data = resp.json()
        assert "demand" in data

    @pytest.mark.asyncio
    async def test_marketplace_stats(self, client):
        resp = await client.get("/api/public/marketplace-stats")
        assert resp.status_code == 200
        data = resp.json()
        # Should return some kind of stats dict
        assert isinstance(data, dict)
