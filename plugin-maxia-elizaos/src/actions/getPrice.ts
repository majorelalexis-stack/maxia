/**
 * MAXIA_GET_PRICE — Fetch live token price from MAXIA oracle (Pyth + CoinGecko).
 */
import type { Action, IAgentRuntime, Memory, State, HandlerCallback, HandlerOptions } from "@elizaos/core";
import { maxiaGet } from "../client.js";

interface PriceData {
  [token: string]: { usd: number; source?: string };
}

const TOKEN_PATTERN = /\b(SOL|ETH|BTC|USDC|USDT|BONK|JUP|RAY|WIF|RNDR|HNT|PYTH|JTO|ONDO|W|LINK|UNI|AAVE|ARB|OP|MATIC|AVAX|BNB|TON|SUI|TRX|NEAR|APT|SEI|XRP)\b/i;

export const getPriceAction: Action = {
  name: "MAXIA_GET_PRICE",
  similes: ["CHECK_PRICE", "TOKEN_PRICE", "CRYPTO_PRICE", "PRICE_CHECK"],
  description:
    "Get the live price of a cryptocurrency token from MAXIA's multi-source oracle " +
    "(Pyth Network SSE, CoinGecko, Helius). Use when user asks about token prices.",

  validate: async (_runtime: IAgentRuntime, message: Memory): Promise<boolean> => {
    const text = (message.content.text ?? "").toLowerCase();
    return /price|worth|cost|how much|value|cours|prix/i.test(text);
  },

  handler: async (
    runtime: IAgentRuntime,
    message: Memory,
    _state: State | undefined,
    _options: HandlerOptions | undefined,
    callback?: HandlerCallback,
  ): Promise<void> => {
    const text = message.content.text ?? "";
    const match = text.match(TOKEN_PATTERN);
    const token = match ? match[1].toUpperCase() : "SOL";

    const res = await maxiaGet<PriceData>(runtime, "/api/public/crypto/prices");

    if (!res.ok || !res.data) {
      await callback?.({ text: `Could not fetch prices: ${res.error ?? "unknown error"}` });
      return;
    }

    const tokenKey = token.toLowerCase();
    const entry = res.data[tokenKey] ?? res.data[token];

    if (!entry) {
      // List available tokens
      const available = Object.keys(res.data).slice(0, 20).map((t) => t.toUpperCase()).join(", ");
      await callback?.({ text: `Token ${token} not found. Available: ${available}` });
      return;
    }

    const price = typeof entry === "number" ? entry : entry.usd;
    const source = typeof entry === "object" ? entry.source ?? "MAXIA oracle" : "MAXIA oracle";

    await callback?.({
      text: `**${token}**: $${price.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 6 })} (source: ${source})`,
      content: { token, price, source },
    });
  },

  examples: [
    [
      { name: "user", content: { text: "What is the price of SOL?" } },
      { name: "agent", content: { text: "**SOL**: $142.50 (source: Pyth Network)", action: "MAXIA_GET_PRICE" } },
    ],
    [
      { name: "user", content: { text: "How much is ETH worth?" } },
      { name: "agent", content: { text: "**ETH**: $3,200.00 (source: Pyth Network)", action: "MAXIA_GET_PRICE" } },
    ],
  ],
};
