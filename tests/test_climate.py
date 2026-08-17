"""Tests for mango.providers.climate — NASA POWER production-risk watch."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from mango.providers import climate


@pytest.fixture(autouse=True)
def clear_caches(tmp_cache_dir):
    """Ensure every test starts with empty cache."""
    pass


def _daily_payload(temp_values, precip_values):
    dates = [f"2026070{i}" for i in range(1, len(temp_values) + 1)]
    return {
        "properties": {
            "parameter": {
                "T2M": dict(zip(dates, temp_values)),
                "PRECTOTCORR": dict(zip(dates, precip_values)),
            }
        }
    }


def _climatology_payload(month_abbr, normal_temp, normal_precip):
    months = ["ANN", "JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
    return {
        "properties": {
            "parameter": {
                "T2M": {m: (normal_temp if m == month_abbr else 15.0) for m in months},
                "PRECTOTCORR": {m: (normal_precip if m == month_abbr else 2.0) for m in months},
            }
        }
    }


def _mock_fetch(daily, clim, fail=False):
    """Stand in for http.fetch_json, which now owns transport and returns parsed JSON."""
    async def fake_fetch(url, **kwargs):
        if fail:
            raise httpx.ConnectError("boom")
        return daily if "daily" in url else clim

    return AsyncMock(side_effect=fake_fetch)


async def test_flags_hot_dry_region():
    import datetime as dt

    month_abbr = climate._MONTH_ABBR[dt.date.today().month - 1]
    daily = _daily_payload([30.0, 31.0, 29.0], [0.0, 0.0, 0.0])
    clim = _climatology_payload(month_abbr, normal_temp=20.0, normal_precip=5.0)

    with patch("mango.providers.climate.http.fetch_json", _mock_fetch(daily, clim)):
        result = await climate.get_climate_risk_watch()

    assert "regions" in result
    assert len(result["flagged_regions"]) == len(climate.REGIONS)
    first_key = next(iter(climate.REGIONS))
    reading = result["regions"][first_key]
    assert reading["temp_anomaly_c"] == 10.0
    assert reading["signal"].startswith("FLAGGED")
    assert "hotter" in reading["signal"]
    assert "drier" in reading["signal"]
    assert reading["watch"] == climate.REGIONS[first_key]["watch"]


async def test_normal_conditions_not_flagged():
    import datetime as dt

    month_abbr = climate._MONTH_ABBR[dt.date.today().month - 1]
    daily = _daily_payload([20.0, 20.0, 20.0], [5.0, 5.0, 5.0])
    clim = _climatology_payload(month_abbr, normal_temp=20.0, normal_precip=5.0)

    with patch("mango.providers.climate.http.fetch_json", _mock_fetch(daily, clim)):
        result = await climate.get_climate_risk_watch()

    assert result["flagged_regions"] == []
    first_key = next(iter(climate.REGIONS))
    assert result["regions"][first_key]["signal"] == "normal — within typical range for this time of year"


async def test_source_failure_returns_error_not_exception():
    with patch(
        "mango.providers.climate.http.fetch_json",
        _mock_fetch({}, {}, fail=True),
    ):
        result = await climate.get_climate_risk_watch()

    first_key = next(iter(climate.REGIONS))
    assert result["regions"][first_key]["error"] == "Connection failed"
    assert result["flagged_regions"] == []
