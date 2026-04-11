# DM #7 — AutoGen (Microsoft)

## Instructions for Claude Chrome

1. Open https://github.com/microsoft/autogen/issues/new/choose
2. Pick the "Discussion" or "Feature Request" template (whichever allows free-form integration questions)
3. Paste the ISSUE content below
4. Submit
5. Return the issue URL

## Target

- **Repo**: microsoft/autogen
- **Key maintainers**: Chi Wang (Microsoft Research), Qingyun Wu, Eric Zhu
- **Why them**: I already published `autogen-maxia 0.1.0` on PyPI. AutoGen has a community extensions system and the ecosystem repo `autogen-ext`. Listing there = exposure to every AutoGen user.
- **NO Twitter** (per user rules — avoid that channel entirely)

## ISSUE / DISCUSSION

**Title**: Integration: `autogen-maxia` — on-chain USDC marketplace for autonomous agents

**Body**:

Hi AutoGen team,

I've published `autogen-maxia` (v0.1.0) on PyPI: https://pypi.org/project/autogen-maxia/

It wraps MAXIA — an open-source USDC escrow marketplace for AI agents with on-chain contracts on Solana and Base mainnet — as an AutoGen tool set. An AutoGen agent can:

- Fetch live crypto prices (65+ tokens, 15 chains, Pyth SSE + Chainlink + CoinGecko fallback)
- Run sentiment analysis on a token (multi-source)
- Get on-chain swap quotes (Jupiter + 0x)
- Rent GPUs via Akash Network
- Browse the AI services marketplace (46 MCP tools total)

Free tier is 100 req/day, no KYC, no credit card. `pip install autogen-maxia`.

**Questions for the team/community**:

1. Is there a preferred path to get `autogen-maxia` listed in the AutoGen community extensions directory, or surfaced in the `autogen-ext` ecosystem repo?
2. The package currently targets AutoGen 0.2.x API — any guidance on the upgrade path to AutoGen 0.4+ would be appreciated, I'll open a follow-up PR.
3. Is there a code review / quality bar I should meet before asking for official recognition?

MAXIA repo: https://github.com/majorelalexis-stack/maxia
Website: https://maxiaworld.app

Happy to open a PR against the `autogen-ext` repo with the MAXIA extension if that's the preferred path, or submit the package to the community extensions list. Thanks for building such a solid multi-agent framework.

— Alexis
