"""Tests for terminalq.providers.stress_backtest — generalized metric stress backtest."""

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from terminalq.providers import stress_backtest


@pytest.fixture(autouse=True)
def clear_caches(tmp_cache_dir):
    """Ensure every test starts with empty cache."""
    pass


def _make_price_df(closes: list[float], start="2020-02-15") -> pd.DataFrame:
    dates = pd.date_range(start=start, periods=len(closes), freq="D")
    return pd.DataFrame({"Close": closes}, index=dates)


async def test_unknown_event_returns_error():
    result = await stress_backtest.get_metric_stress_backtest("not_a_real_event")
    assert "error" in result


async def test_vix_backtest_shapes_groups_with_returns():
    df = _make_price_df([100.0, 80.0])
    mock_ticker = MagicMock()
    mock_ticker.history.return_value = df

    with patch("terminalq.analytics.backtest_utils.yfinance.Ticker", return_value=mock_ticker):
        result = await stress_backtest.get_metric_stress_backtest("vix_2020_covid")

    assert result["metric"] == "vix"
    assert result["window"] == "2020-02-15 to 2020-04-15"
    assert "broad_market" in result["groups"]
    assert result["groups"]["broad_market"]["SPY"]["pct_change"] == -20.0
    assert "vol_products" in result["groups"]
    assert "VIXY" in result["groups"]["vol_products"]


async def test_credit_event_uses_hyg_jnk_not_fred_series():
    df = _make_price_df([90.0, 85.0])
    mock_ticker = MagicMock()
    mock_ticker.history.return_value = df

    with patch("terminalq.analytics.backtest_utils.yfinance.Ticker", return_value=mock_ticker):
        result = await stress_backtest.get_metric_stress_backtest("credit_2008_gfc")

    assert result["metric"] == "credit_spreads"
    assert "HYG" in result["groups"]["hy_bond_proxy"]
    assert "JNK" in result["groups"]["hy_bond_proxy"]


async def test_cpi_event_returns_inflation_linked_groups():
    df = _make_price_df([50.0, 55.0], start="2021-06-01")
    mock_ticker = MagicMock()
    mock_ticker.history.return_value = df

    with patch("terminalq.analytics.backtest_utils.yfinance.Ticker", return_value=mock_ticker):
        result = await stress_backtest.get_metric_stress_backtest("cpi_2021_22_surge")

    assert result["metric"] == "cpi"
    assert "TIP" in result["groups"]["inflation_protected"]
    assert result["peak_value"].startswith("CPI YoY peaked at 9.1%")
