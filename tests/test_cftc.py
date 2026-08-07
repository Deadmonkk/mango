"""Tests for mango.providers.cftc — CFTC Commitment of Traders (free, no API key)."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from mango.providers import cftc


@pytest.fixture(autouse=True)
def clear_caches(tmp_cache_dir):
    """Ensure every test starts with empty cache."""
    pass


def _mock_response(json_data, status_code=200):
    """Create a mock httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.text = ""
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}",
            request=MagicMock(),
            response=resp,
        )
    return resp


_LATEST = {
    "market_and_exchange_names": "BITCOIN - CHICAGO MERCANTILE EXCHANGE",
    "report_date_as_yyyy_mm_dd": "2026-06-02T00:00:00.000",
    "open_interest_all": "19882",
    "noncomm_positions_long_all": "17210",
    "noncomm_positions_short_all": "14752",
    "comm_positions_long_all": "184",
    "comm_positions_short_all": "2779",
    "nonrept_positions_long_all": "1153",
    "nonrept_positions_short_all": "1016",
}

_PRIOR = {
    "market_and_exchange_names": "BITCOIN - CHICAGO MERCANTILE EXCHANGE",
    "report_date_as_yyyy_mm_dd": "2026-05-26T00:00:00.000",
    "open_interest_all": "21625",
    "noncomm_positions_long_all": "17146",
    "noncomm_positions_short_all": "14864",
    "comm_positions_long_all": "212",
    "comm_positions_short_all": "2602",
    "nonrept_positions_long_all": "1267",
    "nonrept_positions_short_all": "1159",
}


async def test_get_cot_report_success():
    """Returns latest positioning with week-over-week change for each trader category."""
    mock_client = AsyncMock()
    mock_client.get.return_value = _mock_response([_LATEST, _PRIOR])
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("mango.providers.cftc.httpx.AsyncClient", return_value=mock_client):
        result = await cftc.get_cot_report("btc")

    assert result["source"] == "cftc"
    assert result["market"] == "btc"
    assert result["report_date"] == "2026-06-02"
    assert result["open_interest"] == 19882

    large_spec = result["large_speculators"]
    assert large_spec["long"] == 17210
    assert large_spec["short"] == 14752
    assert large_spec["net"] == 17210 - 14752

    prior_net = 17146 - 14864
    assert large_spec["net_change"] == (17210 - 14752) - prior_net

    commercial = result["commercial"]
    assert commercial["net"] == 184 - 2779

    small_spec = result["small_speculators"]
    assert small_spec["net"] == 1153 - 1016

    assert "signal" in result


async def test_get_cot_report_unknown_market():
    """Unknown market alias returns an error without making a network call."""
    result = await cftc.get_cot_report("not_a_market")
    assert "error" in result
    assert result["source"] == "cftc"


async def test_get_cot_report_no_data():
    """Empty response from CFTC returns an error dict."""
    mock_client = AsyncMock()
    mock_client.get.return_value = _mock_response([])
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("mango.providers.cftc.httpx.AsyncClient", return_value=mock_client):
        result = await cftc.get_cot_report("btc")

    assert "error" in result
    assert result["source"] == "cftc"


async def test_get_cot_report_connection_error():
    """Connection failure returns an error dict."""
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=httpx.ConnectError("boom"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("mango.providers.cftc.httpx.AsyncClient", return_value=mock_client):
        result = await cftc.get_cot_report("btc")

    assert "error" in result
    assert result["source"] == "cftc"


async def test_get_cot_report_cache_hit():
    """Second call uses cached result without re-fetching."""
    mock_client = AsyncMock()
    mock_client.get.return_value = _mock_response([_LATEST, _PRIOR])
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("mango.providers.cftc.httpx.AsyncClient", return_value=mock_client) as mock_cls:
        first = await cftc.get_cot_report("btc")
        second = await cftc.get_cot_report("btc")

    assert first == second
    assert mock_cls.call_count == 1
