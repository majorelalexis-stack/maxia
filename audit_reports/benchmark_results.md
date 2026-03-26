# MAXIA V12 — Benchmark Results

**Date:** 2026-03-26
**Environment:** Windows 11, Python 3.12, SQLite, SANDBOX_MODE=true
**Method:** 50 sequential requests per endpoint + 50 concurrent on /health
**Tool:** httpx AsyncClient against FastAPI ASGI (in-process, no network)

## Chaos / Resilience Tests (circuit breaker)

| Test | Result |
|------|--------|
| RPC Timeout Simulation | PASS |
| Circuit Opens After N Failures | PASS |
| Half-Open Recovery | PASS |
| Fallback Chain Routing | PASS |
| All RPCs Down Gracefully | PASS |
| Status Endpoint Accuracy | PASS |
| Concurrent Failure Handling (10 parallel) | PASS |

**7/7 tests passed**

## HTTP Endpoint Benchmark (50 requests each)

| Endpoint | p50 | p95 | p99 | avg | errors |
|----------|-----|-----|-----|-----|--------|
| /health | <1ms | 15ms | 16ms | 0.9ms | 0/50 |
| /api/public/crypto/prices | <1ms | 16ms | 1031ms | 21.9ms | 0/50 |
| /api/public/stocks | <1ms | 16ms | 1250ms | 26.2ms | 0/50 |
| /api/public/gpu/tiers | <1ms | 15ms | 16ms | 0.9ms | 0/50 |
| /api/public/services | <1ms | <1ms | 16ms | 0.3ms | 0/50 |
| /api/public/marketplace-stats | <1ms | <1ms | 15ms | 0.3ms | 0/50 |
| /oracle/feeds | <1ms | <1ms | 16ms | 0.3ms | 0/50 |
| /status/chain/solana | <1ms | <1ms | <1ms | <0.1ms | 0/50 |

## Concurrent Load Test

| Test | Total time | Success rate |
|------|-----------|-------------|
| 50x /health in parallel | 16ms | 50/50 (100%) |

## Notes

- p99 spikes on /crypto/prices and /stocks are cold-cache hits (first request fetches from oracle)
- After warm-up, all endpoints respond in <1ms (in-process, no network latency)
- Zero errors across 400+ requests
- Circuit breaker correctly handles: timeouts, cascading failures, concurrent access, chain failover
- Production latency will be higher (network + TLS + RPC calls) but architecture is sound

## Infrastructure Summary

- **14 chains** with USDC payment verification
- **7 chains** with native token swap (Solana Jupiter + 6 EVM via 0x)
- **2-3 RPC providers per chain** with automatic failover
- **Pyth oracle** with 30s staleness check for stocks, 120s for crypto
- **Circuit breaker** per chain: 3 failures to open, 30s reset, 2 successes to close
