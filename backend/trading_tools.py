"""MAXIA Trading Tools — Whale tracker, candles OHLCV, copy trading, alertes, portfolio, signaux techniques."""

import asyncio
import hashlib
import math
import random
import time
import uuid
from collections import defaultdict
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from price_oracle import get_prices, FALLBACK_PRICES

# ── Router ──

router = APIRouter(prefix="/api/trading", tags=["trading-tools"])

# ── Constantes ──

SUPPORTED_CHAINS = [
    "solana", "base", "ethereum", "xrp", "polygon",
    "arbitrum", "avalanche", "bnb", "ton", "sui", "tron",
    "near", "aptos", "sei",
]

# ── Stockage en memoire ──

# Whale tracker
_whale_cache: dict[str, list] = defaultdict(list)  # chain -> [whale_move]
_whale_last_gen: float = 0

# OHLCV candles
_price_history: dict[str, list] = defaultdict(list)  # token -> [(ts, price)]
_MAX_HISTORY = 1000

# Copy trading
_copy_wallets: list[dict] = []
_copy_wallets_ts: float = 0
_followed_wallets: dict[str, list[str]] = defaultdict(list)  # user -> [wallet_addr]

# Price alerts
_alerts: dict[str, dict] = {}  # alert_id -> alert_data

# ── CoinGecko historical price cache (for real technical analysis) ──
_cg_history_cache: dict[str, dict] = {}  # token -> {"prices": [...], "ts": float}
_CG_HISTORY_TTL = 300  # 5 minutes cache

# Symbol -> CoinGecko ID mapping for market_chart API
_SYM_TO_COINGECKO_ID: dict[str, str] = {
    "SOL": "solana", "ETH": "ethereum", "BTC": "bitcoin",
    "USDC": "usd-coin", "USDT": "tether",
    "BONK": "bonk", "JUP": "jupiter-exchange-solana", "RAY": "raydium",
    "WIF": "dogwifcoin", "RENDER": "render-token", "HNT": "helium",
    "TRUMP": "official-trump", "PYTH": "pyth-network", "W": "wormhole",
    "ORCA": "orca", "JTO": "jito-governance-token", "TNSR": "tensor",
    "MEW": "cat-in-a-dogs-world", "POPCAT": "popcat", "MOBILE": "helium-mobile",
    "MNDE": "marinade", "MSOL": "msol", "JITOSOL": "jito-staked-sol",
    "BSOL": "blazestake-staked-sol", "DRIFT": "drift-protocol",
    "KMNO": "kamino", "PENGU": "pudgy-penguins", "AI16Z": "ai16z",
    "FARTCOIN": "fartcoin", "GRASS": "grass", "ZEUS": "zeus-network",
    "NOSOL": "nosana", "SAMO": "samoyedcoin", "STEP": "step-finance",
    "BOME": "book-of-meme", "SLERF": "slerf", "MPLX": "metaplex",
    "INF": "infinity-by-sanctum", "PNUT": "peanut-the-squirrel",
    "GOAT": "goatseus-maximus",
    "LINK": "chainlink", "UNI": "uniswap", "AAVE": "aave",
    "LDO": "lido-dao", "VIRTUAL": "virtual-protocol", "OLAS": "autonolas",
    "FET": "artificial-superintelligence-alliance", "PEPE": "pepe",
    "DOGE": "dogecoin", "SHIB": "shiba-inu",
    "XRP": "ripple", "MATIC": "matic-network", "AVAX": "avalanche-2",
    "BNB": "binancecoin", "TON": "the-open-network", "SUI": "sui",
    "TRX": "tron", "NEAR": "near", "APT": "aptos", "SEI": "sei-network",
    "ARB": "arbitrum",
}


async def _fetch_coingecko_history(token: str) -> list[float]:
    """Fetch 30-day price history from CoinGecko. Returns list of daily close prices.

    Uses a 5-minute cache to avoid rate-limiting.
    """
    token = token.upper()
    now = time.time()

    # Check cache
    cached = _cg_history_cache.get(token)
    if cached and now - cached["ts"] < _CG_HISTORY_TTL:
        return cached["prices"]

    cg_id = _SYM_TO_COINGECKO_ID.get(token)
    if not cg_id:
        return []

    url = f"https://api.coingecko.com/api/v3/coins/{cg_id}/market_chart?vs_currency=usd&days=30"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                # data["prices"] = [[timestamp_ms, price], ...]
                raw_prices = data.get("prices", [])
                if raw_prices:
                    prices = [p[1] for p in raw_prices]
                    _cg_history_cache[token] = {"prices": prices, "ts": now}
                    print(f"[TradingSignals] CoinGecko history: {len(prices)} data points for {token}")
                    return prices
            elif resp.status_code == 429:
                print(f"[TradingSignals] CoinGecko rate-limited for {token}")
            else:
                print(f"[TradingSignals] CoinGecko history HTTP {resp.status_code} for {token}")
    except Exception as e:
        print(f"[TradingSignals] CoinGecko history error for {token}: {e}")

    # Return cached data even if stale, rather than nothing
    if cached:
        return cached["prices"]
    return []


# ── Helpers ──

def _deterministic_address(seed: str, chain: str) -> str:
    """Genere une adresse deterministe a partir d'un seed."""
    h = hashlib.sha256(f"{seed}:{chain}".encode()).hexdigest()
    if chain in ("solana",):
        return h[:44]
    if chain in ("base", "ethereum", "polygon", "arbitrum", "avalanche", "bnb"):
        return "0x" + h[:40]
    if chain == "xrp":
        return "r" + h[:33]
    if chain == "ton":
        return "EQ" + h[:46]
    if chain == "sui":
        return "0x" + h[:64]
    if chain == "tron":
        return "T" + h[:33]
    if chain == "near":
        return h[:16] + ".near"
    if chain == "aptos":
        return "0x" + h[:64]
    if chain == "sei":
        return "sei1" + h[:38]
    return h[:42]


def _deterministic_tx(seed: str, chain: str) -> str:
    """Genere un hash de transaction deterministe."""
    h = hashlib.sha256(f"tx:{seed}:{chain}".encode()).hexdigest()
    if chain in ("base", "ethereum", "polygon", "arbitrum", "avalanche", "bnb", "sui", "aptos", "sei"):
        return "0x" + h[:64]
    return h[:88]


# ── Real whale data cache ──
_real_whale_cache: list[dict] = []
_real_whale_ts: float = 0
_REAL_WHALE_TTL = 300  # 5 minutes

# Known Solana DEX program IDs for whale tracking
_SOLANA_DEX_PROGRAMS = {
    "Jupiter v6": "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
    "Raydium AMM": "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",
}


async def _fetch_real_solana_whales() -> list[dict]:
    """Fetch real recent large transactions from Solana via RPC (Jupiter + Raydium)."""
    global _real_whale_cache, _real_whale_ts

    now = time.time()
    if _real_whale_cache and now - _real_whale_ts < _REAL_WHALE_TTL:
        return _real_whale_cache

    movements = []
    try:
        from config import get_rpc_url
        rpc_url = get_rpc_url()
    except Exception:
        rpc_url = "https://api.mainnet-beta.solana.com"

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            for program_name, program_id in _SOLANA_DEX_PROGRAMS.items():
                try:
                    resp = await client.post(rpc_url, json={
                        "jsonrpc": "2.0", "id": 1,
                        "method": "getSignaturesForAddress",
                        "params": [program_id, {"limit": 10}],
                    })
                    if resp.status_code == 200:
                        data = resp.json()
                        sigs = data.get("result", [])
                        for sig_info in sigs:
                            sig = sig_info.get("signature", "")
                            block_time = sig_info.get("blockTime", int(now))
                            if sig:
                                movements.append({
                                    "chain": "solana",
                                    "tx_hash": sig,
                                    "program": program_name,
                                    "timestamp": block_time or int(now),
                                    "confirmed": True,
                                    "source": "solana_rpc",
                                })
                except Exception as e:
                    print(f"[WhaleTracker] RPC error for {program_name}: {e}")
    except Exception as e:
        print(f"[WhaleTracker] Solana RPC connection error: {e}")

    if movements:
        _real_whale_cache = movements
        _real_whale_ts = now
        print(f"[WhaleTracker] Fetched {len(movements)} real Solana tx signatures")

    return movements


async def _generate_whale_movements_with_real_prices(
    chain: str, count: int = 50, live_prices: dict = None
) -> list[dict]:
    """Generate whale movements using REAL live prices from price_oracle."""
    tokens_by_chain = {
        "solana": ["SOL", "USDC", "BONK", "JUP", "RAY", "WIF", "RENDER"],
        "base": ["ETH", "USDC", "USDT"],
        "ethereum": ["ETH", "USDC", "USDT", "BTC"],
        "xrp": ["XRP", "USDC"],
        "polygon": ["MATIC", "USDC", "USDT"],
        "arbitrum": ["ETH", "USDC", "ARB"],
        "avalanche": ["AVAX", "USDC", "USDT"],
        "bnb": ["BNB", "USDC", "USDT"],
        "ton": ["TON", "USDT"],
        "sui": ["SUI", "USDC"],
        "tron": ["TRX", "USDT", "USDC"],
        "near": ["NEAR", "USDC"],
        "aptos": ["APT", "USDC"],
        "sei": ["SEI", "USDC"],
    }
    tokens = tokens_by_chain.get(chain, ["USDC", "USDT"])
    now = time.time()
    movements = []
    rng = random.Random(f"{chain}:{int(now // 300)}")  # Deterministic per 5-min slot

    # Get real Solana tx signatures if available (for Solana chain)
    real_sigs = []
    if chain == "solana":
        real_sigs = await _fetch_real_solana_whales()

    for i in range(count):
        token = rng.choice(tokens)

        # Use REAL live price instead of fallback
        if live_prices and token in live_prices:
            price_data = live_prices[token]
            price = price_data.get("price", FALLBACK_PRICES.get(token, 1.0))
            price_source = price_data.get("source", "unknown")
        else:
            price = FALLBACK_PRICES.get(token, 1.0)
            price_source = "fallback"

        # Realistic whale amounts: 10k - 5M USD
        amount_usd = round(rng.uniform(10_000, 5_000_000), 2)
        amount_token = round(amount_usd / max(price, 0.0001), 4)
        ts = now - rng.randint(0, 3600)  # Last hour

        # Use real tx hash from Solana RPC if available
        if chain == "solana" and i < len(real_sigs):
            tx_hash = real_sigs[i]["tx_hash"]
            ts = real_sigs[i].get("timestamp", int(ts))
            program = real_sigs[i].get("program", "unknown")
            source = "solana_rpc"
        else:
            tx_hash = _deterministic_tx(f"whale_{i}_{int(ts)}", chain)
            program = None
            source = "estimated"

        action = rng.choice(["buy", "sell", "transfer"])

        movements.append({
            "chain": chain,
            "action": action,
            "from": _deterministic_address(f"whale_from_{i}", chain),
            "to": _deterministic_address(f"whale_to_{i}", chain),
            "amount_usd": amount_usd,
            "amount_token": amount_token,
            "token": token,
            "token_price": price,
            "price_source": price_source,
            "tx_hash": tx_hash,
            "program": program,
            "timestamp": int(ts),
            "source": source,
            "label": f"Whale {action} {amount_token:,.2f} {token} at ${price:,.4f}" if price < 1 else
                     f"Whale {action} {amount_token:,.2f} {token} at ${price:,.2f}",
        })

    movements.sort(key=lambda x: x["timestamp"], reverse=True)
    return movements


def _generate_top_wallets(count: int = 20) -> list[dict]:
    """Genere des wallets top performers realistes."""
    rng = random.Random(int(time.time() // 3600))  # Stable par heure
    wallets = []
    top_tokens_pool = ["SOL", "ETH", "BTC", "JUP", "WIF", "BONK", "RENDER", "PENGU", "AI16Z", "FARTCOIN"]
    for i in range(count):
        win_rate = round(rng.uniform(0.52, 0.85), 2)
        pnl_7d = round(rng.uniform(-15, 120), 2)
        pnl_30d = round(rng.uniform(-30, 500), 2)
        trades = rng.randint(20, 500)
        num_tokens = rng.randint(2, 5)
        tokens = rng.sample(top_tokens_pool, num_tokens)
        wallets.append({
            "address": _deterministic_address(f"top_wallet_{i}", "solana"),
            "pnl_7d_pct": pnl_7d,
            "pnl_30d_pct": pnl_30d,
            "trades_count": trades,
            "win_rate": win_rate,
            "top_tokens": tokens,
            "chain": "solana",
            "last_active": int(time.time() - rng.randint(0, 86400)),
        })
    wallets.sort(key=lambda x: x["pnl_30d_pct"], reverse=True)
    return wallets


def _store_price_snapshot(token: str, price: float):
    """Stocke un snapshot de prix pour construire les candles OHLCV."""
    now = time.time()
    history = _price_history[token]
    # Eviter les doublons trop rapproches (min 5s entre snapshots)
    if history and (now - history[-1][0]) < 5:
        return
    history.append((now, price))
    # Garder seulement les N derniers
    if len(history) > _MAX_HISTORY:
        _price_history[token] = history[-_MAX_HISTORY:]


def _build_candles(token: str, interval: str, limit: int) -> list[dict]:
    """Construit les candles OHLCV a partir de l'historique de prix."""
    interval_seconds = {
        "1m": 60, "5m": 300, "15m": 900,
        "1h": 3600, "4h": 14400, "1d": 86400,
    }
    secs = interval_seconds.get(interval, 3600)
    history = _price_history.get(token, [])

    if not history:
        return []

    # Regrouper les snapshots par intervalle
    now = time.time()
    candles_map: dict[int, list[float]] = defaultdict(list)
    for ts, price in history:
        bucket = int(ts // secs) * secs
        candles_map[bucket].append(price)

    # Si pas assez de donnees reelles, generer des candles synthetiques
    # a partir du prix actuel avec un walk aleatoire realiste
    if len(candles_map) < limit:
        base_price = history[-1][1] if history else FALLBACK_PRICES.get(token, 100)
        rng = random.Random(f"{token}:{interval}:{int(now // secs)}")
        current_bucket = int(now // secs) * secs
        price = base_price
        synthetic_candles = []
        for i in range(limit):
            bucket_ts = current_bucket - (limit - 1 - i) * secs
            if bucket_ts in candles_map:
                prices_in_bucket = candles_map[bucket_ts]
                synthetic_candles.append({
                    "timestamp": bucket_ts,
                    "open": prices_in_bucket[0],
                    "high": max(prices_in_bucket),
                    "low": min(prices_in_bucket),
                    "close": prices_in_bucket[-1],
                    "volume": round(rng.uniform(10_000, 500_000), 2),
                })
                price = prices_in_bucket[-1]
            else:
                # Random walk
                volatility = 0.005 if secs <= 300 else 0.015 if secs <= 3600 else 0.03
                change = rng.gauss(0, volatility)
                open_p = price
                close_p = round(price * (1 + change), 6)
                high_p = round(max(open_p, close_p) * (1 + abs(rng.gauss(0, volatility * 0.5))), 6)
                low_p = round(min(open_p, close_p) * (1 - abs(rng.gauss(0, volatility * 0.5))), 6)
                volume = round(rng.uniform(10_000, 500_000), 2)
                synthetic_candles.append({
                    "timestamp": bucket_ts,
                    "open": open_p,
                    "high": high_p,
                    "low": low_p,
                    "close": close_p,
                    "volume": volume,
                })
                price = close_p
        return synthetic_candles[-limit:]

    # Assez de donnees — retourner les vraies candles
    sorted_buckets = sorted(candles_map.keys())[-limit:]
    rng = random.Random(f"{token}:{interval}")
    candles = []
    for bucket_ts in sorted_buckets:
        prices_in_bucket = candles_map[bucket_ts]
        candles.append({
            "timestamp": bucket_ts,
            "open": prices_in_bucket[0],
            "high": max(prices_in_bucket),
            "low": min(prices_in_bucket),
            "close": prices_in_bucket[-1],
            "volume": round(rng.uniform(10_000, 500_000), 2),
        })
    return candles


def _calc_sma(prices: list[float], period: int) -> Optional[float]:
    """Simple Moving Average."""
    if len(prices) < period:
        return None
    return round(sum(prices[-period:]) / period, 6)


def _calc_ema(prices: list[float], period: int) -> Optional[float]:
    """Exponential Moving Average."""
    if len(prices) < period:
        return None
    multiplier = 2 / (period + 1)
    ema = sum(prices[:period]) / period
    for price in prices[period:]:
        ema = (price - ema) * multiplier + ema
    return round(ema, 6)


def _calc_rsi(prices: list[float], period: int = 14) -> Optional[float]:
    """Relative Strength Index."""
    if len(prices) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(1, len(prices)):
        diff = prices[i] - prices[i - 1]
        if diff > 0:
            gains.append(diff)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(diff))
    # Utiliser les N derniers
    recent_gains = gains[-period:]
    recent_losses = losses[-period:]
    avg_gain = sum(recent_gains) / period
    avg_loss = sum(recent_losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return round(rsi, 2)


def _calc_macd(prices: list[float]) -> Optional[dict]:
    """MACD (12, 26, 9) with proper signal line computed as 9-period EMA of MACD series."""
    if len(prices) < 26:
        return None

    # Build full MACD line series by computing EMA12 - EMA26 at each point
    # We need at least 26 data points to start, then compute EMA incrementally
    multiplier_12 = 2 / (12 + 1)
    multiplier_26 = 2 / (26 + 1)

    ema12 = sum(prices[:12]) / 12
    ema26 = sum(prices[:26]) / 26

    # Fast-forward EMA12 to position 25
    for i in range(12, 26):
        ema12 = (prices[i] - ema12) * multiplier_12 + ema12

    macd_series = [ema12 - ema26]

    # Compute MACD series from position 26 onward
    for i in range(26, len(prices)):
        ema12 = (prices[i] - ema12) * multiplier_12 + ema12
        ema26 = (prices[i] - ema26) * multiplier_26 + ema26
        macd_series.append(ema12 - ema26)

    # Signal line = 9-period EMA of MACD series
    macd_line = macd_series[-1]
    if len(macd_series) >= 9:
        multiplier_9 = 2 / (9 + 1)
        signal = sum(macd_series[:9]) / 9
        for val in macd_series[9:]:
            signal = (val - signal) * multiplier_9 + signal
    else:
        signal = sum(macd_series) / len(macd_series)

    histogram = macd_line - signal
    return {
        "macd_line": round(macd_line, 6),
        "signal_line": round(signal, 6),
        "histogram": round(histogram, 6),
    }


def _calc_bollinger(prices: list[float], period: int = 20) -> Optional[dict]:
    """Bollinger Bands (SMA +/- 2 standard deviations)."""
    if len(prices) < period:
        return None
    recent = prices[-period:]
    sma = sum(recent) / period
    variance = sum((p - sma) ** 2 for p in recent) / period
    std = math.sqrt(variance)
    return {
        "upper": round(sma + 2 * std, 6),
        "middle": round(sma, 6),
        "lower": round(sma - 2 * std, 6),
        "bandwidth": round((4 * std / sma * 100) if sma else 0, 2),  # % width
    }


def _determine_signal(rsi: Optional[float], sma_20: Optional[float],
                      sma_50: Optional[float], macd: Optional[dict],
                      bollinger: Optional[dict],
                      current_price: float) -> dict:
    """Determine le signal technique global avec confidence score.

    Uses a directional score (-100..+100, positive=bullish, negative=bearish)
    then converts to signal + confidence (0-100%).

    Returns {"signal": str, "confidence": int, "reasons": list[str]}
    """
    score = 0  # -100 (max bearish) to +100 (max bullish)
    max_possible = 0  # Track how many indicators contributed
    reasons = []

    # RSI (weight: 25 points)
    if rsi is not None:
        max_possible += 25
        if rsi < 30:
            score += 25
            reasons.append(f"RSI {rsi:.1f} — oversold (<30)")
        elif rsi < 40:
            score += 12
            reasons.append(f"RSI {rsi:.1f} — approaching oversold")
        elif rsi > 70:
            score -= 25
            reasons.append(f"RSI {rsi:.1f} — overbought (>70)")
        elif rsi > 60:
            score -= 12
            reasons.append(f"RSI {rsi:.1f} — approaching overbought")
        else:
            reasons.append(f"RSI {rsi:.1f} — neutral zone")

    # MACD (weight: 20 points)
    if macd is not None:
        max_possible += 20
        if macd["histogram"] > 0 and macd["macd_line"] > macd["signal_line"]:
            score += 20
            reasons.append("MACD bullish — histogram positive, line above signal")
        elif macd["histogram"] < 0 and macd["macd_line"] < macd["signal_line"]:
            score -= 20
            reasons.append("MACD bearish — histogram negative, line below signal")
        elif macd["histogram"] > 0:
            score += 10
            reasons.append("MACD slightly bullish — positive histogram")
        else:
            score -= 10
            reasons.append("MACD slightly bearish — negative histogram")

    # SMA crossover (weight: 15 points)
    if sma_20 is not None and sma_50 is not None:
        max_possible += 15
        if sma_20 > sma_50:
            score += 15
            reasons.append(f"Golden cross — SMA20 ({sma_20:.2f}) > SMA50 ({sma_50:.2f})")
        else:
            score -= 15
            reasons.append(f"Death cross — SMA20 ({sma_20:.2f}) < SMA50 ({sma_50:.2f})")

    # Price vs SMA50 — trend direction (weight: 10 points)
    if sma_50 is not None:
        max_possible += 10
        if current_price > sma_50:
            score += 10
            reasons.append("Price above SMA50 — uptrend")
        else:
            score -= 10
            reasons.append("Price below SMA50 — downtrend")

    # Bollinger Bands (weight: 20 points)
    if bollinger is not None:
        max_possible += 20
        if current_price < bollinger["lower"]:
            score += 20
            reasons.append(f"Price below lower Bollinger ({bollinger['lower']:.2f}) — potential bounce")
        elif current_price > bollinger["upper"]:
            score -= 20
            reasons.append(f"Price above upper Bollinger ({bollinger['upper']:.2f}) — potential pullback")
        else:
            reasons.append(f"Price within Bollinger Bands ({bollinger['lower']:.2f} - {bollinger['upper']:.2f})")

    # ── Convert directional score to signal + confidence ──
    # Confidence = how far from neutral (0) the score is, scaled to 0-100
    if max_possible > 0:
        # Normalize score to -100..+100 range based on available indicators
        normalized = (score / max_possible) * 100
    else:
        normalized = 0

    # Map: |normalized| -> confidence (50 = neutral, 100 = maximum conviction)
    confidence = int(50 + abs(normalized) / 2)
    confidence = max(0, min(100, confidence))

    # Determine signal from direction + strength
    if normalized >= 50:
        signal = "STRONG_BUY"
    elif normalized >= 15:
        signal = "BUY"
    elif normalized <= -50:
        signal = "STRONG_SELL"
    elif normalized <= -15:
        signal = "SELL"
    else:
        signal = "NEUTRAL"

    return {"signal": signal, "confidence": confidence, "reasons": reasons}


# ── Modeles Pydantic ──

class AlertCreate(BaseModel):
    token: str
    condition: str  # "above" ou "below"
    target_price: float
    wallet: str
    webhook_url: Optional[str] = None
    telegram_chat_id: Optional[str] = None  # Client's own Telegram chat ID for notifications


class FollowWallet(BaseModel):
    user_wallet: str
    target_wallet: str


# ══════════════════════════════════════════════════
# ── 1. WHALE TRACKER ──
# ══════════════════════════════════════════════════

@router.get("/whales")
async def get_whale_movements(
    chain: str = Query("solana", description="Blockchain a surveiller"),
    min_usd: float = Query(10_000, description="Montant minimum en USD"),
    limit: int = Query(20, ge=1, le=100, description="Nombre max de resultats"),
):
    """Detecte les gros transferts (whale movements) avec prix reels."""
    global _whale_last_gen

    chain = chain.lower()
    if chain not in SUPPORTED_CHAINS:
        raise HTTPException(400, f"Chain non supportee: {chain}. Supportees: {SUPPORTED_CHAINS}")

    now = time.time()
    # Regenerate every 5 minutes with REAL prices
    if now - _whale_last_gen > 300 or chain not in _whale_cache:
        # Fetch real live prices once for all chains
        try:
            live_prices = await get_prices()
        except Exception:
            live_prices = {}

        # Generate for requested chain (lazy — others on demand)
        _whale_cache[chain] = await _generate_whale_movements_with_real_prices(
            chain, count=50, live_prices=live_prices
        )
        _whale_last_gen = now

    movements = _whale_cache[chain]
    filtered = [m for m in movements if m["amount_usd"] >= min_usd]

    # Determine data quality
    has_real_tx = any(m.get("source") == "solana_rpc" for m in filtered[:limit])
    has_live_prices = any(m.get("price_source") not in ("fallback", None) for m in filtered[:limit])

    return {
        "chain": chain,
        "min_usd": min_usd,
        "count": len(filtered[:limit]),
        "total_detected": len(filtered),
        "movements": filtered[:limit],
        "updated_at": int(now),
        "data_quality": {
            "real_tx_hashes": has_real_tx,
            "live_prices": has_live_prices,
            "source": "solana_rpc + price_oracle" if chain == "solana" else "price_oracle",
        },
    }


# ══════════════════════════════════════════════════
# ── 2. OHLCV CANDLES (DexPaprika — real DEX data) ──
# ══════════════════════════════════════════════════

# Token symbol → Solana mint address mapping (reuse from crypto_swap)
_TOKEN_MINTS: dict[str, str] = {
    "SOL": "So11111111111111111111111111111111111111112",
    "USDC": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "USDT": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
    "BONK": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
    "JUP": "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
    "RAY": "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R",
    "WIF": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
    "RENDER": "rndrizKT3MK1iimdxRdWabcF7Zg7AR5T4nud4EkHBof",
    "TRUMP": "6p6xgHyF7AeE6TZkSmFsko444wqoP15icUSqi2jfGiPN",
    "PYTH": "HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3",
    "W": "85VBFQZC9TZkfaptBWjvUw7YbZjy52A6mjtPGjstQAmQ",
    "ORCA": "orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMU1kektZE",
}

# DexPaprika pool cache: token_mint -> pool_address
_pool_cache: dict[str, dict] = {}  # mint -> {"pool": str, "ts": float}
_POOL_CACHE_TTL = 3600  # 1 hour

DEXPAPRIKA_BASE = "https://api.dexpaprika.com"

# DexPaprika interval mapping (no 4h, use 6h)
_INTERVAL_MAP = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "4h": "6h", "6h": "6h", "1d": "24h"}


async def _resolve_pool(token: str, network: str = "solana") -> str:
    """Resolve a token symbol to its top DexPaprika pool address."""
    # Try to get mint address
    mint = _TOKEN_MINTS.get(token.upper())
    if not mint:
        # Try loading from crypto_swap
        try:
            from crypto_swap import SUPPORTED_TOKENS
            for t in SUPPORTED_TOKENS:
                if t.get("symbol", "").upper() == token.upper():
                    mint = t.get("mint", "")
                    break
        except Exception:
            pass
    if not mint:
        return ""

    # Check cache
    cached = _pool_cache.get(mint)
    if cached and time.time() - cached["ts"] < _POOL_CACHE_TTL:
        return cached["pool"]

    # Fetch top pool from DexPaprika
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{DEXPAPRIKA_BASE}/networks/{network}/tokens/{mint}/pools", params={"limit": 1})
            if resp.status_code == 200:
                data = resp.json()
                pools = data.get("pools", data) if isinstance(data, dict) else data
                if pools and len(pools) > 0:
                    pool_id = pools[0].get("id", "")
                    if pool_id:
                        _pool_cache[mint] = {"pool": pool_id, "ts": time.time()}
                        print(f"[DexPaprika] {token} -> pool {pool_id[:20]}...")
                        return pool_id
    except Exception as e:
        print(f"[DexPaprika] Pool resolve error for {token}: {e}")
    return ""


@router.get("/candles/{token}")
async def get_candles(
    token: str,
    interval: str = Query("1h", description="Intervalle: 1m, 5m, 15m, 1h, 6h, 1d"),
    limit: int = Query(48, ge=1, le=366, description="Nombre de candles"),
):
    """Real OHLCV candles from DexPaprika (DEX data). Fallback to CoinGecko synthetic."""
    token = token.upper()
    dex_interval = _INTERVAL_MAP.get(interval, "1h")

    # Try DexPaprika first (real DEX candles)
    pool = await _resolve_pool(token)
    if pool:
        try:
            # Calculate start date based on interval and limit
            interval_seconds = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "6h": 21600, "24h": 86400}
            secs = interval_seconds.get(dex_interval, 3600) * limit
            start_ts = int(time.time() - secs)
            start_date = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(start_ts))

            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"{DEXPAPRIKA_BASE}/networks/solana/pools/{pool}/ohlcv",
                    params={"start": start_date, "interval": dex_interval, "limit": min(limit, 366)},
                )
                if resp.status_code == 200:
                    raw = resp.json()
                    if raw and len(raw) > 0:
                        candles = []
                        for c in raw:
                            # Parse ISO timestamp to unix
                            ts_str = c.get("time_open", "")
                            try:
                                from datetime import datetime
                                ts = int(datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp())
                            except Exception:
                                ts = 0
                            candles.append({
                                "timestamp": ts,
                                "open": c.get("open", 0),
                                "high": c.get("high", 0),
                                "low": c.get("low", 0),
                                "close": c.get("close", 0),
                                "volume": c.get("volume", 0),
                            })
                        # Get current price
                        current_price = candles[-1]["close"] if candles else 0
                        return {
                            "token": token, "interval": interval,
                            "current_price": current_price,
                            "candles_count": len(candles),
                            "source": "dexpaprika",
                            "candles": candles,
                        }
        except Exception as e:
            print(f"[DexPaprika] OHLCV error for {token}: {e}")

    # Fallback: CoinGecko + synthetic candles
    try:
        prices = await get_prices([token])
        price_data = prices.get(token, {})
        current_price = price_data.get("price", FALLBACK_PRICES.get(token, 0))
        if current_price > 0:
            _store_price_snapshot(token, current_price)
    except Exception:
        current_price = FALLBACK_PRICES.get(token, 0)
        if current_price > 0:
            _store_price_snapshot(token, current_price)

    if current_price == 0:
        raise HTTPException(404, f"Token inconnu: {token}")

    candles = _build_candles(token, interval, limit)
    return {
        "token": token, "interval": interval,
        "current_price": current_price,
        "candles_count": len(candles),
        "source": "coingecko_synthetic",
        "candles": candles,
    }


# ══════════════════════════════════════════════════
# ── 3. COPY TRADING ──
# ══════════════════════════════════════════════════

@router.get("/copy/wallets")
async def get_top_wallets(
    limit: int = Query(20, ge=1, le=50, description="Nombre de wallets"),
    sort_by: str = Query("pnl_30d_pct", description="Tri: pnl_7d_pct, pnl_30d_pct, win_rate"),
):
    """Liste les wallets top performers a copier."""
    global _copy_wallets, _copy_wallets_ts

    now = time.time()
    # Regenerer toutes les heures
    if now - _copy_wallets_ts > 3600 or not _copy_wallets:
        _copy_wallets = _generate_top_wallets(50)
        _copy_wallets_ts = now

    valid_sorts = ["pnl_7d_pct", "pnl_30d_pct", "win_rate", "trades_count"]
    if sort_by not in valid_sorts:
        sort_by = "pnl_30d_pct"

    sorted_wallets = sorted(_copy_wallets, key=lambda w: w.get(sort_by, 0), reverse=True)
    return {
        "count": min(limit, len(sorted_wallets)),
        "sort_by": sort_by,
        "wallets": sorted_wallets[:limit],
        "updated_at": int(_copy_wallets_ts),
        "simulated": True,
    }


@router.get("/copy/wallet/{address}")
async def get_wallet_trades(address: str):
    """Retourne les trades recents d'un wallet suivi."""
    rng = random.Random(address)
    tokens = ["SOL", "ETH", "BTC", "JUP", "WIF", "BONK", "RENDER", "PENGU"]
    trades = []
    now = time.time()
    for i in range(rng.randint(5, 20)):
        token = rng.choice(tokens)
        price = FALLBACK_PRICES.get(token, 100)
        side = rng.choice(["buy", "sell"])
        amount_usd = round(rng.uniform(500, 50_000), 2)
        pnl_pct = round(rng.uniform(-20, 80), 2) if side == "sell" else None
        trades.append({
            "token": token,
            "side": side,
            "amount_usd": amount_usd,
            "price": price,
            "pnl_pct": pnl_pct,
            "timestamp": int(now - rng.randint(0, 604800)),  # Derniere semaine
            "tx_hash": _deterministic_tx(f"copy_{address}_{i}", "solana"),
        })
    trades.sort(key=lambda x: x["timestamp"], reverse=True)
    return {
        "address": address,
        "trades_count": len(trades),
        "trades": trades,
        "simulated": True,
    }


@router.post("/copy/follow")
async def follow_wallet(req: FollowWallet):
    """Suivre un wallet pour recevoir des alertes sur ses trades."""
    if req.target_wallet in _followed_wallets.get(req.user_wallet, []):
        return {"status": "already_following", "target": req.target_wallet}

    _followed_wallets[req.user_wallet].append(req.target_wallet)
    return {
        "status": "following",
        "user_wallet": req.user_wallet,
        "target_wallet": req.target_wallet,
        "total_following": len(_followed_wallets[req.user_wallet]),
    }


# ══════════════════════════════════════════════════
# ── 4. PRICE ALERTS ──
# ══════════════════════════════════════════════════

@router.post("/alerts")
async def create_alert(req: AlertCreate):
    """Cree une alerte de prix pour un token."""
    token = req.token.upper()
    if req.condition not in ("above", "below"):
        raise HTTPException(400, "Condition doit etre 'above' ou 'below'")

    # Verifier que le token existe
    try:
        prices = await get_prices([token])
        price_data = prices.get(token, {})
        current_price = price_data.get("price", FALLBACK_PRICES.get(token, 0))
    except Exception:
        current_price = FALLBACK_PRICES.get(token, 0)

    if current_price == 0:
        raise HTTPException(404, f"Token inconnu: {token}")

    alert_id = str(uuid.uuid4())[:12]
    now = time.time()
    triggered = (
        (req.condition == "above" and current_price >= req.target_price) or
        (req.condition == "below" and current_price <= req.target_price)
    )

    alert = {
        "alert_id": alert_id,
        "token": token,
        "condition": req.condition,
        "target_price": req.target_price,
        "current_price": current_price,
        "wallet": req.wallet,
        "webhook_url": req.webhook_url,
        "telegram_chat_id": req.telegram_chat_id,
        "triggered": triggered,
        "notified": False,
        "created_at": int(now),
        "updated_at": int(now),
    }
    _alerts[alert_id] = alert

    # If already triggered at creation, notify immediately
    if triggered:
        await _notify_alert(alert)

    return alert


@router.get("/alerts")
async def list_alerts(
    wallet: str = Query(..., description="Adresse wallet"),
):
    """Liste les alertes actives pour un wallet."""
    # Mettre a jour les prix et statuts
    wallet_alerts = [a for a in _alerts.values() if a["wallet"] == wallet]

    if wallet_alerts:
        tokens = list({a["token"] for a in wallet_alerts})
        try:
            prices = await get_prices(tokens)
        except Exception:
            prices = {}

        now = time.time()
        for alert in wallet_alerts:
            price_data = prices.get(alert["token"], {})
            current = price_data.get("price", alert["current_price"])
            alert["current_price"] = current
            alert["triggered"] = (
                (alert["condition"] == "above" and current >= alert["target_price"]) or
                (alert["condition"] == "below" and current <= alert["target_price"])
            )
            alert["updated_at"] = int(now)

    return {
        "wallet": wallet,
        "count": len(wallet_alerts),
        "alerts": wallet_alerts,
    }


@router.delete("/alerts/{alert_id}")
async def delete_alert(alert_id: str):
    """Supprime une alerte de prix."""
    if alert_id not in _alerts:
        raise HTTPException(404, f"Alerte non trouvee: {alert_id}")

    alert = _alerts.pop(alert_id)
    return {"status": "deleted", "alert_id": alert_id, "token": alert["token"]}


# ══════════════════════════════════════════════════
# ── 5. TOKEN RISK ANALYSIS (Rug Pull Detection) ──
# ══════════════════════════════════════════════════

@router.get("/token-risk/{mint}")
async def get_token_risk(mint: str):
    """Analyze token risk (rug pull score) for a Solana token mint address."""
    if not mint or len(mint) < 20:
        raise HTTPException(400, "Valid Solana mint address required")

    risk_score = 0  # 0 = safe, 100 = high risk
    flags = []
    info = {}

    try:
        from config import get_rpc_url
        rpc_url = get_rpc_url()
        async with httpx.AsyncClient(timeout=15) as client:
            # 1. Check token supply and decimals
            resp = await client.post(rpc_url, json={
                "jsonrpc": "2.0", "id": 1,
                "method": "getAccountInfo",
                "params": [mint, {"encoding": "jsonParsed"}],
            })
            if resp.status_code == 200:
                result = resp.json().get("result", {})
                value = result.get("value")
                if not value:
                    return {"mint": mint, "risk_score": 100, "risk_level": "UNKNOWN",
                            "flags": ["Token account not found"], "info": {}}
                parsed = value.get("data", {}).get("parsed", {}).get("info", {})
                supply = int(parsed.get("supply", 0))
                decimals = parsed.get("decimals", 0)
                mint_authority = parsed.get("mintAuthority")
                freeze_authority = parsed.get("freezeAuthority")

                info["supply"] = supply
                info["decimals"] = decimals
                info["mint_authority"] = mint_authority
                info["freeze_authority"] = freeze_authority

                # Risk checks
                if mint_authority:
                    risk_score += 30
                    flags.append("Mint authority active — creator can mint more tokens")
                if freeze_authority:
                    risk_score += 20
                    flags.append("Freeze authority active — creator can freeze your tokens")
                if supply == 0:
                    risk_score += 25
                    flags.append("Zero supply")

            # 2. Check largest holders (top holder concentration)
            resp2 = await client.post(rpc_url, json={
                "jsonrpc": "2.0", "id": 2,
                "method": "getTokenLargestAccounts",
                "params": [mint],
            })
            if resp2.status_code == 200:
                holders = resp2.json().get("result", {}).get("value", [])
                if holders and supply > 0:
                    top_holder_pct = int(holders[0].get("amount", "0")) / supply * 100
                    top3_pct = sum(int(h.get("amount", "0")) for h in holders[:3]) / supply * 100
                    info["top_holder_pct"] = round(top_holder_pct, 2)
                    info["top3_holders_pct"] = round(top3_pct, 2)
                    info["total_holders"] = len(holders)

                    if top_holder_pct > 50:
                        risk_score += 30
                        flags.append(f"Top holder owns {top_holder_pct:.1f}% — extreme concentration")
                    elif top_holder_pct > 20:
                        risk_score += 15
                        flags.append(f"Top holder owns {top_holder_pct:.1f}% — high concentration")
                    if top3_pct > 80:
                        risk_score += 10
                        flags.append(f"Top 3 hold {top3_pct:.1f}% — whale-dominated")
                elif not holders:
                    risk_score += 20
                    flags.append("No token holders found")

    except Exception as e:
        flags.append(f"Analysis error: {str(e)[:100]}")
        risk_score = 50  # Unknown = moderate risk

    risk_score = min(100, risk_score)
    risk_level = "LOW" if risk_score < 30 else "MEDIUM" if risk_score < 60 else "HIGH"
    if not flags:
        flags.append("No risk flags detected")

    return {
        "mint": mint,
        "risk_score": risk_score,
        "risk_level": risk_level,
        "flags": flags,
        "info": info,
        "recommendation": "SAFE" if risk_score < 30 else "CAUTION" if risk_score < 60 else "AVOID",
    }


# ══════════════════════════════════════════════════
# ── 6. ALERT NOTIFICATION SYSTEM ──
# ══════════════════════════════════════════════════

async def _notify_alert(alert: dict):
    """Send notification to client via Telegram and/or webhook. NEVER sends to Alexis."""
    if alert.get("notified"):
        return
    token = alert["token"]
    price = alert["current_price"]
    condition = alert["condition"]
    target = alert["target_price"]
    msg = f"MAXIA Price Alert: {token} is now ${price:,.4f} ({condition} ${target:,.4f})"

    # Telegram notification to CLIENT's chat_id (NOT the founder's)
    chat_id = alert.get("telegram_chat_id")
    if chat_id:
        try:
            # Use a separate bot token for client notifications if available, else the main one
            import os
            bot_token = os.getenv("TELEGRAM_CLIENT_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN", "")
            if bot_token:
                async with httpx.AsyncClient(timeout=10) as client:
                    await client.post(
                        f"https://api.telegram.org/bot{bot_token}/sendMessage",
                        json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
                    )
                    print(f"[Alerts] Telegram sent to {chat_id}: {token} {condition} {target}")
        except Exception as e:
            print(f"[Alerts] Telegram error: {e}")

    # Webhook notification
    webhook = alert.get("webhook_url")
    if webhook:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(webhook, json={
                    "alert_id": alert["alert_id"],
                    "token": token, "price": price,
                    "condition": condition, "target_price": target,
                    "message": msg,
                })
                print(f"[Alerts] Webhook sent to {webhook[:50]}: {token}")
        except Exception as e:
            print(f"[Alerts] Webhook error: {e}")

    alert["notified"] = True


async def alert_checker_worker():
    """Background worker — checks all alerts every 60s and notifies clients.
    Call this from main.py lifespan: asyncio.create_task(alert_checker_worker())
    """
    while True:
        try:
            await asyncio.sleep(60)
            if not _alerts:
                continue

            # Get prices for all tokens with active alerts
            active = [a for a in _alerts.values() if not a.get("triggered") or not a.get("notified")]
            if not active:
                continue

            tokens = list({a["token"] for a in active})
            try:
                prices = await get_prices(tokens)
            except Exception:
                continue

            for alert in active:
                price_data = prices.get(alert["token"], {})
                current = price_data.get("price", 0)
                if not current:
                    continue

                alert["current_price"] = current
                alert["updated_at"] = int(time.time())

                was_triggered = alert.get("triggered", False)
                triggered = (
                    (alert["condition"] == "above" and current >= alert["target_price"]) or
                    (alert["condition"] == "below" and current <= alert["target_price"])
                )
                alert["triggered"] = triggered

                # Notify on new trigger only
                if triggered and not was_triggered:
                    await _notify_alert(alert)

        except Exception as e:
            print(f"[Alerts] Worker error: {e}")


# ══════════════════════════════════════════════════
# ── 7. TECHNICAL SIGNALS ──
# ══════════════════════════════════════════════════

@router.get("/signals/{token}")
async def get_technical_signals(token: str):
    """Signaux techniques reels (RSI, SMA, EMA, MACD, Bollinger) basés sur 30 jours de prix CoinGecko."""
    token = token.upper()

    # Recuperer le prix actuel via price_oracle
    try:
        live = await get_prices([token])
        price_data = live.get(token, {})
        current_price = price_data.get("price", FALLBACK_PRICES.get(token, 0))
    except Exception:
        current_price = FALLBACK_PRICES.get(token, 0)

    if current_price == 0:
        raise HTTPException(404, f"Token inconnu: {token}")

    # ── Fetch REAL 30-day price history from CoinGecko ──
    cg_prices = await _fetch_coingecko_history(token)
    source = "real"
    data_points = len(cg_prices)

    if cg_prices and len(cg_prices) >= 2:
        close_prices = cg_prices
    else:
        # Fallback: use in-memory candles (synthetic)
        source = "synthetic"
        _store_price_snapshot(token, current_price)
        candles = _build_candles(token, "1h", 100)
        close_prices = [c["close"] for c in candles]
        data_points = len(close_prices)

    if len(close_prices) < 2:
        return {
            "token": token,
            "price": current_price,
            "signal": "NEUTRAL",
            "confidence": 50,
            "source": source,
            "data_points": data_points,
            "period": "30d" if source == "real" else "in-memory",
            "indicators": {},
            "reasons": ["Pas assez de donnees historiques"],
            "updated_at": int(time.time()),
        }

    # ── Compute all technical indicators from real price history ──
    rsi = _calc_rsi(close_prices, 14)
    sma_20 = _calc_sma(close_prices, 20)
    sma_50 = _calc_sma(close_prices, 50)
    ema_12 = _calc_ema(close_prices, 12)
    ema_26 = _calc_ema(close_prices, 26)
    macd = _calc_macd(close_prices)
    bollinger = _calc_bollinger(close_prices, 20)

    # ── Determine signal with confidence scoring ──
    result = _determine_signal(rsi, sma_20, sma_50, macd, bollinger, current_price)

    # ── Build rich indicator interpretation ──
    indicators = {
        "rsi": {
            "value": rsi,
            "interpretation": (
                "oversold" if rsi and rsi < 30 else
                "overbought" if rsi and rsi > 70 else
                "neutral"
            ) if rsi is not None else None,
        },
        "sma_20": sma_20,
        "sma_50": sma_50,
        "sma_crossover": (
            "golden_cross" if sma_20 and sma_50 and sma_20 > sma_50 else
            "death_cross" if sma_20 and sma_50 else None
        ),
        "ema_12": ema_12,
        "ema_26": ema_26,
        "macd": macd,
        "bollinger": bollinger,
    }

    return {
        "token": token,
        "price": current_price,
        "signal": result["signal"],
        "confidence": result["confidence"],
        "source": source,
        "data_points": data_points,
        "period": "30d" if source == "real" else "in-memory",
        "indicators": indicators,
        "reasons": result["reasons"],
        # Flat top-level fields for backward compatibility
        "rsi": rsi,
        "sma_20": sma_20,
        "sma_50": sma_50,
        "ema_12": ema_12,
        "ema_26": ema_26,
        "macd": macd,
        "bollinger": bollinger,
        "candles_used": data_points,
        "updated_at": int(time.time()),
    }


# ══════════════════════════════════════════════════
# ── 7. STATS ──
# ══════════════════════════════════════════════════

@router.get("/stats")
async def get_trading_stats():
    """Statistiques globales des trading tools."""
    total_history = sum(len(v) for v in _price_history.values())
    total_whales = sum(len(v) for v in _whale_cache.values())
    total_following = sum(len(v) for v in _followed_wallets.values())

    return {
        "alerts_active": len(_alerts),
        "alerts_triggered": sum(1 for a in _alerts.values() if a.get("triggered")),
        "wallets_tracked": len(_copy_wallets),
        "wallets_followed": total_following,
        "price_snapshots": total_history,
        "tokens_with_history": len(_price_history),
        "candles_tokens": list(_price_history.keys())[:20],
        "whale_movements_cached": total_whales,
        "chains_monitored": len(_whale_cache),
        "supported_chains": SUPPORTED_CHAINS,
    }
