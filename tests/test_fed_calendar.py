"""Tests for the FOMC calendar provider and the finnhub calendar fallback."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from terminalq.providers import fed_calendar, finnhub
from tests._upstream_wiring import requires


@pytest.fixture(autouse=True)
def clear_caches(tmp_cache_dir):
    """Ensure every test starts with empty cache."""
    pass


_SAMPLE_HTML = """
<html><body>
<h4>2026 FOMC Meetings</h4>
<div class="fomc-meeting">
  <div class="fomc-meeting__month col-xs-5"><strong>January</strong></div>
  <div class="fomc-meeting__date col-xs-4">27-28</div>
</div>
<div class="fomc-meeting">
  <div class="fomc-meeting__month col-xs-5"><strong>April/May</strong></div>
  <div class="fomc-meeting__date col-xs-4">30-1</div>
</div>
<div class="fomc-meeting">
  <div class="fomc-meeting__month col-xs-5"><strong>June</strong></div>
  <div class="fomc-meeting__date col-xs-4">16-17*</div>
</div>
<h4>2027 FOMC Meetings</h4>
<div class="fomc-meeting">
  <div class="fomc-meeting__month col-xs-5"><strong>January</strong></div>
  <div class="fomc-meeting__date col-xs-4">26-27</div>
</div>
</body></html>
"""


def test_parse_fomc_html_decision_dates():
    """Each meeting resolves to its final (decision) day, year-aware,
    including month-spanning meetings like April/May 30-1."""
    meetings = fed_calendar._parse_fomc_html(_SAMPLE_HTML)

    dates = [m.isoformat() for m in meetings]
    assert "2026-01-28" in dates
    assert "2026-05-01" in dates  # April/May 30-1 → May 1
    assert "2026-06-17" in dates
    assert "2027-01-27" in dates


async def test_get_fomc_meetings_window_and_next():
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.text = _SAMPLE_HTML
    resp.raise_for_status = MagicMock()
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("terminalq.providers.fed_calendar.httpx.AsyncClient", return_value=mock_client):
        result = await fed_calendar.get_fomc_meetings(days_ahead=400)

    assert result["source"] == "federalreserve.gov"
    assert result["next_fomc"] is not None
    assert all(e["impact"] == "high" for e in result["events"])
    assert any("FOMC" in e["event"] for e in result["events"])


async def test_get_fomc_meetings_fetch_failure_returns_error():
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=httpx.ConnectError("boom"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("terminalq.providers.fed_calendar.httpx.AsyncClient", return_value=mock_client):
        result = await fed_calendar.get_fomc_meetings()

    assert "error" in result
    assert result["source"] == "federalreserve.gov"


@requires(finnhub, "fred_ext")
async def test_economic_calendar_falls_back_to_fomc_on_finnhub_error():
    """Finnhub's calendar is premium-only — the tool must degrade to free sources.

    Here FRED releases return empty, so only the FOMC date flows through; this
    isolates the FOMC-fallback path (the full FRED+FOMC merge is covered in
    test_release_calendar.py)."""
    fomc_payload = {
        "events": [{"date": "2026-06-17", "event": "FOMC meeting — rate decision", "impact": "high"}],
        "next_fomc": {"date": "2026-06-17", "days_until": 7},
        "source": "federalreserve.gov",
    }
    with (
        patch.object(finnhub, "_fetch", AsyncMock(return_value={"_error": "HTTP 403"})),
        patch.object(
            finnhub.fred_ext,
            "get_release_calendar",
            AsyncMock(return_value={"events": [], "source": "fred"}),
        ),
        patch.object(finnhub.fed_calendar, "get_fomc_meetings", AsyncMock(return_value=fomc_payload)),
    ):
        result = await finnhub.get_economic_calendar(days=7)

    assert "error" not in result
    assert result["events"] == fomc_payload["events"]
    assert result["next_fomc"]["date"] == "2026-06-17"
    assert "fallback" in result["source"]
