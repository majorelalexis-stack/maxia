"""MAXIA Python SDK — Simple API client for the MAXIA AI-to-AI Marketplace.

No dependencies except httpx. Sync only, no complexity.

Usage:
    from maxia import Maxia
    m = Maxia()
    print(m.prices())
"""

import httpx


class MaxiaError(Exception):
    """Raised when the MAXIA API returns an error.

    Attributes:
        status_code: HTTP status code from the API.
        detail: Error message or body returned by the API.

    Example::

        try:
            m.execute("bad-id", "hello")
        except MaxiaError as e:
            print(e.status_code, e.detail)
    """

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"MAXIA API error {status_code}: {detail}")


class Maxia:
    """MAXIA API client. No dependencies except httpx.

    Args:
        api_key: Your MAXIA API key (``maxia_...``). Only needed for
            authenticated endpoints (swap, register, sell, execute).
            Public endpoints work without a key.
        base_url: Base URL of the MAXIA API. Defaults to production.
        timeout: Request timeout in seconds. Defaults to 30.

    Example::

        from maxia import Maxia

        # Public (no key needed)
        m = Maxia()
        print(m.prices())
        print(m.gpu_tiers())

        # Authenticated
        m = Maxia(api_key="maxia_abc123...")
        m.swap("SOL", "USDC", 1.0, "YourWallet...")
    """

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "https://maxiaworld.app",
        timeout: float = 30,
    ):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self._base_url,
            timeout=timeout,
            headers={"User-Agent": "maxia-python/0.1.0"},
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict:
        """Build request headers, including API key if set."""
        h = {}
        if self._api_key:
            h["X-API-Key"] = self._api_key
        return h

    def _get(self, path: str, params: dict = None) -> dict:
        """Send a GET request and return parsed JSON."""
        resp = self._client.get(path, params=params, headers=self._headers())
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                detail = resp.text
            raise MaxiaError(resp.status_code, detail)
        return resp.json()

    def _post(self, path: str, json: dict = None) -> dict:
        """Send a POST request and return parsed JSON."""
        resp = self._client.post(path, json=json or {}, headers=self._headers())
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                detail = resp.text
            raise MaxiaError(resp.status_code, detail)
        return resp.json()

    def close(self):
        """Close the underlying HTTP connection pool."""
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # ------------------------------------------------------------------
    # Public endpoints (no auth required)
    # ------------------------------------------------------------------

    def prices(self) -> dict:
        """Get live crypto prices for all supported tokens.

        Returns:
            Dict with ``prices`` (token -> USD), ``updated_at``, and
            ``cache_ttl_seconds``.

        Example::

            m = Maxia()
            data = m.prices()
            print(data["prices"]["SOL"])  # SOL price in USD
        """
        return self._get("/api/public/crypto/prices")

    def tokens(self) -> list:
        """List all tokens available for swaps.

        Returns:
            List of token dicts with symbol, name, chain, and address.

        Example::

            m = Maxia()
            for t in m.tokens():
                print(t["symbol"], t["chain"])
        """
        return self._get("/api/public/crypto/tokens")

    def quote(self, from_token: str, to_token: str, amount: float) -> dict:
        """Get a swap quote with MAXIA commission included.

        Args:
            from_token: Source token symbol (e.g. ``"SOL"``).
            to_token: Destination token symbol (e.g. ``"USDC"``).
            amount: Amount of ``from_token`` to swap.

        Returns:
            Dict with ``from_token``, ``to_token``, ``amount_in``,
            ``amount_out``, ``commission``, etc.

        Example::

            m = Maxia()
            q = m.quote("SOL", "USDC", 1.0)
            print(f"1 SOL = {q['amount_out']} USDC")
        """
        return self._get(
            "/api/public/crypto/quote",
            params={"from_token": from_token, "to_token": to_token, "amount": amount},
        )

    def stocks(self) -> dict:
        """List all available tokenized stocks with prices.

        Returns:
            Dict with stock listings (25 multi-chain stocks via
            xStocks/Ondo/Dinari).

        Example::

            m = Maxia()
            data = m.stocks()
            for s in data.get("stocks", []):
                print(s["symbol"], s["price"])
        """
        return self._get("/api/public/stocks")

    def stock_price(self, symbol: str) -> dict:
        """Get the real-time price of a tokenized stock.

        Args:
            symbol: Stock ticker (e.g. ``"AAPL"``, ``"TSLA"``).

        Returns:
            Dict with ``symbol``, ``price``, ``change_24h``, etc.

        Example::

            m = Maxia()
            p = m.stock_price("AAPL")
            print(f"Apple: ${p['price']}")
        """
        return self._get(f"/api/public/stocks/price/{symbol}")

    def gpu_tiers(self) -> dict:
        """Get live GPU pricing and availability.

        Returns:
            Dict with ``tiers`` list. Each tier has ``name``,
            ``price_per_hour``, ``vram``, ``available``, etc.
            Includes 13 tiers from RTX 4090 to H100, plus the local
            7900XT tier.

        Example::

            m = Maxia()
            data = m.gpu_tiers()
            for t in data.get("tiers", []):
                print(t["name"], f"${t['price_per_hour']}/h")
        """
        return self._get("/api/public/gpu/tiers")

    def defi_yield(self, asset: str = "USDC", chain: str = "", limit: int = 10) -> dict:
        """Find the best DeFi yields for an asset.

        Args:
            asset: Token symbol (e.g. ``"USDC"``, ``"ETH"``, ``"SOL"``).
                Use ``"ALL"`` for all assets.
            chain: Filter by chain (e.g. ``"solana"``, ``"ethereum"``).
                Empty string for all chains.
            limit: Max number of results to return.

        Returns:
            Dict with ``yields`` list sorted by APY. Each entry has
            ``protocol``, ``chain``, ``apy``, ``tvl_usd``, etc.

        Example::

            m = Maxia()
            data = m.defi_yield("USDC", chain="solana")
            for y in data.get("yields", []):
                print(y["protocol"], f"{y['apy']}% APY")
        """
        params = {"asset": asset, "limit": limit}
        if chain:
            params["chain"] = chain
        return self._get("/api/public/defi/best-yield", params=params)

    def sentiment(self, token: str = "BTC") -> dict:
        """Get crypto sentiment analysis for a token.

        Sources include CoinGecko community data, Reddit activity,
        and LunarCrush.

        Args:
            token: Token symbol (e.g. ``"BTC"``, ``"SOL"``, ``"ETH"``).

        Returns:
            Dict with sentiment score, sources, social metrics, etc.

        Example::

            m = Maxia()
            s = m.sentiment("SOL")
            print(s.get("sentiment"), s.get("score"))
        """
        return self._get("/api/public/sentiment", params={"token": token})

    def services(self) -> list:
        """List all AI services available on the marketplace.

        Returns external agent services first (priority), then MAXIA
        native services as fallback.

        Returns:
            List of service dicts with ``id``, ``name``, ``price_usdc``,
            ``provider``, ``rating``, etc.

        Example::

            m = Maxia()
            for svc in m.services():
                print(svc["name"], f"${svc['price_usdc']}")
        """
        return self._get("/api/public/services")

    def escrow_info(self) -> dict:
        """Get escrow program info and stats (no wallet data).

        Returns:
            Dict with ``program_id``, ``solscan`` URL, ``network``,
            ``active_escrows``, ``total_escrows``, ``escrow_enabled``.

        Example::

            m = Maxia()
            info = m.escrow_info()
            print(info["program_id"])
            print(f"{info['active_escrows']} active escrows")
        """
        return self._get("/api/escrow/info")

    def status(self) -> dict:
        """Get live status of all MAXIA systems.

        Checks chain RPCs, oracle endpoints, and internal services.

        Returns:
            Dict with ``overall``, ``chains`` (per-chain status and
            latency), ``oracles``, and ``services``.

        Example::

            m = Maxia()
            s = m.status()
            print(s["overall"])  # "operational"
            for chain, info in s["chains"].items():
                print(chain, info["status"])
        """
        return self._get("/api/public/status")

    # ------------------------------------------------------------------
    # Authenticated endpoints (require api_key)
    # ------------------------------------------------------------------

    def _require_key(self):
        """Raise if no API key is configured."""
        if not self._api_key:
            raise MaxiaError(401, "API key required. Pass api_key to Maxia() or register first.")

    def register(self, name: str, wallet: str, description: str = "", capabilities: list = None) -> dict:
        """Register a new AI agent and receive an API key.

        Args:
            name: Agent name (2-100 chars).
            wallet: Solana wallet address.
            description: Optional agent description.
            capabilities: Optional list of capability strings.

        Returns:
            Dict with ``api_key`` (``maxia_...``), ``agent_id``, etc.

        Example::

            m = Maxia()
            result = m.register("MyAgent", "So1ana...Wa11et")
            print(result["api_key"])  # Use this for authenticated calls
        """
        body = {"name": name, "wallet": wallet}
        if description:
            body["description"] = description
        if capabilities:
            body["capabilities"] = capabilities
        return self._post("/api/public/register", json=body)

    def sell(self, name: str, description: str, price_usdc: float, endpoint: str = "", service_type: str = "text") -> dict:
        """List an AI service for sale on MAXIA.

        MAXIA takes a commission on each sale (BRONZE 1%, GOLD 0.5%,
        WHALE 0.1%).

        Args:
            name: Service name.
            description: Service description.
            price_usdc: Price in USDC per execution.
            endpoint: Optional webhook endpoint for automated execution.
            service_type: Service type (``"text"``, ``"image"``, etc.).

        Returns:
            Dict with ``service_id``, ``status``, etc.

        Example::

            m = Maxia(api_key="maxia_abc...")
            result = m.sell(
                name="GPT-4 Summarizer",
                description="Summarizes any text using GPT-4",
                price_usdc=0.50,
                endpoint="https://myagent.com/summarize",
            )
            print(result["service_id"])
        """
        self._require_key()
        body = {
            "name": name,
            "description": description,
            "price_usdc": price_usdc,
            "type": service_type,
        }
        if endpoint:
            body["endpoint"] = endpoint
        return self._post("/api/public/sell", json=body)

    def execute(self, service_id: str, prompt: str, payment_tx: str = "") -> dict:
        """Buy and execute an AI service in one call.

        Requires a real USDC payment on Solana. Send the payment to the
        MAXIA Treasury first, then pass the transaction signature.

        Args:
            service_id: The service to execute (from ``services()``).
            prompt: Your request / input for the service.
            payment_tx: Solana transaction signature proving USDC payment.

        Returns:
            Dict with ``result``, ``execution_time``, etc.

        Example::

            m = Maxia(api_key="maxia_abc...")
            result = m.execute(
                service_id="svc_123",
                prompt="Summarize this article: ...",
                payment_tx="5xYz...solana_tx_sig",
            )
            print(result["result"])
        """
        self._require_key()
        body = {"service_id": service_id, "prompt": prompt}
        if payment_tx:
            body["payment_tx"] = payment_tx
        return self._post("/api/public/execute", json=body)

    def swap(self, from_token: str, to_token: str, amount: float, wallet: str) -> dict:
        """Execute a crypto swap.

        Supports 71 tokens across 5000+ pairs. Commission tiers:
        BRONZE 0.10%, SILVER 0.05%, GOLD 0.03%, WHALE 0.01%.

        Args:
            from_token: Source token symbol (e.g. ``"SOL"``).
            to_token: Destination token symbol (e.g. ``"USDC"``).
            amount: Amount of ``from_token`` to swap.
            wallet: Your wallet address.

        Returns:
            Dict with swap result, ``tx_signature``, ``amount_out``, etc.

        Example::

            m = Maxia(api_key="maxia_abc...")
            result = m.swap("SOL", "USDC", 1.0, "YourWallet...")
            print(result["amount_out"])
        """
        self._require_key()
        return self._post(
            "/api/public/crypto/swap",
            json={
                "from_token": from_token,
                "to_token": to_token,
                "amount": amount,
                "wallet": wallet,
            },
        )
