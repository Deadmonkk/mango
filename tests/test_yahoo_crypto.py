"""Tests for the Yahoo Finance crypto fallback (mango.providers.yahoo_crypto)
and its wiring into CoinGecko quote/batch and crypto technicals."""

from ._upstream_wiring import host_module

from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

coingecko = host_module("terminalq.providers.coingecko")
from mango.providers import crypto_analytics
from mango.providers import yahoo_crypto
from ._upstream_wiring import requires


@pytest.fixture(autouse=True)
def clear_caches(tmp_cache_dir):
    """Every test starts with an empty cache."""
    pass


def _fake_ticker(closes, volumes=None):
    """Build a fake yfinance.Ticker whose .history returns a DataFrame of closes/volumes."""
    volumes = volumes if volumes is not None else [1000.0] * len(closes)
    df = pd.DataFrame({"Close": closes, "Volume": volumes})

    ticker = MagicMock()
    ticker.history = MagicMock(return_value=df)
    factory = MagicMock(return_value=ticker)
    return factory


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_yahoo_ticker_appends_usd():
    assert yahoo_crypto.yahoo_ticker("btc") == "BTC-USD"
    assert yahoo_crypto.yahoo_ticker("ETH") == "ETH-USD"


def test_yahoo_ticker_is_idempotent():
    assert yahoo_crypto.yahoo_ticker("SOL-USD") == "SOL-USD"


def test_period_for_days_maps_to_named_periods():
    assert yahoo_crypto._period_for_days(1) == "5d"
    assert yahoo_crypto._period_for_days(30) == "1mo"
    assert yahoo_crypto._period_for_days(200) == "1y"
    assert yahoo_crypto._period_for_days(900) == "2y"


def test_pct_change_basic():
    closes = [100.0, 110.0]  # +10% over 1 lookback
    assert yahoo_crypto._pct_change(closes, 1) == 10.0


def test_pct_change_insufficient_data_returns_none():
    assert yahoo_crypto._pct_change([100.0], 1) is None
    assert yahoo_crypto._pct_change([100.0, 110.0], 7) is None


# ---------------------------------------------------------------------------
# fetch_crypto_ohlcv / fetch_crypto_quote
# ---------------------------------------------------------------------------


async def test_fetch_ohlcv_slices_to_requested_days():
    closes = [float(i) for i in range(300)]
    with patch.object(yahoo_crypto.yfinance, "Ticker", _fake_ticker(closes)):
        out_closes, out_vols = await yahoo_crypto.fetch_crypto_ohlcv("BTC", days=200)
    assert len(out_closes) == 200
    assert out_closes[-1] == 299.0  # newest preserved


async def test_fetch_ohlcv_empty_dataframe_returns_empty():
    empty_factory = _fake_ticker([])
    with patch.object(yahoo_crypto.yfinance, "Ticker", empty_factory):
        out_closes, out_vols = await yahoo_crypto.fetch_crypto_ohlcv("BTC")
    assert out_closes == [] and out_vols == []


async def test_fetch_ohlcv_never_raises_on_error():
    boom = MagicMock(side_effect=RuntimeError("yahoo down"))
    with patch.object(yahoo_crypto.yfinance, "Ticker", boom):
        out_closes, out_vols = await yahoo_crypto.fetch_crypto_ohlcv("BTC")
    assert out_closes == [] and out_vols == []


async def test_fetch_crypto_quote_shape_and_changes():
    # 41 points so 24h/7d/30d changes are all computable.
    closes = [100.0 + i for i in range(41)]  # last=140, -1=139, -7=133, -30=110
    with patch.object(yahoo_crypto.yfinance, "Ticker", _fake_ticker(closes)):
        q = await yahoo_crypto.fetch_crypto_quote("BTC")
    assert q["symbol"] == "BTC"
    assert q["current_price"] == 140.0
    assert q["price_change_pct_24h"] == round((140 / 139 - 1) * 100, 2)
    assert q["price_change_pct_30d"] == round((140 / 110 - 1) * 100, 2)
    assert q["market_cap"] is None  # Yahoo has no market cap → documented degradation
    assert "fallback" in q["source"]


async def test_fetch_crypto_quote_returns_none_when_no_data():
    with patch.object(yahoo_crypto.yfinance, "Ticker", _fake_ticker([])):
        assert await yahoo_crypto.fetch_crypto_quote("BTC") is None


# ---------------------------------------------------------------------------
# CoinGecko quote/batch fall through to Yahoo on error
# ---------------------------------------------------------------------------


@requires(coingecko, "yahoo_crypto")
async def test_get_crypto_quote_falls_back_to_yahoo():
    fake_quote = {
        "symbol": "BTC",
        "current_price": 50000.0,
        "source": "yahoo_finance (fallback — CoinGecko unavailable)",
    }
    with (
        patch.object(coingecko, "_fetch", AsyncMock(return_value={"_error": "Connection failed"})),
        patch.object(coingecko.yahoo_crypto, "fetch_crypto_quote", AsyncMock(return_value=fake_quote)),
    ):
        result = await coingecko.get_crypto_quote("BTC")
    assert result["current_price"] == 50000.0
    assert "yahoo" in result["source"]


@requires(coingecko, "yahoo_crypto")
async def test_get_crypto_quote_surfaces_error_when_yahoo_also_down():
    with (
        patch.object(coingecko, "_fetch", AsyncMock(return_value={"_error": "Connection failed"})),
        patch.object(coingecko.yahoo_crypto, "fetch_crypto_quote", AsyncMock(return_value=None)),
    ):
        result = await coingecko.get_crypto_quote("BTC")
    assert result["error"] == "Connection failed"
    assert result["source"] == "coingecko"


@requires(coingecko, "yahoo_crypto")
async def test_get_crypto_batch_falls_back_per_symbol():
    def _fb(symbol):
        return {
            "symbol": symbol.upper(),
            "current_price": 1.0,
            "source": "yahoo_finance (fallback — CoinGecko unavailable)",
        }

    with (
        patch.object(coingecko, "_fetch", AsyncMock(return_value={"_error": "Connection failed"})),
        patch.object(coingecko.yahoo_crypto, "fetch_crypto_quote", AsyncMock(side_effect=lambda s: _fb(s))),
    ):
        results = await coingecko.get_crypto_batch(["BTC", "ETH"])
    assert {r["symbol"] for r in results} == {"BTC", "ETH"}
    assert all("yahoo" in r["source"] for r in results)


# ---------------------------------------------------------------------------
# Crypto technicals fall through to Yahoo on error
# ---------------------------------------------------------------------------


async def test_technicals_fall_back_to_yahoo_no_market_cap():
    closes = [100.0 + (i % 7) for i in range(210)]
    volumes = [1000.0] * 210
    with (
        patch.object(crypto_analytics, "_fetch", AsyncMock(return_value={"_error": "Connection failed"})),
        patch.object(crypto_analytics.yahoo_crypto, "fetch_crypto_ohlcv", AsyncMock(return_value=(closes, volumes))),
    ):
        result = await crypto_analytics.get_crypto_technicals("BTC")
    assert result["symbol"] == "BTC"
    assert result["momentum"]["rsi_14"] is not None
    assert result["moving_averages"]["sma_200"] is not None
    assert result["ntv_proxy"]["value"] is None  # no market-cap series on fallback
    assert result["source"].startswith("yahoo_finance")


async def test_technicals_error_when_both_sources_down():
    with (
        patch.object(crypto_analytics, "_fetch", AsyncMock(return_value={"_error": "Connection failed"})),
        patch.object(crypto_analytics.yahoo_crypto, "fetch_crypto_ohlcv", AsyncMock(return_value=([], []))),
    ):
        result = await crypto_analytics.get_crypto_technicals("BTC")
    assert "error" in result
    assert "both unavailable" in result["error"]


async def test_technicals_uses_coingecko_when_available():
    """Happy path still uses CoinGecko and its market-cap-based NVT proxy."""
    prices = [[i, 100.0 + (i % 7)] for i in range(210)]
    cg_data = {
        "prices": prices,
        "total_volumes": [[i, 1_000_000.0] for i in range(210)],
        "market_caps": [[i, 2_000_000_000.0] for i in range(210)],
    }
    with patch.object(crypto_analytics, "_fetch", AsyncMock(return_value=cg_data)):
        result = await crypto_analytics.get_crypto_technicals("BTC")
    assert result["source"] == "coingecko (computed)"
    assert result["ntv_proxy"]["value"] is not None
