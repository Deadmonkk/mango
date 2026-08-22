"""Correlation-regime monitor — detect when cross-asset correlations are shifting.

A single correlation snapshot tells you how assets move together *now*. The more
useful signal is the *change*: when everything starts correlating toward 1, the
diversification that protects a portfolio quietly stops working — a tell that has
preceded most risk-off drawdowns. This compares a recent window against a longer
baseline and flags pairs whose relationship is breaking, with no need to store
historical snapshots (both windows come from one 2-year price pull).

Phase 1 stress-conditioned coupling (added 2026-08-21): the shift-detector above
answers "is coupling changing" but not the sharper macro question — "does this
basket couple unusually tightly specifically when markets are under stress?" A
basket that looks calm on average but locks together on the pivot's worst days is
a diversification trap that a plain correlation number hides. Stress days are
defined as the pivot's own bottom-decile return days within a trailing window —
a percentile-based convention (deterministic, no threshold to age), stated
explicitly in `stress_definition` rather than left implicit. The same split is
recomputed at every rolling position across the full 2-year pull to rank how
unusual today's widening is — that ranking is against a distribution of
*overlapping, non-independent* rolling windows, informative but not a formal
significance test, and it says so.

Known limitation: closes are trimmed to a common length by position, not aligned
by calendar date (see `correlation.py`). Equities/ETFs share the same ~5-day
trading week so this costs little; crypto trades every day, so its correspondence
to a given "stress day" carries extra noise over long lookbacks.
"""

import asyncio

from mango.core.logging import log

from mango.core import cache
from mango.analytics.correlation import DEFAULT_UNIVERSE, _fetch_closes
from mango.analytics.percentiles import percentile_rank
from mango.ext_settings import (
    CACHE_TTL_CORRELATION_REGIME,
    CORRELATION_REGIME_HISTORY_PERIOD,
    CORRELATION_REGIME_LONG_DAYS,
    CORRELATION_REGIME_MIN_ROLLING_SAMPLES,
    CORRELATION_REGIME_MIN_STRESS_DAYS,
    CORRELATION_REGIME_ROLLING_STEP_DAYS,
    CORRELATION_REGIME_SHIFT_DELTA,
    CORRELATION_REGIME_SHORT_DAYS,
    CORRELATION_REGIME_STRESS_PERCENTILE,
    CORRELATION_REGIME_STRESS_PIVOT,
    CORRELATION_REGIME_STRESS_WIDENING_MODERATE,
    CORRELATION_REGIME_STRESS_WIDENING_SHARP,
    CORRELATION_REGIME_STRESS_WINDOW_DAYS,
)
from mango.providers.crypto_analytics import _daily_returns, _pearson

MIN_DATA_POINTS = CORRELATION_REGIME_LONG_DAYS + 5


def _avg_abs_correlation(returns: dict[str, list[float]], tickers: list[str]) -> float:
    """Mean of absolute pairwise correlations — how 'coupled' the basket is."""
    vals = []
    for i, t1 in enumerate(tickers):
        for t2 in tickers[i + 1 :]:
            vals.append(abs(_pearson(returns[t1], returns[t2])))
    return round(sum(vals) / len(vals), 3) if vals else 0.0


def _quantile(sorted_values: list[float], pct: float) -> float:
    """Linear-interpolated percentile VALUE of an already-sorted list (0 <= pct <= 100).

    The inverse of `percentile_rank` (value -> rank here; rank -> value there).
    """
    n = len(sorted_values)
    if n == 1:
        return sorted_values[0]
    rank = (pct / 100) * (n - 1)
    lo_idx = int(rank)
    hi_idx = min(lo_idx + 1, n - 1)
    frac = rank - lo_idx
    return sorted_values[lo_idx] + (sorted_values[hi_idx] - sorted_values[lo_idx]) * frac


def _stress_split(returns: dict[str, list[float]], tickers: list[str], pivot: str) -> dict | None:
    """Split one window's basket-wide coupling into pivot-stress-day vs normal-day.

    All arrays in `returns` must be the same length (same window, positionally
    aligned per the module's known limitation). Returns None when the pivot is
    missing from the basket or too few stress days fall in the window to trust
    a correlation over that subset.
    """
    if pivot not in returns or len(tickers) < 2:
        return None
    pivot_returns = returns[pivot]
    threshold = _quantile(sorted(pivot_returns), CORRELATION_REGIME_STRESS_PERCENTILE)
    stress_idx = {i for i, r in enumerate(pivot_returns) if r <= threshold}
    if len(stress_idx) < CORRELATION_REGIME_MIN_STRESS_DAYS:
        return None
    normal_idx = [i for i in range(len(pivot_returns)) if i not in stress_idx]

    def _subset(indices) -> dict[str, list[float]]:
        return {t: [returns[t][i] for i in indices] for t in tickers}

    normal_coupling = _avg_abs_correlation(_subset(normal_idx), tickers)
    stress_coupling = _avg_abs_correlation(_subset(sorted(stress_idx)), tickers)
    widening = round(stress_coupling - normal_coupling, 3)
    ratio = round(stress_coupling / normal_coupling, 2) if normal_coupling else None
    return {
        "normal_coupling": normal_coupling,
        "stress_coupling": stress_coupling,
        "stress_widening": widening,
        "stress_amplification_ratio": ratio,
        "stress_day_count": len(stress_idx),
        "window_days": len(pivot_returns),
    }


def _rolling_widenings(closes: dict[str, list[float]], tickers: list[str], pivot: str) -> list[float]:
    """Stress-widening at each rolling window position across the full history pull.

    Overlapping, non-independent samples by construction (step < window) — a
    distribution to rank today's reading against, not a set of independent draws.
    """
    full_returns = {t: _daily_returns(closes[t]) for t in tickers}
    n = len(full_returns[pivot])
    window = CORRELATION_REGIME_STRESS_WINDOW_DAYS
    widenings = []
    for start in range(0, max(n - window, 0), CORRELATION_REGIME_ROLLING_STEP_DAYS):
        sub = {t: full_returns[t][start : start + window] for t in tickers}
        split = _stress_split(sub, tickers, pivot)
        if split is not None:
            widenings.append(split["stress_widening"])
    return widenings


def _stress_assessment(split: dict | None, split_reason: str | None, widenings: list[float]) -> str:
    """Plain-English, interpretation-first read of the stress-conditioned coupling."""
    if split is None:
        return f"Stress-conditioned coupling unavailable: {split_reason}."

    parts = [
        f"Basket coupling is {split['normal_coupling']} in normal conditions vs "
        f"{split['stress_coupling']} on {CORRELATION_REGIME_STRESS_PIVOT}'s worst "
        f"~{CORRELATION_REGIME_STRESS_PERCENTILE:.0f}% of days "
        f"(widening of {split['stress_widening']} correlation units, "
        f"n={split['stress_day_count']} stress days of {split['window_days']})."
    ]

    # Anchored on absolute widening (correlation units), not the ratio — a ratio
    # can look extreme off a near-zero baseline coupling while meaning nothing.
    widening = split["stress_widening"]
    if widening < CORRELATION_REGIME_STRESS_WIDENING_MODERATE:
        parts.append("Cross-asset relationships stay roughly stable even under stress.")
    elif widening >= CORRELATION_REGIME_STRESS_WIDENING_SHARP:
        parts.append(
            "Cross-asset relationships amplify sharply under stress — diversification "
            "across this basket is historically unreliable exactly when it matters most."
        )
    else:
        parts.append("Cross-asset relationships tighten moderately under stress.")

    if len(widenings) >= CORRELATION_REGIME_MIN_ROLLING_SAMPLES:
        pct = percentile_rank(widenings, split["stress_widening"])
        parts.append(
            f"This widening sits at the {pct}th percentile of {len(widenings)} overlapping "
            f"rolling {CORRELATION_REGIME_STRESS_WINDOW_DAYS}-day windows over the trailing "
            f"{CORRELATION_REGIME_HISTORY_PERIOD} — informative, not a formal significance test "
            "given the overlap."
        )
    else:
        parts.append(
            f"Not enough rolling windows ({len(widenings)}, need "
            f"{CORRELATION_REGIME_MIN_ROLLING_SAMPLES}) to rank how unusual this is yet."
        )
    return " ".join(parts)


async def get_correlation_regime(symbols: str = "") -> dict:
    """Compare recent vs baseline cross-asset correlation to flag a regime shift.

    Args:
        symbols: Optional comma-separated yfinance tickers. Defaults to the
            cross-asset universe (equities, bonds, commodities, dollar, crypto).

    Returns:
        Dict with the average coupling now vs baseline, the biggest-moving pairs,
        a verdict on whether correlations are tightening (diversification failing)
        or loosening, and a stress-conditioned read (Phase 1): does this basket
        couple unusually tightly specifically on the pivot's worst days, and how
        unusual is that widening vs its own rolling history — or an error dict
        on insufficient data.
    """
    tickers = [s.strip().upper() for s in symbols.split(",") if s.strip()] if symbols else DEFAULT_UNIVERSE
    if len(tickers) < 2:
        return {"error": "Provide at least 2 symbols", "source": "yahoo_finance (computed)"}

    cache_key = f"correlation_regime_{','.join(tickers)}"
    cached = cache.get(cache_key)
    if cached:
        log.debug("Cache hit: %s", cache_key)
        return cached

    all_closes = await asyncio.gather(
        *[_fetch_closes(t, period=CORRELATION_REGIME_HISTORY_PERIOD) for t in tickers], return_exceptions=True
    )

    short_ret: dict[str, list[float]] = {}
    long_ret: dict[str, list[float]] = {}
    excluded: list[str] = []
    for ticker, closes in zip(tickers, all_closes):
        if isinstance(closes, BaseException) or len(closes) < MIN_DATA_POINTS:
            excluded.append(ticker)
            continue
        short_ret[ticker] = _daily_returns(closes[-CORRELATION_REGIME_SHORT_DAYS:])
        long_ret[ticker] = _daily_returns(closes[-CORRELATION_REGIME_LONG_DAYS:])

    included = list(short_ret.keys())
    if len(included) < 2:
        return {"error": "Insufficient price history for regime comparison", "source": "yahoo_finance (computed)"}

    # Stress-conditioned coupling (Phase 1): needs a longer trailing window than
    # the shift-detector above for stress-day sample size, so it works off a
    # separately-filtered ticker set (those with enough history for that window).
    stress_window = CORRELATION_REGIME_STRESS_WINDOW_DAYS
    pivot = CORRELATION_REGIME_STRESS_PIVOT
    stress_capable = {
        t: c for t, c in zip(tickers, all_closes)
        if not isinstance(c, BaseException) and len(c) >= stress_window + 1
    }
    stress_tickers = sorted(stress_capable)
    stress_split: dict | None = None
    stress_reason: str | None = None
    widenings: list[float] = []
    if pivot not in stress_capable:
        stress_reason = f"pivot ticker {pivot!r} not in requested/available symbols"
    elif len(stress_tickers) < 2:
        stress_reason = "fewer than 2 tickers have enough history for the stress window"
    else:
        current_ret = {t: _daily_returns(stress_capable[t][-stress_window:]) for t in stress_tickers}
        stress_split = _stress_split(current_ret, stress_tickers, pivot)
        if stress_split is None:
            stress_reason = (
                f"fewer than {CORRELATION_REGIME_MIN_STRESS_DAYS} of {pivot}'s worst-decile "
                f"days fall in the trailing {stress_window}-day window"
            )
        else:
            widenings = _rolling_widenings(stress_capable, stress_tickers, pivot)

    moved = []
    for i, t1 in enumerate(included):
        for t2 in included[i + 1 :]:
            recent = _pearson(short_ret[t1], short_ret[t2])
            baseline = _pearson(long_ret[t1], long_ret[t2])
            moved.append(
                {
                    "pair": [t1, t2],
                    "baseline": round(baseline, 2),
                    "recent": round(recent, 2),
                    "delta": round(recent - baseline, 2),
                }
            )

    moved.sort(key=lambda p: abs(p["delta"]), reverse=True)
    avg_delta = round(sum(abs(p["delta"]) for p in moved) / len(moved), 3)
    coupling_now = _avg_abs_correlation(short_ret, included)
    coupling_baseline = _avg_abs_correlation(long_ret, included)

    shifting = avg_delta >= CORRELATION_REGIME_SHIFT_DELTA
    tightening = coupling_now > coupling_baseline
    if shifting and tightening:
        verdict = (
            f"REGIME SHIFT — correlations tightening (avg coupling {coupling_baseline} → {coupling_now}). "
            "Assets are moving together more than usual; diversification is weakening, a classic risk-off tell."
        )
    elif shifting:
        verdict = (
            f"REGIME SHIFT — correlations loosening (avg coupling {coupling_baseline} → {coupling_now}). "
            "Relationships are decoupling; old hedges may no longer behave as expected."
        )
    else:
        verdict = (
            f"STABLE — correlations roughly in line with baseline (avg |Δ| {avg_delta}). "
            "Cross-asset relationships are holding; diversification is behaving normally."
        )

    percentile_samples = len(widenings) if widenings else 0
    stress_widening_percentile = (
        percentile_rank(widenings, stress_split["stress_widening"])
        if stress_split is not None and percentile_samples >= CORRELATION_REGIME_MIN_ROLLING_SAMPLES
        else None
    )

    result = {
        "tickers": included,
        "windows": {"recent_days": CORRELATION_REGIME_SHORT_DAYS, "baseline_days": CORRELATION_REGIME_LONG_DAYS},
        "avg_coupling_recent": coupling_now,
        "avg_coupling_baseline": coupling_baseline,
        "avg_abs_delta": avg_delta,
        "regime_shift": shifting,
        "biggest_movers": moved[:5],
        "excluded": excluded,
        "verdict": verdict,
        # --- Phase 1: stress-conditioned coupling ---
        "stress_definition": (
            f"{pivot} daily-return bottom {CORRELATION_REGIME_STRESS_PERCENTILE:.0f}% "
            f"within the trailing {stress_window}-day window"
        ),
        "normal_coupling": stress_split["normal_coupling"] if stress_split else None,
        "stress_coupling": stress_split["stress_coupling"] if stress_split else None,
        "stress_widening": stress_split["stress_widening"] if stress_split else None,
        "stress_amplification_ratio": stress_split["stress_amplification_ratio"] if stress_split else None,
        "stress_day_count": stress_split["stress_day_count"] if stress_split else None,
        "stress_widening_percentile": stress_widening_percentile,
        "stress_rolling_samples": percentile_samples,
        "stress_unavailable_reason": stress_reason,
        "history_window": {"period": CORRELATION_REGIME_HISTORY_PERIOD, "stress_window_days": stress_window},
        "assessment": _stress_assessment(stress_split, stress_reason, widenings),
        "note": (
            "Coupling = mean absolute pairwise correlation; closer to 1 means assets move "
            "more in lockstep. 'Recent' is the last ~1 month, 'baseline' the last ~1 quarter. "
            "A rising delta with rising coupling is the diversification-fails signal. "
            "'stress_widening_percentile' ranks against OVERLAPPING rolling windows (see "
            "'assessment') — informative about how unusual today's reading is, not a p-value. "
            "Closes are trimmed to a common length by position, not aligned by calendar date; "
            "crypto trades every day while equities trade ~5/week, so crypto's correspondence "
            "to a given stress day carries extra noise over long lookbacks."
        ),
        "source": "yahoo_finance (computed)",
    }
    cache.set(cache_key, result, CACHE_TTL_CORRELATION_REGIME)
    return result
