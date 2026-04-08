"""MAXIA API Client for DeerFlow — async-first HTTP client.

Wraps MAXIA public endpoints with both async and sync methods.
Designed for use inside DeerFlow skill functions.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional

import httpx

__all__ = ["MaxiaClient"]

_log = logging.getLogger("deerflow_maxia")

_DEFAULT_BASE_URL = "https://maxiaworld.app"
_DEFAULT_TIMEOUT = 30.0
_MAX_RETRIES = 2


class MaxiaClient:
    """Async HTTP client for the MAXIA AI-to-AI Marketplace.

    Parameters
    ----------
    api_key:
        MAXIA API key (``maxia_...``). Auto-detects from
        ``MAXIA_API_KEY`` env var. Free endpoints work without a key.
    base_url:
        Base URL of the MAXIA instance.
    """

    def __init__(
        self,
        api_key: str = "",
        base_url: str = _DEFAULT_BASE_URL,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self.api_key = api_key or os.getenv("MAXIA_API_KEY", "")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

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
        for attempt in range(_MAX_RETRIES + 1):
            try:
                client = await self._get_client()
                resp = await client.get(path, params=params)
                resp.raise_for_status()
                return resp.json()
            except (httpx.ConnectError, httpx.ReadTimeout):
                if attempt >= _MAX_RETRIES:
                    raise
                await asyncio.sleep(0.5 * (attempt + 1))

    async def _post(self, path: str, payload: Optional[dict] = None) -> Any:
        for attempt in range(_MAX_RETRIES + 1):
            try:
                client = await self._get_client()
                resp = await client.post(path, json=payload or {})
                resp.raise_for_status()
                return resp.json()
            except (httpx.ConnectError, httpx.ReadTimeout):
                if attempt >= _MAX_RETRIES:
                    raise
                await asyncio.sleep(0.5 * (attempt + 1))

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def _run_sync(self, coro: Any) -> Any:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, coro).result()
        return asyncio.run(coro)

    # -- Discovery & Execution --

    async def discover(self, capability: str = "", max_price: float = 100.0) -> list[dict]:
        """Find AI services on the MAXIA marketplace."""
        params: dict[str, Any] = {}
        if capability:
            params["capability"] = capability
        if max_price != 100.0:
            params["max_price"] = max_price
        data = await self._get("/api/public/services", params or None)
        return data.get("services", data) if isinstance(data, dict) else data

    async def execute(self, service_id: str, prompt: str, payment_tx: str = "") -> dict:
        """Buy and execute an AI service."""
        payload: dict[str, Any] = {"service_id": service_id, "prompt": prompt}
        if payment_tx:
            payload["payment_tx"] = payment_tx
        return await self._post("/api/public/execute", payload)

    # -- Crypto --

    async def get_prices(self) -> dict:
        """Get live crypto prices (65+ tokens)."""
        return await self._get("/api/public/crypto/prices")

    async def swap_quote(self, from_token: str, to_token: str, amount: float) -> dict:
        """Get a crypto swap quote."""
        return await self._get("/api/public/crypto/quote", {
            "from_token": from_token, "to_token": to_token, "amount": amount,
        })

    async def get_sentiment(self, token: str) -> dict:
        """Get crypto sentiment analysis."""
        return await self._get("/api/public/sentiment", {"token": token})

    # -- Stocks --

    async def list_stocks(self) -> dict:
        """List tokenized stocks with live prices."""
        return await self._get("/api/public/stocks")

    async def stock_price(self, symbol: str) -> dict:
        """Get price of a tokenized stock."""
        return await self._get(f"/api/public/stocks/price/{symbol}")

    # -- GPU --

    async def gpu_tiers(self) -> dict:
        """List GPU tiers with live pricing."""
        return await self._get("/api/public/gpu/tiers")

    # -- DeFi --

    async def best_yield(self, asset: str = "USDC", chain: str = "") -> dict:
        """Find best DeFi yields across 14 chains."""
        params: dict[str, Any] = {"asset": asset}
        if chain:
            params["chain"] = chain
        return await self._get("/api/public/defi/best-yield", params)

    # -- Wallet --

    async def analyze_wallet(self, address: str) -> dict:
        """Analyze a Solana wallet."""
        return await self._get("/api/public/wallet-analysis", {"address": address})

    # -- Escrow --

    async def escrow_info(self) -> dict:
        """Get escrow program info."""
        return await self._get("/api/escrow/info")

    # -- Sync wrappers --

    def sync_get_prices(self) -> dict:
        return self._run_sync(self.get_prices())

    def sync_swap_quote(self, from_token: str, to_token: str, amount: float) -> dict:
        return self._run_sync(self.swap_quote(from_token, to_token, amount))

    def sync_discover(self, capability: str = "") -> list[dict]:
        return self._run_sync(self.discover(capability))

    def sync_gpu_tiers(self) -> dict:
        return self._run_sync(self.gpu_tiers())

    def sync_best_yield(self, asset: str = "USDC") -> dict:
        return self._run_sync(self.best_yield(asset))

    def __repr__(self) -> str:
        masked = f"{self.api_key[:10]}..." if len(self.api_key) > 10 else "(none)"
        return f"MaxiaClient(base_url={self.base_url!r}, api_key={masked!r})"
