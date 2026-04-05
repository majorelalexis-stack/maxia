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
declare const maxiaPlugin: Plugin;
export default maxiaPlugin;
