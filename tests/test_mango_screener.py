"""Tests for mango.providers.screener — the S&P 500 stock screener.

All network access is faked: `get_sp500_constituents` and
`finnhub.get_company_profile` are monkeypatched directly, so no test ever
touches Wikipedia, Finnhub, or the network. AAA structure throughout.
"""

from __future__ import annotations

import pytest

from mango.providers import finnhub, screener


# --- helpers ---------------------------------------------------------------


def _async_return(value):
    async def _fn(*args, **kwargs):
        return value

    return _fn


def _constituents(rows: list[dict]) -> dict:
    return {"constituents": rows, "count": len(rows), "source": screener.SOURCE}


_SAMPLE_ROWS = [
    {"symbol": "AAA", "name": "Alpha Corp", "sector": "Technology"},
    {"symbol": "BBB", "name": "Beta Inc", "sector": "Technology"},
    {"symbol": "CCC", "name": "Gamma Co", "sector": "Health Care"},
    {"symbol": "DDD", "name": "Delta Ltd", "sector": "Energy"},
]

_PROFILES_BY_SYMBOL = {
    "AAA": {"symbol": "AAA", "market_cap": 500_000.0, "source": "finnhub"},
    "BBB": {"symbol": "BBB", "market_cap": 50_000.0, "source": "finnhub"},
    "CCC": {"symbol": "CCC", "market_cap": 2_000.0, "source": "finnhub"},
    "DDD": {"symbol": "DDD", "market_cap": 150_000.0, "source": "finnhub"},
}


async def _fake_get_company_profile(symbol: str) -> dict:
    return _PROFILES_BY_SYMBOL.get(symbol, {"error": "not found", "symbol": symbol, "source": "finnhub"})


@pytest.fixture(autouse=True)
def _finnhub_key_configured(monkeypatch):
    monkeypatch.setattr(finnhub, "FINNHUB_API_KEY", "finnhub_test_key_1234567890")


# --- sector filtering --------------------------------------------------


@pytest.mark.asyncio
async def test_filters_by_sector_case_insensitively(monkeypatch):
    # Arrange
    monkeypatch.setattr(screener, "get_sp500_constituents", _async_return(_constituents(_SAMPLE_ROWS)))

    # Act
    result = await screener.screen_stocks(sector="technology")

    # Assert
    symbols = {r["symbol"] for r in result["results"]}
    assert symbols == {"AAA", "BBB"}
    assert result["criteria"]["sector"] == "technology"
    assert result["is_complete"] is True


@pytest.mark.asyncio
async def test_no_sector_filter_returns_all_constituents_up_to_limit(monkeypatch):
    # Arrange
    monkeypatch.setattr(screener, "get_sp500_constituents", _async_return(_constituents(_SAMPLE_ROWS)))

    # Act
    result = await screener.screen_stocks(limit=2)

    # Assert
    assert len(result["results"]) == 2
    assert result["count"] == 2
    assert result["is_complete"] is True  # limit truncation is not a fetch-budget truncation


# --- market-cap filtering ------------------------------------------------


@pytest.mark.asyncio
async def test_filters_by_market_cap_bounds(monkeypatch):
    # Arrange: market caps are 500000/50000/2000/150000 (millions USD).
    monkeypatch.setattr(screener, "get_sp500_constituents", _async_return(_constituents(_SAMPLE_ROWS)))
    monkeypatch.setattr(finnhub, "get_company_profile", _fake_get_company_profile)

    # Act: bracket that only DDD (150000) qualifies.
    result = await screener.screen_stocks(min_market_cap=100_000, max_market_cap=200_000)

    # Assert
    symbols = [r["symbol"] for r in result["results"]]
    assert symbols == ["DDD"]
    assert result["results"][0]["market_cap"] == 150_000.0
    assert result["is_complete"] is True


@pytest.mark.asyncio
async def test_min_market_cap_alone_excludes_smaller_names(monkeypatch):
    # Arrange
    monkeypatch.setattr(screener, "get_sp500_constituents", _async_return(_constituents(_SAMPLE_ROWS)))
    monkeypatch.setattr(finnhub, "get_company_profile", _fake_get_company_profile)

    # Act
    result = await screener.screen_stocks(min_market_cap=100_000)

    # Assert
    symbols = {r["symbol"] for r in result["results"]}
    assert symbols == {"AAA", "DDD"}


@pytest.mark.asyncio
async def test_market_cap_filter_without_finnhub_key_returns_error_dict(monkeypatch):
    # Arrange
    monkeypatch.setattr(screener, "get_sp500_constituents", _async_return(_constituents(_SAMPLE_ROWS)))
    monkeypatch.setattr(finnhub, "FINNHUB_API_KEY", "")

    # Act
    result = await screener.screen_stocks(min_market_cap=1_000)

    # Assert
    assert "error" in result
    assert result["source"] == screener.SOURCE


# --- is_complete: fetch-budget truncation vs filter truncation -----------


@pytest.mark.asyncio
async def test_is_complete_false_when_fetch_budget_truncates_candidates(monkeypatch):
    # Arrange: 4 sector-filtered candidates, but the fetch budget only allows 2.
    monkeypatch.setattr(screener, "get_sp500_constituents", _async_return(_constituents(_SAMPLE_ROWS)))
    monkeypatch.setattr(finnhub, "get_company_profile", _fake_get_company_profile)
    monkeypatch.setattr(screener, "SCREENER_FETCH_BUDGET", 2)

    # Act
    result = await screener.screen_stocks(min_market_cap=1)

    # Assert
    assert result["is_complete"] is False


@pytest.mark.asyncio
async def test_is_complete_true_when_all_candidates_were_enriched(monkeypatch):
    # Arrange: fetch budget comfortably covers every sector-filtered candidate.
    monkeypatch.setattr(screener, "get_sp500_constituents", _async_return(_constituents(_SAMPLE_ROWS)))
    monkeypatch.setattr(finnhub, "get_company_profile", _fake_get_company_profile)
    monkeypatch.setattr(screener, "SCREENER_FETCH_BUDGET", 55)

    # Act
    result = await screener.screen_stocks(min_market_cap=1)

    # Assert
    assert result["is_complete"] is True


# --- constituent-source failure -----------------------------------------


@pytest.mark.asyncio
async def test_constituent_source_failure_returns_error_dict(monkeypatch):
    # Arrange
    monkeypatch.setattr(
        screener,
        "get_sp500_constituents",
        _async_return({"error": "could not parse S&P 500 constituent table", "source": screener.SOURCE}),
    )

    # Act
    result = await screener.screen_stocks(sector="Technology")

    # Assert
    assert "error" in result
    assert result["source"] == screener.SOURCE


@pytest.mark.asyncio
async def test_get_sp500_constituents_parses_table_by_header_name(monkeypatch):
    # Arrange: a minimal, realistic Wikipedia-shaped table with columns in a
    # different order than the parser's own field order, proving header-name
    # lookup (not positional indexing) drives the parse.
    html = """
    <table>
      <tr><th>GICS Sector</th><th>Symbol</th><th>Security</th></tr>
      <tr><td>Technology</td><td>AAA</td><td>Alpha Corp</td></tr>
      <tr><td>Energy</td><td>DDD</td><td>Delta Ltd</td></tr>
    </table>
    """

    async def _fake_fetch_html():
        return html

    monkeypatch.setattr(screener, "_fetch_constituents_html", _fake_fetch_html)

    # Act
    result = await screener.get_sp500_constituents()

    # Assert
    assert result["count"] == 2
    assert result["constituents"][0] == {"symbol": "AAA", "name": "Alpha Corp", "sector": "Technology"}


@pytest.mark.asyncio
async def test_get_sp500_constituents_returns_error_on_unparseable_layout(monkeypatch):
    # Arrange: a table missing the expected headers entirely.
    html = "<table><tr><th>Unrelated</th></tr><tr><td>x</td></tr></table>"

    async def _fake_fetch_html():
        return html

    monkeypatch.setattr(screener, "_fetch_constituents_html", _fake_fetch_html)

    # Act
    result = await screener.get_sp500_constituents()

    # Assert
    assert "error" in result
    assert result["source"] == screener.SOURCE
