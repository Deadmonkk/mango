"""Tests for mango.providers.cycle — recession & business-cycle dashboard."""

from unittest.mock import AsyncMock, patch

import pytest

from mango.providers import cycle


@pytest.fixture(autouse=True)
def clear_caches(tmp_cache_dir):
    """Ensure every test starts with empty cache."""
    pass


def _benign_values(series_id: str, limit: int):
    """Healthy expansion: nothing triggered."""
    flat_claims = [220.0] * 20  # thousands, newest first, flat trend
    return {
        "SAHMREALTIME": [0.10],
        "T10Y2Y": [0.55],
        "T10Y3M": [0.40],
        "ICSA": flat_claims,
        "NFCI": [-0.45],
        "GDPNOW": [2.5],
    }.get(series_id)


def _stressed_values(series_id: str, limit: int):
    """Everything triggered: Sahm fired, curves inverted, claims surging, tight conditions, negative GDPNow."""
    rising_claims = [265.0, 262.0, 260.0, 258.0] + [230.0] * 9 + [215.0, 214.0, 213.0, 212.0] + [210.0] * 3
    return {
        "SAHMREALTIME": [0.63],
        "T10Y2Y": [-0.30],
        "T10Y3M": [-0.55],
        "ICSA": rising_claims,
        "NFCI": [0.40],
        "GDPNOW": [-1.2],
    }.get(series_id)


async def test_cycle_position_expansion_no_signals():
    with patch.object(cycle, "_latest_values", AsyncMock(side_effect=_benign_values)):
        result = await cycle.get_cycle_position()

    assert result["source"] == "fred"
    assert result["signals_active"] == 0
    assert result["signals_available"] == 6
    assert "no recession signals" in result["verdict"].lower()
    assert len(result["signals"]) == 6
    sahm = next(s for s in result["signals"] if s["name"] == "sahm_rule")
    assert sahm["triggered"] is False
    assert sahm["value"] == 0.10


async def test_cycle_position_all_signals_triggered():
    with patch.object(cycle, "_latest_values", AsyncMock(side_effect=_stressed_values)):
        result = await cycle.get_cycle_position()

    assert result["signals_active"] == 6
    assert "recession" in result["verdict"].lower()
    claims = next(s for s in result["signals"] if s["name"] == "claims_trend")
    assert claims["triggered"] is True
    # 4-wk avg ~261.25 vs ~213.5 three months ago → > +10%
    assert claims["value"] > 10.0


async def test_cycle_position_survives_partial_failure():
    def values(series_id: str, limit: int):
        if series_id == "NFCI":
            return None
        return _benign_values(series_id, limit)

    with patch.object(cycle, "_latest_values", AsyncMock(side_effect=values)):
        result = await cycle.get_cycle_position()

    assert result["signals_available"] == 5
    nfci = next(s for s in result["signals"] if s["name"] == "financial_conditions")
    assert nfci["triggered"] is None
    assert "unavailable" in nfci["meaning"].lower()


async def test_cycle_position_all_sources_failed_returns_error():
    with patch.object(cycle, "_latest_values", AsyncMock(return_value=None)):
        result = await cycle.get_cycle_position()

    assert "error" in result
    assert result["source"] == "fred"
