"""Tests for mango.analytics.risk — Sharpe, Sortino, max drawdown, VaR, beta.

No network: `load_portfolio` and `get_historical` are monkeypatched with
synthetic fixtures (made-up tickers, round numbers). The headline test
verifies every metric against arithmetic worked out by hand in the comments
below, using the exact definitions documented in `mango/analytics/risk.py`.
"""

from __future__ import annotations

import statistics

import pytest

from mango.analytics import risk

# --- Synthetic fixture -------------------------------------------------------
#
# Two holdings, AAA (60% weight by market value) and BBB (40% weight), plus
# the SPY benchmark, over 6 aligned trading days (5 daily returns — the
# minimum this module accepts).
#
# Daily returns chosen directly (prices are derived from them) so the
# portfolio/benchmark return series are exact, round numbers:
#   AAA returns: [0.02, -0.01,  0.03, -0.02,  0.01]
#   BBB returns: [0.01,  0.01,  0.01,  0.01,  0.01]
#   SPY returns: [0.01, -0.01,  0.01, -0.01,  0.01]
#
# Portfolio return[t] = 0.6*AAA[t] + 0.4*BBB[t]:
#   [0.016, -0.002, 0.022, -0.008, 0.010]

_DATES = [f"2026-01-{i + 1:02d}" for i in range(6)]

_AAA_RETURNS = [0.02, -0.01, 0.03, -0.02, 0.01]
_BBB_RETURNS = [0.01, 0.01, 0.01, 0.01, 0.01]
_SPY_RETURNS = [0.01, -0.01, 0.01, -0.01, 0.01]

_PORTFOLIO_RETURNS = [0.6 * a + 0.4 * b for a, b in zip(_AAA_RETURNS, _BBB_RETURNS)]


def _prices_from_returns(start: float, returns: list[float]) -> list[float]:
    prices = [start]
    for r in returns:
        prices.append(prices[-1] * (1 + r))
    return prices


_AAA_PRICES = _prices_from_returns(100.0, _AAA_RETURNS)
_BBB_PRICES = _prices_from_returns(50.0, _BBB_RETURNS)
_SPY_PRICES = _prices_from_returns(400.0, _SPY_RETURNS)

_PRICES_BY_SYMBOL = {"AAA": _AAA_PRICES, "BBB": _BBB_PRICES, "SPY": _SPY_PRICES}

_HOLDINGS = [
    {
        "account": "Test",
        "symbol": "AAA",
        "name": "Alpha Fund",
        "shares": 10.0,
        "cost_basis": 500.0,
        "market_value": 600.0,
        "unrealized_gl": 100.0,
    },
    {
        "account": "Test",
        "symbol": "BBB",
        "name": "Beta Fund",
        "shares": 10.0,
        "cost_basis": 350.0,
        "market_value": 400.0,
        "unrealized_gl": 50.0,
    },
]


def _history_payload(symbol: str, prices: list[float]) -> dict:
    return {
        "symbol": symbol,
        "prices": [{"date": d, "close": c} for d, c in zip(_DATES, prices)],
        "count": len(prices),
        "source": "yahoo_finance",
    }


def _install_fixture(monkeypatch, prices_by_symbol: dict[str, list[float]], holdings: list[dict]) -> None:
    async def fake_load_portfolio_result():
        return holdings

    def fake_load_portfolio():
        return holdings

    async def fake_get_historical(symbol: str, period: str = "1y", interval: str = "1d") -> dict:
        if symbol not in prices_by_symbol:
            return {"error": f"No data for {symbol!r}", "symbol": symbol, "source": "yahoo_finance"}
        return _history_payload(symbol, prices_by_symbol[symbol])

    monkeypatch.setattr(risk, "load_portfolio", fake_load_portfolio)
    monkeypatch.setattr(risk, "get_historical", fake_get_historical)


# --- Hand-computed metrics ----------------------------------------------


async def test_compute_portfolio_risk_matches_hand_computed_metrics(monkeypatch) -> None:
    # Arrange
    _install_fixture(monkeypatch, _PRICES_BY_SYMBOL, _HOLDINGS)

    # Act
    result = await risk.compute_portfolio_risk(period="1y")

    # Assert — basic shape
    assert result["source"] == "risk"
    assert result["symbols_included"] == ["AAA", "BBB"]
    assert result["n_days"] == 5
    assert result["risk_free_rate_annual"] == risk.RISK_FREE_RATE_ANNUAL

    # Sharpe: mean(excess)/pstdev(excess) * sqrt(252). Excess is net of the
    # daily risk-free rate, which is no longer zero.
    daily_rf = risk.RISK_FREE_RATE_ANNUAL / 252
    excess = [r - daily_rf for r in _PORTFOLIO_RETURNS]
    mean_excess = statistics.fmean(excess)
    stdev_excess = statistics.pstdev(excess)
    expected_sharpe = mean_excess / stdev_excess * (252**0.5)
    assert result["sharpe_ratio"] == pytest.approx(expected_sharpe, abs=1e-3)

    # Sortino: same numerator; denominator is the root-mean-square of the
    # losses over ALL observations (target downside deviation). Up days are
    # mapped to zero, so they stay in the divisor without adding to the sum.
    downside_dev = (sum(min(r, 0.0) ** 2 for r in excess) / len(excess)) ** 0.5
    expected_sortino = mean_excess / downside_dev * (252**0.5)
    assert result["sortino_ratio"] == pytest.approx(expected_sortino, abs=1e-3)

    # Guard the convention itself, not just the arithmetic: the divisor is N,
    # never the count of losing days. The two agree only when every day is a
    # loss, so this fixture (which has up days) separates them.
    losing_days = [r for r in excess if r < 0]
    assert 0 < len(losing_days) < len(excess), "fixture must contain both up and down days"
    down_days_only_dev = (sum(r**2 for r in losing_days) / len(losing_days)) ** 0.5
    wrong_sortino = mean_excess / down_days_only_dev * (252**0.5)
    # Fewer terms over the same sum ⇒ larger denominator ⇒ strictly lower ratio.
    assert wrong_sortino < expected_sortino
    assert result["sortino_ratio"] != pytest.approx(wrong_sortino, abs=1e-3)

    # Max drawdown: worked out by hand from the cumulative-return curve —
    # the trough occurs after the -0.008 day, before the final +0.010 day
    # pushes the curve to a new high.
    cumulative, peak, worst = 1.0, 1.0, 0.0
    for r in _PORTFOLIO_RETURNS:
        cumulative *= 1 + r
        peak = max(peak, cumulative)
        worst = min(worst, cumulative / peak - 1)
    # Reported as a positive magnitude — "max drawdown 8.7%" is the convention.
    assert result["max_drawdown"] == pytest.approx(abs(worst), abs=1e-6)
    assert result["max_drawdown"] > 0  # a real drawdown occurred

    # VaR(95): 5th percentile of the 5 portfolio returns, linear interpolation.
    sorted_returns = sorted(_PORTFOLIO_RETURNS)
    rank = 0.05 * (len(sorted_returns) - 1)
    lo_idx, frac = int(rank), rank - int(rank)
    expected_var = sorted_returns[lo_idx] + (sorted_returns[lo_idx + 1] - sorted_returns[lo_idx]) * frac
    assert result["var_95_daily"] == pytest.approx(expected_var, abs=1e-6)

    # Beta vs SPY: cov(portfolio, spy) / var(spy) — comes out to an exact 1.05
    # by construction of this fixture (see the module-level comment).
    assert result["beta_vs_spy"] == pytest.approx(1.05, abs=1e-6)


# --- Error paths -------------------------------------------------------------


async def test_compute_portfolio_risk_returns_error_when_no_holdings(monkeypatch) -> None:
    # Arrange
    _install_fixture(monkeypatch, _PRICES_BY_SYMBOL, [])

    # Act
    result = await risk.compute_portfolio_risk()

    # Assert
    assert result["source"] == "risk"
    assert "error" in result


async def test_compute_portfolio_risk_returns_error_when_benchmark_history_unavailable(monkeypatch) -> None:
    # Arrange — SPY deliberately excluded from the mocked price universe
    prices_without_spy = {"AAA": _AAA_PRICES, "BBB": _BBB_PRICES}
    _install_fixture(monkeypatch, prices_without_spy, _HOLDINGS)

    # Act
    result = await risk.compute_portfolio_risk()

    # Assert
    assert "error" in result
    assert result["source"] == "risk"


async def test_compute_portfolio_risk_returns_error_when_all_holdings_are_cash(monkeypatch) -> None:
    # Arrange
    cash_only = [
        {
            "account": "Test",
            "symbol": "CASH",
            "name": "Cash",
            "shares": 0.0,
            "cost_basis": 0.0,
            "market_value": 1000.0,
            "unrealized_gl": 0.0,
        }
    ]
    _install_fixture(monkeypatch, _PRICES_BY_SYMBOL, cash_only)

    # Act
    result = await risk.compute_portfolio_risk()

    # Assert
    assert "error" in result
    assert result["source"] == "risk"


# --- Robustness: partial history failure -------------------------------------


async def test_compute_portfolio_risk_excludes_symbol_with_no_history_and_renormalizes(monkeypatch) -> None:
    # Arrange — a third holding, CCC, has no price history available at all
    holdings_with_ccc = _HOLDINGS + [
        {
            "account": "Test",
            "symbol": "CCC",
            "name": "Gamma Fund",
            "shares": 5.0,
            "cost_basis": 100.0,
            "market_value": 200.0,
            "unrealized_gl": 0.0,
        }
    ]
    _install_fixture(monkeypatch, _PRICES_BY_SYMBOL, holdings_with_ccc)  # CCC absent from price map

    # Act
    result = await risk.compute_portfolio_risk()

    # Assert — CCC is excluded, AAA/BBB weights renormalize to the same 60/40
    # split as the main fixture, so the metrics match it exactly.
    assert result["symbols_included"] == ["AAA", "BBB"]
    assert "CCC" in result["symbols_excluded"]
    assert result["beta_vs_spy"] == pytest.approx(1.05, abs=1e-6)
