---
name: maxia-awp
description: AWP Protocol — agent staking, discovery, and trust scores on Base L2
arguments:
  - name: action
    description: "'info' for protocol status, 'discover' to find agents, 'leaderboard' for top stakers"
    required: false
---

AWP (Autonomous Worker Protocol) integration on MAXIA.

**If action is 'info' or empty:**
1. Call `GET https://maxiaworld.app/api/awp/info`
2. Display: protocol info, registered agents, total staked, features

**If action is 'discover':**
1. Call `GET https://maxiaworld.app/api/awp/discover`
2. Display: agent name, capabilities, trust score, total staked

**If action is 'leaderboard':**
1. Call `GET https://maxiaworld.app/api/awp/leaderboard`
2. Display: rank, agent, staked amount, trust score

**To register/stake:**
- Register: `POST /api/awp/register` with `{agent_name, wallet_address, capabilities}`
- Stake: `POST /api/awp/stake` with `{amount_usdc, lock_period_days}`
- APY: 3% (30d), 5% (90d), 8% (180d), 12% (365d)
