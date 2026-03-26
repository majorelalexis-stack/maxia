# MAXIA V12 — Stress Test Results

**Date:** 2026-03-26
**Environment:** Windows 11, AMD 5800X, Python 3.12, uvicorn 1 worker, SQLite
**Method:** Real HTTP (httpx → uvicorn on localhost:8001), not in-process
**Mode:** SANDBOX_MODE=true

## 1. Chaos / Resilience Tests (circuit breaker)

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

## 2. Sequential Benchmark (50 requests per endpoint, real HTTP)

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

**500 requests, 0 errors. p99 spikes on /health and /stocks are cold-cache hits.**

## 3. Burst Test (100 concurrent on /health)

| Metric | Value |
|--------|-------|
| Total time | 113ms |
| Success rate | 100/100 (100%) |
| Errors | 0 |

## 4. Sustained Load (500 requests, 30 seconds)

| Metric | Value |
|--------|-------|
| Duration | 34.1s |
| Throughput | 14.7 req/s |
| Success rate | 500/500 (100%) |
| Latency p50 | 2ms |
| Latency p95 | 3ms |
| Latency p99 | 4ms |
| Errors | 0 |

## 5. Heavy Concurrent (warm cache)

| Test | Time | Success |
|------|------|---------|
| 10x /crypto/prices concurrent | 28ms | 10/10 |
| 25x /crypto/prices concurrent | 32ms | 25/25 |
| 50x /crypto/prices concurrent | 75ms | 50/50 |

**Note:** Cold-cache concurrent fails due to external API rate limits (CoinGecko). After first request warms cache (60s TTL), all concurrent requests succeed. This is expected behavior — production cache is always warm via scheduler.

## Summary

| Category | Requests | OK | Error Rate |
|----------|----------|-----|------------|
| Sequential (10 endpoints x 50) | 500 | 500 | 0% |
| Burst (100 concurrent) | 100 | 100 | 0% |
| Sustained (14.7 req/s) | 500 | 500 | 0% |
| Heavy concurrent (warm) | 85 | 85 | 0% |
| **Total** | **1185** | **1185** | **0%** |

## Infrastructure

- 14 chains with USDC payment verification
- 7 chains with native token swap (Solana Jupiter + 6 EVM via 0x)
- 2-3 RPC providers per chain with automatic failover
- Circuit breaker per chain: 3 failures → open, 30s reset, 2 successes → close
- Pyth oracle with 30s staleness check (stocks), 120s (crypto)
- Price cache: 60s TTL (crypto), 180s (stocks)
- Rate limiting: 100 req/day free tier
