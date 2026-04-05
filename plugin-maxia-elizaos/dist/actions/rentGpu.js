import { maxiaGet } from "../client.js";
export const rentGpuAction = {
    name: "MAXIA_RENT_GPU",
    similes: ["GPU_RENTAL", "RENT_COMPUTE", "GPU_PRICING", "AKASH_GPU"],
    description: "Browse available GPU tiers on MAXIA (Akash Network). " +
        "6 tiers from RTX 3060 to H100, 15-40% cheaper than AWS. " +
        "Use when user asks about GPU rental, compute, or training resources.",
    validate: async (_runtime, message) => {
        const text = (message.content.text ?? "").toLowerCase();
        return /gpu|compute|training|a100|h100|rtx|rent.*gpu|akash|vram/i.test(text);
    },
    handler: async (runtime, message, _state, _options, callback) => {
        const res = await maxiaGet(runtime, "/api/public/gpu/tiers");
        if (!res.ok || !res.data) {
            await callback?.({ text: `Could not fetch GPU tiers: ${res.error ?? "unknown error"}` });
            return;
        }
        const tiers = Array.isArray(res.data) ? res.data : (res.data.tiers ?? []);
        if (tiers.length === 0) {
            await callback?.({ text: "No GPU tiers available at the moment." });
            return;
        }
        const lines = tiers.map((t) => {
            const status = t.available ? "Available" : "Unavailable";
            return `- **${t.name}** (${t.gpu}, ${t.vram_gb}GB VRAM) — $${t.base_price_per_hour}/h [${status}]`;
        });
        await callback?.({
            text: `**MAXIA GPU Tiers** (via Akash Network, 15-40% cheaper than AWS):\n\n${lines.join("\n")}\n\n_To rent, send USDC payment and call /api/gpu/rent_`,
            content: { tiers },
        });
    },
    examples: [
        [
            { name: "user", content: { text: "What GPUs can I rent on MAXIA?" } },
            {
                name: "agent",
                content: {
                    text: "**MAXIA GPU Tiers**:\n\n- **Starter** (RTX 3060, 12GB VRAM) — $0.15/h...",
                    action: "MAXIA_RENT_GPU",
                },
            },
        ],
        [
            { name: "user", content: { text: "I need an A100 for training" } },
            {
                name: "agent",
                content: {
                    text: "**MAXIA GPU Tiers**:\n\n- **Pro A100** (A100, 80GB VRAM) — $1.20/h [Available]...",
                    action: "MAXIA_RENT_GPU",
                },
            },
        ],
    ],
};
