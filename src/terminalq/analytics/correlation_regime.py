"""Correlation-regime monitor — detect when cross-asset correlations are shifting.

A single correlation snapshot tells you how assets move together *now*. The more
useful signal is the *change*: when everything starts correlating toward 1, the
diversification that protects a portfolio quietly stops working — a tell that has
preceded most risk-off drawdowns. This compares a recent window against a longer
baseline and flags pairs whose relationship is breaking, with no need to store
historical snapshots (both windows come from one 6-month price pull).
"""

import asyncio

from terminalq.mango.logging import log

from terminalq.mango import cache
from terminalq.analytics.correlation import DEFAULT_UNIVERSE, _fetch_closes
from terminalq.ext_settings import (
    CACHE_TTL_CORRELATION_REGIME,
    CORRELATION_REGIME_LONG_DAYS,
    CORRELATION_REGIME_SHIFT_DELTA,
    CORRELATION_REGIME_SHORT_DAYS,
)
from terminalq.providers.crypto_analytics import _daily_returns, _pearson

MIN_DATA_POINTS = CORRELATION_REGIME_LONG_DAYS + 5


def _avg_abs_correlation(returns: dict[str, list[float]], tickers: list[str]) -> float:
    """Mean of absolute pairwise correlations — how 'coupled' the basket is."""
    vals = []
    for i, t1 in enumerate(tickers):
        for t2 in tickers[i + 1 :]:
            vals.append(abs(_pearson(returns[t1], returns[t2])))
    return round(sum(vals) / len(vals), 3) if vals else 0.0


async def get_correlation_regime(symbols: str = "") -> dict:
    """Compare recent vs baseline cross-asset correlation to flag a regime shift.

    Args:
        symbols: Optional comma-separated yfinance tickers. Defaults to the
            cross-asset universe (equities, bonds, commodities, dollar, crypto).

    Returns:
        Dict with the average coupling now vs baseline, the biggest-moving pairs,
        and a verdict on whether correlations are tightening (diversification
        failing) or loosening — or an error dict on insufficient data.
    """
    tickers = [s.strip().upper() for s in symbols.split(",") if s.strip()] if symbols else DEFAULT_UNIVERSE
    if len(tickers) < 2:
        return {"error": "Provide at least 2 symbols", "source": "yahoo_finance (computed)"}

    cache_key = f"correlation_regime_{','.join(tickers)}"
    cached = cache.get(cache_key)
    if cached:
        log.debug("Cache hit: %s", cache_key)
        return cached

    all_closes = await asyncio.gather(*[_fetch_closes(t) for t in tickers], return_exceptions=True)

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
        "note": (
            "Coupling = mean absolute pairwise correlation; closer to 1 means assets move "
            "more in lockstep. 'Recent' is the last ~1 month, 'baseline' the last ~1 quarter. "
            "A rising delta with rising coupling is the diversification-fails signal."
        ),
        "source": "yahoo_finance (computed)",
    }
    cache.set(cache_key, result, CACHE_TTL_CORRELATION_REGIME)
    return result
