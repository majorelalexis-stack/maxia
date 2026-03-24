---
name: maxia-yields
description: Find the best DeFi yields across 14 chains
arguments:
  - name: asset
    description: "Asset to find yields for: USDC, ETH, SOL, BTC (default: USDC)"
    required: false
---

DeFi yield scanner on MAXIA — best APY across 14 chains.

1. Call `GET https://maxiaworld.app/api/yields/best?asset={{asset}}&limit=10`
   - Default asset: USDC

2. Display results as table: protocol, chain, APY, TVL, risk level.

3. Data sources: Aave, Compound, Marinade, Jito, Lido, Ref Finance, and more via DeFiLlama.

Supported chains: Solana, Base, Ethereum, XRP, Polygon, Arbitrum, Avalanche, BNB, TON, SUI, TRON, NEAR, Aptos, SEI.
