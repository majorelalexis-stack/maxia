"""MAXIA Chat Handler — Conversational trading chat via natural language.

Accepts natural language commands and routes them to the appropriate
backend module (oracle, swap, risk analysis, leaderboard, etc.).
Falls back to LLM for general questions.

Endpoint: POST /api/chat
Input:  {"message": "price SOL"}
Output: {"response": "SOL: $83.05 (Pyth, 1s ago)", "type": "price", "data": {...}}
"""
import logging
import re
import time
from dataclasses import dataclass
from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from core.error_utils import safe_error

logger = logging.getLogger("chat_handler")

router = APIRouter(prefix="/api/chat", tags=["chat"])

# ── Rate Limiting (10 req/min per IP) ──

_RATE_LIMIT_WINDOW_S = 60
_RATE_LIMIT_MAX = 10
_rate_store: dict[str, list[float]] = {}
_RATE_STORE_MAX_IPS = 10_000  # Prevent unbounded memory growth


def _check_rate_limit(ip: str) -> bool:
    """Return True if the request is allowed, False if rate limited."""
    now = time.time()
    cutoff = now - _RATE_LIMIT_WINDOW_S

    # Evict stale IPs periodically to bound memory
    if len(_rate_store) > _RATE_STORE_MAX_IPS:
        stale_ips = [k for k, v in _rate_store.items() if not v or v[-1] < cutoff]
        for k in stale_ips:
            del _rate_store[k]

    timestamps = _rate_store.get(ip, [])
    # Keep only timestamps within the window
    timestamps = [ts for ts in timestamps if ts > cutoff]

    if len(timestamps) >= _RATE_LIMIT_MAX:
        _rate_store[ip] = timestamps
        return False

    timestamps.append(now)
    _rate_store[ip] = timestamps
    return True


# ── Intent Detection ──

@dataclass(frozen=True)
class ParsedIntent:
    intent: str
    symbol: Optional[str] = None
    amount: Optional[float] = None
    from_token: Optional[str] = None
    to_token: Optional[str] = None
    address: Optional[str] = None
    wallet: Optional[str] = None  # ONE-52: user wallet for TX building
    raw_message: str = ""


# Regex patterns for swap: "swap 10 USDC to SOL", "buy 5 SOL with USDC", "sell 2 ETH for USDC"
_SWAP_PATTERN = re.compile(
    r"(?:swap|buy|sell|exchange|convert|acheter|vendre)\s+"
    r"(\d+(?:\.\d+)?)\s+"
    r"([A-Za-z]+)\s+"
    r"(?:to|for|into|with|en|pour|contre)\s+"
    r"([A-Za-z]+)",
    re.IGNORECASE,
)

# Solana address pattern (base58, 32-44 chars)
_SOLANA_ADDR_PATTERN = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")

# Symbol extraction: "price SOL", "prix BTC", "price of ETH"
_PRICE_SYMBOL_PATTERN = re.compile(
    r"(?:price|prix|cours|quote)\s+(?:of\s+|de\s+)?([A-Za-z]{2,10})",
    re.IGNORECASE,
)

_PRICE_KEYWORDS = {"price", "prix", "cours", "quote"}
_SWAP_KEYWORDS = {"swap", "buy", "sell", "exchange", "convert", "acheter", "vendre"}
_RISK_KEYWORDS = {"risk", "check", "rug", "audit", "risque", "analyser"}
_HELP_KEYWORDS = {"help", "aide", "commands", "commandes", "?"}
_LEADERBOARD_KEYWORDS = {"leaderboard", "top", "classement", "ranking"}
_STOCK_KEYWORDS = {"stocks", "actions", "equity", "equities", "bourse"}
_GPU_KEYWORDS = {"gpu", "gpus", "compute", "rental", "location"}
_YIELD_KEYWORDS = {"yield", "yields", "defi", "apy", "rendement", "rendements", "staking"}
_ALERT_KEYWORDS = {"alert", "alerte", "notify", "notif", "notification", "remind"}
_DCA_KEYWORDS = {"dca", "recurring", "auto-buy", "autobuy", "regulier"}
_PORTFOLIO_KEYWORDS = {"portfolio", "portefeuille", "holdings", "balance", "solde", "wallet"}
_BRIDGE_KEYWORDS = {"bridge", "transfer", "cross-chain", "crosschain", "envoyer"}
_BUY_CRYPTO_KEYWORDS = {"buy", "acheter", "card", "carte", "fiat", "onramp"}


def _detect_intent(message: str) -> ParsedIntent:
    """Parse the user message into a structured intent via keyword matching."""
    msg = message.strip()
    msg_lower = msg.lower()
    words = set(msg_lower.split())

    # 1. Help — exact or near-exact
    if words & _HELP_KEYWORDS or msg_lower in ("help", "aide", "?"):
        return ParsedIntent(intent="help", raw_message=msg)

    # 2. Swap — requires amount + tokens
    swap_match = _SWAP_PATTERN.search(msg)
    if swap_match:
        amount = float(swap_match.group(1))
        from_token = swap_match.group(2).upper()
        to_token = swap_match.group(3).upper()
        return ParsedIntent(
            intent="swap",
            amount=amount,
            from_token=from_token,
            to_token=to_token,
            raw_message=msg,
        )

    # 3. Buy crypto with card — check BEFORE swap_help (buy/acheter overlap)
    if words & _BUY_CRYPTO_KEYWORDS and (words & {"card", "carte", "fiat", "onramp", "credit"} or not swap_match):
        # Only match buy_crypto if card/fiat keywords present, or no swap pattern
        if words & {"card", "carte", "fiat", "onramp", "credit"}:
            sym_match = _PRICE_SYMBOL_PATTERN.search(msg)
            symbol = sym_match.group(1).upper() if sym_match else None
            return ParsedIntent(intent="buy_crypto", symbol=symbol, raw_message=msg)

    # 3b. Swap keyword without parseable format
    if words & _SWAP_KEYWORDS and not swap_match:
        return ParsedIntent(intent="swap_help", raw_message=msg)

    # 4. Risk / check — look for a Solana address
    if words & _RISK_KEYWORDS:
        addr_match = _SOLANA_ADDR_PATTERN.search(msg)
        address = addr_match.group(0) if addr_match else None
        return ParsedIntent(intent="risk", address=address, raw_message=msg)

    # 5. Price — extract symbol
    if words & _PRICE_KEYWORDS:
        sym_match = _PRICE_SYMBOL_PATTERN.search(msg)
        symbol = sym_match.group(1).upper() if sym_match else None
        return ParsedIntent(intent="price", symbol=symbol, raw_message=msg)

    # 6. Leaderboard
    if words & _LEADERBOARD_KEYWORDS:
        return ParsedIntent(intent="leaderboard", raw_message=msg)

    # 7. Stocks
    if words & _STOCK_KEYWORDS:
        return ParsedIntent(intent="stocks", raw_message=msg)

    # 8. GPU
    if words & _GPU_KEYWORDS:
        return ParsedIntent(intent="gpu", raw_message=msg)

    # 9. Yield / DeFi
    if words & _YIELD_KEYWORDS:
        return ParsedIntent(intent="yield", raw_message=msg)

    # 10. Alert — "alert SOL above 100" or "alerte ETH 5%"
    if words & _ALERT_KEYWORDS:
        sym_match = _PRICE_SYMBOL_PATTERN.search(msg)
        symbol = sym_match.group(1).upper() if sym_match else None
        return ParsedIntent(intent="alert", symbol=symbol, raw_message=msg)

    # 11. DCA
    if words & _DCA_KEYWORDS:
        return ParsedIntent(intent="dca", raw_message=msg)

    # 12. Portfolio / Balance
    if words & _PORTFOLIO_KEYWORDS:
        addr_match = _SOLANA_ADDR_PATTERN.search(msg)
        address = addr_match.group(0) if addr_match else None
        return ParsedIntent(intent="portfolio", address=address, raw_message=msg)

    # 13. Bridge
    if words & _BRIDGE_KEYWORDS:
        return ParsedIntent(intent="bridge", raw_message=msg)

    # 14. Fallback — LLM
    return ParsedIntent(intent="llm", raw_message=msg)


# ── Intent Handlers ──

async def _handle_help() -> dict:
    """Return available chat commands."""
    commands = (
        "Available commands:\n"
        "  price <SYMBOL>           — Get live price (e.g. price SOL)\n"
        "  swap <AMT> <FROM> to <TO> — Get swap quote (e.g. swap 10 USDC to SOL)\n"
        "  buy <SYMBOL>             — Buy crypto with credit card\n"
        "  alert <SYMBOL>           — Set up price alerts\n"
        "  dca                      — DCA bot (auto-buy recurring)\n"
        "  portfolio <ADDRESS>      — Wallet portfolio & balances\n"
        "  bridge                   — Cross-chain transfers\n"
        "  check risk <ADDRESS>     — Token rug pull risk analysis\n"
        "  leaderboard              — Top 5 agents by volume\n"
        "  stocks                   — Live stock prices\n"
        "  gpu                      — Available GPU tiers & pricing\n"
        "  yield                    — Top DeFi yields\n"
        "  help                     — This help message\n"
        "  <anything else>          — Ask the AI assistant"
    )
    return {"response": commands, "type": "help", "data": None}


async def _handle_price(intent: ParsedIntent) -> dict:
    """Fetch price for a symbol from Pyth oracle, then price_oracle fallback."""
    symbol = intent.symbol
    if not symbol:
        return {
            "response": "Please specify a symbol. Example: price SOL",
            "type": "error",
            "data": None,
        }

    symbol_upper = symbol.upper()

    # Try Pyth first (fastest, sub-second)
    try:
        from trading.pyth_oracle import CRYPTO_FEEDS, EQUITY_FEEDS, get_pyth_price

        feed_id = CRYPTO_FEEDS.get(symbol_upper) or EQUITY_FEEDS.get(symbol_upper)
        if feed_id:
            result = await get_pyth_price(feed_id)
            if "price" in result and result["price"] > 0:
                price = result["price"]
                age_s = int(time.time() - result.get("publish_time", time.time()))
                source = result.get("source", "pyth")
                return {
                    "response": f"{symbol_upper}: ${price:,.4f} ({source}, {age_s}s ago)",
                    "type": "price",
                    "data": {
                        "symbol": symbol_upper,
                        "price": price,
                        "source": source,
                        "age_seconds": age_s,
                    },
                }
    except Exception as exc:
        logger.warning(f"Pyth lookup failed for {symbol_upper}: {exc}")

    # Fallback to price_oracle
    try:
        from trading.price_oracle import get_price

        price = await get_price(symbol_upper)
        if price and price > 0:
            return {
                "response": f"{symbol_upper}: ${price:,.4f} (oracle)",
                "type": "price",
                "data": {"symbol": symbol_upper, "price": price, "source": "oracle"},
            }
    except Exception as exc:
        logger.warning(f"Price oracle failed for {symbol_upper}: {exc}")

    return {
        "response": f"Could not fetch price for {symbol_upper}. Try a supported symbol (SOL, BTC, ETH, etc.).",
        "type": "error",
        "data": None,
    }


async def _handle_swap(intent: ParsedIntent) -> dict:
    """Get a swap quote (MAXIA commission + Jupiter pricing). Frontend builds the real tx."""
    try:
        from trading.crypto_swap import SUPPORTED_TOKENS, get_swap_quote
        from blockchain.jupiter_router import get_quote as jup_get_quote

        from_token = intent.from_token or ""
        to_token = intent.to_token or ""
        amount = intent.amount or 0

        if from_token not in SUPPORTED_TOKENS:
            supported = ", ".join(sorted(SUPPORTED_TOKENS.keys()))
            return {
                "response": f"Unknown token: {from_token}. Supported: {supported}",
                "type": "error",
                "data": None,
            }
        if to_token not in SUPPORTED_TOKENS:
            supported = ", ".join(sorted(SUPPORTED_TOKENS.keys()))
            return {
                "response": f"Unknown token: {to_token}. Supported: {supported}",
                "type": "error",
                "data": None,
            }
        if amount <= 0:
            return {
                "response": "Amount must be positive.",
                "type": "error",
                "data": None,
            }

        # 1. Get MAXIA quote (with commission info)
        quote = await get_swap_quote(from_token, to_token, amount)

        if "error" in quote:
            return {"response": quote["error"], "type": "error", "data": None}

        out_amount = quote.get("output_amount", 0)
        rate = quote.get("rate", 0)
        commission = quote.get("commission_pct", 0)

        # 2. Get Jupiter quote for real pricing (outAmount, priceImpact)
        from_mint = SUPPORTED_TOKENS[from_token]["mint"]
        to_mint = SUPPORTED_TOKENS[to_token]["mint"]
        decimals = SUPPORTED_TOKENS[from_token].get("decimals", 6)
        amount_raw = int(amount * (10 ** decimals))

        jup_quote = await jup_get_quote(from_mint, to_mint, amount_raw)

        result_data = dict(quote)
        result_data["requires_wallet"] = True

        # ONE-52: build unsigned TX if we have a Jupiter quote + user wallet
        unsigned_tx = None
        if jup_quote.get("success") and jup_quote.get("raw_quote"):
            result_data["jupiter_quote"] = jup_quote["raw_quote"]
            # Try to build the actual unsigned transaction for wallet signing
            if intent.wallet:
                try:
                    from blockchain.jupiter_router import execute_swap as jup_build_tx
                    tx_result = await jup_build_tx(jup_quote["raw_quote"], intent.wallet)
                    if tx_result.get("success") and tx_result.get("swapTransaction"):
                        unsigned_tx = tx_result["swapTransaction"]
                        result_data["unsigned_tx"] = unsigned_tx
                        result_data["last_valid_block_height"] = tx_result.get("lastValidBlockHeight", 0)
                except Exception as e:
                    logger.warning("[Chat] Failed to build swap TX: %s", e)

        if unsigned_tx:
            response_text = (
                f"Swap ready: {amount} {from_token} -> {out_amount:.6f} {to_token}\n"
                f"Rate: 1 {from_token} = {rate:.6f} {to_token}\n"
                f"Commission: {commission}\n\n"
                f"Transaction built — sign with your wallet to execute."
            )
            result_data["tx_ready"] = True
        else:
            response_text = (
                f"Swap quote: {amount} {from_token} -> {out_amount:.6f} {to_token}\n"
                f"Rate: 1 {from_token} = {rate:.6f} {to_token}\n"
                f"Commission: {commission}\n\n"
                f"Connect your wallet to execute this swap."
            )
            result_data["tx_ready"] = False

        return {
            "response": response_text,
            "type": "swap_quote",
            "data": result_data,
        }
    except Exception as exc:
        err = safe_error(exc, "chat_swap")
        return {"response": "Failed to get swap quote. Try again later.", "type": "error", "data": err}


async def _handle_swap_help() -> dict:
    """User tried a swap command but format was unparseable."""
    return {
        "response": (
            "To get a swap quote, use this format:\n"
            "  swap <amount> <FROM_TOKEN> to <TO_TOKEN>\n"
            "Examples:\n"
            "  swap 10 USDC to SOL\n"
            "  buy 0.5 SOL with USDC\n"
            "  sell 100 BONK for USDC"
        ),
        "type": "help",
        "data": None,
    }


async def _handle_risk(intent: ParsedIntent) -> dict:
    """Analyze token rug-pull risk."""
    address = intent.address
    if not address:
        return {
            "response": "Please provide a Solana token address. Example: check risk 7xKX...",
            "type": "error",
            "data": None,
        }

    try:
        from features.web3_services import analyze_token_risk

        result = await analyze_token_risk(address)

        if "error" in result:
            return {"response": result["error"], "type": "error", "data": None}

        score = result.get("risk_score", -1)
        level = result.get("risk_level", "UNKNOWN")
        rec = result.get("recommendation", "N/A")
        warnings = result.get("warnings", [])
        name = result.get("info", {}).get("name", "Unknown")

        warning_text = ""
        if warnings:
            warning_text = "\nWarnings:\n" + "\n".join(f"  - {w}" for w in warnings)

        return {
            "response": (
                f"Risk analysis for {name} ({address[:8]}...):\n"
                f"  Score: {score}/100 ({level})\n"
                f"  Recommendation: {rec}"
                f"{warning_text}"
            ),
            "type": "risk",
            "data": result,
        }
    except Exception as exc:
        err = safe_error(exc, "chat_risk")
        return {"response": "Failed to analyze token risk.", "type": "error", "data": err}


async def _handle_leaderboard() -> dict:
    """Top 5 agents by volume."""
    try:
        from core.database import db

        agents = await db.get_top_agents(limit=5, period_days=30)

        if not agents:
            return {
                "response": "No agents found in the leaderboard yet.",
                "type": "leaderboard",
                "data": [],
            }

        lines = ["Top 5 agents (30d volume):"]
        for i, agent in enumerate(agents, 1):
            name = agent.get("name", "Anonymous")
            volume = agent.get("volume", 0)
            tier = agent.get("tier", "bronze")
            lines.append(f"  {i}. {name} — ${volume:,.2f} ({tier})")

        return {
            "response": "\n".join(lines),
            "type": "leaderboard",
            "data": agents,
        }
    except Exception as exc:
        err = safe_error(exc, "chat_leaderboard")
        return {"response": "Failed to fetch leaderboard.", "type": "error", "data": err}


async def _handle_stocks() -> dict:
    """List live stock prices."""
    try:
        from trading.price_oracle import get_stock_prices

        prices = await get_stock_prices()

        if not prices:
            return {"response": "No stock prices available.", "type": "error", "data": None}

        # Show top 10 by name
        display_symbols = ["AAPL", "TSLA", "NVDA", "GOOGL", "MSFT", "AMZN", "META", "MSTR", "SPY", "QQQ"]
        lines = ["Stock prices:"]
        for sym in display_symbols:
            info = prices.get(sym)
            if info:
                price = info.get("price", 0)
                change = info.get("change", 0)
                source = info.get("source", "")
                sign = "+" if change >= 0 else ""
                lines.append(f"  {sym}: ${price:,.2f} ({sign}{change:.2f}%) [{source}]")

        return {
            "response": "\n".join(lines),
            "type": "stocks",
            "data": {sym: prices.get(sym) for sym in display_symbols if sym in prices},
        }
    except Exception as exc:
        err = safe_error(exc, "chat_stocks")
        return {"response": "Failed to fetch stock prices.", "type": "error", "data": err}


async def _handle_gpu() -> dict:
    """List available GPU tiers and pricing."""
    try:
        from core.config import GPU_TIERS

        if not GPU_TIERS:
            return {"response": "No GPU tiers available.", "type": "error", "data": None}

        lines = ["GPU tiers (Akash Network, 15% markup):"]
        for tier in GPU_TIERS[:8]:  # Show first 8
            label = tier.get("label", tier.get("id", "?"))
            vram = tier.get("vram_gb", 0)
            price = tier.get("base_price_per_hour", 0)
            lines.append(f"  {label}: {vram}GB VRAM — ${price:.2f}/hr")

        return {
            "response": "\n".join(lines),
            "type": "gpu",
            "data": GPU_TIERS[:8],
        }
    except Exception as exc:
        err = safe_error(exc, "chat_gpu")
        return {"response": "Failed to fetch GPU tiers.", "type": "error", "data": err}


async def _handle_yield() -> dict:
    """Top DeFi yields for USDC."""
    try:
        from trading.yield_aggregator import _fetch_all_yields

        all_yields = await _fetch_all_yields()

        if not all_yields:
            return {"response": "No yield data available.", "type": "error", "data": None}

        # Filter USDC yields and sort by APY
        usdc_yields = [y for y in all_yields if y.get("asset", "").upper() == "USDC"]
        usdc_yields.sort(key=lambda y: y.get("apy", 0), reverse=True)
        top_5 = usdc_yields[:5]

        if not top_5:
            return {"response": "No USDC yields found.", "type": "yield", "data": []}

        lines = ["Top USDC yields:"]
        for y in top_5:
            protocol = y.get("protocol", "?")
            chain = y.get("chain", "?")
            apy = y.get("apy", 0)
            tvl = y.get("tvl", 0)
            lines.append(f"  {protocol} ({chain}): {apy:.2f}% APY — TVL ${tvl:,.0f}")

        return {
            "response": "\n".join(lines),
            "type": "yield",
            "data": top_5,
        }
    except Exception as exc:
        err = safe_error(exc, "chat_yield")
        return {"response": "Failed to fetch DeFi yields.", "type": "error", "data": err}


async def _handle_alert(intent: ParsedIntent) -> dict:
    """Guide user to create a price alert."""
    symbol = intent.symbol
    if not symbol:
        return {
            "response": (
                "To create a price alert, use the API:\n"
                "  POST /api/trading/alerts\n"
                "  {\"token\": \"SOL\", \"condition\": \"above\", \"target_price\": 100, \"wallet\": \"YOUR_WALLET\"}\n\n"
                "Conditions: above, below, pct_up, pct_down\n"
                "For percentage: {\"condition\": \"pct_up\", \"pct_change\": 5.0}\n"
                "Add \"repeat\": true to get notified every time (not just once)"
            ),
            "type": "help",
            "data": None,
        }

    return {
        "response": (
            f"To set an alert for {symbol}:\n"
            f"  POST /api/trading/alerts\n"
            f"  {{\"token\": \"{symbol}\", \"condition\": \"above\", \"target_price\": <PRICE>, \"wallet\": \"YOUR_WALLET\"}}\n\n"
            f"Or for percentage change:\n"
            f"  {{\"token\": \"{symbol}\", \"condition\": \"pct_up\", \"pct_change\": 5.0, \"wallet\": \"YOUR_WALLET\"}}"
        ),
        "type": "alert",
        "data": {"token": symbol, "endpoint": "/api/trading/alerts"},
    }


async def _handle_dca() -> dict:
    """Info about DCA bot."""
    return {
        "response": (
            "DCA Bot — Dollar Cost Averaging:\n"
            "  POST /api/trading/dca/create — Create a DCA order\n"
            "  GET  /api/trading/dca/list   — List active orders\n"
            "  POST /api/trading/dca/cancel — Cancel an order\n\n"
            "Example: Buy $10 of SOL every day:\n"
            "  {\"token\": \"SOL\", \"amount_usdc\": 10, \"interval\": \"daily\", \"wallet\": \"YOUR_WALLET\"}\n\n"
            "Intervals: hourly, daily, weekly, monthly\n"
            "37 tokens supported."
        ),
        "type": "dca",
        "data": {"endpoint": "/api/trading/dca/create"},
    }


async def _handle_portfolio(intent: ParsedIntent) -> dict:
    """Fetch portfolio for a wallet address."""
    address = intent.address
    if not address:
        return {
            "response": (
                "Portfolio tracker — provide your wallet address:\n"
                "  GET /api/trading/portfolio?wallet=YOUR_WALLET\n"
                "Or say: portfolio <YOUR_SOLANA_ADDRESS>"
            ),
            "type": "help",
            "data": None,
        }

    try:
        from features.web3_services import analyze_wallet

        result = await analyze_wallet(address)
        if "error" in result:
            return {"response": result["error"], "type": "error", "data": None}

        total = result.get("total_value_usd", 0)
        tokens = result.get("tokens", [])
        lines = [f"Portfolio for {address[:8]}...{address[-4:]}:", f"  Total: ${total:,.2f}"]
        for t in tokens[:10]:
            name = t.get("symbol", "?")
            val = t.get("value_usd", 0)
            amt = t.get("amount", 0)
            if val > 0.01:
                lines.append(f"  {name}: {amt:,.4f} (${val:,.2f})")

        return {
            "response": "\n".join(lines),
            "type": "portfolio",
            "data": result,
        }
    except Exception as exc:
        logger.warning("Portfolio lookup failed: %s", exc)
        return {
            "response": f"Could not fetch portfolio for {address[:8]}... Try the API: GET /api/trading/portfolio?wallet={address}",
            "type": "error",
            "data": None,
        }


async def _handle_bridge() -> dict:
    """Info about cross-chain bridge."""
    return {
        "response": (
            "Cross-chain Bridge (Li.Fi):\n"
            "  POST /api/public/bridge/quote — Get bridge quote\n"
            "  {\"from_chain\": \"solana\", \"to_chain\": \"base\", \"token\": \"USDC\", \"amount\": 100}\n\n"
            "Supported chains: Solana, Ethereum, Base, Polygon, Arbitrum, Avalanche, BNB, Optimism"
        ),
        "type": "bridge",
        "data": {"endpoint": "/api/public/bridge/quote"},
    }


async def _handle_buy_crypto(intent: ParsedIntent) -> dict:
    """Guide user to buy crypto with card."""
    symbol = intent.symbol or "SOL"
    return {
        "response": (
            f"Buy {symbol} with credit card:\n"
            f"  POST /api/fiat/onramp\n"
            f"  {{\"crypto\": \"{symbol}\", \"fiat_amount\": 50, \"fiat_currency\": \"USD\", \"wallet_address\": \"YOUR_WALLET\"}}\n\n"
            f"  GET /api/fiat/providers — See available providers\n"
            f"  GET /api/fiat/supported — See all supported tokens\n\n"
            f"Providers: Transak (1-3%), Moonpay (1.5-4.5%)\n"
            f"Supports: SOL, ETH, BTC, USDC, MATIC, AVAX, BNB\n"
            f"Payment: Card, bank transfer, Apple Pay, Google Pay"
        ),
        "type": "buy_crypto",
        "data": {"token": symbol, "endpoint": "/api/fiat/onramp"},
    }


async def _handle_llm(message: str) -> dict:
    """Route general questions to LLM (Groq -> Mistral fallback via LLMRouter)."""
    try:
        from ai.llm_router import LLMRouter, Tier

        llm = LLMRouter()
        system_prompt = (
            "You are MAXIA, an AI-to-AI marketplace assistant. "
            "Answer concisely about crypto, trading, AI services, and blockchain. "
            "Keep responses under 200 words. Be helpful and accurate."
        )

        response = await llm.call(
            prompt=message,
            tier=Tier.FAST,
            system=system_prompt,
            max_tokens=300,
        )

        if not response:
            return {
                "response": "I couldn't generate a response. Try a specific command like 'price SOL' or 'help'.",
                "type": "llm",
                "data": None,
            }

        return {"response": response, "type": "llm", "data": None}

    except Exception as exc:
        err = safe_error(exc, "chat_llm")
        return {
            "response": "AI assistant is temporarily unavailable. Try 'help' for available commands.",
            "type": "error",
            "data": err,
        }


# ── Intent Router ──

_INTENT_HANDLERS = {
    "help": lambda _: _handle_help(),
    "price": _handle_price,
    "swap": _handle_swap,
    "swap_help": lambda _: _handle_swap_help(),
    "risk": _handle_risk,
    "leaderboard": lambda _: _handle_leaderboard(),
    "stocks": lambda _: _handle_stocks(),
    "gpu": lambda _: _handle_gpu(),
    "yield": lambda _: _handle_yield(),
    "alert": _handle_alert,
    "dca": lambda _: _handle_dca(),
    "portfolio": _handle_portfolio,
    "bridge": lambda _: _handle_bridge(),
    "buy_crypto": _handle_buy_crypto,
}


async def _route_intent(intent: ParsedIntent) -> dict:
    """Route a parsed intent to the appropriate handler."""
    handler = _INTENT_HANDLERS.get(intent.intent)
    if handler:
        return await handler(intent)
    # Default: LLM fallback
    return await _handle_llm(intent.raw_message)


# ── API Models ──

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=1000, description="User message")
    wallet: Optional[str] = Field(None, max_length=60, description="User wallet for TX building")


class ChatResponse(BaseModel):
    response: str
    type: str
    data: Optional[dict | list] = None


# ── Endpoint ──

@router.post("", response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request):
    """Main chat endpoint. Accepts natural language, returns structured response."""
    # Rate limiting by IP
    client_ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(client_ip):
        return ChatResponse(
            response="Rate limited. Maximum 10 requests per minute.",
            type="error",
            data=None,
        )

    message = req.message.strip()
    if not message:
        return ChatResponse(
            response="Please send a message. Type 'help' for available commands.",
            type="error",
            data=None,
        )

    try:
        intent = _detect_intent(message)
        # ONE-52: pass wallet to intent for TX building
        if req.wallet and len(req.wallet) >= 20:
            from dataclasses import replace
            intent = replace(intent, wallet=req.wallet)
        result = await _route_intent(intent)
        return ChatResponse(
            response=result.get("response", ""),
            type=result.get("type", "unknown"),
            data=result.get("data"),
        )
    except Exception as exc:
        err = safe_error(exc, "chat_handler")
        return ChatResponse(
            response="An unexpected error occurred. Please try again.",
            type="error",
            data=err,
        )
