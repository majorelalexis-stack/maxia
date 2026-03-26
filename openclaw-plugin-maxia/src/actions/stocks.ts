/**
 * MAXIA Tokenized Stocks Action
 *
 * Trade fractional tokenized US stocks (AAPL, TSLA, NVDA, GOOGL, MSFT, AMZN, META, etc.)
 * with USDC on multiple chains via xStocks, Ondo, and Dinari protocols.
 *
 * Endpoints:
 *   GET  /api/public/stocks              — list all available stocks
 *   GET  /api/public/stocks/price/:sym   — live price of a stock in USDC
 *   POST /api/public/stocks/buy          — buy fractional shares (auth + payment_tx)
 *   POST /api/public/stocks/sell         — sell shares (auth)
 *   GET  /api/public/stocks/portfolio    — view holdings (auth)
 */

import { Type, type Static } from "@sinclair/typebox";
import { type MaxiaPluginConfig, maxiaFetch } from "../config.js";

// ── List stocks ──

export const StocksListParams = Type.Object({});

export async function handleStocksList(
  _params: Static<typeof StocksListParams>,
  config: MaxiaPluginConfig,
): Promise<string> {
  const result = await maxiaFetch("/api/public/stocks", config);
  return JSON.stringify(result, null, 2);
}

// ── Stock price ──

export const StockPriceParams = Type.Object({
  symbol: Type.String({
    description: "Stock ticker symbol (e.g. AAPL, TSLA, NVDA, GOOGL, MSFT, AMZN, META).",
  }),
});

export async function handleStockPrice(
  params: Static<typeof StockPriceParams>,
  config: MaxiaPluginConfig,
): Promise<string> {
  const sym = params.symbol.toUpperCase();
  const result = await maxiaFetch(`/api/public/stocks/price/${sym}`, config);
  return JSON.stringify(result, null, 2);
}

// ── Buy stock ──

export const StockBuyParams = Type.Object({
  symbol: Type.String({
    description: "Stock ticker symbol to buy.",
  }),
  amount_usdc: Type.Number({
    description: "Amount in USDC to spend on the stock.",
    minimum: 0.01,
  }),
  payment_tx: Type.String({
    description: "On-chain USDC transaction signature.",
  }),
  chain: Type.Optional(
    Type.String({
      description: "Chain for the purchase: solana, base, ethereum, polygon, arbitrum. Default: solana.",
    }),
  ),
});

export type StockBuyInput = Static<typeof StockBuyParams>;

export async function handleStockBuy(
  params: StockBuyInput,
  config: MaxiaPluginConfig,
): Promise<string> {
  if (!config.apiKey) {
    throw new Error("MAXIA API key required to buy stocks.");
  }

  const body: Record<string, unknown> = {
    symbol: params.symbol.toUpperCase(),
    amount_usdc: params.amount_usdc,
    payment_tx: params.payment_tx,
  };
  if (params.chain) body.chain = params.chain.toLowerCase();

  const result = await maxiaFetch("/api/public/stocks/buy", config, {
    method: "POST",
    body: JSON.stringify(body),
  });

  return JSON.stringify(result, null, 2);
}

// ── Sell stock ──

export const StockSellParams = Type.Object({
  symbol: Type.String({
    description: "Stock ticker symbol to sell.",
  }),
  shares: Type.Number({
    description: "Number of fractional shares to sell.",
    minimum: 0.0001,
  }),
});

export type StockSellInput = Static<typeof StockSellParams>;

export async function handleStockSell(
  params: StockSellInput,
  config: MaxiaPluginConfig,
): Promise<string> {
  if (!config.apiKey) {
    throw new Error("MAXIA API key required to sell stocks.");
  }

  const result = await maxiaFetch("/api/public/stocks/sell", config, {
    method: "POST",
    body: JSON.stringify({
      symbol: params.symbol.toUpperCase(),
      shares: params.shares,
    }),
  });

  return JSON.stringify(result, null, 2);
}

// ── Portfolio ──

export const StockPortfolioParams = Type.Object({});

export async function handleStockPortfolio(
  _params: Static<typeof StockPortfolioParams>,
  config: MaxiaPluginConfig,
): Promise<string> {
  if (!config.apiKey) {
    throw new Error("MAXIA API key required to view portfolio.");
  }
  const result = await maxiaFetch("/api/public/stocks/portfolio", config);
  return JSON.stringify(result, null, 2);
}

// ── Tool definitions ──

export const stocksListTool = {
  name: "maxia_stocks_list",
  description:
    "List all tokenized US stocks available on MAXIA (25+ stocks: AAPL, TSLA, NVDA, GOOGL, MSFT, AMZN, META...). " +
    "Multi-chain via xStocks, Ondo, and Dinari. Free, no auth required.",
  parameters: StocksListParams,
  handler: handleStocksList,
};

export const stockPriceTool = {
  name: "maxia_stock_price",
  description:
    "Get the live price of a tokenized stock in USDC. Free, no auth required.",
  parameters: StockPriceParams,
  handler: handleStockPrice,
};

export const stockBuyTool = {
  name: "maxia_stock_buy",
  description:
    "Buy fractional tokenized stock with USDC on MAXIA. " +
    "Requires API key and on-chain USDC payment_tx. Multi-chain support.",
  parameters: StockBuyParams,
  handler: handleStockBuy,
};

export const stockSellTool = {
  name: "maxia_stock_sell",
  description:
    "Sell tokenized stock shares on MAXIA. Requires API key.",
  parameters: StockSellParams,
  handler: handleStockSell,
};

export const stockPortfolioTool = {
  name: "maxia_stock_portfolio",
  description:
    "View your tokenized stock portfolio on MAXIA (holdings, P&L, current values). Requires API key.",
  parameters: StockPortfolioParams,
  handler: handleStockPortfolio,
};
