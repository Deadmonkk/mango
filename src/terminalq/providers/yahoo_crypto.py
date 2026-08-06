"""Yahoo Finance fallback for crypto data when CoinGecko is unavailable.

CoinGecko is the primary crypto source (richer: dominance, FDV, perp funding,
dev activity) but is rate-limited (30 req/min) and intermittently unreachable
(429s and outright connection failures). Yahoo Finance — already a dependency
via ``yfinance`` — serves spot prices and daily OHLCV for crypto under the
``<SYMBOL>-USD`` ticker convention. That is enough to recover spot quotes and
computed technicals (RSI, SMAs, MACD, golden/death cross) when CoinGecko fails.

It does NOT provide CoinGecko-exclusive fields (dominance %, FDV, perp funding,
community/dev activity, NVT via market-cap series), so those remain ``None`` on
fallback and their dedicated tools continue to degrade gracefully.

Provider contract: functions never raise — they catch and return empty / ``None``
so callers can branch instead of handling exceptions.
"""

import asyncio

from terminalq.mango.logging import log

from terminalq._lazy_yfinance import yfinance

# yfinance ``.history`` only accepts named periods, not "Nd". Map a requested
# day count to the smallest named period that yields at least that many daily
# rows (crypto trades 7 days/week, so rows ≈ calendar days).
_PERIOD_FOR_DAYS = (
    (5, "5d"),
    (30, "1mo"),
    (90, "3mo"),
    (180, "6mo"),
    (365, "1y"),
)


def yahoo_ticker(symbol: str) -> str:
    """Map a bare crypto ticker to Yahoo's ``<SYMBOL>-USD`` convention."""
    s = symbol.upper()
    return s if s.endswith("-USD") else f"{s}-USD"


def _period_for_days(days: int) -> str:
    for threshold, period in _PERIOD_FOR_DAYS:
        if days <= threshold:
            return period
    return "2y"


async def fetch_crypto_ohlcv(symbol: str, days: int = 200) -> tuple[list[float], list[float]]:
    """Daily ``(closes, volumes)`` for a crypto asset from Yahoo, oldest→newest.

    Sliced to the last ``days`` rows for parity with CoinGecko's windowed
    ``market_chart`` call. Returns ``([], [])`` when Yahoo has no data. Never
    raises (yfinance is blocking, so it runs in a worker thread).
    """
    ticker = yahoo_ticker(symbol)
    try:
        t = yfinance.Ticker(ticker)
        hist = await asyncio.to_thread(t.history, period=_period_for_days(days), interval="1d")
        if hist.empty:
            return [], []
        closes = [float(c) for c in hist["Close"].dropna().tolist()]
        volumes = [float(v) for v in hist["Volume"].fillna(0).tolist()]
        return closes[-days:], volumes[-days:]
    except Exception as e:  # provider contract: never raise
        log.warning("Yahoo crypto OHLCV fallback failed for %s: %s", ticker, e)
        return [], []


async def fetch_crypto_closes(symbol: str, days: int = 200) -> list[float]:
    """Closing prices (oldest→newest) for a crypto asset; ``[]`` if unavailable."""
    closes, _ = await fetch_crypto_ohlcv(symbol, days)
    return closes


def _pct_change(closes: list[float], lookback: int) -> float | None:
    """Percent change over ``lookback`` trading days, or ``None`` if short on data."""
    if len(closes) <= lookback or closes[-1 - lookback] == 0:
        return None
    return round((closes[-1] / closes[-1 - lookback] - 1) * 100, 2)


async def fetch_crypto_quote(symbol: str) -> dict | None:
    """Spot quote for a crypto asset from Yahoo, shaped like the CoinGecko quote.

    Returns ``None`` when Yahoo also has no data, so the caller can surface the
    original CoinGecko error. Market cap, supply, ATH and intraday high/low are
    unavailable from Yahoo's daily history and are ``None`` on fallback — the
    ``source`` field documents that this is degraded data.
    """
    # 40 days so the 30-day change has a prior reference point.
    closes, volumes = await fetch_crypto_ohlcv(symbol, days=40)
    if len(closes) < 2:
        return None

    price = closes[-1]
    return {
        "symbol": symbol.upper(),
        "name": symbol.upper(),
        "current_price": round(price, 6),
        "market_cap": None,
        "market_cap_rank": None,
        "total_volume": round(volumes[-1], 2) if volumes else None,
        "high_24h": None,
        "low_24h": None,
        "price_change_24h": round(price - closes[-2], 6),
        "price_change_pct_24h": _pct_change(closes, 1),
        "price_change_pct_7d": _pct_change(closes, 7),
        "price_change_pct_30d": _pct_change(closes, 30),
        "circulating_supply": None,
        "total_supply": None,
        "ath": None,
        "ath_change_pct": None,
        "source": "yahoo_finance (fallback — CoinGecko unavailable)",
    }
