"""Tests for mango.providers.retail_sentiment — AAII survey + SPY put/call."""

from unittest.mock import AsyncMock, patch

import pytest

from mango.providers import retail_sentiment


@pytest.fixture(autouse=True)
def clear_caches(tmp_cache_dir):
    """Ensure every test starts with empty cache."""
    pass


_SAMPLE_AAII_HTML = """
<html><body>
<table>
<tr><th>Reported Date</th><th>Bullish</th><th>Neutral</th><th>Bearish</th></tr>
<tr><td>Jun 3</td><td>36.3% </td><td>26.7%</td><td>37.0% </td></tr>
<tr><td>May 27</td><td>35.6% </td><td>22.6%</td><td>41.9% </td></tr>
<tr><td>May 20</td><td>31.7% </td><td>24.7%</td><td>43.6% </td></tr>
<tr><td>May 13</td><td>39.3% </td><td>24.1%</td><td>36.6% </td></tr>
</table>
</body></html>
"""


def test_parse_aaii_table_newest_first():
    rows = retail_sentiment._parse_aaii_table(_SAMPLE_AAII_HTML)

    assert rows[0] == {"week": "Jun 3", "bullish": 36.3, "neutral": 26.7, "bearish": 37.0}
    assert len(rows) == 4


def test_parse_aaii_table_no_table_returns_empty():
    assert retail_sentiment._parse_aaii_table("<html><body>none</body></html>") == []


_SURVEY = retail_sentiment._parse_aaii_table(_SAMPLE_AAII_HTML)


async def test_get_retail_sentiment_combines_sources():
    put_call = {"ratio": 1.35, "put_volume": 2700000.0, "call_volume": 2000000.0}
    with (
        patch.object(retail_sentiment, "_fetch_aaii_survey", AsyncMock(return_value=_SURVEY)),
        patch.object(retail_sentiment, "_fetch_spy_put_call", AsyncMock(return_value=put_call)),
    ):
        result = await retail_sentiment.get_retail_sentiment()

    assert result["source"] == "aaii + yahoo_finance"
    aaii = result["aaii_survey"]
    assert aaii["week"] == "Jun 3"
    assert aaii["bull_bear_spread"] == round(36.3 - 37.0, 1)
    # 4-week average of raw spreads, rounded once at the end
    expected_avg = round(((36.3 - 37.0) + (35.6 - 41.9) + (31.7 - 43.6) + (39.3 - 36.6)) / 4, 1)
    assert aaii["bull_bear_spread_4wk_avg"] == expected_avg
    pc = result["spy_put_call"]
    assert pc["ratio"] == 1.35
    assert "fear" in pc["signal"].lower() or "hedging" in pc["signal"].lower()


async def test_get_retail_sentiment_extreme_pessimism_is_contrarian():
    gloomy = [{"week": "Jun 3", "bullish": 20.0, "neutral": 25.0, "bearish": 55.0}] * 4
    with (
        patch.object(retail_sentiment, "_fetch_aaii_survey", AsyncMock(return_value=gloomy)),
        patch.object(retail_sentiment, "_fetch_spy_put_call", AsyncMock(return_value=None)),
    ):
        result = await retail_sentiment.get_retail_sentiment()

    assert "contrarian" in result["aaii_survey"]["signal"].lower()
    assert "unavailable" in result["spy_put_call"]["signal"].lower()


async def test_get_retail_sentiment_all_sources_failed_returns_error():
    with (
        patch.object(retail_sentiment, "_fetch_aaii_survey", AsyncMock(return_value=[])),
        patch.object(retail_sentiment, "_fetch_spy_put_call", AsyncMock(return_value=None)),
    ):
        result = await retail_sentiment.get_retail_sentiment()

    assert "error" in result
