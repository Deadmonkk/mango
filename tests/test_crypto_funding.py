"""Tests for OI-weighted perpetual funding and its Hyperliquid fallback.

On 2026-08-12 CoinGecko's ``/derivatives`` endpoint returned 429 and the whole
funding read failed, which dropped the liquidation leg (15%) out of the Crypto
Regime Score and forced a renormalisation. Two causes, both covered here:

1. this module called CoinGecko with raw ``httpx``, bypassing the shared
   client's rate limiting, 429 retry/backoff and caching;
2. it had no fallback, even though a keyless single-venue source
   (``providers.hyperliquid``) was already reachable in the same request.
"""

from unittest.mock import AsyncMock, patch

import pytest

from mango.providers import crypto_funding


@pytest.fixture(autouse=True)
def clear_caches(tmp_cache_dir):
    """Every test starts with an empty cache."""
    pass


def _cg_contract(market: str, rate: float, oi: float, symbol: str = "BTC") -> dict:
    return {
        "market": market,
        "index_id": symbol,
        "funding_rate": rate,
        "open_interest": oi,
        "contract_type": "perpetual",
    }


_GOOD_CONTRACTS = [
    _cg_contract("Binance", 0.005, 8_000_000_000.0),
    _cg_contract("OKX", 0.003, 2_000_000_000.0),
    _cg_contract("Ostium", 9.5192, 412_000.0),  # the dust outlier from the docstring
]

_HL_PAYLOAD = {"BTC": {"funding_rates": [0.004], "open_interests": [3_000_000_000.0]}}

_BOTH_PREMIUMS = {
    "venues": [
        {"venue": "deribit", "premium_pct": 0.004},
        {"venue": "hyperliquid", "premium_pct": 0.006},
    ],
    "mean_premium_pct": 0.005,
}


def _patches(*, coingecko, premium=_BOTH_PREMIUMS, hyperliquid=None):
    """Patch the three outbound calls get_btc_funding makes."""
    return (
        patch.object(crypto_funding, "_fetch", AsyncMock(return_value=coingecko)),
        patch.object(crypto_funding, "_observed_premium", AsyncMock(return_value=premium)),
        patch.object(
            crypto_funding.hyperliquid, "fetch_derivatives", AsyncMock(return_value=hyperliquid)
        ),
    )


async def _run(**kwargs) -> dict:
    a, b, c = _patches(**kwargs)
    with a, b, c:
        return await crypto_funding.get_btc_funding("BTC")


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_weighted_funding_excludes_dust_and_outliers():
    result = crypto_funding._weighted_funding(_GOOD_CONTRACTS)

    assert result["venues_weighted"] == 2
    assert result["excluded_below_oi_threshold"] == 0
    assert [e["market"] for e in result["excluded_as_outliers"]] == ["Ostium"]
    # OI-weighted, not the unweighted mean of 0.005/0.003
    assert result["funding_8h_pct"] == pytest.approx(0.0046, abs=1e-4)


def test_weighted_funding_min_oi_is_overridable_for_a_single_venue():
    """The $1B floor removes dust from a 195-contract aggregate. Applied to one
    deep venue it would reject the only source available, so it is a parameter."""
    one_venue = [_cg_contract("Hyperliquid", 0.004, 300_000_000.0)]

    assert "error" in crypto_funding._weighted_funding(one_venue)
    fallback = crypto_funding._weighted_funding(
        one_venue, min_oi_usd=crypto_funding.MIN_SINGLE_VENUE_OI_USD
    )
    assert fallback["venues_weighted"] == 1
    assert fallback["funding_annualized_pct"] == crypto_funding.annualize_8h(0.004)


def test_independent_premium_excludes_the_funding_source_venue():
    assert crypto_funding._independent_premium(_BOTH_PREMIUMS, "coingecko") == pytest.approx(0.005)
    # Hyperliquid-sourced funding may not be verified against Hyperliquid's own basis
    assert crypto_funding._independent_premium(_BOTH_PREMIUMS, "hyperliquid") == pytest.approx(0.004)


def test_independent_premium_is_none_when_only_the_funding_venue_reports():
    only_hl = {"venues": [{"venue": "hyperliquid", "premium_pct": 0.006}], "mean_premium_pct": 0.006}

    assert crypto_funding._independent_premium(only_hl, "hyperliquid") is None


# ---------------------------------------------------------------------------
# Shared CoinGecko client (rate limiting, 429 retry, caching)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coingecko_is_fetched_through_the_shared_retrying_client():
    """The raw httpx.get this replaces had no 429 retry — a single rate limit
    killed the whole funding read."""
    fetch = AsyncMock(return_value=_GOOD_CONTRACTS)
    with (
        patch.object(crypto_funding, "_fetch", fetch),
        patch.object(crypto_funding, "_observed_premium", AsyncMock(return_value=_BOTH_PREMIUMS)),
    ):
        await crypto_funding.get_btc_funding("BTC")

    fetch.assert_awaited_once()
    assert crypto_funding._COINGECKO_DERIVATIVES in fetch.await_args.args


@pytest.mark.asyncio
async def test_coingecko_success_reports_coingecko_as_the_source():
    result = await _run(coingecko=_GOOD_CONTRACTS)

    assert result["funding_source"] == "coingecko"
    assert result["venues_weighted"] == 2
    assert result["basis_consistent"] is True
    assert "single-venue" not in result["note"]


# ---------------------------------------------------------------------------
# Hyperliquid fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limited_coingecko_falls_back_to_hyperliquid():
    result = await _run(
        coingecko={"_error": "rate limited (429): retries exhausted"},
        hyperliquid=_HL_PAYLOAD,
    )

    assert "error" not in result
    assert result["funding_source"] == "hyperliquid"
    assert result["funding_annualized_pct"] == crypto_funding.annualize_8h(0.004)
    assert result["venues_weighted"] == 1


@pytest.mark.asyncio
async def test_fallback_degrades_loudly_about_being_one_venue():
    result = await _run(coingecko={"_error": "429"}, hyperliquid=_HL_PAYLOAD)

    assert "single-venue" in result["note"]
    assert "429" in result["coingecko_error"]
    assert result["source"].startswith("hyperliquid")


@pytest.mark.asyncio
async def test_fallback_cross_checks_against_an_independent_venue_only():
    """Hyperliquid funding verified against Deribit's basis, never its own."""
    result = await _run(coingecko={"_error": "429"}, hyperliquid=_HL_PAYLOAD)

    assert result["basis_consistent"] is True
    assert "deribit" in result["cross_check"]


@pytest.mark.asyncio
async def test_fallback_refuses_to_claim_verification_without_an_independent_venue():
    only_hl = {"venues": [{"venue": "hyperliquid", "premium_pct": 0.006}], "mean_premium_pct": 0.006}
    result = await _run(coingecko={"_error": "429"}, premium=only_hl, hyperliquid=_HL_PAYLOAD)

    assert result["basis_consistent"] is None
    assert "not independently verified" in result["cross_check"]
    assert "error" not in result  # still scoreable, just unverified


@pytest.mark.asyncio
async def test_empty_coingecko_contract_list_also_triggers_the_fallback():
    """A 200 response with no qualifying BTC contracts is a data failure too."""
    result = await _run(coingecko=[], hyperliquid=_HL_PAYLOAD)

    assert result["funding_source"] == "hyperliquid"


@pytest.mark.asyncio
async def test_both_sources_failing_returns_an_error_naming_both():
    result = await _run(coingecko={"_error": "429"}, hyperliquid=None)

    assert "error" in result
    assert "429" in result["error"]
    assert "hyperliquid" in result["error"]


@pytest.mark.asyncio
async def test_fallback_result_is_cached_like_a_primary_result():
    first = await _run(coingecko={"_error": "429"}, hyperliquid=_HL_PAYLOAD)
    # Second call: every outbound source fails, so a non-cached implementation errors
    second = await _run(coingecko={"_error": "429"}, hyperliquid=None)

    assert second == first


@pytest.mark.asyncio
async def test_symbol_is_respected_by_the_fallback():
    fetch_hl = AsyncMock(return_value={"ETH": {"funding_rates": [0.002], "open_interests": [1e9]}})
    with (
        patch.object(crypto_funding, "_fetch", AsyncMock(return_value={"_error": "429"})),
        patch.object(crypto_funding, "_observed_premium", AsyncMock(return_value=_BOTH_PREMIUMS)),
        patch.object(crypto_funding.hyperliquid, "fetch_derivatives", fetch_hl),
    ):
        result = await crypto_funding.get_btc_funding("ETH")

    assert fetch_hl.await_args.args[0] == {"ETH"}
    assert result["symbol"] == "ETH"
    assert result["funding_source"] == "hyperliquid"
