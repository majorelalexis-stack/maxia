/**
 * MAXIA Swap Action
 *
 * Token swap across Solana (Jupiter) and EVM chains (Base, Ethereum, Polygon, etc.).
 * Two operations:
 *   1. Quote — free, no auth: GET /api/public/crypto/quote
 *   2. Execute swap — requires auth + on-chain payment: POST /api/public/crypto/swap
 *
 * 71 tokens, 5000+ pairs. Commission: 0.01% (WHALE) to 0.10% (BRONZE).
 */

import { Type, type Static } from "@sinclair/typebox";
import { type MaxiaPluginConfig, maxiaFetch } from "../config.js";

// ── Quote parameters ──

export const SwapQuoteParams = Type.Object({
  from_token: Type.String({
    description: "Source token symbol (e.g. SOL, ETH, USDC, WBTC, BONK).",
  }),
  to_token: Type.String({
    description: "Destination token symbol.",
  }),
  amount: Type.Number({
    description: "Amount of from_token to swap.",
    minimum: 0,
  }),
  chain: Type.Optional(
    Type.String({
      description: "Chain for the swap: solana (default), base, ethereum, polygon, arbitrum, bnb, avalanche.",
    }),
  ),
});

export type SwapQuoteInput = Static<typeof SwapQuoteParams>;

// ── Execute swap parameters ──

export const SwapExecuteParams = Type.Object({
  from_token: Type.String({ description: "Source token symbol." }),
  to_token: Type.String({ description: "Destination token symbol." }),
  amount: Type.Number({ description: "Amount of from_token to swap.", minimum: 0 }),
  payment_tx: Type.String({
    description: "On-chain USDC transaction signature covering the swap fee.",
  }),
  chain: Type.Optional(
    Type.String({
      description: "Chain: solana (default), base, ethereum, polygon, arbitrum, bnb, avalanche.",
    }),
  ),
  slippage_bps: Type.Optional(
    Type.Number({
      description: "Maximum slippage in basis points (default 50 = 0.5%).",
      minimum: 1,
      maximum: 1000,
    }),
  ),
});

export type SwapExecuteInput = Static<typeof SwapExecuteParams>;

// ── Handlers ──

export async function handleSwapQuote(
  params: SwapQuoteInput,
  config: MaxiaPluginConfig,
): Promise<string> {
  const qs = new URLSearchParams({
    from_token: params.from_token.toUpperCase(),
    to_token: params.to_token.toUpperCase(),
    amount: String(params.amount),
  });
  if (params.chain) qs.set("chain", params.chain.toLowerCase());

  const result = await maxiaFetch(`/api/public/crypto/quote?${qs}`, config);
  return JSON.stringify(result, null, 2);
}

export async function handleSwapExecute(
  params: SwapExecuteInput,
  config: MaxiaPluginConfig,
): Promise<string> {
  if (!config.apiKey) {
    throw new Error("MAXIA API key required for swap execution.");
  }

  const body: Record<string, unknown> = {
    from_token: params.from_token.toUpperCase(),
    to_token: params.to_token.toUpperCase(),
    amount: params.amount,
    payment_tx: params.payment_tx,
  };
  if (params.chain) body.chain = params.chain.toLowerCase();
  if (params.slippage_bps) body.slippage_bps = params.slippage_bps;

  const result = await maxiaFetch("/api/public/crypto/swap", config, {
    method: "POST",
    body: JSON.stringify(body),
  });

  return JSON.stringify(result, null, 2);
}

// ── Bonus: prices tool ──

export async function handlePrices(
  _params: Record<string, never>,
  config: MaxiaPluginConfig,
): Promise<string> {
  const result = await maxiaFetch("/api/public/crypto/prices", config);
  return JSON.stringify(result, null, 2);
}

// ── Tool definitions ──

export const swapQuoteTool = {
  name: "maxia_swap_quote",
  description:
    "Get a swap quote for crypto tokens on MAXIA (71 tokens, 5000+ pairs). " +
    "Supports Solana (Jupiter) and EVM chains (Base, Ethereum, Polygon, Arbitrum, BNB, Avalanche). " +
    "Returns estimated output amount, price impact, and fees. Free, no auth required.",
  parameters: SwapQuoteParams,
  handler: handleSwapQuote,
};

export const swapExecuteTool = {
  name: "maxia_swap_execute",
  description:
    "Execute a token swap on MAXIA. Requires an API key and an on-chain USDC payment_tx. " +
    "Commission: 0.01% (WHALE) to 0.10% (BRONZE). Supports Solana and EVM chains.",
  parameters: SwapExecuteParams,
  handler: handleSwapExecute,
};

export const pricesTool = {
  name: "maxia_prices",
  description:
    "Get live crypto prices for 71 tokens and 10 tokenized stocks from MAXIA. Free, no auth required.",
  parameters: Type.Object({}),
  handler: handlePrices,
};
