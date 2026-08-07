"""Tests for mango.providers.finnhub — the Finnhub market-data client.

All HTTP is faked (httpx.AsyncClient is monkeypatched); no test may touch
the network. AAA structure throughout.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from mango.providers import finnhub


# --- helpers ---------------------------------------------------------------


def _mock_response(json_data, status_code: int = 200) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}", request=MagicMock(), response=resp
        )
    return resp


def _mock_client(responses_by_path: dict) -> AsyncMock:
    """An AsyncMock httpx.AsyncClient whose .get() dispatches on URL suffix (path only)."""

    async def _get(url, **kwargs):
        for suffix, resp in responses_by_path.items():
            if suffix in url:
                return resp
        raise AssertionError(f"unexpected URL in test: {url}")

    client = AsyncMock()
    client.get = AsyncMock(side_effect=_get)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


def _quote_payload(c=227.5, d=1.2, dp=0.53, h=228.1, l=225.9, o=226.0, pc=226.3) -> dict:
    return {"c": c, "d": d, "dp": dp, "h": h, "l": l, "o": o, "pc": pc, "t": 1723046400}


@pytest.fixture(autouse=True)
def _set_api_key(monkeypatch):
    """Most tests need a key configured; individual tests override as needed."""
    monkeypatch.setattr(finnhub, "FINNHUB_API_KEY", "test_key_1234567890")


@pytest.fixture(autouse=True)
def _no_real_cache(tmp_path, monkeypatch):
    """Point the shared file cache at a throwaway directory per test."""
    from mango.core import cache

    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)


# --- get_quote --------------------------------------------------------------


async def test_get_quote_returns_shaped_fields_on_success():
    # Arrange
    client = _mock_client({"/quote": _mock_response(_quote_payload())})

    # Act
    with patch.object(finnhub.httpx, "AsyncClient", return_value=client):
        result = await finnhub.get_quote("AAPL")

    # Assert
    assert result == {
        "symbol": "AAPL",
        "current_price": 227.5,
        "change": 1.2,
        "percent_change": 0.53,
        "high": 228.1,
        "low": 225.9,
        "open": 226.0,
        "previous_close": 226.3,
        "source": "finnhub",
    }


async def test_get_quote_missing_api_key_returns_error_without_network_call(monkeypatch):
    # Arrange
    monkeypatch.setattr(finnhub, "FINNHUB_API_KEY", "")
    with patch.object(finnhub.httpx, "AsyncClient") as client_cls:
        # Act
        result = await finnhub.get_quote("AAPL")

        # Assert
        assert "error" in result
        assert result["symbol"] == "AAPL"
        assert result["source"] == "finnhub"
        client_cls.assert_not_called()


async def test_get_quote_http_error_returns_error_dict_not_raises():
    # Arrange
    client = _mock_client({"/quote": _mock_response({}, status_code=500)})

    # Act
    with patch.object(finnhub.httpx, "AsyncClient", return_value=client):
        result = await finnhub.get_quote("AAPL")

    # Assert
    assert "error" in result
    assert result["symbol"] == "AAPL"
    assert result["source"] == "finnhub"
    assert "500" in result["error"]


async def test_get_quote_never_leaks_api_key_in_error_message(monkeypatch):
    # Arrange
    secret_key = "SUPER_SECRET_FINNHUB_KEY_98765"
    monkeypatch.setenv("FINNHUB_API_KEY", secret_key)
    monkeypatch.setattr(finnhub, "FINNHUB_API_KEY", secret_key)

    async def _boom(url, **kwargs):
        raise httpx.HTTPStatusError(
            f"Client error '403 Forbidden' for url "
            f"'https://finnhub.io/api/v1/quote?symbol=AAPL&token={secret_key}'",
            request=MagicMock(),
            response=MagicMock(),
        )

    client = AsyncMock()
    client.get = AsyncMock(side_effect=_boom)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    # Act
    with patch.object(finnhub.httpx, "AsyncClient", return_value=client):
        result = await finnhub.get_quote("AAPL")

    # Assert
    assert secret_key not in result["error"]


# --- get_quotes_batch --------------------------------------------------


async def test_get_quotes_batch_returns_per_item_errors_while_healthy_legs_succeed():
    # Arrange
    async def fake_get_quote(symbol: str) -> dict:
        if symbol == "BADSYM":
            return {"error": "boom", "symbol": symbol, "source": "finnhub"}
        return {
            "symbol": symbol,
            "current_price": 100.0,
            "change": 1.0,
            "percent_change": 1.0,
            "high": 101.0,
            "low": 99.0,
            "open": 99.5,
            "previous_close": 99.0,
            "source": "finnhub",
        }

    with patch.object(finnhub, "get_quote", fake_get_quote):
        # Act
        results = await finnhub.get_quotes_batch(["AAPL", "BADSYM", "MSFT"])

    # Assert
    assert len(results) == 3
    assert results[0]["symbol"] == "AAPL" and "error" not in results[0]
    assert results[1]["symbol"] == "BADSYM" and "error" in results[1]
    assert results[2]["symbol"] == "MSFT" and "error" not in results[2]


async def test_get_quotes_batch_results_always_carry_symbol_even_on_raised_exception():
    # Arrange
    async def fake_get_quote(symbol: str) -> dict:
        if symbol == "CRASH":
            raise RuntimeError("unexpected failure")
        return {"symbol": symbol, "source": "finnhub"}

    with patch.object(finnhub, "get_quote", fake_get_quote):
        # Act
        results = await finnhub.get_quotes_batch(["AAPL", "CRASH"])

    # Assert
    assert all("symbol" in r for r in results)
    crash_result = next(r for r in results if r["symbol"] == "CRASH")
    assert "error" in crash_result


# --- get_company_profile -------------------------------------------------


async def test_get_company_profile_returns_shaped_fields_on_success():
    # Arrange
    payload = {
        "name": "Apple Inc",
        "country": "US",
        "currency": "USD",
        "exchange": "NASDAQ NMS - GLOBAL MARKET",
        "finnhubIndustry": "Technology",
        "ipo": "1980-12-12",
        "marketCapitalization": 3500000.0,
        "shareOutstanding": 15000.0,
        "weburl": "https://www.apple.com/",
        "logo": "https://example.test/logo.png",
    }
    client = _mock_client({"/stock/profile2": _mock_response(payload)})

    # Act
    with patch.object(finnhub.httpx, "AsyncClient", return_value=client):
        result = await finnhub.get_company_profile("AAPL")

    # Assert
    assert result["symbol"] == "AAPL"
    assert result["name"] == "Apple Inc"
    assert result["industry"] == "Technology"
    assert result["market_cap"] == 3500000.0
    assert result["source"] == "finnhub"


async def test_get_company_profile_empty_payload_returns_error_not_blank_profile():
    # Arrange — Finnhub returns {} for an unrecognized symbol, not an HTTP error.
    client = _mock_client({"/stock/profile2": _mock_response({})})

    # Act
    with patch.object(finnhub.httpx, "AsyncClient", return_value=client):
        result = await finnhub.get_company_profile("NOTREAL")

    # Assert
    assert "error" in result
    assert result["symbol"] == "NOTREAL"


async def test_get_company_profile_missing_api_key_returns_error_without_network_call(monkeypatch):
    # Arrange
    monkeypatch.setattr(finnhub, "FINNHUB_API_KEY", "")
    with patch.object(finnhub.httpx, "AsyncClient") as client_cls:
        # Act
        result = await finnhub.get_company_profile("AAPL")

        # Assert
        assert "error" in result
        client_cls.assert_not_called()


# --- get_company_news ----------------------------------------------------


async def test_get_company_news_returns_shaped_articles_on_success():
    # Arrange
    payload = [
        {
            "headline": "Apple beats estimates",
            "summary": "Q3 results...",
            "source": "Reuters",
            "url": "https://example.test/a",
            "datetime": 1723046400,
            "category": "company",
        }
    ]
    client = _mock_client({"/company-news": _mock_response(payload)})

    # Act
    with patch.object(finnhub.httpx, "AsyncClient", return_value=client):
        result = await finnhub.get_company_news("AAPL", days=7)

    # Assert
    assert result["symbol"] == "AAPL"
    assert result["count"] == 1
    assert result["articles"][0]["headline"] == "Apple beats estimates"
    assert result["articles"][0]["datetime"] is not None
    assert result["source"] == "finnhub"


async def test_get_company_news_http_error_returns_error_dict():
    # Arrange
    client = _mock_client({"/company-news": _mock_response({}, status_code=500)})

    # Act
    with patch.object(finnhub.httpx, "AsyncClient", return_value=client):
        result = await finnhub.get_company_news("AAPL")

    # Assert
    assert "error" in result
    assert result["symbol"] == "AAPL"


# --- get_earnings --------------------------------------------------------


async def test_get_earnings_returns_shaped_entries_on_success():
    # Arrange
    payload = [
        {
            "period": "2026-06-27",
            "actual": 1.4,
            "estimate": 1.35,
            "surprise": 0.05,
            "surprisePercent": 3.7,
        }
    ]
    client = _mock_client({"/stock/earnings": _mock_response(payload)})

    # Act
    with patch.object(finnhub.httpx, "AsyncClient", return_value=client):
        result = await finnhub.get_earnings("AAPL")

    # Assert
    assert result["symbol"] == "AAPL"
    assert result["earnings"][0]["surprise_percent"] == 3.7
    assert result["source"] == "finnhub"


async def test_get_earnings_missing_api_key_returns_error_without_network_call(monkeypatch):
    # Arrange
    monkeypatch.setattr(finnhub, "FINNHUB_API_KEY", "")
    with patch.object(finnhub.httpx, "AsyncClient") as client_cls:
        # Act
        result = await finnhub.get_earnings("AAPL")

        # Assert
        assert "error" in result
        assert result["symbol"] == "AAPL"
        client_cls.assert_not_called()


# --- get_analyst_ratings --------------------------------------------------


async def test_get_analyst_ratings_returns_shaped_entries_on_success():
    # Arrange
    payload = [
        {
            "period": "2026-08-01",
            "strongBuy": 12,
            "buy": 18,
            "hold": 5,
            "sell": 1,
            "strongSell": 0,
        }
    ]
    client = _mock_client({"/stock/recommendation": _mock_response(payload)})

    # Act
    with patch.object(finnhub.httpx, "AsyncClient", return_value=client):
        result = await finnhub.get_analyst_ratings("AAPL")

    # Assert
    assert result["symbol"] == "AAPL"
    assert result["ratings"][0]["strong_buy"] == 12
    assert result["ratings"][0]["strong_sell"] == 0
    assert result["source"] == "finnhub"


async def test_get_analyst_ratings_http_error_returns_error_dict():
    # Arrange
    client = _mock_client({"/stock/recommendation": _mock_response({}, status_code=404)})

    # Act
    with patch.object(finnhub.httpx, "AsyncClient", return_value=client):
        result = await finnhub.get_analyst_ratings("AAPL")

    # Assert
    assert "error" in result
    assert result["symbol"] == "AAPL"


# --- get_economic_calendar -------------------------------------------------


async def test_get_economic_calendar_returns_shaped_events_on_success():
    # Arrange
    payload = {
        "economicCalendar": [
            {
                "time": "2026-08-07",
                "country": "US",
                "event": "Jobs Report",
                "impact": "high",
                "actual": None,
                "estimate": 4.2,
                "prev": 4.1,
                "unit": "%",
            }
        ]
    }
    client = _mock_client({"/calendar/economic": _mock_response(payload)})

    # Act
    with patch.object(finnhub.httpx, "AsyncClient", return_value=client):
        result = await finnhub.get_economic_calendar(days=7)

    # Assert
    assert result["count"] == 1
    assert result["events"][0]["event"] == "Jobs Report"
    assert result["source"] == "finnhub"


async def test_get_economic_calendar_403_returns_error_dict_and_is_not_retried():
    # Arrange
    call_count = 0

    async def _get(url, **kwargs):
        nonlocal call_count
        call_count += 1
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 403
        resp.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError("403 Forbidden", request=MagicMock(), response=resp)
        )
        return resp

    client = AsyncMock()
    client.get = AsyncMock(side_effect=_get)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    # Act
    with patch.object(finnhub.httpx, "AsyncClient", return_value=client):
        result = await finnhub.get_economic_calendar(days=7)

    # Assert
    assert "error" in result
    assert "premium" in result["error"].lower()
    assert call_count == 1  # exactly one attempt: a 403 is not retried


async def test_get_economic_calendar_missing_api_key_returns_error_without_network_call(monkeypatch):
    # Arrange
    monkeypatch.setattr(finnhub, "FINNHUB_API_KEY", "")
    with patch.object(finnhub.httpx, "AsyncClient") as client_cls:
        # Act
        result = await finnhub.get_economic_calendar()

        # Assert
        assert "error" in result
        client_cls.assert_not_called()
