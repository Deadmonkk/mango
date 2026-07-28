"""Tests for the Hyperliquid derivatives fallback (single-venue) and its wiring
into CoinGecko's derivatives dashboard when CoinGecko is unavailable."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from terminalq.providers import coingecko, hyperliquid


@pytest.fixture(autouse=True)
def clear_caches(tmp_cache_dir):
    """Every test starts with an empty cache."""
    pass


_FOCUS = {"BTC", "ETH", "SOL", "XRP", "BNB"}


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_to_float_parses_strings_and_rejects_garbage():
    assert hyperliquid._to_float("0.0000125") == 0.0000125
    assert hyperliquid._to_float(5) == 5.0
    assert hyperliquid._to_float(None) is None
    assert hyperliquid._to_float("abc") is None


def test_normalize_converts_hourly_funding_to_pct_8h_and_oi_to_usd():
    meta = {"universe": [{"name": "BTC"}, {"name": "ETH"}, {"name": "DOGE"}]}
    ctxs = [
        {"funding": "0.0000125", "openInterest": "100", "markPx": "60000"},
        {"funding": "-0.00005", "openInterest": "200", "markPx": "1600"},
        {"funding": "0.0001", "openInterest": "5", "markPx": "0.1"},  # DOGE — not in focus
    ]
    out = hyperliquid._normalize(meta, ctxs, _FOCUS)

    assert set(out) == {"BTC", "ETH"}  # DOGE filtered out
    # 0.0000125 (fraction/hr) × 8 × 100 = 0.01 %/8h
    assert out["BTC"]["funding_rates"] == [0.01]
    # 100 coins × $60,000 = $6,000,000 OI
    assert out["BTC"]["open_interests"] == [6000000.0]
    assert out["ETH"]["funding_rates"] == [round(-0.00005 * 8 * 100, 6)]


def test_normalize_skips_entries_with_no_funding_or_oi():
    meta = {"universe": [{"name": "BTC"}]}
    ctxs = [{"funding": None, "openInterest": None, "markPx": "60000"}]
    assert hyperliquid._normalize(meta, ctxs, _FOCUS) == {}


# ---------------------------------------------------------------------------
# fetch_derivatives (provider level, httpx mocked)
# ---------------------------------------------------------------------------


def _mock_post_client(payload, *, fail=False):
    def _resp():
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.json = MagicMock(return_value=payload)
        resp.raise_for_status = MagicMock()
        return resp

    async def fake_post(url, **kwargs):
        if fail:
            raise httpx.ConnectError("hyperliquid down")
        return _resp()

    client = AsyncMock()
    client.post = AsyncMock(side_effect=fake_post)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


async def test_fetch_derivatives_happy_path():
    payload = [
        {"universe": [{"name": "BTC"}]},
        [{"funding": "0.0000125", "openInterest": "100", "markPx": "60000"}],
    ]
    with patch(
        "terminalq.providers.hyperliquid.httpx.AsyncClient",
        return_value=_mock_post_client(payload),
    ):
        out = await hyperliquid.fetch_derivatives(_FOCUS)
    assert out["BTC"]["funding_rates"] == [0.01]


async def test_fetch_derivatives_bad_shape_returns_none():
    with patch(
        "terminalq.providers.hyperliquid.httpx.AsyncClient",
        return_value=_mock_post_client({"not": "a list"}),
    ):
        assert await hyperliquid.fetch_derivatives(_FOCUS) is None


async def test_fetch_derivatives_network_error_returns_none():
    with patch(
        "terminalq.providers.hyperliquid.httpx.AsyncClient",
        return_value=_mock_post_client(None, fail=True),
    ):
        assert await hyperliquid.fetch_derivatives(_FOCUS) is None


# ---------------------------------------------------------------------------
# CoinGecko derivatives dashboard falls through to Hyperliquid on error
# ---------------------------------------------------------------------------


async def test_derivatives_falls_back_to_hyperliquid():
    fb = {"BTC": {"funding_rates": [0.01], "open_interests": [6000000.0]}}
    with (
        patch.object(coingecko, "_fetch", AsyncMock(return_value={"_error": "Connection failed"})),
        patch.object(coingecko.hyperliquid, "fetch_derivatives", AsyncMock(return_value=fb)),
    ):
        result = await coingecko.get_crypto_derivatives_dashboard()

    assert result["source"] == "hyperliquid (fallback — CoinGecko unavailable)"
    assert result["derivatives"]["BTC"]["avg_funding_rate_8h_pct"] == 0.01
    assert result["derivatives"]["BTC"]["signal"] == "mild bullish bias"
    assert "single venue" in result["note"]


async def test_derivatives_surfaces_error_when_both_down():
    with (
        patch.object(coingecko, "_fetch", AsyncMock(return_value={"_error": "Connection failed"})),
        patch.object(coingecko.hyperliquid, "fetch_derivatives", AsyncMock(return_value=None)),
    ):
        result = await coingecko.get_crypto_derivatives_dashboard()
    assert result["error"] == "Connection failed"
    assert result["source"] == "coingecko"


async def test_derivatives_uses_coingecko_when_available():
    """Happy path still uses CoinGecko's multi-exchange aggregate."""
    tickers = [
        {"index_id": "BTC", "contract_type": "perpetual", "funding_rate": 0.012, "open_interest": 1_000_000},
        {"index_id": "BTC", "contract_type": "perpetual", "funding_rate": 0.008, "open_interest": 2_000_000},
    ]
    with patch.object(coingecko, "_fetch", AsyncMock(return_value=tickers)):
        result = await coingecko.get_crypto_derivatives_dashboard()
    assert result["source"] == "coingecko"
    assert result["derivatives"]["BTC"]["exchanges_tracked"] == 2
    assert result["derivatives"]["BTC"]["avg_funding_rate_8h_pct"] == 0.01  # (0.012+0.008)/2
    assert "single venue" not in result["note"]
