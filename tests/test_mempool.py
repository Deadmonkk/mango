"""Tests for mango.providers.mempool — BTC fee/congestion microstructure."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from mango.providers import mempool


@pytest.fixture(autouse=True)
def clear_caches(tmp_cache_dir):
    """Ensure every test starts with empty cache."""
    pass


_FEES = {"fastestFee": 4, "halfHourFee": 3, "hourFee": 2, "economyFee": 2, "minimumFee": 1}
_MEMPOOL = {"count": 52184, "vsize": 30137833, "total_fee": 11690361}


def _mock_fetch(fees=_FEES, mempool_stats=_MEMPOOL, fail=False):
    """Stand in for http.fetch_json, which now owns transport and returns parsed JSON."""
    async def fake_fetch(url, **kwargs):
        if fail:
            raise httpx.ConnectError("boom")
        return fees if "fees" in url else mempool_stats

    return AsyncMock(side_effect=fake_fetch)


async def test_get_btc_mempool_quiet_network():
    with patch("mango.providers.mempool.http.fetch_json", _mock_fetch()):
        result = await mempool.get_btc_mempool()

    assert result["source"] == "mempool.space"
    assert result["fees_sat_vb"]["fastest"] == 4
    assert result["mempool"]["tx_count"] == 52184
    assert result["mempool"]["vsize_mb"] == 30.14
    assert result["mempool"]["backlog_blocks"] == 31  # ceil(30.14 / 1 vMB per block)
    assert "quiet" in result["signal"].lower()


async def test_get_btc_mempool_congested_network():
    hot_fees = {"fastestFee": 80, "halfHourFee": 60, "hourFee": 40, "economyFee": 20, "minimumFee": 5}
    with patch("mango.providers.mempool.http.fetch_json", _mock_fetch(fees=hot_fees)):
        result = await mempool.get_btc_mempool()

    assert "congested" in result["signal"].lower()


async def test_get_btc_mempool_failure_returns_error():
    with patch("mango.providers.mempool.http.fetch_json", _mock_fetch(fail=True)):
        result = await mempool.get_btc_mempool()

    assert "error" in result
    assert result["source"] == "mempool.space"
