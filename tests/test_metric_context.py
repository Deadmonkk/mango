"""Tests for fred.get_metric_context — percentile context for any FRED metric."""

from unittest.mock import AsyncMock, patch

import pytest

from terminalq.providers import fred_ext as fred


@pytest.fixture(autouse=True)
def clear_caches(tmp_cache_dir):
    """Ensure every test starts with empty cache."""
    pass


_HISTORY = {
    "series": "BAMLH0A0HYM2",
    "title": "ICE BofA US High Yield Index Option-Adjusted Spread",
    "units": "Percent",
    "start_date": "1996-12-31",
    "latest_date": "2026-06-09",
    "latest": 2.8,
    "values": [2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 2.8],
    "source": "fred",
}


async def test_get_metric_context_ranks_latest_against_history():
    with patch.object(fred, "get_series_history", AsyncMock(return_value=_HISTORY)):
        result = await fred.get_metric_context("hy_spread")

    assert result["source"] == "fred"
    assert result["latest"] == 2.8
    # 2.0 and 2.8 are the only observations <= 2.8 → 2 of 10
    assert result["percentile_since_start"] == 20.0
    assert result["history_start"] == "1996-12-31"
    assert result["min"] == 2.0
    assert result["max"] == 10.0
    assert "interpretation" in result
    assert "note" in result


async def test_get_metric_context_propagates_provider_error():
    error = {"error": "Connection failed", "source": "fred"}
    with patch.object(fred, "get_series_history", AsyncMock(return_value=error)):
        result = await fred.get_metric_context("hy_spread")

    assert "error" in result
    assert result["source"] == "fred"


async def test_get_metric_context_empty_history_returns_error():
    empty = dict(_HISTORY, values=[], latest=None)
    with patch.object(fred, "get_series_history", AsyncMock(return_value=empty)):
        result = await fred.get_metric_context("hy_spread")

    assert "error" in result
