---
name: maxia-gpu
description: List GPU tiers or rent a GPU on MAXIA (RTX4090 to H200, pay-per-hour)
arguments:
  - name: action
    description: "'tiers' to list available GPUs, or a tier ID (rtx4090, a6000, a100_80, h100_sxm5, h200) to rent"
    required: false
---

GPU rental on MAXIA via RunPod.

**If action is 'tiers' or empty:**
1. Call `GET https://maxiaworld.app/api/public/gpu/tiers`
2. Display table: tier, GPU name, VRAM, price/hr, vs AWS savings

**If action is a tier ID:**
1. Call `GET https://maxiaworld.app/api/public/gpu/tiers` to show pricing
2. Explain: to rent, user needs MAXIA API key + USDC payment
3. Endpoint: `POST /api/public/gpu/rent` with `{gpu_tier, hours, payment_tx}`

Available tiers: rtx3090 ($0.48/h), rtx4090 ($0.76/h), a6000 ($1.09/h), l40s ($1.25/h), a100_80 ($1.97/h), h100_sxm5 ($2.96/h), h200 ($4.74/h), 4xa100 ($7.88/h).
