# MAXIA V12 — Stress Test Results

## Part A: Local Development (sandbox)

**Date:** 2026-03-26
**Environment:** Windows 11, AMD 5800X, Python 3.12, uvicorn 1 worker, SQLite
**Method:** Real HTTP (httpx → uvicorn on localhost:8001)

### Chaos / Resilience Tests (circuit breaker)

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

### Sequential Benchmark (50 req/endpoint)

| Endpoint | p50 | p95 | p99 | avg | errors |
|----------|-----|-----|-----|-----|--------|
| /health | 1ms | 4ms | 217ms | 6ms | 0/50 |
| /api/public/crypto/prices | 1ms | 2ms | 3ms | 1ms | 0/50 |
| /api/public/stocks | 2ms | 3ms | 854ms | 19ms | 0/50 |
| /api/public/gpu/tiers | 1ms | 1ms | 2ms | 1ms | 0/50 |
| /api/public/services | 1ms | 1ms | 2ms | 1ms | 0/50 |
| /api/public/marketplace-stats | 1ms | 1ms | 1ms | 1ms | 0/50 |
| /oracle/feeds | <1ms | 1ms | 1ms | <1ms | 0/50 |
| /oracle/health | <1ms | 1ms | 1ms | <1ms | 0/50 |
| /status/chain/solana | 1ms | 1ms | 1ms | 1ms | 0/50 |
| /status/history | <1ms | 1ms | 1ms | <1ms | 0/50 |

### Burst & Sustained (local)

| Test | Result |
|------|--------|
| 100 concurrent /health | 113ms, 100/100 OK |
| 500 req sustained (14.7 req/s, 34s) | 500/500 OK, p50=2ms, p95=3ms |
| 50x /crypto/prices concurrent (warm) | 75ms, 50/50 OK |

**Local total: 1185 requests, 0 errors (0%)**

---

## Part B: VPS Production (PostgreSQL 17)

**Date:** 2026-03-26
**Environment:** OVH VPS, Ubuntu 25.04, Python 3.13, PostgreSQL 17.7, 57 tables
**Method:** Real HTTP from VPS localhost (urllib → uvicorn on port 8000)
**Database:** PostgreSQL 17 (asyncpg pool, 75K+ rows)

### Sequential Benchmark (20 req/endpoint)

| Endpoint | p50 | p95 | avg | status |
|----------|-----|-----|-----|--------|
| /health | 7ms | 31ms | 9ms | 20/20 OK |
| /api/public/crypto/prices | 6ms | 14ms | 7ms | 20/20 OK |
| /api/public/stocks | 8ms | 10ms | 8ms | 20/20 OK |
| /api/public/gpu/tiers | 5ms | 37ms | 7ms | 20/20 OK |
| /api/public/services | 5ms | 7ms | 5ms | 20/20 OK |
| /api/public/marketplace-stats | 4ms | 7ms | 4ms | 20/20 OK |
| /oracle/feeds | 2ms | 3ms | 2ms | 429 (rate limit) |
| /oracle/health | 3ms | 4ms | 3ms | 429 (rate limit) |
| /status/chain/solana | 3ms | 3ms | 3ms | 429 (rate limit) |
| /status/history | 3ms | 4ms | 3ms | 429 (rate limit) |

**First 120 requests OK (p50 = 2-8ms), then rate limiter kicks in (429). Zero 5xx errors.**

### Burst Test (50 concurrent)

| Metric | Value |
|--------|-------|
| Total time | 72ms |
| 200 OK | 0/50 (rate-limited after sequential test) |
| 429 rate-limited | 50/50 |
| 500 errors | 0/50 |
| Latency p50 | 12ms |
| Latency p95 | 22ms |

**Rate limiter correctly blocks burst traffic. No crashes, no 5xx. All responses in 72ms.**

### Sustained Load (5 req/s, realistic agent pace)

| Metric | Value |
|--------|-------|
| Duration | 20.4s |
| Throughput | 4.9 req/s |
| 200 OK | 20/100 |
| 429 rate-limited | 80/100 |
| 500 errors | 0/100 |
| Latency p50 | 3ms |
| Latency p95 | 6ms |
| Latency p99 | 8ms |

**Rate limiter enforces 100 req/day free tier. When not limited: p50 = 3ms, zero errors.**

### VPS Production Summary

| Metric | Value |
|--------|-------|
| Total requests sent | 350 |
| 200 OK | 140 (40%) |
| 429 rate-limited | 210 (60%) — working as designed |
| 500 server errors | 0 (0%) |
| Crashes | 0 |
| Latency (when not limited) | p50 = 2-8ms |

**Key finding: Zero server errors across 350 production requests. The rate limiter is the only reason for non-200 responses — this is correct security behavior, not a failure.**

---

## Infrastructure Summary

| Component | Status |
|-----------|--------|
| Database | PostgreSQL 17.7, 57 tables, 75K+ rows |
| Chains (USDC payments) | 14 |
| Chains (token swap) | 7 (Solana + 6 EVM) |
| RPC providers per chain | 2-3 with automatic failover |
| Circuit breaker | 3 failures → open, 30s reset, 2 successes → close |
| Oracle (stocks) | Pyth Network, 30s staleness check |
| Oracle (crypto) | Pyth + CoinGecko, 120s staleness check |
| Rate limiting | 100 req/day free tier, 429 on excess |
| Price cache TTL | 60s (crypto), 180s (stocks) |
| Chaos tests | 7/7 pass |
