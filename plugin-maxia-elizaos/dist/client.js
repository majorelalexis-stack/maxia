import { elizaLogger } from "@elizaos/core";
const DEFAULT_API_URL = "https://maxiaworld.app";
const REQUEST_TIMEOUT_MS = 15_000;
export function getApiUrl(runtime) {
    return runtime.getSetting("MAXIA_API_URL") || DEFAULT_API_URL;
}
export function getApiKey(runtime) {
    const key = runtime.getSetting("MAXIA_API_KEY") || "";
    if (!key) {
        elizaLogger.warn("[plugin-maxia] MAXIA_API_KEY not set");
    }
    return key;
}
export async function maxiaGet(runtime, path, params) {
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
        const data = (await res.json());
        return { ok: true, data };
    }
    catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        elizaLogger.error(`[plugin-maxia] GET ${path} failed: ${msg}`);
        return { ok: false, error: msg };
    }
}
export async function maxiaPost(runtime, path, body) {
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
        const data = (await res.json());
        return { ok: true, data };
    }
    catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        elizaLogger.error(`[plugin-maxia] POST ${path} failed: ${msg}`);
        return { ok: false, error: msg };
    }
}
