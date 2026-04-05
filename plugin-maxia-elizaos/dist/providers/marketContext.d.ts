/**
 * MAXIA_MARKET_CONTEXT — Injects MAXIA marketplace state into agent prompt.
 * Runs before every LLM call so the agent is aware of MAXIA capabilities.
 */
import type { Provider } from "@elizaos/core";
export declare const marketContextProvider: Provider;
