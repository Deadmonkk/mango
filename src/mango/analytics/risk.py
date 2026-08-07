"""Portfolio risk analytics: Sharpe, Sortino, max drawdown, VaR(95), beta vs SPY.

Clean-room implementation written directly from a written specification, not
from any prior risk module in this codebase family.

Definitions (state assumptions explicitly, since none of these are a single
universally-agreed formula):

- **Daily returns** are simple returns computed from each symbol's adjusted
  close (`get_historical` already returns split/dividend-adjusted closes —
  see ``mango.core.historical``): ``r_t = close_t / close_{t-1} - 1``.
- **Portfolio return per day** is the market-value-weighted sum of each
  included holding's daily return, using a single *fixed* weight per symbol
  (today's market value / total included market value). This is a static
  buy-and-hold weighting, not a daily-rebalanced portfolio — the simpler and
  more common convention for a point-in-time risk snapshot, and the only one
  computable from a holdings snapshot rather than a full transaction history.
- **Sharpe ratio** = mean(daily excess return) / stdev(daily excess return),
  annualized by `sqrt(252)` (252 = the conventional US trading-day count).
  Excess return = daily return minus the daily risk-free rate.
- **Sortino ratio** = mean(daily excess return) / downside deviation,
  annualized the same way. Downside deviation is the population stdev of
  *only* the excess-return days that are negative (Sortino's point is to not
  penalize upside volatility).
- **Max drawdown** = the largest peak-to-trough decline in the portfolio's
  cumulative-return curve over the window, expressed as a negative fraction
  (e.g. -0.18 = an 18% drawdown from the running peak).
- **VaR(95)** = the 5th percentile of the daily-return distribution (linear
  interpolation between order statistics), expressed as a signed fraction —
  a more negative number is a worse one-day loss at 95% confidence.
- **Beta vs SPY** = cov(portfolio daily returns, SPY daily returns) /
  var(SPY daily returns), over the same aligned date window.

**Risk-free rate assumption**: `mango.ext_settings` defines no risk-free-rate
constant (checked 2026-08 — it only carries cache TTLs and
technical/sentiment thresholds). This module assumes a **0% annual
risk-free rate** (`RISK_FREE_RATE_ANNUAL` below), so Sharpe/Sortino here are
effectively raw annualized return-per-unit-of-volatility rather than
excess-of-T-bill. Override `RISK_FREE_RATE_ANNUAL` if the host project ever
defines a real rate.

Never raises: no holdings, or no usable price history for any holding (or
for the SPY benchmark), returns ``{"error": ..., "source": "risk"}`` instead
of propagating an exception.
"""

from __future__ import annotations

import asyncio
import statistics
from typing import Any

from mango.core.historical import get_historical
from mango.core.logging import get_logger
from mango.core.portfolio import load_portfolio

log = get_logger("analytics.risk")

SOURCE = "risk"

# Conventional US trading-day count, used to annualize daily statistics.
TRADING_DAYS_PER_YEAR = 252
_ANNUALIZATION_FACTOR = TRADING_DAYS_PER_YEAR**0.5

# See module docstring: no host-defined risk-free rate exists, so this
# assumes 0%. Expressed as an annual rate; divided by TRADING_DAYS_PER_YEAR
# to get the daily rate subtracted from each day's return.
RISK_FREE_RATE_ANNUAL = 0.0

# VaR(95) = the 5th percentile of the daily-return distribution.
VAR_PERCENTILE = 5.0

BENCHMARK_SYMBOL = "SPY"

# Cash/placeholder rows carry no price history and contribute no market risk.
NON_TRADABLE_SYMBOLS = frozenset({"CASH"})

# Below this many aligned trading days, sample statistics (stdev especially)
# are too noisy to be meaningful, so the whole computation is refused rather
# than returned with a misleadingly precise-looking number.
MIN_ALIGNED_TRADING_DAYS = 5

# Decimal places for every ratio/percentage figure in the response.
_ROUND_DP = 4


def _is_valid_close(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _closes_by_date(history: dict) -> dict[str, float]:
    """Extract `{date: close}` from a `get_historical` success payload."""
    prices = history.get("prices") if isinstance(history, dict) else None
    if not prices:
        return {}
    return {row["date"]: row["close"] for row in prices if _is_valid_close(row.get("close"))}


def _daily_returns(dates: list[str], closes_by_date: dict[str, float]) -> list[float]:
    """Simple daily returns over `dates` (already sorted, already aligned)."""
    series = [closes_by_date[d] for d in dates]
    returns = []
    for prev, curr in zip(series, series[1:]):
        if prev == 0:
            returns.append(0.0)
            continue
        returns.append(curr / prev - 1)
    return returns


def _percentile(sorted_values: list[float], pct: float) -> float:
    """Linear-interpolated percentile of an already-sorted list (0 <= pct <= 100)."""
    n = len(sorted_values)
    if n == 1:
        return sorted_values[0]
    rank = (pct / 100) * (n - 1)
    lo_idx = int(rank)
    hi_idx = min(lo_idx + 1, n - 1)
    frac = rank - lo_idx
    return sorted_values[lo_idx] + (sorted_values[hi_idx] - sorted_values[lo_idx]) * frac


def _max_drawdown(returns: list[float]) -> float:
    """Largest peak-to-trough decline of the cumulative-return curve, as a negative fraction."""
    cumulative = 1.0
    peak = 1.0
    worst = 0.0
    for r in returns:
        cumulative *= 1 + r
        peak = max(peak, cumulative)
        drawdown = cumulative / peak - 1 if peak else 0.0
        worst = min(worst, drawdown)
    return worst


def _covariance(xs: list[float], ys: list[float]) -> float:
    mean_x, mean_y = statistics.fmean(xs), statistics.fmean(ys)
    return sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / len(xs)


def _aggregate_market_value_by_symbol(holdings: list[dict[str, Any]]) -> dict[str, float]:
    """Sum market value per symbol across accounts; drop cash/blank/non-positive rows."""
    totals: dict[str, float] = {}
    for holding in holdings:
        symbol = (holding.get("symbol") or "").strip().upper()
        market_value = holding.get("market_value", 0.0)
        if not symbol or symbol in NON_TRADABLE_SYMBOLS:
            continue
        if not isinstance(market_value, (int, float)) or market_value <= 0:
            continue
        totals[symbol] = totals.get(symbol, 0.0) + float(market_value)
    return totals


async def compute_portfolio_risk(period: str = "1y") -> dict:
    """Compute Sharpe, Sortino, max drawdown, VaR(95), and beta-vs-SPY for the portfolio.

    Reads holdings via `mango.core.portfolio.load_portfolio()` and prices via
    `mango.core.historical.get_historical(symbol, period)` for every distinct
    symbol plus the SPY benchmark. See the module docstring for every metric's
    definition and the assumptions baked into it (fixed weights, 0% risk-free
    rate, population downside deviation).
    """
    holdings = load_portfolio()
    if not holdings:
        return {"error": "No portfolio holdings found", "source": SOURCE}

    market_value_by_symbol = _aggregate_market_value_by_symbol(holdings)
    if not market_value_by_symbol:
        return {"error": "No tradable (non-cash, positive market value) holdings found", "source": SOURCE}

    symbols = list(market_value_by_symbol.keys())
    fetch_targets = symbols + [BENCHMARK_SYMBOL]
    histories = await asyncio.gather(
        *[get_historical(sym, period=period) for sym in fetch_targets],
        return_exceptions=True,
    )

    closes_by_symbol: dict[str, dict[str, float]] = {}
    excluded: list[str] = []
    for symbol, history in zip(fetch_targets, histories):
        if isinstance(history, BaseException) or not isinstance(history, dict) or history.get("error"):
            excluded.append(symbol)
            log.warning("compute_portfolio_risk: no usable history for %s", symbol)
            continue
        closes = _closes_by_date(history)
        if len(closes) < 2:
            excluded.append(symbol)
            continue
        closes_by_symbol[symbol] = closes

    if BENCHMARK_SYMBOL not in closes_by_symbol:
        return {"error": f"No usable price history for benchmark {BENCHMARK_SYMBOL!r}", "source": SOURCE}

    included_symbols = [s for s in symbols if s in closes_by_symbol]
    if not included_symbols:
        return {"error": "No usable price history for any portfolio holding", "source": SOURCE}

    # Aligned trading-day window: dates every included symbol AND the
    # benchmark all have a close for, so returns line up day-for-day.
    common_dates = set(closes_by_symbol[BENCHMARK_SYMBOL])
    for symbol in included_symbols:
        common_dates &= set(closes_by_symbol[symbol])
    common_dates_sorted = sorted(common_dates)

    if len(common_dates_sorted) < MIN_ALIGNED_TRADING_DAYS + 1:
        return {
            "error": (
                f"Insufficient aligned trading-day history "
                f"({max(len(common_dates_sorted) - 1, 0)} days, need {MIN_ALIGNED_TRADING_DAYS})"
            ),
            "source": SOURCE,
        }

    included_total_value = sum(market_value_by_symbol[s] for s in included_symbols)
    weights = {s: market_value_by_symbol[s] / included_total_value for s in included_symbols}

    symbol_returns = {
        s: _daily_returns(common_dates_sorted, closes_by_symbol[s]) for s in included_symbols
    }
    benchmark_returns = _daily_returns(common_dates_sorted, closes_by_symbol[BENCHMARK_SYMBOL])

    n_days = len(benchmark_returns)
    portfolio_returns = [
        sum(weights[s] * symbol_returns[s][t] for s in included_symbols) for t in range(n_days)
    ]

    daily_rf = RISK_FREE_RATE_ANNUAL / TRADING_DAYS_PER_YEAR
    excess_returns = [r - daily_rf for r in portfolio_returns]

    mean_excess = statistics.fmean(excess_returns)
    stdev_excess = statistics.pstdev(excess_returns) if len(excess_returns) > 1 else 0.0
    sharpe_ratio = (mean_excess / stdev_excess * _ANNUALIZATION_FACTOR) if stdev_excess else 0.0

    downside_excess = [r for r in excess_returns if r < 0]
    downside_deviation = statistics.pstdev(downside_excess) if len(downside_excess) > 1 else 0.0
    sortino_ratio = (
        (mean_excess / downside_deviation * _ANNUALIZATION_FACTOR) if downside_deviation else 0.0
    )

    max_drawdown = _max_drawdown(portfolio_returns)
    var_95 = _percentile(sorted(portfolio_returns), VAR_PERCENTILE)

    benchmark_variance = statistics.pvariance(benchmark_returns) if len(benchmark_returns) > 1 else 0.0
    beta = (
        _covariance(portfolio_returns, benchmark_returns) / benchmark_variance
        if benchmark_variance
        else 0.0
    )

    return {
        "period": period,
        "as_of": common_dates_sorted[-1],
        "n_days": n_days,
        "sharpe_ratio": round(sharpe_ratio, _ROUND_DP),
        "sortino_ratio": round(sortino_ratio, _ROUND_DP),
        "max_drawdown": round(max_drawdown, _ROUND_DP),
        "var_95_daily": round(var_95, _ROUND_DP),
        "beta_vs_spy": round(beta, _ROUND_DP),
        "risk_free_rate_annual": RISK_FREE_RATE_ANNUAL,
        "symbols_included": included_symbols,
        "symbols_excluded": sorted(set(excluded) - {BENCHMARK_SYMBOL}),
        "source": SOURCE,
    }
