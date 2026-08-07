"""Tests for mango.providers.coingecko — the high-level CoinGecko API surface.

All HTTP is faked by patching ``_fetch`` (the shared low-level fetch helper
this module imports from ``mango.core.coingecko``); no test touches the
network. AAA structure throughout.

Reference shapes are drawn from real saved FR-report payloads
(``~/Desktop/TerminalIQ Reports/.briefs/fr_raw_*.json``), not memory — see
the module docstring in ``mango/providers/coingecko.py`` for exactly which
keys were used.
"""

from unittest.mock import AsyncMock, patch

import pytest

from mango.providers import coingecko as cg

pytestmark = pytest.mark.asyncio


def _patched(return_value=None, side_effect=None):
    """Patch mango.providers.coingecko._fetch for the duration of a `with` block."""
    if side_effect is not None:
        return patch.object(cg, "_fetch", AsyncMock(side_effect=side_effect))
    return patch.object(cg, "_fetch", AsyncMock(return_value=return_value))


# ---------------------------------------------------------------------------
# Fixtures: minimal raw CoinGecko-shaped responses
# ---------------------------------------------------------------------------


def _raw_market_item(**overrides) -> dict:
    item = {
        "id": "bitcoin",
        "symbol": "btc",
        "current_price": 64965,
        "market_cap": 1303615131201,
        "market_cap_rank": 1,
        "total_volume": 20969261105,
        "high_24h": 65312,
        "low_24h": 64124,
        "price_change_24h": 285.79,
        "price_change_percentage_24h": 0.3,
        "price_change_percentage_7d_in_currency": 3.8,
        "price_change_percentage_30d_in_currency": 5.0,
        "circulating_supply": 20067009.0,
        "total_supply": 20067009.0,
        "ath": 126080,
        "ath_change_percentage": -48.4735,
    }
    item.update(overrides)
    return item


def _raw_coin_detail(**overrides) -> dict:
    detail = {
        "symbol": "btc",
        "name": "Bitcoin",
        "market_data": {
            "current_price": {"usd": 64925},
            "circulating_supply": 20067009.0,
            "total_supply": 20067009.0,
            "max_supply": 21000000.0,
            "market_cap": {"usd": 1302790016664},
            "fully_diluted_valuation": {"usd": 1302790016664},
            "price_change_percentage_1h_in_currency": {"usd": 0.00561},
            "price_change_percentage_24h": 0.50094,
            "price_change_percentage_7d": 3.51334,
            "price_change_percentage_14d": 1.44785,
            "price_change_percentage_30d": 5.31403,
            "price_change_percentage_60d": 1.87362,
            "price_change_percentage_200d": -30.21173,
            "price_change_percentage_1y": -44.37816,
            "ath": {"usd": 126080},
            "ath_change_percentage": {"usd": -48.50492},
            "atl": {"usd": 67.81},
        },
        "community_data": {
            "reddit_subscribers": 0,
            "reddit_accounts_active_48h": 0,
            "twitter_followers": None,
            "telegram_channel_user_count": None,
        },
        "developer_data": {
            "stars": 73168,
            "forks": 36426,
            "commit_count_4_weeks": 108,
            "pull_requests_merged": 11215,
            "pull_request_contributors": 846,
        },
    }
    detail.update(overrides)
    return detail


def _raw_global(**overrides) -> dict:
    data = {
        "data": {
            "total_market_cap": {"usd": 2296212262091.7837},
            "total_volume": {"usd": 53407446088.29522},
            "market_cap_change_percentage_24h_usd": 0.20414504038351075,
            "market_cap_percentage": {"btc": 56.75, "eth": 10.09},
            "active_cryptocurrencies": 18218,
        }
    }
    data["data"].update(overrides)
    return data


def _raw_fear_greed(n: int = 7) -> dict:
    values = [29, 25, 27, 25, 28, 27, 27][:n]
    classifications = ["Fear", "Extreme Fear", "Fear", "Extreme Fear", "Fear", "Fear", "Fear"][:n]
    return {
        "data": [
            {"value": str(v), "value_classification": c, "timestamp": str(1786060800 - i * 86400)}
            for i, (v, c) in enumerate(zip(values, classifications))
        ]
    }


def _raw_derivatives_tickers() -> list[dict]:
    return [
        {"index_id": "BTC", "funding_rate": 0.004578, "open_interest": 2319029344.0},
        {"index_id": "ETH", "funding_rate": 0.006095, "open_interest": 1640729828.0},
        {"index_id": "SOL", "funding_rate": 0.01, "open_interest": 301733230.0},
    ]


def _raw_trending() -> dict:
    return {
        "coins": [
            {
                "item": {
                    "name": "Lighter",
                    "symbol": "lit",
                    "market_cap_rank": 89,
                    "data": {
                        "price": 2.3981943850598273,
                        "price_change_percentage_24h": {"usd": 6.512748091024497},
                        "market_cap": 599733995.0,
                    },
                }
            }
        ]
    }


# ---------------------------------------------------------------------------
# get_crypto_batch / get_crypto_quote
# ---------------------------------------------------------------------------


async def test_get_crypto_batch_returns_a_bare_list_matching_saved_shape():
    # Arrange: crypto_batch in a saved fr_raw_*.json is a literal JSON array,
    # not a dict-wrapped payload — the collector stores this function's
    # return value verbatim.
    raw = [_raw_market_item()]

    # Act
    with _patched(return_value=raw):
        result = await cg.get_crypto_batch(["BTC"])

    # Assert
    assert isinstance(result, list)
    item = result[0]
    assert item["symbol"] == "BTC"
    assert item["coin_id"] == "bitcoin"
    assert item["current_price"] == 64965
    assert item["price_change_pct_24h"] == 0.3
    assert item["price_change_pct_7d"] == 3.8
    assert item["price_change_pct_30d"] == 5.0
    assert item["ath_change_pct"] == -48.4735
    assert item["source"] == "coingecko"


async def test_get_crypto_batch_empty_symbols_returns_empty_list_without_fetching():
    with _patched(return_value={"should": "never be seen"}) as mock_fetch:
        result = await cg.get_crypto_batch([])

    assert result == []
    mock_fetch.assert_not_called()


async def test_get_crypto_batch_translates_underscore_error_to_normal_error_key():
    """Guards the fallback: `_fetch`'s `_error` must become `error` (no underscore)
    at this layer, never pass through unchanged."""
    with _patched(return_value={"_error": "rate limited (429): retries exhausted"}):
        result = await cg.get_crypto_batch(["BTC"])

    assert isinstance(result, list) and len(result) == 1
    assert "_error" not in result[0]
    assert result[0]["error"] == "rate limited (429): retries exhausted"
    assert result[0]["source"] == "coingecko"


async def test_get_crypto_batch_unknown_symbol_yields_empty_upstream_list():
    # CoinGecko itself returns an empty array for an id it doesn't recognize
    # rather than erroring — that must not raise here either.
    with _patched(return_value=[]):
        result = await cg.get_crypto_batch(["NOTACOIN"])

    assert result == []


async def test_get_crypto_quote_returns_single_normalized_item():
    with _patched(return_value=[_raw_market_item()]):
        result = await cg.get_crypto_quote("BTC")

    assert result["symbol"] == "BTC"
    assert result["current_price"] == 64965


async def test_get_crypto_quote_unknown_symbol_returns_error_dict():
    with _patched(return_value=[]):
        result = await cg.get_crypto_quote("NOTACOIN")

    assert result["error"] == "no market data found for symbol 'NOTACOIN'"
    assert result["source"] == "coingecko"


async def test_get_crypto_quote_translates_underscore_error():
    with _patched(return_value={"_error": "boom"}):
        result = await cg.get_crypto_quote("BTC")

    assert "_error" not in result
    assert result["error"] == "boom"


# ---------------------------------------------------------------------------
# get_crypto_market_overview
# ---------------------------------------------------------------------------


async def test_get_crypto_market_overview_matches_saved_shape():
    responses = [
        _raw_global(),  # /global
        [],  # stablecoin basket lookup
        _raw_fear_greed(),  # fear & greed
    ]

    async def fake_fetch(client, url, params):
        return responses.pop(0)

    with _patched(side_effect=fake_fetch):
        result = await cg.get_crypto_market_overview()

    assert result["total_market_cap_usd"] == 2296212262091.7837
    assert result["total_volume_24h_usd"] == 53407446088.29522
    assert result["market_cap_signal"] == "expanding"
    assert result["dominance"]["btc"] == 56.75
    assert result["dominance"]["eth"] == 10.09
    assert result["active_cryptocurrencies"] == 18218
    assert result["source"] == "coingecko + alternative.me"
    assert result["note"].startswith("Stablecoin dominance = USDT+USDC+BUSD+DAI")

    # Fear & greed: current carries a signal, and IS the first element of
    # the 7d history (matches the saved payload's shape exactly).
    assert result["fear_greed_current"]["value"] == 29
    assert result["fear_greed_current"]["classification"] == "Fear"
    assert result["fear_greed_current"]["signal"] == "fear"
    assert len(result["fear_greed_7d"]) == 7
    assert result["fear_greed_7d"][0] == result["fear_greed_current"]
    assert "signal" not in result["fear_greed_7d"][1]


async def test_get_crypto_market_overview_survives_fear_greed_outage():
    responses = [_raw_global(), [], {"_error": "alternative.me down"}]

    async def fake_fetch(client, url, params):
        return responses.pop(0)

    with _patched(side_effect=fake_fetch):
        result = await cg.get_crypto_market_overview()

    # A Fear & Greed outage degrades the overview, it does not fail it.
    assert "error" not in result
    assert result["fear_greed_current"] is None
    assert result["fear_greed_7d"] == []
    assert result["total_market_cap_usd"] == 2296212262091.7837


async def test_get_crypto_market_overview_translates_underscore_error_on_global_failure():
    with _patched(return_value={"_error": "global endpoint down"}):
        result = await cg.get_crypto_market_overview()

    assert "_error" not in result
    assert result["error"] == "global endpoint down"
    assert result["source"] == "coingecko"


# ---------------------------------------------------------------------------
# get_crypto_deep
# ---------------------------------------------------------------------------


async def test_get_crypto_deep_matches_saved_btc_shape():
    with _patched(return_value=_raw_coin_detail()):
        result = await cg.get_crypto_deep("BTC")

    assert result["symbol"] == "BTC"
    assert result["name"] == "Bitcoin"
    assert result["price_usd"] == 64925

    assert result["returns"]["24h"] == 0.50094
    assert result["returns"]["7d"] == 3.51334
    assert result["returns"]["30d"] == 5.31403

    assert result["ath_usd"] == 126080
    assert result["ath_change_pct"] == -48.50492
    assert result["atl_usd"] == 67.81

    assert result["supply"]["circulating"] == 20067009.0
    assert result["supply"]["circulating_pct_of_total"] == 100.0
    assert result["supply"]["market_cap_usd"] == 1302790016664
    assert result["supply"]["fdv_to_market_cap_ratio"] == 1.0
    assert result["supply"]["dilution_signal"] == "low dilution risk — most supply already circulating"

    assert result["developer"]["github_stars"] == 73168
    assert result["developer"]["commits_4_weeks"] == 108
    assert result["developer"]["contributors"] == 846
    assert result["developer"]["dev_signal"] == "active"

    assert result["source"] == "coingecko"


async def test_get_crypto_deep_flags_high_dilution_risk_when_fdv_far_exceeds_market_cap():
    detail = _raw_coin_detail(
        market_data={
            **_raw_coin_detail()["market_data"],
            "market_cap": {"usd": 100_000_000},
            "fully_diluted_valuation": {"usd": 500_000_000},
        }
    )

    with _patched(return_value=detail):
        result = await cg.get_crypto_deep("BTC")

    assert result["supply"]["fdv_to_market_cap_ratio"] == 5.0
    assert "high dilution risk" in result["supply"]["dilution_signal"]


async def test_get_crypto_deep_unknown_symbol_returns_error():
    with _patched(return_value={}):
        result = await cg.get_crypto_deep("NOTACOIN")

    assert "error" in result
    assert result["source"] == "coingecko"


async def test_get_crypto_deep_translates_underscore_error():
    with _patched(return_value={"_error": "coin not found"}):
        result = await cg.get_crypto_deep("BTC")

    assert "_error" not in result
    assert result["error"] == "coin not found"


async def test_get_crypto_deep_handles_missing_developer_and_community_blocks():
    # A coin can genuinely lack developer/community data upstream; this must
    # not raise, only degrade those sub-blocks.
    detail = _raw_coin_detail(developer_data={}, community_data={})

    with _patched(return_value=detail):
        result = await cg.get_crypto_deep("BTC")

    assert result["developer"]["dev_signal"] == "quiet"
    assert result["developer"]["commits_4_weeks"] == 0
    assert result["community"]["reddit_subscribers"] == 0


# ---------------------------------------------------------------------------
# get_crypto_derivatives_dashboard
# ---------------------------------------------------------------------------


async def test_get_crypto_derivatives_dashboard_matches_saved_shape_and_annualization():
    with _patched(return_value=_raw_derivatives_tickers()):
        result = await cg.get_crypto_derivatives_dashboard()

    btc = result["derivatives"]["BTC"]
    assert btc["avg_funding_rate_8h_pct"] == 0.004578
    assert btc["avg_funding_annualized_pct"] == 5.01  # 0.004578 * 1095
    assert btc["total_open_interest_usd"] == 2319029344.0
    assert btc["exchanges_tracked"] == 1
    assert btc["signal"] == "mild bullish bias"
    assert result["source"] == "coingecko"
    assert "BNB" not in result["derivatives"]  # no matching tickers — omitted, not errored


async def test_get_crypto_derivatives_dashboard_flags_crowded_long():
    tickers = [{"index_id": "BTC", "funding_rate": 0.120169, "open_interest": 1.0}]

    with _patched(return_value=tickers):
        result = await cg.get_crypto_derivatives_dashboard()

    signal = result["derivatives"]["BTC"]["signal"]
    assert "crowded LONG" in signal
    assert "0.1202%/8h" in signal


async def test_get_crypto_derivatives_dashboard_translates_underscore_error():
    with _patched(return_value={"_error": "derivatives endpoint down"}):
        result = await cg.get_crypto_derivatives_dashboard()

    assert "_error" not in result
    assert result["error"] == "derivatives endpoint down"


# ---------------------------------------------------------------------------
# get_crypto_dominance
# ---------------------------------------------------------------------------


async def test_get_crypto_dominance_matches_saved_shape():
    global_resp = _raw_global(market_cap_percentage={"btc": 56.4, "eth": 9.92})
    stablecoin_resp = [{"market_cap": 100}, {"market_cap": 50}]
    altcoin_pool = [
        {"id": "bitcoin", "price_change_percentage_30d_in_currency": 1.5},
        {"id": "tether", "price_change_percentage_30d_in_currency": 0.0},
        {"id": "some-alt", "price_change_percentage_30d_in_currency": 10.0},
        {"id": "another-alt", "price_change_percentage_30d_in_currency": -2.0},
    ]
    responses = [global_resp, stablecoin_resp, altcoin_pool]

    async def fake_fetch(client, url, params):
        return responses.pop(0)

    with _patched(side_effect=fake_fetch):
        result = await cg.get_crypto_dominance()

    assert result["dominance"]["btc_pct"] == 56.4
    assert result["dominance"]["eth_pct"] == 9.92
    assert result["signals"]["btc_dominance"] == "high — flight to BTC quality"
    assert result["signals"]["altcoin_season"] in {
        "alt season — broad rotation out of BTC",
        "BTC season — money concentrating in Bitcoin",
        "mixed — no clear rotation",
    }
    assert result["altcoin_season_detail"]["btc_30d_return_pct"] == 1.5
    assert result["altcoin_season_detail"]["coins_beating_btc_30d"] == 1  # only "some-alt" (10.0 > 1.5)
    assert result["source"] == "coingecko"


async def test_get_crypto_dominance_translates_underscore_error():
    with _patched(return_value={"_error": "global endpoint down"}):
        result = await cg.get_crypto_dominance()

    assert "_error" not in result
    assert result["error"] == "global endpoint down"


# ---------------------------------------------------------------------------
# get_crypto_trending
# ---------------------------------------------------------------------------


async def test_get_crypto_trending_matches_saved_shape():
    with _patched(return_value=_raw_trending()):
        result = await cg.get_crypto_trending()

    coin = result["trending_coins"][0]
    assert coin["name"] == "Lighter"
    assert coin["symbol"] == "LIT"
    assert coin["market_cap_rank"] == 89
    assert coin["price_usd"] == pytest.approx(2.3981943850598273)
    assert coin["change_24h_pct"] == pytest.approx(6.512748091024497)
    assert coin["change_7d_pct"] is None
    assert coin["market_cap_usd"] == 599733995.0
    assert result["note"].startswith("Most searched on CoinGecko")
    assert result["source"] == "coingecko"


async def test_get_crypto_trending_handles_malformed_entry_without_raising():
    raw = {"coins": [{"item": {"name": "Broken"}}, "not-even-a-dict"]}

    with _patched(return_value=raw):
        result = await cg.get_crypto_trending()

    assert len(result["trending_coins"]) == 1
    assert result["trending_coins"][0]["price_usd"] is None


async def test_get_crypto_trending_translates_underscore_error():
    with _patched(return_value={"_error": "trending endpoint down"}):
        result = await cg.get_crypto_trending()

    assert "_error" not in result
    assert result["error"] == "trending endpoint down"


# ---------------------------------------------------------------------------
# screen_cryptos
# ---------------------------------------------------------------------------


def _screen_pool() -> list[dict]:
    return [
        _raw_market_item(id="bitcoin", symbol="btc", market_cap=1_000_000_000_000, total_volume=5),
        _raw_market_item(id="dogecoin", symbol="doge", market_cap=10_000_000_000, total_volume=50),
        _raw_market_item(id="shiba-inu", symbol="shib", market_cap=1_000_000_000, total_volume=500),
    ]


async def test_screen_cryptos_filters_by_market_cap_bounds():
    with _patched(return_value=_screen_pool()):
        result = await cg.screen_cryptos(min_market_cap_b=5, max_market_cap_b=500)

    symbols = {c["symbol"] for c in result["cryptos"]}
    assert symbols == {"DOGE"}  # 10B is within [5B, 500B]; 1000B and 1B are excluded
    assert result["count"] == 1


async def test_screen_cryptos_sorts_by_requested_metric_descending():
    with _patched(return_value=_screen_pool()):
        result = await cg.screen_cryptos(sort_by="volume", limit=2)

    volumes = [c["total_volume"] for c in result["cryptos"]]
    assert volumes == sorted(volumes, reverse=True)
    assert len(result["cryptos"]) == 2  # limit respected


async def test_screen_cryptos_unknown_sort_by_falls_back_to_market_cap():
    with _patched(return_value=_screen_pool()):
        result = await cg.screen_cryptos(sort_by="not_a_real_field")

    caps = [c["market_cap"] for c in result["cryptos"]]
    assert caps == sorted(caps, reverse=True)
    assert result["filters"]["sort_by"] == "market_cap"


async def test_screen_cryptos_zero_bounds_mean_unbounded():
    with _patched(return_value=_screen_pool()):
        result = await cg.screen_cryptos(min_market_cap_b=0, max_market_cap_b=0)

    assert result["count"] == 3


async def test_screen_cryptos_translates_underscore_error():
    with _patched(return_value={"_error": "markets endpoint down"}):
        result = await cg.screen_cryptos()

    assert "_error" not in result
    assert result["error"] == "markets endpoint down"


async def test_screen_cryptos_empty_upstream_list_returns_empty_result_not_error():
    with _patched(return_value=[]):
        result = await cg.screen_cryptos()

    assert result["cryptos"] == []
    assert result["count"] == 0


# ---------------------------------------------------------------------------
# Shared helper coverage
# ---------------------------------------------------------------------------


def test_as_error_returns_none_for_a_healthy_payload():
    assert cg._as_error({"data": {"ok": True}}) is None


def test_safe_float_parses_dollar_formatted_strings():
    assert cg._safe_float("$64,941.20") == pytest.approx(64941.20)


def test_safe_float_rejects_garbage_without_raising():
    assert cg._safe_float("not a number") is None
    assert cg._safe_float(None) is None
