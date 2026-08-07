"""Tests for mango.core.historical — Yahoo Finance OHLCV bars and dividends.

All network access is faked by patching the lazy yfinance proxy's ``Ticker``
attribute (`mango._lazy_yfinance.yfinance`) as imported into the module
under test; no test may reach the real yfinance/Yahoo Finance.
"""

import math
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from mango.core import historical


@pytest.fixture(autouse=True)
def clear_caches(tmp_cache_dir):
    """Ensure every test starts with an empty cache."""
    pass


def _history_frame(rows: list[dict]) -> pd.DataFrame:
    """Build a fake yfinance `.history()` frame from a list of row dicts.

    Each row dict needs a "date" (ISO string) plus any of
    Open/High/Low/Close/Volume; omitted OHLCV fields default to clean
    numbers so a test only needs to spell out what it cares about.
    """
    index = pd.to_datetime([r["date"] for r in rows])
    frame = pd.DataFrame(
        {
            "Open": [r.get("Open", 100.0) for r in rows],
            "High": [r.get("High", 101.0) for r in rows],
            "Low": [r.get("Low", 99.0) for r in rows],
            "Close": [r.get("Close", 100.5) for r in rows],
            "Volume": [r.get("Volume", 1_000_000) for r in rows],
        },
        index=index,
    )
    return frame


def _patched_ticker(history_frame=None, dividends_series=None, raise_on="history"):
    """Build a MagicMock standing in for `yfinance.Ticker(symbol)`.

    `raise_on` controls which attribute access raises when a test wants to
    simulate an upstream exception; anything not requested returns a mock.
    """
    ticker = MagicMock()
    if history_frame is not None:
        ticker.history.return_value = history_frame
    if dividends_series is not None:
        ticker.dividends = dividends_series
    return ticker


# --- get_historical: ordering, dates, NaN/missing handling -----------------


async def test_get_historical_returns_ascending_even_when_source_is_descending():
    rows = [
        {"date": "2026-01-03", "Close": 102.0},
        {"date": "2026-01-02", "Close": 101.0},
        {"date": "2026-01-01", "Close": 100.0},
    ]
    frame = _history_frame(rows)
    with patch.object(historical.yfinance, "Ticker", return_value=_patched_ticker(frame)):
        result = await historical.get_historical("AAPL")

    dates = [p["date"] for p in result["prices"]]
    assert dates == sorted(dates)
    assert dates[0] == "2026-01-01"
    assert dates[-1] == "2026-01-03"


async def test_get_historical_date_is_iso_string():
    frame = _history_frame([{"date": "2026-03-15"}])
    with patch.object(historical.yfinance, "Ticker", return_value=_patched_ticker(frame)):
        result = await historical.get_historical("AAPL")

    assert result["prices"][0]["date"] == "2026-03-15"
    assert isinstance(result["prices"][0]["date"], str)


async def test_get_historical_drops_nan_rows_instead_of_zero_filling():
    rows = [
        {"date": "2026-01-01", "Close": 100.0, "Volume": 1_000_000},
        {"date": "2026-01-02", "Close": math.nan, "Volume": 1_000_000},
        {"date": "2026-01-03", "Close": 102.0, "Volume": 1_000_000},
    ]
    frame = _history_frame(rows)
    with patch.object(historical.yfinance, "Ticker", return_value=_patched_ticker(frame)):
        result = await historical.get_historical("AAPL")

    dates = [p["date"] for p in result["prices"]]
    assert "2026-01-02" not in dates
    assert result["count"] == 2
    # No zero-filled row snuck in anywhere.
    assert all(p["close"] != 0 for p in result["prices"])


async def test_get_historical_drops_rows_missing_close_or_volume():
    rows = [
        {"date": "2026-01-01", "Close": 100.0, "Volume": 1_000_000},
        {"date": "2026-01-02", "Close": None, "Volume": 1_000_000},
        {"date": "2026-01-03", "Close": 102.0, "Volume": None},
        {"date": "2026-01-04", "Close": 103.0, "Volume": 1_000_000},
    ]
    frame = _history_frame(rows)
    with patch.object(historical.yfinance, "Ticker", return_value=_patched_ticker(frame)):
        result = await historical.get_historical("AAPL")

    dates = [p["date"] for p in result["prices"]]
    assert dates == ["2026-01-01", "2026-01-04"]
    assert result["count"] == len(result["prices"]) == 2


async def test_get_historical_volume_is_int_and_close_is_float():
    frame = _history_frame([{"date": "2026-01-01", "Close": 100.25, "Volume": 2_500_000}])
    with patch.object(historical.yfinance, "Ticker", return_value=_patched_ticker(frame)):
        result = await historical.get_historical("AAPL")

    row = result["prices"][0]
    assert isinstance(row["volume"], int)
    assert isinstance(row["close"], float)
    assert row["close"] == 100.25
    assert row["volume"] == 2_500_000


async def test_get_historical_empty_frame_returns_error_dict_not_raise():
    empty = pd.DataFrame()
    with patch.object(historical.yfinance, "Ticker", return_value=_patched_ticker(empty)):
        result = await historical.get_historical("NOSUCHTICKER")

    assert "error" in result
    assert result["symbol"] == "NOSUCHTICKER"
    assert result["source"] == "yahoo_finance"


async def test_get_historical_exception_returns_error_dict_not_raise():
    ticker = MagicMock()
    ticker.history.side_effect = RuntimeError("upstream blew up")
    with patch.object(historical.yfinance, "Ticker", return_value=ticker):
        result = await historical.get_historical("AAPL")

    assert "error" in result
    assert "upstream blew up" in result["error"]
    assert result["symbol"] == "AAPL"
    assert result["source"] == "yahoo_finance"


async def test_get_historical_count_matches_prices_length():
    rows = [{"date": f"2026-01-{d:02d}"} for d in range(1, 6)]
    frame = _history_frame(rows)
    with patch.object(historical.yfinance, "Ticker", return_value=_patched_ticker(frame)):
        result = await historical.get_historical("AAPL")

    assert result["count"] == len(result["prices"]) == 5


async def test_get_historical_echoes_symbol_period_interval():
    frame = _history_frame([{"date": "2026-01-01"}])
    with patch.object(historical.yfinance, "Ticker", return_value=_patched_ticker(frame)):
        result = await historical.get_historical("MSFT", period="6mo", interval="1wk")

    assert result["symbol"] == "MSFT"
    assert result["period"] == "6mo"
    assert result["interval"] == "1wk"


async def test_get_historical_dispatches_blocking_call_off_event_loop():
    """The blocking yfinance call must go through asyncio.to_thread, not the event loop."""
    frame = _history_frame([{"date": "2026-01-01"}])
    with patch.object(historical.yfinance, "Ticker", return_value=_patched_ticker(frame)):
        with patch.object(
            historical.asyncio, "to_thread", wraps=historical.asyncio.to_thread
        ) as spy:
            await historical.get_historical("AAPL")

    assert spy.called
    # First positional arg to asyncio.to_thread is the blocking function.
    assert spy.call_args[0][0] is historical._fetch_history


# --- get_dividends: ordering, totals, frequency inference -------------------


def _dividend_series(pairs: list[tuple[str, float]]) -> pd.Series:
    index = pd.to_datetime([d for d, _ in pairs])
    return pd.Series([amount for _, amount in pairs], index=index)


async def test_get_dividends_ascending_by_date():
    series = _dividend_series([("2026-06-01", 0.5), ("2026-01-01", 0.5), ("2026-03-01", 0.5)])
    with patch.object(historical.yfinance, "Ticker", return_value=_patched_ticker(dividends_series=series)):
        result = await historical.get_dividends("KO")

    dates = [d["date"] for d in result["dividends"]]
    assert dates == sorted(dates)


async def test_get_dividends_quarterly_frequency_inferred():
    today = date.today()
    pairs = [
        ((today - timedelta(days=273)).isoformat(), 0.5),
        ((today - timedelta(days=182)).isoformat(), 0.5),
        ((today - timedelta(days=91)).isoformat(), 0.5),
        (today.isoformat(), 0.5),
    ]
    series = _dividend_series(pairs)
    with patch.object(historical.yfinance, "Ticker", return_value=_patched_ticker(dividends_series=series)):
        result = await historical.get_dividends("KO")

    assert result["frequency"] == "quarterly"
    assert result["count"] == 4
    assert result["total_paid"] == pytest.approx(2.0)


async def test_get_dividends_monthly_frequency_inferred():
    today = date.today()
    pairs = [((today - timedelta(days=30 * n)).isoformat(), 0.1) for n in range(4, -1, -1)]
    series = _dividend_series(pairs)
    with patch.object(historical.yfinance, "Ticker", return_value=_patched_ticker(dividends_series=series)):
        result = await historical.get_dividends("O")

    assert result["frequency"] == "monthly"


async def test_get_dividends_irregular_spacing_flagged():
    # Gaps of 60, 60, 400 days -> median gap 60 days, which lands in the gap
    # between the monthly band (<=45) and the quarterly band (>=75) — no
    # band claims it, so it must be classified "irregular".
    today = date.today()
    d0 = today - timedelta(days=520)
    d1 = d0 + timedelta(days=60)
    d2 = d1 + timedelta(days=60)
    d3 = d2 + timedelta(days=400)
    assert d3 == today
    pairs = [
        (d0.isoformat(), 1.0),
        (d1.isoformat(), 0.25),
        (d2.isoformat(), 0.25),
        (d3.isoformat(), 5.0),
    ]
    series = _dividend_series(pairs)
    with patch.object(historical.yfinance, "Ticker", return_value=_patched_ticker(dividends_series=series)):
        result = await historical.get_dividends("SPECIAL")

    assert result["frequency"] == "irregular"


async def test_get_dividends_single_payment_cannot_infer_frequency():
    series = _dividend_series([(date.today().isoformat(), 1.0)])
    with patch.object(historical.yfinance, "Ticker", return_value=_patched_ticker(dividends_series=series)):
        result = await historical.get_dividends("NEWPAYER")

    assert result["count"] == 1
    assert result["frequency"] == "irregular"


async def test_get_dividends_no_payments_returns_none_frequency():
    series = pd.Series([], dtype=float)
    with patch.object(historical.yfinance, "Ticker", return_value=_patched_ticker(dividends_series=series)):
        result = await historical.get_dividends("NEVERPAID")

    assert result["count"] == 0
    assert result["frequency"] == "none"
    assert result["total_paid"] == 0.0
    assert "error" not in result


class _RaisingDividendsTicker:
    """Stand-in for a yfinance.Ticker whose `.dividends` property blows up."""

    @property
    def dividends(self):
        raise RuntimeError("boom")


async def test_get_dividends_exception_returns_error_dict_not_raise():
    with patch.object(historical.yfinance, "Ticker", return_value=_RaisingDividendsTicker()):
        result = await historical.get_dividends("AAPL")

    assert "error" in result
    assert result["source"] == "yahoo_finance"


async def test_get_dividends_years_window_excludes_old_payments():
    today = date.today()
    pairs = [
        ((today - timedelta(days=365 * 8)).isoformat(), 1.0),  # outside a 5y window
        ((today - timedelta(days=30)).isoformat(), 0.5),
    ]
    series = _dividend_series(pairs)
    with patch.object(historical.yfinance, "Ticker", return_value=_patched_ticker(dividends_series=series)):
        result = await historical.get_dividends("OLD", years=5)

    assert result["count"] == 1
    assert result["total_paid"] == pytest.approx(0.5)
