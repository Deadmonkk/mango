"""Refuse to cache provider payloads that carry an error.

Why this exists
---------------
Providers in this stack never raise — they return ``{"error": ...}`` dicts
(see the error convention in CLAUDE.md). A plain TTL cache happily stores
those, which turns a two-second upstream blip into a full TTL window of
silent data loss.

That is exactly what happened on 2026-08-06: FRED timed out for a handful of
series during one collector run, the error dicts were cached for an hour, and
the next run replayed 24 ``data unavailable`` cells in 0.0 seconds. Worse, the
loss was not neutral — the Equity Regime Score's credit component reads the
CCC-BB gap, and a *missing* gap meant no dock was applied, so an outage
rendered as the most bullish possible credit reading.

The rule this enforces is "degrade loudly": a failure may be served once, but
it must never be remembered. Partial failures count — a dashboard where 9 of
11 series failed is not a result worth keeping.
"""

from typing import Any

ERROR_KEY = "error"
MAX_SCAN_DEPTH = 4  # provider payloads nest at most ~3 levels (dashboard.indicators.series)


def contains_error(value: Any, _depth: int = 0) -> bool:
    """True if value carries a provider error anywhere within MAX_SCAN_DEPTH.

    Detects both a top-level ``{"error": ...}`` and the nested form that
    dashboards produce, e.g. ``{"indicators": {"gdp": {"error": ...}}}``.
    A falsy ``error`` value (None, "") is not an error.
    """
    if _depth > MAX_SCAN_DEPTH:
        return False
    if isinstance(value, dict):
        if value.get(ERROR_KEY):
            return True
        return any(contains_error(v, _depth + 1) for v in value.values())
    if isinstance(value, list):
        return any(contains_error(v, _depth + 1) for v in value)
    return False


def should_cache(value: Any) -> bool:
    """True when value is clean enough to persist. Errors are never cached."""
    return not contains_error(value)
