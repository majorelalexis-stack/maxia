"""CrewAI Tool wrappers for the MAXIA AI-to-AI Marketplace.

Each tool subclasses :class:`crewai.tools.BaseTool` and exposes a
``_run`` method with a Pydantic ``args_schema`` for input validation,
so that CrewAI agents can invoke them correctly via tool-calling.

Usage::

    from crewai_tools_maxia import get_all_tools

    tools = get_all_tools(api_key="maxia_...")
    # Pass *tools* to any CrewAI agent.
"""

from __future__ import annotations

import json
from typing import Any, Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from crewai_tools_maxia.client import MaxiaClient

__all__ = [
    "MaxiaSwapTool",
    "MaxiaStockPriceTool",
    "MaxiaCryptoPricesTool",
    "MaxiaGPURentalTool",
    "MaxiaDeFiYieldTool",
    "MaxiaSentimentTool",
    "MaxiaServiceDiscoveryTool",
    "MaxiaServiceExecuteTool",
    "MaxiaWalletAnalysisTool",
    "MaxiaEscrowInfoTool",
    "get_all_tools",
]


def _fmt(data: Any) -> str:
    """Format API response as a readable JSON string for the LLM."""
    if isinstance(data, (dict, list)):
        return json.dumps(data, indent=2, ensure_ascii=False)
    return str(data)


# ======================================================================
# Input schemas (Pydantic models)
# ======================================================================


class SwapQuoteInput(BaseModel):
    """Input for getting a crypto swap quote."""
    from_token: str = Field(description="Token to sell, e.g. SOL, USDC, ETH, BTC, BONK")
    to_token: str = Field(description="Token to buy, e.g. SOL, USDC, ETH, BTC, BONK")
    amount: float = Field(description="Amount to swap")


class StockPriceInput(BaseModel):
    """Input for getting a tokenized stock price."""
    symbol: str = Field(description="Stock ticker symbol, e.g. AAPL, TSLA, NVDA, GOOGL, MSFT, AMZN, META")


class EmptyInput(BaseModel):
    """No input required."""
    pass


class DeFiYieldInput(BaseModel):
    """Input for finding the best DeFi yields."""
    asset: str = Field(description="Asset to find yields for, e.g. USDC, ETH, SOL, BTC")
    chain: str = Field(default="", description="Optional chain filter, e.g. ethereum, solana, arbitrum, base")


class SentimentInput(BaseModel):
    """Input for crypto sentiment analysis."""
    token: str = Field(description="Token symbol, e.g. BTC, ETH, SOL, BONK")


class ServiceDiscoveryInput(BaseModel):
    """Input for discovering AI services on the marketplace."""
    capability: str = Field(default="", description="What you are looking for: sentiment, audit, code, data, image, translation, scraper")
    max_price: float = Field(default=100.0, description="Maximum price in USDC")


class ServiceExecuteInput(BaseModel):
    """Input for executing an AI service on the marketplace."""
    service_id: str = Field(description="Service ID obtained from service discovery")
    prompt: str = Field(description="Your request or prompt for the service")
    payment_tx: str = Field(default="", description="Solana USDC payment transaction signature (required for mainnet)")


class WalletAnalysisInput(BaseModel):
    """Input for analyzing a Solana wallet."""
    address: str = Field(description="Solana wallet address to analyze")


# ======================================================================
# Tool implementations
# ======================================================================


class MaxiaSwapTool(BaseTool):
    """Get a crypto swap quote from the MAXIA marketplace.

    Supports 107 tokens and 5000+ pairs on Solana. Returns the
    estimated output amount, price impact, and commission.
    """

    name: str = "maxia_swap_quote"
    description: str = (
        "Get a crypto swap quote on the MAXIA marketplace. "
        "Supports 107 tokens and 5000+ pairs on Solana (SOL, USDC, ETH, BTC, BONK, JUP, WIF, etc.). "
        "Returns estimated output amount, price impact, and commission. "
        "Use this to check token exchange rates before swapping."
    )
    args_schema: Type[BaseModel] = SwapQuoteInput
    client: Any = None

    def _run(self, from_token: str, to_token: str, amount: float) -> str:
        return _fmt(self.client.sync_swap_quote(from_token, to_token, amount))


class MaxiaStockPriceTool(BaseTool):
    """Get the real-time price of a tokenized stock on MAXIA.

    25 US stocks available: AAPL, TSLA, NVDA, GOOGL, MSFT, AMZN,
    META, and more, tradable as fractional shares from 1 USDC.
    """

    name: str = "maxia_stock_price"
    description: str = (
        "Get the real-time price of a tokenized stock on the MAXIA marketplace. "
        "25 US stocks available (AAPL, TSLA, NVDA, GOOGL, MSFT, AMZN, META, etc.) "
        "tradable as fractional shares from 1 USDC on multiple blockchains. "
        "Returns current price, 24h change, and available trading pairs."
    )
    args_schema: Type[BaseModel] = StockPriceInput
    client: Any = None

    def _run(self, symbol: str) -> str:
        return _fmt(self.client.sync_get_stock_price(symbol))


class MaxiaCryptoPricesTool(BaseTool):
    """Get live cryptocurrency prices from MAXIA.

    107 tokens and 25 US stocks updated every 30 seconds.
    """

    name: str = "maxia_crypto_prices"
    description: str = (
        "Get live cryptocurrency prices from the MAXIA marketplace. "
        "107 tokens (SOL, BTC, ETH, BONK, JUP, WIF, and more) plus 25 US stocks, "
        "updated every 30 seconds. Returns a dict of token symbols to USD prices. "
        "No input required."
    )
    args_schema: Type[BaseModel] = EmptyInput
    client: Any = None

    def _run(self) -> str:
        return _fmt(self.client.sync_get_crypto_prices())


class MaxiaGPURentalTool(BaseTool):
    """List GPU tiers available for rent on MAXIA.

    RTX 4090, A100 80GB, H100 SXM5, A6000, 4xA100, and local 7900XT
    with live pricing and competitor comparison.
    """

    name: str = "maxia_gpu_tiers"
    description: str = (
        "List all GPU tiers available for rent on the MAXIA marketplace. "
        "Includes RTX 4090, A100 80GB, H100 SXM5, A6000, 4xA100, and local RX 7900XT "
        "with live pricing in USDC/hour and competitor comparison. "
        "Use this to find the best GPU for AI training, fine-tuning, or inference. "
        "No input required."
    )
    args_schema: Type[BaseModel] = EmptyInput
    client: Any = None

    def _run(self) -> str:
        return _fmt(self.client.sync_get_gpu_tiers())


class MaxiaDeFiYieldTool(BaseTool):
    """Find the best DeFi yields for any asset across 14 chains.

    Data sourced from DeFiLlama covering Aave, Marinade, Jito,
    Compound, Ref Finance, and more.
    """

    name: str = "maxia_defi_yields"
    description: str = (
        "Find the best DeFi yields for any asset across 14 blockchains. "
        "Data from DeFiLlama covering Aave, Marinade, Jito, Compound, Ref Finance, and more. "
        "Provide an asset symbol (USDC, ETH, SOL, BTC) and optionally filter by chain "
        "(ethereum, solana, arbitrum, base, polygon, avalanche, etc.)."
    )
    args_schema: Type[BaseModel] = DeFiYieldInput
    client: Any = None

    def _run(self, asset: str, chain: str = "") -> str:
        return _fmt(self.client.sync_get_defi_yields(asset, chain))


class MaxiaSentimentTool(BaseTool):
    """Get crypto sentiment analysis for any token.

    Sources include CoinGecko, Reddit, and LunarCrush.
    """

    name: str = "maxia_sentiment"
    description: str = (
        "Get crypto market sentiment analysis for any token. "
        "Sources include CoinGecko, Reddit, and LunarCrush. "
        "Returns sentiment score, social volume, and trend direction. "
        "Provide a token symbol like BTC, ETH, SOL, BONK."
    )
    args_schema: Type[BaseModel] = SentimentInput
    client: Any = None

    def _run(self, token: str) -> str:
        return _fmt(self.client.sync_get_sentiment(token))


class MaxiaServiceDiscoveryTool(BaseTool):
    """Discover AI services on the MAXIA marketplace.

    Browse services by capability (code, sentiment, audit, data,
    image, translation, scraper) with price filtering.
    """

    name: str = "maxia_discover_services"
    description: str = (
        "Discover AI services available on the MAXIA AI-to-AI marketplace. "
        "Filter by capability (code, sentiment, audit, data, image, translation, scraper) "
        "and maximum price in USDC. Returns service name, ID, price, provider, and rating. "
        "Use this to find services before executing them."
    )
    args_schema: Type[BaseModel] = ServiceDiscoveryInput
    client: Any = None

    def _run(self, capability: str = "", max_price: float = 100.0) -> str:
        return _fmt(self.client.sync_discover_services(capability, max_price))


class MaxiaServiceExecuteTool(BaseTool):
    """Execute (buy + run) an AI service on the MAXIA marketplace.

    Requires a service_id from discovery and a prompt describing what
    you want. For paid services on mainnet, a USDC payment_tx is needed.
    """

    name: str = "maxia_execute_service"
    description: str = (
        "Execute an AI service on the MAXIA marketplace in one call (buy + run). "
        "Requires a service_id (from maxia_discover_services) and a prompt. "
        "For paid services, include a Solana USDC payment_tx signature. "
        "Returns the service result directly."
    )
    args_schema: Type[BaseModel] = ServiceExecuteInput
    client: Any = None

    def _run(self, service_id: str, prompt: str, payment_tx: str = "") -> str:
        return _fmt(self.client.sync_execute_service(service_id, prompt, payment_tx))


class MaxiaWalletAnalysisTool(BaseTool):
    """Analyze a Solana wallet — holdings, balance, profile classification."""

    name: str = "maxia_wallet_analysis"
    description: str = (
        "Analyze a Solana wallet address. Returns token holdings, SOL balance, "
        "USDC balance, profile classification (whale, trader, holder, new), "
        "and recent activity summary. Useful for due diligence on counterparties."
    )
    args_schema: Type[BaseModel] = WalletAnalysisInput
    client: Any = None

    def _run(self, address: str) -> str:
        return _fmt(self.client.sync_analyze_wallet(address))


class MaxiaEscrowInfoTool(BaseTool):
    """Get MAXIA escrow program info.

    Returns the Solana escrow program ID, network, active/total escrow
    counts, and a Solscan link to verify on-chain.
    """

    name: str = "maxia_escrow_info"
    description: str = (
        "Get MAXIA on-chain escrow program information. "
        "Returns the Solana escrow program ID, network (mainnet-beta), "
        "number of active and total escrows, and a Solscan verification link. "
        "The escrow locks USDC in a PDA until both buyer and seller confirm delivery. "
        "No input required."
    )
    args_schema: Type[BaseModel] = EmptyInput
    client: Any = None

    def _run(self) -> str:
        return _fmt(self.client.sync_get_escrow_info())


# ======================================================================
# Convenience: get all tools at once
# ======================================================================


def get_all_tools(
    api_key: str = "",
    base_url: str = "https://maxiaworld.app",
) -> list[BaseTool]:
    """Return a list of all MAXIA CrewAI tools.

    Parameters
    ----------
    api_key:
        MAXIA API key. Required for authenticated tools (execute,
        wallet analysis, etc.). Free tools work without a key.
    base_url:
        Base URL of the MAXIA instance.

    Returns
    -------
    list[BaseTool]
        All 10 MAXIA tools, ready to pass to a CrewAI agent.
    """
    client = MaxiaClient(api_key=api_key, base_url=base_url)
    return [
        MaxiaSwapTool(client=client),
        MaxiaStockPriceTool(client=client),
        MaxiaCryptoPricesTool(client=client),
        MaxiaGPURentalTool(client=client),
        MaxiaDeFiYieldTool(client=client),
        MaxiaSentimentTool(client=client),
        MaxiaServiceDiscoveryTool(client=client),
        MaxiaServiceExecuteTool(client=client),
        MaxiaWalletAnalysisTool(client=client),
        MaxiaEscrowInfoTool(client=client),
    ]
