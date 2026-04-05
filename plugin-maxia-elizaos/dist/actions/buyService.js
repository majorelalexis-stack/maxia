import { maxiaPost } from "../client.js";
const SERVICE_TYPES = ["text", "code", "audit", "data", "image_gen"];
export const buyServiceAction = {
    name: "MAXIA_BUY_SERVICE",
    similes: ["EXECUTE_SERVICE", "USE_SERVICE", "BUY_AI", "RUN_SERVICE"],
    description: "Buy and execute an AI service on MAXIA marketplace. " +
        "Available services: text, code, audit, data, image_gen. " +
        "Uses sandbox mode by default ($10K virtual USDC). " +
        "Use when user wants to run an AI task through MAXIA.",
    validate: async (_runtime, message) => {
        const text = (message.content.text ?? "").toLowerCase();
        return /buy.*service|execute.*service|run.*service|use.*maxia|audit.*code|generate.*image/i.test(text);
    },
    handler: async (runtime, message, _state, _options, callback) => {
        const text = message.content.text ?? "";
        // Detect service type
        const serviceType = SERVICE_TYPES.find((s) => text.toLowerCase().includes(s)) || "text";
        // Extract the prompt (everything after the service type keyword or the full text)
        const prompt = text
            .replace(/buy|execute|run|use|service|maxia|please|can you/gi, "")
            .trim() || text;
        // Use sandbox for safety
        const res = await maxiaPost(runtime, "/api/public/sandbox/execute", {
            service_type: serviceType,
            prompt,
        });
        if (!res.ok || !res.data) {
            await callback?.({ text: `Service execution failed: ${res.error ?? "unknown error"}` });
            return;
        }
        const r = res.data;
        const lines = [
            `**Service**: ${r.service ?? serviceType}`,
            r.cost_usdc != null ? `**Cost**: $${r.cost_usdc} USDC (sandbox)` : "",
            r.remaining_balance != null ? `**Balance**: $${r.remaining_balance} USDC remaining` : "",
            "",
            r.result ?? "No result returned.",
        ].filter(Boolean);
        await callback?.({
            text: lines.join("\n"),
            content: { result: r },
        });
    },
    examples: [
        [
            { name: "user", content: { text: "Run a code audit on this smart contract" } },
            {
                name: "agent",
                content: {
                    text: "**Service**: audit\n**Cost**: $0.10 USDC (sandbox)\n\n[CRITICAL] No reentrancy guard...",
                    action: "MAXIA_BUY_SERVICE",
                },
            },
        ],
        [
            { name: "user", content: { text: "Use MAXIA text service to summarize this article" } },
            {
                name: "agent",
                content: {
                    text: "**Service**: text\n**Cost**: $0.05 USDC (sandbox)\n\nSummary: ...",
                    action: "MAXIA_BUY_SERVICE",
                },
            },
        ],
    ],
};
