"""Tests for terminalq.analytics.correlation — cross-asset correlation matrix."""

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from terminalq.analytics import correlation
from terminalq.providers.crypto_analytics import _daily_returns, _pearson


@pytest.fixture(autouse=True)
def clear_caches(tmp_cache_dir):
    """Ensure every test starts with empty cache."""
    pass


def _make_close_df(closes: list[float]) -> pd.DataFrame:
    dates = pd.date_range("2025-01-01", periods=len(closes), freq="D")
    return pd.DataFrame({"Close": closes}, index=dates)


def _ticker_factory(data_map: dict[str, list[float]]):
    def _make_ticker(symbol):
        mock_ticker = MagicMock()
        if symbol in data_map:
            mock_ticker.history.return_value = _make_close_df(data_map[symbol])
        else:
            mock_ticker.history.return_value = pd.DataFrame()
        return mock_ticker

    return _make_ticker


# AAA: linear increasing series
_AAA = [100.0 + i for i in range(35)]
# BBB: exactly 2x AAA -> identical daily returns -> correlation 1.0 with AAA
_BBB = [2 * c for c in _AAA]
# CCC: a different decreasing series
_CCC = [200.0 - 0.7 * i for i in range(35)]


async def test_correlation_matrix_custom_symbols():
    """Custom symbols produce a full pairwise matrix with notable pairs."""
    data_map = {"AAA": _AAA, "BBB": _BBB, "CCC": _CCC}

    with patch("terminalq.analytics.correlation.yfinance.Ticker", side_effect=_ticker_factory(data_map)):
        result = await correlation.get_cross_asset_correlation_matrix("AAA,BBB,CCC")

    assert result["source"] == "yahoo_finance (computed)"
    assert set(result["tickers"]) == {"AAA", "BBB", "CCC"}

    matrix = result["matrix"]
    # Diagonal is always 1.0
    for t in ["AAA", "BBB", "CCC"]:
        assert matrix[t][t] == 1.0

    # AAA and BBB have identical returns -> correlation 1.0
    assert matrix["AAA"]["BBB"] == 1.0
    # Symmetric
    assert matrix["BBB"]["AAA"] == matrix["AAA"]["BBB"]

    # Cross-check AAA/CCC against the underlying pearson helper
    expected_corr = _pearson(_daily_returns(_AAA), _daily_returns(_CCC))
    assert matrix["AAA"]["CCC"] == expected_corr

    notable = result["notable_pairs"]
    assert set(notable["highest_positive"]["pair"]) == {"AAA", "BBB"}
    assert notable["highest_positive"]["correlation"] == 1.0
    assert "decoupled" in notable
    assert result["excluded"] == []


async def test_correlation_matrix_excludes_failed_ticker():
    """Tickers with no/insufficient price data are excluded from the matrix."""
    data_map = {"AAA": _AAA, "BBB": _BBB}  # EMPTY intentionally missing -> empty DataFrame

    with patch("terminalq.analytics.correlation.yfinance.Ticker", side_effect=_ticker_factory(data_map)):
        result = await correlation.get_cross_asset_correlation_matrix("AAA,BBB,EMPTY")

    assert set(result["tickers"]) == {"AAA", "BBB"}
    assert "EMPTY" not in result["matrix"]
    assert result["excluded"] == ["EMPTY"]


async def test_correlation_matrix_insufficient_data():
    """All tickers failing returns an error dict."""
    with patch("terminalq.analytics.correlation.yfinance.Ticker", side_effect=_ticker_factory({})):
        result = await correlation.get_cross_asset_correlation_matrix("AAA,BBB")

    assert "error" in result
    assert result["source"] == "yahoo_finance (computed)"


async def test_correlation_matrix_too_few_symbols():
    """A single symbol returns an error without making any network call."""
    result = await correlation.get_cross_asset_correlation_matrix("AAA")
    assert "error" in result


async def test_correlation_matrix_default_universe():
    """Empty symbols arg uses the default cross-asset universe."""
    data_map = {ticker: _AAA for ticker in correlation.DEFAULT_UNIVERSE}

    with patch("terminalq.analytics.correlation.yfinance.Ticker", side_effect=_ticker_factory(data_map)):
        result = await correlation.get_cross_asset_correlation_matrix("")

    assert set(result["tickers"]) == set(correlation.DEFAULT_UNIVERSE)


async def test_correlation_matrix_cache_hit():
    """Second call with the same symbols uses the cached result."""
    data_map = {"AAA": _AAA, "BBB": _BBB, "CCC": _CCC}

    with patch("terminalq.analytics.correlation.yfinance.Ticker", side_effect=_ticker_factory(data_map)) as mock_cls:
        first = await correlation.get_cross_asset_correlation_matrix("AAA,BBB,CCC")
        second = await correlation.get_cross_asset_correlation_matrix("AAA,BBB,CCC")

    assert first == second
    # 3 tickers fetched only on the first call
    assert mock_cls.call_count == 3
