"""Technical-indicator computation over daily OHLCV closes.

Clean-room implementation written directly from a written specification (the
return-shape contract below is fixed by an existing saved payload this
package's caller relies on), not from any prior technicals provider in this
codebase family.

All indicators are derived from ``mango.core.historical.get_historical``,
which returns closes oldest-first — every windowed computation below walks
the series in that order. Never raises: any upstream failure or malformed
input degrades to an ``{"error": ...}`` dict, mirroring the convention used
throughout ``mango.core`` (see ``fred.py``/``historical.py`` for the same
pattern against different upstreams).
"""

from __future__ import annotations

import statistics
from typing import Any

from mango.core import cache
from mango.core.historical import get_historical
from mango.core.logging import get_logger

log = get_logger("technical")

SOURCE = "computed from yahoo_finance data"

# --- indicator periods (named, not magic numbers) --------------------------

SMA_WINDOWS = (20, 50, 200)
EMA_WINDOWS = (12, 26, 50)

RSI_PERIOD = 14
RSI_OVERBOUGHT = 70.0
RSI_OVERSOLD = 30.0

MACD_FAST_PERIOD = 12
MACD_SLOW_PERIOD = 26
MACD_SIGNAL_PERIOD = 9

BOLLINGER_PERIOD = 20
BOLLINGER_STDDEV_MULTIPLIER = 2

ATR_PERIOD = 14

# Rounding precision, chosen to mirror the real saved payload this shape is
# checked against (technicals_SPY in a saved FR audit trail).
PRICE_DECIMALS = 2
RSI_DECIMALS = 2
MACD_DECIMALS = 4
BOLLINGER_PRICE_DECIMALS = 2
BOLLINGER_BANDWIDTH_DECIMALS = 2
BOLLINGER_PERCENT_B_DECIMALS = 4
ATR_DECIMALS = 4

# Historical fetch window — one year of daily bars comfortably covers the
# longest window used below (SMA-200) with room to spare.
HISTORY_PERIOD = "1y"
HISTORY_INTERVAL = "1d"

# Indicators move once per daily close; a 15-minute TTL avoids recomputing
# the whole suite on every call within a report run without serving a stale
# reading across the trading day.
TECHNICALS_CACHE_TTL_SECONDS = 900


def _round(value: float | None, digits: int) -> float | None:
    return round(value, digits) if value is not None else None


# --- SMA ---------------------------------------------------------------


def _sma(closes: list[float], window: int) -> float | None:
    """Simple moving average of the trailing `window` closes.

    None when there isn't enough history for the window — a partial-window
    average would silently understate what a real N-period SMA means.
    """
    if len(closes) < window:
        return None
    return sum(closes[-window:]) / window


def _compute_sma_block(closes: list[float], price: float) -> dict[str, Any]:
    smas = {window: _sma(closes, window) for window in SMA_WINDOWS}
    sma_20, sma_50, sma_200 = smas[20], smas[50], smas[200]

    signals = {
        "above_sma_20": (price > sma_20) if sma_20 is not None else None,
        "above_sma_50": (price > sma_50) if sma_50 is not None else None,
        "above_sma_200": (price > sma_200) if sma_200 is not None else None,
        # A "golden cross" configuration (medium-term average above the
        # long-term average) rather than a true crossover event — the
        # series available here has no memory of the prior day's ordering.
        "golden_cross": (sma_50 > sma_200) if (sma_50 is not None and sma_200 is not None) else None,
    }

    return {
        "current_price": _round(price, PRICE_DECIMALS),
        "sma_20": _round(sma_20, BOLLINGER_PRICE_DECIMALS),
        "sma_50": _round(sma_50, BOLLINGER_PRICE_DECIMALS),
        "sma_200": _round(sma_200, BOLLINGER_PRICE_DECIMALS),
        "signals": signals,
    }


# --- EMA -----------------------------------------------------------------


def _ema_series(closes: list[float], window: int) -> list[float] | None:
    """Full exponential-moving-average series, seeded by a simple average.

    None when there isn't enough history to seed the first EMA value.
    Standard seeding: the first EMA value is the plain SMA of the first
    `window` closes; every value after that uses the recursive EMA formula.
    """
    if len(closes) < window:
        return None

    multiplier = 2.0 / (window + 1)
    series: list[float] = [sum(closes[:window]) / window]
    for close in closes[window:]:
        series.append((close - series[-1]) * multiplier + series[-1])
    return series


def _ema_last(closes: list[float], window: int) -> float | None:
    series = _ema_series(closes, window)
    return series[-1] if series else None


def _compute_ema_block(closes: list[float]) -> dict[str, Any]:
    return {
        f"ema_{window}": _round(_ema_last(closes, window), BOLLINGER_PRICE_DECIMALS)
        for window in EMA_WINDOWS
    }


# --- RSI (Wilder's smoothing) ---------------------------------------------


def _rsi(closes: list[float], period: int) -> float | None:
    """Wilder-smoothed Relative Strength Index.

    Needs at least `period` + 1 closes (period price changes). None
    otherwise — a short-window RSI is not a real 14-period RSI.
    """
    if len(closes) < period + 1:
        return None

    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(c, 0.0) for c in changes]
    losses = [max(-c, 0.0) for c in changes]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _rsi_signal(rsi: float | None) -> str | None:
    if rsi is None:
        return None
    if rsi >= RSI_OVERBOUGHT:
        return "overbought"
    if rsi <= RSI_OVERSOLD:
        return "oversold"
    return "neutral"


def _compute_rsi_block(closes: list[float]) -> dict[str, Any]:
    rsi = _rsi(closes, RSI_PERIOD)
    return {
        "rsi": _round(rsi, RSI_DECIMALS),
        "period": RSI_PERIOD,
        "signal": _rsi_signal(rsi),
    }


# --- MACD ------------------------------------------------------------------


def _macd(closes: list[float]) -> tuple[float, float, float] | None:
    """(macd_line, signal_line, histogram) from the standard 12/26/9 recipe.

    None when there isn't enough history to seed both EMAs plus the 9-period
    signal EMA over the resulting MACD series.
    """
    fast_series = _ema_series(closes, MACD_FAST_PERIOD)
    slow_series = _ema_series(closes, MACD_SLOW_PERIOD)
    if fast_series is None or slow_series is None:
        return None

    # The two EMA series start at different offsets into `closes` (the
    # fast EMA seeds sooner); align them on the shared tail before
    # differencing.
    offset = len(fast_series) - len(slow_series)
    macd_series = [fast_series[offset + i] - slow_series[i] for i in range(len(slow_series))]

    signal_series = _ema_series(macd_series, MACD_SIGNAL_PERIOD)
    if signal_series is None:
        return None

    macd_line = macd_series[-1]
    signal_line = signal_series[-1]
    return macd_line, signal_line, macd_line - signal_line


def _macd_signal(histogram: float | None) -> str | None:
    if histogram is None:
        return None
    if histogram > 0:
        return "bullish"
    if histogram < 0:
        return "bearish"
    return "neutral"


def _compute_macd_block(closes: list[float]) -> dict[str, Any]:
    result = _macd(closes)
    macd_line, signal_line, histogram = result if result is not None else (None, None, None)
    return {
        "macd_line": _round(macd_line, MACD_DECIMALS),
        "signal_line": _round(signal_line, MACD_DECIMALS),
        "histogram": _round(histogram, MACD_DECIMALS),
        "signal": _macd_signal(histogram),
        "parameters": {
            "fast": MACD_FAST_PERIOD,
            "slow": MACD_SLOW_PERIOD,
            "signal": MACD_SIGNAL_PERIOD,
        },
    }


# --- Bollinger Bands ---------------------------------------------------


def _bollinger(closes: list[float], price: float) -> dict[str, float | None]:
    """20-period SMA +/- 2 population standard deviations.

    `bandwidth` here is the absolute point spread between the bands
    (upper - lower), matching this provider's real saved output — not the
    `(upper - lower) / middle` ratio some texts call "bandwidth". None
    fields throughout when there isn't a full 20-bar window yet.
    """
    if len(closes) < BOLLINGER_PERIOD:
        return {
            "current_price": _round(price, PRICE_DECIMALS),
            "upper_band": None,
            "middle_band": None,
            "lower_band": None,
            "bandwidth": None,
            "percent_b": None,
            "signal": None,
        }

    window = closes[-BOLLINGER_PERIOD:]
    middle = sum(window) / BOLLINGER_PERIOD
    stddev = statistics.pstdev(window)
    upper = middle + BOLLINGER_STDDEV_MULTIPLIER * stddev
    lower = middle - BOLLINGER_STDDEV_MULTIPLIER * stddev
    band_spread = upper - lower

    percent_b = (price - lower) / band_spread if band_spread else None

    if percent_b is None:
        signal = None
    elif percent_b > 1:
        signal = "overbought"
    elif percent_b < 0:
        signal = "oversold"
    else:
        signal = "neutral"

    return {
        "current_price": _round(price, PRICE_DECIMALS),
        "upper_band": _round(upper, BOLLINGER_PRICE_DECIMALS),
        "middle_band": _round(middle, BOLLINGER_PRICE_DECIMALS),
        "lower_band": _round(lower, BOLLINGER_PRICE_DECIMALS),
        "bandwidth": _round(band_spread, BOLLINGER_BANDWIDTH_DECIMALS),
        "percent_b": _round(percent_b, BOLLINGER_PERCENT_B_DECIMALS),
        "signal": signal,
    }


# --- ATR (Wilder's smoothing) -----------------------------------------


def _true_ranges(highs: list[float], lows: list[float], closes: list[float]) -> list[float]:
    ranges = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        ranges.append(
            max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
        )
    return ranges


def _atr(highs: list[float], lows: list[float], closes: list[float], period: int) -> float | None:
    """Wilder-smoothed Average True Range. None with fewer than `period` bars."""
    if len(closes) < period:
        return None

    true_ranges = _true_ranges(highs, lows, closes)
    atr = sum(true_ranges[:period]) / period
    for tr in true_ranges[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


def _compute_atr_block(highs: list[float], lows: list[float], closes: list[float]) -> dict[str, Any]:
    atr = _atr(highs, lows, closes, ATR_PERIOD)
    return {"atr": _round(atr, ATR_DECIMALS), "period": ATR_PERIOD}


# --- overall signal ---------------------------------------------------


def _overall_signal(sma_block: dict, macd_block: dict, rsi_block: dict, bollinger_block: dict) -> str:
    """Simple majority vote across the indicators that have an opinion.

    Each indicator contributes at most one vote (bullish/bearish/neutral),
    and only indicators with enough history to have computed a value vote at
    all. A tie, or no indicator having enough history, reads as "neutral" —
    the honest answer when the evidence doesn't lean either way.
    """
    bullish_votes = 0
    bearish_votes = 0

    signals = sma_block.get("signals", {})
    trend_flags = [signals.get("above_sma_20"), signals.get("above_sma_50"), signals.get("above_sma_200")]
    for flag in trend_flags:
        if flag is True:
            bullish_votes += 1
        elif flag is False:
            bearish_votes += 1

    if signals.get("golden_cross") is True:
        bullish_votes += 1
    elif signals.get("golden_cross") is False:
        bearish_votes += 1

    if macd_block.get("signal") == "bullish":
        bullish_votes += 1
    elif macd_block.get("signal") == "bearish":
        bearish_votes += 1

    if rsi_block.get("signal") == "overbought":
        bearish_votes += 1
    elif rsi_block.get("signal") == "oversold":
        bullish_votes += 1

    if bollinger_block.get("signal") == "overbought":
        bearish_votes += 1
    elif bollinger_block.get("signal") == "oversold":
        bullish_votes += 1

    if bullish_votes > bearish_votes:
        return "bullish"
    if bearish_votes > bullish_votes:
        return "bearish"
    return "neutral"


# --- public entry point --------------------------------------------------


async def get_full_technicals(symbol: str) -> dict:
    """Compute the full technical-indicator suite for `symbol`.

    Returns, on success::

        {
          "symbol": symbol, "price": float, "source": SOURCE,
          "overall_signal": "bullish"|"bearish"|"neutral",
          "sma": {...}, "ema": {...}, "rsi": {...}, "macd": {...},
          "bollinger": {...}, "atr": {...},
        }

    Any indicator whose window exceeds the available history comes back as
    None within its own block rather than a number computed from too few
    bars. Never raises: an upstream fetch failure or empty history becomes
    ``{"error": ..., "symbol": symbol, "source": SOURCE}``.
    """
    cache_key = f"technicals_{symbol}_{HISTORY_PERIOD}_{HISTORY_INTERVAL}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    history = await get_historical(symbol, period=HISTORY_PERIOD, interval=HISTORY_INTERVAL)
    if "error" in history:
        log.warning("Technicals: historical fetch failed for %s: %s", symbol, history["error"])
        return {"error": history["error"], "symbol": symbol, "source": SOURCE}

    prices = history.get("prices", [])
    if not prices:
        return {"error": f"no price history for {symbol!r}", "symbol": symbol, "source": SOURCE}

    closes = [row["close"] for row in prices]
    highs = [row["high"] for row in prices]
    lows = [row["low"] for row in prices]
    price = closes[-1]

    sma_block = _compute_sma_block(closes, price)
    ema_block = _compute_ema_block(closes)
    rsi_block = _compute_rsi_block(closes)
    macd_block = _compute_macd_block(closes)
    bollinger_block = _bollinger(closes, price)
    atr_block = _compute_atr_block(highs, lows, closes)

    result = {
        "symbol": symbol,
        "price": _round(price, PRICE_DECIMALS),
        "source": SOURCE,
        "overall_signal": _overall_signal(sma_block, macd_block, rsi_block, bollinger_block),
        "sma": sma_block,
        "ema": ema_block,
        "rsi": rsi_block,
        "macd": macd_block,
        "bollinger": bollinger_block,
        "atr": atr_block,
    }
    cache.set(cache_key, result, TECHNICALS_CACHE_TTL_SECONDS)
    return result
