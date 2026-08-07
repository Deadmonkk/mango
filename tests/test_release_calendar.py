"""Tests for fred.get_release_calendar and the merged economic calendar fallback."""

from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from ._upstream_wiring import requires, host_module

finnhub = host_module("terminalq.providers.finnhub")
from mango.providers import fred_ext as fred


@pytest.fixture(autouse=True)
def clear_caches(tmp_cache_dir):
    """Ensure every test starts with empty cache."""
    pass


def _today_plus(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def _mock_response(json_data, status_code=200):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.text = ""
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}", request=MagicMock(), response=resp
        )
    return resp


def _release_payload():
    """FRED releases/dates shape: high-impact + noise releases, future dates."""
    return {
        "release_dates": [
            {"release_id": 10, "release_name": "Consumer Price Index", "date": _today_plus(1)},
            {"release_id": 345, "release_name": "Research Consumer Price Index", "date": _today_plus(1)},
            {"release_id": 46, "release_name": "Producer Price Index", "date": _today_plus(2)},
            {"release_id": 50, "release_name": "Employment Situation", "date": _today_plus(5)},
            {"release_id": 54, "release_name": "Personal Income and Outlays", "date": _today_plus(6)},
            {"release_id": 140, "release_name": "Gross Domestic Product by State", "date": _today_plus(6)},
            {"release_id": 192, "release_name": "Job Openings and Labor Turnover Survey", "date": _today_plus(40)},
        ]
    }


async def test_release_calendar_filters_to_high_impact(monkeypatch):
    monkeypatch.setattr("mango.providers.fred_ext.FRED_API_KEY", "test_key")

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=_mock_response(_release_payload()))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("mango.providers.fred_ext.httpx.AsyncClient", return_value=mock_client):
        result = await fred.get_release_calendar(days=7)

    assert result["source"] == "fred"
    names = [e["event"] for e in result["events"]]
    # High-impact within the 7-day window only
    assert any("CPI" in n for n in names)
    assert any("Jobs Report" in n or "Employment" in n for n in names)
    assert any("PCE" in n for n in names)
    # Noise releases excluded
    assert not any("Research" in n for n in names)
    assert not any("by State" in n for n in names)
    # JOLTS at +40d is outside the 7-day window
    assert not any("JOLTS" in n for n in names)
    # Every event is dated, high-impact, and carries a plain-English why
    for e in result["events"]:
        assert e["date"] and e["impact"] == "high" and e["why"]
    # Sorted ascending by date
    assert names == [e["event"] for e in sorted(result["events"], key=lambda x: x["date"])]


async def test_release_calendar_no_key_returns_error(monkeypatch):
    monkeypatch.setattr("mango.providers.fred_ext.FRED_API_KEY", "")
    result = await fred.get_release_calendar(days=7)
    assert "error" in result
    assert result["source"] == "fred"


@requires(finnhub, "fred_ext")
async def test_economic_calendar_fallback_merges_fred_and_fomc():
    """When Finnhub is premium-walled, the calendar merges FRED releases + FOMC, date-sorted."""
    fred_payload = {
        "events": [
            {"date": _today_plus(1), "event": "CPI (inflation)", "impact": "high", "why": "inflation gauge"},
            {
                "date": _today_plus(6),
                "event": "PCE (Fed's preferred inflation gauge)",
                "impact": "high",
                "why": "Fed gauge",
            },
        ],
        "source": "fred",
    }
    fomc_payload = {
        "events": [{"date": _today_plus(7), "event": "FOMC meeting — rate decision", "impact": "high"}],
        "next_fomc": {"date": _today_plus(7), "days_until": 7},
        "source": "federalreserve.gov",
    }
    with (
        patch.object(finnhub, "_fetch", AsyncMock(return_value={"_error": "HTTP 403"})),
        patch.object(finnhub.fred_ext, "get_release_calendar", AsyncMock(return_value=fred_payload)),
        patch.object(finnhub.fed_calendar, "get_fomc_meetings", AsyncMock(return_value=fomc_payload)),
    ):
        result = await finnhub.get_economic_calendar(days=7)

    assert "error" not in result
    events = result["events"]
    assert len(events) == 3  # 2 FRED + 1 FOMC
    # Merged and sorted ascending by date
    assert [e["date"] for e in events] == sorted(e["date"] for e in events)
    assert result["next_fomc"]["days_until"] == 7
    assert "fred" in result["source"] and "federalreserve" in result["source"]


@requires(finnhub, "fred_ext")
async def test_economic_calendar_fallback_survives_fred_failure():
    """If FRED releases fail too, still return FOMC dates rather than nothing."""
    fomc_payload = {
        "events": [{"date": _today_plus(7), "event": "FOMC meeting — rate decision", "impact": "high"}],
        "next_fomc": {"date": _today_plus(7), "days_until": 7},
        "source": "federalreserve.gov",
    }
    with (
        patch.object(finnhub, "_fetch", AsyncMock(return_value={"_error": "HTTP 403"})),
        patch.object(finnhub.fred_ext, "get_release_calendar", AsyncMock(return_value={"error": "boom", "source": "fred"})),
        patch.object(finnhub.fed_calendar, "get_fomc_meetings", AsyncMock(return_value=fomc_payload)),
    ):
        result = await finnhub.get_economic_calendar(days=7)

    assert "error" not in result
    assert len(result["events"]) == 1
    assert "FOMC" in result["events"][0]["event"]
