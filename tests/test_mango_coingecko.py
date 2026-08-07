"""Tests for terminalq.mango.coingecko — the CoinGecko API helper.

All HTTP is faked (httpx.AsyncClient / responses are mocked); no test may
touch the network. AAA structure throughout.
"""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from terminalq.mango import coingecko


# --- helpers -----------------------------------------------------------


def _mock_response(json_data: dict | None = None, status_code: int = 200, headers: dict | None = None) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.headers = headers or {}
    if json_data is not None:
        resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    if status_code >= 400 and status_code != coingecko.HTTP_TOO_MANY_REQUESTS:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}", request=MagicMock(), response=resp
        )
    return resp


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    """Redirect the shared file cache to a throwaway dir for every test."""
    from terminalq.mango import cache

    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)


@pytest.fixture(autouse=True)
def _fast_sleep(monkeypatch):
    """Retry/backoff tests would otherwise really sleep; make it instant."""
    monkeypatch.setattr(coingecko.asyncio, "sleep", AsyncMock())


@pytest.fixture(autouse=True)
def _fresh_limiter(monkeypatch):
    """A brand-new limiter per test so acquire() never blocks on prior tests."""
    monkeypatch.setattr(coingecko, "_limiter", coingecko.RateLimiter(coingecko.COINGECKO_RATE_LIMIT_PER_MINUTE))


# --- _resolve_id ---------------------------------------------------------


def test_resolve_id_maps_xrp_to_ripple():
    assert coingecko._resolve_id("XRP") == "ripple"


def test_resolve_id_maps_avax_to_avalanche_2():
    assert coingecko._resolve_id("AVAX") == "avalanche-2"


def test_resolve_id_maps_matic_to_matic_network():
    assert coingecko._resolve_id("MATIC") == "matic-network"


def test_resolve_id_is_case_insensitive():
    assert coingecko._resolve_id("btc") == "bitcoin"
    assert coingecko._resolve_id("Btc") == "bitcoin"
    assert coingecko._resolve_id("BTC") == "bitcoin"


def test_resolve_id_unknown_symbol_falls_through_to_lowercase():
    assert coingecko._resolve_id("FOOBAR") == "foobar"


# --- _fetch: success -----------------------------------------------------


async def test_fetch_returns_parsed_json_on_success():
    # Arrange
    payload = {"bitcoin": {"usd": 65000}}
    client = AsyncMock()
    client.get = AsyncMock(return_value=_mock_response(payload))

    # Act
    result = await coingecko._fetch(client, f"{coingecko.BASE_URL}/simple/price", {"ids": "bitcoin"})

    # Assert
    assert result == payload


async def test_fetch_success_is_cached():
    # Arrange
    payload = {"bitcoin": {"usd": 65000}}
    client = AsyncMock()
    client.get = AsyncMock(return_value=_mock_response(payload))
    url, params = f"{coingecko.BASE_URL}/simple/price", {"ids": "bitcoin"}

    # Act
    await coingecko._fetch(client, url, params)
    await coingecko._fetch(client, url, params)

    # Assert: second call served from cache, only one real HTTP GET made
    assert client.get.await_count == 1


# --- _fetch: the "_error" key spelling ------------------------------------


async def test_fetch_error_key_is_spelled_with_leading_underscore():
    """Locks the exact key spelling the consumer depends on for its fallback
    branch. A well-meaning rename to "error" would silently break the
    Yahoo-fallback path in crypto_analytics.py without failing loudly."""
    # Arrange
    client = AsyncMock()
    client.get = AsyncMock(side_effect=httpx.ConnectError("boom"))

    # Act
    result = await coingecko._fetch(client, f"{coingecko.BASE_URL}/simple/price", {})

    # Assert
    assert "_error" in result
    assert set(result.keys()) == {"_error"}  # exactly "_error", not a plain "error" key


async def test_fetch_error_response_is_not_cached():
    # Arrange
    client = AsyncMock()
    client.get = AsyncMock(return_value=_mock_response(status_code=500))
    url, params = f"{coingecko.BASE_URL}/simple/price", {"ids": "bitcoin"}

    # Act
    first = await coingecko._fetch(client, url, params)

    # A second call must hit the network again since the error was not cached.
    client.get = AsyncMock(return_value=_mock_response({"ok": True}))
    second = await coingecko._fetch(client, url, params)

    # Assert
    assert "_error" in first
    assert second == {"ok": True}
    assert client.get.await_count == 1


# --- _fetch: 429 retry behaviour -------------------------------------------


async def test_fetch_retries_after_429_then_succeeds():
    # Arrange
    payload = {"bitcoin": {"usd": 65000}}
    responses = [_mock_response(status_code=coingecko.HTTP_TOO_MANY_REQUESTS), _mock_response(payload)]
    client = AsyncMock()
    client.get = AsyncMock(side_effect=responses)

    # Act
    result = await coingecko._fetch(client, f"{coingecko.BASE_URL}/simple/price", {"ids": "bitcoin"})

    # Assert
    assert result == payload
    assert client.get.await_count == 2


async def test_fetch_exhausts_retries_on_persistent_429_and_returns_error():
    # Arrange
    client = AsyncMock()
    client.get = AsyncMock(return_value=_mock_response(status_code=coingecko.HTTP_TOO_MANY_REQUESTS))

    # Act
    result = await coingecko._fetch(client, f"{coingecko.BASE_URL}/simple/price", {"ids": "bitcoin"})

    # Assert
    assert "_error" in result
    assert client.get.await_count == coingecko.MAX_RETRY_ATTEMPTS


async def test_fetch_honours_retry_after_header(monkeypatch):
    # Arrange
    sleep_mock = AsyncMock()
    monkeypatch.setattr(coingecko.asyncio, "sleep", sleep_mock)
    responses = [
        _mock_response(status_code=coingecko.HTTP_TOO_MANY_REQUESTS, headers={"Retry-After": "3"}),
        _mock_response({"ok": True}),
    ]
    client = AsyncMock()
    client.get = AsyncMock(side_effect=responses)

    # Act
    result = await coingecko._fetch(client, f"{coingecko.BASE_URL}/simple/price", {})

    # Assert
    assert result == {"ok": True}
    sleep_mock.assert_awaited_once_with(3.0)


# --- _fetch: other failure modes -------------------------------------------


async def test_fetch_non_429_http_error_returns_error_dict():
    # Arrange
    client = AsyncMock()
    client.get = AsyncMock(return_value=_mock_response(status_code=404))

    # Act
    result = await coingecko._fetch(client, f"{coingecko.BASE_URL}/coins/nonexistent", {})

    # Assert
    assert "_error" in result
    assert "404" in result["_error"]


async def test_fetch_timeout_returns_error_dict_not_raises():
    # Arrange
    client = AsyncMock()
    client.get = AsyncMock(side_effect=httpx.ConnectTimeout("timed out"))

    # Act
    result = await coingecko._fetch(client, f"{coingecko.BASE_URL}/simple/price", {})

    # Assert: no exception propagated, an _error dict came back instead
    assert "_error" in result


async def test_fetch_connection_error_returns_error_dict_not_raises():
    # Arrange
    client = AsyncMock()
    client.get = AsyncMock(side_effect=httpx.ConnectError("connection refused"))

    # Act
    result = await coingecko._fetch(client, f"{coingecko.BASE_URL}/simple/price", {})

    # Assert
    assert "_error" in result
