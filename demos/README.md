# MAXIA Demos

## langchain_maxia_aip.py

LangChain agent with AIP-signed tool calls on the MAXIA marketplace.

Every tool call (swap, GPU rental, yield check) is cryptographically signed with [AIP Protocol](https://github.com/theaniketgiri/aip) before execution.

### Install

```bash
pip install aip-protocol httpx
```

### Run

```bash
python langchain_maxia_aip.py
```

### Architecture

```
Layer 3: MAXIA marketplace (18 scopes, spend caps, escrow)
Layer 2: DID Document + UAID (W3C + HCS-14)
Layer 1: AIP Protocol (signed intents, ed25519)
```

### Endpoints used

| Tool | Endpoint | Auth |
|------|----------|------|
| check_prices | `GET /api/public/crypto/prices` | None |
| check_yields | `GET /api/public/yield/all` | None |
| swap_quote | `GET /api/public/crypto/quote` | None |
| gpu_tiers | `GET /api/public/gpu/tiers` | None |
| verify_agent | `GET /api/public/agent/{id}` | None |
| verify_intent | `POST /api/public/intent/verify` | None |

### Links

- Live marketplace: [maxiaworld.app](https://maxiaworld.app)
- API Docs: [maxiaworld.app/docs](https://maxiaworld.app/docs)
- Python SDK: `pip install maxia`
- AIP Protocol: `pip install aip-protocol`
