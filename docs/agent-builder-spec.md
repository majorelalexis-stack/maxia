# Composable Agent Builder — Design Spec

## Vision
No-code tool to assemble AI agents from pre-built blocks. Like Zapier/n8n but for autonomous AI agents on MAXIA.

## User Flow
1. Choose INPUT (what triggers the agent)
2. Choose BRAIN (which LLM processes)
3. Choose ACTIONS (what the agent can do)
4. Set RULES (conditions and limits)
5. Choose OUTPUT (where results go)
6. Deploy → MAXIA hosts and executes 24/7

## Block Types

### INPUT (triggers)
| Block | Description |
|-------|-------------|
| `schedule` | Every X minutes/hours/days |
| `price_alert` | When token price crosses threshold |
| `mention` | When someone mentions on Twitter/Discord |
| `webhook` | External HTTP POST trigger |
| `email` | Incoming email to agent address |
| `on_chain` | On-chain event (transfer, swap, mint) |

### BRAIN (LLM)
| Block | Description |
|-------|-------------|
| `qwen_14b` | Local Qwen 3 14B (free, MAXIA GPU) |
| `qwen_9b` | Local Qwen 3.5 9B (fast, free) |
| `groq` | Groq Llama 3.3 70B (cloud, fast) |
| `claude` | Claude Sonnet (premium) |
| `gpt4` | GPT-4 (premium) |
| `custom` | User's own LLM endpoint |

### ACTIONS (tools)
| Block | Description |
|-------|-------------|
| `swap` | Swap tokens (107 tokens, 7 chains) |
| `gpu_rent` | Rent GPU (13 tiers) |
| `check_prices` | Get crypto prices (78 tokens) |
| `check_yields` | DeFi yield opportunities |
| `buy_stock` | Buy tokenized stocks |
| `send_usdc` | Transfer USDC |
| `escrow_lock` | Lock funds in escrow |
| `post_twitter` | Post tweet |
| `send_discord` | Send Discord message |
| `send_email` | Send email |
| `call_api` | Call any external API |
| `run_code` | Execute Python snippet |

### RULES (conditions)
| Block | Description |
|-------|-------------|
| `if_price_above` | If token price > X |
| `if_price_below` | If token price < X |
| `if_yield_above` | If APY > X% |
| `max_spend_day` | Max USDC per day |
| `max_spend_tx` | Max USDC per transaction |
| `only_hours` | Only execute during specific hours |
| `cooldown` | Wait X minutes between executions |

### OUTPUT (results)
| Block | Description |
|-------|-------------|
| `telegram_alert` | Send Telegram message |
| `discord_alert` | Send Discord webhook |
| `email_report` | Send email summary |
| `log_to_db` | Store in MAXIA database |
| `webhook_out` | Call external webhook |

## Database Schema

```sql
CREATE TABLE agent_blueprints (
    blueprint_id TEXT PRIMARY KEY,
    owner_wallet TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    config JSON NOT NULL,  -- {input, brain, actions, rules, output}
    status TEXT DEFAULT 'draft',  -- draft/active/paused/stopped
    did TEXT,  -- W3C DID
    uaid TEXT,  -- HCS-14 UAID
    executions INTEGER DEFAULT 0,
    last_run TEXT,
    created_at TEXT,
    updated_at TEXT
);
```

## API Endpoints

```
POST   /api/agents/build          — Create a new agent blueprint
GET    /api/agents/blueprints     — List my blueprints
GET    /api/agents/blueprint/{id} — Get blueprint details
PUT    /api/agents/blueprint/{id} — Update blueprint
POST   /api/agents/blueprint/{id}/activate  — Start agent
POST   /api/agents/blueprint/{id}/pause     — Pause agent
DELETE /api/agents/blueprint/{id}            — Delete agent
GET    /api/agents/blueprint/{id}/logs       — Execution logs
GET    /api/agents/blocks                    — List available blocks
```

## Example Blueprint (JSON)

```json
{
    "name": "SOL DCA Bot",
    "input": {"type": "schedule", "interval": "24h"},
    "brain": {"type": "qwen_9b"},
    "actions": [
        {"type": "check_prices", "tokens": ["SOL"]},
        {"type": "swap", "from": "USDC", "to": "SOL", "amount": 10}
    ],
    "rules": [
        {"type": "if_price_below", "token": "SOL", "threshold": 100},
        {"type": "max_spend_day", "amount": 50}
    ],
    "output": [
        {"type": "telegram_alert", "chat_id": "123456"}
    ]
}
```

## Security
- Each blueprint agent gets its own DID + UAID + AIP passport
- Spend caps enforced by agent_permissions (L0-L4)
- Each action signed with AIP before execution
- Blueprint owner can pause/stop at any time

## Implementation Priority
1. Backend API (blueprint CRUD) — 2h
2. Execution engine (scheduler + action runner) — 4h
3. Frontend UI (drag & drop blocks) — 6h+
4. Testing + deployment — 2h

## Status: DESIGN ONLY — Implementation next session
