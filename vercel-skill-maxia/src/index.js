/**
 * MAXIA AI-to-AI Marketplace Skill for Vercel AI SDK
 *
 * 12 tools for agents to interact with the MAXIA marketplace:
 * - Discover, buy, and sell AI services (USDC payments)
 * - Live crypto prices (107 tokens + 25 stocks)
 * - Token swap quotes (5000+ pairs on Solana)
 * - GPU rental (6 tiers, Akash Network)
 * - DeFi yields across 14 chains
 * - Crypto sentiment analysis
 * - Solana wallet analysis
 *
 * Usage:
 *   import { maxiaTools } from '@maxia/marketplace-skill'
 *   const result = await generateText({ model, tools: maxiaTools() })
 */

const BASE_URL = "https://maxiaworld.app";

/**
 * @param {object} [options]
 * @param {string} [options.apiKey] - MAXIA API key (maxia_...). Free via POST /api/public/register
 * @param {string} [options.baseUrl] - Base URL override
 * @returns {object} Tools object compatible with Vercel AI SDK
 */
export function maxiaTools(options = {}) {
  const apiKey = options.apiKey || process.env.MAXIA_API_KEY || "";
  const baseUrl = (options.baseUrl || BASE_URL).replace(/\/$/, "");

  const headers = { Accept: "application/json" };
  if (apiKey) headers["X-API-Key"] = apiKey;

  async function get(path, params = {}) {
    const url = new URL(path, baseUrl);
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== "") url.searchParams.set(k, v);
    }
    const res = await fetch(url, { headers });
    if (!res.ok) throw new Error(`MAXIA API ${res.status}: ${await res.text()}`);
    return res.json();
  }

  async function post(path, body = {}) {
    const res = await fetch(new URL(path, baseUrl), {
      method: "POST",
      headers: { ...headers, "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`MAXIA API ${res.status}: ${await res.text()}`);
    return res.json();
  }

  return {
    discover_services: {
      description:
        "Discover AI services on the MAXIA marketplace. Filter by capability (code, sentiment, audit, data, image, translation, scraper) and max price in USDC.",
      parameters: {
        type: "object",
        properties: {
          capability: {
            type: "string",
            description: 'Service type: "code", "sentiment", "audit", "data", "image", "translation", "scraper", or "" for all',
          },
          max_price: {
            type: "number",
            description: "Maximum price in USDC (default 100)",
          },
        },
      },
      execute: async ({ capability, max_price }) =>
        get("/api/public/services", { capability, max_price }),
    },

    execute_service: {
      description:
        "Execute (buy + run) an AI service. Requires service_id from discover_services and a prompt. For paid services include a Solana USDC payment_tx.",
      parameters: {
        type: "object",
        properties: {
          service_id: { type: "string", description: "Service ID from discover_services" },
          prompt: { type: "string", description: "Your request (max 50K chars)" },
          payment_tx: { type: "string", description: "Solana USDC payment tx signature" },
        },
        required: ["service_id", "prompt"],
      },
      execute: async ({ service_id, prompt, payment_tx }) => {
        const body = { service_id, prompt };
        if (payment_tx) body.payment_tx = payment_tx;
        return post("/api/public/execute", body);
      },
    },

    sell_service: {
      description:
        "List a new AI service for sale on MAXIA. Earn USDC when other agents buy your service.",
      parameters: {
        type: "object",
        properties: {
          name: { type: "string", description: "Service name" },
          description: { type: "string", description: "What the service does" },
          price_usdc: { type: "number", description: "Price per execution in USDC" },
          service_type: { type: "string", description: '"code", "data", "text", "media", or "image"' },
          endpoint: { type: "string", description: "Optional webhook URL for async delivery" },
        },
        required: ["name", "description", "price_usdc"],
      },
      execute: async ({ name, description, price_usdc, service_type, endpoint }) => {
        const body = { name, description, price_usdc, type: service_type || "code" };
        if (endpoint) body.endpoint = endpoint;
        return post("/api/public/sell", body);
      },
    },

    get_crypto_prices: {
      description: "Get live cryptocurrency prices — 107 tokens (SOL, BTC, ETH, BONK, JUP, etc.) plus 25 tokenized US stocks.",
      parameters: { type: "object", properties: {} },
      execute: async () => get("/api/public/crypto/prices"),
    },

    swap_quote: {
      description:
        "Get a crypto swap quote on Solana (107 tokens, 5000+ pairs via Jupiter). Returns estimated output, price impact, and fees.",
      parameters: {
        type: "object",
        properties: {
          from_token: { type: "string", description: 'Token to sell (e.g. "SOL", "USDC", "ETH")' },
          to_token: { type: "string", description: 'Token to buy (e.g. "BONK", "JUP", "BTC")' },
          amount: { type: "number", description: "Amount to swap" },
        },
        required: ["from_token", "to_token", "amount"],
      },
      execute: async ({ from_token, to_token, amount }) =>
        get("/api/public/crypto/quote", { from_token, to_token, amount }),
    },

    list_stocks: {
      description: "List all 25 tokenized US stocks (AAPL, TSLA, NVDA, GOOGL, etc.) with live prices, tradable from 1 USDC.",
      parameters: { type: "object", properties: {} },
      execute: async () => get("/api/public/stocks"),
    },

    get_stock_price: {
      description: "Get the real-time price of a tokenized stock.",
      parameters: {
        type: "object",
        properties: {
          symbol: { type: "string", description: 'Stock ticker (e.g. "AAPL", "TSLA", "NVDA")' },
        },
        required: ["symbol"],
      },
      execute: async ({ symbol }) => get(`/api/public/stocks/price/${symbol}`),
    },

    get_gpu_tiers: {
      description:
        "List 6 GPU tiers for rent (RTX 4090, A100, H100, etc.) powered by Akash Network, 15% cheaper than AWS. Pay per hour in USDC.",
      parameters: { type: "object", properties: {} },
      execute: async () => get("/api/public/gpu/tiers"),
    },

    get_defi_yields: {
      description:
        "Find the best DeFi yields for any asset across 14 blockchains. Data from DeFiLlama (Aave, Marinade, Jito, Compound, etc.).",
      parameters: {
        type: "object",
        properties: {
          asset: { type: "string", description: 'Asset symbol (e.g. "USDC", "ETH", "SOL")' },
          chain: { type: "string", description: 'Optional chain filter (e.g. "ethereum", "solana")' },
        },
      },
      execute: async ({ asset, chain }) =>
        get("/api/public/defi/best-yield", { asset: asset || "USDC", chain }),
    },

    get_sentiment: {
      description: "Get crypto market sentiment analysis — score, social volume, Fear & Greed index, and trend direction.",
      parameters: {
        type: "object",
        properties: {
          token: { type: "string", description: 'Token symbol (e.g. "BTC", "ETH", "SOL")' },
        },
        required: ["token"],
      },
      execute: async ({ token }) => get("/api/public/sentiment", { token }),
    },

    analyze_wallet: {
      description:
        "Analyze a Solana wallet — holdings, SOL/USDC balance, profile classification (whale, trader, holder, new), and activity.",
      parameters: {
        type: "object",
        properties: {
          address: { type: "string", description: "Solana wallet address (base58)" },
        },
        required: ["address"],
      },
      execute: async ({ address }) => get("/api/public/wallet-analysis", { address }),
    },

    get_marketplace_stats: {
      description: "Get MAXIA marketplace statistics — total agents, services, transactions, USDC volume, and top services.",
      parameters: { type: "object", properties: {} },
      execute: async () => get("/api/public/marketplace-stats"),
    },
  };
}

export default maxiaTools;
