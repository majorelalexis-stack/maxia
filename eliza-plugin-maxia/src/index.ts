/**
 * MAXIA Plugin for ElizaOS
 *
 * Connects ElizaOS agents to the MAXIA AI-to-AI marketplace.
 * 14 blockchains, 50 token swap, GPU rental, LLM fine-tuning, tokenized stocks.
 *
 * Usage in ElizaOS character config:
 *   plugins: ["@maxia/eliza-plugin"]
 *   settings: { MAXIA_API_KEY: "your-key" }
 */

const MAXIA_BASE_URL = "https://maxiaworld.app";

interface MaxiaConfig {
  apiKey: string;
  baseUrl?: string;
}

async function maxiaFetch(path: string, config: MaxiaConfig, options: RequestInit = {}): Promise<any> {
  const url = `${config.baseUrl || MAXIA_BASE_URL}${path}`;
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(config.apiKey ? { "X-API-Key": config.apiKey } : {}),
    ...(options.headers as Record<string, string> || {}),
  };

  const resp = await fetch(url, { ...options, headers });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`MAXIA API error ${resp.status}: ${text}`);
  }
  return resp.json();
}

// ── Actions ──

const discoverAction = {
  name: "MAXIA_DISCOVER",
  description: "Find AI services on MAXIA marketplace by capability (audit, code, data, image, text, sentiment, scraper)",
  similes: ["find service", "search marketplace", "discover agent", "list services", "browse marketplace"],
  examples: [
    [{ user: "user1", content: { text: "find a code review service on MAXIA" } }],
    [{ user: "user1", content: { text: "what AI services are available?" } }],
  ],
  validate: async () => true,
  handler: async (runtime: any, message: any, state: any) => {
    const config: MaxiaConfig = { apiKey: runtime.getSetting("MAXIA_API_KEY") || "" };
    const text = message.content.text.toLowerCase();

    let capability = "";
    for (const cap of ["audit", "code", "data", "image", "text", "sentiment", "scraper", "finetune"]) {
      if (text.includes(cap)) { capability = cap; break; }
    }

    const result = await maxiaFetch(`/api/public/discover?capability=${capability}&max_price=100`, config);
    return { text: JSON.stringify(result, null, 2) };
  },
};

const swapAction = {
  name: "MAXIA_SWAP",
  description: "Get crypto swap quotes (50 tokens, 2450 pairs on Solana)",
  similes: ["swap", "exchange", "convert crypto", "trade tokens"],
  examples: [
    [{ user: "user1", content: { text: "swap 10 SOL to USDC" } }],
  ],
  validate: async () => true,
  handler: async (runtime: any, message: any) => {
    const config: MaxiaConfig = { apiKey: runtime.getSetting("MAXIA_API_KEY") || "" };
    const result = await maxiaFetch("/api/public/crypto/prices", config);
    return { text: `Current prices:\n${JSON.stringify(result, null, 2)}\n\nUse POST /api/public/crypto/swap to execute.` };
  },
};

const gpuAction = {
  name: "MAXIA_GPU",
  description: "List GPU tiers and pricing for rental (RTX4090 to H200)",
  similes: ["rent gpu", "gpu pricing", "gpu rental", "compute"],
  examples: [
    [{ user: "user1", content: { text: "what GPUs can I rent?" } }],
  ],
  validate: async () => true,
  handler: async (runtime: any) => {
    const config: MaxiaConfig = { apiKey: runtime.getSetting("MAXIA_API_KEY") || "" };
    const result = await maxiaFetch("/api/public/gpu/tiers", config);
    return { text: JSON.stringify(result, null, 2) };
  },
};

const finetuneAction = {
  name: "MAXIA_FINETUNE",
  description: "Fine-tune LLMs (Llama, Qwen, Mistral, Gemma, DeepSeek) on your dataset via Unsloth",
  similes: ["fine-tune", "train model", "finetune llm", "custom model"],
  examples: [
    [{ user: "user1", content: { text: "what models can I fine-tune?" } }],
  ],
  validate: async () => true,
  handler: async (runtime: any) => {
    const config: MaxiaConfig = { apiKey: runtime.getSetting("MAXIA_API_KEY") || "" };
    const result = await maxiaFetch("/api/finetune/models", config);
    return { text: JSON.stringify(result, null, 2) };
  },
};

const yieldsAction = {
  name: "MAXIA_YIELDS",
  description: "Find best DeFi yields across 14 chains",
  similes: ["best yields", "defi apy", "earn interest", "staking rates"],
  examples: [
    [{ user: "user1", content: { text: "best USDC yields" } }],
  ],
  validate: async () => true,
  handler: async (runtime: any, message: any) => {
    const config: MaxiaConfig = { apiKey: runtime.getSetting("MAXIA_API_KEY") || "" };
    const text = message.content.text.toLowerCase();
    let asset = "USDC";
    for (const a of ["ETH", "SOL", "BTC"]) {
      if (text.includes(a.toLowerCase())) { asset = a; break; }
    }
    const result = await maxiaFetch(`/api/yields/best?asset=${asset}&limit=10`, config);
    return { text: JSON.stringify(result, null, 2) };
  },
};

const stocksAction = {
  name: "MAXIA_STOCKS",
  description: "Trade tokenized US stocks (AAPL, TSLA, NVDA, etc.)",
  similes: ["stock price", "buy stock", "tokenized stocks", "trade stocks"],
  examples: [
    [{ user: "user1", content: { text: "what's the price of TSLA?" } }],
  ],
  validate: async () => true,
  handler: async (runtime: any) => {
    const config: MaxiaConfig = { apiKey: runtime.getSetting("MAXIA_API_KEY") || "" };
    const result = await maxiaFetch("/api/public/stocks", config);
    return { text: JSON.stringify(result, null, 2) };
  },
};

const statsAction = {
  name: "MAXIA_STATS",
  description: "Get MAXIA marketplace statistics",
  similes: ["marketplace stats", "maxia stats", "how many agents"],
  examples: [
    [{ user: "user1", content: { text: "show me MAXIA marketplace stats" } }],
  ],
  validate: async () => true,
  handler: async (runtime: any) => {
    const config: MaxiaConfig = { apiKey: runtime.getSetting("MAXIA_API_KEY") || "" };
    const result = await maxiaFetch("/api/public/marketplace-stats", config);
    return { text: JSON.stringify(result, null, 2) };
  },
};

// ── Plugin Export ──

export const maxiaPlugin = {
  name: "@maxia/eliza-plugin",
  description: "MAXIA AI-to-AI Marketplace — 14 chains, 50 tokens, GPU rental, LLM fine-tuning, tokenized stocks, DeFi yields",
  actions: [
    discoverAction,
    swapAction,
    gpuAction,
    finetuneAction,
    yieldsAction,
    stocksAction,
    statsAction,
  ],
  evaluators: [],
  providers: [],
};

export default maxiaPlugin;
