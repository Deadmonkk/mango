"""Tests for terminalq.providers.options_flow — dealer gamma / options walls."""

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from terminalq.providers import options_flow


@pytest.fixture(autouse=True)
def clear_caches(tmp_cache_dir):
    """Ensure every test starts with empty cache."""
    pass


def _chain(calls_df, puts_df):
    obj = MagicMock()
    obj.calls = calls_df
    obj.puts = puts_df
    return obj


def _make_ticker(spot=600.0, with_options=True):
    ticker = MagicMock()
    ticker.history.return_value = pd.DataFrame({"Close": [spot]}, index=pd.date_range("2026-06-10", periods=1))
    if not with_options:
        ticker.options = []
        return ticker

    ticker.options = ["2099-01-15"]
    calls = pd.DataFrame(
        {
            "strike": [590.0, 610.0],
            "openInterest": [1000, 8000],  # call wall at 610
            "impliedVolatility": [0.20, 0.18],
        }
    )
    puts = pd.DataFrame(
        {
            "strike": [580.0, 595.0],
            "openInterest": [9000, 2000],  # put wall at 580
            "impliedVolatility": [0.25, 0.22],
        }
    )
    ticker.option_chain.return_value = _chain(calls, puts)
    return ticker


async def test_dealer_gamma_computes_walls():
    with patch(
        "terminalq.providers.options_flow.yfinance.Ticker",
        return_value=_make_ticker(),
    ):
        result = await options_flow.get_dealer_gamma("SPY")

    assert result["source"] == "yahoo_finance (options, computed)"
    assert result["symbol"] == "SPY"
    assert result["spot"] == 600.0
    assert result["call_wall"] == 610.0
    assert result["put_wall"] == 580.0
    # Total put OI 11000 vs call OI 9000 -> ratio ~1.22
    assert result["put_call_oi_ratio"] == round(11000 / 9000, 2)
    assert result["net_gamma_regime"] in ("positive", "negative")
    assert "wall" in result["signal"].lower()


async def test_dealer_gamma_no_options_returns_error():
    with patch(
        "terminalq.providers.options_flow.yfinance.Ticker",
        return_value=_make_ticker(with_options=False),
    ):
        result = await options_flow.get_dealer_gamma("ILLIQUID")

    assert "error" in result


async def test_dealer_gamma_empty_price_returns_error():
    ticker = MagicMock()
    ticker.history.return_value = pd.DataFrame()
    with patch("terminalq.providers.options_flow.yfinance.Ticker", return_value=ticker):
        result = await options_flow.get_dealer_gamma("SPY")

    assert "error" in result


def test_bs_gamma_degenerate_inputs():
    assert options_flow._bs_gamma(0, 100, 0.1, 0.2) == 0.0
    assert options_flow._bs_gamma(100, 100, 0, 0.2) == 0.0
    assert options_flow._bs_gamma(100, 100, 0.1, 0) == 0.0


def test_finite_coerces_nan_and_garbage():
    assert options_flow._finite(float("nan"), int) == 0
    assert options_flow._finite(float("inf"), float) == 0.0
    assert options_flow._finite(None, int) == 0
    assert options_flow._finite("junk", float) == 0.0
    assert options_flow._finite(1234.0, int) == 1234


async def test_dealer_gamma_survives_nan_open_interest():
    """Yahoo chains return NaN for openInterest/IV; int(NaN) used to crash the tool."""
    ticker = MagicMock()
    ticker.history.return_value = pd.DataFrame({"Close": [600.0]}, index=pd.date_range("2026-06-10", periods=1))
    ticker.options = ["2099-01-15"]
    nan = float("nan")
    calls = pd.DataFrame(
        {
            "strike": [590.0, 610.0],
            "openInterest": [nan, 8000],  # NaN OI must be skipped, not crash
            "impliedVolatility": [nan, 0.18],
        }
    )
    puts = pd.DataFrame({"strike": [580.0], "openInterest": [9000], "impliedVolatility": [nan]})
    ticker.option_chain.return_value = _chain(calls, puts)

    with patch("terminalq.providers.options_flow.yfinance.Ticker", return_value=ticker):
        result = await options_flow.get_dealer_gamma("SPY")

    # Must return a valid result, not raise: the good (610 call / 580 put) rows survive.
    assert "error" not in result
    assert result["call_wall"] == 610.0
    assert result["put_wall"] == 580.0
    # Valid inputs produce a positive gamma
    assert options_flow._bs_gamma(100, 100, 0.1, 0.2) > 0
