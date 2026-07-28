"""Tests for terminalq.providers.prediction_markets — Polymarket odds."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from terminalq.providers import prediction_markets


@pytest.fixture(autouse=True)
def clear_caches(tmp_cache_dir):
    """Ensure every test starts with empty cache."""
    pass


_SEARCH = {
    "events": [
        {
            "title": "How many Fed rate cuts in 2026?",
            "volume": 500000,
            "endDate": "2026-12-31",
            "markets": [
                {
                    "question": "Will no Fed rate cuts happen in 2026?",
                    "outcomes": '["Yes","No"]',
                    "outcomePrices": '["0.7885","0.2115"]',
                    "volume": "120000",
                    "endDate": "2026-12-31",
                }
            ],
        },
        {
            "title": "Fed rate hike in 2026?",
            "markets": [
                {
                    "question": "Fed rate hike in 2026?",
                    "outcomes": '["Yes","No"]',
                    "outcomePrices": '["0.48","0.52"]',
                    "volume": "300000",
                    "endDate": "2026-12-31",
                }
            ],
        },
    ]
}


def _mock_client(payload=_SEARCH, fail=False):
    def _resp():
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.json = MagicMock(return_value=payload)
        resp.raise_for_status = MagicMock()
        return resp

    async def fake_get(url, **kwargs):
        if fail:
            raise httpx.ConnectError("boom")
        return _resp()

    client = AsyncMock()
    client.get = AsyncMock(side_effect=fake_get)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


async def test_prediction_markets_parses_probabilities():
    with patch(
        "terminalq.providers.prediction_markets.httpx.AsyncClient",
        return_value=_mock_client(),
    ):
        result = await prediction_markets.get_prediction_markets("Fed rate")

    assert result["source"] == "Polymarket (Gamma public-search)"
    assert result["topic"] == "Fed rate"
    assert len(result["markets"]) == 2
    # Sorted by volume desc -> the 300k hike market leads
    top = result["markets"][0]
    assert top["implied_probability_pct"] == 48.0
    assert top["volume_usd"] == 300000
    # The 0.7885 market is present (78.85 -> 78.8 after rounding the float)
    probs = {m["implied_probability_pct"] for m in result["markets"]}
    assert 78.8 in probs


async def test_prediction_markets_skips_unpriced():
    payload = {"events": [{"title": "x", "markets": [{"question": "q", "outcomePrices": None}]}]}
    with patch(
        "terminalq.providers.prediction_markets.httpx.AsyncClient",
        return_value=_mock_client(payload=payload),
    ):
        result = await prediction_markets.get_prediction_markets("x")

    assert result["markets"] == []
    assert "No active" in result["signal"]


async def test_prediction_markets_failure_returns_error():
    with patch(
        "terminalq.providers.prediction_markets.httpx.AsyncClient",
        return_value=_mock_client(fail=True),
    ):
        result = await prediction_markets.get_prediction_markets("Fed rate")

    assert "error" in result
    assert result["source"] == "Polymarket"


def test_parse_yes_probability_handles_bad_data():
    assert prediction_markets._parse_yes_probability({"outcomePrices": "not json"}) is None
    assert prediction_markets._parse_yes_probability({}) is None
    assert prediction_markets._parse_yes_probability({"outcomePrices": '["0.33","0.67"]'}) == 33.0
