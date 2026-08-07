"""Historical percentile context — turns a raw metric into 'where it sits vs its own history'.

A number like 'HY spread 2.8%' means little on its own; '24th percentile
since 1997' tells you whether it is calm or stressed. These helpers compute
that context from a plain list of historical values.
"""

import statistics

# Interpretation bands (percentile, 0-100)
_BOTTOM_DECILE = 10.0
_LOW_BAND = 33.0
_HIGH_BAND = 67.0
_TOP_DECILE = 90.0


def percentile_rank(values: list[float], value: float) -> float | None:
    """Share of historical observations at or below `value`, as 0-100.

    Returns None when there is no history to rank against. Never mutates
    the input list.
    """
    if not values:
        return None
    at_or_below = sum(1 for v in values if v <= value)
    return round(100.0 * at_or_below / len(values), 1)


def describe_percentile(pct: float) -> str:
    """Plain-English read of a percentile rank (positional, not good/bad)."""
    if pct <= _BOTTOM_DECILE:
        return "bottom decile vs history — extremely low"
    if pct <= _LOW_BAND:
        return "below its historical norm"
    if pct < _HIGH_BAND:
        return "mid-range vs history — unremarkable"
    if pct < _TOP_DECILE:
        return "above its historical norm"
    return "top decile vs history — extremely high"


def series_context(values: list[float], value: float | None = None) -> dict:
    """Summarize where a value sits against a full history of observations.

    Args:
        values: Historical observations (any order; oldest-to-newest typical).
        value: Value to rank; defaults to the last observation.

    Returns:
        Dict with latest, percentile, min/max/median, and interpretation —
        or an error dict when history is empty.
    """
    if not values:
        return {"error": "no history available"}
    latest = values[-1] if value is None else value
    pct = percentile_rank(values, latest)
    return {
        "latest": latest,
        "percentile": pct,
        "min": min(values),
        "max": max(values),
        "median": round(statistics.median(values), 2),
        "interpretation": describe_percentile(pct),
    }
