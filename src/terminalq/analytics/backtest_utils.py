"""Shared helper for historical stress-period backtests.

Any provider that answers "how did this ticker actually perform during a
real, dated past stress window" (climate production-risk regions, VIX panic
windows, credit-spread stress, CPI surges, ...) uses this same function
rather than re-implementing the yfinance-history-to-%-change logic per
provider.
"""

from __future__ import annotations

import asyncio

from terminalq.logging_config import log

from terminalq import cache
from terminalq._lazy_yfinance import yfinance


async def ticker_window_return(symbol: str, start: str, end: str, cache_prefix: str, cache_ttl: int) -> dict:
    """Real % price change for one ticker between the first and last close
    available in [start, end]. Never raises — returns an error dict instead,
    since a delisted/not-yet-listed ticker must not abort a batch backtest.

    Args:
        symbol: Ticker or continuous-futures symbol (e.g. "AAPL", "ZC=F").
        start: Window start date, "YYYY-MM-DD".
        end: Window end date, "YYYY-MM-DD".
        cache_prefix: Namespaces the cache key so different callers (climate
            regions, macro-metric backtests, ...) don't collide.
        cache_ttl: Seconds. Historical windows never change, so callers
            should pass a long TTL (weeks/months).
    """
    cache_key = f"{cache_prefix}_{symbol}_{start}_{end}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    try:
        ticker = yfinance.Ticker(symbol)
        df = await asyncio.to_thread(ticker.history, start=start, end=end, interval="1d")
    except Exception as e:  # noqa: BLE001 — provider contract: never raise, report per-ticker
        log.warning("yfinance history failed for %s (%s-%s): %s", symbol, start, end, e)
        return {"symbol": symbol, "error": str(e)}

    if df.empty:
        result = {"symbol": symbol, "error": "No price data for this window (may not have traded yet, or was delisted)"}
    else:
        start_close = float(df["Close"].iloc[0])
        end_close = float(df["Close"].iloc[-1])
        result = {
            "symbol": symbol,
            "start_date": df.index[0].strftime("%Y-%m-%d"),
            "end_date": df.index[-1].strftime("%Y-%m-%d"),
            "start_close": round(start_close, 2),
            "end_close": round(end_close, 2),
            "pct_change": round(100 * (end_close - start_close) / start_close, 1) if start_close else None,
        }
    cache.set(cache_key, result, cache_ttl)
    return result
