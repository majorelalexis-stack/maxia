/**
 * MAXIA Discover Action
 *
 * Lets OpenClaw agents discover AI services available on the MAXIA marketplace.
 * Supports filtering by capability (code, audit, data, image, text, sentiment,
 * scraper, finetune) and maximum price in USDC.
 *
 * Endpoint: GET /api/public/discover?capability=...&max_price=...
 * Auth: not required (public endpoint)
 */

import { Type, type Static } from "@sinclair/typebox";
import { type MaxiaPluginConfig, maxiaFetch } from "../config.js";

// ── Parameter schema ──

export const DiscoverParams = Type.Object({
  capability: Type.Optional(
    Type.String({
      description:
        "Filter by capability: audit, code, data, image, text, sentiment, scraper, finetune. Leave empty for all.",
    }),
  ),
  max_price: Type.Optional(
    Type.Number({
      description: "Maximum price in USDC. Defaults to 100.",
      minimum: 0,
    }),
  ),
});

export type DiscoverInput = Static<typeof DiscoverParams>;

// ── Handler ──

export async function handleDiscover(
  params: DiscoverInput,
  config: MaxiaPluginConfig,
): Promise<string> {
  const capability = params.capability?.trim() || "";
  const maxPrice = params.max_price ?? 100;

  const qs = new URLSearchParams();
  if (capability) qs.set("capability", capability);
  qs.set("max_price", String(maxPrice));

  const result = await maxiaFetch(`/api/public/discover?${qs}`, config);

  return JSON.stringify(result, null, 2);
}

// ── Tool definition (consumed by index.ts) ──

export const discoverTool = {
  name: "maxia_discover",
  description:
    "Discover AI services on the MAXIA marketplace (14 blockchains). " +
    "Filter by capability (code, audit, data, image, text, sentiment, scraper, finetune) " +
    "and max price in USDC. Returns service names, descriptions, pricing, and IDs.",
  parameters: DiscoverParams,
  handler: handleDiscover,
};
