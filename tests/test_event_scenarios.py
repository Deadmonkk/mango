"""Tests for terminalq.providers.event_scenarios — event reaction scaffolding."""

from unittest.mock import AsyncMock, patch

import pytest

from terminalq.providers import event_scenarios


@pytest.fixture(autouse=True)
def clear_caches(tmp_cache_dir):
    pass


_CALENDAR = {
    "events": [
        {"date": "2026-06-12", "event": "CPI", "impact": "high", "why": "inflation"},
        {"date": "2026-06-17", "event": "FOMC decision", "impact": "high", "why": "rates"},
    ],
    "source": "fred",
}
_SNAPSHOT = {"date": "2026-06-11", "equity_regime": 42, "crypto_regime": 49, "cpi_mom": 0.47, "fed_path": "+30bp"}


async def test_event_scenarios_anchors_and_context():
    with (
        patch.object(event_scenarios.fred, "get_release_calendar", new=AsyncMock(return_value=_CALENDAR)),
        patch.object(event_scenarios, "latest_snapshot_per_day", return_value=[_SNAPSHOT]),
    ):
        result = await event_scenarios.get_event_scenarios(days=7)

    assert result["have_snapshot"] is True
    # CPI event gets anchored to the last cpi_mom reading
    cpi_event = next(e for e in result["events"] if e["event"] == "CPI")
    assert cpi_event["anchor"]["current"] == 0.47
    # FOMC has no anchor mapping
    fomc = next(e for e in result["events"] if "FOMC" in e["event"])
    assert fomc["anchor"] is None
    assert result["regime_context"]["crypto_regime"] == 49
    assert result["regime_context"]["fed_path"] == "+30bp"


async def test_event_scenarios_no_snapshot():
    with (
        patch.object(event_scenarios.fred, "get_release_calendar", new=AsyncMock(return_value=_CALENDAR)),
        patch.object(event_scenarios, "latest_snapshot_per_day", return_value=[]),
    ):
        result = await event_scenarios.get_event_scenarios()

    assert result["have_snapshot"] is False
    assert result["regime_context"] == {}


async def test_event_scenarios_calendar_error():
    err = {"error": "FRED_API_KEY not configured", "source": "fred"}
    with patch.object(event_scenarios.fred, "get_release_calendar", new=AsyncMock(return_value=err)):
        result = await event_scenarios.get_event_scenarios()

    assert "error" in result
    assert "hint" in result
