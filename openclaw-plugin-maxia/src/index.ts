/**
 * MAXIA Plugin for OpenClaw
 *
 * Connects OpenClaw agents to the MAXIA AI-to-AI marketplace.
 * 14 blockchains, 71 tokens (5000+ pairs), GPU rental at cost,
 * tokenized stocks (25+), DeFi yields, and 17 native AI services.
 *
 * Installation:
 *   openclaw plugins install @maxia/openclaw-plugin
 *
 * Configuration (in openclaw config):
 *   plugins.entries.maxia-marketplace.config.apiKey = "your-key"
 *
 * Or set env var:
 *   MAXIA_API_KEY=your-key
 *
 * Get a free API key:
 *   POST https://maxiaworld.app/api/public/register
 *   Body: { "name": "my-agent", "wallet": "SOLANA_WALLET" }
 */

import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";
import { Type } from "@sinclair/typebox";

import { type MaxiaPluginConfig, maxiaFetch, MAXIA_DEFAULT_URL } from "./config.js";

// ── Action imports ──
import { discoverTool, handleDiscover, DiscoverParams } from "./actions/discover.js";
import { executeTool, handleExecute, ExecuteParams } from "./actions/execute.js";
import {
  swapQuoteTool, handleSwapQuote, SwapQuoteParams,
  swapExecuteTool, handleSwapExecute, SwapExecuteParams,
  pricesTool, handlePrices,
} from "./actions/swap.js";
import {
  gpuTiersTool, handleGpuTiers, GpuTiersParams,
  gpuCompareTool, handleGpuCompare, GpuCompareParams,
  gpuRentTool, handleGpuRent, GpuRentParams,
  gpuInstancesTool, handleGpuInstances, GpuInstancesParams,
  gpuTerminateTool, handleGpuTerminate, GpuTerminateParams,
} from "./actions/gpu.js";
import {
  stocksListTool, handleStocksList, StocksListParams,
  stockPriceTool, handleStockPrice, StockPriceParams,
  stockBuyTool, handleStockBuy, StockBuyParams,
  stockSellTool, handleStockSell, StockSellParams,
  stockPortfolioTool, handleStockPortfolio, StockPortfolioParams,
} from "./actions/stocks.js";

// ── Helper: resolve config from plugin context ──

function resolveConfig(api: { pluginConfig: Record<string, unknown> }): MaxiaPluginConfig {
  const apiKey =
    (api.pluginConfig?.apiKey as string) ||
    process.env.MAXIA_API_KEY ||
    "";
  const baseUrl =
    (api.pluginConfig?.baseUrl as string) ||
    process.env.MAXIA_BASE_URL ||
    MAXIA_DEFAULT_URL;

  return { apiKey, baseUrl };
}

// ── Helper: wrap a MAXIA handler into an OpenClaw tool execute function ──

function wrapHandler<P>(
  handler: (params: P, config: MaxiaPluginConfig) => Promise<string>,
  configRef: { current: MaxiaPluginConfig },
) {
  return async (_id: string, params: P) => {
    const text = await handler(params, configRef.current);
    return { content: [{ type: "text" as const, text }] };
  };
}

// ── Plugin entry point ──

export default definePluginEntry({
  id: "maxia-marketplace",
  name: "MAXIA Marketplace",
  description:
    "AI-to-AI marketplace on 14 blockchains. " +
    "Discover/buy AI services, swap 71 tokens, rent GPUs at cost, " +
    "trade tokenized stocks, track DeFi yields.",

  register(api) {
    const config = resolveConfig(api);
    const configRef = { current: config };

    api.logger.info(
      `MAXIA plugin loaded — base=${config.baseUrl}, ` +
      `auth=${config.apiKey ? "configured" : "none (register at /api/public/register)"}`,
    );

    // ── Auto-register: announce agent presence on startup ──
    if (config.apiKey) {
      maxiaFetch("/api/public/services", config)
        .then(() => api.logger.info("MAXIA API connection verified."))
        .catch((err: Error) => api.logger.warn(`MAXIA API check failed: ${err.message}`));
    }

    // ────────────────────────────────────────────────
    // 1. DISCOVER — Find AI services on the marketplace
    // ────────────────────────────────────────────────
    api.registerTool({
      name: discoverTool.name,
      description: discoverTool.description,
      parameters: DiscoverParams,
      execute: wrapHandler(handleDiscover, configRef),
    });

    // ────────────────────────────────────────────────
    // 2. EXECUTE — Buy and run an AI service
    // ────────────────────────────────────────────────
    api.registerTool({
      name: executeTool.name,
      description: executeTool.description,
      parameters: ExecuteParams,
      execute: wrapHandler(handleExecute, configRef),
    });

    // ────────────────────────────────────────────────
    // 3. SWAP — Token exchange (Solana + EVM)
    // ────────────────────────────────────────────────
    api.registerTool({
      name: swapQuoteTool.name,
      description: swapQuoteTool.description,
      parameters: SwapQuoteParams,
      execute: wrapHandler(handleSwapQuote, configRef),
    });

    api.registerTool({
      name: swapExecuteTool.name,
      description: swapExecuteTool.description,
      parameters: SwapExecuteParams,
      execute: wrapHandler(handleSwapExecute, configRef),
    });

    api.registerTool({
      name: pricesTool.name,
      description: pricesTool.description,
      parameters: Type.Object({}),
      execute: wrapHandler(handlePrices, configRef),
    });

    // ────────────────────────────────────────────────
    // 4. GPU — Rental at cost (0% markup)
    // ────────────────────────────────────────────────
    api.registerTool({
      name: gpuTiersTool.name,
      description: gpuTiersTool.description,
      parameters: GpuTiersParams,
      execute: wrapHandler(handleGpuTiers, configRef),
    });

    api.registerTool({
      name: gpuCompareTool.name,
      description: gpuCompareTool.description,
      parameters: GpuCompareParams,
      execute: wrapHandler(handleGpuCompare, configRef),
    });

    api.registerTool({
      name: gpuRentTool.name,
      description: gpuRentTool.description,
      parameters: GpuRentParams,
      execute: wrapHandler(handleGpuRent, configRef),
    });

    api.registerTool(
      {
        name: gpuInstancesTool.name,
        description: gpuInstancesTool.description,
        parameters: GpuInstancesParams,
        execute: wrapHandler(handleGpuInstances, configRef),
      },
      { optional: true },
    );

    api.registerTool(
      {
        name: gpuTerminateTool.name,
        description: gpuTerminateTool.description,
        parameters: GpuTerminateParams,
        execute: wrapHandler(handleGpuTerminate, configRef),
      },
      { optional: true },
    );

    // ────────────────────────────────────────────────
    // 5. STOCKS — Tokenized US stocks
    // ────────────────────────────────────────────────
    api.registerTool({
      name: stocksListTool.name,
      description: stocksListTool.description,
      parameters: StocksListParams,
      execute: wrapHandler(handleStocksList, configRef),
    });

    api.registerTool({
      name: stockPriceTool.name,
      description: stockPriceTool.description,
      parameters: StockPriceParams,
      execute: wrapHandler(handleStockPrice, configRef),
    });

    api.registerTool({
      name: stockBuyTool.name,
      description: stockBuyTool.description,
      parameters: StockBuyParams,
      execute: wrapHandler(handleStockBuy, configRef),
    });

    api.registerTool(
      {
        name: stockSellTool.name,
        description: stockSellTool.description,
        parameters: StockSellParams,
        execute: wrapHandler(handleStockSell, configRef),
      },
      { optional: true },
    );

    api.registerTool(
      {
        name: stockPortfolioTool.name,
        description: stockPortfolioTool.description,
        parameters: StockPortfolioParams,
        execute: wrapHandler(handleStockPortfolio, configRef),
      },
      { optional: true },
    );

    api.logger.info(
      `MAXIA plugin registered 15 tools: ` +
      `discover, execute, swap_quote, swap_execute, prices, ` +
      `gpu_tiers, gpu_compare, gpu_rent, gpu_instances, gpu_terminate, ` +
      `stocks_list, stock_price, stock_buy, stock_sell, stock_portfolio`,
    );
  },
});

// ── Re-export for programmatic use outside OpenClaw ──

export { type MaxiaPluginConfig, maxiaFetch } from "./config.js";
export {
  discoverTool,
  executeTool,
  swapQuoteTool,
  swapExecuteTool,
  pricesTool,
  gpuTiersTool,
  gpuCompareTool,
  gpuRentTool,
  gpuInstancesTool,
  gpuTerminateTool,
  stocksListTool,
  stockPriceTool,
  stockBuyTool,
  stockSellTool,
  stockPortfolioTool,
};
