/**
 * MAXIA API Client — shared HTTP layer for all actions/providers.
 */
import type { IAgentRuntime } from "@elizaos/core";
import { elizaLogger } from "@elizaos/core";

const DEFAULT_API_URL = "https://maxiaworld.app";
const REQUEST_TIMEOUT_MS = 15_000;

export function getApiUrl(runtime: IAgentRuntime): string {
  return (runtime.getSetting("MAXIA_API_URL") as string) || DEFAULT_API_URL;
}

export function getApiKey(runtime: IAgentRuntime): string {
  const key = (runtime.getSetting("MAXIA_API_KEY") as string) || "";
  if (!key) {
    elizaLogger.warn("[plugin-maxia] MAXIA_API_KEY not set");
  }
  return key;
}

export interface MaxiaResponse<T = unknown> {
  ok: boolean;
  data?: T;
  error?: string;
}

export async function maxiaGet<T = unknown>(
  runtime: IAgentRuntime,
  path: string,
  params?: Record<string, string>,
): Promise<MaxiaResponse<T>> {
  const base = getApiUrl(runtime);
  const url = new URL(path, base);
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      url.searchParams.set(k, v);
    }
  }

  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

    const res = await fetch(url.toString(), {
      method: "GET",
      headers: {
        "X-API-Key": getApiKey(runtime),
        "Accept": "application/json",
      },
      signal: controller.signal,
    });
    clearTimeout(timeout);

    if (!res.ok) {
      const body = await res.text().catch(() => "");
      return { ok: false, error: `HTTP ${res.status}: ${body.slice(0, 200)}` };
    }

    const data = (await res.json()) as T;
    return { ok: true, data };
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    elizaLogger.error(`[plugin-maxia] GET ${path} failed: ${msg}`);
    return { ok: false, error: msg };
  }
}

export async function maxiaPost<T = unknown>(
  runtime: IAgentRuntime,
  path: string,
  body: Record<string, unknown>,
): Promise<MaxiaResponse<T>> {
  const base = getApiUrl(runtime);
  const url = new URL(path, base);

  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

    const res = await fetch(url.toString(), {
      method: "POST",
      headers: {
        "X-API-Key": getApiKey(runtime),
        "Content-Type": "application/json",
        "Accept": "application/json",
      },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    clearTimeout(timeout);

    if (!res.ok) {
      const text = await res.text().catch(() => "");
      return { ok: false, error: `HTTP ${res.status}: ${text.slice(0, 200)}` };
    }

    const data = (await res.json()) as T;
    return { ok: true, data };
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    elizaLogger.error(`[plugin-maxia] POST ${path} failed: ${msg}`);
    return { ok: false, error: msg };
  }
}
