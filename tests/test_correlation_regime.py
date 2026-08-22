"""Tests for mango.analytics.correlation_regime — correlation shift monitor."""

import math
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from mango.analytics import correlation_regime


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
        "mango.analytics.correlation.yfinance.Ticker",
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
        "mango.analytics.correlation.yfinance.Ticker",
        side_effect=_ticker_factory(data_map),
    ):
        result = await correlation_regime.get_correlation_regime("AAA,BBB")

    assert result["regime_shift"] is False
    assert "STABLE" in result["verdict"]


async def test_correlation_regime_insufficient_data():
    data_map = {"AAA": [100.0, 101.0, 102.0]}  # too short
    with patch(
        "mango.analytics.correlation.yfinance.Ticker",
        side_effect=_ticker_factory(data_map),
    ):
        result = await correlation_regime.get_correlation_regime("AAA,BBB")

    assert "error" in result


async def test_correlation_regime_too_few_symbols():
    result = await correlation_regime.get_correlation_regime("AAA")
    assert "error" in result


async def test_correlation_regime_stress_unavailable_without_pivot():
    """No SPY in the basket -> stress fields are None with a stated reason, other fields intact."""
    data_map = {"AAA": _A, "BBB": _B}
    with patch(
        "mango.analytics.correlation.yfinance.Ticker",
        side_effect=_ticker_factory(data_map),
    ):
        result = await correlation_regime.get_correlation_regime("AAA,BBB")

    assert result["normal_coupling"] is None
    assert result["stress_coupling"] is None
    assert result["stress_widening_percentile"] is None
    assert result["stress_unavailable_reason"] is not None
    assert "unavailable" in result["assessment"].lower()
    # Untouched by the Phase 1 addition:
    assert result["regime_shift"] is True


# --- Phase 1: stress-conditioned coupling -----------------------------------

_STRESS_N = 400


def _stress_series(pivot_crashes_with: float | None) -> tuple[list[float], list[float]]:
    """Build (pivot_returns, other_returns) with a periodic crash every 10th day.

    On non-crash days the two series use different sinusoidal frequencies/phases
    (low, near-decorrelated Pearson). On crash days (10% of days, matching
    CORRELATION_REGIME_STRESS_PERCENTILE) the pivot always drops ~10%; `other`
    either co-crashes proportionally (stress widening should appear) or is left
    on its normal-day pattern (control: no widening).
    """
    pivot, other = [], []
    for i in range(_STRESS_N):
        if i % 10 == 9:
            pivot.append(-0.10 - 0.01 * math.sin(i))
            other.append(pivot[-1] * pivot_crashes_with if pivot_crashes_with is not None else 0.01 * math.sin(i * 1.9 + 2.3))
        else:
            pivot.append(0.01 * math.sin(i * 0.7))
            other.append(0.01 * math.sin(i * 1.9 + 2.3))
    return pivot, other


async def test_correlation_regime_stress_widening_detected():
    """A basket that co-crashes on the pivot's worst days shows stress > normal coupling."""
    spy_ret, tlt_ret = _stress_series(pivot_crashes_with=0.9)  # co-crashes proportionally
    spy_closes = _closes_from_returns(spy_ret)
    tlt_closes = _closes_from_returns(tlt_ret)
    data_map = {"SPY": spy_closes, "TLT": tlt_closes}

    with patch(
        "mango.analytics.correlation.yfinance.Ticker",
        side_effect=_ticker_factory(data_map),
    ):
        result = await correlation_regime.get_correlation_regime("SPY,TLT")

    assert result["stress_unavailable_reason"] is None
    assert result["stress_definition"].startswith("SPY daily-return bottom")
    assert result["stress_day_count"] >= correlation_regime.CORRELATION_REGIME_MIN_STRESS_DAYS
    assert result["stress_coupling"] > result["normal_coupling"]
    assert result["stress_widening"] > 0
    assert result["stress_amplification_ratio"] > 1
    assert result["stress_rolling_samples"] >= correlation_regime.CORRELATION_REGIME_MIN_ROLLING_SAMPLES
    assert result["stress_widening_percentile"] is not None
    assert "amplif" in result["assessment"].lower() or "tighten" in result["assessment"].lower()
    assert "percentile of" in result["assessment"].lower()


async def test_correlation_regime_stress_control_no_widening():
    """When the pair's relationship doesn't change on stress days, widening is ~0."""
    spy_ret, tlt_ret = _stress_series(pivot_crashes_with=None)  # 'other' never sees the crash relationship
    spy_closes = _closes_from_returns(spy_ret)
    tlt_closes = _closes_from_returns(tlt_ret)
    data_map = {"SPY": spy_closes, "TLT": tlt_closes}

    with patch(
        "mango.analytics.correlation.yfinance.Ticker",
        side_effect=_ticker_factory(data_map),
    ):
        result = await correlation_regime.get_correlation_regime("SPY,TLT")

    assert result["stress_unavailable_reason"] is None
    assert abs(result["stress_widening"]) < 0.2
    assert "stable" in result["assessment"].lower()
