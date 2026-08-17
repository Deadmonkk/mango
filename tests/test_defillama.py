"""Tests for mango.providers.defillama — DeFi TVL via DefiLlama (free, no API key)."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from mango.providers import defillama


@pytest.fixture(autouse=True)
def clear_caches(tmp_cache_dir):
    """Ensure every test starts with empty cache."""
    pass


def _mock_response(json_data, status_code=200):
    """Create a mock httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.text = ""
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}",
            request=MagicMock(),
            response=resp,
        )
    return resp


_CHAINS_DATA = [
    {"name": "Ethereum", "tvl": 60_000_000_000, "tokenSymbol": "ETH"},
    {"name": "Solana", "tvl": 8_000_000_000, "tokenSymbol": "SOL"},
    {"name": "Tron", "tvl": 6_000_000_000, "tokenSymbol": "TRX"},
]

# 32 days of history so 1d/7d/30d windows are all available
_HISTORICAL_DATA = [{"date": 1_700_000_000 + i * 86400, "tvl": 70_000_000_000 + i * 100_000_000} for i in range(32)]


async def _mock_fetch_json(url, **kwargs):
    """Stand in for http.fetch_json — returns parsed JSON, not a response."""
    if "historicalChainTvl" in url:
        return _HISTORICAL_DATA
    if "stablecoincharts" in url:
        return _STABLECOIN_HISTORY
    if "stablecoins" in url:
        return _STABLECOIN_ASSETS
    return _CHAINS_DATA


async def test_get_defi_overview_success():
    """Returns total TVL, top chains by share, and TVL trend changes."""
    with patch("mango.providers.defillama.http.fetch_json", _mock_fetch_json):
        result = await defillama.get_defi_overview()

    assert result["source"] == "defillama"
    assert result["total_tvl_usd"] == _HISTORICAL_DATA[-1]["tvl"]

    top_chains = result["top_chains"]
    assert len(top_chains) == 3
    assert top_chains[0]["name"] == "Ethereum"
    assert top_chains[0]["tvl_usd"] == 60_000_000_000
    assert round(top_chains[0]["pct_share"], 2) == round(60_000_000_000 / 74_000_000_000 * 100, 2)

    # 1d change: last vs second-to-last
    expected_1d = (_HISTORICAL_DATA[-1]["tvl"] - _HISTORICAL_DATA[-2]["tvl"]) / _HISTORICAL_DATA[-2]["tvl"] * 100
    assert round(result["tvl_change_1d_pct"], 4) == round(expected_1d, 4)

    assert "trend_signal" in result


async def test_get_defi_overview_error():
    """Connection failure returns an error dict."""
    with patch("mango.providers.defillama.http.fetch_json",
               AsyncMock(side_effect=httpx.ConnectError("boom"))):
        result = await defillama.get_defi_overview()

    assert "error" in result
    assert result["source"] == "defillama"


async def test_get_defi_overview_cache_hit():
    """Second call uses cached result without re-fetching."""
    fetch = AsyncMock(side_effect=_mock_fetch_json)
    with patch("mango.providers.defillama.http.fetch_json", fetch):
        first = await defillama.get_defi_overview()
        second = await defillama.get_defi_overview()

    assert first == second
    # Two endpoints fetched on the first call; the second must hit the cache.
    assert fetch.call_count == 2, "second call refetched instead of using the cache"


# ---------------------------------------------------------------------------
# Stablecoin supply overview
# ---------------------------------------------------------------------------

_STABLECOIN_ASSETS = {
    "peggedAssets": [
        {"name": "Tether", "symbol": "USDT", "circulating": {"peggedUSD": 120_000_000_000}},
        {"name": "USD Coin", "symbol": "USDC", "circulating": {"peggedUSD": 40_000_000_000}},
        {"name": "Dai", "symbol": "DAI", "circulating": {"peggedUSD": 5_000_000_000}},
    ]
}

# 32 days of supply history, growing $200M/day → ~3.9% over 30d
_STABLECOIN_HISTORY = [
    {"date": str(1_700_000_000 + i * 86400), "totalCirculatingUSD": {"peggedUSD": 159_000_000_000 + i * 200_000_000}}
    for i in range(32)
]


async def _mock_stablecoin_get(url, **kwargs):
    if "stablecoincharts" in url:
        return _mock_response(_STABLECOIN_HISTORY)
    return _mock_response(_STABLECOIN_ASSETS)


async def test_get_stablecoins_overview_success():
    """Returns total supply, top stablecoins by share, and supply growth signal."""
    with patch("mango.providers.defillama.http.fetch_json", _mock_fetch_json):
        result = await defillama.get_stablecoins_overview()

    assert result["source"] == "defillama"
    assert result["total_supply_usd"] == _STABLECOIN_HISTORY[-1]["totalCirculatingUSD"]["peggedUSD"]

    top = result["top_stablecoins"]
    assert top[0]["symbol"] == "USDT"
    assert top[0]["supply_usd"] == 120_000_000_000
    assert round(top[0]["pct_share"], 1) == round(120 / 165 * 100, 1)

    # Supply growing ~3.9%/30d → expanding signal
    assert result["supply_change_30d_pct"] > 1
    assert "expanding" in result["trend_signal"]


async def test_get_stablecoins_overview_error():
    """Connection failure returns an error dict."""
    with patch("mango.providers.defillama.http.fetch_json",
               AsyncMock(side_effect=httpx.ConnectError("boom"))):
        result = await defillama.get_stablecoins_overview()

    assert "error" in result
    assert result["source"] == "defillama"
