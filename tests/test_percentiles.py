"""Tests for mango.analytics.percentiles — historical percentile context."""

from mango.analytics import percentiles

# ---------------------------------------------------------------------------
# percentile_rank
# ---------------------------------------------------------------------------


def test_percentile_rank_basic():
    """Rank = share of history at or below the value, as 0-100."""
    values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]

    assert percentiles.percentile_rank(values, 10.0) == 100.0
    assert percentiles.percentile_rank(values, 1.0) == 10.0
    assert percentiles.percentile_rank(values, 5.0) == 50.0


def test_percentile_rank_value_below_all_history():
    """A value below every observation ranks at the 0th percentile."""
    assert percentiles.percentile_rank([5.0, 6.0, 7.0], 1.0) == 0.0


def test_percentile_rank_empty_history_returns_none():
    assert percentiles.percentile_rank([], 5.0) is None


def test_percentile_rank_does_not_mutate_input():
    """Input list must not be sorted in place (immutability)."""
    values = [3.0, 1.0, 2.0]
    percentiles.percentile_rank(values, 2.0)
    assert values == [3.0, 1.0, 2.0]


# ---------------------------------------------------------------------------
# describe_percentile
# ---------------------------------------------------------------------------


def test_describe_percentile_bands():
    assert "bottom decile" in percentiles.describe_percentile(5.0)
    assert "mid-range" in percentiles.describe_percentile(50.0)
    assert "top decile" in percentiles.describe_percentile(95.0)


# ---------------------------------------------------------------------------
# series_context
# ---------------------------------------------------------------------------


def test_series_context_summarizes_history():
    """Context includes latest value, percentile, range stats, and plain-English read."""
    values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]

    ctx = percentiles.series_context(values)

    assert ctx["latest"] == 10.0
    assert ctx["percentile"] == 100.0
    assert ctx["min"] == 1.0
    assert ctx["max"] == 10.0
    assert ctx["median"] == 5.5
    assert "top decile" in ctx["interpretation"]


def test_series_context_explicit_value():
    """A caller-supplied value is ranked instead of the last observation."""
    values = [1.0, 2.0, 3.0, 4.0]

    ctx = percentiles.series_context(values, value=1.0)

    assert ctx["latest"] == 1.0
    assert ctx["percentile"] == 25.0


def test_series_context_empty_history_returns_error():
    ctx = percentiles.series_context([])
    assert "error" in ctx
