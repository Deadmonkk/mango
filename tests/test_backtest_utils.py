"""Tests for mango.analytics.backtest_utils — shared historical-window return helper."""

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from mango.analytics import backtest_utils


@pytest.fixture(autouse=True)
def clear_caches(tmp_cache_dir):
    """Ensure every test starts with empty cache."""
    pass


def _make_price_df(closes: list[float], start="2015-10-01") -> pd.DataFrame:
    dates = pd.date_range(start=start, periods=len(closes), freq="D")
    return pd.DataFrame({"Close": closes}, index=dates)


async def test_ticker_return_computed_from_first_and_last_close():
    df = _make_price_df([100.0, 110.0, 120.0])
    mock_ticker = MagicMock()
    mock_ticker.history.return_value = df

    with patch("mango.analytics.backtest_utils.yfinance.Ticker", return_value=mock_ticker):
        result = await backtest_utils.ticker_window_return(
            "ZC=F", "2015-10-01", "2016-04-30", cache_prefix="test", cache_ttl=60
        )

    assert result["start_close"] == 100.0
    assert result["end_close"] == 120.0
    assert result["pct_change"] == 20.0


async def test_ticker_return_handles_empty_history():
    mock_ticker = MagicMock()
    mock_ticker.history.return_value = pd.DataFrame()

    with patch("mango.analytics.backtest_utils.yfinance.Ticker", return_value=mock_ticker):
        result = await backtest_utils.ticker_window_return(
            "DELISTED", "2015-10-01", "2016-04-30", cache_prefix="test", cache_ttl=60
        )

    assert "error" in result


async def test_ticker_return_handles_exception():
    mock_ticker = MagicMock()
    mock_ticker.history.side_effect = Exception("boom")

    with patch("mango.analytics.backtest_utils.yfinance.Ticker", return_value=mock_ticker):
        result = await backtest_utils.ticker_window_return(
            "BAD", "2015-10-01", "2016-04-30", cache_prefix="test", cache_ttl=60
        )

    assert "error" in result
