"""Tests for trending price fallback — embedded data when the markets call fails."""

from unittest.mock import AsyncMock, patch

import pytest

from terminalq.providers import coingecko


@pytest.fixture(autouse=True)
def clear_caches(tmp_cache_dir):
    """Ensure every test starts with empty cache."""
    pass


_TRENDING_PAYLOAD = {
    "coins": [
        {
            "item": {
                "id": "zcash",
                "name": "Zcash",
                "symbol": "zec",
                "market_cap_rank": 15,
                "data": {
                    "price": 13.39,
                    "price_change_percentage_24h": {"usd": -14.16},
                    "market_cap": "$220,000,000",
                },
            }
        }
    ]
}

_MARKETS_PAYLOAD = [
    {
        "id": "zcash",
        "current_price": 13.42,
        "price_change_percentage_24h": -14.0,
        "price_change_percentage_7d_in_currency": -20.0,
        "market_cap": 221000000,
    }
]


async def test_trending_uses_markets_data_when_available():
    with patch.object(coingecko, "_fetch", AsyncMock(side_effect=[_TRENDING_PAYLOAD, _MARKETS_PAYLOAD])):
        result = await coingecko.get_crypto_trending()

    coin = result["trending_coins"][0]
    assert coin["price_usd"] == 13.42
    assert coin["change_7d_pct"] == -20.0


async def test_trending_falls_back_to_embedded_prices_on_markets_failure():
    """A 429 on the secondary markets call must not blank out prices — the
    trending payload itself carries price, 24h change, and market cap."""
    with patch.object(coingecko, "_fetch", AsyncMock(side_effect=[_TRENDING_PAYLOAD, {"_error": "HTTP 429"}])):
        result = await coingecko.get_crypto_trending()

    coin = result["trending_coins"][0]
    assert coin["price_usd"] == 13.39
    assert coin["change_24h_pct"] == -14.16
    assert coin["market_cap_usd"] == 220000000.0
    assert coin["change_7d_pct"] is None  # not in embedded data — honest null
