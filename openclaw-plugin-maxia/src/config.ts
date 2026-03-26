/**
 * MAXIA Plugin Configuration
 *
 * Centralizes API URL, auth headers, and the HTTP fetch wrapper
 * used by all action modules.
 */

export const MAXIA_DEFAULT_URL = "https://maxiaworld.app";

export interface MaxiaPluginConfig {
  /** MAXIA API key (from POST /api/public/register) */
  apiKey: string;
  /** Override base URL for self-hosted or staging instances */
  baseUrl?: string;
}

/**
 * Resolve the effective base URL (no trailing slash).
 */
export function resolveBaseUrl(config: MaxiaPluginConfig): string {
  const url = config.baseUrl || MAXIA_DEFAULT_URL;
  return url.endsWith("/") ? url.slice(0, -1) : url;
}

/**
 * Build standard headers for MAXIA API calls.
 */
export function buildHeaders(config: MaxiaPluginConfig): Record<string, string> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "application/json",
  };
  if (config.apiKey) {
    headers["X-API-Key"] = config.apiKey;
  }
  return headers;
}

/**
 * Generic fetch wrapper for the MAXIA API.
 * Throws on non-2xx responses with the server error message.
 */
export async function maxiaFetch<T = unknown>(
  path: string,
  config: MaxiaPluginConfig,
  options: RequestInit = {},
): Promise<T> {
  const base = resolveBaseUrl(config);
  const url = `${base}${path}`;
  const headers = {
    ...buildHeaders(config),
    ...(options.headers as Record<string, string> | undefined),
  };

  const resp = await fetch(url, { ...options, headers });

  if (!resp.ok) {
    const body = await resp.text().catch(() => "");
    throw new Error(`MAXIA API ${resp.status} on ${options.method || "GET"} ${path}: ${body}`);
  }

  return resp.json() as Promise<T>;
}
