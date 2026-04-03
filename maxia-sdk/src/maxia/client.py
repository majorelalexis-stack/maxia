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
            headers={"User-Agent": "maxia-python/12.1.0"},
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

    def discover(self, capability: str = "", chain: str = "", min_rating: float = 0, limit: int = 20) -> dict:
        """Discover AI services and agents on the marketplace.

        Args:
            capability: Filter by capability (e.g. ``"swap"``, ``"audit"``).
            chain: Filter by blockchain (e.g. ``"solana"``).
            min_rating: Minimum rating (0-5).
            limit: Max results.

        Returns:
            Dict with ``services`` list and ``total`` count.

        Example::

            m = Maxia()
            data = m.discover(capability="swap")
            for svc in data.get("services", []):
                print(svc["name"])
        """
        params = {"limit": limit}
        if capability:
            params["capability"] = capability
        if chain:
            params["chain"] = chain
        if min_rating:
            params["min_rating"] = min_rating
        return self._get("/api/public/discover", params=params)

    def trending(self) -> dict:
        """Get trending crypto tokens and social buzz.

        Example::

            m = Maxia()
            print(m.trending())
        """
        return self._get("/api/public/trending")

    def fear_greed(self) -> dict:
        """Get the crypto Fear & Greed Index.

        Example::

            m = Maxia()
            fg = m.fear_greed()
            print(fg.get("value"), fg.get("classification"))
        """
        return self._get("/api/public/fear-greed")

    def wallet_analysis(self, address: str) -> dict:
        """Analyze a wallet's holdings and activity.

        Args:
            address: Wallet address (Solana or EVM).

        Example::

            m = Maxia()
            data = m.wallet_analysis("So1ana...addr")
            print(data.get("total_value_usd"))
        """
        return self._get("/api/public/wallet-analysis", params={"address": address})

    def chains(self) -> dict:
        """List all 14 supported blockchains with status.

        Example::

            m = Maxia()
            for chain in m.chains().get("chains", []):
                print(chain["name"], chain["status"])
        """
        return self._get("/api/public/chain-support")

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

    def gpu_rent(self, tier: str, duration_hours: float = 1, wallet: str = "") -> dict:
        """Rent a GPU via Akash Network.

        Args:
            tier: GPU tier (e.g. ``"rtx4090"``, ``"h100_sxm5"``, ``"a100_80gb"``).
            duration_hours: Rental duration in hours.
            wallet: Wallet address for payment.

        Returns:
            Dict with ``pod_id``, ``ssh_command``, ``cost_usdc``, etc.

        Example::

            m = Maxia(api_key="maxia_abc...")
            pod = m.gpu_rent("rtx4090", duration_hours=2, wallet="So1ana...")
            print(pod["ssh_command"])
        """
        self._require_key()
        body = {"tier": tier, "duration_hours": duration_hours}
        if wallet:
            body["wallet"] = wallet
        return self._post("/api/public/gpu/rent", json=body)

    def negotiate(self, service_id: str, proposed_price: float, message: str = "") -> dict:
        """Negotiate the price of a service.

        Args:
            service_id: Service to negotiate on.
            proposed_price: Your proposed price in USDC.
            message: Optional message to the seller.

        Returns:
            Dict with ``accepted``, ``counter_price``, ``message``, etc.

        Example::

            m = Maxia(api_key="maxia_abc...")
            result = m.negotiate("svc_123", proposed_price=0.30)
            if result.get("accepted"):
                print("Deal!")
        """
        self._require_key()
        body = {"service_id": service_id, "proposed_price": proposed_price}
        if message:
            body["message"] = message
        return self._post("/api/public/negotiate", json=body)

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

    # ------------------------------------------------------------------
    # Prepaid Credits (off-chain micropayments, zero gas)
    # ------------------------------------------------------------------

    def credits_balance(self) -> dict:
        """Get prepaid credit balance.

        Returns:
            Dict with ``balance_usdc``, ``total_deposited``, ``total_spent``,
            and ``transactions`` list.

        Example::

            m = Maxia(api_key="maxia_abc...")
            bal = m.credits_balance()
            print(f"${bal['balance_usdc']} available")
        """
        self._require_key()
        return self._get("/api/credits/balance")

    def credits_deposit(self, payment_tx: str, amount_usdc: float, chain: str = "solana") -> dict:
        """Deposit USDC on-chain and receive prepaid credits.

        Send USDC to MAXIA Treasury first, then call this with the tx signature.
        Credits are added instantly — no gas per API call after that.

        Args:
            payment_tx: On-chain USDC transaction signature.
            amount_usdc: Amount deposited.
            chain: Chain used (``"solana"``, ``"base"``, ``"ethereum"``, etc.).

        Example::

            m = Maxia(api_key="maxia_abc...")
            result = m.credits_deposit("5xYz...", 10.0, chain="solana")
            print(f"Balance: ${result['balance_usdc']}")
        """
        self._require_key()
        return self._post("/api/credits/deposit", json={
            "payment_tx": payment_tx,
            "amount_usdc": amount_usdc,
            "chain": chain,
        })

    # ------------------------------------------------------------------
    # Solana DeFi (Lending, Borrowing, Staking)
    # ------------------------------------------------------------------

    def defi_lending(self) -> dict:
        """List Solana lending protocols with live APY rates.

        Example::

            m = Maxia()
            for p in m.defi_lending()["protocols"]:
                print(p["name"], p["supply_apy"].get("USDC", 0))
        """
        return self._get("/api/defi/lending")

    def defi_best_rate(self, asset: str = "USDC") -> dict:
        """Find the best lending rate for an asset across all protocols.

        Example::

            m = Maxia()
            best = m.defi_best_rate("USDC")
            print(f"Best: {best['best_supply']['protocol']} at {best['best_supply']['apy']}%")
        """
        return self._get("/api/defi/lending/best", params={"asset": asset})

    def defi_staking(self) -> dict:
        """List Solana liquid staking protocols (Marinade, Jito, BlazeStake).

        Example::

            m = Maxia()
            for p in m.defi_staking()["protocols"]:
                print(p["name"], f"{p['apy']}% APY")
        """
        return self._get("/api/defi/staking")

    def defi_lend(self, protocol: str, asset: str, amount: float, wallet: str) -> dict:
        """Lend an asset to earn interest. Returns unsigned tx for wallet signing.

        Args:
            protocol: Lending protocol (``"kamino"``, ``"solend"``, ``"marginfi"``).
            asset: Asset to lend (``"USDC"``, ``"SOL"``).
            amount: Amount to lend.
            wallet: Solana wallet address.

        Example::

            m = Maxia(api_key="maxia_abc...")
            tx = m.defi_lend("kamino", "USDC", 100.0, "So1ana...")
            print(tx["transaction_b64"])  # Sign with wallet
        """
        self._require_key()
        return self._post("/api/defi/lend", json={
            "protocol": protocol, "asset": asset, "amount": amount, "wallet": wallet,
        })

    def defi_stake(self, protocol: str, amount: float, wallet: str) -> dict:
        """Stake SOL via liquid staking (Marinade, Jito, BlazeStake).

        Args:
            protocol: Staking protocol (``"marinade"``, ``"jito"``, ``"blazestake"``).
            amount: SOL amount to stake.
            wallet: Solana wallet address.

        Example::

            m = Maxia(api_key="maxia_abc...")
            tx = m.defi_stake("marinade", 1.0, "So1ana...")
        """
        self._require_key()
        return self._post("/api/defi/stake", json={
            "protocol": protocol, "amount": amount, "wallet": wallet,
        })

    # ------------------------------------------------------------------
    # Streaming Payments (pay-per-second for GPU, LLM, etc.)
    # ------------------------------------------------------------------

    def stream_create(self, receiver: str, rate_per_hour: float,
                      max_hours: float = 1, service_id: str = "",
                      payment_tx: str = "") -> dict:
        """Create a streaming payment (pay-per-second).

        Lock USDC upfront. The receiver earns continuously. Either party
        can stop at any time — receiver keeps earned, payer gets refund.

        Args:
            receiver: Receiver wallet address.
            rate_per_hour: USDC per hour to stream.
            max_hours: Maximum duration in hours.
            service_id: Optional service ID being paid for.
            payment_tx: On-chain tx locking the USDC.

        Example::

            m = Maxia(api_key="maxia_abc...")
            stream = m.stream_create(
                receiver="ReceiverWallet...",
                rate_per_hour=0.50,
                max_hours=2,
            )
            print(stream["stream_id"])
        """
        self._require_key()
        body = {
            "receiver": receiver,
            "rate_per_hour": rate_per_hour,
            "max_hours": max_hours,
        }
        if service_id:
            body["service_id"] = service_id
        if payment_tx:
            body["payment_tx"] = payment_tx
        return self._post("/api/stream/create", json=body)

    def stream_stop(self, stream_id: str) -> dict:
        """Stop a streaming payment. Receiver keeps earned, payer gets refund.

        Example::

            m = Maxia(api_key="maxia_abc...")
            result = m.stream_stop("STREAM-ABC123")
            print(f"Earned: ${result['earned_usdc']}, Refund: ${result['refund_usdc']}")
        """
        self._require_key()
        return self._post("/api/stream/stop", json={"stream_id": stream_id})

    def stream_status(self, stream_id: str) -> dict:
        """Get real-time status of a streaming payment.

        Example::

            m = Maxia()
            status = m.stream_status("STREAM-ABC123")
            print(f"Earned so far: ${status['earned_usdc']}")
        """
        return self._get(f"/api/stream/{stream_id}")
