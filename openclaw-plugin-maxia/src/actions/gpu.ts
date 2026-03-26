/**
 * MAXIA GPU Rental Action
 *
 * Rent GPUs at cost (0% markup) via RunPod integration.
 * 8 tiers from RTX 3090 ($0.48/hr) to 4x A100 ($7.88/hr),
 * plus a local 7900XT tier ($0.35/hr).
 *
 * Endpoints:
 *   GET  /api/public/gpu/tiers        — list all tiers and pricing
 *   GET  /api/public/gpu/compare      — compare vs AWS/GCP/Lambda
 *   POST /api/public/gpu/rent         — rent a GPU (auth + payment_tx)
 *   GET  /api/public/gpu/my-instances — list active rentals (auth)
 *   POST /api/public/gpu/terminate/:id — stop a rental (auth)
 */

import { Type, type Static } from "@sinclair/typebox";
import { type MaxiaPluginConfig, maxiaFetch } from "../config.js";

// ── List tiers ──

export const GpuTiersParams = Type.Object({});

export async function handleGpuTiers(
  _params: Static<typeof GpuTiersParams>,
  config: MaxiaPluginConfig,
): Promise<string> {
  const result = await maxiaFetch("/api/public/gpu/tiers", config);
  return JSON.stringify(result, null, 2);
}

// ── Compare pricing ──

export const GpuCompareParams = Type.Object({
  gpu: Type.Optional(
    Type.String({
      description:
        "GPU tier to compare (e.g. h100_sxm5, a100_80gb, rtx4090, rtx3090, local_7900xt). " +
        "Omit for all tiers.",
    }),
  ),
});

export async function handleGpuCompare(
  params: Static<typeof GpuCompareParams>,
  config: MaxiaPluginConfig,
): Promise<string> {
  const qs = new URLSearchParams();
  if (params.gpu) qs.set("gpu", params.gpu);
  const result = await maxiaFetch(`/api/public/gpu/compare?${qs}`, config);
  return JSON.stringify(result, null, 2);
}

// ── Rent GPU ──

export const GpuRentParams = Type.Object({
  gpu_tier: Type.String({
    description:
      "GPU tier to rent: rtx3090, rtx4090, a100_40gb, a100_80gb, h100_pcie, h100_sxm5, h200, 4xa100, local_7900xt.",
  }),
  hours: Type.Number({
    description: "Number of hours to rent.",
    minimum: 1,
  }),
  payment_tx: Type.String({
    description: "On-chain USDC transaction signature covering rental cost.",
  }),
});

export type GpuRentInput = Static<typeof GpuRentParams>;

export async function handleGpuRent(
  params: GpuRentInput,
  config: MaxiaPluginConfig,
): Promise<string> {
  if (!config.apiKey) {
    throw new Error("MAXIA API key required for GPU rental.");
  }

  const result = await maxiaFetch("/api/public/gpu/rent", config, {
    method: "POST",
    body: JSON.stringify({
      gpu_tier: params.gpu_tier,
      hours: params.hours,
      payment_tx: params.payment_tx,
    }),
  });

  return JSON.stringify(result, null, 2);
}

// ── List active instances ──

export const GpuInstancesParams = Type.Object({});

export async function handleGpuInstances(
  _params: Static<typeof GpuInstancesParams>,
  config: MaxiaPluginConfig,
): Promise<string> {
  if (!config.apiKey) {
    throw new Error("MAXIA API key required to list GPU instances.");
  }
  const result = await maxiaFetch("/api/public/gpu/my-instances", config);
  return JSON.stringify(result, null, 2);
}

// ── Terminate instance ──

export const GpuTerminateParams = Type.Object({
  pod_id: Type.String({
    description: "Pod ID of the GPU instance to terminate.",
  }),
});

export async function handleGpuTerminate(
  params: Static<typeof GpuTerminateParams>,
  config: MaxiaPluginConfig,
): Promise<string> {
  if (!config.apiKey) {
    throw new Error("MAXIA API key required to terminate GPU instances.");
  }
  const result = await maxiaFetch(`/api/public/gpu/terminate/${params.pod_id}`, config, {
    method: "POST",
  });
  return JSON.stringify(result, null, 2);
}

// ── Tool definitions ──

export const gpuTiersTool = {
  name: "maxia_gpu_tiers",
  description:
    "List all GPU rental tiers and pricing on MAXIA. " +
    "8 tiers from RTX 3090 ($0.48/hr) to 4x A100 ($7.88/hr) plus local 7900XT ($0.35/hr). " +
    "0% markup — RunPod cost pass-through. Free, no auth required.",
  parameters: GpuTiersParams,
  handler: handleGpuTiers,
};

export const gpuCompareTool = {
  name: "maxia_gpu_compare",
  description:
    "Compare MAXIA GPU pricing vs AWS, GCP, and Lambda Labs. " +
    "Optionally filter by a specific GPU tier. Free, no auth required.",
  parameters: GpuCompareParams,
  handler: handleGpuCompare,
};

export const gpuRentTool = {
  name: "maxia_gpu_rent",
  description:
    "Rent a GPU on MAXIA. Requires API key and on-chain USDC payment. " +
    "Returns pod ID, SSH access, and connection details.",
  parameters: GpuRentParams,
  handler: handleGpuRent,
};

export const gpuInstancesTool = {
  name: "maxia_gpu_instances",
  description:
    "List your active GPU rental instances on MAXIA. Requires API key.",
  parameters: GpuInstancesParams,
  handler: handleGpuInstances,
};

export const gpuTerminateTool = {
  name: "maxia_gpu_terminate",
  description:
    "Terminate an active GPU rental on MAXIA by pod ID. Requires API key.",
  parameters: GpuTerminateParams,
  handler: handleGpuTerminate,
};
