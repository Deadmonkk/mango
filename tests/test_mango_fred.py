"""Tests for terminalq.mango.fred — the FRED API client.

All HTTP is faked (httpx.AsyncClient is monkeypatched); no test may touch the
network. AAA structure throughout.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from terminalq.mango import fred


# --- helpers ---------------------------------------------------------------


def _mock_response(json_data: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}", request=MagicMock(), response=resp
        )
    return resp


def _observations_payload(rows: list[tuple[str, str]]) -> dict:
    """rows: list of (date, value) where value may be "." for missing."""
    return {"observations": [{"date": d, "value": v} for d, v in rows]}


def _metadata_payload(title: str = "Test Series", units: str = "Percent", frequency: str = "Daily") -> dict:
    return {"seriess": [{"title": title, "units": units, "frequency": frequency}]}


def _mock_client(responses_by_path: dict[str, MagicMock]) -> AsyncMock:
    """An AsyncMock httpx.AsyncClient whose .get() dispatches on URL suffix."""

    async def _get(url, **kwargs):
        for suffix, resp in responses_by_path.items():
            if url.endswith(suffix):
                return resp
        raise AssertionError(f"unexpected URL in test: {url}")

    client = AsyncMock()
    client.get = AsyncMock(side_effect=_get)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


@pytest.fixture(autouse=True)
def _set_api_key(monkeypatch):
    """Most tests need a key configured; individual tests override as needed."""
    monkeypatch.setattr(fred, "FRED_API_KEY", "test_key_1234567890")


# --- alias resolution --------------------------------------------------


def test_resolve_series_id_maps_known_alias():
    assert fred._resolve_series_id("cpi") == "CPIAUCSL"


def test_resolve_series_id_is_case_insensitive():
    assert fred._resolve_series_id("CPI") == "CPIAUCSL"
    assert fred._resolve_series_id("Cpi") == "CPIAUCSL"


def test_resolve_series_id_passes_through_raw_id_unchanged():
    assert fred._resolve_series_id("SAHMREALTIME") == "SAHMREALTIME"
    assert fred._resolve_series_id("PSAVERT") == "PSAVERT"


def test_resolve_series_id_distinguishes_gdp_and_real_gdp():
    assert fred._resolve_series_id("gdp") == "GDP"
    assert fred._resolve_series_id("real_gdp") == "GDPC1"


# --- get_series: observation parsing ------------------------------------


async def test_get_series_returns_observations_newest_first():
    # Arrange
    obs_payload = _observations_payload([("2026-08-05", "4.2"), ("2026-08-04", "4.1")])
    client = _mock_client(
        {"/series/observations": _mock_response(obs_payload), "/series": _mock_response(_metadata_payload())}
    )

    # Act
    with patch.object(fred.httpx, "AsyncClient", return_value=client):
        result = await fred.get_series("10y_yield", limit=2)

    # Assert
    assert result["observations"][0]["date"] == "2026-08-05"
    assert result["observations"][1]["date"] == "2026-08-04"


async def test_get_series_converts_string_values_to_float():
    # Arrange
    obs_payload = _observations_payload([("2026-08-05", "4.25")])
    client = _mock_client(
        {"/series/observations": _mock_response(obs_payload), "/series": _mock_response(_metadata_payload())}
    )

    # Act
    with patch.object(fred.httpx, "AsyncClient", return_value=client):
        result = await fred.get_series("10y_yield", limit=1)

    # Assert
    value = result["observations"][0]["value"]
    assert value == 4.25
    assert isinstance(value, float)


async def test_get_series_drops_missing_value_rows():
    # Arrange
    obs_payload = _observations_payload([("2026-08-05", "."), ("2026-08-04", "4.1")])
    client = _mock_client(
        {"/series/observations": _mock_response(obs_payload), "/series": _mock_response(_metadata_payload())}
    )

    # Act
    with patch.object(fred.httpx, "AsyncClient", return_value=client):
        result = await fred.get_series("10y_yield", limit=2)

    # Assert
    assert len(result["observations"]) == 1
    assert result["observations"][0]["date"] == "2026-08-04"


async def test_get_series_includes_resolved_id_and_metadata():
    # Arrange
    obs_payload = _observations_payload([("2026-08-05", "4.2")])
    client = _mock_client(
        {
            "/series/observations": _mock_response(obs_payload),
            "/series": _mock_response(_metadata_payload("10-Year Treasury Yield", "Percent", "Daily")),
        }
    )

    # Act
    with patch.object(fred.httpx, "AsyncClient", return_value=client):
        result = await fred.get_series("10y_yield", limit=1)

    # Assert
    assert result["series_id"] == "DGS10"
    assert result["title"] == "10-Year Treasury Yield"
    assert result["units"] == "Percent"
    assert result["frequency"] == "Daily"


# --- get_series: failure modes -------------------------------------------


async def test_get_series_missing_api_key_returns_error_without_network_call(monkeypatch):
    # Arrange
    monkeypatch.setattr(fred, "FRED_API_KEY", "")
    with patch.object(fred.httpx, "AsyncClient") as client_cls:
        # Act
        result = await fred.get_series("cpi", limit=5)

        # Assert
        assert "error" in result
        assert result["source"] == "fred"
        client_cls.assert_not_called()


async def test_get_series_http_error_returns_error_dict_not_raises():
    # Arrange
    client = _mock_client(
        {
            "/series/observations": _mock_response({}, status_code=404),
            "/series": _mock_response(_metadata_payload()),
        }
    )

    # Act
    with patch.object(fred.httpx, "AsyncClient", return_value=client):
        result = await fred.get_series("cpi", limit=5)

    # Assert
    assert "error" in result
    assert result["series_id"] == "CPIAUCSL"
    assert result["source"] == "fred"
    assert "404" in result["error"]


async def test_get_series_survives_metadata_failure_with_observations_intact():
    # Arrange
    obs_payload = _observations_payload([("2026-08-05", "4.2")])
    client = _mock_client(
        {
            "/series/observations": _mock_response(obs_payload),
            "/series": _mock_response({}, status_code=500),
        }
    )

    # Act
    with patch.object(fred.httpx, "AsyncClient", return_value=client):
        result = await fred.get_series("10y_yield", limit=1)

    # Assert
    assert "error" not in result
    assert len(result["observations"]) == 1
    assert result["title"] == ""
    assert result["units"] == ""
    assert result["frequency"] == ""


async def test_get_series_never_leaks_api_key_in_error_message(monkeypatch):
    # Arrange
    secret_key = "SUPER_SECRET_FRED_KEY_98765"
    monkeypatch.setenv("FRED_API_KEY", secret_key)
    monkeypatch.setattr(fred, "FRED_API_KEY", secret_key)

    async def _boom(url, **kwargs):
        raise httpx.HTTPStatusError(
            f"Client error '403 Forbidden' for url "
            f"'https://api.stlouisfed.org/fred/series/observations?series_id=CPIAUCSL&api_key={secret_key}'",
            request=MagicMock(),
            response=MagicMock(),
        )

    client = AsyncMock()
    client.get = AsyncMock(side_effect=_boom)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    # Act
    with patch.object(fred.httpx, "AsyncClient", return_value=client):
        result = await fred.get_series("cpi", limit=5)

    # Assert
    assert secret_key not in result["error"]


# --- get_economic_dashboard ------------------------------------------------


async def test_get_economic_dashboard_shape_with_one_failing_series(monkeypatch):
    # Arrange
    async def fake_get_series(alias: str, limit: int = 10) -> dict:
        if alias == "unemployment":
            return {"error": "boom", "series_id": "UNRATE", "source": "fred"}
        return {
            "series_id": fred._resolve_series_id(alias),
            "observations": [
                {"date": "2026-08-05", "value": 10.0},
                {"date": "2026-08-04", "value": 9.0},
            ],
            "title": "t",
            "units": "u",
            "frequency": "f",
        }

    monkeypatch.setattr(fred, "get_series", fake_get_series)

    # Act
    result = await fred.get_economic_dashboard()

    # Assert
    assert result["source"] == "fred"
    indicators = result["indicators"]
    assert set(indicators.keys()) == set(fred._DASHBOARD_ALIASES)
    assert indicators["unemployment"] == {"error": "boom"}
    assert indicators["cpi"]["latest_value"] == 10.0
    assert indicators["cpi"]["previous_value"] == 9.0
    assert indicators["cpi"]["change"] == 1.0


async def test_get_economic_dashboard_change_arithmetic_and_none_cases(monkeypatch):
    # Arrange
    async def fake_get_series(alias: str, limit: int = 10) -> dict:
        if alias == "gdp":
            # Only one observation -> no previous value -> change is None.
            return {
                "series_id": "GDP",
                "observations": [{"date": "2026-08-05", "value": 5.0}],
                "title": "",
                "units": "",
                "frequency": "",
            }
        if alias == "cpi":
            return {
                "series_id": "CPIAUCSL",
                "observations": [],
                "title": "",
                "units": "",
                "frequency": "",
            }
        return {
            "series_id": fred._resolve_series_id(alias),
            "observations": [
                {"date": "2026-08-05", "value": 2.5},
                {"date": "2026-08-04", "value": 2.0},
            ],
            "title": "",
            "units": "",
            "frequency": "",
        }

    monkeypatch.setattr(fred, "get_series", fake_get_series)

    # Act
    result = await fred.get_economic_dashboard()

    # Assert
    indicators = result["indicators"]
    assert indicators["gdp"]["latest_value"] == 5.0
    assert indicators["gdp"]["previous_value"] is None
    assert indicators["gdp"]["change"] is None
    assert indicators["cpi"]["latest_value"] is None
    assert indicators["cpi"]["change"] is None
    assert indicators["fed_funds"]["change"] == 0.5
