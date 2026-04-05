/**
 * MAXIA API Client — shared HTTP layer for all actions/providers.
 */
import type { IAgentRuntime } from "@elizaos/core";
export declare function getApiUrl(runtime: IAgentRuntime): string;
export declare function getApiKey(runtime: IAgentRuntime): string;
export interface MaxiaResponse<T = unknown> {
    ok: boolean;
    data?: T;
    error?: string;
}
export declare function maxiaGet<T = unknown>(runtime: IAgentRuntime, path: string, params?: Record<string, string>): Promise<MaxiaResponse<T>>;
export declare function maxiaPost<T = unknown>(runtime: IAgentRuntime, path: string, body: Record<string, unknown>): Promise<MaxiaResponse<T>>;
