"""Historical OHLCV bars and dividend history from Yahoo Finance.

Clean-room implementation written directly from a written specification (the
return-shape contract below is fixed by existing callers), not from any prior
Yahoo-history client in this codebase family.

Both public functions never raise: any upstream failure — a network error, an
unknown ticker, a malformed frame — degrades to an ``{"error": ...}`` dict,
mirroring the provider convention used throughout this package (see
``mango.core.fred`` for the same pattern against a different upstream).
"""

from __future__ import annotations

import asyncio
import math
import statistics
from datetime import date, timedelta
from typing import Any

import pandas as pd

from mango._lazy_yfinance import yfinance
from mango.ext_settings import CACHE_TTL_HISTORY
from mango.core import cache
from mango.core.logging import get_logger

log = get_logger("historical")

SOURCE = "yahoo_finance"

# Dividend declarations change at most a handful of times a year, far slower
# than price bars, so a much longer TTL is safe and avoids re-hitting Yahoo
# for the same symbol on every report run within a day.
DIVIDENDS_CACHE_TTL_SECONDS = 21600  # 6h

# Approximate days in a year, used only to size the dividend lookback window
# (`years` is a coarse filter, not a precise calendar computation).
_DAYS_PER_YEAR = 365

# --- dividend frequency inference -------------------------------------------
#
# Classified from the *median* gap (in days) between consecutive payment
# dates within the requested window. Median rather than mean so one skipped
# or doubled-up payment (a common real-world occurrence) doesn't drag an
# otherwise-regular schedule into "irregular". Bands are centered on the
# calendar cadence with enough slack to absorb weekend/holiday shifts in the
# actual pay date, chosen wide enough to catch real schedules but narrow
# enough that adjacent bands never overlap.
_MONTHLY_GAP_RANGE_DAYS = (20, 45)
_QUARTERLY_GAP_RANGE_DAYS = (75, 110)
_SEMIANNUAL_GAP_RANGE_DAYS = (150, 210)
_ANNUAL_GAP_RANGE_DAYS = (300, 400)

# Below this many payments there is no gap to measure at all, so periodicity
# cannot be inferred no matter how the bands are drawn.
_MIN_PAYMENTS_FOR_INFERENCE = 2


def _is_missing(value: Any) -> bool:
    """True for None or NaN — the two ways yfinance signals a missing cell."""
    return value is None or (isinstance(value, float) and math.isnan(value))


def _ohlcv_rows(history: pd.DataFrame) -> list[dict[str, Any]]:
    """Convert a yfinance history frame into ascending, NaN-free OHLCV rows.

    A row missing any of open/high/low/close/volume is dropped rather than
    zero-filled — a fabricated 0 price or volume would corrupt every
    downstream average (e.g. a caller computing ``volume * close``). Rows are
    returned in ascending date order regardless of the source frame's order,
    since at least one caller reads ``prices[0]``/``prices[-1]`` as the
    window bounds.
    """
    rows: list[dict[str, Any]] = []
    for timestamp, row in history.iterrows():
        open_, high, low, close, volume = (
            row.get("Open"),
            row.get("High"),
            row.get("Low"),
            row.get("Close"),
            row.get("Volume"),
        )
        if any(_is_missing(v) for v in (open_, high, low, close, volume)):
            continue
        rows.append(
            {
                # Fixed "YYYY-MM-DD" contract even for sub-daily intervals —
                # callers do direct string comparison on this field.
                "date": timestamp.strftime("%Y-%m-%d"),
                "open": float(open_),
                "high": float(high),
                "low": float(low),
                "close": float(close),
                "volume": int(volume),
            }
        )
    rows.sort(key=lambda r: r["date"])
    return rows


def _fetch_history(symbol: str, period: str, interval: str) -> pd.DataFrame:
    """Blocking yfinance call — always dispatch via ``asyncio.to_thread``."""
    return yfinance.Ticker(symbol).history(period=period, interval=interval, auto_adjust=True)


async def get_historical(symbol: str, period: str = "1y", interval: str = "1d") -> dict:
    """Fetch OHLCV bars for `symbol` from Yahoo Finance.

    Returns, on success::

        {
          "symbol": symbol, "period": period, "interval": interval,
          "prices": [{"date": "YYYY-MM-DD", "open": float, "high": float,
                       "low": float, "close": float, "volume": int}, ...],
          "count": len(prices),
          "source": "yahoo_finance",
        }

    `prices` is ascending by date (oldest first) — deliberately the opposite
    convention from `mango.core.fred.get_series`, which returns
    newest-first; the two upstreams' natural callers expect different
    orders, so this is not an inconsistency to "fix".

    Never raises. An unknown ticker typically comes back from yfinance as an
    empty frame rather than an exception; both that case and any raised
    exception become an ``{"error": ...}`` dict.
    """
    cache_key = f"historical_{symbol}_{period}_{interval}"
    cached = cache.get(cache_key)
    if cached is not None:
        log.debug("Cache hit: %s", cache_key)
        return cached

    try:
        history = await asyncio.to_thread(_fetch_history, symbol, period, interval)
    except Exception as exc:  # yfinance raises a grab-bag of exception types
        log.warning("Historical: Yahoo fetch failed for %s: %s", symbol, exc)
        return {
            "error": f"Yahoo Finance fetch failed for {symbol!r}: {exc}",
            "symbol": symbol,
            "source": SOURCE,
        }

    if history is None or history.empty:
        log.warning(
            "Historical: no data returned for %s (period=%s interval=%s)", symbol, period, interval
        )
        return {
            "error": f"No historical data returned for {symbol!r} (period={period}, interval={interval})",
            "symbol": symbol,
            "source": SOURCE,
        }

    prices = _ohlcv_rows(history)
    if not prices:
        return {
            "error": f"All rows for {symbol!r} were incomplete (missing close/volume) and dropped",
            "symbol": symbol,
            "source": SOURCE,
        }

    result = {
        "symbol": symbol,
        "period": period,
        "interval": interval,
        "prices": prices,
        "count": len(prices),
        "source": SOURCE,
    }
    cache.set(cache_key, result, CACHE_TTL_HISTORY)
    return result


def _dividend_rows(dividends: "pd.Series[Any]", cutoff: date) -> list[dict[str, Any]]:
    """Convert a yfinance dividend series into ascending, windowed rows.

    Drops NaN amounts (same rationale as `_ohlcv_rows`) and anything paid
    before `cutoff`.
    """
    rows: list[dict[str, Any]] = []
    for timestamp, amount in dividends.items():
        if _is_missing(amount):
            continue
        paid_on = timestamp.date() if hasattr(timestamp, "date") else timestamp
        if paid_on < cutoff:
            continue
        rows.append({"date": timestamp.strftime("%Y-%m-%d"), "amount": float(amount)})
    rows.sort(key=lambda r: r["date"])
    return rows


def _median_gap_days(dates: list[str]) -> float:
    """Median number of days between consecutive ISO dates, already sorted ascending."""
    parsed = [date.fromisoformat(d) for d in dates]
    gaps = [(later - earlier).days for earlier, later in zip(parsed, parsed[1:])]
    return statistics.median(gaps)


def _classify_frequency(median_gap_days: float) -> str:
    """Map a median payment gap (days) onto a payout-frequency label."""
    if _MONTHLY_GAP_RANGE_DAYS[0] <= median_gap_days <= _MONTHLY_GAP_RANGE_DAYS[1]:
        return "monthly"
    if _QUARTERLY_GAP_RANGE_DAYS[0] <= median_gap_days <= _QUARTERLY_GAP_RANGE_DAYS[1]:
        return "quarterly"
    if _SEMIANNUAL_GAP_RANGE_DAYS[0] <= median_gap_days <= _SEMIANNUAL_GAP_RANGE_DAYS[1]:
        return "semi-annual"
    if _ANNUAL_GAP_RANGE_DAYS[0] <= median_gap_days <= _ANNUAL_GAP_RANGE_DAYS[1]:
        return "annual"
    return "irregular"


def _infer_frequency(dividend_dates: list[str]) -> str:
    """Infer payout frequency from ascending ISO dividend dates.

    Zero payments in the window means no dividend history to speak of, so
    "none" (there is nothing irregular about paying nothing). Exactly one
    payment has no gap to measure — that is "irregular", not "none", because
    a payment did happen; there just isn't enough data yet to say it recurs
    on any particular cadence.
    """
    if not dividend_dates:
        return "none"
    if len(dividend_dates) < _MIN_PAYMENTS_FOR_INFERENCE:
        return "irregular"
    return _classify_frequency(_median_gap_days(dividend_dates))


def _fetch_dividends(symbol: str) -> "pd.Series[Any]":
    """Blocking yfinance call — always dispatch via ``asyncio.to_thread``."""
    return yfinance.Ticker(symbol).dividends


async def get_dividends(symbol: str, years: int = 5) -> dict:
    """Fetch dividend history for `symbol` over the trailing `years` years.

    Returns, on success::

        {
          "symbol": symbol,
          "dividends": [{"date": "YYYY-MM-DD", "amount": float}, ...],  # ascending
          "count": len(dividends), "total_paid": float,
          "frequency": "quarterly"|"monthly"|"semi-annual"|"annual"|"irregular"|"none",
          "source": "yahoo_finance",
        }

    Never raises: a fetch failure becomes an ``{"error": ...}`` dict. A
    symbol with no dividends is a valid, non-error result (`frequency`:
    "none"), since plenty of real tickers simply don't pay one.
    """
    cache_key = f"dividends_{symbol}_{years}"
    cached = cache.get(cache_key)
    if cached is not None:
        log.debug("Cache hit: %s", cache_key)
        return cached

    try:
        raw = await asyncio.to_thread(_fetch_dividends, symbol)
    except Exception as exc:  # yfinance raises a grab-bag of exception types
        log.warning("Dividends: Yahoo fetch failed for %s: %s", symbol, exc)
        return {
            "error": f"Yahoo Finance dividend fetch failed for {symbol!r}: {exc}",
            "symbol": symbol,
            "source": SOURCE,
        }

    cutoff = date.today() - timedelta(days=years * _DAYS_PER_YEAR)
    dividends = _dividend_rows(raw, cutoff) if raw is not None else []

    result = {
        "symbol": symbol,
        "dividends": dividends,
        "count": len(dividends),
        "total_paid": round(sum(d["amount"] for d in dividends), 4),
        "frequency": _infer_frequency([d["date"] for d in dividends]),
        "source": SOURCE,
    }
    cache.set(cache_key, result, DIVIDENDS_CACHE_TTL_SECONDS)
    return result
