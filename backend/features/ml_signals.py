"""MAXIA E36 — ML Trading Signals: RSI, MACD, Bollinger Bands, Momentum, Volume Trend.

Lightweight technical analysis using only stdlib (math, statistics).
Fetches real OHLCV candles from CoinGecko free API (14-day window).
Combines 5 indicators into composite signal: STRONG_BUY / BUY / NEUTRAL / SELL / STRONG_SELL.
"""
import logging, math, time
from statistics import mean, stdev
from typing import Optional
import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

logger = logging.getLogger("maxia.ml_signals")
router = APIRouter(prefix="/api/signals", tags=["signals"])

# ── CoinGecko symbol mapping ──
_SYM_TO_CG: dict[str, str] = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
    "AVAX": "avalanche-2", "MATIC": "matic-network", "ARB": "arbitrum",
    "BNB": "binancecoin", "XRP": "ripple", "TON": "the-open-network",
    "SUI": "sui", "NEAR": "near", "APT": "aptos",
    "SEI": "sei-network", "TRX": "tron",
}
SUPPORTED_TOKENS: list[str] = list(_SYM_TO_CG.keys())

# ── Cache ──
_ohlcv_cache: dict[str, dict] = {}
_OHLCV_CACHE_TTL = 300  # 5 minutes
_signal_history: dict[str, list[dict]] = {}
_MAX_HISTORY = 100


# ── Pydantic models ──
class IndicatorValues(BaseModel):
    rsi_14: float = Field(..., description="RSI 14-period (0-100)")
    macd_line: float = Field(..., description="MACD line")
    macd_signal: float = Field(..., description="MACD signal line")
    macd_histogram: float = Field(..., description="MACD histogram")
    bb_upper: float; bb_middle: float; bb_lower: float
    bb_percent: float = Field(..., description="Price position within bands (0-1)")
    momentum_1h: Optional[float] = None
    momentum_4h: Optional[float] = None
    momentum_24h: Optional[float] = None
    volume_trend: str = Field(..., description="rising / falling / flat")

class SignalResponse(BaseModel):
    token: str; price: float
    signal: str = Field(..., description="STRONG_BUY / BUY / NEUTRAL / SELL / STRONG_SELL")
    confidence: float = Field(..., ge=0.0, le=1.0)
    indicators: IndicatorValues
    candles_used: int; data_source: str = "coingecko"; timestamp: int
    disclaimer: str = "Signals are informational only. Not financial advice."


# ── OHLCV Data Fetching ──
async def _fetch_ohlcv(symbol: str) -> list[dict]:
    """Fetch 14-day OHLCV candles from CoinGecko. Returns list of {o, h, l, c, t}."""
    now = time.time()
    cached = _ohlcv_cache.get(symbol)
    if cached and (now - cached["ts"]) < _OHLCV_CACHE_TTL:
        return cached["candles"]
    cg_id = _SYM_TO_CG.get(symbol)
    if not cg_id:
        return []
    candles: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            resp = await client.get(
                f"https://api.coingecko.com/api/v3/coins/{cg_id}/ohlc",
                params={"vs_currency": "usd", "days": "14"},
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    for row in data:
                        if isinstance(row, list) and len(row) >= 5:
                            candles.append({"o": float(row[1]), "h": float(row[2]),
                                            "l": float(row[3]), "c": float(row[4]),
                                            "t": int(row[0] / 1000)})
            elif resp.status_code == 429:
                logger.warning("[MLSignals] CoinGecko rate-limited for %s", symbol)
            else:
                logger.warning("[MLSignals] CoinGecko %d for %s", resp.status_code, symbol)
    except Exception as e:
        logger.error("[MLSignals] OHLCV fetch error for %s: %s", symbol, e)
    if candles:
        _ohlcv_cache[symbol] = {"candles": candles, "ts": now}
        logger.info("[MLSignals] Fetched %d candles for %s", len(candles), symbol)
    return candles


# ── Technical Indicator Calculations (stdlib only) ──
def _ema(values: list[float], period: int) -> list[float]:
    """Exponential Moving Average. NaN-padded to same length as input."""
    if len(values) < period:
        return [float("nan")] * len(values)
    k = 2.0 / (period + 1)
    result: list[float] = [float("nan")] * (period - 1)
    prev = mean(values[:period])  # seed with SMA
    result.append(prev)
    for v in values[period:]:
        prev = v * k + prev * (1 - k)
        result.append(prev)
    return result


def _compute_rsi(closes: list[float], period: int = 14) -> float:
    """RSI using Wilder smoothing. Returns 0-100."""
    if len(closes) < period + 1:
        return 50.0
    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(c, 0.0) for c in changes]
    losses = [abs(min(c, 0.0)) for c in changes]
    avg_gain = mean(gains[:period])
    avg_loss = mean(losses[:period])
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss < 1e-10:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _compute_macd(closes: list[float], fast: int = 12, slow: int = 26,
                   signal_period: int = 9) -> tuple[float, float, float]:
    """MACD: returns (macd_line, signal_line, histogram)."""
    if len(closes) < slow + signal_period:
        return 0.0, 0.0, 0.0
    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    macd_series = [f - s if not (math.isnan(f) or math.isnan(s)) else float("nan")
                   for f, s in zip(ema_fast, ema_slow)]
    valid_macd = [v for v in macd_series if not math.isnan(v)]
    if len(valid_macd) < signal_period:
        return 0.0, 0.0, 0.0
    signal_series = _ema(valid_macd, signal_period)
    macd_val = valid_macd[-1]
    sig_val = signal_series[-1] if not math.isnan(signal_series[-1]) else 0.0
    return macd_val, sig_val, macd_val - sig_val


def _compute_bollinger(closes: list[float], period: int = 20,
                        num_std: float = 2.0) -> tuple[float, float, float, float]:
    """Bollinger Bands. Returns (upper, middle, lower, percent_b)."""
    if len(closes) < period:
        p = closes[-1] if closes else 0.0
        return p * 1.02, p, p * 0.98, 0.5
    window = closes[-period:]
    middle = mean(window)
    sd = stdev(window) if len(window) > 1 else 0.0
    upper, lower = middle + num_std * sd, middle - num_std * sd
    bw = upper - lower
    pct_b = (closes[-1] - lower) / bw if bw > 1e-10 else 0.5
    return upper, middle, lower, pct_b


def _compute_volume_trend(candles: list[dict], lookback: int = 10) -> str:
    """Classify volatility trend (high-low range proxy since CoinGecko has no volume)."""
    if len(candles) < lookback * 2:
        return "flat"
    recent = [c["h"] - c["l"] for c in candles[-lookback:]]
    prior = [c["h"] - c["l"] for c in candles[-lookback * 2 : -lookback]]
    avg_prior = mean(prior) if prior else 0.0
    if avg_prior < 1e-10:
        return "flat"
    ratio = mean(recent) / avg_prior
    if ratio > 1.15:
        return "rising"
    return "falling" if ratio < 0.85 else "flat"


def _compute_momentum(candles: list[dict]) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """Rate of change over ~1h, ~4h, ~24h. CoinGecko 14d gives ~4h candles."""
    if not candles or len(candles) < 2:
        return None, None, None
    price_now = candles[-1]["c"]
    if price_now <= 0:
        return None, None, None
    def _roc(n: int) -> Optional[float]:
        idx = len(candles) - 1 - n
        if idx < 0:
            return None
        past = candles[idx]["c"]
        return round(((price_now - past) / past) * 100, 4) if past > 0 else None
    # 1 candle ~ 4h: mom_1h=1 candle, mom_4h=1 candle, mom_24h=6 candles
    return _roc(1), _roc(1), _roc(6)


# ── Composite Signal Generation ──
def _generate_composite_signal(rsi: float, macd_line: float, macd_signal: float,
                                macd_histogram: float, bb_percent: float,
                                volume_trend: str, momentum_24h: Optional[float]) -> tuple[str, float]:
    """Combine indicators into weighted composite signal. Returns (label, confidence)."""
    score = 0.0
    weights = 0.0

    # RSI (weight 1.5) — oversold bullish, overbought bearish
    w = 1.5; weights += w
    if rsi < 25:       score += w
    elif rsi < 35:     score += w * 0.5
    elif rsi > 75:     score -= w
    elif rsi > 65:     score -= w * 0.5

    # MACD (weight 1.5) — crossover direction
    w = 1.5; weights += w
    if macd_histogram > 0 and macd_line > macd_signal:   score += w
    elif macd_histogram > 0:                              score += w * 0.4
    elif macd_histogram < 0 and macd_line < macd_signal:  score -= w
    elif macd_histogram < 0:                              score -= w * 0.4

    # Bollinger %B (weight 1.0)
    w = 1.0; weights += w
    if bb_percent < 0.1:   score += w
    elif bb_percent < 0.3: score += w * 0.4
    elif bb_percent > 0.9: score -= w
    elif bb_percent > 0.7: score -= w * 0.4

    # Volume/volatility trend (weight 0.5) — amplifies current direction
    w = 0.5; weights += w
    if volume_trend == "rising":
        score += w * 0.5 if score > 0 else (-w * 0.5 if score < 0 else 0)

    # Momentum 24h (weight 0.5)
    if momentum_24h is not None:
        w = 0.5; weights += w
        if momentum_24h > 5.0:     score += w
        elif momentum_24h > 2.0:   score += w * 0.4
        elif momentum_24h < -5.0:  score -= w
        elif momentum_24h < -2.0:  score -= w * 0.4

    if weights < 1e-10:
        return "NEUTRAL", 0.0
    norm = score / weights  # -1.0 to +1.0
    if norm >= 0.6:     label = "STRONG_BUY"
    elif norm >= 0.2:   label = "BUY"
    elif norm <= -0.6:  label = "STRONG_SELL"
    elif norm <= -0.2:  label = "SELL"
    else:               label = "NEUTRAL"
    return label, round(min(abs(norm), 1.0), 3)


# ── Signal Computation Pipeline ──
async def compute_signal(symbol: str) -> dict:
    """Full pipeline: fetch OHLCV, compute indicators, generate composite signal."""
    symbol = symbol.upper()
    if symbol not in _SYM_TO_CG:
        raise HTTPException(400, f"Token '{symbol}' not supported. Available: {', '.join(SUPPORTED_TOKENS)}")
    candles = await _fetch_ohlcv(symbol)
    if not candles or len(candles) < 20:
        raise HTTPException(503, f"Insufficient OHLCV data for {symbol} ({len(candles) if candles else 0} candles, need 20+). Try again in a few minutes.")
    closes = [c["c"] for c in candles]
    rsi = _compute_rsi(closes, period=14)
    macd_line, macd_sig, macd_hist = _compute_macd(closes)
    bb_up, bb_mid, bb_low, bb_pct = _compute_bollinger(closes)
    vol_trend = _compute_volume_trend(candles)
    mom_1h, mom_4h, mom_24h = _compute_momentum(candles)
    signal_label, confidence = _generate_composite_signal(
        rsi, macd_line, macd_sig, macd_hist, bb_pct, vol_trend, mom_24h)

    now_ts = int(time.time())
    result = SignalResponse(
        token=symbol, price=round(closes[-1], 4), signal=signal_label,
        confidence=confidence, candles_used=len(candles), timestamp=now_ts,
        indicators=IndicatorValues(
            rsi_14=round(rsi, 2), macd_line=round(macd_line, 6),
            macd_signal=round(macd_sig, 6), macd_histogram=round(macd_hist, 6),
            bb_upper=round(bb_up, 4), bb_middle=round(bb_mid, 4),
            bb_lower=round(bb_low, 4), bb_percent=round(bb_pct, 4),
            momentum_1h=round(mom_1h, 4) if mom_1h is not None else None,
            momentum_4h=round(mom_4h, 4) if mom_4h is not None else None,
            momentum_24h=round(mom_24h, 4) if mom_24h is not None else None,
            volume_trend=vol_trend))

    # Store in history
    entry = {"token": symbol, "price": closes[-1], "signal": signal_label,
             "confidence": confidence, "rsi": round(rsi, 2),
             "macd_h": round(macd_hist, 6), "bb_pct": round(bb_pct, 4), "timestamp": now_ts}
    hist = _signal_history.setdefault(symbol, [])
    hist.append(entry)
    if len(hist) > _MAX_HISTORY:
        _signal_history[symbol] = hist[-_MAX_HISTORY:]
    return result.model_dump()


# ── API Endpoints ──
@router.get("/latest")
async def signal_latest(token: str = Query("BTC", description="Token symbol (e.g. BTC, ETH, SOL)")):
    """Latest ML-based trading signal for a token. Combines RSI, MACD, Bollinger, volume, momentum."""
    return await compute_signal(token.upper())


@router.get("/scan")
async def signal_scan():
    """Scan all supported tokens and return active signals sorted by confidence."""
    results: list[dict] = []
    errors: list[str] = []
    for symbol in SUPPORTED_TOKENS:
        try:
            results.append(await compute_signal(symbol))
        except HTTPException as e:
            errors.append(f"{symbol}: {e.detail}")
            logger.warning("[MLSignals] Scan skip %s: %s", symbol, e.detail)
        except Exception as e:
            errors.append(f"{symbol}: {e}")
            logger.error("[MLSignals] Scan error %s: %s", symbol, e)
    results.sort(key=lambda r: r.get("confidence", 0), reverse=True)
    buy = sum(1 for r in results if r["signal"] in ("BUY", "STRONG_BUY"))
    sell = sum(1 for r in results if r["signal"] in ("SELL", "STRONG_SELL"))
    return {"signals": results, "summary": {"total": len(results), "buy": buy,
            "sell": sell, "neutral": len(results) - buy - sell, "errors": len(errors)},
            "errors": errors or None, "supported_tokens": SUPPORTED_TOKENS,
            "timestamp": int(time.time())}


@router.get("/history")
async def signal_history(token: str = Query("BTC", description="Token symbol"),
                          limit: int = Query(20, ge=1, le=100)):
    """Recent signal history for a token (in-memory, built from /latest and /scan calls)."""
    symbol = token.upper()
    if symbol not in _SYM_TO_CG:
        raise HTTPException(400, f"Token '{symbol}' not supported. Available: {', '.join(SUPPORTED_TOKENS)}")
    history = _signal_history.get(symbol, [])
    recent = history[-limit:] if history else []
    return {"token": symbol, "history": list(reversed(recent)), "count": len(recent),
            "supported_tokens": SUPPORTED_TOKENS}


@router.get("/supported")
async def signal_supported():
    """List all supported tokens and indicator details."""
    return {"tokens": SUPPORTED_TOKENS, "count": len(SUPPORTED_TOKENS),
            "indicators": ["RSI_14", "MACD_12_26_9", "Bollinger_20_2", "Volume_Trend", "Momentum"],
            "signals": ["STRONG_BUY", "BUY", "NEUTRAL", "SELL", "STRONG_SELL"],
            "data_source": "CoinGecko OHLC (14-day, 4h candles)",
            "cache_ttl_seconds": _OHLCV_CACHE_TTL}
