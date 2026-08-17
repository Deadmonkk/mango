"""Tests for the BTC on-chain fallback: blockchain.com → mempool.space.

mempool.space recovers the network-security fields (hash rate, difficulty,
halving countdown); the 24h transaction count, sent volume and spot price have
no mempool.space equivalent and must surface as None — never a fabricated zero.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from mango.providers import crypto_analytics, mempool


@pytest.fixture(autouse=True)
def clear_caches(tmp_cache_dir):
    """Every test starts with an empty cache."""
    pass


# ---------------------------------------------------------------------------
# mempool.fetch_btc_network_stats
# ---------------------------------------------------------------------------


def _mock_mempool_client(*, hashrate=8.5e20, difficulty=1.2e14, tip="880000", fail=False):
    def _resp(payload=None, text=None):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.json = MagicMock(return_value=payload)
        resp.text = text
        resp.raise_for_status = MagicMock()
        return resp

    async def fake_json(url, **kwargs):
        if fail:
            raise httpx.ConnectError("boom")
        return {"currentHashrate": hashrate, "currentDifficulty": difficulty}

    async def fake_text(url, **kwargs):
        if fail:
            raise httpx.ConnectError("boom")
        return tip  # /blocks/tip/height

    return AsyncMock(side_effect=fake_json), AsyncMock(side_effect=fake_text)


async def test_fetch_network_stats_converts_hashrate_to_gh_s():
    fetch_json, fetch_text = _mock_mempool_client()
    with (
        patch("mango.providers.mempool.http.fetch_json", fetch_json),
        patch("mango.providers.mempool.http.fetch_text", fetch_text),
    ):
        stats = await mempool.fetch_btc_network_stats()
    # 8.5e20 H/s ÷ 1e9 = 8.5e11 GH/s
    assert stats["hash_rate_gh_s"] == 8.5e11
    assert stats["difficulty"] == 1.2e14
    assert stats["total_blocks_mined"] == 880000


async def test_fetch_network_stats_returns_none_on_failure():
    fetch_json, fetch_text = _mock_mempool_client(fail=True)
    with (
        patch("mango.providers.mempool.http.fetch_json", fetch_json),
        patch("mango.providers.mempool.http.fetch_text", fetch_text),
    ):
        assert await mempool.fetch_btc_network_stats() is None


# ---------------------------------------------------------------------------
# get_btc_onchain — primary, fallback, and both-down paths
# ---------------------------------------------------------------------------


_BLOCKCHAIN_STATS = {
    "hash_rate": 9.0e11,
    "difficulty": 1.3e14,
    "n_blocks_total": 880001,
    "minutes_between_blocks": 9.8,
    "n_tx": 410000,
    "estimated_btc_sent": 250_00000000,  # 250 BTC in satoshi
    "estimated_transaction_volume_usd": 1.5e10,
    "total_fees_btc": 15_000000,  # satoshi
    "market_price_usd": 62000.0,
}


def _mock_blockchain_client(*, fail=False):
    def _resp(payload):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.json = MagicMock(return_value=payload)
        resp.raise_for_status = MagicMock()
        return resp

    async def fake_json(url, **kwargs):
        if fail:
            raise httpx.ConnectError("blockchain.com down")
        return _BLOCKCHAIN_STATS

    return AsyncMock(side_effect=fake_json)


async def test_onchain_uses_blockchain_com_when_available():
    with patch("mango.providers.crypto_analytics.http.fetch_json", _mock_blockchain_client()):
        result = await crypto_analytics.get_btc_onchain()
    assert result["source"] == "blockchain.com"
    assert result["network"]["hash_rate_gh_s"] == 9.0e11
    assert result["transactions"]["count_24h"] == 410000
    assert result["transactions"]["volume_btc_24h"] == 250.0
    assert result["network"]["avg_block_time_minutes"] == 9.8


async def test_onchain_falls_back_to_mempool_with_none_tx_fields():
    fallback_stats = {"hash_rate_gh_s": 8.5e11, "difficulty": 1.2e14, "total_blocks_mined": 880000}
    with (
        patch("mango.providers.crypto_analytics.http.fetch_json",
              _mock_blockchain_client(fail=True)),
        patch.object(crypto_analytics.mempool, "fetch_btc_network_stats", AsyncMock(return_value=fallback_stats)),
    ):
        result = await crypto_analytics.get_btc_onchain()

    assert result["source"] == "mempool.space (fallback — blockchain.com unavailable)"
    # Recovered security fields
    assert result["network"]["hash_rate_gh_s"] == 8.5e11
    assert result["network"]["difficulty"] == 1.2e14
    assert result["halving"]["current_block"] == 880000
    # Unavailable on fallback — honest None, never a fabricated zero
    assert result["transactions"]["count_24h"] is None
    assert result["transactions"]["volume_btc_24h"] is None
    assert result["transactions"]["market_price_usd"] is None
    # Halving math still computes off the recovered block height
    assert result["halving"]["blocks_remaining"] > 0


async def test_onchain_error_when_both_sources_down():
    with (
        patch("mango.providers.crypto_analytics.http.fetch_json",
              _mock_blockchain_client(fail=True)),
        patch.object(crypto_analytics.mempool, "fetch_btc_network_stats", AsyncMock(return_value=None)),
    ):
        result = await crypto_analytics.get_btc_onchain()
    assert "error" in result
    assert result["source"] == "blockchain.com"
