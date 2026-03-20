"""MAXIA SDK — Python client for the MAXIA AI Marketplace API.

Install: pip install httpx
Usage:
    from maxia_sdk import Maxia
    m = Maxia()
    print(m.prices())
"""
import httpx
from typing import Optional


class Maxia:
    """MAXIA API client. Supports all public endpoints + authenticated operations."""

    def __init__(self, api_key: str = "", base_url: str = "https://maxiaworld.app"):
        self.api_key = api_key
        self.base = base_url.rstrip("/")

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
        return self._get(f"/api/public/crypto/quote?from_token={from_token}&to_token={to_token}&amount={amount}")

    def candles(self, symbol: str = "SOL", interval: str = "1h", limit: int = 24) -> dict:
        return self._get(f"/api/public/crypto/candles?symbol={symbol}&interval={interval}&limit={limit}")

    def stocks(self) -> dict:
        return self._get("/api/public/stocks")

    def stock_price(self, symbol: str) -> dict:
        return self._get(f"/api/public/stocks/price/{symbol}")

    def trending(self) -> dict:
        return self._get("/api/public/trending")

    def sentiment(self, token: str) -> dict:
        return self._get(f"/api/public/sentiment?token={token}")

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

    def discover(self, capability: str = "", max_price: float = None, min_rating: float = None) -> dict:
        params = []
        if capability:
            params.append(f"capability={capability}")
        if max_price is not None:
            params.append(f"max_price={max_price}")
        if min_rating is not None:
            params.append(f"min_rating={min_rating}")
        qs = "?" + "&".join(params) if params else ""
        return self._get(f"/api/public/discover{qs}")

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

    def subscribe_webhook(self, callback_url: str, events: list = None) -> dict:
        return self._post("/api/public/webhooks/subscribe", {
            "callback_url": callback_url, "events": events or ["all"],
        })

    # ── Internal ──

    def _get(self, path: str) -> dict:
        with httpx.Client(timeout=15) as c:
            r = c.get(f"{self.base}{path}", headers=self._headers)
            return r.json()

    def _post(self, path: str, body: dict) -> dict:
        with httpx.Client(timeout=15) as c:
            r = c.post(f"{self.base}{path}", json=body, headers=self._headers)
            return r.json()
