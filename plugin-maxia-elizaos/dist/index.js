import { elizaLogger } from "@elizaos/core";
import { discoverAction } from "./actions/discover.js";
import { getPriceAction } from "./actions/getPrice.js";
import { swapAction } from "./actions/swap.js";
import { rentGpuAction } from "./actions/rentGpu.js";
import { buyServiceAction } from "./actions/buyService.js";
import { marketContextProvider } from "./providers/marketContext.js";
const maxiaPlugin = {
    name: "plugin-maxia",
    description: "MAXIA AI-to-AI marketplace — swap tokens, rent GPUs, buy/sell AI services with USDC escrow on 15 blockchains",
    init: async (config) => {
        const key = config.MAXIA_API_KEY || "";
        const url = config.MAXIA_API_URL || "https://maxiaworld.app";
        if (!key) {
            elizaLogger.warn("[plugin-maxia] MAXIA_API_KEY not set — some actions will fail");
        }
        elizaLogger.info(`[plugin-maxia] Initialized (API: ${url})`);
    },
    actions: [
        discoverAction,
        getPriceAction,
        swapAction,
        rentGpuAction,
        buyServiceAction,
    ],
    providers: [marketContextProvider],
};
export default maxiaPlugin;
