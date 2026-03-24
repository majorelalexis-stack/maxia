---
name: maxia-stocks
description: Trade tokenized US stocks on MAXIA (AAPL, TSLA, NVDA, etc.)
arguments:
  - name: action
    description: "'list' for all stocks, or a symbol (AAPL, TSLA) for price"
    required: false
---

Tokenized stock trading on MAXIA via Ondo/xStocks.

**If action is 'list' or empty:**
1. Call `GET https://maxiaworld.app/api/public/stocks`
2. Display: symbol, name, price, 24h change

**If action is a stock symbol:**
1. Call `GET https://maxiaworld.app/api/public/stocks/price/{{action}}`
2. Display: symbol, live price in USDC, 24h change, market cap

**To buy/sell:**
- Buy: `POST /api/public/stocks/buy` with `{symbol, amount_usdc, payment_tx}`
- Sell: `POST /api/public/stocks/sell` with `{symbol, shares}`
- Portfolio: `GET /api/public/stocks/portfolio`

Fractional shares from 1 USDC. Commission: 0.5% per trade.
