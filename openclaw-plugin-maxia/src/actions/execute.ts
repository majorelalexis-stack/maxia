/**
 * MAXIA Execute Action
 *
 * Purchase and run an AI service from the MAXIA marketplace.
 * The caller must have already transferred USDC on-chain and provide
 * the transaction signature for verification.
 *
 * Endpoint: POST /api/public/execute
 * Auth: required (X-API-Key)
 *
 * Also supports sandbox mode (no real payment) via POST /api/public/sandbox/execute.
 */

import { Type, type Static } from "@sinclair/typebox";
import { type MaxiaPluginConfig, maxiaFetch } from "../config.js";

// ── Parameter schema ──

export const ExecuteParams = Type.Object({
  service_id: Type.String({
    description: "The service ID returned by maxia_discover.",
  }),
  prompt: Type.String({
    description: "The prompt or input to send to the service.",
  }),
  payment_tx: Type.Optional(
    Type.String({
      description:
        "On-chain USDC transaction signature (Solana or Base). " +
        "Required for real execution. Omit to use sandbox mode.",
    }),
  ),
  chain: Type.Optional(
    Type.String({
      description: "Blockchain of the payment: solana (default), base, ethereum, polygon, arbitrum, etc.",
    }),
  ),
});

export type ExecuteInput = Static<typeof ExecuteParams>;

// ── Handler ──

export async function handleExecute(
  params: ExecuteInput,
  config: MaxiaPluginConfig,
): Promise<string> {
  if (!config.apiKey) {
    throw new Error(
      "MAXIA API key required for execute. Register first via maxia_discover or POST /api/public/register.",
    );
  }

  // Sandbox mode when no payment_tx is provided
  const isSandbox = !params.payment_tx;
  const endpoint = isSandbox
    ? "/api/public/sandbox/execute"
    : "/api/public/execute";

  const body: Record<string, string> = {
    service_id: params.service_id,
    prompt: params.prompt,
  };

  if (params.payment_tx) {
    body.payment_tx = params.payment_tx;
  }
  if (params.chain) {
    body.chain = params.chain;
  }

  const result = await maxiaFetch(endpoint, config, {
    method: "POST",
    body: JSON.stringify(body),
  });

  const prefix = isSandbox ? "[SANDBOX] " : "";
  return `${prefix}${JSON.stringify(result, null, 2)}`;
}

// ── Tool definition ──

export const executeTool = {
  name: "maxia_execute",
  description:
    "Execute (buy and run) an AI service on MAXIA. Requires a service_id from maxia_discover " +
    "and a prompt. For real execution provide the on-chain USDC payment_tx signature. " +
    "Omit payment_tx to run in sandbox mode (free, test-only). " +
    "Supports Solana, Base, Ethereum, Polygon, Arbitrum, and more.",
  parameters: ExecuteParams,
  handler: handleExecute,
};
