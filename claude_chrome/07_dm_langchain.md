# DM #5 — LangChain

## Instructions for Claude Chrome

1. **Primary**: Open https://www.linkedin.com/in/harrison-chase-961287118/ (Harrison Chase, CEO LangChain)
2. Click "Message" or "Connect" with the CONNECT NOTE below
3. **Secondary** (do ALSO, not instead): Open https://github.com/langchain-ai/langchain/issues/new and file a polite integration discovery issue with the GITHUB ISSUE content below. Label: "integration" or "community".
4. Return the final action(s) taken and the issue URL

## Target

- **Name**: Harrison Chase
- **Role**: Co-founder & CEO, LangChain
- **Why him**: I already published `langchain-maxia 0.2.0` on PyPI. LangChain has a partner integrations program and an integrations directory. Getting listed = exposure to every LangChain user.

## CONNECT NOTE (LinkedIn, max 300 chars)

Hi Harrison — I'm Alexis, dev of MAXIA (open-source USDC escrow marketplace for AI agents, live on Solana + Base). I published langchain-maxia 0.2.0 on PyPI. Would love to get it into the official LangChain integrations directory. Repo: github.com/majorelalexis-stack/maxia

## GITHUB ISSUE (file at github.com/langchain-ai/langchain/issues/new)

**Title**: Integration request: listing `langchain-maxia` in the official partner integrations directory

**Body**:

Hi LangChain team,

I've published `langchain-maxia` (v0.2.0) on PyPI — https://pypi.org/project/langchain-maxia/

It wraps MAXIA, an open-source USDC escrow marketplace for AI agents with on-chain contracts on Solana and Base mainnet, as a LangChain toolkit. A LangChain agent can call:

- Live crypto prices (65+ tokens, 15 chains, Pyth + Chainlink + CoinGecko fallback)
- Multi-source sentiment analysis
- On-chain token swaps (Jupiter + 0x)
- GPU rental via Akash Network
- DeFi yield aggregation
- 46 MCP tools total

All grounded in real mainnet contracts, free tier 100 req/day no KYC, `pip install maxia`.

Two questions:
1. Is there a formal path to get `langchain-maxia` listed in the partner integrations directory at https://python.langchain.com/docs/integrations/providers/ ?
2. Is the package organization correct (we follow the `langchain-{partner}` naming convention from your docs)?

MAXIA repo: https://github.com/majorelalexis-stack/maxia
Website: https://maxiaworld.app

Happy to open a PR against the docs if that's the preferred path. Thanks for the incredible ecosystem.

— Alexis
