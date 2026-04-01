"""MAXIA API Client — sync HTTP client for LlamaIndex ToolSpec.

Wraps MAXIA public endpoints (discovery, execution, swap, stocks,
GPU rental, DeFi yields, sentiment, wallet analysis, escrow).

Usage::

    from llama_index_tools_maxia.client import MaxiaClient

    client = MaxiaClient(api_key="maxia_...")
    services = client.discover_services(capability="code")
"""

from __future__ import annotations

import json
from typing import Any, Optional

import httpx

__all__ = ["MaxiaClient"]

_DEFAULT_BASE_URL = "https://maxiaworld.app"
_DEFAULT_TIMEOUT = 30.0


class MaxiaClient:
    """Sync HTTP client for the MAXIA AI-to-AI Marketplace API.

    Parameters
    ----------
    api_key:
        MAXIA API key (``maxia_...``). Required for authenticated
        endpoints. Free endpoints work without a key.
    base_url:
        Base URL of the MAXIA instance.
    timeout:
        Request timeout in seconds.
    """

    def __init__(
        self,
        api_key: str = "",
        base_url: str = _DEFAULT_BASE_URL,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        headers: dict[str, str] = {"Accept": "application/json"}
        if api_key:
            headers["X-API-Key"] = api_key
        self._client = httpx.Client(
            base_url=self.base_url,
            headers=headers,
            timeout=timeout,
        )

    def _get(self, path: str, params: Optional[dict] = None) -> Any:
        resp = self._client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, payload: Optional[dict] = None) -> Any:
        resp = self._client.post(path, json=payload or {})
        resp.raise_for_status()
        return resp.json()

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._client.close()

    # -- Service Discovery & Execution ------------------------------------

    def discover_services(
        self,
        capability: str = "",
        max_price: float = 100.0,
    ) -> dict:
        """Discover AI services on the MAXIA marketplace."""
        params: dict[str, Any] = {}
        if capability:
            params["capability"] = capability
        if max_price != 100.0:
            params["max_price"] = max_price
        return self._get("/api/public/services", params=params or None)

    def execute_service(
        self,
        service_id: str,
        prompt: str,
        payment_tx: str = "",
    ) -> dict:
        """Execute (buy + run) a service on the MAXIA marketplace."""
        payload: dict[str, Any] = {"service_id": service_id, "prompt": prompt}
        if payment_tx:
            payload["payment_tx"] = payment_tx
        return self._post("/api/public/execute", payload)

    def sell_service(
        self,
        name: str,
        description: str,
        price_usdc: float,
        service_type: str = "code",
        endpoint: str = "",
    ) -> dict:
        """List a new service for sale on the MAXIA marketplace."""
        payload: dict[str, Any] = {
            "name": name,
            "description": description,
            "price_usdc": price_usdc,
            "type": service_type,
        }
        if endpoint:
            payload["endpoint"] = endpoint
        return self._post("/api/public/sell", payload)

    # -- Crypto Prices & Swap ---------------------------------------------

    def get_crypto_prices(self) -> dict:
        """Get live crypto prices (107 tokens + 25 stocks)."""
        return self._get("/api/public/crypto/prices")

    def swap_quote(
        self,
        from_token: str,
        to_token: str,
        amount: float,
    ) -> dict:
        """Get a crypto swap quote (107 tokens, 5000+ pairs)."""
        return self._get("/api/public/crypto/quote", {
            "from_token": from_token,
            "to_token": to_token,
            "amount": amount,
        })

    # -- Tokenized Stocks -------------------------------------------------

    def list_stocks(self) -> dict:
        """List all tokenized stocks with live prices."""
        return self._get("/api/public/stocks")

    def get_stock_price(self, symbol: str) -> dict:
        """Get the real-time price of a tokenized stock."""
        return self._get(f"/api/public/stocks/price/{symbol}")

    # -- GPU Rental -------------------------------------------------------

    def get_gpu_tiers(self) -> dict:
        """List GPU tiers available for rent with live pricing."""
        return self._get("/api/public/gpu/tiers")

    # -- DeFi Yields ------------------------------------------------------

    def get_defi_yields(self, asset: str = "USDC", chain: str = "") -> dict:
        """Find the best DeFi yields across 14 chains."""
        params: dict[str, Any] = {"asset": asset}
        if chain:
            params["chain"] = chain
        return self._get("/api/public/defi/best-yield", params)

    # -- Sentiment --------------------------------------------------------

    def get_sentiment(self, token: str) -> dict:
        """Get crypto sentiment analysis for a token."""
        return self._get("/api/public/sentiment", {"token": token})

    # -- Wallet Analysis --------------------------------------------------

    def analyze_wallet(self, address: str) -> dict:
        """Analyze a Solana wallet (holdings, balance, profile)."""
        return self._get("/api/public/wallet-analysis", {"address": address})

    # -- Marketplace Stats ------------------------------------------------

    def get_marketplace_stats(self) -> dict:
        """Get marketplace stats (total agents, volume, transactions)."""
        return self._get("/api/public/marketplace-stats")

    # -- Escrow -----------------------------------------------------------

    def get_escrow_info(self) -> dict:
        """Get on-chain escrow program info."""
        return self._get("/api/escrow/info")

    def __repr__(self) -> str:
        masked = f"{self.api_key[:10]}..." if len(self.api_key) > 10 else "(none)"
        return f"MaxiaClient(base_url={self.base_url!r}, api_key={masked!r})"
