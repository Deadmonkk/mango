"""Tests for mango.providers.market_data — fed funds futures path and equity sentiment."""

from unittest.mock import patch

import pytest

from mango.providers import market_data


@pytest.fixture(autouse=True)
def clear_caches(tmp_cache_dir):
    """Ensure every test starts with empty cache."""
    pass


# ---------------------------------------------------------------------------
# Fed funds futures contract generation
# ---------------------------------------------------------------------------


def test_fed_funds_contracts_generates_month_codes():
    """Contracts roll forward through CME month codes and across year boundaries."""
    contracts = market_data._fed_funds_contracts(2026, 11, 4)

    tickers = [c["ticker"] for c in contracts]
    months = [c["month"] for c in contracts]

    # Nov=X, Dec=Z, Jan=F, Feb=G
    assert tickers == ["ZQX26.CBT", "ZQZ26.CBT", "ZQF27.CBT", "ZQG27.CBT"]
    assert months == ["2026-11", "2026-12", "2027-01", "2027-02"]


# ---------------------------------------------------------------------------
# Fed path
# ---------------------------------------------------------------------------


async def test_get_fed_path_prices_cuts():
    """Rising futures prices = falling implied rates = market pricing cuts."""
    contracts = [
        {"ticker": "ZQM26.CBT", "month": "2026-06"},
        {"ticker": "ZQU26.CBT", "month": "2026-09"},
        {"ticker": "ZQZ26.CBT", "month": "2026-12"},
    ]
    closes = {"ZQM26.CBT": 96.10, "ZQU26.CBT": 96.40, "ZQZ26.CBT": 96.65}

    async def fake_close(symbol):
        return closes[symbol]

    with (
        patch.object(market_data, "_fed_funds_contracts", return_value=contracts),
        patch.object(market_data, "_fetch_last_close", side_effect=fake_close),
    ):
        result = await market_data.get_fed_path()

    assert result["source"] == "yahoo_finance"
    path = result["path"]
    assert len(path) == 3
    assert path[0]["implied_rate_pct"] == 3.9  # 100 - 96.10
    assert path[2]["implied_rate_pct"] == 3.35  # 100 - 96.65
    assert path[0]["change_from_front_bp"] == 0
    assert path[2]["change_from_front_bp"] == -55
    # 55bp of easing priced in → cuts signal
    assert "cut" in result["signal"].lower()


async def test_get_fed_path_skips_missing_contracts():
    """Contracts that fail to fetch are skipped, not fatal."""
    contracts = [
        {"ticker": "ZQM26.CBT", "month": "2026-06"},
        {"ticker": "ZQN26.CBT", "month": "2026-07"},
    ]

    async def fake_close(symbol):
        if symbol == "ZQN26.CBT":
            return None
        return 96.0

    with (
        patch.object(market_data, "_fed_funds_contracts", return_value=contracts),
        patch.object(market_data, "_fetch_last_close", side_effect=fake_close),
    ):
        result = await market_data.get_fed_path()

    assert len(result["path"]) == 1
    assert result["path"][0]["implied_rate_pct"] == 4.0


async def test_get_fed_path_all_missing_returns_error():
    """If no contract data is available at all, return an error dict."""

    async def fake_close(symbol):
        return None

    with (
        patch.object(market_data, "_fed_funds_contracts", return_value=[{"ticker": "ZQM26.CBT", "month": "2026-06"}]),
        patch.object(market_data, "_fetch_last_close", side_effect=fake_close),
    ):
        result = await market_data.get_fed_path()

    assert "error" in result
    assert result["source"] == "yahoo_finance"


# ---------------------------------------------------------------------------
# Equity sentiment
# ---------------------------------------------------------------------------

_LAST_CLOSES = {"^VIX": 25.0, "^VIX3M": 22.0, "^SKEW": 150.0}

_MONTHLY_RETURNS = {
    "RSP": {"current": 180.0, "1mo": -1.0, "3mo": 2.0},
    "SPY": {"current": 730.0, "1mo": -3.0, "3mo": 1.0},
}


async def test_get_equity_sentiment_backwardation_and_breadth():
    """VIX above VIX3M = backwardation (acute fear); RSP beating SPY = broad participation."""

    async def fake_close(symbol):
        return _LAST_CLOSES[symbol]

    async def fake_monthly(symbol):
        return _MONTHLY_RETURNS[symbol]

    with (
        patch.object(market_data, "_fetch_last_close", side_effect=fake_close),
        patch.object(market_data, "_fetch_monthly_returns", side_effect=fake_monthly),
    ):
        result = await market_data.get_equity_sentiment()

    assert result["source"] == "yahoo_finance"

    term = result["vix_term_structure"]
    assert term["vix"] == 25.0
    assert term["vix3m"] == 22.0
    assert term["ratio"] == round(25.0 / 22.0, 3)
    assert "backwardation" in term["signal"]

    skew = result["skew"]
    assert skew["value"] == 150.0
    assert "elevated" in skew["signal"]

    breadth = result["breadth"]
    assert breadth["rsp_vs_spy_1mo_pct"] == 2.0  # -1.0 - (-3.0)
    assert breadth["rsp_vs_spy_3mo_pct"] == 1.0
    assert "broad" in breadth["signal"]


async def test_get_equity_sentiment_survives_partial_failure():
    """A missing VIX3M doesn't break the rest of the report."""

    async def fake_close(symbol):
        if symbol == "^VIX3M":
            return None
        return _LAST_CLOSES[symbol]

    async def fake_monthly(symbol):
        return _MONTHLY_RETURNS[symbol]

    with (
        patch.object(market_data, "_fetch_last_close", side_effect=fake_close),
        patch.object(market_data, "_fetch_monthly_returns", side_effect=fake_monthly),
    ):
        result = await market_data.get_equity_sentiment()

    assert result["vix_term_structure"]["ratio"] is None
    assert result["breadth"]["rsp_vs_spy_1mo_pct"] == 2.0
