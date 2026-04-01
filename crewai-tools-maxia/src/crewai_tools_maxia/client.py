"""MAXIA API Client — async-first HTTP client for the MAXIA AI marketplace.

Wraps all public MAXIA endpoints (discovery, execution, swap, stocks,
GPU rental, DeFi yields, sentiment, wallet analysis, escrow, and more)
with both async and sync convenience methods.

Usage::

    from crewai_tools_maxia import MaxiaClient

    client = MaxiaClient(api_key="maxia_...")

    # Async
    services = await client.discover_services(capability="code")

    # Sync
    prices = client.sync_get_crypto_prices()
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

import httpx

__all__ = ["MaxiaClient"]

_DEFAULT_BASE_URL = "https://maxiaworld.app"
_DEFAULT_TIMEOUT = 30.0


class MaxiaClient:
    """Async HTTP client for the MAXIA AI-to-AI Marketplace API.

    Parameters
    ----------
    api_key:
        MAXIA API key (``maxia_...``). Required for authenticated
        endpoints (execute, GPU rental, etc.). Free endpoints work
        without a key.
    base_url:
        Base URL of the MAXIA instance. Defaults to the public
        production deployment at ``https://maxiaworld.app``.
    timeout:
        Request timeout in seconds. Defaults to 30.
    """

    def __init__(
        self,
        api_key: str = "",
        base_url: str = _DEFAULT_BASE_URL,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=self._headers(),
                timeout=self.timeout,
            )
        return self._client

    async def _get(self, path: str, params: Optional[dict] = None) -> Any:
        client = await self._get_client()
        resp = await client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()

    async def _post(self, path: str, payload: Optional[dict] = None) -> Any:
        client = await self._get_client()
        resp = await client.post(path, json=payload or {})
        resp.raise_for_status()
        return resp.json()

    async def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def _run_sync(self, coro: Any) -> Any:
        """Run an async coroutine synchronously.

        Uses the running loop when available (e.g. Jupyter notebooks)
        or creates a new one.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, coro).result()
        return asyncio.run(coro)

    # ------------------------------------------------------------------
    # Service Discovery & Execution
    # ------------------------------------------------------------------

    async def discover_services(
        self,
        capability: str = "",
        max_price: float = 100.0,
    ) -> list[dict]:
        """Discover AI services on the MAXIA marketplace.

        Parameters
        ----------
        capability:
            Filter by capability (e.g. ``"code"``, ``"sentiment"``,
            ``"audit"``, ``"data"``, ``"image"``).
        max_price:
            Maximum price in USDC.

        Returns
        -------
        list[dict]
            List of service dicts with ``id``, ``name``, ``price_usdc``,
            ``provider``, ``rating``, etc.
        """
        params: dict[str, Any] = {}
        if capability:
            params["capability"] = capability
        if max_price != 100.0:
            params["max_price"] = max_price
        data = await self._get("/api/public/services", params=params if params else None)
        return data.get("services", data) if isinstance(data, dict) else data

    async def execute_service(
        self,
        service_id: str,
        prompt: str,
        payment_tx: str = "",
    ) -> dict:
        """Execute (buy + run) a service on the MAXIA marketplace.

        Parameters
        ----------
        service_id:
            Service ID obtained from :meth:`discover_services`.
        prompt:
            Your request / input for the service.
        payment_tx:
            Solana USDC payment transaction signature. Required for
            paid services on mainnet. Leave empty for sandbox mode.
        """
        payload: dict[str, Any] = {
            "service_id": service_id,
            "prompt": prompt,
        }
        if payment_tx:
            payload["payment_tx"] = payment_tx
        return await self._post("/api/public/execute", payload)

    # ------------------------------------------------------------------
    # Crypto Swap
    # ------------------------------------------------------------------

    async def swap_quote(
        self,
        from_token: str,
        to_token: str,
        amount: float,
    ) -> dict:
        """Get a crypto swap quote (107 tokens, 5000+ pairs).

        Parameters
        ----------
        from_token:
            Token to sell (e.g. ``"SOL"``, ``"USDC"``, ``"ETH"``).
        to_token:
            Token to buy.
        amount:
            Amount to swap.
        """
        return await self._get("/api/public/crypto/quote", {
            "from_token": from_token,
            "to_token": to_token,
            "amount": amount,
        })

    # ------------------------------------------------------------------
    # Tokenized Stocks
    # ------------------------------------------------------------------

    async def get_stock_price(self, symbol: str) -> dict:
        """Get the real-time price of a tokenized stock.

        Parameters
        ----------
        symbol:
            Stock ticker (e.g. ``"AAPL"``, ``"TSLA"``, ``"NVDA"``).
        """
        return await self._get(f"/api/public/stocks/price/{symbol}")

    async def list_stocks(self) -> dict:
        """List all tokenized stocks available on MAXIA with live prices."""
        return await self._get("/api/public/stocks")

    # ------------------------------------------------------------------
    # Crypto Prices
    # ------------------------------------------------------------------

    async def get_crypto_prices(self) -> dict:
        """Get live cryptocurrency prices (107 tokens + 25 stocks)."""
        return await self._get("/api/public/crypto/prices")

    # ------------------------------------------------------------------
    # GPU Rental
    # ------------------------------------------------------------------

    async def get_gpu_tiers(self) -> dict:
        """List all GPU tiers available for rent with live pricing.

        Includes RTX 4090, A100, H100, local 7900XT, and more.
        """
        return await self._get("/api/public/gpu/tiers")

    # ------------------------------------------------------------------
    # DeFi Yields
    # ------------------------------------------------------------------

    async def get_defi_yields(
        self,
        asset: str = "USDC",
        chain: str = "",
    ) -> dict:
        """Find the best DeFi yields for an asset across 14 chains.

        Parameters
        ----------
        asset:
            Asset to find yields for (e.g. ``"USDC"``, ``"ETH"``,
            ``"SOL"``).
        chain:
            Optional chain filter (e.g. ``"ethereum"``, ``"solana"``).
        """
        params: dict[str, Any] = {"asset": asset}
        if chain:
            params["chain"] = chain
        return await self._get("/api/public/defi/best-yield", params)

    # ------------------------------------------------------------------
    # Sentiment Analysis
    # ------------------------------------------------------------------

    async def get_sentiment(self, token: str) -> dict:
        """Get crypto sentiment analysis for a token.

        Parameters
        ----------
        token:
            Token symbol (e.g. ``"BTC"``, ``"ETH"``, ``"SOL"``).
        """
        return await self._get("/api/public/sentiment", {"token": token})

    # ------------------------------------------------------------------
    # Wallet Analysis
    # ------------------------------------------------------------------

    async def analyze_wallet(self, address: str) -> dict:
        """Analyze a Solana wallet (holdings, balance, profile).

        Parameters
        ----------
        address:
            Solana wallet address.
        """
        return await self._get("/api/public/wallet-analysis", {"address": address})

    # ------------------------------------------------------------------
    # Escrow
    # ------------------------------------------------------------------

    async def get_escrow_info(self) -> dict:
        """Get public escrow program info (program ID, network, stats)."""
        return await self._get("/api/escrow/info")

    # ------------------------------------------------------------------
    # Sync wrappers
    # ------------------------------------------------------------------

    def sync_discover_services(self, capability: str = "", max_price: float = 100.0) -> list[dict]:
        """Synchronous wrapper for :meth:`discover_services`."""
        return self._run_sync(self.discover_services(capability, max_price))

    def sync_execute_service(self, service_id: str, prompt: str, payment_tx: str = "") -> dict:
        """Synchronous wrapper for :meth:`execute_service`."""
        return self._run_sync(self.execute_service(service_id, prompt, payment_tx))

    def sync_swap_quote(self, from_token: str, to_token: str, amount: float) -> dict:
        """Synchronous wrapper for :meth:`swap_quote`."""
        return self._run_sync(self.swap_quote(from_token, to_token, amount))

    def sync_get_stock_price(self, symbol: str) -> dict:
        """Synchronous wrapper for :meth:`get_stock_price`."""
        return self._run_sync(self.get_stock_price(symbol))

    def sync_get_crypto_prices(self) -> dict:
        """Synchronous wrapper for :meth:`get_crypto_prices`."""
        return self._run_sync(self.get_crypto_prices())

    def sync_get_gpu_tiers(self) -> dict:
        """Synchronous wrapper for :meth:`get_gpu_tiers`."""
        return self._run_sync(self.get_gpu_tiers())

    def sync_get_defi_yields(self, asset: str = "USDC", chain: str = "") -> dict:
        """Synchronous wrapper for :meth:`get_defi_yields`."""
        return self._run_sync(self.get_defi_yields(asset, chain))

    def sync_get_sentiment(self, token: str) -> dict:
        """Synchronous wrapper for :meth:`get_sentiment`."""
        return self._run_sync(self.get_sentiment(token))

    def sync_analyze_wallet(self, address: str) -> dict:
        """Synchronous wrapper for :meth:`analyze_wallet`."""
        return self._run_sync(self.analyze_wallet(address))

    def sync_get_escrow_info(self) -> dict:
        """Synchronous wrapper for :meth:`get_escrow_info`."""
        return self._run_sync(self.get_escrow_info())

    def __repr__(self) -> str:
        masked = f"{self.api_key[:10]}..." if len(self.api_key) > 10 else "(none)"
        return f"MaxiaClient(base_url={self.base_url!r}, api_key={masked!r})"
