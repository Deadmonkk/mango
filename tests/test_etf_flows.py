"""Tests for mango.providers.etf_flows — spot Bitcoin ETF flows scraped from Farside."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from mango.providers import etf_flows


@pytest.fixture(autouse=True)
def clear_caches(tmp_cache_dir):
    """Ensure every test starts with empty cache."""
    pass


_SAMPLE_HTML = """
<html><body>
<table class="etf">
<thead>
<tr><th>Date</th><th>IBIT</th><th>FBTC</th><th>GBTC</th><th>Total</th></tr>
</thead>
<tbody>
<tr><td>05 Jun 2026</td><td>200.0</td><td>50.5</td><td>(30.0)</td><td>220.5</td></tr>
<tr><td>08 Jun 2026</td><td>100.5</td><td>(20.3)</td><td>-</td><td>80.2</td></tr>
<tr><td>09 Jun 2026</td><td>(150.0)</td><td>(25.0)</td><td>(10.5)</td><td>(185.5)</td></tr>
<tr><td>Total</td><td>40,000.0</td><td>12,000.0</td><td>(20,000.0)</td><td>32,000.0</td></tr>
</tbody>
</table>
</body></html>
"""


def _mock_response(text_data, status_code=200):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = text_data
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}",
            request=MagicMock(),
            response=resp,
        )
    return resp


# ---------------------------------------------------------------------------
# HTML parsing
# ---------------------------------------------------------------------------


def test_parse_cell_value_handles_parentheses_and_dashes():
    """Farside formats negatives as (123.4) and zero/no-flow as '-'."""
    assert etf_flows._parse_value("123.4") == 123.4
    assert etf_flows._parse_value("(123.4)") == -123.4
    assert etf_flows._parse_value("-") == 0.0
    assert etf_flows._parse_value("") == 0.0
    assert etf_flows._parse_value("40,000.0") == 40000.0


_LIVE_LAYOUT_HTML = """
<html><body>
<table class="etf">
<tr><th><span></span></th><th><img src="blackrock.jpg"></th><th><img src="fidelity.jpg"></th><th>Total</th></tr>
<tr><th><span></span></th><th><span>&nbsp;&nbsp;IBIT</span></th><th><span>FBTC</span></th><th><span>BTCO</span></th><th></th></tr>
<tr><td>Fee</td><td>0.25%</td><td>0.25%</td><td>0.25%</td><td></td></tr>
<tr><td>09 Jun 2026</td><td>10.0</td><td>(5.0)</td><td>1.0</td><td>6.0</td></tr>
</table>
</body></html>
"""


def test_parse_farside_html_live_layout():
    """The live page has a logo row (images + 'Total', no tickers), then a
    ticker row that does NOT contain 'Total' (ends with an empty cell), then
    a fee row. Fund names must come from the ticker row."""
    days = etf_flows._parse_farside_html(_LIVE_LAYOUT_HTML)

    assert len(days) == 1
    assert days[0]["flows"] == {"IBIT": 10.0, "FBTC": -5.0, "BTCO": 1.0}
    assert days[0]["total_usd_m"] == 6.0
    assert "" not in days[0]["flows"]


def test_parse_farside_html_extracts_daily_rows():
    """Parser returns one entry per dated row, skipping the cumulative Total row."""
    days = etf_flows._parse_farside_html(_SAMPLE_HTML)

    assert len(days) == 3  # the "Total" summary row is excluded
    first = days[0]
    assert first["date"] == "05 Jun 2026"
    assert first["total_usd_m"] == 220.5
    assert first["flows"]["IBIT"] == 200.0
    assert first["flows"]["GBTC"] == -30.0

    last = days[-1]
    assert last["date"] == "09 Jun 2026"
    assert last["total_usd_m"] == -185.5


# ---------------------------------------------------------------------------
# Full tool flow
# ---------------------------------------------------------------------------


async def test_get_btc_etf_flows_success():
    """Returns recent daily flows with latest-day detail and a flow signal."""
    with patch("mango.providers.etf_flows.http.fetch_text",
               AsyncMock(return_value=_SAMPLE_HTML)):
        result = await etf_flows.get_btc_etf_flows(days=2)

    assert result["source"] == "farside"
    assert len(result["daily"]) == 2  # only last N days requested
    assert result["latest"]["date"] == "09 Jun 2026"
    assert result["latest"]["total_usd_m"] == -185.5
    # Sum over returned window: 80.2 + (-185.5)
    assert result["window_net_flow_usd_m"] == round(80.2 - 185.5, 1)
    assert "outflow" in result["signal"].lower()
    assert "note" in result


async def test_get_btc_etf_flows_blocked_returns_error():
    """A 403 (e.g. Cloudflare) returns an error dict, never raises."""
    denied = httpx.HTTPStatusError(
        "403", request=httpx.Request("GET", "https://x"),
        response=httpx.Response(403),
    )
    with patch("mango.providers.etf_flows.http.fetch_text", AsyncMock(side_effect=denied)):
        result = await etf_flows.get_btc_etf_flows()

    assert "error" in result
    assert result["source"] == "farside"


async def test_get_btc_etf_flows_unparseable_returns_error():
    """HTML without a recognizable flows table returns an error dict."""
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=_mock_response("<html><body>nothing here</body></html>"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("mango.providers.etf_flows.httpx.AsyncClient", return_value=mock_client):
        result = await etf_flows.get_btc_etf_flows()

    assert "error" in result
    assert result["source"] == "farside"
