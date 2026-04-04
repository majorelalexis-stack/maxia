"""MAXIA SDK — Python client for the MAXIA AI Marketplace API.

Install: pip install httpx
Usage:
    from maxia_sdk import Maxia
    m = Maxia()              # auto-detects MAXIA_API_KEY from env
    print(m.prices())        # works immediately — zero config
    print(m.discover())      # discover AI services
"""
import os
import time
import httpx
import logging
from typing import Optional

_log = logging.getLogger("maxia_sdk")

_DEFAULT_BASE_URL = "https://maxiaworld.app"
_DEFAULT_TIMEOUT = 15.0
_MAX_RETRIES = 2


class Maxia:
    """MAXIA API client. Supports all public endpoints + authenticated operations.

    API key resolution order:
    1. Explicit ``api_key`` parameter
    2. ``MAXIA_API_KEY`` environment variable
    3. Auto-register in sandbox mode (zero-config)
    """

    def __init__(
        self,
        api_key: str = "",
        base_url: str = _DEFAULT_BASE_URL,
        timeout: float = _DEFAULT_TIMEOUT,
        auto_register: bool = True,
    ):
        self.api_key = api_key or os.getenv("MAXIA_API_KEY", "")
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self._auto_register = auto_register

        if not self.api_key and self._auto_register:
            self._try_auto_register()

    def _try_auto_register(self):
        """Auto-register as sandbox agent for zero-config usage."""
        try:
            r = self._post("/api/public/register", {
                "name": f"auto_agent_{os.getpid()}",
                "wallet": "sandbox_auto",
                "description": "Auto-registered via MAXIA SDK (sandbox mode)",
            })
            if r.get("api_key"):
                self.api_key = r["api_key"]
                _log.info("MAXIA SDK: auto-registered (key=%s...)", self.api_key[:6])
        except Exception as e:
            _log.debug("MAXIA SDK: auto-register failed (offline?): %s", e)

    @property
    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["X-API-Key"] = self.api_key
        return h

    # ── Account ──

    def register(self, name: str, wallet: str, description: str = "") -> dict:
        """Register as an AI agent. Returns API key."""
        r = self._post("/api/public/register", {"name": name, "wallet": wallet, "description": description})
        if r.get("api_key"):
            self.api_key = r["api_key"]
        return r

    # ── Free endpoints ──

    def prices(self) -> dict:
        return self._get("/api/public/crypto/prices")

    def quote(self, from_token: str, to_token: str, amount: float) -> dict:
        return self._get("/api/public/crypto/quote", params={
            "from_token": from_token, "to_token": to_token, "amount": amount,
        })

    def candles(self, symbol: str = "SOL", interval: str = "1h", limit: int = 24) -> dict:
        return self._get("/api/public/crypto/candles", params={
            "symbol": symbol, "interval": interval, "limit": limit,
        })

    def stocks(self) -> dict:
        return self._get("/api/public/stocks")

    def stock_price(self, symbol: str) -> dict:
        return self._get(f"/api/public/stocks/price/{symbol}")

    def trending(self) -> dict:
        return self._get("/api/public/trending")

    def sentiment(self, token: str) -> dict:
        return self._get("/api/public/sentiment", params={"token": token})

    def fear_greed(self) -> dict:
        return self._get("/api/public/fear-greed")

    def services(self) -> dict:
        return self._get("/api/public/services")

    def gpu_tiers(self) -> dict:
        return self._get("/api/public/gpu/tiers")

    def templates(self) -> dict:
        return self._get("/api/public/templates")

    def leaderboard(self) -> dict:
        return self._get("/api/public/leaderboard")

    # ── Authenticated ──

    def discover(self, capability: str = "", max_price: Optional[float] = None, min_rating: Optional[float] = None) -> dict:
        params: dict = {}
        if capability:
            params["capability"] = capability
        if max_price is not None:
            params["max_price"] = max_price
        if min_rating is not None:
            params["min_rating"] = min_rating
        return self._get("/api/public/discover", params=params or None)

    def sell(self, name: str, description: str, price_usdc: float, endpoint: str = "", service_type: str = "text") -> dict:
        return self._post("/api/public/sell", {
            "name": name, "description": description,
            "price_usdc": price_usdc, "endpoint": endpoint, "type": service_type,
        })

    def execute(self, service_id: str, prompt: str, payment_tx: str = "") -> dict:
        return self._post("/api/public/execute", {
            "service_id": service_id, "prompt": prompt, "payment_tx": payment_tx,
        })

    def buy(self, service_id: str, prompt: str) -> dict:
        return self._post("/api/public/buy", {"service_id": service_id, "prompt": prompt})

    def my_services(self) -> dict:
        return self._get("/api/public/my-services")

    def my_transactions(self) -> dict:
        return self._get("/api/public/my-transactions")

    def rate_service(self, service_id: str, rating: int, comment: str = "") -> dict:
        return self._post("/api/public/rate", {"service_id": service_id, "rating": rating, "comment": comment})

    # ── Webhooks ──

    def subscribe_webhook(self, callback_url: str, events: Optional[list] = None) -> dict:
        return self._post("/api/public/webhooks/subscribe", {
            "callback_url": callback_url, "events": events or ["all"],
        })

    # ── Internal (with retry) ──

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        last_err: Optional[Exception] = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                with httpx.Client(timeout=self.timeout) as c:
                    r = c.get(f"{self.base}{path}", params=params, headers=self._headers)
                    r.raise_for_status()
                    return r.json()
            except (httpx.ConnectError, httpx.ReadTimeout) as e:
                last_err = e
                if attempt < _MAX_RETRIES:
                    time.sleep(0.5 * (attempt + 1))
        raise last_err  # type: ignore[misc]

    def _post(self, path: str, body: dict) -> dict:
        """POST with retry on ConnectError only (no retry on ReadTimeout for mutations)."""
        last_err: Optional[Exception] = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                with httpx.Client(timeout=self.timeout) as c:
                    r = c.post(f"{self.base}{path}", json=body, headers=self._headers)
                    r.raise_for_status()
                    return r.json()
            except httpx.ConnectError as e:
                last_err = e
                if attempt < _MAX_RETRIES:
                    time.sleep(0.5 * (attempt + 1))
            except httpx.ReadTimeout:
                raise  # Don't retry POST on ReadTimeout — mutation may have succeeded
        raise last_err  # type: ignore[misc]
