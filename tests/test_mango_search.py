"""Tests for mango.providers.search — the web-search provider.

All network access is faked: `_fetch_duckduckgo` and the Brave HTTP client
are monkeypatched directly, so no test ever imports the real `ddgs` package
or touches the network. AAA structure throughout.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from mango.providers import search


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


def _mock_client(response: MagicMock) -> AsyncMock:
    client = AsyncMock()
    client.get = AsyncMock(return_value=response)
    # core/http.fetch_json issues requests via .request(); wire it to the same
    # canned response so this test still drives the real provider code path.
    client.request = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


@pytest.fixture(autouse=True)
def _no_brave_key(monkeypatch):
    """Most tests exercise the keyless default; individual tests opt into Brave."""
    monkeypatch.setattr(search, "BRAVE_API_KEY", "")


# --- DuckDuckGo (keyless default) ------------------------------------------


@pytest.mark.asyncio
async def test_web_search_shape_matches_the_fixed_contract(monkeypatch):
    # Arrange
    raw_ddg_results = [
        {"title": "GSCPI - NY Fed", "href": "https://newyorkfed.org/gscpi", "body": "Supply chain index."},
    ]
    monkeypatch.setattr(search, "_fetch_duckduckgo", lambda query, count: raw_ddg_results)

    # Act
    result = await search.web_search("GSCPI latest value", count=5)

    # Assert
    assert set(result.keys()) == {"query", "results", "total_results", "news", "note", "source"}
    assert result["query"] == "GSCPI latest value"
    assert result["source"] == "duckduckgo"
    assert result["total_results"] == 1
    assert result["news"] == []
    assert set(result["results"][0].keys()) == {"title", "url", "description", "age"}
    assert result["results"][0]["title"] == "GSCPI - NY Fed"
    assert result["results"][0]["url"] == "https://newyorkfed.org/gscpi"
    assert result["results"][0]["description"] == "Supply chain index."


@pytest.mark.asyncio
async def test_web_search_no_results_returns_empty_list(monkeypatch):
    # Arrange
    monkeypatch.setattr(search, "_fetch_duckduckgo", lambda query, count: [])

    # Act
    result = await search.web_search("a query with truly nothing found")

    # Assert
    assert result["results"] == []
    assert result["total_results"] == 0
    assert "error" not in result


@pytest.mark.asyncio
async def test_web_search_duckduckgo_exception_returns_error_dict_not_raise(monkeypatch):
    # Arrange
    def _boom(query, count):
        raise RuntimeError("ddgs backend unavailable")

    monkeypatch.setattr(search, "_fetch_duckduckgo", _boom)

    # Act
    result = await search.web_search("anything")

    # Assert
    assert "error" in result
    assert result["query"] == "anything"
    assert result["source"] == "duckduckgo"


@pytest.mark.asyncio
async def test_web_search_rejects_empty_query_without_raising():
    # Act
    result = await search.web_search("")

    # Assert
    assert "error" in result
    assert result["query"] == ""


# --- Brave preferred, DuckDuckGo fallback -----------------------------------


@pytest.mark.asyncio
async def test_web_search_prefers_brave_when_key_is_configured(monkeypatch):
    # Arrange
    monkeypatch.setattr(search, "BRAVE_API_KEY", "brave_test_key_1234567890")
    brave_payload = {
        "web": {"results": [{"title": "Brave result", "url": "https://brave.example", "description": "d", "age": "1d"}]},
        "news": {"results": [{"title": "Brave news", "url": "https://brave.example/news", "description": "n", "age": "2h"}]},
    }
    mock_client = _mock_client(_mock_response(brave_payload))
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: mock_client)
    ddg_called = False

    def _ddg_should_not_run(query, count):
        nonlocal ddg_called
        ddg_called = True
        return []

    monkeypatch.setattr(search, "_fetch_duckduckgo", _ddg_should_not_run)

    # Act
    result = await search.web_search("fed rate cut odds")

    # Assert
    assert result["source"] == "brave"
    assert result["results"][0]["title"] == "Brave result"
    assert result["news"][0]["title"] == "Brave news"
    assert ddg_called is False


@pytest.mark.asyncio
async def test_web_search_falls_back_to_duckduckgo_when_brave_fails(monkeypatch):
    # Arrange
    monkeypatch.setattr(search, "BRAVE_API_KEY", "brave_test_key_1234567890")
    mock_client = _mock_client(_mock_response({}, status_code=403))
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: mock_client)
    monkeypatch.setattr(
        search,
        "_fetch_duckduckgo",
        lambda query, count: [{"title": "Fallback result", "href": "https://ddg.example", "body": "b"}],
    )

    # Act
    result = await search.web_search("fed rate cut odds")

    # Assert
    assert result["source"] == "duckduckgo"
    assert result["results"][0]["title"] == "Fallback result"


@pytest.mark.asyncio
async def test_web_search_falls_back_to_duckduckgo_on_brave_network_error(monkeypatch):
    # Arrange
    monkeypatch.setattr(search, "BRAVE_API_KEY", "brave_test_key_1234567890")

    async def _get(*a, **kw):
        raise httpx.ConnectError("connection refused")

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=_get)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: mock_client)
    monkeypatch.setattr(
        search, "_fetch_duckduckgo", lambda query, count: [{"title": "T", "href": "https://x", "body": "b"}]
    )

    # Act
    result = await search.web_search("anything")

    # Assert
    assert result["source"] == "duckduckgo"
    assert "error" not in result
