/**
 * MAXIA_DISCOVER — Search AI services on the MAXIA marketplace.
 */
import type { Action, IAgentRuntime, Memory, State, HandlerCallback, HandlerOptions } from "@elizaos/core";
import { elizaLogger } from "@elizaos/core";
import { maxiaGet } from "../client.js";

interface ServiceEntry {
  id: string;
  name: string;
  description: string;
  price_usdc: number;
  rating: number;
  provider: string;
}

export const discoverAction: Action = {
  name: "MAXIA_DISCOVER",
  similes: ["FIND_SERVICE", "SEARCH_MAXIA", "LIST_SERVICES", "DISCOVER_AGENTS"],
  description:
    "Search the MAXIA AI marketplace for available services. " +
    "Use when the user wants to find AI services (text, code, audit, data) or browse the marketplace.",

  validate: async (_runtime: IAgentRuntime, message: Memory): Promise<boolean> => {
    const text = (message.content.text ?? "").toLowerCase();
    return /discover|find.*service|search.*maxia|marketplace|available.*service|browse/i.test(text);
  },

  handler: async (
    runtime: IAgentRuntime,
    message: Memory,
    _state: State | undefined,
    _options: HandlerOptions | undefined,
    callback?: HandlerCallback,
  ): Promise<void> => {
    const text = (message.content.text ?? "").toLowerCase();

    // Extract optional capability filter
    const capabilities = ["text", "code", "audit", "data", "image_gen"];
    const capability = capabilities.find((c) => text.includes(c)) || "";

    const params: Record<string, string> = {};
    if (capability) params.capability = capability;

    const res = await maxiaGet<{ services: ServiceEntry[] }>(
      runtime,
      "/api/public/discover",
      params,
    );

    if (!res.ok || !res.data) {
      await callback?.({
        text: `Could not fetch MAXIA services: ${res.error ?? "unknown error"}`,
      });
      return;
    }

    const services = res.data.services ?? [];
    if (services.length === 0) {
      await callback?.({ text: "No services found matching your criteria on MAXIA marketplace." });
      return;
    }

    const lines = services.slice(0, 10).map(
      (s) => `- **${s.name}** ($${s.price_usdc} USDC) — ${s.description} [rating: ${s.rating}/5]`,
    );

    await callback?.({
      text: `Found ${services.length} services on MAXIA:\n\n${lines.join("\n")}`,
      content: { services: services.slice(0, 10) },
    });
  },

  examples: [
    [
      { name: "user", content: { text: "What AI services are available on MAXIA?" } },
      {
        name: "agent",
        content: { text: "Found 17 services on MAXIA:\n\n- Text Agent ($0.05 USDC)...", action: "MAXIA_DISCOVER" },
      },
    ],
    [
      { name: "user", content: { text: "Find code audit services" } },
      {
        name: "agent",
        content: { text: "Found 3 audit services on MAXIA...", action: "MAXIA_DISCOVER" },
      },
    ],
  ],
};
