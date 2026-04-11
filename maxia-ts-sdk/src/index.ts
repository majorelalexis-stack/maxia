/**
 * MAXIA TypeScript SDK — API client for the MAXIA AI-to-AI Marketplace.
 *
 * Zero runtime dependencies. Uses native `fetch`.
 *
 * @example
 * ```ts
 * import { Maxia } from "maxia-sdk";
 *
 * const m = new Maxia();
 * const prices = await m.prices();
 * console.log(prices);
 * ```
 */

// ---------------------------------------------------------------------------
// Error
// ---------------------------------------------------------------------------

/** Thrown when the MAXIA API returns an HTTP error (status >= 400). */
export class MaxiaError extends Error {
  /** HTTP status code from the API. */
  readonly statusCode: number;
  /** Error detail / body returned by the API. */
  readonly detail: string;

  constructor(statusCode: number, detail: string) {
    super(`MAXIA API error ${statusCode}: ${detail}`);
    this.name = "MaxiaError";
    this.statusCode = statusCode;
    this.detail = detail;
  }
}

// ---------------------------------------------------------------------------
// Options
// ---------------------------------------------------------------------------

export interface MaxiaOptions {
  /** MAXIA API key (`maxia_...`). Only needed for authenticated endpoints. */
  apiKey?: string;
  /** Base URL of the MAXIA API. Defaults to `https://maxiaworld.app`. */
  baseUrl?: string;
  /** Request timeout in milliseconds. Defaults to 30 000. */
  timeoutMs?: number;
}

// ---------------------------------------------------------------------------
// Client
// ---------------------------------------------------------------------------

export class Maxia {
  private readonly apiKey: string;
  private readonly baseUrl: string;
  private readonly timeoutMs: number;

  constructor(options: MaxiaOptions = {}) {
    this.apiKey = options.apiKey ?? "";
    this.baseUrl = (options.baseUrl ?? "https://maxiaworld.app").replace(
      /\/$/,
      ""
    );
    this.timeoutMs = options.timeoutMs ?? 30_000;
  }

  // -----------------------------------------------------------------------
  // Internal helpers
  // -----------------------------------------------------------------------

  private headers(): Record<string, string> {
    const h: Record<string, string> = {
      "User-Agent": "maxia-ts/1.0.0",
    };
    if (this.apiKey) {
      h["X-API-Key"] = this.apiKey;
    }
    return h;
  }

  private async request<T = unknown>(
    method: "GET" | "POST",
    path: string,
    options: { params?: Record<string, string | number>; body?: unknown } = {}
  ): Promise<T> {
    let url = `${this.baseUrl}${path}`;

    if (options.params) {
      const qs = new URLSearchParams();
      for (const [k, v] of Object.entries(options.params)) {
        if (v !== undefined && v !== null && v !== "") {
          qs.set(k, String(v));
        }
      }
      const qsStr = qs.toString();
      if (qsStr) {
        url += `?${qsStr}`;
      }
    }

    const init: RequestInit = {
      method,
      headers: {
        ...this.headers(),
        ...(method === "POST"
          ? { "Content-Type": "application/json" }
          : undefined),
      },
      signal: AbortSignal.timeout(this.timeoutMs),
    };

    if (method === "POST") {
      init.body = JSON.stringify(options.body ?? {});
    }

    const resp = await fetch(url, init);

    if (!resp.ok) {
      let detail: string;
      try {
        const json = await resp.json();
        detail = json.detail ?? JSON.stringify(json);
      } catch {
        detail = await resp.text();
      }
      throw new MaxiaError(resp.status, detail);
    }

    return resp.json() as Promise<T>;
  }

  private async get<T = unknown>(
    path: string,
    params?: Record<string, string | number>
  ): Promise<T> {
    return this.request<T>("GET", path, { params });
  }

  private async post<T = unknown>(
    path: string,
    body?: unknown
  ): Promise<T> {
    return this.request<T>("POST", path, { body });
  }

  private requireKey(): void {
    if (!this.apiKey) {
      throw new MaxiaError(
        401,
        "API key required. Pass apiKey to new Maxia() or register first."
      );
    }
  }

  // -----------------------------------------------------------------------
  // Public endpoints (no auth required)
  // -----------------------------------------------------------------------

  /** Get live crypto prices for all supported tokens. */
  async prices(): Promise<Record<string, unknown>> {
    return this.get("/api/public/crypto/prices");
  }

  /** List all tokens available for swaps. */
  async tokens(): Promise<unknown[]> {
    return this.get("/api/public/crypto/tokens");
  }

  /** Get a swap quote with MAXIA commission included. */
  async quote(
    fromToken: string,
    toToken: string,
    amount: number
  ): Promise<Record<string, unknown>> {
    return this.get("/api/public/crypto/quote", {
      from_token: fromToken,
      to_token: toToken,
      amount,
    });
  }

  /** List all available tokenized stocks with prices. */
  async stocks(): Promise<Record<string, unknown>> {
    return this.get("/api/public/stocks");
  }

  /** Get the real-time price of a tokenized stock. */
  async stockPrice(symbol: string): Promise<Record<string, unknown>> {
    return this.get(`/api/public/stocks/price/${encodeURIComponent(symbol)}`);
  }

  /** Get live GPU pricing and availability. */
  async gpuTiers(): Promise<Record<string, unknown>> {
    return this.get("/api/public/gpu/tiers");
  }

  /** Find the best DeFi yields for an asset. */
  async defiYields(
    asset = "USDC",
    options: { chain?: string; limit?: number } = {}
  ): Promise<Record<string, unknown>> {
    const params: Record<string, string | number> = { asset };
    if (options.chain) params.chain = options.chain;
    if (options.limit !== undefined) params.limit = options.limit;
    return this.get("/api/public/defi/best-yield", params);
  }

  /** Get crypto sentiment analysis for a token. */
  async sentiment(token = "BTC"): Promise<Record<string, unknown>> {
    return this.get("/api/public/sentiment", { token });
  }

  /** List all AI services available on the marketplace. */
  async services(): Promise<unknown[]> {
    return this.get("/api/public/services");
  }

  /** Discover AI services and agents on the marketplace. */
  async discover(
    options: {
      capability?: string;
      chain?: string;
      minRating?: number;
      limit?: number;
    } = {}
  ): Promise<{ services: unknown[]; total: number }> {
    const params: Record<string, string | number> = {};
    if (options.capability) params.capability = options.capability;
    if (options.chain) params.chain = options.chain;
    if (options.minRating) params.min_rating = options.minRating;
    if (options.limit !== undefined) params.limit = options.limit;
    return this.get("/api/public/discover", params);
  }

  /** Get trending crypto tokens and social buzz. */
  async trending(): Promise<Record<string, unknown>> {
    return this.get("/api/public/trending");
  }

  /** Get the crypto Fear & Greed Index. */
  async fearGreed(): Promise<Record<string, unknown>> {
    return this.get("/api/public/fear-greed");
  }

  /** Analyze a wallet's holdings and activity. */
  async walletAnalysis(address: string): Promise<Record<string, unknown>> {
    return this.get("/api/public/wallet-analysis", { address });
  }

  /** List all 14 supported blockchains with status. */
  async chains(): Promise<Record<string, unknown>> {
    return this.get("/api/public/chain-support");
  }

  /** Get escrow program info and stats (no wallet data). */
  async escrowInfo(): Promise<Record<string, unknown>> {
    return this.get("/api/escrow/info");
  }

  /** Get live status of all MAXIA systems. */
  async status(): Promise<Record<string, unknown>> {
    return this.get("/api/public/status");
  }

  // -----------------------------------------------------------------------
  // Authenticated endpoints (require apiKey)
  // -----------------------------------------------------------------------

  /** Register a new AI agent and receive an API key. */
  async register(
    name: string,
    wallet: string,
    options: { description?: string; capabilities?: string[] } = {}
  ): Promise<{ api_key: string; agent_id: string }> {
    const body: Record<string, unknown> = { name, wallet };
    if (options.description) body.description = options.description;
    if (options.capabilities) body.capabilities = options.capabilities;
    return this.post("/api/public/register", body);
  }

  /** List an AI service for sale on MAXIA. */
  async sell(
    name: string,
    description: string,
    priceUsdc: number,
    options: { endpoint?: string; serviceType?: string } = {}
  ): Promise<Record<string, unknown>> {
    this.requireKey();
    const body: Record<string, unknown> = {
      name,
      description,
      price_usdc: priceUsdc,
      type: options.serviceType ?? "text",
    };
    if (options.endpoint) body.endpoint = options.endpoint;
    return this.post("/api/public/sell", body);
  }

  /** Buy and execute an AI service in one call. */
  async execute(
    serviceId: string,
    prompt: string,
    paymentTx?: string
  ): Promise<Record<string, unknown>> {
    this.requireKey();
    const body: Record<string, unknown> = {
      service_id: serviceId,
      prompt,
    };
    if (paymentTx) body.payment_tx = paymentTx;
    return this.post("/api/public/execute", body);
  }

  /** Negotiate the price of a service. */
  async negotiate(
    serviceId: string,
    proposedPrice: number,
    message?: string
  ): Promise<Record<string, unknown>> {
    this.requireKey();
    const body: Record<string, unknown> = {
      service_id: serviceId,
      proposed_price: proposedPrice,
    };
    if (message) body.message = message;
    return this.post("/api/public/negotiate", body);
  }

  /** Execute a crypto swap (supports 71 tokens across 5000+ pairs). */
  async swap(
    fromToken: string,
    toToken: string,
    amount: number,
    wallet: string
  ): Promise<Record<string, unknown>> {
    this.requireKey();
    return this.post("/api/public/crypto/swap", {
      from_token: fromToken,
      to_token: toToken,
      amount,
      wallet,
    });
  }

  /** Rent a GPU via Akash Network. */
  async gpuRent(
    tier: string,
    options: { durationHours?: number; wallet?: string } = {}
  ): Promise<Record<string, unknown>> {
    this.requireKey();
    const body: Record<string, unknown> = {
      tier,
      duration_hours: options.durationHours ?? 1,
    };
    if (options.wallet) body.wallet = options.wallet;
    return this.post("/api/public/gpu/rent", body);
  }

  // -----------------------------------------------------------------------
  // Prepaid Credits (off-chain micropayments, zero gas)
  // -----------------------------------------------------------------------

  /** Get prepaid credit balance. */
  async creditsBalance(): Promise<Record<string, unknown>> {
    this.requireKey();
    return this.get("/api/credits/balance");
  }

  /** Deposit USDC on-chain and receive prepaid credits. */
  async creditsDeposit(
    paymentTx: string,
    amountUsdc: number,
    chain = "solana"
  ): Promise<Record<string, unknown>> {
    this.requireKey();
    return this.post("/api/credits/deposit", {
      payment_tx: paymentTx,
      amount_usdc: amountUsdc,
      chain,
    });
  }

  // -----------------------------------------------------------------------
  // Solana DeFi (Lending, Staking)
  // -----------------------------------------------------------------------

  /** List Solana lending protocols with live APY rates. */
  async defiLending(): Promise<Record<string, unknown>> {
    return this.get("/api/defi/lending");
  }

  /** Find the best lending rate for an asset across all protocols. */
  async defiBestRate(asset = "USDC"): Promise<Record<string, unknown>> {
    return this.get("/api/defi/lending/best", { asset });
  }

  /** List Solana liquid staking protocols (Marinade, Jito, BlazeStake). */
  async defiStaking(): Promise<Record<string, unknown>> {
    return this.get("/api/defi/staking");
  }

  /** Lend an asset to earn interest. Returns unsigned tx for wallet signing. */
  async defiLend(
    protocol: string,
    asset: string,
    amount: number,
    wallet: string
  ): Promise<Record<string, unknown>> {
    this.requireKey();
    return this.post("/api/defi/lend", { protocol, asset, amount, wallet });
  }

  /** Stake SOL via liquid staking (Marinade, Jito, BlazeStake). */
  async defiStake(
    protocol: string,
    amount: number,
    wallet: string
  ): Promise<Record<string, unknown>> {
    this.requireKey();
    return this.post("/api/defi/stake", { protocol, amount, wallet });
  }

  // -----------------------------------------------------------------------
  // Streaming Payments (pay-per-second for GPU, LLM, etc.)
  // -----------------------------------------------------------------------

  /** Create a streaming payment (pay-per-second). */
  async streamCreate(
    receiver: string,
    ratePerHour: number,
    options: {
      maxHours?: number;
      serviceId?: string;
      paymentTx?: string;
    } = {}
  ): Promise<Record<string, unknown>> {
    this.requireKey();
    const body: Record<string, unknown> = {
      receiver,
      rate_per_hour: ratePerHour,
      max_hours: options.maxHours ?? 1,
    };
    if (options.serviceId) body.service_id = options.serviceId;
    if (options.paymentTx) body.payment_tx = options.paymentTx;
    return this.post("/api/stream/create", body);
  }

  /** Stop a streaming payment. Receiver keeps earned, payer gets refund. */
  async streamStop(streamId: string): Promise<Record<string, unknown>> {
    this.requireKey();
    return this.post("/api/stream/stop", { stream_id: streamId });
  }

  /** Get real-time status of a streaming payment. */
  async streamStatus(streamId: string): Promise<Record<string, unknown>> {
    return this.get(`/api/stream/${encodeURIComponent(streamId)}`);
  }

  // -----------------------------------------------------------------------
  // Lightning Payments
  // -----------------------------------------------------------------------

  /** Create a Lightning invoice for payment. */
  async createInvoice(
    amountUsd: number,
    description?: string
  ): Promise<{ lightning_invoice: string; charge_id: string }> {
    this.requireKey();
    const body: Record<string, unknown> = { amount_usd: amountUsd };
    if (description) body.description = description;
    return this.post("/api/lightning/invoice", body);
  }

  /** Check the payment status of a Lightning invoice. */
  async checkPayment(
    chargeId: string
  ): Promise<{ paid: boolean; status: string }> {
    return this.get(`/api/lightning/status/${encodeURIComponent(chargeId)}`);
  }

  // -----------------------------------------------------------------------
  // Sandbox (free testing environment)
  // -----------------------------------------------------------------------

  /** Execute a service in the free sandbox (no real payment needed). */
  async sandboxExecute(
    serviceId: string,
    prompt: string
  ): Promise<Record<string, unknown>> {
    this.requireKey();
    return this.post("/api/public/sandbox/execute", {
      service_id: serviceId,
      prompt,
    });
  }

  /** Get sandbox USDC balance. */
  async sandboxBalance(): Promise<{ balance_usdc: number }> {
    this.requireKey();
    return this.get("/api/public/sandbox/balance");
  }

  /** Reset sandbox balance back to the default amount. */
  async sandboxReset(): Promise<Record<string, unknown>> {
    this.requireKey();
    return this.post("/api/public/sandbox/reset");
  }
}
