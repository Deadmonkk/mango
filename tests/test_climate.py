"""Tests for terminalq.providers.climate — NASA POWER production-risk watch."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from terminalq.providers import climate


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


def _mock_client(daily, clim, fail=False):
    def _resp(payload):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.json = MagicMock(return_value=payload)
        resp.raise_for_status = MagicMock()
        return resp

    async def fake_get(url, **kwargs):
        if fail:
            raise httpx.ConnectError("boom")
        return _resp(daily) if "daily" in url else _resp(clim)

    client = AsyncMock()
    client.get = AsyncMock(side_effect=fake_get)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


async def test_flags_hot_dry_region():
    import datetime as dt

    month_abbr = climate._MONTH_ABBR[dt.date.today().month - 1]
    daily = _daily_payload([30.0, 31.0, 29.0], [0.0, 0.0, 0.0])
    clim = _climatology_payload(month_abbr, normal_temp=20.0, normal_precip=5.0)

    with patch("terminalq.providers.climate.httpx.AsyncClient", return_value=_mock_client(daily, clim)):
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

    with patch("terminalq.providers.climate.httpx.AsyncClient", return_value=_mock_client(daily, clim)):
        result = await climate.get_climate_risk_watch()

    assert result["flagged_regions"] == []
    first_key = next(iter(climate.REGIONS))
    assert result["regions"][first_key]["signal"] == "normal — within typical range for this time of year"


async def test_source_failure_returns_error_not_exception():
    with patch(
        "terminalq.providers.climate.httpx.AsyncClient",
        return_value=_mock_client({}, {}, fail=True),
    ):
        result = await climate.get_climate_risk_watch()

    first_key = next(iter(climate.REGIONS))
    assert result["regions"][first_key]["error"] == "Connection failed"
    assert result["flagged_regions"] == []
