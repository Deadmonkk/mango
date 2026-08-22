"""Tests for mango.analytics.regime_history — forward-return calibration."""

from unittest.mock import AsyncMock, patch

import pytest

from mango.analytics import regime_history


@pytest.fixture(autouse=True)
def clear_caches(tmp_cache_dir):
    pass


def _price_series(start_date: str, n: int, start_price: float, daily: float) -> dict:
    """A deterministic rising/falling close series as get_historical would return."""
    from datetime import datetime, timedelta

    base = datetime.strptime(start_date, "%Y-%m-%d").date()
    prices = []
    for i in range(n):
        prices.append({"date": (base + timedelta(days=i)).isoformat(), "close": round(start_price + daily * i, 2)})
    return {"symbol": "X", "prices": prices, "source": "yahoo_finance"}


async def test_regime_history_buckets_forward_returns():
    # One snapshot 40 days ago with a high crypto score; BTC rose over the window.
    snaps = [{"date": "2026-01-01", "crypto_regime": 70, "equity_regime": 42}]
    # BTC: 100 -> 130 over 30 days (+30%); S&P: flat
    btc = _price_series("2026-01-01", 120, 100.0, 1.0)
    spx = _price_series("2026-01-01", 120, 5000.0, 0.0)

    async def fake_get(symbol, **kwargs):
        return btc if symbol == "BTC-USD" else spx

    with (
        patch.object(regime_history, "latest_snapshot_per_day", return_value=snaps),
        patch.object(regime_history.historical, "get_historical", new=AsyncMock(side_effect=fake_get)),
        patch("mango.analytics.regime_history.date") as mock_date,
    ):
        # Freeze "today" well past the 30-day horizon so the sample is matured.
        from datetime import date as real_date

        mock_date.today.return_value = real_date(2026, 6, 1)
        mock_date.side_effect = lambda *a, **k: real_date(*a, **k)
        result = await regime_history.get_regime_history(forward_days=30)

    assert result["source"] == "regime_history (local + yahoo)"
    crypto = result["scores"]["crypto_regime"]
    assert crypto["matured_samples"] == 1
    band = crypto["by_band"]["Bottom-forming"]
    assert band["n"] == 1
    assert band["avg_forward_return_pct"] == 30.0  # +30% over 30 days
    # A single sample has no spread — stdev/stderr are undefined, not fabricated.
    assert band["stdev_pct"] is None
    assert band["stderr_pct"] is None


async def test_regime_history_band_stderr_with_multiple_samples():
    # Two snapshots landing in the same band with different realized returns,
    # so stdev/stderr become computable (n=2).
    snaps = [
        {"date": "2026-01-01", "crypto_regime": 70, "equity_regime": 42},
        {"date": "2026-01-02", "crypto_regime": 72, "equity_regime": 42},
    ]
    btc = _price_series("2026-01-01", 150, 100.0, 1.0)  # steady rise, ~30% by day 30
    spx = _price_series("2026-01-01", 150, 5000.0, 0.0)

    async def fake_get(symbol, **kwargs):
        return btc if symbol == "BTC-USD" else spx

    with (
        patch.object(regime_history, "latest_snapshot_per_day", return_value=snaps),
        patch.object(regime_history.historical, "get_historical", new=AsyncMock(side_effect=fake_get)),
        patch("mango.analytics.regime_history.date") as mock_date,
    ):
        from datetime import date as real_date

        mock_date.today.return_value = real_date(2026, 6, 1)
        mock_date.side_effect = lambda *a, **k: real_date(*a, **k)
        result = await regime_history.get_regime_history(forward_days=30)

    band = result["scores"]["crypto_regime"]["by_band"]["Bottom-forming"]
    assert band["n"] == 2
    assert band["stdev_pct"] is not None
    assert band["stderr_pct"] == round(band["stdev_pct"] / (2**0.5), 2)


async def test_regime_history_no_snapshots():
    with patch.object(regime_history, "latest_snapshot_per_day", return_value=[]):
        result = await regime_history.get_regime_history()
    assert "error" in result


async def test_regime_history_pending_when_not_matured():
    # Snapshot dated "today" -> horizon not elapsed -> pending, no matured samples.
    from datetime import date

    snaps = [{"date": date.today().isoformat(), "crypto_regime": 70}]
    empty = {"symbol": "X", "prices": [], "source": "yahoo_finance"}

    with (
        patch.object(regime_history, "latest_snapshot_per_day", return_value=snaps),
        patch.object(regime_history.historical, "get_historical", new=AsyncMock(return_value=empty)),
    ):
        result = await regime_history.get_regime_history(forward_days=30)

    assert result["scores"]["crypto_regime"]["pending_samples"] == 1
    assert result["scores"]["crypto_regime"]["matured_samples"] == 0
    assert "No snapshot is yet" in result["maturity_caveat"]


def test_band_mapping():
    assert regime_history._band(10) == "Euphoric / expensive"
    assert regime_history._band(42) == "Mid-cycle"
    assert regime_history._band(70) == "Bottom-forming"
    assert regime_history._band(90) == "Deep-value capitulation"
