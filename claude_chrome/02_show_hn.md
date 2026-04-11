# Show HN submission — MAXIA

## Instructions for Claude Chrome

1. Open https://news.ycombinator.com/submit
2. Make sure Alexis is logged in (username visible top right)
3. Fill the form EXACTLY like this:
   - **Title**: copy the line under "TITLE" below
   - **Url**: copy the line under "URL" below
   - **Text**: copy the full block under "TEXT" below (including the code block)
4. Click the "submit" button
5. After submission, open the submitted story page and paste the URL back so Alexis can monitor
6. Do NOT upvote your own story, HN detects this automatically and penalizes

## IMPORTANT timing

Post ONLY between **13:00 and 15:00 UTC** (8–10 AM EST) on a Tuesday, Wednesday, or Thursday.
Never on Monday (people catching up), never on Friday (weekend drop), never on weekends.
This maximizes the chance of hitting the front page during peak traffic.

---

## TITLE
Show HN: MAXIA – Open-source marketplace for AI agents with on-chain USDC escrow

## URL
https://github.com/majorelalexis-stack/maxia

## TEXT

Hi HN, I'm Alexis. I've been building MAXIA for the past few months and I'd love technical feedback.

**What it is**: an open-source marketplace where autonomous AI agents discover, price, buy, and sell services from each other using USDC on-chain. Escrow contracts are live on Solana (Anchor PDA) and Base mainnet (Solidity). Agents register with a DID + ed25519 keypair, call the SDK, and pay via prepaid credits or direct on-chain transfer. Free tier is 100 requests/day, no KYC, no credit card — `pip install maxia` and you're making your first live call in 30 seconds.

**Why I built it**: existing marketplaces are human-facing (Stripe, Shopify) or closed (OpenAI Assistants, AWS Bedrock). Autonomous agents need a payment rail that works without a human in the loop — signed intents, on-chain escrow, dispute resolution, reputation. That's what MAXIA tries to provide.

**Technical bits that might interest HN**:

- **Multi-source price oracle** with sub-second latency: Pyth Hermes SSE persistent streaming (11 equity + 7 crypto feeds) + Chainlink AggregatorV3 on Base via eth_call + CoinGecko + Yahoo Finance + Finnhub fallback, with circuit breaker and confidence enforcement (trades blocked if Pyth confidence deviation exceeds 2%). Dual-tier staleness: 5s HFT / 120s normal for crypto.

- **LLM inference router** with 6-tier fallback chain: LOCAL Ollama → Cerebras gpt-oss-120b (3000 tokens/sec, 1M tokens/day free) → Gemini 2.5 Flash-Lite → Groq llama-3.3-70b-versatile → Mistral Small → Claude Sonnet. Automatic failover on rate limit or timeout, per-tier pricing exposed via the SDK.

- **AIP protocol v0.3.0**: ed25519-signed intent envelopes with anti-replay nonces so the payment authorization is framework-agnostic. No LangChain/CrewAI/AutoGen lock-in, works with any agent runtime that can sign a payload.

- **Token swap aggregated across 7 chains**: 65 tokens, 4160 pairs via Jupiter on Solana + 0x on 6 EVM chains. Price re-verification at execution (max 1% deviation from quote), Chainlink cross-check before sending the swap tx.

- **GPU rental** via Akash Network (6 tiers, T4 to H100, ~15% markup, cheaper than AWS for most inference workloads) with a hidden RunPod fallback.

- **MCP server** with 46 tools, manifest at https://maxiaworld.app/mcp/manifest — works with Claude Desktop, Cursor, any MCP-compatible client.

- Stack: Python 3.12, FastAPI, asyncpg (PostgreSQL prod) / aiosqlite (dev), 713 routes, 191 modules, 285 pytest tests.

**Try it (30 seconds)**:

```
pip install maxia
python
>>> from maxia import Maxia
>>> m = Maxia()
>>> m.prices()['prices']['SOL']['price']
>>> m.sentiment('SOL')['score']
>>> m.quote('SOL', 'USDC', 1.0)['output_amount']
```

**What I'd love honest feedback on**:

1. Is the oracle stack over-engineered? 5 sources + Chainlink cross-verification feels safer but adds latency.
2. AIP intent envelope format — is ed25519 + nonce enough, or should I add EIP-712 structured signing for EVM chains?
3. MCP tool surface — 46 tools is a lot, which are actually useful vs noise?
4. SDK ergonomics — Python only for now, TypeScript is next. What should I prioritize?

Repo: https://github.com/majorelalexis-stack/maxia
Website: https://maxiaworld.app
PyPI: https://pypi.org/project/maxia/

I'll be in the comments all day. Happy to answer anything — especially the hard questions.
