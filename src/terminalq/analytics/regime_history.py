"""Regime-score memory — what actually happened after each score, historically.

The regime scores are weighted by academic priors. This closes the loop: for
every past snapshot, look up what BTC / the S&P actually did N days later and
group the realized forward returns by score band. Over time it answers the only
question that matters — "when the Crypto Regime score was high, did forward
returns actually reward it?" — turning theory into something checked against the
assets you watch. Early on the sample is tiny; the tool says so rather than
pretending a handful of points is signal.
"""

from datetime import date, datetime, timedelta

from terminalq.mango.logging import log

from terminalq.history import latest_snapshot_per_day
from terminalq.mango import historical

# Band edges mirror the global FR scoring rubric.
_BANDS = [
    (0, 25, "Euphoric / expensive"),
    (25, 45, "Mid-cycle"),
    (45, 65, "Neutral / transitional"),
    (65, 80, "Bottom-forming"),
    (80, 101, "Deep-value capitulation"),
]

# Which snapshot score maps to which forward-return symbol.
_SERIES = {
    "crypto_regime": ("BTC-USD", "BTC"),
    "equity_regime": ("^GSPC", "S&P 500"),
}


def _band(score: float) -> str:
    for lo, hi, label in _BANDS:
        if lo <= score < hi:
            return label
    return "out-of-range"


def _close_map(result: dict) -> dict[str, float]:
    if "error" in result:
        return {}
    return {p["date"]: p["close"] for p in result.get("prices", []) if p.get("close")}


def _forward_return(closes: dict[str, float], start: str, forward_days: int) -> float | None:
    """Percent return from the close on/after `start` to the close on/after start+N."""
    if not closes:
        return None
    sorted_dates = sorted(closes)
    try:
        start_dt = datetime.strptime(start, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
    target_dt = start_dt + timedelta(days=forward_days)

    base = next((closes[d] for d in sorted_dates if d >= start), None)
    fwd = next((closes[d] for d in sorted_dates if datetime.strptime(d, "%Y-%m-%d").date() >= target_dt), None)
    if base is None or fwd is None or base == 0:
        return None
    return round((fwd / base - 1) * 100, 2)


async def get_regime_history(forward_days: int = 30) -> dict:
    """Group historical regime scores by band and show realized forward returns.

    Args:
        forward_days: Horizon for the forward return (e.g. 30, 90).

    Returns:
        Dict with per-band average forward returns and sample sizes for both the
        Crypto and Equity regime scores, plus a maturity caveat — or an error if
        no snapshots have been recorded yet.
    """
    snapshots = latest_snapshot_per_day()
    if not snapshots:
        return {
            "error": "No FR snapshots recorded yet. Run FR (which records a snapshot) to build history.",
            "source": "regime_history (local + yahoo)",
        }

    closes_by_symbol: dict[str, dict[str, float]] = {}
    for symbol, _ in _SERIES.values():
        result = await historical.get_historical(symbol, period="2y", interval="1d")
        closes_by_symbol[symbol] = _close_map(result)
        if not closes_by_symbol[symbol]:
            log.warning("regime_history: no price history for %s", symbol)

    today = date.today()
    output: dict[str, dict] = {}
    matured_total = 0
    for score_key, (symbol, label) in _SERIES.items():
        closes = closes_by_symbol[symbol]
        buckets: dict[str, list[float]] = {}
        matured = 0
        pending = 0
        for snap in snapshots:
            score = snap.get(score_key)
            if score is None:
                continue
            try:
                score = float(score)
            except (ValueError, TypeError):
                continue
            snap_date = snap.get("date", "")
            try:
                due = datetime.strptime(snap_date, "%Y-%m-%d").date() + timedelta(days=forward_days)
            except (ValueError, TypeError):
                continue
            if due > today:
                pending += 1
                continue
            ret = _forward_return(closes, snap_date, forward_days)
            if ret is None:
                continue
            buckets.setdefault(_band(score), []).append(ret)
            matured += 1

        matured_total += matured
        band_summary = {
            band: {
                "n": len(rets),
                "avg_forward_return_pct": round(sum(rets) / len(rets), 2),
                "returns": rets,
            }
            for band, rets in sorted(buckets.items())
        }
        output[score_key] = {
            "forward_symbol": label,
            "matured_samples": matured,
            "pending_samples": pending,
            "by_band": band_summary,
        }

    if matured_total == 0:
        maturity = (
            f"No snapshot is yet {forward_days} days old, so there are no realized "
            "forward returns to show. This calibration becomes meaningful as history "
            "accumulates — check back after the horizon has elapsed."
        )
    elif matured_total < 10:
        maturity = (
            f"Only {matured_total} matured sample(s) — directional at best, NOT statistically "
            "meaningful. Treat as a developing track record, not a backtest."
        )
    else:
        maturity = (
            f"{matured_total} matured samples. Compare the bottom-forming/deep-value bands "
            "against the euphoric band: higher forward returns from the high-score bands would "
            "validate the scoring; the reverse would say the weights need tuning."
        )

    return {
        "forward_days": forward_days,
        "snapshots_recorded": len(snapshots),
        "scores": output,
        "maturity_caveat": maturity,
        "note": (
            "Forward return = percent move in the mapped asset from the snapshot date to "
            f"{forward_days} days later. Bands mirror the FR scoring rubric. Higher score = "
            "more bottom-like; the hypothesis is that higher-score bands earn higher forward returns."
        ),
        "source": "regime_history (local + yahoo)",
    }
