/**
 * MAXIA_BUY_SERVICE — Execute an AI service on MAXIA marketplace (sandbox mode).
 * In sandbox mode, uses virtual USDC balance ($10K) — no real payment needed.
 * For production, the agent must send real USDC and pass the tx signature.
 */
import type { Action } from "@elizaos/core";
export declare const buyServiceAction: Action;
