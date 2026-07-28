"""Tests for terminalq.providers.coingecko — symbol mapping, 429 retry, funding rate math."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from terminalq.providers import coingecko


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


# ---------------------------------------------------------------------------
# Symbol → CoinGecko ID mapping
# ---------------------------------------------------------------------------


def test_bnb_resolves_to_binancecoin():
    """BNB is a top-5 coin and must map to its CoinGecko ID, not lowercase passthrough."""
    assert coingecko._resolve_id("BNB") == "binancecoin"
    assert coingecko._resolve_id("bnb") == "binancecoin"


def test_other_major_symbols_resolve():
    """Common top-20 symbols resolve to real CoinGecko IDs."""
    assert coingecko._resolve_id("TRX") == "tron"
    assert coingecko._resolve_id("TON") == "the-open-network"
    assert coingecko._resolve_id("XLM") == "stellar"


def test_unknown_symbol_falls_back_to_lowercase():
    assert coingecko._resolve_id("SOMENEWCOIN") == "somenewcoin"


# ---------------------------------------------------------------------------
# 429 retry with backoff
# ---------------------------------------------------------------------------


async def test_fetch_retries_on_429_then_succeeds():
    """A 429 response is retried with backoff and eventually succeeds."""
    responses = [_mock_response({}, 429), _mock_response({}, 429), _mock_response({"ok": True}, 200)]
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=responses)

    sleep_calls = []

    async def fake_sleep(delay):
        sleep_calls.append(delay)

    with patch("terminalq.providers.coingecko.asyncio.sleep", fake_sleep):
        result = await coingecko._fetch(mock_client, "https://x/test", {})

    assert result == {"ok": True}
    assert mock_client.get.call_count == 3
    # Backoff delays must increase (exponential)
    assert len(sleep_calls) == 2
    assert sleep_calls[1] > sleep_calls[0]


async def test_fetch_gives_up_after_max_retries():
    """Persistent 429s return an error dict instead of raising."""
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=_mock_response({}, 429))

    async def fake_sleep(delay):
        pass

    with patch("terminalq.providers.coingecko.asyncio.sleep", fake_sleep):
        result = await coingecko._fetch(mock_client, "https://x/test", {})

    assert result == {"_error": "HTTP 429"}


async def test_fetch_does_not_retry_other_http_errors():
    """Non-429 HTTP errors fail immediately without retries."""
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=_mock_response({}, 404))

    result = await coingecko._fetch(mock_client, "https://x/test", {})

    assert result == {"_error": "HTTP 404"}
    assert mock_client.get.call_count == 1


# ---------------------------------------------------------------------------
# Funding rate units (CoinGecko returns percent, not fraction)
# ---------------------------------------------------------------------------

_DERIVATIVES_DATA = [
    {
        "index_id": "BTC",
        "contract_type": "perpetual",
        "funding_rate": 0.12,  # 0.12% per 8h (CoinGecko reports percent)
        "open_interest": 1_000_000_000,
    },
    {
        "index_id": "BTC",
        "contract_type": "perpetual",
        "funding_rate": 0.08,
        "open_interest": 500_000_000,
    },
]


async def test_funding_rate_annualized_uses_percent_units():
    """funding_rate from CoinGecko is already in percent — annualization must not multiply by 100 again."""
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=_mock_response(_DERIVATIVES_DATA))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("terminalq.providers.coingecko.httpx.AsyncClient", return_value=mock_client):
        result = await coingecko.get_crypto_derivatives_dashboard()

    btc = result["derivatives"]["BTC"]
    avg = (0.12 + 0.08) / 2  # 0.10% per 8h
    assert btc["avg_funding_rate_8h_pct"] == round(avg, 6)
    # 0.10%/8h × 3 funding periods/day × 365 days = 109.5%/yr — NOT 10,950%
    assert btc["avg_funding_annualized_pct"] == round(avg * 3 * 365, 2)
    assert btc["avg_funding_annualized_pct"] < 200


async def test_funding_signal_formats_percent_correctly():
    """Crowded-long signal shows '0.1000%/8h', not '10.0000%/8h'."""
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=_mock_response(_DERIVATIVES_DATA))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("terminalq.providers.coingecko.httpx.AsyncClient", return_value=mock_client):
        result = await coingecko.get_crypto_derivatives_dashboard()

    signal = result["derivatives"]["BTC"]["signal"]
    assert "crowded LONG" in signal
    assert "0.1000%/8h" in signal


async def test_dominance_falls_back_to_coinpaprika_on_429():
    """When CoinGecko 429s, dominance is rebuilt from keyless CoinPaprika."""
    paprika_global = {"market_cap_usd": 2_000_000_000_000, "bitcoin_dominance_percentage": 55.0}
    paprika_tickers = [
        {"symbol": "BTC", "quotes": {"USD": {"market_cap": 1_100_000_000_000, "percent_change_30d": 5.0}}},
        {"symbol": "ETH", "quotes": {"USD": {"market_cap": 200_000_000_000, "percent_change_30d": 8.0}}},
        {"symbol": "USDT", "quotes": {"USD": {"market_cap": 100_000_000_000, "percent_change_30d": 0.0}}},
        {"symbol": "SOL", "quotes": {"USD": {"market_cap": 80_000_000_000, "percent_change_30d": 12.0}}},
    ]

    async def fake_get(url, **kwargs):
        return _mock_response(paprika_global if url.endswith("/global") else paprika_tickers)

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=fake_get)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    async def fake_fetch(client, url, params):
        return {"_error": "HTTP 429"}

    with patch("terminalq.providers.coingecko._fetch", side_effect=fake_fetch), patch(
        "terminalq.providers.coingecko.httpx.AsyncClient", return_value=mock_client
    ):
        result = await coingecko.get_crypto_dominance()

    assert "error" not in result
    assert "coinpaprika" in result["source"]
    assert result["dominance"]["btc_pct"] == 55.0
    # ETH 200B / 2000B total = 10%; USDT 100B / 2000B = 5%
    assert result["dominance"]["eth_pct"] == 10.0
    assert result["dominance"]["stablecoins_pct"] == 5.0
    # ETH (+8%) and SOL (+12%) both beat BTC (+5%); stablecoins/BTC excluded from the count
    assert result["altcoin_season_detail"]["coins_beating_btc_30d"] == 2
    assert result["altcoin_season_detail"]["coins_measured"] == 2
