# Features Removed in v2-clean

Removed for regulatory compliance (v2-clean branch, 2026-04-20).
Context: repositioning MAXIA as AI infrastructure protocol — no financial services.

## Modules deleted

### trading/
- `tokenized_stocks.py` — xStocks/Ondo/Dinari tokenized stock trading (securities)
- `crypto_swap.py` — user-facing Jupiter/0x DEX swap (DEX license risk)
- `defi_scanner.py` — DeFi yield scanning / lending / staking (financial advice)
- `solana_defi.py` — Solana DeFi lending/borrowing/staking/LP (financial service)
- `evm_swap.py` — EVM multi-chain swap via 0x (DEX license risk)
- `perps_client.py` — Jupiter perpetual futures (derivatives)
- `token_sniper.py` — pump.fun token sniper (market manipulation risk)
- `yield_aggregator.py` — DeFi yield aggregation (financial advice)
- `dca_bot.py` — dollar-cost averaging bot (investment advice)
- `grid_bot.py` — grid trading bot (investment advice)
- `trading_features.py` — copy trading, whale tracker, candles (financial analysis)
- `trading_tools.py` — price alerts, portfolio analytics (financial tools)

### integrations/
- `x402_middleware.py` — x402 micropayment middleware (payment processing)
- `l402_middleware.py` — L402 Lightning payment middleware (Bitcoin payment processing)
- `fiat_onramp.py` — Transak/Moonpay fiat on-ramp (MSB / money transmission)

### blockchain/
- `lightning_api.py` — Bitcoin Lightning Network API (payment processing)

### marketplace/
- `public_api_trading.py` — Public API trading routes (DeFi, stocks, swap)

### features/
- `auto_compound.py` — automated DeFi yield compounding (financial service)

### core/
- `geo_blocking.py` — US geo-blocking middleware (no longer needed — regulated features removed)

## Test files deleted
- `tests/test_dca_grid_bots.py`
- `tests/test_swap_geo_auth.py`
- `tests/test_trading_bots.py`

## What was kept

- Escrow Solana + Base (on-chain smart contracts — legal)
- Price oracle display (Pyth, Chainlink, CoinGecko — data only, not tradable)
- GPU compute via Akash (infrastructure, not financial)
- Prepaid credits USDC (payment for compute services)
- Streaming payments USDC (pay-per-second for services)
- Agent discovery/execute marketplace
- All enterprise features (billing, SSO, metrics, audit)
