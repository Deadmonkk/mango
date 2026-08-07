"""Tests for mango.providers.sectors — sector rotation vs SPY."""

from unittest.mock import AsyncMock, patch

import pytest

from mango.providers import sectors


@pytest.fixture(autouse=True)
def clear_caches(tmp_cache_dir):
    """Ensure every test starts with empty cache."""
    pass


def _fake_returns(symbol: str):
    """SPY +5% on 3mo; cyclicals lead, defensives lag."""
    table = {
        "SPY": {"1mo": 2.0, "3mo": 5.0, "6mo": 10.0},
        "XLK": {"1mo": 4.0, "3mo": 9.0, "6mo": 16.0},
        "XLE": {"1mo": 3.5, "3mo": 8.0, "6mo": 12.0},
        "XLP": {"1mo": 0.5, "3mo": 1.0, "6mo": 3.0},
        "XLU": {"1mo": 1.0, "3mo": 2.0, "6mo": 4.0},
    }
    return table.get(symbol, {"1mo": 2.0, "3mo": 5.0, "6mo": 10.0})


async def test_sector_rotation_relative_performance():
    with patch.object(sectors, "_fetch_returns", AsyncMock(side_effect=_fake_returns)):
        result = await sectors.get_sector_rotation()

    assert result["source"] == "yahoo_finance"
    assert len(result["sectors"]) == len(sectors.SECTOR_ETFS)
    xlk = next(s for s in result["sectors"] if s["etf"] == "XLK")
    assert xlk["relative_3mo_pct"] == 4.0  # 9 − 5
    assert xlk["sector"] == "Technology"


async def test_sector_rotation_leaders_and_laggards():
    with patch.object(sectors, "_fetch_returns", AsyncMock(side_effect=_fake_returns)):
        result = await sectors.get_sector_rotation()

    leader_etfs = [s["etf"] for s in result["leaders_3mo"]]
    laggard_etfs = [s["etf"] for s in result["laggards_3mo"]]
    assert "XLK" in leader_etfs
    assert "XLP" in laggard_etfs


async def test_sector_rotation_cyclical_defensive_spread():
    """Cyclicals beating defensives → positive spread, risk-on signal."""
    with patch.object(sectors, "_fetch_returns", AsyncMock(side_effect=_fake_returns)):
        result = await sectors.get_sector_rotation()

    assert result["cyclical_vs_defensive_3mo_pct"] > 0
    assert "risk-on" in result["signal"].lower()


async def test_sector_rotation_spy_failure_returns_error():
    def returns(symbol: str):
        return None if symbol == "SPY" else _fake_returns(symbol)

    with patch.object(sectors, "_fetch_returns", AsyncMock(side_effect=returns)):
        result = await sectors.get_sector_rotation()

    assert "error" in result
    assert result["source"] == "yahoo_finance"


async def test_sector_rotation_survives_partial_sector_failure():
    def returns(symbol: str):
        return None if symbol == "XLB" else _fake_returns(symbol)

    with patch.object(sectors, "_fetch_returns", AsyncMock(side_effect=returns)):
        result = await sectors.get_sector_rotation()

    etfs = [s["etf"] for s in result["sectors"]]
    assert "XLB" not in etfs
    assert len(result["sectors"]) == len(sectors.SECTOR_ETFS) - 1
