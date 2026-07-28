"""Tests for terminalq.analytics.correlation_regime — correlation shift monitor."""

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from terminalq.analytics import correlation_regime


@pytest.fixture(autouse=True)
def clear_caches(tmp_cache_dir):
    """Ensure every test starts with empty cache."""
    pass


def _closes_from_returns(returns: list[float], start: float = 100.0) -> list[float]:
    closes = [start]
    for r in returns:
        closes.append(closes[-1] * (1 + r))
    return closes


# A deterministic non-constant return pattern (gives variance for Pearson).
_PATTERN = [0.01, -0.008, 0.012, -0.009]
_N = 120
_R_A = [_PATTERN[i % len(_PATTERN)] for i in range(_N)]
# B mirrors A for the first 99 steps (corr ~1), then inverts for the last 21
# (corr ~ -1 in the recent window) -> a regime shift.
_R_B = [_R_A[i] if i < 99 else -_R_A[i] for i in range(_N)]

_A = _closes_from_returns(_R_A)
_B = _closes_from_returns(_R_B)
_A_STABLE = _A
_B_STABLE = [2 * c for c in _A]  # identical returns to A -> stable corr ~1


def _make_close_df(closes: list[float]) -> pd.DataFrame:
    dates = pd.date_range("2025-01-01", periods=len(closes), freq="D")
    return pd.DataFrame({"Close": closes}, index=dates)


def _ticker_factory(data_map: dict[str, list[float]]):
    def _make_ticker(symbol):
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = _make_close_df(data_map[symbol]) if symbol in data_map else pd.DataFrame()
        return mock_ticker

    return _make_ticker


async def test_correlation_regime_detects_shift():
    data_map = {"AAA": _A, "BBB": _B}
    with patch(
        "terminalq.analytics.correlation.yfinance.Ticker",
        side_effect=_ticker_factory(data_map),
    ):
        result = await correlation_regime.get_correlation_regime("AAA,BBB")

    assert result["source"] == "yahoo_finance (computed)"
    assert result["regime_shift"] is True
    assert "SHIFT" in result["verdict"]
    assert result["biggest_movers"]
    assert abs(result["biggest_movers"][0]["delta"]) >= correlation_regime.CORRELATION_REGIME_SHIFT_DELTA


async def test_correlation_regime_stable():
    data_map = {"AAA": _A_STABLE, "BBB": _B_STABLE}
    with patch(
        "terminalq.analytics.correlation.yfinance.Ticker",
        side_effect=_ticker_factory(data_map),
    ):
        result = await correlation_regime.get_correlation_regime("AAA,BBB")

    assert result["regime_shift"] is False
    assert "STABLE" in result["verdict"]


async def test_correlation_regime_insufficient_data():
    data_map = {"AAA": [100.0, 101.0, 102.0]}  # too short
    with patch(
        "terminalq.analytics.correlation.yfinance.Ticker",
        side_effect=_ticker_factory(data_map),
    ):
        result = await correlation_regime.get_correlation_regime("AAA,BBB")

    assert "error" in result


async def test_correlation_regime_too_few_symbols():
    result = await correlation_regime.get_correlation_regime("AAA")
    assert "error" in result
