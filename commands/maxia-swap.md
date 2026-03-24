---
name: maxia-swap
description: Get a crypto swap quote or execute a swap (50 tokens, 2450 pairs on Solana)
arguments:
  - name: from
    description: "Token to sell (e.g. SOL, USDC, ETH, BTC, BONK)"
    required: true
  - name: to
    description: "Token to buy"
    required: true
  - name: amount
    description: "Amount to swap"
    required: true
---

Get a crypto swap quote on MAXIA.

1. Call `GET https://maxiaworld.app/api/public/crypto/quote?from_token={{from}}&to_token={{to}}&amount={{amount}}`

2. Display: input amount, output amount, price impact, commission tier, route.

3. To execute: user must send USDC to MAXIA treasury, then call `POST /api/public/crypto/swap` with the tx signature.

Commission: BRONZE 0.10%, SILVER 0.05%, GOLD 0.03%, WHALE 0.01%.
