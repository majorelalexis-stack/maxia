/**
 * MAXIA_MARKET_CONTEXT — Injects MAXIA marketplace state into agent prompt.
 * Runs before every LLM call so the agent is aware of MAXIA capabilities.
 */
import type { Provider, IAgentRuntime, Memory, State, ProviderResult } from "@elizaos/core";
import { elizaLogger } from "@elizaos/core";
import { maxiaGet } from "../client.js";

interface PriceInfo {
  services?: Record<string, number>;
  gpu_tiers?: Array<{ name: string; base_price_per_hour: number }>;
  commissions?: Record<string, string>;
}

// Cache to avoid hitting the API on every message
let _cache: { data: string; ts: number } | null = null;
const CACHE_TTL_MS = 5 * 60 * 1000; // 5 minutes

export const marketContextProvider: Provider = {
  name: "MAXIA_MARKET_CONTEXT",

  get: async (
    runtime: IAgentRuntime,
    _message: Memory,
    _state: State,
  ): Promise<ProviderResult> => {
    // Return cached if fresh
    if (_cache && Date.now() - _cache.ts < CACHE_TTL_MS) {
      return { text: _cache.data };
    }

    try {
      const res = await maxiaGet<PriceInfo>(runtime, "/api/public/prices");

      if (!res.ok || !res.data) {
        return { text: "MAXIA marketplace: data unavailable." };
      }

      const d = res.data;
      const lines = [
        "=== MAXIA AI Marketplace ===",
        "You can help users with: AI services, token swaps (65 tokens, 7 chains), GPU rental (Akash), price checks.",
        "",
      ];

      if (d.services) {
        lines.push("Services:");
        for (const [name, price] of Object.entries(d.services)) {
          lines.push(`  ${name}: $${price} USDC`);
        }
      }

      if (d.gpu_tiers) {
        lines.push("GPU Tiers:");
        for (const tier of d.gpu_tiers.slice(0, 4)) {
          lines.push(`  ${tier.name}: $${tier.base_price_per_hour}/h`);
        }
      }

      lines.push("============================");

      const result = lines.join("\n");
      _cache = { data: result, ts: Date.now() };
      return { text: result };
    } catch (err) {
      elizaLogger.warn("[plugin-maxia] market context provider error:", String(err));
      return { text: "" };
    }
  },
};
