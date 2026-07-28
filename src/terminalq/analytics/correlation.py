"""Cross-asset correlation matrix — equities, bonds, commodities, dollar, and crypto."""

import asyncio

from terminalq.config import CACHE_TTL_CORRELATIONS
from terminalq.logging_config import log

from terminalq import cache
from terminalq._lazy_yfinance import yfinance
from terminalq.providers.crypto_analytics import _daily_returns, _pearson

# Default cross-asset universe: equities, bonds, commodities, dollar, crypto
DEFAULT_UNIVERSE = ["SPY", "QQQ", "IWM", "TLT", "HYG", "GLD", "USO", "DX-Y.NYB", "BTC-USD", "ETH-USD"]

MIN_DATA_POINTS = 30
CORRELATION_WINDOW = 90
DECOUPLED_THRESHOLD = 0.1


async def _fetch_closes(ticker: str) -> list[float]:
    try:
        t = yfinance.Ticker(ticker)
        hist = await asyncio.to_thread(t.history, period="6mo", interval="1d")
        if hist.empty:
            return []
        return [float(c) for c in hist["Close"].dropna().tolist()]
    except Exception as e:
        log.warning("yfinance fetch failed for %s: %s", ticker, e)
        return []


async def get_cross_asset_correlation_matrix(symbols: str = "") -> dict:
    """Get a pairwise Pearson correlation matrix of daily returns across assets.

    Args:
        symbols: Optional comma-separated yfinance tickers. Defaults to a
            cross-asset universe spanning equities, bonds, commodities,
            the dollar, and crypto.

    Returns:
        Dict with the full correlation matrix, notable pairs (highest
        positive/negative correlation, decoupled pairs), and any tickers
        excluded for lack of data.
    """
    tickers = [s.strip().upper() for s in symbols.split(",") if s.strip()] if symbols else DEFAULT_UNIVERSE
    if len(tickers) < 2:
        return {"error": "Provide at least 2 symbols for a correlation matrix", "source": "yahoo_finance (computed)"}

    cache_key = f"correlation_matrix_{','.join(tickers)}"
    cached = cache.get(cache_key)
    if cached:
        log.debug("Cache hit: %s", cache_key)
        return cached

    all_closes = await asyncio.gather(*[_fetch_closes(t) for t in tickers], return_exceptions=True)

    returns: dict[str, list[float]] = {}
    excluded: list[str] = []
    for ticker, closes in zip(tickers, all_closes):
        if isinstance(closes, BaseException) or len(closes) < MIN_DATA_POINTS:
            excluded.append(ticker)
            continue
        returns[ticker] = _daily_returns(closes[-CORRELATION_WINDOW:])

    if len(returns) < 2:
        return {"error": "Insufficient price data for correlation matrix", "source": "yahoo_finance (computed)"}

    included = list(returns.keys())
    matrix: dict[str, dict[str, float]] = {t1: {} for t1 in included}
    for t1 in included:
        for t2 in included:
            matrix[t1][t2] = 1.0 if t1 == t2 else _pearson(returns[t1], returns[t2])

    pairs = []
    for i, t1 in enumerate(included):
        for t2 in included[i + 1 :]:
            pairs.append({"pair": [t1, t2], "correlation": matrix[t1][t2]})

    highest_positive = max(pairs, key=lambda p: p["correlation"]) if pairs else None
    highest_negative = min(pairs, key=lambda p: p["correlation"]) if pairs else None
    decoupled = [p for p in pairs if abs(p["correlation"]) < DECOUPLED_THRESHOLD]

    result = {
        "tickers": included,
        "period": f"{CORRELATION_WINDOW} days",
        "matrix": matrix,
        "notable_pairs": {
            "highest_positive": highest_positive,
            "highest_negative": highest_negative,
            "decoupled": decoupled,
        },
        "excluded": excluded,
        "note": "Pearson correlation of daily returns: 1.0 = perfect sync, -1.0 = perfect inverse, 0 = no relationship.",
        "source": "yahoo_finance (computed)",
    }
    cache.set(cache_key, result, CACHE_TTL_CORRELATIONS)
    return result
