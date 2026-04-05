/**
 * MAXIA_SWAP — Get a swap quote for tokens across 7 chains.
 * Note: actual execution requires on-chain USDC payment — this action provides quotes.
 */
import type { Action, IAgentRuntime, Memory, State, HandlerCallback, HandlerOptions } from "@elizaos/core";
import { maxiaGet } from "../client.js";

interface QuoteData {
  from_token: string;
  to_token: string;
  amount: number;
  quote_amount: number;
  price: number;
  commission_pct: number;
  commission_usdc: number;
  route?: string;
}

const TOKEN_PATTERN = /\b(SOL|ETH|BTC|USDC|USDT|BONK|JUP|RAY|WIF|RNDR|HNT|PYTH|JTO|LINK|UNI|AAVE|ARB|OP|MATIC|AVAX|BNB|TON|SUI|TRX|NEAR|APT|SEI|XRP)\b/gi;
const AMOUNT_PATTERN = /(\d+(?:\.\d+)?)\s*(?:of\s+)?/i;

export const swapAction: Action = {
  name: "MAXIA_SWAP",
  similes: ["SWAP_TOKENS", "EXCHANGE_CRYPTO", "TRADE_TOKENS", "TOKEN_SWAP"],
  description:
    "Get a swap quote for exchanging tokens on MAXIA (Jupiter on Solana, 0x on 6 EVM chains). " +
    "Supports 65 tokens across 7 chains. Use when user wants to swap or trade crypto.",

  validate: async (_runtime: IAgentRuntime, message: Memory): Promise<boolean> => {
    const text = (message.content.text ?? "").toLowerCase();
    return /swap|exchange|trade|convert|buy.*token|sell.*token/i.test(text);
  },

  handler: async (
    runtime: IAgentRuntime,
    message: Memory,
    _state: State | undefined,
    _options: HandlerOptions | undefined,
    callback?: HandlerCallback,
  ): Promise<void> => {
    const text = message.content.text ?? "";

    // Extract tokens
    const tokens = text.match(TOKEN_PATTERN) ?? [];
    const fromToken = tokens[0]?.toUpperCase() ?? "SOL";
    const toToken = tokens[1]?.toUpperCase() ?? "USDC";

    // Extract amount
    const amountMatch = text.match(AMOUNT_PATTERN);
    const amount = amountMatch ? parseFloat(amountMatch[1]) : 1;

    const res = await maxiaGet<QuoteData>(runtime, "/api/public/crypto/quote", {
      from_token: fromToken,
      to_token: toToken,
      amount: String(amount),
    });

    if (!res.ok || !res.data) {
      await callback?.({ text: `Could not get swap quote: ${res.error ?? "unknown error"}` });
      return;
    }

    const q = res.data;
    const lines = [
      `**Swap Quote**: ${amount} ${fromToken} -> ${toToken}`,
      `You receive: **${q.quote_amount?.toLocaleString("en-US", { maximumFractionDigits: 6 })} ${toToken}**`,
      `Price: $${q.price?.toLocaleString("en-US", { maximumFractionDigits: 6 })}`,
      `Commission: ${q.commission_pct}% ($${q.commission_usdc?.toFixed(4)} USDC)`,
      q.route ? `Route: ${q.route}` : "",
      "",
      "_To execute this swap, send USDC payment to MAXIA and call /api/public/crypto/swap_",
    ].filter(Boolean);

    await callback?.({
      text: lines.join("\n"),
      content: { quote: q },
    });
  },

  examples: [
    [
      { name: "user", content: { text: "Swap 10 SOL to USDC" } },
      {
        name: "agent",
        content: { text: "**Swap Quote**: 10 SOL -> USDC\nYou receive: **1,425.00 USDC**...", action: "MAXIA_SWAP" },
      },
    ],
    [
      { name: "user", content: { text: "How much ETH can I get for 100 USDC?" } },
      {
        name: "agent",
        content: { text: "**Swap Quote**: 100 USDC -> ETH\nYou receive: **0.03125 ETH**...", action: "MAXIA_SWAP" },
      },
    ],
  ],
};
