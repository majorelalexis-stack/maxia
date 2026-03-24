---
name: maxia-discover
description: Find AI services on the MAXIA marketplace by capability, price, or rating
arguments:
  - name: capability
    description: "What you're looking for: audit, code, data, image, text, sentiment, scraper, finetune"
    required: false
---

Search MAXIA marketplace for AI services.

1. Call `GET https://maxiaworld.app/api/public/discover` with query params:
   - `capability`: {{capability}} (or omit to browse all)
   - `max_price`: 100 (default)

2. Display results as a table: name, price, rating, type, description.

3. If the user wants to execute a service, they need:
   - A MAXIA API key (free via `POST /api/public/register`)
   - USDC on Solana to pay

Use the `X-API-Key` header from the `MAXIA_API_KEY` environment variable.
