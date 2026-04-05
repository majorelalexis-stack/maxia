/**
 * @maxia/plugin-marketplace — ElizaOS plugin for the MAXIA AI-to-AI marketplace.
 *
 * Actions:
 *   MAXIA_DISCOVER   — Search services on the marketplace
 *   MAXIA_GET_PRICE  — Live token prices (Pyth + CoinGecko)
 *   MAXIA_SWAP       — Token swap quotes (65 tokens, 7 chains)
 *   MAXIA_RENT_GPU   — GPU tiers and pricing (Akash Network)
 *   MAXIA_BUY_SERVICE — Execute AI services (sandbox mode)
 *
 * Provider:
 *   MAXIA_MARKET_CONTEXT — Injects marketplace state into agent prompt
 */
import type { Plugin } from "@elizaos/core";
import { elizaLogger } from "@elizaos/core";

import { discoverAction } from "./actions/discover.js";
import { getPriceAction } from "./actions/getPrice.js";
import { swapAction } from "./actions/swap.js";
import { rentGpuAction } from "./actions/rentGpu.js";
import { buyServiceAction } from "./actions/buyService.js";
import { marketContextProvider } from "./providers/marketContext.js";

const maxiaPlugin: Plugin = {
  name: "plugin-maxia",
  description:
    "MAXIA AI-to-AI marketplace — swap tokens, rent GPUs, buy/sell AI services with USDC escrow on 15 blockchains",

  init: async (config: Record<string, string>) => {
    const key = config.MAXIA_API_KEY || "";
    const url = config.MAXIA_API_URL || "https://maxiaworld.app";
    if (!key) {
      elizaLogger.warn("[plugin-maxia] MAXIA_API_KEY not set — some actions will fail");
    }
    elizaLogger.info(`[plugin-maxia] Initialized (API: ${url})`);
  },

  actions: [
    discoverAction,
    getPriceAction,
    swapAction,
    rentGpuAction,
    buyServiceAction,
  ],

  providers: [marketContextProvider],
};

export default maxiaPlugin;
