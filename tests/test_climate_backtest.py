"""Tests for mango.providers.climate stress-period backtest (Yahoo Finance mocked)."""

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from mango.providers import climate


@pytest.fixture(autouse=True)
def clear_caches(tmp_cache_dir):
    """Ensure every test starts with empty cache."""
    pass


def _make_price_df(closes: list[float], start="2015-10-01") -> pd.DataFrame:
    dates = pd.date_range(start=start, periods=len(closes), freq="D")
    return pd.DataFrame({"Close": closes}, index=dates)


async def test_unknown_period_returns_error():
    result = await climate.get_climate_stress_backtest("not_a_real_period")
    assert "error" in result


async def test_full_backtest_shapes_regions_with_returns():
    df = _make_price_df([50.0, 55.0])
    mock_ticker = MagicMock()
    mock_ticker.history.return_value = df

    with patch("mango.analytics.backtest_utils.yfinance.Ticker", return_value=mock_ticker):
        result = await climate.get_climate_stress_backtest("el_nino_2015_16")

    assert result["window"] == "2015-10-01 to 2016-04-30"
    assert "us_corn_belt" in result["regions"]
    corn = result["regions"]["us_corn_belt"]
    assert "ZC=F" in corn["commodity_proxy_returns"]
    assert corn["commodity_proxy_returns"]["ZC=F"]["pct_change"] == 10.0
    assert "ADM" in corn["equity_returns"]
    # Region with no Yahoo-tradable commodity proxy (palm oil) stays empty, not fabricated
    assert result["regions"]["indonesia_palm"]["commodity_proxy_returns"] == {}
