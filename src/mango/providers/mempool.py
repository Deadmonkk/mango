"""Bitcoin mempool microstructure — fees and congestion from mempool.space (free, no key).

Fee levels are a direct read on on-chain demand: a near-empty mempool with
1-5 sat/vB fees means little real usage pressure; sustained 50+ sat/vB means
demand is spiking (bull-market congestion or an inscription/ordinals wave).
"""

import math

import httpx

from mango.core import http
from mango.core.logging import log

from mango.core import cache
from mango.ext_settings import (
    CACHE_TTL_MEMPOOL,
    MEMPOOL_FEE_CONGESTED_SAT_VB,
    MEMPOOL_FEE_QUIET_SAT_VB,
)

BASE_URL = "https://mempool.space/api"

_VBYTES_PER_BLOCK = 1_000_000  # ~1 vMB of transactions fit in each block
_BLOCKS_PER_HOUR = 6  # one block every ~10 minutes


def _congestion_signal(fastest_fee: float) -> str:
    if fastest_fee >= MEMPOOL_FEE_CONGESTED_SAT_VB:
        return (
            f"CONGESTED — next-block fee {fastest_fee} sat/vB; heavy on-chain demand "
            "(historically coincides with speculative frenzies)"
        )
    if fastest_fee <= MEMPOOL_FEE_QUIET_SAT_VB:
        return (
            f"quiet — next-block fee only {fastest_fee} sat/vB; minimal on-chain demand "
            "(cheap to transact, but little real usage pressure)"
        )
    return f"normal — next-block fee {fastest_fee} sat/vB; routine on-chain activity"


async def get_btc_mempool() -> dict:
    """Get Bitcoin mempool state: recommended fees and congestion backlog.

    Returns:
        Dict with fee tiers (sat/vB), mempool size/backlog, and a congestion
        signal — or an error dict if mempool.space is unreachable.
    """
    cache_key = "btc_mempool"
    cached = cache.get(cache_key)
    if cached:
        log.debug("Cache hit: %s", cache_key)
        return cached

    try:
        async with httpx.AsyncClient() as client:
            fees = await http.fetch_json(f"{BASE_URL}/v1/fees/recommended", client=client, timeout=15)
            stats = await http.fetch_json(f"{BASE_URL}/mempool", client=client, timeout=15)
    except httpx.TimeoutException:
        log.warning("mempool.space timeout")
        return {"error": "Request timed out", "source": "mempool.space"}
    except httpx.HTTPStatusError as e:
        log.warning("mempool.space HTTP %d", e.response.status_code)
        return {"error": f"HTTP {e.response.status_code}", "source": "mempool.space"}
    except httpx.HTTPError as e:
        log.error("mempool.space connection failed: %s", e)
        return {"error": "Connection failed", "source": "mempool.space"}

    fastest = fees.get("fastestFee", 0)
    vsize = stats.get("vsize", 0)
    vsize_mb = round(vsize / 1e6, 2)
    backlog_blocks = math.ceil(vsize / _VBYTES_PER_BLOCK) if vsize else 0

    result = {
        "fees_sat_vb": {
            "fastest": fastest,
            "half_hour": fees.get("halfHourFee"),
            "hour": fees.get("hourFee"),
            "economy": fees.get("economyFee"),
            "minimum": fees.get("minimumFee"),
        },
        "mempool": {
            "tx_count": stats.get("count"),
            "vsize_mb": vsize_mb,
            "backlog_blocks": backlog_blocks,
            "est_clear_hours": round(backlog_blocks / _BLOCKS_PER_HOUR, 1),
            "total_fees_btc": round(stats.get("total_fee", 0) / 1e8, 4),
        },
        "signal": _congestion_signal(fastest),
        "note": (
            "Fees in satoshis per virtual byte. 'Fastest' targets next-block inclusion. "
            "Backlog = unconfirmed transaction volume vs ~1 vMB per block, one block "
            "every ~10 minutes. Quiet mempools accompany low on-chain demand; sustained "
            "congestion accompanies usage/speculation spikes."
        ),
        "source": "mempool.space",
    }
    cache.set(cache_key, result, CACHE_TTL_MEMPOOL)
    return result


async def fetch_btc_network_stats() -> dict | None:
    """Bitcoin network security stats from mempool.space — the on-chain fallback.

    Recovers the fields that matter for the regime read when blockchain.com's
    ``/stats`` endpoint is down: hash rate, difficulty, and the tip block height
    (which drives the halving countdown). mempool.space does NOT expose 24h
    transaction count, 24h sent volume, or a spot price, so those stay ``None``
    on the fallback path and the caller documents the degraded source.

    Hash rate is returned in GH/s to match blockchain.com's ``hash_rate`` units
    (mempool reports H/s). Returns ``None`` when unavailable; never raises.
    """
    try:
        async with httpx.AsyncClient() as client:
            hashrate_data = await http.fetch_json(
                f"{BASE_URL}/v1/mining/hashrate/3d", client=client, timeout=15
            )
            tip_height = int(
                await http.fetch_text(f"{BASE_URL}/blocks/tip/height", client=client, timeout=15)
            )
    except Exception as e:  # provider contract: never raise
        log.warning("mempool.space network-stats fallback failed: %s", e)
        return None

    current_hashrate_hs = hashrate_data.get("currentHashrate")
    return {
        # H/s → GH/s for parity with blockchain.com's hash_rate field.
        "hash_rate_gh_s": round(current_hashrate_hs / 1e9, 2) if current_hashrate_hs else None,
        "difficulty": hashrate_data.get("currentDifficulty"),
        "total_blocks_mined": tip_height,
    }
